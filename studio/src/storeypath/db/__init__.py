"""Studio's own database: PostgreSQL with PostGIS, the system of record.

Everything of a project is in it — its tree, objects, corrections, items, the drawings
as sent and the packages as exported — with the accounts, the organization's item
types and the history of every change (schema ``storeypath``). On disk there is only
what can be made again: caches (prints of floors, models' files) and the server's
certificate.

Where it is: ``STOREYPATH_DATABASE_URL`` (in the studio image, its own PostgreSQL over a
Unix socket, ``postgresql:///storeypath?host=/run/postgresql``); without it, libpq's
defaults (the local server, the user's own database). One process keeps one
connection pool per database (connect), whatever opens it: Studio's projects
(store.ProjectStore) and accounts (accounts.Accounts) share it.

The schema is brought up to date when the database is first opened: the numbered SQL
files of migrations/ not applied yet, in order, under an advisory lock (two Studios
starting at once apply each once), each recorded in ``storeypath.migrations``.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

URL_ENV = "STOREYPATH_DATABASE_URL"
DEFAULT_URL = "postgresql:///storeypath"  # libpq's own defaults: the local server's socket
SCHEMA = "storeypath"
CHANNEL = "storeypath_changes"  # NOTIFY {project, floors, seq, version} after every change
MIGRATION_LOCK = 0x5370_0001  # pg_advisory_lock key: one Studio brings the schema up to date at a time
POOL_MIN, POOL_MAX = 1, 16
POOL_TIMEOUT_S = 30.0  # a request waits this long for a connection, then fails
MIGRATION_NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")


class DatabaseError(RuntimeError):
    """The database cannot be used: not reached, or made by a newer Studio."""


def default_url(data: str | Path | None = None) -> str:
    """The database of the Studio whose data folder is ``data``: STOREYPATH_DATABASE_URL,
    else libpq's defaults. (The tests give each data folder a database of its own.)"""
    return os.environ.get(URL_ENV) or DEFAULT_URL


def shown(url: str) -> str:
    """A database's URL as it may be shown: without its password."""
    return re.sub(r"(://[^:/@]+):[^@/]*@", r"\1:***@", url)


class Database:
    """A PostgreSQL database with Studio's schema: a pool of connections, each in UTC
    with ``storeypath`` first on its search path."""

    def __init__(self, url: str, min_size: int = POOL_MIN, max_size: int = POOL_MAX, migrate: bool = True):
        self.url = url
        self.pool = ConnectionPool(
            url, min_size=min_size, max_size=max_size, timeout=POOL_TIMEOUT_S, open=False,
            kwargs={"options": f"-c search_path={SCHEMA},public -c TimeZone=UTC"},
            name="storeypath")
        try:
            self.pool.open(wait=True, timeout=POOL_TIMEOUT_S)
        except Exception as e:
            self.pool.close()
            raise DatabaseError(f"cannot reach the database {shown(url)}: {e}") from None
        if migrate:
            self.migrate()

    @contextmanager
    def connection(self):
        """A connection of the pool, given back after (its transaction committed, or rolled
        back when what was done with it failed)."""
        with self.pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self):
        """A connection in a transaction: committed when the block ends, rolled back when
        it raises."""
        with self.pool.connection() as conn:
            with conn.transaction():
                yield conn

    def migrate(self) -> list[str]:
        """The migrations not applied yet, applied in order (the schema first made when
        there is none). Their names."""
        return migrate(self.url)

    def close(self) -> None:
        self.pool.close()


def migrations() -> list[tuple[int, str, str]]:
    """(number, name, SQL) of every migration, in order."""
    folder = resources.files("storeypath.db") / "migrations"
    out = []
    for entry in folder.iterdir():
        m = MIGRATION_NAME.fullmatch(entry.name)
        if m:
            out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    out.sort()
    if [n for n, *_ in out] != list(range(1, len(out) + 1)):
        raise DatabaseError("the migrations are not numbered 0001, 0002, … with none missing")
    return out


def migrate(url: str, upto: int | None = None) -> list[str]:
    """Bring the schema of the database at ``url`` up to date (or up to migration
    ``upto``), under an advisory lock: the names of the migrations applied now. Refused
    (DatabaseError) when the database was brought further by a newer Studio."""
    known = migrations()
    upto = len(known) if upto is None else upto
    done = []
    with psycopg.connect(url, autocommit=True, options="-c TimeZone=UTC") as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        try:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            conn.execute(f"CREATE TABLE IF NOT EXISTS {SCHEMA}.migrations (number integer PRIMARY KEY, "
                         "name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
            have = {n for (n,) in conn.execute(f"SELECT number FROM {SCHEMA}.migrations")}
            if have and max(have) > len(known):
                raise DatabaseError(f"the database {shown(url)} was brought up to date by a newer Studio "
                                    f"(migration {max(have)}; this Studio knows {len(known)}): update Studio")
            for number, name, sql in known[:upto]:
                if number in have:
                    continue
                with conn.transaction():
                    conn.execute(f"SET LOCAL search_path = {SCHEMA}, public")
                    conn.execute(sql)
                    conn.execute(f"INSERT INTO {SCHEMA}.migrations (number, name) VALUES (%s, %s)", (number, name))
                done.append(name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
    return done


def applied(conn) -> int:
    """The number of the last migration the database has."""
    row = conn.execute(f"SELECT max(number) FROM {SCHEMA}.migrations").fetchone()
    return row[0] or 0


_open: dict[str, Database] = {}
_open_lock = threading.Lock()


def connect(url: str | None = None, data: str | Path | None = None) -> Database:
    """The database at ``url`` (default_url(data) when not given), opened once a process
    and shared: its schema brought up to date the first time."""
    url = url or default_url(data)
    with _open_lock:
        db = _open.get(url)
        if db is None:
            db = _open[url] = Database(url)
        return db


def forget(url: str) -> None:
    """The pool of the database at ``url`` closed and forgotten (a database dropped)."""
    with _open_lock:
        db = _open.pop(url, None)
    if db is not None:
        db.close()
