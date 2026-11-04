// Spaces and zones corrected: their type, name and number, accepted as they are, deleted
// and restored, their capacity, finishes and link to the floors above and below. Each is a
// change of its own, saved at once (undone and redone; others see it live). Also what
// their desks say of them (capacity and grade), and the next one to review.

import { roomTaken, viewOnly } from "./access.js";
import { request } from "./api.js";
import { emit } from "./bus.js";
import * as finish from "./finish.js";
import { say, toast } from "./notify.js";
import { placeLabels, renderOutline, styleSpace } from "./plan.js";
import { select } from "./selection.js";
import { BASE, code, reviewSpaces, state, title, typeOf, units, visible, within } from "./state.js";
import { dirty3d, sync3dSpace } from "./view3d.js";

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

/** What an area set aside is, as a person says (Studio's reading says it first): "sky" open
 * to the sky (a courtyard or light well: a hole in the floor, its doors leading outside),
 * "aside" set aside as part of the floor (a shaft, a gap), "room" a room after all. */
export function sayWhat(id, what) {
  const body = what === "room" ? { ignored: false } : { ignored: true, sky: what === "sky" };
  return saveSpace(body, id);
}

/** What an area is now, of those three: a room (not set aside), open to the sky, or set aside. */
export const whatItIs = (s) => (!s.ignored ? "room" : s.sky ? "sky" : "aside");

/** A space's change saved (``body`` as Studio takes it: objects/<id>); shown at once. */
export async function saveSpace(body, id = state.selected, { quiet = false } = {}) {
  const s = state.byId.get(id);
  if (!s || viewOnly() || roomTaken(s.kind === "zone" ? s.space_id || s.id : s.id)) return null;
  let updated;
  try {
    updated = await request(`${BASE}/objects/${s.id}`, body);
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
    return null;
  }
  shown([updated]);
  const flagged = "sky" in body ? (body.sky ? "open to the sky: a hole in the floor" : "set aside, part of the floor")
    : "hidden" in body ? (body.hidden ? "hidden" : "shown again")
    : "ignored" in body ? (body.ignored ? "deleted" : "restored")
    : "capacity" in body ? (body.capacity === null ? "capacity from its desks" : `seats ${body.capacity}`) : finish.said(body);
  if (!quiet) toast(flagged ? `${title(updated)}: ${flagged}` : body.reset ? "Corrections removed" : `Saved ${code(updated.id)}`);
  // (an area set aside that a person said what it is of stays chosen, to see it)
  if (!visible(updated) && state.selected === updated.id && !("sky" in body)) select(null);
  return updated;
}

/** Spaces as Studio now has them: kept, drawn, in 3D, and counted (the floor's to review). */
function shown(spaces) {
  let opened = false; // a courtyard or light well open to the sky now, or no longer: the floor's outline
  for (const s of spaces) {
    const i = state.floor.spaces.findIndex((x) => x.id === s.id);
    if (i < 0) continue;
    opened ||= Boolean(state.floor.spaces[i].sky) !== Boolean(s.sky);
    state.floor.spaces[i] = s;
    state.byId.set(s.id, s);
    styleSpace(s);
    sync3dSpace(s); // its label (and its floor's finish, by its type) in 3D at once
  }
  placeLabels();
  if (opened) {
    renderOutline();
    dirty3d([state.floor.id]); // (its slab open there, or whole: built again in 3D)
  }
  const floor = state.project.floors.find((f) => f.id === state.floor.id);
  if (floor) floor.review = reviewSpaces().length;
  emit("spaces", spaces.map((s) => s.id));
  emit("project");
}

/** Rooms changed at once (finish.js: every room of a type finished): shown as saved. */
export function updatedSpaces(spaces) {
  shown(spaces);
}

// ---- corrections, a field at a time ------------------------------------------------------
// A room's correction holds its type, name and number as a person set them (each left out:
// as detected). A field set back to what was detected leaves the correction; the last one
// gone, the room is as detected again (a reset). Accepting it as it is checks it: off the
// list to review (but for a missing type).

const FIELDS = ["type", "name", "number"];

/** The correction a room would have with ``changes`` ({type?, name?, number?}). */
export function correctionWith(s, changes) {
  const c = { ...(s.correction || {}) };
  for (const k of FIELDS) {
    if (!(k in changes)) continue;
    const v = typeof changes[k] === "string" ? changes[k].trim() : changes[k];
    const detected = s.detected[k] ?? (k === "type" ? null : "");
    if (v === null || v === undefined || v === detected) delete c[k];
    else c[k] = v;
  }
  for (const k of Object.keys(c)) if (!FIELDS.includes(k)) delete c[k];
  return c;
}

const sameCorrection = (a, b) => JSON.stringify(Object.entries(a || {}).sort()) === JSON.stringify(Object.entries(b || {}).sort());

/** A room's type, name or number corrected (``changes``), saved at once. */
export async function correct(s, changes, options = {}) {
  const c = correctionWith(s, changes);
  const had = s.correction || null;
  if (had && sameCorrection(c, had)) return s;
  if (!had && !Object.keys(c).length) return s; // as detected, and it was
  if (!Object.keys(c).length) return saveSpace({ reset: true }, s.id, options); // the last correction taken away
  return saveSpace({ correction: c }, s.id, options);
}

/** A room accepted as it is (its corrections kept): checked. */
export function accept(s, options = {}) {
  return saveSpace({ correction: s.correction ? correctionWith(s, {}) : {} }, s.id, options);
}

/** A room as detected again: its type, name and number corrections taken away. */
export function useDetected(s) {
  return saveSpace({ reset: true }, s.id);
}

/** Each of ``spaces`` changed by ``fn(space)`` (a promise each, one after another: a change
 * each, undone one at a time), saying how far it is; then said in a toast. */
export async function forEach(spaces, fn, saying) {
  let done = 0;
  for (const s of spaces) {
    say(`${saying}: ${done + 1} of ${spaces.length}…`);
    const got = await fn(s);
    if (got) done++;
  }
  say("");
  return done;
}

export function nextToReview(step = 1) {
  const review = reviewSpaces();
  if (!review.length) return;
  const i = review.findIndex((s) => s.id === state.selected);
  const at = i < 0 ? (step > 0 ? 0 : review.length - 1) : (i + step + review.length) % review.length;
  select(review[at].id, { fly: true });
}
