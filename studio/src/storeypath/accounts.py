"""Who may use Studio, and what each person may see and change: accounts, sessions,
sharing and an audit log, kept in one SQLite file of the data folder, ``studio.db``
(mode 0600). The projects stay files, as they always were; only who may use them is
in the database.

    users           the accounts: name, role, capabilities, password (scrypt), whether active
    project_access  per project (by its code): its owner
    grants          who a project is shared with, on what part of it, how far
    sessions        who is logged in: the SHA-256 of each session's token (never the token)
    audit           what was done that matters: logins, users, sharing, projects created,
                    opened and deleted, exports, backups

Roles: an admin manages users and sees and does everything; an engineer may create
projects and open files as new projects, and owns what they create; a user sees only
what is shared with them. Capabilities an admin may give anyone: ``backup`` (download
the whole data folder) and ``catalogue`` (change the item types); admins have both.

A project is shared with a person on a scope (the whole project, one of its buildings,
one of its floors) at a level: view < edit < share, each including the ones before.
A grant on a project covers all its buildings and floors, ones added later too; on a
building, all its floors. A person's level on a floor is the highest of their grants
on the floor, its building and its project (Sight). The owner has share on the whole
project, an admin everywhere. Scopes are resolved through the workspace (a floor's
building is the building it is in), never by how IDs begin.

The command line (``storeypath users``) writes the same file while Studio runs: SQLite
keeps the two apart (WAL, short transactions, a busy timeout). Sessions are kept too, so
a restart logs nobody out; a session ends when not used for IDLE_S, after ABSOLUTE_S,
on logout, and when its user's ``session_epoch`` is raised (a password, role or
capability changed, the account disabled). The schema has a version (PRAGMA
user_version) and is brought up to date, in order, when Studio or the command line
opens it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DB_FILE = "studio.db"
ROLES = ("admin", "engineer", "user")
CAPABILITIES = ("backup", "catalogue")
LEVELS = ("view", "edit", "share")  # each includes the ones before it
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,31}")
MIN_PASSWORD = 10
MAX_PASSWORD = 1024
# scrypt: 32 MiB of memory a try (128·r·N), about a tenth of a second
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN = 2**15, 8, 1, 32
SCRYPT_MAXMEM = 256 * 1024 * 1024
IDLE_S = 8 * 3600  # a session not used for this long ends…
ABSOLUTE_S = 7 * 24 * 3600  # …and every session after this long
SEEN_EVERY_S = 60  # a session's last use is written at most this often
THROTTLE_WINDOW_S = 15 * 60  # failed logins counted over this long
USER_FAILURES = 5  # failed logins for one username from one address, then it waits
ADDRESS_FAILURES = 20  # failed logins from one address, then it waits
THROTTLE_FIRST_S = 30  # the first wait; each failure after it doubles it…
THROTTLE_MAX_S = 15 * 60  # …up to this
FAILURE_KEYS_MAX = 10_000  # failed-login counts kept at most (beyond it, the oldest go)
LONGEST_USERNAME_TRIED = 64  # a longer one is no username: refused before anything else, never kept
PASSWORD_CHECKS = 4  # passwords checked at once (scrypt: 32 MiB each)…
PASSWORD_WAIT_S = 5.0  # …one waits this long for its turn, then is answered 503
BUSY_TIMEOUT_MS = 5000  # a writer waits this long for another (Studio, the command line)
COOKIE = "sp_session"
ADMIN_PASSWORD_ENV = "STOREYPATH_ADMIN_PASSWORD"  # unattended first start: the first admin
ADMIN_USER_ENV = "STOREYPATH_ADMIN_USER"
TEMPORARY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: read out, typed in


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def cookie_name(port) -> str:
    """The session cookie of a Studio reached on ``port`` (sp_session_8080): browsers keep
    one host's cookies for all its ports, so two Studios on one machine keep their own."""
    return f"{COOKIE}_{port}"


# ---- refusals -------------------------------------------------------------------

class Refused(Exception):
    """A request refused: its HTTP status, what to say, and anything more the page needs."""

    status = 403

    def __init__(self, message: str, **more):
        super().__init__(message)
        self.more = more


class Unauthorized(Refused):
    """Not logged in (401): the pages go to the login page."""

    status = 401


class Forbidden(Refused):
    """Logged in, and not allowed (403)."""

    status = 403


class Gone(Refused):
    """What is no longer there (404): the setup, once Studio has a user."""

    status = 404


class Throttled(Refused):
    """Too many failed logins: try again in ``retry_after`` seconds (429)."""

    status = 429


class Overloaded(Refused):
    """Too many passwords being checked at once: try again in ``retry_after`` seconds (503)."""

    status = 503


# ---- passwords ------------------------------------------------------------------

def hash_password(password: str) -> str:
    """``scrypt$N$r$p$salt$hash`` (salt and hash in base64): kept, never the password."""
    salt = secrets.token_bytes(16)
    n, r, p = SCRYPT_N, SCRYPT_R, SCRYPT_P
    key = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=SCRYPT_LEN, maxmem=SCRYPT_MAXMEM)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` is the one ``stored`` was made from (compared in constant time)."""
    try:
        kind, n, r, p, salt, key = stored.split("$")
        if kind != "scrypt":
            return False
        want = base64.b64decode(key)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(want), maxmem=SCRYPT_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def check_password(password, username: str) -> str:
    """A password a person may have: at least MIN_PASSWORD characters, and not their
    username. ValueError, saying why, when it is not."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise ValueError(f"a password has at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"a password has at most {MAX_PASSWORD} characters")
    if password.strip().lower() == username.lower():
        raise ValueError("a password is not the username")
    return password


def temporary_password() -> str:
    """A password an admin gives (a new user, a reset), to be changed at the first login:
    four groups of four letters and digits that cannot be mistaken for one another."""
    return "-".join("".join(secrets.choice(TEMPORARY_ALPHABET) for _ in range(4)) for _ in range(4))


def check_username(username) -> str:
    """A username as kept: lower case, 2 to 32 letters, digits, dots, dashes and
    underscores, starting with a letter or digit. It is never a file's name."""
    name = (username or "").strip().lower() if isinstance(username, str) else ""
    if not USERNAME.fullmatch(name):
        raise ValueError("a username is 2 to 32 letters, digits, '.', '-' or '_', starting with a letter or digit")
    return name


# ---- what is kept ------------------------------------------------------------------

class User(BaseModel):
    id: str  # never changes: grants and the audit log refer to it
    username: str
    name: str = ""
    role: Literal["admin", "engineer", "user"] = "user"
    capabilities: list[Literal["backup", "catalogue"]] = Field(default_factory=list)
    active: bool = True
    password: str
    must_change_password: bool = False
    created_at: str = Field(default_factory=utcnow)
    password_changed_at: str | None = None
    last_login_at: str | None = None
    session_epoch: int = 0

    def can(self, capability: str) -> bool:
        return self.role == "admin" or capability in self.capabilities

    def view(self, full: bool = False) -> dict:
        """What the pages are told of a person: who they are; ``full``, for an admin, all
        but the password."""
        out = {"id": self.id, "username": self.username, "name": self.name or self.username}
        if full:
            out.update(self.model_dump(exclude={"password", "id", "username", "name"}))
        return out


class Scope(BaseModel):
    """What a grant covers: the whole project, one building, or one floor (by its ID)."""

    kind: Literal["project", "building", "floor"]
    id: str | None = None


class Grant(BaseModel):
    user: str  # a user's id
    scope: Scope
    level: Literal["view", "edit", "share"]
    by: str | None = None  # who gave it (a user's id)
    at: str = Field(default_factory=utcnow)


class ProjectAccess(BaseModel):
    owner: str | None = None  # None: made before accounts; admins manage it until it has an owner
    grants: list[Grant] = Field(default_factory=list)


# the person when Studio runs without accounts (storeypath review, on this computer alone)
LOCAL = User(id="local", username="local", name="This computer", role="admin", password="!")


# ---- the database ------------------------------------------------------------------

# Each brings the schema from the version before it to its own (PRAGMA user_version is
# the number of them applied). Never changed once released: a change is a new one.
MIGRATIONS = [
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL CHECK (role IN ('admin', 'engineer', 'user')),
        capabilities TEXT NOT NULL DEFAULT '[]',
        active INTEGER NOT NULL DEFAULT 1,
        password TEXT NOT NULL,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        password_changed_at TEXT,
        last_login_at TEXT,
        session_epoch INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE project_access (
        code TEXT PRIMARY KEY,
        owner TEXT REFERENCES users(id)
    );
    CREATE TABLE grants (
        project TEXT NOT NULL REFERENCES project_access(code) ON DELETE CASCADE,
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('project', 'building', 'floor')),
        scope_id TEXT NOT NULL DEFAULT '',
        user TEXT NOT NULL REFERENCES users(id),
        level TEXT NOT NULL CHECK (level IN ('view', 'edit', 'share')),
        given_by TEXT REFERENCES users(id),
        given_at TEXT NOT NULL,
        UNIQUE (project, scope_kind, scope_id, user)
    );
    CREATE INDEX grants_of_user ON grants (user);
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user TEXT NOT NULL REFERENCES users(id),
        created REAL NOT NULL,
        last_seen REAL NOT NULL,
        expires REAL NOT NULL,
        epoch INTEGER NOT NULL,
        address TEXT
    );
    CREATE INDEX sessions_of_user ON sessions (user);
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        user_id TEXT,
        username TEXT,
        address TEXT,
        action TEXT NOT NULL,
        target TEXT,
        outcome TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '{}'
    );
    """,
]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _user(row) -> User:
    return User(id=row["id"], username=row["username"], name=row["name"], role=row["role"],
                capabilities=json.loads(row["capabilities"]), active=bool(row["active"]), password=row["password"],
                must_change_password=bool(row["must_change_password"]), created_at=row["created_at"],
                password_changed_at=row["password_changed_at"], last_login_at=row["last_login_at"],
                session_epoch=row["session_epoch"])


class Accounts:
    """The accounts, sessions, sharing and audit log of one data folder (its studio.db)."""

    def __init__(self, data: str | Path, clock=time.time):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / DB_FILE
        self.clock = clock
        self._failures: dict[tuple, list[float]] = {}  # a throttle's key -> when its tries failed
        self._noted: dict[tuple, float] = {}  # a throttle's key -> when a wait for it was last audited
        self._pruned = 0.0
        self._lock = threading.Lock()  # the failures, and the setup token
        self._checks = threading.BoundedSemaphore(PASSWORD_CHECKS)  # passwords checked at once
        self._setup: str | None = None
        self._dummy: str | None = None
        if not self.path.exists():  # the accounts' file is the owner's alone
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    @contextmanager
    def _connect(self):
        """A connection of its own (Studio answers each request on a thread of its own):
        foreign keys on, waiting BUSY_TIMEOUT_MS for another writer; closed after."""
        db = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            yield db
        finally:
            db.close()

    @contextmanager
    def _write(self):
        """A short transaction that writes: it holds the write lock from its start, so what
        it reads is still so when it writes (BEGIN IMMEDIATE); undone when it fails."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    def _migrate(self) -> None:
        with self._write() as db:
            have = db.execute("PRAGMA user_version").fetchone()[0]
            if have > len(MIGRATIONS):
                raise RuntimeError(f"{self.path} was made by a newer Studio (schema {have}): update Studio")
            for n, script in enumerate(MIGRATIONS[have:], start=have + 1):
                for statement in (s.strip() for s in script.split(";")):
                    if statement:
                        db.execute(statement)
                db.execute(f"PRAGMA user_version={n}")

    def _read(self, sql: str, args=()) -> list:
        with self._connect() as db:
            return db.execute(sql, args).fetchall()

    # ---- users ------------------------------------------------------------------

    def users(self) -> list[User]:
        return [_user(r) for r in self._read("SELECT * FROM users ORDER BY username")]

    def user(self, user_id: str | None) -> User | None:
        rows = self._read("SELECT * FROM users WHERE id = ?", (user_id,)) if isinstance(user_id, str) else []
        return _user(rows[0]) if rows else None

    def by_username(self, username: str) -> User | None:
        name = (username or "").strip().lower() if isinstance(username, str) else ""
        rows = self._read("SELECT * FROM users WHERE username = ?", (name,))
        return _user(rows[0]) if rows else None

    def has_users(self) -> bool:
        return bool(self._read("SELECT 1 FROM users LIMIT 1"))

    def add_user(self, username: str, password: str, *, name: str = "", role: str = "user",
                 capabilities=(), must_change_password: bool = True) -> User:
        """A new account. ``must_change_password``: the password is a temporary one (an
        admin's), changed at the first login."""
        username = check_username(username)
        check_password(password, username)
        role, capabilities = _role(role), _capabilities(capabilities)
        hashed = hash_password(password)  # before the transaction: it takes a while
        now = utcnow()
        with self._write() as db:
            if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                raise ValueError(f"there is a user {username} already")
            taken = {r[0] for r in db.execute("SELECT id FROM users")}
            uid = _new_id(taken)
            db.execute("INSERT INTO users (id, username, name, role, capabilities, active, password, "
                       "must_change_password, created_at, password_changed_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                       (uid, username, _name(name) or username, role, json.dumps(capabilities), hashed,
                        int(must_change_password), now, now))
        return self.user(uid)

    def update_user(self, user_id: str, *, name=None, role=None, capabilities=None, active=None) -> User:
        """A user's name, role, capabilities or whether they may log in, changed. A role
        or capabilities changed, or the account disabled, ends their sessions. Refused
        when it would leave no admin who can log in."""
        with self._write() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(f"no user {user_id}")
            user = _user(row)
            before = (user.role, sorted(user.capabilities), user.active)
            if name is not None:
                user.name = _name(name) or user.username
            if role is not None:
                user.role = _role(role)
            if capabilities is not None:
                user.capabilities = _capabilities(capabilities)
            if active is not None:
                if not isinstance(active, bool):
                    raise ValueError("active is true or false")
                user.active = active
            ended = (user.role, sorted(user.capabilities), user.active) != before
            db.execute("UPDATE users SET name = ?, role = ?, capabilities = ?, active = ?, "
                       "session_epoch = session_epoch + ? WHERE id = ?",
                       (user.name, user.role, json.dumps(user.capabilities), int(user.active), int(ended), user_id))
            if not db.execute("SELECT 1 FROM users WHERE role = 'admin' AND active = 1").fetchone():
                raise ValueError("Studio needs an admin who can log in: make another admin first")
            if ended:
                db.execute("DELETE FROM sessions WHERE user = ?", (user_id,))
        return self.user(user_id)

    def set_password(self, user_id: str, password: str, *, temporary: bool) -> User:
        """A user's password set: by an admin (``temporary``: changed at the next login), or
        by themselves. Every session of theirs ends."""
        user = self.user(user_id)
        if user is None:
            raise KeyError(f"no user {user_id}")
        check_password(password, user.username)
        hashed = hash_password(password)
        with self._write() as db:
            db.execute("UPDATE users SET password = ?, must_change_password = ?, password_changed_at = ?, "
                       "session_epoch = session_epoch + 1 WHERE id = ?", (hashed, int(temporary), utcnow(), user_id))
            db.execute("DELETE FROM sessions WHERE user = ?", (user_id,))
        return self.user(user_id)

    # ---- logging in ---------------------------------------------------------------

    def login(self, username, password, address: str = "") -> tuple[str, User]:
        """A new session (its token) for the person whose username and password these
        are. The same answer for a username not known as for a wrong password, after
        the same work (a password is checked either way). Throttled (_try): after
        USER_FAILURES failed logins for a username from one address, or ADDRESS_FAILURES
        from one address, within THROTTLE_WINDOW_S, each try waits (Throttled), longer
        each time; a person's own logins never wait for someone else's wrong passwords.
        A "username" longer than LONGEST_USERNAME_TRIED is none: refused as a wrong one
        without a password checked, kept (in the audit) only cut short."""
        if isinstance(username, str) and len(username) > LONGEST_USERNAME_TRIED:
            keys = [("address", _throttled_as(address))]
            with self._try("login", keys, address, username[:24] + "…"):
                raise Unauthorized("wrong username or password")
        name = username.strip().lower() if isinstance(username, str) else ""
        with self._try("login", self._keys(name, address), address, name or None) as attempt:
            user = self.by_username(name)
            with self._checking():
                ok = verify_password(password if isinstance(password, str) else "",
                                     user.password if user else self._dummy_hash())
            if not (ok and user is not None and user.active):
                attempt.user = user
                raise Unauthorized("wrong username or password")
            attempt.ok()
        with self._write() as db:
            db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utcnow(), user.id))
        self.audit("login", user, address)
        return self.start_session(user, address), user

    def _dummy_hash(self) -> str:
        if self._dummy is None:
            self._dummy = hash_password(secrets.token_urlsafe(12))
        return self._dummy

    @staticmethod
    def _keys(username: str, address: str) -> list[tuple]:
        """The counts a password tried for ``username`` from ``address`` goes into: that
        username from that address (no one else's wrong passwords make its person wait),
        and that address."""
        where = _throttled_as(address)
        return [("user", username, where), ("address", where)]

    @contextmanager
    def _checking(self):
        """A turn to check a password (scrypt: 32 MiB of memory each): PASSWORD_CHECKS at
        once; one that waits PASSWORD_WAIT_S for its turn is answered 503 (Overloaded)."""
        if not self._checks.acquire(timeout=PASSWORD_WAIT_S):
            raise Overloaded("Studio is checking many passwords at once: try again in a moment", retry_after=2)
        try:
            yield
        finally:
            self._checks.release()

    @contextmanager
    def _try(self, action: str, keys: list[tuple], address: str, target):
        """A password tried (a login, a password change): counted as a failure for each of
        ``keys`` before it is checked, so that many tried at once never pass the limits
        together, and taken back when it is right (``ok()``) or was not checked at all
        (Overloaded). Raises Throttled, before anything is checked, while the failures
        counted say to wait (written in the audit once a window for each key that
        waits). A try ending in Unauthorized or Forbidden is written in the audit as
        failed (by its ``user``, when known)."""
        attempt = _Attempt()
        now = self.clock()
        with self._lock:
            wait, waiting = self._waiting(keys, now)
            if not wait:
                for key in keys:
                    self._failures.setdefault(key, []).append(now)
                self._prune(now)
            note = bool(wait) and now - self._noted.get(waiting, -THROTTLE_WINDOW_S) >= THROTTLE_WINDOW_S
            if note:
                self._noted[waiting] = now
        if wait:
            if note:
                self.audit(action, None, address, target=target, outcome="throttled")
            raise Throttled(f"too many failed tries: try again in {_duration(wait)}", retry_after=wait)
        try:
            yield attempt
        except (Unauthorized, Forbidden):
            self.audit(action, attempt.user, address, target=target, outcome="failed")
            raise
        except Overloaded:
            self._take_back(keys, now)
            raise
        finally:
            if attempt.right:
                self._take_back(keys, now, forgive=keys[0][0] == "user")

    def _waiting(self, keys, now: float) -> tuple[int, tuple | None]:
        """How long (seconds) a try must wait by the failures counted for ``keys``, and
        the key that says so (the lock held)."""
        wait, waiting = 0.0, None
        for key in keys:
            times = [t for t in self._failures.get(key, []) if now - t < THROTTLE_WINDOW_S]
            if times:
                self._failures[key] = times
            else:
                self._failures.pop(key, None)
            limit = USER_FAILURES if key[0] == "user" else ADDRESS_FAILURES
            if len(times) >= limit:
                pause = min(THROTTLE_MAX_S, THROTTLE_FIRST_S * 2 ** (len(times) - limit))
                if times[-1] + pause - now > wait:
                    wait, waiting = times[-1] + pause - now, key
        return max(0, int(wait + 0.999)), waiting

    def _take_back(self, keys, at: float, forgive: bool = False) -> None:
        """A try counted at ``at`` taken back; ``forgive``: the failures of its first key
        (the username's, from that address) forgotten too, as the password was right."""
        with self._lock:
            for i, key in enumerate(keys):
                if forgive and i == 0:
                    self._failures.pop(key, None)
                    continue
                times = self._failures.get(key)
                if times and at in times:
                    times.remove(at)
                    if not times:
                        del self._failures[key]

    def _prune(self, now: float) -> None:
        """Failures older than the window forgotten (at most once a minute, or when too
        many are kept); never more than FAILURE_KEYS_MAX kept: beyond it, those failed
        longest ago go first. The lock held."""
        if len(self._failures) <= FAILURE_KEYS_MAX and now - self._pruned < 60:
            return
        self._pruned = now
        last = lambda times: times[-1]  # noqa: E731 (the latest failure of a key)
        for key in [k for k, times in self._failures.items() if now - last(times) >= THROTTLE_WINDOW_S]:
            del self._failures[key]
        for key in [k for k, at in self._noted.items() if now - at >= THROTTLE_WINDOW_S]:
            del self._noted[key]
        over = len(self._failures) - FAILURE_KEYS_MAX
        if over > 0:
            for key in sorted(self._failures, key=lambda k: last(self._failures[k]))[:over]:
                del self._failures[key]
        if len(self._noted) > FAILURE_KEYS_MAX:
            self._noted.clear()

    def start_session(self, user: User, address: str | None = None) -> str:
        """A new session of ``user``: its token, for the cookie; only its hash is kept."""
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self._write() as db:
            db.execute("DELETE FROM sessions WHERE expires < ? OR last_seen < ?", (now, now - IDLE_S))
            db.execute("INSERT INTO sessions (token_hash, user, created, last_seen, expires, epoch, address) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (_token_hash(token), user.id, now, now, now + ABSOLUTE_S, user.session_epoch, address))
        return token

    def session(self, token: str | None) -> User | None:
        """The person a session is of, or None when it has ended: logged out, not used
        for IDLE_S, older than ABSOLUTE_S, or its user disabled or their sessions ended
        (session_epoch raised)."""
        if not token or not isinstance(token, str) or len(token) > 200:
            return None
        key, now = _token_hash(token), self.clock()
        rows = self._read("SELECT s.last_seen, s.expires, s.epoch, u.* FROM sessions s JOIN users u ON u.id = s.user "
                          "WHERE s.token_hash = ?", (key,))
        if not rows:
            return None
        row = rows[0]
        user = _user(row)
        if now - row["last_seen"] > IDLE_S or now > row["expires"] or not user.active \
                or user.session_epoch != row["epoch"]:
            with self._write() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
            return None
        if now - row["last_seen"] >= SEEN_EVERY_S:
            with self._write() as db:
                db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, key))
        return user

    def logout(self, token: str | None) -> User | None:
        if not token or not isinstance(token, str):
            return None
        key = _token_hash(token)
        with self._write() as db:
            row = db.execute("SELECT user FROM sessions WHERE token_hash = ?", (key,)).fetchone()
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
        return self.user(row["user"]) if row else None

    def change_own_password(self, user: User, current, new, address: str = "") -> str:
        """A person's own password changed (``current`` checked, a wrong one counted and
        waited for as a failed login is, by their username and address): every session
        of theirs ends, and this one goes on as a new one (its token)."""
        with self._try("password changed", self._keys(user.username, address), address, user.username) as attempt:
            attempt.user = user
            with self._checking():
                if not verify_password(current if isinstance(current, str) else "", user.password):
                    raise Forbidden("the current password is not right")
                attempt.ok()
                if isinstance(new, str) and isinstance(current, str) and new == current:
                    raise ValueError("choose a new password, not the one you have")
                user = self.set_password(user.id, new, temporary=False)
        self.audit("password changed", user, address, target=user.username)
        return self.start_session(user, address)

    # ---- the first admin ---------------------------------------------------------

    def setup_token(self) -> str | None:
        """While there are no users: the one-time token of the setup link (made once per
        start), for the first admin; None once there is a user."""
        if self.has_users():
            return None
        with self._lock:
            if self._setup is None:
                self._setup = secrets.token_urlsafe(24)
            return self._setup

    def setup(self, token, username, name, password, address: str = "") -> tuple[str, User]:
        """The first admin, made with the setup link's token; then the setup is gone for good."""
        expected = self._setup
        if self.has_users() or expected is None:
            raise Gone("Studio is set up already: log in")
        with self._try("setup", [("address", _throttled_as(address))], address, None) as attempt:
            if not isinstance(token, str) or not hmac.compare_digest(token.encode(), expected.encode()):
                raise Forbidden("this setup link is not the one Studio printed when it started")
            attempt.ok()
            with self._checking():
                user = self.add_user(username, password, name=name or "", role="admin", must_change_password=False)
        with self._lock:
            self._setup = None
        self.audit("setup", user, address, target=user.username)
        return self.start_session(user, address), user

    def bootstrap(self, environ=os.environ) -> User | None:
        """Unattended start (Docker): with STOREYPATH_ADMIN_PASSWORD set (and
        STOREYPATH_ADMIN_USER, else "admin") and no users yet, that admin is made."""
        password = environ.get(ADMIN_PASSWORD_ENV)
        if not password or self.has_users():
            return None
        username = environ.get(ADMIN_USER_ENV) or "admin"
        user = self.add_user(username, password, name=username, role="admin", must_change_password=False)
        self.audit("setup", user, None, target=user.username, how=ADMIN_PASSWORD_ENV)
        return user

    # ---- sharing ------------------------------------------------------------------

    def _entries(self, where: str = "", args=()) -> dict[str, ProjectAccess]:
        out: dict[str, ProjectAccess] = {}
        for r in self._read(f"SELECT code, owner FROM project_access {where}", args):
            out[r["code"]] = ProjectAccess(owner=r["owner"])
        for r in self._read(f"SELECT * FROM grants {where.replace('code', 'project')} ORDER BY given_at, rowid", args):
            out.setdefault(r["project"], ProjectAccess()).grants.append(
                Grant(user=r["user"], scope=Scope(kind=r["scope_kind"], id=r["scope_id"] or None), level=r["level"],
                      by=r["given_by"], at=r["given_at"]))
        return out

    def project_access(self, code: str) -> ProjectAccess:
        return self._entries("WHERE code = ?", (code,)).get(code) or ProjectAccess()

    def all_access(self) -> dict[str, ProjectAccess]:
        return self._entries()

    def set_owner(self, code: str, user_id: str | None) -> None:
        with self._write() as db:
            db.execute("INSERT INTO project_access (code, owner) VALUES (?, ?) "
                       "ON CONFLICT (code) DO UPDATE SET owner = excluded.owner", (code, user_id))

    def give(self, code: str, user_id: str, by: str | None) -> str | None:
        """A project given another owner (by an admin): the owner before keeps share on
        the whole project, as a grant of their own (shown, and taken away like any). The
        owner before (their id), if any."""
        with self._write() as db:
            row = db.execute("SELECT owner FROM project_access WHERE code = ?", (code,)).fetchone()
            before = row["owner"] if row else None
            db.execute("INSERT INTO project_access (code, owner) VALUES (?, ?) "
                       "ON CONFLICT (code) DO UPDATE SET owner = excluded.owner", (code, user_id))
            if before is not None and before != user_id:
                db.execute("INSERT INTO grants (project, scope_kind, scope_id, user, level, given_by, given_at) "
                           "VALUES (?, 'project', '', ?, 'share', ?, ?) ON CONFLICT (project, scope_kind, scope_id, user) "
                           "DO UPDATE SET level = 'share', given_by = excluded.given_by, given_at = excluded.given_at",
                           (code, before, by, utcnow()))
        return before

    def claim(self, code: str, user_id: str) -> bool:
        """A new project's owner: only when nothing is kept of a project of that code (no
        owner, no grants), never in place of what is. Whether it was."""
        with self._write() as db:
            return db.execute("INSERT OR IGNORE INTO project_access (code, owner) VALUES (?, ?)",
                              (code, user_id)).rowcount == 1

    def unclaim(self, code: str, user_id: str) -> None:
        """A claim undone (the project was not made after all): when it is still only that."""
        with self._write() as db:
            db.execute("DELETE FROM project_access WHERE code = ? AND owner = ? AND NOT EXISTS "
                       "(SELECT 1 FROM grants WHERE project = ?)", (code, user_id, code))

    def set_grant(self, code: str, user_id: str, scope: Scope, level: str | None, by: str | None) -> str:
        """``user_id``'s grant on ``scope`` of a project set to ``level`` (None: taken
        away): one grant a person a scope. What was done: added, changed, removed or
        nothing."""
        if level is not None and level not in LEVELS:
            raise ValueError(f"a level is one of {', '.join(LEVELS)}, or null to take it away")
        key = (code, scope.kind, scope.id or "", user_id)
        with self._write() as db:
            db.execute("INSERT OR IGNORE INTO project_access (code) VALUES (?)", (code,))
            have = db.execute("SELECT level FROM grants WHERE project = ? AND scope_kind = ? AND scope_id = ? "
                              "AND user = ?", key).fetchone()
            if level is None:
                if have is None:
                    return "nothing"
                db.execute("DELETE FROM grants WHERE project = ? AND scope_kind = ? AND scope_id = ? AND user = ?", key)
                return "removed"
            if have is not None:
                if have["level"] == level:
                    return "nothing"
                db.execute("UPDATE grants SET level = ?, given_by = ?, given_at = ? WHERE project = ? AND "
                           "scope_kind = ? AND scope_id = ? AND user = ?", (level, by, utcnow(), *key))
                return "changed"
            db.execute("INSERT INTO grants (project, scope_kind, scope_id, user, level, given_by, given_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", (*key, level, by, utcnow()))
            return "added"

    def forget(self, code: str) -> None:
        """A project deleted: who it was shared with, gone with it."""
        with self._write() as db:
            db.execute("DELETE FROM project_access WHERE code = ?", (code,))

    def edits_somewhere(self, user: User) -> bool:
        """Whether a person may change anything of any project (to open a package into it)."""
        if user.role == "admin":
            return True
        return bool(self._read("SELECT 1 FROM project_access WHERE owner = ? UNION ALL SELECT 1 FROM grants "
                               "WHERE user = ? AND level IN ('edit', 'share') LIMIT 1", (user.id, user.id)))

    def shares_somewhere(self, user: User) -> bool:
        """Whether a person may share anything (to be shown the users to share with)."""
        if user.role == "admin":
            return True
        return bool(self._read("SELECT 1 FROM project_access WHERE owner = ? UNION ALL "
                               "SELECT 1 FROM grants WHERE user = ? AND level = 'share' LIMIT 1", (user.id, user.id)))

    # ---- the audit log --------------------------------------------------------------

    def audit(self, action: str, user: User | None, address: str | None, target=None, outcome: str = "ok",
              **more) -> None:
        """What was done, recorded: when, who (id and username), from where, what, to what,
        how it went, and anything more."""
        with self._write() as db:
            db.execute("INSERT INTO audit (at, user_id, username, address, action, target, outcome, details) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (utcnow(), user.id if user else None, user.username if user else None, address or None,
                        action, None if target is None else str(target), outcome,
                        json.dumps(more, ensure_ascii=False, default=str)))

    def audit_tail(self, n: int = 200) -> list[dict]:
        """The latest ``n`` entries, newest first."""
        out = []
        for r in self._read("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (n,)):
            out.append({"at": r["at"], "user": {"id": r["user_id"], "username": r["username"]} if r["user_id"] else None,
                        "address": r["address"], "action": r["action"], "target": r["target"],
                        "outcome": r["outcome"], **json.loads(r["details"] or "{}")})
        return out


def _new_id(taken: set[str]) -> str:
    """U and ten base32 characters: random, never a name."""
    from .ids import CROCKFORD_ALPHABET

    while True:
        uid = "U" + "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(10))
        if uid not in taken:
            return uid


def _role(role) -> str:
    if role not in ROLES:
        raise ValueError(f"a role is one of {', '.join(ROLES)}")
    return role


def _capabilities(capabilities) -> list[str]:
    if not isinstance(capabilities, (list, tuple, set, type(None))) \
            or not all(isinstance(c, str) and c in CAPABILITIES for c in capabilities or []):
        raise ValueError(f"capabilities are any of {', '.join(CAPABILITIES)}")
    return sorted(set(capabilities or []))


def _name(name) -> str:
    if name is not None and not isinstance(name, str):
        raise ValueError("a name is text")
    return " ".join((name or "").split())[:100]


def _duration(seconds: int) -> str:
    return f"{seconds} seconds" if seconds < 120 else f"{(seconds + 59) // 60} minutes"


class _Attempt:
    """A password tried (Accounts._try): whose it was, when known, and whether it was right."""

    def __init__(self):
        self.user: User | None = None
        self.right = False

    def ok(self) -> None:
        self.right = True


def _throttled_as(address: str | None) -> str:
    """The address failed tries are counted by."""
    return address or ""


# ---- what one person may do in one project ------------------------------------------

def rank(level: str | None) -> int:
    return LEVELS.index(level) + 1 if level else 0


def highest(*levels: str | None) -> str | None:
    return max(levels, key=rank, default=None) if any(levels) else None


class Sight:
    """What one person may do in one project: their level on the project as a whole, on
    each of its buildings and on each of its floors, from the workspace as it is (a
    floor added later is covered by its building's and the project's grants). None of
    a level: they may not see it."""

    def __init__(self, ws, access: ProjectAccess | None, user: User | None):
        from .ids import make_id

        access = access or ProjectAccess()
        self.user, self.access, self.code = user, access, ws.id
        self.owner = user is not None and access.owner is not None and access.owner == user.id
        self.admin = user is None or user.role == "admin"
        mine = [g for g in access.grants if user is not None and g.user == user.id]
        self.project = "share" if self.admin or self.owner else \
            highest(*(g.level for g in mine if g.scope.kind == "project"))
        self.buildings: dict[str, str | None] = {}
        self.floors: dict[str, str | None] = {}
        self.building_of: dict[str, str] = {}  # floor ID -> its building's
        self.location_of: dict[str, str] = {}  # building ID -> its location's
        for loc in ws.locations:
            loc_id = make_id(ws.id, loc.code)
            for b in loc.buildings:
                b_id = make_id(ws.id, loc.code, b.code)
                self.location_of[b_id] = loc_id
                self.buildings[b_id] = highest(self.project, *(g.level for g in mine if g.scope.kind == "building"
                                                                and g.scope.id == b_id))
                for f in b.floors:
                    f_id = make_id(ws.id, loc.code, b.code, f.code)
                    self.building_of[f_id] = b_id
                    self.floors[f_id] = highest(self.buildings[b_id], *(g.level for g in mine if g.scope.kind == "floor"
                                                                         and g.scope.id == f_id))

    def floor(self, floor_id: str) -> str | None:
        return self.floors.get(floor_id)

    def building(self, building_id: str) -> str | None:
        """The level on a building as a whole (its own grants and the project's)."""
        return self.buildings.get(building_id)

    def sees_building(self, building_id: str) -> bool:
        """Whether any of a building is seen: the building, or one of its floors."""
        return bool(self.buildings.get(building_id)) or any(
            lvl for f, lvl in self.floors.items() if self.building_of[f] == building_id)

    def scope(self, kind: str, scope_id: str | None) -> str | None:
        """The level on a scope (None for one not in the project)."""
        if kind == "project":
            return self.project
        return (self.buildings if kind == "building" else self.floors).get(scope_id)

    def any(self) -> bool:
        return bool(self.project) or any(self.buildings.values()) or any(self.floors.values())

    def most(self) -> str | None:
        """The highest level anywhere in the project."""
        return highest(self.project, *self.buildings.values(), *self.floors.values())

    @property
    def whole(self) -> bool:
        """Whether the whole project is seen (nothing of it to leave out)."""
        return bool(self.project)

    def can(self) -> dict:
        """What the pages are told: the level on the project, each building and each floor."""
        return {"project": self.project, "most": self.most(), "owner": self.owner, "admin": self.admin,
                "share": self.most() == "share", "delete": self.owner or self.admin,
                "buildings": {b: lvl for b, lvl in self.buildings.items() if lvl or self.sees_building(b)},
                "floors": {f: lvl for f, lvl in self.floors.items() if lvl}}

    def seen(self, ws):
        """A copy of the workspace with only what is seen: the floors (and their spaces,
        openings and items), the buildings with one seen, their locations. Every building
        keeps the place it has on its site plan (a footprint of fewer floors would move
        it). It has no export history (a preview lists no changes against packages of what
        is not seen) and none of what the models read in the drawings. Never saved."""
        from .export import settle
        from .ids import make_id, parse_id

        if self.whole:
            return ws
        copy = ws.model_copy(deep=True)
        for loc in copy.locations:
            if loc.buildings:
                settle(loc)
        keep_floors = set()
        for loc in copy.locations:
            for b in list(loc.buildings):
                b_id = make_id(copy.id, loc.code, b.code)
                b.floors = [f for f in b.floors if self.floors.get(make_id(copy.id, loc.code, b.code, f.code))]
                keep_floors |= {make_id(copy.id, loc.code, b.code, f.code) for f in b.floors}
                if not b.floors and not self.buildings.get(b_id):
                    loc.buildings.remove(b)
        copy.locations = [loc for loc in copy.locations if loc.buildings]

        def floor_of(object_id: str) -> str | None:
            try:
                return parse_id(object_id).prefix("floor")
            except ValueError:
                return None

        copy.objects = {i: r for i, r in copy.objects.items() if floor_of(i) in keep_floors}
        copy.overrides = {i: o for i, o in copy.overrides.items() if i in copy.objects}
        copy.items = {i: it for i, it in copy.items.items() if it.floor_id in keep_floors}
        copy.exports, copy.readings, copy.vision = [], {}, {}
        return copy
