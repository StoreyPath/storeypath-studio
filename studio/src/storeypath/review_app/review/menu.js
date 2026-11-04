// The right-click menu: what can be done where the pointer is, on the plan or in the 3D
// view. A shortcut to the same commands as the tools, the inspector and the keys: each
// says its key as the keyboard map has it.

import { editable } from "./access.js";
import { $, el, icon } from "./dom.js";
import { addOpening, deleteItem, drawnNear, drawnSpaceAt, openingAt, openingMeta, openingName, openingNear,
  sizeForm, startLine, startSpace, takeAway } from "./drawing.js";
import * as finish from "./finish.js";
import { assetAt, changeAsset, mirrorAsset, placeAsset, turnAsset } from "./items.js";
import { deleteAssets, groupOf, mirrorAssets, turnAssets, ungroupAssets } from "./groups.js";
import { copyItems, openCreateLayout, openPlaceLayout, pasteAt, roomUnder } from "./furnish.js";
import { openInsert } from "./palette.js";
import { keyNow, keyText } from "./keys.js";
import { finishOf } from "/viewer/src/finishes.js";
import { spaceAt } from "./plan.js";
import { setFlag } from "./rooms.js";
import { select, selectAsset, selectItem } from "./selection.js";
import { readable, state, title, typeLabel, typeOf, view3d } from "./state.js";
import { setTool } from "./tools.js";
import { drawHere, viewHere } from "./view3d.js";

const WALL_REACH_M = 0.8; // a right-click this near a room's wall on the plan offers that wall

let opener = null; // what had the keys before the menu opened: given them back when it closes

export function closeMenu() {
  const m = $("menu");
  if (!m.hidden) {
    m.hidden = true;
    m.replaceChildren();
    if (opener?.isConnected && document.activeElement?.closest?.("#menu, body") ) opener.focus?.({ preventScroll: true });
    opener = null;
  }
}

export const menuOpen = () => !$("menu").hidden;

/** The menu shown at (cx, cy), kept in the window. */
export function placeMenu(cx, cy) {
  const m = $("menu");
  if (m.hidden) opener = document.activeElement;
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.max(4, Math.min(cx, window.innerWidth - r.width - 4))}px`;
  m.style.top = `${Math.max(4, Math.min(cy, window.innerHeight - r.height - 4))}px`;
}

/** The key of a command, as the map has it now ("" when none runs it here). */
const key = (id) => {
  const k = keyNow(id);
  return k ? keyText(k) : "";
};

export function menuItem(label, action, { danger = false, disabled = false, hint = "", ico = null } = {}) {
  const b = el("button", { type: "button", role: "menuitem", class: `menu-item${danger ? " danger" : ""}` },
    ico ? icon(ico, { size: 15 }) : el("span", { class: "icon-space", "aria-hidden": "true" }), el("span", { class: "label" }, label),
    hint ? el("span", { class: "key" }, hint) : null);
  b.disabled = disabled;
  b.addEventListener("click", () => {
    closeMenu();
    action();
  });
  return b;
}

const heading = (text, meta) => [el("div", { class: "menu-heading" }, text), meta ? el("div", { class: "menu-meta" }, meta) : null];

/** The menu on the plan, at ``p`` (plan metres) shown at (cx, cy) on the page. */
/** What can be done to an item's group: turned, let go of, deleted (one change each). */
function groupItems(asset, { heading: said = true } = {}) {
  const all = groupOf(asset), t = typeOf(asset.type), out = [];
  if (said) out.push(...heading(`A group of ${all.length} items`, `${t ? t.name_en : asset.type} and ${all.length - 1} more, together`));
  out.push(menuItem("Turn the group 90°", () => turnAssets(all, 90), { hint: said ? key("item.turn") : "", ico: "rotate-ccw" }));
  out.push(menuItem("Mirror the group left to right", () => mirrorAssets(all, "x"), { hint: said ? key("item.mirror-x") : "", ico: "flip-vertical-2" }));
  out.push(menuItem("Mirror the group top to bottom", () => mirrorAssets(all, "y"), { hint: said ? key("item.mirror-y") : "", ico: "flip-horizontal-2" }));
  out.push(menuItem("Ungroup", () => ungroupAssets(all), { hint: said ? key("item.ungroup") : "", ico: "square-split-horizontal" }));
  out.push(menuItem("Delete the group", () => deleteAssets(all), { danger: true, hint: said ? key("edit.delete") : "", ico: "trash-2" }));
  out.push(menuItem("Copy the group", () => copyItems(all), { hint: said ? key("item.copy") : "", ico: "copy" }));
  return out;
}

export function openMenu(cx, cy, p) {
  if (state.tool) setTool(null);
  const items = [];
  const asset = assetAt(p);
  const d = asset ? null : openingNear(p);
  const line = d || asset ? null : drawnNear(p);
  const s = d || line || asset ? null : spaceAt(...p);
  if (asset && groupOf(asset).length > 1 && !(state.asset === asset.id)) { // one of a group: the group (chosen whole)
    selectAsset(asset.id);
    items.push(...groupItems(asset));
    items.push(el("hr"));
  } else if (asset) {
    if (state.asset !== asset.id) selectAsset(asset.id);
    const t = typeOf(asset.type);
    items.push(...heading(t ? t.name_en : asset.type, asset.id));
    items.push(menuItem("Turn 90°", () => turnAsset(asset, asset.rotation + 90), { hint: key("item.turn"), ico: "rotate-ccw" }));
    items.push(menuItem("Mirror left to right", () => mirrorAsset(asset, "x"), { hint: key("item.mirror-x"), ico: "flip-vertical-2" }));
    items.push(menuItem("Mirror top to bottom", () => mirrorAsset(asset, "y"), { hint: key("item.mirror-y"), ico: "flip-horizontal-2" }));
    items.push(menuItem(asset.retired ? "Restore" : "Delete", () => changeAsset(asset, { retired: !asset.retired }),
      { danger: !asset.retired, hint: asset.retired ? "" : key("edit.delete"), ico: asset.retired ? "rotate-cw" : "trash-2" }));
    if (!asset.retired) items.push(menuItem("Copy", () => copyItems([asset]), { hint: key("item.copy"), ico: "copy" }));
    if (asset.group) items.push(menuItem("Choose its group", () => selectAsset(asset.id), { ico: "layers" }));
    items.push(el("hr"));
  } else if (d) {
    selectItem({ kind: "door", id: d.id });
    items.push(...heading(openingName(d), openingMeta(d) || " "));
    if (!d.ignored) items.push(menuItem("Change size…", () => sizeMenu(cx, cy, d), { disabled: !readable(), hint: readable() ? "" : "needs its drawing", ico: "move" }));
    items.push(menuItem(d.drawn ? "Take it away" : d.ignored ? "Restore" : "Delete", () => deleteItem(),
      { danger: !d.ignored || d.drawn, hint: key("edit.delete"), ico: d.ignored && !d.drawn ? "rotate-cw" : "trash-2" }));
    items.push(el("hr"));
  } else if (line) {
    selectItem({ kind: line.kind, at: line.at, line: line.line });
    items.push(...heading(line.kind === "wall" ? "Wall drawn here" : "Dividing line drawn here", `${line.length.toFixed(2)} m long`));
    items.push(menuItem("Take it away", () => deleteItem(), { danger: true, hint: key("edit.delete"), ico: "trash-2" }));
    items.push(el("hr"));
  } else if (s) {
    select(s.id);
    items.push(...heading(title(s), typeLabel(s.type)));
    if (editable()) {
      const land = Boolean(state.floor?.layout); // (an area of a layout: its ground, no walls of its own)
      const now = finish.shown(s), wall = land ? null : finish.wallAt(s, p, WALL_REACH_M);
      items.push(menuItem(`Paint the ${land ? "ground" : "floor"}: ${finishOf(now.floor)?.name ?? now.floor}…`, () => finish.chooseAt(cx, cy, s, "floor"), { ico: "paint-roller" }));
      if (wall) items.push(menuItem("Paint this wall…", () => finish.chooseAt(cx, cy, s, "wall", wall), { ico: "paint-roller" }));
      if (!land) items.push(menuItem(`Paint all its walls: ${finishOf(now.wall)?.name ?? now.wall}…`, () => finish.chooseAt(cx, cy, s, "wall"), { ico: "paint-roller" }));
    }
    items.push(menuItem(s.ignored ? "Restore this space" : "Delete this space", () => setFlag(s.id, "ignored", !s.ignored),
      { danger: !s.ignored, hint: s.ignored ? "" : key("edit.delete"), ico: s.ignored ? "rotate-cw" : "trash-2" }));
    items.push(el("hr"));
  }
  // what is added is read with the floor's drawing: a floor from a package has none yet
  const onWall = Boolean(openingAt(p, 0.9));
  const off = !readable() ? "needs its drawing" : onWall ? "" : "on a wall";
  if (state.floor?.layout) { // (a location's layout: what is drawn in its boundary walls is a gate)
    items.push(menuItem("Add a gate here", () => addOpening("gate", p), { disabled: !onWall || !readable(), hint: off, ico: "door-open" }));
  } else {
    items.push(menuItem("Add a door here", () => addOpening("door", p), { disabled: !onWall || !readable(), hint: off, ico: "door-open" }));
    items.push(menuItem("Add a window here", () => addOpening("window", p), { disabled: !onWall || !readable(), hint: off }));
    items.push(menuItem("Add an opening here", () => addOpening("opening", p), { disabled: !onWall || !readable(), hint: off }));
  }
  items.push(el("hr"));
  items.push(menuItem("Draw a wall from here", () => startLine("wall", p), { disabled: !readable(), hint: readable() ? key("tool.wall") : "needs its drawing", ico: "brick-wall" }));
  items.push(menuItem("Divide a space from here", () => startLine("divide", p), { disabled: !readable(), hint: readable() ? key("tool.divide") : "needs its drawing", ico: "square-split-horizontal" }));
  items.push(menuItem("Draw a space from here", () => startSpace(p), { disabled: !readable(), hint: readable() ? key("tool.space") : "needs its drawing", ico: "vector-square" }));
  items.push(menuItem("Place an item here…", () => openInsert((code) => placeAsset(code, p)), { hint: key("tool.place"), ico: "armchair" }));
  // furniture layouts (furnish.js): a room's items kept, and placed again in any room
  const room = roomUnder(p);
  if (room) {
    items.push(menuItem("Create a furniture layout from this room…", () => openCreateLayout(p), { ico: "sofa" }));
    // (offered with none known: another on the project may have saved one since; the dialog reads them again, or says none)
    const none = !(state.furnishings || []).length;
    items.push(menuItem("Place a furniture layout here…", () => openPlaceLayout(p), { hint: none ? "none saved yet" : "", ico: "layers" }));
  }
  if (state.clipboard) {
    items.push(menuItem(`Paste here: ${state.clipboard.label}`, () => pasteAt(p), { hint: key("edit.paste"), ico: "corner-down-left" }));
  }
  const shown = state.floor?.converted_at ? "" : "the floor is not read yet";
  items.push(el("hr"), menuItem("Walk here", () => viewHere(p, "walk"), { disabled: Boolean(shown), hint: shown, ico: "footprints" }));
  items.push(menuItem("See here in 3D", () => viewHere(p, "3d"), { disabled: Boolean(shown), hint: shown, ico: "box" }));
  const drawnSpace = drawnSpaceAt(p);
  if (drawnSpace) {
    items.push(el("hr"), menuItem("Take the drawn space away", () => takeAway("space", p, drawnSpace), { danger: true, ico: "trash-2" }));
  }
  $("menu").replaceChildren(...items.filter(Boolean));
  placeMenu(cx, cy);
  focusFirst();
}

/** The keys on the menu's first choice that deletes nothing. */
function focusFirst() {
  const m = $("menu");
  (m.querySelector("button:not(:disabled):not(.danger)") || m.querySelector("button:not(:disabled)"))?.focus();
}

function sizeMenu(cx, cy, d) {
  const form = sizeForm(d, { done: closeMenu, compact: true });
  $("menu").replaceChildren(el("div", { class: "menu-heading" }, `${openingName(d)}: size`), form);
  placeMenu(cx, cy);
  form.elements.width.focus();
  form.elements.width.select();
}

/** A right-click on the 3D view or walking (``p``: what the world says is there, with the
 * door within reach under the pointer, walking): a door opened or shut; the floor or the
 * walls there painted with the brush, or their finish taken up; an item placed there;
 * what is there chosen, an item deleted or turned; and that place shown on the plan. */
export function menu3d(cx, cy, p = null) {
  const w = view3d.world;
  if (!w || !state.floor) return;
  if (state.tool && state.tool !== "paint" && state.tool !== "place") setTool(null);
  const on = p?.floor === state.floor.id ? p : null;
  const here = on?.local ?? null;
  const asset = on?.item ? (state.floor.items || []).find((a) => a.id === on.item) : null;
  const unit = on?.space ? state.byId.get(on.space) : null;
  const room = on?.wall && on.room ? state.byId.get(on.room) : null; // the walls' room: the side under the pointer
  const may = editable(), walking = view3d.mode === "walk";
  const items = [];
  // what is there
  if (on?.door && walking) {
    const open = w.doorOpen?.(on.door), d = (state.floor.doors || []).find((x) => x.id === on.door);
    items.push(...heading(d ? openingName(d) : "Door", (d && openingMeta(d)) || on.door));
    items.push(menuItem(open ? "Close the door" : "Open the door", () => w.toggleDoor(on.door), { hint: "E", ico: "door-open" }));
  } else if (asset) {
    const t = typeOf(asset.type);
    items.push(...heading(t ? t.name_en : asset.type, asset.id));
  } else if (unit) items.push(...heading(title(unit), on.wall ? "Its walls" : typeLabel(unit.type)));
  if (asset && groupOf(asset).length > 1) { // one of a group: chosen whole, or alone
    const whole = state.assets.includes(asset.id) && state.assets.length > 1;
    items.push(menuItem("Choose the group", () => selectAsset(asset.id), { ico: "layers", disabled: whole }));
    items.push(menuItem("Choose this one alone", () => selectAsset(asset.id, { alone: true }), { ico: "mouse-pointer-2",
      disabled: state.asset === asset.id }));
    if (may) items.push(...groupItems(asset, { heading: false }));
  } else if (asset) {
    items.push(menuItem("Select", () => selectAsset(asset.id), { ico: "mouse-pointer-2", disabled: state.asset === asset.id }));
    if (may) { // (its keys said when it is the item chosen: they are its then)
      const chosen = state.asset === asset.id;
      items.push(menuItem("Turn 90°", () => turnAsset(asset, asset.rotation + 90), { hint: chosen ? key("item.turn") : "", ico: "rotate-ccw" }));
      items.push(menuItem("Mirror left to right", () => mirrorAsset(asset, "x"), { hint: chosen ? key("item.mirror-x") : "", ico: "flip-vertical-2" }));
      items.push(menuItem("Mirror top to bottom", () => mirrorAsset(asset, "y"), { hint: chosen ? key("item.mirror-y") : "", ico: "flip-horizontal-2" }));
      items.push(menuItem("Delete", () => changeAsset(asset, { retired: true }), { danger: true, hint: chosen ? key("edit.delete") : "", ico: "trash-2" }));
      items.push(menuItem("Copy", () => copyItems([asset]), { hint: chosen ? key("item.copy") : "", ico: "copy" }));
    }
  } else if (unit && !on?.door) {
    items.push(menuItem("Select", () => select(unit.id), { ico: "mouse-pointer-2", disabled: state.selected === unit.id }));
  }
  // painted with the brush (the Paint tool's, in use or not), or its finish taken up
  if (may && unit && !asset && !on?.door) {
    const walls = Boolean(room), now = finish.shown(walls ? room : unit);
    if (items.length) items.push(el("hr"));
    if (walls) {
      const wall = here ? finish.wallAt(room, here) : null;
      if (wall) items.push(menuItem("Paint this wall…", () => finish.chooseAt(cx, cy, room, "wall", wall), { ico: "paint-roller" }));
      items.push(menuItem(`Paint all the room's walls: ${finishOf(now.wall)?.name ?? now.wall}…`,
        () => finish.chooseAt(cx, cy, room, "wall"), { ico: "paint-roller" }));
    } else {
      items.push(menuItem(`Paint the floor: ${finishOf(now.floor)?.name ?? now.floor}…`, () => finish.chooseAt(cx, cy, unit, "floor"),
        { ico: "paint-roller" }));
    }
    items.push(menuItem("Pick up its finish", () => finish.paintAt(on, { takeUp: true }), { ico: "pipette", hint: "Alt-click" }));
  }
  if (may && here) {
    if (items.length) items.push(el("hr"));
    items.push(menuItem("Place an item here…", () => openInsert((code) => placeAsset(code, here)), { hint: key("tool.place"), ico: "armchair" }));
    if (state.clipboard) items.push(menuItem(`Paste here: ${state.clipboard.label}`, () => pasteAt(here), { hint: key("edit.paste"), ico: "corner-down-left" }));
  }
  if (items.length) items.push(el("hr"));
  items.push(menuItem(may ? "Draw here in 2D" : "Show here in 2D", () => drawHere(here), { disabled: !here, hint: key("view.here-2d"), ico: "map" }));
  $("menu").replaceChildren(...items.filter(Boolean));
  placeMenu(cx, cy);
  focusFirst();
}

/** The menu's own keys: up and down through what it offers; it closes on a click
 * elsewhere, or the window changing. */
export function setupMenu() {
  document.addEventListener("pointerdown", (e) => { if (!e.target.closest?.("#menu")) closeMenu(); }, true);
  window.addEventListener("resize", closeMenu);
  window.addEventListener("blur", closeMenu);
  $("menu").addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeMenu();
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    const items = [...$("menu").querySelectorAll("button:not(:disabled)")];
    const i = items.indexOf(document.activeElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
    items[next]?.focus();
    e.preventDefault();
  });
}
