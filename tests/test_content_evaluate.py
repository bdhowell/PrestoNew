import unittest
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parent.parent


class GraphicEvaluationPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        playwright = await async_playwright().start()
        self.addAsyncCleanup(playwright.stop)
        browser = await playwright.chromium.launch()
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()

    async def load_page(self, events: list[dict[str, object]]) -> None:
        await self.page.set_content("""
            <script id="evaluation-poster" type="application/json">{"pid":11}</script>
            <p id="evaluation-status"></p>
            <div id="graphic-evaluations"></div>
            <button id="retry-evaluation" hidden>Try again</button>
            <a id="continue-review" href="/content/review" hidden>Continue</a>
        """)
        await self.page.add_style_tag(path=str(ROOT / "static/css/pages/content-evaluate.css"))
        await self.page.evaluate("""events => {
            window.messages = [];
            window.commands = [];
            window.requestCount = 0;
            window.wikkit = {
                say: message => { messages.push(message); return Promise.resolve(); },
                execute: command => commands.push(command),
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

    async def test_results_are_grouped_and_suitability_uses_either_section(self) -> None:
        def result(sid: int, category: str, filename: str, methods: str, findings: str):
            return {
                "type": "result",
                "source": {"sid": sid, "category": category, "filename": filename},
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
                            "explanation": "Results explanation.",
                            "supported_elements": [],
                            "limitations": [],
                            "suggested_improvements": [],
                        },
                    },
                },
            }

        await self.load_page([
            {"type": "start", "total": 2, "uploaded": 1, "extracted": 1},
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
        self.assertEqual(await self.page.locator("#graphic-evaluations h2").all_text_contents(), [
            "Uploaded graphics", "Extracted graphics",
        ])
        self.assertEqual(await self.page.locator(".graphic-evaluation h3").all_text_contents(), [
            "uploaded.png", "extracted.png",
        ])
        self.assertEqual(await self.page.locator(".evaluation-verdict").all_text_contents(), [
            "Suitable for the poster.", "Not suitable for the poster.",
        ])
        self.assertEqual(await self.page.locator(".graphic-classification").all_text_contents(), [
            "Classification: chart", "Classification: chart",
        ])
        self.assertEqual(await self.page.locator("#evaluation-status").text_content(),
                         "Evaluation complete: one suitable and one not suitable.")
        self.assertFalse(await self.page.locator("#continue-review").is_hidden())
        self.assertEqual(await self.page.evaluate("commands"), [])
        self.assertIn("graphic one of two.", await self.page.evaluate("messages.join(' ')"))
        for width in (1280, 375):
            with self.subTest(width=width):
                await self.page.set_viewport_size({"width": width, "height": 800})
                cards = await self.page.locator(".graphic-evaluation").evaluate_all("""cards =>
                    cards.map(card => [...card.children].map(child => {
                        const {left, top, bottom, width} = child.getBoundingClientRect();
                        return {left, top, bottom, width};
                    }))
                """)
                for children in cards:
                    for previous, current in zip(children, children[1:]):
                        self.assertGreaterEqual(current["top"], previous["bottom"])
                        self.assertAlmostEqual(current["left"], previous["left"], delta=1)
                        self.assertAlmostEqual(current["width"], previous["width"], delta=1)

    async def test_math_expressions_are_rendered_as_latex(self) -> None:
        await self.page.set_content("""
            <script id="evaluation-poster" type="application/json">{"pid":11}</script>
            <p id="evaluation-status"></p><div id="graphic-evaluations"></div>
            <button id="retry-evaluation" hidden></button><a id="continue-review" hidden></a>
        """)
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

    async def test_already_evaluated_graphics_redirect_to_layout(self) -> None:
        await self.load_page([
            {"type": "start", "total": 2, "uploaded": 1, "extracted": 1},
            {"type": "already-evaluated"},
        ])
        self.assertEqual(await self.page.evaluate("commands"), [
            {"does": "redirect", "payload": "/layout/edit"},
        ])
        self.assertTrue(await self.page.locator("#continue-review").is_hidden())
        self.assertTrue(await self.page.locator("#retry-evaluation").is_hidden())
        self.assertEqual(await self.page.evaluate("messages"), [])

    async def test_no_graphics_message_redirects_to_conference_query(self) -> None:
        await self.load_page([{"type": "start", "total": 0, "uploaded": 0, "extracted": 0},
                              {"type": "empty"}])
        self.assertIn("I didn&rsquo;t find any graphics", await self.page.evaluate("messages.join(' ')"))
        self.assertEqual(await self.page.evaluate("commands"), [
            {"does": "redirect", "payload": "/conferences/query"},
        ])

    async def test_interrupted_stream_automatically_resumes(self) -> None:
        await self.load_page([{"type": "start", "total": 1, "uploaded": 1, "extracted": 0}])
        await self.page.evaluate("""() => {
            window.fetch = async () => new Response(
                JSON.stringify({type: 'already-evaluated'}) + '\\n',
            );
        }""")
        await self.page.wait_for_function("commands.length === 1")
        self.assertEqual(await self.page.evaluate("commands"), [
            {"does": "redirect", "payload": "/layout/edit"},
        ])
        self.assertEqual(await self.page.evaluate("messages"), [])
        self.assertTrue(await self.page.locator("#retry-evaluation").is_hidden())

    async def test_repeated_interruptions_offer_friendly_manual_retry(self) -> None:
        await self.load_page([{"type": "start", "total": 1, "uploaded": 1, "extracted": 0}])
        await self.page.locator("#retry-evaluation").wait_for(state="visible")
        self.assertEqual(await self.page.evaluate("requestCount"), 3)
        message = await self.page.locator("#evaluation-status").text_content()
        self.assertIn("Please try again; completed evaluations have been saved.", message)
        self.assertNotIn("stream", message)
        self.assertTrue(await self.page.locator("#continue-review").is_hidden())
        self.assertEqual(await self.page.evaluate("commands"), [])
        await self.page.evaluate("""() => {
            window.fetch = async () => new Response(
                JSON.stringify({type: 'already-evaluated'}) + '\\n',
            );
        }""")
        await self.page.locator("#retry-evaluation").click()
        await self.page.wait_for_function("commands.length === 1")
        self.assertTrue(await self.page.locator("#retry-evaluation").is_hidden())


if __name__ == "__main__":
    unittest.main()
