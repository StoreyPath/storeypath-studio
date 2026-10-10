// StoreyPath 3D: a building of a project in 3D, the whole window, to look at it, walk
// through it and show it on a big screen. Read only. Part of StoreyPath Studio; opened by
// Review's "3D window" (its Share menu, its floor's inspector) and by a project's page
// ("Walk in 3D", a building's 3D, an export's 3D):
//
//   world.html?pkg=<one of Studio's packages>[&building=<id>][&floor=<id>]
//
// pkg is a package of Studio's own (/api/projects/<code>/…: the building as it is now,
// preview.storeypath?building=<id>, or an export). Also, as the page keeps them in its
// address as they change: mode=walk, xray=1, cutaway=1, explode=<m>, items=1 or 0
// (furniture and equipment; without it, shown when one floor is), hidden=1, style=real
// or model, quality=auto, high or low (without them, as this browser last had them, here,
// in Review or in Navigate). The world is StoreyPath's (viewer/src/world): this page is
// its frame, on Studio's theme, components and top bar.

import { accountMenu, sentAway, whoami } from "./account.js";
import { setupChrome } from "./chrome.js";
import { lookOptions, setupLook } from "./look.js";
import { el, icon, save, saved } from "./review/dom.js";
import { kbd } from "./review/keys.js";
import { hideTooltip } from "./review/tooltip.js";

const WORLD = "/viewer/src/world/world.js";
const THEME = "/viewer/src/theme.js";
const FINISHES = "/viewer/src/finishes.js";
const DOORS = "storeypath.world.doors"; // as Review keeps it: doors open as you walk into them, or by hand
const TURN = 0.12; // radians a second: the building turned slowly while presenting

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const flag = (name) => (params.get(name) === "1" ? true : params.get(name) === "0" ? false : null);

const state = {
  world: null, // StoreyPathWorld
  pkg: null, // the package it shows
  code: null, // the project's code, from the package's address
  source: null, // where the package comes from: "now" (the project as it is) or "export"
  labels: true,
  showMap: true,
  presenting: false,
  ownFullscreen: false, // full screen asked for by presenting: left with it
  turning: false, // presenting in the dollhouse: the building turned slowly
  turnAt: 0,
  look: null, // the look's and quality's controls (look.js)
  theme: null, // the viewer's TYPE_COLORS and typeLabel
  finishes: null, // the viewer's finishOf
  plan: null, // the minimap's floor plan, and its floor
  planFloor: null,
  doorAim: null, // walking: the door at the cross ({id, open}) or null
};

// ---- the package's address: one of Studio's own --------------------------------------------

/** A package address of Studio's own (/api/projects/<code>/…, this origin), or null. */
export function ownPackage(raw) {
  if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return null;
  try {
    const u = new URL(raw, location.origin);
    if (u.origin !== location.origin || !/^\/api\/projects\/[^/]+\/./.test(u.pathname)) return null;
    return u.pathname + u.search;
  } catch {
    return null;
  }
}

const codeOf = (path) => decodeURIComponent(path.split("/")[3] || "");

// ---- small things ---------------------------------------------------------------------------

let toastTimer;
function toast(text, error = false) {
  const t = $("toast");
  t.textContent = text;
  t.classList.toggle("error", error);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), error ? 9000 : 3200);
}

const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const typeWords = (t) => cap(state.theme ? state.theme.typeLabel(t || "unspecified") : String(t || "").replaceAll("_", " "));
const typeColour = (t) => state.theme?.TYPE_COLORS[t] || state.theme?.TYPE_COLORS.unspecified || "#999";
const floorName = (id) => state.pkg?.get(id)?.properties.name ?? id;

/** A toggle kept in the address (true or false: 1 or 0; a word as it is; null: taken out of it). */
function remember(name, value) {
  const url = new URL(location.href);
  if (value === null || value === undefined) url.searchParams.delete(name);
  else url.searchParams.set(name, typeof value === "string" ? value : typeof value === "number" ? String(value) : value ? "1" : "0");
  history.replaceState(null, "", url);
}

function problem(title, text, action = null) {
  $("loading").hidden = true;
  $("problem").replaceChildren(el("h3", {}, title), el("p", {}, text), action);
  $("problem").hidden = false;
}

// ---- where you are --------------------------------------------------------------------------

function renderCrumbs() {
  const pkg = state.pkg, w = state.world;
  const parts = [el("a", { class: "crumb", href: "/" }, "Projects")];
  const sep = () => el("span", { class: "crumb-sep", "aria-hidden": "true" }, "/");
  if (pkg) {
    parts.push(sep(), el("a", { id: "crumb-project", class: "crumb", href: `/#/p/${encodeURIComponent(state.code || pkg.project.id)}`,
      "data-tip": "The project's page" }, pkg.project.name));
    const b = w?.building ? pkg.get(w.building) : null;
    if (b) {
      parts.push(sep());
      const name = b.properties.name || b.properties.code;
      if (pkg.buildings.length > 1) {
        const button = el("button", { type: "button", class: "crumb crumb-building", "aria-haspopup": "menu", "aria-expanded": "false",
          "data-tip": "Another building", "aria-current": "page" }, el("span", {}, name), icon("chevron-down", { size: 14 }));
        button.addEventListener("click", (e) => {
          e.stopPropagation();
          buildingMenu(button);
        });
        parts.push(button);
      } else {
        parts.push(el("span", { class: "crumb crumb-here", "aria-current": "page", "data-tip": b.id }, name));
      }
      document.title = `${name} · ${pkg.project.name} · StoreyPath 3D`;
    }
  }
  $("crumbs").replaceChildren(...parts);
}

function buildingMenu(button) {
  closeMenu();
  const w = state.world;
  const menu = el("div", { class: "menu", role: "menu", id: "building-menu" },
    el("div", { class: "menu-section" }, "Buildings"),
    state.pkg.buildings.map((b) => {
      const item = el("button", { type: "button", role: "menuitemradio", "aria-checked": String(b.id === w.building) },
        icon(b.id === w.building ? "check" : "building-2"), el("span", { class: "label" }, b.properties.name || b.properties.code));
      item.addEventListener("click", () => {
        closeMenu();
        if (b.id !== w.building) w.setBuilding(b.id);
      });
      return item;
    }));
  document.body.append(menu);
  const r = button.getBoundingClientRect();
  Object.assign(menu.style, { left: `${r.left}px`, top: `${r.bottom + 4}px` });
  button.setAttribute("aria-expanded", "true");
  menu.querySelector("[aria-checked='true']")?.focus();
  const away = (e) => {
    if (!menu.contains(e.target)) closeMenu();
  };
  setTimeout(() => document.addEventListener("click", away, { once: true }));
  menu.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      closeMenu();
      button.focus();
    }
  });
}

function closeMenu() {
  $("building-menu")?.remove();
  document.querySelector(".crumb-building")?.setAttribute("aria-expanded", "false");
}

// ---- the floors -----------------------------------------------------------------------------

function renderFloors() {
  const w = state.world, pkg = state.pkg;
  const box = $("floor-stack");
  if (!w || !pkg || !w.building) return box.replaceChildren();
  const floors = [...pkg.floorsOf(w.building)].reverse(); // the top one first
  const walking = w.mode === "walk";
  const current = walking ? w.walkFloor : w.floor;
  const parts = [];
  if (!walking && floors.length > 1) {
    const all = el("button", { type: "button", class: "fs-all", "aria-pressed": String(current === null),
      "data-tip": "Every floor of the building" }, "All");
    all.addEventListener("click", () => w.setFloor(null));
    parts.push(all, el("div", { class: "fs-sep", role: "separator" }));
  }
  for (const f of floors) {
    const here = f.id === current;
    const b = el("button", { type: "button", "data-id": f.id, "aria-current": here ? "true" : null,
      "aria-label": `${f.properties.name}${here ? ", shown" : ""}`, "data-tip": f.properties.name },
    f.properties.code || f.id.split("-").at(-1));
    b.addEventListener("click", () => w.setFloor(f.id));
    parts.push(b);
  }
  box.replaceChildren(...parts);
  box.hidden = !floors.length;
}

/** One floor up (+1) or down (−1) from the one shown (all shown: the ground floor). */
function stepFloor(step) {
  const w = state.world, pkg = state.pkg;
  if (!w || !pkg) return;
  const list = pkg.floorsOf(w.building);
  if (!list.length) return;
  const i = list.findIndex((f) => f.id === w.floor);
  const ground = Math.max(0, list.findIndex((f) => f.properties.ordinal === 0));
  const next = i < 0 ? ground : i + step;
  if (next < 0 || next >= list.length) return toast(step > 0 ? "This is the top floor" : "This is the lowest floor");
  w.setFloor(list[next].id);
}

// ---- the toggles ------------------------------------------------------------------------------

const pressed = (id, on) => $(id).setAttribute("aria-pressed", String(Boolean(on)));

function setXray(on) {
  state.world.setXray(on);
  pressed("xray", on);
  remember("xray", on || null);
}

function setCutaway(on) {
  state.world.setCutaway(on);
  pressed("cutaway", on);
  remember("cutaway", on || null);
}

function setLabels(on) {
  state.labels = on;
  state.world.setLabels(on);
  pressed("labels", on);
}

function setItems(on) {
  state.world.setItems(on);
  remember("items", on);
  renderItems();
}

/** The Items button says whether items are drawn now: as asked, or as the view has them. */
function renderItems() {
  if (state.world) pressed("items", state.world.items);
}

function setHidden(on) {
  state.world.setShowHidden(on);
  pressed("hidden-spaces", on);
  remember("hidden", on || null);
  state.planFloor = null; // the map drawn again
}

function setExplode(m, { quiet = false } = {}) {
  const w = state.world;
  m = Math.max(0, Math.min(8, Number(m) || 0));
  $("explode").value = String(m);
  $("explode-box").classList.toggle("on", m > 0);
  w.setExplode(m);
  if (m > 0 && w.floor !== null && w.mode !== "walk") w.setFloor(null); // floors apart: every floor
  if (!quiet) remember("explode", m || null);
}

function setMap(on) {
  state.showMap = on;
  pressed("map", on);
  showWalk();
}

function setDoors(auto) {
  state.world.setDoors(auto ? "auto" : "manual");
  save(DOORS, auto ? "auto" : "manual");
  pressed("doors", auto);
}

function setMode(mode) {
  const w = state.world;
  if (!w || !w.package || mode === w.mode) return;
  w.setMode(mode);
}

// ---- presenting ---------------------------------------------------------------------------------

function setPresenting(on) {
  if (on === state.presenting) return;
  state.presenting = on;
  hideTooltip();
  closeMenu();
  document.body.classList.toggle("presenting", on);
  pressed("present", on);
  const hint = $("present-hint");
  clearTimeout(setPresenting.timer);
  if (on) {
    hint.hidden = false;
    hint.classList.remove("fading");
    setPresenting.timer = setTimeout(() => {
      hint.classList.add("fading");
      setPresenting.timer = setTimeout(() => (hint.hidden = true), 400);
    }, 2600);
    if (!document.fullscreenElement && document.fullscreenEnabled) {
      document.documentElement.requestFullscreen?.().then(() => (state.ownFullscreen = true)).catch(() => {});
    }
    setTurning(state.world?.mode === "dollhouse");
  } else {
    hint.hidden = true;
    setTurning(false);
    if (state.ownFullscreen && document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    state.ownFullscreen = false;
  }
  remember("present", null);
}

/** Presenting in the dollhouse: the building turned slowly, until someone moves the view. */
function setTurning(on) {
  state.turning = Boolean(on);
  state.turnAt = 0;
  if (state.turning) requestAnimationFrame(turn);
}

function turn(t) {
  const w = state.world;
  if (!state.turning || !w || w.mode !== "dollhouse" || w.paused) {
    state.turning = false;
    return;
  }
  const dt = state.turnAt ? Math.min(0.1, (t - state.turnAt) / 1000) : 0;
  state.turnAt = t;
  const cam = w.camera, c = w.target, a = TURN * dt;
  const dx = cam.position.x - c.x, dz = cam.position.z - c.z;
  cam.position.x = c.x + dx * Math.cos(a) - dz * Math.sin(a);
  cam.position.z = c.z + dx * Math.sin(a) + dz * Math.cos(a);
  requestAnimationFrame(turn);
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  else document.documentElement.requestFullscreen?.().catch(() => toast("This browser would not go full screen", true));
  state.ownFullscreen = false;
}

// ---- what is chosen: its details ------------------------------------------------------------------

function closeDetails() {
  $("details").hidden = true;
  $("details").replaceChildren();
}

function head(title, kind, swatch, ar = null) {
  const close = el("button", { type: "button", class: "btn-ghost btn-icon btn-sm", "aria-label": "Close", "data-key": "escape" }, icon("x"));
  close.addEventListener("click", () => {
    closeDetails();
    state.world.select(null, { go: false });
  });
  return el("div", { class: "w-card-head" },
    el("div", { class: "grow" }, el("h3", {}, title), ar ? el("div", { class: "w-card-ar", dir: "rtl", lang: "ar" }, ar) : null,
      el("div", { class: "w-card-kind" }, el("span", { class: "swatch", style: `background:${swatch}` }), kind)),
    close);
}

function idRow(id) {
  const copy = el("button", { type: "button", class: "btn-ghost btn-icon btn-sm", "aria-label": "Copy the ID", "data-tip": "Copy the ID" }, icon("copy", { size: 14 }));
  copy.addEventListener("click", () => {
    navigator.clipboard?.writeText(id).then(() => toast("ID copied"), () => toast(id));
  });
  return [el("dt", {}, "ID"), el("dd", {}, el("span", { class: "w-id" }, el("code", {}, id), copy))];
}

function walkHere(id) {
  const b = el("button", { type: "button", class: "btn-primary" }, icon("footprints"), "Walk here");
  b.addEventListener("click", () => {
    state.world.setMode("walk");
    state.world.select(id);
  });
  return b;
}

function reviewLink(feature) {
  const p = feature.properties;
  if (!state.code) return null;
  const hash = new URLSearchParams({ floor: p.floor_id });
  if (p.kind !== "item") hash.set("space", feature.id);
  return el("a", { class: "button", href: `/review.html?p=${encodeURIComponent(state.code)}#${hash}`,
    "data-tip": "This floor in Review, to correct it" }, icon("external-link"), "Review");
}

/** A room chosen: its name, type, number, ID, floor, area, finishes, seats, where its doors lead. */
function renderRoom(feature) {
  const p = feature.properties, pkg = state.pkg;
  const area = p.area_m2 ?? p.area;
  const fin = state.world.finishOf?.(feature.id);
  const finish = (code) => state.finishes?.finishOf(code)?.name ?? code;
  // its ways through (doors and openings, not windows), each room once
  const seen = new Set();
  const doors = pkg.doorsOf(feature.id).filter(({ door, space }) => door.properties.type !== "window"
    && !seen.has(space?.id ?? "outside") && seen.add(space?.id ?? "outside"));
  const rows = [
    p.number ? [el("dt", {}, "Number"), el("dd", {}, p.number)] : null,
    idRow(feature.id),
    [el("dt", {}, "Floor"), el("dd", {}, floorName(p.floor_id))],
    area ? [el("dt", {}, "Area"), el("dd", {}, `${Number(area).toFixed(1)} m²`)] : null,
    p.capacity ? [el("dt", {}, "Seats"), el("dd", {}, String(p.capacity))] : null,
    fin ? [el("dt", {}, "Floor finish"), el("dd", {}, finish(fin.floor))] : null,
    fin && p.kind === "space" ? [el("dt", {}, "Walls"), el("dd", {}, finish(fin.wall))] : null,
    p.hidden || p.ignored ? [el("dt", {}, "In Studio"), el("dd", {}, p.hidden ? "hidden" : "deleted")] : null,
  ].filter(Boolean);
  const leads = doors.length ? el("div", { class: "w-section" }, el("h4", {}, "Its doors lead to"),
    el("div", { class: "w-doors" }, doors.map(({ space }) => {
      if (!space) return el("button", { type: "button", disabled: true }, "Outside");
      const b = el("button", { type: "button" }, space.properties.name || typeWords(space.properties.type));
      b.addEventListener("click", () => state.world.select(space.id));
      return b;
    }))) : null;
  $("details").replaceChildren(
    head(p.name || typeWords(p.type), [typeWords(p.type), p.number].filter(Boolean).join(" · "), typeColour(p.type)),
    el("dl", {}, rows.flat()), leads,
    el("div", { class: "w-actions" }, walkHere(feature.id), reviewLink(feature)));
  $("details").hidden = false;
}

/** An item chosen: its type, its tag (its ID), where it stands, its details: those entered in
 * StoreyPath, and those the system that manages the asset keeps (never in a package). */
function renderItem(feature) {
  const p = feature.properties, pkg = state.pkg;
  const type = pkg.itemType(p.type);
  const room = pkg.get(p.zone_id) ?? pkg.get(p.space_id);
  const where = room ? [room.properties.name, room.properties.number].filter(Boolean).join(" ") || typeWords(room.properties.type) : "";
  const value = (f) => {
    const v = p.values?.[f.key];
    if (v === undefined || v === null || v === "") return el("span", { class: "muted" }, "—");
    return f.kind === "color" ? [el("span", { class: "swatch", style: `background:${v}` }), ` ${v}`] : String(v);
  };
  const fields = type?.fields ?? [];
  const ours = fields.filter((f) => f.owner !== "system"), theirs = fields.filter((f) => f.owner === "system");
  const rows = [
    idRow(feature.id),
    [el("dt", {}, "Floor"), el("dd", {}, floorName(p.floor_id))],
    where ? [el("dt", {}, "In"), el("dd", {}, where)] : null,
    [el("dt", {}, "Size"), el("dd", {}, `${[p.width_m, p.depth_m, p.height_m].map((v) => Number(v).toFixed(2)).join(" × ")} m`)],
    [el("dt", {}, "Mounted"), el("dd", {}, `${p.mount}${p.elevation_m ? `, ${Number(p.elevation_m).toFixed(2)} m up` : ""}`)],
    ...ours.map((f) => [el("dt", {}, f.name_en), el("dd", {}, value(f))]),
  ].filter(Boolean);
  $("details").replaceChildren(
    head(type?.name_en ?? p.name, [cap(p.category), p.type].join(" · "), type?.color ?? "#8a8a8a", type?.name_ar || null),
    el("dl", {}, rows.flat()),
    theirs.length ? el("div", { class: "w-section" }, el("p", { class: "w-note" },
      `Kept by the system that manages it: ${theirs.map((f) => f.name_en).join(", ")}`)) : null,
    el("div", { class: "w-actions" }, walkHere(feature.id), reviewLink(feature)));
  $("details").hidden = false;
}

// ---- walking ------------------------------------------------------------------------------------

function showWalk() {
  const w = state.world;
  const walking = w?.mode === "walk", locked = walking && w.walking;
  document.body.classList.toggle("walking", Boolean(walking));
  $("walk-hud").hidden = !walking;
  $("crosshair").hidden = !locked;
  $("walk-enter").hidden = !walking || locked;
  $("minimap").hidden = !walking || !state.showMap;
  renderHint();
}

function renderStairs() {
  const w = state.world;
  if (!w || w.mode !== "walk") return;
  const list = state.pkg.floorsOf(w.building);
  const i = list.findIndex((f) => f.id === w.walkFloor);
  const ways = [];
  if (w.atStairs && list[i + 1]) ways.push(`E or PgUp: up to ${list[i + 1].properties.name}`);
  if (w.atStairs && list[i - 1]) ways.push(`Q or PgDn: down to ${list[i - 1].properties.name}`);
  $("walk-stairs").textContent = ways.join(" · ");
}

/** Walking: up (+1) or down (−1) at stairs or a lift. */
function walkFloor(step) {
  const w = state.world;
  if (!w.atStairs) toast("Find stairs or a lift to go up or down");
  else if (!w.changeFloor(step)) toast(step > 0 ? "This is the top floor" : "This is the lowest floor");
  renderStairs();
}

// ---- the status bar ------------------------------------------------------------------------------

function renderHint() {
  const w = state.world;
  const hint = $("sb-hint");
  if (!w?.package) return hint.replaceChildren();
  const walking = w.mode === "walk";
  const parts = walking
    ? [icon("footprints", { size: 14 }), el("span", { class: "sb-mode" }, "Walk"),
      el("span", {}, w.walking ? "W A S D or the arrows to move · Shift to run · E or a click: the door at the cross · Esc frees the mouse"
        : "Click the view to look around")]
    : [icon("rotate-3d", { size: 14 }), el("span", { class: "sb-mode" }, "Dollhouse"),
      el("span", {}, "Drag to turn · Shift-drag or right-drag to move · scroll to zoom · click a room for its details")];
  hint.replaceChildren(...parts);
  // the door within reach at the cross (the world says which): E works it, and a click once the mouse looks
  const door = $("sb-door");
  const aim = walking ? state.doorAim : null;
  door.hidden = !aim;
  door.replaceChildren(...(aim ? [icon("door-open", { size: 14 }),
    `Door ahead: E${w.walking ? " or a click" : ""} ${aim.open ? "closes" : "opens"} it`] : []));
}

function renderLook() {
  const look = state.world?.look;
  if (!look) return;
  const why = { software: "software graphics", virtual: "virtual graphics", integrated: "built-in graphics", slow: "drew slowly" }[look.why];
  $("sb-look").replaceChildren(icon("gauge", { size: 13 }),
    `${cap(look.style)} · ${look.drawn === "low" ? "Low" : "High"}${look.quality === "auto" ? " (Auto" + (why ? `: ${why}` : "") + ")" : ""}`);
}

function renderSource() {
  const pkg = state.pkg, w = state.world;
  if (!pkg || !w?.building) return;
  const floors = pkg.floorsOf(w.building).length;
  const what = state.source === "now" ? "As it is now" : `Export #${pkg.manifest.export?.sequence ?? "?"}`;
  $("sb-source").replaceChildren(icon("building-2", { size: 13 }), `${floors} floor${floors === 1 ? "" : "s"} · ${what}`);
}

// ---- the minimap (walking) -------------------------------------------------------------------------

function drawMinimap() {
  requestAnimationFrame(drawMinimap);
  const w = state.world, mini = $("minimap");
  if (!w || w.mode !== "walk" || mini.hidden || !state.theme) return;
  const ctx = mini.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const size = mini.clientWidth;
  if (!size) return;
  if (mini.width !== Math.round(size * dpr)) mini.width = mini.height = Math.round(size * dpr);
  if (state.planFloor !== w.walkFloor) {
    state.plan = w.plan(w.walkFloor);
    state.planFloor = w.walkFloor;
  }
  const plan = state.plan;
  if (!plan) return;
  const me = w.player, css = getComputedStyle(document.documentElement);
  const scale = (size / 26) * dpr; // about 26 m across
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, mini.width, mini.height);
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(0, 0, mini.width, mini.height, 13 * dpr);
  ctx.clip();
  ctx.setTransform(scale, 0, 0, scale, mini.width / 2 - me.x * scale, mini.height / 2 - me.z * scale);
  const path = (rings) => {
    ctx.beginPath();
    for (const ring of rings) {
      ring.forEach(([x, z], k) => (k ? ctx.lineTo(x, z) : ctx.moveTo(x, z)));
      ctx.closePath();
    }
  };
  for (const s of plan.spaces) {
    path(s.rings);
    ctx.fillStyle = typeColour(s.type) + (s.id === w.room?.id ? "ff" : "88");
    ctx.fill("evenodd");
  }
  if (w.items) {
    for (const it of plan.items) {
      path([it.ring]);
      ctx.fillStyle = it.color;
      ctx.fill();
    }
  }
  path(plan.walls);
  ctx.fillStyle = css.getPropertyValue("--plan-walls").trim() || "#3d3d42";
  ctx.fill("evenodd");
  // you: a dot and the way you look
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  const accent = css.getPropertyValue("--accent").trim() || "#3b82f6";
  const cx = mini.width / 2, cy = mini.height / 2, a = Math.atan2(me.dz, me.dx);
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.arc(cx, cy, 34 * dpr, a - 0.5, a + 0.5);
  ctx.closePath();
  ctx.globalAlpha = 0.22;
  ctx.fillStyle = accent;
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.beginPath();
  ctx.arc(cx, cy, 5 * dpr, 0, Math.PI * 2);
  ctx.fillStyle = accent;
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2 * dpr;
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

// ---- the keys ---------------------------------------------------------------------------------------

const KEYS = [
  ["View", [["3", "Dollhouse"], ["4", "Walk"], ["x", "X-ray"], ["c", "Cutaway (the dollhouse)"], ["l", "Labels"], ["i", "Items"],
    ["pageup pagedown", "A floor up or down"]]],
  ["Walking", [["w a s d", "Move (or the arrows)"], ["shift", "Run"], ["e", "Open or close the door at the cross"],
    ["e q", "Up or down at stairs and lifts (or PgUp, PgDn)"], ["m", "The map"], ["escape", "Free the mouse"]]],
  ["The screen", [["p", "Present: the building alone"], ["t", "Presenting: turn the building, or stop it"], ["f", "Full screen"],
    ["?", "These keys"], ["escape", "Back: stop presenting, close what is open"]]],
];

function showKeys() {
  const list = $("keys-list");
  if (!list.childElementCount) {
    list.append(...KEYS.flatMap(([group, keys]) => [el("h3", {}, group),
      ...keys.flatMap(([chords, what]) => [el("span", { class: "k" }, chords.split(" ").map((c) => kbd(c))), el("span", {}, what)])]));
  }
  $("keys-dialog").showModal();
}

const typing = (t) => t?.closest?.("input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=button]), select, textarea, [contenteditable]");

function onKey(e) {
  // (a key the world took — E at a door it opened or shut — is not the page's)
  if (e.defaultPrevented || e.isComposing || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
  if (e.target.closest?.("dialog[open]")) return;
  const w = state.world;
  if (e.key === "Escape") {
    if ($("building-menu")) return closeMenu();
    if (state.presenting) return setPresenting(false);
    if (!$("details").hidden) {
      closeDetails();
      w?.select(null, { go: false });
    }
    return;
  }
  if (e.key === "?") return showKeys();
  if (!w?.package || e.repeat) return;
  const walking = w.mode === "walk";
  const k = e.code;
  const act = {
    Digit3: () => setMode("dollhouse"),
    Digit4: () => setMode("walk"),
    KeyX: () => setXray($("xray").getAttribute("aria-pressed") !== "true"),
    KeyC: () => !walking && setCutaway($("cutaway").getAttribute("aria-pressed") !== "true"),
    KeyL: () => setLabels(!state.labels),
    KeyI: () => setItems(!w.items),
    KeyM: () => walking && setMap(!state.showMap),
    KeyP: () => setPresenting(!state.presenting),
    KeyT: () => state.presenting && setTurning(!state.turning),
    KeyF: () => toggleFullscreen(),
    PageUp: () => (walking ? walkFloor(1) : stepFloor(1)),
    PageDown: () => (walking ? walkFloor(-1) : stepFloor(-1)),
    KeyE: () => walking && w.atStairs && walkFloor(1),
    KeyQ: () => walking && w.atStairs && walkFloor(-1),
  }[k];
  if (!act) return;
  if (act() !== false) e.preventDefault();
}

// ---- the page ---------------------------------------------------------------------------------------

function wire() {
  for (const b of $("mode").querySelectorAll("button")) b.addEventListener("click", () => setMode(b.dataset.mode));
  $("xray").addEventListener("click", () => setXray($("xray").getAttribute("aria-pressed") !== "true"));
  $("cutaway").addEventListener("click", () => setCutaway($("cutaway").getAttribute("aria-pressed") !== "true"));
  $("labels").addEventListener("click", () => setLabels(!state.labels));
  $("items").addEventListener("click", () => setItems(!state.world.items));
  $("hidden-spaces").addEventListener("click", () => setHidden($("hidden-spaces").getAttribute("aria-pressed") !== "true"));
  $("map").addEventListener("click", () => setMap(!state.showMap));
  $("doors").addEventListener("click", () => setDoors($("doors").getAttribute("aria-pressed") !== "true"));
  $("explode").addEventListener("input", (e) => setExplode(e.target.value));
  $("present").addEventListener("click", () => setPresenting(!state.presenting));
  $("fullscreen").addEventListener("click", toggleFullscreen);
  $("keys-open").addEventListener("click", showKeys);
  $("keys-dialog").addEventListener("click", (e) => {
    if (e.target === $("keys-dialog") || e.target.closest("[data-close]")) $("keys-dialog").close();
  });
  document.addEventListener("fullscreenchange", () => {
    pressed("fullscreen", Boolean(document.fullscreenElement));
    if (!document.fullscreenElement && state.ownFullscreen) { // Esc left full screen: presenting ends with it
      state.ownFullscreen = false;
      setPresenting(false);
    }
  });
  $("walk-enter").addEventListener("click", () => {
    document.activeElement?.blur?.(); // keys to the walker, not to a button
    state.world?.startWalking();
  });
  // presenting: the pointer shown while it moves; a drag or a scroll stops the turning
  let still;
  $("stage").addEventListener("pointermove", () => {
    if (!state.presenting) return;
    document.body.classList.add("pointer-moved");
    clearTimeout(still);
    still = setTimeout(() => document.body.classList.remove("pointer-moved"), 1800);
  });
  for (const type of ["pointerdown", "wheel"]) $("stage").addEventListener(type, () => setTurning(false), { passive: true });
  document.addEventListener("keydown", onKey);
  state.look = setupLook({ styles: [...$("look").querySelectorAll("button")], quality: $("quality") }, () => state.world);
  $("quality").addEventListener("change", () => remember("quality", $("quality").value));
  for (const b of $("look").querySelectorAll("button")) b.addEventListener("click", () => remember("style", b.dataset.style));
}

function listen(w) {
  w.addEventListener("buildingchange", () => {
    closeDetails();
    renderCrumbs();
    renderFloors();
    renderItems();
    renderSource();
    remember("building", w.building);
  });
  w.addEventListener("floorchange", ({ detail: { id } }) => {
    renderFloors();
    renderItems();
    if (w.mode !== "walk") remember("floor", id);
    if (id !== null && Number($("explode").value) > 0) setExplode(0, { quiet: false });
  });
  w.addEventListener("modechange", ({ detail: { mode } }) => {
    for (const b of $("mode").querySelectorAll("button")) b.setAttribute("aria-checked", String(b.dataset.mode === mode));
    closeDetails();
    if (mode === "walk") setTurning(false);
    else if (state.presenting) setTurning(true);
    state.doorAim = null;
    renderFloors();
    renderItems();
    showWalk();
    remember("mode", mode === "walk" ? "walk" : null);
  });
  w.addEventListener("walklock", () => showWalk());
  w.addEventListener("roomchange", ({ detail }) => {
    const type = typeWords(detail.type);
    $("walk-room").textContent = detail.id ? detail.name || type : "Outside";
    // its type, unless its name says it already (CORRIDOR, a corridor), and its number
    const said = detail.name && detail.name.toLowerCase() !== type.toLowerCase() ? type : "";
    $("walk-meta").textContent = detail.id ? [said, detail.number].filter(Boolean).join(" · ") : "";
    renderStairs();
  });
  w.addEventListener("select", ({ detail: { id, feature } }) => {
    if (!id || !feature || w.mode === "walk") return closeDetails();
    if (feature.properties.kind === "item") renderItem(feature);
    else renderRoom(feature);
  });
  w.addEventListener("dooraim", ({ detail }) => {
    state.doorAim = detail?.id ? detail : null;
    renderHint();
  });
  w.addEventListener("doorchange", ({ detail }) => {
    if (state.doorAim?.id === detail.id) state.doorAim = { id: detail.id, open: detail.open };
    renderHint();
  });
  w.addEventListener("lookchange", renderLook);
}

async function start() {
  setupChrome();
  wire();
  const path = ownPackage(params.get("pkg"));
  whoami().then((me) => $("account").replaceChildren(accountMenu(me))).catch(() => {});
  if (!path) {
    problem("Nothing to show", "This page shows a building of a project in Studio in 3D. Open it from a project's page "
      + "(Walk in 3D) or from Review (3D window).", el("a", { class: "button btn-primary", href: "/" }, "All projects"));
    return;
  }
  state.code = codeOf(path);
  state.source = /\/preview\.storeypath(\?|$)/.test(path) ? "now" : "export";
  const [{ StoreyPathWorld }, theme, finishes] = await Promise.all([import(WORLD), import(THEME), import(FINISHES).catch(() => null)]);
  state.theme = theme;
  state.finishes = finishes;
  let bytes;
  try {
    const res = await fetch(path);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      if (sentAway(res, data)) return;
      problem(res.status === 404 ? "Not found" : "It could not be read",
        res.status === 404 ? "No such project or building, or it is not shared with you."
          : res.status === 403 ? `You may not see this building whole: ${data.error || "ask whoever shares the project"}.`
            : data.error || `${res.status} ${res.statusText}`,
        el("a", { class: "button", href: "/" }, "All projects"));
      return;
    }
    $("loading-text").textContent = "Building the world…";
    bytes = await res.blob();
  } catch (e) {
    problem("It could not be read", e.message);
    return;
  }
  const look = lookOptions();
  const style = params.get("style") === "model" || params.get("style") === "real" ? params.get("style") : look.style;
  const quality = ["auto", "high", "low"].includes(params.get("quality")) ? params.get("quality") : look.quality;
  let w;
  try {
    w = state.world = new StoreyPathWorld($("world"), { showHidden: params.get("hidden") === "1", items: flag("items"),
      style, quality, doors: saved(DOORS) === "manual" ? "manual" : "auto" });
  } catch (e) {
    problem("3D cannot be shown here", `This browser cannot draw the 3D view (it needs WebGL 2): ${e.message}`);
    return;
  }
  window.storeypathWorld = w; // for the console
  listen(w);
  state.look.attach(w);
  renderLook();
  try {
    state.pkg = await w.open(bytes);
  } catch (e) {
    problem("It could not be shown", e.message || String(e));
    return;
  }
  // ?building=<id>, or ?floor=<id> (its building)
  const pkg = state.pkg;
  const floor = params.get("floor") && pkg.get(params.get("floor"));
  const building = floor ? floor.properties.building_id : params.get("building");
  if (building && pkg.get(building) && building !== w.building) await w.setBuilding(building);
  if (floor) w.setFloor(floor.id);
  pressed("hidden-spaces", params.get("hidden") === "1");
  pressed("doors", w.doors !== "manual");
  if (params.get("xray") === "1") setXray(true);
  if (params.get("cutaway") === "1") setCutaway(true);
  if (Number(params.get("explode")) > 0) setExplode(params.get("explode"));
  if (params.get("mode") === "walk") w.setMode("walk");
  $("loading").hidden = true;
  $("toolbar").hidden = false;
  renderCrumbs();
  renderFloors();
  renderItems();
  renderSource();
  showWalk();
  requestAnimationFrame(drawMinimap);
  document.body.classList.add("ready");
}

window.storeypathWorldPage = { state, setPresenting, setTurning, setMode, setExplode, showKeys };
start().catch((e) => {
  problem("Something went wrong", e.message || String(e));
  console.error(e);
});
