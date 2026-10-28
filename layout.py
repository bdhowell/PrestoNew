"""Routes for viewing and editing the selected poster layout."""

import logging
import html
import math
import re
from dataclasses import dataclass

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from lxml import etree
from lxml import html as dom

import content
import elucidation
import identity
import posters
from common import clean_response
from routers import wikkit
from routers.content import STORAGE_ERRORS
from routers.wikkit import read_json, render_page, templates
from wikkit import Chatbot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/layout", tags=["layout"])
PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
SCALE = {"cm": 1.0, "mm": 0.1, "in": 2.54}
ALIASES = {
    "methods": "materials and methods", "conclusions": "conclusion",
    "citations": "literature cited", "information": "additional information",
}


def uid_for(request: Request) -> int:
    uid = getattr(request.state, "user_id", None)
    if type(uid) is not int:
        raise HTTPException(401, "Please sign in to access your poster.")
    return uid


async def owned(request: Request, pid: int | None = None, *, draft: bool = False) -> posters.Poster:
    uid = uid_for(request)
    session = identity.session(request)
    selected = pid if pid is not None else (session.get("poster_id") if session is not None else None)
    if type(selected) is not int:
        raise HTTPException(409, "Please create or open a poster first.")
    poster = await posters.store().get(uid, selected)
    if poster is None:
        raise HTTPException(404, "Poster not found")
    if draft and poster.purchased:
        raise HTTPException(409, "Purchased posters cannot be edited.")
    return poster


def append_html(element: etree._Element, value: str) -> None:
    for fragment in dom.fragments_fromstring(value):
        if isinstance(fragment, str):
            if len(element):
                element[-1].tail = (element[-1].tail or "") + fragment
            else:
                element.text = (element.text or "") + fragment
        else:
            element.append(fragment)


async def layout(template: str, pid: int, *, uid: int, request: Request | None = None) -> str:
    """Render a fresh template, using the trusted owner's ID for all database reads."""
    if template not in posters.TEMPLATES:
        raise ValueError("Unknown poster template")
    poster = await posters.store().get(uid, pid)
    if poster is None:
        raise HTTPException(404, "Poster not found")
    sections, authors = await posters.store().content(uid, pid)
    values = {which: elucidation.clean_html(value) for which, value in sections}
    values["authors"] = ", ".join(
        html.escape(name).replace(" ", "&nbsp;") + (f"&nbsp;({html.escape(affiliation)})" if affiliation else "")
        for name, affiliation in authors
    )
    values["author"] = values["authors"]
    if request is None:
        request = Request({"type": "http", "headers": [], "state": {"user_id": uid}})
    raw = templates.env.get_template(f"layouts/{template}.html").render(
        request=request, poster=poster, layout_template=template,
        theme=poster.theme if poster.theme in posters.installed_themes() else "",
        fonts=poster.fonts if poster.fonts in posters.installed_fonts() else "",
    )
    document = dom.document_fromstring(raw)
    if poster.logo:
        logo = document.get_element_by_id("logo")
        etree.SubElement(logo, "img", src=f"/layout/logo?pid={pid}", alt="Poster logo")
    for pane in document.xpath('//*[@data-sections or @data-section]'):
        groups = pane.get("data-sections", pane.get("data-section", "")).split(",")
        for group in groups:
            items = [item.strip() for item in group.split("|") if item.strip()]
            if not items:
                continue
            parent = pane
            vertical = "vertical" in pane.get("class", "").split()
            if len(items) > 1:
                parent = etree.SubElement(
                    pane, "div", id=f"{'-'.join(items)}-parent",
                    attrib={"class": "section-parent vertical" if vertical else "section-parent"},
                )
            for item in items:
                value = values.get(ALIASES.get(item, item), "")
                if item in ("title", "author", "authors"):
                    append_html(parent, value)
                    continue
                section = etree.SubElement(
                    parent, "div", id=item,
                    attrib={"class": "section vertical" if vertical else "section"},
                )
                heading = etree.SubElement(section, "div", attrib={"class": "header"})
                heading.text = item.title()
                body = etree.SubElement(section, "div", attrib={"class": "content"})
                append_html(body, value)
                # Elucidation stores its own heading; the layout supplies it instead.
                if len(body) and body[0].tag in ("h2", "h3"):
                    body.text = (body.text or "") + (body[0].tail or "")
                    body.remove(body[0])
    return dom.tostring(document, encoding="unicode", doctype="<!DOCTYPE html>")


@router.get("")
async def layout_home() -> RedirectResponse:
    return RedirectResponse("/layout/layout", status_code=303)


@router.get("/edit")
@router.get("/layout")
async def edit_layout(
    request: Request, pid: int | None = Query(default=None),
) -> Response:
    uid = getattr(request.state, "user_id", None)
    if type(uid) is not int:
        return RedirectResponse("/account/signin", status_code=303)

    session = identity.session(request)
    selected_pid = pid if pid is not None else (
        session.get("poster_id") if session is not None else None
    )
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
    try:
        rendered = await layout(template, poster.pid, uid=uid, request=request)
    except psycopg.Error:
        logger.exception("Could not render poster %s", poster.pid)
        raise HTTPException(503, "Your layout could not be loaded. Please try again.") from None
    return HTMLResponse(rendered, headers=PRIVATE)


@router.get("/qrcode")
async def qrcode(request: Request, pid: int = Query(gt=0)) -> Response:
    try:
        poster = await owned(request, pid)
    except psycopg.Error:
        logger.exception("Could not load QR code for poster %s", pid)
        raise HTTPException(503, "Your QR code could not be loaded.") from None
    if not poster.moreinfopng:
        raise HTTPException(404, "Poster QR code not found")
    return Response(bytes(poster.moreinfopng), media_type="image/png", headers=PRIVATE)


@router.get("/logo")
async def logo(request: Request, pid: int = Query(gt=0)) -> Response:
    try:
        poster = await owned(request, pid)
    except psycopg.Error:
        logger.exception("Could not load logo for poster %s", pid)
        raise HTTPException(503, "Your logo could not be loaded.") from None
    if not poster.logo:
        raise HTTPException(404, "Poster logo not found")
    media_type = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml",
        "image/png": "image/png", "image/jpeg": "image/jpeg", "image/svg+xml": "image/svg+xml",
    }.get(poster.logotype or "")
    if media_type is None:
        logger.error("Unsupported logo type for poster %s: %r", pid, poster.logotype)
        raise HTTPException(415, "Unsupported poster logo type")
    return Response(bytes(poster.logo), media_type=media_type, headers={
        **PRIVATE, "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
    })


@router.get("/source")
async def source(request: Request, sid: int = Query(gt=0)) -> Response:
    try:
        result = await content.store().source_file(uid_for(request), sid)
    except FileNotFoundError:
        raise HTTPException(404, "Source file not found") from None
    except STORAGE_ERRORS:
        logger.exception("Could not load source file %s", sid)
        raise HTTPException(503, "Your source file could not be loaded.") from None
    if result is None:
        raise HTTPException(404, "Source file not found")
    data, media_type = result
    return Response(data, media_type=media_type, headers={
        **PRIVATE, "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
    })


def number(value: object) -> float | None:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) and result > 0 else None


@dataclass(frozen=True)
class DimensionRules:
    name: str
    units: str
    required_width: float | None
    required_height: float | None
    max_width: float | None
    max_height: float | None

    @classmethod
    def from_details(cls, name: str, details: dict) -> "DimensionRules":
        unit = str(details.get("poster_units") or details.get("units") or "cm").lower()
        unit = {"inches": "in", "inch": "in", "centimeters": "cm", "millimeters": "mm"}.get(unit, unit)
        if unit not in SCALE:
            raise ValueError("Conference poster units must be cm, mm, or in.")
        values = []
        for kind in ("required", "maximum"):
            width = number(details.get(f"{kind}_poster_width"))
            height = number(details.get(f"{kind}_poster_height"))
            dimensions = details.get(f"{kind}_poster_dimensions")
            if isinstance(dimensions, str):
                match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)", dimensions.strip())
                if match:
                    width = width or number(match[1])
                    height = height or number(match[2])
            values.extend(value * SCALE[unit] if value else None for value in (width, height))
        return cls(name, unit, *values)

    @property
    def fixed(self) -> bool:
        return self.required_width is not None and self.required_height is not None

    def errors(self, width: float, height: float) -> list[str]:
        errors = []
        for label, value, minimum, required, maximum in (
            ("width", width, posters.POSTER_MIN_WIDTH_CM, self.required_width, self.max_width),
            ("height", height, posters.POSTER_MIN_HEIGHT_CM, self.required_height, self.max_height),
        ):
            if not math.isfinite(value) or value <= 0:
                errors.append(f"Poster {label} must be a positive finite number.")
            elif required is not None and not math.isclose(value, required, abs_tol=0.001):
                errors.append(f"Poster {label} must match the conference requirement.")
            elif value < minimum and required is None:
                errors.append(f"Your poster {label} is too small. Nobody will be able to read it.")
            if maximum is not None and value > maximum + 0.001:
                errors.append(f"Poster {label} exceeds the maximum allowed.")
            if value > 609.6:
                errors.append(f"Poster {label} must not exceed 609.6 cm.")
        return errors


def parse_dimensions(text: str, default_units: str) -> tuple[float, float, str]:
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(cm|mm|in)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|in)?\s*",
        text, re.IGNORECASE,
    )
    if match is None:
        raise ValueError("Enter height x width, with units cm, mm, or in (for example, 36 in x 48 in).")
    first, second = match[2], match[4]
    units = (second or first or default_units).lower()
    height = float(match[1]) * SCALE[(first or units).lower()]
    width = float(match[3]) * SCALE[(second or units).lower()]
    if not all(math.isfinite(value) for value in (width, height)):
        raise ValueError("Poster dimensions must be finite numbers.")
    return width, height, units


async def rules_for(uid: int, pid: int) -> DimensionRules:
    name, details = await posters.store().conference_details(uid, pid)
    return DimensionRules.from_details(name, details)


def dimensions_payload(width: float, height: float, units: str) -> dict[str, object]:
    return {"width": width / SCALE[units], "height": height / SCALE[units], "units": units}


async def save_dimensions(uid: int, pid: int, width: float, height: float, units: str) -> None:
    if not await posters.store().set_dimensions(uid, pid, width, height, units):
        raise HTTPException(409, "This poster can no longer be edited.")


@router.get("/dimensions")
async def dimensions_page(request: Request) -> Response:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    try:
        poster = await owned(request)
    except psycopg.Error:
        logger.exception("Could not load poster dimensions")
        raise HTTPException(503, "Your poster could not be loaded.") from None
    if poster.purchased:
        return RedirectResponse(f"/posters/{poster.pid}/view", status_code=303)
    return render_page(request, "layout/dimensions.html", "layout-dimensions", poster=poster)


@router.post("/dimensions")
async def update_dimensions(request: Request) -> JSONResponse:
    data = await read_json(request)
    text = data.get("dimensions")
    if not isinstance(text, str) or len(text) > 100:
        raise HTTPException(422, "Enter height x width and units.")
    try:
        poster = await owned(request, draft=True)
        rules = await rules_for(uid_for(request), poster.pid)
        width, height, units = parse_dimensions(text, rules.units if rules.name else poster.units)
        errors = rules.errors(width, height)
        payload = dimensions_payload(width, height, units)
        if errors:
            return JSONResponse({"ok": False, "errors": errors, "preview": payload}, status_code=422)
        await save_dimensions(uid_for(request), poster.pid, width, height, units)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except psycopg.Error:
        logger.exception("Could not save poster dimensions")
        raise HTTPException(503, "Your poster dimensions could not be saved.") from None
    return JSONResponse({"ok": True, "preview": payload, "redirect": "/layout/edit"})


async def dimensions_workflow(bot: Chatbot) -> None:
    uid = getattr(bot.websocket.state, "user_id", None)
    pid = getattr(bot.websocket.state, "poster_id", None)
    if type(uid) is not int or type(pid) is not int:
        await bot.send({"say": "<p>Please create or open a poster first.</p>"})
        return
    try:
        poster = await posters.store().get(uid, pid)
        if poster is None or poster.purchased:
            await bot.send({"say": "<p>This poster is not available for editing.</p>"})
            return
        rules = await rules_for(uid, pid)
        name = identity.display_name(bot.websocket)
        conference = html.escape(rules.name)
        if rules.fixed:
            width, height = rules.required_width, rules.required_height
            assert width is not None and height is not None
            errors = rules.errors(width, height)
            if errors:
                raise ValueError(" ".join(errors))
            await save_dimensions(uid, pid, width, height, rules.units)
            await bot.request_response(
                f"<p>Okay, {name}, {conference} requires a poster height of "
                f"{height / SCALE[rules.units]:g} {rules.units} and width of "
                f"{width / SCALE[rules.units]:g} {rules.units}.</p>"
                "<p>I&rsquo;ve set your poster dimensions to the required values. "
                "Let me know when you&rsquo;re ready to proceed.</p>"
                '<button class="action" data-sends="ok">I&rsquo;m ready</button>'
            )
        else:
            width = height = None
            if rules.max_width is not None and rules.max_height is not None:
                reply = await bot.request_response(
                    f"<p>Okay, {name}, {conference} has a maximum poster height of "
                    f"{rules.max_height / SCALE[rules.units]:g} {rules.units} and width of "
                    f"{rules.max_width / SCALE[rules.units]:g} {rules.units}.</p>"
                    "<p>Do you want to set your poster dimensions to these maximum values? "
                    "You can always change them later.</p>"
                    '<button class="action" data-sends="ok">Set the dimensions to maximum</button>'
                    '<button class="action" data-sends="cancel">I want to set my dimensions</button>'
                )
                if (clean_response(reply) or {}).get("response") in ("yes", "idk"):
                    width, height = rules.max_width, rules.max_height
            prompt = (
                f"<p>So what are the dimensions of your poster, {name}? "
                "Enter height x width in cm, mm, or in. You can always change them later.</p>"
            )
            while True:
                units = rules.units
                if width is None or height is None:
                    reply = await bot.request_response(prompt)
                    if (clean_response(reply) or {}).get("response") == "idk":
                        if rules.max_width is None or rules.max_height is None:
                            prompt = "<p>No maximum dimensions are available. Please enter height x width and units.</p>"
                            continue
                        width, height = rules.max_width, rules.max_height
                        await bot.send({"say": "<p>I&rsquo;ll use the maximum dimensions. You can adjust them later.</p>"})
                    else:
                        try:
                            width, height, units = parse_dimensions(
                                reply, rules.units if rules.name else poster.units,
                            )
                        except ValueError as error:
                            prompt = f"<p>{html.escape(str(error))}</p>"
                            await bot.send({"command": {"does": "correct-dimensions", "payload": None}})
                            continue
                errors = rules.errors(width, height)
                if not errors:
                    await save_dimensions(uid, pid, width, height, units)
                    await bot.send({"say": "<p>Done!</p>"})
                    break
                prompt = "<p>" + " ".join(errors) + "</p>"
                await bot.send({"command": {
                    "does": "correct-dimensions", "payload": dimensions_payload(width, height, units),
                }})
                width = height = None
        await bot.send({"command": {"does": "redirect", "payload": "/layout/edit"}})
    except (psycopg.Error, ValueError, HTTPException):
        logger.exception("Could not set dimensions for poster %s", pid)
        await bot.send({"say": "<p>Your poster dimensions could not be saved. Please reload and try again.</p>"})


wikkit.WORKFLOWS["layout-dimensions"] = dimensions_workflow