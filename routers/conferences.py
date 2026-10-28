"""Find the conference or fair where the selected draft poster will be shown."""

import logging
import re

import Levenshtein
import openai
import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

import conferences
import identity
import posters
from common import clean_response
from conferences import Conference
from routers import wikkit
from routers.wikkit import read_json, render_page
from wikkit import Chatbot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/conferences", tags=["conferences"])

LAYOUT_PAGE = "/layout/upload"
MATCH_RATIO = 0.7
MAX_CRITERIA_LENGTH = 2000
DECLINES = ["none", "noneofthese", "nomatch", "continue", "skip", "moveon", "continuewithoutconferenceinformation"]
FIND_ERRORS = (openai.OpenAIError, ValueError, KeyError, TypeError)

INTRO = """
<div>Many conferences and fairs have specific requirements for poster designs.</div>
<div>If we can find your conference or fair, I can likely find any poster requirements for it.</div>
<div>Please provide any information you have about your conference or fair.</div>
<div>Besides its name, its location (some conferences and fairs occur in multiple cities), purpose, and dates are helpful.</div>
"""
NO_INFORMATION = "That&rsquo;s okay, we can continue without conference information."
CONTINUE_WITHOUT = "Okay, we will continue without conference information."
YES_NO = (
    '<p class="centered">'
    '<button class="action" data-sends="yes" data-group="confirmation">Yes</button> &nbsp;'
    '<button class="action" data-sends="no" data-group="confirmation">No</button></p>'
)
CONFIRM = "<p>I found a conference or fair that I think is yours.<br>Am I right?</p>" + YES_NO
MORE_INFORMATION = "<p>Please tell me anything else you know about your conference or fair.</p>"


def not_found(name: str) -> str:
    return (
        f"<p>I&rsquo;m sorry {name} I couldn&rsquo;t find any matching conferences or fairs.</p>"
        "<div>Can you add anything?</div>" + YES_NO
    )


def scope(table: list[Conference]) -> str:
    kinds = {conference.kind for conference in table}
    if kinds == {"conference"}:
        return "conference"
    if kinds == {"fair"}:
        return "fair"
    return "conference or fair"


def select_message(name: str, table: list[Conference]) -> str:
    kind = scope(table)
    return f"""
<p>
{name}, please select the {kind} that best matches your poster requirements by clicking on it, entering its corresponding number, or telling me its name.
If there is no matching {kind}, just let me know and we&rsquo;ll move on.
</p>
<p>
If none of these are correct, just let me know to continue without conference information.
</p>
Enter the number or the name of the {kind}
"""


def similar(text: str, conference: Conference) -> bool:
    return Levenshtein.ratio(" ".join(text.lower().split()), " ".join(conference.name.lower().split())) > MATCH_RATIO


def merge(table: list[Conference], found: list[Conference]) -> None:
    """Add newly found conferences to the table, keeping stored rows over web duplicates."""
    keys = {conference.key: index for index, conference in enumerate(table)}
    for conference in found:
        index = keys.get(conference.key)
        if index is None:
            keys[conference.key] = len(table)
            table.append(conference)
        elif table[index].cid is None and conference.cid is not None:
            table[index] = conference


def choice(reply: str, table: list[Conference]) -> Conference | str:
    """Interpret a reply to the selection prompt.

    Returns the chosen conference, "decline", "unclear", or "more" for
    additional search information.
    """
    number = re.fullmatch(r"(?:number|no\.?|#)?\s*(\d{1,3})\.?", reply.strip(), re.IGNORECASE)
    if number:
        index = int(number.group(1))
        return table[index - 1] if 1 <= index <= len(table) else "unclear"
    cleaned = clean_response(reply, [conference.name for conference in table] + DECLINES) or {}
    if cleaned.get("type") == "system":
        return "unclear" if cleaned["response"] == "yes" else "decline"
    if cleaned.get("type") == "caller":
        if cleaned["match"] in DECLINES:
            return "decline"
        return next(conference for conference in table if conference.name == cleaned["match"])
    return "more"


async def find(criteria: str) -> list[Conference]:
    """Search the web for conferences matching free-text criteria."""
    return [conferences.from_search_result(result) for result in await conferences.get_conference_info(criteria) or []]


async def selected_draft(request: Request) -> int | None:
    pid = getattr(request.state, "poster_id", None)
    uid = getattr(request.state, "user_id", None)
    if pid is None or uid is None:
        return None
    poster = await posters.store().get(uid, pid)
    return pid if poster is not None and not poster.purchased else None


@router.get("/find")
async def find_page(request: Request) -> RedirectResponse:
    """Navigation (e.g., after sign-in) goes to the interactive conference finder."""
    return RedirectResponse("/conferences/query", status_code=303)


@router.post("/find")
async def find_conferences(request: Request) -> JSONResponse:
    data = await read_json(request)
    if getattr(request.state, "user_id", None) is None:
        raise HTTPException(401, "Please sign in to find conferences.")
    criteria = data.get("criteria")
    if not isinstance(criteria, str) or not criteria.strip() or len(criteria) > MAX_CRITERIA_LENGTH:
        raise HTTPException(422, f"Please describe the conference in up to {MAX_CRITERIA_LENGTH} characters.")
    try:
        found = await find(" ".join(criteria.split()))
    except FIND_ERRORS:
        logger.exception("Could not find conferences")
        raise HTTPException(503, "Conferences could not be searched. Please try again.") from None
    return JSONResponse({"ok": True, "conferences": [conference.public() for conference in found]})


@router.get("/query")
async def query_page(request: Request) -> Response:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    try:
        pid = await selected_draft(request)
    except psycopg.Error:
        logger.exception("Could not load the poster for conference selection")
        raise HTTPException(503, "Your poster could not be loaded. Please try again.") from None
    if pid is None:
        return RedirectResponse("/posters/list", status_code=303)
    return render_page(request, "conferences/query.html", "conferences-query")


async def continue_without(bot: Chatbot, message: str) -> None:
    await bot.send({"say": f"<p>{message}</p>", "command": {"does": "redirect", "payload": LAYOUT_PAGE}})


async def choose(bot: Chatbot, uid: int, pid: int, conference: Conference) -> bool:
    """Store and attach the conference, remember it, and move on to uploads."""
    try:
        conference = await conferences.store().select(uid, pid, conference)
        if bot.long_term_memory is None:
            raise LookupError("Poster memory is unavailable.")
        await bot.long_term_memory.set("conference", conference.memory())
    except LookupError as error:
        await bot.send({"say": f"<p>I&rsquo;m sorry, I couldn&rsquo;t select that conference. {error}</p>"})
        return False
    except psycopg.Error:
        logger.exception("Could not select conference for poster %s", pid)
        await bot.send({"say": "<p>I&rsquo;m sorry, I couldn&rsquo;t save that conference. Please try again.</p>"})
        return False
    await bot.send({"command": {"does": "redirect", "payload": LAYOUT_PAGE}})
    return True


async def query_workflow(bot: Chatbot) -> None:
    state = bot.websocket.state
    uid = getattr(state, "user_id", None)
    pid = getattr(state, "poster_id", None)
    if uid is None or pid is None:
        await bot.send({"say": "<p>Please create or open a poster before choosing its conference.</p>"})
        return
    name = identity.display_name(bot.websocket)
    latest = await bot.request_response(INTRO)
    if (clean_response(latest) or {}).get("response") in ("idk", "no"):
        await continue_without(bot, NO_INFORMATION)
        return
    criteria = latest.strip()
    table: list[Conference] = []
    while True:
        await bot.send({"command": {"does": "conferences-searching"}})
        try:
            stored = await conferences.store().search(criteria)
        except psycopg.Error:
            logger.exception("Could not search stored conferences")
            stored = []
        merge(table, stored)
        if stored:
            await show(bot, table)
            if len(stored) == 1 and similar(latest, stored[0]):
                reply = await bot.request_response(CONFIRM)
                picked = choice(reply, table)
                if (clean_response(reply) or {}).get("response") == "yes":
                    picked = stored[0]
                if isinstance(picked, Conference) and await choose(bot, uid, pid, picked):
                    return
                if picked == "more":
                    latest = reply.strip()
                    criteria = f"{criteria}\n{latest}"
                    continue
            await bot.send({"command": {"does": "conferences-searching"}})
        try:
            found = await find(criteria)
        except FIND_ERRORS:
            logger.exception("Could not find conferences")
            found = []
            await bot.send({"say": "<p>I&rsquo;m sorry, I couldn&rsquo;t search for conferences and fairs just now.</p>"})
        merge(table, found)
        await show(bot, table)
        if not table:
            reply = await bot.request_response(not_found(name))
            answer = (clean_response(reply) or {}).get("response")
            if answer == "yes":
                reply = await bot.request_response(MORE_INFORMATION)
                answer = (clean_response(reply) or {}).get("response")
            if answer in ("no", "idk"):
                await continue_without(bot, CONTINUE_WITHOUT)
                return
        else:
            prompt = select_message(name, table)
            while True:
                reply = await bot.request_response(prompt)
                picked = choice(reply, table)
                if isinstance(picked, Conference):
                    if await choose(bot, uid, pid, picked):
                        return
                    continue
                if picked == "decline":
                    await continue_without(bot, CONTINUE_WITHOUT)
                    return
                if picked == "more":
                    break
                prompt = f"<p>Please enter the number or the name of the {scope(table)}, or tell me more about it.</p>"
        latest = reply.strip()
        criteria = f"{criteria}\n{latest}"


async def show(bot: Chatbot, table: list[Conference]) -> None:
    await bot.send({"command": {
        "does": "show-conferences", "payload": [conference.public() for conference in table],
    }})


wikkit.WORKFLOWS["conferences-query"] = query_workflow
