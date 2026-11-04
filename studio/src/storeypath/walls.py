"""Finding spaces in drawings that have walls but no room outlines.

Everything on the wall layers (walls, windows, columns) is merged into one wall
mass, and the cores of walls drawn as two lines are filled in. The openings in it
are then closed:

- where a door is drawn: along the closed position of its swing, or across the
  extent of a door block that has no swing;
- where a line on a door or window layer runs from one wall to another: glazing
  (straight or curved) or a door leaf drawn closed;
- where a wall stops and another wall faces its end within ``max_doorway``: a
  doorway or window with nothing drawn in it; or within ``max_glazing`` with
  glazing drawn in the gap: a window (glazing from wall to wall closes that much too);
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

from .geometry import as_polygons, closing, polygonal
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
    fronts: list[LineString] = field(default_factory=list)  # of the windows, those of glass fronts: each from
    # wall to wall, where a partition meets the glass too (the room past it is no room it joins)


def read_fabric(lines: list, fills: list, doors: list[DoorShape], opening_lines: list, cfg: WallsConfig,
                mass=None, glass_lines: list | None = None, gaps: list | None = None) -> Fabric:
    """The walls as drawn and the windows in them. Also measures each door's span.
    ``glass_lines``: the glazing on layers named for windows or glass (its glass fronts are
    windows, one for each stretch from wall to wall); ``gaps``: the spans of gaps closed as
    glazed (_bridge_wall_ends), windows too."""
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
    panes = shapely.STRtree(glass_lines) if glass_lines else None
    bars = [bar for d in doors for bar in _door_bars(d, mass, panes)]  # (sets each door's span and width)
    near_door = shapely.union_all([d.box.buffer(0.3) for d in doors]) if doors else Polygon()
    glass = [ln for ln in _connectors(mass, opening_lines, cfg.max_glazing) if not ln.intersects(near_door)] \
        if opening_lines else []
    windows = _one_per_window(glass + list(gaps or []))
    runs = _glass_runs(_glass_pieces(glass_lines)) if glass_lines else []
    fronts = _front_windows(_glass_fronts(_with_doors(mass, bars, doors), runs, cfg.max_glazing), doors, runs) \
        if runs else []
    if fronts:  # a window along a glass front is the front's
        along = shapely.union_all([f.buffer(0.3, cap_style="flat") for f in fronts])
        windows = [w for w in windows if w.intersection(along).length < 0.5 * w.length]
    return Fabric(walls, windows + fronts, fronts=fronts)


def _with_doors(mass, bars: list, doors: list[DoorShape]):
    """The walls with the doors in them: each door's leaves closed (``bars``), and a door
    drawn as a block with no swing, its extent. A glass front stops at a door."""
    blocks = [d.box for d in doors if not d.swings]
    return unary_union([mass, *bars, *blocks]) if bars or blocks else mass


FRONT_WINDOW_MIN_M = 0.5  # what is left of a glass front's stretch beside a door, shorter, is no window
FRONT_DEPTH_M = 0.6  # a glass front's frames and glass, side by side, lie within this depth (a wall's at most)


def _front_windows(stretches: list[LineString], doors: list[DoorShape], runs: list) -> list[LineString]:
    """A glass front's windows: one for each stretch from wall to wall. Where it is drawn as
    several lines side by side (its glass, its frame's faces), the stretches parted by the walls
    meeting it (a room's) are its windows, the longer ones beside them not; not where a door
    stands (its own glass is the door's). Each lies in the middle of the glass's depth there
    (``runs``: _glass_runs'), so that it joins the rooms on either side of it."""
    out: list[LineString] = []
    taken = Polygon()
    doors_at = shapely.union_all([d.box.buffer(0.1) for d in doors]) if doors else Polygon()
    for s in sorted(stretches, key=lambda ln: ln.length):
        rest = s.difference(taken.union(doors_at)) if not (taken.is_empty and doors_at.is_empty) else s
        pieces = [p for p in shapely.get_parts(rest) if p.geom_type == "LineString" and p.length >= FRONT_WINDOW_MIN_M]
        out += pieces
        if pieces:
            taken = taken.union(shapely.union_all([p.buffer(FRONT_DEPTH_M, cap_style="flat") for p in pieces]))
    if not out or not runs:
        return out
    lines = [line for line, _ in runs]
    tree = shapely.STRtree(lines)
    middle = []
    for w in out:
        c = np.asarray(w.coords)
        u = (c[-1] - c[0]) / w.length
        n = np.array([-u[1], u[0]])
        offsets = [0.0]
        for j in tree.query(w.buffer(FRONT_DEPTH_M, cap_style="flat"), predicate="intersects"):
            r = np.asarray(lines[int(j)].coords)
            v = r[-1] - r[0]
            if abs(v[0] * u[1] - v[1] * u[0]) > np.sin(np.radians(GLASS_ALIGN_DEG)) * np.hypot(*v):
                continue  # (across it: a mullion)
            off = [float((q - c[0]) @ n) for q in r]
            if max(abs(o) for o in off) <= FRONT_DEPTH_M:
                offsets.append(sum(off) / 2)
        shift = (min(offsets) + max(offsets)) / 2
        middle.append(LineString(c + n * shift))
    return middle


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
    pockets: list  # regions left out as open to the outside: (polygon, why) — "around" the
    # building (a sheet's frame, the garden inside a plot wall) or "recess" (most of its
    # edge along the building's outline, no room name inside)
    doorways: list[Polygon] = field(default_factory=list)  # openings closed with no door drawn
    open_edges: object = None  # where spaces meet the outside with no door or window drawn
    fabric: Fabric | None = None
    envelope: object = None  # the building's extent: what the walls enclose, gaps in them bridged
    cut: list = field(default_factory=list)  # (found ``within`` a window) the areas its edge cuts through
    grounds: list = field(default_factory=list)  # (``open_ground``) the open ground round the rooms, kept as spaces


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
    within: tuple | None = None,
    open_ground: bool = False,
    glass_lines: list | None = None,
) -> WallSpaces:
    """Spaces enclosed by walls. ``lines`` are wall-layer linework, ``fills`` solid
    wall areas (from hatches), ``doors`` the doors found, ``opening_lines`` the
    linework on door and window layers (glazing tells a window from a gap), and
    ``glass_lines`` those of it on layers named for windows or glass, not dashed (their
    glass fronts close as walls: _glass_fronts).

    ``within`` (envelope, window): the spaces of one part of a building found again,
    from what is drawn in and near it: the building's extent is ``envelope`` (as found
    for the whole), and areas the ``window``'s edge cuts through are left out (``cut``),
    as are the outside's around the building (the whole is needed to tell them).

    ``open_ground``: a roof's plan, whose open ground round the rooms standing on it (the
    roof's deck, inside its parapets) would read as the outside round a building (a
    garden, the yard inside a land line): it is kept, as spaces (``grounds``)."""
    mass = wall_mass(lines, fills, cfg)
    if mass.is_empty:
        return WallSpaces([], None, [])
    panes = shapely.STRtree(glass_lines) if glass_lines else None
    seals = [bar for d in doors for bar in _door_bars(d, mass, panes)]
    built = mass
    # glazing from wall to wall, or to a door (a side light beside an entrance's doors)
    against = unary_union([mass, *seals]) if seals else mass
    glass = _connectors(against, opening_lines, cfg.max_glazing) if opening_lines else []
    # glass fronts: panes in line end to end, each stretch from wall to wall (or to a door)
    if glass_lines:
        runs = _glass_runs(_glass_pieces(glass_lines))
        glass += _glass_fronts(_with_doors(mass, seals, doors), runs, cfg.max_glazing)
    seals += list(shapely.buffer(glass, SNAP_M)) if glass else []
    mass = unary_union([mass, *seals]) if seals else mass
    mass = _bridge_door_boxes(mass, [d.box for d in doors if not d.swings], cfg.max_thickness)
    openings = shapely.union_all(opening_lines) if opening_lines else None
    mass, bridges = _bridge_wall_ends(mass, cfg.max_doorway, cfg.max_glazing, cfg.max_thickness, openings)
    near_door = shapely.union_all([d.box.buffer(0.2) for d in doors]) if doors else Polygon()
    doorways = [b for b, glazed in bridges if not glazed and not b.intersects(near_door)]
    glazed_gaps = [b for b, glazed in bridges if glazed]

    envelope = _envelope(mass, cfg.max_opening) if within is None else within[0].intersection(within[1])
    edge = (envelope if within is None else within[0]).boundary.buffer(SNAP_M)
    # each line widened, then joined: widening thousands of lines joined in one is
    # very slow (minutes on a large building's door and window layers)
    glazing = shapely.union_all(shapely.buffer(opening_lines, cfg.max_thickness / 2 + 0.05)) if opening_lines else None
    spaces, pockets, open_edges, grounds = [], [], [], []
    regions = [r for r in _apart([r for r in as_polygons(envelope.difference(mass)) if r.area >= min_area], edge)
               if r.area >= min_area and not r.buffer(-cfg.min_width / 2).is_empty]
    cut, cut_edge = [], within[1].boundary.buffer(SNAP_M * 2) if within is not None else None
    around = set()
    if within is None:
        around = _surrounding(regions, envelope.area, label_points)
        # the windows and doors in its gaps
        ways = unary_union([*seals, *(b for b, _ in bridges)]) if seals or bridges else None
        around |= _within_the_land_line(regions, envelope, around, built,
                                        ways.buffer(cfg.max_thickness / 2 + 0.05) if ways is not None else None)
    for k, region in enumerate(regions):
        if k in around and not open_ground:
            pockets.append((region, "around"))  # the garden inside a plot wall, a sheet's frame: outside
            continue
        if cut_edge is not None and region.intersects(cut_edge):
            cut.append(region)  # (a part found again: what its edge cuts through is as it was)
            continue
        # Edges along the envelope rather than walls: open to the outside, unless
        # glazing is drawn there (a window).
        stretch = region.boundary.intersection(edge)
        if glazing is not None and not stretch.is_empty:
            stretch = stretch.difference(glazing)
        if stretch.length / region.boundary.length > EXTERIOR_SHARE and not any(
            region.contains(p) for p in label_points
        ):
            pockets.append((region, "recess"))  # a recess in the facade
            continue
        open_edges.append(stretch)
        # Region edges sit SNAP_M off the wall lines; put them back on the lines. At a
        # very sharp corner that can break hairline slivers off: they are not rooms.
        found = [p for p in as_polygons(region.buffer(SNAP_M, join_style="mitre").simplify(SIMPLIFY_M))
                 if p.area >= min_area]
        spaces += found
        if k in around:  # (open_ground: a roof's deck)
            grounds += found

    outline = envelope.difference(unary_union([p for p, _ in pockets])) if pockets else envelope
    outline = outline.buffer(-SNAP_M, join_style="mitre")
    parts = as_polygons(outline)
    outline = parts[0] if len(parts) == 1 else MultiPolygon(parts) if parts else None
    fabric = read_fabric(lines, fills, doors, opening_lines, cfg, built, glass_lines, [_axis(b) for b in glazed_gaps])
    return WallSpaces(spaces, outline, pockets, doorways,
                      shapely.union_all(open_edges) if open_edges else None, fabric, envelope, cut, grounds)


HAIRLINE_M = 0.005  # areas joined only by a strip thinner than twice this are apart


def _apart(regions: list[Polygon], edge) -> list[Polygon]:
    """The areas between the walls, those joined only by a hairline along the building's edge
    (``edge``) taken apart: the envelope's edge runs a hair off the walls' faces where it follows
    them, and two recesses in a facade (either side of a glass front) would be one area along it."""
    shapely.prepare(edge)
    out = []
    for r in regions:
        if r.intersects(edge):
            core = as_polygons(r.buffer(-HAIRLINE_M, join_style="mitre"))
            if len(core) > 1:
                out += [p for c in core
                        for p in as_polygons(r.intersection(c.buffer(2 * HAIRLINE_M, join_style="mitre")))]
                continue
        out.append(r)
    return out


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


GLASS_FRAME_M = 0.35  # a closed shape on an opening layer this thin, and long, is a pane in its frame
GLASS_PANE_MIN_M = 0.3  # glazing shorter than this is a mullion's or a frame's detail, not a pane
GLASS_JOINT_M = 0.1  # panes in line this near end to end (a mullion's width between them, or none) are one front…
GLASS_ALIGN_M = 0.02  # …each standing this near the other's line…
GLASS_ALIGN_DEG = 2.0  # …and running its way within this much
GLASS_END_M = 0.06  # a glass front's stretch ends on a wall this near it (as a connector's ends)


def _glass_fronts(against, runs: list, max_pane: float) -> list[LineString]:
    """Glass fronts: glazing on a layer named for windows or glass, drawn as panes in line end
    to end (window blocks in a row, each pane from mullion to mullion), is one run of glass,
    however many pieces it is drawn in. Each stretch of a run from wall to wall closes like a
    wall: the run's own ends where both meet a wall, and where partitions meet it from inside
    (or doors, sealed along their leaves, stand in it), each piece between them. Each end meets
    the wall in line with it or square against its jamb (as a connector's), however long: where
    the glass ends, at a wall, says how far it goes. A stretch that is one pane from wall to wall,
    up to ``max_pane``, is a window like any (a connector's): not one of these; a longer pane on
    a layer named for windows or glass is one. A stretch is never laid across a door: the run
    is parted at each door's leaf or block, and the door closes its own gap. ``against``: the
    walls, with the doors' leaves and blocks, as one area; ``runs``: the glazing's runs
    (_glass_runs, of _glass_pieces)."""
    out = []
    for run, panes in runs:
        x0, y0, x1, y1 = run.bounds
        reach = GLASS_END_M + 0.3
        local = shapely.clip_by_rect(against, x0 - reach, y0 - reach, x1 + reach, y1 + reach)
        if local.is_empty:
            continue
        shapely.prepare(local)
        for part in shapely.get_parts(run.difference(local)):
            if part.geom_type != "LineString" or part.length < GLASS_PANE_MIN_M:
                continue
            c = np.asarray(part.coords)
            a, b = c[0], c[-1]
            if not (shapely.dwithin(local, Point(a), GLASS_END_M) and shapely.dwithin(local, Point(b), GLASS_END_M)):
                continue
            s0, s1 = sorted((run.project(Point(a)), run.project(Point(b))))
            if any(p0 <= s0 + GLASS_JOINT_M and p1 >= s1 - GLASS_JOINT_M and p1 - p0 <= s1 - s0 + 2 * GLASS_JOINT_M
                   and p1 - p0 <= max_pane for p0, p1 in panes):
                continue  # one pane from wall to wall: a window like any (_connectors)
            if all(_in_line(local, p, q - p) or _jamb(local, p, q - p) for p, q in ((a, b), (b, a))):
                out.append(_to_the_walls(part, local))
    return out


def _to_the_walls(part: LineString, walls) -> LineString:
    """A stretch of glass carried on, at either end, to the wall it stops just short of."""
    c = np.asarray(part.coords)
    a, b = c[0], c[-1]
    u = (b - a) / (np.hypot(*(b - a)) or 1.0)
    da, db = walls.distance(Point(a)), walls.distance(Point(b))
    return LineString([a - u * (da + 0.01 if da > 0 else 0.0), b + u * (db + 0.01 if db > 0 else 0.0)])


def _glass_pieces(lines: list) -> list[LineString]:
    """The panes in glazing lines: each straight stretch of a line at least GLASS_PANE_MIN_M
    long, and the middle line of a pane drawn as a thin closed frame."""
    out = []
    for ln in lines:
        c = np.asarray(ln.coords)
        if len(c) < 2:
            continue
        if np.hypot(*(c[-1] - c[0])) < 0.05:  # closed: a pane in its frame, or nothing
            if len(c) >= 4 and (mid := _pane(c)) is not None and mid.length >= GLASS_PANE_MIN_M:
                out.append(mid)
            continue
        c = np.asarray(ln.simplify(0.005).coords)
        out += [LineString([p, q]) for p, q in zip(c[:-1], c[1:]) if np.hypot(*(q - p)) >= GLASS_PANE_MIN_M]
    return out


def _glass_runs(pieces: list[LineString]) -> list[tuple[LineString, list[tuple[float, float]]]]:
    """Panes in line, end to end (overlapping, touching, or a mullion apart: GLASS_JOINT_M),
    joined: each run one straight line from its first pane's end to its last's, with where
    each of its panes is along it (from its start)."""
    if not pieces:
        return []
    ends = np.array([np.asarray(p.coords)[[0, -1]] for p in pieces])  # (n, 2, 2)
    way = ends[:, 1] - ends[:, 0]
    way /= np.hypot(way[:, 0], way[:, 1])[:, None]
    tree = shapely.STRtree(pieces)
    left, right = tree.query(pieces, predicate="dwithin", distance=GLASS_JOINT_M)
    keep = left < right
    left, right = left[keep], right[keep]
    turned = np.abs(way[left, 0] * way[right, 1] - way[left, 1] * way[right, 0])
    parallel = turned <= np.sin(np.radians(GLASS_ALIGN_DEG))
    off = ends[right] - ends[left, 0][:, None, :]  # the other's ends from this one's start
    across = np.abs(way[left, 0][:, None] * off[..., 1] - way[left, 1][:, None] * off[..., 0])
    joined = parallel & (across.max(axis=1) <= GLASS_ALIGN_M)
    parent = list(range(len(pieces)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in zip(left[joined], right[joined]):
        parent[root(int(i))] = root(int(j))
    groups: dict[int, list[int]] = {}
    for i in range(len(pieces)):
        groups.setdefault(root(i), []).append(i)
    runs = []
    for members in groups.values():
        k = max(members, key=lambda i: pieces[i].length)  # the run's line: its longest pane's
        origin, u = ends[k, 0], way[k]
        spans = sorted(tuple(sorted(((ends[i] - origin) @ u).tolist())) for i in members)
        group = [spans[0]]
        for span in spans[1:] + [None]:
            if span is not None and span[0] <= max(t for _, t in group) + GLASS_JOINT_M:
                group.append(span)
                continue
            t0, t1 = group[0][0], max(t for _, t in group)  # (a gap: another run along the same line)
            runs.append((LineString([origin + u * t0, origin + u * t1]), [(p0 - t0, p1 - t0) for p0, p1 in group]))
            if span is not None:
                group = [span]
    return runs


OUTLINED_PAST_M = 0.03  # a wall's fill is looked for this far past each end of a stretch drawn only by its outline
OUTLINED_THIN_M = 0.05  # a thinner rectangle is a frame's or a pane's, not a stretch of wall


def outlined_stretches(lines: list, fills: list, widths: tuple[float, float], max_thickness: float) -> list[tuple]:
    """The stretches of a filled wall drawn by their outline alone: on a wall layer a closed
    rectangle, ``widths`` long and at most ``max_thickness`` thick, with no fill in it, where the
    wall's fill carries on past both its ends, as thick as it is. An opening is drawn so; so is a
    cabinet or a recess in the wall: what it is, the rooms beside it tell (extract._outlined_ways_in).
    Each as (its rectangle, its span: along the wall's middle, from end to end)."""
    if not lines or not fills:
        return []
    tree = shapely.STRtree(fills)

    def filled(p) -> bool:
        return len(tree.query(Point(p), predicate="intersects")) > 0

    least, most = 2 * (widths[0] + OUTLINED_THIN_M), 2 * (widths[1] + max_thickness)
    out = []
    for ln in lines:
        if not least <= ln.length <= most:
            continue
        c = np.asarray(ln.simplify(0.005).coords)
        if len(c) != 5 or np.hypot(*(c[-1] - c[0])) > 0.05:
            continue
        sides = c[1:] - c[:-1]
        lengths = np.hypot(sides[:, 0], sides[:, 1])
        if abs(lengths[0] - lengths[2]) > 0.02 or abs(lengths[1] - lengths[3]) > 0.02 \
                or abs(sides[0] @ sides[1]) > 0.03 * lengths[0] * lengths[1]:
            continue  # not a rectangle
        k = int(lengths[1] > lengths[0])  # a long side
        width, thick = lengths[k], lengths[1 - k]
        if not (widths[0] <= width <= widths[1] and OUTLINED_THIN_M <= thick <= max_thickness):
            continue
        middle, along = c[:4].mean(axis=0), sides[k] / width
        across = np.array([-along[1], along[0]])
        if filled(middle):
            continue
        past = [middle + side * along * (width / 2 + OUTLINED_PAST_M) + share * thick * across
                for side in (-1, 1) for share in (-0.35, 0.0, 0.35)]
        if all(filled(p) for p in past):
            out.append((Polygon(c), LineString([middle - along * width / 2, middle + along * width / 2])))
    return out


def _connectors(mass, lines: list, max_length: float) -> list:
    """Lines that carry a wall across a gap: glazing (straight or curved, or a pane drawn
    as a thin closed frame: its middle line, end to end) or a door leaf drawn closed.
    Both ends sit on a wall, each running in line with it or square against its jamb; the
    middle is clear of walls. Lines that meet walls at an angle (a cross marking a lift car)
    and other closed shapes do not qualify."""
    shapely.prepare(mass)
    out = []
    for ln in lines:
        if ln.length < 0.1:
            continue
        c = np.asarray(ln.coords)
        if np.hypot(*(c[-1] - c[0])) < 0.05:  # closed: a pane in its frame, or nothing that carries a wall
            if len(c) < 4 or not (ln := _pane(c)) or ln.length > max_length:
                continue
            c = np.asarray(ln.coords)
        elif ln.length > max_length:
            continue
        a, b = Point(c[0]), Point(c[-1])
        if not (shapely.dwithin(mass, a, 0.06) and shapely.dwithin(mass, b, 0.06)):
            continue
        if mass.contains(ln.interpolate(0.5, normalized=True)):
            continue
        # each end continues the wall, or stands against its jamb: the wall's face square across
        # it (a pier wider than reach, or a wall meeting the facade beside a window, has short
        # faces along); a line meeting a wall at an angle (a lift car's cross) does neither
        ends = ((c[0], c[1] - c[0]), (c[-1], c[-2] - c[-1]))
        if all(_in_line(mass, p, d) or _jamb(mass, p, d) for p, d in ends):
            out.append(ln)
    return out


def _jamb(mass, point, direction) -> bool:
    """Whether a face of the wall across ``direction`` (within 25° of square to it), at
    least 0.1 m long, stands within 0.06 m of ``point``: the jamb a pane ends against."""
    x, y = point
    edges = shapely.simplify(shapely.clip_by_rect(mass.boundary, x - 0.3, y - 0.3, x + 0.3, y + 0.3), 0.005)
    norm = float(np.hypot(*direction)) or 1.0
    here = Point(x, y)
    for part in shapely.get_parts(edges):
        c = np.asarray(part.coords)
        for p, q in zip(c[:-1], c[1:]):
            length = float(np.hypot(*(q - p)))
            if length >= 0.1 and abs(np.dot(q - p, direction)) / (length * norm) <= np.sin(np.radians(25.0)) \
                    and LineString([p, q]).distance(here) <= 0.06:
                return True
    return False


def _pane(ring) -> LineString | None:
    """A closed shape's middle line, end to end, when it is a thin rectangle (a pane in
    its frame, a side light): no wider than GLASS_FRAME_M, at least three times as long."""
    poly = Polygon(ring)
    if not poly.is_valid or poly.area < 0.002:  # (a frame's corner piece, or nothing)
        return None
    with np.errstate(divide="ignore", invalid="ignore"):  # (an edge of no length on the way round)
        box_ = poly.minimum_rotated_rectangle
        c = np.asarray(box_.exterior.coords)[:4]
        sides = sorted(np.hypot(*(c[(i + 1) % 4] - c[i])) for i in range(4))
        if sides[0] > GLASS_FRAME_M or sides[2] < 3 * sides[0] or poly.area < 0.8 * box_.area:
            return None
        return _axis(poly)


IN_LINE_EDGE_M = 0.08  # a wall face this long near a line's end shows which way the wall runs there


def _in_line(mass, point, direction, tolerance_deg: float = 25.0) -> bool:
    """Whether a line leaving ``point`` along ``direction`` continues the wall there:
    parallel to a face of the wall within reach of the point. Beside a window the
    wall may be a short stub whose end face, across the wall, is its longest edge
    there; its faces along the wall still show the way it runs. A face drawn in many short
    pieces (a fill's edge) is taken as the straight run they make."""
    x, y = point
    edges = shapely.simplify(shapely.clip_by_rect(mass.boundary, x - 0.3, y - 0.3, x + 0.3, y + 0.3), 0.005)
    norm = float(np.hypot(*direction)) or 1.0
    for part in shapely.get_parts(edges):
        c = np.asarray(part.coords)
        for p, q in zip(c[:-1], c[1:]):
            length = float(np.hypot(*(q - p)))
            if length >= IN_LINE_EDGE_M and abs(np.dot(q - p, direction)) / (length * norm) >= np.cos(np.radians(tolerance_deg)):
                return True
    return False


def _door_bars(door: DoorShape, mass, glass=None) -> list[Polygon]:
    """Bars along the door's leaves in their closed position: from each hinge to
    the end of its swing that lies against the far jamb, or, for the two leaves of
    a double door, where the leaves meet. The jamb is a wall's, or the glass of a
    glass front the door stands in (``glass``: an STRtree of the glazing on layers
    named for windows or glass)."""

    def away(p) -> float:  # from the nearest wall, or glass
        d = mass.distance(p)
        if glass is not None and d > 0:
            near, dist = glass.query_nearest(p, max_distance=d, return_distance=True)
            d = min([d, *dist.tolist()]) if len(near) else d
        return d

    closed: list[tuple[Point, Point]] = []
    if len(door.swings) == 2:
        (h1, e1), (h2, e2) = [(s.hinge, s.ends) for s in door.swings]
        d, i, j = min((e1[i].distance(e2[j]), i, j) for i in range(2) for j in range(2))
        if d < 0.15:
            closed = [(h1, e1[i]), (h2, e2[j])]
    if not closed:
        for s in door.swings:
            a, b = s.ends
            da, db = away(a), away(b)
            if min(da, db) <= 0.3:  # else not a door in a wall
                closed.append((s.hinge, a if da <= db else b))
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
    mass = polygonal(mass)  # (a line left in it by an overlay: no boundary, no triangles, no gaps spanned)
    pts = shapely.get_coordinates(shapely.segmentize(mass.boundary, max_opening / 4))
    # Points a fraction of a millimetre apart break the triangulation ("could not
    # locate vertex"): one per millimetre, snapped; failing still, without the triangles.
    pts = np.unique(np.round(pts, 3), axis=0)
    tris = []
    for tolerance in (0.001, 0.01):
        try:
            tris = shapely.get_parts(shapely.delaunay_triangles(shapely.multipoints(pts), tolerance=tolerance))
            break
        except shapely.errors.GEOSException:
            continue
    kept = []
    if len(tris):
        corners = shapely.get_coordinates(tris).reshape(-1, 4, 2)[:, :3]
        longest = np.linalg.norm(corners - np.roll(corners, -1, axis=1), axis=2).max(axis=1)
        kept = list(tris[longest <= max_opening])
    hull = shapely.union_all([mass, *kept])
    return unary_union([Polygon(p.exterior) for p in as_polygons(hull)])


def _cross(p, q) -> float:
    return float(p[0] * q[1] - p[1] * q[0])
