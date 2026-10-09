// Review mode: the rooms to review, one after another, for a floor with many. Each is
// brought into the middle of the view (the others step back), and the inspector says why
// it is flagged, with its likely types: 1–9 sets one, Enter accepts it as it is (as
// Review's Accept does: checked, off the list), and either goes on to the next; N and P
// (or Shift-N) go forward and back without changing it; Esc stops. The count is the
// floor's, live (others' changes too).

import { editable } from "./access.js";
import { emit, on } from "./bus.js";
import { command } from "./commands.js";
import { $, el, icon } from "./dom.js";
import { keysOf, keyText } from "./keys.js";
import { likelyTypes as likely } from "./likely.js";
import { toast } from "./notify.js";
import { flyToSpace } from "./plan.js";
import { accept, correct } from "./rooms.js";
import { clearSelection, select } from "./selection.js";
import { reviewSpaces, state, title, tucked, units, view3d } from "./state.js";
import { setTool } from "./tools.js";
import { pick3d } from "./view3d.js";

const review = { on: false, index: 0 };

export const reviewing = () => review.on;

/** The likely types of a room (the shortlist: 1–9). */
export const likelyTypes = (s) => likely(s, units().filter((u) => !tucked(u)), state.project.types);

function show(id) {
  const queue = reviewSpaces();
  review.index = Math.max(0, queue.findIndex((s) => s.id === id));
  select(id);
  flyToSpace(state.byId.get(id), { always: true });
  if (view3d.shown) pick3d(id, { go: true });
  render();
}

/** Review mode on, from the room chosen when it is to review, else the first. */
export function startReview() {
  const queue = reviewSpaces();
  if (!queue.length) return toast("Every room on this floor is checked");
  if (state.tool) setTool(null);
  review.on = true;
  $("map").classList.add("reviewing");
  const at = queue.find((s) => s.id === state.selected) || queue[Math.min(review.index, queue.length - 1)] || queue[0];
  emit("review");
  show(at.id);
}

export function stopReview() {
  if (!review.on) return;
  review.on = false;
  $("map").classList.remove("reviewing");
  render();
  emit("review");
}

/** The next room to review (``dir`` -1: the one before). The room shown, when it has just
 * left the list (checked), counts from where it was. */
export function step(dir = 1) {
  const queue = reviewSpaces();
  if (!queue.length) {
    finished();
    return;
  }
  const i = queue.findIndex((s) => s.id === state.selected);
  let at;
  if (i >= 0) at = (i + dir + queue.length) % queue.length;
  else at = dir > 0 ? review.index % queue.length : (review.index - 1 + queue.length) % queue.length;
  show(queue[at].id);
}

/** After a room was set or accepted in review mode: on to the next. */
export function advance(id) {
  if (!review.on) return;
  const queue = reviewSpaces();
  if (!queue.length) return finished();
  if (queue.some((s) => s.id === id)) return step(1); // still to review (a type still missing): the next all the same
  step(1);
}

function finished() {
  stopReview();
  clearSelection();
  toast("Every room on this floor is checked");
}

function chosenToReview() {
  const s = state.byId.get(state.selected);
  return s && review.on ? s : null;
}

/** The bar over the canvas while reviewing: where you are in the list, back, next, stop. */
function render() {
  const bar = $("review-bar");
  if (!review.on) {
    bar.hidden = true;
    return bar.replaceChildren();
  }
  const queue = reviewSpaces();
  const all = units().filter((s) => !tucked(s)).length;
  const i = queue.findIndex((s) => s.id === state.selected);
  const s = state.byId.get(state.selected);
  const key = (id) => keyText(keysOf(id)[0] || "");
  const btn = (ico, label, id, fn) => {
    const b = el("button", { type: "button", class: "btn-ghost btn-icon btn-sm", "aria-label": label, "data-tip": label, "data-key": keysOf(id)[0] || null },
      icon(ico, { size: 16 }));
    b.addEventListener("click", fn);
    return b;
  };
  const done = el("button", { type: "button", class: "btn-sm", "data-key": "escape", "data-tip": "Stop reviewing" }, "Done");
  done.addEventListener("click", stopReview);
  bar.replaceChildren(
    el("div", { class: "to-name" }, icon("list-checks", { size: 16 }), el("span", {}, "Reviewing")),
    el("div", { class: "to-sep" }),
    btn("chevron-left", "The one before", "review.prev", () => step(-1)),
    el("div", { class: "rb-where", "aria-live": "polite" },
      el("strong", {}, s ? title(s) : "—"),
      el("span", { class: "muted" }, i >= 0 ? `${i + 1} of ${queue.length} to review` : `checked · ${queue.length} left`)),
    btn("chevron-right", "The next one", "review.next", () => step(1)),
    el("div", { class: "rb-progress", role: "progressbar", "aria-label": "Rooms checked", "aria-valuemin": "0", "aria-valuemax": String(all),
      "aria-valuenow": String(all - queue.length) }, el("span", { style: `width:${all ? ((all - queue.length) / all) * 100 : 0}%` })),
    el("span", { class: "rb-keys muted" }, `1–9 a type · ${key("review.accept")} accept`),
    el("div", { class: "to-sep" }),
    done,
  );
  bar.hidden = false;
}

export function setupReviewMode() {
  command({ id: "review.start", title: "Review the rooms that need a look", group: "Review", icon: "list-checks",
    words: "to review queue check flagged next", when: () => Boolean(state.floor) && reviewSpaces().length > 0,
    why: () => "Every room on this floor is checked", run: startReview });
  command({ id: "review.next", title: "Next room to review", group: "Review", icon: "chevron-right", keys: ["n"],
    when: () => Boolean(state.floor) && (review.on || reviewSpaces().length > 0), why: () => "Every room on this floor is checked",
    run: () => (review.on ? step(1) : startReview()) });
  command({ id: "review.prev", title: "Room before, to review", group: "Review", icon: "chevron-left", keys: ["shift+n"],
    when: () => Boolean(state.floor) && (review.on || reviewSpaces().length > 0), why: () => "Every room on this floor is checked",
    run: () => (review.on ? step(-1) : startReview()) });
  command({ id: "review.stop", title: "Stop reviewing", group: "Review", when: () => review.on, why: () => "Not reviewing", run: stopReview });
  // in review mode: the room's keys
  command({ id: "review.n", title: "Next room to review", group: "Review", scope: "review", keys: ["n"], palette: false, run: () => step(1) });
  command({ id: "review.p", title: "Room before", group: "Review", scope: "review", keys: ["p", "shift+n"], palette: false, run: () => step(-1) });
  command({ id: "review.accept", title: "Accept the room as it is", group: "Review", scope: "review", keys: ["enter"], icon: "check",
    when: () => Boolean(chosenToReview()) && editable(), why: () => (editable() ? "Choose a room to review" : "View only"),
    run: () => {
      const s = chosenToReview();
      if (s.type === "unspecified") return toast("It needs a type: press its number (1–9), or choose one", true);
      accept(s).then((done) => { if (done) advance(s.id); });
    } });
  for (let i = 1; i <= 9; i++) {
    command({ id: `review.type-${i}`, title: `Type ${i} of the likely ones`, group: "Review", scope: "review", keys: [String(i)], palette: false,
      when: () => Boolean(chosenToReview()) && editable(),
      run: () => {
        const s = chosenToReview();
        const t = likelyTypes(s)[i - 1];
        if (!t) return;
        correct(s, { type: t.type }).then((done) => { if (done) advance(s.id); });
      } });
  }
  for (const e of ["spaces", "floor", "project", "selection"]) on(e, render);
  on("floor", (d) => { if (d?.changed) stopReview(); });
  on("tool", () => { if (state.tool) stopReview(); });
}
