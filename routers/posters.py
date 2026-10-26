"""Poster listing, creation, deletion, and read-only previews."""

import html
import logging
import re
from typing import Literal

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import identity
import posters
import elucidation
from markupsafe import Markup
from common import clean_response
from routers.wikkit import BASE_DIR, read_json, render_page, templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/posters", tags=["posters"])


def _uid(request: Request) -> int:
    uid = getattr(request.state, "user_id", None)
    if uid is None:
        raise HTTPException(401, "Please sign in to manage your posters.")
    return uid


def _select(request: Request, pid: int) -> None:
    session = identity.session(request)
    if session is None:
        raise HTTPException(503, "Poster editing requires server-side sessions.")
    session["poster_id"] = pid


async def _owned(request: Request, pid: int) -> posters.Poster:
    poster = await posters.store().get(_uid(request), pid)
    if poster is None:
        raise HTTPException(404, "Poster not found")
    return poster


def installed_themes() -> list[str]:
    css = (BASE_DIR / "static/css/themes.css").read_text()
    themes = re.findall(r"^\s*\.([a-zA-Z_][a-zA-Z0-9_-]*)\s*\{", css, re.MULTILINE)
    if not themes:
        raise RuntimeError("No poster themes are installed")
    return themes


@router.get("/list", response_class=HTMLResponse)
async def poster_list(
    request: Request, q: str = Query("", max_length=500),
    sort: posters.Sort = "name", direction: posters.Direction = "asc",
    view: Literal["list", "grid"] = "list",
) -> Response:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    query = " ".join(q.split())
    embedding, notice = await posters.query_embedding(query) if query else (None, None)
    try:
        items = await posters.store().list(_uid(request), query, embedding, sort, direction)
    except psycopg.Error:
        logger.exception("Could not list posters")
        return render_page(
            request, "posters/list.html", "posters-list",
            "<p>I couldn&rsquo;t load your posters. Please try again.</p>",
            status_code=503, items=[], q=q, sort=sort, direction=direction, view=view,
            notice="Your posters could not be loaded. Please reload to try again.",
        )
    return render_page(
        request, "posters/list.html", "posters-list",
        items=items, q=query, sort=sort, direction=direction, view=view, notice=notice,
    )


@router.post("/check")
async def check_poster(request: Request) -> JSONResponse:
    await read_json(request)
    uid = _uid(request)
    try:
        pid = await posters.store().unpurchased(uid)
    except psycopg.Error:
        logger.exception("Could not check for an unpurchased poster")
        raise HTTPException(503, "Your posters could not be checked. Please try again.") from None
    if pid is not None:
        return JSONResponse({
            "ok": False,
            "say": f"<p>I&rsquo;m sorry, {identity.display_name(request)}, "
                   "you can work on only one poster at a time. "
                   "Would you like me to open it for you?</p>"
                   '<p class="centered">'
                   '<button class="action" data-sends="yes" data-group="confirmation">Yes</button> &nbsp;'
                   '<button class="action" data-sends="no" data-group="confirmation">Not now</button></p>',
            "existing_poster": pid,
        }, status_code=409)
    return JSONResponse({"ok": True, "redirect": "/posters/create"})


@router.post("/open")
async def open_current_poster(request: Request) -> JSONResponse:
    data = await read_json(request)
    uid = _uid(request)
    answer = data.get("response")
    if "response" in data:
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
            raise HTTPException(422, "Please enter a response of up to 2000 characters.")
        if (clean_response(answer) or {}).get("response") != "yes":
            return JSONResponse({"ok": True, "opened": False})
    pid = await _open_current(request, uid)
    return JSONResponse({
        "ok": True, "opened": True, "redirect": f"/layout/edit?pid={pid}",
        "say": _current_poster_message(request),
    })


def _current_poster_message(request: Request) -> str:
    return f"<p>Here is your current unpurchased poster, {identity.display_name(request)}.</p>"


@router.get("/open")
async def open_current_page(request: Request) -> Response:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    pid = await _open_current(request, _uid(request))
    session = identity.session(request)
    assert session is not None
    session["wikkit_says"] = _current_poster_message(request)
    return RedirectResponse(f"/layout/edit?pid={pid}", status_code=303)


async def _open_current(request: Request, uid: int) -> int:
    if identity.session(request) is None:
        raise HTTPException(503, "Poster editing requires server-side sessions.")
    try:
        pid = await posters.store().unpurchased(uid)
    except psycopg.Error:
        logger.exception("Could not open the current poster")
        raise HTTPException(503, "Your poster could not be opened. Please try again.") from None
    if pid is None:
        raise HTTPException(404, "You do not have an unpurchased poster.")
    _select(request, pid)
    request.state.poster_id = pid
    request.state.poster = {"pid": pid}
    return pid


@router.post("/create")
async def create_poster(request: Request) -> JSONResponse:
    await read_json(request)
    uid = _uid(request)
    if identity.session(request) is None:
        raise HTTPException(503, "Creating a poster requires server-side sessions.")
    try:
        pid, created = await posters.store().new_or_existing(uid)
    except psycopg.Error:
        logger.exception("Could not create poster")
        raise HTTPException(503, "Your poster could not be created. Please try again.") from None
    name = identity.display_name(request)
    _select(request, pid)
    request.state.poster_id = pid
    request.state.poster = {"pid": pid}
    if not created:
        return JSONResponse({
            "ok": True,
            "say": f"<p>I&rsquo;m sorry, {name}! You already have an existing "
                   "unpurchased poster. Here it is.</p>",
            "redirect": f"/layout/edit?pid={pid}",
        })
    return JSONResponse({"ok": True, "redirect": "/content/upload"})


@router.get("/{pid}/open")
async def open_poster(request: Request, pid: int) -> RedirectResponse:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    poster = await _owned(request, pid)
    if poster.purchased:
        return RedirectResponse(f"/posters/{pid}/view", status_code=303)
    _select(request, pid)
    return RedirectResponse("/layout/edit", status_code=303)


@router.post("/{pid}/delete")
async def delete_poster(request: Request, pid: int) -> JSONResponse:
    data = await read_json(request)
    uid = _uid(request)
    if data.get("confirmed") is not True:
        raise HTTPException(422, "Please confirm that you want to delete this poster.")
    return await _delete_confirmed(request, uid, pid)


@router.post("/delete")
async def confirm_delete(request: Request) -> JSONResponse:
    data = await read_json(request)
    uid = _uid(request)
    session = identity.session(request)
    if session is None:
        raise HTTPException(503, "Poster deletion requires server-side sessions.")
    pid = data.get("pid")
    if type(pid) is not int or pid <= 0:
        raise HTTPException(422, "A valid poster ID is required.")
    if "response" not in data:
        try:
            poster = await _owned(request, pid)
        except psycopg.Error:
            logger.exception("Could not load poster %s for deletion", pid)
            raise HTTPException(503, "Your poster could not be loaded. Please try again.") from None
        session["delete_poster_id"] = pid
        return JSONResponse({
            "ok": True, "confirmation_required": True,
            "say": (
                f'<p>Are you sure you want to delete "{html.escape(poster.name)}?"</p>'
                '<p class="centered">'
                '<button class="action" data-sends="yes" data-group="confirmation">Yes</button> &nbsp;'
                '<button class="action" data-sends="no" data-group="confirmation">No</button>'
                "</p>"
            ),
        })
    response = data["response"]
    if not isinstance(response, str) or not response.strip() or len(response) > 2000:
        raise HTTPException(422, "Please enter a response of up to 2000 characters.")
    if session.get("delete_poster_id") != pid:
        raise HTTPException(409, "Please request deletion again before confirming.")
    if (clean_response(response) or {}).get("response") != "yes":
        session.pop("delete_poster_id", None)
        return JSONResponse({
            "ok": True, "deleted": False,
            "say": "<p>Okay, I won&rsquo;t delete your poster.</p>",
        })
    reply = await _delete_confirmed(request, uid, pid)
    session.pop("delete_poster_id", None)
    return reply


async def _delete_confirmed(request: Request, uid: int, pid: int) -> JSONResponse:
    try:
        deleted = await posters.store().delete(uid, pid)
    except psycopg.Error:
        logger.exception("Could not delete poster %s", pid)
        raise HTTPException(503, "Your poster could not be deleted. Please try again.") from None
    if not deleted:
        raise HTTPException(404, "Poster not found")
    session = identity.session(request)
    if session is not None and session.get("poster_id") == pid:
        session.pop("poster_id", None)
    return JSONResponse({
        "ok": True, "deleted": True,
        "say": "<p>Okay, I deleted your poster.</p>",
    })


@router.get("/{pid}/view", response_class=HTMLResponse)
async def view_poster(request: Request, pid: int) -> Response:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    poster = await _owned(request, pid)
    if not poster.purchased:
        return RedirectResponse(f"/posters/{pid}/open", status_code=303)
    return render_page(request, "posters/view.html", "posters-view", poster=poster)


@router.get("/{pid}/preview", response_class=HTMLResponse)
async def preview(request: Request, pid: int) -> Response:
    poster = await _owned(request, pid)
    if poster.dimensions is None:
        return HTMLResponse(
            "<p>Preview unavailable: poster dimensions have not been set.</p>",
            headers={"Cache-Control": "private, no-store"},
        )
    sections, authors = await posters.store().content(_uid(request), pid)
    sections = [(which, Markup(elucidation.clean_html(text))) for which, text in sections]
    width, height = poster.inches
    return templates.TemplateResponse(
        request, "posters/document.html",
        {
            "poster": poster, "sections": sections, "authors": authors,
            "width": width * 96, "height": height * 96,
            "theme": poster.theme if poster.theme in installed_themes() else "",
            "layout": poster.template if poster.template in posters.TEMPLATES else "arena",
        },
        headers={"Cache-Control": "private, no-store"},
    )
