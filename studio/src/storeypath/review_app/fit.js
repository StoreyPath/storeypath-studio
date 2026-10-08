// Where an item may stand, and the magnet that lines it up: an item stays in the
// room it was placed in (a space, wall to wall; a zone has no walls, so the space it
// is part of), and, near a wall or another item, turns square to it and moves up
// against it. Everything here is in the plan's metres (the drawing's, y up), an
// item's turn counter-clockwise, its front (where its user sits) its own -y.
// Pure functions, no page: review.js uses them as an item is placed, dragged,
// nudged or turned, and the tests run them in Node.

/** What goes with a desk, by the grade it is for, as the viewers draw it: visitors'
 * chairs across it (armchairs for the president's), a return at its side (an
 * L-shaped desk), a cabinet behind its chair, and a high-backed chair. */
export const DESK_SETS = {
  junior: { visitors: 0 },
  senior: { visitors: 0, return: true },
  section_head: { visitors: 1, return: true },
  manager: { visitors: 2, return: true },
  director: { visitors: 2, return: true, cabinet: true, executive: true },
  c_level: { visitors: 2, return: true, cabinet: true, executive: true },
  president: { visitors: 2, armchairs: true, return: true, cabinet: true, executive: true },
};

/** Where visitors' chairs stand across a desk ``w`` wide: their middles along it, and
 * their size. */
export function visitorChairs(set, w) {
  const vw = set.armchairs ? 0.7 : 0.46, vd = set.armchairs ? 0.62 : 0.46;
  const xs = set.visitors === 1 ? [0] : set.visitors === 2 ? [-1, 1].map((q) => q * Math.max(vw / 2 + 0.06, Math.min(w / 4, 0.6))) : [];
  return { xs, vw, vd };
}

/** The box an item takes in its own frame, [u0, v0, u1, v1]: its footprint, and for a
 * desk everything drawn round it (its chair and, by its grade, a return, a cabinet
 * behind the chair, visitors' chairs across it). ``t``: its type (width, depth, code,
 * grade). */
export function itemBox(t) {
  const w = t?.width ?? 1, d = t?.depth ?? 0.6;
  if (((t?.code || "").split("-")[0]) !== "DESK") return [-w / 2, -d / 2, w / 2, d / 2];
  const set = DESK_SETS[t.grade] || DESK_SETS.junior;
  const user = Math.max(set.executive ? 0.7 : 0.52, set.return ? 0.8 : 0, set.cabinet ? 1.4 : 0);
  const { xs, vw, vd } = visitorChairs(set, w);
  const across = xs.length ? 0.15 + vd : 0;
  const side = Math.max(w / 2, ...xs.map((x) => Math.abs(x) + vw / 2));
  return [-side, -(d / 2 + user), side, d / 2 + across];
}

const rad = (deg) => (deg * Math.PI) / 180;
const norm = (deg) => ((deg % 360) + 360) % 360;

/** The box's corners on the plan, an item at ``at`` turned ``rot``°: counter-clockwise
 * from its own (u0, v0). */
export function corners(at, rot, [u0, v0, u1, v1]) {
  const c = Math.cos(rad(rot)), s = Math.sin(rad(rot));
  return [[u0, v0], [u1, v0], [u1, v1], [u0, v1]].map(([u, v]) => [at[0] + u * c - v * s, at[1] + u * s + v * c]);
}

/** A space's or zone's rings (GeoJSON Polygon or MultiPolygon): outlines and holes. */
export function ringsOf(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat();
  return [];
}

/** Whether a point is in rings (even-odd: holes left out). */
export function inRings(p, rings) {
  let inside = false;
  for (const ring of rings) {
    for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
      const [xj, yj] = ring[j], [xk, yk] = ring[k];
      if ((yj > p[1]) !== (yk > p[1]) && p[0] < ((xk - xj) * (p[1] - yj)) / (yk - yj) + xj) inside = !inside;
    }
  }
  return inside;
}

/** The room an item placed at ``p`` belongs to: the space (not ignored) it is in, a
 * zone's own space when it is in a zone. ``spaces``: the floor's spaces and zones. */
export function roomAt(p, spaces) {
  const hit = (u) => !u.ignored && inRings(p, ringsOf(u.geometry));
  const space = spaces.find((u) => u.kind === "space" && hit(u));
  if (space) return space;
  const zone = spaces.find((u) => u.kind === "zone" && hit(u));
  return zone ? spaces.find((u) => u.id === zone.space_id) || null : null;
}

const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
/** Whether two segments cross, touching ends aside. */
function crosses(a, b, c, d) {
  const d1 = cross(c, d, a), d2 = cross(c, d, b), d3 = cross(a, b, c), d4 = cross(a, b, d);
  return ((d1 > 1e-12 && d2 < -1e-12) || (d1 < -1e-12 && d2 > 1e-12)) && ((d3 > 1e-12 && d4 < -1e-12) || (d3 < -1e-12 && d4 > 1e-12));
}

/** Whether the item's box (an item at ``at`` turned ``rot``) stands wholly in the room
 * (its ``rings``): its corners in it, no wall across it, no pillar (a hole) within it.
 * Up against a wall is in (a millimetre's give). */
export function fits(at, rot, box, rings) {
  const give = 0.002;
  const inner = [box[0] + give, box[1] + give, box[2] - give, box[3] - give];
  if (inner[0] >= inner[2] || inner[1] >= inner[3]) return false;
  const c = corners(at, rot, inner);
  if (!c.every((p) => inRings(p, rings))) return false;
  for (const ring of rings) {
    for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
      for (let i = 0; i < 4; i++) if (crosses(ring[k], ring[j], c[i], c[(i + 1) % 4])) return false;
      if (inRings(ring[j], [c])) return false; // a corner of the room (a pillar's) within it
    }
  }
  return true;
}

/** A room's walls: each edge of its rings, its direction and the way into the room. */
export function wallsOf(rings) {
  const out = [];
  for (const ring of rings) {
    for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
      const a = ring[k], b = ring[j];
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (len < 0.05) continue;
      const t = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
      let n = [-t[1], t[0]];
      const mid = [(a[0] + b[0]) / 2 + n[0] * 0.01, (a[1] + b[1]) / 2 + n[1] * 0.01];
      if (!inRings(mid, rings)) n = [-n[0], -n[1]];
      out.push({ a, b, t, n, len });
    }
  }
  return out;
}

/** How far the box is from a wall, into the room (negative: through it), and whether
 * it is along the wall (overlaps it, give or take ``reach``). */
function gapTo(wall, c, reach) {
  const along = c.map((p) => (p[0] - wall.a[0]) * wall.t[0] + (p[1] - wall.a[1]) * wall.t[1]);
  const off = c.map((p) => (p[0] - wall.a[0]) * wall.n[0] + (p[1] - wall.a[1]) * wall.n[1]);
  return { gap: Math.min(...off), beside: Math.max(...along) > -reach && Math.min(...along) < wall.len + reach };
}

/** The turn nearest ``rot`` that puts one of the item's sides (``back`` only: its back,
 * its own +y) against a wall facing out along ``out`` (a direction). */
function squareTo(rot, out, backOnly) {
  const psi = (Math.atan2(out[1], out[0]) * 180) / Math.PI; // the way out of the room
  if (backOnly) return norm(psi - 90); // its own +y is at rot + 90
  const delta = ((((psi - rot) % 90) + 135) % 90) - 45; // to the nearest quarter turn
  return norm(rot + delta);
}

/**
 * The magnet: the item (at ``at``, turned ``rot``, its ``box``) moved up against what
 * is near, and turned square to it. ``rings``: its room's; ``others``: the other items
 * in the room, {at, rot, box}; ``reach``: how near (metres) is near; ``mount``: its
 * type's (one on a wall goes against the nearest wall, its back to it; one on the
 * ceiling is not drawn to walls). Returns {at, rot, guides: [[p, q], …]} (what it was
 * drawn to, to show).
 */
export function snap({ at, rot, box, rings, others = [], reach = 0.3, mount = "floor" }) {
  let pos = [...at], turn = rot;
  const guides = [];
  const walls = rings.length && mount !== "ceiling" ? wallsOf(rings) : [];
  const locked = []; // the directions it is held in, by a wall
  // the nearest wall along it: within reach (on a wall: the nearest one, however far)
  let best = null;
  for (const w of walls) {
    const { gap, beside } = gapTo(w, corners(pos, turn, box), reach);
    if (!beside) continue;
    if (mount === "wall" || Math.abs(gap) <= reach) {
      if (!best || Math.abs(gap) < Math.abs(best.gap)) best = { w, gap };
    }
  }
  if (best) {
    const w = best.w;
    turn = squareTo(turn, [-w.n[0], -w.n[1]], mount === "wall");
    const { gap } = gapTo(w, corners(pos, turn, box), Infinity);
    pos = [pos[0] - gap * w.n[0], pos[1] - gap * w.n[1]];
    locked.push(w.n);
    guides.push([w.a, w.b]);
    // and into a corner: a wall across this one, within reach
    let corner = null;
    for (const v of walls) {
      if (v === w || Math.abs(v.t[0] * w.t[0] + v.t[1] * w.t[1]) > 0.2) continue;
      const g = gapTo(v, corners(pos, turn, box), reach);
      if (g.beside && Math.abs(g.gap) <= reach && (!corner || Math.abs(g.gap) < Math.abs(corner.gap))) corner = { v, gap: g.gap };
    }
    if (corner) {
      pos = [pos[0] - corner.gap * corner.v.n[0], pos[1] - corner.gap * corner.v.n[1]];
      locked.push(corner.v.n);
      guides.push([corner.v.a, corner.v.b]);
    }
  }
  // other items: square to one turned nearly as it is (when no wall turned it), then
  // side by side with it and lined up with its edges, along what no wall holds
  const free = (dir) => locked.every((n) => Math.abs(n[0] * dir[0] + n[1] * dir[1]) < 0.2);
  for (const axis of [0, 1]) {
    let pick = null;
    for (const o of others) {
      const diff = ((((o.rot - turn) % 90) + 135) % 90) - 45;
      if (Math.abs(diff) > (best ? 0.5 : 10)) continue;
      const oc = Math.cos(rad(o.rot)), os = Math.sin(rad(o.rot));
      const dir = axis === 0 ? [oc, os] : [-os, oc]; // o's own x, then its own y
      if (!free(dir)) continue;
      const along = (p) => p[0] * dir[0] + p[1] * dir[1];
      const ours = corners(pos, turn + diff, box).map(along), theirs = corners(o.at, o.rot, o.box).map(along);
      const [a0, a1] = [Math.min(...ours), Math.max(...ours)], [b0, b1] = [Math.min(...theirs), Math.max(...theirs)];
      // across the other way: only when the two are side by side, not far apart
      const odir = [-dir[1], dir[0]], across = (p) => p[0] * odir[0] + p[1] * odir[1];
      const oa = corners(pos, turn + diff, box).map(across), ob = corners(o.at, o.rot, o.box).map(across);
      const apart = Math.max(Math.min(...oa) - Math.max(...ob), Math.min(...ob) - Math.max(...oa));
      if (apart > reach) continue;
      for (const shift of [b1 - a0, b0 - a1, b0 - a0, b1 - a1]) { // against it either side, or its edges lined up
        if (Math.abs(shift) <= reach && (!pick || Math.abs(shift) < Math.abs(pick.shift))) pick = { shift, dir, diff, o };
      }
    }
    if (pick) {
      turn = norm(turn + pick.diff);
      pos = [pos[0] + pick.shift * pick.dir[0], pos[1] + pick.shift * pick.dir[1]];
      locked.push(pick.dir);
      const oc = corners(pick.o.at, pick.o.rot, pick.o.box), mc = corners(pos, turn, box);
      guides.push([[(oc[0][0] + oc[2][0]) / 2, (oc[0][1] + oc[2][1]) / 2], [(mc[0][0] + mc[2][0]) / 2, (mc[0][1] + mc[2][1]) / 2]]);
    }
  }
  return { at: pos, rot: turn, guides };
}

/** Where the item may go on its way from ``from`` (where it fits) towards ``to``: all
 * the way when it fits there, else as far as it fits (against the wall it met). */
export function stopAt(from, to, rot, box, rings) {
  if (fits(to, rot, box, rings)) return to;
  let lo = 0, hi = 1;
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2, p = [from[0] + (to[0] - from[0]) * mid, from[1] + (to[1] - from[1]) * mid];
    if (fits(p, rot, box, rings)) lo = mid;
    else hi = mid;
  }
  return [from[0] + (to[0] - from[0]) * lo, from[1] + (to[1] - from[1]) * lo];
}

/** The nearest place to ``p`` (within ``within`` metres) where the item fits in the
 * room turned ``rot``, or null: for placing it where it was asked for, or turning it
 * where it stands. */
export function nearestFit(p, rot, box, rings, within = 3) {
  if (fits(p, rot, box, rings)) return p;
  const step = 0.05;
  for (let r = step; r <= within + 1e-9; r += step) {
    const n = Math.max(8, Math.ceil((2 * Math.PI * r) / step));
    let found = null;
    for (let i = 0; i < n; i++) {
      const q = [p[0] + r * Math.cos((2 * Math.PI * i) / n), p[1] + r * Math.sin((2 * Math.PI * i) / n)];
      if (fits(q, rot, box, rings)) { found = q; break; }
    }
    if (found) return found;
  }
  return null;
}

/**
 * Where an item goes when it is placed, dragged, nudged or turned: the magnet (unless
 * ``magnet`` is false: a nudge), kept in its room; nothing of either when ``free``.
 * ``want``: {at, rot} asked for; ``last``: {at, rot} where it stood, or null for a new
 * one; ``rings``: its room's, or [] when it is in no room (nothing holds it). Returns
 * {at, rot, guides, held} (held: it met a wall on the way), or null when it cannot be
 * there at all (it does not fit in the room, or not turned so).
 */
export function settle({ want, last, box, rings, others = [], reach = 0.3, mount = "floor", free = false, magnet = true }) {
  if (free) return { at: want.at, rot: want.rot, guides: [], held: false };
  const s = magnet ? snap({ ...want, box, rings, others, reach, mount }) : { ...want, guides: [] };
  if (!rings.length || fits(s.at, s.rot, box, rings)) return { at: s.at, rot: s.rot, guides: s.guides, held: false };
  if (magnet && fits(want.at, want.rot, box, rings)) return { at: want.at, rot: want.rot, guides: [], held: false };
  if (last && fits(last.at, want.rot, box, rings)) { // as far as it goes, then against what it met
    const stopped = stopAt(last.at, want.at, want.rot, box, rings);
    if (magnet) {
      const again = snap({ at: stopped, rot: want.rot, box, rings, others, reach, mount });
      if (fits(again.at, again.rot, box, rings)) return { at: again.at, rot: again.rot, guides: again.guides, held: true };
    }
    return { at: stopped, rot: want.rot, guides: [], held: true };
  }
  const near = nearestFit(want.at, want.rot, box, rings, last ? 1 : 3);
  return near ? { at: near, rot: want.rot, guides: [], held: true } : null;
}
