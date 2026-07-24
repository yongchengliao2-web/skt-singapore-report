from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError


SNAPSHOT_RELATIVE_DIR = Path("assets") / "material_snapshots"
SNAPSHOT_WEB_PREFIX = "/assets/material_snapshots/"
IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".m3u8", ".m4v", ".mov", ".mp4", ".webm"}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SKT-material-snapshot/1.0"
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MIN_SNAPSHOT_BYTES = 512
MAX_SNAPSHOT_SIZE = (960, 720)


def is_link_material(row: dict[str, Any]) -> bool:
    mode = str(row.get("snapshot_mode") or "").strip().casefold()
    source = str(row.get("material_source") or row.get("source") or "").strip().casefold()
    post_url = str(row.get("post_url") or "").strip()
    return mode == "link" or "kol" in source or bool(post_url)


def material_source_url(row: dict[str, Any]) -> str:
    if is_link_material(row):
        return ""
    for key in ("preview_url", "play_url"):
        value = str(row.get(key) or "").strip()
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
    return ""


def snapshot_filename(row: dict[str, Any], source_url: str) -> str:
    key = str(row.get("material_id") or row.get("material_key") or "material")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", key).strip("-_")[:56] or "material"
    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}.jpg"


def source_kind(row: dict[str, Any], source_url: str) -> str:
    extension = Path(urlparse(source_url).path).suffix.casefold()
    file_type = str(row.get("file_type") or "").casefold()
    material_type = str(row.get("material_type") or "").casefold()
    if extension in VIDEO_EXTENSIONS or file_type.startswith("video/") or any(token in material_type for token in ("video", "视频")):
        return "video"
    if extension in IMAGE_EXTENSIONS or file_type.startswith("image/") or any(token in material_type for token in ("image", "图片", "gif")):
        return "image"
    return "unknown"


def valid_snapshot(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < MIN_SNAPSHOT_BYTES:
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, UnidentifiedImageError):
        return False


def save_snapshot(image: Image.Image, output_path: Path) -> None:
    image.seek(0)
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    image.thumbnail(MAX_SNAPSHOT_SIZE, Image.Resampling.LANCZOS)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}-",
        suffix=".jpg.part",
        dir=output_path.parent,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    try:
        image.save(temp_path, format="JPEG", quality=82, optimize=True, progressive=True)
        if temp_path.stat().st_size < MIN_SNAPSHOT_BYTES:
            raise RuntimeError("generated snapshot is unexpectedly small")
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)


def download_image_snapshot(source_url: str, output_path: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            request = Request(
                source_url,
                headers={
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Cache-Control": "no-cache",
                    "User-Agent": USER_AGENT,
                },
            )
            with urlopen(request, timeout=45) as response:
                content_length = int(response.headers.get("Content-Length") or 0)
                if content_length > MAX_IMAGE_BYTES:
                    raise RuntimeError("source image exceeds download limit")
                content = response.read(MAX_IMAGE_BYTES + 1)
            if len(content) > MAX_IMAGE_BYTES:
                raise RuntimeError("source image exceeds download limit")
            from io import BytesIO

            with Image.open(BytesIO(content)) as image:
                save_snapshot(image, output_path)
            return
        except Exception as error:  # Network and source formats vary across older DMS rows.
            last_error = error
            if attempt == 0:
                time.sleep(0.5)
    raise RuntimeError(str(last_error or "image snapshot failed"))


def find_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        return ""


def download_video_snapshot(source_url: str, output_path: Path, ffmpeg: str) -> None:
    if not ffmpeg:
        raise RuntimeError("ffmpeg is unavailable")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}-",
        suffix=".jpg.part",
        dir=output_path.parent,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-user_agent",
        USER_AGENT,
        "-ss",
        "0.20",
        "-i",
        source_url,
        "-frames:v",
        "1",
        "-vf",
        "scale=960:720:force_original_aspect_ratio=decrease",
        "-c:v",
        "mjpeg",
        "-q:v",
        "3",
        "-f",
        "image2",
        str(temp_path),
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=90,
            creationflags=creation_flags,
        )
        if completed.returncode != 0 or not temp_path.exists():
            message = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise RuntimeError(message[-1][:220] if message else "ffmpeg did not produce a frame")
        with Image.open(temp_path) as image:
            image.load()
            save_snapshot(image, output_path)
    finally:
        temp_path.unlink(missing_ok=True)


def ensure_material_snapshots(payload: dict[str, Any], site_dir: Path) -> dict[str, int]:
    rows = list(payload.get("material_rows") or []) + list(payload.get("library_rows") or [])
    snapshot_dir = site_dir / SNAPSHOT_RELATIVE_DIR
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    jobs: dict[str, dict[str, Any]] = {}
    linked_keys: set[str] = set()
    for row in rows:
        if is_link_material(row):
            row.pop("snapshot_url", None)
            linked_keys.add(str(row.get("material_key") or row.get("material_id") or id(row)))
            continue
        source_url = material_source_url(row)
        if not source_url:
            row.pop("snapshot_url", None)
            continue
        filename = snapshot_filename(row, source_url)
        output_path = snapshot_dir / filename
        web_path = f"{SNAPSHOT_WEB_PREFIX}{filename}"
        job = jobs.setdefault(
            filename,
            {
                "source_url": source_url,
                "output_path": output_path,
                "kind": source_kind(row, source_url),
                "rows": [],
                "label": str(row.get("material_id") or row.get("material_key") or filename),
            },
        )
        job["rows"].append(row)
        job["web_path"] = web_path

    stats = {
        "referenced": len(jobs),
        "linked": len(linked_keys),
        "cached": 0,
        "created": 0,
        "failed": 0,
        "pruned": 0,
    }
    pending_images: list[dict[str, Any]] = []
    pending_videos: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []

    for job in jobs.values():
        if valid_snapshot(job["output_path"]):
            stats["cached"] += 1
            for row in job["rows"]:
                row["snapshot_url"] = job["web_path"]
            continue
        job["output_path"].unlink(missing_ok=True)
        if job["kind"] == "image":
            pending_images.append(job)
        elif job["kind"] == "video":
            pending_videos.append(job)
        else:
            failures.append((job["label"], "unsupported source type"))

    def run_jobs(items: list[dict[str, Any]], worker_count: int, worker: Any) -> None:
        if not items:
            return
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="skt-snapshot") as executor:
            future_jobs = {executor.submit(worker, job): job for job in items}
            for future in as_completed(future_jobs):
                job = future_jobs[future]
                try:
                    future.result()
                    if not valid_snapshot(job["output_path"]):
                        raise RuntimeError("snapshot validation failed")
                    stats["created"] += 1
                    for row in job["rows"]:
                        row["snapshot_url"] = job["web_path"]
                except Exception as error:
                    job["output_path"].unlink(missing_ok=True)
                    failures.append((job["label"], str(error)))

    run_jobs(
        pending_images,
        8,
        lambda job: download_image_snapshot(job["source_url"], job["output_path"]),
    )
    ffmpeg = find_ffmpeg()
    run_jobs(
        pending_videos,
        2,
        lambda job: download_video_snapshot(job["source_url"], job["output_path"], ffmpeg),
    )

    expected_files = set(jobs)
    for path in snapshot_dir.glob("*.jpg"):
        if path.name not in expected_files:
            path.unlink(missing_ok=True)
            stats["pruned"] += 1

    stats["failed"] = len(failures)
    print(
        "Material snapshots: "
        f"{stats['linked']} linked, {stats['created']} created, {stats['cached']} cached, "
        f"{stats['failed']} failed, {stats['pruned']} stale removed."
    )
    for label, message in failures[:10]:
        print(f"WARNING: snapshot unavailable for {label}: {message}")
    if len(failures) > 10:
        print(f"WARNING: {len(failures) - 10} additional snapshot failures were omitted.")
    return stats
