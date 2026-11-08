import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from lxml import html
from playwright.async_api import async_playwright
from starlette.requests import HTTPConnection

import layout
import magic
import posters
from tests.test_layout import DRAFT


class ProfileMeasurementsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch()
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()

    async def test_section_scroll_metrics_include_text_and_graphics_separately(self) -> None:
        await self.page.set_content("""
            <style>
                .section { width: 100px; height: 100px; overflow: hidden; }
                #text { width: 120px; height: 150px; }
                #graphic { width: 40px; height: 25px; }
            </style>
            <div class="section" id="introduction"><div id="text">Text</div></div>
            <div class="section" id="results"><div class="graphic" id="graphic"></div></div>
        """)
        with patch.dict("os.environ", {}, clear=True):
            profile = await magic._profile_page(self.page)
        introduction = profile["sections"]["introduction"]
        results = profile["sections"]["results"]
        self.assertEqual(introduction["area"], 10000)
        self.assertEqual(introduction["occupied"], 18000)
        self.assertEqual(introduction["density"], 1.8)
        self.assertEqual(introduction["vertical_overflow"], 100 / 150)
        self.assertEqual(introduction["graph_area"], 0)
        self.assertEqual(introduction["graph_density"], 0)
        self.assertEqual(results["occupied"], 10000)
        self.assertEqual(results["density"], 1)
        self.assertEqual(results["vertical_overflow"], 1)
        self.assertEqual(results["graph_area"], 1000)
        self.assertEqual(results["graph_density"], 0.1)
        self.assertEqual(profile["total_area"], 20000)
        self.assertEqual(profile["occupied_area"], 28000)
        self.assertEqual(profile["density"], 1.4)
        self.assertEqual(profile["overflowing_sections"], ["introduction", "results"])
        self.assertEqual(profile["underflowing_sections"], [])

    async def test_measures_graphics_and_aggregates_all_sections(self) -> None:
        await self.page.set_content("""
            <style>
              .section { width: 100px; height: 100px; }
              .graphic { display: block; }
              #tiny { width: 5px; height: 5px; }
              #wide { width: 34px; height: 40px; }
              #results-graphic { width: 30px; height: 50px; }
              #big { width: 110px; height: 100px; }
            </style>
            <div style="transform: scale(.5); transform-origin: top left">
              <div id="introduction" class="section">
                <span id="tiny" class="graphic"></span><span id="wide" class="graphic"></span>
              </div>
              <div id="results" class="section"><span id="results-graphic" class="graphic"></span></div>
              <div id="conclusions" class="section"><span id="big" class="graphic"></span></div>
            </div>
        """)
        with patch.dict("os.environ", {}, clear=True):
            profile = await magic._profile_page(self.page)
        self.assertEqual(profile["total_area"], 30000)
        self.assertEqual(profile["occupied_area"], 31000)
        self.assertAlmostEqual(profile["density"], profile["occupied_area"] / 30000)
        self.assertEqual(profile["underflowing_sections"], [])
        self.assertEqual(profile["overflowing_sections"], ["introduction", "results", "conclusion"])
        intro = profile["sections"]["introduction"]
        self.assertEqual(intro["fit"], ">")
        self.assertEqual(intro["graph_area"], 25 + 1360)
        self.assertAlmostEqual(intro["graph_density"], 1385 / 10000)
        self.assertTrue(intro["graphics"][0]["underflow"])
        self.assertEqual(intro["graphics"][1]["width"], 34)
        self.assertTrue(intro["graphics"][1]["too_wide"])
        self.assertTrue(intro["graphics"][1]["too_tall"])
        self.assertFalse(profile["sections"]["results"]["graphics"][0]["too_tall"])
        self.assertTrue(profile["sections"]["conclusion"]["overflow"])
        self.assertEqual(profile["sections"]["conclusion"]["fit"], ">")
        self.assertEqual([item["id"] for item in profile["oversized_graphics"]], ["wide", "big"])
        json.dumps(profile)

    async def test_thresholds_are_inclusive_and_configurable(self) -> None:
        await self.page.set_content("""
            <style>.section { width:100px; height:100px }
            .graphic { width:50px; height:60px }</style>
            <div class="section" id="results"><div class="graphic" id="g"></div></div>
        """)
        with patch.dict("os.environ", {
            "SECTION_MIN_OCCUPIED": "100", "SECTION_MAX_OCCUPIED": "100",
            "SECTION_UNDERFLOW_OCCUPIED": "30", "TOO_WIDE": "0.5",
            "EXTREMELY_TALL": "0.6",
        }, clear=True):
            profile = await magic._profile_page(self.page)
        section = profile["sections"]["results"]
        self.assertEqual(section["fit"], "=")
        self.assertFalse(section["graphics"][0]["underflow"])
        self.assertFalse(section["graphics"][0]["oversized"])
        self.assertEqual(profile["overflowing_sections"], [])

    async def test_fit_classifies_scroll_density_at_thresholds(self) -> None:
        await self.page.set_content("""
            <style>
                .section { box-sizing: border-box; width: 100px; height: 100px; overflow: hidden; }
                #below { border-left: 51px solid; }
                #equal { border-left: 50px solid; }
                #above { border-left: 49px solid; }
            </style>
            <div class="section" id="below"></div>
            <div class="section" id="equal"></div>
            <div class="section" id="above"></div>
        """)
        with patch.dict("os.environ", {
            "SECTION_MIN_OCCUPIED": "50", "SECTION_MAX_OCCUPIED": "50",
        }, clear=True):
            profile = await magic._profile_page(self.page)
        self.assertEqual(
            [profile["sections"][name]["density"] for name in ("below", "equal", "above")],
            [0.49, 0.5, 0.51],
        )
        self.assertEqual(
            [profile["sections"][name]["fit"] for name in ("below", "equal", "above")],
            ["<", "=", ">"],
        )
        self.assertEqual(profile["underflowing_sections"], ["below"])
        self.assertEqual(profile["overflowing_sections"], ["above"])

    async def test_empty_section_and_nested_clipping_use_section_scroll_box(self) -> None:
        await self.page.set_content("""
            <style>
                .section { width: 100px; height: 100px; overflow: hidden; }
                .clipped { width: 100px; height: 100px; overflow: hidden; }
                .long { width: 100px; height: 200px; }
            </style>
            <div class="section" id="empty"></div>
            <div class="section" id="nested"><div class="clipped"><div class="long">Text</div></div></div>
        """)
        with patch.dict("os.environ", {}, clear=True):
            profile = await magic._profile_page(self.page)
        for name in ("empty", "nested"):
            section = profile["sections"][name]
            self.assertEqual(section["occupied"], 10000)
            self.assertEqual(section["density"], 1)
            self.assertEqual(section["vertical_overflow"], 1)
            self.assertEqual(section["fit"], ">")

    async def test_invalid_layout_and_settings_fail(self) -> None:
        for markup in ("", '<div class="section" id="x" style="width:0;height:0"></div>',
                       '<div class="section" id="x"></div><div class="section" id="x"></div>'):
            await self.page.set_content(markup)
            with self.subTest(markup=markup), self.assertRaises(ValueError):
                await magic._profile_page(self.page)
        await self.page.set_content(
            '<div class="section" id="x" style="box-sizing:border-box;width:100px;'
            'height:100px;border:50px solid"></div>',
        )
        with self.assertRaisesRegex(ValueError, "positive scroll height"):
            await magic._profile_page(self.page)
        with patch.dict("os.environ", {"SECTION_MIN_OCCUPIED": "nan"}), self.assertRaises(ValueError):
            magic._thresholds()
        with patch.dict("os.environ", {"SECTION_MIN_OCCUPIED": "80", "SECTION_MAX_OCCUPIED": "75"}):
            with self.assertRaises(ValueError):
                magic._thresholds()

    async def test_saved_layout_renders_geometry_and_sizing_without_writes(self) -> None:
        store = AsyncMock()
        store.get.return_value = replace(DRAFT, layout_settings={"arena": {"left": 0.1}})
        store.content.return_value = ([("results", "<p>Results</p>")], [])
        store.conference_details.return_value = ("", {})
        with patch.object(posters, "store", return_value=store), patch.object(
            layout, "balance_header_widths", AsyncMock(return_value={"#title": 100, "#authors": 100}),
        ):
            rendered = await layout.layout("arena", 11, uid=7)
            document = html.document_fromstring(rendered)
            for link in document.xpath('//link[contains(@href, "fonts.googleapis.com")]'):
                link.getparent().remove(link)
            saved_sizing = document.get_element_by_id("layout").get("data-resize-settings")
            with patch.object(magic.layout, "layout", AsyncMock(return_value=html.tostring(
                document, encoding="unicode",
            ))):
                profile = await magic.profile_poster(11, uid=7)
            sizing = json.loads(saved_sizing)
            sizing["left"] = 0
            document.get_element_by_id("layout").set("data-resize-settings", json.dumps(sizing))
            with patch.object(magic.layout, "layout", AsyncMock(return_value=html.tostring(
                document, encoding="unicode",
            ))):
                baseline = await magic.profile_poster(11, uid=7)
        self.assertIn("results", profile["sections"])
        self.assertIn("conclusion", profile["sections"])
        self.assertTrue(all(section["graph_area"] == 0 for section in profile["sections"].values()))
        self.assertTrue(all(section["density"] > 0 for section in profile["sections"].values()))
        self.assertGreater(
            profile["sections"]["introduction"]["area"],
            baseline["sections"]["introduction"]["area"],
        )
        self.assertLess(profile["sections"]["results"]["area"], baseline["sections"]["results"]["area"])
        store.get.assert_awaited_with(7, 11)
        store.set_layout_geometry.assert_not_awaited()
        store.set_layout_sizing.assert_not_awaited()


class ProfileRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.uid = 7

        async def identity(connection: HTTPConnection) -> None:
            connection.state.user_id = self.uid

        app = FastAPI(dependencies=[Depends(identity)])
        app.include_router(magic.router)
        self.client = TestClient(app)
        self.store = AsyncMock()
        self.store.get.return_value = None
        mocked = patch.object(posters, "store", return_value=self.store)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_owner_access_and_private_response(self) -> None:
        with patch.object(magic, "profile_poster", AsyncMock(return_value={"sections": {}})) as profile:
            response = self.client.get("/magic/11/profile")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        profile.assert_awaited_once_with(11, uid=7)

    def test_authentication_and_ownership(self) -> None:
        self.uid = None
        self.assertEqual(self.client.get("/magic/11/profile").status_code, 401)
        self.store.get.assert_not_awaited()
        self.uid = 7
        self.assertEqual(self.client.get("/magic/11/profile").status_code, 404)
        self.store.get.assert_awaited_once_with(7, 11)

    def test_missing_dimensions_and_failure_are_reported(self) -> None:
        self.store.get.return_value = replace(DRAFT, width=None)
        self.assertEqual(self.client.get("/magic/11/profile").status_code, 409)
        with patch.object(magic, "profile_poster", AsyncMock(side_effect=ValueError("invalid"))):
            with self.assertLogs("magic", "ERROR"):
                self.assertEqual(self.client.get("/magic/11/profile").status_code, 503)
