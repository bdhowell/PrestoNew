import unittest
import asyncio
import os
import tempfile
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import cv2
import numpy
import psycopg

import content
import graphics
from tests.test_layout import DRAFT, LayoutDatabaseTests, LayoutRouteTests


class GraphicsSelectionTests(unittest.TestCase):
    def test_complexity_matches_canny_formula_and_threshold(self) -> None:
        image = numpy.random.default_rng(42).integers(0, 256, (150, 200), dtype=numpy.uint8)
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        edges = cv2.Canny(cv2.GaussianBlur(image, (3, 3), 0), 50, 150)
        expected = numpy.count_nonzero(edges) / image.size / (image.size / 10000) ** 0.15
        with patch.dict(os.environ, {"IMAGE_COMPLEXITY_THRESHOLD": str(expected),
                                    "IMAGE_COMPLEXITY_PENALTY_POWER": "0.15"}):
            result = graphics.image_complexity(encoded.tobytes())
            self.assertAlmostEqual(result["score"], expected)
            self.assertFalse(result["too_complex"])
        with patch.dict(os.environ, {"IMAGE_COMPLEXITY_THRESHOLD": str(expected / 2)}):
            self.assertTrue(graphics.image_complexity(encoded.tobytes())["too_complex"])

    def test_complexity_accepts_jpeg_and_rejects_bad_inputs(self) -> None:
        success, encoded = cv2.imencode(".jpg", numpy.zeros((20, 20), dtype=numpy.uint8))
        self.assertTrue(success)
        self.assertEqual(graphics.image_complexity(encoded.tobytes())["score"], 0)
        with self.assertRaises(ValueError):
            graphics.image_complexity(b"not an image")
        for key, value in (("IMAGE_COMPLEXITY_THRESHOLD", "nan"),
                           ("IMAGE_COMPLEXITY_THRESHOLD", "1.1"),
                           ("IMAGE_COMPLEXITY_PENALTY_POWER", "-1")):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaises(ValueError):
                    graphics.image_complexity(encoded.tobytes())

    def test_math_only_sections_keep_source_order(self) -> None:
        items = [
            content.GraphicSource(1, "image.png"),
            content.GraphicSource(2, "formula.png", type="math"),
            content.GraphicSource(3, "qr.png", kind="qrcode", type="math"),
            content.GraphicSource(4, "formula2.png", type="math"),
        ]
        for section in ("abstract", "introduction", "conclusion", "future plans"):
            with self.subTest(section=section):
                self.assertEqual([item.sid for item in graphics.suitable_graphics(section, items)], [2, 4])

    def test_assessed_sections_order_by_type_then_section_rating(self) -> None:
        items = [
            content.GraphicSource(1, "low.png", assessments={"results": {"rating": "weakly"}}),
            content.GraphicSource(2, "high.png", assessments={"results": {"rating": "highly"}}),
            content.GraphicSource(3, "other.svg", type="svg"),
            content.GraphicSource(4, "unrated.png"),
            content.GraphicSource(5, "qr.png", kind="qrcode"),
            content.GraphicSource(6, "unknown", type="unsupported"),
        ]
        self.assertEqual([item.sid for item in graphics.suitable_graphics("results", items)], [3, 2, 1, 4])
        self.assertEqual([item.sid for item in graphics.suitable_graphics("materials and methods", items)], [3, 1, 2, 4])

    def test_restricted_sections_are_rejected(self) -> None:
        for section in ("additional information", "literature cited", "acknowledgements", "title"):
            with self.subTest(section=section), self.assertRaises(ValueError):
                graphics.suitable_graphics(section, [])

    def test_graphic_is_rebuilt_from_owned_source_and_round_trips(self) -> None:
        source = content.GraphicSource(21, '<chart>.png')
        value = (
            '<p>Before</p><span class="graphic" data-sid="21" data-type="png" '
            'data-width="320" data-placement="top-left"><img src="https://evil.test/image" '
            'onerror="alert(1)"></span><p>After</p>'
        )
        result = graphics.clean_section(value, sources=[source], section="results")
        self.assertIn('src="/layout/source?sid=21"', result)
        self.assertIn('alt="&lt;chart&gt;.png"', result)
        self.assertIn('data-placement="top-left"', result)
        self.assertNotIn("evil.test", result)
        self.assertNotIn("onerror", result)
        self.assertEqual(graphics.clean_section(result), result)

    def test_rejects_graphics_from_another_poster_or_wrong_section(self) -> None:
        value = '<span class="graphic" data-sid="21" data-type="png"></span>'
        with self.assertRaises(ValueError):
            graphics.clean_section(value, sources=[], section="results")
        with self.assertRaises(ValueError):
            graphics.clean_section(value, sources=[content.GraphicSource(21, "a.png")],
                                   section="introduction")

    def test_math_uses_source_expressions_not_submitted_markup(self) -> None:
        value = (
            '<span class="graphic" data-sid="21" data-type="math">'
            '<span class="graphic-math">forged</span></span>'
        )
        source = content.GraphicSource(21, "formula.png", type="math", math_expressions=[r"x < y"])
        result = graphics.clean_section(value, sources=[source], section="abstract")
        self.assertIn(r'\[x &lt; y\]', result)
        self.assertNotIn("forged", result)
        self.assertEqual(graphics.clean_section(result), result)

    def test_qr_is_scannable_and_rejects_non_http_urls(self) -> None:
        png = graphics.qr_png("https://example.test/subject?q=poster")
        image = cv2.imdecode(numpy.frombuffer(png, dtype=numpy.uint8), cv2.IMREAD_GRAYSCALE)
        decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
        self.assertEqual(decoded, "https://example.test/subject?q=poster")
        for value in ("javascript:alert(1)", "https://user:pass@example.test", "https://bad host/", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                graphics.qr_png(value)

    def test_qr_replacement_preserves_text_and_other_graphics(self) -> None:
        section = '<p>Contact us</p>' + graphics.replace_qr("", 8) + "<p>More details</p>"
        result = graphics.replace_qr(section, 9)
        self.assertIn("<p>Contact us</p>", result)
        self.assertIn("<p>More details</p>", result)
        self.assertNotIn('data-sid="8"', result)
        self.assertEqual(graphics.graphic_ids(result), {9})
        self.assertEqual(graphics.clean_section(result), result)


class GraphicsCatalogRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        LayoutRouteTests.setUp(self)
        self.store.invalidate = Mock()
        self.sources = AsyncMock()
        self.sources.graphic_sources.return_value = [
            content.GraphicSource(21, "formula.png", type="math", math_expressions=["x^2"]),
            content.GraphicSource(22, "chart.png", assessments={"results": {"rating": "highly"}}),
        ]
        _, encoded = cv2.imencode(".png", numpy.zeros((20, 20), dtype=numpy.uint8))
        self.sources.graphic_source.return_value = (encoded.tobytes(), "image/png")
        mocked = patch.object(content, "store", return_value=self.sources)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_catalog_is_owner_scoped_and_filtered(self) -> None:
        response = self.client.get("/layout/graphics", params={"pid": 11, "section": "introduction"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual([item["sid"] for item in response.json()["graphics"]], [21])
        self.assertEqual(response.json()["graphics"][0]["math_expressions"], ["x^2"])
        self.store.get.assert_awaited_once_with(7, 11)
        self.sources.graphic_sources.assert_awaited_once_with(7, 11)

    def test_catalog_checks_identity_ownership_and_draft(self) -> None:
        params = {"pid": 11, "section": "results"}
        self.uid = None
        self.assertEqual(self.client.get("/layout/graphics", params=params).status_code, 401)
        self.uid = 7
        self.store.get.return_value = None
        self.assertEqual(self.client.get("/layout/graphics", params=params).status_code, 404)
        self.store.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.get("/layout/graphics", params=params).status_code, 409)
        self.sources.graphic_sources.assert_not_awaited()

    def test_invalid_section_or_poster_is_rejected(self) -> None:
        for section in ("additional information", "literature cited", "acknowledgements", "invalid"):
            with self.subTest(section=section):
                self.assertEqual(self.client.get(
                    "/layout/graphics", params={"pid": 11, "section": section},
                ).status_code, 422)
        self.assertEqual(self.client.get(
            "/layout/graphics", params={"pid": 0, "section": "results"},
        ).status_code, 422)
        self.sources.graphic_sources.assert_not_awaited()

    def test_storage_errors_are_reported(self) -> None:
        self.sources.graphic_sources.side_effect = psycopg.OperationalError("down")
        with self.assertLogs("layout", "ERROR"):
            response = self.client.get("/layout/graphics", params={"pid": 11, "section": "results"})
        self.assertEqual(response.status_code, 503)

    def test_catalog_omits_complex_raster_but_not_svg(self) -> None:
        image = numpy.random.default_rng(8).integers(0, 256, (100, 100), dtype=numpy.uint8)
        _, encoded = cv2.imencode(".png", image)
        self.sources.graphic_source.return_value = (encoded.tobytes(), "image/png")
        self.sources.graphic_sources.return_value.append(
            content.GraphicSource(23, "vector.svg", mimetype="image/svg+xml", type="svg")
        )
        with patch.dict(os.environ, {"IMAGE_COMPLEXITY_THRESHOLD": "0.01"}):
            response = self.client.get("/layout/graphics", params={"pid": 11, "section": "results"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["sid"] for item in response.json()["graphics"]], [23])
        self.assertCountEqual([call.args for call in self.sources.graphic_source.await_args_list],
                              [(7, 11, 21), (7, 11, 22)])

    def test_invalid_raster_reports_catalog_error(self) -> None:
        self.sources.graphic_source.return_value = (b"invalid", "image/png")
        with self.assertLogs("layout", "ERROR"):
            response = self.client.get("/layout/graphics", params={"pid": 11, "section": "results"})
        self.assertEqual(response.status_code, 503)

    def test_saving_graphics_checks_owner_source_and_section(self) -> None:
        self.store.save_section.return_value = True
        graphic = '<span class="graphic" data-sid="22" data-type="png" data-width="200"></span>'
        response = self.client.post("/layout/content", json={
            "pid": 11, "section": "results", "content": f"<p>Text</p>{graphic}",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('src="/layout/source?sid=22"', response.json()["content"])
        self.assertEqual(self.store.save_section.await_args.args[3], response.json()["content"])
        self.assertEqual(self.client.post("/layout/content", json={
            "pid": 11, "section": "introduction", "content": graphic,
        }).status_code, 422)
        self.assertEqual(self.client.post("/layout/content", json={
            "pid": 11, "section": "results",
            "content": '<span class="graphic" data-sid="999" data-type="png"></span>',
        }).status_code, 422)

    def test_qr_status_and_generation_are_owner_scoped(self) -> None:
        self.sources.qr_exists.return_value = True
        self.sources.save_qr.return_value = '<p>QR saved</p>'
        response = self.client.get("/layout/qrcode/status", params={"pid": 11})
        self.assertEqual(response.json(), {"ok": True, "exists": True})
        response = self.client.post("/layout/qrcode", json={
            "pid": 11, "url": "https://example.test/", "replace": True,
        })
        self.assertEqual(response.json(), {"ok": True, "content": "<p>QR saved</p>"})
        self.assertEqual(self.sources.save_qr.await_args.args[:2], (7, 11))
        self.assertTrue(self.sources.save_qr.await_args.kwargs["replace"])
        self.assertTrue(self.sources.save_qr.await_args.args[2].startswith(b"\x89PNG"))
        self.assertEqual(self.client.post("/layout/qrcode", json={
            "pid": 11, "url": "javascript:alert(1)", "replace": False,
        }).status_code, 422)
        self.store.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.post("/layout/qrcode", json={
            "pid": 11, "url": "https://example.test/", "replace": False,
        }).status_code, 409)


@unittest.skipUnless(os.environ.get("LAYOUT_TEST_DATABASE_URL"), "Set LAYOUT_TEST_DATABASE_URL for PostgreSQL tests")
class GraphicsDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        LayoutDatabaseTests.setUp(self)
        self.store = content.SourceStore("LAYOUT_TEST_DATABASE_URL", self.schema)

    def test_qr_source_and_section_are_replaced_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"SOURCES_DIR": directory, "GCS_BUCKET": ""},
        ):
            first = graphics.qr_png("https://example.test/first")
            second = graphics.qr_png("https://example.test/second")
            self.connection.execute(
                "INSERT INTO section (pid, which, content, updated) "
                "VALUES (%s, 'additional information', '<p>Contact</p>', now())", (self.pid,),
            )
            original_text = asyncio.run(self.store.source_text(7, self.pid))
            original_completion = asyncio.run(self.store.completion(7, self.pid))
            original_evaluation = asyncio.run(self.store.evaluation_sources(7, self.pid))
            self.assertFalse(asyncio.run(self.store.qr_exists(7, self.pid)))
            section = asyncio.run(self.store.save_qr(7, self.pid, first, replace=False))
            self.assertIn("<p>Contact</p>", section)
            sid = next(iter(graphics.graphic_ids(section)))
            self.assertEqual(asyncio.run(self.store.source_text(7, self.pid)), original_text)
            self.assertEqual(asyncio.run(self.store.completion(7, self.pid)), original_completion)
            self.assertEqual(asyncio.run(self.store.evaluation_sources(7, self.pid)), original_evaluation)
            qr_source = next(item for item in asyncio.run(self.store.list_sources(7, self.pid)) if item.sid == sid)
            self.assertTrue(qr_source.extracted)
            asyncio.run(self.store.extract(7, self.pid, sid))
            self.assertEqual(self.connection.execute(
                "SELECT kind FROM source WHERE sid = %s", (sid,),
            ).fetchone()[0], "qrcode")
            self.assertFalse(asyncio.run(self.store.save_graphic_evaluation(
                7, self.pid, sid, {}, "unused",
            )))
            self.assertEqual(asyncio.run(self.store.graphic_source(7, self.pid, sid)), (first, "image/png"))
            with self.assertRaises(content.SourceConflictError):
                asyncio.run(self.store.save_qr(7, self.pid, second, replace=False))
            updated = asyncio.run(self.store.save_qr(7, self.pid, second, replace=True))
            self.assertEqual(graphics.graphic_ids(updated), {sid})
            self.assertEqual(asyncio.run(self.store.graphic_source(7, self.pid, sid)), (second, "image/png"))
            self.assertEqual(self.connection.execute(
                "SELECT count(*) FROM source WHERE pid = %s AND kind = 'qrcode'", (self.pid,),
            ).fetchone()[0], 1)
            self.assertEqual(self.connection.execute(
                "SELECT content FROM section WHERE pid = %s AND which = 'additional information'",
                (self.pid,),
            ).fetchone()[0], updated)
