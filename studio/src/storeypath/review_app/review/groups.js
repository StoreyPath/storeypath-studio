// Items together. A set (the catalogue's sets: a workstation, a meeting table and its chairs,
// a lounge) is placed in one go: each piece an item of its own, with its own ID, and the
// pieces one group (format 0.10: the items' `group`). A click on one of a group chooses the
// group; a double-click, or a piece named in the inspector, that piece alone; Shift- or
// ⌘-click adds an item to those chosen. Items chosen together are moved, turned and mirrored
// (about their middle), deleted, grouped (⌘G) and let go of their group (⌘⇧G) as one change,
// undone as one.

import { viewOnly } from "./access.js";
import { request } from "./api.js";
import { emit } from "./bus.js";
import { svg } from "./dom.js";
import { openFloor } from "./floor.js";
import { toast } from "./notify.js";
import { itemBox } from "../fit.js";
import { assetShape } from "../itemshape.js";
import { renderAssets } from "./items.js";
import { selectAssets } from "./selection.js";
import { BASE, round4, state, typeOf } from "./state.js";
import { update3d } from "./view3d.js";

/** A set of the catalogue by its code (null: none). */
export const setOf = (code) => (state.sets || []).find((s) => s.code === code) || null;

/** The items of an item's group as the floor has them now (shown ones), else the item alone. */
export function groupOf(a) {
  if (!a?.group) return a ? [a] : [];
  return (state.floor?.items || []).filter((x) => x.group === a.group && (!x.retired || state.showHidden));
}

/** Where a set's pieces stand, the set's middle at ``at`` turned ``rot`` degrees (counter-
 * clockwise, as an item's): [{type, x, y, rotation}], as Studio places them (review.py). */
export function setPieces(set, at, rot = 0) {
  const r = (rot * Math.PI) / 180, c = Math.cos(r), s = Math.sin(r);
  return set.pieces.map((p) => ({ type: p.type, x: at[0] + p.x * c - p.y * s, y: at[1] + p.x * s + p.y * c,
    rotation: (((p.rotation || 0) + rot) % 360 + 360) % 360 }));
}

/** The box a set takes in its own frame, [u0, v0, u1, v1]: round each of its pieces'
 * footprints, turned as it is in the set (for the magnet: fit.js, as an item's box). */
export function setBox(set) {
  let u0 = Infinity, v0 = Infinity, u1 = -Infinity, v1 = -Infinity;
  for (const p of set.pieces) {
    const [a0, b0, a1, b1] = itemBox(typeOf(p.type) || { code: p.type });
    const r = ((p.rotation || 0) * Math.PI) / 180, c = Math.cos(r), s = Math.sin(r);
    for (const [u, v] of [[a0, b0], [a1, b0], [a1, b1], [a0, b1]]) {
      const x = p.x + u * c - v * s, y = p.y + u * s + v * c;
      [u0, v0, u1, v1] = [Math.min(u0, x), Math.min(v0, y), Math.max(u1, x), Math.max(v1, y)];
    }
  }
  return [u0, v0, u1, v1];
}

/** A set's pieces drawn as they would stand (the ghost of a set being placed): one group. */
export function setGhost(set, at, rot, cls) {
  const g = svg("g", { class: "set-ghost" });
  for (const p of setPieces(set, at, rot)) g.append(assetShape(p, typeOf(p.type) || p, cls));
  return g;
}

/** The middle of items, as their group is turned about it: the middle of their box. */
export function middleOf(list) {
  const xs = list.map((a) => a.x), ys = list.map((a) => a.y);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

/** The floor's items as Studio answered (``got``: items, each as Studio gives one), drawn
 * again in 2D and 3D; ``select``, chosen. */
export function took(got, { select = null } = {}) {
  const list = state.floor.items;
  for (const a of got) {
    const i = list.findIndex((x) => x.id === a.id);
    const now = { ...a, floor_id: a.floor_id || state.floor.id };
    if (i >= 0) list[i] = now;
    else list.push(now);
  }
  renderAssets();
  update3d();
  emit("items", got.map((a) => a.id));
  if (select) selectAssets(select, { group: got.find((a) => select.includes(a.id))?.group ?? null });
}

/** A set placed: its middle at ``at``, turned ``rot``. Its items, chosen. */
export async function placeSet(code, at, rot = 0) {
  if (viewOnly()) return null;
  try {
    const got = await request(`${BASE}/floors/${state.floor.id}/sets`, { set: code, x: round4(at[0]), y: round4(at[1]), rotation: round4(rot) });
    took(got.items, { select: got.items.map((a) => a.id) });
    return got;
  } catch (e) {
    toast(`Not placed: ${e.message}`, true);
    return null;
  }
}

/** Items changed together, one change (``changes``: [{id, x?, y?, rotation?, retired?}];
 * ``more``: {group: "new" | null}); chosen still, as they are now. */
export async function changeAssets(changes, more = {}) {
  if (viewOnly() || !changes.length) return null;
  try {
    const got = await request(`${BASE}/items`, { items: changes, ...more });
    took(got.items, { select: got.items.map((a) => a.id) }); // (deleted too: chosen, to bring back)
    return got;
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
    await openFloor(state.floor.id, null, { keepView: true }); // as it is saved
    return null;
  }
}

/** Items moved together by ``dx``, ``dy`` metres. */
export const moveAssets = (list, dx, dy) =>
  changeAssets(list.map((a) => ({ id: a.id, x: round4(a.x + dx), y: round4(a.y + dy) })));

/** Items turned together by ``deg`` (counter-clockwise) about their middle: each where the
 * turn takes it, turned as much. */
export function turnAssets(list, deg) {
  const [cx, cy] = middleOf(list), r = (deg * Math.PI) / 180, c = Math.cos(r), s = Math.sin(r);
  return changeAssets(list.map((a) => ({ id: a.id, x: round4(cx + (a.x - cx) * c - (a.y - cy) * s),
    y: round4(cy + (a.x - cx) * s + (a.y - cy) * c), rotation: round4((((a.rotation || 0) + deg) % 360 + 360) % 360) })));
}

/** An item's turn mirrored (``axis`` "x": left to right, across the plan's up; "y": top to
 * bottom): the way its front faces, as the mirror shows it. Its shape is the same on both
 * sides (itemshape.js), so a mirrored item is the item turned so. */
export const mirrored = (rot, axis) => ((((axis === "x" ? -rot : 180 - rot) % 360) + 360) % 360);

/** Items mirrored together about their middle (``axis`` as mirrored's): each where the
 * mirror takes it, facing as mirrored; a workstation's pedestal on its other side. */
export function mirrorAssets(list, axis) {
  const [cx, cy] = middleOf(list);
  return changeAssets(list.map((a) => ({ id: a.id, ...(axis === "x" ? { x: round4(2 * cx - a.x) } : { y: round4(2 * cy - a.y) }),
    rotation: round4(mirrored(a.rotation || 0, axis)) })));
}

/** Items taken away together (``retired``: false, brought back). */
export const deleteAssets = (list, retired = true) => changeAssets(list.map((a) => ({ id: a.id, retired })));

/** Items made one group (two or more), or let go of theirs. */
export const groupAssets = (list) => (list.length < 2 ? toast("Choose two items or more to group (Shift-click each)", true)
  : changeAssets(list.map((a) => ({ id: a.id })), { group: "new" }));
export const ungroupAssets = (list) => changeAssets(list.map((a) => ({ id: a.id })), { group: null });
