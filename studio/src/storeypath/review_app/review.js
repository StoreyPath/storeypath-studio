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
  selected: null,
  view: { k: 1, tx: 0, ty: 0 }, // screen = (x·k + tx, −y·k + ty)
  showHidden: false, // show spaces marked hidden or ignored
  fitted: false,
  filter: "",
};

// ---- helpers -------------------------------------------------------------

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
  if (changed) {
    state.drawingBounds = null;
    $("drawing").replaceChildren(); // never show another floor's drawing underneath
  }
  $("floor").value = id;
  renderFloorMeta();
  renderPlan();
  renderLists();
  renderLegend();
  if (!keepView || changed) fit();
  select(spaceId && state.byId.has(spaceId) ? spaceId : null, { fly: Boolean(spaceId) });
  loadDrawing(id);
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
  $("walk3d").href = "/viewer/examples/world/index.html?" + new URLSearchParams({
    pkg: `/api/${BASE}/preview.storeypath`, floor: f.id }); // the project as it is now

  const box = $("warnings");
  box.hidden = !f.warnings.length;
  box.querySelector("summary").textContent =
    `${f.warnings.length} warning${f.warnings.length === 1 ? "" : "s"} from the last conversion`;
  box.querySelector("ul").replaceChildren(...f.warnings.map((w) => el("li", {}, w)));
}

async function loadDrawing(id) {
  $("status").textContent = "Loading drawing…";
  try {
    const drawing = await request(`${BASE}/floors/${id}/drawing`);
    if (state.floor?.id !== id) return;
    renderDrawing(drawing);
    $("status").textContent = "";
    if (!state.floor.spaces.length && !state.floor.outline) fit();
  } catch (e) {
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
  for (const s of f.spaces) {
    const path = svg("path", { d: pathData(s.geometry) });
    path.dataset.id = s.id;
    spaces.append(path);
    state.paths.set(s.id, path);
    state.bounds.set(s.id, extent(s.geometry));
    const label = svg("text");
    labels.append(label);
    state.labels.set(s.id, label);
    styleSpace(s);
  }
  $("doors").replaceChildren(
    ...f.doors.map((d) => svg("circle", { cx: d.point[0], cy: d.point[1], r: 0.12 })),
  );
  placeLabels();
}

function matches(s, q) {
  if (!q) return true;
  return [s.id, s.name, s.number, typeLabel(s.type), s.type]
    .some((v) => v && v.toLowerCase().includes(q));
}

const tucked = (s) => s.hidden || s.ignored;
const visible = (s) => state.showHidden || !tucked(s);

function styleSpace(s) {
  const path = state.paths.get(s.id);
  if (!path) return;
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
  placeLabels();
}

let labelFrame = 0;
function placeLabels() {
  if (labelFrame) return;
  labelFrame = requestAnimationFrame(() => {
    labelFrame = 0;
    const { k, tx, ty } = state.view;
    for (const s of state.floor?.spaces || []) {
      const label = state.labels.get(s.id);
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
  const map = $("svg");
  let drag = null;
  map.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, tx: state.view.tx, ty: state.view.ty, moved: false, target: e.target };
    map.setPointerCapture(e.pointerId);
  });
  map.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    map.classList.add("dragging");
    state.view = { ...state.view, tx: drag.tx + dx, ty: drag.ty + dy };
    updateView();
  });
  const end = () => {
    if (!drag) return;
    const { moved, target } = drag;
    drag = null;
    map.classList.remove("dragging");
    if (!moved) select(target?.dataset?.id || null);
  };
  map.addEventListener("pointerup", end);
  map.addEventListener("pointercancel", () => {
    drag = null;
    map.classList.remove("dragging");
  });
  map.addEventListener("wheel", (e) => {
    e.preventDefault();
    const { left, top } = viewport();
    const speed = e.deltaMode === 1 ? 0.05 : 0.0015;
    zoomAt(e.clientX - left, e.clientY - top, Math.exp(-e.deltaY * speed));
  }, { passive: false });
  // Keep the middle of the plan in the middle when the window changes size.
  let size = null;
  new ResizeObserver(() => {
    const { w, h } = viewport();
    if (!state.fitted) fit();
    else if (size) {
      state.view = { ...state.view, tx: state.view.tx + (w - size.w) / 2, ty: state.view.ty + (h - size.h) / 2 };
      updateView();
    }
    size = { w, h };
  }).observe($("map"));

  $("fit").addEventListener("click", fit);
  $("show-drawing").addEventListener("change", (e) => $("drawing").classList.toggle("hidden", !e.target.checked));
  $("show-labels").addEventListener("change", (e) => $("labels").classList.toggle("hidden", !e.target.checked));
  $("show-hidden").addEventListener("change", (e) => {
    state.showHidden = e.target.checked;
    if (!state.floor) return;
    state.floor.spaces.forEach(styleSpace);
    placeLabels();
    renderLists();
    renderLegend();
  });
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
    el("span", { class: "sub" }, s.ignored ? "ignored" : s.hidden ? "hidden" : `${typeLabel(s.type)} · ${code(s.id)}`),
    withReasons ? el("span", { class: "why" }, s.reasons.join("; "),
      el("span", { class: "quicks" }, quick("Ignore", "ignored", "Not worth anything: off the list, kept with its ID"),
        quick("Hide", "hidden", "Real, but not shown unless asked for"))) : null,
  );
  li.classList.toggle("selected", s.id === state.selected);
  li.addEventListener("click", () => select(s.id, { fly: true }));
  return li;
}

function reviewSpaces() {
  return state.floor.spaces.filter((s) => s.reasons.length);
}

function renderLists() {
  const review = reviewSpaces();
  $("review-count").textContent = `(${review.length})`;
  $("review-list").replaceChildren(...review.map((s) => listItem(s, true)));
  $("review-done").hidden = review.length > 0 || !state.floor.spaces.length;
  $("next").disabled = !review.length;

  const all = state.floor.spaces.filter(visible);
  const shown = all.filter((s) => matches(s, state.filter));
  const tuckedCount = state.floor.spaces.length - state.floor.spaces.filter((s) => !tucked(s)).length;
  $("space-count").textContent = (state.filter ? `(${shown.length} of ${all.length})` : `(${all.length})`)
    + (tuckedCount && !state.showHidden ? ` · ${tuckedCount} hidden or ignored` : "");
  const items = shown.slice(0, LIST_LIMIT).map((s) => listItem(s, false));
  if (shown.length > LIST_LIMIT) items.push(el("li", { class: "meta" }, `${shown.length - LIST_LIMIT} more…`));
  $("space-list").replaceChildren(...items);
}

function renderLegend() {
  const counts = new Map();
  for (const s of state.floor.spaces.filter(visible)) counts.set(s.type, (counts.get(s.type) || 0) + 1);
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
  const previous = state.byId.get(state.selected);
  state.selected = id && state.byId.has(id) ? id : null;
  if (previous) styleSpace(previous);
  const s = state.byId.get(state.selected);
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
  $("ed-ignore").textContent = s.ignored ? "Don't ignore" : "Ignore";
  $("ed-hide").textContent = s.hidden ? "Show" : "Hide";
  $("ed-flags").textContent = s.ignored ? "Ignored" : s.hidden ? "Hidden" : "";
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
    : "ignored" in body ? (body.ignored ? "ignored" : "no longer ignored") : null;
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
    let job = await request(`${BASE}/floors/${id}/convert`, {});
    while (job.state === "waiting" || job.state === "running") {
      $("status").textContent = job.log.at(-1) || "Re-reading drawing…";
      await new Promise((r) => setTimeout(r, 700));
      job = await request(`jobs/${job.id}`);
    }
    if (job.state === "failed") throw new Error(job.error);
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
  $("ed-ignore").addEventListener("click", () => {
    const s = state.byId.get(state.selected);
    if (s) setFlag(s.id, "ignored", !s.ignored);
  });
  $("ed-hide").addEventListener("click", () => {
    const s = state.byId.get(state.selected);
    if (s) setFlag(s.id, "hidden", !s.hidden);
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
      if (typing) e.target.blur();
      else select(null);
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "n" || e.key === "N") nextToReview();
    else if (e.key === "f" || e.key === "F") fit();
  });
}

setupMap();
setupPanel();
start();
