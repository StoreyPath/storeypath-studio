"""What Studio decided in an area, and what people changed there: reading.json and
corrections.json of an area sample, in the sample's frame and with its own short IDs
(S1, Z1, D1, W1, O1, I1). The same is made of a replay's reading (tools.replay), so
the two compare one to one."""

from __future__ import annotations

import re

from shapely import wkt
from shapely.geometry import LineString, Point, Polygon, mapping, shape

from ..extract import _center, _text_lines, _walk
from ..geometry import iou
from ..reading import NOT_A_ROOM
from ..types import SpaceType
from .frame import Frame, rounded

PREFIX = {"space": "S", "zone": "Z", "door": "D", "window": "W", "opening": "O", "item": "I"}
EDGE_SHARE = 0.95  # a room with less than this share of it inside the area is at its edge


def decided_by(type_source: str | None, corrected: bool = False) -> str:
    """Who decided a type, in words: rules, the language model, the vision model, symbols
    (SymPoint-V2, research only), a person, or nobody (no type)."""
    if corrected:
        return "person"
    s = type_source or "default"
    if s == "default":
        return "nobody"
    if s == "model":
        return "language model"
    if s == "vision":
        return "vision model"
    if s.startswith("symbols:"):
        return "symbols"
    if s == "doors":
        return "rules, after the vision model"
    if s.startswith("drawn"):
        return "person (drawn in review)"
    if s == "package":
        return "a package"
    return "rules"


def model_name(name: str | None) -> str | None:
    """A model's name as a sample says it: a helper known only by its address (an
    organization's machine) is not named."""
    if name and ("://" in name or re.match(r"^[\w.-]+:\d+", name)):
        return "a vision model (its address left out)"
    return name


def opening_kind(r) -> str:
    return r.type if r.type in ("door", "window") else "opening"


class LocalIds:
    """Short IDs of the objects and items of a sample, numbered top to bottom, left to
    right: S1… spaces, Z1… zones, D1… doors, W1… windows, O1… openings, I1… items."""

    def __init__(self, records: list, items: list, frame: Frame):
        self.of: dict[str, str] = {}
        groups: dict[str, list] = {}
        for r in records:
            g = shape(r.geometry)
            p = g.representative_point() if r.kind != "opening" else _middle(r)
            kind = r.kind if r.kind != "opening" else opening_kind(r)
            groups.setdefault(kind, []).append((-round(p.y, 1), round(p.x, 1), r.id))
        for it in items:
            groups.setdefault("item", []).append((-round(it.y, 1), round(it.x, 1), it.id))
        for kind, rows in groups.items():
            for n, (_, _, real) in enumerate(sorted(rows), start=1):
                self.of[real] = f"{PREFIX[kind]}{n}"

    def __call__(self, real: str | None) -> str | None:
        if real is None:
            return None
        return self.of.get(real, "outside")


def _middle(r) -> Point:
    if r.span and len(r.span) >= 2:
        return LineString(r.span).interpolate(0.5, normalized=True)
    return Point(r.geometry["coordinates"])


def in_area(ws, floor_id: str, frame: Frame) -> tuple[list, list, list]:
    """The floor's spaces and zones, openings and items in the sample's extent."""
    rooms, openings = [], []
    for r in ws.floor_objects(floor_id):
        if not r.geometry:
            continue
        if r.kind == "opening":
            m = _middle(r)
            if frame.holds(m.x, m.y):
                openings.append(r)
        elif frame.touches(shape(r.geometry)):
            rooms.append(r)
    items = [it for it in ws.floor_items(floor_id) if frame.holds(it.x, it.y)]
    return rooms, openings, items


def parse_layer_line(line: str) -> dict:
    """A line of a floor's 'how it was read' (analyse.Analysis.summary): layer, roles, why."""
    m = re.match(r"^(?P<layer>.*?): (?P<roles>[^()]*?)(?: \((?P<why>.*)\))?$", line)
    if not m:
        return {"layer": line, "roles": [], "why": None}
    return {"layer": m["layer"], "roles": [r.strip() for r in m["roles"].split(",") if r.strip()], "why": m["why"]}


def texts_of(doc, scale: float) -> list[dict]:
    """Every text of the drawing's part (inside blocks too): its lines, where it is
    (metres, the sample's frame), layer, height and turn."""
    out = []
    for e, layer in _walk(doc.modelspace()):
        if e.dxftype() not in ("TEXT", "MTEXT", "ATTRIB"):
            continue
        lines, c = _text_lines(e), _center(e)
        if not lines or c is None:
            continue
        height = e.dxf.get("char_height" if e.dxftype() == "MTEXT" else "height", 0) or 0
        try:
            turn = e.get_rotation() if e.dxftype() == "MTEXT" else e.dxf.get("rotation", 0)
        except Exception:
            turn = 0
        out.append({"lines": lines, "text": " ".join(lines), "at": [round(c[0] * scale, 3), round(c[1] * scale, 3)],
                    "layer": layer, "height_m": round(height * scale, 3), "rotation": round(float(turn or 0), 1)})
    return out


def read_as(text: str, ws, profile) -> dict:
    """How Studio reads a text: by its rules (a room's type, or never a room's name), and
    the answer kept for it (a model's, a person's, the rules')."""
    out: dict = {}
    t = text.strip()
    if not t or NOT_A_ROOM.match(t) or len(t) > 40 or len(t.split()) > 5:
        out["rules"] = "not a room name (a level, tag, direction, size or note)"
    else:
        kind, rule = profile.classify(t, [], "")
        out["rules"] = {"type": kind.value, "rule": rule} if kind != SpaceType.UNSPECIFIED else None
    r = ws.readings.get(t)
    if r is not None:
        out["kept_answer"] = {"type": r.type.value if r.type else None, "source": r.source,
                              "rooms_only": r.rooms_only, "asked": model_name(r.asked)}
    return out


def reading_of(ws, floor_id: str, frame: Frame, ids: LocalIds, rooms: list, openings: list, *,
               profile, texts: list[dict], layers_used, settings: dict) -> dict:
    """reading.json: what Studio decided in the area, and why (see docs/AREA-SAMPLES.md)."""
    f = ws.floor(floor_id)
    spaces = []
    shapes = {}
    for r in sorted(rooms, key=lambda r: _order(ids(r.id))):
        g = shape(r.geometry)
        o = ws.overrides.get(r.id)
        eff = ws.effective(r)
        cut = frame.geom(g)
        whole = frame.moved(g)
        inside = whole.intersection(frame.rect).area / whole.area if whole.area > 0 else 0.0
        shapes[r.id] = whole
        spaces.append({
            "id": ids(r.id), "kind": r.kind, "parent": ids(r.parent), "zones": [ids(z) for z in r.zones],
            "type": r.type, "type_source": r.type_source,
            "decided_by": decided_by(r.type_source) if r.type != SpaceType.UNSPECIFIED.value else "nobody",
            "name": r.name, "number": r.number, "label": r.label,
            "name_read_as": read_as(r.name, ws, profile) if r.name else None,
            "issues": list(r.issues), "set_aside_by_vision": r.detected_ignored,
            "now": {"type": getattr(eff["type"], "value", eff["type"]), "name": eff["name"], "number": eff["number"],
                    "ignored": eff["ignored"], "hidden": eff["hidden"], "corrected": bool(o is not None and eff["corrected"])},
            "review_reasons": ws.review_reasons(r),
            "area_m2": round(g.area, 2), "inside_share": round(inside, 3), "at_edge": inside < EDGE_SHARE,
            "geometry": rounded(mapping(cut)) if cut is not None else None,
        })
    doors = []
    for r in sorted(openings, key=lambda r: _order(ids(r.id))):
        m = _middle(r)
        doors.append({
            "id": ids(r.id), "type": opening_kind(r), "found_as": r.type_source,
            "decided_by": "person (drawn in review)" if (r.type_source or "").startswith("drawn") else "rules",
            "connects": [ids(c) for c in r.connects], "middle": frame.point(m.x, m.y),
            "span": frame.points(r.span) if r.span else None, "width": r.width,
            "swings": [frame.points(s) for s in r.swings] if r.swings else None,
            "tag": r.tag, "sill": r.sill, "height": r.height, "issues": list(r.issues),
            "ignored": ws.effective(r)["ignored"],
        })
    units = [(r, shapes[r.id]) for r in rooms if not r.zones]
    text_rows = []
    for n, t in enumerate(sorted(texts, key=lambda t: (-t["at"][1], t["at"][0])), start=1):
        p = Point(*t["at"])
        room = next((ids(r.id) for r, g in units if g.covers(p)), None)
        labels = [ids(r.id) for r, _ in units if r.label and t["text"] and t["text"] in r.label.replace("\n", " ")]
        text_rows.append({"id": f"T{n}", **t, "in": room, "label_of": labels[0] if labels else None,
                          "read_as": read_as(t["text"], ws, profile),
                          "lines_read_as": {ln: read_as(ln, ws, profile) for ln in t["lines"]} if len(t["lines"]) > 1
                          else None})
    vision = []
    for a in ws.vision.values():
        if "shape" in a:
            try:
                g = wkt.loads(a["shape"])
            except Exception:
                continue
            if not frame.touches(g):
                continue
            match = next((ids(r.id) for r, s in units if _same_shape(frame.moved(g), s)), None)
            vision.append({"kind": "room", "room": match, "outline": a.get("outline"), "type": a.get("type"),
                           "model": model_name(a.get("model")), "shape": _geojson(frame, g),
                           "shape_wkt": wkt.dumps(frame.moved(g), rounding_precision=2)})
        elif "cut" in a:
            try:
                room_text, line_text = a["cut"].split("|", 1)
                g, line = wkt.loads(room_text), wkt.loads(line_text)
            except Exception:
                continue
            if not frame.touches(line):
                continue
            match = next((ids(r.id) for r, s in units if _same_shape(frame.moved(g), s)), None)
            vision.append({"kind": "line across a room", "room": match, "a": a.get("a"), "b": a.get("b"),
                           "model": model_name(a.get("model")), "line": _geojson(frame, line),
                           "cut_wkt": wkt.dumps(frame.moved(g), rounding_precision=2) + "|"
                           + wkt.dumps(frame.moved(line), rounding_precision=2)})
    symbols = []
    for s in f.symbols or []:
        if frame.holds(*s["point"]):
            x0, y0, x1, y1 = s["box"]
            symbols.append({"label": s["label"], "score": s["score"], "point": frame.point(*s["point"]),
                            "box": [*frame.point(x0, y0), *frame.point(x1, y1)]})
    roles = [parse_layer_line(line) for line in f.layers]
    return {
        "frame": {"units": "m", "origin": "the area's lower-left corner",
                  "area": [0, 0, frame.w, frame.h], "extent": [-frame.margin, -frame.margin,
                                                               frame.w + frame.margin, frame.h + frame.margin]},
        "settings": settings,
        "floor": {"method": f.method, "warnings": list(f.warnings),
                  "converted_at": f.converted_at.isoformat() if f.converted_at else None,
                  "wall_thickness_m": f.wall_thickness,
                  "outline": frame.geojson(f.outline), "walls_found": frame.geojson(f.walls)},
        "layers": {
            "in_sample": [{"layer": name, "entities": n, "read_as": next((x["roles"] for x in roles if x["layer"] == name), None),
                           "why": next((x["why"] for x in roles if x["layer"] == name), None)}
                          for name, n in sorted(layers_used.items())],
            "read_on_floor": len(roles),
        },
        "spaces": spaces,
        "openings": doors,
        "texts": text_rows,
        "vision": vision,
        "symbols": symbols,
    }


def corrections_of(ws, floor_id: str, frame: Frame, ids: LocalIds, rooms: list, openings: list, items: list,
                   catalogue=None) -> dict:
    """corrections.json: what people changed in the area, the right answers."""
    objects = []
    for r in sorted([*rooms, *openings], key=lambda r: _order(ids(r.id))):
        o = ws.overrides.get(r.id)
        if o is None:
            continue
        set_ = o.model_dump(exclude_none=True)
        row = {"id": ids(r.id), "kind": r.kind if r.kind != "opening" else opening_kind(r),
               "detected": {"type": r.type, "name": r.name, "number": r.number, "ignored": r.detected_ignored},
               "accepted_as_is": not set_}
        for key in ("type", "name", "number", "hidden", "ignored", "capacity", "floor_finish", "wall_finish"):
            if key in set_:
                row[key] = getattr(set_[key], "value", set_[key])
        if "stack" in set_:
            row["linked_to_another_floor"] = set_["stack"] != ""
        objects.append(row)
    e = ws.floor(floor_id).edits
    lines = lambda pts: [frame.points(line) for line in pts if _line_touches(frame, line)]  # noqa: E731
    drawn = {
        "walls": lines(e.walls), "dividers": lines(e.dividers),
        "openings": [{"type": o.type, "span": frame.points(o.span), "sill": o.sill, "height": o.height}
                     for o in e.openings if _line_touches(frame, o.span)],
        "resized": [{"at": frame.point(*x.at), "width": x.width, "sill": x.sill, "height": x.height}
                    for x in e.resized if frame.holds(*x.at)],
        "spaces": [frame.points(ring) for ring in e.spaces if frame.touches(Polygon(ring))],
    }
    units = [(r, frame.moved(shape(r.geometry))) for r in rooms if not r.zones]
    placed = []
    for it in sorted(items, key=lambda it: _order(ids(it.id))):
        p = Point(*frame.point(it.x, it.y))
        t = catalogue.get(it.type) if catalogue is not None else None
        placed.append({"id": ids(it.id), "type": it.type, "category": t.category if t else None,
                       "at": frame.point(it.x, it.y), "rotation": round(it.rotation, 1),
                       "in": next((ids(r.id) for r, g in units if g.covers(p)), None)})
    return {"objects": objects, "drawn": drawn, "items": placed}


def _line_touches(frame: Frame, pts) -> bool:
    return len(pts) >= 2 and frame.touches(LineString(pts))


def _same_shape(a, b) -> bool:
    if a.equals_exact(b, 0.01):
        return True
    try:
        return iou(a, b) >= 0.9
    except Exception:
        return False


def _geojson(frame: Frame, g) -> dict | None:
    cut = frame.geom(g)
    return rounded(mapping(cut)) if cut is not None else None


def _order(local: str | None):
    if not local or local == "outside":
        return ("~", 0)
    return (local[0], int(local[1:]) if local[1:].isdigit() else 0)
