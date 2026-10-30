import os
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import psycopg
from pydantic import ValidationError

import content
import docxtractor
import elucidation
from tests import test_content


def poster_content(**changes) -> elucidation.PosterContent:
    data = {
        "title": "Research study",
        "authors": [{"name": "Ada", "institution": "Research Institute", "errata": "ada@example.com"}],
        "Abstract": "<h2>Abstract</h2><p>We tested the hypothesis.<br>Our study found an effect.</p>",
        "introduction": "<h2>Introduction</h2><p>We investigated a question <strong>[1]</strong>.</p>",
        "materials and methods": "<p>We measured outcomes.</p>",
        "results": "<p>The procedure worked.</p><p>We found an effect.</p><ul><li>An important result.</li></ul>",
        "conclusions": "<p>The evidence supports our hypothesis.</p>",
        "future plans": "",
        "literature cited": "<ol><li>Supplied citation.</li></ol>",
        "acknowledgements": ["Funding organization"],
        "further information": '<p>Contact <a href="mailto:ada@example.com">Ada</a>.</p>',
    }
    data.update(changes)
    return elucidation.PosterContent.model_validate(data)


class ElucidationModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_output_request_has_required_fields_and_no_browsing(self) -> None:
        client = AsyncMock()
        client.responses.create.return_value = SimpleNamespace(
            status="completed", output_text=poster_content().model_dump_json(by_alias=True),
        )
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), \
                patch.object(elucidation.openai, "AsyncOpenAI", return_value=context):
            result = await elucidation.analyze("Source research")
        self.assertEqual(result.title, "Research study")
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["input"], "Source research")
        self.assertNotIn("tools", kwargs)
        format = kwargs["text"]["format"]
        self.assertEqual(format["type"], "json_schema")
        self.assertTrue(format["strict"])
        schema = format["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertIn("Abstract", schema["properties"])
        author_schema = schema["$defs"]["Author"]
        self.assertEqual(set(author_schema["required"]), {"name", "institution", "errata"})
        self.assertFalse(author_schema["additionalProperties"])
        self.assertIn("9th grade", kwargs["instructions"])
        self.assertIn("not more than four sentences", kwargs["instructions"])

    async def test_refusal_incomplete_malformed_and_missing_field_responses_fail_explicitly(self) -> None:
        client = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), \
                patch.object(elucidation.openai, "AsyncOpenAI", return_value=context):
            for status, text in (
                ("incomplete", "{}"), ("completed", ""),
                ("completed", "not JSON"), ("completed", '{"title":"Research"}'),
            ):
                with self.subTest(status=status, text=text):
                    client.responses.create.return_value = SimpleNamespace(status=status, output_text=text)
                    with self.assertRaises(elucidation.ElucidationError):
                        await elucidation.analyze("Research")

    async def test_missing_credentials_fail_without_a_request(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch.object(elucidation.openai, "AsyncOpenAI") as client:
            with self.assertRaises(elucidation.ElucidationError):
                await elucidation.analyze("Research")
        client.assert_not_called()

    async def test_sections_preserve_all_fields_and_allow_only_safe_html(self) -> None:
        result = poster_content(
            title="<strong>Research &amp; findings</strong>",
            introduction='<h2>Introduction</h2><p onclick="alert(1)">Text <strong>[1]</strong>'
                         '<a href="javascript:alert(1)">link</a><img src=x onerror="alert(1)"></p>',
            acknowledgements=["<em>Funder</em>", "<script>alert(1)</script>"],
        )
        sections = result.sections()
        self.assertEqual(sections["title"], "Research & findings")
        self.assertIn("<h2>Materials and methods</h2>", sections["materials and methods"])
        self.assertIn("<h2>Conclusions</h2>", sections["conclusion"])
        self.assertIn("<h2>Further information</h2>", sections["additional information"])
        self.assertIn("<ul><li><em>Funder</em></li>", sections["acknowledgements"])
        self.assertIn("<strong>[1]</strong>", sections["introduction"])
        self.assertNotIn("onclick=", sections["introduction"])
        self.assertNotIn('href="javascript:', sections["introduction"])
        self.assertNotIn("<img", sections["introduction"])
        self.assertNotIn("<script>", sections["acknowledgements"])
        self.assertEqual(sections["future plans"], "")

    async def test_author_fields_are_plain_and_duplicate_names_are_rejected(self) -> None:
        result = poster_content(authors=[{"name": "<b>Ada</b>", "institution": "A &amp; B", "errata": "Contact"}])
        self.assertEqual(result.authors[0].name, "Ada")
        self.assertEqual(result.authors[0].institution, "A & B")
        for authors in (
            [{"name": " ", "institution": "", "errata": ""}],
            [{"name": "Ada", "institution": "", "errata": ""},
             {"name": "ada", "institution": "", "errata": ""}],
        ):
            with self.assertRaises(ValidationError):
                poster_content(authors=authors)

    async def test_results_limit_excludes_header_and_rejects_three_hundred_words(self) -> None:
        result = poster_content(results="<h2>Results</h2><p>" + "word " * 299 + "</p>")
        self.assertIn("Results", result.results)
        with self.assertRaises(ValidationError):
            poster_content(results="<p>" + "word " * 300 + "</p>")

    async def test_native_apple_documents_do_not_require_os_export(self) -> None:
        from docling_core.types.doc import DoclingDocument, DocItemLabel
        document = DoclingDocument(name="study")
        document.add_text(label=DocItemLabel.PARAGRAPH, text="Research")
        client = Mock()
        client.convert.return_value = SimpleNamespace(status="success", document=document, errors=[])
        for suffix in (".pages", ".numbers", ".key"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                result = docxtractor.extract_document(b"archive", filename="study" + suffix,
                                                      output_dir=directory, client=client)
                self.assertEqual(result["processed_format"], suffix[1:])
                self.assertEqual(client.convert.call_args.kwargs["source"].suffix, suffix)


class ElucidationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        test_content.ContentRouteTests.setUp(self)
        self.snapshot = content.SourceText(1, "x" * 4000, "fingerprint", 4000)
        self.sources.source_text.return_value = self.snapshot
        self.result = poster_content()
        mocked = patch.object(elucidation, "analyze", AsyncMock(return_value=self.result))
        self.analyze = mocked.start()
        self.addCleanup(mocked.stop)

    def test_get_renders_initial_wikkit_message_progress_choices_and_retry(self) -> None:
        response = self.client.get("/content/elucidate")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Please give me a few minutes", response.text)
        self.assertIn("I\\u0026rsquo;m reviewing", response.text)
        self.assertIn("\\u0026lt;Ada\\u0026gt;", response.text)
        self.assertIn("Getting a cup of tea.", response.text)
        self.assertIn('id="retry-elucidation"', response.text)
        self.assertIn("/static/javascript/pages/content-elucidate.js", response.text)
        self.analyze.assert_not_awaited()

    def test_post_analyzes_merged_sources_and_saves_before_conference_redirect(self) -> None:
        response = self.client.post("/content/elucidate", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "redirect": "/conferences/query"})
        self.sources.source_text.assert_awaited_once_with(7, 11)
        self.analyze.assert_awaited_once_with(self.snapshot.text)
        self.sources.save_elucidation.assert_awaited_once_with(7, 11, self.snapshot, self.result)
        self.posters.invalidate.assert_called_once_with(7, 11)

    def test_page_and_post_require_signed_in_owner_and_editable_selected_poster(self) -> None:
        self.uid = None
        response = self.client.get("/content/elucidate", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/account/signin")
        self.assertEqual(self.client.post("/content/elucidate", json={}).status_code, 401)
        self.uid = 7
        self.session.clear()
        self.assertEqual(self.client.post("/content/elucidate", json={}).status_code, 409)
        self.session["poster_id"] = 11
        self.posters.get.return_value = None
        self.assertEqual(self.client.post("/content/elucidate", json={}).status_code, 404)
        from dataclasses import replace
        self.posters.get.return_value = replace(test_content.DRAFT, purchased=True)
        self.assertEqual(self.client.post("/content/elucidate", json={}).status_code, 409)
        self.analyze.assert_not_awaited()

    def test_post_requires_same_origin_json_and_revalidates_minimum(self) -> None:
        self.assertEqual(self.client.post("/content/elucidate", content="").status_code, 415)
        self.assertEqual(self.client.post("/content/elucidate", json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
        for count, length in ((0, 0), (1, 0), (1, 3999)):
            self.sources.source_text.return_value = content.SourceText(count, "x" * length, "hash", length)
            self.assertEqual(self.client.post("/content/elucidate", json={}).status_code, 409)
        self.analyze.assert_not_awaited()
        self.sources.save_elucidation.assert_not_awaited()

    def test_analysis_cannot_skip_pending_extraction_even_with_enough_existing_text(self) -> None:
        self.sources.source_text.side_effect = content.SourceConflictError(
            "Please finish reading your files at /content/extract before running analysis."
        )
        response = self.client.post("/content/elucidate", json={})
        self.assertEqual(response.status_code, 409)
        self.assertIn("/content/extract", response.json()["detail"])
        self.analyze.assert_not_awaited()
        self.sources.save_elucidation.assert_not_awaited()

    def test_analysis_failures_do_not_save_and_database_failures_do_not_redirect(self) -> None:
        self.analyze.side_effect = elucidation.ElucidationError("Analysis was incomplete.")
        with self.assertLogs("routers.content", "ERROR"):
            response = self.client.post("/content/elucidate", json={})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("redirect", response.json())
        self.sources.save_elucidation.assert_not_awaited()
        self.analyze.side_effect = None
        self.sources.save_elucidation.side_effect = psycopg.OperationalError("down")
        with self.assertLogs("routers.content", "ERROR"):
            self.assertEqual(self.client.post("/content/elucidate", json={}).status_code, 503)

    def test_changed_sources_return_conflict_without_redirect(self) -> None:
        self.sources.save_elucidation.side_effect = content.SourceConflictError("Sources changed.")
        response = self.client.post("/content/elucidate", json={})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("redirect", response.json())

    def test_preview_renders_safe_section_html_without_duplicate_headers(self) -> None:
        from routers import posters as poster_routes
        self.client.app.include_router(poster_routes.router)
        self.posters.content.return_value = (
            [("introduction", '<h2>Introduction</h2><p>Research <strong>[1]</strong></p><script>bad()</script>')],
            [("Ada", "Research Institute")],
        )
        response = self.client.get("/posters/11/preview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count("<h2>Introduction</h2>"), 1)
        self.assertIn("<strong>[1]</strong>", response.text)
        self.assertNotIn("<script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)


class ElucidationStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_text_is_owner_scoped_merged_once_and_fingerprinted(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchall.return_value = [
            (1, "study.md", "Research", "text/markdown", True),
            (2, "data.xlsx", "<table><tr><td>A &amp; B</td></tr></table>", content.FORMATS[".xlsx"][0], True),
        ]
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            snapshot = await store.source_text(7, 11)
        self.assertEqual(snapshot.text, "Research\n\nA & B")
        self.assertEqual(snapshot.character_count, 13)
        statement, params = connection.execute.call_args.args
        self.assertIn("p.uid = %s AND p.pid = %s AND s.parent IS NULL", statement)
        self.assertEqual(params, (7, 11))
        first_hash = snapshot.fingerprint
        connection.execute.return_value.fetchall.return_value[0] = (1, "study.md", "Changed", "text/markdown", True)
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            self.assertNotEqual((await store.source_text(7, 11)).fingerprint, first_hash)

    async def test_source_snapshot_rejects_pending_files_even_with_enough_existing_text(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchall.return_value = [
            (1, "study.md", "Research" * 1000, "text/markdown", True),
            (2, "new.md", None, "text/markdown", False),
        ]
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            with self.assertRaises(content.SourceConflictError):
                await store.source_text(7, 11)

    async def test_save_is_transactional_and_preserves_author_metadata_and_all_sections(self) -> None:
        store = content.SourceStore()
        connection = AsyncMock()
        connection.execute.return_value.fetchone.return_value = (11,)
        snapshot = content.SourceText(1, "research", "hash", 8)
        result = poster_content()
        with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                patch.object(store, "source_text", AsyncMock(return_value=snapshot)):
            await store.save_elucidation(7, 11, snapshot, result)
        calls = connection.execute.await_args_list
        self.assertIn('FROM "user"', calls[0].args[0])
        self.assertIn("FOR UPDATE", calls[0].args[0])
        self.assertEqual(calls[1].args[1], (7, 11))
        self.assertIn("NOT EXISTS", calls[1].args[0])
        self.assertEqual(calls[2].args, ("DELETE FROM authors WHERE pid = %s", (11,)))
        self.assertEqual(calls[3].args[1], (11, "Ada", "Research Institute", "ada@example.com"))
        sections = {call.args[1][1]: call.args[1][2] for call in calls if "INSERT INTO section" in call.args[0]}
        self.assertEqual(sections, result.sections())
        self.assertIn("acknowledgements", sections)
        self.assertIn("additional information", sections)
        connection.__aexit__.assert_awaited_once_with(None, None, None)

    async def test_ownership_purchase_or_source_change_prevents_all_writes(self) -> None:
        for missing in (True, False):
            store = content.SourceStore()
            connection = AsyncMock()
            connection.execute.return_value.fetchone.return_value = None if missing else (11,)
            snapshot = content.SourceText(1, "research", "old", 8)
            with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                    patch.object(store, "source_text", AsyncMock(return_value=content.SourceText(1, "new", "new", 3))):
                with self.assertRaises(content.SourceConflictError):
                    await store.save_elucidation(7, 11, snapshot, poster_content())
            self.assertFalse(any("DELETE" in call.args[0] or "INSERT" in call.args[0]
                                 for call in connection.execute.await_args_list))
            self.assertEqual(connection.__aexit__.call_args.args[0], content.SourceConflictError)

    async def test_failed_author_or_section_write_rolls_back_the_whole_transaction(self) -> None:
        for fail_at in (4, 7):
            store = content.SourceStore()
            connection = AsyncMock()
            cursor = AsyncMock()
            cursor.fetchone.return_value = (11,)
            connection.execute.side_effect = [cursor] * (fail_at - 1) + [psycopg.OperationalError("down")]
            snapshot = content.SourceText(1, "research", "hash", 8)
            with patch.object(store, "_connect", AsyncMock(return_value=connection)), \
                    patch.object(store, "source_text", AsyncMock(return_value=snapshot)):
                with self.assertRaises(psycopg.OperationalError):
                    await store.save_elucidation(7, 11, snapshot, poster_content())
            self.assertEqual(connection.__aexit__.call_args.args[0], psycopg.OperationalError)


@unittest.skipUnless(os.environ.get("POSTERS_TEST_DATABASE_URL"), "Set POSTERS_TEST_DATABASE_URL for PostgreSQL tests")
class ElucidationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        test_content.ContentDatabaseTests.setUpClass.__func__(cls)

    @classmethod
    def cleanup_schema(cls) -> None:
        test_content.ContentDatabaseTests.cleanup_schema.__func__(cls)

    def setUp(self) -> None:
        test_content.ContentDatabaseTests.setUp(self)
        self.pid, _ = asyncio.run(self.store.new_or_existing(7))
        self.connection.execute(
            "INSERT INTO source (pid, kind, parent, text, size, filename, mimetype) "
            "VALUES (%s, 'text', NULL, 'Research results', 16, 'paper.md', 'text/markdown')",
            (self.pid,),
        )

    def test_save_persists_authors_errata_all_sections_and_replaces_stale_values(self) -> None:
        snapshot = asyncio.run(self.store.source_text(7, self.pid))
        asyncio.run(self.store.save_elucidation(7, self.pid, snapshot, poster_content()))
        authors = self.connection.execute(
            "SELECT name, affiliation, errata FROM authors WHERE pid = %s", (self.pid,),
        ).fetchall()
        self.assertEqual(authors, [("Ada", "Research Institute", "ada@example.com")])
        sections = dict(self.connection.execute(
            "SELECT which::text, content FROM section WHERE pid = %s", (self.pid,),
        ).fetchall())
        self.assertEqual(sections, poster_content().sections())
        result = poster_content(authors=[], acknowledgements=[], **{"future plans": ""})
        asyncio.run(self.store.save_elucidation(7, self.pid, snapshot, result))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM authors WHERE pid = %s", (self.pid,),
        ).fetchone()[0], 0)
        self.assertEqual(self.connection.execute(
            "SELECT content FROM section WHERE pid = %s AND which = 'acknowledgements'", (self.pid,),
        ).fetchone()[0], "")

    def test_changed_sources_or_different_owner_do_not_replace_existing_sections(self) -> None:
        snapshot = asyncio.run(self.store.source_text(7, self.pid))
        asyncio.run(self.store.save_elucidation(7, self.pid, snapshot, poster_content()))
        self.connection.execute("UPDATE source SET text = 'Changed evidence' WHERE pid = %s", (self.pid,))
        for uid in (7, 8):
            with self.assertRaises(content.SourceConflictError):
                asyncio.run(self.store.save_elucidation(uid, self.pid, snapshot, poster_content(title="Stale title")))
        self.assertEqual(self.connection.execute(
            "SELECT content FROM section WHERE pid = %s AND which = 'title'", (self.pid,),
        ).fetchone()[0], "Research study")


if __name__ == "__main__":
    unittest.main()
