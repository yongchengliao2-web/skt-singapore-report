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

    def test_expected_date_uses_shanghai_yesterday(self) -> None:
        now = datetime(2026, 7, 27, 10, 45, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(expected_minimum_date(now), "2026-07-26")


if __name__ == "__main__":
    unittest.main()
