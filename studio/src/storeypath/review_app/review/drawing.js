// Drawing what the drawing leaves out: a wall, a dividing line, a space, a door, a window,
// an opening. Each is kept with the floor and read with its drawing every time (walls part
// spaces as drawn walls do); the floor is read again at once. Plan coordinates are local
// metres, as the spaces are. Also: what is under the pointer (a door, a line drawn here),
// taking away what was drawn, and a door's, window's or opening's size.

import { viewOnly } from "./access.js";
import { followJob, request } from "./api.js";
import { emit } from "./bus.js";
import { $, svg } from "./dom.js";
import { assetShape, drawGuides, placeAsset, placing } from "./items.js";
import { openFloor, refreshProject } from "./floor.js";
import { toast } from "./notify.js";
import { settle } from "../fit.js";
import { selectItem } from "./selection.js";
import { BASE, inRing, rings, state, toSegment, typeOf, view3d } from "./state.js";
import { setTool } from "./tools.js";
import { dirty3d } from "./view3d.js";
import * as vertical from "./vertical.js";

export const WIDTHS = { door: 0.9, window: 1.2, opening: 1.0 }; // what is added, until given another size
export const LINES = { wall: "wall", divide: "divider" }; // the tools that draw a line, and what it is

/** The floor's wall edges (and the walls drawn here), for snapping to. */
function wallSegments() {
  if (state.segments) return state.segments;
  const list = [];
  const add = (ring) => { for (let i = 0; i + 1 < ring.length; i++) list.push([ring[i], ring[i + 1]]); };
  if (state.floor?.walls) rings(state.floor.walls).forEach(add);
  (state.floor?.edits?.walls || []).forEach(add);
  state.segments = list;
  return list;
}

function nearestOnWalls(p, reach) {
  let best = null;
  for (const [a, b] of wallSegments()) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    const q = [a[0] + t * dx, a[1] + t * dy];
    const d = Math.hypot(p[0] - q[0], p[1] - q[1]);
    if (d <= reach && (!best || d < best.d)) best = { q, d, seg: [a, b] };
  }
  return best;
}

function insideWalls(p) {
  let inside = false;
  for (const ring of state.floor?.walls ? rings(state.floor.walls) : []) {
    for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
      const [xj, yj] = ring[j], [xk, yk] = ring[k];
      if ((yj > p[1]) !== (yk > p[1]) && p[0] < ((xk - xj) * (p[1] - yj)) / (yk - yj) + xj) inside = !inside;
    }
  }
  return inside;
}

/** Where a wall end goes: a wall corner or edge within a few pixels, else straight
 * across or along from where the wall starts when nearly so, else the point. */
export function snapWall(p, from = state.wallStart) {
  const reach = 10 / state.view.k;
  let corner = null;
  for (const [a, b] of wallSegments()) {
    for (const v of [a, b]) {
      const d = Math.hypot(p[0] - v[0], p[1] - v[1]);
      if (d <= reach && (!corner || d < corner.d)) corner = { q: v, d };
    }
  }
  if (corner) return corner.q;
  const edge = nearestOnWalls(p, reach);
  if (edge) return edge.q;
  const s = from;
  if (s) {
    const angle = Math.atan2(p[1] - s[1], p[0] - s[0]);
    const square = Math.round(angle / (Math.PI / 2)) * (Math.PI / 2);
    if (Math.abs(angle - square) < (7 * Math.PI) / 180) {
      const len = Math.hypot(p[0] - s[0], p[1] - s[1]);
      return [s[0] + len * Math.cos(square), s[1] + len * Math.sin(square)];
    }
  }
  return p;
}

/** A door or window placed on the wall nearest the point: along it, in its middle. */
export function openingAt(p, wide = WIDTHS[state.tool]) {
  const width = Math.min(Math.max(Number(wide) || 0.9, 0.3), 6);
  const near = nearestOnWalls(p, Math.max(1.0, 12 / state.view.k));
  if (!near) return null;
  const [a, b] = near.seg;
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  const u = [(b[0] - a[0]) / len, (b[1] - a[1]) / len], n = [-u[1], u[0]];
  const t = state.floor.wall_thickness || 0.2;
  const into = insideWalls([near.q[0] + n[0] * 0.05, near.q[1] + n[1] * 0.05]) ? 1 : -1;
  const c = [near.q[0] + n[0] * into * (t / 2), near.q[1] + n[1] * into * (t / 2)];
  return [[c[0] - (u[0] * width) / 2, c[1] - (u[1] * width) / 2], [c[0] + (u[0] * width) / 2, c[1] + (u[1] * width) / 2]];
}

export function clearPreview() {
  for (const id of ["preview", "preview-print"]) $(id).replaceChildren();
}

export function preview(p) {
  if (state.busy) return;
  const r = 5 / state.view.k;
  let shapes = [];
  if (state.tool === "place") {
    const t = typeOf(state.placeType);
    if (t) { // where it would go: drawn to walls and other items, kept in the room
      const got = settle({ want: { at: p, rot: 0 }, last: null, ...placing(p, t.code), free: state.alt });
      drawGuides(got?.guides);
      shapes.push(() => assetShape({ x: (got?.at ?? p)[0], y: (got?.at ?? p)[1], rotation: got?.rot ?? 0 }, t,
        got ? "asset-ghost" : "asset-ghost refused"));
    }
  } else if (state.tool === "space") {
    const end = snapWall(p);
    const pts = [...state.corners, end];
    const closing = state.corners.length >= 3 && closesSpace(end);
    shapes = [];
    if (pts.length >= 2) {
      const d = `M${pts.map(([x, y]) => `${x},${y}`).join("L")}${closing ? "Z" : ""}`;
      shapes.push(() => svg("path", { d, class: "space-outline" }));
    }
    for (const [x, y] of state.corners) shapes.push(() => svg("circle", { cx: x, cy: y, r }));
    shapes.push(() => svg("circle", { cx: end[0], cy: end[1], r: closing ? r * 1.8 : r }));
  } else if (state.tool in LINES) {
    const end = snapWall(p);
    shapes = state.wallStart
      ? [() => svg("line", { x1: state.wallStart[0], y1: state.wallStart[1], x2: end[0], y2: end[1], class: state.tool }),
        () => svg("circle", { cx: state.wallStart[0], cy: state.wallStart[1], r })]
      : [];
    shapes.push(() => svg("circle", { cx: end[0], cy: end[1], r }));
  } else {
    const span = openingAt(p);
    if (span) shapes = [() => svg("line", { x1: span[0][0], y1: span[0][1], x2: span[1][0], y2: span[1][1], class: state.tool })];
  }
  for (const id of ["preview", "preview-print"]) $(id).replaceChildren(...shapes.map((make) => make()));
}

/** Whether a point closes the space being drawn: on its first corner (a few pixels). */
function closesSpace(p) {
  const first = state.corners[0];
  return Boolean(first) && Math.hypot(p[0] - first[0], p[1] - first[1]) <= 10 / state.view.k;
}

/** The space being drawn, closed and saved; the floor is read again with it. */
export function closeSpace() {
  if (vertical.drawn(state.corners)) return; // vertical.js: a lift or stairs, drawn typed
  const ring = state.corners;
  if (ring.length < 3) return toast("A space needs at least three corners", true);
  state.corners = [];
  state.wallStart = null;
  setTool(null);
  submitEdit({ add: { space: ring } }, "Adding the space…");
}

/** The space being drawn: its last corner taken back. */
export function backCorner() {
  state.corners.pop();
  state.wallStart = state.corners.at(-1) ?? null;
  clearPreview();
}

export function toolClick(p) {
  if (state.busy) return;
  if (state.tool === "place") return placeAsset(state.placeType, p);
  if (state.tool === "space") {
    const at = snapWall(p);
    if (state.corners.length >= 3 && closesSpace(at)) return closeSpace();
    const last = state.corners.at(-1);
    if (last && Math.hypot(at[0] - last[0], at[1] - last[1]) < 0.05) return; // a double-click's second click
    state.corners.push(at);
    state.wallStart = at; // the next corner squares to this one
    preview(p);
    return;
  }
  if (state.tool in LINES) {
    const at = snapWall(p);
    if (!state.wallStart) {
      state.wallStart = at;
      preview(p);
      return;
    }
    const line = [state.wallStart, at];
    state.wallStart = null;
    if (Math.hypot(at[0] - line[0][0], at[1] - line[0][1]) < 0.1) return;
    const kind = LINES[state.tool];
    submitEdit({ add: { [kind]: line } }, kind === "wall" ? "Adding the wall…" : "Dividing the space…");
  } else {
    const span = openingAt(p);
    if (!span) return toast("Click on a wall (or nearer one)", true);
    submitEdit({ add: { opening: { type: state.tool, span } } }, `Adding the ${state.tool}…`);
  }
}

export async function submitEdit(body, saying, after = null) {
  if (viewOnly()) return;
  state.busy = true;
  $("map").classList.add("busy");
  try {
    await followJob(await request(`${BASE}/floors/${state.floor.id}/edits`, body), saying);
    await refreshProject();
    view3d.dirty.add(state.floor.id); // read again with it: built again in 3D (that floor alone)
    await openFloor(state.floor.id, null, { keepView: true });
    toast(body.remove ? "Taken away" : body.resize ? "Size changed; the floor was read again" : "Added; the floor was read again");
    after?.();
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
  } finally {
    state.busy = false;
    $("map").classList.remove("busy");
    clearPreview();
  }
}

/** The door, window or opening under the pointer: its bar, its leaves or its mark,
 * within a few pixels (or its wall's thickness). */
export function openingNear(p, px = 9) {
  const reach = Math.max(px / state.view.k, (state.floor?.wall_thickness || 0.2) * 0.6);
  let best = null;
  for (const d of state.floor?.doors || []) {
    if (d.ignored && !state.showHidden) continue;
    let dist = Math.hypot(d.point[0] - p[0], d.point[1] - p[1]);
    if (d.span) dist = Math.min(dist, toSegment(p, d.span[0], d.span[1]));
    for (const [h, q] of d.swings || []) dist = Math.min(dist, toSegment(p, h, q));
    if (dist <= reach && (!best || dist < best.dist)) best = { d, dist };
  }
  return best?.d || null;
}

/** A wall or dividing line drawn here, under the pointer: {kind, at, line} (at: its
 * middle; line: its two ends, as saved, to take away that one and no other). */
export function drawnNear(p, px = 8) {
  const reach = px / state.view.k;
  let best = null;
  const lines = [...(state.floor?.edits?.walls || []).map((w) => ["wall", w]), ...(state.floor?.edits?.dividers || []).map((w) => ["divider", w])];
  for (const [kind, [a, b]] of lines) {
    const dist = toSegment(p, a, b);
    if (dist <= reach && (!best || dist < best.dist)) best = { kind, at: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], length: Math.hypot(b[0] - a[0], b[1] - a[1]), line: [a, b], dist };
  }
  return best ? { kind: best.kind, at: best.at, length: best.length, line: best.line } : null;
}

/** The space drawn here (its ring, as saved) a point is in, or null. */
export function drawnSpaceAt(p) {
  return (state.floor?.edits?.spaces || []).find((ring) => inRing(p, ring)) || null;
}

/** Taking away what was drawn here: its kind and its points as saved (the server
 * takes that one, never another drawn near it), and where it is, for an older server. */
export function takeAway(kind, at, shape) {
  return submitEdit({ remove: { kind, at, ...(shape ? { shape } : {}) } }, "Taking it away…");
}

/** The opening drawn here that the floor reads as this door, window or opening: of
 * those drawn, the one whose middle is nearest its middle. */
function drawnOpeningOf(d) {
  const mid = d.middle || [(d.span[0][0] + d.span[1][0]) / 2, (d.span[0][1] + d.span[1][1]) / 2];
  let best = null;
  for (const o of state.floor?.edits?.openings || []) {
    const m = [(o.span[0][0] + o.span[1][0]) / 2, (o.span[0][1] + o.span[1][1]) / 2];
    const dist = Math.hypot(m[0] - mid[0], m[1] - mid[1]);
    if (dist <= 0.5 && (!best || dist < best.dist)) best = { o, dist };
  }
  return { at: mid, span: best?.o.span || null };
}

export function openingName(d) {
  const name = d.type === "window" ? "Window" : d.type === "door" ? "Door" : "Opening";
  return d.tag ? `${name} ${d.tag}` : name;
}

export function openingMeta(d) {
  return [d.width && `${d.width.toFixed(2)} m wide`, d.sill != null && `sill ${d.sill.toFixed(2)} m`,
    d.height != null && `${d.height.toFixed(2)} m high`, d.drawn && "drawn here", d.resize && "size changed here"]
    .filter(Boolean).join(" · ");
}

// ---- sizes ---------------------------------------------------------------
// A door, window or opening's width (jamb to jamb), sill (windows) and height, in
// metres. Of one drawn here, they change it; of one of the drawing, they are kept
// with the floor (found again by where it is) and "Size as drawn" takes them away.
// An empty field: as the drawing (or its schedule) has it.

const fmt = (v) => (v == null ? "" : Number(v).toFixed(2));

export function fillSizeForm(form, d) {
  form.elements.width.value = fmt(d.width);
  form.elements.sill.value = fmt(d.sill);
  form.elements.height.value = fmt(d.height);
  form.querySelector(".sill").hidden = d.type !== "window";
  form.querySelector(".as-drawn").hidden = d.drawn || !d.resize;
}

/** The sizes to keep: a field left as it was keeps what it was given (or as drawn). */
export function sizesFrom(form, d) {
  const given = d.drawn ? { width: null, sill: d.sill ?? null, height: d.height ?? null } : { width: null, sill: null, height: null, ...(d.resize || {}) };
  const out = {};
  for (const key of ["width", "sill", "height"]) {
    const raw = form.elements[key].value.trim();
    if (!raw) out[key] = null;
    else if (fmt(raw) === fmt(d[key])) out[key] = given[key];
    else out[key] = Number(raw);
  }
  return out;
}

export function resize(d, sizes) {
  return submitEdit({ resize: { at: d.middle, ...sizes } }, "Changing the size…", () => selectItem({ kind: "door", id: d.id }));
}

export function addOpening(type, p) {
  const span = openingAt(p, WIDTHS[type]);
  if (!span) return toast("Right-click on a wall (or nearer one)", true);
  const mid = [(span[0][0] + span[1][0]) / 2, (span[0][1] + span[1][1]) / 2];
  submitEdit({ add: { opening: { type, span } } }, `Adding the ${type}…`, () => {
    // the new one, chosen: to give it its size
    const added = (state.floor.doors || []).filter((x) => x.drawn)
      .map((x) => ({ x, d: Math.hypot(x.middle[0] - mid[0], x.middle[1] - mid[1]) }))
      .sort((a, b) => a.d - b.d)[0];
    if (added && added.d < 0.5) selectItem({ kind: "door", id: added.x.id });
  });
}

export function startSpace(p) {
  setTool("space");
  const at = snapWall(p);
  state.corners = [at];
  state.wallStart = at;
  preview(p);
}

export function startLine(tool, p) {
  setTool(tool);
  state.wallStart = snapWall(p);
  preview(p);
  toast(tool === "wall" ? "Wall: click where it ends (it snaps to walls). Esc to stop."
    : "Divide: click where the line ends, right across the space. Esc to stop.");
}

/** What is chosen (a door, window, opening or line drawn here) deleted or taken away;
 * a deleted one of the drawing restored. */
export async function deleteItem() {
  const item = state.item;
  if (!item || state.busy || viewOnly()) return;
  if (item.kind === "wall" || item.kind === "divider") return takeAway(item.kind, item.at, item.line);
  const d = state.floor.doors.find((x) => x.id === item.id);
  if (!d) return;
  if (d.drawn) {
    const { at, span } = drawnOpeningOf(d);
    return takeAway("opening", at, span);
  }
  try {
    const updated = await request(`${BASE}/objects/${d.id}`, { ignored: !d.ignored });
    Object.assign(d, updated);
    dirty3d([state.floor.id]); // a wall with it or without it, in 3D
    toast(`${openingName(d)}: ${d.ignored ? "deleted" : "restored"}`);
    if (d.ignored && !state.showHidden) selectItem(null);
    else selectItem(item);
    emit("spaces", [d.id]);
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
  }
}
