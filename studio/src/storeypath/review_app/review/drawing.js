// Drawing what the drawing leaves out: a wall, a dividing line, a space, a door, a window,
// an opening. Each is kept with the floor and read with its drawing every time (walls part
// spaces as drawn walls do). A wall, a dividing line or a space has the floor read again at
// once; a door, window, opening or gate (added, sized, retyped, turned, moved or taken away)
// changes the floor at once, with no read (Studio's openings.py): drawn as it will be the
// moment it is made (state.ghosts), then as Studio has it. Plan coordinates are local
// metres, as the spaces are. Also: what is under the pointer (a door, a line drawn here),
// taking away what was drawn, and a door's, window's or opening's size.
//
// The tools (tools.js): Wall (W), Space (R), Divide (D), Door / window / opening (O). The
// Stairs and lifts tool (vertical.js) draws its shape with the space's corners here.

import { editable, roomTaken, viewOnly, whyNotEditable } from "./access.js";
import { command } from "./commands.js";
import { followJob, request } from "./api.js";
import { emit, on } from "./bus.js";
import { $, el, svg } from "./dom.js";
import { refreshProject } from "./floor.js";
import { refreshFloor } from "./live.js";
import { toast } from "./notify.js";
import { selectItem } from "./selection.js";
import { BASE, inRing, rings, state, toSegment, view3d } from "./state.js";
import { renderDoors, renderDrawn } from "./plan.js";
import { setTool, tool } from "./tools.js";
import { dirty3d } from "./view3d.js";
import * as vertical from "./vertical.js";

export const WIDTHS = { door: 0.9, window: 1.2, opening: 1.0, gate: 4.0 }; // what is added, until given another size
export const LINES = { wall: "wall", divide: "divider" }; // the tools that draw a line, and what it is
const OPENING_NAMES = { door: "Door", window: "Window", opening: "Opening" };
// on a location's layout (format 0.12): a gate in its boundary walls (up to 12 m: one for cars); every
// opening drawn on a layout is read as a gate (siteplan.py)
const LAYOUT_OPENINGS = { gate: "Gate" };
/** What the opening tool adds here, by kind: its name. */
const openingKinds = () => (state.floor?.layout ? LAYOUT_OPENINGS : OPENING_NAMES);
/** How wide an opening added here may be, in metres. */
export const widestOpening = () => (state.floor?.layout ? 12 : 6);

state.openingKind = "door"; // what the opening tool adds
state.openingWidth = null; // its width (null: its kind's)
// which way a door added swings, turned with R: its hinge on one jamb or the other (bit 1),
// opening into the side the pointer is on or away from it (bit 2)
state.openingSwing = 0;
let hovered = null; // the plan point the opening tool last showed its door at (a key redraws it there)
const shaping = () => state.tool === "space" || state.tool === "stairs"; // a shape drawn corner by corner

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

/** The width an opening of ``kind`` is added with. */
const widthOf = (kind = state.openingKind) => state.openingWidth ?? WIDTHS[kind];

/** A door or window placed on the wall nearest the point: along it, in its middle. */
export function openingAt(p, wide = widthOf()) {
  const width = Math.min(Math.max(Number(wide) || 0.9, 0.3), widestOpening());
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

/** A door's leaf when open as it is added at ``p`` (``span``, from openingAt): [hinge, free
 * edge], hung and turned as state.openingSwing says, across the wall. */
export function leafAt(span, p, swing = state.openingSwing) {
  const [a, b] = span;
  const w = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  const n = [-(b[1] - a[1]) / w, (b[0] - a[0]) / w];
  const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const toward = (p[0] - mid[0]) * n[0] + (p[1] - mid[1]) * n[1] >= 0 ? 1 : -1; // the pointer's side
  const side = toward * (swing & 2 ? -1 : 1);
  const hinge = span[swing & 1];
  return [hinge, [hinge[0] + n[0] * side * w, hinge[1] + n[1] * side * w]];
}

/** A door's leaf and the arc its free edge sweeps to the other jamb, as a path. */
function swingPath(span, leaf) {
  const [hinge, free] = leaf;
  const other = span[0] === hinge ? span[1] : span[0];
  const r = Math.hypot(free[0] - hinge[0], free[1] - hinge[1]);
  const cross = (free[0] - hinge[0]) * (other[1] - hinge[1]) - (free[1] - hinge[1]) * (other[0] - hinge[0]);
  return `M${hinge[0]},${hinge[1]}L${free[0]},${free[1]}A${r},${r} 0 0 ${cross > 0 ? 1 : 0} ${other[0]},${other[1]}`;
}

export function clearPreview() {
  for (const id of ["preview", "preview-print"]) $(id).replaceChildren();
}

/** What the tool in use would add, at ``p``, drawn on the plan (and on the print beside it). */
export function preview(p) {
  if (state.busy) return;
  const r = 5 / state.view.k;
  let shapes = [];
  if (shaping()) {
    const end = snapWall(p);
    const pts = [...state.corners, end];
    const closing = state.corners.length >= 3 && closesSpace(end);
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
  } else if (state.tool === "opening") {
    hovered = p;
    const span = openingAt(p);
    if (span) shapes = [() => svg("line", { x1: span[0][0], y1: span[0][1], x2: span[1][0], y2: span[1][1], class: state.openingKind })];
    if (span && state.openingKind === "door") shapes.push(() => svg("path", { d: swingPath(span, leafAt(span, p)), class: "swing" }));
  }
  for (const id of ["preview", "preview-print"]) $(id).replaceChildren(...shapes.map((make) => make()));
}

/** Whether a point closes the space being drawn: on its first corner (a few pixels). */
function closesSpace(p) {
  const first = state.corners[0];
  return Boolean(first) && Math.hypot(p[0] - first[0], p[1] - first[1]) <= 10 / state.view.k;
}

/** The space being drawn, closed and saved; the floor is read again with it (a lift or
 * stairs: vertical.js sends it typed). */
export function closeSpace() {
  if (state.tool === "stairs") return vertical.drawn(state.corners);
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
  emit("tool-progress");
}

/** A click of a drawing tool on the plan. */
export function toolClick(p) {
  if (state.busy) return;
  if (shaping()) {
    const at = snapWall(p);
    if (state.corners.length >= 3 && closesSpace(at)) return closeSpace();
    const last = state.corners.at(-1);
    if (last && Math.hypot(at[0] - last[0], at[1] - last[1]) < 0.05) return; // a double-click's second click
    state.corners.push(at);
    state.wallStart = at; // the next corner squares to this one
    preview(p);
    emit("tool-progress");
    return;
  }
  if (state.tool in LINES) {
    const at = snapWall(p);
    if (!state.wallStart) {
      state.wallStart = at;
      preview(p);
      emit("tool-progress");
      return;
    }
    const line = [state.wallStart, at];
    state.wallStart = null;
    emit("tool-progress");
    if (Math.hypot(at[0] - line[0][0], at[1] - line[0][1]) < 0.1) return;
    const kind = LINES[state.tool];
    submitEdit({ add: { [kind]: line } }, kind === "wall" ? "Adding the wall…" : "Dividing the space…");
  } else if (state.tool === "opening") {
    const span = openingAt(p);
    if (!span) return toast("Click on a wall (or nearer one)", true);
    const swing = state.openingKind === "door" ? leafAt(span, p) : undefined;
    const opening = { type: state.openingKind, span, swing };
    submitEdit({ add: { opening } }, `Adding the ${state.openingKind}…`, null, ghost([], [addedOpening(opening)]));
  }
}

// ---- doors, windows, openings and gates: changed at once ---------------------------------
// Studio changes them with no read of the floor (openings.py). The change is drawn as it will
// be the moment it is made (a ghost: the floor's openings it hides, and what it shows in their
// place), the next one may be made at once, and when Studio has it the floor's openings are
// drawn as Studio has them (its answer's version reached: the ghost goes). Refused, the floor
// is as it was, and the page says why. One Studio cannot change at once (no read of the floor
// kept, walls drawn since) is read again, as a wall is.

/** Whether an edit is of a door, window, opening or gate (changed at once, as a rule). */
const ofOpenings = (body) => Boolean(body.add?.opening || body.resize || body.move || body.remove?.kind === "opening");

let ghosts = 0; // (each opening added, a name of its own until Studio gives it its ID)

/** A change drawn as it will be: the openings it hides (their IDs), and those it shows. */
const ghost = (hide, show) => ({ hide, show: show.map((d) => ({ ...d, pending: true })), until: null });

/** An opening added ({type, span, swing}), as the floor will list it. */
function addedOpening(o) {
  const [a, b] = o.span;
  return { id: `pending-${++ghosts}`, type: o.type, span: o.span, point: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2],
    swings: o.swing ? [o.swing] : [], drawn: true };
}

/** An opening slid along its wall by (dx, dy): its span, its mark and its leaves with it. */
function shifted(d, dx, dy) {
  const by = ([x, y]) => [x + dx, y + dy];
  return { ...d, span: d.span.map(by), point: by(d.point), middle: d.middle && by(d.middle),
    swings: (d.swings || []).map((leaf) => leaf.map(by)) };
}

/** An opening of another width, about its middle; a door's leaves on their jambs, as long as it is wide. */
function widened(d, width) {
  const [a, b] = [d.span[0], d.span.at(-1)];
  const w = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1, u = [(b[0] - a[0]) / w, (b[1] - a[1]) / w];
  const m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], h = width / 2;
  const span = [[m[0] - u[0] * h, m[1] - u[1] * h], [m[0] + u[0] * h, m[1] + u[1] * h]];
  const swings = (d.swings || []).map(([hinge, free]) => {
    const jamb = Math.hypot(hinge[0] - a[0], hinge[1] - a[1]) <= Math.hypot(hinge[0] - b[0], hinge[1] - b[1]) ? span[0] : span[1];
    const k = width / w;
    return [jamb, [jamb[0] + (free[0] - hinge[0]) * k, jamb[1] + (free[1] - hinge[1]) * k]];
  });
  return { ...d, span, swings, width };
}

/** What a change of an opening ``d`` (resize's or move's body) will make of it, drawn so. */
function changed(d, body) {
  if (!d?.span) return null;
  if (body.move) {
    const u = along(d), t = (body.move.to[0] - d.middle[0]) * u[0] + (body.move.to[1] - d.middle[1]) * u[1];
    return ghost([d.id], [shifted(d, u[0] * t, u[1] * t)]);
  }
  const r = body.resize;
  let out = r.width ? widened(d, r.width) : { ...d };
  if (r.type !== undefined && r.type !== null) out = { ...out, type: r.type, swings: r.type === "door" ? out.swings : [] };
  if (r.swing) out = { ...out, swings: [r.swing] };
  return ghost([d.id], [out]);
}

/** The ghosts the floor shown has caught up with (its version, at least the one each was saved
 * as) let go; those of another floor too. */
function settle() {
  const before = state.ghosts.length;
  state.ghosts = state.ghosts.filter((g) => g.floor === state.floor?.id && !(g.until != null && (state.floor?.version ?? -1) >= g.until));
  if (state.ghosts.length !== before) renderDoors();
}

export async function submitEdit(body, saying, after = null, shown = null) {
  if (viewOnly()) return;
  // a wall or dividing line: drawn at once, the next one drawn while it is read (Studio
  // reads them in turn); a door, window, opening or gate: drawn as it will be, the next one
  // made at once (Studio changes them with no read); anything else is waited for
  const line = body.add?.wall ? ["wall", body.add.wall] : body.add?.divider ? ["divider", body.add.divider] : null;
  const quick = ofOpenings(body);
  const floorId = state.floor.id;
  if (line) {
    state.pending.push(line);
    renderDrawn();
  } else if (quick) {
    if (shown) {
      shown.floor = floorId;
      state.ghosts.push(shown);
      clearPreview();
      renderDoors();
    }
  } else {
    state.busy = true;
  }
  if (!quick) $("map").classList.add("busy");
  let read = !quick;
  try {
    const answer = await request(`${BASE}/floors/${floorId}/edits`, body);
    if (answer.at_once) { // changed at once: the floor's openings as Studio has them now
      if (shown) shown.until = answer.version ?? null;
      if (state.floor?.id === floorId) await refreshFloor(null);
      if (body.remove) toast("Taken away");
    } else {
      read = true;
      if (quick) $("map").classList.add("busy");
      await followJob(answer, saying);
      await refreshProject();
      view3d.dirty.add(floorId); // read again with it: built again in 3D (that floor alone)
      state.busy = false;
      if (state.floor?.id === floorId) await refreshFloor(null); // what the read changed, drawn in place
      toast(body.remove ? "Taken away" : body.resize ? "Size changed; the floor was read again" : "Added; the floor was read again");
    }
    after?.();
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
  } finally {
    if (shown) { // (refused, or read: as Studio has it; changed at once: until the floor shown has caught up)
      if (shown.until == null) state.ghosts = state.ghosts.filter((g) => g !== shown);
      settle();
      renderDoors();
    }
    if (line) {
      state.pending = state.pending.filter((x) => x !== line);
      renderDrawn();
    } else if (!quick) {
      state.busy = false;
      clearPreview();
    }
    if (read && !state.busy && !state.pending.length) $("map").classList.remove("busy");
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

/** The length of a drawn line chosen ({kind, at}), or null. */
export function drawnLength(item) {
  const list = item.kind === "wall" ? state.floor?.edits?.walls : state.floor?.edits?.dividers;
  const w = (list || []).find(([a, b]) => `${(a[0] + b[0]) / 2},${(a[1] + b[1]) / 2}` === item.at.join(","));
  return w ? Math.hypot(w[1][0] - w[0][0], w[1][1] - w[0][1]) : null;
}

/** The space drawn here (its ring, as saved) a point is in, or null. */
export function drawnSpaceAt(p) {
  return (state.floor?.edits?.spaces || []).find((ring) => inRing(p, ring)) || null;
}

/** Taking away what was drawn here: its kind and its points as saved (the server
 * takes that one, never another drawn near it), and where it is, for an older server. */
export function takeAway(kind, at, shape, d = null) {
  return submitEdit({ remove: { kind, at, ...(shape ? { shape } : {}) } }, "Taking it away…", null,
    d ? ghost([d.id], []) : null);
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
  const name = d.type === "window" ? "Window" : d.type === "door" ? "Door" : d.type === "gate" ? "Gate" : "Opening";
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

/** A form of a door's, window's or opening's sizes; ``done`` after it is sent. */
export function sizeForm(d, { done = () => {}, compact = false } = {}) {
  const row = (name, label, min, max, cls = "") => el("label", { class: `size-row ${cls}`.trim() }, el("span", {}, label),
    el("span", { class: "size-input" }, el("input", { name, type: "number", min, max, step: "0.05", placeholder: "as drawn",
      inputmode: "decimal" }), el("span", { class: "unit" }, "m")));
  const form = el("form", { class: `size-form${compact ? " compact" : ""}`, autocomplete: "off" },
    row("width", "Width", "0.3", "8"), row("sill", "Sill", "0", "3", "sill"), row("height", "Height", "0.3", "10"),
    el("div", { class: "size-actions" },
      el("button", { type: "submit", class: "btn-primary btn-sm" }, "Change size"),
      el("button", { type: "button", class: "btn-sm as-drawn", "data-tip": "Back to the size the drawing gives it" }, "Size as drawn")));
  form.elements.width.value = fmt(d.width);
  form.elements.sill.value = fmt(d.sill);
  form.elements.height.value = fmt(d.height);
  form.querySelector(".sill").hidden = d.type !== "window";
  form.querySelector(".as-drawn").hidden = d.drawn || !d.resize;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    done();
    resize(d, sizesFrom(form, d));
  });
  form.querySelector(".as-drawn").addEventListener("click", () => {
    done();
    resize(d, { width: null, sill: null, height: null });
  });
  return form;
}

/** The sizes to keep: a field left as it was keeps what it was given (or as drawn). */
function sizesFrom(form, d) {
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
  const body = { resize: { at: d.middle, ...sizes } };
  return submitEdit(body, "Changing the size…", () => selectItem({ kind: "door", id: d.id }), changed(d, body));
}

/** A door, window or opening chosen, as the floor has it (null: none chosen). */
export const chosenOpening = () => (state.item?.kind === "door" ? (state.floor?.doors || []).find((d) => d.id === state.item.id && !d.ignored) ?? null : null);

/** Its direction along its wall: a unit vector from one jamb to the other (null: no span). */
function along(d) {
  if (!d?.span) return null;
  const [a, b] = [d.span[0], d.span.at(-1)];
  const n = Math.hypot(b[0] - a[0], b[1] - a[1]);
  return n ? [(b[0] - a[0]) / n, (b[1] - a[1]) / n] : null;
}

/** An opening slid ``t`` metres along its wall: drawn where it would go (the preview), and its
 * middle there. */
function slid(d, t) {
  const u = along(d);
  const span = d.span.map(([x, y]) => [x + u[0] * t, y + u[1] * t]);
  for (const id of ["preview", "preview-print"]) {
    $(id).replaceChildren(svg("line", { x1: span[0][0], y1: span[0][1], x2: span[1][0], y2: span[1][1], class: d.type }));
  }
  return [d.middle[0] + u[0] * t, d.middle[1] + u[1] * t];
}

/** A drag of the opening chosen from ``from`` to ``p`` (plan points): it slides along its
 * wall as far as the pointer went along it. Its middle there. */
export function slideOpening(d, from, p) {
  const u = along(d);
  return u ? slid(d, (p[0] - from[0]) * u[0] + (p[1] - from[1]) * u[1]) : null;
}

/** The opening moved so its middle is at ``to`` along its wall (at once). */
export function moveOpening(d, to) {
  const body = { move: { at: d.middle, to } };
  return submitEdit(body, `Moving the ${d.type === "opening" ? "opening" : d.type}…`, () => {
    selectItem({ kind: "door", id: d.id });
  }, changed(d, body));
}

// the arrows on an opening chosen: its slide gathered while they are pressed, sent once they stop
// (soon: Studio moves it at once)
let nudged = null; // { d, t, timer }
const NUDGE_SETTLE_MS = 200;

/** An arrow on the opening chosen: along its wall by ``step`` metres, the way the arrow
 * points as far as its wall runs that way; one across its wall says which arrows move it. */
function nudgeOpening(dx, dy, step) {
  const d = chosenOpening(), u = along(d);
  if (!d || !u) return;
  const k = dx * u[0] + dy * u[1];
  if (Math.abs(k) < 0.3) return toast(`It moves along its wall: ${Math.abs(u[0]) > Math.abs(u[1]) ? "← and →" : "↑ and ↓"} here`);
  if (nudged?.d.id !== d.id) nudged = { d, t: 0, timer: null };
  nudged.t += Math.sign(k) * step;
  slid(d, nudged.t);
  clearTimeout(nudged.timer);
  nudged.timer = setTimeout(() => {
    const { d: was, t } = nudged;
    nudged = null;
    if (Math.abs(t) >= 0.005) moveOpening(was, slid(was, t));
    else clearPreview();
  }, NUDGE_SETTLE_MS);
}

/** A door's way (TURNS: bit 1 the jamb it hangs on, bit 2 the side it opens to) as it hangs
 * now: -1 when it has no leaf. */
function wayOf(d) {
  const leaf = d.swings?.[0], u = along(d);
  if (!leaf || !u) return -1;
  const [a, b] = [d.span[0], d.span.at(-1)];
  const h = Math.hypot(leaf[0][0] - b[0], leaf[0][1] - b[1]) < Math.hypot(leaf[0][0] - a[0], leaf[0][1] - a[1]) ? 1 : 0;
  const side = (leaf[1][0] - leaf[0][0]) * -u[1] + (leaf[1][1] - leaf[0][1]) * u[0] >= 0 ? 0 : 2;
  return h | side;
}

/** A door's leaf for a way (TURNS) on its span: [hinge, free edge], across its wall. */
function leafOfWay(d, way) {
  const u = along(d), [a, b] = [d.span[0], d.span.at(-1)];
  const w = Math.hypot(b[0] - a[0], b[1] - a[1]), side = way & 2 ? -1 : 1;
  const hinge = way & 1 ? b : a;
  return [hinge, [hinge[0] - u[1] * side * w, hinge[1] + u[0] * side * w]];
}

// R on a door chosen: its turns gathered while pressed, sent once they stop
let turning = null; // { d, way, timer }

/** R (Shift: back) on the door chosen: turned to its next way (TURNS), shown at once, the
 * floor read again with it once the presses stop. One with no leaf gets its first. */
export function turnChosenDoor(by) {
  const d = chosenOpening();
  if (!d?.span) return;
  if (d.type !== "door") return toast(`A ${d.type} has no door to turn: make it a door first`);
  if (turning?.d.id !== d.id) turning = { d, way: wayOf(d), timer: null };
  const i = TURNS.indexOf(turning.way);
  turning.way = i < 0 ? TURNS[0] : TURNS[(i + (by > 0 ? 1 : 3)) % 4];
  const leaf = leafOfWay(d, turning.way);
  for (const id of ["preview", "preview-print"]) $(id).replaceChildren(svg("path", { d: swingPath(d.span, leaf), class: "swing" }));
  clearTimeout(turning.timer);
  turning.timer = setTimeout(() => {
    const { d: was, way } = turning;
    turning = null;
    if (way === wayOf(was)) return clearPreview();
    const body = { resize: { at: was.middle, swing: leafOfWay(was, way) } };
    submitEdit(body, "Turning the door…", () => selectItem({ kind: "door", id: was.id }), changed(was, body));
  }, NUDGE_SETTLE_MS);
}

/** A door, window or opening said to be another (null: as the drawing reads it): one the
 * drawing reads wrong set right, kept through every re-read (at once). */
export function retype(d, type) {
  const body = { resize: { at: d.middle, type } };
  return submitEdit(body, type ? `Making it ${type === "opening" ? "an opening" : `a ${type}`}…`
    : "Back to what the drawing says…", () => selectItem({ kind: "door", id: d.id }), type ? changed(d, body) : null);
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
  }, ghost([], [addedOpening({ type, span })]));
}

export function startSpace(p) {
  setTool("space");
  if (state.tool !== "space") return;
  const at = snapWall(p);
  state.corners = [at];
  state.wallStart = at;
  preview(p);
  emit("tool-progress");
}

export function startLine(kind, p) {
  setTool(kind);
  if (state.tool !== kind) return;
  state.wallStart = snapWall(p);
  preview(p);
  emit("tool-progress");
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
    return takeAway("opening", at, span, d);
  }
  if (roomTaken(...(d.connects || []))) return;
  try {
    const updated = await request(`${BASE}/objects/${d.id}`, { ignored: !d.ignored });
    Object.assign(d, updated);
    dirty3d([state.floor.id]); // a wall with it or without it, in 3D
    toast(`${openingName(d)}: ${d.ignored ? "deleted" : "restored"}`);
    if (d.ignored && !state.showHidden) selectItem(null);
    else selectItem(item);
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
  }
}

// ---- the tools ---------------------------------------------------------------------------

const ON_PLAN = (label) => () => `${label} is drawn on the plan`;
const lineHint = (label, what) => () => (state.wallStart
  ? `Click where the ${what} ends · it snaps to walls and squares up · Esc: start again`
  : `Click where the ${what} starts · ${label === "Divide" ? "right across the space: it becomes two zones, no wall" : "it snaps to walls"}`);

/** Esc in a drawing tool: the line or shape under way given up first. */
const giveUp = () => {
  if (!state.wallStart && !state.corners.length) return false;
  state.wallStart = null;
  state.corners = [];
  clearPreview();
  emit("tool-progress");
  return true;
};

const drawingPlan = { hover: preview, click: toolClick, dblclick: () => { if (shaping() && state.corners.length >= 3) closeSpace(); } };

/** The opening tool's door, drawn again where the pointer last was (a key changed it). */
function redrawOpening() {
  if (state.tool === "opening" && hovered) preview(hovered);
  emit("tool-progress");
}

// a door's four ways, a quarter turn at a time: hung on one jamb, on the other, then opening
// the other way on that one, and on the first (state.openingSwing)
const TURNS = [0, 1, 3, 2];

/** R (Shift: back): the door to add turned to its next way (TURNS). */
function turnDoor(by) {
  if (state.openingKind !== "door") return toast(`A ${state.openingKind} has no door to turn`);
  state.openingSwing = TURNS[(TURNS.indexOf(state.openingSwing) + (by > 0 ? 1 : 3)) % 4];
  redrawOpening();
}

/** [ and ]: the opening to add narrower or wider, within what it may be. */
function widen(by) {
  const v = Math.round((widthOf() + by) * 100) / 100;
  state.openingWidth = Math.min(Math.max(v, 0.3), widestOpening());
  emit("tool-options");
  redrawOpening();
}

export function setupDrawing() {
  // a door chosen: R turns it, as the door tool does before it is added (Shift: back)
  for (const [key, by, what] of [["r", 1, "Turn the door chosen: the other jamb, then the other side"], ["shift+r", -1, "Turn the door chosen back"]]) {
    command({ id: `opening.turn${by < 0 ? "-back" : ""}`, title: what, group: "Door, window", scope: "opening", keys: [key], palette: false,
      when: () => chosenOpening()?.type === "door" && Boolean(chosenOpening()?.span) && editable() && !state.floor?.layout,
      why: () => (chosenOpening() ? (chosenOpening().type !== "door" ? "Only a door turns: make it a door first" : whyNotEditable())
        : "Choose a door first"),
      run: () => turnChosenDoor(by) });
  }
  // a door, window or opening chosen: the arrows slide it along its wall (Shift: 50 cm)
  for (const [key, dx, dy] of [["arrowleft", -1, 0], ["arrowright", 1, 0], ["arrowup", 0, 1], ["arrowdown", 0, -1]]) {
    for (const [mods, step] of [["", 0.05], ["shift+", 0.5]]) {
      command({ id: `opening.move-${key.slice(5)}${mods ? "-far" : ""}`, title: `Move the door, window or opening ${key.slice(5)} along its wall${mods ? " 50 cm" : ""}`,
        group: "Door, window", scope: "opening", keys: [`${mods}${key}`], palette: false, repeat: true,
        when: () => Boolean(chosenOpening()?.span) && editable() && !state.floor?.layout,
        why: () => (chosenOpening() ? whyNotEditable() : "Choose a door, window or opening first"),
        run: () => nudgeOpening(dx, dy, step) });
    }
  }
  // what the opening tool adds follows the floor: on a location's layout a gate, else a door
  let wasLayout = false;
  on("floor", () => {
    settle(); // (changes of openings drawn as they will be: gone once the floor shown has them)
    const layout = Boolean(state.floor?.layout);
    if (layout !== wasLayout || !(state.openingKind in openingKinds())) {
      state.openingKind = layout ? "gate" : "door";
      state.openingWidth = null;
      emit("tool-options");
    }
    wasLayout = layout;
  });
  tool({ id: "wall", label: "Wall", icon: "brick-wall", key: "w", group: "draw", edits: true, drawing: true,
    wrongView: ON_PLAN("A wall"), hint: lineHint("Wall", "wall"), escape: giveUp, plan: drawingPlan, stop: clearPreview });
  tool({ id: "space", label: "Space", icon: "vector-square", key: "r", group: "draw", edits: true, drawing: true,
    words: "room area draw a space polygon colonnade",
    wrongView: ON_PLAN("A space"), escape: giveUp, plan: drawingPlan, stop: clearPreview,
    hint: () => (state.corners.length >= 3 ? "Click the next corner · click the first, double-click or Enter to close it · Backspace takes a corner back"
      : state.corners.length ? "Click the next corner (corners snap to walls) · Backspace takes it back"
        : "Click its corners: they snap to walls · for an area the drawing encloses nowhere"),
    keys: { enter: [() => closeSpace(), "close the shape"], backspace: [() => backCorner(), "take back the last corner"] } });
  tool({ id: "divide", label: "Divide", icon: "square-split-horizontal", key: "d", group: "draw", edits: true, drawing: true,
    words: "split zone dividing line", wrongView: ON_PLAN("A dividing line"), hint: lineHint("Divide", "line"), escape: giveUp,
    plan: drawingPlan, stop: clearPreview });
  tool({ id: "opening", label: "Door, window, opening", icon: "door-open", key: "o", group: "draw", edits: true, drawing: true,
    words: "door window opening doorway",
    wrongView: ON_PLAN("A door, window or opening"), plan: drawingPlan, stop: clearPreview,
    hint: () => `Click on a ${state.floor?.layout ? "boundary wall" : "wall"} where the ${state.openingKind} goes · ${(widthOf()).toFixed(2)} m wide`
      + `${state.openingKind === "door" ? " · R turns it" : ""} · [ ] narrower, wider`,
    keys: {
      r: [() => turnDoor(1), "turn the door: the other jamb, the other side"],
      "shift+r": [() => turnDoor(-1), "turn the door back"],
      "[": [() => widen(-0.05), "5 cm narrower"],
      "]": [() => widen(0.05), "5 cm wider"],
    },
    options: () => {
      const kinds = el("div", { class: "segmented", role: "radiogroup", "aria-label": "What to add" },
        ...Object.entries(openingKinds()).map(([kind, name]) => {
          const b = el("button", { type: "button", role: "radio", "aria-checked": String(state.openingKind === kind),
            "data-tip": kind === "opening" ? "A way through, no door" : kind === "gate" ? "A gate in the boundary walls, for people or cars (up to 12 m)" : `A ${kind}` }, name);
          b.addEventListener("click", () => {
            state.openingKind = kind;
            state.openingWidth = null;
            emit("tool-options");
            emit("tool-progress");
          });
          return b;
        }));
      const width = el("input", { type: "number", min: "0.3", max: String(widestOpening()), step: "0.05", "aria-label": "Width in metres",
        value: widthOf().toFixed(2) });
      // as it is typed: the door under the pointer as wide at once
      width.addEventListener("input", () => {
        const v = Number(width.value);
        if (Number.isFinite(v) && v >= 0.3 && v <= widestOpening()) {
          state.openingWidth = v;
          redrawOpening();
        }
      });
      width.addEventListener("change", () => {
        const v = Number(width.value);
        state.openingWidth = Number.isFinite(v) && v >= 0.3 && v <= widestOpening() ? v : null;
        width.value = widthOf().toFixed(2);
        emit("tool-progress");
      });
      // Enter: back to the plan, to click where it goes
      width.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); width.blur(); $("svg").focus({ preventScroll: true }); } });
      return [kinds, el("label", { class: "to-group" }, el("span", { class: "to-label" }, "Width"), width, el("span", { class: "to-label" }, "m"))];
    } });
}
