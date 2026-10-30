"""Wikkit the Wizard: landing/pricing/help pages and websocket."""

import html
import logging
import os
import re
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import identity
from common import clean_response
from wikkit import Chatbot, Workflow

logger = logging.getLogger(__name__)
DEFAULT_URL = "/wikkit/magic"
SOCKET_PATH = "/wikkit/ws"
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
templates = Jinja2Templates(directory=BASE_DIR / "templates")
router = APIRouter(prefix="/wikkit", tags=["wikkit"])

_PAGE_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


def page_assets(page_name: str) -> dict[str, str | None]:
    if not _PAGE_NAME_PATTERN.fullmatch(page_name):
        raise ValueError(f"Invalid page asset name: {page_name!r}")

    assets: dict[str, str | None] = {}
    for asset_type, extension in (
        ("css", "css"), ("javascript", "js"), ("javascript", "does")
    ):
        asset_path = STATIC_DIR / asset_type / "pages" / f"{page_name}.{extension}"
        assets["does" if extension == "does" else asset_type] = (
            f"/static/{asset_type}/pages/{page_name}.{extension}"
            if asset_path.is_file()
            else None
        )
    return assets


templates.env.globals["page_assets"] = page_assets
templates.env.globals["app_name"] = identity.application_name


def navigation_url(url: str | None) -> str:
    """Return a same-origin navigation path, or the default page."""
    if url is None:
        return DEFAULT_URL
    url = url.strip()
    # Browsers treat "\" as "/" and strip tabs/newlines, so "/\evil" or
    # "/\t/evil" would otherwise become protocol-relative URLs.
    if not url.startswith("/") or "\\" in url or not url.isprintable():
        return DEFAULT_URL
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return DEFAULT_URL
    return url


def chat_window_transition_ms() -> int:
    duration = int(os.environ.get("CHAT_WINDOW_TRANSITION", "440"))
    if duration < 0:
        raise ValueError("CHAT_WINDOW_TRANSITION must be non-negative.")
    return duration


def chat_input_transition_ms() -> int:
    duration = int(os.environ.get("CHAT_INPUT_TRANSITION", "500"))
    if duration < 0:
        raise ValueError("CHAT_INPUT_TRANSITION must be non-negative.")
    return duration


@router.get("")
async def wikkit(request: Request) -> RedirectResponse:
    return RedirectResponse(DEFAULT_URL, status_code=303)


@router.post("")
async def wikkit_with_url(
    request: Request, url: Annotated[str | None, Form()] = None
) -> RedirectResponse:
    return RedirectResponse(navigation_url(url), status_code=303)


def render_page(
    request: Request, template: str, page_name: str, says: str | None = None,
    status_code: int = 200, **context: object,
) -> HTMLResponse:
    """Render a page with Wikkit; `says` is trusted HTML Wikkit says on load."""
    session = identity.session(request)
    pending = session.pop("wikkit_says", None) if session is not None else None
    if isinstance(pending, str):
        says = pending + (says or "")
    return templates.TemplateResponse(
        request, template,
        {
            "page_name": page_name, "wikkit_says": says,
            "socket_path": SOCKET_PATH,
            "chat_window_transition_ms": chat_window_transition_ms(),
            "chat_input_transition_ms": chat_input_transition_ms(),
            **context,
        },
        status_code=status_code,
    )


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Cross-origin request rejected")


async def read_json(request: Request) -> dict[str, object]:
    """Read a same-origin JSON object body.

    Requiring application/json forces a CORS preflight for cross-site callers,
    which together with SameSite=Lax cookies protects these state changes.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type != "application/json":
        raise HTTPException(415, "Expected application/json")
    check_origin(request)
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "Expected a JSON object")
    return data


def _app_name() -> str:
    return html.escape(identity.application_name())


_NAME_PREFIX = re.compile(
    r"^(?:(?:hi|hello|hey)\b[\s,!.]*)?"
    r"(?:(?:my name is|my name's|i am|i'm|im|it's|it is|this is|call me|you can call me)\s+)?",
    re.IGNORECASE,
)
_NAME_PATTERN = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'’-])*")


def extract_name(response: str) -> str | None:
    """Return the name in a reply such as "Ada" or "Call me Ada.", if there is one."""
    name = _NAME_PREFIX.sub("", " ".join(response.split())).strip(" .,!?;:\"'")
    if (
        not name or len(name.split()) > 4 or not _NAME_PATTERN.fullmatch(name)
        or (clean_response(name) or {}).get("type") == "system"
    ):
        return None
    return identity.clean_name(name)


async def magic_workflow(bot: Chatbot) -> None:
    app_name = _app_name()
    await bot.send({"say": f"""
<div>
Welcome to {app_name}. I&rsquo;m Wikkit, {app_name}&rsquo;s resident wizard.
</div>
<div>
With a few minutes and a little magic, we can quickly and easily prepare your conference or fair poster.
</div>
<p>
Can I have your name to start?
</p>
"""})
    prompt = None
    tries = 3
    while True:
        name = extract_name(await bot.request_response(prompt))
        if name is not None:
            await bot.send({"say": f"""
<p>
Nice to meet you, {html.escape(name)}!
</p>
"""})
            break
        tries -= 1
        if tries == 0:
            await bot.send({"say": """
<p>
I&rsquo;m sorry, I didn't catch your name.
You can always tell me later.
For now I&rsquo;ll call you <em>Friend</em>.
</p>
"""})
            name = identity.DEFAULT_NAME
            break
        prompt = """
<p>
I&rsquo;m sorry, I didn&rsquo;t catch your name. Could you please tell it to me?
</p>
"""
    user = getattr(bot.websocket.state, "user", None)
    if isinstance(user, dict):
        user["user-name"] = name
    await bot.send({"command": {"does": "remember-name", "payload": {"name": name}}})
    await bot.send({"command": {"does": "redirect", "payload": "/account/register"}})


async def pricing_workflow(bot: Chatbot) -> None:
    response = await bot.request_response()
    answer = (clean_response(response) or {}).get("response")
    if answer == "no":
        await bot.send({"say": "<p>Okay! Just let me know whenever you&rsquo;re ready.</p>"})
        return
    if answer == "yes":
        response = await bot.request_response("""
<p>
Sure, what else would you like to know?
</p>
""")
    await bot.handle_message(response)


FINISHED_RESPONSES = ["finished", "imfinished", "done", "imdone", "alldone"]


def finished_uploading(response: str) -> bool:
    return (clean_response(response, FINISHED_RESPONSES) or {}).get("type") == "caller"


async def upload_workflow(bot: Chatbot) -> None:
    while True:
        response = await bot.request_response(remind=False)
        if finished_uploading(response):
            await bot.send({"command": {"does": "finished-uploading"}})
        else:
            await bot.handle_message(response)


WORKFLOWS: dict[str, Workflow] = {
    "wikkit-magic": magic_workflow,
    "wikkit-pricing": pricing_workflow,
    "content-upload": upload_workflow,
}


@router.get("/magic", response_class=HTMLResponse)
async def magic(request: Request) -> Response:
    if identity.cookie_name(request) is not None:
        return RedirectResponse("/account/signin", status_code=303)
    return render_page(request, "wikkit/magic.html", "wikkit-magic")


@router.post("/name")
async def remember_name(request: Request) -> JSONResponse:
    """Remember the visitor's name in a cookie so returning visitors are greeted."""
    name = identity.clean_name((await read_json(request)).get("name"))
    if name is None:
        raise HTTPException(422, "A name is required")
    request.state.user["user-name"] = name
    response = JSONResponse({"ok": True})
    identity.set_name_cookie(response, name)
    return response


def _price() -> str:
    try:
        price = float(os.environ.get("RENDER_PRICE", "0"))
    except ValueError:
        price = 0.0
    return f"${price:,.2f}" if price > 0 else "free"


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request) -> HTMLResponse:
    says = f"""
<p>
Here is our pricing information, {identity.display_name(request)}.
As you can see, it&rsquo;s really simple: you pay only when you&rsquo;re ready to download your print-ready poster.
</p>
<p>
Do you have any questions?
</p>
"""
    return render_page(
        request, "wikkit/pricing.html", "wikkit-pricing", says, price=_price()
    )


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request) -> HTMLResponse:
    says = f"""
<p>
What can I help you with, {identity.display_name(request)}?
</p>
"""
    return render_page(
        request, "wikkit/help.html", "wikkit-help", says,
        support_email=os.environ.get("SUPPORT_EMAIL", "support@prestopresentations.com"),
    )


@router.websocket("/ws")
async def wikkit_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    chatbot = Chatbot(websocket, websocket.state.memory, WORKFLOWS)
    websocket.state.chatbot = chatbot
    await chatbot.run()
