"""Wikkit the Wizard: chatbot shell page, default iframe page, and websocket."""

import re
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from wikkit import Chatbot

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


def frame_url(url: str | None) -> str:
    """Return a same-origin path for the iframe, or the default page.

    Iframe pages call connect(), which reaches into the parent window, so only
    same-origin paths can work; anything else would also allow framing other sites.
    """
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


def _shell(request: Request, url: str | None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "wikkit/wikkit.html",
        {
            "url": frame_url(url),
            "socket_path": SOCKET_PATH,
            "page_name": "wikkit",
        },
    )


@router.get("", response_class=HTMLResponse)
async def wikkit(request: Request) -> HTMLResponse:
    return _shell(request, None)


@router.post("", response_class=HTMLResponse)
async def wikkit_with_url(
    request: Request, url: Annotated[str | None, Form()] = None
) -> HTMLResponse:
    return _shell(request, url)


@router.get("/magic", response_class=HTMLResponse)
async def magic(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "wikkit/magic.html", {"page_name": "wikkit-magic"}
    )


@router.websocket("/ws")
async def wikkit_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    chatbot = Chatbot(websocket, websocket.state.memory)
    websocket.state.chatbot = chatbot
    await chatbot.run()
