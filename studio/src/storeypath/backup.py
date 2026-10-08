"""Studio's whole database as one file, and a database from one.

A backup (``storeypath-backup-<UTC yyyymmdd-HHMMSS>.sql.gz``) is every table of
Studio's schema — the projects with their drawings and the packages exported of
them, the item types, the accounts (with the passwords' scrypt hashes: a restore
needs them), who each project is shared with, the audit log and every project's
history — as one moment saw them: read in one transaction (REPEATABLE READ), so a
change made meanwhile is either all in it or not at all, and nothing need wait for
it. Whoever may download one therefore holds every password's hash and the whole
audit log. No session goes in (everyone logs in again), nor who is editing which
floor; nor Studio's certificate and key (a Studio restored makes its own, and
browsers warn once about it), nor its cache.

The file is gzip'd SQL as psql reads it (COPY … FROM stdin blocks, in an order that
keeps every reference whole), its second line saying what it is: written as it is
read, so a large database needs no copy on the disk. ``restore`` loads one into an
empty database (refused for one with projects or accounts), and brings it up to
date. A backup of an older Studio (a .tar.gz of its data folder) is restored by
bringing that folder in (db/importer.py).
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import db as databases

FORMAT = "storeypath-backup"
FORMAT_VERSION = 1
FIRST_LINE = "-- StoreyPath Studio backup"
ROOT = "storeypath-data"  # every member of an older Studio's backup is under this folder
# in the order they are loaded: each after those it refers to
TABLES = ("users", "project_access", "grants", "audit", "projects", "locations", "buildings", "floors", "objects",
          "overrides", "items", "readings", "vision", "drawings", "exports", "catalogue", "settings", "history")
LEFT_OUT = ("sessions", "floor_locks", "migrations")  # who is logged in, who is editing: not kept
ORDER = {"history": "seq", "grants": "id", "audit": "id", "exports": "id"}
# drawings sent and not yet kept (their private information is still in them): not kept
WHERE = {"drawings": "NOT incoming"}


def backup_name(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"storeypath-backup-{now:%Y%m%d-%H%M%S}.sql.gz"


def _where(table: str) -> str:
    return f" WHERE {WHERE[table]}" if table in WHERE else ""


def _columns(conn, table: str) -> list[str]:
    return [c for (c,) in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "AND is_generated = 'NEVER' ORDER BY ordinal_position", (databases.SCHEMA, table))]


def write_backup(db, out) -> dict:
    """The database ``db`` (a db.Database) written to ``out`` (a file opened for
    writing, or anything with write()) as gzip'd SQL: how many tables, rows and bytes
    of data it holds."""
    counts = {"tables": 0, "rows": 0, "bytes": 0}
    with db.connection() as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            migration = databases.applied(conn)
            rows = {t: conn.execute(f"SELECT count(*) FROM {databases.SCHEMA}.{t}{_where(t)}").fetchone()[0]
                    for t in TABLES}
            about = {"format": FORMAT, "format_version": FORMAT_VERSION, "migration": migration,
                     "made_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "rows": rows}
            with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as z:
                z.write(f"{FIRST_LINE}\n-- {json.dumps(about)}\n".encode())
                z.write(b"SET client_encoding = 'UTF8';\nSET standard_conforming_strings = on;\n\n")
                for table in TABLES:
                    cols = ", ".join(_columns(conn, table))
                    z.write(f"COPY {databases.SCHEMA}.{table} ({cols}) FROM stdin;\n".encode())
                    order = f" ORDER BY {ORDER[table]}" if table in ORDER else ""
                    with conn.cursor().copy(f"COPY (SELECT {cols} FROM {databases.SCHEMA}.{table}{_where(table)}"
                                            f"{order}) TO STDOUT") as copy:
                        for data in copy:
                            z.write(data)
                            counts["bytes"] += len(data)
                    z.write(b"\\.\n\n")
                    counts["tables"] += 1
                    counts["rows"] += rows[table]
                # where each numbering (history's, the audit log's…) goes on from
                for name, value, called in conn.execute(
                        "SELECT sequencename, COALESCE(last_value, 1), last_value IS NOT NULL FROM pg_sequences "
                        "WHERE schemaname = %s ORDER BY sequencename", (databases.SCHEMA,)).fetchall():
                    z.write(f"SELECT pg_catalog.setval('{databases.SCHEMA}.{name}', {value}, "
                            f"{'true' if called else 'false'});\n".encode())
        finally:
            conn.rollback()
    return counts


def is_backup(archive: Path) -> bool:
    """Whether a file is a backup of this kind (not an older Studio's data folder)."""
    try:
        with gzip.open(archive, "rb") as f:
            return f.readline().decode("utf-8", "replace").strip() == FIRST_LINE
    except (OSError, EOFError):
        return False


def _empty(conn) -> bool:
    return not conn.execute(f"SELECT EXISTS (SELECT 1 FROM {databases.SCHEMA}.projects) OR "
                            f"EXISTS (SELECT 1 FROM {databases.SCHEMA}.users)").fetchone()[0]


def restore(archive: Path, url: str) -> dict:
    """A backup loaded into the database at ``url``, which must have no project and no
    account (ValueError when it does): everything as it was, and nobody logged in. A
    backup of an older Studio (a .tar.gz of its data folder) is brought in as its
    folder would be (db/importer.py). How many rows (or projects) it held."""
    archive = Path(archive)
    if not is_backup(archive):
        return _restore_folder(archive, url)
    with gzip.open(archive, "rb") as f:
        f.readline()
        about = json.loads(f.readline().decode().removeprefix("-- "))
        if about.get("format") != FORMAT:
            raise ValueError("not a StoreyPath backup")
        known = len(databases.migrations())
        if about["migration"] > known:
            raise ValueError(f"this backup was made by a newer Studio (migration {about['migration']}; "
                             f"this Studio knows {known}): update Studio")
        databases.migrate(url, upto=about["migration"])
        import psycopg

        rows = 0
        with psycopg.connect(url, options=f"-c search_path={databases.SCHEMA},public -c TimeZone=UTC") as conn:
            if not _empty(conn):
                raise ValueError("the database has projects or accounts already: restore into an empty one")
            lines = iter(f)
            for raw in lines:
                text = raw.decode("utf-8").strip()
                if not text or text.startswith("--") or text.startswith("SET "):
                    continue
                if text.startswith("COPY ") and text.endswith(" FROM stdin;"):
                    table = text.split()[1]
                    if table.split(".")[-1] not in TABLES:
                        raise ValueError(f"not a StoreyPath backup: {table}")
                    with conn.cursor().copy(text.removesuffix(" FROM stdin;") + " FROM STDIN") as copy:
                        for raw in lines:
                            if raw == b"\\.\n":
                                break
                            copy.write(raw)
                            rows += 1
                        else:
                            raise ValueError("the backup ends part way: it was not restored")
                elif text.startswith("SELECT pg_catalog.setval('"):
                    conn.execute(text)
                else:
                    raise ValueError(f"not a StoreyPath backup: {text[:60]!r}")
            conn.commit()
    databases.migrate(url)
    return {"rows": rows, "tables": about.get("rows", {})}


# ---- an older Studio's backup: its data folder -----------------------------------

def check_backup(archive: Path) -> list[tarfile.TarInfo]:
    """The members of an older Studio's backup, each named as it goes into the data
    folder; ValueError when any is not a regular file or folder under storeypath-data/,
    or has an absolute path or .. in it."""
    out = []
    with tarfile.open(archive, "r:*") as tar:
        for m in tar.getmembers():
            p = PurePosixPath(m.name)
            if p.is_absolute() or m.name.startswith("/") or "\\" in m.name or ".." in p.parts:
                raise ValueError(f"not a StoreyPath backup: {m.name!r} would land outside the folder")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"not a StoreyPath backup: {m.name!r} is a link or a device")
            if not p.parts or p.parts[0] != ROOT:
                raise ValueError(f"not a StoreyPath backup: {m.name!r} is not in {ROOT}/")
            out.append(m)
    if not out:
        raise ValueError("not a StoreyPath backup: it is empty")
    return out


def _restore_folder(archive: Path, url: str) -> dict:
    from .db.importer import import_folder

    try:
        check_backup(archive)
    except tarfile.TarError as e:
        raise ValueError(f"not a StoreyPath backup: {e}") from None
    db = databases.connect(url)
    with db.connection() as conn:
        if not _empty(conn):
            raise ValueError("the database has projects or accounts already: restore into an empty one")
    folder = Path(tempfile.mkdtemp(prefix="storeypath-restore-"))
    try:
        os.chmod(folder, 0o700)  # the accounts in it are the owner's alone
        with tarfile.open(archive, "r:*") as tar:
            chosen = []
            for m in tar.getmembers():
                p = PurePosixPath(m.name)
                if len(p.parts) == 1:
                    continue
                m.name = str(PurePosixPath(*p.parts[1:]))
                chosen.append(m)
            tar.extractall(folder, members=chosen, filter="data")
        report = import_folder(folder, db)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return {"projects": len(report["projects"]), "users": report["users"], "from": "an older Studio's data folder"}
