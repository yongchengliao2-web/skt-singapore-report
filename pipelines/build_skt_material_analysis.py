# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

from build_skt_alignment import (
    DEFAULT_OFFSITE_FX_RATE,
    OUTPUT_DIR,
    ROOT,
    SITE_DIR,
    SPREADSHEET_ID,
    clean_text,
    ensure_source_csvs,
    get_value,
    load_category_reference,
    normalize_text,
    parse_date,
    parse_number,
    read_csv,
    resolve_categories,
)
from build_skt_material_snapshots import ensure_material_snapshots


DMS_DIR = ROOT / "data" / "dms"
MATERIAL_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(?:SC|JJ)[A-Za-z0-9]{6,}(?![A-Za-z0-9])", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def first_url(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        match = URL_RE.search(text)
        if match:
            return match.group(0)
    return ""


def material_code(*values: Any) -> str:
    for value in values:
        match = MATERIAL_CODE_RE.search(str(value or ""))
        if match:
            return match.group(0).upper()
    return ""


def fallback_material_key(*values: Any) -> str:
    text = " ".join(clean_text(value) for value in values if clean_text(value))
    if not text:
        text = "unknown"
    digest = hashlib.sha1(text.casefold().encode("utf-8")).hexdigest()[:12]
    return f"AD-{digest.upper()}"


def normalize_material_type(*values: Any) -> str:
    text = " ".join(clean_text(value).casefold() for value in values if clean_text(value))
    if not text:
        return "未标记"
    if any(token in text for token in ("video", "视频", "mp4", "mov", "webm")):
        return "视频"
    if "gif" in text:
        return "GIF"
    if any(token in text for token in ("image", "图片", "图文", "jpg", "jpeg", "png", "webp")):
        return "图片"
    if "carousel" in text or "轮播" in text:
        return "轮播"
    return clean_text(values[0]) if values and clean_text(values[0]) else "未标记"


def metric_row() -> dict[str, Any]:
    return {
        "spend": 0.0,
        "spend_rmb": 0.0,
        "purchase_value": 0.0,
        "purchase_value_rmb": 0.0,
        "impressions": 0.0,
        "clicks": 0.0,
        "conversions": 0.0,
        "add_to_cart": 0.0,
        "row_count": 0,
    }


def add_metrics(target: dict[str, Any], source: dict[str, Any], weight: float = 1.0) -> None:
    for key in ("spend", "spend_rmb", "purchase_value", "purchase_value_rmb", "impressions", "clicks", "conversions", "add_to_cart"):
        target[key] = float(target.get(key) or 0.0) + float(source.get(key) or 0.0) * weight
    target["row_count"] = int(target.get("row_count") or 0) + int(source.get("row_count") or 0)


def finalize_metrics(row: dict[str, Any]) -> dict[str, Any]:
    row["roas"] = safe_div(float(row.get("purchase_value_rmb") or 0.0), float(row.get("spend_rmb") or 0.0))
    row["ctr"] = safe_div(float(row.get("clicks") or 0.0), float(row.get("impressions") or 0.0))
    row["cvr"] = safe_div(float(row.get("conversions") or 0.0), float(row.get("clicks") or 0.0))
    row["cpa_rmb"] = safe_div(float(row.get("spend_rmb") or 0.0), float(row.get("conversions") or 0.0))
    row["aov_rmb"] = safe_div(float(row.get("purchase_value_rmb") or 0.0), float(row.get("conversions") or 0.0))
    return row


DAILY_PAYLOAD_FIELDS = (
    "date",
    "material_key",
    "material_id",
    "material_name",
    "product",
    "category",
    "categories",
    "material_type",
    "post_url",
    "preview_url",
    "play_url",
    "spend",
    "spend_rmb",
    "purchase_value",
    "purchase_value_rmb",
    "impressions",
    "clicks",
    "conversions",
    "add_to_cart",
    "row_count",
    "roas",
    "ctr",
    "cvr",
)


def slim_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    return value


def compact_daily_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: slim_value(row.get(key)) for key in DAILY_PAYLOAD_FIELDS if row.get(key) not in (None, "", [])}


def latest_dms_csv() -> Path | None:
    latest = DMS_DIR / "skt_dms_materials_latest.csv"
    if latest.exists() and latest.stat().st_size > 0:
        return latest
    candidates = sorted(DMS_DIR.glob("skt_dms_materials_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def load_dms_rows(category_ref: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], str]:
    path = latest_dms_csv()
    if path is None:
        return {}, [], ""

    rows: list[dict[str, Any]] = []
    by_code: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            code = clean_text(raw.get("material_id") or raw.get("素材编号")).upper()
            product = clean_text(raw.get("product") or raw.get("产品"))
            name = clean_text(raw.get("material_name") or raw.get("素材名称"))
            categories = resolve_categories(category_ref, product, name)
            item = {
                "material_id": code,
                "material_key": code or fallback_material_key(product, name),
                "material_name": name or code or "未命名素材",
                "product": product or "未填产品",
                "category": " / ".join(categories),
                "categories": categories,
                "material_type": normalize_material_type(raw.get("material_type") or raw.get("素材类型"), name),
                "material_source": clean_text(raw.get("material_source") or raw.get("素材来源")) or "DMS",
                "status": clean_text(raw.get("status") or raw.get("投放状态")),
                "ad_count": parse_number(raw.get("ad_count") or raw.get("广告数量")),
                "linked_ad_count": parse_number(raw.get("linked_ad_count") or raw.get("关联广告ID数")),
                "created_at": clean_text(raw.get("created_at") or raw.get("创建时间")),
                "post_url": clean_text(raw.get("post_url") or raw.get("帖子链接") or raw.get("Post URL")),
                "preview_url": clean_text(raw.get("preview_url") or raw.get("预览链接")),
                "play_url": clean_text(raw.get("play_url") or raw.get("播放链接")),
                "file_type": clean_text(raw.get("file_type") or raw.get("文件类型")),
                "file_size_mb": parse_number(raw.get("file_size_mb") or raw.get("文件大小MB")),
                "source": "DMS",
            }
            rows.append(item)
            if code:
                by_code[code] = item
            for alias in MATERIAL_CODE_RE.findall(name):
                by_code[alias.upper()] = item
    return by_code, rows, str(path)


def build_offsite_materials(path: Path, category_ref: dict[str, Any], dms_lookup: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_day_material: dict[tuple[str, str], dict[str, Any]] = {}
    material_meta: dict[str, dict[str, Any]] = {}

    for row in read_csv(path):
        day = parse_date(get_value(row, "Date_start"))
        if not day:
            continue

        ad_name = clean_text(get_value(row, "Ad_name"))
        adset_name = clean_text(get_value(row, "adset_name"))
        campaign_name = clean_text(get_value(row, "campaign_name"))
        product = clean_text(get_value(row, "产品")) or "未归类产品"
        code = material_code(ad_name, adset_name, campaign_name)
        key = code or fallback_material_key(ad_name, adset_name, campaign_name, product)
        dms = dms_lookup.get(code, {}) if code else {}
        categories = resolve_categories(category_ref, product, ad_name, campaign_name, dms.get("material_name"))
        category = " / ".join(categories)
        row_fx = parse_number(get_value(row, "汇率", "Exchange Rate", "FX")) or DEFAULT_OFFSITE_FX_RATE
        spend = parse_number(get_value(row, "Spend"))
        purchase = parse_number(get_value(row, "Purchase Value"))
        metrics = {
            "spend": spend,
            "spend_rmb": spend * row_fx,
            "purchase_value": purchase,
            "purchase_value_rmb": purchase * row_fx,
            "impressions": parse_number(get_value(row, "Impressions")),
            "clicks": parse_number(get_value(row, "link-Click", "all-click")),
            "conversions": parse_number(get_value(row, "Conversions")),
            "add_to_cart": parse_number(get_value(row, "Add_to_cart")),
            "row_count": 1,
        }

        dms_name = dms.get("material_name") or ""
        dms_name_is_code = bool(MATERIAL_CODE_RE.fullmatch(dms_name))
        material_name = ("" if dms_name_is_code else dms_name) or ad_name or code or key
        material_type = normalize_material_type(dms.get("material_type"), get_value(row, "广告类型"), ad_name)
        preview_url = dms.get("preview_url") or dms.get("play_url") or first_url(ad_name, campaign_name)
        post_url = dms.get("post_url") or first_url(get_value(row, "帖子链接"), get_value(row, "Post URL"))
        meta = material_meta.setdefault(
            key,
            {
                "material_key": key,
                "material_id": dms.get("material_id") or code,
                "material_name": material_name,
                "product": dms.get("product") or product,
                "category": category,
                "categories": categories,
                "material_type": material_type,
                "funnel_type": clean_text(get_value(row, "类型")) or "未标记",
                "audience_type": clean_text(get_value(row, "拉新/再营销")) or "未标记",
                "ad_format": clean_text(get_value(row, "广告类型")) or material_type,
                "ad_name": ad_name,
                "campaign_name": campaign_name,
                "post_url": post_url,
                "preview_url": preview_url,
                "play_url": dms.get("play_url", ""),
                "material_source": dms.get("material_source") or "站外数据源",
                "status": dms.get("status", ""),
                "created_at": dms.get("created_at", ""),
                "source": "站外数据源+DMS" if dms else "站外数据源",
            },
        )
        if dms and not meta.get("preview_url"):
            meta["preview_url"] = dms.get("preview_url") or dms.get("play_url") or ""

        day_row = by_day_material.setdefault(
            (day, key),
            {
                **metric_row(),
                "date": day,
                **meta,
            },
        )
        add_metrics(day_row, metrics)

    daily_rows = [finalize_metrics(row) for row in by_day_material.values()]
    daily_rows.sort(key=lambda item: (item["date"], item["spend_rmb"]), reverse=True)

    by_material: dict[str, dict[str, Any]] = {}
    for row in daily_rows:
        target = by_material.setdefault(row["material_key"], {**metric_row(), **material_meta.get(row["material_key"], {})})
        add_metrics(target, row)
    material_rows = [finalize_metrics(row) for row in by_material.values()]
    material_rows.sort(key=lambda item: (item["spend_rmb"], item["purchase_value_rmb"], item["conversions"]), reverse=True)
    return daily_rows, material_rows


def build_library_rows(dms_rows: list[dict[str, Any]], material_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_keys = {row["material_key"] for row in material_rows}
    library: list[dict[str, Any]] = []
    for row in dms_rows:
        if row["material_key"] in active_keys:
            continue
        library.append(
            {
                **metric_row(),
                **row,
                "funnel_type": "未投放",
                "audience_type": "未投放",
                "ad_format": row.get("material_type") or "未标记",
                "ad_name": row.get("material_name") or "",
                "campaign_name": "",
                "source": "DMS未命中站外消耗",
                "roas": None,
                "ctr": None,
                "cvr": None,
                "cpa_rmb": None,
                "aov_rmb": None,
            }
        )
    library.sort(key=lambda item: (item.get("created_at") or "", item.get("ad_count") or 0), reverse=True)
    return library[:300]


def summarize_source(daily_rows: list[dict[str, Any]], material_rows: list[dict[str, Any]], dms_rows: list[dict[str, Any]], dms_path: str) -> dict[str, Any]:
    dates = sorted({row["date"] for row in daily_rows})
    total = finalize_metrics(metric_row())
    for row in daily_rows:
        add_metrics(total, row)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date_min": dates[0] if dates else "",
        "date_max": dates[-1] if dates else "",
        "offsite_daily_rows": len(daily_rows),
        "active_materials": len(material_rows),
        "dms_materials": len(dms_rows),
        "dms_source_csv": Path(dms_path).name if dms_path else "",
        "total_spend_rmb": total["spend_rmb"],
        "total_purchase_value_rmb": total["purchase_value_rmb"],
        "total_roas": total["roas"],
    }


def build_payload() -> dict[str, Any]:
    paths = ensure_source_csvs()
    category_ref = load_category_reference(paths["category_map"])
    dms_lookup, dms_rows, dms_path = load_dms_rows(category_ref)
    daily_rows, material_rows = build_offsite_materials(paths["offsite"], category_ref, dms_lookup)
    library_rows = build_library_rows(dms_rows, material_rows)

    categories = sorted({category for row in daily_rows + library_rows for category in row.get("categories", []) if category})
    products = sorted({row.get("product") for row in daily_rows + library_rows if row.get("product")})
    material_types = sorted({row.get("material_type") for row in daily_rows + library_rows if row.get("material_type")})

    return {
        "brand": "SKT",
        "market": "Singapore",
        "page": "material-analysis",
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit?usp=sharing",
        "source": summarize_source(daily_rows, material_rows, dms_rows, dms_path),
        "material_daily_rows": [compact_daily_row(row) for row in daily_rows],
        "material_rows": material_rows[:800],
        "library_rows": library_rows,
        "categories": categories,
        "products": products,
        "material_types": material_types,
        "field_map": [
            {
                "module": "站外素材绩效",
                "sheet": "站外数据源",
                "date": "Date_start",
                "metric": "Spend / Purchase Value / Impressions / link-Click / Conversions / Add_to_cart / 汇率",
                "normalization": "Spend 与 Purchase Value 乘行级汇率后进入 RMB 指标；素材编号优先从 Ad_name/adset_name/campaign_name 中识别 SC/JJ 编号。",
            },
            {
                "module": "品类归因",
                "sheet": "品类表",
                "date": "-",
                "metric": "单品 / SKU / 产品名 / 品类",
                "normalization": "产品、广告名、活动名和 DMS 素材名共同匹配品类；多品类素材在品类汇总中按品类数均摊。",
            },
            {
                "module": "DMS素材库",
                "sheet": "web.cerahdms.com DMS API 本地缓存",
                "date": "created_at",
                "metric": "materialCode / materialName / adProductNames / materialType / preview_url",
                "normalization": "DMS 只提供素材元信息和预览链接，不参与花费/GMV计算；公开页面不包含任何授权凭证。",
            },
        ],
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="referrer" content="no-referrer" />
  <title>SKT 新加坡全品类素材分析</title>
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
      --good: #0f766e;
      --warn: #b7791f;
      --bad: #b42318;
      --shadow: 0 12px 30px rgba(20, 63, 50, 0.08);
      --radius: 8px;
      font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body { margin: 0; color: var(--ink); background: var(--bg); line-height: 1.55; letter-spacing: 0; }
    a { color: inherit; }
    .topbar { position: sticky; top: 0; z-index: 40; border-bottom: 1px solid var(--line); background: rgba(244, 247, 245, .94); backdrop-filter: blur(14px); }
    .topbar-inner { width: min(1580px, calc(100vw - 96px)); margin: 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 0; }
    .brand { display: inline-flex; align-items: center; gap: 8px; font-weight: 950; }
    .mark { display: inline-grid; place-items: center; width: 34px; height: 34px; border-radius: 8px; background: var(--accent); color: #fff; font-size: 14px; }
    .nav { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px; }
    .nav a { padding: 7px 9px; border-radius: 8px; color: var(--muted); text-decoration: none; font-size: 12px; font-weight: 850; }
    .nav a:hover, .nav a.active { background: #fff; color: var(--ink); }
    .page { width: min(1580px, calc(100vw - 96px)); margin: 0 auto; padding: 18px 0 48px; }
    .shell { display: grid; gap: 14px; min-width: 0; }
    .hero { display: grid; place-items: center; min-height: 118px; padding: 28px; border-radius: 8px; background: #123f32; color: #fff; box-shadow: var(--shadow); text-align: center; }
    .hero h1 { margin: 0; font-size: clamp(32px, 3.2vw, 50px); line-height: 1.08; font-weight: 950; letter-spacing: 0; }
    .hero p { margin: 12px auto 0; max-width: 900px; color: rgba(255,255,255,.88); font-size: 15px; font-weight: 750; }
    .filters, .panel { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); box-shadow: var(--shadow); }
    .filters { padding: 14px; }
    .filter-grid { display: grid; grid-template-columns: minmax(150px, .76fr) minmax(330px, 1.25fr) minmax(330px, 1.25fr); gap: 12px; align-items: end; }
    .filter-row-secondary { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(180px, 1.1fr) minmax(240px, 1.45fr) minmax(170px, .9fr) minmax(170px, .9fr) auto; gap: 10px; align-items: end; padding-top: 2px; }
    .filter-group, .period-card { min-width: 0; display: grid; gap: 5px; }
    label, .period-label { color: var(--muted); font-size: 12px; font-weight: 900; }
    select, input { width: 100%; min-height: 36px; border: 1px solid var(--line); border-radius: 8px; background: #fff; color: var(--ink); padding: 7px 9px; font: inherit; font-size: 13px; font-weight: 750; }
    .period-card { grid-template-columns: 72px minmax(128px, 1fr) minmax(128px, 1fr); align-items: center; gap: 8px; padding: 8px; border-radius: 8px; background: var(--panel-soft); }
    .button { min-height: 36px; padding: 0 12px; border: 1px solid var(--accent); border-radius: 8px; background: var(--accent); color: #fff; font: inherit; font-size: 13px; font-weight: 900; cursor: pointer; white-space: nowrap; }
    .button.secondary { background: #fff; color: var(--accent); }
    .section { scroll-margin-top: 82px; }
    .section-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin: 8px 0 10px; }
    .section-head h2 { margin: 0; font-size: 20px; line-height: 1.2; font-weight: 950; letter-spacing: 0; }
    .section-note { margin: 4px 0 0; color: var(--muted); font-size: 13px; font-weight: 700; }
    .metric-grid { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 10px; }
    .metric { min-width: 0; padding: 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); box-shadow: var(--shadow); }
    .metric b { display: block; color: var(--muted); font-size: 12px; font-weight: 850; }
    .metric strong { display: block; margin-top: 5px; font-size: clamp(20px, 1.8vw, 30px); line-height: 1; font-weight: 950; white-space: nowrap; }
    .metric span { display: block; margin-top: 7px; color: var(--muted); font-size: 12px; font-weight: 800; }
    .delta.up { color: var(--good); }
    .delta.down { color: var(--bad); }
    .grid-2 { display: grid; grid-template-columns: 1.3fr 1fr; gap: 14px; }
    .grid-even { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .panel { padding: 14px; min-width: 0; }
    .chart { height: 390px; min-width: 0; }
    .chart.small { height: 340px; }
    .material-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
    .search-box { max-width: 420px; }
    .dms-board { display: grid; gap: 12px; }
    .dms-group { border: 1px solid var(--line); border-radius: 8px; background: #fff; overflow: hidden; }
    .dms-group-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; background: var(--panel-soft); border-bottom: 1px solid var(--line); }
    .dms-group-head h3 { margin: 0; font-size: 15px; font-weight: 950; }
    .dms-group-head span { color: var(--muted); font-size: 12px; font-weight: 850; }
    .dms-card-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; padding: 12px; }
    .dms-card { display: grid; grid-template-rows: auto 1fr; min-width: 0; border: 1px solid var(--line); border-radius: 8px; background: #fff; overflow: hidden; }
    .dms-media { position: relative; aspect-ratio: 16 / 10; background: #10241e; overflow: hidden; }
    .dms-media img, .dms-media video { width: 100%; height: 100%; object-fit: cover; display: block; background: #10241e; }
    .media-placeholder { width: 100%; height: 100%; display: grid; place-items: center; padding: 18px; color: #d8eee7; text-align: center; font-weight: 900; }
    .media-placeholder small { display: block; margin-top: 6px; color: rgba(216,238,231,.75); font-weight: 700; }
    .dms-pills { position: absolute; top: 8px; left: 8px; right: 8px; z-index: 1; display: flex; gap: 5px; flex-wrap: wrap; }
    .dms-pill { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 3px 6px; border-radius: 8px; background: rgba(255,255,255,.9); color: var(--ink); font-size: 11px; font-weight: 900; }
    .dms-body { display: grid; gap: 9px; padding: 12px; min-width: 0; align-content: start; }
    .dms-name { margin: 0; font-size: 14px; line-height: 1.28; font-weight: 950; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .dms-id { color: var(--muted); font-size: 12px; font-weight: 850; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .dms-meta-line { display: flex; flex-wrap: wrap; gap: 6px; min-width: 0; }
    .dms-meta-line span { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 3px 7px; border-radius: 6px; background: var(--panel-soft); color: var(--muted); font-size: 11px; font-weight: 900; }
    .dms-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
    .dms-field { min-width: 0; padding: 6px 7px; border-radius: 6px; background: #f7faf8; }
    .dms-field b { display: block; color: var(--muted); font-size: 11px; font-weight: 850; }
    .dms-field span { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; font-weight: 900; }
    .dms-actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .dms-actions a, .dms-actions button { border: 1px solid var(--line); border-radius: 8px; background: #fff; color: var(--accent); padding: 5px 7px; font: inherit; font-size: 12px; font-weight: 900; text-decoration: none; cursor: pointer; }
    .dms-actions a.primary-link { border-color: var(--accent); background: var(--accent); color: #fff; }
    .table-links { display: inline-flex; gap: 6px; justify-content: flex-end; }
    .table-links a { padding: 4px 7px; border: 1px solid var(--line); border-radius: 7px; color: var(--accent); text-decoration: none; font-size: 12px; font-weight: 900; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
    th { position: sticky; top: 0; z-index: 1; background: #f7faf8; color: var(--muted); font-size: 12px; font-weight: 950; }
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
    .table-wrap { max-height: 480px; overflow: auto; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    .table-primary { color: var(--ink); font-weight: 900; }
    .muted { color: var(--muted); }
    .side-nav { position: fixed; top: 128px; right: 18px; z-index: 20; width: 158px; padding: 10px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255,255,255,.92); box-shadow: var(--shadow); backdrop-filter: blur(10px); transition: width 160ms ease, padding 160ms ease; }
    .side-nav-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .side-nav strong { display: block; flex: 1; padding: 4px 6px 8px; color: var(--muted); font-size: 12px; font-weight: 900; }
    .side-nav-toggle { display: inline-grid; place-items: center; width: 28px; height: 28px; border: 1px solid var(--line); border-radius: 8px; background: #fff; color: var(--ink); cursor: pointer; font-weight: 900; line-height: 1; }
    .side-nav-links { display: grid; gap: 2px; }
    .side-nav a { display: block; padding: 8px 7px; border-radius: 6px; color: var(--ink); text-decoration: none; font-size: 12px; font-weight: 850; line-height: 1.25; }
    .side-nav a:hover { background: var(--panel-soft); color: var(--accent); }
    .side-nav.is-collapsed { width: 42px; padding: 8px 6px; }
    .side-nav.is-collapsed .side-nav-head { justify-content: center; }
    .side-nav.is-collapsed strong, .side-nav.is-collapsed .side-nav-links { display: none; }
    .side-nav.is-collapsed .side-nav-icon { transform: rotate(180deg) translateY(1px); }
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
    .empty { padding: 30px; color: var(--muted); text-align: center; font-weight: 850; }
    @media (max-width: 1320px) {
      .filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .filter-row-secondary { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .metric-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .dms-card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .side-nav { display: none; }
    }
    @media (max-width: 900px) {
      .page, .topbar-inner { width: min(100% - 28px, 1580px); }
      .topbar-inner { align-items: flex-start; flex-direction: column; }
      .grid-2, .grid-even { grid-template-columns: 1fr; }
      .metric-grid, .filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .filter-row-secondary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .dms-card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .period-card { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .period-label { grid-column: 1 / -1; }
      .dms-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 560px) {
      .metric-grid, .filter-grid, .filter-row-secondary, .dms-card-grid { grid-template-columns: 1fr; }
      .period-card { grid-template-columns: 1fr; }
      .hero { padding: 20px; }
      .chart { height: 320px; }
    }
  </style>
</head>
<body>
  <nav class="topbar">
    <div class="topbar-inner">
      <div class="brand"><span class="mark">SKT</span><span>新加坡</span></div>
      <div class="nav">
        <a href="index.html">经营总览</a>
        <a class="active" href="skt-material-analysis.html">素材分析</a>
        <a href="#summary">核心指标</a>
        <a href="#category">品类</a>
        <a href="#material-board">素材卡片</a>
        <a href="#material-table">明细</a>
        <a href="#field-contract">口径</a>
      </div>
    </div>
  </nav>

  <main class="page">
    <div class="shell">
      <section class="hero" id="overview">
        <div>
          <h1>SKT-新加坡 全品类素材分析</h1>
          <p>以站外数据源为绩效口径，按 DMS 素材编号、广告名、产品和品类拆解花费、GMV、转化、点击和素材预览。</p>
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
          <div class="period-card">
            <span class="period-label">当前周期</span>
            <input type="date" id="startDateFilter" />
            <input type="date" id="endDateFilter" />
          </div>
          <div class="period-card">
            <span class="period-label">对比周期</span>
            <input type="date" id="compareStartDateFilter" />
            <input type="date" id="compareEndDateFilter" />
          </div>
          <div class="filter-row-secondary">
            <div class="filter-group">
              <label for="categoryFilter">品类</label>
              <select id="categoryFilter"><option value="ALL">全部品类</option></select>
            </div>
            <div class="filter-group">
              <label for="productFilter">产品</label>
              <select id="productFilter"><option value="ALL">全部产品</option></select>
            </div>
            <div class="filter-group">
              <label for="typeFilter">素材类型</label>
              <select id="typeFilter"><option value="ALL">全部类型</option></select>
            </div>
            <div class="filter-group">
              <label for="statusFilter">素材范围</label>
              <select id="statusFilter">
                <option value="ACTIVE">只看有消耗</option>
                <option value="ALL">含 DMS 未消耗素材</option>
                <option value="GOOD">ROAS ≥ 3</option>
                <option value="WEAK">有花费无转化</option>
              </select>
            </div>
            <div class="filter-group">
              <button class="button secondary" id="resetFilters" type="button">重置</button>
            </div>
          </div>
        </div>
        <p class="section-note" id="periodSummary">默认到昨天，本月对比上月同期。</p>
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
          <a href="#trend">趋势</a>
          <a href="#category">品类结构</a>
          <a href="#product">产品结构</a>
          <a href="#material-board">素材卡片</a>
          <a href="#material-table">素材明细</a>
          <a href="#field-contract">字段口径</a>
        </div>
      </nav>

      <section class="metric-grid section" id="summary"></section>

      <section class="section" id="trend">
        <div class="section-head">
          <div>
            <h2>素材投放趋势</h2>
            <p class="section-note">按素材级站外花费、Purchase Value RMB 和 ROAS 判断素材承接趋势。</p>
          </div>
        </div>
        <div class="panel"><div class="chart" id="trendChart"></div></div>
      </section>

      <section class="section" id="category">
        <div class="grid-2">
          <div>
            <div class="section-head">
              <div>
                <h2>品类素材效率</h2>
                <p class="section-note">多品类素材在品类汇总中均摊，用于判断品类内容供给和效率。</p>
              </div>
            </div>
            <div class="panel"><div class="chart small" id="categoryChart"></div></div>
          </div>
          <div>
            <div class="section-head">
              <div>
                <h2>素材类型表现</h2>
                <p class="section-note">看图片、视频、GIF、轮播等内容形态的花费和转化结构。</p>
              </div>
            </div>
            <div class="panel"><div class="chart small" id="typeChart"></div></div>
          </div>
        </div>
      </section>

      <section class="section" id="product">
        <div class="section-head">
          <div>
            <h2>产品素材表现</h2>
            <p class="section-note">按产品聚合素材数、花费、GMV、订单、ROAS 和点击效率。</p>
          </div>
        </div>
        <div class="table-wrap" id="productTable"></div>
      </section>

      <section class="section" id="material-board">
        <div class="section-head">
          <div>
            <h2>DMS 素材卡片</h2>
            <p class="section-note">优先展示有消耗素材；切到“含 DMS 未消耗素材”可检查素材库沉淀。</p>
          </div>
        </div>
        <div class="panel">
          <div class="material-toolbar">
            <input class="search-box" type="search" id="materialSearch" placeholder="搜索素材编号 / 素材名 / 产品 / 广告名" />
            <button class="button secondary" id="downloadTrend" type="button">下载趋势图</button>
          </div>
          <div class="dms-board" id="dmsBoard"></div>
        </div>
      </section>

      <section class="section" id="material-table">
        <div class="section-head">
          <div>
            <h2>素材明细</h2>
            <p class="section-note">按当前周期聚合，默认按花费排序。</p>
          </div>
        </div>
        <div class="table-wrap" id="materialTable"></div>
      </section>

      <section class="section" id="field-contract">
        <div class="section-head">
          <div>
            <h2>字段口径</h2>
            <p class="section-note">页面只展示当前数据源可支撑的结论，不把 DMS 授权凭证写入公开页面。</p>
          </div>
        </div>
        <div class="table-wrap" id="fieldTable"></div>
      </section>
    </div>
  </main>

  <script>
    const PAGE_DATA = __PAYLOAD__;
    const dailyRows = PAGE_DATA.material_daily_rows || [];
    const baseMaterialRows = PAGE_DATA.material_rows || [];
    const materialMetaByKey = new Map(baseMaterialRows.map(row => [row.material_key, row]));
    const libraryRows = PAGE_DATA.library_rows || [];
    const charts = {};
    const state = {
      period: 'MTD',
      category: 'ALL',
      product: 'ALL',
      type: 'ALL',
      status: 'ACTIVE',
      search: '',
      start: '',
      end: '',
      compareStart: '',
      compareEnd: '',
    };

    const fmt0 = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
    const fmt1 = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    const fmt2 = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    const byId = id => document.getElementById(id);
    const n = value => Number(value || 0);
    const unique = values => [...new Set(values.filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), 'zh-CN'));
    const money = value => `¥${fmt0.format(n(value))}`;
    const usd = value => `$${fmt1.format(n(value))}`;
    const num = (value, digits = 0) => Number.isFinite(Number(value)) ? (digits === 0 ? fmt0.format(n(value)) : (digits === 1 ? fmt1.format(n(value)) : fmt2.format(n(value)))) : '-';
    const pct = value => Number.isFinite(Number(value)) ? `${fmt1.format(n(value) * 100)}%` : '-';
    const ratio = value => Number.isFinite(Number(value)) ? fmt2.format(n(value)) : '-';
    const compact = value => {
      const abs = Math.abs(n(value));
      if (abs >= 100000000) return `${fmt1.format(n(value) / 100000000)}亿`;
      if (abs >= 10000) return `${fmt1.format(n(value) / 10000)}万`;
      return fmt0.format(n(value));
    };
    const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    const isoTodayLocal = () => {
      const now = new Date();
      now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
      return now.toISOString().slice(0, 10);
    };
    const addDays = (dateText, days) => {
      const date = new Date(`${dateText}T00:00:00`);
      date.setDate(date.getDate() + days);
      date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
      return date.toISOString().slice(0, 10);
    };
    const addMonths = (dateText, months) => {
      const date = new Date(`${dateText}T00:00:00`);
      const day = date.getDate();
      date.setDate(1);
      date.setMonth(date.getMonth() + months);
      const last = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
      date.setDate(Math.min(day, last));
      date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
      return date.toISOString().slice(0, 10);
    };
    const dates = unique(dailyRows.map(row => row.date));
    const firstDate = dates[0] || isoTodayLocal();
    const maxDate = dates[dates.length - 1] || isoTodayLocal();
    const defaultEnd = [addDays(isoTodayLocal(), -1), maxDate].sort()[0] || maxDate;
    const clampDate = value => [firstDate, value || firstDate].sort()[1] > maxDate ? maxDate : [firstDate, value || firstDate].sort()[1];

    function initChart(id) {
      if (!charts[id]) charts[id] = echarts.init(byId(id));
      return charts[id];
    }
    function setMtd() {
      state.end = clampDate(defaultEnd);
      state.start = `${state.end.slice(0, 8)}01`;
      if (state.start < firstDate) state.start = firstDate;
      state.compareStart = addMonths(state.start, -1);
      state.compareEnd = addMonths(state.end, -1);
    }
    function applyPreset(value) {
      state.period = value;
      if (value === 'CUSTOM') return;
      if (value === 'MTD') {
        setMtd();
      } else {
        const days = Number(value);
        state.end = clampDate(defaultEnd);
        state.start = clampDate(addDays(state.end, -(days - 1)));
        state.compareEnd = addDays(state.start, -1);
        state.compareStart = addDays(state.compareEnd, -(days - 1));
      }
      syncDateInputs();
    }
    function syncDateInputs() {
      byId('startDateFilter').value = state.start;
      byId('endDateFilter').value = state.end;
      byId('compareStartDateFilter').value = state.compareStart;
      byId('compareEndDateFilter').value = state.compareEnd;
      byId('periodFilter').value = state.period;
    }
    function rowMatchesFilters(row, includeStatus = true) {
      if (state.category !== 'ALL' && !(row.categories || []).includes(state.category)) return false;
      if (state.product !== 'ALL' && row.product !== state.product) return false;
      if (state.type !== 'ALL' && row.material_type !== state.type) return false;
      if (includeStatus) {
        if (state.status === 'ACTIVE' && n(row.spend_rmb) <= 0) return false;
        if (state.status === 'GOOD' && n(row.roas) < 3) return false;
        if (state.status === 'WEAK' && !(n(row.spend_rmb) > 0 && n(row.conversions) <= 0)) return false;
      }
      if (state.search) {
        const haystack = [row.material_id, row.material_name, row.product, row.category, row.ad_name, row.campaign_name, row.post_url].join(' ').toLowerCase();
        if (!haystack.includes(state.search.toLowerCase())) return false;
      }
      return true;
    }
    function rowsInRange(start, end) {
      return dailyRows.filter(row => row.date >= start && row.date <= end && rowMatchesFilters(row));
    }
    function aggregate(rows) {
      const total = { spend: 0, spend_rmb: 0, purchase_value: 0, purchase_value_rmb: 0, impressions: 0, clicks: 0, conversions: 0, add_to_cart: 0, materials: new Set(), activeMaterials: new Set(), row_count: 0 };
      rows.forEach(row => {
        total.spend += n(row.spend);
        total.spend_rmb += n(row.spend_rmb);
        total.purchase_value += n(row.purchase_value);
        total.purchase_value_rmb += n(row.purchase_value_rmb);
        total.impressions += n(row.impressions);
        total.clicks += n(row.clicks);
        total.conversions += n(row.conversions);
        total.add_to_cart += n(row.add_to_cart);
        total.row_count += n(row.row_count);
        total.materials.add(row.material_key);
        if (n(row.spend_rmb) > 0) total.activeMaterials.add(row.material_key);
      });
      total.material_count = total.materials.size;
      total.active_material_count = total.activeMaterials.size;
      total.roas = total.spend_rmb ? total.purchase_value_rmb / total.spend_rmb : null;
      total.ctr = total.impressions ? total.clicks / total.impressions : null;
      total.cvr = total.clicks ? total.conversions / total.clicks : null;
      total.cpa_rmb = total.conversions ? total.spend_rmb / total.conversions : null;
      return total;
    }
    function delta(current, previous, formatter) {
      if (!previous) return '<span class="muted">对比期无基数</span>';
      const rate = (current - previous) / Math.abs(previous);
      const cls = rate >= 0 ? 'up' : 'down';
      return `<span class="delta ${cls}">环比 ${rate >= 0 ? '+' : ''}${pct(rate)}</span>`;
    }
    function renderSummary(currentRows, compareRows) {
      const cur = aggregate(currentRows);
      const prev = aggregate(compareRows);
      const cards = [
        ['有消耗素材', num(cur.active_material_count), delta(cur.active_material_count, prev.active_material_count)],
        ['站外花费 RMB', money(cur.spend_rmb), delta(cur.spend_rmb, prev.spend_rmb)],
        ['Purchase Value RMB', money(cur.purchase_value_rmb), delta(cur.purchase_value_rmb, prev.purchase_value_rmb)],
        ['ROAS', ratio(cur.roas), delta(cur.roas || 0, prev.roas || 0)],
        ['转化', num(cur.conversions), delta(cur.conversions, prev.conversions)],
        ['点击', num(cur.clicks), delta(cur.clicks, prev.clicks)],
        ['CTR', pct(cur.ctr), delta(cur.ctr || 0, prev.ctr || 0)],
        ['CVR', pct(cur.cvr), delta(cur.cvr || 0, prev.cvr || 0)],
      ];
      byId('summary').innerHTML = cards.map(([label, value, sub]) => `<div class="metric"><b>${label}</b><strong>${value}</strong><span>${sub}</span></div>`).join('');
      byId('periodSummary').textContent = `当前周期 ${state.start} 至 ${state.end}，对比 ${state.compareStart} 至 ${state.compareEnd}。DMS素材库 ${num(PAGE_DATA.source.dms_materials)} 条，站外命中素材 ${num(PAGE_DATA.source.active_materials)} 个。`;
    }
    function aggregateBy(rows, keyFn, shareCategories = false) {
      const map = new Map();
      rows.forEach(row => {
        const keys = shareCategories ? (row.categories || ['未归类']) : [keyFn(row)];
        const weight = shareCategories ? 1 / Math.max(keys.length, 1) : 1;
        keys.forEach(key => {
          const item = map.get(key) || { name: key, spend_rmb: 0, purchase_value_rmb: 0, conversions: 0, clicks: 0, impressions: 0, materials: new Set() };
          item.spend_rmb += n(row.spend_rmb) * weight;
          item.purchase_value_rmb += n(row.purchase_value_rmb) * weight;
          item.conversions += n(row.conversions) * weight;
          item.clicks += n(row.clicks) * weight;
          item.impressions += n(row.impressions) * weight;
          item.materials.add(row.material_key);
          map.set(key, item);
        });
      });
      return Array.from(map.values()).map(item => ({
        ...item,
        material_count: item.materials.size,
        roas: item.spend_rmb ? item.purchase_value_rmb / item.spend_rmb : null,
        ctr: item.impressions ? item.clicks / item.impressions : null,
        cvr: item.clicks ? item.conversions / item.clicks : null,
      })).sort((a, b) => b.spend_rmb - a.spend_rmb);
    }
    function renderTrend(rows) {
      const byDate = new Map();
      rows.forEach(row => {
        const item = byDate.get(row.date) || { date: row.date, spend_rmb: 0, purchase_value_rmb: 0, conversions: 0 };
        item.spend_rmb += n(row.spend_rmb);
        item.purchase_value_rmb += n(row.purchase_value_rmb);
        item.conversions += n(row.conversions);
        byDate.set(row.date, item);
      });
      const data = Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
      initChart('trendChart').setOption({
        color: ['#146b52', '#c66b3d', '#2f7ea0'],
        tooltip: { trigger: 'axis' },
        legend: { top: 0 },
        grid: { left: 70, right: 70, top: 54, bottom: 42 },
        xAxis: { type: 'category', data: data.map(row => row.date.slice(5)), axisLabel: { hideOverlap: true } },
        yAxis: [
          { type: 'value', name: 'RMB', axisLabel: { formatter: compact } },
          { type: 'value', name: 'ROAS', axisLabel: { formatter: value => fmt1.format(value) } },
        ],
        series: [
          { name: '花费RMB', type: 'bar', barMaxWidth: 26, data: data.map(row => row.spend_rmb) },
          { name: 'GMV/PV RMB', type: 'bar', barMaxWidth: 26, data: data.map(row => row.purchase_value_rmb) },
          { name: 'ROAS', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 5, data: data.map(row => row.spend_rmb ? row.purchase_value_rmb / row.spend_rmb : 0) },
        ],
      }, true);
    }
    function renderCategory(rows) {
      const data = aggregateBy(rows, row => row.category, true).slice(0, 12).reverse();
      initChart('categoryChart').setOption({
        color: ['#146b52', '#c66b3d', '#2f7ea0'],
        tooltip: { trigger: 'axis' },
        legend: { top: 0 },
        grid: { left: 92, right: 52, top: 48, bottom: 28 },
        xAxis: { type: 'value', axisLabel: { formatter: compact } },
        yAxis: { type: 'category', data: data.map(row => row.name) },
        series: [
          { name: '花费RMB', type: 'bar', data: data.map(row => row.spend_rmb) },
          { name: 'GMV/PV RMB', type: 'bar', data: data.map(row => row.purchase_value_rmb) },
          { name: 'ROAS', type: 'line', data: data.map(row => row.roas || 0) },
        ],
      }, true);
    }
    function renderType(rows) {
      const data = aggregateBy(rows, row => row.material_type).slice(0, 10);
      initChart('typeChart').setOption({
        color: ['#2f7ea0', '#146b52'],
        tooltip: { trigger: 'axis' },
        legend: { top: 0 },
        grid: { left: 68, right: 20, top: 48, bottom: 42 },
        xAxis: { type: 'category', data: data.map(row => row.name) },
        yAxis: { type: 'value', axisLabel: { formatter: compact } },
        series: [
          { name: '花费RMB', type: 'bar', barMaxWidth: 34, data: data.map(row => row.spend_rmb) },
          { name: '转化', type: 'line', smooth: true, data: data.map(row => row.conversions) },
        ],
      }, true);
    }
    function materialRowsForPeriod(rows) {
      const map = new Map();
      rows.forEach(row => {
        const meta = materialMetaByKey.get(row.material_key) || {};
        const item = map.get(row.material_key) || { ...meta, ...row, spend: 0, spend_rmb: 0, purchase_value: 0, purchase_value_rmb: 0, impressions: 0, clicks: 0, conversions: 0, add_to_cart: 0, row_count: 0 };
        ['spend', 'spend_rmb', 'purchase_value', 'purchase_value_rmb', 'impressions', 'clicks', 'conversions', 'add_to_cart', 'row_count'].forEach(key => item[key] += n(row[key]));
        map.set(row.material_key, item);
      });
      let values = Array.from(map.values()).map(row => ({
        ...row,
        roas: row.spend_rmb ? row.purchase_value_rmb / row.spend_rmb : null,
        ctr: row.impressions ? row.clicks / row.impressions : null,
        cvr: row.clicks ? row.conversions / row.clicks : null,
        cpa_rmb: row.conversions ? row.spend_rmb / row.conversions : null,
      }));
      if (state.status === 'ALL') {
        const used = new Set(values.map(row => row.material_key));
        libraryRows.forEach(row => {
          if (!used.has(row.material_key) && rowMatchesFilters(row, false)) values.push(row);
        });
      }
      values = values.filter(row => rowMatchesFilters(row));
      values.sort((a, b) => n(b.spend_rmb) - n(a.spend_rmb) || n(b.purchase_value_rmb) - n(a.purchase_value_rmb));
      return values;
    }
    function isVideoUrl(url) {
      return /\\.(mp4|webm|mov|m4v)(\\?|#|$)/i.test(String(url || ''));
    }
    function isImageUrl(url) {
      return /\\.(png|jpe?g|webp|gif)(\\?|#|$)/i.test(String(url || ''));
    }
    function materialMedia(row) {
      const url = String(row.preview_url || row.play_url || '').trim();
      const snapshotUrl = String(row.snapshot_url || '').trim();
      const alt = escapeHtml(row.material_name || row.material_id || '素材预览');
      if (snapshotUrl) return `<img class="material-preview" src="${escapeHtml(snapshotUrl)}" data-fallback-url="${escapeHtml(url)}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
      if (url && isVideoUrl(url)) return `<video class="material-preview" controls muted preload="metadata" src="${escapeHtml(url)}"></video>`;
      if (url && isImageUrl(url)) return `<img class="material-preview" src="${escapeHtml(url)}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
      if (url) return `<span class="media-placeholder"><b>已抓到素材链接</b><small>点击下方打开</small></span>`;
      return `<span class="media-placeholder"><b>${escapeHtml(row.material_type || '素材预览')}</b><small>暂无可嵌入预览，可复制编号到DMS查询</small></span>`;
    }
    function materialLinks(row) {
      const postUrl = String(row.post_url || '').trim();
      const materialUrl = String(row.preview_url || row.play_url || '').trim();
      const links = [];
      if (postUrl) links.push({ url: postUrl, label: '打开帖子', primary: true });
      if (materialUrl && materialUrl !== postUrl) links.push({ url: materialUrl, label: '打开素材', primary: false });
      return links;
    }
    function dmsCard(row) {
      const title = row.material_name || row.material_id || row.ad_name || '未命名素材';
      const actions = materialLinks(row).map(item => `<a class="${item.primary ? 'primary-link' : ''}" href="${escapeHtml(item.url)}" target="_blank" rel="noopener" title="${escapeHtml(item.url)}">${item.label}</a>`);
      if (row.material_id) actions.push(`<button type="button" class="copy-material-code" data-code="${escapeHtml(row.material_id)}">复制编号</button>`);
      return `<article class="dms-card">
        <div class="dms-media">
          <div class="dms-pills">
            <span class="dms-pill">${escapeHtml(row.material_type || '未标记')}</span>
            <span class="dms-pill">${escapeHtml(row.source || '素材')}</span>
          </div>
          ${materialMedia(row)}
        </div>
        <div class="dms-body">
          <h3 class="dms-name">${escapeHtml(title)}</h3>
          <div class="dms-id">${escapeHtml(row.material_id || row.material_key || '-')}</div>
          <div class="dms-meta-line">
            <span title="${escapeHtml(row.product || '-')}">产品：${escapeHtml(row.product || '-')}</span>
            <span title="${escapeHtml(row.category || '-')}">品类：${escapeHtml(row.category || '-')}</span>
          </div>
          <div class="dms-fields">
            <div class="dms-field"><b>花费</b><span>${money(row.spend_rmb)}</span></div>
            <div class="dms-field"><b>ROAS</b><span>${ratio(row.roas)}</span></div>
            <div class="dms-field"><b>GMV/PV</b><span>${money(row.purchase_value_rmb)}</span></div>
            <div class="dms-field"><b>转化</b><span>${num(row.conversions)}</span></div>
            <div class="dms-field"><b>CTR</b><span>${pct(row.ctr)}</span></div>
            <div class="dms-field"><b>CVR</b><span>${pct(row.cvr)}</span></div>
          </div>
          ${actions.length ? `<div class="dms-actions">${actions.join('')}</div>` : ''}
        </div>
      </article>`;
    }
    function renderDmsBoard(materialRows) {
      const groups = aggregateBy(materialRows, row => row.category).slice(0, 8);
      const html = groups.map(group => {
        const rows = materialRows.filter(row => row.category === group.name || (row.categories || []).includes(group.name)).slice(0, 8);
        return `<section class="dms-group">
          <div class="dms-group-head">
            <h3>${escapeHtml(group.name)}</h3>
            <span>${num(group.material_count)}个素材，花费${money(group.spend_rmb)}，ROAS ${ratio(group.roas)}</span>
          </div>
          <div class="dms-card-grid">${rows.map(dmsCard).join('')}</div>
        </section>`;
      }).join('');
      byId('dmsBoard').innerHTML = html || '<div class="empty">当前筛选范围暂无素材。</div>';
    }
    function tableHtml(headers, rows, formatter) {
      if (!rows.length) return '<div class="empty">当前筛选范围暂无数据。</div>';
      return `<table><thead><tr>${headers.map(header => `<th>${header}</th>`).join('')}</tr></thead><tbody>${rows.map(formatter).join('')}</tbody></table>`;
    }
    function renderProductTable(rows) {
      const data = aggregateBy(rows, row => row.product).slice(0, 80);
      byId('productTable').innerHTML = tableHtml(['产品', '素材数', '花费RMB', 'GMV/PV RMB', 'ROAS', '转化', 'CTR', 'CVR'], data, row => `
        <tr>
          <td class="table-primary">${escapeHtml(row.name)}</td>
          <td>${num(row.material_count)}</td>
          <td>${money(row.spend_rmb)}</td>
          <td>${money(row.purchase_value_rmb)}</td>
          <td>${ratio(row.roas)}</td>
          <td>${num(row.conversions)}</td>
          <td>${pct(row.ctr)}</td>
          <td>${pct(row.cvr)}</td>
        </tr>`);
    }
    function renderMaterialTable(rows) {
      byId('materialTable').innerHTML = tableHtml(['素材', '产品', '品类', '类型', '链接', '花费RMB', '花费USD', 'GMV/PV RMB', 'ROAS', '转化', '点击', 'CTR', 'CVR'], rows.slice(0, 180), row => {
        const links = materialLinks(row);
        const linkHtml = links.length
          ? `<span class="table-links">${links.map(item => `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener" title="${escapeHtml(item.url)}">${item.label.replace('打开', '')}</a>`).join('')}</span>`
          : '<span class="muted">-</span>';
        return `
        <tr>
          <td class="table-primary">${escapeHtml(row.material_id || row.material_name || row.material_key)}</td>
          <td>${escapeHtml(row.product || '-')}</td>
          <td>${escapeHtml(row.category || '-')}</td>
          <td>${escapeHtml(row.material_type || '-')}</td>
          <td>${linkHtml}</td>
          <td>${money(row.spend_rmb)}</td>
          <td>${usd(row.spend)}</td>
          <td>${money(row.purchase_value_rmb)}</td>
          <td>${ratio(row.roas)}</td>
          <td>${num(row.conversions)}</td>
          <td>${num(row.clicks)}</td>
          <td>${pct(row.ctr)}</td>
          <td>${pct(row.cvr)}</td>
        </tr>`;
      });
    }
    function renderFieldTable() {
      byId('fieldTable').innerHTML = tableHtml(['模块', '来源', '日期字段', '指标', '处理口径'], PAGE_DATA.field_map || [], row => `
        <tr>
          <td class="table-primary">${escapeHtml(row.module)}</td>
          <td>${escapeHtml(row.sheet)}</td>
          <td>${escapeHtml(row.date)}</td>
          <td>${escapeHtml(row.metric)}</td>
          <td>${escapeHtml(row.normalization)}</td>
        </tr>`);
    }
    function fillSelect(selectId, values, allLabel) {
      const select = byId(selectId);
      select.innerHTML = `<option value="ALL">${allLabel}</option>` + values.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
    }
    function setupFilters() {
      fillSelect('categoryFilter', PAGE_DATA.categories || [], '全部品类');
      fillSelect('productFilter', PAGE_DATA.products || [], '全部产品');
      fillSelect('typeFilter', PAGE_DATA.material_types || [], '全部类型');
      applyPreset('MTD');
      ['categoryFilter', 'productFilter', 'typeFilter', 'statusFilter'].forEach(id => {
        byId(id).addEventListener('change', event => {
          const key = id.replace('Filter', '');
          state[key] = event.target.value;
          renderAll();
        });
      });
      byId('periodFilter').addEventListener('change', event => {
        applyPreset(event.target.value);
        renderAll();
      });
      ['startDateFilter', 'endDateFilter', 'compareStartDateFilter', 'compareEndDateFilter'].forEach(id => {
        byId(id).addEventListener('change', () => {
          state.period = 'CUSTOM';
          state.start = byId('startDateFilter').value;
          state.end = byId('endDateFilter').value;
          state.compareStart = byId('compareStartDateFilter').value;
          state.compareEnd = byId('compareEndDateFilter').value;
          syncDateInputs();
          renderAll();
        });
      });
      byId('materialSearch').addEventListener('input', event => {
        state.search = event.target.value.trim();
        renderAll();
      });
      byId('resetFilters').addEventListener('click', () => {
        state.category = 'ALL';
        state.product = 'ALL';
        state.type = 'ALL';
        state.status = 'ACTIVE';
        state.search = '';
        ['categoryFilter', 'productFilter', 'typeFilter'].forEach(id => byId(id).value = 'ALL');
        byId('statusFilter').value = 'ACTIVE';
        byId('materialSearch').value = '';
        applyPreset('MTD');
        renderAll();
      });
      byId('downloadTrend').addEventListener('click', () => {
        const chart = charts.trendChart;
        if (!chart) return;
        const link = document.createElement('a');
        link.href = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#ffffff' });
        link.download = 'skt-material-trend.png';
        link.click();
      });
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
      try { saved = localStorage.getItem('sktMaterialSideNavCollapsed') === '1'; } catch (error) {}
      setCollapsed(saved);
      toggle.addEventListener('click', () => {
        const collapsed = !nav.classList.contains('is-collapsed');
        setCollapsed(collapsed);
        try { localStorage.setItem('sktMaterialSideNavCollapsed', collapsed ? '1' : '0'); } catch (error) {}
      });
    }
    function renderAll() {
      const currentRows = rowsInRange(state.start, state.end);
      const compareRows = rowsInRange(state.compareStart, state.compareEnd);
      const materialRows = materialRowsForPeriod(currentRows);
      renderSummary(currentRows, compareRows);
      renderTrend(currentRows);
      renderCategory(currentRows);
      renderType(currentRows);
      renderProductTable(currentRows);
      renderDmsBoard(materialRows);
      renderMaterialTable(materialRows);
      renderFieldTable();
    }
    byId('dmsBoard').addEventListener('click', async event => {
      const button = event.target.closest('.copy-material-code');
      if (!button) return;
      const code = button.dataset.code || '';
      try {
        await navigator.clipboard.writeText(code);
        button.textContent = '已复制';
        setTimeout(() => { button.textContent = '复制编号'; }, 1200);
      } catch (error) {}
    });
    byId('dmsBoard').addEventListener('error', event => {
      const media = event.target.closest('.material-preview');
      if (!media) return;
      const fallbackUrl = String(media.dataset.fallbackUrl || '').trim();
      if (fallbackUrl && !media.dataset.fallbackUsed && isImageUrl(fallbackUrl)) {
        media.dataset.fallbackUsed = '1';
        media.removeAttribute('data-fallback-url');
        media.src = fallbackUrl;
        return;
      }
      const container = media.closest('.dms-media');
      media.remove();
      if (container && !container.querySelector('.media-placeholder')) {
        container.insertAdjacentHTML('beforeend', '<span class="media-placeholder"><b>预览暂不可用</b><small>可点击下方“打开素材”查看原文件</small></span>');
      }
    }, true);
    setupFilters();
    setupSideNav();
    renderAll();
    window.addEventListener('resize', () => Object.values(charts).forEach(chart => chart.resize()));
  </script>
</body>
</html>
"""


def build_html(payload: dict[str, Any]) -> str:
    return HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def run() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    ensure_material_snapshots(payload, SITE_DIR)
    html = build_html(payload)
    output_path = OUTPUT_DIR / "skt_material_analysis.html"
    site_path = SITE_DIR / "skt-material-analysis.html"
    public_path = ROOT / "skt-material-analysis.html"
    output_path.write_text(html, encoding="utf-8")
    site_path.write_text(html, encoding="utf-8")
    try:
        public_path.write_text(html, encoding="utf-8")
    except OSError as exc:
        print(f"WARNING: public material copy was not replaced: {exc}")
    (ROOT / ".nojekyll").touch()
    return output_path


if __name__ == "__main__":
    print(run())
