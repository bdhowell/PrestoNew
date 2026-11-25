"""Browser-measured typography for poster headers."""

import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from lxml import etree, html as dom
from playwright.async_api import Page, Route, async_playwright

ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
BALANCE_SCRIPT = (ROOT / "static/javascript/layout-balance.js").read_text()
THUMBNAIL_WIDTH = 640
THUMBNAIL_HEIGHT = 640


async def balance_text_width(page: Page, selector: str) -> float:
    """Balance rendered line widths at CSS-pixel precision, returning a percentage.

    Keep the initial line count and minimize normalized line-width variance.
    The container must initially fill its available, centered layout slot.
    """
    await page.evaluate("""async () => {
        await document.fonts.ready;
        if (Array.from(document.fonts).some(font => font.status === "error")) {
            throw new Error("Poster fonts could not be loaded.");
        }
    }""")
    return await page.locator(selector).evaluate(BALANCE_SCRIPT)


async def balance_header_widths(rendered: str, width: float) -> dict[str, float]:
    """Measure a script-free header at the poster's physical CSS-pixel width."""
    document = dom.document_fromstring(rendered)
    for script in document.xpath("//script"):
        script.getparent().remove(script)
    body = document.find("body")
    if body is None:
        raise ValueError("Poster document has no body")
    headers = document.find_class("poster-header")
    if not headers:
        raise ValueError("Poster document has no header")
    header = headers[0]
    canvases = document.xpath('//*[@id="poster-canvas"]')
    header.getparent().remove(header)
    for child in list(body):
        body.remove(child)
    if canvases:
        canvas = dom.Element("div", attrib=dict(canvases[0].attrib))
        canvas.append(header)
        body.append(canvas)
    else:
        body.append(header)
    body.set("style", f"{body.get('style', '')}; width: {width}px")
    for image in body.xpath(".//img"):
        image.getparent().remove(image)
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
            link.get("rel") == "stylesheet"
            and urlsplit(href).hostname == "fonts.googleapis.com"
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
            page = await browser.new_page(viewport={"width": round(width), "height": 900})
            await page.route("**/*", assets_only)
            await page.set_content(dom.tostring(document, encoding="unicode"), wait_until="load")
            if not await page.evaluate(
                "() => Array.from(document.querySelectorAll('link[rel=stylesheet]')).every(link => link.sheet)"
            ):
                raise ValueError("Poster font stylesheet could not be loaded")
            return {
                selector: await balance_text_width(page, selector)
                for selector in ("#title", "#authors")
            }
        finally:
            await browser.close()


async def poster_thumbnail(rendered: str) -> bytes:
    """Contain a proportionally scaled poster on a contrasting square PNG."""
    document = dom.document_fromstring(rendered)
    for link in document.xpath("//link[@rel='stylesheet']"):
        href = link.get("href", "")
        if href.startswith("/static/"):
            path = (ROOT / href.lstrip("/")).resolve()
            if not path.is_relative_to(ROOT / "static") or path.suffix != ".css":
                raise ValueError("Invalid poster stylesheet")
            style = etree.Element("style")
            style.text = path.read_text()
            link.getparent().replace(link, style)
        else:
            link.getparent().remove(link)

    body = document.find("body")
    if body is None:
        raise ValueError("Poster preview has no body")
    posters = document.xpath(
        ".//article[contains(concat(' ', normalize-space(@class), ' '), ' poster-document ')]"
    )
    poster = posters[0] if posters else None
    frame = etree.Element("div", id="thumbnail-frame")
    if poster is not None:
        poster.getparent().remove(poster)
        frame.append(poster)
    else:
        for child in list(body):
            body.remove(child)
            frame.append(child)
    for child in list(body):
        body.remove(child)
    body.append(frame)
    body.set("style", "margin: 0; width: 640px; height: 640px; overflow: hidden;")
    head = document.find("head")
    if head is None:
        head = etree.Element("head")
        document.insert(document.index(body), head)
    style = etree.SubElement(head, "style")
    style.text = """
        html, body { width: 640px; height: 640px; margin: 0; }
        #thumbnail-frame {
            position: relative; width: 640px; height: 640px; overflow: hidden;
            display: flex; align-items: center; justify-content: center;
            background: #d6d8da; color: #111;
        }
    """

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(
                viewport={"width": THUMBNAIL_WIDTH, "height": THUMBNAIL_HEIGHT},
                device_scale_factor=1,
            )
            await page.set_content(dom.tostring(document, encoding="unicode"), wait_until="load")
            await page.evaluate("""async () => {
                await document.fonts.ready;
                const frame = document.getElementById("thumbnail-frame");
                const poster = frame.querySelector(".poster-document");
                if (!poster) return;
                poster.style.position = "absolute";
                const scale = Math.min(
                    (frame.clientWidth - 24) / poster.offsetWidth,
                    (frame.clientHeight - 24) / poster.offsetHeight,
                );
                poster.style.transformOrigin = "top left";
                poster.style.transform = `scale(${scale})`;
                poster.style.left = `${(frame.clientWidth - poster.offsetWidth * scale) / 2}px`;
                poster.style.top = `${(frame.clientHeight - poster.offsetHeight * scale) / 2}px`;
            }""")
            return await page.locator("#thumbnail-frame").screenshot(type="png", animations="disabled")
        finally:
            await browser.close()


async def poster_pdf(rendered: str, *, logo: str | None = None) -> bytes:
    """Render the owner's saved poster layout as a single print-ready PDF page."""
    document = dom.document_fromstring(rendered)
    canvases = document.xpath('//*[@id="poster-canvas"]')
    canvas = canvases[0] if canvases else None
    print_render = canvas is not None
    if print_render:
        width, height = _editor_canvas_size(canvas)
        container = canvas
        while container.getparent() is not None and container.get("id") != "poster-container":
            container = container.getparent()
        if container.get("id") != "poster-container":
            raise ValueError("Poster layout has no printable container")
        stage = canvas
        while stage.getparent() is not None and stage.get("id") != "poster-stage":
            stage = stage.getparent()
        if stage.get("id") != "poster-stage" or stage.getparent() is not container:
            raise ValueError("Poster layout has no printable stage")
        for sibling in list(document.find("body")):
            if sibling.get("id") != "poster-container":
                sibling.getparent().remove(sibling)
        for sibling in list(container):
            if sibling.get("id") != "poster-stage":
                sibling.getparent().remove(sibling)
        for sibling in list(stage):
            if sibling.get("id") != "poster-canvas":
                sibling.getparent().remove(sibling)
        document.find("body").set("data-print-render", "true")
        container.attrib["style"] = f"display:block;width:{width}px;height:{height}px;overflow:hidden"
        stage.attrib["style"] = f"position:relative;width:{width}px;height:{height}px;margin:0;overflow:hidden"
        canvas.attrib["style"] = (
            f"{canvas.get('style', '')};width:{width}px;height:{height}px;transform:none"
        )
        if logo is not None:
            for image in canvas.xpath('.//img[starts-with(@src, "/layout/logo")]'):
                image.set("src", logo)

    for link in document.xpath("//link[@rel='stylesheet']"):
        href = link.get("href", "")
        url = urlsplit(href)
        if url.scheme == "https" and url.hostname == "fonts.googleapis.com":
            continue
        if not href.startswith("/static/"):
            link.getparent().remove(link)
            continue
        path = (ROOT / href.lstrip("/")).resolve()
        if not path.is_relative_to(STATIC_ROOT) or path.suffix != ".css":
            raise ValueError("Invalid poster stylesheet")
        style = etree.Element("style")
        style.text = path.read_text()
        link.getparent().replace(link, style)

    if print_render:
        for script in document.xpath("//script"):
            src = script.get("src", "")
            if src == "/static/javascript/mathjax.js":
                script.text = (ROOT / "static/javascript/mathjax.js").read_text()
                del script.attrib["src"]
            elif src != "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js":
                script.getparent().remove(script)
        for name in ("layout-geometry", "layout-resize"):
            script = etree.Element("script")
            script.text = (ROOT / f"static/javascript/{name}.js").read_text()
            document.find("body").append(script)
    else:
        posters = document.xpath(
            ".//article[contains(concat(' ', normalize-space(@class), ' '), ' poster-document ')]"
        )
        if not posters:
            raise ValueError("Poster document has no printable canvas")
        try:
            width, height = float(posters[0].get("data-width", "")), float(posters[0].get("data-height", ""))
        except ValueError:
            raise ValueError("Poster document has invalid print dimensions") from None
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise ValueError("Poster document has invalid print dimensions")

    head = document.find("head")
    if head is None:
        raise ValueError("Poster document has no head")
    page_style = etree.SubElement(head, "style")
    page_style.text = (
        f"@page {{ size: {width}px {height}px; margin: 0; }} "
        f"html, body {{ width: {width}px; height: {height}px; margin: 0; }}"
        + (
            " body { display: block; overflow: hidden; background: #fff; } "
            "#poster-container { flex: none; } "
            "body[data-print-render='true'] #poster-container, "
            "body[data-print-render='true'] #poster-container * { visibility: visible; }"
            if print_render else ""
        )
    )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(
                viewport={"width": round(width), "height": round(height)},
                device_scale_factor=1,
            )
            failed_assets: list[str] = []
            page.on("requestfailed", lambda request: failed_assets.append(request.url))
            page.on("response", lambda response: failed_assets.append(response.url) if not response.ok else None)
            await page.set_content(dom.tostring(document, encoding="unicode"), wait_until="load")
            if not await page.evaluate(
                "() => Array.from(document.querySelectorAll('link[rel=stylesheet]')).every(link => link.sheet)"
            ):
                raise ValueError("Poster font stylesheet could not be loaded")
            if print_render:
                await page.evaluate("() => window.posterSizingReady")
            await page.evaluate("""async () => {
                await document.fonts.ready;
                if (Array.from(document.fonts).some(font => font.status === "error")) {
                    throw new Error("Poster fonts could not be loaded.");
                }
                if (window.MathJax?.startup?.promise) await window.MathJax.startup.promise;
            }""")
            if any(
                urlsplit(url).hostname in ("fonts.googleapis.com", "fonts.gstatic.com")
                for url in failed_assets
            ):
                raise ValueError("Poster fonts could not be loaded")
            return await page.pdf(
                prefer_css_page_size=True, print_background=True,
                margin={"top": 0, "right": 0, "bottom": 0, "left": 0},
            )
        finally:
            await browser.close()


def _editor_canvas_size(canvas: etree._Element) -> tuple[float, float]:
    style = canvas.get("style", "")
    dimensions = {}
    for name in ("width", "height"):
        match = re.search(rf"(?:^|;)\s*{name}\s*:\s*([0-9]+(?:\.[0-9]+)?)px", style)
        if match is None:
            raise ValueError(f"Poster canvas has no valid {name}")
        dimensions[name] = float(match.group(1))
    return dimensions["width"], dimensions["height"]
