import asyncio
import hashlib
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

import common
import openai


Result = TypeVar("Result")


@dataclass(frozen=True)
class IntentDispatchResult(Generic[Result]):
    intent: str
    entities: list[str]
    handler_result: Result|None


SYSTEM_PROMPT = f"""
## Instructions
You are {common.AGENT_THE_NAME}.
You are an expert in preparing conference posters, including layout and sizing, and preparing graphics and text elements.
You guide each customer through preparing their conference poster using helpful and detailed conversations.
"""

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

NO_CACHE = "-n" in sys.argv or "--no-cache" in sys.argv
CLEAR_CACHE = "-c" in sys.argv or "--clear-cache" in sys.argv
NO_DELAY = "-d" in sys.argv or "--no-delay" in sys.argv 

_cache_init_lock = asyncio.Lock()
_cache_initialized = False


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


async def _initialize_cache() -> None:
    global _cache_initialized
    if _cache_initialized:
        return
    async with _cache_init_lock:
        if not _cache_initialized:
            await asyncio.to_thread(_init_cache)
            _cache_initialized = True


async def interact_with_openai(
    prompt: str,
    schema: dict[str, object] | None = None,
    model: str = "gpt-5.6-terra",
    temperature: float = 0.7,
    system_prompt: str = SYSTEM_PROMPT,
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

    await _initialize_cache()
    if not NO_CACHE:
        try:
            cached = await asyncio.to_thread(cache_file.read_text, encoding="utf-8")
        except FileNotFoundError:
            pass
        else:
            return json.loads(cached)["output_text"]

    async with openai.AsyncOpenAI() as client:
        response = await client.responses.create(**request)
        output_text = response.output_text.strip()

    await asyncio.to_thread(
        cache_file.write_text, json.dumps({"output_text": output_text}), encoding="utf-8"
    )

    return output_text
