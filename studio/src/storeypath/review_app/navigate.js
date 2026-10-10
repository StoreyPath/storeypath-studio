// StoreyPath Navigate: the way between two places of a building, as a kiosk in its
// lobby would show it. Part of StoreyPath Studio; the page is
// navigate.html?p=<code>&building=<id>[&from=<id>&to=<id>&accessible=1].
//
// The way is Studio's (GET /api/projects/<code>/buildings/<id>/navigation?from&to:
// navigation.py, the same way every reader of the package finds); the places to
// choose from are its network's (kiosks, entrances, every room). The plan of each
// floor the way crosses is drawn by StoreyPath's plan engine (viewer/svg, built), the
// building in 3D by its world (viewer/src/world), both from the building as it is
// now (preview.storeypath), each with the way on it. Who may see the whole building
// may find the way in it; the server checks.

import { accountMenu, sentAway, whoami } from "./account.js";
import { setupChrome } from "./chrome.js";
import { WITH_OTHERS, onHold, together, toggled } from "./floorpick.js";
import { lookOptions, setupLook } from "./look.js";
import { normalizeItemId } from "/viewer/src/ids.js"; // items' IDs as people type them (the viewers' own)

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(typeof location === "undefined" ? "" : location.search); // (none in a test)
const CODE = params.get("p");
const PLAN_ENGINE = "/viewer/svg/dist/index.js"; // the plan engine, compiled (npm run build in viewer/svg)
const WORLD = "/viewer/src/world/world.js";
const CHOICES_SHOWN = 60;

const state = {
  project: null,
  buildings: [], // those of the project the person may see whole
  building: null,
  network: null, // the building's network (floors, places, nodes)
  floorNames: new Map(), // floor ID → its name
  floorOrder: [], // floor IDs, lowest first
  places: new Map(), // space or zone ID → place
  choices: [], // what a way may start or end at
  route: null,
  floor: null, // the floor whose plan is shown
  view: "plan", // "plan", "3d" or "both"
  bytes: null, // the building as a package (preview.storeypath), for both views
  planModule: undefined, // the plan engine's module (null: not built here)
  pkg: null, // the package as the plan engine reads it
  engine: null,
  planShows: null, // the floor the plan engine has
  fitWay: false, // the plan fitted to the way on it (else to the floor)
  world: null, // the 3D world, made when first shown
  worldShows: null, // the building the 3D world has open
  look: null, // its look's and quality's controls
  asking: 0, // the latest way asked for: an older answer is dropped
  step: null, // the step shown (lit in the list)
  playing: null, // "playing", "paused" or "ended"
  player: null, // the view playing the way (the plan engine, or the 3D world)
};

// ---- helpers -----------------------------------------------------------------------

async function api(path) {
  const res = await fetch(`/api/${path}`);
  const data = await res.json().catch(() => ({}));
  if (sentAway(res, data)) throw Object.assign(new Error("log in again"), { status: res.status });
  if (!res.ok) throw Object.assign(new Error(data.error || `${res.status} ${res.statusText}`), { status: res.status });
  return data;
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

let toastTimer;
function toast(text, error = false) {
  const t = $("toast");
  t.textContent = text;
  t.classList.toggle("error", error);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), error ? 9000 : 3000);
}

const message = (text) => ($("message").textContent = text || "");
const cap = (s) => (/^[a-z]/.test(s) ? s[0].toUpperCase() + s.slice(1) : s);
const typeWords = (t) => ({ elevator: "lift", unspecified: "room" })[t] ?? t.replaceAll("_", " ");
const floorName = (id) => state.floorNames.get(id) ?? id;

/** A time in words: "45 s", "2 min", "1 min 20 s". */
export function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60), rest = s % 60;
  return rest >= 5 ? `${m} min ${rest} s` : `${m} min`;
}

// ---- what a way may start or end at ------------------------------------------------

/** The network's places to choose from: its kiosks (each with its item's ID, the tag
 * on it), then its entrances (ways in from outside), then every room a person arrives
 * in, each with its floor; ``id`` is what the API is asked with (a kiosk's item, an
 * entrance's node, a space's or zone's ID). */
export function choicesOf(network) {
  const floors = new Map(network.floors.map((f) => [f.id, f.name]));
  const places = new Map(network.places.map((p) => [p.id, p]));
  const labelOf = (id) => places.get(id)?.label ?? "the room";
  const out = [];
  const nodes = [...network.nodes].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (const n of nodes) {
    if (n.kind === "kiosk" && n.item_id) {
      out.push({ id: n.item_id, node: n.id, group: "kiosk", floor: n.floor_id,
        label: `Kiosk in ${labelOf(n.zone_id || n.space_id)}`, sub: `${floors.get(n.floor_id) ?? n.floor_id} · ${n.item_id}` });
    }
  }
  for (const n of nodes) {
    if (n.kind === "entrance") {
      out.push({ id: n.id, node: n.id, group: "entrance", floor: n.floor_id,
        label: `Entrance into ${labelOf((n.spaces || [])[0])}`, sub: floors.get(n.floor_id) ?? n.floor_id });
    }
  }
  const arrivals = new Map(); // place → its node (a room's, a lift's, the stairs')
  for (const n of nodes) {
    if (["room", "lift", "stairs", "escalator", "ramp"].includes(n.kind)) {
      const place = n.zone_id || n.space_id;
      if (place && !arrivals.has(place)) arrivals.set(place, n);
    }
  }
  const rooms = [];
  for (const [place, n] of arrivals) {
    const p = places.get(place);
    if (!p) continue;
    const code = place.split("-").at(-1);
    rooms.push({ id: place, node: n.id, group: "room", floor: n.floor_id, type: p.type, label: cap(p.label),
      sub: `${floors.get(n.floor_id) ?? n.floor_id} · ${typeWords(p.type)} · ${code}` });
  }
  const order = new Map(network.floors.map((f, i) => [f.id, i]));
  rooms.sort((a, b) => (order.get(a.floor) ?? 0) - (order.get(b.floor) ?? 0)
    || a.label.localeCompare(b.label, undefined, { numeric: true }) || (a.id < b.id ? -1 : 1));
  return [...out, ...rooms];
}

/** The choices a search finds: a kiosk's item's ID as typed (7k2q xm9f 4dp), that
 * kiosk; else every word of it in the label, the floor, the type or the ID (a room's
 * name may read as an item's ID too). */
export function search(choices, query) {
  const tag = normalizeItemId(query);
  const kiosk = tag ? choices.filter((c) => c.id === tag) : [];
  if (kiosk.length) return kiosk;
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return choices;
  return choices.filter((c) => {
    const text = `${c.label} ${c.sub} ${c.id} ${c.group}`.toLowerCase();
    return words.every((w) => text.includes(w));
  });
}

/** A search box with its list: choose by clicking, or the arrows and Enter. */
class Picker {
  constructor(root, onChoose) {
    this.input = root.querySelector("input");
    this.list = root.querySelector("ul");
    this.onChoose = onChoose;
    this.chosen = null;
    this.shown = [];
    this.active = -1;
    this.input.addEventListener("focus", () => {
      this.input.select();
      this.open("");
    });
    this.input.addEventListener("input", () => this.open(this.input.value));
    this.input.addEventListener("keydown", (e) => this.key(e));
    this.input.addEventListener("blur", () => setTimeout(() => this.close(true), 150));
    this.list.addEventListener("mousedown", (e) => {
      const li = e.target.closest("li[data-i]");
      if (!li) return;
      e.preventDefault();
      this.choose(this.shown[Number(li.dataset.i)]);
    });
  }

  open(query) {
    this.shown = search(state.choices, query).slice(0, CHOICES_SHOWN);
    this.active = this.shown.length ? 0 : -1;
    let group = null;
    const items = [];
    this.shown.forEach((c, i) => {
      if (c.group !== group) {
        group = c.group;
        items.push(el("li", { class: "group", role: "presentation" },
          { kiosk: "Kiosks", entrance: "Entrances", room: "Rooms" }[group]));
      }
      items.push(el("li", { "data-i": i, role: "option", id: `${this.list.id}-${i}`, class: i === this.active ? "active" : null },
        el("span", { class: "label" }, c.label), el("span", { class: "sub" }, c.sub)));
    });
    if (!items.length) items.push(el("li", { class: "none" }, "Nothing found"));
    this.list.replaceChildren(...items);
    this.list.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  close(restore = false) {
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    if (restore) this.input.value = this.chosen?.label ?? "";
  }

  key(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (this.list.hidden) return this.open(this.input.value);
      const n = this.shown.length;
      if (!n) return;
      this.active = (this.active + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
      for (const li of this.list.querySelectorAll("li[data-i]")) li.classList.toggle("active", Number(li.dataset.i) === this.active);
      this.list.querySelector("li.active")?.scrollIntoView({ block: "nearest" });
      this.input.setAttribute("aria-activedescendant", `${this.list.id}-${this.active}`);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!this.list.hidden && this.shown[this.active]) this.choose(this.shown[this.active]);
    } else if (e.key === "Escape") {
      this.close(true);
    }
  }

  choose(c, { quiet = false } = {}) {
    this.chosen = c ?? null;
    this.input.value = c?.label ?? "";
    this.input.title = c ? `${c.label} · ${c.sub}` : "";
    this.close();
    if (!quiet) this.onChoose(c);
  }

  /** Choose by ID (a kiosk's item, as typed too, or node, an entrance's node, a place), quietly. */
  set(id) {
    const tag = normalizeItemId(id);
    const c = id ? state.choices.find((x) => x.id === id || x.node === id || x.id === tag) : null;
    this.choose(c ?? null, { quiet: true });
    return Boolean(c);
  }
}

// ---- the page ----------------------------------------------------------------------

let from, to;

async function start() {
  window.storeypathNavigate = { state, showStep }; // for the console and the tests
  from = new Picker($("from"), () => changed());
  to = new Picker($("to"), () => changed());
  setupChrome();
  $("swap").addEventListener("click", () => {
    const a = from.chosen, b = to.chosen;
    from.choose(b, { quiet: true });
    to.choose(a, { quiet: true });
    changed();
  });
  $("accessible").checked = params.get("accessible") === "1";
  $("accessible").addEventListener("change", () => changed());
  $("building").addEventListener("change", () => openBuilding($("building").value));
  for (const b of $("view-mode").querySelectorAll("button")) b.addEventListener("click", () => setView(b.dataset.view));
  $("fit").addEventListener("click", () => fitPlan());
  $("fly").addEventListener("click", () => fly());
  $("play").addEventListener("click", () => play());
  $("restart").addEventListener("click", () => play({ restart: true }));
  $("steps").addEventListener("keydown", stepKeys);
  document.addEventListener("keydown", (e) => {
    // ↑ ↓ go through the steps from anywhere but a field, a list of choices or a view taken in hand
    if ((e.key !== "ArrowUp" && e.key !== "ArrowDown") || e.defaultPrevented || !state.route) return;
    if (e.target.closest?.("input, select, textarea, .choices, #plan, #world3d, #steps")) return;
    stepKeys(e);
  });
  // the plan's colours follow the page's light or dark
  new MutationObserver(() => state.engine?.setOptions({ theme: theme() }))
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  // the 3D view's look and quality, remembered in this browser (look.js)
  state.look = setupLook({ styles: [...$("look").querySelectorAll("button")], quality: $("quality") }, () => state.world);
  if (!CODE) {
    message("No project: open this page from a project's page (Navigate).");
    return;
  }
  $("back").href = `/#/p/${encodeURIComponent(CODE)}`;
  try {
    const me = await whoami();
    $("account").replaceChildren(accountMenu(me));
    state.project = await api(`projects/${encodeURIComponent(CODE)}`);
  } catch (e) {
    message(e.status === 404 ? "No such project, or it is not shared with you." : e.message);
    return;
  }
  document.title = `${state.project.project.name} · Find the way`;
  $("back").textContent = state.project.project.name;
  $("project-meta").textContent = "From a kiosk, an entrance or any room, to any room";
  const can = state.project.can;
  const sees = (b) => !can || ["view", "edit", "share"].includes(can.buildings?.[b.id] ?? can.project);
  state.buildings = state.project.locations.flatMap((l) => l.buildings)
    .filter((b) => b.floors.some((f) => f.converted) && sees(b));
  if (!state.buildings.length) {
    message("No building here may be shown whole to you: finding the way needs every floor of a building.");
    return;
  }
  $("building").replaceChildren(...state.buildings.map((b) => el("option", { value: b.id }, b.name)));
  $("building-field").hidden = state.buildings.length < 2;
  const wanted = params.get("building");
  const first = state.buildings.find((b) => b.id === wanted) ?? state.buildings[0];
  $("building").value = first.id;
  setView(params.get("view") || saved("storeypath.navigate.view") || "plan", { quiet: true });
  planModule(); // (loaded while the building is read)
  await openBuilding(first.id, { from: params.get("from"), to: params.get("to") });
}

/** A building's network, its places to choose from, and its package for the views. */
async function openBuilding(id, { from: start = null, to: end = null } = {}) {
  state.building = id;
  state.route = null;
  state.pkg = null;
  state.bytes = null;
  state.planShows = null;
  showRoute();
  message("Reading the building…");
  try {
    state.network = await api(`projects/${encodeURIComponent(CODE)}/buildings/${encodeURIComponent(id)}/navigation`);
  } catch (e) {
    message(e.status === 403 || e.status === 404
      ? `You may not see the whole of this building: finding the way needs every floor of it. (${e.message})`
      : `The building's ways could not be read: ${e.message}`);
    return;
  }
  const net = state.network;
  state.floorNames = new Map(net.floors.map((f) => [f.id, f.name]));
  state.floorOrder = net.floors.map((f) => f.id);
  state.places = new Map(net.places.map((p) => [p.id, p]));
  state.choices = choicesOf(net);
  if (!from.set(start)) from.set(state.choices.find((c) => c.group === "kiosk")?.id ?? state.choices.find((c) => c.group === "entrance")?.id);
  if (!to.set(end)) to.choose(null, { quiet: true });
  state.floor = from.chosen?.floor ?? groundFloor();
  drawFloorTabs();
  message(state.choices.length ? "" : "This building has no rooms to find the way between yet.");
  loadPackage(id); // the views, while the way is asked for
  await changed({ keepUrl: Boolean(start || end) });
}

const groundFloor = () => state.network.floors.find((f) => f.ordinal === 0)?.id ?? state.floorOrder[0] ?? null;

/** The building as a package (as it is now), for the plan and the 3D view. */
async function loadPackage(id) {
  try {
    const res = await fetch(`/api/projects/${encodeURIComponent(CODE)}/preview.storeypath?building=${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `${res.status} ${res.statusText}`);
    const bytes = await res.arrayBuffer();
    if (state.building !== id) return;
    state.bytes = bytes;
  } catch (e) {
    $("plan-note").textContent = `The building could not be drawn: ${e.message}`;
    $("plan-note").hidden = false;
    return;
  }
  await drawPlan({ way: true });
  if (state.view !== "plan") await drawWorld();
}

/** From, to or the stairs changed: the way asked for again. */
async function changed({ keepUrl = false } = {}) {
  const a = from.chosen, b = to.chosen;
  const accessible = $("accessible").checked;
  if (!keepUrl) remember();
  if (!a || !b) {
    state.route = null;
    showRoute();
    if (state.choices.length) message(!a ? "Choose where the way starts." : "Choose where to go.");
    return;
  }
  const asked = ++state.asking;
  message("Finding the way…");
  try {
    const q = new URLSearchParams({ from: a.id, to: b.id });
    if (accessible) q.set("accessible", "1");
    const { route } = await api(`projects/${encodeURIComponent(CODE)}/buildings/${encodeURIComponent(state.building)}/navigation?${q}`);
    if (asked !== state.asking) return;
    state.route = route;
    message("");
    state.floor = route.legs[0]?.floor_id ?? state.floor;
  } catch (e) {
    if (asked !== state.asking) return;
    state.route = null;
    message(e.status === 404 ? `${cap(e.message)}.` : `The way could not be found: ${e.message}`);
  }
  showRoute();
  if (keepUrl) remember();
}

/** The page's address says what it shows, to come back to or send on. */
function remember() {
  const q = new URLSearchParams({ p: CODE, building: state.building });
  if (from.chosen) q.set("from", from.chosen.id);
  if (to.chosen) q.set("to", to.chosen.id);
  if ($("accessible").checked) q.set("accessible", "1");
  history.replaceState(null, "", `${location.pathname}?${q}`);
}

// ---- the way, in words ---------------------------------------------------------------

/** The turns a person makes along a walk (its line, in a floor's metres, y up), as the
 * page tells them: where (metres from its start) and which way ("left", "right"; `slight`
 * for a bend under 60°). Jogs under half a metre are smoothed out; turns within `merge`
 * metres of each other are one; none within `edge` metres of its start, nor `end` of its
 * end (where the arrival says which side the door is on). Presentation only: the steps
 * themselves are the network's, the same in every reader. */
export function turnsOf(points, { angle = 38, edge = 2, end = 3.5, merge = 2.5 } = {}) {
  const pts = [];
  for (const p of points) if (!pts.length || Math.hypot(p[0] - pts.at(-1)[0], p[1] - pts.at(-1)[1]) > 0.2) pts.push(p);
  if (pts.length < 3) return [];
  const simple = simplify(pts, 0.5);
  const lengths = [0];
  for (let i = 1; i < simple.length; i++) lengths.push(lengths[i - 1] + Math.hypot(simple[i][0] - simple[i - 1][0], simple[i][1] - simple[i - 1][1]));
  const total = lengths.at(-1);
  const raw = [];
  for (let i = 1; i < simple.length - 1; i++) {
    const [a, b, c] = [simple[i - 1], simple[i], simple[i + 1]];
    const h1 = Math.atan2(b[1] - a[1], b[0] - a[0]), h2 = Math.atan2(c[1] - b[1], c[0] - b[0]);
    const turn = (Math.atan2(Math.sin(h2 - h1), Math.cos(h2 - h1)) * 180) / Math.PI; // + left (y up), - right
    const at = lengths[i];
    if (at < edge || total - at < end) continue;
    if (raw.length && at - raw.at(-1).at < merge) raw.at(-1).turn += turn;
    else raw.push({ at, turn });
  }
  return raw.filter((t) => Math.abs(t.turn) >= angle)
    .map((t) => ({ at: Math.round(t.at), side: t.turn > 0 ? "left" : "right", slight: Math.abs(t.turn) < 60 }));
}

/** A line with fewer points: none further than `tolerance` from it (Douglas–Peucker). */
function simplify(points, tolerance) {
  if (points.length < 3) return points;
  const [a, b] = [points[0], points.at(-1)];
  const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
  let far = -1, at = 0;
  for (let i = 1; i < points.length - 1; i++) {
    const d = len > 1e-9 ? Math.abs((points[i][0] - a[0]) * dy - (points[i][1] - a[1]) * dx) / len : Math.hypot(points[i][0] - a[0], points[i][1] - a[1]);
    if (d > far) [far, at] = [d, i];
  }
  if (far <= tolerance) return [a, b];
  return [...simplify(points.slice(0, at + 1), tolerance).slice(0, -1), ...simplify(points.slice(at), tolerance)];
}

/** The leg each step is on (the start and walks on theirs, a ride on the leg it leaves,
 * the arrival on the last), as the plan engine has them. */
export function stepLegs(route) {
  let leg = 0;
  const last = Math.max(0, route.legs.length - 1);
  return route.steps.map((s) => (s.kind === "arrive" ? last : s.kind === "take" ? Math.min(last, leg++) : Math.min(last, leg)));
}

/** Seconds each step takes: a walk's metres at the network's pace; the rides share what
 * the way's time has left; the start and arrival none. */
export function stepSeconds(route, speed = 1.2) {
  const walks = route.steps.map((s) => (s.kind === "walk" ? s.metres / speed : 0));
  const rides = route.steps.filter((s) => s.kind === "take").length;
  const left = Math.max(0, route.seconds - walks.reduce((a, b) => a + b, 0));
  return route.steps.map((s, i) => (s.kind === "walk" ? walks[i] : s.kind === "take" ? left / rides : 0));
}

const GLYPH = { // the plan engine's pictures (viewer/svg glyphs.ts), when it is not loaded
  stairs: "M3 20h5v-5h5v-5h5V5h3", lift: "M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM9.5 10 12 7.5 14.5 10M9.5 14 12 16.5 14.5 14",
  up: "M12 19V5M6 11l6-6 6 6", down: "M12 5v14M6 13l6 6 6-6",
};
const svgIcon = (inner) => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = inner;
  return s;
};
const lucide = (name) => svgIcon(`<use href="/icons/lucide.svg#${name}"/>`);
const glyph = (name) => svgIcon(`<path d="${(state.planModule?.ROUTE_GLYPHS ?? GLYPH)[name] ?? GLYPH.stairs}"/>`);

function showRoute() {
  const r = state.route;
  $("result").hidden = !r;
  stopPlaying();
  state.step = null;
  if (r) {
    const rides = [...new Set(r.changes.map((c) => c.by))];
    const floors = new Set(r.legs.map((l) => l.floor_id)).size;
    $("sum-metres").textContent = `${Math.round(r.metres)} m`;
    $("sum-time").textContent = duration(r.seconds);
    $("sum-sub").replaceChildren(...[
      ...(rides.length ? rides.map((by) => el("span", { class: "chip" }, glyph(by === "lift" ? "lift" : "stairs"), `by ${by}`))
        : [el("span", {}, "On one floor")]),
      floors > 1 ? el("span", {}, `${floors} floors`) : null,
      r.accessible ? el("span", { class: "chip" }, lucide("accessibility"), "step-free") : null].filter(Boolean));
    renderSteps(r);
  } else {
    $("steps").replaceChildren();
  }
  drawFloorTabs();
  drawPlan({ way: true });
  if (state.world) drawWorldRoute();
  $("fly").disabled = !r || !state.world?.flyRoute;
}

/** The steps as cards, a floor's heading before each floor's: its picture, its words,
 * the turns along a walk, how far and how long. */
function renderSteps(r) {
  const legs = stepLegs(r), secs = stepSeconds(r, state.network?.speed_m_s || 1.2);
  const items = [];
  let floor = null;
  r.steps.forEach((s, i) => {
    const on = r.legs[legs[i]]?.floor_id;
    if (s.kind !== "take" && on !== floor) {
      floor = on;
      items.push(el("li", { class: "floor-head", role: "presentation" }, floorName(on)));
    }
    let icon;
    const meta = [];
    let turns = null;
    if (s.kind === "start") icon = el("span", { class: "step-icon" });
    else if (s.kind === "walk") {
      icon = el("span", { class: "step-icon" }, lucide("footprints"));
      meta.push(`${s.metres} m`, duration(secs[i]));
      const found = turnsOf(r.legs[legs[i]].points, { end: s.to === "destination" ? 3.5 : 1.5 }).slice(0, 4);
      if (found.length) {
        turns = el("ul", { class: "step-turns" }, found.map((t) => el("li", {},
          lucide(t.side === "left" ? "corner-up-left" : "corner-up-right"),
          `${t.slight ? "Bear" : "Turn"} ${t.side} after ${t.at} m`)));
      }
    } else if (s.kind === "take") {
      icon = el("span", { class: "step-icon" }, glyph(s.by === "lift" ? "lift" : "stairs"),
        el("span", { class: "way" }, glyph(s.direction === "down" ? "down" : "up")));
      meta.push(`${floorName(s.from_floor_id)} → ${floorName(s.to_floor_id)}`);
      if (secs[i] >= 1) meta.push(`about ${duration(secs[i])}`);
    } else {
      icon = el("span", { class: "step-icon" }, lucide("map-pin"));
      meta.push(floorName(s.floor_id));
    }
    if (s.kind === "start") meta.push(floorName(s.floor_id));
    const li = el("li", { class: `step ${s.kind}${i > 0 && r.steps[i - 1].kind === "take" ? " after-ride" : ""}`, "data-i": i,
      "data-floor": on || null, tabindex: -1, id: `step-${i}`, "aria-label": s.text },
    icon, el("span", { class: "step-body" }, el("span", { class: "step-text" }, s.text), turns,
      el("span", { class: "step-meta" }, meta.map((m, k) => el("span", {}, k ? `· ${m}` : m)))));
    if (s.kind === "take") li.classList.add("next-floor");
    li.addEventListener("click", () => showStep(i));
    items.push(li);
  });
  $("steps").replaceChildren(...items);
  $("steps").tabIndex = 0;
}

/** A step shown: lit in the list, framed on the plan and in 3D (its floor shown). */
function showStep(i, { from: by = "list" } = {}) {
  const r = state.route;
  if (!r || i < 0 || i >= r.steps.length) return;
  markStep(i);
  if (by === "list") {
    stopPlaying();
    if (state.engine?.showStep && state.view !== "3d") state.engine.showStep(i);
    if (state.world?.showStep && state.view !== "plan") state.world.showStep(i);
  }
}

/** The step shown, lit, its card in view; the list says so to screen readers. */
function markStep(i) {
  state.step = i;
  for (const li of $("steps").querySelectorAll(".step")) li.classList.toggle("here", Number(li.dataset.i) === i);
  const li = $(`step-${i}`);
  if (li) {
    $("steps").setAttribute("aria-activedescendant", li.id);
    li.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
}

/** ↑ ↓ through the steps; Enter shows the one lit. */
function stepKeys(e) {
  const r = state.route;
  if (!r) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const at = state.step ?? (e.key === "ArrowDown" ? -1 : r.steps.length);
    showStep(Math.max(0, Math.min(r.steps.length - 1, at + (e.key === "ArrowDown" ? 1 : -1))));
  } else if (e.key === "Enter" && state.step !== null) {
    e.preventDefault();
    showStep(state.step);
  }
}

// ---- playing the way -----------------------------------------------------------------

/** Play (or pause, or play on): on the plan a dot walks the way, floor by floor; in 3D
 * the camera goes along it; with both, the plan plays and the 3D view shows each step
 * as it gets there. From the start again with ``restart``. */
function play({ restart = false } = {}) {
  if (!state.route) return;
  const plan = state.view !== "3d" ? state.engine : null, world = state.view === "3d" ? state.world : null;
  const player = plan ?? world;
  if (!player?.playRoute) return;
  if (state.playing === "playing" && !restart) {
    player.pauseRoute();
    return;
  }
  const run = player.playRoute({ restart: restart || state.playing === "ended" });
  state.player = player;
  run.then(() => { if (state.player === player && state.playing !== "paused") state.player = null; });
}

/** Playing stopped (another way, or a step chosen). */
function stopPlaying() {
  state.engine?.stopRoute?.();
  state.world?.stopRoute?.();
  playState(null);
}

/** The Play button and the progress bar as playing is: "playing", "paused", "ended", or null. */
function playState(s) {
  state.playing = s;
  const b = $("play");
  b.setAttribute("aria-pressed", String(s === "playing"));
  b.querySelector(".play-label").textContent = s === "playing" ? "Pause" : s === "paused" ? "Play on" : s === "ended" ? "Again" : "Play";
  $("restart").hidden = s !== "paused";
  $("progress").classList.toggle("on", s === "playing" || s === "paused");
  if (!s || s === "ended") $("progress").firstElementChild.style.width = "0";
}

/** A view's way events: the step it shows (lit in the list), how far it has played. */
function follow(view) {
  view.addEventListener("routestep", (e) => {
    markStep(e.detail.index);
    // playing on the plan with both shown: the 3D view on the same step
    if (view === state.engine && state.view === "both" && state.playing === "playing") state.world?.showStep?.(e.detail.index);
  });
  view.addEventListener("routeplay", (e) => {
    if (state.player && view !== state.player) return;
    playState(e.detail.state === "stopped" ? null : e.detail.state);
  });
  view.addEventListener("routeprogress", (e) => {
    $("progress").firstElementChild.style.width = `${Math.round(e.detail.fraction * 1000) / 10}%`;
  });
}

// ---- the plan ----------------------------------------------------------------------

/** The floors as tabs: those the way goes over marked, numbered in the order it goes
 * over them. */
function drawFloorTabs() {
  const onRoute = new Map();
  (state.route?.legs ?? []).forEach((leg, i) => { if (!onRoute.has(leg.floor_id)) onRoute.set(leg.floor_id, onRoute.size + 1); });
  const floors = state.network?.floors ?? []; // lowest first
  $("floors").replaceChildren(...floors.map((f) => el("button", {
    type: "button", class: [f.id === state.floor ? "active" : "", onRoute.has(f.id) ? "on-route" : ""].join(" ").trim() || null,
    "aria-pressed": String(f.id === state.floor), "data-floor": f.id,
    title: onRoute.has(f.id) ? `${f.name}: part ${onRoute.get(f.id)} of the way` : f.name, onclick: () => showFloor(f.id),
  }, onRoute.has(f.id) ? el("span", { class: "leg" }, String(onRoute.get(f.id))) : null, f.name)));
}

/** A floor's tab: that floor; its part of the way framed when it has one. */
function showFloor(id) {
  if (!id || !state.engine || !state.pkg) return;
  const leg = (state.route?.legs ?? []).findIndex((l) => l.floor_id === id);
  stopPlaying();
  if (leg >= 0 && state.engine.showLeg) state.engine.showLeg(leg);
  else if (id !== state.planShows) state.engine.setFloor(state.planModule.floorFromPackage(state.pkg, id), { fade: true });
}

/** The plan engine (viewer/svg, built), or null when it is not built here. */
async function planModule() {
  if (state.planModule === undefined) {
    state.planModule = import(PLAN_ENGINE).then((m) => (state.planModule = m), () => (state.planModule = null));
  }
  return state.planModule;
}

const theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");

/** The plan: its engine made once (calm, light or dark with the page), the floor the way
 * starts on with the way drawn on it (`way`: drawn again), else the floor chosen. */
async function drawPlan({ way = false } = {}) {
  if (!state.bytes || !state.network) return;
  const mod = await planModule();
  const note = $("plan-note");
  if (!mod) {
    note.textContent = "The plan is not shown: Studio's plan engine is not built here (npm run build in viewer/svg). The steps are on the left.";
    note.hidden = false;
    return;
  }
  try {
    if (!state.pkg) state.pkg = await mod.readPackage(state.bytes);
    if (!state.engine) {
      state.engine = new mod.FloorPlanEngine($("plan"), { title: "Floor plan with the way", interactive: () => false, interactiveItems: false,
        style: "wayfinding", theme: theme() });
      state.engine.addEventListener("floorchange", (e) => {
        state.floor = state.planShows = e.detail.id;
        drawFloorTabs();
      });
      follow(state.engine);
    }
    const r = state.route;
    const floor = r ? r.legs[0].floor_id : state.floor;
    if (!way && state.planShows === floor) return;
    if (state.planShows !== floor || !r) state.engine.setFloor(mod.floorFromPackage(state.pkg, floor), { fit: !r, fade: Boolean(state.planShows) });
    if (typeof state.engine.showRoute === "function") {
      state.engine.showRoute(r, { floorName, fit: true, startLabel: startLabel(), style: "wayfinding",
        floorPlan: (id) => mod.floorFromPackage(state.pkg, id) });
    }
    state.fitWay = Boolean(r);
    note.hidden = true;
  } catch (e) {
    note.textContent = `The plan could not be drawn: ${e.message}`;
    note.hidden = false;
  }
}

/** What the start's mark says: "You are here" at a kiosk; else where it is. */
function startLabel() {
  const c = from.chosen;
  if (!c) return null;
  return c.group === "kiosk" ? "You are here" : c.group === "entrance" ? "Entrance" : "Start";
}

/** The way on this floor; again, the whole floor (and so on, each press). */
function fitPlan() {
  if (!state.engine) return;
  state.fitWay = !state.fitWay && Boolean(state.route) && typeof state.engine.fitRoute === "function";
  if (state.fitWay) state.engine.fitRoute();
  else state.engine.fit();
}

// ---- the 3D view ---------------------------------------------------------------------

async function drawWorld() {
  if (!state.bytes) return;
  const note = $("world-note");
  try {
    if (!state.world) {
      const { StoreyPathWorld } = await import(WORLD);
      state.world = new StoreyPathWorld($("world3d"), { labels: true, ...lookOptions() });
      state.look?.attach(state.world);
      follow(state.world);
      for (const type of ["floorchange", "floorsshown"]) state.world.addEventListener(type, () => renderWorldFloors());
    }
    if (state.worldShows !== state.building) {
      note.textContent = "Building the 3D view…";
      note.hidden = false;
      await state.world.open(new Blob([state.bytes]));
      state.worldShows = state.building;
      state.world.setFloor(null);
      state.world.setCutaway?.(true); // walls cut low, as on a plan: the way shows on every floor
    }
    note.hidden = true;
    drawWorldRoute();
  } catch (e) {
    note.textContent = `The 3D view could not be shown: ${e.message}`;
    note.hidden = false;
  }
}

function drawWorldRoute() {
  const w = state.world;
  if (!w || state.worldShows !== state.building || typeof w.showRoute !== "function") return;
  w.setFloors?.(null); // (a new way: the floors it walks on, by lift past others: those alone)
  w.showRoute(state.route, { fit: true, startLabel: startLabel(), floorName });
  $("fly").disabled = !state.route || typeof w.flyRoute !== "function";
  renderWorldFloors();
}

/** The 3D view's floors (bottom right), those shown marked: a way of several floors shows
 * those it walks on (Way: a lift's floors ridden past left out), else every floor (All); a
 * click shows a floor on its own, ⌘-click (Ctrl-click; a long press) one with those shown. */
function renderWorldFloors() {
  const box = $("world-floors"), w = state.world, pkg = w?.package;
  const floors = w?.setFloors && pkg && w.building && state.worldShows === state.building ? pkg.floorsOf(w.building) : [];
  box.hidden = floors.length < 2;
  if (box.hidden) return box.replaceChildren();
  const ids = floors.map((f) => f.id), shown = w.shownFloors, asked = w.floors; // (lowest first)
  const multi = new Set((state.route?.legs ?? []).map((l) => l.floor_id)).size > 1;
  const marked = asked ?? (shown.length === ids.length ? [] : shown);
  const first = el("button", { type: "button", class: "fs-all", "aria-pressed": String(asked === null),
    "data-tip": multi ? "The floors the way walks on" : "Every floor of the building" }, multi ? "Way" : "All");
  first.addEventListener("click", () => w.setFloors(null));
  const parts = [first, el("div", { class: "fs-sep", role: "separator" })];
  for (const f of [...floors].reverse()) { // (the top one first)
    const here = marked.includes(f.id), name = floorName(f.id);
    const b = el("button", { type: "button", "data-id": f.id, "aria-current": here ? "true" : null,
      "aria-label": `${name}${here ? ", shown" : ""}`, "data-tip": `${name} · ${WITH_OTHERS}` },
    f.properties.code || f.id.split("-").at(-1));
    const withOthers = () => w.setFloors(toggled(asked ?? shown, f.id, ids, { every: !multi }));
    onHold(b, withOthers);
    b.addEventListener("click", (e) => (together(e) ? withOthers() : w.setFloor(f.id)));
    parts.push(b);
  }
  box.replaceChildren(...parts);
}

async function fly() {
  if (!state.world?.flyRoute || !state.route) return;
  stopPlaying();
  state.player = state.world;
  $("fly").disabled = true;
  try {
    await state.world.flyRoute();
  } finally {
    $("fly").disabled = false;
  }
}

function setView(view, { quiet = false } = {}) {
  if (!["plan", "3d", "both"].includes(view)) view = "plan";
  if (state.playing) stopPlaying();
  state.view = view;
  $("panes").className = view;
  $("views").dataset.view = view;
  for (const b of $("view-mode").querySelectorAll("button")) {
    b.classList.toggle("active", b.dataset.view === view);
    b.setAttribute("aria-pressed", String(b.dataset.view === view));
  }
  if (!quiet) save("storeypath.navigate.view", view);
  if (view !== "plan") drawWorld();
}

// Per-browser conveniences (the view chosen); never needed to work.
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
    // not kept: nothing else changes
  }
}

if (typeof document !== "undefined" && document.getElementById("panel")) {
  start().catch((e) => {
    message(e.message);
    toast(e.message, true);
  });
}
