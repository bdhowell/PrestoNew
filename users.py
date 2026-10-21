"""PostgreSQL user accounts, password hashing, and signed time-limited links."""

import base64
import hashlib
import hmac
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import sql

SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LENGTH = 2**14, 8, 1, 32
CONFIRMATION_PURPOSE = "confirm"
RECOVERY_PURPOSE = "recover"


class DuplicateUserError(ValueError):
    """The username and/or email address already belongs to an account."""

    def __init__(self, fields: set[str]) -> None:
        super().__init__(f"already registered: {', '.join(sorted(fields))}")
        self.fields = fields


@dataclass(frozen=True)
class User:
    uid: int
    name: str
    username: str
    email: str
    password: str
    recovery: datetime | None
    verified: datetime | None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=SCRYPT_LENGTH,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_password(password: str, stored: str | None) -> bool:
    """Check a password; a missing hash still costs one hash to resist timing probes."""
    try:
        scheme, n, r, p, salt, expected = (stored or _DUMMY_HASH).split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(_unb64(expected)),
        )
    except ValueError:
        return False
    return stored is not None and hmac.compare_digest(digest, _unb64(expected))


def _secret() -> bytes:
    secret = os.environ.get("SECRET_KEY", "").encode()
    if len(secret) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 bytes to sign account links")
    return secret


def link_token(purpose: str, user: User) -> str:
    """Sign a link bound to the user's recovery timestamp and current password.

    Clearing or resetting `recovery`, or changing the password, invalidates it.
    """
    if user.recovery is None:
        raise ValueError("user has no pending confirmation or recovery")
    message = f"{purpose}:{user.uid}:{user.recovery.isoformat()}:{user.password}"
    return _b64(hmac.digest(_secret(), message.encode(), "sha256"))


def check_link(purpose: str, user: User | None, token: str, max_age: int) -> bool:
    if user is None or user.recovery is None:
        return False
    if datetime.now(timezone.utc) - user.recovery > timedelta(seconds=max_age):
        return False
    return hmac.compare_digest(link_token(purpose, user), token)


class UserStore:
    """Synchronous store; call from a threadpool in async code."""

    _COLUMNS = "uid, name, username, email, password, recovery, verified"

    def __init__(self, database_env: str = "DATABASE_URL", schema: str = "public") -> None:
        self.database_env = database_env
        self.schema = schema
        self.table = sql.Identifier(schema, "user")
        self._initialized = False
        self._lock = threading.Lock()

    def _connect(self) -> psycopg.Connection:
        dsn = os.environ.get(self.database_env)
        if not dsn:
            raise RuntimeError(f"{self.database_env} is required for user accounts")
        connection = psycopg.connect(dsn)
        self._ensure_table(connection)
        return connection

    def _ensure_table(self, connection: psycopg.Connection) -> None:
        with self._lock:
            if self._initialized:
                return
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"presto:{self.schema}.user",),
                )
                # Mirrors the "user" table in schema/schema.sql.
                connection.execute(sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        uid         SERIAL PRIMARY KEY,
                        name        TEXT NOT NULL,
                        username    TEXT NOT NULL UNIQUE,
                        email       TEXT NOT NULL UNIQUE,
                        password    TEXT NOT NULL,
                        superuser   INTEGER NOT NULL DEFAULT 0 CHECK (superuser IN (0, 1)),
                        pwd_updated TIMESTAMPTZ NOT NULL,
                        recovery    TIMESTAMPTZ,
                        created     TIMESTAMPTZ NOT NULL,
                        updated     TIMESTAMPTZ NOT NULL,
                        verified    TIMESTAMPTZ
                    )
                    """
                ).format(self.table))
            self._initialized = True

    def _one(self, query: sql.Composable, params: tuple[object, ...]) -> User | None:
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return User(*row) if row is not None else None

    def _select(self, where: str) -> sql.Composed:
        return sql.SQL("SELECT {} FROM {} WHERE " + where).format(
            sql.SQL(self._COLUMNS), self.table
        )

    def _update(self, assignments: str, where: str) -> sql.Composed:
        return sql.SQL(
            "UPDATE {} SET " + assignments + ", updated = now() WHERE " + where
            + " RETURNING {}"
        ).format(self.table, sql.SQL(self._COLUMNS))

    def get(self, uid: int) -> User | None:
        return self._one(self._select("uid = %s"), (uid,))

    def get_by_email(self, email: str) -> User | None:
        return self._one(self._select("lower(email) = lower(%s)"), (email,))

    def conflicts(self, username: str, email: str) -> set[str]:
        query = sql.SQL(
            "SELECT lower(username) = lower(%s), lower(email) = lower(%s) FROM {} "
            "WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)"
        ).format(self.table)
        with self._connect() as connection:
            rows = connection.execute(query, (username, email, username, email)).fetchall()
        fields = set()
        for username_taken, email_taken in rows:
            if username_taken:
                fields.add("username")
            if email_taken:
                fields.add("email")
        return fields

    def create(self, name: str, username: str, email: str, password_hash: str) -> User:
        """Create an unverified user with a pending confirmation timestamp."""
        query = sql.SQL(
            "INSERT INTO {} (name, username, email, password, pwd_updated, recovery, "
            "created, updated) VALUES (%s, %s, %s, %s, now(), now(), now(), now()) "
            "RETURNING {}"
        ).format(self.table, sql.SQL(self._COLUMNS))
        try:
            user = self._one(query, (name, username, email, password_hash))
        except psycopg.errors.UniqueViolation:
            raise DuplicateUserError(self.conflicts(username, email) or {"username"}) from None
        assert user is not None
        return user

    def start_recovery(self, uid: int) -> User | None:
        """Stamp a new confirmation/recovery time, superseding older links."""
        return self._one(self._update("recovery = now()", "uid = %s"), (uid,))

    def verify(self, uid: int, recovery: datetime) -> User | None:
        """Consume a confirmation link once; returns None if already consumed."""
        return self._one(
            self._update(
                "verified = COALESCE(verified, now()), recovery = NULL",
                "uid = %s AND recovery = %s",
            ),
            (uid, recovery),
        )

    def set_password(
        self, uid: int, password_hash: str, recovery: datetime | None = None
    ) -> User | None:
        """Change a password; with `recovery`, consume that recovery link once.

        Completing an emailed recovery also proves ownership of the address.
        """
        if recovery is None:
            query = self._update(
                "password = %s, pwd_updated = now(), recovery = NULL", "uid = %s"
            )
            return self._one(query, (password_hash, uid))
        query = self._update(
            "password = %s, pwd_updated = now(), recovery = NULL, "
            "verified = COALESCE(verified, now())",
            "uid = %s AND recovery = %s",
        )
        return self._one(query, (password_hash, uid, recovery))

    def delete(self, uid: int) -> bool:
        """Delete a user and their chatbot user memory, whose foreign key doesn't cascade."""
        memory_table = sql.Identifier(self.schema, "user_memory")  # see memory.Memory
        with self._connect() as connection, connection.transaction():
            exists = connection.execute(
                "SELECT to_regclass(%s) IS NOT NULL",
                (memory_table.as_string(connection),),
            ).fetchone()
            if exists is not None and exists[0]:
                connection.execute(
                    sql.SQL("DELETE FROM {} WHERE uid = %s").format(memory_table), (uid,)
                )
            query = sql.SQL("DELETE FROM {} WHERE uid = %s").format(self.table)
            return connection.execute(query, (uid,)).rowcount == 1


_store: UserStore | None = None


def store() -> UserStore:
    global _store
    if _store is None:
        _store = UserStore()
    return _store
