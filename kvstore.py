"""PostgreSQL JSON memory with pickleable, connection-free store handles."""

import hashlib
import os

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


class KVStore:
    """Keys are unique within an owner, or within the unowned scope.

    Missing keys raise KeyError. Values must be JSON serializable. Only the
    database environment-variable name, never a connection or DSN, is retained.
    """

    def __init__(
        self,
        table: str,
        parent_table: str | None = None,
        *,
        schema: str = "public",
        foreign_key_value: object = None,
        database_env: str = "DATABASE_URL",
    ) -> None:
        self.table = table
        self.schema = schema
        self.database_env = database_env
        self.foreign_key_name: str | None = None
        self.foreign_key_type: str | None = None
        self.foreign_key_value = foreign_key_value
        target = sql.Identifier(schema, table)
        with self._connect() as connection, connection.cursor() as cursor:
            # Serialize discovery/creation for this table across worker processes.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{schema}.{table}",)
            )
            parent_key = None
            parent_type = None
            if parent_table is not None:
                cursor.execute(
                    """
                    SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    JOIN pg_attribute a
                      ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
                    WHERE n.nspname = %s AND t.relname = %s AND c.contype = 'p'
                    """,
                    (schema, parent_table),
                )
                columns = cursor.fetchall()
                if len(columns) != 1:
                    raise ValueError("parent table must have a single-column primary key")
                parent_key, parent_type = columns[0]
                if parent_key in ("key", "value"):
                    raise ValueError("parent primary key conflicts with KV columns")
            cursor.execute(
                """
                SELECT t.oid FROM pg_class t
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = %s AND t.relname = %s
                """,
                (schema, table),
            )
            if cursor.fetchone() is None:
                owner_column = sql.SQL("")
                if parent_table is not None:
                    owner_column = sql.SQL(", {} {} REFERENCES {} ({})").format(
                        sql.Identifier(parent_key),
                        sql.SQL(parent_type),
                        sql.Identifier(schema, parent_table),
                        sql.Identifier(parent_key),
                    )
                cursor.execute(
                    sql.SQL("CREATE TABLE {} (key TEXT NOT NULL, value JSONB NOT NULL {})")
                    .format(target, owner_column)
                )
            cursor.execute(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod),
                       pn.nspname, pt.relname, pa.attname,
                       cardinality(c.conkey), cardinality(c.confkey),
                       format_type(pa.atttypid, pa.atttypmod),
                       EXISTS (
                           SELECT 1 FROM pg_constraint pk
                           WHERE pk.conrelid = c.confrelid AND pk.contype = 'p'
                             AND pk.conkey = c.confkey
                       )
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_attribute a
                  ON a.attrelid = t.oid AND a.attnum = c.conkey[1]
                JOIN pg_class pt ON pt.oid = c.confrelid
                JOIN pg_namespace pn ON pn.oid = pt.relnamespace
                JOIN pg_attribute pa
                  ON pa.attrelid = pt.oid AND pa.attnum = c.confkey[1]
                WHERE n.nspname = %s AND t.relname = %s AND c.contype = 'f'
                """,
                (schema, table),
            )
            foreign_keys = cursor.fetchall()
            if len(foreign_keys) > 1 or any(
                row[5:7] != (1, 1) for row in foreign_keys
            ):
                raise ValueError("KV table must have at most one single-column foreign key")
            if foreign_keys:
                (
                    name, dtype, parent_schema, parent, referenced,
                    _, _, referenced_type, is_pk,
                ) = foreign_keys[0]
                if name in ("key", "value"):
                    raise ValueError("foreign key conflicts with KV columns")
                if not is_pk or dtype != referenced_type:
                    raise ValueError("KV foreign key must match its parent's primary key type")
                if parent_table is not None and (
                    parent_schema, parent, referenced, name, dtype
                ) != (schema, parent_table, parent_key, parent_key, parent_type):
                    raise ValueError("existing KV foreign key does not match parent primary key")
                self.foreign_key_name = name
                self.foreign_key_type = dtype
            elif parent_table is not None:
                raise ValueError("existing KV table has no foreign key to the parent")
            if foreign_key_value is not None and self.foreign_key_name is None:
                raise ValueError("cannot bind an owner without a foreign key")
            cursor.execute(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
                FROM pg_attribute a
                JOIN pg_class t ON t.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = %s AND t.relname = %s
                  AND a.attnum > 0 AND NOT a.attisdropped
                """,
                (schema, table),
            )
            columns = {name: (dtype, required) for name, dtype, required in cursor}
            if columns.get("key") != ("text", True) or columns.get("value") != ("jsonb", True):
                raise ValueError("KV table requires non-null key TEXT and value JSONB columns")
            suffix = hashlib.sha256(f"{schema}.{table}".encode()).hexdigest()[:16]
            if self.foreign_key_name is None:
                cursor.execute(
                    sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (key)").format(
                        sql.Identifier(f"kv_{suffix}_key"), target
                    )
                )
            else:
                owner = sql.Identifier(self.foreign_key_name)
                cursor.execute(
                    sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (key, {})").format(
                        sql.Identifier(f"kv_{suffix}_owner"), target, owner
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (key) WHERE {} IS NULL"
                    ).format(sql.Identifier(f"kv_{suffix}_unowned"), target, owner)
                )

    def _connect(self) -> psycopg.Connection:
        dsn = os.environ.get(self.database_env)
        if not dsn:
            raise RuntimeError(f"{self.database_env} must be set to use persistent memory")
        return psycopg.connect(dsn)

    def _owner(self, args: tuple[object, ...], kwargs: dict[str, object]) -> object:
        if len(args) > 1 or len(kwargs) > 1 or (args and kwargs):
            raise TypeError("supply at most one foreign key value")
        if kwargs and self.foreign_key_name not in kwargs:
            raise TypeError(f"expected foreign key keyword {self.foreign_key_name!r}")
        supplied = bool(args or kwargs)
        owner = args[0] if args else next(iter(kwargs.values()), self.foreign_key_value)
        if self.foreign_key_name is None and supplied:
            raise TypeError("this KV store has no foreign key")
        if self.foreign_key_value is not None and owner != self.foreign_key_value:
            raise ValueError("cannot access a different owner through a bound store")
        return owner

    def set(self, key: str, value: object, *args: object, **kwargs: object) -> None:
        owner_value = self._owner(args, kwargs)
        if not isinstance(key, str):
            raise TypeError("key must be a string")
        target = sql.Identifier(self.schema, self.table)
        values: tuple[object, ...] = (key, Jsonb(value))
        if self.foreign_key_name is None:
            query = sql.SQL(
                "INSERT INTO {} (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).format(target)
        else:
            owner = sql.Identifier(self.foreign_key_name)
            conflict = (
                sql.SQL("(key) WHERE {} IS NULL").format(owner)
                if owner_value is None else sql.SQL("(key, {})").format(owner)
            )
            query = sql.SQL(
                "INSERT INTO {} (key, value, {}) VALUES (%s, %s, %s) "
                "ON CONFLICT {} DO UPDATE SET value = EXCLUDED.value"
            ).format(target, owner, conflict)
            values += (owner_value,)
        with self._connect() as connection:
            connection.execute(query, values)

    def get(self, key: str, *args: object, **kwargs: object) -> tuple[object, object]:
        owner_value = self._owner(args, kwargs)
        if not isinstance(key, str):
            raise TypeError("key must be a string")
        owner = (
            sql.SQL("NULL") if self.foreign_key_name is None
            else sql.Identifier(self.foreign_key_name)
        )
        query = sql.SQL("SELECT value, {} FROM {} WHERE key = %s").format(
            owner, sql.Identifier(self.schema, self.table)
        )
        values: tuple[object, ...] = (key,)
        if self.foreign_key_name is not None:
            query += sql.SQL(" AND {} IS NOT DISTINCT FROM %s").format(owner)
            values += (owner_value,)
        with self._connect() as connection:
            row = connection.execute(query, values).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0], row[1]
