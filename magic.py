"""Measure sections and graphics in an owner's saved poster layout."""

import logging
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from lxml import html as dom
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Route, async_playwright

import layout
import posters

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/magic", tags=["magic"])
ROOT = Path(__file__).resolve().parent

SECTIONS = [
    "title", "abstract", "introduction", "methods", "results", "conclusion",
    "plans", "citations", "information", "acknowledgements",
]
TOO_LONG = ">"
TOO_SHORT = "<"
JUST_RIGHT = "="
GRAPHICS_SAFE_DPI = 150


def _setting(name: str, default: float, maximum: float) -> float:
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}")
    return value


GRAPHICS_EXTREMELY_TALL = _setting("EXTREMELY_TALL", 0.50, 1)
GRAPHICS_TOO_TALL = _setting("TOO_TALL", 0.33, 1)
GRAPHICS_TOO_WIDE = _setting("TOO_WIDE", 0.33, 1)


def _thresholds() -> dict[str, float]:
    values = {
        "min": _setting("SECTION_MIN_OCCUPIED", 30, 100) / 100,
        "max": _setting("SECTION_MAX_OCCUPIED", 75, 100) / 100,
        "underflow": _setting("SECTION_UNDERFLOW_OCCUPIED", 0.40, 100) / 100,
        "wide": _setting("TOO_WIDE", GRAPHICS_TOO_WIDE, 1),
        "tall": _setting("TOO_TALL", GRAPHICS_TOO_TALL, 1),
        "extremely_tall": _setting("EXTREMELY_TALL", GRAPHICS_EXTREMELY_TALL, 1),
    }
    if values["min"] > values["max"]:
        raise ValueError("SECTION_MIN_OCCUPIED must not exceed SECTION_MAX_OCCUPIED")
    return values


async def _profile_page(page: Page) -> dict:
    thresholds = _thresholds()
    measurements = await page.locator(".section").evaluate_all("""sections => sections.map(section => {
        const bounds = section.getBoundingClientRect();
        const width = section.offsetWidth, height = section.offsetHeight;
        const scaleX = bounds.width / width, scaleY = bounds.height / height;
        return {
            name: section.id,
            width, height, scrollWidth: section.scrollWidth, scrollHeight: section.scrollHeight,
            graphics: Array.from(section.querySelectorAll(".graphic"), graphic => {
                const rect = graphic.getBoundingClientRect();
                return {id: graphic.id, width: rect.width / scaleX, height: rect.height / scaleY};
            }),
        };
    })""")
    if not measurements:
        raise ValueError("Poster layout has no sections")

    sections = {}
    overflowing_sections = []
    underflowing_sections = []
    all_oversized_graphics = []
    total_area = 0.0
    occupied_area = 0.0
    for measurement in measurements:
        name = "conclusion" if measurement["name"] == "conclusions" else measurement["name"]
        width, height = measurement["width"], measurement["height"]
        if not name or name in sections or width <= 0 or height <= 0:
            raise ValueError("Poster sections must have unique IDs and positive dimensions")
        scroll_width, scroll_height = measurement["scrollWidth"], measurement["scrollHeight"]
        if scroll_height <= 0:
            raise ValueError("Poster sections must have a positive scroll height")
        area = width * height
        graphics = []
        oversized_graphics = []
        for item in measurement["graphics"]:
            graphic_width, graphic_height = item["width"], item["height"]
            if not all(math.isfinite(value) and value >= 0 for value in (graphic_width, graphic_height)):
                raise ValueError("Graphic dimensions are invalid")
            width_ratio = graphic_width / width
            height_ratio = graphic_height / height
            too_wide = width_ratio > thresholds["wide"]
            too_tall = height_ratio > thresholds["extremely_tall" if name == "results" else "tall"]
            graphic_area = graphic_width * graphic_height
            percentage = graphic_area / area
            graphic = {
                "id": item["id"], "width": graphic_width, "height": graphic_height,
                "area": graphic_area, "percentage": percentage,
                "overflow": percentage > 1, "underflow": percentage < thresholds["underflow"],
                "too_wide": too_wide, "too_tall": too_tall, "oversized": too_wide or too_tall,
            }
            graphics.append(graphic)
            if graphic["oversized"]:
                oversized_graphics.append({
                    "section": name, "id": item["id"], "width_ratio": width_ratio,
                    "height_ratio": height_ratio, "too_wide": too_wide, "too_tall": too_tall,
                })
        occupied = scroll_width * scroll_height
        density = occupied / area
        graph_area = sum(graphic["area"] for graphic in graphics)
        if density < thresholds["min"]:
            underflowing_sections.append(name)
        if density > thresholds["max"]:
            overflowing_sections.append(name)
        sections[name] = {
            "area": area, "occupied": occupied, "density": density,
            "graph_area": graph_area, "graph_density": graph_area / area,
            "vertical_overflow": height / scroll_height,
            "overflow": density > 1, "underflow": density < thresholds["min"],
            "fit": TOO_SHORT if density < thresholds["min"] else (
                TOO_LONG if density > thresholds["max"] else JUST_RIGHT
            ),
            "oversized_graphics": oversized_graphics, "graphics": graphics,
        }
        total_area += area
        occupied_area += occupied
        all_oversized_graphics.extend(oversized_graphics)
    return {
        "total_area": total_area, "occupied_area": occupied_area,
        "density": occupied_area / total_area, "sections": sections,
        "overflowing_sections": overflowing_sections,
        "underflowing_sections": underflowing_sections,
        "oversized_graphics": all_oversized_graphics,
    }


async def profile_poster(pid: int, *, uid: int) -> dict:
    """Profile saved layout, using an explicitly supplied trusted owner ID."""
    poster = await posters.store().get(uid, pid)
    if poster is None:
        raise HTTPException(404, "Poster not found")
    if poster.width is None or poster.height is None:
        raise HTTPException(409, "Set poster dimensions before profiling it.")
    template = poster.template if poster.template in posters.TEMPLATES else "arena"
    rendered = await layout.layout(template, pid, uid=uid)
    document = dom.document_fromstring(rendered)
    for script in document.xpath("//script"):
        script.getparent().remove(script)
    for link in document.xpath("//link"):
        href = link.get("href", "")
        if link.get("rel") == "stylesheet" and href.startswith("/static/"):
            path = (ROOT / href.lstrip("/")).resolve()
            if not path.is_relative_to(ROOT / "static") or path.suffix != ".css":
                raise ValueError("Invalid poster stylesheet")
            style = dom.Element("style")
            style.text = path.read_text()
            link.getparent().replace(link, style)
        elif not (
            link.get("rel") == "stylesheet" and urlsplit(href).hostname == "fonts.googleapis.com"
        ):
            link.getparent().remove(link)

    async def assets_only(route: Route) -> None:
        url = urlsplit(route.request.url)
        if url.scheme == "https" and url.hostname in ("fonts.googleapis.com", "fonts.gstatic.com"):
            await route.continue_()
        else:
            await route.abort()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.route("**/*", assets_only)
            await page.set_content(dom.tostring(document, encoding="unicode"), wait_until="load")
            await page.evaluate("""async () => {
                await document.fonts.ready;
                if (Array.from(document.fonts).some(font => font.status === "error") ||
                    !Array.from(document.querySelectorAll("link[rel=stylesheet]")).every(link => link.sheet)) {
                    throw new Error("Poster fonts could not be loaded.");
                }
                window.fetch = async (url, options) => {
                    if (url !== "/layout/geometry") throw new Error("Unexpected profile request");
                    return {ok: true, json: async () => ({ok: true, geometry: JSON.parse(options.body).geometry})};
                };
            }""")
            for name in ("layout-zoom.js", "layout-geometry.js"):
                await page.add_script_tag(path=str(ROOT / "static/javascript" / name))
            await page.evaluate("() => window.posterGeometryReady")
            await page.add_script_tag(path=str(ROOT / "static/javascript/layout-resize.js"))
            await page.wait_for_selector(".resize-overlay", state="attached")
            return await _profile_page(page)
        finally:
            await browser.close()


@router.get("/{pid}/profile")
async def poster_profile(request: Request, response: Response, pid: int) -> dict:
    uid = layout.uid_for(request)
    try:
        profile = await profile_poster(pid, uid=uid)
    except (psycopg.Error, PlaywrightError, OSError, ValueError):
        logger.exception("Could not profile poster %s", pid)
        raise HTTPException(503, "Your poster could not be profiled.") from None
    response.headers.update(layout.PRIVATE)
    return profile
