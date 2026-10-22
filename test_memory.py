import asyncio
import os
import pickle
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from psycopg import sql
from starlette.types import Receive, Scope, Send

from kvstore import KVStore
from memory import Memory, current_memory, memory_context
from routers.wikkit import router
from wikkit import Chatbot


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL for PostgreSQL tests")
class KVStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = f"memory_test_{uuid.uuid4().hex}"
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            connection.execute(sql.SQL(
                "CREATE TABLE {} (pid BIGINT PRIMARY KEY);"
                "CREATE TABLE {} (uid UUID PRIMARY KEY)"
            ).format(
                sql.Identifier(self.schema, "poster"),
                sql.Identifier(self.schema, "user"),
            ))
            connection.execute(sql.SQL("INSERT INTO {} VALUES (1), (2)").format(
                sql.Identifier(self.schema, "poster")
            ))
        self.addCleanup(self.drop_schema)

    def drop_schema(self) -> None:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def store(
        self, table: str = "memory", parent: str | None = None,
        *, foreign_key_value: object = None,
    ) -> KVStore:
        store = KVStore(
            table, parent, schema=self.schema, database_env="TEST_DATABASE_URL",
            foreign_key_value=foreign_key_value,
        )
        asyncio.run(store.initialize())
        return store

    def test_unowned_json_roundtrip_upsert_missing_and_pickle(self) -> None:
        store = self.store()
        for value in [None, False, 42, "hello", ["nested", {"name": "Ada"}]]:
            asyncio.run(store.set("value", value))
            self.assertEqual(asyncio.run(store.get("value")), (value, None))
        with self.assertRaises(KeyError):
            asyncio.run(store.get("missing"))
        restored = pickle.loads(pickle.dumps(store))
        asyncio.run(restored.set("value", {"restored": True}))
        self.assertEqual(asyncio.run(store.get("value")), ({"restored": True}, None))
        with self.assertRaises(TypeError):
            asyncio.run(store.set("bad", {1, 2}))
        with self.assertRaises(TypeError):
            asyncio.run(store.set("value", 1, pid=1))

    def test_foreign_key_discovery_scope_isolation_and_unowned_values(self) -> None:
        store = self.store(parent="poster")
        self.assertEqual((store.foreign_key_name, store.foreign_key_type), ("pid", "bigint"))
        asyncio.run(store.set("name", "first", 1))
        asyncio.run(store.set("name", "second", pid=2))
        asyncio.run(store.set("name", "unowned"))
        asyncio.run(store.set("name", "updated", pid=1))
        self.assertEqual(asyncio.run(store.get("name", pid=1)), ("updated", 1))
        self.assertEqual(asyncio.run(store.get("name", 2)), ("second", 2))
        self.assertEqual(asyncio.run(store.get("name")), ("unowned", None))
        rediscovered = self.store()
        self.assertEqual(rediscovered.foreign_key_name, "pid")
        self.assertEqual(asyncio.run(rediscovered.get("name", pid=2)), ("second", 2))
        bound = self.store(foreign_key_value=1)
        self.assertEqual(asyncio.run(bound.get("name")), ("updated", 1))
        for operation in (bound.get, lambda key, **kw: bound.set(key, "leak", **kw)):
            with self.assertRaises(ValueError):
                asyncio.run(operation("name", pid=2))
            with self.assertRaises(ValueError):
                asyncio.run(operation("name", pid=None))
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            asyncio.run(store.set("name", "invalid", pid=999))
        self.assertEqual(asyncio.run(store.get("name", pid=1)), ("updated", 1))
        with self.assertRaises(TypeError):
            asyncio.run(store.set("name", "invalid", uid=1))
        with self.assertRaises(TypeError):
            asyncio.run(store.set("name", "invalid", 1, pid=1))

    def test_uuid_parent_and_quoted_identifiers(self) -> None:
        owner = uuid.uuid4()
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(sql.SQL("INSERT INTO {} VALUES (%s)").format(
                sql.Identifier(self.schema, "user")
            ), (owner,))
        store = self.store('quoted " memory', "user", foreign_key_value=owner)
        self.assertEqual(store.foreign_key_type, "uuid")
        asyncio.run(store.set("key'; DROP TABLE poster; --", "safe"))
        self.assertEqual(
            asyncio.run(store.get("key'; DROP TABLE poster; --")), ("safe", owner)
        )

    def test_dynamic_keyword_uses_actual_parent_column_and_type_modifier(self) -> None:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(sql.SQL(
                'CREATE TABLE {} ("Owner Name" VARCHAR(20) PRIMARY KEY);'
                """INSERT INTO {} VALUES ('Ada')"""
            ).format(
                sql.Identifier(self.schema, "named"),
                sql.Identifier(self.schema, "named"),
            ))
        store = self.store("named_memory", "named")
        self.assertEqual(store.foreign_key_name, "Owner Name")
        self.assertEqual(store.foreign_key_type, "character varying(20)")
        asyncio.run(store.set("title", "Hello", **{"Owner Name": "Ada"}))
        self.assertEqual(
            asyncio.run(store.get("title", **{"Owner Name": "Ada"})), ("Hello", "Ada")
        )

    def test_invalid_parent_and_existing_tables_fail_explicitly(self) -> None:
        self.store("unowned")
        with self.assertRaisesRegex(ValueError, "no foreign key"):
            self.store("unowned", "poster")
        with self.assertRaisesRegex(ValueError, "single-column"):
            self.store("missing_parent", "missing")
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(sql.SQL(
                "CREATE TABLE {} (a INT, b INT, PRIMARY KEY (a, b));"
                "CREATE TABLE {} (key TEXT PRIMARY KEY, value TEXT)"
            ).format(
                sql.Identifier(self.schema, "composite"),
                sql.Identifier(self.schema, "bad"),
            ))
        with self.assertRaisesRegex(ValueError, "single-column"):
            self.store("new", "composite")
        with self.assertRaisesRegex(ValueError, "JSONB"):
            self.store("bad")
        self.store("owned", "poster")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.store("owned", "user")
        with self.assertRaisesRegex(ValueError, "without a foreign key"):
            self.store("unowned", foreign_key_value=1)

    def test_existing_foreign_key_must_reference_matching_single_primary_key(self) -> None:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(sql.SQL(
                "CREATE TABLE {} (id INT PRIMARY KEY, other INT UNIQUE);"
                "CREATE TABLE {} (key TEXT NOT NULL, value JSONB NOT NULL, "
                "owner INT REFERENCES {} (other));"
                "CREATE TABLE {} (key TEXT NOT NULL, value JSONB NOT NULL, "
                "pid INTEGER REFERENCES {} (pid))"
            ).format(
                sql.Identifier(self.schema, "parent"),
                sql.Identifier(self.schema, "nonprimary"),
                sql.Identifier(self.schema, "parent"),
                sql.Identifier(self.schema, "wrong_type"),
                sql.Identifier(self.schema, "poster"),
            ))
        for table in ("nonprimary", "wrong_type"):
            with self.subTest(table=table), self.assertRaisesRegex(ValueError, "primary key type"):
                self.store(table)

    def test_concurrent_creation_and_upsert(self) -> None:
        def write(value: int) -> None:
            store = self.store("concurrent", "poster", foreign_key_value=1)
            asyncio.run(store.set("key", value))

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(12)))
        self.assertIn(
            asyncio.run(self.store("concurrent").get("key", pid=1))[0], range(12)
        )
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            count = connection.execute(sql.SQL("SELECT count(*) FROM {}").format(
                sql.Identifier(self.schema, "concurrent")
            )).fetchone()
        self.assertEqual(count, (1,))

    def test_memory_container_reconnects_after_pickle(self) -> None:
        memory = Memory(
            short_term_memory={"step": "title"},
            long_term_memory=self.store("poster_memory", "poster", foreign_key_value=1),
            poster_id=1,
        )
        restored = pickle.loads(pickle.dumps(memory))
        self.assertEqual(restored.short_term_memory, {"step": "title"})
        self.assertIsNotNone(restored.long_term_memory)
        assert restored.long_term_memory is not None
        asyncio.run(restored.long_term_memory.set("title", "My poster"))
        self.assertEqual(
            asyncio.run(self.store("poster_memory").get("title", pid=1)),
            ("My poster", 1),
        )


class MemoryContextTests(unittest.TestCase):
    def make_client(
        self, session: dict[str, object] | None = None, user_id: object = None,
        poster_id: object = None,
    ) -> TestClient:
        app = FastAPI(dependencies=[Depends(memory_context)])

        @app.middleware("http")
        async def trusted_state(request: Request, call_next):
            if session is not None:
                request.scope["session"] = session
            request.state.user_id = user_id
            request.state.poster_id = poster_id
            return await call_next(request)

        @app.get("/")
        def endpoint(request: Request):
            memory = current_memory()
            self.assertIs(memory, request.state.memory)
            self.assertIs(memory.short_term_memory, request.state.short_term_memory)
            self.assertIs(memory.long_term_memory, request.state.long_term_memory)
            self.assertIs(memory.user_memory, request.state.user_memory)
            count = memory.short_term_memory.get("count", 0)
            assert isinstance(count, int)
            memory.short_term_memory["count"] = count + 1
            return {"count": count + 1}

        return TestClient(app)

    def test_server_session_retains_pickleable_memory(self) -> None:
        session: dict[str, object] = {}
        client = self.make_client(session)
        self.assertEqual(client.get("/").json(), {"count": 1})
        self.assertEqual(client.get("/").json(), {"count": 2})
        restored = pickle.loads(pickle.dumps(session))
        self.assertEqual(self.make_client(restored).get("/").json(), {"count": 3})
        with self.assertRaises(LookupError):
            current_memory()

    def test_anonymous_without_session_is_request_local_and_ignores_client_ids(self) -> None:
        client = self.make_client()
        for _ in range(2):
            self.assertEqual(client.get("/?user_id=1&poster_id=2").json(), {"count": 1})

    def test_trusted_ids_bind_scopes_and_identity_change_clears_context(self) -> None:
        session: dict[str, object] = {"chatbot_memory": Memory(short_term_memory={"count": 99})}
        with patch("memory.KVStore", return_value=None) as store:
            response = self.make_client(session, user_id=7, poster_id=9).get("/")
        self.assertEqual(response.json(), {"count": 1})
        self.assertEqual(store.call_count, 2)
        store.assert_any_call("poster_memory", "poster", foreign_key_value=9)
        store.assert_any_call("user_memory", "user", foreign_key_value=7)
        self.assertEqual(self.make_client(session).get("/").json(), {"count": 1})

    def test_invalid_identity_and_corrupt_session_are_errors(self) -> None:
        for value in ("1", True, 0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.make_client(user_id=value).get("/")
        with self.assertRaises(TypeError):
            self.make_client({"chatbot_memory": {}}).get("/")

    def test_database_configuration_failure_is_not_hidden(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                asyncio.run(KVStore("user_memory").get("missing"))

    def test_websocket_workflow_async_context_and_session_reuse(self) -> None:
        app = FastAPI(dependencies=[Depends(memory_context)])
        app.include_router(router)
        session: dict[str, object] = {}

        async def trusted_session(scope: Scope, receive: Receive, send: Send) -> None:
            scope["session"] = session
            await app(scope, receive, send)

        def handler_memory(chatbot: Chatbot) -> None:
            self.assertIs(current_memory(), chatbot.memory)
            self.assertEqual(chatbot.short_term_memory["last_user_input"], "start")

        async def workflow(chatbot: Chatbot, message: str) -> None:
            handler_memory(chatbot)
            answer = await chatbot.request_response("Name?")
            self.assertEqual(chatbot.short_term_memory["last_user_input"], answer)
            chatbot.short_term_memory["name"] = answer
            await chatbot.send({"say": answer})

        client = TestClient(trusted_session)
        with patch.object(Chatbot, "handle_message", workflow):
            with client.websocket_connect("/wikkit/ws") as socket:
                socket.send_text("start")
                self.assertEqual(socket.receive_json()["say"], "Name?")
                socket.send_text("Ada")
                self.assertEqual(socket.receive_json(), {"say": "Ada"})
        memory = session["chatbot_memory"]
        self.assertIsInstance(memory, Memory)
        assert isinstance(memory, Memory)
        self.assertEqual(memory.short_term_memory["name"], "Ada")
        self.assertIsNone(memory.long_term_memory)
        self.assertIsNone(memory.user_memory)
        self.assertEqual(self.make_client(session).get("/").json(), {"count": 1})
        self.assertEqual(memory.short_term_memory["name"], "Ada")


if __name__ == "__main__":
    unittest.main()
