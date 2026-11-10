"""Redis server-side sessions; cookies contain only a random identifier."""

import hmac
import logging
import os
import pickle
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

from anyio import CancelScope
from redis.asyncio import Redis
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{43}\Z")
SESSION_COOKIE_NAME = "__Host-session"
LOCAL_SESSION_COOKIE_NAME = "presto_session"


class SessionConflictError(RuntimeError):
    """A concurrent request changed or expired this session."""


@dataclass
class Session:
    identifier: str
    data: dict[str, object]
    version: int
    expires_at: datetime
    original: bytes = field(repr=False)


class RedisSessionStore:
    _LOAD = """
local expires = redis.call('PEXPIRETIME', KEYS[1])
if expires < 0 then return {} end
local data = redis.call('HMGET', KEYS[1], 'data', 'version')
return {data[1], data[2], expires}
"""
    _SAVE = """
local version = redis.call('HGET', KEYS[1], 'version')
local now = redis.call('TIME')
local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local expires = tonumber(ARGV[4])
if ARGV[1] == '0' then
    if version then return {} end
    version = 1
else
    if not version then return {} end
    if ARGV[3] == '0' then
        if version ~= ARGV[1] then return {} end
        version = tonumber(version) + 1
    end
    expires = math.max(expires, redis.call('PEXPIRETIME', KEYS[1]))
end
if expires <= now_ms then return {} end
if ARGV[3] == '0' or ARGV[1] == '0' then
    redis.call('HSET', KEYS[1], 'data', ARGV[2], 'version', version)
end
redis.call('PEXPIREAT', KEYS[1], expires)
return {tonumber(version), expires}
"""

    def __init__(
        self, secret_key: str, *, max_age: int = 600,
        redis_url: str = "redis://localhost:6379/0",
    ) -> None:
        if len(secret_key.encode()) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 bytes")
        if max_age <= 0:
            raise ValueError("SESSION_MAX_AGE must be positive")
        self._secret = secret_key.encode()
        self.max_age = max_age
        self.redis_url = redis_url

    def _connect(self) -> Redis:
        return Redis.from_url(
            self.redis_url, socket_connect_timeout=5, socket_timeout=5,
        )

    def _key(self, identifier: str) -> str:
        return f"presto:session:{identifier}"

    async def delete(self, identifier: str | None) -> None:
        if identifier is not None and _SESSION_ID.fullmatch(identifier):
            async with self._connect() as connection:
                await connection.delete(self._key(identifier))

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

    async def load(self, identifier: str | None) -> Session:
        if identifier is not None and _SESSION_ID.fullmatch(identifier):
            async with self._connect() as connection:
                row = await connection.execute_command(
                    "EVAL", self._LOAD, 1, self._key(identifier),
                )
            if not isinstance(row, list):
                raise TypeError("Redis session load returned an invalid result")
            if row:
                expires_at = datetime.fromtimestamp(int(row[2]) / 1000, UTC)
                if expires_at > datetime.now(UTC):
                    data, original = self._decode(identifier, row[0])
                    return Session(identifier, data, int(row[1]), expires_at, original)
        return Session(
            secrets.token_urlsafe(32), {}, 0,
            datetime.now(UTC) + timedelta(seconds=self.max_age),
            pickle.dumps({}, protocol=pickle.HIGHEST_PROTOCOL),
        )

    async def save(self, session: Session, *, renew: bool = False) -> None:
        payload = pickle.dumps(session.data, protocol=pickle.HIGHEST_PROTOCOL)
        unchanged = session.version != 0 and payload == session.original
        if unchanged and not renew:
            return
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.max_age)
            if renew else session.expires_at
        )
        encoded = self._encode(session.identifier, payload)
        async with self._connect() as connection:
            row = await connection.execute_command(
                "EVAL", self._SAVE, 1, self._key(session.identifier), str(session.version),
                encoded, "1" if unchanged else "0", int(expires_at.timestamp() * 1000),
            )
        if not isinstance(row, list):
            raise TypeError("Redis session save returned an invalid result")
        if not row:
            raise SessionConflictError("session changed or expired; retry with a fresh request")
        # A read-only renewal can encounter a newer payload; do not adopt its version.
        if not unchanged:
            session.version = int(row[0])
        session.expires_at = datetime.fromtimestamp(int(row[1]) / 1000, UTC)
        session.original = payload


def configured_session_store() -> RedisSessionStore | None:
    backend = os.environ.get("SESSION_BACKEND", "auto")
    if backend not in ("auto", "none", "redis"):
        raise ValueError("SESSION_BACKEND must be auto, redis, or none")
    if backend == "none":
        logger.warning("Server-side sessions explicitly disabled")
        return None
    redis_url = os.environ.get("REDIS_URL")
    secret = os.environ.get("SECRET_KEY")
    if backend == "auto" and not redis_url and not secret:
        logger.warning("Server-side sessions disabled: REDIS_URL and SECRET_KEY are not configured")
        return None
    if not secret:
        raise RuntimeError("Redis sessions require SECRET_KEY")
    return RedisSessionStore(
        secret, max_age=int(os.environ.get("SESSION_MAX_AGE", "600")),
        redis_url=redis_url or "redis://localhost:6379/0",
    )


class RedisSessionMiddleware:
    """Save HTTP sessions before headers, websocket sessions after teardown.

    HTTP state must be finalized before response headers (not in a streaming
    body or background task). Websockets must first obtain a cookie over HTTP.
    """

    def __init__(
        self, app: ASGIApp, *, store: RedisSessionStore | None,
        secure: bool = True,
    ) -> None:
        self.app = app
        self.store = store
        self.secure = secure
        self.cookie_name = SESSION_COOKIE_NAME if secure else LOCAL_SESSION_COOKIE_NAME

    def _cookie(self, session: Session) -> str:
        cookie = SimpleCookie()
        cookie[self.cookie_name] = session.identifier
        morsel = cookie[self.cookie_name]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "strict"
        morsel["max-age"] = max(
            0, int((session.expires_at - datetime.now(UTC)).total_seconds())
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
        path = scope.get("path", "").rstrip("/")
        if scope["type"] == "http" and path == "/account/signout":
            await store.delete(connection.cookies.get(self.cookie_name))
            scope["session"] = {}
            await self.app(scope, receive, send)
            return
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
        session = await store.load(connection.cookies.get(self.cookie_name))
        if scope["type"] == "websocket" and session.version == 0:
            await send({
                "type": "websocket.close", "code": 1008,
                "reason": "Load a page over HTTP to establish a session first",
            })
            return
        scope["session"] = session.data
        scope["server_session"] = session
        scope["session_monitor"] = True

        async def send_with_session(message: Message) -> None:
            if message["type"] == "http.response.start":
                if path != "/system/timecheck":
                    await store.save(session, renew=True)
                headers = MutableHeaders(scope=message)
                if path != "/system/timecheck":
                    headers.append("set-cookie", self._cookie(session))
                headers["cache-control"] = "no-store"
                headers.add_vary_header("Cookie")
            await send(message)

        if scope["type"] == "websocket":
            try:
                await self.app(scope, receive, send_with_session)
            finally:
                with CancelScope(shield=True):
                    await store.save(session)
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
