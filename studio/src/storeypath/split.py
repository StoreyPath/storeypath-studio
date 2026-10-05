"""Splitting open-plan areas into the rooms their labels name.

Drawings often have no wall between a sitting area, a dining area and a hall:
they form one enclosed area with several room labels in it. A person reading the
plan separates them where the area is narrowest between the labels: between two
wall ends, from a wall end to the wall it faces, at a column.

This does the same: the area is triangulated, and a minimum cut (the shortest
total length of lines) separates one label from the others; that repeats until
every part has the labels of one room. Labels that could only be separated by
long cuts stay together: they are zones of one room.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import linemerge, unary_union
from shapely.ops import split as split_by_line

from .geometry import as_polygons

SIMPLIFY_M = 0.05  # curves are simplified this much before triangulating
STRAIGHTER = 1.2  # a straight cut across the room may be this much longer than the shortest


def split_by_labels(
    polygon: Polygon, groups: list[list[Point]], max_cut: float, min_area: float
) -> tuple[list[Polygon], list[LineString]]:
    """Split ``polygon`` so that each part holds one group of label points.
    Returns the parts and the cut lines between them."""
    work = [(polygon, groups)]
    parts, cuts = [], []
    budget = 2 * len(groups)  # each split separates at least one group
    while work:
        poly, gs = work.pop()
        found = _cheapest_separation(poly, gs, max_cut, min_area) if len(gs) > 1 and budget > 0 else None
        if found is None:
            parts.append(poly)
            continue
        budget -= 1
        inside, outside, cut, gs_in, gs_out = found
        straight = _straighter(poly, cut, gs_in, gs_out, max_cut, min_area)
        if straight is not None:
            inside, outside, cut = straight
        cuts.extend(cut)
        work += [(inside, gs_in), (outside, gs_out)]
    return parts, cuts


def _straighter(poly: Polygon, cut: list[LineString], gs_in, gs_out, max_cut: float, min_area: float):
    """A person divides a room straight across, square to its walls, not along the
    shortest diagonal between two wall corners: a straight cut through the same
    place (either end of the shortest, or its middle), along or across the room's
    walls, that still separates the labels and is not much longer."""
    if len(cut) != 1 or cut[0].length == 0:
        return None
    line = cut[0]
    angle = _wall_direction(poly)
    reach = math.dist(poly.bounds[:2], poly.bounds[2:])
    best = None
    for through in (Point(line.coords[0]), Point(line.coords[-1]), line.interpolate(0.5, normalized=True)):
        for a in (angle, angle + math.pi / 2):
            dx, dy = math.cos(a) * reach, math.sin(a) * reach
            across = LineString([(through.x - dx, through.y - dy), (through.x + dx, through.y + dy)])
            pieces = [g for g in shapely.get_parts(poly.intersection(across))
                      if isinstance(g, LineString) and not g.is_empty and g.length > 0]
            if not pieces:
                continue
            piece = min(pieces, key=lambda g: g.distance(through))
            if piece.length > min(STRAIGHTER * line.length, max_cut) or (best and piece.length >= best[0]):
                continue
            # A line that ends on the boundary may not cut it (rounding): reach past it a little.
            (x0, y0), (x1, y1) = piece.coords[0], piece.coords[-1]
            ux, uy = (x1 - x0) / piece.length * 0.05, (y1 - y0) / piece.length * 0.05
            blade = LineString([(x0 - ux, y0 - uy), (x1 + ux, y1 + uy)])
            halves = [g for g in shapely.get_parts(split_by_line(poly, blade)) if isinstance(g, Polygon)]
            if len(halves) != 2 or min(h.area for h in halves) < min_area:
                continue
            for first, second in (halves, halves[::-1]):
                if all(_within(g, first) for g in gs_in) and all(_within(g, second) for g in gs_out):
                    best = (piece.length, first, second, piece)
    return (best[1], best[2], [best[3]]) if best else None


def _wall_direction(poly: Polygon) -> float:
    """The direction of a room's walls (radians, modulo a right angle): the mean of
    its edges' directions, each counted by its length."""
    xy = list(poly.exterior.coords)
    c = s = 0.0
    for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
        length, theta = math.hypot(x1 - x0, y1 - y0), math.atan2(y1 - y0, x1 - x0)
        c, s = c + length * math.cos(4 * theta), s + length * math.sin(4 * theta)
    return math.atan2(s, c) / 4


def _within(group: list[Point], poly: Polygon) -> bool:
    return sum(poly.covers(p) for p in group) * 2 > len(group)


def _cheapest_separation(poly: Polygon, groups, max_cut, min_area):
    mesh = _Mesh(poly.simplify(SIMPLIFY_M))
    if mesh.n < 2:
        return None
    seeds = [mesh.seeds(g) for g in groups]
    if any(not s for s in seeds):
        return None
    options = []
    for i, seed in enumerate(seeds):
        others = set().union(*(s for j, s in enumerate(seeds) if j != i))
        if seed & others:
            continue
        value, side = mesh.min_cut(seed, others)
        if value <= max_cut:
            options.append((value, i, side))
    for _, _, side in sorted(options, key=lambda o: o[:2]):
        inside, outside = mesh.halves(side)
        if inside.area < min_area or outside.area < min_area:
            continue
        gs_in = [g for g in groups if _within(g, inside)]
        gs_out = [g for g in groups if not _within(g, inside)]
        if gs_in and gs_out:  # a cut must leave labels on both sides
            return inside, outside, mesh.cut_lines(side), gs_in, gs_out
    return None


class _Mesh:
    """A triangulation of a polygon and the adjacency between its triangles,
    weighted by the length of the edge they share."""

    def __init__(self, poly: Polygon):
        tris = [t for t in shapely.get_parts(shapely.constrained_delaunay_triangles(poly)) if t.area > 1e-9]
        self.tris = tris
        self.n = len(tris)
        self.tree = shapely.STRtree(tris)
        self.adj: list[dict[int, float]] = [dict() for _ in tris]
        self.shared: dict[tuple[int, int], tuple] = {}
        owner: dict[tuple, int] = {}
        for i, t in enumerate(tris):
            c = [tuple(np.round(p, 9)) for p in np.asarray(t.exterior.coords)[:3]]
            for a, b in ((c[0], c[1]), (c[1], c[2]), (c[2], c[0])):
                key = (a, b) if a <= b else (b, a)
                j = owner.pop(key, None)
                if j is None:
                    owner[key] = i
                    continue
                length = float(np.hypot(a[0] - b[0], a[1] - b[1]))
                self.adj[i][j] = self.adj[j][i] = length
                self.shared[(min(i, j), max(i, j))] = key

    def seeds(self, points: list[Point]) -> set[int]:
        out = set()
        for p in points:
            hits = [int(i) for i in self.tree.query(p, predicate="intersects")]
            if not hits:
                hits = [int(self.tree.nearest(p))]
            out.update(hits[:1])
        return out

    def min_cut(self, sources: set[int], sinks: set[int]) -> tuple[float, set[int]]:
        """Edmonds–Karp max flow from ``sources`` to ``sinks``; returns the cut's
        total length and the triangles on the source side."""
        flow: dict[tuple[int, int], float] = {}

        def residual(u: int, v: int) -> float:
            return self.adj[u][v] - flow.get((u, v), 0.0)

        total = 0.0
        while True:
            parent = {s: -1 for s in sources}
            queue = deque(sources)
            end = -1
            while queue and end < 0:
                u = queue.popleft()
                for v in self.adj[u]:
                    if v not in parent and residual(u, v) > 1e-9:
                        parent[v] = u
                        if v in sinks:
                            end = v
                            break
                        queue.append(v)
            if end < 0:
                return total, set(parent)
            path, v = [], end
            while parent[v] >= 0:
                path.append((parent[v], v))
                v = parent[v]
            push = min(residual(u, v) for u, v in path)
            for u, v in path:
                flow[(u, v)] = flow.get((u, v), 0.0) + push
                flow[(v, u)] = flow.get((v, u), 0.0) - push
            total += push

    def halves(self, side: set[int]) -> tuple[Polygon, Polygon]:
        """The two sides of a cut. Stray triangles that touch their side only at a
        corner join the other side, so no area is lost."""
        inside = as_polygons(unary_union([self.tris[i] for i in side]))
        outside = as_polygons(unary_union([t for i, t in enumerate(self.tris) if i not in side]))
        main_in = max(inside, key=lambda p: p.area)
        main_out = max(outside, key=lambda p: p.area)
        stray_in = [p for p in inside if p is not main_in]
        stray_out = [p for p in outside if p is not main_out]
        return _largest(unary_union([main_in, *stray_out])), _largest(unary_union([main_out, *stray_in]))

    def cut_lines(self, side: set[int]) -> list[LineString]:
        edges = [
            LineString(self.shared[(min(i, j), max(i, j))])
            for i in side for j in self.adj[i] if j not in side
        ]
        merged = linemerge(edges) if edges else None
        return list(shapely.get_parts(merged)) if merged is not None else []


def _largest(geom) -> Polygon:
    parts = as_polygons(geom)
    if len(parts) == 1:
        return parts[0]
    # Triangles touching at a single point leave slivers; keep the main body.
    return max(parts, key=lambda p: p.area) if parts else Polygon()
