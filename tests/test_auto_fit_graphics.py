import base64
import os
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import cv2
import numpy
from playwright.async_api import async_playwright

import magic
import posters
from tests.test_auto_fit import profile, section
from tests.test_layout import DRAFT

ROOT = Path(__file__).resolve().parent.parent


def graphic(placement="left-middle", width=100, src="/layout/source?sid=5"):
    return (
        f'<span class="graphic" contenteditable="false" data-sid="5" data-type="png" '
        f'data-placement="{placement}" data-width="{width}" style="width:{width}px">'
        f'<img src="{src}" alt="Figure"></span>'
    )


def words(count):
    return "<p>" + " ".join(["word"] * count) + "</p>"


class GraphicMarkupTests(unittest.TestCase):
    def test_split_and_compose_preserve_proportional_position_and_resize(self):
        prior = f"<p>One</p>{graphic()}<p>Two</p><p>Three &lt;x&gt;</p>"
        text, node, anchor = magic._split_graphic(prior)
        self.assertEqual(text, "<p>One</p><p>Two</p><p>Three &lt;x&gt;</p>")
        self.assertAlmostEqual(anchor, 1 / 3)
        revision = magic._compose("<p>A</p><p>B</p><p>C</p>", node, anchor, 333)
        self.assertEqual(revision, "<p>A</p>" + graphic(width=333) + "<p>B</p><p>C</p>")
        self.assertEqual(magic._strip_graphics(revision), "<p>A</p><p>B</p><p>C</p>")
        self.assertEqual(magic._graphic_count(revision), 1)

    def test_top_and_bottom_placements_stay_at_the_container_edges(self):
        for placement, expected in (("top-center", 0.0), ("bottom-right", 1.0)):
            _, node, anchor = magic._split_graphic(f"<p>A</p><p>B</p>{graphic(placement)}<p>C</p>")
            self.assertEqual(anchor, expected)
            revision = magic._compose("<p>X</p><p>Y</p>", node, anchor, None)
            self.assertEqual(revision.startswith('<span class="graphic"'), placement.startswith("top"))

    def test_graphic_inside_a_paragraph_is_extracted_without_losing_text(self):
        text, _, anchor = magic._split_graphic(f"<p>Lead</p><p>{graphic()}Body</p>")
        self.assertEqual(text, "<p>Lead</p><p>Body</p>")
        self.assertEqual(anchor, 0.5)
        text, _, _ = magic._split_graphic(f"<p>{graphic()}</p><p>Body</p>")
        self.assertEqual(text, "<p>Body</p>")

    def test_only_one_graphic_can_be_arranged(self):
        with self.assertRaises(ValueError):
            magic._split_graphic(graphic() + graphic("right-middle"))

    def test_discernible_size_uses_visual_angle_at_viewing_distance(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertAlmostEqual(magic._discernible_short_side() / 96, 3.144, places=3)
        with patch.dict(os.environ, {"GRAPHIC_VIEWING_DISTANCE": "72"}):
            self.assertAlmostEqual(magic._discernible_short_side() / 96, 6.287, places=3)
        with patch.dict(os.environ, {"AUTOFIT_TARGET_OCCUPIED_PERCENTAGE": "50"}), \
                self.assertRaises(ValueError):
            magic._occupancy()


class GraphicFittingBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page(viewport={"width": 1400, "height": 1000})
        _, encoded = cv2.imencode(".png", numpy.full((800, 1600, 3), 128, numpy.uint8))
        self.data = "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode()
        self.print = patch("builtins.print")
        self.print.start()
        self.addCleanup(self.print.stop)

    async def load(self, prior, *, width=1000, height=800, placement="left-middle"):
        await self.page.set_content(f"""<!DOCTYPE html>
            <style>
                .section {{ box-sizing: border-box; width: {width}px; height: {height}px; padding: 20px;
                           overflow-y: scroll; font: 16px/24px sans-serif; }}
                .header {{ height: 25px; }}
                .content p {{ margin: 0 0 8px; }}
            </style>
            <div class="section" id="results"><div class="header">Results</div>
                <div class="content" contenteditable="true" data-section="results"></div></div>
        """)
        await self.page.add_style_tag(path=str(ROOT / "static/css/graphics.css"))
        live = magic._compose(prior, magic._split_graphic(graphic(placement))[1], 0.0, 100)
        await self.page.locator(".content").evaluate(
            "(element, value) => element.innerHTML = value", live.replace("/layout/source?sid=5", self.data),
        )
        await self.page.locator("img").evaluate("image => image.decode()")
        return graphic(placement) + prior

    async def fit(self, saved, source=None, cached=()):
        with patch.object(magic, "_shorten", AsyncMock(
            side_effect=lambda text, name, count: words(count),
        )) as shorten, patch.object(magic, "_clarity", AsyncMock(return_value="excellent")) as clarity:
            result = await magic._fit_graphic_section(
                self.page, "results", source or saved, saved, list(cached),
            )
        return result, shorten, clarity

    async def assert_fits(self, revision):
        measured = await magic._measure_container(self.page, "results")
        self.assertFalse(measured["overflow"])
        self.assertGreaterEqual(measured["utilization"], .6)
        width = int(revision.split('data-width="')[1].split('"')[0])
        self.assertGreaterEqual(width, magic._discernible_short_side() * 2)
        self.assertEqual(measured["graphic_width"], width)
        self.assertEqual(await self.page.locator("img").evaluate("image => image.src.startsWith('data:')"), True)
        return measured

    async def test_sparse_section_enlarges_graphic_without_rewriting_text(self):
        saved = await self.load(words(150))
        self.assertLess((await magic._measure_container(self.page, "results"))["utilization"], .6)
        (revision, note), shorten, clarity = await self.fit(saved)
        self.assertIsNotNone(revision)
        self.assertIsNone(note)
        shorten.assert_not_awaited()
        clarity.assert_not_awaited()
        self.assertEqual(magic._strip_graphics(revision), words(150))
        await self.assert_fits(revision)

    async def test_overflowing_text_is_shortened_around_a_discernible_graphic(self):
        saved = await self.load(words(900), placement="top-center")
        self.assertTrue((await magic._measure_container(self.page, "results"))["overflow"])
        (revision, _), shorten, clarity = await self.fit(saved)
        self.assertIsNotNone(revision)
        shorten.assert_awaited()
        clarity.assert_awaited()
        self.assertLess(magic._words(magic._strip_graphics(revision)), 900)
        self.assertTrue(revision.startswith('<span class="graphic"'))
        await self.assert_fits(revision)

    async def test_longer_cached_sizing_is_preferred_over_new_rewrites(self):
        saved = await self.load(words(900), placement="right-middle")
        cached = [graphic("right-middle") + words(250)]
        (revision, _), shorten, _ = await self.fit(saved, cached=cached)
        self.assertEqual(magic._words(magic._strip_graphics(revision)), 250)
        shorten.assert_not_awaited()
        await self.assert_fits(revision)

    async def test_container_too_small_for_a_discernible_graphic_is_reported(self):
        saved = await self.load(words(20), width=500, height=400)
        (revision, note), shorten, _ = await self.fit(saved)
        self.assertIsNone(revision)
        self.assertIn("discernible from 3 feet", note)
        shorten.assert_not_awaited()


class GraphicAutoFitDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = AsyncMock()
        self.store.invalidate = Mock()
        self.store.get.return_value = DRAFT
        self.connection = self.store._connect.return_value
        self.connection.execute.return_value.fetchall.return_value = []
        self.connection.execute.return_value.fetchone.return_value = (42,)
        self.saved = {"results": graphic() + "<p>Original full text</p>", "introduction": "<p>Other text</p>"}
        self.page = Mock()
        self.page.locator.return_value = Mock(evaluate_all=AsyncMock(return_value=[]))
        self.measure = AsyncMock(return_value={"overflow": True, "utilization": .9})
        self.graphic_fit = AsyncMock(return_value=(graphic(width=640) + "<p>Short text</p>", None))
        self.text_fit = AsyncMock(return_value="<p>Longer text again</p>")

        @asynccontextmanager
        async def page(*args, **kwargs):
            yield self.page

        for mocked in (
            patch.object(posters, "store", return_value=self.store),
            patch.object(magic, "_fit_snapshot", AsyncMock(return_value=("poster", "settings", self.saved))),
            patch.object(magic, "_poster_page", page),
            patch.object(magic, "_render_section", AsyncMock()),
            patch.object(magic, "_measure_container", self.measure),
            patch.object(magic, "_fit_graphic_section", self.graphic_fit),
            patch.object(magic, "_fit_section", self.text_fit),
            patch.object(magic, "_profile_page", AsyncMock(return_value=profile(
                results=section(), introduction=section(),
            ))),
            patch.dict(os.environ, {"OPENAI_API_KEY": "test"}),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    async def test_graphic_sections_are_fitted_instead_of_cribbed(self):
        result = await magic.auto_fit(11, ["results"], uid=7)
        self.graphic_fit.assert_awaited_once()
        self.assertEqual(result["cribbed"], [])
        self.assertEqual(result["command"]["payload"]["revisions"]["results"],
                         graphic(width=640) + "<p>Short text</p>")
        self.assertEqual(result["command"]["payload"]["expected"]["results"], self.saved["results"])
        insertion = next(call for call in self.connection.execute.await_args_list
                         if "INSERT INTO sizings" in call.args[0])
        self.assertEqual(insertion.args[1][2], 2)

    async def test_fitting_graphic_sections_are_left_alone(self):
        self.measure.return_value = {"overflow": False, "utilization": .7}
        result = await magic.auto_fit(11, ["results"], uid=7)
        self.graphic_fit.assert_not_awaited()
        self.assertIn("already fit", result["say"])

    async def test_impossible_graphic_fit_restores_prior_and_explains(self):
        self.graphic_fit.return_value = (None, "<b>too large</b>")
        result = await magic.auto_fit(11, ["results"], uid=7)
        self.assertEqual(result["cribbed"], ["results"])
        self.assertIn("too large", result["say"])
        magic._render_section.assert_awaited_with(self.page, "results", self.saved["results"])

    async def test_multiple_graphics_in_an_overflowing_section_need_manual_adjustment(self):
        self.saved["results"] = graphic() + graphic("right-middle") + "<p>Text</p>"
        with patch.object(magic, "_profile_page", AsyncMock(return_value=profile(
            results=section(overflow=100), introduction=section(),
        ))):
            result = await magic.auto_fit(11, ["results"], uid=7)
        self.assertEqual(result["cribbed"], ["results"])
        self.assertIn("only one graphic", result["say"])
        self.graphic_fit.assert_not_awaited()

    async def test_sparse_text_prefers_longer_cached_sizing_then_original(self):
        self.saved["introduction"] = "<p>Short intro</p>"
        self.connection.execute.return_value.fetchall.return_value = [
            ("introduction", "<p>Short intro</p>", "<p>Full original introduction text</p>", "hash"),
            ("introduction", "<p>Longer intro text</p>", "<p>Full original introduction text</p>", "hash"),
        ]
        with patch.object(magic.hashlib, "sha256", return_value=Mock(hexdigest=Mock(return_value="hash"))), \
                patch.object(magic, "_profile_page", AsyncMock(return_value=profile(
                    results=section(), introduction=section(height=30),
                ))):
            self.text_fit.side_effect = [None, "<p>Full original introduction text</p>"]
            result = await magic.auto_fit(11, ["introduction"], uid=7)
        first, second = self.text_fit.await_args_list
        self.assertEqual(first.kwargs, {"initial": ["<p>Longer intro text</p>"], "search": False})
        self.assertEqual(second.kwargs, {"initial": ["<p>Full original introduction text</p>"]})
        self.assertEqual(second.args[2], "<p>Full original introduction text</p>")
        self.assertEqual(result["command"]["payload"]["revisions"],
                         {"introduction": "<p>Full original introduction text</p>"})

    async def test_sparse_text_without_a_fitting_revision_suggests_more_content(self):
        self.saved["introduction"] = "<p>Short intro</p>"
        self.connection.execute.return_value.fetchall.return_value = [
            ("introduction", self.saved["introduction"], "<p>Full original introduction text</p>", "hash"),
        ]
        self.text_fit.return_value = None
        with patch.object(magic, "_profile_page", AsyncMock(return_value=profile(
            results=section(), introduction=section(height=30),
        ))):
            result = await magic.auto_fit(11, ["introduction"], uid=7)
        self.assertEqual(result["cribbed"], ["introduction"])
        self.assertIn(
            "Introduction is too short. Consider adjusting the poster&rsquo;s font size or a smaller poster.",
            result["say"],
        )
        self.assertEqual(result["command"]["payload"]["revisions"], {})
        magic._render_section.assert_awaited_with(self.page, "introduction", self.saved["introduction"])

    async def test_sparse_text_at_original_length_is_not_rewritten(self):
        with patch.object(magic, "_profile_page", AsyncMock(return_value=profile(
            results=section(), introduction=section(height=30),
        ))):
            result = await magic.auto_fit(11, ["introduction"], uid=7)
        self.text_fit.assert_not_awaited()
        self.assertIn("already fit", result["say"])


class SparseProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_lists_sections_more_than_forty_percent_empty(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content("""
                <style>.section {width:200px;height:200px;overflow:hidden;font:10px/10px monospace}
                       p {margin:0}</style>
                <div class="section" id="introduction"><div class="content"><p>Short</p></div></div>
                <div class="section" id="results"><div class="content"><p>""" + "word " * 330 + """</p></div></div>
            """)
            with patch.dict(os.environ, {}, clear=True):
                measured = await magic._profile_page(page)
            await browser.close()
        self.assertEqual(measured["sparse_sections"], ["introduction"])


class EnqueueBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_requested_section_not_flagged_by_profile_is_fitted_on_request(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content("""
                <div id="layout" data-pid="11"><div class="section" id="results">
                    <div class="content" contenteditable="true" data-section="results"><p>Text</p></div>
                </div></div><output id="content-save-status"></output>
            """)
            await page.evaluate("""() => {
                window.requests = [];
                window.messages = [];
                window.wikkit = {say: message => messages.push(message)};
                window.fetch = async (url, options) => {
                    requests.push({url, body: options?.body ? JSON.parse(options.body) : null});
                    const body = url.endsWith("/start") ? {ok: true, run_id: "run"}
                        : {ok: true, say: "<p>Already fits.</p>", cribbed: [], sizings: {}};
                    return {ok: true, json: async () => body};
                };
            }""")
            await page.add_script_tag(path=str(ROOT / "static/javascript/layout-content.js"))
            await page.evaluate("window.autoFitPoster(['results'])")
            await page.wait_for_function("messages.length === 1")
            requests = await page.evaluate("requests")
            await browser.close()
        self.assertEqual([request["url"] for request in requests],
                         ["/magic/11/auto-fit/start", "/magic/11/auto-fit"])
        self.assertEqual(requests[1]["body"], {"run_id": "run", "sections": ["results"]})
