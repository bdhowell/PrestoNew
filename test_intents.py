import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import intents
import llm


class IntentStackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.stack = intents.IntentStack()

    def test_general_maps_cover_all_intents(self) -> None:
        self.assertEqual(set(intents.GENERAL_EXAMPLES), set(intents.GENERAL_HANDLERS))
        self.assertEqual(len(intents.GENERAL_EXAMPLES), 14)

    def test_general_maps_cannot_be_popped(self) -> None:
        with self.assertRaisesRegex(IndexError, "general"):
            self.stack.pop()
        self.assertEqual(len(self.stack.examples_stack), 1)
        self.assertEqual(len(self.stack.handlers_stack), 1)

    def test_latest_examples_replace_rather_than_extend(self) -> None:
        self.stack.push({}, {"help": ["page assistance"], "custom": ["custom"]})
        self.stack.push({}, {"help": ["temporary assistance"]})
        examples = self.stack.accumulated_examples()
        self.assertEqual(examples["help"], ("temporary assistance",))
        self.assertEqual(examples["custom"], ("custom",))
        self.assertEqual(examples["undo"], ("undo",))
        self.stack.pop()
        self.assertEqual(
            self.stack.accumulated_examples()["help"], ("page assistance",)
        )
        self.stack.pop()
        self.assertNotIn("custom", self.stack.accumulated_examples())
        self.assertEqual(
            self.stack.accumulated_examples()["help"], intents.GENERAL_EXAMPLES["help"]
        )

    def test_pop_removes_only_paired_examples(self) -> None:
        self.stack.push({"custom": None}, {"custom": ["custom"]})
        self.stack.push({"help": None})
        examples, handlers = self.stack.pop()
        self.assertIsNone(examples)
        self.assertEqual(handlers, {"help": None})
        self.assertIn("custom", self.stack.accumulated_examples())
        examples, handlers = self.stack.pop()
        self.assertEqual(examples, {"custom": ("custom",)})
        self.assertEqual(handlers, {"custom": None})
        self.assertNotIn("custom", self.stack.accumulated_examples())
        with self.assertRaises(ValueError):
            self.stack.push({"custom": None})

    def test_invalid_push_is_atomic(self) -> None:
        for examples in (None, {"new": ["new"]}):
            with self.subTest(examples=examples):
                with self.assertRaisesRegex(ValueError, "unknown"):
                    self.stack.push({"unknown": None}, examples)
                self.assertEqual(len(self.stack.examples_stack), 1)
                self.assertEqual(len(self.stack.handlers_stack), 1)
        with self.assertRaisesRegex(TypeError, "callable"):
            self.stack.push({"help": 42})  # type: ignore[dict-item]

    def test_maps_are_snapshots_and_conversations_are_isolated(self) -> None:
        phrases = ["custom"]
        examples = {"custom": phrases}
        handlers: dict[str, intents.Handler | None] = {"custom": None}
        self.stack.push(handlers, examples)
        phrases.append("changed")
        examples.clear()
        handlers.clear()
        self.assertEqual(self.stack.accumulated_examples()["custom"], ("custom",))
        self.assertIn("custom", self.stack.handlers_stack[-1])
        self.assertNotIn("custom", intents.IntentStack().accumulated_examples())

    def test_invalid_examples_are_rejected(self) -> None:
        for examples in ({"": ["x"]}, {"x": []}, {"x": "phrase"}, {"x": [""]}):
            with self.subTest(examples=examples):
                with self.assertRaises(ValueError):
                    self.stack.push({}, examples)

    async def test_handler_lookup_falls_through_and_passes_arguments(self) -> None:
        handler = Mock(return_value={"done": True})
        self.stack.push({"help": handler})
        self.stack.push({"undo": None})
        classification = intents.IntentClassification("help", ["Ada"], "help Ada")
        result = await self.stack.handle_intent(classification)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.intent, "help")
        self.assertEqual(result.entities, ["Ada"])
        self.assertEqual(result.handler_result, {"done": True})
        handler.assert_called_once_with(["Ada"], "help Ada")

    async def test_none_handler_blocks_lower_handler(self) -> None:
        handler = Mock()
        self.stack.push({"help": handler})
        self.stack.push({"help": None})
        self.assertIsNone(
            await self.stack.handle_intent(intents.IntentClassification("help", [], "help"))
        )
        handler.assert_not_called()

    async def test_callable_returning_none_still_returns_dispatch_result(self) -> None:
        self.stack.push({"help": lambda entities, message: None})
        result = await self.stack.handle_intent(
            intents.IntentClassification("help", [], "help")
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNone(result.handler_result)

    async def test_async_handler_is_awaited(self) -> None:
        async def handler(entities: list[str], message: str) -> str:
            return f"{entities[0]}: {message}"

        self.stack.push({"help": handler})
        result = await self.stack.handle_intent(
            intents.IntentClassification("help", ["Ada"], "help Ada")
        )
        assert result is not None
        self.assertEqual(result.handler_result, "Ada: help Ada")

    async def test_missing_handler_raises(self) -> None:
        self.stack.push({}, {"custom": ["custom"]})
        with self.assertRaisesRegex(intents.MissingIntentHandlerError, "custom"):
            await self.stack.handle_intent(
                intents.IntentClassification("custom", [], "custom")
            )

    async def test_handler_errors_propagate(self) -> None:
        self.stack.push({"help": Mock(side_effect=RuntimeError("handler failed"))})
        with self.assertRaisesRegex(RuntimeError, "handler failed"):
            await self.stack.handle_intent(intents.IntentClassification("help", [], "help"))

    @patch("intents.interact_with_openai", new_callable=AsyncMock)
    async def test_autopop_uses_examples_and_handler_before_removing_pair(
        self, classify: Mock
    ) -> None:
        classify.return_value = '{"intent": "custom", "entities": ["Ada"]}'
        handler = Mock(return_value="done")
        self.stack.push(
            {"custom": handler}, {"custom": ["temporary example"]}, autopop=True
        )
        result = await self.stack.dispatch("custom Ada")
        assert result is not None
        self.assertEqual(result.handler_result, "done")
        handler.assert_called_once_with(["Ada"], "custom Ada")
        self.assertIn("- custom: temporary example", classify.call_args.args[0])
        self.assertEqual(len(self.stack.handlers_stack), 1)
        self.assertNotIn("custom", self.stack.accumulated_examples())

    async def test_autopop_handles_none_and_fallthrough(self) -> None:
        for handlers in ({"help": None}, {}):
            with self.subTest(handlers=handlers):
                self.stack.push(handlers, autopop=True)
                result = await self.stack.handle_intent(
                    intents.IntentClassification("help", [], "help")
                )
                self.assertEqual(result is None, "help" in handlers)
                self.assertEqual(len(self.stack.handlers_stack), 1)
                self.assertEqual(len(self.stack.examples_stack), 1)

    async def test_autopop_consumes_only_original_top_layer(self) -> None:
        self.stack.push({"help": None}, autopop=True)
        self.stack.push({"undo": None}, autopop=True)
        classification = intents.IntentClassification("help", [], "help")
        await self.stack.handle_intent(classification)
        self.assertEqual(len(self.stack.handlers_stack), 2)
        await self.stack.handle_intent(classification)
        self.assertEqual(len(self.stack.handlers_stack), 1)
        await self.stack.handle_intent(classification)
        self.assertEqual(len(self.stack.handlers_stack), 1)

    async def test_non_autopop_top_preserves_lower_autopop_layer(self) -> None:
        self.stack.push({"help": None}, autopop=True)
        self.stack.push({"undo": None})
        await self.stack.handle_intent(intents.IntentClassification("help", [], "help"))
        self.assertEqual(len(self.stack.handlers_stack), 3)
        self.stack.pop()
        await self.stack.handle_intent(intents.IntentClassification("help", [], "help"))
        self.assertEqual(len(self.stack.handlers_stack), 1)

    async def test_autopop_on_handler_error_and_missing_handler(self) -> None:
        self.stack.push(
            {"help": Mock(side_effect=RuntimeError("handler failed"))}, autopop=True
        )
        with self.assertRaisesRegex(RuntimeError, "handler failed"):
            await self.stack.handle_intent(intents.IntentClassification("help", [], "help"))
        self.assertEqual(len(self.stack.handlers_stack), 1)
        self.stack.push({}, {"custom": ["custom"]}, autopop=True)
        with self.assertRaises(intents.MissingIntentHandlerError):
            await self.stack.handle_intent(
                intents.IntentClassification("custom", [], "custom")
            )
        self.assertEqual(len(self.stack.handlers_stack), 1)
        self.assertNotIn("custom", self.stack.accumulated_examples())

    @patch(
        "intents.interact_with_openai",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM failed"),
    )
    async def test_autopop_on_classifier_error(self, classify: Mock) -> None:
        self.stack.push({"help": None}, autopop=True)
        with self.assertRaisesRegex(RuntimeError, "LLM failed"):
            await self.stack.dispatch("help")
        self.assertEqual(len(self.stack.handlers_stack), 1)

    @patch("intents.interact_with_openai", new_callable=AsyncMock)
    async def test_separate_classification_preserves_handler_until_handled(
        self, classify: Mock
    ) -> None:
        classify.return_value = '{"intent": "help", "entities": []}'
        self.stack.push({"help": None}, autopop=True)
        classification = await self.stack.identify_intent("help")
        self.assertEqual(len(self.stack.handlers_stack), 2)
        self.assertIsNone(await self.stack.handle_intent(classification))
        self.assertEqual(len(self.stack.handlers_stack), 1)

    async def test_autopop_preserves_layer_pushed_by_handler(self) -> None:
        def handler(entities: list[str], message: str) -> str:
            self.assertEqual(len(self.stack.handlers_stack), 2)
            self.stack.push({"undo": None}, autopop=True)
            return "done"

        self.stack.push({"help": handler}, {"help": ["temporary"]}, autopop=True)
        await self.stack.handle_intent(intents.IntentClassification("help", [], "help"))
        self.assertEqual(len(self.stack.handlers_stack), 2)
        self.assertEqual(self.stack.handlers_stack[-1], {"undo": None})
        self.assertEqual(
            self.stack.accumulated_examples()["help"], intents.GENERAL_EXAMPLES["help"]
        )
        await self.stack.handle_intent(intents.IntentClassification("undo", [], "undo"))
        self.assertEqual(len(self.stack.handlers_stack), 1)

    async def test_handler_can_manually_pop_without_consuming_another_layer(self) -> None:
        self.stack.push({"help": None}, autopop=True)

        def handler(entities: list[str], message: str) -> None:
            self.stack.pop()

        self.stack.push({"help": handler}, autopop=True)
        await self.stack.handle_intent(intents.IntentClassification("help", [], "help"))
        self.assertEqual(len(self.stack.handlers_stack), 2)

    async def test_autopop_rejects_orphaning_new_handlers(self) -> None:
        def handler(entities: list[str], message: str) -> None:
            self.stack.push({"custom": None})

        self.stack.push(
            {"custom": handler}, {"custom": ["custom"]}, autopop=True
        )
        with self.assertRaisesRegex(ValueError, "custom"):
            await self.stack.handle_intent(
                intents.IntentClassification("custom", [], "custom")
            )
        self.assertEqual(len(self.stack.handlers_stack), 3)
        self.assertIn("custom", self.stack.accumulated_examples())

    async def test_default_redirects_and_null_handlers(self) -> None:
        urls = {
            "sign-up": "/account/register",
            "sign-in": "/account/signin",
            "sign-out": "/account/signout",
            "product-pricing": "/wikkit/pricing",
            "reset-password": "/account/recover",
            "help": "/wikkit/help",
            "create-poster": "/poster/create",
        }
        for intent in intents.GENERAL_EXAMPLES:
            with self.subTest(intent=intent):
                result = await self.stack.handle_intent(
                    intents.IntentClassification(intent, [], intent)
                )
                if intent in urls:
                    assert result is not None
                    self.assertEqual(
                        result.handler_result, {"type": "redirect", "url": urls[intent]}
                    )
                else:
                    self.assertIsNone(result)

    def test_client_json_preserves_message_and_classification(self) -> None:
        classification = intents.IntentClassification("help", ["Ada"], 'help "Ada"')
        self.assertEqual(json.loads(classification.to_client_json()), {
            "intent": "help", "entities": ["Ada"], "message": 'help "Ada"',
        })

    @patch("intents.interact_with_openai", new_callable=AsyncMock)
    async def test_classifier_uses_accumulated_examples_and_schema(self, classify: Mock) -> None:
        classify.return_value = '{"intent": "help", "entities": ["Ada"]}'
        self.stack.push({}, {"help": ["page assistance"]})
        result = await self.stack.dispatch("help Ada")
        assert result is not None
        self.assertEqual(result.entities, ["Ada"])
        prompt = classify.call_args.args[0]
        self.assertIn("- help: page assistance\n", prompt)
        self.assertNotIn("- help: help\n", prompt)
        self.assertIn("help Ada", prompt)
        schema = classify.call_args.kwargs["schema"]
        self.assertEqual(
            schema["properties"]["intent"]["enum"],
            list(self.stack.accumulated_examples()),
        )

    @patch("intents.interact_with_openai", new_callable=AsyncMock)
    async def test_invalid_classifier_responses_raise(self, classify: Mock) -> None:
        for response in (
            "[]", "null", "not json",
            '{"intent": "unknown", "entities": []}',
            '{"intent": "help", "entities": "Ada"}',
            '{"intent": "help", "entities": [42]}',
            '{"intent": "help"}',
        ):
            with self.subTest(response=response):
                classify.return_value = response
                error = TypeError if response in ("[]", "null") else ValueError
                with self.assertRaises(error):
                    await self.stack.dispatch("help")

    @patch("intents.interact_with_openai", new_callable=AsyncMock)
    async def test_blank_message_rejected_before_llm(self, classify: Mock) -> None:
        with self.assertRaisesRegex(ValueError, "message"):
            await self.stack.dispatch(" \n")
        classify.assert_not_called()

    @patch("intents.interact_with_openai", new_callable=AsyncMock)
    async def test_single_map_api(self, classify: Mock) -> None:
        classify.return_value = '{"intent": "custom", "entities": ["Ada"]}'
        handler = Mock(return_value="done")
        result = await intents.identify_intent(
            {"custom": ["custom"]}, {"custom": handler}, "custom Ada"
        )
        assert result is not None
        self.assertEqual(result.handler_result, "done")
        handler.assert_called_once_with(["Ada"], "custom Ada")
        self.assertIsNone(await intents.identify_intent(
            {"custom": ["custom"]}, {"custom": None}, "custom"
        ))
        with self.assertRaises(intents.MissingIntentHandlerError):
            await intents.identify_intent({"custom": ["custom"]}, {}, "custom")
        with self.assertRaisesRegex(ValueError, "at least one"):
            await intents.identify_intent({}, {}, "custom")


class LLMClientTests(unittest.IsolatedAsyncioTestCase):
    @patch("llm.CACHE_DIR")
    @patch("llm.NO_CACHE", True)
    @patch("llm.openai.AsyncOpenAI")
    async def test_classification_can_construct_openai_client(
        self, client: Mock, cache_dir: Mock
    ) -> None:
        client.return_value.__aenter__.return_value = client.return_value
        client.return_value.responses.create = AsyncMock(
            return_value=Mock(output_text=" response ")
        )
        self.assertEqual(await llm.interact_with_openai("test prompt"), "response")
        client.assert_called_once_with()
        cache_dir.mkdir.assert_called_once()


if __name__ == "__main__":
    unittest.main()
