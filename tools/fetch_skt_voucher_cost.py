#!/usr/bin/env python3
"""Export SKT item-level Shopee voucher cost from BigQuery."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
DEFAULT_OUTPUT = ROOT / "cache" / "skt_voucher_item_daily.json"
PROJECT_ID = "advance-rush-406115"
DATASET_ID = "dim_shopee_ads_performance"
TABLE_ID = "sg_skt_onsite_voucher_id_cost"
SOURCE_TABLE = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
DISTRIBUTION_TABLE = f"{PROJECT_ID}.{DATASET_ID}.sg_skt_onsite_voucher_cost_by_item"
LOCATION = "northamerica-northeast1"
VOUCHER_FX_RATE = 5.2
VOUCHER_FACTOR = 0.55
COLUMNS = ("date", "product_id", "voucher_spend_sgd")

QUERY = f"""
WITH latest_totals AS (
  SELECT *
  FROM `{SOURCE_TABLE}`
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY business_key
    ORDER BY loaded_at DESC, payload_hash DESC
  ) = 1
), daily_totals AS (
  SELECT period_start, ROUND(SUM(IFNULL(seller_voucher_cost, 0)) * {VOUCHER_FACTOR}, 2) AS daily_voucher_cost_sgd,
    MAX(loaded_at) AS latest_loaded_at
  FROM latest_totals GROUP BY period_start
), latest_items AS (
  SELECT * FROM `{DISTRIBUTION_TABLE}` WHERE brand_code = 'SKT'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY business_key ORDER BY loaded_at DESC, payload_hash DESC) = 1
), item_weights AS (
  SELECT period_start, CAST(item_id AS STRING) AS product_id,
    SUM(net_voucher_cost_sgd) AS voucher_distribution_weight
  FROM latest_items WHERE net_voucher_cost_sgd IS NOT NULL GROUP BY period_start, product_id
)
SELECT CAST(t.period_start AS STRING) AS date, COALESCE(i.product_id, '0') AS product_id,
  IFNULL(i.voucher_distribution_weight, 0) AS voucher_distribution_weight,
  t.daily_voucher_cost_sgd, CAST(t.latest_loaded_at AS STRING) AS latest_loaded_at
FROM daily_totals t LEFT JOIN item_weights i USING (period_start)
ORDER BY date, product_id
"""


def allocate_daily_cost(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconcile item weights to the same brand-day amount used by budget planning."""
    from decimal import Decimal

    by_day: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_day.setdefault(str(record['date']), []).append(record)
    allocated = []
    for day, items in by_day.items():
        totals = {Decimal(str(item['daily_voucher_cost_sgd'])) for item in items}
        if len(totals) != 1:
            raise ValueError(f'Inconsistent voucher daily totals: {day}')
        total = totals.pop()
        weights = [Decimal(str(item.get('voucher_distribution_weight') or 0)) for item in items]
        weight_total = sum(weights)
        if weight_total <= 0:
            allocated.append({**items[0], 'product_id': '0', 'voucher_spend_sgd': float(total)})
            continue
        remainder = total
        for index, (item, weight) in enumerate(zip(items, weights)):
            amount = remainder if index == len(items) - 1 else (total * weight / weight_total).quantize(Decimal('0.000001'))
            remainder -= amount
            allocated.append({**item, 'voucher_spend_sgd': float(amount)})
    return allocated


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
            "source": "Shopee seller voucher cost aligned with budget planning",
            "source_table": SOURCE_TABLE,
            "definition": "ROUND(SUM(seller_voucher_cost) * 0.55, 2) per day; allocated by item net voucher cost weights",
            "estimation_factor": VOUCHER_FACTOR,
            "distribution_source_table": DISTRIBUTION_TABLE,
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
    if metadata.get('estimation_factor') != VOUCHER_FACTOR or metadata.get('fx_rate') != VOUCHER_FX_RATE:
        raise RuntimeError('voucher cache does not use the budget planning contract')
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
        records = query_records(
            QUERY,
            project_id=PROJECT_ID,
            location=LOCATION,
            explicit_bq_path=args.bq_path,
        )
        if not records:
            raise RuntimeError("BigQuery voucher query returned no rows")
        payload = build_payload(allocate_daily_cost(records))
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
    except (OSError, ValueError, RuntimeError) as exc:
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
