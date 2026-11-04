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

**A location's site** (format 0.13, FORMAT.md "Routes across a site"). A layout's package
has a network of its own (build_site_network): its land's areas walked as one ground
inside its boundary and one outside it (the road), as the zones of one room are (their
edges are lines on the ground, not walls; the boundary walls, other walls on the land and
the buildings' footprints are not walked), a gate's node in each gate with an approach
on each side, kiosks standing on the land, and an entrance where each building's door to
the outside on its ground floor meets its footprint's edge (site_entrances): the
building's door, in the site's frame, joined by a ``link`` to the same door's node in the
building's own package. A way across a site (site_route) is found on the layout's network
and its buildings' together, the same way as in a building: each link a change between
the land and a building (``enter``, ``leave``).
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import pickle
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, shape
from shapely.ops import nearest_points, polylabel, unary_union

from .geometry import as_polygons
from .georef import Georeferencer
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
CORNER_SLACK_M = 0.03  # the corners a way bends round, of the walking region drawn this much simpler
WALK_BACK_M = 2.0  # the way a person walks, before a door: over this much of the way
DECIMALS = 3  # points, in metres

# What a person walks through: rooms for passing through, and rooms they go to (a way
# through one costs ROOM_PENALTY_S each way in). The others are not walked: plant
# rooms, shafts, voids. FORMAT.md, "Navigation (0.8)".
PASS_TYPES = frozenset({"corridor", "lobby", "open_area", "elevator", "stairs", "escalator", "ramp", "parking",
                        "terrace", "balcony", "roof", "living_room", "dining_room"})
ROOM_TYPES = frozenset({"office", "room", "meeting_room", "restroom", "kitchen", "storage", "bedroom", "bathroom",
                        "dressing_room", "laundry", "prayer_room", "unspecified"})
WALKABLE = PASS_TYPES | ROOM_TYPES
VERTICAL = {"elevator": "lift", "stairs": "stairs", "escalator": "escalator", "ramp": "ramp"}  # type -> node kind
ARRIVALS = frozenset({"room", "lift", "stairs", "escalator", "ramp"})  # the node a place is arrived at
DOORS = frozenset({"door", "entrance", "gate"})
LINK = "link"  # (0.13) an edge from a building's entrance on the land to its door in the building's package
ENTRANCE = "entrance"  # a change through a link: into a building, or out of one
THROUGH = frozenset({"corridor", "ramp"})  # "along" it; anything else, "through" it
TYPE_WORDS = {"elevator": "lift", "unspecified": "room", "open_area": "open area", "meeting_room": "meeting room",
              "living_room": "living room", "dining_room": "dining room", "dressing_room": "dressing room",
              "prayer_room": "prayer room", "open_to_below": "void", "electrical_room": "electrical room",
              "idf_room": "IDF room"}


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
    way through the room's nodes is nearly as short as (a greedy spanner).

    Of the lines between its nodes and the region's inward corners, a coarse grid of the
    region first throws out those that surely leave it (most, in a long corridor), and
    the ways are found from every node at once over those inside (a corridor
    of hundreds of doors and corners in seconds, not a minute)."""
    if len(terminals) < 2:
        return []
    # the corners of the region drawn a little simpler (a wall drawn with many points
    # bends a way no more than its line does): a way may come CORNER_SLACK_M nearer a wall
    tol = region.buffer(CORNER_SLACK_M + 0.005)
    shapely.prepare(tol)
    ids = [t[0] for t in terminals]
    points = [t[1] for t in terminals]
    known = set(points)
    corners = [c for c in dict.fromkeys(_reflex_corners(region.simplify(CORNER_SLACK_M))) if c not in known]
    pts = np.array(points + corners, dtype=float)
    n, k = len(pts), len(points)
    ii, jj = np.triu_indices(n, 1)
    d = pts[jj] - pts[ii]
    length = np.hypot(d[:, 0], d[:, 1])
    real = length > 1e-9
    pair = np.flatnonzero(real)
    if len(pair) > GRID_PAIRS:
        pair = pair[_maybe_inside(tol, pts[ii[pair]], pts[jj[pair]])]
    seen = pair[shapely.covers(tol, shapely.linestrings(np.stack([pts[ii[pair]], pts[jj[pair]]], axis=1)))]
    seen = np.concatenate([seen, np.flatnonzero(~real)])
    u, v, w = np.r_[ii[seen], jj[seen]], np.r_[jj[seen], ii[seen]], np.r_[length[seen], length[seen]]
    dist, pred = _shortest_from(n, k, u, v, w)
    d = dist[:, :k]
    pairs = sorted((float(d[a, b]), ids[a], ids[b], a, b) for a in range(k) for b in range(a + 1, k)
                   if np.isfinite(d[a, b]))
    have = np.full((k, k), np.inf)
    np.fill_diagonal(have, 0.0)
    out = []
    for w_, _, _, a, b in pairs:
        if have[a, b] <= SPANNER_STRETCH * w_ + SPANNER_SLACK_M:
            continue
        have = np.minimum(have, np.minimum(have[:, a, None] + w_ + have[None, b, :],
                                           have[:, b, None] + w_ + have[None, a, :]))
        way = [b]
        while way[-1] != a:
            way.append(int(pred[a, way[-1]]))
            if way[-1] < 0 or len(way) > n:
                raise RuntimeError(f"no way back from {ids[b]} to {ids[a]}")
        line = [tuple(pts[i]) for i in reversed(way)]
        if ids[a] < ids[b]:
            out.append((ids[a], ids[b], line))
        else:
            out.append((ids[b], ids[a], line[::-1]))
    return out


GRID_PAIRS = 20000  # lines to look at beyond which a coarse grid of the region first throws some out
GRID_CELL_M = 0.2


def _maybe_inside(region, a, b, samples: int = 24) -> np.ndarray:
    """Whether each line a–b may be inside ``region``: False for those with a point in a
    cell of a GRID_CELL_M grid that is wholly outside it (more than two cells from it),
    which surely leave it; the rest are looked at exactly."""
    from PIL import Image, ImageDraw

    cell = GRID_CELL_M
    grown = region.buffer(cell * 2, join_style="mitre")
    x0, y0, x1, y1 = grown.bounds
    image = Image.new("1", (int((x1 - x0) / cell) + 2, int((y1 - y0) / cell) + 2), 0)
    draw = ImageDraw.Draw(image)
    at = lambda ring: [((x - x0) / cell - 0.5, (y - y0) / cell - 0.5) for x, y in ring.coords]  # noqa: E731 (a cell's middle at its index)
    for poly in sorted(as_polygons(grown), key=lambda p: -p.area):  # (one in another's hole after it)
        draw.polygon(at(poly.exterior), fill=1)
        for hole in poly.interiors:
            draw.polygon(at(hole), fill=0)
    inside = np.asarray(image, dtype=bool)
    t = (np.arange(samples) + 0.5) / samples
    out = np.ones(len(a), dtype=bool)
    for s in range(0, len(a), 100_000):
        p, q = a[s:s + 100_000], b[s:s + 100_000]
        cx = ((p[:, None, 0] + (q[:, None, 0] - p[:, None, 0]) * t - x0) / cell).astype(np.int64)
        cy = ((p[:, None, 1] + (q[:, None, 1] - p[:, None, 1]) * t - y0) / cell).astype(np.int64)
        out[s:s + 100_000] = inside[cy, cx].all(axis=1)
    return out


def _shortest_from(n: int, k: int, u, v, w) -> tuple[np.ndarray, np.ndarray]:
    """The shortest ways from points 0…k-1 to all n over the edges u→v (length w), all at
    once (rounds of every edge, Bellman–Ford): their lengths (k × n) and each point's
    point before it on one (-1: none)."""
    dist = np.full((k, n), np.inf)
    dist[np.arange(k), np.arange(k)] = 0.0
    pred = np.full((k, n), -1, dtype=np.int64)
    if not len(u):
        return dist, pred
    order = np.argsort(v, kind="stable")
    u, v, w = u[order], v[order], w[order]
    changed = np.zeros(n, dtype=bool)
    changed[:k] = True
    while True:  # each round, the edges from points a way to changed in the last
        e = np.flatnonzero(changed[u])
        if not len(e):
            break
        ue, ve = u[e], v[e]
        starts = np.flatnonzero(np.r_[True, ve[1:] != ve[:-1]])
        heads = ve[starts]
        best = np.minimum.reduceat(dist[:, ue] + w[e], starts, axis=1)
        now = dist[:, heads]
        better = best < now - 1e-12
        dist[:, heads] = np.where(better, best, now)
        changed[:] = False
        changed[heads[better.any(axis=0)]] = True
    # each point's point before it: an edge on a shortest way to it, from a point nearer
    # (or as near, over an edge of no length: from the source, or from a point numbered
    # lower, so that two points in one place never lead to each other)
    via, here, there = dist[:, u] + w, dist[:, u], dist[:, v]
    with np.errstate(invalid="ignore"):  # (inf − inf: not on one)
        tight = np.isfinite(via) & (np.abs(via - there) <= 1e-9 * np.maximum(1.0, there))
        nearer = here < there - 1e-12
    source = np.arange(k)[:, None]
    on = tight & (nearer | (u[None, :] == source) | (u[None, :] < v[None, :])) & (v[None, :] != source)
    s, e = np.nonzero(on)
    pred[s, v[e]] = u[e]
    return dist, pred


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
        _floor_kept(ws, f_id, cat, net, stack_key, placement)
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


FLOOR_NETWORKS_KEPT = 32  # floors whose part of the walking network is kept (the latest of each)
_kept_floors: OrderedDict[str, tuple[str, bytes]] = OrderedDict()  # floor ID -> (what it was worked out from, its part)
_kept_lock = threading.Lock()


def _floor_kept(ws, f_id: str, cat, net: _Network, stack_key: dict, placement) -> None:
    """_floor, kept: a floor's part of the network is worked out again only when what
    it is worked out from changed (_floor_said), so a building whose floors hold a
    thousand rooms each is ready in a second, not half a minute, after a change to one
    of them (or to none). A floor's part only adds to the network (its nodes are its
    own), so it is the same worked out alone."""
    said = _floor_said(ws, f_id, cat, stack_key, placement)
    with _kept_lock:
        kept = _kept_floors.get(f_id)
        if kept is not None:
            _kept_floors.move_to_end(f_id)
    if kept is not None and kept[0] == said:
        nodes, edges, places = pickle.loads(kept[1])  # (a copy: the network it goes into is added to)
    else:
        part = _Network(net.georef)
        _floor(ws, f_id, cat, part, stack_key)
        nodes, edges, places = part.nodes, part.edges, part.places
        with _kept_lock:
            _kept_floors[f_id] = (said, pickle.dumps((nodes, edges, places), protocol=pickle.HIGHEST_PROTOCOL))
            _kept_floors.move_to_end(f_id)
            while len(_kept_floors) > FLOOR_NETWORKS_KEPT:
                _kept_floors.popitem(last=False)
    net.nodes.update(nodes)
    net.edges.update(edges)
    net.places.update(places)


def _floor_said(ws, f_id: str, cat, stack_key: dict, placement) -> str:
    """All a floor's part of the network is worked out from (_floor), hashed: its rooms,
    zones and openings with their corrections and their lifts' and stairs' keys, its
    kiosks and their types, and where its building is on the map."""
    h = hashlib.sha256(placement.model_dump_json().encode())
    for r in sorted(ws.floor_objects(f_id), key=lambda r: r.id):
        h.update(r.model_dump_json().encode())
        h.update(json.dumps([ws.effective(r), stack_key.get(r.id)], sort_keys=True, default=str).encode())
    for it in sorted(ws.floor_items(f_id), key=lambda i: i.id):
        if _is_kiosk(it):
            t = cat.get(it.type)
            h.update(it.model_dump_json().encode())
            h.update(t.model_dump_json().encode() if t is not None else b"-")
    return h.hexdigest()


def _is_kiosk(it) -> bool:
    return it.type == "KIOSK" or it.type.startswith("KIOSK-")


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
        if not _is_kiosk(it):
            continue
        front = _kiosk_front(it, cat)
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
        x0, y0, x1, y1 = u.area.bounds
        for oid, other in units.items():
            ox0, oy0, ox1, oy1 = other.area.bounds
            if oid == sid or ox0 > x1 + 0.3 or ox1 < x0 - 0.3 or oy0 > y1 + 0.3 or oy1 < y0 - 0.3:
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


# ---- a location's site (format 0.13) ----------------------------------------------------

# what a person walks on a location's land: its areas but pools (and a ramp, an open area,
# an area a person drew with no type); the edges between areas are lines on the ground
LAND_WALKED = frozenset({"yard", "parking", "driveway", "garden", "courtyard", "path", "gate_area", "road", "terrace",
                         "ramp", "open_area", "unspecified"})
CLOSE_M = 0.06  # areas side by side are one ground: the hairline between them closed
ENTRANCE_REACH_M = 2.0  # a door to the outside this near its footprint's edge is a way in from the land
STEP_M = 0.02  # a floor this much or more above the ground at its door: a step into it
RISE_MAX_M = 3.0  # a floor further from the ground than this: levels of two datums, not a step
RAMP_REACH_M = 1.5  # a ramp on the land this near an entrance takes its step
VEHICLE_GATE_M = 2.5  # a gate this wide or wider is a vehicle gate (as a step calls it)
LAND_NODES = frozenset({"room", "approach", "kiosk", "gate", "entrance"})  # what a point on the land starts at


@dataclass
class Entrance:
    """Where a building is entered from its location's land: a door to the outside on its
    ground floor, at its footprint's edge."""

    building_id: str
    footprint_id: str
    opening_id: str  # the door, in its building
    floor_id: str  # its floor (the building's ground floor)
    door: tuple[float, float]  # its middle, in the site's frame
    edge: tuple[float, float]  # where the way to it crosses the footprint's edge, in the site's frame
    along: tuple[float, float]  # the footprint's edge there
    rise: float | None  # how far the floor stands above the ground there, metres; None: not known


@dataclass
class _Ground:
    """The land a person walks on one side of its boundary (inside the plot, or the road
    outside it): its areas together, as the zones of one room."""

    area: object
    areas: list  # (record, shape, type) of its areas
    region: object = None
    terminals: list = field(default_factory=list)  # (node id, point)

    def area_at(self, p) -> tuple:
        """The area a point is in (the nearest, when it is in none; of those as near, the
        first by ID)."""
        point = Point(p)
        hit = [a for a in self.areas if a[1].covers(point)]
        if not hit:
            hit = sorted(self.areas, key=lambda a: (a[1].distance(point), a[0].id))[:1]
        return min(hit, key=lambda a: a[0].id)


def to_site(ws, building_id: str):
    """A function from a point of a building's own frame to its location's site's frame
    (where its layout is drawn); None when the building is on the map by itself and its
    location is not."""
    from .export import on_site, site_placement, site_positions

    loc = ws.location(building_id.rsplit("-", 1)[0])
    b = ws.building(building_id)
    if b.placement is None:
        pos = site_positions(loc)[b.code]
        return lambda x, y: on_site(pos, x, y)
    if loc.placement is None:
        return None
    there, site = Georeferencer(b.placement), Georeferencer(site_placement(loc))
    return lambda x, y: site.local(*there.lonlat(x, y))


def _edge_at(poly, p) -> tuple[tuple[float, float], tuple[float, float]]:
    """The point of a polygon's edge nearest p, and the edge's direction there."""
    ring = poly.boundary
    q = nearest_points(ring, Point(p))[0]
    best = None
    for line in getattr(ring, "geoms", [ring]):
        cs = list(line.coords)
        for a, b in zip(cs, cs[1:]):
            d = LineString([a, b]).distance(q)
            if best is None or d < best[0]:
                best = (d, (b[0] - a[0], b[1] - a[1]))
    return (q.x, q.y), best[1] if best else (0.0, 0.0)


def _rise(r) -> float | None:
    """How far a footprint's floor stands above the ground at its doors, as its site plan
    writes their levels; None when it does not write both (or writes levels of two datums)."""
    levels = r.levels or {}
    floor, ground = levels.get("floor"), levels.get("ground")
    if floor is None or ground is None or abs(floor - ground) > RISE_MAX_M:
        return None
    return round(floor - ground, 3)


def site_entrances(ws, layout_id: str, catalogue=None) -> tuple[list[Entrance], list[dict]]:
    """Where each building standing on a layout is entered from the land: each of its doors
    to the outside on its ground floor (its network's entrances: a door or opening joining
    one of its rooms to nothing) whose middle, on the site, is within ENTRANCE_REACH_M of
    its footprint's edge. And, for each footprint's building with none, why: notes, each
    {building_id, footprint_id, why} (why in words, for a person)."""
    from .catalogue import default_catalogue
    from .export import footprint_of
    from .types import FloorType

    cat = catalogue or default_catalogue()
    out: list[Entrance] = []
    notes: list[dict] = []
    linked: set[str] = set()
    for r in sorted(ws.floor_objects(layout_id), key=lambda r: r.id):
        if r.kind != "footprint" or not r.geometry or ws.effective(r)["ignored"]:
            continue
        b_id = footprint_of(ws, r)
        if b_id is None:
            continue
        try:
            b = ws.building(b_id)
        except KeyError:
            continue
        name = b.name or r.name or b_id

        def note(why: str) -> None:
            notes.append({"building_id": b_id, "footprint_id": r.id, "why": f"{name}: {why}"})

        ground = {f"{b_id}-{f.code}" for f in b.floors if f.ordinal == 0 and f.type == FloorType.FLOOR}
        if not ground or not any(ws.floor_objects(fid) for fid in ground):
            note("its ground floor is not read yet: no way into it from the land" if ground
                 else "it has no ground floor yet: no way into it from the land")
            continue
        place = to_site(ws, b_id)
        if place is None:
            note("it is placed on the map by itself and its location is not: where its doors are on the land "
                 "is not known")
            continue
        net = build_network(ws, b_id, cat)
        doors = [n for n in net["nodes"] if n["kind"] == "entrance" and n["floor_id"] in ground
                 and n["opening_id"] not in linked]
        if not doors:
            note("no door to the outside on its ground floor (a door joining one of its rooms to the outside, in its "
                 "outer wall): no way into it from the land. Draw one in Review")
            continue
        fp = shape(r.geometry).buffer(0)
        rise = _rise(r)
        found, nearest = 0, math.inf
        for n in doors:
            p = _pt(place(n["local"]["x_m"], n["local"]["y_m"]))
            d = fp.boundary.distance(Point(p))
            nearest = min(nearest, d)
            if d > ENTRANCE_REACH_M:
                continue
            q, along = _edge_at(fp, p)
            out.append(Entrance(b_id, r.id, n["opening_id"], n["floor_id"], p, _pt(q), along, rise))
            linked.add(n["opening_id"])
            found += 1
        if not found:
            note(f"none of its doors to the outside is at its footprint's edge (the nearest is {nearest:.1f} m from "
                 "it): is it placed on its footprint?")
    return out, notes


def _kiosk_front(it, cat) -> tuple[float, float]:
    """Where people stand before a kiosk's screen: KIOSK_STAND_M in front of it."""
    t = cat.get(it.type)
    depth = t.depth if t else 0.45
    r = math.radians(it.rotation)
    fx, fy = math.sin(r), -math.cos(r)
    return it.x + fx * (depth / 2 + KIOSK_STAND_M), it.y + fy * (depth / 2 + KIOSK_STAND_M)


def _middle_of(o) -> tuple[tuple[float, float], tuple[float, float]]:
    """An opening's middle, and its direction along its wall (none known: (0, 0))."""
    if o.span and len(o.span) == 2:
        (x0, y0), (x1, y1) = o.span
        return _pt(((x0 + x1) / 2, (y0 + y1) / 2)), (x1 - x0, y1 - y0)
    c = o.geometry["coordinates"]
    return _pt((c[0], c[1])), (0.0, 0.0)


def _gate_place(ws, o, layout_id: str) -> dict:
    """A gate as a step calls it: its name, else "the vehicle gate" or "the pedestrian gate"
    by its width."""
    eff = ws.effective(o)
    kind = "vehicle gate" if (o.width or 0.0) >= VEHICLE_GATE_M else "pedestrian gate"
    return {"id": o.id, "kind": "opening", "space_id": None, "floor_id": layout_id, "type": "gate",
            "label": label_of(eff["name"], eff["number"], kind)}


def build_site_network(ws, layout_id: str, catalogue=None, notes: list | None = None) -> dict:
    """A location's layout's walking network (format 0.13), as its package's
    navigation.json holds it: positions in the site's frame. Its areas walked (LAND_WALKED)
    as one ground inside the plot and one outside it (the road), the hairlines between them
    closed, its walls and every footprint left out of them; a ``room`` node in each area, a
    ``gate`` node in each gate with an ``approach`` on each side, a ``kiosk`` before each
    kiosk on the land, and an ``entrance`` at each building's door to the outside at its
    footprint's edge (site_entrances: its node at the door's middle on the site, its
    approach on the land in front of the footprint's edge), joined to the building's own
    node of that door (door:<opening>, in the building's package) by a ``link`` of no
    length, not accessible where a step into it is known (its floor above the ground, as the
    site plan writes their levels, and no ramp at it). ``notes`` is told why a building has
    no way in."""
    from .catalogue import default_catalogue
    from .export import site_placement
    from .ids import parse_id

    cat = catalogue or default_catalogue()
    loc = ws.location(parse_id(layout_id).prefix("location"))
    f = ws.floor(layout_id)
    net = _Network(Georeferencer(site_placement(loc)))
    objects = sorted(ws.floor_objects(layout_id), key=lambda r: r.id)
    plot = shape(f.outline).buffer(0) if f.outline else None
    standing = [shape(r.geometry).buffer(0) for r in objects
                if r.kind == "footprint" and r.geometry and not ws.effective(r)["ignored"]]
    blocked = unary_union([*standing, *([shape(f.walls).buffer(0)] if f.walls else [])])
    sides: dict[str, list] = {"in": [], "out": []}
    walked = []
    for r in objects:
        if r.kind != "space" or not r.geometry:
            continue
        eff = ws.effective(r)
        if eff["hidden"] or eff["ignored"] or eff["type"] not in LAND_WALKED:
            continue
        g = shape(r.geometry).buffer(0)
        if g.is_empty:
            continue
        outside = plot is not None and plot.intersection(g).area < 0.5 * g.area
        sides["out" if outside else "in"].append((r, g, eff["type"]))
        walked.append((r, g, eff["type"]))
        net.places[r.id] = _place(ws, r, layout_id)
    grounds: list[_Ground] = []
    ground_of: dict[str, _Ground] = {}
    for side in ("in", "out"):
        if not sides[side]:
            continue
        area = unary_union([g.buffer(CLOSE_M, join_style="mitre") for _, g, _ in sides[side]])
        area = area.buffer(-CLOSE_M, join_style="mitre").difference(blocked).buffer(0)
        if area.is_empty:
            continue
        gr = _Ground(area=area, areas=sides[side], region=walking_region(area))
        grounds.append(gr)
        for r, _, _ in sides[side]:
            ground_of[r.id] = gr
    # where people arrive in each area
    for gr in grounds:
        for r, g, _ in gr.areas:
            p = _inside(gr.region, label_point(g))
            gr.terminals.append((net.node(f"room:{r.id}", "room", layout_id, p, r.id, None), p))
    # kiosks standing on the land: where people stand before the screen
    for it in sorted(ws.floor_items(layout_id), key=lambda i: i.id):
        if not _is_kiosk(it):
            continue
        front = _kiosk_front(it, cat)
        gr = next((g for g in grounds if g.area.covers(Point(front))), None) or \
            next((g for g in grounds if g.area.covers(Point(it.x, it.y))), None)
        if gr is None:
            net.node(f"kiosk:{it.id}", "kiosk", layout_id, _pt(front), None, None, item_id=it.id)
            continue
        p = _inside(gr.region, front)
        gr.terminals.append((net.node(f"kiosk:{it.id}", "kiosk", layout_id, p, gr.area_at(p)[0].id, None,
                                      item_id=it.id), p))
    # gates: a node in each, and one in front of it in each area it opens onto
    for o in objects:
        if o.kind != "opening" or ws.effective(o)["ignored"] or ws.effective(o)["type"] == "window":
            continue
        opens_onto = [c for c in dict.fromkeys(o.connects) if c in ground_of]
        if not opens_onto:
            continue  # into nothing walked
        middle, along = _middle_of(o)
        gate = net.node(f"door:{o.id}", "gate", layout_id, middle, None, None, opening_id=o.id, spaces=opens_onto)
        net.places[o.id] = _gate_place(ws, o, layout_id)
        for sid in opens_onto:
            gr = ground_of[sid]
            own = next(g for r, g, _ in gr.areas if r.id == sid)
            p = approach_point(own, gr.region, middle, along)
            nid = net.node(f"approach:{o.id}@{sid}", "approach", layout_id, p, sid, None, opening_id=o.id)
            gr.terminals.append((nid, p))
            net.edge(gate, nid, "door", [middle, p], space_id=sid)
    # the buildings' entrances: at each building's door, on the site
    entrances, said = site_entrances(ws, layout_id, cat)
    for e in entrances:
        b = ws.building(e.building_id)
        near = [(gr.area.distance(Point(e.edge)), i) for i, gr in enumerate(grounds)]
        if not near or min(near)[0] > 0.5:
            said.append({"building_id": e.building_id, "footprint_id": e.footprint_id,
                         "why": f"{b.name}: its door {e.opening_id} opens onto no area of the land a person walks"})
            continue
        gr = grounds[min(near)[1]]
        p = approach_point(gr.area, gr.region, e.edge, e.along)
        area_id = gr.area_at(p)[0].id
        steps = None if e.rise is None else e.rise >= STEP_M
        if any(t == "ramp" and g.distance(Point(e.edge)) <= RAMP_REACH_M for _, g, t in walked):
            steps = False  # a ramp takes its step
        node = net.node(f"entrance:{e.opening_id}", "entrance", layout_id, e.door, None, None,
                        opening_id=e.opening_id, spaces=[e.footprint_id], footprint_id=e.footprint_id,
                        building_id=e.building_id, steps=steps,
                        rise_m=round(e.rise, 2) if e.rise is not None else None)
        nid = net.node(f"approach:{e.opening_id}@{area_id}", "approach", layout_id, p, area_id, None,
                       opening_id=e.opening_id)
        gr.terminals.append((nid, p))
        inside = shape(ws.objects[e.footprint_id].geometry).buffer(0).covers(Point(e.door))
        line = [e.door, e.edge, p] if inside and math.dist(e.door, e.edge) > 0.05 else [e.door, p]
        net.edge(node, nid, "door", line, space_id=area_id)
        net.edge(f"door:{e.opening_id}", node, LINK, [], length=0.0, seconds=0.0, accessible=steps is not True)
        net.places[e.footprint_id] = {"id": e.footprint_id, "kind": "footprint", "space_id": None,
                                      "floor_id": layout_id, "type": "building",
                                      "label": label_of(b.name, None, "building")}
    if notes is not None:
        notes.extend(said)
    # across each ground
    for gr in grounds:
        for a, b, line in walks(gr.region, gr.terminals):
            path = LineString(line) if len(line) > 1 and _length(line) > 0 else Point(line[0])
            area = min(gr.areas, key=lambda x: (-path.intersection(x[1]).length, x[0].id))[0].id
            net.edge(a, b, "walk", line, space_id=area)
    return {
        "speed_m_s": SPEED_M_S,
        "layout": layout_id,
        "buildings": [],
        "floors": [{"id": layout_id, "building_id": None, "name": f.name, "ordinal": f.ordinal,
                    "elevation": f.elevation}],
        "places": sorted(net.places.values(), key=lambda p: p["id"]),
        "nodes": sorted(net.nodes.values(), key=lambda n: n["id"]),
        "edges": sorted(net.edges.values(), key=lambda e: (e["from"], e["to"])),
    }


# ---- the way ----------------------------------------------------------------------------

def floor_of_id(i: str) -> str:
    return i.rsplit("-", 1)[0]


class Graph:
    """navigation.json, ready to route on: a package's, or a site's (site_graph: a layout's
    and its buildings' together)."""

    def __init__(self, nav: dict):
        self.nav = nav
        self.layout = nav.get("layout")  # (a layout's, 0.13) its ID: the floor of the land
        self.nodes = {n["id"]: n for n in nav.get("nodes", [])}
        self.places = {p["id"]: p for p in nav.get("places", [])}
        self.floors = {f["id"]: f for f in nav.get("floors", [])}
        self.order = {f["id"]: i for i, f in enumerate(nav.get("floors", []))}
        self.adjacent: dict[str, list[tuple[str, dict]]] = {i: [] for i in self.nodes}
        for e in nav.get("edges", []):
            if e["from"] in self.nodes and e["to"] in self.nodes:  # (a link: both packages', or neither)
                self.adjacent[e["from"]].append((e["to"], e))
                self.adjacent[e["to"]].append((e["from"], e))

    def ends(self, ref: str, items=None) -> list[str]:
        """The nodes a place is: a node's ID; a space's, or a zone's (where it is
        arrived at: its zones', for a space divided into zones); an opening's (its door's
        or gate's node); an item's (a kiosk's own node, else where it stands: ``items``,
        item ID -> its feature or properties)."""
        if ref in self.nodes:
            return [ref]
        found = sorted(n["id"] for n in self.nodes.values()
                       if n["kind"] in ARRIVALS and ref in (n["space_id"], n["zone_id"]))
        if found:
            return found
        if f"door:{ref}" in self.nodes:
            return [f"door:{ref}"]
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
        if n["kind"] == "gate":  # a gate is a place of its own
            return n.get("opening_id")
        if n["kind"] in DOORS:
            spaces = n.get("spaces") or []
            return spaces[0] if spaces else None
        return n["zone_id"] or n["space_id"]

    def nearest(self, x: float, y: float) -> str:
        """The land's node nearest a point of the site (metres of the site's frame): of its
        rooms, approaches, kiosks, gates and entrances, by straight distance; of those as
        near, the first by ID. KeyError when the network has no land."""
        best = None
        for n in self.nodes.values():
            if self.layout is None or n["floor_id"] != self.layout or n["kind"] not in LAND_NODES:
                continue
            dx, dy = n["local"]["x_m"] - x, n["local"]["y_m"] - y
            d = dx * dx + dy * dy
            if best is None or d < best[0] or (d == best[0] and n["id"] < best[1]):
                best = (d, n["id"])
        if best is None:
            raise KeyError("no land in the network: a point on it is not known")
        return best[1]

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
        if edges[ride[0]]["kind"] == LINK:  # (0.13) between the land and a building: in from its entrance, or out
            into = bool(graph.nodes[a].get("footprint_id"))
            changes.append({"by": ENTRANCE, "from_floor_id": fa, "to_floor_id": fb, "from_node": a, "to_node": b,
                            "floors": 0, "direction": "in" if into else "out"})
            continue
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
             "approach": "Start in {}", "door": "Start at the door of {}", "gate": "Start at {}"}
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
            if c["by"] == ENTRANCE:  # (0.13) into a building from the land, or out of it
                land = graph.nodes[c["from_node"] if c["direction"] == "in" else c["to_node"]]
                building = (land.get("spaces") or [None])[0]
                label = (graph.places.get(building) or {}).get("label") or "the building"
                into = c["direction"] == "in"
                steps.append({"kind": "enter" if into else "leave", "building_id": land.get("building_id"),
                              "place": building, "from_floor_id": c["from_floor_id"], "to_floor_id": c["to_floor_id"],
                              "text": f"Go into {label}" if into else f"Go out of {label}"})
                continue
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


# ---- a way across a site (format 0.13) --------------------------------------------------

def site_graph(layout_nav: dict, building_navs: list[dict]) -> Graph:
    """A site's walking network, ready to route on: its layout's network (its package's
    navigation.json) and its buildings' (theirs), as one. Each link from an entrance on
    the land to its door in a building joins them where that building's network is given;
    a building not given is not reached."""
    navs = sorted(building_navs, key=lambda n: (n.get("buildings") or [""])[0])
    merged = {"speed_m_s": layout_nav.get("speed_m_s", SPEED_M_S), "layout": layout_nav.get("layout"),
              "buildings": sorted(b for n in navs for b in n.get("buildings", [])),
              "floors": [], "places": [], "nodes": [], "edges": []}
    for nav in [layout_nav, *navs]:  # (the land's floor first, then each building's floors in order)
        for key in ("floors", "places", "nodes", "edges"):
            merged[key] += nav.get(key, [])
    return Graph(merged)


def _point(ref) -> tuple[float, float] | None:
    """A point on the land as given: (x, y), [x, y] or {"x_m", "y_m"}; None for an ID."""
    if isinstance(ref, dict) and "x_m" in ref and "y_m" in ref:
        return float(ref["x_m"]), float(ref["y_m"])
    if isinstance(ref, (tuple, list)) and len(ref) == 2 and all(isinstance(v, (int, float)) for v in ref):
        return float(ref[0]), float(ref[1])
    return None


def site_route(layout_nav: dict | Graph, building_navs: list[dict] = (), start=None, end=None, *,
               accessible: bool = False, items=None, notes: list[dict] = ()) -> dict:
    """The way across a location's site: from a gate, a kiosk, an area or a point on its land
    (a point: the land's node nearest it, Graph.nearest; ``from`` is that node), or a room or
    item in one of its buildings, to any of those, on the layout's network and its
    buildings' together (site_graph; or a Graph already made of them). As route() gives a
    way in a building, with each way into or out of a building a change ``by`` entrance
    (``direction`` in or out) and a step ``enter`` or ``leave``: its legs on the land in the
    site's frame, in a building in the building's. ``items``: item ID -> its feature, of
    every package. NoRoute when there is none, saying why where it can (``notes``: why a
    building has no way in, site_entrances'); KeyError for what the networks do not know."""
    graph = layout_nav if isinstance(layout_nav, Graph) else site_graph(layout_nav, list(building_navs))
    ends = []
    for ref in (start, end):
        p = _point(ref)
        ends.append(graph.nearest(*p) if p is not None else ref)
    try:
        return route(graph, ends[0], ends[1], accessible=accessible, items=items)
    except NoRoute as e:
        raise NoRoute(_why_no_way(graph, ends, accessible, items, notes) or str(e)) from None


def _why_no_way(graph: Graph, ends: list[str], accessible: bool, items, notes) -> str | None:
    """Why there is no way across a site, in words: a building at either end with no way in
    from the land (as notes say, or none of its entrances on the land), or, without stairs,
    steps known at the entrances the way would take."""
    for ref in ends:
        try:
            nodes = graph.ends(ref, items)
        except KeyError:
            continue
        floor = graph.floors.get(graph.nodes[nodes[0]]["floor_id"]) or {}
        b_id = floor.get("building_id")
        if not b_id:
            continue
        said = next((n["why"] for n in notes if n.get("building_id") == b_id), None)
        if said:
            return f"no way into it from the land: {said}"
        if not any(n.get("building_id") == b_id and n["kind"] == "entrance" and n.get("footprint_id")
                   for n in graph.nodes.values()):
            return f"no way into {b_id} from the land: the site's network has no entrance into it"
    if accessible:
        try:
            way = route(graph, ends[0], ends[1], items=items)
        except (NoRoute, KeyError):
            return None
        for a, b in zip(way["nodes"], way["nodes"][1:]):
            e = _edge(graph, a, b)
            if e["kind"] == LINK and not e["accessible"]:
                land = graph.nodes[a if graph.nodes[a].get("footprint_id") else b]
                label = graph.label((land.get("spaces") or [None])[0])
                rise = land.get("rise_m")
                return (f"no way without stairs or steps: the entrance into {label} has a step"
                        + (f" (its floor {rise:g} m above the ground, as the site plan writes their levels)"
                           if rise is not None else ""))
    return None
