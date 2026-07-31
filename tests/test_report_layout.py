import unittest
from pathlib import Path

from pipelines.build_skt_alignment import HTML_TEMPLATE as MAIN_REPORT_TEMPLATE


ROOT = Path(__file__).resolve().parents[1]
MATERIAL_REPORT_TEMPLATE = (ROOT / "pipelines" / "build_skt_material_analysis.py").read_text(encoding="utf-8")


class ReportLayoutTests(unittest.TestCase):
    def test_wide_layout_reserves_navigation_rail_before_centering(self) -> None:
        for template in (MAIN_REPORT_TEMPLATE, MATERIAL_REPORT_TEMPLATE):
            with self.subTest(page="material" if "PAGE_DATA" in template else "main"):
                self.assertIn("--report-rail-width: 176px", template)
                self.assertIn("--report-balanced-width: min(1580px, calc(100vw - 272px))", template)
                self.assertIn(
                    "margin-left: calc((100vw - var(--report-rail-width) - var(--report-balanced-width)) / 2)",
                    template,
                )
                self.assertIn("document.body.classList.toggle('side-nav-collapsed', collapsed)", template)

    def test_navigation_is_hidden_when_there_is_no_side_rail(self) -> None:
        media_rule = "@media (min-width: 1181px) and (max-width: 1699px)"
        for template in (MAIN_REPORT_TEMPLATE, MATERIAL_REPORT_TEMPLATE):
            self.assertIn(media_rule, template)

    def test_kol_materials_render_as_direct_external_links(self) -> None:
        self.assertIn("function materialExternalUrl(row)", MATERIAL_REPORT_TEMPLATE)
        self.assertIn("if (isLinkMaterial(row))", MATERIAL_REPORT_TEMPLATE)
        self.assertIn('class="external-post-preview"', MATERIAL_REPORT_TEMPLATE)
        self.assertIn('rel="noopener noreferrer"', MATERIAL_REPORT_TEMPLATE)

    def test_product_visitor_conversion_uses_filtered_product_rows(self) -> None:
        self.assertIn('id="product-visitor-conversion"', MAIN_REPORT_TEMPLATE)
        self.assertIn('id="productMediaChart"', MAIN_REPORT_TEMPLATE)
        self.assertIn('id="productTrafficChart"', MAIN_REPORT_TEMPLATE)
        self.assertIn("function renderProductMediaChart(productRows)", MAIN_REPORT_TEMPLATE)
        self.assertIn("function renderProductTrafficChart(productRows)", MAIN_REPORT_TEMPLATE)
        self.assertIn("renderProductMediaChart(productRows);", MAIN_REPORT_TEMPLATE)
        self.assertIn("renderProductTrafficChart(productRows);", MAIN_REPORT_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
