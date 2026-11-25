import hashlib
import os
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from lxml import html
from playwright.async_api import Browser, Error as PlaywrightError, Page, async_playwright
import pypdfium2
from starlette.requests import HTTPConnection

import layout
import poster_rendering
import posters
from routers import account


DRAFT = posters.Poster(11, "Research", None, 36, 48, None, None, False, None, units="in")
PURCHASED = replace(DRAFT, purchased=True)


class PurchaseRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = {"poster_id": 11}
        self.store = AsyncMock()
        self.store.get.return_value = DRAFT
        self.store.purchase.return_value = True
        self.store.record_download.return_value = True
        self.store_patch = patch.object(posters, "store", return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

        async def identity(connection: HTTPConnection) -> None:
            connection.state.user_id = 7
            connection.state.user = {"user-name": "Ada"}
            connection.scope["session"] = self.session

        application = FastAPI(dependencies=[Depends(identity)])
        application.include_router(account.router)
        self.client = TestClient(application)

    def test_purchase_page_is_a_placeholder_and_displays_configured_price(self) -> None:
        with patch.dict(os.environ, {"POSTER_PRICE": "12.50"}):
            response = self.client.get("/account/purchase")
        self.assertEqual(response.status_code, 200)
        self.assertIn("$12.50", response.text)
        self.assertIn("Online payment will be available here", response.text)

    def test_confirmed_purchase_records_the_configured_amount(self) -> None:
        with patch.dict(os.environ, {"POSTER_PRICE": "12.50"}):
            response = self.client.post("/account/purchase", json={"pid": 11})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "redirect": "/account/download"})
        self.store.purchase.assert_awaited_once_with(7, 11, Decimal("12.50"), ANY)
        self.assertEqual(self.session["poster_id"], 11)

    def test_repeat_purchase_is_idempotent(self) -> None:
        self.store.get.return_value = PURCHASED
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/account/purchase", json={"pid": 11})
        self.assertEqual(response.status_code, 200)
        self.store.purchase.assert_not_awaited()

    def test_download_renders_pdf_and_records_download_hash(self) -> None:
        self.store.get.return_value = PURCHASED
        layout = AsyncMock(return_value="<html><body>saved editor layout</body></html>")
        with (
            patch.object(account.layout_routes, "layout", layout),
            patch.object(poster_rendering, "poster_pdf", new=AsyncMock(return_value=b"%PDF-test")),
        ):
            response = self.client.get("/account/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-test")
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="poster-11.pdf"')
        layout.assert_awaited_once_with("arena", 11, uid=7, request=ANY)
        self.store.record_download.assert_awaited_once_with(
            7, 11, hashlib.sha256(b"%PDF-test").hexdigest()
        )


class PurchaseBrowserAndPdfTests(unittest.IsolatedAsyncioTestCase):
    async def test_paid_poster_download_confirms_only_after_success(self) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(accept_downloads=True)
                await page.route("https://presto.test/posters/list*", lambda route: route.fulfill(
                    content_type="text/html", body="<html><body></body></html>",
                ))
                for automatic, success, button in (
                    (False, True, False), (True, True, False), (False, False, False),
                    (False, True, True), (False, False, True),
                ):
                    with self.subTest(automatic=automatic, success=success, button=button):
                        await page.goto("https://presto.test/posters/list?download=11")
                        await page.set_content(
                            '<button data-new-poster>New</button><p class="poster-message"></p>'
                            + ('<button type="button" data-download-url="/account/download?pid=11" '
                               if button else '<a href="/account/download?pid=11" ')
                            + 'data-download-poster '
                            'data-poster-name="&lt;Research&gt;" '
                            + ('data-auto-download ' if automatic else '')
                            + ('>Print</button>' if button else '>Poster</a>')
                        )
                        await page.evaluate("""() => {
                            window.messages = [];
                            window.wikkit = {say: async text => messages.push(text)};
                        }""")
                        await page.route("https://presto.test/account/download?pid=11", lambda route: route.fulfill(
                            status=200 if success else 503,
                            content_type="application/pdf" if success else "application/json",
                            body=b"%PDF-test" if success else b'{"detail":"Download unavailable."}',
                        ))
                        if success:
                            async with page.expect_download() as downloaded:
                                await page.add_script_tag(path=str(
                                    poster_rendering.ROOT / "static/javascript/posters/router.js",
                                ))
                                if not automatic:
                                    await page.locator("[data-download-poster]").click()
                            self.assertEqual((await downloaded.value).suggested_filename, "poster-11.pdf")
                            await page.wait_for_function("messages.length === 1")
                            self.assertEqual(await page.evaluate("messages"), [
                                "<p>I&rsquo;ve downloaded a copy of &lt;Research&gt;.</p>",
                            ])
                            if automatic:
                                self.assertNotIn("download=", page.url)
                        else:
                            await page.add_script_tag(path=str(
                                poster_rendering.ROOT / "static/javascript/posters/router.js",
                            ))
                            await page.locator("[data-download-poster]").click()
                            await page.wait_for_function(
                                "document.querySelector('.poster-message').textContent === 'Download unavailable.'",
                            )
                            self.assertEqual(await page.evaluate("messages"), [])
                        await page.unroute("https://presto.test/account/download?pid=11")
            finally:
                await browser.close()

    async def test_pdf_rejects_failed_font_stylesheets_and_font_files(self) -> None:
        rendered = """
            <html><head>
                <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">
            </head><body>
                <article class="poster-document" data-width="3456" data-height="4608">
                    Poster text
                </article>
            </body></html>
        """
        new_page = Browser.new_page
        for failed_asset in ("stylesheet", "font"):
            with self.subTest(asset=failed_asset):
                async def configure_page(browser, *args, **kwargs):
                    page = await new_page(browser, *args, **kwargs)
                    if failed_asset == "stylesheet":
                        await page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                    else:
                        await page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(
                            content_type="text/css",
                            body="""
                                @font-face {
                                    font-family: BrokenPosterFont;
                                    src: url(data:font/woff2;base64,aW52YWxpZA==);
                                }
                                .poster-document { font-family: BrokenPosterFont; }
                            """,
                        ))
                    return page

                with patch.object(Browser, "new_page", configure_page), patch.object(
                    Page, "pdf", new_callable=AsyncMock,
                ) as pdf:
                    with self.assertRaisesRegex(
                        (ValueError, PlaywrightError), "Poster font.*could not be loaded",
                    ):
                        await poster_rendering.poster_pdf(rendered)
                    pdf.assert_not_awaited()

    async def test_pdf_matches_customized_editor_geometry_and_font_metrics(self) -> None:
        geometry = {
            "scale": 0.3, "header": 0.2, "layout": 0.8,
            "columns": [0.25, 0.5, 0.25], "rows": [0.7, 0.3],
            "vertical": {"pane0": [0.4, 0.6], "pane1": [1], "pane2": [1]},
            "horizontal": {"pane3": [0.3, 0.3, 0.4]},
        }
        poster = replace(
            DRAFT, template="arena", fonts="modern", theme="blue",
            layout_geometry={"arena": geometry},
            layout_settings={"arena": {
                "left": 0.1, "right": 0.05, "bottom": 0.08,
                "introduction": 0.65, "sections": {"pane3": [0.4, 0.3, 0.3]},
            }},
            font_sizing={"sections": 1.2, "title": 1.3, "authors": 0.8},
        )
        store = AsyncMock()
        store.get.return_value = poster
        store.content.return_value = ([
            ("title", "Customized poster"),
            ("introduction", "<p>Introduction text.</p>"),
            ("materials and methods", "<p>Methods text.</p>"),
            ("results", "<p>Results text.</p>"),
        ], [("Ada Lovelace", "Institute")])
        store.conference_details.return_value = ("", {})
        with patch.object(posters, "store", return_value=store), patch.object(
            layout, "balance_header_widths", AsyncMock(return_value={"#title": 80, "#authors": 90}),
        ):
            rendered = await layout.layout("arena", 11, uid=7)

        measure = """() => {
            const canvas = document.getElementById("poster-canvas").getBoundingClientRect();
            return Object.fromEntries(["poster-content", "title", "authors", "layout",
                "pane0", "pane1", "pane2", "pane3", "introduction", "methods",
                "citations", "acknowledgements", "information"].map(id => {
                const element = document.getElementById(id);
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return [id, {
                    x: (rect.x - canvas.x) / canvas.width,
                    y: (rect.y - canvas.y) / canvas.height,
                    width: rect.width / canvas.width, height: rect.height / canvas.height,
                    family: style.fontFamily, size: style.fontSize, weight: style.fontWeight,
                }];
            }));
        }"""
        new_page = Browser.new_page
        pdf = Page.pdf
        snapshots = []
        requests = []

        async def configured_page(browser, *args, **kwargs):
            page = await new_page(browser, *args, **kwargs)
            # Use a deterministic font stylesheet without depending on Google's availability.
            await page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(
                content_type="text/css",
                body=".modern { --body-font-face: monospace; --heading-font-face: monospace; }",
            ))
            page.on("request", lambda request: requests.append(request.url))
            return page

        async def measured_pdf(page, *args, **kwargs):
            await page.emulate_media(media="print")
            snapshots.append(await page.evaluate(measure))
            self.assertEqual(await page.locator(".resize-overlay").count(), 0)
            return await pdf(page, *args, **kwargs)

        with patch.object(Browser, "new_page", configured_page):
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page(viewport={"width": 1000, "height": 900})
                    document = html.document_fromstring(rendered)
                    for script in document.xpath("//script"):
                        script.getparent().remove(script)
                    for link in document.xpath("//link[@rel='stylesheet']"):
                        if link.get("href", "").startswith("/static/"):
                            style = html.Element("style")
                            style.text = (poster_rendering.ROOT / link.get("href").lstrip("/")).read_text()
                            link.getparent().replace(link, style)
                    await page.set_content(html.tostring(document, encoding="unicode"))
                    for name in ("layout-zoom", "layout-geometry", "layout-resize"):
                        await page.add_script_tag(
                            path=str(poster_rendering.ROOT / f"static/javascript/{name}.js"),
                        )
                    await page.evaluate("() => window.posterGeometryReady")
                    await page.wait_for_function(
                        "() => document.querySelectorAll('.resize-gutter').length === 6",
                    )
                    expected = await page.evaluate(measure)
                finally:
                    await browser.close()
            requests.clear()
            with patch.object(Page, "pdf", measured_pdf):
                result = await poster_rendering.poster_pdf(rendered)
        self.assertTrue(result.startswith(b"%PDF-"))
        self.assertTrue(any(url.startswith("https://fonts.googleapis.com/") for url in requests))
        self.assertFalse(any("/layout/" in url for url in requests))
        self.assertEqual(len(snapshots), 1)
        for id, values in expected.items():
            for name, value in values.items():
                with self.subTest(element=id, metric=name):
                    if isinstance(value, float):
                        self.assertAlmostEqual(snapshots[0][id][name], value, delta=0.0001)
                    else:
                        self.assertEqual(snapshots[0][id][name], value)

    async def test_print_button_waits_for_choice_and_cancel_keeps_editing(self) -> None:
        controls = "".join(
            f'<button id="{name}"></button>'
            for name in (
                "bold", "italic", "underline", "strikethrough", "superscript",
                "subscript", "ullist", "ollist", "code", "plaintext", "cut",
                "copy", "paste",
            )
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.set_content(
                    '<body data-user-name="Ada &lt;img src=x&gt;">'
                    '<div id="editor-controls"><button id="print">Print</button>'
                    f"{controls}</div><div id=\"layout\" data-pid=\"11\"></div>"
                    '<output id="content-save-status"></output></body>'
                )
                await page.evaluate(
                    """() => {
                        window.messages = [];
                        window.wikkit = {
                            say: message => window.messages.push(message),
                            requestResponse: () => Promise.resolve("cancel"),
                        };
                    }"""
                )
                await page.add_script_tag(
                    path=str(Path("static/javascript/layout-editor.js").resolve())
                )
                await page.locator("#print").click()
                await page.wait_for_function("window.messages.length === 2")
                messages = await page.evaluate("window.messages")
                self.assertIn("Ada &lt;img src=x&gt;", messages[0])
                self.assertIn('data-sends="ready"', messages[0])
                self.assertIn('data-sends="cancel"', messages[0])
                self.assertIn("Keep working on your poster", messages[1])
                self.assertEqual(errors, [])
                await page.evaluate(
                    """() => {
                        window.purchaseEvents = [];
                        window.wikkit.requestResponse = () => Promise.resolve("ready");
                        window.flushPosterContent = async () => purchaseEvents.push("flush");
                        window.fetch = async () => {
                            purchaseEvents.push("purchase");
                            throw new Error("stop before navigation");
                        };
                    }"""
                )
                await page.locator("#print").click()
                await page.wait_for_function(
                    "window.purchaseEvents.length === 2 && window.messages.length === 4"
                )
                self.assertEqual(
                    await page.evaluate("window.purchaseEvents"), ["flush", "purchase"]
                )
            finally:
                await browser.close()

    async def test_poster_pdf_keeps_the_editor_layout_and_full_size(self) -> None:
        geometry = (
            '{"scale":1,"header":0.2,"layout":0.8,"columns":[0.2,0.5,0.3],'
            '"rows":[0.7,0.3],"vertical":{"pane0":[0.4,0.6],"pane1":[1],'
            '"pane2":[1]},"horizontal":{"pane3":[0.3,0.3,0.4]}}'
        )
        rendered = """
        <!doctype html>
        <html><head>
            <link rel="stylesheet" href="/static/css/main.css">
            <link rel="stylesheet" href="/static/css/layout.css">
            <link rel="stylesheet" href="/static/css/layouts/arena.css">
            <link rel="stylesheet" href="/static/css/themes.css">
            <link rel="stylesheet" href="/static/css/fonts.css">
        </head><body>
            <div id="poster-container">
                <div id="poster-stage">
                    <div id="poster-canvas" style="width:3456px;height:4608px;--poster-font-scale:1">
                        <div id="poster-content">
                            <header id="poster-header"></header>
                            <div id="layout" class="layout three-column"
                                 data-resize-settings='{"left":0,"right":0,"bottom":0,"introduction":null,"sections":{}}'
                                 data-initial-geometry='REPLACE_GEOMETRY'>
                                <div id="pane0" class="pane">
                                    <div class="section"><h2>First</h2></div>
                                    <div class="section"><h2>Second</h2></div>
                                </div>
                                <div id="pane1" class="pane"><div class="section"><h2>Third</h2></div></div>
                                <div id="pane2" class="pane"><div class="section"><h2>Fourth</h2></div></div>
                                <div id="pane3" class="pane horizontal">
                                    <div class="section"><h2>Fifth</h2></div>
                                    <div class="section"><h2>Sixth</h2></div>
                                    <div class="section"><h2>Seventh</h2></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </body></html>
        """.replace("REPLACE_GEOMETRY", geometry)
        pdf = await poster_rendering.poster_pdf(rendered)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        document = pypdfium2.PdfDocument(pdf)
        try:
            self.assertEqual(len(document), 1)
            self.assertEqual(document[0].get_size(), (2592, 3456))
            text = document[0].get_textpage().get_text_range()
            self.assertEqual(
                [word for word in (
                    "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh",
                )
                 if word in text],
                ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh"],
            )
            image = document[0].render(scale=0.05).to_pil().convert("RGB")
            self.assertLess(min(min(channel.getextrema()) for channel in image.split()), 255)
        finally:
            document.close()
