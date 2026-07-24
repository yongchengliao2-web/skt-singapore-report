import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pipelines.build_skt_material_snapshots import (
    SNAPSHOT_RELATIVE_DIR,
    ensure_material_snapshots,
    snapshot_filename,
)
from scripts.download_protected_assets import download_assets, referenced_snapshots


class MaterialSnapshotTests(unittest.TestCase):
    def test_existing_snapshot_is_attached_to_every_matching_row(self) -> None:
        source_url = "https://cdn.example.com/material/example.jpg"
        first_row = {
            "material_id": "SC123456",
            "material_key": "SC123456",
            "preview_url": source_url,
            "material_type": "image",
        }
        second_row = dict(first_row)
        payload = {"material_rows": [first_row], "library_rows": [second_row]}

        with tempfile.TemporaryDirectory() as temp_dir:
            site_dir = Path(temp_dir)
            filename = snapshot_filename(first_row, source_url)
            snapshot_path = site_dir / SNAPSHOT_RELATIVE_DIR / filename
            snapshot_path.parent.mkdir(parents=True)
            Image.new("RGB", (40, 40), "white").save(snapshot_path, "JPEG", quality=90)

            stats = ensure_material_snapshots(payload, site_dir)

            self.assertEqual(stats["cached"], 1)
            self.assertEqual(stats["created"], 0)
            self.assertEqual(first_row["snapshot_url"], f"/assets/material_snapshots/{filename}")
            self.assertEqual(second_row["snapshot_url"], first_row["snapshot_url"])

    def test_referenced_snapshots_are_mapped_under_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_path = root / "material.html"
            html_path.write_text(
                'const PAGE_DATA={"snapshot_url":"/assets/material_snapshots/a.jpg",'
                '"other":{"snapshot_url":"/assets/material_snapshots/b-2.jpg"}};',
                encoding="utf-8",
            )

            assets = referenced_snapshots([html_path], root / "preserved")

            self.assertEqual([remote for remote, _ in assets], [
                "/assets/material_snapshots/a.jpg",
                "/assets/material_snapshots/b-2.jpg",
            ])
            self.assertTrue(all(str(output).startswith(str((root / "preserved").resolve())) for _, output in assets))

    def test_snapshot_preservation_accepts_eighty_percent_success(self) -> None:
        assets = [(f"/assets/material_snapshots/{index}.jpg", Path(f"{index}.jpg")) for index in range(5)]

        def fake_download(_base_url: str, remote_path: str, _output_path: Path, _token: str) -> int:
            if remote_path.endswith("/4.jpg"):
                raise RuntimeError("temporary response")
            return 1024

        with patch("scripts.download_protected_assets.download_asset", side_effect=fake_download):
            download_assets(
                "https://example.com/",
                assets,
                session_token="test-token",
                workers=2,
                minimum_success_rate=0.8,
            )

        with patch("scripts.download_protected_assets.download_asset", side_effect=fake_download):
            with self.assertRaises(RuntimeError):
                download_assets(
                    "https://example.com/",
                    assets,
                    session_token="test-token",
                    workers=2,
                    minimum_success_rate=0.9,
                )


if __name__ == "__main__":
    unittest.main()
