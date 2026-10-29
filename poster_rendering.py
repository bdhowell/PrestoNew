"""Browser-measured typography for poster headers."""

from pathlib import Path
from urllib.parse import urlsplit

from lxml import html as dom
from playwright.async_api import Page, Route, async_playwright

ROOT = Path(__file__).resolve().parent
BALANCE_SCRIPT = (ROOT / "static/javascript/layout-balance.js").read_text()


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
