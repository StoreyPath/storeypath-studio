// The right-click menu: what can be done where the pointer is, on the plan or in the 3D
// view. A shortcut to the same commands as the tools, the inspector and the keys: each
// says its key as the keyboard map has it.

import { editable } from "./access.js";
import { $, el, icon } from "./dom.js";
import { addOpening, deleteItem, drawnNear, drawnSpaceAt, openingAt, openingMeta, openingName, openingNear,
  sizeForm, startLine, startSpace, takeAway } from "./drawing.js";
import { assetAt, changeAsset, placeAsset, turnAsset } from "./items.js";
import { keysOf, keyText } from "./keys.js";
import { spaceAt } from "./plan.js";
import { setFlag } from "./rooms.js";
import { select, selectAsset, selectItem } from "./selection.js";
import { readable, state, title, typeLabel, typeOf, view3d } from "./state.js";
import { setTool } from "./tools.js";
import { drawHere } from "./view3d.js";

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

/** The key of a command, as the map has it ("" when it has none). */
const key = (id) => {
  const k = keysOf(id)[0];
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
export function openMenu(cx, cy, p) {
  if (state.tool) setTool(null);
  const items = [];
  const asset = assetAt(p);
  const d = asset ? null : openingNear(p);
  const line = d || asset ? null : drawnNear(p);
  const s = d || line || asset ? null : spaceAt(...p);
  if (asset) {
    selectAsset(asset.id);
    const t = typeOf(asset.type);
    items.push(...heading(t ? t.name_en : asset.type, asset.id));
    items.push(menuItem("Turn 90°", () => turnAsset(asset, asset.rotation + 90), { hint: key("item.turn"), ico: "rotate-ccw" }));
    items.push(menuItem(asset.retired ? "Restore" : "Delete", () => changeAsset(asset, { retired: !asset.retired }),
      { danger: !asset.retired, hint: asset.retired ? "" : key("edit.delete"), ico: asset.retired ? "rotate-cw" : "trash-2" }));
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
    items.push(menuItem(s.ignored ? "Restore this space" : "Delete this space", () => setFlag(s.id, "ignored", !s.ignored),
      { danger: !s.ignored, hint: s.ignored ? "" : key("edit.delete"), ico: s.ignored ? "rotate-cw" : "trash-2" }));
    items.push(el("hr"));
  }
  // what is added is read with the floor's drawing: a floor from a package has none yet
  const onWall = Boolean(openingAt(p, 0.9));
  const off = !readable() ? "needs its drawing" : onWall ? "" : "on a wall";
  items.push(menuItem("Add a door here", () => addOpening("door", p), { disabled: !onWall || !readable(), hint: off, ico: "door-open" }));
  items.push(menuItem("Add a window here", () => addOpening("window", p), { disabled: !onWall || !readable(), hint: off }));
  items.push(menuItem("Add an opening here", () => addOpening("opening", p), { disabled: !onWall || !readable(), hint: off }));
  items.push(el("hr"));
  items.push(menuItem("Draw a wall from here", () => startLine("wall", p), { disabled: !readable(), hint: readable() ? key("tool.wall") : "needs its drawing", ico: "brick-wall" }));
  items.push(menuItem("Divide a space from here", () => startLine("divide", p), { disabled: !readable(), hint: readable() ? key("tool.divide") : "needs its drawing", ico: "square-split-horizontal" }));
  items.push(menuItem("Draw a space from here", () => startSpace(p), { disabled: !readable(), hint: readable() ? key("tool.space") : "needs its drawing", ico: "vector-square" }));
  items.push(menuItem("Place an item here…", () => placeItemMenu(cx, cy, p), { hint: key("tool.place"), ico: "armchair" }));
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

export function placeItemMenu(cx, cy, p) {
  const items = [el("div", { class: "menu-heading" }, "Place an item here")];
  for (const t of state.catalogue) {
    const b = menuItem(t.name_en, () => placeAsset(t.code, p));
    b.firstChild.replaceWith(el("span", { class: "swatch", style: `background:${t.color || "#8a8a8a"}` }));
    items.push(b);
  }
  $("menu").replaceChildren(...items.filter(Boolean));
  placeMenu(cx, cy);
  $("menu").querySelector("button")?.focus();
}

/** A right-click on the 3D view (walking: at the cross, the mouse given back): what is
 * there, and showing that place on the plan, to draw there. */
export function menu3d(cx, cy) {
  const w = view3d.world;
  if (!w || !state.floor) return;
  const walking = w.walking;
  const p = walking ? w.pointAt() : w.pointAt(cx, cy);
  if (walking) {
    w.stopWalking();
    const r = $("world3d").getBoundingClientRect();
    [cx, cy] = [r.left + r.width / 2, r.top + r.height / 2];
  }
  if (state.tool) setTool(null);
  const here = p?.floor === state.floor.id ? p.local : null;
  const asset = p?.item ? (state.floor.items || []).find((a) => a.id === p.item) : null;
  const s = !asset && p?.space ? state.byId.get(p.space) : null;
  const items = [];
  const may = editable();
  if (asset) {
    selectAsset(asset.id);
    const t = typeOf(asset.type);
    items.push(...heading(t ? t.name_en : asset.type, asset.id));
    if (may) {
      items.push(menuItem("Turn 90°", () => turnAsset(asset, asset.rotation + 90), { hint: key("item.turn"), ico: "rotate-ccw" }));
      items.push(menuItem("Delete", () => changeAsset(asset, { retired: true }), { danger: true, hint: key("edit.delete"), ico: "trash-2" }));
    }
    items.push(el("hr"));
  } else if (s) {
    select(s.id);
    items.push(...heading(title(s), typeLabel(s.type)), el("hr"));
  }
  items.push(menuItem(may ? "Draw here in 2D" : "Show here in 2D", () => drawHere(here), { disabled: !here, hint: key("view.here-2d"), ico: "map" }));
  if (may && here) items.push(menuItem("Place an item here…", () => placeItemMenu(cx, cy, here), { ico: "armchair" }));
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
