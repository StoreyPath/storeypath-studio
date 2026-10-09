// Many people at once: what others change, refreshed in place (together.js).
// A change someone else saved on the floor shown is read again and drawn where the person
// is: the same view, what they chose still chosen (if it is still there), an editor they
// have open left as they are typing in it, unless what it shows is what was changed.

import { editableByAccess } from "./access.js";
import { request } from "./api.js";
import { emit } from "./bus.js";
import { $ } from "./dom.js";
import { renderAssets } from "./items.js";
import { say, toast } from "./notify.js";
import { renderPlan, styleSpace } from "./plan.js";
import { select, selectAsset, selectItem } from "./selection.js";
import { BASE, pathData, reviewSpaces, state, view3d } from "./state.js";
import { carry, dirty3d, update3d } from "./view3d.js";

// what is to be read again: everything, or what these IDs are of; and whether a read is under way
const live = { busy: false, waiting: null, retry: 0 };

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
    const floor = await request(`${BASE}/floors/${encodeURIComponent(id)}`);
    if (state.floor?.id !== id) return;
    const changedHere = (thing) => want.all || want.targets.has(thing);
    state.floor = floor;
    state.byId = new Map(floor.spaces.map((s) => [s.id, s]));
    state.segments = null;
    const entry = state.project.floors.find((f) => f.id === id);
    if (entry) {
      entry.review = reviewSpaces().length;
      emit("project");
    }
    renderPlan(); // (the chosen door or drawn line stays chosen: state.item)
    // the space chosen: kept while it is there; its editor shown again only when it changed
    if (state.selected && !state.byId.has(state.selected)) select(null);
    else if (state.selected) {
      const s = state.byId.get(state.selected);
      styleSpace(s);
      $("spaces").append(state.paths.get(s.id));
      $("print-selected").setAttribute("d", pathData(s.geometry));
    }
    if (state.asset && !(floor.items || []).some((a) => a.id === state.asset)) selectAsset(null);
    else renderAssets();
    if (state.item?.kind === "door" && !floor.doors.some((d) => d.id === state.item.id)) selectItem(null);
    emit("floor", { refreshed: true, changedHere });
    // in 3D at once: the items (that floor's alone) and the rooms' labels; walls and doors
    // changed (the floor read again, a door deleted): that floor built again
    update3d();
    if (want.shape || floor.doors.some((d) => want.targets.has(d.id))) dirty3d([id]);
  } catch (e) {
    toast(`Could not show the change: ${e.message}`, true);
  } finally {
    live.busy = false;
  }
}
