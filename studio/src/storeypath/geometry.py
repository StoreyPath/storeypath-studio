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
    """Fill gaps and slots narrower than 2·r while keeping square corners square.
    Square-cornered offsets can lose a thin piece that meets others at an angle: what
    was there is always kept."""
    return geom.buffer(r, join_style="mitre").buffer(-r, join_style="mitre").union(geom)


def span_of_width(span, width: float) -> list[list[float]]:
    """A span (jamb to jamb, [[x, y], [x, y]]) of another width, along the same line,
    about the same middle."""
    (x0, y0), (x1, y1) = span[0], span[-1]
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 or 1.0
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    h = width / 2
    return [[round(cx - ux * h, 4), round(cy - uy * h, 4)], [round(cx + ux * h, 4), round(cy + uy * h, 4)]]


def leaves_of_width(swings, old_span, new_span) -> list:
    """A door's leaves ([hinge, free edge when open]) when its span changes: each hinge
    on the new jamb at its side, each leaf as much longer or shorter as the door."""
    (a0, a1), (b0, b1) = (old_span[0], old_span[-1]), (new_span[0], new_span[-1])
    old_w = ((a1[0] - a0[0]) ** 2 + (a1[1] - a0[1]) ** 2) ** 0.5 or 1.0
    k = (((b1[0] - b0[0]) ** 2 + (b1[1] - b0[1]) ** 2) ** 0.5) / old_w
    out = []
    for hinge, free in swings:
        near0 = (hinge[0] - a0[0]) ** 2 + (hinge[1] - a0[1]) ** 2 <= (hinge[0] - a1[0]) ** 2 + (hinge[1] - a1[1]) ** 2
        h = b0 if near0 else b1
        out.append([[round(h[0], 4), round(h[1], 4)],
                    [round(h[0] + (free[0] - hinge[0]) * k, 4), round(h[1] + (free[1] - hinge[1]) * k, 4)]])
    return out

