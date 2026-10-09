// The floor in 3D, and walked through.
// One view, three ways to see the floor, in the same place: the plan (2D), the floor as
// built (3D: orbit it) and a walk through it. The floor, what is chosen and the panels stay
// as they are from one to another, and the plan keeps its view. The 3D world (Studio's
// package of the floor's building, not recorded as an export) is built once and kept:
// switching is at once. What changes shows in it at once: items (yours, or others' as
// they are saved) are drawn again on their floor alone, a room's name or type its label
// and floor; walls, doors, dividers and spaces (drawn on the plan) come with the floor
// read again, and that floor alone is built again when the reading is done. In 3D and
// walking, a click chooses a room or an item (walking: at the cross), items are placed as
// on the plan (fit.js: held in their room, lined up by the magnet; Alt: as they are) and,
// in 3D, dragged; right-click (or 2) shows that place on the plan, to draw there. In 3D
// the building's floors can all be shown (the floor stack's All); walking, PgUp and PgDn
// go up and down at stairs and lifts (E is the world's: doors).

import { editable } from "./access.js";
import { emit, on } from "./bus.js";
import { $, save, saved, svg } from "./dom.js";
import { clearPreview } from "./drawing.js";
import * as finish from "./finish.js";
import { changeAsset, drawGuides, placeAsset, placing, renderAssets } from "./items.js";
import { lookOptions } from "../look.js";
import { closeMenu, menu3d } from "./menu.js";
import { say, toast } from "./notify.js";
import { centerOn, planPoint, scaleFor, viewport } from "./plan.js";
import { select, selectAsset } from "./selection.js";
import { settle } from "../fit.js";
import { openFloor } from "./floor.js";
import { BASE, buildingOf, readable, round4, state, typeLabel, typeOf, view3d } from "./state.js";
import { setTool } from "./tools.js";

const REACH_3D = 0.3; // m: how near a wall or an item the magnet takes an item in 3D (fit.js)
view3d.allFloors = false; // in 3D, the building's floors all shown (not walking)
view3d.doorAim = null; // walking: the door aimed at ({id, open}), when the world opens doors

/** Walking, doors open as you walk into them ("auto"), or only with E or a click
 * ("manual"): remembered in this browser, for a world that opens doors. */
export function doorsMode() {
  return saved("storeypath.world.doors") === "manual" ? "manual" : "auto";
}

export function setDoorsMode(mode) {
  save("storeypath.world.doors", mode);
  if (typeof view3d.world?.setDoors === "function") view3d.world.setDoors(mode);
  emit("settings");
}

/** Choose a room or an item in the 3D view too, without hearing it back as a click there. */
export function pick3d(id, { go = false } = {}) {
  if (!view3d.shown || !view3d.world || !view3d.building) return;
  view3d.picking = true;
  try {
    view3d.world.select(id, { go });
  } finally {
    view3d.picking = false;
  }
}

/** The plan (2D), the floor in 3D, or walking through it ("2d", "3d", "walk"), in one view. */
export async function setView(mode) {
  if (mode === view3d.mode) return;
  if (mode !== "2d" && !state.floor?.converted_at) return toast("This floor is not converted yet: there is nothing to show in 3D", true);
  const was = view3d.mode;
  if (was === "2d") {
    const { w, h } = viewport();
    view3d.plan = w && h ? planPoint(w / 2, h / 2) : null; // where the plan looks: where walking starts
  }
  view3d.from = was;
  view3d.mode = mode;
  view3d.shown = mode !== "2d";
  $("map").classList.toggle("in3d", view3d.shown);
  $("map").classList.toggle("walking", mode === "walk");
  $("world3d").hidden = !view3d.shown;
  closeMenu();
  clearPreview();
  drawGuides();
  view3d.world?.ghost(null);
  showWalk();
  emit("view"); // (a tool that does not work in this view is put down: tools.js)
  if (!view3d.shown) {
    view3d.world?.pause(); // kept as it is, for the next time
    return;
  }
  view3d.world?.resume();
  await refresh3d();
}

/** The 3D view brought up to date: one build at a time; asked again while one is
 * being built (another floor, a building of its own, a change saved), it is done once
 * more when that one is done, with what is asked then. */
export async function refresh3d() {
  if (!view3d.shown || !state.floor) return;
  if (view3d.busy) {
    view3d.again = true;
    return view3d.busy;
  }
  view3d.busy = (async () => {
    try {
      do {
        view3d.again = false;
        if (!(await build3d())) break;
      } while (view3d.again && view3d.shown && state.floor);
    } finally {
      view3d.busy = null;
      view3d.again = false;
    }
  })();
  return view3d.busy;
}

/** The 3D view of the floor shown: its building built (again, whole, when it is another
 * or was moved), the floors changed since read again (they alone), then the floor, its
 * items and rooms as the page has them, what is chosen, and the mode. Whether it could be
 * shown. */
export async function build3d() {
  if (!state.floor.converted_at) {
    toast("This floor is not converted yet: there is nothing to show in 3D", true);
    setView("2d");
    return false;
  }
  const building = buildingOf(state.floor.id);
  const url = `/api/${BASE}/preview.storeypath?building=${encodeURIComponent(building)}`;
  try {
    if (!view3d.world) {
      const { StoreyPathWorld } = await import("/viewer/src/world/world.js");
      view3d.world = new StoreyPathWorld($("world3d"), { showHidden: state.showHidden, ...lookOptions(), doors: doorsMode() });
      view3d.look.attach(view3d.world);
      setup3d(view3d.world);
    }
    if (view3d.stale || view3d.building !== building) {
      say("Building the 3D view…");
      // up to date as of now: a change saved while it is built makes its floor to read again
      view3d.stale = false;
      const dirty = [...view3d.dirty].filter((f) => f.startsWith(`${building}-`));
      dirty.forEach((f) => view3d.dirty.delete(f));
      const other = view3d.building && view3d.building !== building;
      try {
        await view3d.world.open(url);
      } catch (e) {
        view3d.stale = true;
        throw e;
      }
      view3d.building = building;
      if (other && view3d.world.mode === "walk") { // into another building: walking again, from its middle
        view3d.world.setMode("dollhouse");
        view3d.from = "3d";
      }
    } else {
      const floors = [...view3d.dirty].filter((f) => f.startsWith(`${building}-`));
      if (floors.length) {
        say("Showing the change in 3D…");
        floors.forEach((f) => view3d.dirty.delete(f));
        try {
          await view3d.world.reload(url, { floors });
        } catch (e) {
          floors.forEach((f) => view3d.dirty.add(f));
          throw e;
        }
      }
    }
    // the floor shown now, when it is of the building built (else that one is built next)
    if (state.floor?.id.startsWith(`${view3d.building}-`)) {
      view3d.world.setFloor(view3d.allFloors && view3d.mode === "3d" ? null : state.floor.id);
      update3d();
    }
    pick3d(state.selected || state.asset || null, { go: false });
    say("");
    applyMode();
    return true;
  } catch (e) {
    say("");
    toast(`The 3D view could not be shown: ${e.message}`, true);
    setView("2d");
    return false;
  }
}

/** In 3D, every floor of the building shown (true), or the floor alone. */
export function setAllFloors(on) {
  view3d.allFloors = Boolean(on);
  if (view3d.world && view3d.building && view3d.mode === "3d") view3d.world.setFloor(on ? null : state.floor.id);
  emit("floors3d");
}

/** The world in the mode asked for: orbiting (back where it was before walking), or
 * walking: from the room or item chosen, else from where the 3D view looked, or the plan. */
function applyMode() {
  const w = view3d.world;
  if (!w || !view3d.shown) return;
  draggable3d();
  if (view3d.mode === "walk" && w.mode !== "walk") {
    const chosen = state.selected || state.asset;
    w.setMode("walk", { floor: state.floor.id, ...walkFrom() });
    if (chosen) pick3d(chosen, { go: true }); // walked to: in the room, before the item
  } else if (view3d.mode === "3d" && w.mode !== "dollhouse") {
    w.setMode("dollhouse", { back: true });
    if (view3d.allFloors) w.setFloor(null);
  }
  showWalk();
}

/** Where walking starts when nothing is chosen: where the 3D view looked; from the plan,
 * the middle of its view, facing up the plan. */
function walkFrom() {
  const w = view3d.world;
  if (view3d.from === "3d") return { at: w.target };
  const c = view3d.plan;
  const p = c && w.worldPoint(c), up = c && w.worldPoint([c[0], c[1] + 1]);
  return p && up ? { at: p, heading: Math.atan2(-(up.x - p.x), -(up.z - p.z)) } : {};
}

/** Items carried in 3D by a drag: only by who may change the floor, and not while placing. */
export function draggable3d() {
  view3d.world?.setDraggable(editable() && state.tool !== "place" && !finish.painting());
}

/** An item as the 3D world takes it: where it stands (the plan's metres) and its type's size. */
function given3d(a) {
  const t = typeOf(a.type);
  return { id: a.id, type: a.type, x: a.x, y: a.y, rotation: a.rotation || 0, ...(t ? { width: t.width, depth: t.depth,
    height: t.height, mount: t.mount || "floor", elevation: t.elevation ?? null, color: t.color, grade: t.grade ?? null } : {}) };
}

/** The floor shown, in 3D as the page has it now: its items drawn again (that floor's
 * alone: milliseconds), and its rooms' names and types. */
export function update3d() {
  const w = view3d.world, f = state.floor;
  if (!w || !f || !view3d.building || !f.id.startsWith(`${view3d.building}-`)) return;
  w.setFloorItems(f.id, (f.items || []).filter((a) => !a.retired).map(given3d));
  for (const s of f.spaces) sync3dSpace(s);
  view3d.crossKey = ""; // the cross's ghost seen again (the magnet sees the new items)
  if (state.tool === "place") aimSoon();
}

/** A room as corrected here, in 3D: its label at once, its floor's and walls' finishes in
 * place (nothing built again), the floor built again when its type changed. */
export function sync3dSpace(s) {
  const w = view3d.world;
  const p = w?.package?.get(s.id)?.properties;
  if (!p) return;
  const now = { name: s.name || null, number: s.number || null, type: s.type, hidden: Boolean(s.hidden), ignored: Boolean(s.ignored),
    floor_finish: s.floor_finish ?? null, wall_finish: s.wall_finish ?? null };
  const was = { name: p.name || null, number: p.number || null, type: p.type, hidden: Boolean(p.hidden), ignored: Boolean(p.ignored),
    floor_finish: p.floor_finish ?? null, wall_finish: p.wall_finish ?? null };
  if (Object.keys(now).some((k) => now[k] !== was[k])) w.updateSpace(s.id, now);
}

/** Floors whose walls, doors or spaces changed: read again in 3D (they alone), soon when
 * it is shown, else when it is next. */
export function dirty3d(floors) {
  for (const f of floors) view3d.dirty.add(f);
  if (!view3d.shown) return;
  clearTimeout(view3d.soon);
  view3d.soon = setTimeout(refresh3d, 250);
}

/** What the 3D world says: a click on a room or an item (chosen here, as on the plan), a
 * click while placing (placed there), an item carried, the room walked into, the mouse
 * taken or given back, another floor walked to. */
function setup3d(world) {
  world.setLabels(state.showLabels !== false);
  world.addEventListener("select", ({ detail: { id } }) => {
    if (view3d.picking) return; // our own choice, echoed back
    if (id && (state.floor?.items || []).some((a) => a.id === id)) return id !== state.asset && selectAsset(id);
    if (id === null && state.asset) return selectAsset(null);
    // all floors shown: a room of another floor opens that floor, with it chosen
    const floor = id && id.split("-").slice(0, 4).join("-");
    if (id && !state.byId.has(id) && floor !== state.floor?.id && state.project?.floors.some((f) => f.id === floor)) {
      return openFloor(floor, id);
    }
    if (id !== state.selected && (id === null || state.byId.has(id))) select(id);
  });
  world.addEventListener("pick", (e) => {
    if (finish.painting()) { // painted (or taken up), not chosen
      e.preventDefault();
      if (editable()) finish.pick(e.detail);
      return;
    }
    if (state.tool !== "place" || !state.placeType) return;
    e.preventDefault(); // placed, not chosen
    const p = e.detail;
    if (!p.local || p.floor !== state.floor?.id) return toast("Click on this floor's floor, where it goes", true);
    placeAsset(state.placeType, p.local, p.altKey || state.alt, REACH_3D);
  });
  world.addEventListener("itemdragstart", ({ detail }) => carryStart(detail));
  world.addEventListener("itemdrag", ({ detail }) => carryMove(detail));
  world.addEventListener("itemdragend", ({ detail }) => carryEnd(detail));
  world.addEventListener("roomchange", ({ detail }) => showRoom(detail));
  world.addEventListener("walklock", () => showWalk());
  // a world that opens doors: E (or a click) at a door is its own; the door aimed at is said
  world.addEventListener("doorchange", () => showWalk());
  world.addEventListener("dooraim", ({ detail }) => {
    view3d.doorAim = detail?.id ? detail : null;
    showWalk();
  });
  world.addEventListener("floorchange", () => { // up or down the stairs: that floor, here too
    if (view3d.mode === "walk" && world.walkFloor && world.walkFloor !== state.floor?.id) openFloor(world.walkFloor);
  });
}

/** The 3D view's pointer: where the item being placed would go, follows it; a press,
 * walking with the mouse free, takes it to look; a right-click (not a drag) offers what
 * can be done there. */
function setup3dPointer() {
  const box = $("world3d");
  let right = null;
  box.addEventListener("pointermove", (e) => {
    view3d.pointer = [e.clientX, e.clientY];
    state.alt = e.altKey;
    if (state.tool === "place" && !e.buttons) aimSoon();
  });
  box.addEventListener("pointerleave", () => { if (!view3d.world?.walking) view3d.world?.ghost(null); });
  box.addEventListener("pointerdown", (e) => {
    if (e.button === 2) right = [e.clientX, e.clientY];
  });
  box.addEventListener("pointerup", (e) => {
    if (e.button !== 2 || !right) return;
    const moved = !view3d.world?.walking && Math.hypot(e.clientX - right[0], e.clientY - right[1]) > 4; // a drag moves the view
    right = null;
    if (!moved) menu3d(e.clientX, e.clientY);
  });
  box.addEventListener("contextmenu", (e) => e.preventDefault());
  $("walk-enter").addEventListener("click", startLooking);
}

/** Walking: the mouse taken to look (from a click). */
function startLooking() {
  document.activeElement?.blur?.(); // keys to the walker, not to a field
  view3d.world?.startWalking();
}

export function aimSoon() {
  if (view3d.aim) return;
  view3d.aim = requestAnimationFrame(() => {
    view3d.aim = 0;
    aim3d();
  });
}

/** Where the item being placed would go, shown in 3D: under the pointer, or walking at
 * the cross; settled as on the plan (fit.js), red where it does not fit. */
function aim3d() {
  const w = view3d.world;
  if (!w || !view3d.shown) return;
  const t = state.tool === "place" ? typeOf(state.placeType) : null;
  const walking = view3d.mode === "walk";
  const p = !t ? null : walking ? (w.walking ? w.pointAt() : null) : view3d.pointer && w.pointAt(...view3d.pointer);
  if (!p?.local || p.floor !== state.floor?.id) {
    w.ghost(null);
    return;
  }
  const got = settle({ want: { at: p.local, rot: 0 }, last: null, ...placing(p.local, t.code, null, 0, REACH_3D), free: state.alt });
  const at = got?.at ?? p.local;
  w.ghost({ ...given3d({ type: t.code, x: at[0], y: at[1], rotation: got?.rot ?? 0 }), ok: Boolean(got), guides: got?.guides || [] });
}

/** Walking with the mouse taken, while placing: the ghost at the cross, as the view moves. */
function followCross() {
  if (view3d.cross) return;
  const step = () => {
    const w = view3d.world;
    if (!w || view3d.mode !== "walk" || !w.walking || state.tool !== "place") {
      view3d.cross = 0;
      view3d.crossKey = "";
      return;
    }
    const key = [...w.camera.position.toArray(), ...w.camera.quaternion.toArray()].map((v) => v.toFixed(4)).join();
    if (key !== view3d.crossKey) {
      view3d.crossKey = key;
      aim3d();
    }
    view3d.cross = requestAnimationFrame(step);
  };
  view3d.cross = requestAnimationFrame(step);
}

/** Walking: the room you are in, the cross when the mouse is taken, how to start when not. */
export function showWalk() {
  const w = view3d.world, walking = view3d.mode === "walk";
  const locked = walking && Boolean(w?.walking);
  $("walk-hud").hidden = !walking;
  document.body.classList.toggle("walking", walking); // (messages above the room walked in)
  $("crosshair").hidden = !locked;
  $("walk-enter").hidden = !walking || locked || !w || w.mode !== "walk";
  $("map").classList.toggle("placing", state.tool === "place" && view3d.shown);
  $("walk-keys").textContent = !locked ? "" : state.tool === "place" ? "Click: place it at the cross · Alt: as it is · Esc: free the mouse"
    : finish.painting() ? "Click: paint the floor or wall at the cross · Alt-click: take up its finish · Esc: free the mouse"
    : view3d.doorAim ? `E or click: ${view3d.doorAim.open ? "close" : "open"} the door · Esc: free the mouse`
      : editable() ? `Click: choose · R , . turn · Del delete · 2: here in 2D${doors() ? " · E or click: a door" : ""} · Esc: free the mouse`
        : `Click: choose · 2: here in 2D${doors() ? " · E or click: a door" : ""} · Esc: free the mouse`;
  $("walk-doors").hidden = !doors();
  if (locked && state.tool === "place") followCross();
  else if (walking) w?.ghost(null);
  emit("walk");
}

/** Whether the world opens and closes doors (E is then its key, walking, at a door). */
export const doors = () => Boolean(view3d.world && ("doors" in view3d.world || typeof view3d.world.toggleDoor === "function"));

function showRoom(r) {
  $("walk-room").textContent = r.id ? r.name || typeLabel(r.type) : "Outside";
  $("walk-meta").textContent = [r.id && r.name ? typeLabel(r.type) : "", r.number, r.stairs ? "E or PgUp up · Q or PgDn down" : ""].filter(Boolean).join(" · ");
}

/** The place under the pointer in 3D (walking: at the cross), on this floor: plan metres, or null. */
export function pointerPlace() {
  const w = view3d.world;
  if (!w) return null;
  const p = w.walking ? w.pointAt() : view3d.pointer ? w.pointAt(...view3d.pointer) : null;
  return p?.floor === state.floor?.id ? p.local : null;
}

/** Walking: up (1) or down (-1) at stairs or a lift. */
export function walkFloor(step) {
  const w = view3d.world;
  if (!w || view3d.mode !== "walk") return;
  if (!w.atStairs) toast("Find stairs or a lift to go up or down");
  else if (!w.changeFloor(step)) toast(step > 0 ? "This is the top floor" : "This is the lowest floor");
}

/** A place on the plan: 2D, centred there (nearer, when it was far out), marked a moment;
 * with ``tool``, drawing it from there. */
export function drawHere(p, tool = null) {
  setView("2d");
  requestAnimationFrame(() => {
    if (p) {
      centerOn([p[0], p[1], p[0], p[1]], Math.max(state.view.k, scaleFor([p[0] - 8, p[1] - 8, p[0] + 8, p[1] + 8], 20)));
      const ping = svg("circle", { cx: p[0], cy: p[1], r: 0.6, class: "ping" });
      $("guides").append(ping);
      setTimeout(() => ping.remove(), 1700);
    }
    if (!tool) return;
    if (!readable()) toast("This floor has no drawing yet: add its drawing to change its walls and openings", true);
    else setTool(tool);
  });
}

/** The 3D view's part of the page: its pointer; a 2D tool asked for in 3D (its key shows
 * the place under the pointer on the plan with it; its button or the palette, the plan as
 * it was, with it); and it follows the tool (what may be dragged, the ghost of what is
 * placed, the walking keys) and who may change the floor. */
export function setupView3d() {
  setup3dPointer();
  on("draw-here", (what) => {
    if (typeof what === "string") return drawHere(pointerPlace(), what);
    drawHere(null, what.tool);
  });
  on("tool", () => {
    if (state.tool !== "place") view3d.world?.ghost(null);
    else aimSoon();
    draggable3d();
    showWalk();
  });
  on("tool-options", () => { if (state.tool === "place") aimSoon(); });
  on("access", draggable3d);
}

// An item carried across its floor in 3D: settled as on the plan (fit.js), shown as a
// ghost (red where it does not fit: it goes where it last fitted), saved when put down.
export const carry = { asset: null };

function carryStart({ id, local }) {
  const a = (state.floor?.items || []).find((x) => x.id === id);
  if (!a || !local || !editable()) return;
  const rot = a.rotation || 0;
  Object.assign(carry, { asset: a, from: [a.x, a.y], start: local, rot0: rot,
    fit: { ...placing([a.x, a.y], a.type, a.id, rot, REACH_3D), last: { at: [a.x, a.y], rot } } });
  view3d.world.setFloorItems(state.floor.id, state.floor.items.filter((x) => !x.retired && x !== a).map(given3d));
}

function carryMove({ local, altKey }) {
  const a = carry.asset;
  if (!a || !local) return;
  const f = carry.fit;
  const want = { at: [carry.from[0] + local[0] - carry.start[0], carry.from[1] + local[1] - carry.start[1]], rot: carry.rot0 };
  const got = settle({ want, last: f.last, ...f, free: altKey });
  if (got) f.last = { at: got.at, rot: got.rot };
  const at = got ? got.at : want.at;
  view3d.world.ghost({ ...given3d({ ...a, x: at[0], y: at[1], rotation: got ? got.rot : want.rot }), ok: Boolean(got), guides: got?.guides || [] });
}

function carryEnd({ cancelled }) {
  const a = carry.asset;
  carry.asset = null;
  view3d.world?.ghost(null);
  if (!a) return;
  const { at, rot } = carry.fit.last;
  if (cancelled || (Math.hypot(at[0] - a.x, at[1] - a.y) < 1e-4 && rot === carry.rot0)) return update3d();
  [a.x, a.y, a.rotation] = [at[0], at[1], rot]; // shown there now; saved (or put back as saved)
  update3d();
  renderAssets();
  changeAsset(a, { x: round4(at[0]), y: round4(at[1]), ...(rot !== carry.rot0 ? { rotation: round4(rot) } : {}) });
}
