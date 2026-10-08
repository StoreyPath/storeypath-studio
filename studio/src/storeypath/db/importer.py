"""A data folder of an older Studio, brought into the database: ``storeypath db
import --data <folder>``, and the first start of a Studio whose database is empty.

What comes in: every project of the folder (``<code>/<code>.spproj``, or a *.spproj in
the folder itself), with its drawings (those its floors are read from, wherever they
are, and the others in its drawings/ folder, with the words read in them) and the
packages exported of it (exports/*.storeypath: kept with their export records, or on
their own); the item types (catalogue.json); and studio.db: the accounts, who owns
each project and who it is shared with, and the audit log (not the sessions: everyone
logs in again).

It may be run again: a project the database has already is left as it is (its code
is listed as skipped), as is a user of that id or username. Nothing in the folder is
changed or deleted.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from psycopg.types.json import Jsonb

from ..workspace import Workspace
from .store import COMMAND_LINE, ProjectStore

IMPORT_LOCK = 0x5370_0003  # pg_advisory_lock key: one import at a time
DRAWING_TYPES = (".dxf", ".dwg")
WORDS = ".words.txt"
HIDDEN = (".incoming-", ".writing-", ".opening-", ".unpacking-", ".replaced-")


def project_files(data: Path) -> list[Path]:
    """The workspace files of a data folder, as Studio found its projects: a *.spproj in
    it, or in a folder of it (none in a hidden one)."""
    data = Path(data)
    found = []
    for path in sorted(data.glob("*.spproj")) + sorted(data.glob("*/*.spproj")):
        if any(part.startswith(".") for part in path.relative_to(data).parts):
            continue
        found.append(path)
    return found


def has_projects(data: Path) -> bool:
    return bool(project_files(data))


def first_start(data: Path, db, store: ProjectStore | None = None) -> dict | None:
    """At Studio's start: the folder's projects brought in when the database has none
    and the folder has some; its accounts when the database has no user and the
    folder has studio.db. What was brought in, or None when nothing was."""
    data = Path(data)
    store = store or ProjectStore(db)
    with db.connection() as conn:
        no_projects = conn.execute("SELECT NOT EXISTS (SELECT 1 FROM projects)").fetchone()[0]
        no_users = conn.execute("SELECT NOT EXISTS (SELECT 1 FROM users)").fetchone()[0]
    projects = no_projects and has_projects(data)
    accounts = no_users and (data / "studio.db").is_file()
    if not (projects or accounts):
        return None
    return import_folder(data, db, store, projects=projects, accounts=accounts)


def import_folder(data: Path, db, store: ProjectStore | None = None, *, projects: bool = True,
                  accounts: bool = True, say=lambda line: None) -> dict:
    """Everything of an older Studio's data folder brought into the database (see the
    module): what came in, and what was left (skipped, with why)."""
    data = Path(data)
    if not data.is_dir():
        raise ValueError(f"{data} is not a folder")
    store = store or ProjectStore(db)
    report = {"projects": [], "skipped": [], "drawings": 0, "packages": 0, "catalogue": False,
              "users": 0, "grants": 0, "audit": 0, "notes": []}
    with db.connection() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (IMPORT_LOCK,))
        try:
            if accounts and (data / "studio.db").is_file():
                _accounts(data / "studio.db", db, report, say)
            if projects:
                _catalogue(data, store, report, say)
                for path in project_files(data):
                    try:
                        _project(path, store, report, say)
                    except Exception as e:  # one that cannot be read leaves the others
                        report["skipped"].append({"file": str(path), "why": f"{type(e).__name__}: {e}"})
                        say(f"{path}: not brought in: {e}")
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (IMPORT_LOCK,))
            conn.commit()
    return report


def _catalogue(data: Path, store: ProjectStore, report: dict, say) -> None:
    from .. import catalogue

    path = data / catalogue.FILE_NAME
    with store.db.connection() as conn:
        have = conn.execute("SELECT 1 FROM settings WHERE key = 'catalogue'").fetchone()
    if have or not path.is_file():
        return
    cat, _ = catalogue.read(path.read_text(encoding="utf-8"))
    cat.check()
    store.save_catalogue(cat)
    store.catalogue()  # with the default types a newer Studio brings
    report["catalogue"] = True
    say(f"item types: {len(cat.types)} from {path.name}")


def _project(path: Path, store: ProjectStore, report: dict, say) -> None:
    from ..profile import AUTO, builtin_profiles

    ws = Workspace.load(path)
    if store.exists(ws.id):
        report["skipped"].append({"code": ws.id, "file": str(path), "why": "in the database already"})
        say(f"{ws.id}: in the database already: left as it is")
        return
    folder = path.parent
    root = folder.resolve()
    drawings: dict[str, tuple[bytes, str | None]] = {}
    paths: dict[Path, str] = {}  # a file -> its name in the project's drawings

    def keep(file: Path) -> str:
        file = file.resolve()
        if file in paths:
            return paths[file]
        name, n = file.name, 2
        while name in drawings:
            name, n = f"{file.stem}-{n}{file.suffix}", n + 1
        words = file.with_name(file.name + WORDS)
        drawings[name] = (file.read_bytes(), words.read_text(encoding="utf-8", errors="replace")
                          if words.is_file() else None)
        paths[file] = name
        return name

    builtin = set(builtin_profiles()) | {AUTO}
    for *_, f, f_id in ws.iter_floors():
        if f.source is None:
            continue
        file = Path(f.source.path) if Path(f.source.path).is_absolute() else folder / f.source.path
        if file.is_file():
            f.source.path = f"drawings/{keep(file)}"
        else:
            name = Path(f.source.path.replace("\\", "/")).name or "drawing"
            f.source.path = f"drawings/{name}"
            report["notes"].append(f"{f_id}: its drawing {file} is not there: add it to read the floor again")
        if f.source.profile not in builtin:  # a profile file of the folder's: the drawing's own layers
            report["notes"].append(f"{f_id}: read with its own layers (auto) in place of {f.source.profile}")
            f.source.profile = AUTO
    if (folder / "drawings").is_dir():
        for file in sorted((folder / "drawings").iterdir()):
            if file.is_file() and file.suffix.lower() in DRAWING_TYPES and not file.name.startswith(HIDDEN) \
                    and not file.name.startswith(".") and file.resolve().is_relative_to(root):
                keep(file)
    packages: dict[str, bytes] = {}
    loose: dict[str, bytes] = {}
    recorded = {r.file for r in ws.exports}
    if (folder / "exports").is_dir():
        for file in sorted((folder / "exports").glob("*.storeypath")):
            if file.name.startswith(HIDDEN) or not file.is_file():
                continue
            (packages if file.name in recorded else loose)[file.name] = file.read_bytes()
    store.create(ws, by=COMMAND_LINE, kind="import", drawings=drawings, export_bytes=packages)
    for name, blob in loose.items():
        store.keep_package(ws.id, name, blob)
    report["projects"].append(ws.id)
    report["drawings"] += len(drawings)
    report["packages"] += len(packages) + len(loose)
    say(f"{ws.id} ({ws.project.name}): {sum(1 for _ in ws.iter_floors())} floors, {len(drawings)} drawings, "
        f"{len(packages) + len(loose)} packages")


def _accounts(path: Path, db, report: dict, say) -> None:
    """studio.db's accounts, owners, grants and audit log (its sessions are not kept)."""
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        with db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (0x5370_0002,))  # accounts.ACCOUNTS_LOCK
            if "users" in tables:
                for r in source.execute("SELECT * FROM users"):
                    done = conn.execute(
                        "INSERT INTO users (id, username, name, role, capabilities, active, password, "
                        "must_change_password, created_at, password_changed_at, last_login_at, session_epoch) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (r["id"], r["username"], r["name"], r["role"], Jsonb(json.loads(r["capabilities"] or "[]")),
                         bool(r["active"]), r["password"], bool(r["must_change_password"]), r["created_at"],
                         r["password_changed_at"], r["last_login_at"], r["session_epoch"] or 0)).rowcount
                    report["users"] += done
                    if not done:
                        say(f"user {r['username']}: the database has a user of that id or username: left as it is")
            known = {u for (u,) in conn.execute("SELECT id FROM users")}
            if "project_access" in tables:
                for r in source.execute("SELECT * FROM project_access"):
                    conn.execute("INSERT INTO project_access (code, owner) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                 (r["code"], r["owner"] if r["owner"] in known else None))
            if "grants" in tables:
                for r in source.execute("SELECT * FROM grants ORDER BY given_at, rowid"):
                    if r["user"] not in known:
                        continue
                    report["grants"] += conn.execute(
                        "INSERT INTO grants (project, scope_kind, scope_id, user_id, level, given_by, given_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (r["project"], r["scope_kind"], r["scope_id"] or "", r["user"], r["level"],
                         r["given_by"] if r["given_by"] in known else None, r["given_at"])).rowcount
            done = conn.execute("SELECT 1 FROM settings WHERE key = 'imported audit'").fetchone()
            if "audit" in tables and not done:
                for r in source.execute("SELECT * FROM audit ORDER BY id"):
                    conn.execute("INSERT INTO audit (at, user_id, username, address, action, target, outcome, "
                                 "details) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                                 (r["at"], r["user_id"], r["username"], r["address"], r["action"], r["target"],
                                  r["outcome"], Jsonb(json.loads(r["details"] or "{}"))))
                    report["audit"] += 1
                conn.execute("INSERT INTO settings (key, value) VALUES ('imported audit', %s)",
                             (Jsonb({"from": os.path.abspath(path)}),))
    finally:
        source.close()
    say(f"accounts: {report['users']} users, {report['grants']} grants, {report['audit']} audit entries")
