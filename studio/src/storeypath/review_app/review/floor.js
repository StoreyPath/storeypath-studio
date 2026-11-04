// The project and the floor shown: loaded at the start (the floor in the address, else the
// first with something to review), another floor opened, the floor's drawing read again.

import { viewOnly } from "./access.js";
import { followJob, request } from "./api.js";
import { emit } from "./bus.js";
import { loadFurnishings } from "./furnish.js";
import { loadCatalogue, renderAssets } from "./items.js";
import { say, toast } from "./notify.js";
import { clearUnderlay, fit, renderPlan, showUnderlay } from "./plan.js";
import { clearSelection, keepChosen, select } from "./selection.js";
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
  loadFurnishings(); // (the right-click menu offers placing one when there are)
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
  state.segments = null;
  state.wallStart = null;
  if (changed) clearUnderlay();
  onFloor(id, floor.editing); // (together.js: this floor's stream, who is editing which of its rooms)
  renderPlan();
  if (!keepView || changed) fit();
  if (changed) clearSelection();
  else keepChosen(); // the same floor read again: what is chosen stays chosen while it is there
  renderAssets();
  emit("floor", { changed });
  if (spaceId && state.byId.has(spaceId)) select(spaceId, { fly: true });
  showUnderlay();
  if (view3d.shown) refresh3d();
  if (changed) followReading(id);
}

/** A reading of the floor that was going on as it was opened (the page opened again while
 * it runs, or another person's): the floor shown as being read, as when it is read from
 * here, until it ends; then shown as read. */
async function followReading(id) {
  let jobs;
  try {
    ({ jobs } = await request(`${BASE}/jobs`));
  } catch {
    return;
  }
  const job = jobs.find((j) => (j.state === "waiting" || j.state === "running") && (j.floors || []).includes(id));
  if (!job || state.converting || state.floor?.id !== id) return;
  state.converting = true;
  state.reading = job.id;
  emit("converting");
  try {
    await followJob(job, "The floor's drawing is being read…");
    await refreshProject();
    view3d.dirty.add(id); // built again in 3D (that floor alone)
    state.converting = false;
    if (state.floor?.id === id) await openFloor(id, state.selected, { keepView: true });
  } catch (e) {
    toast(e.stopped ? `Reading stopped: ${e.message}` : `The reading did not finish: ${e.message}`, !e.stopped);
  } finally {
    state.converting = false;
    state.reading = null;
    emit("converting");
    say("");
  }
}

/** The read of the floor's drawing under way stopped: the floor stays as it was, and
 * what vision answered is kept (read again, it asks only about the rest). */
export async function stopReading() {
  if (!state.reading) return;
  try {
    await request(`jobs/${state.reading}/stop`, {});
    say("Stopping the read…");
  } catch (e) {
    toast(`Could not stop the read: ${e.message}`, true);
  }
}

/** The floor's drawing read again (a revised one too): IDs and corrections kept. */
export async function reconvert() {
  if (viewOnly()) return;
  const id = state.floor.id;
  state.converting = true;
  emit("converting");
  say("Re-reading drawing…");
  const reading = async (body) => {
    const started = await request(`${BASE}/floors/${id}/convert`, body);
    state.reading = started.id;
    emit("converting");
    return followJob(started, "Re-reading drawing…");
  };
  try {
    let job = await reading({});
    // a reading that would retire most of the floor, or rooms people corrected, is held back: applied only when asked
    const why = job.result.why?.[id] || "This reading finds far fewer rooms than the floor has (or none), so nothing was changed: the floor keeps its rooms and their IDs";
    if ((job.result.held || []).includes(id) && confirm(`${why.charAt(0).toUpperCase()}${why.slice(1)}.\n\nThe rooms it no longer has are retired if you read it anyway (their IDs are never used again). Read anyway?`)) {
      job = await reading({ force: true });
    }
    toast(job.result.summaries.join("\n"));
    await refreshProject();
    view3d.dirty.add(id); // built again in 3D (that floor alone)
    state.converting = false;
    await openFloor(id, state.selected, { keepView: true });
  } catch (e) {
    toast(e.stopped ? `Reading stopped: ${e.message}` : `Could not re-read the drawing: ${e.message}`, !e.stopped);
    say("");
  } finally {
    state.converting = false;
    state.reading = null;
    emit("converting");
  }
}
