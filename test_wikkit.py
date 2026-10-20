import unittest
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import app
from llm import IntentDispatchResult
from routers import wikkit
import wikkit as chatbot


def dispatched(handler_result: object) -> IntentDispatchResult[object]:
    return IntentDispatchResult(intent="help", entities=[], handler_result=handler_result)


class WikkitPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_home_redirects_to_wikkit(self) -> None:
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/wikkit")

    def test_get_loads_default_page_in_iframe(self) -> None:
        response = self.client.get("/wikkit")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="chatbot"', response.text)
        self.assertIn('id="chatinput" contenteditable="plaintext-only"', response.text)
        self.assertIn('data-src="/wikkit/magic"', response.text)
        self.assertIn('data-socket="/wikkit/ws"', response.text)
        self.assertIn('src="/static/javascript/wikkit.js"', response.text)
        self.assertIn('href="/static/css/router.css"', response.text)

    def test_post_loads_requested_url_in_iframe(self) -> None:
        response = self.client.post("/wikkit", data={"url": "/poster/create?step=1&a=b"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-src="/poster/create?step=1&amp;a=b"', response.text)

    def test_chatbot_loads_aladin_font(self) -> None:
        response = self.client.get("/wikkit")
        self.assertIn(
            'href="https://fonts.googleapis.com/css2?family=Aladin&display=swap"',
            response.text,
        )
        self.assertNotIn("family=Henny+Penny", response.text)

    def test_post_without_url_loads_default_page(self) -> None:
        response = self.client.post("/wikkit")
        self.assertIn('data-src="/wikkit/magic"', response.text)

    def test_frame_url_rejects_non_same_origin_urls(self) -> None:
        for url in [
            "", "https://evil.example", "//evil.example", "/\\evil.example",
            "/\t/evil.example", "javascript:alert(1)", "relative/path",
        ]:
            with self.subTest(url=url):
                self.assertEqual(wikkit.frame_url(url), wikkit.DEFAULT_URL)
        self.assertEqual(wikkit.frame_url(" /wikkit/help "), "/wikkit/help")

    def test_post_escapes_url_attribute(self) -> None:
        response = self.client.post("/wikkit", data={"url": '/x"><script>alert(1)</script>'})
        self.assertNotIn("<script>alert(1)", response.text)
        self.assertIn("&#34;&gt;&lt;script&gt;", response.text)

    def test_magic_page_loads_wikkit_and_connects(self) -> None:
        response = self.client.get("/wikkit/magic")
        self.assertEqual(response.status_code, 200)
        self.assertIn('src="/static/javascript/wikkit.js"', response.text)
        self.assertIn("connect(", response.text)
        self.assertNotIn("/static/css/pages/wikkit-magic.css", response.text)
        self.assertNotIn("/static/javascript/pages/wikkit-magic.js", response.text)

    def test_page_assets_are_included_when_present(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            static_dir = Path(temporary_directory)
            css_dir = static_dir / "css" / "pages"
            javascript_dir = static_dir / "javascript" / "pages"
            css_dir.mkdir(parents=True)
            javascript_dir.mkdir(parents=True)
            (css_dir / "wikkit-magic.css").touch()
            (javascript_dir / "wikkit-magic.js").touch()
            (javascript_dir / "wikkit-magic.does").write_text(
                "registerWikkitCommands({commands: {}});", encoding="utf-8"
            )

            with patch.object(wikkit, "STATIC_DIR", static_dir):
                response = self.client.get("/wikkit/magic")
                shell = self.client.get("/wikkit")
            asset_app = FastAPI()
            asset_app.mount("/static", StaticFiles(directory=static_dir))
            asset_response = TestClient(asset_app).get(
                "/static/javascript/pages/wikkit-magic.does"
            )

        self.assertIn(
            'href="/static/css/pages/wikkit-magic.css"',
            response.text,
        )
        self.assertIn(
            '<script src="/static/javascript/pages/wikkit-magic.js" defer></script>',
            response.text,
        )
        self.assertIn(
            '<script src="/static/javascript/pages/wikkit-magic.does" defer></script>',
            response.text,
        )
        self.assertLess(
            response.text.index('src="/static/javascript/wikkit.js"'),
            response.text.index('src="/static/javascript/pages/wikkit-magic.does"'),
        )
        self.assertNotIn("wikkit-magic.does", shell.text)
        self.assertEqual(asset_response.status_code, 200)
        self.assertTrue(asset_response.headers["content-type"].startswith("text/javascript"))

    def test_each_optional_asset_is_independent(self) -> None:
        for asset_type, extension, key in (
            ("css", "css", "css"),
            ("javascript", "js", "javascript"),
            ("javascript", "does", "does"),
        ):
            with self.subTest(extension=extension), TemporaryDirectory() as directory:
                static_dir = Path(directory)
                asset = static_dir / asset_type / "pages" / f"wikkit.{extension}"
                asset.parent.mkdir(parents=True)
                asset.touch()
                with patch.object(wikkit, "STATIC_DIR", static_dir):
                    assets = wikkit.page_assets("wikkit")
                    for method in (self.client.get, self.client.post):
                        response = method("/wikkit")
                        self.assertIn(f"/static/{asset_type}/pages/wikkit.{extension}", response.text)
                self.assertEqual(
                    {name for name, url in assets.items() if url is not None}, {key}
                )

    def test_invalid_page_asset_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            wikkit.page_assets("../secret")

    def test_static_client_assets_are_served(self) -> None:
        script = self.client.get("/static/javascript/wikkit.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("window.connect", script.text)
        stylesheet = self.client.get("/static/css/router.css")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("text/css", stylesheet.headers["content-type"])
        self.assertIn("#chatbot {", stylesheet.text)


class WikkitReplyTests(unittest.TestCase):
    def test_redirect_becomes_client_command(self) -> None:
        reply = chatbot.client_reply(dispatched({"type": "redirect", "url": "/wikkit/help"}))
        self.assertEqual(reply, {"command": {"does": "redirect", "payload": "/wikkit/help"}})

    def test_canonical_and_legacy_handler_results(self) -> None:
        for result in [
            {"say": "Hello", "command": {"does": "highlight", "payload": [{"id": 1}]}},
            {"user": "Hello", "client": {"action": "highlight", "payload": [{"id": 1}]}},
        ]:
            with self.subTest(result=result):
                self.assertEqual(chatbot.client_reply(dispatched(result)), {
                    "say": "Hello",
                    "command": {"does": "highlight", "payload": [{"id": 1}]},
                })

    def test_responses_are_selected_randomly(self) -> None:
        with patch.object(chatbot.random, "choice", return_value="Second") as choose:
            reply = chatbot.client_reply(dispatched({"responses": ["First", "Second"]}))
        choose.assert_called_once_with(["First", "Second"])
        self.assertEqual(reply, {"say": "Second"})

    def test_omitted_payload_is_not_changed_to_null(self) -> None:
        self.assertEqual(
            chatbot.client_reply(dispatched({"command": {"does": "highlight"}})),
            {"command": {"does": "highlight"}},
        )

    def test_invalid_results_raise_explicit_errors(self) -> None:
        for result in [
            {"responses": []}, {"say": 42}, {"command": "execute"},
            {"command": {"does": " "}},
        ]:
            with self.subTest(result=result), self.assertRaises((TypeError, ValueError)):
                chatbot.client_reply(dispatched(result))

    def test_unhandled_and_unsupported_results_tell_user(self) -> None:
        self.assertEqual(chatbot.client_reply(None), {"say": chatbot.UNHANDLED_MESSAGE})
        with self.assertLogs("wikkit", "WARNING"):
            reply = chatbot.client_reply(dispatched("<b>raw</b>"))
        self.assertEqual(reply, {"say": chatbot.UNHANDLED_MESSAGE})

    def test_dispatch_failure_tells_user(self) -> None:
        stack = chatbot.IntentStack()
        with patch.object(stack, "dispatch", side_effect=RuntimeError("down")):
            with self.assertLogs("wikkit", "ERROR"):
                self.assertEqual(chatbot.respond(stack, "help"), {"say": chatbot.FAILURE_MESSAGE})

    def test_overlong_message_is_not_dispatched(self) -> None:
        stack = chatbot.IntentStack()
        with patch.object(stack, "dispatch") as dispatch:
            reply = chatbot.respond(stack, "x" * (chatbot.MAX_MESSAGE_LENGTH + 1))
        dispatch.assert_not_called()
        self.assertEqual(reply, {"say": chatbot.TOO_LONG_MESSAGE})


class WikkitSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_socket_dispatches_messages_through_one_intent_stack(self) -> None:
        stacks: list[chatbot.IntentStack] = []
        original_dispatch = chatbot.IntentStack.dispatch

        def fake_dispatch(stack: chatbot.IntentStack, message: str):
            stacks.append(stack)
            if message == "help me":
                return dispatched({"type": "redirect", "url": "/wikkit/help"})
            return None

        with patch.object(chatbot.IntentStack, "dispatch", fake_dispatch):
            with self.client.websocket_connect("/wikkit/ws") as socket:
                socket.send_text("   ")
                socket.send_text("  help me  ")
                self.assertEqual(
                    socket.receive_json(),
                    {"command": {"does": "redirect", "payload": "/wikkit/help"}},
                )
                socket.send_text("undo")
                self.assertEqual(socket.receive_json(), {"say": chatbot.UNHANDLED_MESSAGE})
        self.assertIs(chatbot.IntentStack.dispatch, original_dispatch)
        self.assertEqual(len(stacks), 2)
        self.assertIs(stacks[0], stacks[1])

    def test_socket_closes_on_binary_message(self) -> None:
        with self.client.websocket_connect("/wikkit/ws") as socket:
            socket.send_bytes(b"\x00")
            with self.assertRaises(WebSocketDisconnect) as closed:
                socket.receive_text()
        self.assertEqual(closed.exception.code, 1003)

    def test_workflow_waits_for_response_before_continuing(self) -> None:
        async def workflow(session: chatbot.Chatbot, message: str) -> None:
            response = await session.request_response("Which conference?")
            await session.send({"say": f"{message}: {response}"})

        with patch.object(chatbot.Chatbot, "handle_message", workflow):
            with self.client.websocket_connect("/wikkit/ws") as socket:
                socket.send_text("start")
                self.assertEqual(socket.receive_json(), {
                    "say": "Which conference?",
                    "command": {"does": "request-response"},
                })
                socket.send_text("  ")
                socket.send_text("x" * (chatbot.MAX_MESSAGE_LENGTH + 1))
                self.assertEqual(socket.receive_json(), {
                    "say": chatbot.TOO_LONG_MESSAGE,
                    "command": {"does": "request-response"},
                })
                socket.send_text("  Example Conference  ")
                self.assertEqual(socket.receive_json(), {"say": "start: Example Conference"})

    def test_server_can_send_before_user_input(self) -> None:
        original_run = chatbot.Chatbot.run

        async def run(session: chatbot.Chatbot) -> None:
            await session.send({"command": {"does": "highlight", "payload": {"id": 1}}})
            await original_run(session)

        with patch.object(chatbot.Chatbot, "run", run):
            with self.client.websocket_connect("/wikkit/ws") as socket:
                self.assertEqual(socket.receive_json(), {
                    "command": {"does": "highlight", "payload": {"id": 1}},
                })

    def test_disconnect_cancels_waiting_workflow(self) -> None:
        cancelled = []

        async def workflow(session: chatbot.Chatbot, message: str) -> None:
            try:
                await session.request_response()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        with patch.object(chatbot.Chatbot, "handle_message", workflow):
            with self.client.websocket_connect("/wikkit/ws") as socket:
                socket.send_text("start")
                self.assertEqual(socket.receive_json(), {"command": {"does": "request-response"}})
        self.assertEqual(cancelled, [True])


class WikkitAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_after_close_fails_explicitly(self) -> None:
        session = chatbot.Chatbot(AsyncMock())
        session._closed = True
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await session.send({"say": "Hello"})

    async def test_only_one_response_wait_can_be_active(self) -> None:
        session = chatbot.Chatbot(AsyncMock())
        waiting = asyncio.create_task(session.request_response())
        await asyncio.sleep(0)
        try:
            with self.assertRaisesRegex(RuntimeError, "already waiting"):
                await session.request_response()
        finally:
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
        self.assertIsNone(session._response)


if __name__ == "__main__":
    unittest.main()
