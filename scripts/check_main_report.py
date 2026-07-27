from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any


DATA_MARKER = "const DATA = "
SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
REQUIRED_FRESHNESS = (
    "sp_gmv",
    "tt_gmv",
    "offsite",
    "onsite_ads",
    "onsite_products",
)


def read_report_payload(path: Path) -> dict[str, Any]:
    html = path.read_text(encoding="utf-8")
    marker_index = html.find(DATA_MARKER)
    if marker_index < 0:
        raise RuntimeError(f"main report DATA payload is missing: {path}")
    json_start = marker_index + len(DATA_MARKER)
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[json_start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"main report DATA payload is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"main report DATA payload is not an object: {path}")
    return payload


def expected_minimum_date(now: datetime | None = None) -> str:
    current = now or datetime.now(SHANGHAI_TIMEZONE)
    return (current.date() - timedelta(days=1)).isoformat()


def validate_freshness(payload: dict[str, Any], minimum_date: str) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    freshness = summary.get("freshness") or {}
    stale = {
        key: str(freshness.get(key) or "")
        for key in REQUIRED_FRESHNESS
        if str(freshness.get(key) or "") < minimum_date
    }
    if stale:
        details = ", ".join(f"{key}={value or 'missing'}" for key, value in stale.items())
        raise RuntimeError(f"main report source is older than {minimum_date}: {details}")
    return {
        "generated_at": summary.get("generated_at"),
        "report_date": summary.get("report_date"),
        "date_start": summary.get("date_start"),
        "date_end": summary.get("date_end"),
        "minimum_date": minimum_date,
        "freshness": {key: freshness.get(key) for key in REQUIRED_FRESHNESS},
        "daily_rows": len(payload.get("daily_rows") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate SKT main-report source freshness.")
    parser.add_argument("html", type=Path)
    parser.add_argument("--minimum-date", default="")
    args = parser.parse_args()

    result = validate_freshness(
        read_report_payload(args.html),
        args.minimum_date or expected_minimum_date(),
    )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
