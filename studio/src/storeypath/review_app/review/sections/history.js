// What was changed of one room or item: the floor's history (Studio's, as the History
// drawer lists it), the rows that name it. Asked for when its section is open; asked again
// after a change on the floor.

import { request } from "../api.js";
import { on } from "../bus.js";
import { el } from "../dom.js";
import { BASE, state } from "../state.js";
import { avatar } from "../../together.js";

let cache = null; // {floor, rows: Promise}

function rows() {
  const floor = state.floor?.id;
  if (!floor) return Promise.resolve([]);
  if (cache?.floor !== floor) {
    cache = { floor, rows: request(`${BASE}/history?floor=${encodeURIComponent(floor)}&n=200`).then((h) => h.entries).catch(() => null) };
  }
  return cache.rows;
}

const when = (iso) => {
  const t = new Date(iso), now = new Date();
  const s = (now - t) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return t.toDateString() === now.toDateString() ? t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : t.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
};

/** The history of ``id`` on the floor shown, as an element filled when it comes. */
export function roomHistory(id) {
  const box = el("ol", { class: "mini-history" }, el("li", { class: "muted" }, "…"));
  rows().then((list) => {
    if (list === null) return box.replaceChildren(el("li", { class: "muted" }, "History is not available here"));
    const mine = list.filter((e) => (e.targets || []).includes(id));
    box.replaceChildren(...(mine.length ? mine.slice(0, 20).map((e) => el("li", { class: e.undone ? "undone" : "" },
      avatar(e.who), el("div", {}, el("div", {}, el("strong", {}, e.mine ? "You" : e.who.name), " ", e.line),
        el("div", { class: "muted" }, when(e.at), e.undone ? " · undone" : ""))))
      : [el("li", { class: "muted" }, "No changes to it yet")]));
  });
  return box;
}

export function setupHistorySection() {
  for (const e of ["spaces", "items", "floor"]) on(e, () => { cache = null; });
}
