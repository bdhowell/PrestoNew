import unittest
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent.parent
FORMATTING = {
    "bold": "b",
    "italic": "i",
    "underline": "u",
    "strikethrough": "strike",
    "superscript": "sup",
    "subscript": "sub",
    "code": "code",
}


class SectionFormattingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch()
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page()
        await self.page.route(
            "https://presto.test/**", lambda route: route.fulfill(body="<!doctype html>"),
        )

    async def load_editor(self) -> None:
        await self.page.goto("https://presto.test/")
        await self.page.set_content(
            '<div class="content" contenteditable="true" data-section="results">'
            "Sample text</div>"
            + (ROOT / "templates/layoutfoot.html").read_text()
        )
        await self.page.evaluate("""() => {
            window.savedRequests = [];
            window.fetch = async (url, options) => {
                window.savedRequests.push(JSON.parse(options.body));
                return {ok: true, json: async () => ({ok: true})};
            };
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-content.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-editor.js"))
        await self.page.locator(".content").evaluate("""element => {
            element.focus();
            const range = document.createRange();
            range.selectNodeContents(element);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }""")

    async def test_buttons_format_toggle_and_save_the_selected_text(self) -> None:
        for button, tag in FORMATTING.items():
            with self.subTest(button=button):
                await self.load_editor()
                control = self.page.locator(f"#{button}")
                await control.click()
                self.assertEqual(await self.page.locator(f".content {tag}").count(), 1)
                self.assertEqual(await self.page.locator(f".content {tag}").inner_text(), "Sample text")
                self.assertEqual(await control.get_attribute("aria-pressed"), "true")
                await self.page.wait_for_function(
                    "document.getElementById('content-save-status').textContent === 'Changes saved.'"
                )
                self.assertEqual(
                    await self.page.evaluate("window.savedRequests.at(-1)"),
                    {"section": "results", "content": f"<{tag}>Sample text</{tag}>"},
                )

                await control.click()
                self.assertEqual(await self.page.locator(f".content {tag}").count(), 0)
                self.assertEqual(await control.get_attribute("aria-pressed"), "false")
                self.assertEqual(await self.page.locator(".content").inner_text(), "Sample text")

    async def test_toolbar_status_and_zoom_alignment(self) -> None:
        await self.load_editor()
        await self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        await self.page.locator("#content-save-status").evaluate(
            "element => element.textContent = 'Changes saved.'",
        )
        for width in (1600, 900, 360):
            with self.subTest(width=width):
                await self.page.set_viewport_size({"width": width, "height": 700})
                bounds = await self.page.evaluate("""() => {
                    const rect = id => document.getElementById(id).getBoundingClientRect();
                    const bold = rect('bold');
                    const redo = rect('redo');
                    const status = rect('content-save-status');
                    const slider = rect('poster-zoom');
                    const value = rect('poster-zoom-value');
                    return {
                        leftInset: bold.left, rightInset: innerWidth - value.right,
                        statusAfterRedo: Boolean(document.querySelector(
                            '#editor-controls #redo + #content-save-status')),
                        statusLeft: status.left, redoRight: redo.right,
                        statusTop: status.top, redoBottom: redo.bottom,
                        sliderGap: value.left - slider.right,
                        sliderTop: slider.top, valueBottom: value.bottom,
                        pageWidth: document.documentElement.scrollWidth,
                    };
                }""")
                self.assertAlmostEqual(bounds["leftInset"], bounds["rightInset"], delta=0.1)
                self.assertTrue(bounds["statusAfterRedo"])
                if width == 1600:
                    self.assertGreater(bounds["statusLeft"], bounds["redoRight"])
                    self.assertLess(bounds["statusTop"], bounds["redoBottom"])
                self.assertAlmostEqual(bounds["sliderGap"], 6, delta=0.1)
                self.assertLess(bounds["sliderTop"], bounds["valueBottom"])
                self.assertLessEqual(bounds["pageWidth"], width)

    async def test_buttons_format_text_typed_at_the_caret(self) -> None:
        for button, tag in FORMATTING.items():
            with self.subTest(button=button):
                await self.load_editor()
                await self.page.locator(".content").evaluate("""element => {
                    getSelection().collapse(element.firstChild, element.textContent.length);
                }""")
                await self.page.locator(f"#{button}").click()
                await self.page.keyboard.type("typed")
                self.assertEqual(await self.page.locator(f".content {tag}").inner_text(), "typed")
                await self.page.wait_for_function(
                    "document.getElementById('content-save-status').textContent === 'Changes saved.'"
                )

    async def test_plain_text_removes_nested_inline_formatting_and_saves(self) -> None:
        await self.load_editor()
        await self.page.locator(".content").evaluate("""element => {
            element.innerHTML = '<b><i><u><strike>Styled</strike></u></i></b> '
                + '<code>code</code> <sup>super</sup> <sub>sub</sub> '
                + '<a href="https://example.com">link</a>';
            const range = document.createRange();
            range.selectNodeContents(element);
            getSelection().removeAllRanges();
            getSelection().addRange(range);
        }""")
        await self.page.locator("#plaintext").click()
        self.assertEqual(
            await self.page.locator(".content").inner_html(),
            "Styled code super sub link",
        )
        self.assertEqual(await self.page.evaluate("getSelection().toString()"), "Styled code super sub link")
        for button in FORMATTING:
            self.assertEqual(await self.page.locator(f"#{button}").get_attribute("aria-pressed"), "false")
        self.assertEqual(await self.page.locator("#code").get_attribute("aria-pressed"), "false")
        await self.page.wait_for_function(
            "document.getElementById('content-save-status').textContent === 'Changes saved.'"
        )
        self.assertEqual(
            await self.page.evaluate("window.savedRequests.at(-1)"),
            {"section": "results", "content": "Styled code super sub link"},
        )

    async def test_plain_text_preserves_formatting_outside_a_partial_selection(self) -> None:
        await self.load_editor()
        await self.page.locator(".content").evaluate("""element => {
            element.innerHTML = '<b><code>Before selected after</code></b>';
            const text = element.querySelector('code').firstChild;
            const range = document.createRange();
            range.setStart(text, 7);
            range.setEnd(text, 15);
            getSelection().removeAllRanges();
            getSelection().addRange(range);
        }""")
        await self.page.locator("#plaintext").click()
        self.assertEqual(
            await self.page.locator(".content").inner_html(),
            "<b><code>Before </code></b>selected<b><code> after</code></b>",
        )
        self.assertEqual(await self.page.evaluate("getSelection().toString()"), "selected")

    async def test_plain_text_is_disabled_without_selected_text(self) -> None:
        await self.load_editor()
        await self.page.locator(".content").evaluate("""element => {
            getSelection().collapse(element.firstChild, 0);
        }""")
        await self.page.wait_for_function("document.getElementById('plaintext').disabled")
        self.assertEqual(await self.page.locator(".content").inner_html(), "Sample text")
        self.assertEqual(await self.page.evaluate("window.savedRequests"), [])

    async def test_partial_code_toggle_preserves_surrounding_code(self) -> None:
        await self.load_editor()
        await self.page.locator(".content").evaluate("""element => {
            element.innerHTML = '<code>Before selected after</code>';
            const text = element.firstChild.firstChild;
            const range = document.createRange();
            range.setStart(text, 7);
            range.setEnd(text, 15);
            getSelection().removeAllRanges();
            getSelection().addRange(range);
        }""")
        await self.page.locator("#code").click()
        self.assertEqual(
            await self.page.locator(".content").inner_html(),
            "<code>Before </code>selected<code> after</code>",
        )
        self.assertEqual(await self.page.evaluate("getSelection().toString()"), "selected")

    async def test_toolbar_does_not_format_outside_or_across_editable_sections(self) -> None:
        await self.load_editor()
        await self.page.evaluate("""() => {
            const other = document.createElement('div');
            other.className = 'content';
            other.contentEditable = 'true';
            other.textContent = 'Other text';
            document.body.append(other);
            const range = document.createRange();
            range.setStart(document.querySelector('.content').firstChild, 0);
            range.setEnd(other.firstChild, 5);
            getSelection().removeAllRanges();
            getSelection().addRange(range);
        }""")
        await self.page.wait_for_function("document.getElementById('bold').disabled")
        await self.page.locator("#poster-zoom").focus()
        for button in (*FORMATTING, "plaintext", "cut", "copy", "paste"):
            self.assertTrue(await self.page.locator(f"#{button}").is_disabled())
        self.assertEqual(await self.page.evaluate("window.savedRequests"), [])

    async def test_keyboard_toolbar_activation_preserves_selection(self) -> None:
        await self.load_editor()
        await self.page.locator("#bold").focus()
        await self.page.keyboard.press("Enter")
        self.assertEqual(await self.page.locator(".content b").inner_text(), "Sample text")
        self.assertEqual(await self.page.evaluate("getSelection().toString()"), "Sample text")

    async def test_code_can_be_turned_off_at_the_caret(self) -> None:
        await self.load_editor()
        await self.page.locator(".content").evaluate("""element => {
            element.innerHTML = '<code>Sample text</code>';
            getSelection().collapse(element.firstChild.firstChild, 7);
        }""")
        await self.page.locator("#code").click()
        await self.page.keyboard.type("plain")
        self.assertEqual(
            await self.page.locator(".content").inner_html(),
            "<code>Sample </code>plain<code>text</code>",
        )

    async def test_code_toggle_preserves_other_inline_formatting(self) -> None:
        await self.load_editor()
        await self.page.locator(".content").evaluate("""element => {
            element.innerHTML = '<code><b>Before selected after</b></code>';
            const text = element.querySelector('b').firstChild;
            const range = document.createRange();
            range.setStart(text, 7);
            range.setEnd(text, 15);
            getSelection().removeAllRanges();
            getSelection().addRange(range);
        }""")
        await self.page.locator("#code").click()
        self.assertEqual(
            await self.page.locator(".content").inner_html(),
            "<code><b>Before </b></code><b>selected</b><code><b> after</b></code>",
        )

    async def test_copy_and_cut_preserve_rich_text_on_the_system_clipboard(self) -> None:
        await self.load_editor()
        await self.page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        await self.page.locator("#bold").click()
        await self.page.wait_for_function(
            "document.getElementById('content-save-status').textContent === 'Changes saved.'"
        )
        await self.page.evaluate("window.savedRequests = []")
        await self.page.locator("#copy").click()
        clipboard = await self.page.evaluate("""async () => {
            const items = await navigator.clipboard.read();
            return {
                text: await (await items[0].getType('text/plain')).text(),
                html: await (await items[0].getType('text/html')).text(),
            };
        }""")
        self.assertEqual(clipboard["text"], "Sample text")
        self.assertIn("Sample text", clipboard["html"])
        self.assertIn("<b", clipboard["html"])
        self.assertEqual(await self.page.locator(".content").inner_html(), "<b>Sample text</b>")
        self.assertEqual(await self.page.evaluate("window.savedRequests"), [])
        self.assertEqual(await self.page.evaluate("getSelection().toString()"), "Sample text")

        await self.page.locator("#cut").click()
        self.assertEqual((await self.page.locator(".content").inner_text()).strip(), "")
        self.assertEqual(await self.page.evaluate("navigator.clipboard.readText()"), "Sample text")
        await self.page.wait_for_function("window.savedRequests.length === 1")
        self.assertNotIn("Sample text", (await self.page.evaluate("window.savedRequests[0]"))["content"])
        await self.page.locator("#paste").click()
        await self.page.wait_for_function("window.savedRequests.length === 2")
        self.assertEqual(await self.page.locator(".content b").inner_text(), "Sample text")

    async def test_rich_paste_replaces_selection_and_saves_safe_formatting(self) -> None:
        await self.load_editor()
        await self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {value: {
                read: async () => [{
                    types: ['text/html', 'text/plain'],
                    getType: async () => new Blob([
                        '<b onclick="alert(1)">Bold</b><i>Italic</i><code>Code</code>'
                        + '<u>Under</u><strike>Strike</strike><sup>Up</sup><sub>Down</sub>'
                        + '<script>alert(1)</script><img src=x onerror="alert(1)">'
                        + '<a href="javascript:alert(1)">Unsafe</a>'
                        + '<a href="https://example.com">Safe</a>'
                    ], {type: 'text/html'}),
                }],
            }});
        }""")
        await self.page.locator("#paste").click()
        expected = (
            "<b>Bold</b><i>Italic</i><code>Code</code><u>Under</u>"
            "<strike>Strike</strike><sup>Up</sup><sub>Down</sub>"
            '<a>Unsafe</a><a href="https://example.com">Safe</a>'
        )
        await self.page.wait_for_function("window.savedRequests.length === 1")
        self.assertEqual(await self.page.locator(".content").inner_html(), expected)
        self.assertEqual(
            await self.page.evaluate("window.savedRequests[0]"),
            {"section": "results", "content": expected},
        )

    async def test_plain_clipboard_text_is_not_interpreted_as_html(self) -> None:
        await self.load_editor()
        await self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {value: {
                read: async () => [{
                    types: ['text/plain'],
                    getType: async () => new Blob(['<b>literal</b>'], {type: 'text/plain'}),
                }],
            }});
            const element = document.querySelector('.content');
            getSelection().collapse(element.firstChild, element.textContent.length);
        }""")
        await self.page.locator("#paste").click()
        await self.page.wait_for_function("window.savedRequests.length === 1")
        self.assertEqual(
            await self.page.locator(".content").inner_html(),
            "Sample text&lt;b&gt;literal&lt;/b&gt;",
        )

    async def test_clipboard_controls_are_disabled_without_a_valid_selection(self) -> None:
        await self.load_editor()
        await self.page.locator(".content").evaluate("""element => {
            getSelection().collapse(element.firstChild, 0);
        }""")
        await self.page.wait_for_function("document.getElementById('cut').disabled")
        self.assertTrue(await self.page.locator("#copy").is_disabled())
        self.assertFalse(await self.page.locator("#paste").is_disabled())
        await self.page.locator("#poster-zoom").focus()
        for action in ("cut", "copy", "paste"):
            self.assertTrue(await self.page.locator(f"#{action}").is_disabled())

    async def test_denied_paste_reports_error_without_changing_content(self) -> None:
        await self.load_editor()
        await self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {value: {
                read: async () => { throw new DOMException('Denied', 'NotAllowedError'); },
            }});
        }""")
        await self.page.locator("#paste").click()
        await self.page.wait_for_function(
            "document.getElementById('content-save-status').textContent.includes('denied')"
        )
        self.assertEqual(await self.page.locator(".content").inner_html(), "Sample text")
        self.assertEqual(await self.page.evaluate("window.savedRequests"), [])

    async def test_pending_paste_does_not_replace_a_changed_selection(self) -> None:
        await self.load_editor()
        await self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {value: {
                read: () => new Promise(resolve => { window.finishRead = resolve; }),
            }});
        }""")
        await self.page.locator("#paste").click()
        await self.page.locator(".content").evaluate("""element => {
            getSelection().collapse(element.firstChild, 0);
        }""")
        await self.page.wait_for_function("document.getElementById('copy').disabled")
        await self.page.evaluate("""() => window.finishRead([{
            types: ['text/plain'],
            getType: async () => new Blob(['Pasted'], {type: 'text/plain'}),
        }])""")
        await self.page.wait_for_function(
            "document.getElementById('content-save-status').textContent.includes('selection changed')"
        )
        self.assertEqual(await self.page.locator(".content").inner_html(), "Sample text")
        self.assertEqual(await self.page.evaluate("window.savedRequests"), [])

    async def test_paste_converts_common_inline_styles_to_supported_tags(self) -> None:
        await self.load_editor()
        await self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {value: {
                read: async () => [{
                    types: ['text/html'],
                    getType: async () => new Blob([
                        '<div><span style="font-weight:700;font-style:italic;'
                        + 'text-decoration:underline line-through;color:red" onclick="bad()">'
                        + 'Styled</span></div><div><span style="vertical-align:super">Up</span>'
                        + '<span style="vertical-align:sub">Down</span></div>'
                    ], {type: 'text/html'}),
                }],
            }});
        }""")
        await self.page.locator("#paste").click()
        await self.page.wait_for_function("window.savedRequests.length === 1")
        self.assertEqual(await self.page.locator(".content b").inner_text(), "Styled")
        self.assertEqual(await self.page.locator(".content i").inner_text(), "Styled")
        self.assertEqual(await self.page.locator(".content u").inner_text(), "Styled")
        self.assertEqual(await self.page.locator(".content strike").inner_text(), "Styled")
        self.assertEqual(await self.page.locator(".content sup").inner_text(), "Up")
        self.assertEqual(await self.page.locator(".content sub").inner_text(), "Down")
        self.assertNotIn("style=", await self.page.locator(".content").inner_html())
        self.assertNotIn("onclick", await self.page.locator(".content").inner_html())

    async def test_denied_cut_and_copy_leave_the_selection_intact(self) -> None:
        for action in ("cut", "copy"):
            with self.subTest(action=action):
                await self.load_editor()
                await self.page.evaluate("document.execCommand = () => false")
                await self.page.locator(f"#{action}").click()
                self.assertIn("denied", await self.page.locator("#content-save-status").inner_text())
                self.assertEqual(await self.page.locator(".content").inner_html(), "Sample text")
                self.assertEqual(await self.page.evaluate("getSelection().toString()"), "Sample text")
                self.assertEqual(await self.page.evaluate("window.savedRequests"), [])

    async def test_clipboard_read_text_fallback(self) -> None:
        await self.load_editor()
        await self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {value: {
                readText: async () => 'Plain clipboard',
            }});
        }""")
        await self.page.locator("#paste").focus()
        await self.page.keyboard.press("Enter")
        await self.page.wait_for_function("window.savedRequests.length === 1")
        self.assertEqual(await self.page.locator(".content").inner_html(), "Plain clipboard")

    async def test_partial_cut_preserves_surrounding_content_and_saves(self) -> None:
        await self.load_editor()
        await self.page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        await self.page.locator(".content").evaluate("""element => {
            element.innerHTML = '<b>Before selected after</b>';
            const range = document.createRange();
            range.setStart(element.firstChild.firstChild, 7);
            range.setEnd(element.firstChild.firstChild, 15);
            getSelection().removeAllRanges();
            getSelection().addRange(range);
        }""")
        await self.page.locator("#cut").focus()
        await self.page.keyboard.press("Enter")
        self.assertEqual(await self.page.evaluate("navigator.clipboard.readText()"), "selected")
        remaining = await self.page.locator(".content b").text_content()
        self.assertEqual(remaining.replace("\u00a0", " "), "Before  after")
        await self.page.wait_for_function("window.savedRequests.length === 1")
        self.assertEqual(
            (await self.page.evaluate("window.savedRequests[0]"))["content"],
            await self.page.locator(".content").inner_html(),
        )
