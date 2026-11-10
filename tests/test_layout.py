import asyncio
import json
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
        self.assertIn('href="/static/css/router.css"', response.text)
        self.assertIn('src="/static/javascript/wikkit.js"', response.text)
        self.assertIn("window.wikkit = connect(", response.text)
        self.store.get.assert_awaited_with(7, 11)
        self.store.content.assert_awaited_once_with(7, 11)
        document = html.fromstring(response.text)
        self.assertEqual(document.get_element_by_id("title").text_content(), "Research")
        template_dialog = document.get_element_by_id("template-dialog")
        self.assertEqual(template_dialog.get("data-template"), "arena")
        self.assertEqual(template_dialog.get("data-width"), "36")
        self.assertEqual(template_dialog.get("data-height"), "48")
        self.assertEqual(
            document.xpath('//*[@id="template-dialog"]//input[@name="category"]/@value'),
            ["square", "landscape", "portrait", "custom"],
        )
        self.assertEqual(
            document.xpath('//*[@id="template-dialog"]//select[@name="units"]/option/@value'),
            ["in", "cm", "mm"],
        )
        self.assertEqual(
            document.get_element_by_id("template-units").getnext().get("id"), "template-categories",
        )
        self.assertEqual(document.get_element_by_id("authors").text_content(), "<Ada>\u00a0(Institute)")
        self.assertIn("Experiment", document.get_element_by_id("methods").text_content())
        self.assertEqual(len(document.xpath('//*[@id="methods"]//h2')), 0)
        self.assertIn("Findings", document.get_element_by_id("conclusions").text_content())
        self.assertEqual(
            document.xpath('//*[@id="methods"]//div[@class="content"]/@contenteditable'),
            ["true"],
        )
        self.assertEqual(
            document.xpath('//*[@id="methods"]//div[@class="content"]/@data-section'),
            ["materials and methods"],
        )

    def test_profile_validation_interval_is_exposed_to_the_editor(self) -> None:
        with patch.dict(os.environ, {"PROFILE_VALIDATION_INTERVAL": "425"}):
            document = html.fromstring(self.client.get("/layout/layout").text)
        self.assertEqual(
            document.get_element_by_id("layout").get("data-profile-validation-interval"),
            "425",
        )

    def test_profile_validation_interval_rejects_invalid_values(self) -> None:
        for value in ("invalid", "-1", "2147483648"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"PROFILE_VALIDATION_INTERVAL": value},
            ), self.assertRaises(ValueError):
                routes.profile_validation_interval()

    def test_section_headings_use_full_names(self) -> None:
        document = html.fromstring(self.client.get("/layout/layout").text)
        self.assertEqual(
            document.xpath('//*[@id="methods"]//div[@class="header"]/text()'),
            ["Materials and Methods"],
        )
        self.assertEqual(
            document.xpath('//*[@id="citations"]//div[@class="header"]/text()'),
            ["Literature Cited"],
        )
        self.assertEqual(
            document.xpath('//*[@id="information"]//div[@class="header"]/text()'),
            ["Additional Information"],
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

    def test_section_save_with_explicit_poster_is_owner_scoped(self) -> None:
        self.store.save_section.return_value = True
        response = self.client.post(
            "/layout/content", json={"pid": 11, "section": "results", "content": "<p>Updated</p>"},
        )
        self.assertEqual(response.status_code, 200)
        self.store.get.assert_awaited_with(7, 11)
        self.store.save_section.assert_awaited_once_with(7, 11, "results", "<p>Updated</p>")
        for pid in (0, -1, True, "11"):
            self.assertEqual(self.client.post(
                "/layout/content", json={"pid": pid, "section": "results", "content": "Text"},
            ).status_code, 422)
        self.store.get.return_value = None
        self.assertEqual(self.client.post(
            "/layout/content", json={"pid": 12, "section": "results", "content": "Text"},
        ).status_code, 404)

    def test_revision_save_is_conditional_on_original_content(self) -> None:
        self.store.save_section_if_unchanged.return_value = True
        data = {"pid": 11, "section": "results", "content": "<p>Revised</p>",
                "expected": "<p>Original</p>"}
        self.assertEqual(self.client.post("/layout/content", json=data).status_code, 200)
        self.store.save_section_if_unchanged.assert_awaited_once_with(
            7, 11, "results", "<p>Revised</p>", "<p>Original</p>",
        )
        self.store.save_section_if_unchanged.return_value = False
        response = self.client.post("/layout/content", json=data)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Copy your edits", response.json()["detail"])
        data["expected"] = 123
        self.assertEqual(self.client.post("/layout/content", json=data).status_code, 422)

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
            "<ul><li>Bullet</li></ul><ol><li>Number</li></ol>"
        )
        self.store.save_section.return_value = True
        response = self.client.post(
            "/layout/content", json={"section": "results", "content": markup},
        )
        self.assertEqual(response.status_code, 200)
        self.store.save_section.assert_awaited_once_with(7, 11, "results", markup)
        self.store.content.return_value = ([("results", markup)], [])
        document = html.fromstring(self.client.get("/layout/edit").text)
        for tag in ("b", "i", "code", "u", "strike", "sup", "sub", "ul", "ol"):
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
                    f"width: {DRAFT.inches[0] * 96:.6f}px; height: {DRAFT.inches[1] * 96:.6f}px; "
                    f"--poster-font-scale: {DRAFT.font_scale:.9f}; "
                    "--sections-font-scale: 1; --title-font-scale: 1; --authors-font-scale: 1",
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

    def test_drafts_missing_dimensions_redirect_before_rendering(self) -> None:
        for path in ("/layout/edit", "/layout/layout", "/layout/edit?pid=52"):
            for width, height in ((None, None), (None, 48), (36, None)):
                with self.subTest(path=path, width=width, height=height):
                    self.session["poster_id"] = 11
                    self.store.get.return_value = replace(
                        DRAFT, pid=52 if "pid=" in path else 11, width=width, height=height,
                    )
                    response = self.client.get(path, follow_redirects=False)
                    self.assertEqual(response.status_code, 303)
                    self.assertEqual(response.headers["location"], "/layout/dimensions")
                    self.assertEqual(self.session["poster_id"], self.store.get.return_value.pid)
        self.store.content.assert_not_awaited()
        self.balancer.assert_not_awaited()
        self.store.set_resumption.assert_not_awaited()

    def test_missing_dimensions_can_be_set_before_upload(self) -> None:
        self.store.get.return_value = replace(DRAFT, width=None, height=None)
        response = self.client.get("/layout/edit?pid=11")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.url.path, "/layout/dimensions")
        self.assertIn('data-page="layout-dimensions"', response.text)
        response = self.client.post("/layout/dimensions", json={"dimensions": "48 in x 36 in"})
        self.assertEqual(response.status_code, 200)
        self.store.set_dimensions.assert_awaited_once_with(7, 11, 36 * 2.54, 48 * 2.54, "in")
        self.assertEqual(response.json()["redirect"], "/content/upload")

    def test_saved_theme_and_fonts_are_applied_to_layout(self) -> None:
        self.store.get.return_value = replace(DRAFT, theme="blues", fonts="technical")
        document = html.fromstring(self.client.get("/layout/edit").text)
        self.assertEqual(document.find("body").get("class").split(), [
            "blue", "corners-very-rounded", "colored-poster-header",
            "colored-section-headers", "technical",
        ])
        self.assertTrue(document.xpath('//link[@href="/static/css/themes.css"]'))
        self.assertTrue(document.xpath('//link[@href="/static/css/fonts.css"]'))

    def test_theme_dialog_exposes_settings_and_saves_current_poster(self) -> None:
        document = html.fromstring(self.client.get("/layout/edit").text)
        self.assertEqual(
            document.xpath('//*[@id="theme-options"]//input[@name="color"]/@value'),
            list(posters.THEME_PICKER_COLORS),
        )
        self.assertEqual(
            document.xpath('//*[@id="theme-options"]//input[@name="corners"]/@value'),
            list(posters.THEME_CORNERS),
        )
        self.assertEqual(
            document.xpath('//*[@id="theme-options"]//input[@type="checkbox"]/@name'),
            ["colored_poster_header", "colored_section_headers"],
        )
        self.assertEqual(document.get_element_by_id("theme-dialog").get("data-pid"), "11")
        self.store.set_theme.return_value = True
        self.session["poster_id"] = 99
        response = self.client.post("/layout/theme", json={
            "pid": 11, "color": "green", "corners": "rounded",
            "colored_poster_header": True, "colored_section_headers": False,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.store.set_theme.assert_awaited_once_with(
            7, 11, "green",
            posters.ThemeSettings(
                corners="rounded", colored_poster_header=True, colored_section_headers=False,
            ),
        )
        self.store.get.return_value = replace(
            DRAFT, theme="green",
            theme_settings={"corners": "rounded", "colored_poster_header": True},
        )
        document = html.fromstring(self.client.get("/layout/edit?pid=11").text)
        self.assertEqual(document.find("body").get("class").split(), [
            "green", "corners-rounded", "colored-poster-header",
        ])

    def test_theme_save_accepts_a_legacy_request_from_cached_editor_assets(self) -> None:
        self.store.set_theme.return_value = True
        response = self.client.post("/layout/theme", json={"pid": 11, "theme": "greens"})
        self.assertEqual(response.status_code, 200)
        self.store.set_theme.assert_awaited_once_with(
            7, 11, "green",
            posters.ThemeSettings(
                corners="very-rounded",
                colored_poster_header=True,
                colored_section_headers=True,
            ),
        )

    def test_font_dialog_lists_installed_sets_and_saves_current_poster(self) -> None:
        document = html.fromstring(self.client.get("/layout/edit").text)
        self.assertEqual(
            document.xpath('//*[@id="font-options"]//input/@value'),
            posters.installed_fonts(),
        )
        self.assertEqual(document.get_element_by_id("font-dialog").get("data-pid"), "11")
        self.assertEqual(document.get_element_by_id("font").get("aria-controls"), "font-dialog")
        for option in posters.installed_fonts():
            preview = document.xpath(f'//*[@id="font-options"]//span[@class="font-preview {option}"]')
            self.assertEqual(len(preview), 1)
            self.assertTrue(preview[0].xpath('.//span[@class="header"]'))
            self.assertTrue(preview[0].xpath('.//span[@class="content"]'))
            self.assertTrue(preview[0].xpath(".//code"))
        self.store.set_fonts.return_value = True
        self.session["poster_id"] = 99
        response = self.client.post("/layout/fonts", json={"pid": 11, "fonts": "technical"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.store.set_fonts.assert_awaited_once_with(7, 11, "technical")
        self.store.get.return_value = replace(DRAFT, theme="blues", fonts="technical")
        document = html.fromstring(self.client.get("/layout/edit?pid=11").text)
        self.assertEqual(document.find("body").get("class").split(), [
            "blue", "corners-very-rounded", "colored-poster-header",
            "colored-section-headers", "technical",
        ])

    def test_font_save_rejects_invalid_unowned_and_purchased_posters(self) -> None:
        for data in (
            {"pid": 11, "fonts": "missing"}, {"pid": 11, "fonts": ["modern"]},
            {"pid": True, "fonts": "modern"}, {"fonts": "modern"},
            {"pid": 0, "fonts": "modern"}, {"pid": 11},
        ):
            with self.subTest(data=data):
                self.assertEqual(self.client.post("/layout/fonts", json=data).status_code, 422)
        data = {"pid": 11, "fonts": "modern"}
        self.assertEqual(self.client.post(
            "/layout/fonts", json=data, headers={"Origin": "https://elsewhere.test"},
        ).status_code, 403)
        self.uid = None
        self.assertEqual(self.client.post("/layout/fonts", json=data).status_code, 401)
        self.uid = 7
        self.store.get.return_value = None
        self.assertEqual(self.client.post("/layout/fonts", json=data).status_code, 404)
        self.store.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.post("/layout/fonts", json=data).status_code, 409)
        self.store.set_fonts.assert_not_awaited()

    def test_font_save_reports_storage_errors_and_conflicts(self) -> None:
        data = {"pid": 11, "fonts": "modern"}
        self.store.set_fonts.return_value = False
        self.assertEqual(self.client.post("/layout/fonts", json=data).status_code, 409)
        self.store.set_fonts.side_effect = psycopg.OperationalError("offline")
        with self.assertLogs("layout", "ERROR"):
            self.assertEqual(self.client.post("/layout/fonts", json=data).status_code, 503)

    def test_theme_save_rejects_invalid_unowned_and_purchased_posters(self) -> None:
        for data in (
            {"pid": 11, "color": "missing", "corners": "square"},
            {"pid": 11, "color": ["blue"], "corners": "square"},
            {"pid": 11, "color": "blue", "corners": "invalid"},
            {"pid": 11, "color": "blue", "corners": "square",
             "colored_poster_header": "yes"},
            {"pid": True, "color": "blue", "corners": "square"},
            {"color": "blue", "corners": "square"},
        ):
            self.assertEqual(self.client.post("/layout/theme", json=data).status_code, 422)
        data = {"pid": 11, "color": "blue", "corners": "square"}
        self.assertEqual(self.client.post(
            "/layout/theme", json=data, headers={"Origin": "https://elsewhere.test"},
        ).status_code, 403)
        self.store.get.return_value = None
        self.assertEqual(self.client.post("/layout/theme", json=data).status_code, 404)
        self.store.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.post("/layout/theme", json=data).status_code, 409)
        self.store.set_theme.assert_not_awaited()

    def test_theme_save_reports_storage_errors_and_conflicts(self) -> None:
        data = {"pid": 11, "color": "blue", "corners": "square"}
        self.store.set_theme.return_value = False
        self.assertEqual(self.client.post("/layout/theme", json=data).status_code, 409)
        self.store.set_theme.side_effect = psycopg.OperationalError("offline")
        with self.assertLogs("layout", "ERROR"):
            self.assertEqual(self.client.post("/layout/theme", json=data).status_code, 503)

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
                self.assertEqual(authors.text_content(), "<Ada>\u00a0(Institute), Grace, Lin\u00a0(University)")
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

    def test_explicit_poster_is_selected_before_saving_resumption(self) -> None:
        self.session.clear()
        self.store.get.return_value = replace(DRAFT, pid=12)
        response = self.client.get("/layout/edit?pid=12", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session["poster_id"], 12)
        self.store.set_resumption.assert_awaited_once_with(
            7, 12, "http://testserver/layout/edit?pid=12&xyzzy=arpa",
        )

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
        self.store.set_resumption.assert_awaited_once_with(
            7, 11, "http://testserver/layout/dimensions?xyzzy=arpa",
        )

    def test_resumed_dimensions_page_restores_its_own_resumption_marker(self) -> None:
        response = self.client.get("/layout/dimensions?tag=a%2Fb&tag=two&xyzzy=arpa")
        self.assertEqual(response.status_code, 200)
        self.store.set_resumption.assert_awaited_once_with(
            7, 11, "http://testserver/layout/dimensions?tag=a%2Fb&tag=two&xyzzy=arpa",
        )

    def test_dimension_updates_store_centimeters_and_report_the_display_units(self) -> None:
        response = self.client.post("/layout/dimensions", json={"dimensions": "36 in x 48 in"})
        self.assertEqual(response.status_code, 200)
        self.store.set_dimensions.assert_awaited_once_with(7, 11, 121.92, 91.44, "in")
        self.assertEqual(response.json()["preview"], {"height": 36, "width": 48, "units": "in"})
        self.assertEqual(response.json()["redirect"], "/content/upload")

    def test_dimension_updates_can_preserve_a_selected_template(self) -> None:
        response = self.client.post(
            "/layout/dimensions",
            json={"dimensions": "48 in x 36 in", "template": "central"},
        )
        self.assertEqual(response.status_code, 200)
        self.store.set_dimensions.assert_awaited_once_with(7, 11, 91.44, 121.92, "in", "central")
        self.assertEqual(response.json()["redirect"], "/layout/edit")
        response = self.client.post(
            "/layout/dimensions",
            json={"dimensions": "48 in x 36 in", "template": "unknown"},
        )
        self.assertEqual(response.status_code, 422)

    def test_standard_30_by_40_sizes_are_available(self) -> None:
        for dimensions in ("30 in x 40 in", "40 in x 30 in"):
            with self.subTest(dimensions=dimensions):
                response = self.client.post("/layout/dimensions", json={"dimensions": dimensions})
                self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.set_dimensions.await_count, 2)

    def test_dimension_updates_preserve_fractional_dimensions(self) -> None:
        response = self.client.post("/layout/dimensions", json={"dimensions": "36.5 in x 48 in"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["preview"], {"height": 36.5, "width": 48, "units": "in"})
        args = self.store.set_dimensions.await_args.args
        self.assertEqual(args[:2], (7, 11))
        self.assertAlmostEqual(args[2], 121.92)
        self.assertAlmostEqual(args[3], 92.71)
        self.assertEqual(args[4], "in")

    def test_all_presets_are_accepted_in_both_orientations(self) -> None:
        for units, presets in posters.POSTER_PRESETS.items():
            for name, width, height in presets:
                for w, h in ((width, height), (height, width)):
                    with self.subTest(units=units, name=name, width=w, height=h):
                        response = self.client.post(
                            "/layout/dimensions",
                            json={"dimensions": f"{h} {units} x {w} {units}", "template": "central"},
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        args = self.store.set_dimensions.await_args.args
                        self.assertAlmostEqual(args[2], w * routes.SCALE[units])
                        self.assertAlmostEqual(args[3], h * routes.SCALE[units])
                        self.assertEqual(args[4:], (units, "central"))

    def test_iso_presets_still_enforce_conference_constraints(self) -> None:
        self.store.conference_details.return_value = (
            "Meeting", {"maximum_poster_height": 100, "poster_units": "cm"},
        )
        response = self.client.post("/layout/dimensions", json={"dimensions": "118.9 x 84.1 cm"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("height is too big", " ".join(response.json()["errors"]))
        self.store.set_dimensions.assert_not_awaited()

    def test_landscape_preset_limits_are_rendered_and_enforced_consistently(self) -> None:
        self.store.conference_details.return_value = (
            "Meeting", {"maximum_poster_width": 122, "poster_units": "cm"},
        )
        response = self.client.get("/layout/layout")
        self.assertEqual(response.status_code, 200)
        document = html.fromstring(response.text)
        errors = json.loads(document.get_element_by_id("template-dialog").get("data-preset-errors"))
        index = posters.POSTER_PRESETS["in"].index(("", 42, 72))
        expected = ["Poster width is too big. The maximum is 48.0315 in."]
        self.assertEqual(errors["in"]["landscape"][index], expected)
        self.assertEqual(errors["in"]["portrait"][index], [])
        response = self.client.post(
            "/layout/dimensions", json={"dimensions": "42 in x 72 in", "template": "central"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"], expected)
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
                        "Poster height is too big. The maximum is 60 in.",
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
        self.assertEqual(self.bot.send.await_args.args[0]["command"]["payload"], "/content/upload")

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
            self.assertTrue(await store.set_dimensions(7, 11, 150, 100, "cm", "central"))
            self.assertEqual(
                connection.execute.await_args.args[1],
                (150, 100, "cm", "central", 7, 11),
            )
            for units, width, height in (("cm", 84.1, 118.9), ("mm", 841, 1189)):
                scale = posters.POSTER_UNIT_TO_CM[units]
                self.assertTrue(await store.set_dimensions(7, 11, width * scale, height * scale, units))
                params = connection.execute.await_args.args[1]
                self.assertAlmostEqual(params[0], width)
                self.assertAlmostEqual(params[1], height)
                self.assertEqual(params[2], units)

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

    def test_dimension_migration_preserves_existing_values_and_iso_fractions(self) -> None:
        self.connection.execute(
            "ALTER TABLE poster ALTER COLUMN width TYPE INTEGER, ALTER COLUMN height TYPE INTEGER",
        )
        self.connection.execute(
            "UPDATE poster SET width = 36, height = 48, units = 'in' WHERE pid = %s", (self.pid,),
        )
        migration = (
            Path(__file__).resolve().parent.parent / "schema/poster-dimensions.sql"
        ).read_text()
        self.connection.execute(migration)
        self.connection.execute(migration)
        self.assertEqual(self.connection.execute(
            "SELECT width, height, units FROM poster WHERE pid = %s", (self.pid,),
        ).fetchone(), (36, 48, "in"))

        async def exercise() -> None:
            for units, width, height in (("cm", 84.1, 118.9), ("mm", 841, 1189)):
                scale = posters.POSTER_UNIT_TO_CM[units]
                self.assertTrue(await self.store.set_dimensions(
                    7, self.pid, width * scale, height * scale, units,
                ))
                poster = await self.store.get(7, self.pid)
                self.assertAlmostEqual(poster.width, width)
                self.assertAlmostEqual(poster.height, height)
                self.assertEqual(poster.units, units)
        asyncio.run(exercise())

    def test_persisted_dimensions_reach_rendering_and_cannot_modify_purchased_or_foreign_posters(self) -> None:
        async def exercise() -> None:
            self.assertTrue(await self.store.set_dimensions(7, self.pid, 150, 100, "mm"))
            poster = await self.store.get(7, self.pid)
            self.assertEqual(poster.centimeters, (150, 100))
            self.assertEqual((poster.width, poster.height), (1500, 1000))
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
