from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import os
from pathlib import Path
import sys
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


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


def download_assets(
    base_url: str,
    assets: list[tuple[str, Path]],
    password: str = "",
    session_token: str = "",
) -> None:
    cookies = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))
    opener.addheaders = [("User-Agent", "skt-report-asset-preserver/1.0")]

    if session_token:
        opener.addheaders.append(("Cookie", f"skt_report_auth={session_token}"))
    elif password:
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

    for remote_path, output_path in assets:
        asset_url = urljoin(base_url.rstrip("/") + "/", remote_path.lstrip("/"))
        request = Request(asset_url, headers={"Cache-Control": "no-cache"})
        with opener.open(request, timeout=120) as response:
            content = response.read()
        validate_html(output_path, content)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = output_path.with_suffix(output_path.suffix + ".part")
        part_path.write_bytes(content)
        part_path.replace(output_path)
        print(f"Preserved {remote_path} -> {output_path} ({len(content)} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download authenticated SKT report assets without browser automation.")
    parser.add_argument("--base-url", default="https://skt-singapore-report.pages.dev/")
    parser.add_argument("--password-env", default="SKT_REPORT_PASSWORD")
    parser.add_argument("--session-token-env", default="SKT_REPORT_SESSION_TOKEN")
    parser.add_argument("--asset", action="append", type=parse_asset, required=True)
    args = parser.parse_args()

    password = os.environ.get(args.password_env, "")
    session_token = os.environ.get(args.session_token_env, "")
    if not password and not session_token:
        parser.error(
            f"environment variable {args.session_token_env} or {args.password_env} must be set"
        )

    try:
        download_assets(
            args.base_url,
            args.asset,
            password=password,
            session_token=session_token,
        )
    except Exception as error:
        print(f"Asset preservation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
