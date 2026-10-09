// The geometry of a floor for the 3D world, built from the package: in the
// browser (world.js) or ahead of time in Node (viewer/world/bake.mjs, which writes
// it as binary glTF for a package's world/ folder). No DOM here: three.js's maths
// and geometry only, so both build the same floor.
//
// A floor comes out as a few merged pieces, one per material and use (the walls'
// faces, the floor finishes of offices, the door leaves, …), so a floor of 800
// rooms is drawn in a few dozen draw calls. The floor finishes and the x-ray
// volumes carry their room's index (`_room`; `rooms` lists the IDs), to pick a room
// by its triangles. Boxes (door frames, skirting, …) and furniture are written
// straight into arrays, the furniture from one template a kind and size: a floor of
// a thousand rooms and desks is built in tens of milliseconds.
//
// What every look draws is here: skirting along the walls, architraves and lever
// handles on doors, window boards, ceiling panels (ceilingPanels: where the walker's
// lights are), furniture with its chairs; and, asked for (`edges`, the "model"
// look), the lines along their edges. How they are drawn is materials.js's.
//
// Local metres: x east, y up, z south, from the building's origin (originOf). A
// plan point is [x, n] (n north, so z = −n).

import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { toLonLat, wrapLongitude } from "./frame.js";

/** This builder's version: a floor pre-built by another (before 2, without its
 * items; before 3, without skirting, architraves, handles, window boards, ceiling
 * panels and the finer furniture) is built again. */
export const BUILDER = 3;

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
const WINDOW_FRAME = 0.035; // m, the frame round the glass, and how deep it is
const WINDOW_DEPTH = 0.065;
const GLASS = 0.012; // m, thick
const PANE = 1.0; // m: a mullion about this often across a window
const SKIRTING = { height: 0.08, proud: 0.014 }; // m, along every face of every wall
const ARCHITRAVE = { width: 0.075, proud: 0.016 }; // m, round a door on both faces of its wall
const PANEL = { half: 0.3, step: 2.4, narrow: 3.4, clear: 0.15 }; // m: ceiling panels, 60 cm square, about every 2.4 m
const UNLIT = new Set(["shaft", "elevator", "open_to_below"]); // no ceiling panels
const EDGE_TURN = Math.cos((20 * Math.PI) / 180); // a wall's corner turns more than this: a line up it (edges)
const DOUBLE_DOOR = 1.3; // m: a door wider than this, drawn without its swings, has two leaves
const OUTDOOR = new Set(["terrace", "balcony"]); // open to the sky, behind parapets
const PARAPET = 1.1; // m, when the package gives no parapet height
const ITEM_COLOR = "#8a8a8a"; // an item whose type the package does not describe (or not as #rrggbb)
const COLOR = /^#[0-9a-f]{6}$/i; // the colours a package may give (catalogue.json): nothing else is used
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
  // the details: skirting along every face of the walls and parapets, architraves
  // round doors and their lever handles, a board on each window's sill, and ceiling
  // panels (lit when walking)
  skirting: { material: "skirting" },
  trim: { material: "trim", view: "full" },
  handle: { material: "handle", view: "full" },
  sillBoard: { material: "sillBoard" },
  lights: { material: "lightPanel", view: "walk" },
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
/** The pieces with lines along their edges when asked for (`edges`): the walls (their
 * lines on `wall` and `parapet`), the slab, door and window frames, door leaves, window
 * boards and the items; not the wall over and under openings (it is the wall), the
 * floors, glass, skirting, architraves, handles or ceiling. */
export const EDGED = new Set(["slab", "wall", "parapet", "frame", "doorFrame", "door", "sillBoard",
  "items", "items:high", "items:light", "items:light:high"]);

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
 * none), and the metres in a degree of longitude and latitude there. Its
 * longitudes are taken within 180° of its first: one across the antimeridian is
 * a building's width, not the world's. */
export function originOf(pkg, buildingId) {
  const floors = pkg.floorsOf(buildingId);
  let ref = null, x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const visit = (c) => {
    if (typeof c[0] === "number") {
      ref ??= c[0];
      const x = wrapLongitude(c[0], ref);
      x0 = Math.min(x0, x); y0 = Math.min(y0, c[1]); x1 = Math.max(x1, x); y1 = Math.max(y1, c[1]);
    } else c.forEach(visit);
  };
  for (const f of floors.length ? floors : [pkg.get(buildingId)]) if (f?.geometry) visit(f.geometry.coordinates);
  const lon = Number.isFinite(x0) ? wrapLongitude((x0 + x1) / 2) : 0;
  const lat = Number.isFinite(y0) ? (y0 + y1) / 2 : 0;
  return { lon, lat, kx: 111320 * Math.cos((lat * Math.PI) / 180), ky: 110540 };
}

/** Longitude/latitude → local meters [x east, n north] (across the antimeridian, the short way). */
export function toLocal(origin, [lon, lat]) {
  return [wrapLongitude(lon - origin.lon) * origin.kx, (lat - origin.lat) * origin.ky];
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

  // the furniture and equipment (format 0.6, planItem), and the edges of those on the
  // floor, which the walker bumps into
  const placement = pkg.manifest?.placements?.[props.building_id];
  setItems(plan, (pkg.itemsOn?.(floor.id) ?? []).map((item) => planItem(item.id, item.properties,
    { origin, placement, wallHeight, type: pkg.itemType?.(item.properties.type) })));

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
 * ``obstacles``, what the walker bumps into: [[x1, z1, x2, z2], …]. With ``edges``,
 * the pieces of EDGED carry the lines along their edges as well (``edges``: x, y, z
 * at each end of each); with ``only: "edges"``, they carry nothing else and nothing
 * else is built (no triangulation: quick), for a floor shown already. */
export function buildPieces(plan, options = {}) {
  const o = { ...GEOMETRY, ...options };
  const solid = o.only !== "edges", lined = Boolean(o.edges) || !solid;
  const { elevation: e, wallHeight, parapetHeight, thickness, walls, parapets } = plan;
  const sets = new Map(); // name → { piece, geometries, boxes, edges }
  const set = (key, { type, hidden = false } = {}) => {
    const name = [key, type, hidden && "hidden"].filter(Boolean).join(":");
    let s = sets.get(name);
    if (!s) {
      s = { piece: { name, ...PIECES[key], ...(type ? { type } : {}), ...(hidden ? { hidden } : {}) }, geometries: [], boxes: null,
        edges: lined && EDGED.has(key) ? [] : null };
      sets.set(name, s);
    }
    return s;
  };
  const put = (key, geometry, how) => {
    if (!geometry?.attributes.position?.count) return geometry?.dispose();
    set(key, how).geometries.push(geometry);
  };
  // a box from plan point p to q, y0 to y1 above the floor, ``depth`` across (its middle
  // on p–q), with the faces ``faces`` (FACE): written straight into its piece's arrays
  const box = (key, p, q, y0, y1, depth, faces = FACE.all) => {
    const s = set(key);
    if (solid) (s.boxes ??= new Boxes()).box(e, p, q, y0, y1, depth, faces);
    if (s.edges) boxEdges(s.edges, e, p, q, y0, y1, depth);
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
  if (solid) {
    const slab = extrude(plan.outline, o.slab, e - o.slab);
    slab?.clearGroups();
    put("slab", slab);
  }
  if (lined) outlineEdges(set("slab").edges, plan.outline, e - o.slab, e);

  // the rooms: an x-ray volume each, and a floor finish for each of their units
  const roofed = []; // under the ceiling: every room but terraces and balconies
  for (const r of plan.rooms) {
    if (!r.outdoor) roofed.push(...r.rings);
    if (!solid) continue;
    const volume = extrude(r.rings, (r.outdoor ? parapetHeight : wallHeight) - 0.05, e + 0.01);
    volume.clearGroups();
    put("volume", room(volume, r.id), { type: r.type, hidden: r.units.length > 0 && r.units.every((u) => u.tucked) });
    for (const u of r.units) {
      if (u.type === "open_to_below") continue;
      put("floor", room(flat(u.rings, e + 0.004), u.id), { type: u.type, hidden: u.tucked });
    }
  }

  // the ceiling, seen only when walking; none over terraces and balconies; and its
  // panels, 60 cm square, where the walker's lights are (their faces down alone)
  const ceilingPolys = plan.units.length ? roofed : plan.outline;
  if (solid && ceilingPolys.length) put("ceiling", flat(ceilingPolys, e + wallHeight));
  if (solid) {
    const { half } = PANEL, y0 = wallHeight - 0.014;
    for (const { x, z, ux, un } of ceilingPanels(plan)) {
      const cx = x, cn = -z;
      box("lights", [cx - ux * half, cn - un * half], [cx + ux * half, cn + un * half], y0, y0 + 0.012, half * 2, FACE.bottom);
    }
  }

  // the walls and parapets, full height (cut low by cutPieces), with skirting along
  // every face
  const extruded = (polys, height, key, top) => {
    if (solid) {
      const [caps, sides] = capsAndSides(extrude(polys, height, e));
      put(key, sides);
      put(top, caps);
      skirting(polys, box);
    }
    if (lined) outlineEdges(set(key).edges, polys, e, e + height);
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

  // door heads, frames, architraves, leaves and handles; window sills, boards, heads,
  // frames and glass
  const obstacles = [];
  for (const ring of plan.wallRings) {
    for (let i = 0; i + 1 < ring.length; i++) {
      obstacles.push([ring[i][0], -ring[i][1], ring[i + 1][0], -ring[i + 1][1]]);
    }
  }
  const wallAt = jambs(plan);
  for (const way of plan.ways) {
    const { type } = way;
    const len = Math.hypot(way.b[0] - way.a[0], way.b[1] - way.a[1]);
    if (len < 0.3 || !inFullWall(way)) continue;
    const ux = (way.b[0] - way.a[0]) / len, un = (way.b[1] - way.a[1]) / len;
    // the wall it is in: as thick as its jambs, its middle where theirs are (the span may
    // be on a face, and outer walls thicker than the floor's own)
    const { depth: thickness, shift } = wallAt(way, ux, un);
    const at = (t, across = 0) => [way.a[0] + ux * t - un * (across + shift), way.a[1] + un * t + ux * (across + shift)];
    // (the wall over and under it a little into the wall each side: no slit at its jambs)
    const a = at(0), b = at(len), a2 = at(-0.02), b2 = at(len + 0.02);
    // sizes from the drawing's schedule where it gives them; a window taller than the
    // floor (through two storeys) stops at this floor's ceiling
    const ceiling = wallHeight - 0.02;
    if (type === "window") {
      const sill = Math.min(Math.max(way.sill ?? o.windowSill, 0), ceiling - 0.2);
      const head = Math.min(way.height !== null && way.height !== undefined ? sill + way.height : o.windowHead, ceiling);
      if (sill > 0.01) {
        box("sills", a2, b2, 0, sill, thickness);
        // a board on the sill, a little proud of the wall each side; skirting along the sill
        box("sillBoard", at(-0.04), at(len + 0.04), sill - 0.025, sill + 0.002, thickness + 0.07, FACE.all & ~FACE.bottom);
        if (solid) {
          const { height, proud } = SKIRTING;
          for (const side of [-1, 1]) {
            const off = side * (thickness / 2 + proud / 2);
            box("skirting", at(0, off), at(len, off), 0, height, proud, FACE.all & ~FACE.bottom & ~(side > 0 ? FACE.right : FACE.left));
          }
        }
      }
      if (head < wallHeight - 0.01) box("heads", a2, b2, head, wallHeight, thickness);
      box("glass", a, b, sill, head, GLASS);
      // the frame: along the sill and the head, at each side, and a mullion about
      // every metre between
      const f = WINDOW_FRAME, depth = Math.min(thickness, WINDOW_DEPTH);
      box("frame", a, b, sill, sill + f, depth);
      box("frame", a, b, head - f, head, depth);
      const panes = Math.max(1, Math.round(len / PANE));
      for (let k = 0; k <= panes; k++) {
        const t = Math.min(Math.max((k * len) / panes, f / 2), len - f / 2);
        box("frame", at(t - f / 2), at(t + f / 2), sill + f, head - f, depth);
      }
      obstacles.push([way.a[0], -way.a[1], way.b[0], -way.b[1]]); // you cannot walk through a window
    } else if (type === "door") {
      const top = Math.min(way.height ?? o.doorHead, ceiling);
      if (top < wallHeight - 0.01) box("heads", a2, b2, top, wallHeight, thickness);
      // the frame: a jamb each side and a head, standing a little proud of the wall
      const c = Math.min(CASING, len / 4), depth = thickness + 0.03;
      box("doorFrame", a, at(c), 0, top, depth);
      box("doorFrame", at(len - c), b, 0, top, depth);
      box("doorFrame", at(c), at(len - c), top - c, top, depth);
      // architraves on both faces of the wall: up each side and over the head
      if (solid) {
        const { width: w, proud: p } = ARCHITRAVE, high = Math.min(top + w - 0.015, wallHeight - 0.005);
        for (const side of [-1, 1]) {
          const off = side * (thickness / 2 + p / 2), faces = FACE.all & ~FACE.bottom & ~(side > 0 ? FACE.right : FACE.left);
          box("trim", at(-w + 0.015, off), at(0.015, off), 0, high, p, faces);
          box("trim", at(len - 0.015, off), at(len + w - 0.015, off), 0, high, p, faces);
          box("trim", at(0.015, off), at(len - 0.015, off), top - 0.015, high, p, faces);
        }
      }
      // the leaves, open as the plan draws them; without the swings, open into the
      // room it serves, one leaf or two; a lever handle each side, by its free edge
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
        box("door", from, to, 0.01, top - c, LEAF);
        obstacles.push([from[0], -from[1], to[0], -to[1]]); // an open leaf stands in the way
        if (solid) leverHandles(from, [dx, dn], w, box);
      }
    } else if (len <= OPEN_SPAN) { // a doorway: a way through with no door
      box("heads", a2, b2, o.doorHead, wallHeight, thickness);
    }
  }

  // one geometry per piece; texture coordinates only where there is a texture
  const pieces = [];
  for (const { piece: p, geometries, boxes, edges } of sets.values()) {
    const lines = edges?.length ? { edges: new Float32Array(edges) } : {};
    if (!solid) {
      if (lines.edges) pieces.push({ ...p, ...lines });
      continue;
    }
    for (const g of geometries) if (!TEXTURED.has(p.material)) g.deleteAttribute("uv");
    if (boxes) geometries.push(geometries.length ? boxes.geometry().toNonIndexed() : boxes.geometry());
    if (!geometries.length) continue;
    const geometry = geometries.length === 1 ? geometries[0] : mergeGeometries(geometries, false);
    if (geometries.length > 1) geometries.forEach((g) => g.dispose());
    pieces.push({ ...p, geometry: upright(geometry), ...lines });
  }
  return { pieces, rooms, obstacles };
}

/** How thick the wall an opening is in is, and how far its middle is across from the
 * opening's span: from the wall's jambs, the short edges across it at each end of the
 * span (walls are drawn with gaps at their openings); else the floor's thickness, on
 * the span. A function of (way, ux, un) → { depth, shift }. */
function jambs(plan) {
  const cell = 1, grid = new Map();
  const key = (i, j) => i * 100003 + j;
  for (const ring of [...plan.walls, ...plan.parapets].flat(1)) {
    for (let k = 0; k + 1 < ring.length; k++) {
      const p = ring[k], q = ring[k + 1], len = Math.hypot(q[0] - p[0], q[1] - p[1]);
      if (len < 0.03 || len > 1.2) continue; // a jamb is a wall's thickness long
      const i = Math.floor((p[0] + q[0]) / 2 / cell), j = Math.floor((p[1] + q[1]) / 2 / cell);
      if (!grid.has(key(i, j))) grid.set(key(i, j), []);
      grid.get(key(i, j)).push([p, q, len]);
    }
  }
  return ({ a, b }, ux, un) => {
    const depths = [], shifts = [];
    for (const end of [a, b]) {
      const i0 = Math.floor(end[0] / cell), j0 = Math.floor(end[1] / cell);
      for (let i = i0 - 1; i <= i0 + 1; i++) {
        for (let j = j0 - 1; j <= j0 + 1; j++) {
          for (const [p, q, len] of grid.get(key(i, j)) ?? []) {
            if (Math.abs(((q[0] - p[0]) * ux + (q[1] - p[1]) * un) / len) > 0.25) continue; // across the span
            const mx = (p[0] + q[0]) / 2 - end[0], mn = (p[1] + q[1]) / 2 - end[1];
            const along = mx * ux + mn * un, across = -mx * un + mn * ux;
            if (Math.abs(along) > 0.12 || Math.abs(across) > 0.6) continue;
            depths.push(len);
            shifts.push(across);
          }
        }
      }
    }
    if (!depths.length) return { depth: plan.thickness, shift: 0 };
    const middle = (v) => v.sort((x, y) => x - y)[v.length >> 1];
    return { depth: middle(depths), shift: middle(shifts) };
  };
}

// ---- boxes, and the lines along edges --------------------------------------------------

/** A box's faces, to leave out those never seen (against a wall, on the floor). */
export const FACE = { top: 1, bottom: 2, end: 4, start: 8, left: 16, right: 32, all: 63 };
// each face: the axis it faces along (0 along, 1 across: to the left, 2 up), which way,
// and the two axes it spans, in the order that turns its corners counter-clockwise seen
// from outside (along × across = up in the world: z is south, n north)
const BOX_SIDES = [[2, 1, 0, 1], [2, 0, 1, 0], [0, 1, 1, 2], [0, 0, 2, 1], [1, 1, 2, 0], [1, 0, 0, 2]];

/** Boxes written straight into arrays, for a piece: a box from plan point p to q, y0 to
 * y1 above a floor at e, ``depth`` across, its middle on p–q; its corners shared by its
 * faces, with no normals and no texture (the materials of boxes are flat-shaded), as the
 * items' are. Quicker by far than a BoxGeometry each, merged, and a third of the size. */
class Boxes {
  constructor() {
    this.position = [];
    this.index = [];
  }

  box(e, p, q, y0, y1, depth, faces) {
    const len = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (len < 1e-6) return;
    const ux = (q[0] - p[0]) / len, un = (q[1] - p[1]) / len, d = depth / 2;
    const range = [[0, len], [-d, d], [y0, y1]];
    const vertex = new Array(8); // corner i (bit 1 along, 2 across, 4 up): its vertex, once a face has it
    const c = [0, 0, 0];
    for (let f = 0; f < 6; f++) {
      if (!(faces & (1 << f))) continue;
      const [axis, side, ia, ib] = BOX_SIDES[f];
      c[axis] = side;
      const corner = [];
      for (const [ka, kb] of [[0, 0], [1, 0], [1, 1], [0, 1]]) {
        c[ia] = ka;
        c[ib] = kb;
        const i = c[0] + 2 * c[1] + 4 * c[2];
        if (vertex[i] === undefined) {
          const [t, s, h] = [range[0][c[0]], range[1][c[1]], range[2][c[2]]];
          vertex[i] = this.position.length / 3;
          this.position.push(p[0] + ux * t - un * s, e + h, -(p[1] + un * t + ux * s));
        }
        corner.push(vertex[i]);
      }
      this.index.push(corner[0], corner[1], corner[2], corner[0], corner[2], corner[3]);
    }
  }

  geometry() {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.position, 3));
    g.setIndex(this.position.length / 3 > 65535 ? new THREE.Uint32BufferAttribute(this.index, 1)
      : new THREE.Uint16BufferAttribute(this.index, 1));
    return g;
  }
}

/** The 12 edges of a box as Boxes places one, as lines (x, y, z at each end). */
function boxEdges(out, e, p, q, y0, y1, depth) {
  const len = Math.hypot(q[0] - p[0], q[1] - p[1]);
  if (len < 1e-6) return;
  const ux = (q[0] - p[0]) / len, un = (q[1] - p[1]) / len, d = depth / 2;
  const corner = (i) => {
    const t = i & 1 ? len : 0, s = i & 2 ? d : -d, h = i & 4 ? y1 : y0;
    return [p[0] + ux * t - un * s, e + h, -(p[1] + un * t + ux * s)];
  };
  for (const [i, j] of BOX_EDGES) out.push(...corner(i), ...corner(j));
}
// a box's edges, between its corners (bits: 1, 2 and 4 for the three axes)
const BOX_EDGES = [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]];

/** Lines along the walls of polygons standing from y0 to y1: round each ring at the
 * bottom and the top, and up each corner (where the wall turns). */
function outlineEdges(out, polygons, y0, y1) {
  for (const ring of polygons.flat(1)) {
    const n = ring.length - 1; // closed: its last point is its first
    if (n < 2) continue;
    for (let i = 0; i < n; i++) {
      const [x0, n0] = ring[i], [x1, n1] = ring[i + 1];
      out.push(x0, y0, -n0, x1, y0, -n1, x0, y1, -n0, x1, y1, -n1);
      const [xp, np] = ring[(i + n - 1) % n];
      const ax = x0 - xp, an = n0 - np, bx = x1 - x0, bn = n1 - n0;
      const la = Math.hypot(ax, an), lb = Math.hypot(bx, bn);
      if (la > 1e-6 && lb > 1e-6 && (ax * bx + an * bn) / (la * lb) < EDGE_TURN) out.push(x0, y0, -n0, x0, y1, -n0);
    }
  }
}

// ---- the details ---------------------------------------------------------------------

/** A ring's signed area (plan metres): positive when counter-clockwise. */
function signedArea(ring) {
  let a = 0;
  for (let i = 0; i + 1 < ring.length; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  return a / 2;
}

/** A skirting board along every face of walls (polygons), a little proud of it, run
 * its thickness past each end so that outer corners close; its back (against the
 * wall) and its underside left out. */
function skirting(polygons, box) {
  const { height, proud } = SKIRTING;
  for (const polygon of polygons) {
    polygon.forEach((ring, r) => {
      // outward from the wall's solid: right of the way an outer ring runs counter-clockwise
      const s = (r === 0 ? 1 : -1) * (signedArea(ring) > 0 ? 1 : -1);
      const faces = FACE.all & ~FACE.bottom & ~(s > 0 ? FACE.left : FACE.right);
      for (let i = 0; i + 1 < ring.length; i++) {
        const a = ring[i], b = ring[i + 1];
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
        if (len < 0.05) continue;
        const ux = (b[0] - a[0]) / len, un = (b[1] - a[1]) / len;
        const ox = s * un * (proud / 2), on = -s * ux * (proud / 2);
        box("skirting", [a[0] - ux * proud + ox, a[1] - un * proud + on], [b[0] + ux * proud + ox, b[1] + un * proud + on],
          0, height, proud, faces);
      }
    });
  }
}

/** Lever handles each side of a door leaf (from its hinge along ``dir``, ``w`` wide),
 * near its free edge: a rose, a neck and the lever. */
function leverHandles(from, [dx, dn], w, box) {
  const nx = -dn, nn = dx; // across the leaf
  const at = (t, off) => [from[0] + dx * t + nx * off, from[1] + dn * t + nn * off];
  const t = w - 0.07, y = 1.0;
  for (const side of [-1, 1]) {
    const o = (k) => side * (LEAF / 2 + k);
    box("handle", at(t - 0.03, o(0.005)), at(t + 0.03, o(0.005)), y - 0.03, y + 0.03, 0.01);
    box("handle", at(t - 0.01, o(0.03)), at(t + 0.01, o(0.03)), y - 0.01, y + 0.01, 0.05);
    box("handle", at(t - 0.13, o(0.055)), at(t + 0.012, o(0.055)), y - 0.011, y + 0.011, 0.022);
  }
}

/** Where a floor's ceiling panels are (60 cm square): on a grid square to each room's
 * longest edge, about every 2.4 m, a row down the middle of a narrow room; none in
 * shafts and lifts, outdoors or in rooms hidden in review. Each { x, z } (local metres,
 * its middle), the way it runs (ux, un: plan) and the unit (space or zone) it lights.
 * Worked out once a plan. */
export function ceilingPanels(plan) {
  if (plan.panels) return plan.panels;
  const { half, step, narrow: narrowest, clear } = PANEL;
  const out = [];
  for (const room of plan.rooms) {
    if (room.outdoor) continue;
    for (const u of room.units) {
      if (u.tucked || UNLIT.has(u.type)) continue;
      for (const polygon of u.rings) {
        const outer = polygon[0];
        let best = 0, ux = 1, un = 0;
        for (let i = 0; i + 1 < outer.length; i++) {
          const ex = outer[i + 1][0] - outer[i][0], en = outer[i + 1][1] - outer[i][1], l = Math.hypot(ex, en);
          if (l > best) {
            best = l;
            ux = ex / l;
            un = en / l;
          }
        }
        const vx = -un, vn = ux;
        let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
        for (const [x, n] of outer) {
          const pu = x * ux + n * un, pv = x * vx + n * vn;
          u0 = Math.min(u0, pu); u1 = Math.max(u1, pu); v0 = Math.min(v0, pv); v1 = Math.max(v1, pv);
        }
        const rows = (a, b, every) => {
          const n = Math.max(1, Math.round((b - a) / every));
          return Array.from({ length: n }, (_, i) => a + ((i + 0.5) * (b - a)) / n);
        };
        const narrow = v1 - v0 < narrowest;
        const us = rows(u0, u1, narrow ? step * 1.25 : step), vs = narrow ? [(v0 + v1) / 2] : rows(v0, v1, step);
        const inRoom = (x, n) => inside(outer, x, n) && !polygon.slice(1).some((h) => inside(h, x, n));
        const r = half + clear;
        for (const pu of us) {
          for (const pv of vs) {
            const cx = pu * ux + pv * vx, cn = pu * un + pv * vn;
            if (![[-1, -1], [1, -1], [1, 1], [-1, 1]].every(([a, b]) => inRoom(cx + (a * ux + b * vx) * r, cn + (a * un + b * vn) * r))) continue;
            out.push({ x: cx, z: -cn, ux, un, unit: u.id });
          }
        }
      }
    }
  }
  plan.panels = out;
  return out;
}

/** What a look across a floor stops at, as its plan has it, for aiming without its
 * triangles (a floor of a thousand rooms is aimed at in well under a millisecond):
 * the edges of its walls and parapets, its windows, and the heads over its doors and
 * doorways, each [x1, z1, x2, z2, from, to, toCut] (local metres; from and to: how high
 * it stands above the floor; toCut: how high in the cutaway view). */
export function occluders(plan, options = {}) {
  const o = { ...GEOMETRY, ...options };
  const out = [];
  const edges = (polys, to, toCut) => {
    for (const ring of polys.flat(1)) {
      for (let i = 0; i + 1 < ring.length; i++) out.push([ring[i][0], -ring[i][1], ring[i + 1][0], -ring[i + 1][1], 0, to, toCut]);
    }
  };
  edges(plan.walls, plan.wallHeight, Math.min(o.cutHeight, plan.wallHeight));
  edges(plan.parapets, plan.parapetHeight, Math.min(o.cutHeight, plan.parapetHeight));
  for (const { a, b, type, height } of plan.ways) {
    const span = [a[0], -a[1], b[0], -b[1]];
    if (type === "window") out.push([...span, 0, plan.wallHeight, Math.min(o.cutHeight, plan.wallHeight)]);
    else if (type === "door" || Math.hypot(b[0] - a[0], b[1] - a[1]) <= OPEN_SPAN) { // the head over it, not cut
      out.push([...span, Math.min(type === "door" ? height ?? o.doorHead : o.doorHead, plan.wallHeight - 0.02), plan.wallHeight, 0]);
    }
  }
  return out;
}

/** The walls and parapets cut low, for the cutaway view, from the full ones: each
 * vertex above ``cutHeight`` comes down to it, which is the extrusion that high
 * (an extrusion's sides have v = 1 − height), without building it again; their
 * lines (``edges``) likewise. */
export function cutPieces(pieces, elevation, options = {}) {
  const y = elevation + (options.cutHeight ?? GEOMETRY.cutHeight);
  const out = [];
  for (const p of pieces) {
    const name = CUT_LOW[p.name];
    if (!name) continue;
    const cut = { name, ...PIECES[name] };
    if (p.geometry) {
      const geometry = p.geometry.clone();
      const position = geometry.getAttribute("position"), uv = geometry.getAttribute("uv");
      for (let i = 0; i < position.count; i++) {
        if (position.getY(i) <= y) continue;
        position.setY(i, y);
        uv?.setY(i, 1 - (y - elevation));
      }
      cut.geometry = geometry;
    }
    if (p.edges) {
      const edges = p.edges.slice();
      for (let i = 1; i < edges.length; i += 3) if (edges[i] > y) edges[i] = y;
      cut.edges = edges;
    }
    out.push(cut);
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

/** An item as the world plans it, from its properties as a package gives them (or
 * null when they do not say where it is): where it stands ([x, n], local metres), the
 * way its front faces (fx, fn), its size, how high its bottom is, its colour and the
 * grade of a desk. Where it stands in its building (format 0.7: ``local``, its middle
 * and its turn counter-clockwise from the drawing's -y) is put on the map by the
 * building's ``placement``, as Studio put its walls there; older packages give its
 * point and heading on the map. ``type``: its catalogue entry (colour, grade). */
export function planItem(id, p, { origin, placement, wallHeight, type }) {
  const own = p.local && placement ? p.local : null;
  if (!own && !p.display_point) return null;
  const [x, n] = toLocal(origin, own ? toLonLat(placement, [own.x_m, own.y_m]) : p.display_point);
  // the drawing's +y faces the placement's bearing, so its -y the opposite way
  const heading = own ? (placement.bearing || 0) + 180 - (own.rotation_deg || 0) : p.heading ?? 0;
  const h = (heading * Math.PI) / 180;
  const { color, grade } = type ?? {};
  const it = { id, type: p.type, mount: p.mount ?? "floor", x, n, fx: Math.sin(h), fn: Math.cos(h),
    width: p.width_m || 1, depth: p.depth_m || 0.6, height: p.height_m || 0.75,
    color: typeof color === "string" && COLOR.test(color) ? color : ITEM_COLOR,
    grade: typeof grade === "string" && grade in DESK_SETS ? grade : null };
  it.y = it.mount === "ceiling" ? wallHeight - it.height - 0.01 : p.elevation_m ?? (it.mount === "wall" ? WALL_ITEM : 0);
  return it;
}

/** A floor's plan given its items (planItem's; null ones left out): ``items``, and the
 * edges of those on the floor, which the walker bumps into (``itemObstacles``). */
export function setItems(plan, items) {
  plan.items = items.filter(Boolean);
  plan.itemObstacles = [];
  for (const it of plan.items) {
    if (it.mount !== "floor") continue;
    const c = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => itemPoint(it, (a * it.width) / 2, (b * it.depth) / 2));
    c.forEach((q, i) => plan.itemObstacles.push([q[0], q[1], c[(i + 1) % 4][0], c[(i + 1) % 4][1]]));
  }
  return plan;
}

/** Furniture drawn in its own frame — across, up, and ahead (towards its front) — once
 * for each kind and size, as a template its items are copies of (buildItems): plain
 * boxes (their corners shared by their faces), bevelled boxes, and cylinders, with no
 * normals (the material is flat-shaded) and no texture. Each vertex has its colour and
 * its finish (FIN: how rough and how metallic), and the parts their lines along their
 * edges (the thin ones none). Parts are placed and turned within the item by ``at``,
 * drawn in a finish by ``in``. */
class Shapes {
  constructor() {
    this.position = [];
    this.color = [];
    this.finish = [];
    this.index = [];
    this.lines = [];
    this.frame = { x: 0, z: 0, a: 0, c: 1, s: 0 };
    this.fin = FIN.plain;
  }

  /** Draw with parts placed at (x, z) and turned ``a`` (radians) in the frame. */
  at(x, z, a, draw) {
    const f = this.frame, t = f.a + a;
    this.frame = { x: f.x + x * f.c - z * f.s, z: f.z + x * f.s + z * f.c, a: t, c: Math.cos(t), s: Math.sin(t) };
    draw();
    this.frame = f;
  }

  /** Draw in a finish (FIN). */
  in(fin, draw) {
    const was = this.fin;
    this.fin = fin;
    draw();
    this.fin = was;
  }

  /** A point of the frame (across, up, ahead) in the item's own. */
  #point(across, up, ahead) {
    const f = this.frame;
    return [f.x + across * f.c - ahead * f.s, up, f.z + across * f.s + ahead * f.c];
  }

  #vertex(across, up, ahead, rgb) {
    this.position.push(...this.#point(across, up, ahead));
    this.color.push(rgb[0], rgb[1], rgb[2]);
    this.finish.push(this.fin[0], this.fin[1]);
  }

  #line(a, b) {
    this.lines.push(...this.#point(...a), ...this.#point(...b));
  }

  /** A box across x0–x1, up y0–y1 and ahead z0–z1. */
  box([x0, x1], [y0, y1], [z0, z1], rgb, edged = thick(x1 - x0, y1 - y0, z1 - z0)) {
    const at = this.position.length / 3;
    const X = [x0, x1], Y = [y0, y1], Z = [z0, z1];
    for (let i = 0; i < 8; i++) this.#vertex(X[i & 1], Y[(i >> 1) & 1], Z[(i >> 2) & 1], rgb);
    for (const v of BOX_FACES) this.index.push(at + v);
    if (!edged) return;
    const corner = (i) => [X[i & 1], Y[(i >> 1) & 1], Z[(i >> 2) & 1]];
    for (const [i, j] of BOX_EDGES) this.#line(corner(i), corner(j));
  }

  /** A box with its edges chamfered ``b`` metres (a bevel that catches the light); its
   * lines along the middle of its bevels. */
  rbox([x0, x1], [y0, y1], [z0, z1], rgb, b = 0.01, edged = thick(x1 - x0, y1 - y0, z1 - z0)) {
    b = Math.min(b, (x1 - x0) * 0.45, (y1 - y0) * 0.45, (z1 - z0) * 0.45);
    if (b <= 0.0005) return this.box([x0, x1], [y0, y1], [z0, z1], rgb, edged);
    const at = this.position.length / 3;
    const X = [x0, x1], Y = [y0, y1], Z = [z0, z1], Xi = [x0 + b, x1 - b], Yi = [y0 + b, y1 - b], Zi = [z0 + b, z1 - b];
    // each corner (bits: 1 across, 2 up, 4 ahead) has a vertex on each of its three faces
    for (let c = 0; c < 8; c++) {
      const sx = c & 1, sy = (c >> 1) & 1, sz = (c >> 2) & 1;
      this.#vertex(X[sx], Yi[sy], Zi[sz], rgb);
      this.#vertex(Xi[sx], Y[sy], Zi[sz], rgb);
      this.#vertex(Xi[sx], Yi[sy], Z[sz], rgb);
    }
    for (const v of RBOX_FACES) this.index.push(at + v);
    if (!edged) return;
    const m = (lo, hi, side) => (side ? hi - b / 2 : lo + b / 2); // the middle of a bevel
    for (const p of [0, 1]) {
      for (const q of [0, 1]) {
        this.#line([Xi[0], m(y0, y1, p), m(z0, z1, q)], [Xi[1], m(y0, y1, p), m(z0, z1, q)]); // along x
        this.#line([m(x0, x1, p), Yi[0], m(z0, z1, q)], [m(x0, x1, p), Yi[1], m(z0, z1, q)]); // up
        this.#line([m(x0, x1, p), m(y0, y1, q), Zi[0]], [m(x0, x1, p), m(y0, y1, q), Zi[1]]); // ahead
      }
    }
  }

  /** An upright cylinder of radius ``r`` at (x, z) from y0 to y1; its lines round its ends. */
  cyl(x, z, r, [y0, y1], rgb, sides = 12, edged = r >= 0.04) {
    const at = this.position.length / 3;
    for (let i = 0; i < sides; i++) {
      const a = (i / sides) * Math.PI * 2;
      this.#vertex(x + r * Math.cos(a), y0, z + r * Math.sin(a), rgb);
      this.#vertex(x + r * Math.cos(a), y1, z + r * Math.sin(a), rgb);
    }
    for (const v of cylinderFaces(sides)) this.index.push(at + v);
    if (!edged) return;
    for (let i = 0; i < sides; i++) {
      const a = (i / sides) * Math.PI * 2, b = ((i + 1) / sides) * Math.PI * 2;
      for (const y of [y0, y1]) this.#line([x + r * Math.cos(a), y, z + r * Math.sin(a)], [x + r * Math.cos(b), y, z + r * Math.sin(b)]);
    }
  }

  /** The template: its arrays, as buildItems copies them. */
  done() {
    return { vertices: this.position.length / 3, position: Float32Array.from(this.position), color: Uint8Array.from(this.color),
      finish: Uint8Array.from(this.finish), index: Uint32Array.from(this.index), lines: Float32Array.from(this.lines) };
  }
}

/** Whether a part is thick enough for lines along its edges (not a leg, a rail, a handle). */
function thick(...sizes) {
  return sizes.sort((a, b) => b - a)[1] >= 0.04;
}

// a box's corners: bit 1 across, bit 2 up, bit 4 ahead; its faces outwards
const BOX_FACES = [1, 3, 7, 1, 7, 5, 0, 4, 6, 0, 6, 2, 2, 6, 7, 2, 7, 3, 0, 1, 5, 0, 5, 4, 4, 5, 7, 4, 7, 6, 0, 2, 3, 0, 3, 1];

/** Convex faces (lists of vertices of ``points``) as triangles facing away from
 * ``centre``: worked out once for each shape, as its template. */
function wound(points, faces, centre) {
  const out = [];
  for (const f of faces) {
    const [a, b, c] = [points[f[0]], points[f[1]], points[f[2]]];
    const n = [(b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
      (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
      (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])];
    const m = [0, 1, 2].map((k) => f.reduce((s, i) => s + points[i][k], 0) / f.length - centre[k]);
    const g = n[0] * m[0] + n[1] * m[1] + n[2] * m[2] >= 0 ? f : [...f].reverse();
    for (let i = 1; i + 1 < g.length; i++) out.push(g[0], g[i], g[i + 1]);
  }
  return out;
}

// a bevelled box's faces, from its 24 vertices (rbox): 6 faces, 12 bevels and 8 corners
const RBOX_FACES = (() => {
  const points = [];
  for (let c = 0; c < 8; c++) {
    const s = [c & 1, (c >> 1) & 1, (c >> 2) & 1].map((v) => (v ? 1 : 0));
    const i = s.map((v) => (v ? 0.9 : 0.1));
    points.push([s[0], i[1], i[2]], [i[0], s[1], i[2]], [i[0], i[1], s[2]]);
  }
  const vx = (c) => c * 3, vy = (c) => c * 3 + 1, vz = (c) => c * 3 + 2;
  const C = (sx, sy, sz) => sx | (sy << 1) | (sz << 2);
  const faces = [];
  for (const s of [0, 1]) {
    faces.push([C(s, 0, 0), C(s, 1, 0), C(s, 1, 1), C(s, 0, 1)].map(vx));
    faces.push([C(0, s, 0), C(1, s, 0), C(1, s, 1), C(0, s, 1)].map(vy));
    faces.push([C(0, 0, s), C(1, 0, s), C(1, 1, s), C(0, 1, s)].map(vz));
  }
  for (const p of [0, 1]) {
    for (const q of [0, 1]) {
      faces.push([vy(C(0, p, q)), vy(C(1, p, q)), vz(C(1, p, q)), vz(C(0, p, q))]);
      faces.push([vx(C(p, 0, q)), vx(C(p, 1, q)), vz(C(p, 1, q)), vz(C(p, 0, q))]);
      faces.push([vx(C(p, q, 0)), vx(C(p, q, 1)), vy(C(p, q, 1)), vy(C(p, q, 0))]);
    }
  }
  for (let c = 0; c < 8; c++) faces.push([vx(c), vy(c), vz(c)]);
  return wound(points, faces, [0.5, 0.5, 0.5]);
})();

const CYLINDERS = new Map();
/** A cylinder's faces (cyl), by its number of sides: its sides and its two ends. */
function cylinderFaces(sides) {
  if (!CYLINDERS.has(sides)) {
    const points = [];
    for (let i = 0; i < sides; i++) {
      const a = (i / sides) * Math.PI * 2;
      points.push([Math.cos(a), 0, Math.sin(a)], [Math.cos(a), 1, Math.sin(a)]);
    }
    const faces = [];
    for (let i = 0; i < sides; i++) faces.push([2 * i, 2 * ((i + 1) % sides), 2 * ((i + 1) % sides) + 1, 2 * i + 1]);
    faces.push(Array.from({ length: sides }, (_, i) => 2 * i), Array.from({ length: sides }, (_, i) => 2 * i + 1));
    CYLINDERS.set(sides, wound(points, faces, [0, 0.5, 0]));
  }
  return CYLINDERS.get(sides);
}

/** A colour as the vertex colours keep it: 0–255 in linear light. */
function rgb(color, { dark = 0, light = 0 } = {}) {
  const c = new THREE.Color(color);
  if (dark) c.multiplyScalar(1 - dark);
  if (light) c.lerp(new THREE.Color(1, 1, 1), light);
  return [c.r, c.g, c.b].map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255));
}
const SCREEN = "#0e1117";
const BED_FRAME = "#5a4334";
const LINEN = "#ecebe6";
const STEEL = "#b7babe", POWDER = "#2e3136", FABRIC = "#41444c", LEATHER = "#25262a", PALE = "#dcdcda", WALNUT = "#3b2a1e";

/** How rough and how metallic a part is (0–255 each): the "real" look reads them
 * (`_finish`), the "model" look leaves them. */
const FIN = {
  plain: [153, 0], fabric: [245, 0], wood: [125, 0], lacquer: [90, 0], metal: [70, 255], dark: [110, 160],
  plastic: [115, 0], screen: [25, 0], linen: [235, 0], leather: [140, 0],
};

/** What goes with a desk, by the grade it is for (its type's, format 0.7): visitors'
 * chairs across it (armchairs for the president's), a return at its side (an
 * L-shaped desk), a credenza behind its chair, and an executive chair. The SVG plan
 * draws the same. */
export const DESK_SETS = {
  junior: { visitors: 0 },
  senior: { visitors: 0, return: true },
  section_head: { visitors: 1, return: true },
  manager: { visitors: 2, return: true },
  director: { visitors: 2, return: true, cabinet: true, executive: true },
  c_level: { visitors: 2, return: true, cabinet: true, executive: true },
  president: { visitors: 2, armchairs: true, return: true, cabinet: true, executive: true },
};
// where a desk's chair and its visitors' chairs stand (DRAW.DESK, itemExtent)
const CHAIR = { behind: 0.4, reach: 0.335 }; // its middle behind the desk's back edge, and its base's reach
const VISITOR = { side: { half: 0.235, depth: 0.24, back: 0.245 }, arm: { half: 0.36, depth: 0.31, back: 0.31 }, gap: 0.2 };

/** An office chair facing -ahead: five spokes on castors, a gas lift, a seat, a back
 * and arms; ``exec``, wider, in leather, its back up to the head. */
function officeChair(s, exec) {
  const seat = rgb(exec ? LEATHER : FABRIC), base = rgb(exec ? STEEL : POWDER), castor = rgb("#1b1c1f");
  for (let i = 0; i < 5; i++) {
    s.at(0, 0, (i / 5) * Math.PI * 2 + 0.3, () => {
      s.in(exec ? FIN.metal : FIN.dark, () => s.box([-0.016, 0.016], [0.045, 0.075], [0.02, 0.31], base));
      s.in(FIN.plastic, () => s.box([-0.012, 0.012], [0, 0.048], [0.27, 0.315], castor));
    });
  }
  s.in(FIN.metal, () => {
    s.cyl(0, 0, 0.045, [0.045, 0.1], base, 12);
    s.cyl(0, 0, 0.021, [0.1, 0.4], rgb(STEEL), 8);
  });
  const w = exec ? 0.27 : 0.25;
  s.in(exec ? FIN.leather : FIN.fabric, () => {
    s.rbox([-w, w], [0.4, 0.49], [-0.25, 0.22], seat, 0.035);
    s.rbox([-w + 0.02, w - 0.02], [0.56, exec ? 1.22 : 1.0], [0.22, exec ? 0.31 : 0.27], seat, exec ? 0.045 : 0.03);
  });
  s.in(FIN.dark, () => {
    s.box([-0.03, 0.03], [0.42, 0.6], [0.17, 0.24], base); // the back's stem
    for (const side of [-1, 1]) { // the arms: a post and a pad
      const x = side * (w + 0.015);
      s.box([x - 0.015, x + 0.015], [0.45, 0.64], [-0.02, 0.05], base);
      s.in(FIN.plastic, () => s.rbox([x - 0.03, x + 0.03], [0.64, 0.665], [-0.13, 0.11], rgb("#202125"), 0.01));
    }
  });
}

/** A visitor's chair facing -ahead: four slim legs, a seat and a back. */
function sideChair(s) {
  const seat = rgb(FABRIC), leg = rgb(STEEL);
  s.in(FIN.metal, () => {
    for (const x of [-0.2, 0.2]) {
      s.box([x - 0.011, x + 0.011], [0, 0.44], [-0.2, -0.178], leg);
      s.box([x - 0.011, x + 0.011], [0, 0.88], [0.18, 0.202], leg);
    }
  });
  s.in(FIN.fabric, () => {
    s.rbox([-0.235, 0.235], [0.44, 0.5], [-0.235, 0.2], seat, 0.025);
    s.rbox([-0.225, 0.225], [0.54, 0.86], [0.2, 0.245], seat, 0.02);
  });
}

/** An upholstered armchair facing -ahead, on four short wooden feet. */
function armchair(s, c) {
  const body = rgb(c, { dark: 0.35 }), cushion = rgb(c, { dark: 0.25 });
  s.in(FIN.leather, () => {
    s.rbox([-0.36, 0.36], [0.08, 0.42], [-0.31, 0.31], body, 0.04);
    s.rbox([-0.25, 0.25], [0.42, 0.5], [-0.31, 0.19], cushion, 0.035);
    s.rbox([-0.36, 0.36], [0.42, 0.88], [0.19, 0.31], body, 0.05);
    for (const x of [-1, 1]) s.rbox(x < 0 ? [-0.36, -0.25] : [0.25, 0.36], [0.42, 0.62], [-0.31, 0.25], body, 0.04);
  });
  s.in(FIN.wood, () => {
    for (const x of [-0.3, 0.3]) for (const z of [-0.25, 0.25]) s.cyl(x, z, 0.02, [0, 0.08], rgb(WALNUT), 8);
  });
}

/** A drawer unit (x0–x1, y0–y1, z0–z1), its front ahead, its drawers' grooves and handles. */
function pedestal(s, [x0, x1], [y0, y1], [z0, z1], c, drawers = 3) {
  s.in(FIN.wood, () => s.rbox([x0, x1], [y0, y1], [z0, z1], c, 0.006));
  const groove = rgb("#1f1d1b"), h = (y1 - y0) / drawers;
  for (let i = 1; i < drawers; i++) s.box([x0 + 0.01, x1 - 0.01], [y0 + i * h - 0.002, y0 + i * h + 0.002], [z1, z1 + 0.002], groove, false);
  s.in(FIN.metal, () => {
    for (let i = 0; i < drawers; i++) {
      const y = y0 + (i + 0.75) * h, m = (x0 + x1) / 2;
      s.box([m - 0.06, m + 0.06], [y - 0.006, y + 0.006], [z1 + 0.002, z1 + 0.018], rgb(STEEL));
    }
  });
}

/** How each kind of item is drawn, by its type code's first part (DESK-MANAGER is a
 * desk); others by how they are mounted. Each gets the item (its width, depth, height
 * and grade) and its colour, and draws it in its own frame, its front ahead. */
const DRAW = {
  // a top on a steel frame with a modesty panel (an executive's: on panel ends, with a
  // drawer pedestal), its chair pulled up to it; and what goes with its grade (DESK_SETS)
  DESK(s, it, c) {
    const { width: w, depth: d, height: h } = it;
    const set = DESK_SETS[it.grade] ?? DESK_SETS.junior;
    const top = 0.03, wood = rgb(c), dark = rgb(c, { dark: 0.25 });
    s.in(FIN.wood, () => s.rbox([-w / 2, w / 2], [h - top, h], [-d / 2, d / 2], wood, 0.006));
    if (set.executive) {
      s.in(FIN.wood, () => {
        for (const side of [-1, 1]) {
          const x = side * (w / 2 - 0.035);
          s.rbox([x - 0.03, x + 0.03], [0, h - top], [-d / 2 + 0.02, d / 2 - 0.02], dark, 0.006);
        }
        s.rbox([-w / 2 + 0.06, w / 2 - 0.06], [0.15, h - top], [-d / 2 + 0.03, -d / 2 + 0.055], dark, 0.004);
      });
      pedestal(s, [w / 2 - 0.5, w / 2 - 0.07], [0.04, h - top - 0.005], [-d / 2 + 0.08, d / 2 - 0.03], dark);
    } else {
      const frame = rgb(POWDER);
      s.in(FIN.dark, () => {
        for (const side of [-1, 1]) {
          const x = side * (w / 2 - 0.06);
          for (const z of [-d / 2 + 0.06, d / 2 - 0.06]) s.box([x - 0.025, x + 0.025], [0, h - top], [z - 0.025, z + 0.025], frame);
          s.box([x - 0.025, x + 0.025], [h - top - 0.05, h - top], [-d / 2 + 0.06, d / 2 - 0.06], frame);
          s.box([x - 0.025, x + 0.025], [0, 0.03], [-d / 2 + 0.03, d / 2 - 0.03], frame);
        }
        s.box([-w / 2 + 0.08, w / 2 - 0.08], [h - top - 0.05, h - top], [-d / 2 + 0.05, -d / 2 + 0.08], frame);
      });
      s.in(FIN.plastic, () => s.box([-w / 2 + 0.1, w / 2 - 0.1], [0.3, h - top - 0.05], [-d / 2 + 0.06, -d / 2 + 0.075], rgb(PALE)));
    }
    if (set.return) { // an L: a return at the user's right, on a pedestal turned to the chair
      const r = Math.min(0.5, w / 3);
      s.in(FIN.wood, () => {
        s.rbox([w / 2 - r, w / 2], [h - top, h], [d / 2 - 0.002, d / 2 + 0.8], wood, 0.006);
        s.rbox([w / 2 - r + 0.03, w / 2 - 0.03], [0, h - top], [d / 2 + 0.73, d / 2 + 0.78], dark, 0.006);
      });
      const half = (r - 0.08) / 2;
      s.at(w / 2 - r / 2, d / 2 + 0.5, Math.PI / 2, () => pedestal(s, [-0.2, 0.2], [0.04, h - top - 0.005], [-half, half], dark));
    }
    if (set.cabinet) { // a credenza behind the chair, its doors towards the desk
      const y1 = Math.min(0.72, h), z0 = d / 2 + 0.95, z1 = d / 2 + 1.4, hd = (z1 - z0) / 2;
      s.at(0, (z0 + z1) / 2, Math.PI, () => {
        s.box([-w * 0.44, w * 0.44], [0, 0.05], [-hd + 0.03, hd - 0.03], rgb("#1f1d1b"));
        s.in(FIN.wood, () => s.rbox([-w * 0.45, w * 0.45], [0.05, y1], [-hd, hd], dark, 0.008));
        for (const x of [-w * 0.15, w * 0.15]) s.box([x - 0.002, x + 0.002], [0.07, y1 - 0.03], [hd, hd + 0.002], rgb("#1a1817"), false);
        s.in(FIN.metal, () => {
          for (const x of [-w * 0.3, -w * 0.18, w * 0.18, w * 0.3]) s.box([x - 0.006, x + 0.006], [y1 - 0.2, y1 - 0.08], [hd + 0.002, hd + 0.018], rgb(STEEL));
        });
      });
    }
    s.at(0, d / 2 + CHAIR.behind, 0, () => officeChair(s, set.executive));
    // visitors across it, facing its user
    const v = set.armchairs ? VISITOR.arm : VISITOR.side;
    for (const x of visitorsAt(set, w)) s.at(x, -d / 2 - VISITOR.gap - v.depth, Math.PI, () => (set.armchairs ? armchair(s, c) : sideChair(s)));
  },
  // a base on short feet, a back and arms, and its cushions
  SOFA(s, it, c) {
    const { width: w, depth: d, height: h } = it;
    const arm = Math.min(0.2, w / 6), back = Math.min(0.24, d / 3), seat = Math.min(0.44, h * 0.56);
    const body = rgb(c), cushion = rgb(c, { light: 0.08 });
    s.in(FIN.wood, () => {
      for (const x of [-w / 2 + 0.08, w / 2 - 0.08]) for (const z of [-d / 2 + 0.08, d / 2 - 0.08]) s.cyl(x, z, 0.022, [0, 0.09], rgb(WALNUT), 8);
    });
    s.in(FIN.fabric, () => {
      s.rbox([-w / 2, w / 2], [0.09, 0.3], [-d / 2, d / 2], body, 0.025);
      s.rbox([-w / 2, w / 2], [0.3, h - 0.06], [-d / 2, -d / 2 + 0.13], body, 0.04);
      for (const x of [-w / 2, w / 2 - arm]) s.rbox([x, x + arm], [0.09, seat + 0.18], [-d / 2, d / 2], body, 0.06);
      const n = w - 2 * arm > 1.5 ? 3 : 2, gap = 0.012, cw = (w - 2 * arm - gap * (n - 1)) / n;
      for (let i = 0; i < n; i++) {
        const x = -w / 2 + arm + i * (cw + gap);
        s.rbox([x, x + cw], [0.3, seat], [-d / 2 + back - 0.02, d / 2 - 0.01], cushion, 0.045);
        s.rbox([x, x + cw], [seat - 0.02, h], [-d / 2 + 0.11, -d / 2 + back + 0.04], cushion, 0.06);
      }
    });
  },
  // a thin panel, its screen ahead
  TV(s, it, c) {
    const { width: w, depth: d, height: h } = it;
    const t = Math.min(d, 0.045), b = Math.min(0.012, w / 20);
    s.in(FIN.plastic, () => s.rbox([-w / 2, w / 2], [0, h], [-d / 2, -d / 2 + t], rgb(c), 0.006));
    s.in(FIN.screen, () => s.box([-w / 2 + b, w / 2 - b], [b, h - b], [-d / 2 + t, -d / 2 + t + 0.002], rgb(SCREEN), false));
  },
  // a cabinet, the machine on it and its lid, its panel and tray; a band of its colour
  COPIER(s, it, c) {
    const { width: w, depth: d, height: h } = it;
    const body = rgb("#d6d7d9"), light = rgb("#ecedee"), dark = rgb("#2a2c30"), groove = rgb("#8d9096");
    const cab = h * 0.46;
    s.in(FIN.plastic, () => {
      s.rbox([-w / 2, w / 2], [0.03, cab], [-d / 2, d / 2], body, 0.012);
      s.rbox([-w / 2 + 0.01, w / 2 - 0.01], [cab, h - 0.1], [-d / 2 + 0.01, d / 2 - 0.01], body, 0.012);
      s.rbox([-w / 2 + 0.03, w / 2 - 0.03], [h - 0.1, h], [-d / 2 + 0.03, d / 2 - 0.03], light, 0.012);
      s.rbox([w / 2 - 0.36, w / 2 - 0.06], [h - 0.12, h - 0.07], [d / 2 - 0.04, d / 2 + 0.1], dark, 0.01); // its panel
      s.rbox([-w / 2 - 0.16, -w / 2 + 0.01], [h * 0.62, h * 0.635], [-d / 4, d / 4], light, 0.004); // its tray
    });
    for (const y of [cab * 0.36, cab * 0.68]) s.box([-w / 2 + 0.03, w / 2 - 0.03], [y - 0.003, y + 0.003], [d / 2, d / 2 + 0.002], groove, false);
    s.box([-w / 2 + 0.02, w / 2 - 0.02], [cab - 0.025, cab], [d / 2, d / 2 + 0.003], rgb(c), false);
    s.in(FIN.screen, () => s.box([w / 2 - 0.3, w / 2 - 0.16], [h - 0.07, h - 0.068], [d / 2, d / 2 + 0.07], rgb("#1d3346"), false));
    s.box([-w / 2 + 0.03, w / 2 - 0.03], [0, 0.03], [-d / 2 + 0.03, d / 2 - 0.03], dark, false);
  },
  // a padded headboard, a frame on feet, the mattress, pillows against the headboard and
  // the covers, in the item's colour, turned down below them
  BED(s, it, c) {
    const { width: w, depth: d, height: h } = it;
    const head = Math.min(0.09, d / 20), top = Math.min(0.55, h * 0.6), base = 0.3;
    const frame = rgb(BED_FRAME), linen = rgb(LINEN), cover = rgb(c);
    const back = -d / 2 + head;
    s.in(FIN.fabric, () => s.rbox([-w / 2, w / 2], [0, h], [-d / 2, back], rgb(c, { dark: 0.3 }), 0.035));
    s.in(FIN.wood, () => {
      s.rbox([-w / 2, w / 2], [0.08, base], [back, d / 2], frame, 0.012);
      for (const x of [-w / 2 + 0.06, w / 2 - 0.06]) s.cyl(x, d / 2 - 0.06, 0.025, [0, 0.08], frame, 8);
    });
    s.in(FIN.linen, () => {
      s.rbox([-w / 2 + 0.03, w / 2 - 0.03], [base, top], [back + 0.02, d / 2 - 0.03], linen, 0.05);
      const n = w >= 1.3 ? 2 : 1, gap = 0.06, pw = (w - 0.14 - gap * (n - 1)) / n;
      for (let i = 0; i < n; i++) {
        const x = -w / 2 + 0.07 + i * (pw + gap);
        s.rbox([x, x + pw], [top - 0.02, top + 0.13], [back + 0.06, back + 0.46], linen, 0.06);
      }
    });
    s.in(FIN.fabric, () => {
      s.rbox([-w / 2 + 0.01, w / 2 - 0.01], [top - 0.14, top + 0.04], [back + 0.6, d / 2 - 0.005], cover, 0.04);
      s.rbox([-w / 2 + 0.008, w / 2 - 0.008], [top - 0.1, top + 0.055], [back + 0.58, back + 0.78], rgb(c, { light: 0.25 }), 0.035);
    });
  },
  // a plinth, a post and a head on it, its screen ahead
  KIOSK(s, it, c) {
    const { width: w, depth: d, height: h } = it;
    const plinth = Math.min(0.05, h / 20), head = Math.min(0.8, h * 0.45), b = Math.min(0.04, w / 10, head / 10);
    const back = -Math.min(0.06, d / 4), front = Math.min(0.08, d / 2);
    s.in(FIN.dark, () => s.rbox([-w / 2, w / 2], [0, plinth], [-d / 2, d / 2], rgb(c, { dark: 0.55 }), 0.012));
    s.in(FIN.lacquer, () => {
      s.rbox([-w * 0.2, w * 0.2], [plinth, h - head + 0.02], [-d * 0.25, d * 0.15], rgb(c), 0.03);
      s.rbox([-w / 2, w / 2], [h - head, h], [back, front], rgb(c), 0.025);
    });
    s.in(FIN.screen, () => s.box([-w / 2 + b, w / 2 - b], [h - head + b, h - b], [front, front + 0.003], rgb(SCREEN), false));
  },
  // a pale disc under the ceiling, its light in its colour
  ACCESS(s, it, c) {
    const r = Math.min(it.width, it.depth) / 2;
    s.in(FIN.plastic, () => {
      s.cyl(0, 0, r, [it.height * 0.4, it.height], rgb(PALE), 20);
      s.cyl(0, 0, r * 0.88, [0, it.height * 0.4], rgb("#f2f2f0"), 20, false);
      s.cyl(0, 0, r * 0.08, [-0.002, 0.001], rgb(c), 8, false);
    });
  },
};
DRAW.SCREEN = DRAW.TV;
DRAW.PRINTER = DRAW.COPIER;

/** Anything else: a box; on the ceiling, a disc when it is round enough. */
function drawPlain(s, it, c) {
  if (it.mount === "ceiling" && Math.abs(it.width - it.depth) < 0.1 * Math.max(it.width, it.depth)) return DRAW.ACCESS(s, it, c);
  s.box([-it.width / 2, it.width / 2], [0, it.height], [-it.depth / 2, it.depth / 2], rgb(c), true);
}

/** Where a desk's visitors sit across it (DESK_SETS): across, from its middle. */
function visitorsAt(set, w) {
  const vw = set.armchairs ? 0.72 : 0.47;
  return set.visitors === 1 ? [0] : set.visitors === 2 ? [-1, 1].map((q) => q * Math.max(vw / 2 + 0.08, Math.min(w / 4, 0.62))) : [];
}

/** An item's template (Shapes.done), made once for its kind and size: ``form``
 * "detailed" (as DRAW draws it) or "light" (a box). */
function template(cache, it, form) {
  const kind = form === "light" ? "light" : DRAW[it.type.split("-")[0]] ? it.type.split("-")[0] : "plain";
  const key = [kind, it.width, it.depth, it.height, it.color, it.grade, kind === "plain" ? it.mount : ""].join("|");
  let t = cache.get(key);
  if (!t) {
    const s = new Shapes();
    if (kind === "light") drawPlain(s, { ...it, mount: "floor" }, it.color);
    else (DRAW[kind] ?? drawPlain)(s, it, it.color);
    t = s.done();
    cache.set(key, t);
  }
  return t;
}

/** A floor's furniture and equipment as pieces, as buildPieces gives its other
 * ones: ``form`` "detailed" (each drawn as DRAW says), or "light", a box each, for
 * a whole building at once. Two pieces at most: what stands on the floor or below
 * the cut (``items``: a kiosk taller than the cut walls is still seen), and what is
 * above it, on a wall or under the ceiling (``items:high``); ``_item`` indexes
 * ``plan.items``, ``_finish`` says how rough and metallic each vertex is. Each item a
 * copy of its kind and size's template, turned and moved to where it stands: a
 * thousand desks in milliseconds. With ``edges``, the lines along their parts' edges
 * too (``edges``); with ``only: "edges"``, those alone. */
export function buildItems(plan, form = "detailed", options = {}) {
  const o = { ...GEOMETRY, ...options };
  const solid = o.only !== "edges", lined = Boolean(o.edges) || !solid;
  const cache = new Map();
  const parts = [[], []]; // below the cut, above it: [template, item, its index, its bottom]
  plan.items.forEach((it, k) => {
    const above = !(it.mount === "floor" || it.y + it.height <= o.cutHeight);
    parts[above ? 1 : 0].push([template(cache, it, form), it, k, plan.elevation + it.y]);
  });
  const pieces = [];
  parts.forEach((list, above) => {
    if (!list.length) return;
    const name = ["items", form === "light" && "light", above && "high"].filter(Boolean).join(":");
    const piece = { name, ...PIECES[name] };
    if (solid) piece.geometry = placed(list, plan.items.length);
    if (lined) piece.edges = placedLines(list);
    pieces.push(piece);
  });
  return pieces;
}

/** Items' templates copied to where each stands, as one geometry. */
function placed(list, count) {
  let vertices = 0, indices = 0;
  for (const [t] of list) {
    vertices += t.vertices;
    indices += t.index.length;
  }
  const position = new Float32Array(vertices * 3), color = new Uint8Array(vertices * 3), finish = new Uint8Array(vertices * 2);
  const item = new (count > 65535 ? Uint32Array : Uint16Array)(vertices);
  const index = new (vertices > 65535 ? Uint32Array : Uint16Array)(indices);
  let v = 0, i = 0;
  for (const [t, it, k, base] of list) {
    const p = t.position, { x, n, fx, fn } = it;
    for (let j = 0; j < t.vertices; j++) {
      const a = p[3 * j], u = p[3 * j + 1], b = p[3 * j + 2], at = 3 * (v + j);
      position[at] = x - a * fn + b * fx;
      position[at + 1] = base + u;
      position[at + 2] = -(n + a * fx + b * fn);
    }
    color.set(t.color, 3 * v);
    finish.set(t.finish, 2 * v);
    item.fill(k, v, v + t.vertices);
    for (let j = 0; j < t.index.length; j++) index[i + j] = t.index[j] + v;
    v += t.vertices;
    i += t.index.length;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(position, 3));
  g.setAttribute("color", new THREE.BufferAttribute(color, 3, true));
  g.setAttribute("_finish", new THREE.BufferAttribute(finish, 2, true));
  g.setAttribute("_item", new THREE.BufferAttribute(item, 1));
  g.setIndex(new THREE.BufferAttribute(index, 1));
  return g;
}

/** Items' templates' lines, where each stands. */
function placedLines(list) {
  let n = 0;
  for (const [t] of list) n += t.lines.length;
  const out = new Float32Array(n);
  let at = 0;
  for (const [t, it, , base] of list) {
    const p = t.lines, { x, n: north, fx, fn } = it;
    for (let j = 0; j < p.length; j += 3) {
      const a = p[j], u = p[j + 1], b = p[j + 2];
      out[at++] = x - a * fn + b * fx;
      out[at++] = base + u;
      out[at++] = -(north + a * fx + b * fn);
    }
  }
  return out;
}

/** What an item takes in its own frame, as DRAW draws it: [across0, ahead0, across1,
 * ahead1, top] (metres from its middle and its bottom): its footprint and height, and
 * for a desk its chair and what goes with its grade. */
export function itemExtent(it) {
  const w = it.width, d = it.depth;
  if (it.type.split("-")[0] !== "DESK") return [-w / 2, -d / 2, w / 2, d / 2, it.height];
  const set = DESK_SETS[it.grade] ?? DESK_SETS.junior;
  const v = set.armchairs ? VISITOR.arm : VISITOR.side, xs = visitorsAt(set, w);
  const side = Math.max(w / 2, CHAIR.reach, ...xs.map((x) => Math.abs(x) + v.half));
  const front = xs.length ? d / 2 + VISITOR.gap + v.depth + v.back : d / 2;
  const behind = Math.max(CHAIR.behind + CHAIR.reach, set.return ? 0.8 : 0, set.cabinet ? 1.4 : 0);
  return [-side, -front, side, d / 2 + behind, Math.max(it.height, set.executive ? 1.22 : 1.0)];
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
