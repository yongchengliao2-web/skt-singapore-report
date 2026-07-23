# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import json
import mimetypes
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlparse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DMS_DIR = ROOT / "data" / "dms"
DEFAULT_HOST = "https://web.cerahdms.com"
DEFAULT_COUNTRY = "sg"
DEFAULT_PAGE_SIZE = 500
DEFAULT_BRAND_ID = "5"
DEFAULT_TENANT_ID = "125"
TYPE_MAP = {1: "图片", 2: "视频", 3: "GIF"}
SOURCE_MAP = {1: "素材列表", 2: "设计素材", 4: "KOL素材"}


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return default


def clean_token(token: str) -> str:
    return token.replace("Bearer ", "").strip()


def find_token() -> str:
    token = env_first("SKT_DMS_TOKEN", "DMS_TOKEN", "DMS_AUTHORIZATION", "DMS_BEARER_TOKEN")
    return clean_token(token)


def edge_local_storage_dir() -> Path:
    profile = env_first("SKT_DMS_EDGE_PROFILE", default="Default")
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data" / profile / "Local Storage" / "leveldb"


def edge_token_candidates(limit: int = 260) -> list[str]:
    if env_first("SKT_DMS_USE_EDGE_TOKEN", default="1").lower() in {"0", "false", "no"}:
        return []

    root = edge_local_storage_dir()
    if not root.exists():
        return []

    blob = b""
    for path in root.glob("*"):
        if not path.is_file() or path.stat().st_size > 80_000_000:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"web.cerahdms.com" in data or b"cerahdms.com" in data:
            blob += data + b"\n"

    candidates: list[str] = []
    seen: set[bytes] = set()
    for match in re.finditer(rb"TOKEN|OKEN|access[_-]?token|Authorization", blob, re.IGNORECASE):
        chunk = blob[max(0, match.start() - 120) : match.end() + 700]
        for seq in re.findall(rb"[A-Za-z0-9._\-]{24,800}", chunk):
            low = seq.lower()
            if any(
                marker in low
                for marker in (
                    b"token",
                    b"https",
                    b"cerah",
                    b"domain",
                    b"tenant",
                    b"brand",
                    b"aplus",
                    b"localstorage",
                    b"authorization",
                    b"country",
                    b"normal",
                )
            ):
                continue
            if re.fullmatch(rb"[0-9]+", seq) or seq in seen:
                continue
            seen.add(seq)
            candidates.append(seq.decode("ascii", "ignore"))
            if len(candidates) >= limit:
                return candidates
    return candidates


def token_sources() -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    skt_token = clean_token(env_first("SKT_DMS_TOKEN"))
    if skt_token:
        sources.append(("SKT_DMS_TOKEN", skt_token))
    sources.extend((f"Edge local session #{idx}", token) for idx, token in enumerate(edge_token_candidates(), start=1))
    generic_token = clean_token(env_first("DMS_TOKEN", "DMS_AUTHORIZATION", "DMS_BEARER_TOKEN"))
    if generic_token and all(generic_token != token for _, token in sources):
        sources.append(("DMS_TOKEN", generic_token))
    return sources


def norm_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/uploadfile/") or url.startswith("/m3u8/"):
        return "https://cdn-prod.feimeidms.com" + url
    return url


def first_external_url(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            found = first_external_url(*value)
            if found:
                return found
        elif isinstance(value, dict):
            found = first_external_url(*value.values())
            if found:
                return found
        else:
            text = str(value or "").strip()
            if not text:
                continue
            match = re.search(r"https?://[^\s\"'<>，,;；]+", text, re.IGNORECASE)
            if match:
                return norm_url(match.group(0))
    return ""


def as_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except ValueError:
        return 0


def as_float(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except ValueError:
        return 0.0


def pick_file(row: dict[str, Any]) -> dict[str, Any]:
    files = row.get("ossFiles") or row.get("materialFileList") or []
    return files[0] if files else {}


def product_name(row: dict[str, Any]) -> str:
    return str(row.get("adProductNames") or row.get("productItemNames") or row.get("productName") or "未填产品").strip()


def material_type(row: dict[str, Any], file: dict[str, Any]) -> str:
    value = row.get("materialType")
    mime = str(file.get("mimeType") or "").lower()
    suffix = str(file.get("suffix") or "").lower().strip(".")
    if value == 3 or suffix == "gif" or mime == "image/gif":
        return "GIF"
    if value == 2 or mime.startswith("video/") or suffix in {"mp4", "mov", "webm", "m4v"}:
        return "视频"
    if value == 1 or mime.startswith("image/") or suffix in {"jpg", "jpeg", "png", "webp"}:
        return "图片"
    return TYPE_MAP.get(value, str(value or "未标记"))


def file_size_mb(file: dict[str, Any]) -> float:
    size = as_float(file.get("size") or file.get("fileSize") or file.get("contentLength"))
    return round(size / 1024 / 1024, 2) if size else 0.0


def ig_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
        if isinstance(loaded, list):
            return [str(item).strip() for item in loaded if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


def request_page(token: str, page_no: int, *, host: str, country: str, page_size: int) -> tuple[list[dict[str, Any]], int]:
    url = host.rstrip("/") + "/api/admin-api/igad/advertisement/allPage"
    payload = json.dumps({"pageNo": page_no, "pageSize": page_size}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "corp": env_first("SKT_DMS_CORP", default="fm"),
        "fm-country": country,
        "fm-domain-id": env_first("SKT_DMS_DOMAIN_ID", default="0"),
        "fm-domain-type": env_first("SKT_DMS_DOMAIN_TYPE", default="brand"),
        "fm-system": env_first("SKT_DMS_SYSTEM", default="ad"),
        "hash": "#/place/material",
        "referer": f"{host.rstrip('/')}/dms/{country}/ad/",
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh",
        "content-type": "application/json",
    }
    optional_headers = {
        "fm-brandid": env_first("SKT_DMS_BRAND_ID", "DMS_BRAND_ID", default=DEFAULT_BRAND_ID),
        "fm-uid": env_first("SKT_DMS_UID", "DMS_UID"),
        "tenant-id": env_first("SKT_DMS_TENANT_ID", "DMS_TENANT_ID", default=DEFAULT_TENANT_ID),
    }
    for key, value in optional_headers.items():
        if value:
            headers[key] = value

    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=45) as response:
        body = json.loads(response.read().decode("utf-8", "replace"))
    if body.get("code") != 200:
        raise RuntimeError(f"DMS API code {body.get('code')}: {body.get('message')}")
    data = body.get("data") or {}
    return data.get("list") or [], int(data.get("total") or 0)


def fetch_rows(token: str, *, host: str, country: str, page_size: int, max_pages: int = 0) -> tuple[list[dict[str, Any]], int]:
    first_rows, total = request_page(token, 1, host=host, country=country, page_size=page_size)
    rows = list(first_rows)
    page_count = max(1, (total + page_size - 1) // page_size)
    if max_pages > 0:
        page_count = min(page_count, max_pages)
    for page_no in range(2, page_count + 1):
        page_rows, _ = request_page(token, page_no, host=host, country=country, page_size=page_size)
        rows.extend(page_rows)
        time.sleep(0.08)
    return rows, total


def choose_extension(record: dict[str, Any]) -> str:
    preview_url = record.get("preview_url") or record.get("play_url") or ""
    suffix = Path(urlparse(preview_url).path).suffix
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(record.get("file_type") or "")
    return guessed or ".bin"


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    file = pick_file(row)
    preview_url = norm_url(file.get("fullUrlWithHttps") or file.get("fullUrl") or file.get("url") or row.get("url"))
    play_url = norm_url(file.get("fullPlayUrl") or file.get("playUrl"))
    ids = ig_ids(row.get("igAdIds"))
    post_url = first_external_url(
        row.get("channelUrls"),
        row.get("channelUrlsStr"),
        row.get("networkDiskLink"),
        row.get("postUrl"),
        row.get("post_url"),
        row.get("postLink"),
        row.get("postCopy"),
        row.get("publishUrl"),
        row.get("shareUrl"),
    )
    return {
        "product": product_name(row),
        "material_id": str(row.get("materialCode") or "").strip(),
        "material_name": str(row.get("materialName") or "").strip(),
        "material_type": material_type(row, file),
        "material_source": SOURCE_MAP.get(row.get("dataFromType"), f"dataFromType={row.get('dataFromType')}"),
        "status": str(row.get("status") or "").strip(),
        "ad_count": as_int(row.get("adCount")),
        "linked_ad_count": len(ids),
        "linked_ad_ids": ",".join(ids),
        "created_at": str(row.get("createdAt") or "").strip(),
        "updated_at": str(row.get("updatedAt") or "").strip(),
        "post_url": post_url,
        "preview_url": preview_url,
        "play_url": play_url,
        "file_type": str(file.get("mimeType") or file.get("suffix") or "").strip(),
        "file_size_mb": file_size_mb(file),
        "material_row_id": str(row.get("id") or "").strip(),
        "file_id": str(file.get("id") or "").strip(),
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(max_pages: int = 0) -> dict[str, Any]:
    sources = token_sources()
    if not sources:
        raise RuntimeError("DMS token is missing. Set SKT_DMS_TOKEN or DMS_TOKEN in the user environment.")

    host = env_first("SKT_DMS_HOST", default=DEFAULT_HOST)
    country = env_first("SKT_DMS_COUNTRY", default=DEFAULT_COUNTRY)
    page_size = int(env_first("SKT_DMS_PAGE_SIZE", default=str(DEFAULT_PAGE_SIZE)))
    run_date = date.today().strftime("%Y%m%d")

    DMS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    total = 0
    used_source = ""
    failures: list[str] = []
    for source, token in sources:
        try:
            rows, total = fetch_rows(token, host=host, country=country, page_size=page_size, max_pages=max_pages)
            used_source = source
            if total or rows:
                break
        except Exception as exc:
            failures.append(f"{source}: {str(exc).splitlines()[0][:160]}")
    if not used_source:
        detail = "; ".join(failures[:5])
        raise RuntimeError(f"DMS refresh failed for all configured token sources. {detail}")

    raw_path = DMS_DIR / f"raw_allpage_{country}_{run_date}.json"
    raw_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    normalized = [normalize_row(row) for row in rows]
    fieldnames = [
        "product",
        "material_id",
        "material_name",
        "material_type",
        "material_source",
        "status",
        "ad_count",
        "linked_ad_count",
        "linked_ad_ids",
        "created_at",
        "updated_at",
        "post_url",
        "preview_url",
        "play_url",
        "file_type",
        "file_size_mb",
        "material_row_id",
        "file_id",
    ]
    csv_path = DMS_DIR / f"skt_dms_materials_{run_date}.csv"
    latest_path = DMS_DIR / "skt_dms_materials_latest.csv"
    write_csv(csv_path, normalized, fieldnames)
    write_csv(latest_path, normalized, fieldnames)

    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "country": country,
        "api_total": total,
        "rows": len(rows),
        "token_source": used_source,
        "csv": str(csv_path),
        "latest_csv": str(latest_path),
        "raw": str(raw_path),
    }
    summary_path = DMS_DIR / f"skt_dms_materials_summary_{run_date}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SKT Singapore DMS material metadata into local CSV cache.")
    parser.add_argument("--max-pages", type=int, default=0, help="Optional cap for smoke tests. 0 means all pages.")
    args = parser.parse_args()
    summary = run(max_pages=args.max_pages)
    print(json.dumps({key: value for key, value in summary.items() if key not in {"raw"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
