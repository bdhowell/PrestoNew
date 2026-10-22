"""Trusted request identity: `state.user_id` and `state.user` for requests and sockets.

The signed-in user ID comes only from the server-side session. The name cookie
merely remembers what a returning visitor asked to be called; it grants nothing.
"""

import html
import os
from collections.abc import MutableMapping
from urllib.parse import quote, unquote

from starlette.requests import HTTPConnection
from starlette.responses import Response

import users

NAME_COOKIE = "presto_user"
NAME_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
MAX_NAME_LENGTH = 60
DEFAULT_NAME = "Friend"
SESSION_USER_ID = "user_id"


def application_name() -> str:
    return os.environ.get("APPLICATION_NAME", "PrestoPresentations")


def clean_name(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME_LENGTH or not name.isprintable():
        return None
    return name


def cookie_name(connection: HTTPConnection) -> str | None:
    """The returning visitor's name, or None for a first-time visitor."""
    value = connection.cookies.get(NAME_COOKIE)
    return clean_name(unquote(value)) if value else None


def set_name_cookie(response: Response, name: str) -> None:
    response.set_cookie(
        NAME_COOKIE, quote(name), max_age=NAME_COOKIE_MAX_AGE, path="/",
        httponly=True, samesite="lax",
        secure=os.environ.get("SESSION_COOKIE_SECURE", "true") == "true",
    )


def session(connection: HTTPConnection) -> MutableMapping[str, object] | None:
    return connection.scope.get("session")


def display_name(connection: HTTPConnection) -> str:
    """HTML-escaped `state.user['user-name']` for interpolation into Wikkit's HTML."""
    user = getattr(connection.state, "user", None) or {}
    return html.escape(str(user.get("user-name") or DEFAULT_NAME))


def sign_in(connection: HTTPConnection, user: users.User) -> None:
    data = session(connection)
    if data is None:
        raise RuntimeError("signing in requires the server-side session backend")
    data[SESSION_USER_ID] = user.uid
    _install(connection, user)


def sign_out(connection: HTTPConnection) -> None:
    data = session(connection)
    if data is not None:
        data.clear()
    _install(connection, None)


def _install(connection: HTTPConnection, user: users.User | None) -> None:
    connection.state.user_id = user.uid if user is not None else None
    connection.state.user = {
        "user-name": user.name if user is not None else cookie_name(connection) or DEFAULT_NAME,
        "uid": user.uid if user is not None else None,
        "username": user.username if user is not None else None,
        "email": user.email if user is not None else None,
    }


async def identity_context(connection: HTTPConnection) -> None:
    """App dependency; must run before `memory.memory_context`, which trusts user_id."""
    data = session(connection)
    user = None
    uid = data.get(SESSION_USER_ID) if data is not None else None
    if type(uid) is int and uid > 0:
        user = await users.store().get(uid)
        if user is None:  # account deleted elsewhere
            data.pop(SESSION_USER_ID, None)
    _install(connection, user)
