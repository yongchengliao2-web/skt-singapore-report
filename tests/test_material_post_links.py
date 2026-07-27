import sys
import unittest
from pathlib import Path


PIPELINES_DIR = Path(__file__).resolve().parents[1] / "pipelines"
if str(PIPELINES_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINES_DIR))

from build_skt_material_analysis import (  # noqa: E402
    find_dms_material,
    instagram_post_url,
    post_shortcodes,
)


class MaterialPostLinkTests(unittest.TestCase):
    def test_matches_pdrn_post_name_to_dms_instagram_url(self) -> None:
        dms_row = {
            "material_id": "SG-DUM86UWK29E",
            "post_url": "https://www.instagram.com/reel/DUM86uwk29e/",
            "snapshot_mode": "link",
        }
        lookup = {"POST:dum86uwk29e": dms_row}

        matched = find_dms_material(
            lookup,
            "PDRN面霜_帖子_DC_V_DUM86uwk29e_staceyng13",
        )

        self.assertIs(matched, dms_row)
        self.assertEqual(matched["post_url"], "https://www.instagram.com/reel/DUM86uwk29e/")

    def test_matches_5x_post_name_shortcode(self) -> None:
        name = "5X面霜_帖子_DC_V_DTsM9Ydk4Yq_qhairunnajwa"
        self.assertIn("DTsM9Ydk4Yq", post_shortcodes(name))
        self.assertEqual(
            instagram_post_url(name),
            "https://www.instagram.com/reel/DTsM9Ydk4Yq/",
        )

    def test_dms_url_keeps_canonical_post_type(self) -> None:
        url = "https://www.instagram.com/p/ABC_def-12/"
        self.assertIn("ABC_def-12", post_shortcodes(url))


if __name__ == "__main__":
    unittest.main()
