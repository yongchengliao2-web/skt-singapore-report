import tempfile
import unittest
from pathlib import Path

from tools.fetch_skt_bq_platform_daily import build_payload, validate_cache, write_payload


class FetchBigQueryPlatformDailyTests(unittest.TestCase):
    def records(self) -> list[dict[str, str]]:
        return [
            {
                "date": "2026-09-01",
                "platform": "Shopee",
                "gmv_rmb": "124302",
                "order_count": "1124",
                "sales_units": "1956",
                "gmv_loaded_at": "2026-09-07 02:01:46",
                "sales_loaded_at": "2026-09-07 02:01:46",
            },
            {
                "date": "2026-09-01",
                "platform": "TikTok",
                "gmv_rmb": "69641",
                "order_count": "540",
                "sales_units": "1007",
                "gmv_loaded_at": "2026-09-07 02:01:46",
                "sales_loaded_at": "2026-09-07 02:01:46",
            },
        ]

    def test_builds_and_validates_the_allowlisted_cache(self) -> None:
        payload = build_payload(self.records())
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "platform.json"
            write_payload(path, payload)
            validated = validate_cache(path)

        self.assertEqual(validated["metadata"]["date_end"], "2026-09-01")
        self.assertEqual(sum(row[4] for row in validated["rows"]), 2963)

    def test_rejects_duplicate_date_platform_keys(self) -> None:
        records = self.records()
        records.append(dict(records[0]))
        with self.assertRaisesRegex(RuntimeError, "duplicate date/platform"):
            build_payload(records)

    def test_rejects_a_date_missing_one_platform(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "missing a platform"):
            build_payload(self.records()[:1])

    def test_rejects_negative_sales_units(self) -> None:
        records = self.records()
        records[0]["sales_units"] = "-1"
        with self.assertRaisesRegex(RuntimeError, "invalid sales_units"):
            build_payload(records)


if __name__ == "__main__":
    unittest.main()
