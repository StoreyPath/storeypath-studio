// The tool in use: drawing a wall, a dividing line, a space, a door, a window or an
// opening, or placing an item; none: choosing what is clicked.

import { viewOnly } from "./access.js";
import { emit } from "./bus.js";
import { $ } from "./dom.js";
import { clearPreview } from "./drawing.js";
import * as finish from "./finish.js";
import { toast } from "./notify.js";
import * as sample from "./sample.js";
import { select, selectItem } from "./selection.js";
import { state, view3d } from "./state.js";
import { draggable3d, showWalk } from "./view3d.js";

const HINTS = {
  wall: "Wall: click where it starts and where it ends (it snaps to walls). Esc to stop.",
  divide: "Divide: click the line's two ends, right across the space (no wall: it becomes two zones). Esc to stop.",
  door: "Door: click on a wall where it goes. Esc to stop.",
  window: "Window: click on a wall where it goes. Esc to stop.",
  opening: "Opening (a way through, no door): click on a wall where it goes. Esc to stop.",
  place: "Place: click on the plan where it goes; again for another. Esc to stop.",
  place3d: "Place: click on the floor where it goes (it lines up as on the plan; Alt: as it is); again for another. Esc to stop.",
  placeWalk: "Place: aim the cross at the floor where it goes and click; again for another. Esc frees the mouse, then Esc stops.",
  space: "Space: click its corners (they snap to walls); click the first again, double-click or Enter to close it. Backspace takes the last corner back, Esc stops.",
};

/** ``tool`` in use (the same again, or null: none). */
export function setTool(tool) {
  if (tool) sample.stop(); // sample.js: a tool instead of the area to share
  if (tool && state.tool !== tool && viewOnly()) return;
  state.tool = tool && state.tool !== tool ? tool : null;
  if (state.tool && finish.painting()) finish.setPaint(false); // a tool instead of painting
  state.wallStart = null;
  state.corners = [];
  if (state.tool !== "place") state.placeType = null;
  $("map").classList.toggle("drawing-tool", Boolean(state.tool));
  clearPreview();
  view3d.world?.ghost(null);
  if (state.tool) {
    select(null);
    selectItem(null);
    toast(HINTS[state.tool === "place" && view3d.shown ? (view3d.mode === "walk" ? "placeWalk" : "place3d") : state.tool]);
  }
  draggable3d();
  showWalk();
  emit("tool");
}
