import csv
import tempfile
import unittest
from pathlib import Path

from pipelines.build_skt_alignment import load_sp_gmv


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


if __name__ == "__main__":
    unittest.main()
