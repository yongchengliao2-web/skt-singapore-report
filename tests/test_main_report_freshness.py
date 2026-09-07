import tempfile
import unittest
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from scripts.check_main_report import (
    expected_minimum_date,
    read_report_payload,
    validate_freshness,
)


class MainReportFreshnessTests(unittest.TestCase):
    def payload(self, date_value: str) -> dict:
        return {
            "summary": {
                "generated_at": "2026-07-27 12:00",
                "report_date": "2026-07-27",
                "date_start": "2026-01-01",
                "date_end": date_value,
                "freshness": {
                    "sp_gmv": date_value,
                    "tt_gmv": date_value,
                    "platform_sales_bq": date_value,
                    "offsite": date_value,
                    "onsite_ads": date_value,
                    "onsite_products": date_value,
                },
            },
            "daily_rows": [{"date": date_value}],
        }

    def test_reads_embedded_main_report_payload(self) -> None:
        payload = self.payload("2026-07-26")
        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "index.html"
            html_path.write_text(
                f"<script>const DATA = {json.dumps(payload)};</script>",
                encoding="utf-8",
            )
            self.assertEqual(read_report_payload(html_path), payload)

    def test_rejects_any_stale_required_source(self) -> None:
        payload = self.payload("2026-07-26")
        payload["summary"]["freshness"]["onsite_products"] = "2026-07-23"
        with self.assertRaisesRegex(RuntimeError, "onsite_products=2026-07-23"):
            validate_freshness(payload, "2026-07-26")

    def test_allows_one_stale_source_when_configured(self) -> None:
        payload = self.payload("2026-07-26")
        payload["summary"]["freshness"]["tt_gmv"] = "2026-07-17"
        result = validate_freshness(payload, "2026-07-26", max_stale_sources=1)
        self.assertEqual(result["stale_sources"], {"tt_gmv": "2026-07-17"})

    def test_rejects_more_stale_sources_than_configured(self) -> None:
        payload = self.payload("2026-07-26")
        payload["summary"]["freshness"]["tt_gmv"] = "2026-07-17"
        payload["summary"]["freshness"]["onsite_ads"] = "2026-07-18"
        with self.assertRaisesRegex(RuntimeError, "2 stale sources"):
            validate_freshness(payload, "2026-07-26", max_stale_sources=1)

    def test_rejects_missing_source_even_when_stale_sources_are_allowed(self) -> None:
        payload = self.payload("2026-07-26")
        payload["summary"]["freshness"]["tt_gmv"] = ""
        with self.assertRaisesRegex(RuntimeError, "source date is missing: tt_gmv"):
            validate_freshness(payload, "2026-07-26", max_stale_sources=5)

    def test_expected_date_allows_two_business_days_of_source_lag(self) -> None:
        monday = datetime(2026, 8, 3, 10, 45, tzinfo=timezone(timedelta(hours=8)))
        wednesday = datetime(2026, 8, 5, 10, 45, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(expected_minimum_date(monday), "2026-07-30")
        self.assertEqual(expected_minimum_date(wednesday), "2026-08-03")

    def test_expected_date_skips_weekends_for_manual_refreshes(self) -> None:
        sunday = datetime(2026, 8, 2, 14, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(expected_minimum_date(sunday), "2026-07-30")


if __name__ == "__main__":
    unittest.main()
