// The project and the floor shown: loaded at the start (the floor in the address, else the
// first with something to review), another floor opened, the floor's drawing read again.

import { viewOnly } from "./access.js";
import { followJob, request } from "./api.js";
import { emit } from "./bus.js";
import { loadCatalogue, renderAssets } from "./items.js";
import { say, toast } from "./notify.js";
import { clearUnderlay, fit, renderPlan, showUnderlay } from "./plan.js";
import { select, selectAsset } from "./selection.js";
import { BASE, CODE, state, view3d } from "./state.js";
import { onFloor } from "../together.js";
import { refresh3d } from "./view3d.js";

/** The project's floors as Studio has them now (their counts to review). */
export async function refreshProject() {
  state.project = await request(`${BASE}/review`);
  emit("project");
}

/** The project loaded, then its first floor; ``ready(project)`` before the floor opens. */
export async function start(ready) {
  if (!CODE) {
    location.href = "/";
    return;
  }
  try {
    state.project = await request(`${BASE}/review`);
  } catch (e) {
    toast(`Cannot load the project: ${e.message}`, true);
    return;
  }
  await ready?.(state.project);
  emit("project");
  await loadCatalogue();
  const { floors } = state.project;
  const hash = new URLSearchParams(location.hash.slice(1));
  const first = floors.find((f) => f.id === hash.get("floor")) || floors.find((f) => f.review) || floors[0];
  if (!first) {
    emit("floor"); // (no floor: the panels say so)
    return;
  }
  await openFloor(first.id, hash.get("space"));
}

export async function openFloor(id, spaceId = null, { keepView = false } = {}) {
  let floor;
  try {
    floor = await request(`${BASE}/floors/${id}`);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  const changed = state.floor?.id !== id;
  state.floor = floor;
  state.byId = new Map(floor.spaces.map((s) => [s.id, s]));
  state.selected = null;
  state.item = null;
  state.segments = null;
  state.wallStart = null;
  if (changed) clearUnderlay();
  onFloor(id, floor.lock); // (together.js: this floor's stream, who is on it)
  renderPlan();
  if (!keepView || changed) fit();
  if (changed) selectAsset(null);
  renderAssets();
  emit("floor", { changed });
  select(spaceId && state.byId.has(spaceId) ? spaceId : null, { fly: Boolean(spaceId) });
  showUnderlay();
  if (view3d.shown) refresh3d();
}

/** The floor's drawing read again (a revised one too): IDs and corrections kept. */
export async function reconvert() {
  if (viewOnly()) return;
  const id = state.floor.id;
  state.converting = true;
  emit("converting");
  say("Re-reading drawing…");
  try {
    let job = await followJob(await request(`${BASE}/floors/${id}/convert`, {}), "Re-reading drawing…");
    // a reading that would retire most of the floor is held back: applied only when asked
    if ((job.result.held || []).includes(id) && confirm("This reading finds far fewer rooms than the floor has (or none), so nothing was changed: the floor keeps its rooms and their IDs. If the drawing really lost those rooms, read it anyway: the rooms it no longer has are retired. Read anyway?")) {
      job = await followJob(await request(`${BASE}/floors/${id}/convert`, { force: true }), "Re-reading drawing…");
    }
    toast(job.result.summaries.join("\n"));
    await refreshProject();
    view3d.dirty.add(id); // built again in 3D (that floor alone)
    state.converting = false;
    await openFloor(id, state.selected, { keepView: true });
  } catch (e) {
    toast(`Could not re-read the drawing: ${e.message}`, true);
    say("");
  } finally {
    state.converting = false;
    emit("converting");
  }
}
