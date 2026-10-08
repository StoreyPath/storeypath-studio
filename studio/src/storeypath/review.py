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
                                                  {"ignored": bool} deletes a space or an opening (or restores it)
    POST /api/projects/<code>/floors/<id>/edits   {"add": {"wall": [[x, y], [x, y]]}}, {"add": {"divider": …}},
                                                  {"add": {"opening": {"type", "span"}}} or
                                                  {"remove": {"kind", "shape": [[x, y], …], "at": [x, y]}}:
                                                  what a person draws, kept through every conversion; then
                                                  the floor is read again (a job)

A change is refused (Busy, answered 409) while a job works on the project: the job
would save over it.

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

from .cad import DrawingError, meters_per_unit, read_drawing
from .db.store import Changes, _same, drawing_name, floor_of
from .errors import Busy, NotFound
from .export import _label_point, capacity_of, seating
from .extract import CURVE_TOLERANCE_M, _center, _flatten, _text_lines, _walk, modelspace_entities
from .ids import make_item_id
from .profile import Profile, load_profile, resolve_profile
from .stacks import NOT_LINKED, STACK_TYPES
from .types import SpaceType
from .workspace import DrawnOpening, Item, ObjectRecord, Override, ResizedOpening, Workspace, utcnow

__all__ = ["Busy", "NotFound", "Review", "ProjectFile", "StoredProject", "File"]

CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml",
                 ".json": "application/json", ".png": "image/png", ".woff2": "font/woff2",
                 ".storeypath": "application/zip"}
CORRECTABLE = ("type", "name", "number")
REMOVE_REACH_M = 0.5  # removing what was drawn takes the drawn wall or opening this near
RESIZE_BANDS = {"width": (0.3, 8.0), "sill": (0.0, 3.0), "height": (0.3, 10.0)}  # metres
PRINT_PX_PER_M = 100  # a floor's print: a pixel a centimetre…
PRINT_MAX_PX = 10000  # …within this many pixels a side
PRINT_MARGIN_M = 3.0  # around the floor's spaces, when the floor has no region of its own
PRINT_VERSION = 1  # changing how prints are drawn draws them again
CACHE_DIR = ".storeypath-cache"  # beside the workspace file
_PRINTING = threading.Lock()  # one print drawn at a time: rendering goes through ezdxf's caches
EDIT_LISTS = ("walls", "dividers", "openings", "resized", "spaces")


class File(NamedTuple):
    """A file the server sends as it is."""

    data: bytes
    content_type: str


BUSY_MESSAGE = ("a job is working on this project (adding floors, converting or exporting): "
                "nothing was changed; make the change again when the job is done")
BUSY_WAIT_S = 1.0  # a change waits this long for a short one (placing a building) to finish


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

    def change(self, fn, by=None):
        with self._lock:
            base = self.current()
            changes, result = fn(base)
            if not changes.empty():
                ws = changes.apply(base)
                ws.save(self.path)
                self._ws, self._stamp = ws, self._on_disk()
            return result

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

    def change(self, fn, by=None):
        return self.store.change(self.code, fn, by=by)

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


def _dump(model) -> dict | None:
    return None if model is None else model.model_dump(mode="json", exclude_none=True)


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

    def _change(self, fn, by=None):
        """A change (``fn``: the project as it is -> (Changes, answer)), made only while no
        job is changing the project (else Busy: a job saves the copy it loaded, which
        would lose the change). One that fails part way changes nothing."""
        lock = self._changing() if self._changing else None
        if lock is not None and not lock.acquire(timeout=BUSY_WAIT_S):
            raise Busy(BUSY_MESSAGE)
        try:
            return self.source.change(fn, by=by)
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
        for loc, b, f, fid in ws.iter_floors():
            spaces = [r for r in ws.floor_objects(fid) if r.kind in ("space", "zone") and not r.zones]
            floors.append({
                "id": fid, "name": f.name, "ordinal": f.ordinal,
                "building": b.name, "building_id": fid.rsplit("-", 1)[0], "location": loc.name,
                "source": Path(f.source.path).name if f.source else None,
                "converted": f.converted_at is not None,
                "spaces": len(spaces), "review": sum(1 for r in spaces if ws.review_reasons(r)),
            })
        return {
            "project": {"id": ws.id, "name": ws.project.name},
            "file": self.source.name,
            "floors": floors,
            "types": [t.value for t in SpaceType],
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
        units = [(self._shape(r)["shape"], r) for r in objects
                 if r.kind in ("space", "zone") and r.geometry and not ws.effective(r)["ignored"]]
        return seating(ws, cat, floor_id, units=units)

    def floor(self, floor_id: str) -> dict:
        ws = self.workspace()
        f = self._floor(ws, floor_id)
        cat = self.catalogue()
        objects = sorted(ws.floor_objects(floor_id), key=lambda r: r.id)
        seats = self._seats(ws, cat, floor_id, objects)
        return {
            "id": floor_id, "name": f.name, "ordinal": f.ordinal,
            "source": Path(f.source.path).name if f.source else None,
            "converted_at": f.converted_at.isoformat() if f.converted_at else None,
            "method": f.method, "warnings": f.warnings, "outline": f.outline,
            # spaces and their zones; a space divided into zones is used through them
            # a shape with nothing in it (a sliver read by an older Studio) is not shown
            "spaces": [self._space(ws, r, seats, cat) for r in objects
                       if r.kind in ("space", "zone") and not self._shape(r)["empty"]],
            "doors": [self._door(ws, r, f.edits.resized) for r in objects if r.kind == "opening"],
            # for drawing walls and doors onto: the walls as found, and what was drawn
            "walls": f.walls, "wall_thickness": f.wall_thickness, "edits": f.edits.model_dump(),
            # furniture and equipment on the floor, retired ones too (to be restored)
            "items": [self._item(i, cat) for i in sorted(ws.floor_items(floor_id, include_retired=True),
                                                          key=lambda i: i.id)],
        }

    # ---- items: furniture and equipment ---------------------------------------------

    def catalogue(self):
        from .catalogue import default_catalogue

        return self._catalogue() if self._catalogue else default_catalogue()

    def _item(self, it, cat=None) -> dict:
        t = (cat or self.catalogue()).get(it.type)
        return {"id": it.id, "type": it.type, "floor_id": it.floor_id, "x": it.x, "y": it.y, "rotation": it.rotation,
                "values": it.values, "retired": it.status == "retired",
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
            elif f.kind == "choice" and value not in f.choices:
                raise ValueError(f"{key} is one of {', '.join(f.choices)}")
            elif f.kind == "color" and not (isinstance(value, str) and len(value) == 7 and value.startswith("#")):
                raise ValueError(f"{key} is a colour, #rrggbb")
            elif f.kind == "text":
                value = str(value).strip()[:200]
            out[key] = value
        return out

    def add_item(self, floor_id: str, body: dict, by=None) -> dict:
        """An item placed on a floor: ``{type, x, y, rotation?, values?}`` (local metres),
        numbered after every item of the project."""
        cat = self.catalogue()

        def fn(ws: Workspace):
            self._floor(ws, floor_id)
            values = self._item_values(body.get("type"), body.get("values"), cat)
            x, y = _number(body, "x"), _number(body, "y")
            it = Item(id=make_item_id(ws.id, ws.next_item_seq), type=body["type"], floor_id=floor_id, x=x, y=y,
                      rotation=_number(body, "rotation", 0.0) % 360, values=values)
            ch = Changes(part="item", kind="add", targets=[it.id], floors={floor_id},
                         before={"item": None}, after={"item": _dump(it)})
            ch.items[it.id] = it
            ch.project = {"next_item_seq": ws.next_item_seq + 1}
            return ch, it

        return self._item(self._change(fn, by), cat)

    def change_item(self, item_id: str, body: dict, by=None) -> dict:
        """An item moved, turned, given another type or details, carried to another floor
        (``floor_id``), taken away (``{"retired": true}``) or brought back: all that is
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
                new.floor_id = body["floor_id"]
            if "type" in body:
                self._item_values(body["type"], None, cat)  # a type of the catalogue
                new.type = body["type"]
                own = {f.key for f in cat.get(new.type).fields if f.owner == "storeypath"}
                new.values = {k: v for k, v in new.values.items() if k in own}  # what the new type has
                kinds.append("retype")
            if "values" in body:
                new.values = self._item_values(new.type, body["values"], cat)
                kinds.append("values")
            for key in ("x", "y"):
                if key in body:
                    setattr(new, key, _number(body, key))
            if "x" in body or "y" in body:
                kinds.append("move")
            if "rotation" in body:
                new.rotation = _number(body, "rotation") % 360
                kinds.append("turn")
            ch = Changes(part="item", kind=kinds[0] if kinds else "change", targets=[item_id],
                         floors={f for f in (it.floor_id, new.floor_id) if f},
                         before={"item": _dump(it)}, after={"item": _dump(new)})
            if not _same(new, it):
                ch.items[item_id] = new
            return ch, new

        return self._item(self._change(fn, by), cat)

    def _door(self, ws: Workspace, r: ObjectRecord, resized=()) -> dict:
        eff = ws.effective(r)
        middle = _middle(r)
        return {"id": r.id, "type": r.type, "point": r.geometry["coordinates"], "connects": r.connects,
                "span": r.span, "width": r.width, "swings": r.swings, "tag": r.tag,
                "sill": r.sill, "height": r.height, "drawn": (r.type_source or "").startswith("drawn"),
                "middle": [round(middle.x, 4), round(middle.y, 4)],
                # the size given in review to one of the drawing's (null: as drawn)
                "resize": next((x.model_dump(exclude={"at"}) for x in resized
                                if Point(x.at).distance(middle) <= REMOVE_REACH_M), None),
                "ignored": eff["ignored"],
                "divider": self._divider_of(ws, r)}

    def edit(self, floor_id: str, body: dict, by=None) -> None:
        """Add or remove what a person drew on a floor: a wall, a line dividing a space
        (no wall: its zones), a door, a window or an opening (local meters). Removing
        takes the one of its kind drawn as the page shows it (_remove)."""

        def fn(ws: Workspace):
            was = self._floor(ws, floor_id)
            f = was.model_copy(update={"edits": was.edits.model_copy(deep=True)})  # its edits changed alone
            add = body.get("add") if isinstance(body.get("add"), dict) else {}
            kind = "draw"
            if "wall" in add or "divider" in add:
                what = "wall" if "wall" in add else "divider"
                line = _points(add[what], 2)
                if LineString(line).length < 0.1:
                    raise ValueError(f"a {what} is longer than 10 cm")
                (f.edits.walls if what == "wall" else f.edits.dividers).append(line)
            elif isinstance(body.get("add"), dict) and "opening" in body["add"]:
                o = body["add"]["opening"]
                if not isinstance(o, dict) or o.get("type") not in ("door", "window", "opening"):
                    raise ValueError('an opening is {"type": "door", "window" or "opening", "span": [[x, y], [x, y]]}')
                span = _points(o.get("span"), 2)
                if not 0.3 <= LineString(span).length <= 6:
                    raise ValueError("an opening is 0.3 to 6 m wide")
                f.edits.openings.append(DrawnOpening(type=o["type"], span=span))
            elif isinstance(body.get("add"), dict) and "space" in body["add"]:
                f.edits.spaces.append(_ring(body["add"]["space"]))
            elif isinstance(body.get("resize"), dict) and "at" in body["resize"]:
                self._resize(ws, floor_id, f, body["resize"])
                kind = "resize"
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
            return ch, None

        self._change(fn, by)

    def _resize(self, ws: Workspace, floor_id: str, f, body: dict) -> None:
        """A door, window or opening given another width, sill or height (metres; null:
        as drawn). One drawn in review changes; one of the drawing keeps its new size
        in the floor's edits, found again by its middle at every conversion."""
        at = Point(_points([body["at"]], 1)[0])
        sizes = {key: _size(body.get(key), key, band) for key, band in RESIZE_BANDS.items()}
        drawn = [(LineString(o.span).distance(at), i) for i, o in enumerate(f.edits.openings)]
        near = [d for d in drawn if d[0] <= REMOVE_REACH_M]
        if near:
            o = f.edits.openings[min(near)[1]]
            if sizes["width"] is not None:
                o.span = _resized_span(o.span, sizes["width"])
            o.sill, o.height = sizes["sill"], sizes["height"]
            return
        openings = [(_middle(r).distance(at), r) for r in ws.floor_objects(floor_id) if r.kind == "opening"]
        found = [o for o in openings if o[0] <= REMOVE_REACH_M]
        if not found:
            raise NotFound("no door, window or opening there")
        middle = _middle(min(found, key=lambda o: o[0])[1])
        f.edits.resized = [x for x in f.edits.resized if Point(x.at).distance(middle) > REMOVE_REACH_M]
        if any(v is not None for v in sizes.values()):
            f.edits.resized.append(ResizedOpening(at=[round(middle.x, 4), round(middle.y, 4)], **sizes))

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
            "correction": o.model_dump(exclude_none=True, exclude={"hidden", "ignored", "capacity", "stack"})
            if o and eff["corrected"] else None,
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

    def correct(self, object_id: str, body: dict, by=None) -> dict:
        """Change a space's correction:

        ``{"correction": {type, name, number}}`` replaces type, name and number: fields
        left out (or null) use what was detected, "" removes a detected name or number,
        {} accepts it as it is. ``{"hidden": bool}`` and ``{"ignored": bool}`` set the
        flags. ``{"capacity": n}`` sets how many people it is meant to seat (null: as its
        desks say). ``{"stack": id}`` links a lift, stairs, escalator or ramp to one on
        another floor of its building (stacks.py), ``""`` to none, null: as found.
        ``{"reset": true}`` removes the correction (the flags, capacity and link stay)."""

        def fn(ws: Workspace):
            r = ws.objects.get(object_id)
            if r is None or r.status != "active" or r.kind not in ("space", "zone", "opening"):
                raise NotFound(f"no active space, zone or opening {object_id}")
            if r.kind == "opening" and set(body) - {"ignored"}:
                raise ValueError("an opening can only be deleted or restored")
            current = ws.overrides.get(object_id) or Override()
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
            elif any(f in body for f in flags) or "capacity" in body or "stack" in body:
                values = current.model_dump(exclude_none=True, exclude={"hidden", "ignored", "capacity", "stack"})
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
            override = Override(**values, **flags, capacity=capacity, stack=stack)
            if checked and not values and (capacity is not None or stack is not None) and override.hidden is None:
                override.hidden = False  # accepted as it is, with a capacity or a link: the mark of the check
            new = None if override == Override() and not checked else override
            had = ws.overrides.get(object_id)
            kind = "reset" if body.get("reset") else "correct" if "correction" in body else \
                ("delete" if body["ignored"] else "restore") if "ignored" in body else \
                ("hide" if body["hidden"] else "show") if "hidden" in body else \
                ("link" if stack else "unlink" if stack == NOT_LINKED else "as found") if "stack" in body else "capacity"
            ch = Changes(part="object", kind=kind, targets=[object_id], floors={floor_of(object_id)},
                         before={"override": _dump(had)}, after={"override": _dump(new)})
            if not (had is None and new is None) and not _same(had, new):
                ch.overrides[object_id] = new
            return ch, None

        self._change(fn, by)
        ws = self.workspace()
        r = ws.objects[object_id]
        return self._door(ws, r, self._floor(ws, floor_of(object_id)).edits.resized) if r.kind == "opening" \
            else self._space(ws, r)

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


def _middle(r: ObjectRecord) -> Point:
    """An opening's middle: of its span when known, else where it is."""
    if r.span and len(r.span) >= 2:
        return LineString(r.span).interpolate(0.5, normalized=True)
    return Point(r.geometry["coordinates"])


def _round_box(box) -> list[float]:
    return [round(v, 3) for v in box]


def _print_of(review: "Review", floor_id: str) -> tuple[Path, Path]:
    """Where a floor's print and its placement are kept, drawing them first when the
    drawing, the part of it read or the floor's spaces changed."""
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
    raw = json.dumps([PRINT_VERSION, *stamp, src.units, src.region, src.offset, extent])
    key = hashlib.sha1(raw.encode()).hexdigest()[:16]
    folder = review.source.prints()
    png, info = folder / f"{floor_id}-{key}.png", folder / f"{floor_id}-{key}.json"
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
        for old in folder.glob(f"{floor_id}-*"):  # an earlier print of this floor
            old.unlink(missing_ok=True)
        part = png.with_suffix(".part")
        part.write_bytes(print_png(doc, drawing_box, width, height))
        part.replace(png)
        info.write_text(json.dumps({"bounds": _round_box(local), "width": width, "height": height,
                                    "px_per_m": round(per_m, 2), "key": key}))
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


def floor_print(review: "Review", floor_id: str) -> dict:
    """A floor's drawing as printed: where it lies (local meters) and its size."""
    return json.loads(_print_of(review, floor_id)[1].read_text())


def floor_print_png(review: "Review", floor_id: str) -> File:
    return File(_print_of(review, floor_id)[0].read_bytes(), "image/png")


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
