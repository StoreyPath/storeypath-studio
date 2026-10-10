// The floor in 3D, and walked through.
// One view, three ways to see the floor, in the same place: the plan (2D), the floor as
// built (3D: orbit it) and a walk through it. The floor, what is chosen and the panels stay
// as they are from one to another, and the plan keeps its view. The 3D world (Studio's
// package of the floor's building, not recorded as an export) is built once and kept:
// switching is at once. What changes shows in it at once: items (yours, or others' as
// they are saved) are drawn again on their floor alone, a room's name or type its label
// and floor; walls, doors, dividers and spaces (drawn on the plan) come with the floor
// read again, and that floor alone is built again when the reading is done.
//
// In 3D and walking a click chooses a room or an item where the pointer is; items are
// placed as on the plan (fit.js: held in their room, lined up by the magnet; Alt: as they
// are), their ghost following the pointer, and dragged (walking: the item chosen); Paint
// marks the floor or the walls under the pointer and paints them with a click; a
// right-click offers what can be done there (menu.js: menu3d). Walking never takes the
// mouse: a drag looks round, W A S D move, a double-click goes there, and the panels stay
// usable; E is a door's (the world's: under the pointer, else ahead), else up the stairs;
// PgUp, PgDn and Q go up and down at stairs and lifts. In 3D the building's floors can all
// be shown (the floor stack's All).

import { editable } from "./access.js";
import { emit, on } from "./bus.js";
import { $, save, saved, svg } from "./dom.js";
import { clearPreview } from "./drawing.js";
import * as finish from "./finish.js";
import { changeAsset, drawGuides, placeAsset, placing, renderAssets } from "./items.js";
import { lookOptions } from "../look.js";
import { closeMenu, menu3d, menuOpen } from "./menu.js";
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

/** Walking, how fast a drag turns the view: "slow", "normal" (what was pressed stays under
 * the pointer) or "fast", remembered in this browser (the 3D page follows it too). */
export const LOOK_SPEEDS = { slow: 0.6, normal: 1, fast: 1.6 };
export function lookSpeed() {
  const v = saved("storeypath.world.look-speed");
  return v in LOOK_SPEEDS ? v : "normal";
}

export function setLookSpeed(speed) {
  if (!(speed in LOOK_SPEEDS)) return;
  save("storeypath.world.look-speed", speed);
  if (view3d.world && "lookSensitivity" in view3d.world) view3d.world.lookSensitivity = LOOK_SPEEDS[speed];
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
      if ("lookSensitivity" in view3d.world) view3d.world.lookSensitivity = LOOK_SPEEDS[lookSpeed()];
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

/** Items carried in 3D (walking, the item chosen) by a drag: only by who may change the
 * floor, and not while placing or painting. */
export function draggable3d() {
  view3d.world?.setDraggable(editable() && state.tool !== "place" && !finish.painting());
}

/** An item as the 3D world takes it: where it stands (the plan's metres) and its type's size. */
function given3d(a) {
  const t = typeOf(a.type);
  return { id: a.id, type: a.type, x: a.x, y: a.y, rotation: a.rotation || 0, ...(t ? { width: t.width, depth: t.depth,
    height: t.height, mount: t.mount || "floor", elevation: t.elevation ?? null, color: t.color, grade: t.grade ?? null,
    shape: t.shape ?? null } : {}) };
}

/** The floor shown, in 3D as the page has it now: its items drawn again (that floor's
 * alone: milliseconds), and its rooms' names and types. */
export function update3d() {
  const w = view3d.world, f = state.floor;
  if (!w || !f || !view3d.building || !f.id.startsWith(`${view3d.building}-`)) return;
  w.setFloorItems(f.id, (f.items || []).filter((a) => !a.retired).map(given3d));
  for (const s of f.spaces) sync3dSpace(s);
  view3d.crossKey = ""; // the ghost under the pointer seen again (the magnet sees the new items)
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
    if (finish.painting()) { // painted (or taken up), not chosen: seen as it is now, unmarked, until the pointer leaves it
      e.preventDefault();
      if (editable()) finish.pick(e.detail);
      view3d.painted = JSON.stringify(paintTarget(e.detail));
      world.mark(null);
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
  // what is under the pointer, walking: marked as a click would act on it, said in the status bar
  world.addEventListener("hover", ({ detail }) => {
    view3d.hovered = detail;
    markUnder(detail);
    emit("walk");
  });
  // a right-click (walking, a long press too): what can be done there
  world.addEventListener("menu", ({ detail }) => menu3d(detail.clientX, detail.clientY, detail));
  // a world that opens doors: a click on one, or E (the door under the pointer, or ahead); said
  world.addEventListener("doorchange", () => showWalk());
  world.addEventListener("dooraim", ({ detail }) => {
    view3d.doorAim = detail?.id ? detail : null;
    showWalk();
  });
  world.addEventListener("floorchange", () => { // up or down the stairs: that floor, here too
    if (view3d.mode === "walk" && world.walkFloor && world.walkFloor !== state.floor?.id) openFloor(world.walkFloor);
  });
}

/** The 3D view's pointer: where it is (the item being placed follows it; painting, what a
 * click would paint is marked under it, in 3D as the world does walking: hover). */
function setup3dPointer() {
  const box = $("world3d");
  box.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch") return;
    view3d.pointer = [e.clientX, e.clientY];
    state.alt = e.altKey;
    if (state.tool === "place" && !e.buttons) aimSoon();
    if (view3d.mode === "3d" && !e.buttons) hoverSoon();
  });
  box.addEventListener("pointerleave", () => {
    view3d.pointer = null;
    if (!carry.asset) view3d.world?.ghost(null);
    view3d.world?.mark(null);
  });
  box.addEventListener("contextmenu", (e) => e.preventDefault()); // (the world's menu event says a right-click)
}

/** In 3D (orbiting), what is under the pointer, once a frame: marked as a click would act on it. */
function hoverSoon() {
  if (view3d.hover) return;
  view3d.hover = requestAnimationFrame(() => {
    view3d.hover = 0;
    const w = view3d.world;
    if (!w || view3d.mode !== "3d" || !view3d.pointer) return;
    markUnder(w.pointAt(...view3d.pointer));
  });
}

/** What a click would act on now, under the pointer where it is, marked again (the tool or
 * what is chosen changed). */
function remarkUnder() {
  const w = view3d.world;
  if (w && view3d.shown) markUnder(view3d.mode === "walk" ? view3d.hovered : view3d.pointer ? w.pointAt(...view3d.pointer) : null);
}

/** What a click there would act on, marked lightly in the world: painting, the floor (or a
 * wall's side, the walls of the room it faces) under the pointer; choosing, an item there.
 * Placing, nothing (its ghost shows it). */
function markUnder(p) {
  const w = view3d.world;
  if (!w?.mark) return;
  if (!p || p.floor !== state.floor?.id || state.tool === "place" || menuOpen() || !editable() && finish.painting()) return w.mark(null);
  if (finish.painting()) {
    const target = paintTarget(p), key = JSON.stringify(target);
    if (key === view3d.painted) return w.mark(null); // (just painted: seen as it is)
    view3d.painted = null;
    return w.mark(target);
  }
  w.mark(p.item && !p.door && p.item !== state.asset ? { item: p.item } : null); // (the item chosen: lit already)
}

/** What a click paints there: the walls of the room on a wall's side, else the floor's room. */
const paintTarget = (p) => (p?.wall && p.room ? { walls: p.room } : p?.space ? { floor: p.space } : null);

/** Walking: E. The door under the pointer, else the nearest ahead (the world takes E first
 * when there is one: here when it did not), else up at stairs or a lift. */
export function walkDoor() {
  const w = view3d.world;
  if (!w || view3d.mode !== "walk") return;
  if (w.useDoor?.()) return;
  if (w.atStairs) return walkFloor(1);
  toast("No door within reach: E opens or closes the door under the pointer, or the one ahead (at stairs or a lift, E goes up)");
}

/** The right-click menu from the keys (Shift+F10, the menu key), in 3D or walking: at the
 * pointer, else the middle of the view. */
export function menuHere() {
  const r = $("world3d").getBoundingClientRect();
  const [x, y] = view3d.pointer ?? [r.left + r.width / 2, r.top + r.height / 2];
  const w = view3d.world, p = w?.pointAt(x, y) ?? null;
  menu3d(x, y, p ? { ...p, door: view3d.doorAim?.under ? view3d.doorAim.id : null } : null);
}

export function aimSoon() {
  if (view3d.aim) return;
  view3d.aim = requestAnimationFrame(() => {
    view3d.aim = 0;
    aim3d();
  });
}

/** Where the item being placed would go, shown in 3D: under the pointer, settled as on the
 * plan (fit.js), red where it does not fit. */
function aim3d() {
  const w = view3d.world;
  if (!w || !view3d.shown) return;
  const t = state.tool === "place" ? typeOf(state.placeType) : null;
  const p = t && view3d.pointer ? w.pointAt(...view3d.pointer) : null;
  if (!p?.local || p.floor !== state.floor?.id) {
    w.ghost(null);
    return;
  }
  const got = settle({ want: { at: p.local, rot: 0 }, last: null, ...placing(p.local, t.code, null, 0, REACH_3D), free: state.alt });
  const at = got?.at ?? p.local;
  w.ghost({ ...given3d({ type: t.code, x: at[0], y: at[1], rotation: got?.rot ?? 0 }), ok: Boolean(got), guides: got?.guides || [] });
}

/** Walking while placing: the ghost under the pointer, again as the view moves (the
 * walker walking or looking round, the pointer still). */
function followPointer() {
  if (view3d.cross) return;
  const step = () => {
    const w = view3d.world;
    if (!w || view3d.mode !== "walk" || state.tool !== "place") {
      view3d.cross = 0;
      view3d.crossKey = "";
      return;
    }
    const key = [...w.camera.position.toArray(), ...w.camera.quaternion.toArray(), ...(view3d.pointer ?? [])].map((v) => v.toFixed(4)).join();
    if (key !== view3d.crossKey) {
      view3d.crossKey = key;
      aim3d();
    }
    view3d.cross = requestAnimationFrame(step);
  };
  view3d.cross = requestAnimationFrame(step);
}

/** Walking: the room you are in, and what can be done now (the tool's, the item chosen's,
 * the door's), the ghost following the pointer while placing. */
export function showWalk() {
  const w = view3d.world, walking = view3d.mode === "walk";
  $("walk-hud").hidden = !walking;
  document.body.classList.toggle("walking", walking); // (messages above the room walked in)
  $("map").classList.toggle("placing", state.tool === "place" && view3d.shown);
  $("walk-keys").textContent = !walking ? "" : walkKeys();
  if (walking && state.tool === "place") followPointer();
  else if (walking && !carry.asset) w?.ghost(null);
  emit("walk");
}

/** Walking: what the keys and the mouse do now, in a line. */
function walkKeys() {
  const may = editable();
  if (state.tool === "place") return "Click the floor: place it · Alt-click: as it is · Esc: stop placing";
  if (finish.painting()) return "Click a floor or a wall: paint it · Alt-click: take up its finish · Esc: stop painting";
  if (state.asset && may) return "R , . turn it · Del delete · drag it to move it · Esc: let it go";
  return "Drag to look · W A S D to move · double-click to go there";
}

function showRoom(r) {
  $("walk-room").textContent = r.id ? r.name || typeLabel(r.type) : "Outside";
  $("walk-meta").textContent = [r.id && r.name ? typeLabel(r.type) : "", r.number, r.stairs ? "E or PgUp up · Q or PgDn down" : ""].filter(Boolean).join(" · ");
}

/** The place under the pointer in 3D and walking, on this floor (plan metres); walking with
 * the pointer off the view, where the walker stands; else null. */
export function pointerPlace() {
  const w = view3d.world;
  if (!w) return null;
  const p = view3d.pointer ? w.pointAt(...view3d.pointer) : null;
  if (p?.floor === state.floor?.id && p.local) return p.local;
  if (view3d.mode === "walk" && w.walkFloor === state.floor?.id) return w.buildingPoint({ x: w.player.x, z: w.player.z });
  return null;
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
  on("selection", () => {
    showWalk();
    remarkUnder();
  });
  on("draw-here", (what) => {
    if (typeof what === "string") return drawHere(pointerPlace(), what);
    drawHere(null, what.tool);
  });
  on("tool", () => {
    if (state.tool !== "place") view3d.world?.ghost(null);
    else aimSoon();
    draggable3d();
    showWalk();
    remarkUnder();
  });
  on("view", () => view3d.world?.mark?.(null));
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
