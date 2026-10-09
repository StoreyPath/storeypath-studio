// Spaces and zones corrected: their type, name and number, accepted as they are, deleted
// and restored, their capacity, finishes and link to the floors above and below. Each is a
// change of its own, saved at once (undone and redone; others see it live). Also what
// their desks say of them (capacity and grade), and the next one to review.

import { viewOnly } from "./access.js";
import { request } from "./api.js";
import { emit } from "./bus.js";
import * as finish from "./finish.js";
import { toast } from "./notify.js";
import { placeLabels, styleSpace } from "./plan.js";
import { select } from "./selection.js";
import { BASE, code, reviewSpaces, state, title, typeOf, units, visible, within } from "./state.js";
import { sync3dSpace } from "./view3d.js";

export const GRADES = ["president", "c_level", "director", "manager", "section_head", "senior", "junior"];
export const GRADE_NAMES = { president: "the President", c_level: "C-level", director: "a Director", manager: "a Manager",
  section_head: "a Head of section", senior: "senior staff", junior: "junior staff" };

/** What the items standing in a space or zone say of it (as Studio exports it): their
 * workplaces and the highest grade; a divided space counts its zones' items too. */
export function seatsOf(s) {
  const out = { workplaces: 0, grade: null };
  for (const a of state.floor?.items || []) {
    if (a.retired) continue;
    const t = typeOf(a.type);
    if (!t || !(t.workplaces || t.grade)) continue;
    const at = [a.x, a.y];
    const zone = units().find((u) => u.kind === "zone" && !u.ignored && within(at, u.geometry));
    const space = zone ? zone.space_id : state.floor.spaces.find((u) => u.kind === "space" && !u.ignored && within(at, u.geometry))?.id;
    if (s.id !== space && s.id !== zone?.id) continue;
    out.workplaces += t.workplaces || 0;
    if (t.grade && (!out.grade || GRADES.indexOf(t.grade) < GRADES.indexOf(out.grade))) out.grade = t.grade;
  }
  return out;
}

/** The items standing in a space or zone (a divided space: in its zones too). */
export function itemsIn(s) {
  return (state.floor?.items || []).filter((a) => (!a.retired || state.showHidden) && within([a.x, a.y], s.geometry));
}

export function setFlag(id, flag, value) {
  return saveSpace({ [flag]: value }, id);
}

/** A space's change saved (``body`` as Studio takes it: objects/<id>); shown at once. */
export async function saveSpace(body, id = state.selected, { quiet = false } = {}) {
  const s = state.byId.get(id);
  if (!s || viewOnly()) return null;
  let updated;
  try {
    updated = await request(`${BASE}/objects/${s.id}`, body);
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
    return null;
  }
  shown([updated]);
  const flagged = "hidden" in body ? (body.hidden ? "hidden" : "shown again")
    : "ignored" in body ? (body.ignored ? "deleted" : "restored")
    : "capacity" in body ? (body.capacity === null ? "capacity from its desks" : `seats ${body.capacity}`) : finish.said(body);
  if (!quiet) toast(flagged ? `${title(updated)}: ${flagged}` : body.reset ? "Corrections removed" : `Saved ${code(updated.id)}`);
  if (!visible(updated) && state.selected === updated.id) select(null);
  return updated;
}

/** Spaces as Studio now has them: kept, drawn, in 3D, and counted (the floor's to review). */
function shown(spaces) {
  for (const s of spaces) {
    const i = state.floor.spaces.findIndex((x) => x.id === s.id);
    if (i < 0) continue;
    state.floor.spaces[i] = s;
    state.byId.set(s.id, s);
    styleSpace(s);
    sync3dSpace(s); // its label (and its floor's finish, by its type) in 3D at once
  }
  placeLabels();
  const floor = state.project.floors.find((f) => f.id === state.floor.id);
  if (floor) floor.review = reviewSpaces().length;
  emit("spaces", spaces.map((s) => s.id));
  emit("project");
}

/** Rooms changed at once (finish.js: every room of a type finished): shown as saved. */
export function updatedSpaces(spaces) {
  shown(spaces);
}

export function nextToReview(step = 1) {
  const review = reviewSpaces();
  if (!review.length) return;
  const i = review.findIndex((s) => s.id === state.selected);
  const at = i < 0 ? (step > 0 ? 0 : review.length - 1) : (i + step + review.length) % review.length;
  select(review[at].id, { fly: true });
}
