"""Owner-scoped poster storage and hybrid full-text/vector search."""

import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import openai
import psycopg
from psycopg import sql
from starlette.requests import HTTPConnection

import identity

logger = logging.getLogger(__name__)
Sort = Literal["name", "conference_date", "relevance"]
Direction = Literal["asc", "desc"]
TEMPLATES = ("arena", "central", "vertical")


def installed_themes() -> list[str]:
    return stylesheet_classes("themes")


def installed_fonts() -> list[str]:
    return stylesheet_classes("fonts")


def stylesheet_classes(stylesheet: Literal["themes", "fonts"]) -> list[str]:
    css = (Path(__file__).resolve().parent / "static" / "css" / f"{stylesheet}.css").read_text()
    classes = list(dict.fromkeys(re.findall(
        r"^\s*\.([a-zA-Z_][a-zA-Z0-9_-]*)\s*\{", css, re.MULTILINE,
    )))
    if not classes:
        raise RuntimeError(f"No poster {stylesheet} are installed")
    return classes

POSTER_UNITS = "cm"  # default units for poster dimensions
POSTER_MIN_WIDTH_CM = 36 * 2.54  # cm
POSTER_MIN_HEIGHT_CM = 36 * 2.54  # cm
POSTER_MAX_WIDTH_CM = 96 * 2.54  # cm
POSTER_MAX_HEIGHT_CM = 96 * 2.54  # cm
POSTER_UNIT_TO_CM = {"cm": 1, "mm": 0.1, "in": 2.54}

@dataclass(frozen=True)
class Poster:
    pid: int
    name: str
    description: str | None
    width: int | None
    height: int | None
    template: str | None
    theme: str | None
    purchased: bool
    conference_date: datetime | None
    resumption: str | None = None
    units: str = "cm"
    logo: bytes | None = None
    logotype: str | None = None
    moreinfopng: bytes | None = None
    fonts: str | None = None

    @property
    def centimeters(self) -> tuple[float, float]:
        if self.width is None or self.height is None:
            raise ValueError(f"Poster {self.pid} dimensions have not been set")
        scale = POSTER_UNIT_TO_CM.get(self.units)
        if scale is None:
            raise ValueError(f"Poster {self.pid} has invalid dimension units")
        width, height = self.width * scale, self.height * scale
        if not all(0 < value <= 240 * 2.54 for value in (width, height)):
            raise ValueError(f"Poster {self.pid} dimensions must be within 609.6 cm")
        return width, height

    @property
    def inches(self) -> tuple[float, float]:
        width, height = self.centimeters
        return width / 2.54, height / 2.54

    @property
    def display_dimensions(self) -> str:
        width, height = self.centimeters
        scale = POSTER_UNIT_TO_CM[self.units]
        return f"{width / scale:g} x {height / scale:g} {self.units}"


async def query_embedding(query: str) -> tuple[list[float] | None, str | None]:
    if not os.environ.get("OPENAI_API_KEY"):
        logger.info("Poster semantic search unavailable: OPENAI_API_KEY is not configured")
        return None, "Semantic search is unavailable; showing keyword matches."
    try:
        async with openai.AsyncOpenAI(timeout=10, max_retries=0) as client:
            result = await client.embeddings.create(
                model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
                input=query, dimensions=1536,
            )
    except openai.OpenAIError:
        logger.warning("Poster query embedding failed; using keyword search", exc_info=True)
        return None, "Semantic search is unavailable; showing keyword matches."
    embedding = result.data[0].embedding if result.data else []
    if len(embedding) != 1536 or not all(math.isfinite(value) for value in embedding):
        logger.error("Poster query embedding has invalid dimensions or values")
        return None, "Semantic search is unavailable; showing keyword matches."
    return embedding, None


class PosterStore:
    def __init__(self, database_env: str = "DATABASE_URL", schema: str = "public") -> None:
        self.database_env = database_env
        self.schema = schema
        self._last: tuple[int, Poster, float] | None = None

    def invalidate(self, uid: int, pid: int) -> None:
        if self._last is not None and (self._last[0], self._last[1].pid) == (uid, pid):
            self._last = None

    def clear_cache(self) -> None:
        self._last = None

    async def _connect(self) -> psycopg.AsyncConnection:
        dsn = os.environ.get(self.database_env)
        if not dsn:
            raise RuntimeError(f"{self.database_env} is required for posters")
        connection = await psycopg.AsyncConnection.connect(dsn)
        try:
            await connection.execute(sql.SQL("SET search_path TO {}, public").format(
                sql.Identifier(self.schema)
            ))
        except Exception:
            await connection.close()
            raise
        return connection

    _ROWS = """
        SELECT p.pid,
               coalesce(nullif(t.content, ''), nullif(p.description, ''), 'Untitled poster') AS name,
               p.description, p.width, p.height, p.template, p.theme,
               EXISTS (SELECT 1 FROM purchase b WHERE b.pid = p.pid) AS purchased,
               (SELECT min(c.starts) FROM show s JOIN conference c USING (cid)
                WHERE s.pid = p.pid) AS conference_date,
               p.resumption, p.units, p.logo, p.logotype, p.moreinfopng, p.fonts
        FROM poster p
        LEFT JOIN section t ON t.pid = p.pid AND t.which = 'title'
        WHERE p.uid = %s
    """

    async def get(self, uid: int, pid: int) -> Poster | None:
        if self._last is not None:
            owner, poster, expires = self._last
            if (owner, poster.pid) == (uid, pid) and time.monotonic() < expires:
                return poster
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(self._ROWS + " AND p.pid = %s", (uid, pid))
            row = await cursor.fetchone()
        poster = Poster(*row) if row else None
        self._last = (uid, poster, time.monotonic() + 2) if poster else None
        return poster

    async def list(
        self, uid: int, query: str = "", embedding: list[float] | None = None,
        sort: Sort = "name", direction: Direction = "asc",
    ) -> list[Poster]:
        order = {
            "name": "lower(name)", "conference_date": "conference_date", "relevance": "score",
        }[sort]
        if direction not in ("asc", "desc"):
            raise ValueError("Invalid poster sort direction")
        params: list[object] = [uid]
        scoped = self._ROWS
        if query:
            vector = "[" + ",".join(map(str, embedding)) + "]" if embedding is not None else None
            multiplier = int(os.environ.get("AUTOCOMPLETE_VECTOR_CANDIDATE_MULTIPLIER", "4"))
            minimum = int(os.environ.get("AUTOCOMPLETE_VECTOR_CANDIDATE_MIN", "100"))
            k = float(os.environ.get("AUTOCOMPLETE_MERGE_RRF_K", "60"))
            fts_weight = float(os.environ.get("AUTOCOMPLETE_MERGE_FTS_WEIGHT", "1"))
            vector_weight = float(os.environ.get("AUTOCOMPLETE_MERGE_VECTOR_WEIGHT", "1"))
            if multiplier < 1 or minimum < 1 or not all(
                math.isfinite(value) and value > 0 for value in (k, fts_weight, vector_weight)
            ):
                raise ValueError("Invalid autocomplete ranking settings")
            if os.environ.get("AUTOCOMPLETE_MERGE_METHOD", "rrf").lower() != "rrf":
                raise ValueError("Only rrf autocomplete merging is supported")
            candidate_limit = max(100 * multiplier, minimum)
            statement = """
                WITH scoped AS (
            """ + scoped + """
                ), fts AS (
                    SELECT p.pid,
                           row_number() OVER (
                               ORDER BY ts_rank_cd(p.search_vector, websearch_to_tsquery('english', %s))
                                        DESC, p.pid
                           ) AS rank
                    FROM poster p JOIN scoped s USING (pid)
                    WHERE p.search_vector @@ websearch_to_tsquery('english', %s)
                    ORDER BY rank LIMIT %s
                ), vec AS (
                    SELECT p.pid, row_number() OVER (
                        ORDER BY p.embedding <=> %s::vector, p.pid
                    ) AS rank
                    FROM poster p JOIN scoped s USING (pid)
                    WHERE %s::vector IS NOT NULL AND p.embedding IS NOT NULL
                    ORDER BY rank LIMIT %s
                ), ranked AS (
                    SELECT coalesce(f.pid, v.pid) AS pid,
                           coalesce(%s / (%s + f.rank), 0) +
                           coalesce(%s / (%s + v.rank), 0) AS score
                    FROM fts f FULL OUTER JOIN vec v USING (pid)
                )
                SELECT s.* FROM scoped s JOIN ranked USING (pid)
            """
            params.extend([
                query, query, candidate_limit, vector, vector, candidate_limit,
                fts_weight, k, vector_weight, k,
            ])
        else:
            statement = "WITH scoped AS (" + scoped + ") SELECT * FROM scoped"
            if sort == "relevance":
                order = "lower(name)"
        statement += f" ORDER BY {order} {direction} NULLS LAST, lower(name), pid"
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(statement, params)
            rows = await cursor.fetchall()
        return [Poster(*row) for row in rows]

    async def unpurchased(
        self, uid: int, connection: psycopg.AsyncConnection | None = None,
    ) -> int | None:
        """Find the owner's draft, optionally within the creation transaction."""
        if connection is None:
            connection = await self._connect()
            async with connection:
                return await self.unpurchased(uid, connection)
        cursor = await connection.execute(
            "SELECT pid FROM poster p WHERE uid = %s AND NOT EXISTS "
            "(SELECT 1 FROM purchase b WHERE b.pid = p.pid) ORDER BY pid LIMIT 1",
            (uid,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def new_or_existing(self, uid: int) -> tuple[int, bool]:
        """Lock the owner so concurrent New Poster clicks cannot create two drafts."""
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute('SELECT uid FROM "user" WHERE uid = %s FOR UPDATE', (uid,))
            if await cursor.fetchone() is None:
                raise ValueError("Poster owner no longer exists")
            pid = await self.unpurchased(uid, connection)
            if pid is not None:
                return pid, False
            theme = random.choice(installed_themes())
            fonts = random.choice(installed_fonts())
            cursor = await connection.execute(
                "INSERT INTO poster (uid, width, height, theme, fonts, created) "
                "VALUES (%s, NULL, NULL, %s, %s, now()) RETURNING pid",
                (uid, theme, fonts),
            )
            row = await cursor.fetchone()
            assert row is not None
            return row[0], True

    async def initialize(self, uid: int, pid: int, template: str, theme: str) -> None:
        self.invalidate(uid, pid)
        connection = await self._connect()
        async with connection:
            await connection.execute(
                "UPDATE poster p SET template = coalesce(template, %s), "
                "theme = coalesce(theme, %s), updated = now() "
                "WHERE uid = %s AND pid = %s AND NOT EXISTS "
                "(SELECT 1 FROM purchase b WHERE b.pid = p.pid)",
                (template, theme, uid, pid),
            )
        self.invalidate(uid, pid)

    async def set_resumption(self, uid: int, pid: int, url: str | None) -> bool:
        self.invalidate(uid, pid)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "UPDATE poster SET resumption = %s WHERE uid = %s AND pid = %s RETURNING pid",
                (url, uid, pid),
            )
            saved = await cursor.fetchone() is not None
        self.invalidate(uid, pid)
        return saved

    async def delete(self, uid: int, pid: int) -> bool:
        self.invalidate(uid, pid)
        connection = await self._connect()
        async with connection:
            # Serialize deletion with draft creation for this owner.
            await connection.execute('SELECT uid FROM "user" WHERE uid = %s FOR UPDATE', (uid,))
            cursor = await connection.execute(
                "SELECT pid FROM poster WHERE uid = %s AND pid = %s FOR UPDATE", (uid, pid)
            )
            if await cursor.fetchone() is None:
                return False
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
            return await cursor.fetchone() is not None

    async def content(self, uid: int, pid: int) -> tuple[list[tuple[str, str]], list[tuple[str, str | None]]]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT s.which::text, s.content FROM section s JOIN poster p USING (pid) "
                "WHERE p.uid = %s AND p.pid = %s ORDER BY s.which", (uid, pid),
            )
            sections = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT a.name, a.affiliation FROM authors a JOIN poster p USING (pid) "
                "WHERE p.uid = %s AND p.pid = %s ORDER BY a.aid", (uid, pid),
            )
            authors = await cursor.fetchall()
        return sections, authors

    async def save_section(self, uid: int, pid: int, which: str, value: str) -> bool:
        self.invalidate(uid, pid)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "INSERT INTO section (pid, which, content, updated, embedding) "
                "SELECT p.pid, %s::section_which, %s, now(), NULL FROM poster p "
                "WHERE p.uid = %s AND p.pid = %s "
                "AND NOT EXISTS (SELECT 1 FROM purchase b WHERE b.pid = p.pid) "
                "ON CONFLICT (pid, which) DO UPDATE SET content = excluded.content, "
                "updated = excluded.updated, embedding = NULL RETURNING xid",
                (which, value, uid, pid),
            )
            if await cursor.fetchone() is None:
                return False
            await connection.execute(
                "UPDATE poster SET updated = now(), embedding = NULL "
                "WHERE uid = %s AND pid = %s", (uid, pid),
            )
        self.invalidate(uid, pid)
        return True

    async def conference_details(self, uid: int, pid: int) -> tuple[str, dict]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT c.name, c.details FROM poster p JOIN conference c USING (cid) "
                "WHERE p.uid = %s AND p.pid = %s", (uid, pid),
            )
            row = await cursor.fetchone()
        return (row[0], row[1] or {}) if row else ("", {})

    async def set_theme(self, uid: int, pid: int, theme: str) -> bool:
        if theme not in installed_themes():
            raise ValueError("Unknown poster theme")
        self.invalidate(uid, pid)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "UPDATE poster p SET theme = %s, updated = now() "
                "WHERE p.uid = %s AND p.pid = %s AND NOT EXISTS "
                "(SELECT 1 FROM purchase b WHERE b.pid = p.pid) RETURNING p.pid",
                (theme, uid, pid),
            )
            saved = await cursor.fetchone() is not None
        self.invalidate(uid, pid)
        return saved

    async def set_dimensions(
        self, uid: int, pid: int, width: float, height: float, units: str,
    ) -> bool:
        scale = POSTER_UNIT_TO_CM.get(units)
        if scale is None or not all(
            math.isfinite(value) and 0 < value <= 609.6 for value in (width, height)
        ):
            raise ValueError("Invalid poster dimensions or units")
        unit_width, unit_height = width / scale, height / scale
        rounded_width, rounded_height = round(unit_width), round(unit_height)
        if not all(
            math.isclose(value, rounded, abs_tol=0.001)
            for value, rounded in (
                (unit_width, rounded_width), (unit_height, rounded_height),
            )
        ):
            raise ValueError("Poster dimensions must be whole numbers in the selected units")
        self.invalidate(uid, pid)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "UPDATE poster p SET width = %s, height = %s, units = %s, "
                "template = %s, updated = now() "
                "WHERE p.uid = %s AND p.pid = %s AND NOT EXISTS "
                "(SELECT 1 FROM purchase b WHERE b.pid = p.pid) RETURNING p.pid",
                (
                    rounded_width, rounded_height, units,
                    "arena" if width >= height else "vertical", uid, pid,
                ),
            )
            saved = await cursor.fetchone() is not None
        self.invalidate(uid, pid)
        return saved


_store = PosterStore()


def store() -> PosterStore:
    return _store


async def poster_context(connection: HTTPConnection) -> None:
    """Resolve the selected poster only after trusted account identity is installed."""
    connection.state.poster_id = None
    data = identity.session(connection)
    pid = data.get("poster_id") if data is not None else None
    uid = getattr(connection.state, "user_id", None)
    if type(pid) is int and uid is not None:
        poster = await store().get(uid, pid)
        if poster is not None:
            connection.state.poster_id = poster.pid
            return
    if data is not None:
        data.pop("poster_id", None)
