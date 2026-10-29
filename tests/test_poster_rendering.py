import unittest
from pathlib import Path

from playwright.async_api import Error as PlaywrightError, async_playwright

from poster_rendering import balance_header_widths, balance_text_width

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
