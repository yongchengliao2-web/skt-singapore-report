import csv
import json
import tempfile
import unittest
from pathlib import Path

from pipelines.build_skt_alignment import (
    HTML_TEMPLATE,
    assign_onsite_products_to_offsite_catalog,
    canonical_offsite_product_name,
    empty_daily_row,
    infer_offsite_advertised_product,
    load_category_reference,
    load_bq_platform_daily,
    load_dms_commerce,
    load_new_mapping,
    load_offsite,
    load_onsite_ads,
    load_onsite_products,
    load_sp_gmv,
    match_platform_unit_products,
    normalize_audience_type,
    normalize_text,
    resolve_category,
    finalize_daily_rows,
    validate_downloaded_sheet,
)


class BigQueryPlatformDailyTests(unittest.TestCase):
    def write_cache(self, path: Path, rows: list[list[object]], **metadata: object) -> None:
        dates = sorted({str(row[0]) for row in rows})
        payload = {
            "metadata": {
                "brand": "SKT",
                "country_code": "SG",
                "scope": "parent",
                "source_table": "advance-rush-406115.dim_shopee_ads_performance.sg_dms_gmv_sales_daily",
                "currency": "RMB",
                "date_start": dates[0],
                "date_end": dates[-1],
                "row_count": len(rows),
                **metadata,
            },
            "columns": ["date", "platform", "gmv_rmb", "order_count", "sales_units"],
            "rows": rows,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_overwrites_daily_platform_summary_without_touching_detail_rows(self) -> None:
        rows = [
            ["2026-09-01", "Shopee", 124302, 1124, 1956],
            ["2026-09-01", "TikTok", 69641, 540, 1007],
        ]
        daily = {
            "2026-09-01": {
                **empty_daily_row("2026-09-01"),
                "sp_gmv_rmb": 1,
                "tt_gmv_rmb": 2,
                "sp_orders": 3,
                "tt_orders": 4,
                "sp_units": 5,
                "tt_units": 6,
                "product_paid_units": 99,
            }
        }
        product_rows = [{"product": "5X面霜", "sp_units": 5, "tt_units": 6}]
        category_rows = [{"category": "面霜", "sp_units": 5, "tt_units": 6}]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "platform.json"
            self.write_cache(path, rows)
            audit = load_bq_platform_daily(path, daily)

        finalized = finalize_daily_rows(daily)[0]
        self.assertEqual(finalized["platform_units"], 2963)
        self.assertEqual(finalized["platform_orders"], 1664)
        self.assertEqual(finalized["platform_gmv_rmb"], 193943)
        self.assertEqual(finalized["product_paid_units"], 99)
        self.assertEqual(product_rows[0]["sp_units"] + product_rows[0]["tt_units"], 11)
        self.assertEqual(category_rows[0]["sp_units"] + category_rows[0]["tt_units"], 11)
        self.assertEqual(audit["source"], "BigQuery")

    def test_rejects_incomplete_platform_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "platform.json"
            self.write_cache(path, [["2026-09-01", "Shopee", 124302, 1124, 1956]])
            with self.assertRaisesRegex(ValueError, "缺少 SP 或 TT"):
                load_bq_platform_daily(path, {})

    def test_rejects_a_cache_from_an_unapproved_source(self) -> None:
        rows = [
            ["2026-09-01", "Shopee", 124302, 1124, 1956],
            ["2026-09-01", "TikTok", 69641, 540, 1007],
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "platform.json"
            self.write_cache(path, rows, source_table="other.table")
            with self.assertRaisesRegex(ValueError, "不在 SKT 白名单"):
                load_bq_platform_daily(path, {})


class DownloadedSheetValidationTests(unittest.TestCase):
    def write_csv(self, path: Path, headers: list[str]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerow(headers)

    def test_rejects_ref_headers_before_replacing_onsite_products(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "onsite_products.csv.part"
            self.write_csv(source, ["#REF!", "", "#REF!"])

            with self.assertRaisesRegex(ValueError, r"#REF! headers in columns A,C"):
                validate_downloaded_sheet("站内产品数据-skt", source)

    def test_rejects_a_different_sheet_returned_for_onsite_products(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "onsite_products.csv.part"
            self.write_csv(source, ["店铺", "日期date", "GMV(After Seller Discounts)"])

            with self.assertRaisesRegex(ValueError, "returned unexpected columns"):
                validate_downloaded_sheet("站内产品数据-skt", source)

    def test_accepts_the_expected_onsite_product_contract(self) -> None:
        headers = [
            "日期date",
            "Item ID",
            "SKU",
            "Product",
            "链接",
            "品类",
            "Sales (Placed Order) (SGD)",
            "Units (Paid Order)",
            "Product Visitors (Visit)",
            "Product Page Views",
            "Product Visitors (Add to Cart)",
            "汇率",
            "extra",
            "Product Impressions",
            "Product Clicks",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "onsite_products.csv.part"
            self.write_csv(source, headers)

            validate_downloaded_sheet("站内产品数据-skt", source)

    def test_placeholder_ad_fields_do_not_match_an_arbitrary_category(self) -> None:
        category_ref = {
            "item_id_to_category": {},
            "sku_to_category": {},
            "item_name_to_category": {},
            "sku_name_to_category": {},
            "searchable_names": [("5x洗面奶5x面霜5x水5x精华5x防晒", "5PCS")],
            "keyword_categories": ["5PCS"],
        }

        category = resolve_category(
            category_ref,
            "-",
            "#N/A",
            "skintific",
            default="泛店铺/无产品",
        )

        self.assertEqual(category, "泛店铺/无产品")

    def test_accepts_shifted_onsite_product_fields_by_name(self) -> None:
        headers = [
            "二级品类",
            "三级品类",
            "品类",
            "链接",
            "日期date",
            "Item ID",
            "Product",
            "Sales (Placed Order) (SGD)",
            "Product Impression",
            "Product Clicks",
            "Units (Paid Order)",
            "Product Visitors (Visit)",
            "Product Page Views",
            "Product Visitors (Add to Cart)",
            "汇率",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "onsite_products.csv.part"
            self.write_csv(source, headers)
            validate_downloaded_sheet("站内产品数据-skt", source)


class LoadSpGmvTests(unittest.TestCase):
    def test_uses_after_seller_discounts_when_customer_payment_is_blank(self) -> None:
        fieldnames = [
            "店铺",
            "日期date",
            "Order Status",
            "Order Count",
            "GMV(After Seller Discounts)",
            "GMV(Customer Payment)",
        ]
        source_row = {
            "店铺": "新加坡SKT旗舰店",
            "日期date": "22/06/2026",
            "Order Status": "SHIPPED",
            "Order Count": "1022",
            "GMV(After Seller Discounts)": "21038.8",
            "GMV(Customer Payment)": "",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sp_store_gmv.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(source_row)

            daily = {}
            stores = load_sp_gmv(source, daily, fx_rate=5.35)

        row = daily["2026-06-22"]
        self.assertAlmostEqual(row["sp_gmv_sgd"], 21038.8)
        self.assertAlmostEqual(row["sp_gmv_rmb"], 112557.58)
        self.assertAlmostEqual(stores["新加坡SKT旗舰店"]["gmv_rmb"], 112557.58)

    def test_does_not_substitute_customer_payment_for_the_fixed_sp_field(self) -> None:
        fieldnames = [
            "店铺",
            "日期date",
            "Order Status",
            "Order Count",
            "GMV(After Seller Discounts)",
            "GMV(Customer Payment)",
        ]
        source_row = {
            "店铺": "新加坡SKT旗舰店",
            "日期date": "22/07/2026",
            "Order Status": "COMPLETED",
            "Order Count": "1",
            "GMV(After Seller Discounts)": "100",
            "GMV(Customer Payment)": "80",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sp_store_gmv.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(source_row)

            daily = {}
            load_sp_gmv(source, daily, fx_rate=5.35)

        self.assertAlmostEqual(daily["2026-07-22"]["sp_gmv_sgd"], 100.0)
        self.assertAlmostEqual(daily["2026-07-22"]["sp_gmv_rmb"], 535.0)


class LoadDmsCommerceTests(unittest.TestCase):
    def test_uses_dms_rmb_gmv_and_daily_sku_units(self) -> None:
        import json

        cache = {
            "brand": "SKT",
            "currency": "RMB",
            "gmv": [
                {"date": "2026-08-01", "platform": "Shopee", "gmv_rmb": 1200, "orders": 12},
                {"date": "2026-08-01", "platform": "TikTok", "gmv_rmb": 800, "orders": 8},
                {"date": "2026-07-01", "platform": "Shopee", "gmv_rmb": 900, "orders": 9},
            ],
            "units": [
                {"date": "2026-08-01", "platform": "Shopee", "sku": "SKU-1", "product": "5X面霜", "units": 5},
                {"date": "2026-07-01", "platform": "Shopee", "sku": "SKU-1", "product": "5X面霜", "units": 3},
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "skt_dms_commerce_latest.json"
            source.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            daily = {}
            category_daily = {}
            store_rows, unit_rows = load_dms_commerce(
                source,
                daily,
                {"sku_to_category": {"sku1": "面霜"}, "sku_to_onsite_product": {}},
                category_daily,
            )

        self.assertEqual(store_rows["DMS / Shopee"]["gmv_rmb"], 2100)
        self.assertEqual(daily["2026-08-01"]["sp_gmv_rmb"], 1200)
        self.assertEqual(daily["2026-08-01"]["tt_gmv_rmb"], 800)
        finalized = {row["date"]: row for row in finalize_daily_rows(daily)}
        self.assertEqual(finalized["2026-08-01"]["platform_units"], 5)
        self.assertEqual(len(unit_rows), 2)
        current_units = next(row for row in unit_rows if row["date"] == "2026-08-01")
        self.assertEqual(current_units["category"], "面霜")
        self.assertEqual(current_units["prior_units"], 3)


class LoadOnsiteProductTests(unittest.TestCase):
    def test_deduplicates_source_sales_before_product_gmv_conversion(self) -> None:
        headers = [
            "二级品类",
            "三级品类",
            "品类",
            "链接",
            "日期date",
            "Item ID",
            "Product",
            "Sales (Placed Order) (SGD)",
            "Product Impression",
            "Product Clicks",
            "Units (Paid Order)",
            "Product Visitors (Visit)",
            "Product Page Views",
            "Product Visitors (Add to Cart)",
            "汇率",
        ]
        values = [
            "新增分组",
            "新增品类",
            "面膜",
            "美白面膜",
            "8月1日",
            "",
            "SKT Mask",
            "100",
            "1000",
            "100",
            "4",
            "200",
            "300",
            "50",
            "5.35",
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "onsite_products.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                writer.writerow(values)

            daily = {}
            category_daily = {}
            category_rows, product_rows, product_daily_rows = load_onsite_products(
                source,
                daily,
                fx_rate=5.35,
                category_ref={},
                category_daily=category_daily,
            )

        self.assertAlmostEqual(daily["2026-08-01"]["product_paid_sales_sgd"], 50.0)
        self.assertAlmostEqual(daily["2026-08-01"]["product_paid_sales_rmb"], 267.5)
        self.assertAlmostEqual(category_rows[0]["paid_sales_sgd"], 50.0)
        self.assertAlmostEqual(category_rows[0]["paid_sales_rmb"], 267.5)
        self.assertAlmostEqual(product_rows[0]["paid_sales_sgd"], 50.0)
        self.assertAlmostEqual(product_rows[0]["paid_sales_rmb"], 267.5)
        self.assertAlmostEqual(product_daily_rows[0]["paid_sales_sgd"], 50.0)
        self.assertAlmostEqual(product_daily_rows[0]["paid_sales_rmb"], 267.5)
        self.assertAlmostEqual(product_rows[0]["product_impressions"], 1000.0)
        self.assertAlmostEqual(product_rows[0]["product_clicks"], 100.0)

    def test_merges_item_variation_rows_before_coupon_enrichment(self) -> None:
        headers = [
            "日期date",
            "Item ID",
            "SKU",
            "Product",
            "链接",
            "品类",
            "Sales (Placed Order) (SGD)",
            "Units (Paid Order)",
            "Product Visitors (Visit)",
            "Product Page Views",
            "Product Visitors (Add to Cart)",
            "汇率",
        ]
        values = [
            ["8月1日", "123456789", "-", "5X面霜", "5X面霜", "面霜", "100", "2", "20", "30", "4", "5.35"],
            ["8月1日", "123456789", "variation-1", "5X面霜", "5X面霜", "未归类", "100", "2", "20", "30", "4", "5.35"],
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "onsite_products.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                writer.writerows(values)

            category_rows, product_rows, product_daily_rows = load_onsite_products(
                source,
                {},
                fx_rate=5.35,
                category_ref={},
                category_daily={},
            )

        self.assertEqual(len(product_rows), 1)
        self.assertEqual(len(product_daily_rows), 1)
        self.assertEqual(product_rows[0]["category"], "面霜")
        self.assertAlmostEqual(product_rows[0]["paid_sales_sgd"], 100.0)
        self.assertEqual(category_rows[0]["category"], "面霜")


class NewMappingTests(unittest.TestCase):
    def test_reads_duplicate_category_headers_and_fills_down_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "new_mapping.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["分组", "品类", "", "Item ID", "产品", "品类"])
                writer.writerow(["护肤", "面霜", "", "123456789", "5X面霜", "面霜"])
                writer.writerow(["", "", "", "987654321", "5X精华", "精华"])

            mapping = load_new_mapping(source)

        self.assertEqual(mapping["group_order"], ["护肤"])
        self.assertEqual(mapping["item_by_id"]["123456789"]["category"], "面霜")
        self.assertEqual(mapping["item_by_id"]["987654321"]["group"], "护肤")
        self.assertEqual(mapping["category_group_map_normalized"]["面霜"], "护肤")


class LoadOnsiteAdProductTests(unittest.TestCase):
    def test_matches_link_to_t_catalog_and_returns_product_daily_spend(self) -> None:
        fieldnames = [
            "日期date",
            "广告花费-RMB",
            "广告GMV-RMB",
            "链接",
            "Ad Name",
            "Product ID",
            "Impression",
            "Clicks",
            "Conversions",
            "Items Sold",
        ]
        source_rows = [
            {
                "日期date": "2026-08-10",
                "广告花费-RMB": "100",
                "广告GMV-RMB": "500",
                "链接": "5X面霜",
                "Ad Name": "5X cream",
                "Product ID": "",
                "Impression": "10",
                "Clicks": "2",
                "Conversions": "1",
                "Items Sold": "1",
            },
            {
                "日期date": "2026-07-10",
                "广告花费-RMB": "80",
                "广告GMV-RMB": "400",
                "链接": "5X面霜",
                "Ad Name": "5X cream",
                "Product ID": "",
                "Impression": "8",
                "Clicks": "1",
                "Conversions": "1",
                "Items Sold": "1",
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "onsite_ads.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(source_rows)

            daily = {}
            category_daily = {}
            _, _, product_rows, product_daily_rows = load_onsite_ads(
                source,
                daily,
                {"offsite_product_by_normalized": {normalize_text("5X面霜"): "5X面霜"}},
                category_daily,
            )

        self.assertEqual(product_rows[0]["advertised_product"], "5X面霜")
        self.assertAlmostEqual(product_rows[0]["onsite_spend_rmb"], 180.0)
        self.assertEqual(len(product_daily_rows), 2)
        self.assertAlmostEqual(daily["2026-08-10"]["onsite_spend_rmb"], 100.0)


class OffsiteAudienceTests(unittest.TestCase):
    def test_normalizes_q_column_values_and_preserves_blanks(self) -> None:
        self.assertEqual(normalize_audience_type("拉新"), "拉新")
        self.assertEqual(normalize_audience_type("再营销"), "再营销")
        self.assertEqual(normalize_audience_type(""), "未标记")

    def test_groups_q_column_by_day_and_applies_row_exchange_rate(self) -> None:
        fieldnames = [
            "Date_start",
            "Spend",
            "Purchase Value",
            "汇率",
            "link-Click",
            "Conversions",
            "拉新/再营销",
            "产品",
        ]
        source_rows = [
            {
                "Date_start": "2026-07-28",
                "Spend": "10",
                "Purchase Value": "30",
                "汇率": "6.9",
                "link-Click": "20",
                "Conversions": "2",
                "拉新/再营销": "拉新",
                "产品": "测试产品",
            },
            {
                "Date_start": "2026-07-28",
                "Spend": "5",
                "Purchase Value": "25",
                "汇率": "7",
                "link-Click": "10",
                "Conversions": "3",
                "拉新/再营销": "再营销",
                "产品": "测试产品",
            },
            {
                "Date_start": "2026-07-28",
                "Spend": "1",
                "Purchase Value": "2",
                "汇率": "6.9",
                "link-Click": "4",
                "Conversions": "1",
                "拉新/再营销": "",
                "产品": "测试产品",
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "offsite.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(source_rows)
            daily = {}
            category_daily = {}
            category_ref = {"offsite_product_by_normalized": {}}
            *_, audience_rows = load_offsite(source, daily, category_ref, category_daily)

        by_audience = {row["audience"]: row for row in audience_rows}
        self.assertAlmostEqual(by_audience["拉新"]["spend_rmb"], 69.0)
        self.assertAlmostEqual(by_audience["拉新"]["purchase_value_rmb"], 207.0)
        self.assertAlmostEqual(by_audience["再营销"]["spend_rmb"], 35.0)
        self.assertAlmostEqual(by_audience["再营销"]["purchase_value_rmb"], 175.0)
        self.assertAlmostEqual(by_audience["未标记"]["spend_rmb"], 6.9)
        self.assertEqual(by_audience["未标记"]["clicks"], 4.0)
        self.assertEqual(by_audience["未标记"]["conversions"], 1.0)

    def test_report_renders_the_q_column_audience_comparison_table(self) -> None:
        self.assertIn('id="audience-performance"', HTML_TEMPLATE)
        self.assertIn('class="audience-table"', HTML_TEMPLATE)
        self.assertIn("function renderAudienceTable", HTML_TEMPLATE)
        self.assertIn("花费环比", HTML_TEMPLATE)
        self.assertIn("转化率环比", HTML_TEMPLATE)


class OffsiteProductCatalogTests(unittest.TestCase):
    def test_matches_onsite_alias_to_the_column_t_product(self) -> None:
        category_ref = {
            "offsite_product_by_normalized": {
                normalize_text("半哑精华绿色气垫"): "半哑精华绿色气垫",
            },
        }

        self.assertEqual(
            infer_offsite_advertised_product(category_ref, "半哑光气垫"),
            "半哑精华绿色气垫",
        )

    def test_canonicalizes_bundle_names_for_offsite_display(self) -> None:
        self.assertEqual(canonical_offsite_product_name("面霜合集-377面霜"), "面霜合集")
        self.assertEqual(canonical_offsite_product_name("防晒合集-哑光防晒"), "防晒")
        self.assertEqual(canonical_offsite_product_name("洁面合集+PDRN精华"), "洁面")
        self.assertEqual(canonical_offsite_product_name("防晒喷雾"), "防晒喷雾")

    def test_loads_physical_column_t_as_the_advertised_product_catalog(self) -> None:
        headers = [f"column_{index}" for index in range(20)]
        headers[19] = "\u7ad9\u5916\u6295\u653e\u4ea7\u54c1"
        values = [""] * 20
        values[19] = "5X\u9762\u971c"

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "category_map.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                writer.writerow(values)
            reference = load_category_reference(source)

        self.assertEqual(reference["offsite_products"], ["5X\u9762\u971c"])
        self.assertEqual(reference["offsite_product_count"], 1)
        self.assertEqual(reference["offsite_product_by_normalized"][normalize_text("5X\u9762\u971c")], "5X\u9762\u971c")

    def test_loads_r_and_u_columns_as_explicit_product_overrides(self) -> None:
        headers = [f"column_{index}" for index in range(21)]
        values = [""] * 21
        values[12] = "SKINTIFIC-05"
        values[17] = "23935452636"
        values[19] = "5X\u9762\u971c"
        values[20] = "5X\u9762\u971c"

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "category_map.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                writer.writerow(values)
            reference = load_category_reference(source)

        self.assertEqual(reference["sku_to_onsite_product"][normalize_text("SKINTIFIC-05")], "23935452636")
        self.assertEqual(reference["offsite_product_to_onsite_product"][normalize_text("5X\u9762\u971c")], "5X\u9762\u971c")

    def test_r_column_override_resolves_ambiguous_platform_unit_product(self) -> None:
        unit_rows = [
            {
                "platform": "SP",
                "sku": "SKINTIFIC-334",
                "product": "5X\u9762\u971c100g",
                "product_override": "25507637912",
                "category": "\u9762\u971c",
                "units": 10,
                "prior_units": 8,
            }
        ]
        product_rows = [
            {"item_id": "23935452636", "product": "5X\u9762\u971c", "category": "\u9762\u971c", "paid_sales_rmb": 100},
            {"item_id": "25507637912", "product": "5X\u9762\u971c-80g", "category": "\u9762\u971c", "paid_sales_rmb": 80},
        ]

        assignments, gaps = match_platform_unit_products(unit_rows, product_rows)

        key = (normalize_text("SKINTIFIC-334"), normalize_text("5X\u9762\u971c100g"), "\u9762\u971c")
        self.assertEqual(assignments[key], ("5X\u9762\u971c-80g", "\u9762\u971c"))
        self.assertEqual(gaps, [])

    def test_assigns_one_onsite_product_and_leaves_the_rest_unadvertised(self) -> None:
        reference = {"offsite_products": ["5X\u9762\u971c"]}
        catalog_rows = [
            {
                "product": "5X\u9762\u971c",
                "category": "\u9762\u971c",
                "product_title": "SKINTIFIC 5X Ceramide Barrier Cream",
                "paid_sales_rmb": 1000,
            },
            {
                "product": "5X\u9762\u971c-80g",
                "category": "\u9762\u971c",
                "product_title": "SKINTIFIC 5X Ceramide Barrier Cream 80g",
                "paid_sales_rmb": 900,
            },
            {
                "product": "GEL\u6d17\u9762\u5976",
                "category": "\u6d01\u9762",
                "product_title": "SKINTIFIC Gel Cleanser",
                "paid_sales_rmb": 800,
            },
        ]

        assignments = assign_onsite_products_to_offsite_catalog(reference, catalog_rows)

        self.assertEqual(assignments[(normalize_text("5X\u9762\u971c"), "\u9762\u971c")], "5X\u9762\u971c")
        self.assertNotIn((normalize_text("5X\u9762\u971c-80g"), "\u9762\u971c"), assignments)
        self.assertNotIn((normalize_text("GEL\u6d17\u9762\u5976"), "\u6d01\u9762"), assignments)

    def test_infers_longest_catalog_product_when_source_product_is_blank(self) -> None:
        products = [
            "\u6c34\u6cb9\u55b7\u96fe",
            "PDRN\u6c34\u6cb9\u55b7\u96fe",
            "\u7c89\u8272PDRN\u6c34\u6cb9\u55b7\u96fe",
        ]
        reference = {
            "offsite_products": products,
            "offsite_product_by_normalized": {normalize_text(product): product for product in products},
        }

        inferred = infer_offsite_advertised_product(
            reference,
            "",
            "\u7c89\u8272PDRN\u6c34\u6cb9\u55b7\u96fe_\u5e16\u5b50_DC_V_test",
            "",
            "generic_campaign",
        )

        self.assertEqual(inferred, "\u7c89\u8272PDRN\u6c34\u6cb9\u55b7\u96fe")

    def test_blank_source_product_keeps_spend_on_the_inferred_product(self) -> None:
        product = "PDRN\u9762\u971c"
        reference = {
            "offsite_products": [product],
            "offsite_product_by_normalized": {normalize_text(product): product},
        }
        fieldnames = [
            "Date_start",
            "Spend",
            "Purchase Value",
            "\u6c47\u7387",
            "\u4ea7\u54c1",
            "Ad_name",
            "adset_name",
            "campaign_name",
        ]
        source_row = {
            "Date_start": "2026-08-03",
            "Spend": "10",
            "Purchase Value": "30",
            "\u6c47\u7387": "6.9",
            "\u4ea7\u54c1": "",
            "Ad_name": "PDRN\u9762\u971c_\u5e16\u5b50_DC_V_test",
            "adset_name": "",
            "campaign_name": "generic_campaign",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "offsite.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(source_row)
            product_rows, *_ = load_offsite(source, {}, reference, {})

        self.assertEqual(len(product_rows), 1)
        self.assertEqual(product_rows[0]["product"], product)
        self.assertEqual(product_rows[0]["advertised_product"], product)
        self.assertAlmostEqual(product_rows[0]["spend_rmb"], 69.0)

    def test_uses_source_title_for_a_trusted_pdrn_alias(self) -> None:
        reference = {"offsite_products": ["PDRN\u6c34\u6cb9\u55b7\u96fe"]}
        catalog_rows = [
            {
                "product": "\u7f8e\u767d\u6c34\u6cb9\u55b7\u96fe",
                "category": "\u8865\u6c34\u55b7\u96fe",
                "product_title": "SKINTIFIC PDRN Radiance Bright Serum Spray",
                "paid_sales_rmb": 1000,
            }
        ]

        assignments = assign_onsite_products_to_offsite_catalog(reference, catalog_rows)

        self.assertEqual(
            assignments[(normalize_text("\u7f8e\u767d\u6c34\u6cb9\u55b7\u96fe"), "\u8865\u6c34\u55b7\u96fe")],
            "PDRN\u6c34\u6cb9\u55b7\u96fe",
        )

    def test_report_groups_unmatched_onsite_products_with_zero_offsite_fields(self) -> None:
        self.assertIn("row.placement_status === 'unadvertised'", HTML_TEMPLATE)
        self.assertIn("\u672a\u6295\u653e\u4ea7\u54c1", HTML_TEMPLATE)
        self.assertIn('class="not-advertised-value">-', HTML_TEMPLATE)

    def test_report_keeps_unmatched_offsite_spend_visible(self) -> None:
        self.assertIn("function aggregateUnmatchedOffsiteRow", HTML_TEMPLATE)
        self.assertIn("\u5f85\u8865\u4ea7\u54c1\u6620\u5c04", HTML_TEMPLATE)
        self.assertIn("\u82b1\u8d39\u5df2\u4fdd\u7559", HTML_TEMPLATE)

    def test_report_adds_onsite_spend_and_period_change_columns(self) -> None:
        self.assertIn("<th>站内花费</th><th>环比</th>", HTML_TEMPLATE)
        self.assertIn("function aggregateAdvertisedOnsiteSpendRows", HTML_TEMPLATE)
        self.assertIn("selectedOnsiteAdProductRows(period, 'compare')", HTML_TEMPLATE)

    def test_report_adds_collapsible_offsite_product_table(self) -> None:
        self.assertIn('id="offsiteProductToggle"', HTML_TEMPLATE)
        self.assertIn('class="offsite-product-toggle-row offsite-product-group-row"', HTML_TEMPLATE)
        self.assertIn('data-expanded-label="已投放产品', HTML_TEMPLATE)
        self.assertIn('aria-controls="offsiteProductTable"', HTML_TEMPLATE)
        self.assertIn('data-offsite-product-row-key', HTML_TEMPLATE)
        self.assertIn('id="offsiteProductRestore"', HTML_TEMPLATE)
        self.assertIn('恢复隐藏行', HTML_TEMPLATE)
        self.assertIn("function setupOffsiteProductToggle", HTML_TEMPLATE)
        self.assertIn("sktOffsiteProductTableCollapsed", HTML_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
