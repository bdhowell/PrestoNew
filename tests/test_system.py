import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from PIL import Image

import content
import posters
from routers import system


class CatalogRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict(os.environ, {"SYSTEM_PURGE_ENABLED": "true"})
        environment.start()
        self.addCleanup(environment.stop)
        self.catalog = AsyncMock(return_value=[])
        self.graphic = AsyncMock(return_value=None)
        for name, mock in (("catalog", self.catalog), ("catalog_graphic", self.graphic)):
            patched = patch.object(system._store, name, mock)
            patched.start()
            self.addCleanup(patched.stop)
        app = FastAPI()
        app.include_router(system.router)
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def test_form_accepts_poster_id_without_identity_or_selected_draft(self) -> None:
        response = self.client.get("/system/catalog")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="pid"', response.text)
        self.assertIn('action="/system/catalog"', response.text)
        self.assertIn("/static/css/pages/system-catalog.css", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.catalog.assert_not_awaited()
        response = self.client.get("/system/catalog?pid=11")
        self.assertEqual(response.status_code, 200)
        self.assertIn("No sources are associated", response.text)
        self.catalog.assert_awaited_once_with(11)

    def test_lists_text_originals_children_and_exact_size_threshold(self) -> None:
        self.catalog.return_value = [
            content.Source(1, "<paper>.pdf", "application/pdf", 12, "", extracted=False),
            content.Source(2, "text.md", "text/markdown", 12, "", parent=1, kind="text"),
            content.Source(3, "large.png", "image/png", 12, "", parent=1, width=450, height=450, kind="image"),
            content.Source(4, "narrow.png", "image/png", 12, "", width=449, height=900, kind="chart"),
            content.Source(5, "short.png", "image/png", 12, "", width=900, height=449, kind="logo"),
            content.Source(6, "unknown.svg", "image/svg+xml", 12, "", kind="svg"),
            content.Source(7, "missing.png", "image/png", 12, "", width=450, kind="image"),
            content.Source(8, "zero.png", "image/png", 12, "", width=0, height=900, kind="image"),
            content.Source(9, "table.png", "image/png", 12, "", width=900, height=450, kind="table",
                           description="<script>bad()</script>"),
        ]
        response = self.client.get("/system/catalog?pid=11")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Poster 11: 9 sources", response.text)
        self.assertIn("&lt;paper&gt;.pdf", response.text)
        self.assertIn("Extraction pending", response.text)
        self.assertIn("Extracted from source 1", response.text)
        self.assertEqual(response.text.count('aria-label="Text source"'), 2)
        self.assertEqual(response.text.count("Suitable: meets minimum size"), 2)
        self.assertEqual(response.text.count("Not suitable: below minimum size"), 2)
        self.assertEqual(response.text.count("Unknown: size cannot be verified"), 3)
        self.assertIn('3.00&quot; &times; 3.00&quot; at 150 dpi', response.text)
        for sid in range(3, 10):
            self.assertIn(f"/system/catalog/11/{sid}/thumbnail", response.text)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", response.text)
        self.assertNotIn("<script>bad()", response.text)

    def test_invalid_poster_ids_missing_posters_and_database_errors(self) -> None:
        for pid in ("0", "-1", "abc", "", "2147483648", "1.5"):
            self.assertEqual(self.client.get("/system/catalog?pid=" + pid).status_code, 422)
        self.catalog.assert_not_awaited()
        self.catalog.return_value = None
        self.assertEqual(self.client.get("/system/catalog?pid=11").status_code, 404)
        self.catalog.side_effect = psycopg.OperationalError("offline")
        with self.assertLogs("routers.system", "ERROR"):
            self.assertEqual(self.client.get("/system/catalog?pid=11").status_code, 503)

    def test_both_routes_apply_purge_guard_before_storage(self) -> None:
        paths = ("/system/catalog?pid=11", "/system/catalog/11/3/thumbnail")
        for path in paths:
            for value in ("", "false", "TRUE", "1"):
                with patch.dict(os.environ, {"SYSTEM_PURGE_ENABLED": value}):
                    self.assertEqual(self.client.get(path).status_code, 403)
            for host in ("203.0.113.1", "testclient"):
                client = TestClient(self.client.app, base_url="http://localhost", client=(host, 50000))
                self.assertEqual(client.get(path).status_code, 403)
            for host in ("example.com", "localhost.evil.test", "0.0.0.0", "[invalid"):
                self.assertEqual(self.client.get(path, headers={"Host": host}).status_code, 403)
            self.assertEqual(self.client.get(path, headers=[
                ("Host", "localhost"), ("Host", "example.com"),
            ]).status_code, 403)
            for name in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto"):
                self.assertEqual(self.client.get(path, headers={name: "127.0.0.1"}).status_code, 403)
        self.catalog.assert_not_awaited()
        self.graphic.assert_not_awaited()

    def test_thumbnail_uses_explicit_poster_and_source_and_preserves_aspect_ratio(self) -> None:
        image = io.BytesIO()
        Image.new("RGB", (900, 450)).save(image, "PNG")
        self.graphic.return_value = (image.getvalue(), "image/png")
        response = self.client.get("/system/catalog/11/3/thumbnail")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.graphic.assert_awaited_once_with(11, 3)
        with Image.open(io.BytesIO(response.content)) as thumbnail:
            self.assertEqual(thumbnail.size, (160, 80))

    def test_svg_thumbnail_is_an_image_with_sandbox_and_no_inline_execution(self) -> None:
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="450" height="450"/>'
        self.graphic.return_value = (svg, "image/svg+xml")
        response = self.client.get("/system/catalog/11/3/thumbnail")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, svg)
        self.assertEqual(response.headers["content-type"], "image/svg+xml")
        self.assertIn("sandbox", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_thumbnail_missing_and_failed_storage_and_decode_are_explicit(self) -> None:
        self.assertEqual(self.client.get("/system/catalog/11/3/thumbnail").status_code, 404)
        for error in (psycopg.OperationalError("offline"), OSError("missing")):
            self.graphic.side_effect = error
            with self.assertLogs("routers.system", "ERROR"):
                self.assertEqual(self.client.get("/system/catalog/11/3/thumbnail").status_code, 503)
        self.graphic.side_effect = None
        self.graphic.return_value = (b"invalid image", "image/png")
        with self.assertLogs("routers.system", "ERROR"):
            self.assertEqual(self.client.get("/system/catalog/11/3/thumbnail").status_code, 503)


class SystemRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve() / "sources"
        (self.root / "0").mkdir(parents=True)
        (self.root / "0" / "upload").write_bytes(b"source")
        (self.root / "9" / "nested").mkdir(parents=True)
        (self.root / "keep").mkdir()
        (self.root / "note.txt").write_text("keep")
        environment = patch.dict(os.environ, {
            "SYSTEM_PURGE_ENABLED": "true", "SOURCES_DIR": str(self.root), "GCS_BUCKET": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.purge = AsyncMock(return_value={"posters": 2, "conferences": 3, "users": 0})
        mocked = patch.object(system._store, "purge", self.purge)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.cache = Mock(spec=posters.PosterStore)
        mocked = patch.object(posters, "store", return_value=self.cache)
        mocked.start()
        self.addCleanup(mocked.stop)
        app = FastAPI()
        app.include_router(system.router)
        self.client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))

    def post(self, **kwargs):
        return self.client.post("/system/purge", json={"confirm": "purge"}, **kwargs)

    def test_purge_deletes_only_numbered_directories_and_clears_cache(self) -> None:
        with self.assertLogs("routers.system", "WARNING"):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "ok": True, "posters": 2, "conferences": 3, "users": 0, "source_directories": 2,
        })
        self.purge.assert_awaited_once()
        self.cache.clear_cache.assert_called_once()
        self.assertEqual({path.name for path in self.root.iterdir()}, {"keep", "note.txt"})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_only_exact_unambiguous_query_opt_in_deletes_users(self) -> None:
        for query in ("", "?users=no", "?users=true", "?users=YES", "?users=", "?users=yes&users=no"):
            with self.subTest(query=query), self.assertLogs("routers.system", "WARNING"):
                response = self.client.post("/system/purge" + query, json={"confirm": "purge", "users": "yes"})
                self.assertEqual(response.status_code, 200)
                self.purge.assert_awaited_with(users=False)
        with self.assertLogs("routers.system", "WARNING"):
            response = self.client.post("/system/purge?users=yes", json={"confirm": "purge"})
        self.assertEqual(response.status_code, 200)
        self.purge.assert_awaited_with(users=True)

    def test_is_post_only_and_requires_explicit_opt_in(self) -> None:
        self.assertEqual(self.client.get("/system/purge").status_code, 405)
        for value in ("", "false", "TRUE", "1"):
            with patch.dict(os.environ, {"SYSTEM_PURGE_ENABLED": value}):
                self.assertEqual(self.post().status_code, 403)
        self.purge.assert_not_awaited()
        self.assertTrue((self.root / "0" / "upload").exists())

    def test_rejects_nonlocal_clients_even_with_localhost_host(self) -> None:
        for host in ("192.168.1.1", "203.0.113.1", "::ffff:192.168.1.1", "testclient"):
            client = TestClient(self.client.app, base_url="http://localhost", client=(host, 50000))
            self.assertEqual(client.post("/system/purge", json={"confirm": "purge"}).status_code, 403)
        self.purge.assert_not_awaited()

    def test_rejects_nonlocal_or_duplicate_hosts(self) -> None:
        for host in ("example.com", "localhost.evil.test", "0.0.0.0", "127.0.0.2", "[invalid"):
            self.assertEqual(self.post(headers={"Host": host}).status_code, 403)
        self.assertEqual(self.post(headers=[("Host", "localhost"), ("Host", "example.com")]).status_code, 403)
        self.purge.assert_not_awaited()

    def test_all_direct_loopback_variants_are_allowed(self) -> None:
        for host, url in (("127.0.0.1", "http://127.0.0.1:8000"), ("::1", "http://[::1]:8000")):
            client = TestClient(self.client.app, base_url=url, client=(host, 50000))
            with self.assertLogs("routers.system", "WARNING"):
                self.assertEqual(client.post("/system/purge", json={"confirm": "purge"}).status_code, 200)

    def test_rejects_forwarded_headers_and_cross_origin_requests(self) -> None:
        for name in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Forwarded-Port"):
            self.assertEqual(self.post(headers={name: "127.0.0.1"}).status_code, 403)
        self.assertEqual(self.post(headers={"Origin": "https://example.com"}).status_code, 403)
        self.purge.assert_not_awaited()

    def test_requires_json_and_exact_confirmation(self) -> None:
        self.assertEqual(self.client.post("/system/purge", data="confirm=purge").status_code, 415)
        for data in ({}, {"confirm": True}, {"confirm": "PURGE"}):
            self.assertEqual(self.client.post("/system/purge", json=data).status_code, 422)
        self.purge.assert_not_awaited()

    def test_cloud_storage_is_rejected_before_database_changes(self) -> None:
        with patch.dict(os.environ, {"GCS_BUCKET": "example-bucket"}):
            self.assertEqual(self.post().status_code, 409)
        self.purge.assert_not_awaited()

    def test_unsafe_source_roots_are_rejected(self) -> None:
        for root in (Path("/"), Path.home(), Path(__file__).resolve().parent.parent):
            with patch.dict(os.environ, {"SOURCES_DIR": str(root)}):
                self.assertEqual(self.post().status_code, 409)
        self.purge.assert_not_awaited()

    def test_symlink_or_file_shards_prevent_all_changes(self) -> None:
        target = self.root / "keep"
        (self.root / "1").symlink_to(target, target_is_directory=True)
        self.assertEqual(self.post().status_code, 409)
        self.assertTrue(target.is_dir())
        (self.root / "1").unlink()
        (self.root / "1").write_text("not a directory")
        self.assertEqual(self.post().status_code, 409)
        self.purge.assert_not_awaited()
        self.assertTrue((self.root / "0" / "upload").exists())

    def test_symlinks_inside_a_shard_do_not_delete_external_data(self) -> None:
        preserved = self.root / "keep" / "preserved"
        preserved.write_text("keep")
        (self.root / "0" / "link").symlink_to(preserved)
        with self.assertLogs("routers.system", "WARNING"):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(preserved.read_text(), "keep")

    def test_database_failure_leaves_files_untouched(self) -> None:
        self.purge.side_effect = psycopg.OperationalError("offline")
        with self.assertLogs("routers.system", "ERROR"):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn("source directories were not deleted", response.json()["detail"])
        self.assertTrue((self.root / "0" / "upload").exists())
        self.cache.clear_cache.assert_not_called()

    def test_file_failure_explicitly_reports_partial_purge(self) -> None:
        with patch.object(system.shutil, "rmtree", side_effect=PermissionError("denied")):
            with self.assertLogs("routers.system", "ERROR"):
                response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn("database was purged", response.json()["detail"])
        self.purge.assert_awaited_once()
        self.cache.clear_cache.assert_called_once()

    def test_upload_recreates_a_removed_source_shard(self) -> None:
        with self.assertLogs("routers.system", "WARNING"):
            self.assertEqual(self.post().status_code, 200)
        files = content.SourceFiles()
        fileid = uuid4()
        path = files.put(fileid, b"new upload", "image/png")
        self.assertEqual(Path(path).read_bytes(), b"new upload")
        self.assertEqual(Path(path).parent, self.root / str(fileid.int % 10))


class SystemStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_poster_deletion_precedes_conferences_in_one_transaction(self) -> None:
        store = system.SystemStore()
        connection = AsyncMock()
        memory, deleted_posters, deleted_conferences = AsyncMock(), AsyncMock(), AsyncMock()
        memory.fetchone.return_value = (None,)
        deleted_posters.rowcount = 2
        deleted_conferences.rowcount = 3
        connection.execute.side_effect = [AsyncMock(), memory, deleted_posters, deleted_conferences]
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            self.assertEqual(await store.purge(), {"posters": 2, "conferences": 3, "users": 0})
        statements = [call.args[0] for call in connection.execute.await_args_list]
        self.assertEqual(statements[-2:], ["DELETE FROM poster", "DELETE FROM conference"])
        connection.__aenter__.assert_awaited_once()
        connection.__aexit__.assert_awaited_once_with(None, None, None)

    async def test_optional_poster_memory_is_purged_before_posters(self) -> None:
        store = system.SystemStore(schema="isolated")
        connection = AsyncMock()
        memory = AsyncMock()
        memory.fetchone.return_value = ("isolated.poster_memory",)
        deleted = Mock(rowcount=0)
        connection.execute.side_effect = [Mock(), memory, Mock(), deleted, deleted]
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            await store.purge()
        calls = connection.execute.await_args_list
        self.assertEqual(calls[1].args[1], ("isolated.poster_memory",))
        self.assertIn("poster_memory", calls[2].args[0].as_string())
        self.assertEqual(calls[3].args[0], "DELETE FROM poster")

    async def test_transaction_receives_database_failure_for_rollback(self) -> None:
        store = system.SystemStore()
        connection = AsyncMock()
        connection.execute.side_effect = psycopg.OperationalError("offline")
        with patch.object(store, "_connect", AsyncMock(return_value=connection)):
            with self.assertRaises(psycopg.OperationalError):
                await store.purge()
        self.assertIs(connection.__aexit__.await_args.args[0], psycopg.OperationalError)


@unittest.skipUnless(os.environ.get("SYSTEM_TEST_DATABASE_URL"), "Set SYSTEM_TEST_DATABASE_URL for isolated PostgreSQL tests")
class SystemDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = "test_system_" + uuid4().hex
        self.connection = psycopg.connect(os.environ["SYSTEM_TEST_DATABASE_URL"], autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(
            self.connection.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
        )
        self.connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        self.connection.execute((Path(__file__).resolve().parent.parent / "schema/schema.sql").read_text())
        self.connection.execute(
            'INSERT INTO "user" (uid, name, username, email, password, pwd_updated, created, updated) '
            "VALUES (7, 'Ada', 'ada', 'ada@example.test', 'hash', now(), now(), now())",
        )
        self.connection.execute(
            "INSERT INTO conference (cid, name, description, starts, ends, location, administrator, "
            "password, notify, created, updated, hideafter) "
            "VALUES (1, 'Meeting', 'Research', now(), now(), 'Local', '', '', 'never', now(), now(), 0)",
        )
        self.connection.execute(
            "INSERT INTO poster (pid, uid, cid, created) VALUES (1, 7, 1, now())",
        )
        self.connection.execute(
            "INSERT INTO source (pid, kind, size, mimetype) VALUES (1, 'text', 1, 'text/plain')",
        )
        self.connection.execute("INSERT INTO authors (pid, name) VALUES (1, 'Ada')")
        self.connection.execute("INSERT INTO purchase VALUES (1, 7, 10, 'test', now())")
        self.connection.execute(
            "CREATE TABLE poster_memory (pid integer REFERENCES poster(pid), key text, value jsonb)",
        )
        self.connection.execute("INSERT INTO poster_memory VALUES (1, 'test', '{}')")
        self.store = system.SystemStore("SYSTEM_TEST_DATABASE_URL", self.schema)

    def test_catalog_includes_children_and_posters_without_owners(self) -> None:
        self.connection.execute(
            "INSERT INTO source (pid, kind, parent, filename, size, mimetype, width, height) "
            "SELECT 1, 'image', sid, 'figure.png', 12, 'image/png', 450, 450 FROM source WHERE pid = 1",
        )
        self.connection.execute(
            "INSERT INTO source (pid, kind, size, mimetype, width, height) "
            "VALUES (1, 'thumbnail', 12, 'image/png', 640, 480)",
        )
        self.connection.execute("INSERT INTO poster (pid, created) VALUES (2, now())")
        self.connection.execute(
            "INSERT INTO source (pid, kind, size, mimetype) VALUES (2, 'text', 1, 'text/plain')",
        )
        items = asyncio.run(self.store.catalog(1))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].filename, "Untitled source")
        self.assertEqual(items[1].parent, items[0].sid)
        self.assertEqual(items[1].width, 450)
        self.assertEqual(len(asyncio.run(self.store.catalog(2))), 1)
        self.assertIsNone(asyncio.run(self.store.catalog(999)))
        self.connection.execute("DELETE FROM source WHERE pid = 2")
        self.assertEqual(asyncio.run(self.store.catalog(2)), [])

    def test_catalog_graphic_is_scoped_to_poster_and_source(self) -> None:
        with tempfile.NamedTemporaryFile() as upload:
            upload.write(b"image")
            upload.flush()
            sid = self.connection.execute(
                "INSERT INTO source (pid, kind, size, mimetype, storage_uri) "
                "VALUES (1, 'image', 5, 'image/png', %s) RETURNING sid", (upload.name,),
            ).fetchone()[0]
            self.assertEqual(asyncio.run(self.store.catalog_graphic(1, sid)), (b"image", "image/png"))
            self.assertIsNone(asyncio.run(self.store.catalog_graphic(2, sid)))
            self.assertIsNone(asyncio.run(self.store.catalog_graphic(1, 999)))
            text_sid = self.connection.execute(
                "SELECT sid FROM source WHERE kind = 'text'",
            ).fetchone()[0]
            self.assertIsNone(asyncio.run(self.store.catalog_graphic(1, text_sid)))
            thumbnail_sid = self.connection.execute(
                "INSERT INTO source (pid, kind, size, mimetype, storage_uri) "
                "VALUES (1, 'thumbnail', 5, 'image/png', %s) RETURNING sid",
                (upload.name,),
            ).fetchone()[0]
            self.assertIsNone(asyncio.run(self.store.catalog_graphic(1, thumbnail_sid)))

    def test_purge_removes_records_dependents_and_memory_but_preserves_users(self) -> None:
        self.assertEqual(asyncio.run(self.store.purge()), {"posters": 1, "conferences": 1, "users": 0})
        for table in ("poster", "conference", "source", "authors", "purchase", "poster_memory"):
            self.assertEqual(self.connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)),
            ).fetchone()[0], 0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM "user"').fetchone()[0], 1)
        self.assertEqual(asyncio.run(self.store.purge()), {"posters": 0, "conferences": 0, "users": 0})

    def test_user_opt_in_deletes_accounts_and_optional_memory(self) -> None:
        self.connection.execute(
            'CREATE TABLE user_memory (uid integer REFERENCES "user"(uid), key text, value jsonb)',
        )
        self.connection.execute("INSERT INTO user_memory VALUES (7, 'test', '{}')")
        self.assertEqual(asyncio.run(self.store.purge(users=True)), {
            "posters": 1, "conferences": 1, "users": 1,
        })
        self.assertEqual(self.connection.execute('SELECT count(*) FROM "user"').fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM user_memory").fetchone()[0], 0)

    def test_user_delete_failure_rolls_back_entire_purge(self) -> None:
        self.connection.execute('CREATE TABLE protected_user (uid integer REFERENCES "user"(uid))')
        self.connection.execute("INSERT INTO protected_user VALUES (7)")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            asyncio.run(self.store.purge(users=True))
        for table in ("poster", "conference", "source", "poster_memory", "user"):
            self.assertEqual(self.connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)),
            ).fetchone()[0], 1)

    def test_conference_delete_failure_rolls_back_poster_and_dependent_deletions(self) -> None:
        self.connection.execute("CREATE TABLE protected_conference (cid integer REFERENCES conference(cid))")
        self.connection.execute("INSERT INTO protected_conference VALUES (1)")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            asyncio.run(self.store.purge())
        for table in ("poster", "conference", "source", "authors", "purchase", "poster_memory"):
            self.assertEqual(self.connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)),
            ).fetchone()[0], 1)
import asyncio
