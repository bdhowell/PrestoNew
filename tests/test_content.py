import asyncio
import io
import json
import os
import tempfile
import unittest
import uuid
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import quote

import psycopg
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from google.api_core.exceptions import Forbidden, NotFound, ServiceUnavailable
from starlette.requests import HTTPConnection

import content
import source_processing
import posters
from evaluation import EvaluationResponse
from tests import test_posters
from routers import content as routes
from tests.test_posters import DRAFT, ROOT

ITEM = content.Source(1, "<paper>.pdf", "application/pdf", 12, "2026-10-25")


class ContentRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.uid = 7
        self.session = {"poster_id": 11}
        self.posters = AsyncMock(spec=posters.PosterStore)
        self.posters.get.return_value = DRAFT
        self.sources = AsyncMock()
        self.sources.list_sources.return_value = []
        self.sources.add.return_value = ITEM
        self.sources.completion.return_value = (0, 0)
        self.sources.evaluation_sources.return_value = []
        for target, value in (("posters.store", self.posters), ("content.store", self.sources)):
            mocked = patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

        async def identity(connection: HTTPConnection) -> None:
            connection.state.user_id = self.uid
            connection.state.user = {"user-name": "<Ada>"}
            connection.scope["session"] = self.session

        application = FastAPI(dependencies=[Depends(identity)])
        application.include_router(routes.router)
        application.mount("/static", StaticFiles(directory=ROOT / "static"))
        self.client = TestClient(application)

    def upload(self, data=b"%PDF-1.7\n", filename="paper.pdf", **kwargs):
        return self.client.post("/content/upload", content=data, headers={
            "Content-Type": "application/octet-stream", "X-Filename": quote(filename),
            **kwargs,
        })

    def test_empty_page_shows_introduction_and_all_supported_extensions(self) -> None:
        response = self.client.get("/content/upload")
        self.assertEqual(response.status_code, 200)
        self.assertIn("upload your source content", response.text)
        self.assertIn("Apple Numbers, Pages, and Keynote", response.text)
        self.assertIn("Markdown (MD)", response.text)
        self.assertIn('id="items-list"', response.text)
        self.assertIn("/static/javascript/pages/content-upload.js", response.text)
        for suffix in content.FORMATS:
            self.assertIn(suffix, response.text)
        self.sources.list_sources.assert_awaited_once_with(7, 11)

    def test_content_pages_save_the_requested_resumption_url(self) -> None:
        for path in ("/content/upload", "/content/extract", "/content/elucidate", "/content/evaluate"):
            for query in ("tag=a%2Fb&tag=two", "tag=a%2Fb&tag=two&xyzzy=arpa"):
                with self.subTest(path=path, query=query):
                    self.posters.set_resumption.reset_mock()
                    response = self.client.get(f"{path}?{query}")
                    self.assertEqual(response.status_code, 200)
                    marker = "" if query.endswith("xyzzy=arpa") else "&xyzzy=arpa"
                    self.posters.set_resumption.assert_awaited_once_with(
                        7, 11, f"http://testserver{path}?{query}{marker}",
                    )

    def test_elucidation_page_shows_wikkit_writing_graphic(self) -> None:
        response = self.client.get("/content/elucidate")
        self.assertEqual(response.status_code, 200)
        self.assertIn('/static/graphics/wikkitwriting.png', response.text)
        self.assertIn('alt="Wikkit writing"', response.text)

    def test_existing_files_escape_names_and_do_not_repeat_first_upload_intro(self) -> None:
        self.sources.list_sources.return_value = [ITEM]
        response = self.client.get("/content/upload")
        self.assertIn("&lt;paper&gt;.pdf", response.text)
        self.assertNotIn("upload your source content", response.text)
        self.assertNotIn("Rich Text Format", response.text)

    def test_reduced_conversion_notice_is_visible_on_page_and_upload_response(self) -> None:
        with patch.dict(os.environ, {"DOCLING_ENABLE_ENRICHMENT": "false"}):
            page = self.client.get("/content/upload")
            response = self.upload()
        self.assertIn(source_processing.REDUCED_FEATURE_NOTICE, page.text)
        self.assertEqual(response.status_code, 201)
        self.assertIn(source_processing.REDUCED_FEATURE_NOTICE, response.json()["say"])
        with patch.dict(os.environ, {"DOCLING_ENABLE_ENRICHMENT": "true"}):
            self.assertNotIn(source_processing.REDUCED_FEATURE_NOTICE, self.client.get("/content/upload").text)

    def test_upload_returns_metadata_and_passes_owner_checked_poster(self) -> None:
        response = self.upload(filename="résumé.pdf")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["item"]["sid"], 1)
        self.assertNotIn("storage_uri", response.text)
        self.sources.add.assert_awaited_once_with(7, 11, "résumé.pdf", b"%PDF-1.7\n")
        self.posters.get.assert_awaited_once_with(7, 11)

    def test_upload_requires_identity_owned_selected_draft(self) -> None:
        self.uid = None
        response = self.client.get("/content/upload", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/account/signin")
        self.assertEqual(self.upload().status_code, 401)
        self.uid = 7
        self.session.clear()
        self.assertEqual(self.upload().status_code, 409)
        self.session["poster_id"] = 11
        self.posters.get.return_value = None
        self.assertEqual(self.upload().status_code, 404)
        self.posters.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.upload().status_code, 409)
        self.sources.add.assert_not_awaited()

    def test_invalid_requests_are_rejected_before_storage(self) -> None:
        self.assertEqual(self.client.post("/content/upload", json={}).status_code, 415)
        self.assertEqual(self.upload(Origin="https://evil.example").status_code, 403)
        for name in ("", "../paper.pdf", "folder\\paper.pdf", "file.exe", "a\n.pdf", "a" * 260 + ".pdf", "file.rtf"):
            self.assertEqual(self.upload(filename=name).status_code, 422)
        self.sources.add.assert_not_awaited()

    def test_streamed_upload_limit_is_enforced_without_content_length(self) -> None:
        with patch.dict(os.environ, {"POSTER_UPLOAD_MAX_MB": "1"}):
            self.assertEqual(self.upload(data=(b"x" * 524288 for _ in range(3))).status_code, 413)
        self.sources.add.assert_not_awaited()

    def test_validation_and_storage_failures_are_explicit(self) -> None:
        self.sources.add.side_effect = ValueError("Empty files cannot be uploaded.")
        self.assertEqual(self.upload(data=b"").status_code, 422)
        for error in (psycopg.OperationalError("down"), OSError("disk full"), ServiceUnavailable("down")):
            self.sources.add.side_effect = error
            with self.assertLogs("routers.content", "ERROR"):
                self.assertEqual(self.upload().status_code, 503)
        self.sources.list_sources.side_effect = psycopg.OperationalError("down")
        with self.assertLogs("routers.content", "ERROR"):
            self.assertEqual(self.client.get("/content/upload").status_code, 503)

    def test_extracted_checks_empty_no_text_and_exact_configured_threshold(self) -> None:
        with patch.dict(os.environ, {"SOURCE_TEXT_MIN_LENGTH": "4000"}):
            for count, length, message in (
                (0, 0, "you can&rsquo;t have a poster without any content!"),
                (1, 0, "I can&rsquo;t magic a complete poster out of thin air."),
                (1, 3999, "I&rsquo;m unable to conjure enough content to create a useful poster."),
            ):
                self.sources.completion.return_value = (count, length)
                response = self.client.post("/content/extracted", json={})
                self.assertEqual(response.status_code, 409)
                self.assertNotIn("redirect", response.json())
                self.assertIn("&lt;Ada&gt;", response.json()["say"])
                self.assertIn(message, response.json()["say"])
            for length in (4000, 4001):
                self.sources.completion.return_value = (1, length)
                response = self.client.post("/content/extracted", json={})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"ok": True, "redirect": "/content/elucidate", "say": ""})
        self.sources.completion.assert_awaited_with(7, 11)

    def test_finish_requires_same_origin_json_and_editable_owner(self) -> None:
        self.assertEqual(self.client.post("/content/finished", json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/content/finished", content="").status_code, 415)
        self.posters.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.post("/content/finished", json={}).status_code, 409)
        self.sources.completion.assert_not_awaited()

    def test_extracted_default_minimum_is_four_thousand_and_errors_are_visible(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.sources.completion.return_value = (1, 3999)
            self.assertEqual(self.client.post("/content/extracted", json={}).status_code, 409)
            self.sources.completion.return_value = (1, 4000)
            self.assertEqual(self.client.post("/content/extracted", json={}).status_code, 200)
        self.sources.completion.side_effect = psycopg.OperationalError("down")
        with self.assertLogs("routers.content", "ERROR"):
            self.assertEqual(self.client.post("/content/extracted", json={}).status_code, 503)

    def test_finished_uploads_go_to_reading_page_before_text_validation(self) -> None:
        self.assertEqual(self.client.post("/content/finished", json={}).status_code, 409)
        self.sources.list_sources.return_value = [replace(ITEM, extracted=False)]
        response = self.client.post("/content/finished", json={})
        self.assertEqual(response.json(), {"ok": True, "redirect": "/content/extract"})
        self.sources.completion.assert_not_awaited()

    def test_reading_page_lists_only_pending_originals_and_announces_upload_completion(self) -> None:
        self.sources.list_sources.return_value = [
            replace(ITEM, extracted=False), replace(ITEM, sid=2),
            replace(ITEM, sid=3, parent=1, extracted=False),
        ]
        response = self.client.get("/content/extract")
        self.assertEqual(response.status_code, 200)
        self.assertIn("your content has finished uploading", response.text)
        self.assertIn("reading through it now", response.text)
        self.assertIn('/static/graphics/wikkitbooks.png', response.text)
        self.assertIn('alt="Wikkit reading from books"', response.text)
        self.assertIn("/static/javascript/pages/content-extract.js", response.text)
        self.assertIn('"sid": 1', response.text)
        self.assertNotIn('"sid": 2', response.text)
        self.assertNotIn('"sid": 3', response.text)
        self.assertNotIn("<paper>.pdf", response.text)
        self.uid = None
        response = self.client.get("/content/extract", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/account/signin")

    def test_extraction_is_owner_checked_and_reports_failures(self) -> None:
        response = self.client.post("/content/extract/1", json={})
        self.assertEqual(response.json(), {"ok": True})
        self.sources.extract.assert_awaited_once_with(7, 11, 1)
        for error, code in (
            (content.SourceConflictError("Source not found"), 409),
            (source_processing.ProcessingError("Docling is unavailable"), 503),
            (OSError("missing original"), 503),
            (ValueError("Invalid source"), 422),
        ):
            self.sources.extract.side_effect = error
            if code == 409:
                self.assertEqual(self.client.post("/content/extract/1", json={}).status_code, code)
            else:
                with self.assertLogs("routers.content", "ERROR"):
                    self.assertEqual(self.client.post("/content/extract/1", json={}).status_code, code)

    def test_extraction_rejects_cross_origin_non_json_and_unowned_poster(self) -> None:
        for url in ("/content/extract/1", "/content/extracted"):
            self.assertEqual(self.client.post(url, json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
            self.assertEqual(self.client.post(url, content="").status_code, 415)
        self.posters.get.return_value = None
        self.assertEqual(self.client.post("/content/extract/1", json={}).status_code, 404)
        self.sources.extract.assert_not_awaited()

    def test_completion_cannot_skip_pending_extraction(self) -> None:
        self.sources.list_sources.return_value = [replace(ITEM, extracted=False)]
        self.assertEqual(self.client.post("/content/extracted", json={}).status_code, 409)
        self.sources.completion.assert_not_awaited()

    def test_processing_service_configuration_errors_are_reported_explicitly(self) -> None:
        self.sources.add.side_effect = source_processing.ProcessingError("DOCLING_SERVICE_URL is required")
        with self.assertLogs("routers.content", "ERROR"):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertIn("DOCLING_SERVICE_URL", response.json()["detail"])

    def test_size_warning_counts_exact_threshold_and_first_description_line(self) -> None:
        small = replace(ITEM, width=299, height=1000, description="<Logo>\nsecond line")
        large = replace(ITEM, sid=2, width=300, height=300)
        self.assertTrue(small.ill_sized)
        self.assertFalse(large.ill_sized)
        self.assertFalse(ITEM.ill_sized)
        self.assertIn("One file will likely", routes.size_warning([small, large]))
        self.assertIn("Two files will likely", routes.size_warning([small, small]))
        self.sources.list_sources.return_value = [small, large]
        response = self.client.get("/content/upload")
        self.assertIn('class="ill-sized"', response.text)
        self.assertIn("&lt;Logo&gt;", response.text)
        self.assertNotIn("second line", response.text)
        self.assertIn("/content/1/thumbnail", response.text)
        self.assertIn('id="finish-upload"', response.text)

    def test_thumbnail_is_owner_checked_and_reencoded_as_private_png(self) -> None:
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (1000, 500)).save(buffer, format="JPEG")
        self.sources.graphic.return_value = buffer.getvalue()
        response = self.client.get("/content/1/thumbnail")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        with Image.open(io.BytesIO(response.content)) as image:
            self.assertEqual(image.size, (160, 80))
            self.assertEqual(image.format, "PNG")
        self.sources.graphic.assert_awaited_once_with(7, 11, 1)
        self.sources.graphic.return_value = None
        self.assertEqual(self.client.get("/content/99/thumbnail").status_code, 404)

    def test_graphic_evaluation_page_shows_counts_and_uses_selected_poster(self) -> None:
        self.sources.evaluation_sources.return_value = [
            content.GraphicEvaluationSource(1, "upload.png", "image/png", None, None, None),
            content.GraphicEvaluationSource(2, "extract.png", "image/png", 1, None, None),
        ]
        response = self.client.get("/content/evaluate")
        self.assertEqual(response.status_code, 200)
        self.assertIn("one graphic you uploaded", response.text)
        self.assertIn("one graphic I was able to extract", response.text)
        self.assertIn('/static/graphics/wikkitmeasuring.png', response.text)
        self.assertIn('alt="Wikkit measuring a graphic"', response.text)
        self.assertIn("/static/javascript/pages/content-evaluate.js", response.text)
        self.assertIn('"pid": 11', response.text)
        self.sources.evaluation_sources.assert_awaited_once_with(7, 11)

    def test_graphic_evaluation_streams_persists_and_groups_source_results(self) -> None:
        self.sources.evaluation_sources.return_value = [
            content.GraphicEvaluationSource(1, "uploaded.png", "image/png", None, None, None),
            content.GraphicEvaluationSource(2, "extracted.jpg", "image/jpeg", 1, None, None),
        ]
        self.sources.source_text.return_value = content.SourceText(
            1, "Original research context", "fingerprint", 27,
        )
        self.posters.content.return_value = (
            [("materials and methods", "<p>Experimental methods</p>"),
             ("results", "<p>Observed results</p>")], [],
        )
        self.sources.graphic_source.return_value = (b"image-bytes", "image/png")
        self.sources.save_graphic_evaluation.return_value = True
        evaluation_result = {
            "image_analysis": {
                "classification": "image",
                "math_expressions": [],
                "description": "A research image",
            },
            "evaluations": {
                "materials_and_methods": {"rating": "significantly"},
                "results": {"rating": "weakly"},
            },
        }
        client = Mock()
        with patch.object(routes.openai, "OpenAI", return_value=client) as openai_client, \
                patch.object(
                    routes.graphic_evaluation, "evaluate_image",
                    return_value=Mock(model_dump=Mock(return_value=evaluation_result)),
                ) as evaluate:
            response = self.client.post("/content/evaluate/11", json={})

        self.assertEqual(response.status_code, 200)
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(
            [event["type"] for event in events],
            ["start", "progress", "result", "progress", "result", "complete"],
        )
        self.assertEqual(events[0], {
            "type": "start", "total": 2, "uploaded": 1, "extracted": 1,
        })
        self.assertEqual(
            [events[index]["source"]["category"] for index in (2, 4)],
            ["uploaded", "extracted"],
        )
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(evaluate.call_args.args[1:], (
            "Original research context", "Experimental methods", "Observed results",
        ))
        self.assertEqual(self.sources.save_graphic_evaluation.await_count, 2)
        self.assertEqual(
            self.sources.save_graphic_evaluation.await_args_list[0].args,
            (
                7, 11, 1, evaluation_result,
                content.graphic_evaluation_hash("fingerprint", "Experimental methods", "Observed results", None),
            ),
        )
        openai_client.assert_called_once_with()
        client.close.assert_called_once_with()

    def test_graphic_evaluation_is_owner_checked_and_reports_empty_sources(self) -> None:
        self.sources.evaluation_sources.return_value = []
        response = self.client.post("/content/evaluate/11", json={})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(response.status_code, 200)
        self.assertEqual([event["type"] for event in events], ["start", "empty"])
        self.assertEqual(events[0]["total"], 0)
        self.assertEqual(self.client.post("/content/evaluate/12", json={}).status_code, 409)
        self.assertEqual(self.client.post(
            "/content/evaluate/11", json={}, headers={"Origin": "https://evil.example"},
        ).status_code, 403)
        self.assertEqual(self.client.post("/content/evaluate/11", content="").status_code, 415)
        self.uid = None
        self.assertEqual(self.client.post("/content/evaluate/11", json={}).status_code, 401)

    def test_graphic_evaluation_skips_to_layout_for_current_saved_results(self) -> None:
        evaluation_result = {
            "image_analysis": {"classification": "image", "math_expressions": []},
            "evaluations": {},
        }
        self.sources.evaluation_sources.return_value = [
            content.GraphicEvaluationSource(
                1, "saved.png", "image/png", None, evaluation_result, None,
            ),
        ]
        self.sources.source_text.return_value = content.SourceText(
            1, "Original research context", "fingerprint", 27,
        )
        self.posters.content.return_value = ([], [])
        with patch.object(routes.openai, "OpenAI") as openai_client:
            response = self.client.post("/content/evaluate/11", json={})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([event["type"] for event in events], ["start", "already-evaluated"])
        openai_client.assert_not_called()
        self.sources.graphic_source.assert_not_awaited()
        self.sources.save_graphic_evaluation.assert_not_awaited()

    def test_graphic_evaluation_recalculates_saved_results_without_latex_field(self) -> None:
        legacy_result = {
            "image_analysis": {"classification": "image"},
            "evaluations": {},
        }
        evaluation_result = {
            "image_analysis": {"classification": "chart", "math_expressions": []},
            "evaluations": {},
        }
        expected_hash = content.graphic_evaluation_hash(
            "fingerprint", "No Materials and Methods text was provided.",
            "No Results text was provided.", None,
        )
        self.sources.evaluation_sources.return_value = [
            content.GraphicEvaluationSource(
                1, "saved.png", "image/png", None, legacy_result, None,
            ),
        ]
        self.sources.source_text.return_value = content.SourceText(
            1, "Original research context", "fingerprint", 27,
        )
        self.posters.content.return_value = ([], [])
        self.sources.graphic_source.return_value = (b"image-bytes", "image/png")
        self.sources.save_graphic_evaluation.return_value = True
        with patch.object(routes.openai, "OpenAI"), patch.object(
            routes.graphic_evaluation, "evaluate_image",
            return_value=Mock(model_dump=Mock(return_value=evaluation_result)),
        ) as evaluate:
            response = self.client.post("/content/evaluate/11", json={})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(
            [event["type"] for event in events],
            ["start", "progress", "result", "complete"],
        )
        self.assertEqual(events[2]["evaluation"]["image_analysis"]["classification"], "chart")
        evaluate.assert_called_once()
        self.sources.save_graphic_evaluation.assert_awaited_once_with(
            7, 11, 1, evaluation_result, expected_hash,
        )

    def test_graphic_evaluation_reuses_current_results_and_reevaluates_stale_results(self) -> None:
        evaluation_result = {
            "image_analysis": {"classification": "image", "math_expressions": []},
            "evaluations": {},
        }
        expected_hash = content.graphic_evaluation_hash(
            "fingerprint", "No Materials and Methods text was provided.",
            "No Results text was provided.", None,
        )
        self.sources.evaluation_sources.return_value = [
            content.GraphicEvaluationSource(
                1, "saved.png", "image/png", None, evaluation_result, None,
            ),
            content.GraphicEvaluationSource(
                2, "stale.png", "image/png", None, None, None,
            ),
        ]
        self.sources.source_text.return_value = content.SourceText(
            1, "Original research context", "fingerprint", 27,
        )
        self.posters.content.return_value = ([], [])
        self.sources.graphic_source.return_value = (b"image-bytes", "image/png")
        self.sources.save_graphic_evaluation.return_value = True
        with patch.object(routes.openai, "OpenAI"), patch.object(
            routes.graphic_evaluation, "evaluate_image",
            return_value=Mock(model_dump=Mock(return_value=evaluation_result)),
        ) as evaluate:
            response = self.client.post("/content/evaluate/11", json={})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([event["type"] for event in events], [
            "start", "result", "progress", "result", "complete",
        ])
        self.assertEqual(events[1]["source"]["sid"], 1)
        self.assertEqual(events[3]["source"]["sid"], 2)
        evaluate.assert_called_once()
        self.sources.graphic_source.assert_awaited_once_with(7, 11, 2)
        self.sources.save_graphic_evaluation.assert_awaited_once_with(
            7, 11, 2, evaluation_result, expected_hash,
        )

    def test_resumed_evaluation_finishes_only_graphics_not_saved_before_interruption(self) -> None:
        evaluation_result = {
            "image_analysis": {"classification": "image", "math_expressions": []},
            "evaluations": {},
        }
        expected_hash = content.graphic_evaluation_hash(
            "fingerprint", "No Materials and Methods text was provided.",
            "No Results text was provided.", None,
        )
        self.sources.evaluation_sources.return_value = [
            content.GraphicEvaluationSource(
                sid, f"image-{sid}.png", "image/png", None, None, None,
            )
            for sid in (1, 2)
        ]
        self.sources.source_text.return_value = content.SourceText(
            1, "Original research context", "fingerprint", 27,
        )
        self.posters.content.return_value = ([], [])
        self.sources.graphic_source.return_value = (b"image-bytes", "image/png")

        async def save_result(uid, pid, sid, result, fingerprint):
            self.sources.evaluation_sources.return_value[sid - 1] = content.GraphicEvaluationSource(
                sid, f"image-{sid}.png", "image/png", None, result, None,
            )
            return True

        self.sources.save_graphic_evaluation.side_effect = save_result
        parsed = Mock(model_dump=Mock(return_value=evaluation_result))
        with patch.object(routes.openai, "OpenAI"), patch.object(
            routes.graphic_evaluation, "evaluate_image",
            side_effect=[parsed, RuntimeError("Interrupted")],
        ), self.assertLogs("routers.content", "ERROR"):
            response = self.client.post("/content/evaluate/11", json={})
        self.assertEqual(json.loads(response.text.splitlines()[-1])["type"], "error")
        self.sources.save_graphic_evaluation.assert_awaited_once_with(
            7, 11, 1, evaluation_result, expected_hash,
        )

        self.posters.set_resumption.reset_mock()
        response = self.client.get("/content/evaluate?xyzzy=arpa")
        self.assertEqual(response.status_code, 200)
        self.posters.set_resumption.assert_awaited_once_with(
            7, 11, "http://testserver/content/evaluate?xyzzy=arpa",
        )
        self.sources.graphic_source.reset_mock()
        with patch.object(routes.openai, "OpenAI"), patch.object(
            routes.graphic_evaluation, "evaluate_image", return_value=parsed,
        ) as evaluate:
            response = self.client.post("/content/evaluate/11", json={})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual([event["type"] for event in events], [
            "start", "result", "progress", "result", "complete",
        ])
        self.sources.graphic_source.assert_awaited_once_with(7, 11, 2)
        evaluate.assert_called_once()

    def test_source_image_route_is_owner_checked_and_private(self) -> None:
        self.sources.graphic_source.return_value = (b"image-bytes", "image/png")
        response = self.client.get("/content/1/image")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"image-bytes")
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.sources.graphic_source.assert_awaited_once_with(7, 11, 1)
        self.sources.graphic_source.return_value = None
        self.assertEqual(self.client.get("/content/99/image").status_code, 404)


class EvaluationModelTests(unittest.TestCase):
    def test_math_classification_includes_latex_expressions(self) -> None:
        criteria = {
            key: "Assessment." for key in (
                "relevance", "technical_accuracy", "specificity", "completeness",
                "evidentiary_support", "potential_for_misinterpretation",
            )
        }
        section = {
            "rating": "significantly", "explanation": "Supports the methods.",
            "criteria": criteria, "supported_elements": [], "limitations": [],
            "suggested_improvements": [],
        }
        response = EvaluationResponse.model_validate({
            "image_analysis": {
                "classification": "math expression",
                "math_expressions": [r"E=mc^2", r"\frac{1}{2}"],
                "type": "Equation", "description": "Two equations.",
                "research_context": "Research.",
            },
            "evaluations": {
                "materials_and_methods": section,
                "results": section,
            },
        })
        self.assertEqual(
            response.image_analysis.math_expressions,
            [r"E=mc^2", r"\frac{1}{2}"],
        )


class SourceFileTests(unittest.TestCase):
    def test_hash_handles_text_and_bytes_consistently(self) -> None:
        self.assertEqual(content.source_hash("résumé"), content.source_hash("résumé".encode()))
        self.assertEqual(len(content.source_hash(b"research")), 64)

    def test_all_supported_formats_validate_and_reject_mismatches(self) -> None:
        headers = {
            ".pdf": b"%PDF-1.7", ".png": b"\x89PNG\r\n\x1a\n",
            ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff",
            ".tif": b"II*\0", ".tiff": b"MM\0*",
            ".md": b"# Research",
        }
        for suffix in content.FORMATS:
            if suffix in headers:
                data = headers[suffix]
            else:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    prefix = {".docx": "word/", ".pptx": "ppt/", ".xlsx": "xl/", ".xlsm": "xl/"}.get(suffix, "Index/")
                    archive.writestr(prefix + "document.xml", "document")
                data = buffer.getvalue()
            with self.subTest(suffix=suffix):
                self.assertEqual(content.validate_file("source" + suffix, data), content.FORMATS[suffix])
                with self.assertRaises(ValueError):
                    content.validate_file("source" + suffix, b"")
                if suffix != ".md":
                    with self.assertRaises(ValueError):
                        content.validate_file("source" + suffix, b"not this file format")
        with self.assertRaises(ValueError):
            content.validate_file("file.md", b"\xff")
        with self.assertRaises(ValueError):
            content.validate_file("file.md", b"binary\0text")

    def test_local_files_are_uuid_named_and_sharded_across_ten_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SOURCES_DIR": directory, "GCS_BUCKET": ""}):
            files = content.SourceFiles()
            files.prepare_directories()
            self.assertEqual(
                {path.name for path in Path(directory).iterdir()},
                {str(shard) for shard in range(10)},
            )
            for number in range(10):
                fileid = uuid.UUID(int=number)
                uri = files.put(fileid, b"source", "application/pdf")
                self.assertEqual(Path(uri).relative_to(Path(directory).resolve()).as_posix(), f"{number}/{fileid}")
                self.assertEqual(Path(uri).read_bytes(), b"source")
                files.delete(uri)
                self.assertFalse(Path(uri).exists())
                files.delete(uri)

    def test_local_delete_preserves_permission_errors(self) -> None:
        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                content.SourceFiles().delete("/sources/one")

    def test_gcs_uses_configured_bucket_and_uuid_key(self) -> None:
        client = Mock()
        with patch.dict(os.environ, {"GCS_BUCKET": "source-test"}), patch.object(content.storage, "Client", return_value=client):
            files = content.SourceFiles()
            fileid = uuid.UUID(int=17)
            uri = files.put(fileid, b"source", "application/pdf")
            self.assertEqual(uri, f"gs://source-test/7/{fileid}")
            client.bucket.assert_called_with("source-test")
            client.bucket().blob.assert_called_with(f"7/{fileid}")
            client.bucket().blob().upload_from_string.assert_called_once_with(
                b"source", content_type="application/pdf", if_generation_match=0, timeout=60,
            )
            files.delete(uri)
            client.bucket().blob().delete.assert_called_once_with(timeout=60)

    def test_gcs_delete_tolerates_only_missing_objects(self) -> None:
        client = Mock()
        with patch.object(content.storage, "Client", return_value=client):
            files = content.SourceFiles()
            client.bucket().blob().delete.side_effect = NotFound("missing")
            files.delete("gs://source-test/one")
            for error in (Forbidden("denied"), ServiceUnavailable("down")):
                with self.subTest(error=type(error).__name__):
                    client.bucket().blob().delete.side_effect = error
                    with self.assertRaises(type(error)):
                        files.delete("gs://source-test/one")


class SourceStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for name, value in (
            ("extract", Mock(return_value=content.processing.Processed(text="text"))),
            ("summarize", AsyncMock(return_value="Summary")),
        ):
            mocked = patch.object(content.processing, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    async def test_list_sources_scopes_owner_and_poster_and_orders_chronologically(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchall.return_value = [
            (1, "file.md", "text/markdown", 4, "now"),
        ]
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            items = await store.list_sources(7, 11)
        self.assertEqual(items, [content.Source(1, "file.md", "text/markdown", 4, "now")])
        statement, params = connection.execute.call_args.args
        self.assertIn("p.uid = %s AND p.pid = %s", statement)
        self.assertIn("ORDER BY s.added, s.sid", statement)
        self.assertEqual(params, (7, 11))

    async def test_graphic_previews_do_not_require_evaluation_columns(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchall.return_value = [
            (21, "chart.png"), (22, "paper figure.jpg"),
        ]
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            items = await store.graphic_sources(7, 11)
        self.assertEqual(items, [
            content.GraphicSource(21, "chart.png"), content.GraphicSource(22, "paper figure.jpg"),
        ])
        statement, params = connection.execute.await_args.args
        self.assertNotIn("evaluation", statement)
        self.assertNotIn("s.hash", statement)
        self.assertIn("p.uid = %s AND p.pid = %s", statement)
        self.assertIn("s.width IS NOT NULL", statement)
        self.assertIn("s.height IS NOT NULL", statement)
        self.assertIn("s.storage_uri IS NOT NULL", statement)
        self.assertIn("s.mimetype IN ('image/png', 'image/jpeg')", statement)
        self.assertIn("s.kind <> 'thumbnail'", statement)
        self.assertIn("coalesce(s.filename, parent.filename, 'Untitled source')", statement)
        self.assertIn("ORDER BY (s.parent IS NOT NULL), s.sid", statement)
        self.assertEqual(params, (7, 11))

    async def test_thumbnail_replacement_does_not_require_evaluation_columns(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        updated = datetime(2026, 11, 10, tzinfo=timezone.utc)
        owner, state, existing, saved = (AsyncMock() for _ in range(4))
        owner.fetchone.return_value = (11,)
        state.fetchone.return_value = (updated, None, None)
        existing.fetchone.return_value = (22, "/sources/old-thumbnail")
        connection.execute.side_effect = [owner, state, existing, saved]
        files = Mock()
        files.put.return_value = "/sources/new-thumbnail"
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                patch.object(content, "SourceFiles", return_value=files), \
                patch.object(content.processing, "image_dimensions", return_value=(640, 480)):
            self.assertTrue(await store.save_thumbnail(
                7, 11, b"\x89PNG\r\n\x1a\nimage", (updated, None),
            ))
        statement, params = connection.execute.await_args_list[-1].args
        self.assertNotIn("evaluation", statement)
        self.assertEqual(params[-1], 22)
        files.delete.assert_called_once_with("/sources/old-thumbnail")

    async def test_delete_removes_source_files_before_deleting_poster_record(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        owner, poster, sources, memory, deleted = (
            AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(),
        )
        owner.fetchone.return_value = (7,)
        poster.fetchone.return_value = (11,)
        memory.fetchone.return_value = (None,)
        deleted.fetchone.return_value = (11,)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / "one", Path(directory) / "two"]
            for path in paths:
                path.write_bytes(b"source")
            sources.fetchall.return_value = [(str(path),) for path in paths]
            poster_delete_saw_missing_files = []
            responses = iter((owner, poster, sources, memory, deleted))

            async def execute(statement: str, params: tuple = ()) -> AsyncMock:
                if isinstance(statement, str) and statement.startswith("DELETE FROM poster WHERE"):
                    poster_delete_saw_missing_files.append(
                        all(not path.exists() for path in paths)
                    )
                return next(responses)

            connection.execute.side_effect = execute
            with patch.object(store, "_connect", AsyncMock(return_value=connection)):
                self.assertTrue(await store.delete(7, 11))

            self.assertEqual(poster_delete_saw_missing_files, [True])
            self.assertIn(
                "SELECT DISTINCT storage_uri FROM source",
                connection.execute.await_args_list[2].args[0],
            )

    async def test_delete_keeps_poster_record_when_source_file_cannot_be_removed(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        owner, poster, sources = AsyncMock(), AsyncMock(), AsyncMock()
        owner.fetchone.return_value = (7,)
        poster.fetchone.return_value = (11,)
        sources.fetchall.return_value = [("/sources/one",)]
        connection.execute.side_effect = [owner, poster, sources]
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source"
            source_path.write_bytes(b"source")
            sources.fetchall.return_value = [(str(source_path),)]
            with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                    patch.object(content, "run_in_threadpool", AsyncMock(side_effect=OSError("permission denied"))):
                with self.assertRaises(content.SourceDeletionError):
                    await store.delete(7, 11)
        self.assertEqual(connection.execute.await_count, 3)

    async def test_delete_missing_source_still_removes_memory_and_poster(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        owner, poster, sources, memory, cleared, deleted = (
            AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(),
        )
        poster.fetchone.return_value = (11,)
        memory.fetchone.return_value = (f"{store.schema}.poster_memory",)
        deleted.fetchone.return_value = (11,)
        connection.execute.side_effect = [owner, poster, sources, memory, cleared, deleted]
        with tempfile.TemporaryDirectory() as directory:
            sources.fetchall.return_value = [(str(Path(directory) / "missing"),)]
            with patch.object(store, "_connect", AsyncMock(return_value=connection)):
                self.assertTrue(await store.delete(7, 11))
        memory_statement, memory_params = connection.execute.await_args_list[4].args
        self.assertIn("poster_memory", memory_statement.as_string())
        self.assertEqual(memory_params, (11,))
        self.assertEqual(connection.execute.await_args_list[5].args, (
            "DELETE FROM poster WHERE uid = %s AND pid = %s RETURNING pid", (7, 11),
        ))

    async def test_failed_database_insert_removes_saved_file(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        owner, poster = AsyncMock(), AsyncMock()
        owner.fetchone.return_value = (7,)
        poster.fetchone.return_value = (11,)
        connection.execute.side_effect = [owner, poster, psycopg.OperationalError("down")]
        files = Mock()
        files.put.return_value = "/sources/1/test"
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), patch.object(content, "SourceFiles", return_value=files):
            with self.assertRaises(psycopg.OperationalError):
                await store.add(7, 11, "file.md", b"text")
        files.delete.assert_called_once_with("/sources/1/test")

    async def test_successful_insert_preserves_file_and_records_source_metadata(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        owner, poster, inserted = AsyncMock(), AsyncMock(), AsyncMock()
        owner.fetchone.return_value = (7,)
        poster.fetchone.return_value = (11,)
        inserted.fetchone.return_value = (1, "file.md", "text/markdown", 4, "now")
        connection.execute.side_effect = [owner, poster, inserted]
        files = Mock()
        files.put.return_value = "/sources/1/test"
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), patch.object(content, "SourceFiles", return_value=files):
            result = await store.add(7, 11, "file.md", b"text")
        self.assertEqual(result.filename, "file.md")
        files.delete.assert_not_called()
        statement, params = connection.execute.await_args_list[2].args
        self.assertEqual(params[:6], (11, "text", None, "file.md", "text/markdown", 4))
        self.assertIsInstance(params[6], uuid.UUID)
        self.assertEqual(params[7:11], (content.source_hash(b"text"), "/sources/1/test", None, None))
        self.assertIs(params[-1], False)
        content.processing.extract.assert_not_called()
        content.processing.summarize.assert_not_awaited()

    async def test_foreign_or_purchased_poster_never_writes_a_file(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        owner, poster = AsyncMock(), AsyncMock()
        owner.fetchone.return_value = (7,)
        poster.fetchone.return_value = None
        connection.execute.side_effect = [owner, poster]
        files = Mock()
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), patch.object(content, "SourceFiles", return_value=files):
            with self.assertRaises(ValueError):
                await store.add(7, 99, "file.md", b"text")
        files.put.assert_not_called()

    async def test_identical_graphics_reuse_existing_poster_scoped_record(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchone.return_value = (
            1, "figure.png", "image/png", 10, "now", None, "figure", 300, 400, "image",
        )
        files = Mock()
        asset = source_processing.Asset("figure.png", b"image", "image/png", "image", width=300, height=400)
        result = await store._insert_source(connection, files, [], 11, asset, 7)
        self.assertEqual(result.sid, 1)
        statement, params = connection.execute.call_args.args
        self.assertIn("s.pid = %s AND s.hash = %s", statement)
        self.assertEqual(params, (11, content.source_hash(b"image")))
        files.put.assert_not_called()

    async def test_math_expressions_are_saved_as_distinct_latex_child_sources(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        digest = content.source_hash(b"source image")
        locked, sections, prior, deleted = (
            AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(),
        )
        locked.fetchone.return_value = (
            1, digest, None, "equation.png", "image/png", "/sources/source", None,
        )
        sections.fetchall.return_value = []
        prior.fetchall.return_value = []
        inserted = []
        for index, expression in enumerate((r"E=mc^2", r"\frac{1}{2}"), start=1):
            cursor = AsyncMock()
            cursor.fetchone.return_value = (
                index + 10, f"evaluation-math-expression-{index}.tex", "text/plain",
                len(expression), "now", 1, "LaTeX expression", None, None,
                "math expression", True,
            )
            inserted.append(cursor)
        connection.execute.side_effect = [
            locked, sections, prior, deleted, *inserted,
            AsyncMock(), AsyncMock(), AsyncMock(),
        ]
        files = Mock()
        files.put.side_effect = ["/sources/equation-1", "/sources/equation-2"]
        snapshot = content.SourceText(1, "", "fingerprint", 0)
        expected_hash = content.graphic_evaluation_hash(
            snapshot.fingerprint, "No Materials and Methods text was provided.",
            "No Results text was provided.", digest,
        )
        criteria = {
            key: "Assessment." for key in (
                "relevance", "technical_accuracy", "specificity", "completeness",
                "evidentiary_support", "potential_for_misinterpretation",
            )
        }
        section = {
            "rating": "significantly", "explanation": "Supports the methods.",
            "criteria": criteria, "supported_elements": [], "limitations": [],
            "suggested_improvements": [],
        }
        result = {
            "image_analysis": {
                "classification": "math expression",
                "math_expressions": [r"E=mc^2", r"\frac{1}{2}"],
            },
            "evaluations": {
                "materials_and_methods": section, "results": section,
            },
        }
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                patch.object(store, "source_text", AsyncMock(return_value=snapshot)), \
                patch.object(content, "SourceFiles", return_value=files):
            self.assertTrue(await store.save_graphic_evaluation(
                7, 11, 1, result, expected_hash,
            ))
        self.assertEqual(
            [call.args[1] for call in files.put.call_args_list],
            [b"E=mc^2", b"\\frac{1}{2}"],
        )
        source_inserts = [
            call.args[1] for call in connection.execute.await_args_list
            if "INSERT INTO source" in call.args[0]
        ]
        self.assertEqual([params[2] for params in source_inserts], [1, 1])
        self.assertEqual([params[9] for params in source_inserts], [r"E=mc^2", r"\frac{1}{2}"])

    async def test_logo_replacement_uses_pixel_area_and_keeps_smaller_as_image(self) -> None:
        for old_area, expected_kind in ((100, "logo"), (1000000, "image"), (90000, "image")):
            with self.subTest(old_area=old_area):
                store = content.SourceStore()
                connection = AsyncMock()
                missing, old, inserted, updated = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
                missing.fetchone.return_value = None
                old.fetchone.return_value = (9, old_area)
                inserted.fetchone.return_value = (
                    10, "logo.png", "image/png", 5, "now", None, "A logo", 300, 300, expected_kind,
                )
                connection.execute.side_effect = (
                    [missing, old, updated, inserted] if expected_kind == "logo"
                    else [missing, old, inserted]
                )
                files = Mock()
                files.put.return_value = "/sources/logo"
                asset = source_processing.Asset("logo.png", b"image", "image/png", "image",
                                                description="A logo", width=300, height=300)
                result = await store._insert_source(connection, files, [], 11, asset, None)
                self.assertEqual(result.kind, expected_kind)
                self.assertEqual(connection.execute.await_args_list[-1].args[1][1], expected_kind)
                if expected_kind == "logo":
                    self.assertIn("UPDATE source SET kind = 'image'", connection.execute.await_args_list[2].args[0])

    async def test_children_record_document_parent_and_all_files_rollback_on_failure(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        poster, original, updated, missing = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        poster.fetchone.return_value = (11,)
        original.fetchone.return_value = ("file.md", "/sources/document", "text", False)
        missing.fetchone.return_value = None
        connection.execute.side_effect = [poster, original, updated, missing, psycopg.OperationalError("down")]
        files = Mock()
        files.read.return_value = b"text"
        files.put.return_value = "/sources/child"
        processed = source_processing.Processed(text="text", assets=[
            source_processing.Asset("formula.txt", b"e=mc^2", "text/plain", "image", text="e=mc^2"),
        ])
        with patch.object(content.processing, "extract", return_value=processed), \
                patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                patch.object(content, "SourceFiles", return_value=files):
            with self.assertRaises(psycopg.OperationalError):
                await store.extract(7, 11, 1)
        child_params = connection.execute.await_args_list[-1].args[1]
        self.assertEqual(child_params[2], 1)
        self.assertEqual([call.args[0] for call in files.delete.call_args_list], ["/sources/child"])

    async def test_extraction_updates_saved_original_and_can_be_retried_without_reprocessing(self) -> None:
        for already_extracted in (False, True):
            with self.subTest(already_extracted=already_extracted):
                store = content.SourceStore()
                connection = AsyncMock()
                poster, original, updated = AsyncMock(), AsyncMock(), AsyncMock()
                poster.fetchone.return_value = (11,)
                original.fetchone.return_value = ("file.md", "/sources/document", "text", already_extracted)
                connection.execute.side_effect = [poster, original, updated]
                files = Mock()
                files.read.return_value = b"text"
                content.processing.extract.reset_mock()
                content.processing.summarize.reset_mock()
                with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                        patch.object(content, "SourceFiles", return_value=files):
                    await store.extract(7, 11, 1)
                files.put.assert_not_called()
                if already_extracted:
                    files.read.assert_not_called()
                    content.processing.extract.assert_not_called()
                else:
                    files.read.assert_called_once_with("/sources/document")
                    content.processing.extract.assert_called_once_with("file.md", b"text")
                    content.processing.summarize.assert_not_awaited()
                    statement, params = connection.execute.await_args_list[-1].args
                    self.assertIn("extracted = TRUE", statement)
                    self.assertEqual(params, ("text", None, None, None, "text", 1))

    async def test_failed_conversion_keeps_original_for_retry(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        poster, original = AsyncMock(), AsyncMock()
        poster.fetchone.return_value = (11,)
        original.fetchone.return_value = ("file.md", "/sources/document", "text", False)
        connection.execute.side_effect = [poster, original]
        files = Mock()
        files.read.return_value = b"text"
        processing_error = source_processing.ProcessingError("down")
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                patch.object(content, "SourceFiles", return_value=files), \
                patch.object(content.processing, "extract", side_effect=processing_error):
            with self.assertRaises(source_processing.ProcessingError) as raised:
                await store.extract(7, 11, 1)
        self.assertIs(raised.exception, processing_error)
        self.assertEqual(connection.execute.await_count, 2)
        files.delete.assert_not_called()

    async def test_missing_or_uneditable_source_never_reads_storage(self) -> None:
        for poster_row, source_row in ((None, None), ((11,), None)):
            store = content.SourceStore()
            connection = AsyncMock()
            poster, original = AsyncMock(), AsyncMock()
            poster.fetchone.return_value = poster_row
            original.fetchone.return_value = source_row
            connection.execute.side_effect = [poster, original]
            files = Mock()
            with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                    patch.object(content, "SourceFiles", return_value=files):
                with self.assertRaises(content.SourceConflictError):
                    await store.extract(7, 11, 1)
            files.read.assert_not_called()

    async def test_spreadsheet_markup_does_not_count_toward_text_threshold(self) -> None:
        connection = AsyncMock()
        connection.execute.return_value.fetchall.return_value = [
            ("<table><tr><td>A &amp; B</td></tr></table>", content.FORMATS[".xlsx"][0]),
            ("research", "text/markdown"), (None, "image/png"),
        ]
        store = content.SourceStore()
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            self.assertEqual(await store.completion(7, 11), (3, 13))

    async def test_hierarchy_places_children_under_parents_in_chronological_order(self) -> None:
        connection = AsyncMock()
        connection.execute.return_value.fetchall.return_value = [
            (1, "first.pdf", "application/pdf", 1, "1", None, None, None, None, "mixed"),
            (2, "second.pdf", "application/pdf", 1, "2", None, None, None, None, "mixed"),
            (3, "child.png", "image/png", 1, "3", 1, None, 300, 300, "image"),
        ]
        store = content.SourceStore()
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            items = await store.list_sources(7, 11)
        self.assertEqual([(item.sid, item.depth) for item in items], [(1, 0), (3, 1), (2, 0)])


class ProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_chart_images_can_be_normalized_to_png(self) -> None:
        from PIL import Image

        source = io.BytesIO()
        Image.new("RGB", (37, 23), "blue").save(source, format="JPEG")
        png = source_processing.image_as_png(source.getvalue())
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(source_processing.image_dimensions(png), (37, 23))

    async def test_spreadsheet_cells_and_sheet_names_are_html_escaped(self) -> None:
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.title = "<Research>"
        workbook.active.append(["<script>bad</script>", "Data & results", 42])
        buffer = io.BytesIO()
        workbook.save(buffer)
        for extension in (".xlsx", ".xlsm"):
            result = source_processing.extract("sheet" + extension, buffer.getvalue())
            self.assertIn("&lt;Research&gt;", result.text)
            self.assertIn("&lt;script&gt;bad&lt;/script&gt;", result.text)
            self.assertIn("Data &amp; results", result.text)
            self.assertNotIn("<script>", result.text)

    async def test_docling_extracts_text_assets_and_enables_descriptions_and_math(self) -> None:
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "figure.png"
            Image.new("RGB", (299, 600)).save(path)
            manifest = {
                "output_directory": directory, "warnings": [],
                "source_texts": [{"id": "caption", "text": "Figure 1"}],
                "elements": [{
                    "kind": "picture", "caption_refs": ["caption"], "annotations": [{"text": "A graph"}],
                    "text": None, "assets": ["figure.png"],
                }, {
                    "kind": "formula", "caption_refs": [], "annotations": [],
                    "text": r"E=mc^2", "assets": [],
                }, {
                    "kind": "formula", "caption_refs": [], "annotations": [],
                    "text": r"x=\frac{1}{2}", "assets": [],
                }],
            }
            with patch.dict(os.environ, {"DOCLING_SERVICE_URL": "https://converter.invalid"}), \
                    patch.object(source_processing.docxtractor, "extract_document", return_value=manifest) as convert:
                result = source_processing.extract("research.pdf", b"%PDF-1.7")
            self.assertEqual(result.text, "Figure 1\n\n\\[E=mc^2\\]\n\n\\[x=\\frac{1}{2}\\]")
            self.assertEqual(result.assets[0].width, 299)
            self.assertEqual(result.assets[0].description, "Figure 1\nA graph")
            self.assertEqual(result.assets[1].text, "E=mc^2")
            self.assertEqual(result.assets[1].kind, "math expression")
            self.assertTrue(result.assets[1].filename.endswith(".tex"))
            self.assertEqual(result.assets[2].text, r"x=\frac{1}{2}")
            self.assertEqual(result.assets[2].kind, "math expression")
            self.assertTrue(convert.call_args.kwargs["describe_pictures"])
            self.assertTrue(convert.call_args.kwargs["extract_chart_data"])
            self.assertTrue(convert.call_args.kwargs["enrich_formulas"])

    async def test_apple_documents_always_use_docling_native_conversion(self) -> None:
        manifest = {"output_directory": "/tmp", "warnings": [], "source_texts": [], "elements": []}
        with patch.dict(os.environ, {"DOCLING_SERVICE_URL": "https://converter.invalid"}), \
                patch.object(source_processing.docxtractor, "extract_document", return_value=manifest) as convert:
            for suffix in (".pages", ".key"):
                source_processing.extract("research" + suffix, b"archive")
                self.assertTrue(convert.call_args.kwargs["native_iwork"])
                self.assertEqual(convert.call_args.kwargs["filename"], "research" + suffix)

    def test_reduced_conversion_is_explicit_and_retains_source_text(self) -> None:
        manifest = {
            "output_directory": "/tmp", "warnings": [],
            "source_texts": [{"id": "paragraph", "text": "Research evidence"}], "elements": [],
        }
        with patch.dict(os.environ, {
            "DOCLING_SERVICE_URL": "https://converter.invalid", "DOCLING_ENABLE_ENRICHMENT": "false",
        }), patch.object(source_processing.docxtractor, "extract_document", return_value=manifest) as convert:
            result = source_processing.extract("research.pdf", b"%PDF-1.7")
        for option in ("describe_pictures", "extract_chart_data", "enrich_formulas"):
            self.assertIs(convert.call_args.kwargs[option], False)
        self.assertEqual(result.text, "Research evidence")
        self.assertIn(source_processing.REDUCED_FEATURE_NOTICE, result.warnings)

    def test_enrichment_defaults_to_enabled_and_rejects_invalid_settings(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(source_processing.enrichment_enabled())
        with patch.dict(os.environ, {"DOCLING_ENABLE_ENRICHMENT": "off"}):
            with self.assertRaisesRegex(source_processing.ProcessingError, "must be true or false"):
                source_processing.enrichment_enabled()

    async def test_rtf_is_rejected_by_validation_and_processing(self) -> None:
        self.assertNotIn(".rtf", content.FORMATS)
        with self.assertRaises(ValueError):
            content.validate_file("file.rtf", b"{\\rtf1 text}")
        with self.assertRaises(ValueError):
            source_processing.extract("file.rtf", b"{\\rtf1 text}")

    async def test_numbers_requires_actual_table_data_and_produces_html(self) -> None:
        manifest = {"output_directory": "/tmp", "warnings": [], "source_texts": [], "elements": []}
        with patch.dict(os.environ, {"DOCLING_SERVICE_URL": "https://converter.invalid"}), \
                patch.object(source_processing.docxtractor, "extract_document", return_value=manifest):
            with self.assertRaises(source_processing.ProcessingError):
                source_processing.extract("sheet.numbers", b"archive")
            manifest["elements"] = [{
                "kind": "table", "caption_refs": [], "annotations": [], "assets": [], "text": "",
                "data": {"table_cells": [
                    {"start_row_offset_idx": 0, "start_col_offset_idx": 0, "text": "<Research>"},
                ]},
            }]
            result = source_processing.extract("sheet.numbers", b"archive")
            self.assertEqual(result.text, "<table><tr><td>&lt;Research&gt;</td></tr></table>")

    def test_refused_docling_requests_explain_instance_and_key_checks(self) -> None:
        from docling.service_client.exceptions import ServiceError
        refused = ServiceError("Task submission failed.", status_code=403)
        with patch.dict(os.environ, {"DOCLING_SERVICE_URL": "https://docling.example"}), \
                patch.object(source_processing.docxtractor, "extract_document", side_effect=refused):
            with self.assertRaises(source_processing.ProcessingError) as raised:
                source_processing.extract("file.md", b"# Research")
        self.assertIn("refused the request (HTTP 403)", str(raised.exception))
        self.assertIn("instance is enabled", str(raised.exception))
        self.assertIs(raised.exception.__cause__, refused)

    async def test_missing_services_fail_explicitly(self) -> None:
        with patch.dict(os.environ, {"DOCLING_SERVICE_URL": "", "OPENAI_API_KEY": ""}):
            with self.assertRaises(source_processing.ProcessingError):
                source_processing.extract("file.md", b"# Research")
            with self.assertRaises(source_processing.ProcessingError):
                connection = AsyncMock()
                connection.execute.return_value.fetchone.side_effect = [("research",), None]
                with patch.object(posters.store(), "_connect", AsyncMock(return_value=connection)):
                    await source_processing.summarize(11, "results", 1)

    async def test_upload_chat_finished_replies_invoke_the_same_validated_finish_command(self) -> None:
        from routers.wikkit import finished_uploading, upload_workflow
        for reply in ("Finished", "I'm finished", "I’m finished", "Done", "I'm done", "All done!"):
            with self.subTest(reply=reply):
                self.assertTrue(finished_uploading(reply))
        for reply in ("No", "yes", "What formats can you read?"):
            with self.subTest(reply=reply):
                self.assertFalse(finished_uploading(reply))
        replies = asyncio.Queue()
        for reply in ("I'm all done", "What formats can you read?"):
            replies.put_nowait(reply)
        async def next_reply(**_: object) -> str:
            return await replies.get()

        bot = AsyncMock()
        bot.request_response.side_effect = next_reply
        task = asyncio.create_task(upload_workflow(bot))
        try:
            for _ in range(20):
                if bot.handle_message.await_count:
                    break
                await asyncio.sleep(0)
            bot.request_response.assert_awaited_with(remind=False)
            bot.send.assert_awaited_once_with({"command": {"does": "finished-uploading"}})
            bot.handle_message.assert_awaited_once_with("What formats can you read?")
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_summary_uses_model_without_browsing_tools(self) -> None:
        client = AsyncMock()
        client.responses.create.return_value.output_text = "Summary of research"
        context = AsyncMock()
        context.__aenter__.return_value = client
        connection = AsyncMock()
        connection.execute.return_value.fetchone.side_effect = [("Research facts",), None, (1,)]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), \
                patch.object(source_processing.openai, "AsyncOpenAI", return_value=context), \
                patch.object(posters.store(), "_connect", AsyncMock(return_value=connection)):
            self.assertEqual(await source_processing.summarize(11, "results", 3), "Summary of research")
        self.assertNotIn("tools", client.responses.create.call_args.kwargs)

    async def test_real_docling_manifest_with_injected_client_never_calls_network(self) -> None:
        from types import SimpleNamespace
        from docling_core.types.doc import DoclingDocument, DocItemLabel
        import docxtractor
        document = DoclingDocument(name="research")
        document.add_text(label=DocItemLabel.PARAGRAPH, text="Research results")
        client = Mock()
        client.convert.return_value = SimpleNamespace(status="success", document=document, errors=[])
        with tempfile.TemporaryDirectory() as directory:
            result = docxtractor.extract_document(
                b"# Research results", filename="paper.md", output_dir=directory,
                client=client,
            )
        self.assertEqual(result["source_texts"][0]["text"], "Research results")
        options = client.convert.call_args.kwargs["options"]
        self.assertTrue(options.do_picture_description)
        self.assertTrue(options.do_formula_enrichment)
        self.assertTrue(options.do_chart_extraction)
        self.assertEqual(options.image_export_mode, "referenced")
        self.assertTrue(options.include_images)
        self.assertTrue(options.include_page_images)

    def test_referenced_docling_bundle_preserves_extracted_pngs(self) -> None:
        from types import SimpleNamespace
        from PIL import Image
        from docling.service_client import DoclingServiceClient
        from docling_core.types.doc import DoclingDocument, ImageRef
        import docxtractor

        document = DoclingDocument(name="research")
        picture = document.add_picture(
            image=ImageRef.from_pil(Image.new("RGB", (320, 400), "red"), dpi=150),
        )
        picture.image.uri = Path("artifacts/picture.png")
        png = io.BytesIO()
        Image.new("RGB", (320, 400), "red").save(png, format="PNG")
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("document.json", document.model_dump_json())
            archive.writestr("artifacts/picture.png", png.getvalue())

        with DoclingServiceClient(url="https://docling.example") as sdk:
            restored = sdk._document_from_artifact("resource_bundle", bundle.getvalue())
        client = Mock()
        client.convert.return_value = SimpleNamespace(status="success", document=restored, errors=[])
        with tempfile.TemporaryDirectory() as directory:
            result = docxtractor.extract_document(
                b"# Research", filename="paper.md", output_dir=directory, client=client,
            )
            asset = Path(result["output_directory"]) / result["elements"][0]["asset"]
            with Image.open(asset) as image:
                self.assertEqual(image.size, (320, 400))
                self.assertEqual(image.format, "PNG")
        self.assertEqual(result["warnings"], [])


@unittest.skipUnless(os.environ.get("POSTERS_TEST_DATABASE_URL"), "Set POSTERS_TEST_DATABASE_URL for PostgreSQL tests")
class ContentDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        test_posters.PosterDatabaseTests.setUpClass.__func__(cls)
        cls.store = content.SourceStore("POSTERS_TEST_DATABASE_URL", cls.schema)

    @classmethod
    def cleanup_schema(cls) -> None:
        test_posters.PosterDatabaseTests.cleanup_schema.__func__(cls)

    def setUp(self) -> None:
        test_posters.PosterDatabaseTests.setUp(self)

    def test_source_analysis_cleanup_migration_is_repeatable(self) -> None:
        self.connection.execute(
            "ALTER TABLE source ADD COLUMN summary TEXT, ADD COLUMN evaluation JSONB, "
            "ADD COLUMN evaluation_hash TEXT"
        )
        migration = (ROOT / "schema/source-analysis-cleanup.sql").read_text()
        self.connection.execute(migration)
        self.connection.execute(migration)
        columns = {
            row[0] for row in self.connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'source'",
            ).fetchall()
        }
        self.assertNotIn("summary", columns)
        self.assertNotIn("evaluation", columns)
        self.assertNotIn("evaluation_hash", columns)

    def test_image_assessment_migration_is_repeatable(self) -> None:
        self.connection.execute("ALTER TABLE assessment ALTER COLUMN cid SET NOT NULL")
        self.connection.execute(
            "ALTER TABLE assessment DROP COLUMN criteria, DROP COLUMN supported_elements, "
            "DROP COLUMN limitations, DROP COLUMN suggested_improvements"
        )
        migration = (ROOT / "schema/image-assessment.sql").read_text()
        self.connection.execute(migration)
        self.connection.execute(migration)
        columns = dict(self.connection.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'assessment' "
        ).fetchall())
        self.assertEqual(columns["cid"], "YES")
        self.assertEqual(columns["sid"], "YES")
        for column in (
            "criteria", "supported_elements", "limitations", "suggested_improvements",
        ):
            self.assertIn(column, columns)
        self.assertIsNotNone(self.connection.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
            "AND indexname = 'idx_assessment_sid_section'",
        ).fetchone())

    def test_source_thumbnail_migration_is_repeatable(self) -> None:
        self.connection.execute("ALTER TABLE source DROP COLUMN updated")
        self.connection.execute("ALTER TABLE source DROP CONSTRAINT source_kind_check")
        self.connection.execute(
            "ALTER TABLE source ADD CONSTRAINT source_kind_check CHECK "
            "(kind IN ('text', 'svg', 'mixed', 'image', 'math', 'chart', 'table', "
            "'logo', 'moreinfo', 'qrcode'))"
        )
        self.connection.execute("DROP INDEX idx_source_pid_kind_unique")
        self.connection.execute(
            "CREATE UNIQUE INDEX idx_source_pid_kind_unique ON source (pid, kind) "
            "WHERE kind IN ('logo', 'moreinfo', 'qrcode')"
        )
        migration = (ROOT / "schema/source-thumbnail.sql").read_text()
        self.connection.execute(migration)
        self.connection.execute(migration)
        columns = {
            row[0] for row in self.connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'source'",
            ).fetchall()
        }
        self.assertIn("updated", columns)
        pid, _ = asyncio.run(self.store.new_or_existing(7))
        self.connection.execute(
            "INSERT INTO source (pid, kind, size, mimetype) "
            "VALUES (%s, 'thumbnail', 0, 'image/png')", (pid,),
        )

    def test_source_image_classification_migration_is_repeatable(self) -> None:
        self.connection.execute("ALTER TABLE source DROP CONSTRAINT source_kind_check")
        self.connection.execute(
            "ALTER TABLE source ADD CONSTRAINT source_kind_check CHECK "
            "(kind IN ('text', 'svg', 'mixed', 'image', 'math', 'chart', 'table', "
            "'logo', 'moreinfo', 'qrcode', 'thumbnail'))"
        )
        migration = (ROOT / "schema/source-image-classification.sql").read_text()
        self.connection.execute(migration)
        self.connection.execute(migration)
        pid, _ = asyncio.run(self.store.new_or_existing(7))
        for kind in ("diagram", "math expression"):
            self.connection.execute(
                "INSERT INTO source (pid, kind, size, mimetype) "
                "VALUES (%s, %s, 1, 'image/png')", (pid, kind),
            )

    def test_poster_thumbnail_refreshes_for_poster_and_source_updates(self) -> None:
        from PIL import Image

        pid, _ = asyncio.run(self.store.new_or_existing(7))
        first = io.BytesIO()
        Image.new("RGB", (32, 24), "red").save(first, format="PNG")
        second = io.BytesIO()
        Image.new("RGB", (32, 24), "blue").save(second, format="PNG")

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"SOURCES_DIR": directory, "GCS_BUCKET": ""},
        ):
            state = asyncio.run(self.store.thumbnail_state(7, pid))
            self.assertIsNotNone(state)
            self.assertTrue(state.stale)
            self.assertTrue(asyncio.run(self.store.save_thumbnail(
                7, pid, first.getvalue(), state.revision,
            )))
            fresh = asyncio.run(self.store.thumbnail_state(7, pid))
            self.assertFalse(fresh.stale)
            self.assertEqual(asyncio.run(self.store.list_sources(7, pid)), [])
            self.assertEqual(asyncio.run(self.store.completion(7, pid)), (0, 0))
            self.assertEqual(asyncio.run(self.store.source_text(7, pid)).count, 0)
            self.assertEqual(asyncio.run(self.store.evaluation_sources(7, pid)), [])
            sid = self.connection.execute(
                "SELECT sid FROM source WHERE pid = %s AND kind = 'thumbnail'", (pid,),
            ).fetchone()[0]
            self.assertEqual(asyncio.run(self.store.poster_thumbnail(7, pid)), first.getvalue())

            self.connection.execute(
                "INSERT INTO source (pid, kind, size, mimetype, updated) "
                "VALUES (%s, 'text', 1, 'text/plain', now() + interval '1 second')", (pid,),
            )
            stale = asyncio.run(self.store.thumbnail_state(7, pid))
            self.assertTrue(stale.stale)
            self.assertTrue(asyncio.run(self.store.save_thumbnail(
                7, pid, second.getvalue(), stale.revision,
            )))
            self.assertEqual(
                self.connection.execute(
                    "SELECT sid FROM source WHERE pid = %s AND kind = 'thumbnail'", (pid,),
                ).fetchone()[0],
                sid,
            )
            self.assertEqual(asyncio.run(self.store.poster_thumbnail(7, pid)), second.getvalue())
            self.assertIsNone(asyncio.run(self.store.thumbnail_state(8, pid)))

        self.purchase(pid)
        self.assertIsNone(asyncio.run(self.store.thumbnail_state(7, pid)))

    def test_chart_evaluation_normalizes_jpeg_to_png(self) -> None:
        from PIL import Image

        pid, _ = asyncio.run(self.store.new_or_existing(7))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chart.jpg"
            image = io.BytesIO()
            Image.new("RGB", (37, 23), "blue").save(image, format="JPEG")
            data = image.getvalue()
            path.write_bytes(data)
            sid = self.connection.execute(
                "INSERT INTO source (pid, kind, filename, size, mimetype, width, height, "
                "storage_uri, hash) VALUES (%s, 'image', 'chart.jpg', %s, 'image/jpeg', "
                "37, 23, %s, %s) RETURNING sid",
                (pid, len(data), str(path), content.source_hash(data)),
            ).fetchone()[0]
            snapshot = asyncio.run(self.store.source_text(7, pid))
            expected_hash = content.graphic_evaluation_hash(
                snapshot.fingerprint, "No Materials and Methods text was provided.",
                "No Results text was provided.", content.source_hash(data),
            )
            criteria = {
                key: "Assessment." for key in (
                    "relevance", "technical_accuracy", "specificity", "completeness",
                    "evidentiary_support", "potential_for_misinterpretation",
                )
            }
            section = {
                "rating": "significantly", "explanation": "Chart support.",
                "criteria": criteria, "supported_elements": [], "limitations": [],
                "suggested_improvements": [],
            }
            result = {
                "image_analysis": {
                    "classification": "chart", "math_expressions": [],
                },
                "evaluations": {
                    "materials_and_methods": section, "results": section,
                },
            }
            with patch.dict(os.environ, {
                "SOURCES_DIR": str(Path(directory) / "stored"), "GCS_BUCKET": "",
            }):
                self.assertTrue(asyncio.run(self.store.save_graphic_evaluation(
                    7, pid, sid, result, expected_hash,
                )))
                row = self.connection.execute(
                    "SELECT kind, filename, mimetype, storage_uri, hash "
                    "FROM source WHERE sid = %s", (sid,),
                ).fetchone()
                png = Path(row[3]).read_bytes()
            self.assertEqual(row[:3], ("chart", "chart.png", "image/png"))
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertFalse(path.exists())
            self.assertEqual(row[4], content.source_hash(png))
            cached = asyncio.run(self.store.evaluation_sources(7, pid))[0].evaluation
            self.assertEqual(cached["image_analysis"]["classification"], "chart")

    def test_graphic_evaluations_persist_and_are_owner_scoped(self) -> None:
        pid, _ = asyncio.run(self.store.new_or_existing(7))
        criteria = {
            "relevance": "Relevant.",
            "technical_accuracy": "Accurate.",
            "specificity": "Specific.",
            "completeness": "Complete.",
            "evidentiary_support": "Supported.",
            "potential_for_misinterpretation": "Clear.",
        }

        def section_evaluation(rating: str, explanation: str) -> dict[str, object]:
            return {
                "rating": rating,
                "explanation": explanation,
                "criteria": criteria.copy(),
                "supported_elements": ["A labeled element."],
                "limitations": ["One limitation."],
                "suggested_improvements": ["Add a legend."],
            }

        evaluation_result = {
            "image_analysis": {"classification": "image", "math_expressions": []},
            "evaluations": {
                "materials_and_methods": section_evaluation(
                    "significantly", "The image shows the procedure.",
                ),
                "results": section_evaluation("highly", "The image shows the findings."),
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graphic.png"
            path.write_bytes(b"image")
            sid = self.connection.execute(
                "INSERT INTO source (pid, kind, filename, size, mimetype, width, height, storage_uri) "
                "VALUES (%s, 'image', 'graphic.png', 5, 'image/png', 400, 300, %s) RETURNING sid",
                (pid, str(path)),
            ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO section (pid, which, content) VALUES "
                "(%s, 'materials and methods', 'Methods'), (%s, 'results', 'Results')",
                (pid, pid),
            )
            graphics = asyncio.run(self.store.evaluation_sources(7, pid))
            self.assertEqual(graphics, [
                content.GraphicEvaluationSource(sid, "graphic.png", "image/png", None, None, None),
            ])
            self.assertEqual(
                asyncio.run(self.store.graphic_source(7, pid, sid)),
                (b"image", "image/png"),
            )
            snapshot = asyncio.run(self.store.source_text(7, pid))
            expected_hash = content.graphic_evaluation_hash(
                snapshot.fingerprint, "Methods", "Results", None,
            )
            self.assertTrue(asyncio.run(self.store.save_graphic_evaluation(
                7, pid, sid, evaluation_result,
                expected_hash,
            )))
            self.assertEqual(
                self.connection.execute(
                    "SELECT kind FROM source WHERE sid = %s", (sid,),
                ).fetchone()[0],
                "image",
            )
            cached = asyncio.run(self.store.evaluation_sources(7, pid))[0].evaluation
            self.assertEqual(cached["image_analysis"], {
                "classification": "image", "description": "", "math_expressions": [],
            })
            self.assertEqual(cached["evaluations"]["materials_and_methods"], section_evaluation(
                "significantly", "The image shows the procedure.",
            ))
            self.assertEqual(cached["evaluations"]["results"], section_evaluation(
                "highly", "The image shows the findings.",
            ))
            self.assertEqual(
                self.connection.execute(
                    "SELECT cid, sid, section::text, rating, explanation, criteria, "
                    "supported_elements, limitations, suggested_improvements FROM assessment "
                    "WHERE pid = %s ORDER BY aid", (pid,),
                ).fetchall(),
                [
                    (
                        None, sid, "materials and methods", "significantly",
                        "The image shows the procedure.", criteria,
                        ["A labeled element."], ["One limitation."], ["Add a legend."],
                    ),
                    (
                        None, sid, "results", "highly", "The image shows the findings.",
                        criteria,
                        ["A labeled element."], ["One limitation."], ["Add a legend."],
                    ),
                ],
            )
            updated_methods = section_evaluation("highly", "Updated procedure assessment.")
            updated_criteria = {
                "relevance": "Updated relevance.",
                "technical_accuracy": "Updated accuracy.",
                "specificity": "Updated specificity.",
                "completeness": "Updated completeness.",
                "evidentiary_support": "Updated support.",
                "potential_for_misinterpretation": "Updated clarity.",
            }
            updated_methods["criteria"] = updated_criteria
            updated_methods["supported_elements"] = ["Updated element."]
            updated_methods["limitations"] = ["Updated limitation."]
            updated_methods["suggested_improvements"] = ["Add an updated legend."]
            updated_results = section_evaluation("somewhat", "Updated findings assessment.")
            updated_results["criteria"] = updated_criteria
            updated_result = {
                "image_analysis": {"classification": "diagram", "math_expressions": []},
                "evaluations": {
                    "materials_and_methods": updated_methods,
                    "results": updated_results,
                },
            }
            self.assertTrue(asyncio.run(self.store.save_graphic_evaluation(
                7, pid, sid, updated_result, expected_hash,
            )))
            self.assertEqual(
                self.connection.execute(
                    "SELECT kind FROM source WHERE sid = %s", (sid,),
                ).fetchone()[0],
                "diagram",
            )
            self.assertEqual(
                self.connection.execute(
                    "SELECT section::text, rating, explanation, criteria, "
                    "supported_elements, limitations, suggested_improvements FROM assessment "
                    "WHERE pid = %s ORDER BY section", (pid,),
                ).fetchall(),
                [
                    (
                        "materials and methods", "highly", "Updated procedure assessment.",
                        updated_criteria,
                        ["Updated element."],
                        ["Updated limitation."], ["Add an updated legend."],
                    ),
                    (
                        "results", "somewhat", "Updated findings assessment.",
                        updated_criteria,
                        ["A labeled element."], ["One limitation."], ["Add a legend."],
                    ),
                ],
            )
            math_result = {
                "image_analysis": {
                    "classification": "math expression",
                    "math_expressions": [r"E=mc^2", r"\frac{1}{2}"],
                },
                "evaluations": {
                    "materials_and_methods": updated_methods,
                    "results": updated_results,
                },
            }
            with patch.dict(os.environ, {
                "SOURCES_DIR": str(Path(directory) / "math-sources"), "GCS_BUCKET": "",
            }):
                self.assertTrue(asyncio.run(self.store.save_graphic_evaluation(
                    7, pid, sid, math_result, expected_hash,
                )))
                expressions = self.connection.execute(
                    "SELECT parent, kind, text, filename, storage_uri FROM source "
                    "WHERE parent = %s ORDER BY sid", (sid,),
                ).fetchall()
                self.assertEqual(
                    [(parent, kind, text, filename) for parent, kind, text, filename, _ in expressions],
                    [
                        (sid, "math expression", r"E=mc^2", "evaluation-math-expression-1.tex"),
                        (sid, "math expression", r"\frac{1}{2}", "evaluation-math-expression-2.tex"),
                    ],
                )
                self.assertTrue(all(Path(uri).is_file() for *_, uri in expressions))
            self.assertEqual(asyncio.run(self.store.evaluation_sources(8, pid)), [])
            self.assertTrue(asyncio.run(self.store.save_section(7, pid, "results", "Updated results")))
            self.assertIsNone(asyncio.run(self.store.evaluation_sources(7, pid))[0].evaluation)


    def test_multiple_top_level_sources_persist_in_order_and_are_owner_scoped(self) -> None:
        pid, _ = asyncio.run(self.store.new_or_existing(7))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SOURCES_DIR": directory, "GCS_BUCKET": ""}), \
                patch.object(content.processing, "extract", side_effect=lambda name, data: content.processing.Processed(text=data.decode())), \
                patch.object(content.processing, "summarize", AsyncMock(return_value="Summary")):
            first = asyncio.run(self.store.add(7, pid, "first.md", b"first"))
            second = asyncio.run(self.store.add(7, pid, "second.md", b"second"))
            self.assertEqual([item.sid for item in asyncio.run(self.store.list_sources(7, pid))], [first.sid, second.sid])
            self.assertEqual(asyncio.run(self.store.list_sources(8, pid)), [])
            rows = self.connection.execute("SELECT parent, storage_uri FROM source WHERE pid = %s", (pid,)).fetchall()
            self.assertTrue(all(parent is None and Path(uri).is_file() for parent, uri in rows))
            self.assertFalse(first.extracted)
            self.assertFalse(second.extracted)
            self.assertEqual(asyncio.run(self.store.completion(7, pid)), (2, 0))
            asyncio.run(self.store.extract(7, pid, first.sid))
            asyncio.run(self.store.extract(7, pid, second.sid))
            self.assertEqual(asyncio.run(self.store.completion(7, pid)), (2, 11))
            self.assertTrue(all(item.extracted for item in asyncio.run(self.store.list_sources(7, pid))))
            self.connection.execute((ROOT / "schema/source-extraction.sql").read_text())

    def test_concurrent_retries_extract_once_and_preserve_original_on_failure(self) -> None:
        pid, _ = asyncio.run(self.store.new_or_existing(7))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SOURCES_DIR": directory, "GCS_BUCKET": ""}):
            item = asyncio.run(self.store.add(7, pid, "research.md", b"research"))
            uri = self.connection.execute("SELECT storage_uri FROM source WHERE sid = %s", (item.sid,)).fetchone()[0]
            with patch.object(content.processing, "extract", side_effect=source_processing.ProcessingError("offline")):
                with self.assertRaises(source_processing.ProcessingError):
                    asyncio.run(self.store.extract(7, pid, item.sid))
            self.assertEqual(Path(uri).read_bytes(), b"research")
            self.assertFalse(asyncio.run(self.store.list_sources(7, pid))[0].extracted)
            with patch.object(content.processing, "extract", return_value=source_processing.Processed(text="research")) as extract, \
                    patch.object(content.processing, "summarize", AsyncMock(return_value="Summary")):
                async def retry_concurrently() -> None:
                    await asyncio.gather(
                        self.store.extract(7, pid, item.sid),
                        self.store.extract(7, pid, item.sid),
                    )

                asyncio.run(retry_concurrently())
            extract.assert_called_once()
            self.assertEqual(asyncio.run(self.store.completion(7, pid)), (1, 8))

    def test_migration_preserves_existing_extracted_sources(self) -> None:
        pid, _ = asyncio.run(self.store.new_or_existing(7))
        self.connection.execute("ALTER TABLE source DROP COLUMN extracted")
        try:
            self.connection.execute(
                "INSERT INTO source (pid, kind, text, size, mimetype) VALUES (%s, 'text', 'existing', 8, 'text/markdown')",
                (pid,),
            )
        finally:
            self.connection.execute((ROOT / "schema/source-extraction.sql").read_text())
        self.connection.execute((ROOT / "schema/source-extraction.sql").read_text())
        self.assertTrue(asyncio.run(self.store.list_sources(7, pid))[0].extracted)

    def test_image_description_and_logo_classification_are_deferred_until_extraction(self) -> None:
        from PIL import Image
        image = io.BytesIO()
        Image.new("RGB", (300, 400)).save(image, format="PNG")
        pid, _ = asyncio.run(self.store.new_or_existing(7))
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"SOURCES_DIR": directory, "GCS_BUCKET": ""}), \
                patch.object(content.processing, "describe_image", AsyncMock(return_value="A research logo")) as describe:
            item = asyncio.run(self.store.add(7, pid, "graphic.png", image.getvalue()))
            describe.assert_not_awaited()
            self.assertFalse(item.extracted)
            self.assertEqual(item.kind, "image")
            asyncio.run(self.store.extract(7, pid, item.sid))
            describe.assert_awaited_once_with(image.getvalue())
            sources = asyncio.run(self.store.list_sources(7, pid))
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0].sid, item.sid)
            self.assertEqual(sources[0].kind, "logo")
            self.assertEqual(sources[0].description, "A research logo")
            self.assertEqual((sources[0].width, sources[0].height), (300, 400))
            self.assertTrue(sources[0].extracted)


if __name__ == "__main__":
    unittest.main()
