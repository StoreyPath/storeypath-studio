"""Where an area sample is: the area a person chose on a floor (local metres) and the
sample's own frame, its lower-left corner at (0, 0), metres."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.affinity import translate
from shapely.geometry import box, mapping, shape

MAX_SIDE_M = 50.0  # an area is at most this wide and high: a sample is never a whole plan
MIN_SIDE_M = 0.5  # …and at least this
MARGIN_M = 1.0  # what lies this close around it comes too (a wall on its edge stays whole)


@dataclass(frozen=True)
class Area:
    """A rectangle on a floor, local metres (rounded to the centimetre)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def of(cls, value) -> "Area":
        """From a request: [x0, y0, x1, y1] (any two opposite corners), local metres."""
        if isinstance(value, dict):
            value = [value.get(k) for k in ("x0", "y0", "x1", "y1")]
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError("area: [x0, y0, x1, y1], two opposite corners in metres")
        try:
            xs = [float(v) for v in value]
        except (TypeError, ValueError):
            raise ValueError("area: [x0, y0, x1, y1], numbers of metres") from None
        if not all(math.isfinite(v) and abs(v) < 1e7 for v in xs):
            raise ValueError("area: [x0, y0, x1, y1], numbers of metres")
        x0, x1 = sorted((xs[0], xs[2]))
        y0, y1 = sorted((xs[1], xs[3]))
        area = cls(round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))
        if area.width < MIN_SIDE_M or area.height < MIN_SIDE_M:
            raise ValueError(f"the area is empty: drag a rectangle at least {MIN_SIDE_M:g} m on each side")
        if area.width > MAX_SIDE_M + 0.01 or area.height > MAX_SIDE_M + 0.01:
            raise ValueError(f"the area is {area.width:.1f} × {area.height:.1f} m: a sample is at most "
                             f"{MAX_SIDE_M:g} × {MAX_SIDE_M:g} m, so choose a smaller part of the floor")
        return area

    @property
    def width(self) -> float:
        return round(self.x1 - self.x0, 2)

    @property
    def height(self) -> float:
        return round(self.y1 - self.y0, 2)

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


class Frame:
    """The sample's frame: a floor's local metres moved so that the area's lower-left
    corner is (0, 0), and cut to the area and its margin (``extent``)."""

    def __init__(self, area: Area, margin: float = MARGIN_M):
        self.area, self.margin = area, margin
        self.dx, self.dy = area.x0, area.y0
        self.w, self.h = area.width, area.height
        self.local_extent = box(area.x0 - margin, area.y0 - margin, area.x1 + margin, area.y1 + margin)
        self.extent = box(-margin, -margin, self.w + margin, self.h + margin)  # the sample's frame
        self.rect = box(0, 0, self.w, self.h)  # the area itself

    def touches(self, geom) -> bool:
        """Whether a geometry of the floor (local metres) touches the area or its margin."""
        return geom is not None and not geom.is_empty and geom.intersects(self.local_extent)

    def moved(self, geom):
        """A geometry of the floor (local metres) in the sample's frame, not cut."""
        return translate(geom, -self.dx, -self.dy)

    def geom(self, geom):
        """A geometry of the floor (local metres) in the sample's frame, cut to its extent;
        None when nothing of it is there."""
        if geom is None or geom.is_empty:
            return None
        out = self.moved(geom).intersection(self.extent)
        return None if out.is_empty else out

    def geojson(self, value) -> dict | None:
        """A GeoJSON geometry of the floor, in the sample's frame, cut (coordinates in mm)."""
        if not value:
            return None
        g = self.geom(shape(value))
        return None if g is None else rounded(mapping(g))

    def point(self, x: float, y: float) -> list[float]:
        return [round(x - self.dx, 3), round(y - self.dy, 3)]

    def points(self, pts) -> list[list[float]]:
        return [self.point(x, y) for x, y in pts]

    def holds(self, x: float, y: float) -> bool:
        """Whether a point of the floor lies in the sample's extent."""
        return self.local_extent.covers(_point(x, y))


def _point(x, y):
    from shapely.geometry import Point

    return Point(x, y)


def rounded(value, digits: int = 3):
    """Coordinates of a GeoJSON geometry (or nested lists of numbers) rounded."""
    if isinstance(value, dict):
        return {k: rounded(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(v, digits) for v in value]
    if isinstance(value, float):
        return round(value, digits)
    return value
