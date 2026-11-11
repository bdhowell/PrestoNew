import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
from playwright.async_api import async_playwright

import content
import elucidation
import posters
from routers import content as routes
from tests import test_content

DRAFT = test_content.DRAFT
ROOT = test_content.ROOT


class ReviewRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        test_content.ContentRouteTests.setUp(self)
        self.posters.content.return_value = ([
            ("title", "Example &amp; study"),
            ("introduction", '<h2>Introduction</h2><p>Safe <strong>text</strong></p><script>bad()</script>'),
        ], [])
        self.posters.set_resumption.return_value = True
        self.sources.graphic_sources.return_value = []
        self.sources.evaluation_sources.side_effect = psycopg.errors.UndefinedColumn(
            "column s.evaluation does not exist",
        )

    def test_review_renders_safe_sections_and_resumption(self) -> None:
        response = self.client.get("/content/review")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertIn('data-section="introduction"', response.text)
        self.assertIn('data-section="abstract"', response.text)
        self.assertIn("<strong>text</strong>", response.text)
        self.assertNotIn("<script>bad()", response.text)
        self.assertIn("give me feedback", response.text)
        self.assertIn('href="#review-1"', response.text)
        self.assertIn('data-username="&lt;Ada&gt;"', response.text)
        self.assertIn('data-next-page="/layout/edit"', response.text)
        self.assertLess(response.text.index('id="review-sections"'), response.text.index('id="editor-controls"'))
        self.assertIn("https://kit.fontawesome.com/938fc9d55b.js", response.text)
        self.assertIn('aria-label="Bold" title="Bold"><i class="fa-solid fa-bold"', response.text)
        self.assertIn("/static/javascript/layout-editor.js", response.text)
        self.assertIn("/static/javascript/pages/content-review.js", response.text)
        self.assertIn('id="revision-dialog"', response.text)
        self.assertIn('id="revision-accept"', response.text)
        self.assertIn('id="revision-reject"', response.text)
        self.assertIn('id="review-panes"', response.text)
        self.assertIn('id="review-divider" role="separator"', response.text)
        self.assertIn('id="review-graphics"', response.text)
        self.assertIn("No source graphics are available for this poster.", response.text)
        self.posters.content.assert_awaited_once_with(7, 11)
        self.sources.graphic_sources.assert_awaited_once_with(7, 11)
        self.sources.evaluation_sources.assert_not_awaited()
        self.posters.set_resumption.assert_awaited_once_with(7, 11, "http://testserver/content/review?xyzzy=arpa")

    def test_review_renders_uploaded_and_extracted_source_graphics(self) -> None:
        self.sources.graphic_sources.return_value = [
            content.GraphicSource(21, '<chart & "data">.png'),
            content.GraphicSource(22, "paper figure.jpg"),
        ]
        response = self.client.get("/content/review")
        self.assertEqual(response.status_code, 200)
        self.assertIn('src="/content/21/image"', response.text)
        self.assertIn('src="/content/22/image"', response.text)
        self.assertIn('alt="&lt;chart &amp; &#34;data&#34;&gt;.png"', response.text)
        self.assertIn("<figcaption>paper figure.jpg</figcaption>", response.text)
        self.assertNotIn("No source graphics are available", response.text)
        self.sources.graphic_sources.assert_awaited_once_with(7, 11)
        self.sources.evaluation_sources.assert_not_awaited()

    def test_graphics_storage_failure_is_reported(self) -> None:
        self.sources.graphic_sources.side_effect = psycopg.OperationalError("down")
        with self.assertLogs("routers.content", "ERROR"):
            response = self.client.get("/content/review")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Your sections and graphics could not be loaded.", response.text)

    def test_review_continues_to_edit_when_dimensions_are_missing(self) -> None:
        self.posters.get.return_value = replace(DRAFT, width=None, height=None)
        response = self.client.get("/content/review")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-next-page="/layout/edit"', response.text)

    def test_review_requires_signed_in_owner_and_draft(self) -> None:
        self.uid = None
        self.assertEqual(self.client.get("/content/review", follow_redirects=False).status_code, 303)
        self.uid = 7
        self.session.clear()
        self.assertEqual(self.client.get("/content/review").status_code, 409)
        self.session["poster_id"] = 11
        self.posters.get.return_value = None
        self.assertEqual(self.client.get("/content/review").status_code, 404)
        self.posters.get.return_value = replace(DRAFT, purchased=True)
        self.assertEqual(self.client.get("/content/review").status_code, 409)
        self.posters.content.assert_not_awaited()

    def test_storage_failure_is_reported(self) -> None:
        self.posters.content.side_effect = psycopg.OperationalError("down")
        with self.assertLogs("routers.content", "ERROR"):
            self.assertEqual(self.client.get("/content/review").status_code, 503)

    def test_resumed_review_restores_its_own_resumption_marker(self) -> None:
        self.assertEqual(self.client.get("/content/review?tag=a%2Fb&tag=two&xyzzy=arpa").status_code, 200)
        self.posters.set_resumption.assert_awaited_once_with(
            7, 11, "http://testserver/content/review?tag=a%2Fb&tag=two&xyzzy=arpa",
        )


class ReviewRenderedBrowserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        test_content.ContentRouteTests.setUp(self)
        self.posters.content.return_value = ([("results", "<p>Results</p>")], [])
        self.sources.graphic_sources.return_value = []

    async def test_divider_resizes_on_rendered_page_with_all_assets(self) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            self.addAsyncCleanup(browser.close)
            page = await browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def serve(route):
                if route.request.url.startswith("http://testserver/"):
                    response = self.client.get(route.request.url)
                    await route.fulfill(
                        status=response.status_code, body=response.content,
                        content_type=response.headers.get("content-type", "text/plain"),
                    )
                else:
                    await route.abort()

            await page.route("**/*", serve)
            await page.goto("http://testserver/content/review")
            divider = page.locator("#review-divider")
            handle = await divider.bounding_box()
            sections = page.locator("#review-sections")
            before = await sections.bounding_box()
            await page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + 50)
            await page.mouse.down()
            await page.mouse.move(handle["x"] + 100, handle["y"] + 50, steps=5)
            await page.mouse.up()
            after = await sections.bounding_box()
            self.assertEqual(errors, [])
            self.assertGreater(after["width"], before["width"] + 50)


class ReviewWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_names_and_ambiguous_feedback(self) -> None:
        for message, expected in (
            ("Shorten the results", "results"),
            ("Make Materials and Methods clearer", "materials and methods"),
            ("Revise the conclusions", "conclusion"),
            ("Change the acknowledgments", "acknowledgements"),
            ("Make it shorter", None),
            ("Change results and methods", None),
        ):
            with self.subTest(message=message):
                self.assertEqual(routes.review_section(message), expected)

    async def test_rewrite_sends_owner_scoped_snapshot_and_revision(self) -> None:
        bot = SimpleNamespace(
            websocket=SimpleNamespace(state=SimpleNamespace(user_id=7, poster_id=11)),
            request_response=AsyncMock(side_effect=["Shorten the results", asyncio.CancelledError]),
            send=AsyncMock(),
        )
        store = AsyncMock(spec=posters.PosterStore)
        store.get.return_value = DRAFT
        store.content.return_value = ([("results", "<p>Original</p>")], [])
        with patch.object(posters, "store", return_value=store), patch.object(
            routes, "rewrite_section", AsyncMock(return_value="<p>Revised</p>"),
        ) as rewrite:
            with self.assertRaises(asyncio.CancelledError):
                await routes.review_workflow(bot)
        store.get.assert_awaited_once_with(7, 11)
        store.content.assert_awaited_once_with(7, 11)
        rewrite.assert_awaited_once_with("results", "<p>Original</p>", "Shorten the results")
        self.assertEqual(bot.send.await_args_list[-1].args[0]["command"], {
            "does": "review-rewrite",
            "payload": {"pid": 11, "section": "results", "original": "<p>Original</p>", "expected": "<p>Original</p>",
                        "content": "<p>Revised</p>"},
        })

    async def test_rewrite_failure_and_missing_identity_are_explicit(self) -> None:
        for uid, failure in ((None, False), (7, True)):
            bot = SimpleNamespace(
                websocket=SimpleNamespace(state=SimpleNamespace(user_id=uid, poster_id=11)),
                request_response=AsyncMock(side_effect=["Revise results", asyncio.CancelledError]),
                send=AsyncMock(),
            )
            store = AsyncMock(spec=posters.PosterStore)
            store.get.return_value = DRAFT
            store.content.return_value = ([("results", "text")], [])
            with patch.object(posters, "store", return_value=store), patch.object(
                routes, "rewrite_section", AsyncMock(side_effect=elucidation.ElucidationError("failed")),
            ):
                if failure:
                    with self.assertLogs("routers.content", "ERROR"), self.assertRaises(asyncio.CancelledError):
                        await routes.review_workflow(bot)
                    self.assertEqual(bot.send.await_args_list[-1].args[0]["command"]["does"], "review-failed")
                else:
                    with self.assertRaises(asyncio.CancelledError):
                        await routes.review_workflow(bot)
                    store.get.assert_not_awaited()

    async def test_model_response_is_sanitized_and_invalid_results_fail(self) -> None:
        client = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = client
        client.responses.create.return_value = SimpleNamespace(
            status="completed", output_text='<p onclick="bad()">Revised</p><script>bad()</script>',
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), patch.object(
            routes.openai, "AsyncOpenAI", return_value=context,
        ):
            result = await routes.rewrite_section("results", "<p>Original</p>", "Shorten results")
            self.assertNotIn("<script>", result)
            self.assertNotIn("onclick", result)
            self.assertIn("Original", client.responses.create.await_args.kwargs["input"])
            client.responses.create.return_value = SimpleNamespace(status="incomplete", output_text="")
            with self.assertRaises(elucidation.ElucidationError):
                await routes.rewrite_section("results", "", "Revise results")


class ReviewBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch()
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        self.requests = []
        self.fail_save = False
        self.conflict = False

        async def route_request(route):
            if route.request.url.endswith("/layout/content"):
                self.requests.append(route.request.post_data_json)
                await route.fulfill(status=409 if self.conflict else 503 if self.fail_save else 200, json={
                    "ok": not (self.fail_save or self.conflict),
                    "detail": "Section changed" if self.conflict else "Save failed",
                })
            else:
                await route.fulfill(body="""
                    <div id="review" data-pid="11" data-username="Ada" data-next-page="/layout/edit">
                    <nav aria-label="Poster sections"><ul><li><a href="#review-1">Results</a></li></ul></nav>
                    <div class="review-main">
                    <div id="review-panes">
                    <div id="review-sections">
                    <section id="review-1"><h2>Results</h2>
                    <div class="content" contenteditable="true" data-section="results"><p>Original</p></div>
                    </section>
                    </div>
                    <div id="review-divider" role="separator" tabindex="0" aria-orientation="vertical"
                         aria-label="Resize sections and source graphics" aria-controls="review-sections review-graphics"
                         aria-valuemin="20" aria-valuemax="80" aria-valuenow="50"
                         aria-valuetext="Sections 50%, source graphics 50%"></div>
                    <section id="review-graphics" tabindex="0" aria-labelledby="review-graphics-heading">
                    <h2 id="review-graphics-heading">Source graphics</h2>
                    <figure><img alt="Source chart" width="600" height="400"
                        src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='600' height='400'%3E%3C/svg%3E">
                    <figcaption>chart.png</figcaption></figure>
                    </section>
                    </div>
                    </div></div>
                    <div class="review-actions">
                    <div class="editor-controls-container">
                    <div id="editor-controls">
                    <button id="bold" aria-label="Bold"><i class="fa-solid fa-bold"></i></button>
                    <button id="italic">Italic</button>
                    <button id="underline">Underline</button><button id="strikethrough">Strike</button>
                    <button id="plaintext">Plain</button><button id="code">Code</button>
                    <button id="superscript">Sup</button><button id="subscript">Sub</button>
                    <button id="ullist">Bullets</button><button id="ollist">Numbered</button>
                    <button id="cut">Cut</button><button id="copy">Copy</button><button id="paste">Paste</button>
                    </div></div>
                    <output id="content-save-status"></output>
                    <button id="review-continue">Continue</button></div>
                    <dialog id="revision-dialog">
                    <p id="revision-section"></p><div id="revision-original"></div>
                    <div id="revision-proposed"></div><p id="revision-status"></p>
                    <button id="revision-accept">Accept revision</button>
                    <button id="revision-reject">Reject revision</button></dialog>
                """, content_type="text/html")

        await self.page.route("https://review.test/**", route_request)
        await self.page.goto("https://review.test/content/review")
        await self.page.add_style_tag(path=str(ROOT / "static/css/pages/content-review.css"))
        await self.page.evaluate("""() => {
            window.messages = [];
            window.wikkit = {say: value => window.messages.push(value)};
            window.registerWikkitCommands = value => { window.commands = value.commands; };
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/pages/content-review.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-editor.js"))

    async def pane_bounds(self):
        return await self.page.evaluate("""() => {
            const rect = id => {
                const {left, right, top, bottom, width} = document.getElementById(id).getBoundingClientRect();
                return {left, right, top, bottom, width};
            };
            return {
                sections: rect('review-sections'), divider: rect('review-divider'),
                graphics: rect('review-graphics'), panes: rect('review-panes'),
            };
        }""")

    async def test_panes_are_side_by_side_and_graphics_fit_at_narrow_widths(self) -> None:
        for width in (1280, 600, 375):
            with self.subTest(width=width):
                await self.page.set_viewport_size({"width": width, "height": 800})
                bounds = await self.pane_bounds()
                self.assertAlmostEqual(bounds["sections"]["right"], bounds["divider"]["left"], delta=1)
                self.assertAlmostEqual(bounds["divider"]["right"], bounds["graphics"]["left"], delta=1)
                self.assertEqual(bounds["divider"]["width"], 10)
                self.assertEqual(bounds["sections"]["top"], bounds["graphics"]["top"])
                self.assertEqual(bounds["sections"]["bottom"], bounds["graphics"]["bottom"])
                fits = await self.page.locator("#review-graphics").evaluate(
                    "pane => pane.scrollWidth === pane.clientWidth",
                )
                self.assertTrue(fits)

    async def test_divider_resizes_with_pointer_and_clamps_both_panes(self) -> None:
        before = await self.pane_bounds()
        handle = before["divider"]
        await self.page.mouse.move(handle["left"] + handle["width"] / 2, handle["top"] + 50)
        await self.page.mouse.down()
        await self.page.mouse.move(handle["left"] + 100, handle["top"] + 50, steps=5)
        after = await self.pane_bounds()
        self.assertGreater(after["sections"]["width"], before["sections"]["width"])
        self.assertLess(after["graphics"]["width"], before["graphics"]["width"])
        await self.page.mouse.move(before["panes"]["right"] + 20, handle["top"] + 50)
        self.assertEqual(await self.page.locator("#review-divider").get_attribute("aria-valuenow"), "80")
        await self.page.mouse.move(before["panes"]["left"] - 20, handle["top"] + 50)
        self.assertEqual(await self.page.locator("#review-divider").get_attribute("aria-valuenow"), "20")
        await self.page.mouse.up()
        await self.page.mouse.move(handle["left"], handle["top"] + 50)
        self.assertEqual(await self.page.locator("#review-divider").get_attribute("aria-valuenow"), "20")
        self.assertEqual(self.requests, [])

    async def test_divider_keyboard_controls_and_resize_proportions(self) -> None:
        divider = self.page.locator("#review-divider")
        await divider.focus()
        for key, expected in (("ArrowRight", 51), ("Shift+ArrowLeft", 41), ("Home", 20),
                              ("ArrowLeft", 20), ("End", 80), ("ArrowRight", 80)):
            await divider.press(key)
            self.assertEqual(await divider.get_attribute("aria-valuenow"), str(expected))
        self.assertEqual(await divider.get_attribute("aria-valuetext"), "Sections 80%, source graphics 20%")
        await self.page.set_viewport_size({"width": 600, "height": 800})
        bounds = await self.pane_bounds()
        self.assertAlmostEqual(bounds["sections"]["width"] / bounds["graphics"]["width"], 4, delta=0.02)

    async def test_panes_scroll_independently(self) -> None:
        await self.page.evaluate("""() => {
            for (const id of ['review-sections', 'review-graphics']) {
                const filler = document.createElement('div');
                filler.style.height = '1500px';
                document.getElementById(id).append(filler);
            }
            document.getElementById('review-sections').scrollTop = 100;
        }""")
        self.assertEqual(await self.page.locator("#review-graphics").evaluate("pane => pane.scrollTop"), 0)
        await self.page.locator("#review-graphics").evaluate("pane => { pane.scrollTop = 200; }")
        self.assertEqual(await self.page.locator("#review-sections").evaluate("pane => pane.scrollTop"), 100)
        self.assertEqual(await self.page.locator("#review-graphics").evaluate("pane => pane.scrollTop"), 200)

    async def test_section_links_and_toolbar_match_review_styles(self) -> None:
        styles = await self.page.evaluate("""() => {
            const link = getComputedStyle(document.querySelector('#review nav a'));
            const list = getComputedStyle(document.querySelector('#review nav ul'));
            const button = getComputedStyle(document.querySelector('#bold'));
            return {
                color: link.color,
                weight: link.fontWeight,
                decoration: link.textDecorationLine,
                bullets: list.listStyleType,
                buttonWidth: button.width,
                buttonBackground: button.backgroundColor,
                buttonBorder: button.borderTopColor,
            };
        }""")
        self.assertEqual(styles, {
            "color": "rgb(0, 0, 205)",
            "weight": "700",
            "decoration": "none",
            "bullets": "none",
            "buttonWidth": "32px",
            "buttonBackground": "rgb(68, 58, 128)",
            "buttonBorder": "rgb(142, 133, 194)",
        })

    async def test_clicked_section_link_is_highlighted(self) -> None:
        link = self.page.locator("#review nav a")
        await link.click()
        self.assertEqual(await link.get_attribute("aria-current"), "location")
        colors = await self.page.evaluate("""() => ({
            link: getComputedStyle(document.querySelector('#review nav a')).color,
            outlineStyle: getComputedStyle(document.querySelector('#review nav a')).outlineStyle,
            outlineWidth: getComputedStyle(document.querySelector('#review nav a')).outlineWidth,
            outlineColor: getComputedStyle(document.querySelector('#review nav a')).outlineColor,
            heading: getComputedStyle(document.querySelector('#review-sections h2')).color,
        })""")
        self.assertEqual(colors, {
            "link": "rgb(244, 211, 94)",
            "outlineStyle": "solid",
            "outlineWidth": "1px",
            "outlineColor": "rgb(0, 0, 0)",
            "heading": "rgb(244, 211, 94)",
        })

    async def test_failed_save_is_announced_and_next_edit_saves(self) -> None:
        editor = self.page.locator(".content")
        self.fail_save = True
        await editor.fill("Changed")
        await self.page.wait_for_function(
            "() => document.querySelector('#content-save-status').textContent === 'Changes could not be saved.'",
        )
        self.assertEqual(self.requests[-1], {"pid": 11, "section": "results", "content": "<p>Changed</p>"})
        self.assertFalse(await self.page.locator("#review-retry").count())
        self.assertEqual(
            await self.page.evaluate("messages.at(-1)"),
            "<p>Ada, I&rsquo;m very sorry but I&rsquo;m having trouble saving your changes right now. "
            "Please reload the page and try making your changes again in a few minutes. "
            "I apology for inconveniencing you.</p>",
        )
        self.fail_save = False
        await editor.fill("Changed again")
        await self.page.wait_for_function(
            "() => document.querySelector('#content-save-status').textContent === 'Changes saved.'",
        )
        self.assertEqual(self.requests[-1]["content"], "<p>Changed again</p>")

    async def propose_revision(self) -> None:
        await self.page.evaluate("""async () => {
            commands['review-start'].does({pid: 11});
            await commands['review-rewrite'].does({
                pid: 11, section: 'results', original: '<p>Original</p>',
                expected: '<p>Original</p>', content: '<p>Revised</p>'
            });
        }""")

    async def test_accept_saves_only_after_confirmation(self) -> None:
        await self.propose_revision()
        self.assertTrue(await self.page.locator("#revision-dialog").is_visible())
        self.assertEqual(await self.page.locator("#revision-original").inner_html(), "<p>Original</p>")
        self.assertEqual(await self.page.locator("#revision-proposed").inner_html(), "<p>Revised</p>")
        self.assertEqual(await self.page.locator(".content").inner_html(), "<p>Original</p>")
        self.assertEqual(self.requests, [])
        self.assertTrue(await self.page.locator("#review-continue").is_disabled())
        await self.page.locator("#revision-accept").click()
        await self.page.locator("#revision-dialog").wait_for(state="hidden")
        self.assertEqual(await self.page.locator(".content").inner_html(), "<p>Revised</p>")
        self.assertEqual(self.requests[-1]["content"], "<p>Revised</p>")
        self.assertEqual(self.requests[-1]["expected"], "<p>Original</p>")
        self.assertFalse(await self.page.locator("#review-continue").is_disabled())

    async def test_reject_and_escape_leave_section_unchanged(self) -> None:
        for action in ("reject", "escape"):
            with self.subTest(action=action):
                await self.propose_revision()
                if action == "reject":
                    await self.page.locator("#revision-reject").click()
                else:
                    await self.page.keyboard.press("Escape")
                self.assertTrue(await self.page.locator("#revision-dialog").is_hidden())
                self.assertEqual(await self.page.locator(".content").inner_html(), "<p>Original</p>")
                self.assertEqual(self.requests, [])
                self.assertFalse(await self.page.locator("#review-continue").is_disabled())

    async def test_rewrite_rejects_changes_during_generation_or_before_acceptance(self) -> None:
        await self.page.locator(".content").fill("My changes")
        await self.propose_revision()
        self.assertEqual(await self.page.locator(".content").inner_text(), "My changes")
        self.assertTrue(await self.page.locator("#revision-dialog").is_hidden())
        self.assertIn("changed while I was working", await self.page.evaluate("messages.at(-1)"))

    async def test_accept_fails_safely_and_can_retry(self) -> None:
        await self.propose_revision()
        self.fail_save = True
        await self.page.locator("#revision-accept").click()
        await self.page.wait_for_function(
            "() => document.querySelector('#revision-status').textContent.includes('Save failed')",
        )
        self.assertTrue(await self.page.locator("#revision-dialog").is_visible())
        self.assertEqual(await self.page.locator(".content").inner_html(), "<p>Original</p>")
        self.assertIn("Save failed", await self.page.locator("#revision-status").inner_text())
        self.fail_save = False
        await self.page.locator("#revision-accept").click()
        await self.page.locator("#revision-dialog").wait_for(state="hidden")
        self.assertEqual(await self.page.locator(".content").inner_html(), "<p>Revised</p>")

    async def test_accept_does_not_overwrite_newer_local_or_saved_content(self) -> None:
        await self.propose_revision()
        await self.page.locator(".content").evaluate(
            "element => { element.innerHTML = '<p>Newer</p>'; }",
        )
        await self.page.locator("#revision-accept").click()
        self.assertIn("section changed", (await self.page.locator("#revision-status").inner_text()).lower())
        self.assertEqual(self.requests, [])
        await self.page.locator("#revision-reject").click()
        self.assertEqual(await self.page.locator(".content").inner_html(), "<p>Newer</p>")
        await self.page.locator(".content").evaluate(
            "element => { element.innerHTML = '<p>Original</p>'; }",
        )
        await self.propose_revision()
        self.conflict = True
        await self.page.locator("#revision-accept").click()
        await self.page.wait_for_function(
            "() => document.querySelector('#revision-status').textContent.includes('Section changed')",
        )
        self.assertEqual(await self.page.locator(".content").inner_html(), "<p>Original</p>")
        self.assertEqual(self.requests[-1]["expected"], "<p>Original</p>")
        await self.page.locator("#revision-reject").click()
        self.assertTrue(await self.page.locator("#revision-dialog").is_hidden())

    async def test_overlapping_proposal_does_not_replace_first(self) -> None:
        await self.propose_revision()
        await self.page.evaluate("""() => {
            commands['review-start'].does({pid: 11});
            commands['review-rewrite'].does({
                pid: 11, section: 'results', original: '<p>Original</p>',
                expected: '<p>Original</p>', content: '<p>Second</p>'
            });
        }""")
        self.assertEqual(await self.page.locator("#revision-proposed").inner_html(), "<p>Revised</p>")
        self.assertEqual(self.requests, [])

    async def test_formatting_saves_and_continue_flushes_pending_edit(self) -> None:
        editor = self.page.locator(".content")
        await editor.click()
        await self.page.evaluate("""() => {
            const text = document.querySelector('.content p').firstChild;
            const selection = getSelection();
            const range = document.createRange();
            range.selectNodeContents(text);
            selection.removeAllRanges();
            selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }""")
        await self.page.locator("#bold").click()
        self.assertIn("<b>Original</b>", await editor.inner_html())
        await self.page.locator("#review-continue").click()
        await self.page.wait_for_url("https://review.test/layout/edit")
        self.assertEqual(self.requests[-1]["content"], "<p><b>Original</b></p>")

    async def test_failed_save_blocks_continue_and_rewrite_blocks_navigation(self) -> None:
        self.fail_save = True
        await self.page.locator(".content").fill("Unsaved")
        await self.page.locator("#review-continue").click()
        await self.page.wait_for_function(
            "() => document.querySelector('#content-save-status').textContent === 'Changes could not be saved.'",
        )
        self.assertFalse(await self.page.locator("#review-retry").count())
        self.assertIn("I apology for inconveniencing you.", await self.page.evaluate("messages.at(-1)"))
        self.assertTrue(self.page.url.endswith("/content/review"))
        self.assertFalse(await self.page.locator("#review-continue").is_disabled())
        await self.page.evaluate("commands['review-start'].does({pid: 11})")
        self.assertTrue(await self.page.locator("#review-continue").is_disabled())
        await self.page.evaluate("commands['review-failed'].does({pid: 11})")
        self.assertFalse(await self.page.locator("#review-continue").is_disabled())

    async def test_enter_creates_supported_paragraph_markup(self) -> None:
        editor = self.page.locator(".content")
        await editor.click()
        await self.page.keyboard.press("End")
        await self.page.keyboard.press("Enter")
        await self.page.keyboard.type("Second paragraph")
        await self.page.wait_for_function(
            "() => document.querySelector('#content-save-status').textContent === 'Changes saved.'",
        )
        self.assertIn("<p>Second paragraph</p>", self.requests[-1]["content"])
