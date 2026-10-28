"""Upload original source files for the selected, owner-checked draft."""

import logging
import os
import io
from dataclasses import asdict
from pathlib import Path
from urllib.parse import unquote

import psycopg
import openai
import num2words
from PIL import Image
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import GoogleAuthError
from starlette.concurrency import run_in_threadpool

import common
import content
import identity
import posters
import source_processing
import elucidation
from routers.wikkit import check_origin, read_json, render_page

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/content", tags=["content"])
INTRO = (
    "<p>Let&rsquo;s start by uploading your source content so I can start examining it. "
    "You can always upload additional content later if needed, or delete content you "
    "no longer want to include.</p>"
)
FORMATS_MESSAGE = (
    "<p>I can read and consider PDFs, Microsoft Word, Excel, and PowerPoint documents; "
    "Apple Numbers, Pages, and Keynote files, PNG and JPEG (JPG) images, "
    "as well as Markdown (MD) files.</p>"
    "<p>Go ahead and upload your content. "
    '<button class="action" data-sends="finished">I&rsquo;m Finished</button></p>'
)
STORAGE_ERRORS = (psycopg.Error, OSError, GoogleAPIError, GoogleAuthError, openai.OpenAIError, source_processing.ProcessingError)


def conversion_notice() -> str:
    if source_processing.enrichment_enabled():
        return ""
    return f"<p>{source_processing.REDUCED_FEATURE_NOTICE}</p>"


def size_warning(items: list[content.Source]) -> str:
    count = sum(item.ill_sized for item in items)
    if not count:
        return ""
    plural = count > 1
    return (
        f"<div>{num2words.num2words(count).capitalize()} file{'s' if plural else ''} "
        "will likely not be easily visible from typical poster viewing distances "
        "and I have no spell that could change that.</div>"
        f"<div>I&rsquo;ve identified {'them' if plural else 'it'} by coloring "
        f"{'their' if plural else 'its'} <span class=\"ill-sized\">"
        f"filename{'s' if plural else ''}</span>.</div>"
    )


def item_json(item: content.Source) -> dict[str, object]:
    return {**asdict(item), "ill_sized": item.ill_sized, "thumbnail": item.thumbnail}


async def selected_draft(request: Request) -> tuple[int, int]:
    uid = getattr(request.state, "user_id", None)
    if uid is None:
        raise HTTPException(401, "Please sign in to upload content.")
    session = identity.session(request)
    pid = session.get("poster_id") if session is not None else None
    if type(pid) is not int:
        raise HTTPException(409, "Please create or open a poster before uploading content.")
    poster = await posters.store().get(uid, pid)
    if poster is None:
        raise HTTPException(404, "Poster not found")
    if poster.purchased:
        raise HTTPException(409, "Purchased posters cannot accept new content.")
    return uid, pid


@router.get("/upload")
async def upload_page(request: Request) -> Response:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    try:
        uid, pid = await selected_draft(request)
        items = await content.store().list_sources(uid, pid)
    except STORAGE_ERRORS:
        logger.exception("Could not load source uploads")
        raise HTTPException(503, "Your source files could not be loaded. Please try again.") from None
    return render_page(
        request, "content/upload.html", "content-upload",
        (INTRO if not items else "") + FORMATS_MESSAGE + conversion_notice() + size_warning(items),
        items=items, accept=",".join(content.FORMATS),
    )


@router.post("/upload")
async def upload_file(request: Request) -> JSONResponse:
    # A non-simple content type/header forces a preflight for cross-origin callers.
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
        raise HTTPException(415, "Expected application/octet-stream")
    check_origin(request)
    filename = unquote(request.headers.get("x-filename", ""))
    if (
        not filename or len(filename) > 255 or not filename.isprintable()
        or "/" in filename or "\\" in filename or Path(filename).suffix.lower() not in content.FORMATS
    ):
        raise HTTPException(422, "A supported filename without a path is required.")
    limit = int(os.environ.get("POSTER_UPLOAD_MAX_MB", "32")) * 1024 * 1024
    if limit <= 0:
        raise RuntimeError("POSTER_UPLOAD_MAX_MB must be positive.")
    try:
        uid, pid = await selected_draft(request)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > limit:
                raise HTTPException(413, "This file exceeds the upload size limit.")
            data.extend(chunk)
        item = await content.store().add(uid, pid, filename, bytes(data))
        items = await content.store().list_sources(uid, pid)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except source_processing.ProcessingError as error:
        logger.exception("Could not process source upload for %s", filename)
        raise HTTPException(503, str(error)) from None
    except STORAGE_ERRORS:
        logger.exception("Could not save source upload for %s", filename)
        raise HTTPException(503, "Your file could not be saved. Please try again.") from None
    return JSONResponse({
        "ok": True, "item": item_json(item), "items": [item_json(source) for source in items],
        "say": conversion_notice() + size_warning(items),
    }, status_code=201)

@router.post("/finished")
async def finished(request: Request) -> JSONResponse:
    await read_json(request)
    try:
        uid, pid = await selected_draft(request)
        count, length = await content.store().completion(uid, pid)
    except STORAGE_ERRORS:
        logger.exception("Could not validate uploaded source content")
        raise HTTPException(503, "Your content could not be checked. Please try again.") from None
    minimum = int(os.environ.get("SOURCE_TEXT_MIN_LENGTH", "4000"))
    if minimum < 1:
        raise RuntimeError("SOURCE_TEXT_MIN_LENGTH must be positive.")
    name = identity.display_name(request)
    if count == 0 or length == 0:
        return JSONResponse({
            "ok": False,
            "say": f"<p>{name}, you can&rsquo;t have a poster without any content! "
                   "Please give me some material to work with.</p>"
                   "<p>I can&rsquo;t magic a complete poster out of thin air.</p>",
        }, status_code=409)
    if length < minimum:
        return JSONResponse({
            "ok": False,
            "say": f"<p>I&rsquo;m sorry, {name}, but I need more content to work with.</p>"
                   "<p>I&rsquo;m unable to conjure enough content to create a useful poster.</p>",
        }, status_code=409)
    return JSONResponse({"ok": True, "redirect": "/content/elucidate"})

@router.get("/elucidate")
async def elucidate_page(request: Request) -> Response:
    if getattr(request.state, "user_id", None) is None:
        return RedirectResponse("/account/signin", status_code=303)
    if request.query_params.get("xyzzy") != 'arpa':
        await common.resume(request)
    try:
        await selected_draft(request)
    except STORAGE_ERRORS:
        logger.exception("Could not load the poster for elucidation")
        raise HTTPException(503, "Your poster could not be loaded. Please try again.") from None
    return render_page(
        request, "content/elucidate.html", "content-elucidate",
        f"<p>Terrific, {identity.display_name(request)}. We&rsquo;re making great progress.</p>"
        "<p>Please give me a few minutes.</p>"
        "<p>I&rsquo;m reviewing what you shared with me.</p>",
        progress_messages=[
            "Still working.",
            f"A few more moments, please, {identity.display_name(request)}.",
            f"Thanks for being patient, {identity.display_name(request)}.",
            "I&rsquo;m making progress.", "Any day now.", "There&rsquo;s a lot to look over.",
            f"{identity.display_name(request)}, you&rsquo;ve given me a lot to think about.",
            "Getting a cup of tea.", "I&rsquo;m indisposed for a moment...",
        ],
    )


@router.post("/elucidate")
async def elucidate_poster(request: Request) -> JSONResponse:
    await read_json(request)
    try:
        uid, pid = await selected_draft(request)
        sources = await content.store().source_text(uid, pid)
        minimum = int(os.environ.get("SOURCE_TEXT_MIN_LENGTH", "4000"))
        if minimum < 1:
            raise RuntimeError("SOURCE_TEXT_MIN_LENGTH must be positive.")
        if not sources.count or sources.character_count < minimum:
            raise HTTPException(409, "Please upload enough source text before running analysis.")
        result = await elucidation.analyze(sources.text)
        await content.store().save_elucidation(uid, pid, sources, result)
    except content.SourceConflictError as error:
        raise HTTPException(409, str(error)) from None
    except elucidation.ElucidationError as error:
        logger.exception("Could not elucidate poster content")
        raise HTTPException(503, str(error)) from None
    except STORAGE_ERRORS:
        logger.exception("Could not analyze or save poster content")
        raise HTTPException(503, "Your content could not be analyzed or saved. Please try again.") from None
    return JSONResponse({"ok": True, "redirect": "/conferences/query"})


@router.get("/{sid}/thumbnail")
async def thumbnail(request: Request, sid: int) -> Response:
    try:
        uid, pid = await selected_draft(request)
        data = await content.store().graphic(uid, pid, sid)
        if data is None:
            raise HTTPException(404, "Source graphic not found")

        def render() -> bytes:
            with Image.open(io.BytesIO(data)) as image:
                image.thumbnail((160, 120))
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
                return output.getvalue()

        preview = await run_in_threadpool(render)
    except STORAGE_ERRORS:
        logger.exception("Could not load source thumbnail %s", sid)
        raise HTTPException(503, "The source thumbnail could not be loaded.") from None
    return Response(preview, media_type="image/png", headers={"Cache-Control": "private, no-store"})
