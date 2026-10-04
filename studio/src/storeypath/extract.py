"""Turning one floor drawing into spaces, labels and doors.

Everything here works in local meters: drawing coordinates times the unit scale.
IDs are not assigned here (see convert.py).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import ezdxf.bbox
import ezdxf.path
from ezdxf.document import Drawing
from ezdxf.entities import DXFGraphic
from shapely import STRtree, make_valid
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, unary_union

from .cad import meters_per_unit
from .profile import Profile
from .types import SpaceType

MAX_BLOCK_DEPTH = 8
CURVE_TOLERANCE_M = 0.02
OUTLINE_CLOSING_M = 0.3
OUTLINE_MIN_HOLE_M2 = 10.0


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


@dataclass
class ExtractedDoor:
    footprint: Polygon
    point: Point
    connects: list[int]  # indexes into FloorExtraction.spaces


@dataclass
class FloorExtraction:
    spaces: list[ExtractedSpace]
    doors: list[ExtractedDoor]
    outline: Polygon | MultiPolygon | None
    scale: float  # meters per drawing unit
    warnings: list[str] = field(default_factory=list)


def _walk(entities, parent_layer: str | None = None, depth: int = 0):
    """Yield (entity, effective layer) through nested block inserts. Entities on
    layer "0" inside a block take the layer of the insert, as CAD programs show them."""
    for e in entities:
        layer = e.dxf.get("layer", "0")
        if parent_layer is not None and layer == "0":
            layer = parent_layer
        yield e, layer
        if e.dxftype() == "INSERT":
            for attrib in e.attribs:
                a_layer = attrib.dxf.get("layer", "0")
                yield attrib, (layer if a_layer == "0" else a_layer)
            if depth < MAX_BLOCK_DEPTH:
                try:
                    children = list(e.virtual_entities())
                except Exception:  # broken or unsupported block content
                    continue
                yield from _walk(children, layer, depth + 1)


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


def _as_polygons(geom) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom] if not geom.is_empty else []
    if hasattr(geom, "geoms"):
        return [g for part in geom.geoms for g in _as_polygons(part)]
    return []


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
    return (" ".join(names) or None), number


def extract_floor(doc: Drawing, profile: Profile, units: str | None = None) -> FloorExtraction:
    scale = meters_per_unit(doc, units)
    tol = CURVE_TOLERANCE_M / scale
    warnings: list[str] = []
    if not units and doc.header.get("$INSUNITS", 0) == 0:
        warnings.append(
            f"drawing has no unit setting; assumed 1 unit = {scale} m (set units on the floor to override)"
        )

    closed_shapes: list[tuple[Polygon, str]] = []
    linework: list[LineString] = []
    labels: list[Label] = []
    blocks: list[tuple[str, Point]] = []
    door_boxes: list[Polygon] = []
    layer_counts: Counter[str] = Counter()
    loose_door_entities = 0

    for e, layer in _walk(doc.modelspace()):
        kind = e.dxftype()
        layer_counts[layer] += 1
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
            if profile.label_layers.fullmatch(layer):
                lines, c = _text_lines(e), _center(e)
                if lines and c:
                    labels.append(Label(lines, Point(c[0] * scale, c[1] * scale)))
            continue
        if kind == "INSERT":
            if profile.door_layers.fullmatch(layer):
                ext = ezdxf.bbox.extents([e], fast=True)
                if ext.has_data:
                    door_boxes.append(
                        box(ext.extmin.x * scale, ext.extmin.y * scale, ext.extmax.x * scale, ext.extmax.y * scale)
                    )
            elif profile.block_layers.fullmatch(layer):
                c = _center(e)
                if c:
                    blocks.append((e.dxf.name, Point(c[0] * scale, c[1] * scale)))
            continue
        if profile.door_layers.fullmatch(layer) and kind in ("LINE", "ARC", "LWPOLYLINE"):
            loose_door_entities += 1
        if not profile.space_layers.fullmatch(layer):
            continue
        if kind == "HATCH":
            continue  # outlines are read from boundary lines, not fills
        flat = _flatten(e, tol)
        if flat is None:
            continue
        pts, closed = flat
        pts = [(x * scale, y * scale) for x, y in pts]
        if closed and len(pts) >= 3:
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = make_valid(poly)
            for p in _as_polygons(poly):
                closed_shapes.append((p, layer))
        else:
            linework.append(LineString(pts))

    if linework:
        for p in polygonize(unary_union(linework)):
            closed_shapes.append((p, "linework"))

    spaces, containers = _clean_spaces(closed_shapes, profile, warnings)
    outline = _floor_outline(spaces, containers)

    if not spaces:
        top = ", ".join(f"{name} ({n})" for name, n in layer_counts.most_common(15))
        warnings.append(
            f"no spaces found on layers matching {profile.spaces.layers}; "
            f"busiest layers in this drawing: {top}"
        )

    _assign_labels(spaces, labels, profile, warnings)
    _assign_blocks(spaces, blocks)
    for s in spaces:
        s.type, s.type_source = profile.classify(s.name, s.blocks, s.layer)

    doors = _connect_doors(spaces, door_boxes, profile.doors.reach, warnings)
    if loose_door_entities and not door_boxes:
        warnings.append(
            f"{loose_door_entities} lines/arcs on door layers are not blocks; doors are only read from blocks"
        )
    return FloorExtraction(spaces, doors, outline, scale, warnings)


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
        dup = next((k for k in kept if _iou(k.polygon, poly) > 0.95), None)
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
    for p in _as_polygons(merged):
        holes = [h for h in p.interiors if Polygon(h).area >= OUTLINE_MIN_HOLE_M2]
        parts.append(Polygon(p.exterior, holes))
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _iou(a: Polygon, b: Polygon) -> float:
    if not a.intersects(b):
        return 0.0
    inter = a.intersection(b).area
    return inter / (a.area + b.area - inter)


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
    for i, ls in per_space.items():
        ls.sort(key=lambda lb: (-round(lb.point.y, 1), lb.point.x))  # reading order
        spaces[i].name, spaces[i].number = _split_label([ln for lb in ls for ln in lb.lines], profile)
    if orphans:
        sample = ", ".join(repr(o) for o in orphans[:5])
        warnings.append(f"{len(orphans)} label(s) are not inside any space: {sample}")


def _assign_blocks(spaces, blocks) -> None:
    if not spaces:
        return
    tree = STRtree([s.polygon for s in spaces])
    for name, pt in blocks:
        i = _containing_space(tree, spaces, pt)
        if i is not None:
            spaces[i].blocks.append(name)


def _connect_doors(spaces, door_boxes, reach, warnings) -> list[ExtractedDoor]:
    if not spaces:
        return []
    polys = [s.polygon for s in spaces]
    tree = STRtree(polys)
    doors = []
    unconnected = 0
    for fp in door_boxes:
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
        doors.append(ExtractedDoor(footprint=fp, point=point, connects=connects))
    if unconnected:
        warnings.append(f"{unconnected} door(s) are not next to any space and were skipped")
    return doors
