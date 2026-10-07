"""The review editor's operations on one project: a floor's spaces over its
drawing, and corrections to them.

Corrections are written to the workspace file straight away, as ``storeypath fix``
does, and the file is re-read whenever it changes on disk, so the editor and the
command line can be used side by side. The web server is in server.py:

    GET  /api/projects/<code>/review              project, floors, space types
    GET  /api/projects/<code>/floors/<id>         a floor's spaces and doors, local meters
    GET  /api/projects/<code>/floors/<id>/drawing the floor's source drawing, as linework
    GET  /api/projects/<code>/floors/<id>/print   the drawing as printed: where it lies, its size
    GET  /api/projects/<code>/floors/<id>/print.png  the print (drawn once, kept until the drawing changes)
    POST /api/projects/<code>/floors/<id>/convert re-read the drawing (keeps IDs): a job
    POST /api/projects/<code>/objects/<id>        {"correction": {type, name, number}} or {"reset": true};
                                                  {"ignored": bool} deletes a space or an opening (or restores it)
    POST /api/projects/<code>/floors/<id>/edits   {"add": {"wall": [[x, y], [x, y]]}}, {"add": {"divider": …}},
                                                  {"add": {"opening": {"type", "span"}}} or {"remove": {"at": [x, y]}}:
                                                  what a person draws, kept through every conversion; then
                                                  the floor is read again (a job)
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import NamedTuple

from shapely.geometry import LineString, Point, shape

from .cad import DrawingError, meters_per_unit, read_drawing
from .export import _label_point
from .extract import CURVE_TOLERANCE_M, _center, _flatten, _text_lines, _walk, modelspace_entities
from .profile import Profile, load_profile, resolve_profile
from .types import SpaceType
from .workspace import DrawnOpening, ObjectRecord, Override, ResizedOpening, Workspace

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


class File(NamedTuple):
    """A file the server sends as it is."""

    data: bytes
    content_type: str


class NotFound(Exception):
    pass



def _divider(opening: ObjectRecord, areas: dict) -> list[list[list[float]]] | None:
    """Where Studio divided an open area (no wall drawn): the edge the two spaces
    it joins share, as lines of points."""
    if len(opening.connects) != 2 or not all(c in areas for c in opening.connects):
        return None
    a, b = (areas[c] for c in opening.connects)
    edge = a.boundary.intersection(b.buffer(0.02))
    lines = [g for g in getattr(edge, "geoms", [edge]) if isinstance(g, LineString) and g.length >= 0.05]
    return [[[round(x, 3), round(y, 3)] for x, y in g.coords] for g in lines] or None

class Review:
    """The editor's operations on one workspace file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._ws: Workspace | None = None
        self._mtime: int | None = None
        self._drawings: dict[tuple, dict] = {}

    def _load(self) -> Workspace:
        mtime = self.path.stat().st_mtime_ns
        if self._ws is None or mtime != self._mtime:
            self._ws, self._mtime = Workspace.load(self.path), mtime
        return self._ws

    def _save(self, ws: Workspace) -> None:
        ws.save(self.path)
        self._mtime = self.path.stat().st_mtime_ns

    def _floor(self, ws: Workspace, floor_id: str):
        try:
            return ws.floor(floor_id)
        except (KeyError, ValueError):
            raise NotFound(f"no floor {floor_id}") from None

    def project(self) -> dict:
        with self._lock:
            ws = self._load()
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
                "file": self.path.name,
                "floors": floors,
                "types": [t.value for t in SpaceType],
            }

    def floor(self, floor_id: str) -> dict:
        with self._lock:
            ws = self._load()
            f = self._floor(ws, floor_id)
            objects = sorted(ws.floor_objects(floor_id), key=lambda r: r.id)
            areas = {r.id: shape(r.geometry) for r in objects if r.kind == "space"}
            return {
                "id": floor_id, "name": f.name, "ordinal": f.ordinal,
                "source": Path(f.source.path).name if f.source else None,
                "converted_at": f.converted_at.isoformat() if f.converted_at else None,
                "method": f.method, "warnings": f.warnings, "outline": f.outline,
                # spaces and their zones; a space divided into zones is used through them
                "spaces": [self._space(ws, r) for r in objects if r.kind in ("space", "zone")],
                "doors": [self._door(ws, r, areas, f.edits.resized) for r in objects if r.kind == "opening"],
                # for drawing walls and doors onto: the walls as found, and what was drawn
                "walls": f.walls, "wall_thickness": f.wall_thickness, "edits": f.edits.model_dump(),
            }

    def _door(self, ws: Workspace, r: ObjectRecord, areas: dict | None = None, resized=()) -> dict:
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
                "divider": _divider(r, areas or {}) if r.type_source in ("split", "doorway") else None}

    def edit(self, floor_id: str, body: dict) -> None:
        """Add or remove what a person drew on a floor: a wall, a line dividing a space
        (no wall: its zones), a door, a window or an opening (local meters). Removing
        takes what was drawn nearest a point."""
        with self._lock:
            ws = self._load()
            f = self._floor(ws, floor_id)
            add = body.get("add") if isinstance(body.get("add"), dict) else {}
            if "wall" in add or "divider" in add:
                kind = "wall" if "wall" in add else "divider"
                line = _points(add[kind], 2)
                if LineString(line).length < 0.1:
                    raise ValueError(f"a {kind} is longer than 10 cm")
                (f.edits.walls if kind == "wall" else f.edits.dividers).append(line)
            elif isinstance(body.get("add"), dict) and "opening" in body["add"]:
                o = body["add"]["opening"]
                if not isinstance(o, dict) or o.get("type") not in ("door", "window", "opening"):
                    raise ValueError('an opening is {"type": "door", "window" or "opening", "span": [[x, y], [x, y]]}')
                span = _points(o.get("span"), 2)
                if not 0.3 <= LineString(span).length <= 6:
                    raise ValueError("an opening is 0.3 to 6 m wide")
                f.edits.openings.append(DrawnOpening(type=o["type"], span=span))
            elif isinstance(body.get("resize"), dict) and "at" in body["resize"]:
                self._resize(ws, floor_id, f, body["resize"])
            elif isinstance(body.get("remove"), dict) and "at" in body["remove"]:
                at = Point(_points([body["remove"]["at"]], 1)[0])
                lists = {"wall": f.edits.walls, "divider": f.edits.dividers, "opening": f.edits.openings}
                drawn = [(LineString(x.span if kind == "opening" else x).distance(at), kind, i)
                         for kind, items in lists.items() for i, x in enumerate(items)]
                near = [d for d in drawn if d[0] <= REMOVE_REACH_M]
                if not near:
                    raise NotFound("nothing drawn there")
                _, kind, i = min(near)
                lists[kind].pop(i)
            else:
                raise ValueError('send {"add": {"wall" or "divider": …}}, {"add": {"opening": …}}, '
                                 '{"resize": {"at": [x, y], "width", "sill", "height"}} or {"remove": {"at": [x, y]}}')
            self._save(ws)

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

    def _space(self, ws: Workspace, r: ObjectRecord) -> dict:
        eff = ws.effective(r)
        o = ws.overrides.get(r.id)
        geom = shape(r.geometry)
        x, y = _label_point(geom)
        width, height = _room_at(geom, x, y)
        return {
            "id": r.id, "kind": r.kind, "space_id": r.parent, "zones": list(r.zones),
            "type": eff["type"], "name": eff["name"], "number": eff["number"],
            "detected": {"type": r.type, "name": r.name, "number": r.number, "source": r.type_source},
            "drawing_label": r.label,
            "correction": o.model_dump(exclude_none=True, exclude={"hidden", "ignored"}) if o else None,
            "hidden": eff["hidden"], "ignored": eff["ignored"],
            "reasons": ws.review_reasons(r),
            "area": round(geom.area, 2),
            "label_point": [round(x, 3), round(y, 3)],
            "label_room": [round(width, 2), round(height, 2)],
            "geometry": r.geometry,
        }

    def correct(self, object_id: str, body: dict) -> dict:
        """Change a space's correction:

        ``{"correction": {type, name, number}}`` replaces type, name and number: fields
        left out (or null) use what was detected, "" removes a detected name or number,
        {} accepts it as it is. ``{"hidden": bool}`` and ``{"ignored": bool}`` set the
        flags. ``{"reset": true}`` removes the correction (the flags stay)."""
        with self._lock:
            ws = self._load()
            r = ws.objects.get(object_id)
            if r is None or r.status != "active" or r.kind not in ("space", "zone", "opening"):
                raise NotFound(f"no active space, zone or opening {object_id}")
            if r.kind == "opening" and set(body) - {"ignored"}:
                raise ValueError("an opening can only be deleted or restored")
            current = ws.overrides.get(object_id) or Override()
            flags = {"hidden": current.hidden, "ignored": current.ignored}
            for flag in ("hidden", "ignored"):
                if flag in body:
                    if not isinstance(body[flag], bool):
                        raise ValueError(f"{flag} is true or false")
                    flags[flag] = body[flag] or None
            if body.get("reset"):
                values = {}
            elif "correction" in body:
                c = body["correction"]
                if not isinstance(c, dict) or set(c) - set(CORRECTABLE):
                    raise ValueError(f"correction must be an object with any of: {', '.join(CORRECTABLE)}")
                values = {k: v.strip() if isinstance(v, str) else v for k, v in c.items() if v is not None}
                if "type" in values:
                    values["type"] = SpaceType(values["type"])
            elif any(f in body for f in flags):
                values = current.model_dump(exclude_none=True, exclude={"hidden", "ignored"})
            else:
                raise ValueError("nothing to change")
            override = Override(**values, **flags)
            # An empty correction means "accepted"; one left empty only by clearing a flag
            # (or by a reset) means nothing and is removed.
            accepted = object_id in ws.overrides and not (current.hidden or current.ignored)
            flag_only = "correction" not in body and not body.get("reset")
            if override == Override() and (body.get("reset") or (flag_only and not accepted)):
                ws.overrides.pop(object_id, None)
            else:
                ws.overrides[object_id] = override
            self._save(ws)
            return self._door(ws, r) if r.kind == "opening" else self._space(ws, r)

    def drawing(self, floor_id: str) -> dict:
        with self._lock:
            ws = self._load()
            f = self._floor(ws, floor_id)
        if f.source is None:
            return {"groups": {}, "texts": []}
        path = self.path.parent / f.source.path
        if not path.exists():
            raise DrawingError(f"drawing not found: {path}")
        src = f.source
        key = (floor_id, str(path), path.stat().st_mtime_ns, src.profile, src.units, src.region, src.offset)
        if key not in self._drawings:
            doc = read_drawing(path)
            profile = load_profile(resolve_profile(src.profile, self.path.parent))
            self._drawings[key] = drawing_linework(
                doc, profile, meters_per_unit(doc, src.units), src.region, src.offset
            )
        return self._drawings[key]


def _points(value, n: int) -> list[list[float]]:
    """``n`` points [x, y] (local meters) from a request."""
    try:
        pts = [[round(float(x), 4), round(float(y), 4)] for x, y in value]
    except (TypeError, ValueError):
        raise ValueError(f"expected {n} points [x, y]") from None
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
    with review._lock:
        ws = review._load()
        f = review._floor(ws, floor_id)
        if f.source is None:
            raise NotFound(f"floor {floor_id} has no drawing")
        src = f.source
        path = review.path.parent / src.path
        if not path.exists():
            raise DrawingError(f"drawing not found: {path}")
        extent = None
        if src.region is None:  # around what was found on the floor
            geoms = [shape(r.geometry) for r in ws.floor_objects(floor_id) if r.kind == "space"]
            if f.outline:
                geoms.append(shape(f.outline))
            if geoms:
                xs0, ys0, xs1, ys1 = zip(*(g.bounds for g in geoms))
                extent = _round_box((min(xs0) - PRINT_MARGIN_M, min(ys0) - PRINT_MARGIN_M,
                                     max(xs1) + PRINT_MARGIN_M, max(ys1) + PRINT_MARGIN_M))
    raw = json.dumps([PRINT_VERSION, str(path), path.stat().st_mtime_ns, src.units, src.region, src.offset, extent])
    key = hashlib.sha1(raw.encode()).hexdigest()[:16]
    folder = review.path.parent / CACHE_DIR / "prints"
    png, info = folder / f"{floor_id}-{key}.png", folder / f"{floor_id}-{key}.json"
    if png.exists() and info.exists():
        return png, info
    with _PRINTING:
        if png.exists() and info.exists():
            return png, info
        from ezdxf import bbox as ebbox

        from .vision import print_png

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
