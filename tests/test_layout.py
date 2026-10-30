import asyncio
import os
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import psycopg
from psycopg import sql
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from lxml import html
from starlette.requests import HTTPConnection

import content
import layout as routes
import posters
from routers import wikkit

DRAFT = posters.Poster(11, "Research", None, 36, 48, None, None, False, None, units="in")


class LayoutRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.uid = 7
        self.session = {"poster_id": 11}
        self.store = AsyncMock()
        self.store.get.return_value = DRAFT
        self.store.content.return_value = (
            [("title", "Research"), ("materials and methods", "<h2>Methods</h2><p>Experiment</p>"),
             ("conclusion", "<p>Findings</p>"), ("literature cited", "<p>References</p>")],
            [("<Ada>", "Institute")],
        )
        self.store.conference_details.return_value = ("", {})
        self.store.set_dimensions.return_value = True
        mocked = patch.object(posters, "store", return_value=self.store)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(routes, "balance_header_widths", AsyncMock(return_value={
            "#title": 80.0, "#authors": 90.0,
        }))
        self.balancer = mocked.start()
        self.addCleanup(mocked.stop)

        async def identity(connection: HTTPConnection) -> None:
            connection.state.user_id = self.uid
            connection.state.user = {"user-name": "Ada"}
            connection.scope["session"] = self.session

        application = FastAPI(dependencies=[Depends(identity)])
        application.include_router(routes.router)
        self.client = TestClient(application)

    def test_selected_draft_renders_its_layout(self) -> None:
        response = self.client.get("/layout/layout")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="pane0"', response.text)
        self.store.get.assert_awaited_with(7, 11)
        self.store.content.assert_awaited_once_with(7, 11)
        document = html.fromstring(response.text)
        self.assertEqual(document.get_element_by_id("title").text_content(), "Research")
        self.assertEqual(document.get_element_by_id("authors").text_content(), "<Ada> (Institute)")
        self.assertIn("Experiment", document.get_element_by_id("methods").text_content())
        self.assertEqual(len(document.xpath('//*[@id="methods"]//h2')), 0)
        self.assertIn("Findings", document.get_element_by_id("conclusion").text_content())
        self.assertEqual(
            document.xpath('//*[@id="methods"]//div[@class="content"]/@contenteditable'),
            ["true"],
        )
        self.assertEqual(
            document.xpath('//*[@id="methods"]//div[@class="content"]/@data-section'),
            ["materials and methods"],
        )

    def test_section_content_is_saved_to_its_database_section(self) -> None:
        self.store.save_section.return_value = True
        response = self.client.post(
            "/layout/content",
            json={"section": "materials and methods", "content": "<p>Updated</p>"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.store.save_section.assert_awaited_once_with(
            7, 11, "materials and methods", "<p>Updated</p>",
        )

    def test_section_save_rejects_invalid_input_and_cross_origin_requests(self) -> None:
        for data in (
            {"section": "not a section", "content": "text"},
            {"section": "results", "content": 123},
            {"section": "results", "content": "x" * 100_001},
        ):
            with self.subTest(data=data):
                response = self.client.post("/layout/content", json=data)
                self.assertEqual(response.status_code, 422)
        response = self.client.post(
            "/layout/content",
            json={"section": "results", "content": "text"},
            headers={"Origin": "https://elsewhere.test"},
        )
        self.assertEqual(response.status_code, 403)
        self.store.save_section.assert_not_awaited()

    def test_editor_formatting_survives_saving_and_rendering(self) -> None:
        markup = (
            "<b>Bold</b> <i>Italic</i> <code>Code</code> <u>Underline</u> "
            "<strike>Strike</strike> <sup>Up</sup> <sub>Down</sub>"
        )
        self.store.save_section.return_value = True
        response = self.client.post(
            "/layout/content", json={"section": "results", "content": markup},
        )
        self.assertEqual(response.status_code, 200)
        self.store.save_section.assert_awaited_once_with(7, 11, "results", markup)
        self.store.content.return_value = ([("results", markup)], [])
        document = html.fromstring(self.client.get("/layout/edit").text)
        for tag in ("b", "i", "code", "u", "strike", "sup", "sub"):
            self.assertEqual(len(document.xpath(f'//*[@id="results"]//{tag}')), 1)

    def test_headers_include_measured_widths_for_every_template(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                self.store.get.return_value = replace(DRAFT, template=template)
                document = html.fromstring(self.client.get("/layout/edit").text)
                self.assertEqual(document.get_element_by_id("title").get("style"), "width: 80.000000%")
                self.assertEqual(document.get_element_by_id("authors").get("style"), "width: 90.000000%")
                self.assertAlmostEqual(self.balancer.await_args.args[1], DRAFT.inches[0] * 96)
                self.assertEqual(
                    document.get_element_by_id("poster-canvas").get("style"),
                    f"width: {DRAFT.inches[0] * 96:.6f}px; height: {DRAFT.inches[1] * 96:.6f}px",
                )
                self.assertIsNone(document.find("body").get("style"))
                self.assertTrue(document.xpath('//*[@id="poster-container"]//*[@id="poster-header"]'))
                self.assertTrue(document.xpath('//*[@id="poster-container"]//*[@id="layout"]'))
                self.assertEqual(document.get_element_by_id("controls").getparent().tag, "body")
                self.assertTrue(document.xpath('//script[@src="/static/javascript/layout-zoom.js"]'))

    def test_browser_render_failure_is_reported_explicitly(self) -> None:
        self.balancer.side_effect = routes.PlaywrightError("Chromium unavailable")
        with self.assertLogs("layout", "ERROR"):
            response = self.client.get("/layout/edit")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Your layout could not be loaded", response.text)

    def test_edit_route_selects_requested_owned_poster(self) -> None:
        self.store.get.return_value = replace(DRAFT, pid=12, template="central")
        response = self.client.get("/layout/edit?pid=12")
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="pane vertical"', response.text)
        self.assertIn('class="section-parent vertical"', response.text)
        self.assertEqual(self.session["poster_id"], 12)

    def test_saved_theme_and_fonts_are_applied_to_layout(self) -> None:
        self.store.get.return_value = replace(DRAFT, theme="blues", fonts="technical")
        document = html.fromstring(self.client.get("/layout/edit").text)
        self.assertEqual(document.find("body").get("class").split(), ["blues", "technical"])
        self.assertTrue(document.xpath('//link[@href="/static/css/themes.css"]'))
        self.assertTrue(document.xpath('//link[@href="/static/css/fonts.css"]'))

    def test_authors_are_comma_separated_below_the_centered_title(self) -> None:
        self.store.content.return_value = (
            [("title", "Research")],
            [("<Ada>", "Institute"), ("Grace", None), ("Lin", "University")],
        )
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                self.store.get.return_value = replace(DRAFT, template=template)
                document = html.fromstring(self.client.get("/layout/edit").text)
                authors = document.get_element_by_id("authors")
                self.assertEqual(authors.text_content(), "<Ada> (Institute), Grace, Lin (University)")
                self.assertFalse(authors.xpath(".//br"))
                self.assertIn("authors", authors.get("class").split())
                title = document.get_element_by_id("title")
                self.assertIn("title", title.get("class").split())
                self.assertIs(title.getparent().getnext(), authors)

    def test_home_redirects_to_layout(self) -> None:
        response = self.client.get("/layout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/layout/layout")

    def test_requires_sign_in_and_a_selected_poster(self) -> None:
        self.uid = None
        response = self.client.get("/layout/layout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/account/signin")
        self.uid = 7
        self.session.clear()
        response = self.client.get("/layout/layout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/posters/list")
        self.store.get.assert_not_awaited()

    def test_purchased_poster_redirects_to_view(self) -> None:
        self.store.get.return_value = replace(DRAFT, purchased=True)
        response = self.client.get("/layout/layout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/posters/11/view")

    def test_grouped_sections_and_logo_are_rendered_into_a_fresh_template(self) -> None:
        self.store.get.return_value = replace(DRAFT, template="vertical", logo=b"image", logotype="png")
        first = self.client.get("/layout/edit").text
        self.assertIn('/layout/logo?pid=11', first)
        self.assertIn('id="citations-acknowledgements-information-parent"', first)
        self.assertIn('href="/static/css/layouts/vertical.css"', first)
        self.store.content.return_value = ([("title", "Changed")], [])
        second = self.client.get("/layout/edit").text
        self.assertNotIn("References", second)
        self.assertIn("Changed", second)

    def test_stored_section_html_is_sanitized(self) -> None:
        self.store.content.return_value = (
            [("title", '<img src=x onerror="alert(1)">'), ("results", '<script>alert(1)</script><p>Safe</p>')],
            [],
        )
        document = html.fromstring(self.client.get("/layout/edit").text)
        self.assertFalse(document.xpath('//*[@id="title"]//img | //*[@id="results"]//script'))
        self.assertIn("Safe", document.get_element_by_id("results").text_content())

    def test_removing_stored_heading_preserves_unwrapped_content(self) -> None:
        self.store.content.return_value = ([("results", "<h2>Results</h2>Plain findings<p>Details</p>")], [])
        document = html.fromstring(self.client.get("/layout/edit").text)
        body = document.get_element_by_id("results").find('.//div[@class="content"]')
        self.assertEqual(body.text_content(), "Plain findingsDetails")

    def test_binary_routes_return_exact_bytes_and_types(self) -> None:
        self.store.get.return_value = replace(DRAFT, logo=b"logo", logotype="jpg", moreinfopng=b"qr")
        for endpoint, expected, media_type in (
            ("logo", b"logo", "image/jpeg"), ("qrcode", b"qr", "image/png"),
        ):
            response = self.client.get(f"/layout/{endpoint}?pid=11")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, expected)
            self.assertEqual(response.headers["content-type"], media_type)
            self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_binary_routes_require_identity_ownership_and_content(self) -> None:
        for endpoint in ("logo", "qrcode"):
            self.assertEqual(self.client.get(f"/layout/{endpoint}?pid=11").status_code, 404)
            self.store.get.return_value = None
            self.assertEqual(self.client.get(f"/layout/{endpoint}?pid=11").status_code, 404)
            self.uid = None
            self.assertEqual(self.client.get(f"/layout/{endpoint}?pid=11").status_code, 401)
            self.uid = 7
            self.store.get.return_value = DRAFT
        self.store.get.return_value = replace(DRAFT, logo=b"x", logotype="text/html")
        with self.assertLogs("layout", "ERROR"):
            self.assertEqual(self.client.get("/layout/logo?pid=11").status_code, 415)

    def test_source_is_owner_scoped_and_returned_without_conversion(self) -> None:
        store = AsyncMock()
        store.source_file.return_value = (b"<svg/>", "image/svg+xml")
        with patch.object(content, "store", return_value=store):
            response = self.client.get("/layout/source?sid=22")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"<svg/>")
            self.assertEqual(response.headers["content-type"], "image/svg+xml")
            store.source_file.assert_awaited_once_with(7, 22)
            store.source_file.return_value = None
            self.assertEqual(self.client.get("/layout/source?sid=22").status_code, 404)
            self.uid = None
            self.assertEqual(self.client.get("/layout/source?sid=22").status_code, 401)

    def test_dimension_page_registers_workflow_and_hides_correction_until_invalid(self) -> None:
        response = self.client.get("/layout/dimensions")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-page="layout-dimensions"', response.text)
        self.assertIn('id="dimension-correction" hidden', response.text)
        self.assertIs(wikkit.WORKFLOWS["layout-dimensions"], routes.dimensions_workflow)

    def test_dimension_updates_store_centimeters_and_report_the_display_units(self) -> None:
        response = self.client.post("/layout/dimensions", json={"dimensions": "36 in x 48 in"})
        self.assertEqual(response.status_code, 200)
        self.store.set_dimensions.assert_awaited_once_with(7, 11, 121.92, 91.44, "in")
        self.assertEqual(response.json()["preview"], {"height": 36, "width": 48, "units": "in"})
        self.assertEqual(response.json()["redirect"], "/layout/edit")

    def test_dimension_updates_require_whole_numbers_for_integer_storage(self) -> None:
        response = self.client.post("/layout/dimensions", json={"dimensions": "36.5 in x 48 in"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("whole number using the selected units", " ".join(response.json()["errors"]))
        self.store.set_dimensions.assert_not_awaited()

    def test_dimension_route_uses_entered_units_for_limit_messages(self) -> None:
        self.store.conference_details.return_value = (
            "Meeting", {"maximum_poster_width": 121.92, "poster_units": "cm"},
        )
        for text, expected in (
            ("30 in x 36 in", "The minimum is 36 in."),
            ("36 in x 50 in", "The maximum is 48 in."),
        ):
            with self.subTest(text=text):
                response = self.client.post("/layout/dimensions", json={"dimensions": text})
                self.assertEqual(response.status_code, 422)
                self.assertIn(expected, " ".join(response.json()["errors"]))
        self.store.set_dimensions.assert_not_awaited()

    def test_dimensions_validate_minimum_and_maximum_without_saving(self) -> None:
        self.store.conference_details.return_value = (
            "Meeting", {"maximum_poster_height": 100, "maximum_poster_width": 150, "poster_units": "cm"},
        )
        for text, message in (
            ("100 x 90 cm", "width is too small"), ("110 x 150 cm", "height is too big"),
        ):
            response = self.client.post("/layout/dimensions", json={"dimensions": text})
            self.assertEqual(response.status_code, 422)
            self.assertIn(message, " ".join(response.json()["errors"]))
            self.assertIn("preview", response.json())
        self.store.set_dimensions.assert_not_awaited()

    def test_dimension_updates_enforce_required_sizes_and_purchased_guard(self) -> None:
        self.store.conference_details.return_value = (
            "Meeting", {"required_poster_height": 36, "required_poster_width": 48, "poster_units": "in"},
        )
        self.assertEqual(self.client.post("/layout/dimensions", json={"dimensions": "40 x 48"}).status_code, 422)
        self.assertEqual(self.client.post("/layout/dimensions", json={"dimensions": "36 x 48"}).status_code, 200)
        self.store.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.post("/layout/dimensions", json={"dimensions": "36 x 48"}).status_code, 409)

    def test_dimension_errors_are_explicit_and_requests_are_same_origin(self) -> None:
        self.assertEqual(self.client.post("/layout/dimensions", data="x").status_code, 415)
        self.assertEqual(self.client.post(
            "/layout/dimensions", json={}, headers={"Origin": "https://elsewhere.test"},
        ).status_code, 403)
        for value in ("hello", "0 x 100", "1e9 x 100", "100 ft x 100 ft", 100, None):
            self.assertEqual(self.client.post("/layout/dimensions", json={"dimensions": value}).status_code, 422)
        self.store.get.side_effect = psycopg.OperationalError("offline")
        with self.assertLogs("layout", "ERROR"):
            self.assertEqual(self.client.post("/layout/dimensions", json={"dimensions": "100 x 100"}).status_code, 503)


class DimensionValueTests(unittest.TestCase):
    def test_poster_displays_integer_dimensions_in_stored_units(self) -> None:
        for width, height, units, expected, centimeters in (
            (91, 122, "cm", "91 x 122 cm", (91, 122)),
            (36, 48, "in", "36 x 48 in", (91.44, 121.92)),
            (914, 1219, "mm", "914 x 1219 mm", (91.4, 121.9)),
        ):
            poster = replace(DRAFT, width=width, height=height, units=units)
            self.assertEqual(poster.display_dimensions, expected)
            self.assertEqual(poster.centimeters, centimeters)

    def test_units_and_axis_order(self) -> None:
        for value, units, expected in (
            ("36 in x 48 in", "cm", (121.92, 91.44, "in")),
            ("914.4 mm x 1219.2 mm", "cm", (121.92, 91.44, "mm")),
            ("100 x 150", "cm", (150, 100, "cm")),
            ("100 cm x 48 in", "cm", (121.92, 100, "in")),
            ("100 X 150 CM", "in", (150, 100, "cm")),
        ):
            width, height, display = routes.parse_dimensions(value, units)
            self.assertAlmostEqual(width, expected[0])
            self.assertAlmostEqual(height, expected[1])
            self.assertEqual(display, expected[2])

    def test_minimum_threshold_is_in_centimeters_and_partial_maxima_are_enforced(self) -> None:
        rules = routes.DimensionRules.from_details("Meeting", {"maximum_poster_width": 150})
        self.assertEqual(rules.errors(91.44, 91.44, "in"), [])
        self.assertTrue(rules.errors(91.43, 91.44))
        self.assertTrue(rules.errors(150.01, 100))
        self.assertEqual(rules.errors(150, 100), [])

    def test_dimension_limits_are_reported_in_display_units(self) -> None:
        rules = routes.DimensionRules.from_details("Meeting", {
            "maximum_poster_width": 48, "poster_units": "in",
        })
        for units, minimum, maximum in (
            ("cm", "91.44 cm", "121.92 cm"),
            ("mm", "914.4 mm", "1219.2 mm"),
            ("in", "36 in", "48 in"),
        ):
            with self.subTest(units=units):
                self.assertIn(f"The minimum is {minimum}.", " ".join(rules.errors(80, 100, units)))
                self.assertIn(f"The maximum is {maximum}.", " ".join(rules.errors(130, 100, units)))
        self.assertIn("The minimum is 36 in.", " ".join(rules.errors(80, 100)))

    def test_configured_maxima_apply_independently_to_each_axis(self) -> None:
        with patch.object(posters, "POSTER_MAX_WIDTH_CM", 121.92), patch.object(
            posters, "POSTER_MAX_HEIGHT_CM", 152.4,
        ):
            for details in ({}, {"maximum_poster_width": 300, "maximum_poster_height": 300}):
                with self.subTest(details=details):
                    rules = routes.DimensionRules.from_details("", details)
                    self.assertEqual(rules.errors(121.92, 152.4, "in"), [])
                    self.assertEqual(rules.errors(122, 153, "in"), [
                        "Poster width is too big. The maximum is 48 in.",
                        "Poster width must be a whole number using the selected units.",
                        "Poster height is too big. The maximum is 60 in.",
                        "Poster height must be a whole number using the selected units.",
                    ])
            rules = routes.DimensionRules.from_details("Meeting", {
                "maximum_poster_width": 100, "maximum_poster_height": 110,
            })
            self.assertEqual(rules.errors(101, 111), [
                "Poster width is too big. The maximum is 100 cm.",
                "Poster height is too big. The maximum is 110 cm.",
            ])

    def test_conference_dimension_pairs_and_singleton_lists(self) -> None:
        rules = routes.DimensionRules.from_details("Meeting", {
            "required_poster_dimensions": "48 x 36", "maximum_poster_height": [40], "poster_units": "inches",
        })
        self.assertTrue(rules.fixed)
        self.assertEqual((rules.required_width, rules.required_height, rules.max_height), (121.92, 91.44, 101.6))


class DimensionWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.store = AsyncMock()
        self.store.get.return_value = DRAFT
        self.store.conference_details.return_value = ("", {})
        self.store.set_dimensions.return_value = True
        mocked = patch.object(posters, "store", return_value=self.store)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.bot = AsyncMock()
        self.bot.websocket = HTTPConnection({
            "type": "websocket", "headers": [],
            "state": {"user_id": 7, "poster_id": 11, "user": {"user-name": "Ada"}},
        })

    async def test_required_dimensions_are_saved_before_confirmation(self) -> None:
        self.store.conference_details.return_value = ("<Meeting>", {
            "required_poster_width": 48, "required_poster_height": 36, "poster_units": "in",
        })
        self.bot.request_response.return_value = "ok"
        await routes.dimensions_workflow(self.bot)
        self.store.set_dimensions.assert_awaited_once_with(7, 11, 121.92, 91.44, "in")
        self.assertIn("&lt;Meeting&gt;", self.bot.request_response.await_args.args[0])
        self.assertEqual(self.bot.send.await_args.args[0]["command"]["payload"], "/layout/edit")

    async def test_accept_maximum_and_idk_use_cm_and_correct_display_units(self) -> None:
        self.store.conference_details.return_value = ("Meeting", {
            "maximum_poster_width": 48, "maximum_poster_height": 36, "poster_units": "in",
        })
        for reply in ("ok", "idk"):
            self.store.set_dimensions.reset_mock()
            self.bot.request_response.return_value = reply
            await routes.dimensions_workflow(self.bot)
            self.store.set_dimensions.assert_awaited_once_with(7, 11, 121.92, 91.44, "in")

    async def test_declining_maxima_allows_custom_dimensions(self) -> None:
        self.store.conference_details.return_value = ("Meeting", {
            "maximum_poster_width": 150, "maximum_poster_height": 150, "poster_units": "cm",
        })
        self.bot.request_response.side_effect = ["cancel", "140 x 100 cm"]
        await routes.dimensions_workflow(self.bot)
        self.store.set_dimensions.assert_awaited_once_with(7, 11, 100, 140, "cm")

    async def test_invalid_input_shows_correction_and_retries_until_valid(self) -> None:
        self.bot.request_response.side_effect = ["idk", "nonsense", "10 cm x 100 cm", "100 cm x 150 cm"]
        await routes.dimensions_workflow(self.bot)
        corrections = [
            call.args[0]["command"] for call in self.bot.send.await_args_list
            if call.args[0].get("command", {}).get("does") == "correct-dimensions"
        ]
        self.assertEqual(len(corrections), 2)
        self.assertEqual(corrections[-1]["payload"]["height"], 10)
        self.store.set_dimensions.assert_awaited_once_with(7, 11, 150, 100, "cm")

    async def test_database_failure_is_reported_without_redirect(self) -> None:
        self.store.get.side_effect = psycopg.OperationalError("offline")
        with self.assertLogs("layout", "ERROR"):
            await routes.dimensions_workflow(self.bot)
        self.assertIn("could not be saved", self.bot.send.await_args.args[0]["say"])


class LayoutStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_is_owner_scoped_bounded_and_invalidated_on_write(self) -> None:
        store = posters.PosterStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchone.return_value = (
            DRAFT.pid, DRAFT.name, DRAFT.description, DRAFT.width, DRAFT.height,
            DRAFT.template, DRAFT.theme, DRAFT.purchased, DRAFT.conference_date,
            DRAFT.resumption, DRAFT.units,
        )
        with patch.object(store, "_connect", AsyncMock(return_value=connection)) as connect:
            with patch.object(posters.time, "monotonic", return_value=10):
                self.assertEqual(await store.get(7, 11), DRAFT)
                self.assertEqual(await store.get(7, 11), DRAFT)
                self.assertEqual(connect.await_count, 1)
            with patch.object(posters.time, "monotonic", return_value=13):
                await store.get(7, 11)
                self.assertEqual(connect.await_count, 2)
                await store.get(8, 11)
                self.assertEqual(connect.await_count, 3)
                store.invalidate(8, 11)
                await store.get(8, 11)
                self.assertEqual(connect.await_count, 4)

    async def test_dimension_storage_is_owner_scoped_and_chooses_orientation(self) -> None:
        store = posters.PosterStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchone.return_value = (11,)
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            for width, height, template in (
                (150, 100, "arena"), (100, 150, "vertical"),
            ):
                self.assertTrue(await store.set_dimensions(7, 11, width, height, "cm"))
                statement, params = connection.execute.await_args.args
                self.assertIn("p.uid = %s AND p.pid = %s", statement)
                self.assertIn("NOT EXISTS", statement)
                self.assertEqual(params, (width, height, "cm", template, 7, 11))
            with self.assertRaisesRegex(ValueError, "whole numbers"):
                await store.set_dimensions(7, 11, 123.456789, 100, "cm")

    async def test_source_file_lookup_checks_owner_before_reading_storage(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchone.return_value = ("/stored/file", "image/png")
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), patch.object(
            content.SourceFiles, "read", return_value=b"image",
        ) as read:
            self.assertEqual(await store.source_file(7, 22), (b"image", "image/png"))
            statement, params = connection.execute.await_args.args
            self.assertIn("p.uid = %s AND s.sid = %s", statement)
            self.assertEqual(params, (7, 22))
            read.assert_called_once_with("/stored/file")
            connection.execute.return_value.fetchone.return_value = None
            self.assertIsNone(await store.source_file(8, 22))
            read.assert_called_once()


@unittest.skipUnless(os.environ.get("LAYOUT_TEST_DATABASE_URL"), "Set LAYOUT_TEST_DATABASE_URL for PostgreSQL tests")
class LayoutDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = "test_layout_" + uuid.uuid4().hex
        self.connection = psycopg.connect(os.environ["LAYOUT_TEST_DATABASE_URL"], autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(
            self.connection.execute,
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
        )
        self.connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        self.ddl = (Path(__file__).resolve().parent.parent / "schema/schema.sql").read_text()
        self.connection.execute(self.ddl)
        self.connection.execute(
            'INSERT INTO "user" (uid, name, username, email, password, pwd_updated, created, updated) '
            "VALUES (7, 'Ada', 'ada', 'ada@example.test', 'hash', now(), now(), now())"
        )
        self.pid = self.connection.execute(
            "INSERT INTO poster (uid, created) VALUES (7, now()) RETURNING pid",
        ).fetchone()[0]
        self.store = posters.PosterStore("LAYOUT_TEST_DATABASE_URL", self.schema)

    def test_new_posters_start_with_null_dimensions(self) -> None:
        poster = asyncio.run(self.store.get(7, self.pid))
        self.assertIsNone(poster.width)
        self.assertIsNone(poster.height)
        self.assertEqual(self.connection.execute(
            "INSERT INTO poster (uid, created) VALUES (7, now()) RETURNING width, height",
        ).fetchone(), (None, None))

    def test_persisted_dimensions_reach_rendering_and_cannot_modify_purchased_or_foreign_posters(self) -> None:
        async def exercise() -> None:
            self.assertTrue(await self.store.set_dimensions(7, self.pid, 150, 100, "mm"))
            poster = await self.store.get(7, self.pid)
            self.assertEqual(poster.centimeters, (15, 10))
            self.assertEqual((poster.width, poster.height), (150, 100))
            self.assertEqual((poster.template, poster.units), ("arena", "mm"))
            with patch.object(posters, "store", return_value=self.store):
                rendered = await routes.layout("arena", self.pid, uid=7)
                self.assertIn('id="pane0"', rendered)
            self.assertFalse(await self.store.set_dimensions(8, self.pid, 100, 150, "cm"))
            self.connection.execute(
                "INSERT INTO purchase VALUES (%s, 7, 10, 'test', now())", (self.pid,),
            )
            self.assertFalse(await self.store.set_dimensions(7, self.pid, 100, 150, "cm"))
        asyncio.run(exercise())
