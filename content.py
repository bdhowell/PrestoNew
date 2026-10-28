"""Original source uploads and their owner-scoped metadata."""

import hashlib
import io
import os
import zipfile
import logging
import re
import json
from html import unescape
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

from google.cloud import storage
from google.api_core.exceptions import GoogleAPIError, NotFound
from google.auth.exceptions import GoogleAuthError
import psycopg
from psycopg import sql
from starlette.concurrency import run_in_threadpool

from posters import PosterStore
import source_processing as processing
import elucidation

logger = logging.getLogger(__name__)


class SourceConflictError(RuntimeError):
    """The poster or its source text changed while analysis was running."""


class SourceDeletionError(RuntimeError):
    """A poster's source file could not be removed."""


@dataclass(frozen=True)
class SourceText:
    count: int
    text: str
    fingerprint: str
    character_count: int


def readable_text(text: str, mimetype: str) -> str:
    if mimetype in (
        FORMATS[".xlsx"][0], FORMATS[".xlsm"][0], FORMATS[".numbers"][0],
    ) or "<table>" in text:
        text = unescape(re.sub(r"<[^>]+>", " ", text))
    return text.strip()


FORMATS = {
    ".pdf": ("application/pdf", "mixed"),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "mixed"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "mixed"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "table"),
    ".xlsm": ("application/vnd.ms-excel.sheet.macroEnabled.12", "table"),
    ".png": ("image/png", "image"),
    ".tif": ("image/tiff", "image"),
    ".tiff": ("image/tiff", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".pages": ("application/vnd.apple.pages", "mixed"),
    ".numbers": ("application/vnd.apple.numbers", "table"),
    ".key": ("application/vnd.apple.keynote", "mixed"),
    ".md": ("text/markdown", "text"),
}


def source_hash(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def validate_file(filename: str, data: bytes) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in FORMATS:
        raise ValueError("This file type is not supported.")
    if not data:
        raise ValueError("Empty files cannot be uploaded.")
    if suffix == ".md":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Markdown files must contain UTF-8 text.") from None
        valid = "\0" not in text
    elif suffix == ".pdf":
        valid = data.startswith(b"%PDF-")
    elif suffix == ".png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in (".jpg", ".jpeg"):
        valid = data.startswith(b"\xff\xd8\xff")
    elif suffix in (".tif", ".tiff"):
        valid = data.startswith((b"II*\0", b"MM\0*", b"II+\0", b"MM\0+"))
    else:
        valid = zipfile.is_zipfile(io.BytesIO(data))
        if valid:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    names = archive.namelist()
                    prefix = {
                        ".docx": "word/", ".pptx": "ppt/",
                        ".xlsx": "xl/", ".xlsm": "xl/",
                    }.get(suffix)
                    valid = any(name.startswith(prefix) for name in names) if prefix else any(
                        name.startswith(("Index/", "Metadata/")) or name == "index.xml"
                        for name in names
                    )
            except zipfile.BadZipFile:
                valid = False
    if not valid:
        raise ValueError("The file contents do not match its filename extension.")
    return FORMATS[suffix]


@dataclass(frozen=True)
class Source:
    sid: int
    filename: str
    mimetype: str
    size: int
    added: str
    parent: int | None = None
    description: str | None = None
    width: int | None = None
    height: int | None = None
    kind: str = "mixed"
    depth: int = 0

    @property
    def ill_sized(self) -> bool:
        return self.width is not None and self.height is not None and min(self.width, self.height) < 300

    @property
    def thumbnail(self) -> bool:
        return self.width is not None and self.height is not None


class SourceFiles:
    def __init__(self) -> None:
        self.bucket = os.environ.get("GCS_BUCKET")
        self.root = Path(os.environ.get("SOURCES_DIR", Path(__file__).parent / "sources")).resolve()

    def prepare_directories(self) -> None:
        if self.bucket:
            return
        for shard in range(10):
            (self.root / str(shard)).mkdir(parents=True, exist_ok=True)

    def put(self, fileid: UUID, data: bytes, mimetype: str) -> str:
        key = f"{fileid.int % 10}/{fileid}"
        if self.bucket:
            blob = storage.Client().bucket(self.bucket).blob(key)
            blob.upload_from_string(data, content_type=mimetype, if_generation_match=0, timeout=60)
            return f"gs://{self.bucket}/{key}"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        output = path.open("xb")
        try:
            with output:
                output.write(data)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return str(path)

    def delete(self, uri: str) -> None:
        """Remove a source file, treating an already-absent file as deleted."""
        if uri.startswith("gs://"):
            bucket, key = uri[5:].split("/", 1)
            try:
                storage.Client().bucket(bucket).blob(key).delete(timeout=60)
            except NotFound:
                pass
        else:
            Path(uri).unlink(missing_ok=True)

    def read(self, uri: str) -> bytes:
        if uri.startswith("gs://"):
            bucket, key = uri[5:].split("/", 1)
            return storage.Client().bucket(bucket).blob(key).download_as_bytes(timeout=60)
        return Path(uri).read_bytes()


class SourceStore(PosterStore):
    async def delete(self, uid: int, pid: int) -> bool:
        self.invalidate(uid, pid)
        connection = await self._connect()
        async with connection:
            await connection.execute('SELECT uid FROM "user" WHERE uid = %s FOR UPDATE', (uid,))
            cursor = await connection.execute(
                "SELECT pid FROM poster WHERE uid = %s AND pid = %s FOR UPDATE", (uid, pid)
            )
            if await cursor.fetchone() is None:
                return False

            cursor = await connection.execute(
                "SELECT DISTINCT storage_uri FROM source "
                "WHERE pid = %s AND storage_uri IS NOT NULL", (pid,),
            )
            files = SourceFiles()
            for (uri,) in await cursor.fetchall():
                try:
                    await run_in_threadpool(files.delete, uri)
                except (OSError, GoogleAPIError, GoogleAuthError, ValueError) as error:
                    raise SourceDeletionError(
                        f"Could not remove a source file for poster {pid}."
                    ) from error

            cursor = await connection.execute(
                "SELECT to_regclass(%s)", (f"{self.schema}.poster_memory",)
            )
            row = await cursor.fetchone()
            if row is not None and row[0] is not None:
                await connection.execute(
                    sql.SQL("DELETE FROM {} WHERE pid = %s").format(
                        sql.Identifier(self.schema, "poster_memory")
                    ), (pid,),
                )
            cursor = await connection.execute(
                "DELETE FROM poster WHERE uid = %s AND pid = %s RETURNING pid", (uid, pid)
            )
            deleted = await cursor.fetchone() is not None
        if deleted:
            self.invalidate(uid, pid)
        return deleted

    async def source_file(self, uid: int, sid: int) -> tuple[bytes, str] | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT s.storage_uri, s.mimetype FROM source s JOIN poster p USING (pid) "
                "WHERE p.uid = %s AND s.sid = %s", (uid, sid),
            )
            row = await cursor.fetchone()
        if row is None or not row[0]:
            return None
        return await run_in_threadpool(SourceFiles().read, row[0]), row[1]

    _SOURCE_COLUMNS = (
        "s.sid, coalesce(s.filename, parent.filename, 'Untitled source'), s.mimetype, "
        "s.size, s.added::text, s.parent, s.description, s.width, s.height, s.kind"
    )

    async def list_sources(self, uid: int, pid: int) -> list[Source]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT " + self._SOURCE_COLUMNS +
                " FROM source s JOIN poster p USING (pid) LEFT JOIN source parent ON parent.sid = s.parent "
                "WHERE p.uid = %s AND p.pid = %s ORDER BY s.added, s.sid", (uid, pid),
            )
            items = [Source(*row) for row in await cursor.fetchall()]
        children: dict[int | None, list[Source]] = {}
        ids = {item.sid for item in items}
        for item in items:
            children.setdefault(item.parent if item.parent in ids else None, []).append(item)
        ordered = []

        def append(parent: int | None, depth: int) -> None:
            for item in children.get(parent, []):
                ordered.append(replace(item, depth=depth))
                append(item.sid, depth + 1)

        append(None, 0)
        if len(ordered) != len(items):
            raise RuntimeError("Source hierarchy contains a cycle.")
        return ordered

    async def add(self, uid: int, pid: int, filename: str, data: bytes) -> Source:
        mimetype, kind = validate_file(filename, data)
        if kind == "image":
            width, height = await run_in_threadpool(processing.image_dimensions, data)
            processed = processing.Processed(
                width=width, height=height,
                description=await processing.describe_image(data),
            )
        else:
            processed = await run_in_threadpool(processing.extract, filename, data)
            processed.summary = await processing.summarize(processed.text)
        for asset in processed.assets:
            if asset.width is not None and not asset.description:
                asset.description = await processing.describe_image(asset.data)
        for warning in processed.warnings:
            logger.warning("Source conversion warning for %s: %s", filename, warning)
        files = SourceFiles()
        connection = await self._connect()
        written: list[str] = []
        try:
            async with connection:
                cursor = await connection.execute(
                    'SELECT uid FROM "user" WHERE uid = %s FOR UPDATE', (uid,),
                )
                if await cursor.fetchone() is None:
                    raise ValueError("Poster owner no longer exists.")
                cursor = await connection.execute(
                    "SELECT pid FROM poster p WHERE p.uid = %s AND p.pid = %s AND NOT EXISTS "
                    "(SELECT 1 FROM purchase b WHERE b.pid = p.pid) FOR UPDATE", (uid, pid),
                )
                if await cursor.fetchone() is None:
                    raise ValueError("This poster is no longer available for uploads.")
                original = processing.Asset(
                    filename, data, mimetype, kind, processed.text, processed.description,
                    processed.width, processed.height,
                )
                result = await self._insert_source(
                    connection, files, written, pid, original, None, processed.summary,
                )
                for asset in processed.assets:
                    await self._insert_source(connection, files, written, pid, asset, result.sid)
        except BaseException:
            for uri in written:
                try:
                    await run_in_threadpool(files.delete, uri)
                except (OSError, GoogleAPIError, GoogleAuthError):
                    logger.exception("Could not remove failed source upload %s", uri)
            raise
        return result

    async def _insert_source(
        self, connection: psycopg.AsyncConnection, files: SourceFiles, written: list[str], pid: int,
        asset: processing.Asset, parent: int | None, summary: str = "",
    ) -> Source:
        digest = source_hash(asset.data)
        if asset.width is not None or parent is not None:
            cursor = await connection.execute(
                "SELECT " + self._SOURCE_COLUMNS +
                " FROM source s LEFT JOIN source parent ON parent.sid = s.parent "
                "WHERE s.pid = %s AND s.hash = %s ORDER BY s.sid LIMIT 1", (pid, digest),
            )
            row = await cursor.fetchone()
            if row is not None:
                return Source(*row)
        kind = asset.kind
        if asset.width is not None and "logo" in (asset.filename + " " + asset.description).lower():
            cursor = await connection.execute(
                "SELECT sid, coalesce(width, 0) * coalesce(height, 0)::bigint "
                "FROM source WHERE pid = %s AND kind = 'logo'", (pid,),
            )
            logo = await cursor.fetchone()
            area = asset.width * (asset.height or 0)
            if logo is None or area > logo[1]:
                if logo is not None:
                    await connection.execute("UPDATE source SET kind = 'image' WHERE sid = %s", (logo[0],))
                kind = "logo"
        fileid = uuid4()
        uri = await run_in_threadpool(files.put, fileid, asset.data, asset.mimetype)
        written.append(uri)
        cursor = await connection.execute(
            "INSERT INTO source (pid, kind, parent, filename, mimetype, size, fileid, hash, "
            "storage_uri, text, summary, description, width, height) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING sid, filename, mimetype, size, added::text, parent, description, width, height, kind",
            (pid, kind, parent, asset.filename, asset.mimetype, len(asset.data), fileid,
             digest, uri, asset.text or None, summary or None, asset.description or None,
             asset.width, asset.height),
        )
        row = await cursor.fetchone()
        assert row is not None
        return Source(*row)

    async def completion(self, uid: int, pid: int) -> tuple[int, int]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT CASE WHEN s.parent IS NULL THEN s.text END, s.mimetype "
                "FROM source s JOIN poster p USING (pid) "
                "WHERE p.uid = %s AND p.pid = %s ORDER BY s.added, s.sid", (uid, pid),
            )
            rows = await cursor.fetchall()
        texts = []
        for text, mimetype in rows:
            if text:
                texts.append(readable_text(text, mimetype))
        return len(rows), sum(len(text.strip()) for text in texts)

    async def source_text(
        self, uid: int, pid: int, connection: psycopg.AsyncConnection | None = None,
    ) -> SourceText:
        if connection is None:
            connection = await self._connect()
            async with connection:
                return await self.source_text(uid, pid, connection)
        cursor = await connection.execute(
            "SELECT s.sid, s.filename, s.text, s.mimetype FROM source s JOIN poster p USING (pid) "
            "WHERE p.uid = %s AND p.pid = %s AND s.parent IS NULL ORDER BY s.added, s.sid",
            (uid, pid),
        )
        rows = await cursor.fetchall()
        texts = [readable_text(text, mimetype) for _, _, text, mimetype in rows if text]
        return SourceText(
            len(rows), "\n\n".join(text for text in texts if text),
            source_hash(json.dumps(rows, ensure_ascii=True)), sum(len(text) for text in texts),
        )

    async def save_elucidation(
        self, uid: int, pid: int, snapshot: SourceText, result: elucidation.PosterContent,
    ) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute('SELECT uid FROM "user" WHERE uid = %s FOR UPDATE', (uid,))
            cursor = await connection.execute(
                "SELECT pid FROM poster p WHERE p.uid = %s AND p.pid = %s AND NOT EXISTS "
                "(SELECT 1 FROM purchase b WHERE b.pid = p.pid) FOR UPDATE", (uid, pid),
            )
            if await cursor.fetchone() is None:
                raise SourceConflictError("This poster is no longer available for editing.")
            current = await self.source_text(uid, pid, connection)
            if current.fingerprint != snapshot.fingerprint:
                raise SourceConflictError("Your sources changed during analysis. Please run the analysis again.")
            await connection.execute("DELETE FROM authors WHERE pid = %s", (pid,))
            for author in result.authors:
                await connection.execute(
                    "INSERT INTO authors (pid, name, affiliation, errata) VALUES (%s, %s, %s, %s)",
                    (pid, author.name, author.institution or None, author.errata or None),
                )
            for which, text in result.sections().items():
                await connection.execute(
                    "INSERT INTO section (pid, which, content, updated) VALUES (%s, %s, %s, now()) "
                    "ON CONFLICT (pid, which) DO UPDATE SET content = excluded.content, "
                    "updated = excluded.updated, embedding = NULL",
                    (pid, which, text),
                )
            await connection.execute("UPDATE poster SET updated = now(), embedding = NULL WHERE pid = %s", (pid,))

    async def graphic(self, uid: int, pid: int, sid: int) -> bytes | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT s.storage_uri FROM source s JOIN poster p USING (pid) "
                "WHERE p.uid = %s AND p.pid = %s AND s.sid = %s "
                "AND s.width IS NOT NULL AND s.height IS NOT NULL", (uid, pid, sid),
            )
            row = await cursor.fetchone()
        return await run_in_threadpool(SourceFiles().read, row[0]) if row and row[0] else None


_store = SourceStore()


def store() -> SourceStore:
    return _store
