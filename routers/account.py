"""Account registration, confirmation, sign-in/out, password recovery, and deletion."""

import asyncio
import html
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import identity
import mailer
import users
from routers.wikkit import read_json, render_page

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/account", tags=["account"])

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256
_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,39}\Z")
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+\Z")
RECOVERY_GRANT = "password_recovery"
POSTERS_PAGE = "/posters/list"


def _max_age(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def confirmation_max_age() -> int:
    return _max_age("CONFIRMATION_LINK_MAX_AGE", 24 * 60 * 60)


def recovery_max_age() -> int:
    return _max_age("RECOVERY_LINK_MAX_AGE", 60 * 60)


def _reply(
    ok: bool, says: str | None = None, *, redirect: str | None = None,
    errors: dict[str, str] | None = None, status_code: int = 200,
    command: dict[str, str] | None = None,
    signed_in: bool | None = None,
) -> JSONResponse:
    """AJAX result: `says` is trusted HTML for Wikkit; `errors` are plain text per field."""
    body: dict[str, object] = {"ok": ok}
    if says is not None:
        body["say"] = says
    if redirect is not None:
        body["redirect"] = redirect
    if errors:
        body["errors"] = errors
    if command is not None:
        body["command"] = command
    if signed_in is not None:
        body["signed_in"] = signed_in
    return JSONResponse(body, status_code=status_code)


def _text(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _password(data: dict[str, object], key: str = "password") -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _password_error(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Passwords must have at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Passwords must have at most {MAX_PASSWORD_LENGTH} characters."
    return None


def _signed_in(request: Request) -> bool:
    return bool(getattr(request.state, "user_id", None))


def _sessions_available(request: Request) -> bool:
    return identity.session(request) is not None


SESSIONS_UNAVAILABLE = """
<p>
I&rsquo;m sorry, but my spellbook is closed: accounts are unavailable right now. Please try again later.
</p>
"""


def resumption_page(request: Request) -> str:
    """Resume a poster, or find a conference when no poster is selected."""
    poster = getattr(request.state, "poster", None)
    if poster is None:
        return "/conferences/find"
    page = poster.get("resumption-page") if isinstance(poster, dict) else None
    return page if isinstance(page, str) and page.startswith("/") and not page.startswith("//") else POSTERS_PAGE


def _link(request: Request, path: str, **query: object) -> str:
    """Absolute link for email; set PUBLIC_BASE_URL so links never trust the Host header."""
    base = os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)
    return f"{base.rstrip('/')}{path}?{urlencode(query)}"


async def _send_confirmation(request: Request, user: users.User) -> None:
    link = _link(
        request, "/account/confirmation",
        uid=user.uid, token=users.link_token(users.CONFIRMATION_PURPOSE, user),
    )
    app_name = identity.application_name()
    await asyncio.to_thread(
        mailer.send_email, user.email, f"Confirm your {app_name} account",
        f"Hello {user.name},\n\n"
        f"Please confirm your {app_name} account by opening this link:\n\n{link}\n\n"
        f"The link expires in {confirmation_max_age() // 3600} hours. "
        "If you didn't register, you can ignore this email.\n\n"
        f"Wikkit, {app_name}'s resident wizard\n",
    )


def _require_sign_in(request: Request) -> RedirectResponse | None:
    return None if _signed_in(request) else RedirectResponse("/account/signin", status_code=303)


@router.get("", include_in_schema=False)
async def account(request: Request) -> RedirectResponse:
    return RedirectResponse(
        "/account/update" if _signed_in(request) else "/account/signin", status_code=303
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    says = """
<p>Please fill out and submit the registration form so that we can get started on your poster.</p>
"""
    return render_page(request, "account/register.html", "account-register", says)


@router.post("/register")
async def register(request: Request) -> JSONResponse:
    data = await read_json(request)
    username, email, password = _text(data, "username"), _text(data, "email"), _password(data)
    errors: dict[str, str] = {}
    if not _USERNAME.fullmatch(username):
        errors["username"] = (
            "Usernames have 3 to 40 letters, digits, periods, hyphens, or underscores, "
            "and start with a letter or digit."
        )
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        errors["email"] = "Please enter a valid email address."
    if (problem := _password_error(password)) is not None:
        errors["password"] = problem
    if errors:
        return _reply(
            False, "<p>Please correct the highlighted fields.</p>", errors=errors, status_code=422
        )
    store = users.store()
    taken = await store.conflicts(username, email)
    if not taken:
        name = identity.cookie_name(request)
        if name is None or name == identity.DEFAULT_NAME:
            name = username
        try:
            password_hash = await asyncio.to_thread(users.hash_password, password)
            user = await store.create(name, username, email, password_hash)
        except users.DuplicateUserError as error:
            taken = error.fields
    if taken:
        errors = {
            field: f"That {field} is already registered." for field in sorted(taken)
        }
        says = f"""
<p>
I&rsquo;m sorry, {identity.display_name(request)}, but that {' and '.join(sorted(taken))}
{'are' if len(taken) > 1 else 'is'} already registered.
If you already have an account, you can <a href="/account/signin">sign in</a> instead.
</p>
"""
        return _reply(False, says, errors=errors, status_code=409)
    await _send_confirmation(request, user)
    says = f"""
<p>
Thank you, {html.escape(user.name)}! I&rsquo;ve sent a confirmation link to <strong>{html.escape(user.email)}</strong>.
</p>
<p>
Please open it to finish registering, and then we&rsquo;ll get started on your poster.
</p>
"""
    return _reply(True, says)


@router.get("/confirmation", response_class=HTMLResponse)
async def confirmation(request: Request, uid: int = 0, token: str = "") -> Response:
    store = users.store()
    if uid == 0 and not token:
        # Emailed links redirect here once used, so reloading never spends the link twice.
        user_id = getattr(request.state, "user_id", None)
        user = await store.get(user_id) if user_id is not None else None
        if user is None:
            return RedirectResponse("/account/signin", status_code=303)
        return _confirmed_page(request, user)
    user = await store.get(uid) if uid > 0 else None
    confirmed = None
    if user is not None and users.check_link(
        users.CONFIRMATION_PURPOSE, user, token, confirmation_max_age()
    ):
        assert user.recovery is not None
        confirmed = await store.verify(user.uid, user.recovery)
    if confirmed is None:
        says = """
<p>
I&rsquo;m sorry, but that confirmation link is invalid, already used, or expired.
Please <a href="/account/signin">sign in</a> and I&rsquo;ll send you a fresh one.
</p>
"""
        return render_page(
            request, "account/confirmation.html", "account-confirmation", says,
            status_code=400, confirmed=False,
        )
    if not _sessions_available(request):
        return _confirmed_page(request, confirmed)
    identity.sign_in(request, confirmed)
    return RedirectResponse("/account/confirmation", status_code=303)


def _confirmed_page(request: Request, user: users.User) -> HTMLResponse:
    says = f"""
<p>
Congratulations, {html.escape(user.name)}.
We&rsquo;re all set.
</p>
<p>
Let&rsquo;s get started on your poster!
</p>
"""
    return render_page(
        request, "account/confirmation.html", "account-confirmation", says,
        confirmed=True, name=user.name, next_page=resumption_page(request),
    )


@router.get("/signin", response_class=HTMLResponse)
async def signin_page(request: Request, email: str = "") -> Response:
    if _signed_in(request):
        return RedirectResponse(resumption_page(request), status_code=303)
    name = identity.cookie_name(request)
    data = identity.session(request)
    if data is not None and name is not None:
        data["username"] = name
    if name is not None:
        says = f"""
<p>
Welcome back, {identity.display_name(request)}.
I&rsquo;m happy to see you again.
</p>
<p>
Please sign in and we&rsquo;ll continue working on your poster.
</p>
"""
    else:
        says = """
<p>
Please sign in and we&rsquo;ll continue working on your poster.
</p>
"""
    return render_page(
        request, "account/signin.html", "account-signin", says,
        returning_name=name, email=email, clear_chat=True,
    )


@router.post("/validate")
async def validate(request: Request) -> JSONResponse:
    data = await read_json(request)
    if not _sessions_available(request):
        return _reply(False, SESSIONS_UNAVAILABLE, status_code=503)
    email, password = _text(data, "email"), _password(data)
    user = await users.store().get_by_email(email) if email else None
    valid = await asyncio.to_thread(
        users.verify_password, password[:MAX_PASSWORD_LENGTH],
        user.password if user is not None else None,
    )
    if user is not None and valid and user.verified is None:
        pending = await users.store().start_recovery(user.uid)
        if pending is not None:
            await _send_confirmation(request, pending)
        says = f"""
<p>
{html.escape(user.name)}, you haven&rsquo;t confirmed your email address yet.
I&rsquo;ve sent a fresh confirmation link to <strong>{html.escape(user.email)}</strong>.
</p>
"""
        return _reply(False, says, status_code=403)
    if user is not None and valid:
        identity.sign_in(request, user)

        says = """
<p>
Let&rsquo;s get back to work on your poster!
</p>
"""
        return _reply(
            True, says, redirect=resumption_page(request), command={"does": "clear-chat"},
            signed_in=True,
        )
    name = identity.cookie_name(request)
    if name is not None:
        says = f"""
<p>
I&rsquo;m sorry, {html.escape(name)}, I don&rsquo;t recognize your email or password.
Please try again.
</p>
"""
    else:
        says = """
<p>
I&rsquo;m sorry, I don&rsquo;t recognize that email or password.
Please try again.
</p>
"""
    return _reply(False, says, status_code=401)


@router.get("/signout", response_class=HTMLResponse)
async def signout(request: Request) -> HTMLResponse:
    name = identity.display_name(request)
    identity.sign_out(request)
    says = f"""
<p>
Okay, {name}, you have been signed out of {html.escape(identity.application_name())}.
</p>
<p>
I hope to see you again soon.
</p>
"""
    return render_page(request, "account/signout.html", "account-signout", says)


@router.get("/update", response_class=HTMLResponse)
async def update_page(request: Request) -> Response:
    if (redirect := _require_sign_in(request)) is not None:
        return redirect
    says = f"""
<p>
What would you like to change, {identity.display_name(request)}?
</p>
"""
    return render_page(request, "account/update.html", "account-update", says)


@router.get("/recover", response_class=HTMLResponse)
async def recover_page(request: Request, email: str = "") -> HTMLResponse:
    says = f"""
<p>
Please tell me your email address, {identity.display_name(request)}, so I can send you a recovery link.
</p>
"""
    return render_page(
        request, "account/recover.html", "account-recover", says, email=email
    )


@router.post("/recover")
async def recover(request: Request) -> JSONResponse:
    email = _text(await read_json(request), "email")
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        return _reply(
            False, "<p>Please enter a valid email address.</p>",
            errors={"email": "Please enter a valid email address."},
        )
    store = users.store()
    user = await store.get_by_email(email)
    if user is not None:
        user = await store.start_recovery(user.uid)
    if user is not None:
        link = _link(
            request, "/account/recovered",
            uid=user.uid, token=users.link_token(users.RECOVERY_PURPOSE, user),
        )
        app_name = identity.application_name()
        await asyncio.to_thread(
            mailer.send_email, user.email, f"Reset your {app_name} password",
            f"Hello {user.name},\n\n"
            f"Open this link to choose a new {app_name} password:\n\n{link}\n\n"
            f"The link expires in {recovery_max_age() // 60} minutes. "
            "If you didn't ask to reset your password, you can ignore this email.\n\n"
            f"Wikkit, {app_name}'s resident wizard\n",
        )
    # The same answer whether or not the address is registered.
    says = f"""
<p>
If <strong>{html.escape(email)}</strong> belongs to an account, I&rsquo;ve sent it a recovery link.
Please check your email.
</p>
"""
    return _reply(True, says)


@router.get("/recovered", response_class=HTMLResponse)
async def recovered(request: Request, uid: int = 0, token: str = "") -> Response:
    user = await users.store().get(uid) if uid > 0 else None
    data = identity.session(request)
    if (
        data is not None and user is not None and user.recovery is not None
        and users.check_link(users.RECOVERY_PURPOSE, user, token, recovery_max_age())
    ):
        data[RECOVERY_GRANT] = {"uid": user.uid, "recovery": user.recovery}
        return RedirectResponse("/account/password", status_code=303)
    says = f"""
<p>
I&rsquo;m sorry, {identity.display_name(request)}, but that recovery link is invalid, already used, or expired.
Tell me your email address and I&rsquo;ll send you a new one.
</p>
"""
    return render_page(
        request, "account/recovered.html", "account-recovered", says, status_code=400
    )


def _recovery_grant(request: Request) -> tuple[int, datetime] | None:
    data = identity.session(request)
    grant = data.get(RECOVERY_GRANT) if data is not None else None
    if not isinstance(grant, dict):
        return None
    uid, recovery = grant.get("uid"), grant.get("recovery")
    if type(uid) is not int or not isinstance(recovery, datetime):
        return None
    if (datetime.now(timezone.utc) - recovery).total_seconds() > recovery_max_age():
        return None
    return uid, recovery


@router.get("/password", response_class=HTMLResponse)
async def password_page(request: Request) -> Response:
    recovering = _recovery_grant(request) is not None
    if not recovering and (redirect := _require_sign_in(request)) is not None:
        return redirect
    says = f"""
<p>
Let&rsquo;s choose a new password, {identity.display_name(request)}.
</p>
"""
    return render_page(
        request, "account/password.html", "account-password", says,
        recovering=recovering,
    )


@router.post("/password")
async def change_password(request: Request) -> JSONResponse:
    data = await read_json(request)
    password = _password(data)
    if (problem := _password_error(password)) is not None:
        return _reply(False, f"<p>{html.escape(problem)}</p>", errors={"password": problem})
    store = users.store()
    grant = _recovery_grant(request)
    if grant is not None:
        uid, recovery = grant
        password_hash = await asyncio.to_thread(users.hash_password, password)
        user = await store.set_password(uid, password_hash, recovery)
        session = identity.session(request)
        assert session is not None
        session.pop(RECOVERY_GRANT, None)
        if user is None:
            says = """
<p>
I&rsquo;m sorry, but that recovery link has already been used.
Please <a href="/account/recover">request a new one</a>.
</p>
"""
            return _reply(False, says, status_code=409)
        identity.sign_in(request, user)
    elif _signed_in(request):
        current = await store.get(request.state.user_id)
        if current is None or not await asyncio.to_thread(
            users.verify_password, _password(data, "current_password")[:MAX_PASSWORD_LENGTH],
            current.password,
        ):
            message = "That isn't your current password."
            return _reply(
                False, f"<p>I&rsquo;m sorry, {identity.display_name(request)}. {message}</p>",
                errors={"current_password": message}, status_code=403,
            )
        password_hash = await asyncio.to_thread(users.hash_password, password)
        await store.set_password(current.uid, password_hash)
    else:
        return _reply(
            False, '<p>Please <a href="/account/signin">sign in</a> first.</p>', status_code=401
        )
    says = f"""
<p>
All done, {identity.display_name(request)}! Your new password is in place.
</p>
"""
    return _reply(True, says, redirect=resumption_page(request), signed_in=True)


@router.get("/delete", response_class=HTMLResponse)
async def delete_page(request: Request) -> Response:
    if (redirect := _require_sign_in(request)) is not None:
        return redirect
    says = f"""
<p>
{identity.display_name(request)}, I&rsquo;m sorry to see you go.
To delete your account, please confirm your email address and password.
</p>
<p>
This can&rsquo;t be undone.
</p>
"""
    return render_page(request, "account/delete.html", "account-delete", says)


@router.post("/delete")
async def delete(request: Request) -> JSONResponse:
    data = await read_json(request)
    if not _signed_in(request):
        return _reply(
            False, '<p>Please <a href="/account/signin">sign in</a> first.</p>', status_code=401
        )
    store = users.store()
    user = await store.get(request.state.user_id)
    if (
        user is None or _text(data, "email").lower() != user.email.lower()
        or not await asyncio.to_thread(
            users.verify_password, _password(data)[:MAX_PASSWORD_LENGTH], user.password
        )
    ):
        return _reply(
            False,
            f"<p>I&rsquo;m sorry, {identity.display_name(request)}, "
            "but that email address and password don&rsquo;t match your account.</p>",
            status_code=403,
        )
    name = identity.display_name(request)
    await store.delete(user.uid)
    identity.sign_out(request)
    says = f"""
<p>
Your account has been deleted, {name}. Farewell, and may your future posters be magnificent!
</p>
"""
    response = _reply(True, says, redirect="/wikkit/magic", signed_in=False)
    response.delete_cookie(identity.NAME_COOKIE, path="/")
    return response
