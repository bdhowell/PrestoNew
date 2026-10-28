import Levenshtein
import logging
import num2words
import psycopg
import random
import re
import string

from fastapi import HTTPException, Request
from starlette.datastructures import URL

import identity
import posters

logger = logging.getLogger(__name__)


def _resumption_identity(request: Request) -> tuple[int, int]:
    uid = getattr(request.state, "user_id", None)
    if uid is None:
        raise HTTPException(401, "Please sign in to manage your posters.")
    session = identity.session(request)
    pid = session.get("poster_id") if session is not None else None
    if type(pid) is not int or pid <= 0:
        raise HTTPException(409, "Please create or open a poster before managing its resumption page.")
    return uid, pid


async def resume(request: Request, url: str | URL | None = None, clear: bool = True) -> None:
    """Save a URL, marked for clearing on resumption unless clear is False."""
    uid, pid = _resumption_identity(request)
    value = str(request.url if url is None else url)
    if not value.strip():
        raise HTTPException(422, "A resumption URL is required.")
    if clear:
        page, fragment_separator, fragment = value.partition("#")
        separator = "&" if "?" in page else "?"
        if page.endswith(("?", "&")):
            separator = ""
        value = f"{page}{separator}xyzzy=arpa{fragment_separator}{fragment}"
    try:
        saved = await posters.store().set_resumption(uid, pid, value)
    except psycopg.Error:
        logger.exception("Could not save poster resumption URL")
        raise HTTPException(503, "Your resumption page could not be saved. Please try again.") from None
    if not saved:
        raise HTTPException(404, "Poster not found")


async def clear_resume(request: Request) -> None:
    """Clear the saved resumption URL for the signed-in user's selected poster."""
    uid, pid = _resumption_identity(request)
    try:
        cleared = await posters.store().set_resumption(uid, pid, None)
    except psycopg.Error:
        logger.exception("Could not clear poster resumption URL")
        raise HTTPException(503, "Your resumption page could not be cleared. Please try again.") from None
    if not cleared:
        raise HTTPException(404, "Poster not found")


SEEKING_ACTIONS = {
    "startover" : "restart",
    "restart" : "restart",
    "reset" : "restart",
    "goback" : "revert",
    "undo" : "revert",
    "revert" : "revert"
}

SEEKING_OPTIONS = {
    ("callme", r"call\.?me\.?"),
    ("mynameis", r"my\.?name\.?is\.?"),
    ("iam", r"i\.?am\.?")
}

PUNCTUATION_TRANSLATION_TABLE = str.maketrans('', '', string.punctuation + "‘’“”")

AGENT_NAME = "Wikkit"
AGENT_THE_NAME = f"{AGENT_NAME}, the PrestoPresentations Wizard"


def format_date(datetime_obj):
    day = num2words.num2words(datetime_obj.day, to='ordinal')
    return datetime_obj.strftime(f"%A, %B {day}, %Y")

def add_the(name: str, leading: bool = True) -> str:
    return name if name.split()[0].endswith("'s") or name.split()[0].endswith("s") else f"The {name}" if leading else f"the {name}"

def pluralize(n: int, es=False):
    if es:
        return "es" if n == 1 else ""

    return "s" if n != 1 else ""

def check_seeking(str: str, keywords: list[str]) -> dict|None:
    cleaned_str = clean_string(str, ".")

    for action, pattern in SEEKING_OPTIONS:
        if re.search(pattern, cleaned_str):
            return {"type":"action", "response": action}

    iwantto = "iwantto" in cleaned_str or "iwouldliketo" in cleaned_str
    iwant = "iwant" in cleaned_str or "iwouldlike" in cleaned_str
    ineed = "ineed" in cleaned_str or "irequire" in cleaned_str
    iall = iwantto or iwant or ineed

    if iwantto:
        scope = "todo"
    elif iwant:
        scope = "wants"
    elif ineed:
        scope = "needs"
    else:
        scope = None

    for action, response in SEEKING_ACTIONS.items():
        if cleaned_str == action or (iall and action in cleaned_str):
            return {"type":"action", "response": response, "scope": scope}

    if not iall:
        return None
    
    for keyword in keywords:
        if clean_string(keyword) in cleaned_str:
            return {"type":"caller", "keyword": keyword, "scope": scope }
        
    return None

def clean_string(s: str, spaces="") -> str:
    """Lowercase, remove punctuation, and replace spaces (removed by default)."""
    return " ".join(s.split()).replace(" ", spaces).translate(PUNCTUATION_TRANSLATION_TABLE).lower().strip()

def clean_response(response: str, other: list[str]|None = None) -> dict|None:
    if not response:
        return None
    
    response = clean_string(response)

    if response in ["no",  "nope", "nah"] or response == "n":
        return {"type": "system", "response": "no"}
    if response in ["yes", "sure", "ok", "okay", "yep"] or response == "y":
        return {"type":"system", "response": "yes"}
    if response in ["idontknow", "imnotsure", "notsure", "unsure", "idk", "maybe"]:
        return {"type":"system", "response": "idk"}

    highest_ratio = 0
    best_match = None

    if other:
        for item in other:
            current_ratio = Levenshtein.ratio(response, clean_string(item))

            if current_ratio > highest_ratio:
                highest_ratio = current_ratio
                best_match = item

    if highest_ratio < 0.7:
        best_match = None

    if best_match:
        return {"type": "caller", "match": best_match, "ratio": highest_ratio, "cleaned": response}
    
    return {"type": "unknown", "response": response}
