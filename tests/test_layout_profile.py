import unittest
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent.parent


class LayoutProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()

    async def test_saved_content_is_profiled_after_a_quiet_period(self) -> None:
        await self.page.set_content("""
            <div id="poster-content">
                <div id="layout" data-pid="11" data-profile-validation-interval="100">
                    <div id="results" class="section">
                        <div id="graphic" class="graphic"></div>
                        <div class="content" contenteditable="true" data-section="results"></div>
                    </div>
                    <div id="citations" class="section"></div>
                    <div id="conclusions" class="section"></div>
                </div>
            </div>
            <output id="content-save-status"></output>
            <style>
                #results {height: 100px; padding: 0; overflow: auto}
                #results .content {height: 200px; margin: 0}
            </style>
        """)
        await self.page.evaluate("""() => {
            window.requests = [];
            window.feedback = [];
            window.profileResponse = {
                sections: {results: {}, citations: {}, conclusion: {}},
                overflowing_sections: ["results"],
                underflowing_sections: ["citations", "conclusion"],
                oversized_graphics: [{section: "results", id: "graphic"}],
            };
            window.wikkit = {say: message => feedback.push(message)};
            window.fetch = async url => {
                requests.push(url);
                return {
                    ok: true,
                    json: async () => url === "/layout/content"
                        ? {ok: true} : profileResponse,
                };
            };
        }""")
        await self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-content.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-profile.js"))
        await self.page.wait_for_function("feedback.length === 1")
        self.assertEqual(await self.page.evaluate("requests"), ["/magic/11/profile"])
        await self.page.locator(".content").evaluate("""element => {
            element.textContent = "Updated poster text";
            element.dispatchEvent(new Event("input", {bubbles: true}));
        }""")
        await self.page.wait_for_function("requests.includes('/layout/content')")
        await self.page.evaluate("""() => {
            document.dispatchEvent(new Event("poster-layout-updated"));
            document.dispatchEvent(new Event("poster-layout-updated"));
        }""")
        await self.page.wait_for_function("requests.length === 3")
        self.assertEqual(await self.page.evaluate("requests"), [
            "/magic/11/profile", "/layout/content", "/magic/11/profile",
        ])
        self.assertTrue(await self.page.locator("#results").evaluate(
            "element => element.classList.contains('layout-profile-attention')",
        ))
        self.assertTrue(await self.page.locator("#graphic").evaluate(
            "element => element.classList.contains('layout-profile-attention')",
        ))
        self.assertTrue(await self.page.locator("#citations").evaluate(
            "element => element.classList.contains('layout-profile-attention')",
        ))
        self.assertTrue(await self.page.locator("#conclusions").evaluate(
            "element => element.classList.contains('layout-profile-attention')",
        ))
        self.assertEqual(await self.page.locator("#results").evaluate(
            "element => getComputedStyle(element).outlineStyle",
        ), "solid")
        self.assertIn("Results is overflowing", await self.page.evaluate("feedback[0]"))
        self.assertIn("consider resizing it", await self.page.evaluate("feedback[0]"))
        self.assertIn("Literature Cited has room for more content", await self.page.evaluate("feedback[0]"))
        self.assertIn("Command-click on a Mac", await self.page.evaluate("feedback[0]"))

        await self.page.locator("#results").evaluate("""section => {
            section.dispatchEvent(new PointerEvent("pointerdown", {
                bubbles: true, button: 2, clientX: 40, clientY: 50,
            }));
        }""")
        self.assertIn("layout-profile-context-target", await self.page.locator(
            "#results",
        ).get_attribute("class"))
        await self.page.locator("#results").evaluate("""section => {
            section.dispatchEvent(new MouseEvent("contextmenu", {
                bubbles: true, cancelable: true, button: 2, clientX: 40, clientY: 50,
            }));
        }""")
        self.assertEqual(await self.page.locator("#layout-context-menu [role=menuitem]").all_text_contents(), [
            "Auto-fit (not available yet)",
            "Resize graphics (not available yet)",
            "Adjust text content",
        ])
        self.assertEqual(await self.page.locator(
            "#layout-context-menu button:disabled",
        ).count(), 2)
        self.assertIn("wand-cursor.png", await self.page.locator("#results").evaluate(
            "element => getComputedStyle(element).cursor",
        ))
        for target in ("#layout-context-menu", "#layout-context-menu button:enabled",
                       "#layout-context-menu button:disabled"):
            for element in await self.page.locator(target).all():
                await element.hover()
                self.assertIn("wand-cursor.png", await element.evaluate(
                    "element => getComputedStyle(element).cursor",
                ))
        await self.page.get_by_role("menuitem", name="Adjust text content").click()
        self.assertTrue(await self.page.locator(".content").evaluate(
            "element => document.activeElement === element",
        ))
        self.assertEqual(await self.page.locator("#layout-context-menu").count(), 0)
        await self.page.locator("#results").evaluate("""section => {
            section.dispatchEvent(new PointerEvent("pointerdown", {
                bubbles: true, button: 0, metaKey: true, clientX: 40, clientY: 50,
            }));
        }""")
        self.assertEqual(await self.page.locator("#layout-context-menu").count(), 1)
        await self.page.keyboard.press("Escape")
        self.assertEqual(await self.page.locator("#layout-context-menu").count(), 0)

        await self.page.evaluate("""() => {
            document.querySelector("#results .content").style.height = "20px";
            profileResponse = {
                sections: {results: {}, citations: {}, conclusion: {}},
                overflowing_sections: [],
                underflowing_sections: [],
                oversized_graphics: [],
            };
            document.dispatchEvent(new Event("poster-layout-updated"));
        }""")
        await self.page.wait_for_function("requests.length === 4")
        self.assertFalse(await self.page.locator("#results").evaluate(
            "element => element.classList.contains('layout-profile-attention')",
        ))
        self.assertFalse(await self.page.locator("#graphic").evaluate(
            "element => element.classList.contains('layout-profile-attention')",
        ))
        self.assertGreaterEqual(await self.page.evaluate("feedback.length"), 2)

    async def test_outlines_follow_current_section_and_graphic_warnings(self) -> None:
        await self.page.set_content("""
            <div id="poster-content">
                <div id="layout" data-pid="11" data-profile-validation-interval="0">
                    <div id="results" class="section">
                        <div id="old-graphic" class="graphic"></div>
                        <div id="new-graphic" class="graphic"></div>
                    </div>
                    <div id="conclusions" class="section"></div>
                </div>
            </div>
        """)
        await self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        await self.page.evaluate("""() => {
            window.feedback = [];
            window.wikkit = {say: message => feedback.push(message)};
            window.profileResponse = {
                sections: {results: {}, conclusion: {}},
                overflowing_sections: ["results"],
                underflowing_sections: ["conclusion"],
                oversized_graphics: [{section: "results", id: "old-graphic"}],
            };
            window.fetch = async () => ({ok: true, json: async () => profileResponse});
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-profile.js"))
        await self.page.wait_for_function("feedback.length === 1")
        self.assertEqual(await self.page.locator(".layout-profile-attention").evaluate_all(
            "elements => elements.map(element => element.id)",
        ), ["results", "old-graphic", "conclusions"])

        await self.page.evaluate("""() => {
            profileResponse = {
                sections: {results: {}, conclusion: {}},
                overflowing_sections: [],
                underflowing_sections: [],
                oversized_graphics: [{section: "results", id: "new-graphic"}],
            };
            document.dispatchEvent(new Event("poster-layout-updated"));
        }""")
        await self.page.wait_for_function("feedback.length === 2")
        self.assertEqual(await self.page.locator(".layout-profile-attention").evaluate_all(
            "elements => elements.map(element => element.id)",
        ), ["results", "new-graphic"])
        self.assertIn("consider resizing it", await self.page.evaluate("feedback[1]"))
        for target in ("results", "old-graphic", "new-graphic", "conclusions"):
            with self.subTest(target=target):
                outline = await self.page.locator(f"#{target}").evaluate(
                    "element => getComputedStyle(element).outlineStyle",
                )
                self.assertEqual(outline, "solid" if target in ("results", "new-graphic") else "none")

        await self.page.evaluate("""() => {
            profileResponse.oversized_graphics = [];
            document.dispatchEvent(new Event("poster-layout-updated"));
        }""")
        await self.page.wait_for_function(
            "document.querySelectorAll('.layout-profile-attention').length === 0",
        )
        self.assertEqual(await self.page.evaluate("feedback.length"), 2)

    async def test_warning_and_editing_outlines_stay_visible_at_poster_zoom(self) -> None:
        await self.page.set_content("""
            <div id="poster-container" style="width:800px;height:600px;flex:none">
                <div id="poster-stage">
                    <div id="poster-canvas" style="width:4000px;height:3000px;padding:0">
                        <div id="poster-content" data-fixed-geometry="true"
                             style="width:8000px;height:6000px;transform:scale(.5)">
                            <div id="poster-header"></div>
                            <div id="layout" data-pid="11" data-profile-validation-interval="0">
                                <div id="results" class="section" style="width:2000px;height:1500px">
                                    <div id="graphic" class="graphic" style="width:400px;height:400px"></div>
                                    <div id="editor" class="content" contenteditable="true">Edit me</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <input id="poster-zoom" type="range"><output id="poster-zoom-value"></output>
        """)
        await self.page.add_style_tag(path=str(ROOT / "static/css/layout.css"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/wikkit.js"))
        await self.page.evaluate("""() => {
            window.feedback = [];
            window.wikkit = {say: message => feedback.push(message)};
            window.profileResponse = {
                sections: {results: {}},
                overflowing_sections: ["results"],
                underflowing_sections: [],
                oversized_graphics: [{section: "results", id: "graphic"}],
            };
            window.fetch = async () => ({ok: true, json: async () => profileResponse});
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-zoom.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-profile.js"))
        await self.page.wait_for_function("feedback.length === 1")
        for zoom in (20, 50, 100):
            await self.page.locator("#poster-zoom").evaluate("""(slider, value) => {
                slider.value = value;
                slider.dispatchEvent(new Event("input"));
            }""", zoom)
            for target in ("results", "graphic"):
                with self.subTest(zoom=zoom, target=target):
                    thickness = await self.page.locator(f"#{target}").evaluate("""element => {
                        const scale = element.getBoundingClientRect().width / element.offsetWidth;
                        return parseFloat(getComputedStyle(element).outlineWidth) * scale;
                    }""")
                    self.assertAlmostEqual(thickness, 3, delta=0.15)

        await self.page.locator("#editor").click()
        self.assertEqual(await self.page.locator("#results").evaluate(
            "element => getComputedStyle(element).outlineColor",
        ), "rgb(255, 191, 0)")
        self.assertEqual(await self.page.locator("#editor").evaluate(
            "element => getComputedStyle(element).outlineColor",
        ), "rgb(0, 102, 255)")
        await self.page.locator("#editor").evaluate(
            "element => element.classList.add('layout-profile-attention')",
        )
        self.assertEqual(await self.page.locator("#editor").evaluate(
            "element => getComputedStyle(element).outlineColor",
        ), "rgb(0, 102, 255)")
        await self.page.locator("#editor").blur()
        self.assertEqual(await self.page.locator("#editor").evaluate(
            "element => getComputedStyle(element).outlineColor",
        ), "rgb(255, 191, 0)")
        await self.page.locator("#editor").evaluate(
            "element => element.classList.remove('layout-profile-attention')",
        )
        await self.page.locator("#editor").evaluate("""element => {
            element.classList.remove("content");
            element.contentEditable = "plaintext-only";
        }""")
        await self.page.evaluate("""() => {
            profileResponse.overflowing_sections = [];
            profileResponse.oversized_graphics = [];
            document.dispatchEvent(new Event("poster-layout-updated"));
        }""")
        await self.page.wait_for_function("!document.querySelector('.layout-profile-attention')")
        await self.page.locator("#editor").click()
        self.assertEqual(await self.page.locator("#editor").evaluate(
            "element => getComputedStyle(element).outlineColor",
        ), "rgb(0, 102, 255)")
        thickness = await self.page.locator("#editor").evaluate("""element =>
            parseFloat(getComputedStyle(element).outlineWidth)
                * element.getBoundingClientRect().width / element.offsetWidth
        """)
        self.assertAlmostEqual(thickness, 3, delta=0.15)
        await self.page.locator("#editor").blur()
        self.assertEqual(await self.page.locator("#editor").evaluate(
            "element => getComputedStyle(element).outlineStyle",
        ), "none")

    async def test_cut_rechecks_live_overflow_at_configured_interval_before_save(self) -> None:
        await self.page.set_content("""
            <div id="poster-content">
                <div id="layout" data-pid="11" data-profile-validation-interval="40">
                    <div id="introduction" class="section"
                         style="height:100px;padding:0;overflow:auto">
                        <div class="content" contenteditable="true" data-section="introduction"
                             style="height:200px;margin:0">Text to cut</div>
                    </div>
                </div>
            </div>
            <output id="content-save-status"></output>
        """)
        await self.page.evaluate("""() => {
            window.feedback = [];
            window.profileRequests = 0;
            window.wikkit = {say: message => feedback.push(message)};
            window.fetch = async url => {
                if (url === "/layout/content") {
                    return new Promise(resolve => {
                        window.finishSave = () => resolve({ok: true, json: async () => ({ok: true})});
                    });
                }
                profileRequests++;
                return {ok: true, json: async () => ({
                    sections: {introduction: {}},
                    overflowing_sections: ["introduction"],
                    underflowing_sections: [],
                    oversized_graphics: [],
                })};
            };
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-content.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-profile.js"))
        await self.page.wait_for_function("feedback.length === 1")
        await self.page.locator(".content").evaluate("""element => {
            element.textContent = "Short";
            element.style.height = "20px";
            element.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "deleteByCut"}));
        }""")
        await self.page.wait_for_function(
            "!document.getElementById('introduction').classList.contains('layout-profile-attention')",
            timeout=250,
        )
        self.assertEqual(await self.page.evaluate("profileRequests"), 1)
        await self.page.wait_for_function("typeof finishSave === 'function'")
        await self.page.evaluate("finishSave()")
        await self.page.wait_for_function("profileRequests === 2")
        self.assertFalse(await self.page.locator("#introduction").evaluate(
            "element => element.classList.contains('layout-profile-attention')",
        ))

    async def test_initial_profile_waits_for_dom_and_geometry(self) -> None:
        await self.page.route("https://presto.test/layout-profile.js", lambda route: route.fulfill(
            path=str(ROOT / "static/javascript/layout-profile.js"),
            content_type="application/javascript",
        ))
        await self.page.route("https://presto.test/layout/edit", lambda route: route.fulfill(
            content_type="text/html",
            body="""
                <!DOCTYPE html>
                <html><head>
                    <script>
                        window.requests = [];
                        window.feedback = [];
                        window.posterGeometryReady = new Promise(resolve => {
                            window.finishGeometry = resolve;
                        });
                        document.addEventListener("DOMContentLoaded", () => {
                            window.wikkit = {say: message => feedback.push(message)};
                        }, {once: true});
                        window.fetch = async url => {
                            requests.push(url);
                            return {ok: true, json: async () => ({
                                sections: {results: {}},
                                overflowing_sections: ["results"],
                                underflowing_sections: [],
                                oversized_graphics: [],
                            })};
                        };
                    </script>
                    <script src="/layout-profile.js" defer></script>
                </head><body>
                    <div id="layout" data-pid="11" data-profile-validation-interval="0">
                        <div id="results" class="section"></div>
                    </div>
                </body></html>
            """,
        ))
        await self.page.goto("https://presto.test/layout/edit")
        self.assertEqual(await self.page.evaluate("requests"), [])
        await self.page.evaluate("finishGeometry()")
        await self.page.wait_for_function("feedback.length === 1")
        self.assertEqual(await self.page.evaluate("requests"), ["/magic/11/profile"])
        self.assertEqual(await self.page.locator(".layout-profile-attention").count(), 1)

    async def test_profile_failure_is_reported_to_wikkit(self) -> None:
        await self.page.set_content("""
            <div id="layout" data-pid="11" data-profile-validation-interval="0"></div>
        """)
        await self.page.evaluate("""() => {
            window.feedback = [];
            window.wikkit = {say: message => feedback.push(message)};
            window.fetch = async () => ({
                ok: false,
                json: async () => ({detail: "Poster unavailable."}),
            });
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/layout-profile.js"))
        await self.page.wait_for_function("feedback.length === 1")
        self.assertIn("couldn&rsquo;t check the layout", await self.page.evaluate("feedback[0]"))
