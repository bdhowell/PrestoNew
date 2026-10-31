"""Explicitly enabled, direct-local development maintenance."""

import ipaddress
import logging
import os
from pathlib import Path
import shutil
from typing import Annotated
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import GoogleAuthError
from PIL import Image
from psycopg import sql
from starlette.concurrency import run_in_threadpool

import content
import identity
import posters
import source_processing
from routers.wikkit import read_json, render_page

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/system", tags=["system"])


def require_local(request: Request) -> None:
    if os.environ.get("SYSTEM_PURGE_ENABLED") != "true":
        raise HTTPException(403, "Local system purging is disabled.")
    if any(
        name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
        for name in request.headers
    ):
        raise HTTPException(403, "System purging cannot be accessed through a proxy.")
    try:
        local_client = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local_client = False
    hosts = request.headers.getlist("host")
    try:
        host = urlsplit("//" + hosts[0]).hostname if len(hosts) == 1 else None
    except ValueError:
        host = None
    if not local_client or host not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "System purging requires a direct localhost connection.")


def source_directories() -> list[Path]:
    files = content.SourceFiles()
    if files.bucket:
        raise HTTPException(409, "System purging is unavailable with cloud source storage.")
    root = files.root
    repository = Path(__file__).resolve().parent.parent
    if root in (Path.home(), *Path.home().parents, repository, *repository.parents):
        raise HTTPException(409, "The configured source directory is unsafe to purge.")
    directories = []
    for shard in range(10):
        path = root / str(shard)
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise HTTPException(409, f"Source subdirectory {shard} is not a regular directory.")
        if path.exists():
            directories.append(path)
    return directories


def remove_directories(directories: list[Path]) -> None:
    for path in directories:
        if path.is_symlink() or path.resolve() != path:
            raise OSError("A source subdirectory changed during purging.")
        shutil.rmtree(path)


class SystemStore(posters.PosterStore):
    async def catalog(self, pid: int) -> list[content.Source] | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute("SELECT pid FROM poster WHERE pid = %s", (pid,))
            if await cursor.fetchone() is None:
                return None
            cursor = await connection.execute(
                "SELECT " + content.SourceStore._SOURCE_COLUMNS +
                " FROM source s LEFT JOIN source parent ON parent.sid = s.parent "
                "WHERE s.pid = %s ORDER BY s.added, s.sid", (pid,),
            )
            return [content.Source(*row) for row in await cursor.fetchall()]

    async def catalog_graphic(self, pid: int, sid: int) -> tuple[bytes, str] | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT storage_uri, mimetype FROM source WHERE pid = %s AND sid = %s "
                "AND (mimetype LIKE 'image/%%' OR kind IN "
                "('svg', 'image', 'chart', 'logo', 'moreinfo', 'qrcode'))", (pid, sid),
            )
            row = await cursor.fetchone()
        if row is None or not row[0]:
            return None
        return await run_in_threadpool(content.SourceFiles().read, row[0]), row[1]

    async def purge(self, *, users: bool = False) -> dict[str, int]:
        connection = await self._connect()
        async with connection:
            await connection.execute("LOCK TABLE poster, conference IN EXCLUSIVE MODE")
            cursor = await connection.execute("SELECT to_regclass(%s)", (f"{self.schema}.poster_memory",))
            row = await cursor.fetchone()
            if row is not None and row[0] is not None:
                await connection.execute(
                    sql.SQL("DELETE FROM {}").format(sql.Identifier(self.schema, "poster_memory")),
                )
            cursor = await connection.execute("DELETE FROM poster")
            poster_count = cursor.rowcount
            cursor = await connection.execute("DELETE FROM conference")
            conference_count = cursor.rowcount
            user_count = 0
            if users:
                cursor = await connection.execute(
                    "SELECT to_regclass(%s)", (f"{self.schema}.user_memory",),
                )
                row = await cursor.fetchone()
                if row is not None and row[0] is not None:
                    await connection.execute(
                        sql.SQL("DELETE FROM {}").format(sql.Identifier(self.schema, "user_memory")),
                    )
                cursor = await connection.execute('DELETE FROM "user"')
                user_count = cursor.rowcount
        return {"posters": poster_count, "conferences": conference_count, "users": user_count}


_store = SystemStore()


@router.get("/catalog")
async def catalog(
    request: Request, pid: Annotated[int | None, Query(ge=1, le=2147483647)] = None,
) -> Response:
    require_local(request)
    items = None
    if pid is not None:
        try:
            items = await _store.catalog(pid)
        except psycopg.Error:
            logger.exception("Could not load source catalog for poster %s", pid)
            raise HTTPException(503, "The source catalog could not be loaded.") from None
        if items is None:
            raise HTTPException(404, "Poster not found")
    response = render_page(
        request, "system/catalog.html", "system-catalog", pid=pid, items=items,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/catalog/{pid}/{sid}/thumbnail")
async def catalog_thumbnail(request: Request, pid: int, sid: int) -> Response:
    require_local(request)
    try:
        graphic = await _store.catalog_graphic(pid, sid)
        if graphic is None:
            raise HTTPException(404, "Source graphic not found")
        data, mimetype = graphic
        if mimetype != "image/svg+xml":
            data = await run_in_threadpool(source_processing.image_thumbnail, data)
            mimetype = "image/png"
    except (psycopg.Error, OSError, GoogleAPIError, GoogleAuthError, Image.DecompressionBombError):
        logger.exception("Could not load catalog thumbnail %s for poster %s", sid, pid)
        raise HTTPException(503, "The source thumbnail could not be loaded.") from None
    return Response(data, media_type=mimetype, headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    })


@router.post("/purge")
async def purge(request: Request) -> JSONResponse:
    require_local(request)
    data = await read_json(request)
    if data.get("confirm") != "purge":
        raise HTTPException(422, 'Confirm permanent deletion with {"confirm": "purge"}.')
    purge_users = request.query_params.getlist("users") == ["yes"]
    try:
        directories = await run_in_threadpool(source_directories)
        counts = await _store.purge(users=purge_users)
    except psycopg.Error:
        logger.exception("Could not purge the development database")
        raise HTTPException(503, "The database purge failed; source directories were not deleted.") from None
    except OSError:
        logger.exception("Could not inspect local source directories")
        raise HTTPException(503, "Source directories could not be inspected; nothing was purged.") from None

    posters.store().clear_cache()
    session = identity.session(request)
    if session is not None:
        if purge_users:
            session.clear()
        else:
            session.pop("poster_id", None)
    try:
        await run_in_threadpool(remove_directories, directories)
    except OSError:
        logger.exception("Database purged, but local source deletion did not complete")
        raise HTTPException(
            503, "The database was purged, but source deletion is incomplete. Check server logs and retry.",
        ) from None
    logger.warning(
        "Local system purge deleted %s posters, %s conferences, %s users, and %s source directories",
        counts["posters"], counts["conferences"], counts["users"], len(directories),
    )
    return JSONResponse(
        {"ok": True, **counts, "source_directories": len(directories)},
        headers={"Cache-Control": "no-store"},
    )
