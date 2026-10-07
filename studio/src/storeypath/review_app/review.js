// StoreyPath Review: check a converted project floor by floor and correct its spaces.
// Part of StoreyPath Studio (see server.py); the project is ?p=<code>. Every
// correction is saved to the workspace file at once.

import { TYPE_COLORS, typeLabel } from "./theme.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const DRAWING_ORDER = ["other", "outlines", "doors", "walls"]; // bottom to top
const LIST_LIMIT = 400;
const $ = (id) => document.getElementById(id);
const CODE = new URLSearchParams(location.search).get("p");
const BASE = `projects/${encodeURIComponent(CODE || "")}`;

const state = {
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
  fitted: false,
  filter: "",
  item: null, // a door, window or drawn line chosen: {kind: "door", id}, or {kind: "wall" or "divider", at}
  tool: null, // drawing: "wall", "divide", "door", "window" or "opening"
  wallStart: null, // a wall or dividing line being drawn: where it starts
  busy: false, // an edit is being saved and the floor read again
  segments: null, // the floor's wall edges, for snapping to
};

// ---- helpers -------------------------------------------------------------

// Per-browser conveniences (how the drawing is shown); never needed to work.
function saved(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // not remembered; fine
  }
}

function savedMode() {
  const mode = saved("storeypath.drawing");
  return ["print", "lines", "off"].includes(mode) ? mode : "print";
}

async function request(path, body) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(`/api/${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else e.setAttribute(k, v);
  }
  e.append(...children.filter((c) => c !== null && c !== undefined));
  return e;
}

function svg(tag, attrs = {}) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

const color = (type) => TYPE_COLORS[type] || TYPE_COLORS.unspecified;
const code = (id) => id.split("-").at(-1);
const title = (s) => [s.name, s.number].filter(Boolean).join(" ") || "Unnamed space";
const swatch = (type) => el("span", { class: "swatch", style: `background:${color(type)}` });

let toastTimer;
function toast(message, error = false) {
  const t = $("toast");
  t.textContent = message;
  t.classList.toggle("error", error);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), error ? 8000 : 2500);
}

function rings(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat();
  return [];
}

function pathData(geometry) {
  return rings(geometry)
    .map((ring) => "M" + ring.map(([x, y]) => `${x} ${y}`).join("L") + "Z")
    .join("");
}

function extent(geometry) {
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

function union(boxes) {
  const valid = boxes.filter(Boolean);
  if (!valid.length) return null;
  return [
    Math.min(...valid.map((b) => b[0])), Math.min(...valid.map((b) => b[1])),
    Math.max(...valid.map((b) => b[2])), Math.max(...valid.map((b) => b[3])),
  ];
}

// ---- project and floors --------------------------------------------------

async function start() {
  if (!CODE) {
    location.href = "/";
    return;
  }
  $("back").href = `/#/p/${encodeURIComponent(CODE)}`;
  try {
    state.project = await request(`${BASE}/review`);
  } catch (e) {
    toast(`Cannot load the project: ${e.message}`, true);
    return;
  }
  const { project, file, floors, types } = state.project;
  document.title = `${project.name} · StoreyPath Review`;
  $("project-name").textContent = project.name;
  $("project-meta").replaceChildren(el("code", {}, project.id), ` · ${file}`);
  $("ed-type").replaceChildren(...types.map((t) => el("option", { value: t }, typeLabel(t))));
  fillFloorSelect();

  const hash = new URLSearchParams(location.hash.slice(1));
  const first = floors.find((f) => f.id === hash.get("floor")) || floors.find((f) => f.review) || floors[0];
  if (!first) {
    $("floor-meta").textContent = "This project has no floors yet (add them with `storeypath add-floor`).";
    return;
  }
  await openFloor(first.id, hash.get("space"));
}

function floorOptionText(f) {
  const review = f.review ? ` · ${f.review} to review` : "";
  return f.converted ? `${f.name}${review}` : `${f.name} · not converted`;
}

function fillFloorSelect() {
  const groups = new Map();
  for (const f of state.project.floors) {
    const key = `${f.location} · ${f.building}`;
    if (!groups.has(key)) groups.set(key, el("optgroup", { label: key }));
    groups.get(key).append(el("option", { value: f.id }, floorOptionText(f)));
  }
  $("floor").replaceChildren(...groups.values());
  if (state.floor) $("floor").value = state.floor.id;
}

async function openFloor(id, spaceId = null, { keepView = false } = {}) {
  let floor;
  try {
    floor = await request(`${BASE}/floors/${id}`);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  const changed = state.floor?.id !== id;
  state.floor = floor;
  state.byId = new Map(floor.spaces.map((s) => [s.id, s]));
  state.selected = null;
  state.item = null;
  state.segments = null;
  state.wallStart = null;
  renderItemEditor();
  if (changed) {
    state.drawingBounds = null;
    $("drawing").replaceChildren(); // never show another floor's drawing underneath
    $("print").replaceChildren();
    $("print-copy").replaceChildren();
    state.underlay = { lines: null, print: null };
  }
  $("floor").value = id;
  renderFloorMeta();
  renderPlan();
  renderLists();
  renderLegend();
  if (!keepView || changed) fit();
  select(spaceId && state.byId.has(spaceId) ? spaceId : null, { fly: Boolean(spaceId) });
  showUnderlay();
}

// The drawing under the spaces: as printed (an image Studio draws once per drawing),
// or its lines; each loaded when first shown.
// Side by side, the print is on the left and the spaces alone on the right.
function showUnderlay() {
  const mode = state.side ? "off" : state.drawingMode;
  $("print").classList.toggle("hidden", mode !== "print");
  $("drawing").classList.toggle("hidden", mode !== "lines");
  $("map").classList.toggle("printed", mode === "print");
  $("svg-print").toggleAttribute("hidden", !state.side); // an <svg> has no .hidden
  $("drawing-label").hidden = state.side;
  arrange();
  const id = state.floor?.id;
  if (!id || !state.floor.source) return;
  if ((mode === "print" || state.side) && state.underlay.print !== id) loadPrint(id);
  if (mode === "lines" && state.underlay.lines !== id) loadDrawing(id);
}

async function loadPrint(id) {
  state.underlay.print = id;
  $("status").textContent = "Drawing the print… (the first time for a floor can take a minute)";
  try {
    const info = await request(`${BASE}/floors/${id}/print`);
    if (state.floor?.id !== id) return;
    const [x0, y0, x1, y1] = info.bounds;
    const image = svg("image", {
      x: x0, y: y0, width: x1 - x0, height: y1 - y0, preserveAspectRatio: "none",
      href: `/api/${BASE}/floors/${encodeURIComponent(id)}/print.png?k=${info.key}`,
    });
    // The world is drawn with y up; an image is drawn with y down: flip it about its middle.
    const g = svg("g", { transform: `matrix(1 0 0 -1 0 ${y0 + y1})` });
    g.append(image);
    image.addEventListener("load", () => { if (state.floor?.id === id) $("status").textContent = ""; });
    image.addEventListener("error", () => { if (state.floor?.id === id) $("status").textContent = "The print could not be shown"; });
    $("print").replaceChildren(g);
    $("print-copy").replaceChildren(g.cloneNode(true));
    if (!state.drawingBounds) state.drawingBounds = info.bounds;
    if (!state.floor.spaces.length && !state.floor.outline) fit();
  } catch (e) {
    state.underlay.print = null;
    if (state.floor?.id === id) $("status").textContent = `Print not shown: ${e.message}`;
  }
}

function renderFloorMeta() {
  const f = state.floor;
  const how = { walls: "Spaces found from walls", outlines: "Spaces from room outlines" }[f.method];
  const parts = [f.converted_at ? how || "Converted" : "Not converted yet"];
  if (f.source) parts.push(f.source);
  if (f.converted_at) parts.push(new Date(f.converted_at).toLocaleString());
  $("floor-meta").textContent = parts.join(" · ");
  $("convert").disabled = !f.source;
  $("walk3d").hidden = !f.converted_at;
  $("open-print").hidden = !f.source;
  $("open-print").href = `/api/${BASE}/floors/${encodeURIComponent(f.id)}/print.png`;
  $("walk3d").href = "/viewer/examples/world/index.html?" + new URLSearchParams({
    pkg: `/api/${BASE}/preview.storeypath`, floor: f.id }); // the project as it is now

  const box = $("warnings");
  box.hidden = !f.warnings.length;
  box.querySelector("summary").textContent =
    `${f.warnings.length} warning${f.warnings.length === 1 ? "" : "s"} from the last conversion`;
  box.querySelector("ul").replaceChildren(...f.warnings.map((w) => el("li", {}, w)));
}

async function loadDrawing(id) {
  state.underlay.lines = id;
  $("status").textContent = "Loading drawing…";
  try {
    const drawing = await request(`${BASE}/floors/${id}/drawing`);
    if (state.floor?.id !== id) return;
    renderDrawing(drawing);
    $("status").textContent = "";
    if (!state.floor.spaces.length && !state.floor.outline) fit();
  } catch (e) {
    state.underlay.lines = null;
    if (state.floor?.id === id) $("status").textContent = `Drawing not shown: ${e.message}`;
  }
}

// ---- drawing the plan ----------------------------------------------------

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

function renderPlan() {
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
  $("drawn").replaceChildren(...drawn.flatMap(([kind, [a, b]]) => {
    const at = `${(a[0] + b[0]) / 2},${(a[1] + b[1]) / 2}`;
    const chosen = state.item?.kind === kind && state.item.at.join(",") === at;
    return ["hit", `${kind === "divider" ? "divide" : ""}${chosen ? " selected" : ""}`].map((cls) => {
      const line = svg("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: cls });
      line.dataset.at = at;
      line.dataset.kind = kind;
      return line;
    });
  }));
  placeLabels();
}

function matches(s, q) {
  if (!q) return true;
  return [s.id, s.name, s.number, typeLabel(s.type), s.type]
    .some((v) => v && v.toLowerCase().includes(q));
}

const tucked = (s) => s.hidden || s.ignored;
const visible = (s) => state.showHidden || !tucked(s);
// A space divided into zones is drawn by its walls and used through its zones.
const divided = (s) => (s.zones || []).length > 0;
const units = () => (state.floor?.spaces || []).filter((s) => !divided(s));

function styleSpace(s) {
  const path = state.paths.get(s.id);
  if (!path || divided(s)) return;
  path.style.fill = color(s.type);
  path.style.display = visible(s) ? "" : "none";
  path.classList.toggle("tucked", tucked(s));
  path.classList.toggle("review", s.reasons.length > 0);
  path.classList.toggle("selected", s.id === state.selected);
  path.classList.toggle("dim", !matches(s, state.filter));
  const label = state.labels.get(s.id);
  label.replaceChildren();
  label.dataset.name = s.name || "";
  label.dataset.number = s.number || (s.name ? "" : code(s.id));
}

// ---- view: pan, zoom, labels ---------------------------------------------

function updateView() {
  const { k, tx, ty } = state.view;
  $("world").setAttribute("transform", `matrix(${k} 0 0 ${-k} ${tx} ${ty})`);
  $("world-print").setAttribute("transform", `matrix(${k} 0 0 ${-k} ${tx} ${ty})`); // the same view, side by side
  placeLabels();
}

// Side by side, the floor is split the way it shows larger: a wide floor above and
// below, a tall one left and right. When the split changes, the floor is fitted again.
function arrange() {
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
function spaceAt(x, y) {
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
function showCursor(pane, sx, sy) {
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
function placeLabels() {
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
      const fit = (text) => (text.length > max ? text.slice(0, max - 1) + "…" : text);
      const lines = [label.dataset.name, label.dataset.number].filter(Boolean);
      const both = lines.length === 2 && h >= 34;
      const shown = both ? lines : lines.slice(0, 1);
      label.replaceChildren(...shown.map((text, i) => {
        const span = svg("tspan", { x: sx, y: sy + (both ? (i === 0 ? -3 : 12) : 4) });
        if (text === label.dataset.number && text !== label.dataset.name) span.setAttribute("class", "num");
        span.textContent = fit(text);
        return span;
      }));
    }
  });
}

function viewport() {
  const r = $("svg").getBoundingClientRect();
  return { w: r.width, h: r.height, left: r.left, top: r.top };
}

function floorBounds() {
  const f = state.floor;
  if (!f) return null;
  return union([...state.bounds.values(), extent(f.outline)]) || state.drawingBounds;
}

function scaleFor(bounds, pad = 40) {
  const { w, h } = viewport();
  const [x0, y0, x1, y1] = bounds;
  return Math.min((w - 2 * pad) / Math.max(x1 - x0, 0.5), (h - 2 * pad) / Math.max(y1 - y0, 0.5));
}

function centerOn(bounds, k) {
  const { w, h } = viewport();
  const [x0, y0, x1, y1] = bounds;
  state.view = { k, tx: w / 2 - ((x0 + x1) / 2) * k, ty: h / 2 + ((y0 + y1) / 2) * k };
  updateView();
}

function fit() {
  const bounds = floorBounds();
  const { w, h } = viewport();
  if (!bounds || !w || !h) return;
  state.fitted = true;
  centerOn(bounds, scaleFor(bounds));
}

function focusSpace(s) {
  const b = state.bounds.get(s.id);
  const floor = floorBounds();
  if (!b || !floor) return;
  const { k, tx, ty } = state.view;
  const { w, h } = viewport();
  const [sx0, sy0, sx1, sy1] = [b[0] * k + tx, -b[3] * k + ty, b[2] * k + tx, -b[1] * k + ty];
  const visible = sx0 >= 0 && sy0 >= 0 && sx1 <= w && sy1 <= h && sx1 - sx0 >= 60;
  if (visible) return;
  // The space at about a third of the view, never zoomed out beyond the whole floor.
  const pad = Math.max(b[2] - b[0], b[3] - b[1]);
  const around = [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
  centerOn(b, Math.max(scaleFor(floor), scaleFor(around, 20)));
}

function zoomAt(sx, sy, factor) {
  const { k, tx, ty } = state.view;
  const floor = floorBounds();
  const min = floor ? scaleFor(floor) / 8 : 0.01;
  const nk = Math.min(Math.max(k * factor, min), 4000);
  const wx = (sx - tx) / k;
  const wy = (ty - sy) / k;
  state.view = { k: nk, tx: sx - wx * nk, ty: sy + wy * nk };
  updateView();
}

function setupMap() {
  for (const pane of [$("svg"), $("svg-print")]) bindPane(pane);
  // Keep the middle of the plan in the middle when the window changes size, or
  // the view is split side by side.
  let size = null;
  new ResizeObserver(() => {
    const { w, h } = viewport();
    if (!state.fitted || state.refit) fit();
    else if (size) {
      state.view = { ...state.view, tx: state.view.tx + (w - size.w) / 2, ty: state.view.ty + (h - size.h) / 2 };
      updateView();
    }
    size = { w, h };
  }).observe($("svg"));
  new ResizeObserver(() => { if (state.side) arrange(); }).observe($("map")); // a wider or taller window

  $("side-by-side").checked = state.side;
  $("side-by-side").addEventListener("change", (e) => {
    state.side = e.target.checked;
    save("storeypath.side", state.side ? "1" : "0");
    showCursor($("svg"), null, null);
    showUnderlay();
  });

  $("fit").addEventListener("click", fit);
  $("drawing-mode").value = state.drawingMode;
  $("drawing-mode").addEventListener("change", (e) => {
    state.drawingMode = e.target.value;
    save("storeypath.drawing", state.drawingMode);
    showUnderlay();
  });
  $("show-labels").addEventListener("change", (e) => $("labels").classList.toggle("hidden", !e.target.checked));
  $("show-hidden").addEventListener("change", (e) => {
    state.showHidden = e.target.checked;
    if (!state.floor) return;
    renderPlan();
    state.floor.spaces.forEach(styleSpace);
    placeLabels();
    renderLists();
    renderLegend();
  });
}

// Dragging pans, the wheel zooms, a click selects: the same on both sides.
function bindPane(pane) {
  const local = (e) => {
    const r = pane.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  let drag = null;
  pane.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, tx: state.view.tx, ty: state.view.ty, moved: false, target: e.target };
    pane.setPointerCapture(e.pointerId);
  });
  pane.addEventListener("pointermove", (e) => {
    const [sx, sy] = local(e);
    showCursor(pane, sx, sy);
    if (state.tool && !drag?.moved) preview(planPoint(sx, sy));
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    pane.classList.add("dragging");
    state.view = { ...state.view, tx: drag.tx + dx, ty: drag.ty + dy };
    updateView();
  });
  const end = (e) => {
    if (!drag) return;
    const { moved, target } = drag;
    drag = null;
    pane.classList.remove("dragging");
    if (moved) return;
    const p = planPoint(...local(e));
    if (state.tool) return toolClick(p);
    const door = openingNear(p);
    if (door) return selectItem({ kind: "door", id: door.id });
    const line = drawnNear(p);
    if (line) return selectItem({ kind: line.kind, at: line.at });
    // on the plan, what was clicked; on the print, the space drawn there
    select(pane === $("svg") ? target?.dataset?.id || null : spaceAt(...p)?.id || null);
  };
  // A right-click: what can be done there
  pane.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (!state.floor || state.busy) return;
    openMenu(e.clientX, e.clientY, planPoint(...local(e)));
  });
  pane.addEventListener("pointerup", end);
  pane.addEventListener("pointercancel", () => {
    drag = null;
    pane.classList.remove("dragging");
  });
  pane.addEventListener("pointerleave", () => showCursor(pane, null, null));
  pane.addEventListener("wheel", (e) => {
    e.preventDefault();
    closeMenu();
    const [sx, sy] = local(e);
    const speed = e.deltaMode === 1 ? 0.05 : 0.0015;
    zoomAt(sx, sy, Math.exp(-e.deltaY * speed));
  }, { passive: false });
}

// ---- lists and legend ----------------------------------------------------

function listItem(s, withReasons) {
  const quick = (label, flag, tip) => {
    const b = el("button", { type: "button", class: "quick", title: tip }, label);
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      setFlag(s.id, flag, true);
    });
    return b;
  };
  const li = el("li", { "data-id": s.id, class: tucked(s) ? "tucked" : "" },
    swatch(s.type),
    el("span", {}, title(s)),
    el("span", { class: "sub" }, s.ignored ? "deleted" : s.hidden ? "hidden"
      : `${s.kind === "zone" ? "zone · " : ""}${typeLabel(s.type)} · ${code(s.id)}`),
    withReasons ? el("span", { class: "why" }, s.reasons.join("; "),
      el("span", { class: "quicks" }, quick("Delete", "ignored",
        "Not there, or not worth anything: out of the plan and the package, kept with its ID"))) : null,
  );
  li.classList.toggle("selected", s.id === state.selected);
  li.addEventListener("click", () => select(s.id, { fly: true }));
  return li;
}

function reviewSpaces() {
  return units().filter((s) => s.reasons.length);
}

function renderLists() {
  const review = reviewSpaces();
  $("review-count").textContent = `(${review.length})`;
  $("review-list").replaceChildren(...review.map((s) => listItem(s, true)));
  $("review-done").hidden = review.length > 0 || !units().length;
  $("next").disabled = !review.length;

  const all = units().filter(visible);
  const shown = all.filter((s) => matches(s, state.filter));
  const tuckedCount = units().length - units().filter((s) => !tucked(s)).length;
  $("space-count").textContent = (state.filter ? `(${shown.length} of ${all.length})` : `(${all.length})`)
    + (tuckedCount && !state.showHidden ? ` · ${tuckedCount} deleted` : "");
  const items = shown.slice(0, LIST_LIMIT).map((s) => listItem(s, false));
  if (shown.length > LIST_LIMIT) items.push(el("li", { class: "meta" }, `${shown.length - LIST_LIMIT} more…`));
  $("space-list").replaceChildren(...items);
}

function renderLegend() {
  const counts = new Map();
  for (const s of units().filter(visible)) counts.set(s.type, (counts.get(s.type) || 0) + 1);
  const types = state.project.types.filter((t) => counts.has(t));
  $("legend").replaceChildren(...types.map((t) =>
    el("li", {}, swatch(t), typeLabel(t), el("span", { class: "count" }, String(counts.get(t)))),
  ));
}

function markListSelection() {
  for (const li of document.querySelectorAll(".list li[data-id]")) {
    li.classList.toggle("selected", li.dataset.id === state.selected);
  }
}

// ---- selection and editing -----------------------------------------------

function select(id, { fly = false } = {}) {
  if (id && state.item) selectItem(null);
  const previous = state.byId.get(state.selected);
  state.selected = id && state.byId.has(id) ? id : null;
  if (previous) styleSpace(previous);
  const s = state.byId.get(state.selected);
  $("print-selected").setAttribute("d", s ? pathData(s.geometry) : ""); // and on the print beside it
  if (s) {
    styleSpace(s);
    $("spaces").append(state.paths.get(s.id)); // on top, so its outline shows
    if (fly) focusSpace(s);
  }
  renderEditor();
  markListSelection();
  const hash = new URLSearchParams({ floor: state.floor.id });
  if (s) hash.set("space", s.id);
  history.replaceState(null, "", `#${hash}`);
}

function formCorrection(s) {
  const d = s.detected;
  const c = {};
  const type = $("ed-type").value;
  const name = $("ed-name").value.trim();
  const number = $("ed-number").value.trim();
  if (type !== d.type) c.type = type;
  if (name !== (d.name || "")) c.name = name;
  if (number !== (d.number || "")) c.number = number;
  return c;
}

const same = (a, b) => JSON.stringify(a, Object.keys(a).sort()) === JSON.stringify(b, Object.keys(b).sort());

function updateEditorState() {
  const s = state.byId.get(state.selected);
  if (!s) return;
  const d = s.detected;
  const hint = (id, text, changed) => {
    $(id).textContent = text;
    $(id).classList.toggle("changed", changed);
  };
  const source = d.source === "default" ? "no rule matched" : d.source.replace(/:.*/, "");
  hint("ed-type-detected", `detected: ${typeLabel(d.type)} (${source})`, $("ed-type").value !== d.type);
  hint("ed-name-detected", d.name ? `detected: ${d.name}` : "none detected", $("ed-name").value.trim() !== (d.name || ""));
  hint("ed-number-detected", d.number ? `detected: ${d.number}` : "none detected",
    $("ed-number").value.trim() !== (d.number || ""));

  const c = formCorrection(s);
  const save = $("ed-save");
  if (s.correction && same(c, s.correction)) {
    save.textContent = "Saved";
    save.disabled = true;
  } else if (!s.correction && !Object.keys(c).length) {
    save.textContent = s.reasons.length ? "Accept as is" : "Save";
    save.disabled = !s.reasons.length;
  } else {
    save.textContent = "Save";
    save.disabled = false;
  }
  $("ed-reset").hidden = !s.correction;
  $("ed-delete").textContent = s.ignored ? "Restore" : "Delete";
  $("ed-flags").textContent = s.ignored ? "Deleted" : s.hidden ? "Hidden" : "";
}

function renderEditor() {
  const s = state.byId.get(state.selected);
  $("editor").hidden = !s;
  if (!s) return;
  $("ed-title").textContent = title(s);
  $("ed-id").textContent = s.id;
  $("ed-reasons").replaceChildren(...s.reasons.map((r) => el("li", {}, r)));
  $("ed-type").value = s.type;
  $("ed-type").style.borderLeft = `6px solid ${color(s.type)}`;
  $("ed-name").value = s.name || "";
  $("ed-number").value = s.number || "";
  $("ed-area").textContent = `${s.area} m²${s.correction ? " · corrected" : ""}`;
  updateEditorState();
}

function setFlag(id, flag, value) {
  return saveSpace({ [flag]: value }, id);
}

async function saveSpace(body, id = state.selected) {
  const s = state.byId.get(id);
  if (!s) return;
  let updated;
  try {
    updated = await request(`${BASE}/objects/${s.id}`, body);
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
    return;
  }
  const i = state.floor.spaces.findIndex((x) => x.id === s.id);
  state.floor.spaces[i] = updated;
  state.byId.set(updated.id, updated);
  styleSpace(updated);
  placeLabels();
  renderLists();
  renderLegend();
  renderEditor();
  const floor = state.project.floors.find((f) => f.id === state.floor.id);
  floor.review = reviewSpaces().length;
  fillFloorSelect();
  const flagged = "hidden" in body ? (body.hidden ? "hidden" : "shown again")
    : "ignored" in body ? (body.ignored ? "deleted" : "restored") : null;
  toast(flagged ? `${title(updated)}: ${flagged}` : body.reset ? "Corrections removed" : `Saved ${code(updated.id)}`);
  if (!visible(updated) && state.selected === updated.id) select(null);
}

function nextToReview() {
  const review = reviewSpaces();
  if (!review.length) return;
  const i = review.findIndex((s) => s.id === state.selected);
  select(review[(i + 1) % review.length].id, { fly: true });
}

async function reconvert() {
  const id = state.floor.id;
  const button = $("convert");
  button.disabled = true;
  $("status").textContent = "Re-reading drawing…";
  try {
    const job = await followJob(await request(`${BASE}/floors/${id}/convert`, {}), "Re-reading drawing…");
    toast(job.result.summaries.join("\n"));
    state.project = await request(`${BASE}/review`);
    fillFloorSelect();
    await openFloor(id, state.selected, { keepView: true });
  } catch (e) {
    toast(`Could not re-read the drawing: ${e.message}`, true);
    $("status").textContent = "";
  } finally {
    button.disabled = false;
  }
}

/** Wait for a server job, saying what it is doing; resolves with the job when done. */
async function followJob(job, saying) {
  while (job.state === "waiting" || job.state === "running") {
    $("status").textContent = job.log.at(-1) || saying;
    await new Promise((r) => setTimeout(r, 700));
    job = await request(`jobs/${job.id}`);
  }
  $("status").textContent = "";
  if (job.state === "failed") throw new Error(job.error);
  return job;
}

// ---- drawing what the drawing leaves out ---------------------------------
// A wall, a door or a window drawn here is kept with the floor and read with its
// drawing every time (walls part spaces as drawn walls do); the floor is read again
// at once. Plan coordinates are local meters, as the spaces are.

const HINTS = {
  wall: "Wall: click where it starts and where it ends (it snaps to walls). Esc to stop.",
  divide: "Divide: click the line's two ends, right across the space (no wall: it becomes two zones). Esc to stop.",
  door: "Door: click on a wall where it goes. Esc to stop.",
  window: "Window: click on a wall where it goes. Esc to stop.",
  opening: "Opening (a way through, no door): click on a wall where it goes. Esc to stop.",
};
const WIDTHS = { door: 0.9, window: 1.2, opening: 1.0 }; // what is added, until given another size
const LINES = { wall: "wall", divide: "divider" }; // the tools that draw a line, and what it is

function planPoint(sx, sy) {
  const { k, tx, ty } = state.view;
  return [(sx - tx) / k, (ty - sy) / k];
}

function setTool(tool) {
  state.tool = tool && state.tool !== tool ? tool : null;
  state.wallStart = null;
  $("map").classList.toggle("drawing-tool", Boolean(state.tool));
  clearPreview();
  if (state.tool) {
    select(null);
    selectItem(null);
    toast(HINTS[state.tool]);
  }
}

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
function snapWall(p) {
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
  const s = state.wallStart;
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
function openingAt(p, wide = WIDTHS[state.tool]) {
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

function clearPreview() {
  for (const id of ["preview", "preview-print"]) $(id).replaceChildren();
}

function preview(p) {
  if (state.busy) return;
  const r = 5 / state.view.k;
  let shapes = [];
  if (state.tool in LINES) {
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

function toolClick(p) {
  if (state.busy) return;
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

async function submitEdit(body, saying, after = null) {
  state.busy = true;
  $("map").classList.add("busy");
  try {
    await followJob(await request(`${BASE}/floors/${state.floor.id}/edits`, body), saying);
    state.project = await request(`${BASE}/review`);
    fillFloorSelect();
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

function toSegment(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

/** The door, window or opening under the pointer: its bar, its leaves or its mark,
 * within a few pixels (or its wall's thickness). */
function openingNear(p, px = 9) {
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

/** A wall or dividing line drawn here, under the pointer: {kind, at} (at: its middle). */
function drawnNear(p, px = 8) {
  const reach = px / state.view.k;
  let best = null;
  const lines = [...(state.floor?.edits?.walls || []).map((w) => ["wall", w]), ...(state.floor?.edits?.dividers || []).map((w) => ["divider", w])];
  for (const [kind, [a, b]] of lines) {
    const dist = toSegment(p, a, b);
    if (dist <= reach && (!best || dist < best.dist)) best = { kind, at: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], length: Math.hypot(b[0] - a[0], b[1] - a[1]), dist };
  }
  return best ? { kind: best.kind, at: best.at, length: best.length } : null;
}

function selectItem(item) {
  state.item = item;
  if (item) select(null);
  renderItemEditor();
  if (state.floor) renderPlan();
}

function renderItemEditor() {
  const item = state.item;
  $("item-editor").hidden = !item;
  if (!item) return;
  if (item.kind === "wall" || item.kind === "divider") {
    const list = item.kind === "wall" ? state.floor.edits?.walls : state.floor.edits?.dividers;
    const w = (list || []).find(([a, b]) => `${(a[0] + b[0]) / 2},${(a[1] + b[1]) / 2}` === item.at.join(","));
    $("it-title").textContent = item.kind === "wall" ? "Wall drawn here" : "Dividing line drawn here";
    $("it-id").textContent = "";
    $("it-meta").textContent = (w ? `${Math.hypot(w[1][0] - w[0][0], w[1][1] - w[0][1]).toFixed(2)} m long` : "")
      + (item.kind === "divider" ? " · no wall: the space's zones" : "");
    $("it-delete").textContent = "Take away";
    $("it-flags").textContent = "";
    $("it-size").hidden = true;
    return;
  }
  const d = state.floor.doors.find((x) => x.id === item.id);
  if (!d) return;
  $("it-title").textContent = openingName(d);
  $("it-id").textContent = d.id;
  $("it-meta").textContent = openingMeta(d);
  $("it-delete").textContent = d.drawn ? "Take away" : d.ignored ? "Restore" : "Delete";
  $("it-flags").textContent = d.ignored ? "Deleted" : "";
  $("it-size").hidden = d.ignored;
  fillSizeForm($("it-size"), d);
}

function openingName(d) {
  const name = d.type === "window" ? "Window" : d.type === "door" ? "Door" : "Opening";
  return d.tag ? `${name} ${d.tag}` : name;
}

function openingMeta(d) {
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

function fillSizeForm(form, d) {
  form.elements.width.value = fmt(d.width);
  form.elements.sill.value = fmt(d.sill);
  form.elements.height.value = fmt(d.height);
  form.querySelector(".sill").hidden = d.type !== "window";
  form.querySelector(".as-drawn").hidden = d.drawn || !d.resize;
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

function resize(d, sizes) {
  return submitEdit({ resize: { at: d.middle, ...sizes } }, "Changing the size…", () => selectItem({ kind: "door", id: d.id }));
}

// ---- the right-click menu --------------------------------------------------

function closeMenu() {
  const m = $("menu");
  if (!m.hidden) {
    m.hidden = true;
    m.replaceChildren();
  }
}

function placeMenu(cx, cy) {
  const m = $("menu");
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.max(4, Math.min(cx, window.innerWidth - r.width - 4))}px`;
  m.style.top = `${Math.max(4, Math.min(cy, window.innerHeight - r.height - 4))}px`;
}

function menuItem(label, action, { danger = false, disabled = false, hint = "" } = {}) {
  const b = el("button", { type: "button", role: "menuitem", class: danger ? "danger" : "" }, label);
  if (hint) b.append(el("span", { class: "key" }, hint));
  b.disabled = disabled;
  b.addEventListener("click", () => {
    closeMenu();
    action();
  });
  return b;
}

function openMenu(cx, cy, p) {
  if (state.tool) setTool(null);
  const items = [];
  const d = openingNear(p);
  const line = d ? null : drawnNear(p);
  const s = d || line ? null : spaceAt(...p);
  if (d) {
    selectItem({ kind: "door", id: d.id });
    items.push(el("div", { class: "heading" }, openingName(d)), el("div", { class: "meta" }, openingMeta(d) || " "));
    if (!d.ignored) items.push(menuItem("Change size…", () => sizeMenu(cx, cy, d)));
    items.push(menuItem(d.drawn ? "Take it away" : d.ignored ? "Restore" : "Delete", () => deleteItem(), { danger: !d.ignored || d.drawn, hint: "Del" }));
    items.push(el("hr"));
  } else if (line) {
    selectItem({ kind: line.kind, at: line.at });
    items.push(el("div", { class: "heading" }, line.kind === "wall" ? "Wall drawn here" : "Dividing line drawn here"),
      el("div", { class: "meta" }, `${line.length.toFixed(2)} m long`));
    items.push(menuItem("Take it away", () => deleteItem(), { danger: true, hint: "Del" }));
    items.push(el("hr"));
  } else if (s) {
    select(s.id);
    items.push(el("div", { class: "heading" }, title(s)), el("div", { class: "meta" }, typeLabel(s.type)));
    items.push(menuItem(s.ignored ? "Restore this space" : "Delete this space", () => setFlag(s.id, "ignored", !s.ignored),
      { danger: !s.ignored, hint: s.ignored ? "" : "Del" }));
    items.push(el("hr"));
  }
  const onWall = Boolean(openingAt(p, 0.9));
  const off = onWall ? "" : "on a wall";
  items.push(menuItem("Add a door here", () => addOpening("door", p), { disabled: !onWall, hint: off }));
  items.push(menuItem("Add a window here", () => addOpening("window", p), { disabled: !onWall, hint: off }));
  items.push(menuItem("Add an opening here", () => addOpening("opening", p), { disabled: !onWall, hint: off }));
  items.push(menuItem("Draw a wall from here", () => startLine("wall", p), { hint: "W" }));
  items.push(menuItem("Divide a space from here", () => startLine("divide", p), { hint: "V" }));
  $("menu").replaceChildren(...items);
  placeMenu(cx, cy);
  $("menu").querySelector("button:not(:disabled)")?.focus();
}

function sizeMenu(cx, cy, d) {
  const form = $("it-size").cloneNode(true);
  form.removeAttribute("id");
  form.hidden = false;
  fillSizeForm(form, d);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    closeMenu();
    resize(d, sizesFrom(form, d));
  });
  form.querySelector(".as-drawn").addEventListener("click", () => {
    closeMenu();
    resize(d, { width: null, sill: null, height: null });
  });
  $("menu").replaceChildren(el("div", { class: "heading" }, `${openingName(d)}: size`), form);
  placeMenu(cx, cy);
  form.elements.width.focus();
  form.elements.width.select();
}

function addOpening(type, p) {
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

function startLine(tool, p) {
  setTool(tool);
  state.wallStart = snapWall(p);
  preview(p);
  toast(tool === "wall" ? "Wall: click where it ends (it snaps to walls). Esc to stop."
    : "Divide: click where the line ends, right across the space. Esc to stop.");
}

async function deleteItem() {
  const item = state.item;
  if (!item || state.busy) return;
  if (item.kind === "wall" || item.kind === "divider") return submitEdit({ remove: { at: item.at } }, "Taking it away…");
  const d = state.floor.doors.find((x) => x.id === item.id);
  if (!d) return;
  if (d.drawn) {
    const mid = [(d.span[0][0] + d.span[1][0]) / 2, (d.span[0][1] + d.span[1][1]) / 2];
    return submitEdit({ remove: { at: mid } }, "Taking it away…");
  }
  try {
    const updated = await request(`${BASE}/objects/${d.id}`, { ignored: !d.ignored });
    Object.assign(d, updated);
    toast(`${$("it-title").textContent}: ${d.ignored ? "deleted" : "restored"}`);
    if (d.ignored && !state.showHidden) selectItem(null);
    else selectItem(item);
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
  }
}

function setupPanel() {
  $("floor").addEventListener("change", (e) => openFloor(e.target.value));
  $("convert").addEventListener("click", reconvert);
  $("next").addEventListener("click", nextToReview);
  $("ed-close").addEventListener("click", () => select(null));
  $("ed-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(state.selected);
      toast("ID copied");
    } catch {
      toast("Could not copy; select the ID and copy it", true);
    }
  });
  $("ed-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const s = state.byId.get(state.selected);
    if (s && !$("ed-save").disabled) saveSpace({ correction: formCorrection(s) });
  });
  $("ed-reset").addEventListener("click", () => saveSpace({ reset: true }));
  $("ed-delete").addEventListener("click", () => {
    const s = state.byId.get(state.selected);
    if (s) setFlag(s.id, "ignored", !s.ignored);
  });
  $("it-close").addEventListener("click", () => selectItem(null));
  $("it-delete").addEventListener("click", deleteItem);
  $("it-size").addEventListener("submit", (e) => {
    e.preventDefault();
    const d = state.item?.kind === "door" && state.floor.doors.find((x) => x.id === state.item.id);
    if (d) resize(d, sizesFrom($("it-size"), d));
  });
  $("it-size").querySelector(".as-drawn").addEventListener("click", () => {
    const d = state.item?.kind === "door" && state.floor.doors.find((x) => x.id === state.item.id);
    if (d) resize(d, { width: null, sill: null, height: null });
  });
  // the menu closes on a click elsewhere, Escape, or the window changing
  document.addEventListener("pointerdown", (e) => { if (!e.target.closest?.("#menu")) closeMenu(); }, true);
  window.addEventListener("resize", closeMenu);
  window.addEventListener("blur", closeMenu);
  $("menu").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = [...$("menu").querySelectorAll("button:not(:disabled)")];
    const i = items.indexOf(document.activeElement);
    items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
    e.preventDefault();
  });
  for (const id of ["ed-type", "ed-name", "ed-number"]) $(id).addEventListener("input", updateEditorState);
  $("ed-type").addEventListener("change", (e) => (e.target.style.borderLeft = `6px solid ${color(e.target.value)}`));
  $("search").addEventListener("input", (e) => {
    state.filter = e.target.value.trim().toLowerCase();
    state.floor.spaces.forEach(styleSpace);
    placeLabels();
    renderLists();
  });

  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest?.("input, select, textarea");
    if (e.key === "Escape") {
      if (!$("menu").hidden) closeMenu();
      else if (typing) e.target.blur();
      else if (state.tool) setTool(null);
      else if (state.item) selectItem(null);
      else select(null);
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      // what is chosen: a door, window, opening or drawn line; else a space
      if (state.item) deleteItem();
      else {
        const s = state.byId.get(state.selected);
        if (s && !s.ignored) setFlag(s.id, "ignored", true);
      }
      e.preventDefault();
      return;
    }
    if (e.key === "n" || e.key === "N") nextToReview();
    else if (e.key === "f" || e.key === "F") fit();
    else if (e.key === "w" || e.key === "W") setTool("wall");
    else if (e.key === "v" || e.key === "V") setTool("divide");
    else if (e.key === "d" || e.key === "D") setTool("door");
    else if (e.key === "o" || e.key === "O") setTool("opening");
  });
}

setupMap();
setupPanel();
start();
