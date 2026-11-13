"""Original source uploads and their owner-scoped metadata."""

import hashlib
import io
import os
import zipfile
import logging
import re
import json
from html import unescape
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from google.cloud import storage
from google.api_core.exceptions import GoogleAPIError, NotFound
from google.auth.exceptions import GoogleAuthError
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
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

IMAGE_CLASSIFICATIONS = frozenset({
    "table", "chart", "diagram", "math expression", "image",
})
EVALUATED_MATH_FILENAME_PREFIX = "evaluation-math-expression-"


def source_hash(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def graphic_evaluation_hash(
    source_fingerprint: str, methods: str, results: str, graphic_hash: str | None,
) -> str:
    return source_hash(json.dumps(
        [source_fingerprint, methods, results, graphic_hash], ensure_ascii=True,
    ))


def graphic_type(mimetype: str, kind: str, filename: str) -> str | None:
    if kind == "table":
        return "tables"
    if kind == "math expression":
        return "math"
    if mimetype == "image/svg+xml":
        return "svg"
    if mimetype == "image/png":
        return "png"
    if mimetype == "image/gif":
        return "gif"
    if mimetype == "image/jpeg":
        suffix = Path(filename).suffix.lower()
        return suffix[1:] if suffix in (".jpg", ".jpeg") else "jpg"
    return None


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
    extracted: bool = True
    depth: int = 0

    @property
    def ill_sized(self) -> bool:
        return self.width is not None and self.height is not None and min(self.width, self.height) < 300

    @property
    def thumbnail(self) -> bool:
        return self.width is not None and self.height is not None


@dataclass(frozen=True)
class GraphicSource:
    sid: int
    filename: str
    mimetype: str = "image/png"
    kind: str = "image"
    description: str | None = None
    parent: int | None = None
    type: str = "png"
    assessments: dict[str, dict[str, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphicEvaluationSource:
    sid: int
    filename: str
    mimetype: str
    parent: int | None
    evaluation: dict[str, object] | None
    digest: str | None

    @property
    def category(self) -> str:
        return "uploaded" if self.parent is None else "extracted"


@dataclass(frozen=True)
class PosterThumbnailState:
    poster_updated: datetime
    sources_updated: datetime | None
    thumbnail_updated: datetime | None

    @property
    def stale(self) -> bool:
        return (
            self.thumbnail_updated is None
            or self.poster_updated > self.thumbnail_updated
            or (
                self.sources_updated is not None
                and self.sources_updated > self.thumbnail_updated
            )
        )

    @property
    def revision(self) -> tuple[datetime, datetime | None]:
        return self.poster_updated, self.sources_updated


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
                "WHERE p.uid = %s AND s.sid = %s AND s.kind <> 'thumbnail'", (uid, sid),
            )
            row = await cursor.fetchone()
        if row is None or not row[0]:
            return None
        return await run_in_threadpool(SourceFiles().read, row[0]), row[1]

    async def thumbnail_state(self, uid: int, pid: int) -> PosterThumbnailState | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT coalesce(p.updated, p.created), "
                "(SELECT max(s.updated) FROM source s "
                " WHERE s.pid = p.pid AND s.kind <> 'thumbnail'), "
                "(SELECT t.updated FROM source t "
                " WHERE t.pid = p.pid AND t.kind = 'thumbnail' "
                "AND t.storage_uri IS NOT NULL) "
                "FROM poster p WHERE p.uid = %s AND p.pid = %s "
                "AND NOT EXISTS (SELECT 1 FROM purchase b WHERE b.pid = p.pid)",
                (uid, pid),
            )
            row = await cursor.fetchone()
        return PosterThumbnailState(*row) if row is not None else None

    async def save_thumbnail(
        self, uid: int, pid: int, data: bytes,
        expected_revision: tuple[datetime, datetime | None],
    ) -> bool:
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Poster thumbnails must be PNG images.")
        width, height = await run_in_threadpool(processing.image_dimensions, data)
        files = SourceFiles()
        written: list[str] = []
        old_uri: str | None = None
        connection = await self._connect()
        try:
            async with connection:
                cursor = await connection.execute(
                    "SELECT p.pid FROM poster p WHERE p.uid = %s AND p.pid = %s "
                    "AND NOT EXISTS (SELECT 1 FROM purchase b WHERE b.pid = p.pid) "
                    "FOR UPDATE", (uid, pid),
                )
                if await cursor.fetchone() is None:
                    return False
                cursor = await connection.execute(
                    "SELECT coalesce(p.updated, p.created), "
                    "(SELECT max(s.updated) FROM source s "
                    " WHERE s.pid = p.pid AND s.kind <> 'thumbnail'), "
                    "(SELECT t.updated FROM source t "
                    " WHERE t.pid = p.pid AND t.kind = 'thumbnail' "
                    "AND t.storage_uri IS NOT NULL) "
                    "FROM poster p WHERE p.uid = %s AND p.pid = %s",
                    (uid, pid),
                )
                row = await cursor.fetchone()
                state = PosterThumbnailState(*row)
                if state.revision != expected_revision:
                    return False
                if not state.stale:
                    return True

                cursor = await connection.execute(
                    "SELECT sid, storage_uri FROM source "
                    "WHERE pid = %s AND kind = 'thumbnail' FOR UPDATE", (pid,),
                )
                existing = await cursor.fetchone()
                if existing is None:
                    asset = processing.Asset(
                        "thumbnail.png", data, "image/png", "thumbnail",
                        width=width, height=height,
                    )
                    await self._insert_source(
                        connection, files, written, pid, asset, None, extracted=True,
                    )
                else:
                    sid, old_uri = existing
                    fileid = uuid4()
                    uri = await run_in_threadpool(files.put, fileid, data, "image/png")
                    written.append(uri)
                    await connection.execute(
                        "UPDATE source SET filename = 'thumbnail.png', mimetype = 'image/png', "
                        "size = %s, height = %s, width = %s, fileid = %s, storage_uri = %s, "
                        "hash = %s, parent = NULL, text = NULL, description = NULL, "
                        "added = now(), updated = now(), extracted = TRUE WHERE sid = %s",
                        (len(data), height, width, fileid, uri, source_hash(data), sid),
                    )
        except BaseException:
            for uri in written:
                try:
                    await run_in_threadpool(files.delete, uri)
                except (OSError, GoogleAPIError, GoogleAuthError):
                    logger.exception("Could not remove failed poster thumbnail %s", uri)
            raise
        if old_uri:
            try:
                await run_in_threadpool(files.delete, old_uri)
            except (OSError, GoogleAPIError, GoogleAuthError, ValueError):
                logger.exception("Could not remove replaced poster thumbnail %s", old_uri)
        return True

    async def poster_thumbnail(self, uid: int, pid: int) -> bytes | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT s.storage_uri FROM source s JOIN poster p USING (pid) "
                "WHERE p.uid = %s AND p.pid = %s AND s.kind = 'thumbnail'",
                (uid, pid),
            )
            row = await cursor.fetchone()
        if row is None or not row[0]:
            return None
        return await run_in_threadpool(SourceFiles().read, row[0])

    _SOURCE_COLUMNS = (
        "s.sid, coalesce(s.filename, parent.filename, 'Untitled source'), s.mimetype, "
        "s.size, s.added::text, s.parent, "
        "CASE WHEN s.kind = 'math expression' THEN s.text ELSE s.description END, "
        "s.width, s.height, s.kind, s.extracted"
    )

    async def list_sources(self, uid: int, pid: int) -> list[Source]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT " + self._SOURCE_COLUMNS +
                " FROM source s JOIN poster p USING (pid) LEFT JOIN source parent ON parent.sid = s.parent "
                "WHERE p.uid = %s AND p.pid = %s AND s.kind <> 'thumbnail' "
                "ORDER BY s.added, s.sid", (uid, pid),
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
        width = height = None
        if kind == "image":
            width, height = await run_in_threadpool(processing.image_dimensions, data)
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
                    filename, data, mimetype, kind, width=width, height=height,
                )
                result = await self._insert_source(
                    connection, files, written, pid, original, None, extracted=False,
                )
        except BaseException:
            for uri in written:
                try:
                    await run_in_threadpool(files.delete, uri)
                except (OSError, GoogleAPIError, GoogleAuthError):
                    logger.exception("Could not remove failed source upload %s", uri)
            raise
        return result

    async def extract(self, uid: int, pid: int, sid: int) -> None:
        files = SourceFiles()
        connection = await self._connect()
        written: list[str] = []
        try:
            async with connection:
                # Serialize extraction and edits for this poster, including retries in other tabs.
                cursor = await connection.execute(
                    "SELECT p.pid FROM poster p WHERE p.uid = %s AND p.pid = %s AND NOT EXISTS "
                    "(SELECT 1 FROM purchase b WHERE b.pid = p.pid) FOR UPDATE", (uid, pid),
                )
                if await cursor.fetchone() is None:
                    raise SourceConflictError("This poster is no longer available for extraction.")
                cursor = await connection.execute(
                    "SELECT filename, storage_uri, kind, extracted FROM source "
                    "WHERE pid = %s AND sid = %s AND parent IS NULL FOR UPDATE", (pid, sid),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise SourceConflictError("Source file not found for this poster.")
                filename, uri, kind, extracted = row
                if extracted:
                    return
                data = await run_in_threadpool(files.read, uri)
                if kind in ("image", "logo"):
                    width, height = await run_in_threadpool(processing.image_dimensions, data)
                    result = processing.Processed(
                        width=width, height=height,
                        description=await processing.describe_image(data),
                    )
                else:
                    result = await run_in_threadpool(processing.extract, filename, data)
                for asset in result.assets:
                    if asset.width is not None and not asset.description:
                        asset.description = await processing.describe_image(asset.data)
                for warning in result.warnings:
                    logger.warning("Source conversion warning for %s: %s", filename, warning)
                original = processing.Asset(
                    filename, data, "", kind, result.text, result.description,
                    result.width, result.height,
                )
                kind = await self._asset_kind(connection, pid, original)
                await connection.execute(
                    "UPDATE source SET text = %s, description = %s, "
                    "width = %s, height = %s, kind = %s, extracted = TRUE, updated = now() "
                    "WHERE sid = %s",
                    (result.text or None, result.description or None, result.width,
                     result.height, kind, sid),
                )
                for asset in result.assets:
                    await self._insert_source(connection, files, written, pid, asset, sid)
        except BaseException:
            for uri in written:
                try:
                    await run_in_threadpool(files.delete, uri)
                except (OSError, GoogleAPIError, GoogleAuthError):
                    logger.exception("Could not remove failed extraction asset %s", uri)
            raise

    async def _insert_source(
        self, connection: psycopg.AsyncConnection, files: SourceFiles, written: list[str], pid: int,
        asset: processing.Asset, parent: int | None, extracted: bool = True,
        deduplicate: bool = True,
    ) -> Source:
        digest = source_hash(asset.data)
        if deduplicate and asset.kind != "thumbnail" and (asset.width is not None or parent is not None):
            cursor = await connection.execute(
                "SELECT " + self._SOURCE_COLUMNS +
                " FROM source s LEFT JOIN source parent ON parent.sid = s.parent "
                "WHERE s.pid = %s AND s.hash = %s AND s.kind <> 'thumbnail' "
                "ORDER BY s.sid LIMIT 1", (pid, digest),
            )
            row = await cursor.fetchone()
            if row is not None:
                return Source(*row)
        kind = await self._asset_kind(connection, pid, asset) if extracted else asset.kind
        fileid = uuid4()
        uri = await run_in_threadpool(files.put, fileid, asset.data, asset.mimetype)
        written.append(uri)
        cursor = await connection.execute(
            "INSERT INTO source (pid, kind, parent, filename, mimetype, size, fileid, hash, "
            "storage_uri, text, description, width, height, extracted) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING sid, filename, mimetype, size, added::text, parent, description, width, height, kind, extracted",
            (pid, kind, parent, asset.filename, asset.mimetype, len(asset.data), fileid,
             digest, uri, asset.text or None, asset.description or None,
             asset.width, asset.height, extracted),
        )
        row = await cursor.fetchone()
        assert row is not None
        return Source(*row)

    async def _asset_kind(
        self, connection: psycopg.AsyncConnection, pid: int, asset: processing.Asset,
    ) -> str:
        if asset.width is not None and "logo" in (asset.filename + " " + asset.description).lower():
            cursor = await connection.execute(
                "SELECT sid, coalesce(width, 0) * coalesce(height, 0)::bigint "
                "FROM source WHERE pid = %s AND kind = 'logo'", (pid,),
            )
            logo = await cursor.fetchone()
            if logo is None or asset.width * (asset.height or 0) > logo[1]:
                if logo is not None:
                    await connection.execute(
                        "UPDATE source SET kind = 'image', updated = now() WHERE sid = %s",
                        (logo[0],),
                    )
                return "logo"
        return asset.kind

    async def completion(self, uid: int, pid: int) -> tuple[int, int]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT CASE WHEN s.parent IS NULL THEN s.text END, s.mimetype "
                "FROM source s JOIN poster p USING (pid) "
                "WHERE p.uid = %s AND p.pid = %s AND s.kind <> 'thumbnail' "
                "ORDER BY s.added, s.sid", (uid, pid),
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
            "SELECT s.sid, s.filename, s.text, s.mimetype, s.extracted FROM source s JOIN poster p USING (pid) "
            "WHERE p.uid = %s AND p.pid = %s AND s.parent IS NULL AND s.kind <> 'thumbnail' "
            "ORDER BY s.added, s.sid",
            (uid, pid),
        )
        rows = await cursor.fetchall()
        if any(not extracted for _, _, _, _, extracted in rows):
            raise SourceConflictError("Please finish reading your files at /content/extract before running analysis.")
        texts = [readable_text(text, mimetype) for _, _, text, mimetype, _ in rows if text]
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
                "AND s.width IS NOT NULL AND s.height IS NOT NULL "
                "AND s.kind <> 'thumbnail'", (uid, pid, sid),
            )
            row = await cursor.fetchone()
        return await run_in_threadpool(SourceFiles().read, row[0]) if row and row[0] else None

    async def graphic_source(self, uid: int, pid: int, sid: int) -> tuple[bytes, str] | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT s.storage_uri, s.mimetype FROM source s JOIN poster p USING (pid) "
                "WHERE p.uid = %s AND p.pid = %s AND s.sid = %s "
                "AND s.width IS NOT NULL AND s.height IS NOT NULL "
                "AND s.mimetype IN ('image/svg+xml', 'image/png', 'image/jpeg', 'image/gif') "
                "AND s.kind <> 'thumbnail'", (uid, pid, sid),
            )
            row = await cursor.fetchone()
        if row is None or not row[0]:
            return None
        return await run_in_threadpool(SourceFiles().read, row[0]), row[1]

    async def graphic_sources(self, uid: int, pid: int) -> list[GraphicSource]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT s.sid, coalesce(s.filename, parent.filename, 'Untitled source'), "
                "s.mimetype, s.kind, s.description, s.parent, "
                "a.section::text, a.rating, a.explanation, a.criteria, "
                "a.supported_elements, a.limitations, a.suggested_improvements, a.created, "
                "(SELECT max(root.updated) FROM source root WHERE root.pid = s.pid "
                "AND root.parent IS NULL AND root.kind <> 'thumbnail'), "
                "(SELECT section.updated FROM section "
                "WHERE section.pid = s.pid AND section.which::text = a.section::text), s.updated "
                "FROM source s JOIN poster p USING (pid) "
                "LEFT JOIN source parent ON parent.sid = s.parent "
                "LEFT JOIN assessment a ON a.sid = s.sid "
                "AND a.section IN ('materials and methods', 'results') "
                "WHERE p.uid = %s AND p.pid = %s AND s.width IS NOT NULL "
                "AND s.height IS NOT NULL AND s.storage_uri IS NOT NULL "
                "AND s.mimetype IN ('image/svg+xml', 'image/png', 'image/jpeg', 'image/gif') "
                "AND s.kind <> 'thumbnail' "
                "ORDER BY (s.parent IS NOT NULL), s.sid, a.section", (uid, pid),
            )
            rows = await cursor.fetchall()

        graphics: list[GraphicSource] = []
        index = 0
        while index < len(rows):
            sid = rows[index][0]
            grouped = []
            while index < len(rows) and rows[index][0] == sid:
                grouped.append(rows[index])
                index += 1
            row = grouped[0]
            filename = row[1]
            source_type = graphic_type(row[2], row[3], filename)
            if source_type is None:
                continue
            assessments = {}
            for item in grouped:
                section = item[6]
                if section is None or item[13] is None:
                    continue
                latest_change = max(
                    (value for value in (item[14], item[15], item[16]) if value is not None),
                    default=None,
                )
                if latest_change is None or item[13] < latest_change:
                    continue
                assessments[section] = {
                    "rating": item[7],
                    "explanation": item[8],
                    "criteria": item[9] or {},
                    "supported_elements": item[10] or [],
                    "limitations": item[11] or [],
                    "suggested_improvements": item[12] or [],
                }
            graphics.append(GraphicSource(
                sid, filename, row[2], row[3], row[4], row[5], source_type, assessments,
            ))
        return graphics

    async def evaluation_sources(self, uid: int, pid: int) -> list[GraphicEvaluationSource]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT s.sid, coalesce(s.filename, parent.filename, 'Untitled source'), "
                "s.mimetype, s.parent, s.kind, s.description, s.hash, "
                "ARRAY(SELECT formula.text FROM source formula "
                "WHERE formula.pid = s.pid AND formula.parent = s.sid "
                "AND formula.kind = 'math expression' "
                "AND formula.filename LIKE %s "
                "ORDER BY formula.sid), "
                "a.section::text, a.rating, a.explanation, a.criteria, "
                "a.supported_elements, a.limitations, a.suggested_improvements, a.created, "
                "(SELECT max(root.updated) FROM source root WHERE root.pid = s.pid "
                "AND root.parent IS NULL AND root.kind <> 'thumbnail'), "
                "(SELECT max(section.updated) FROM section "
                "WHERE section.pid = s.pid AND section.which IN "
                "('materials and methods', 'results')), s.updated "
                "FROM source s JOIN poster p USING (pid) "
                "LEFT JOIN source parent ON parent.sid = s.parent "
                "LEFT JOIN assessment a ON a.sid = s.sid "
                "AND a.section IN ('materials and methods', 'results') "
                "WHERE p.uid = %s AND p.pid = %s AND s.width IS NOT NULL "
                "AND s.height IS NOT NULL AND s.storage_uri IS NOT NULL "
                "AND s.mimetype IN ('image/png', 'image/jpeg') "
                "AND s.kind <> 'thumbnail' "
                "ORDER BY (s.parent IS NOT NULL), s.sid, a.section",
                (f"{EVALUATED_MATH_FILENAME_PREFIX}%", uid, pid),
            )
            rows = await cursor.fetchall()

        graphics: list[GraphicEvaluationSource] = []
        index = 0
        while index < len(rows):
            sid = rows[index][0]
            grouped = []
            while index < len(rows) and rows[index][0] == sid:
                grouped.append(rows[index])
                index += 1
            row = grouped[0]
            assessments = {
                item[8]: {
                    "rating": item[9],
                    "explanation": item[10],
                    "criteria": item[11],
                    "supported_elements": item[12] or [],
                    "limitations": item[13] or [],
                    "suggested_improvements": item[14] or [],
                }
                for item in grouped if item[8] is not None
            }
            assessment_times = [item[15] for item in grouped if item[15] is not None]
            latest_change = max(
                (value for value in (row[16], row[17], row[18]) if value is not None),
                default=None,
            )
            current = (
                set(assessments) == {"materials and methods", "results"}
                and len(assessment_times) == 2
                and latest_change is not None
                and all(created >= latest_change for created in assessment_times)
            )
            classification = row[4] if row[4] in IMAGE_CLASSIFICATIONS else "image"
            evaluation = None
            if current:
                evaluation = {
                    "image_analysis": {
                        "classification": classification,
                        "description": row[5] or "",
                        "math_expressions": row[7] or [],
                    },
                    "evaluations": {
                        "materials_and_methods": assessments["materials and methods"],
                        "results": assessments["results"],
                    },
                }
            graphics.append(GraphicEvaluationSource(
                row[0], row[1], row[2], row[3], evaluation, row[6],
            ))
        return graphics

    async def save_graphic_evaluation(
        self, uid: int, pid: int, sid: int, result: dict[str, object],
        expected_hash: str,
    ) -> bool:
        files = SourceFiles()
        written: list[str] = []
        obsolete: list[str] = []
        connection = await self._connect()
        chart_old_uri: str | None = None
        try:
            async with connection:
                cursor = await connection.execute(
                    "SELECT s.sid, s.hash, p.cid, s.filename, s.mimetype, s.storage_uri, s.description "
                    "FROM source s JOIN poster p USING (pid) "
                    "WHERE p.uid = %s AND p.pid = %s AND s.sid = %s "
                    "AND s.width IS NOT NULL AND s.height IS NOT NULL "
                    "AND s.mimetype IN ('image/png', 'image/jpeg') "
                    "AND s.kind <> 'thumbnail' "
                    "AND NOT EXISTS (SELECT 1 FROM purchase b WHERE b.pid = p.pid) "
                    "FOR UPDATE OF p, s", (uid, pid, sid),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                snapshot = await self.source_text(uid, pid, connection)
                cursor = await connection.execute(
                    "SELECT which::text, content FROM section WHERE pid = %s "
                    "AND which IN ('materials and methods', 'results')", (pid,),
                )
                sections = dict(await cursor.fetchall())
                methods = elucidation.plain_text(sections.get("materials and methods", "")) \
                    or "No Materials and Methods text was provided."
                results = elucidation.plain_text(sections.get("results", "")) \
                    or "No Results text was provided."
                current_hash = graphic_evaluation_hash(snapshot.fingerprint, methods, results, row[1])
                if current_hash != expected_hash:
                    return False
                evaluations = result.get("evaluations")
                if not isinstance(evaluations, dict):
                    raise ValueError("Graphic evaluation must contain section evaluations.")
                image_analysis = result.get("image_analysis")
                classification = (
                    image_analysis.get("classification")
                    if isinstance(image_analysis, dict) else None
                )
                math_expressions = (
                    image_analysis.get("math_expressions")
                    if isinstance(image_analysis, dict) else None
                )
                if (
                    not isinstance(classification, str)
                    or classification not in IMAGE_CLASSIFICATIONS
                ):
                    raise ValueError("Graphic evaluation has invalid image classification.")
                if (
                    not isinstance(math_expressions, list)
                    or not all(
                        isinstance(expression, str) and expression.strip()
                        for expression in math_expressions
                    )
                    or (classification != "math expression" and math_expressions)
                ):
                    raise ValueError("Graphic evaluation has invalid math expressions.")
                assessment_rows = []
                for key, section in (
                    ("materials_and_methods", "materials and methods"),
                    ("results", "results"),
                ):
                    evaluation = evaluations.get(key)
                    if not isinstance(evaluation, dict):
                        raise ValueError(f"Graphic evaluation is missing {section}.")
                    rating = evaluation.get("rating")
                    explanation = evaluation.get("explanation")
                    criteria = evaluation.get("criteria")
                    supported_elements = evaluation.get("supported_elements")
                    limitations = evaluation.get("limitations")
                    suggested_improvements = evaluation.get("suggested_improvements")
                    if (
                        not isinstance(rating, str)
                        or not isinstance(explanation, str)
                        or not isinstance(criteria, dict)
                        or set(criteria) != {
                            "relevance", "technical_accuracy", "specificity", "completeness",
                            "evidentiary_support", "potential_for_misinterpretation",
                        }
                        or not all(isinstance(value, str) for value in criteria.values())
                        or any(
                            not isinstance(values, list)
                            or not all(isinstance(value, str) for value in values)
                            for values in (
                                supported_elements, limitations, suggested_improvements,
                            )
                        )
                    ):
                        raise ValueError(f"Graphic evaluation has invalid {section} assessment.")
                    assessment_rows.append((
                        pid, row[2], sid, section, rating, explanation, Jsonb(criteria),
                        supported_elements, limitations, suggested_improvements,
                    ))

                if classification == "chart" and row[4] != "image/png":
                    if not row[5]:
                        raise ValueError("The chart image is no longer available.")
                    image_data = await run_in_threadpool(files.read, row[5])
                    png_data = await run_in_threadpool(processing.image_as_png, image_data)
                    width, height = await run_in_threadpool(processing.image_dimensions, png_data)
                    fileid = uuid4()
                    uri = await run_in_threadpool(files.put, fileid, png_data, "image/png")
                    written.append(uri)
                    chart_old_uri = row[5]
                    png_hash = source_hash(png_data)
                    await connection.execute(
                        "UPDATE source SET filename = %s, mimetype = 'image/png', size = %s, "
                        "width = %s, height = %s, fileid = %s, storage_uri = %s, hash = %s, "
                        "updated = now() "
                        "WHERE pid = %s AND sid = %s",
                        (
                            Path(row[3] or "chart").with_suffix(".png").name,
                            len(png_data), width, height, fileid, uri, png_hash, pid, sid,
                        ),
                    )

                cursor = await connection.execute(
                    "SELECT storage_uri FROM source WHERE pid = %s AND parent = %s "
                    "AND kind = 'math expression' AND filename LIKE %s FOR UPDATE",
                    (pid, sid, f"{EVALUATED_MATH_FILENAME_PREFIX}%"),
                )
                obsolete.extend(uri for (uri,) in await cursor.fetchall() if uri)
                await connection.execute(
                    "DELETE FROM source WHERE pid = %s AND parent = %s "
                    "AND kind = 'math expression' AND filename LIKE %s",
                    (pid, sid, f"{EVALUATED_MATH_FILENAME_PREFIX}%"),
                )
                if classification == "math expression":
                    for index, expression in enumerate(math_expressions, start=1):
                        data = expression.encode("utf-8")
                        await self._insert_source(
                            connection, files, written, pid,
                            processing.Asset(
                                f"{EVALUATED_MATH_FILENAME_PREFIX}{index}.tex",
                                data, "text/plain", "math expression", text=expression,
                                description="LaTeX expression from image evaluation.",
                            ),
                            sid, deduplicate=False,
                        )

                await connection.execute(
                    "UPDATE source SET kind = CASE "
                    "WHEN %s = 'image' AND kind = 'logo' THEN kind ELSE %s END, "
                    "description = %s, updated = now() "
                    "WHERE pid = %s AND sid = %s",
                    (
                        classification, classification,
                        image_analysis.get("description") or row[6], pid, sid,
                    ),
                )
                for assessment in assessment_rows:
                    await connection.execute(
                        "INSERT INTO assessment (pid, cid, sid, section, rating, explanation, "
                        "criteria, supported_elements, limitations, suggested_improvements) "
                        "VALUES (%s, %s, %s, %s::section_which, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (sid, section) DO UPDATE SET "
                        "pid = EXCLUDED.pid, cid = EXCLUDED.cid, rating = EXCLUDED.rating, "
                        "explanation = EXCLUDED.explanation, criteria = EXCLUDED.criteria, "
                        "supported_elements = EXCLUDED.supported_elements, "
                        "limitations = EXCLUDED.limitations, "
                        "suggested_improvements = EXCLUDED.suggested_improvements, created = now()",
                        assessment,
                    )
        except BaseException:
            for uri in written:
                try:
                    await run_in_threadpool(files.delete, uri)
                except (OSError, GoogleAPIError, GoogleAuthError, ValueError):
                    logger.exception("Could not remove failed evaluation source %s", uri)
            raise

        for uri in [*obsolete, *([chart_old_uri] if chart_old_uri else [])]:
            try:
                await run_in_threadpool(files.delete, uri)
            except (OSError, GoogleAPIError, GoogleAuthError, ValueError):
                logger.exception("Could not remove replaced evaluation source %s", uri)
        return True


_store = SourceStore()


def store() -> SourceStore:
    return _store
