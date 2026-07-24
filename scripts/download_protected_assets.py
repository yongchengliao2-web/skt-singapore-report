from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


SNAPSHOT_PATH_RE = re.compile(r'"snapshot_url"\s*:\s*"(/assets/material_snapshots/[A-Za-z0-9_.-]+)"')
USER_AGENT = "skt-report-asset-preserver/1.1"


def parse_asset(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("asset must use REMOTE_PATH=OUTPUT_PATH")
    remote_path, output_path = value.split("=", 1)
    if not remote_path.startswith("/") or not output_path.strip():
        raise argparse.ArgumentTypeError("asset must use /REMOTE_PATH=OUTPUT_PATH")
    return remote_path, Path(output_path)


def validate_html(output_path: Path, content: bytes) -> None:
    if len(content) < 1024:
        raise RuntimeError(f"downloaded page is unexpectedly small: {output_path}")
    if b'action="/__auth"' in content:
        raise RuntimeError(f"authentication failed while downloading: {output_path}")

    filename = output_path.name.lower()
    if "material" in filename and b"PAGE_DATA" not in content:
        raise RuntimeError(f"material page marker is missing: {output_path}")
    if filename in {"index.html", "skt-onsite-offsite-alignment.html"} and b"const DATA =" not in content:
        raise RuntimeError(f"main report marker is missing: {output_path}")


def authenticate(
    base_url: str,
    password: str = "",
    session_token: str = "",
) -> str:
    if session_token:
        return session_token

    cookies = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))
    opener.addheaders = [("User-Agent", USER_AGENT)]

    if password:
        login_url = urljoin(base_url.rstrip("/") + "/", "__auth")
        login_request = Request(
            login_url,
            data=urlencode({"password": password}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with opener.open(login_request, timeout=60) as response:
            response.read(1024)

        if not any(cookie.name == "skt_report_auth" for cookie in cookies):
            raise RuntimeError("report authentication did not return a session cookie")
    else:
        raise RuntimeError("neither a report session token nor a password was provided")

    return next(cookie.value for cookie in cookies if cookie.name == "skt_report_auth")


def validate_asset(remote_path: str, output_path: Path, content: bytes) -> None:
    if remote_path.startswith("/assets/material_snapshots/"):
        if len(content) < 512 or not content.startswith(b"\xff\xd8"):
            raise RuntimeError(f"downloaded snapshot is invalid: {remote_path}")
        return
    validate_html(output_path, content)


def download_asset(
    base_url: str,
    remote_path: str,
    output_path: Path,
    session_token: str,
) -> int:
    asset_url = urljoin(base_url.rstrip("/") + "/", remote_path.lstrip("/"))
    content = b""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = Request(
                asset_url,
                headers={
                    "Cache-Control": "no-cache",
                    "Cookie": f"skt_report_auth={session_token}",
                    "User-Agent": USER_AGENT,
                },
            )
            with urlopen(request, timeout=120) as response:
                content = response.read()
            validate_asset(remote_path, output_path, content)
            break
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(attempt + 1)
    else:
        raise RuntimeError(str(last_error or "asset download failed"))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_suffix(output_path.suffix + ".part")
    part_path.write_bytes(content)
    part_path.replace(output_path)
    return len(content)


def download_assets(
    base_url: str,
    assets: list[tuple[str, Path]],
    session_token: str,
    workers: int = 1,
    minimum_success_rate: float = 1.0,
) -> None:
    if not assets:
        return

    unique_assets = list(dict.fromkeys(assets))
    if workers <= 1 or len(unique_assets) == 1:
        for remote_path, output_path in unique_assets:
            size = download_asset(base_url, remote_path, output_path, session_token)
            print(f"Preserved {remote_path} -> {output_path} ({size} bytes)")
        return

    completed_count = 0
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(unique_assets))) as executor:
        future_assets = {
            executor.submit(download_asset, base_url, remote_path, output_path, session_token): (remote_path, output_path)
            for remote_path, output_path in unique_assets
        }
        for future in as_completed(future_assets):
            remote_path, output_path = future_assets[future]
            try:
                future.result()
                completed_count += 1
                if completed_count % 50 == 0 or completed_count == len(unique_assets):
                    print(f"Preserved {completed_count}/{len(unique_assets)} referenced snapshots")
            except Exception as error:
                failures.append(f"{remote_path}: {error}")

    if failures:
        success_rate = completed_count / len(unique_assets)
        preview = "; ".join(failures[:5])
        omitted = f"; {len(failures) - 5} more" if len(failures) > 5 else ""
        message = (
            f"{len(failures)} referenced snapshot downloads failed "
            f"({success_rate:.1%} succeeded): {preview}{omitted}"
        )
        if success_rate < minimum_success_rate:
            raise RuntimeError(message)
        print(f"WARNING: {message}", file=sys.stderr)


def referenced_snapshots(html_paths: list[Path], output_root: Path) -> list[tuple[str, Path]]:
    root = output_root.resolve()
    assets: list[tuple[str, Path]] = []
    for html_path in html_paths:
        if not html_path.exists():
            raise RuntimeError(f"snapshot reference page does not exist: {html_path}")
        content = html_path.read_text(encoding="utf-8")
        for remote_path in sorted(set(SNAPSHOT_PATH_RE.findall(content))):
            output_path = (root / remote_path.lstrip("/")).resolve()
            if root != output_path and root not in output_path.parents:
                raise RuntimeError(f"snapshot output escapes destination root: {remote_path}")
            assets.append((remote_path, output_path))
    return list(dict.fromkeys(assets))


def main() -> int:
    parser = argparse.ArgumentParser(description="Download authenticated SKT report assets without browser automation.")
    parser.add_argument("--base-url", default="https://skt-singapore-report.pages.dev/")
    parser.add_argument("--password-env", default="SKT_REPORT_PASSWORD")
    parser.add_argument("--session-token-env", default="SKT_REPORT_SESSION_TOKEN")
    parser.add_argument("--asset", action="append", type=parse_asset, default=[])
    parser.add_argument("--snapshot-html", action="append", type=Path, default=[])
    parser.add_argument("--snapshot-output-root", type=Path, default=Path(".preserved"))
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--minimum-success-rate", type=float, default=1.0)
    args = parser.parse_args()

    if not args.asset and not args.snapshot_html:
        parser.error("at least one --asset or --snapshot-html is required")
    if args.workers < 1 or args.workers > 32:
        parser.error("--workers must be between 1 and 32")
    if not 0.0 <= args.minimum_success_rate <= 1.0:
        parser.error("--minimum-success-rate must be between 0 and 1")

    password = os.environ.get(args.password_env, "")
    session_token = os.environ.get(args.session_token_env, "")
    if not password and not session_token:
        parser.error(
            f"environment variable {args.session_token_env} or {args.password_env} must be set"
        )

    try:
        session_token = authenticate(
            args.base_url,
            password=password,
            session_token=session_token,
        )
        download_assets(
            args.base_url,
            args.asset,
            session_token=session_token,
        )
        snapshot_assets = referenced_snapshots(args.snapshot_html, args.snapshot_output_root)
        download_assets(
            args.base_url,
            snapshot_assets,
            session_token=session_token,
            workers=args.workers,
            minimum_success_rate=args.minimum_success_rate,
        )
        if args.snapshot_html:
            print(f"Preserved {len(snapshot_assets)} snapshots referenced by material HTML")
    except Exception as error:
        print(f"Asset preservation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
