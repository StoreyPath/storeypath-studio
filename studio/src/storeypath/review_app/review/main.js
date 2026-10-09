// StoreyPath Review: check a converted project floor by floor and correct its spaces.
// Part of StoreyPath Studio; the project is ?p=<code>. Every correction is saved at once.
// A floor the person may only view is shown with nothing to change it (the server refuses
// changes anyway). Others' changes appear as they are saved, one person edits a floor at
// a time, and each undoes their own (together.js; live.js).
//
// This file puts the page together: each part sets itself up, in order, then the project
// is loaded. What each part is: README.md beside this folder.

import { accountMenu, whoami } from "../account.js";
import { setupTogether } from "../together.js";
import { followJob, request } from "./api.js";
import { emit } from "./bus.js";
import { $, el } from "./dom.js";
import { snapWall } from "./drawing.js";
import * as finish from "./finish.js";
import { openFloor, start } from "./floor.js";
import { togetherHooks } from "./live.js";
import { setupMenu } from "./menu.js";
import { toast } from "./notify.js";
import { fillTypes, setupPanel } from "./panel.js";
import { planPoint, restyle, setupPlan } from "./plan.js";
import { setupPointer } from "./pointer.js";
import { saveSpace, updatedSpaces } from "./rooms.js";
import * as sample from "./sample.js";
import { select } from "./selection.js";
import { BASE, CODE, readable, state, title, units, view3d } from "./state.js";
import { setTool } from "./tools.js";
import { viewOnly } from "./access.js";
import { draggable3d, setup3dPointer, showWalk } from "./view3d.js";
import * as vertical from "./vertical.js";

setupPlan();
setupPointer();
setupMenu();
setup3dPointer();
setupPanel();
vertical.setup({ state, BASE, request, followJob, openFloor, fillFloorSelect: () => emit("project"), saveSpace, toast, el,
  setTool, snapWall, planPoint, viewOnly, readable, select });
finish.setup({ state, BASE, request, toast, el, saveSpace, viewOnly, units, title, updated: updatedSpaces,
  restyle: () => {
    restyle();
    emit("settings");
  },
  // painting: no drawing or placing tool at once, and nothing dragged
  stopTools: () => {
    if (finish.painting() && state.tool) setTool(null);
    draggable3d();
  },
  showWalk: () => showWalk() });
sample.setup({ state, BASE, toast, setTool });
window.storeypathReview = { state, view3d }; // for the console (and the browser checks)

(async () => {
  if (!CODE) {
    location.href = "/";
    return;
  }
  const me = await whoami();
  $("account").replaceChildren(accountMenu(me));
  await start((project) => {
    setupTogether({ code: CODE, base: BASE, me, hooks: togetherHooks() });
    fillTypes(project.types);
  });
})();
