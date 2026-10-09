"""Projects in Studio's database (ProjectStore): each project whole — its tree,
objects, corrections, items, readings, what the vision model saw, its export history
with the packages as sent, its drawings — and every change to it, recorded.

Reading. ``current(code)`` is the project as it is now, as the Workspace the rest of
Studio works on: kept in memory, and read again (one statement, the database making
the JSON the Workspace is read from) when its ``version`` moved. It is shared: never
to be changed. ``load(code, building=None, floors=None)`` is a Workspace of one's own,
to change and save: whole, or with only one building's or some floors' objects,
corrections and items (the tree, readings, vision and exports are always whole).

Writing. Every change is one transaction that raises the project's ``version`` first
(which holds the project's row until it commits: changes to one project are made one
after another), works out what it changes on the project as it is then, writes only
those rows, raises the ``version`` of each floor it touched, records itself in
``history`` and notifies ``storeypath_changes`` ({project, floors, seq, version}),
delivered once it commits. The project kept in memory is then brought up to date by
the same changes, without reading it again.

- Review's changes (change): one correction, one item, one floor's drawn edits.
- ``save_floor(ws, floor_id)``: a conversion's result for that floor (its objects and
  corrections, the floor, the building's next ID number, new readings and vision
  answers); ``save_project(ws)``: whatever differs between ``ws`` and the project
  (placing buildings, adding floors, an export, a file opened into it or in its place).
  What is written is what differs, row by row.
- ``create(ws)``: a new project; ``delete(code)``: a project and everything of it.

One editor a floor at a time. A person's change from a page (``editor``: their
session, the page) takes the lock of each floor it touches for that person, in its own
transaction (``floor_locks``), or is refused (Locked, 423, naming who holds it) while
another person holds one. A lock goes when its person leaves the floor
(release_lock), when nothing (no change, no heartbeat of their page open on it,
touch_locks) kept it for LOCK_IDLE_S, or when an admin takes it over (take_over,
recorded in the history). Steps of Studio's own (a floor read again, an export) take no
floor lock: they hold the project's step lock (StepLock) while they change it, which
also keeps every other Studio on the database out of it.

Times are UTC. Geometry is GeoJSON in each building's own frame (local metres), kept
exactly; PostGIS geometries are made from it in the database (SRID 0).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..errors import Conflict, Locked, NotFound
from ..workspace import (ExportRecord, FloorEdits, Item, Location, ObjectRecord, Override, Project, Reading,
                         Workspace)
from . import CHANNEL, Database

COMMAND_LINE = "command line"  # who a change from the command line is of
LOCAL = {"local": True}  # who a change is of on this computer without accounts
OBJECT_MORE = ("connects", "parent", "zones", "span", "width", "swings", "sill", "height", "tag", "issues",
               "detected_ignored")
DRAWINGS = "drawings"  # a floor's drawing is drawings/<name>: its name in the project's drawings
LOCK_IDLE_S = 15 * 60  # a floor's lock nothing kept for this long (no change, no heartbeat) is free
STEP_LOCK = 0x5370  # pg_advisory_lock(STEP_LOCK, hashtext(code)): a project-wide step works on the project


class ProjectExists(Exception):
    """A project of that code is in the database already."""


@dataclass(frozen=True)
class Editor:
    """Where a person's change comes from: their session (whose floor locks it takes)
    and the page that sent it (told back with the change, so that page knows it has it)."""

    session: str
    page: str | None = None


class StepLock:
    """A project's lock, held while a step of Studio's changes the project as a whole
    (adding floors, reading them, placing buildings, exporting, opening a file into it)
    and, briefly, while Review saves a change: one thread of this Studio at a time
    (re-entrant), and one Studio at a time on the database (a PostgreSQL advisory lock,
    held on a connection of its own while the lock is held). Used as threading's locks
    are (acquire, release, with)."""

    def __init__(self, db: Database, code: str):
        self.db, self.code = db, code
        self._here = threading.RLock()
        self._depth = 0  # times the thread holding it took it
        self._conn = None

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if not self._here.acquire(blocking, timeout):
            return False
        if self._depth == 0:
            try:
                got = self._take(blocking, timeout)
            except BaseException:
                self._here.release()
                raise
            if not got:
                self._here.release()
                return False
        self._depth += 1
        return True

    def _take(self, blocking: bool, timeout: float) -> bool:
        conn = self.db.pool.getconn()
        try:
            conn.autocommit = True
            if blocking and (timeout is None or timeout < 0):
                conn.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", (STEP_LOCK, self.code))
                self._conn = conn
                return True
            deadline = time.monotonic() + (timeout if blocking else 0)
            while True:
                if conn.execute("SELECT pg_try_advisory_lock(%s, hashtext(%s))", (STEP_LOCK, self.code)).fetchone()[0]:
                    self._conn = conn
                    return True
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        except BaseException:
            self._give_back(conn)
            raise
        self._give_back(conn)
        return False

    def _give_back(self, conn) -> None:
        try:
            conn.autocommit = False
        except Exception:  # noqa: BLE001 (a broken connection: the pool drops it)
            pass
        self.db.pool.putconn(conn)

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            conn, self._conn = self._conn, None
            try:
                conn.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", (STEP_LOCK, self.code))
            except Exception:  # noqa: BLE001 (its connection is gone: so is the lock)
                pass
            finally:
                self._give_back(conn)
        self._here.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class _Nothing(Exception):
    """A change that changes nothing: rolled back, nothing recorded."""


def floor_of(object_id: str) -> str:
    """The floor an object (by its ID) is on: its ID without its own code."""
    return object_id.rsplit("-", 1)[0]


def drawing_name(path: str) -> str:
    """A floor's drawing (its source path) by its name in the project's drawings."""
    return Path(str(path).replace("\\", "/")).name


def who_of(by) -> Any:
    """Who a change is of, as history keeps it: {id, username, name} of a person,
    {"local": true} on this computer without accounts, or "command line"; a user's id
    alone is filled in from the accounts."""
    if by is None:
        return dict(LOCAL)
    if by == COMMAND_LINE:
        return COMMAND_LINE
    if isinstance(by, str):
        return {"id": by}
    if isinstance(by, dict):
        return by
    if getattr(by, "id", None) == "local":
        return dict(LOCAL)
    return {"id": by.id, "username": by.username, "name": getattr(by, "name", "") or by.username}


def _user_id(by) -> str | None:
    who = who_of(by)
    return who.get("id") if isinstance(who, dict) else None


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _j(value) -> Jsonb | None:
    return None if value is None else Jsonb(value, dumps=_dumps)


def _same(a, b) -> bool:
    """Two records the same, as they are kept (a tuple and a list of the same points are)."""
    if a == b:
        return True
    if a is None or b is None or type(a) is not type(b):
        return False
    if hasattr(a, "model_dump"):
        return a.model_dump(mode="json") == b.model_dump(mode="json")
    return json.loads(_dumps(a)) == json.loads(_dumps(b))


# ---- what a change changes -------------------------------------------------------

@dataclass
class Changes:
    """What one transaction changes of a project, row by row: written to the database,
    then to the project kept in memory. Each mapping is key -> the new value, or None
    for one taken away. And what history records of it."""

    project: dict | None = None  # name, created_at, format_version
    tree: list[Location] | None = None  # the locations after, when any of the tree changed
    nodes: list[tuple] = field(default_factory=list)  # (table, key, row or None) of the tree to write
    edits: dict[str, FloorEdits] = field(default_factory=dict)  # floor ID -> its drawn edits alone
    objects: dict[str, ObjectRecord | None] = field(default_factory=dict)
    overrides: dict[str, Override | None] = field(default_factory=dict)
    items: dict[str, Item | None] = field(default_factory=dict)
    readings: dict[str, Reading | None] = field(default_factory=dict)
    vision: dict[str, dict | None] = field(default_factory=dict)
    exports: dict[int, ExportRecord | None] = field(default_factory=dict)  # by position
    export_bytes: dict[str, bytes] = field(default_factory=dict)  # file -> the package as sent
    drawings: dict[str, bytes | None] = field(default_factory=dict)  # name -> its bytes (kept), or None (gone)
    words: dict[str, str] = field(default_factory=dict)  # name -> the words of a drawing put now
    floors: set[str] = field(default_factory=set)  # the floors it touched (their version raised)
    # history
    part: str = "project"  # object, item, edit, floor, building, project
    kind: str = "change"
    targets: list[str] = field(default_factory=list)
    before: Any = None
    after: Any = None
    more: dict = field(default_factory=dict)

    def empty(self) -> bool:
        return not (self.project or self.tree is not None or self.edits or self.objects or self.overrides
                    or self.items or self.readings or self.vision or self.exports or self.export_bytes
                    or self.drawings or self.words)

    def apply(self, base: Workspace) -> Workspace:
        """The project after these changes: a new Workspace sharing with ``base`` all they
        leave as it was (``base`` itself is not changed)."""
        update: dict[str, Any] = {}
        if self.project:
            p = self.project
            update["project"] = Project(code=base.project.code, name=p.get("name", base.project.name),
                                        created_at=p.get("created_at", base.project.created_at))
            if "format_version" in p:
                update["format_version"] = p["format_version"]
        if self.tree is not None:
            update["locations"] = self.tree
        elif self.edits:
            update["locations"] = _with_edits(base, self.edits)
        for name in ("objects", "overrides", "items", "readings", "vision"):
            changed = getattr(self, name)
            if changed:
                d = dict(getattr(base, name))
                for k, v in changed.items():
                    if v is None:
                        d.pop(k, None)
                    else:
                        d[k] = v
                update[name] = d
        if self.exports:
            records = list(base.exports)
            for pos in sorted(self.exports):
                while len(records) <= pos:
                    records.append(None)
                records[pos] = self.exports[pos]
            update["exports"] = [r for r in records if r is not None]
        return base.model_copy(update=update) if update else base


def _put_drawings(ch: Changes, drawings: dict | None) -> None:
    """Drawings to keep (name -> bytes, or (bytes, its words)) in a change."""
    for name, value in (drawings or {}).items():
        data, words = value if isinstance(value, tuple) else (value, None)
        ch.drawings[name] = data
        if words is not None:
            ch.words[name] = words


def _with_edits(ws: Workspace, edits: dict[str, FloorEdits]) -> list[Location]:
    """The tree with these floors' drawn edits, sharing the rest of it."""
    out = []
    for loc in ws.locations:
        buildings, changed = [], False
        for b in loc.buildings:
            floors = []
            for f in b.floors:
                f_id = f"{ws.id}-{loc.code}-{b.code}-{f.code}"
                floors.append(f.model_copy(update={"edits": edits[f_id]}) if f_id in edits else f)
            if any(x is not y for x, y in zip(floors, b.floors)):
                buildings.append(b.model_copy(update={"floors": floors}))
                changed = True
            else:
                buildings.append(b)
        out.append(loc.model_copy(update={"buildings": buildings}) if changed else loc)
    return out


# ---- the rows of a project -------------------------------------------------------

def _tree_rows(ws: Workspace) -> dict[tuple, tuple[dict, Any]]:
    """Every node of the tree as its row: (table, key…) -> (row, node)."""
    out = {}
    for i, loc in enumerate(ws.locations):
        out["locations", loc.code] = ({"position": i, "name": loc.name, "address": loc.address,
                                       "placement": loc.placement.model_dump(mode="json") if loc.placement else None,
                                       "site_origin": list(loc.site_origin) if loc.site_origin is not None else None},
                                      loc)
        for j, b in enumerate(loc.buildings):
            out["buildings", loc.code, b.code] = (
                {"position": j, "name": b.name, "next_object_seq": b.next_object_seq,
                 "placement": b.placement.model_dump(mode="json") if b.placement else None,
                 "site": b.site.model_dump(mode="json") if b.site else None}, b)
            for k, f in enumerate(b.floors):
                out["floors", loc.code, b.code, f.code] = ({"position": k}, f)
    return out


def _floor_row(f) -> dict:
    d = f.model_dump(mode="json")
    return {k: d[k] for k in ("name", "ordinal", "elevation", "height", "parapet_height", "source", "outline",
                              "converted_at", "method", "warnings", "layers", "walls", "wall_thickness",
                              "symbols", "symbols_key", "edits")}


def _object_row(code: str, r: ObjectRecord) -> tuple:
    d = r.model_dump(mode="json")
    more = {k: d[k] for k in OBJECT_MORE if not (d[k] is None or d[k] == [] or d[k] is False)}
    return (code, r.id, floor_of(r.id), r.kind, r.type, r.type_source, r.name, r.number, r.label,
            _j(d["geometry"]), _j(more), r.status, r.created_at, r.retired_at)


def _override_row(code: str, object_id: str, o: Override, by) -> tuple:
    return (code, object_id, floor_of(object_id), o.type.value if o.type is not None else None, o.name, o.number,
            o.hidden, o.ignored, o.capacity, o.stack, o.floor_finish, o.wall_finish, by)


def _item_row(code: str, it: Item, by) -> tuple:
    return (code, it.id, it.floor_id or "", it.type, it.x, it.y, it.rotation, _j(dict(it.values)), it.status,
            it.created_at, it.retired_at, by)


def _reading_row(code: str, text: str, r: Reading) -> tuple:
    return (code, text, r.type.value if r.type is not None else None, r.source, r.rooms_only, r.asked)


# the project as the JSON a Workspace is read from, made by the database
_OBJECT_JSON = ("jsonb_build_object('id', o.id, 'kind', o.kind, 'type', o.type, 'type_source', o.type_source, "
                "'name', o.name, 'number', o.number, 'label', o.label, 'geometry', o.geometry, 'status', o.status, "
                "'created_at', o.created_at, 'retired_at', o.retired_at) || o.more")
_FLOOR_JSON = ("jsonb_build_object('code', f.code, 'name', f.name, 'ordinal', f.ordinal, 'elevation', f.elevation, "
               "'height', f.height, 'parapet_height', f.parapet_height, 'source', f.source, 'outline', f.outline, "
               "'converted_at', f.converted_at, 'method', f.method, 'warnings', f.warnings, 'layers', f.layers, "
               "'walls', f.walls, 'wall_thickness', f.wall_thickness, 'symbols', f.symbols, "
               "'symbols_key', f.symbols_key, 'edits', f.edits)")
_LOAD = f"""
SELECT p.version, json_build_object(
  'format', 'storeypath-workspace', 'format_version', p.format_version,
  'project', json_build_object('code', p.code, 'name', p.name, 'created_at', p.created_at),
  'locations', COALESCE((SELECT json_agg(json_build_object(
      'code', l.code, 'name', l.name, 'address', l.address, 'placement', l.placement, 'site_origin', l.site_origin,
      'buildings', COALESCE((SELECT json_agg(json_build_object(
          'code', b.code, 'name', b.name, 'placement', b.placement, 'site', b.site,
          'next_object_seq', b.next_object_seq,
          'floors', COALESCE((SELECT json_agg({_FLOOR_JSON} ORDER BY f.position) FROM floors f
                              WHERE f.project = b.project AND f.location = b.location AND f.building = b.code), '[]'))
        ORDER BY b.position) FROM buildings b WHERE b.project = l.project AND b.location = l.code), '[]'))
    ORDER BY l.position) FROM locations l WHERE l.project = p.code), '[]'),
  'objects', COALESCE((SELECT json_object_agg(o.id, {_OBJECT_JSON} ORDER BY o.position) FROM objects o
                       WHERE o.project = p.code AND {{scope_o}}), '{{{{}}}}'),
  'overrides', COALESCE((SELECT json_object_agg(v.object, json_build_object(
      'type', v.type, 'name', v.name, 'number', v.number, 'hidden', v.hidden, 'ignored', v.ignored,
      'capacity', v.capacity, 'stack', v.stack, 'floor_finish', v.floor_finish, 'wall_finish', v.wall_finish)
      ORDER BY v.position) FROM overrides v WHERE v.project = p.code AND {{scope_v}}), '{{{{}}}}'),
  'items', COALESCE((SELECT json_object_agg(i.id, json_build_object(
      'id', i.id, 'type', i.type, 'floor_id', i.floor, 'x', i.x, 'y', i.y, 'rotation', i.rotation,
      'values', i.details, 'status', i.status, 'created_at', i.created_at, 'retired_at', i.retired_at)
      ORDER BY i.position) FROM items i WHERE i.project = p.code AND {{scope_i}}), '{{{{}}}}'),
  'readings', COALESCE((SELECT json_object_agg(r.text, json_build_object(
      'type', r.type, 'source', r.source, 'rooms_only', r.rooms_only, 'asked', r.asked) ORDER BY r.position)
      FROM readings r WHERE r.project = p.code), '{{{{}}}}'),
  'vision', COALESCE((SELECT json_object_agg(x.key, x.answer ORDER BY x.position) FROM vision x
                      WHERE x.project = p.code), '{{{{}}}}'),
  'exports', COALESCE((SELECT json_agg(e.record ORDER BY e.position) FROM exports e
                       WHERE e.project = p.code AND e.position IS NOT NULL), '[]')
)::text FROM projects p WHERE p.code = %(code)s
"""


def _scope_sql(scope) -> dict[str, str]:
    """What of a project's objects, corrections and items a load holds."""
    if scope is None:
        return {"scope_o": "true", "scope_v": "true", "scope_i": "true"}
    kind, _ = scope
    if kind == "building":
        test = "starts_with({}, %(prefix)s)"
    else:
        test = "{} = ANY(%(floors)s)"
    return {"scope_o": test.format("o.floor"), "scope_v": test.format("v.floor"), "scope_i": test.format("i.floor")}


def _in_scope(scope, floor_id: str) -> bool:
    if scope is None:
        return True
    kind, value = scope
    return floor_id.startswith(value) if kind == "building" else floor_id in value


def _covers(have, want) -> bool:
    """Whether a Workspace loaded with ``have`` holds all a save of ``want`` writes."""
    if have is None:
        return True
    if want is None:
        return False
    if have[0] == "building":
        return want[0] == "building" and want[1] == have[1] or \
            want[0] == "floors" and all(f.startswith(have[1]) for f in want[1])
    return want[0] == "floors" and set(want[1]) <= set(have[1])


def _scope(building: str | None, floors) -> tuple | None:
    if building is not None:
        return ("building", building + "-")
    if floors is not None:
        return ("floors", frozenset(floors))
    return None


@dataclass
class _Kept:
    version: int
    ws: Workspace


# ---- the store -------------------------------------------------------------------

class ProjectStore:
    """The projects of a Studio's database."""

    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._kept: dict[str, _Kept] = {}
        self._reading: dict[str, threading.Lock] = {}
        self._scopes: dict[int, tuple | None] = {}  # id(Workspace loaded) -> what of the project it holds
        self._catalogue: tuple[int, Any] | None = None
        # called with a project's code after each change to it commits (`storeypath review`
        # writes its file again)
        self.listeners: list[Callable[[str], None]] = []

    # ---- reading ------------------------------------------------------------------

    def codes(self) -> list[str]:
        with self.db.connection() as conn:
            return [c for (c,) in conn.execute("SELECT code FROM projects ORDER BY code")]

    def exists(self, code: str) -> bool:
        return self.version(code) is not None

    def version(self, code: str) -> int | None:
        if not isinstance(code, str):
            return None
        with self.db.connection() as conn:
            row = conn.execute("SELECT version FROM projects WHERE code = %s", (code,)).fetchone()
        return row[0] if row else None

    def item_taken(self, item_id: str) -> bool:
        """Whether an item of any project here has this ID (the items' key): a new item's
        ID is drawn again when it has."""
        with self.db.connection() as conn:
            return conn.execute("SELECT EXISTS (SELECT 1 FROM items WHERE id = %s)", (item_id,)).fetchone()[0]

    def name(self, code: str) -> str:
        with self.db.connection() as conn:
            row = conn.execute("SELECT name FROM projects WHERE code = %s", (code,)).fetchone()
        if row is None:
            raise NotFound(f"no project {code}")
        return row[0]

    def _read(self, conn, code: str, scope=None) -> tuple[int, Workspace]:
        args: dict[str, Any] = {"code": code}
        if scope is not None:
            args["prefix" if scope[0] == "building" else "floors"] = \
                scope[1] if scope[0] == "building" else sorted(scope[1])
        row = conn.execute(_LOAD.format(**_scope_sql(scope)), args).fetchone()
        if row is None:
            raise NotFound(f"no project {code}")
        return row[0], Workspace.model_validate_json(row[1])

    def current(self, code: str) -> Workspace:
        """The project as it is now: shared, never to be changed (load one to change)."""
        version = self.version(code)
        if version is None:
            raise NotFound(f"no project {code}")
        kept = self._kept.get(code)
        if kept is not None and kept.version == version:
            return kept.ws
        with self._lock:
            reading = self._reading.setdefault(code, threading.Lock())
        with reading:  # read once, however many ask at once
            kept = self._kept.get(code)
            if kept is not None and kept.version >= version:
                return kept.ws
            with self.db.connection() as conn:
                version, ws = self._read(conn, code)
            self._keep(code, version, ws)
            return ws

    def _keep(self, code: str, version: int, ws: Workspace) -> None:
        with self._lock:
            kept = self._kept.get(code)
            if kept is None or kept.version < version:
                self._kept[code] = _Kept(version, ws)

    def load(self, code: str, building: str | None = None, floors=None) -> Workspace:
        """The project as a Workspace of one's own, to change and save: whole, or with only
        ``building``'s (its ID) or ``floors``' (their IDs) objects, corrections and items."""
        scope = _scope(building, floors)
        if scope is None:
            ws = self.current(code)
            ws = Workspace.model_validate_json(ws.model_dump_json())
        else:
            with self.db.connection() as conn:
                _, ws = self._read(conn, code, scope)
        self._scopes[id(ws)] = scope
        weakref.finalize(ws, self._scopes.pop, id(ws), None)
        return ws

    # ---- changing -----------------------------------------------------------------

    def change(self, code: str, fn: Callable[[Workspace], tuple[Changes, Any]], *, by=None,
               create: Workspace | None = None, editor: Editor | None = None) -> Any:
        """One change of a project, as one transaction: ``fn`` is given the project as it
        is then (never to be changed) and says what it changes (Changes) and what to
        answer; that is written, with its history row, and notified. A change of nothing
        is rolled back, and records nothing. ``create``: a new project (ProjectExists when
        its code is taken). ``editor``: a person's change from a page, which takes the
        lock of each floor it touches (Locked when another person holds one)."""
        changed = None
        try:
            with self.db.transaction() as conn:
                if create is not None:
                    row = conn.execute(
                        "INSERT INTO projects (code, name, created_at, format_version) "
                        "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING version",
                        (code, create.project.name, create.project.created_at, create.format_version)).fetchone()
                    if row is None:
                        raise ProjectExists(code)
                    version = row[0]
                    base = Workspace(project=create.project)
                else:
                    row = conn.execute("UPDATE projects SET version = version + 1, changed_at = now() "
                                       "WHERE code = %s RETURNING version", (code,)).fetchone()
                    if row is None:
                        raise NotFound(f"no project {code}")
                    version = row[0]
                    kept = self._kept.get(code)
                    base = kept.ws if kept is not None and kept.version == version - 1 else self._read(conn, code)[1]
                changes, result = fn(base)
                if changes.empty() and create is None:
                    raise _Nothing
                taken = self._take_locks(conn, code, changes.floors, by, editor) if editor is not None else []
                self._write(conn, code, changes, by)
                seq = self._history(conn, code, version, changes, by)
                said = {"project": code, "floors": sorted(changes.floors), "seq": seq, "version": version}
                if editor is not None and editor.page:
                    said["page"] = editor.page
                if taken:
                    said["locks"] = taken
                conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, _dumps(said)))
                changed = (version, changes.apply(base))
        except _Nothing:
            return result
        self._keep(code, *changed)
        for listener in list(self.listeners):
            listener(code)
        return result

    def save(self, ws: Workspace, *, floors=None, building: str | None = None, by=None, part: str = "project",
             kind: str = "save", targets=(), more: dict | None = None, export_bytes: dict | None = None,
             drawings: dict | None = None, replace_drawings: bool = False) -> bool:
        """What differs between ``ws`` and the project written, in one change: in the tree,
        readings, vision and exports always; in the objects, corrections and items of
        ``floors`` (IDs) or ``building`` (ID), or of the whole project when neither is
        given (``ws`` must hold what it saves: loaded so). ``export_bytes``: the packages
        (file -> bytes) of export records new in it; ``drawings``: drawings to keep
        (name -> bytes), the project's others taken away when ``replace_drawings``.
        Whether anything differed."""
        scope = _scope(building, floors)
        if id(ws) in self._scopes and not _covers(self._scopes[id(ws)], scope):
            raise ValueError("this workspace holds part of the project: it saves that part alone")
        have = {d["name"] for d in self.drawings(ws.id)} if replace_drawings else set()

        def fn(base: Workspace):
            ch = diff(ws, base, scope)
            ch.part, ch.kind, ch.targets, ch.more = part, kind, list(targets), dict(more or {})
            ch.export_bytes = dict(export_bytes or {})
            _put_drawings(ch, drawings)
            ch.drawings.update({n: None for n in have if n not in ch.drawings})
            return ch, ch

        return not self.change(ws.id, fn, by=by).empty()

    def save_floor(self, ws: Workspace, floor_id: str, *, by=None, kind: str = "read", more: dict | None = None) -> bool:
        """A floor read again: its objects and corrections, the floor, its building's next
        ID number, and the readings and vision answers new in ``ws``."""
        return self.save(ws, floors=[floor_id], by=by, part="floor", kind=kind, targets=[floor_id], more=more)

    def save_project(self, ws: Workspace, **kw) -> bool:
        """What differs between ``ws`` (the whole project) and the project, written (save)."""
        return self.save(ws, **kw)

    def create(self, ws: Workspace, *, by=None, kind: str = "create", drawings: dict | None = None,
               export_bytes: dict | None = None) -> None:
        """A new project, whole (ProjectExists when its code is taken)."""

        def fn(base: Workspace):
            ch = diff(ws, base, None)
            ch.part, ch.kind, ch.targets = "project", kind, [ws.id]
            _put_drawings(ch, drawings)
            ch.export_bytes = dict(export_bytes or {})
            return ch, None

        self.change(ws.id, fn, by=by, create=ws)

    def delete(self, code: str) -> None:
        """A project and everything of it (its drawings, packages and history) gone."""
        with self.db.transaction() as conn:
            gone = conn.execute("DELETE FROM projects WHERE code = %s RETURNING version", (code,)).fetchone()
            if gone is None:
                raise NotFound(f"no project {code}")
            conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, _dumps({"project": code, "deleted": True})))
        with self._lock:
            self._kept.pop(code, None)

    def forget(self, code: str | None = None) -> None:
        """What is kept in memory of a project (of every project) forgotten: read again."""
        with self._lock:
            if code is None:
                self._kept.clear()
            else:
                self._kept.pop(code, None)

    # ---- writing rows -------------------------------------------------------------

    def _write(self, conn, code: str, ch: Changes, by) -> None:
        uid = _user_id(by)
        if ch.project:
            sets = ", ".join(f"{k} = %({k})s" for k in ch.project)
            conn.execute(f"UPDATE projects SET {sets} WHERE code = %(code)s", {**ch.project, "code": code})
        self._write_tree(conn, code, ch)
        for f_id, edits in ch.edits.items():
            conn.execute("UPDATE floors SET edits = %s WHERE id = %s",
                         (_j(edits.model_dump(mode="json")), f_id))
        with conn.cursor() as cur:
            gone = [(code, i) for i, r in ch.objects.items() if r is None]
            put = [_object_row(code, r) for r in ch.objects.values() if r is not None]
            if gone:
                cur.executemany("DELETE FROM objects WHERE project = %s AND id = %s", gone)
            if put:
                cur.executemany(
                    "INSERT INTO objects (project, id, floor, kind, type, type_source, name, number, label, geometry, "
                    "more, status, created_at, retired_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s) ON CONFLICT (project, id) DO UPDATE SET floor = EXCLUDED.floor, kind = EXCLUDED.kind, "
                    "type = EXCLUDED.type, type_source = EXCLUDED.type_source, name = EXCLUDED.name, "
                    "number = EXCLUDED.number, label = EXCLUDED.label, geometry = EXCLUDED.geometry, "
                    "more = EXCLUDED.more, status = EXCLUDED.status, created_at = EXCLUDED.created_at, "
                    "retired_at = EXCLUDED.retired_at", put)
            gone = [(code, i) for i, o in ch.overrides.items() if o is None]
            put = [_override_row(code, i, o, uid) for i, o in ch.overrides.items() if o is not None]
            if gone:
                cur.executemany("DELETE FROM overrides WHERE project = %s AND object = %s", gone)
            if put:
                cur.executemany(
                    "INSERT INTO overrides (project, object, floor, type, name, number, hidden, ignored, capacity, "
                    "stack, floor_finish, wall_finish, changed_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s) ON CONFLICT (project, "
                    "object) DO UPDATE SET floor = EXCLUDED.floor, type = EXCLUDED.type, name = EXCLUDED.name, "
                    "number = EXCLUDED.number, hidden = EXCLUDED.hidden, ignored = EXCLUDED.ignored, "
                    "capacity = EXCLUDED.capacity, stack = EXCLUDED.stack, floor_finish = EXCLUDED.floor_finish, "
                    "wall_finish = EXCLUDED.wall_finish, changed_by = EXCLUDED.changed_by, changed_at = now(), "
                    "version = overrides.version + 1", put)
            gone = [(code, i) for i, it in ch.items.items() if it is None]
            put = [_item_row(code, it, uid) for it in ch.items.values() if it is not None]
            if gone:
                cur.executemany("DELETE FROM items WHERE project = %s AND id = %s", gone)
            if put:
                # an item's ID is the key of its row in the whole Studio: an asset is one
                # project's, and one of another project here is never written over
                other = cur.execute("SELECT id FROM items WHERE id = ANY(%s) AND project <> %s ORDER BY id LIMIT 1",
                                    ([row[1] for row in put], code)).fetchone()
                if other is not None:
                    raise Conflict(f"item {other[0]} is an item of another project here: an item's ID is one "
                                   "project's. Nothing was changed")
                cur.executemany(
                    "INSERT INTO items (project, id, floor, type, x, y, rotation, details, status, created_at, "
                    "retired_at, changed_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET floor = EXCLUDED.floor, type = EXCLUDED.type, "
                    "x = EXCLUDED.x, y = EXCLUDED.y, rotation = EXCLUDED.rotation, details = EXCLUDED.details, "
                    "status = EXCLUDED.status, created_at = EXCLUDED.created_at, retired_at = EXCLUDED.retired_at, "
                    "changed_by = EXCLUDED.changed_by, changed_at = now(), version = items.version + 1 "
                    "WHERE items.project = EXCLUDED.project", put)
            gone = [(code, t) for t, r in ch.readings.items() if r is None]
            put = [_reading_row(code, t, r) for t, r in ch.readings.items() if r is not None]
            if gone:
                cur.executemany("DELETE FROM readings WHERE project = %s AND text = %s", gone)
            if put:
                cur.executemany(
                    "INSERT INTO readings (project, text, type, source, rooms_only, asked) VALUES (%s, %s, %s, %s, %s, "
                    "%s) ON CONFLICT (project, text) DO UPDATE SET type = EXCLUDED.type, source = EXCLUDED.source, "
                    "rooms_only = EXCLUDED.rooms_only, asked = EXCLUDED.asked", put)
            gone = [(code, k) for k, v in ch.vision.items() if v is None]
            put = [(code, k, _j(v)) for k, v in ch.vision.items() if v is not None]
            if gone:
                cur.executemany("DELETE FROM vision WHERE project = %s AND key = %s", gone)
            if put:
                cur.executemany("INSERT INTO vision (project, key, answer) VALUES (%s, %s, %s) "
                                "ON CONFLICT (project, key) DO UPDATE SET answer = EXCLUDED.answer", put)
            for pos, record in sorted(ch.exports.items()):
                if record is None:
                    cur.execute("DELETE FROM exports WHERE project = %s AND position = %s", (code, pos))
                    continue
                data = ch.export_bytes.get(record.file)
                cur.execute(
                    "INSERT INTO exports (project, position, sequence, file, record, bytes, size, sha256) "
                    "VALUES (%(p)s, %(pos)s, %(seq)s, %(file)s, %(record)s, %(bytes)s, %(size)s, %(sha)s) "
                    "ON CONFLICT (project, position) DO UPDATE SET sequence = EXCLUDED.sequence, "
                    "record = EXCLUDED.record, "
                    "bytes = CASE WHEN EXCLUDED.file = exports.file THEN COALESCE(EXCLUDED.bytes, exports.bytes) "
                    "ELSE EXCLUDED.bytes END, "
                    "size = CASE WHEN EXCLUDED.file = exports.file THEN COALESCE(EXCLUDED.size, exports.size) "
                    "ELSE EXCLUDED.size END, "
                    "sha256 = CASE WHEN EXCLUDED.file = exports.file THEN COALESCE(EXCLUDED.sha256, exports.sha256) "
                    "ELSE EXCLUDED.sha256 END, file = EXCLUDED.file",
                    {"p": code, "pos": pos, "seq": record.sequence, "file": record.file,
                     "record": _j(record.model_dump(mode="json", exclude_none=True)), "bytes": data,
                     "size": len(data) if data is not None else None,
                     "sha": hashlib.sha256(data).hexdigest() if data is not None else None})
            for name, data in ch.drawings.items():
                if data is None:
                    cur.execute("DELETE FROM drawings WHERE project = %s AND name = %s AND NOT incoming", (code, name))
                else:
                    cur.execute(
                        "INSERT INTO drawings (project, name, bytes, size, sha256, words, uploaded_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (project, name) DO UPDATE SET "
                        "incoming = false, bytes = EXCLUDED.bytes, size = EXCLUDED.size, sha256 = EXCLUDED.sha256, "
                        "words = EXCLUDED.words, uploaded_by = EXCLUDED.uploaded_by, uploaded_at = now()",
                        (code, name, data, len(data), hashlib.sha256(data).hexdigest(), ch.words.get(name), uid))
        if ch.floors:
            conn.execute("UPDATE floors SET version = version + 1 WHERE project = %s AND id = ANY(%s)",
                         (code, sorted(ch.floors)))

    def _write_tree(self, conn, code: str, ch: Changes) -> None:
        order = {"locations": 0, "buildings": 1, "floors": 2}
        gone = sorted((n for n in ch.nodes if n[2] is None), key=lambda n: -order[n[0]])
        put = sorted((n for n in ch.nodes if n[2] is not None), key=lambda n: order[n[0]])
        for table, key, _ in gone:
            where = {"locations": "code = %s", "buildings": "location = %s AND code = %s",
                     "floors": "location = %s AND building = %s AND code = %s"}[table]
            conn.execute(f"DELETE FROM {table} WHERE project = %s AND {where}", (code, *key))
        for table, key, row in put:
            if table == "locations":
                cols = {"code": key[0], **row}
            elif table == "buildings":
                cols = {"location": key[0], "code": key[1], **row}
            else:
                cols = {"location": key[0], "building": key[1], "code": key[2],
                        "id": f"{code}-{key[0]}-{key[1]}-{key[2]}", **row}
            cols = {k: _j(v) if isinstance(v, (dict, list)) else v for k, v in cols.items()}
            keys = {"locations": ("code",), "buildings": ("location", "code"),
                    "floors": ("location", "building", "code")}[table]
            names = ", ".join(cols)
            values = ", ".join(f"%({k})s" for k in cols)
            sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in cols if k not in keys and k != "id")
            conn.execute(f"INSERT INTO {table} (project, {names}) VALUES (%(project)s, {values}) "
                         f"ON CONFLICT (project, {', '.join(keys)}) DO UPDATE SET {sets}", {"project": code, **cols})

    def _history(self, conn, code: str, version: int, ch: Changes, by) -> int:
        who = who_of(by)
        if isinstance(who, dict) and set(who) == {"id"}:
            row = conn.execute("SELECT username, name FROM users WHERE id = %s", (who["id"],)).fetchone()
            if row is not None:
                who = {"id": who["id"], "username": row[0], "name": row[1] or row[0]}
        row = conn.execute(
            "INSERT INTO history (project, version, who, part, kind, floors, targets, before, after, undoes, redoes, "
            "more) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING seq",
            (code, version, Jsonb(who), ch.part, ch.kind, sorted(ch.floors), list(ch.targets), _j(ch.before),
             _j(ch.after), ch.more.get("undoes"), ch.more.get("redoes"),
             Jsonb({k: v for k, v in ch.more.items() if k not in ("undoes", "redoes")}, dumps=_dumps))).fetchone()
        return row[0]

    # ---- history ------------------------------------------------------------------

    _ROW = "SELECT seq, version, at, who, part, kind, floors, targets, before, after, undoes, redoes, more FROM history "

    def _rows(self, sql: str, args) -> list[dict]:
        with self.db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(self._ROW + sql, args).fetchall()
        for r in rows:
            r["at"] = r["at"].isoformat()
            r.update({k: v for k, v in (r.pop("more") or {}).items() if k not in r})
        return rows

    def history(self, code: str, floor: str | None = None, limit: int = 50, after: int = 0,
                before: int | None = None) -> list[dict]:
        """The latest changes of a project (on ``floor``; after ``after``, before
        ``before``), newest first."""
        return self._rows("WHERE project = %s AND seq > %s AND (%s::bigint IS NULL OR seq < %s) AND "
                          "(%s::text IS NULL OR %s = ANY(floors)) ORDER BY seq DESC LIMIT %s",
                          (code, after, before, before, floor, floor, limit))

    def history_row(self, code: str, seq: int) -> dict | None:
        rows = self._rows("WHERE project = %s AND seq = %s", (code, seq))
        return rows[0] if rows else None

    def own_rows(self, code: str, who, parts, depth: int) -> list[dict]:
        """A person's latest ``depth`` changes of ``parts`` and every barrier among them (a
        file opened in place of the project or a building), oldest first: what their undo
        and redo are worked out from. ``who``: as history keeps them."""
        if isinstance(who, dict) and who.get("local"):
            mine, value = "who @> %s::jsonb", _dumps(LOCAL)
        elif isinstance(who, dict) and who.get("id"):
            mine, value = "who->>'id' = %s", who["id"]
        else:
            return []
        rows = self._rows(f"WHERE project = %s AND ((part = ANY(%s) AND {mine}) OR more @> '{{\"barrier\": true}}') "
                          "ORDER BY seq DESC LIMIT %s", (code, list(parts), value, depth))
        return rows[::-1]

    def later_rows(self, code: str, seq: int, targets, floors) -> list[dict]:
        """The changes after ``seq`` of any of ``targets``, or drawn on ``floors``, newest first."""
        return self._rows("WHERE project = %s AND seq > %s AND (targets && %s OR (part = 'edit' AND floors && %s)) "
                          "ORDER BY seq DESC LIMIT 500", (code, seq, list(targets), list(floors)))

    def undone(self, code: str, seqs) -> set[int]:
        """Of the changes ``seqs``, those undone (and not redone since)."""
        with self.db.connection() as conn:
            return {s for (s,) in conn.execute(
                "SELECT u.undoes FROM history u WHERE u.project = %s AND u.undoes = ANY(%s) AND NOT EXISTS "
                "(SELECT 1 FROM history r WHERE r.project = u.project AND r.redoes = u.seq)", (code, list(seqs)))}

    # ---- one editor a floor at a time -----------------------------------------------

    def _holder(self, conn, who_id: str | None) -> dict:
        """Who holds a lock, as the pages are told: {id, username, name}; this computer."""
        if who_id is None:
            return {"id": "local", "username": "local", "name": "This computer"}
        row = conn.execute("SELECT username, name FROM users WHERE id = %s", (who_id,)).fetchone()
        return {"id": who_id, "username": row[0] if row else "?", "name": (row[1] or row[0]) if row else "Someone"}

    def _take_locks(self, conn, code: str, floors, by, editor: Editor) -> list[str]:
        """The locks of ``floors`` taken (or kept) for the person ``by``, in the change's
        transaction (the project's row is held by it: lock takers come one at a time);
        Locked when another person holds one. The floors whose lock was not theirs before."""
        who = _user_id(by)
        taken = []
        for f in sorted(floors):
            row = conn.execute(
                "SELECT who, since, last_seen < now() - make_interval(secs => %s) FROM floor_locks WHERE floor = %s "
                "FOR UPDATE", (LOCK_IDLE_S, f)).fetchone()
            if row is not None and not row[2] and row[0] != who:
                holder = {"floor": f, "who": self._holder(conn, row[0]), "since": row[1].isoformat()}
                raise Locked(f"{holder['who']['name']} is editing this floor (since {row[1]:%H:%M} UTC): you can "
                             "look; you can edit when they are done", holder)
            if row is None or row[2] or row[0] != who:
                conn.execute("INSERT INTO floor_locks (floor, project, who, session) VALUES (%s, %s, %s, %s) "
                             "ON CONFLICT (floor) DO UPDATE SET who = EXCLUDED.who, session = EXCLUDED.session, "
                             "since = now(), last_seen = now()", (f, code, who, editor.session))
                taken.append(f)
            else:
                conn.execute("UPDATE floor_locks SET session = %s, last_seen = now() WHERE floor = %s",
                             (editor.session, f))
        return taken

    def _hold_project(self, conn, code: str) -> None:
        if conn.execute("SELECT 1 FROM projects WHERE code = %s FOR UPDATE", (code,)).fetchone() is None:
            raise NotFound(f"no project {code}")

    def _notify_locks(self, conn, code: str, floors) -> None:
        if floors:
            conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, _dumps({"project": code, "locks": sorted(floors)})))

    def take_lock(self, code: str, floor: str, by, editor: Editor) -> None:
        """A floor's lock taken (or kept) for a person before a step of theirs changes it
        (reading it again): Locked when another person holds it."""
        with self.db.transaction() as conn:
            self._hold_project(conn, code)
            self._notify_locks(conn, code, self._take_locks(conn, code, [floor], by, editor))

    def release_lock(self, code: str, floor: str, by) -> bool:
        """A person's lock of a floor let go (they left it, or are done editing it):
        whether they held it."""
        with self.db.transaction() as conn:
            gone = conn.execute("DELETE FROM floor_locks WHERE floor = %s AND project = %s AND who IS NOT DISTINCT "
                                "FROM %s RETURNING floor", (floor, code, _user_id(by))).fetchone()
            if gone:
                self._notify_locks(conn, code, [floor])
        return gone is not None

    def take_over(self, code: str, floor: str, by, editor: Editor) -> dict | None:
        """A floor's lock taken from whoever holds it (by an admin), recorded in the history
        as the floor taken over: who held it ({id, username, name}), None when nobody did
        (then it is just taken)."""
        who = _user_id(by)
        with self.db.transaction() as conn:
            self._hold_project(conn, code)
            row = conn.execute("SELECT who, last_seen < now() - make_interval(secs => %s) FROM floor_locks "
                               "WHERE floor = %s FOR UPDATE", (LOCK_IDLE_S, floor)).fetchone()
            held = row is not None and not row[1] and row[0] != who
            was = self._holder(conn, row[0]) if held else None
            conn.execute("INSERT INTO floor_locks (floor, project, who, session) VALUES (%s, %s, %s, %s) "
                         "ON CONFLICT (floor) DO UPDATE SET who = EXCLUDED.who, session = EXCLUDED.session, "
                         "since = now(), last_seen = now()", (floor, code, who, editor.session))
            if held:
                version = conn.execute("SELECT version FROM projects WHERE code = %s", (code,)).fetchone()[0]
                ch = Changes(part="floor", kind="take over", targets=[floor], floors={floor},
                             before={"lock": was}, after={"lock": self._holder(conn, who)},
                             more={"from_name": was["name"]})
                seq = self._history(conn, code, version, ch, by)
                conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, _dumps({
                    "project": code, "floors": [floor], "seq": seq, "version": version, "locks": [floor]})))
            else:
                self._notify_locks(conn, code, [floor])
        return was

    def locks(self, code: str, floors=None) -> dict[str, dict]:
        """The floors of a project someone is editing (of ``floors``, when given): floor ->
        {floor, who: {id, username, name}, since, session}."""
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT l.floor, l.who, u.username, u.name, l.since, l.session FROM floor_locks l "
                "LEFT JOIN users u ON u.id = l.who WHERE l.project = %s AND l.last_seen >= now() - "
                "make_interval(secs => %s) AND (%s::text[] IS NULL OR l.floor = ANY(%s))",
                (code, LOCK_IDLE_S, None if floors is None else list(floors),
                 None if floors is None else list(floors))).fetchall()
        out = {}
        for floor, who, username, name, since, session in rows:
            person = {"id": "local", "username": "local", "name": "This computer"} if who is None \
                else {"id": who, "username": username or "?", "name": name or username or "Someone"}
            out[floor] = {"floor": floor, "who": person, "since": since.isoformat(), "session": session}
        return out

    def touch_locks(self, code: str, floors, by) -> int:
        """A person's locks of ``floors`` kept (their page is open on them): how many."""
        if not floors:
            return 0
        with self.db.transaction() as conn:
            return conn.execute("UPDATE floor_locks SET last_seen = now() WHERE project = %s AND floor = ANY(%s) AND "
                                "who IS NOT DISTINCT FROM %s AND last_seen >= now() - make_interval(secs => %s)",
                                (code, list(floors), _user_id(by), LOCK_IDLE_S)).rowcount

    def sweep_locks(self) -> dict[str, list[str]]:
        """The locks nothing kept for LOCK_IDLE_S let go: project -> its floors let go
        (each project's pages told)."""
        with self.db.transaction() as conn:
            rows = conn.execute("DELETE FROM floor_locks WHERE last_seen < now() - make_interval(secs => %s) "
                                "RETURNING project, floor", (LOCK_IDLE_S,)).fetchall()
            out: dict[str, list[str]] = {}
            for project, floor in rows:
                out.setdefault(project, []).append(floor)
            for project, floors in out.items():
                self._notify_locks(conn, project, floors)
        return out

    # ---- drawings -----------------------------------------------------------------

    def drawings(self, code: str, incoming: bool = False) -> list[dict]:
        """The project's drawings (name, size, sha256, uploaded_at), by name; ``incoming``:
        those sent and waiting for a person instead."""
        with self.db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            return cur.execute("SELECT name, size, sha256, uploaded_at, uploaded_by FROM drawings "
                               "WHERE project = %s AND incoming = %s ORDER BY name", (code, incoming)).fetchall()

    def drawing(self, code: str, name: str, incoming: bool = False) -> dict | None:
        """A drawing's name, size and sha256 (not its bytes); None when there is none."""
        with self.db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            return cur.execute("SELECT name, size, sha256, words IS NOT NULL AS has_words FROM drawings "
                               "WHERE project = %s AND name = %s AND incoming = %s",
                               (code, name, incoming)).fetchone()

    def drawing_bytes(self, code: str, name: str, incoming: bool = False) -> bytes:
        with self.db.connection() as conn:
            row = conn.execute("SELECT bytes FROM drawings WHERE project = %s AND name = %s AND incoming = %s",
                               (code, name, incoming)).fetchone()
        if row is None:
            raise NotFound(f"no drawing {name}")
        return bytes(row[0])

    def put_drawing(self, code: str, name: str, data: bytes, *, words: str | None = None, by=None) -> None:
        """A drawing kept in the project (in place of one of that name)."""

        def fn(base):
            ch = Changes(part="project", kind="drawing", targets=[name], drawings={name: data},
                         more={"size": len(data)})
            if words is not None:
                ch.words[name] = words
            return ch, None

        self.change(code, fn, by=by)

    def remove_drawing(self, code: str, name: str, by=None) -> None:
        """A drawing of the project taken away (a floor read from it keeps what was read)."""
        if self.drawing(code, name) is None:
            raise NotFound(f"no drawing {name}")
        self.change(code, lambda base: (Changes(part="project", kind="drawing removed", targets=[name],
                                                drawings={name: None}), None), by=by)

    def put_incoming(self, code: str, name: str, data: bytes, by=None) -> None:
        """A drawing sent, kept until a person says what of it to keep (not yet the
        project's: no change of it)."""
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE code = %s", (code,)).fetchone() is None:
                raise NotFound(f"no project {code}")
            conn.execute("INSERT INTO drawings (project, name, incoming, bytes, size, sha256, uploaded_by) "
                         "VALUES (%s, %s, true, %s, %s, %s, %s) ON CONFLICT (project, name) DO UPDATE SET "
                         "bytes = EXCLUDED.bytes, size = EXCLUDED.size, sha256 = EXCLUDED.sha256",
                         (code, name, data, len(data), hashlib.sha256(data).hexdigest(), _user_id(by)))

    def drop_incoming(self, code: str | None = None, name: str | None = None) -> int:
        """Drawings sent and waiting taken away: one, a project's, or all (Studio started
        again: none is waiting any more)."""
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM drawings WHERE incoming AND (%s::text IS NULL OR project = %s) "
                                "AND (%s::text IS NULL OR name = %s)", (code, code, name, name)).rowcount

    def words(self, code: str, name: str) -> str | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT words FROM drawings WHERE project = %s AND name = %s AND NOT incoming",
                               (code, name)).fetchone()
        if row is None:
            raise NotFound(f"no drawing {name}")
        return row[0]

    def set_words(self, code: str, name: str, words: str) -> None:
        """The words read in a drawing kept with it (what is in it does not change)."""
        with self.db.transaction() as conn:
            conn.execute("UPDATE drawings SET words = %s WHERE project = %s AND name = %s AND NOT incoming",
                         (words, code, name))

    @contextmanager
    def files(self, code: str, names=None, folder: str | Path | None = None, incoming: bool = False):
        """The project's drawings (``names``, or all) as files, in a folder of their own
        (``<folder>/drawings/<name>``) made for as long as the block runs, then removed:
        for what reads drawings from files. The folder is given to the block."""
        where = Path(tempfile.mkdtemp(prefix="storeypath-", dir=folder))
        try:
            (where / DRAWINGS).mkdir()
            with self.db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT name, bytes FROM drawings WHERE project = %s AND incoming = %s "
                                "AND (%s::text[] IS NULL OR name = ANY(%s))",
                                (code, incoming, None if names is None else list(names),
                                 None if names is None else list(names)))
                    for name, data in cur:
                        (where / DRAWINGS / drawing_name(name)).write_bytes(data)
            yield where
        finally:
            shutil.rmtree(where, ignore_errors=True)

    # ---- exports ------------------------------------------------------------------

    def export_files(self, code: str) -> list[str]:
        """The packages kept of the project (their file names), newest first."""
        with self.db.connection() as conn:
            return sorted((f for (f,) in conn.execute(
                "SELECT DISTINCT file FROM exports WHERE project = %s AND bytes IS NOT NULL", (code,))), reverse=True)

    def export_bytes(self, code: str, file: str) -> bytes:
        with self.db.connection() as conn:
            row = conn.execute("SELECT bytes FROM exports WHERE project = %s AND file = %s AND bytes IS NOT NULL "
                               "ORDER BY position DESC NULLS LAST LIMIT 1", (code, file)).fetchone()
        if row is None:
            raise NotFound(f"no export {file}")
        return bytes(row[0])

    def keep_package(self, code: str, file: str, data: bytes) -> None:
        """A package kept with no record of it in the project's export history (made by a
        Studio before records were): listed and sent as it is."""
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM exports WHERE project = %s AND file = %s", (code, file)).fetchone():
                conn.execute("UPDATE exports SET bytes = COALESCE(bytes, %s), size = COALESCE(size, %s), "
                             "sha256 = COALESCE(sha256, %s) WHERE project = %s AND file = %s",
                             (data, len(data), hashlib.sha256(data).hexdigest(), code, file))
            else:
                conn.execute("INSERT INTO exports (project, file, bytes, size, sha256) VALUES (%s, %s, %s, %s, %s)",
                             (code, file, data, len(data), hashlib.sha256(data).hexdigest()))

    # ---- the catalogue of item types (the organization's) ----------------------------

    def catalogue(self):
        """The item types of every project (catalogue.py): the default ones the first
        time, and the default types a newer Studio brings that it lacks (and what it
        says of its own types where they say nothing) added. A copy of one's own."""
        from .. import catalogue as catalogues

        with self.db.connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'catalogue'").fetchone()
        version = row[0] if row else None
        kept = self._catalogue
        if kept is not None and version is not None and kept[0] == version:
            return kept[1].model_copy(deep=True)
        with self.db.connection() as conn:
            types = [t for (t,) in conn.execute("SELECT type FROM catalogue ORDER BY position")]
        if version is None and not types:
            cat = catalogues.default_catalogue()
            self.save_catalogue(cat)
            return cat
        cat, filled = catalogues.read(json.dumps({"types": types}))
        cat.check()
        known = {t.code for t in cat.types}
        new = [t for t in catalogues.default_catalogue().types if t.code not in known]
        if new or filled:
            cat.types.extend(new)
            self.save_catalogue(cat)
            return cat
        self._catalogue = (version, cat.model_copy(deep=True))
        return cat

    def save_catalogue(self, cat) -> None:
        cat.check()
        with self.db.transaction() as conn:
            row = conn.execute("INSERT INTO settings (key, value) VALUES ('catalogue', '1') ON CONFLICT (key) DO "
                               "UPDATE SET value = to_jsonb(settings.value::text::bigint + 1) RETURNING value").fetchone()
            conn.execute("DELETE FROM catalogue")
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO catalogue (code, position, type) VALUES (%s, %s, %s)",
                                [(t.code, i, _j(t.model_dump(mode="json"))) for i, t in enumerate(cat.types)])
        self._catalogue = (row[0], cat.model_copy(deep=True))


# ---- what differs ----------------------------------------------------------------

def diff(ws: Workspace, base: Workspace, scope=None) -> Changes:
    """What makes ``base`` (the project as it is) into ``ws``: the tree, readings, vision
    and export history whole; objects, corrections and items within ``scope`` (None: all).
    The values are copies (``ws`` may go on being changed)."""
    ch = Changes()
    p = {}
    if ws.project.name != base.project.name:
        p["name"] = ws.project.name
    if ws.project.created_at != base.project.created_at:
        p["created_at"] = ws.project.created_at
    if ws.format_version != base.format_version:
        p["format_version"] = ws.format_version
    ch.project = p or None

    new_nodes, old_nodes = _tree_rows(ws), _tree_rows(base)
    for key, (row, node) in new_nodes.items():
        old = old_nodes.get(key)
        if key[0] == "floors":
            if old is None or old[0] != row or not _same_floor(node, old[1]):
                ch.nodes.append((key[0], key[1:], {**row, **_floor_row(node)}))
                ch.floors.add(f"{ws.id}-{'-'.join(key[1:])}")
        elif old is None or old[0] != row:
            ch.nodes.append((key[0], key[1:], row))
    for key in old_nodes.keys() - new_nodes.keys():
        ch.nodes.append((key[0], key[1:], None))
    if ch.nodes:
        ch.tree = [loc.model_copy(deep=True) for loc in ws.locations]

    def scoped(keys, floor):
        return {k for k in keys if _in_scope(scope, floor(k))}

    for name, floor in (("objects", floor_of), ("overrides", floor_of)):
        mine, theirs = getattr(ws, name), getattr(base, name)
        out = getattr(ch, name)
        for k in scoped(mine.keys(), floor):
            if k not in theirs or not _same(mine[k], theirs[k]):
                out[k] = mine[k].model_copy(deep=True)
        for k in scoped(theirs.keys(), floor) - mine.keys():
            out[k] = None
    # (an item is in scope by where it is in ws, or where it was)
    mine_items = {k for k, it in ws.items.items() if _in_scope(scope, it.floor_id or "")}
    their_items = {k for k, it in base.items.items() if _in_scope(scope, it.floor_id or "")}
    for k in mine_items:
        if k not in base.items or not _same(ws.items[k], base.items[k]):
            ch.items[k] = ws.items[k].model_copy(deep=True)
    for k in their_items - ws.items.keys():
        ch.items[k] = None
    if scope is not None:
        for k in their_items & ws.items.keys() - mine_items:  # carried out of the scope
            if not _same(ws.items[k], base.items[k]):
                ch.items[k] = ws.items[k].model_copy(deep=True)
    for k, r in ws.readings.items():
        if k not in base.readings or not _same(r, base.readings[k]):
            ch.readings[k] = r.model_copy(deep=True)
    for k in base.readings.keys() - ws.readings.keys():
        ch.readings[k] = None
    for k, v in ws.vision.items():
        if k not in base.vision or not _same(v, base.vision[k]):
            ch.vision[k] = json.loads(_dumps(v))
    for k in base.vision.keys() - ws.vision.keys():
        ch.vision[k] = None
    for i, r in enumerate(ws.exports):
        if i >= len(base.exports) or not _same(r, base.exports[i]):
            ch.exports[i] = r.model_copy(deep=True)
    for i in range(len(ws.exports), len(base.exports)):
        ch.exports[i] = None

    for i in (*ch.objects, *ch.overrides):
        ch.floors.add(floor_of(i))
    for i, it in ch.items.items():
        for f in (it.floor_id if it is not None else None, base.items[i].floor_id if i in base.items else None):
            if f:
                ch.floors.add(f)
    known = {f"{ws.id}-{loc.code}-{b.code}-{f.code}" for loc in ws.locations for b in loc.buildings for f in b.floors}
    ch.floors &= known | {f"{base.id}-{loc.code}-{b.code}-{f.code}" for loc in base.locations
                          for b in loc.buildings for f in b.floors}
    return ch


def _same_floor(a, b) -> bool:
    return a is b or _same(a, b)
