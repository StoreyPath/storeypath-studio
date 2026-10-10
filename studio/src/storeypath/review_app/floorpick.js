// Floors chosen in a floor stack (the 3D window's, Review's in 3D, the Navigate page's): a
// click shows a floor on its own; ⌘-click (Ctrl-click elsewhere; a long press on a tablet)
// shows it with the floors shown, or takes it away from them: the ground floor and the
// third, say, the others hidden (the world's setFloors).

import { MAC } from "./review/keys.js";

/** What ⌘-click (Ctrl-click) does to a floor of a stack, said in its tooltip. */
export const WITH_OTHERS = `${MAC ? "⌘" : "Ctrl"}-click: with the floors shown, or not`;

/** Whether a click is to show a floor with the others (⌘ or Ctrl held), not on its own. */
export const together = (e) => Boolean(e?.metaKey || e?.ctrlKey);

/** The floors shown once ``id`` is added to ``shown`` (IDs; null for every floor) or taken
 * away from them: in the building's order (``all``, lowest first); null when that is every
 * floor (unless ``every`` is false: then all of them, by name); ``shown`` as it was when it
 * would leave none. */
export function toggled(shown, id, all, { every = true } = {}) {
  const base = shown ?? all;
  const next = base.includes(id) ? base.filter((f) => f !== id) : all.filter((f) => base.includes(f) || f === id);
  if (!next.length) return shown;
  return every && next.length === all.length ? null : next;
}

/** A long press (a finger held half a second) on ``button`` does ``held`` in place of its
 * click: the click it ends in is not taken as one, though the stack (its parent) be drawn
 * again meanwhile. */
export function onHold(button, held) {
  let timer = 0;
  const stop = () => clearTimeout(timer);
  button.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") return;
    const box = button.parentElement;
    stop();
    timer = setTimeout(() => {
      if (box) swallowClick(box);
      held();
    }, 500);
  });
  for (const type of ["pointerup", "pointercancel", "pointerleave"]) button.addEventListener(type, stop);
  button.addEventListener("contextmenu", (e) => e.preventDefault()); // (the long press's own menu)
}

/** The next click in ``box`` (the one a long press ends in) not taken; none, once another
 * press starts. */
function swallowClick(box) {
  const off = new AbortController();
  box.addEventListener("click", (e) => {
    e.stopPropagation();
    e.preventDefault();
    off.abort();
  }, { capture: true, signal: off.signal });
  box.addEventListener("pointerdown", () => off.abort(), { capture: true, signal: off.signal });
}
