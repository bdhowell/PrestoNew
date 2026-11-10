import asyncio
import os
import pickle
import time
import unittest
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from redis import Redis
from starlette.websockets import WebSocketDisconnect

from identity import identity_context
from memory import current_memory, memory_context
from routers import system, wikkit
from sessions import (
    LOCAL_SESSION_COOKIE_NAME, SESSION_COOKIE_NAME, RedisSessionMiddleware,
    RedisSessionStore, Session, SessionConflictError, configured_session_store,
)
from wikkit import Chatbot

SECRET = "session-test-key-not-for-production" * 2


def run_async(awaitable):
    return asyncio.run(awaitable)


class SessionConfigurationTests(unittest.TestCase):
    def test_auto_and_explicit_disabled_modes(self) -> None:
        for settings in ({}, {"DATABASE_URL": "unused"}, {"SESSION_BACKEND": "none"}):
            with patch.dict(os.environ, settings, clear=True):
                self.assertIsNone(configured_session_store())

    def test_invalid_configuration_fails(self) -> None:
        for settings in (
            {"REDIS_URL": "redis://localhost"},
            {"SESSION_BACKEND": "redis"},
            {"SESSION_BACKEND": "postgres"},
            {"SESSION_BACKEND": "unknown"},
            {"SECRET_KEY": "short"},
            {"SECRET_KEY": SECRET, "SESSION_MAX_AGE": "0"},
        ):
            with self.subTest(settings=list(settings)), patch.dict(os.environ, settings, clear=True):
                with self.assertRaises((ValueError, RuntimeError)):
                    configured_session_store()

    def test_valid_configuration_and_local_default(self) -> None:
        with patch.dict(os.environ, {
            "REDIS_URL": "redis://localhost:6380/2", "SECRET_KEY": SECRET,
            "SESSION_MAX_AGE": "1200", "SESSION_BACKEND": "redis",
        }, clear=True):
            store = configured_session_store()
        self.assertIsInstance(store, RedisSessionStore)
        self.assertEqual(store.max_age, 1200)
        self.assertEqual(store.redis_url, "redis://localhost:6380/2")
        with patch.dict(os.environ, {"SECRET_KEY": SECRET}, clear=True):
            self.assertEqual(configured_session_store().redis_url, "redis://localhost:6379/0")

    def test_signature_is_checked_before_unpickling_and_bound_to_identifier(self) -> None:
        store = RedisSessionStore(SECRET)
        encoded = store._encode("first", pickle.dumps({"name": "Ada"}))
        for identifier, value in (("second", encoded), ("first", encoded[:-1] + b"x")):
            with patch("sessions.pickle.loads") as loads:
                with self.assertRaisesRegex(ValueError, "integrity"):
                    store._decode(identifier, value)
                loads.assert_not_called()
        self.assertEqual(store._decode("first", encoded)[0], {"name": "Ada"})


class SessionMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RedisSessionStore(SECRET)
        self.session = Session(
            "A" * 43, {}, 1, datetime.now(timezone.utc) + timedelta(minutes=10), b"",
        )
        self.store.load = AsyncMock(return_value=self.session)
        self.store.save = AsyncMock()
        self.store.delete = AsyncMock()
        app = FastAPI(dependencies=[Depends(identity_context)])
        app.add_middleware(RedisSessionMiddleware, store=self.store)
        app.include_router(system.router)
        app.include_router(wikkit.router)

        @app.get("/state")
        def state():
            return {"ok": True}

        self.client = TestClient(app, base_url="https://testserver")

    def test_cookies_and_renewal_on_normal_requests(self) -> None:
        response = self.client.get("/state")
        self.store.save.assert_awaited_once_with(self.session, renew=True)
        cookie = response.headers["set-cookie"]
        self.assertTrue(cookie.startswith(f"{SESSION_COOKIE_NAME}="))
        for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age="):
            self.assertIn(attribute, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Cookie", response.headers["vary"])

    def test_local_cookie_has_no_host_prefix(self) -> None:
        middleware = RedisSessionMiddleware(FastAPI(), store=None, secure=False)
        cookie = middleware._cookie(self.session)
        self.assertTrue(cookie.startswith(f"{LOCAL_SESSION_COOKIE_NAME}="))
        self.assertNotIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_timecheck_is_read_only_and_returns_seconds_name_and_timestamp(self) -> None:
        self.client.cookies.set("presto_user", "Ada")
        for _ in range(3):
            response = self.client.get("/system/timecheck")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {"remaining_seconds", "expires_at", "name"})
            self.assertGreater(response.json()["remaining_seconds"], 590)
            self.assertLessEqual(response.json()["remaining_seconds"], 600)
            self.assertEqual(response.json()["expires_at"], self.session.expires_at.timestamp())
            self.assertEqual(response.json()["name"], "Ada")
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.store.save.assert_not_awaited()

    def test_expired_unknown_or_missing_session_redirects_without_saving(self) -> None:
        for version, expiry in (
            (0, datetime.now(timezone.utc) + timedelta(minutes=10)),
            (1, datetime.now(timezone.utc) - timedelta(seconds=1)),
        ):
            self.session.version, self.session.expires_at = version, expiry
            response = self.client.get("/system/timecheck", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/system/loggedout/")
            self.assertNotIn("set-cookie", response.headers)
        self.store.save.assert_not_awaited()

    def test_loggedout_page_personalizes_and_clears_cookies_without_loading_or_renewing(self) -> None:
        for path in ("/system/loggedout", "/system/loggedout/"):
            self.client.cookies.set("presto_user", "Ada%20%3Cscript%3E")
            self.client.cookies.set(SESSION_COOKIE_NAME, self.session.identifier)
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Ada \\u0026lt;script\\u0026gt;", response.text)
            self.assertIn("logged out because your session expired", response.text)
            self.assertIn('data-wikkit-offline="true"', response.text)
            self.assertNotIn('src="/static/javascript/sessions.js"', response.text)
            self.assertIn("Max-Age=0", response.headers["set-cookie"])
            self.assertIn("/account/signin", response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.store.load.assert_not_awaited()
        self.store.save.assert_not_awaited()
        self.store.delete.assert_awaited_with(self.session.identifier)

    def test_disabled_backend_is_explicit(self) -> None:
        app = FastAPI()
        app.include_router(system.router)
        self.assertEqual(TestClient(app).get("/system/timecheck").status_code, 503)

    def test_regular_page_installs_session_monitor(self) -> None:
        response = self.client.get("/wikkit/pricing")
        self.assertIn('src="/static/javascript/sessions.js" defer', response.text)

    def test_failure_does_not_send_success_and_concurrent_write_returns_conflict(self) -> None:
        self.store.save.side_effect = RuntimeError("Redis unavailable")
        with self.assertRaisesRegex(RuntimeError, "Redis unavailable"):
            self.client.get("/state")
        self.store.save.side_effect = SessionConflictError("conflict")
        with self.assertLogs("sessions", "WARNING"):
            self.assertEqual(self.client.get("/state").status_code, 409)

    def test_other_response_cookies_are_preserved(self) -> None:
        response = self.client.get("/system/loggedout")
        self.assertEqual(len(response.headers.get_list("set-cookie")), 2)


@unittest.skipUnless(os.environ.get("TEST_REDIS_URL"), "set TEST_REDIS_URL for Redis tests")
class SessionBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url = os.environ["TEST_REDIS_URL"]
        self.store = RedisSessionStore(SECRET, redis_url=self.url)
        self.redis = Redis.from_url(self.url)
        self.identifiers: set[str] = set()

    def tearDown(self) -> None:
        for identifier in self.identifiers:
            self.redis.delete(self.store._key(identifier))
        self.redis.close()

    def new_session(self) -> Session:
        session = run_async(self.store.load(None))
        self.identifiers.add(session.identifier)
        return session

    def client(self, store: RedisSessionStore | None = None) -> TestClient:
        app = FastAPI(dependencies=[
            Depends(identity_context), Depends(memory_context, scope="function"),
        ])
        app.add_middleware(RedisSessionMiddleware, store=store or self.store)
        app.include_router(wikkit.router)
        app.include_router(system.router)

        @app.get("/state")
        def state(request: Request):
            self.identifiers.add(request.scope["server_session"].identifier)
            return dict(current_memory().short_term_memory)

        @app.post("/remember")
        def remember(request: Request):
            self.identifiers.add(request.scope["server_session"].identifier)
            current_memory().short_term_memory["name"] = "Ada"
            return {"saved": True}

        async def cleanup(request: Request) -> AsyncIterator[None]:
            yield
            request.scope["session"]["cleanup"] = True

        @app.get("/cleanup", dependencies=[Depends(cleanup, scope="function")])
        def cleanup_endpoint(request: Request):
            self.identifiers.add(request.scope["server_session"].identifier)
            return {"ok": True}

        return TestClient(app, base_url="https://testserver")

    def test_roundtrip_ttl_and_fresh_store_instance(self) -> None:
        client = self.client()
        self.assertEqual(client.post("/remember").status_code, 200)
        identifier = client.cookies[SESSION_COOKIE_NAME]
        self.assertGreater(self.redis.pttl(self.store._key(identifier)), 590000)
        self.assertLessEqual(self.redis.pttl(self.store._key(identifier)), 600000)
        fresh = self.client(RedisSessionStore(SECRET, redis_url=self.url))
        fresh.cookies.set(SESSION_COOKIE_NAME, identifier)
        self.assertEqual(fresh.get("/state").json(), {"name": "Ada"})

    def test_missing_unknown_malformed_and_expired_sessions(self) -> None:
        for identifier in (None, "invalid", "A" * 43):
            session = run_async(self.store.load(identifier))
            self.assertNotEqual(session.identifier, identifier)
            self.assertEqual((session.data, session.version), ({}, 0))
        session = self.new_session()
        run_async(self.store.save(session))
        self.redis.pexpire(self.store._key(session.identifier), 1)
        time.sleep(0.01)
        self.assertNotEqual(run_async(self.store.load(session.identifier)).identifier, session.identifier)
        session.data["new"] = True
        with self.assertRaises(SessionConflictError):
            run_async(self.store.save(session, renew=True))

    def test_readonly_poll_does_not_extend_ttl_but_normal_activity_does(self) -> None:
        client = self.client()
        client.get("/state")
        identifier = client.cookies[SESSION_COOKIE_NAME]
        before = run_async(self.store.load(identifier))
        key = self.store._key(identifier)
        self.redis.pexpire(key, 30000)
        for _ in range(3):
            response = client.get("/system/timecheck")
            self.assertEqual(response.status_code, 200)
            self.assertGreater(response.json()["remaining_seconds"], 29)
            self.assertLessEqual(response.json()["remaining_seconds"], 30)
        self.assertLessEqual(self.redis.pttl(key), 30000)
        self.assertEqual(client.get("/state").status_code, 200)
        after = run_async(self.store.load(identifier))
        self.assertEqual(after.version, before.version)
        self.assertGreater(self.redis.pttl(key), 590000)

    def test_concurrent_writes_rejected_and_readonly_renewal_preserves_newer_data(self) -> None:
        session = self.new_session()
        run_async(self.store.save(session))
        first = run_async(self.store.load(session.identifier))
        second = run_async(self.store.load(session.identifier))
        first.data["name"] = "Ada"
        run_async(self.store.save(first))
        run_async(self.store.save(second, renew=True))
        self.assertEqual(second.version, 1)
        second.data["name"] = "Grace"
        with self.assertRaises(SessionConflictError):
            run_async(self.store.save(second))
        self.assertEqual(run_async(self.store.load(session.identifier)).data, {"name": "Ada"})

    def test_expiry_never_shortens_and_stale_socket_can_save_after_http_renewal(self) -> None:
        session = self.new_session()
        session.expires_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        run_async(self.store.save(session))
        newer = run_async(self.store.load(session.identifier))
        run_async(self.store.save(newer, renew=True))
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.data["socket"] = True
        run_async(self.store.save(session))
        self.assertGreater(self.redis.pttl(self.store._key(session.identifier)), 590000)

    def test_corrupt_payload_is_not_silently_replaced(self) -> None:
        session = self.new_session()
        run_async(self.store.save(session))
        self.redis.hset(self.store._key(session.identifier), "data", b"corrupt")
        with self.assertRaisesRegex(ValueError, "integrity"):
            run_async(self.store.load(session.identifier))

    def test_function_dependency_cleanup_is_persisted(self) -> None:
        client = self.client()
        self.assertEqual(client.get("/cleanup").status_code, 200)
        self.assertTrue(run_async(self.store.load(client.cookies[SESSION_COOKIE_NAME])).data["cleanup"])

    def test_expired_timecheck_redirect_and_loggedout_revocation(self) -> None:
        client = self.client()
        client.get("/state")
        identifier = client.cookies[SESSION_COOKIE_NAME]
        response = client.get("/system/loggedout")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.redis.exists(self.store._key(identifier)))
        self.assertEqual(client.get("/system/timecheck", follow_redirects=False).status_code, 303)
        self.assertNotIn(SESSION_COOKIE_NAME, client.cookies)

    def test_expired_timecheck_does_not_create_a_replacement_session(self) -> None:
        client = self.client()
        client.get("/state")
        identifier = client.cookies[SESSION_COOKIE_NAME]
        self.redis.delete(self.store._key(identifier))
        response = client.get("/system/timecheck", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotEqual(run_async(self.store.load(identifier)).identifier, identifier)

    def test_websocket_requires_session_and_rejects_cross_origin(self) -> None:
        client = self.client()
        for headers in ({}, {"origin": "https://evil.example"}):
            with self.assertRaises(WebSocketDisconnect) as closed:
                with client.websocket_connect("wss://testserver/wikkit/ws", headers=headers):
                    pass
            self.assertEqual(closed.exception.code, 1008)
        client.get("/state")
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("wss://testserver/wikkit/ws", headers={"origin": "https://evil.example"}):
                pass
        with client.websocket_connect("wss://testserver/wikkit/ws", headers={"origin": "https://testserver"}):
            pass

    def test_loggedout_socket_cannot_bypass_origin_validation_or_revoke_session(self) -> None:
        client = self.client()
        client.get("/state")
        identifier = client.cookies[SESSION_COOKIE_NAME]
        with self.assertRaises(WebSocketDisconnect) as closed:
            with client.websocket_connect(
                "wss://testserver/system/loggedout", headers={"origin": "https://evil.example"},
            ):
                pass
        self.assertEqual(closed.exception.code, 1008)
        self.assertTrue(self.redis.exists(self.store._key(identifier)))

    def test_socket_memory_survives_disconnect_and_reconnect(self) -> None:
        client = self.client()
        client.get("/state")

        async def workflow(bot: Chatbot, message: str) -> None:
            if message == "remember":
                bot.short_term_memory["name"] = "Ada"
            await bot.send({"say": bot.short_term_memory["name"]})

        with patch.object(Chatbot, "handle_message", workflow):
            for message in ("remember", "recall"):
                with client.websocket_connect("wss://testserver/wikkit/ws") as socket:
                    self.assertEqual(socket.receive_json()["command"]["does"], "configure")
                    socket.send_text(message)
                    self.assertEqual(socket.receive_json(), {"say": "Ada"})
        self.assertEqual(client.get("/state").json(), {"last_user_input": "recall", "name": "Ada"})

    def test_disconnect_cancellation_saves_after_workflow_teardown(self) -> None:
        client = self.client()
        client.get("/state")

        async def workflow(bot: Chatbot, message: str) -> None:
            try:
                await bot.request_response("Waiting")
            finally:
                bot.short_term_memory["cancelled"] = True

        with patch.object(Chatbot, "handle_message", workflow):
            with client.websocket_connect("wss://testserver/wikkit/ws") as socket:
                socket.receive_json()
                socket.send_text("start")
                self.assertEqual(socket.receive_json()["say"], "Waiting")
        memory = run_async(self.store.load(client.cookies[SESSION_COOKIE_NAME])).data["chatbot_memory"]
        self.assertTrue(memory.short_term_memory["cancelled"])

    def test_websocket_save_failure_is_explicit(self) -> None:
        client = self.client()
        client.get("/state")
        with patch.object(self.store, "save", side_effect=SessionConflictError("conflict")):
            with self.assertRaisesRegex(SessionConflictError, "conflict"):
                with client.websocket_connect("wss://testserver/wikkit/ws"):
                    pass


if __name__ == "__main__":
    unittest.main()
