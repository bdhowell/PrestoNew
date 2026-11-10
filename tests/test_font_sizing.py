import asyncio
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from lxml import html
from pydantic import ValidationError

import posters
from tests import test_layout, test_poster_rendering

DRAFT = test_layout.DRAFT
ROOT = test_poster_rendering.ROOT


class FontSizingRouteTests(unittest.TestCase):
    setUp = test_layout.LayoutRouteTests.setUp

    def test_saves_each_target_and_restores_before_header_measurement(self) -> None:
        self.store.set_font_sizing.return_value = True
        for target, value in (("sections", 0.5), ("title", 1.5), ("authors", 1.25)):
            response = self.client.post("/layout/font-sizing", json={
                "pid": 11, "target": target, "value": value,
            })
            self.assertEqual(response.status_code, 200)
            self.store.set_font_sizing.assert_awaited_with(7, 11, target, value)
        self.store.get.return_value = replace(DRAFT, font_sizing={
            "sections": 0.5, "title": 1.5, "authors": 1.25,
        })
        document = html.fromstring(self.client.get("/layout/edit").text)
        controls = document.get_element_by_id("font-size-controls")
        self.assertEqual(controls.get("data-pid"), "11")
        self.assertEqual(json.loads(controls.get("data-sizing")), self.store.get.return_value.font_sizing)
        measured = html.fromstring(self.balancer.await_args.args[0])
        style = measured.get_element_by_id("poster-canvas").get("style")
        self.assertIn("--sections-font-scale: 0.5", style)
        self.assertIn("--title-font-scale: 1.5", style)
        self.assertIn("--authors-font-scale: 1.25", style)
        self.assertTrue(document.xpath('//script[@src="/static/javascript/layout-font-sizing.js"]'))

    def test_rejects_bad_values_and_enforces_identity_ownership_and_origin(self) -> None:
        payload = {"pid": 11, "target": "sections", "value": 1}
        for changes in (
            {"pid": True}, {"pid": 0}, {"target": "missing"}, {"target": []},
            {"value": 0.49}, {"value": 1.51}, {"value": True}, {"value": "1"}, {"value": None},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(
                    "/layout/font-sizing", json=payload | changes,
                ).status_code, 422)
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValidationError):
                posters.FontSizing(sections=value)
        self.assertEqual(self.client.post(
            "/layout/font-sizing", json=payload, headers={"Origin": "https://elsewhere.test"},
        ).status_code, 403)
        self.uid = None
        self.assertEqual(self.client.post("/layout/font-sizing", json=payload).status_code, 401)
        self.uid = 7
        self.store.get.return_value = None
        self.assertEqual(self.client.post("/layout/font-sizing", json=payload).status_code, 404)
        self.store.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.post("/layout/font-sizing", json=payload).status_code, 409)
        self.store.set_font_sizing.assert_not_awaited()

    def test_reports_storage_failures(self) -> None:
        payload = {"pid": 11, "target": "title", "value": 1.2}
        self.store.set_font_sizing.return_value = False
        self.assertEqual(self.client.post("/layout/font-sizing", json=payload).status_code, 409)
        self.store.set_font_sizing.side_effect = posters.psycopg.OperationalError("offline")
        with self.assertLogs("layout", "ERROR"):
            self.assertEqual(self.client.post("/layout/font-sizing", json=payload).status_code, 503)


class FontSizingStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_updates_only_one_target_and_invalidates_owner_cache(self) -> None:
        store = posters.PosterStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchone.return_value = (11,)
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), patch.object(
            store, "invalidate", wraps=store.invalidate,
        ) as invalidate:
            self.assertTrue(await store.set_font_sizing(7, 11, "authors", 0.75))
            statement, params = connection.execute.await_args_list[0].args
            self.assertIn("p.uid = %s AND p.pid = %s", statement)
            self.assertIn("NOT EXISTS", statement)
            self.assertIn("jsonb_set", statement)
            self.assertEqual(params[0].obj, {"font-sizing": {"authors": 0.75}})
            self.assertEqual(params[1:], (7, 11))
            self.assertEqual(invalidate.call_count, 2)
            connection.execute.return_value.fetchone.return_value = None
            self.assertFalse(await store.set_font_sizing(8, 11, "authors", 1))


@unittest.skipUnless(
    os.environ.get("LAYOUT_TEST_DATABASE_URL"),
    "Set LAYOUT_TEST_DATABASE_URL for PostgreSQL tests",
)
class FontSizingDatabaseTests(unittest.TestCase):
    setUp = test_layout.LayoutDatabaseTests.setUp

    def test_persistence_preserves_other_targets_and_settings(self) -> None:
        self.connection.execute(
            "INSERT INTO userposter (uid, pid, editor, settings) VALUES (7, %s, 1, %s)",
            (self.pid, posters.Jsonb({"other": True, "layout-resizing": {"arena": {"left": 0.1}}})),
        )

        async def exercise() -> None:
            self.assertTrue(await self.store.set_font_sizing(7, self.pid, "title", 1.5))
            self.assertTrue(await self.store.set_font_sizing(7, self.pid, "sections", 0.75))
            poster = await self.store.get(7, self.pid)
            self.assertEqual(poster.font_sizes, posters.FontSizing(title=1.5, sections=0.75))
            self.assertEqual(poster.layout_settings, {"arena": {"left": 0.1}})
            self.assertFalse(await self.store.set_font_sizing(8, self.pid, "authors", 1.1))
            self.connection.execute(
                "INSERT INTO purchase VALUES (%s, 7, 10, 'test', now())", (self.pid,),
            )
            self.assertFalse(await self.store.set_font_sizing(7, self.pid, "authors", 1.1))

        asyncio.run(exercise())
        settings = self.connection.execute(
            "SELECT settings FROM userposter WHERE uid = 7 AND pid = %s", (self.pid,),
        ).fetchone()[0]
        self.assertTrue(settings["other"])


class FontSizingBrowserTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_poster_rendering.TextBalancingTests.asyncSetUp
    editor = test_poster_rendering.TextBalancingTests.editor
    set_zoom = test_poster_rendering.TextBalancingTests.set_zoom
    assert_content_bounds = test_poster_rendering.TextBalancingTests.assert_content_bounds

    async def load_controls(self, **sizing: float) -> None:
        await self.page.route(
            "https://presto.test/**", lambda route: route.fulfill(body="<!doctype html>"),
        )
        await self.page.goto("https://presto.test/")
        await self.editor(poster=replace(DRAFT, font_sizing=sizing))
        await self.page.evaluate("""() => {
            window.saves = [];
            window.fetch = async (url, options) => {
                saves.push({url, data: JSON.parse(options.body)});
                return {ok: true, json: async () => ({ok: true})};
            };
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-font-sizing.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-editor.js"))
        self.assertTrue(await self.page.locator("#font-size-controls").is_hidden())
        await self.page.locator("#extended").click()

    async def change_size(self, target: str, percentage: int) -> None:
        await self.page.locator("#font-sizing").select_option(target)
        await self.page.locator("#font-size").evaluate("""(slider, value) => {
            slider.value = value;
            slider.dispatchEvent(new Event('input', {bubbles: true}));
            slider.dispatchEvent(new Event('change', {bubbles: true}));
        }""", percentage)

    async def test_independent_sizes_line_spacing_and_restored_slider_values(self) -> None:
        await self.load_controls(title=1.2)
        self.assertEqual(await self.page.locator("#font-size").input_value(), "100")
        await self.page.locator("#font-sizing").select_option("title")
        self.assertEqual(await self.page.locator("#font-size").input_value(), "120")
        baseline = await self.page.locator("#layout").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)",
        )
        for target, percentage in (("sections", 50), ("title", 150), ("authors", 75)):
            await self.change_size(target, percentage)
        await self.page.wait_for_function("saves.length === 3 && !document.getElementById('font-size-retry').offsetWidth")
        for selector, factor in (("#layout", 0.5), ("#title", 3.0 * 1.5), ("#authors", 1.375 * 0.75)):
            size, spacing = await self.page.locator(selector).evaluate("""element => {
                const style = getComputedStyle(element);
                return [parseFloat(style.fontSize), parseFloat(style.lineHeight)];
            }""")
            self.assertAlmostEqual(size, baseline * factor, delta=0.01)
            self.assertAlmostEqual(spacing, size * 1.6, delta=0.01)
        await self.page.locator("#font-sizing").select_option("sections")
        self.assertEqual(await self.page.locator("#font-size").input_value(), "50")
        self.assertEqual(await self.page.locator("#font-size-value").inner_text(), "50%")
        self.assertEqual(await self.page.locator("#font-size").get_attribute("aria-label"), "Sections font size")
        self.assertEqual(await self.page.evaluate("saves.map(save => save.data)"), [
            {"pid": 11, "target": "sections", "value": 0.5},
            {"pid": 11, "target": "title", "value": 1.5},
            {"pid": 11, "target": "authors", "value": 0.75},
        ])

    async def test_saved_font_size_notifies_layout_profiler(self) -> None:
        await self.load_controls()
        await self.page.evaluate("""() => {
            window.profileUpdates = 0;
            document.addEventListener("poster-layout-updated", () => profileUpdates++);
        }""")

        await self.change_size("sections", 80)
        await self.page.wait_for_function(
            "document.getElementById('content-save-status').textContent === 'Font sizing saved.'",
        )

        self.assertEqual(await self.page.evaluate("profileUpdates"), 1)

    async def test_save_failure_is_visible_and_retry_saves_latest_value(self) -> None:
        await self.load_controls()
        await self.page.evaluate("""() => {
            window.fetch = async () => ({
                ok: false, json: async () => ({detail: 'Storage unavailable.'}),
            });
        }""")
        await self.change_size("title", 140)
        await self.page.wait_for_function("!document.getElementById('font-size-retry').hidden")
        self.assertIn("Storage unavailable.", await self.page.locator("#content-save-status").inner_text())
        await self.page.evaluate("""() => {
            window.fetch = async (url, options) => {
                saves.push(JSON.parse(options.body));
                return {ok: true, json: async () => ({ok: true})};
            };
        }""")
        await self.page.locator("#font-size-retry").click()
        await self.page.wait_for_function("document.getElementById('content-save-status').textContent === 'Font sizing saved.'")
        self.assertEqual(await self.page.evaluate("saves"), [
            {"pid": 11, "target": "title", "value": 1.4},
        ])

    async def test_inflight_changes_are_serialized_and_keep_latest_values(self) -> None:
        await self.load_controls()
        await self.page.evaluate("""() => {
            window.fetch = (url, options) => {
                saves.push(JSON.parse(options.body));
                return new Promise(resolve => { window.finishSave = () =>
                    resolve({ok: true, json: async () => ({ok: true})}); });
            };
        }""")
        await self.change_size("sections", 110)
        await self.change_size("sections", 140)
        await self.change_size("authors", 60)
        self.assertEqual(await self.page.evaluate("saves.length"), 1)
        for count in (2, 3):
            await self.page.evaluate("finishSave()")
            await self.page.wait_for_function(f"saves.length === {count}")
        await self.page.evaluate("finishSave()")
        await self.page.wait_for_function("document.getElementById('content-save-status').textContent === 'Font sizing saved.'")
        self.assertEqual(await self.page.evaluate("saves"), [
            {"pid": 11, "target": "sections", "value": 1.1},
            {"pid": 11, "target": "sections", "value": 1.4},
            {"pid": 11, "target": "authors", "value": 0.6},
        ])

    async def test_controls_fit_small_screens_and_font_changes_refit_content(self) -> None:
        await self.load_controls()
        await self.page.set_viewport_size({"width": 360, "height": 700})
        await self.change_size("sections", 150)
        await self.page.wait_for_function("document.getElementById('content-save-status').textContent === 'Font sizing saved.'")
        await self.set_zoom("fit")
        await self.assert_content_bounds()
        bounds = await self.page.locator("#font-size-controls").bounding_box()
        self.assertGreaterEqual(bounds["x"], 0)
        self.assertLessEqual(bounds["x"] + bounds["width"], 360)
        self.assertEqual(await self.page.locator("#font-size").get_attribute("max"), "150")
        self.assertEqual(await self.page.locator("#font-size").get_attribute("min"), "50")

    async def test_font_controls_are_on_right_of_extended_tray_and_toggle_with_it(self) -> None:
        await self.load_controls()
        await self.page.set_viewport_size({"width": 1200, "height": 800})
        self.assertEqual(await self.page.locator("#extended-controls > #font-size-controls").count(), 1)
        formatting = await self.page.locator("#ollist").bounding_box()
        fonts = await self.page.locator("#font-size-controls").bounding_box()
        self.assertGreater(fonts["x"], formatting["x"] + formatting["width"])
        self.assertAlmostEqual(fonts["y"] + fonts["height"] / 2,
                               formatting["y"] + formatting["height"] / 2, delta=1)
        await self.page.locator("#extended").click()
        self.assertTrue(await self.page.locator("#font-size-controls").is_hidden())
        await self.page.locator("#extended").click()
        await self.page.locator("#font-sizing").focus()
        await self.page.keyboard.press("Escape")
        self.assertTrue(await self.page.locator("#font-size-controls").is_hidden())
        self.assertEqual(await self.page.locator(":focus").get_attribute("id"), "extended")

    async def test_keyboard_slider_adjusts_selected_target_and_serializes_initial_state(self) -> None:
        await self.load_controls(sections=0.8, title=1.3, authors=1.1)
        await self.page.locator("#font-sizing").select_option("authors")
        await self.page.locator("#font-size").focus()
        await self.page.keyboard.press("ArrowRight")
        await self.page.wait_for_function("document.getElementById('content-save-status').textContent === 'Font sizing saved.'")
        self.assertEqual(await self.page.locator("#font-size-value").inner_text(), "111%")
        self.assertEqual(await self.page.evaluate("saves[0].data"), {
            "pid": 11, "target": "authors", "value": 1.11,
        })
        await self.page.locator("#font-sizing").select_option("title")
        self.assertEqual(await self.page.locator("#font-size").input_value(), "130")
        await self.page.locator("#font-sizing").select_option("sections")
        self.assertEqual(await self.page.locator("#font-size").input_value(), "80")

    async def test_saved_multipliers_apply_to_read_only_documents(self) -> None:
        poster = replace(DRAFT, font_sizing={"sections": 0.6, "title": 1.5, "authors": 1.2})
        rendered = test_layout.routes.templates.env.get_template("posters/document.html").render(
            poster=poster, width=3456, height=4608, sections=[("results", "Findings")],
            authors=[("Ada", "Institute")], theme="blues", fonts="technical", layout="arena",
        )
        await self.page.set_content(rendered)
        await self.page.add_style_tag(path=str(ROOT / "static/css/pages/poster-document.css"))
        for selector, baseline, multiplier in (
            (".poster-sections", 32, 0.6), ("h1", 80, 1.5), (".author", 32, 1.2),
        ):
            size, spacing = await self.page.locator(selector).evaluate("""element => {
                const style = getComputedStyle(element);
                return [parseFloat(style.fontSize), parseFloat(style.lineHeight)];
            }""")
            self.assertAlmostEqual(size, baseline * poster.font_scale * multiplier, delta=0.01)
            self.assertAlmostEqual(spacing, size * 1.4, delta=0.01)
