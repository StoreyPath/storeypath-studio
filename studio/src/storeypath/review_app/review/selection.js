// What is chosen on the floor, the same in 2D, 3D and walking: a space or zone, an item
// (furniture or equipment), or a door, window, opening or line drawn here. One at a time;
// choosing one lets the others go. The plan, the 3D view and the address follow; the
// panels hear of it (bus: "selection").

import { emit } from "./bus.js";
import { $ } from "./dom.js";
import { renderAssets } from "./items.js";
import { focusSpace, renderPlan, styleSpace } from "./plan.js";
import { pathData, state } from "./state.js";
import { pick3d } from "./view3d.js";

/** A space or zone chosen (null: none); ``fly``: brought into view. */
export function select(id, { fly = false } = {}) {
  if (id && state.item) selectItem(null);
  if (id && state.asset) selectAsset(null);
  const previous = state.byId.get(state.selected);
  state.selected = id && state.byId.has(id) ? id : null;
  if (previous) styleSpace(previous);
  const s = state.byId.get(state.selected);
  $("print-selected").setAttribute("d", s ? pathData(s.geometry) : ""); // and on the print beside it
  if (s) {
    styleSpace(s);
    $("spaces").append(state.paths.get(s.id)); // on top, so its outline shows
    if (fly) focusSpace(s);
  }
  emit("selection");
  pick3d(state.selected, { go: fly });
  const hash = new URLSearchParams({ floor: state.floor.id });
  if (s) hash.set("space", s.id);
  history.replaceState(null, "", `#${hash}`);
}

/** A door, window, opening or drawn line chosen ({kind: "door", id} or {kind: "wall" |
 * "divider", at, line}; null: none). */
export function selectItem(item) {
  state.item = item;
  if (item) select(null);
  emit("selection");
  if (state.floor) renderPlan();
}

/** An item (furniture or equipment) chosen, by its ID (null: none). */
export function selectAsset(id) {
  state.asset = id;
  if (id) {
    select(null);
    if (state.item) selectItem(null);
  }
  renderAssets();
  emit("selection");
  if (id || !state.selected) pick3d(id);
}
