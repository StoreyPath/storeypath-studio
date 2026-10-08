// Finding the way in a building (format 0.8): the way on a package's walking network
// (navigation.json) from one place to another, the same way every reader finds it:
// StoreyPath Studio (studio/src/storeypath/navigation.py), the Go module
// (go/navigation.go) and this module, shared by both viewers. spec/FORMAT.md,
// "Navigation (0.8)", is the rule; spec/conformance/routes.json holds ways all of
// them find the same.
//
//   import { route } from "@storeypath/viewer/navigation";   // or from the 2D or 3D viewer
//   const way = route(pkg, kioskItemId, officeSpaceId, { accessible: true });
//   way.steps.map((s) => s.text);   // "Walk 48 m along CORRIDOR to the lift", …
//
// Plain JavaScript, no dependencies: it works on the parsed navigation.json alone (and
// the package's items, for a way to an item), in a browser or in Node.

/** The node a place is arrived at. */
const ARRIVALS = new Set(["room", "lift", "stairs", "escalator", "ramp"]);
const DOORS = new Set(["door", "entrance"]);
const RIDES = new Set(["lift", "stairs", "escalator", "ramp"]);
/** "along" a corridor or ramp; "through" anything else. */
const THROUGH = new Set(["corridor", "ramp"]);
/** The way a person walks before a door: over this much of the way (metres). */
const WALK_BACK_M = 2.0;

const round2 = (v) => Math.floor(v * 100 + 0.5) / 100;
const wholeMetres = (v) => Math.floor(v + 0.5);
const capital = (text) => (/^[a-z]/.test(text) ? text[0].toUpperCase() + text.slice(1) : text);
const same = (p, q) => p[0] === q[0] && p[1] === q[1];
const lastOf = (list) => list[list.length - 1];

/** A package's walking network (navigation.json), ready to route on: its nodes,
 * places and floors by ID, and each node's edges. */
export class Graph {
  /** @param {object} nav navigation.json, as parsed */
  constructor(nav) {
    this.nav = nav;
    this.nodes = new Map((nav.nodes ?? []).map((n) => [n.id, n]));
    this.places = new Map((nav.places ?? []).map((p) => [p.id, p]));
    this.floors = new Map((nav.floors ?? []).map((f) => [f.id, f]));
    /** Each floor's place in the building, lowest first. */
    this.order = new Map((nav.floors ?? []).map((f, i) => [f.id, i]));
    /** Node ID → [the node at the other end, the edge][]. */
    this.adjacent = new Map([...this.nodes.keys()].map((id) => [id, []]));
    for (const e of nav.edges ?? []) {
      if (!this.nodes.has(e.from) || !this.nodes.has(e.to)) continue;
      this.adjacent.get(e.from).push([e.to, e]);
      this.adjacent.get(e.to).push([e.from, e]);
    }
  }

  /** The nodes a place is: a node's ID; a space's or a zone's (where it is arrived at:
   * its zones', for a space divided into zones); an item's (a kiosk's own node, else
   * the zone or space it stands in, from `items`: its feature or properties by ID).
   * Throws for an ID the network does not know. */
  ends(ref, items = null) {
    if (this.nodes.has(ref)) return [ref];
    const found = [];
    for (const n of this.nodes.values()) {
      if (ARRIVALS.has(n.kind) && (n.space_id === ref || n.zone_id === ref)) found.push(n.id);
    }
    if (found.length) return found.sort(compare);
    if (this.nodes.has(`kiosk:${ref}`)) return [`kiosk:${ref}`];
    const it = items?.get(ref);
    if (it) {
      const p = it.properties ?? it;
      for (const place of [p.zone_id, p.space_id]) if (place) return this.ends(place);
    }
    throw new Error(`no node, place or item ${ref} in the network`);
  }

  /** The place (zone, else space) a node is in; a door's first space. */
  placeOf(id) {
    const n = this.nodes.get(id);
    if (DOORS.has(n.kind)) return n.spaces?.[0] ?? null;
    return n.zone_id || n.space_id || null;
  }

  /** What a step calls a place ("OFFICE 112", "the corridor"). */
  label(place) {
    const p = place ? this.places.get(place) : null;
    return p ? p.label : "the room";
  }
}

/** Strings compared as their bytes (IDs are ASCII). */
function compare(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** A queue of [distance, node ID], the least first: by distance, then by ID. */
class Queue {
  #heap = [];
  get size() { return this.#heap.length; }
  #less(i, j) {
    const a = this.#heap[i], b = this.#heap[j];
    return a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
  }
  push(entry) {
    const h = this.#heap;
    h.push(entry);
    let i = h.length - 1;
    while (i > 0) {
      const up = (i - 1) >> 1;
      if (!this.#less(i, up)) break;
      [h[i], h[up]] = [h[up], h[i]];
      i = up;
    }
  }
  pop() {
    const h = this.#heap;
    const top = h[0];
    const last = h.pop();
    if (h.length) {
      h[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < h.length && this.#less(l, m)) m = l;
        if (r < h.length && this.#less(r, m)) m = r;
        if (m === i) break;
        [h[i], h[m]] = [h[m], h[i]];
        i = m;
      }
    }
    return top;
  }
}

/** The cheapest way (in whole tenths of a second) from any source to any target: its
 * nodes, or null. Nodes are taken in order of their distance and, at one distance, of
 * their IDs; each keeps the way it was first reached by at its least distance. */
export function shortest(graph, sources, targets, accessible = false) {
  const want = new Set(targets);
  const dist = new Map(), prev = new Map(), done = new Set();
  const queue = new Queue();
  for (const s of sources) {
    dist.set(s, 0);
    prev.set(s, null);
  }
  for (const s of [...new Set(sources)].sort(compare)) queue.push([0, s]);
  while (queue.size) {
    const [d, u] = queue.pop();
    if (done.has(u)) continue;
    done.add(u);
    if (want.has(u)) {
      const path = [u];
      while (prev.get(path[path.length - 1]) !== null) path.push(prev.get(path[path.length - 1]));
      return path.reverse();
    }
    for (const [v, e] of graph.adjacent.get(u)) {
      if (accessible && !e.accessible) continue;
      const nd = d + Math.round(e.cost * 10);
      if (!dist.has(v) || nd < dist.get(v)) {
        dist.set(v, nd);
        prev.set(v, u);
        queue.push([nd, v]);
      }
    }
  }
  return null;
}

const graphs = new WeakMap(); // navigation.json → its Graph, made once

function networkOf(pkg) {
  if (pkg instanceof Graph) return { graph: pkg, items: null };
  const nav = pkg && "navigation" in pkg && !("nodes" in pkg) ? pkg.navigation : pkg;
  if (!nav || !Array.isArray(nav.nodes)) {
    throw new Error("This package has no walking network (navigation.json, format 0.8).");
  }
  let graph = graphs.get(nav);
  if (!graph) graphs.set(nav, (graph = new Graph(nav)));
  return { graph, items: Array.isArray(pkg?.items) ? pkg.items : null };
}

const forward = (e, a) => (e.from === a ? e.path : [...e.path].reverse());
const pointOf = (n) => [n.local.x_m, n.local.y_m];

/**
 * The way from `from` to `to` (a node's, space's, zone's or item's ID): its nodes, its
 * legs (the walking on one floor: the line to draw), its changes of floor, its length
 * (metres) and time (seconds), and its steps. On lifts and ramps alone with
 * `accessible`. Null when there is no way; throws for an ID the network does not know.
 *
 * @param pkg a package (as loadPackage or readPackage give it: its `navigation`, and its
 *   `items` for a way to an item), navigation.json itself, or a Graph
 * @param {string} from
 * @param {string} to
 * @param {{ accessible?: boolean, items?: Iterable<object> | Map<string, object> }} [options]
 */
export function route(pkg, from, to, { accessible = false, items = null } = {}) {
  const { graph, items: own } = networkOf(pkg);
  const list = items ?? own;
  const byId = list instanceof Map ? list : list ? new Map([...list].map((f) => [f.id, f])) : null;
  const path = shortest(graph, graph.ends(from, byId), graph.ends(to, byId), accessible);
  if (path === null) return null;
  const edge = (a, b) => graph.adjacent.get(a).find(([v]) => v === b)[1];
  const edges = path.slice(1).map((b, i) => edge(path[i], b));
  // runs on one floor, between the rides from floor to floor
  const runs = [[]], rides = [];
  edges.forEach((e, i) => {
    if (e.kind === "walk" || e.kind === "door") lastOf(runs).push(i);
    else if (rides.length && lastOf(lastOf(rides)) === i - 1 && edges[lastOf(rides)[0]].kind === e.kind
      && lastOf(runs).length === 0) lastOf(rides).push(i);
    else {
      rides.push([i]);
      runs.push([]);
    }
  });
  const starts = [0, ...rides.map((r) => lastOf(r) + 1)]; // the node each run starts at
  const legs = runs.map((run, k) => {
    const at = starts[k];
    const points = [pointOf(graph.nodes.get(path[at]))];
    let metres = 0;
    for (const i of run) {
      for (const p of forward(edges[i], path[i]).slice(1)) if (!same(p, points[points.length - 1])) points.push([p[0], p[1]]);
      metres += edges[i].length_m;
    }
    return { floor_id: graph.nodes.get(path[at]).floor_id, points, metres: round2(metres) };
  });
  const changes = rides.map((ride) => {
    const a = path[ride[0]], b = path[lastOf(ride) + 1];
    const fa = graph.nodes.get(a).floor_id, fb = graph.nodes.get(b).floor_id;
    const apart = (graph.order.get(fb) ?? 0) - (graph.order.get(fa) ?? 0);
    return { by: edges[ride[0]].kind, from_floor_id: fa, to_floor_id: fb, from_node: a, to_node: b,
      floors: Math.abs(apart), direction: apart > 0 ? "up" : "down" };
  });
  let metres = 0, seconds = 0;
  for (const e of edges) {
    metres += e.length_m;
    seconds += e.seconds;
  }
  return { from, to, accessible, nodes: path, metres: round2(metres), seconds: round2(seconds), legs, changes,
    steps: steps(graph, path, edges, runs, starts, changes) };
}

const START = { kiosk: "Start at the kiosk in ", entrance: "Start at the entrance into ", room: "Start in ",
  approach: "Start in ", door: "Start at the door of " };

/** What to tell a person, step by step: each a kind, its values and its text in English
 * (a system words them in its own language from the kind and values). */
function steps(graph, path, edges, runs, starts, changes) {
  const first = graph.nodes.get(path[0]);
  const here = graph.placeOf(path[0]);
  const text = RIDES.has(first.kind) ? `Start at the ${first.kind}` : (START[first.kind] ?? "Start in ") + graph.label(here);
  const out = [{ kind: "start", node: path[0], node_kind: first.kind, place: here, floor_id: first.floor_id, text }];
  const goal = graph.placeOf(path[path.length - 1]);
  runs.forEach((run, n) => {
    const at = starts[n];
    let metres = 0;
    const by = new Map();
    for (const i of run) {
      const e = edges[i];
      metres += e.length_m;
      const place = e.zone_id || e.space_id;
      if (place) by.set(place, (by.get(place) ?? 0) + e.length_m);
    }
    const last = n === runs.length - 1;
    const m = wholeMetres(metres);
    if (run.length && m > 0) {
      let along = null;
      for (const [place, length] of by) {
        if (along === null || length > by.get(along) || (length === by.get(along) && place < along)) along = place;
      }
      const to = last ? "destination" : changes[n].by;
      const toText = last ? graph.label(goal) : `the ${to}`;
      const kind = (along !== null && graph.places.get(along)?.type) || "";
      const text = along === null || (last && along === goal) ? `Walk ${m} m to ${toText}`
        : `Walk ${m} m ${THROUGH.has(kind) ? "along" : "through"} ${graph.label(along)} to ${toText}`;
      out.push({ kind: "walk", floor_id: graph.nodes.get(path[at]).floor_id, metres: m, along, to,
        place: last ? goal : null, text });
    }
    if (!last) {
      const c = changes[n];
      const name = graph.floors.get(c.to_floor_id)?.name || c.to_floor_id;
      out.push({ kind: "take", by: c.by, from_floor_id: c.from_floor_id, to_floor_id: c.to_floor_id, floors: c.floors,
        direction: c.direction, text: `Take the ${c.by} ${c.direction} to ${name}` });
    }
  });
  const side = path.length === 1 ? "here" : sideOf(graph, path, edges, runs[runs.length - 1], starts[starts.length - 1], goal);
  const label = graph.label(goal);
  const said = { here: `You are at ${label}`, ahead: `${capital(label)} is ahead`,
    left: `${capital(label)} is on your left`, right: `${capital(label)} is on your right` }[side];
  out.push({ kind: "arrive", place: goal, floor_id: graph.nodes.get(path[path.length - 1]).floor_id, side, text: said });
  return out;
}

/** Which side the destination's door is on, as a person walks to it: from the way
 * they walk before turning to it (over WALK_BACK_M) and the way into the room. */
function sideOf(graph, path, edges, run, at, goal) {
  const end = at + run.length; // the run's last node
  for (let j = end - 1; j > at; j--) {
    if (!DOORS.has(graph.nodes.get(path[j]).kind)) continue;
    if (graph.placeOf(path[j + 1]) !== goal || graph.placeOf(path[j - 1]) === goal) return "ahead";
    const points = [pointOf(graph.nodes.get(path[at]))];
    for (let i = at; i < j - 1; i++) {
      for (const p of forward(edges[i], path[i]).slice(1)) if (!same(p, points[points.length - 1])) points.push(p);
    }
    let back = WALK_BACK_M, q = points[0];
    for (let i = points.length - 1; i > 0; i--) {
      const [x1, y1] = points[i], [x0, y0] = points[i - 1];
      const dx = x0 - x1, dy = y0 - y1;
      const seg = Math.sqrt(dx * dx + dy * dy);
      if (seg >= back) {
        q = [x1 + dx * (back / seg), y1 + dy * (back / seg)];
        break;
      }
      back -= seg;
      q = points[i - 1];
    }
    const last = points[points.length - 1];
    const wx = last[0] - q[0], wy = last[1] - q[1];
    const door = graph.nodes.get(path[j]).local, inside = graph.nodes.get(path[j + 1]).local;
    const rx = inside.x_m - door.x_m, ry = inside.y_m - door.y_m;
    if (wx === 0 && wy === 0) return "ahead";
    const dot = wx * rx + wy * ry;
    const cross = wx * ry - wy * rx;
    if (dot > 0 && Math.abs(cross) <= 0.5 * dot) return "ahead";
    return cross > 0 ? "left" : cross < 0 ? "right" : "ahead";
  }
  return "ahead";
}
