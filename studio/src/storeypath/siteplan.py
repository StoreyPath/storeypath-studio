"""Reading a site plan into a location's layout (format 0.12; docs/DESIGN-LOCATION-LAYOUTS.md).

A site plan shows a plot of land: its boundary (the property limits), the walls and
fences round it with their gates, the buildings standing on it, and the open ground
between them (a driveway, parking, a garden), often with the road outside. Reading one:

1. what is drawn in the plan's region (gather): lines, solid and dashed, arcs, hatches'
   outlines, texts, blocks;
2. the boundary (boundary): the property limits, by their layer's name or a text along
   them, each piece read as the line it lies on, the plot what those lines close round
   the plan; else the outer outline of its walls;
3. the faces the drawn lines make inside the plot; thin ones along its edge are its
   boundary walls (walls), and the gaps in those, a gate's width, its gates (gates);
4. buildings and land (land): the texts say which is which (a building's name, a
   floor's level; a ground level, PARKING, GARDEN), read by rules and, for the rest, by
   the language model: the open land is the largest face against the boundary walls and
   the faces land texts are in; the rest, a piece at a time, is a building's footprint
   when a building's text is in it or walls close round it (buildings are holes in the
   land), its outline without holes;
5. the land's faces grouped into areas by their texts (areas): the faces with none are the
   yard; outside the plot, a face labelled as a road is the road;
6. the north arrow (north): a block named so, or one holding an N, the one nearest the
   plot; the bearing the plan's up faces from its turn.

Everything is in metres of the drawing (its units' scale), less the source's offset: the
layout's frame, which Studio makes the site's (convert.read_layout chooses the offset).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

import numpy as np
import shapely
import shapely.affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, unary_union

from .cad import meters_per_unit
from .extract import CURVE_TOLERANCE_M, _center, _flatten, _text_lines, _walk, dashed_lines, plan_entities
from .geometry import as_polygons
from .reading import NOT_A_ROOM
from .types import SpaceType
from .workspace import NorthRead

# ---- what texts say ------------------------------------------------------------------

# the property limits: a text along them, or their layer's name
PROPERTY_TEXT = re.compile(
    r"\b(property|plot|lot|site|land)\s*(limits?|lines?|boundar(y|ies))\b|\bboundary\s*(lines?|limits?)?\b"
    r"|حدود", re.I)
PROPERTY_LAYER = re.compile(r"propert|plot|boundar|limit|lot.?line|site.?line|حدود", re.I)
# levels: a floor's (a building's: finished floor, slab, top of a parapet) or the ground's
BUILDING_LEVEL = re.compile(r"\b(f\.?\s?f\.?\s?l|s\.?\s?s\.?\s?l|t\.?\s?o\.?\s?s|top|roof|slab|parapet)\b", re.I)
GROUND_LEVEL = re.compile(r"\b(f\.?\s?g\.?\s?l|n\.?\s?g\.?\s?l|e\.?\s?g\.?\s?l|g\.?\s?l|ground\s+level)\b", re.I)
# a floor's finished level (+0.45 FFL), and the value a level text writes (±0.00, +450)
FLOOR_LEVEL = re.compile(r"\bf\.?\s?f\.?\s?l\b", re.I)
LEVEL_VALUE = re.compile(r"(±|\+/-|[+\-−])?\s*(\d+(?:[.,]\d+)?)")
GROUND_NEAR_M = 30.0  # a footprint's ground: the ground's level written nearest it, within this
# what a building is called, in the words site plans use
BUILDING_WORDS = re.compile(
    r"\b(villa|house|home|building|bldg|block|tower|majlis|kitchen|annex|annexe|outbuilding|out\s*house|guard|"
    r"security|gate\s*house|guest\s*house|mosque|masjid|office|warehouse|store|storage|shed|hall|school|clinic|"
    r"pump\s*room|plant\s*room|electrical\s*room|sub\s*station|substation|generator|garage|workshop|"
    r"maid|driver|servant|staff\s*(room|quarters?)|quarters|service\s*block|g\s*\+\s*\d|wc|toilet|room|rm)\b"
    r"|فيلا|مبنى|مجلس|مطبخ|ملحق|غرفة|حارس|مسجد|مستودع", re.I)
# what an area of land is called (the first that fits)
AREA_WORDS: list[tuple[SpaceType, re.Pattern]] = [
    (SpaceType.POOL, re.compile(r"\b(swimming\s*)?pool\b|مسبح", re.I)),
    (SpaceType.PARKING, re.compile(r"\b(car\s*)?park(ing)?\b|\bcar\s*port\b|\bcarport\b|مواقف|موقف", re.I)),
    (SpaceType.DRIVEWAY, re.compile(r"\bdrive\s*way\b|\bdriveway\b|\bdrive\b|\baccess\s*(road|way)\b", re.I)),
    (SpaceType.GATE_AREA, re.compile(r"\bgate\s*(area|zone)\b|\bentrance\s*(area|gate)\b", re.I)),
    (SpaceType.GARDEN, re.compile(r"\b(garden|lawn|landscap\w*|planting|planter|green\s*area|grass)\b|حديقة|زراعة",
                                  re.I)),
    (SpaceType.PATH, re.compile(r"\b(foot\s*)?path(way)?\b|\bwalk\s*way\b|\bwalkway\b|\bside\s*walk\b|\bpavement\b",
                                re.I)),
    (SpaceType.COURTYARD, re.compile(r"\bcourt\s*yard\b|\bcourtyard\b|\bcourt\b|فناء|حوش", re.I)),
    (SpaceType.YARD, re.compile(r"\b(back|front|side)?\s*yard\b|\bopen\s*(area|space|ground)\b", re.I)),
    (SpaceType.TERRACE, re.compile(r"\b(terrace|patio|deck|seating\s*area)\b|تراس", re.I)),
    (SpaceType.ROAD, re.compile(r"\b(road|street|st|str|highway|avenue|lane)\b\.?|شارع|طريق", re.I)),
]
# steps, arrows, notes of no place
NOTHING_WORDS = re.compile(r"^(step(s)?(\s*(up|down))?|up|dn|down|ramp\s*(up|down)|scale\b.*|n|north)$", re.I)
# a level of something not a floor nor the ground (a wall's top): a note
LEVEL_NOTE = re.compile(r"\b(lvl|level|el)\b\.?\s*[±+\-]?\s*\d", re.I)
# beside the plot, not of it
OUTSIDE_WORDS = re.compile(r"\bneighbou?r(ing)?\b|\badjacent\b|\bexisting\s+plot\b|جار", re.I)
# what reads as a word, letters spaced out for show (N E I G H B O U R, R  O  A  D)
SPACED = re.compile(r"^(\S)(\s+\S)+$")
NUMBERISH = re.compile(r"^[\s±+\-−.,:/()0-9xX×%]*(sq\.?\s*m\.?|m2|m²|mm|cm|m)?\.?$", re.I)

BUILDING, AREA, GROUND, OUTSIDE, NOTHING = "building", "area", "ground", "outside", "nothing"


def plain(text: str) -> str:
    """A text as read: its lines joined, letters spaced out for show joined again, a zero
    typed for an O between letters read as the O."""
    t = " ".join(text.split())
    if SPACED.fullmatch(t) and len(t) > 3:
        t = re.sub(r"\s{2,}", "\u0000", t).replace(" ", "").replace("\u0000", " ")
    return re.sub(r"(?<=[A-Za-z])0(?=[A-Za-z])", "O", t)


def what_text_says(text: str) -> tuple[str, SpaceType | None] | None:
    """What a site plan's text says by its words alone: (BUILDING, None) a building's
    name or a floor's level; (AREA, type) an area of land; (GROUND, None) a ground level;
    (OUTSIDE, None) a neighbour; (NOTHING, None) a dimension, a note's number. None: the
    words do not say (the language model may)."""
    t = plain(text)
    if not t or NUMBERISH.fullmatch(t):
        return NOTHING, None
    if GROUND_LEVEL.search(t):
        return GROUND, None
    if BUILDING_LEVEL.search(t):
        return BUILDING, None
    if OUTSIDE_WORDS.search(t):
        return OUTSIDE, None
    if PROPERTY_TEXT.search(t) or NOTHING_WORDS.fullmatch(t) or LEVEL_NOTE.search(t) or NOT_A_ROOM.match(t):
        return NOTHING, None
    for kind, pattern in AREA_WORDS:
        if pattern.search(t):
            return AREA, kind
    if BUILDING_WORDS.search(t):
        return BUILDING, None
    return None


# ---- what is drawn ---------------------------------------------------------------------

@dataclass
class Text:
    text: str  # its lines joined
    point: Point  # its middle, metres
    angle: float  # degrees counter-clockwise, as written
    height: float  # metres
    layer: str


@dataclass
class Block:
    name: str
    layer: str
    point: Point  # its insertion point, metres
    middle: Point | None  # the middle of what it draws, metres
    rotation: float  # degrees counter-clockwise
    flipped: bool  # placed upside down (its y scale negative): its up points down
    north: Point | None = None  # an N in it (metres), where it has one


@dataclass
class Line:
    geom: LineString
    layer: str
    dashed: bool
    closed: bool


@dataclass
class Drawn:
    """What a site plan's region holds, in metres (before the offset)."""

    scale: float
    lines: list[Line] = field(default_factory=list)
    arcs: list[tuple[Point, float, float]] = field(default_factory=list)  # (centre, radius, sweep°): gate swings
    fills: list[tuple[Polygon, str, str]] = field(default_factory=list)  # (outline, layer, pattern)
    texts: list[Text] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)


NORTH_NAME = re.compile(r"north|^n$|n[\s_-]*arrow|compass|شمال", re.I)
ANNOTATION_KINDS = ("DIMENSION", "LEADER", "MLEADER", "POINT", "VIEWPORT", "IMAGE", "WIPEOUT")


def gather(doc, region=None, units: str | None = None) -> Drawn:
    """What is drawn in a site plan's ``region`` (drawing units), in metres."""
    scale = meters_per_unit(doc, units)
    tol = CURVE_TOLERANCE_M / scale
    dashed = dashed_lines(doc)
    out = Drawn(scale)
    in_north: dict[int, Block] = {}  # a north arrow's pieces, by the block they are in

    def expand(insert, layer) -> bool:
        b = _block(insert, layer, scale)
        out.blocks.append(b)
        if NORTH_NAME.search(b.name):
            in_north[id(insert)] = b
        return True

    stack: list = []  # (the insert being taken apart): an N text in a north arrow
    for e, layer in _walk(plan_entities(doc, region), expand=expand):
        kind = e.dxftype()
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
            lines, c = _text_lines(e), _center(e)
            if lines and c:
                t = Text(" ".join(lines), Point(c[0] * scale, c[1] * scale), _angle(e), _height(e) * scale, layer)
                out.texts.append(t)
            continue
        if kind == "INSERT" or kind in ANNOTATION_KINDS:
            continue
        if kind == "HATCH":
            for poly in _hatch_outlines(e, tol, scale):
                out.fills.append((poly, layer, str(e.dxf.get("pattern_name", "") or "")))
            continue
        if kind == "ARC":
            try:
                c = e.ocs().to_wcs(e.dxf.center)
                sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360
                if all(math.isfinite(v) for v in (c.x, c.y, e.dxf.radius, sweep)):
                    out.arcs.append((Point(c.x * scale, c.y * scale), e.dxf.radius * scale, sweep))
            except Exception:
                pass
        flat = _flatten(e, tol)
        if flat is None:
            continue
        pts, closed = flat
        pts = [(x * scale, y * scale) for x, y in pts]
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        if len(pts) >= 2 and LineString(pts).length > 1e-6:
            out.lines.append(Line(LineString(pts), layer, dashed(e, layer), closed))
    _north_letters(out)
    return out


def _block(insert, layer: str, scale: float) -> Block:
    import ezdxf.bbox

    p = insert.dxf.get("insert")
    try:
        ext = ezdxf.bbox.extents([insert], fast=True)
        middle = Point(ext.center.x * scale, ext.center.y * scale) if ext.has_data else None
    except Exception:
        middle = None
    ys = insert.dxf.get("yscale", 1.0)
    return Block(insert.dxf.get("name", ""), layer, Point(p.x * scale, p.y * scale) if p is not None else Point(0, 0),
                 middle, float(insert.dxf.get("rotation", 0.0)) % 360, ys < 0)


def _north_letters(drawn: Drawn) -> None:
    """A north arrow's N: a text N within the arrow's reach, the nearest to its middle."""
    for b in drawn.blocks:
        if b.middle is None or not NORTH_NAME.search(b.name):
            continue
        near = [t for t in drawn.texts if plain(t.text).upper() in ("N", "NORTH", "ش")
                and t.point.distance(b.middle) <= 3.0]
        if near:
            b.north = min(near, key=lambda t: t.point.distance(b.middle)).point


def _angle(e) -> float:
    try:
        if e.dxftype() == "MTEXT":
            return float(e.get_rotation()) % 360
        return float(e.dxf.get("rotation", 0.0)) % 360
    except Exception:
        return 0.0


def _height(e) -> float:
    try:
        return float(e.dxf.get("char_height" if e.dxftype() == "MTEXT" else "height", 0.25) or 0.25)
    except Exception:
        return 0.25


def _hatch_outlines(e, tol: float, scale: float) -> list[Polygon]:
    """A hatch's boundaries as polygons, metres (a broken one: none)."""
    import ezdxf.path

    from .extract import _points

    try:
        paths = list(ezdxf.path.from_hatch(e))
    except Exception:
        return []
    out = []
    for path in paths:
        try:
            pts = _points(path, tol)
        except Exception:
            continue
        if pts is None or len(pts) < 3:
            continue
        poly = shapely.make_valid(Polygon([(x * scale, y * scale) for x, y in pts]))
        out.extend(p for p in as_polygons(poly) if p.area > 0)
    return out


# ---- the boundary ------------------------------------------------------------------------

PROPERTY_REACH_M = 1.2  # a property text names the line it lies along this near (or four of its heights)
PROPERTY_ANGLE = 8.0  # degrees: a text lies along a line this near its direction
SAME_EDGE_ANGLE = 1.5  # degrees: two pieces of one edge of the plot lie this near one direction…
SAME_EDGE_M = 0.35  # …and this near one line
MIN_PLOT_M2 = 20.0  # a plot is at least this large


@dataclass
class Boundary:
    polygon: Polygon | None
    how: str  # "property lines", "property layer", "walls", "outline", or "" (none found)
    edges: list = field(default_factory=list)  # the property limits' lines: (point, unit direction)


def _segments(lines: list[Line], keep=lambda line: True) -> list[tuple[tuple[float, float], tuple[float, float], Line]]:
    out = []
    for line in lines:
        if not keep(line):
            continue
        cs = list(line.geom.coords)
        for a, b in zip(cs, cs[1:]):
            if math.dist(a, b) >= 0.05:
                out.append((a, b, line))
    return out


def _direction(a, b) -> float:
    """A segment's direction, degrees in [0, 180)."""
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180


def _turned(a: float, b: float) -> float:
    """How far apart two directions are, degrees, either way round (0 to 90)."""
    d = abs(a - b) % 180
    return min(d, 180 - d)


def _edge_lines(drawn: Drawn) -> tuple[list, str]:
    """The property limits as lines (point, unit direction): pieces on a layer named so,
    else the pieces each property text lies along. And what said so."""
    on_layer = _segments(drawn.lines, lambda line: bool(PROPERTY_LAYER.search(line.layer)))
    pieces, how = [], ""
    if on_layer:
        pieces, how = [(a, b) for a, b, _ in on_layer if math.dist(a, b) >= 1.0], "property layer"
    if not pieces:
        segs = _segments(drawn.lines)
        if segs:
            arr = np.array([(a[0], a[1], b[0], b[1]) for a, b, _ in segs])
            for t in drawn.texts:
                if not PROPERTY_TEXT.search(plain(t.text)):
                    continue
                reach = max(PROPERTY_REACH_M, 4 * t.height)
                best, best_d = None, reach
                for (a, b, _), row in zip(segs, arr):
                    if _turned(_direction(a, b), t.angle) > PROPERTY_ANGLE or math.dist(a, b) < 0.5:
                        continue
                    d = LineString([a, b]).distance(t.point)
                    if d < best_d:
                        best, best_d = (a, b), d
                if best is not None:
                    pieces.append(best)
            how = "property lines" if pieces else ""
    # the pieces of one edge (along one line) are one line
    edges: list[list] = []  # [angle, point on it, unit direction, length]
    for a, b in pieces:
        ang = _direction(a, b)
        u = (math.cos(math.radians(ang)), math.sin(math.radians(ang)))
        for e in edges:
            if _turned(e[0], ang) <= SAME_EDGE_ANGLE and _off_line(e[1], e[2], a) <= SAME_EDGE_M \
                    and _off_line(e[1], e[2], b) <= SAME_EDGE_M:
                e[3] += math.dist(a, b)
                break
        else:
            edges.append([ang, a, u, math.dist(a, b)])
    return [(e[1], e[2]) for e in edges], how


def _off_line(p, u, q) -> float:
    """How far point q is off the line through p along unit direction u."""
    return abs((q[0] - p[0]) * u[1] - (q[1] - p[1]) * u[0])


def boundary(drawn: Drawn, region_m: Polygon | None = None) -> Boundary:
    """The plot (see the module's docstring): what the property limits close round the
    plan's drawing, else the outer outline of its walls."""
    solid = [line.geom for line in drawn.lines if not line.dashed]
    extent = region_m if region_m is not None else (unary_union(solid).envelope if solid else None)
    edges, how = _edge_lines(drawn)
    if len(edges) >= 3 and extent is not None:
        big = extent.buffer(max(math.sqrt(extent.area), 10.0))
        x0, y0, x1, y1 = big.bounds
        far = math.hypot(x1 - x0, y1 - y0)
        cut = [LineString([(p[0] - u[0] * far, p[1] - u[1] * far), (p[0] + u[0] * far, p[1] + u[1] * far)])
               for p, u in edges]
        faces = [f for f in polygonize(unary_union([*cut, big.exterior])) if f.area >= MIN_PLOT_M2]
        if faces:
            # the face holding the most of what is drawn (solid lines' length inside it)
            tree = shapely.STRtree(solid) if solid else None

            def held(face) -> float:
                if tree is None:
                    return 0.0
                inner = face.buffer(-0.05)
                return sum(solid[i].intersection(inner).length for i in tree.query(inner))

            best = max(faces, key=held)
            if held(best) > 0 and best.area < big.area * 0.9:
                return Boundary(shapely.normalize(best), how, edges)
    walled = _walled_outline(drawn)
    if walled is not None:
        return Boundary(walled, "walls", edges)
    return Boundary(None, "", edges)


APART_M = 1.5  # lines this near (each way) are of one drawing: farther, a north arrow or a note beside it


def _walled_outline(drawn: Drawn) -> Polygon | None:
    """The plot when no property limits are drawn: the outline round the largest body of
    lines drawn (lines within APART_M of each other are one: the plan, apart from its
    north arrow, its notes and its title's underline), its walls' outer faces; the gaps
    of its gates closed across. Its convex hull: a plot is seldom anything but convex."""
    solid = [line.geom for line in drawn.lines if not line.dashed and line.geom.length > 0.05]
    if not solid:
        return None
    tree = shapely.STRtree(solid)
    groups = list(range(len(solid)))

    def root(i: int) -> int:
        while groups[i] != i:
            groups[i] = groups[groups[i]]
            i = groups[i]
        return i

    for i, g in enumerate(solid):
        for j in tree.query(g, predicate="dwithin", distance=2 * APART_M):
            a, b = root(i), root(int(j))
            if a != b:
                groups[a] = b
    bodies: dict[int, list] = {}
    for i, g in enumerate(solid):
        bodies.setdefault(root(i), []).append(g)
    body = max(bodies.values(), key=lambda gs: sum(g.length for g in gs))
    hull = shapely.MultiLineString(body).convex_hull
    if not isinstance(hull, Polygon) or hull.area < MIN_PLOT_M2:
        return None
    return shapely.normalize(hull)


# ---- faces, walls and gates ------------------------------------------------------------------

WALL_MAX_M = 0.7  # a face no wider than this is a wall (a fence, a parapet)
EDGE_BAND_M = 0.7  # a wall within this of the plot's edge is a boundary wall
GATE_M = (0.7, 12.0)  # a gap in the boundary walls this wide is a gate (wider: an open side)
SWING_REACH_M = 1.5  # a swing (an arc) this near a gap says it is a gate
MIN_FACE_M2 = 0.05


@dataclass
class Face:
    polygon: Polygon
    thin: bool  # a wall's
    edge: bool = False  # against the plot's edge (a boundary wall's, when thin)
    texts: list = field(default_factory=list)  # (Text, what it says)
    kind: str | None = None  # "wall", "land", "building"
    walled: bool = False  # land closed round by walls that no flood reached: a courtyard


SNAP_M = 0.05  # lines this near (half of it each side) meet: drawings leave hairline gaps


def faces_in(drawn: Drawn, plot: Polygon) -> list[Face]:
    """The faces the solid lines drawn make inside the plot (its edge closing them): what
    is left of it between the lines, each line SNAP_M wide so that lines drawn short of
    meeting meet; each face grown back by half that, as drawn."""
    inside = plot.buffer(0.01)
    lines = []
    for line in drawn.lines:
        if line.dashed or not line.geom.intersects(inside):
            continue
        g = line.geom.intersection(inside)
        lines.extend(g.geoms if hasattr(g, "geoms") else [g])
    lines = [g for g in lines if isinstance(g, LineString) and g.length > 0]
    mass = unary_union([*lines, plot.exterior]).buffer(SNAP_M / 2, cap_style="flat", join_style="mitre")
    faces = []
    for p in as_polygons(plot.difference(mass)):
        p = p.buffer(SNAP_M / 2, join_style="mitre").intersection(plot)
        for q in as_polygons(p):
            if q.area >= MIN_FACE_M2:
                faces.append(Face(q, _thin(q)))
    band = plot.exterior.buffer(EDGE_BAND_M)
    for f in faces:
        f.edge = f.polygon.intersection(band).area >= 0.5 * f.polygon.area if f.thin else \
            f.polygon.exterior.intersection(plot.exterior.buffer(0.02)).length >= 0.5
    return faces


def _thin(p: Polygon) -> bool:
    """Whether a face is no wider than a wall: a circle WALL_MAX_M across fits nowhere in it."""
    try:
        radius = shapely.maximum_inscribed_circle(p, 0.01).length
    except Exception:
        radius = p.area / max(p.length, 1e-9)
    return radius <= WALL_MAX_M / 2


def boundary_walls(faces: list[Face], plot: Polygon):
    """The walls and fences along the plot's edge: its thin faces against it."""
    walls = [f.polygon for f in faces if f.thin and f.edge]
    return unary_union(walls) if walls else None


@dataclass
class Gap:
    span: LineString  # along the plot's edge, from one end of the gap to the other
    swings: int  # door swings (arcs) drawn in it


def wall_gaps(plot: Polygon, covered, drawn: Drawn) -> list[Gap]:
    """The stretches of the plot's edge no wall (nor building) stands on, as wide as a
    gate: ``covered`` is what stands on the edge."""
    ring = plot.exterior
    open_ = ring.difference(covered.buffer(0.05)) if covered is not None and not covered.is_empty else ring
    parts = [g for g in getattr(open_, "geoms", [open_]) if isinstance(g, LineString) and g.length > 0]
    parts = shapely.line_merge(shapely.MultiLineString(parts)) if len(parts) > 1 else (parts[0] if parts else None)
    out = []
    for g in getattr(parts, "geoms", [parts]) if parts is not None else []:
        if not isinstance(g, LineString):
            continue
        (x0, y0), (x1, y1) = g.coords[0], g.coords[-1]
        L = math.hypot(x1 - x0, y1 - y0)
        if L <= 0:
            continue
        ux, uy = (x1 - x0) / L, (y1 - y0) / L  # (as wide as it is: what covered it was grown by)
        span = LineString([(x0 - ux * 0.05, y0 - uy * 0.05), (x1 + ux * 0.05, y1 + uy * 0.05)])
        if not (GATE_M[0] <= span.length <= GATE_M[1]):
            continue
        near = span.buffer(SWING_REACH_M)
        swings = sum(1 for c, r, sweep in drawn.arcs if 0.3 <= r <= 6.0 and 40 <= sweep <= 140 and near.contains(c))
        out.append(Gap(span, swings))
    return out


# ---- land and buildings ------------------------------------------------------------------------

LARGE_SIDE_M = 2.0  # the land touches the boundary walls this long, at least
WALLED_SHARE = 0.6  # a piece of no text whose edge is this much walls is a building
MIN_BUILDING_M2 = 4.0  # a footprint is at least this large
MIN_AREA_M2 = 1.0  # an area of land is at least this large


def _place_texts(faces: list[Face], texts: list[tuple[Text, tuple]]) -> None:
    tree = shapely.STRtree([f.polygon for f in faces])
    for t, said in texts:
        for i in tree.query(t.point, predicate="intersects"):
            faces[i].texts.append((t, said))
            break


def _neighbours(faces: list[Face]) -> list[set[int]]:
    """Which faces share an edge with which (directly: one line between them)."""
    tree = shapely.STRtree([f.polygon for f in faces])
    out = [set() for _ in faces]
    for i, f in enumerate(faces):
        for j in tree.query(f.polygon.buffer(0.01)):
            if j != i and f.polygon.boundary.intersection(faces[j].polygon.boundary.buffer(0.01)).length >= 0.1:
                out[i].add(j)
    return out


def land_and_buildings(faces: list[Face], plot: Polygon, drawn: Drawn, texts: list[tuple[Text, tuple]],
                       gaps: list[Gap]) -> None:
    """Each face's kind: a wall's (thin), the land's, or a building's. Outlines on site
    plans are often left open (a building's walls drawn short of meeting, its round
    corners on another layer), so faces alone cannot say where a building ends: the free
    ground between the lines is flooded from what the texts say (a ground level or an
    area's name, and the inside of each gate, for the land; a building's name or a floor's
    level, for buildings), the widest places first (a watershed on how far each place is
    from the lines: two floods meet at the narrowest place between them, a gap in a
    building's walls). Each face is then what most of it was flooded as; one flooded as
    much by both is cut where they met. A face no flood reached is a building's when walls
    close round it (footprints), else the land's."""
    grid = _Grid(plot, drawn)
    seeds: list[tuple[tuple[int, int], int]] = []
    building = 2
    for t, said in texts:
        if not said or said[0] not in (GROUND, AREA, BUILDING) or not plot.contains(t.point):
            continue
        cell = grid.free_cell_near(t.point)
        if cell is None:
            continue
        if said[0] == BUILDING:
            seeds.append((cell, building))
            building += 1
        else:
            seeds.append((cell, LAND))
    for g in gaps:  # just inside each gate
        mid = g.span.interpolate(0.5, normalized=True)
        inward = _inward(plot, g.span, 0.6)
        if inward is not None and (cell := grid.free_cell_near(inward)) is not None:
            seeds.append((cell, LAND))
    labels = grid.flood(seeds) if seeds else None
    split: list[tuple[Face, Polygon]] = []
    for f in faces:
        if f.thin:
            f.kind = "wall"
            continue
        votes = grid.votes(f.polygon, labels) if labels is not None else {}
        land = votes.get(LAND, 0)
        built = sum(n for k, n in votes.items() if k >= 2)
        if land == 0 and built == 0:
            f.kind = None  # reached by no flood: footprints decides
            continue
        f.kind = "land" if land >= built else "building"
        if min(land, built) * grid.cell ** 2 >= SPLIT_M2:
            split.append(f)
    if split:
        built_region = grid.region(labels, lambda k: k >= 2)
        land_region = grid.region(labels, lambda k: k == LAND)
        c = grid.cell
        for f in split:
            # each piece flooded as a building's is cut off where the floods met, only where
            # that is a gap in the walls (a door, a building's walls drawn short of meeting): a
            # building's text out on open ground floods a stretch of it, met on a wide front
            parts = []
            for q in as_polygons(f.polygon.intersection(built_region)):
                cut = q.boundary.intersection(land_region.buffer(c)).length
                if q.area >= SPLIT_M2 and cut <= OPEN_SIDE_SHARE * q.boundary.length:
                    parts.append(q.buffer(3 * c))
            if not parts:
                continue
            taken = f.polygon.difference(land_region.buffer(c / 2)).intersection(unary_union(parts))
            faces.remove(f)
            for part, kind in ((taken, "building"), (f.polygon.difference(taken), "land")):
                for q in as_polygons(part):
                    if q.area >= MIN_FACE_M2:
                        g = Face(q, False, f.edge, [x for x in f.texts if q.buffer(0.01).contains(x[0].point)], kind)
                        faces.append(g)

LAND = 1
SPLIT_M2 = 3.0  # a face flooded as land and as a building, each over this much, is cut where they met…
OPEN_SIDE_SHARE = 0.4  # …unless that is this much of the building's part's edge (not a gap in walls)
MAX_CELLS = 250_000  # the flood's grid: at most this many cells (a cell 10 cm, or larger on a large plot)


def _inward(plot: Polygon, span: LineString, d: float) -> Point | None:
    """The point ``d`` metres into the plot from the middle of a stretch of its edge."""
    (x0, y0), (x1, y1) = span.coords[0], span.coords[-1]
    mx, my, L = (x0 + x1) / 2, (y0 + y1) / 2, math.hypot(x1 - x0, y1 - y0)
    if L == 0:
        return None
    nx, ny = -(y1 - y0) / L, (x1 - x0) / L
    for sign in (1, -1):
        p = Point(mx + sign * nx * d, my + sign * ny * d)
        if plot.contains(p):
            return p
    return None


class _Grid:
    """The plot as a grid of cells, each free (no line drawn through it) or not, with how
    far its middle is from the nearest line (the plot's edge one of them)."""

    def __init__(self, plot: Polygon, drawn: Drawn):
        x0, y0, x1, y1 = plot.bounds
        self.cell = c = max(0.1, math.sqrt(plot.area / MAX_CELLS))
        self.x0, self.y0 = x0, y0
        nx, ny = max(1, math.ceil((x1 - x0) / c)), max(1, math.ceil((y1 - y0) / c))
        xs = x0 + (np.arange(nx) + 0.5) * c
        ys = y0 + (np.arange(ny) + 0.5) * c
        self.X, self.Y = np.meshgrid(xs, ys)
        inside = shapely.contains_xy(plot, self.X, self.Y)
        segments = [line.geom for line in drawn.lines if not line.dashed and line.geom.intersects(plot)]
        segments.append(plot.exterior)
        tree = shapely.STRtree(segments)
        pts = shapely.points(self.X[inside], self.Y[inside])
        found, dist = tree.query_nearest(pts, return_distance=True, all_matches=False)
        clearance = np.zeros(self.X.shape)
        flat = np.zeros(len(pts))
        flat[found[0]] = dist
        clearance[inside] = flat
        self.clearance = clearance
        self.free = inside & (clearance > c * 0.75)

    def free_cell_near(self, p: Point, reach: float = 1.0) -> tuple[int, int] | None:
        """The free cell at a point, else the nearest free one within ``reach``."""
        c = self.cell
        r, k = int((p.y - self.y0) // c), int((p.x - self.x0) // c)
        n = int(math.ceil(reach / c))
        best, best_d = None, None
        for i in range(max(0, r - n), min(self.free.shape[0], r + n + 1)):
            for j in range(max(0, k - n), min(self.free.shape[1], k + n + 1)):
                if self.free[i, j]:
                    d = (i - r) ** 2 + (j - k) ** 2
                    if best_d is None or d < best_d:
                        best, best_d = (i, j), d
        return best

    def flood(self, seeds: list[tuple[tuple[int, int], int]]) -> np.ndarray:
        """Each free cell's label, flooded from ``seeds`` ((cell, label)) the widest places
        first (a watershed): 0 where no flood reaches."""
        import heapq

        labels = np.zeros(self.free.shape, dtype=np.int32)
        heap: list = []
        n = 0
        free, clear = self.free, self.clearance
        rows, cols = free.shape
        for (r, k), lab in seeds:
            heap.append((-clear[r, k], n, r, k, lab))
            n += 1
        heapq.heapify(heap)
        while heap:
            _, _, r, k, lab = heapq.heappop(heap)
            if labels[r, k]:
                continue
            labels[r, k] = lab
            for i, j in ((r + 1, k), (r - 1, k), (r, k + 1), (r, k - 1)):
                if 0 <= i < rows and 0 <= j < cols and free[i, j] and not labels[i, j]:
                    heapq.heappush(heap, (-clear[i, j], n, i, j, lab))
                    n += 1
        return labels

    def votes(self, poly: Polygon, labels: np.ndarray) -> dict[int, int]:
        """How many cells of each label lie in a polygon."""
        c = self.cell
        x0, y0, x1, y1 = poly.bounds
        r0, r1 = max(0, int((y0 - self.y0) // c)), min(labels.shape[0], int((y1 - self.y0) // c) + 1)
        k0, k1 = max(0, int((x0 - self.x0) // c)), min(labels.shape[1], int((x1 - self.x0) // c) + 1)
        if r0 >= r1 or k0 >= k1:
            return {}
        inside = shapely.contains_xy(poly, self.X[r0:r1, k0:k1], self.Y[r0:r1, k0:k1])
        values, counts = np.unique(labels[r0:r1, k0:k1][inside], return_counts=True)
        return {int(v): int(n) for v, n in zip(values, counts) if v}

    def region(self, labels: np.ndarray, which) -> Polygon:
        """The cells of the labels ``which`` says, as one shape (their rows' runs joined),
        grown by half a cell over the lines between them."""
        c = self.cell
        boxes = []
        mask = np.vectorize(which)(labels) & (labels > 0) if labels.size else labels
        for r in range(mask.shape[0]):
            row = mask[r]
            if not row.any():
                continue
            edges = np.flatnonzero(np.diff(np.concatenate([[0], row.astype(np.int8), [0]])))
            for a, b in zip(edges[::2], edges[1::2]):
                boxes.append(box(self.x0 + a * c, self.y0 + r * c, self.x0 + b * c, self.y0 + (r + 1) * c))
        if not boxes:
            return Polygon()
        return unary_union(boxes).buffer(c, join_style="mitre").buffer(-c / 2, join_style="mitre")


def _walls_along(f: Face, faces: list[Face], near: set[int]) -> float:
    """How long a face runs along boundary walls."""
    walls = [faces[j].polygon for j in near if faces[j].thin and faces[j].edge]
    if not walls:
        return 0.0
    return f.polygon.boundary.intersection(unary_union(walls).buffer(0.02)).length


@dataclass
class FoundFootprint:
    polygon: Polygon
    texts: list[str]  # the texts in it, as written (a building's name among them)
    named: bool  # a building's text is in it
    floor_level: float | None = None  # its floor's level, as written in it (FFL), metres
    ground_level: float | None = None  # the ground's level written nearest it (FGL, NGL), metres


def footprints(faces: list[Face], plot: Polygon) -> list[FoundFootprint]:
    """The buildings standing on the land: the faces flooded as buildings', with the walls
    round them (what of the thin faces lies within a wall's width of them), each piece its
    outline without holes (a courtyard closed round by a building is the building's). A
    face no flood reached is a courtyard (closed round by walls, no text in it), unless it
    lies in a building's outline."""
    built = [f for f in faces if f.kind == "building"]
    if not built:
        for f in faces:
            if f.kind is None:
                f.kind = "land"
        return []
    inner = unary_union([f.polygon for f in built])
    walls = unary_union([f.polygon for f in faces if f.thin])
    grown = inner.buffer(WALL_MAX_M, join_style="mitre").intersection(unary_union([inner, walls]))
    out = []
    taken = []
    for piece in as_polygons(grown.buffer(0.02).buffer(-0.02)):
        outline = Polygon(piece.exterior)
        if outline.area < MIN_BUILDING_M2:
            continue
        members = [f for f in faces if not f.thin and f.polygon.representative_point().within(outline)]
        texts = [t.text for f in members for t, s in f.texts if s is None or s[0] == BUILDING]
        named = any(s and s[0] == BUILDING for f in members for _, s in f.texts)
        for f in members:
            f.kind = "building"
        taken.append(outline)
        out.append(FoundFootprint(shapely.normalize(outline.intersection(plot).buffer(0)), list(dict.fromkeys(texts)),
                                  named))
    for f in faces:
        if f.kind is None:
            f.kind = "land"
            f.walled = True
    return out


def level_of(text: str) -> float | None:
    """The level a text writes, metres (+0.45 FFL, FGL ±0.00, F.F.L. +450): a whole number
    of two digits or more, with no decimal point, is millimetres. None: no number in it."""
    m = LEVEL_VALUE.search(plain(text))
    if m is None:
        return None
    digits = m.group(2).replace(",", ".")
    value = float(digits)
    if "." not in digits and value >= 10:
        value /= 1000
    return -value if m.group(1) in ("-", "−") else value


def levels(standing: list[FoundFootprint], said: list[tuple[Text, tuple]]) -> None:
    """Each footprint's floor level (the lowest FFL written in it) and the ground's level
    nearest it (an FGL or NGL written outside every footprint, within GROUND_NEAR_M): how
    far its floor stands above the ground at its doors, where both are written."""
    grounds = [(t.point, v) for t, s in said if s and s[0] == GROUND and (v := level_of(t.text)) is not None
               and not any(fp.polygon.covers(t.point) for fp in standing)]
    for fp in standing:
        floors = [v for t in fp.texts if FLOOR_LEVEL.search(plain(t)) and (v := level_of(t)) is not None]
        fp.floor_level = min(floors) if floors else None
        near = sorted((fp.polygon.distance(p), v) for p, v in grounds if fp.polygon.distance(p) <= GROUND_NEAR_M)
        fp.ground_level = near[0][1] if near else None


# ---- areas, gates, the road --------------------------------------------------------------------

@dataclass
class FoundArea:
    polygon: Polygon
    type: SpaceType
    type_source: str  # "label" (its text), "model", "walls" (a courtyard), "default" (the yard), "person"
    name: str | None = None  # its text, as read
    label: str | None = None  # its texts as written, joined by line breaks
    issues: list[str] = field(default_factory=list)


@dataclass
class FoundGate:
    span: LineString  # jamb to jamb, along the boundary
    swings: int  # leaves drawn
    connects: list[int] = field(default_factory=list)  # areas (indexes) it joins: inside, then outside
    drawn: bool = False  # drawn by a person in review


def areas(faces: list[Face], plot: Polygon) -> list[FoundArea]:
    """The land's faces as areas: a face holding an area's name is of that type (one
    holding names of several types, or none, is the yard's); faces of no name are the
    yard's together (each piece of it an area); a face closed round by walls that no flood
    reached is a courtyard; one smaller than MIN_AREA_M2 joins the area round it."""
    land = [f for f in faces if f.kind == "land"]
    named, plain_faces = [], []
    for f in land:
        kinds = {s[1] for _, s in f.texts if s and s[0] == AREA and s[1] is not None}
        if len(kinds) == 1 and f.polygon.area >= MIN_AREA_M2:
            words = [plain(t.text) for t, s in f.texts if s and s[0] == AREA]
            named.append((f, next(iter(kinds)), words))
        elif f.walled and f.polygon.area >= MIN_BUILDING_M2:
            named.append((f, SpaceType.COURTYARD, []))
        else:
            plain_faces.append((f, sorted({plain(t.text) for t, s in f.texts if s and s[0] == AREA})))
    out = []
    # named faces of one type side by side are one area
    groups: list[list] = []
    for f, kind, words in named:
        for g in groups:
            if g[1] == kind and any(f.polygon.buffer(0.1).intersects(h.polygon) for h, _ in g[0]):
                g[0].append((f, words))
                break
        else:
            groups.append([[(f, words)], kind])
    for members, kind in groups:
        poly = _joined([f.polygon for f, _ in members])
        words = list(dict.fromkeys(w for _, ws in members for w in ws))
        texts = list(dict.fromkeys(t.text for f, _ in members for t, s in f.texts if s and s[0] == AREA))
        for p in as_polygons(poly):
            out.append(FoundArea(p, kind, "walls" if kind == SpaceType.COURTYARD and not words else "label",
                                 name=words[0] if words else None, label="\n".join(texts) or None))
    rest = _joined([f.polygon for f, _ in plain_faces])
    mixed = sorted({w for _, ws in plain_faces for w in ws})
    for p in as_polygons(rest):
        if p.area < MIN_AREA_M2:
            continue
        issues = [f"names areas of more than one kind with no line between them: {', '.join(mixed)}"] if mixed else []
        out.append(FoundArea(p, SpaceType.YARD, "default", issues=issues))
    # what is too small to be an area of its own joins the one it lies in
    small = [a for a in out if a.polygon.area < MIN_AREA_M2]
    out = [a for a in out if a.polygon.area >= MIN_AREA_M2]
    for a in small:
        host = max(out, key=lambda b: b.polygon.buffer(0.1).intersection(a.polygon).area, default=None)
        if host is not None:
            host.polygon = _joined([host.polygon, a.polygon])
    for a in out:  # holes of what joined them filled
        a.polygon = _fill_small_holes(a.polygon, MIN_BUILDING_M2)
    return out


def _joined(polys) -> Polygon | MultiPolygon:
    """Faces side by side as one shape (the lines between them taken in)."""
    if not polys:
        return Polygon()
    return unary_union([p.buffer(SNAP_M, join_style="mitre") for p in polys]).buffer(-SNAP_M, join_style="mitre")


def _fill_small_holes(geom, max_area: float):
    parts = []
    for p in as_polygons(geom):
        parts.append(Polygon(p.exterior, [h for h in p.interiors if Polygon(h).area > max_area]))
    return parts[0] if len(parts) == 1 else MultiPolygon(parts) if parts else geom


ROAD_DEPTH_M = (6.0, 20.0)  # a road drawn from its name alone: twice as deep as its name is far, within these


def road(plot: Polygon, texts: list[tuple[Text, tuple]]) -> FoundArea | None:
    """The road beside the plot, where a road's name is written outside it: a strip along
    the plot's side nearest the name, as long as that side, twice as deep as the name is
    far from it (within ROAD_DEPTH_M): a site plan seldom draws its far side."""
    names = [t for t, s in texts if s and s[0] == AREA and s[1] == SpaceType.ROAD and not plot.contains(t.point)
             and plot.exterior.distance(t.point) <= ROAD_DEPTH_M[1]]
    if not names:
        return None
    t = min(names, key=lambda t: plot.exterior.distance(t.point))
    ring = list(plot.exterior.coords)
    side = min((LineString([a, b]) for a, b in zip(ring, ring[1:])), key=lambda s: s.distance(t.point))
    (x0, y0), (x1, y1) = side.coords
    L = side.length
    if L <= 0:
        return None
    depth = min(max(2 * side.distance(t.point), ROAD_DEPTH_M[0]), ROAD_DEPTH_M[1])
    nx, ny = -(y1 - y0) / L, (x1 - x0) / L
    if plot.contains(Point((x0 + x1) / 2 + nx * 0.1, (y0 + y1) / 2 + ny * 0.1)):
        nx, ny = -nx, -ny
    strip = Polygon([(x0, y0), (x1, y1), (x1 + nx * depth, y1 + ny * depth), (x0 + nx * depth, y0 + ny * depth)])
    return FoundArea(shapely.normalize(strip), SpaceType.ROAD, "label", name=plain(t.text), label=t.text,
                     issues=["its far side is not drawn: drawn twice as deep as its name is far from the plot"])


def gates(gaps: list[Gap], plot: Polygon, found: list[FoundArea]) -> list[FoundGate]:
    """A gate in each gap of the boundary walls, joining the area just inside it to the one
    just outside (the road), when there is one."""
    out = []
    for g in gaps:
        inner = _inward(plot, g.span, 0.3)
        mid = g.span.interpolate(0.5, normalized=True)
        outer = Point(2 * mid.x - inner.x, 2 * mid.y - inner.y) if inner is not None else None
        joins = []
        for p in (inner, outer):
            if p is None:
                continue
            hit = next((i for i, a in enumerate(found) if a.polygon.buffer(0.2).contains(p)), None)
            if hit is not None and hit not in joins:
                joins.append(hit)
        out.append(FoundGate(g.span, g.swings, joins))
    return out


# ---- north -------------------------------------------------------------------------------------

def north(drawn: Drawn, plot: Polygon | None) -> NorthRead | None:
    """Which way the plan's up faces, from its north arrow: a block named so (or holding an
    N), the one nearest the plot; its direction its N's from its middle when it holds one,
    else its block's up turned as it is placed (upside down, when placed so)."""
    arrows = [b for b in drawn.blocks if NORTH_NAME.search(b.name) and b.middle is not None]
    if not arrows:
        return None
    if plot is not None:
        reach = max(math.sqrt(plot.area), 10.0) * 1.5
        arrows = [b for b in arrows if plot.distance(b.middle) <= reach] or arrows
        b = min(arrows, key=lambda b: plot.distance(b.middle))
    else:
        b = arrows[0]
    turn = b.rotation + (180 if b.flipped else 0)  # counter-clockwise: its up turned so
    bearing, sure, how = turn % 360, 0.7, f"the north arrow ({b.name}) beside the plan, turned {b.rotation:g}°"
    if b.north is not None and b.north.distance(b.middle) > 0.05:  # its N: where it points
        said = (-math.degrees(math.atan2(b.north.x - b.middle.x, b.north.y - b.middle.y))) % 360
        if _turned_full(said, bearing) <= 30:
            sure, how = 0.9, f"the north arrow ({b.name}) beside the plan, turned {b.rotation:g}°, its N that way"
        else:  # drawn turned in its block: its N says
            bearing, sure, how = said, 0.5, f"the N of the north arrow ({b.name}) beside the plan"
    others = [o for o in arrows if o is not b and _turned_full(o.rotation, b.rotation) > 2]
    if others:
        sure -= 0.2
        how += f"; {len(others)} other arrow(s) nearby point elsewhere"
    return NorthRead(bearing=round(bearing, 2) % 360, sure=round(max(sure, 0.1), 2), how=how)


def _turned_full(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


# ---- reading a site plan -----------------------------------------------------------------------

SURROUND_M = 10.0  # a plan's north arrow and the road's name are read this far beyond its region

NAME_NOISE = re.compile(r"\b(proposed|prop|new|existing|exist|ext|future|the|plan|g\s*\+\s*\d+)\b\.?", re.I)


@dataclass
class SitePlanRead:
    """What a site plan was read as, in the layout's frame (metres, less the offset)."""

    boundary: Polygon | None
    boundary_from: str  # property lines, property layer, walls; "" none found
    walls: object  # the boundary walls and fences (and walls standing on the land), as drawn
    wall_thickness: float | None
    areas: list[FoundArea]
    gates: list[FoundGate]
    footprints: list[FoundFootprint]
    north: NorthRead | None
    warnings: list[str]
    middle: tuple[float, float] | None  # the plot's middle, drawing metres (before the offset)
    # what review's gates are put in on (openings.LandBase): the gates of the drawing and the areas
    # (their shapes, in the order of ``areas``), in the drawing's metres, and where the layout's frame is
    # there (dx, dy: the offset in metres)
    gates_found: list = field(default_factory=list)
    areas_found: list = field(default_factory=list)
    frame: tuple[float, float] = (0.0, 0.0)


def building_name(texts: list[str]) -> str | None:
    """A footprint's name from its texts: the one that names a building (VILLA, ANNEX,
    GATE HOUSE), its words for the plan's state (PROPOSED, NEW, G+1) left out; in words
    as people write them."""
    names = [t for t in texts if BUILDING_WORDS.search(plain(t)) and not BUILDING_LEVEL.search(plain(t))]
    if not names:
        return None
    best = max(names, key=lambda t: (len(NAME_NOISE.sub(" ", plain(t)).split()), len(t)))
    words = " ".join(NAME_NOISE.sub(" ", plain(best)).split())
    if not words:
        return None
    return words.title() if words.isupper() else words


def read_site_plan(doc, region=None, units: str | None = None, offset=None, edits=None, read_texts=None,
                   say=None) -> SitePlanRead:
    """A site plan read (see the module's docstring), in the layout's frame: drawing metres
    less ``offset`` (drawing units). ``edits``: what people drew on the layout in review
    (workspace.FloorEdits, local metres): walls and dividers part areas as lines drawn do,
    openings are gates, spaces are areas. ``read_texts(texts)``: what the texts the rules
    do not know say ({text: (BUILDING, None) …}: the language model's answers), or None."""
    say = say or (lambda line: None)
    scale = meters_per_unit(doc, units)
    # (what is beside the plan too: its north arrow, the road's name, its title)
    beside = SURROUND_M / scale
    around = (region[0] - beside, region[1] - beside, region[2] + beside, region[3] + beside) if region else None
    drawn = gather(doc, around, units)
    dx, dy = (offset[0] * scale, offset[1] * scale) if offset else (0.0, 0.0)
    warnings: list[str] = []
    if edits is not None:
        for line in [*edits.walls, *edits.dividers]:
            drawn.lines.append(Line(LineString([(x + dx, y + dy) for x, y in line]), "(drawn in review)", False, False))
    said = [(t, what_text_says(t.text)) for t in drawn.texts]
    unknown = sorted({plain(t.text) for t, s in said if s is None})
    if unknown and read_texts is not None:
        try:
            answers = read_texts(unknown)
        except Exception as e:  # the model is not there: the rules alone
            answers = {}
            warnings.append(f"the language model was not used: {e}")
        said = [(t, answers.get(plain(t.text)) if s is None else s) for t, s in said]
    region_m = box(*(v * scale for v in region)) if region is not None else None
    found = boundary(drawn, region_m)
    if found.polygon is None:
        warnings.append("no plot found: no property limits, nor walls round the plan")
        return SitePlanRead(None, "", None, None, [], [], [], north(drawn, None), warnings, None)
    plot = found.polygon
    say(f"the plot: {plot.area:.0f} m², from its {found.how}")
    faces = faces_in(drawn, plot)
    _place_texts(faces, said)
    first = wall_gaps(plot, boundary_walls(faces, plot), drawn)
    land_and_buildings(faces, plot, drawn, said, first)
    standing = footprints(faces, plot)
    levels(standing, said)
    covered = unary_union([f.polygon for f in faces if (f.thin and f.edge) or f.kind == "building"])
    gaps = wall_gaps(plot, covered, drawn)
    found_areas = areas(faces, plot)
    if (r := road(plot, said)) is not None:
        found_areas.append(r)
    if edits is not None and edits.spaces:  # areas a person drew: theirs, cut out of the others
        drawn_areas = [Polygon([(x + dx, y + dy) for x, y in ring]) for ring in edits.spaces if len(ring) >= 3]
        drawn_areas = [p for p in drawn_areas if p.is_valid and p.area >= MIN_AREA_M2]
        if drawn_areas:
            cut = unary_union(drawn_areas)
            for a in found_areas:
                a.polygon = a.polygon.difference(cut)
            found_areas = [a for a in found_areas if a.polygon.area >= MIN_AREA_M2]
            found_areas += [FoundArea(p, SpaceType.UNSPECIFIED, "person") for p in drawn_areas]
    found_gates = gates(gaps, plot, found_areas)
    gates_found = [replace(g, connects=list(g.connects)) for g in found_gates]  # (before review's: openings.py)
    areas_found = [a.polygon for a in found_areas]
    if edits is not None:
        found_gates = edited_gates(found_gates, areas_found, edits, dx, dy)
    walls_drawn = unary_union([f.polygon for f in _standing_walls(faces) if not any(
        fp.polygon.contains(f.polygon.representative_point()) for fp in standing)])
    thick = _wall_thickness([f.polygon for f in faces if f.thin and f.edge])
    for fp in standing:
        if not fp.named:
            fp.texts = fp.texts or []
    arrow = north(drawn, plot)
    say(f"{len(standing)} building(s), {len(found_areas)} area(s), {len(found_gates)} gate(s)"
        + (f"; north: up faces {arrow.bearing:g}°" if arrow else "; no north arrow"))
    move = (lambda g: shapely.affinity.translate(g, -dx, -dy)) if (dx or dy) else (lambda g: g)
    for a in found_areas:
        a.polygon = move(a.polygon)
    for g in found_gates:
        g.span = move(g.span)
    for fp in standing:
        fp.polygon = move(fp.polygon)
    c = plot.centroid
    return SitePlanRead(move(plot), found.how, move(walls_drawn) if not walls_drawn.is_empty else None, thick,
                        found_areas, found_gates, standing, arrow, warnings, (c.x, c.y), gates_found, areas_found,
                        (dx, dy))


GATE_REACH_M = 0.3  # a gate drawn in review joins the areas this near it…
RESIZED_GATE_M = 1.0  # …and a gate of the drawing given another width is the one whose middle is this near


def edited_gates(gates: list[FoundGate], areas: list, edits, dx: float = 0.0, dy: float = 0.0) -> list[FoundGate]:
    """A layout's gates with what review did to them (``edits``: its drawn edits, local
    metres): each gate drawn joining the areas this near it (``areas``: their shapes, in the
    read's order; two at most), each of the drawing's given another width keeping its
    middle; in the drawing's metres (``dx``, ``dy``: where the layout's frame is there). A
    read of the site plan puts them in so, and so does a change of its gates at once
    (openings.LandBase); ``gates`` are not changed."""
    out = [replace(g, connects=list(g.connects)) for g in gates]
    tree = shapely.STRtree(areas) if areas and edits.openings else None
    for o in edits.openings:
        span = LineString([(x + dx, y + dy) for x, y in o.span])
        near = sorted(int(i) for i in tree.query(span, predicate="dwithin", distance=GATE_REACH_M)) if tree else []
        inside = [i for i in near if areas[i].buffer(GATE_REACH_M).intersects(span)]
        out.append(FoundGate(span, 0, inside[:2], drawn=True))
    for rz in edits.resized:  # the drawing's gates given another width in review: by their middles
        at = Point(rz.at[0] + dx, rz.at[1] + dy)
        near = [(g.span.interpolate(0.5, normalized=True).distance(at), k) for k, g in enumerate(out) if not g.drawn]
        near = [n for n in near if n[0] <= RESIZED_GATE_M]
        if rz.width and near:
            g = out[min(near)[1]]
            (x0, y0), (x1, y1) = g.span.coords[0], g.span.coords[-1]
            mx, my, L = (x0 + x1) / 2, (y0 + y1) / 2, g.span.length
            ux, uy = (x1 - x0) / L, (y1 - y0) / L
            h = rz.width / 2
            g.span = LineString([(mx - ux * h, my - uy * h), (mx + ux * h, my + uy * h)])
    return out


def _standing_walls(faces: list[Face]) -> list[Face]:
    """The thin faces that are walls: not the treads of steps (thin faces side by side
    along their length), nor anything thinner than a wall is built."""
    thin = [f for f in faces if f.thin]
    tree = shapely.STRtree([f.polygon for f in thin]) if thin else None
    out = []
    for i, f in enumerate(thin):
        if 2 * f.polygon.area / max(f.polygon.length, 1e-9) < 0.05:
            continue
        beside = 0
        for j in tree.query(f.polygon.buffer(SNAP_M)):
            if j != i and f.polygon.buffer(SNAP_M).intersection(thin[j].polygon).area >= SNAP_M * 1.0:
                beside += 1
        if beside >= 2 and not f.edge:  # a tread between two others
            continue
        out.append(f)
    return out


def _wall_thickness(walls: list[Polygon]) -> float | None:
    """The boundary walls' typical thickness: each wall face's area over half its edge."""
    sizes = sorted(2 * w.area / w.length for w in walls if w.length > 0 and w.area > 0.05)
    return round(sizes[len(sizes) // 2], 3) if sizes else None
