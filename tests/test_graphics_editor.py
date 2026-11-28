import unittest
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent.parent


class GraphicsEditorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch()
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        await self.page.goto("about:blank")
        buttons = "".join(
            f'<button id="{name}">{name}</button>'
            for name in (
                "bold", "italic", "underline", "strikethrough", "superscript", "subscript",
                "ullist", "ollist", "code", "plaintext", "cut", "copy", "paste", "image",
                "graphics-left", "graphics-center", "graphics-right",
            )
        )
        await self.page.set_content(f"""
            <body data-user-name="Ada">
            <div id="poster-canvas"><div id="layout" data-pid="11" data-graphics-safe-dpi="150">
                <div class="content" contenteditable="true" data-section="results">Hello</div>
                <div class="content" contenteditable="true" data-section="materials and methods">Methods</div>
                <div class="content" contenteditable="true" data-section="introduction">Intro</div>
                <div class="content" contenteditable="true" data-section="literature cited">References</div>
                <div class="content" contenteditable="true" data-section="additional information">Contact</div>
            </div></div>
            <div id="editor-controls">{buttons}</div>
            <output id="content-save-status"></output>
            <dialog id="graphics-dialog"><div id="graphics-grid"></div>
                <p id="graphics-status"></p><button id="graphics-cancel">Cancel</button>
                <button id="graphics-insert">Insert</button></dialog>
            <dialog id="graphics-preview"><h2 id="graphics-preview-title"></h2>
                <div id="graphics-preview-content"></div>
                <button id="graphics-preview-close">Close</button></dialog>
            </body>
        """)
        await self.page.evaluate("""() => {
            window.messages = [];
            window.wikkit = {say: text => messages.push(text)};
            window.requests = [];
            window.fetch = async url => {
                requests.push(url);
                return {ok: true, json: async () => ({ok: true, graphics: [
                    {sid: 22, type: 'png', filename: 'chart.png', math_expressions: []},
                    {sid: 23, type: 'math', filename: 'formula', math_expressions: ['x^2']},
                ]})};
            };
        }""")
        for script in ("layout-editor.js", "layout-graphics.js", "layout-graphic-controls.js"):
            await self.page.add_script_tag(path=str(ROOT / "static/javascript" / script))

    async def select_section(self, name: str) -> None:
        await self.page.locator(f'[data-section="{name}"]').evaluate("""element => {
            element.focus();
            const range = document.createRange();
            range.selectNodeContents(element);
            range.collapse(false);
            getSelection().removeAllRanges();
            getSelection().addRange(range);
        }""")
        await self.page.wait_for_timeout(20)

    async def test_no_section_and_restricted_section_show_wikkit_message(self) -> None:
        await self.page.locator("#image").click()
        self.assertIn("select a poster section", await self.page.evaluate("messages[0]"))
        await self.select_section("literature cited")
        await self.page.locator("#image").click()
        self.assertIn("reserved", await self.page.evaluate("messages[1]"))
        self.assertFalse(await self.page.locator("#graphics-dialog").evaluate("element => element.open"))
        self.assertEqual(await self.page.evaluate("requests"), [])

    async def test_picker_preview_cancel_and_insert_at_caret(self) -> None:
        await self.select_section("results")
        await self.page.locator("#image").click()
        self.assertTrue(await self.page.locator("#graphics-dialog").evaluate("element => element.open"))
        self.assertIn("pid=11", (await self.page.evaluate("requests[0]")))
        choice = self.page.locator(".graphics-choice").first
        await choice.dblclick()
        self.assertEqual(await choice.get_attribute("aria-pressed"), "true")
        self.assertTrue(await self.page.locator("#graphics-preview").evaluate("element => element.open"))
        await self.page.locator("#graphics-preview-close").click()
        await self.page.locator("#graphics-cancel").click()
        self.assertEqual(await self.page.locator('[data-section="results"] .graphic').count(), 0)
        await self.select_section("results")
        await self.page.locator("#image").click()
        await self.page.locator(".graphics-choice").first.click()
        await self.page.locator("#graphics-insert").click()
        graphic = self.page.locator('[data-section="results"] .graphic')
        self.assertEqual(await graphic.count(), 1, await self.page.locator("#graphics-status").text_content())
        self.assertEqual(await graphic.get_attribute("data-sid"), "22")
        self.assertEqual(await graphic.locator("img").get_attribute("src"), "/layout/source?sid=22")
        self.assertTrue((await self.page.locator('[data-section="results"]').inner_html()).startswith("Hello"))

    async def test_second_graphic_requires_deleting_the_existing_one(self) -> None:
        await self.page.locator('[data-section="results"]').evaluate("""editor => {
            editor.innerHTML += '<span class="graphic" data-type="png"></span>';
        }""")
        await self.select_section("results")
        await self.page.locator("#image").click()
        self.assertIn("Ada, you must first cut or otherwise delete the existing graphic in the results section",
                      await self.page.evaluate("messages.at(-1)"))
        self.assertFalse(await self.page.locator("#graphics-dialog").evaluate("element => element.open"))
        self.assertEqual(await self.page.evaluate("requests"), [])

    async def test_float_controls_change_selected_graphic_placement(self) -> None:
        await self.page.locator('[data-section="results"]').evaluate("""editor => {
            editor.innerHTML = '<span class="graphic" data-sid="22" data-type="png" ' +
                'data-placement="free"><img alt="Chart"></span>';
        }""")
        graphic = self.page.locator('[data-section="results"] .graphic')
        await graphic.click()

        button_ids = ("graphics-left", "graphics-center", "graphics-right")
        for button_id, placement in (
            ("graphics-left", "left-middle"),
            ("graphics-center", "free"),
            ("graphics-right", "right-middle"),
        ):
            button = self.page.locator(f"#{button_id}")
            self.assertFalse(await button.is_disabled())
            await button.click()
            self.assertEqual(await graphic.get_attribute("data-placement"), placement)
            for candidate in button_ids:
                self.assertEqual(
                    await self.page.locator(f"#{candidate}").get_attribute("aria-pressed"),
                    str(candidate == button_id).lower(),
                )

    async def test_qr_replacement_no_leaves_the_section_unchanged(self) -> None:
        await self.page.evaluate("""() => {
            wikkit.requestResponse = async () => 'no';
            window.fetch = async () => ({ok: true, json: async () => ({ok: true, exists: true})});
        }""")
        await self.select_section("additional information")
        await self.page.locator("#image").click()
        await self.page.wait_for_function("messages.length === 2")
        self.assertIn("already exists", await self.page.evaluate("messages[0]"))
        self.assertIn("won’t replace", await self.page.evaluate("messages[1]"))
        self.assertEqual(await self.page.locator('[data-section="additional information"]').inner_html(), "Contact")

    async def test_qr_requires_an_explicit_confirmation(self) -> None:
        await self.page.evaluate("""() => {
            const answers = ['maybe', 'no'];
            wikkit.requestResponse = async () => answers.shift();
            window.fetch = async () => ({ok: true, json: async () => ({ok: true, exists: true})});
        }""")
        await self.select_section("additional information")
        await self.page.locator("#image").click()
        await self.page.wait_for_function("messages.length === 3")
        self.assertIn("answer yes or no", await self.page.evaluate("messages[1]"))
        self.assertIn("won’t replace", await self.page.evaluate("messages[2]"))
        self.assertEqual(await self.page.locator('[data-section="additional information"]').inner_html(), "Contact")

    async def test_qr_creation_prompts_and_displays_the_saved_section(self) -> None:
        await self.page.evaluate("""() => {
            const answers = ['yes', 'https://example.test/'];
            wikkit.requestResponse = async () => answers.shift();
            window.flushPosterContent = async () => { window.flushed = true; };
            window.fetch = async (url, options) => {
                if (!options) return {ok: true, json: async () => ({ok: true, exists: false})};
                window.qrRequest = JSON.parse(options.body);
                return {ok: true, json: async () => ({ok: true, content:
                    '<h2>Additional Information</h2><p>Contact</p><span class="graphic" data-sid="7"></span>'})};
            };
        }""")
        await self.select_section("additional information")
        await self.page.locator("#image").click()
        await self.page.wait_for_function("window.qrRequest !== undefined")
        self.assertTrue(await self.page.evaluate("window.flushed"))
        self.assertEqual(await self.page.evaluate("window.qrRequest"), {
            "pid": 11, "url": "https://example.test/", "replace": False,
        })
        section = self.page.locator('[data-section="additional information"]')
        await self.page.wait_for_function(
            "document.querySelector('[data-section=\"additional information\"] .graphic') !== null"
        )
        self.assertEqual(await section.locator("h2").count(), 0)
        self.assertEqual(await section.get_attribute("contenteditable"), "true")

    async def test_resize_warns_for_low_dpi_and_drag_snaps(self) -> None:
        await self.page.add_style_tag(path=str(ROOT / "static/css/graphics.css"))
        await self.page.add_style_tag(content="""
            [data-section="results"] { width: 800px; height: 500px; }
        """)
        await self.select_section("results")
        await self.page.locator("#image").click()
        await self.page.locator(".graphics-choice").first.click()
        await self.page.locator("#graphics-insert").click()
        graphic = self.page.locator('[data-section="results"] .graphic')
        await graphic.locator("img").evaluate("""img => {
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 10;
            img.src = canvas.toDataURL();
        }""")
        await self.page.wait_for_function(
            "document.querySelector('.graphic img').naturalWidth === 10"
        )
        await graphic.click()
        initial = await graphic.bounding_box()
        self.assertEqual(await self.page.locator(".graphic-handle").count(), 8)
        handle = await self.page.locator(".graphic-handle-e").bounding_box()
        await self.page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
        await self.page.mouse.down()
        await self.page.mouse.move(handle["x"] + handle["width"] / 2 + 80, handle["y"] + handle["height"] / 2)
        await self.page.mouse.up()
        self.assertAlmostEqual(
            float(await graphic.get_attribute("data-width")) / float(await graphic.get_attribute("data-height")),
            initial["width"] / initial["height"],
            places=2,
        )
        self.assertIn("likely to be blurry", await self.page.evaluate("messages.at(-1)"))
        move = await self.page.locator(".graphic-move").bounding_box()
        bounds = await self.page.locator('[data-section="results"]').bounding_box()
        await self.page.mouse.move(move["x"] + move["width"] / 2, move["y"] + move["height"] / 2)
        await self.page.mouse.down()
        await self.page.mouse.move(bounds["x"] + bounds["width"] * .9, bounds["y"] + 5)
        await self.page.mouse.up()
        self.assertEqual(await graphic.get_attribute("data-placement"), "top-right")

    async def test_resize_preserves_aspect_ratio_and_stops_at_container_space(self) -> None:
        await self.page.add_style_tag(path=str(ROOT / "static/css/graphics.css"))
        await self.page.add_style_tag(content="""
            [data-section="results"] { width: 260px; height: 140px; padding: 0; }
            [data-section="results"] .graphic { max-width: 100%; margin: 0; }
        """)
        await self.page.locator('[data-section="results"]').evaluate("""editor => {
            editor.innerHTML = '<span class="graphic" data-sid="22" data-type="png" ' +
                'data-placement="free" data-width="100" data-height="100" ' +
                'style="width:100px;height:100px"><img alt="Chart"></span>';
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 10;
            editor.querySelector('img').src = canvas.toDataURL();
        }""")
        await self.page.locator(".graphic img").evaluate("image => image.decode()")
        graphic = self.page.locator('[data-section="results"] .graphic')
        await graphic.click()
        handle = await self.page.locator(".graphic-handle-e").bounding_box()
        await self.page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
        await self.page.mouse.down()
        await self.page.mouse.move(handle["x"] + handle["width"] / 2 + 200, handle["y"] + handle["height"] / 2)
        await self.page.mouse.up()
        width = int(await graphic.get_attribute("data-width"))
        height = int(await graphic.get_attribute("data-height"))
        self.assertEqual(width, height)
        self.assertEqual(height, 140)
        self.assertLessEqual(await graphic.evaluate("element => element.getBoundingClientRect().bottom"),
                             await self.page.locator('[data-section="results"]').evaluate(
                                 "element => element.getBoundingClientRect().bottom"))

    async def test_graphic_clipboard_respects_destination_section(self) -> None:
        await self.page.add_style_tag(path=str(ROOT / "static/css/graphics.css"))
        await self.select_section("results")
        await self.page.locator("#image").click()
        await self.page.locator(".graphics-choice").first.click()
        await self.page.locator("#graphics-insert").click()
        await self.page.locator('[data-section="results"] .graphic').click()
        html = await self.page.evaluate("""() => {
            const data = new DataTransfer();
            const event = new ClipboardEvent('cut', {bubbles: true, cancelable: true, clipboardData: data});
            document.querySelector('[data-section="results"]').dispatchEvent(event);
            return data.getData('text/html');
        }""")
        self.assertIn('data-presto-pid="11"', html)
        self.assertEqual(await self.page.locator('[data-section="results"] .graphic').count(), 0)
        await self.select_section("introduction")
        await self.page.evaluate("""html => {
            const data = new DataTransfer();
            data.setData('text/html', html);
            const event = new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: data});
            document.querySelector('[data-section="introduction"]').dispatchEvent(event);
        }""", html)
        self.assertEqual(await self.page.locator('[data-section="introduction"] .graphic').count(), 0)
        self.assertIn("cannot be pasted", await self.page.evaluate("messages.at(-1)"))
        await self.select_section("materials and methods")
        await self.page.evaluate("""html => {
            const data = new DataTransfer();
            data.setData('text/html', html);
            document.querySelector('[data-section="materials and methods"]').dispatchEvent(
                new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: data})
            );
        }""", html)
        graphic = self.page.locator('[data-section="materials and methods"] .graphic')
        self.assertEqual(await graphic.count(), 1)
        self.assertEqual(await graphic.get_attribute("data-sid"), "22")
        await self.page.evaluate("""html => {
            const data = new DataTransfer();
            data.setData('text/html', html);
            document.querySelector('[data-section="materials and methods"]').dispatchEvent(
                new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: data})
            );
        }""", html)
        self.assertEqual(await graphic.count(), 1)
        self.assertIn("Ada, you must first cut or otherwise delete the existing graphic",
                      await self.page.evaluate("messages.at(-1)"))
        handled = await self.page.evaluate("""html => {
            const editor = document.querySelector('[data-section="materials and methods"]');
            const range = document.createRange();
            range.selectNodeContents(editor);
            range.collapse(false);
            return window.pastePosterGraphic(editor, range, html);
        }""", html)
        self.assertTrue(handled)
        self.assertEqual(await graphic.count(), 1)
