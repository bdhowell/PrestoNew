import hashlib
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

import openai

import chatbot


Result = TypeVar("Result")


@dataclass(frozen=True)
class IntentDispatchResult(Generic[Result]):
    intent: str
    entities: list[str]
    handler_result: Result


SYSTEM_PROMPT = f"""
## Instructions
You are {chatbot.AGENT_THE_NAME}.
You are an expert in preparing conference posters, including layout and sizing, and preparing graphics and text elements.
You guide each customer through preparing their conference poster using helpful and detailed conversations.
"""

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

NO_CACHE = "-n" in sys.argv or "--no-cache" in sys.argv
CLEAR_CACHE = "-c" in sys.argv or "--clear-cache" in sys.argv
NO_DELAY = "-d" in sys.argv or "--no-delay" in sys.argv 


def _cache_key(request: dict[str, Any]) -> str:
    payload = json.dumps(request, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def clear_cache() -> None:
    if not CACHE_DIR.exists():
        return
    for cached_file in CACHE_DIR.glob("*.json"):
        cached_file.unlink(missing_ok=True)


def prune_expired_cache(max_age_seconds: int = CACHE_MAX_AGE_SECONDS) -> None:
    if not CACHE_DIR.exists():
        return
    now = time.time()
    for cached_file in CACHE_DIR.glob("*.json"):
        if now - cached_file.stat().st_mtime > max_age_seconds:
            cached_file.unlink(missing_ok=True)


def _init_cache() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CLEAR_CACHE:
        clear_cache()
    prune_expired_cache()

_init_cache()


def interact_with_openai(
    prompt: str,
    schema: dict[str, object] | None = None,
    model: str = "gpt-5.6-terra",
    temperature: float = 0.7,
    system_prompt: str = chatbot.SYSTEM_PROMPT,
) -> str:
    request: dict[str, Any] = {
        "model": model,
        "instructions": system_prompt,
        "input": prompt,
        "tools": [{"type": "web_search_preview"}],
    }
    if not model.startswith("gpt-5"):
        request["temperature"] = temperature
    if schema is not None:
        request["text"] = {
            "format": {
                "type": "json_schema",
                "name": "response_schema",
                "schema": schema,
                "strict": True,
            }
        }

    cache_key = _cache_key(request)
    cache_file = _cache_path(cache_key)

    if not NO_CACHE and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))["output_text"]

    client = openai.OpenAI()
    response = client.responses.create(**request)
    output_text = response.output_text.strip()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"output_text": output_text}), encoding="utf-8")

    return output_text


def dispatch_intent(
    intents: Mapping[str, Sequence[str]],
    intent_map: Mapping[str, Callable[[str], Result]],
    message: str,
) -> IntentDispatchResult[Result]:
    if not intents:
        raise ValueError("intents must contain at least one intent")
    if not message.strip():
        raise ValueError("message must not be empty")

    unexpected_handlers = set(intent_map) - set(intents)
    if unexpected_handlers:
        raise ValueError(
            f"handlers have no matching intent: {', '.join(sorted(unexpected_handlers))}"
        )

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
    result: dict[str, Any] = json.loads(
        interact_with_openai(
            prompt,
            schema=schema,
            system_prompt=(
                "You classify user messages by intent and extract named "
                "entities. Follow the provided schema exactly."
            ),
        )
    )
    intent = result.get("intent")
    if not isinstance(intent, str) or intent not in intents:
        raise ValueError(f"classifier returned an unknown intent: {intent!r}")
    entities = result.get("entities")
    if not isinstance(entities, list) or not all(
        isinstance(entity, str) for entity in entities
    ):
        raise ValueError(f"classifier returned invalid entities: {entities!r}")
    if intent not in intent_map:
        raise LookupError(f"no handler registered for intent: {intent}")

    return IntentDispatchResult(
        intent=intent,
        entities=entities,
        handler_result=intent_map[intent](message),
    )
