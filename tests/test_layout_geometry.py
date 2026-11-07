import asyncio
import os
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from lxml import html
from pydantic import ValidationError

import posters
from tests import test_layout, test_layout_resizing

ROOT = test_layout_resizing.ROOT
DRAFT = test_layout.DRAFT
GEOMETRY = {
    "scale": 1.0, "header": 0.1, "layout": 0.8,
    "columns": [0.25, 0.5, 0.25], "rows": [0.8, 0.2],
    "vertical": {"pane0": [0.5, 0.5], "pane1": [1.0], "pane2": [1.0]},
    "horizontal": {"pane3": [0.3, 0.3, 0.4]},
}


class GeometryRouteTests(unittest.TestCase):
    setUp = test_layout.LayoutRouteTests.setUp

    def payload(self, geometry: dict | None = None) -> dict:
        return {"pid": 11, "template": "arena", "geometry": geometry or GEOMETRY}

    def test_initializes_and_renders_the_stored_template_geometry(self) -> None:
        self.store.set_layout_geometry.return_value = posters.LayoutGeometry.model_validate(GEOMETRY)
        response = self.client.post("/layout/geometry", json=self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "geometry": GEOMETRY})
        uid, pid, template, geometry = self.store.set_layout_geometry.await_args.args
        self.assertEqual((uid, pid, template), (7, 11, "arena"))
        self.assertEqual(geometry.model_dump(), GEOMETRY)
        self.store.get.return_value = replace(DRAFT, layout_geometry={"arena": GEOMETRY})
        document = html.fromstring(self.client.get("/layout/edit").text)
        self.assertEqual(
            document.get_element_by_id("layout").get("data-initial-geometry"),
            geometry.model_dump_json(),
        )
        self.assertTrue(document.xpath('//script[@src="/static/javascript/layout-geometry.js"]'))

    def test_rejects_bad_geometry_identity_ownership_template_and_origin(self) -> None:
        for change in (
            {"scale": 0}, {"scale": 1.1}, {"header": -1}, {"layout": 2},
            {"columns": [0.5, 0.5]}, {"rows": [1.0]}, {"vertical": {}},
            {"horizontal": {"missing": [1.0]}}, {"rows": [0.8, 0.3]},
            {"columns": [True, 0.2, 0.3]},
        ):
            with self.subTest(change=change):
                self.assertEqual(self.client.post(
                    "/layout/geometry", json=self.payload(GEOMETRY | change),
                ).status_code, 422)
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValidationError):
                posters.LayoutGeometry.model_validate(GEOMETRY | {"columns": [value, 0.3, 0.4]})
        self.assertEqual(self.client.post(
            "/layout/geometry", json=self.payload(), headers={"Origin": "https://elsewhere.test"},
        ).status_code, 403)
        self.uid = None
        self.assertEqual(self.client.post("/layout/geometry", json=self.payload()).status_code, 401)
        self.uid = 7
        self.store.get.return_value = None
        self.assertEqual(self.client.post("/layout/geometry", json=self.payload()).status_code, 404)
        self.store.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.post("/layout/geometry", json=self.payload()).status_code, 409)
        self.store.get.return_value = replace(DRAFT, template="central")
        self.assertEqual(self.client.post("/layout/geometry", json=self.payload()).status_code, 409)
        self.store.set_layout_geometry.assert_not_awaited()

    def test_reports_storage_errors_and_conflicts(self) -> None:
        self.store.set_layout_geometry.return_value = None
        self.assertEqual(self.client.post("/layout/geometry", json=self.payload()).status_code, 409)
        self.store.set_layout_geometry.side_effect = posters.psycopg.OperationalError("offline")
        with self.assertLogs("layout", "ERROR"):
            self.assertEqual(self.client.post("/layout/geometry", json=self.payload()).status_code, 503)


class GeometryStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_first_baseline_and_invalidates_owner_cache(self) -> None:
        store = posters.PosterStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchone.return_value = (GEOMETRY,)
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), patch.object(
            store, "invalidate", wraps=store.invalidate,
        ) as invalidate:
            geometry = posters.LayoutGeometry.model_validate(GEOMETRY)
            self.assertEqual(await store.set_layout_geometry(7, 11, "arena", geometry), geometry)
            statement, params = connection.execute.await_args.args
            self.assertIn("p.uid = %s AND p.pid = %s", statement)
            self.assertIn("coalesce(p.template, 'arena') = %s", statement)
            self.assertIn("NOT EXISTS", statement)
            self.assertIn("(excluded.settings->'layout-geometry') || coalesce", statement)
            self.assertEqual(params[1:], (7, 11, "arena", "arena"))
            self.assertEqual(invalidate.call_count, 2)
            connection.execute.return_value.fetchone.return_value = None
            self.assertIsNone(await store.set_layout_geometry(8, 11, "arena", geometry))


@unittest.skipUnless(os.environ.get("LAYOUT_TEST_DATABASE_URL"), "Set LAYOUT_TEST_DATABASE_URL for PostgreSQL tests")
class GeometryDatabaseTests(unittest.TestCase):
    setUp = test_layout.LayoutDatabaseTests.setUp

    def test_first_geometry_survives_reinitialization_and_preserves_settings(self) -> None:
        self.connection.execute(
            "INSERT INTO userposter (uid, pid, editor, settings) VALUES (7, %s, 1, %s)",
            (self.pid, posters.Jsonb({"font-sizing": {"title": 1.2}, "other": True})),
        )

        async def exercise() -> None:
            geometry = posters.LayoutGeometry.model_validate(GEOMETRY)
            self.assertEqual(await self.store.set_layout_geometry(7, self.pid, "arena", geometry), geometry)
            changed = geometry.model_copy(update={"header": 0.2})
            self.assertEqual(await self.store.set_layout_geometry(7, self.pid, "arena", changed), geometry)
            poster = await self.store.get(7, self.pid)
            self.assertEqual(poster.layout_geometry, {"arena": GEOMETRY})
            self.assertEqual(poster.font_sizing, {"title": 1.2})
            self.assertIsNone(await self.store.set_layout_geometry(8, self.pid, "arena", geometry))
            self.connection.execute(
                "INSERT INTO purchase VALUES (%s, 7, 10, 'test', now())", (self.pid,),
            )
            self.assertIsNone(await self.store.set_layout_geometry(7, self.pid, "arena", geometry))
        asyncio.run(exercise())


class GeometryBrowserTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_layout.LayoutRouteTests.setUp
    asyncSetUp = test_layout_resizing.SizingBrowserTests.asyncSetUp
    editor = test_layout_resizing.SizingBrowserTests.editor
    geometry = test_layout_resizing.SizingBrowserTests.geometry
    drag = test_layout_resizing.SizingBrowserTests.drag

    async def load(
        self, template: str = "arena", initial: dict | None = None,
        sizing: dict | None = None, edited: bool = False,
    ) -> None:
        await self.page.goto("about:blank")
        await self.editor(template)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        await self.page.evaluate("""({initial, sizing, edited}) => {
            const layout = document.getElementById('layout');
            layout.dataset.initialGeometry = JSON.stringify(initial);
            if (sizing) layout.dataset.resizeSettings = JSON.stringify(sizing);
            if (edited) {
                document.getElementById('poster-canvas').style.setProperty('--sections-font-scale', 1.5);
                document.getElementById('poster-canvas').style.setProperty('--title-font-scale', 1.5);
                document.getElementById('title').textContent = 'A longer title '.repeat(30);
                document.querySelector('#results .content').textContent = 'More findings '.repeat(1000);
            }
            window.geometryRequests = [];
            window.fetch = async (url, options) => {
                const data = JSON.parse(options.body);
                geometryRequests.push({url, data});
                return {ok: true, json: async () => ({ok: true, geometry: data.geometry})};
            };
        }""", {"initial": initial, "sizing": sizing, "edited": edited})
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-geometry.js"))
        await self.page.evaluate("window.posterGeometryReady")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-resize.js"))
        await self.page.wait_for_function("document.querySelector('.resize-overlay')")
        await self.page.wait_for_timeout(50)
        self.assertEqual(self.errors, [])

    async def all_areas(self) -> dict:
        return await self.page.locator("#poster-header, #layout, #layout .pane, #layout .section, #layout .section-parent").evaluate_all(
            """elements => Object.fromEntries(elements.map(element => {
                const style = getComputedStyle(element);
                return [element.id, [parseFloat(style.width), parseFloat(style.height)]];
            }))""",
        )

    async def test_bottom_gutter_matches_header_gap_with_saved_geometry_and_canvas_resize(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                await self.load(template)
                initial = await self.page.evaluate("geometryRequests[0].data.geometry")
                for factor in (0.9, 1.05):
                    with self.subTest(saved_layout_factor=factor):
                        await self.load(template, initial=initial | {"layout": initial["layout"] * factor})
                        for height_delta, zoom in ((0, "100"), (500, "50")):
                            await self.page.evaluate("""({delta, zoom}) => {
                                const canvas = document.getElementById('poster-canvas');
                                canvas.style.height = `${parseFloat(canvas.style.height) + delta}px`;
                                const slider = document.getElementById('poster-zoom');
                                slider.value = zoom;
                                slider.dispatchEvent(new Event('input'));
                            }""", {"delta": height_delta, "zoom": zoom})
                            await self.page.wait_for_timeout(100)
                            gaps = await self.page.evaluate("""() => {
                                const rect = id => document.getElementById(id).getBoundingClientRect();
                                const content = rect('poster-content');
                                const header = rect('poster-header');
                                const layout = rect('layout');
                                const paneBottom = Math.max(...[...document.querySelectorAll('#layout > .pane')]
                                    .map(pane => pane.getBoundingClientRect().bottom));
                                return {top: layout.top - header.bottom, bottom: content.bottom - layout.bottom,
                                    paneBottom: layout.bottom - paneBottom};
                            }""")
                            self.assertGreater(gaps["top"], 0)
                            self.assertAlmostEqual(gaps["bottom"], gaps["top"], delta=0.1)
                            self.assertAlmostEqual(gaps["paneBottom"], 0, delta=0.1)

    async def test_initial_geometry_does_not_change_with_content_or_any_font_target(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                await self.load(template)
                before = await self.all_areas()
                await self.page.evaluate("""() => {
                    for (const element of document.querySelectorAll('#layout .content')) {
                        element.textContent = 'Additional scientific observations. '.repeat(1000);
                    }
                    for (const target of ['sections', 'title', 'authors']) {
                        document.getElementById('poster-canvas').style.setProperty(`--${target}-font-scale`, 1.5);
                    }
                    document.getElementById('title').textContent = 'A much longer title '.repeat(40);
                    document.getElementById('authors').textContent = 'Additional author '.repeat(50);
                    document.getElementById('poster-canvas').dispatchEvent(new Event('poster-font-size'));
                }""")
                await self.page.wait_for_timeout(150)
                self.assertEqual(await self.all_areas(), before)
                self.assertTrue(await self.page.locator("#results").evaluate(
                    "element => element.scrollHeight > element.clientHeight && getComputedStyle(element).overflow === 'hidden'",
                ))
                self.assertEqual(await self.page.evaluate("geometryRequests.length"), 1)
                self.store.get.return_value = replace(DRAFT, template=template)
                measured = await self.page.evaluate("geometryRequests[0].data.geometry")
                self.store.set_layout_geometry.return_value = posters.LayoutGeometry.model_validate(measured)
                response = self.client.post("/layout/geometry", json={
                    "pid": 11, "template": template, "geometry": measured,
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.errors, [])

    async def test_reopening_restores_baseline_and_manual_resizing_still_works(self) -> None:
        for template in posters.TEMPLATES:
            with self.subTest(template=template):
                await self.load(template)
                initial = await self.page.evaluate("geometryRequests[0].data.geometry")
                before = await self.all_areas()
                await self.load(template, initial=initial, edited=True)
                self.assertEqual(await self.all_areas(), before)
                self.assertEqual(await self.page.evaluate("geometryRequests.length"), 0)
                await self.drag("introduction", y=10000)
                current = await self.geometry()
                self.assertAlmostEqual(
                    current["introduction"]["height"] /
                    (current["introduction"]["height"] + current["methods"]["height"]), 0.75, delta=0.001,
                )
                await self.page.wait_for_timeout(350)
                sizing = await self.page.evaluate("geometryRequests.at(-1).data.sizing")
                resized = await self.all_areas()
                await self.load(template, initial=initial, sizing=sizing, edited=True)
                self.assertEqual(await self.all_areas(), resized)

    async def test_storage_failure_is_visible_without_releasing_fixed_areas(self) -> None:
        await self.load()
        await self.page.goto("about:blank")
        await self.editor()
        await self.page.evaluate("""() => {
            window.fetch = async () => ({ok: false, json: async () => ({detail: 'Storage unavailable'})});
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-geometry.js"))
        await self.page.evaluate("window.posterGeometryReady")
        self.assertIn("Storage unavailable", await self.page.locator("#content-save-status").inner_text())
        self.assertEqual(await self.page.locator("#poster-content").get_attribute("data-fixed-geometry"), "true")

    async def test_manual_columns_bottom_and_sections_keep_their_adjustments_after_edits(self) -> None:
        await self.load()
        initial = await self.geometry()
        await self.drag("left", x=initial["layout"]["width"] * 0.1)
        await self.drag("bottom", y=-initial["layout"]["height"] * 0.1)
        await self.drag("pane3-0", x=-10000)
        before = await self.all_areas()
        await self.page.evaluate("""() => {
            document.querySelector('#citations .content').textContent = 'References '.repeat(1000);
            document.getElementById('poster-canvas').style.setProperty('--sections-font-scale', 1.5);
            document.getElementById('poster-canvas').dispatchEvent(new Event('poster-font-size'));
        }""")
        await self.page.wait_for_timeout(150)
        self.assertEqual(await self.all_areas(), before)
        geometry = await self.geometry()
        self.assertAlmostEqual(
            (geometry["pane0"]["width"] - initial["pane0"]["width"]) / geometry["layout"]["width"],
            0.1, delta=0.001,
        )
        self.assertAlmostEqual(geometry["citations"]["width"] / geometry["layout"]["width"], 0.2, delta=0.001)
        self.assertEqual(self.errors, [])
