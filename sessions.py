"""PostgreSQL server-side sessions; cookies contain only a random identifier."""

import hmac
import logging
import os
import pickle
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie

import psycopg
from anyio import CancelScope
from fastapi.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class SessionConflictError(RuntimeError):
    """A concurrent request changed or expired this session."""


@dataclass
class Session:
    identifier: str
    data: dict[str, object]
    version: int
    expires_at: datetime
    original: bytes = field(repr=False)


class PostgresSessionStore:
    def __init__(
        self, secret_key: str, *, max_age: int = 600,
        database_env: str = "DATABASE_URL",
    ) -> None:
        if len(secret_key.encode()) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 bytes")
        if max_age <= 0:
            raise ValueError("SESSION_MAX_AGE must be positive")
        self._secret = secret_key.encode()
        self.max_age = max_age
        self.database_env = database_env
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def _connect(self) -> psycopg.Connection:
        dsn = os.environ.get(self.database_env)
        if not dsn:
            raise RuntimeError(f"{self.database_env} is required for PostgreSQL sessions")
        return psycopg.connect(dsn)

    def _ensure_table(self) -> None:
        with self._initialize_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('presto:public.chatbot_session'))"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.chatbot_session (
                        session_id TEXT PRIMARY KEY,
                        data BYTEA NOT NULL,
                        version BIGINT NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS chatbot_session_expires_at "
                    "ON public.chatbot_session (expires_at)"
                )
            self._initialized = True

    def _encode(self, identifier: str, payload: bytes) -> bytes:
        signature = hmac.digest(self._secret, identifier.encode() + payload, "sha256")
        return signature + payload

    def _decode(self, identifier: str, encoded: bytes) -> tuple[dict[str, object], bytes]:
        signature, payload = encoded[:32], encoded[32:]
        expected = hmac.digest(self._secret, identifier.encode() + payload, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise ValueError("session integrity check failed; refusing to unpickle")
        data = pickle.loads(payload)
        if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
            raise TypeError("persisted session must be a dictionary with string keys")
        return data, payload

    def load(self, identifier: str | None) -> Session:
        self._ensure_table()
        with self._connect() as connection:
            connection.execute("DELETE FROM public.chatbot_session WHERE expires_at <= now()")
            if identifier is not None and _SESSION_ID.fullmatch(identifier):
                row = connection.execute(
                    "SELECT data, version, expires_at FROM public.chatbot_session "
                    "WHERE session_id = %s AND expires_at > now()",
                    (identifier,),
                ).fetchone()
                if row is not None:
                    data, original = self._decode(identifier, bytes(row[0]))
                    return Session(identifier, data, row[1], row[2], original)
        return Session(
            secrets.token_urlsafe(32), {}, 0,
            datetime.now(timezone.utc) + timedelta(seconds=self.max_age),
            pickle.dumps({}, protocol=pickle.HIGHEST_PROTOCOL),
        )

    def save(self, session: Session) -> None:
        payload = pickle.dumps(session.data, protocol=pickle.HIGHEST_PROTOCOL)
        if session.version and payload == session.original:
            return
        encoded = self._encode(session.identifier, payload)
        with self._connect() as connection:
            if session.version == 0:
                row = connection.execute(
                    "INSERT INTO public.chatbot_session (session_id, data, version, expires_at) "
                    "VALUES (%s, %s, 1, %s) RETURNING version",
                    (session.identifier, encoded, session.expires_at),
                ).fetchone()
            else:
                row = connection.execute(
                    "UPDATE public.chatbot_session SET data = %s, version = version + 1 "
                    "WHERE session_id = %s AND version = %s AND expires_at > now() "
                    "RETURNING version",
                    (encoded, session.identifier, session.version),
                ).fetchone()
            if row is None:
                raise SessionConflictError("session changed or expired; retry with a fresh request")
        session.version = row[0]
        session.original = payload


def configured_session_store() -> PostgresSessionStore | None:
    backend = os.environ.get("SESSION_BACKEND", "auto")
    if backend not in ("auto", "none", "postgres"):
        raise ValueError("SESSION_BACKEND must be auto, postgres, or none")
    if backend == "none":
        logger.warning("Server-side sessions explicitly disabled")
        return None
    database = os.environ.get("DATABASE_URL")
    secret = os.environ.get("SECRET_KEY")
    if backend == "auto" and not database and not secret:
        logger.warning("Server-side sessions disabled: DATABASE_URL and SECRET_KEY are not configured")
        return None
    if not database or not secret:
        raise RuntimeError("PostgreSQL sessions require both DATABASE_URL and SECRET_KEY")
    return PostgresSessionStore(secret, max_age=int(os.environ.get("SESSION_MAX_AGE", "600")))


class PostgresSessionMiddleware:
    """Save HTTP sessions before headers, websocket sessions after teardown.

    HTTP state must be finalized before response headers (not in a streaming
    body or background task). Websockets must first obtain a cookie over HTTP.
    """

    def __init__(
        self, app: ASGIApp, *, store: PostgresSessionStore | None,
        secure: bool = True,
    ) -> None:
        self.app = app
        self.store = store
        self.cookie_name = "presto_session"
        self.secure = secure

    def _cookie(self, session: Session) -> str:
        cookie = SimpleCookie()
        cookie[self.cookie_name] = session.identifier
        morsel = cookie[self.cookie_name]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "lax"
        morsel["max-age"] = max(
            0, int((session.expires_at - datetime.now(timezone.utc)).total_seconds())
        )
        if self.secure:
            morsel["secure"] = True
        return morsel.OutputString()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        store = self.store
        if (
            store is None or scope["type"] not in ("http", "websocket")
            or scope.get("path", "").startswith("/static/")
        ):
            await self.app(scope, receive, send)
            return
        if "session" in scope:
            raise RuntimeError("another session middleware has already installed scope['session']")
        connection = HTTPConnection(scope)
        # WebSocket handshakes are not protected by cookie SameSite semantics.
        if scope["type"] == "websocket":
            origins = connection.headers.getlist("origin")
            expected_origin = (
                ("https" if scope["scheme"] == "wss" else "http")
                + "://" + connection.headers.get("host", "")
            )
            if origins and origins != [expected_origin]:
                await send({"type": "websocket.close", "code": 1008, "reason": "Invalid origin"})
                return
        session = await run_in_threadpool(
            store.load, connection.cookies.get(self.cookie_name)
        )
        if scope["type"] == "websocket" and session.version == 0:
            await send({
                "type": "websocket.close", "code": 1008,
                "reason": "Load a page over HTTP to establish a session first",
            })
            return
        scope["session"] = session.data

        async def send_with_session(message: Message) -> None:
            if message["type"] == "http.response.start":
                await run_in_threadpool(store.save, session)
                headers = MutableHeaders(scope=message)
                headers.append("set-cookie", self._cookie(session))
                headers["cache-control"] = "no-store"
                headers.add_vary_header("Cookie")
            await send(message)

        if scope["type"] == "websocket":
            try:
                await self.app(scope, receive, send_with_session)
            finally:
                with CancelScope(shield=True):
                    await run_in_threadpool(store.save, session)
        else:
            try:
                await self.app(scope, receive, send_with_session)
            except SessionConflictError:
                logger.warning("Concurrent or expired HTTP session write rejected")
                await JSONResponse(
                    {"detail": "Session changed or expired. Reload the page and retry."},
                    status_code=409,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
