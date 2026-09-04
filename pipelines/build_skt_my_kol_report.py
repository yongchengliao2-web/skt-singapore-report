from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DMS_RAW_PATH = ROOT / "data" / "dms" / "raw_allpage_my_20260903.json"
META_DAILY_PATH = ROOT / "data" / "raw" / "skt_my_meta_daily_20260801_20260901.csv"
OUTPUT_DIR = ROOT / "output"
SITE_DIR = ROOT / "site"
OUTPUT_PATH = OUTPUT_DIR / "skt_my_kol_post_analysis.html"
SITE_PATH = SITE_DIR / "skt-my-kol-post-analysis.html"
CONVENIENCE_PATH = Path(r"C:\Users\Administrator\Documents\NP 帖子\SKT-MY-帖子投放分析.html")
REPORT_START = "2026-08-01"
REPORT_END = "2026-09-01"
DMS_SOURCE_DATE = "20260903"
DMS_PAGE_URL = "https://web.cerahdms.com/dms/my/ad/#/place/material"
AI_SHEET_URL = "https://docs.google.com/spreadsheets/d/1xLFTtwoluAazJ84AW8a_9v7azxJNlSpjkvBUfHltyNw/edit?gid=1358169725#gid=1358169725"
CATEGORY_ORDER = ["面霜", "精华", "棉片", "防晒"]
DMS_STATUS_LABELS = {
    "1": "待投放",
    "2": "已投放",
    "4": "复投池",
    "5": "投放失败",
}
CANDIDATE_BLOCK_LABELS = (
    "cannot be used",
    "cannot be used for",
    "deleted",
    "code wrong",
    "无法投放",
    "已删除",
    "编码错误",
)

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
POST_RE = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
CODE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,5}-[A-Za-z0-9_-]{4,})(?![A-Za-z0-9])")
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9_-]{8,24})(?![A-Za-z0-9])")


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " / ".join(text(item) for item in value if text(item))
    if isinstance(value, dict):
        return " / ".join(f"{key}:{text(item)}" for key, item in value.items() if text(item))
    return str(value).strip()


def number(value: Any) -> float:
    if value in (None, "", "-", "null", "None"):
        return 0.0
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def integer(value: Any) -> int:
    return int(round(number(value)))


def external_url(value: Any) -> str:
    raw = text(value)
    if raw.startswith("//"):
        return "https:" + raw
    match = URL_RE.search(raw)
    return match.group(0).rstrip(".,)") if match else ""


def media_external_url(value: Any) -> str:
    raw = text(value).strip()
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/uploadfile/") or raw.startswith("/m3u8/"):
        return "https://cdn-prod.feimeidms.com" + raw
    if raw.startswith("crawler/"):
        return "https://dms-upload-prod-sg.oss-ap-southeast-1.aliyuncs.com/" + raw
    return external_url(raw)


def list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text(item) for item in value if text(item)]
    raw = text(value)
    if not raw:
        return []
    if raw.startswith("["):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, list):
            return [text(item) for item in loaded if text(item)]
    return [part.strip() for part in re.split(r"[,，;；]", raw) if part.strip()]


def normalize_key(value: Any) -> str:
    return text(value).strip().upper().rstrip(".,;:)]}")


def post_shortcode(value: Any) -> str:
    match = POST_RE.search(text(value))
    return match.group(1) if match else ""


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def empty_metrics() -> dict[str, Any]:
    return {
        "spend": 0.0,
        "gmv": 0.0,
        "impressions": 0.0,
        "clicks": 0.0,
        "orders": 0.0,
        "add_to_cart": 0.0,
        "post_engagement": 0.0,
        "video_views": 0.0,
        "row_count": 0,
        "dates": set(),
        "ad_variants": set(),
        "accounts": set(),
        "campaigns": set(),
        "match_methods": set(),
    }


def add_metric(target: dict[str, Any], row: dict[str, Any], method: str = "") -> None:
    for destination, source in (
        ("spend", "spend"),
        ("gmv", "purchase_value"),
        ("impressions", "impressions"),
        ("clicks", "inline_link_clicks"),
        ("orders", "purchase_times"),
        ("add_to_cart", "add_to_cart_times"),
        ("post_engagement", "post_engagement"),
        ("video_views", "video_play_actions"),
    ):
        target[destination] = float(target.get(destination) or 0.0) + number(row.get(source))
    target["row_count"] = int(target.get("row_count") or 0) + 1
    date_value = text(row.get("date_start"))
    if date_value:
        target["dates"].add(date_value)
    signature = "|".join(
        text(row.get(key)).strip()
        for key in ("account_name", "campaign_name", "adset_name", "ad_name")
    )
    if signature:
        target["ad_variants"].add(signature)
    account = text(row.get("account_name")).strip()
    campaign = text(row.get("campaign_name")).strip()
    if account:
        target["accounts"].add(account)
    if campaign:
        target["campaigns"].add(campaign)
    if method:
        target["match_methods"].add(method)


def finalize_metric(row: dict[str, Any]) -> dict[str, Any]:
    row["roi"] = safe_ratio(row.get("gmv", 0.0), row.get("spend", 0.0))
    row["ctr"] = safe_ratio(row.get("clicks", 0.0), row.get("impressions", 0.0))
    row["cvr"] = safe_ratio(row.get("orders", 0.0), row.get("clicks", 0.0))
    row["current_days"] = len(row.get("dates") or set())
    row["current_ad_variants"] = len(row.get("ad_variants") or set())
    row["current_accounts"] = len(row.get("accounts") or set())
    row["current_campaigns"] = len(row.get("campaigns") or set())
    row["first_date"] = min(row["dates"]) if row.get("dates") else ""
    row["last_date"] = max(row["dates"]) if row.get("dates") else ""
    return row


def category_labels(item: dict[str, Any]) -> list[str]:
    product_text = " ".join(
        text(item.get(key)) for key in ("productItemNames", "adProductNames")
    ).casefold()
    labels: list[str] = []
    if "面霜" in product_text:
        labels.append("面霜")
    if "精华" in product_text and "气垫" not in product_text and "唇部精华" not in product_text:
        labels.append("精华")
    if "棉片" in product_text or "啫喱片" in product_text:
        labels.append("棉片")
    if "防晒" in product_text:
        labels.append("防晒")
    return labels


THEME_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "效果对比 / 进阶",
        (
            "before & after",
            "before-after",
            "skin progress",
            "day progress",
            "comparison",
            "对比",
            "效果",
            "进阶",
            "变化",
        ),
    ),
    (
        "使用演示 / 护肤流程",
        (
            "application",
            "how to",
            "routine",
            "steps",
            "skincare routine",
            "使用",
            "流程",
            "步骤",
        ),
    ),
    (
        "提亮 / 水光肌",
        (
            "glow",
            "bright",
            "radiance",
            "glass skin",
            "水光",
            "提亮",
            "光泽",
            "亮白",
        ),
    ),
    (
        "修护 / 屏障",
        (
            "repair",
            "barrier",
            "healing",
            "sensitive",
            "修护",
            "屏障",
            "舒缓",
        ),
    ),
    (
        "毛孔 / 焕肤",
        (
            "pore",
            "peel",
            "exfoliat",
            "texture",
            "毛孔",
            "刷酸",
            "焕肤",
            "平滑",
        ),
    ),
    (
        "妆效 / 底妆",
        (
            "makeup",
            "cakey",
            "coverage",
            "tone up",
            "half face",
            "底妆",
            "持妆",
            "控油",
            "妆",
        ),
    ),
    (
        "场景 / 户外测试",
        (
            "outdoor",
            "wear test",
            "oil paper",
            "on-the-go",
            "train",
            "car",
            "waterproof",
            "户外",
            "吸油纸",
            "防水",
        ),
    ),
    (
        "套组 / 优惠 / 开箱",
        (
            "unboxing",
            "deal",
            "promo",
            "set",
            "paid partnership",
            "套组",
            "优惠",
            "开箱",
            "礼盒",
        ),
    ),
    (
        "专家 / 教育",
        (
            "education",
            "dermatologist",
            "expert",
            "ingredient",
            "科普",
            "专家",
            "成分",
        ),
    ),
]


def theme_info(item: dict[str, Any]) -> tuple[str, str]:
    explicit = text(item.get("themes"))
    selling = text(item.get("sellingPoint"))
    fields = " ".join(
        text(item.get(key))
        for key in (
            "themes",
            "sellingPoint",
            "contentType",
            "kolType",
            "project",
            "playName",
            "firstLevelLabels",
            "secondLevelLabels",
            "thirdLevelLabels",
            "customLabels",
            "materialName",
            "postCopy",
            "audioToText",
            "videoToText",
            "picToText",
        )
    ).casefold()
    for label, keywords in THEME_RULES:
        if any(keyword.casefold() in fields for keyword in keywords):
            return label, "DMS主题/卖点/内容字段"
    if text(item.get("contentType")):
        return text(item.get("contentType")).upper(), "DMS内容类型"
    if explicit or selling:
        return "DMS已标注（未归入主题桶）", "DMS原始字段"
    return "未标注", "DMS未标注"


def dms_quality_score(item: dict[str, Any]) -> float:
    grade_points = {"S": 35, "A": 28, "B": 18, "C": 8}
    follower_points = {"1": 22, "2": 18, "3": 14, "4": 9, "5": 5}
    grade = text(item.get("grade")).upper()
    follower_grade = text(item.get("follower_grade") or item.get("followerGrade"))
    followers = number(item.get("followers"))
    likes = number(item.get("like_count") if "like_count" in item else item.get("likeCount"))
    engagement = likes / followers if followers else 0.0
    score = float(grade_points.get(grade, 0) + follower_points.get(follower_grade, 0))
    score += min(15.0, math.log10(followers + 1) * 3.5)
    score += min(12.0, math.log10(likes + 1) * 2.5)
    score += min(10.0, engagement * 1000)
    score += 5.0 if text(item.get("themes")) else 0.0
    score += 5.0 if text(item.get("selling_point") or item.get("sellingPoint")) else 0.0
    score += 3.0 if text(item.get("content_type") or item.get("contentType")) else 0.0
    score += 2.0 if item.get("media_url") else 0.0
    score += 3.0 if item.get("post_url") else 0.0
    return round(score, 1)


def normalize_dms_item(raw: dict[str, Any]) -> dict[str, Any]:
    files = raw.get("ossFiles") or []
    files = files if isinstance(files, list) else []
    file = next(
        (
            candidate
            for candidate in files
            if media_external_url(
                candidate.get("fullUrlWithHttps")
                or candidate.get("fullUrl")
                or candidate.get("url")
            )
        ),
        files[0] if files else {},
    )
    media_url = media_external_url(
        file.get("fullUrlWithHttps")
        or file.get("fullUrl")
        or file.get("url")
        or raw.get("previewUrl")
        or raw.get("videoUrl")
    )
    play_url = media_external_url(file.get("fullPlayUrl") or file.get("playUrl"))
    post_url = external_url(raw.get("url") or raw.get("networkDiskLink"))
    if not post_url:
        post_url = external_url(raw.get("channelUrls") or raw.get("channelUrlsStr"))
    code = text(raw.get("materialCode"))
    mime = text(file.get("mimeType")).casefold()
    is_video = mime.startswith("video/") or text(raw.get("isVideo")).casefold() in {"1", "true"}
    is_image = mime.startswith("image/")
    media_kind = "video" if is_video else "image" if is_image else ""
    ig_ad_ids = list_values(raw.get("igAdIds"))
    status_code = text(raw.get("status"))
    item = {
        "material_code": code,
        "material_name": text(raw.get("materialName")) or code or "未命名帖子",
        "product_item": text(raw.get("productItemNames")),
        "ad_product": text(raw.get("adProductNames")),
        "product": text(raw.get("adProductNames")) or text(raw.get("productItemNames")) or "未标注产品",
        "material_type": "视频帖子" if is_video else "图文帖子" if is_image else "帖子",
        "media_kind": media_kind,
        "media_mime": mime,
        "post_url": post_url,
        "media_url": media_url or play_url,
        "play_url": play_url,
        "snapshot_mode": "media" if media_url or play_url else "post" if post_url else "none",
        "username": text(raw.get("username")),
        "publish_date": text(raw.get("publishDate")),
        "created_at": text(raw.get("createdAt")),
        "updated_at": text(raw.get("updatedAt")),
        "themes": text(raw.get("themes")),
        "selling_point": text(raw.get("sellingPoint")),
        "content_type": text(raw.get("contentType")),
        "kol_type": text(raw.get("kolType")),
        "grade": text(raw.get("grade")),
        "followers": integer(raw.get("followers")),
        "follower_grade": text(raw.get("followerGrade")),
        "like_count": integer(raw.get("likeCount")),
        "menu": text(raw.get("menu")),
        "project": text(raw.get("project")),
        "historical_ad_count": len(ig_ad_ids),
        "dms_ad_count": integer(raw.get("adCount")),
        "ig_ad_ids": ig_ad_ids,
        "dms_status_code": status_code,
        "dms_status": DMS_STATUS_LABELS.get(status_code, "未标注"),
        "custom_labels": text(raw.get("customLabels")),
        "copyright_flag": raw.get("copyrightFlag"),
        "copyright_expiration_time": text(raw.get("copyrightExpirationTime")),
        "last_failed_time": text(raw.get("lastFailedTime")),
        "failed_reason": text(raw.get("failedReason")),
        "ad_error": raw.get("adError"),
        "source": "DMS MY KOL素材",
    }
    item["categories"] = category_labels(raw)
    item["theme_bucket"], item["theme_source"] = theme_info(raw)
    item["quality_score"] = dms_quality_score(item)
    return item


def add_index(mapping: dict[str, list[dict[str, Any]]], key: str, item: dict[str, Any]) -> None:
    normalized = normalize_key(key)
    if normalized:
        mapping.setdefault(normalized, []).append(item)


def unique_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        code = text(item.get("material_code"))
        if code:
            result[code] = item
    return list(result.values())


def build_dms_index(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_suffix: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_shortcode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        code = normalize_key(item.get("material_code"))
        variants = {code, code.rstrip("_"), code.replace("_", "")}
        for variant in variants:
            add_index(by_code, variant, item)
        if "-" in code:
            suffix = code.split("-", 1)[1]
            for variant in {suffix, suffix.rstrip("_"), suffix.replace("_", "")}:
                if len(variant) >= 6:
                    add_index(by_suffix, variant, item)
        shortcode = post_shortcode(item.get("post_url"))
        if shortcode:
            add_index(by_shortcode, shortcode.casefold(), item)
        for value in (item.get("material_name"), item.get("product"), item.get("ad_product")):
            name = text(value).casefold()
            if name:
                add_index(by_name, name, item)
    return {
        "by_code": by_code,
        "by_suffix": by_suffix,
        "by_shortcode": by_shortcode,
        "by_name": by_name,
    }


UNKNOWN_CREATIVE_IDS = {"", "UNKNOWN", "NULL", "N/A", "NONE", "UNKNOW"}


def resolve_meta_row(row: dict[str, Any], index: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    creative_id = normalize_key(row.get("creative_id"))
    if creative_id not in UNKNOWN_CREATIVE_IDS:
        exact = unique_items(index["by_code"].get(creative_id, []))
        if len(exact) == 1:
            return exact[0], "creative_id"
        if "-" in creative_id:
            suffix = creative_id.split("-", 1)[1]
            candidates = unique_items(index["by_suffix"].get(suffix, []))
            if len(candidates) == 1:
                return candidates[0], "creative_id_suffix"

    combined = " ".join(text(row.get(key)) for key in ("ad_name", "adset_name", "campaign_name"))
    candidates: list[dict[str, Any]] = []
    for token in CODE_TOKEN_RE.findall(combined):
        normalized = normalize_key(token)
        candidates.extend(index["by_code"].get(normalized, []))
        if "-" in normalized:
            suffix = normalized.split("-", 1)[1]
            candidates.extend(index["by_suffix"].get(suffix, []))
    candidates = unique_items(candidates)
    if len(candidates) == 1:
        return candidates[0], "ad_name_code"

    shortcode_candidates: list[dict[str, Any]] = []
    for token in TOKEN_RE.findall(combined):
        shortcode_candidates.extend(index["by_shortcode"].get(token.casefold(), []))
    shortcode_candidates = unique_items(shortcode_candidates)
    if len(shortcode_candidates) == 1:
        return shortcode_candidates[0], "post_shortcode"

    ad_name = text(row.get("ad_name")).casefold()
    if ad_name:
        exact_name = unique_items(index["by_name"].get(ad_name, []))
        if len(exact_name) == 1:
            return exact_name[0], "ad_name_exact"
    return None, "unmatched"


def load_dms() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_items = json.loads(DMS_RAW_PATH.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if text(raw.get("dataFromType")) != "4":
            continue
        if not text(raw.get("materialCode")):
            continue
        items.append(normalize_dms_item(raw))
    return items, build_dms_index(items)


def load_meta_rows() -> list[dict[str, Any]]:
    with META_DAILY_PATH.open("r", encoding="gb18030", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return [
        row
        for row in rows
        if text(row.get("brand")) == "SKT"
        and text(row.get("country")) == "Malaysia"
        and text(row.get("creative_type")) == "帖子"
        and text(row.get("ad_type")) == "种草"
        and REPORT_START <= text(row.get("date_start")) <= REPORT_END
    ]


def rank_value(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    less = sum(1 for candidate in values if candidate < value)
    equal = sum(1 for candidate in values if candidate == value)
    return less + (equal + 1) / 2


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else None


def weighted_pearson(xs: list[float], ys: list[float], weights: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys) or len(xs) != len(weights):
        return None
    weight_total = sum(weights)
    if not weight_total:
        return None
    mean_x = sum(x * weight for x, weight in zip(xs, weights)) / weight_total
    mean_y = sum(y * weight for y, weight in zip(ys, weights)) / weight_total
    numerator = sum(weight * (x - mean_x) * (y - mean_y) for x, y, weight in zip(xs, ys, weights))
    denominator = math.sqrt(
        sum(weight * (x - mean_x) ** 2 for x, weight in zip(xs, weights))
        * sum(weight * (y - mean_y) ** 2 for y, weight in zip(ys, weights))
    )
    return numerator / denominator if denominator else None


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    x_ranks = [rank_value(xs, value) for value in xs]
    y_ranks = [rank_value(ys, value) for value in ys]
    return pearson(x_ranks, y_ranks)


def round_metric(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def serialize_material(item: dict[str, Any], metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = metrics or finalize_metric(empty_metrics())
    categories = list(item.get("categories") or [])
    return {
        "material_code": item.get("material_code", ""),
        "material_name": item.get("material_name", ""),
        "product": item.get("product", ""),
        "product_item": item.get("product_item", ""),
        "ad_product": item.get("ad_product", ""),
        "categories": categories,
        "material_type": item.get("material_type", ""),
        "post_url": item.get("post_url", ""),
        "media_url": item.get("media_url", ""),
        "play_url": item.get("play_url", ""),
        "username": item.get("username", ""),
        "publish_date": item.get("publish_date", ""),
        "created_at": item.get("created_at", ""),
        "themes": item.get("themes", ""),
        "selling_point": item.get("selling_point", ""),
        "content_type": item.get("content_type", ""),
        "kol_type": item.get("kol_type", ""),
        "grade": item.get("grade", ""),
        "followers": item.get("followers", 0),
        "follower_grade": item.get("follower_grade", ""),
        "like_count": item.get("like_count", 0),
        "menu": item.get("menu", ""),
        "project": item.get("project", ""),
        "historical_ad_count": item.get("historical_ad_count", 0),
        "dms_ad_count": item.get("dms_ad_count", 0),
        "dms_status_code": item.get("dms_status_code", ""),
        "dms_status": item.get("dms_status", "未标注"),
        "custom_labels": item.get("custom_labels", ""),
        "copyright_flag": item.get("copyright_flag"),
        "copyright_expiration_time": item.get("copyright_expiration_time", ""),
        "last_failed_time": item.get("last_failed_time", ""),
        "failed_reason": item.get("failed_reason", ""),
        "ad_error": item.get("ad_error"),
        "media_kind": item.get("media_kind", ""),
        "media_mime": item.get("media_mime", ""),
        "snapshot_mode": item.get("snapshot_mode", "none"),
        "theme_bucket": item.get("theme_bucket", "未标注"),
        "theme_source": item.get("theme_source", "DMS未标注"),
        "quality_score": item.get("quality_score", 0),
        "current_row_count": metrics.get("row_count", 0),
        "current_spend": round(float(metrics.get("spend", 0.0)), 4),
        "current_gmv": round(float(metrics.get("gmv", 0.0)), 4),
        "current_impressions": int(round(metrics.get("impressions", 0.0))),
        "current_clicks": int(round(metrics.get("clicks", 0.0))),
        "current_orders": round(float(metrics.get("orders", 0.0)), 4),
        "current_add_to_cart": round(float(metrics.get("add_to_cart", 0.0)), 4),
        "current_roi": round_metric(metrics.get("roi"), 4),
        "current_ctr": round_metric(metrics.get("ctr"), 8),
        "current_cvr": round_metric(metrics.get("cvr"), 8),
        "current_days": metrics.get("current_days", 0),
        "current_ad_variants": metrics.get("current_ad_variants", 0),
        "current_accounts": metrics.get("current_accounts", 0),
        "current_campaigns": metrics.get("current_campaigns", 0),
        "current_first_date": metrics.get("first_date", ""),
        "current_last_date": metrics.get("last_date", ""),
        "match_methods": sorted(metrics.get("match_methods") or []),
        "current_status": (
            "已投放"
            if metrics.get("spend", 0.0) > 0
            else "有记录无花费"
            if metrics.get("row_count", 0)
            else "当前窗口未匹配"
        ),
    }


def aggregate_group(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        group_key = text(row.get(key)) or "未标注"
        current = grouped.setdefault(
            group_key,
            {
                "name": group_key,
                "material_count": 0,
                "spend": 0.0,
                "gmv": 0.0,
                "impressions": 0.0,
                "clicks": 0.0,
                "orders": 0.0,
            },
        )
        current["material_count"] += 1
        current["spend"] += number(row.get("current_spend"))
        current["gmv"] += number(row.get("current_gmv"))
        current["impressions"] += number(row.get("current_impressions"))
        current["clicks"] += number(row.get("current_clicks"))
        current["orders"] += number(row.get("current_orders"))
    for current in grouped.values():
        current["roi"] = safe_ratio(current["gmv"], current["spend"])
        current["ctr"] = safe_ratio(current["clicks"], current["impressions"])
        current["cvr"] = safe_ratio(current["orders"], current["clicks"])
    return sorted(
        grouped.values(),
        key=lambda row: (row["gmv"], row["spend"], row["material_count"]),
        reverse=True,
    )


def add_spend_shares(rows: list[dict[str, Any]], total_spend: float) -> list[dict[str, Any]]:
    for row in rows:
        row["spend_share"] = round_metric(safe_ratio(number(row.get("spend")), total_spend), 8)
    return rows


def aggregate_total(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current: dict[str, Any] = {
        "name": "总计",
        "material_count": len(rows),
        "spend": 0.0,
        "gmv": 0.0,
        "impressions": 0.0,
        "clicks": 0.0,
        "orders": 0.0,
    }
    for row in rows:
        current["spend"] += number(row.get("current_spend"))
        current["gmv"] += number(row.get("current_gmv"))
        current["impressions"] += number(row.get("current_impressions"))
        current["clicks"] += number(row.get("current_clicks"))
        current["orders"] += number(row.get("current_orders"))
    current["roi"] = safe_ratio(current["gmv"], current["spend"])
    current["ctr"] = safe_ratio(current["clicks"], current["impressions"])
    current["cvr"] = safe_ratio(current["orders"], current["clicks"])
    current["spend_share"] = 1.0 if current["spend"] else None
    return current


THEME_INSIGHT_FIELDS = (
    "name",
    "material_count",
    "spend",
    "spend_share",
    "gmv",
    "orders",
    "impressions",
    "clicks",
    "ctr",
    "cvr",
    "roi",
)


def compact_theme_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {key: row.get(key) for key in THEME_INSIGHT_FIELDS}


def best_theme_row(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    eligible = [
        row
        for row in rows
        if number(row.get("spend")) >= 10
        and row.get(metric) is not None
        and (
            metric != "ctr"
            or number(row.get("impressions")) > 0
        )
        and (
            metric != "cvr"
            or number(row.get("clicks")) > 0
        )
    ]
    return max(
        eligible,
        key=lambda row: (
            number(row.get(metric)),
            number(row.get("gmv")),
            number(row.get("spend")),
        ),
        default={},
    )


def build_theme_insights(theme_stats: dict[str, Any]) -> dict[str, Any]:
    overall = list(theme_stats.get("overall_all") or [])
    raw = list(theme_stats.get("raw_overall_all") or [])
    spend_leader = max(
        overall,
        key=lambda row: (number(row.get("spend")), number(row.get("gmv"))),
        default={},
    )
    return {
        "spend_leader": compact_theme_row(spend_leader),
        "roi_best": compact_theme_row(best_theme_row(overall, "roi")),
        "ctr_best": compact_theme_row(best_theme_row(overall, "ctr")),
        "cvr_best": compact_theme_row(best_theme_row(overall, "cvr")),
        "raw_roi_best": compact_theme_row(best_theme_row(raw, "roi")),
        "raw_ctr_best": compact_theme_row(best_theme_row(raw, "ctr")),
    }


def candidate_is_blocked(row: dict[str, Any]) -> bool:
    labels = text(row.get("custom_labels")).casefold()
    if any(label.casefold() in labels for label in CANDIDATE_BLOCK_LABELS):
        return True
    if row.get("ad_error") in (True, 1, "1", "true", "True"):
        return True
    if text(row.get("failed_reason")):
        return True
    expiration = text(row.get("copyright_expiration_time"))[:10]
    return bool(expiration and expiration < REPORT_END)


def category_stats(
    category: str,
    library: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [row for row in library if category in (row.get("categories") or [])]
    paid = [row for row in rows if number(row.get("current_spend")) > 0]
    eligible = [
        row
        for row in paid
        if number(row.get("current_spend")) >= 10
        and row.get("current_roi") is not None
        and row.get("current_ctr") is not None
    ]
    roi_values = [number(row.get("current_roi")) for row in eligible]
    ctr_values = [number(row.get("current_ctr")) for row in eligible]
    roi_median = statistics.median(roi_values) if roi_values else None
    ctr_median = statistics.median(ctr_values) if ctr_values else None
    top = sorted(
        paid,
        key=lambda row: (
            number(row.get("current_gmv")),
            number(row.get("current_spend")),
            number(row.get("current_orders")),
        ),
        reverse=True,
    )[:10]
    efficiency = sorted(
        [row for row in eligible if number(row.get("current_orders")) > 0],
        key=lambda row: (number(row.get("current_roi")), number(row.get("current_gmv"))),
        reverse=True,
    )[:8]
    bad = [
        row
        for row in paid
        if number(row.get("current_spend")) >= 100
        and (
            number(row.get("current_days")) >= 3
            or number(row.get("current_ad_variants")) >= 2
        )
        and roi_median is not None
        and ctr_median is not None
        and row.get("current_roi") is not None
        and row.get("current_ctr") is not None
        and number(row.get("current_roi")) < roi_median
        and number(row.get("current_ctr")) < ctr_median
    ]
    bad = sorted(
        bad,
        key=lambda row: (number(row.get("current_spend")), number(row.get("current_gmv"))),
        reverse=True,
    )[:10]
    candidate_pool = [
        row
        for row in rows
        if row.get("dms_status_code") == "1"
        and number(row.get("historical_ad_count")) == 0
        and number(row.get("current_spend")) <= 0
        and not candidate_is_blocked(row)
    ]
    candidates = sorted(
        candidate_pool,
        key=lambda row: (
            number(row.get("quality_score")),
            number(row.get("followers")),
            number(row.get("like_count")),
        ),
        reverse=True,
    )[:10]
    return {
        "category": category,
        "dms_posts": len(rows),
        "matched_materials": sum(1 for row in rows if number(row.get("current_row_count")) > 0),
        "paid_materials": len(paid),
        "no_current_materials": sum(1 for row in rows if number(row.get("current_row_count")) == 0),
        "no_delivery_materials": sum(1 for row in rows if number(row.get("current_spend")) <= 0),
        "pending_materials": sum(1 for row in rows if row.get("dms_status_code") == "1"),
        "never_run_materials": sum(1 for row in rows if number(row.get("historical_ad_count")) == 0),
        "spend": round(sum(number(row.get("current_spend")) for row in rows), 4),
        "gmv": round(sum(number(row.get("current_gmv")) for row in rows), 4),
        "impressions": int(round(sum(number(row.get("current_impressions")) for row in rows))),
        "clicks": int(round(sum(number(row.get("current_clicks")) for row in rows))),
        "orders": round(sum(number(row.get("current_orders")) for row in rows), 4),
        "roi": safe_ratio(
            sum(number(row.get("current_gmv")) for row in rows),
            sum(number(row.get("current_spend")) for row in rows),
        ),
        "ctr": safe_ratio(
            sum(number(row.get("current_clicks")) for row in rows),
            sum(number(row.get("current_impressions")) for row in rows),
        ),
        "roi_median": roi_median,
        "ctr_median": ctr_median,
        "top10": top,
        "efficiency_top": efficiency,
        "bad_repeat": bad,
        "test_candidates": candidates,
    }


def build_quadrants(material_rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        row
        for row in material_rows
        if number(row.get("current_spend")) >= 10
        and number(row.get("current_impressions")) > 0
        and row.get("current_roi") is not None
        and row.get("current_ctr") is not None
    ]
    ctr_values = [number(row.get("current_ctr")) for row in eligible]
    roi_values = [number(row.get("current_roi")) for row in eligible]
    ctr_median = statistics.median(ctr_values) if ctr_values else None
    roi_median = statistics.median(roi_values) if roi_values else None
    groups: dict[str, list[dict[str, Any]]] = {
        "高CTR / 高ROI": [],
        "高CTR / 低ROI": [],
        "低CTR / 高ROI": [],
        "低CTR / 低ROI": [],
    }
    for row in eligible:
        high_ctr = number(row.get("current_ctr")) >= (ctr_median or 0)
        high_roi = number(row.get("current_roi")) >= (roi_median or 0)
        if high_ctr and high_roi:
            group = "高CTR / 高ROI"
        elif high_ctr:
            group = "高CTR / 低ROI"
        elif high_roi:
            group = "低CTR / 高ROI"
        else:
            group = "低CTR / 低ROI"
        groups[group].append(row)
    summaries = []
    for name, rows in groups.items():
        summaries.append(
            {
                "name": name,
                "count": len(rows),
                "spend": round(sum(number(row.get("current_spend")) for row in rows), 4),
                "gmv": round(sum(number(row.get("current_gmv")) for row in rows), 4),
                "roi": safe_ratio(
                    sum(number(row.get("current_gmv")) for row in rows),
                    sum(number(row.get("current_spend")) for row in rows),
                ),
                "examples": sorted(
                    rows,
                    key=lambda row: (
                        number(row.get("current_spend")),
                        number(row.get("current_gmv")),
                    ),
                    reverse=True,
                )[:5],
            }
        )
    xs = [number(row.get("current_ctr")) for row in eligible]
    ys = [number(row.get("current_roi")) for row in eligible]
    weights = [number(row.get("current_spend")) for row in eligible]
    return {
        "eligible_count": len(eligible),
        "ctr_median": ctr_median,
        "roi_median": roi_median,
        "pearson": pearson(xs, ys),
        "spearman": spearman(xs, ys),
        "weighted_pearson": weighted_pearson(xs, ys, weights),
        "summaries": summaries,
        "scatter": [
            {
                "code": row.get("material_code", ""),
                "product": row.get("product", ""),
                "category": " / ".join(row.get("categories") or []),
                "ctr": row.get("current_ctr"),
                "roi": row.get("current_roi"),
                "spend": row.get("current_spend"),
                "gmv": row.get("current_gmv"),
                "post_url": row.get("post_url", ""),
            }
            for row in eligible
        ],
    }


def build_daily_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, float]] = defaultdict(
        lambda: {"spend": 0.0, "gmv": 0.0, "impressions": 0.0, "clicks": 0.0, "orders": 0.0}
    )
    for row in rows:
        day = text(row.get("date_start"))
        if not day:
            continue
        grouped[day]["spend"] += number(row.get("spend"))
        grouped[day]["gmv"] += number(row.get("purchase_value"))
        grouped[day]["impressions"] += number(row.get("impressions"))
        grouped[day]["clicks"] += number(row.get("inline_link_clicks"))
        grouped[day]["orders"] += number(row.get("purchase_times"))
    result = []
    for day in sorted(grouped):
        current = grouped[day]
        current["roi"] = safe_ratio(current["gmv"], current["spend"])
        current["ctr"] = safe_ratio(current["clicks"], current["impressions"])
        result.append({"date": day, **{key: round_metric(value, 8) for key, value in current.items()}})
    return result


def build_theme_stats(library: list[dict[str, Any]]) -> dict[str, Any]:
    paid = [row for row in library if number(row.get("current_spend")) > 0]
    total_spend = sum(number(row.get("current_spend")) for row in paid)
    all_theme = add_spend_shares(aggregate_group(paid, "theme_bucket"), total_spend)
    raw_theme = add_spend_shares(aggregate_group(paid, "themes"), total_spend)
    by_category = {}
    for category in CATEGORY_ORDER:
        category_rows = [
            row for row in paid if category in (row.get("categories") or [])
        ]
        category_spend = sum(number(row.get("current_spend")) for row in category_rows)
        by_category[category] = aggregate_group(
            category_rows,
            "theme_bucket",
        )
        by_category[category] = add_spend_shares(by_category[category], category_spend)[:12]
    selling_rows = [
        row
        for row in paid
        if text(row.get("selling_point"))
    ]
    selling_stats = add_spend_shares(
        aggregate_group(selling_rows, "selling_point"), total_spend
    )[:15]
    content_stats = add_spend_shares(
        aggregate_group(paid, "content_type"), total_spend
    )
    return {
        "overall": all_theme[:15],
        "overall_all": all_theme,
        "raw_overall": raw_theme[:30],
        "raw_overall_all": raw_theme,
        "total": aggregate_total(paid),
        "by_category": by_category,
        "selling_points": selling_stats,
        "content_types": content_stats,
    }


def compact_unmatched(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = text(row.get("ad_name")) or text(row.get("creative_id")) or "未命名广告"
        current = grouped.setdefault(
            key,
            {
                "ad_name": key,
                "creative_id": text(row.get("creative_id")),
                "rows": 0,
                "spend": 0.0,
                "gmv": 0.0,
            },
        )
        current["rows"] += 1
        current["spend"] += number(row.get("spend"))
        current["gmv"] += number(row.get("purchase_value"))
    return sorted(
        grouped.values(),
        key=lambda row: (row["spend"], row["gmv"], row["rows"]),
        reverse=True,
    )[:15]


def build_payload() -> dict[str, Any]:
    dms_items, dms_index = load_dms()
    meta_rows = load_meta_rows()
    metric_by_code: dict[str, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    match_counts: Counter[str] = Counter()
    for row in meta_rows:
        item, method = resolve_meta_row(row, dms_index)
        if item is None:
            unmatched.append(row)
            match_counts["unmatched"] += 1
            continue
        code = text(item.get("material_code"))
        metrics = metric_by_code.setdefault(code, empty_metrics())
        add_metric(metrics, row, method)
        match_counts[method] += 1
    for metrics in metric_by_code.values():
        finalize_metric(metrics)

    library: list[dict[str, Any]] = []
    for item in dms_items:
        metrics = metric_by_code.get(text(item.get("material_code")))
        if metrics is None:
            metrics = finalize_metric(empty_metrics())
        library.append(serialize_material(item, metrics))
    target_library = [
        row for row in library if any(category in CATEGORY_ORDER for category in row.get("categories") or [])
    ]
    linked_library = [row for row in target_library if number(row.get("current_row_count")) > 0]
    category_data = {
        category: category_stats(category, target_library)
        for category in CATEGORY_ORDER
    }

    total = empty_metrics()
    linked_total = empty_metrics()
    for row in meta_rows:
        add_metric(total, row)
    for row in linked_library:
        linked_total["spend"] += number(row.get("current_spend"))
        linked_total["gmv"] += number(row.get("current_gmv"))
        linked_total["impressions"] += number(row.get("current_impressions"))
        linked_total["clicks"] += number(row.get("current_clicks"))
        linked_total["orders"] += number(row.get("current_orders"))
        linked_total["row_count"] += number(row.get("current_row_count"))
    finalize_metric(total)
    finalize_metric(linked_total)

    category_summary = []
    for category in CATEGORY_ORDER:
        current = category_data[category]
        category_summary.append(
            {
                key: value
                for key, value in current.items()
                if key not in {"top10", "efficiency_top", "bad_repeat", "test_candidates"}
            }
        )

    quadrants = build_quadrants(linked_library)
    theme_stats = build_theme_stats(target_library)
    theme_insights = build_theme_insights(theme_stats)
    daily_series = build_daily_series(meta_rows)
    all_filter_spend = sum(number(row.get("spend")) for row in meta_rows)
    all_filter_gmv = sum(number(row.get("purchase_value")) for row in meta_rows)
    linked_spend = sum(number(row.get("current_spend")) for row in linked_library)
    linked_gmv = sum(number(row.get("current_gmv")) for row in linked_library)
    payload = {
        "report": {
            "brand": "SKT",
            "country": "Malaysia",
            "date_start": REPORT_START,
            "date_end": REPORT_END,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dms_source_date": DMS_SOURCE_DATE,
            "dms_page_url": DMS_PAGE_URL,
            "ai_sheet_url": AI_SHEET_URL,
            "fr_reference_note": "仅参考 FR 报告的结构与分析方法；本页数据、素材和结论均为 SKT Malaysia。",
            "currency": "MYR",
            "filters": {
                "brand": "SKT",
                "country": "Malaysia",
                "creative_type": "帖子",
                "ad_type": "种草",
                "pic_filter": "全部投放人（DMS 页面未设置投放人筛选）",
            },
        },
        "overview": {
            "dms_kol_posts": len(dms_items),
            "target_dms_posts": len(target_library),
            "filtered_meta_rows": len(meta_rows),
            "filtered_meta_ads_estimate": len(
                {
                    "|".join(
                        text(row.get(key)).strip()
                        for key in ("account_name", "campaign_name", "adset_name", "ad_name")
                    )
                    for row in meta_rows
                }
            ),
            "resolved_meta_rows": len(meta_rows) - len(unmatched),
            "linked_materials": len(metric_by_code),
            "linked_target_materials": len(linked_library),
            "unmatched_rows": len(unmatched),
            "all_filter_spend": round(all_filter_spend, 4),
            "all_filter_gmv": round(all_filter_gmv, 4),
            "all_filter_roi": safe_ratio(all_filter_gmv, all_filter_spend),
            "all_filter_impressions": int(round(sum(number(row.get("impressions")) for row in meta_rows))),
            "all_filter_clicks": int(round(sum(number(row.get("inline_link_clicks")) for row in meta_rows))),
            "all_filter_orders": round(sum(number(row.get("purchase_times")) for row in meta_rows), 4),
            "all_filter_ctr": safe_ratio(
                sum(number(row.get("inline_link_clicks")) for row in meta_rows),
                sum(number(row.get("impressions")) for row in meta_rows),
            ),
            "linked_spend": round(linked_spend, 4),
            "linked_gmv": round(linked_gmv, 4),
            "linked_roi": safe_ratio(linked_gmv, linked_spend),
            "linked_spend_coverage": safe_ratio(linked_spend, all_filter_spend),
            "linked_gmv_coverage": safe_ratio(linked_gmv, all_filter_gmv),
            "dms_category_posts": {
                category: sum(1 for row in target_library if category in (row.get("categories") or []))
                for category in CATEGORY_ORDER
            },
            "target_no_delivery_materials": sum(
                1 for row in target_library if number(row.get("current_spend")) <= 0
            ),
            "target_pending_materials": sum(
                1 for row in target_library if row.get("dms_status_code") == "1"
            ),
            "target_never_run_materials": sum(
                1 for row in target_library if number(row.get("historical_ad_count")) == 0
            ),
            "match_counts": dict(match_counts),
        },
        "category_summary": category_summary,
        "categories": category_data,
        "quadrants": quadrants,
        "theme_stats": theme_stats,
        "theme_insights": theme_insights,
        "daily_series": daily_series,
        "library": target_library,
        "unmatched_top": compact_unmatched(unmatched),
        "unmatched_meta": {
            "rows": len(unmatched),
            "spend": round(sum(number(row.get("spend")) for row in unmatched), 4),
            "gmv": round(sum(number(row.get("purchase_value")) for row in unmatched), 4),
        },
        "data_quality": {
            "dms_total_rows": len(dms_items),
            "dms_kol_rows_have_post_url": sum(1 for row in dms_items if row.get("post_url")),
            "dms_kol_rows_have_media_url": sum(1 for row in dms_items if row.get("media_url")),
            "dms_target_rows": len(target_library),
            "ai_sheet_status": "在线核验（2026-09-03）：用户链接 gid=1358169725 导出为「站外素材」5,381 行，其中 MY-帖子编号 0 条、面霜/精华/棉片/防晒目标行 0 条；其 IMPORTRANGE 源表 All_data 共 65,832 行，窗口内 Malaysia+帖子 817 行，但四品类目标行 0 条；「站外广告数据」工作表返回 #ERROR!/#REF!/#N/A。因此它不能参与本次效果排名，排名改用正式 SKT Malaysia Meta 日表。",
            "ranking_rule": "四品类 TOP 10 按当前窗口已匹配帖子素材的 GMV 降序；效率榜另按花费不少于 10 MYR 的 ROI 降序。",
            "candidate_rule": "DMS status=待投放、历史广告关联为 0 且当前窗口无花费的帖子；排除 DMS 明确标记为不可用/已删除、广告错误、失败原因或版权已过期的素材，再按评级、达人量级、粉丝、点赞、主题/卖点标注等信号排序，仅称优先测试候选，不代表已验证效果。",
            "risk_rule": "花费不少于 100 MYR、至少 3 个投放日或 2 个广告变体，且 ROI 与 CTR 同时低于该品类花费不少于 10 MYR素材的中位数。",
            "category_rule": "只依据 DMS adProductNames/productItemNames；精华排除唇部精华与含气垫的精华命名，组合素材可同时进入多个品类。",
            "ctr_rule": "CTR = inline_link_clicks / impressions；ROI = purchase_value / spend；CTR-ROI关系使用素材聚合后的分子分母，不平均日行指标。",
        },
    }
    return payload


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>SKT MY 帖子投放闭环分析</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <script src="https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js"></script>
  <style>
    :root {
      --bg: #f5f4ee;
      --surface: #fffefa;
      --surface-soft: #f0f3ed;
      --surface-warm: #fbf1e7;
      --ink: #1e2928;
      --muted: #66716e;
      --line: #dce3dc;
      --accent: #c85f43;
      --accent-dark: #8f3f30;
      --teal: #167a76;
      --teal-soft: #e1f0ec;
      --gold: #bb852d;
      --gold-soft: #fbf0d9;
      --bad: #a74238;
      --bad-soft: #fae7e2;
      --shadow: 0 10px 24px rgba(36, 53, 46, .07);
      --radius: 8px;
      font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body { margin: 0; color: var(--ink); background: var(--bg); line-height: 1.52; letter-spacing: 0; }
    a { color: inherit; }
    button, input, select { font: inherit; }
    button { cursor: pointer; }
    .topbar { position: sticky; top: 0; z-index: 50; border-bottom: 1px solid var(--line); background: rgba(245, 244, 238, .95); backdrop-filter: blur(14px); }
    .topbar-inner { width: min(1500px, calc(100% - 52px)); min-height: 62px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 18px; }
    .brand { display: inline-flex; align-items: center; gap: 10px; color: var(--ink); text-decoration: none; font-weight: 950; white-space: nowrap; }
    .brand-mark { display: inline-grid; place-items: center; width: 38px; height: 38px; border-radius: 8px; background: var(--ink); color: #fff; font-size: 13px; letter-spacing: 0; }
    .brand-sub { color: var(--muted); font-size: 12px; font-weight: 850; }
    .topbar-right { display: flex; align-items: center; justify-content: flex-end; gap: 12px; min-width: 0; }
    .nav { display: flex; gap: 3px; flex-wrap: wrap; justify-content: flex-end; }
    .nav a { padding: 7px 8px; border-radius: 6px; color: var(--muted); text-decoration: none; font-size: 12px; font-weight: 850; }
    .nav a:hover, .nav a.active { background: var(--surface); color: var(--accent-dark); }
    .icon-text { display: inline-flex; align-items: center; gap: 6px; }
    .icon-text svg, .icon-button svg, .close-button svg { width: 15px; height: 15px; stroke-width: 2.2; }
    .page { width: min(1500px, calc(100% - 52px)); margin: 0 auto; padding: 20px 0 56px; }
    .hero { display: grid; grid-template-columns: 1.25fr .75fr; gap: 22px; align-items: center; min-height: 224px; padding: 30px 34px; border-radius: var(--radius); background: #203a37; color: #fff; box-shadow: var(--shadow); }
    .eyebrow { color: #f2c9a7; font-size: 12px; font-weight: 950; letter-spacing: .04em; }
    h1 { margin: 8px 0 0; font-size: 42px; line-height: 1.08; letter-spacing: 0; font-weight: 950; }
    .hero-copy { max-width: 780px; margin: 13px 0 0; color: rgba(255,255,255,.82); font-size: 14px; font-weight: 700; }
    .hero-meta { display: grid; gap: 8px; justify-items: end; align-content: center; }
    .scope-pill { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 7px 10px; border: 1px solid rgba(255,255,255,.18); border-radius: 6px; color: #f6f6ee; background: rgba(255,255,255,.08); font-size: 12px; font-weight: 850; text-align: right; }
    .scope-pill svg { width: 15px; height: 15px; color: #f2c9a7; }
    .section { margin-top: 28px; scroll-margin-top: 80px; }
    .section-head { display: flex; align-items: end; justify-content: space-between; gap: 18px; margin-bottom: 12px; }
    .section-head h2 { margin: 0; font-size: 23px; line-height: 1.2; font-weight: 950; }
    .section-head p { max-width: 850px; margin: 5px 0 0; color: var(--muted); font-size: 13px; font-weight: 700; }
    .section-index { color: var(--accent); font-size: 12px; font-weight: 950; }
    .metric-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }
    .metric { min-width: 0; padding: 15px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); box-shadow: var(--shadow); }
    .metric-label { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 12px; font-weight: 850; }
    .metric-label svg { width: 14px; height: 14px; color: var(--accent); }
    .metric-value { display: block; margin-top: 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 27px; line-height: 1; font-weight: 950; }
    .metric-note { display: block; margin-top: 8px; color: var(--muted); font-size: 11px; font-weight: 750; }
    .notice { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: start; margin-top: 12px; padding: 14px 16px; border: 1px solid #ead6b1; border-radius: var(--radius); background: var(--gold-soft); color: #654d22; }
    .notice > svg { width: 20px; height: 20px; margin-top: 1px; color: var(--gold); }
    .notice strong { display: block; font-size: 13px; font-weight: 950; }
    .notice p { margin: 4px 0 0; font-size: 12px; font-weight: 700; }
    .notice a { color: var(--accent-dark); font-weight: 900; }
    .notice-action { align-self: center; padding: 7px 10px; border: 1px solid #d4b77b; border-radius: 6px; background: transparent; color: #654d22; text-decoration: none; font-size: 12px; font-weight: 900; white-space: nowrap; }
    .readout-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    .readout { min-width: 0; min-height: 148px; padding: 16px; border-top: 3px solid var(--accent); background: var(--surface); box-shadow: var(--shadow); }
    .readout:nth-child(2) { border-top-color: var(--teal); }
    .readout:nth-child(3) { border-top-color: var(--gold); }
    .readout:nth-child(4) { border-top-color: var(--bad); }
    .readout h3 { margin: 0; font-size: 15px; font-weight: 950; }
    .readout p { margin: 9px 0 0; color: var(--muted); font-size: 13px; font-weight: 700; }
    .theme-table-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .theme-panel { min-width: 0; padding: 18px; border: 1px solid #d5e2d9; border-radius: var(--radius); background: #edf4ef; }
    .theme-panel h3 { margin: 0; color: #214b3f; font-size: 20px; line-height: 1.2; font-weight: 950; }
    .theme-panel p { margin: 5px 0 12px; color: #5f766c; font-size: 12px; font-weight: 750; }
    .theme-scroll { max-height: 510px; overflow: auto; border: 1px solid #d5e2d9; border-radius: var(--radius); background: var(--surface); }
    .theme-table { min-width: 980px; font-size: 12px; }
    .theme-table th { background: #e5efe8; color: #214b3f; }
    .theme-table td, .theme-table th { padding: 10px 9px; }
    .theme-table td:first-child, .theme-table th:first-child { min-width: 190px; text-align: left; white-space: normal; }
    .theme-table tbody tr:hover { background: #f5faf6; }
    .theme-table tfoot td { background: #eaf2ec; color: #214b3f; font-weight: 950; }
    .theme-conclusion { margin-top: 14px; padding: 18px 20px; border: 1px solid #d5e2d9; border-radius: var(--radius); background: #edf4ef; }
    .theme-conclusion h3 { margin: 0 0 8px; color: #214b3f; font-size: 20px; font-weight: 950; }
    .theme-conclusion ul { margin: 0; padding-left: 21px; color: #315c4e; }
    .theme-conclusion li { margin: 6px 0; padding-left: 3px; font-size: 13px; font-weight: 700; }
    .theme-conclusion strong { color: #214b3f; font-weight: 950; }
    .grid-2 { display: grid; grid-template-columns: 1.15fr .85fr; gap: 14px; }
    .grid-even { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
    .panel { min-width: 0; padding: 16px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); box-shadow: var(--shadow); }
    .panel-head { display: flex; align-items: start; justify-content: space-between; gap: 14px; margin-bottom: 11px; }
    .panel-head h3 { margin: 0; font-size: 16px; font-weight: 950; }
    .panel-head p { margin: 3px 0 0; color: var(--muted); font-size: 12px; font-weight: 700; }
    .chart { width: 100%; height: 360px; }
    .chart.tall { height: 430px; }
    .chart.small { height: 300px; }
    .table-wrap { width: 100%; overflow: auto; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); }
    table { width: 100%; min-width: 780px; border-collapse: collapse; font-size: 12px; }
    .benchmark-table { min-width: 1100px; }
    .benchmark-table td { min-width: 170px; white-space: normal; text-align: left; vertical-align: top; }
    .benchmark-table th { text-align: left; }
    th, td { padding: 10px 9px; border-bottom: 1px solid var(--line); text-align: right; vertical-align: middle; white-space: nowrap; }
    th { position: sticky; top: 0; z-index: 2; background: #eef1eb; color: var(--muted); font-size: 11px; font-weight: 950; }
    th:first-child, td:first-child, th:nth-child(3), td:nth-child(3), th:nth-child(4), td:nth-child(4) { text-align: left; }
    tr:last-child td { border-bottom: 0; }
    .table-main { color: var(--ink); font-weight: 950; }
    .table-sub { display: block; max-width: 290px; overflow: hidden; text-overflow: ellipsis; color: var(--muted); font-size: 11px; font-weight: 700; }
    .category-tag, .status-tag, .signal-tag { display: inline-flex; align-items: center; padding: 3px 6px; border-radius: 5px; font-size: 10px; font-weight: 950; }
    .category-tag { margin: 2px 3px 2px 0; background: var(--teal-soft); color: var(--teal); }
    .status-tag.good { background: var(--teal-soft); color: var(--teal); }
    .status-tag.neutral { background: #eef0ee; color: var(--muted); }
    .status-tag.bad { background: var(--bad-soft); color: var(--bad); }
    .signal-tag { background: var(--surface-warm); color: var(--accent-dark); }
    .positive { color: var(--teal); font-weight: 950; }
    .negative { color: var(--bad); font-weight: 950; }
    .muted { color: var(--muted); }
    .thumb { position: relative; display: block; width: 66px; height: 86px; padding: 0; overflow: hidden; border: 1px solid var(--line); border-radius: 6px; background: #dfe7e1; }
    .thumb video, .thumb img { width: 100%; height: 100%; display: block; object-fit: cover; background: #253837; }
    .thumb-badge { position: absolute; right: 4px; bottom: 4px; padding: 2px 4px; border-radius: 4px; color: #fff; background: rgba(25, 48, 45, .82); font-size: 9px; font-weight: 900; }
    .thumb-empty { display: grid; place-items: center; width: 66px; height: 86px; border: 1px dashed var(--line); border-radius: 6px; color: var(--muted); font-size: 10px; font-weight: 850; text-align: center; }
    .link-row { display: inline-flex; align-items: center; justify-content: flex-end; gap: 5px; }
    .link-button, .copy-button, .icon-button { display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-height: 27px; padding: 4px 7px; border: 1px solid var(--line); border-radius: 5px; background: var(--surface); color: var(--accent-dark); text-decoration: none; font-size: 11px; font-weight: 900; }
    .link-button.primary { border-color: var(--accent); background: var(--accent); color: #fff; }
    .copy-button, .icon-button { cursor: pointer; }
    .icon-button { width: 28px; padding: 0; }
    .tabs { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 10px; }
    .tab { display: inline-flex; align-items: center; gap: 5px; min-height: 32px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: var(--muted); font-size: 12px; font-weight: 900; }
    .tab:hover, .tab.active { border-color: var(--accent); background: var(--surface-warm); color: var(--accent-dark); }
    .toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 9px; margin-bottom: 10px; }
    .toolbar-left, .toolbar-right { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
    .select, .search { min-height: 34px; padding: 6px 9px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: var(--ink); font-size: 12px; font-weight: 800; }
    .search { width: 280px; }
    .button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 34px; padding: 6px 10px; border: 1px solid var(--accent); border-radius: 6px; background: var(--accent); color: #fff; font-size: 12px; font-weight: 950; }
    .button.secondary { border-color: var(--line); background: var(--surface); color: var(--ink); }
    .button svg { width: 15px; height: 15px; }
    .quadrant-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
    .quadrant { min-width: 0; padding: 12px; border-left: 3px solid var(--teal); background: var(--surface-soft); }
    .quadrant:nth-child(2) { border-left-color: var(--gold); }
    .quadrant:nth-child(3) { border-left-color: var(--accent); }
    .quadrant:nth-child(4) { border-left-color: var(--bad); }
    .quadrant h4 { margin: 0; font-size: 13px; font-weight: 950; }
    .quadrant strong { display: block; margin-top: 8px; font-size: 20px; font-weight: 950; }
    .quadrant p { margin: 5px 0 0; color: var(--muted); font-size: 11px; font-weight: 750; }
    .stat-strip { display: flex; flex-wrap: wrap; gap: 8px; color: var(--muted); font-size: 12px; font-weight: 800; }
    .stat-strip b { color: var(--ink); }
    .empty { padding: 24px; color: var(--muted); text-align: center; font-size: 13px; font-weight: 800; }
    .method-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .method { min-width: 0; padding: 14px; border: 1px solid var(--line); background: var(--surface-soft); }
    .method h3 { margin: 0; font-size: 14px; font-weight: 950; }
    .method p { margin: 7px 0 0; color: var(--muted); font-size: 12px; font-weight: 700; }
    .footer { margin-top: 38px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: 11px; font-weight: 750; }
    .modal { position: fixed; inset: 0; z-index: 100; display: none; place-items: center; padding: 20px; background: rgba(24, 38, 35, .72); }
    .modal.open { display: grid; }
    .modal-box { position: relative; width: min(720px, 100%); max-height: calc(100vh - 40px); overflow: auto; padding: 16px; border-radius: var(--radius); background: var(--surface); box-shadow: 0 24px 60px rgba(0,0,0,.2); }
    .close-button { position: absolute; top: 10px; right: 10px; display: inline-grid; place-items: center; width: 32px; height: 32px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); color: var(--ink); }
    .modal-media-shell { width: min(420px, 100%); max-height: 64vh; min-height: 120px; margin: 0 auto; display: grid; place-items: center; border-radius: 6px; background: #253837; overflow: hidden; }
    .modal-media { max-width: 100%; max-height: 64vh; display: block; border-radius: 6px; background: #253837; }
    .modal-title { margin: 14px 42px 0 0; font-size: 18px; font-weight: 950; }
    .modal-meta { margin-top: 6px; color: var(--muted); font-size: 12px; font-weight: 750; }
    .modal-links { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
    @media (max-width: 1250px) {
      .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .readout-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .hero { grid-template-columns: 1fr; }
      .hero-meta { justify-items: start; }
      .scope-pill { text-align: left; }
    }
    @media (max-width: 900px) {
      .topbar-inner, .page { width: min(100% - 28px, 1500px); }
      .topbar-inner { align-items: flex-start; flex-direction: column; padding: 9px 0; }
      .topbar-right { width: 100%; justify-content: space-between; align-items: flex-start; }
      .nav { justify-content: flex-start; }
      .grid-2, .grid-even, .method-grid { grid-template-columns: 1fr; }
      .theme-table-grid { grid-template-columns: 1fr; }
      .quadrant-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .chart.tall { height: 360px; }
    }
    @media (max-width: 600px) {
      .page { padding-top: 12px; }
      .hero { padding: 22px; min-height: 0; }
      h1 { font-size: 31px; }
      .metric-grid, .readout-grid { grid-template-columns: 1fr 1fr; }
      .metric { padding: 12px; }
      .metric-value { font-size: 22px; }
      .section-head { display: block; }
      .search { width: 100%; }
      .toolbar-left, .toolbar-right { width: 100%; }
      .select { flex: 1; min-width: 0; }
      .quadrant-grid { grid-template-columns: 1fr; }
      .notice { grid-template-columns: auto 1fr; }
      .notice-action { grid-column: 2; justify-self: start; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="#top"><span class="brand-mark">SKT</span><span>MY 帖子投放分析</span><span class="brand-sub">KOL素材闭环</span></a>
      <div class="topbar-right">
        <nav class="nav">
          <a class="active" href="#overview">总览</a>
          <a href="#ctr-roi">CTR × ROI</a>
          <a href="#opportunities">优先测试</a>
          <a href="#risks">重复低效</a>
          <a href="#top10">TOP 10</a>
          <a href="#domestic">国内借鉴</a>
          <a href="#lineage">口径</a>
        </nav>
        <button class="button secondary" id="exportCsv"><i data-lucide="download"></i>导出素材 CSV</button>
      </div>
    </div>
  </header>
  <main class="page" id="top">
    <section class="hero">
      <div>
        <div class="eyebrow">SKT · MALAYSIA · DMS KOL POSTS</div>
        <h1>帖子投放闭环<br>从素材到放大动作</h1>
        <p class="hero-copy">把 DMS 的原帖、达人信号和媒体快照，与 Meta 日表的花费、链接点击和购买价值配对。当前页只分析帖子与种草，不把收割素材混进排名。</p>
      </div>
      <div class="hero-meta">
        <div class="scope-pill"><i data-lucide="calendar-days"></i><span id="heroDate"></span></div>
        <div class="scope-pill"><i data-lucide="filter"></i><span>帖子 · 种草 · 全部投放人</span></div>
        <div class="scope-pill"><i data-lucide="database"></i><span id="heroSource"></span></div>
      </div>
    </section>

    <section class="section" id="overview">
      <div class="section-head">
        <div><div class="section-index">01 / 现况</div><h2>先看盘子：哪些被投，哪些还没有被验证</h2><p>Meta 总览保留全部匹配范围；品类排名只使用 DMS 精确关联的帖子素材。组合素材按产品进入多个品类，所以品类花费不可相加。</p></div>
      </div>
      <div class="metric-grid" id="metrics"></div>
      <div class="notice" id="aiNotice">
        <i data-lucide="triangle-alert"></i>
        <div><strong>AI 表已在线核验，但当前不能作为四品类效果源</strong><p>用户链接的 gid 实际导出「站外素材」5,381 行，其中 MY-帖子编号 0 条、四个目标品类 0 条；其 IMPORTRANGE 源表 All_data 在窗口内只有 817 条 Malaysia 帖子，四品类目标行仍为 0 条；「站外广告数据」工作表还返回 #ERROR!、#REF!、#N/A。因此效果排名改用 SKT Malaysia 正式 Meta 清洗日表，DMS 负责原帖、快照和素材元数据。</p></div>
        <a class="notice-action" href="https://docs.google.com/spreadsheets/d/1xLFTtwoluAazJ84AW8a_9v7azxJNlSpjkvBUfHltyNw/edit?gid=1358169725#gid=1358169725" target="_blank" rel="noopener noreferrer">打开 AI 表</a>
      </div>
      <div class="grid-2" style="margin-top:14px">
        <div class="panel"><div class="panel-head"><div><h3>四品类投放对比</h3><p>当前窗口已关联帖子；GMV / 花费为 Meta 原始 MYR</p></div></div><div class="table-wrap"><table id="categoryTable"></table></div></div>
        <div class="panel"><div class="panel-head"><div><h3>窗口节奏</h3><p>全部帖子 + 种草日表聚合</p></div></div><div class="chart" id="trendChart"></div></div>
      </div>
      <div class="grid-even" style="margin-top:14px">
        <div class="panel"><div class="panel-head"><div><h3>品类花费与 GMV</h3><p>双轴展示，避免规模差掩盖效率</p></div></div><div class="chart small" id="categoryChart"></div></div>
        <div class="panel"><div class="panel-head"><div><h3>读法</h3><p>本报告把“效果”和“测试价值”分开</p></div></div><div class="method-grid" style="grid-template-columns:1fr 1fr"><div class="method"><h3>已投放</h3><p>看 GMV、ROI、CTR、投放日和广告变体，回答“已经证明什么”。</p></div><div class="method"><h3>未验证</h3><p>看 DMS 评级、达人量级、互动和主题信号，回答“下一批先测什么”。</p></div></div></div>
      </div>
    </section>

    <section class="section" id="readout">
      <div class="section-head"><div><div class="section-index">02 / 主题表现</div><h2>主题表现与结论</h2><p>用现有已投帖子数据同时看归类主题与 DMS 原始主题；花费、GMV、订单、展示和点击均为 Meta 日表聚合，币种为 MYR。</p></div></div>
      <div class="theme-table-grid">
        <div class="theme-panel"><h3>主题表现表</h3><p>归类主题 · 按 GMV 降序 · 花费占比按当前已投帖子合计计算</p><div class="theme-scroll"><table class="theme-table" id="themePerformanceTable"></table></div></div>
        <div class="theme-panel"><h3>原表主题表现表</h3><p>DMS 原始主题字段 · 空值显示为 unclassified · 保留原始命名</p><div class="theme-scroll"><table class="theme-table" id="rawThemePerformanceTable"></table></div></div>
      </div>
      <div class="theme-conclusion"><h3>结论</h3><ul id="themeConclusion"></ul></div>
    </section>

    <section class="section" id="ctr-roi">
      <div class="section-head"><div><div class="section-index">03 / 关系</div><h2>CTR 高，不等于 ROI 高</h2><p>用素材聚合后的链接点击率和购买价值回报做散点，分界线取花费不少于 10 MYR素材的中位数。</p></div></div>
      <div class="grid-2">
        <div class="panel"><div class="panel-head"><div><h3>素材级 CTR × ROI</h3><p id="corrNote"></p></div></div><div class="chart tall" id="scatterChart"></div></div>
        <div class="panel"><div class="panel-head"><div><h3>四象限怎么用</h3><p>优先级按“证明强度”与“放量风险”判断</p></div></div><div class="quadrant-grid" id="quadrantGrid"></div><div class="table-wrap" style="margin-top:12px"><table id="quadrantTable"></table></div></div>
      </div>
    </section>

    <section class="section" id="opportunities">
      <div class="section-head"><div><div class="section-index">04 / 增量</div><h2>好但没跑过：先做测试候选池</h2><p>以下素材来自 DMS「待投放」且没有历史广告关联，当前窗口也没有实际花费。它们是 DMS 信号较强的优先测试候选，不等于已经验证有效。</p></div></div>
      <div class="panel"><div class="tabs" id="opportunityTabs"></div><div class="stat-strip" id="opportunityNote"></div><div class="table-wrap" style="margin-top:10px"><table id="opportunityTable"></table></div></div>
    </section>

    <section class="section" id="risks">
      <div class="section-head"><div><div class="section-index">05 / 止损</div><h2>坏素材一直跑：把预算从重复低效里拿回来</h2><p>名单要求花费、投放持续性和 CTR / ROI 双低同时成立。它不是单日偶发波动，而是应进入停投、换钩子或换人群复盘的对象。</p></div></div>
      <div class="panel"><div class="tabs" id="riskTabs"></div><div class="stat-strip" id="riskNote"></div><div class="table-wrap" style="margin-top:10px"><table id="riskTable"></table></div></div>
    </section>

    <section class="section" id="top10">
      <div class="section-head"><div><div class="section-index">06 / 放大</div><h2>四品类 TOP 10 帖子素材</h2><p>主榜按当前窗口 GMV 排名，适合回答“已经贡献规模的内容”。每条都保留 DMS 记录中的原帖 URL；有存档文件时另附 DMS 媒体快照直链。</p></div></div>
      <div class="panel"><div class="tabs" id="topTabs"></div><div class="stat-strip" id="topNote"></div><div class="table-wrap" style="margin-top:10px"><table id="topTable"></table></div></div>
      <div class="grid-even" style="margin-top:14px"><div class="panel"><div class="panel-head"><div><h3>效率榜</h3><p>花费不少于 10 MYR 且有订单，防止小样本 ROI 误导</p></div></div><div class="table-wrap"><table id="efficiencyTable"></table></div></div><div class="panel"><div class="panel-head"><div><h3>主题与卖点</h3><p id="categoryThemeNote"></p></div></div><div class="table-wrap"><table id="categoryThemeTable"></table></div></div></div>
    </section>

    <section class="section" id="content">
      <div class="section-head"><div><div class="section-index">07 / 内容</div><h2>什么主题值得复制，什么信息还缺</h2><p>主题优先使用 DMS 原始主题；缺失时才用卖点、内容类型等字段归桶，未标注不会被强行解释。</p></div></div>
      <div class="grid-2">
        <div class="panel"><div class="panel-head"><div><h3>主题 GMV 贡献</h3><p>目标品类的已投帖子素材</p></div></div><div class="chart" id="themeChart"></div></div>
        <div class="panel"><div class="panel-head"><div><h3>全局主题表现</h3><p>不同主题的规模和效率</p></div></div><div class="table-wrap"><table id="themeTable"></table></div></div>
      </div>
      <div class="grid-even" style="margin-top:14px">
        <div class="panel"><div class="panel-head"><div><h3>卖点原始字段</h3><p>只展示 DMS 已填写的卖点</p></div></div><div class="table-wrap"><table id="sellingTable"></table></div></div>
        <div class="panel"><div class="panel-head"><div><h3>内容类型</h3><p>UGC / PGC / 未标注</p></div></div><div class="table-wrap"><table id="contentTable"></table></div></div>
      </div>
    </section>

    <section class="section" id="domestic">
      <div class="section-head"><div><div class="section-index">08 / 国内借鉴</div><h2>把国内内容结构翻译成 SKT 测试动作</h2><p>这里只借鉴品类内容的表达结构和货架承接方式，不导入 FR、NP 或其他品牌的素材、数字和文案；下方“当前主题”仍来自 SKT DMS。</p></div></div>
      <div class="panel"><div class="table-wrap"><table id="domesticTable"></table></div></div>
    </section>

    <section class="section" id="library">
      <div class="section-head"><div><div class="section-index">09 / 素材库</div><h2>按 DMS 编号回看</h2><p>计算使用全部目标品类 DMS 帖子；这里提供可搜索的轻量明细，默认按当前花费与 DMS 信号排序。</p></div></div>
      <div class="panel"><div class="toolbar"><div class="toolbar-left"><select class="select" id="libraryCategory"><option value="全部">全部目标品类</option></select><select class="select" id="libraryDmsStatus"><option value="全部">DMS全部状态</option><option value="待投放">待投放</option><option value="已投放">已投放</option><option value="投放失败">投放失败</option><option value="复投池">复投池</option></select><select class="select" id="libraryStatus"><option value="全部">窗口全部状态</option><option value="已投放">已投放</option><option value="有记录无花费">有记录无花费</option><option value="当前窗口未匹配">当前窗口未匹配</option></select><input class="search" id="librarySearch" placeholder="搜索编号、产品、达人、主题"></div><div class="toolbar-right"><button class="button secondary" id="resetLibrary"><i data-lucide="rotate-ccw"></i>重置</button></div></div><div class="stat-strip" id="libraryNote"></div><div class="table-wrap" style="margin-top:10px"><table id="libraryTable"></table></div></div>
    </section>

    <section class="section" id="lineage">
      <div class="section-head"><div><div class="section-index">10 / 口径</div><h2>数据链路、边界和下一步</h2><p>所有数字都可以回到本地快照或原始链接；未匹配部分明确留在缺口里。</p></div></div>
      <div class="method-grid">
        <div class="method"><h3>DMS</h3><p id="dmsLineage"></p></div>
        <div class="method"><h3>Meta</h3><p id="metaLineage"></p></div>
        <div class="method"><h3>AI 表</h3><p id="aiLineage"></p></div>
      </div>
      <div class="panel" style="margin-top:10px"><div class="panel-head"><div><h3>未匹配 Meta 帖子记录</h3><p>这些行计入 Meta 总览，但没有 DMS KOL记录，不能进入带快照的素材排名。</p></div><span class="signal-tag" id="unmatchedBadge"></span></div><div class="table-wrap"><table id="unmatchedTable"></table></div></div>
    </section>

    <footer class="footer"><span id="footerText"></span>　参考 FR 仅用于结构与分析方法；本页不含 FR、NP 或其他品牌素材。</footer>
  </main>
  <div class="modal" id="mediaModal" aria-hidden="true">
    <div class="modal-box">
      <button class="close-button" id="closeModal" title="关闭"><i data-lucide="x"></i></button>
      <div class="modal-media-shell" id="modalMedia"></div>
      <div class="modal-title" id="modalTitle"></div>
      <div class="modal-meta" id="modalMeta"></div>
      <div class="modal-links" id="modalLinks"></div>
    </div>
  </div>
  <script>
    const REPORT = __REPORT_DATA__;
    const CATEGORIES = ["面霜", "精华", "棉片", "防晒"];
    const DOMESTIC_BENCHMARK = {
      "面霜": ["问题先行：干燥、敏感、暗沉；再用半脸或周期记录给结果", "效果对比 / 进阶、修护 / 屏障、套组 / 优惠", "前三秒先给肤况问题，再展示质地、涂抹和周期证据；把气候与肤质放进字幕", "Shopee首图与标题同步问题词、适用肤质和套组优惠", "CTR看问题钩子，CVR与ROI看证据和货架承接"],
      "精华": ["把成分名词翻译成可见结果：提亮、光泽、平滑，再接早晚流程", "提亮 / 水光肌、使用演示 / 护肤流程、效果对比 / 进阶", "一条帖子只打一个结果，近景展示质地与吸收，补早晚顺序和搭配边界", "首图突出功效结果与使用顺序，详情页补成分、肤质和组合逻辑", "CTR看结果表达，CVR与ROI看功效信任和组合承接"],
      "棉片": ["一片一侧的擦拭演示，把粗糙、毛孔和便利性变成可见动作", "妆效 / 底妆、毛孔 / 焕肤、使用演示 / 护肤流程", "先展示擦拭前后和棉片触感，再讲频次、适用肤质与刺激边界", "首图展示使用步骤和片数，标题补频次、肤感与适用场景", "CTR看动作钩子，CVR与ROI看效果可信度和使用门槛"],
      "防晒": ["通勤、出汗、户外和妆前是入口，同时回答泛白、黏腻、搓泥", "场景 / 户外测试、妆效 / 底妆、提亮 / 水光肌", "用半脸、户外或吸油纸实测建立证据，再补复涂和妆前搭配", "首图与详情页同时交代肤感、妆前兼容和户外场景", "CTR看场景冲突，CVR与ROI看肤感证据和商品页承接"],
    };
    const state = { topCategory: "面霜", opportunityCategory: "面霜", riskCategory: "面霜", libraryCategory: "全部", libraryStatus: "全部", libraryDmsStatus: "全部", librarySearch: "" };
    const categoryColors = { "面霜": "#c85f43", "精华": "#167a76", "棉片": "#bb852d", "防晒": "#6b6e9e" };
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
    const n = (value) => Number(value || 0);
    const money = (value, decimals = 0) => "MYR " + n(value).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    const money2 = (value) => value == null ? "—" : "MYR " + n(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const integerFormat = (value) => n(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
    const pct = (value, decimals = 2) => value == null ? "—" : (n(value) * 100).toFixed(decimals) + "%";
    const ratio = (value) => value == null ? "—" : n(value).toFixed(2) + "x";
    const shortText = (value, max = 42) => { const raw = String(value || ""); return raw.length > max ? raw.slice(0, max) + "…" : raw; };
    const icons = () => { if (window.lucide && typeof window.lucide.createIcons === "function") window.lucide.createIcons(); };
    const tags = (row) => (row.categories || []).map((item) => '<span class="category-tag">' + esc(item) + '</span>').join("");
    const statusTag = (status) => '<span class="status-tag ' + (status === "已投放" ? "good" : (status === "当前窗口未匹配" || status === "有记录无花费") ? "neutral" : "bad") + '">' + esc(status) + '</span>';
    const dmsStatusTag = (status) => '<span class="status-tag ' + (status === "已投放" ? "good" : status === "投放失败" ? "bad" : "neutral") + '">' + esc(status || "未标注") + '</span>';
    const signal = (value) => value ? '<span class="signal-tag">' + esc(value) + '</span>' : '<span class="muted">未标注</span>';

    function linkButtons(row) {
      const links = [];
      if (row.post_url) links.push('<a class="link-button primary" href="' + esc(row.post_url) + '" target="_blank" rel="noopener noreferrer" title="' + esc(row.post_url) + '"><i data-lucide="external-link"></i>Instagram原帖</a>');
      if (row.media_url) {
        const label = row.media_kind === "image" ? "DMS图片快照" : "DMS视频快照";
        const icon = row.media_kind === "image" ? "image" : "play-square";
        links.push('<a class="link-button" href="' + esc(row.media_url) + '" target="_blank" rel="noopener noreferrer" title="' + esc(row.media_url) + '"><i data-lucide="' + icon + '"></i>' + label + '</a>');
      }
      if (row.material_code) links.push('<button class="icon-button" data-copy="' + esc(row.material_code) + '" title="复制DMS编号"><i data-lucide="copy"></i></button>');
      return '<span class="link-row">' + links.join("") + '</span>';
    }

    function thumb(row) {
      if (!row.media_url) {
        if (row.post_url) return '<a class="thumb-empty" href="' + esc(row.post_url) + '" target="_blank" rel="noopener noreferrer" title="打开Instagram原帖"><i data-lucide="external-link"></i>原帖</a>';
        return '<span class="thumb-empty"><i data-lucide="image-off"></i>无链接</span>';
      }
      const media = row.media_kind === "image"
        ? '<img src="' + esc(row.media_url) + '" loading="lazy" alt="DMS图片快照">'
        : '<video src="' + esc(row.media_url) + '" muted playsinline preload="metadata"></video>';
      return '<button class="thumb" data-media-code="' + esc(row.material_code) + '" title="打开DMS快照">' + media + '<span class="thumb-badge">DMS</span></button>';
    }

    function materialNameCell(row) {
      const meta = [row.material_code, row.username ? "@" + row.username : "", row.grade ? "评级 " + row.grade : ""].filter(Boolean).join(" · ");
      return '<div class="table-main">' + esc(shortText(row.product || row.material_name, 38)) + '</div><span class="table-sub">' + esc(meta || row.material_name || "未命名帖子") + '</span>' + tags(row);
    }

    function renderMetrics() {
      const o = REPORT.overview;
      const items = [
        ["DMS MY KOL帖子", integerFormat(o.dms_kol_posts), "全部 15,161 条帖子素材", "database"],
        ["目标品类DMS帖", integerFormat(o.target_dms_posts), "面霜 / 精华 / 棉片 / 防晒", "layers-3"],
        ["Meta帖子素材", integerFormat(o.linked_materials), "精确关联到 DMS 的素材", "link-2"],
        ["窗口花费", money(o.all_filter_spend), "全部帖子 + 种草", "wallet"],
        ["窗口GMV", money(o.all_filter_gmv), "Purchase Value", "shopping-bag"],
        ["ROI / CTR", ratio(o.all_filter_roi) + " · " + pct(o.all_filter_ctr), "Meta 聚合口径", "activity"],
      ];
      $("metrics").innerHTML = items.map((item) => '<div class="metric"><div class="metric-label"><i data-lucide="' + item[3] + '"></i>' + item[0] + '</div><strong class="metric-value">' + item[1] + '</strong><span class="metric-note">' + item[2] + '</span></div>').join("");
      $("heroDate").textContent = REPORT.report.date_start + " — " + REPORT.report.date_end;
      $("heroSource").textContent = integerFormat(o.dms_kol_posts) + " DMS帖子 / " + integerFormat(o.filtered_meta_rows) + " Meta日行";
    }

    function renderCategoryTable() {
      const rows = REPORT.category_summary;
      $("categoryTable").innerHTML = '<thead><tr><th>品类</th><th>DMS帖子</th><th>已投素材</th><th>当前花费</th><th>当前GMV</th><th>ROI</th><th>CTR</th><th>窗口无花费</th></tr></thead><tbody>' + rows.map((row) => '<tr><td class="table-main">' + esc(row.category) + '</td><td>' + integerFormat(row.dms_posts) + '</td><td>' + integerFormat(row.paid_materials) + '</td><td>' + money(row.spend) + '</td><td>' + money(row.gmv) + '</td><td>' + ratio(row.roi) + '</td><td>' + pct(row.ctr) + '</td><td>' + integerFormat(row.no_delivery_materials) + '</td></tr>').join("") + '</tbody>';
    }

    const themeDisplayName = (row, raw) => {
      if (row.name === "总计") return row.name;
      if (row.name === "未标注") return raw ? "unclassified" : "未识别主题";
      return row.name || (raw ? "unclassified" : "未识别主题");
    };
    function themeTableMarkup(rows, total, raw) {
      const headers = ["维度", "帖子数", "花费", "花费占比", "GMV", "订单数", "展示", "点击", "CTR", "CVR", "ROI"];
      const head = '<thead><tr>' + headers.map((item) => '<th>' + item + '</th>').join("") + '</tr></thead>';
      const body = (rows || []).map((row) => '<tr><td class="table-main">' + esc(themeDisplayName(row, raw)) + '</td><td>' + integerFormat(row.material_count) + '</td><td>' + money2(row.spend) + '</td><td>' + pct(row.spend_share) + '</td><td>' + money2(row.gmv) + '</td><td>' + integerFormat(row.orders) + '</td><td>' + integerFormat(row.impressions) + '</td><td>' + integerFormat(row.clicks) + '</td><td>' + pct(row.ctr) + '</td><td>' + pct(row.cvr) + '</td><td>' + ratio(row.roi) + '</td></tr>').join("");
      const footer = total ? '<tfoot><tr><td class="table-main">' + esc(themeDisplayName(total, raw)) + '</td><td>' + integerFormat(total.material_count) + '</td><td>' + money2(total.spend) + '</td><td>' + pct(total.spend_share) + '</td><td>' + money2(total.gmv) + '</td><td>' + integerFormat(total.orders) + '</td><td>' + integerFormat(total.impressions) + '</td><td>' + integerFormat(total.clicks) + '</td><td>' + pct(total.ctr) + '</td><td>' + pct(total.cvr) + '</td><td>' + ratio(total.roi) + '</td></tr></tfoot>' : '';
      return head + '<tbody>' + (body || '<tr><td colspan="11" class="empty">暂无主题数据。</td></tr>') + '</tbody>' + footer;
    }
    function renderThemeTables() {
      const stats = REPORT.theme_stats || {};
      $("themePerformanceTable").innerHTML = themeTableMarkup(stats.overall_all || stats.overall, stats.total, false);
      $("rawThemePerformanceTable").innerHTML = themeTableMarkup(stats.raw_overall || stats.raw_overall_all, stats.total, true);
      const insights = REPORT.theme_insights || {};
      const bullets = [];
      const spend = insights.spend_leader;
      const roi = insights.roi_best;
      const ctr = insights.ctr_best;
      const cvr = insights.cvr_best;
      const rawRoi = insights.raw_roi_best;
      const rawCtr = insights.raw_ctr_best;
      if (spend && spend.name) bullets.push('<li><strong>投放集中：</strong>' + esc(themeDisplayName(spend, false)) + ' 拿到最高花费 ' + money2(spend.spend) + '，占 ' + pct(spend.spend_share) + '。</li>');
      if (roi && roi.name) bullets.push('<li><strong>ROI 最优：</strong>' + esc(themeDisplayName(roi, false)) + ' ROI ' + ratio(roi.roi) + '，花费 ' + money2(roi.spend) + '，是当前更有效的 GMV 承接主题。</li>');
      if (ctr && ctr.name) bullets.push('<li><strong>CTR 最优：</strong>' + esc(themeDisplayName(ctr, false)) + ' CTR ' + pct(ctr.ctr) + '，优先复盘它的首帧、问题钩子和场景表达。</li>');
      if (cvr && cvr.name) bullets.push('<li><strong>CVR 最优：</strong>' + esc(themeDisplayName(cvr, false)) + ' CVR ' + pct(cvr.cvr) + '，在订单承接效率上更强。</li>');
      if (rawRoi && rawRoi.name) bullets.push('<li><strong>原表主题 ROI 高点：</strong>' + esc(themeDisplayName(rawRoi, true)) + ' ROI ' + ratio(rawRoi.roi) + '，花费 ' + money2(rawRoi.spend) + '；先确认样本量，再决定是否复制。</li>');
      if (rawCtr && rawCtr.name) bullets.push('<li><strong>原表主题 CTR 高点：</strong>' + esc(themeDisplayName(rawCtr, true)) + ' CTR ' + pct(rawCtr.ctr) + '，可作为下一轮点击型素材的参考模板。</li>');
      $("themeConclusion").innerHTML = bullets.length ? bullets.join("") : '<li>当前窗口暂无足够的主题表现数据。</li>';
    }

    function renderTabs(containerId, stateKey) {
      const container = $(containerId);
      container.innerHTML = CATEGORIES.map((category) => '<button class="tab ' + (state[stateKey] === category ? "active" : "") + '" data-tab-key="' + stateKey + '" data-tab-category="' + category + '">' + esc(category) + '</button>').join("");
    }

    function renderOpportunity() {
      renderTabs("opportunityTabs", "opportunityCategory");
      const category = state.opportunityCategory;
      const data = REPORT.categories[category];
      $("opportunityNote").innerHTML = '<span><b>' + esc(category) + '</b> DMS帖子 ' + integerFormat(data.dms_posts) + '</span><span>DMS待投放 <b>' + integerFormat(data.pending_materials) + '</b></span><span>从未有历史广告 <b>' + integerFormat(data.never_run_materials) + '</b></span><span>下表显示优先测试 10 条</span>';
      const rows = data.test_candidates;
      $("opportunityTable").innerHTML = '<thead><tr><th>#</th><th>快照</th><th>素材 / 产品</th><th>DMS信号</th><th>主题 / 卖点</th><th>历史广告关联</th><th>DMS状态 / 本窗口</th><th>链接</th></tr></thead><tbody>' + (rows.length ? rows.map((row, index) => '<tr><td>' + (index + 1) + '</td><td>' + thumb(row) + '</td><td>' + materialNameCell(row) + '</td><td>' + signal("信号分 " + row.quality_score) + '<span class="table-sub">粉丝 ' + integerFormat(row.followers) + ' · 点赞 ' + integerFormat(row.like_count) + '</span><span class="table-sub">评级 ' + esc(row.grade || "—") + ' · 达人量级 ' + esc(row.follower_grade || "—") + '</span></td><td>' + signal(row.theme_bucket) + '<span class="table-sub">' + esc(shortText(row.themes || row.selling_point || "DMS未标注", 46)) + '</span></td><td>' + integerFormat(row.historical_ad_count) + '</td><td>' + dmsStatusTag(row.dms_status) + '<span class="table-sub">' + esc(row.current_status) + '</span></td><td>' + linkButtons(row) + '</td></tr>').join("") : '<tr><td colspan="8" class="empty">当前品类没有可展示的候选。</td></tr>') + '</tbody>';
      icons();
    }

    function renderRisk() {
      renderTabs("riskTabs", "riskCategory");
      const category = state.riskCategory;
      const data = REPORT.categories[category];
      $("riskNote").innerHTML = '<span><b>' + esc(category) + '</b> ROI中位数 ' + ratio(data.roi_median) + '</span><span>CTR中位数 ' + pct(data.ctr_median) + '</span><span>满足双低 + 持续投放条件 ' + integerFormat(data.bad_repeat.length) + ' 条</span>';
      const rows = data.bad_repeat;
      $("riskTable").innerHTML = '<thead><tr><th>#</th><th>快照</th><th>素材 / 产品</th><th>花费</th><th>GMV</th><th>ROI</th><th>CTR</th><th>持续性</th><th>判断</th><th>链接</th></tr></thead><tbody>' + (rows.length ? rows.map((row, index) => '<tr><td>' + (index + 1) + '</td><td>' + thumb(row) + '</td><td>' + materialNameCell(row) + '</td><td>' + money(row.current_spend) + '</td><td>' + money(row.current_gmv) + '</td><td class="negative">' + ratio(row.current_roi) + '</td><td class="negative">' + pct(row.current_ctr) + '</td><td>' + integerFormat(row.current_days) + '日 / ' + integerFormat(row.current_ad_variants) + '变体</td><td>' + signal("双低") + '<span class="table-sub">建议停投或换钩子复测</span></td><td>' + linkButtons(row) + '</td></tr>').join("") : '<tr><td colspan="10" class="empty">当前品类没有满足阈值的重复低效素材。</td></tr>') + '</tbody>';
      icons();
    }

    function renderTop() {
      renderTabs("topTabs", "topCategory");
      const category = state.topCategory;
      const data = REPORT.categories[category];
      $("topNote").innerHTML = '<span><b>' + esc(category) + '</b> 已投素材 ' + integerFormat(data.paid_materials) + '</span><span>花费 ' + money(data.spend) + '</span><span>GMV ' + money(data.gmv) + '</span><span>主榜按 GMV 降序</span>';
      const rows = data.top10;
      $("topTable").innerHTML = '<thead><tr><th>#</th><th>快照</th><th>素材 / 产品</th><th>主题</th><th>花费</th><th>GMV</th><th>ROI</th><th>CTR</th><th>订单</th><th>投放日 / 广告</th><th>链接</th></tr></thead><tbody>' + (rows.length ? rows.map((row, index) => '<tr><td>' + (index + 1) + '</td><td>' + thumb(row) + '</td><td>' + materialNameCell(row) + '</td><td>' + signal(row.theme_bucket) + '<span class="table-sub">' + esc(shortText(row.themes || row.selling_point || "DMS未标注", 42)) + '</span></td><td>' + money(row.current_spend) + '</td><td class="positive">' + money(row.current_gmv) + '</td><td>' + ratio(row.current_roi) + '</td><td>' + pct(row.current_ctr) + '</td><td>' + integerFormat(row.current_orders) + '</td><td>' + integerFormat(row.current_days) + '日 / ' + integerFormat(row.current_ad_variants) + '</td><td>' + linkButtons(row) + '</td></tr>').join("") : '<tr><td colspan="11" class="empty">当前品类没有已投放素材。</td></tr>') + '</tbody>';
      const efficiency = data.efficiency_top;
      $("efficiencyTable").innerHTML = '<thead><tr><th>素材</th><th>花费</th><th>GMV</th><th>ROI</th><th>CTR</th><th>链接</th></tr></thead><tbody>' + (efficiency.length ? efficiency.map((row) => '<tr><td>' + materialNameCell(row) + '</td><td>' + money(row.current_spend) + '</td><td>' + money(row.current_gmv) + '</td><td class="positive">' + ratio(row.current_roi) + '</td><td>' + pct(row.current_ctr) + '</td><td>' + linkButtons(row) + '</td></tr>').join("") : '<tr><td colspan="6" class="empty">暂无达到效率榜样本门槛的素材。</td></tr>') + '</tbody>';
      const themes = (REPORT.theme_stats.by_category[category] || []).slice(0, 10);
      $("categoryThemeNote").textContent = category + "已投素材的 DMS主题归桶";
      $("categoryThemeTable").innerHTML = '<thead><tr><th>主题</th><th>素材数</th><th>花费</th><th>GMV</th><th>ROI</th></tr></thead><tbody>' + (themes.length ? themes.map((row) => '<tr><td class="table-main">' + esc(row.name) + '</td><td>' + integerFormat(row.material_count) + '</td><td>' + money(row.spend) + '</td><td>' + money(row.gmv) + '</td><td>' + ratio(row.roi) + '</td></tr>').join("") : '<tr><td colspan="5" class="empty">当前品类主题字段不足。</td></tr>') + '</tbody>';
      icons();
    }

    function renderQuadrants() {
      const q = REPORT.quadrants;
      $("corrNote").textContent = "样本 " + integerFormat(q.eligible_count) + " 条 · Pearson " + (q.pearson == null ? "—" : q.pearson.toFixed(2)) + " · Spearman " + (q.spearman == null ? "—" : q.spearman.toFixed(2)) + " · 花费加权 " + (q.weighted_pearson == null ? "—" : q.weighted_pearson.toFixed(2));
      $("quadrantGrid").innerHTML = q.summaries.map((row) => '<div class="quadrant"><h4>' + esc(row.name) + '</h4><strong>' + integerFormat(row.count) + ' 条</strong><p>花费 ' + money(row.spend) + '<br>GMV ' + money(row.gmv) + ' · ROI ' + ratio(row.roi) + '</p></div>').join("");
      $("quadrantTable").innerHTML = '<thead><tr><th>象限</th><th>样本</th><th>优先动作</th><th>代表素材</th></tr></thead><tbody>' + q.summaries.map((row) => {
        const action = row.name === "高CTR / 高ROI" ? "保留结构，扩大人群与预算" : row.name === "低CTR / 高ROI" ? "保留购买承接，测试更强首帧" : row.name === "高CTR / 低ROI" ? "查点击诱因与落地页承接" : "暂停重复投放，重做钩子";
        const examples = (row.examples || []).slice(0, 3).map((item) => '<a href="' + esc(item.post_url || "#") + '" target="_blank" rel="noopener noreferrer">' + esc(item.material_code) + '</a>').join(" · ") || "—";
        return '<tr><td class="table-main">' + esc(row.name) + '</td><td>' + integerFormat(row.count) + '</td><td>' + esc(action) + '</td><td>' + examples + '</td></tr>';
      }).join("") + '</tbody>';
      icons();
    }

    function renderContent() {
      const stats = REPORT.theme_stats;
      const themeRows = stats.overall || [];
      $("themeTable").innerHTML = '<thead><tr><th>主题</th><th>素材数</th><th>花费</th><th>GMV</th><th>ROI</th><th>CTR</th></tr></thead><tbody>' + themeRows.map((row) => '<tr><td class="table-main">' + esc(row.name) + '</td><td>' + integerFormat(row.material_count) + '</td><td>' + money(row.spend) + '</td><td>' + money(row.gmv) + '</td><td>' + ratio(row.roi) + '</td><td>' + pct(row.ctr) + '</td></tr>').join("") + '</tbody>';
      $("sellingTable").innerHTML = '<thead><tr><th>卖点原文</th><th>素材数</th><th>花费</th><th>GMV</th><th>ROI</th></tr></thead><tbody>' + (stats.selling_points || []).map((row) => '<tr><td class="table-main" title="' + esc(row.name) + '">' + esc(shortText(row.name, 42)) + '</td><td>' + integerFormat(row.material_count) + '</td><td>' + money(row.spend) + '</td><td>' + money(row.gmv) + '</td><td>' + ratio(row.roi) + '</td></tr>').join("") + '</tbody>';
      $("contentTable").innerHTML = '<thead><tr><th>内容类型</th><th>素材数</th><th>花费</th><th>GMV</th><th>ROI</th></tr></thead><tbody>' + (stats.content_types || []).map((row) => '<tr><td class="table-main">' + esc(row.name) + '</td><td>' + integerFormat(row.material_count) + '</td><td>' + money(row.spend) + '</td><td>' + money(row.gmv) + '</td><td>' + ratio(row.roi) + '</td></tr>').join("") + '</tbody>';
    }

    function renderDomestic() {
      const rows = CATEGORIES.map((category) => {
        const benchmark = DOMESTIC_BENCHMARK[category];
        const observed = (REPORT.theme_stats.by_category[category] || []).slice(0, 3).map((row) => row.name).join("、") || "当前品类主题字段不足";
        return [category, benchmark[0], observed, benchmark[2], benchmark[3], benchmark[4]];
      });
      $("domesticTable").className = "benchmark-table";
      $("domesticTable").innerHTML = '<thead><tr><th>品类</th><th>国内内容入口（方法论）</th><th>SKT当前主题信号</th><th>帖子测试动作</th><th>Shopee承接</th><th>首要验证</th></tr></thead><tbody>' + rows.map((row) => '<tr><td class="table-main">' + esc(row[0]) + '</td><td>' + esc(row[1]) + '</td><td>' + esc(row[2]) + '</td><td>' + esc(row[3]) + '</td><td>' + esc(row[4]) + '</td><td>' + esc(row[5]) + '</td></tr>').join("") + '</tbody>';
    }

    function renderLibrary() {
      const category = state.libraryCategory;
      const status = state.libraryStatus;
      const dmsStatus = state.libraryDmsStatus;
      const search = state.librarySearch.toLowerCase();
      let rows = REPORT.library.filter((row) => {
        const categoryMatch = category === "全部" || (row.categories || []).includes(category);
        const statusMatch = status === "全部" || row.current_status === status;
        const dmsStatusMatch = dmsStatus === "全部" || row.dms_status === dmsStatus;
        const haystack = [row.material_code, row.product, row.material_name, row.username, row.theme_bucket, row.themes, row.selling_point].join(" ").toLowerCase();
        return categoryMatch && statusMatch && dmsStatusMatch && (!search || haystack.includes(search));
      });
      rows.sort((a, b) => n(b.current_spend) - n(a.current_spend) || n(b.quality_score) - n(a.quality_score));
      const total = rows.length;
      rows = rows.slice(0, 160);
      $("libraryNote").innerHTML = '<span>符合条件 <b>' + integerFormat(total) + '</b> 条</span><span>页面展示前 ' + integerFormat(rows.length) + ' 条</span><span>目标品类 DMS帖子共 <b>' + integerFormat(REPORT.overview.target_dms_posts) + '</b> 条</span>';
      $("libraryTable").innerHTML = '<thead><tr><th>素材 / 产品</th><th>主题</th><th>DMS信号</th><th>DMS状态</th><th>窗口状态</th><th>花费</th><th>GMV</th><th>ROI</th><th>CTR</th><th>链接</th></tr></thead><tbody>' + (rows.length ? rows.map((row) => '<tr><td>' + materialNameCell(row) + '</td><td>' + signal(row.theme_bucket) + '</td><td>信号 ' + n(row.quality_score).toFixed(1) + '<span class="table-sub">粉丝 ' + integerFormat(row.followers) + ' · 历史广告 ' + integerFormat(row.historical_ad_count) + '</span></td><td>' + dmsStatusTag(row.dms_status) + '</td><td>' + statusTag(row.current_status) + '</td><td>' + money(row.current_spend) + '</td><td>' + money(row.current_gmv) + '</td><td>' + ratio(row.current_roi) + '</td><td>' + pct(row.current_ctr) + '</td><td>' + linkButtons(row) + '</td></tr>').join("") : '<tr><td colspan="10" class="empty">没有符合条件的素材。</td></tr>') + '</tbody>';
      icons();
    }

    function renderLineage() {
      const o = REPORT.overview;
      const d = REPORT.data_quality;
      $("dmsLineage").innerHTML = "读取 MY DMS 全量 KOL帖子 " + integerFormat(d.dms_total_rows) + " 条（快照 " + integerFormat(d.dms_kol_rows_have_media_url) + " 条，原帖链接 " + integerFormat(d.dms_kol_rows_have_post_url) + " 条）。<br><a href=\"" + esc(REPORT.report.dms_page_url) + "\" target=\"_blank\" rel=\"noopener noreferrer\">打开 DMS 帖子列表</a>";
      $("metaLineage").textContent = "正式清洗日表：SKT / Malaysia / " + REPORT.report.date_start + " 至 " + REPORT.report.date_end + " / creative_type=帖子 / ad_type=种草。共 " + integerFormat(o.filtered_meta_rows) + " 行；其中 " + integerFormat(o.unmatched_rows) + " 行未精确关联 DMS。";
      $("aiLineage").textContent = d.ai_sheet_status;
      $("unmatchedBadge").textContent = integerFormat(o.unmatched_rows) + " 行 · " + money(REPORT.unmatched_meta.spend);
      $("unmatchedTable").innerHTML = '<thead><tr><th>Meta广告名</th><th>creative_id</th><th>行数</th><th>花费</th><th>GMV</th></tr></thead><tbody>' + (REPORT.unmatched_top || []).map((row) => '<tr><td class="table-main" title="' + esc(row.ad_name) + '">' + esc(shortText(row.ad_name, 76)) + '</td><td>' + esc(row.creative_id || "—") + '</td><td>' + integerFormat(row.rows) + '</td><td>' + money(row.spend) + '</td><td>' + money(row.gmv) + '</td></tr>').join("") + '</tbody>';
      $("footerText").textContent = "生成时间 " + REPORT.report.generated_at + " · 数据范围 " + REPORT.report.date_start + " 至 " + REPORT.report.date_end + " · 币种 MYR";
    }

    let charts = [];
    function chartFallback(id, message) { $(id).innerHTML = '<div class="empty">' + esc(message) + '</div>'; }
    function renderCharts() {
      if (!window.echarts) {
        chartFallback("trendChart", "图表库未加载，可使用下方表格与指标。");
        chartFallback("categoryChart", "图表库未加载，可使用上方品类表。");
        chartFallback("scatterChart", "图表库未加载，可使用 CTR × ROI 象限表。");
        chartFallback("themeChart", "图表库未加载，可使用主题表。");
        return;
      }
      charts.forEach((chart) => chart.dispose());
      charts = [];
      const trend = echarts.init($("trendChart"));
      const days = REPORT.daily_series;
      trend.setOption({
        animation: false,
        color: ["#c85f43", "#167a76", "#bb852d"],
        tooltip: { trigger: "axis" },
        legend: { top: 0, textStyle: { color: "#66716e", fontSize: 11 } },
        grid: { left: 48, right: 48, top: 36, bottom: 32 },
        xAxis: { type: "category", data: days.map((row) => row.date.slice(5)), axisLabel: { color: "#66716e", fontSize: 10 } },
        yAxis: [{ type: "value", name: "MYR", axisLabel: { color: "#66716e", fontSize: 10 } }, { type: "value", name: "ROI", axisLabel: { color: "#66716e", fontSize: 10 } }],
        series: [
          { name: "花费", type: "bar", barMaxWidth: 16, data: days.map((row) => row.spend) },
          { name: "GMV", type: "line", smooth: true, symbolSize: 5, data: days.map((row) => row.gmv) },
          { name: "ROI", type: "line", yAxisIndex: 1, smooth: true, symbolSize: 5, data: days.map((row) => row.roi) },
        ],
      });
      charts.push(trend);
      const category = echarts.init($("categoryChart"));
      const catRows = REPORT.category_summary;
      category.setOption({
        animation: false,
        color: ["#c85f43", "#167a76"],
        tooltip: { trigger: "axis" },
        legend: { top: 0, textStyle: { color: "#66716e", fontSize: 11 } },
        grid: { left: 48, right: 48, top: 36, bottom: 34 },
        xAxis: { type: "category", data: catRows.map((row) => row.category), axisLabel: { color: "#66716e", fontSize: 11 } },
        yAxis: [{ type: "value", name: "花费", axisLabel: { color: "#66716e", fontSize: 10 } }, { type: "value", name: "GMV", axisLabel: { color: "#66716e", fontSize: 10 } }],
        series: [{ name: "花费", type: "bar", barMaxWidth: 28, data: catRows.map((row) => row.spend) }, { name: "GMV", type: "bar", yAxisIndex: 1, barMaxWidth: 28, data: catRows.map((row) => row.gmv) }],
      });
      charts.push(category);
      const scatter = echarts.init($("scatterChart"));
      const scatterData = REPORT.quadrants.scatter || [];
      scatter.setOption({
        animation: false,
        color: CATEGORIES.map((category) => categoryColors[category]),
        tooltip: {
          formatter: (params) => {
            const row = params.data[3] || {};
            return "<b>" + esc(row.code || "") + "</b><br>" + esc(row.product || "") + "<br>CTR " + pct(row.ctr) + " · ROI " + ratio(row.roi) + "<br>花费 " + money(row.spend) + " · GMV " + money(row.gmv);
          },
        },
        legend: { top: 0, textStyle: { color: "#66716e", fontSize: 11 } },
        grid: { left: 58, right: 22, top: 38, bottom: 46 },
        xAxis: { type: "value", name: "CTR", axisLabel: { color: "#66716e", formatter: (value) => value.toFixed(1) + "%" }, splitLine: { lineStyle: { color: "#e6ebe5" } }, min: 0 },
        yAxis: { type: "value", name: "ROI", axisLabel: { color: "#66716e", formatter: (value) => value.toFixed(0) + "x" }, splitLine: { lineStyle: { color: "#e6ebe5" } }, min: 0 },
        series: CATEGORIES.map((category) => ({ name: category, type: "scatter", symbolSize: (value) => Math.max(7, Math.min(24, Math.sqrt(n(value[2])) / 2)), data: scatterData.filter((row) => row.category.split(" / ").includes(category)).map((row) => [n(row.ctr) * 100, n(row.roi), n(row.spend), row]) })).concat([{ name: "中位线", type: "scatter", data: [], markLine: { silent: true, lineStyle: { color: "#8b9690", type: "dashed" }, data: [{ xAxis: n(REPORT.quadrants.ctr_median) * 100 }, { yAxis: n(REPORT.quadrants.roi_median) }] } }]),
      });
      charts.push(scatter);
      const theme = echarts.init($("themeChart"));
      const themes = (REPORT.theme_stats.overall || []).slice(0, 12).reverse();
      theme.setOption({
        animation: false,
        color: ["#167a76"],
        tooltip: { trigger: "axis" },
        grid: { left: 110, right: 24, top: 18, bottom: 28 },
        xAxis: { type: "value", axisLabel: { color: "#66716e", fontSize: 10 } },
        yAxis: { type: "category", data: themes.map((row) => row.name), axisLabel: { color: "#66716e", fontSize: 10 } },
        series: [{ type: "bar", barMaxWidth: 18, data: themes.map((row) => row.gmv) }],
      });
      charts.push(theme);
    }

    function openMedia(code) {
      const row = REPORT.library.find((item) => item.material_code === code);
      if (!row) return;
      const modal = $("mediaModal");
      const mediaContainer = $("modalMedia");
      mediaContainer.innerHTML = "";
      const media = document.createElement(row.media_kind === "image" ? "img" : "video");
      media.className = "modal-media";
      media.src = row.media_url || "";
      if (row.media_kind === "image") {
        media.alt = "DMS图片快照";
      } else {
        media.controls = true;
        media.playsInline = true;
      }
      mediaContainer.appendChild(media);
      $("modalTitle").textContent = row.product || row.material_name || row.material_code;
      $("modalMeta").textContent = [row.material_code, row.username ? "@" + row.username : "", row.theme_bucket, row.selling_point || "DMS卖点未标注"].filter(Boolean).join(" · ");
      $("modalLinks").innerHTML = linkButtons(row);
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
      icons();
    }
    function closeMedia() {
      const modal = $("mediaModal");
      $("modalMedia").innerHTML = "";
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    }
    function exportCsv() {
      const headers = ["material_code", "product", "categories", "theme_bucket", "dms_status", "current_status", "current_spend", "current_gmv", "current_roi", "current_ctr", "current_orders", "current_days", "current_ad_variants", "post_url", "media_url"];
      const lines = [headers.join(",")].concat(REPORT.library.map((row) => headers.map((key) => "\"" + String(key === "categories" ? (row.categories || []).join(" / ") : row[key] == null ? "" : row[key]).replace(/"/g, "\"\"") + "\"").join(",")));
      const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "skt_my_kol_post_materials.csv";
      link.click();
      URL.revokeObjectURL(url);
    }

    document.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-tab-key]");
      if (tab) {
        state[tab.dataset.tabKey] = tab.dataset.tabCategory;
        if (tab.dataset.tabKey === "topCategory") renderTop();
        if (tab.dataset.tabKey === "opportunityCategory") renderOpportunity();
        if (tab.dataset.tabKey === "riskCategory") renderRisk();
        return;
      }
      const media = event.target.closest("[data-media-code]");
      if (media) { openMedia(media.dataset.mediaCode); return; }
      const copy = event.target.closest("[data-copy]");
      if (copy) {
        navigator.clipboard && navigator.clipboard.writeText(copy.dataset.copy);
        copy.title = "已复制";
        setTimeout(() => { copy.title = "复制DMS编号"; }, 1200);
      }
    });
    $("libraryCategory").addEventListener("change", (event) => { state.libraryCategory = event.target.value; renderLibrary(); });
    $("libraryDmsStatus").addEventListener("change", (event) => { state.libraryDmsStatus = event.target.value; renderLibrary(); });
    $("libraryStatus").addEventListener("change", (event) => { state.libraryStatus = event.target.value; renderLibrary(); });
    $("librarySearch").addEventListener("input", (event) => { state.librarySearch = event.target.value.trim(); renderLibrary(); });
    $("resetLibrary").addEventListener("click", () => { state.libraryCategory = "全部"; state.libraryStatus = "全部"; state.libraryDmsStatus = "全部"; state.librarySearch = ""; $("libraryCategory").value = "全部"; $("libraryDmsStatus").value = "全部"; $("libraryStatus").value = "全部"; $("librarySearch").value = ""; renderLibrary(); });
    $("exportCsv").addEventListener("click", exportCsv);
    $("closeModal").addEventListener("click", closeMedia);
    $("mediaModal").addEventListener("click", (event) => { if (event.target.id === "mediaModal") closeMedia(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMedia(); });
    CATEGORIES.forEach((category) => { const option = document.createElement("option"); option.value = category; option.textContent = category; $("libraryCategory").appendChild(option); });
    renderMetrics();
    renderCategoryTable();
    renderThemeTables();
    renderQuadrants();
    renderOpportunity();
    renderRisk();
    renderTop();
    renderContent();
    renderDomestic();
    renderLibrary();
    renderLineage();
    icons();
    renderCharts();
    window.addEventListener("resize", () => charts.forEach((chart) => chart.resize()));
  </script>
</body>
</html>
"""


def build_html(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    serialized = serialized.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__REPORT_DATA__", serialized)


def validate_payload(payload: dict[str, Any]) -> None:
    overview = payload["overview"]
    if overview["dms_kol_posts"] < 10000:
        raise ValueError("DMS KOL rows are unexpectedly small")
    if overview["filtered_meta_rows"] < overview["resolved_meta_rows"]:
        raise ValueError("Resolved Meta rows exceed filtered rows")
    for category in CATEGORY_ORDER:
        rows = payload["categories"][category]["top10"]
        candidates = payload["categories"][category]["test_candidates"]
        if len(rows) != 10:
            raise ValueError(f"{category} top list does not contain 10 rows")
        if len(candidates) != 10:
            raise ValueError(f"{category} candidate list does not contain 10 rows")
        if any(
            row.get("dms_status_code") != "1"
            or row.get("historical_ad_count") != 0
            or number(row.get("current_spend")) > 0
            or candidate_is_blocked(row)
            for row in candidates
        ):
            raise ValueError(f"{category} candidate list contains a previously run material")
        for row in rows + candidates + payload["categories"][category]["bad_repeat"]:
            if row.get("post_url") and not row["post_url"].startswith("http"):
                raise ValueError(f"Invalid post URL for {row.get('material_code')}")


def run() -> Path:
    payload = build_payload()
    validate_payload(payload)
    html = build_html(payload)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    SITE_PATH.write_text(html, encoding="utf-8")
    try:
        CONVENIENCE_PATH.write_text(html, encoding="utf-8")
    except OSError:
        pass
    summary_path = OUTPUT_DIR / "skt_my_kol_post_analysis_summary.json"
    summary_path.write_text(json.dumps(payload["overview"], ensure_ascii=False, indent=2), encoding="utf-8")
    return OUTPUT_PATH


if __name__ == "__main__":
    print(run())
