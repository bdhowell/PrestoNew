import unittest
from pathlib import Path

from jinja2 import Environment
from playwright.async_api import async_playwright

import posters


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


class AppearanceDialogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.page.route(
            "https://presto.test/**", lambda route: route.fulfill(body="<!doctype html>"),
        )
        await self.page.goto("https://presto.test/")
        footer = Environment(autoescape=True).from_string(
            (ROOT / "templates/layoutfoot.html").read_text(),
        ).render(poster={"pid": 11}, themes=posters.installed_themes(),
                 font_sets=posters.installed_fonts())
        await self.page.set_content(
            '<body class="blue corners-square elegant"><div class="content" contenteditable="true">Sample text</div>'
            + footer,
        )
        for stylesheet in ("layout", "fonts", "themes"):
            await self.page.add_style_tag(path=str(ROOT / f"static/css/{stylesheet}.css"))
        await self.page.evaluate("""() => {
            window.savedRequests = [];
            window.fetch = async (url, options) => {
                window.savedRequests.push({url, data: JSON.parse(options.body)});
                return {ok: true, json: async () => ({ok: true})};
            };
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-theme.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-editor.js"))
        self.assertTrue(await self.page.locator("#appearance-controls").is_hidden())
        await self.page.locator("#appearance").click()
        self.assertEqual(await self.page.locator("#appearance").get_attribute("aria-expanded"), "true")

    async def test_cancel_escape_and_apply_preserve_other_appearance_setting(self) -> None:
        for button, setting, current, target in (
            ("font", "fonts", "elegant", "technical"),
        ):
            with self.subTest(button=button):
                await self.page.locator(f"#{button}").click()
                self.assertTrue(await self.page.locator(f'input[value="{current}"]').is_checked())
                await self.page.locator(f'label:has(input[value="{target}"])').click()
                await self.page.locator(f"#{button}-cancel").click()
                self.assertEqual(await self.page.evaluate("window.savedRequests.length"), 0)
                self.assertIn(current, (await self.page.locator("body").get_attribute("class")).split())
                await self.page.locator(f"#{button}").click()
                self.assertTrue(await self.page.locator(f'input[value="{current}"]').is_checked())
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_function(
                    f"!document.getElementById('{button}-dialog').open",
                )
                self.assertFalse(await self.page.locator(f"#{button}-dialog").is_visible())
                self.assertTrue(await self.page.locator("#appearance-controls").is_visible())
                await self.page.locator(f"#{button}").click()
                await self.page.locator(f'label:has(input[value="{target}"])').click()
                await self.page.locator(f"#{button}-apply").click()
                await self.page.wait_for_function(
                    f"!document.getElementById('{button}-dialog').open",
                )
                self.assertEqual(set((await self.page.locator("body").get_attribute("class")).split()), {
                    "blue", "corners-square", target,
                })
                self.assertEqual(await self.page.evaluate("window.savedRequests.pop()"), {
                    "url": f"/layout/{setting}", "data": {"pid": 11, setting: target},
                })

    async def test_theme_controls_save_all_settings_and_keep_other_appearance(self) -> None:
        await self.page.locator("#theme").click()
        self.assertTrue(await self.page.locator('input[name="color"][value="blue"]').is_checked())
        self.assertTrue(await self.page.locator('input[name="corners"][value="square"]').is_checked())
        await self.page.locator('label:has(input[name="color"][value="purple"])').click()
        await self.page.locator('label:has(input[name="corners"][value="very-rounded"])').click()
        await self.page.locator('label:has(input[name="colored_poster_header"])').click()
        await self.page.locator('label:has(input[name="colored_section_headers"])').click()
        await self.page.locator("#theme-apply").click()
        await self.page.wait_for_function("!document.getElementById('theme-dialog').open")
        self.assertEqual(set((await self.page.locator("body").get_attribute("class")).split()), {
            "purple", "corners-very-rounded", "colored-poster-header",
            "colored-section-headers", "elegant",
        })
        self.assertEqual(await self.page.evaluate("window.savedRequests.pop()"), {
            "url": "/layout/theme",
            "data": {
                "pid": 11, "color": "purple", "corners": "very-rounded",
                "colored_poster_header": True, "colored_section_headers": True,
            },
        })

    async def test_corner_and_header_examples_follow_selected_color_and_shape(self) -> None:
        await self.page.locator("#theme").click()
        await self.page.locator('label:has(input[name="color"][value="yellow"])').click()
        await self.page.locator('label:has(input[name="corners"][value="rounded"])').click()
        examples = await self.page.locator(
            ".theme-corner-option, .theme-toggle-option",
        ).evaluate_all("""elements => elements.map(element => {
            const style = getComputedStyle(element);
            return {
                background: style.backgroundColor,
                color: style.color,
                radius: style.borderRadius,
            };
        })""")
        for example in examples:
            self.assertEqual(example["background"], "rgb(225, 181, 28)")
            self.assertEqual(example["color"], "rgb(34, 34, 34)")
        for example, expected in zip(examples[:4], (0, 20 / 3, 40 / 3, 20)):
            self.assertAlmostEqual(float(example["radius"].removesuffix("px")), expected, places=2)
        for example in examples[4:]:
            self.assertAlmostEqual(
                float(example["radius"].removesuffix("px")),
                40 / 3,
                places=2,
            )

    async def test_failed_save_stays_open_and_can_be_retried(self) -> None:
        await self.page.evaluate("""() => {
            window.fetch = async () => ({
                ok: false, json: async () => ({detail: 'Storage unavailable.'})
            });
        }""")
        await self.page.locator("#font").click()
        await self.page.locator('label:has(input[value="modern"])').click()
        await self.page.locator("#font-apply").click()
        self.assertEqual(
            await self.page.locator("#font-status").inner_text(),
            "Storage unavailable. Please try again.",
        )
        self.assertTrue(await self.page.locator("#font-dialog").is_visible())
        self.assertTrue(await self.page.locator("#font-apply").is_enabled())
        self.assertEqual(
            set((await self.page.locator("body").get_attribute("class")).split()),
            {"blue", "corners-square", "elegant"},
        )
        await self.page.evaluate("""() => {
            window.fetch = async () => ({ok: true, json: async () => ({ok: true})});
        }""")
        await self.page.locator("#font-apply").click()
        await self.page.wait_for_function("!document.getElementById('font-dialog').open")
        self.assertEqual(
            set((await self.page.locator("body").get_attribute("class")).split()),
            {"blue", "corners-square", "modern"},
        )

    async def test_font_previews_use_their_own_fonts_and_fit_small_screens(self) -> None:
        await self.page.locator("#font").click()
        for option in posters.installed_fonts():
            fonts = await self.page.locator(f".font-preview.{option}").evaluate("""element => ({
                body: getComputedStyle(element).fontFamily,
                heading: getComputedStyle(element.querySelector('.header')).fontFamily,
                code: getComputedStyle(element.querySelector('code')).fontFamily,
            })""")
            self.assertEqual(len(set(fonts.values())), 3)
        await self.page.set_viewport_size({"width": 360, "height": 640})
        bounds = await self.page.locator("#font-dialog").bounding_box()
        self.assertGreaterEqual(bounds["x"], 0)
        self.assertLessEqual(bounds["x"] + bounds["width"], 360)
        self.assertLessEqual(bounds["height"], 640)

    async def test_palette_buttons_have_contrasting_theme_colors(self) -> None:
        await self.page.locator("#theme").click()
        self.assertEqual(
            await self.page.locator(".theme-color-option").count(),
            len(posters.THEME_COLORS),
        )
        buttons = await self.page.locator(".theme-color-option").evaluate_all("""elements =>
            elements.map(element => {
                const style = getComputedStyle(element);
                const luminance = color => {
                    const rgb = color.match(/\\d+/g).slice(0, 3).map(value => {
                        const channel = Number(value) / 255;
                        return channel <= 0.04045 ? channel / 12.92
                            : ((channel + 0.055) / 1.055) ** 2.4;
                    });
                    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
                };
                const text = luminance(style.color);
                const background = luminance(style.backgroundColor);
                return {
                    contrast: (Math.max(text, background) + 0.05)
                        / (Math.min(text, background) + 0.05),
                    inputVisible: getComputedStyle(element.querySelector("input")).opacity !== "0"
                        && getComputedStyle(element.querySelector("input")).clipPath === "none",
                };
            })""")
        for button in buttons:
            self.assertGreaterEqual(button["contrast"], 4.5)
            self.assertFalse(button["inputVisible"])
        self.assertTrue(await self.page.locator(".theme-color-option").first.evaluate(
            "element => getComputedStyle(element).backgroundColor !== 'rgba(0, 0, 0, 0)'",
        ))
        for corner, radius in (
            ("square", 0), ("slightly-rounded", 20 / 3),
            ("rounded", 40 / 3), ("very-rounded", 20),
        ):
            button_radius = await self.page.locator(
                f'.theme-corner-option:has(input[value="{corner}"])',
            ).evaluate("element => getComputedStyle(element).borderRadius")
            self.assertAlmostEqual(float(button_radius.removesuffix("px")), radius, places=2)
        await self.page.evaluate("""() => {
            const header = document.createElement("div");
            header.className = "poster-header";
            header.textContent = "Poster title";
            document.body.append(header);
            document.body.classList.add("colored-poster-header", "colored-section-headers");
        }""")
        for theme in posters.THEME_COLORS:
            with self.subTest(theme=theme):
                actual = await self.page.locator(".poster-header").evaluate("""(element, theme) => {
                    document.body.classList.remove(...document.body.classList);
                    document.body.classList.add(theme, "colored-poster-header");
                    return getComputedStyle(element).backgroundColor;
                }""", theme)
                self.assertNotEqual(actual, "rgba(0, 0, 0, 0)")

    async def test_read_only_header_colors_are_opt_in(self) -> None:
        await self.page.set_content("""
            <article class="poster-document blue corners-rounded">
                <header>Poster title</header>
                <h2>Section title</h2>
                <div id="sample-section" class="section">Section content</div>
            </article>
        """)
        for path in ("themes.css", "pages/poster-document.css"):
            await self.page.add_style_tag(path=str(ROOT / "static/css" / path))
        before = await self.page.locator(".poster-document").evaluate("""element => [
            getComputedStyle(element.querySelector("header")).backgroundColor,
            getComputedStyle(element.querySelector("h2")).backgroundColor,
            getComputedStyle(element.querySelector("h2")).borderRadius,
            getComputedStyle(element.querySelector(".section")).borderRadius,
        ]""")
        self.assertEqual(before[:2], ["rgba(0, 0, 0, 0)", "rgba(0, 0, 0, 0)"])
        self.assertEqual(before[2:], ["20px", "40px"])
        for corner, section_radius in (
            ("square", "0px"), ("slightly-rounded", "20px"),
            ("rounded", "40px"), ("very-rounded", "60px"),
        ):
            radius = await self.page.locator("#sample-section").evaluate(
                """(element, corner) => {
                    element.closest(".poster-document").classList.remove(
                        "corners-square", "corners-slightly-rounded",
                        "corners-rounded", "corners-very-rounded",
                    );
                    element.closest(".poster-document").classList.add(`corners-${corner}`);
                    return getComputedStyle(element).borderRadius;
                }""", corner,
            )
            self.assertEqual(radius, section_radius)
        after = await self.page.locator(".poster-document").evaluate("""element => {
            element.classList.add("colored-poster-header", "colored-section-headers");
            return [
                getComputedStyle(element.querySelector("header")).backgroundColor,
                getComputedStyle(element.querySelector("h2")).backgroundColor,
            ];
        }""")
        self.assertNotEqual(after[0], "rgba(0, 0, 0, 0)")
        self.assertNotEqual(after[1], "rgba(0, 0, 0, 0)")

    async def test_new_theme_colors_have_readable_contrast_in_documents(self) -> None:
        await self.page.set_content("""
            <article class="poster-document">
                <header>Poster title</header><h2>Section title</h2><p>Poster content</p>
            </article>
        """)
        for path in ("themes.css", "pages/poster-document.css"):
            await self.page.add_style_tag(path=str(ROOT / "static/css" / path))
        for theme in posters.THEME_COLORS:
            with self.subTest(theme=theme):
                contrasts = await self.page.locator("article").evaluate("""(element, theme) => {
                    element.className = `poster-document ${theme} colored-poster-header colored-section-headers`;
                    const luminance = color => {
                        const rgb = color.match(/\\d+/g).slice(0, 3).map(value => {
                            const channel = Number(value) / 255;
                            return channel <= 0.04045 ? channel / 12.92
                                : ((channel + 0.055) / 1.055) ** 2.4;
                        });
                        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
                    };
                    return [element, element.querySelector('header'), element.querySelector('h2')].map(node => {
                        const style = getComputedStyle(node);
                        const text = luminance(style.color);
                        const background = luminance(style.backgroundColor);
                        return (Math.max(text, background) + 0.05)
                            / (Math.min(text, background) + 0.05);
                    });
                }""", theme)
                for contrast in contrasts:
                    self.assertGreaterEqual(contrast, 4.5)


class ChatbotToggleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.page.route(
            "https://presto.test/**", lambda route: route.fulfill(body="<!doctype html>"),
        )
        await self.page.goto("https://presto.test/")
        await self.page.set_content('<body><button id="wikkit">Wikkit</button></body>')
        await self.page.add_style_tag(path=str(ROOT / "static/css/router.css"))
        await self.page.evaluate("""() => {
            class MockWebSocket extends EventTarget {
                constructor() {
                    super();
                    this.readyState = MockWebSocket.OPEN;
                    this.sent = [];
                    window.chatSocket = this;
                }
                send(text) { this.sent.push(text); }
                close() {}
            }
            MockWebSocket.CONNECTING = 0;
            MockWebSocket.OPEN = 1;
            window.WebSocket = MockWebSocket;
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/wikkit.js"))
        await self.page.evaluate("window.wikkit = connect(() => {})")

    async def prompt(self, local: bool = False) -> None:
        await self.page.evaluate("""local => {
            wikkit.say('<button data-sends="yes">Yes</button><button data-sends="no">No</button>'
                + '<input required><textarea></textarea><select><option>Choose</option>'
                + '<option>Other</option></select>');
            if (local) {
                window.answer = null;
                wikkit.requestResponse().then(answer => { window.answer = answer; });
            } else {
                return wikkit.execute({does: 'request-response'});
            }
        }""", local)

    async def test_typed_response_disables_all_controls_for_server_and_local_waits(self) -> None:
        for local in (False, True):
            with self.subTest(local=local):
                await self.prompt(local)
                await self.page.locator("#chatinput").fill("My answer")
                await self.page.locator("#chatinput").press("Enter")
                self.assertEqual(await self.page.locator(
                    "#chatlog :is(button, input, textarea, select):enabled",
                ).count(), 0)
                if local:
                    self.assertEqual(await self.page.evaluate("window.answer"), "My answer")
                await self.page.evaluate("wikkit.say('<button data-sends=\"next\">Next</button>')")
                self.assertTrue(await self.page.locator("#chatlog button").last.is_enabled())
                await self.page.evaluate("wikkit.execute({does: 'clear-chat'})")

    async def test_embedded_responses_disable_other_groups_and_fields(self) -> None:
        for control in ("button", "input", "textarea", "select"):
            with self.subTest(control=control):
                await self.prompt()
                if control == "button":
                    await self.page.locator("#chatlog button").first.click()
                elif control == "select":
                    await self.page.locator("#chatlog select").select_option("Other")
                else:
                    await self.page.locator(f"#chatlog {control}").fill("Answer")
                    if control == "input":
                        await self.page.locator("#chatlog input").press("Enter")
                    else:
                        await self.page.locator("#chatlog textarea + button").click()
                self.assertEqual(await self.page.locator(
                    "#chatlog :is(button, input, textarea, select):enabled",
                ).count(), 0)
                await self.page.evaluate("wikkit.execute({does: 'clear-chat'})")

    async def test_invalid_response_leaves_controls_active(self) -> None:
        await self.prompt()
        await self.page.locator("#chatlog input").press("Enter")
        self.assertTrue(await self.page.locator("#chatlog input").is_enabled())
        self.assertTrue(await self.page.locator("#chatlog button").first.is_enabled())
        self.assertEqual(await self.page.evaluate("chatSocket.sent"), [])

    async def test_late_memory_ack_cannot_submit_or_reenable_obsolete_controls(self) -> None:
        for ok in (True, False):
            with self.subTest(ok=ok):
                await self.prompt()
                await self.page.evaluate("""() => {
                    chatSocket.sent = [];
                    wikkit.say('<input id="remembered" data-memory="session" data-name="answer">');
                }""")
                await self.page.locator("#remembered").fill("Old answer")
                await self.page.locator("#remembered").press("Enter")
                await self.page.locator("#chatinput").fill("New answer")
                await self.page.locator("#chatinput").press("Enter")
                await self.page.evaluate("""ok => {
                    const {memory} = JSON.parse(chatSocket.sent[0]);
                    chatSocket.dispatchEvent(new MessageEvent('message', {
                        data: JSON.stringify({memory: {id: memory.id, ok, error: 'Write failed'}}),
                    }));
                }""", ok)
                self.assertEqual(await self.page.locator(
                    "#chatlog :is(button, input, textarea, select):enabled",
                ).count(), 0)
                self.assertEqual((await self.page.evaluate("chatSocket.sent"))[1:], ["New answer"])
                await self.page.evaluate("wikkit.execute({does: 'clear-chat'})")

    async def test_failed_memory_write_can_be_retried_without_completing_wait(self) -> None:
        await self.prompt(True)
        await self.page.evaluate("""() => {
            wikkit.say('<input id="remembered" data-memory="session" data-name="answer">');
        }""")
        await self.page.locator("#remembered").fill("Answer")
        await self.page.locator("#remembered").press("Enter")
        await self.page.evaluate("""() => {
            const {memory} = JSON.parse(chatSocket.sent[0]);
            chatSocket.dispatchEvent(new MessageEvent('message', {
                data: JSON.stringify({memory: {id: memory.id, ok: false, error: 'Write failed'}}),
            }));
        }""")
        self.assertTrue(await self.page.locator("#remembered").is_enabled())
        self.assertTrue(await self.page.locator("#remembered + button").is_enabled())
        self.assertTrue(await self.page.locator("#chatlog button").first.is_enabled())
        self.assertIsNone(await self.page.evaluate("window.answer"))
        await self.page.locator("#remembered").press("Enter")
        await self.page.evaluate("""() => {
            const {memory} = JSON.parse(chatSocket.sent[1]);
            chatSocket.dispatchEvent(new MessageEvent('message', {
                data: JSON.stringify({memory: {id: memory.id, ok: true}}),
            }));
        }""")
        self.assertEqual(await self.page.evaluate("window.answer"), "Answer")
        self.assertEqual(await self.page.locator(
            "#chatlog :is(button, input, textarea, select):enabled",
        ).count(), 0)

    async def test_completed_controls_remain_inactive_after_navigation(self) -> None:
        await self.prompt()
        await self.page.locator("#chatinput").fill("Answer")
        await self.page.locator("#chatinput").press("Enter")
        await self.page.goto("https://presto.test/next")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/wikkit.js"))
        self.assertGreater(await self.page.locator("#chatlog button").count(), 0)
        self.assertEqual(await self.page.locator(
            "#chatlog :is(button, input, textarea, select):enabled",
        ).count(), 0)

    async def test_clicking_outlined_element_or_child_hides_outline_without_blocking_click(self) -> None:
        await self.page.evaluate("""() => {
            const element = document.createElement('button');
            element.id = 'outlined';
            element.style.outline = '4px solid red';
            element.innerHTML = '<span>Outlined</span>';
            element.onclick = () => { element.dataset.clicked = 'true'; };
            document.body.prepend(element);
        }""")
        await self.page.locator("#outlined span").click()
        self.assertEqual(await self.page.locator("#outlined").get_attribute("data-clicked"), "true")
        self.assertEqual(await self.page.locator("#outlined").evaluate(
            "element => getComputedStyle(element).outlineStyle",
        ), "none")

    async def test_handle_appearance_matches_on_public_and_layout_pages(self) -> None:
        await self.page.add_style_tag(path=str(ROOT / "static/css/main.css"))
        await self.page.locator("body").evaluate("element => element.className = 'wikkit-page'")
        handle = self.page.locator("#chatbot .chatbot-handle")
        appearance = """element => {
            const style = getComputedStyle(element);
            const icon = getComputedStyle(element, '::before');
            return {
                fontFamily: style.fontFamily,
                fontSize: style.fontSize,
                fontWeight: style.fontWeight,
                lineHeight: style.lineHeight,
                height: element.getBoundingClientRect().height,
                color: style.color,
                background: style.backgroundColor,
                icon: icon.backgroundImage,
                iconWidth: icon.width,
                iconHeight: icon.height,
            };
        }"""
        public = await handle.evaluate(appearance)
        self.assertEqual(public["fontSize"], "32px")
        self.assertEqual(public["lineHeight"], "51.2px")
        self.assertIn("/static/graphics/wikkicon.png", public["icon"])
        self.assertEqual(public["iconWidth"], "32px")
        self.assertEqual(public["iconHeight"], "32px")

        await self.page.locator("body").evaluate("element => element.className = ''")
        await self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        self.assertEqual(await handle.evaluate(appearance), public)

    async def test_wikkit_button_toggles_chatbot_visibility_and_accessibility(self) -> None:
        chatbot = self.page.locator("#chatbot")
        button = self.page.locator("#wikkit")

        self.assertEqual(
            await chatbot.evaluate("element => getComputedStyle(element).transitionDuration"),
            "0.44s, 0s",
        )
        self.assertEqual(await chatbot.evaluate("element => getComputedStyle(element).visibility"), "visible")
        await button.click()
        self.assertTrue(
            await chatbot.evaluate("element => element.classList.contains('chat-hidden')"),
        )
        self.assertTrue(await chatbot.evaluate("element => element.inert"))
        self.assertEqual(await chatbot.get_attribute("aria-hidden"), "true")
        await self.page.wait_for_function(
            "getComputedStyle(document.getElementById('chatbot')).visibility === 'hidden'",
        )

        await button.click()
        self.assertFalse(
            await chatbot.evaluate("element => element.classList.contains('chat-hidden')"),
        )
        self.assertFalse(await chatbot.evaluate("element => element.inert"))
        self.assertIsNone(await chatbot.get_attribute("aria-hidden"))
        await self.page.wait_for_function(
            "getComputedStyle(document.getElementById('chatbot')).visibility === 'visible'",
        )


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
            document.getElementById('theme-dialog').remove();
            document.getElementById('font-dialog').remove();
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
        self.assertTrue(await self.page.locator("#extended-controls").is_hidden())
        self.assertEqual(await self.page.locator("#extended").get_attribute("aria-expanded"), "false")
        await self.page.locator("#extended").click()

    async def test_extended_tray_toggles_without_resizing_or_losing_selection(self) -> None:
        await self.load_editor()
        await self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        await self.page.locator("#extended").click()
        tray = self.page.locator("#extended-controls")
        self.assertTrue(await tray.is_hidden())
        self.assertEqual(await self.page.locator("#extended").get_attribute("aria-expanded"), "false")
        for width in (1600, 900, 360):
            with self.subTest(width=width):
                await self.page.set_viewport_size({"width": width, "height": 700})
                before = await self.page.locator("#controls").bounding_box()
                await self.page.locator("#extended").click()
                self.assertTrue(await tray.is_visible())
                self.assertEqual(
                    await self.page.locator("#extended").get_attribute("aria-expanded"), "true",
                )
                self.assertEqual(await self.page.locator("#controls").bounding_box(), before)
                bounds = await tray.bounding_box()
                self.assertLessEqual(bounds["y"] + bounds["height"], before["y"])
                self.assertGreaterEqual(bounds["x"], 0)
                self.assertLessEqual(bounds["x"] + bounds["width"], width)
                self.assertEqual(await self.page.evaluate("getSelection().toString()"), "Sample text")
                await self.page.locator("#extended").click()
                self.assertTrue(await tray.is_hidden())

        await self.page.locator("#extended").focus()
        await self.page.keyboard.press("Enter")
        await self.page.locator("#code").focus()
        await self.page.keyboard.press("Escape")
        self.assertTrue(await tray.is_hidden())
        self.assertEqual(await self.page.locator(":focus").get_attribute("id"), "extended")
        self.assertEqual(await self.page.evaluate("window.savedRequests"), [])

    async def test_list_buttons_toggle_switch_and_save(self) -> None:
        for button, tag, other in (("ullist", "ul", "ollist"), ("ollist", "ol", "ullist")):
            with self.subTest(button=button):
                await self.load_editor()
                await self.page.locator(f"#{button}").click()
                self.assertEqual(await self.page.locator(".content").inner_html(),
                                 f"<{tag}><li>Sample text</li></{tag}>")
                self.assertEqual(
                    await self.page.locator(f"#{button}").get_attribute("aria-pressed"), "true",
                )
                await self.page.wait_for_function(
                    "document.getElementById('content-save-status').textContent === 'Changes saved.'"
                )
                self.assertEqual(await self.page.evaluate("window.savedRequests.at(-1)"), {
                    "section": "results", "content": f"<{tag}><li>Sample text</li></{tag}>",
                })
                await self.page.locator(f"#{other}").click()
                self.assertEqual(await self.page.locator(f".content {tag}").count(), 0)
                self.assertEqual(
                    await self.page.locator(f"#{button}").get_attribute("aria-pressed"), "false",
                )
                self.assertEqual(
                    await self.page.locator(f"#{other}").get_attribute("aria-pressed"), "true",
                )
                await self.page.locator(f"#{other}").click()
                self.assertEqual(await self.page.locator(".content li").count(), 0)
                self.assertEqual(await self.page.locator(".content").inner_text(), "Sample text")

    async def test_lists_apply_to_selected_paragraphs_and_caret(self) -> None:
        for button, tag in (("ullist", "ul"), ("ollist", "ol")):
            with self.subTest(button=button):
                await self.load_editor()
                await self.page.locator(".content").evaluate("""element => {
                    element.innerHTML = '<p>First</p><p>Second</p><p>Outside</p>';
                    const range = document.createRange();
                    range.setStart(element.children[0].firstChild, 0);
                    range.setEnd(element.children[1].firstChild, 6);
                    getSelection().removeAllRanges();
                    getSelection().addRange(range);
                }""")
                await self.page.locator(f"#{button}").click()
                self.assertEqual(
                    await self.page.locator(f".content {tag} li").all_text_contents(),
                    ["First", "Second"],
                )
                self.assertEqual(
                    await self.page.locator(".content > p:last-child").inner_text(), "Outside",
                )
                await self.load_editor()
                await self.page.locator(".content").evaluate("""element => {
                    getSelection().collapse(element.firstChild, 0);
                }""")
                await self.page.locator(f"#{button}").click()
                await self.page.keyboard.type("typed ")
                self.assertEqual(
                    await self.page.locator(f".content {tag} li").inner_text(), "typed Sample text",
                )

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
                    const wikkit = rect('wikkit');
                    const status = rect('content-save-status');
                    const slider = rect('poster-zoom');
                    const value = rect('poster-zoom-value');
                    return {
                        leftInset: bold.left, rightInset: innerWidth - value.right,
                        statusAfterWikkit: Boolean(document.querySelector(
                            '#editor-controls #wikkit + #content-save-status')),
                        statusLeft: status.left, wikkitRight: wikkit.right,
                        statusTop: status.top, wikkitBottom: wikkit.bottom,
                        sliderGap: value.left - slider.right,
                        sliderTop: slider.top, valueBottom: value.bottom,
                        pageWidth: document.documentElement.scrollWidth,
                    };
                }""")
                self.assertAlmostEqual(bounds["leftInset"], bounds["rightInset"], delta=0.1)
                self.assertTrue(bounds["statusAfterWikkit"])
                if width == 1600:
                    self.assertGreater(bounds["statusLeft"], bounds["wikkitRight"])
                    self.assertLess(bounds["statusTop"], bounds["wikkitBottom"])
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
        for button in (*FORMATTING, "ullist", "ollist", "plaintext", "cut", "copy", "paste"):
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
