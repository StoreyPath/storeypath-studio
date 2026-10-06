"""Finding spaces in drawings that have walls but no room outlines.

Everything on the wall layers (walls, windows, columns) is merged into one wall
mass, and the cores of walls drawn as two lines are filled in. The openings in it
are then closed:

- where a door is drawn: along the closed position of its swing, or across the
  extent of a door block that has no swing;
- where a line on a door or window layer runs from one wall to another: glazing
  (straight or curved) or a door leaf drawn closed;
- where a wall stops and another wall faces its end within ``max_doorway``: a
  doorway or window with nothing drawn in it; or within ``max_opening`` with
  glazing drawn in the gap: a window;
- around the outside, wider gaps up to ``max_opening`` are spanned by the building
  envelope, so the room behind a missing door is not lost.

Each region left enclosed is a space. Wall thickness and direction do not matter:
curved and diagonal walls are handled like straight ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from .geometry import as_polygons, closing
from .profile import WallsConfig

SNAP_M = 0.03  # wall lines this close (×2) are joined; also the drawn line width
EXTERIOR_SHARE = 0.25  # a region with more of its edge than this open to the outside is not a space
SIMPLIFY_M = 0.005
MIN_OPEN_M = 0.2  # shorter stretches of envelope along a space are noise
WALL_END_MIN_SIDE_M = 0.2  # a wall end's sides run back along the wall at least this far
WALL_PIECE_MIN_M = 1.0  # smaller loose pieces (columns, scraps) have no wall ends
SWING_BAR_M = 0.1  # half-thickness of the bar that seals a door along its closed leaf


@dataclass
class DoorSwing:
    """A door's swing arc: the hinge and the arc's two ends (leaf open and closed)."""

    hinge: Point
    ends: tuple[Point, Point]


@dataclass
class DoorShape:
    """A door as drawn: its extent, and its swing(s) when it has them (two for a
    double door). ``span`` (jamb to jamb) and ``width`` are found from the swings."""

    box: Polygon
    swings: list[DoorSwing] = field(default_factory=list)
    span: LineString | None = None
    width: float | None = None


@dataclass
class Fabric:
    """What the building is made of, as drawn: the walls with their door and window
    gaps (for modelling in 3D), and the windows found in those gaps."""

    walls: object  # (Multi)Polygon at the drawn thickness, meters
    windows: list[LineString]  # each window's span, jamb to jamb
    thickness: float | None = None


def read_fabric(lines: list, fills: list, doors: list[DoorShape], opening_lines: list, cfg: WallsConfig,
                mass=None) -> Fabric:
    """The walls as drawn and the windows in them. Also measures each door's span."""
    mass = wall_mass(lines, fills, cfg) if mass is None else mass
    if mass.is_empty:
        return Fabric(mass, [])
    # The mass is widened by SNAP_M on each side; back to the drawn faces. Walls drawn
    # as single lines have no thickness of their own: they are given 10 cm.
    walls = mass.buffer(-SNAP_M, join_style="mitre")
    if lines:
        single = shapely.union_all(lines).difference(walls.buffer(0.01))
        if not single.is_empty:
            walls = unary_union([walls, single.buffer(0.05, cap_style="flat", join_style="mitre")])
    for d in doors:
        _door_bars(d, mass)  # sets span and width
    near_door = shapely.union_all([d.box.buffer(0.3) for d in doors]) if doors else Polygon()
    glass = [ln for ln in _connectors(mass, opening_lines, cfg.max_opening) if not ln.intersects(near_door)] \
        if opening_lines else []
    return Fabric(walls, _one_per_window(glass))


def _one_per_window(lines: list) -> list[LineString]:
    """Glazing is drawn as two or three parallel lines: one span per window."""
    if not lines:
        return []
    groups = as_polygons(shapely.union_all(shapely.buffer(np.array(lines, dtype=object), 0.15)))
    out = []
    for g in groups:
        members = [ln for ln in lines if ln.intersects(g)]
        longest = max(members, key=lambda ln: ln.length)
        c = np.asarray(longest.coords)
        out.append(LineString([c[0], c[-1]]))
    return out


@dataclass
class WallSpaces:
    polygons: list[Polygon]
    outline: Polygon | MultiPolygon | None
    pockets: int  # regions dropped as open to the outside
    doorways: list[Polygon] = field(default_factory=list)  # openings closed with no door drawn
    open_edges: object = None  # where spaces meet the outside with no door or window drawn
    fabric: Fabric | None = None


def open_issue(polygon: Polygon, open_edges) -> list[str]:
    """The review note for a space that meets the outside with nothing drawn."""
    if open_edges is None or open_edges.is_empty:
        return []
    length = polygon.boundary.buffer(SNAP_M * 2).intersection(open_edges).length
    return [f"open to the outside through {length:.1f} m with no door or window drawn"] if length > MIN_OPEN_M else []


def wall_mass(lines: list, fills: list[Polygon], cfg: WallsConfig):
    """All walls as one area: lines and fills widened by SNAP_M, cores filled."""
    parts = list(shapely.buffer(lines, SNAP_M, cap_style="square", join_style="mitre")) if lines else []
    parts += list(shapely.buffer(fills, SNAP_M, join_style="mitre")) if fills else []
    mass = shapely.union_all(parts) if parts else Polygon()
    return closing(mass, cfg.max_thickness / 2) if not mass.is_empty else mass


def spaces_from_walls(
    lines: list,
    fills: list[Polygon],
    doors: list[DoorShape],
    opening_lines: list,
    label_points: list[Point],
    cfg: WallsConfig,
    min_area: float,
) -> WallSpaces:
    """Spaces enclosed by walls. ``lines`` are wall-layer linework, ``fills`` solid
    wall areas (from hatches), ``doors`` the doors found, ``opening_lines`` the
    linework on door and window layers (glazing tells a window from a gap)."""
    mass = wall_mass(lines, fills, cfg)
    if mass.is_empty:
        return WallSpaces([], None, 0)
    seals = [bar for d in doors for bar in _door_bars(d, mass)]
    built = mass
    seals += list(shapely.buffer(_connectors(mass, opening_lines, cfg.max_opening), SNAP_M)) if opening_lines else []
    mass = unary_union([mass, *seals]) if seals else mass
    mass = _bridge_door_boxes(mass, [d.box for d in doors if not d.swings], cfg.max_thickness)
    openings = shapely.union_all(opening_lines) if opening_lines else None
    mass, bridges = _bridge_wall_ends(mass, cfg.max_doorway, cfg.max_opening, cfg.max_thickness, openings)
    near_door = shapely.union_all([d.box.buffer(0.2) for d in doors]) if doors else Polygon()
    doorways = [b for b, glazed in bridges if not glazed and not b.intersects(near_door)]
    glazed_gaps = [b for b, glazed in bridges if glazed]

    envelope = _envelope(mass, cfg.max_opening)
    edge = envelope.boundary.buffer(SNAP_M)
    glazing = openings.buffer(cfg.max_thickness / 2 + 0.05) if openings is not None else None
    spaces, pockets, open_edges = [], [], []
    regions = [r for r in as_polygons(envelope.difference(mass))
               if r.area >= min_area and not r.buffer(-cfg.min_width / 2).is_empty]
    around = _surrounding(regions, envelope.area, label_points)
    ways = unary_union([*seals, *(b for b, _ in bridges)]) if seals or bridges else None  # the windows and doors in its gaps
    around |= _within_the_land_line(regions, envelope, around, built,
                                    ways.buffer(cfg.max_thickness / 2 + 0.05) if ways is not None else None)
    for k, region in enumerate(regions):
        if k in around:
            pockets.append(region)  # the garden inside a plot wall, a sheet's frame: outside
            continue
        # Edges along the envelope rather than walls: open to the outside, unless
        # glazing is drawn there (a window).
        stretch = region.boundary.intersection(edge)
        if glazing is not None and not stretch.is_empty:
            stretch = stretch.difference(glazing)
        if stretch.length / region.boundary.length > EXTERIOR_SHARE and not any(
            region.contains(p) for p in label_points
        ):
            pockets.append(region)  # a recess in the facade
            continue
        open_edges.append(stretch)
        # Region edges sit SNAP_M off the wall lines; put them back on the lines.
        spaces += as_polygons(region.buffer(SNAP_M, join_style="mitre").simplify(SIMPLIFY_M))

    outline = envelope.difference(unary_union(pockets)) if pockets else envelope
    outline = outline.buffer(-SNAP_M, join_style="mitre")
    parts = as_polygons(outline)
    outline = parts[0] if len(parts) == 1 else MultiPolygon(parts) if parts else None
    fabric = read_fabric(lines, fills, doors, opening_lines, cfg, built)
    fabric.windows = _one_per_window(fabric.windows + [_axis(b) for b in glazed_gaps])
    return WallSpaces(spaces, outline, len(pockets), doorways,
                      shapely.union_all(open_edges) if open_edges else None, fabric)


SURROUNDS = 3  # an area with this many others inside or within its reach…
SURROUNDS_SHARE = 0.5  # …holding this share of their area…
OUTSIDE_SHARE = 0.25  # …and (when it wraps round them) this share of the plan is the outside


def _surrounding(regions: list[Polygon], plan_area: float, label_points) -> set[int]:
    """Areas that are the outside around the building: the garden between a plot
    wall and the house, a sheet's frame around a plan. Such an area holds most of
    the other areas in its holes, or (a garden that meets the house, so has no hole)
    is a big unnamed area wrapped round most of them. A corridor ringing an office
    core holds a few small rooms, not most of the floor; a named area is a room."""
    if len(regions) <= SURROUNDS:
        return set()
    points = [r.representative_point() for r in regions]
    tree = shapely.STRtree(points)
    areas = [r.area for r in regions]
    total = sum(areas)
    out = set()
    for k, region in enumerate(regions):
        others = total - areas[k]
        holes = shapely.union_all([Polygon(ring) for ring in region.interiors]) if region.interiors else None
        inside = [int(j) for j in tree.query(holes, predicate="contains")] if holes is not None else []
        inside = [j for j in inside if j != k]
        if len(inside) >= SURROUNDS and sum(areas[j] for j in inside) >= SURROUNDS_SHARE * others:
            out.add(k)
            continue
        if region.area < OUTSIDE_SHARE * plan_area or any(region.contains(p) for p in label_points):
            continue
        hull = region.convex_hull
        within = [int(j) for j in tree.query(hull, predicate="contains") if int(j) != k]
        if len(within) >= SURROUNDS and sum(areas[j] for j in within) >= SURROUNDS_SHARE * others:
            out.add(k)
    return out


LINE_M = 0.1  # areas this close are parted by a single drawn line, not a wall
LAND_LINE_SHARE = 0.15  # an area with this share of its edge on the outside across a single line…
LAND_LINE_MIN_M = 10.0  # …this long…
WALLED_SHARE = 0.6  # …round a building whose own outside is walls with thickness, this much of it


def _within_the_land_line(regions: list[Polygon], envelope, outside: set[int], built, ways) -> set[int]:
    """The yard inside the line that marks the land: a big area (named or not; a
    yard holds level notes, steps, a landing) parted from the outside, along a good
    stretch of its edge, by a single line, round a building whose own outside is
    walls drawn with thickness (``ways``: its windows and doors, which are not). A
    room meets the outside through a wall, a window or a door; a plan drawn in single lines has no yard to tell apart."""
    x0, y0, x1, y1 = envelope.bounds
    beyond = unary_union([box(x0 - 1, y0 - 1, x1 + 1, y1 + 1).difference(envelope),
                          *[regions[j] for j in outside]])
    near_beyond = beyond.buffer(LINE_M)
    plan = envelope.area - sum(regions[j].area for j in outside)
    thick = None
    found = set()
    for k, region in enumerate(regions):
        if k in outside or region.area < OUTSIDE_SHARE * plan:
            continue
        stretch = region.boundary.intersection(near_beyond)
        if ways is not None and not stretch.is_empty:
            stretch = stretch.difference(ways)
        if stretch.length < LAND_LINE_MIN_M or stretch.length < LAND_LINE_SHARE * region.boundary.length:
            continue
        # The building left once the area is taken away (without the line round it):
        # is its outside walls with thickness?
        rest = envelope.difference(beyond.union(region).buffer(0.01))
        rest = rest.buffer(-LINE_M, join_style="mitre").buffer(LINE_M, join_style="mitre")
        edge = unary_union([Polygon(p.exterior) for p in as_polygons(rest)]).boundary
        if ways is not None:
            edge = edge.difference(ways)
        if edge.length == 0:
            continue
        if thick is None:
            thick = built.buffer(-LINE_M / 2, join_style="mitre").buffer(LINE_M, join_style="mitre")
        if edge.intersection(thick).length >= WALLED_SHARE * edge.length:
            found.add(k)
    return found


def _axis(poly: Polygon) -> LineString:
    """The long middle line of a gap's filling: the span of the opening."""
    c = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    sides = [np.hypot(*(c[(i + 1) % 4] - c[i])) for i in range(4)]
    i = int(np.argmin(sides))  # a short side: across the wall
    a = (c[i] + c[(i + 1) % 4]) / 2
    b = (c[(i + 2) % 4] + c[(i + 3) % 4]) / 2
    return LineString([a, b])


def _connectors(mass, lines: list, max_length: float) -> list:
    """Lines that carry a wall across a gap: glazing (straight or curved) or a door
    leaf drawn closed. Both ends sit on a wall and run in line with it; the middle
    is clear of walls. Lines that meet walls at an angle (a cross marking a lift
    car, a counter edge) and closed shapes do not qualify."""
    shapely.prepare(mass)
    out = []
    for ln in lines:
        if ln.length > max_length or ln.length < 0.1:
            continue
        c = np.asarray(ln.coords)
        if np.hypot(*(c[-1] - c[0])) < 0.05:
            continue  # closed
        a, b = Point(c[0]), Point(c[-1])
        if not (shapely.dwithin(mass, a, 0.06) and shapely.dwithin(mass, b, 0.06)):
            continue
        if mass.contains(ln.interpolate(0.5, normalized=True)):
            continue
        if _in_line(mass, c[0], c[1] - c[0]) and _in_line(mass, c[-1], c[-2] - c[-1]):
            out.append(ln)
    return out


IN_LINE_EDGE_M = 0.08  # a wall face this long near a line's end shows which way the wall runs there


def _in_line(mass, point, direction, tolerance_deg: float = 25.0) -> bool:
    """Whether a line leaving ``point`` along ``direction`` continues the wall there:
    parallel to a face of the wall within reach of the point. Beside a window the
    wall may be a short stub whose end face, across the wall, is its longest edge
    there; its faces along the wall still show the way it runs."""
    x, y = point
    edges = shapely.clip_by_rect(mass.boundary, x - 0.3, y - 0.3, x + 0.3, y + 0.3)
    norm = float(np.hypot(*direction)) or 1.0
    for part in shapely.get_parts(edges):
        c = np.asarray(part.coords)
        for p, q in zip(c[:-1], c[1:]):
            length = float(np.hypot(*(q - p)))
            if length >= IN_LINE_EDGE_M and abs(np.dot(q - p, direction)) / (length * norm) >= np.cos(np.radians(tolerance_deg)):
                return True
    return False


def _door_bars(door: DoorShape, mass) -> list[Polygon]:
    """Bars along the door's leaves in their closed position: from each hinge to
    the end of its swing that lies against the far jamb, or, for the two leaves of
    a double door, where the leaves meet."""
    closed: list[tuple[Point, Point]] = []
    if len(door.swings) == 2:
        (h1, e1), (h2, e2) = [(s.hinge, s.ends) for s in door.swings]
        d, i, j = min((e1[i].distance(e2[j]), i, j) for i in range(2) for j in range(2))
        if d < 0.15:
            closed = [(h1, e1[i]), (h2, e2[j])]
    if not closed:
        for s in door.swings:
            a, b = s.ends
            end = a if mass.distance(a) <= mass.distance(b) else b
            if mass.distance(end) <= 0.3:  # else not a door in a wall
                closed.append((s.hinge, end))
    if len(closed) == 2:  # a double door: from one hinge to the other
        door.span = LineString([closed[0][0], closed[1][0]])
    elif closed:
        door.span = LineString(closed[0])
    else:
        x0, y0, x1, y1 = door.box.bounds
        door.width = round(max(x1 - x0, y1 - y0), 3)
    if door.span is not None:
        door.width = round(door.span.length, 3)
    return [LineString(pair).buffer(SWING_BAR_M, cap_style="square", join_style="mitre") for pair in closed]


def _bridge_door_boxes(mass, door_boxes: list[Polygon], reach: float):
    """Close the wall opening each door block (without a swing) stands in.

    Around a door, gaps up to the door's width are filled. The fill stays within
    ``reach`` of the door so it spans the wall it sits in but no neighbouring room.
    """
    bridges = []
    for door in door_boxes:
        x0, y0, x1, y1 = door.bounds
        r = max(x1 - x0, y1 - y0) / 2 * 1.1 + SNAP_M
        zone = (x0 - reach, y0 - reach, x1 + reach, y1 + reach)
        local = shapely.clip_by_rect(mass, zone[0] - r, zone[1] - r, zone[2] + r, zone[3] + r)
        if local.is_empty:
            continue
        bridges.append(shapely.clip_by_rect(closing(local, r), *zone))
    return unary_union([mass, *bridges]) if bridges else mass


def _wall_ends(mass, max_thickness: float):
    """The faces where walls stop: short edges whose sides both run back along the
    wall. Yields (a, b, outward unit normal) as numpy points."""
    for poly in as_polygons(mass):
        x0, y0, x1, y1 = poly.bounds
        if max(x1 - x0, y1 - y0) < WALL_PIECE_MIN_M:
            continue
        poly = orient(poly.simplify(0.02), 1.0)  # interior on the left of every ring
        for ring in [poly.exterior, *poly.interiors]:
            c = np.asarray(ring.coords)[:-1]
            n = len(c)
            if n < 4:
                continue
            for i in range(n):
                prev, a, b, nxt = c[i - 1], c[i], c[(i + 1) % n], c[(i + 2) % n]
                face = b - a
                width = np.hypot(*face)
                if not (0.01 < width <= max_thickness + 0.05):
                    continue
                u, v = a - prev, nxt - b
                lu, lv = np.hypot(*u), np.hypot(*v)
                if lu < WALL_END_MIN_SIDE_M or lv < WALL_END_MIN_SIDE_M:
                    continue
                if np.dot(u, v) / (lu * lv) > -0.8:  # the sides must run opposite ways
                    continue
                if _cross(u, face) <= 0 or _cross(face, v) <= 0:  # both corners convex
                    continue
                yield a, b, np.array([face[1], -face[0]]) / width


def _bridge_wall_ends(mass, max_doorway: float, max_window: float, max_thickness: float, openings=None):
    """Close gaps where a wall stops and another wall faces it: within
    ``max_doorway`` always (a doorway, or a window with nothing drawn), within
    ``max_window`` when ``openings`` linework runs through the gap (glazing).

    From each wall end, the free space straight ahead (as wide as the wall) is
    followed; if walls close it off, it is the gap. Returns the new mass and the
    bridges as (polygon, glazed).
    """
    reach = max(max_doorway, max_window if openings is not None else 0.0)
    bridges = []
    for a, b, normal in _wall_ends(mass, max_thickness):
        inset = (b - a) * 0.1
        a1, b1 = a + inset, b - inset
        a2, b2 = a1 + normal * reach, b1 + normal * reach
        sweep = Polygon([a1, b1, b2, a2])
        free = sweep.difference(shapely.clip_by_rect(mass, *sweep.bounds))
        start = LineString([a1 + normal * 0.005, b1 + normal * 0.005])
        ahead = [p for p in as_polygons(free) if p.intersects(start)]
        if not ahead:
            continue  # already closed
        if any(p.distance(LineString([a2, b2])) < 0.02 for p in ahead):
            continue  # nothing faces this wall end
        gap = unary_union(ahead)
        depth = float(np.max((shapely.get_coordinates(gap) - a1) @ normal))
        glazed = False
        if depth > max_doorway:
            # Wider than a doorway: closed only where glazing runs along the gap.
            glazed = openings is not None and gap.intersection(openings).length >= 0.5 * depth
            if not glazed:
                continue
        bridges.append((gap.buffer(SNAP_M, join_style="mitre"), glazed))
    if not bridges:
        return mass, []
    merged = []
    for part in as_polygons(shapely.union_all([b for b, _ in bridges])):
        merged.append((part, any(g and b.intersects(part) for b, g in bridges)))
    return unary_union([mass, *(b for b, _ in bridges)]), merged


def _envelope(mass, max_opening: float):
    """The building's outline: the walls, with every gap in the outside up to
    ``max_opening`` spanned, and everything inside filled. Built from triangles
    between nearby wall points, so it works for walls of any shape and thickness."""
    pts = shapely.get_coordinates(shapely.segmentize(mass.boundary, max_opening / 4))
    tris = shapely.get_parts(shapely.delaunay_triangles(shapely.multipoints(pts)))
    kept = []
    if len(tris):
        corners = shapely.get_coordinates(tris).reshape(-1, 4, 2)[:, :3]
        longest = np.linalg.norm(corners - np.roll(corners, -1, axis=1), axis=2).max(axis=1)
        kept = list(tris[longest <= max_opening])
    hull = shapely.union_all([mass, *kept])
    return unary_union([Polygon(p.exterior) for p in as_polygons(hull)])


def _cross(p, q) -> float:
    return float(p[0] * q[1] - p[1] * q[0])
