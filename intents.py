"""Conversation-local intent classification and layered handler dispatch."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeVar

from llm import IntentDispatchResult, interact_with_openai

Result = TypeVar("Result")
type Examples = Mapping[str, Sequence[str]]
type Handler = Callable[[list[str], str], object]
type Handlers = Mapping[str, Handler | None]

GENERAL_EXAMPLES: Examples = MappingProxyType({
    "sign-out": ("sign out", "sign out", "log off", "logout"),
    "sign-up": ("register", "create an account", "sign up", "sign-up", "make a poster"),
    "sign-in": ("sign in", "log in", "login", "log-in"),
    "product-pricing": ("pricing", "what does it cost", "is this free"),
    "reset-password": ("reset password", "change password", "lost password"),
    "purchase": ("buy",),
    "delete": ("remove", "delete", "discard"),
    "create-poster": ("create poster",),
    "delete-poster": ("delete poster",),
    "download": ("downloads",),
    "help": ("help",),
    "undo": ("undo",),
    "redo": ("redo",),
    "okay": ("okay",),
})


def _redirect(url: str) -> Handler:
    return lambda _entities, _message: {"type": "redirect", "url": url}


GENERAL_HANDLERS: Handlers = MappingProxyType({
    "sign-up": _redirect("/account/register"),
    "sign-in": _redirect("/acccount/signin"),
    "sign-out": _redirect("/account/signout"),
    "product-pricing": _redirect("/wikkit/pricing"),
    "reset-password": _redirect("/account/reset"),
    "help": _redirect("/wikkit/help"),
    "create-poster": _redirect("/poster/create"),
    "purchase": None,
    "delete": None,
    "delete-poster": None,
    "download": None,
    "undo": None,
    "redo": None,
    "okay": None,
})


@dataclass(frozen=True)
class IntentClassification:
    intent: str
    entities: list[str]
    message: str

    def to_client_json(self) -> str:
        """Serialize a classified message for forwarding to the client."""
        return json.dumps({
            "message": self.message,
            "intent": self.intent,
            "entities": self.entities,
        })


class MissingIntentHandlerError(LookupError):
    """No handler map contains the classified intent."""


def _copy_examples(examples: Examples) -> Examples:
    copied: dict[str, tuple[str, ...]] = {}
    for intent, phrases in examples.items():
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent names must be nonempty strings")
        if isinstance(phrases, str) or not phrases:
            raise ValueError(f"intent {intent!r} must have a sequence of examples")
        if any(not isinstance(phrase, str) or not phrase.strip() for phrase in phrases):
            raise ValueError(f"intent {intent!r} must have nonempty string examples")
        copied[intent] = tuple(phrases)
    return MappingProxyType(copied)


def _validate_handlers(intent_map: Handlers, examples: Examples) -> None:
    unexpected_handlers = set(intent_map) - set(examples)
    if unexpected_handlers:
        raise ValueError(
            f"handlers have no matching intent: {', '.join(sorted(unexpected_handlers))}"
        )
    for intent, handler in intent_map.items():
        if handler is not None and not callable(handler):
            raise TypeError(f"handler for {intent!r} must be callable or None")


def _classify(intents: Examples, message: str) -> IntentClassification:
    if not intents:
        raise ValueError("intents must contain at least one intent")
    if not message.strip():
        raise ValueError("message must not be empty")

    intent_descriptions = "\n".join(
        f"- {intent}: {', '.join(phrases)}" for intent, phrases in intents.items()
    )
    prompt = (
        "Classify the user's message into exactly one of the supplied intents "
        "and extract any named entities explicitly mentioned in it. Use the "
        "example phrases as guidance, not as an exhaustive list. Return entity "
        "strings exactly as written, in order, without duplicates. Do not "
        "include generic concepts or inferred information; use an empty list "
        "when there are no named entities.\n\n"
        f"Intents:\n{intent_descriptions}\n\n"
        f"Message:\n{message}"
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": list(intents),
            },
            "entities": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["intent", "entities"],
        "additionalProperties": False,
    }
    result: object = json.loads(
        interact_with_openai(
            prompt,
            schema=schema,
            system_prompt=(
                "You classify user messages by intent and extract named "
                "entities. Follow the provided schema exactly."
            ),
        )
    )
    if not isinstance(result, dict):
        raise TypeError(f"classifier returned an invalid response: {result!r}")
    intent = result.get("intent")
    if not isinstance(intent, str) or intent not in intents:
        raise ValueError(f"classifier returned an unknown intent: {intent!r}")
    entities = result.get("entities")
    if not isinstance(entities, list) or not all(
        isinstance(entity, str) for entity in entities
    ):
        raise ValueError(f"classifier returned invalid entities: {entities!r}")
    return IntentClassification(intent, entities, message)


def _dispatch(
    classification: IntentClassification,
    handler_maps: Sequence[Mapping[str, Callable[[list[str], str], Result] | None]],
) -> IntentDispatchResult[Result] | None:
    for handlers in reversed(handler_maps):
        if classification.intent not in handlers:
            continue
        handler = handlers[classification.intent]
        if handler is None:
            return None
        result = handler(classification.entities, classification.message)
        return IntentDispatchResult(
            intent=classification.intent,
            entities=classification.entities,
            handler_result=result,
        )
    raise MissingIntentHandlerError(
        f"no handler for intent: {classification.intent!r}"
    )


def identify_intent(
    intents: Examples,
    intent_map: Mapping[str, Callable[[list[str], str], Result] | None],
    message: str,
) -> IntentDispatchResult[Result] | None:
    """Classify and dispatch with one examples map and one handlers map."""
    examples = _copy_examples(intents)
    _validate_handlers(intent_map, examples)
    return _dispatch(_classify(examples, message), [intent_map])


@dataclass(frozen=True, eq=False)
class _IntentLayer:
    handlers: Handlers
    examples: Examples | None = None
    autopop: bool = False


class IntentStack:
    """Create one stack per conversation; general maps cannot be popped."""

    def __init__(self) -> None:
        self._layers = [_IntentLayer(GENERAL_HANDLERS, GENERAL_EXAMPLES)]

    @property
    def examples_stack(self) -> tuple[Examples, ...]:
        return tuple(
            layer.examples for layer in self._layers if layer.examples is not None
        )

    @property
    def handlers_stack(self) -> tuple[Handlers, ...]:
        return tuple(layer.handlers for layer in self._layers)

    def accumulated_examples(self) -> dict[str, Sequence[str]]:
        examples: dict[str, Sequence[str]] = {}
        for layer in self.examples_stack:
            examples.update(layer)
        return examples

    def push(
        self, handlers: Handlers, examples: Examples | None = None,
        autopop: bool = False,
    ) -> None:
        """Push a layer, optionally consumed after handling the next message."""
        copied_examples = None if examples is None else _copy_examples(examples)
        copied_handlers = MappingProxyType(dict(handlers))
        available = self.accumulated_examples()
        if copied_examples is not None:
            available.update(copied_examples)
        _validate_handlers(copied_handlers, available)
        self._layers.append(_IntentLayer(copied_handlers, copied_examples, autopop))

    def pop(self) -> tuple[Examples | None, Handlers]:
        """Pop top handlers and their paired examples, if any, atomically."""
        if len(self._layers) == 1:
            raise IndexError("cannot pop the general intent maps")
        layer = self._layers.pop()
        return layer.examples, layer.handlers

    def _autopop(self, layer: _IntentLayer) -> None:
        if not layer.autopop or layer not in self._layers:
            return
        remaining = [entry for entry in self._layers if entry is not layer]
        available: dict[str, Sequence[str]] = {}
        for entry in remaining:
            if entry.examples is not None:
                available.update(entry.examples)
            _validate_handlers(entry.handlers, available)
        self._layers.remove(layer)

    def identify_intent(self, message: str) -> IntentClassification:
        return _classify(self.accumulated_examples(), message)

    def handle_intent(
        self, classification: IntentClassification
    ) -> IntentDispatchResult[object] | None:
        layer = self._layers[-1]
        try:
            return _dispatch(classification, self.handlers_stack)
        finally:
            self._autopop(layer)

    def dispatch(self, message: str) -> IntentDispatchResult[object] | None:
        layer = self._layers[-1]
        try:
            classification = self.identify_intent(message)
            return _dispatch(classification, self.handlers_stack)
        finally:
            self._autopop(layer)
