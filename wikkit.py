"""Conversation-local Wikkit protocol, server sends, and response waiting."""

import asyncio
import logging
import random
from collections.abc import Mapping
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from intents import IntentStack
from llm import IntentDispatchResult

MAX_MESSAGE_LENGTH = 2000
UNHANDLED_MESSAGE = "Sorry, I can't help with that yet."
FAILURE_MESSAGE = "Sorry, I had trouble understanding that. Please try again."
TOO_LONG_MESSAGE = (
    f"That message is too long. Please keep it under {MAX_MESSAGE_LENGTH} characters."
)

logger = logging.getLogger(__name__)
type ClientReply = dict[str, object]


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


def respond(stack: IntentStack, message: str) -> ClientReply:
    if len(message) > MAX_MESSAGE_LENGTH:
        return {"say": TOO_LONG_MESSAGE}
    try:
        return client_reply(stack.dispatch(message))
    except Exception:
        logger.exception("Wikkit failed to handle a message")
        return {"say": FAILURE_MESSAGE}


class Chatbot:
    """One instance per socket; retain it to send unsolicited server messages."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.stack = IntentStack()
        self._messages: asyncio.Queue[str] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._response: asyncio.Future[str] | None = None
        self._closed = False

    async def send(self, reply: ClientReply) -> None:
        async with self._send_lock:
            if self._closed:
                raise RuntimeError("Wikkit conversation is closed")
            await self.websocket.send_json(reply)

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
        await self.send(await run_in_threadpool(respond, self.stack, message))

    async def _dispatch(self) -> None:
        while True:
            await self.handle_message(await self._messages.get())

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
            if len(message) > MAX_MESSAGE_LENGTH:
                reply: ClientReply = {"say": TOO_LONG_MESSAGE}
                if self._response is not None and not self._response.done():
                    reply["command"] = {"does": "request-response"}
                await self.send(reply)
            elif self._response is not None and not self._response.done():
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
            for task in (receiver, worker):
                task.cancel()
            for task in (receiver, worker):
                with suppress(asyncio.CancelledError):
                    await task
