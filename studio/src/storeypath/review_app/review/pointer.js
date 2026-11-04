// The pointer on the plan (and on the print beside it). With the Select tool: a click
// chooses what is there (Shift- or ⌘-click adds a room to those chosen), a drag moves the
// plan or carries an item (by who may change the floor), Shift-drag draws a band that
// chooses the rooms it covers. Another tool gets the pointer (tools.js: its plan
// handlers). Everywhere: the wheel zooms, the middle button, Space held or the Pan tool
// move the plan, a right-click opens the menu.

import { editable, viewOnly, whyNotRoom } from "./access.js";
import { emit } from "./bus.js";
import { chosenOpening, clearPreview, drawnNear, moveOpening, openingNear, slideOpening } from "./drawing.js";
import { $ } from "./dom.js";
import { assetAt, assetOf, changeAsset, chosenAll, drawGuides, itemRoom, placing, renderAssets } from "./items.js";
import { changeAssets, groupOf } from "./groups.js";
import { closeMenu, openMenu } from "./menu.js";
import { planPoint, showCursor, spaceAt, stopFlight, updateView, zoomAt } from "./plan.js";
import { settle } from "../fit.js";
import { select, selectAsset, selectItem, selectSpaces } from "./selection.js";
import { round4, state, units, view3d, visible } from "./state.js";
import { activeTool } from "./tools.js";

let spaceHeld = false;
const typing = (t) => t?.closest?.("input, select, textarea, [contenteditable]");


const DOUBLE_MS = 450; // two clicks this near in time are a double-click
export function setupPointer() {
  for (const pane of [$("svg"), $("svg-print")]) bindPane(pane);
  // Space held: the hand, for a moment (while drawing too)
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || e.repeat || typing(e.target) || view3d.shown || e.target.closest?.("button, a, [role=tab], [role=radio]")) return;
    spaceHeld = true;
    $("map").classList.add("space-held");
    e.preventDefault();
  });
  const release = () => {
    spaceHeld = false;
    $("map").classList.remove("space-held");
  };
  document.addEventListener("keyup", (e) => { if (e.code === "Space") release(); });
  window.addEventListener("blur", release);
}

/** Rooms whose label point is in a band of the screen (two corners, the pane's pixels). */
function roomsIn(pane, a, b) {
  const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  const { k, tx, ty } = state.view;
  return units().filter((s) => {
    if (!visible(s)) return false;
    const sx = s.label_point[0] * k + tx, sy = -s.label_point[1] * k + ty;
    return sx >= x0 && sx <= x1 && sy >= y0 && sy <= y1;
  }).map((s) => s.id);
}

function bindPane(pane) {
  const local = (e) => {
    const r = pane.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  const band = $("band");
  let drag = null;
  let lastPick = { id: null, at: 0 }; // the item clicked last, and when (a double-click: DOUBLE_MS)
  pane.addEventListener("pointerdown", (e) => {
    stopFlight();
    const [sx, sy] = local(e);
    const p = planPoint(sx, sy);
    const t = activeTool();
    const panning = e.button === 1 || (e.button === 0 && (spaceHeld || state.tool === "pan"));
    if (panning) {
      e.preventDefault();
      drag = { mode: "pan", x: e.clientX, y: e.clientY, tx: state.view.tx, ty: state.view.ty, moved: true };
      pane.classList.add("dragging");
      pane.setPointerCapture(e.pointerId);
      return;
    }
    if (e.button !== 0) return;
    if (t?.plan?.down?.(p, e)) { // the tool takes the drag (Share an area's rectangle)
      e.preventDefault(); // no text chosen on the way
      drag = { mode: "tool" };
      pane.setPointerCapture(e.pointerId);
      return;
    }
    if (!state.tool && e.shiftKey && pane === $("svg")) { // a band over the rooms to choose (a Shift-click: an item added)
      drag = { mode: "band", from: [sx, sy], moved: false, picked: assetOf(e.target) || assetAt(p) };
      pane.setPointerCapture(e.pointerId);
      return;
    }
    // the door, window or opening chosen, pressed: dragged, it slides along its wall
    const door = !state.tool && !e.shiftKey ? chosenOpening() : null;
    if (door?.span && pane === $("svg") && editable() && !state.floor?.layout && openingNear(p)?.id === door.id) {
      drag = { mode: "opening", d: door, from: p, x: e.clientX, y: e.clientY, moved: false };
      pane.setPointerCapture(e.pointerId);
      return;
    }
    // the item clicked, or one the click falls just beside (a small one: before the area under it)
    const picked = !state.tool && (assetOf(e.target) || assetAt(p));
    // dragged only by who may change the floor, and not in a room someone else is editing
    const asset = picked && editable() && !whyNotRoom(itemRoom(picked)) ? picked : null;
    drag = { mode: "press", x: e.clientX, y: e.clientY, tx: state.view.tx, ty: state.view.ty, moved: false, target: e.target,
      asset, picked, from: asset ? [asset.x, asset.y] : null };
    // one of items chosen together, or of a group: all of them carried as one (as they are: no
    // magnet); a piece of a group chosen alone (clicked in its group chosen), that piece alone
    const alone = asset && state.assets.length === 1 && state.assets[0] === asset.id;
    const together = asset && (alone ? [asset] : state.assets.length > 1 && state.assets.includes(asset.id) ? chosenAll() : groupOf(asset));
    if (asset && together.length > 1 && !(e.metaKey || e.ctrlKey)) {
      if (together.some((a) => whyNotRoom(itemRoom(a)))) drag.asset = null;
      else drag.many = together.map((a) => ({ a, from: [a.x, a.y] }));
    } else if (asset) { // its room holds it; walls and other items draw it (fit.js)
      const rot = asset.rotation || 0;
      drag.fit = { ...placing(drag.from, asset.type, asset.id, rot), rot0: rot, last: { at: drag.from, rot } };
    }
    pane.setPointerCapture(e.pointerId);
  });
  pane.addEventListener("pointermove", (e) => {
    const [sx, sy] = local(e);
    const p = planPoint(sx, sy);
    state.alt = e.altKey;
    showCursor(pane, sx, sy);
    emit("cursor", p);
    const t = activeTool();
    if (drag?.mode === "tool") return t?.plan?.move?.(p, e);
    if (drag?.mode === "band") {
      const [x0, y0] = drag.from;
      drag.moved = drag.moved || Math.hypot(sx - x0, sy - y0) > 3;
      Object.entries({ x: Math.min(x0, sx), y: Math.min(y0, sy), width: Math.abs(sx - x0), height: Math.abs(sy - y0) })
        .forEach(([k, v]) => band.setAttribute(k, v));
      band.hidden = !drag.moved;
      return;
    }
    if (!drag?.moved && state.tool && !spaceHeld) t?.plan?.hover?.(p, e);
    if (drag?.mode === "opening") {
      if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 4) return;
      drag.moved = true;
      pane.classList.add("dragging");
      drag.to = slideOpening(drag.d, drag.from, p);
      return;
    }
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    pane.classList.add("dragging");
    if (drag.many) { // items carried together: each as far as the pointer went
      for (const { a, from } of drag.many) [a.x, a.y] = [from[0] + dx / state.view.k, from[1] - dy / state.view.k];
      renderAssets();
      return;
    }
    if (drag.asset) { // an item carried across the plan: it follows the pointer, held in its room
      const f = drag.fit;
      const want = { at: [drag.from[0] + dx / state.view.k, drag.from[1] - dy / state.view.k], rot: f.rot0 };
      const got = settle({ want, last: f.last, ...f, free: e.altKey });
      if (got) {
        [drag.asset.x, drag.asset.y] = got.at;
        drag.asset.rotation = got.rot;
        f.last = { at: got.at, rot: got.rot };
        drawGuides(got.guides);
      }
      renderAssets();
      return;
    }
    state.view = { ...state.view, tx: drag.tx + dx, ty: drag.ty + dy };
    updateView();
  });
  pane.addEventListener("dblclick", (e) => {
    const t = activeTool();
    if (!t?.plan?.dblclick) return;
    e.preventDefault();
    t.plan.dblclick(planPoint(...local(e)), e);
  });
  const end = (e) => {
    const d = drag;
    drag = null;
    if (!d) return;
    pane.classList.remove("dragging");
    const p = planPoint(...local(e));
    const t = activeTool();
    if (d.mode === "tool") return t?.plan?.up?.(p, e);
    if (d.mode === "pan") return;
    if (d.mode === "opening") { // slid along its wall, or a click on it: chosen still
      if (d.moved && d.to) return moveOpening(d.d, d.to);
      clearPreview();
      return selectItem({ kind: "door", id: d.d.id });
    }
    if (d.mode === "band") {
      band.hidden = true;
      if (d.moved) return selectSpaces(roomsIn(pane, d.from, local(e)), { add: true });
      if (d.picked) return selectAsset(d.picked.id, { add: true }); // a Shift-click on an item: added to those chosen
      // a Shift-click: the room added to those chosen
      const s = spaceAt(...p);
      if (s) select(s.id, { add: true });
      return;
    }
    drawGuides();
    const { moved, target, asset, picked, fit, many } = d;
    if (many && moved) {
      return changeAssets(many.map(({ a }) => ({ id: a.id, x: round4(a.x), y: round4(a.y) })));
    }
    if (asset && moved) {
      const turned = (asset.rotation || 0) !== fit.rot0 ? { rotation: round4(asset.rotation) } : {};
      return changeAsset(asset, { x: round4(asset.x), y: round4(asset.y), ...turned });
    }
    if (moved) return;
    if (state.tool) return t?.plan?.click?.(p, e);
    if (picked) {
      // a second click on one of a group, at once, or a click on one of a group chosen whole:
      // that one alone, to drag or change by itself (counted here: a click redraws the items,
      // so the page's own dblclick never comes)
      const now = performance.now(), again = lastPick.id === picked.id && now - lastPick.at < DOUBLE_MS;
      lastPick = { id: picked.id, at: now }; // (a third at once: alone still)
      const inChosen = picked.group && state.assets.length > 1 && state.assets.includes(picked.id)
        && chosenAll().every((a) => a.group === picked.group);
      if ((again || inChosen) && picked.group && !(e.metaKey || e.ctrlKey)) return selectAsset(picked.id, { alone: true });
      return selectAsset(picked.id, { add: e.metaKey || e.ctrlKey });
    }
    const door = openingNear(p);
    if (door) return selectItem({ kind: "door", id: door.id });
    const line = drawnNear(p);
    if (line) return selectItem({ kind: line.kind, at: line.at, line: line.line });
    // on the plan, what was clicked; on the print, the space drawn there
    const id = pane === $("svg") ? target?.dataset?.id || null : spaceAt(...p)?.id || null;
    select(id, { add: Boolean(id) && (e.metaKey || e.ctrlKey) });
  };
  // A right-click: what can be done there
  pane.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (state.tool === "share" || !state.floor || state.busy || viewOnly()) return;
    openMenu(e.clientX, e.clientY, planPoint(...local(e)));
  });
  pane.addEventListener("pointerup", end);
  pane.addEventListener("pointercancel", () => {
    drag = null;
    band.hidden = true;
    drawGuides();
    pane.classList.remove("dragging");
  });
  pane.addEventListener("pointerleave", () => {
    showCursor(pane, null, null);
    emit("cursor", null);
  });
  pane.addEventListener("wheel", (e) => {
    e.preventDefault();
    closeMenu();
    stopFlight();
    const [sx, sy] = local(e);
    const speed = e.deltaMode === 1 ? 0.05 : e.ctrlKey ? 0.01 : 0.0015; // (a pinch comes as Ctrl and a wheel)
    zoomAt(sx, sy, Math.exp(-e.deltaY * speed));
  }, { passive: false });
}
