"""The review editor's operations on one project: a floor's spaces over its
drawing, and corrections to them.

Corrections are written to the workspace file straight away, as ``storeypath fix``
does, and the file is re-read whenever it changes on disk, so the editor and the
command line can be used side by side. The web server is in server.py:

    GET  /api/projects/<code>/review              project, floors, space types
    GET  /api/projects/<code>/floors/<id>         a floor's spaces and doors, local meters
    GET  /api/projects/<code>/floors/<id>/drawing the floor's source drawing, as linework
    POST /api/projects/<code>/floors/<id>/convert re-read the drawing (keeps IDs): a job
    POST /api/projects/<code>/objects/<id>        {"correction": {type, name, number}} or {"reset": true}
"""

from __future__ import annotations

import threading
from pathlib import Path

from shapely.geometry import LineString, Point, shape

from .cad import DrawingError, meters_per_unit, read_drawing
from .export import _label_point
from .extract import CURVE_TOLERANCE_M, _center, _flatten, _text_lines, _walk, modelspace_entities
from .profile import Profile, load_profile, resolve_profile
from .types import SpaceType
from .workspace import ObjectRecord, Override, Workspace

CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
                 ".svg": "image/svg+xml",
                 ".json": "application/json", ".png": "image/png", ".woff2": "font/woff2",
                 ".storeypath": "application/zip"}
CORRECTABLE = ("type", "name", "number")


class NotFound(Exception):
    pass


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
                spaces = [r for r in ws.floor_objects(fid) if r.kind == "space"]
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
            return {
                "id": floor_id, "name": f.name, "ordinal": f.ordinal,
                "source": Path(f.source.path).name if f.source else None,
                "converted_at": f.converted_at.isoformat() if f.converted_at else None,
                "method": f.method, "warnings": f.warnings, "outline": f.outline,
                "spaces": [self._space(ws, r) for r in objects if r.kind == "space"],
                "doors": [{"id": r.id, "type": r.type, "point": r.geometry["coordinates"], "connects": r.connects,
                           "span": r.span, "width": r.width}
                          for r in objects if r.kind == "opening"],
            }

    def _space(self, ws: Workspace, r: ObjectRecord) -> dict:
        eff = ws.effective(r)
        o = ws.overrides.get(r.id)
        geom = shape(r.geometry)
        x, y = _label_point(geom)
        width, height = _room_at(geom, x, y)
        return {
            "id": r.id, "type": eff["type"], "name": eff["name"], "number": eff["number"],
            "detected": {"type": r.type, "name": r.name, "number": r.number, "source": r.type_source},
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
            if r is None or r.status != "active" or r.kind != "space":
                raise NotFound(f"no active space {object_id}")
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
            return self._space(ws, r)

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
