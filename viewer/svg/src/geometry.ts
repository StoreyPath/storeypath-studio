// Small plane geometry the engine needs: bounds, point in polygon, and a point
// well inside a polygon for a label or a pin (the pole of inaccessibility).

import type { Polygon, Ring, XY } from "./types.js";

export type Box = [number, number, number, number]; // x0, y0, x1, y1

export function boundsOf(polygons: Polygon[], into?: Box): Box {
  const b: Box = into ?? [Infinity, Infinity, -Infinity, -Infinity];
  for (const poly of polygons) {
    for (const p of poly[0] ?? []) {
      if (p[0] < b[0]) b[0] = p[0];
      if (p[1] < b[1]) b[1] = p[1];
      if (p[0] > b[2]) b[2] = p[0];
      if (p[1] > b[3]) b[3] = p[1];
    }
  }
  return b;
}

export function finite(b: Box): boolean {
  return b.every(Number.isFinite) && b[2] >= b[0] && b[3] >= b[1];
}

function inRing(ring: Ring, x: number, y: number): boolean {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i]!, b = ring[j]!;
    if ((a[1] > y) !== (b[1] > y) && x < ((b[0] - a[0]) * (y - a[1])) / (b[1] - a[1]) + a[0]) hit = !hit;
  }
  return hit;
}

export function inside(polygons: Polygon[], p: XY): boolean {
  return polygons.some((poly) => inRing(poly[0] ?? [], p[0], p[1]) && !poly.slice(1).some((h) => inRing(h, p[0], p[1])));
}

function segmentDistance(p: XY, a: XY, b: XY): number {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

/** Signed distance from a point to a polygon's edges: positive inside. */
function edgeDistance(poly: Polygon, p: XY): number {
  let d = Infinity;
  for (const ring of poly) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) d = Math.min(d, segmentDistance(p, ring[i]!, ring[j]!));
  }
  return inside([poly], p) ? d : -d;
}

/** A point inside a polygon, as far from its edges as can be found to `precision`
 * (the polylabel method): where a label or a pin sits in an L-shaped room. */
export function poleOf(polygons: Polygon[], precision = 0.05): XY {
  let best: XY = [0, 0], bestDist = -Infinity;
  for (const poly of polygons) {
    const b = boundsOf([poly]);
    if (!finite(b)) continue;
    const size = Math.min(b[2] - b[0], b[3] - b[1]);
    if (size <= 0) continue;
    let h = size / 2;
    type Cell = { x: number; y: number; h: number; d: number; max: number };
    const cell = (x: number, y: number, h: number): Cell => {
      const d = edgeDistance(poly, [x, y]);
      return { x, y, h, d, max: d + h * Math.SQRT2 };
    };
    const queue: Cell[] = [];
    for (let x = b[0]; x < b[2]; x += 2 * h) for (let y = b[1]; y < b[3]; y += 2 * h) queue.push(cell(x + h, y + h, h));
    const c = cell((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 0);
    let top = c.d > -Infinity ? c : queue[0]!;
    for (const q of queue) if (q.d > top.d) top = q;
    let guard = 0;
    while (queue.length && guard++ < 20000) {
      queue.sort((p, q) => p.max - q.max);
      const q = queue.pop()!;
      if (q.d > top.d) top = q;
      if (q.max - top.d <= precision) continue;
      h = q.h / 2;
      queue.push(cell(q.x - h, q.y - h, h), cell(q.x + h, q.y - h, h), cell(q.x - h, q.y + h, h), cell(q.x + h, q.y + h, h));
    }
    if (top.d > bestDist) {
      bestDist = top.d;
      best = [top.x, top.y];
    }
  }
  return best;
}

/** SVG path data for polygons (each ring closed). */
export function pathOf(polygons: Polygon[]): string {
  let d = "";
  for (const poly of polygons) {
    for (const ring of poly) {
      if (ring.length < 3) continue;
      d += `M${ring.map((p) => `${round(p[0])},${round(p[1])}`).join("L")}Z`;
    }
  }
  return d;
}

export const round = (v: number): number => Math.round(v * 1000) / 1000;
