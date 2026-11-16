import hashlib
import os
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright
import pypdfium2
from starlette.requests import HTTPConnection

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
