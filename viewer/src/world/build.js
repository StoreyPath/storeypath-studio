// The geometry of a floor for the 3D world, built from the package: in the
// browser (world.js) or ahead of time in Node (viewer/world/bake.mjs, which writes
// it as binary glTF for a package's world/ folder). No DOM here: three.js's maths
// and geometry only, so both build the same floor.
//
// A floor comes out as a few merged pieces, one per material and use (the walls'
// faces, the floor finishes of offices, the door leaves, …), so a floor of 800
// rooms is drawn in a few dozen draw calls. The floor finishes and the x-ray
// volumes carry their room's index (`_room`; `rooms` lists the IDs), to pick a room
// by its triangles.
//
// Local metres: x east, y up, z south, from the building's origin (originOf). A
// plan point is [x, n] (n north, so z = −n).

import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

/** This builder's version: a floor pre-built by another (before 2, without its
 * items) is built again. */
export const BUILDER = 2;

/** What shapes the geometry; a world built with others builds its floors itself. */
export const GEOMETRY = {
  slab: 0.22, // m, floor slab thickness
  doorHead: 2.1, // m above the floor
  windowSill: 0.9,
  windowHead: 2.2,
  wallThickness: 0.2, // when the package does not say
  cutHeight: 1.25, // walls are cut to this in the cutaway view
};
const OPEN_SPAN = 2.6; // m: wider ways through are open-plan joins, with no wall above
const LEAF = 0.045; // m, a door leaf's thickness
const CASING = 0.06; // m, the frame round a door
const WINDOW_FRAME = 0.05; // m, the frame round the glass
const PANE = 1.0; // m: a mullion about this often across a window
const DOUBLE_DOOR = 1.3; // m: a door wider than this, drawn without its swings, has two leaves
const OUTDOOR = new Set(["terrace", "balcony"]); // open to the sky, behind parapets
const PARAPET = 1.1; // m, when the package gives no parapet height
const ITEM_COLOR = "#8a8a8a"; // an item whose type the package does not describe
const WALL_ITEM = 1.2; // m, the bottom of an item on a wall, when the package does not say

/** The pieces of a floor: the material each is drawn with (a name in materials.js;
 * "floor" and "volume" by the space type) and when it shows: "full", not in the
 * cutaway view; "cut", only in it; "walk", on the walker's floor; "xray", in the
 * x-ray view; otherwise always. */
export const PIECES = {
  slab: { material: "slab" },
  wall: { material: "wall", view: "full" }, // the walls' faces, full height
  wallTop: { material: "wallTop", view: "full" }, // their tops (and undersides)
  wallLow: { material: "wall", view: "cut" }, // the walls' faces, cut low
  wallCut: { material: "wallCut", view: "cut" }, // where they are cut
  parapet: { material: "wall", view: "full" },
  parapetTop: { material: "wallTop", view: "full" },
  parapetLow: { material: "wall", view: "cut" },
  parapetCut: { material: "wallCut", view: "cut" },
  heads: { material: "wallPlain", view: "full" }, // over doors and windows
  sills: { material: "wallPlain" },
  glass: { material: "glass", view: "full" },
  frame: { material: "frame", view: "full" }, // round the glass
  doorFrame: { material: "doorFrame", view: "full" },
  door: { material: "door", view: "full" }, // the leaves
  ceiling: { material: "ceiling", view: "walk" },
  floor: { material: "floor" }, // floor:<type>, each space type's finish
  volume: { material: "volume", view: "xray" }, // volume:<type>
  // the furniture and equipment (buildItems), coloured by vertex, kept apart from the
  // rest: those standing below the cut, and those above it (on a wall, under the
  // ceiling); each detailed, or light (a box each) for a whole building
  items: { material: "item" },
  "items:high": { material: "item", view: "full" },
  "items:light": { material: "item", form: "light" },
  "items:light:high": { material: "item", view: "full", form: "light" },
};
const TEXTURED = new Set(["floor", "wall"]); // the materials with a texture: the rest need no uv
// the pieces cut low (cutPieces), from the full ones
const CUT_LOW = { wall: "wallLow", wallTop: "wallCut", parapet: "parapetLow", parapetTop: "parapetCut" };

/** Distance from point ``p`` to the segment ``a``–``b`` (plan meters). */
function distanceToSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

/** Point in ring (ray casting), ring as [[x, n], …]. */
export function inside(ring, x, n) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, ni] = ring[i], [xj, nj] = ring[j];
    if ((ni > n) !== (nj > n) && x < ((xj - xi) * (n - ni)) / (nj - ni) + xi) hit = !hit;
  }
  return hit;
}

/** A building's local origin: the middle of its floors (or of the building, with
 * none), and the metres in a degree of longitude and latitude there. */
export function originOf(pkg, buildingId) {
  const floors = pkg.floorsOf(buildingId);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const visit = (c) => {
    if (typeof c[0] === "number") {
      x0 = Math.min(x0, c[0]); y0 = Math.min(y0, c[1]); x1 = Math.max(x1, c[0]); y1 = Math.max(y1, c[1]);
    } else c.forEach(visit);
  };
  for (const f of floors.length ? floors : [pkg.get(buildingId)]) if (f?.geometry) visit(f.geometry.coordinates);
  const lon = Number.isFinite(x0) ? (x0 + x1) / 2 : 0;
  const lat = Number.isFinite(y0) ? (y0 + y1) / 2 : 0;
  return { lon, lat, kx: 111320 * Math.cos((lat * Math.PI) / 180), ky: 110540 };
}

/** Longitude/latitude → local meters [x east, n north]. */
export function toLocal(origin, [lon, lat]) {
  return [(lon - origin.lon) * origin.kx, (lat - origin.lat) * origin.ky];
}

/** A GeoJSON (Multi)Polygon → polygons as rings of [x, n] in local meters. */
function polygonsOf(geometry, origin) {
  if (!geometry) return [];
  const polys = geometry.type === "Polygon" ? [geometry.coordinates]
    : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
  return polys.map((rings) => rings.map((ring) => ring.map((c) => toLocal(origin, c))));
}

/** A polygon's rings as a shape, or null. A ring too small to have an inside (a
 * hole a few millimetres wide, collapsed by the package's 1 cm rounding) is
 * left out: the triangulator fails on it, and takes the whole floor with it. */
function shapeOf(rings) {
  const toPts = (ring) => ring.map(([x, n]) => new THREE.Vector2(x, n));
  const usable = (pts) => pts.length >= 3 && Math.abs(THREE.ShapeUtils.area(pts)) > 1e-4;
  const outer = toPts(rings[0]);
  if (!usable(outer)) return null;
  const shape = new THREE.Shape(outer);
  for (const hole of rings.slice(1)) {
    const pts = toPts(hole);
    if (usable(pts)) shape.holes.push(new THREE.Path(pts));
  }
  return shape;
}

/** Shapes for ``polygons``, built with ``make``; when that fails on the whole set,
 * with only the shapes it works on, one bad outline does not hide the rest. */
function shaped(polygons, make) {
  const shapes = polygons.map(shapeOf).filter(Boolean);
  if (!shapes.length) return new THREE.BufferGeometry();
  try {
    return make(shapes);
  } catch {
    const good = shapes.filter((shape) => {
      try {
        make([shape]).dispose();
        return true;
      } catch {
        return false;
      }
    });
    return good.length ? make(good) : new THREE.BufferGeometry();
  }
}

/** Polygons extruded upward from ``y`` by ``depth``: group 0 the top and bottom
 * faces, group 1 the sides. */
function extrude(polygons, depth, y) {
  if (!polygons.length) return null;
  const g = shaped(polygons, (shapes) => new THREE.ExtrudeGeometry(shapes, { depth, bevelEnabled: false, curveSegments: 1 }));
  g.rotateX(-Math.PI / 2);
  g.translate(0, y, 0);
  return g;
}

/** Polygons laid flat at height ``y``, facing up. */
export function flat(polygons, y) {
  const g = shaped(polygons, (shapes) => new THREE.ShapeGeometry(shapes, 1));
  g.rotateX(-Math.PI / 2);
  g.translate(0, y, 0);
  return g;
}

/** An extrusion's [top and bottom faces, sides] as geometries of their own. */
function capsAndSides(g) {
  if (!g || !g.groups.length) return [g, null];
  const parts = [0, 1].map((material) => {
    const ranges = g.groups.filter((r) => r.materialIndex === material);
    const part = new THREE.BufferGeometry();
    for (const [name, attr] of Object.entries(g.attributes)) {
      const size = attr.itemSize;
      const array = new attr.array.constructor(ranges.reduce((n, r) => n + r.count, 0) * size);
      let at = 0;
      for (const r of ranges) {
        array.set(attr.array.subarray(r.start * size, (r.start + r.count) * size), at);
        at += r.count * size;
      }
      part.setAttribute(name, new THREE.BufferAttribute(array, size));
    }
    return part;
  });
  g.dispose();
  return parts;
}

/** What a floor holds, in local metres, with no geometry: its rooms, walls and ways
 * through. Cheap: a world that has the floor pre-built needs only this. */
export function planFloor(pkg, floor, origin, options = {}) {
  const o = { ...GEOMETRY, ...options };
  const local = (c) => toLocal(origin, c);
  const polygons = (g) => polygonsOf(g, origin);
  const props = floor.properties;
  const e = props.elevation;
  const wallHeight = Math.max(2.4, props.height - o.slab);
  const plan = {
    id: floor.id, elevation: e, height: props.height, ordinal: props.ordinal, wallHeight,
    parapetHeight: Math.min(props.parapet_height_m || PARAPET, wallHeight),
    thickness: props.wall_thickness_m || o.wallThickness,
    outline: polygons(floor.geometry),
    rooms: [], // the spaces: walls stand on their edges
    units: [], // what is used: the zones of a divided space, else the space
    bounds: [Infinity, Infinity, -Infinity, -Infinity], // x0, z0, x1, z1
  };

  // One volume per space; a space divided into zones is used through them: each has
  // its own floor, highlight and label, and no wall.
  for (const space of pkg.spacesOn(floor.id)) {
    const sp = space.properties;
    const rings = polygons(space.geometry);
    if (!rings.length) continue;
    const room = { id: space.id, type: sp.type, rings, outdoor: sp.outdoor ?? OUTDOOR.has(sp.type), units: [] };
    plan.rooms.push(room); // a glazed veranda is not outdoor: older packages say by type
    const zones = pkg.zonesOf(space.id);
    for (const unit of zones.length ? zones : [space]) {
      const p = unit.properties;
      const polys = unit === space ? rings : polygons(unit.geometry);
      if (!polys.length) continue;
      const [lx, ln] = p.display_point ? local(p.display_point) : polys[0][0][0];
      let x0 = Infinity, n0 = Infinity, x1 = -Infinity, n1 = -Infinity;
      for (const [x, n] of polys.flat(2)) {
        x0 = Math.min(x0, x); n0 = Math.min(n0, n); x1 = Math.max(x1, x); n1 = Math.max(n1, n);
      }
      const u = { id: unit.id, space: space.id, type: p.type, name: p.name, number: p.number,
        tucked: Boolean(p.hidden || p.ignored), rings: polys, label: [lx, ln],
        centre: { x: lx, z: -ln }, size: Math.max(x1 - x0, n1 - n0, 2) };
      plan.units.push(u);
      room.units.push(u);
    }
  }

  // the walls, rising to the ceiling; drawn along the rooms' edges when the package
  // has none (older packages, drawings without wall layers)
  plan.walls = polygons(props.walls);
  if (plan.walls.length) {
    plan.ways = pkg.openings
      .filter((x) => x.properties.floor_id === floor.id && x.properties.span && !x.properties.ignored) // deleted in review: left out
      .map((x) => {
        const [a, b] = x.properties.span.map(local);
        const leaves = (x.properties.swings || []).map((leaf) => leaf.map(local));
        return { a, b, type: x.properties.type, connects: x.properties.connects || [], leaves,
          sill: x.properties.sill_m ?? null, height: x.properties.height_m ?? null };
      });
  } else {
    plan.thickness = props.wall_thickness_m || 0.12;
    const fallback = roomWalls(pkg, floor.id, plan.rooms, plan.thickness, origin); // spaces, not zones: no wall between zones
    plan.walls = fallback.walls;
    plan.ways = fallback.gaps;
  }
  // the parapets: the low walls around terraces, balconies and the roof
  plan.parapets = polygons(props.parapets);
  plan.wallRings = plan.walls.flat(1).concat(plan.parapets.flat(1));

  // the furniture and equipment (format 0.6): where each stands, the way its front
  // faces (x east, n north), its size, how high its bottom is and its colour; and
  // the edges of those on the floor, which the walker bumps into
  plan.items = [];
  plan.itemObstacles = [];
  for (const item of pkg.itemsOn?.(floor.id) ?? []) {
    const p = item.properties;
    if (!p.display_point) continue;
    const [x, n] = local(p.display_point);
    const h = ((p.heading ?? 0) * Math.PI) / 180;
    const it = { id: item.id, type: p.type, mount: p.mount ?? "floor", x, n, fx: Math.sin(h), fn: Math.cos(h),
      width: p.width_m || 1, depth: p.depth_m || 0.6, height: p.height_m || 0.75,
      color: pkg.itemType?.(p.type)?.color ?? ITEM_COLOR };
    it.y = it.mount === "ceiling" ? wallHeight - it.height - 0.01 : p.elevation_m ?? (it.mount === "wall" ? WALL_ITEM : 0);
    plan.items.push(it);
    if (it.mount !== "floor") continue;
    const c = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => itemPoint(it, (a * it.width) / 2, (b * it.depth) / 2));
    c.forEach((q, i) => plan.itemObstacles.push([q[0], q[1], c[(i + 1) % 4][0], c[(i + 1) % 4][1]]));
  }

  const b = plan.bounds;
  for (const rings of [...plan.outline, ...plan.walls, ...plan.units.flatMap((u) => u.rings)]) {
    for (const [x, n] of rings[0]) {
      b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], -n); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], -n);
    }
  }
  return plan;
}

/** Thin walls along the edges of the rooms, open where the doors and windows are. */
function roomWalls(pkg, floorId, rooms, thickness, origin) {
  const openings = pkg.openings
    .filter((o) => o.properties.floor_id === floorId && o.geometry?.type === "Point")
    .map((o) => ({ p: toLocal(origin, o.geometry.coordinates), type: o.properties.type,
      w: Math.min(Math.max(o.properties.width_m || 0.9, 0.7), 2.4) }));
  const seen = new Set();
  const pieces = [], gaps = [];
  for (const s of rooms) {
    for (const ring of s.rings.flat(1)) {
      for (let i = 0; i + 1 < ring.length; i++) {
        const a = ring[i], b = ring[i + 1];
        const key = [a, b].map((q) => `${q[0].toFixed(2)},${q[1].toFixed(2)}`).sort().join("|");
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
        if (seen.has(key) || len < 0.05) continue;
        seen.add(key);
        const ux = (b[0] - a[0]) / len, un = (b[1] - a[1]) / len;
        const at = (t) => [a[0] + ux * t, a[1] + un * t];
        const cuts = [];
        for (const o of openings) {
          const t = (o.p[0] - a[0]) * ux + (o.p[1] - a[1]) * un;
          const off = Math.abs((o.p[1] - a[1]) * ux - (o.p[0] - a[0]) * un);
          if (off < 0.45 && t > 0 && t < len) cuts.push([Math.max(0, t - o.w / 2), Math.min(len, t + o.w / 2), o.type]);
        }
        cuts.sort((x, y) => x[0] - y[0]);
        let done = 0;
        for (const [c0, c1, type] of cuts) {
          if (c0 > done + 0.05) pieces.push([at(done), at(c0)]);
          if (c1 > Math.max(c0, done)) gaps.push({ a: at(Math.max(c0, done)), b: at(c1), type });
          done = Math.max(done, c1);
        }
        if (len > done + 0.05) pieces.push([at(done), at(len)]);
      }
    }
  }
  const h = thickness / 2;
  const walls = pieces.map(([p, q]) => {
    const len = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1;
    const nx = (-(q[1] - p[1]) / len) * h, nn = ((q[0] - p[0]) / len) * h;
    const ring = [[p[0] + nx, p[1] + nn], [q[0] + nx, q[1] + nn], [q[0] - nx, q[1] - nn], [p[0] - nx, p[1] - nn]];
    return [[...ring, ring[0]]];
  });
  return { walls, gaps };
}

/** A floor's geometry, from its plan: ``pieces``, each { name, material, view,
 * type, hidden, geometry } (PIECES; a piece of hidden or ignored rooms is one of
 * its own, ``hidden``); ``rooms``, the IDs the ``_room`` attribute indexes; and
 * ``obstacles``, what the walker bumps into: [[x1, z1, x2, z2], …]. */
export function buildPieces(plan, options = {}) {
  const o = { ...GEOMETRY, ...options };
  const { elevation: e, wallHeight, parapetHeight, thickness, walls, parapets } = plan;
  const sets = new Map(); // name → { piece, geometries }
  const put = (key, geometry, { type, hidden = false } = {}) => {
    if (!geometry?.attributes.position?.count) return geometry?.dispose();
    const name = [key, type, hidden && "hidden"].filter(Boolean).join(":");
    if (!sets.has(name)) {
      sets.set(name, { piece: { name, ...PIECES[key], ...(type ? { type } : {}), ...(hidden ? { hidden } : {}) }, geometries: [] });
    }
    sets.get(name).geometries.push(geometry);
  };
  const rooms = [], indices = new Map();
  const Index = plan.rooms.length + plan.units.length > 65535 ? Uint32Array : Uint16Array;
  const room = (geometry, id) => { // each vertex says whose it is
    if (!geometry.attributes.position) return geometry;
    if (!indices.has(id)) indices.set(id, rooms.push(id) - 1);
    geometry.setAttribute("_room", new THREE.BufferAttribute(new Index(geometry.attributes.position.count).fill(indices.get(id)), 1));
    return geometry;
  };

  // the slab
  const slab = extrude(plan.outline, o.slab, e - o.slab);
  slab?.clearGroups();
  put("slab", slab);

  // the rooms: an x-ray volume each, and a floor finish for each of their units
  const roofed = []; // under the ceiling: every room but terraces and balconies
  for (const r of plan.rooms) {
    if (!r.outdoor) roofed.push(...r.rings);
    const volume = extrude(r.rings, (r.outdoor ? parapetHeight : wallHeight) - 0.05, e + 0.01);
    volume.clearGroups();
    put("volume", room(volume, r.id), { type: r.type, hidden: r.units.length > 0 && r.units.every((u) => u.tucked) });
    for (const u of r.units) {
      if (u.type === "open_to_below") continue;
      put("floor", room(flat(u.rings, e + 0.004), u.id), { type: u.type, hidden: u.tucked });
    }
  }

  // the ceiling, seen only when walking (it casts no shadow: rooms stay sunlit); none
  // over terraces and balconies
  const ceilingPolys = plan.units.length ? roofed : plan.outline;
  if (ceilingPolys.length) put("ceiling", flat(ceilingPolys, e + wallHeight));

  // the walls and parapets, full height (cut low by cutPieces)
  const extruded = (polys, height, key, top) => {
    const [caps, sides] = capsAndSides(extrude(polys, height, e));
    put(key, sides);
    put(top, caps);
  };
  extruded(walls, wallHeight, "wall", "wallTop");
  extruded(parapets, parapetHeight, "parapet", "parapetTop");

  // Openings in a parapet (no full wall at either side) get no head, sill or glass:
  // a full wall ends at one of its jambs (its middle is far from any wall when wide).
  const fullSegments = [];
  for (const ring of walls.flat(1)) {
    for (let i = 0; i + 1 < ring.length; i++) fullSegments.push([ring[i], ring[i + 1]]);
  }
  const inFullWall = ({ a, b }) => !parapets.length || [a, b].some((jamb) => fullSegments.some(([p, q]) =>
    distanceToSegment(jamb, p, q) <= thickness + 0.3));

  // door heads, frames and leaves; window sills, heads, frames and glass
  // a box from plan point p to q, y0 to y1 above the floor, depth across
  const piece = (p, q, y0, y1, depth) => {
    const g = new THREE.BoxGeometry(Math.hypot(q[0] - p[0], q[1] - p[1]), y1 - y0, depth);
    g.rotateY(Math.atan2(q[1] - p[1], q[0] - p[0]));
    g.translate((p[0] + q[0]) / 2, e + (y0 + y1) / 2, -(p[1] + q[1]) / 2);
    return g;
  };
  const obstacles = [];
  for (const ring of plan.wallRings) {
    for (let i = 0; i + 1 < ring.length; i++) {
      obstacles.push([ring[i][0], -ring[i][1], ring[i + 1][0], -ring[i + 1][1]]);
    }
  }
  for (const way of plan.ways) {
    const { a, b, type } = way;
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (len < 0.3 || !inFullWall(way)) continue;
    const box = (y0, y1, depth) => piece(a, b, y0, y1, depth);
    const ux = (b[0] - a[0]) / len, un = (b[1] - a[1]) / len;
    const at = (t) => [a[0] + ux * t, a[1] + un * t];
    // sizes from the drawing's schedule where it gives them; a window taller than the
    // floor (through two storeys) stops at this floor's ceiling
    const ceiling = wallHeight - 0.02;
    if (type === "window") {
      const sill = Math.min(Math.max(way.sill ?? o.windowSill, 0), ceiling - 0.2);
      const head = Math.min(way.height !== null && way.height !== undefined ? sill + way.height : o.windowHead, ceiling);
      if (sill > 0.01) put("sills", box(0, sill, thickness));
      if (head < wallHeight - 0.01) put("heads", box(head, wallHeight, thickness));
      put("glass", box(sill, head, 0.02));
      // the frame: along the sill and the head, at each side, and a mullion about
      // every metre between
      const f = WINDOW_FRAME, depth = Math.min(thickness, 0.09);
      put("frame", piece(a, b, sill, sill + f, depth));
      put("frame", piece(a, b, head - f, head, depth));
      const panes = Math.max(1, Math.round(len / PANE));
      for (let k = 0; k <= panes; k++) {
        const t = Math.min(Math.max((k * len) / panes, f / 2), len - f / 2);
        put("frame", piece(at(t - f / 2), at(t + f / 2), sill, head, depth));
      }
      obstacles.push([a[0], -a[1], b[0], -b[1]]); // you cannot walk through a window
    } else if (type === "door") {
      const top = Math.min(way.height ?? o.doorHead, ceiling);
      if (top < wallHeight - 0.01) put("heads", box(top, wallHeight, thickness));
      // the frame: a jamb each side and a head, standing a little proud of the wall
      const c = Math.min(CASING, len / 4), depth = thickness + 0.03;
      put("doorFrame", piece(a, at(c), 0, top, depth));
      put("doorFrame", piece(at(len - c), b, 0, top, depth));
      put("doorFrame", piece(a, b, top - c, top, depth));
      // the leaves, open as the plan draws them; without the swings, open into the
      // room it serves, one leaf or two
      let open = way.leaves || [];
      if (!open.length) {
        const mid = at(len / 2), side = [-un, ux];
        const into = plan.units.filter((s) => way.connects?.includes(s.id))
          .some((s) => s.rings.some((r) => inside(r[0], mid[0] + side[0] * 0.5, mid[1] + side[1] * 0.5)));
        const k = into ? 1 : -1;
        const hinges = len > DOUBLE_DOOR ? [[0, len / 2], [len, len / 2]] : [[0, len]];
        open = hinges.map(([t, w]) => {
          const h = at(t);
          return [h, [h[0] + side[0] * k * w, h[1] + side[1] * k * w]];
        });
      }
      for (const [h, q] of open) {
        const reach = Math.hypot(q[0] - h[0], q[1] - h[1]);
        if (reach < 0.3) continue;
        const w = Math.min(reach, len) - c, dx = (q[0] - h[0]) / reach, dn = (q[1] - h[1]) / reach;
        const from = [h[0] + dx * c, h[1] + dn * c], to = [h[0] + dx * (c + w), h[1] + dn * (c + w)];
        put("door", piece(from, to, 0.01, top - c, LEAF));
        obstacles.push([from[0], -from[1], to[0], -to[1]]); // an open leaf stands in the way
      }
    } else if (len <= OPEN_SPAN) { // a doorway: a way through with no door
      put("heads", box(o.doorHead, wallHeight, thickness));
    }
  }

  // one geometry per piece; texture coordinates only where there is a texture
  const pieces = [];
  for (const { piece: p, geometries } of sets.values()) {
    const geometry = geometries.length === 1 ? geometries[0] : mergeGeometries(geometries, false);
    if (geometries.length > 1) geometries.forEach((g) => g.dispose());
    if (!TEXTURED.has(p.material)) geometry.deleteAttribute("uv");
    pieces.push({ ...p, geometry: upright(geometry) });
  }
  return { pieces, rooms, obstacles };
}

/** The walls and parapets cut low, for the cutaway view, from the full ones: each
 * vertex above ``cutHeight`` comes down to it, which is the extrusion that high
 * (an extrusion's sides have v = 1 − height), without building it again. */
export function cutPieces(pieces, elevation, options = {}) {
  const y = elevation + (options.cutHeight ?? GEOMETRY.cutHeight);
  const out = [];
  for (const p of pieces) {
    const name = CUT_LOW[p.name];
    if (!name) continue;
    const geometry = p.geometry.clone();
    const position = geometry.getAttribute("position"), uv = geometry.getAttribute("uv");
    for (let i = 0; i < position.count; i++) {
      if (position.getY(i) <= y) continue;
      position.setY(i, y);
      uv?.setY(i, 1 - (y - elevation));
    }
    out.push({ name, ...PIECES[name], geometry });
  }
  return out;
}

/** A triangle with no area (the triangulator leaves a few) has no normal: it points
 * up, as glTF wants every normal a unit long. */
function upright(geometry) {
  const n = geometry.getAttribute("normal");
  for (let i = 0; n && i < n.count; i++) {
    if (n.getX(i) === 0 && n.getY(i) === 0 && n.getZ(i) === 0) n.setY(i, 1);
  }
  return geometry;
}

// ---- furniture and equipment ---------------------------------------------------

/** A point of an item, ``across`` its width and ``ahead`` towards its front (metres
 * from its middle), in local [x, z]. */
export function itemPoint(it, across, ahead) {
  return [it.x - across * it.fn + ahead * it.fx, -(it.n + across * it.fx + ahead * it.fn)];
}

/** Boxes and discs written straight into arrays, each with its corners shared by its
 * faces: no normals (the material is flat-shaded), no texture, a colour and an item
 * index a vertex; so a floor of thousands of desks is built in milliseconds and kept
 * small in a pre-built file. */
class Shapes {
  constructor() {
    this.position = [];
    this.color = [];
    this.item = [];
    this.index = [];
  }

  /** The vertex of an item at (across, up, ahead) in its own frame (ahead: towards
   * its front), in its colour. */
  #vertex(it, k, across, up, ahead, rgb) {
    const [x, z] = itemPoint(it, across, ahead);
    this.position.push(x, it.base + up, z);
    this.color.push(rgb[0], rgb[1], rgb[2]);
    this.item.push(k);
  }

  /** A box across x0–x1, up y0–y1 and ahead z0–z1 of item ``k`` (``it``). */
  box(it, k, [x0, x1], [y0, y1], [z0, z1], rgb) {
    const at = this.position.length / 3;
    for (let i = 0; i < 8; i++) this.#vertex(it, k, i & 1 ? x1 : x0, i & 2 ? y1 : y0, i & 4 ? z1 : z0, rgb);
    for (const v of BOX_FACES) this.index.push(at + v);
  }

  /** A disc of radius ``r`` from y0 to y1, its middle at the item's. */
  disc(it, k, r, [y0, y1], rgb, sides = 16) {
    const at = this.position.length / 3;
    for (let i = 0; i < sides; i++) {
      const a = (i / sides) * Math.PI * 2;
      this.#vertex(it, k, r * Math.cos(a), y0, r * Math.sin(a), rgb);
      this.#vertex(it, k, r * Math.cos(a), y1, r * Math.sin(a), rgb);
    }
    for (let i = 0; i < sides; i++) {
      const j = (i + 1) % sides;
      this.index.push(at + 2 * i, at + 2 * i + 1, at + 2 * j + 1, at + 2 * i, at + 2 * j + 1, at + 2 * j);
      if (i > 1) this.index.push(at, at + 2 * (i - 1), at + 2 * i, at + 1, at + 2 * i + 1, at + 2 * (i - 1) + 1);
    }
  }

  geometry(Index) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.position, 3));
    g.setAttribute("color", new THREE.BufferAttribute(Uint8Array.from(this.color), 3, true));
    g.setAttribute("_item", new THREE.BufferAttribute(Index.from(this.item), 1));
    g.setIndex(this.position.length / 3 > 65535 ? new THREE.Uint32BufferAttribute(this.index, 1)
      : new THREE.Uint16BufferAttribute(this.index, 1));
    return g;
  }
}
// a box's corners: bit 1 across, bit 2 up, bit 4 ahead; its faces outwards
const BOX_FACES = [1, 3, 7, 1, 7, 5, 0, 4, 6, 0, 6, 2, 2, 6, 7, 2, 7, 3, 0, 1, 5, 0, 5, 4, 4, 5, 7, 4, 7, 6, 0, 2, 3, 0, 3, 1];

/** A colour as the vertex colours keep it: 0–255 in linear light. */
function rgb(color, { dark = 0, light = 0 } = {}) {
  const c = new THREE.Color(color);
  if (dark) c.multiplyScalar(1 - dark);
  if (light) c.lerp(new THREE.Color(1, 1, 1), light);
  return [c.r, c.g, c.b].map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255));
}
const CHAIR = "#3d4048";
const SCREEN = "#0e1117";

/** How each kind of item is drawn, by its type code's first part (DESK-MANAGER is a
 * desk); others by how they are mounted. Each gets the item (w, d, h: its size) and
 * draws it in its own frame, its front ahead. */
const DRAW = {
  // a top on two end panels with a modesty panel at the back, and a chair
  DESK(s, it, k, c) {
    const { width: w, depth: d, height: h } = it;
    const top = Math.min(0.035, h / 4), under = rgb(c, { dark: 0.55 }), chair = rgb(CHAIR);
    s.box(it, k, [-w / 2, w / 2], [h - top, h], [-d / 2, d / 2], rgb(c));
    for (const side of [-1, 1]) {
      s.box(it, k, side < 0 ? [-w / 2 + 0.02, -w / 2 + 0.06] : [w / 2 - 0.06, w / 2 - 0.02], [0, h - top], [-d / 2 + 0.04, d / 2 - 0.04], under);
    }
    s.box(it, k, [-w / 2 + 0.06, w / 2 - 0.06], [Math.max(0, h - 0.45), h - top], [-d / 2 + 0.04, -d / 2 + 0.06], under);
    s.box(it, k, [-0.24, 0.24], [0.42, 0.48], [d / 2 + 0.14, d / 2 + 0.6], chair);
    s.box(it, k, [-0.22, 0.22], [0.48, 0.95], [d / 2 + 0.56, d / 2 + 0.62], chair);
    s.box(it, k, [-0.03, 0.03], [0, 0.42], [d / 2 + 0.34, d / 2 + 0.4], chair);
  },
  // a seat between two arms, before a back
  SOFA(s, it, k, c) {
    const { width: w, depth: d, height: h } = it;
    const arm = Math.min(0.2, w / 6), back = Math.min(0.22, d / 3), seat = Math.min(0.42, h * 0.55);
    s.box(it, k, [-w / 2 + arm, w / 2 - arm], [0, seat], [-d / 2 + back, d / 2], rgb(c, { light: 0.12 }));
    s.box(it, k, [-w / 2, w / 2], [0, h], [-d / 2, -d / 2 + back], rgb(c));
    for (const x of [-w / 2, w / 2 - arm]) s.box(it, k, [x, x + arm], [0, Math.min(h, seat + 0.2)], [-d / 2 + back, d / 2], rgb(c));
  },
  // a thin dark panel, its screen ahead
  TV(s, it, k, c) {
    const { width: w, depth: d, height: h } = it;
    const t = Math.min(d, 0.05), b = Math.min(0.03, w / 10, h / 10);
    s.box(it, k, [-w / 2, w / 2], [0, h], [-d / 2, -d / 2 + t], rgb(c));
    s.box(it, k, [-w / 2 + b, w / 2 - b], [b, h - b], [-d / 2 + t, -d / 2 + t + 0.004], rgb(SCREEN));
  },
  // a box with a lighter lid
  COPIER(s, it, k, c) {
    const { width: w, depth: d, height: h } = it;
    const lid = Math.min(0.08, h / 6);
    s.box(it, k, [-w / 2, w / 2], [0, h - lid], [-d / 2, d / 2], rgb(c));
    s.box(it, k, [-w / 2 + 0.02, w / 2 - 0.02], [h - lid, h], [-d / 2 + 0.02, d / 2 - 0.02], rgb(c, { light: 0.6 }));
  },
  // a small disc, under the ceiling
  ACCESS(s, it, k, c) {
    s.disc(it, k, Math.min(it.width, it.depth) / 2, [0, it.height], rgb(c));
  },
};
DRAW.SCREEN = DRAW.TV;
DRAW.PRINTER = DRAW.COPIER;

/** Anything else: a box; on the ceiling, a disc when it is round enough. */
function drawPlain(s, it, k, c) {
  if (it.mount === "ceiling" && Math.abs(it.width - it.depth) < 0.1 * Math.max(it.width, it.depth)) return DRAW.ACCESS(s, it, k, c);
  s.box(it, k, [-it.width / 2, it.width / 2], [0, it.height], [-it.depth / 2, it.depth / 2], rgb(c));
}

/** A floor's furniture and equipment as pieces, as buildPieces gives its other
 * ones: ``form`` "detailed" (each drawn as DRAW says), or "light", a box each, for
 * a whole building at once. Two pieces at most: what stands below the cut
 * (``items``), and what is above it, on a wall or under the ceiling (``items:high``);
 * ``_item`` indexes ``plan.items``. */
export function buildItems(plan, form = "detailed", options = {}) {
  const o = { ...GEOMETRY, ...options };
  const low = new Shapes(), high = new Shapes();
  plan.items.forEach((it, k) => {
    const s = it.y + it.height <= o.cutHeight ? low : high;
    const placed = { ...it, base: plan.elevation + it.y };
    if (form === "light") drawPlain(s, { ...placed, mount: "floor" }, k, it.color);
    else (DRAW[it.type.split("-")[0]] ?? drawPlain)(s, placed, k, it.color);
  });
  const Index = plan.items.length > 65535 ? Uint32Array : Uint16Array;
  const pieces = [];
  for (const [shapes, above] of [[low, false], [high, true]]) {
    if (!shapes.index.length) continue;
    const name = ["items", form === "light" && "light", above && "high"].filter(Boolean).join(":");
    pieces.push({ name, ...PIECES[name], geometry: shapes.geometry(Index) });
  }
  return pieces;
}

/** A box round an item (``it``, of plan.items), ``pad`` metres wider each way: its
 * highlight. */
export function itemBox(plan, it, pad = 0.05) {
  const g = new THREE.BoxGeometry(it.width + 2 * pad, it.height + 2 * pad, it.depth + 2 * pad);
  g.rotateY(Math.PI - Math.atan2(it.fx, it.fn));
  g.translate(it.x, plan.elevation + it.y + it.height / 2, -it.n);
  return g;
}

/** A floor built: its plan and its geometry. */
export function buildFloor(pkg, floor, origin, options = {}) {
  const plan = planFloor(pkg, floor, origin, options);
  return { plan, ...buildPieces(plan, options) };
}
