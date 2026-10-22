import asyncio
import os
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import openai
import psycopg
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from psycopg import sql
from starlette.requests import HTTPConnection

import posters
from routers import posters as routes

ROOT = Path(__file__).resolve().parent
DRAFT = posters.Poster(11, "<Research>", "Description", "36x48", None, None, False, None)


class PosterRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.uid = 7
        self.session = {}
        self.store = AsyncMock()
        self.store.list.return_value = [DRAFT, replace(DRAFT, pid=12, purchased=True)]
        self.store.get.return_value = DRAFT
        self.store.content.return_value = (
            [("title", "<Research>"), ("results", "<script>alert(1)</script>")],
            [("Ada", "Institute")],
        )
        mocked = patch.object(posters, "store", return_value=self.store)
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

    def test_list_modes_highlight_draft_and_escape_content(self) -> None:
        for mode in ("list", "grid"):
            response = self.client.get("/posters/list", params={"view": mode})
            self.assertEqual(response.status_code, 200)
            self.assertIn('class="poster-card current"', response.text)
            self.assertIn('/posters/11/open', response.text)
            self.assertIn('/posters/12/view', response.text)
            self.assertIn('&lt;Research&gt;', response.text)
            self.assertIn('data-new-poster', response.text)
            self.assertEqual('data-thumbnail' in response.text, mode == "grid")

    def test_signed_out_pages_redirect_and_mutations_require_identity(self) -> None:
        self.uid = None
        for path in ("/posters/list", "/posters/create", "/posters/11/open", "/posters/11/view"):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/account/signin")
        self.assertEqual(self.client.post("/posters/new", json={}).status_code, 401)
        self.assertEqual(self.client.post("/posters/11/delete", json={"confirmed": True}).status_code, 401)
        self.assertEqual(self.client.get("/posters/11/preview").status_code, 401)
        self.store.list.assert_not_awaited()

    def test_search_normalizes_query_and_passes_sort_options(self) -> None:
        with patch.object(posters, "query_embedding", AsyncMock(return_value=([1.0], None))) as embed:
            response = self.client.get("/posters/list", params={
                "q": "  climate \n research ", "sort": "conference_date",
                "direction": "desc", "view": "grid",
            })
        self.assertEqual(response.status_code, 200)
        embed.assert_awaited_once_with("climate research")
        self.store.list.assert_awaited_once_with(7, "climate research", [1.0], "conference_date", "desc")
        self.assertEqual(self.client.get("/posters/list?sort=DROP%20TABLE").status_code, 422)
        self.assertEqual(self.client.get("/posters/list?direction=invalid").status_code, 422)
        self.assertEqual(self.client.get("/posters/list?view=invalid").status_code, 422)

    def test_blank_query_does_not_embed_and_search_unavailability_is_visible(self) -> None:
        with patch.object(posters, "query_embedding", AsyncMock(return_value=(None, "Keyword search only"))) as embed:
            self.client.get("/posters/list?q=%20%20")
            embed.assert_not_awaited()
            response = self.client.get("/posters/list?q=research")
        self.assertIn("Keyword search only", response.text)

    def test_new_poster_selects_draft_and_returns_wikkit_redirect(self) -> None:
        self.store.new_or_existing.return_value = (11, True)
        response = self.client.post("/posters/new", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect"], "/posters/create")
        self.assertIn("&lt;Ada&gt;", response.json()["say"])
        self.assertEqual(self.session["poster_id"], 11)
        self.store.new_or_existing.assert_awaited_once_with(7)

    def test_existing_draft_offers_to_open_without_changing_selection(self) -> None:
        self.store.new_or_existing.return_value = (11, False)
        response = self.client.post("/posters/new", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["existing_poster"], 11)
        self.assertIn("Would you like me to open it for you?", response.json()["say"])
        self.assertNotIn("redirect", response.json())
        self.assertNotIn("poster_id", self.session)

    def test_create_initializes_vertical_layout_and_installed_theme(self) -> None:
        self.session["poster_id"] = 11
        response = self.client.get("/posters/create")
        self.assertEqual(response.status_code, 200)
        self.store.initialize.assert_awaited_once_with(7, 11, "vertical", "basic")
        self.assertIn("/posters/11/open", response.text)
        self.session.clear()
        self.assertEqual(self.client.get("/posters/create", follow_redirects=False).headers["location"], "/posters/list")

    def test_draft_open_selects_owner_checked_poster_and_redirects_to_editor(self) -> None:
        response = self.client.get("/posters/11/open", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/layout/edit")
        self.assertEqual(self.session["poster_id"], 11)
        self.store.get.assert_awaited_once_with(7, 11)
        self.store.get.return_value = None
        self.assertEqual(self.client.get("/posters/99/open").status_code, 404)

    def test_purchased_viewer_and_preview_are_owner_scoped(self) -> None:
        self.store.get.return_value = replace(DRAFT, purchased=True, theme="basic")
        response = self.client.get("/posters/11/view")
        self.assertEqual(response.status_code, 200)
        self.assertIn("data-fit", response.text)
        self.assertIn("data-full-size", response.text)
        self.assertNotIn("connect(", response.text)
        response = self.client.get("/posters/11/preview")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-width="3456.0"', response.text)
        self.assertIn('data-height="4608.0"', response.text)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert", response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.store.content.assert_awaited_once_with(7, 11)
        self.store.get.return_value = None
        self.assertEqual(self.client.get("/posters/99/view").status_code, 404)
        self.assertEqual(self.client.get("/posters/99/preview").status_code, 404)

    def test_unpurchased_view_redirects_to_edit_flow(self) -> None:
        response = self.client.get("/posters/11/view", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/posters/11/open")

    def test_delete_requires_confirmation_and_clears_selected_poster(self) -> None:
        for value in (None, False, "true", 1):
            response = self.client.post("/posters/11/delete", json={"confirmed": value})
            self.assertEqual(response.status_code, 422)
        self.store.delete.assert_not_awaited()
        self.store.delete.return_value = True
        self.session["poster_id"] = 11
        response = self.client.post("/posters/11/delete", json={"confirmed": True})
        self.assertTrue(response.json()["ok"])
        self.assertNotIn("poster_id", self.session)
        self.store.delete.assert_awaited_once_with(7, 11)
        self.store.delete.return_value = False
        self.assertEqual(self.client.post("/posters/99/delete", json={"confirmed": True}).status_code, 404)

    def test_mutations_reject_cross_origin_and_non_json_requests(self) -> None:
        for path in ("/posters/new", "/posters/11/delete"):
            self.assertEqual(self.client.post(path, data={}).status_code, 415)
            self.assertEqual(self.client.post(path, json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.store.new_or_existing.assert_not_awaited()
        self.store.delete.assert_not_awaited()

    def test_database_errors_are_visible(self) -> None:
        self.store.list.side_effect = psycopg.OperationalError("unavailable")
        self.store.new_or_existing.side_effect = psycopg.OperationalError("unavailable")
        self.store.delete.side_effect = psycopg.OperationalError("unavailable")
        with self.assertLogs("routers.posters", "ERROR"):
            response = self.client.get("/posters/list")
            self.assertEqual(response.status_code, 503)
            self.assertIn("could not be loaded", response.text)
            self.assertEqual(self.client.post("/posters/new", json={}).status_code, 503)
            self.assertEqual(self.client.post("/posters/11/delete", json={"confirmed": True}).status_code, 503)


class PosterValueTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedding_fallback_is_explicit(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            embedding, notice = await posters.query_embedding("research")
        self.assertIsNone(embedding)
        self.assertIn("keyword", notice)
        client = AsyncMock()
        client.embeddings.create.side_effect = openai.APIConnectionError(request=None)
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), patch.object(openai, "AsyncOpenAI", return_value=context):
            with self.assertLogs("posters", "WARNING"):
                embedding, notice = await posters.query_embedding("research")
        self.assertIsNone(embedding)
        self.assertIsNotNone(notice)

    async def test_dimensions_reject_unsafe_values(self) -> None:
        self.assertEqual(DRAFT.inches, (36, 48))
        for value in ("0x48", "36x99999", "-1x48", "36;DROP TABLE", "NaNx48"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(DRAFT, dimensions=value).inches

    async def test_selected_poster_identity_is_revalidated(self) -> None:
        connection = HTTPConnection({"type": "http", "headers": [], "session": {"poster_id": 11}})
        connection.state.user_id = 7
        store = AsyncMock()
        with patch.object(posters, "store", return_value=store):
            store.get.return_value = DRAFT
            await posters.poster_context(connection)
            self.assertEqual(connection.state.poster_id, 11)
            store.get.return_value = None
            await posters.poster_context(connection)
            self.assertIsNone(connection.state.poster_id)
            self.assertNotIn("poster_id", connection.scope["session"])


@unittest.skipUnless(os.environ.get("POSTERS_TEST_DATABASE_URL"), "Set POSTERS_TEST_DATABASE_URL for isolated PostgreSQL tests")
class PosterDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = "test_posters_" + uuid.uuid4().hex
        cls.dsn = os.environ["POSTERS_TEST_DATABASE_URL"]
        cls.connection = psycopg.connect(cls.dsn, autocommit=True)
        cls.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.addClassCleanup(cls.cleanup_schema)
        cls.connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(cls.schema)))
        cls.connection.execute((ROOT / "schema/schema.sql").read_text())
        cls.store = posters.PosterStore("POSTERS_TEST_DATABASE_URL", cls.schema)

    @classmethod
    def cleanup_schema(cls) -> None:
        cls.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))
        cls.connection.close()

    def setUp(self) -> None:
        self.connection.execute('TRUNCATE "user" RESTART IDENTITY CASCADE')
        self.connection.execute("""
            INSERT INTO "user" (uid, name, username, email, password, pwd_updated, created, updated)
            VALUES (7, 'Ada', 'ada', 'ada@example.com', 'hash', now(), now(), now()),
                   (8, 'Grace', 'grace', 'grace@example.com', 'hash', now(), now(), now())
        """)

    def create(self, name: str, uid: int = 7) -> int:
        row = self.connection.execute(
            "INSERT INTO poster (uid, description, created) VALUES (%s, %s, now()) RETURNING pid",
            (uid, name),
        ).fetchone()
        return row[0]

    def purchase(self, pid: int) -> None:
        self.connection.execute(
            "INSERT INTO purchase VALUES (%s, 7, 10, 'test', now())", (pid,)
        )

    def test_concurrent_new_clicks_create_one_draft_and_allow_new_after_purchase(self) -> None:
        async def concurrent():
            return await asyncio.gather(*(self.store.new_or_existing(7) for _ in range(6)))
        results = asyncio.run(concurrent())
        self.assertEqual(len({pid for pid, _ in results}), 1)
        self.assertEqual(sum(created for _, created in results), 1)
        self.purchase(results[0][0])
        new_pid, created = asyncio.run(self.store.new_or_existing(7))
        self.assertTrue(created)
        self.assertNotEqual(new_pid, results[0][0])

    def test_list_owner_scope_sort_and_purchase_state(self) -> None:
        z = self.create("Zoology")
        a = self.create("Astronomy")
        self.create("Private", uid=8)
        self.purchase(z)
        rows = asyncio.run(self.store.list(7))
        self.assertEqual([row.pid for row in rows], [a, z])
        self.assertTrue(rows[1].purchased)
        rows = asyncio.run(self.store.list(7, direction="desc"))
        self.assertEqual([row.pid for row in rows], [z, a])
        self.assertIsNone(asyncio.run(self.store.get(8, a)))

    def test_fts_tracks_description_sections_authors_and_keyterms(self) -> None:
        pid = self.create("Ocean science")
        private = self.create("Ocean science", uid=8)
        self.assertEqual([p.pid for p in asyncio.run(self.store.list(7, "ocean"))], [pid])
        self.connection.execute(
            "INSERT INTO section (pid, which, content, updated) VALUES (%s, 'title', 'Coral reefs', now())", (pid,)
        )
        self.assertEqual(asyncio.run(self.store.list(7, "coral"))[0].name, "Coral reefs")
        self.connection.execute("UPDATE section SET content = 'Volcanoes' WHERE pid = %s", (pid,))
        self.assertEqual(asyncio.run(self.store.list(7, "coral")), [])
        self.connection.execute("INSERT INTO authors (pid, name) VALUES (%s, 'Lovelace')", (pid,))
        self.assertEqual(asyncio.run(self.store.list(7, "Lovelace"))[0].pid, pid)
        self.connection.execute("INSERT INTO keyterm (pid, text) VALUES (%s, 'Geology')", (pid,))
        self.assertEqual(asyncio.run(self.store.list(7, "geology"))[0].pid, pid)
        self.connection.execute("DELETE FROM keyterm WHERE pid = %s", (pid,))
        self.assertEqual(asyncio.run(self.store.list(7, "geology")), [])
        self.assertIsNotNone(private)

    def test_vector_only_and_hybrid_search_are_owner_scoped(self) -> None:
        one = [1.0] + [0.0] * 1535
        other = [0.0, 1.0] + [0.0] * 1534
        exact = self.create("Coral")
        semantic = self.create("Marine ecosystems")
        private = self.create("Coral", uid=8)
        for pid, vector in ((exact, one), (semantic, other), (private, one)):
            self.connection.execute("UPDATE poster SET embedding = %s::vector WHERE pid = %s", (str(vector), pid))
        rows = asyncio.run(self.store.list(7, "coral", one, "relevance", "desc"))
        self.assertEqual([row.pid for row in rows], [exact, semantic])
        rows = asyncio.run(self.store.list(7, "no-keyword-match", other, "relevance", "desc"))
        self.assertEqual([row.pid for row in rows], [semantic, exact])

    def test_initialize_is_idempotent_and_never_modifies_purchased_or_foreign_posters(self) -> None:
        pid = self.create("Draft")
        asyncio.run(self.store.initialize(8, pid, "central", "basic"))
        self.assertIsNone(asyncio.run(self.store.get(7, pid)).template)
        asyncio.run(self.store.initialize(7, pid, "vertical", "basic"))
        asyncio.run(self.store.initialize(7, pid, "central", "other"))
        self.assertEqual(asyncio.run(self.store.get(7, pid)).template, "vertical")
        self.purchase(pid)
        asyncio.run(self.store.initialize(7, pid, "arena", "other"))
        self.assertEqual(asyncio.run(self.store.get(7, pid)).theme, "basic")

    def test_conference_sort_uses_earliest_show_date_and_keeps_unset_dates_last(self) -> None:
        early = self.create("Early")
        late = self.create("Late")
        unset = self.create("No conference")
        for name, date, pid in (
            ("First", "2026-01-01", early),
            ("Later", "2026-10-01", early),
            ("Second", "2026-06-01", late),
        ):
            cid = self.connection.execute(
                "INSERT INTO conference (name, description, starts, ends, location, "
                "administrator, password, notify, created, updated, hideafter) "
                "VALUES (%s, '', %s, %s, '', '', '', 'never', now(), now(), 0) RETURNING cid",
                (name, date, date),
            ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO show (cid, pid, searchable) VALUES (%s, %s, 0)", (cid, pid)
            )
        ascending = asyncio.run(self.store.list(7, sort="conference_date"))
        descending = asyncio.run(self.store.list(7, sort="conference_date", direction="desc"))
        self.assertEqual([row.pid for row in ascending], [early, late, unset])
        self.assertEqual([row.pid for row in descending], [late, early, unset])

    def test_delete_checks_owner_and_cascades_dependents_including_memory(self) -> None:
        pid = self.create("Delete me")
        self.purchase(pid)
        self.connection.execute('CREATE TABLE IF NOT EXISTS poster_memory (pid integer REFERENCES poster(pid), key text, value jsonb)')
        self.connection.execute("INSERT INTO poster_memory VALUES (%s, 'test', '{}')", (pid,))
        self.assertFalse(asyncio.run(self.store.delete(8, pid)))
        self.assertIsNotNone(asyncio.run(self.store.get(7, pid)))
        self.assertTrue(asyncio.run(self.store.delete(7, pid)))
        self.assertIsNone(asyncio.run(self.store.get(7, pid)))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM purchase WHERE pid = %s", (pid,)).fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM poster_memory WHERE pid = %s", (pid,)).fetchone()[0], 0)

    def test_schema_can_be_reapplied(self) -> None:
        pid = self.create("Backfill")
        self.connection.execute("UPDATE poster SET search_vector = NULL WHERE pid = %s", (pid,))
        self.connection.execute((ROOT / "schema/schema.sql").read_text())
        self.assertEqual(asyncio.run(self.store.list(7, "backfill"))[0].pid, pid)


if __name__ == "__main__":
    unittest.main()
