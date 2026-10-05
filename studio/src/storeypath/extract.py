"""Turning one floor drawing into spaces, labels and doors.

Everything here works in local meters: drawing coordinates times the unit scale.
IDs are not assigned here (see convert.py).
"""

from __future__ import annotations

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
    blocks: list[str] = field(default_factory=list)
    type: SpaceType = SpaceType.UNSPECIFIED
    type_source: str = "default"
    issues: list[str] = field(default_factory=list)  # reasons a person should look at it


@dataclass
class ExtractedDoor:
    footprint: Polygon
    point: Point
    connects: list[int]  # indexes into FloorExtraction.spaces
    # "door": drawn as a block or swing; "doorway": a gap in a wall with no door
    # drawn; "split": where open-plan rooms meet; "window"
    source: str = "door"
    span: LineString | None = None  # jamb to jamb, meters
    width: float | None = None


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


def _walk(entities, parent_layer: str | None = None, depth: int = 0,
          expand: Callable[[DXFGraphic, str], bool] | None = None):
    """Yield (entity, effective layer) through nested block inserts. Entities on
    layer "0" inside a block take the layer of the insert, as CAD programs show them.
    ``expand(insert, layer)`` returning False keeps a block's contents out."""
    for e in entities:
        layer = e.dxf.get("layer", "0")
        if parent_layer is not None and layer == "0":
            layer = parent_layer
        yield e, layer
        if e.dxftype() == "INSERT":
            for attrib in e.attribs:
                a_layer = attrib.dxf.get("layer", "0")
                yield attrib, (layer if a_layer == "0" else a_layer)
            if depth < MAX_BLOCK_DEPTH and (expand is None or expand(e, layer)):
                try:
                    children = list(e.virtual_entities())
                except Exception:  # broken or unsupported block content
                    continue
                yield from _walk(children, layer, depth + 1, expand)


def modelspace_entities(doc: Drawing, region: tuple[float, float, float, float] | None = None):
    """Top-level entities of the drawing; with ``region`` (x0, y0, x1, y1 in drawing
    units) only those whose middle lies inside it, for drawings that hold several
    floors (or sheets) side by side."""
    if region is None:
        yield from doc.modelspace()
        return
    entities, centres = _entity_index(doc)
    x0, y0, x1, y1 = region
    inside = (centres[:, 0] >= x0) & (centres[:, 0] <= x1) & (centres[:, 1] >= y0) & (centres[:, 1] <= y1)
    for i in np.nonzero(inside)[0]:
        yield entities[i]


def _entity_index(doc: Drawing):
    """The middle of every top-level entity, measured once per drawing: picking one
    plan out of a sheet set is then a lookup, not a pass over every entity."""
    index = getattr(doc, "_storeypath_index", None)
    if index is None:
        entities, centres = [], []
        cache = ezdxf.bbox.Cache()
        for e in doc.modelspace():
            try:
                ext = ezdxf.bbox.extents([e], fast=True, cache=cache)
            except Exception:
                continue
            if ext.has_data:
                entities.append(e)
                centres.append((ext.center.x, ext.center.y))
        index = (entities, np.array(centres, dtype=float).reshape(-1, 2))
        doc._storeypath_index = index
    return index


def _flatten(e: DXFGraphic, tolerance: float) -> tuple[list[tuple[float, float]], bool] | None:
    """Points along an entity and whether it is closed."""
    try:
        path = ezdxf.path.make_path(e)
    except (TypeError, ValueError):
        return None
    pts = [(v.x, v.y) for v in path.flattening(tolerance)]
    if len(pts) < 2:
        return None
    closed = bool(getattr(e, "closed", False)) or e.dxftype() in ("CIRCLE",)
    if not closed and len(pts) > 3:
        closed = Point(pts[0]).distance(Point(pts[-1])) <= tolerance
    return pts, closed


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
        if ext.has_data:
            c = ext.center
            return c.x, c.y
    except Exception:
        pass
    insert = e.dxf.get("insert")
    return (insert.x, insert.y) if insert is not None else None


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


def extract_floor(
    doc: Drawing,
    profile: Profile,
    units: str | None = None,
    region: tuple[float, float, float, float] | None = None,
    offset: tuple[float, float] | None = None,
    skip_label: Callable[[str], bool] | None = None,
) -> FloorExtraction:
    """Spaces, doors and outline of one floor, in meters. ``region`` limits the
    drawing to one floor's plan; ``offset`` (drawing units) is subtracted from every
    point so that floors drawn side by side line up. ``skip_label`` leaves out texts
    on label layers that do not name rooms (levels, notes…)."""
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
    layer_counts: Counter[str] = Counter()
    loose_door_entities = 0

    def expand(insert, layer) -> bool:  # a door block is read whole, not its lines
        return not profile.door_layers.fullmatch(layer)

    for e, layer in _walk(modelspace_entities(doc, region), expand=expand):
        kind = e.dxftype()
        layer_counts[layer] += 1
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
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
        on_walls = bool(profile.wall_layers.fullmatch(layer))  # walls are kept in every case
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

    door_shapes = _merge_doors(door_shapes)
    doorways: list[Polygon] = []
    open_edges = fabric = None
    spaces, containers = _clean_spaces(closed_shapes, profile, warnings)
    outline = _floor_outline(spaces, containers)
    used = "outlines"
    if not spaces and method != "outlines" and (wall_lines or wall_fills):
        found = spaces_from_walls(
            wall_lines, wall_fills, door_shapes, opening_lines, [lb.point for lb in labels], profile.walls,
            profile.spaces.min_area,
        )
        spaces = [ExtractedSpace(polygon=p, layer="walls") for p in found.polygons]
        doorways, open_edges, fabric = found.doorways, found.open_edges, found.fabric
        outline, used = found.outline, "walls"
        if found.pockets:
            warnings.append(
                f"{found.pockets} area(s) open to the outside were left out; "
                "if one is a room, check that its doors are drawn on a door layer"
            )

    if not spaces:
        top = ", ".join(f"{name} ({n})" for name, n in layer_counts.most_common(15))
        looked = {"outlines": f"outlines on layers matching {profile.spaces.layers}",
                  "walls": f"walls on layers matching {profile.walls.layers}"}
        where = " or ".join(v for k, v in looked.items() if method in (k, "auto"))
        warnings.append(f"no spaces found from {where}; busiest layers in this drawing: {top}")

    cuts: list[LineString] = []
    if used == "walls":
        spaces, cuts = _split_open_areas(spaces, labels, profile)
        for s in spaces:
            s.issues += open_issue(s.polygon, open_edges)
    _assign_labels(spaces, labels, profile, warnings)
    _assign_blocks(spaces, blocks)
    for s in spaces:
        s.type, s.type_source = profile.classify(s.name, s.blocks, s.layer)

    if fabric is None and (wall_lines or wall_fills):
        fabric = read_fabric(wall_lines, wall_fills, door_shapes, opening_lines, profile.walls)
    doors = _connect_doors(spaces, [d.box for d in door_shapes], profile.doors.reach, warnings,
                           [(d.span, d.width) for d in door_shapes])
    # Openings closed with no door drawn count as doors between two spaces; on the
    # outside they are most likely windows.
    doors += [
        replace(d, source="doorway")
        for d in _connect_doors(spaces, doorways, profile.doors.reach, [])
        if len(d.connects) == 2
    ]
    doors += [
        replace(d, source="split")
        for d in _connect_doors(spaces, [c.buffer(0.05) for c in cuts], profile.doors.reach, [])
        if len(d.connects) == 2
    ]
    if fabric is not None:
        doors += [
            replace(d, source="window")
            for d in _connect_doors(spaces, [w.buffer(0.15, cap_style="flat") for w in fabric.windows],
                                    profile.doors.reach, [], [(w, round(w.length, 3)) for w in fabric.windows])
        ]
    if loose_door_entities and not door_shapes:
        warnings.append(
            f"{loose_door_entities} lines/arcs on door layers are not door blocks or swing arcs; no doors found"
        )
    if offset:
        dx, dy = -offset[0] * scale, -offset[1] * scale
        for s in spaces:
            s.polygon = translate(s.polygon, dx, dy)
        for d in doors:
            d.footprint, d.point = translate(d.footprint, dx, dy), translate(d.point, dx, dy)
            d.span = translate(d.span, dx, dy) if d.span is not None else None
        outline = translate(outline, dx, dy) if outline is not None else None
        if fabric is not None:
            fabric.walls = translate(fabric.walls, dx, dy)
    walls = fabric.walls if fabric is not None and not fabric.walls.is_empty else None
    if spaces:
        rooms = unary_union([s.polygon for s in spaces])
        if walls is not None:
            walls = _attached(walls, rooms)
        if outline is not None:  # drawn from the same walls: without the markers' pieces too
            outline = unary_union([p for p in as_polygons(outline) if p.distance(rooms) <= 0.1]) or outline
    return FloorExtraction(spaces, doors, outline, scale, warnings, used, walls, _thickness(walls))


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
    """A door block: its extent, and the swing arcs drawn in it."""
    ext = ezdxf.bbox.extents([e], fast=True)
    if not ext.has_data:
        return None
    swings = []
    try:
        for child in e.virtual_entities():
            if child.dxftype() == "ARC" and (shape := _door_swing(child, scale)) is not None:
                swings += shape.swings
    except Exception:  # broken or unsupported block content
        pass
    return DoorShape(box(ext.extmin.x * scale, ext.extmin.y * scale, ext.extmax.x * scale, ext.extmax.y * scale),
                     swings)


def _merge_doors(doors: list[DoorShape]) -> list[DoorShape]:
    """One door per opening: the two leaves of a double door become one."""
    if len(doors) < 2:
        return doors
    merged = as_polygons(unary_union([d.box.buffer(DOOR_MERGE_M / 2, join_style="mitre") for d in doors]))
    out = [DoorShape(box(*p.buffer(-DOOR_MERGE_M / 2, join_style="mitre").bounds)) for p in merged]
    for d in doors:
        target = next(o for o, p in zip(out, merged) if p.intersects(d.box))
        target.swings += d.swings
    return out


def _split_open_areas(spaces, labels, profile) -> tuple[list[ExtractedSpace], list[LineString]]:
    """Spaces holding the labels of several rooms, split between them where they
    are narrowest (see split.py)."""
    if not spaces or not labels:
        return spaces, []
    tree = STRtree([s.polygon for s in spaces])
    per_space: dict[int, list[Label]] = {}
    for label in labels:
        i = _containing_space(tree, spaces, label.point)
        if i is not None:
            per_space.setdefault(i, []).append(label)
    out, cuts = [], []
    for i, s in enumerate(spaces):
        groups = _label_groups(per_space.get(i, []))
        if len(groups) < 2:
            out.append(s)
            continue
        parts, lines = split_by_labels(
            s.polygon, [[lb.point for lb in g] for g in groups], profile.spaces.max_split, profile.spaces.min_area
        )
        if len(parts) == 1:
            out.append(s)
            continue
        cuts += lines
        note = "separated from a neighbouring room where no wall is drawn; check the dividing line"
        out += [replace(s, polygon=p, issues=[*s.issues, note]) for p in parts]
    return out, cuts


def _wall_hatch(e, tol, scale, max_thickness, lines, fills) -> None:
    """A hatch on a wall layer: a filled wall area if small or thin, otherwise just
    its boundary (a hatch covering a whole floor must not turn it into wall)."""
    for path in ezdxf.path.from_hatch(e):
        pts = [(v.x * scale, v.y * scale) for v in path.flattening(tol)]
        if len(pts) < 3:
            continue
        for poly in as_polygons(make_valid(Polygon(pts))):
            if poly.area <= HATCH_FILL_MAX_M2 or poly.buffer(-max_thickness / 2).is_empty:
                fills.append(poly)
            else:
                lines.append(poly.exterior)


def _clean_spaces(
    shapes: list[tuple[Polygon, str]], profile: Profile, warnings: list[str]
) -> tuple[list[ExtractedSpace], list[Polygon]]:
    """Drop tiny and duplicate outlines. Outlines that contain several others are
    returned separately as floor-outline candidates instead of spaces."""
    shapes = [(p, layer) for p, layer in shapes if p.area >= profile.spaces.min_area]
    shapes.sort(key=lambda s: -s[0].area)

    kept: list[ExtractedSpace] = []
    containers: list[Polygon] = []
    for i, (poly, layer) in enumerate(shapes):
        inner = [
            q for q, _ in shapes[i + 1:]
            if poly.contains(q.representative_point()) and q.area < poly.area * 0.9
        ]
        if len(inner) >= 2:
            containers.append(poly)
            continue
        dup = next((k for k in kept if iou(k.polygon, poly) > 0.95), None)
        if dup is not None:
            continue
        if len(inner) == 1:
            poly = poly.difference(inner[0])
            warnings.append(
                f"an outline on {layer} contains one other outline; the inner area was cut out of it"
            )
        kept.append(ExtractedSpace(polygon=poly, layer=layer))

    if containers:
        warnings.append(
            f"{len(containers)} outline(s) enclosing several spaces were used as the floor outline, not as spaces"
        )
    _warn_overlaps(kept, warnings)
    return kept, containers


def _floor_outline(spaces: list[ExtractedSpace], containers: list[Polygon]):
    if containers:
        return unary_union(containers)
    if not spaces:
        return None
    c = OUTLINE_CLOSING_M
    merged = unary_union([s.polygon.buffer(c, join_style="mitre") for s in spaces]).buffer(
        -c, join_style="mitre"
    )
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
        spaces[i].name, spaces[i].number = _split_label([ln for lb in ls for ln in lb.lines], profile)
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


def _connect_doors(spaces, door_boxes, reach, warnings, spans=None) -> list[ExtractedDoor]:
    """Doors (by their extents) and the one or two spaces each joins. ``spans`` gives
    each door's (span, width) when known."""
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
        span, width = spans[k] if spans else (None, None)
        doors.append(ExtractedDoor(footprint=fp, point=point, connects=connects, span=span, width=width))
    if unconnected:
        warnings.append(f"{unconnected} door(s) are not next to any space and were skipped")
    return doors
