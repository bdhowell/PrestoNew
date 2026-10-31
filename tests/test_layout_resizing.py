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


class SizingRouteTests(unittest.TestCase):
    setUp = test_layout.LayoutRouteTests.setUp

    def payload(self, **sizing: object) -> dict:
        return {"pid": 11, "template": "arena", "sizing": sizing}

    def test_saves_normalized_positions_for_the_owned_draft(self) -> None:
        self.store.set_layout_sizing.return_value = True
        response = self.client.post("/layout/sizing", json=self.payload(
            left=0.15, right=0.12, bottom=0.05, introduction=0.7,
            sections={"pane3": [0.2, 0.3, 0.5]},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        uid, pid, template, sizing = self.store.set_layout_sizing.await_args.args
        self.assertEqual((uid, pid, template), (7, 11, "arena"))
        self.assertEqual(sizing.left, 0.15)
        self.assertEqual(sizing.sections, {"pane3": [0.2, 0.3, 0.5]})

    def test_rejects_invalid_limits_groups_and_input_types(self) -> None:
        for sizing in (
            {"left": -0.001}, {"right": 0.151}, {"bottom": 0.16},
            {"introduction": 0.249}, {"introduction": 0.751},
            {"left": True}, {"left": "0.1"}, {"extra": 1},
            {"sections": {"pane3": [0.19, 0.4, 0.41]}},
            {"sections": {"pane3": [0.3, 0.3, 0.3]}},
            {"sections": {"pane3": [0.5, 0.5]}},
            {"sections": {"unknown": [0.5, 0.5]}},
        ):
            with self.subTest(sizing=sizing):
                self.assertEqual(
                    self.client.post("/layout/sizing", json=self.payload(**sizing)).status_code, 422,
                )
        self.store.set_layout_sizing.assert_not_awaited()
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValidationError):
                posters.LayoutSizing(left=value)

    def test_checks_identity_ownership_template_purchase_and_origin(self) -> None:
        self.uid = None
        self.assertEqual(self.client.post("/layout/sizing", json=self.payload()).status_code, 401)
        self.uid = 7
        self.store.get.return_value = None
        self.assertEqual(self.client.post("/layout/sizing", json=self.payload()).status_code, 404)
        self.store.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.post("/layout/sizing", json=self.payload()).status_code, 409)
        self.store.get.return_value = replace(DRAFT, template="central")
        self.assertEqual(self.client.post("/layout/sizing", json=self.payload()).status_code, 409)
        self.store.get.return_value = DRAFT
        self.assertEqual(self.client.post(
            "/layout/sizing", json=self.payload(), headers={"Origin": "https://elsewhere.test"},
        ).status_code, 403)
        self.store.set_layout_sizing.assert_not_awaited()

    def test_reports_failed_storage_and_rejects_bottom_for_other_templates(self) -> None:
        self.store.set_layout_sizing.return_value = False
        self.assertEqual(self.client.post("/layout/sizing", json=self.payload()).status_code, 409)
        self.store.set_layout_sizing.side_effect = posters.psycopg.OperationalError("offline")
        with self.assertLogs("layout", "ERROR"):
            self.assertEqual(self.client.post("/layout/sizing", json=self.payload()).status_code, 503)
        self.store.get.return_value = replace(DRAFT, template="central")
        self.assertEqual(self.client.post("/layout/sizing", json={
            "pid": 11, "template": "central", "sizing": {"bottom": 0.1},
        }).status_code, 422)

    def test_render_restores_only_the_selected_templates_positions(self) -> None:
        self.store.get.return_value = replace(DRAFT, layout_settings={
            "arena": {"left": 0.1, "introduction": 0.6},
            "central": {"right": 0.07},
        })
        document = html.fromstring(self.client.get("/layout/edit").text)
        element = document.get_element_by_id("layout")
        self.assertEqual(element.get("data-pid"), "11")
        self.assertEqual(element.get("data-template"), "arena")
        self.assertEqual(json.loads(element.get("data-resize-settings"))["left"], 0.1)
        self.assertTrue(document.xpath('//script[@src="/static/javascript/layout-resize.js"]'))
        self.store.get.return_value = replace(self.store.get.return_value, template="central")
        document = html.fromstring(self.client.get("/layout/edit").text)
        sizing = json.loads(document.get_element_by_id("layout").get("data-resize-settings"))
        self.assertEqual(sizing["left"], 0)
        self.assertEqual(sizing["right"], 0.07)


class SizingStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_draft_template_guards_and_cache_invalidation(self) -> None:
        store = posters.PosterStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchone.return_value = (11,)
        sizing = posters.LayoutSizing(left=0.1)
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), patch.object(
            store, "invalidate", wraps=store.invalidate,
        ) as invalidate:
            self.assertTrue(await store.set_layout_sizing(7, 11, "arena", sizing))
            statement, params = connection.execute.await_args_list[0].args
            self.assertIn("p.uid = %s AND p.pid = %s", statement)
            self.assertIn("coalesce(p.template, 'arena') = %s", statement)
            self.assertIn("NOT EXISTS", statement)
            self.assertIn("jsonb_set", statement)
            self.assertEqual(params[1:], (7, 11, "arena"))
            self.assertEqual(invalidate.call_count, 2)
            connection.execute.return_value.fetchone.return_value = None
            self.assertFalse(await store.set_layout_sizing(8, 11, "arena", sizing))


@unittest.skipUnless(
    os.environ.get("LAYOUT_TEST_DATABASE_URL"), "Set LAYOUT_TEST_DATABASE_URL for PostgreSQL tests",
)
class SizingDatabaseTests(unittest.TestCase):
    setUp = test_layout.LayoutDatabaseTests.setUp

    def test_persists_positions_preserves_other_settings_and_guards_writes(self) -> None:
        self.connection.execute(
            "INSERT INTO userposter (uid, pid, editor, settings) VALUES (7, %s, 1, %s)",
            (self.pid, posters.Jsonb({"other-setting": True, "layout-resizing": {"central": {"left": 0.03}}})),
        )

        async def exercise() -> None:
            sizing = posters.LayoutSizing(left=0.12, introduction=0.6)
            self.assertTrue(await self.store.set_layout_sizing(7, self.pid, "arena", sizing))
            poster = await self.store.get(7, self.pid)
            self.assertEqual(poster.layout_settings["arena"], sizing.model_dump())
            self.assertEqual(poster.layout_settings["central"], {"left": 0.03})
            self.assertFalse(await self.store.set_layout_sizing(8, self.pid, "arena", sizing))
            self.assertFalse(await self.store.set_layout_sizing(7, self.pid, "central", sizing))
            self.connection.execute(
                "INSERT INTO purchase VALUES (%s, 7, 10, 'test', now())", (self.pid,),
            )
            self.assertFalse(await self.store.set_layout_sizing(7, self.pid, "arena", sizing))
        asyncio.run(exercise())
        settings = self.connection.execute(
            "SELECT settings FROM userposter WHERE uid = 7 AND pid = %s", (self.pid,),
        ).fetchone()[0]
        self.assertTrue(settings["other-setting"])


class SizingBrowserTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_poster_rendering.TextBalancingTests.asyncSetUp
    editor = test_poster_rendering.TextBalancingTests.editor

    async def load(self, template: str = "arena", sizing: dict | None = None) -> None:
        await self.page.goto("about:blank")
        await self.editor(template)
        await self.page.evaluate("""sizing => {
            window.sizingRequests = [];
            window.fetch = async (url, options) => {
                window.sizingRequests.push({url, data: JSON.parse(options.body)});
                return {ok: true, json: async () => ({ok: true})};
            };
            if (sizing) document.getElementById("layout").dataset.resizeSettings = JSON.stringify(sizing);
        }""", sizing)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-resize.js"))
        await self.page.wait_for_timeout(100)
        self.assertEqual(self.errors, [])

    async def geometry(self) -> dict:
        return await self.page.evaluate("""() => {
            const rect = id => {
                const element = document.getElementById(id);
                const {x, y, width, height} = element.getBoundingClientRect();
                return {x, y, width, height, unscaledWidth: parseFloat(getComputedStyle(element).width),
                    unscaledHeight: parseFloat(getComputedStyle(element).height)};
            };
            return Object.fromEntries(["layout", "pane0", "pane1", "pane2", "pane3",
                "introduction", "methods", "citations", "acknowledgements", "information"]
                .filter(id => document.getElementById(id)).map(id => [id, rect(id)]));
        }""")

    async def drag(self, name: str, x: float = 0, y: float = 0) -> None:
        handle = self.page.locator(f"[data-resize='{name}'] .resize-handle")
        if not await handle.is_visible():
            await self.page.locator(f"[data-resize='{name}'] .resize-toggle").click()
        await handle.scroll_into_view_if_needed()
        box = await handle.bounding_box()
        start_x, start_y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        await self.page.mouse.move(start_x, start_y)
        await self.page.mouse.down()
        await self.page.mouse.move(start_x + x, start_y + y, steps=5)
        await self.page.mouse.up()
        await self.page.wait_for_timeout(100)
        self.assertEqual(self.errors, [])

    async def test_column_handles_toggle_preserve_total_and_enforce_inward_limits(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                await self.load(template)
                self.assertFalse(await self.page.locator("#resize-left").is_visible())
                before = await self.geometry()
                await self.drag("left", x=before["layout"]["width"] * 0.2)
                after = await self.geometry()
                displacement = after["pane0"]["unscaledWidth"] - before["pane0"]["unscaledWidth"]
                self.assertAlmostEqual(displacement / after["layout"]["unscaledWidth"], 0.15, delta=0.001)
                self.assertAlmostEqual(after["pane2"]["unscaledWidth"], before["pane2"]["unscaledWidth"], delta=0.1)
                self.assertAlmostEqual(after["pane1"]["unscaledWidth"] + displacement,
                                       before["pane1"]["unscaledWidth"], delta=0.1)
                await self.drag("left", x=-after["layout"]["width"] * 0.3)
                restored = await self.geometry()
                self.assertAlmostEqual(restored["pane0"]["unscaledWidth"], before["pane0"]["unscaledWidth"], delta=0.1)
                await self.drag("right", x=-before["layout"]["width"] * 0.2)
                after = await self.geometry()
                self.assertAlmostEqual((after["pane2"]["unscaledWidth"] - before["pane2"]["unscaledWidth"])
                                       / after["layout"]["unscaledWidth"], 0.15, delta=0.001)
                await self.page.locator("#resize-right").focus()
                await self.page.keyboard.press("Home")
                await self.page.wait_for_timeout(50)
                self.assertAlmostEqual((await self.geometry())["pane2"]["unscaledWidth"],
                                       before["pane2"]["unscaledWidth"], delta=0.1)
                await self.page.keyboard.press("Escape")
                self.assertFalse(await self.page.locator("#resize-right").is_visible())
                await self.page.locator("[data-resize='left'] .resize-toggle").click(position={"x": 1, "y": 1})
                self.assertTrue(await self.page.locator("#resize-left").is_visible())
                await self.page.locator("[data-resize='left'] .resize-toggle").click(position={"x": 1, "y": 1})
                self.assertFalse(await self.page.locator("#resize-left").is_visible())

    async def test_only_one_affordance_is_active_at_a_time(self) -> None:
        await self.load()
        before = await self.geometry()
        for name in ("left", "right", "bottom", "pane3-0", "pane3-1", "introduction", "left"):
            with self.subTest(handle=name):
                await self.page.locator(f"[data-resize='{name}'] .resize-toggle").click()
                self.assertEqual(await self.page.locator(".resize-handle:not([hidden])").count(), 1)
                self.assertEqual(await self.page.locator(".resize-toggle[aria-expanded='true']").count(), 1)
                self.assertTrue(await self.page.locator(f"#resize-{name}").is_visible())
                self.assertEqual(await self.geometry(), before)
        await self.page.locator("#resize-left").click()
        self.assertEqual(await self.page.locator(".resize-handle:not([hidden])").count(), 0)
        self.assertEqual(await self.page.locator(".resize-toggle[aria-expanded='true']").count(), 0)
        self.assertEqual(await self.page.evaluate("window.sizingRequests"), [])

    async def test_column_snapping_uses_six_screen_pixels_and_the_moving_handle(self) -> None:
        await self.load()
        before = await self.geometry()
        width = before["layout"]["width"]
        await self.drag("left", x=width * 0.1)
        width = (await self.geometry())["layout"]["width"]
        await self.drag("right", x=-width * 0.1 - 5)
        after = await self.geometry()
        self.assertAlmostEqual(after["pane0"]["width"] - before["pane0"]["width"],
                               after["pane2"]["width"] - before["pane2"]["width"], delta=0.1)
        await self.drag("right", x=-8)
        after = await self.geometry()
        self.assertGreater(after["pane2"]["width"] - after["pane0"]["width"], 6)
        await self.drag("left", x=4)
        after = await self.geometry()
        self.assertAlmostEqual(after["pane0"]["width"], after["pane2"]["width"], delta=0.1)

    async def test_bottom_height_and_introduction_split_limits_preserve_gutters(self) -> None:
        await self.load()
        before = await self.geometry()
        await self.drag("bottom", y=-before["layout"]["height"] * 0.3)
        after = await self.geometry()
        change = after["pane3"]["unscaledHeight"] - before["pane3"]["unscaledHeight"]
        self.assertAlmostEqual(change / after["layout"]["unscaledHeight"], 0.15, delta=0.001)
        for pane in ("pane0", "pane1", "pane2"):
            self.assertAlmostEqual(after[pane]["unscaledHeight"] + change, before[pane]["unscaledHeight"], delta=0.1)
        for ratio, direction in ((0.75, 10000), (0.25, -10000)):
            before = await self.geometry()
            await self.drag("introduction", y=direction)
            after = await self.geometry()
            pair = after["introduction"]["height"] + after["methods"]["height"]
            self.assertAlmostEqual(after["introduction"]["height"] / pair, ratio, delta=0.001)
            before_gap = before["methods"]["y"] - before["introduction"]["y"] - before["introduction"]["height"]
            after_gap = after["methods"]["y"] - after["introduction"]["y"] - after["introduction"]["height"]
            self.assertAlmostEqual(after_gap, before_gap, delta=0.1)

    async def test_section_boundaries_do_not_drag_hidden_and_enforce_twenty_percent(self) -> None:
        await self.load()
        before = await self.geometry()
        toggle = self.page.locator("[data-resize='pane3-0'] .resize-toggle")
        box = await toggle.bounding_box()
        await self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await self.page.mouse.down()
        await self.page.mouse.move(box["x"] + 50, box["y"])
        await self.page.mouse.up()
        self.assertEqual((await self.geometry())["citations"]["width"], before["citations"]["width"])
        await self.drag("pane3-0", x=-10000)
        after = await self.geometry()
        self.assertAlmostEqual(after["citations"]["width"] / after["layout"]["width"], 0.2, delta=0.001)
        self.assertAlmostEqual(after["citations"]["unscaledWidth"] + after["acknowledgements"]["unscaledWidth"],
                               before["citations"]["unscaledWidth"] + before["acknowledgements"]["unscaledWidth"], delta=0.1)
        self.assertAlmostEqual(after["information"]["unscaledWidth"], before["information"]["unscaledWidth"], delta=0.1)
        await self.drag("pane3-1", x=10000)
        after = await self.geometry()
        self.assertGreaterEqual(after["information"]["width"] / after["layout"]["width"], 0.1999)

    async def test_saved_geometry_restores_and_failures_are_visible(self) -> None:
        await self.load()
        width = (await self.geometry())["layout"]["width"]
        await self.drag("left", x=width * 0.08)
        await self.page.wait_for_function("() => window.sizingRequests.length > 0")
        request = await self.page.evaluate("window.sizingRequests.at(-1)")
        self.assertEqual(request["url"], "/layout/sizing")
        self.assertEqual(request["data"]["pid"], 11)
        before = await self.geometry()
        await self.load(sizing=request["data"]["sizing"])
        after = await self.geometry()
        self.assertAlmostEqual(after["pane0"]["unscaledWidth"], before["pane0"]["unscaledWidth"], delta=0.1)
        self.assertFalse(await self.page.locator("#resize-left").is_visible())
        await self.page.evaluate("""() => {
            window.fetch = async () => ({ok: false, json: async () => ({detail: "Storage unavailable"})});
        }""")
        await self.drag("left", x=10)
        await self.page.wait_for_function(
            "() => document.getElementById('content-save-status').textContent.includes('Storage unavailable')",
        )

    async def test_zoomed_dragging_uses_screen_displacement(self) -> None:
        await self.load()
        await self.page.locator("#poster-zoom").evaluate("""slider => {
            slider.value = "70";
            slider.dispatchEvent(new Event("input", {bubbles: true}));
        }""")
        before = await self.geometry()
        await self.drag("left", x=before["layout"]["width"] * 0.1)
        after = await self.geometry()
        self.assertAlmostEqual((after["pane0"]["width"] - before["pane0"]["width"])
                               / after["layout"]["width"], 0.1, delta=0.001)

    async def test_restores_all_positions_and_retains_limits_after_font_and_viewport_changes(self) -> None:
        sizing = {
            "left": 0.1, "right": 0.08, "bottom": 0.12,
            "introduction": 0.65, "sections": {"pane3": [0.22, 0.33, 0.45]},
        }
        await self.load(sizing=sizing)
        for font_size, viewport_width in ((16, 640), (18, 1000), (14, 450)):
            with self.subTest(font_size=font_size, viewport_width=viewport_width):
                await self.page.set_viewport_size({"width": viewport_width, "height": 900})
                await self.page.evaluate(
                    "size => document.documentElement.style.fontSize = `${size}px`", font_size,
                )
                await self.page.wait_for_timeout(150)
                geometry = await self.geometry()
                width = geometry["layout"]["unscaledWidth"]
                gap = await self.page.locator("#layout").evaluate(
                    "element => parseFloat(getComputedStyle(element).columnGap)",
                )
                self.assertAlmostEqual(
                    (geometry["pane0"]["unscaledWidth"] - (width - 2 * gap) / 4) / width,
                    0.1, delta=0.001,
                )
                self.assertAlmostEqual(
                    (geometry["pane2"]["unscaledWidth"] - (width - 2 * gap) / 4) / width,
                    0.08, delta=0.001,
                )
                for section in ("citations", "acknowledgements", "information"):
                    self.assertGreaterEqual(geometry[section]["unscaledWidth"] / width, 0.1999)
                pair = geometry["introduction"]["height"] + geometry["methods"]["height"]
                self.assertAlmostEqual(geometry["introduction"]["height"] / pair, 0.65, delta=0.001)
                height = geometry["layout"]["unscaledHeight"]
                self.assertAlmostEqual(
                    (geometry["pane3"]["unscaledHeight"] - (height - gap) / 5) / height,
                    0.12, delta=0.001,
                )
                self.assertEqual(self.errors, [])
        self.assertEqual(await self.page.evaluate("window.sizingRequests"), [])

    async def test_introduction_split_is_available_for_all_templates(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                await self.load(template)
                self.assertFalse(await self.page.locator("#resize-introduction").is_visible())
                await self.drag("introduction", y=10000)
                geometry = await self.geometry()
                self.assertAlmostEqual(
                    geometry["introduction"]["height"]
                    / (geometry["introduction"]["height"] + geometry["methods"]["height"]),
                    0.75, delta=0.001,
                )
                self.assertEqual(
                    await self.page.locator("#resize-bottom").count(), 1 if template == "arena" else 0,
                )

    async def test_changes_during_a_save_are_queued_without_losing_the_latest_positions(self) -> None:
        await self.load()
        await self.page.evaluate("""() => {
            window.fetch = (url, options) => new Promise(resolve => {
                window.sizingRequests.push({url, data: JSON.parse(options.body)});
                window.finishSizingSave = () => resolve({ok: true, json: async () => ({ok: true})});
            });
        }""")
        width = (await self.geometry())["layout"]["width"]
        await self.drag("left", x=width * 0.08)
        await self.drag("right", x=-(await self.geometry())["layout"]["width"] * 0.12)
        self.assertEqual(await self.page.evaluate("window.sizingRequests.length"), 1)
        await self.page.evaluate("window.finishSizingSave()")
        await self.page.wait_for_function("() => window.sizingRequests.length === 2")
        sizing = await self.page.evaluate("window.sizingRequests[1].data.sizing")
        self.assertAlmostEqual(sizing["left"], 0.08, delta=0.001)
        self.assertAlmostEqual(sizing["right"], 0.12, delta=0.001)
        await self.page.evaluate("window.finishSizingSave()")
        await self.page.wait_for_function(
            "() => document.getElementById('content-save-status').textContent === 'Layout sizing saved.'",
        )
