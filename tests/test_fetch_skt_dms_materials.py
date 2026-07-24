import unittest

from pipelines.fetch_skt_dms_materials import normalize_row


class FetchSktDmsMaterialTests(unittest.TestCase):
    def test_kol_row_uses_original_post_url_and_keeps_preview_separate(self) -> None:
        row = normalize_row(
            {
                "id": 881,
                "materialCode": "SC12345678",
                "materialName": "KOL launch post",
                "dataFromType": 4,
                "url": "https://www.instagram.com/reel/ABC123/",
                "ossFiles": [
                    {
                        "fullUrlWithHttps": "https://cdn.example.com/previews/ABC123.jpg",
                        "mimeType": "image/jpeg",
                    }
                ],
            }
        )

        self.assertEqual(row["material_source"], "KOL素材")
        self.assertEqual(row["snapshot_mode"], "link")
        self.assertEqual(row["post_url"], "https://www.instagram.com/reel/ABC123/")
        self.assertEqual(row["preview_url"], "https://cdn.example.com/previews/ABC123.jpg")


if __name__ == "__main__":
    unittest.main()
