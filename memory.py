"""Memory scopes shared by HTTP requests and chatbot workflows."""

from collections.abc import AsyncIterator, MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

from kvstore import KVStore


@dataclass
class Memory:
    """Safe to pickle in a trusted server-side session, without socket state."""

    short_term_memory: dict[str, object] = field(default_factory=dict)
    long_term_memory: KVStore | None = None
    user_memory: KVStore | None = None
    poster_id: int | None = None
    user_id: int | None = None

    @classmethod
    def for_identity(cls, poster_id: int | None, user_id: int | None) -> Memory:
        return cls(
            long_term_memory=(
                KVStore("poster_memory", "poster", foreign_key_value=poster_id)
                if poster_id is not None else None
            ),
            user_memory=(
                KVStore("user_memory", "user", foreign_key_value=user_id)
                if user_id is not None else None
            ),
            poster_id=poster_id,
            user_id=user_id,
        )


_current_memory: ContextVar[Memory] = ContextVar("chatbot_memory")


def current_memory() -> Memory:
    """Return the active memory, including inside threadpool intent handlers."""
    return _current_memory.get()


def _identity(connection: HTTPConnection, name: str) -> int | None:
    value = getattr(connection.state, name, None)
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError(f"trusted state.{name} must be a positive integer or None")
    return value


async def memory_context(connection: HTTPConnection) -> AsyncIterator[Memory]:
    """Trust only IDs installed by server-side authentication/authorization.

    An optional session mapping must be server-side, support Python objects,
    and persist mutations after the HTTP request or websocket finishes.
    """
    poster_id = _identity(connection, "poster_id")
    user_id = _identity(connection, "user_id")
    session = connection.scope.get("session")
    if session is not None and not isinstance(session, MutableMapping):
        raise TypeError("memory requires a mutable server-side session mapping")
    memory = session.get("chatbot_memory") if session is not None else None
    if memory is not None and not isinstance(memory, Memory):
        raise TypeError("session chatbot_memory must be a Memory instance")
    if memory is None or (memory.poster_id, memory.user_id) != (poster_id, user_id):
        memory = await run_in_threadpool(Memory.for_identity, poster_id, user_id)
    if session is not None:
        session["chatbot_memory"] = memory
    connection.state.short_term_memory = memory.short_term_memory
    connection.state.long_term_memory = memory.long_term_memory
    connection.state.user_memory = memory.user_memory
    connection.state.memory = memory
    token = _current_memory.set(memory)
    try:
        yield memory
    finally:
        try:
            if session is not None:
                session["chatbot_memory"] = memory
        finally:
            _current_memory.reset(token)
