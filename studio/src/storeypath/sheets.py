"""Drawings that hold several plans side by side.

Architects often put every floor plan (and elevations, sections, a site plan) in
one drawing. ``find_views`` finds the plans in it: groups of walls with a title
under them. ``align_floors`` works out how far apart the floor plans of one
building were drawn, so they can be stacked: floors share columns and outside
walls, so the plans line up where their walls overlap most.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import ezdxf.bbox
import numpy as np
import shapely
from ezdxf.document import Drawing
from shapely.geometry import box

from .extract import _center, _flatten, _text_lines, _walk, _wall_hatch, modelspace_entities, CURVE_TOLERANCE_M
from .geometry import as_polygons
from .profile import Profile
from .walls import wall_mass

VIEW_GAP_M = 3.0  # walls closer than this belong to the same plan
VIEW_MIN_AREA_M2 = 20.0
VIEW_MIN_ENTITIES = 20
VIEW_MARGIN_M = 1.0
VIEW_MIN_WALLS_M = 15.0  # a plan has at least this much wall
FRAME_EDGE_M = 1.5
FRAME_MIN_M = 40.0  # a sheet frame is at least this big (an A3 sheet at 1:100)
VIEW_MAX_ENTITY_M = 120.0  # anything larger is a frame around sheets, not a wall
TITLE_BELOW_M = 15.0  # how far under a plan its title may sit (past grid bubbles and dimensions)
ALIGN_CELL_M = 0.1

TITLE_WORDS = re.compile(r"\b(plan|floor|level|storey|story|basement|roof|mezzanine)\b", re.IGNORECASE)
ORDINAL_WORDS = [
    (re.compile(r"\b(basement|cellar|b\.?\s?f\.?)\b", re.I), -1),
    (re.compile(r"\b(ground|g\.?\s?f\.?)\b", re.I), 0),
    (re.compile(r"\b(first|1st)\b", re.I), 1),
    (re.compile(r"\b(second|2nd)\b", re.I), 2),
    (re.compile(r"\b(third|3rd)\b", re.I), 3),
    (re.compile(r"\b(fourth|4th)\b", re.I), 4),
]
ROOF_WORDS = re.compile(r"\b(roof|terrace|penthouse)\b", re.IGNORECASE)
NOT_PLAN_WORDS = re.compile(r"\b(elevation|section|facade|detail)\b", re.IGNORECASE)


@dataclass
class View:
    index: int  # 1-based, top to bottom, left to right
    title: str | None
    region: tuple[float, float, float, float]  # drawing units
    size: tuple[float, float]  # meters
    entities: int
    ordinal: int | None  # guessed from the title; roof plans are one above the highest
    preview: list[list[float]] = field(default_factory=list)  # wall lines, meters from the region's corner

    @property
    def is_plan(self) -> bool:
        """False for views titled as elevations, sections or details."""
        return not (self.title and NOT_PLAN_WORDS.search(self.title) and "PLAN" not in self.title.upper())

    def matches(self, query: str) -> bool:
        return query.isdigit() and int(query) == self.index or (
            self.title is not None and query.lower() in self.title.lower()
        )


def find_views(doc: Drawing, profile: Profile, scale: float, auto: bool = False, is_room_name=None) -> list[View]:
    """The plans in a drawing: groups of walls, with their titles.

    With ``auto`` the wall layers are not known in advance. Then, like a person
    scanning a sheet set, the drawing is searched for wall structures (pairs of
    parallel lines a wall's thickness apart, from any layer) and those with door
    swings or room names inside are the plans; tables, frames and elevations have
    neither."""
    texts = _texts(doc)
    if auto:
        views = _plans_by_walls(doc, scale, texts, is_room_name)
    else:
        views = _group(_boxes(doc, scale, profile.wall_layers.fullmatch), texts, scale)
    views.sort(key=lambda v: (-round(v.region[3] * scale / 10), v.region[0]))
    for i, v in enumerate(views, 1):
        v.index = i
        v.ordinal = _ordinal(v.title)
    top = max((v.ordinal for v in views if v.ordinal is not None), default=None)
    for v in views:
        if v.title and ROOF_WORDS.search(v.title) and v.ordinal is None:
            v.ordinal = (top + 1) if top is not None else None
    return views


def _plans_by_walls(doc, scale, texts, is_room_name) -> list[View]:
    from .analyse import WALL_MIN_THICKNESS_M, scan

    sc = scan(doc, scale)
    walls = [g for _, g, gap in sc.paired if gap >= WALL_MIN_THICKNESS_M]
    if not walls:
        return []
    tree = shapely.STRtree(walls)
    swing_pts = np.array([(x, y) for _, x, y in sc.swings]) if sc.swings else np.zeros((0, 2))
    room_pts = np.array([(x, y) for _, t, x, y in sc.texts if is_room_name and is_room_name(t)]) \
        if sc.texts else np.zeros((0, 2))
    found = []
    for members in _wall_groups(walls):
        if sum(m.length for m in members) / 2 >= VIEW_MIN_WALLS_M:  # each wall has two faces
            found.append((members, shapely.union_all(members).bounds))
    views = []
    for members, (x0, y0, x1, y1) in found:
        if _is_frame(members, (x0, y0, x1, y1), [b for _, b in found]):
            continue

        def inside(pts):
            return int(np.sum((pts[:, 0] >= x0) & (pts[:, 0] <= x1) & (pts[:, 1] >= y0) & (pts[:, 1] <= y1))) \
                if len(pts) else 0

        doors, rooms = inside(swing_pts), inside(room_pts)
        if doors < 2 and not (doors >= 1 and rooms >= 3):
            continue  # walls without doors: a table, title block, frame or elevation
        m = VIEW_MARGIN_M
        region = ((x0 - m) / scale, (y0 - m) / scale, (x1 + m) / scale, (y1 + m) / scale)
        ox, oy = x0 - m, y0 - m
        preview = [[round(v, 2) for x, y in line.coords for v in (x - ox, y - oy)] for line in members][:6000]
        views.append(View(0, _title(tuple(v / scale for v in (x0, y0, x1, y1)), texts, scale), region,
                          (round(x1 - x0, 1), round(y1 - y0, 1)), len(members), None, preview))
    return views


def _wall_groups(walls, depth: int = 0) -> list[list]:
    """Wall lines grouped by nearness. A group that is mostly a border (a sheet frame
    with a plan drawn close to it) is split: the border is dropped, the rest regrouped."""
    if not walls:
        return []
    blobs = as_polygons(shapely.union_all(shapely.buffer(np.array(walls, dtype=object), VIEW_GAP_M / 2)))
    tree = shapely.STRtree(walls)
    out = []
    for blob in blobs:
        members = [walls[int(i)] for i in tree.query(blob, predicate="intersects")]
        x0, y0, x1, y1 = shapely.union_all(members).bounds
        edge = shapely.box(x0, y0, x1, y1).boundary.buffer(FRAME_EDGE_M)
        on_edge = [m for m in members if m.intersection(edge).length >= 0.8 * m.length]
        big = max(x1 - x0, y1 - y0) >= FRAME_MIN_M
        if depth == 0 and big and sum(m.length for m in on_edge) >= 0.2 * sum(m.length for m in members):
            inner = [m for m in members if m not in on_edge]
            out += _wall_groups(inner, depth + 1)
        else:
            out.append(members)
    return out


def _is_frame(members, bounds, all_bounds) -> bool:
    """A frame around a drawing (a sheet border drawn as a double line): it holds
    other drawings and most of its length runs along its own outside edge."""
    x0, y0, x1, y1 = bounds
    holds_others = any(b != bounds and b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1 for b in all_bounds)
    if not holds_others:
        return False
    edge = shapely.box(x0, y0, x1, y1).boundary.buffer(FRAME_EDGE_M)
    total = sum(m.length for m in members)
    return sum(m.intersection(edge).length for m in members) >= 0.4 * total


def _texts(doc: Drawing):
    out = []
    for e in doc.modelspace():
        kind = e.dxftype()
        if kind in ("TEXT", "MTEXT"):
            lines, c = _text_lines(e), _center(e)
            height = e.dxf.get("char_height" if kind == "MTEXT" else "height", 0) or 0
            if lines and c:
                out.append((" ".join(lines), c, height))
    return out


def _boxes(doc: Drawing, scale: float, keep_layer, region=None) -> list:
    """Extents of the top-level entities on the layers ``keep_layer`` accepts."""
    cache = ezdxf.bbox.Cache()
    boxes = []
    for e in modelspace_entities(doc, region):
        if e.dxftype() in ("TEXT", "MTEXT", "DIMENSION") or not keep_layer(e.dxf.get("layer", "0")):
            continue
        try:
            ext = ezdxf.bbox.extents([e], fast=True, cache=cache)
        except Exception:
            continue
        if ext.has_data and max(ext.size.x, ext.size.y) * scale <= VIEW_MAX_ENTITY_M:
            boxes.append(box(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y))
    return boxes


def _group(boxes, texts, scale) -> list[View]:
    """Plans: groups of wall entities lying together."""
    if not boxes:
        return []
    gap = VIEW_GAP_M / scale
    groups = as_polygons(shapely.union_all(shapely.buffer(boxes, gap / 2, join_style="mitre")))
    tree = shapely.STRtree(boxes)
    views = []
    for g in groups:
        members = [boxes[int(i)] for i in tree.query(g, predicate="contains")]
        if len(members) < VIEW_MIN_ENTITIES:
            continue
        x0, y0, x1, y1 = shapely.union_all(members).bounds
        if (x1 - x0) * (y1 - y0) * scale * scale < VIEW_MIN_AREA_M2:
            continue
        m = VIEW_MARGIN_M / scale
        views.append(View(0, _title((x0, y0, x1, y1), texts, scale), (x0 - m, y0 - m, x1 + m, y1 + m),
                          (round((x1 - x0) * scale, 1), round((y1 - y0) * scale, 1)), len(members), None))
    return views


def _title(bounds, texts, scale) -> str | None:
    """The title of a plan: the most title-like text just under it (or inside its
    bottom edge), as wide as the plan."""
    x0, y0, x1, y1 = bounds
    below = TITLE_BELOW_M / scale
    candidates = []
    for text, (x, y), height in texts:
        if x0 <= x <= x1 and y0 - below <= y <= y0 + (y1 - y0) * 0.1:
            candidates.append((bool(TITLE_WORDS.search(text)), height, -abs(y - y0), text))
    if not candidates:
        return None
    best = max(candidates)
    return best[3] if best[0] or best[1] * scale >= 0.3 else None


TITLE_KINDS = [
    ("elevation", re.compile(r"\b(elevations?|fa[cç]ades?)\b", re.I)),
    ("section", re.compile(r"\b(sections?|cross.?section)\b", re.I)),
    ("site_plan", re.compile(r"\b(site|location|development|key)\s+plan\b", re.I)),
    ("schedule", re.compile(r"\bschedules?\b", re.I)),
    ("roof_plan", re.compile(r"\broof\b.*\bplan\b", re.I)),
    ("floor_plan", re.compile(r"\b(floor|level|storey|story|basement|mezzanine)\b.*\bplan\b|\bplan\b", re.I)),
]
# Words a title may hold besides its kind and floor without naming anything else.
PLAIN_TITLE_WORDS = re.compile(
    r"\b(modified|proposed|existing|new|revised|typical|the|of|floor|level|plan|layout|drawing|"
    r"ground|first|second|third|fourth|basement|roof|deck|1st|2nd|3rd|4th|g\.?f\.?|elevations?|front|rear|back|"
    r"left|right|side|north|south|east|west|sections?|cross|site|location|development|key|schedule|of|"
    r"openings?|doors?|windows?|scale|[a-z]-[a-z]|\d+)\b|[^\w]", re.I)


def read_title(title: str | None):
    """What a sheet title says, from the words alone: (kind, floor). None when the
    title is not plainly one of the common English forms or names something more
    (a building: OUT KITCHEN FLOOR PLAN) — those are for the language model."""
    if not title:
        return None
    if PLAIN_TITLE_WORDS.sub("", title).strip():
        return None
    for kind, pattern in TITLE_KINDS:
        if pattern.search(title):
            floor = _ordinal(title) if kind == "floor_plan" else None
            if kind == "floor_plan" and floor is None:
                return None
            return kind, floor
    return None


def _ordinal(title: str | None) -> int | None:
    if not title:
        return None
    for pattern, ordinal in ORDINAL_WORDS:
        if pattern.search(title):
            return ordinal
    return None


def floor_walls(doc: Drawing, profile: Profile, scale: float, region):
    """The wall mass of one plan (meters, drawing position)."""
    tol = CURVE_TOLERANCE_M / scale
    lines, fills = [], []
    for e, layer in _walk(modelspace_entities(doc, region)):
        if not profile.wall_layers.fullmatch(layer) or e.dxftype() in ("TEXT", "MTEXT", "ATTRIB", "INSERT"):
            continue
        if e.dxftype() == "HATCH":
            _wall_hatch(e, tol, scale, profile.walls.max_thickness, lines, fills)
            continue
        flat = _flatten(e, tol)
        if flat:
            pts, closed = flat
            pts = [(x * scale, y * scale) for x, y in pts]
            lines.append(shapely.LineString(pts + pts[:1] if closed and pts[0] != pts[-1] else pts))
    return wall_mass(lines, fills, profile.walls)


def align(reference, other) -> tuple[tuple[float, float], float]:
    """How far ``other`` (a wall mass) lies from ``reference``: the shift (meters)
    to subtract from ``other`` so that it lines up, and the share of the smaller
    plan's walls that then overlap."""
    a, (ax, ay) = _raster(reference)
    b, (bx, by) = _raster(other)
    shape = (a.shape[0] + b.shape[0], a.shape[1] + b.shape[1])
    corr = np.fft.irfft2(np.fft.rfft2(a, shape) * np.conj(np.fft.rfft2(b, shape)), shape)
    ki, kj = np.unravel_index(int(np.argmax(corr)), corr.shape)
    score = float(corr[ki, kj]) / max(1.0, min(a.sum(), b.sum()))
    di, dj = _refine(corr, ki, kj)
    ki = ki - shape[0] if ki > shape[0] // 2 else ki
    kj = kj - shape[1] if kj > shape[1] // 2 else kj
    tx = bx - ax - (kj + dj) * ALIGN_CELL_M
    ty = by - ay - (ki + di) * ALIGN_CELL_M
    return (tx, ty), min(1.0, score)


def _raster(mass) -> tuple[np.ndarray, tuple[float, float]]:
    x0, y0, x1, y1 = mass.bounds
    xs = np.arange(x0, x1 + ALIGN_CELL_M, ALIGN_CELL_M)
    ys = np.arange(y0, y1 + ALIGN_CELL_M, ALIGN_CELL_M)
    gx, gy = np.meshgrid(xs, ys)
    shapely.prepare(mass)
    grid = shapely.contains_xy(mass, gx, gy).astype(float)
    return grid, (x0, y0)


def _refine(corr: np.ndarray, i: int, j: int) -> tuple[float, float]:
    """Sub-cell position of a correlation peak (parabola through its neighbours)."""

    def vertex(m1: float, c: float, p1: float) -> float:
        d = m1 - 2 * c + p1
        return 0.0 if d == 0 else 0.5 * (m1 - p1) / d

    h, w = corr.shape
    return (vertex(corr[i - 1, j], corr[i, j], corr[(i + 1) % h, j]),
            vertex(corr[i, j - 1], corr[i, j], corr[i, (j + 1) % w]))
