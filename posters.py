"""Owner-scoped poster storage and hybrid full-text/vector search."""

import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class Poster:
    pid: int
    name: str
    description: str | None
    dimensions: str | None
    template: str | None
    theme: str | None
    purchased: bool
    conference_date: datetime | None
    resumption: str | None = None

    @property
    def inches(self) -> tuple[float, float]:
        if self.dimensions is None:
            raise ValueError(f"Poster {self.pid} dimensions have not been set")
        match = re.fullmatch(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", self.dimensions)
        if match is None:
            raise ValueError(f"Poster {self.pid} has invalid dimensions")
        width, height = map(float, match.groups())
        if not all(math.isfinite(value) and 0 < value <= 240 for value in (width, height)):
            raise ValueError(f"Poster {self.pid} dimensions must be within 240 inches")
        return width, height


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
               p.description, p.dimensions, p.template, p.theme,
               EXISTS (SELECT 1 FROM purchase b WHERE b.pid = p.pid) AS purchased,
               (SELECT min(c.starts) FROM show s JOIN conference c USING (cid)
                WHERE s.pid = p.pid) AS conference_date,
               p.resumption
        FROM poster p
        LEFT JOIN section t ON t.pid = p.pid AND t.which = 'title'
        WHERE p.uid = %s
    """

    async def get(self, uid: int, pid: int) -> Poster | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(self._ROWS + " AND p.pid = %s", (uid, pid))
            row = await cursor.fetchone()
        return Poster(*row) if row else None

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
            cursor = await connection.execute(
                "INSERT INTO poster (uid, dimensions, created) VALUES (%s, NULL, now()) RETURNING pid",
                (uid,),
            )
            row = await cursor.fetchone()
            assert row is not None
            return row[0], True

    async def initialize(self, uid: int, pid: int, template: str, theme: str) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                "UPDATE poster p SET template = coalesce(template, %s), "
                "theme = coalesce(theme, %s), updated = now() "
                "WHERE uid = %s AND pid = %s AND NOT EXISTS "
                "(SELECT 1 FROM purchase b WHERE b.pid = p.pid)",
                (template, theme, uid, pid),
            )

    async def set_resumption(self, uid: int, pid: int, url: str | None) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "UPDATE poster SET resumption = %s WHERE uid = %s AND pid = %s RETURNING pid",
                (url, uid, pid),
            )
            return await cursor.fetchone() is not None

    async def delete(self, uid: int, pid: int) -> bool:
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
