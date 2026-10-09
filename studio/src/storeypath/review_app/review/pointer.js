// The pointer on the plan (and on the print beside it): dragging pans, the wheel zooms, a
// click chooses what is there or is the tool's; an item is dragged by who may change the
// floor; a right-click opens the menu.

import { editable, viewOnly } from "./access.js";
import { closeSpace, drawnNear, openingNear, preview, toolClick } from "./drawing.js";
import { $ } from "./dom.js";
import { assetOf, changeAsset, drawGuides, placing, renderAssets } from "./items.js";
import { closeMenu, openMenu } from "./menu.js";
import { planPoint, showCursor, spaceAt, updateView, zoomAt } from "./plan.js";
import * as sample from "./sample.js";
import { settle } from "../fit.js";
import { select, selectAsset, selectItem } from "./selection.js";
import { round4, state } from "./state.js";

export function setupPointer() {
  for (const pane of [$("svg"), $("svg-print")]) bindPane(pane);
}

// Dragging pans, the wheel zooms, a click selects: the same on both sides.
function bindPane(pane) {
  const local = (e) => {
    const r = pane.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  let drag = null;
  pane.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (sample.sharing()) { // sample.js: the area to share, dragged out
      e.preventDefault(); // no text chosen on the way
      sample.down(planPoint(...local(e)));
      pane.setPointerCapture(e.pointerId);
      return;
    }
    const picked = !state.tool && assetOf(e.target);
    const asset = picked && editable() ? picked : null; // dragged only by who may change the floor
    drag = { x: e.clientX, y: e.clientY, tx: state.view.tx, ty: state.view.ty, moved: false, target: e.target,
      asset, picked, from: asset ? [asset.x, asset.y] : null };
    if (asset) { // its room holds it; walls and other items draw it (fit.js)
      const rot = asset.rotation || 0;
      drag.fit = { ...placing(drag.from, asset.type, asset.id, rot), rot0: rot, last: { at: drag.from, rot } };
    }
    pane.setPointerCapture(e.pointerId);
  });
  pane.addEventListener("pointermove", (e) => {
    const [sx, sy] = local(e);
    state.alt = e.altKey;
    showCursor(pane, sx, sy);
    if (sample.sharing()) return sample.move(planPoint(sx, sy));
    if (state.tool && !drag?.moved) preview(planPoint(sx, sy));
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    pane.classList.add("dragging");
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
    if (state.tool === "space" && state.corners.length >= 3) {
      e.preventDefault();
      closeSpace();
    }
  });
  const end = (e) => {
    if (sample.sharing()) return sample.up(planPoint(...local(e)));
    if (!drag) return;
    const { moved, target, asset, picked, fit } = drag;
    drag = null;
    pane.classList.remove("dragging");
    drawGuides();
    if (asset && moved) {
      const turned = (asset.rotation || 0) !== fit.rot0 ? { rotation: round4(asset.rotation) } : {};
      return changeAsset(asset, { x: round4(asset.x), y: round4(asset.y), ...turned });
    }
    if (moved) return;
    const p = planPoint(...local(e));
    if (state.tool) return toolClick(p);
    if (picked) return selectAsset(picked.id);
    const door = openingNear(p);
    if (door) return selectItem({ kind: "door", id: door.id });
    const line = drawnNear(p);
    if (line) return selectItem({ kind: line.kind, at: line.at, line: line.line });
    // on the plan, what was clicked; on the print, the space drawn there
    select(pane === $("svg") ? target?.dataset?.id || null : spaceAt(...p)?.id || null);
  };
  // A right-click: what can be done there
  pane.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (sample.sharing() || !state.floor || state.busy || viewOnly()) return;
    openMenu(e.clientX, e.clientY, planPoint(...local(e)));
  });
  pane.addEventListener("pointerup", end);
  pane.addEventListener("pointercancel", () => {
    drag = null;
    drawGuides();
    pane.classList.remove("dragging");
  });
  pane.addEventListener("pointerleave", () => showCursor(pane, null, null));
  pane.addEventListener("wheel", (e) => {
    e.preventDefault();
    closeMenu();
    const [sx, sy] = local(e);
    const speed = e.deltaMode === 1 ? 0.05 : 0.0015;
    zoomAt(sx, sy, Math.exp(-e.deltaY * speed));
  }, { passive: false });
}
