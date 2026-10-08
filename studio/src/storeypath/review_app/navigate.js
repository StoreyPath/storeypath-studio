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

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(typeof location === "undefined" ? "" : location.search); // (none in a test)
const CODE = params.get("p");
const PLAN_ENGINE = "/viewer/svg/dist/index.js"; // the plan engine, compiled (npm run build in viewer/svg)
const WORLD = "/viewer/src/world/world.js";
const CHOICES_SHOWN = 60;
const STEP_ICONS = { start: "●", walk: "↑", take: "⇅", arrive: "⚑" };
const BY_WORDS = { lift: "Lift", stairs: "Stairs", escalator: "Escalator", ramp: "Ramp" };

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
  asking: 0, // the latest way asked for: an older answer is dropped
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

/** The network's places to choose from: its kiosks, then its entrances (ways in from
 * outside), then every room a person arrives in, each with its floor; ``id`` is what
 * the API is asked with (a kiosk's item, an entrance's node, a space's or zone's ID). */
export function choicesOf(network) {
  const floors = new Map(network.floors.map((f) => [f.id, f.name]));
  const places = new Map(network.places.map((p) => [p.id, p]));
  const labelOf = (id) => places.get(id)?.label ?? "the room";
  const out = [];
  const nodes = [...network.nodes].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (const n of nodes) {
    if (n.kind === "kiosk" && n.item_id) {
      out.push({ id: n.item_id, node: n.id, group: "kiosk", floor: n.floor_id,
        label: `Kiosk in ${labelOf(n.zone_id || n.space_id)}`, sub: floors.get(n.floor_id) ?? n.floor_id });
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

/** The choices a search finds: every word of it in the label, the floor, the type or the ID. */
export function search(choices, query) {
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

  /** Choose by ID (a kiosk's item or node, an entrance's node, a place), quietly. */
  set(id) {
    const c = id ? state.choices.find((x) => x.id === id || x.node === id) : null;
    this.choose(c ?? null, { quiet: true });
    return Boolean(c);
  }
}

// ---- the page ----------------------------------------------------------------------

let from, to;

async function start() {
  from = new Picker($("from"), () => changed());
  to = new Picker($("to"), () => changed());
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
  if (!CODE) {
    message("No project: open this page from a project's page (Navigate).");
    return;
  }
  $("back").href = `/#/p/${encodeURIComponent(CODE)}`;
  $("back").textContent = "← Project";
  try {
    const me = await whoami();
    $("account").replaceChildren(accountMenu(me));
    state.project = await api(`projects/${encodeURIComponent(CODE)}`);
  } catch (e) {
    message(e.status === 404 ? "No such project, or it is not shared with you." : e.message);
    return;
  }
  document.title = `${state.project.project.name} · Navigate`;
  $("project-meta").textContent = state.project.project.name;
  const can = state.project.can;
  const sees = (b) => !can || ["view", "edit", "share"].includes(can.buildings?.[b.id] ?? can.project);
  state.buildings = state.project.locations.flatMap((l) => l.buildings)
    .filter((b) => b.floors.some((f) => f.converted) && sees(b));
  if (!state.buildings.length) {
    message("No building here may be shown whole to you: finding the way needs every floor of a building.");
    return;
  }
  $("building").replaceChildren(...state.buildings.map((b) => el("option", { value: b.id }, b.name)));
  $("building").disabled = state.buildings.length < 2;
  const wanted = params.get("building");
  const first = state.buildings.find((b) => b.id === wanted) ?? state.buildings[0];
  $("building").value = first.id;
  setView(saved("storeypath.navigate.view") || "plan", { quiet: true });
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
  await drawPlan();
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

function showRoute() {
  const r = state.route;
  $("result").hidden = !r;
  if (r) {
    const rides = r.changes.map((c) => c.by);
    const by = rides.length ? ` · by ${[...new Set(rides)].join(" and ")}` : "";
    $("summary").replaceChildren(
      el("strong", {}, `${Math.round(r.metres)} m`), el("span", {}, ` · about ${duration(r.seconds)}`),
      el("span", { class: "meta" }, by));
    $("steps").replaceChildren(...r.steps.map((s, i) => {
      const floor = s.kind === "take" ? s.to_floor_id : s.floor_id;
      const li = el("li", { class: `step ${s.kind}${s.by ? " " + s.by : ""}`, "data-floor": floor || null, tabindex: floor ? 0 : null,
        title: floor ? `Show ${floorName(floor)}` : null },
      el("span", { class: "icon", "aria-hidden": "true" }, STEP_ICONS[s.kind] ?? "·"),
      el("span", { class: "text" }, s.text,
        s.kind === "start" || s.kind === "arrive" ? el("span", { class: "sub" }, floorName(s.floor_id)) : null,
        s.kind === "take" ? el("span", { class: "sub" }, `${floorName(s.from_floor_id)} → ${floorName(s.to_floor_id)}`) : null));
      if (floor) {
        const go = () => showFloor(floor);
        li.addEventListener("click", go);
        li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      }
      return li;
    }));
  }
  drawFloorTabs();
  markSteps();
  drawPlan();
  if (state.world) drawWorldRoute();
  $("fly").disabled = !r || !state.world?.flyRoute;
}

// ---- the plan ----------------------------------------------------------------------

/** The floors as tabs: those the way crosses marked, in the order it crosses them. */
function drawFloorTabs() {
  const onRoute = new Map();
  (state.route?.legs ?? []).forEach((leg, i) => { if (!onRoute.has(leg.floor_id)) onRoute.set(leg.floor_id, i + 1); });
  const floors = state.network?.floors ?? []; // lowest first
  $("floors").replaceChildren(...floors.map((f) => el("button", {
    type: "button", class: [f.id === state.floor ? "active" : "", onRoute.has(f.id) ? "on-route" : ""].join(" ").trim() || null,
    "aria-pressed": String(f.id === state.floor), "data-floor": f.id,
    title: onRoute.has(f.id) ? `${f.name}: part ${onRoute.get(f.id)} of the way` : f.name, onclick: () => showFloor(f.id),
  }, f.name, onRoute.has(f.id) ? el("span", { class: "leg" }, String(onRoute.get(f.id))) : null)));
}

function showFloor(id) {
  if (!id) return;
  state.floor = id;
  state.fitWay = false;
  drawFloorTabs();
  markSteps();
  drawPlan();
}

/** The steps on the floor shown, marked. */
function markSteps() {
  for (const li of $("steps").querySelectorAll("li[data-floor]")) li.classList.toggle("here", li.dataset.floor === state.floor);
}

/** The plan engine (viewer/svg, built), or null when it is not built here. */
async function planModule() {
  if (state.planModule === undefined) {
    try {
      state.planModule = await import(PLAN_ENGINE);
    } catch {
      state.planModule = null;
    }
  }
  return state.planModule;
}

async function drawPlan() {
  if (!state.bytes || !state.floor || !state.network) return;
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
      state.engine = new mod.FloorPlanEngine($("plan"), { title: "Floor plan with the way", interactive: () => false, interactiveItems: false });
    }
    const plan = mod.floorFromPackage(state.pkg, state.floor);
    const same = state.planShows === state.floor; // the same floor again: the view stays
    state.engine.setFloor(plan, { fit: !same });
    state.planShows = state.floor;
    if (typeof state.engine.showRoute === "function") {
      state.engine.showRoute(state.route, { floorName }); // over the whole floor (Fit: the way alone)
    }
    note.hidden = true;
  } catch (e) {
    note.textContent = `The plan could not be drawn: ${e.message}`;
    note.hidden = false;
  }
}

/** The floor whole; again, the way on it alone (and so on, each press). */
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
      state.world = new StoreyPathWorld($("world3d"), { labels: true });
    }
    if (state.worldShows !== state.building) {
      note.textContent = "Building the 3D view…";
      note.hidden = false;
      await state.world.open(new Blob([state.bytes]));
      state.worldShows = state.building;
      state.world.setFloor(null);
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
  w.showRoute(state.route);
  $("fly").disabled = !state.route || typeof w.flyRoute !== "function";
}

async function fly() {
  if (!state.world?.flyRoute || !state.route) return;
  $("fly").disabled = true;
  try {
    await state.world.flyRoute({ seconds: Math.min(20, Math.max(6, state.route.seconds / 8)) });
  } finally {
    $("fly").disabled = false;
  }
}

function setView(view, { quiet = false } = {}) {
  if (!["plan", "3d", "both"].includes(view)) view = "plan";
  state.view = view;
  $("panes").className = view;
  $("views").dataset.view = view;
  for (const b of $("view-mode").querySelectorAll("button")) b.classList.toggle("active", b.dataset.view === view);
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
