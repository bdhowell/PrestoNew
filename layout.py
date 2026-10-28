"""Routes for viewing and editing the selected poster layout."""

import logging

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

import identity
import posters
from routers.wikkit import templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/layout", tags=["layout"])


@router.get("")
async def layout_home() -> RedirectResponse:
    return RedirectResponse("/layout/layout", status_code=303)


@router.get("/edit")
async def edit_layout(
    request: Request, pid: int | None = Query(default=None),
) -> Response:
    uid = getattr(request.state, "user_id", None)
    if type(uid) is not int:
        return RedirectResponse("/account/signin", status_code=303)

    selected_pid = pid if pid is not None else getattr(request.state, "poster_id", None)
    if type(selected_pid) is not int:
        return RedirectResponse("/posters/list", status_code=303)

    try:
        poster = await posters.store().get(uid, selected_pid)
    except psycopg.Error:
        logger.exception("Could not load poster layout for poster %s", selected_pid)
        raise HTTPException(503, "Your poster could not be loaded. Please try again.") from None
    if poster is None:
        if pid is not None:
            raise HTTPException(404, "Poster not found")
        return RedirectResponse("/posters/list", status_code=303)
    if poster.purchased:
        return RedirectResponse(f"/posters/{poster.pid}/view", status_code=303)

    if pid is not None:
        session = identity.session(request)
        if session is None:
            raise HTTPException(503, "Poster editing requires server-side sessions.")
        session["poster_id"] = poster.pid

    template = poster.template if poster.template in posters.TEMPLATES else "arena"
    return templates.TemplateResponse(
        request, f"layouts/{template}.html", {"poster": poster}
    )