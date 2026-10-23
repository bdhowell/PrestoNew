"""Poster listing, creation, deletion, and read-only previews."""

import html
import logging
import random
import re
from typing import Literal

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import identity
import posters
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


@router.post("/new")
async def new_poster(request: Request) -> JSONResponse:
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
    if not created:
        return JSONResponse({
            "ok": False,
            "say": f"<p>I&rsquo;m sorry, {name}, you can work on only one poster at a time. "
                   "Would you like me to open it for you?</p>",
            "existing_poster": pid,
        }, status_code=409)
    _select(request, pid)
    return JSONResponse({
        "ok": True, "redirect": "/posters/create",
        "say": f"<p>I&rsquo;m creating your new poster, {name}!</p>",
    })


@router.get("/create", response_class=HTMLResponse)
async def create_page(request: Request) -> Response:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    session = identity.session(request)
    pid = session.get("poster_id") if session is not None else None
    if type(pid) is not int:
        return RedirectResponse("/posters/list", status_code=303)
    poster = await _owned(request, pid)
    if poster.purchased:
        return RedirectResponse("/posters/list", status_code=303)
    width, height = poster.inches
    await posters.store().initialize(
        _uid(request), pid,
        "vertical" if height > width else random.choice(("arena", "central")),
        random.choice(installed_themes()),
    )
    poster = await _owned(request, pid)
    return render_page(
        request, "posters/create.html", "posters-create", poster=poster,
    )


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
            "say": f'<p>Are you sure you want to delete "{html.escape(poster.name)}"?</p>',
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
    sections, authors = await posters.store().content(_uid(request), pid)
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
