// The top bar: where you are (the project, the building, the floor: its menu switches to
// another, with how many rooms each has to review), the view (2D, 3D, Walk), search (the
// palette), undo, redo and history (together.js binds them), who is here, and Share: the
// building's package, the project's file, an area of the floor, the print, the 3D view on
// a page of its own, the way through the building.

import { editableByAccess } from "./access.js";
import { on } from "./bus.js";
import { canRun, command, run, whyNot } from "./commands.js";
import { $, el } from "./dom.js";
import { keysOf, keyText, MAC } from "./keys.js";
import { closeMenu, menuItem, placeMenu } from "./menu.js";
import { toast } from "./notify.js";
import { BASE, buildingOf, CODE, state, view3d } from "./state.js";

const floorOf = (id) => state.project?.floors.find((f) => f.id === id) || null;

// ---- where you are ------------------------------------------------------------------------

function renderCrumbs() {
  const p = state.project;
  if (!p) return;
  $("crumb-project").textContent = p.project.name;
  $("crumb-project").href = `/#/p/${encodeURIComponent(CODE)}`;
  const f = state.floor ? floorOf(state.floor.id) : null;
  $("crumb-building").textContent = f ? f.building : "";
  $("crumb-building").dataset.tip = f ? `${f.location} · ${f.building}` : "";
  $("crumb-floor-name").textContent = f ? f.name : p.floors.length ? "Choose a floor" : "No floors yet";
  const n = f?.review || 0;
  $("crumb-floor-review").hidden = !n;
  $("crumb-floor-review").textContent = String(n);
  $("crumb-floor-review").dataset.tip = `${n} to review`;
  document.title = f ? `${f.name} · ${p.project.name} · StoreyPath Review` : `${p.project.name} · StoreyPath Review`;
  $("view-only").hidden = !state.floor || editableByAccess();
  for (const b of document.querySelectorAll("#view-mode button")) {
    const off = b.dataset.view !== "2d" && !state.floor?.converted_at;
    b.setAttribute("aria-disabled", String(off));
    b.dataset.why = off ? "This floor is not converted yet: there is nothing to show in 3D" : "";
  }
}

/** The floors, grouped by site and building, in a menu under the floor's name. */
function floorMenu() {
  const button = $("crumb-floor");
  if (!$("menu").hidden && button.getAttribute("aria-expanded") === "true") return closeMenu();
  const items = [];
  const groups = new Map();
  for (const f of state.project.floors) {
    const key = `${f.location} · ${f.building}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(f);
  }
  for (const [name, floors] of groups) {
    items.push(el("div", { class: "menu-section" }, name));
    for (const f of [...floors].sort((a, b) => b.ordinal - a.ordinal)) {
      const b = menuItem(f.name, () => openFloorById(f.id), { ico: f.id === state.floor?.id ? "check" : null,
        hint: f.converted ? "" : "not converted" });
      if (f.review) b.append(el("span", { class: "badge warn", "data-tip": `${f.review} to review` }, String(f.review)));
      if (f.id === state.floor?.id) b.setAttribute("aria-current", "true");
      items.push(b);
    }
  }
  $("menu").replaceChildren(...items.filter(Boolean));
  const r = button.getBoundingClientRect();
  placeMenu(r.left, r.bottom + 4);
  button.setAttribute("aria-expanded", "true");
  ($("menu").querySelector("[aria-current='true']") || $("menu").querySelector("button"))?.focus();
}

let openFloorById = () => {};

// ---- share ---------------------------------------------------------------------------------

function shareMenu() {
  const button = $("share-open");
  if (!$("menu").hidden && button.getAttribute("aria-expanded") === "true") return closeMenu();
  const item = (id, label, ico) => {
    const k = keysOf(id)[0];
    const b = menuItem(label, () => run(id, null, { say: (why) => toast(why, true) }), { ico, hint: k ? keyText(k) : "", disabled: !canRun(id) });
    if (!canRun(id)) b.dataset.tip = whyNot(id);
    return b;
  };
  const f = state.floor ? floorOf(state.floor.id) : null;
  $("menu").replaceChildren(
    el("div", { class: "menu-section" }, "Send"),
    item("share.export", `Export a package of ${f ? f.building : "the building"}`, "package"),
    item("share.project", "Download the project file", "download"),
    item("tool.share", "Share an area of this floor…", "square-dashed-mouse-pointer"),
    el("hr"),
    el("div", { class: "menu-section" }, "Open"),
    item("share.print", "The print, full size", "printer"),
    item("share.window", "The building in 3D, on its own page", "app-window"),
    item("tool.route", "Find the way in this building", "route"),
  );
  const r = button.getBoundingClientRect();
  placeMenu(r.right - 260, r.bottom + 4);
  button.setAttribute("aria-expanded", "true");
  $("menu").querySelector("button:not(:disabled)")?.focus();
}

/** A building's package exported (Studio's export, as the project page does it), then
 * downloaded; a copy stays in the project's list. */
async function exportBuilding(request, followJob, say) {
  const building = buildingOf(state.floor.id);
  const f = floorOf(state.floor.id);
  try {
    say(`Exporting ${f?.building || "the building"}…`);
    const job = await followJob(await request(`${BASE}/export`, { building }), `Exporting ${f?.building || "the building"}…`);
    const file = job.result.file;
    const a = el("a", { href: `/api/${BASE}/exports/${encodeURIComponent(file)}`, download: file });
    document.body.append(a);
    a.click();
    a.remove();
    toast(`Exported ${file}: downloading it. A copy stays in the project's list of packages`);
  } catch (e) {
    toast(`Not exported: ${e.message}`, true);
  } finally {
    say("");
  }
}

const open = (url) => window.open(url, "_blank", "noopener");

/** The way between two places of the floor's building (navigate.html). */
export const navigateUrl = () => "/navigate.html?" + new URLSearchParams({ p: CODE || "",
  ...(state.floor ? { building: buildingOf(state.floor.id) } : {}) });

export function setupTopbar({ openFloor, request, followJob, say }) {
  openFloorById = openFloor;
  $("search-key").textContent = MAC ? "⌘K" : "Ctrl K";
  $("crumb-floor").addEventListener("click", floorMenu);
  $("share-open").addEventListener("click", shareMenu);
  $("search-open").addEventListener("click", () => run("palette.open"));
  // the menus' buttons say when they are open
  new MutationObserver(() => {
    if ($("menu").hidden) for (const id of ["crumb-floor", "share-open"]) $(id).setAttribute("aria-expanded", "false");
  }).observe($("menu"), { attributes: true, attributeFilter: ["hidden"] });

  // the view: a radio group (the arrows move through it)
  const views = [...document.querySelectorAll("#view-mode button")];
  for (const b of views) {
    b.addEventListener("click", () => {
      if (b.getAttribute("aria-disabled") === "true") return toast(b.dataset.why, true);
      run(`view.${b.dataset.view}`);
    });
    b.addEventListener("keydown", (e) => {
      const i = views.indexOf(b);
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!step) return;
      e.preventDefault();
      const next = views[(i + step + views.length) % views.length];
      next.focus();
      next.click();
    });
  }
  const markView = () => {
    for (const b of views) {
      const on_ = b.dataset.view === view3d.mode;
      b.setAttribute("aria-checked", String(on_));
      b.tabIndex = on_ ? 0 : -1;
    }
  };
  markView();
  on("view", markView);
  for (const e of ["project", "floor", "access"]) on(e, renderCrumbs);

  command({ id: "share.export", title: "Export the building's package", group: "Share", icon: "package",
    words: "storeypath package download export building",
    when: () => Boolean(state.floor) && editableByAccess(), why: () => (state.floor ? "Exporting takes edit access to the building" : "Open a floor first"),
    run: () => exportBuilding(request, followJob, say) });
  command({ id: "share.project", title: "Download the project file", group: "Share", icon: "download",
    words: "storeypath-project send another studio",
    run: () => {
      const a = el("a", { href: `/api/${BASE}/project.storeypath-project`, download: `${CODE}.storeypath-project` });
      document.body.append(a);
      a.click();
      a.remove();
    } });
  command({ id: "share.print", title: "Open the print, full size", group: "Share", icon: "printer", words: "drawing png image",
    when: () => Boolean(state.floor?.source), why: () => "This floor has no drawing",
    run: () => open(`/api/${BASE}/floors/${encodeURIComponent(state.floor.id)}/print.png`) });
  command({ id: "share.window", title: "Open the building in 3D on its own page", group: "Share", icon: "app-window",
    words: "full screen window present", when: () => Boolean(state.floor?.converted_at), why: () => "This floor is not converted yet",
    run: () => open("/world.html?" + new URLSearchParams({
      pkg: `/api/${BASE}/preview.storeypath?building=${encodeURIComponent(buildingOf(state.floor.id))}`, floor: state.floor.id })) });
}
