"""Conversation-local Wikkit protocol, server sends, and response waiting."""

import asyncio
import html
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect, responses

from intents import IntentStack
from llm import IntentDispatchResult
from memory import Memory

MAX_MESSAGE_LENGTH = 2000
UNHANDLED_MESSAGE = "Sorry, I can't help with that yet."
FAILURE_MESSAGE = "Sorry, I had trouble understanding that. Please try again."
TOO_LONG_MESSAGE = (
    f"That message is too long. Please keep it under {MAX_MESSAGE_LENGTH} characters."
)

logger = logging.getLogger(__name__)
type ClientReply = dict[str, object]
type Workflow = Callable[["Chatbot"], Awaitable[None]]
_SUBSTITUTION_PATTERN = re.compile(
    r"\{\{\s*((?:user|poster|session)(?:\.[A-Za-z][A-Za-z0-9_-]*)+)\s*\}\}"
)


def client_reply(result: IntentDispatchResult[object] | None) -> ClientReply:
    """Normalize intent results to the say/command protocol."""
    if result is None:
        return {"say": UNHANDLED_MESSAGE}
    handled = result.handler_result
    if isinstance(handled, Mapping):
        if handled.get("type") == "redirect" and isinstance(handled.get("url"), str):
            return {"command": {"does": "redirect", "payload": handled["url"]}}
        reply: ClientReply = {}
        responses = handled.get("responses")
        if responses is not None:
            if (
                not isinstance(responses, list)
                or not responses
                or not all(isinstance(response, str) for response in responses)
            ):
                raise ValueError("responses must be a nonempty list of strings")
            reply["say"] = random.choice(responses)
        else:
            say = handled.get("say", handled.get("user"))
            if say is not None:
                if not isinstance(say, str):
                    raise TypeError("say must be a string")
                reply["say"] = say
        command = handled.get("command", handled.get("client"))
        if command is not None:
            if not isinstance(command, Mapping):
                raise TypeError("command must be an object")
            action = command.get("does", command.get("action"))
            if not isinstance(action, str) or not action.strip():
                raise ValueError("command.does must be a nonempty command key")
            normalized: dict[str, object] = {"does": action}
            if "payload" in command:
                normalized["payload"] = command["payload"]
            reply["command"] = normalized
        if reply:
            return reply
    logger.warning(
        "Unsupported handler result for intent %r: %r", result.intent, handled
    )
    return {"say": UNHANDLED_MESSAGE}


def page_event(message: str) -> str | None:
    """Return the page slug from a `{"page": slug}` control message, if it is one."""
    if not message.startswith("{"):
        return None
    try:
        data = json.loads(message)
    except ValueError:
        return None
    if isinstance(data, dict) and set(data) == {"page"} and isinstance(data["page"], str):
        return data["page"]
    return None


async def respond(stack: IntentStack, message: str) -> ClientReply:
    if len(message) > MAX_MESSAGE_LENGTH:
        return {"say": TOO_LONG_MESSAGE}
    try:
        return client_reply(await stack.dispatch(message))
    except Exception:
        logger.exception("Wikkit failed to handle a message")
        return {"say": FAILURE_MESSAGE}


class Chatbot:
    """One instance per socket; retain it to send unsolicited server messages."""

    def __init__(
        self, websocket: WebSocket, memory: Memory | None = None,
        workflows: Mapping[str, Workflow] | None = None,
    ) -> None:
        self.websocket = websocket
        self.memory = memory if memory is not None else Memory()
        self.short_term_memory = self.memory.short_term_memory
        self.long_term_memory = self.memory.long_term_memory
        self.user_memory = self.memory.user_memory
        self.stack = IntentStack()
        self._messages: asyncio.Queue[str] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._response: asyncio.Future[str] | None = None
        self._closed = False
        self.workflows = dict(workflows or {})
        self._workflow: asyncio.Task[None] | None = None

    def substitute(self, text: str) -> str:
        """Replace public state fields with HTML-escaped values.

        Unavailable values are left unchanged.
        """
        def replace(match: re.Match[str]) -> str:
            root, *fields = match.group(1).split(".")
            value: object = getattr(self.websocket.state, root, None)
            for field in fields:
                if isinstance(value, Mapping):
                    value = value.get(field)
                else:
                    value = getattr(value, field, None)
            if value is None or callable(value):
                logger.warning("Unavailable chatbot placeholder: %s", match.group(0))
                return match.group(0)
            return html.escape(str(value))

        return _SUBSTITUTION_PATTERN.sub(replace, text)

    async def send(self, reply: ClientReply) -> None:
        async with self._send_lock:
            if self._closed:
                raise RuntimeError("Wikkit conversation is closed")
            if "say" in reply:
                say = reply["say"]
                if not isinstance(say, str):
                    raise TypeError("say must be a string")
                reply = {**reply, "say": self.substitute(say)}
            await self.websocket.send_json(reply)


    async def replies(self, options: list[str]) -> None:
        await self.send({"say": random.choice(options)}) if options else None


    async def request_response(self, say: str | None = None) -> str:
        """Pause this workflow until the next nonempty, valid user message."""
        if self._response is not None:
            raise RuntimeError("Wikkit is already waiting for a response")
        response = asyncio.get_running_loop().create_future()
        self._response = response
        reply: ClientReply = {"command": {"does": "request-response"}}
        if say is not None:
            reply["say"] = say
        try:
            await self.send(reply)
            return await response
        finally:
            self._response = None
            if not response.done():
                response.cancel()

    async def handle_message(self, message: str) -> None:
        """Override for async conversation workflows that request responses."""
        await self.send(await respond(self.stack, message))

    async def open_page(self, page: str) -> None:
        """Cancel the previous page's workflow, then start this page's, if any."""
        previous, self._workflow = self._workflow, None
        if previous is not None and not previous.done():
            previous.cancel()
            with suppress(asyncio.CancelledError):
                await previous
        workflow = self.workflows.get(page)
        if workflow is not None:
            self._workflow = asyncio.create_task(self._run_workflow(workflow))

    async def _run_workflow(self, workflow: Workflow) -> None:
        try:
            await workflow(self)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Wikkit page workflow failed")
            with suppress(RuntimeError):
                await self.send({"say": FAILURE_MESSAGE})

    async def _dispatch(self) -> None:
        while True:
            message = await self._messages.get()
            self.short_term_memory["last_user_input"] = message
            await self.handle_message(message)

    async def _receive(self) -> None:
        while True:
            try:
                event = await self.websocket.receive()
            except WebSocketDisconnect:
                return
            if event["type"] == "websocket.disconnect":
                return
            text = event.get("text")
            if text is None:
                await self.websocket.close(
                    code=1003, reason="Wikkit accepts text messages only."
                )
                return
            message = text.strip()
            if not message:
                continue
            page = page_event(message)
            if page is not None:
                await self.open_page(page)
            elif len(message) > MAX_MESSAGE_LENGTH:
                reply: ClientReply = {"say": TOO_LONG_MESSAGE}
                if self._response is not None and not self._response.done():
                    reply["command"] = {"does": "request-response"}
                await self.send(reply)
            elif self._response is not None and not self._response.done():
                self.short_term_memory["last_user_input"] = message
                self._response.set_result(message)
            else:
                await self._messages.put(message)

    async def run(self) -> None:
        receiver = asyncio.create_task(self._receive())
        worker = asyncio.create_task(self._dispatch())
        try:
            done, _ = await asyncio.wait(
                (receiver, worker), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            self._closed = True
            if self._response is not None:
                self._response.cancel()
            tasks = [receiver, worker]
            if self._workflow is not None:
                tasks.append(self._workflow)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
