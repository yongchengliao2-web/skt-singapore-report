from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def resolve_bq(explicit_path: str = "") -> str | None:
    candidates = [
        explicit_path,
        os.environ.get("BQ_PATH", ""),
        shutil.which("bq") or "",
        shutil.which("bq.cmd") or "",
        r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd",
        r"C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin\bq.cmd",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def run_cli_query(
    bq_path: str,
    query: str,
    *,
    project_id: str,
    location: str,
    max_rows: int = 100000,
) -> list[dict[str, Any]]:
    command = [
        bq_path,
        f"--project_id={project_id}",
        f"--location={location}",
        "query",
        "--use_legacy_sql=false",
        "--format=json",
        f"--max_rows={max_rows}",
        "--quiet",
        " ".join(query.split()),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "BigQuery query failed").strip()
        raise RuntimeError(error.splitlines()[-1][:500])
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("BigQuery returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise RuntimeError("BigQuery returned a non-array result")
    return [dict(row) for row in payload if isinstance(row, dict)]


def load_authorized_user_credentials() -> dict[str, str] | None:
    raw = os.environ.get("BQ_CREDENTIALS_JSON", "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("BQ_CREDENTIALS_JSON is not valid JSON") from exc
    required = ("client_id", "client_secret", "refresh_token")
    if payload.get("type") != "authorized_user" or any(not payload.get(key) for key in required):
        raise RuntimeError("BQ_CREDENTIALS_JSON is not a supported authorized-user credential")
    return {key: str(payload[key]) for key in required}


def refresh_access_token(credentials: dict[str, str]) -> str:
    body = urlencode(
        {
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "refresh_token": credentials["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("ascii")
    request = Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Google OAuth token refresh failed: {type(exc).__name__}") from exc
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Google OAuth token refresh returned no access token")
    return token


def request_json(url: str, token: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=data,
        method="POST" if body is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "SKT-BigQuery-Refresh/1.0",
        },
    )
    try:
        with urlopen(request, timeout=210) as response:
            payload = json.load(response)
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
            message = payload.get("error", {}).get("message")
        except (json.JSONDecodeError, AttributeError):
            message = ""
        detail = str(message or f"HTTP {exc.code}").replace("\n", " ")[:300]
        raise RuntimeError(f"BigQuery REST request failed: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"BigQuery REST request failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("BigQuery REST returned an invalid response")
    return payload


def decode_query_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields = [str(field.get("name") or "") for field in (payload.get("schema") or {}).get("fields", [])]
    if not fields:
        raise RuntimeError("BigQuery REST result is missing its schema")
    rows: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        cells = row.get("f") if isinstance(row, dict) else None
        if not isinstance(cells, list) or len(cells) != len(fields):
            raise RuntimeError("BigQuery REST result row does not match its schema")
        rows.append({field: cell.get("v") if isinstance(cell, dict) else None for field, cell in zip(fields, cells)})
    return rows


def run_rest_query(
    credentials: dict[str, str],
    query: str,
    *,
    project_id: str,
    location: str,
    max_rows: int = 100000,
) -> list[dict[str, Any]]:
    token = refresh_access_token(credentials)
    base_url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project_id}/queries"
    payload = request_json(
        f"{base_url}?location={location}",
        token,
        body={
            "query": query,
            "useLegacySql": False,
            "maxResults": str(max_rows),
            "timeoutMs": 180000,
        },
    )
    job_reference = payload.get("jobReference") or {}
    job_id = str(job_reference.get("jobId") or "").strip()
    for _ in range(6):
        if payload.get("jobComplete"):
            break
        if not job_id:
            raise RuntimeError("BigQuery REST query did not return a job ID")
        time.sleep(2)
        payload = request_json(
            f"{base_url}/{job_id}?location={location}&maxResults={max_rows}&timeoutMs=30000",
            token,
        )
    if not payload.get("jobComplete"):
        raise RuntimeError("BigQuery REST query did not finish before timeout")
    if payload.get("pageToken"):
        raise RuntimeError("BigQuery REST query exceeded the configured row limit")
    return decode_query_rows(payload)


def query_records(
    query: str,
    *,
    project_id: str,
    location: str,
    explicit_bq_path: str = "",
    max_rows: int = 100000,
) -> list[dict[str, Any]]:
    credentials = load_authorized_user_credentials()
    if credentials:
        return run_rest_query(
            credentials,
            query,
            project_id=project_id,
            location=location,
            max_rows=max_rows,
        )
    bq_path = resolve_bq(explicit_bq_path)
    if not bq_path:
        raise RuntimeError("BigQuery authentication is unavailable")
    return run_cli_query(
        bq_path,
        query,
        project_id=project_id,
        location=location,
        max_rows=max_rows,
    )
