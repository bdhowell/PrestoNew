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

import content
import posters
from routers import system


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
