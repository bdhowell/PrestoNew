import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import app
from llm import IntentDispatchResult
from routers import wikkit


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

    def test_post_loads_requested_url_in_iframe(self) -> None:
        response = self.client.post("/wikkit", data={"url": "/poster/create?step=1&a=b"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-src="/poster/create?step=1&amp;a=b"', response.text)

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

    def test_static_client_assets_are_served(self) -> None:
        script = self.client.get("/static/javascript/wikkit.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("window.connect", script.text)
        self.assertEqual(self.client.get("/static/javascript/wikkit/router.css").status_code, 200)


class WikkitReplyTests(unittest.TestCase):
    def test_redirect_becomes_client_command(self) -> None:
        reply = wikkit.client_reply(dispatched({"type": "redirect", "url": "/wikkit/help"}))
        self.assertEqual(reply, {"client": {"action": "redirect", "payload": "/wikkit/help"}})

    def test_unhandled_and_unsupported_results_tell_user(self) -> None:
        self.assertEqual(wikkit.client_reply(None), {"user": wikkit.UNHANDLED_MESSAGE})
        with self.assertLogs("routers.wikkit", "WARNING"):
            reply = wikkit.client_reply(dispatched("<b>raw</b>"))
        self.assertEqual(reply, {"user": wikkit.UNHANDLED_MESSAGE})

    def test_dispatch_failure_tells_user(self) -> None:
        stack = wikkit.IntentStack()
        with patch.object(stack, "dispatch", side_effect=RuntimeError("down")):
            with self.assertLogs("routers.wikkit", "ERROR"):
                self.assertEqual(wikkit.respond(stack, "help"), {"user": wikkit.FAILURE_MESSAGE})

    def test_overlong_message_is_not_dispatched(self) -> None:
        stack = wikkit.IntentStack()
        with patch.object(stack, "dispatch") as dispatch:
            reply = wikkit.respond(stack, "x" * (wikkit.MAX_MESSAGE_LENGTH + 1))
        dispatch.assert_not_called()
        self.assertEqual(reply, {"user": wikkit.TOO_LONG_MESSAGE})


class WikkitSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_socket_dispatches_messages_through_one_intent_stack(self) -> None:
        stacks: list[wikkit.IntentStack] = []
        original_dispatch = wikkit.IntentStack.dispatch

        def fake_dispatch(stack: wikkit.IntentStack, message: str):
            stacks.append(stack)
            if message == "help me":
                return dispatched({"type": "redirect", "url": "/wikkit/help"})
            return None

        with patch.object(wikkit.IntentStack, "dispatch", fake_dispatch):
            with self.client.websocket_connect("/wikkit/ws") as socket:
                socket.send_text("   ")
                socket.send_text("  help me  ")
                self.assertEqual(
                    socket.receive_json(),
                    {"client": {"action": "redirect", "payload": "/wikkit/help"}},
                )
                socket.send_text("undo")
                self.assertEqual(socket.receive_json(), {"user": wikkit.UNHANDLED_MESSAGE})
        self.assertIs(wikkit.IntentStack.dispatch, original_dispatch)
        self.assertEqual(len(stacks), 2)
        self.assertIs(stacks[0], stacks[1])

    def test_socket_closes_on_binary_message(self) -> None:
        with self.client.websocket_connect("/wikkit/ws") as socket:
            socket.send_bytes(b"\x00")
            with self.assertRaises(WebSocketDisconnect) as closed:
                socket.receive_text()
        self.assertEqual(closed.exception.code, 1003)


if __name__ == "__main__":
    unittest.main()
