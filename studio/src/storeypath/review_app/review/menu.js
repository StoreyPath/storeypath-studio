// The right-click menu: what can be done where the pointer is, on the plan or in the 3D
// view. A shortcut to the same commands as the tools and the inspector.

import { editable } from "./access.js";
import { $, el } from "./dom.js";
import { addOpening, deleteItem, drawnNear, drawnSpaceAt, fillSizeForm, openingAt, openingMeta, openingName, openingNear,
  resize, sizesFrom, startLine, startSpace, takeAway } from "./drawing.js";
import { assetAt, changeAsset, placeAsset, turnAsset } from "./items.js";
import { spaceAt } from "./plan.js";
import { setFlag } from "./rooms.js";
import { select, selectAsset, selectItem } from "./selection.js";
import { readable, state, title, typeLabel, typeOf, view3d } from "./state.js";
import { setTool } from "./tools.js";
import { drawHere } from "./view3d.js";

export function closeMenu() {
  const m = $("menu");
  if (!m.hidden) {
    m.hidden = true;
    m.replaceChildren();
  }
}

export const menuOpen = () => !$("menu").hidden;

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
    items.push(el("div", { class: "heading" }, t ? t.name_en : asset.type), el("div", { class: "meta" }, asset.id));
    items.push(menuItem("Turn 90°", () => turnAsset(asset, (asset.rotation + 90) % 360), { hint: "R" }));
    items.push(menuItem(asset.retired ? "Restore" : "Delete", () => changeAsset(asset, { retired: !asset.retired }),
      { danger: !asset.retired, hint: asset.retired ? "" : "Del" }));
    items.push(el("hr"));
  } else if (d) {
    selectItem({ kind: "door", id: d.id });
    items.push(el("div", { class: "heading" }, openingName(d)), el("div", { class: "meta" }, openingMeta(d) || " "));
    if (!d.ignored) items.push(menuItem("Change size…", () => sizeMenu(cx, cy, d), { disabled: !readable(), hint: readable() ? "" : "needs its drawing" }));
    items.push(menuItem(d.drawn ? "Take it away" : d.ignored ? "Restore" : "Delete", () => deleteItem(), { danger: !d.ignored || d.drawn, hint: "Del" }));
    items.push(el("hr"));
  } else if (line) {
    selectItem({ kind: line.kind, at: line.at, line: line.line });
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
  // what is added is read with the floor's drawing: a floor from a package has none yet
  const onWall = Boolean(openingAt(p, 0.9));
  const off = !readable() ? "needs its drawing" : onWall ? "" : "on a wall";
  items.push(menuItem("Add a door here", () => addOpening("door", p), { disabled: !onWall || !readable(), hint: off }));
  items.push(menuItem("Add a window here", () => addOpening("window", p), { disabled: !onWall || !readable(), hint: off }));
  items.push(menuItem("Add an opening here", () => addOpening("opening", p), { disabled: !onWall || !readable(), hint: off }));
  items.push(menuItem("Draw a wall from here", () => startLine("wall", p), { disabled: !readable(), hint: readable() ? "W" : "needs its drawing" }));
  items.push(menuItem("Divide a space from here", () => startLine("divide", p), { disabled: !readable(), hint: readable() ? "V" : "needs its drawing" }));
  items.push(menuItem("Draw a space from here", () => startSpace(p), { disabled: !readable(), hint: readable() ? "S" : "needs its drawing" }));
  items.push(menuItem("Place an item here…", () => placeItemMenu(cx, cy, p)));
  const drawnSpace = drawnSpaceAt(p);
  if (drawnSpace) {
    items.push(menuItem("Take the drawn space away", () => takeAway("space", p, drawnSpace), { danger: true }));
  }
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

export function placeItemMenu(cx, cy, p) {
  const items = [el("div", { class: "heading" }, "Place an item here")];
  for (const t of state.catalogue) items.push(menuItem(t.name_en, () => placeAsset(t.code, p)));
  $("menu").replaceChildren(...items);
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
    items.push(el("div", { class: "heading" }, t ? t.name_en : asset.type), el("div", { class: "meta" }, asset.id));
    if (may) {
      items.push(menuItem("Turn 90°", () => turnAsset(asset, (asset.rotation + 90) % 360), { hint: "R" }));
      items.push(menuItem("Delete", () => changeAsset(asset, { retired: true }), { danger: true, hint: "Del" }));
    }
    items.push(el("hr"));
  } else if (s) {
    select(s.id);
    items.push(el("div", { class: "heading" }, title(s)), el("div", { class: "meta" }, typeLabel(s.type)), el("hr"));
  }
  items.push(menuItem(may ? "Draw here in 2D" : "Show here in 2D", () => drawHere(here), { disabled: !here, hint: "2" }));
  if (may && here) items.push(menuItem("Place an item here…", () => placeItemMenu(cx, cy, here)));
  $("menu").replaceChildren(...items);
  placeMenu(cx, cy);
  $("menu").querySelector("button:not(:disabled)")?.focus();
}

/** The menu's own keys: up and down through what it offers. */
export function setupMenu() {
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
}
