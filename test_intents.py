import json
import unittest
from unittest.mock import Mock, patch

import intents
import llm


class IntentStackTests(unittest.TestCase):
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
        self.assertEqual(self.stack.accumulated_examples()["help"], ("help",))

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

    def test_handler_lookup_falls_through_and_passes_arguments(self) -> None:
        handler = Mock(return_value={"done": True})
        self.stack.push({"help": handler})
        self.stack.push({"undo": None})
        classification = intents.IntentClassification("help", ["Ada"], "help Ada")
        result = self.stack.handle_intent(classification)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.intent, "help")
        self.assertEqual(result.entities, ["Ada"])
        self.assertEqual(result.handler_result, {"done": True})
        handler.assert_called_once_with(["Ada"], "help Ada")

    def test_none_handler_blocks_lower_handler(self) -> None:
        handler = Mock()
        self.stack.push({"help": handler})
        self.stack.push({"help": None})
        self.assertIsNone(
            self.stack.handle_intent(intents.IntentClassification("help", [], "help"))
        )
        handler.assert_not_called()

    def test_callable_returning_none_still_returns_dispatch_result(self) -> None:
        self.stack.push({"help": lambda entities, message: None})
        result = self.stack.handle_intent(
            intents.IntentClassification("help", [], "help")
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNone(result.handler_result)

    def test_missing_handler_raises(self) -> None:
        self.stack.push({}, {"custom": ["custom"]})
        with self.assertRaisesRegex(intents.MissingIntentHandlerError, "custom"):
            self.stack.handle_intent(
                intents.IntentClassification("custom", [], "custom")
            )

    def test_handler_errors_propagate(self) -> None:
        self.stack.push({"help": Mock(side_effect=RuntimeError("handler failed"))})
        with self.assertRaisesRegex(RuntimeError, "handler failed"):
            self.stack.handle_intent(intents.IntentClassification("help", [], "help"))

    def test_default_redirects_and_null_handlers(self) -> None:
        urls = {
            "sign-up": "/account/register",
            "sign-in": "/acccount/signin",
            "sign-out": "/account/signout",
            "product-pricing": "/wikkit/pricing",
            "reset-password": "/account/reset",
            "help": "/wikkit/help",
            "create-poster": "/poster/create",
        }
        for intent in intents.GENERAL_EXAMPLES:
            with self.subTest(intent=intent):
                result = self.stack.handle_intent(
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

    @patch("intents.interact_with_openai")
    def test_classifier_uses_accumulated_examples_and_schema(self, classify: Mock) -> None:
        classify.return_value = '{"intent": "help", "entities": ["Ada"]}'
        self.stack.push({}, {"help": ["page assistance"]})
        result = self.stack.dispatch("help Ada")
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

    @patch("intents.interact_with_openai")
    def test_invalid_classifier_responses_raise(self, classify: Mock) -> None:
        for response in (
            "[]", "null", "not json",
            '{"intent": "unknown", "entities": []}',
            '{"intent": "help", "entities": "Ada"}',
            '{"intent": "help", "entities": [42]}',
            '{"intent": "help"}',
        ):
            with self.subTest(response=response):
                classify.return_value = response
                with self.assertRaises(ValueError):
                    self.stack.dispatch("help")

    @patch("intents.interact_with_openai")
    def test_blank_message_rejected_before_llm(self, classify: Mock) -> None:
        with self.assertRaisesRegex(ValueError, "message"):
            self.stack.dispatch(" \n")
        classify.assert_not_called()

    @patch("intents.interact_with_openai")
    def test_single_map_api(self, classify: Mock) -> None:
        classify.return_value = '{"intent": "custom", "entities": ["Ada"]}'
        handler = Mock(return_value="done")
        result = intents.identify_intent(
            {"custom": ["custom"]}, {"custom": handler}, "custom Ada"
        )
        assert result is not None
        self.assertEqual(result.handler_result, "done")
        handler.assert_called_once_with(["Ada"], "custom Ada")
        self.assertIsNone(intents.identify_intent(
            {"custom": ["custom"]}, {"custom": None}, "custom"
        ))
        with self.assertRaises(intents.MissingIntentHandlerError):
            intents.identify_intent({"custom": ["custom"]}, {}, "custom")
        with self.assertRaisesRegex(ValueError, "at least one"):
            intents.identify_intent({}, {}, "custom")


class LLMClientTests(unittest.TestCase):
    @patch("llm.CACHE_DIR")
    @patch("llm.NO_CACHE", True)
    @patch("llm.openai.OpenAI")
    def test_classification_can_construct_openai_client(
        self, client: Mock, cache_dir: Mock
    ) -> None:
        client.return_value.responses.create.return_value.output_text = " response "
        self.assertEqual(llm.interact_with_openai("test prompt"), "response")
        client.assert_called_once_with()
        cache_dir.mkdir.assert_called_once()


if __name__ == "__main__":
    unittest.main()
