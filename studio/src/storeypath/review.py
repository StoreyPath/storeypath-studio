"""The review editor's operations on one project: a floor's spaces over its
drawing, and corrections to them.

A project is reviewed where it is kept (ProjectFile, StoredProject): in Studio's
database, each change one small transaction on exactly what it touches (one
correction, one item, one floor's drawn edits) with its history row (db/store.py); or
as one workspace file (``storeypath fix`` and the tests), saved whole at each change
and read again whenever it changes on disk. The web server is in server.py:

    GET  /api/projects/<code>/review              project, floors, space types
    GET  /api/projects/<code>/floors/<id>         a floor's spaces and doors, local meters
    GET  /api/projects/<code>/floors/<id>/drawing the floor's source drawing, as linework
    GET  /api/projects/<code>/floors/<id>/print   the drawing as printed: where it lies, its size
    GET  /api/projects/<code>/floors/<id>/print.png  the print (drawn once, kept until the drawing changes)
    POST /api/projects/<code>/floors/<id>/convert re-read the drawing (keeps IDs): a job
    POST /api/projects/<code>/objects/<id>        {"correction": {type, name, number}} or {"reset": true};
                                                  {"ignored": bool} deletes a space or an opening (or restores it);
                                                  {"floor_finish": code or null, "wall_finish": …} its finishes
    POST /api/projects/<code>/floors/<id>/finishes  {"ids": […] or "type": t, "floor_finish", "wall_finish"}: many
                                                  rooms' finishes at once, one change
    POST /api/projects/<code>/floors/<id>/edits   {"add": {"wall": [[x, y], [x, y]]}}, {"add": {"divider": …}},
                                                  {"add": {"opening": {"type", "span"}}} or
                                                  {"remove": {"kind", "shape": [[x, y], …], "at": [x, y]}}:
                                                  what a person draws, kept through every conversion; then
                                                  the floor is read again (a job)
    POST /api/projects/<code>/floors/<id>/sample/preview  {area, keep?, remove?}: what an area sample of the
                                                  floor would hold (areasample/): its pictures, what is taken out
    POST /api/projects/<code>/floors/<id>/sample  {area, note?, keep?, remove?}: the sample, <id>.spsample
    POST /api/projects/<code>/floors/<id>/release    one's rooms on the floor let go (Done editing)
    POST /api/projects/<code>/floors/<id>/take-over {room?}  an admin takes a room (or every room) others edit
    POST /api/projects/<code>/undo {floor?}       one's own latest change undone (step_back); …/redo

A change is refused (Busy, answered 409) while a job works on the project: the job
would save over it. A person's change takes the lock of each room it touches, and is
refused (Locked, 423) while another person holds one (db/store.py): many people edit a
floor at once, one a room. Each change is
recorded with what says it in words as it was then (history.py: label, what, was,
now…), and each person undoes and redoes their own (step_back).

Who may call these is checked before they are (server.Gate): the project's floors
are those the person may see; a floor, its drawing and its print need view on that
floor (the drawing and print are its plan's part of the sheet; a floor read from a
whole sheet other floors are read from too needs view on each of them); a change
(edits, items, corrections, reading again) needs edit on the floor, and an item
carried to another floor, edit on that one too. Each change says who made it
(``by``: the person, for its history).
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union

from .cad import DrawingError, meters_per_unit, read_drawing
from . import finishes, history
from .db.store import Changes, Editor, _same, _spaces, drawing_name, floor_of, room_at, who_of
from .errors import Busy, Conflict, NotFound
from .export import Units, _label_point, capacity_of, seating, walked_doors as _doors
from .extract import CURVE_TOLERANCE_M, _center, _flatten, _text_lines, _walk, modelspace_entities
from .geometry import leaves_of_width
from .ids import normalize_item_id
from .profile import Profile, load_profile, resolve_profile
from .stacks import NOT_LINKED, STACK_TYPES
from .types import LAND_TYPES, SpaceType
from .workspace import (DrawnOpening, FloorEdits, Furnishing, FurnishingPiece, Item, ObjectRecord, Override,
                        ResizedOpening, Workspace, utcnow)

__all__ = ["Busy", "NotFound", "Review", "ProjectFile", "StoredProject", "File"]

CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml",
                 ".json": "application/json", ".png": "image/png", ".woff2": "font/woff2",
                 ".storeypath": "application/zip"}
CORRECTABLE = ("type", "name", "number")
# what a correction keeps of an object besides its type, name and number
KEPT = {"hidden", "ignored", "capacity", "stack", "floor_finish", "wall_finish", "wall_finishes"}
FINISHED_AT_ONCE = 5000  # rooms whose finishes one change sets, at most
REMOVE_REACH_M = 0.5  # removing what was drawn takes the drawn wall or opening this near
DOOR_REACH_M = 2.0  # an item that serves a door (a card reader), placed this near one, is linked to it
VIEW_TOP = {"view_deg": 360.0, "range_m": 500.0}  # a camera's own view (its fields of these keys): at most
RESIZE_BANDS = {"width": (0.3, 8.0), "sill": (0.0, 3.0), "height": (0.3, 10.0)}  # metres
OPENING_TYPES = ("door", "window", "opening")  # what an opening off a layout may be said to be
MOVE_MAX_M = 30.0  # an opening moves along its wall this far at most at once
SWING_JAMB_M = 0.05  # a drawn door's leaf is hung this near one of its jambs…
SWING_LENGTH = (0.85, 1.15)  # …as long as the door is wide, give or take…
SWING_ACROSS = math.sin(math.radians(20))  # …and across its wall, give or take 20°
PRINT_PX_PER_M = 100  # a floor's print: a pixel a centimetre…
PRINT_MAX_PX = 10000  # …within this many pixels a side
PRINT_MARGIN_M = 3.0  # around the floor's spaces, when the floor has no region of its own
PRINT_VERSION = 2  # changing how prints are drawn draws them again (2: hatch outlines that do not close left out)
CACHE_DIR = ".storeypath-cache"  # beside the workspace file
_PRINTING = threading.Lock()  # one print drawn at a time: rendering goes through ezdxf's caches
EDIT_LISTS = ("walls", "dividers", "openings", "resized", "spaces")


class File(NamedTuple):
    """A file the server sends as it is."""

    data: bytes
    content_type: str


BUSY_MESSAGE = ("a job is working on this project (adding floors, converting or exporting): "
                "nothing was changed; make the change again when the job is done")
BUSY_WAIT_S = 5.0  # a change waits this long for a short one (a floor's read applied, a building placed) to end


# ---- where a project is kept ------------------------------------------------------

class ProjectFile:
    """A project as one workspace file: read again whenever it changed on disk (the
    command line may change it meanwhile), and saved whole at each change. Its drawings
    are where its floors say, beside it; its prints are kept in CACHE_DIR beside it."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._ws: Workspace | None = None
        self._stamp: tuple | None = None

    @property
    def name(self) -> str:
        return self.path.name

    def _on_disk(self) -> tuple:
        s = self.path.stat()  # every save is a new file (Workspace.save): its inode tells it apart
        return s.st_mtime_ns, s.st_ino, s.st_size

    def current(self) -> Workspace:
        with self._lock:
            stamp = self._on_disk()
            if self._ws is None or stamp != self._stamp:
                self._ws, self._stamp = Workspace.load(self.path), stamp
            return self._ws

    def change(self, fn, by=None, editor=None):
        with self._lock:
            base = self.current()
            changes, result = fn(base)
            if not changes.empty():
                ws = changes.apply(base)
                ws.save(self.path)
                self._ws, self._stamp = ws, self._on_disk()
            return result

    def item_taken(self, item_id: str) -> bool:
        """Whether an item outside the project has this ID: none it knows of (the file
        is the project alone)."""
        return False

    def _path(self, src) -> Path:
        path = self.path.parent / src.path
        if not path.exists():
            raise DrawingError(f"drawing not found: {path}")
        return path

    def drawing_key(self, src) -> tuple:
        """What tells this drawing apart from another (and from itself changed)."""
        path = self._path(src)
        return str(path), path.stat().st_mtime_ns

    @contextmanager
    def drawing_file(self, src):
        yield self._path(src)

    def profile(self, src) -> str:
        return resolve_profile(src.profile, self.path.parent)

    def prints(self) -> Path:
        return self.path.parent / CACHE_DIR / "prints"


class StoredProject:
    """A project in Studio's database (db/store.py): its changes are transactions on what
    they touch; its drawings are read from the database into a file for as long as one
    is needed (in ``work``); its prints are kept in ``cache`` (made again when gone)."""

    def __init__(self, store, code: str, cache: Path, work: Path | None = None):
        self.store, self.code, self.cache, self.work = store, code, Path(cache), work

    @property
    def name(self) -> str:
        return self.code

    def current(self) -> Workspace:
        return self.store.current(self.code)

    def now(self) -> tuple[int, Workspace]:
        """The project as it is now, with its version."""
        return self.store.now(self.code)

    def changed_since(self, since: int, until: int):
        """What the changes after version ``since`` up to ``until`` touched (store.Touched),
        or None when that is not known here."""
        return self.store.changed_since(self.code, since, until)

    def change(self, fn, by=None, editor=None):
        return self.store.change(self.code, fn, by=by, editor=editor)

    def item_taken(self, item_id: str) -> bool:
        """Whether an item of any project of this Studio has this ID (its database's
        items, by their key)."""
        return self.store.item_taken(item_id)

    def drawing_key(self, src) -> tuple:
        name = drawing_name(src.path)
        info = self.store.drawing(self.code, name)
        if info is None:
            raise DrawingError(f"drawing not found: {name}")
        return name, info["sha256"]

    @contextmanager
    def drawing_file(self, src):
        name = drawing_name(src.path)
        if self.work is not None:
            self.work.mkdir(parents=True, exist_ok=True)
        with self.store.files(self.code, [name], folder=self.work) as folder:
            path = folder / "drawings" / name
            if not path.exists():
                raise DrawingError(f"drawing not found: {name}")
            yield path

    def profile(self, src) -> str:
        return src.profile  # a built-in profile, or auto (a project's own YAML is not kept)

    def prints(self) -> Path:
        return self.cache / "prints" / self.code


def _divider(opening: ObjectRecord, areas: dict) -> list[list[list[float]]] | None:
    """Where Studio divided an open area (no wall drawn): the edge the two spaces
    it joins share, as lines of points."""
    if len(opening.connects) != 2 or not all(c in areas for c in opening.connects):
        return None
    a, b = (areas[c] for c in opening.connects)
    edge = a.boundary.intersection(b.buffer(0.02))
    lines = [g for g in getattr(edge, "geoms", [edge]) if isinstance(g, LineString) and g.length >= 0.05]
    return [[[round(x, 3), round(y, 3)] for x, y in g.coords] for g in lines] or None


FURNISHING_NAME_MAX = 80  # a furniture layout's name, in characters
PIECES_MAX = 500  # items in a furniture layout, or placed in one go


def _room_name(ws: Workspace, room: str) -> str | None:
    """A room as it is named now (its name and number as corrected, else as read): None
    when it has neither."""
    o, fix = ws.objects.get(room), ws.overrides.get(room)
    if o is None:
        return None
    name = fix.name if fix is not None and fix.name is not None else o.name
    number = fix.number if fix is not None and fix.number is not None else o.number
    return " ".join(x for x in (name, number) if x) or None


def _dump(model) -> dict | None:
    return None if model is None else model.model_dump(mode="json", exclude_none=True)


# ---- what history keeps to say a change in words (history.describe) ------------------

LINE_KEYS = ("label", "what", "was", "now", "changes", "to_name", "new_label", "list", "floor_to", "wall_to", "wall_one_to",
             "rooms", "count")


def _type_name(cat, code: str) -> str:
    t = cat.get(code)
    return t.name_en if t is not None else code


def _as_shown(r: ObjectRecord, o: Override | None) -> dict:
    """An object's type, name and number with the correction ``o`` (as Workspace.effective)."""
    t = o.type if o is not None and o.type else r.type
    return {"type": getattr(t, "value", t),
            "name": (o.name or None) if o is not None and o.name is not None else r.name,
            "number": (o.number or None) if o is not None and o.number is not None else r.number}


def _object_lines(r: ObjectRecord, had: Override | None, new: Override | None, kind: str) -> dict:
    """What a correction's row says of it: the object as it was shown (its label), what
    it is (space, zone, door…), and for a correction, what it was and became."""
    was = _as_shown(r, had)
    what = r.type if r.kind == "opening" and r.type in history.OPENINGS else \
        "opening" if r.kind == "opening" else r.kind
    out = {"label": " ".join(x for x in (was["name"], was["number"]) if x) or None, "what": what}
    if kind == "correct":
        out["was"], out["now"] = was, _as_shown(r, new)
    return {k: v for k, v in out.items() if v is not None}


def _finish_lines(body: dict, finish: dict) -> dict:
    """What a finish's row says of it: the finishes set (a code, or "" for its type's; one
    wall's, "" for as the rest)."""
    out = {}
    for key, line in (("floor_finish", "floor_to"), ("wall_finish", "wall_to")):
        if key in body:
            out[line] = finish.get(key) or ""
    if isinstance(body.get("wall_finish_at"), dict):
        out["wall_one_to"] = body["wall_finish_at"].get("finish") or ""
    return out


SAME_WALL_M = 0.05  # a wall finished on its own, given again this near: the same wall


def _one_wall(paints: list, at) -> list:
    """A room's walls finished one by one (Override.wall_finishes) with one more, given
    another finish, or put back as the rest (``at``: {"line": [[x, y], [x, y]], "finish":
    a WALL-… code, or null})."""
    from .workspace import WallPaint

    if not isinstance(at, dict) or "line" not in at:
        raise ValueError('wall_finish_at is {"line": [[x, y], [x, y]], "finish": a wall finish\'s code, or null}')
    code = finishes.check(at.get("finish"), "wall")
    try:
        line = WallPaint(line=at["line"], finish=code or "WALL-PAINT-WHITE").line
    except Exception:
        raise ValueError("wall_finish_at's line is two different points, [x, y] each") from None

    def same(w) -> bool:
        return all(math.dist(a, b) <= SAME_WALL_M for a, b in zip(w.line, line)) or \
            all(math.dist(a, b) <= SAME_WALL_M for a, b in zip(w.line, line[::-1]))

    kept = [w for w in paints if not same(w)]
    return kept + [WallPaint(line=line, finish=code)] if code else kept


def _drawn_what(name: str, shapes: list) -> str:
    """What was drawn, from the list of the floor's edits it is in."""
    if name == "openings":
        return next((x.get("type") for x in shapes if isinstance(x, dict) and x.get("type")), "opening")
    return {"walls": "wall", "dividers": "divider", "spaces": "space"}.get(name, "opening")


def _inverse(ws: Workspace, entry: dict, ch: Changes, refused) -> None:
    """Into ``ch``: what puts back what the history row ``entry`` changed, as it was before
    it; refused() raised when it is not as the row left it (changed since)."""
    part, target = entry["part"], (entry["targets"] or [None])[0]
    if part == "object" and "overrides" in (entry["after"] or {}):  # many rooms at once (finishes)
        afters, befores = entry["after"]["overrides"], (entry["before"] or {}).get("overrides") or {}
        ch.before, ch.after = {"overrides": {}}, {"overrides": {}}
        for target, want in afters.items():
            r = ws.objects.get(target)
            have = ws.overrides.get(target)
            if r is None or r.status != "active" or \
                    not _same(Override.model_validate(want) if want is not None else None, have):
                raise refused()
            back = befores.get(target)
            new = Override.model_validate(back) if back is not None else None
            ch.before["overrides"][target], ch.after["overrides"][target] = _dump(have), _dump(new)
            if not _same(have, new):
                ch.overrides[target] = new
    elif part == "object":
        r = ws.objects.get(target)
        if r is None or r.status != "active":
            raise refused()
        have = ws.overrides.get(target)
        want = (entry["after"] or {}).get("override")
        if not _same(Override.model_validate(want) if want is not None else None, have):
            raise refused()
        back = (entry["before"] or {}).get("override")
        new = Override.model_validate(back) if back is not None else None
        ch.before, ch.after = {"override": _dump(have)}, {"override": _dump(new)}
        if not _same(have, new):
            ch.overrides[target] = new
    elif part == "item" and "items" in (entry["after"] or {}):  # many at once (a set placed, a group moved)
        afters, befores = entry["after"]["items"], (entry["before"] or {}).get("items") or {}
        floors = {f for *_, f in ws.iter_floors()}
        ch.before, ch.after = {"items": {}}, {"items": {}}
        for target, want in afters.items():
            have = ws.items.get(target)
            if have is None or want is None or not _same(Item.model_validate(want), have):
                raise refused()
            back = befores.get(target)
            if back is None:  # it was added: retired, its ID never given again
                new = have.model_copy(deep=True)
                new.status, new.retired_at = "retired", utcnow()
            else:
                new = Item.model_validate(back)
            if new.floor_id not in floors:
                raise refused()
            ch.floors |= {f for f in (have.floor_id, new.floor_id) if f}
            ch.before["items"][target], ch.after["items"][target] = _dump(have), _dump(new)
            ch.items[target] = new
    elif part == "item":
        have = ws.items.get(target)
        want = (entry["after"] or {}).get("item")
        if have is None or want is None or not _same(Item.model_validate(want), have):
            raise refused()
        back = (entry["before"] or {}).get("item")
        if back is None:  # it was added: retired, its ID never given again
            new = have.model_copy(deep=True)
            new.status, new.retired_at = "retired", utcnow()
        else:
            new = Item.model_validate(back)
        if new.floor_id not in {f for *_, f in ws.iter_floors()}:
            raise refused()
        ch.floors = {f for f in (have.floor_id, new.floor_id) if f}
        ch.before, ch.after = {"item": _dump(have)}, {"item": _dump(new)}
        ch.items[target] = new
    elif part == "edit":
        try:
            f = ws.floor(target)
        except (KeyError, ValueError):
            raise refused() from None
        edits = f.edits.model_dump(mode="json")
        shapes = edits.get(entry.get("list"))
        if shapes is None:
            raise refused()
        for x in entry["after"] or []:
            if x not in shapes:
                raise refused()
            shapes.remove(x)
        shapes.extend(entry["before"] or [])
        ch.edits[target] = FloorEdits.model_validate(edits)
        ch.before, ch.after = entry["after"] or [], entry["before"] or []
        ch.more["list"] = entry["list"]
        if entry.get("opening") and entry.get("by"):  # a move: its record back with it (its ID kept)
            dx, dy = entry["by"]
            record = ws.objects.get(entry["opening"])
            if record is not None:
                ch.objects[record.id] = _shifted(record, -dx, -dy)
            ch.more.update(opening=entry["opening"], by=[-dx, -dy])
    else:
        raise Conflict(f"{history.describe(entry)}: not undone here")


class Review:
    """The editor's operations on one project: ``source`` is where it is kept (a
    ProjectFile or a StoredProject; a path is a workspace file)."""

    def __init__(self, source, catalogue=None, changing=None):
        if isinstance(source, (str, Path)):
            source = ProjectFile(source)
        self.source = source
        self.path = getattr(source, "path", None)  # a workspace file's
        self._catalogue = catalogue  # () -> the Studio's catalogue of item types (catalogue.py)
        # () -> the lock a job holds while it changes the project (server.py): a change
        # made here takes it, and is refused while a job has it
        self._changing = changing
        self._drawings: dict[tuple, dict] = {}
        # what each object's shape gives (its area, where its label goes…), worked out
        # once a shape: object ID -> (the geometry it is of, what it gives)
        self._shapes: dict[str, tuple[dict, dict]] = {}
        self._dividers: dict[str, tuple[tuple, list | None]] = {}

    def workspace(self) -> Workspace:
        """The project as it is now: to look things up in (who may do what, server.Gate),
        never to change."""
        return self.source.current()

    def _change(self, fn, by=None, editor: Editor | None = None):
        """A change (``fn``: the project as it is -> (Changes, answer)), made only while no
        job is changing the project (else Busy: a job saves the copy it loaded, which
        would lose the change). One that fails part way changes nothing. ``editor``: a
        person's change from a page, which takes the lock of each room it touches
        (Locked while another person holds one)."""
        lock = self._changing() if self._changing else None
        if lock is not None and not lock.acquire(timeout=BUSY_WAIT_S):
            raise Busy(BUSY_MESSAGE)
        try:
            return self.source.change(fn, by=by, editor=editor)
        finally:
            if lock is not None:
                lock.release()

    def _floor(self, ws: Workspace, floor_id: str):
        try:
            return ws.floor(floor_id)
        except (KeyError, ValueError):
            raise NotFound(f"no floor {floor_id}") from None

    def project(self) -> dict:
        ws = self.workspace()
        floors = []
        for loc, b, f, fid in ws.iter_floors(layouts=True):
            spaces = [r for r in ws.floor_objects(fid) if r.kind in ("space", "zone") and not r.zones]
            floors.append({
                "id": fid, "name": f.name, "ordinal": f.ordinal, "elevation": f.elevation,
                "type": f.type.value,  # (a mezzanine stands in its building's floor of that ordinal)
                # a location's layout (0.12): its land, named by its location
                "building": loc.name if f.layout else b.name, "building_id": fid.rsplit("-", 1)[0],
                "location": loc.name, "layout": f.layout,
                "source": Path(f.source.path).name if f.source else None,
                "converted": f.converted_at is not None,
                "spaces": len(spaces), "review": sum(1 for r in spaces if ws.review_reasons(r)),
            })
        return {
            "project": {"id": ws.id, "name": ws.project.name},
            "file": self.source.name,
            "floors": floors,
            "types": [t.value for t in SpaceType],
            # what a layout's areas are (0.12): offered first there
            "land_types": [t.value for t in LAND_TYPES],
        }

    # ---- what a shape gives ------------------------------------------------------

    def _shape(self, r: ObjectRecord) -> dict:
        """What an object's shape gives, worked out once a shape (a record whose geometry
        is the same object as before gives what it gave)."""
        kept = self._shapes.get(r.id)
        if kept is not None and kept[0] is r.geometry:
            return kept[1]
        geom = shape(r.geometry)
        out = {"shape": geom, "empty": geom.is_empty}
        if r.kind != "opening" and not geom.is_empty:
            x, y = _label_point(geom)
            width, height = _room_at(geom, x, y)
            out.update(area=round(geom.area, 2), label_point=[round(x, 3), round(y, 3)],
                       label_room=[round(width, 2), round(height, 2)])
        self._shapes[r.id] = (r.geometry, out)
        return out

    def _divider_of(self, ws: Workspace, r: ObjectRecord) -> list | None:
        if r.type_source not in ("split", "doorway"):
            return None
        joined = [ws.objects.get(c) for c in r.connects]
        key = (id(r.geometry), tuple(r.connects), *(id(j.geometry) if j is not None else None for j in joined))
        kept = self._dividers.get(r.id)
        if kept is not None and kept[0] == key and kept[2] is r.geometry:
            return kept[1]
        areas = {j.id: self._shape(j)["shape"] for j in joined if j is not None and j.kind == "space"}
        out = _divider(r, areas)
        self._dividers[r.id] = (key, out, r.geometry)
        return out

    def _seats(self, ws: Workspace, cat, floor_id: str, objects=None) -> dict:
        """What the items on a floor say of its spaces (export.seating), from the shapes
        worked out once."""
        if not any((t := cat.get(it.type)) is not None and (t.workplaces or t.grade)
                   for it in ws.floor_items(floor_id)):
            return {}
        objects = ws.floor_objects(floor_id) if objects is None else objects
        units = Units((self._shape(r)["shape"], r) for r in objects
                      if r.kind in ("space", "zone") and r.geometry and not ws.effective(r)["ignored"])
        return seating(ws, cat, floor_id, units=units)

    def floor(self, floor_id: str, since: int | None = None) -> dict:
        """The floor as Review shows it, and the project's ``version`` it is of. With
        ``since`` (a version the page has the floor as of), only what changed after it,
        where that is known here (``"since"`` then says so; else the whole floor): the
        spaces, zones, openings and items changed, the IDs of those gone from the floor,
        what the items say of each space when one of them changed (``seats``), and the
        floor's own fields with all its openings when it changed itself."""
        version, ws = self.source.now() if hasattr(self.source, "now") else (None, self.workspace())
        f = self._floor(ws, floor_id)
        cat = self.catalogue()
        touched = self.source.changed_since(since, version) \
            if since is not None and version is not None and hasattr(self.source, "changed_since") else None
        if touched is not None:
            return self._floor_since(ws, f, floor_id, cat, version, since, touched)
        objects = sorted(ws.floor_objects(floor_id), key=lambda r: r.id)
        seats = self._seats(ws, cat, floor_id, objects)
        return {
            "id": floor_id, "version": version, **self._floor_fields(f),
            # spaces and their zones; a space divided into zones is used through them
            # a shape with nothing in it (a sliver read by an older Studio) is not shown
            "spaces": [self._space(ws, r, seats, cat) for r in objects
                       if r.kind in ("space", "zone") and not self._shape(r)["empty"]],
            "doors": [self._door(ws, r, f.edits.resized) for r in objects if r.kind == "opening"],
            # a layout's (0.12): the buildings standing on it, drawn under what is placed
            **({"footprints": self._footprints(ws, objects)} if f.layout else {}),
            # furniture and equipment on the floor, retired ones too (to be restored), in
            # the order they were placed (the last on top: their IDs are in no order)
            "items": [self._item(i, cat) for i in ws.floor_items(floor_id, include_retired=True)],
        }

    def _footprints(self, ws: Workspace, objects) -> list[dict]:
        """A layout's footprints (0.12), each with the building it is, and that one's name."""
        from .export import footprint_of

        out = []
        for r in objects:
            if r.kind != "footprint" or r.status != "active":
                continue
            b_id = footprint_of(ws, r)
            try:
                name = ws.building(b_id).name if b_id else None
            except (KeyError, ValueError):
                b_id, name = None, None
            out.append({"id": r.id, "name": r.name, "label": r.label, "geometry": r.geometry, "building": b_id,
                        "building_name": name, "match": r.match})
        return out

    def _floor_fields(self, f) -> dict:
        """A floor's own fields, as Review shows them."""
        return {
            "name": f.name, "ordinal": f.ordinal,
            "source": Path(f.source.path).name if f.source else None,
            "converted_at": f.converted_at.isoformat() if f.converted_at else None,
            "method": f.method, "warnings": f.warnings, "outline": f.outline,
            # for drawing walls and doors onto: the walls as found, and what was drawn
            "walls": f.walls, "wall_thickness": f.wall_thickness, "edits": f.edits.model_dump(),
            # a location's layout (0.12): its plot is its outline, its walls stand this high
            "layout": f.layout, "height": f.height,
            "north": f.north.model_dump(mode="json") if f.north is not None else None,
        }

    def _floor_since(self, ws: Workspace, f, floor_id: str, cat, version: int, since: int, touched) -> dict:
        """What changed on a floor after version ``since`` (floor)."""
        objects = {r.id: r for r in ws.floor_objects(floor_id)}
        mine = sorted(i for i in touched.ids if i.startswith(floor_id + "-"))
        itself = floor_id in touched.floors
        items = sorted(i for i in touched.ids if i in ws.items)
        seats = self._seats(ws, cat, floor_id, list(objects.values())) if items or itself else None
        spaces, doors, gone = [], [], [i for i in mine if i not in objects]
        for i in mine:
            r = objects.get(i)
            if r is None:
                continue
            if r.kind in ("space", "zone"):
                if self._shape(r)["empty"]:
                    gone.append(i)
                else:
                    spaces.append(self._space(ws, r, seats, cat))
            elif r.kind == "opening" and not itself:
                doors.append(self._door(ws, r, f.edits.resized))
        if itself:  # (an opening's size given in review is the floor's)
            doors = [self._door(ws, r, f.edits.resized) for r in sorted(objects.values(), key=lambda r: r.id)
                     if r.kind == "opening"]
        out = {"id": floor_id, "version": version, "since": since, "spaces": spaces, "doors": doors,
               "items": [self._item(ws.items[i], cat) for i in items if ws.items[i].floor_id == floor_id],
               # (and items moved to another floor, or taken away)
               "gone": gone + [i for i in items if ws.items[i].floor_id != floor_id] +
                       sorted(i for i in touched.ids if i not in ws.items and i not in ws.objects)}
        if seats is not None:  # how many each space seats, as its items now say
            out["seats"] = {}
            for r in objects.values():
                if r.kind in ("space", "zone"):
                    capacity, capacity_from = capacity_of(ws.effective(r), seats.get(r.id))
                    out["seats"][r.id] = {"capacity": capacity, "capacity_from": capacity_from,
                                          "workplaces": (seats.get(r.id) or {}).get("workplaces", 0),
                                          "grade": (seats.get(r.id) or {}).get("grade")}
        if itself:
            out.update(self._floor_fields(f))
        if f.layout and (itself or any(objects[i].kind == "footprint" for i in mine if i in objects)):
            out["footprints"] = self._footprints(ws, objects.values())
        return out

    # ---- items: furniture and equipment ---------------------------------------------

    def catalogue(self):
        from .catalogue import default_catalogue

        return self._catalogue() if self._catalogue else default_catalogue()

    def _item(self, it, cat=None) -> dict:
        t = (cat or self.catalogue()).get(it.type)
        return {"id": it.id, "type": it.type, "floor_id": it.floor_id, "x": it.x, "y": it.y, "rotation": it.rotation,
                "values": it.values, "retired": it.status == "retired", "group": it.group, "door_id": it.door_id,
                "name_en": t.name_en if t else it.type, "name_ar": t.name_ar if t else "",
                "category": t.category if t else "furniture", "color": t.color if t else "#8a8a8a",
                "width": t.width if t else 1.0, "depth": t.depth if t else 0.6, "mount": t.mount if t else "floor"}

    def _item_values(self, type_code: str, values, cat=None) -> dict:
        """An item's details from a request: only its type's StoreyPath fields, each of
        its kind (the managing system's fields are entered there, not here)."""
        t = (cat or self.catalogue()).get(type_code)
        if t is None or t.retired:
            raise ValueError(f"no item type {type_code} in the catalogue")
        if values is None:
            return {}
        if not isinstance(values, dict):
            raise ValueError("values: an object of field → value")
        fields = {f.key: f for f in t.fields}
        out = {}
        for key, value in values.items():
            f = fields.get(key)
            if f is None:
                raise ValueError(f"{t.code} has no field {key}")
            if f.owner != "storeypath":
                raise ValueError(f"{t.code}'s {key} is entered in the system that manages the asset, not here")
            if value is None or value == "":
                continue  # cleared
            if f.kind == "number":
                value = _finite(value, key)
                if key in VIEW_TOP and not 0 < value <= VIEW_TOP[key]:
                    raise ValueError(f"{key} is more than 0 and at most {VIEW_TOP[key]:g}")
            elif f.kind == "choice" and value not in f.choices:
                raise ValueError(f"{key} is one of {', '.join(f.choices)}")
            elif f.kind == "color" and not (isinstance(value, str) and len(value) == 7 and value.startswith("#")):
                raise ValueError(f"{key} is a colour, #rrggbb")
            elif f.kind == "text":
                value = str(value).strip()[:200]
            out[key] = value
        return out

    def add_item(self, floor_id: str, body: dict, by=None, editor: Editor | None = None) -> dict:
        """An item placed on a floor: ``{type, x, y, rotation?, values?, door_id?}`` (local
        metres), with a new ID: drawn again while an item of the project, or of any project
        of this Studio, has it. One of a type that serves a door is linked to ``door_id``
        (null: none), else to the nearest door within DOOR_REACH_M."""
        cat = self.catalogue()

        def fn(ws: Workspace):
            self._floor(ws, floor_id)
            values = self._item_values(body.get("type"), body.get("values"), cat)
            x, y = _number(body, "x"), _number(body, "y")
            t = cat.get(body["type"])
            door = _door_of(ws, floor_id, t, body["door_id"]) if "door_id" in body \
                else _nearest_door(ws, floor_id, (x, y)) if t.serves_door else None
            it = Item(id=ws.new_item_id(self.source.item_taken), type=body["type"], floor_id=floor_id, x=x, y=y,
                      rotation=_number(body, "rotation", 0.0) % 360, values=values, door_id=door)
            ch = Changes(part="item", kind="add", targets=[it.id], floors={floor_id},
                         before={"item": None}, after={"item": _dump(it)},
                         more={"label": _type_name(cat, it.type), "changes": ["add"]})
            ch.items[it.id] = it
            return ch, it

        return self._item(self._change(fn, by, editor), cat)

    def _new_group(self, ws: Workspace) -> str:
        """A key for a new group of items: G and eight random symbols (Crockford's base32,
        never an asset's tag), no group of the project's items has."""
        import secrets

        used = {it.group for it in ws.items.values() if it.group}
        while True:
            key = "G" + "".join(secrets.choice("0123456789ABCDEFGHJKMNPQRSTVWXYZ") for _ in range(8))
            if key not in used:
                return key

    def place_set(self, floor_id: str, body: dict, by=None, editor: Editor | None = None) -> dict:
        """A set placed on a floor (catalogue.py sets_of): ``{set, x, y, rotation?}``, where
        its middle goes and its turn (local metres, degrees). Each piece an item of its own,
        with a new ID, where the set puts it; the pieces one new group. One change: undone,
        all of them are taken away."""
        from .catalogue import sets_of

        cat = self.catalogue()
        s = next((x for x in sets_of(cat) if x.code == body.get("set")), None)
        if s is None:
            raise ValueError(f"no set {body.get('set')} in the catalogue")
        x0, y0, turn = _number(body, "x"), _number(body, "y"), _number(body, "rotation", 0.0) % 360
        c, sn = math.cos(math.radians(turn)), math.sin(math.radians(turn))
        taken = getattr(self.source, "item_taken", None)

        def fn(ws: Workspace):
            self._floor(ws, floor_id)
            group = self._new_group(ws)
            ch = Changes(part="item", kind="add", targets=[], floors={floor_id}, before={"items": {}}, after={"items": {}},
                         more={"label": s.name_en, "changes": ["add"], "count": len(s.pieces)})
            made = []
            for piece in s.pieces:
                it = Item(id=ws.new_item_id(lambda i: i in ch.items or (taken is not None and taken(i))), type=piece.type,
                          floor_id=floor_id, x=round(x0 + piece.x * c - piece.y * sn, 4),
                          y=round(y0 + piece.x * sn + piece.y * c, 4), rotation=round((piece.rotation + turn) % 360, 4),
                          group=group)
                ch.items[it.id] = it
                ch.targets.append(it.id)
                ch.before["items"][it.id], ch.after["items"][it.id] = None, _dump(it)
                made.append(it)
            return ch, made

        made = self._change(fn, by, editor)
        return {"items": [self._item(it, cat) for it in made], "group": made[0].group}

    def change_items(self, body: dict, by=None, editor: Editor | None = None) -> dict:
        """Several items changed as one change, undone as one (a group moved, turned or
        taken away; items grouped, or let go of their group): ``{items: [{id, x?, y?,
        rotation?, retired?}], group?}``, each moved, turned, taken away or brought back as
        given; with ``group``, all of them one new group ("new", two or more) or none
        (null). All that is asked, or (when any of it cannot be) nothing."""
        cat = self.catalogue()
        asked = body.get("items")
        if not isinstance(asked, list) or not asked or not all(isinstance(a, dict) and isinstance(a.get("id"), str)
                                                                 for a in asked):
            raise ValueError("items: a list of {id, …}, one or more")
        if len({a["id"] for a in asked}) != len(asked):
            raise ValueError("an item is given twice")
        regroup = "group" in body
        if regroup and body["group"] not in ("new", None):
            raise ValueError('group is "new" (one new group) or null (none)')
        if regroup and body["group"] == "new" and len(asked) < 2:
            raise ValueError("a group is of two items or more")

        def fn(ws: Workspace):
            group = self._new_group(ws) if regroup and body["group"] == "new" else None
            ch = Changes(part="item", kind="change", targets=[a["id"] for a in asked], before={"items": {}},
                         after={"items": {}})
            kinds, out = [], []
            for a in asked:
                it = ws.items.get(a["id"])
                if it is None:
                    raise NotFound(f"no item {a['id']}")
                new = it.model_copy(deep=True)
                if "retired" in a:
                    if not isinstance(a["retired"], bool):
                        raise ValueError("retired is true or false")
                    new.status = "retired" if a["retired"] else "active"
                    new.retired_at = utcnow() if a["retired"] else None
                    kinds.append("delete" if a["retired"] else "restore")
                for key in ("x", "y"):
                    if key in a:
                        setattr(new, key, _number(a, key))
                if "x" in a or "y" in a:
                    kinds.append("move")
                if "rotation" in a:
                    new.rotation = _number(a, "rotation") % 360
                    kinds.append("turn")
                if regroup:
                    new.group = group
                    kinds.append("group" if group else "ungroup")
                ch.before["items"][it.id], ch.after["items"][it.id] = _dump(it), _dump(new)
                ch.floors |= {f for f in (it.floor_id, new.floor_id) if f}
                if not _same(new, it):
                    ch.items[it.id] = new
                out.append(new)
            kinds = list(dict.fromkeys(kinds))  # (each once, in order)
            ch.kind = kinds[0] if kinds else "change"
            first = ws.items[asked[0]["id"]]
            ch.more = {"label": _type_name(cat, first.type) if len(asked) == 1 else f"{len(asked)} items", "changes": kinds,
                       "count": len(asked)}
            return ch, out

        return {"items": [self._item(it, cat) for it in self._change(fn, by, editor)]}

    # ---- furniture layouts (workspace.Furnishing), items placed together ---------------

    def furnishings(self) -> list[dict]:
        """The project's furniture layouts, oldest first: each with its pieces, where they
        stand from the corner of the room's box they were taken from."""
        cat = self.catalogue()
        return [self._furnishing(f, cat) for f in sorted(self.workspace().furnishings.values(),
                                                          key=lambda f: (f.created_at, f.id))]

    def _furnishing(self, f: Furnishing, cat=None) -> dict:
        cat = cat or self.catalogue()
        return {"id": f.id, "name": f.name, "floor_id": f.floor_id, "room": f.room, "width": f.width,
                "depth": f.depth, "pieces": [p.model_dump(mode="json") for p in f.pieces], "count": len(f.pieces),
                "missing": sorted({p.type for p in f.pieces if cat.get(p.type) is None or cat.get(p.type).retired}),
                "created_by": f.created_by, "created_at": f.created_at.isoformat()}

    def _new_furnishing_id(self, ws: Workspace) -> str:
        """An ID for a new furniture layout: L and eight random symbols (Crockford's base32)."""
        import secrets

        while True:
            key = "L" + "".join(secrets.choice("0123456789ABCDEFGHJKMNPQRSTVWXYZ") for _ in range(8))
            if key not in ws.furnishings:
                return key

    def save_furnishing(self, floor_id: str, body: dict, by=None, editor: Editor | None = None) -> dict:
        """A furniture layout made of a room's items: ``{room, name}`` (a room of the floor;
        a name of 1 to 80 characters no other layout of the project has, either case). Its
        pieces are the items standing in the room (as an item's room is found: db/store.py
        room_at), not taken away, each where it stands from the corner of the room's box
        (its least x and y) and turned as it is; their IDs and details are not kept. One
        change, in the project's history (a layout is deleted, not undone)."""
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name: the furniture layout's name")
        name = " ".join(name.split())
        if len(name) > FURNISHING_NAME_MAX:
            raise ValueError(f"name: {FURNISHING_NAME_MAX} characters at most")
        room = body.get("room")
        if not isinstance(room, str) or not room:
            raise ValueError("room: the ID of the room it is taken from")
        cat = self.catalogue()
        who = who_of(by)
        made_by = (who.get("name") or who.get("username")) if isinstance(who, dict) and not who.get("local") else None

        def fn(ws: Workspace):
            self._floor(ws, floor_id)
            shape_ = next((g for sid, g in _spaces(ws, floor_id) if sid == room), None)
            if shape_ is None:
                raise NotFound(f"no room {room} on this floor")
            if any(f.name.casefold() == name.casefold() for f in ws.furnishings.values()):
                raise Conflict(f"a furniture layout is named {name} already: choose another name")
            mine = [it for it in ws.items.values() if it.floor_id == floor_id and it.status == "active"
                    and room_at(ws, floor_id, (it.x, it.y)) == room]
            if not mine:
                raise ValueError("there is no furniture in this room to make a layout of")
            if len(mine) > PIECES_MAX:
                raise ValueError(f"a furniture layout holds {PIECES_MAX} items at most; this room has {len(mine)}")
            x0, y0, x1, y1 = shape_.bounds
            mine.sort(key=lambda it: (round(it.y, 3), round(it.x, 3), it.id))
            f = Furnishing(id=self._new_furnishing_id(ws), name=name, floor_id=floor_id, room=_room_name(ws, room),
                           width=round(x1 - x0, 4), depth=round(y1 - y0, 4), created_by=made_by,
                           pieces=[FurnishingPiece(type=it.type, x=round(it.x - x0, 4), y=round(it.y - y0, 4),
                                                   rotation=round(it.rotation % 360, 4)) for it in mine])
            ch = Changes(part="project", kind="furniture layout", targets=[f.id], before={"furnishing": None},
                         after={"furnishing": _dump(f)}, more={"label": name, "count": len(f.pieces), "changes": ["save"]})
            ch.furnishings[f.id] = f
            return ch, f

        return self._furnishing(self._change(fn, by, editor), cat)

    def delete_furnishing(self, furnishing_id: str, by=None, editor: Editor | None = None) -> dict:
        """A furniture layout taken away; what was placed of it stays as it is."""

        def fn(ws: Workspace):
            f = ws.furnishings.get(furnishing_id)
            if f is None:
                raise NotFound(f"no furniture layout {furnishing_id}")
            ch = Changes(part="project", kind="furniture layout", targets=[f.id], before={"furnishing": _dump(f)},
                         after={"furnishing": None}, more={"label": f.name, "count": len(f.pieces), "changes": ["delete"]})
            ch.furnishings[f.id] = None
            return ch, f

        gone = self._change(fn, by, editor)
        return {"id": gone.id, "deleted": True}

    def place_items(self, floor_id: str, body: dict, by=None, editor: Editor | None = None) -> dict:
        """Items placed on a floor in one go (a furniture layout placed in a room, or items
        pasted): ``{items: [{type, x, y, rotation?}], group?, label?}``, each where it is
        given (local metres, degrees), with a new ID; one of a type that serves a door
        linked to the nearest door, as one placed alone. With ``group`` (true when not
        given and there are two or more), all of them one new group. ``label`` names them
        in history (a layout's name). One change: undone, all of them are taken away."""
        asked = body.get("items")
        if not isinstance(asked, list) or not asked or not all(isinstance(a, dict) for a in asked):
            raise ValueError("items: a list of {type, x, y, rotation?}, one or more")
        if len(asked) > PIECES_MAX:
            raise ValueError(f"items: {PIECES_MAX} at most at once")
        group = body.get("group", len(asked) > 1)
        if not isinstance(group, bool):
            raise ValueError("group is true or false")
        label = body.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > 120):
            raise ValueError("label: a name of 1 to 120 characters")
        cat = self.catalogue()
        wanted = []
        for a in asked:
            self._item_values(a.get("type"), None, cat)  # (a type of the catalogue, not retired)
            wanted.append((a["type"], _number(a, "x"), _number(a, "y"), _number(a, "rotation", 0.0) % 360))
        taken = getattr(self.source, "item_taken", None)

        def fn(ws: Workspace):
            self._floor(ws, floor_id)
            key = self._new_group(ws) if group and len(wanted) > 1 else None
            first = cat.get(wanted[0][0])
            ch = Changes(part="item", kind="add", targets=[], floors={floor_id}, before={"items": {}}, after={"items": {}},
                         more={"label": " ".join(label.split()) if label else
                               first.name_en if len(wanted) == 1 else f"{len(wanted)} items",
                               "changes": ["add"], "count": len(wanted)})
            made = []
            for code, x, y, rot in wanted:
                t = cat.get(code)
                it = Item(id=ws.new_item_id(lambda i: i in ch.items or (taken is not None and taken(i))), type=code,
                          floor_id=floor_id, x=round(x, 4), y=round(y, 4), rotation=round(rot, 4), group=key,
                          door_id=_nearest_door(ws, floor_id, (x, y)) if t.serves_door else None)
                ch.items[it.id] = it
                ch.targets.append(it.id)
                ch.before["items"][it.id], ch.after["items"][it.id] = None, _dump(it)
                made.append(it)
            return ch, made

        made = self._change(fn, by, editor)
        return {"items": [self._item(it, cat) for it in made], "group": made[0].group}

    def find_item(self, text: str) -> dict:
        """An item by its ID as a person typed it (7k2q xm9f 4dp: either case, O for 0, I
        and L for 1, with or without its hyphens): what it is, and where (its floor)."""
        it = self.workspace().items.get(normalize_item_id(text) or text)
        if it is None:
            raise NotFound(f"no item {text}")
        return self._item(it)

    def change_item(self, item_id: str, body: dict, by=None, editor: Editor | None = None) -> dict:
        """An item moved, turned, given another type or details, linked to the door it
        serves (``door_id``; null: none), carried to another floor (``floor_id``: unlinked
        from its door), taken away (``{"retired": true}``) or brought back: all that is
        asked, or (when any of it cannot be) nothing."""
        cat = self.catalogue()

        def fn(ws: Workspace):
            it = ws.items.get(item_id)
            if it is None:
                raise NotFound(f"no item {item_id}")
            new = it.model_copy(deep=True)  # changed whole, then put in its place
            kinds = []
            if "retired" in body:
                if not isinstance(body["retired"], bool):
                    raise ValueError("retired is true or false")
                new.status = "retired" if body["retired"] else "active"
                new.retired_at = utcnow() if body["retired"] else None
                kinds.append("delete" if body["retired"] else "restore")
            if "floor_id" in body:
                if not isinstance(body["floor_id"], str):
                    raise NotFound(f"no floor {body['floor_id']}")
                self._floor(ws, body["floor_id"])
                if body["floor_id"] != new.floor_id:
                    kinds.append("carry")
                    new.door_id = None  # (its door is the other floor's)
                new.floor_id = body["floor_id"]
            if "type" in body:
                self._item_values(body["type"], None, cat)  # a type of the catalogue
                new.type = body["type"]
                own = {f.key for f in cat.get(new.type).fields if f.owner == "storeypath"}
                new.values = {k: v for k, v in new.values.items() if k in own}  # what the new type has
                if not cat.get(new.type).serves_door:
                    new.door_id = None
                elif new.door_id is None:
                    new.door_id = _nearest_door(ws, new.floor_id, (new.x, new.y))
                kinds.append("retype")
            if "values" in body:
                new.values = self._item_values(new.type, body["values"], cat)
                kinds.append("values")
            if "door_id" in body:
                new.door_id = _door_of(ws, new.floor_id, cat.get(new.type), body["door_id"])
                kinds.append("door")
            for key in ("x", "y"):
                if key in body:
                    setattr(new, key, _number(body, key))
            if "x" in body or "y" in body:
                kinds.append("move")
            if "rotation" in body:
                new.rotation = _number(body, "rotation") % 360
                kinds.append("turn")
            more = {"label": _type_name(cat, it.type), "changes": kinds}
            if "carry" in kinds:
                more["to_name"] = self._floor(ws, new.floor_id).name
            if "retype" in kinds:
                more["new_label"] = _type_name(cat, new.type)
            ch = Changes(part="item", kind=kinds[0] if kinds else "change", targets=[item_id],
                         floors={f for f in (it.floor_id, new.floor_id) if f},
                         before={"item": _dump(it)}, after={"item": _dump(new)}, more=more)
            if not _same(new, it):
                ch.items[item_id] = new
            return ch, new

        return self._item(self._change(fn, by, editor), cat)

    def _door(self, ws: Workspace, r: ObjectRecord, resized=()) -> dict:
        eff = ws.effective(r)
        middle = _middle(r)
        return {"id": r.id, "type": r.type, "point": r.geometry["coordinates"], "connects": r.connects,
                "span": r.span, "width": r.width, "swings": r.swings, "tag": r.tag,
                "sill": r.sill, "height": r.height, "drawn": (r.type_source or "").startswith("drawn"),
                "middle": [round(middle.x, 4), round(middle.y, 4)],
                # the size given in review to one of the drawing's (null: as drawn)
                "resize": next((x.model_dump(exclude={"at"} | {k for k in ("type", "to", "swing") if getattr(x, k) is None})
                                for x in resized if Point(x.to or x.at).distance(middle) <= REMOVE_REACH_M), None),
                "ignored": eff["ignored"],
                "divider": self._divider_of(ws, r)}

    def edit(self, floor_id: str, body: dict, by=None, editor: Editor | None = None) -> None:
        """Add or remove what a person drew on a floor: a wall, a line dividing a space
        (no wall: its zones), a door, a window or an opening (local meters). Removing
        takes the one of its kind drawn as the page shows it (_remove)."""

        def fn(ws: Workspace):
            was = self._floor(ws, floor_id)
            f = was.model_copy(update={"edits": was.edits.model_copy(deep=True)})  # its edits changed alone
            add = body.get("add") if isinstance(body.get("add"), dict) else {}
            kind, what, moved = "draw", None, None
            if "wall" in add or "divider" in add:
                what = "wall" if "wall" in add else "divider"
                line = _points(add[what], 2)
                if LineString(line).length < 0.1:
                    raise ValueError(f"a {what} is longer than 10 cm")
                (f.edits.walls if what == "wall" else f.edits.dividers).append(line)
            elif isinstance(body.get("add"), dict) and "opening" in body["add"]:
                o = body["add"]["opening"]
                kinds = ("door", "window", "opening", "gate") if f.layout else ("door", "window", "opening")
                if not isinstance(o, dict) or o.get("type") not in kinds:
                    raise ValueError('an opening is {"type": "door", "window" or "opening" (on a layout, "gate"), '
                                     '"span": [[x, y], [x, y]]}')
                span = _points(o.get("span"), 2)
                widest = 12 if f.layout else 6  # (a gate for cars)
                if not 0.3 <= LineString(span).length <= widest:
                    raise ValueError(f"an opening is 0.3 to {widest} m wide")
                swing = _swing(o.get("swing"), span) if o["type"] == "door" else None
                f.edits.openings.append(DrawnOpening(type="door" if o["type"] == "gate" else o["type"], span=span,
                                                     swing=swing))
            elif isinstance(body.get("add"), dict) and "space" in body["add"]:
                f.edits.spaces.append(_ring(body["add"]["space"]))
            elif isinstance(body.get("resize"), dict) and "at" in body["resize"]:
                what = self._resize(ws, floor_id, f, body["resize"])
                kind = "resize"
            elif isinstance(body.get("move"), dict) and "at" in body["move"] and "to" in body["move"]:
                what, moved = self._move(ws, floor_id, f, body["move"])
                kind = "move"
            elif isinstance(body.get("remove"), dict) and ("at" in body["remove"] or "shape" in body["remove"]):
                _remove(f, body["remove"])
                kind = "erase"
            else:
                raise ValueError('send {"add": {"wall" or "divider": …}}, {"add": {"opening": …}}, '
                                 '{"add": {"space": [[x, y], …]}}, {"resize": {"at": [x, y], "width", "sill", '
                                 '"height"}} or {"remove": {"kind", "shape": its points as drawn, "at": [x, y]}}')
            ch = Changes(part="edit", kind=kind, targets=[floor_id], floors={floor_id})
            old, new = was.edits.model_dump(mode="json"), f.edits.model_dump(mode="json")
            for name in EDIT_LISTS:
                if old[name] != new[name]:
                    ch.more.setdefault("list", name)
                    ch.before = [x for x in old[name] if x not in new[name]]
                    ch.after = [x for x in new[name] if x not in old[name]]
                    ch.edits[floor_id] = f.edits
            if ch.edits:
                ch.more["what"] = what or _drawn_what(ch.more["list"], ch.before + ch.after)
            if kind == "move" and moved is not None:  # its record with it: where the next read finds it (its ID kept)
                record, by_xy = moved
                ch.objects[record.id] = record
                ch.more.update(opening=record.id, by=by_xy)
            return ch, None

        self._change(fn, by, editor)

    def _resize(self, ws: Workspace, floor_id: str, f, body: dict) -> str:
        """A door, window or opening given another width, sill or height (metres; null:
        as drawn), or another type (door, window or opening; null: as read): one the
        drawing reads wrong (a glazed door drawn like a window). What is left out of the
        request stays as it was. One drawn in review changes; one of the drawing keeps
        its new size and type in the floor's edits, found again by its middle at every
        conversion. Which it is now (door, window, opening)."""
        at = Point(_points([body["at"]], 1)[0])
        bands = {**RESIZE_BANDS, "width": (0.3, 12.0)} if f.layout else RESIZE_BANDS  # (a gate for cars, 0.12)
        sizes = {key: _size(body.get(key), key, band) for key, band in bands.items() if key in body}
        if "type" in body:
            if f.layout:
                raise ValueError("an opening on a layout is a gate")
            if body["type"] is not None and body["type"] not in OPENING_TYPES:
                raise ValueError(f"type: {', '.join(OPENING_TYPES)}, or null (as read)")
        drawn = [(LineString(o.span).distance(at), i) for i, o in enumerate(f.edits.openings)]
        near = [d for d in drawn if d[0] <= REMOVE_REACH_M]
        if near:
            o = f.edits.openings[min(near)[1]]
            if sizes.get("width") is not None:
                new = _resized_span(o.span, sizes["width"])
                if o.swing:  # its leaf with it, hung on the same jamb
                    o.swing = leaves_of_width([o.swing], o.span, new)[0]
                o.span = new
            o.sill, o.height = sizes.get("sill", o.sill), sizes.get("height", o.height)
            if body.get("type") is not None and body["type"] != o.type:
                o.type = body["type"]  # (its leaf kept: a door again hangs as it did)
            if "swing" in body:
                if o.type != "door":
                    raise ValueError("only a door swings")
                o.swing = _swing(body["swing"], o.span)
            return o.type
        openings = [(_middle(r).distance(at), r) for r in ws.floor_objects(floor_id) if r.kind == "opening"]
        found = [o for o in openings if o[0] <= REMOVE_REACH_M]
        if not found:
            raise NotFound("no door, window or opening there")
        nearest = min(found, key=lambda o: o[0])[1]
        middle = _middle(nearest)
        had = _given_at(f.edits.resized, middle)
        kept = had.model_dump(exclude={"at"}) if had else {"width": None, "sill": None, "height": None, "type": None, "to": None,
                                                         "swing": None}
        given = {**kept, **sizes, **({"type": body["type"]} if "type" in body else {})}
        if "swing" in body:
            if (given["type"] or nearest.type) != "door":
                raise ValueError("only a door swings")
            if not nearest.span:
                raise ValueError("a door with no width read cannot be hung")
            given["swing"] = _swing(body["swing"], nearest.span)
        read_at = had.at if had else [round(middle.x, 4), round(middle.y, 4)]  # (where the drawing has it)
        f.edits.resized = [x for x in f.edits.resized if x is not had]
        if any(v is not None for v in given.values()):
            f.edits.resized.append(ResizedOpening(at=read_at, **given))
        if given["type"] is not None:
            return given["type"]
        return nearest.type if nearest.type in history.OPENINGS else "opening"

    def _move(self, ws: Workspace, floor_id: str, f, body: dict):
        """A door, window or opening moved along its wall so that its middle is as near
        ``to`` as the wall lets: one drawn in review, its span (and leaf); one of the
        drawing keeps where it was put in the floor's edits, beside its size, found again
        by where the drawing has it. Not off its wall. Which it is, and its record moved
        with it ((record, [dx, dy]): its ID kept by the next read), or None."""
        at = Point(_points([body["at"]], 1)[0])
        to = _points([body["to"]], 1)[0]
        records = [(_middle(r).distance(at), r) for r in ws.floor_objects(floor_id) if r.kind == "opening"]
        near = [o for o in records if o[0] <= REMOVE_REACH_M]
        record = min(near, key=lambda o: o[0])[1] if near else None
        drawn = [(LineString(o.span).distance(at), i) for i, o in enumerate(f.edits.openings)]
        mine = [d for d in drawn if d[0] <= REMOVE_REACH_M]
        o = f.edits.openings[min(mine)[1]] if mine else None
        span = o.span if o is not None else record.span if record is not None and record.span else None
        if span is None:
            raise NotFound("no door, window or opening there")
        (x0, y0), (x1, y1) = span[0], span[-1]
        width = math.dist((x0, y0), (x1, y1)) or 1.0
        ux, uy = (x1 - x0) / width, (y1 - y0) / width
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        t = (to[0] - mx) * ux + (to[1] - my) * uy
        if abs(t) > MOVE_MAX_M:
            raise ValueError(f"an opening moves along its wall {MOVE_MAX_M:g} m at most at once")
        dx, dy = round(ux * t, 4), round(uy * t, 4)
        if abs(t) < 0.005:
            raise ValueError("it is there already")
        moved = [[x + dx, y + dy] for x, y in span]
        gaps = [r.span for r in ws.floor_objects(floor_id) if r.kind == "opening" and r.span] + [x.span for x in f.edits.openings]
        if not _on_walls(f, span, moved, gaps):
            raise ValueError("it would leave its wall: an opening moves along the wall it is in")
        shift = lambda pts: [[round(x + dx, 4), round(y + dy, 4)] for x, y in pts]  # noqa: E731
        if o is not None:
            o.span = shift(o.span)
            if o.swing:
                o.swing = shift(o.swing)
            kind = o.type
        else:
            mid = _middle(record)
            had = _given_at(f.edits.resized, mid)
            put = [round(mid.x + dx, 4), round(mid.y + dy, 4)]
            if had is not None and had.swing:
                had.swing = shift(had.swing)
            if had is None:
                f.edits.resized.append(ResizedOpening(at=[round(mid.x, 4), round(mid.y, 4)], to=put))
            else:
                back = math.dist(had.at, put) < 0.005  # (moved back to where the drawing has it)
                had.to = None if back else put
                if all(v is None for v in had.model_dump(exclude={"at"}).values()):
                    f.edits.resized = [x for x in f.edits.resized if x is not had]
            kind = record.type if record.type in history.OPENINGS else "opening"
        if record is None:
            return kind, None
        return kind, (_shifted(record, dx, dy), [dx, dy])

    def _space(self, ws: Workspace, r: ObjectRecord, seats: dict | None = None, cat=None) -> dict:
        eff = ws.effective(r)
        o = ws.overrides.get(r.id)
        if seats is None:  # what the items on its floor say of it
            seats = self._seats(ws, cat or self.catalogue(), floor_of(r.id))
        capacity, capacity_from = capacity_of(eff, seats.get(r.id))
        geo = self._shape(r)
        return {
            "id": r.id, "kind": r.kind, "space_id": r.parent, "zones": list(r.zones),
            "type": eff["type"], "name": eff["name"], "number": eff["number"],
            "detected": {"type": r.type, "name": r.name, "number": r.number, "source": r.type_source},
            "drawing_label": r.label,
            # a person's correction or check ({}: accepted as it is); not a capacity alone
            "correction": o.model_dump(exclude_none=True, exclude=KEPT) if o and eff["corrected"] else None,
            # what its floor and (a space's) walls are finished in, set here; null: as its type
            "floor_finish": eff["floor_finish"], "wall_finish": eff["wall_finish"], "wall_finishes": eff["wall_finishes"],
            "hidden": eff["hidden"], "ignored": eff["ignored"],
            # how many it seats: set here, else its desks'; and who it is laid out for
            "capacity": capacity, "capacity_from": capacity_from, "capacity_set": eff["capacity"],
            "workplaces": (seats.get(r.id) or {}).get("workplaces", 0), "grade": (seats.get(r.id) or {}).get("grade"),
            "reasons": ws.review_reasons(r),
            "area": geo.get("area", 0.0),
            "label_point": geo.get("label_point", [0.0, 0.0]),
            "label_room": geo.get("label_room", [0.0, 0.0]),
            "geometry": r.geometry,
        }

    def correct(self, object_id: str, body: dict, by=None, editor: Editor | None = None) -> dict:
        """Change a space's correction:

        ``{"correction": {type, name, number}}`` replaces type, name and number: fields
        left out (or null) use what was detected, "" removes a detected name or number,
        {} accepts it as it is. ``{"hidden": bool}`` and ``{"ignored": bool}`` set the
        flags. ``{"capacity": n}`` sets how many people it is meant to seat (null: as its
        desks say). ``{"stack": id}`` links a lift, stairs, escalator or ramp to one on
        another floor of its building (stacks.py), ``""`` to none, null: as found.
        ``{"floor_finish": code}`` and (a space's) ``{"wall_finish": code}`` set what its floor
        and walls are finished in (finishes.py; null: as its type); ``wall_finish`` sets all
        its walls, those finished one by one too. ``{"wall_finish_at": {"line": [[x, y], [x,
        y]], "finish": code}}`` one wall (its face towards the room, building metres; null:
        as the rest).
        ``{"reset": true}`` removes the correction (the flags, capacity, link and finishes stay)."""

        def fn(ws: Workspace):
            r = ws.objects.get(object_id)
            if r is None or r.status != "active" or r.kind not in ("space", "zone", "opening"):
                raise NotFound(f"no active space, zone or opening {object_id}")
            if r.kind == "opening" and set(body) - {"ignored"}:
                raise ValueError("an opening can only be deleted or restored")
            current = ws.overrides.get(object_id) or Override()
            finish = {"floor_finish": current.floor_finish, "wall_finish": current.wall_finish,
                      "wall_finishes": current.wall_finishes}
            for key, applies in (("floor_finish", "floor"), ("wall_finish", "wall")):
                if key in body:
                    if applies == "wall" and r.kind != "space":
                        raise ValueError("a zone has no walls of its own: set its space's wall_finish")
                    finish[key] = finishes.check(body[key], applies)
            if "wall_finish" in body:  # all its walls: none finished otherwise any more
                finish["wall_finishes"] = None
            if "wall_finish_at" in body:  # one wall
                if r.kind != "space":
                    raise ValueError("a zone has no walls of its own: set its space's wall_finish_at")
                finish["wall_finishes"] = _one_wall(finish["wall_finishes"] or [], body["wall_finish_at"]) or None
            capacity = current.capacity
            if "capacity" in body:
                capacity = body["capacity"]
                if capacity is not None and (isinstance(capacity, bool) or not isinstance(capacity, int)
                                             or not 0 <= capacity <= 10000):
                    raise ValueError("capacity is a whole number from 0, or null (as its desks say)")
            # hidden False is no flag (nothing is hidden as detected): it marks a check, below
            flags = {"hidden": current.hidden or None, "ignored": current.ignored}
            detected = {"hidden": False, "ignored": bool(r.detected_ignored)}
            for flag in ("hidden", "ignored"):
                if flag in body:
                    if not isinstance(body[flag], bool):
                        raise ValueError(f"{flag} is true or false")
                    # as detected: no correction; otherwise kept, so a space Studio set
                    # aside (vision: not a room) can be restored
                    flags[flag] = None if body[flag] == detected[flag] else body[flag]
            if body.get("reset"):
                values = {}
            elif "correction" in body:
                c = body["correction"]
                if not isinstance(c, dict) or set(c) - set(CORRECTABLE):
                    raise ValueError(f"correction must be an object with any of: {', '.join(CORRECTABLE)}")
                values = {k: v.strip() if isinstance(v, str) else v for k, v in c.items() if v is not None}
                if "type" in values:
                    values["type"] = SpaceType(values["type"])
            elif any(f in body for f in flags) or set(body) & {"capacity", "stack", "floor_finish", "wall_finish",
                                                              "wall_finish_at"}:
                values = current.model_dump(exclude_none=True, exclude=KEPT)
            else:
                raise ValueError("nothing to change")
            stack = current.stack
            if "stack" in body:  # linked as it will be typed: a lift drawn is typed and linked at once
                typed = values.get("type") if "correction" in body else None
                stack = _stack_target(ws, r, body["stack"], typed or ws.effective(r)["type"])
            # Checked: a person saved a correction, or accepted it as it is, and has not reset
            # it since. A flag or a capacity set keeps that as it was: a capacity alone is no
            # check (workspace.effective), nor one left empty by clearing a flag.
            was_checked = object_id in ws.overrides and ws.effective(r)["corrected"] \
                and not (current.hidden or current.ignored)
            checked = False if body.get("reset") else True if "correction" in body else was_checked
            override = Override(**values, **flags, capacity=capacity, stack=stack, **finish)
            if checked and not values and (capacity is not None or stack is not None or any(finish.values())) \
                    and override.hidden is None:
                override.hidden = False  # accepted as it is, with a capacity, a link or finishes: the mark of the check
            new = None if override == Override() and not checked else override
            had = ws.overrides.get(object_id)
            kind = "reset" if body.get("reset") else "correct" if "correction" in body else \
                ("delete" if body["ignored"] else "restore") if "ignored" in body else \
                ("hide" if body["hidden"] else "show") if "hidden" in body else \
                ("link" if stack else "unlink" if stack == NOT_LINKED else "as found") if "stack" in body else \
                "finish" if {"floor_finish", "wall_finish", "wall_finish_at"} & set(body) else "capacity"
            ch = Changes(part="object", kind=kind, targets=[object_id], floors={floor_of(object_id)},
                         before={"override": _dump(had)}, after={"override": _dump(new)},
                         more={**_object_lines(r, had, new, kind), **_finish_lines(body, finish)})
            if not (had is None and new is None) and not _same(had, new):
                ch.overrides[object_id] = new
            return ch, None

        self._change(fn, by, editor)
        ws = self.workspace()
        r = ws.objects[object_id]
        return self._door(ws, r, self._floor(ws, floor_of(object_id)).edits.resized) if r.kind == "opening" \
            else self._space(ws, r)

    def finish_rooms(self, floor_id: str, body: dict, by=None, editor: Editor | None = None) -> dict:
        """Many rooms of a floor finished at once, as one change (undone as one): ``{"ids":
        [space and zone IDs]}`` or ``{"type": t}`` (every space and zone in use of that type
        on the floor, as corrected), with ``floor_finish`` and/or ``wall_finish`` (a code of
        finishes.py, or null for each room's type's default; walls only a space's: a zone's
        are its space's). {"changed": the IDs whose finishes changed, "spaces": each as the
        floor lists it}."""
        if not isinstance(body, dict) or not ({"floor_finish", "wall_finish"} & set(body)):
            raise ValueError('send {"ids": […] or "type": t, "floor_finish": code or null, "wall_finish": code or null}')
        floor_code = finishes.check(body["floor_finish"], "floor") if "floor_finish" in body else None
        wall_code = finishes.check(body["wall_finish"], "wall") if "wall_finish" in body else None

        def fn(ws: Workspace):
            self._floor(ws, floor_id)
            if isinstance(body.get("ids"), list):
                ids = body["ids"]
                if len(ids) > FINISHED_AT_ONCE or not all(isinstance(i, str) for i in ids):
                    raise ValueError(f"ids: at most {FINISHED_AT_ONCE} IDs of spaces and zones")
                rooms = []
                for i in dict.fromkeys(ids):
                    r = ws.objects.get(i)
                    if r is None or r.status != "active" or r.kind not in ("space", "zone") or floor_of(i) != floor_id:
                        raise NotFound(f"no active space or zone {i} on this floor")
                    rooms.append(r)
                label = None
            elif isinstance(body.get("type"), str):
                kind = SpaceType(body["type"])
                rooms = [r for r in ws.floor_objects(floor_id) if r.kind in ("space", "zone") and not r.zones
                         and ws.effective(r)["type"] == kind.value]
                label = history.type_label(kind.value)
            else:
                raise ValueError('name the rooms: {"ids": […]} or {"type": "office"}')
            before, after, changed = {}, {}, []
            ch = Changes(part="object", kind="finish", floors={floor_id})
            for r in rooms:
                had = ws.overrides.get(r.id)
                current = had or Override()
                update = {}
                if "floor_finish" in body:
                    update["floor_finish"] = floor_code
                if "wall_finish" in body and r.kind == "space":
                    update["wall_finish"] = wall_code
                    update["wall_finishes"] = None  # all its walls: none finished otherwise any more
                new = current.model_copy(update=update)
                checked = had is not None and ws.effective(r)["corrected"] and not (current.hidden or current.ignored)
                if checked and new.type is None and new.name is None and new.number is None and new.hidden is None:
                    new.hidden = False  # accepted as it is: the mark of the check stays
                if new == Override():
                    new = None
                if _same(had, new):
                    continue
                before[r.id], after[r.id] = _dump(had), _dump(new)
                ch.overrides[r.id] = new
                changed.append(r.id)
            ch.targets = changed
            ch.before, ch.after = {"overrides": before}, {"overrides": after}
            n = len(changed)
            ch.more = {"what": "space", "rooms": n,
                       "label": (f"{n} {label}s" if label and n != 1 else f"1 {label}" if label else
                                 f"{n} rooms" if n != 1 else None),
                       **_finish_lines(body, {"floor_finish": floor_code, "wall_finish": wall_code})}
            ch.more = {k: v for k, v in ch.more.items() if v is not None}
            if n == 1 and not label:
                ch.more.update({k: v for k, v in _object_lines(ws.objects[changed[0]], None, None, "finish").items()
                                if k == "label"})
            return ch, changed

        changed = self._change(fn, by, editor)
        ws = self.workspace()
        cat = self.catalogue()
        seats = self._seats(ws, cat, floor_id)
        return {"changed": changed, "spaces": [self._space(ws, ws.objects[i], seats, cat) for i in changed]}

    # ---- undo and redo: each person their own changes ---------------------------------

    def steps(self, by=None, floor_id: str | None = None, visible=None) -> dict:
        """What the person would undo now (on ``floor_id``) and what they would redo:
        {"undo": {seq, line, at} or None, "redo": …}."""
        store = getattr(self.source, "store", None)
        if store is None:
            return {"undo": None, "redo": None}
        who = who_of(by)
        rows = store.own_rows(self.source.code, who, history.UNDOABLE, history.STACK_DEPTH)
        return history.next_steps(rows, history.key(who), floor_id, visible)

    def step_back(self, floor_id: str | None = None, by=None, editor: Editor | None = None, *, redo: bool = False,
                  check=None) -> dict:
        """The person's latest change (on ``floor_id``) undone by applying its inverse, as
        any change is made (Busy while a job works on the project; Locked while another
        person edits its floor) and recorded as an undo of it; with ``redo``, their latest
        undoing redone. Refused (Conflict, naming who and when) when what it changed was
        changed since. ``check(row)``: whether the person may (raises when not). An item
        added is retired, never taken out (its ID is never given again); what was drawn
        is taken away or put back, and the floor is to be read again (``read``). What was
        done: {seq, line, part, floors, read}."""
        store = getattr(self.source, "store", None)
        if store is None:
            raise ValueError("undo needs Studio's database: this project is a file here")
        code, who = self.source.code, who_of(by)
        me = history.key(who)
        doing = "redone" if redo else "undone"
        undo, redo_ = history.stacks(store.own_rows(code, who, history.UNDOABLE, history.STACK_DEPTH), me)
        entry = history.latest(redo_ if redo else undo, floor_id)
        if entry is None:
            where = " on this floor" if floor_id else ""
            raise Conflict(f"nothing of yours to {'redo' if redo else 'undo'}{where}", nothing=True)
        if check is not None:
            check(entry)
        # someone else's change since, of what it changed (one's own later ones are undone
        # first, or are this one's undoing and redoing; the state is checked below anyway)
        later = [e for e in store.later_rows(code, entry["seq"], entry["targets"], entry["floors"])
                 if history.touches(e, entry) and history.key(e.get("who")) != me]
        if later:
            raise history.refused(entry, later[0], me, doing)

        def fn(ws: Workspace):
            ch = Changes(part=entry["part"], kind=entry["kind"], targets=list(entry["targets"]),
                         floors=set(entry["floors"]),
                         more={k: entry[k] for k in LINE_KEYS if entry.get(k) is not None})
            ch.more["redoes" if redo else "undoes"] = entry["seq"]
            _inverse(ws, entry, ch, lambda: history.refused(entry, None, me, doing))
            return ch, None

        self._change(fn, by, editor)
        return {"seq": entry["seq"], "line": history.describe(entry), "part": entry["part"],
                "floors": entry["floors"], "read": entry["part"] == "edit"}

    def drawing(self, floor_id: str) -> dict:
        ws = self.workspace()
        f = self._floor(ws, floor_id)
        if f.source is None:
            return {"groups": {}, "texts": []}
        src = f.source
        key = (floor_id, *self.source.drawing_key(src), src.profile, src.units, src.region, src.offset)
        if key not in self._drawings:
            with self.source.drawing_file(src) as path:
                doc = read_drawing(path)
            profile = load_profile(self.source.profile(src))
            self._drawings[key] = drawing_linework(
                doc, profile, meters_per_unit(doc, src.units), src.region, src.offset
            )
        return self._drawings[key]


def _stack_target(ws: Workspace, r: ObjectRecord, target, kind: str) -> str | None:
    """A space's link to other floors from a request (``kind``: its type, as it will be):
    the ID of a lift, stairs, escalator or ramp on another floor of its building, ""
    (linked to none) or None (as found)."""
    if target is None:
        return None
    if not isinstance(target, str):
        raise ValueError('stack is the ID of a lift or stairs on another floor, "" for none, or null for as found')
    if r.kind != "space" or kind not in STACK_TYPES:
        raise ValueError("only a lift, stairs, escalator or ramp is linked to other floors")
    if target == NOT_LINKED:
        return target
    t = ws.objects.get(target)
    if t is None or t.status != "active" or t.kind != "space":
        raise NotFound(f"no space {target}")
    here, there = floor_of(r.id), floor_of(target)
    if here == there or floor_of(here) != floor_of(there):
        raise ValueError("a lift or stairs is linked to one on another floor of its building")
    if ws.effective(t)["type"] not in STACK_TYPES:
        raise ValueError(f"{target} is not a lift, stairs, escalator or ramp")
    return target


DRAWN_KINDS = ("wall", "divider", "opening", "space")
SAME_POINT_M = 0.001  # a shape sent back is the one drawn when its points are this near


def _remove(f, body: dict) -> None:
    """What a person drew on a floor, taken away: of its ``kind`` (wall, divider,
    opening, space) the one whose points are ``shape`` (as the floor's edits give
    them), else the one nearest ``at``. Without a kind (an older page), the nearest of
    any, a line before a space it lies in."""
    lists = {"wall": f.edits.walls, "divider": f.edits.dividers, "opening": f.edits.openings,
             "space": f.edits.spaces}
    kind = body.get("kind")
    if kind is not None and (not isinstance(kind, str) or kind not in lists):
        raise ValueError(f"kind is one of {', '.join(DRAWN_KINDS)}")
    kinds = [kind] if kind else list(DRAWN_KINDS)

    def points(k, x):
        return x.span if k == "opening" else x

    if body.get("shape") is not None:
        want = _closed(_xy(body["shape"], "shape: its points [[x, y], …], as drawn"))
        for k in kinds:
            for i, x in enumerate(lists[k]):
                have = _closed(points(k, x))
                if len(have) == len(want) and all(abs(a - b) <= SAME_POINT_M for p, q in zip(have, want)
                                                  for a, b in zip(p, q)):
                    lists[k].pop(i)
                    return
        raise NotFound(f"no {kind or 'drawing'} drawn so here: it may have been taken away already")
    at = Point(_points([body["at"]], 1)[0])

    def reach(k, x):  # a drawn space: anywhere inside it
        if k == "space":
            return Polygon(x).distance(at)
        return LineString(points(k, x)).distance(at)

    # nearest first; at one distance, a line before a space (one drawn across it lies in it)
    drawn = [(reach(k, x), k == "space", DRAWN_KINDS.index(k), i) for k in kinds for i, x in enumerate(lists[k])]
    near = [d for d in drawn if d[0] <= REMOVE_REACH_M]
    if not near:
        raise NotFound(f"no {kind or 'drawing'} drawn there")
    _, _, k, i = min(near)
    lists[DRAWN_KINDS[k]].pop(i)


def _closed(pts) -> list:
    """A ring's points without the first again at its end."""
    pts = [list(p) for p in pts]
    return pts[:-1] if len(pts) > 2 and pts[0] == pts[-1] else pts


DRAWN_SPACE_MIN_M2 = 1.0


def _ring(value) -> list[list[float]]:
    """A space's outline from a request: three or more points [x, y] (local meters),
    a simple shape of at least DRAWN_SPACE_MIN_M2."""
    if isinstance(value, list) and len(value) > 501:
        raise ValueError("a space has 3 to 500 corners")
    pts = _xy(value, "a space is its corners: [[x, y], [x, y], [x, y], …]")
    if len(pts) > 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3 or len(pts) > 500:
        raise ValueError("a space has 3 to 500 corners")
    poly = Polygon(pts)
    if not poly.is_valid:
        raise ValueError("a space's outline must not cross itself")
    if poly.area < DRAWN_SPACE_MIN_M2:
        raise ValueError(f"a space is at least {DRAWN_SPACE_MIN_M2:g} m²")
    return pts


def _finite(value, what: str) -> float:
    """A finite number from a request: NaN and the infinities are not numbers here."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"{what} is a number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{what} is a number") from None
    if not math.isfinite(number):
        raise ValueError(f"{what} is a number")
    return number


def _number(body: dict, key: str, default: float | None = None) -> float:
    return _finite(body.get(key, default), key)


def _xy(value, error: str) -> list[list[float]]:
    """Points [x, y] (local meters, finite) from a request; ValueError(``error``) when
    they are not."""
    try:
        return [[round(_finite(x, "x"), 4), round(_finite(y, "y"), 4)] for x, y in value]
    except (TypeError, ValueError):
        raise ValueError(error) from None


def _points(value, n: int) -> list[list[float]]:
    """``n`` points [x, y] (local meters) from a request."""
    if isinstance(value, list) and len(value) != n:
        raise ValueError(f"expected {n} points [x, y]")
    pts = _xy(value, f"expected {n} points [x, y]")
    if len(pts) != n:
        raise ValueError(f"expected {n} points [x, y]")
    return pts


def _given_at(resized, middle: Point):
    """What was given in review to the drawing's opening whose middle is ``middle`` now
    (moved, or where the drawing has it), or None."""
    return next((x for x in resized if Point(x.to or x.at).distance(middle) <= REMOVE_REACH_M), None)


def _shifted(r: ObjectRecord, dx: float, dy: float) -> ObjectRecord:
    """An opening's record moved by (dx, dy): where it is, its span, its leaves."""
    shift = lambda pts: [[round(x + dx, 4), round(y + dy, 4)] for x, y in pts]  # noqa: E731
    x, y = r.geometry["coordinates"][:2]
    return r.model_copy(update={"geometry": {"type": "Point", "coordinates": [round(x + dx, 4), round(y + dy, 4)]},
                                "span": shift(r.span) if r.span else r.span,
                                "swings": [shift(leaf) for leaf in r.swings] if r.swings else r.swings}, deep=True)


def _on_walls(f, span, moved, gaps=()) -> bool:
    """Whether a span moved along its wall is still in it: the walls as read along nine
    tenths of it, give or take a wall's thickness, the gaps of its own and of the floor's
    other openings (``gaps``, their spans) taken as wall (a door slid across a window's)."""
    if not f.walls:
        return True  # (no walls read: nothing to tell)
    t = max(f.wall_thickness or 0.2, 0.1)
    walls = unary_union([shape(f.walls)] + [LineString(g).buffer(t, cap_style="flat") for g in [span, *gaps] if len(g) >= 2])
    line = LineString(moved)
    inside = sum(1 for k in range(11) if walls.distance(line.interpolate(k / 10, normalized=True)) <= t / 2)
    return inside >= 10


def _swing(value, span) -> list[list[float]] | None:
    """A drawn door's leaf when open, from a request: [hinge, its free edge], the hinge
    on one of its jambs (put exactly on it), as long as the door is wide and across its
    wall (SWING_*). None for none."""
    if value is None:
        return None
    hinge, free = _points(value, 2)
    jamb = min(span, key=lambda j: math.dist(j, hinge))
    width = math.dist(span[0], span[1])
    if math.dist(jamb, hinge) > SWING_JAMB_M:
        raise ValueError("a door's swing is hung on one of its jambs")
    leaf = math.dist(hinge, free)
    if not SWING_LENGTH[0] * width <= leaf <= SWING_LENGTH[1] * width:
        raise ValueError("a door's leaf is as long as the door is wide")
    along = ((span[1][0] - span[0][0]) * (free[0] - hinge[0]) + (span[1][1] - span[0][1]) * (free[1] - hinge[1])) / (width * leaf)
    if abs(along) > SWING_ACROSS:
        raise ValueError("a door's leaf opens across its wall")
    dx, dy = free[0] - hinge[0], free[1] - hinge[1]
    return [[round(jamb[0], 4), round(jamb[1], 4)], [round(jamb[0] + dx, 4), round(jamb[1] + dy, 4)]]


def _size(value, key: str, band: tuple[float, float]) -> float | None:
    """A size in metres from a request, within its band; None (as drawn) for null."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} is a number of metres, or null") from None
    if not band[0] <= v <= band[1]:
        raise ValueError(f"{key} is {band[0]:g} to {band[1]:g} m")
    return round(v, 3)


def _resized_span(span, width: float) -> list[list[float]]:
    from .geometry import span_of_width

    return span_of_width(span, width)


def _door_of(ws: Workspace, floor_id: str, t, door_id) -> str | None:
    """``door_id`` as the door an item of type ``t`` on a floor serves, checked: one of the
    floor's doors (_doors), and a type that serves one; None: none."""
    if door_id is None:
        return None
    if t is None or not t.serves_door:
        raise ValueError(f"{t.name_en if t else 'This item'} serves no door")
    if not isinstance(door_id, str) or door_id not in {r.id for r in _doors(ws, floor_id)}:
        raise ValueError(f"no door {str(door_id)[:40]} on the item's floor")
    return door_id


def _nearest_door(ws: Workspace, floor_id: str, at) -> str | None:
    """The door nearest ``at`` on a floor (its span, jamb to jamb, else where it is), when
    within DOOR_REACH_M; None: none so near."""
    p = Point(at)
    near = min((((LineString(r.span) if r.span and len(r.span) >= 2 else Point(r.geometry["coordinates"])).distance(p), r.id)
                for r in _doors(ws, floor_id)), default=None)
    return near[1] if near and near[0] <= DOOR_REACH_M else None


def _middle(r: ObjectRecord) -> Point:
    """An opening's middle: of its span when known, else where it is."""
    if r.span and len(r.span) >= 2:
        return LineString(r.span).interpolate(0.5, normalized=True)
    return Point(r.geometry["coordinates"])


def _round_box(box) -> list[float]:
    return [round(v, 3) for v in box]


def _print_of(review: "Review", floor_id: str, text: bool = True) -> tuple[Path, Path]:
    """Where a floor's print and its placement are kept, drawing them first when the
    drawing, the part of it read or the floor's spaces changed. Without ``text``: the
    print with the drawing's texts left out (Review's labels are written over it, so a
    room is not labelled twice), kept beside the other."""
    ws = review.workspace()
    f = review._floor(ws, floor_id)
    if f.source is None:
        raise NotFound(f"floor {floor_id} has no drawing")
    src = f.source
    stamp = review.source.drawing_key(src)
    extent = None
    if src.region is None:  # around what was found on the floor
        geoms = [review._shape(r)["shape"] for r in ws.floor_objects(floor_id) if r.kind == "space"]
        if f.outline:
            geoms.append(shape(f.outline))
        geoms = [g for g in geoms if not g.is_empty]
        if geoms:
            xs0, ys0, xs1, ys1 = zip(*(g.bounds for g in geoms))
            extent = _round_box((min(xs0) - PRINT_MARGIN_M, min(ys0) - PRINT_MARGIN_M,
                                 max(xs1) + PRINT_MARGIN_M, max(ys1) + PRINT_MARGIN_M))
    raw = json.dumps([PRINT_VERSION, *stamp, src.units, src.region, src.offset, extent, *([] if text else ["no text"])])
    key = hashlib.sha1(raw.encode()).hexdigest()[:16]
    folder = review.source.prints()
    stem = floor_id if text else f"{floor_id}~notext"  # (a floor's ID has no ~: the two kept apart)
    png, info = folder / f"{stem}-{key}.png", folder / f"{stem}-{key}.json"
    if png.exists() and info.exists():
        return png, info
    with _PRINTING:
        if png.exists() and info.exists():
            return png, info
        from ezdxf import bbox as ebbox

        from .vision import print_png

        with review.source.drawing_file(src) as path:
            doc = read_drawing(path)
        scale = meters_per_unit(doc, src.units)
        ox, oy = src.offset or (0.0, 0.0)
        if src.region is not None:
            x0, y0, x1, y1 = src.region
            local = ((x0 - ox) * scale, (y0 - oy) * scale, (x1 - ox) * scale, (y1 - oy) * scale)
        elif extent is not None:
            local = tuple(extent)
        else:  # nothing found yet: the whole drawing
            box = ebbox.extents(doc.modelspace(), fast=True)
            local = ((box.extmin.x - ox) * scale, (box.extmin.y - oy) * scale,
                     (box.extmax.x - ox) * scale, (box.extmax.y - oy) * scale)
        x0, y0, x1, y1 = local
        w, h = max(x1 - x0, 0.01), max(y1 - y0, 0.01)
        per_m = min(PRINT_PX_PER_M, PRINT_MAX_PX / max(w, h))
        width, height = max(1, round(w * per_m)), max(1, round(h * per_m))
        drawing_box = (x0 / scale + ox, y0 / scale + oy, x1 / scale + ox, y1 / scale + oy)
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob(f"{stem}-*"):  # an earlier print of this floor
            old.unlink(missing_ok=True)
        part = png.with_suffix(".part")
        part.write_bytes(print_png(doc, drawing_box, width, height, text=text))
        part.replace(png)
        info.write_text(json.dumps({"bounds": _round_box(local), "width": width, "height": height,
                                    "px_per_m": round(per_m, 2), "key": key,
                                    "drawing": drawing_name(src.path)}))  # (its prints go with it: deleted)
    return png, info


def _room_at(geom, x: float, y: float) -> tuple[float, float]:
    """How much room a label at (x, y) has: the width and height of the space
    measured through that point."""
    x0, y0, x1, y1 = geom.bounds
    point = Point(x, y)
    out = []
    for line in (LineString([(x0 - 1, y), (x1 + 1, y)]), LineString([(x, y0 - 1), (x, y1 + 1)])):
        cut = line.intersection(geom)
        parts = getattr(cut, "geoms", [cut])
        out.append(min((p.length for p in parts if p.distance(point) < 1e-6), default=0.0))
    return out[0], out[1]


def floor_print(review: "Review", floor_id: str, text: bool = True) -> dict:
    """A floor's drawing as printed: where it lies (local meters) and its size (without
    ``text``: of the print with its texts left out)."""
    return json.loads(_print_of(review, floor_id, text)[1].read_text())


def floor_print_png(review: "Review", floor_id: str, text: bool = True) -> File:
    return File(_print_of(review, floor_id, text)[0].read_bytes(), "image/png")


def drawing_linework(doc, profile: Profile, scale: float, region=None, offset=None) -> dict:
    """What is visible in a drawing (or in ``region`` of it, moved by ``offset``, as
    for a floor), as polylines in meters (flat [x0, y0, x1, y1, …] lists) grouped by
    what the profile makes of their layers, and its texts as
    [x, y, height, rotation, lines, is_label]."""
    hidden = {layer.dxf.name for layer in doc.layers if layer.is_off() or layer.is_frozen()}
    tol = CURVE_TOLERANCE_M / scale
    ox, oy = offset or (0.0, 0.0)
    groups: dict[str, list[list[float]]] = {"walls": [], "doors": [], "outlines": [], "other": []}
    texts = []
    for e, layer in _walk(modelspace_entities(doc, region)):
        kind = e.dxftype()
        if layer in hidden or kind in ("INSERT", "HATCH", "DIMENSION", "POINT"):
            continue
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
            lines, c = _text_lines(e), _center(e)
            if lines and c:
                height = e.dxf.get("char_height" if kind == "MTEXT" else "height", 0) or 0
                rotation = e.get_rotation() if kind == "MTEXT" else e.dxf.get("rotation", 0)
                texts.append([round((c[0] - ox) * scale, 2), round((c[1] - oy) * scale, 2), round(height * scale, 3),
                              round(rotation, 1), lines, bool(profile.label_layers.fullmatch(layer))])
            continue
        flat = _flatten(e, tol)
        if flat is None:
            continue
        pts, closed = flat
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        if profile.wall_layers.fullmatch(layer):
            group = "walls"
        elif profile.door_layers.fullmatch(layer):
            group = "doors"
        elif profile.space_layers.fullmatch(layer):
            group = "outlines"
        else:
            group = "other"
        groups[group].append([round(v, 2) for x, y in pts for v in ((x - ox) * scale, (y - oy) * scale)])
    return {"groups": groups, "texts": texts}
