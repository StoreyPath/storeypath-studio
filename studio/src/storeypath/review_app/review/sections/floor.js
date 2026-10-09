// The inspector when nothing is chosen: the floor. How far its review is (and the way into
// review mode), its drawing (the file, how it was read, when; read it again; its print),
// the warnings of its last reading, and what is on it.

import { header, row, section } from "../inspector.js";
import { canRun, run } from "../commands.js";
import { el, icon } from "../dom.js";
import { reconvert } from "../floor.js";
import { keysOf } from "../keys.js";
import { reviewSpaces, state, tucked, units } from "../state.js";

const HOW = { walls: "Rooms found from its walls", outlines: "Rooms from the outlines drawn", package: "From a package, without its drawing" };

function floorHeader({ floor }) {
  if (!floor) {
    return [el("h2", { class: "ih-title" }, state.project?.floors.length ? "Choose a floor" : "No floors yet"),
      el("div", { class: "ih-sub" }, state.project?.floors.length ? "From the floor menu in the top bar" : "Add drawings on the project's page")];
  }
  const f = state.project.floors.find((x) => x.id === floor.id);
  return [el("div", { class: "ih-top" }, icon("layers", { size: 18, cls: "ih-icon" }), el("h2", { class: "ih-title" }, floor.name)),
    el("div", { class: "ih-sub" }, f ? `${f.location} · ${f.building}` : ""),
    el("div", { class: "ih-id" }, el("code", {}, floor.id))];
}

function progress({ floor }) {
  if (!floor) return null;
  const all = units().filter((s) => !tucked(s)).length;
  const left = reviewSpaces().length;
  const done = all - left;
  const parts = [
    el("div", { class: "progress-line" }, el("strong", {}, `${done} of ${all}`), el("span", { class: "muted" }, " rooms checked")),
    el("div", { class: "queue-progress big", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(all), "aria-valuenow": String(done),
      "aria-label": "Rooms checked" }, el("span", { style: `width:${all ? (done / all) * 100 : 0}%` })),
  ];
  if (left) {
    const go = el("button", { type: "button", class: "btn-primary btn-block", "data-key": keysOf("review.next")[0] || null,
      "data-tip": "One room after another: why it is flagged, its likely types (1–9), Enter accepts" },
    icon("list-checks", { size: 15 }), `Review ${left} room${left === 1 ? "" : "s"}`);
    go.addEventListener("click", () => run("review.start"));
    parts.push(go);
  } else if (all) parts.push(el("div", { class: "queue-done" }, icon("circle-check", { size: 16 }), "Every room is checked"));
  return parts;
}

function drawing({ floor }) {
  if (!floor) return null;
  const parts = [
    row("Drawing", el("span", { class: "value" }, floor.source || "None: add it on the project's page")),
    row("Read", el("span", { class: "value" }, floor.converted_at ? HOW[floor.method] || "Read" : "Not read yet"),
      floor.converted_at ? el("div", { class: "detected" }, new Date(floor.converted_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })) : null),
  ];
  const again = el("button", { type: "button", class: "btn-sm edit-only", disabled: !floor.source || state.converting || !canRun("floor.reconvert") || null,
    "data-tip": "Read the floor's drawing again (a revised one too): IDs and corrections are kept" },
  state.converting ? el("span", { class: "spinner", "aria-hidden": "true" }) : icon("refresh-cw", { size: 14 }),
  state.converting ? "Reading…" : "Re-read drawing");
  again.addEventListener("click", reconvert);
  const print = el("button", { type: "button", class: "btn-sm", disabled: !floor.source || null, "data-tip": "The floor's drawing as printed, full size, in a new tab" },
    icon("printer", { size: 14 }), "Open print");
  print.addEventListener("click", () => run("share.print"));
  const window_ = el("button", { type: "button", class: "btn-sm", disabled: !floor.converted_at || null,
    "data-tip": "The building in 3D on a page of its own, to show it full screen (nothing is changed from there)" },
  icon("app-window", { size: 14 }), "3D window");
  window_.addEventListener("click", () => run("share.window"));
  parts.push(el("div", { class: "sec-actions wrap" }, again, print, window_));
  return parts;
}

function warnings({ floor }) {
  if (!floor?.warnings?.length) return null;
  return el("ul", { class: "warnings" }, ...floor.warnings.map((w) => el("li", {}, icon("triangle-alert", { size: 13 }), el("span", {}, w))));
}

function onIt({ floor }) {
  if (!floor) return null;
  const spaces = floor.spaces.filter((s) => s.kind === "space" && !tucked(s)).length;
  const zones = floor.spaces.filter((s) => s.kind === "zone" && !tucked(s)).length;
  const doors = floor.doors.filter((d) => !d.ignored);
  const count = (t) => doors.filter((d) => (t === "opening" ? d.type !== "door" && d.type !== "window" : d.type === t)).length;
  const items = (floor.items || []).filter((a) => !a.retired).length;
  const area = floor.spaces.filter((s) => !tucked(s) && !(s.zones || []).length).reduce((a, s) => a + (s.area || 0), 0);
  const stat = (n, what) => el("div", { class: "stat" }, el("strong", {}, String(n)), el("span", {}, what));
  return el("div", { class: "stats" }, stat(spaces, "spaces"), stat(zones, "zones"), stat(count("door"), "doors"),
    stat(count("window"), "windows"), stat(count("opening"), "openings"), stat(items, "items"),
    el("div", { class: "stat wide" }, el("strong", {}, `${Math.round(area).toLocaleString()} m²`), el("span", {}, "in its rooms")));
}

export function setupFloorSections() {
  header({ kind: "floor", render: floorHeader });
  section({ id: "floor.review", kinds: ["floor"], title: "Review", order: 10, render: progress });
  section({ id: "floor.drawing", kinds: ["floor"], title: "Drawing", order: 20, render: drawing });
  section({ id: "floor.warnings", kinds: ["floor"], title: "Warnings from its reading", order: 30, render: warnings,
    summary: ({ floor }) => `${floor?.warnings?.length || 0}` });
  section({ id: "floor.stats", kinds: ["floor"], title: "On this floor", order: 40, render: onIt });
}
