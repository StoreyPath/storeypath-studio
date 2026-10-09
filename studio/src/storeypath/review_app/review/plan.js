// The plan (2D): the floor's spaces, doors, what was drawn here and its items in SVG, over
// the floor's drawing (as printed, or its lines); its view (pan, zoom, fit, side by side
// with the print) and its labels. A pointer on it is the tools' (tools.js).

import { request } from "./api.js";
import { $, svg } from "./dom.js";
import * as finish from "./finish.js";
import { say } from "./notify.js";
import { isChosen } from "./selection.js";
import { BASE, code, color, divided, extent, matches, pathData, rings, state, tucked, union, units, visible } from "./state.js";

const DRAWING_ORDER = ["other", "outlines", "doors", "walls"]; // bottom to top

// ---- the drawing under the spaces ------------------------------------------------------

// The drawing under the spaces: as printed (an image Studio draws once per drawing),
// or its lines; each loaded when first shown. Side by side, the print is on the left and
// the spaces alone on the right.
//
// A room is labelled once. As printed with Studio's labels on, the print is the one drawn
// without the drawing's texts (Studio's labels are written in their place: what Studio
// read, and what a person corrected); with the labels off, the print with its texts: the
// drawing's own words (Labels, T, goes from one to the other: what Studio read against
// what the drawing says). Side by side, the print beside the rooms has its texts. Over the
// drawing's lines, its room labels (on its label layers) are hidden while Studio's are
// shown (CSS: #map.lined.labels-on).

/** Whether the print shown now has the drawing's texts. */
const printWithText = () => state.side || !state.showLabels;

export function showUnderlay() {
  const mode = state.side ? "off" : state.drawingMode;
  $("print").classList.toggle("hidden", mode !== "print");
  $("drawing").classList.toggle("hidden", mode !== "lines");
  $("map").classList.toggle("printed", mode === "print");
  $("map").classList.toggle("lined", mode === "lines");
  $("svg-print").toggleAttribute("hidden", !state.side); // an <svg> has no .hidden
  arrange();
  placeLabels();
  const id = state.floor?.id;
  if (!id || !state.floor.source) return;
  if (mode === "print" || state.side) showPrint(id, printWithText());
  if (mode === "lines" && state.underlay.lines !== id) loadDrawing(id);
}

/** The floor's drawing forgotten (another floor is shown: never its drawing under it). */
export function clearUnderlay() {
  state.drawingBounds = null;
  $("drawing").replaceChildren();
  $("print").replaceChildren();
  $("print-copy").replaceChildren();
  state.underlay = { lines: null, print: null, prints: new Map() };
}

/** The print of floor ``id`` (with its texts, or without them) under the spaces and on the
 * left side by side: at once when it was shown before, else asked for. */
function showPrint(id, text) {
  const key = `${id}:${text ? "text" : "plain"}`;
  if (state.underlay.print === key) return;
  state.underlay.prints ??= new Map();
  const made = state.underlay.prints.get(key);
  if (made) {
    state.underlay.print = key;
    $("print").replaceChildren(made);
    $("print-copy").replaceChildren(made.cloneNode(true));
    return;
  }
  loadPrint(id, text, key);
}

async function loadPrint(id, text, key) {
  state.underlay.print = key;
  say("Drawing the print… (the first time for a floor can take a minute)");
  const q = text ? "" : "?text=0";
  try {
    const info = await request(`${BASE}/floors/${id}/print${q}`);
    if (state.floor?.id !== id) return;
    const [x0, y0, x1, y1] = info.bounds;
    const image = svg("image", {
      x: x0, y: y0, width: x1 - x0, height: y1 - y0, preserveAspectRatio: "none",
      href: `/api/${BASE}/floors/${encodeURIComponent(id)}/print.png?k=${info.key}${text ? "" : "&text=0"}`,
    });
    // The world is drawn with y up; an image is drawn with y down: flip it about its middle.
    const g = svg("g", { transform: `matrix(1 0 0 -1 0 ${y0 + y1})` });
    g.append(image);
    image.addEventListener("load", () => { if (state.floor?.id === id) say(""); });
    image.addEventListener("error", () => { if (state.floor?.id === id) say("The print could not be shown"); });
    state.underlay.prints.set(key, g);
    if (state.underlay.print === key) { // (still the one wanted)
      $("print").replaceChildren(g);
      $("print-copy").replaceChildren(g.cloneNode(true));
    }
    if (!state.drawingBounds) state.drawingBounds = info.bounds;
    if (!state.floor.spaces.length && !state.floor.outline) fit();
  } catch (e) {
    if (state.underlay.print === key) state.underlay.print = null;
    if (state.floor?.id === id) say(`Print not shown: ${e.message}`);
  }
}

async function loadDrawing(id) {
  state.underlay.lines = id;
  say("Loading drawing…");
  try {
    const drawing = await request(`${BASE}/floors/${id}/drawing`);
    if (state.floor?.id !== id) return;
    renderDrawing(drawing);
    say("");
    if (!state.floor.spaces.length && !state.floor.outline) fit();
  } catch (e) {
    state.underlay.lines = null;
    if (state.floor?.id === id) say(`Drawing not shown: ${e.message}`);
  }
}

function renderDrawing({ groups, texts }) {
  const g = $("drawing");
  g.replaceChildren();
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const name of DRAWING_ORDER) {
    const lines = groups[name];
    if (!lines?.length) continue;
    const parts = [];
    for (const pts of lines) {
      let d = `M${pts[0]} ${pts[1]}`;
      for (let i = 2; i < pts.length; i += 2) d += `L${pts[i]} ${pts[i + 1]}`;
      parts.push(d);
      for (let i = 0; i < pts.length; i += 2) {
        if (pts[i] < x0) x0 = pts[i];
        if (pts[i] > x1) x1 = pts[i];
        if (pts[i + 1] < y0) y0 = pts[i + 1];
        if (pts[i + 1] > y1) y1 = pts[i + 1];
      }
    }
    g.append(svg("path", { class: name, d: parts.join("") }));
  }
  for (const [x, y, h, rotation, lines, isLabel] of texts) {
    if (!h) continue;
    const t = svg("text", {
      transform: `translate(${x} ${y}) scale(1 -1) rotate(${-rotation})`,
      "font-size": h,
      "text-anchor": "middle",
      class: isLabel ? "label" : "",
    });
    lines.forEach((line, i) => {
      const span = svg("tspan", { x: 0, y: (i - (lines.length - 1) / 2) * h * 1.4, "dominant-baseline": "central" });
      span.textContent = line;
      t.append(span);
    });
    g.append(t);
  }
  state.drawingBounds = x0 <= x1 ? [x0, y0, x1, y1] : null;
}

// ---- the floor's spaces, doors and what was drawn ---------------------------------------

export function renderPlan() {
  const f = state.floor;
  $("outline").setAttribute("d", f.outline ? pathData(f.outline) : "");

  const spaces = $("spaces");
  const labels = $("labels");
  spaces.replaceChildren();
  labels.replaceChildren();
  state.paths.clear();
  state.labels.clear();
  state.bounds.clear();
  for (const s of f.spaces.filter(divided)) { // under its zones: just its outline, along the walls
    spaces.append(svg("path", { d: pathData(s.geometry), class: "divided" }));
    state.bounds.set(s.id, extent(s.geometry));
  }
  for (const s of units()) {
    const path = svg("path", { d: pathData(s.geometry) });
    path.dataset.id = s.id;
    if (s.kind === "zone") path.classList.add("zone");
    spaces.append(path);
    state.paths.set(s.id, path);
    state.bounds.set(s.id, extent(s.geometry));
    const label = svg("text");
    labels.append(label);
    state.labels.set(s.id, label);
    styleSpace(s);
  }
  // A line Studio drew to divide an open area (no wall in the drawing): thin, light and
  // dashed over the rooms, where walls show as dark gaps between them.
  const dividers = f.doors.flatMap((d) => d.divider || []).map((line) => line.map((p) => p.join(",")).join(" "));
  // Doors as a bar across their opening, jamb to jamb (dashed: a doorway with no door;
  // thin: a window), with a mark in the middle; where the span is not known, the mark.
  // A door whose swing was drawn is shown as plans show it: the leaf open, and the arc
  // it sweeps to shut.
  const kind = (d) => (d.type === "door" ? "door" : d.type === "window" ? "window" : "way");
  const swung = f.doors.filter((d) => d.type === "door" && d.span && d.swings?.length);
  const leaf = (d, [h, q]) => {
    const from = (p) => Math.hypot(p[0] - h[0], p[1] - h[1]);
    const jamb = from(d.span[0]) > from(d.span[1]) ? d.span[0] : d.span[1]; // the other side
    const r = from(q), k = r / (from(jamb) || 1);
    const shut = [h[0] + (jamb[0] - h[0]) * k, h[1] + (jamb[1] - h[1]) * k];
    const sweep = (q[0] - h[0]) * (shut[1] - h[1]) - (q[1] - h[1]) * (shut[0] - h[0]) > 0 ? 1 : 0;
    return svg("path", { d: `M${h} L${q} A${r},${r} 0 0 ${sweep} ${shut}`, class: `swing${state.item?.id === d.id ? " selected" : ""}` });
  };
  const shown = f.doors.filter((d) => state.showHidden || !d.ignored); // deleted: left out
  $("doors").replaceChildren(
    ...dividers.map((points) => svg("polyline", { points, class: "divider" })),
    ...shown.filter((d) => d.span && !swung.includes(d)).map((d) => svg("line", {
      x1: d.span[0][0], y1: d.span[0][1], x2: d.span[1][0], y2: d.span[1][1],
      class: `span ${kind(d)}${d.ignored ? " deleted" : ""}${state.item?.id === d.id ? " selected" : ""}` })),
    ...swung.filter((d) => shown.includes(d)).flatMap((d) => d.swings.map((s) => leaf(d, s))),
    ...shown.map((d) => {
      const mark = svg("circle", { cx: d.point[0], cy: d.point[1], r: d.type === "window" ? 0.07 : 0.16,
        class: `${kind(d)}${d.ignored ? " deleted" : ""}${state.item?.id === d.id ? " selected" : ""}` });
      mark.dataset.door = d.id;
      return mark;
    }),
  );
  // Walls and dividing lines drawn in review, over the rest: chosen by a click, to
  // take them away
  const drawn = [...(f.edits?.walls || []).map((w) => ["wall", w]), ...(f.edits?.dividers || []).map((d) => ["divider", d])];
  $("drawn").replaceChildren(...drawn.flatMap(([kind_, [a, b]]) => {
    const at = `${(a[0] + b[0]) / 2},${(a[1] + b[1]) / 2}`;
    const chosen = state.item?.kind === kind_ && state.item.at.join(",") === at;
    return ["hit", `${kind_ === "divider" ? "divide" : ""}${chosen ? " selected" : ""}`].map((cls) => {
      const line = svg("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: cls });
      line.dataset.at = at;
      line.dataset.kind = kind_;
      return line;
    });
  }));
  placeLabels();
}

export function styleSpace(s) {
  const path = state.paths.get(s.id);
  if (!path || divided(s)) return;
  path.style.fill = finish.fill(s) ?? color(s.type); // (coloured by floor finish: its finish's tone)
  path.style.display = visible(s) ? "" : "none";
  path.classList.toggle("tucked", tucked(s));
  path.classList.toggle("review", s.reasons.length > 0);
  path.classList.toggle("selected", isChosen(s.id));
  path.classList.toggle("focus", s.id === state.focus);
  path.classList.toggle("dim", !matches(s, state.filter));
  const label = state.labels.get(s.id);
  label.dataset.name = s.name || "";
  label.dataset.number = s.number || (s.name ? "" : code(s.id));
  placeLabels(); // its label written again (once a frame, however many are restyled)
}

/** Every space styled again (the colours changed, a filter, deleted shown). */
export function restyle() {
  for (const s of state.floor?.spaces || []) styleSpace(s);
  placeLabels();
}

// ---- view: pan, zoom, labels ---------------------------------------------

const viewListeners = new Set();

/** ``fn()`` whenever the plan's view moves (what is written in the screen's pixels: placed again). */
export const onViewChange = (fn) => viewListeners.add(fn);

export function updateView() {
  const { k, tx, ty } = state.view;
  $("world").setAttribute("transform", `matrix(${k} 0 0 ${-k} ${tx} ${ty})`);
  $("world-print").setAttribute("transform", `matrix(${k} 0 0 ${-k} ${tx} ${ty})`); // the same view, side by side
  placeLabels();
  for (const fn of viewListeners) fn();
}

// Side by side, the floor is split the way it shows larger: a wide floor above and
// below, a tall one left and right. When the split changes, the floor is fitted again.
export function arrange() {
  let layout = "single";
  const b = floorBounds();
  if (state.side) {
    layout = "cols";
    const r = $("map").getBoundingClientRect();
    if (b && r.width && r.height) {
      const w = Math.max(b[2] - b[0], 0.5);
      const h = Math.max(b[3] - b[1], 0.5);
      const cols = Math.min(r.width / 2 / w, r.height / h);
      const rows = Math.min(r.width / w, r.height / 2 / h);
      if (rows > cols) layout = "rows";
    }
  }
  const map = $("map");
  map.classList.toggle("side", layout !== "single");
  map.classList.toggle("rows", layout === "rows");
  map.classList.toggle("cols", layout === "cols");
  if (layout !== state.layout) {
    state.layout = layout;
    // Fitted again once the panes have their new size; until then a resize fits too,
    // rather than keeping the old middle.
    state.refit = true;
    requestAnimationFrame(() => {
      fit();
      requestAnimationFrame(() => { state.refit = false; });
    });
  }
}

// The space under a point of the plan (world meters), topmost first.
export function spaceAt(x, y) {
  const spaces = units();
  for (let i = spaces.length - 1; i >= 0; i--) {
    const s = spaces[i];
    const b = state.bounds.get(s.id);
    if (!visible(s) || !b || x < b[0] || x > b[2] || y < b[1] || y > b[3]) continue;
    let inside = false;
    for (const ring of rings(s.geometry)) {
      for (let j = 0, k = ring.length - 1; j < ring.length; k = j++) {
        const [xj, yj] = ring[j];
        const [xk, yk] = ring[k];
        if ((yj > y) !== (yk > y) && x < ((xk - xj) * (y - yj)) / (yk - yj) + xj) inside = !inside;
      }
    }
    if (inside) return s;
  }
  return null;
}

// Side by side, a cross on one side shows where the pointer is on the other.
export function showCursor(pane, sx, sy) {
  const other = $(pane === $("svg") ? "cursor-print" : "cursor-main");
  $(pane === $("svg") ? "cursor-main" : "cursor-print").replaceChildren();
  if (!state.side || sx === null) {
    other.replaceChildren();
    return;
  }
  other.replaceChildren(
    svg("line", { x1: sx - 14, y1: sy, x2: sx + 14, y2: sy }),
    svg("line", { x1: sx, y1: sy - 14, x2: sx, y2: sy + 14 }),
    svg("circle", { cx: sx, cy: sy, r: 4 }),
  );
}

let labelFrame = 0;
export function placeLabels() {
  if (labelFrame) return;
  labelFrame = requestAnimationFrame(() => {
    labelFrame = 0;
    const { k, tx, ty } = state.view;
    for (const s of units()) {
      const label = state.labels.get(s.id);
      if (!label) continue;
      if (!visible(s)) {
        label.replaceChildren();
        continue;
      }
      const w = s.label_room[0] * k; // room for the label where it sits
      const h = s.label_room[1] * k;
      if (w < 44 || h < 18) {
        label.replaceChildren();
        continue;
      }
      const sx = s.label_point[0] * k + tx;
      const sy = -s.label_point[1] * k + ty;
      const max = Math.max(3, Math.floor(w / 7.5));
      const fitText = (text) => (text.length > max ? text.slice(0, max - 1) + "…" : text);
      let lines = [label.dataset.name, label.dataset.number].filter(Boolean);
      // a name that does not fit, beside a number that does: the number (rooms go by it)
      if (lines.length === 2 && lines[0].length > max && lines[1].length <= max) lines = [lines[1]];
      const both = lines.length === 2 && h >= 34;
      const shown = both ? lines : lines.slice(0, 1);
      label.replaceChildren(...shown.map((text, i) => {
        const span = svg("tspan", { x: sx, y: sy + (both ? (i === 0 ? -3 : 12) : 4) });
        if (text === label.dataset.number && text !== label.dataset.name) span.setAttribute("class", "num");
        span.textContent = fitText(text);
        return span;
      }));
    }
  });
}

export function viewport() {
  const r = $("svg").getBoundingClientRect();
  return { w: r.width, h: r.height, left: r.left, top: r.top };
}

export function floorBounds() {
  const f = state.floor;
  if (!f) return null;
  return union([...state.bounds.values(), extent(f.outline)]) || state.drawingBounds;
}

export function scaleFor(bounds, pad = 40) {
  const { w, h } = viewport();
  const [x0, y0, x1, y1] = bounds;
  return Math.min((w - 2 * pad) / Math.max(x1 - x0, 0.5), (h - 2 * pad) / Math.max(y1 - y0, 0.5));
}

export function centerOn(bounds, k) {
  const { w, h } = viewport();
  const [x0, y0, x1, y1] = bounds;
  state.view = { k, tx: w / 2 - ((x0 + x1) / 2) * k, ty: h / 2 + ((y0 + y1) / 2) * k };
  updateView();
}

export function fit() {
  const bounds = floorBounds();
  const { w, h } = viewport();
  if (!bounds || !w || !h) return;
  state.fitted = true;
  centerOn(bounds, scaleFor(bounds));
}

/** A space brought into view when it is not (well) in it: at about a third of the view,
 * never zoomed out beyond the whole floor; moved there smoothly (``always``: centred on it
 * even when it is in view, as reviewing one room after another does). */
export function flyToSpace(s, { always = false } = {}) {
  const b = state.bounds.get(s?.id);
  const floor = floorBounds();
  if (!b || !floor) return;
  const { k, tx, ty } = state.view;
  const { w, h } = viewport();
  if (!w || !h) return;
  const [sx0, sy0, sx1, sy1] = [b[0] * k + tx, -b[3] * k + ty, b[2] * k + tx, -b[1] * k + ty];
  const inView = sx0 >= 0 && sy0 >= 0 && sx1 <= w && sy1 <= h && sx1 - sx0 >= 60;
  if (inView && !always) return;
  const pad = Math.max(b[2] - b[0], b[3] - b[1], 2);
  const around = [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
  const nk = Math.max(scaleFor(floor), scaleFor(around, 20));
  animateTo({ k: nk, tx: w / 2 - ((b[0] + b[2]) / 2) * nk, ty: h / 2 + ((b[1] + b[3]) / 2) * nk });
}

let flight = 0;
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The view moved to ``to`` ({k, tx, ty}) smoothly: zooming in log steps, the point in the
 * middle travelling straight; at once when motion is reduced, or the plan is hidden. */
export function animateTo(to, ms = 360) {
  cancelAnimationFrame(flight);
  const from = { ...state.view };
  const { w, h } = viewport();
  if (reduced() || !w || !h) {
    state.view = to;
    return updateView();
  }
  // the world point at the middle of the view, before and after
  const mid = (v) => [(w / 2 - v.tx) / v.k, (v.ty - h / 2) / v.k];
  const [a, b] = [mid(from), mid(to)];
  const t0 = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3);
  const step = (now) => {
    const t = Math.min(1, (now - t0) / ms), e = ease(t);
    const k = Math.exp(Math.log(from.k) + (Math.log(to.k) - Math.log(from.k)) * e);
    const c = [a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e];
    state.view = { k, tx: w / 2 - c[0] * k, ty: h / 2 + c[1] * k };
    updateView();
    if (t < 1) flight = requestAnimationFrame(step);
    else {
      state.view = to;
      updateView();
    }
  };
  flight = requestAnimationFrame(step);
}

/** The view's flight stopped (the person moved the plan themselves). */
export const stopFlight = () => cancelAnimationFrame(flight);

/** Zoomed about the middle of the view (the zoom buttons, + and −). */
export function zoomBy(factor) {
  const { w, h } = viewport();
  stopFlight();
  zoomAt(w / 2, h / 2, factor);
}

/** The plan moved by (dx, dy) pixels (the arrows). */
export function panBy(dx, dy) {
  stopFlight();
  state.view = { ...state.view, tx: state.view.tx + dx, ty: state.view.ty + dy };
  updateView();
}

export function zoomAt(sx, sy, factor) {
  const { k, tx, ty } = state.view;
  const floor = floorBounds();
  const min = floor ? scaleFor(floor) / 8 : 0.01;
  const nk = Math.min(Math.max(k * factor, min), 4000);
  const wx = (sx - tx) / k;
  const wy = (ty - sy) / k;
  state.view = { k: nk, tx: sx - wx * nk, ty: sy + wy * nk };
  updateView();
}

export function planPoint(sx, sy) {
  const { k, tx, ty } = state.view;
  return [(sx - tx) / k, (ty - sy) / k];
}

/** The plan kept in the middle when the window changes size, or the view is split side by side. */
export function setupPlan() {
  let size = null;
  new ResizeObserver(() => {
    const { w, h } = viewport();
    if (!w || !h) return; // hidden (the floor in 3D, or walked): the plan keeps its view for when it is back
    if (!state.fitted || state.refit) fit();
    else if (size) {
      state.view = { ...state.view, tx: state.view.tx + (w - size.w) / 2, ty: state.view.ty + (h - size.h) / 2 };
      updateView();
    }
    size = { w, h };
  }).observe($("svg"));
  new ResizeObserver(() => { if (state.side) arrange(); }).observe($("map")); // a wider or taller window
}
