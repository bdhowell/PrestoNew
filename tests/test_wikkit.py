import unittest
import asyncio
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright, expect
from starlette.websockets import WebSocketDisconnect

with patch.dict(os.environ, {"SESSION_BACKEND": "none"}):
    from app import app
from llm import IntentDispatchResult
import identity
from routers import wikkit
import wikkit as chatbot


def dispatched(handler_result: object) -> IntentDispatchResult[object]:
    return IntentDispatchResult(intent="help", entities=[], handler_result=handler_result)

class ConfiguredTestClient(TestClient):
    @contextmanager
    def websocket_connect(self, *args, **kwargs):
        with super().websocket_connect(*args, **kwargs) as socket:
            configuration = socket.receive_json()
            assert configuration == {
                "command": {"does": "configure", "payload": wikkit.chat_word_timing()},
            }
            yield socket


class WikkitPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_home_redirects_to_magic_page(self) -> None:
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/wikkit/magic")

    def test_get_redirects_to_default_page_without_iframe(self) -> None:
        redirect = self.client.get("/wikkit", follow_redirects=False)
        self.assertEqual(redirect.status_code, 303)
        self.assertEqual(redirect.headers["location"], "/wikkit/magic")
        response = self.client.get("/wikkit")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<nav class="basic-menu" aria-label="Main navigation">', response.text)
        self.assertIn('href="/wikkit/pricing">Pricing</a>', response.text)
        self.assertIn('href="/account/signin">Sign in</a>', response.text)
        self.assertNotIn('href="/account/signout">Sign out</a>', response.text)
        self.assertNotIn("<iframe", response.text)
        self.assertNotIn('id="chatbot"', response.text)  # Created by the client.
        self.assertIn('data-socket="/wikkit/ws"', response.text)
        self.assertIn('data-chat-window-transition="440"', response.text)
        self.assertIn('data-chat-input-transition="500"', response.text)
        self.assertIn('src="/static/javascript/wikkit.js"', response.text)
        self.assertIn('href="/static/css/router.css"', response.text)

    def test_chat_window_transition_uses_configured_duration(self) -> None:
        with patch.dict(os.environ, {"CHAT_WINDOW_TRANSITION": "125"}):
            response = self.client.get("/wikkit")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-chat-window-transition="125"', response.text)

    def test_chat_input_transition_uses_configured_duration(self) -> None:
        with patch.dict(os.environ, {"CHAT_INPUT_TRANSITION": "250"}):
            response = self.client.get("/wikkit")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-chat-input-transition="250"', response.text)

    def test_chat_word_timing_defaults_and_configuration(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("CHAT_WORD_INTERVAL", None)
            os.environ.pop("CHAT_WORD_DEVIATION", None)
            self.assertEqual(wikkit.chat_word_timing(), {
                "CHAT_WORD_INTERVAL": 400, "CHAT_WORD_DEVIATION": 100,
            })
        for interval, deviation in ((400, 100), (75, 25), (0, 0)):
            with self.subTest(interval=interval), patch.dict(os.environ, {
                "CHAT_WORD_INTERVAL": str(interval), "CHAT_WORD_DEVIATION": str(deviation),
            }):
                response = self.client.get("/wikkit")
                self.assertNotIn("data-chat-word-timing", response.text)
                self.assertEqual(wikkit.chat_word_timing(), {
                    "CHAT_WORD_INTERVAL": interval, "CHAT_WORD_DEVIATION": deviation,
                })

    def test_invalid_chat_word_timing_is_reported(self) -> None:
        for interval, deviation in (("-1", "0"), ("400", "-1"), ("50", "100"), ("bad", "0")):
            with self.subTest(interval=interval, deviation=deviation), patch.dict(os.environ, {
                "CHAT_WORD_INTERVAL": interval, "CHAT_WORD_DEVIATION": deviation,
            }):
                with self.assertRaises(ValueError):
                    wikkit.chat_word_timing()

    def test_authenticated_user_sees_sign_out_link(self) -> None:
        request = wikkit.Request({
            "type": "http",
            "method": "GET",
            "path": "/wikkit",
            "headers": [],
            "query_string": b"",
            "state": {"user_id": 1},
        })

        response = wikkit.render_page(request, "wikkit/magic.html", "wikkit-magic")
        rendered = response.body.decode()

        self.assertIn('href="/account/signout">Sign out</a>', rendered)
        self.assertNotIn('href="/account/signin">Sign in</a>', rendered)

    def test_post_redirects_to_requested_url(self) -> None:
        response = self.client.post(
            "/wikkit", data={"url": "/poster/create?step=1&a=b"}, follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/poster/create?step=1&a=b")

    def test_chatbot_loads_aladin_font(self) -> None:
        response = self.client.get("/wikkit")
        self.assertIn(
            'href="https://fonts.googleapis.com/css2?family=Aladin&display=swap"',
            response.text,
        )
        self.assertNotIn("family=Henny+Penny", response.text)

    def test_post_without_url_loads_default_page(self) -> None:
        response = self.client.post("/wikkit", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/wikkit/magic")

    def test_navigation_url_rejects_non_same_origin_urls(self) -> None:
        for url in [
            "", "https://evil.example", "//evil.example", "/\\evil.example",
            "/\t/evil.example", "javascript:alert(1)", "relative/path",
        ]:
            with self.subTest(url=url):
                self.assertEqual(wikkit.navigation_url(url), wikkit.DEFAULT_URL)
                response = self.client.post("/wikkit", data={"url": url}, follow_redirects=False)
                self.assertEqual(response.headers["location"], wikkit.DEFAULT_URL)
        self.assertEqual(wikkit.navigation_url(" /wikkit/help "), "/wikkit/help")

    def test_post_does_not_render_url_as_html(self) -> None:
        response = self.client.post(
            "/wikkit", data={"url": '/x"><script>alert(1)</script>'}, follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("<script>alert(1)", response.text)
        self.assertNotIn("<script>", response.headers["location"])

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
                asset = static_dir / asset_type / "pages" / f"wikkit-magic.{extension}"
                asset.parent.mkdir(parents=True)
                asset.touch()
                with patch.object(wikkit, "STATIC_DIR", static_dir):
                    assets = wikkit.page_assets("wikkit-magic")
                    for method in (self.client.get, self.client.post):
                        response = method("/wikkit")
                        self.assertIn(f"/static/{asset_type}/pages/wikkit-magic.{extension}", response.text)
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
        self.assertIn("z-index: 9999;", stylesheet.text)
        self.assertNotIn("#wikkit-frame", stylesheet.text)
        self.assertIn("#chatbot.chat-hidden {", stylesheet.text)
        self.assertIn('"hide-chat"', script.text)
        self.assertIn('"show-chat"', script.text)
        self.assertIn('"close-input"', script.text)
        self.assertIn('"open-input"', script.text)
        self.assertIn("#chatbot.input-closed #chatinput {", stylesheet.text)

    def test_public_pages_include_overlay_assets_and_menu(self) -> None:
        for path in ("/wikkit/magic", "/wikkit/help", "/wikkit/pricing", "/account/signin"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("<iframe", response.text)
                self.assertIn('src="/static/javascript/wikkit.js"', response.text)
                self.assertIn('href="/static/css/router.css"', response.text)
                self.assertIn('<nav class="basic-menu"', response.text)
                self.assertIn('data-socket="/wikkit/ws"', response.text)
                self.assertIn("window.wikkit = connect(", response.text)


class WikkitBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        request = wikkit.Request({
            "type": "http", "method": "GET", "path": "/layout/dimensions",
            "headers": [], "query_string": b"", "state": {"user_id": 7},
        })
        self.word_interval = 0
        self.word_deviation = 0
        self.prompt_on_connect = True
        client = TestClient(app)
        self.addCleanup(client.close)

        async def serve(route) -> None:
            path = urlsplit(route.request.url).path
            if path == "/layout/dimensions":
                rendered = wikkit.render_page(
                    request, "layout/dimensions.html", "layout-dimensions",
                )
                await route.fulfill(body=rendered.body, content_type="text/html")
            elif path.startswith("/static/"):
                response = client.get(path)
                await route.fulfill(
                    status=response.status_code, body=response.content,
                    content_type=response.headers["content-type"],
                )
            else:
                await route.abort()

        def connect(socket) -> None:
            self.socket = socket
            socket.send(json.dumps({"command": {"does": "configure", "payload": {
                "CHAT_WORD_INTERVAL": self.word_interval,
                "CHAT_WORD_DEVIATION": self.word_deviation,
            }}}))

            def receive(message) -> None:
                if self.prompt_on_connect and json.loads(message).get("page") == "layout-dimensions":
                    socket.send(json.dumps({
                        "say": "<p>Enter your poster height x width.</p>",
                        "command": {"does": "request-response"},
                    }))
            socket.on_message(receive)

        await self.page.route("**/*", serve)
        await self.page.route_web_socket("**/wikkit/ws", connect)

    async def test_loggedout_waits_for_login_button_without_a_socket(self) -> None:
        client = TestClient(app)
        self.addCleanup(client.close)
        client.cookies.set("presto_user", "Ada")
        response = client.get("/system/loggedout/")
        await self.page.route(
            "**/system/loggedout/",
            lambda route: route.fulfill(body=response.content, content_type="text/html"),
        )
        await self.page.route(
            "**/account/signin",
            lambda route: route.fulfill(body="<h1>Sign in</h1>", content_type="text/html"),
        )
        await self.page.add_init_script("""() => {
            sessionStorage.setItem('wikkit.chatbot', JSON.stringify({
                content: '<header class="chatbot-handle">Wikkit</header>'
                    + '<div id="chatlog"><div class="chat">'
                    + '<button data-sends="login" disabled>Sign in again</button>'
                    + '</div></div><div id="chatinput"></div>',
                geometry: {left: 0, top: 0, width: 400, height: 300},
            }));
        }""")
        await self.page.clock.install()
        await self.page.goto("http://testserver/system/loggedout/")
        await self.page.clock.run_for(15000)
        button = self.page.locator('#chatlog button[data-sends="login"]')
        await expect(button).to_be_enabled()
        await expect(button).to_be_focused()
        await expect(self.page.locator("#chatlog")).to_contain_text(
            "Ada, you’ve been logged out because your session expired.",
        )
        self.assertFalse(hasattr(self, "socket"))
        self.assertEqual(
            await self.page.locator("main a").get_attribute("href"), "/account/signin",
        )
        await self.page.clock.run_for(180000)
        await expect(button).to_be_enabled()
        await expect(self.page.locator("#chatlog")).to_contain_text("Are you still there?")
        await button.click()
        await self.page.wait_for_url("**/account/signin")
        self.assertEqual(self.errors, [])

    async def start_timed_output(self) -> None:
        self.word_interval = 400
        self.word_deviation = 100
        self.prompt_on_connect = False
        await self.page.goto("http://wikkit.test/layout/dimensions")
        await self.page.clock.install()
        await self.page.clock.pause_at(datetime.now() + timedelta(seconds=1))

    async def test_output_preserves_html_whitespace_and_queues_commands(self) -> None:
        await self.start_timed_output()
        markup = '  First\t <strong>second</strong>\n\nthird  <button data-sends="ok">Done</button> '
        self.socket.send(json.dumps({
            "say": markup, "command": {"does": "close-input"},
        }))
        await self.page.locator("#chatlog .chat").wait_for()
        await self.page.wait_for_function(
            "document.querySelector('#chatlog .chat').textContent.includes('First')"
        )
        self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), "  First\t Done")
        self.assertTrue(await self.page.locator("#chatlog .chat").evaluate("el => el.inert"))
        await self.page.clock.fast_forward(299)
        self.assertEqual(await self.page.locator("#chatlog strong").text_content(), "")
        await self.page.clock.fast_forward(201)
        self.assertEqual(await self.page.locator("#chatlog strong").text_content(), "second")
        self.assertFalse(await self.page.locator("#chatbot").evaluate(
            "el => el.classList.contains('input-closed')"
        ))
        self.socket.send(json.dumps({"say": "<p>Next message</p>"}))
        await self.page.clock.run_for(2000)
        await expect(self.page.locator("#chatlog .chat")).to_have_count(2)
        await self.page.clock.run_for(1000)
        await expect(self.page.locator("#chatlog [aria-busy]")).to_have_count(0)
        self.assertEqual(
            await self.page.locator("#chatlog .chat").first.text_content(),
            "  First\t second\n\nthird  Done ",
        )
        self.assertEqual(await self.page.locator("#chatlog strong").text_content(), "second")
        self.assertEqual(await self.page.locator("#chatlog button").get_attribute("data-sends"), "ok")
        self.assertEqual(await self.page.locator("#chatlog .chat").last.text_content(), "Next message")
        self.assertFalse(await self.page.locator("#chatlog .chat").first.evaluate("el => el.inert"))
        await expect(self.page.locator("#chatbot")).to_have_class("input-closed")
        self.assertEqual(self.errors, [])

    async def test_embedded_controls_render_in_full_before_text_finishes(self) -> None:
        await self.start_timed_output()
        await self.page.evaluate("""() => {
            window.wikkit.say(
                '<p>First second third</p>'
                + '<button data-sends="ok"><strong>Use these</strong> dimensions</button>'
                + '<label>Poster name <input value="My poster"></label>'
                + '<textarea>Full initial text</textarea>'
                + '<select><option>First option</option><option>Second option</option></select>'
            );
        }""")
        await expect(self.page.locator("#chatlog .chat")).to_have_attribute("aria-busy", "true")
        self.assertEqual(await self.page.locator("#chatlog p").text_content(), "First ")
        self.assertEqual(
            await self.page.locator("#chatlog button[data-sends]").text_content(),
            "Use these dimensions",
        )
        self.assertEqual(await self.page.locator("#chatlog input").input_value(), "My poster")
        self.assertEqual(await self.page.locator("#chatlog label").text_content(), "Poster name OK")
        self.assertEqual(await self.page.locator("#chatlog textarea").input_value(), "Full initial text")
        self.assertEqual(
            await self.page.locator("#chatlog option").all_text_contents(),
            ["First option", "Second option"],
        )
        self.assertEqual(await self.page.locator("#chatlog button.embedded-ok").all_text_contents(), ["OK", "OK"])
        await self.page.clock.run_for(1000)
        self.assertEqual(await self.page.locator("#chatlog p").text_content(), "First second third")
        await expect(self.page.locator("#chatlog [aria-busy]")).to_have_count(0)
        self.assertEqual(self.errors, [])

    async def test_word_delay_reaches_configured_jitter_bounds(self) -> None:
        await self.start_timed_output()
        for random, delay in ((0, 300), (0.5, 400), (0.999999, 500)):
            with self.subTest(random=random):
                await self.page.evaluate("""random => {
                    Math.random = () => random;
                    window.wikkit.say('one  \\t\\ntwo');
                }""", random)
                message = self.page.locator("#chatlog .chat").last
                await expect(message).to_have_attribute("aria-busy", "true")
                self.assertEqual(await message.text_content(), "one  \t\n")
                await self.page.clock.run_for(delay - 1)
                self.assertEqual(await message.text_content(), "one  \t\n")
                await self.page.clock.run_for(1)
                self.assertEqual(await message.text_content(), "one  \t\ntwo")
                self.assertIsNone(await message.get_attribute("aria-busy"))

    async def test_configure_updates_current_output_without_waiting_for_queue(self) -> None:
        await self.page.add_init_script("""{
            const OriginalWebSocket = WebSocket;
            window.WebSocket = class extends OriginalWebSocket {
                constructor(...args) {
                    super(...args);
                    this.addEventListener('message', event => {
                        const command = JSON.parse(event.data).command;
                        if (command?.does === 'configure') window.lastConfiguration = command.payload;
                    });
                }
            };
        }""")
        await self.start_timed_output()
        await self.page.evaluate("""() => {
            Math.random = () => 0.5;
            window.wikkit.say('one two three four');
        }""")
        message = self.page.locator("#chatlog .chat")
        self.assertEqual(await message.text_content(), "one ")
        self.socket.send(json.dumps({"command": {"does": "configure", "payload": {
            "CHAT_WORD_INTERVAL": 100, "CHAT_WORD_DEVIATION": 0,
        }}}))
        await self.page.wait_for_function("window.lastConfiguration?.CHAT_WORD_INTERVAL === 100")
        # The pending delay retains its original duration; later delays use the update.
        await self.page.clock.run_for(400)
        self.assertEqual(await message.text_content(), "one two ")
        await self.page.clock.run_for(99)
        self.assertEqual(await message.text_content(), "one two ")
        await self.page.clock.run_for(1)
        self.assertEqual(await message.text_content(), "one two three ")
        await self.page.evaluate("window.wikkit.execute({does: 'configure', payload: {CHAT_WORD_INTERVAL: 0}})")
        await self.page.clock.run_for(100)
        self.assertEqual(await message.text_content(), "one two three four")
        await self.page.evaluate("window.wikkit.say('five six seven')")
        await expect(self.page.locator("#chatlog .chat").last).to_have_text("five six seven")
        self.assertEqual(self.errors, [])

    async def test_configure_validates_updates_atomically_and_retains_other_values(self) -> None:
        await self.start_timed_output()
        for payload in (None, [], {"CHAT_WORD_INTERVAL": -1}, {"CHAT_WORD_INTERVAL": "100"},
                        {"CHAT_WORD_DEVIATION": 500}, {"CHAT_WORD_INTERVAL": 1.5}):
            with self.subTest(payload=payload):
                result = await self.page.evaluate("""async payload => {
                    try {
                        await window.wikkit.execute({does: 'configure', payload});
                        return 'accepted';
                    } catch (error) {
                        return error.name;
                    }
                }""", payload)
                self.assertIn(result, ("TypeError", "RangeError"))
        await self.page.evaluate("""async () => {
            await window.wikkit.execute({does: 'configure', payload: {custom: {enabled: true}}});
            Math.random = () => 0.5;
            window.wikkit.say('one two');
        }""")
        await self.page.clock.run_for(399)
        self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), "one ")
        await self.page.clock.run_for(1)
        self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), "one two")

    async def test_whitespace_only_and_inline_word_fragments(self) -> None:
        await self.start_timed_output()
        for markup, expected in ((" \t\n ", " \t\n "), ("hel<strong>lo</strong> \tworld", "hello \t")):
            with self.subTest(markup=markup):
                await self.page.evaluate("window.wikkit.execute({does: 'clear-chat'})")
                await self.page.evaluate("markup => { window.wikkit.say(markup); }", markup)
                self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), expected)
                await self.page.clock.run_for(500)
        self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), "hello \tworld")

    async def test_page_response_request_waits_for_prompt(self) -> None:
        await self.start_timed_output()
        await self.page.evaluate("""() => {
            window.wikkit.say('First second');
            window.response = window.wikkit.requestResponse();
        }""")
        self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), "First ")
        self.assertFalse(await self.page.locator("#chatinput").evaluate(
            "el => document.activeElement === el"
        ))
        await self.page.clock.run_for(500)
        self.assertTrue(await self.page.locator("#chatinput").evaluate(
            "el => document.activeElement === el"
        ))
        await self.page.locator("#chatinput").fill("Okay")
        await self.page.locator("#chatinput").press("Enter")
        self.assertEqual(await self.page.evaluate("window.response"), "Okay")

    async def test_navigation_persists_complete_output_without_replaying(self) -> None:
        await self.start_timed_output()
        await self.page.evaluate("() => { window.wikkit.say('one two three'); }")
        self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), "one ")
        await self.page.reload()
        self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), "one two three")
        await expect(self.page.locator("#chatlog [aria-busy]")).to_have_count(0)
        self.assertFalse(await self.page.locator("#chatlog .chat").evaluate("el => el.inert"))

    async def test_zero_timing_outputs_immediately(self) -> None:
        self.prompt_on_connect = False
        await self.page.goto("http://wikkit.test/layout/dimensions")
        await self.page.evaluate("window.wikkit.say('one  two\\nthree')")
        self.assertEqual(await self.page.locator("#chatlog .chat").text_content(), "one  two\nthree")
        await expect(self.page.locator("#chatlog [aria-busy]")).to_have_count(0)

    async def test_all_output_paths_use_word_animation(self) -> None:
        await self.start_timed_output()
        await self.page.evaluate("Math.random = () => 0")
        for kind in ("say", "user", "page", "echo", "notice"):
            with self.subTest(kind=kind):
                await self.page.evaluate("window.wikkit.execute({does: 'clear-chat'})")
                if kind in ("say", "user"):
                    self.socket.send(json.dumps({kind: "first second"}))
                elif kind == "page":
                    await self.page.evaluate("() => { window.wikkit.say('first second'); }")
                elif kind == "echo":
                    await self.page.evaluate("window.wikkit.command('{\"value\": \"second\"}', true)")
                else:
                    self.socket.send(json.dumps({"command": {"does": "unknown-command"}}))
                message = self.page.locator("#chatlog .chat")
                await expect(message).to_have_attribute("aria-busy", "true")
                initial = await message.text_content()
                await self.page.clock.run_for(299)
                self.assertEqual(await message.text_content(), initial)
                await self.page.clock.run_for(5000)
                await expect(message).not_to_have_attribute("aria-busy")
                self.assertGreater(len(await message.text_content()), len(initial))
        self.assertEqual(self.errors, [])

    async def assert_response_available(self) -> None:
        chatbot = self.page.locator("#chatbot")
        await expect(chatbot).to_be_visible()
        self.assertFalse(await chatbot.evaluate("element => element.inert"))
        self.assertIsNone(await chatbot.get_attribute("aria-hidden"))
        chatinput = self.page.locator("#chatinput")
        await expect(chatinput).to_be_visible()
        self.assertFalse(await chatinput.evaluate("element => element.inert"))
        self.assertIsNone(await chatinput.get_attribute("aria-hidden"))
        self.assertEqual(self.errors, [])

    async def test_dimensions_prompt_reopens_persisted_hidden_chat(self) -> None:
        await self.page.goto("http://wikkit.test/layout/dimensions")
        await self.page.locator("#chatlog").get_by_text("Enter your poster height x width.").wait_for()
        await self.page.evaluate("window.wikkit.execute({does: 'hide-close'})")
        await self.page.wait_for_function(
            "JSON.parse(sessionStorage.getItem('wikkit.chatbot'))['chat-hidden'] === true"
        )
        await self.page.reload()
        await self.page.wait_for_function(
            "document.querySelectorAll('#chatlog .chat').length === 2"
        )
        await self.assert_response_available()

    async def test_request_aliases_reopen_hidden_chat_and_closed_input(self) -> None:
        await self.page.goto("http://wikkit.test/layout/dimensions")
        for command in ("request-response", "request"):
            with self.subTest(command=command):
                await self.page.evaluate("""async command => {
                    await window.wikkit.execute({does: 'hide-close'});
                    await window.wikkit.execute({does: command});
                }""", command)
                await self.assert_response_available()

    async def test_new_output_reveals_chat_without_reopening_input(self) -> None:
        await self.page.goto("http://wikkit.test/layout/dimensions")
        await self.page.locator("#chatlog").get_by_text("Enter your poster height x width.").wait_for()
        for output in ("say", "user", "page", "echo", "notice"):
            with self.subTest(output=output):
                await self.page.evaluate("window.wikkit.execute({does: 'hide-close'})")
                if output in ("say", "user"):
                    self.socket.send(json.dumps({output: f"<p>{output} output</p>"}))
                elif output == "page":
                    await self.page.evaluate("window.wikkit.say('<p>Page output</p>')")
                elif output == "echo":
                    await self.page.evaluate("window.wikkit.command('{}', true)")
                else:
                    self.socket.send(json.dumps({"command": {"does": "unknown-command"}}))
                chatbot = self.page.locator("#chatbot")
                await expect(chatbot).not_to_have_class("chat-hidden input-closed")
                await expect(chatbot).to_be_visible()
                self.assertFalse(await chatbot.evaluate("element => element.inert"))
                self.assertIsNone(await chatbot.get_attribute("aria-hidden"))
                await expect(chatbot).to_have_class("input-closed")
        self.assertEqual(self.errors, [])


class WikkitReplyTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_dispatch_failure_tells_user(self) -> None:
        stack = chatbot.IntentStack()
        with patch.object(stack, "dispatch", side_effect=RuntimeError("down")):
            with self.assertLogs("wikkit", "ERROR"):
                self.assertEqual(
                    await chatbot.respond(stack, "help"),
                    {"say": chatbot.FAILURE_MESSAGE},
                )

    async def test_overlong_message_is_not_dispatched(self) -> None:
        stack = chatbot.IntentStack()
        with patch.object(stack, "dispatch") as dispatch:
            reply = await chatbot.respond(stack, "x" * (chatbot.MAX_MESSAGE_LENGTH + 1))
        dispatch.assert_not_called()
        self.assertEqual(reply, {"say": chatbot.TOO_LONG_MESSAGE})


class WikkitSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ConfiguredTestClient(app)

    def test_socket_sends_configuration_before_output(self) -> None:
        with patch.dict(os.environ, {"CHAT_WORD_INTERVAL": "80", "CHAT_WORD_DEVIATION": "20"}):
            with TestClient(app).websocket_connect("/wikkit/ws") as socket:
                self.assertEqual(socket.receive_json(), {"command": {
                    "does": "configure",
                    "payload": {"CHAT_WORD_INTERVAL": 80, "CHAT_WORD_DEVIATION": 20},
                }})

    def test_memory_frame_stores_before_response_and_accepts_length_limit(self) -> None:
        async def workflow(session: chatbot.Chatbot, message: str) -> None:
            response = await session.request_response("Your answer?")
            await session.send({"say": f"{session.short_term_memory['answer']}: {response}"})

        with patch.object(chatbot.Chatbot, "handle_message", workflow):
            with self.client.websocket_connect("/wikkit/ws") as socket:
                socket.send_text("start")
                self.assertEqual(socket.receive_json()["say"], "Your answer?")
                socket.send_json({"memory": {
                    "id": 1, "scope": "session", "name": "answer", "value": "Ada",
                }})
                self.assertEqual(socket.receive_json(), {"memory": {"id": 1, "ok": True}})
                socket.send_text("Ada")
                self.assertEqual(socket.receive_json(), {"say": "Ada: Ada"})
            with self.client.websocket_connect("/wikkit/ws") as socket:
                socket.send_json({"memory": {
                    "id": 2, "scope": "session", "name": "answer", "value": "x" * 2000,
                }})
                self.assertEqual(socket.receive_json(), {"memory": {"id": 2, "ok": True}})
                socket.send_text("start")
                socket.receive_json()
                socket.send_text("Grace")
                self.assertEqual(socket.receive_json(), {"say": f"{'x' * 2000}: Grace"})

    def test_socket_dispatches_messages_through_one_intent_stack(self) -> None:
        stacks: list[chatbot.IntentStack] = []
        original_dispatch = chatbot.IntentStack.dispatch

        async def fake_dispatch(stack: chatbot.IntentStack, message: str):
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
    def setUp(self) -> None:
        self.websocket = WebSocket({"type": "websocket", "state": {
            "user": {"name": "Ada", "active": False, "count": 0},
            "poster": SimpleNamespace(title="A poster", author={"name": "Grace"}),
            "session": {"id": 42},
        }}, receive=AsyncMock(), send=AsyncMock())
        self.session = chatbot.Chatbot(self.websocket)

    async def test_embedded_memory_scopes_and_acknowledgements(self) -> None:
        self.session.user_memory = AsyncMock()
        self.session.long_term_memory = AsyncMock()
        with patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send:
            for request_id, scope in enumerate(("session", "user", "poster"), 1):
                self.assertTrue(await self.session._remember_control(json.dumps({
                    "memory": {"id": request_id, "scope": scope, "name": "answer", "value": "Ada"},
                })))
                send.assert_awaited_with({"memory": {"id": request_id, "ok": True}})
        self.assertEqual(self.session.short_term_memory["answer"], "Ada")
        self.session.user_memory.set.assert_awaited_once_with("answer", "Ada")
        self.session.long_term_memory.set.assert_awaited_once_with("answer", "Ada")

    async def test_embedded_memory_rejects_invalid_frames_without_writes(self) -> None:
        valid = {"id": 1, "scope": "session", "name": "answer", "value": "Ada"}
        invalid = [
            None, {}, {**valid, "id": True}, {**valid, "scope": "other"},
            {**valid, "name": ""}, {**valid, "name": "x" * 2001},
            {**valid, "value": ""}, {**valid, "value": 42},
            {**valid, "value": "x" * 2001}, {**valid, "user_id": 5},
        ]
        with patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send:
            for data in invalid:
                with self.subTest(data=data), self.assertLogs("wikkit", "WARNING"):
                    self.assertTrue(await self.session._remember_control(json.dumps({"memory": data})))
                    self.assertFalse(send.await_args.args[0]["memory"]["ok"])
        self.assertEqual(self.session.short_term_memory, {})

    async def test_embedded_memory_unavailable_and_storage_failures_are_reported(self) -> None:
        with patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send:
            for scope in ("user", "poster"):
                frame = json.dumps({"memory": {
                    "id": 1, "scope": scope, "name": "answer", "value": "Ada",
                }})
                with self.assertLogs("wikkit", "WARNING"):
                    await self.session._remember_control(frame)
                self.assertIn("unavailable", send.await_args.args[0]["memory"]["error"])
            self.session.long_term_memory = AsyncMock()
            for error in (RuntimeError("private database details"), ValueError("private schema details")):
                self.session.long_term_memory.set.side_effect = error
                with self.assertLogs("wikkit", "ERROR"):
                    await self.session._remember_control(frame)
                self.assertEqual(send.await_args.args[0]["memory"], {
                    "id": 1, "ok": False,
                    "error": "Wikkit could not store that value. Please try again.",
                })

    async def test_embedded_memory_does_not_consume_workflow_response(self) -> None:
        self.websocket._receive = AsyncMock(return_value={"type": "websocket.connect"})
        await self.websocket.accept()
        waiting = asyncio.create_task(self.session.request_response())
        await asyncio.sleep(0)
        self.websocket.receive = AsyncMock(side_effect=[
            {"type": "websocket.receive", "text": json.dumps({"memory": {
                "id": 1, "scope": "session", "name": "answer", "value": "Ada",
            }})},
            {"type": "websocket.receive", "text": "Ada"},
            {"type": "websocket.disconnect"},
        ])
        await self.session._receive()
        self.assertEqual(await waiting, "Ada")
        self.assertEqual(self.session.short_term_memory["answer"], "Ada")
        self.assertTrue(self.session._messages.empty())

    async def test_ordinary_text_and_json_are_not_memory_frames(self) -> None:
        for text in ("Ada", '{"page":"help"}', '{"memory": {}, "other": 1}', "[]"):
            self.assertFalse(await self.session._remember_control(text))

    async def test_substitute_reads_mapping_and_object_fields(self) -> None:
        self.assertEqual(
            self.session.substitute(
                "{{user.name}}: {{poster.title}} by {{poster.author.name}} "
                "({{ session.id }}) {{user.active}}/{{user.count}}/{{user.name}}"
            ),
            "Ada: A poster by Grace (42) False/0/Ada",
        )

    async def test_substitute_leaves_unavailable_values_unchanged(self) -> None:
        self.websocket.state.user = None
        self.websocket.state.poster = {"title": None}
        del self.websocket.state.session
        text = "{{user.name}} {{poster.title}} {{session.id}}"
        with self.assertLogs("wikkit", "WARNING"):
            self.assertEqual(self.session.substitute(text), text)

    async def test_substitute_leaves_missing_fields_and_methods_unchanged(self) -> None:
        text = "{{user.missing}} {{poster.missing.name}} {{user.name.upper}}"
        with self.assertLogs("wikkit", "WARNING"):
            self.assertEqual(self.session.substitute(text), text)

    async def test_substitute_does_not_evaluate_or_recurse(self) -> None:
        self.websocket.state.user = {"name": "{{session.id}}"}
        text = "{{other.name}} {{user.__class__}} {{user.name.upper()}} {{user}}"
        self.assertEqual(self.session.substitute(text), text)
        self.assertEqual(self.session.substitute("{{user.name}}"), "{{session.id}}")
        self.assertEqual(self.session.substitute("Plain text {user.name}"), "Plain text {user.name}")
        self.assertEqual(self.session.substitute(""), "")

    async def test_send_substitutes_only_say_without_mutating_reply(self) -> None:
        reply = {
            "say": "Hello {{user.name}}",
            "command": {"does": "highlight", "payload": "{{poster.title}}"},
        }
        with patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send:
            await self.session.send(reply)
            send.assert_awaited_once_with({
                "say": "Hello Ada",
                "command": {"does": "highlight", "payload": "{{poster.title}}"},
            })
        self.assertEqual(reply["say"], "Hello {{user.name}}")

    async def test_send_uses_current_conversation_state(self) -> None:
        with patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send:
            await self.session.send({"say": "{{user.name}}"})
            self.websocket.state.user = {"name": "Grace"}
            await self.session.send({"say": "{{user.name}}"})
        self.assertEqual(
            [call.args[0] for call in send.await_args_list],
            [{"say": "Ada"}, {"say": "Grace"}],
        )

    async def test_send_rejects_non_string_say(self) -> None:
        with patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send:
            with self.assertRaisesRegex(TypeError, "say must be a string"):
                await self.session.send({"say": 42})
            send.assert_not_awaited()

    async def test_substitute_keeps_conversations_isolated(self) -> None:
        other = chatbot.Chatbot(WebSocket({
            "type": "websocket", "state": {"user": {"name": "Grace"}},
        }, receive=AsyncMock(), send=AsyncMock()))
        self.assertEqual(other.substitute("Hi {{user.name}}"), "Hi Grace")
        self.assertEqual(self.session.substitute("Hi {{user.name}}"), "Hi Ada")

    async def test_random_replies_are_substituted(self) -> None:
        with patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send:
            await self.session.replies(["Hi {{user.name}}"])
            send.assert_awaited_once_with({"say": "Hi Ada"})

    async def test_response_prompt_is_substituted(self) -> None:
        with patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send:
            waiting = asyncio.create_task(self.session.request_response("Hi {{user.name}}"))
            await asyncio.sleep(0)
            try:
                send.assert_awaited_once_with({
                    "say": "Hi Ada", "command": {"does": "request-response"},
                })
            finally:
                waiting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiting

    async def test_intent_responses_are_substituted(self) -> None:
        with (
            patch.object(self.session.stack, "dispatch", return_value=dispatched({
                "responses": ["Hi {{user.name}}"],
            })),
            patch.object(self.websocket, "send_json", new_callable=AsyncMock) as send,
        ):
            await self.session.handle_message("hello")
            send.assert_awaited_once_with({"say": "Hi Ada"})

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


class WikkitRouterPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_magic_page_shows_brand_for_new_visitors(self) -> None:
        response = self.client.get("/wikkit/magic")
        self.assertEqual(response.status_code, 200)
        self.assertIn('src="/static/graphics/wikkit.svg"', response.text)
        self.assertIn('class="brand-name"', response.text)
        self.assertIn('data-page="wikkit-magic"', response.text)
        self.assertIn('src="/static/javascript/pages/wikkit-magic.does"', response.text)
        self.assertEqual(self.client.get("/static/graphics/wikkit.svg").status_code, 200)

    def test_magic_page_sends_returning_visitors_to_sign_in(self) -> None:
        self.client.cookies.set(identity.NAME_COOKIE, "Ada")
        response = self.client.get("/wikkit/magic", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/account/signin")

    def test_pricing_and_help_greet_by_escaped_name(self) -> None:
        self.client.cookies.set(identity.NAME_COOKIE, "%3Ci%3EAda%3C%2Fi%3E")
        for path, page in (("/wikkit/pricing", "wikkit-pricing"), ("/wikkit/help", "wikkit-help")):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'data-page="{page}"', response.text)
                self.assertIn(r"\u0026lt;i\u0026gt;Ada\u0026lt;/i\u0026gt;", response.text)
                self.assertNotIn("<i>Ada</i>", response.text)
        self.assertIn('href="/account/recover"', self.client.get("/wikkit/help").text)

    def test_pricing_shows_configured_price(self) -> None:
        with patch.dict(os.environ, {"RENDER_PRICE": "19.5"}):
            self.assertIn("$19.50", self.client.get("/wikkit/pricing").text)
        with patch.dict(os.environ, {"RENDER_PRICE": "0"}):
            self.assertIn("<strong>free</strong>", self.client.get("/wikkit/pricing").text)

    def test_remember_name_sets_cookie_from_json_only(self) -> None:
        self.assertEqual(self.client.post("/wikkit/name", data={"name": "Ada"}).status_code, 415)
        self.assertEqual(self.client.post("/wikkit/name", json={"name": " "}).status_code, 422)
        response = self.client.post("/wikkit/name", json={"name": "  Ada   Lovelace "})
        self.assertEqual(response.json(), {"ok": True})
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{identity.NAME_COOKIE}=Ada%20Lovelace", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("samesite=lax", cookie.lower())

    def test_extract_name(self) -> None:
        for reply, name in (
            ("Ada", "Ada"), ("  my name is Ada Lovelace. ", "Ada Lovelace"),
            ("Call me Ada!", "Ada"), ("Hi, I'm Jean-Luc", "Jean-Luc"),
            ("Hello Ada", "Ada"), ("Imogen", "Imogen"), ("Zoë O'Brien", "Zoë O'Brien"),
            ("no", None), ("idk", None), ("???", None), ("<b>Ada</b>", None),
            ("r2d2", None), ("I would rather not say that", None), ("hi", None),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(wikkit.extract_name(reply), name)


class WikkitWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ConfiguredTestClient(app)

    def open_page(self, socket, page: str) -> None:
        socket.send_text(json.dumps({"page": page}))

    def test_page_events_are_parsed_strictly(self) -> None:
        self.assertEqual(chatbot.page_event('{"page": "wikkit-help"}'), "wikkit-help")
        for message in ('{"page": 1}', '{"page": "x", "y": 1}', "page", "{nope", '["page"]'):
            with self.subTest(message=message):
                self.assertIsNone(chatbot.page_event(message))

    def test_magic_workflow_learns_and_remembers_name(self) -> None:
        with self.client.websocket_connect("/wikkit/ws") as socket:
            self.open_page(socket, "wikkit-magic")
            self.assertIn("Can I have your name to start?", socket.receive_json()["say"])
            self.assertEqual(socket.receive_json(), {"command": {"does": "request-response"}})
            socket.send_text("no")
            retry = socket.receive_json()
            self.assertIn("Could you please tell it to me?", retry["say"])
            self.assertEqual(retry["command"], {"does": "request-response"})
            socket.send_text("Call me Ada.")
            self.assertIn("Nice to meet you, Ada!", socket.receive_json()["say"])
            self.assertEqual(socket.receive_json(), {
                "command": {"does": "remember-name", "payload": {"name": "Ada"}},
            })
            self.assertEqual(socket.receive_json(), {
                "command": {"does": "redirect", "payload": "/account/register"},
            })

    def test_magic_workflow_calls_unnamed_visitors_friend(self) -> None:
        with self.client.websocket_connect("/wikkit/ws") as socket:
            self.open_page(socket, "wikkit-magic")
            socket.receive_json()
            socket.receive_json()
            for _ in range(2):
                socket.send_text("?")
                socket.receive_json()
            socket.send_text("no")
            self.assertIn("For now I&rsquo;ll call you <em>Friend</em>.", socket.receive_json()["say"])
            self.assertEqual(socket.receive_json()["command"]["payload"], {"name": "Friend"})

    def test_new_page_cancels_previous_workflow(self) -> None:
        with patch.object(chatbot.IntentStack, "dispatch", return_value=None) as dispatch:
            with self.client.websocket_connect("/wikkit/ws") as socket:
                self.open_page(socket, "wikkit-magic")
                socket.receive_json()
                socket.receive_json()
                self.open_page(socket, "wikkit-help")
                socket.send_text("Ada")
                self.assertEqual(socket.receive_json(), {"say": chatbot.UNHANDLED_MESSAGE})
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[-1], "Ada")

    def test_pricing_workflow_answers_follow_up_questions(self) -> None:
        with patch.object(chatbot.IntentStack, "dispatch", return_value=dispatched(
            {"type": "redirect", "url": "/account/register"}
        )) as dispatch:
            with self.client.websocket_connect("/wikkit/ws") as socket:
                self.open_page(socket, "wikkit-pricing")
                self.assertEqual(socket.receive_json(), {"command": {"does": "request-response"}})
                socket.send_text("yes")
                follow_up = socket.receive_json()
                self.assertIn("what else would you like to know?", follow_up["say"])
                socket.send_text("How do I sign up?")
                self.assertEqual(socket.receive_json(), {
                    "command": {"does": "redirect", "payload": "/account/register"},
                })
                self.open_page(socket, "wikkit-pricing")
                socket.receive_json()
                socket.send_text("nope")
                self.assertIn("Okay!", socket.receive_json()["say"])
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[-1], "How do I sign up?")

    def test_failed_workflow_tells_user(self) -> None:
        async def broken(_bot: chatbot.Chatbot) -> None:
            raise RuntimeError("boom")

        with patch.dict(wikkit.WORKFLOWS, {"broken": broken}):
            with self.client.websocket_connect("/wikkit/ws") as socket:
                with self.assertLogs("wikkit", "ERROR"):
                    self.open_page(socket, "broken")
                    self.assertEqual(socket.receive_json(), {"say": chatbot.FAILURE_MESSAGE})


class WikkitSubstitutionEscapingTests(unittest.TestCase):
    def test_substituted_values_are_html_escaped_and_hyphenated_fields_work(self) -> None:
        websocket = WebSocket({"type": "websocket", "state": {
            "user": {"user-name": "<script>x</script>"},
        }}, receive=AsyncMock(), send=AsyncMock())
        self.assertEqual(
            chatbot.Chatbot(websocket).substitute("Hi {{user.user-name}}"),
            "Hi &lt;script&gt;x&lt;/script&gt;",
        )


if __name__ == "__main__":
    unittest.main()
