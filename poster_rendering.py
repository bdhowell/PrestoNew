"""Browser-measured typography for poster headers."""

from pathlib import Path
from urllib.parse import urlsplit

from lxml import etree, html as dom
from playwright.async_api import Page, Route, async_playwright

ROOT = Path(__file__).resolve().parent
BALANCE_SCRIPT = (ROOT / "static/javascript/layout-balance.js").read_text()
THUMBNAIL_WIDTH = 640
THUMBNAIL_HEIGHT = 480


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
    """Render a poster preview into a fixed-size PNG thumbnail."""
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
    body.set("style", "margin: 0; width: 640px; height: 480px; overflow: hidden;")
    head = document.find("head")
    if head is None:
        head = etree.Element("head")
        document.insert(document.index(body), head)
    style = etree.SubElement(head, "style")
    style.text = """
        html, body { width: 640px; height: 480px; margin: 0; }
        #thumbnail-frame {
            position: relative; width: 640px; height: 480px; overflow: hidden;
            display: flex; align-items: center; justify-content: center;
            background: #fff; color: #111;
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
                const scale = Math.min(
                    (frame.clientWidth - 24) / poster.offsetWidth,
                    (frame.clientHeight - 24) / poster.offsetHeight,
                );
                poster.style.position = "absolute";
                poster.style.transformOrigin = "top left";
                poster.style.transform = `scale(${scale})`;
                poster.style.left = `${(frame.clientWidth - poster.offsetWidth * scale) / 2}px`;
                poster.style.top = `${(frame.clientHeight - poster.offsetHeight * scale) / 2}px`;
            }""")
            return await page.locator("#thumbnail-frame").screenshot(type="png", animations="disabled")
        finally:
            await browser.close()
