"""A floor's doors, windows and openings as review changes them: at once, with no read.

A read finds a floor's rooms first, from its drawing, the walls and dividing lines drawn
on it in review and the spaces drawn there, and only then puts in what review did to its
openings (convert.read_floor): the doors, windows and openings drawn, their gaps cut in
the walls, each joined to the spaces beside it (drawn_openings); the drawing's own given
another size, type, leaf or place along their wall (resized_openings). No opening review
adds, sizes, retypes, turns or moves changes a room. So what a read found up to there
(Base: the spaces, the drawing's openings and the ways through Studio made, the walls with
their gaps) is kept with the floor, in memory and in Studio's cache (for Studio started
again), and whenever review changes the floor's openings they are worked out again from
it (Base.worked) by the very code the read puts them in with, and matched to the floor's
openings as a read matches them (match_doors, door_fields): the floor is then what reading
it again would make it (its openings, their IDs, the spaces each joins, the gaps in its
walls, the doors' leaves; and so its walking network), in the time a change takes
(at_once). A read applied after a change of openings it did not see works its openings out
again the same way, with the floor's openings as they are then (convert.apply_read).

A kept base serves while the floor is as that read left it (_Kept.why_not): the same
drawing (its content, the part read, its offset, units and layers' profile), the same kind
of level, the same walls, dividing lines and spaces drawn, its spaces as that read
registered them, all read by this reading code (convert.READ_CODE). Otherwise the floor is
read again, as before, and the job says why. A floor with none that fits (read by an older
Studio) gets one when a person who may edit it opens it: it is read as it is in the
background, applied to a copy of the project alone (server.Studio.warm), and what that read
keeps fits the floor when it finds the rooms the floor has.

A location's layout (format 0.12) is kept the same way (LandBase): its gates are found
from its site plan after its areas, which no gate changes (siteplan.edited_gates), and
matched by where they are (match_gates).

The walls are changed piece by piece (WallParts): an opening's gap is cut, or filled back,
in the parts of the walls near it alone, so a floor of a thousand rooms takes about as
long as a small one.
"""

from __future__ import annotations

import copy
import json
import math
import pickle
import threading
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import shapely
from shapely import STRtree
from shapely.affinity import translate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping

from .extract import ExtractedDoor, _connect_doors
from .geometry import as_polygons, leaves_of_width, span_of_width
from .ids import child_id, format_object_code, parse_id

DOOR_MATCH_DISTANCE = 0.5  # m: an opening this near one read before is that one…
SAME_OPENING_M = 0.05  # …and this near, that one whatever kind it is read as
RESIZE_REACH_M = 0.5  # an opening given another size in review is found this near its middle
RETYPED = {"door": "door", "window": "window", "opening": "doorway"}  # an opening given another type: its source
GATE_MATCH_M = 1.0  # a gate read this near one read before is that one
LISTS = ("openings", "resized")  # the lists of a floor's edits changed at once, with no read
BASES_KEPT = 12  # floors whose base is kept in memory (a floor of a thousand rooms: some 10 MB); the rest on disk
NO_BASE = ("no read of this floor is kept to change its openings on at once (it was read by an older Studio, "
           "or its read was not kept)")


def opening_type(source: str) -> str:
    """What an opening found so (its source) is: a door, a window or an opening."""
    return {"door": "door", "glazing": "door", "assumed": "door", "window": "window",
            "drawn door": "door", "drawn window": "window", "drawn opening": "opening"}.get(source, "opening")


# ---- the walls, part by part --------------------------------------------------------------

class WallParts:
    """A floor's walls as their separate parts (polygons), changed where an opening is: its
    gap cut (cut), what it no longer takes put back (fill), what lies near it (within), each
    in the parts near it alone. Parts keep their order; a part changed is where it was.
    ``walls`` None: the floor has no walls read, and nothing changes them."""

    def __init__(self, walls):
        self.none = walls is None
        self.parts: list[Polygon] = [] if walls is None else list(walls) if isinstance(walls, list) else as_polygons(walls)
        self._tree: STRtree | None = None

    def _near(self, g) -> list[int]:
        if not self.parts or g is None or g.is_empty:
            return []
        if self._tree is None:
            self._tree = STRtree(self.parts)
        return sorted(int(i) for i in self._tree.query(g, predicate="intersects"))

    def _put(self, gone: list[int], new: list[Polygon]) -> None:
        """The parts ``gone`` (indexes) replaced by ``new``, where the first of them was."""
        if not gone and not new:
            return
        at, drop, out = (gone[0] if gone else len(self.parts)), set(gone), []
        for i, p in enumerate(self.parts):
            if i == at:
                out.extend(new)
            if i not in drop:
                out.append(p)
        if at == len(self.parts):
            out.extend(new)
        self.parts, self._tree = out, None

    def cut(self, g) -> None:
        """A gap: ``g`` taken out of the walls."""
        if self.none:
            return
        near = self._near(g)
        if near:
            self._put(near, [q for i in near for q in as_polygons(self.parts[i].difference(g))])

    def fill(self, g) -> None:
        """Wall again: ``g`` put into the walls, one with the parts it meets."""
        if self.none or g is None or g.is_empty:
            return
        near = self._near(g)
        self._put(near, as_polygons(shapely.union_all([g, *(self.parts[i] for i in near)])))

    def within(self, g):
        """What of the walls lies in ``g``."""
        pieces = [self.parts[i].intersection(g) for i in self._near(g)]
        return shapely.union_all(pieces) if pieces else Polygon()

    def geometry(self):
        """The walls as one geometry (None: none read)."""
        if self.none:
            return None
        return self.parts[0] if len(self.parts) == 1 else MultiPolygon(self.parts)


WALL_GRID_M = 1e-4  # a floor's walls are kept to 0.1 mm, as every shape is (convert._local)


def walls_as_kept(walls, rounded: dict | None = None) -> dict | None:
    """A floor's walls as it keeps them (``walls``: a geometry, or its parts): GeoJSON in
    local metres to 0.1 mm, each part rounded on its own (``rounded``: what each part was
    rounded to, by part, kept: a floor's parts change a few at a time); all of them as one
    where parts rounded so would overlap (convert._local). A read keeps a floor's walls so,
    and so does a change of its openings at once (at_once): the same parts, the same walls."""
    from .convert import _local

    if walls is None:
        return None
    parts = walls if isinstance(walls, list) else as_polygons(walls)
    polys, coords = [], []
    for p in parts:
        hit = rounded.get(id(p)) if rounded is not None else None
        if hit is None or hit[0] is not p:
            try:
                r = as_polygons(shapely.set_precision(p, WALL_GRID_M))
            except shapely.errors.GEOSException:
                return _local(MultiPolygon(parts))
            hit = (p, r, [mapping(q)["coordinates"] for q in r])
            if rounded is not None:
                rounded[id(p)] = hit
        polys.extend(hit[1])
        coords.extend(hit[2])
    if len(polys) > 1 and not MultiPolygon(polys).is_valid:  # (rounded onto each other)
        return _local(MultiPolygon(parts))
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": coords[0]}
    return {"type": "MultiPolygon", "coordinates": coords}


class _Space:
    """A space as _connect_doors takes it: its shape alone."""

    __slots__ = ("polygon",)

    def __init__(self, polygon):
        self.polygon = polygon


class _Rooms:
    """The spaces' shapes, for what of them lies near a shape (an opening's gap)."""

    def __init__(self, polygons: list):
        self.polygons = polygons
        self._tree = STRtree(polygons) if polygons else None

    def near(self, g):
        if self._tree is None:
            return Polygon()
        return shapely.union_all([self.polygons[int(i)] for i in sorted(self._tree.query(g, predicate="intersects"))])


@dataclass
class _Work:
    """A floor's openings being worked out (Base.worked): its spaces, the openings so far
    (the base's shared until one is changed: door), its walls."""

    spaces: list  # _Space, in the read's order (doors join them by index)
    polygons: list
    doors: list  # ExtractedDoor
    walls: WallParts
    thickness: float
    owned: set = field(default_factory=set)  # the doors copied from the base, to be changed

    def door(self, k: int) -> ExtractedDoor:
        """Door ``k``, its own copy (the base's are shared by every change worked out on it)."""
        if k not in self.owned:
            d = self.doors[k]
            self.doors[k] = replace(d, connects=list(d.connects), swings=list(d.swings), issues=list(d.issues))
            self.owned.add(k)
        return self.doors[k]


# ---- the openings review drew or changed, as a read puts them in ------------------------------

def drawn_openings(o: _Work, drawn, reach: float) -> None:
    """The doors, windows and openings a person added in review: openings like any, joined
    to the spaces beside them, with their gap cut in the walls."""
    for d in drawn:
        span = LineString(d.span)
        if span.length < 0.2:
            continue
        gap = span.buffer(o.thickness, cap_style="flat")
        found = _connect_doors(o.spaces, [gap], reach, [], [(span, round(span.length, 3))])
        for x in found:
            x.source = f"drawn {d.type}"
            x.sill, x.height = d.sill, d.height
            if d.type == "door" and d.swing and len(d.swing) == 2:
                x.swings = [hung(x.span, d.swing)]
            o.owned.add(len(o.doors))
            o.doors.append(x)
        o.walls.cut(gap)


def _middles(doors: list) -> np.ndarray:
    """Each opening's middle (of its span when known, else where it is): x, y rows."""
    if not doors:
        return np.zeros((0, 2))
    lines = [d.span for d in doors]
    has = np.array([s is not None for s in lines])
    out = shapely.get_coordinates([d.point for d in doors]).astype(float)
    if has.any():
        out[has] = shapely.get_coordinates(
            shapely.line_interpolate_point([s for s in lines if s is not None], 0.5, normalized=True))
    return out


def resized_openings(o: _Work, resized, reach: float = 0.4) -> None:
    """The drawing's doors, windows and openings given another size or type, or moved, in
    review: each the opening nearest its middle, its gap in the walls made to fit (what it
    no longer takes is wall again, as thick as the wall on either side), a door's leaves
    with it; one given another type is that (a window read where a door is: a door); one
    moved, along its wall to where it was put (_moved_along)."""
    if not resized:
        return
    rooms = _Rooms(o.polygons)
    middles = _middles(o.doors)
    review = np.array([not d.source.startswith("drawn") for d in o.doors], dtype=bool)
    for rz in resized:
        if not review.any():
            break
        far = np.hypot(middles[:, 0] - rz.at[0], middles[:, 1] - rz.at[1])
        far[~review] = np.inf
        k = int(np.argmin(far))  # (the first of the nearest)
        if not far[k] <= RESIZE_REACH_M:
            continue
        d = o.door(k)
        if rz.type is not None and opening_type(d.source) != rz.type:  # read wrong: what it is
            d.source = RETYPED[rz.type]
            if rz.type != "door":
                d.swings = []  # (no leaf: a window, a way through)
        if rz.sill is not None:
            d.sill = rz.sill
        if rz.height is not None:
            d.height = rz.height
        if d.span is None or d.span.length < 0.05:
            if rz.width is not None:
                d.width = rz.width
            continue
        if rz.width is not None:
            old = [list(c) for c in d.span.coords]
            new = span_of_width(old, rz.width)
            d.swings = [tuple(map(tuple, leaf)) for leaf in leaves_of_width(d.swings, old, new)] if d.swings else []
            d.span, d.width = LineString(new), round(rz.width, 3)
            _regap(o, rooms, LineString(old), d.span)
        if rz.to is not None:
            _moved_along(o, rooms, d, rz.to, reach)
        if rz.width is not None or rz.to is not None:  # (where it is now, for the next one found by its middle)
            middles[k] = shapely.get_coordinates(d.span.interpolate(0.5, normalized=True))[0]
        if rz.swing is not None and opening_type(d.source) == "door":
            d.swings = [hung(d.span, rz.swing)]


def hung(span, swing) -> tuple:
    """A door's leaf as it was set (``swing``: [hinge, free edge]), on its span as it is
    now: hung on the jamb nearest where it was hung, opening to the same side of its wall,
    as long as the door is wide."""
    (x0, y0), (x1, y1) = span.coords[0], span.coords[-1]
    width = math.hypot(x1 - x0, y1 - y0) or 1.0
    (hx, hy), (fx, fy) = swing
    jamb = min(((x0, y0), (x1, y1)), key=lambda j: math.hypot(j[0] - hx, j[1] - hy))
    nx, ny = -(y1 - y0) / width, (x1 - x0) / width
    side = 1.0 if (fx - hx) * nx + (fy - hy) * ny >= 0 else -1.0
    return ((round(jamb[0], 4), round(jamb[1], 4)), (round(jamb[0] + nx * side * width, 4), round(jamb[1] + ny * side * width, 4)))


def _regap(o: _Work, rooms: _Rooms, old, new) -> None:
    """An opening's gap in the walls from ``old`` (its span) to ``new``: what it no longer
    takes is wall again, as thick as the wall on either side, and where it is now is open."""
    if o.walls.none:
        return
    fill = _gap_band(o.walls, old, o.thickness)
    if fill is not None:
        o.walls.fill(fill.difference(rooms.near(fill)))
    o.walls.cut(new.buffer(o.thickness, cap_style="flat"))


def _moved_along(o: _Work, rooms: _Rooms, d, to, reach: float) -> None:
    """An opening moved along its wall so that its middle is as near ``to`` as its wall
    lets: its span, point and leaves with it, its gap with it, joined to the spaces beside
    it where it is now."""
    (x0, y0), (x1, y1) = d.span.coords[0], d.span.coords[-1]
    ux, uy = (x1 - x0) / d.span.length, (y1 - y0) / d.span.length
    m = d.span.interpolate(0.5, normalized=True)
    t = (to[0] - m.x) * ux + (to[1] - m.y) * uy
    if abs(t) < 0.005:
        return
    dx, dy = ux * t, uy * t
    old = d.span
    d.span, d.point, d.footprint = translate(old, dx, dy), translate(d.point, dx, dy), translate(d.footprint, dx, dy)
    d.swings = [((h[0] + dx, h[1] + dy), (f[0] + dx, f[1] + dy)) for h, f in d.swings]
    _regap(o, rooms, old, d.span)
    found = _connect_doors(o.spaces, [d.span.buffer(o.thickness, cap_style="flat")], reach, [],
                           [(d.span, round(d.span.length, 3))])
    if found:
        d.connects = found[0].connects


def _gap_band(walls: WallParts, span, thickness: float):
    """The wall a gap interrupts, as if it did not: the hull of the wall's ends on either
    side of the span (None when there are none)."""
    (x0, y0), (x1, y1) = span.coords[0], span.coords[-1]
    ux, uy = (x1 - x0) / span.length, (y1 - y0) / span.length
    e = max(thickness, 0.3)
    longer = LineString([(x0 - ux * e, y0 - uy * e), (x1 + ux * e, y1 + uy * e)])
    ends = walls.within(longer.buffer(1.5 * thickness + 0.05, cap_style="flat"))
    return None if ends.is_empty else ends.convex_hull


# ---- matching openings to the floor's, as a read does ---------------------------------------

def match_doors(existing: list, doors: list) -> list[int | None]:
    """Which of the floor's openings (``existing``: their records) each opening found keeps
    the ID of, by position (an index into ``existing``, or None: a new one): windows only
    with windows, ways through (doors, doorways) only with ways through, unless it stands
    where it stood (the same opening, read as another kind: what was done to it stays);
    each the nearest not taken yet, in the order they were found."""
    if not existing or not doors:
        return [None] * len(doors)
    xy = np.array([r.geometry["coordinates"][:2] for r in existing], dtype=float)
    windows = [r.type == "window" for r in existing]
    tree = STRtree(shapely.points(xy))
    at = shapely.get_coordinates([d.point for d in doors])
    found, near = tree.query(shapely.points(at), predicate="dwithin", distance=DOOR_MATCH_DISTANCE)
    cands: dict[int, list[int]] = {}
    for i, j in sorted(zip(found.tolist(), near.tolist())):
        cands.setdefault(i, []).append(j)
    used: set[int] = set()
    out: list[int | None] = []
    for i, door in enumerate(doors):
        window = opening_type(door.source) == "window"
        best, best_d = None, DOOR_MATCH_DISTANCE
        px, py = at[i]
        for j in cands.get(i, ()):
            d = math.hypot(px - xy[j, 0], py - xy[j, 1])
            alike = windows[j] == window or d <= SAME_OPENING_M
            if j not in used and alike and d <= best_d:
                best, best_d = j, d
        if best is not None:
            used.add(best)
        out.append(best)
    return out


def door_fields(door: ExtractedDoor, space_ids: list[str]) -> dict:
    """What an opening's record says of it, as found: its kind and how it was found, its tag,
    where it is, the spaces it joins (``space_ids``: the spaces' IDs, in the read's order), its
    span, width, leaves, sill and height (local metres, to 0.1 mm)."""
    r4 = lambda pts: [[round(x, 4), round(y, 4)] for x, y in pts]  # noqa: E731
    return {"type": opening_type(door.source), "type_source": door.source, "tag": door.tag,
            "issues": list(door.issues),
            "geometry": {"type": "Point", "coordinates": [round(door.point.x, 4), round(door.point.y, 4)]},
            "connects": [space_ids[i] for i in door.connects],
            "span": r4(door.span.coords) if door.span is not None else None, "width": door.width,
            "swings": [r4(leaf) for leaf in door.swings] or None, "sill": door.sill, "height": door.height}


def match_gates(before: list, gates: list) -> list:
    """Which of a layout's gates (``before``: their records) each gate found keeps the ID of,
    by where it is (GATE_MATCH_M): the record, or None (a new one); each the nearest not
    taken yet, in the order they were found."""
    used: set[str] = set()
    out = []
    for g in gates:
        mid = g.span.interpolate(0.5, normalized=True)
        near = [(Point(r.geometry["coordinates"][:2]).distance(mid), r) for r in before if r.id not in used]
        near = [(d, r) for d, r in near if d <= GATE_MATCH_M]
        record = min(near, key=lambda x: x[0])[1] if near else None
        if record is not None:
            used.add(record.id)
        out.append(record)
    return out


def gate_fields(g, area_ids: list) -> dict:
    """What a gate's record says of it, as found (``area_ids``: the layout's areas' IDs, in the
    read's order)."""
    mid = g.span.interpolate(0.5, normalized=True)
    return {"type": "gate", "type_source": "drawn gate" if g.drawn else "drawing" if g.swings else "gap",
            "geometry": {"type": "Point", "coordinates": [round(mid.x, 4), round(mid.y, 4)]},
            "span": [[round(x, 4), round(y, 4)] for x, y in g.span.coords], "width": round(g.span.length, 3),
            "connects": [area_ids[i] for i in g.connects if 0 <= i < len(area_ids)],
            "issues": [] if g.swings or g.drawn else ["a gap in the boundary walls: no gate drawn in it"]}


def put_fields(record, fields: dict) -> None:
    for k, v in fields.items():
        setattr(record, k, v)


def _same_value(a, b) -> bool:
    """Two values the same as they are kept (a tuple and a list of the same points are)."""
    if a == b:
        return True
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same_value(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_value(a[k], b[k]) for k in a)
    return False


# ---- what a read found, kept --------------------------------------------------------------------

def drawing_key(src) -> str:
    """What a floor's plan is read from, as its source says (its content as last read)."""
    return json.dumps([src.sha256, src.region, src.offset, src.units, src.profile])


def drawn_key(edits) -> str:
    """What drawn on a floor parts its rooms: its walls, dividing lines and spaces drawn."""
    return json.dumps([edits.walls, edits.dividers, edits.spaces])


def edits_key(edits) -> str:
    """What review did to a floor's openings (its edits' openings and resized)."""
    d = edits.model_dump(mode="json", include=set(LISTS))
    return json.dumps([d["openings"], d["resized"]])


@dataclass
class _Kept:
    """What a base checks a floor against: the read's spaces as registered (IDs, shapes)."""

    floor_id: str
    drawing: str = ""  # drawing_key, once registered
    kind: str = ""  # the floor's type
    drawn: str = ""  # drawn_key
    ids: list[str] = field(default_factory=list)  # the spaces' (or a layout's areas') IDs, in the read's order
    shapes: list[str] = field(default_factory=list)  # their geometry as registered (JSON)
    rooms: str = "space"  # the kind of record they are

    def __post_init__(self):
        self._seen: dict[str, object] = {}  # space ID -> its geometry checked (the same object: the same)

    def __getstate__(self):
        state = dict(self.__dict__)
        state.pop("_seen", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._seen = {}

    def register(self, ws, floor_id: str, ids: list) -> None:
        """The floor as the read was applied to it (``ids``: its spaces' IDs, in the read's order)."""
        f = ws.floor(floor_id)
        self.drawing, self.kind, self.drawn = drawing_key(f.source), f.type.value, drawn_key(f.edits)
        self.ids = list(ids)
        self.shapes = [json.dumps(ws.objects[i].geometry) if i is not None and i in ws.objects else "" for i in self.ids]
        self._seen = {}

    def why_not(self, ws, floor_id: str) -> str | None:
        """Why the floor is not as the read left it (None: it is)."""
        if not self.ids and not self.shapes and not self.drawing:
            return NO_BASE
        f = ws.floor(floor_id)
        if f.source is None or drawing_key(f.source) != self.drawing:
            return "its drawing, or the part of it read, changed since it was last read"
        if f.type.value != self.kind:
            return "it was made another kind of level since it was last read"
        if drawn_key(f.edits) != self.drawn:
            return "walls, dividing lines or spaces were drawn on it since it was last read"
        have = [r for r in ws.floor_objects(floor_id) if r.kind == self.rooms]
        if len(have) != len([i for i in self.ids if i is not None]):
            return "its rooms changed since it was last read"
        for i, shape_ in zip(self.ids, self.shapes):
            if i is None:
                continue
            r = ws.objects.get(i)
            if r is None or r.status != "active" or r.kind != self.rooms:
                return "its rooms changed since it was last read"
            if self._seen.get(i) is r.geometry:
                continue
            if json.dumps(r.geometry) != shape_:
                return "its rooms changed since it was last read"
            self._seen[i] = r.geometry
        return None


@dataclass
class Base(_Kept):
    """What a floor's read found before review's openings were put in (see the module): its
    spaces' shapes (in the read's order), the drawing's openings and the ways through Studio
    made, its walls with their gaps, how thick they are, how far an opening reaches a space."""

    polygons: list = field(default_factory=list)
    doors: list = field(default_factory=list)  # ExtractedDoor, never changed (each change copies what it changes)
    walls: object = None
    thickness: float = 0.2
    reach: float = 0.4

    def __post_init__(self):
        super().__post_init__()
        self._worked_out()

    def _worked_out(self) -> None:
        """What is worked out of it once, for every change worked out on it (not kept on disk)."""
        self.parts = None if self.walls is None else as_polygons(self.walls)  # (the same parts every time)
        self.rounded: dict = {}  # the walls' parts as rounded (walls_as_kept), by part
        self._fields: dict = {}  # its own openings' door_fields, by opening
        self._own = {id(d): d for d in self.doors}

    def __getstate__(self):
        state = super().__getstate__()
        for k in ("parts", "rounded", "_fields", "_own"):
            state.pop(k, None)
        return state

    def __setstate__(self, state):
        super().__setstate__(state)
        self._worked_out()

    def register(self, ws, floor_id: str, ids: list) -> None:
        super().register(ws, floor_id, ids)
        self._fields = {}

    def door_fields(self, door) -> dict:
        """door_fields of an opening (its spaces' IDs as registered): of one of its own
        (unchanged), worked out once."""
        if self._own.get(id(door)) is not door:
            return door_fields(door, self.ids)
        hit = self._fields.get(id(door))
        if hit is None:
            hit = self._fields[id(door)] = door_fields(door, self.ids)
        return hit

    @classmethod
    def of(cls, floor_id: str, ex, reach: float) -> "Base":
        """The base of a floor's extraction, just before review's openings are put in it."""
        return cls(floor_id=floor_id, polygons=[s.polygon for s in ex.spaces],
                   doors=[replace(d, connects=list(d.connects), swings=list(d.swings), issues=list(d.issues))
                          for d in ex.doors],
                   walls=ex.walls, thickness=max(ex.wall_thickness or 0.2, 0.1), reach=reach)

    def worked(self, edits) -> tuple[list, WallParts]:
        """The floor's openings and walls with what review did to them (``edits``: the
        floor's), as a read puts it in: (the openings, the walls)."""
        o = _Work([_Space(p) for p in self.polygons], self.polygons, list(self.doors), WallParts(self.parts),
                  self.thickness)
        drawn_openings(o, edits.openings, self.reach)
        resized_openings(o, edits.resized, self.reach)
        return o.doors, o.walls


@dataclass
class LandBase(_Kept):
    """What a layout's read found before review's gates were put in: its gates in the
    drawing's metres, its areas (in the read's order), and how far the layout's frame is
    from the drawing's (dx, dy)."""

    gates: list = field(default_factory=list)  # siteplan.FoundGate, never changed
    areas: list = field(default_factory=list)  # polygons, drawing metres
    dx: float = 0.0
    dy: float = 0.0

    def worked(self, edits) -> list:
        """The layout's gates with what review did to them, in the layout's frame."""
        from .siteplan import edited_gates

        gates = edited_gates(self.gates, self.areas, edits, self.dx, self.dy)
        if self.dx or self.dy:
            gates = [replace(g, span=translate(g.span, -self.dx, -self.dy)) for g in gates]
        return gates


_kept: OrderedDict[str, _Kept] = OrderedDict()
_lock = threading.Lock()


def _file(folder, floor_id: str) -> Path:
    return Path(folder) / f"{floor_id}.openings"


def keep(base: _Kept, folder: str | Path | None = None) -> None:
    """A floor's base kept in memory and, written in the background, in ``folder`` (Studio's
    cache of reads), with the reading code that made it."""
    from . import convert

    with _lock:
        _kept[base.floor_id] = base
        _kept.move_to_end(base.floor_id)
        while len(_kept) > BASES_KEPT:
            _kept.popitem(last=False)
    if folder is not None:
        threading.Thread(target=_write, args=(_file(folder, base.floor_id), base, convert.READ_CODE), daemon=True,
                         name=f"keeping {base.floor_id}'s openings").start()


def _write(path: Path, base: _Kept, code: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f"{path.name}.{threading.get_ident()}.part")
        part.write_bytes(pickle.dumps((code, base), protocol=pickle.HIGHEST_PROTOCOL))
        part.replace(path)
    except OSError:
        pass  # (not kept on disk: the floor is read again the first time after Studio starts again)


def kept(floor_id: str, folder: str | Path | None = None) -> _Kept | None:
    """A floor's base: in memory, else in ``folder`` (taken only when made by this reading code)."""
    from . import convert

    with _lock:
        base = _kept.get(floor_id)
        if base is not None:
            _kept.move_to_end(floor_id)
            return base
    if folder is None:
        return None
    try:
        code, base = pickle.loads(_file(folder, floor_id).read_bytes())
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError, TypeError):
        return None
    if code != convert.READ_CODE or not isinstance(base, _Kept) or base.floor_id != floor_id:
        return None
    with _lock:
        base = _kept.setdefault(floor_id, base)
        while len(_kept) > BASES_KEPT:
            _kept.popitem(last=False)
    return base


def ready(ws, floor_id: str, folder: str | Path | None = None) -> bool:
    """Whether a change of the floor's openings would be made at once now (a base kept, that fits it)."""
    base = kept(floor_id, folder)
    try:
        return base is not None and isinstance(base, LandBase) == ws.floor(floor_id).layout and \
            base.why_not(ws, floor_id) is None
    except (KeyError, ValueError):
        return False


def forget(floor_id: str | None = None) -> None:
    """What is kept in memory of a floor (of every floor) forgotten."""
    with _lock:
        if floor_id is None:
            _kept.clear()
        else:
            _kept.pop(floor_id, None)


# ---- a change of openings, at once ----------------------------------------------------------------

@dataclass
class AtOnce:
    """A floor's openings worked out at once (at_once): the records changed (new ones, those
    changed, those retired: by ID), the floor's walls as a read keeps them (None: the walls
    are not changed: a layout's), the building's next object number when new IDs were given."""

    objects: dict
    walls: dict | None
    walls_changed: bool
    next_object_seq: int | None


def _ids(ws, floor_id: str):
    """New object IDs on a floor, as a read gives them (convert._allocate_id), without
    changing ``ws``: (take one, the building's next number after those taken)."""
    b = ws.building(parse_id(floor_id).prefix("building"))
    seq = b.next_object_seq

    def take() -> str:
        nonlocal seq
        while True:
            candidate = child_id(floor_id, format_object_code(seq))
            seq += 1
            if candidate not in ws.objects:
                return candidate

    return take, lambda: seq, seq


def at_once(ws, floor_id: str, folder: str | Path | None = None) -> AtOnce | str:
    """The floor's openings as reading it again would make them, worked out from its kept
    base and its edits as they are in ``ws`` (never changed), or why that cannot be (a
    read is needed)."""
    from .workspace import ObjectRecord, utcnow

    f = ws.floor(floor_id)
    base = kept(floor_id, folder)
    if base is None:
        return NO_BASE
    if isinstance(base, LandBase) != f.layout:
        return NO_BASE
    why = base.why_not(ws, floor_id)
    if why is not None:
        return why
    now = utcnow()
    take, seq, first = _ids(ws, floor_id)
    existing = [r for r in ws.floor_objects(floor_id) if r.kind == "opening"]
    out: dict = {}
    if f.layout:
        found = base.worked(f.edits)
        matched = match_gates(existing, found)
        fields = [gate_fields(g, base.ids) for g in found]
        kind = "gate"
        walls, walls_changed = None, False
    else:
        found, parts = base.worked(f.edits)
        index = match_doors(existing, found)
        matched = [existing[j] if j is not None else None for j in index]
        fields = [base.door_fields(d) for d in found]
        kind = None
        walls, walls_changed = walls_as_kept(None if parts.none else parts.parts, base.rounded), True
    used = set()
    for record, values in zip(matched, fields):
        if record is None:
            new = ObjectRecord(id=take(), kind="opening", type=kind or values["type"], geometry={})
            put_fields(new, copy.deepcopy(values))
            out[new.id] = new
            continue
        used.add(record.id)
        if not all(_same_value(getattr(record, k), v) for k, v in values.items()):
            changed = record.model_copy()
            put_fields(changed, copy.deepcopy(values))
            out[record.id] = changed
    for r in existing:
        if r.id not in used:
            gone = r.model_copy()
            gone.status, gone.retired_at = "retired", now
            out[r.id] = gone
    return AtOnce(out, walls, walls_changed, seq() if seq() != first else None)
