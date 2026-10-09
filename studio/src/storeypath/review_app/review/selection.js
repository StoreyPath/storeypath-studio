// What is chosen on the floor: one model, the same in 2D, 3D and walking. One kind at a
// time:
//
//   space    one space or zone, or several (Shift- or ⌘-click, a band dragged with Shift)
//   asset    an item (furniture or equipment)
//   opening  a door, window or opening
//   drawn    a wall or dividing line drawn here ({kind: "wall" | "divider", at, line})
//
// Choosing lets what was chosen before go (but for spaces added to spaces). The plan, the
// 3D view and the address follow at once; the panels hear of it (bus: "selection").
// state.selected (the one space), state.asset and state.item read from it, for the code
// that asks of one thing.

import { emit } from "./bus.js";
import { $ } from "./dom.js";
import { renderAssets } from "./items.js";
import { flyToSpace, renderPlan, styleSpace } from "./plan.js";
import { pathData, state } from "./state.js";
import { pick3d } from "./view3d.js";

export const sel = { kind: null, ids: [], line: null };

Object.defineProperties(state, {
  selected: { get: () => (sel.kind === "space" && sel.ids.length === 1 ? sel.ids[0] : null), configurable: true },
  asset: { get: () => (sel.kind === "asset" ? sel.ids[0] : null), configurable: true },
  item: {
    get: () => (sel.kind === "opening" ? { kind: "door", id: sel.ids[0] } : sel.kind === "drawn" ? sel.line : null),
    configurable: true,
  },
});

/** The spaces chosen (one or more), as the floor has them now. */
export const chosenSpaces = () => (sel.kind === "space" ? sel.ids.map((id) => state.byId.get(id)).filter(Boolean) : []);
export const isChosen = (id) => sel.kind === "space" && sel.ids.includes(id);
export const count = () => (sel.kind === "drawn" ? 1 : sel.ids.length);

function set(kind, ids, line = null) {
  const before = new Set(sel.kind === "space" ? sel.ids : []);
  const wasItem = sel.kind === "opening" || sel.kind === "drawn";
  sel.kind = ids.length || line ? kind : null;
  sel.ids = ids;
  sel.line = line;
  const now = new Set(sel.kind === "space" ? sel.ids : []);
  for (const id of new Set([...before, ...now])) {
    const s = state.byId.get(id);
    if (s) styleSpace(s);
  }
  for (const id of sel.kind === "space" ? sel.ids : []) {
    const path = state.paths.get(id);
    if (path) $("spaces").append(path); // on top, so its outline shows
  }
  const chosen = chosenSpaces();
  $("print-selected").setAttribute("d", chosen.map((s) => pathData(s.geometry)).join("")); // and on the print beside it
  if (wasItem || sel.kind === "opening" || sel.kind === "drawn") renderPlan();
  renderAssets();
  if (state.floor) {
    const hash = new URLSearchParams({ floor: state.floor.id });
    if (state.selected) hash.set("space", state.selected);
    history.replaceState(null, "", `#${hash}`);
  }
  emit("selection");
}

/** A space or zone chosen alone (null: nothing); ``fly``: brought into view;
 * ``add``: added to the spaces chosen, or taken out of them when it is one. */
export function select(id, { fly = false, add = false } = {}) {
  if (!id || !state.byId.has(id)) {
    if (!add) set(null, []);
    return;
  }
  if (add && sel.kind === "space") {
    const ids = sel.ids.includes(id) ? sel.ids.filter((x) => x !== id) : [...sel.ids, id];
    set("space", ids);
    pick3d(ids.at(-1) || null, { go: false });
    return;
  }
  set("space", [id]);
  if (fly) flyToSpace(state.byId.get(id));
  pick3d(id, { go: fly });
}

/** Several spaces chosen (``add``: to those chosen). */
export function selectSpaces(ids, { add = false } = {}) {
  const now = add && sel.kind === "space" ? [...new Set([...sel.ids, ...ids])] : [...new Set(ids)];
  set("space", now.filter((id) => state.byId.has(id)));
  pick3d(now.at(-1) || null, { go: false });
}

/** A door, window, opening ({kind: "door", id}) or drawn line ({kind: "wall" | "divider",
 * at, line}) chosen; null: none. */
export function selectItem(item) {
  if (!item) {
    if (sel.kind === "opening" || sel.kind === "drawn") set(null, []);
    return;
  }
  if (item.kind === "door") set("opening", [item.id]);
  else set("drawn", [], item);
  pick3d(null);
}

/** An item (furniture or equipment) chosen, by its ID (null: none). */
export function selectAsset(id) {
  if (!id) {
    if (sel.kind === "asset") {
      set(null, []);
      pick3d(null);
    }
    return;
  }
  set("asset", [id]);
  pick3d(id);
}

export function clearSelection() {
  if (sel.kind) {
    set(null, []);
    pick3d(null);
  }
}

/** What is chosen, kept only while the floor still has it (after the floor is read again). */
export function keepChosen() {
  if (sel.kind === "space") {
    const ids = sel.ids.filter((id) => state.byId.has(id));
    if (ids.length !== sel.ids.length) return set(ids.length ? "space" : null, ids);
  } else if (sel.kind === "asset" && !(state.floor?.items || []).some((a) => a.id === sel.ids[0])) return set(null, []);
  else if (sel.kind === "opening" && !(state.floor?.doors || []).some((d) => d.id === sel.ids[0])) return set(null, []);
  else if (sel.kind === "drawn") {
    const list = sel.line.kind === "wall" ? state.floor?.edits?.walls : state.floor?.edits?.dividers;
    const at = sel.line.at.join(",");
    if (!(list || []).some(([a, b]) => `${(a[0] + b[0]) / 2},${(a[1] + b[1]) / 2}` === at)) return set(null, []);
  }
  // still there: drawn as chosen again (the plan was drawn anew)
  for (const s of chosenSpaces()) styleSpace(s);
  $("print-selected").setAttribute("d", chosenSpaces().map((s) => pathData(s.geometry)).join(""));
}
