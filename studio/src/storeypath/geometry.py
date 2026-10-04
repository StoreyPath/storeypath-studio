"""Small geometry helpers shared by extraction and conversion. Units are meters."""

from __future__ import annotations

from shapely.geometry import Polygon


def as_polygons(geom) -> list[Polygon]:
    """The non-empty polygons in any geometry."""
    if isinstance(geom, Polygon):
        return [geom] if not geom.is_empty else []
    if hasattr(geom, "geoms"):
        return [g for part in geom.geoms for g in as_polygons(part)]
    return []


def iou(a, b) -> float:
    """Intersection over union of two areas: 1.0 when identical, 0.0 when disjoint."""
    if not a.intersects(b):
        return 0.0
    inter = a.intersection(b).area
    return inter / (a.area + b.area - inter)


def closing(geom, r: float):
    """Fill gaps and slots narrower than 2·r while keeping square corners square."""
    return geom.buffer(r, join_style="mitre").buffer(-r, join_style="mitre")
