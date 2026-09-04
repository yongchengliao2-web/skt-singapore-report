from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "dms" / "skt_dms_commerce_latest.json"
BASE_URL = "https://web.cerahdms.com/api"
SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
PLATFORMS = {"Shopee": "0", "TikTok": "1"}
EXPECTED_SHOPEE_SHOPS = {"1122745773", "973870035", "1414481946"}
EXPECTED_TIKTOK_SHOPS = {"SGLCELLL9K"}
DEFAULT_START = date(2026, 1, 1)
ORDER_STATUSES = ["1", "2", "3"]


class DmsRefreshError(RuntimeError):
    pass


def parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def date_strings(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]


def numeric(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        result = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise DmsRefreshError(f"DMS returned a non-numeric value: {value!r}") from exc
    if not math.isfinite(result) or result < 0:
        raise DmsRefreshError(f"DMS returned an invalid numeric value: {value!r}")
    return result


def read_token() -> str:
    token = os.environ.get("SKT_DMS_TOKEN", "").strip()
    if not token:
        token_file = Path.home() / "Desktop" / "DMS-token.txt"
        if token_file.is_file():
            for line in token_file.read_text(encoding="utf-8-sig").splitlines():
                if line.strip():
                    token = line.strip()
                    break
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if len(token) < 20 or token.casefold() in {"token", "your token", "bearer token"}:
        raise DmsRefreshError("SKT DMS token is missing or invalid")
    return f"Bearer {token}"


class DmsClient:
    def __init__(self, token: str, retries: int = 3, timeout: int = 180) -> None:
        self.token = token
        self.retries = retries
        self.timeout = timeout
        self._shops: dict[str, list[str]] | None = None

    def post(self, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        url = f"{BASE_URL.rstrip('/')}/{path.lstrip('/') }"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Authorization": self.token,
                    "Content-Type": "application/json;charset=UTF-8",
                    "User-Agent": "SKT-DMS-Commerce-Refresh/1.0",
                },
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.load(response)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise DmsRefreshError(f"DMS request failed at {path}: {type(exc).__name__}") from exc
                time.sleep(min(10, 2**attempt))
                continue
            if not isinstance(result, Mapping):
                raise DmsRefreshError(f"DMS returned an invalid response at {path}")
            try:
                code = int(result.get("code"))
            except (TypeError, ValueError):
                code = 0
            if code == 200:
                return result
            message = str(result.get("message") or result.get("msg") or "unknown error").replace("\n", " ")
            if code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= self.retries:
                raise DmsRefreshError(f"DMS business code={code} at {path}: {message[:180]}")
            time.sleep(min(10, 2**attempt))
        raise DmsRefreshError(f"DMS request exhausted retries at {path}") from last_error

    def shops(self) -> dict[str, list[str]]:
        if self._shops is not None:
            return self._shops
        payload = self.post("admin-api/financial/shop-manage/list", {})
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise DmsRefreshError("DMS shop list is not an array")
        shops = {"0": [], "1": []}
        for row in rows:
            if not isinstance(row, Mapping) or str(row.get("region", "")).upper() != "SG":
                continue
            platform = str(row.get("platform", ""))
            shop_id = str(row.get("shopId", "")).strip()
            if platform in shops and shop_id:
                shops[platform].append(shop_id)
        if not EXPECTED_SHOPEE_SHOPS.issubset(set(shops["0"])):
            raise DmsRefreshError("SKT DMS token does not expose all expected Shopee shops")
        if not EXPECTED_TIKTOK_SHOPS.issubset(set(shops["1"])):
            raise DmsRefreshError("SKT DMS token does not expose the expected TikTok shop")
        self._shops = {key: sorted(set(value)) for key, value in shops.items()}
        return self._shops

    def gmv(self, platform: str, day: str) -> Mapping[str, Any]:
        payload = self.post(
            {
                "Shopee": "admin-api/financial/shopee/stat/stat-region-gmv",
                "TikTok": "admin-api/financial/tt-statistics/order-status-stat-region",
            }[platform],
            {
                "regions": ["SG"],
                "orderMainStatus": ORDER_STATUSES,
                "createTime": {"start": day, "end": day},
            },
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise DmsRefreshError(f"DMS {platform} GMV for {day} is not an array")
        if not rows:
            return {"salesPriceSumRmb": 0, "orderCountSum": 0}
        if len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise DmsRefreshError(f"DMS {platform} GMV for {day} returned {len(rows)} rows")
        return rows[0]

    def sales(self, platform: str, start: str, end: str) -> list[Mapping[str, Any]]:
        payload = self.post(
            "admin-api/financial/sku-sale/daily-statistics",
            {
                "region": "SG",
                "platform": PLATFORMS[platform],
                "shopCodes": self.shops()[PLATFORMS[platform]],
                "recordDateParam": {"start": start, "end": end},
            },
        )
        rows = payload.get("data")
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            raise DmsRefreshError(f"DMS {platform} sales returned invalid data")
        return rows


def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {"gmv": [], "units": []}
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DmsRefreshError(f"DMS cache cannot be read: {type(exc).__name__}") from exc
    if cache.get("brand") != "SKT" or not isinstance(cache.get("gmv"), list) or not isinstance(cache.get("units"), list):
        raise DmsRefreshError("DMS cache schema is invalid")
    return cache


def previous_month_day(value: str) -> str:
    current = date.fromisoformat(value)
    month = current.month - 1 or 12
    year = current.year if current.month > 1 else current.year - 1
    day = min(current.day, (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day)
    return date(year, month, day).isoformat()


def normalize_sales(platform: str, rows: list[Mapping[str, Any]], dates: set[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        sku = str(row.get("sku") or "").strip()
        if not sku or sku.casefold() == "total":
            continue
        daily = row.get("dailyDatas")
        if not isinstance(daily, list):
            raise DmsRefreshError(f"DMS {platform} SKU {sku} has invalid daily data")
        for daily_row in daily:
            if not isinstance(daily_row, Mapping):
                continue
            day = str(daily_row.get("recordDate") or "").strip()
            if day not in dates:
                continue
            output.append(
                {
                    "date": day,
                    "platform": platform,
                    "sku": sku,
                    "product": str(row.get("skuName") or "").strip(),
                    "units": int(round(numeric(daily_row.get("count")))),
                }
            )
    return output


def write_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_PATH.with_suffix(".json.part")
    temporary.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(CACHE_PATH)


def refresh(start: date, end: date, force_days: int) -> dict[str, Any]:
    client = DmsClient(read_token())
    shops = client.shops()
    cache = load_cache()
    gmv_rows = {
        (str(row.get("date")), str(row.get("platform"))): row
        for row in cache.get("gmv", [])
        if row.get("date") and row.get("platform")
    }
    refresh_from = max(start, end - timedelta(days=max(force_days - 1, 0))).isoformat()
    missing_or_stale = {
        (day, platform)
        for day in date_strings(start, end)
        for platform in PLATFORMS
        if day >= refresh_from or (day, platform) not in gmv_rows
    }
    for day, platform in sorted(missing_or_stale):
        row = client.gmv(platform, day)
        gmv_rows[(day, platform)] = {
            "date": day,
            "platform": platform,
            "gmv_rmb": round(numeric(row.get("salesPriceSumRmb")), 2),
            "orders": int(round(numeric(row.get("orderCountSum")))),
        }

    dates = set(date_strings(start, end))
    unit_rows: list[dict[str, Any]] = []
    for platform in PLATFORMS:
        unit_rows.extend(normalize_sales(platform, client.sales(platform, start.isoformat(), end.isoformat()), dates))

    cache = {
        "schema_version": 1,
        "brand": "SKT",
        "country": "SG",
        "currency": "RMB",
        "date_start": min(row["date"] for row in gmv_rows.values()),
        "date_end": max(row["date"] for row in gmv_rows.values()),
        "generated_at": datetime.now(SHANGHAI_TIMEZONE).isoformat(timespec="seconds"),
        "shops": shops,
        "gmv": sorted(gmv_rows.values(), key=lambda row: (row["date"], row["platform"])),
        "units": sorted(unit_rows, key=lambda row: (row["date"], row["platform"], row["sku"])),
        "source": {
            "gmv": "DMS stat-region-gmv / order-status-stat-region",
            "units": "DMS sku-sale/daily-statistics",
            "gmv_currency": "DMS salesPriceSumRmb, already RMB",
        },
    }
    write_cache(cache)
    return {
        "cache": str(CACHE_PATH),
        "date_range": [cache["date_start"], cache["date_end"]],
        "gmv_rows": len(cache["gmv"]),
        "unit_rows": len(cache["units"]),
        "shops": {key: len(value) for key, value in shops.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh SKT platform GMV and SKU units from DMS.")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=datetime.now(SHANGHAI_TIMEZONE).date() - timedelta(days=1))
    parser.add_argument("--force-days", type=int, default=7)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.start > args.end:
        raise SystemExit("--start must be no later than --end")
    if args.check:
        result = load_cache()
        print(json.dumps({"cache": str(CACHE_PATH), "date_range": [result.get("date_start"), result.get("date_end")], "gmv_rows": len(result.get("gmv", [])), "unit_rows": len(result.get("units", []))}, ensure_ascii=False))
        return
    print(json.dumps(refresh(args.start, args.end, args.force_days), ensure_ascii=False))


if __name__ == "__main__":
    main()
