#!/usr/bin/env python3
"""Export SKT item-level Shopee voucher cost from BigQuery."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "cache" / "skt_voucher_item_daily.json"
PROJECT_ID = "advance-rush-406115"
DATASET_ID = "dim_shopee_ads_performance"
TABLE_ID = "sg_skt_onsite_voucher_cost_by_item"
SOURCE_TABLE = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
VOUCHER_FX_RATE = 5.23
COLUMNS = ("date", "product_id", "voucher_spend_sgd")

QUERY = f"""
WITH latest AS (
  SELECT *
  FROM `{SOURCE_TABLE}`
  WHERE brand_code = 'SKT'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY business_key
    ORDER BY loaded_at DESC, payload_hash DESC
  ) = 1
)
SELECT
  CAST(period_start AS STRING) AS date,
  CAST(item_id AS STRING) AS product_id,
  ROUND(SUM(net_voucher_cost_sgd), 6) AS voucher_spend_sgd,
  CAST(MAX(loaded_at) AS STRING) AS latest_loaded_at
FROM latest
WHERE net_voucher_cost_sgd IS NOT NULL
GROUP BY date, product_id
ORDER BY date, product_id
"""


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


def run_bq_query(bq_path: str) -> list[dict[str, Any]]:
    command = [
        bq_path,
        "query",
        "--use_legacy_sql=false",
        "--format=json",
        "--max_rows=100000",
        "--quiet",
        " ".join(QUERY.split()),
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
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("BigQuery voucher query returned no rows")
    return [dict(row) for row in payload if isinstance(row, dict)]


def build_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    keys: set[tuple[str, str]] = set()
    latest_loaded_at = ""
    for record in records:
        day = str(record.get("date") or "").strip()
        product_id = str(record.get("product_id") or "").strip()
        if not day or not product_id.isdigit():
            raise RuntimeError("BigQuery voucher row has an invalid date or product ID")
        try:
            spend_sgd = float(record.get("voucher_spend_sgd") or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("BigQuery voucher row has an invalid amount") from exc
        if not math.isfinite(spend_sgd):
            raise RuntimeError("BigQuery voucher row has a non-finite amount")
        key = (day, product_id)
        if key in keys:
            raise RuntimeError("BigQuery voucher rows contain duplicate date/product keys")
        keys.add(key)
        rows.append([day, product_id, round(spend_sgd, 6)])
        latest_loaded_at = max(latest_loaded_at, str(record.get("latest_loaded_at") or "").strip())
    rows.sort(key=lambda row: (row[0], int(row[1])))
    if not rows:
        raise RuntimeError("BigQuery voucher query returned no usable rows")
    return {
        "metadata": {
            "brand": "SKT",
            "source": "Shopee item voucher net cost via BigQuery",
            "source_table": SOURCE_TABLE,
            "definition": "seller voucher cost - Shopee voucher cost on the same order item",
            "currency": "SGD",
            "fx_rate": VOUCHER_FX_RATE,
            "display_currency": "RMB",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_loaded_at": latest_loaded_at or None,
            "date_start": rows[0][0],
            "date_end": rows[-1][0],
            "row_count": len(rows),
            "product_count": len({row[1] for row in rows}),
        },
        "columns": list(COLUMNS),
        "rows": rows,
    }


def validate_cache(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("columns") != list(COLUMNS):
        raise RuntimeError("voucher cache columns do not match the data contract")
    metadata = payload.get("metadata") or {}
    rows = payload.get("rows") or []
    if metadata.get("brand") != "SKT" or metadata.get("source_table") != SOURCE_TABLE:
        raise RuntimeError("voucher cache source is outside the SKT allowlist")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("voucher cache is empty")
    if int(metadata.get("row_count") or 0) != len(rows):
        raise RuntimeError("voucher cache row count does not reconcile")
    return payload


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--bq-path", default="")
    parser.add_argument(
        "--require-live",
        action="store_true",
        help="fail instead of using the existing cache when BigQuery is unavailable",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output)
    try:
        bq_path = resolve_bq(args.bq_path)
        if not bq_path:
            raise RuntimeError("BigQuery CLI is unavailable")
        payload = build_payload(run_bq_query(bq_path))
        write_payload(output, payload)
        validate_cache(output)
        print(json.dumps({
            "status": "live",
            "output": str(output),
            "rows": payload["metadata"]["row_count"],
            "products": payload["metadata"]["product_count"],
            "date_end": payload["metadata"]["date_end"],
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        if args.require_live or not output.is_file():
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
            return 1
        try:
            payload = validate_cache(output)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as cache_exc:
            print(json.dumps({"status": "failed", "error": str(cache_exc)}, ensure_ascii=False))
            return 1
        print(json.dumps({
            "status": "cache",
            "reason": str(exc),
            "output": str(output),
            "rows": payload["metadata"]["row_count"],
            "date_end": payload["metadata"]["date_end"],
        }, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
