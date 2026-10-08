"""A data folder as an older Studio kept it: its accounts in studio.db (SQLite), its
projects as workspace files beside their drawings and packages."""

import json
import sqlite3
from pathlib import Path

from storeypath.accounts import hash_password

SCHEMA = """
CREATE TABLE users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK (role IN ('admin', 'engineer', 'user')), capabilities TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1, password TEXT NOT NULL, must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, password_changed_at TEXT, last_login_at TEXT, session_epoch INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE project_access (code TEXT PRIMARY KEY, owner TEXT REFERENCES users(id));
CREATE TABLE grants (
    project TEXT NOT NULL REFERENCES project_access(code) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '', user TEXT NOT NULL REFERENCES users(id),
    level TEXT NOT NULL, given_by TEXT REFERENCES users(id), given_at TEXT NOT NULL,
    UNIQUE (project, scope_kind, scope_id, user)
);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY, user TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
    last_seen REAL NOT NULL, expires REAL NOT NULL, epoch INTEGER NOT NULL, address TEXT
);
CREATE TABLE audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, user_id TEXT, username TEXT, address TEXT,
    action TEXT NOT NULL, target TEXT, outcome TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}'
);
"""
AT = "2026-09-01T08:00:00+00:00"


def studio_db(data: Path, password: str, owner_of=(), shared=()) -> dict:
    """studio.db with an admin (boss), an engineer (eng) and a user (vera), each with
    ``password``; boss owns each project of ``owner_of``; vera may view each
    (code, scope kind, scope id) of ``shared``; a login in the audit log, and a session
    (never brought in). The users' ids."""
    ids = {"boss": "U0000000BOSS", "eng": "U00000000ENG", "vera": "U0000000VERA"}
    with sqlite3.connect(Path(data) / "studio.db") as db:
        db.executescript(SCHEMA)
        for name, role in (("boss", "admin"), ("eng", "engineer"), ("vera", "user")):
            db.execute("INSERT INTO users (id, username, name, role, capabilities, password, created_at, "
                       "password_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (ids[name], name, name.title(), role, json.dumps(["backup"] if name == "eng" else []),
                        hash_password(password), AT, AT))
        for code in owner_of:
            db.execute("INSERT INTO project_access (code, owner) VALUES (?, ?)", (code, ids["boss"]))
        for code, kind, scope_id in shared:
            db.execute("INSERT OR IGNORE INTO project_access (code) VALUES (?)", (code,))
            db.execute("INSERT INTO grants (project, scope_kind, scope_id, user, level, given_by, given_at) "
                       "VALUES (?, ?, ?, ?, 'view', ?, ?)", (code, kind, scope_id or "", ids["vera"], ids["boss"], AT))
        db.execute("INSERT INTO audit (at, user_id, username, address, action, outcome, details) "
                   "VALUES (?, ?, 'boss', '10.0.0.1', 'login', 'ok', '{}')", (AT, ids["boss"]))
        db.execute("INSERT INTO sessions VALUES ('abc', ?, 1, 1, 9e12, 0, '10.0.0.1')", (ids["boss"],))
    return ids
