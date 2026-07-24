# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "output"
SITE_DIR = ROOT / "site"

SPREADSHEET_ID = "1d5dBa6AJsJNNcA23NoNJd4OX3douJ4gWmHa94vJdRpk"
DEFAULT_FX_RATE = 5.35
DEFAULT_OFFSITE_FX_RATE = 6.9
ONSITE_PRODUCT_IMPRESSION_INDEX = 13
ONSITE_PRODUCT_CLICK_INDEX = 14

SOURCES: dict[str, dict[str, Any]] = {
    "sp_gmv": {
        "sheet": "SP店铺实收GMV",
        "filename": "sp_store_gmv.csv",
        "fallbacks": ["sp_gmv.csv"],
    },
    "tt_gmv": {
        "sheet": "TT-销售GMV",
        "filename": "tt_sales_gmv.csv",
        "fallbacks": ["tt_gmv.csv"],
    },
    "offsite": {
        "sheet": "站外数据源",
        "filename": "offsite.csv",
        "fallbacks": ["offsite.csv"],
    },
    "onsite_ads": {
        "sheet": "站内广告",
        "filename": "onsite_ads.csv",
        "fallbacks": ["onsite_ads.csv"],
    },
    "onsite_products": {
        "sheet": "站内产品数据-skt",
        "filename": "onsite_products.csv",
        "fallbacks": ["onsite_products.csv"],
    },
    "sp_units": {
        "sheet": "SP-销量",
        "filename": "sp_units.csv",
        "fallbacks": ["SP-销量.csv"],
    },
    "tt_units": {
        "sheet": "TT-销量",
        "filename": "tt_units.csv",
        "fallbacks": ["TT-销量.csv"],
    },
    "category_map": {
        "sheet": "品类表",
        "filename": "category_map.csv",
        "fallbacks": ["品类表.csv"],
    },
}


def _download_sheet(sheet_name: str, destination: Path) -> None:
    cachebust = int(datetime.now().timestamp())
    url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?"
        f"tqx=out:csv&sheet={quote(sheet_name)}&cachebust={cachebust}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    request = Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urlopen(request, timeout=90) as response, part.open("wb") as handle:
        handle.write(response.read())
    part.replace(destination)


def ensure_source_csvs() -> dict[str, Path]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    resolved: dict[str, Path] = {}

    for key, config in SOURCES.items():
        target = RAW_DIR / config["filename"]
        downloaded = False
        try:
            _download_sheet(config["sheet"], target)
            downloaded = True
        except Exception:
            pass

        if key == "offsite":
            resolved[key] = resolve_offsite_source(target, config, prefer_target=downloaded)
            continue

        if downloaded:
            resolved[key] = target
            continue

        if target.exists() and target.stat().st_size > 0:
            resolved[key] = target
            continue

        copied = False
        for fallback in config.get("fallbacks", []):
            fallback_path = ROOT / fallback
            if fallback_path.exists() and fallback_path.stat().st_size > 0:
                shutil.copy2(fallback_path, target)
                copied = True
                break

        if not copied:
            _download_sheet(config["sheet"], target)

        resolved[key] = target

    return resolved


def parse_number(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text or text in {"-", "--", "#N/A", "nan", "None"}:
        return 0.0
    text = (
        text.replace(",", "")
        .replace("$", "")
        .replace("SGD", "")
        .replace("RMB", "")
        .replace("%", "")
        .strip()
    )
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_date(value: Any, default_year: int = 2026) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass

    match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", text)
    if match:
        return date(default_year, int(match.group(1)), int(match.group(2))).isoformat()

    return None


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def get_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row.get(name)
    lowered = {str(key).casefold(): key for key in row}
    for name in names:
        key = lowered.get(name.casefold())
        if key is not None:
            return row.get(key)
    return None


def profile_offsite_csv(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None

    profile: dict[str, Any] = {
        "path": path,
        "rows": 0,
        "dated_rows": 0,
        "dated_positive_rows": 0,
        "dated_spend": 0.0,
        "latest_date": "",
        "has_fx": False,
        "fx_counts": {},
    }
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [header for header in (reader.fieldnames or []) if header is not None]
        profile["has_fx"] = any(header.strip() in {"汇率", "Exchange Rate", "FX"} for header in headers)
        for row in reader:
            profile["rows"] += 1
            day = parse_date(get_value(row, "Date_start"))
            spend = parse_number(get_value(row, "Spend"))
            row_fx = parse_number(get_value(row, "汇率", "Exchange Rate", "FX"))
            if row_fx > 0:
                rounded_fx = round(row_fx, 6)
                profile["fx_counts"][rounded_fx] = profile["fx_counts"].get(rounded_fx, 0) + 1
            if not day:
                continue
            profile["dated_rows"] += 1
            profile["latest_date"] = max(profile["latest_date"], day)
            if spend > 0:
                profile["dated_positive_rows"] += 1
                profile["dated_spend"] += spend
    return profile


def infer_offsite_fx(profiles: list[dict[str, Any]]) -> float:
    fx_counts: dict[float, int] = {}
    for profile in profiles:
        for value, count in profile.get("fx_counts", {}).items():
            fx_counts[value] = fx_counts.get(value, 0) + count
    if fx_counts:
        return max(fx_counts.items(), key=lambda item: item[1])[0]
    return DEFAULT_OFFSITE_FX_RATE


def select_offsite_profile(profiles: list[dict[str, Any]], target: Path) -> dict[str, Any]:
    def score(profile: dict[str, Any]) -> tuple[float, ...]:
        is_target = 1.0 if profile["path"] == target else 0.0
        has_fx = 1.0 if profile.get("has_fx") else 0.0
        return (
            float(profile.get("dated_positive_rows") or 0),
            float(profile.get("dated_rows") or 0),
            float(profile.get("dated_spend") or 0.0),
            has_fx,
            is_target,
        )

    return max(profiles, key=score)


def write_offsite_with_fx(source: Path, destination: Path, fallback_fx: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    with source.open("r", encoding="utf-8-sig", newline="") as read_handle, part.open(
        "w", encoding="utf-8-sig", newline=""
    ) as write_handle:
        reader = csv.DictReader(read_handle)
        fieldnames = []
        for header in reader.fieldnames or []:
            if header is None or header == "" or header in fieldnames:
                continue
            fieldnames.append(header)
        if "汇率" not in fieldnames:
            fieldnames.append("汇率")

        writer = csv.DictWriter(write_handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in reader:
            cleaned = {field: row.get(field, "") for field in fieldnames}
            if parse_number(cleaned.get("汇率")) <= 0:
                cleaned["汇率"] = f"{fallback_fx:g}"
            writer.writerow(cleaned)
    part.replace(destination)


def resolve_offsite_source(target: Path, config: dict[str, Any], prefer_target: bool = False) -> Path:
    candidates: list[dict[str, Any]] = []
    target_profile = profile_offsite_csv(target)
    if target_profile:
        candidates.append(target_profile)

    for fallback in config.get("fallbacks", []):
        fallback_path = ROOT / fallback
        fallback_profile = profile_offsite_csv(fallback_path)
        if fallback_profile:
            candidates.append(fallback_profile)

    if not candidates:
        _download_sheet(config["sheet"], target)
        return target

    fallback_fx = infer_offsite_fx(candidates)
    if prefer_target and target_profile and target_profile.get("dated_rows"):
        write_offsite_with_fx(target_profile["path"], target, fallback_fx)
        return target

    selected = select_offsite_profile(candidates, target)
    write_offsite_with_fx(selected["path"], target, fallback_fx)
    return target


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_text(value: Any) -> str:
    text = clean_text(value).casefold()
    return re.sub(r"[\s_\-（）()【】\[\],，:/：]+", "", text)


def safe_cell(row: list[str], index: int) -> str:
    return clean_text(row[index]) if index < len(row) else ""


def put_mapping(mapping: dict[str, str], key: str, category: str) -> None:
    normalized = normalize_text(key)
    category = clean_text(category)
    if normalized and category and normalized not in mapping:
        mapping[normalized] = category


def load_category_reference(path: Path) -> dict[str, Any]:
    rows = read_csv_rows(path)
    item_id_to_category: dict[str, str] = {}
    item_name_to_category: dict[str, str] = {}
    sku_to_category: dict[str, str] = {}
    sku_name_to_category: dict[str, str] = {}
    shop_name_to_store: dict[str, str] = {}
    categories: set[str] = set()

    for row in rows[1:]:
        shop_name = safe_cell(row, 0)
        store_name = safe_cell(row, 1)
        if shop_name and store_name:
            put_mapping(shop_name_to_store, shop_name, store_name)

        item_id = safe_cell(row, 7)
        item_name = safe_cell(row, 8)
        item_category = safe_cell(row, 9)
        if item_category:
            categories.add(item_category)
        if item_id and item_category:
            put_mapping(item_id_to_category, item_id, item_category)
        if item_name and item_category:
            put_mapping(item_name_to_category, item_name, item_category)

        sku = safe_cell(row, 12)
        sku_name = safe_cell(row, 13)
        sku_category = safe_cell(row, 16)
        if sku_category:
            categories.add(sku_category)
        if sku and sku_category:
            put_mapping(sku_to_category, sku, sku_category)
        if sku_name and sku_category:
            put_mapping(sku_name_to_category, sku_name, sku_category)

    searchable_names: list[tuple[str, str]] = []
    for mapping in (item_name_to_category, sku_name_to_category):
        searchable_names.extend((name, category) for name, category in mapping.items() if len(name) >= 2)
    searchable_names.sort(key=lambda item: len(item[0]), reverse=True)

    keyword_categories = [category for category in categories if category and "+" not in category and len(category) >= 2]
    priority = {
        "气垫": 100,
        "粉底液": 96,
        "粉底": 95,
        "粉饼": 92,
        "防晒喷雾": 90,
        "防晒": 88,
        "补水喷雾": 84,
        "面霜": 80,
        "面膜": 76,
        "精华": 72,
        "唇部精华": 70,
        "洁面": 68,
        "爽肤水": 66,
        "棉片": 64,
        "眼霜": 62,
        "卸妆": 60,
    }
    keyword_categories.sort(key=lambda item: (priority.get(item, 0), len(item)), reverse=True)

    return {
        "item_id_to_category": item_id_to_category,
        "item_name_to_category": item_name_to_category,
        "sku_to_category": sku_to_category,
        "sku_name_to_category": sku_name_to_category,
        "shop_name_to_store": shop_name_to_store,
        "categories": sorted(categories),
        "searchable_names": searchable_names,
        "keyword_categories": keyword_categories,
        "category_count": len(categories),
        "item_count": len(item_id_to_category),
        "sku_count": len(sku_to_category),
        "shop_count": len(shop_name_to_store),
    }


def resolve_category(category_ref: dict[str, Any], *values: Any, default: str = "未归类") -> str:
    normalized_values = [normalize_text(value) for value in values if clean_text(value)]
    for mapping_name in ("item_id_to_category", "sku_to_category", "item_name_to_category", "sku_name_to_category"):
        mapping = category_ref.get(mapping_name, {})
        for value in normalized_values:
            if value in mapping:
                return mapping[value]

    for value in normalized_values:
        for name, category in category_ref.get("searchable_names", []):
            if name and (name in value or value in name):
                return category

    for value in normalized_values:
        for category in category_ref.get("keyword_categories", []):
            if normalize_text(category) in value:
                return category

    joined = " ".join(clean_text(value) for value in values)
    if "混合目录" in joined or "目录" in joined:
        return "多品类/目录"
    return default


def resolve_categories(category_ref: dict[str, Any], *values: Any) -> list[str]:
    joined = clean_text(" ".join(clean_text(value) for value in values))
    split_parts = [part for part in re.split(r"combo:|[+＋/&、，,]", joined) if part.strip()]
    if len(split_parts) > 1:
        categories = []
        for part in split_parts:
            category = resolve_category(category_ref, part, default="")
            if category and category not in categories:
                categories.append(category)
        return categories or ["多品类/目录"]
    return [resolve_category(category_ref, joined)]


def empty_daily_row(day: str) -> dict[str, Any]:
    return {
        "date": day,
        "sp_gmv_rmb": 0.0,
        "sp_gmv_sgd": 0.0,
        "sp_orders": 0.0,
        "tt_gmv_rmb": 0.0,
        "tt_gmv_local": 0.0,
        "tt_orders": 0.0,
        "platform_gmv_rmb": 0.0,
        "platform_orders": 0.0,
        "offsite_spend": 0.0,
        "offsite_spend_rmb": 0.0,
        "offsite_purchase_value": 0.0,
        "offsite_purchase_value_rmb": 0.0,
        "offsite_impressions": 0.0,
        "offsite_clicks": 0.0,
        "offsite_conversions": 0.0,
        "offsite_add_to_cart": 0.0,
        "media_spend_rmb": 0.0,
        "media_sales_rmb": 0.0,
        "onsite_spend_rmb": 0.0,
        "onsite_ad_gmv_rmb": 0.0,
        "onsite_impressions": 0.0,
        "onsite_clicks": 0.0,
        "onsite_conversions": 0.0,
        "onsite_items_sold": 0.0,
        "product_paid_sales_sgd": 0.0,
        "product_paid_sales_rmb": 0.0,
        "product_paid_units": 0.0,
        "product_visitors": 0.0,
        "product_page_views": 0.0,
        "product_add_to_cart_visitors": 0.0,
        "product_clicks": 0.0,
        "product_impressions": 0.0,
    }


def extract_fx_rate(path: Path) -> float:
    rows = read_csv_rows(path)
    for row in rows:
        values = list(row)
        for index, value in enumerate(values):
            if str(value).strip() == "汇率":
                for candidate in values[index + 1 :]:
                    parsed = parse_number(candidate)
                    if parsed > 0:
                        return parsed
    return DEFAULT_FX_RATE


def add_daily(daily: dict[str, dict[str, Any]], day: str) -> dict[str, Any]:
    return daily.setdefault(day, empty_daily_row(day))


def empty_category_day(day: str, category: str) -> dict[str, Any]:
    return {
        "date": day,
        "category": category,
        "product_paid_sales_rmb": 0.0,
        "product_paid_sales_sgd": 0.0,
        "product_paid_units": 0.0,
        "product_visitors": 0.0,
        "product_page_views": 0.0,
        "product_add_to_cart_visitors": 0.0,
        "product_clicks": 0.0,
        "product_impressions": 0.0,
        "sp_units": 0.0,
        "sp_prior_units": 0.0,
        "tt_units": 0.0,
        "tt_prior_units": 0.0,
        "onsite_spend_rmb": 0.0,
        "onsite_ad_gmv_rmb": 0.0,
        "onsite_impressions": 0.0,
        "onsite_clicks": 0.0,
        "onsite_conversions": 0.0,
        "onsite_items_sold": 0.0,
        "offsite_spend_rmb": 0.0,
        "offsite_purchase_value_rmb": 0.0,
        "offsite_spend": 0.0,
        "offsite_purchase_value": 0.0,
        "offsite_conversions": 0.0,
        "offsite_clicks": 0.0,
        "offsite_add_to_cart": 0.0,
    }


def add_category_day(
    category_daily: dict[tuple[str, str], dict[str, Any]], day: str, category: str
) -> dict[str, Any]:
    category = clean_text(category) or "未归类"
    return category_daily.setdefault((day, category), empty_category_day(day, category))


def load_sp_gmv(path: Path, daily: dict[str, dict[str, Any]], fx_rate: float) -> dict[str, dict[str, float]]:
    by_store: dict[str, dict[str, float]] = {}
    active_status = {"COMPLETED", "SHIPPED", "TO SHIP"}
    for row in read_csv(path):
        day = parse_date(get_value(row, "日期date"))
        if not day:
            continue
        status = str(get_value(row, "Order Status") or "").strip().upper()
        if status and status not in active_status:
            continue
        store = str(get_value(row, "店铺") or "未识别店铺").strip()
        orders = parse_number(get_value(row, "Order Count"))
        gmv_after_seller = parse_number(get_value(row, "GMV(After Seller Discounts)"))
        gmv_sgd = gmv_after_seller
        gmv_rmb = gmv_sgd * fx_rate

        item = add_daily(daily, day)
        item["sp_orders"] += orders
        item["sp_gmv_sgd"] += gmv_sgd
        item["sp_gmv_rmb"] += gmv_rmb

        store_row = by_store.setdefault(
            store,
            {"orders": 0.0, "gmv_sgd": 0.0, "gmv_rmb": 0.0, "gmv_after_seller_sgd": 0.0},
        )
        store_row["orders"] += orders
        store_row["gmv_sgd"] += gmv_sgd
        store_row["gmv_rmb"] += gmv_rmb
        store_row["gmv_after_seller_sgd"] += gmv_after_seller

    return by_store


def load_tt_gmv(path: Path, daily: dict[str, dict[str, Any]]) -> None:
    for row in read_csv(path):
        day = parse_date(get_value(row, "Order Date"))
        if not day:
            continue
        orders = parse_number(get_value(row, "Order Count"))
        gmv_local = parse_number(get_value(row, "GMV(After seller discounts)"))
        gmv_rmb = parse_number(get_value(row, "GMV(After seller discounts) RMB"))
        item = add_daily(daily, day)
        item["tt_orders"] += orders
        item["tt_gmv_local"] += gmv_local
        item["tt_gmv_rmb"] += gmv_rmb


def load_offsite(
    path: Path,
    daily: dict[str, dict[str, Any]],
    category_ref: dict[str, Any],
    category_daily: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_product: dict[tuple[str, str], dict[str, Any]] = {}
    by_product_day: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_type: dict[str, dict[str, Any]] = {}
    by_category: dict[str, dict[str, Any]] = {}
    for row in read_csv(path):
        day = parse_date(get_value(row, "Date_start"))
        if not day:
            continue
        spend = parse_number(get_value(row, "Spend"))
        purchase = parse_number(get_value(row, "Purchase Value"))
        row_fx = parse_number(get_value(row, "汇率", "Exchange Rate", "FX"))
        if row_fx <= 0:
            row_fx = 1.0
        spend_rmb = spend * row_fx
        purchase_rmb = purchase * row_fx
        impressions = parse_number(get_value(row, "Impressions"))
        clicks = parse_number(get_value(row, "link-Click", "all-click"))
        conversions = parse_number(get_value(row, "Conversions"))
        add_to_cart = parse_number(get_value(row, "Add_to_cart"))

        item = add_daily(daily, day)
        item["offsite_spend"] += spend
        item["offsite_spend_rmb"] += spend_rmb
        item["offsite_purchase_value"] += purchase
        item["offsite_purchase_value_rmb"] += purchase_rmb
        item["offsite_impressions"] += impressions
        item["offsite_clicks"] += clicks
        item["offsite_conversions"] += conversions
        item["offsite_add_to_cart"] += add_to_cart

        product = str(get_value(row, "产品") or "未归类产品").strip() or "未归类产品"
        categories = resolve_categories(category_ref, product, get_value(row, "Ad_name"), get_value(row, "campaign_name"))
        category_label = " / ".join(categories)
        product_row = by_product.setdefault(
            (product, category_label),
            {
                "product": product,
                "category": category_label,
                "spend": 0.0,
                "spend_rmb": 0.0,
                "purchase_value": 0.0,
                "purchase_value_rmb": 0.0,
                "impressions": 0.0,
                "conversions": 0.0,
                "clicks": 0.0,
                "add_to_cart": 0.0,
                "avg_fx": 0.0,
                "fx_weight": 0.0,
            },
        )
        product_row["spend"] += spend
        product_row["spend_rmb"] += spend_rmb
        product_row["purchase_value"] += purchase
        product_row["purchase_value_rmb"] += purchase_rmb
        product_row["impressions"] += impressions
        product_row["conversions"] += conversions
        product_row["clicks"] += clicks
        product_row["add_to_cart"] += add_to_cart
        product_row["avg_fx"] += row_fx * spend
        product_row["fx_weight"] += spend

        product_day_row = by_product_day.setdefault(
            (day, product, category_label),
            {
                "date": day,
                "product": product,
                "category": category_label,
                "spend": 0.0,
                "spend_rmb": 0.0,
                "purchase_value": 0.0,
                "purchase_value_rmb": 0.0,
                "impressions": 0.0,
                "conversions": 0.0,
                "clicks": 0.0,
                "add_to_cart": 0.0,
                "avg_fx": 0.0,
                "fx_weight": 0.0,
            },
        )
        product_day_row["spend"] += spend
        product_day_row["spend_rmb"] += spend_rmb
        product_day_row["purchase_value"] += purchase
        product_day_row["purchase_value_rmb"] += purchase_rmb
        product_day_row["impressions"] += impressions
        product_day_row["conversions"] += conversions
        product_day_row["clicks"] += clicks
        product_day_row["add_to_cart"] += add_to_cart
        product_day_row["avg_fx"] += row_fx * spend
        product_day_row["fx_weight"] += spend

        funnel_type = str(get_value(row, "类型") or "未标记").strip() or "未标记"
        type_row = by_type.setdefault(
            funnel_type,
            {
                "type": funnel_type,
                "spend": 0.0,
                "spend_rmb": 0.0,
                "purchase_value": 0.0,
                "purchase_value_rmb": 0.0,
                "conversions": 0.0,
                "clicks": 0.0,
            },
        )
        type_row["spend"] += spend
        type_row["spend_rmb"] += spend_rmb
        type_row["purchase_value"] += purchase
        type_row["purchase_value_rmb"] += purchase_rmb
        type_row["conversions"] += conversions
        type_row["clicks"] += clicks

        share = 1 / max(len(categories), 1)
        for category in categories:
            category_row = by_category.setdefault(
                category,
                {
                    "category": category,
                    "spend": 0.0,
                    "spend_rmb": 0.0,
                    "purchase_value": 0.0,
                    "purchase_value_rmb": 0.0,
                    "conversions": 0.0,
                    "clicks": 0.0,
                    "add_to_cart": 0.0,
                },
            )
            category_row["spend"] += spend * share
            category_row["spend_rmb"] += spend_rmb * share
            category_row["purchase_value"] += purchase * share
            category_row["purchase_value_rmb"] += purchase_rmb * share
            category_row["conversions"] += conversions * share
            category_row["clicks"] += clicks * share
            category_row["add_to_cart"] += add_to_cart * share

            category_day = add_category_day(category_daily, day, category)
            category_day["offsite_spend"] += spend * share
            category_day["offsite_spend_rmb"] += spend_rmb * share
            category_day["offsite_purchase_value"] += purchase * share
            category_day["offsite_purchase_value_rmb"] += purchase_rmb * share
            category_day["offsite_conversions"] += conversions * share
            category_day["offsite_clicks"] += clicks * share
            category_day["offsite_add_to_cart"] += add_to_cart * share

    def finalize_offsite_product_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in rows:
            row["avg_fx"] = row["avg_fx"] / row["fx_weight"] if row["fx_weight"] else None
            row.pop("fx_weight", None)
            row["roas"] = row["purchase_value_rmb"] / row["spend_rmb"] if row["spend_rmb"] else None
        return rows

    product_rows = finalize_offsite_product_rows(list(by_product.values()))
    product_daily_rows = finalize_offsite_product_rows(list(by_product_day.values()))
    type_rows = list(by_type.values())
    for row in type_rows:
        row["roas"] = row["purchase_value_rmb"] / row["spend_rmb"] if row["spend_rmb"] else None
    category_rows = list(by_category.values())
    for row in category_rows:
        row["roas"] = row["purchase_value_rmb"] / row["spend_rmb"] if row["spend_rmb"] else None

    product_rows.sort(key=lambda item: item["spend_rmb"], reverse=True)
    product_daily_rows.sort(key=lambda item: (item["date"], item["spend_rmb"]), reverse=True)
    type_rows.sort(key=lambda item: item["spend_rmb"], reverse=True)
    category_rows.sort(key=lambda item: item["spend_rmb"], reverse=True)
    return product_rows, type_rows, category_rows, product_daily_rows


def load_onsite_ads(
    path: Path,
    daily: dict[str, dict[str, Any]],
    category_ref: dict[str, Any],
    category_daily: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_type: dict[str, dict[str, Any]] = {}
    by_category: dict[str, dict[str, Any]] = {}
    for row in read_csv(path):
        day = parse_date(get_value(row, "日期date"))
        if not day:
            continue
        spend = parse_number(get_value(row, "广告花费-RMB"))
        gmv = parse_number(get_value(row, "广告GMV-RMB"))
        impressions = parse_number(get_value(row, "Impression"))
        clicks = parse_number(get_value(row, "Clicks"))
        conversions = parse_number(get_value(row, "Conversions"))
        items_sold = parse_number(get_value(row, "Items Sold"))

        item = add_daily(daily, day)
        item["onsite_spend_rmb"] += spend
        item["onsite_ad_gmv_rmb"] += gmv
        item["onsite_impressions"] += impressions
        item["onsite_clicks"] += clicks
        item["onsite_conversions"] += conversions
        item["onsite_items_sold"] += items_sold

        category = resolve_category(
            category_ref,
            get_value(row, "Product ID"),
            get_value(row, "链接"),
            get_value(row, "Ad Name"),
            default="泛店铺/无产品",
        )
        category_row = by_category.setdefault(
            category,
            {
                "category": category,
                "spend_rmb": 0.0,
                "gmv_rmb": 0.0,
                "impressions": 0.0,
                "clicks": 0.0,
                "conversions": 0.0,
                "items_sold": 0.0,
            },
        )
        category_row["spend_rmb"] += spend
        category_row["gmv_rmb"] += gmv
        category_row["impressions"] += impressions
        category_row["clicks"] += clicks
        category_row["conversions"] += conversions
        category_row["items_sold"] += items_sold

        category_day = add_category_day(category_daily, day, category)
        category_day["onsite_spend_rmb"] += spend
        category_day["onsite_ad_gmv_rmb"] += gmv
        category_day["onsite_impressions"] += impressions
        category_day["onsite_clicks"] += clicks
        category_day["onsite_conversions"] += conversions
        category_day["onsite_items_sold"] += items_sold

        ad_type = str(get_value(row, "Ads Type") or "未标记").strip() or "未标记"
        type_row = by_type.setdefault(
            ad_type,
            {"ad_type": ad_type, "spend_rmb": 0.0, "gmv_rmb": 0.0, "conversions": 0.0, "items_sold": 0.0},
        )
        type_row["spend_rmb"] += spend
        type_row["gmv_rmb"] += gmv
        type_row["conversions"] += conversions
        type_row["items_sold"] += items_sold

    rows = list(by_type.values())
    for row in rows:
        row["roas"] = row["gmv_rmb"] / row["spend_rmb"] if row["spend_rmb"] else None
    category_rows = list(by_category.values())
    for row in category_rows:
        row["roas"] = row["gmv_rmb"] / row["spend_rmb"] if row["spend_rmb"] else None
    rows.sort(key=lambda item: item["spend_rmb"], reverse=True)
    category_rows.sort(key=lambda item: item["spend_rmb"], reverse=True)
    return rows, category_rows


def load_onsite_products(
    path: Path,
    daily: dict[str, dict[str, Any]],
    fx_rate: float,
    category_ref: dict[str, Any],
    category_daily: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_category: dict[str, dict[str, Any]] = {}
    by_product: dict[tuple[str, str], dict[str, Any]] = {}
    by_product_day: dict[tuple[str, str, str], dict[str, Any]] = {}
    raw_rows = read_csv_rows(path)
    if not raw_rows:
        return [], [], []

    headers = raw_rows[0]
    for values in raw_rows[1:]:
        row = dict(zip(headers, values))
        day = parse_date(get_value(row, "日期date"))
        if not day:
            continue

        category = str(get_value(row, "品类") or "").strip()
        if not category:
            category = resolve_category(
                category_ref,
                get_value(row, "Item ID"),
                get_value(row, "链接"),
                get_value(row, "Product"),
                get_value(row, "SKU"),
            )
        product = str(get_value(row, "链接") or get_value(row, "Product") or "未命名单品").strip() or "未命名单品"
        paid_sales_sgd = parse_number(get_value(row, "Sales (Placed Order) (SGD)", "Sales (Paid Order) (SGD)"))
        row_fx = parse_number(get_value(row, "汇率", "Exchange Rate", "FX"))
        if row_fx <= 0:
            row_fx = fx_rate
        paid_sales_rmb = paid_sales_sgd * row_fx
        paid_units = parse_number(get_value(row, "Units (Paid Order)"))
        visitors = parse_number(get_value(row, "Product Visitors (Visit)"))
        page_views = parse_number(get_value(row, "Product Page Views"))
        add_to_cart_visitors = parse_number(get_value(row, "Product Visitors (Add to Cart)"))
        product_clicks = parse_number(
            values[ONSITE_PRODUCT_CLICK_INDEX] if len(values) > ONSITE_PRODUCT_CLICK_INDEX else ""
        )
        product_impressions = parse_number(
            values[ONSITE_PRODUCT_IMPRESSION_INDEX] if len(values) > ONSITE_PRODUCT_IMPRESSION_INDEX else ""
        )

        item = add_daily(daily, day)
        item["product_paid_sales_sgd"] += paid_sales_sgd
        item["product_paid_sales_rmb"] += paid_sales_rmb
        item["product_paid_units"] += paid_units
        item["product_visitors"] += visitors
        item["product_page_views"] += page_views
        item["product_add_to_cart_visitors"] += add_to_cart_visitors
        item["product_clicks"] += product_clicks
        item["product_impressions"] += product_impressions

        category_day = add_category_day(category_daily, day, category)
        category_day["product_paid_sales_sgd"] += paid_sales_sgd
        category_day["product_paid_sales_rmb"] += paid_sales_rmb
        category_day["product_paid_units"] += paid_units
        category_day["product_visitors"] += visitors
        category_day["product_page_views"] += page_views
        category_day["product_add_to_cart_visitors"] += add_to_cart_visitors
        category_day["product_clicks"] += product_clicks
        category_day["product_impressions"] += product_impressions

        category_row = by_category.setdefault(
            category,
            {
                "category": category,
                "paid_sales_sgd": 0.0,
                "paid_sales_rmb": 0.0,
                "paid_units": 0.0,
                "visitors": 0.0,
                "page_views": 0.0,
                "add_to_cart_visitors": 0.0,
                "product_clicks": 0.0,
                "product_impressions": 0.0,
            },
        )
        category_row["paid_sales_sgd"] += paid_sales_sgd
        category_row["paid_sales_rmb"] += paid_sales_rmb
        category_row["paid_units"] += paid_units
        category_row["visitors"] += visitors
        category_row["page_views"] += page_views
        category_row["add_to_cart_visitors"] += add_to_cart_visitors
        category_row["product_clicks"] += product_clicks
        category_row["product_impressions"] += product_impressions

        product_row = by_product.setdefault(
            (product, category),
            {
                "product": product,
                "category": category,
                "paid_sales_sgd": 0.0,
                "paid_sales_rmb": 0.0,
                "paid_units": 0.0,
                "visitors": 0.0,
                "page_views": 0.0,
                "add_to_cart_visitors": 0.0,
                "product_clicks": 0.0,
                "product_impressions": 0.0,
            },
        )
        product_row["paid_sales_sgd"] += paid_sales_sgd
        product_row["paid_sales_rmb"] += paid_sales_rmb
        product_row["paid_units"] += paid_units
        product_row["visitors"] += visitors
        product_row["page_views"] += page_views
        product_row["add_to_cart_visitors"] += add_to_cart_visitors
        product_row["product_clicks"] += product_clicks
        product_row["product_impressions"] += product_impressions

        product_day_row = by_product_day.setdefault(
            (day, product, category),
            {
                "date": day,
                "product": product,
                "category": category,
                "paid_sales_sgd": 0.0,
                "paid_sales_rmb": 0.0,
                "paid_units": 0.0,
                "visitors": 0.0,
                "page_views": 0.0,
                "add_to_cart_visitors": 0.0,
                "product_clicks": 0.0,
                "product_impressions": 0.0,
            },
        )
        product_day_row["paid_sales_sgd"] += paid_sales_sgd
        product_day_row["paid_sales_rmb"] += paid_sales_rmb
        product_day_row["paid_units"] += paid_units
        product_day_row["visitors"] += visitors
        product_day_row["page_views"] += page_views
        product_day_row["add_to_cart_visitors"] += add_to_cart_visitors
        product_day_row["product_clicks"] += product_clicks
        product_day_row["product_impressions"] += product_impressions

    rows = list(by_category.values())
    for row in rows:
        row["sales_per_visitor"] = row["paid_sales_rmb"] / row["visitors"] if row["visitors"] else None
        row["unit_conversion_rate"] = row["paid_units"] / row["visitors"] if row["visitors"] else None
        row["add_to_cart_rate"] = row["add_to_cart_visitors"] / row["visitors"] if row["visitors"] else None
        row["ctr"] = row["product_clicks"] / row["product_impressions"] if row["product_impressions"] else None

    def finalize_product_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in rows:
            row["sales_per_visitor"] = row["paid_sales_rmb"] / row["visitors"] if row["visitors"] else None
            row["unit_conversion_rate"] = row["paid_units"] / row["visitors"] if row["visitors"] else None
            row["add_to_cart_rate"] = row["add_to_cart_visitors"] / row["visitors"] if row["visitors"] else None
            row["ctr"] = row["product_clicks"] / row["product_impressions"] if row["product_impressions"] else None
        return rows

    product_rows = finalize_product_rows(list(by_product.values()))
    product_daily_rows = finalize_product_rows(list(by_product_day.values()))
    rows.sort(key=lambda item: item["paid_sales_rmb"], reverse=True)
    product_rows.sort(key=lambda item: item["paid_sales_rmb"], reverse=True)
    product_daily_rows.sort(key=lambda item: (item["date"], item["paid_sales_rmb"]), reverse=True)
    return rows, product_rows, product_daily_rows


def load_platform_units(
    path: Path,
    platform: str,
    category_ref: dict[str, Any],
    category_daily: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in read_csv(path):
        day = parse_date(get_value(row, "日期"))
        if not day:
            continue
        category = clean_text(get_value(row, "品类")) or resolve_category(
            category_ref, get_value(row, "SKU编码"), get_value(row, "产品")
        )
        units = parse_number(get_value(row, "销量"))
        prior_units = parse_number(get_value(row, "上月同期销售"))
        category_day = add_category_day(category_daily, day, category)
        if platform == "SP":
            category_day["sp_units"] += units
            category_day["sp_prior_units"] += prior_units
        else:
            category_day["tt_units"] += units
            category_day["tt_prior_units"] += prior_units
        rows.append(
            {
                "platform": platform,
                "date": day,
                "sku": get_value(row, "SKU编码"),
                "product": get_value(row, "产品"),
                "category": category,
                "units": units,
                "prior_units": prior_units,
            }
        )
    return rows


def add_category_derived(row: dict[str, Any]) -> dict[str, Any]:
    row["platform_units"] = row.get("sp_units", 0.0) + row.get("tt_units", 0.0)
    row["prior_platform_units"] = row.get("sp_prior_units", 0.0) + row.get("tt_prior_units", 0.0)
    row["unit_growth"] = (
        (row["platform_units"] - row["prior_platform_units"]) / row["prior_platform_units"]
        if row["prior_platform_units"]
        else None
    )
    row["sp_unit_share"] = row["sp_units"] / row["platform_units"] if row["platform_units"] else None
    row["tt_unit_share"] = row["tt_units"] / row["platform_units"] if row["platform_units"] else None
    row["onsite_roas"] = (
        row["onsite_ad_gmv_rmb"] / row["onsite_spend_rmb"] if row.get("onsite_spend_rmb") else None
    )
    row["offsite_roas"] = (
        row["offsite_purchase_value_rmb"] / row["offsite_spend_rmb"] if row.get("offsite_spend_rmb") else None
    )
    row["media_spend_rmb"] = row.get("onsite_spend_rmb", 0.0) + row.get("offsite_spend_rmb", 0.0)
    row["media_sales_rmb"] = row.get("onsite_ad_gmv_rmb", 0.0) + row.get("offsite_purchase_value_rmb", 0.0)
    row["media_roas"] = row["media_sales_rmb"] / row["media_spend_rmb"] if row["media_spend_rmb"] else None
    row["add_to_cart_rate"] = (
        row["product_add_to_cart_visitors"] / row["product_visitors"] if row.get("product_visitors") else None
    )
    row["unit_conversion_rate"] = (
        row["product_paid_units"] / row["product_visitors"] if row.get("product_visitors") else None
    )
    row["sales_per_visitor"] = (
        row["product_paid_sales_rmb"] / row["product_visitors"] if row.get("product_visitors") else None
    )
    row["onsite_ad_coverage"] = (
        row["onsite_ad_gmv_rmb"] / row["product_paid_sales_rmb"] if row.get("product_paid_sales_rmb") else None
    )
    row["media_spend_ratio"] = (
        row["media_spend_rmb"] / row["product_paid_sales_rmb"] if row.get("product_paid_sales_rmb") else None
    )
    return row


def summarize_category_daily(category_daily: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    by_category: dict[str, dict[str, Any]] = {}
    for row in category_daily.values():
        category = row["category"]
        target = by_category.setdefault(category, {"category": category})
        for key, value in row.items():
            if key in {"date", "category"}:
                continue
            target[key] = target.get(key, 0.0) + float(value or 0.0)
    rows = [add_category_derived(row) for row in by_category.values()]
    rows.sort(key=lambda item: item.get("product_paid_sales_rmb", 0.0), reverse=True)
    total_sales = sum(row.get("product_paid_sales_rmb", 0.0) for row in rows)
    total_media = sum(row.get("media_spend_rmb", 0.0) for row in rows)
    total_units = sum(row.get("platform_units", 0.0) for row in rows)
    for row in rows:
        row["sales_share"] = row.get("product_paid_sales_rmb", 0.0) / total_sales if total_sales else None
        row["media_share"] = row.get("media_spend_rmb", 0.0) / total_media if total_media else None
        row["unit_share"] = row.get("platform_units", 0.0) / total_units if total_units else None
    return rows


OFFSITE_FIELDS = (
    "offsite_spend",
    "offsite_spend_rmb",
    "offsite_purchase_value",
    "offsite_purchase_value_rmb",
    "offsite_conversions",
    "offsite_clicks",
    "offsite_add_to_cart",
)


def redistribute_catalog_offsite(
    category_daily: dict[tuple[str, str], dict[str, Any]], category_ref: dict[str, Any]
) -> None:
    real_categories = set(category_ref.get("categories", []))
    catalog_labels = {"多品类/目录"}
    excluded_labels = catalog_labels | {"未归类", "#N/A", "泛店铺/无产品"}
    weight_fields = (
        "product_paid_sales_rmb",
        "platform_units",
        "product_visitors",
        "onsite_ad_gmv_rmb",
        "onsite_spend_rmb",
    )

    for key, catalog_row in list(category_daily.items()):
        day, category = key
        if category not in catalog_labels:
            continue
        metrics = {field: float(catalog_row.get(field) or 0.0) for field in OFFSITE_FIELDS}
        if not any(metrics.values()):
            continue

        candidates = [
            row
            for (row_day, row_category), row in category_daily.items()
            if row_day == day
            and row_category not in excluded_labels
            and (not real_categories or row_category in real_categories)
        ]
        if not candidates:
            continue

        weighted_rows: list[tuple[dict[str, Any], float]] = []
        for field in weight_fields:
            weighted_rows = [(row, float(row.get(field) or 0.0)) for row in candidates if float(row.get(field) or 0.0) > 0]
            if weighted_rows:
                break
        if not weighted_rows:
            share = 1 / len(candidates)
            weighted_rows = [(row, share) for row in candidates]

        total_weight = sum(weight for _, weight in weighted_rows)
        if total_weight <= 0:
            continue

        for target, weight in weighted_rows:
            share = weight / total_weight
            for field, value in metrics.items():
                target[field] = float(target.get(field) or 0.0) + value * share
                catalog_row[field] = 0.0

        if not any(float(catalog_row.get(field) or 0.0) for field in catalog_row if field not in {"date", "category"}):
            category_daily.pop(key, None)


def finalize_daily_rows(daily: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [daily[day] for day in sorted(daily)]
    for row in rows:
        row["platform_gmv_rmb"] = row["sp_gmv_rmb"] + row["tt_gmv_rmb"]
        row["platform_orders"] = row["sp_orders"] + row["tt_orders"]
        row["sp_share"] = row["sp_gmv_rmb"] / row["platform_gmv_rmb"] if row["platform_gmv_rmb"] else None
        row["tt_share"] = row["tt_gmv_rmb"] / row["platform_gmv_rmb"] if row["platform_gmv_rmb"] else None
        row["onsite_roas"] = row["onsite_ad_gmv_rmb"] / row["onsite_spend_rmb"] if row["onsite_spend_rmb"] else None
        row["offsite_roas"] = (
            row["offsite_purchase_value_rmb"] / row["offsite_spend_rmb"] if row["offsite_spend_rmb"] else None
        )
        row["media_spend_rmb"] = row["onsite_spend_rmb"] + row["offsite_spend_rmb"]
        row["media_sales_rmb"] = row["onsite_ad_gmv_rmb"] + row["offsite_purchase_value_rmb"]
        row["media_roas"] = row["media_sales_rmb"] / row["media_spend_rmb"] if row["media_spend_rmb"] else None
        row["onsite_spend_ratio"] = row["onsite_spend_rmb"] / row["platform_gmv_rmb"] if row["platform_gmv_rmb"] else None
        row["offsite_spend_ratio"] = (
            row["offsite_spend_rmb"] / row["platform_gmv_rmb"] if row["platform_gmv_rmb"] else None
        )
        row["media_spend_ratio"] = row["media_spend_rmb"] / row["platform_gmv_rmb"] if row["platform_gmv_rmb"] else None
        row["product_unit_conversion_rate"] = row["product_paid_units"] / row["product_visitors"] if row["product_visitors"] else None
        row["product_add_to_cart_rate"] = row["product_add_to_cart_visitors"] / row["product_visitors"] if row["product_visitors"] else None
    return rows


def sum_field(rows: list[dict[str, Any]], field: str) -> float:
    return sum(float(row.get(field) or 0.0) for row in rows)


def max_date_for(rows: list[dict[str, Any]], field: str) -> str:
    dates = [str(row.get("date")) for row in rows if row.get(field)]
    return max(dates) if dates else ""


def build_summary(daily_rows: list[dict[str, Any]], fx_rate: float) -> dict[str, Any]:
    totals = {field: sum_field(daily_rows, field) for field in daily_rows[0] if field != "date"} if daily_rows else {}
    totals["platform_gmv_rmb"] = sum_field(daily_rows, "platform_gmv_rmb")
    totals["platform_orders"] = sum_field(daily_rows, "platform_orders")
    totals["sp_share"] = totals["sp_gmv_rmb"] / totals["platform_gmv_rmb"] if totals.get("platform_gmv_rmb") else None
    totals["tt_share"] = totals["tt_gmv_rmb"] / totals["platform_gmv_rmb"] if totals.get("platform_gmv_rmb") else None
    totals["onsite_roas"] = totals["onsite_ad_gmv_rmb"] / totals["onsite_spend_rmb"] if totals.get("onsite_spend_rmb") else None
    totals["offsite_roas"] = (
        totals["offsite_purchase_value_rmb"] / totals["offsite_spend_rmb"]
        if totals.get("offsite_spend_rmb")
        else None
    )
    totals["media_spend_rmb"] = totals.get("onsite_spend_rmb", 0.0) + totals.get("offsite_spend_rmb", 0.0)
    totals["media_sales_rmb"] = totals.get("onsite_ad_gmv_rmb", 0.0) + totals.get("offsite_purchase_value_rmb", 0.0)
    totals["media_roas"] = totals["media_sales_rmb"] / totals["media_spend_rmb"] if totals.get("media_spend_rmb") else None
    totals["onsite_spend_ratio"] = totals["onsite_spend_rmb"] / totals["platform_gmv_rmb"] if totals.get("platform_gmv_rmb") else None
    totals["offsite_spend_ratio"] = (
        totals["offsite_spend_rmb"] / totals["platform_gmv_rmb"] if totals.get("platform_gmv_rmb") else None
    )
    totals["media_spend_ratio"] = (
        totals["media_spend_rmb"] / totals["platform_gmv_rmb"] if totals.get("platform_gmv_rmb") else None
    )
    totals["product_add_to_cart_rate"] = (
        totals["product_add_to_cart_visitors"] / totals["product_visitors"]
        if totals.get("product_visitors")
        else None
    )
    totals["product_unit_conversion_rate"] = (
        totals["product_paid_units"] / totals["product_visitors"] if totals.get("product_visitors") else None
    )
    return {
        "fx_rate": fx_rate,
        "date_start": daily_rows[0]["date"] if daily_rows else "",
        "date_end": daily_rows[-1]["date"] if daily_rows else "",
        "report_date": date.today().isoformat(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "totals": totals,
        "freshness": {
            "sp_gmv": max_date_for(daily_rows, "sp_gmv_rmb"),
            "tt_gmv": max_date_for(daily_rows, "tt_gmv_rmb"),
            "offsite": max_date_for(daily_rows, "offsite_spend_rmb"),
            "onsite_ads": max_date_for(daily_rows, "onsite_spend_rmb"),
            "onsite_products": max_date_for(daily_rows, "product_paid_sales_rmb"),
        },
    }


def build_payload() -> dict[str, Any]:
    paths = ensure_source_csvs()
    fx_rate = extract_fx_rate(paths["category_map"])
    category_ref = load_category_reference(paths["category_map"])
    daily: dict[str, dict[str, Any]] = {}
    category_daily: dict[tuple[str, str], dict[str, Any]] = {}

    store_rows = load_sp_gmv(paths["sp_gmv"], daily, fx_rate)
    load_tt_gmv(paths["tt_gmv"], daily)
    offsite_product_rows, offsite_type_rows, offsite_category_rows, offsite_product_daily_rows = load_offsite(
        paths["offsite"], daily, category_ref, category_daily
    )
    onsite_ad_type_rows, onsite_ad_category_rows = load_onsite_ads(
        paths["onsite_ads"], daily, category_ref, category_daily
    )
    product_category_rows, product_rows, product_daily_rows = load_onsite_products(
        paths["onsite_products"], daily, fx_rate, category_ref, category_daily
    )
    unit_rows = load_platform_units(paths["sp_units"], "SP", category_ref, category_daily) + load_platform_units(
        paths["tt_units"], "TT", category_ref, category_daily
    )
    redistribute_catalog_offsite(category_daily, category_ref)
    daily_rows = finalize_daily_rows(daily)
    category_daily_rows = [add_category_derived(row) for row in sorted(category_daily.values(), key=lambda item: (item["date"], item["category"]))]
    category_rows = summarize_category_daily(category_daily)

    payload = {
        "brand": "SKT",
        "market": "Singapore",
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit?usp=sharing",
        "summary": build_summary(daily_rows, fx_rate),
        "daily_rows": daily_rows,
        "store_rows": [
            {"store": store, **values}
            for store, values in sorted(store_rows.items(), key=lambda item: item[1]["gmv_rmb"], reverse=True)
        ],
        "category_rows": category_rows[:60],
        "category_daily_rows": category_daily_rows,
        "product_category_rows": product_category_rows[:60],
        "product_rows": product_rows[:80],
        "product_daily_rows": product_daily_rows,
        "offsite_product_rows": offsite_product_rows[:50],
        "offsite_product_daily_rows": offsite_product_daily_rows,
        "offsite_category_rows": offsite_category_rows[:50],
        "offsite_type_rows": offsite_type_rows,
        "onsite_ad_type_rows": onsite_ad_type_rows,
        "onsite_ad_category_rows": onsite_ad_category_rows[:50],
        "category_reference": {
            "category_count": category_ref["category_count"],
            "item_count": category_ref["item_count"],
            "sku_count": category_ref["sku_count"],
            "shop_count": category_ref["shop_count"],
        },
        "unit_rows_sample_size": len(unit_rows),
        "field_map": [
            {
                "module": "平台GMV-SP",
                "sheet": "SP店铺实收GMV",
                "date": "日期date",
                "metric": "GMV(After Seller Discounts)（I列）",
                "normalization": f"SGD x 汇率 {fx_rate:g} -> RMB",
            },
            {
                "module": "平台GMV-TT",
                "sheet": "TT-销售GMV",
                "date": "Order Date",
                "metric": "GMV(After seller discounts) RMB",
                "normalization": "直接使用 RMB 字段",
            },
            {
                "module": "站外",
                "sheet": "站外数据源",
                "date": "Date_start",
                "metric": "Spend / Purchase Value / 汇率 / Impressions / link-Click / Conversions",
                "normalization": "Spend 与 Purchase Value x 行级汇率 -> RMB；单品按品类表映射，混合目录按当日品类经营权重拆分",
            },
            {
                "module": "站内广告",
                "sheet": "站内广告",
                "date": "日期date",
                "metric": "广告花费-RMB / 广告GMV-RMB / Impression / Clicks / Conversions",
                "normalization": "使用源表 RMB 字段",
            },
            {
                "module": "站内商品",
                "sheet": "站内产品数据-skt",
                "date": "日期date",
                "metric": "Sales (Placed Order) (SGD) / 汇率 / Units / Visitors / ATC",
                "normalization": "L 列 Sales (Placed Order) (SGD) x 行级汇率 -> RMB 商品 GMV",
            },
            {
                "module": "品类映射",
                "sheet": "品类表",
                "date": "无",
                "metric": "Item ID / 单品 / SKU / 产品名 / 品类 / 汇率",
                "normalization": "用于 SP 汇率、单品/广告/销量品类归因；精确匹配优先，名称与关键词兜底",
            },
            {
                "module": "品类销量补充",
                "sheet": "SP-销量 / TT-销量",
                "date": "日期",
                "metric": "SKU编码 / 销量 / 上月同期销售 / 品类",
                "normalization": "按 SKU/产品/品类汇总到品类日维度，用于 SP/TT 销量结构与环比判断",
            },
        ],
    }
    return payload


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SKT 新加坡站内外联动看板</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <style>
    :root {
      --bg: #f4f7f5;
      --panel: #ffffff;
      --panel-soft: #eef5f2;
      --ink: #17382f;
      --muted: #667b73;
      --line: #d9e5df;
      --accent: #146b52;
      --accent-2: #c66b3d;
      --accent-3: #2f7ea0;
      --accent-4: #7f5ca8;
      --good: #0f766e;
      --bad: #b42318;
      --shadow: 0 12px 30px rgba(20, 63, 50, 0.08);
      --radius: 8px;
      font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.55;
      letter-spacing: 0;
      font-family: var(--font-family, "Microsoft YaHei", "PingFang SC", sans-serif);
    }
    a { color: inherit; }
    .page {
      width: min(1580px, calc(100vw - 96px));
      margin: 0 auto;
      padding: 18px 0 48px;
    }
    .shell {
      display: grid;
      gap: 14px;
      min-width: 0;
    }
    .hero {
      display: grid;
      place-items: center;
      min-height: 120px;
      padding: 28px;
      border-radius: 8px;
      background: #123f32;
      color: #fff;
      box-shadow: var(--shadow);
    }
    .hero-copy {
      min-width: 0;
      text-align: center;
    }
    .hero-eyebrow {
      margin-bottom: 8px;
      color: #bde2d8;
      font-size: 13px;
      font-weight: 900;
    }
    .hero h1 {
      margin: 0;
      font-size: clamp(34px, 3.4vw, 52px);
      line-height: 1.08;
      font-weight: 950;
      letter-spacing: 0;
    }
    .hero p {
      max-width: 860px;
      margin: 12px 0 0;
      color: rgba(255, 255, 255, 0.9);
      font-size: 16px;
      font-weight: 750;
    }
    .hero-status-card {
      display: grid;
      grid-template-columns: auto auto auto;
      align-items: center;
      justify-content: end;
      gap: 14px;
      min-height: 54px;
      padding: 13px 16px;
      border: 1px solid rgba(255, 255, 255, 0.24);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.11);
      color: #fff;
      white-space: nowrap;
    }
    .hero-status-card span {
      color: rgba(255, 255, 255, 0.78);
      font-size: 12px;
      font-weight: 850;
    }
    .hero-status-card strong {
      font-size: 14px;
      font-weight: 950;
    }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 40;
      border-bottom: 1px solid var(--line);
      background: rgba(244, 247, 245, 0.92);
      backdrop-filter: blur(14px);
    }
    .topbar-inner {
      width: min(1580px, calc(100vw - 96px));
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 0;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--ink);
      font-weight: 850;
      white-space: nowrap;
    }
    .mark {
      width: 30px;
      height: 30px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      color: #fff;
      background: var(--accent);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0;
    }
    .nav {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 4px;
    }
    .nav a {
      padding: 7px 9px;
      border-radius: 8px;
      color: var(--muted);
      text-decoration: none;
      font-size: 12px;
      font-weight: 800;
    }
    .nav a:hover {
      background: #fff;
      color: var(--ink);
    }
    .panel,
    .filters {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    .filters {
      position: sticky;
      top: 51px;
      z-index: 30;
      padding: 16px;
      background: rgba(246, 250, 248, 0.96);
      border-radius: 18px;
      backdrop-filter: blur(10px);
    }
    .report-title {
      margin: 0 0 12px;
      color: var(--ink);
      text-align: center;
      font-size: clamp(28px, 2.5vw, 38px);
      line-height: 1.05;
      font-weight: 900;
      letter-spacing: 0;
    }
    .filter-grid {
      display: grid;
      grid-template-columns: minmax(150px, 0.7fr) 1.15fr 1.15fr minmax(180px, 0.72fr) auto;
      gap: 8px;
      align-items: end;
    }
    .period-card {
      display: grid;
      grid-template-columns: auto minmax(130px, 1fr) minmax(130px, 1fr);
      gap: 8px;
      align-items: center;
      min-height: 48px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fbfdfc;
      min-width: 0;
    }
    .period-card .period-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 850;
      white-space: nowrap;
    }
    .filter-group {
      display: grid;
      gap: 5px;
      min-width: 0;
    }
    .filter-group label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 850;
    }
    select,
    input[type="date"] {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 8px 10px;
      background: #fbfdfc;
      color: var(--ink);
      font: inherit;
      font-size: 13px;
      font-weight: 750;
      outline: none;
    }
    select:focus,
    input[type="date"]:focus {
      border-color: rgba(20, 107, 82, 0.55);
      box-shadow: 0 0 0 3px rgba(20, 107, 82, 0.09);
    }
    .filter-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    .button {
      min-height: 38px;
      border: 1px solid var(--ink);
      border-radius: var(--radius);
      padding: 8px 13px;
      background: var(--ink);
      color: #fff;
      font: inherit;
      font-size: 13px;
      font-weight: 850;
      cursor: pointer;
    }
    .button:disabled {
      cursor: wait;
      opacity: 0.72;
    }
    .button.secondary {
      border-color: var(--line);
      background: #fbfdfc;
      color: var(--ink);
    }
    .refresh-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      min-width: 108px;
    }
    .refresh-icon {
      display: inline-block;
      font-size: 17px;
      line-height: 1;
    }
    .refresh-button.is-loading .refresh-icon {
      animation: refresh-spin 0.9s linear infinite;
    }
    .filter-feedback {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 8px;
    }
    .filter-feedback .section-note {
      margin: 0;
    }
    .refresh-status {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      text-align: right;
    }
    .refresh-status[data-state="success"] { color: #146b52; }
    .refresh-status[data-state="failure"] { color: #b42318; }
    @keyframes refresh-spin {
      to { transform: rotate(360deg); }
    }
    .metric-shell {
      padding: 20px 22px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #ffffff;
      box-shadow: var(--shadow);
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 14px;
      min-width: 0;
    }
    .metric {
      min-height: 150px;
      padding: 16px;
      border: 1px solid #cbdcf0;
      border-radius: 14px;
      background: #fbfdfc;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .metric .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 850;
    }
    .metric .value {
      margin-top: 8px;
      color: var(--ink);
      font-size: 24px;
      line-height: 1.08;
      font-weight: 900;
      word-break: break-word;
    }
    .metric-delta {
      margin-top: 7px;
      font-size: 12px;
      line-height: 1.3;
      font-weight: 850;
      color: var(--muted);
    }
    .metric-delta.up,
    .metric-sub-delta.up {
      color: #c40000;
    }
    .metric-delta.down,
    .metric-sub-delta.down {
      color: #008a3d;
    }
    .metric-divider {
      height: 1px;
      margin: 10px 0 8px;
      background: var(--line);
    }
    .metric-sub {
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
      font-weight: 800;
    }
    .metric-sub-delta { color: var(--muted); }
    .delta {
      display: inline-block;
      margin-left: 4px;
      color: var(--muted);
      font-weight: 800;
    }
    .delta.good { color: var(--good); }
    .delta.bad { color: var(--bad); }
    .section {
      padding: 18px;
      scroll-margin-top: 118px;
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-end;
      margin-bottom: 14px;
    }
    .section-head.compact { margin-bottom: 10px; }
    h2 {
      margin: 0;
      font-size: 22px;
      line-height: 1.25;
      font-weight: 900;
    }
    .section-note {
      margin: 5px 0 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1.35fr 1fr;
      gap: 14px;
    }
    .grid-even {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
      padding: 18px;
      min-width: 0;
    }
    .chart {
      width: 100%;
      height: 430px;
    }
    .chart.small {
      height: 300px;
    }
    .chart.medium {
      height: 360px;
    }
    .compare-chart-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }
    .compare-chart-card {
      min-width: 0;
      padding: 14px 14px 10px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    .compare-chart-title {
      margin: 0 0 4px;
      color: var(--ink);
      font-size: 16px;
      line-height: 1.3;
      font-weight: 900;
    }
    .chart.compare-chart {
      height: 250px;
    }
    .table-wrap {
      overflow: auto;
      max-height: 560px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fff;
    }
    .action-signal-panel {
      margin-top: 14px;
      overflow: hidden;
      border: 1px solid #d7c5a8;
      border-left: 5px solid #b7791f;
      border-radius: var(--radius);
      background: #fffdf8;
      box-shadow: 0 8px 22px rgba(96, 74, 42, 0.06);
    }
    .action-signal-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 24px;
      padding: 16px 18px 14px;
      border-bottom: 1px solid #e9dcc8;
    }
    .action-signal-heading {
      min-width: 0;
    }
    .action-signal-kicker {
      display: block;
      margin-bottom: 4px;
      color: #8a5b16;
      font-size: 11px;
      line-height: 1.2;
      font-weight: 900;
    }
    .action-signal-heading h3 {
      margin: 0;
      color: var(--ink);
      font-size: 18px;
      line-height: 1.35;
      font-weight: 900;
    }
    .action-signal-heading p,
    .action-signal-rule,
    .action-signal-footnote {
      margin: 5px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
      font-weight: 750;
    }
    .action-signal-rule {
      flex: 0 1 470px;
      margin: 1px 0 0;
      text-align: right;
    }
    .action-signal-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      min-width: 0;
    }
    .action-signal-group {
      min-width: 0;
      padding: 14px 18px 16px;
    }
    .action-signal-group + .action-signal-group {
      border-left: 1px solid #e9dcc8;
    }
    .action-signal-group-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 2px;
    }
    .action-signal-group-head strong {
      font-size: 14px;
      line-height: 1.3;
      font-weight: 900;
    }
    .action-signal-group.reduce .action-signal-group-head strong,
    .action-signal-group.reduce .signal-action {
      color: #a33a2b;
    }
    .action-signal-group.scale .action-signal-group-head strong,
    .action-signal-group.scale .signal-action {
      color: #0f766e;
    }
    .action-signal-count {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.3;
      font-weight: 850;
    }
    .action-signal-list {
      display: grid;
      padding: 0;
      margin: 0;
      list-style: none;
    }
    .action-signal-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      padding: 12px 0;
      border-bottom: 1px solid #eee4d4;
      min-width: 0;
    }
    .action-signal-item:last-child {
      border-bottom: 0;
    }
    .action-signal-copy {
      min-width: 0;
    }
    .action-signal-product {
      display: flex;
      align-items: baseline;
      gap: 8px;
      min-width: 0;
    }
    .action-signal-product strong {
      overflow-wrap: anywhere;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.35;
      font-weight: 900;
    }
    .action-signal-product span {
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
      font-weight: 750;
    }
    .action-signal-evidence {
      display: flex;
      flex-wrap: wrap;
      gap: 4px 12px;
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      font-weight: 750;
    }
    .signal-delta {
      margin-left: 3px;
      font-weight: 900;
    }
    .signal-delta.up { color: #c40000; }
    .signal-delta.down { color: #008a3d; }
    .signal-action {
      min-width: 64px;
      padding: 6px 8px;
      border: 1px solid currentColor;
      border-radius: 4px;
      text-align: center;
      font-size: 12px;
      line-height: 1.2;
      font-weight: 900;
    }
    .action-signal-empty {
      padding: 18px 0 14px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      font-weight: 750;
    }
    .action-signal-footnote {
      padding: 0 18px 14px;
      margin: 0;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 1180px;
      font-size: 13px;
      background: #fff;
    }
    th, td {
      padding: 10px 11px;
      border-bottom: 1px solid var(--line);
      text-align: right;
      white-space: nowrap;
    }
    th:first-child, td:first-child,
    th:nth-child(2), td:nth-child(2) {
      text-align: left;
    }
    thead th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f2f7f4;
      color: var(--muted);
      font-size: 12px;
      font-weight: 850;
    }
    tbody tr:nth-child(even) td {
      background: rgba(238, 245, 242, 0.42);
    }
    .empty-state {
      padding: 28px;
      color: var(--muted);
      font-size: 14px;
      font-weight: 800;
      text-align: center;
      background: #fff;
    }
    .analysis-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      width: min(100%, 1260px);
      margin: 0 auto;
      min-width: 0;
    }
    .insight-box {
      min-width: 0;
      min-height: 168px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fbfdfc;
      box-shadow: 0 8px 18px rgba(20, 63, 50, 0.045);
    }
    .insight-box h3 {
      margin: 0 0 8px;
      font-size: 15px;
      line-height: 1.35;
    }
    .insight-box p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.65;
    }
    .insight-box strong { color: var(--ink); }
    #narrative {
      width: min(100%, 1320px);
      margin-left: auto;
      margin-right: auto;
    }
    #narrative .section-head {
      justify-content: center;
      text-align: center;
    }
    .insight-list {
      display: grid;
      gap: 8px;
      padding: 0;
      margin: 0;
      list-style: none;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }
    .insight-list li {
      display: grid;
      grid-template-columns: 16px 1fr;
      gap: 8px;
    }
    .insight-list li::before {
      content: "";
      width: 8px;
      height: 8px;
      margin-top: 7px;
      border-radius: 50%;
      background: var(--accent);
    }
    .muted { color: var(--muted); }
    .side-nav {
      position: fixed;
      top: 128px;
      right: 18px;
      z-index: 20;
      width: 158px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.94);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
      transition: width 160ms ease, padding 160ms ease;
    }
    .side-nav-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .side-nav strong {
      display: block;
      flex: 1;
      padding: 4px 6px 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
    }
    .side-nav-toggle {
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfdfc;
      color: var(--ink);
      cursor: pointer;
      font-size: 18px;
      font-weight: 900;
      line-height: 1;
    }
    .side-nav-toggle:hover {
      background: var(--panel-soft);
      color: var(--accent);
    }
    .side-nav-icon {
      display: block;
      transform: translateY(-1px);
      transition: transform 160ms ease;
    }
    .side-nav-links {
      display: grid;
      gap: 2px;
    }
    .side-nav a {
      display: block;
      padding: 8px 7px;
      border-radius: 6px;
      color: var(--ink);
      text-decoration: none;
      font-size: 12px;
      font-weight: 800;
      line-height: 1.25;
    }
    .side-nav a:hover {
      background: var(--panel-soft);
      color: var(--accent);
    }
    .side-nav.is-collapsed {
      width: 42px;
      padding: 8px 6px;
    }
    .side-nav.is-collapsed .side-nav-head {
      justify-content: center;
    }
    .side-nav.is-collapsed strong,
    .side-nav.is-collapsed .side-nav-links {
      display: none;
    }
    .side-nav.is-collapsed .side-nav-icon {
      transform: rotate(180deg) translateY(1px);
    }
    @media (min-width: 1700px) {
      .page,
      .topbar-inner {
        --report-rail-width: 176px;
        --report-balanced-width: min(1580px, calc(100vw - 272px));
        width: var(--report-balanced-width);
        margin-left: calc((100vw - var(--report-rail-width) - var(--report-balanced-width)) / 2);
        margin-right: auto;
      }
      body.side-nav-collapsed .page,
      body.side-nav-collapsed .topbar-inner {
        --report-rail-width: 60px;
        --report-balanced-width: min(1580px, calc(100vw - 156px));
      }
    }
    @media (min-width: 1181px) and (max-width: 1699px) {
      .side-nav { display: none; }
    }
    .table-primary {
      font-weight: 800;
      color: var(--ink);
    }
    .table-compare {
      margin-top: 4px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.2;
      font-weight: 800;
    }
    .table-compare.up { color: #c40000; }
    .table-compare.down { color: #008a3d; }
    .product-cell {
      min-width: 220px;
      max-width: 360px;
      white-space: normal;
      line-height: 1.35;
      font-weight: 850;
      color: var(--ink);
    }
    .category-cell {
      min-width: 92px;
      color: var(--muted);
      font-weight: 800;
    }
    .offsite-product-table {
      width: 100%;
      min-width: 1100px;
      table-layout: fixed;
      font-size: 12px;
    }
    .offsite-product-table th,
    .offsite-product-table td {
      width: auto;
      padding-right: 7px;
      padding-left: 7px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .offsite-product-table th:first-child,
    .offsite-product-table td:first-child {
      width: 18%;
    }
    .offsite-product-table .product-cell {
      min-width: 0;
      max-width: none;
    }
    .offsite-product-table th:not(:first-child),
    .offsite-product-table td:not(:first-child) {
      text-align: right;
    }
    @media (max-width: 1180px) {
      .page, .topbar-inner { width: min(100% - 48px, 1580px); }
      .side-nav { display: none; }
      .hero { grid-template-columns: 1fr; }
      .hero-status-card { justify-content: start; width: 100%; }
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid-2, .grid-even { grid-template-columns: 1fr; }
      .compare-chart-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .analysis-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .action-signal-head { display: block; }
      .action-signal-rule { margin-top: 7px; text-align: left; }
      .filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .filter-actions { justify-content: flex-start; }
    }
    @media (max-width: 760px) {
      .page, .topbar-inner { width: min(100% - 24px, 1580px); }
      .page { padding: 14px 0 32px; }
      .topbar-inner { align-items: flex-start; flex-direction: column; }
      .hero { min-height: 0; padding: 18px; }
      .hero p { font-size: 14px; }
      .hero-status-card {
        grid-template-columns: 1fr;
        align-items: start;
        gap: 4px;
        white-space: normal;
      }
      .filters { position: static; }
      .filter-grid { grid-template-columns: 1fr; }
      .period-card { grid-template-columns: 1fr; }
      .filter-actions { width: 100%; }
      .filter-actions .button { flex: 1; }
      .filter-feedback { align-items: flex-start; flex-direction: column; }
      .refresh-status { text-align: left; }
      .metric-grid { grid-template-columns: 1fr; }
      .compare-chart-grid { grid-template-columns: 1fr; }
      .analysis-grid { grid-template-columns: 1fr; }
      .action-signal-grid { grid-template-columns: 1fr; }
      .action-signal-group + .action-signal-group {
        border-top: 1px solid #e9dcc8;
        border-left: 0;
      }
      .action-signal-item { align-items: start; }
      .action-signal-product { display: grid; gap: 2px; }
      .chart { height: 340px; }
      .section-head { display: block; }
    }
  </style>
</head>
<body>
  <nav class="topbar">
    <div class="topbar-inner">
      <div class="brand"><span class="mark">SKT</span><span>新加坡</span></div>
      <div class="nav">
        <a href="#overview">概览</a>
        <a href="#period">周期</a>
        <a href="#narrative">复盘</a>
        <a href="#trend">趋势</a>
        <a href="#category-overview">品类</a>
        <a href="#product-drilldown">产品</a>
        <a href="#field-contract">口径</a>
      </div>
    </div>
  </nav>

  <main class="page">
    <div class="shell">
    <section class="hero" id="overview">
      <div class="hero-copy">
        <h1>SKT-新加坡</h1>
      </div>
    </section>

    <section class="filters" id="period">
      <div class="filter-grid">
        <div class="filter-group">
          <label for="periodFilter">周期模板</label>
          <select id="periodFilter">
            <option value="MTD">本月 vs 上月同期</option>
            <option value="7">近 7 天 vs 前 7 天</option>
            <option value="14">近 14 天 vs 前 14 天</option>
            <option value="30">近 30 天 vs 前 30 天</option>
            <option value="CUSTOM">自定义周期</option>
          </select>
        </div>
        <div class="period-card" aria-label="当前周期">
          <span class="period-label">当前周期</span>
          <input type="date" id="startDateFilter" />
          <input type="date" id="endDateFilter" />
        </div>
        <div class="period-card" aria-label="对比周期">
          <span class="period-label">对比周期</span>
          <input type="date" id="compareStartDateFilter" />
          <input type="date" id="compareEndDateFilter" />
        </div>
        <div class="filter-group">
          <label for="categoryFilter">品类聚焦</label>
          <select id="categoryFilter">
            <option value="ALL">全部品类</option>
          </select>
        </div>
        <div class="filter-actions">
          <button class="button secondary" id="resetFilters" type="button">重置</button>
          <button class="button secondary" id="downloadTrend" type="button">下载主图</button>
          <button class="button refresh-button" id="refreshReport" type="button">
            <span class="refresh-icon" aria-hidden="true">↻</span>
            <span id="refreshButtonLabel">刷新数据</span>
          </button>
        </div>
      </div>
      <div class="filter-feedback">
        <p class="section-note" id="periodSummary">默认当前周期为本月 1 日到昨天，对比上月同日段。</p>
        <p class="refresh-status" id="refreshStatus" role="status" aria-live="polite" hidden></p>
      </div>
    </section>

    <section class="metric-shell" id="summary">
      <div class="metric-grid" id="metricGrid"></div>
    </section>

    <nav class="side-nav" id="sideNav" aria-label="板块导航">
      <div class="side-nav-head">
        <strong>板块导航</strong>
        <button class="side-nav-toggle" id="sideNavToggle" type="button" aria-controls="sideNavLinks" aria-expanded="true" title="收起导航" aria-label="收起导航">
          <span class="side-nav-icon">‹</span>
        </button>
      </div>
      <div class="side-nav-links" id="sideNavLinks">
        <a href="#summary">核心指标</a>
        <a href="#period">周期对比</a>
        <a href="#narrative">经营复盘</a>
        <a href="#trend">GMV 趋势</a>
        <a href="#platform-split">渠道与漏斗</a>
        <a href="#category-overview">品类结构</a>
        <a href="#visitor-conversion">访客转化</a>
        <a href="#category-detail">品类明细</a>
        <a href="#product-drilldown">产品明细</a>
        <a href="#offsite-product-detail">站外产品</a>
        <a href="skt-material-analysis.html">素材分析</a>
        <a href="#daily-detail">日明细</a>
        <a href="#field-contract">字段口径</a>
      </div>
    </nav>

    <section class="panel section" id="narrative">
      <div class="analysis-grid" id="narrativeGrid"></div>
    </section>

    <section class="section" id="trend">
      <div class="section-head">
        <div>
          <h2>商品点击率 / 转化率 / 品类GMV / 商品访客 曲线</h2>
          <p class="section-note">选择品类后，商品GMV、访客、点击率与转化率来自「站内产品数据-skt」；站内花费与站外花费RMB随品类归因筛选，SP GMV 保留为全站参考线。</p>
        </div>
      </div>
      <div class="panel">
        <div class="chart" id="trendChart"></div>
      </div>
    </section>

    <section class="section" id="period-comparison">
      <div class="section-head">
        <div>
          <h2>核心指标 vs 上期</h2>
          <p class="section-note">实线为当前周期，虚线为对比周期；六张图均随日期、对比周期和品类筛选联动，SP GMV 始终保留全站口径。</p>
        </div>
      </div>
      <div class="compare-chart-grid">
        <div class="compare-chart-card">
          <h3 class="compare-chart-title">产品点击 vs 上期</h3>
          <div class="chart compare-chart" id="clicksCompareChart"></div>
        </div>
        <div class="compare-chart-card">
          <h3 class="compare-chart-title">点击率 vs 上期</h3>
          <div class="chart compare-chart" id="ctrCompareChart"></div>
        </div>
        <div class="compare-chart-card">
          <h3 class="compare-chart-title">转化率 vs 上期</h3>
          <div class="chart compare-chart" id="conversionCompareChart"></div>
        </div>
        <div class="compare-chart-card">
          <h3 class="compare-chart-title">SP GMV vs 上期</h3>
          <div class="chart compare-chart" id="spGmvCompareChart"></div>
        </div>
        <div class="compare-chart-card">
          <h3 class="compare-chart-title">站内花费 vs 上期</h3>
          <div class="chart compare-chart" id="onsiteSpendCompareChart"></div>
        </div>
        <div class="compare-chart-card">
          <h3 class="compare-chart-title">站外花费 vs 上期</h3>
          <div class="chart compare-chart" id="offsiteSpendCompareChart"></div>
        </div>
      </div>
    </section>

    <section class="section" id="platform-split">
      <div class="grid-2">
        <div>
          <div class="section-head">
            <div>
              <h2>SP / TT 平台 GMV 拆分</h2>
              <p class="section-note">用于判断平台盘子变化和渠道贡献重心。</p>
            </div>
          </div>
          <div class="panel">
            <div class="chart small" id="platformSplitChart"></div>
          </div>
        </div>
        <div>
          <div class="section-head">
            <div>
              <h2>站内外承接漏斗</h2>
              <p class="section-note">用标准化指数并排看站外点击、站内访问、加购和支付件数。</p>
            </div>
          </div>
          <div class="panel">
            <div class="chart small" id="funnelChart"></div>
          </div>
        </div>
      </div>
    </section>

    <section class="section" id="category-overview">
      <div class="grid-2">
        <div>
          <div class="section-head">
            <div>
              <h2>品类销售与销量结构</h2>
              <p class="section-note">合并站内商品销售额、SP/TT 销量补充与品类表映射，随日期筛选刷新。</p>
            </div>
          </div>
          <div class="panel">
            <div class="chart small" id="categoryChart"></div>
          </div>
        </div>
        <div>
          <div class="section-head">
            <div>
              <h2>经营判断</h2>
              <p class="section-note">基于当前字段可支撑的业务诊断。</p>
            </div>
          </div>
          <div class="panel">
            <ul class="insight-list" id="insightList"></ul>
          </div>
        </div>
      </div>
    </section>

    <section class="section" id="visitor-conversion">
      <div class="section-head">
        <div>
          <h2>品类站内访客与转化率</h2>
          <p class="section-note">横轴为站内商品访客，纵轴为支付件转化率，气泡大小代表商品销售额 RMB，用于判断流量规模和承接效率是否匹配。</p>
        </div>
      </div>
      <div class="grid-even">
        <div class="panel">
          <div class="chart medium" id="categoryMediaChart"></div>
        </div>
        <div class="panel">
          <div class="chart medium" id="unitSplitChart"></div>
        </div>
      </div>
    </section>

    <section class="section" id="category-detail">
      <div class="section-head">
        <div>
          <h2>品类经营明细</h2>
          <p class="section-note">用于判断哪些品类是规模盘、投放盘、效率盘或承接短板。</p>
        </div>
      </div>
      <div class="table-wrap" id="categoryTable"></div>
    </section>

    <section class="section" id="product-drilldown">
      <div class="section-head">
        <div>
          <h2>产品经营明细</h2>
          <p class="section-note">按「站内产品数据-skt」拆到单品并保留归因品类；字段随日期与品类筛选刷新，数值下方为当前周期对比上周期。</p>
        </div>
      </div>
      <div class="table-wrap" id="productTable"></div>
    </section>

    <section class="section" id="offsite-product-detail">
      <div class="section-head">
        <div>
          <h2>站外产品投放明细</h2>
          <p class="section-note">按产品汇总展示 SP 商品GMV、GMV占比、人民币花费、站外GMV、ROAS，以及展示、点击、加购、转化；GMV占比为商品GMV占当前筛选下全部商品GMV，同样支持周期环比。</p>
        </div>
      </div>
      <div class="table-wrap" id="offsiteProductTable"></div>
      <div class="action-signal-panel" id="offsiteActionSignals" aria-live="polite"></div>
    </section>

    <section class="section" id="daily-detail">
      <div class="section-head">
        <div>
          <h2>日明细</h2>
          <p class="section-note">展示当前周期最近 20 天，用于快速核对数据。</p>
        </div>
      </div>
      <div class="table-wrap" id="dailyTable"></div>
    </section>

    <section class="section" id="field-contract">
      <div class="section-head">
        <div>
          <h2>字段取数说明</h2>
          <p class="section-note">字段契约与口径说明，后续如果接自动刷新或发布页，优先改这里对应的 pipeline 映射。</p>
        </div>
      </div>
      <div class="table-wrap" id="fieldTable"></div>
    </section>
    </div>
  </main>

  <script>
    const DATA = __PAYLOAD__;
    const charts = {};
    const fmt0 = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
    const fmt1 = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 });
    const fmt2 = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });

    function byId(id) { return document.getElementById(id); }
    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[char]));
    }
    function n(value) { return Number(value || 0); }
    function money(value) { return '¥' + fmt0.format(n(value)); }
    function compact(value) {
      const num = n(value);
      if (Math.abs(num) >= 100000000) return (num / 100000000).toFixed(2) + '亿';
      if (Math.abs(num) >= 10000) return (num / 10000).toFixed(1) + '万';
      return fmt0.format(num);
    }
    function ratio(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
      return (Number(value) * 100).toFixed(1) + '%';
    }
    function roas(value) {
      if (value === null || value === undefined || !Number.isFinite(Number(value))) return '-';
      return Number(value).toFixed(2);
    }
    function money1(value) {
      return '¥' + fmt1.format(n(value));
    }
    function compactMoney(value) {
      const num = n(value);
      if (Math.abs(num) >= 100000000) return '¥' + (num / 100000000).toFixed(2) + '亿';
      if (Math.abs(num) >= 10000) return '¥' + (num / 10000).toFixed(1) + '万';
      return '¥' + fmt1.format(num);
    }
    function number1(value) {
      return fmt1.format(n(value));
    }
    function chartValue(value) {
      if (Array.isArray(value)) return number1(value[value.length - 1]);
      return number1(value);
    }
    function axisTooltip(params) {
      const first = Array.isArray(params) ? params[0] : params;
      const lines = [first?.axisValueLabel || first?.axisValue || ''];
      (Array.isArray(params) ? params : [params]).forEach(param => {
        lines.push(`${param.marker}${param.seriesName}：${chartValue(param.value)}`);
      });
      return lines.join('<br/>');
    }
    function parseDay(value) {
      return value ? new Date(value + 'T00:00:00') : null;
    }
    function formatDay(day) {
      if (!day) return '';
      const year = day.getFullYear();
      const month = String(day.getMonth() + 1).padStart(2, '0');
      const date = String(day.getDate()).padStart(2, '0');
      return `${year}-${month}-${date}`;
    }
    function dataDateBounds() {
      const rows = DATA.daily_rows || [];
      return {
        start: rows[0]?.date || DATA.summary.date_start,
        end: rows[rows.length - 1]?.date || DATA.summary.date_end,
      };
    }
    function addDays(value, offset) {
      const day = parseDay(value);
      if (!day) return '';
      day.setDate(day.getDate() + offset);
      return formatDay(day);
    }
    function maxIso(...values) {
      return values.filter(Boolean).sort().slice(-1)[0] || '';
    }
    function minIso(...values) {
      return values.filter(Boolean).sort()[0] || '';
    }
    function clampIso(value, min, max) {
      if (!value) return '';
      let output = value;
      if (min && output < min) output = min;
      if (max && output > max) output = max;
      return output;
    }
    function daysBetween(start, end) {
      const startDay = parseDay(start);
      const endDay = parseDay(end);
      if (!startDay || !endDay || start > end) return 0;
      return Math.round((endDay - startDay) / 86400000) + 1;
    }
    function monthStart(value) {
      const day = parseDay(value);
      return day ? formatDay(new Date(day.getFullYear(), day.getMonth(), 1)) : '';
    }
    function sameDayPreviousMonth(value) {
      const day = parseDay(value);
      if (!day) return '';
      const lastPreviousMonthDay = new Date(day.getFullYear(), day.getMonth(), 0).getDate();
      const compareDay = Math.min(day.getDate(), lastPreviousMonthDay);
      return formatDay(new Date(day.getFullYear(), day.getMonth() - 1, compareDay));
    }
    function currentAnchorDate() {
      const bounds = dataDateBounds();
      const reportDate = DATA.summary.report_date || bounds.end;
      const yesterday = addDays(reportDate, -1);
      return clampIso(minIso(yesterday, bounds.end), bounds.start, bounds.end);
    }
    function applyPreset(period) {
      if (period === 'CUSTOM') return;
      const bounds = dataDateBounds();
      const anchor = currentAnchorDate();
      if (!anchor) return;
      byId('endDateFilter').value = anchor;
      if (period === 'MTD') {
        const currentStart = maxIso(monthStart(anchor), bounds.start);
        const compareEnd = sameDayPreviousMonth(anchor);
        const compareStart = maxIso(monthStart(compareEnd), bounds.start);
        byId('startDateFilter').value = currentStart;
        byId('compareStartDateFilter').value = compareStart;
        byId('compareEndDateFilter').value = clampIso(compareEnd, bounds.start, bounds.end);
        return;
      }
      const days = Number(period);
      const start = Number.isFinite(days) ? addDays(anchor, -days + 1) : bounds.start;
      const currentStart = maxIso(start, bounds.start);
      const compareEnd = addDays(currentStart, -1);
      const compareStart = addDays(compareEnd, -days + 1);
      byId('startDateFilter').value = currentStart;
      byId('compareStartDateFilter').value = clampIso(compareStart, bounds.start, bounds.end);
      byId('compareEndDateFilter').value = clampIso(compareEnd, bounds.start, bounds.end);
    }
    function selectedPeriod() {
      const start = byId('startDateFilter').value;
      const end = byId('endDateFilter').value;
      const compareStart = byId('compareStartDateFilter').value;
      const compareEnd = byId('compareEndDateFilter').value;
      const mode = byId('periodFilter')?.value || 'MTD';
      if (!start || !end || !compareStart || !compareEnd || start > end || compareStart > compareEnd) {
        return {
          mode,
          days: 0,
          invalid: true,
          current: { start: start || '', end: end || '' },
          compare: { start: compareStart || '', end: compareEnd || '' },
        };
      }
      const days = daysBetween(start, end);
      return {
        mode,
        days,
        invalid: false,
        current: { start, end },
        compare: { start: compareStart, end: compareEnd },
      };
    }
    function updatePeriodSummary(period) {
      const modeText = byId('periodFilter')?.selectedOptions?.[0]?.textContent || '自定义周期';
      const currentText = period?.current ? `${period.current.start || '-'} ~ ${period.current.end || '-'}` : '-';
      const compareText = period?.compare ? `${period.compare.start || '-'} ~ ${period.compare.end || '-'}` : '-';
      const dayText = period?.invalid ? '周期未完整' : `${period.days} 天`;
      const summary = `${modeText}：当前 ${currentText}（${dayText}） / 对比 ${compareText}`;
      if (byId('periodSummary')) byId('periodSummary').textContent = summary;
      if (byId('heroPeriod')) byId('heroPeriod').textContent = modeText;
      if (byId('heroFreshness')) byId('heroFreshness').textContent = period?.current?.end ? `截至 ${period.current.end}` : '截至昨天';
      if (byId('periodBadge')) byId('periodBadge').textContent = modeText;
    }
    function rowInPeriod(row, period) {
      if (!row || !row.date || !period || !period.start || !period.end) return false;
      return row.date >= period.start && row.date <= period.end;
    }
    function rowsInPeriod(rows, period) {
      return (rows || []).filter(row => rowInPeriod(row, period));
    }
    function selectedRows(period) {
      return period?.invalid ? [] : rowsInPeriod(DATA.daily_rows || [], period.current);
    }
    function previousRows(period) {
      return period?.invalid ? [] : rowsInPeriod(DATA.daily_rows || [], period.compare);
    }
    function selectedCategoryDailyRows(period, rangeName = 'current') {
      if (period?.invalid) return [];
      const category = byId('categoryFilter')?.value || 'ALL';
      const range = period?.[rangeName] || period?.current;
      return (DATA.category_daily_rows || []).filter(row => {
        if (!rowInPeriod(row, range)) return false;
        if (category !== 'ALL' && row.category !== category) return false;
        return true;
      });
    }
    function sum(rows, key) {
      return rows.reduce((total, row) => total + n(row[key]), 0);
    }
    function totals(rows) {
      const total = {
        platform_gmv_rmb: sum(rows, 'platform_gmv_rmb'),
        sp_gmv_rmb: sum(rows, 'sp_gmv_rmb'),
        tt_gmv_rmb: sum(rows, 'tt_gmv_rmb'),
        platform_orders: sum(rows, 'platform_orders'),
        offsite_spend: sum(rows, 'offsite_spend'),
        offsite_purchase_value: sum(rows, 'offsite_purchase_value'),
        offsite_spend_rmb: sum(rows, 'offsite_spend_rmb'),
        offsite_purchase_value_rmb: sum(rows, 'offsite_purchase_value_rmb'),
        onsite_spend_rmb: sum(rows, 'onsite_spend_rmb'),
        onsite_ad_gmv_rmb: sum(rows, 'onsite_ad_gmv_rmb'),
        onsite_impressions: sum(rows, 'onsite_impressions'),
        offsite_impressions: sum(rows, 'offsite_impressions'),
        offsite_clicks: sum(rows, 'offsite_clicks'),
        product_paid_sales_rmb: sum(rows, 'product_paid_sales_rmb'),
        product_visitors: sum(rows, 'product_visitors'),
        product_add_to_cart_visitors: sum(rows, 'product_add_to_cart_visitors'),
        product_paid_units: sum(rows, 'product_paid_units'),
        product_clicks: sum(rows, 'product_clicks'),
        product_impressions: sum(rows, 'product_impressions'),
      };
      total.sp_share = total.platform_gmv_rmb ? total.sp_gmv_rmb / total.platform_gmv_rmb : null;
      total.tt_share = total.platform_gmv_rmb ? total.tt_gmv_rmb / total.platform_gmv_rmb : null;
      total.offsite_roas = total.offsite_spend_rmb ? total.offsite_purchase_value_rmb / total.offsite_spend_rmb : null;
      total.onsite_roas = total.onsite_spend_rmb ? total.onsite_ad_gmv_rmb / total.onsite_spend_rmb : null;
      total.media_spend_rmb = total.onsite_spend_rmb + total.offsite_spend_rmb;
      total.media_sales_rmb = total.onsite_ad_gmv_rmb + total.offsite_purchase_value_rmb;
      total.media_roas = total.media_spend_rmb ? total.media_sales_rmb / total.media_spend_rmb : null;
      total.media_spend_ratio = total.platform_gmv_rmb ? total.media_spend_rmb / total.platform_gmv_rmb : null;
      total.add_to_cart_rate = total.product_visitors ? total.product_add_to_cart_visitors / total.product_visitors : null;
      total.unit_conversion_rate = total.product_visitors ? total.product_paid_units / total.product_visitors : null;
      total.product_ctr = total.product_impressions ? total.product_clicks / total.product_impressions : null;
      total.product_aov = total.product_paid_units ? total.product_paid_sales_rmb / total.product_paid_units : null;
      total.onsite_spend_ratio = total.platform_gmv_rmb ? total.onsite_spend_rmb / total.platform_gmv_rmb : null;
      total.offsite_spend_ratio = total.platform_gmv_rmb ? total.offsite_spend_rmb / total.platform_gmv_rmb : null;
      return total;
    }
    function initChart(id) {
      if (!charts[id]) charts[id] = echarts.init(byId(id), null, { renderer: 'canvas' });
      return charts[id];
    }
    function deltaText(current, previous) {
      const currentValue = n(current);
      const previousValue = n(previous);
      if (!previousValue && !currentValue) return '持平';
      if (!previousValue) return '上期为0';
      const change = (currentValue - previousValue) / Math.abs(previousValue);
      return `${change >= 0 ? '+' : ''}${(change * 100).toFixed(1)}%`;
    }
    function deltaClass(current, previous, options = {}) {
      if (options.neutral || !n(previous)) return '';
      const diff = n(current) - n(previous);
      if (!diff) return '';
      const isGood = options.inverse ? diff < 0 : diff > 0;
      return isGood ? 'good' : 'bad';
    }
    function compareHtml(current, previous, formatter, options = {}) {
      return `上期 ${formatter(previous)} <span class="delta ${deltaClass(current, previous, options)}">对比上期 ${deltaText(current, previous)}</span>`;
    }
    function metricDeltaClass(current, previous) {
      if (!n(previous)) return '';
      const diff = n(current) - n(previous);
      if (!diff) return '';
      return diff > 0 ? 'up' : 'down';
    }
    function metricDeltaHtml(current, previous) {
      return `<div class="metric-delta ${metricDeltaClass(current, previous)}">环比 ${deltaText(current, previous)}</div>`;
    }
    function metricSubHtml(label, current, previous, formatter) {
      return `<div class="metric-sub">${label} ${formatter(current)} <span class="metric-sub-delta ${metricDeltaClass(current, previous)}">| 环比 ${deltaText(current, previous)}</span></div>`;
    }
    function tableMetricHtml(current, previous, formatter, options = {}) {
      const compareClass = metricDeltaClass(current, previous);
      return `<div class="table-primary">${formatter(current)}</div><div class="table-compare ${compareClass}">环比 ${deltaText(current, previous)}</div>`;
    }
    function dateSeries(start, end) {
      const days = [];
      if (!start || !end || start > end) return days;
      let cursor = start;
      while (cursor <= end) {
        days.push(cursor);
        cursor = addDays(cursor, 1);
      }
      return days;
    }
    function alignPeriodRows(currentRows, compareRows, period) {
      const currentMap = new Map((currentRows || []).map(row => [row.date, row]));
      const compareMap = new Map((compareRows || []).map(row => [row.date, row]));
      const currentDates = dateSeries(period.current.start, period.current.end);
      const compareDates = dateSeries(period.compare.start, period.compare.end);
      return currentDates.map((currentDate, index) => {
        const compareDate = compareDates[index] || '';
        return {
          index: index + 1,
          currentDate,
          compareDate,
          current: currentMap.get(currentDate) || { date: currentDate },
          compare: compareMap.get(compareDate) || { date: compareDate },
        };
      });
    }
    function renderMetrics(rows, compareRows) {
      const t = totals(rows);
      const p = totals(compareRows);
      const cards = [
        {
          label: '全站GMV（SP）',
          value: money1(t.sp_gmv_rmb),
          current: t.sp_gmv_rmb,
          previous: p.sp_gmv_rmb,
          subs: [
            ['SP占平台', t.sp_share, p.sp_share, ratio],
            ['TT GMV', t.tt_gmv_rmb, p.tt_gmv_rmb, compactMoney],
          ],
        },
        {
          label: '站内销量',
          value: fmt0.format(t.product_paid_units),
          current: t.product_paid_units,
          previous: p.product_paid_units,
          subs: [
            ['AOV', t.product_aov, p.product_aov, compactMoney],
            ['订单', t.platform_orders, p.platform_orders, value => fmt0.format(n(value))],
          ],
        },
        {
          label: '访客',
          value: fmt0.format(t.product_visitors),
          current: t.product_visitors,
          previous: p.product_visitors,
          subs: [
            ['站外点击', t.offsite_clicks, p.offsite_clicks, value => fmt0.format(n(value))],
            ['加购率', t.add_to_cart_rate, p.add_to_cart_rate, ratio],
          ],
        },
        {
          label: 'CTR',
          value: ratio(t.product_ctr),
          current: t.product_ctr,
          previous: p.product_ctr,
          subs: [
            ['转化率', t.unit_conversion_rate, p.unit_conversion_rate, ratio],
            ['产品点击', t.product_clicks, p.product_clicks, value => fmt0.format(n(value))],
          ],
        },
        {
          label: '站内花费',
          value: money1(t.onsite_spend_rmb),
          current: t.onsite_spend_rmb,
          previous: p.onsite_spend_rmb,
          subs: [
            ['总花费占比', t.onsite_spend_ratio, p.onsite_spend_ratio, ratio],
            ['站内ROAS', t.onsite_roas, p.onsite_roas, roas],
          ],
        },
        {
          label: '站外花费RMB',
          value: money1(t.offsite_spend_rmb),
          current: t.offsite_spend_rmb,
          previous: p.offsite_spend_rmb,
          subs: [
            ['曝光', t.offsite_impressions, p.offsite_impressions, value => fmt0.format(n(value))],
            ['站外ROAS', t.offsite_roas, p.offsite_roas, roas],
          ],
        },
      ];
      byId('metricGrid').innerHTML = cards.map(card => `
        <article class="metric">
          <div class="label">${card.label}</div>
          <div class="value">${card.value}</div>
          ${metricDeltaHtml(card.current, card.previous)}
          <div class="metric-divider"></div>
          ${card.subs.map(([label, current, previous, formatter]) => metricSubHtml(label, current, previous, formatter)).join('')}
        </article>
      `).join('');
    }
    const TREND_SCOPED_FIELDS = [
      'product_paid_sales_rmb',
      'product_paid_sales_sgd',
      'product_paid_units',
      'product_visitors',
      'product_page_views',
      'product_add_to_cart_visitors',
      'product_clicks',
      'product_impressions',
      'onsite_spend_rmb',
      'onsite_ad_gmv_rmb',
      'onsite_impressions',
      'onsite_clicks',
      'onsite_conversions',
      'onsite_items_sold',
      'offsite_spend',
      'offsite_spend_rmb',
      'offsite_purchase_value',
      'offsite_purchase_value_rmb',
      'offsite_impressions',
      'offsite_clicks',
      'offsite_conversions',
      'offsite_add_to_cart',
    ];
    function aggregateTrendDaily(rows) {
      const byDate = new Map();
      rows.forEach(row => {
        if (!row.date) return;
        if (!byDate.has(row.date)) {
          const empty = { date: row.date };
          TREND_SCOPED_FIELDS.forEach(field => { empty[field] = 0; });
          byDate.set(row.date, empty);
        }
        const target = byDate.get(row.date);
        TREND_SCOPED_FIELDS.forEach(field => { target[field] += n(row[field]); });
      });
      return byDate;
    }
    function selectedTrendRows(period, rangeName = 'current') {
      const baseRows = rangeName === 'compare' ? previousRows(period) : selectedRows(period);
      const scopedByDate = aggregateTrendDaily(selectedCategoryDailyRows(period, rangeName));
      return baseRows.map(row => {
        const scoped = scopedByDate.get(row.date) || {};
        const output = { ...row };
        TREND_SCOPED_FIELDS.forEach(field => { output[field] = n(scoped[field]); });
        return output;
      });
    }
    function renderTrend(rows, compareRows, period) {
      const chart = initChart('trendChart');
      const showLabels = rows.length <= 14;
      const rate = (top, bottom) => n(bottom) ? n(top) / n(bottom) : null;
      const pointLabel = formatter => ({
        show: showLabels,
        position: 'top',
        color: '#667b73',
        fontSize: 11,
        formatter: params => formatter(params.value),
      });
      const tooltipValue = (name, value) => {
        if (name.includes('点击率') || name.includes('转化率')) return ratio(value);
        if (name.includes('访客')) return fmt0.format(n(value));
        return money(value);
      };
      chart.setOption({
        color: ['#13736b', '#b56d3b', '#2f6fed', '#138a86', '#d94f8d', '#7c3cff', '#f07d00'],
        tooltip: {
          trigger: 'axis',
          formatter: params => {
            const first = Array.isArray(params) ? params[0] : params;
            const dateText = rows[first?.dataIndex || 0]?.date || first?.axisValue || '';
            const lines = [dateText];
            (Array.isArray(params) ? params : [params]).forEach(param => {
              lines.push(`${param.marker}${param.seriesName}：${tooltipValue(param.seriesName, param.value)}`);
            });
            return lines.join('<br/>');
          },
        },
        toolbox: {
          right: 8,
          top: 4,
          feature: { saveAsImage: { title: '下载', pixelRatio: 2, backgroundColor: '#ffffff' } },
        },
        legend: { top: 0, itemGap: 14, textStyle: { color: '#667b73', fontSize: 12 } },
        grid: { left: 70, right: 82, top: 58, bottom: 42 },
        xAxis: {
          type: 'category',
          data: rows.map(row => row.date.slice(5)),
          axisLabel: { hideOverlap: true, color: '#667b73' },
          axisLine: { lineStyle: { color: '#9aa8a2' } },
          axisTick: { show: false },
        },
        yAxis: [
          {
            type: 'value',
            name: '商品效率',
            axisLabel: { formatter: value => ratio(value), color: '#667b73' },
            splitLine: { lineStyle: { color: '#d9e5df' } },
          },
          {
            type: 'value',
            name: 'RMB / 人数',
            axisLabel: { formatter: value => compact(value), color: '#667b73' },
            splitLine: { show: false },
          },
        ],
        series: [
          {
            name: '商品点击率',
            type: 'line',
            smooth: true,
            yAxisIndex: 0,
            symbolSize: 6,
            lineStyle: { width: 2 },
            label: pointLabel(ratio),
            data: rows.map(row => rate(row.product_clicks, row.product_impressions)),
          },
          {
            name: '支付件转化率',
            type: 'line',
            smooth: true,
            yAxisIndex: 0,
            symbolSize: 6,
            lineStyle: { width: 2 },
            label: pointLabel(ratio),
            data: rows.map(row => rate(row.product_paid_units, row.product_visitors)),
          },
          {
            name: '站外花费RMB',
            type: 'line',
            smooth: true,
            yAxisIndex: 1,
            symbolSize: 5,
            lineStyle: { width: 2 },
            label: pointLabel(value => money(value)),
            data: rows.map(row => n(row.offsite_spend_rmb)),
          },
          {
            name: '站内花费',
            type: 'line',
            smooth: true,
            yAxisIndex: 1,
            symbolSize: 5,
            lineStyle: { width: 2 },
            label: pointLabel(value => money(value)),
            data: rows.map(row => n(row.onsite_spend_rmb)),
          },
          {
            name: '商品GMV',
            type: 'line',
            smooth: true,
            yAxisIndex: 1,
            symbolSize: 5,
            lineStyle: { width: 2.5 },
            label: pointLabel(value => money(value)),
            data: rows.map(row => n(row.product_paid_sales_rmb)),
          },
          {
            name: '商品访客数',
            type: 'line',
            smooth: true,
            yAxisIndex: 1,
            symbolSize: 5,
            lineStyle: { width: 2 },
            label: pointLabel(value => fmt0.format(n(value))),
            data: rows.map(row => n(row.product_visitors)),
          },
          {
            name: 'SP GMV',
            type: 'line',
            smooth: true,
            yAxisIndex: 1,
            symbolSize: 5,
            lineStyle: { width: 2.5 },
            label: pointLabel(value => money(value)),
            data: rows.map(row => n(row.sp_gmv_rmb)),
          },
        ],
      }, true);
    }
    function renderPeriodComparisonCharts(currentRows, compareRows, period) {
      const alignedRows = alignPeriodRows(currentRows, compareRows, period);
      const compareRange = period?.compare?.start && period?.compare?.end
        ? `${period.compare.start.slice(5)}-${period.compare.end.slice(5)}`
        : '上期';
      const dailyRate = (row, numerator, denominator) => {
        const base = n(row?.[denominator]);
        return base ? n(row?.[numerator]) / base : null;
      };
      const configs = [
        {
          id: 'clicksCompareChart',
          name: '产品点击',
          value: row => n(row?.product_clicks),
          formatValue: value => fmt0.format(n(value)),
          formatAxis: value => compact(value),
        },
        {
          id: 'ctrCompareChart',
          name: '产品点击率',
          value: row => dailyRate(row, 'product_clicks', 'product_impressions'),
          formatValue: ratio,
          formatAxis: ratio,
        },
        {
          id: 'conversionCompareChart',
          name: '支付件转化率',
          value: row => dailyRate(row, 'product_paid_units', 'product_visitors'),
          formatValue: ratio,
          formatAxis: ratio,
        },
        {
          id: 'spGmvCompareChart',
          name: '全站GMV（SP）',
          value: row => n(row?.sp_gmv_rmb),
          formatValue: money,
          formatAxis: compact,
        },
        {
          id: 'onsiteSpendCompareChart',
          name: '站内花费',
          value: row => n(row?.onsite_spend_rmb),
          formatValue: money,
          formatAxis: compact,
        },
        {
          id: 'offsiteSpendCompareChart',
          name: '站外花费RMB',
          value: row => n(row?.offsite_spend_rmb),
          formatValue: money,
          formatAxis: compact,
        },
      ];
      configs.forEach(config => {
        initChart(config.id).setOption({
          color: ['#2563eb', '#93c5fd'],
          tooltip: {
            trigger: 'axis',
            formatter: params => {
              const items = Array.isArray(params) ? params : [params];
              const row = alignedRows[items[0]?.dataIndex || 0] || {};
              const lines = [row.currentDate || ''];
              items.forEach((item, index) => {
                const sourceDate = index === 0 ? row.currentDate : row.compareDate;
                lines.push(`${item.marker}${item.seriesName}：${config.formatValue(item.value)}${sourceDate ? `（${sourceDate}）` : ''}`);
              });
              return lines.join('<br/>');
            },
          },
          legend: { top: 0, left: 'center', itemGap: 14, textStyle: { color: '#667b73', fontSize: 11 } },
          toolbox: {
            right: 0,
            top: 0,
            feature: { saveAsImage: { title: '下载', pixelRatio: 2, backgroundColor: '#ffffff' } },
          },
          grid: { left: 54, right: 18, top: 54, bottom: 34 },
          xAxis: {
            type: 'category',
            data: alignedRows.map(row => row.currentDate.slice(5)),
            axisLabel: { hideOverlap: true, color: '#667b73' },
            axisLine: { lineStyle: { color: '#9aa8a2' } },
            axisTick: { show: false },
          },
          yAxis: {
            type: 'value',
            axisLabel: { formatter: value => config.formatAxis(value), color: '#667b73' },
            splitLine: { lineStyle: { color: '#e2e9e6' } },
          },
          series: [
            {
              name: config.name,
              type: 'line',
              smooth: true,
              symbol: 'circle',
              symbolSize: 5,
              lineStyle: { width: 3 },
              areaStyle: { opacity: 0.08 },
              data: alignedRows.map(row => config.value(row.current)),
            },
            {
              name: `${config.name}（${compareRange}）`,
              type: 'line',
              smooth: true,
              symbol: 'circle',
              symbolSize: 5,
              lineStyle: { width: 2, type: 'dashed' },
              data: alignedRows.map(row => config.value(row.compare)),
            },
          ],
        }, true);
      });
    }
    function renderPlatformSplit(rows) {
      initChart('platformSplitChart').setOption({
        color: ['#2563eb', '#0f766e', '#111111'],
        tooltip: { trigger: 'axis', formatter: axisTooltip },
        legend: { top: 0 },
        grid: { left: 66, right: 20, top: 48, bottom: 40 },
        xAxis: { type: 'category', data: rows.map(row => row.date), axisLabel: { hideOverlap: true } },
        yAxis: { type: 'value', name: 'RMB', axisLabel: { formatter: value => compact(value) } },
        series: [
          { name: 'SP', type: 'line', smooth: true, symbolSize: 4, areaStyle: { opacity: 0.12 }, data: rows.map(row => n(row.sp_gmv_rmb)) },
          { name: 'TT', type: 'line', smooth: true, symbolSize: 4, areaStyle: { opacity: 0.12 }, data: rows.map(row => n(row.tt_gmv_rmb)) },
          { name: '平台GMV', type: 'line', smooth: true, symbolSize: 3, lineStyle: { type: 'dashed', width: 2 }, data: rows.map(row => n(row.platform_gmv_rmb)) },
        ],
      }, true);
    }
    function renderFunnel(rows) {
      const values = [
        ['站外点击', sum(rows, 'offsite_clicks')],
        ['站外转化', sum(rows, 'offsite_conversions')],
        ['商品访问', sum(rows, 'product_visitors')],
        ['商品加购', sum(rows, 'product_add_to_cart_visitors')],
        ['支付件数', sum(rows, 'product_paid_units')],
      ];
      const max = Math.max(...values.map(item => item[1]), 1);
      initChart('funnelChart').setOption({
        color: ['#2563eb'],
        tooltip: { formatter: params => `${params.name}<br/>实际值：${fmt0.format(values[params.dataIndex][1])}<br/>指数：${fmt1.format(params.value)}` },
        grid: { left: 72, right: 18, top: 28, bottom: 40 },
        xAxis: { type: 'category', data: values.map(item => item[0]) },
        yAxis: { type: 'value', name: '指数', max: 100 },
        series: [{ type: 'bar', barMaxWidth: 34, data: values.map(item => item[1] / max * 100) }],
      }, true);
    }
    function addCategoryDerived(row) {
      row.platform_units = n(row.sp_units) + n(row.tt_units);
      row.prior_platform_units = n(row.sp_prior_units) + n(row.tt_prior_units);
      row.unit_growth = row.prior_platform_units ? (row.platform_units - row.prior_platform_units) / row.prior_platform_units : null;
      row.sp_unit_share = row.platform_units ? n(row.sp_units) / row.platform_units : null;
      row.tt_unit_share = row.platform_units ? n(row.tt_units) / row.platform_units : null;
      row.onsite_roas = n(row.onsite_spend_rmb) ? n(row.onsite_ad_gmv_rmb) / n(row.onsite_spend_rmb) : null;
      row.offsite_roas = n(row.offsite_spend_rmb) ? n(row.offsite_purchase_value_rmb) / n(row.offsite_spend_rmb) : null;
      row.media_spend_rmb = n(row.onsite_spend_rmb) + n(row.offsite_spend_rmb);
      row.media_sales_rmb = n(row.onsite_ad_gmv_rmb) + n(row.offsite_purchase_value_rmb);
      row.media_roas = row.media_spend_rmb ? row.media_sales_rmb / row.media_spend_rmb : null;
      row.add_to_cart_rate = n(row.product_visitors) ? n(row.product_add_to_cart_visitors) / n(row.product_visitors) : null;
      row.unit_conversion_rate = n(row.product_visitors) ? n(row.product_paid_units) / n(row.product_visitors) : null;
      row.sales_per_visitor = n(row.product_visitors) ? n(row.product_paid_sales_rmb) / n(row.product_visitors) : null;
      row.media_spend_ratio = n(row.product_paid_sales_rmb) ? row.media_spend_rmb / n(row.product_paid_sales_rmb) : null;
      return row;
    }
    function aggregateCategoryRows(rows) {
      const byCategory = new Map();
      rows.forEach(row => {
        const category = row.category || '未归类';
        if (!byCategory.has(category)) byCategory.set(category, { category });
        const target = byCategory.get(category);
        Object.entries(row).forEach(([key, value]) => {
          if (key === 'date' || key === 'category') return;
          target[key] = n(target[key]) + n(value);
        });
      });
      const result = [...byCategory.values()].map(addCategoryDerived);
      const salesTotal = sum(result, 'product_paid_sales_rmb');
      const mediaTotal = sum(result, 'media_spend_rmb');
      const unitTotal = result.reduce((total, row) => total + n(row.platform_units), 0);
      result.forEach(row => {
        row.sales_share = salesTotal ? n(row.product_paid_sales_rmb) / salesTotal : null;
        row.media_share = mediaTotal ? n(row.media_spend_rmb) / mediaTotal : null;
        row.unit_share = unitTotal ? n(row.platform_units) / unitTotal : null;
      });
      result.sort((a, b) => n(b.product_paid_sales_rmb) - n(a.product_paid_sales_rmb));
      return result;
    }
    function selectedCategoryRows(period, rangeName = 'current') {
      return aggregateCategoryRows(selectedCategoryDailyRows(period, rangeName));
    }
    function categoryMatches(rowCategory, selectedCategory) {
      if (selectedCategory === 'ALL') return true;
      return String(rowCategory || '').split(/\\s*\\/\\s*/).includes(selectedCategory);
    }
    function selectedDateMatch(row, period) {
      return rowInPeriod(row, period.current);
    }
    function selectedProductRows(period, rangeName = 'current') {
      if (period?.invalid) return [];
      const category = byId('categoryFilter')?.value || 'ALL';
      const range = period?.[rangeName] || period?.current;
      const source = (DATA.product_daily_rows && DATA.product_daily_rows.length)
        ? DATA.product_daily_rows
        : (DATA.product_rows || []);
      const byProduct = new Map();
      source
        .filter(row => rowInPeriod(row, range) && categoryMatches(row.category, category))
        .forEach(row => {
          const key = `${row.category || '未归类'}||${row.product || '未命名单品'}`;
          if (!byProduct.has(key)) byProduct.set(key, {
            product: row.product || '未命名单品',
            category: row.category || '未归类',
            paid_sales_sgd: 0,
            paid_sales_rmb: 0,
            paid_units: 0,
            visitors: 0,
            page_views: 0,
            add_to_cart_visitors: 0,
            product_clicks: 0,
            product_impressions: 0,
          });
          const target = byProduct.get(key);
          ['paid_sales_sgd', 'paid_sales_rmb', 'paid_units', 'visitors', 'page_views', 'add_to_cart_visitors', 'product_clicks', 'product_impressions']
            .forEach(field => { target[field] += n(row[field]); });
        });
      const rows = [...byProduct.values()];
      const productSalesTotal = rows.reduce((total, row) => total + n(row.paid_sales_rmb), 0);
      rows.forEach(row => {
        row.sales_per_visitor = row.visitors ? row.paid_sales_rmb / row.visitors : null;
        row.unit_conversion_rate = row.visitors ? row.paid_units / row.visitors : null;
        row.add_to_cart_rate = row.visitors ? row.add_to_cart_visitors / row.visitors : null;
        row.ctr = row.product_impressions ? row.product_clicks / row.product_impressions : null;
        row.gmv_share = productSalesTotal ? row.paid_sales_rmb / productSalesTotal : null;
      });
      rows.sort((a, b) => n(b.paid_sales_rmb) - n(a.paid_sales_rmb));
      return rows;
    }
    function selectedOffsiteProductRows(period, rangeName = 'current') {
      if (period?.invalid) return [];
      const category = byId('categoryFilter')?.value || 'ALL';
      const range = period?.[rangeName] || period?.current;
      const source = (DATA.offsite_product_daily_rows && DATA.offsite_product_daily_rows.length)
        ? DATA.offsite_product_daily_rows
        : (DATA.offsite_product_rows || []);
      const byProduct = new Map();
      source
        .filter(row => rowInPeriod(row, range) && categoryMatches(row.category, category))
        .forEach(row => {
          const key = `${row.category || '未归类'}||${row.product || '未归类产品'}`;
          if (!byProduct.has(key)) byProduct.set(key, {
            product: row.product || '未归类产品',
            category: row.category || '未归类',
            spend: 0,
            spend_rmb: 0,
            purchase_value: 0,
            purchase_value_rmb: 0,
            impressions: 0,
            conversions: 0,
            clicks: 0,
            add_to_cart: 0,
            fx_weighted: 0,
            fx_weight: 0,
          });
          const target = byProduct.get(key);
          ['spend', 'spend_rmb', 'purchase_value', 'purchase_value_rmb', 'impressions', 'conversions', 'clicks', 'add_to_cart']
            .forEach(field => { target[field] += n(row[field]); });
          if (n(row.avg_fx) && n(row.spend)) {
            target.fx_weighted += n(row.avg_fx) * n(row.spend);
            target.fx_weight += n(row.spend);
          }
        });
      const rows = [...byProduct.values()];
      const offsiteSpendTotal = rows.reduce((total, row) => total + n(row.spend_rmb), 0);
      rows.forEach(row => {
        row.avg_fx = row.fx_weight ? row.fx_weighted / row.fx_weight : null;
        row.roas = row.spend_rmb ? row.purchase_value_rmb / row.spend_rmb : null;
        row.spend_share = offsiteSpendTotal ? row.spend_rmb / offsiteSpendTotal : null;
        delete row.fx_weighted;
        delete row.fx_weight;
      });
      rows.sort((a, b) => n(b.spend_rmb) - n(a.spend_rmb));
      return rows;
    }
    function normalizeProductKey(value) {
      return String(value || '').toLowerCase().replace(/[\\s\\-_/|()+（）\\[\\]【】,，.。:：]+/g, '');
    }
    function buildProductGmvLookup(productRows) {
      const lookup = { byCategoryName: new Map(), byName: new Map(), byCategory: new Map(), all: [] };
      (productRows || []).forEach(row => {
        const category = String(row.category || '-');
        const nameKey = normalizeProductKey(row.product);
        if (!nameKey) return;
        const entry = {
          category,
          product: row.product || '-',
          nameKey,
          gmv: n(row.paid_sales_rmb),
        };
        const categoryNameKey = `${category}||${nameKey}`;
        const currentCategoryName = lookup.byCategoryName.get(categoryNameKey);
        if (!currentCategoryName || entry.gmv > currentCategoryName.gmv) lookup.byCategoryName.set(categoryNameKey, entry);
        const currentName = lookup.byName.get(nameKey);
        if (!currentName || entry.gmv > currentName.gmv) lookup.byName.set(nameKey, entry);
        if (!lookup.byCategory.has(category)) lookup.byCategory.set(category, []);
        lookup.byCategory.get(category).push(entry);
        lookup.all.push(entry);
      });
      return lookup;
    }
    function productKeyOverlapScore(leftKey, rightKey) {
      if (!leftKey || !rightKey) return 0;
      const leftChars = [...new Set([...leftKey])];
      const rightChars = new Set([...rightKey]);
      const overlap = leftChars.filter(char => rightChars.has(char)).length;
      return overlap / Math.max(leftChars.length, 1);
    }
    function lookupSpProductGmv(row, lookup) {
      if (!row || !lookup) return 0;
      const category = String(row.category || '-');
      const nameKey = normalizeProductKey(row.product);
      if (!nameKey) return 0;
      const exact = lookup.byCategoryName.get(`${category}||${nameKey}`);
      if (exact) return exact.gmv;
      const byName = lookup.byName.get(nameKey);
      if (byName) return byName.gmv;
      const fuzzy = (lookup.byCategory.get(category) || [])
        .filter(item => item.nameKey.includes(nameKey) || nameKey.includes(item.nameKey))
        .sort((a, b) => b.gmv - a.gmv)[0];
      if (fuzzy) return fuzzy.gmv;
      const globalContains = (lookup.all || [])
        .filter(item => item.nameKey.includes(nameKey) || nameKey.includes(item.nameKey))
        .sort((a, b) => b.gmv - a.gmv)[0];
      if (globalContains) return globalContains.gmv;
      const overlap = (lookup.byCategory.get(category) || [])
        .map(item => ({ ...item, score: productKeyOverlapScore(nameKey, item.nameKey) }))
        .filter(item => item.score >= 0.45)
        .sort((a, b) => (b.score - a.score) || (b.gmv - a.gmv))[0];
      return overlap ? overlap.gmv : 0;
    }
    function offsiteProductKey(row) {
      return `${row?.category || '-'}||${row?.product || '-'}`;
    }
    function relativeChange(current, previous) {
      const base = n(previous);
      if (base <= 0) return null;
      return (n(current) - base) / Math.abs(base);
    }
    function signalChangeText(change) {
      if (change === null || change === undefined || !Number.isFinite(Number(change))) return '无有效基期';
      const value = Number(change) * 100;
      return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
    }
    function buildOffsiteActionRows(offsiteProductRows, compareOffsiteProductRows, productRows, compareProductRows) {
      const currentByProduct = new Map((offsiteProductRows || []).map(row => [offsiteProductKey(row), row]));
      const compareByProduct = new Map((compareOffsiteProductRows || []).map(row => [offsiteProductKey(row), row]));
      const productKeys = new Set([...currentByProduct.keys(), ...compareByProduct.keys()]);
      const currentSpGmvLookup = buildProductGmvLookup(productRows);
      const compareSpGmvLookup = buildProductGmvLookup(compareProductRows);
      return [...productKeys].map(key => {
        const current = currentByProduct.get(key) || {};
        const previous = compareByProduct.get(key) || {};
        const identity = currentByProduct.get(key) || compareByProduct.get(key) || {};
        const currentSpend = n(current.spend_rmb);
        const previousSpend = n(previous.spend_rmb);
        const currentSpGmv = lookupSpProductGmv(identity, currentSpGmvLookup);
        const previousSpGmv = lookupSpProductGmv(identity, compareSpGmvLookup);
        return {
          product: identity.product || '未归类产品',
          category: identity.category || '未归类',
          currentSpend,
          previousSpend,
          currentSpGmv,
          previousSpGmv,
          spendChange: relativeChange(currentSpend, previousSpend),
          spGmvChange: relativeChange(currentSpGmv, previousSpGmv),
          hasComparableBase: previousSpend > 0 && previousSpGmv > 0,
        };
      });
    }
    function renderCategoryChart(categoryRows) {
      const rows = categoryRows.slice(0, 12).reverse();
      initChart('categoryChart').setOption({
        color: ['#0f766e', '#b7791f', '#2563eb'],
        tooltip: { trigger: 'axis', formatter: axisTooltip },
        legend: { top: 0 },
        grid: { left: 90, right: 46, top: 48, bottom: 30 },
        xAxis: { type: 'value', axisLabel: { formatter: value => compact(value) } },
        yAxis: { type: 'category', data: rows.map(row => row.category) },
        series: [
          { name: '商品销售额RMB', type: 'bar', data: rows.map(row => n(row.product_paid_sales_rmb)) },
          { name: '媒体花费RMB', type: 'bar', data: rows.map(row => n(row.media_spend_rmb)) },
          { name: 'SP+TT销量', type: 'bar', data: rows.map(row => n(row.platform_units)) },
        ],
      }, true);
    }
    function renderCategoryMediaChart(categoryRows) {
      const rows = categoryRows
        .filter(row => n(row.product_visitors) || n(row.product_paid_units))
        .slice(0, 24);
      initChart('categoryMediaChart').setOption({
        color: ['#17594f'],
        tooltip: {
          formatter: params => {
            const row = params.data.raw;
            return `${row.category}<br/>站内访客：${fmt0.format(n(row.product_visitors))}<br/>支付件转化率：${ratio(row.unit_conversion_rate)}<br/>加购率：${ratio(row.add_to_cart_rate)}<br/>商品销售额：${money(row.product_paid_sales_rmb)}<br/>支付件数：${fmt0.format(n(row.product_paid_units))}`;
          },
        },
        grid: { left: 72, right: 30, top: 36, bottom: 58 },
        xAxis: { type: 'value', name: '站内访客', axisLabel: { formatter: value => compact(value) } },
        yAxis: { type: 'value', name: '支付件转化率', axisLabel: { formatter: value => ratio(value) } },
        series: [{
          name: '品类',
          type: 'scatter',
          symbolSize: value => Math.max(12, Math.min(54, Math.sqrt(value[2] || 0) / 32)),
          data: rows.map(row => ({
            name: row.category,
            value: [n(row.product_visitors), n(row.unit_conversion_rate), n(row.product_paid_sales_rmb)],
            raw: row,
          })),
          label: { show: true, formatter: params => params.name, position: 'right' },
          encode: { x: 0, y: 1 },
        }],
      }, true);
    }
    function renderUnitSplitChart(categoryRows) {
      const rows = categoryRows.filter(row => n(row.platform_units)).slice(0, 12).reverse();
      initChart('unitSplitChart').setOption({
        color: ['#2563eb', '#0f766e'],
        tooltip: { trigger: 'axis', formatter: axisTooltip },
        legend: { top: 0 },
        grid: { left: 86, right: 24, top: 48, bottom: 32 },
        xAxis: { type: 'value', axisLabel: { formatter: value => compact(value) } },
        yAxis: { type: 'category', data: rows.map(row => row.category) },
        series: [
          { name: 'SP销量', type: 'bar', stack: 'units', data: rows.map(row => n(row.sp_units)) },
          { name: 'TT销量', type: 'bar', stack: 'units', data: rows.map(row => n(row.tt_units)) },
        ],
      }, true);
    }
    function renderInsights(rows, compareRows, categoryRows, offsiteProductRows, period) {
      const t = totals(rows);
      const p = totals(compareRows || []);
      const topCategory = categoryRows[0];
      const topMediaCategory = [...categoryRows].sort((a, b) => n(b.media_spend_rmb) - n(a.media_spend_rmb))[0];
      const topOffsite = offsiteProductRows[0];
      const freshness = DATA.summary.freshness || {};
      const ref = DATA.category_reference || {};
      const currentRange = period?.current ? `${period.current.start} ~ ${period.current.end}` : '-';
      const compareRange = period?.compare ? `${period.compare.start} ~ ${period.compare.end}` : '-';
      const topCategoryName = topCategory ? escapeHtml(topCategory.category) : '暂无品类';
      const topMediaCategoryName = topMediaCategory ? escapeHtml(topMediaCategory.category) : '暂无品类';
      const topOffsiteName = topOffsite ? escapeHtml(topOffsite.product) : '暂无产品';
      const topOffsiteCategory = topOffsite ? escapeHtml(topOffsite.category || '-') : '-';
      const narrative = [
        {
          title: '现况 + 原因',
          body: `当前周期 <strong>${currentRange}</strong> 平台 GMV 为 <strong>${money(t.platform_gmv_rmb)}</strong>，环比 ${deltaText(t.platform_gmv_rmb, p.platform_gmv_rmb)}；SP 占 ${ratio(t.sp_share)}，TT 占 ${ratio(t.tt_share)}。这版平台盘子只使用 SP 店铺实收 GMV 与 TT 销售 GMV，站内商品销售额和广告 GMV 只用于解释流量、承接和投放效率。`,
        },
        {
          title: '增量点汇总',
          body: topCategory
            ? `商品销售额最高品类是 <strong>${topCategoryName}</strong>，销售额 ${money(topCategory.product_paid_sales_rmb)}，SP+TT 销量 ${fmt0.format(n(topCategory.platform_units))}，加购率 ${ratio(topCategory.add_to_cart_rate)}。站外花费RMB最高产品是 <strong>${topOffsiteName}</strong>，归因品类 ${topOffsiteCategory}，花费 ${money(topOffsite?.spend_rmb)}。`
            : '当前筛选下暂未形成可用的品类汇总，需要先确认站内商品、SP/TT 销量与品类表映射是否覆盖当前周期。',
        },
        {
          title: '问题',
          body: `媒体侧当前总花费 ${money(t.media_spend_rmb)}，占平台 GMV ${ratio(t.media_spend_ratio)}；站内 ROAS ${roas(t.onsite_roas)}，站外 ROAS ${roas(t.offsite_roas)}。访客 ${fmt0.format(n(t.product_visitors))}，支付件转化率 ${ratio(t.unit_conversion_rate)}。需要重点看“有访问但转化弱”与“花费高但回收弱”的品类。`,
        },
        {
          title: '下一步动作',
          body: `问题1 -> 动作1：先检查 <strong>${topMediaCategoryName}</strong> 的媒体费率、站内外 ROAS 和商品页转化，决定预算是否继续加。问题2 -> 动作2：对访客高但支付件转化低的品类下钻到商品 Top 表，优先改首图、券、组合装和评价承接。问题3 -> 动作3：站外继续按 Spend x 汇率核对 RMB 花费，避免低估站外投放对总媒体费率的压力。`,
        },
      ];
      if (byId('narrativeGrid')) {
        byId('narrativeGrid').innerHTML = narrative.map(item => `
          <article class="insight-box">
            <h3>${item.title}</h3>
            <p>${item.body}</p>
          </article>
        `).join('');
      }
      const insights = [
        `当前周期 ${currentRange}，对比周期 ${compareRange}。`,
        `平台 GMV 当前按 SP 店铺实收与 TT 销售 GMV 合并，SP 占比 ${ratio(t.sp_share)}，TT 占比 ${ratio(t.tt_share)}，平台 GMV 环比 ${deltaText(t.platform_gmv_rmb, p.platform_gmv_rmb)}。`,
        `站内广告 ROAS 为 ${roas(t.onsite_roas)}；站外按行级汇率转 RMB 后 ROAS 为 ${roas(t.offsite_roas)}，总媒体花费占平台 GMV ${ratio(t.media_spend_ratio)}。`,
        topCategory ? `当前筛选下商品销售额最高品类是 ${topCategory.category}，销售额 ${money(topCategory.product_paid_sales_rmb)}，SP+TT 销量 ${fmt0.format(topCategory.platform_units)}，加购率 ${ratio(topCategory.add_to_cart_rate)}。` : '当前筛选下暂未形成有效品类汇总。',
        topMediaCategory ? `媒体投入最高品类是 ${topMediaCategory.category}，媒体花费 ${money(topMediaCategory.media_spend_rmb)}，综合 ROAS ${roas(topMediaCategory.media_roas)}，媒体花费/商品销售额 ${ratio(topMediaCategory.media_spend_ratio)}。` : '当前筛选下暂无可归因媒体投入。',
        topOffsite ? `站外花费RMB最高产品是 ${topOffsite.product}，映射品类 ${topOffsite.category || '-'}，花费 ${money(topOffsite.spend_rmb)}，站外GMV ${money(topOffsite.purchase_value_rmb)}。` : '站外产品维度暂未形成有效汇总。',
        `品类表已读取 ${fmt0.format(ref.item_count || 0)} 个 Item ID、${fmt0.format(ref.sku_count || 0)} 个 SKU 与 ${fmt0.format(ref.category_count || 0)} 个品类标签，用于广告、销量与商品的归因兜底。`,
        `数据新鲜度：SP ${freshness.sp_gmv || '-'}，TT ${freshness.tt_gmv || '-'}，站外 ${freshness.offsite || '-'}，站内广告 ${freshness.onsite_ads || '-'}。`,
      ];
      if (byId('insightList')) {
        byId('insightList').innerHTML = insights.map(text => `<li>${escapeHtml(text)}</li>`).join('');
      }
    }
    function renderCategoryTable(categoryRows, compareCategoryRows) {
      const rows = categoryRows.slice(0, 40);
      if (!rows.length) {
        byId('categoryTable').innerHTML = '<div class="empty-state">当前周期暂无品类数据</div>';
        return;
      }
      const compareByCategory = new Map((compareCategoryRows || []).map(row => [row.category, row]));
      byId('categoryTable').innerHTML = `
        <table>
          <thead>
            <tr>
              <th>品类</th><th>商品销售额RMB</th><th>销售占比</th><th>SP销量</th><th>TT销量</th><th>销量增幅</th><th>访问</th><th>加购率</th><th>支付件转化</th><th>站内花费</th><th>站内ROAS</th><th>站外花费RMB</th><th>站外ROAS</th><th>总媒体花费</th><th>综合ROAS</th><th>媒体/销售额</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(row => {
              const previous = compareByCategory.get(row.category) || {};
              return `
                <tr>
                  <td>${row.category}</td>
                  <td>${tableMetricHtml(row.product_paid_sales_rmb, previous.product_paid_sales_rmb, money)}</td>
                  <td>${tableMetricHtml(row.sales_share, previous.sales_share, ratio, { neutral: true })}</td>
                  <td>${tableMetricHtml(row.sp_units, previous.sp_units, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.tt_units, previous.tt_units, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.unit_growth, previous.unit_growth, ratio)}</td>
                  <td>${tableMetricHtml(row.product_visitors, previous.product_visitors, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.add_to_cart_rate, previous.add_to_cart_rate, ratio)}</td>
                  <td>${tableMetricHtml(row.unit_conversion_rate, previous.unit_conversion_rate, ratio)}</td>
                  <td>${tableMetricHtml(row.onsite_spend_rmb, previous.onsite_spend_rmb, money, { neutral: true })}</td>
                  <td>${tableMetricHtml(row.onsite_roas, previous.onsite_roas, roas)}</td>
                  <td>${tableMetricHtml(row.offsite_spend_rmb, previous.offsite_spend_rmb, money, { neutral: true })}</td>
                  <td>${tableMetricHtml(row.offsite_roas, previous.offsite_roas, roas)}</td>
                  <td>${tableMetricHtml(row.media_spend_rmb, previous.media_spend_rmb, money, { neutral: true })}</td>
                  <td>${tableMetricHtml(row.media_roas, previous.media_roas, roas)}</td>
                  <td>${tableMetricHtml(row.media_spend_ratio, previous.media_spend_ratio, ratio, { inverse: true })}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    }
    function renderOffsiteProductTable(offsiteProductRows, compareOffsiteProductRows, productRows, compareProductRows) {
      const rows = offsiteProductRows.slice(0, 30);
      if (!rows.length) {
        byId('offsiteProductTable').innerHTML = '<div class="empty-state">暂无站外产品数据</div>';
        return;
      }
      const compareByProduct = new Map((compareOffsiteProductRows || []).map(row => [`${row.category || '-'}||${row.product || '-'}`, row]));
      const spGmvLookup = buildProductGmvLookup(productRows);
      const compareSpGmvLookup = buildProductGmvLookup(compareProductRows);
      const spProductGmvTotal = sum(productRows || [], 'paid_sales_rmb');
      const compareSpProductGmvTotal = sum(compareProductRows || [], 'paid_sales_rmb');
      byId('offsiteProductTable').innerHTML = `
        <table class="offsite-product-table">
          <thead><tr><th>产品</th><th>SP商品GMV</th><th>GMV占比</th><th>花费RMB</th><th>消耗占比</th><th>站外GMV</th><th>ROAS</th><th>展示</th><th>点击</th><th>加购</th><th>转化</th></tr></thead>
          <tbody>
            ${rows.map(row => {
              const previous = compareByProduct.get(`${row.category || '-'}||${row.product || '-'}`) || {};
              const spProductGmv = lookupSpProductGmv(row, spGmvLookup);
              const previousSpProductGmv = lookupSpProductGmv(row, compareSpGmvLookup);
              const spProductGmvShare = spProductGmvTotal ? spProductGmv / spProductGmvTotal : null;
              const previousSpProductGmvShare = compareSpProductGmvTotal ? previousSpProductGmv / compareSpProductGmvTotal : null;
              return `
                <tr>
                  <td><div class="product-cell">${escapeHtml(row.product)}</div></td>
                  <td>${tableMetricHtml(spProductGmv, previousSpProductGmv, money)}</td>
                  <td>${tableMetricHtml(spProductGmvShare, previousSpProductGmvShare, ratio)}</td>
                  <td>${tableMetricHtml(row.spend_rmb, previous.spend_rmb, money, { neutral: true })}</td>
                  <td>${tableMetricHtml(row.spend_share, previous.spend_share, ratio, { neutral: true })}</td>
                  <td>${tableMetricHtml(row.purchase_value_rmb, previous.purchase_value_rmb, money)}</td>
                  <td>${tableMetricHtml(row.roas, previous.roas, roas)}</td>
                  <td>${tableMetricHtml(row.impressions, previous.impressions, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.clicks, previous.clicks, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.add_to_cart, previous.add_to_cart, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.conversions, previous.conversions, value => fmt0.format(n(value)))}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    }
    function renderActionSignalGroup(type, title, rows, emptyText) {
      const visibleRows = rows.slice(0, 4);
      const isReduce = type === 'reduce';
      const actionText = isReduce ? '建议减量' : '建议加投';
      const directionClass = change => change > 0 ? 'up' : (change < 0 ? 'down' : '');
      return `
        <section class="action-signal-group ${type}">
          <div class="action-signal-group-head">
            <strong>${title}</strong>
            <span class="action-signal-count">${rows.length} 个商品</span>
          </div>
          ${visibleRows.length ? `
            <ol class="action-signal-list">
              ${visibleRows.map(row => `
                <li class="action-signal-item">
                  <div class="action-signal-copy">
                    <div class="action-signal-product">
                      <strong>${escapeHtml(row.product)}</strong>
                      <span>${escapeHtml(row.category)}</span>
                    </div>
                    <div class="action-signal-evidence">
                      <span>花费 ${money(row.currentSpend)}<b class="signal-delta ${directionClass(row.spendChange)}">${signalChangeText(row.spendChange)}</b></span>
                      <span>SP商品GMV ${money(row.currentSpGmv)}<b class="signal-delta ${directionClass(row.spGmvChange)}">${signalChangeText(row.spGmvChange)}</b></span>
                    </div>
                  </div>
                  <span class="signal-action">${actionText}</span>
                </li>
              `).join('')}
            </ol>
            ${rows.length > visibleRows.length ? `<div class="action-signal-count">另有 ${rows.length - visibleRows.length} 个，已按业务影响排序</div>` : ''}
          ` : `<div class="action-signal-empty">${emptyText}</div>`}
        </section>
      `;
    }
    function renderOffsiteActionSignals(offsiteProductRows, compareOffsiteProductRows, productRows, compareProductRows, period) {
      const target = byId('offsiteActionSignals');
      if (!target) return;
      if (period?.invalid) {
        target.innerHTML = '<div class="action-signal-empty" style="padding:18px">请先选择完整的当前周期与对比周期。</div>';
        return;
      }
      const reduceSpendThreshold = 0.10;
      const actionRows = buildOffsiteActionRows(offsiteProductRows, compareOffsiteProductRows, productRows, compareProductRows);
      const comparableRows = actionRows.filter(row => row.hasComparableBase);
      const reduceRows = comparableRows
        .filter(row => row.spendChange >= reduceSpendThreshold && row.spGmvChange < 0)
        .sort((a, b) => {
          const impactA = Math.max(a.currentSpend - a.previousSpend, 0) + Math.max(a.previousSpGmv - a.currentSpGmv, 0);
          const impactB = Math.max(b.currentSpend - b.previousSpend, 0) + Math.max(b.previousSpGmv - b.currentSpGmv, 0);
          return impactB - impactA;
        });
      const scaleRows = comparableRows
        .filter(row => row.spGmvChange > 0 || (row.spendChange < 0 && row.spGmvChange > row.spendChange))
        .sort((a, b) => {
          const impactA = Math.max(a.currentSpGmv - a.previousSpGmv, 0) + Math.max(a.previousSpend - a.currentSpend, 0);
          const impactB = Math.max(b.currentSpGmv - b.previousSpGmv, 0) + Math.max(b.previousSpend - b.currentSpend, 0);
          return impactB - impactA;
        });
      const scaleEmptyText = '当前周期暂无 GMV 增长或 GMV 跌幅小于花费跌幅的商品。';
      const category = byId('categoryFilter')?.value || 'ALL';
      const categoryText = category === 'ALL' ? '全部品类' : category;
      const currentRange = `${period.current.start} 至 ${period.current.end}`;
      const compareRange = `${period.compare.start} 至 ${period.compare.end}`;
      const excludedCount = actionRows.length - comparableRows.length;
      target.innerHTML = `
        <div class="action-signal-head">
          <div class="action-signal-heading">
            <span class="action-signal-kicker">基础判断</span>
            <h3>站外投放动作提示</h3>
            <p>${escapeHtml(categoryText)} · 当前 ${escapeHtml(currentRange)} 对比 ${escapeHtml(compareRange)}</p>
          </div>
          <p class="action-signal-rule">花费增幅 ≥10% 且 SP 商品GMV下降，提示减量；SP 商品GMV上涨，或花费下降时 GMV跌幅小于花费跌幅，提示加投。零基期不强行计算环比。</p>
        </div>
        <div class="action-signal-grid">
          ${renderActionSignalGroup('reduce', '减量预警 · 花费增、GMV降', reduceRows, '当前筛选下暂无达到条件的减量预警。')}
          ${renderActionSignalGroup('scale', '加投机会 · GMV增长 / 相对抗跌', scaleRows, scaleEmptyText)}
        </div>
        <p class="action-signal-footnote">共核对 ${actionRows.length} 个站外产品，其中 ${comparableRows.length} 个具备双周期有效基数${excludedCount ? `，${excludedCount} 个因上期花费或上期 SP 商品GMV为 0 未下动作结论` : ''}。执行前再结合库存、利润与素材状态确认。</p>
      `;
    }
    function renderProductTable(productRows, compareProductRows) {
      const rows = productRows.slice(0, 40);
      if (!rows.length) {
        byId('productTable').innerHTML = '<div class="empty-state">暂无匹配商品数据</div>';
        return;
      }
      const compareByProduct = new Map((compareProductRows || []).map(row => [`${row.category || '-'}||${row.product || '-'}`, row]));
      byId('productTable').innerHTML = `
        <table>
          <thead>
            <tr>
              <th>商品</th><th>品类</th><th>商品销售额RMB</th><th>GMV占比</th><th>销量</th><th>访问</th><th>页面浏览</th><th>加购访客</th><th>加购率</th><th>支付件转化</th><th>商品点击率</th><th>客访价值</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(row => {
              const previous = compareByProduct.get(`${row.category || '-'}||${row.product || '-'}`) || {};
              return `
                <tr>
                  <td><div class="product-cell">${escapeHtml(row.product)}</div></td>
                  <td><span class="category-cell">${escapeHtml(row.category || '-')}</span></td>
                  <td>${tableMetricHtml(row.paid_sales_rmb, previous.paid_sales_rmb, money)}</td>
                  <td>${tableMetricHtml(row.gmv_share, previous.gmv_share, ratio, { neutral: true })}</td>
                  <td>${tableMetricHtml(row.paid_units, previous.paid_units, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.visitors, previous.visitors, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.page_views, previous.page_views, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.add_to_cart_visitors, previous.add_to_cart_visitors, value => fmt0.format(n(value)))}</td>
                  <td>${tableMetricHtml(row.add_to_cart_rate, previous.add_to_cart_rate, ratio)}</td>
                  <td>${tableMetricHtml(row.unit_conversion_rate, previous.unit_conversion_rate, ratio)}</td>
                  <td>${tableMetricHtml(row.ctr, previous.ctr, ratio)}</td>
                  <td>${tableMetricHtml(row.sales_per_visitor, previous.sales_per_visitor, money)}</td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      `;
    }
    function renderDailyTable(rows) {
      const latest = [...rows].reverse().slice(0, 20);
      if (!latest.length) {
        byId('dailyTable').innerHTML = '<div class="empty-state">当前周期暂无日明细数据</div>';
        return;
      }
      byId('dailyTable').innerHTML = `
        <table>
          <thead>
            <tr>
              <th>日期</th><th>平台GMV</th><th>SP GMV</th><th>TT GMV</th><th>站外花费RMB</th><th>站内花费</th><th>商品访问</th><th>加购</th><th>支付件</th>
            </tr>
          </thead>
          <tbody>
            ${latest.map(row => `
              <tr>
                <td>${row.date}</td>
                <td>${money(row.platform_gmv_rmb)}</td>
                <td>${money(row.sp_gmv_rmb)}</td>
                <td>${money(row.tt_gmv_rmb)}</td>
                <td>${money(row.offsite_spend_rmb)}</td>
                <td>${money(row.onsite_spend_rmb)}</td>
                <td>${fmt0.format(n(row.product_visitors))}</td>
                <td>${fmt0.format(n(row.product_add_to_cart_visitors))}</td>
                <td>${fmt0.format(n(row.product_paid_units))}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }
    function renderFieldTable() {
      const rows = DATA.field_map || [];
      byId('fieldTable').innerHTML = `
        <table>
          <thead><tr><th>模块</th><th>Sheet</th><th>日期字段</th><th>核心字段</th><th>口径处理</th></tr></thead>
          <tbody>
            ${rows.map(row => `
              <tr>
                <td>${row.module}</td>
                <td>${row.sheet}</td>
                <td>${row.date}</td>
                <td>${row.metric}</td>
                <td>${row.normalization}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }
    function renderAll() {
      const period = selectedPeriod();
      updatePeriodSummary(period);
      const rows = selectedRows(period);
      const compare = previousRows(period);
      const categoryRows = selectedCategoryRows(period);
      const compareCategoryRows = selectedCategoryRows(period, 'compare');
      const offsiteProductRows = selectedOffsiteProductRows(period);
      const compareOffsiteProductRows = selectedOffsiteProductRows(period, 'compare');
      const productRows = selectedProductRows(period);
      const compareProductRows = selectedProductRows(period, 'compare');
      const trendRows = selectedTrendRows(period);
      const compareTrendRows = selectedTrendRows(period, 'compare');
      renderMetrics(rows, compare);
      renderTrend(trendRows, compareTrendRows, period);
      renderPeriodComparisonCharts(trendRows, compareTrendRows, period);
      renderPlatformSplit(rows);
      renderFunnel(rows);
      renderCategoryChart(categoryRows);
      renderCategoryMediaChart(categoryRows);
      renderUnitSplitChart(categoryRows);
      renderInsights(rows, compare, categoryRows, offsiteProductRows, period);
      renderCategoryTable(categoryRows, compareCategoryRows);
      renderProductTable(productRows, compareProductRows);
      renderOffsiteProductTable(offsiteProductRows, compareOffsiteProductRows, productRows, compareProductRows);
      renderOffsiteActionSignals(offsiteProductRows, compareOffsiteProductRows, productRows, compareProductRows, period);
      renderDailyTable(rows);
      renderFieldTable();
    }
    function setupDateControls() {
      const bounds = dataDateBounds();
      ['startDateFilter', 'endDateFilter', 'compareStartDateFilter', 'compareEndDateFilter'].forEach(id => {
        byId(id).min = bounds.start || '';
        byId(id).max = bounds.end || '';
      });
      byId('periodFilter').value = 'MTD';
      applyPreset('MTD');
    }
    function setupCategoryControls() {
      const select = byId('categoryFilter');
      const scores = new Map();
      (DATA.category_rows || []).forEach(row => {
        if (!row.category) return;
        scores.set(row.category, Math.max(n(scores.get(row.category)), n(row.product_paid_sales_rmb), n(row.media_spend_rmb), n(row.platform_units)));
      });
      (DATA.category_daily_rows || []).forEach(row => {
        if (row.category && !scores.has(row.category)) scores.set(row.category, 0);
      });
      select.innerHTML = '';
      select.add(new Option('全部品类', 'ALL'));
      [...scores.entries()]
        .sort((a, b) => n(b[1]) - n(a[1]) || a[0].localeCompare(b[0], 'zh-CN'))
        .forEach(([category]) => select.add(new Option(category, category)));
      select.value = 'ALL';
      select.addEventListener('change', renderAll);
    }
    function setupSideNav() {
      const nav = byId('sideNav');
      const toggle = byId('sideNavToggle');
      if (!nav || !toggle) return;
      const setCollapsed = collapsed => {
        nav.classList.toggle('is-collapsed', collapsed);
        document.body.classList.toggle('side-nav-collapsed', collapsed);
        toggle.setAttribute('aria-expanded', String(!collapsed));
        toggle.setAttribute('aria-label', collapsed ? '展开导航' : '收起导航');
        toggle.title = collapsed ? '展开导航' : '收起导航';
      };
      let saved = false;
      try {
        saved = localStorage.getItem('sktSideNavCollapsed') === '1';
      } catch (error) {
        saved = false;
      }
      setCollapsed(saved);
      toggle.addEventListener('click', () => {
        const collapsed = !nav.classList.contains('is-collapsed');
        setCollapsed(collapsed);
        try {
          localStorage.setItem('sktSideNavCollapsed', collapsed ? '1' : '0');
        } catch (error) {}
      });
    }
    const REFRESH_STORAGE_KEY = 'sktMainReportRefreshRequest';
    const REFRESH_POLL_INTERVAL_MS = 5000;
    const REFRESH_API_MAX_ATTEMPTS = 3;
    const REFRESH_API_TIMEOUT_MS = 30000;
    const ONLINE_REPORT_URL = 'https://skt-singapore-report.pages.dev/';
    let refreshPollTimer = null;
    let refreshPollFailures = 0;

    function setRefreshState(state, message, busy = false) {
      const button = byId('refreshReport');
      const label = byId('refreshButtonLabel');
      const status = byId('refreshStatus');
      button.disabled = busy;
      button.classList.toggle('is-loading', busy);
      label.textContent = busy ? (state === 'in_progress' ? '正在刷新' : '等待刷新') : '刷新数据';
      status.hidden = !message;
      status.dataset.state = state || '';
      status.textContent = message || '';
    }

    function readRefreshRequest() {
      try {
        return sessionStorage.getItem(REFRESH_STORAGE_KEY) || '';
      } catch (error) {
        return '';
      }
    }

    function saveRefreshRequest(requestId) {
      try {
        if (requestId) sessionStorage.setItem(REFRESH_STORAGE_KEY, requestId);
        else sessionStorage.removeItem(REFRESH_STORAGE_KEY);
      } catch (error) {}
    }

    function waitForRefreshRetry(delayMs) {
      return new Promise(resolve => window.setTimeout(resolve, delayMs));
    }

    async function refreshApi(path, options = {}) {
      let response = null;
      for (let attempt = 1; attempt <= REFRESH_API_MAX_ATTEMPTS; attempt += 1) {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), REFRESH_API_TIMEOUT_MS);
        try {
          response = await fetch(path, {
            cache: 'no-store',
            credentials: 'same-origin',
            ...options,
            headers: { Accept: 'application/json', ...(options.headers || {}) },
            signal: controller.signal,
          });
          break;
        } catch (error) {
          if (attempt >= REFRESH_API_MAX_ATTEMPTS) {
            const message = navigator.onLine === false
              ? '当前网络不可用，请恢复网络后重试'
              : '连接刷新服务失败，已自动重试，请稍后再点一次';
            throw new Error(message);
          }
          await waitForRefreshRetry(attempt * 1000);
        } finally {
          window.clearTimeout(timeout);
        }
      }
      if (!response) throw new Error('连接刷新服务失败');
      const contentType = response.headers.get('content-type') || '';
      const payload = contentType.includes('application/json') ? await response.json() : {};
      if (!response.ok) {
        if (response.status === 401) throw new Error('登录已过期，请重新载入页面登录');
        throw new Error(payload.message || `刷新服务返回 ${response.status}`);
      }
      return payload;
    }

    function stopRefreshPolling() {
      if (refreshPollTimer) window.clearTimeout(refreshPollTimer);
      refreshPollTimer = null;
    }

    async function pollRefreshStatus(requestId) {
      stopRefreshPolling();
      try {
        const result = await refreshApi(`/__refresh?id=${encodeURIComponent(requestId)}`);
        refreshPollFailures = 0;
        const status = result.status || 'queued';
        if (status === 'completed') {
          saveRefreshRequest('');
          if (result.conclusion === 'success') {
            setRefreshState('success', '数据已更新，正在载入最新报表', false);
            window.setTimeout(() => window.location.reload(), 1200);
          } else {
            setRefreshState('failure', result.message || '刷新失败，线上报表已保留原版本', false);
          }
          return;
        }
        if (status === 'in_progress') {
          setRefreshState('in_progress', '正在抓取 Google Sheet 并发布报表', true);
        } else {
          setRefreshState('queued', result.reused ? '已有刷新任务，正在等待执行' : '刷新任务已提交，正在等待执行', true);
        }
      } catch (error) {
        refreshPollFailures += 1;
        if (refreshPollFailures >= 3) {
          saveRefreshRequest('');
          setRefreshState('failure', error.message || '暂时无法查询刷新状态', false);
          return;
        }
        setRefreshState('queued', '状态查询暂时失败，正在重试', true);
      }
      refreshPollTimer = window.setTimeout(() => pollRefreshStatus(requestId), REFRESH_POLL_INTERVAL_MS);
    }

    async function requestReportRefresh() {
      stopRefreshPolling();
      const isLocalPreview = window.location.protocol === 'file:'
        || ['localhost', '127.0.0.1'].includes(window.location.hostname);
      if (isLocalPreview) {
        setRefreshState('queued', '正在打开线上报表', true);
        window.location.assign(ONLINE_REPORT_URL);
        return;
      }
      setRefreshState('queued', '正在提交刷新任务', true);
      try {
        const result = await refreshApi('/__refresh', { method: 'POST' });
        const requestId = result.request_id;
        if (!requestId) throw new Error('刷新服务未返回任务编号');
        saveRefreshRequest(requestId);
        refreshPollFailures = 0;
        setRefreshState(result.status || 'queued', result.reused ? '已有刷新任务，已接入进度' : '刷新任务已提交', true);
        await pollRefreshStatus(requestId);
      } catch (error) {
        saveRefreshRequest('');
        setRefreshState('failure', error.message || '刷新任务提交失败', false);
      }
    }

    function setupRefreshControl() {
      byId('refreshReport').addEventListener('click', requestReportRefresh);
      const requestId = readRefreshRequest();
      if (requestId) {
        setRefreshState('queued', '正在恢复刷新任务状态', true);
        pollRefreshStatus(requestId);
      }
    }
    function setup() {
      setupDateControls();
      setupCategoryControls();
      setupSideNav();
      setupRefreshControl();
      byId('periodFilter').addEventListener('change', event => {
        applyPreset(event.target.value);
        renderAll();
      });
      ['startDateFilter', 'endDateFilter', 'compareStartDateFilter', 'compareEndDateFilter'].forEach(id => {
        byId(id).addEventListener('change', () => {
          byId('periodFilter').value = 'CUSTOM';
          renderAll();
        });
      });
      byId('resetFilters').addEventListener('click', () => {
        byId('periodFilter').value = 'MTD';
        byId('categoryFilter').value = 'ALL';
        applyPreset('MTD');
        renderAll();
      });
      byId('downloadTrend').addEventListener('click', () => {
        const chart = charts.trendChart;
        if (!chart) return;
        const link = document.createElement('a');
        link.href = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#ffffff' });
        link.download = 'skt-platform-gmv-media-trend.png';
        link.click();
      });
      window.addEventListener('resize', () => Object.values(charts).forEach(chart => chart.resize()));
      renderAll();
    }
    setup();
  </script>
</body>
</html>
"""


def build_html(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__PAYLOAD__", payload_json)


def validate_report_html(html: str) -> None:
    required_fragments = (
        '<table class="offsite-product-table">',
        '<thead><tr><th>产品</th><th>SP商品GMV</th><th>GMV占比</th><th>花费RMB</th><th>消耗占比</th><th>站外GMV</th><th>ROAS</th><th>展示</th><th>点击</th><th>加购</th><th>转化</th></tr></thead>',
        'id="offsiteActionSignals"',
        "function renderOffsiteActionSignals",
        'id="refreshReport"',
        "function setupRefreshControl",
        "'/__refresh'",
        "REFRESH_API_MAX_ATTEMPTS",
        'id="clicksCompareChart"',
        'id="ctrCompareChart"',
        'id="conversionCompareChart"',
        'id="spGmvCompareChart"',
        'id="onsiteSpendCompareChart"',
        'id="offsiteSpendCompareChart"',
        "function renderPeriodComparisonCharts",
        "GMV(After Seller Discounts)（I列）",
        '<a href="skt-material-analysis.html">素材分析</a>',
    )
    forbidden_fragments = (
        "<th>Purchase Value RMB</th>",
        "<th>平均汇率</th>",
        "<th>归因品类</th><th>SP商品GMV</th>",
        '"metric": "GMV(Customer Payment)"',
    )
    missing = [fragment for fragment in required_fragments if fragment not in html]
    stale = [fragment for fragment in forbidden_fragments if fragment in html]
    if missing or stale:
        raise ValueError(f"Report UI contract failed. Missing={missing}; stale={stale}")


def run() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    html = build_html(payload)
    validate_report_html(html)
    output_path = OUTPUT_DIR / "skt_onsite_offsite_alignment.html"
    site_path = SITE_DIR / "skt-onsite-offsite-alignment.html"
    index_path = SITE_DIR / "index.html"
    public_index_path = ROOT / "index.html"
    nojekyll_path = ROOT / ".nojekyll"
    output_path.write_text(html, encoding="utf-8")
    site_path.write_text(html, encoding="utf-8")
    index_path.write_text(html, encoding="utf-8")
    public_index_path.write_text(html, encoding="utf-8")
    nojekyll_path.touch()
    return output_path


if __name__ == "__main__":
    print(run())
