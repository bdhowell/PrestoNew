"""Image upscaling for signed-in users; see upscaler.py for the methods and checks."""

import asyncio
import json
import os
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import upscaler
from routers.wikkit import check_origin

router = APIRouter(prefix="/images", tags=["images"])
# Models hold a lot of memory; queue requests instead of running them side by side.
_busy = asyncio.Semaphore(1)


@router.post("/upscale")
async def upscale(
    request: Request,
    scale: Annotated[float | None, Query(gt=0)] = None,
    print_width: Annotated[float | None, Query(gt=0, description="final printed width in inches")] = None,
    dpi: Annotated[int, Query(gt=0, le=2400)] = 300,
    method: Annotated[str | None, Query()] = None,
    check: bool = True,
    strict: bool = False,
) -> Response:
    """Upscale a PNG or JPEG body; the result's method, SSIM and warnings are in X-Upscale-* headers."""
    if getattr(request.state, "user_id", None) is None:
        raise HTTPException(401, "Please sign in to upscale images.")
    # A non-simple content type forces a preflight for cross-origin callers.
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
        raise HTTPException(415, "Expected application/octet-stream")
    check_origin(request)
    limit = int(os.environ.get("POSTER_UPLOAD_MAX_MB", "32")) * 1024 * 1024
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(413, "The image is too large.")
        data.extend(chunk)
    async with _busy:
        try:
            result = await run_in_threadpool(
                upscaler.upscale, bytes(data), scale=scale, print_width_in=print_width, dpi=dpi,
                method=method, check=check, strict=strict,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except upscaler.UpscaleError as error:
            raise HTTPException(503, str(error)) from error
    summary = result.summary()
    return Response(result.png, media_type="image/png", headers={
        "X-Upscale-Method": result.method,
        "X-Upscale-Scale": str(summary["scale"]),
        "X-Upscale-Size": f"{result.size[0]}x{result.size[1]}",
        "X-Upscale-SSIM": "" if result.ssim is None else str(summary["ssim"]),
        "X-Upscale-Warnings": json.dumps(result.warnings),
    })
