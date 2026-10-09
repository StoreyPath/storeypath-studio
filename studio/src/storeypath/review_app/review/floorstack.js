// The floor stack, on the canvas (bottom right), the same in 2D and 3D: the building's
// floors, the top one first, the one shown marked, those with rooms to review dotted; a
// click opens a floor. In 3D, All shows every floor of the building at once. And the
// zoom buttons beside it (2D).

import { on } from "./bus.js";
import { run } from "./commands.js";
import { $, el } from "./dom.js";
import { openFloor } from "./floor.js";
import { buildingOf, state, view3d } from "./state.js";

/** A floor's short name: its code (F00, B1…), as its ID ends. */
export const floorCode = (id) => id.split("-").at(-1);

/** The floors of the building of the floor shown, the top one first. */
export function buildingFloors() {
  if (!state.floor || !state.project) return [];
  const b = buildingOf(state.floor.id);
  return state.project.floors.filter((f) => buildingOf(f.id) === b).sort((x, y) => y.ordinal - x.ordinal);
}

function render() {
  const box = $("floor-stack");
  const floors = buildingFloors();
  if (floors.length < 2 && !(view3d.mode === "3d" && floors.length)) return box.replaceChildren();
  const parts = [];
  if (view3d.mode === "3d") {
    const all = el("button", { type: "button", class: "fs-all", "aria-pressed": String(view3d.allFloors),
      "data-tip": view3d.allFloors ? "Show this floor alone" : "Show every floor of the building" }, "All");
    all.addEventListener("click", () => run("view.all-floors"));
    parts.push(all, el("div", { class: "fs-sep", role: "separator" }));
  }
  for (const f of floors) {
    const current = f.id === state.floor.id;
    const b = el("button", { type: "button", "aria-current": current ? "true" : null,
      "aria-label": `${f.name}${f.review ? `, ${f.review} to review` : ""}${current ? ", shown" : ""}`,
      "data-tip": `${f.name}${f.review ? ` · ${f.review} to review` : ""}${f.converted ? "" : " · not converted"}` },
    el("span", {}, floorCode(f.id)), f.review ? el("span", { class: "fs-dot", "aria-hidden": "true" }) : null);
    b.addEventListener("click", () => {
      if (!current) openFloor(f.id);
      else if (view3d.allFloors) run("view.all-floors");
    });
    parts.push(b);
  }
  box.replaceChildren(...parts);
}

export function setupFloorStack() {
  for (const e of ["project", "floor", "view", "floors3d"]) on(e, render);
  $("zoom-in").addEventListener("click", () => run("view.zoom-in"));
  $("zoom-out").addEventListener("click", () => run("view.zoom-out"));
  $("fit").addEventListener("click", () => run("view.fit"));
}
