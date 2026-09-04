import unittest
from pathlib import Path

from pipelines.build_skt_alignment import HTML_TEMPLATE as MAIN_REPORT_TEMPLATE


ROOT = Path(__file__).resolve().parents[1]
MAIN_REPORT_SOURCE = (ROOT / "pipelines" / "build_skt_alignment.py").read_text(encoding="utf-8")
MATERIAL_REPORT_TEMPLATE = (ROOT / "pipelines" / "build_skt_material_analysis.py").read_text(encoding="utf-8")
PASSWORD_WORKER = (ROOT / "scripts" / "cloudflare_password_worker.js").read_text(encoding="utf-8")


class ReportLayoutTests(unittest.TestCase):
    def test_skt_brand_theme_uses_blue_across_public_surfaces(self) -> None:
        for template in (MAIN_REPORT_TEMPLATE, MATERIAL_REPORT_TEMPLATE):
            with self.subTest(page="material" if "PAGE_DATA" in template else "main"):
                self.assertIn("--accent: #2563eb", template)
                self.assertIn("background: #173f7a", template)
                self.assertNotIn("--accent: #146b52", template)
                self.assertNotIn("background: #123f32", template)

        self.assertIn("border-top: 4px solid #2563eb", PASSWORD_WORKER)
        self.assertIn("background: #2563eb", PASSWORD_WORKER)
        self.assertNotIn("#146b52", PASSWORD_WORKER)

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

    def test_category_rows_expand_to_all_filtered_products(self) -> None:
        self.assertIn('id="categorySectionToggle"', MAIN_REPORT_TEMPLATE)
        self.assertIn("sktCategorySectionCollapsed", MAIN_REPORT_TEMPLATE)
        self.assertIn("function setupCategorySectionToggle", MAIN_REPORT_TEMPLATE)
        self.assertIn("setupCategorySectionToggle();", MAIN_REPORT_TEMPLATE)
        self.assertIn('class="category-detail-table"', MAIN_REPORT_TEMPLATE)
        self.assertIn("data-category-row-key", MAIN_REPORT_TEMPLATE)
        self.assertIn("function setupCategoryRowToggles", MAIN_REPORT_TEMPLATE)
        self.assertIn("sktCategoryExpandedRows", MAIN_REPORT_TEMPLATE)
        self.assertIn(
            "renderCategoryTable(categoryRows, compareCategoryRows, productRows, compareProductRows)",
            MAIN_REPORT_TEMPLATE,
        )
        self.assertIn("categoryProducts.map(product =>", MAIN_REPORT_TEMPLATE)
        self.assertIn(
            "<div>商品</div><div>商品销售额RMB</div><div>销售占比</div><div>访问</div><div>加购率</div><div>支付件转化</div>",
            MAIN_REPORT_TEMPLATE,
        )
        self.assertIn("function enrichProductMediaRows", MAIN_REPORT_TEMPLATE)
        self.assertIn("function tableAvailableMetricHtml", MAIN_REPORT_TEMPLATE)

    def test_filter_summary_warns_when_core_sources_are_partial(self) -> None:
        self.assertIn("function coreDataCompleteDate()", MAIN_REPORT_TEMPLATE)
        self.assertIn("核心源表完整至", MAIN_REPORT_TEMPLATE)
        self.assertIn('dataset.warning = String(hasPartialData)', MAIN_REPORT_TEMPLATE)

    def test_report_removes_operating_copy_and_old_product_columns(self) -> None:
        self.assertNotIn("经营", MAIN_REPORT_TEMPLATE)
        self.assertNotIn("经营", MATERIAL_REPORT_TEMPLATE)
        self.assertNotIn("SP销量</th>", MAIN_REPORT_TEMPLATE)
        self.assertNotIn("TT销量</th>", MAIN_REPORT_TEMPLATE)
        self.assertNotIn("销量增幅</th>", MAIN_REPORT_TEMPLATE)
        onsite_loader = MAIN_REPORT_SOURCE.split("def load_onsite_products", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('get_value(row, "Sales (Placed Order) (SGD)", "Sales (Paid Order) (SGD)")', onsite_loader)
        self.assertIn("按字段名读取 Sales (Placed Order) (SGD)", MAIN_REPORT_SOURCE)
        self.assertIn("row = dict(zip(headers, values))", onsite_loader)
        self.assertNotIn("values[", onsite_loader)

    def test_summary_units_use_dms_platform_unit_totals(self) -> None:
        self.assertIn("platform_units: sum(rows, 'platform_units')", MAIN_REPORT_TEMPLATE)
        self.assertIn("value: fmt0.format(t.platform_units)", MAIN_REPORT_TEMPLATE)
        self.assertIn("current: t.platform_units", MAIN_REPORT_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
