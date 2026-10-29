import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from lxml import html
from playwright.async_api import Error as PlaywrightError, async_playwright

import layout
import posters
from poster_rendering import balance_header_widths, balance_text_width
from tests.test_layout import DRAFT

ROOT = Path(__file__).resolve().parent.parent

LINE_WIDTHS = """element => {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const lines = new Map();
    while (walker.nextNode()) {
        const range = document.createRange();
        range.selectNodeContents(walker.currentNode);
        for (const rect of range.getClientRects()) {
            if (!rect.width) continue;
            const key = Math.round(rect.top);
            const line = lines.get(key) || {left: rect.left, right: rect.right};
            line.left = Math.min(line.left, rect.left);
            line.right = Math.max(line.right, rect.right);
            lines.set(key, line);
        }
    }
    return Array.from(lines.values(), line => line.right - line.left);
}"""


def imbalance(widths: list[float]) -> float:
    mean = sum(widths) / len(widths)
    return sum((width - mean) ** 2 for width in widths) / (len(widths) * mean ** 2)


class TextBalancingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch()
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page(viewport={"width": 640, "height": 900})

    async def load(self, text: str, width: int = 420) -> None:
        await self.page.set_content(f"""
            <style>
                * {{ box-sizing: border-box; margin: 0; padding: 0; }}
                body {{ width: {width}px; }}
                #text {{
                    width: 100%; margin-inline: auto; text-align: center;
                    font: 20px/1.6 monospace; overflow-wrap: anywhere;
                }}
            </style>
            <div id="text">{text}</div>
        """)

    async def test_balances_rendered_lines_and_preserves_line_count(self) -> None:
        await self.load("A detailed investigation of cells and their response to treatment", width=500)
        element = self.page.locator("#text")
        original = await element.evaluate(LINE_WIDTHS)
        percentage = await balance_text_width(self.page, "#text")
        balanced = await element.evaluate(LINE_WIDTHS)
        self.assertEqual(len(balanced), len(original))
        self.assertLess(imbalance(balanced), imbalance(original))
        self.assertLess(imbalance(balanced), 0.02)
        self.assertGreater(percentage, 0)
        self.assertLess(percentage, 100)
        bounds = await element.bounding_box()
        self.assertIsNotNone(bounds)
        self.assertAlmostEqual(bounds["x"] + bounds["width"] / 2, 250, delta=0.1)

        # Compare with every available CSS-pixel width, not just the baseline.
        score = imbalance(balanced)
        for width in range(1, 501):
            await element.evaluate("(element, width) => element.style.width = `${width}px`", width)
            candidate = await element.evaluate(LINE_WIDTHS)
            if len(candidate) == len(original):
                self.assertLessEqual(score, imbalance(candidate) + 1e-8)

    async def test_inline_markup_nonbreaking_author_names_and_unicode(self) -> None:
        for text in (
            "<strong>A detailed investigation</strong> of cells and their response to treatment",
            "Ada&nbsp;Lovelace&nbsp;(Institute), Grace&nbsp;Hopper&nbsp;(University), "
            "Ren&eacute;&nbsp;Descartes&nbsp;(Laboratory)",
            "研究者の共同研究と結果についての報告：細胞の反応を詳しく調べる研究",
            "First explicit line<br>Another line with several more words to wrap",
        ):
            with self.subTest(text=text):
                await self.load(text)
                element = self.page.locator("#text")
                original = await element.evaluate(LINE_WIDTHS)
                markup = await element.inner_html()
                await balance_text_width(self.page, "#text")
                balanced = await element.evaluate(LINE_WIDTHS)
                self.assertEqual(len(balanced), len(original))
                self.assertLessEqual(imbalance(balanced), imbalance(original) + 1e-8)
                self.assertEqual(await element.inner_html(), markup)

    async def test_empty_and_single_line_text_keep_available_width(self) -> None:
        for text in ("", " ", "Short title"):
            with self.subTest(text=text):
                await self.load(text)
                self.assertEqual(await balance_text_width(self.page, "#text"), 100)

    async def test_hidden_container_reports_an_error(self) -> None:
        await self.load("Title", width=0)
        with self.assertRaisesRegex(PlaywrightError, "no available width"):
            await balance_text_width(self.page, "#text")

    async def test_header_renderer_loads_local_styles_and_measures_both_sections(self) -> None:
        rendered = """
            <html><head><link rel="stylesheet" href="/static/css/layout.css"></head>
            <body style="--heading-font-face: monospace; width: 640px">
                <div class="poster-header">
                    <div class="logo-title">
                        <div id="logo"></div>
                        <div id="title" class="title">A detailed investigation of cells
                            and their response to treatment</div>
                    </div>
                    <div id="authors" class="authors">Ada Lovelace, Grace Hopper,
                        Rene Descartes, Katherine Johnson, Alan Turing</div>
                </div>
            </body></html>
        """
        widths = await balance_header_widths(rendered, 640)
        self.assertEqual(set(widths), {"#title", "#authors"})
        for width in widths.values():
            self.assertGreater(width, 0)
            self.assertLessEqual(width, 100)
        self.assertLess(widths["#title"], 100)
        await self.page.set_content(rendered.replace(
            '<link rel="stylesheet" href="/static/css/layout.css">',
            "<style>" + (ROOT / "static/css/layout.css").read_text() + "</style>",
        ))
        for selector, percentage in widths.items():
            element = self.page.locator(selector)
            before = await element.evaluate(LINE_WIDTHS)
            await element.evaluate(
                "(element, width) => element.style.width = `${width}%`", percentage,
            )
            after = await element.evaluate(LINE_WIDTHS)
            self.assertEqual(len(after), len(before))
            self.assertLessEqual(imbalance(after), imbalance(before) + 1e-8)
            for viewport in (400, 1000):
                await self.page.set_viewport_size({"width": viewport, "height": 900})
                self.assertEqual(await element.evaluate(LINE_WIDTHS), after)
                bounds = await element.bounding_box()
                self.assertIsNotNone(bounds)
                self.assertAlmostEqual(bounds["x"] + bounds["width"] / 2, 320, delta=0.1)

    async def editor(self, template: str = "arena") -> None:
        store = AsyncMock()
        store.get.return_value = DRAFT
        store.content.return_value = ([("title", "Poster title"), ("results", "<p>Results</p>")], [])
        with patch.object(posters, "store", return_value=store), patch.object(
            layout, "balance_header_widths", AsyncMock(return_value={"#title": 80, "#authors": 100}),
        ):
            rendered = await layout.layout(template, 11, uid=7)
        document = html.document_fromstring(rendered)
        for script in document.xpath("//script"):
            script.getparent().remove(script)
        for link in document.xpath("//link"):
            href = link.get("href", "")
            if href.startswith("/static/") and link.get("rel") == "stylesheet":
                style = html.Element("style")
                style.text = (ROOT / href.lstrip("/")).read_text()
                link.getparent().replace(link, style)
            else:
                link.getparent().remove(link)
        await self.page.set_content(html.tostring(document, encoding="unicode"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-zoom.js"))
        await self.page.wait_for_function(
            "() => document.getElementById('poster-stage').style.height !== ''",
        )

    async def dimensions(self) -> dict:
        return await self.page.evaluate("""() => {
            const viewport = document.getElementById("poster-container");
            const menu = document.querySelector(".basic-menu").getBoundingClientRect();
            const controls = document.getElementById("controls").getBoundingClientRect();
            const canvas = document.getElementById("poster-canvas").getBoundingClientRect();
            const bounds = viewport.getBoundingClientRect();
            return {
                top: bounds.top, bottom: bounds.bottom, width: bounds.width,
                menuBottom: menu.bottom, controlsTop: controls.top, controlsBottom: controls.bottom,
                canvasWidth: canvas.width, canvasHeight: canvas.height,
                scrollWidth: viewport.scrollWidth, clientWidth: viewport.clientWidth,
                scrollHeight: viewport.scrollHeight, clientHeight: viewport.clientHeight,
                bodyWidth: document.body.scrollWidth, bodyHeight: document.body.scrollHeight,
                sliderMin: Number(document.getElementById("poster-zoom").min),
            };
        }""")

    async def set_zoom(self, value: str) -> None:
        await self.page.locator("#poster-zoom").evaluate("""(slider, value) => {
            slider.value = value === "fit" ? slider.min : value;
            slider.dispatchEvent(new Event("input", {bubbles: true}));
        }""", value)

    async def test_editor_viewport_zoom_and_fit_for_all_templates(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                await self.page.set_viewport_size({"width": 900, "height": 700})
                await self.editor(template)
                full = await self.dimensions()
                self.assertAlmostEqual(full["canvasWidth"], DRAFT.inches[0] * 96, delta=0.1)
                self.assertAlmostEqual(full["canvasHeight"], DRAFT.inches[1] * 96, delta=0.1)
                self.assertAlmostEqual(full["top"], full["menuBottom"], delta=0.1)
                self.assertAlmostEqual(full["bottom"], full["controlsTop"], delta=0.1)
                self.assertAlmostEqual(full["controlsBottom"], 700, delta=0.1)
                self.assertAlmostEqual(full["width"], 900, delta=0.1)
                self.assertEqual(full["bodyWidth"], 900)
                self.assertEqual(full["bodyHeight"], 700)
                title_width = await self.page.locator("#title").evaluate("element => element.offsetWidth")
                await self.set_zoom("fit")
                fit = await self.dimensions()
                self.assertLessEqual(fit["scrollWidth"], fit["clientWidth"] + 1)
                self.assertLessEqual(fit["scrollHeight"], fit["clientHeight"] + 1)
                self.assertAlmostEqual(
                    fit["canvasWidth"] / full["canvasWidth"], fit["sliderMin"] / 100, delta=1e-6,
                )
                self.assertEqual(
                    await self.page.locator("#title").evaluate("element => element.offsetWidth"),
                    title_width,
                )
                await self.set_zoom("50")
                halfway = await self.dimensions()
                self.assertAlmostEqual(halfway["canvasWidth"], full["canvasWidth"] / 2, delta=0.1)
                await self.set_zoom("100")
                self.assertAlmostEqual(
                    (await self.dimensions())["canvasWidth"], full["canvasWidth"], delta=0.1,
                )

    async def test_fit_tracks_viewport_menu_and_content_resizing(self) -> None:
        await self.editor()
        await self.set_zoom("fit")
        for width, height in ((360, 500), (1100, 800)):
            await self.page.set_viewport_size({"width": width, "height": height})
            await self.page.wait_for_timeout(100)
            dimensions = await self.dimensions()
            self.assertAlmostEqual(dimensions["top"], dimensions["menuBottom"], delta=0.1)
            self.assertAlmostEqual(dimensions["bottom"], dimensions["controlsTop"], delta=0.1)
            self.assertLessEqual(dimensions["scrollWidth"], dimensions["clientWidth"] + 1)
            self.assertLessEqual(dimensions["scrollHeight"], dimensions["clientHeight"] + 1)
        previous = (await self.dimensions())["sliderMin"]
        await self.page.locator("#layout").evaluate("element => element.style.height = '6000px'")
        await self.page.wait_for_timeout(100)
        dimensions = await self.dimensions()
        self.assertAlmostEqual(dimensions["sliderMin"], previous, delta=0.01)
        self.assertLessEqual(dimensions["scrollHeight"], dimensions["clientHeight"] + 1)
        await self.assert_content_bounds()

    async def assert_content_bounds(self) -> None:
        bounds = await self.page.evaluate("""() => {
            const canvas = document.getElementById("poster-canvas");
            const content = document.getElementById("poster-content");
            const poster = canvas.getBoundingClientRect();
            const scale = poster.width / canvas.offsetWidth;
            const style = getComputedStyle(canvas);
            const area = {
                left: poster.left + parseFloat(style.paddingLeft) * scale,
                top: poster.top + parseFloat(style.paddingTop) * scale,
                right: poster.right - parseFloat(style.paddingRight) * scale,
                bottom: poster.bottom - parseFloat(style.paddingBottom) * scale,
            };
            const rectangles = [content, ...content.querySelectorAll("*")]
                .map(element => element.getBoundingClientRect())
                .filter(rect => rect.width && rect.height);
            return {
                area, padding: parseFloat(style.paddingLeft),
                gutter: parseFloat(getComputedStyle(document.getElementById("layout")).gap),
                left: Math.min(...rectangles.map(rect => rect.left)),
                top: Math.min(...rectangles.map(rect => rect.top)),
                right: Math.max(...rectangles.map(rect => rect.right)),
                bottom: Math.max(...rectangles.map(rect => rect.bottom)),
            };
        }""")
        self.assertAlmostEqual(bounds["padding"], 2 * bounds["gutter"], delta=0.1)
        for side in ("left", "top"):
            self.assertGreaterEqual(bounds[side], bounds["area"][side] - 1)
        for side in ("right", "bottom"):
            self.assertLessEqual(bounds[side], bounds["area"][side] + 1)

    async def test_poster_margins_and_vertical_sections_fill_available_height(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                await self.editor(template)
                await self.assert_content_bounds()
                sections = await self.page.locator(
                    ".layout .pane:not(.horizontal), .layout .section-parent.vertical",
                ).evaluate_all(
                    """panes => panes.map(pane => {
                        const children = [...pane.children];
                        const gutter = parseFloat(getComputedStyle(pane).gap);
                        return {
                            height: pane.clientHeight,
                            occupied: children.reduce((sum, child) => sum + child.offsetHeight, 0)
                                + Math.max(0, children.length - 1) * gutter,
                        };
                    })""",
                )
                for section in sections:
                    self.assertAlmostEqual(section["height"], section["occupied"], delta=2)
                layout_bottom = await self.page.locator("#layout").evaluate(
                    "element => element.getBoundingClientRect().bottom",
                )
                content_bottom = await self.page.locator("#poster-content").evaluate(
                    "element => element.getBoundingClientRect().bottom",
                )
                self.assertAlmostEqual(layout_bottom, content_bottom, delta=1)

    async def test_overflowing_content_shrinks_without_changing_poster_dimensions(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                await self.editor(template)
                await self.page.locator("#results .content").evaluate(
                    "(element, text) => element.textContent = text",
                    "Detailed experimental results and observations. " * 2500,
                )
                await self.page.wait_for_function(
                    "() => document.getElementById('poster-content').style.transform !== 'scale(1)'",
                )
                await self.assert_content_bounds()
                larger_overflows = await self.page.locator("#poster-content").evaluate("""element => {
                    const width = element.style.width;
                    const height = element.style.height;
                    element.style.width = `${parseFloat(width) / 1.01}px`;
                    element.style.height = `${parseFloat(height) / 1.01}px`;
                    const overflows = element.scrollWidth > element.clientWidth
                        || element.scrollHeight > element.clientHeight;
                    element.style.width = width;
                    element.style.height = height;
                    return overflows;
                }""")
                self.assertTrue(larger_overflows)
                dimensions = await self.dimensions()
                self.assertAlmostEqual(dimensions["canvasWidth"], DRAFT.inches[0] * 96, delta=0.1)
                self.assertAlmostEqual(dimensions["canvasHeight"], DRAFT.inches[1] * 96, delta=0.1)
                await self.set_zoom("fit")
                await self.assert_content_bounds()
                await self.page.locator("#results .content").evaluate(
                    "element => element.textContent = 'Results'",
                )
                await self.page.wait_for_function(
                    "() => document.getElementById('poster-content').style.transform === 'scale(1)'",
                )

    async def test_header_measurement_handles_nested_zoom_canvas(self) -> None:
        await self.editor()
        await self.set_zoom("fit")
        widths = await balance_header_widths(await self.page.content(), DRAFT.inches[0] * 96)
        self.assertEqual(widths, {"#title": 100, "#authors": 100})

    async def test_header_measurement_uses_the_same_margins_as_the_editor(self) -> None:
        await self.editor()
        await self.page.locator("#poster-canvas").evaluate("""element => {
            element.style.width = "900px";
            element.style.height = "1400px";
        }""")
        await self.page.locator("#title").evaluate("""element => {
            element.style.width = "100%";
            element.textContent = "A detailed investigation of cells and their response to treatment "
                + "with experimental observations and implications for future research";
        }""")
        await self.page.wait_for_timeout(100)
        widths = await balance_header_widths(await self.page.content(), 900)
        expected = await balance_text_width(self.page, "#title")
        self.assertAlmostEqual(widths["#title"], expected, delta=0.01)

    async def test_images_and_unbroken_text_fit_with_custom_gutters(self) -> None:
        for template, width, height in (
            ("arena", 1400, 900), ("central", 1000, 1000), ("vertical", 900, 1400),
        ):
            with self.subTest(template=template):
                await self.editor(template)
                await self.page.locator("#poster-canvas").evaluate(
                    """(element, size) => {
                        element.style.width = `${size[0]}px`;
                        element.style.height = `${size[1]}px`;
                        element.style.setProperty("--gutter", "1rem");
                    }""", [width, height],
                )
                await self.page.locator("#results .content").evaluate("""element => {
                    element.textContent = "Observation".repeat(300);
                    const image = document.createElement("img");
                    image.style.width = "5000px";
                    image.style.height = "2000px";
                    image.alt = "Large research image";
                    element.append(image);
                }""")
                await self.page.wait_for_function(
                    "() => document.getElementById('poster-content').style.transform !== 'scale(1)'",
                )
                await self.assert_content_bounds()
                dimensions = await self.dimensions()
                self.assertAlmostEqual(dimensions["canvasWidth"], width, delta=0.1)
                self.assertAlmostEqual(dimensions["canvasHeight"], height, delta=0.1)

    async def test_small_poster_is_not_enlarged_and_slider_is_disabled(self) -> None:
        await self.editor()
        await self.page.locator("#poster-canvas").evaluate("""element => {
            element.style.width = "300px";
            element.style.height = "250px";
            element.querySelector("#layout").style.display = "none";
        }""")
        await self.page.wait_for_function(
            "() => document.getElementById('poster-zoom').disabled",
        )
        self.assertEqual((await self.dimensions())["sliderMin"], 100)
        self.assertAlmostEqual((await self.dimensions())["canvasWidth"], 300, delta=0.1)
