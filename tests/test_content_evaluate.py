import unittest
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent.parent

EVALUATION_MARKUP = """
    <main>
    <h1>Evaluating your graphics</h1>
    <p class="centered-text"><img width="400" height="300" alt="Wikkit measuring a graphic"></p>
    <script id="evaluation-poster" type="application/json">{"pid":11}</script>
    <p id="evaluation-status"></p>
    <div id="evaluation-progress" hidden>
        <span id="evaluation-progress-count"></span><progress id="evaluation-progress-meter"></progress>
        <span id="evaluation-remaining"></span>
    </div>
    <article id="graphic-evaluations" class="graphic-evaluation" tabindex="0" aria-label="Latest graphic evaluation" hidden>
        <div id="evaluation-summary" class="evaluation-metrics" aria-label="Graphic evaluation summary"></div>
        <div id="evaluation-panes">
            <div id="evaluation-preview"></div>
            <div id="evaluation-divider" role="separator" tabindex="0" aria-valuenow="50"></div>
            <div id="evaluation-details"></div>
        </div>
    </article>
    <button id="retry-evaluation" hidden>Try again</button>
    <p class="evaluation-actions"><button id="continue-review" data-sends="ok" hidden>Continue to Poster</button></p>
    </main>
"""


class GraphicEvaluationPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()

    async def load_page(self, events: list[dict[str, object]]) -> None:
        await self.page.set_content(EVALUATION_MARKUP)
        await self.page.evaluate("""() => {
            document.body.classList.add('wikkit-page');
            document.body.dataset.page = 'content-evaluate';
        }""")
        await self.page.add_style_tag(path=str(ROOT / "static/css/main.css"))
        await self.page.add_style_tag(path=str(ROOT / "static/css/router.css"))
        await self.page.add_style_tag(path=str(ROOT / "static/css/pages/content-evaluate.css"))
        await self.page.evaluate("""events => {
            window.messages = [];
            window.commands = [];
            window.requestCount = 0;
            window.wikkit = {
                say: message => { messages.push(message); return Promise.resolve(); },
                execute: command => commands.push(command),
                requestResponse: () => new Promise(resolve => { window.answerWikkit = resolve; }),
            };
            window.fetch = async (url, options) => {
                window.requestCount++;
                window.request = {url, method: options.method, body: options.body};
                const data = events.map(event => JSON.stringify(event)).join("\\n") + "\\n";
                return new Response(data, {
                    status: 200,
                    headers: {"Content-Type": "application/x-ndjson"},
                });
            };
        }""", events)
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/common.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/pages/content-evaluate.js"))

    async def test_latest_result_and_suitability_uses_either_section(self) -> None:
        def result(sid: int, category: str, filename: str, methods: str, findings: str):
            return {
                "type": "result",
                "source": {"sid": sid, "category": category, "filename": filename},
                "complexity": {"score": 0.125, "threshold": 0.5, "too_complex": False},
                "legibility": {"score": 0.875, "threshold": 0.5, "below_threshold": False},
                "evaluation": {
                    "image_analysis": {
                        "classification": "chart",
                        "math_expressions": [],
                        "description": "A scientific image",
                    },
                    "evaluations": {
                        "materials_and_methods": {
                            "rating": methods,
                            "explanation": "Methods explanation.",
                            "supported_elements": [],
                            "limitations": [],
                            "suggested_improvements": [],
                        },
                        "results": {
                            "rating": findings,
                            "explanation": "Results explanation. " * 120 + "END OF DETAILS",
                            "supported_elements": [],
                            "limitations": [],
                            "suggested_improvements": [],
                        },
                    },
                },
            }

        await self.load_page([
            {"type": "start", "total": 2, "uploaded": 1, "extracted": 1,
             "estimate_word": "zero point seven"},
            {"type": "progress", "current": 1, "total": 2, "current_word": "one", "total_word": "two"},
            result(12, "extracted", "extracted.png", "weakly", "weakly"),
            {"type": "progress", "current": 2, "total": 2, "current_word": "two", "total_word": "two"},
            result(11, "uploaded", "uploaded.png", "weakly", "significantly"),
            {"type": "complete"},
        ])
        await self.page.locator("#continue-review").wait_for(state="visible")

        self.assertEqual(await self.page.evaluate("request"), {
            "url": "/content/evaluate/11", "method": "POST", "body": "{}",
        })
        self.assertEqual(await self.page.locator("#graphic-evaluations h2").count(), 0)
        self.assertEqual(await self.page.locator("#evaluation-preview img").get_attribute("src"),
                         "/content/11/image")
        self.assertEqual(await self.page.locator(".evaluation-metrics").text_content(),
                         "Classification: chartComplexity: 12.5%Legibility: 87.5%Suitable for the poster.")
        self.assertEqual(await self.page.locator(".evaluation-verdict").all_text_contents(), [
            "Suitable for the poster.",
        ])
        self.assertEqual(await self.page.locator(".graphic-classification").all_text_contents(), [
            "Classification: chart",
        ])
        self.assertEqual(await self.page.locator("#evaluation-status").text_content(),
                         "Graphic review complete: one suitable and one not suitable.")
        self.assertFalse(await self.page.locator("#continue-review").is_hidden())
        self.assertEqual(await self.page.evaluate("commands"), [])
        self.assertIn("graphic one of two.", await self.page.evaluate("messages.join(' ')"))
        self.assertIn("reviewing two graphics", await self.page.evaluate("messages.join(' ')"))
        self.assertIn("predicting zero point seven minutes", await self.page.evaluate("messages.join(' ')"))
        for width, height, chat_open in ((1280, 800, False), (375, 800, False), (375, 800, True),
                                         (375, 667, True)):
            with self.subTest(width=width, height=height, chat_open=chat_open):
                await self.page.set_viewport_size({"width": width, "height": height})
                await self.page.evaluate("""open => {
                    document.body.style.setProperty('--chat-window-transition', '0ms');
                    document.querySelector('#chatbot')?.remove();
                    if (open) {
                        const chatbot = document.createElement('div');
                        chatbot.id = 'chatbot';
                        document.body.append(chatbot);
                    }
                }""", chat_open)
                metrics = await self.page.locator("#evaluation-summary").evaluate("""summary => {
                    const items = [...summary.children];
                    return {
                        tops: items.map(item => item.getBoundingClientRect().top),
                        widths: items.map(item => [item.clientWidth, item.scrollWidth]),
                        background: getComputedStyle(summary).backgroundColor,
                        overflow: getComputedStyle(summary).overflowX,
                    };
                }""")
                self.assertEqual(len(metrics["tops"]), 4)
                self.assertTrue(all(top == metrics["tops"][0] for top in metrics["tops"]))
                self.assertTrue(all(client == scroll for client, scroll in metrics["widths"]))
                self.assertEqual(metrics["background"], "rgb(228, 224, 246)")
                self.assertEqual(metrics["overflow"], "visible")
                if width == 375:
                    self.assertGreater(await self.page.locator("#graphic-evaluations").evaluate("""panel => {
                        panel.scrollLeft = panel.scrollWidth;
                        return panel.scrollLeft;
                    }"""), 0)
                self.assertGreater(await self.page.locator("#graphic-evaluations").evaluate("""panel => {
                    panel.scrollTop = panel.scrollHeight;
                    return panel.scrollTop;
                }"""), 0)
                self.assertTrue(await self.page.locator("#graphic-evaluations").evaluate("""panel => {
                    const last = panel.querySelector('#evaluation-details section:last-child p');
                    return last.textContent.endsWith('END OF DETAILS')
                        && last.getBoundingClientRect().bottom <= panel.getBoundingClientRect().bottom;
                }"""))
                self.assertEqual(await self.page.locator("#evaluation-preview").evaluate(
                    "node => getComputedStyle(node).overflowY"), "visible")
                self.assertEqual(await self.page.locator("#evaluation-details").evaluate(
                    "node => getComputedStyle(node).overflowY"), "visible")
                self.assertEqual(await self.page.evaluate("""() => {
                    const root = document.scrollingElement;
                    return [root.scrollWidth - root.clientWidth, root.scrollHeight - root.clientHeight];
                }"""), [0, 0])
                action = await self.page.locator("#continue-review").evaluate("""button => {
                    const box = button.getBoundingClientRect();
                    const main = button.closest('main').getBoundingClientRect();
                    return {top: box.top, bottom: box.bottom, right: box.right,
                        mainBottom: main.bottom, mainRight: main.right};
                }""")
                self.assertGreaterEqual(action["top"], 0, action)
                self.assertLessEqual(action["bottom"], action["mainBottom"], action)
                self.assertLessEqual(action["right"], action["mainRight"], action)
                self.assertGreater(await self.page.locator("#graphic-evaluations").evaluate(
                    "panel => panel.clientHeight"), 50)
                self.assertEqual(await self.page.locator("main").evaluate(
                    "main => [main.scrollWidth - main.clientWidth, main.scrollHeight - main.clientHeight]"), [0, 0])

    async def test_live_progress_and_resizable_latest_result(self) -> None:
        await self.page.set_content(EVALUATION_MARKUP)
        await self.page.evaluate("""() => {
            document.body.classList.add('wikkit-page');
            document.body.dataset.page = 'content-evaluate';
        }""")
        await self.page.add_style_tag(path=str(ROOT / "static/css/main.css"))
        await self.page.add_style_tag(path=str(ROOT / "static/css/router.css"))
        await self.page.add_style_tag(path=str(ROOT / "static/css/pages/content-evaluate.css"))
        await self.page.clock.install()
        await self.page.evaluate("""() => {
            window.wikkit = {say: () => Promise.resolve(), requestResponse: () => new Promise(() => {})};
            const encoder = new TextEncoder();
            const stream = new ReadableStream({start(controller) {
                window.emit = event => controller.enqueue(encoder.encode(JSON.stringify(event) + "\\n"));
                window.finish = () => controller.close();
            }});
            window.fetch = async () => new Response(stream);
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/common.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/pages/content-evaluate.js"))

        def result(sid: int) -> dict[str, object]:
            section = {
                "rating": "highly", "explanation": f"Details for {sid}.", "supported_elements": [],
                "limitations": [], "suggested_improvements": [],
            }
            return {
                "type": "result", "source": {"sid": sid, "category": "uploaded", "filename": f"{sid}.png"},
                "complexity": {"score": 0.1, "threshold": 0.5, "too_complex": False},
                "legibility": {"score": 0.9, "threshold": 0.5, "below_threshold": False},
                "evaluation": {
                    "image_analysis": {"classification": "chart", "description": f"Image {sid}"},
                    "evaluations": {"materials_and_methods": section, "results": section},
                },
            }

        async def emit(event: dict[str, object]) -> None:
            await self.page.evaluate("event => window.emit(event)", event)

        await emit({"type": "start", "total": 2, "estimate_word": "zero point seven"})
        await emit(result(99))
        await emit({"type": "progress", "current": 1, "total": 2, "current_word": "one", "total_word": "two"})
        await self.page.wait_for_function("document.querySelector('#evaluation-preview img')?.getAttribute('src') === '/content/99/image'")
        await self.page.wait_for_function("document.querySelector('#evaluation-status').textContent.includes('image 1')")
        self.assertEqual(await self.page.locator("#evaluation-progress-count").text_content(), "0 of 2")
        self.assertEqual(await self.page.locator("#evaluation-remaining").text_content(),
                         "(40 seconds estimated remaining)")
        await self.page.clock.run_for(3000)
        self.assertEqual(await self.page.locator("#evaluation-remaining").text_content(),
                         "(37 seconds estimated remaining)")
        await emit(result(1))
        await self.page.wait_for_function("document.querySelector('#evaluation-progress-count').textContent === '1 of 2'")
        estimate = int((await self.page.locator("#evaluation-remaining").text_content()).split()[0][1:])
        self.assertIn(estimate, (3, 4))
        self.assertEqual(await self.page.locator("#evaluation-progress-meter").evaluate("meter => meter.value"), 1)
        self.assertEqual(await self.page.locator("#evaluation-preview img").get_attribute("src"), "/content/1/image")
        self.assertIn("Details for 1.", await self.page.locator("#evaluation-details").inner_text())

        divider = self.page.locator("#evaluation-divider")
        await divider.focus()
        await self.page.keyboard.press("Shift+ArrowDown")
        self.assertEqual(await divider.get_attribute("aria-valuenow"), "60")
        await divider.scroll_into_view_if_needed()
        box = await divider.bounding_box()
        await self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await self.page.mouse.down()
        await self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] - 50)
        await self.page.mouse.up()
        resized = await divider.get_attribute("aria-valuenow")
        self.assertLess(int(resized), 60)

        await self.page.clock.run_for(1000)
        self.assertEqual(await self.page.locator("#evaluation-remaining").text_content(),
                         f"({estimate - 1} seconds estimated remaining)")
        await emit({"type": "progress", "current": 2, "total": 2, "current_word": "two", "total_word": "two"})
        await self.page.wait_for_function("document.querySelector('#evaluation-status').textContent.includes('image 2')")
        await self.page.clock.run_for(2000)
        await emit(result(2))
        await self.page.wait_for_function("document.querySelector('#evaluation-progress-count').textContent === '2 of 2'")
        self.assertEqual(await self.page.locator("#evaluation-remaining").text_content(),
                         "(0 seconds estimated remaining)")
        self.assertEqual(await self.page.locator("#evaluation-preview img").get_attribute("src"), "/content/2/image")
        self.assertEqual(await self.page.locator("#evaluation-preview img").count(), 1)
        self.assertIn("Details for 2.", await self.page.locator("#evaluation-details").inner_text())
        self.assertNotIn("Details for 1.", await self.page.locator("#evaluation-details").inner_text())
        self.assertEqual(await divider.get_attribute("aria-valuenow"), resized)
        await emit({"type": "complete"})
        await self.page.evaluate("window.finish()")
        await self.page.locator("#continue-review").wait_for(state="visible")

    async def test_complexity_overrides_relevance_and_explains_verdict(self) -> None:
        section = {
            "rating": "highly", "explanation": "", "supported_elements": [],
            "limitations": [], "suggested_improvements": [],
        }
        await self.load_page([
            {"type": "start", "total": 1, "uploaded": 1, "extracted": 0,
             "estimate_word": "zero point three"},
            {
                "type": "result",
                "source": {"sid": 1, "category": "uploaded", "filename": "detailed.png"},
                "complexity": {"score": 0.2, "threshold": 0.1, "too_complex": True},
                "evaluation": {
                    "image_analysis": {
                        "classification": "image", "math_expressions": [], "description": "Detailed image",
                    },
                    "evaluations": {"materials_and_methods": section, "results": section},
                },
            },
            {"type": "complete"},
        ])
        await self.page.locator("#continue-review").wait_for(state="visible")
        self.assertEqual(await self.page.locator(".evaluation-verdict").text_content(),
                         "Not suitable for the poster.")
        self.assertIn(
            "Complexity score (20.0%) exceeds the poster threshold (10.0%)",
            await self.page.locator(".graphic-evaluation").inner_text(),
        )
        self.assertEqual(await self.page.locator("#evaluation-status").text_content(),
                         "Graphic review complete: zero suitable and one not suitable.")

    async def test_legibility_below_threshold_overrides_relevance_and_explains_verdict(self) -> None:
        section = {
            "rating": "highly", "explanation": "", "supported_elements": [],
            "limitations": [], "suggested_improvements": [],
        }
        await self.load_page([
            {"type": "start", "total": 1, "uploaded": 1, "extracted": 0,
             "estimate_word": "zero point three"},
            {
                "type": "result",
                "source": {"sid": 1, "category": "uploaded", "filename": "illegible.png"},
                "complexity": {"score": 0.1, "threshold": 0.5, "too_complex": False},
                "legibility": {"score": 0.8, "threshold": 0.95, "below_threshold": True},
                "evaluation": {
                    "image_analysis": {
                        "classification": "image", "math_expressions": [], "description": "Detailed image",
                    },
                    "evaluations": {"materials_and_methods": section, "results": section},
                },
            },
            {"type": "complete"},
        ])
        await self.page.locator("#continue-review").wait_for(state="visible")
        self.assertEqual(await self.page.locator(".evaluation-verdict").text_content(),
                         "Not suitable for the poster.")
        self.assertIn(
            "Legibility score (80.0%) is below the poster threshold (95.0%)",
            await self.page.locator(".graphic-evaluation").inner_text(),
        )
        self.assertEqual(await self.page.locator("#evaluation-status").text_content(),
                         "Graphic review complete: zero suitable and one not suitable.")

    async def test_threshold_failure_is_reported_without_evaluation_details(self) -> None:
        await self.load_page([
            {"type": "start", "total": 0, "uploaded": 0, "extracted": 0,
             "estimate_word": "zero"},
            {
                "type": "result",
                "source": {"sid": 1, "category": "uploaded", "filename": "too-complex.png"},
                "complexity": {"score": 0.2, "threshold": 0.1, "too_complex": True},
                "legibility": {"score": 1.0, "threshold": 0.95, "below_threshold": False},
                "evaluation": None,
            },
            {"type": "complete"},
        ])
        card = self.page.locator(".graphic-evaluation")
        self.assertEqual(await card.locator(".evaluation-verdict").text_content(),
                         "Not suitable for the poster.")
        self.assertEqual(await card.locator(".graphic-classification").text_content(),
                         "Classification: not evaluated")
        self.assertIn("Not evaluated because this image did not meet", await card.inner_text())
        self.assertEqual(await card.locator("section").count(), 0)
        self.assertEqual(await card.locator("img").get_attribute("alt"), "too-complex.png")
        self.assertNotIn("predicting", await self.page.evaluate("messages.join(' ')"))

    async def test_math_expressions_are_rendered_as_latex(self) -> None:
        await self.page.set_content(EVALUATION_MARKUP)
        await self.page.evaluate("""() => {
            window.mathJaxSource = {typeset() {}};
            window.wikkit = {say: () => Promise.resolve(), execute: () => {}};
            const evaluation = {
                image_analysis: {
                    classification: "math expression",
                    math_expressions: ["E=mc^2", "\\\\frac{1}{2}"],
                    description: "Two equations.",
                },
                evaluations: {
                    materials_and_methods: {
                        rating: "weakly", explanation: "", supported_elements: [],
                        limitations: [], suggested_improvements: [],
                    },
                    results: {
                        rating: "weakly", explanation: "", supported_elements: [],
                        limitations: [], suggested_improvements: [],
                    },
                },
            };
            const events = [
                {type: "result", source: {sid: 1, category: "uploaded", filename: "equations.png"}, evaluation},
                {type: "complete"},
            ];
            window.fetch = async () => new Response(
                events.map(event => JSON.stringify(event)).join("\\n") + "\\n",
                {status: 200, headers: {"Content-Type": "application/x-ndjson"}},
            );
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/common.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/pages/content-evaluate.js"))
        await self.page.wait_for_function(
            "() => document.querySelectorAll('.graphic-math').length === 2",
        )
        self.assertEqual(await self.page.locator(".graphic-math").all_text_contents(), [
            r"\[E=mc^2\]", r"\[\frac{1}{2}\]",
        ])

    async def test_tables_are_rendered_with_the_parent_font(self) -> None:
        await self.load_page([
            {
                "type": "result",
                "source": {
                    "sid": 1, "category": "uploaded", "filename": "data.png",
                    "type": "tables",
                    "table_html": "<table><tr><th>Group</th><th>Count</th></tr>"
                                  "<tr><td>Control</td><td>12</td></tr></table>",
                },
                "evaluation": {
                    "image_analysis": {
                        "classification": "table", "math_expressions": [],
                        "description": "A data table.",
                    },
                    "evaluations": {
                        "materials_and_methods": {
                            "rating": "weakly", "explanation": "", "supported_elements": [],
                            "limitations": [], "suggested_improvements": [],
                        },
                        "results": {
                            "rating": "highly", "explanation": "", "supported_elements": [],
                            "limitations": [], "suggested_improvements": [],
                        },
                    },
                },
            },
            {"type": "complete"},
        ])
        self.assertEqual(await self.page.locator(".graphic-table th").all_text_contents(),
                         ["Group", "Count"])
        self.assertEqual(await self.page.locator(".graphic-table td").all_text_contents(),
                         ["Control", "12"])
        self.assertEqual(await self.page.locator(".graphic-evaluation img").count(), 0)
        self.assertEqual(await self.page.locator(".graphic-table table").evaluate("""table =>
            getComputedStyle(table).font
        """), await self.page.locator(".graphic-evaluation").evaluate("""parent =>
            getComputedStyle(parent).font
        """))

    async def test_already_evaluated_graphics_offer_review(self) -> None:
        await self.load_page([
            {"type": "start", "total": 0, "uploaded": 0, "extracted": 0},
            {"type": "already-evaluated"},
        ])
        await self.page.locator("#continue-review").wait_for(state="visible")
        self.assertEqual(await self.page.evaluate("commands"), [])
        self.assertTrue(await self.page.locator("#retry-evaluation").is_hidden())
        self.assertIn('data-sends="ok">Continue to Poster</button>', await self.page.evaluate("messages.join(' ')"))

    async def test_no_graphics_message_redirects_to_conference_query(self) -> None:
        await self.load_page([{"type": "start", "total": 0, "uploaded": 0, "extracted": 0},
                              {"type": "empty"}])
        self.assertIn("I didn&rsquo;t find any graphics", await self.page.evaluate("messages.join(' ')"))
        self.assertEqual(await self.page.evaluate("commands"), [
            {"does": "redirect", "payload": "/conferences/query"},
        ])

    async def test_interrupted_stream_automatically_resumes(self) -> None:
        await self.load_page([{"type": "start", "total": 0, "uploaded": 0, "extracted": 0}])
        await self.page.evaluate("""() => {
            window.fetch = async () => new Response(
                JSON.stringify({type: 'already-evaluated'}) + '\\n',
            );
        }""")
        await self.page.locator("#continue-review").wait_for(state="visible")
        self.assertEqual(await self.page.evaluate("commands"), [])
        self.assertTrue(await self.page.locator("#retry-evaluation").is_hidden())

    async def test_repeated_interruptions_offer_friendly_manual_retry(self) -> None:
        await self.load_page([{"type": "start", "total": 1, "uploaded": 1, "extracted": 0,
                               "estimate_word": "zero point three"}])
        await self.page.locator("#retry-evaluation").wait_for(state="visible")
        self.assertEqual(await self.page.evaluate("requestCount"), 3)
        message = await self.page.locator("#evaluation-status").text_content()
        self.assertIn("Please try again; completed evaluations have been saved.", message)
        self.assertNotIn("stream", message)
        self.assertEqual(await self.page.locator("#evaluation-remaining").text_content(), "(estimate paused)")
        self.assertTrue(await self.page.locator("#continue-review").is_hidden())
        self.assertEqual(await self.page.evaluate("commands"), [])
        await self.page.evaluate("""() => {
            window.fetch = async () => new Response(
                JSON.stringify({type: 'already-evaluated'}) + '\\n',
            );
        }""")
        await self.page.locator("#retry-evaluation").click()
        await self.page.locator("#continue-review").wait_for(state="visible")
        self.assertTrue(await self.page.locator("#retry-evaluation").is_hidden())

    async def test_completion_waits_for_wikkit_response_before_review(self) -> None:
        await self.load_page([{"type": "complete"}])
        await self.page.wait_for_function("typeof window.answerWikkit === 'function'")
        self.assertTrue(await self.page.locator("#retry-evaluation").is_hidden())
        self.assertTrue(await self.page.locator("#continue-review").is_visible())
        self.assertEqual(await self.page.evaluate("commands"), [])
        self.assertIn('<button data-sends="ok">Continue to Poster</button>',
                      await self.page.evaluate("messages.join(' ')"))
        await self.page.evaluate("window.answerWikkit('ok')")
        await self.page.wait_for_function("commands.length === 1")
        self.assertEqual(await self.page.evaluate("commands"), [
            {"does": "redirect", "payload": "/content/review"},
        ])

    async def test_wikkit_continues_to_review_with_template_origin(self) -> None:
        await self.page.route("https://evaluate.test/**", lambda route: route.fulfill(body=""))
        await self.page.goto("https://evaluate.test/content/evaluate?from=template")
        await self.load_page([{"type": "already-evaluated"}])
        await self.page.locator("#continue-review").wait_for(state="visible")
        await self.page.evaluate("window.answerWikkit('ok')")
        await self.page.wait_for_function("commands.length === 1")
        self.assertEqual(await self.page.evaluate("commands"), [
            {"does": "redirect", "payload": "/content/review?from=template"},
        ])

    async def test_page_button_continues_to_review(self) -> None:
        await self.page.route("https://evaluate.test/**", lambda route: route.fulfill(body="Review"))
        await self.page.goto("https://evaluate.test/")
        await self.load_page([{"type": "complete"}])
        await self.page.locator("#continue-review").click()
        await self.page.wait_for_url("https://evaluate.test/content/review")

    async def test_processing_error_shows_retry_but_not_continue(self) -> None:
        await self.load_page([{"type": "error", "detail": "A graphic could not be evaluated."}])
        await self.page.locator("#retry-evaluation").wait_for(state="visible")
        self.assertTrue(await self.page.locator("#continue-review").is_hidden())
        self.assertEqual(await self.page.evaluate("commands"), [])

    async def test_real_wikkit_button_waits_for_success_and_opens_review(self) -> None:
        await self.page.route("https://evaluate.test/**", lambda route: route.fulfill(body="Review"))
        await self.page.goto("https://evaluate.test/")
        await self.page.set_content(EVALUATION_MARKUP)
        await self.page.evaluate("""() => {
            document.body.className = "wikkit-page";
            document.body.dataset.wikkitOffline = "true";
            document.body.dataset.page = "content-evaluate";
        }""")
        for css in ("main.css", "router.css", "pages/content-evaluate.css"):
            await self.page.add_style_tag(path=str(ROOT / "static/css" / css))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/wikkit.js"))
        await self.page.evaluate("""async () => {
            window.wikkit = connect(() => {});
            await window.wikkit.execute({does: "configure", payload: {
                CHAT_WORD_INTERVAL: 0, CHAT_WORD_DEVIATION: 0,
            }});
            window.fetch = () => new Promise(resolve => {
                window.finishEvaluation = () => resolve(new Response('{"type":"complete"}\\n'));
            });
        }""")
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/common.js"))
        await self.page.add_script_tag(path=str(ROOT / "static/javascript/pages/content-evaluate.js"))
        self.assertTrue(await self.page.locator("#retry-evaluation").is_hidden())
        self.assertTrue(await self.page.locator("#continue-review").is_hidden())
        self.assertEqual(await self.page.locator('#chatlog button[data-sends="ok"]').count(), 0)
        await self.page.evaluate("window.finishEvaluation()")
        await self.page.locator("#continue-review").wait_for(state="visible")
        button = self.page.locator('#chatlog button[data-sends="ok"]')
        await button.wait_for(state="visible")
        self.assertEqual(await button.text_content(), "Continue to Poster")
        self.assertEqual(self.page.url, "https://evaluate.test/")
        await button.click()
        await self.page.wait_for_url("https://evaluate.test/content/review")


if __name__ == "__main__":
    unittest.main()
