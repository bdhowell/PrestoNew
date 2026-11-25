"""Section-specific selection and safe persistence of poster source graphics."""

import base64
import html
import math
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

import cv2
import lxml.etree as etree
from lxml import html as dom

import content
from content import GraphicSource
import elucidation
import source_processing as processing


GRAPHIC_TYPES = ("svg", "png", "jpg", "jpeg", "gif", "tables", "math")
GRAPHICS_SAFE_DPI = 150
MATH_SECTIONS = frozenset({"abstract", "introduction", "conclusion", "future plans"})
ASSESSED_SECTIONS = frozenset({"materials and methods", "results"})
RATINGS = ("none", "weakly", "somewhat", "significantly", "highly", "definitively")
PLACEMENTS = frozenset({
    "free", "top-left", "top-center", "top-right", "left-middle", "right-middle",
    "bottom-left", "bottom-center", "bottom-right",
})


def complexity_threshold() -> float:
    try:
        threshold = float(os.environ.get("IMAGE_COMPLEXITY_THRESHOLD", "0.5"))
    except ValueError as error:
        raise ValueError("Image complexity settings must be numbers.") from error
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("IMAGE_COMPLEXITY_THRESHOLD must be between 0 and 1.")
    return threshold


def legibility_threshold() -> float:
    try:
        threshold = float(os.environ.get("DIAGRAM_LEGIBILITY_THRESHOLD", "0.95"))
    except ValueError as error:
        raise ValueError("Diagram legibility settings must be numbers.") from error
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("DIAGRAM_LEGIBILITY_THRESHOLD must be between 0 and 1.")
    return threshold


def image_complexity(data: bytes) -> dict[str, float | bool]:
    """Score a raster image with blurred Canny edge density and an area penalty."""
    threshold = complexity_threshold()
    score = processing.image_complexity_score(data)
    return {"score": score, "threshold": threshold, "too_complex": score > threshold}


def image_legibility(data: bytes, mimetype: str) -> dict[str, float | bool]:
    """Score an image's legibility and compare it with the poster threshold."""
    threshold = legibility_threshold()
    score = processing.image_legibility_score(data, mimetype)
    return {"score": score, "threshold": threshold, "below_threshold": score < threshold}


def qr_png(url: str) -> bytes:
    """Generate a QR code locally without fetching the submitted URL."""
    if not isinstance(url, str) or not url or len(url) > 2048 or not url.isprintable():
        raise ValueError("Enter a valid HTTP or HTTPS URL of at most 2048 characters.")
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError
        if any(character.isspace() for character in url):
            raise ValueError
        parts.port
    except ValueError:
        raise ValueError("Enter a valid HTTP or HTTPS URL of at most 2048 characters.") from None
    try:
        matrix = cv2.QRCodeEncoder.create().encode(url)
        matrix = cv2.copyMakeBorder(matrix, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
        matrix = cv2.resize(matrix, None, fx=16, fy=16, interpolation=cv2.INTER_NEAREST)
        success, encoded = cv2.imencode(".png", matrix)
    except cv2.error as error:
        raise ValueError("The URL is too long to encode as a QR code.") from error
    if not success:
        raise ValueError("The QR code could not be generated.")
    return encoded.tobytes()


def replace_qr(section_html: str, sid: int) -> str:
    """Replace only the QR graphic, retaining surrounding section content."""
    root, nodes = _graphic_nodes(clean_section(section_html))
    for node in nodes:
        if node.get("data-type") == "qrcode":
            node.drop_tree()
    value = (root.text or "") + "".join(
        etree.tostring(child, encoding="unicode", method="html") for child in root
    )
    return value + (
        f'<span class="graphic" contenteditable="false" data-sid="{sid}" '
        f'data-type="qrcode" data-placement="free" data-width="240" style="width:240px">'
        f'<img src="/layout/source?sid={sid}" alt="QR code"></span>'
    )


def _graphic_nodes(value: str) -> tuple[etree._Element, list[etree._Element]]:
    root = dom.fragment_fromstring(value, create_parent=True)
    nodes = root.xpath('.//span[contains(concat(" ", normalize-space(@class), " "), " graphic ")]')
    return root, nodes


def graphic_ids(value: str) -> set[int]:
    _, nodes = _graphic_nodes(value)
    ids = set()
    for node in nodes:
        sid = node.get("data-sid", "")
        if not re.fullmatch(r"[1-9][0-9]{0,9}", sid):
            raise ValueError("A graphic has an invalid source ID.")
        ids.add(int(sid))
    return ids


def clean_section(
    value: str, *, sources: list[GraphicSource] | None = None, section: str | None = None,
) -> str:
    """Keep only constrained graphic markers while applying the normal HTML sanitizer."""
    root, nodes = _graphic_nodes(value)
    if not nodes:
        return elucidation.clean_html(value)
    allowed = None
    if sources is not None:
        if section == "additional information":
            allowed = {item.sid: item for item in sources if item.kind == "qrcode"}
        elif section not in MATH_SECTIONS | ASSESSED_SECTIONS:
            raise ValueError("Graphics are not available for this section.")
        else:
            allowed = {item.sid: item for item in suitable_graphics(section, sources)}
    replacements: dict[str, str] = {}
    for node in nodes:
        if node.getparent() is None or any(parent in nodes for parent in node.iterancestors()):
            raise ValueError("Nested graphics are not supported.")
        sid = node.get("data-sid", "")
        if not re.fullmatch(r"[1-9][0-9]{0,9}", sid):
            raise ValueError("A graphic has an invalid source ID.")
        source = allowed.get(int(sid)) if allowed is not None else None
        if allowed is not None and source is None:
            raise ValueError("This graphic is not available for this poster section.")
        kind = ("qrcode" if source.kind == "qrcode" else source.type) if source is not None else node.get("data-type")
        if kind not in (*GRAPHIC_TYPES, "qrcode"):
            raise ValueError("A graphic has an invalid type.")
        placement = node.get("data-placement", "free")
        if placement not in PLACEMENTS:
            raise ValueError("A graphic has an invalid placement.")
        width = node.get("data-width", "240")
        height = node.get("data-height", "")
        if not re.fullmatch(r"[1-9][0-9]{0,4}", width) or int(width) > 10000:
            raise ValueError("A graphic has an invalid width.")
        if height and (not re.fullmatch(r"[1-9][0-9]{0,4}", height) or int(height) > 10000):
            raise ValueError("A graphic has an invalid height.")
        style = f"width:{width}px" + (f";height:{height}px" if height else "")
        if kind == "math":
            expressions = source.math_expressions if source is not None else [
                child.text_content() for child in node.xpath('./span[@class="graphic-math"]')
            ]
            if source is not None:
                expressions = [rf"\[{expression}\]" for expression in expressions]
            body = "".join(
                f'<span class="graphic-math">{html.escape(expression)}</span>'
                for expression in expressions
            )
        else:
            label = source.filename if source is not None else (
                node.xpath("./img/@alt") or ["Poster graphic"]
            )[0]
            body = f'<img src="/layout/source?sid={sid}" alt="{html.escape(label, quote=True)}">'
        marker = f"POSTERGRAPHIC{uuid4().hex}TOKEN"
        replacements[marker] = (
            f'<span class="graphic" contenteditable="false" data-sid="{sid}" '
            f'data-type="{kind}" data-placement="{placement}" data-width="{width}"'
            + (f' data-height="{height}"' if height else "")
            + f' style="{style}">{body}</span>'
        )
        placeholder = etree.Element("span")
        placeholder.text = marker
        placeholder.tail = node.tail
        node.getparent().replace(node, placeholder)
    serialized = (root.text or "") + "".join(
        etree.tostring(child, encoding="unicode", method="html") for child in root
    )
    result = elucidation.clean_html(serialized)
    for marker, markup in replacements.items():
        result = result.replace(f"&lt;span&gt;{marker}&lt;/span&gt;", markup)
    return result


async def embed_sources(rendered: str, uid: int, pid: int) -> str:
    """Embed owner-scoped graphic bytes for browser renders without a session."""
    document = dom.document_fromstring(rendered)
    images = document.xpath(
        '//span[contains(concat(" ", normalize-space(@class), " "), " graphic ")]/img'
    )
    if not images:
        return rendered
    assets: dict[int, str] = {}
    for image in images:
        match = re.fullmatch(r"/layout/source\?sid=([1-9][0-9]{0,9})", image.get("src", ""))
        if match is None:
            raise ValueError("A poster graphic has an invalid source.")
        sid = int(match.group(1))
        if sid not in assets:
            result = await content.store().graphic_source(uid, pid, sid)
            if result is None:
                raise ValueError("A poster graphic source is unavailable.")
            data, mimetype = result
            assets[sid] = f"data:{mimetype};base64,{base64.b64encode(data).decode('ascii')}"
        image.set("src", assets[sid])
    return etree.tostring(document, encoding="unicode", method="html", doctype="<!DOCTYPE html>")


def suitable_graphics(section: str, graphics: list[GraphicSource]) -> list[GraphicSource]:
    """Return insertable source types in the review panel's order."""
    if section in MATH_SECTIONS:
        return [
            graphic for graphic in graphics
            if graphic.type == "math" and graphic.kind != "qrcode"
        ]
    if section not in ASSESSED_SECTIONS:
        raise ValueError("Graphics are not available for this section.")

    candidates = [
        graphic for graphic in graphics
        if (
            graphic.type in GRAPHIC_TYPES
            and graphic.kind != "qrcode"
            and any(
                graphic.assessments.get(assessed_section, {}).get("rating")
                in RATINGS[RATINGS.index("significantly"):]
                for assessed_section in ASSESSED_SECTIONS
            )
        )
    ]

    def order(graphic: GraphicSource) -> tuple[int, int]:
        assessment = graphic.assessments.get(section, {})
        rating = assessment.get("rating")
        rank = RATINGS.index(rating) if isinstance(rating, str) and rating in RATINGS else -1
        return GRAPHIC_TYPES.index(graphic.type), -rank

    return sorted(candidates, key=order)
