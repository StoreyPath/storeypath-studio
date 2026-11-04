"""Projects in Studio's database (ProjectStore): each project whole — its tree,
objects, corrections, items, readings, furniture layouts, what the vision model saw, its export history
with the packages as sent, its drawings — and every change to it, recorded.

Reading. ``current(code)`` is the project as it is now, as the Workspace the rest of
Studio works on: kept in memory, and read again (one statement, the database making
the JSON the Workspace is read from) when its ``version`` moved. It is shared: never
to be changed. ``load(code, building=None, floors=None)`` is a Workspace of one's own,
to change and save: whole, or with only one building's or some floors' objects,
corrections and items (the tree, readings, furniture layouts, vision and exports are always whole).

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

Many people on a floor, one a room. A person's change from a page (``editor``: their
session, the page) takes the lock of each room it touches for that person
(rooms_touched: a space, its zones and doors, the items in it, what is drawn on it; an
item standing in no room by its own ID), in its own transaction (``room_locks``), or is
refused (Locked, 423, naming who holds which room) while another person holds one. A
room's lock goes when its person is done editing the floor or leaves it (release_lock),
when they made no change to it for ROOM_IDLE_S (a page left open keeps none: others are
not kept out of rooms a person went past), or when an admin takes it over (take_over,
recorded in the history). A person reading a whole floor again waits until nobody else
holds a room on it (whole_floor). Steps of Studio's own (a floor read again, an export)
take no room locks: they hold the project's step lock (StepLock) while they change it,
which also keeps every other Studio on the database out of it.

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
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from shapely.geometry import LineString, Point, Polygon, shape

from ..errors import Conflict, Locked, NotFound
from ..ids import LAND_CODE
from ..workspace import (ExportRecord, FloorEdits, Furnishing, Item, Location, ObjectRecord, Override, Project,
                         Reading, Workspace)
from . import CHANNEL, Database

COMMAND_LINE = "command line"  # who a change from the command line is of
LOCAL = {"local": True}  # who a change is of on this computer without accounts
OBJECT_MORE = ("connects", "parent", "zones", "span", "width", "swings", "sill", "height", "tag", "issues",
               "detected_ignored", "match", "levels")
DRAWINGS = "drawings"  # a floor's drawing is drawings/<name>: its name in the project's drawings
ROOM_IDLE_S = 5 * 60  # a room its person made no change to for this long is free again
NEAR_M = 0.3  # what is drawn touches the rooms within this of it (half a thick wall)
STEP_LOCK = 0x5370  # pg_advisory_lock(STEP_LOCK, hashtext(code)): a project-wide step works on the project


class ProjectExists(Exception):
    """A project of that code is in the database already."""


@dataclass(frozen=True)
class Editor:
    """Where a person's change comes from: their session (whose room locks it takes)
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


# ---- the rooms a change touches (room locks) ----------------------------------------

def rooms_touched(base: Workspace, after: Workspace, ch: Changes) -> dict[str, set[str]]:
    """The rooms a change touches, by floor (each the lock it takes): a space changed or
    corrected; a zone's own space; the spaces a door or opening joins; the space an item
    stands in, where it was and where it is (the item itself, by its ID, where it stands
    in none); the spaces within NEAR_M of what was drawn on a floor, or taken away."""
    out: dict[str, set[str]] = {}
    for oid in (*ch.objects, *ch.overrides):
        for ws in (base, after):
            for room in _rooms_of(ws.objects.get(oid), oid):
                out.setdefault(floor_of(oid), set()).add(room)
    for iid in ch.items:
        for ws in (base, after):
            it = ws.items.get(iid)
            if it is not None and it.floor_id:
                out.setdefault(it.floor_id, set()).add(room_at(ws, it.floor_id, (it.x, it.y)) or iid)
    for fid, edits in ch.edits.items():
        near = rooms_near(base, fid, _drawn_changed(_edits_of(base, fid), edits))
        if near:
            out.setdefault(fid, set()).update(near)
    return out


def rooms_near(ws: Workspace, floor: str, shapes) -> set[str]:
    """The rooms of a floor within NEAR_M of any of ``shapes`` (shapely geometries)."""
    near = [g.buffer(NEAR_M) for g in shapes]
    return {sid for sid, g in _spaces(ws, floor) if any(n.intersects(g) for n in near)} if near else set()


def room_at(ws: Workspace, floor: str, at) -> str | None:
    """The room (a space's ID) a point on a floor is in, as Review places an item there
    (review_app/fit.js roomAt): a space not ignored, or the space of a zone it is in."""
    p = Point(at)
    hit = next((sid for sid, g in _spaces(ws, floor) if g.covers(p)), None)
    if hit is None:
        hit = next((sid for sid, g in _spaces(ws, floor, kind="zone") if g.covers(p)), None)
    return hit


def room_name(ws: Workspace, room: str) -> str:
    """A room as people know it: its name and number, else its code; an item by its tag."""
    if room in ws.items:
        return f"the item {room}"
    o = ws.objects.get(room)
    if o is None:
        return room.rsplit("-", 1)[-1]
    fix = ws.overrides.get(room)
    name = (fix.name if fix and fix.name else None) or o.name
    number = (fix.number if fix and fix.number else None) or o.number
    return " ".join(x for x in (name, number) if x) or room.rsplit("-", 1)[-1]


def _rooms_of(o: ObjectRecord | None, oid: str) -> list[str]:
    if o is None or o.kind == "space":
        return [oid]
    if o.kind == "zone":
        return [o.parent or oid]
    return list(o.connects) or [oid]  # a door, a window, an opening: the spaces it joins


def _spaces(ws: Workspace, floor: str, kind: str = "space"):
    """The floor's rooms not retired nor ignored (zones: as their space), with their
    shapes: (room ID, shapely geometry)."""
    prefix = floor + "-"
    for oid, o in ws.objects.items():
        if o.kind != kind or o.status != "active" or not oid.startswith(prefix) or not o.geometry:
            continue
        fix = ws.overrides.get(oid)
        if (fix.ignored if fix is not None and fix.ignored is not None else o.detected_ignored):
            continue
        try:
            g = shape(o.geometry)
        except (ValueError, TypeError, AttributeError):
            continue
        yield (o.parent or oid) if kind == "zone" else oid, g


def _edits_of(ws: Workspace, floor: str) -> FloorEdits:
    for *_, f, f_id in ws.iter_floors(layouts=True):
        if f_id == floor:
            return f.edits
    return FloorEdits()


def _drawn_changed(old: FloorEdits, new: FloorEdits) -> list:
    """What was drawn on a floor, or taken away, between two of its edits: shapes."""
    shapes = []
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    for name in b:
        for x in [x for x in a.get(name, []) if x not in b[name]] + [x for x in b[name] if x not in a.get(name, [])]:
            try:
                if name in ("walls", "dividers"):
                    shapes.append(LineString(x))
                elif name == "openings":
                    shapes.append(LineString(x["span"]))
                elif name == "resized":
                    shapes.append(Point(x["at"]).buffer(max(0.5, (x.get("width") or 1) / 2)))
                elif name == "spaces":
                    shapes.append(Polygon(x))
            except (ValueError, TypeError, KeyError):
                continue
    return shapes


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
    furnishings: dict[str, Furnishing | None] = field(default_factory=dict)  # furniture layouts, by ID
    vision: dict[str, dict | None] = field(default_factory=dict)
    exports: dict[int, ExportRecord | None] = field(default_factory=dict)  # by position
    export_bytes: dict[str, bytes] = field(default_factory=dict)  # file -> the package as sent
    drawings: dict[str, bytes | None] = field(default_factory=dict)  # name -> its bytes (kept), or None (gone)
    words: dict[str, str] = field(default_factory=dict)  # name -> the words of a drawing put now
    sent: dict[str, str] = field(default_factory=dict)  # name -> the sha256 of a drawing put now, as it was sent
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
                    or self.items or self.readings or self.furnishings or self.vision or self.exports or self.export_bytes
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
        for name in ("objects", "overrides", "items", "readings", "furnishings", "vision"):
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
    """The tree with these floors' drawn edits (a layout's too), sharing the rest of it."""

    def edited(loc: Location, b):
        floors = []
        for f in b.floors:
            f_id = f"{ws.id}-{loc.code}-{b.code}-{f.code}"
            floors.append(f.model_copy(update={"edits": edits[f_id]}) if f_id in edits else f)
        return b.model_copy(update={"floors": floors}) if any(x is not y for x, y in zip(floors, b.floors)) else b

    out = []
    for loc in ws.locations:
        buildings = [edited(loc, b) for b in loc.buildings]
        layout = edited(loc, loc.layout) if loc.layout is not None else None
        changed = any(x is not y for x, y in zip(buildings, loc.buildings)) or layout is not loc.layout
        out.append(loc.model_copy(update={"buildings": buildings, "layout": layout}) if changed else loc)
    return out


# ---- the rows of a project -------------------------------------------------------

def _tree_rows(ws: Workspace) -> dict[tuple, tuple[dict, Any]]:
    """Every node of the tree as its row: (table, key…) -> (row, node)."""
    out = {}
    for i, loc in enumerate(ws.locations):
        out["locations", loc.code] = ({"position": i, "name": loc.name, "address": loc.address,
                                       "placement": loc.placement.model_dump(mode="json") if loc.placement else None,
                                       "site_origin": list(loc.site_origin) if loc.site_origin is not None else None,
                                       "north": loc.north.model_dump(mode="json") if loc.north else None},
                                      loc)
        # (its land, its layout's, a row of buildings of its own code after its buildings)
        land = [loc.layout] if loc.layout is not None else []
        for j, b in enumerate([*loc.buildings, *land]):
            out["buildings", loc.code, b.code] = (
                {"position": j, "name": b.name, "next_object_seq": b.next_object_seq,
                 "placement": b.placement.model_dump(mode="json") if b.placement else None,
                 "site": b.site.model_dump(mode="json") if b.site else None}, b)
            for k, f in enumerate(b.floors):
                out["floors", loc.code, b.code, f.code] = ({"position": k}, f)
    return out


def _floor_row(f) -> dict:
    d = f.model_dump(mode="json")
    return {k: d[k] for k in ("name", "ordinal", "elevation", "height", "type", "host", "above_host", "parapet_height",
                              "source", "outline",
                              "converted_at", "method", "warnings", "layers", "walls", "wall_thickness",
                              "symbols", "symbols_key", "edits", "north")}


def _object_row(code: str, r: ObjectRecord) -> tuple:
    d = r.model_dump(mode="json")
    more = {k: d[k] for k in OBJECT_MORE if not (d[k] is None or d[k] == [] or d[k] is False)}
    return (code, r.id, floor_of(r.id), r.kind, r.type, r.type_source, r.name, r.number, r.label,
            _j(d["geometry"]), _j(more), r.status, r.created_at, r.retired_at)


def _override_row(code: str, object_id: str, o: Override, by) -> tuple:
    return (code, object_id, floor_of(object_id), o.type.value if o.type is not None else None, o.name, o.number,
            o.hidden, o.ignored, o.capacity, o.stack, o.floor_finish, o.wall_finish,
            _j([w.model_dump() for w in o.wall_finishes]) if o.wall_finishes else None, o.building, by)


def _item_row(code: str, it: Item, by) -> tuple:
    return (code, it.id, it.floor_id or "", it.type, it.x, it.y, it.rotation, _j(dict(it.values)), it.group, it.door_id,
            it.status, it.created_at, it.retired_at, by)


def _reading_row(code: str, text: str, r: Reading) -> tuple:
    return (code, text, r.type.value if r.type is not None else None, r.source, r.rooms_only, r.asked)


# the project as the JSON a Workspace is read from, made by the database
_OBJECT_JSON = ("jsonb_build_object('id', o.id, 'kind', o.kind, 'type', o.type, 'type_source', o.type_source, "
                "'name', o.name, 'number', o.number, 'label', o.label, 'geometry', o.geometry, 'status', o.status, "
                "'created_at', o.created_at, 'retired_at', o.retired_at) || o.more")
_FLOOR_JSON = ("jsonb_build_object('code', f.code, 'name', f.name, 'ordinal', f.ordinal, 'elevation', f.elevation, "
               "'height', f.height, 'type', f.type, 'host', f.host, 'above_host', f.above_host, "
               "'parapet_height', f.parapet_height, 'source', f.source, 'outline', f.outline, "
               "'converted_at', f.converted_at, 'method', f.method, 'warnings', f.warnings, 'layers', f.layers, "
               "'walls', f.walls, 'wall_thickness', f.wall_thickness, 'symbols', f.symbols, "
               "'symbols_key', f.symbols_key, 'edits', f.edits, 'north', f.north)")
_BUILDING_JSON = ("json_build_object('code', b.code, 'name', b.name, 'placement', b.placement, 'site', b.site, "
                  "'next_object_seq', b.next_object_seq, "
                  f"'floors', COALESCE((SELECT json_agg({_FLOOR_JSON} ORDER BY f.position) FROM floors f "
                  "WHERE f.project = b.project AND f.location = b.location AND f.building = b.code), '[]'))")
_LOAD = f"""
SELECT p.version, json_build_object(
  'format', 'storeypath-workspace', 'format_version', p.format_version,
  'project', json_build_object('code', p.code, 'name', p.name, 'created_at', p.created_at),
  'locations', COALESCE((SELECT json_agg(json_build_object(
      'code', l.code, 'name', l.name, 'address', l.address, 'placement', l.placement, 'site_origin', l.site_origin,
      'north', l.north,
      'buildings', COALESCE((SELECT json_agg({_BUILDING_JSON} ORDER BY b.position) FROM buildings b
                             WHERE b.project = l.project AND b.location = l.code AND b.code <> '{LAND_CODE}'), '[]'),
      'layout', (SELECT {_BUILDING_JSON} FROM buildings b
                 WHERE b.project = l.project AND b.location = l.code AND b.code = '{LAND_CODE}'))
    ORDER BY l.position) FROM locations l WHERE l.project = p.code), '[]'),
  'objects', COALESCE((SELECT json_object_agg(o.id, {_OBJECT_JSON} ORDER BY o.position) FROM objects o
                       WHERE o.project = p.code AND {{scope_o}}), '{{{{}}}}'),
  'overrides', COALESCE((SELECT json_object_agg(v.object, json_build_object(
      'type', v.type, 'name', v.name, 'number', v.number, 'hidden', v.hidden, 'ignored', v.ignored,
      'capacity', v.capacity, 'stack', v.stack, 'floor_finish', v.floor_finish, 'wall_finish', v.wall_finish,
      'wall_finishes', v.wall_finishes, 'building', v.building)
      ORDER BY v.position) FROM overrides v WHERE v.project = p.code AND {{scope_v}}), '{{{{}}}}'),
  'items', COALESCE((SELECT json_object_agg(i.id, json_build_object(
      'id', i.id, 'type', i.type, 'floor_id', i.floor, 'x', i.x, 'y', i.y, 'rotation', i.rotation,
      'values', i.details, 'group', i.group_key, 'door_id', i.door, 'status', i.status, 'created_at', i.created_at, 'retired_at', i.retired_at)
      ORDER BY i.position) FROM items i WHERE i.project = p.code AND {{scope_i}}), '{{{{}}}}'),
  'readings', COALESCE((SELECT json_object_agg(r.text, json_build_object(
      'type', r.type, 'source', r.source, 'rooms_only', r.rooms_only, 'asked', r.asked) ORDER BY r.position)
      FROM readings r WHERE r.project = p.code), '{{{{}}}}'),
  'furnishings', COALESCE((SELECT json_object_agg(n.id, json_build_object(
      'id', n.id, 'name', n.name, 'floor_id', n.floor, 'room', n.room, 'width', n.width, 'depth', n.depth,
      'pieces', n.pieces, 'created_by', n.created_by, 'created_at', n.created_at) ORDER BY n.position)
      FROM furnishings n WHERE n.project = p.code), '{{{{}}}}'),
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


@dataclass(frozen=True)
class Touched:
    """What one change touched (ProjectStore.changed_since): objects, corrections (by
    their object) and items, by ID, and the floors whose own row changed (their walls,
    outline, drawn edits…)."""

    ids: frozenset
    floors: frozenset


CHANGES_KEPT = 2000  # a project's latest changes whose IDs are kept (changed_since)


# ---- the store -------------------------------------------------------------------

class ProjectStore:
    """The projects of a Studio's database."""

    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._kept: dict[str, _Kept] = {}
        self._touched: dict[str, OrderedDict[int, Touched]] = {}  # code -> version -> what that change touched
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
        return self.now(code)[1]

    def now(self, code: str) -> tuple[int, Workspace]:
        """The project as it is now (current), with its version."""
        version = self.version(code)
        if version is None:
            raise NotFound(f"no project {code}")
        kept = self._kept.get(code)
        if kept is not None and kept.version == version:
            return kept.version, kept.ws
        with self._lock:
            reading = self._reading.setdefault(code, threading.Lock())
        with reading:  # read once, however many ask at once
            kept = self._kept.get(code)
            if kept is not None and kept.version >= version:
                return kept.version, kept.ws
            with self.db.connection() as conn:
                version, ws = self._read(conn, code)
            self._keep(code, version, ws)
            return version, ws

    def changed_since(self, code: str, since: int, until: int) -> Touched | None:
        """What the changes after version ``since`` up to ``until`` touched, together;
        None when this Studio does not know each of them (made by another Studio on the
        database, before it started, or too long ago)."""
        if since > until:
            return None
        with self._lock:
            log = self._touched.get(code, {})
            if any(v not in log for v in range(since + 1, until + 1)):
                return None
            changes = [log[v] for v in range(since + 1, until + 1)]
        return Touched(frozenset().union(*(t.ids for t in changes)), frozenset().union(*(t.floors for t in changes)))

    def _touch(self, code: str, version: int, ch: Changes) -> None:
        floors = {f"{code}-{'-'.join(key)}" for table, key, _ in ch.nodes if table == "floors"} | set(ch.edits)
        touched = Touched(frozenset([*ch.objects, *ch.overrides, *ch.items]), frozenset(floors))
        with self._lock:
            log = self._touched.setdefault(code, OrderedDict())
            log[version] = touched
            while len(log) > CHANGES_KEPT:
                log.popitem(last=False)

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
        lock of each room it touches (rooms_touched; Locked while another person holds one)."""
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
                after = changes.apply(base)
                taken = self._take_locks(conn, code, rooms_touched(base, after, changes), by, editor, base) \
                    if editor is not None else []
                self._write(conn, code, changes, by)
                seq = self._history(conn, code, version, changes, by)
                said = {"project": code, "floors": sorted(changes.floors), "seq": seq, "version": version}
                if editor is not None and editor.page:
                    said["page"] = editor.page
                if taken:
                    said["locks"] = taken
                conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, _dumps(said)))
                changed = (version, after)
        except _Nothing:
            return result
        self._touch(code, changed[0], changes)
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
            self._touched.pop(code, None)

    def forget(self, code: str | None = None) -> None:
        """What is kept in memory of a project (of every project) forgotten: read again."""
        with self._lock:
            if code is None:
                self._kept.clear()
                self._touched.clear()
            else:
                self._kept.pop(code, None)
                self._touched.pop(code, None)

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
                    "stack, floor_finish, wall_finish, wall_finishes, building, changed_by) VALUES (%s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (project, "
                    "object) DO UPDATE SET floor = EXCLUDED.floor, type = EXCLUDED.type, name = EXCLUDED.name, "
                    "number = EXCLUDED.number, hidden = EXCLUDED.hidden, ignored = EXCLUDED.ignored, "
                    "capacity = EXCLUDED.capacity, stack = EXCLUDED.stack, floor_finish = EXCLUDED.floor_finish, "
                    "wall_finish = EXCLUDED.wall_finish, wall_finishes = EXCLUDED.wall_finishes, "
                    "building = EXCLUDED.building, "
                    "changed_by = EXCLUDED.changed_by, changed_at = now(), "
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
                    "INSERT INTO items (project, id, floor, type, x, y, rotation, details, group_key, door, status, "
                    "created_at, retired_at, changed_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET floor = EXCLUDED.floor, type = EXCLUDED.type, "
                    "x = EXCLUDED.x, y = EXCLUDED.y, rotation = EXCLUDED.rotation, details = EXCLUDED.details, "
                    "group_key = EXCLUDED.group_key, door = EXCLUDED.door, "
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
            gone = [(code, k) for k, f in ch.furnishings.items() if f is None]
            put = [(code, f.id, f.name, f.floor_id, f.room, f.width, f.depth,
                    _j([p.model_dump(mode="json") for p in f.pieces]), f.created_by, f.created_at)
                   for f in ch.furnishings.values() if f is not None]
            if gone:
                cur.executemany("DELETE FROM furnishings WHERE project = %s AND id = %s", gone)
            if put:
                cur.executemany(
                    "INSERT INTO furnishings (project, id, name, floor, room, width, depth, pieces, created_by, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (project, id) DO UPDATE SET "
                    "name = EXCLUDED.name, floor = EXCLUDED.floor, room = EXCLUDED.room, width = EXCLUDED.width, "
                    "depth = EXCLUDED.depth, pieces = EXCLUDED.pieces", put)
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
                        "INSERT INTO drawings (project, name, bytes, size, sha256, sent_sha256, words, uploaded_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (project, name) DO UPDATE SET "
                        "incoming = false, bytes = EXCLUDED.bytes, size = EXCLUDED.size, sha256 = EXCLUDED.sha256, "
                        "sent_sha256 = EXCLUDED.sent_sha256, words = EXCLUDED.words, "
                        "uploaded_by = EXCLUDED.uploaded_by, uploaded_at = now()",
                        (code, name, data, len(data), hashlib.sha256(data).hexdigest(), ch.sent.get(name),
                         ch.words.get(name), uid))
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

    # ---- many people on a floor, one a room ------------------------------------------

    def _holder(self, conn, who_id: str | None) -> dict:
        """Who holds a lock, as the pages are told: {id, username, name}; this computer."""
        if who_id is None:
            return {"id": "local", "username": "local", "name": "This computer"}
        row = conn.execute("SELECT username, name FROM users WHERE id = %s", (who_id,)).fetchone()
        return {"id": who_id, "username": row[0] if row else "?", "name": (row[1] or row[0]) if row else "Someone"}

    def _take_locks(self, conn, code: str, rooms: dict[str, set[str]], by, editor: Editor,
                    ws: Workspace) -> list[str]:
        """The locks of ``rooms`` (floor -> its rooms) taken, or kept, for the person
        ``by``, in the change's transaction (the project's row is held by it: lock takers
        come one at a time); Locked, naming who and the room (as ``ws`` names it), while
        another person holds one. The floors where a room became theirs."""
        who = _user_id(by)
        taken = []
        for f in sorted(rooms):
            want = sorted(rooms[f])
            rows = conn.execute(
                "SELECT room, who, since, last_seen < now() - make_interval(secs => %s) FROM room_locks "
                "WHERE floor = %s AND room = ANY(%s) ORDER BY room FOR UPDATE", (ROOM_IDLE_S, f, want)).fetchall()
            for room, holder, since, idle in rows:
                if not idle and holder != who:
                    raise self._locked(conn, ws, f, room, holder, since)
            mine = sorted(room for room, holder, _, idle in rows if not idle and holder == who)
            if mine:
                conn.execute("UPDATE room_locks SET session = %s, last_seen = now() WHERE floor = %s AND room = ANY(%s)",
                             (editor.session, f, mine))
            new = [r for r in want if r not in mine]
            if new:
                conn.execute("INSERT INTO room_locks (floor, room, project, who, session) "
                             "SELECT %s, r, %s, %s, %s FROM unnest(%s::text[]) AS r ON CONFLICT (floor, room) DO UPDATE "
                             "SET who = EXCLUDED.who, session = EXCLUDED.session, since = now(), last_seen = now()",
                             (f, code, who, editor.session, new))
                taken.append(f)
        return taken

    def _locked(self, conn, ws: Workspace, floor: str, room: str, who_id, since, *, whole: bool = False) -> Locked:
        """Another person holds a room: what the page is told (423)."""
        holder = {"floor": floor, "room": room, "room_name": room_name(ws, room), "who": self._holder(conn, who_id),
                  "since": since.isoformat()}
        then = f"(since {since:%H:%M} UTC)"
        if whole:
            return Locked(f"{holder['who']['name']} is editing {holder['room_name']} on this floor {then}: you can "
                          "read the floor again when nobody else is editing it", holder)
        return Locked(f"{holder['who']['name']} is editing {holder['room_name']} {then}: you can look; you can "
                      "change it when they are done", holder)

    def _hold_project(self, conn, code: str) -> None:
        if conn.execute("SELECT 1 FROM projects WHERE code = %s FOR UPDATE", (code,)).fetchone() is None:
            raise NotFound(f"no project {code}")

    def _notify_locks(self, conn, code: str, floors) -> None:
        if floors:
            conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, _dumps({"project": code, "locks": sorted(floors)})))

    def whole_floor(self, code: str, floor: str, by) -> None:
        """A step of a person's on a whole floor (reading it again) may go ahead: Locked,
        naming who and a room of theirs, while another person holds a room on it."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT room, who, since FROM room_locks WHERE floor = %s AND last_seen >= now() - "
                               "make_interval(secs => %s) AND who IS DISTINCT FROM %s::text ORDER BY since, room LIMIT 1",
                               (floor, ROOM_IDLE_S, _user_id(by))).fetchone()
            if row is not None:
                raise self._locked(conn, self.current(code), floor, *row, whole=True)

    def release_lock(self, code: str, floor: str, by) -> bool:
        """A person's rooms on a floor let go (they left it, or are done editing it):
        whether they held any."""
        with self.db.transaction() as conn:
            gone = conn.execute("DELETE FROM room_locks WHERE floor = %s AND project = %s AND who IS NOT DISTINCT "
                                "FROM %s::text RETURNING room", (floor, code, _user_id(by))).fetchall()
            if gone:
                self._notify_locks(conn, code, [floor])
        return bool(gone)

    def take_over(self, code: str, floor: str, by, editor: Editor, room: str | None = None) -> list[dict]:
        """A room another person holds taken by an admin (``room``), or every room others
        hold on the floor, recorded in the history: the people they were taken from
        ({id, username, name}), none when nobody else held them."""
        who = _user_id(by)
        with self.db.transaction() as conn:
            self._hold_project(conn, code)
            rows = conn.execute(
                "SELECT room, who FROM room_locks WHERE floor = %s AND last_seen >= now() - make_interval(secs => %s) "
                "AND who IS DISTINCT FROM %s::text AND (%s::text IS NULL OR room = %s) ORDER BY since, room FOR UPDATE",
                (floor, ROOM_IDLE_S, who, room, room)).fetchall()
            if not rows:
                return []
            conn.execute("UPDATE room_locks SET who = %s, session = %s, since = now(), last_seen = now() "
                         "WHERE floor = %s AND room = ANY(%s)", (who, editor.session, floor, [r for r, _ in rows]))
            was = []
            for holder in dict.fromkeys(w for _, w in rows):
                was.append(self._holder(conn, holder))
            ws = self.current(code)
            version = conn.execute("SELECT version FROM projects WHERE code = %s", (code,)).fetchone()[0]
            ch = Changes(part="floor", kind="take over", targets=[room or floor], floors={floor},
                         before={"lock": was[0], "people": was, "rooms": [r for r, _ in rows]},
                         after={"lock": self._holder(conn, who)},
                         more={"from_name": ", ".join(p["name"] for p in was),
                               "room_name": room_name(ws, room) if room else None})
            seq = self._history(conn, code, version, ch, by)
            conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, _dumps({
                "project": code, "floors": [floor], "seq": seq, "version": version, "locks": [floor]})))
        return was

    def locks(self, code: str, floors=None) -> dict[str, dict]:
        """Who is editing the floors of a project (of ``floors``, when given): floor ->
        {floor, editors: [{who: {id, username, name}, since, rooms: [IDs]}] (by when they
        started), rooms: {room: {who, since}}}; floors nobody edits left out."""
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT l.floor, l.room, l.who, u.username, u.name, l.since FROM room_locks l "
                "LEFT JOIN users u ON u.id = l.who WHERE l.project = %s AND l.last_seen >= now() - "
                "make_interval(secs => %s) AND (%s::text[] IS NULL OR l.floor = ANY(%s)) ORDER BY l.since, l.room",
                (code, ROOM_IDLE_S, None if floors is None else list(floors),
                 None if floors is None else list(floors))).fetchall()
        out: dict[str, dict] = {}
        for floor, room, who, username, name, since in rows:
            person = {"id": "local", "username": "local", "name": "This computer"} if who is None \
                else {"id": who, "username": username or "?", "name": name or username or "Someone"}
            f = out.setdefault(floor, {"floor": floor, "editors": [], "rooms": {}})
            editor = next((e for e in f["editors"] if e["who"]["id"] == person["id"]), None)
            if editor is None:
                editor = {"who": person, "since": since.isoformat(), "rooms": []}
                f["editors"].append(editor)
            editor["rooms"].append(room)
            f["rooms"][room] = {"who": person, "since": since.isoformat()}
        return out

    def sweep_locks(self) -> dict[str, list[str]]:
        """The rooms their person made no change to for ROOM_IDLE_S let go: project -> the
        floors of those (each project's pages told)."""
        with self.db.transaction() as conn:
            rows = conn.execute("DELETE FROM room_locks WHERE last_seen < now() - make_interval(secs => %s) "
                                "RETURNING project, floor", (ROOM_IDLE_S,)).fetchall()
            out: dict[str, list[str]] = {}
            for project, floor in rows:
                if floor not in out.setdefault(project, []):
                    out[project].append(floor)
            for project, floors in out.items():
                self._notify_locks(conn, project, floors)
        return out

    # ---- drawings -----------------------------------------------------------------

    def drawings(self, code: str, incoming: bool = False) -> list[dict]:
        """The project's drawings (name, size, sha256, sent_sha256, uploaded_at), by name;
        ``incoming``: those sent and waiting for a person instead."""
        with self.db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            return cur.execute("SELECT name, size, sha256, sent_sha256, uploaded_at, uploaded_by FROM drawings "
                               "WHERE project = %s AND incoming = %s ORDER BY name", (code, incoming)).fetchall()

    def same_drawing(self, code: str, sha256: str) -> dict | None:
        """The project's drawing a file of this sha256 is (name, uploaded_at): one kept from
        a file sent with these bytes (sent_sha256), or kept as these bytes; the first kept
        when there are several. None when it has none: another project's do not count."""
        with self.db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            return cur.execute("SELECT name, uploaded_at FROM drawings WHERE project = %s AND NOT incoming "
                               "AND (sent_sha256 = %s OR sha256 = %s) ORDER BY uploaded_at, name LIMIT 1",
                               (code, sha256, sha256)).fetchone()

    def drawing_names_had(self, code: str) -> set[str]:
        """Every name a drawing of the project has had: those kept, and those taken away
        (its history says), so that a new one is never given the name of one gone."""
        with self.db.connection() as conn:
            kept = {n for (n,) in conn.execute("SELECT name FROM drawings WHERE project = %s AND NOT incoming",
                                               (code,))}
            gone = {n for (n,) in conn.execute("SELECT DISTINCT unnest(targets) FROM history "
                                               "WHERE project = %s AND kind = 'drawing removed'", (code,))}
        return kept | gone

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

    def put_drawing(self, code: str, name: str, data: bytes, *, words: str | None = None, sent: str | None = None,
                    by=None) -> None:
        """A drawing kept in the project (in place of one of that name); ``sent``: the
        sha256 of the file it was kept from, as it was sent."""

        def fn(base):
            ch = Changes(part="project", kind="drawing", targets=[name], drawings={name: data},
                         more={"size": len(data)})
            if words is not None:
                ch.words[name] = words
            if sent is not None:
                ch.sent[name] = sent
            return ch, None

        self.change(code, fn, by=by)

    def remove_drawing(self, code: str, name: str, by=None, check: Callable[[Workspace], None] | None = None) -> None:
        """A drawing of the project taken away, its words with it. ``check``: given the
        project as it is in the change, raises when the drawing may not go (a floor reads
        from it): nothing is changed then."""
        if self.drawing(code, name) is None:
            raise NotFound(f"no drawing {name}")

        def fn(base):
            if check is not None:
                check(base)
            return Changes(part="project", kind="drawing removed", targets=[name], drawings={name: None}), None

        self.change(code, fn, by=by)

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
        says of its own types where they say nothing) added; each type's ``model`` as
        Studio has one (item_models). A copy of one's own."""
        from .. import catalogue as catalogues

        with self.db.connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'catalogue'").fetchone()
        version = row[0] if row else None
        kept = self._catalogue
        if kept is not None and version is not None and kept[0] == version:
            return kept[1].model_copy(deep=True)
        with self.db.connection() as conn:
            types = [t for (t,) in conn.execute("SELECT type FROM catalogue ORDER BY position")]
            # the catalogue format its types were saved in (catalogue.py CATALOGUE_VERSION): none, 1
            row = conn.execute("SELECT value FROM settings WHERE key = 'catalogue_format'").fetchone()
        if version is None and not types:
            self.save_catalogue(catalogues.default_catalogue())
            return self.catalogue()
        saved_as = row[0] if row and isinstance(row[0], int) else 1
        cat, filled = catalogues.read(json.dumps({"format_version": saved_as, "types": types}))
        cat.check()
        known = {t.code for t in cat.types}
        new = [t for t in catalogues.default_catalogue().types if t.code not in known]
        if new or filled:
            cat.types.extend(new)
            self.save_catalogue(cat)
            return self.catalogue()
        with self.db.connection() as conn:
            modelled = {c for (c,) in conn.execute("SELECT code FROM item_models")}
        for t in cat.types:
            t.model = catalogues.model_path(t.code) if t.code in modelled else None
        self._catalogue = (version, cat.model_copy(deep=True))
        return cat

    def save_catalogue(self, cat) -> None:
        """The catalogue replaced (its types' ``model`` is not kept: it follows from the
        models Studio has)."""
        from ..catalogue import CATALOGUE_VERSION

        cat.check()
        with self.db.transaction() as conn:
            self._catalogue_changed(conn)
            conn.execute("INSERT INTO settings (key, value) VALUES ('catalogue_format', %s) ON CONFLICT (key) DO "
                         "UPDATE SET value = EXCLUDED.value", (_j(CATALOGUE_VERSION),))
            conn.execute("DELETE FROM catalogue")
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO catalogue (code, position, type) VALUES (%s, %s, %s)",
                                [(t.code, i, _j(t.model_dump(mode="json", exclude={"model"})))
                                 for i, t in enumerate(cat.types)])

    def catalogue_version(self):
        """The catalogue's version: it moves on with every change to the item types or
        their models (_catalogue_changed). None before the catalogue was first read."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = 'catalogue'").fetchone()
        return row[0] if row else None

    def _catalogue_changed(self, conn) -> None:
        """The catalogue's version moved on (in ``conn``'s transaction): every Studio on the
        database reads it again."""
        conn.execute("INSERT INTO settings (key, value) VALUES ('catalogue', '1') ON CONFLICT (key) DO "
                     "UPDATE SET value = to_jsonb(settings.value::text::bigint + 1)")
        self._catalogue = None

    # ---- item types' 3D models (models.py) ---------------------------------------------

    def model_file(self, code: str) -> bytes | None:
        """The model a package carries for the type ``code``, or None."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT glb FROM item_models WHERE code = %s", (code,)).fetchone()
        return bytes(row[0]) if row else None

    def model_source(self, code: str) -> dict | None:
        """The file a type's model was made from, as it came, and how it was made from it:
        {name, source, turn, units (chosen, or None), up}."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT name, source, turn, units_chosen, up FROM item_models WHERE code = %s",
                               (code,)).fetchone()
        return {"name": row[0], "source": bytes(row[1]), "turn": row[2], "units": row[3], "up": row[4]} if row else None

    def models(self) -> dict[str, dict]:
        """What each type's model is, by its code (no bytes): name, triangles, size…"""
        with self.db.connection() as conn:
            rows = conn.execute("SELECT code, name, source_sha256, source_triangles, units, turn, sha256, triangles, "
                                "size, added_by, added_at, length(source), length(glb), units_from, units_chosen, up, "
                                "size_before FROM item_models").fetchall()
        return {r[0]: {"name": r[1], "source_sha256": r[2], "source_triangles": r[3], "units": r[4], "turn": r[5],
                       "sha256": r[6], "triangles": r[7], "size": r[8], "added_by": r[9], "added_at": r[10].isoformat(),
                       "source_bytes": r[11], "bytes": r[12], "units_from": r[13], "units_chosen": r[14], "up": r[15],
                       "size_before": r[16]}
                for r in rows}

    def save_model(self, code: str, name: str, source: bytes, model, turn: int, by=None, units: str | None = None,
                   up: str = "y", before=None) -> None:
        """A type's model (models.Model) kept, with the file it was made from and how
        (``turn``, the ``units`` chosen, ``up``); one it had replaced (``before``, the type's
        size before it had a model, kept from the first)."""
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO item_models (code, name, source, source_sha256, source_triangles, units, turn, glb, sha256, "
                "triangles, size, added_by, units_from, units_chosen, up, size_before) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (code) "
                "DO UPDATE SET name = EXCLUDED.name, source = EXCLUDED.source, source_sha256 = EXCLUDED.source_sha256, "
                "source_triangles = EXCLUDED.source_triangles, units = EXCLUDED.units, turn = EXCLUDED.turn, "
                "glb = EXCLUDED.glb, sha256 = EXCLUDED.sha256, triangles = EXCLUDED.triangles, size = EXCLUDED.size, "
                "added_by = EXCLUDED.added_by, added_at = now(), units_from = EXCLUDED.units_from, "
                "units_chosen = EXCLUDED.units_chosen, up = EXCLUDED.up, "
                "size_before = coalesce(item_models.size_before, EXCLUDED.size_before)",
                (code, name, source, hashlib.sha256(source).hexdigest(), model.source_triangles, model.units, turn,
                 model.glb, hashlib.sha256(model.glb).hexdigest(), model.triangles, _j(list(model.size)), _j(who_of(by)),
                 model.units_from, units, up, _j(list(before)) if before else None))
            self._catalogue_changed(conn)

    def drop_model(self, code: str) -> bool:
        """A type's model taken away (it is drawn by its shape again): whether it had one."""
        with self.db.transaction() as conn:
            gone = conn.execute("DELETE FROM item_models WHERE code = %s", (code,)).rowcount
            if gone:
                self._catalogue_changed(conn)
        return bool(gone)


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
                if old is None or not _same_floor(node, old[1]):  # (its place in the list alone: not it)
                    ch.floors.add(f"{ws.id}-{'-'.join(key[1:])}")
        elif old is None or old[0] != row:
            ch.nodes.append((key[0], key[1:], row))
    for key in old_nodes.keys() - new_nodes.keys():
        ch.nodes.append((key[0], key[1:], None))
        if key[0] == "floors":  # a floor taken away is a floor it touched (its pages are told)
            ch.floors.add(f"{base.id}-{'-'.join(key[1:])}")
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
    for k, f in ws.furnishings.items():
        if k not in base.furnishings or not _same(f, base.furnishings[k]):
            ch.furnishings[k] = f.model_copy(deep=True)
    for k in base.furnishings.keys() - ws.furnishings.keys():
        ch.furnishings[k] = None
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
    known = {f_id for *_, f_id in ws.iter_floors(layouts=True)}
    ch.floors &= known | {f_id for *_, f_id in base.iter_floors(layouts=True)}
    return ch


def _same_floor(a, b) -> bool:
    return a is b or _same(a, b)
