// StoreyPath Review: check a converted project floor by floor and correct it.
// Part of StoreyPath Studio; the project is ?p=<code>. Every correction is saved at once.
// A floor the person may only view is shown with nothing to change it (the server refuses
// changes anyway). Others' changes appear as they are saved, one person edits a floor at
// a time, and each undoes their own (together.js; live.js).
//
// This file puts the page together: each part sets itself up, in order (the tools in the
// order the rail shows them), then the project is loaded. What each part is, and how to
// add a tool, a command or an inspector section: README.md beside this folder.

import { accountMenu, whoami } from "../account.js";
import { setupTogether } from "../together.js";
import { viewOnly } from "./access.js";
import { setupActions } from "./actions.js";
import { followJob, request } from "./api.js";
import { emit } from "./bus.js";
import { allCommands, run } from "./commands.js";
import { $, el, icon } from "./dom.js";
import { backCorner, clearPreview, preview, setupDrawing, snapWall, toolClick } from "./drawing.js";
import * as finish from "./finish.js";
import { openFloor, start } from "./floor.js";
import { setupFloorStack } from "./floorstack.js";
import { setupInspector } from "./inspector.js";
import { setupItems } from "./items.js";
import { allBindings, keyConflicts } from "./keys.js";
import { setupLayout } from "./layout.js";
import { togetherHooks } from "./live.js";
import { setupMeasure } from "./measure.js";
import { setupMenu } from "./menu.js";
import { setupNavigator } from "./navigator.js";
import { say, toast } from "./notify.js";
import { restyle, setupPlan, spaceAt } from "./plan.js";
import { setupPointer } from "./pointer.js";
import { saveSpace, updatedSpaces } from "./rooms.js";
import { setupReviewMode } from "./reviewmode.js";
import * as sample from "./sample.js";
import { setupFloorSections } from "./sections/floor.js";
import { setupHistorySection } from "./sections/history.js";
import { setupOtherSections } from "./sections/others.js";
import { setupRoomSections } from "./sections/room.js";
import { sel, select } from "./selection.js";
import { BASE, CODE, readable, state, title, units, view3d } from "./state.js";
import { setupStatusbar } from "./statusbar.js";
import { allTools, setTool, setupTools, tool } from "./tools.js";
import { setupTooltips } from "./tooltip.js";
import { setupTopbar } from "./topbar.js";
import * as vertical from "./vertical.js";
import { draggable3d, setupView3d, showWalk } from "./view3d.js";

setupTooltips();
setupLayout();
// who moves with Tab sees where the keys are on the canvas too (a click there does not ring it)
document.addEventListener("keydown", (e) => { if (e.key === "Tab") document.body.classList.add("tabbing"); }, true);
document.addEventListener("pointerdown", () => document.body.classList.remove("tabbing"), true);
setupPlan();
setupPointer();
setupMenu();

// the tools, in the rail's order within each group, and every command
setupActions();
setupDrawing();
vertical.setup({ state, BASE, request, followJob, openFloor, fillFloorSelect: () => emit("project"), saveSpace, toast, el, emit,
  tool, setTool, snapWall, preview, toolClick, backCorner, clearPreview, viewOnly, readable, select });
setupItems();
finish.setup({ state, BASE, request, toast, el, icon, emit, tool, setTool, saveSpace, viewOnly, units, title, spaceAt,
  updated: updatedSpaces,
  restyle: () => {
    restyle();
    emit("settings");
  },
  stopTools: () => draggable3d(), // painting: nothing dragged in 3D
  showWalk: () => showWalk() });
setupMeasure();
sample.setup({ state, BASE, toast, el, emit, tool, setTool });
setupReviewMode();
setupView3d();
view3d.look = { attach: (world) => world.addEventListener("lookchange", () => emit("settings")) };

// the panels and bars, which listen for what they show
setupTools();
setupTopbar({ openFloor, request, followJob, say });
setupNavigator();
setupHistorySection();
setupFloorSections();
setupRoomSections();
setupOtherSections();
setupInspector();
setupFloorStack();
setupStatusbar();

// for the console, and the browser tests
window.storeypathReview = { state, view3d, sel, run, commands: allCommands, tools: allTools, keys: allBindings, keyConflicts };

(async () => {
  if (!CODE) {
    location.href = "/";
    return;
  }
  const me = await whoami();
  $("account").replaceChildren(accountMenu(me));
  await start(() => {
    setupTogether({ code: CODE, base: BASE, me, hooks: togetherHooks() });
  });
  document.body.classList.add("ready");
})();
