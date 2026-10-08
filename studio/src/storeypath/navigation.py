"""Indoor navigation: a building's walking network (format 0.8, ``navigation.json``),
and the way from one place in it to another.

Studio works the network out when it makes a package (export.py), and every reader
routes on it the same way: Studio here, the Go module (go/navigation.go) and the
viewers (viewer/src/navigation.js) give the same way, the same steps, for the same
package (spec/conformance/routes.json). spec/FORMAT.md, "Navigation (0.8)", is the
rule; this is Studio's side of it.

**The network.** Nodes are where a person is or passes: each door and opening that is
a way through (``door``; one to the outside, ``entrance``), at the middle of its span;
in front of it on each side, in the room it opens into (``approach``: half the room's
depth in front of it, at most 2 m, so the approaches of a corridor's doors line up
along its middle); a point in each space and zone a person arrives at (``room``: its
label point, clear of the walls); each lift, stairs, escalator and ramp on each floor
(``lift``, ``stairs``, …: in that space); each wayfinding kiosk, where people stand
before its screen (``kiosk``). Edges are the walks between them: through a door (from
it to its approaches, ``door``), across a room between any two of its nodes along the
shortest way that keeps MARGIN_M from its walls where the room allows it (``walk``;
the zones of a divided space are one room: no wall parts them), and between floors by
the lifts and stairs that are one stack (stacks.py). Each walk in a room is kept only
when no other way through the room's nodes is nearly as short (SPANNER_STRETCH): a
corridor's doors are joined each to the next along it, not each to every other.

**The way.** The fewest seconds (``cost``: walking at SPEED_M_S, lifts and stairs as
LIFT_WAIT_S, LIFT_FLOOR_S and STAIRS_FLOOR_S say, and ROOM_PENALTY_S for each way into
a room that is not for passing through), in whole tenths of a second, nodes taken in
order of how far they are and, at the same distance, of their IDs: so every reader
takes the same way where two are as short.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, shape
from shapely.ops import nearest_points, polylabel, unary_union

from .geometry import as_polygons
from .stacks import stacks as find_stacks

NAVIGATION_FILE = "navigation.json"
SPEED_M_S = 1.3  # walking
MARGIN_M = 0.35  # a way keeps this far from walls, where the room is wide enough
APPROACH_MAX_M = 2.0  # how far in front of a door its approach is, at most
LIFT_WAIT_S = 30.0  # waiting for a lift, once a ride
LIFT_FLOOR_S = 4.0  # a lift's ride, a floor
STAIRS_FLOOR_S = 12.0  # stairs (escalators and ramps between floors likewise), a floor
ROOM_PENALTY_S = 30.0  # each way into a room not for passing through (an office): its cost, not its time
KIOSK_STAND_M = 0.6  # where people stand, in front of a kiosk's screen
SPANNER_STRETCH = 1.02  # a walk is kept unless the room's other walks join its ends within this…
SPANNER_SLACK_M = 0.05  # …and this
JOIN_MIN_M = 0.5  # a lift or stairs with no way in joins a room it shares this much edge with
WALK_BACK_M = 2.0  # the way a person walks, before a door: over this much of the way
DECIMALS = 3  # points, in metres

# What a person walks through: rooms for passing through, and rooms they go to (a way
# through one costs ROOM_PENALTY_S each way in). The others are not walked: plant
# rooms, shafts, voids. FORMAT.md, "Navigation (0.8)".
PASS_TYPES = frozenset({"corridor", "lobby", "open_area", "elevator", "stairs", "escalator", "ramp", "parking",
                        "terrace", "balcony", "living_room", "dining_room"})
ROOM_TYPES = frozenset({"office", "room", "meeting_room", "restroom", "kitchen", "storage", "bedroom", "bathroom",
                        "dressing_room", "laundry", "prayer_room", "unspecified"})
WALKABLE = PASS_TYPES | ROOM_TYPES
VERTICAL = {"elevator": "lift", "stairs": "stairs", "escalator": "escalator", "ramp": "ramp"}  # type -> node kind
ARRIVALS = frozenset({"room", "lift", "stairs", "escalator", "ramp"})  # the node a place is arrived at
DOORS = frozenset({"door", "entrance"})
THROUGH = frozenset({"corridor", "ramp"})  # "along" it; anything else, "through" it
TYPE_WORDS = {"elevator": "lift", "unspecified": "room", "open_area": "open area", "meeting_room": "meeting room",
              "living_room": "living room", "dining_room": "dining room", "dressing_room": "dressing room",
              "prayer_room": "prayer room", "open_to_below": "void"}


class NoRoute(ValueError):
    """No way between two places (none, or none without stairs)."""


def type_words(kind: str) -> str:
    return TYPE_WORDS.get(kind, kind.replace("_", " "))


def label_of(name: str | None, number: str | None, kind: str) -> str:
    """What a place is called in a step: its name and number ("OFFICE 112"), its name,
    its type and number ("Room 114"), or "the" and its type ("the corridor")."""
    name, number = (name or "").strip(), (number or "").strip()
    if name and number:
        return name if number in name else f"{name} {number}"
    if name:
        return name
    words = type_words(kind)
    if number:
        return f"{words[:1].upper()}{words[1:]} {number}"
    return f"the {words}"


def _r(v: float) -> float:
    return round(float(v), DECIMALS)


def _pt(p) -> tuple[float, float]:
    return (_r(p[0]), _r(p[1]))


# ---- geometry ---------------------------------------------------------------------------

def walking_region(area) -> object:
    """Where a way across a room may go: MARGIN_M in from its walls, and in the parts of
    it too narrow for that (a passage under 2 × MARGIN_M wide), up to them."""
    area = area.buffer(0)
    inner = area.buffer(-MARGIN_M, join_style="mitre")
    opened = inner.buffer(MARGIN_M, join_style="mitre")
    narrow = [p for p in as_polygons(area.difference(opened)) if p.area >= 0.02]
    if not narrow:
        return inner if not inner.is_empty else area
    # a narrow part reaches into the wide part it opens from
    reach = unary_union(narrow).buffer(MARGIN_M + 0.02, join_style="mitre").intersection(area)
    out = unary_union([inner, reach]) if not inner.is_empty else area
    return out.buffer(0)


def _inside(region, p) -> tuple[float, float]:
    """A point, or the nearest point of the region to it when it is not in it."""
    point = Point(p)
    if region.covers(point):
        return _pt(p)
    q = nearest_points(region, point)[0]
    return _pt((q.x, q.y))


def label_point(geom) -> tuple[float, float]:
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda p: p.area)
    if isinstance(geom, Polygon):
        p = polylabel(geom, tolerance=0.05)
    else:
        p = geom.representative_point()
    return p.x, p.y


def _reflex_corners(region) -> list[tuple[float, float]]:
    """The corners of a region a shortest way bends round: those where it turns in
    (each ring walked with the region on its left)."""
    out = []
    for poly in as_polygons(shapely.orient_polygons(region)):
        for ring in (poly.exterior, *poly.interiors):
            pts = list(ring.coords)[:-1]
            n = len(pts)
            for i in range(n):
                (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[(i + 1) % n]
                if (bx - ax) * (cy - by) - (by - ay) * (cx - bx) < -1e-9:
                    out.append(_pt((bx, by)))
    return out


def approach_point(area, region, middle, along) -> tuple[float, float] | None:
    """In front of a way through, in a room: from its middle across the room, half the
    room's depth there (so a corridor's are on its middle line), at most APPROACH_MAX_M;
    in the walking region. ``along``: the way through's direction along its wall."""
    ax, ay = along
    length = math.hypot(ax, ay)
    if length == 0:
        q = nearest_points(region, Point(middle))[0]
        return _inside(region, (q.x, q.y))
    nx, ny = -ay / length, ax / length
    mx, my = middle
    near = [area.distance(Point(mx + s * nx * 0.25, my + s * ny * 0.25)) for s in (1, -1)]
    if near[1] < near[0]:
        nx, ny = -nx, -ny
    ray = LineString([(mx, my), (mx + nx * 40, my + ny * 40)]).intersection(area)
    pieces = []
    for g in getattr(ray, "geoms", [ray]):
        if isinstance(g, LineString) and not g.is_empty:
            ts = [(x - mx) * nx + (y - my) * ny for x, y in g.coords]
            pieces.append((min(ts), max(ts)))
    if not pieces:
        q = nearest_points(region, Point(middle))[0]
        return _inside(region, (q.x, q.y))
    t0, t1 = min(pieces)
    t = t0 + min((t1 - t0) / 2, APPROACH_MAX_M)
    return _inside(region, (mx + nx * t, my + ny * t))


def walks(region, terminals: list[tuple[str, tuple[float, float]]]) -> list[tuple[str, str, list]]:
    """The walks across one room between its nodes: (id, id, points), the ids in order.
    Each the shortest way in the walking region (round its corners); only those no other
    way through the room's nodes is nearly as short as (a greedy spanner)."""
    if len(terminals) < 2:
        return []
    tol = region.buffer(0.005)
    shapely.prepare(tol)
    ids = [t[0] for t in terminals]
    points = [t[1] for t in terminals]
    known = set(points)
    corners = [c for c in dict.fromkeys(_reflex_corners(region)) if c not in known]
    pts = np.array(points + corners, dtype=float)
    n, k = len(pts), len(points)
    ii, jj = np.triu_indices(n, 1)
    seg = np.stack([pts[ii], pts[jj]], axis=1)
    length = np.hypot(seg[:, 1, 0] - seg[:, 0, 0], seg[:, 1, 1] - seg[:, 0, 1])
    seen = np.ones(len(ii), dtype=bool)
    real = length > 1e-9
    if real.any():
        seen[real] = shapely.covers(tol, shapely.linestrings(seg[real]))
    dist = np.full((n, n), np.inf)
    np.fill_diagonal(dist, 0.0)
    dist[ii[seen], jj[seen]] = length[seen]
    dist[jj[seen], ii[seen]] = length[seen]
    nxt = np.where(np.isfinite(dist), np.arange(n)[None, :], -1)
    for m in range(n):  # every pair's shortest way (Floyd–Warshall), and its next point
        alt = dist[:, m, None] + dist[None, m, :]
        better = alt < dist - 1e-12
        if better.any():
            dist = np.where(better, alt, dist)
            nxt = np.where(better, nxt[:, m, None], nxt)
    d = dist[:k, :k]
    pairs = sorted((float(d[a, b]), ids[a], ids[b], a, b) for a in range(k) for b in range(a + 1, k)
                   if np.isfinite(d[a, b]))
    have = np.full((k, k), np.inf)
    np.fill_diagonal(have, 0.0)
    out = []
    for w, _, _, a, b in pairs:
        if have[a, b] <= SPANNER_STRETCH * w + SPANNER_SLACK_M:
            continue
        have = np.minimum(have, np.minimum(have[:, a, None] + w + have[None, b, :],
                                           have[:, b, None] + w + have[None, a, :]))
        way = [a]
        while way[-1] != b:
            way.append(int(nxt[way[-1], b]))
        line = [tuple(pts[i]) for i in way]
        if ids[a] < ids[b]:
            out.append((ids[a], ids[b], line))
        else:
            out.append((ids[b], ids[a], line[::-1]))
    return out


def _length(points) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


# ---- the network ------------------------------------------------------------------------

@dataclass
class _Unit:
    """A room a person walks across: a space, or a space divided into zones (one room:
    no wall parts its zones)."""

    space: object  # its ObjectRecord
    type: str  # its type (a divided space's: its own)
    area: object  # where its floor is walked: the space, less the zones that are not walked
    zones: list = field(default_factory=list)  # (ObjectRecord, shape, type) of its zones walked on
    region: object = None
    terminals: list = field(default_factory=list)  # (node id, point)

    def zone_at(self, p) -> tuple | None:
        point = Point(p)
        hit = [z for z in self.zones if z[1].covers(point)]
        if not hit:
            hit = sorted(self.zones, key=lambda z: (z[1].distance(point), z[0].id))[:1]
        return hit[0] if hit else None

    def place_at(self, p) -> tuple[str | None, str]:
        """The zone a point is in (None in a space with none), and the type there."""
        if not self.zones:
            return None, self.type
        z = self.zone_at(p)
        return (z[0].id, z[2]) if z else (None, self.type)


class _Network:
    def __init__(self, georef):
        self.georef = georef
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str], dict] = {}
        self.places: dict[str, dict] = {}

    def node(self, nid: str, kind: str, floor_id: str, p, space_id=None, zone_id=None, **more) -> str:
        if nid in self.nodes:
            return nid
        lon, lat = self.georef.lonlat(p[0], p[1])
        self.nodes[nid] = {"id": nid, "kind": kind, "floor_id": floor_id, "space_id": space_id, "zone_id": zone_id,
                           "local": {"x_m": p[0], "y_m": p[1]},
                           "lonlat": [round(lon, 7), round(lat, 7)], **more}
        return nid

    def edge(self, a: str, b: str, kind: str, points, *, seconds=None, length=None, cost_extra=0.0,
             accessible=True, space_id=None, zone_id=None) -> None:
        if b < a:
            a, b, points = b, a, list(points)[::-1]
        points = [list(_pt(p)) for p in points]
        length = round(_length(points) if length is None else length, 2)
        seconds = round(length / SPEED_M_S if seconds is None else seconds, 1)
        e = {"from": a, "to": b, "kind": kind, "length_m": length, "seconds": seconds,
             "cost": round(seconds + cost_extra, 1), "accessible": accessible, "space_id": space_id,
             "zone_id": zone_id, "path": points}
        have = self.edges.get((a, b))
        if have is None or (e["cost"], e["kind"]) < (have["cost"], have["kind"]):  # one edge a pair: the cheaper
            self.edges[(a, b)] = e


def _units(ws, f_id: str) -> dict[str, _Unit]:
    """The rooms of a floor a person walks across, by space ID."""
    objects = {r.id: r for r in ws.floor_objects(f_id)}
    out = {}
    for r in sorted(objects.values(), key=lambda r: r.id):
        if r.kind != "space" or not r.geometry:
            continue
        eff = ws.effective(r)
        if eff["hidden"] or eff["ignored"]:
            continue
        geom = shape(r.geometry).buffer(0)
        if geom.is_empty:
            continue
        zones = [objects[z] for z in r.zones if z in objects and objects[z].status == "active"]
        if zones:
            walked, off = [], []
            for z in zones:
                ze = ws.effective(z)
                zg = shape(z.geometry).buffer(0)
                if ze["type"] in WALKABLE and not ze["hidden"]:
                    walked.append((z, zg, ze["type"]))
                else:
                    off.append(zg)
            if not walked:
                continue
            area = geom.difference(unary_union(off)) if off else geom
            out[r.id] = _Unit(space=r, type=eff["type"], area=area.buffer(0), zones=walked)
        elif eff["type"] in WALKABLE:
            out[r.id] = _Unit(space=r, type=eff["type"], area=geom)
    for u in out.values():
        u.region = walking_region(u.area)
    return out


def _place(ws, r, floor_id: str, space_id: str | None = None) -> dict:
    """A space or zone as a step calls it. A zone with no name or number is called by
    its space's (half of a corridor is the corridor), and by its space's type when it
    has none of its own."""
    eff = ws.effective(r)
    name, number, kind = eff["name"], eff["number"], eff["type"]
    if r.kind == "zone" and not name and not number and r.parent in ws.objects:
        whole = ws.effective(ws.objects[r.parent])
        name, number = whole["name"], whole["number"]
        if kind == "unspecified":
            kind = whole["type"]
    return {"id": r.id, "kind": r.kind, "space_id": space_id, "floor_id": floor_id, "type": kind,
            "label": label_of(name, number, kind)}


def build_network(ws, building_id: str, catalogue=None, placement=None) -> dict:
    """A building's walking network, as navigation.json holds it (one building's part:
    its floors, places, nodes and edges). ``placement``: where the building is on the
    map (export.placements' own, unless given)."""
    from .catalogue import default_catalogue
    from .export import placements
    from .georef import Georeferencer

    cat = catalogue or default_catalogue()
    if placement is None:
        placement = placements(ws)[building_id][0]
    net = _Network(Georeferencer(placement))
    floors = [(fid, f) for *_, f, fid in ws.iter_floors() if fid.startswith(building_id + "-")]
    stack_list = find_stacks(ws, building_id)
    stack_key = {m: s.key for s in stack_list for m in s.members}
    for f_id, _ in floors:
        _floor(ws, f_id, cat, net, stack_key)
    _vertical(ws, floors, stack_list, net)
    for e in net.edges.values():  # the places walked through, too
        for pid in (e["space_id"], e["zone_id"]):
            if pid and pid not in net.places and pid in ws.objects:
                r = ws.objects[pid]
                net.places[pid] = _place(ws, r, pid.rsplit("-", 1)[0], r.parent)
    return {
        "speed_m_s": SPEED_M_S,
        "buildings": [building_id],
        "floors": [{"id": fid, "building_id": building_id, "name": f.name, "ordinal": f.ordinal,
                    "elevation": f.elevation} for fid, f in floors],
        "places": sorted(net.places.values(), key=lambda p: p["id"]),
        "nodes": sorted(net.nodes.values(), key=lambda n: n["id"]),
        "edges": sorted(net.edges.values(), key=lambda e: (e["from"], e["to"])),
    }


def _floor(ws, f_id: str, cat, net: _Network, stack_key: dict) -> None:
    units = _units(ws, f_id)
    for sid, u in units.items():
        if u.zones:
            net.places[sid] = _place(ws, u.space, f_id)
            for z, _, _ in u.zones:
                net.places[z.id] = _place(ws, z, f_id, sid)
        else:
            net.places[sid] = _place(ws, u.space, f_id)
    # where people arrive in each room: a lift's or stairs' own node, else a room node a
    # zone (of a divided space), else the space
    for sid, u in units.items():
        if u.zones:
            for z, zg, _ in u.zones:
                if ws.effective(z)["ignored"]:
                    continue
                p = _inside(u.region, label_point(zg))
                u.terminals.append((net.node(f"room:{z.id}", "room", f_id, p, sid, z.id), p))
            continue
        p = _inside(u.region, label_point(u.area))
        if u.type in VERTICAL:
            kind = VERTICAL[u.type]
            nid = net.node(f"{kind}:{sid.rsplit('-', 1)[1]}@{f_id}", kind, f_id, p, sid, None,
                           stack=stack_key.get(sid))
        else:
            nid = net.node(f"room:{sid}", "room", f_id, p, sid, None)
        u.terminals.append((nid, p))
    # kiosks: where people stand before the screen
    for it in sorted(ws.floor_items(f_id), key=lambda i: i.id):
        if not (it.type == "KIOSK" or it.type.startswith("KIOSK-")):
            continue
        t = cat.get(it.type)
        depth = t.depth if t else 0.45
        r = math.radians(it.rotation)
        fx, fy = math.sin(r), -math.cos(r)
        front = (it.x + fx * (depth / 2 + KIOSK_STAND_M), it.y + fy * (depth / 2 + KIOSK_STAND_M))
        u = next((u for u in units.values() if u.area.covers(Point(front))), None) or \
            next((u for u in units.values() if u.area.covers(Point(it.x, it.y))), None)
        if u is None:
            net.node(f"kiosk:{it.id}", "kiosk", f_id, _pt(front), None, None, item_id=it.id)
            continue
        p = _inside(u.region, front)
        zone, _ = u.place_at(p)
        u.terminals.append((net.node(f"kiosk:{it.id}", "kiosk", f_id, p, u.space.id, zone, item_id=it.id), p))
    # doors and openings: a node in the way through, and one in front of it on each side
    joined = set()
    for o in sorted((r for r in ws.floor_objects(f_id) if r.kind == "opening"), key=lambda r: r.id):
        if o.type == "window" or ws.effective(o)["ignored"]:
            continue
        sides = [c for c in o.connects if c in units]
        if not sides or len(sides) < len(o.connects) and len(o.connects) > 1:
            continue  # it leads into what is not walked (a plant room)
        if o.span and len(o.span) == 2:
            (x0, y0), (x1, y1) = o.span
            middle, along = ((x0 + x1) / 2, (y0 + y1) / 2), (x1 - x0, y1 - y0)
        else:
            c = o.geometry["coordinates"]
            middle, along = (c[0], c[1]), (0.0, 0.0)
        middle = _pt(middle)
        kind = "entrance" if len(o.connects) == 1 else "door"
        door = net.node(f"door:{o.id}", kind, f_id, middle, None, None, opening_id=o.id, spaces=sorted(sides))
        for sid in sorted(sides):
            _approach(net, units[sid], door, f"approach:{o.id}@{sid}", f_id, middle, along, opening_id=o.id)
            joined.add(sid)
    # a lift or stairs with no way in drawn (one drawn in review, say) joins the rooms it touches
    for sid, u in units.items():
        if u.type not in VERTICAL or sid in joined or u.zones:
            continue
        for oid, other in units.items():
            if oid == sid:
                continue
            shared = u.area.boundary.intersection(other.area.buffer(0.3))
            pieces = [g for g in getattr(shared, "geoms", [shared]) if isinstance(g, LineString) and g.length > 0]
            if not pieces or max(p.length for p in pieces) < JOIN_MIN_M:
                continue
            piece = max(pieces, key=lambda p: p.length)
            mid = piece.interpolate(0.5, normalized=True)
            d = piece.project(mid)
            a, b = piece.interpolate(max(0.0, d - 0.1)), piece.interpolate(min(piece.length, d + 0.1))
            middle = _pt((mid.x, mid.y))
            pair = "+".join(sorted((sid, oid)))
            door = net.node(f"door:{pair}", "door", f_id, middle, None, None, opening_id=None,
                            spaces=sorted((sid, oid)))
            for side in sorted((sid, oid)):
                _approach(net, units[side], door, f"approach:{pair}@{side}", f_id, middle, (b.x - a.x, b.y - a.y))
    # across each room
    for sid, u in units.items():
        for a, b, line in walks(u.region, u.terminals):
            zone = None
            if u.zones:
                path = LineString(line) if len(line) > 1 and _length(line) > 0 else Point(line[0])
                zone = min(u.zones, key=lambda z: (-path.intersection(z[1]).length, z[0].id))[0].id
            net.edge(a, b, "walk", line, space_id=sid, zone_id=zone)


def _approach(net: _Network, u: _Unit, door: str, nid: str, f_id: str, middle, along, **more) -> None:
    p = approach_point(u.area, u.region, middle, along)
    zone, kind = u.place_at(p)
    net.node(nid, "approach", f_id, p, u.space.id, zone, **more)
    u.terminals.append((nid, p))
    net.edge(door, nid, "door", [middle, p], cost_extra=ROOM_PENALTY_S if kind in ROOM_TYPES else 0.0,
             space_id=u.space.id, zone_id=zone)


def _vertical(ws, floors, stack_list, net: _Network) -> None:
    """Between floors: a lift joins every floor of its stack to every other; stairs,
    escalators and ramps each floor of it to the next one up that it serves."""
    order = {fid: i for i, (fid, _) in enumerate(floors)}
    elevation = {fid: f.elevation for fid, f in floors}
    by_space = {n["space_id"]: n for n in net.nodes.values() if n["kind"] in VERTICAL.values()}
    for s in stack_list:
        nodes = sorted((by_space[m] for m in s.members if m in by_space), key=lambda n: (order[n["floor_id"]], n["id"]))
        pairs = []
        if s.type == "elevator":
            pairs = [(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1:] if a["floor_id"] != b["floor_id"]]
        else:
            levels = sorted({order[n["floor_id"]] for n in nodes})
            for lo, hi in zip(levels, levels[1:]):
                pairs += [(a, b) for a in nodes if order[a["floor_id"]] == lo for b in nodes
                          if order[b["floor_id"]] == hi]
        kind = VERTICAL[s.type]
        for a, b in pairs:
            floors_apart = abs(order[b["floor_id"]] - order[a["floor_id"]])
            seconds = LIFT_WAIT_S + LIFT_FLOOR_S * floors_apart if kind == "lift" else STAIRS_FLOOR_S * floors_apart
            pa, pb = (a["local"]["x_m"], a["local"]["y_m"]), (b["local"]["x_m"], b["local"]["y_m"])
            net.edge(a["id"], b["id"], kind, [pa, pb], seconds=seconds,
                     length=abs(elevation[b["floor_id"]] - elevation[a["floor_id"]]),
                     accessible=kind in ("lift", "ramp"))


def items_in(ws, building_id: str) -> dict[str, dict]:
    """Where each item of a building stands (item ID -> its space_id and zone_id), as
    its package says: for a way to an item (route's ``items``)."""
    from .export import floor_units, standing_in

    out = {}
    for *_, fid in ws.iter_floors():
        if not fid.startswith(building_id + "-"):
            continue
        units = floor_units(ws, fid)
        for it in ws.floor_items(fid):
            space, zone = standing_in(it, units)
            out[it.id] = {"space_id": space, "zone_id": zone}
    return out


def navigation_file(ws, buildings: list[str], catalogue=None, placed=None) -> dict:
    """navigation.json for a package of these buildings (one, for a package Studio
    sends; Studio's own previews may hold several)."""
    from .export import placements

    placed = placed or placements(ws)
    out = {"speed_m_s": SPEED_M_S, "buildings": sorted(buildings), "floors": [], "places": [], "nodes": [],
           "edges": []}
    for b in sorted(buildings):
        net = build_network(ws, b, catalogue, placed[b][0])
        for key in ("floors", "places", "nodes", "edges"):
            out[key] += net[key]
    return out


# ---- the way ----------------------------------------------------------------------------

def floor_of_id(i: str) -> str:
    return i.rsplit("-", 1)[0]


class Graph:
    """navigation.json, ready to route on."""

    def __init__(self, nav: dict):
        self.nav = nav
        self.nodes = {n["id"]: n for n in nav.get("nodes", [])}
        self.places = {p["id"]: p for p in nav.get("places", [])}
        self.floors = {f["id"]: f for f in nav.get("floors", [])}
        self.order = {f["id"]: i for i, f in enumerate(nav.get("floors", []))}
        self.adjacent: dict[str, list[tuple[str, dict]]] = {i: [] for i in self.nodes}
        for e in nav.get("edges", []):
            if e["from"] in self.nodes and e["to"] in self.nodes:
                self.adjacent[e["from"]].append((e["to"], e))
                self.adjacent[e["to"]].append((e["from"], e))

    def ends(self, ref: str, items=None) -> list[str]:
        """The nodes a place is: a node's ID; a space's, or a zone's (where it is
        arrived at: its zones', for a space divided into zones); an item's (a kiosk's
        own node, else where it stands: ``items``, item ID -> its feature or properties)."""
        if ref in self.nodes:
            return [ref]
        found = sorted(n["id"] for n in self.nodes.values()
                       if n["kind"] in ARRIVALS and ref in (n["space_id"], n["zone_id"]))
        if found:
            return found
        if f"kiosk:{ref}" in self.nodes:
            return [f"kiosk:{ref}"]
        it = (items or {}).get(ref)
        if it is not None:
            props = it.get("properties", it)
            for place in (props.get("zone_id"), props.get("space_id")):
                if place:
                    return self.ends(place)
        raise KeyError(f"no node, place or item {ref} in the network")

    def place_of(self, nid: str) -> str | None:
        n = self.nodes[nid]
        if n["kind"] in DOORS:
            spaces = n.get("spaces") or []
            return spaces[0] if spaces else None
        return n["zone_id"] or n["space_id"]

    def label(self, place: str | None) -> str:
        p = self.places.get(place) if place else None
        return p["label"] if p else "the room"


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text[:1].isascii() and text[:1].islower() else text


def _round2(v: float) -> float:
    return math.floor(v * 100 + 0.5) / 100


def _metres(v: float) -> int:
    return int(math.floor(v + 0.5))


def shortest(graph: Graph, sources: list[str], targets: list[str], accessible: bool = False) -> list[str] | None:
    """The cheapest way (in whole tenths of a second) from any source to any target: its
    nodes. At the same distance, nodes are taken in order of their IDs."""
    want = set(targets)
    dist = {s: 0 for s in sources}
    prev: dict[str, str | None] = {s: None for s in sources}
    heap = [(0, s) for s in sorted(set(sources))]
    heapq.heapify(heap)
    done = set()
    while heap:
        d, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u in want:
            path = [u]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            return path[::-1]
        for v, e in graph.adjacent[u]:
            if accessible and not e["accessible"]:
                continue
            nd = d + int(round(e["cost"] * 10))
            if v not in dist or nd < dist[v]:
                dist[v], prev[v] = nd, u
                heapq.heappush(heap, (nd, v))
    return None


def _edge(graph: Graph, a: str, b: str) -> dict:
    return next(e for v, e in graph.adjacent[a] if v == b)


def _forward(e: dict, a: str) -> list:
    return e["path"] if e["from"] == a else e["path"][::-1]


def route(nav: dict | Graph, start: str, end: str, *, accessible: bool = False, items=None) -> dict:
    """The way from ``start`` to ``end`` (a node's, space's, zone's or item's ID), on
    lifts and ramps alone when ``accessible``: its nodes, its legs (a floor each, the
    line to draw), its changes of floor, its length and time, and its steps. NoRoute
    when there is none; KeyError for an ID the network does not know."""
    graph = nav if isinstance(nav, Graph) else Graph(nav)
    path = shortest(graph, graph.ends(start, items), graph.ends(end, items), accessible)
    if path is None:
        raise NoRoute(f"no way from {start} to {end}" + (" without stairs" if accessible else ""))
    edges = [_edge(graph, path[i], path[i + 1]) for i in range(len(path) - 1)]
    # runs on one floor, between the rides from floor to floor
    runs, rides = [[]], []
    for i, e in enumerate(edges):
        if e["kind"] in ("walk", "door"):
            runs[-1].append(i)
        elif rides and rides[-1][-1] == i - 1 and edges[rides[-1][0]]["kind"] == e["kind"] and not runs[-1]:
            rides[-1].append(i)
        else:
            rides.append([i])
            runs.append([])
    starts = [0] + [r[-1] + 1 for r in rides]  # the node each run starts at
    legs = []
    for run, at in zip(runs, starts):
        points = [list(map(float, (graph.nodes[path[at]]["local"]["x_m"], graph.nodes[path[at]]["local"]["y_m"])))]
        metres = 0.0
        for i in run:
            for p in _forward(edges[i], path[i])[1:]:
                if p != points[-1]:
                    points.append(p)
            metres += edges[i]["length_m"]
        legs.append({"floor_id": graph.nodes[path[at]]["floor_id"], "points": points, "metres": _round2(metres)})
    changes = []
    for ride in rides:
        a, b = path[ride[0]], path[ride[-1] + 1]
        fa, fb = graph.nodes[a]["floor_id"], graph.nodes[b]["floor_id"]
        apart = graph.order.get(fb, 0) - graph.order.get(fa, 0)
        changes.append({"by": edges[ride[0]]["kind"], "from_floor_id": fa, "to_floor_id": fb, "from_node": a,
                        "to_node": b, "floors": abs(apart), "direction": "up" if apart > 0 else "down"})
    metres = seconds = 0.0
    for e in edges:
        metres += e["length_m"]
        seconds += e["seconds"]
    return {"from": start, "to": end, "accessible": accessible, "nodes": path, "metres": _round2(metres),
            "seconds": _round2(seconds), "legs": legs, "changes": changes,
            "steps": _steps(graph, path, edges, runs, starts, changes)}


def _steps(graph: Graph, path, edges, runs, starts, changes) -> list[dict]:
    """What to tell a person, step by step: each a kind, its values and its text in
    English (a system words them in its own language from the kind and values)."""
    first = graph.nodes[path[0]]
    here = graph.place_of(path[0])
    words = {"kiosk": "Start at the kiosk in {}", "entrance": "Start at the entrance into {}", "room": "Start in {}",
             "approach": "Start in {}", "door": "Start at the door of {}"}
    if first["kind"] in VERTICAL.values():
        text = f"Start at the {first['kind']}"
    else:
        text = words.get(first["kind"], "Start in {}").format(graph.label(here))
    steps = [{"kind": "start", "node": path[0], "node_kind": first["kind"], "place": here,
              "floor_id": first["floor_id"], "text": text}]
    goal = graph.place_of(path[-1])
    for n, (run, at) in enumerate(zip(runs, starts)):
        metres, by = 0.0, {}
        for i in run:
            e = edges[i]
            metres += e["length_m"]
            place = e["zone_id"] or e["space_id"]
            if place:
                by[place] = by.get(place, 0.0) + e["length_m"]
        last = n == len(runs) - 1
        m = _metres(metres)
        if run and m > 0:
            along = None
            for place, length in by.items():
                if along is None or length > by[along] or (length == by[along] and place < along):
                    along = place
            to = "destination" if last else changes[n]["by"]
            to_text = graph.label(goal) if last else f"the {to}"
            kind = (graph.places.get(along) or {}).get("type", "")
            if along is None or (last and along == goal):
                text = f"Walk {m} m to {to_text}"
            else:
                text = f"Walk {m} m {'along' if kind in THROUGH else 'through'} {graph.label(along)} to {to_text}"
            steps.append({"kind": "walk", "floor_id": graph.nodes[path[at]]["floor_id"], "metres": m, "along": along,
                          "to": to, "place": goal if last else None, "text": text})
        if not last:
            c = changes[n]
            name = (graph.floors.get(c["to_floor_id"]) or {}).get("name") or c["to_floor_id"]
            steps.append({"kind": "take", "by": c["by"], "from_floor_id": c["from_floor_id"],
                          "to_floor_id": c["to_floor_id"], "floors": c["floors"], "direction": c["direction"],
                          "text": f"Take the {c['by']} {c['direction']} to {name}"})
    side = "here" if len(path) == 1 else _side(graph, path, edges, runs[-1], starts[-1], goal)
    label = graph.label(goal)
    text = {"here": f"You are at {label}", "ahead": f"{_cap(label)} is ahead",
            "left": f"{_cap(label)} is on your left", "right": f"{_cap(label)} is on your right"}[side]
    steps.append({"kind": "arrive", "place": goal, "floor_id": graph.nodes[path[-1]]["floor_id"], "side": side,
                  "text": text})
    return steps


def _side(graph: Graph, path, edges, run, at, goal) -> str:
    """Which side the destination's door is on, as a person walks to it: from the way
    they walk before turning to it (over WALK_BACK_M) and the way into the room."""
    end = at + len(run)  # the run's last node
    for j in range(end - 1, at, -1):
        if graph.nodes[path[j]]["kind"] not in DOORS:
            continue
        if graph.place_of(path[j + 1]) != goal or graph.place_of(path[j - 1]) == goal:
            return "ahead"
        points = [[graph.nodes[path[at]]["local"]["x_m"], graph.nodes[path[at]]["local"]["y_m"]]]
        for i in range(at, j - 1):
            for p in _forward(edges[i], path[i])[1:]:
                if p != points[-1]:
                    points.append(p)
        back, q = WALK_BACK_M, points[0]
        for i in range(len(points) - 1, 0, -1):
            (x1, y1), (x0, y0) = points[i], points[i - 1]
            dx, dy = x0 - x1, y0 - y1
            seg = math.sqrt(dx * dx + dy * dy)
            if seg >= back:
                q = [x1 + dx * (back / seg), y1 + dy * (back / seg)]
                break
            back -= seg
            q = points[i - 1]
        wx, wy = points[-1][0] - q[0], points[-1][1] - q[1]
        door, inside = graph.nodes[path[j]]["local"], graph.nodes[path[j + 1]]["local"]
        rx, ry = inside["x_m"] - door["x_m"], inside["y_m"] - door["y_m"]
        if wx == 0 and wy == 0:
            return "ahead"
        dot = wx * rx + wy * ry
        cross = wx * ry - wy * rx
        if dot > 0 and abs(cross) <= 0.5 * dot:
            return "ahead"
        return "left" if cross > 0 else "right" if cross < 0 else "ahead"
    return "ahead"
