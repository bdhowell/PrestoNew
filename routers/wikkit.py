"""Wikkit the Wizard: chatbot shell page, default iframe page, and websocket."""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from intents import IntentStack
from llm import IntentDispatchResult

DEFAULT_URL = "/wikkit/magic"
SOCKET_PATH = "/wikkit/ws"
MAX_MESSAGE_LENGTH = 2000

UNHANDLED_MESSAGE = "Sorry, I can't help with that yet."
FAILURE_MESSAGE = "Sorry, I had trouble understanding that. Please try again."
TOO_LONG_MESSAGE = (
    f"That message is too long. Please keep it under {MAX_MESSAGE_LENGTH} characters."
)

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
router = APIRouter(prefix="/wikkit", tags=["wikkit"])

type ClientReply = dict[str, object]


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


def client_reply(result: IntentDispatchResult[object] | None) -> ClientReply:
    """Translate an intent dispatch result into the websocket reply format."""
    if result is None:
        return {"user": UNHANDLED_MESSAGE}
    handled = result.handler_result
    if (
        isinstance(handled, Mapping)
        and handled.get("type") == "redirect"
        and isinstance(handled.get("url"), str)
    ):
        return {"client": {"action": "redirect", "payload": handled["url"]}}
    logger.warning(
        "Unsupported handler result for intent %r: %r", result.intent, handled
    )
    return {"user": UNHANDLED_MESSAGE}


def respond(stack: IntentStack, message: str) -> ClientReply:
    if len(message) > MAX_MESSAGE_LENGTH:
        return {"user": TOO_LONG_MESSAGE}
    try:
        return client_reply(stack.dispatch(message))
    except Exception:
        logger.exception("Wikkit failed to handle a message")
        return {"user": FAILURE_MESSAGE}


def _shell(request: Request, url: str | None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "wikkit/wikkit.html",
        {"url": frame_url(url), "socket_path": SOCKET_PATH},
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
    return templates.TemplateResponse(request, "wikkit/magic.html")


@router.websocket("/ws")
async def wikkit_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    stack = IntentStack()
    while True:
        event = await websocket.receive()
        if event["type"] == "websocket.disconnect":
            return
        text = event.get("text")
        if text is None:
            await websocket.close(code=1003, reason="Wikkit accepts text messages only.")
            return
        message = text.strip()
        if message:
            await websocket.send_json(await run_in_threadpool(respond, stack, message))
