import os
import pickle
import unittest
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from memory import current_memory, memory_context
from routers.wikkit import router
from sessions import (
    PostgresSessionMiddleware,
    PostgresSessionStore,
    Session,
    SessionConflictError,
    configured_session_store,
)
from wikkit import Chatbot

SECRET = "session-test-key-not-for-production" * 2


class SessionConfigurationTests(unittest.TestCase):
    def test_auto_and_explicit_database_free_modes(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(configured_session_store())
        with patch.dict(os.environ, {"SESSION_BACKEND": "none"}, clear=True):
            self.assertIsNone(configured_session_store())

    def test_partial_configuration_and_invalid_settings_fail(self) -> None:
        for settings in (
            {"DATABASE_URL": "unused"},
            {"SECRET_KEY": SECRET},
            {"SESSION_BACKEND": "postgres"},
            {"SESSION_BACKEND": "unknown"},
            {"DATABASE_URL": "unused", "SECRET_KEY": "short"},
            {"DATABASE_URL": "unused", "SECRET_KEY": SECRET, "SESSION_MAX_AGE": "0"},
        ):
            with self.subTest(settings=list(settings)), patch.dict(os.environ, settings, clear=True):
                with self.assertRaises((ValueError, RuntimeError)):
                    configured_session_store()

    def test_valid_configuration(self) -> None:
        with patch.dict(os.environ, {
            "DATABASE_URL": "unused", "SECRET_KEY": SECRET,
            "SESSION_MAX_AGE": "1200",
        }, clear=True):
            store = configured_session_store()
        self.assertIsInstance(store, PostgresSessionStore)
        assert store is not None
        self.assertEqual(store.max_age, 1200)

    def test_signature_checked_before_unpickling_and_bound_to_session_id(self) -> None:
        store = PostgresSessionStore(SECRET)
        encoded = store._encode("first", pickle.dumps({"name": "Ada"}))
        for identifier, value in (("second", encoded), ("first", encoded[:-1] + b"x")):
            with patch("sessions.pickle.loads") as loads:
                with self.assertRaisesRegex(ValueError, "integrity"):
                    store._decode(identifier, value)
                loads.assert_not_called()
        self.assertEqual(store._decode("first", encoded)[0], {"name": "Ada"})


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "set TEST_DATABASE_URL for PostgreSQL tests")
class SessionBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = PostgresSessionStore(SECRET, database_env="TEST_DATABASE_URL")
        self.identifiers: set[str] = set()

    def tearDown(self) -> None:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            for identifier in self.identifiers:
                connection.execute(
                    "DELETE FROM public.chatbot_session WHERE session_id = %s",
                    (identifier,),
                )

    def new_session(self) -> Session:
        session = self.store.load(None)
        self.identifiers.add(session.identifier)
        return session

    def client(self, store: PostgresSessionStore | None = None) -> TestClient:
        app = FastAPI(dependencies=[Depends(memory_context, scope="function")])
        app.add_middleware(PostgresSessionMiddleware, store=store or self.store)
        app.include_router(router)

        @app.get("/state")
        def state(request: Request):
            memory = current_memory()
            self.identifiers.add(request.cookies.get("presto_session", ""))
            return dict(memory.short_term_memory)

        @app.post("/remember")
        def remember():
            memory = current_memory()
            memory.short_term_memory["name"] = "Ada"
            return {"saved": True}

        async def cleanup(request: Request) -> AsyncIterator[None]:
            yield
            request.scope["session"]["cleanup"] = True

        @app.get("/cleanup", dependencies=[Depends(cleanup, scope="function")])
        def cleanup_endpoint():
            return {"ok": True}

        return TestClient(app, base_url="https://testserver")

    def track_cookie(self, client: TestClient) -> str:
        identifier = client.cookies["presto_session"]
        self.identifiers.add(identifier)
        return identifier

    def test_http_cookie_roundtrip_and_fresh_backend_instance(self) -> None:
        client = self.client()
        response = client.post("/remember")
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age="):
            self.assertIn(attribute, cookie)
        self.assertNotIn("Ada", cookie)
        identifier = self.track_cookie(client)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Cookie", response.headers["vary"])
        fresh_store = PostgresSessionStore(SECRET, database_env="TEST_DATABASE_URL")
        reconnected = self.client(fresh_store)
        reconnected.cookies.set("presto_session", identifier, domain="testserver.local")
        self.assertEqual(reconnected.get("/state").json(), {"name": "Ada"})

    def test_missing_unknown_and_expired_cookie_get_new_identifier(self) -> None:
        for previous in ("unknown", "A" * 43):
            client = self.client()
            client.cookies.set("presto_session", previous, domain="testserver.local")
            self.assertEqual(client.get("/state").status_code, 200)
            self.assertNotEqual(self.track_cookie(client), previous)
        session = self.new_session()
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.store.save(session)
        renewed = self.store.load(session.identifier)
        self.assertNotEqual(renewed.identifier, session.identifier)
        self.assertEqual(renewed.data, {})
        self.assertEqual(renewed.version, 0)

    def test_function_dependency_cleanup_is_persisted(self) -> None:
        client = self.client()
        self.assertEqual(client.get("/cleanup").status_code, 200)
        identifier = self.track_cookie(client)
        self.assertTrue(self.store.load(identifier).data["cleanup"])

    def test_concurrent_writes_are_rejected_and_read_only_requests_do_not_conflict(self) -> None:
        session = self.new_session()
        self.store.save(session)
        first = self.store.load(session.identifier)
        second = self.store.load(session.identifier)
        first.data["name"] = "Ada"
        self.store.save(first)
        self.store.save(second)  # An unchanged snapshot must not overwrite a writer.
        second.data["name"] = "Grace"
        with self.assertRaises(SessionConflictError):
            self.store.save(second)
        self.assertEqual(self.store.load(session.identifier).data, {"name": "Ada"})

    def test_expired_session_cannot_be_resurrected(self) -> None:
        session = self.new_session()
        self.store.save(session)
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(
                "UPDATE public.chatbot_session SET expires_at = now() - interval '1 second' "
                "WHERE session_id = %s", (session.identifier,),
            )
        session.data["change"] = True
        with self.assertRaises(SessionConflictError):
            self.store.save(session)

    def test_corrupt_database_payload_is_not_silently_replaced(self) -> None:
        session = self.new_session()
        self.store.save(session)
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(
                "UPDATE public.chatbot_session SET data = %s WHERE session_id = %s",
                (b"corrupt", session.identifier),
            )
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.store.load(session.identifier)

    def test_websocket_disconnect_persists_memory_and_reconnect_restores_it(self) -> None:
        client = self.client()
        client.get("/state")
        self.track_cookie(client)

        async def workflow(chatbot: Chatbot, message: str) -> None:
            if message == "remember":
                chatbot.short_term_memory["name"] = "Ada"
            await chatbot.send({"say": chatbot.short_term_memory["name"]})

        with patch.object(Chatbot, "handle_message", workflow):
            with client.websocket_connect("wss://testserver/wikkit/ws") as socket:
                socket.send_text("remember")
                self.assertEqual(socket.receive_json(), {"say": "Ada"})
            with client.websocket_connect("wss://testserver/wikkit/ws") as socket:
                socket.send_text("recall")
                self.assertEqual(socket.receive_json(), {"say": "Ada"})
        self.assertEqual(
            client.get("/state").json(), {"last_user_input": "recall", "name": "Ada"}
        )

    def test_websocket_requires_existing_session_and_rejects_cross_origin(self) -> None:
        client = self.client()
        with self.assertRaises(WebSocketDisconnect) as closed:
            with client.websocket_connect("wss://testserver/wikkit/ws"):
                pass
        self.assertEqual(closed.exception.code, 1008)
        client.get("/state")
        self.track_cookie(client)
        with self.assertRaises(WebSocketDisconnect) as closed:
            with client.websocket_connect(
                "wss://testserver/wikkit/ws", headers={"origin": "https://evil.example"},
            ):
                pass
        self.assertEqual(closed.exception.code, 1008)
        with client.websocket_connect(
            "wss://testserver/wikkit/ws", headers={"origin": "https://testserver"},
        ):
            pass

    def test_failure_to_save_http_session_does_not_send_success(self) -> None:
        client = self.client()
        with patch.object(self.store, "save", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                client.post("/remember")

    def test_concurrent_http_write_returns_conflict_not_success(self) -> None:
        client = self.client()
        client.get("/state")
        self.track_cookie(client)
        with patch.object(self.store, "save", side_effect=SessionConflictError("conflict")):
            response = client.post("/remember")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Reload", response.json()["detail"])

    def test_disconnect_cancellation_saves_memory_after_workflow_teardown(self) -> None:
        client = self.client()
        client.get("/state")
        identifier = self.track_cookie(client)

        async def workflow(chatbot: Chatbot, message: str) -> None:
            try:
                await chatbot.request_response("Waiting")
            finally:
                chatbot.short_term_memory["cancelled"] = True

        with patch.object(Chatbot, "handle_message", workflow):
            with client.websocket_connect("wss://testserver/wikkit/ws") as socket:
                socket.send_text("start")
                self.assertEqual(socket.receive_json()["say"], "Waiting")
        memory = self.store.load(identifier).data["chatbot_memory"]
        self.assertTrue(memory.short_term_memory["cancelled"])

    def test_websocket_save_failure_is_explicit(self) -> None:
        client = self.client()
        client.get("/state")
        self.track_cookie(client)
        with patch.object(self.store, "save", side_effect=SessionConflictError("conflict")):
            with self.assertRaisesRegex(SessionConflictError, "conflict"):
                with client.websocket_connect("wss://testserver/wikkit/ws"):
                    pass


if __name__ == "__main__":
    unittest.main()
