// Review's state: the project, the floor shown and what is chosen on it, the plan's view,
// the tool, the 3D view's; and what the rest reads from them (a floor's spaces in use,
// what needs a look, names and colours). One object each, shared by every part.

import { TYPE_COLORS, typeLabel } from "../theme.js";
import { saved } from "./dom.js";

export const CODE = new URLSearchParams(location.search).get("p");
export const BASE = `projects/${encodeURIComponent(CODE || "")}`;

export const state = {
  project: null,
  floor: null, // the current floor, as /api/floors/<id> returns it
  byId: new Map(), // space id → space
  paths: new Map(), // space id → <path>
  labels: new Map(), // space id → <text>
  bounds: new Map(), // space id → [x0, y0, x1, y1]
  drawingBounds: null,
  drawingMode: savedMode(), // "print", "lines" or "off"
  side: saved("storeypath.side") === "1", // the print beside the spaces, not under them
  layout: "single", // "single", or side by side in "cols" (left | right) or "rows" (top / bottom)
  refit: false, // the layout changed: fit the floor again
  underlay: { lines: null, print: null }, // the floor whose drawing each layer holds
  selected: null,
  view: { k: 1, tx: 0, ty: 0 }, // screen = (x·k + tx, −y·k + ty)
  showHidden: false, // show what was deleted (or hidden)
  showLabels: true, // the rooms' names and numbers on the plan (and in 3D)
  fitted: false,
  filter: "",
  item: null, // a door, window or drawn line chosen: {kind: "door", id}, or {kind: "wall" or "divider", at}
  tool: null, // drawing: "wall", "divide", "door", "window" or "opening"
  wallStart: null, // a wall or dividing line being drawn: where it starts (a space: its last corner)
  corners: [], // a space being drawn: its corners so far
  catalogue: [], // the item types (furniture and equipment) of this Studio
  asset: null, // the item (furniture or equipment) chosen
  placeType: null, // the type being placed (tool "place")
  busy: false, // an edit is being saved and the floor read again
  segments: null, // the floor's wall edges, for snapping to
  alt: false, // Alt held (placing or dragging freely)
};

// mode: "2d", "3d" or "walk" (shown: not 2d); busy: the build under way (a promise); again:
// asked for while it was, so done once more after it; stale: the building to be built again
// whole; dirty: floors to read again; from: the view before (where walking starts); plan:
// the middle of the plan's view then; pointer: the last pointer over the 3D view; look:
// its look's and quality's controls
export const view3d = { world: null, building: null, stale: true, shown: false, mode: "2d", busy: null, again: false,
  picking: false, dirty: new Set(), from: "2d", plan: null, pointer: null, aim: 0, cross: 0, crossKey: "", soon: 0,
  look: null };

function savedMode() {
  const mode = saved("storeypath.drawing");
  return ["print", "lines", "off"].includes(mode) ? mode : "print";
}

// ---- spaces ------------------------------------------------------------------------

export const color = (type) => TYPE_COLORS[type] || TYPE_COLORS.unspecified;
export const code = (id) => id.split("-").at(-1);
export const title = (s) => [s.name, s.number].filter(Boolean).join(" ") || "Unnamed space";
/** A type as a name on its own (a heading, a list): its first letter capital ("Meeting room"). */
export const typeName = (type) => {
  const t = typeLabel(type);
  return t.charAt(0).toUpperCase() + t.slice(1);
};
export { typeLabel };

export const tucked = (s) => s.hidden || s.ignored;
export const visible = (s) => state.showHidden || !tucked(s);
// A space divided into zones is drawn by its walls and used through its zones.
export const divided = (s) => (s.zones || []).length > 0;
export const units = () => (state.floor?.spaces || []).filter((s) => !divided(s));
export const reviewSpaces = () => units().filter((s) => s.reasons.length);

export function matches(s, q) {
  if (!q) return true;
  return [s.id, s.name, s.number, typeLabel(s.type), s.type]
    .some((v) => v && v.toLowerCase().includes(q));
}

/** A floor's building: its ID's first parts. */
export const buildingOf = (floorId) => floorId.split("-").slice(0, 3).join("-");

/** Whether the floor has its drawing, to read it again with what is added. */
export const readable = () => Boolean(state.floor?.source);

export const round4 = (v) => Math.round(v * 1e4) / 1e4;
export const typeOf = (code_) => state.catalogue.find((t) => t.code === code_) || null;

// ---- shapes ------------------------------------------------------------------------

export function rings(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat();
  return [];
}

export function pathData(geometry) {
  return rings(geometry)
    .map((ring) => "M" + ring.map(([x, y]) => `${x} ${y}`).join("L") + "Z")
    .join("");
}

export function extent(geometry) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const ring of rings(geometry)) {
    for (const [x, y] of ring) {
      if (x < x0) x0 = x;
      if (y < y0) y0 = y;
      if (x > x1) x1 = x;
      if (y > y1) y1 = y;
    }
  }
  return x0 <= x1 ? [x0, y0, x1, y1] : null;
}

export function union(boxes) {
  const valid = boxes.filter(Boolean);
  if (!valid.length) return null;
  return [
    Math.min(...valid.map((b) => b[0])), Math.min(...valid.map((b) => b[1])),
    Math.max(...valid.map((b) => b[2])), Math.max(...valid.map((b) => b[3])),
  ];
}

/** Whether a point is inside a ring of [x, y] points. */
export function inRing(p, ring) {
  let inside = false;
  for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
    const [xj, yj] = ring[j], [xk, yk] = ring[k];
    if ((yj > p[1]) !== (yk > p[1]) && p[0] < ((xk - xj) * (p[1] - yj)) / (yk - yj) + xj) inside = !inside;
  }
  return inside;
}

/** Whether a point is in a shape, its holes left out (even-odd over its rings). */
export function within(p, geometry) {
  return rings(geometry).reduce((inside, ring) => inside !== inRing(p, ring), false);
}

export function toSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}
