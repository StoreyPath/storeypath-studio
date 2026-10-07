"""Turning one floor drawing into spaces, labels and doors.

Everything here works in local meters: drawing coordinates times the unit scale.
IDs are not assigned here (see convert.py).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import ezdxf.bbox
import ezdxf.path
import numpy as np
from ezdxf.document import Drawing
from ezdxf.entities import DXFGraphic
from shapely import STRtree, make_valid
from shapely.affinity import translate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, unary_union

from .cad import drawing_units, header_units, meters_per_unit
from .geometry import as_polygons, iou
from .profile import Profile
from .types import SpaceType
from .split import split_by_labels
from .walls import DoorShape, DoorSwing, open_issue, read_fabric, spaces_from_walls

MAX_BLOCK_DEPTH = 8
CURVE_TOLERANCE_M = 0.02
OUTLINE_CLOSING_M = 0.3
OUTLINE_MIN_HOLE_M2 = 10.0
HATCH_FILL_MAX_M2 = 4.0  # larger hatches on wall layers count as wall only when thin
LABEL_GROUP_M = 1.5  # label pieces closer than this belong to the same room
DOOR_SWING_RADIUS_M = (0.35, 2.0)  # an arc on a door layer this size and…
DOOR_SWING_SWEEP = (60.0, 120.0)  # …about a quarter turn is a door swing
DOOR_MERGE_M = 0.1  # door swings this close are one door (double doors)
# A block on a door layer with no swing drawn in it is a door only when its name
# says so, or when it is shaped like a door leaf in a wall (a sliding door): this
# long and no thicker. Basins, baths and cars put on door layers are not doors.
DOOR_LEAF_M = (0.5, 3.0)
DOOR_LEAF_THICK_M = 0.3
DOOR_NAME = re.compile(r"door|d[oö]r|t[uü]r|porte|puerta|باب|ابواب|أبواب", re.I)
# Door and window tags (D4, W-12, SD2): the key to the drawing's schedule of
# openings, drawn beside each one. A tag says which an opening is.
OPENING_TAG = re.compile(r"(?P<kind>[A-Z]{1,2})\s?-?\s?(?P<n>\d{1,3}[A-Z]?)")
DOOR_TAGS = {"D", "DR", "SD", "GD", "FD", "MD"}
WINDOW_TAGS = {"W", "WD", "WN", "FW", "SW"}
DRAWN_WALL_M = 0.2  # a wall a person draws in review
DRAWN_REACH_M = 0.3  # a line drawn in review reaches this far past its ends
TAG_REACH_M = 1.0  # a tag is drawn this close to its opening
SLIDING_DOOR_M = (0.6, 2.6)  # glazing between two rooms this wide, with no window tag, is a sliding door


@dataclass
class Label:
    lines: list[str]
    point: Point


@dataclass
class ExtractedSpace:
    polygon: Polygon
    layer: str
    name: str | None = None
    number: str | None = None
    label: str | None = None  # the text in it as the drawing writes it, its lines in reading order
    blocks: list[str] = field(default_factory=list)
    type: SpaceType = SpaceType.UNSPECIFIED
    type_source: str = "default"
    issues: list[str] = field(default_factory=list)  # reasons a person should look at it
    ignored: bool = False  # judged not a room (vision): set aside, for a person to restore


@dataclass
class ExtractedZone(ExtractedSpace):
    """A part of a space used for one thing, with no wall between it and the rest
    (a majlis and a dining area in one hall). The zones of a space divide it."""

    space: int = -1  # index into FloorExtraction.spaces


@dataclass
class ExtractedDoor:
    footprint: Polygon
    point: Point
    connects: list[int]  # indexes into FloorExtraction.spaces
    # "door": drawn as a block or swing; "doorway": a gap in a wall with no door
    # drawn; "split": where open-plan rooms meet; "window"; "glazing": a sliding door,
    # drawn like a window but between two rooms
    source: str = "door"
    span: LineString | None = None  # jamb to jamb, meters
    width: float | None = None
    # its leaves as the swings are drawn: (hinge, free edge when open), meters
    swings: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)
    tag: str | None = None  # its tag in the drawing (D4, W12)
    sill: float | None = None  # metres above the floor, and its height: from the schedule (schedule.py)
    height: float | None = None
    issues: list[str] = field(default_factory=list)  # for review


@dataclass
class FloorExtraction:
    spaces: list[ExtractedSpace]
    doors: list[ExtractedDoor]
    outline: Polygon | MultiPolygon | None
    scale: float  # meters per drawing unit
    warnings: list[str] = field(default_factory=list)
    method: str = "outlines"  # how spaces were found: "outlines" or "walls"
    walls: object = None  # the walls as drawn, with their door and window gaps
    wall_thickness: float | None = None
    labels: list[Label] = field(default_factory=list)  # the room names and numbers as placed
    zones: list[ExtractedZone] = field(default_factory=list)  # parts of open spaces, by space index

    def units(self) -> list[ExtractedSpace]:
        """What is named, typed and used: the zones, and the spaces that have none."""
        zoned = {z.space for z in self.zones}
        return [*self.zones, *(s for i, s in enumerate(self.spaces) if i not in zoned)]


MAX_BLOCK_PIECES = 1_000_000  # entities taken out of blocks in one walk through a drawing, at most


def _walk(entities, parent_layer: str | None = None, depth: int = 0,
          expand: Callable[[DXFGraphic, str], bool] | None = None, _within: tuple[str, ...] = (),
          _budget: list[int] | None = None):
    """Yield (entity, effective layer) through nested block inserts. Entities on
    layer "0" inside a block take the layer of the insert, as CAD programs show them.
    ``expand(insert, layer)`` returning False keeps a block's contents out. A block
    placed inside itself is not expanded again, and at most MAX_BLOCK_PIECES are
    taken out of blocks: a broken drawing's blocks would otherwise never end."""
    budget = [MAX_BLOCK_PIECES] if _budget is None else _budget
    for e in entities:
        layer = e.dxf.get("layer", "0")
        if parent_layer is not None and layer == "0":
            layer = parent_layer
        yield e, layer
        if e.dxftype() == "INSERT":
            for attrib in e.attribs:
                a_layer = attrib.dxf.get("layer", "0")
                yield attrib, (layer if a_layer == "0" else a_layer)
            name = e.dxf.get("name", "")
            if depth < MAX_BLOCK_DEPTH and name not in _within and budget[0] > 0 \
                    and (expand is None or expand(e, layer)):
                try:
                    children = list(e.virtual_entities())
                except Exception:  # broken or unsupported block content
                    continue
                budget[0] -= len(children)
                yield from _walk(children, layer, depth + 1, expand, (*_within, name), budget)


def modelspace_entities(doc: Drawing, region: tuple[float, float, float, float] | None = None):
    """Top-level entities of the drawing; with ``region`` (x0, y0, x1, y1 in drawing
    units) only those whose middle lies inside it, for drawings that hold several
    floors (or sheets) side by side. To read a plan, see plan_entities."""
    if region is None:
        yield from doc.modelspace()
        return
    entities, centres, _ = _entity_index(doc)
    x0, y0, x1, y1 = region
    inside = (centres[:, 0] >= x0) & (centres[:, 0] <= x1) & (centres[:, 1] >= y0) & (centres[:, 1] <= y1)
    for i in np.nonzero(inside)[0]:
        yield entities[i]


def plan_entities(doc: Drawing, region: tuple[float, float, float, float] | None = None):
    """The entities of one plan: as modelspace_entities, but a block placed across
    ``region`` (a sheet pasted as one block, a bound xref holding several plans) is
    not given whole to the plan that holds its middle: it is taken apart, and its
    pieces whose middle lies inside are the plan's (themselves taken apart when they
    too lie across it). Pieces on layer "0" take the block's layer, as in _walk."""
    if region is None:
        yield from doc.modelspace()
        return
    entities, centres, boxes = _entity_index(doc)
    x0, y0, x1, y1 = region
    inside = (centres[:, 0] >= x0) & (centres[:, 0] <= x1) & (centres[:, 1] >= y0) & (centres[:, 1] <= y1)
    across = (boxes[:, 0] <= x1) & (boxes[:, 2] >= x0) & (boxes[:, 1] <= y1) & (boxes[:, 3] >= y0) & ~(
        (boxes[:, 0] >= x0) & (boxes[:, 2] <= x1) & (boxes[:, 1] >= y0) & (boxes[:, 3] <= y1))
    budget = [MAX_BLOCK_PIECES]
    for i in np.nonzero(inside | across)[0]:
        e = entities[i]
        if across[i] and e.dxftype() == "INSERT":
            yield from _pieces_in(e, region, 1, (e.dxf.get("name", ""),), budget)
        elif inside[i]:
            yield e


def _pieces_in(insert, region, depth: int, within: tuple[str, ...], budget: list[int]):
    """The pieces of a block placed across ``region`` that lie in it (see plan_entities)."""
    try:
        children = list(insert.virtual_entities())
    except Exception:  # broken or unsupported block content
        return
    budget[0] -= len(children)
    layer = insert.dxf.get("layer", "0")
    x0, y0, x1, y1 = region
    for c in children:
        if c.dxf.get("layer", "0") == "0":
            c.dxf.layer = layer  # a copy: the drawing is not changed
        try:
            ext = ezdxf.bbox.extents([c], fast=True)
        except Exception:
            continue
        if not ext.has_data or not _finite(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y):
            continue
        lo, hi = ext.extmin, ext.extmax
        within_region = lo.x >= x0 and hi.x <= x1 and lo.y >= y0 and hi.y <= y1
        overlaps = lo.x <= x1 and hi.x >= x0 and lo.y <= y1 and hi.y >= y0
        name = c.dxf.get("name", "") if c.dxftype() == "INSERT" else ""
        if name and overlaps and not within_region and depth < MAX_BLOCK_DEPTH and name not in within \
                and budget[0] > 0:
            yield from _pieces_in(c, region, depth + 1, (*within, name), budget)
        elif x0 <= ext.center.x <= x1 and y0 <= ext.center.y <= y1:
            yield c


def _entity_index(doc: Drawing):
    """The middle and extents of every top-level entity, measured once per drawing:
    picking one plan out of a sheet set is then a lookup, not a pass over every
    entity. An entity whose extents cannot be measured, or are not finite, is left out."""
    index = getattr(doc, "_storeypath_index", None)
    if index is None:
        entities, centres, boxes = [], [], []
        cache = ezdxf.bbox.Cache()
        for e in doc.modelspace():
            try:
                ext = ezdxf.bbox.extents([e], fast=True, cache=cache)
            except Exception:
                continue
            if ext.has_data and _finite(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y):
                entities.append(e)
                centres.append((ext.center.x, ext.center.y))
                boxes.append((ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y))
        index = (entities, np.array(centres, dtype=float).reshape(-1, 2),
                 np.array(boxes, dtype=float).reshape(-1, 4))
        doc._storeypath_index = index
    return index


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def drawing_extents(doc: Drawing) -> tuple[float, float, float, float] | None:
    """Where the drawing's entities lie (x0, y0, x1, y1, drawing units), measured one
    entity at a time: a broken entity is left out instead of failing the whole."""
    x0 = y0 = math.inf
    x1 = y1 = -math.inf
    cache = ezdxf.bbox.Cache()
    for e in doc.modelspace():
        try:
            ext = ezdxf.bbox.extents([e], fast=True, cache=cache)
        except Exception:
            continue
        if ext.has_data and _finite(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y):
            x0, y0 = min(x0, ext.extmin.x), min(y0, ext.extmin.y)
            x1, y1 = max(x1, ext.extmax.x), max(y1, ext.extmax.y)
    return (x0, y0, x1, y1) if x0 <= x1 else None


def _flatten(e: DXFGraphic, tolerance: float) -> tuple[list[tuple[float, float]], bool] | None:
    """Points along an entity and whether it is closed. None for an entity that
    cannot be read as a line: a broken one (a spline with too few points, a point
    at infinity or not a number) is left out, not the floor."""
    try:
        pts = _points(ezdxf.path.make_path(e), tolerance)
    except Exception:  # broken or unsupported geometry
        return None
    if pts is None or len(pts) < 2:
        return None
    closed = bool(getattr(e, "closed", False)) or e.dxftype() in ("CIRCLE",)
    if not closed and len(pts) > 3:
        closed = Point(pts[0]).distance(Point(pts[-1])) <= tolerance
    return pts, closed


MAX_CURVE_STEPS = 1e7  # a curve this many times the flattening tolerance across is no drawing's


def _points(path, tolerance: float) -> list[tuple[float, float]] | None:
    """A path's points, its curves flattened. None for a path with a point that is
    not finite, or a curve too large to flatten (flattening those never ends)."""
    vertices = path.control_vertices()
    if not all(_finite(v.x, v.y) for v in vertices):
        return None
    if path.has_curves and vertices:
        xs, ys = [v.x for v in vertices], [v.y for v in vertices]
        if max(max(xs) - min(xs), max(ys) - min(ys)) > MAX_CURVE_STEPS * tolerance:
            return None
    return [(v.x, v.y) for v in path.flattening(tolerance)]


def _text_lines(e: DXFGraphic) -> list[str]:
    t = e.dxftype()
    if t == "MTEXT":
        raw = e.plain_text(split=False)
    elif t in ("TEXT", "ATTRIB"):
        raw = e.plain_text()
    else:
        return []
    return [line.strip() for line in raw.replace("\r", "\n").split("\n") if line.strip()]


def _center(e: DXFGraphic) -> tuple[float, float] | None:
    try:
        ext = ezdxf.bbox.extents([e], fast=True)
        if ext.has_data and _finite(ext.center.x, ext.center.y):
            return ext.center.x, ext.center.y
    except Exception:
        pass
    insert = e.dxf.get("insert")
    return (insert.x, insert.y) if insert is not None and _finite(insert.x, insert.y) else None


def _split_label(lines: list[str], profile: Profile) -> tuple[str | None, str | None]:
    """Name and number from a space's label lines, e.g. ["OFFICE", "204"] or ["OFFICE 204"]."""
    names, number = [], None
    for line in lines:
        if profile.number_re.fullmatch(line):
            number = number or line
            continue
        head, _, tail = line.rpartition(" ")
        if head and number is None and profile.number_re.fullmatch(tail):
            names.append(head.strip())
            number = tail
        else:
            names.append(line)
    return (" ".join(dict.fromkeys(names)) or None), number  # ROOF at both ends: one ROOF


def dashed_lines(doc: Drawing) -> Callable[[DXFGraphic, str], bool]:
    """Whether an entity, on its layer (as ``_walk`` gives it), is drawn dashed:
    hidden and centre lines, the edges of what is overhead (a dome, a void, the
    floor above) or below. They are never walls."""
    types = set()
    for lt in doc.linetypes:
        try:
            pattern = lt.simplified_line_pattern()
        except Exception:
            pattern = ()
        if len(pattern) > 1:
            types.add(lt.dxf.name.upper())
    layers = {layer.dxf.name: layer.dxf.get("linetype", "Continuous").upper() for layer in doc.layers}

    def dashed(e: DXFGraphic, layer: str) -> bool:
        lt = e.dxf.get("linetype", "BYLAYER").upper()
        return (layers.get(layer, "CONTINUOUS") if lt == "BYLAYER" else lt) in types

    return dashed


def extract_floor(
    doc: Drawing,
    profile: Profile,
    units: str | None = None,
    region: tuple[float, float, float, float] | None = None,
    offset: tuple[float, float] | None = None,
    skip_label: Callable[[str], bool] | None = None,
    drawn_walls: list | None = None,
    drawn_dividers: list | None = None,
) -> FloorExtraction:
    """Spaces, doors and outline of one floor, in meters. ``region`` limits the
    drawing to one floor's plan; ``offset`` (drawing units) is subtracted from every
    point so that floors drawn side by side line up. ``skip_label`` leaves out texts
    on label layers that do not name rooms (levels, notes…). ``drawn_walls`` are
    walls a person drew in review ([[x, y], [x, y]], local meters): walls like any;
    ``drawn_dividers`` divide the spaces they cross into zones, with no wall."""
    scale = meters_per_unit(doc, units)
    tol = CURVE_TOLERANCE_M / scale
    warnings: list[str] = []
    if not units:
        used, said = drawing_units(doc), header_units(doc)
        if said and used != said:
            warnings.append(f"the drawing says it is in {said}, but its doors are drawn in {used}; read as {used}")
        elif not said and used != getattr(doc, "_storeypath_units", None):
            warnings.append(f"the drawing has no unit setting; read as {used} (set units on the floor to override)")

    method = profile.spaces.method
    closed_shapes: list[tuple[Polygon, str]] = []
    linework: list[LineString] = []
    wall_lines: list[LineString] = []
    wall_fills: list[Polygon] = []
    labels: list[Label] = []
    blocks: list[tuple[str, Point]] = []
    door_shapes: list[DoorShape] = []
    opening_lines: list[LineString] = []  # door/window linework: glazing, leaves, frames
    tags: list[tuple[str, str, Point]] = []  # (tag, D or W, where): door and window tags
    layer_counts: Counter[str] = Counter()
    loose_door_entities = 0

    def expand(insert, layer) -> bool:  # a door block is read whole, not its lines
        return not profile.door_layers.fullmatch(layer)

    is_dashed = dashed_lines(doc)

    for e, layer in _walk(plan_entities(doc, region), expand=expand):
        kind = e.dxftype()
        layer_counts[layer] += 1
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
            if (tag := _opening_tag(e)) is not None and (c := _center(e)):
                tags.append((*tag, Point(c[0] * scale, c[1] * scale)))
            if profile.label_layers.fullmatch(layer):
                lines, c = _text_lines(e), _center(e)
                if skip_label is not None and lines and all(
                    skip_label(ln) and not profile.number_re.fullmatch(ln) for ln in lines
                ):
                    continue
                if lines and c:
                    labels.append(Label(lines, Point(c[0] * scale, c[1] * scale)))
            continue
        if kind == "INSERT":
            if profile.door_layers.fullmatch(layer):
                if (shape := _door_block(e, scale)) is not None:
                    door_shapes.append(shape)
            elif profile.block_layers.fullmatch(layer):
                c = _center(e)
                if c:
                    blocks.append((e.dxf.name, Point(c[0] * scale, c[1] * scale)))
            continue
        if profile.door_layers.fullmatch(layer):
            if kind == "ARC" and (shape := _door_swing(e, scale)) is not None:
                door_shapes.append(shape)
            elif (flat := _flatten(e, tol)) is not None:
                loose_door_entities += 1
                opening_lines.append(LineString([(x * scale, y * scale) for x, y in flat[0]]))
        # walls are kept in every case; a dashed line on a wall layer is overhead
        on_walls = bool(profile.wall_layers.fullmatch(layer)) and (kind == "HATCH" or not is_dashed(e, layer))
        on_spaces = method != "walls" and bool(profile.space_layers.fullmatch(layer))
        if not (on_walls or on_spaces):
            continue
        if kind == "HATCH":
            # Outlines are read from boundary lines, not fills; walls from both.
            if on_walls:
                _wall_hatch(e, tol, scale, profile.walls.max_thickness, wall_lines, wall_fills)
            continue
        flat = _flatten(e, tol)
        if flat is None:
            continue
        pts, closed = flat
        pts = [(x * scale, y * scale) for x, y in pts]
        if on_walls:
            wall_lines.append(LineString(pts + pts[:1] if closed and pts[0] != pts[-1] else pts))
        if not on_spaces:
            continue
        if closed and len(pts) >= 3:
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = make_valid(poly)
            for p in as_polygons(poly):
                closed_shapes.append((p, layer))
        else:
            linework.append(LineString(pts))

    if linework:
        for p in polygonize(unary_union(linework)):
            closed_shapes.append((p, "linework"))
    dx, dy = (offset[0] * scale, offset[1] * scale) if offset else (0.0, 0.0)
    for (x0, y0), (x1, y1) in drawn_walls or []:  # drawn in local meters: back where the drawing has them
        wall_fills.append(LineString([(x0 + dx, y0 + dy), (x1 + dx, y1 + dy)])
                          .buffer(DRAWN_WALL_M / 2, cap_style="square", join_style="mitre"))

    door_shapes = _merge_doors(door_shapes)
    doorways: list[Polygon] = []
    open_edges = fabric = None
    outside = 0
    spaces, containers = _clean_spaces(closed_shapes, profile, warnings, [lb.point for lb in labels])
    outline = _floor_outline(spaces, containers)
    used = "outlines"
    wall_lines = _without_crosses(wall_lines)
    if not spaces and method != "outlines" and (wall_lines or wall_fills):
        found = spaces_from_walls(
            wall_lines, wall_fills, door_shapes, opening_lines, [lb.point for lb in labels], profile.walls,
            profile.spaces.min_area,
        )
        spaces = [ExtractedSpace(polygon=p, layer="walls") for p in _without_spikes(found.polygons)
                  if p.area >= profile.spaces.min_area]
        doorways, open_edges, fabric = found.doorways, found.open_edges, found.fabric
        outline, used = found.outline, "walls"
        outside = len(found.pockets)
        if found.pockets:
            warnings.append(
                f"{len(found.pockets)} area(s) open to the outside were left out; "
                "if one is a room, check that its doors are drawn on a door layer"
            )

    if used == "outlines" and drawn_walls:  # rooms drawn as outlines: a drawn wall parts them too
        spaces = _parted_by(spaces, [_drawn_line(w, dx, dy) for w in drawn_walls], profile.spaces.min_area)

    if not spaces:
        top = ", ".join(f"{name} ({n})" for name, n in layer_counts.most_common(15))
        looked = {"outlines": f"outlines on layers matching {profile.spaces.layers}",
                  "walls": f"walls on layers matching {profile.walls.layers}"}
        where = " or ".join(v for k, v in looked.items() if method in (k, "auto"))
        warnings.append(f"no spaces found from {where}; busiest layers in this drawing: {top}")

    zones: list[ExtractedZone] = []
    gaps: list[LineString] = []
    if used == "walls":
        spaces, zones, gaps = _divide_open_areas(spaces, labels, profile, fabric.walls if fabric else None)
        for s in spaces:
            s.issues += open_issue(s.polygon, open_edges)
    if drawn_dividers:
        zones = _divided_by_drawn(spaces, zones, [_drawn_line(d, dx, dy) for d in drawn_dividers],
                                  profile.spaces.min_area)
    zoned = {z.space for z in zones}
    units = [*zones, *(s for i, s in enumerate(spaces) if i not in zoned)]
    _assign_labels(units, labels, profile, warnings)
    _assign_blocks(units, blocks)
    for s in units:
        s.type, s.type_source = profile.classify(s.name, s.blocks, s.layer)
    type_zoned_spaces(spaces, zones)

    if fabric is None and (wall_lines or wall_fills):
        fabric = read_fabric(wall_lines, wall_fills, door_shapes, opening_lines, profile.walls)
    doors = _connect_doors(spaces, [d.box for d in door_shapes], profile.doors.reach, warnings,
                           [(d.span, d.width, _leaves(d)) for d in door_shapes])
    # Openings closed with no door drawn count as doors between two spaces; on the
    # outside they are most likely windows.
    doors += [
        replace(d, source="doorway")
        for d in _connect_doors(spaces, doorways, profile.doors.reach, [])
        if len(d.connects) == 2
    ]
    if fabric is not None:
        doors += [
            replace(d, source="window")
            for d in _connect_doors(spaces, [w.buffer(0.15, cap_style="flat") for w in fabric.windows],
                                    profile.doors.reach, [], [(w, round(w.length, 3)) for w in fabric.windows])
        ]
    doors += [  # across a gap in a wall with no door drawn, where two labelled rooms meet
        replace(d, source="doorway")
        for d in _connect_doors(spaces, [g.buffer(0.05) for g in gaps], profile.doors.reach, [])
        if len(d.connects) == 2
    ]
    _read_tags(doors, tags)
    if loose_door_entities and not door_shapes:
        warnings.append(
            f"{loose_door_entities} lines/arcs on door layers are not door blocks or swing arcs; no doors found"
        )
    if offset:
        dx, dy = -offset[0] * scale, -offset[1] * scale
        for s in [*spaces, *zones]:
            s.polygon = translate(s.polygon, dx, dy)
        for d in doors:
            d.footprint, d.point = translate(d.footprint, dx, dy), translate(d.point, dx, dy)
            d.span = translate(d.span, dx, dy) if d.span is not None else None
            d.swings = [((h[0] + dx, h[1] + dy), (q[0] + dx, q[1] + dy)) for h, q in d.swings]
        outline = translate(outline, dx, dy) if outline is not None else None
        labels = [Label(lb.lines, translate(lb.point, dx, dy)) for lb in labels]
        if fabric is not None:
            fabric.walls = translate(fabric.walls, dx, dy)
    walls = fabric.walls if fabric is not None and not fabric.walls.is_empty else None
    if spaces:
        rooms = unary_union([s.polygon for s in spaces])
        if walls is not None:
            walls = _attached(walls, rooms)
        if outline is not None:  # drawn from the same walls: without the markers' pieces too
            outline = unary_union([p for p in as_polygons(outline) if p.distance(rooms) <= 0.1]) or outline
        if outline is not None and outside:  # areas left out as the outside: the walls round them too
            outline, walls = _to_the_building(outline, walls, rooms)
    return FloorExtraction(spaces, doors, outline, scale, warnings, used, walls, _thickness(walls), labels, zones)


def _opening_tag(e) -> tuple[str, str] | None:
    """(tag, "D" or "W") for a door or window tag: one short text, a known prefix
    and a number."""
    lines = _text_lines(e)
    if len(lines) != 1 or not (m := OPENING_TAG.fullmatch(lines[0].strip().upper())):
        return None
    prefix = m["kind"]
    kind = "D" if prefix in DOOR_TAGS else "W" if prefix in WINDOW_TAGS else None
    return (f"{prefix}{m['n']}", kind) if kind else None


def _read_tags(doors: list[ExtractedDoor], tags) -> None:
    """Each tag names the opening it is drawn by (the nearest within reach). A door
    tag makes a door of an opening drawn as glazing or left open; glazing between two
    rooms with no window tag is a sliding door too, for review. Doors drawn as doors
    stay doors whatever the tag."""
    taken: dict[int, float] = {}
    for tag, kind, point in tags:
        near = [(d.footprint.distance(point), i) for i, d in enumerate(doors)]
        if not near:
            break
        dist, i = min(near)
        if dist <= TAG_REACH_M and dist < taken.get(i, float("inf")):
            taken[i] = dist
            doors[i].tag = f"{tag}:{kind}"
    for d in doors:
        tag, _, kind = (d.tag or "").partition(":")
        d.tag = tag or None
        if d.source in ("window", "doorway", "split") and kind == "D":
            d.source = "door"
        elif d.source == "window" and len(d.connects) == 2 and kind != "W" and d.width is not None \
                and SLIDING_DOOR_M[0] <= d.width <= SLIDING_DOOR_M[1]:
            d.source = "glazing"
            d.issues.append("drawn as glazing between two rooms, taken as a sliding door; check it")


LIFT_DOOR_M = 0.9  # an assumed lift door's width
# A lift opens onto circulation, never into a bathroom or another shaft.
NOT_FOR_LIFT_DOORS = {SpaceType.ELEVATOR, SpaceType.STAIRS, SpaceType.ESCALATOR, SpaceType.SHAFT,
                      SpaceType.RESTROOM, SpaceType.BATHROOM, SpaceType.STORAGE, SpaceType.UTILITY,
                      SpaceType.OPEN_TO_BELOW}
CIRCULATION = {SpaceType.CORRIDOR, SpaceType.LOBBY, SpaceType.OPEN_AREA, SpaceType.UNSPECIFIED}


def add_lift_doors(ex: FloorExtraction, reach: float = 0.4) -> None:
    """A lift with no way in drawn (its doors are often left out of plans) gets one,
    for review, on the wall it shares with a hall, lobby or corridor, else with any
    room it may open onto: the longest such wall."""
    for i, s in enumerate(ex.spaces):
        if s.type != SpaceType.ELEVATOR or any(i in d.connects for d in ex.doors if d.source != "window"):
            continue
        best = None
        for j, other in enumerate(ex.spaces):
            if j == i or other.type in NOT_FOR_LIFT_DOORS:
                continue
            shared = s.polygon.exterior.intersection(other.polygon.buffer(reach))
            pieces = [p for p in getattr(shared, "geoms", [shared]) if p.length > 0]
            if not pieces:
                continue
            piece = max(pieces, key=lambda p: p.length)
            rank = (other.type in CIRCULATION, piece.length)
            if piece.length >= LIFT_DOOR_M / 2 and (best is None or rank > best[0]):
                best = (rank, j, piece)
        if best is None:
            continue
        _, j, piece = best
        mid = piece.interpolate(0.5, normalized=True)
        half = min(LIFT_DOOR_M, piece.length) / 2
        d = piece.project(mid)
        span = LineString([piece.interpolate(max(0.0, d - half)), piece.interpolate(min(piece.length, d + half))])
        other = ex.spaces[j]
        ex.doors.append(ExtractedDoor(
            footprint=span.buffer(0.15, cap_style="flat"), point=mid, connects=[i, j], source="assumed",
            span=span, width=round(span.length, 3),
            issues=[f"no door into the lift is drawn; one is assumed on the side facing "
                    f"{other.name or other.type.value.replace('_', ' ')}; check it"],
        ))


X_MIN_M = 0.8  # lines this long and longer may mark an X
X_MIDDLE = 0.15  # an X's lines cross within this share of their length of their middles
X_DIAGONAL_DEG = (15.0, 75.0)  # …and run at an angle to the plan's walls


def _without_crosses(lines: list[LineString]) -> list[LineString]:
    """Wall lines without the X marks drawn across voids, openings to below and lift
    cars: two straight stretches that cross near both their middles, diagonally to
    the plan's main directions, whether drawn as two lines or inside one polyline with
    the rectangle around them. Walls meet at their ends or run into each other; they
    do not cross diagonally through each other's middles."""
    segments = []  # (line index, segment index, segment)
    for k, ln in enumerate(lines):
        c = list(ln.coords)
        for m, (a, b) in enumerate(zip(c, c[1:])):
            seg = LineString([a, b])
            if seg.length >= X_MIN_M:
                segments.append((k, m, seg))
    if len(segments) < 2:
        return lines

    def angle(seg):
        (x0, y0), (x1, y1) = seg.coords
        return np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 90.0

    angles = np.array([angle(seg) for _, _, seg in segments])
    hist, _ = np.histogram(angles, bins=90, range=(0.0, 90.0), weights=[seg.length for _, _, seg in segments])
    axis = float(np.argmax(hist)) + 0.5  # the plan's main wall direction, modulo 90°
    diagonal = [s for s, a in zip(segments, angles) if X_DIAGONAL_DEG[0] <= (a - axis) % 90.0 <= X_DIAGONAL_DEG[1]]
    if len(diagonal) < 2:
        return lines
    tree = STRtree([seg for _, _, seg in diagonal])
    marks: set[tuple[int, int]] = set()
    for i, (k, m, seg) in enumerate(diagonal):
        for j in tree.query(seg, predicate="crosses"):
            j = int(j)
            if j <= i:
                continue
            other = diagonal[j][2]
            if not 0.5 <= seg.length / other.length <= 2.0:
                continue
            hit = seg.intersection(other)
            if hit.geom_type == "Point" and all(abs(g.project(hit, normalized=True) - 0.5) <= X_MIDDLE for g in (seg, other)):
                marks.update({(k, m), diagonal[j][:2]})
    if not marks:
        return lines
    out = []
    for k, ln in enumerate(lines):
        cut = sorted(m for kk, m in marks if kk == k)
        if not cut:
            out.append(ln)
            continue
        c = list(ln.coords)
        run = [c[0]]
        for m in range(len(c) - 1):  # keep the line, less its X stretches
            if m in cut:
                if len(run) > 1:
                    out.append(LineString(run))
                run = [c[m + 1]]
            else:
                run.append(c[m + 1])
        if len(run) > 1:
            out.append(LineString(run))
    return out


def _attached(walls, rooms, touch: float = 0.1):
    """The walls of the building: the ones its rooms meet (a room ends at a wall's
    face; a column stands inside), and whatever is built onto those. A filled section
    or elevation marker, a north arrow or a symbol beside the plan, drawn on a wall
    layer, stands apart and is left out."""
    pieces = as_polygons(walls)
    kept = [p.distance(rooms) <= touch for p in pieces]
    grew = True
    while grew:  # what touches a kept piece is kept
        grew = False
        for i, p in enumerate(pieces):
            if not kept[i] and any(k and p.distance(q) <= touch for k, q in zip(kept, pieces)):
                kept[i] = grew = True
    return unary_union([p for p, k in zip(pieces, kept) if k]) or None


OUTSIDE_REACH_M = 0.6  # an area set aside this close to the outline's edge is outside…
STRIP_M = 0.6  # …and strips of outline narrower than this left by it (a land line, a plot wall) go too


def keep_to_the_building(ex: FloorExtraction) -> None:
    """The floor's outline and walls without the outside around the building: the
    areas set aside as not rooms that reach the edge of the plan (a yard inside the
    line that marks the land, the pool in that yard…), the line itself, and any
    plot wall running on from the house. Rooms are never cut; areas set aside
    inside the building (a shaft, a gap) stay part of it."""
    if ex.outline is None or ex.outline.is_empty:
        return
    rooms = [s.polygon for s in ex.spaces if not s.ignored]
    aside = [s.polygon for s in ex.spaces if s.ignored]
    if not rooms or not aside:
        return
    outline, removed = ex.outline, []
    while aside:  # from the edge inwards: the yard first, then what stands in it
        edge = unary_union([Polygon(p.exterior) for p in as_polygons(outline)]).boundary
        out = [p for p in aside if p.distance(edge) <= OUTSIDE_REACH_M]
        if not out:
            break
        removed += out
        aside = [p for p in aside if all(p is not q for q in out)]
        outline = outline.difference(unary_union(out).buffer(0.01, join_style="mitre"))
    if not removed:
        return
    ex.outline, ex.walls = _to_the_building(outline, ex.walls, unary_union(rooms))
    ex.wall_thickness = _thickness(ex.walls)


def _to_the_building(outline, walls, rooms):
    """An outline the outside was taken from, without the strips it leaves (the line
    marking the land, a plot wall running on from the house) or the parts that hold
    no room (a pool in the yard); the walls kept to it."""
    half = STRIP_M / 2
    opened = outline.buffer(-half, join_style="mitre").buffer(half, join_style="mitre")
    parts = [p for p in as_polygons(opened.union(rooms)) if p.intersection(rooms).area > 0.01]
    outline = unary_union(parts) if parts else None
    if walls is not None and outline is not None:
        walls = walls.intersection(outline.buffer(half, join_style="mitre"))
        walls = _attached(unary_union([p for p in as_polygons(walls) if p.area > 0.001]), rooms)
    return outline, walls


def _thickness(walls) -> float | None:
    """A wall network's typical thickness: area over half its outline, which for long
    thin shapes is their width."""
    if walls is None or walls.length == 0:
        return None
    return round(2 * walls.area / walls.length, 3)


def _door_swing(e, scale: float) -> DoorShape | None:
    """A door drawn as loose lines, from its swing: an arc of about a quarter turn
    centred on the hinge, as wide as the door."""
    radius = e.dxf.radius * scale
    sweep = (e.dxf.end_angle - e.dxf.start_angle) % 360
    if not (DOOR_SWING_RADIUS_M[0] <= radius <= DOOR_SWING_RADIUS_M[1]
            and DOOR_SWING_SWEEP[0] <= sweep <= DOOR_SWING_SWEEP[1]):
        return None
    flat = _flatten(e, CURVE_TOLERANCE_M / scale)
    if flat is None:
        return None
    pts = [(x * scale, y * scale) for x, y in flat[0]]
    c = e.ocs().to_wcs(e.dxf.center)
    hinge = Point(c.x * scale, c.y * scale)
    xs, ys = [p[0] for p in pts] + [hinge.x], [p[1] for p in pts] + [hinge.y]
    return DoorShape(box(min(xs), min(ys), max(xs), max(ys)), [DoorSwing(hinge, (Point(pts[0]), Point(pts[-1])))])


def _door_block(e, scale: float) -> DoorShape | None:
    """A door block: its extent, and the swing arcs drawn in it. None for a block
    that is no door (a basin or a car on a door layer), or one that cannot be read."""
    try:
        ext = ezdxf.bbox.extents([e], fast=True)
    except Exception:  # a block the drawing does not define, broken content
        return None
    if not ext.has_data or not _finite(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y):
        return None
    swings = []
    try:
        for child in e.virtual_entities():
            if child.dxftype() == "ARC" and (shape := _door_swing(child, scale)) is not None:
                swings += shape.swings
    except Exception:  # broken or unsupported block content
        pass
    footprint = box(ext.extmin.x * scale, ext.extmin.y * scale, ext.extmax.x * scale, ext.extmax.y * scale)
    if not swings and not DOOR_NAME.search(e.dxf.name):
        x0, y0, x1, y1 = footprint.bounds
        long, thick = max(x1 - x0, y1 - y0), min(x1 - x0, y1 - y0)
        if not (DOOR_LEAF_M[0] <= long <= DOOR_LEAF_M[1] and thick <= DOOR_LEAF_THICK_M):
            return None
    return DoorShape(footprint, swings)


def _merge_doors(doors: list[DoorShape]) -> list[DoorShape]:
    """One door per opening: the two leaves of a double door become one."""
    if len(doors) < 2:
        return doors
    merged = as_polygons(unary_union([d.box.buffer(DOOR_MERGE_M / 2, join_style="mitre") for d in doors]))
    out = [DoorShape(box(*p.buffer(-DOOR_MERGE_M / 2, join_style="mitre").bounds)) for p in merged]
    tree = STRtree(merged)
    for d in doors:
        out[min(int(k) for k in tree.query(d.box, predicate="intersects"))].swings += d.swings
    return out


ZONE_NOTE = "a zone of an open space, divided where its labels are (no wall); check the line"
GAP_NOTE = "divided from a neighbouring room across a gap in the wall with no door drawn; check it"
GAP_REACH_M = (0.3, 0.8)  # a wall going on this far beyond each end of a dividing line, in line with it


def _divide_open_areas(spaces, labels, profile, walls) -> tuple[list, list[ExtractedZone], list[LineString]]:
    """Spaces holding the labels of several rooms, divided between them where they
    are narrowest (see split.py). Across a gap in a wall (a doorway with no door
    drawn) they become separate spaces, joined there; across open floor, with no
    wall at all, the space stays whole and is divided into zones. Returns the spaces,
    the zones (by index into those spaces) and the gaps divided across."""
    if not spaces or not labels:
        return spaces, [], []
    tree = STRtree([s.polygon for s in spaces])
    per_space: dict[int, list[Label]] = {}
    for label in labels:
        i = _containing_space(tree, spaces, label.point)
        if i is not None:
            per_space.setdefault(i, []).append(label)
    out, zones, gaps = [], [], []
    for i, s in enumerate(spaces):
        groups = _label_groups(per_space.get(i, []))
        parts, lines = (split_by_labels(s.polygon, [[lb.point for lb in g] for g in groups],
                                        profile.spaces.max_split, profile.spaces.min_area)
                        if len(groups) >= 2 else ([s.polygon], []))
        if len(parts) == 1:
            out.append(s)
        elif lines and all(_across_a_wall_gap(line, walls) for line in lines):
            out += [replace(s, polygon=p, issues=[*s.issues, GAP_NOTE]) for p in parts]
            gaps += lines
        else:
            out.append(s)
            zones += [ExtractedZone(polygon=p, layer=s.layer, issues=[ZONE_NOTE], space=len(out) - 1) for p in parts]
    return out, zones, gaps


def _across_a_wall_gap(line: LineString, walls) -> bool:
    """Whether a dividing line closes a gap in a straight wall: the wall goes on
    beyond both of its ends, in line with it. A line across open floor runs from
    wall to wall instead, and beyond its ends lie other rooms or the outside."""
    if walls is None or walls.is_empty or line.length == 0:
        return False
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    ux, uy = (x1 - x0) / line.length, (y1 - y0) / line.length
    near = walls.buffer(0.05)
    return all(near.contains(Point(x + sign * ux * d, y + sign * uy * d))
               for (x, y), sign in (((x0, y0), -1), ((x1, y1), 1)) for d in GAP_REACH_M)


SPIKE_M = 0.4  # no part of a room is narrower than this: a wedge between wall lines, a window's gap
SPIKE_KEEP_M2 = 1.0  # what trimming leaves of a room counts as a body of it when this big


def _without_spikes(polygons: list) -> list:
    """Rooms without the slivers that run off them: a wedge between two wall lines,
    the gap of a window left open, reached through a narrow neck. What is narrower
    than SPIKE_M is trimmed off; the room itself never grows."""
    half = SPIKE_M / 2
    out = []
    for poly in polygons:
        opened = poly.buffer(-half, join_style="mitre").buffer(half, join_style="mitre").intersection(poly)
        parts = [p for p in as_polygons(opened) if p.area >= SPIKE_KEEP_M2]
        # one body left: that is the room; two (halves joined by a narrow neck) or
        # none: leave it as found
        out.append(parts[0] if len(parts) == 1 else poly)
    return out


TREAD_GAP_M = (0.2, 0.4)  # a stair's treads are drawn this far apart…
TREAD_LEN_M = (0.7, 3.0)  # …this long…
TREADS_MIN = 6  # …at least this many side by side
STAIR_SHARE = 0.25  # a room this much covered by flights is a stair (a stair in a hall is not)


def stair_flights(segments: list[LineString]) -> list[Polygon]:
    """Flights of stairs as drawn: runs of at least TREADS_MIN like lines side by
    side, evenly spaced (the treads). A grid (tiles), with a like run across it, is
    not a flight. Returns the area of each flight."""
    import math

    def angle(seg):
        (x0, y0), (x1, y1) = seg.coords[0], seg.coords[-1]
        return math.atan2(y1 - y0, x1 - x0) % math.pi

    segs = [g for g in segments if TREAD_LEN_M[0] <= g.length <= TREAD_LEN_M[1]]
    groups: dict[int, list] = {}
    for g in segs:
        groups.setdefault(round(math.degrees(angle(g)) / 3) % 60, []).append(g)
    runs = []
    for key, gs in groups.items():
        a = math.radians(key * 3)
        nx, ny = -math.sin(a), math.cos(a)  # across the lines
        tx, ty = math.cos(a), math.sin(a)  # along them
        rows = [(g.centroid.x * nx + g.centroid.y * ny, g.centroid.x * tx + g.centroid.y * ty, g) for g in gs]
        # Lines that line up end to end make a flight's treads; two flights side by
        # side (a stair that turns) are two tracks.
        rows.sort(key=lambda r: r[1])
        tracks, track = [], []
        for r in rows:
            if track and r[1] - track[0][1] > 0.3 * track[0][2].length:
                tracks.append(track)
                track = []
            track.append(r)
        if track:
            tracks.append(track)
        for track in tracks:
            track.sort(key=lambda r: r[0])
            run = [track[0]]
            for r in track[1:] + [None]:
                last = run[-1]
                if r is not None:
                    gap = r[0] - last[0]
                    if gap < 0.05:  # a tread drawn as two lines, or one line drawn twice
                        continue
                    if TREAD_GAP_M[0] <= gap <= TREAD_GAP_M[1] \
                            and abs(r[2].length - last[2].length) <= 0.25 * last[2].length:
                        run.append(r)
                        continue
                if len(run) >= TREADS_MIN:
                    runs.append((key, unary_union([x[2] for x in run]).convex_hull))
                run = [r] if r is not None else []
    flights = []
    for key, area in runs:  # a like run across it makes it a grid
        if not any(abs(((k - key) % 60) - 30) <= 2 and other.intersection(area).area > 0.5 * min(other.area, area.area)
                   for k, other in runs):
            flights.append(area)
    return flights


def type_stairs(units, flights: list[Polygon]) -> None:
    """Spaces and zones mostly taken up by flights of stairs are stairs, whatever a
    model guessed; a name read from the drawing still decides."""
    if not flights:
        return
    tree = STRtree(flights)
    for u in units:
        if u.name and u.type_source not in ("default", "vision"):
            continue
        covered = sum(flights[int(i)].intersection(u.polygon).area for i in tree.query(u.polygon))
        if covered >= STAIR_SHARE * u.polygon.area and u.type != SpaceType.STAIRS:
            u.type, u.type_source = SpaceType.STAIRS, "treads"
            u.issues = [i for i in u.issues if not i.startswith("vision: typed")]
            u.issues.append("typed stairs from the treads drawn in it; check it")


def type_zoned_spaces(spaces, zones) -> None:
    """A space divided into zones is named by them; it takes its largest zone's type."""
    largest: dict[int, ExtractedZone] = {}
    for z in zones:
        if z.space not in largest or z.polygon.area > largest[z.space].polygon.area:
            largest[z.space] = z
    for i, z in largest.items():
        s = spaces[i]
        s.name = s.number = s.label = None  # its texts are its zones'
        s.type, s.type_source = z.type, "zones"
        s.issues = [i for i in s.issues if not i.startswith("has the labels of")]


def _wall_hatch(e, tol, scale, max_thickness, lines, fills) -> None:
    """A hatch on a wall layer: a filled wall area if small or thin, otherwise just
    its boundary (a hatch covering a whole floor must not turn it into wall). A
    boundary that cannot be read is left out."""
    try:
        paths = list(ezdxf.path.from_hatch(e))
    except Exception:  # a broken boundary
        return
    for path in paths:
        try:
            pts = _points(path, tol)
        except Exception:
            continue
        if pts is None or len(pts) < 3:
            continue
        pts = [(x * scale, y * scale) for x, y in pts]
        for poly in as_polygons(make_valid(Polygon(pts))):
            if poly.area <= HATCH_FILL_MAX_M2 or poly.buffer(-max_thickness / 2).is_empty:
                fills.append(poly)
            else:
                lines.append(poly.exterior)


CONTAINER_SHARE = 0.5  # an outline is the floor's, not a room, when the outlines in it cover this much of it
TWIN_IOU = 0.8  # an outline round another this alike is the same room drawn twice (gross and net)
TWIN_SHARE = 0.5  # …as is one round another at least this big with only a wall's thickness between


def _clean_spaces(
    shapes: list[tuple[Polygon, str]], profile: Profile, warnings: list[str], label_points=(),
) -> tuple[list[ExtractedSpace], list[Polygon]]:
    """Drop tiny and duplicate outlines. A room drawn twice, to the walls' middle and
    to their faces (gross and net), is one room: the inner outline is kept (the outer
    when only it holds the room's label), never the ring between. An outline holding
    several others is the floor's outline (returned separately, as a candidate), not a
    room, when they cover most of it or it holds no label of its own; otherwise it is
    a room with the others cut out of it (an open office round two shafts)."""
    shapes = [(p, layer) for p, layer in shapes if p.area >= profile.spaces.min_area]
    shapes.sort(key=lambda s: -s[0].area)
    polys = [p for p, _ in shapes]
    middles = STRtree([p.representative_point() for p in polys])
    outlines = STRtree(polys)
    labels = STRtree(list(label_points))
    thin = profile.walls.max_thickness / 2

    def labelled(area) -> bool:
        return len(labels.query(area, predicate="contains")) > 0

    kept: list[ExtractedSpace] = []
    final: dict[int, Polygon] = {}  # the outlines kept as spaces, by index
    dropped: set[int] = set()
    containers: list[Polygon] = []
    cut = 0
    for i, (poly, layer) in enumerate(shapes):
        if i in dropped:
            continue
        inner = sorted(j for j in (int(k) for k in middles.query(poly, predicate="contains"))
                       if j > i and j not in dropped)
        twin = next((j for j in inner if _same_room(poly, polys[j], thin)), None)
        if twin is not None:
            if labelled(polys[twin]) or not labelled(poly):
                continue  # the gross outline of a room drawn net too: the net one is the room
            dropped.add(twin)  # the label is in the ring: this outline is the room
            inner.remove(twin)
        if len(inner) >= 2:
            others = unary_union([polys[j] for j in inner])
            covered = others.intersection(poly).area / poly.area
            if covered >= CONTAINER_SHARE or not labelled(poly.difference(others)):
                containers.append(poly)
                continue
        near = (int(k) for k in outlines.query(poly, predicate="intersects"))
        if any(j < i and j in final and iou(final[j], poly) > 0.95 for j in near):
            continue
        if inner:
            poly = poly.difference(unary_union([polys[j] for j in inner]))
            cut += 1
        final[i] = poly
        kept.append(ExtractedSpace(polygon=poly, layer=layer))

    if cut:
        warnings.append(f"{cut} outline(s) contain other outlines; the inner areas were cut out of them")
    if containers:
        warnings.append(
            f"{len(containers)} outline(s) enclosing several spaces were used as the floor outline, not as spaces"
        )
    _warn_overlaps(kept, warnings)
    return kept, containers


def _same_room(outer: Polygon, inner: Polygon, thin: float) -> bool:
    """Whether an outline round another is the same room drawn again: nearly the
    same, or only a wall's thickness bigger all round (gross round net)."""
    if iou(outer, inner) > TWIN_IOU:
        return True
    return inner.area >= TWIN_SHARE * outer.area and outer.difference(inner).buffer(-thin).is_empty


def _floor_outline(spaces: list[ExtractedSpace], containers: list[Polygon]):
    """The floor's outline: the outlines drawn round its rooms, with any room drawn
    outside them; else the rooms, closed over the walls between them."""
    if not spaces:
        return unary_union(containers) if containers else None
    c = OUTLINE_CLOSING_M
    merged = unary_union([s.polygon.buffer(c, join_style="mitre") for s in spaces]).buffer(
        -c, join_style="mitre"
    )
    if containers:
        merged = unary_union([*containers, merged])
    parts = []
    for p in as_polygons(merged):
        holes = [h for h in p.interiors if Polygon(h).area >= OUTLINE_MIN_HOLE_M2]
        parts.append(Polygon(p.exterior, holes))
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _warn_overlaps(spaces: list[ExtractedSpace], warnings: list[str]) -> None:
    polys = [s.polygon for s in spaces]
    tree = STRtree(polys)
    count = 0
    for i, p in enumerate(polys):
        for j in tree.query(p, predicate="intersects"):
            if j <= i:
                continue
            area = p.intersection(polys[j]).area
            if area > 0.05 * min(p.area, polys[j].area):
                count += 1
    if count:
        warnings.append(f"{count} pair(s) of spaces overlap; check the outlines in review")


def _containing_space(tree: STRtree, spaces: list[ExtractedSpace], pt: Point) -> int | None:
    hits = [int(i) for i in tree.query(pt, predicate="within")]
    if not hits:
        return None
    return min(hits, key=lambda i: spaces[i].polygon.area)


def _assign_labels(spaces, labels, profile, warnings) -> None:
    if not spaces:
        return
    tree = STRtree([s.polygon for s in spaces])
    per_space: dict[int, list[Label]] = {}
    orphans = []
    for label in labels:
        i = _containing_space(tree, spaces, label.point)
        if i is None:
            orphans.append(" / ".join(label.lines))
        else:
            per_space.setdefault(i, []).append(label)
    merged = 0
    for i, ls in per_space.items():
        ls.sort(key=lambda lb: (-round(lb.point.y, 1), lb.point.x))  # reading order
        lines = [ln for lb in ls for ln in lb.lines]
        spaces[i].name, spaces[i].number = _split_label(lines, profile)
        spaces[i].label = "\n".join(lines) or None
        groups = _label_groups(ls)
        if len(groups) > 1:
            merged += 1
            names = " / ".join(repr(" ".join(ln for lb in g for ln in lb.lines)) for g in groups)
            spaces[i].issues.append(f"has the labels of {len(groups)} rooms: {names}")
    if merged:
        warnings.append(
            f"{merged} space(s) have the labels of several rooms; rooms may have merged "
            "through an opening without a door block or a missing wall"
        )
    if orphans:
        sample = ", ".join(repr(o) for o in orphans[:5])
        warnings.append(f"{len(orphans)} label(s) are not inside any space: {sample}")


def _label_groups(labels: list[Label]) -> list[list[Label]]:
    """Labels split into groups of pieces that sit together (name above number…).
    Groups that say the same (ROOF at both ends of a roof) are one room's."""
    groups: list[list[Label]] = []
    for label in labels:
        near = [g for g in groups if any(label.point.distance(o.point) <= LABEL_GROUP_M for o in g)]
        merged = [label] + [lb for g in near for lb in g]
        groups = [g for g in groups if g not in near] + [merged]
    by_text: dict[str, list[Label]] = {}
    for g in groups:
        text = " ".join(" ".join(line for lb in sorted(g, key=lambda lb: (-lb.point.y, lb.point.x))
                                 for line in lb.lines).upper().split())
        by_text.setdefault(text, []).extend(g)
    groups = list(by_text.values())
    for g in groups:
        g.sort(key=lambda lb: (-round(lb.point.y, 1), lb.point.x))
    return sorted(groups, key=lambda g: (-round(g[0].point.y, 1), g[0].point.x))


def _assign_blocks(spaces, blocks) -> None:
    if not spaces:
        return
    tree = STRtree([s.polygon for s in spaces])
    for name, pt in blocks:
        i = _containing_space(tree, spaces, pt)
        if i is not None:
            spaces[i].blocks.append(name)


def _leaves(door: DoorShape) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """A door's leaves, as its swings are drawn: each (hinge, free edge when open).
    Of a swing's two ends, the open one stands off the span; the other lies on it."""
    if door.span is None:
        return []
    out = []
    for sw in door.swings:
        a, b = sw.ends
        open_end = a if door.span.distance(a) >= door.span.distance(b) else b
        out.append(((round(sw.hinge.x, 4), round(sw.hinge.y, 4)), (round(open_end.x, 4), round(open_end.y, 4))))
    return out


def _drawn_line(points, dx: float, dy: float) -> LineString:
    """A line drawn in review (local meters), where the drawing has it, a little past
    each end: a click just short of a wall still reaches it."""
    (x0, y0), (x1, y1) = points
    length = max(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 1e-9)
    ux, uy = (x1 - x0) / length * DRAWN_REACH_M, (y1 - y0) / length * DRAWN_REACH_M
    return LineString([(x0 + dx - ux, y0 + dy - uy), (x1 + dx + ux, y1 + dy + uy)])


def _halves(polygon, line: LineString, min_area: float) -> list | None:
    """The parts a line cuts a polygon into, when it cuts right across."""
    from shapely.ops import split

    if not line.crosses(polygon):
        return None
    parts = [p for p in as_polygons(split(polygon, line)) if p.area >= min_area]
    return parts if len(parts) > 1 else None


def _parted_by(spaces: list[ExtractedSpace], lines: list[LineString], min_area: float) -> list[ExtractedSpace]:
    """Spaces parted by the walls a person drew across them."""
    out = []
    for s in spaces:
        parts = [s.polygon]
        for line in lines:
            parts = [q for p in parts for q in (_halves(p, line, min_area) or [p])]
        out += [replace(s, polygon=p) for p in parts] if len(parts) > 1 else [s]
    return out


def _divided_by_drawn(spaces: list[ExtractedSpace], zones: list, lines: list[LineString],
                      min_area: float) -> list:
    """Zones from the lines a person drew across spaces in review: a space used for
    two things with no wall between. A space already divided has the zone the line
    crosses divided again."""
    zones = list(zones)
    for line in lines:
        zoned = {z.space for z in zones}
        out = []
        for z in zones:
            parts = _halves(z.polygon, line, min_area)
            out += [replace(z, polygon=p, issues=[]) for p in parts] if parts else [z]
        for i, s in enumerate(spaces):
            if i not in zoned and (parts := _halves(s.polygon, line, min_area)):
                out += [ExtractedZone(polygon=p, layer=s.layer, space=i) for p in parts]
        zones = out
    return zones


def _connect_doors(spaces, door_boxes, reach, warnings, spans=None) -> list[ExtractedDoor]:
    """Doors (by their extents) and the one or two spaces each joins. ``spans`` gives
    each door's (span, width) when known, and its leaves after them."""
    if not spaces:
        return []
    polys = [s.polygon for s in spaces]
    tree = STRtree(polys)
    doors = []
    unconnected = 0
    for k, fp in enumerate(door_boxes):
        zone = fp.buffer(reach)
        near = []
        for i in tree.query(zone, predicate="intersects"):
            i = int(i)
            near.append((round(fp.distance(polys[i]), 3), -zone.intersection(polys[i]).area, i))
        near.sort()
        connects = [i for _, _, i in near[:2]]
        if not connects:
            unconnected += 1
            continue
        # The door sits in the wall between the spaces it joins: the part of its
        # zone that lies within reach of all of them but inside none of them.
        wall = zone
        for i in connects:
            wall = wall.intersection(polys[i].buffer(reach)).difference(polys[i])
        point = wall.centroid if not wall.is_empty else fp.centroid
        span, width, *leaves = spans[k] if spans else (None, None)
        doors.append(ExtractedDoor(footprint=fp, point=point, connects=connects, span=span, width=width,
                                   swings=leaves[0] if leaves else []))
    if unconnected:
        warnings.append(f"{unconnected} door(s) are not next to any space and were skipped")
    return doors
