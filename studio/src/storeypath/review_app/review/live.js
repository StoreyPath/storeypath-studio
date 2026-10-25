// Many people at once: what others change, refreshed in place (together.js).
// A change someone else saved on the floor shown is read again and drawn where the person
// is: the same view, what they chose still chosen (if it is still there), an editor they
// have open left as they are typing in it, unless what it shows is what was changed.

import { editableByAccess } from "./access.js";
import { request } from "./api.js";
import { emit } from "./bus.js";
import { renderAssets } from "./items.js";
import { say, toast } from "./notify.js";
import { patchPlan, renderPlan } from "./plan.js";
import { keepChosen } from "./selection.js";
import { BASE, CODE, reviewSpaces, state, view3d } from "./state.js";
import { carry, dirty3d, update3d } from "./view3d.js";

// what is to be read again: everything, or what these IDs are of; and whether a read is under way
const live = { busy: false, waiting: null, retry: 0 };

// a floor's own fields, sent with what changed when the floor itself changed (Review.floor); a
// layout's too (format 0.12: its walls' height, its north arrow, the footprints when one changed)
const FLOOR_FIELDS = ["name", "ordinal", "source", "converted_at", "method", "warnings", "outline", "walls",
  "wall_thickness", "edits", "layout", "height", "north", "footprints"];

/** The floor the page has, with what changed since (Review.floor ``since``): the spaces,
 * openings and items changed in it, those gone out, what the items say of each space, the
 * floor's own fields when it changed itself, and all its openings when it was read again
 * (``doors_all``; a change of openings at once sends those it changed alone). Spaces and
 * openings in the order of their IDs, as the whole floor has them. */
function patched(floor, d) {
  const gone = new Set(d.gone);
  const merge = (list, fresh) => {
    const byId = new Map(fresh.map((x) => [x.id, x]));
    const had = new Set(list.map((x) => x.id));
    return list.filter((x) => !gone.has(x.id)).map((x) => byId.get(x.id) ?? x).concat(fresh.filter((x) => !had.has(x.id)));
  };
  const order = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  let spaces = merge(floor.spaces, d.spaces).sort(order);
  if (d.seats) spaces = spaces.map((s) => (d.seats[s.id] ? { ...s, ...d.seats[s.id] } : s));
  const doors = d.doors_all ? d.doors : merge(floor.doors, d.doors).sort(order);
  const out = { ...floor, spaces, doors, items: merge(floor.items, d.items), version: d.version, editing: d.editing };
  for (const k of FLOOR_FIELDS) if (k in d) out[k] = d[k];
  return out;
}

/** What a change changed (null: anything; undefined: nothing more, a read put off), and
 * whether it may have changed the floor's shape (its walls, doors or spaces: built again
 * in 3D) rather than its items or rooms' details only. */
function whatChanged(change) {
  if (change === undefined) return { all: false, shape: false, targets: new Set() };
  const shape = !change || !["item", "object"].includes(change.part);
  return change && change.part !== "floor" ? { all: false, shape, targets: new Set(change.targets || []) }
    : { all: true, shape, targets: new Set() };
}

function together(a, b) {
  return a ? { all: a.all || b.all, shape: a.shape || b.shape, targets: new Set([...a.targets, ...b.targets]) } : b;
}

/** A change on another floor, or to the building or project: what of it the 3D world
 * shows, read again when that floor is shown (the building: built again whole). */
function heardElsewhere(change) {
  if (!view3d.building) return;
  if (change.floor) {
    if (change.floor.startsWith(`${view3d.building}-`) && change.floor !== state.floor?.id) view3d.dirty.add(change.floor);
  } else if (change.part === "building" || change.part === "project") {
    view3d.stale = true;
    if (view3d.shown) dirty3d([]);
  }
}

/** What together.js is given of Review. */
export function togetherHooks() {
  return {
    request,
    toast,
    editableByAccess: () => editableByAccess(),
    floorName: () => {
      const f = state.project?.floors.find((x) => x.id === state.floor?.id);
      return f ? `${f.name} · ${f.building}` : "";
    },
    status: (text) => say(text),
    changed: (change, said) => {
      if (said) toast(said);
      refreshFloor(change);
    },
    reload: () => refreshFloor(null),
    elsewhere: heardElsewhere,
    showLock: () => emit("access"),
    presence: () => emit("presence"),
    steps: () => emit("steps"),
  };
}


/** The floor shown read again after a change (null: whatever changed), drawn in place.
 * One at a time; one asked for while the person drags an item or a change of theirs is
 * being saved waits for it. */
export async function refreshFloor(change) {
  const want = together(live.waiting, whatChanged(change));
  if (live.busy || state.busy || document.querySelector(".pane.dragging") || carry.asset) {
    live.waiting = want;
    clearTimeout(live.retry);
    live.retry = setTimeout(() => refreshFloor(undefined), 400);
    return;
  }
  live.waiting = null;
  const id = state.floor?.id;
  if (!id) return;
  live.busy = true;
  try {
    // what changed since the floor the page has (the whole floor when Studio cannot say)
    const have = state.floor;
    const since = Number.isInteger(have?.version) ? `?since=${have.version}` : "";
    const got = await request(`${BASE}/floors/${encodeURIComponent(id)}${since}`);
    if (state.floor?.id !== id) return;
    if (got.since != null && state.floor !== have) { // the floor shown read meanwhile: its own changes asked for again
      live.waiting = together(live.waiting, want);
      clearTimeout(live.retry);
      live.retry = setTimeout(() => refreshFloor(undefined), 0);
      return;
    }
    const changedHere = (thing) => want.all || want.targets.has(thing);
    const before = state.byId;
    const floor = got.since != null ? patched(have, got) : got;
    state.floor = floor;
    state.byId = new Map(floor.spaces.map((s) => [s.id, s]));
    state.segments = null;
    const entry = state.project.floors.find((f) => f.id === id);
    if (entry) {
      entry.review = reviewSpaces().length;
      emit("project");
    }
    if (got.since != null) patchPlan(got, before);
    else renderPlan();
    // what is chosen: kept while it is there; the inspector shown again only when it changed
    keepChosen();
    renderAssets();
    emit("floor", { refreshed: true, changedHere });
    // in 3D at once: the items (that floor's alone) and the rooms' labels; walls and doors
    // changed (the floor read again, a door deleted): that floor built again
    update3d();
    // (a courtyard or light well open to the sky now, or no longer: its slab open there, or whole)
    const opened = (got.spaces || []).some((s) => Boolean(s.sky) !== Boolean(before.get(s.id)?.sky));
    if (want.shape || opened || floor.doors.some((d) => want.targets.has(d.id))) dirty3d([id]);
  } catch (e) {
    if (e.status === 404 && state.floor?.id === id) { // the floor deleted (by someone else): its project
      toast("This floor was deleted: back to the project", true);
      setTimeout(() => { location.href = `/#/p/${encodeURIComponent(CODE)}`; }, 2500);
      return;
    }
    toast(`Could not show the change: ${e.message}`, true);
  } finally {
    live.busy = false;
  }
}
