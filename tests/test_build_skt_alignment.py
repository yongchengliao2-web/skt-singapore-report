import csv
import tempfile
import unittest
from pathlib import Path

from pipelines.build_skt_alignment import (
    HTML_TEMPLATE,
    assign_onsite_products_to_offsite_catalog,
    load_category_reference,
    load_sp_gmv,
    normalize_text,
)


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


if __name__ == "__main__":
    unittest.main()
