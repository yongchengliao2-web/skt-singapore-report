import csv
import tempfile
import unittest
from pathlib import Path

from pipelines.build_skt_alignment import (
    HTML_TEMPLATE,
    assign_onsite_products_to_offsite_catalog,
    infer_offsite_advertised_product,
    load_category_reference,
    load_offsite,
    load_sp_gmv,
    normalize_audience_type,
    normalize_text,
    validate_downloaded_sheet,
)


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


if __name__ == "__main__":
    unittest.main()
