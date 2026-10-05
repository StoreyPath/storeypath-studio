"""Reading a drawing's layers the way a person does: by what is drawn on them.

Layer names are a hint at best (``jun wall``, ``ELE4``, ``0``). A person looks at
the drawing instead: walls are long pairs of parallel lines a wall's thickness
apart (or concentric arcs), doors are quarter-circle swings as wide as a door,
columns are small repeated squares or circles, room outlines are closed shapes
that tile the floor, the grid is long lines across the whole plan, and room labels
are words that name rooms. This module measures exactly that for every layer of a
plan, and writes the layer-mapping profile that conversion uses.

Each plan of a drawing is read on its own, because one architect's layer can hold
different things on different sheets (door leaves on one plan, roof parapets on
another).
"""

from __future__ import annotations

import copy
import math
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import shapely
from ezdxf.document import Drawing

from .extract import CURVE_TOLERANCE_M, _center, _flatten, _text_lines, _walk, modelspace_entities
from .profile import (
    BlocksConfig,
    DoorsConfig,
    LabelsConfig,
    Profile,
    Rule,
    SpacesConfig,
    WallsConfig,
    load_profile,
)

WALL_GAP_M = (0.05, 0.5)  # the two faces of a wall are this far apart
SWING_RADIUS_M = (0.35, 2.0)
SWING_SWEEP_DEG = (75.0, 105.0)
COLUMN_SIZE_M = (0.15, 1.2)
ROOM_AREA_M2 = (1.5, 2000.0)
MIN_SEGMENT_M = 0.3
ANGLE_BIN_DEG = 2.0
STRUCTURE_JOIN_M = 0.6  # paired lines this close (across a doorway) belong to one structure
WALL_MIN_THICKNESS_M = 0.09  # thinner pairs are glazing, handrails or frames
WALL_MIN_STRUCTURE_M = 8.0  # a layer of walls holds at least this much connected wall
WALL_SHARE_OF_MAIN = 0.2  # …and at least this share of the plan's main wall structure
# A plan may hold several buildings or wings standing apart: every structure this
# big is part of the frame, not only the largest. Cars and furniture stay far smaller.
BUILDING_SHARE = 0.2  # of the largest structure…
BUILDING_MIN_M = 4 * WALL_MIN_STRUCTURE_M  # …and at least this much paired wall line

NAME_HINTS: list[tuple[str, re.Pattern[str]]] = [
    ("window", re.compile(r"glaz|win(d|do)|(^|[^a-z])win([^a-z]|$)|fen[eê]t|fenster|ventana|نافذ|شباك|شبابيك", re.I)),
    ("door", re.compile(r"door|d[oö]r|t[uü]r\b|porte|puerta|باب|أبواب|ابواب", re.I)),
    ("column", re.compile(r"col(s|umn)|s-cols|pilar|st[uü]tze|عمود|اعمدة|أعمدة", re.I)),
    ("wall", re.compile(r"wall|wand|mur\b|muro|pared|جدار|جدران|حائط", re.I)),
    ("room_name", re.compile(r"room.?name|area.?iden|spce.?iden|anno.?room|room.?tag|اسماء|أسماء", re.I)),
    ("room_outline", re.compile(r"^a-area$|area.?bdry|spce$|^rooms?$", re.I)),
    ("furniture", re.compile(r"furn|equip|sanit|plumb|fixt|اثاث|أثاث", re.I)),
    ("dimension", re.compile(r"dim|cota|bema[ßs]|أبعاد|ابعاد", re.I)),
    ("grid", re.compile(r"grid|axis|^ax$|achse|محاور", re.I)),
    ("stairs", re.compile(r"stair|strs|steps?\b|tread|riser|درج|سلم", re.I)),
    ("railing", re.compile(r"rail|balust|guard|gel[aä]nder|garde.?corps|barandilla|درابزين", re.I)),
]
# Layers that a name says hold something else are not read as walls, however
# much their lines look like them: handrails and window frames are pairs of lines
# a wall's thickness apart, step outlines are small closed shapes like columns.
# Windows are then read as glazing in the walls' gaps.
NOT_WALLS = {"furniture", "dimension", "grid", "stairs", "railing"}
# A layer named for doors or windows holds their frames and leaves, which look like
# walls too; it is read as walls only when it holds most of the plan's walls (the
# drafter drew the walls on it).
OPENINGS_NAMED = {"door", "window"}
MOSTLY_WALLS = 0.5


@dataclass
class LayerStats:
    name: str
    entities: int = 0
    length: float = 0.0  # meters of linework (segments of MIN_SEGMENT_M and more)
    dashed: float = 0.0  # of which drawn dashed (hidden, centre lines)
    paired: float = 0.0  # of which with a parallel partner a wall's thickness away
    ladder: float = 0.0  # of which evenly spaced on both sides: stair treads, tiles, hatching
    framed: float = 0.0  # paired length that is part of the plan's main wall frame
    along_frame: float = 0.0  # paired length outside the frame but in line with it (glazing)
    gaps: list[float] = field(default_factory=list)  # partners' distances
    swings: int = 0
    small_closed: int = 0  # column-sized closed shapes (without a text inside: not tags)
    closed_rooms: int = 0  # room-sized closed shapes
    room_shapes: list = field(default_factory=list)  # those shapes (polygons)
    texts: list[str] = field(default_factory=list)
    blocks: int = 0

    @property
    def wall_share(self) -> float:
        return self.paired / self.length if self.length else 0.0

    @property
    def thickness(self) -> float | None:
        return float(np.median(self.gaps)) if self.gaps else None


@dataclass
class LayerRole:
    layer: str
    roles: list[str]
    why: str


@dataclass
class Analysis:
    layers: dict[str, LayerStats]
    roles: list[LayerRole]
    profile: Profile
    wall_thickness: float | None

    def summary(self) -> list[str]:
        return [f"{r.layer}: {', '.join(r.roles)} ({r.why})" for r in self.roles if r.roles]


def name_hint(layer: str) -> str | None:
    for role, pattern in NAME_HINTS:
        if pattern.search(layer):
            return role
    return None


@dataclass
class _Seg:
    layer: str
    pts: tuple[float, float, float, float]
    dashed: bool


@dataclass
class _Arc:
    layer: str
    cx: float
    cy: float
    r: float
    start: float
    sweep: float
    dashed: bool
    paired: bool = False
    swing: bool = False


@dataclass
class Scan:
    """What is drawn, measured: per layer, and where (meters)."""

    stats: dict[str, LayerStats]
    paired: list  # (layer, LineString, gap): lines with a parallel partner a wall's thickness away
    swings: list  # (layer, x, y): door swings
    texts: list  # (layer, text, x, y)


def analyse(
    doc: Drawing,
    scale: float,
    region: tuple[float, float, float, float] | None = None,
    is_room_name: Callable[[str], bool | None] | None = None,
    base: Profile | None = None,
) -> Analysis:
    """Measure every layer of a plan (or of ``region`` of the drawing) and decide
    what each holds. ``is_room_name`` says whether a text names a room (rules or
    the language model; None when unsure). ``base`` supplies the type rules."""
    sc = scan(doc, scale, region)
    thickness = _main_frame(sc.paired, sc.stats)
    names = [(x, y) for _, t, x, y in sc.texts if is_room_name is not None and is_room_name(t)]
    roles = _decide_all(sc.stats, is_room_name, thickness, names)
    return Analysis(sc.stats, roles, _profile(roles, base or load_profile("ncs"), thickness), thickness)


def scan(doc: Drawing, scale: float, region: tuple[float, float, float, float] | None = None) -> Scan:
    """Measure what is drawn in a plan (or ``region``), per layer. Measured once per
    plan and drawing; each caller gets its own copy of the per-layer figures."""
    scans = doc.__dict__.setdefault("_storeypath_scans", {})
    key = (scale, tuple(region) if region else None)
    if key not in scans:
        scans[key] = _scan(doc, scale, region)
    found = scans[key]
    return Scan({k: copy.copy(v) for k, v in found.stats.items()}, found.paired, found.swings, found.texts)


def _scan(doc: Drawing, scale: float, region) -> Scan:
    stats: dict[str, LayerStats] = {}
    texts: list = []
    segs: list[_Seg] = []
    arcs: list[_Arc] = []
    small: list[tuple[str, tuple[float, float, float, float]]] = []  # column-sized shapes
    text_points: list[tuple[float, float]] = []
    tol = CURVE_TOLERANCE_M / scale
    dashed_types = _dashed_linetypes(doc)
    layer_types = {layer.dxf.name: layer.dxf.get("linetype", "Continuous").upper() for layer in doc.layers}

    for e, layer in _walk(modelspace_entities(doc, region)):
        st = stats.setdefault(layer, LayerStats(layer))
        st.entities += 1
        kind = e.dxftype()
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
            text = " ".join(_text_lines(e))
            st.texts.append(text)
            if (c := _center(e)) is not None:
                text_points.append((c[0] * scale, c[1] * scale))
                texts.append((layer, text, c[0] * scale, c[1] * scale))
            continue
        if kind == "INSERT":
            st.blocks += 1
            continue
        if kind in ("HATCH", "DIMENSION", "POINT"):
            continue
        lt = e.dxf.get("linetype", "BYLAYER").upper()
        dashed = (layer_types.get(layer, "CONTINUOUS") if lt == "BYLAYER" else lt) in dashed_types
        if kind == "ARC":
            c = e.ocs().to_wcs(e.dxf.center)
            arcs.append(_Arc(layer, c.x * scale, c.y * scale, e.dxf.radius * scale, e.dxf.start_angle,
                             (e.dxf.end_angle - e.dxf.start_angle) % 360, dashed))
            continue
        flat = _flatten(e, tol)
        if flat is None:
            continue
        pts, closed = flat
        pts = [(x * scale, y * scale) for x, y in pts]
        if closed and len(pts) >= 3:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if COLUMN_SIZE_M[0] <= max(w, h) <= COLUMN_SIZE_M[1]:
                small.append((layer, (min(xs), min(ys), max(xs), max(ys))))
            elif ROOM_AREA_M2[0] <= abs(_shoelace(pts)) <= ROOM_AREA_M2[1] and min(w, h) >= 0.8:
                st.closed_rooms += 1
                st.room_shapes.append(shapely.Polygon(pts))
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            if math.hypot(x2 - x1, y2 - y1) >= MIN_SEGMENT_M:
                segs.append(_Seg(layer, (x1, y1, x2, y2), dashed))

    _count_columns(small, text_points, stats)
    paired_lines = _pair_segments(segs, stats)
    paired_lines += _pair_arcs(arcs, stats)
    swings = [(a.layer, a.cx, a.cy) for a in arcs if a.swing]
    return Scan(stats, paired_lines, swings, texts)


def _count_columns(small, text_points, stats) -> None:
    """Column-sized shapes, leaving out tags: a shape with a text inside it (W4, D1)."""
    pts = np.array(text_points) if text_points else np.zeros((0, 2))
    for layer, (x0, y0, x1, y1) in small:
        inside = (pts[:, 0] >= x0) & (pts[:, 0] <= x1) & (pts[:, 1] >= y0) & (pts[:, 1] <= y1)
        if not inside.any():
            stats[layer].small_closed += 1


def plan_texts(doc: Drawing, region=None, per_layer: int | None = None, unknown=None) -> list[str]:
    """The distinct short texts of a plan: the ones that could name rooms. With
    ``per_layer``, at most that many of each layer's texts that ``unknown`` says are
    not known yet: a few examples are enough to tell what a layer holds."""
    by_layer: dict[str, list[str]] = defaultdict(list)
    for e, layer in _walk(modelspace_entities(doc, region)):
        if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
            text = " ".join(_text_lines(e)).strip()
            if text and len(text) <= 40 and len(text.split()) <= 5 and text not in by_layer[layer]:
                by_layer[layer].append(text)
    out = []
    for texts in by_layer.values():
        if per_layer is not None:
            texts = [t for t in texts if unknown is None or unknown(t)][:per_layer]
        out += texts
    return list(dict.fromkeys(out))


def _dashed_linetypes(doc) -> set[str]:
    """Names of the drawing's linetypes that have gaps in them."""
    out = set()
    for lt in doc.linetypes:
        try:
            pattern = lt.simplified_line_pattern()
        except Exception:
            pattern = ()
        if len(pattern) > 1:
            out.add(lt.dxf.name.upper())
    return out


def _shoelace(pts) -> float:
    a = np.asarray(pts)
    return 0.5 * float(np.dot(a[:, 0], np.roll(a[:, 1], -1)) - np.dot(a[:, 1], np.roll(a[:, 0], -1)))


def _pair_segments(segs: list[_Seg], stats) -> list[tuple[str, object]]:
    """For every solid segment, the length along which another runs parallel to it a
    wall's thickness away. Returns the paired segments as (layer, geometry)."""
    for sg in segs:
        x1, y1, x2, y2 = sg.pts
        length = math.hypot(x2 - x1, y2 - y1)
        st = stats[sg.layer]
        st.length += length
        if sg.dashed:
            st.dashed += length
    solid = [sg for sg in segs if not sg.dashed]
    if not solid:
        return []
    p = np.array([sg.pts for sg in solid], dtype=float)
    d = p[:, 2:] - p[:, :2]
    length = np.hypot(d[:, 0], d[:, 1])
    theta = np.mod(np.arctan2(d[:, 1], d[:, 0]), np.pi)
    nbins = int(180 / ANGLE_BIN_DEG)
    bins = np.floor(np.degrees(theta) / ANGLE_BIN_DEG).astype(int) % nbins
    # Per segment and side (below, above): the longest overlap with a partner, and its gap.
    best = np.zeros((len(solid), 2))
    partner_gap = np.full((len(solid), 2), np.nan)
    by_bin: dict[int, list[int]] = defaultdict(list)
    for i, b in enumerate(bins):
        by_bin[int(b)].append(i)
    for b, members in by_bin.items():
        group = np.array(members + by_bin.get((b + 1) % nbins, []))
        if len(group) < 2:
            continue
        ang = np.radians((b + 1) * ANGLE_BIN_DEG)  # the shared direction of the two bins
        u = np.array([math.cos(ang), math.sin(ang)])
        n = np.array([-u[1], u[0]])
        offset = ((p[group, :2] + p[group, 2:]) / 2) @ n
        t1, t2 = p[group, :2] @ u, p[group, 2:] @ u
        lo, hi = np.minimum(t1, t2), np.maximum(t1, t2)
        order = np.argsort(offset)
        offset, lo, hi, group = offset[order], lo[order], hi[order], group[order]
        own = np.isin(group, members)
        j0 = 0
        for i in range(len(group)):
            while offset[i] - offset[j0] > WALL_GAP_M[1]:
                j0 += 1
            for j in range(j0, i):
                gap = offset[i] - offset[j]
                if gap < WALL_GAP_M[0] or not (own[i] or own[j]):
                    continue
                overlap = min(hi[i], hi[j]) - max(lo[i], lo[j])
                if overlap <= 0:
                    continue
                for k, side in ((group[i], 0), (group[j], 1)):  # j lies below i
                    if overlap > best[k, side]:
                        best[k, side] = min(overlap, length[k])
                        partner_gap[k, side] = gap
    out = []
    for i, sg in enumerate(solid):
        st = stats[sg.layer]
        below, above = best[i]
        if below > 0 and above > 0 and abs(partner_gap[i, 0] - partner_gap[i, 1]) < 0.03:
            st.ladder += float(max(below, above))  # a rung: treads, tiles, hatch lines
        elif below > 0 or above > 0:
            side = 0 if below >= above else 1
            st.paired += float(best[i, side])
            st.gaps.append(float(partner_gap[i, side]))
            out.append((sg.layer, shapely.LineString([sg.pts[:2], sg.pts[2:]]), float(partner_gap[i, side])))
    return out


def _pair_arcs(arcs: list[_Arc], stats) -> list[tuple[str, object]]:
    """Curved walls and rounded corners: solid arcs around the same centre a wall's
    thickness apart. A single quarter arc of door width is a door swing."""
    by_centre: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, a in enumerate(arcs):
        by_centre[(round(a.cx / 0.1), round(a.cy / 0.1))].append(i)
    out = []
    for i, a in enumerate(arcs):
        st = stats[a.layer]
        length = a.r * math.radians(a.sweep)
        st.length += length
        if a.dashed:
            st.dashed += length
        key = (round(a.cx / 0.1), round(a.cy / 0.1))
        for j in (j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in by_centre.get((key[0] + dx, key[1] + dy), [])):
            b = arcs[j]
            if j != i and not (a.dashed or b.dashed) and WALL_GAP_M[0] <= abs(b.r - a.r) <= WALL_GAP_M[1] \
                    and _angles_overlap(a.start, a.sweep, b.start, b.sweep):
                a.paired = True
                st.paired += length
                st.gaps.append(abs(b.r - a.r))
                angles = np.radians(np.linspace(a.start, a.start + a.sweep, 9))
                out.append((a.layer, shapely.LineString(np.c_[a.cx + a.r * np.cos(angles), a.cy + a.r * np.sin(angles)]),
                            abs(b.r - a.r)))
                break
        if not a.paired and SWING_RADIUS_M[0] <= a.r <= SWING_RADIUS_M[1] \
                and SWING_SWEEP_DEG[0] <= a.sweep <= SWING_SWEEP_DEG[1]:
            st.swings += 1
            a.swing = True
    return out


def _main_frame(paired_lines, stats) -> float | None:
    """The plan's main wall frame: all solid paired lines, from every layer, joined
    where they meet or are a doorway apart; the largest such structure, and any
    other nearly as big (wings or buildings standing apart). Walls are often split
    over several layers, but they always form frames, while cars, furniture and
    equipment stay in small separate pieces. Records each layer's share of the frame
    and returns the frame's wall thickness."""
    lines = [(layer, geom, gap) for layer, geom, gap in paired_lines if gap >= WALL_MIN_THICKNESS_M]
    if not lines:
        return None
    root_of: dict[int, int] = {}
    geoms = np.array([g for _, g, _ in lines], dtype=object)
    blobs = shapely.buffer(geoms, STRUCTURE_JOIN_M)
    parent = list(range(len(lines)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    left, right = shapely.STRtree(blobs).query(blobs, predicate="intersects")
    for i, j in zip(left, right):
        parent[root(int(i))] = root(int(j))
    sizes: dict[int, float] = defaultdict(float)
    for i, g in enumerate(geoms):
        sizes[root(i)] += g.length
        root_of[id(g)] = root(i)
    largest = max(sizes.values())
    mains = {r for r, size in sizes.items() if size == largest or size >= max(BUILDING_MIN_M, BUILDING_SHARE * largest)}
    gaps, weights, frame = [], [], []
    for i, (layer, g, gap) in enumerate(lines):
        if root(i) in mains:
            stats[layer].framed += g.length
            gaps.append(gap)
            weights.append(g.length)
            frame.append(g)
    # Glazing fills the gaps of the frame: on a wall's own line, within a window's
    # width of where the wall stops.
    others = [(layer, g) for layer, g, gap in paired_lines
              if (gap < WALL_MIN_THICKNESS_M or root_of.get(id(g)) not in mains) and len(g.coords) == 2]
    if others:
        inline = _in_line([g for _, g in others], [g for g in frame if len(g.coords) == 2])
        for (layer, g), ok in zip(others, inline):
            if ok:
                stats[layer].along_frame += g.length
    order = np.argsort(gaps)
    cumulative = np.cumsum(np.array(weights)[order])
    thickness = float(np.array(gaps)[order][np.searchsorted(cumulative, cumulative[-1] / 2)])
    # Pairs much thinner than the walls but joined to the frame: glazing in its gaps.
    for i, (layer, g, gap) in enumerate(lines):
        if root(i) in mains and gap < 0.7 * thickness:
            stats[layer].along_frame += g.length
    return thickness


def _in_line(segments, frame, offset_tol: float = 0.35, reach: float = 4.0) -> list[bool]:
    """For each segment: does it lie on the line of a frame segment (same direction,
    within ``offset_tol`` across), no more than ``reach`` beyond its end?"""
    def params(geoms):
        c = np.array([np.asarray(g.coords).ravel() for g in geoms])
        d = c[:, 2:] - c[:, :2]
        theta = np.mod(np.arctan2(d[:, 1], d[:, 0]), np.pi)
        return c, theta

    fc, ftheta = params(frame)
    sc, stheta = params(segments)
    out = []
    for (x1, y1, x2, y2), th in zip(sc, stheta):
        dth = np.abs(np.mod(ftheta - th + np.pi / 2, np.pi) - np.pi / 2)
        cand = np.nonzero(dth < np.radians(3))[0]
        if not len(cand):
            out.append(False)
            continue
        u = np.array([math.cos(th), math.sin(th)])
        n = np.array([-u[1], u[0]])
        f = fc[cand]
        mid_f = (f[:, :2] + f[:, 2:]) / 2
        across = np.abs((mid_f - np.array([(x1 + x2) / 2, (y1 + y2) / 2])) @ n)
        a, b = sorted((np.dot([x1, y1], u), np.dot([x2, y2], u)))
        fa, fb = f[:, :2] @ u, f[:, 2:] @ u
        lo, hi = np.minimum(fa, fb), np.maximum(fa, fb)
        apart = np.maximum(0, np.maximum(lo - b, a - hi))
        out.append(bool(np.any((across <= offset_tol) & (apart <= reach))))
    return out


def _angles_overlap(a0, s0, a1, s1) -> bool:
    def inside(a, start, sweep):
        return (a - start) % 360 <= sweep
    return any(inside(a, a1, s1) for a in (a0, a0 + s0 / 2, a0 + s0)) or inside(a1, a0, s0)


def _decide_all(stats: dict[str, LayerStats], is_room_name, thickness: float | None, names=()) -> list[LayerRole]:
    """What each layer holds. Walls are judged against the plan's main wall frame:
    a layer is walls when a real part of the frame is drawn on it."""
    frame = sum(st.framed for st in stats.values())
    points = np.array(names) if len(names) else np.zeros((0, 2))
    return [_decide(st, is_room_name, frame, thickness, _one_room_each(st, points)) for st in stats.values()]


OUTLINED_SHARE = 0.3  # room outlines hold at least this share of the plan's room names


def _one_room_each(st: LayerStats, points) -> bool:
    """Whether a layer's closed shapes are room outlines: each holds at most one
    room's name, and together they hold a good share of the plan's names. The inside
    faces of a wall network also close, but door gaps join rooms, so one shape holds
    several names; lift cars or equipment boxes each hold a name too, but only of
    the few rooms they stand in."""
    if st.closed_rooms < 3 or not len(points):
        return False
    counts = [int(np.sum(shapely.contains_xy(poly, points[:, 0], points[:, 1]))) for poly in st.room_shapes]
    named = sum(1 for c in counts if c == 1)
    return sum(1 for c in counts if c <= 1) >= 0.8 * len(counts) and named >= max(2, OUTLINED_SHARE * len(points))


NUMBER_OR_TAG = re.compile(r"^([a-z]{1,2}\s?-?\s?)?\d{1,4}[a-z]?$", re.IGNORECASE)  # 201, D1, W-10, 12A

def _decide(st: LayerStats, is_room_name, frame: float, thickness: float | None, outlines: bool) -> LayerRole:
    hint = name_hint(st.name)
    roles, why = [], []
    names = [t for t in st.texts if t.strip()]
    if names and is_room_name is not None:
        # Each word once, so a tag by every air conditioner (SAC UNIT) does not outvote
        # the room names beside it; numbers and tags (201, D1) say nothing either way.
        words = [t for t in dict.fromkeys(t.strip() for t in names) if not NUMBER_OR_TAG.match(t)]
        known = [v for v in (is_room_name(t) for t in words) if v is not None]
        rooms = sum(1 for v in known if v)
        if rooms >= 2 and rooms >= 0.5 * len(known):
            roles.append("labels")
            why.append(f"{rooms} of {len(names)} texts name rooms")
    elif hint == "room_name" and names:
        roles.append("labels")
        why.append("layer name")
    if outlines and st.closed_rooms * 4 >= st.entities - len(st.texts) and hint in (None, "room_outline"):
        roles.append("outlines")
        why.append(f"{st.closed_rooms} room outlines, one name each")
    own_gap = st.thickness or 0.0
    in_frame = st.framed >= max(WALL_MIN_STRUCTURE_M / 2, 0.05 * frame) and st.framed >= 0.3 * st.paired
    thick_enough = thickness is None or own_gap >= 0.7 * thickness
    if st.dashed > 0.5 * st.length:
        why.append("drawn dashed (hidden or centre lines)")
    elif st.ladder > st.paired:
        why.append(f"{st.ladder:.0f} m of evenly spaced lines (stairs or a pattern)")
    elif ("outlines" not in roles and in_frame and thick_enough and hint not in NOT_WALLS
          and (hint not in OPENINGS_NAMED or st.framed >= MOSTLY_WALLS * frame)):
        roles.append("walls")
        why.append(f"{st.framed / 2:.0f} m of the wall frame, {own_gap:.2f} m thick")
    elif st.small_closed >= 4 and st.small_closed * 2 >= st.entities - len(st.texts) and hint in (None, "column", "wall"):
        roles.append("walls")
        why.append(f"{st.small_closed} columns")
    glazing = ("walls" not in roles and "outlines" not in roles and st.along_frame >= max(2.0, 0.5 * st.paired)
               and st.ladder <= st.paired and st.dashed <= 0.5 * st.length
               and hint not in ("furniture", "stairs", "dimension", "grid"))
    if st.swings >= 2 or glazing or (hint in ("door", "window") and st.entities > len(st.texts)):
        roles.append("openings")
        why.append(f"{st.swings} door swings" if st.swings >= 2 else
                   f"{st.along_frame:.0f} m of glazing in line with the walls" if glazing
                   else f"name suggests {hint}s")
    return LayerRole(st.name, roles, "; ".join(why) or (f"name suggests {hint}" if hint else "nothing recognised"))


def _profile(roles: list[LayerRole], base: Profile, thickness: float | None) -> Profile:
    def layers(role: str) -> list[str]:
        return [re.escape(r.layer) for r in roles if role in r.roles]

    walls = WallsConfig(**{**base.walls.model_dump(), "layers": layers("walls")})
    if thickness:
        walls.max_thickness = max(walls.max_thickness, round(thickness * 2 + 0.1, 2))
    outlines = layers("outlines")
    return Profile(
        name="auto",
        description="written by StoreyPath from what is drawn on each layer",
        spaces=SpacesConfig(**{**base.spaces.model_dump(), "layers": outlines,
                               "method": "auto" if outlines else "walls"}),
        walls=walls,
        labels=LabelsConfig(**{**base.labels.model_dump(), "layers": layers("labels")}),
        doors=DoorsConfig(**{**base.doors.model_dump(), "layers": layers("openings")}),
        blocks=BlocksConfig(layers=[".*"]),  # block names speak for themselves (ELEVATOR_CAR)
        rules=[Rule(**r.model_dump(exclude_none=True)) for r in base.rules],
    )
