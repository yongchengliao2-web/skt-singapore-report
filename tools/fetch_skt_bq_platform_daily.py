#!/usr/bin/env python3
"""Export SKT platform GMV, orders, and parent-level sales units from BigQuery."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

try:
    from bigquery_query import query_records
except ModuleNotFoundError:
    from tools.bigquery_query import query_records


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "cache" / "skt_bq_platform_daily.json"
PROJECT_ID = "advance-rush-406115"
DATASET_ID = "dim_shopee_ads_performance"
TABLE_ID = "sg_dms_gmv_sales_daily"
SOURCE_TABLE = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
LOCATION = "northamerica-northeast1"
BRAND = "SKT"
COUNTRY_CODE = "SG"
SCOPE = "parent"
PLATFORMS = ("Shopee", "TikTok")
COLUMNS = ("date", "platform", "gmv_rmb", "order_count", "sales_units")

QUERY = f"""
SELECT
  CAST(date AS STRING) AS date,
  platform,
  ROUND(gmv_rmb, 6) AS gmv_rmb,
  order_count,
  sales_units,
  CAST(gmv_loaded_at AS STRING) AS gmv_loaded_at,
  CAST(sales_loaded_at AS STRING) AS sales_loaded_at
FROM `{SOURCE_TABLE}`
WHERE country_code = '{COUNTRY_CODE}'
  AND brand = '{BRAND}'
  AND scope = '{SCOPE}'
  AND platform IN ('Shopee', 'TikTok')
ORDER BY date, platform
"""


def parse_non_negative(record: dict[str, Any], field: str) -> float:
    try:
        value = float(record.get(field) or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"BigQuery platform row has an invalid {field}") from exc
    if not math.isfinite(value) or value < 0:
        raise RuntimeError(f"BigQuery platform row has an invalid {field}")
    return value


def build_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[list[Any]] = []
    keys: set[tuple[str, str]] = set()
    platforms_by_date: dict[str, set[str]] = {}
    gmv_loaded_at = ""
    sales_loaded_at = ""
    today = date.today()
    for record in records:
        day = str(record.get("date") or "").strip()
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError as exc:
            raise RuntimeError("BigQuery platform row has an invalid date") from exc
        if parsed_day > today:
            raise RuntimeError("BigQuery platform row is dated in the future")
        platform = str(record.get("platform") or "").strip()
        if platform not in PLATFORMS:
            raise RuntimeError("BigQuery platform row is outside the platform allowlist")
        key = (day, platform)
        if key in keys:
            raise RuntimeError("BigQuery platform rows contain duplicate date/platform keys")
        keys.add(key)
        platforms_by_date.setdefault(day, set()).add(platform)
        gmv_rmb = parse_non_negative(record, "gmv_rmb")
        order_count = parse_non_negative(record, "order_count")
        sales_units = parse_non_negative(record, "sales_units")
        if not order_count.is_integer() or not sales_units.is_integer():
            raise RuntimeError("BigQuery platform orders and sales units must be integers")
        rows.append([day, platform, round(gmv_rmb, 6), int(order_count), int(sales_units)])
        gmv_loaded_at = max(gmv_loaded_at, str(record.get("gmv_loaded_at") or "").strip())
        sales_loaded_at = max(sales_loaded_at, str(record.get("sales_loaded_at") or "").strip())

    incomplete_dates = sorted(day for day, platforms in platforms_by_date.items() if platforms != set(PLATFORMS))
    if incomplete_dates:
        raise RuntimeError(f"BigQuery platform rows are missing a platform on {incomplete_dates[-1]}")
    rows.sort(key=lambda row: (row[0], row[1]))
    if not rows:
        raise RuntimeError("BigQuery platform query returned no usable rows")
    return {
        "metadata": {
            "brand": BRAND,
            "country_code": COUNTRY_CODE,
            "scope": SCOPE,
            "source": "SKT daily platform commerce via BigQuery",
            "source_table": SOURCE_TABLE,
            "currency": "RMB",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gmv_loaded_at": gmv_loaded_at or None,
            "sales_loaded_at": sales_loaded_at or None,
            "date_start": rows[0][0],
            "date_end": rows[-1][0],
            "row_count": len(rows),
            "day_count": len(platforms_by_date),
            "platforms": list(PLATFORMS),
        },
        "columns": list(COLUMNS),
        "rows": rows,
    }


def validate_cache(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("columns") != list(COLUMNS):
        raise RuntimeError("platform cache columns do not match the data contract")
    metadata = payload.get("metadata") or {}
    rows = payload.get("rows") or []
    expected_metadata = {
        "brand": BRAND,
        "country_code": COUNTRY_CODE,
        "scope": SCOPE,
        "source_table": SOURCE_TABLE,
        "currency": "RMB",
    }
    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
        raise RuntimeError("platform cache source is outside the SKT allowlist")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("platform cache is empty")
    if int(metadata.get("row_count") or 0) != len(rows):
        raise RuntimeError("platform cache row count does not reconcile")
    records = [dict(zip(COLUMNS, row)) for row in rows]
    rebuilt = build_payload(records)
    if rebuilt["rows"] != rows:
        raise RuntimeError("platform cache rows are not normalized")
    if metadata.get("date_start") != rows[0][0] or metadata.get("date_end") != rows[-1][0]:
        raise RuntimeError("platform cache date bounds do not reconcile")
    return payload


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
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
        records = query_records(
            QUERY,
            project_id=PROJECT_ID,
            location=LOCATION,
            explicit_bq_path=args.bq_path,
        )
        if not records:
            raise RuntimeError("BigQuery platform query returned no rows")
        payload = build_payload(records)
        write_payload(output, payload)
        validate_cache(output)
        print(json.dumps({
            "status": "live",
            "output": str(output),
            "rows": payload["metadata"]["row_count"],
            "days": payload["metadata"]["day_count"],
            "date_end": payload["metadata"]["date_end"],
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
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
