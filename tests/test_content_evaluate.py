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
        await self.page.evaluate("""events => {
            window.messages = [];
            window.commands = [];
            window.wikkit = {
                say: message => { messages.push(message); return Promise.resolve(); },
                execute: command => commands.push(command),
            };
            window.fetch = async (url, options) => {
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
                    "image_analysis": {"description": "A scientific image"},
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
        self.assertEqual(await self.page.locator("#evaluation-status").text_content(),
                         "Evaluation complete: one suitable and one not suitable.")
        self.assertFalse(await self.page.locator("#continue-review").is_hidden())
        self.assertEqual(await self.page.evaluate("commands"), [])
        self.assertIn("Processing image one of two.", await self.page.evaluate("messages.join(' ')"))

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


if __name__ == "__main__":
    unittest.main()
