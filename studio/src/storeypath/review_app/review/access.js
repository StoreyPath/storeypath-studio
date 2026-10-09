// What the person may do on a floor: change it (their access lets them, and nobody else is
// editing it), or only look at it. The server refuses changes anyway; the page offers
// nothing that would be refused.

import { lockedByOther } from "../together.js";
import { toast } from "./notify.js";
import { state } from "./state.js";

/** Whether the person's access lets them change a floor (else they may only view it). */
export function editableByAccess(floorId = state.floor?.id) {
  const f = state.project?.floors.find((x) => x.id === floorId);
  return !f || f.can === undefined || f.can === "edit" || f.can === "share";
}

/** Whether the person may change the floor (the one shown: and nobody else is editing it). */
export function editable(floorId = state.floor?.id) {
  return editableByAccess(floorId) && !(floorId === state.floor?.id && lockedByOther());
}

/** Why the floor shown may not be changed now ("" when it may). */
export function whyNotEditable() {
  if (editable()) return "";
  return editableByAccess() ? "Someone else is editing this floor: you can edit when they are done"
    : "View only: you may look at this floor, not change it";
}

/** True (and says so) when the floor shown may only be looked at. */
export function viewOnly() {
  const why = whyNotEditable();
  if (!why) return false;
  toast(why, true);
  return true;
}
