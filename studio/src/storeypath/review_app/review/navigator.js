// The navigator (left): three tabs.
//
//   Rooms  a filter (an item's tag typed in it finds that item); the rooms to review, to
//          go through one by one (review mode); every room grouped by its type (or, when the
//          plan is coloured so, by its floor's finish), each group with its colour and count.
//   Items  the floor's furniture and equipment by type; an item found by its tag on any
//          floor; a type placed from its group.
//   View   how the floor is shown: the drawing under it, side by side with the print,
//          labels, colours, what was deleted; the 3D view's look and quality, all floors;
//          the interface's theme.
//
// A room or item clicked is chosen and brought into view (Shift- or ⌘-click adds a room).
// The arrows move through a list, Enter chooses.

import { on } from "./bus.js";
import { run } from "./commands.js";
import { $, el, icon, save, saved } from "./dom.js";
import * as finish from "./finish.js";
import { categories, findAsset, placeType, showAsset } from "./items.js";
import { keysOf, keyText } from "./keys.js";
import { toast } from "./notify.js";
import { placeLabels, styleSpace } from "./plan.js";
import { reviewing } from "./reviewmode.js";
import { doorsMode } from "./view3d.js";
import { isChosen, select, selectAsset } from "./selection.js";
import { code, color, matches, reviewSpaces, state, title, tucked, typeName, typeOf, units, view3d, visible, within } from "./state.js";
import { normalizeItemId } from "/viewer/src/ids.js"; // items' IDs as people type them (the viewers' own)

const TABS = ["rooms", "items", "view"];
const open = new Set(JSON.parse(saved("storeypath.review.groups") || "[]")); // the groups opened
const shownFor = new Set(); // groups opened to show the room chosen (not remembered)
const LIMIT = 500; // rows in a group, at most (a filter finds the rest)

// ---- tabs ----------------------------------------------------------------------------

export function showTab(name) {
  for (const t of TABS) {
    const on_ = t === name;
    $(`tab-${t}`).setAttribute("aria-selected", String(on_));
    $(`tab-${t}`).tabIndex = on_ ? 0 : -1;
    $(`pane-${t}`).hidden = !on_;
  }
  save("storeypath.review.tab", name);
  if (name === "items") renderItems();
  if (name === "view") renderView();
}

// ---- rooms -----------------------------------------------------------------------------

function filterRooms(text) {
  state.filter = text.trim().toLowerCase();
  for (const s of state.floor?.spaces || []) styleSpace(s);
  placeLabels();
  renderRoomList();
}

function roomRow(s) {
  const b = el("button", { type: "button", class: `room-row${tucked(s) ? " tucked" : ""}`, "data-id": s.id,
    "aria-pressed": String(isChosen(s.id)) },
  el("span", { class: "rr-name" }, title(s)),
  s.reasons.length ? el("span", { class: "rr-flag", "data-tip": s.reasons.join("; ") }, icon("triangle-alert", { size: 12, label: "To review" })) : null,
  el("span", { class: "rr-code" }, s.ignored ? "deleted" : s.hidden ? "hidden" : code(s.id)));
  b.addEventListener("click", (e) => select(s.id, { fly: !(e.shiftKey || e.metaKey || e.ctrlKey), add: e.shiftKey || e.metaKey || e.ctrlKey }));
  return b;
}

/** The groups of rooms: by type (the project's order), or by floor finish. */
function roomGroups(rooms) {
  if (finish.colouredBy() === "finish") {
    return finish.groups(rooms).map((g) => ({ key: `finish:${g.code}`, name: g.name, swatch: finish.swatch(g.code, 14), rooms: g.rooms }));
  }
  const by = new Map();
  for (const s of rooms) {
    if (!by.has(s.type)) by.set(s.type, []);
    by.get(s.type).push(s);
  }
  const order = state.project.types;
  return [...by].sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
    .map(([type, list]) => ({ key: `type:${type}`, name: typeName(type), swatch: el("span", { class: "swatch", style: `background:${color(type)}` }), rooms: list }));
}

function renderReviewRow() {
  const box = $("rooms-review");
  if (!box || !state.floor) return;
  const n = reviewSpaces().length;
  const all = units().filter((s) => !tucked(s)).length;
  const key = keysOf("review.next")[0];
  if (!n) {
    box.replaceChildren(el("div", { class: "queue-done" }, icon("circle-check", { size: 16 }),
      el("span", {}, all ? "Every room is checked" : "No rooms on this floor")));
    return;
  }
  const b = el("button", { type: "button", class: `queue-row${reviewing() ? " active" : ""}`, id: "review-start",
    "data-tip": reviewing() ? "Reviewing: N next, P back, Esc stops" : "Go through them one by one", "data-key": key || "" },
  icon("list-checks", { size: 16 }), el("span", { class: "queue-text" }, el("strong", {}, "To review"),
    el("span", { class: "queue-sub" }, `${all - n} of ${all} checked`)),
  el("span", { class: "badge warn" }, String(n)), icon("chevron-right", { size: 15 }));
  b.addEventListener("click", () => run(reviewing() ? "review.next" : "review.start"));
  const bar = el("div", { class: "queue-progress", role: "progressbar", "aria-label": "Checked", "aria-valuemin": "0",
    "aria-valuemax": String(all), "aria-valuenow": String(all - n) }, el("span", { style: `width:${all ? ((all - n) / all) * 100 : 0}%` }));
  box.replaceChildren(b, bar);
}

function renderRoomList() {
  const box = $("room-groups");
  if (!box || !state.floor) return;
  const all = units().filter(visible);
  const shown = all.filter((s) => matches(s, state.filter));
  const groups = roomGroups(shown);
  const filtering = Boolean(state.filter);
  const parts = groups.map((g) => {
    const expanded = filtering || open.has(g.key) || shownFor.has(g.key);
    const head = el("button", { type: "button", class: "group-head", "aria-expanded": String(expanded) },
      icon("chevron-right", { size: 14, cls: "chev" }), g.swatch, el("span", { class: "group-name" }, g.name),
      g.rooms.some((s) => s.reasons.length) ? el("span", { class: "group-flag", "data-tip": "Some need a look" }) : null,
      el("span", { class: "count" }, String(g.rooms.length)));
    head.addEventListener("click", () => {
      if (open.has(g.key) || shownFor.has(g.key)) {
        open.delete(g.key);
        shownFor.delete(g.key);
      } else open.add(g.key);
      save("storeypath.review.groups", JSON.stringify([...open]));
      renderRoomList();
    });
    const rows = expanded ? g.rooms.slice(0, LIMIT).map(roomRow) : [];
    if (expanded && g.rooms.length > LIMIT) rows.push(el("div", { class: "more" }, `${g.rooms.length - LIMIT} more: type to find them`));
    return el("div", { class: "group", "data-group": g.key }, head, expanded ? el("div", { class: "group-rows" }, ...rows) : null);
  });
  const deleted = units().filter(tucked).length;
  if (!shown.length) parts.push(el("p", { class: "empty" }, filtering ? "No room matches" : "No rooms on this floor"));
  if (deleted && !state.showHidden) {
    const show = el("button", { type: "button", class: "btn-link" }, "show them");
    show.addEventListener("click", () => run("view.deleted"));
    parts.push(el("p", { class: "deleted-note" }, `${deleted} deleted · `, show));
  }
  box.replaceChildren(...parts);
  $("rooms-count").textContent = filtering ? `${shown.length} of ${all.length}` : `${all.length} room${all.length === 1 ? "" : "s"}`;
}

function markRooms() {
  // the room chosen (one): its group opened for the while, when it is closed
  const one = state.selected && state.byId.get(state.selected);
  if (one && !document.querySelector(`#room-groups .room-row[data-id="${CSS.escape(one.id)}"]`)) {
    const key = finish.colouredBy() === "finish" ? `finish:${finish.shown(one).floor}` : `type:${one.type}`;
    if (!shownFor.has(key) && !open.has(key)) {
      shownFor.clear();
      shownFor.add(key);
      renderRoomList();
    }
  }
  for (const b of document.querySelectorAll("#room-groups .room-row")) b.setAttribute("aria-pressed", String(isChosen(b.dataset.id)));
  const first = document.querySelector("#room-groups .room-row[aria-pressed='true']");
  if (first && !first.contains(document.activeElement)) first.scrollIntoView({ block: "nearest" });
}

function renderRooms() {
  const pane = $("pane-rooms");
  if (!pane.dataset.made) {
    pane.dataset.made = "1";
    const input = el("input", { id: "search", type: "search", placeholder: "Filter rooms, or an item's tag", autocomplete: "off",
      "aria-label": "Filter the rooms by name, number, type or ID; an item's tag finds that item" });
    input.addEventListener("input", () => {
      const typed = input.value;
      filterRooms(typed);
      const tag = normalizeItemId(typed); // an item's ID, as typed (7k2q xm9f 4dp): that item
      if (!tag) return;
      findAsset(tag, () => input.value === typed).then((r) => {
        if (r.found) {
          filterRooms("");
          input.value = "";
          showTab("items");
        } else if (r.error && input.value === typed && !units().some((s) => matches(s, state.filter))) toast(r.error, true);
      });
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        document.querySelector("#room-groups .group-head, #room-groups .room-row")?.focus();
        e.preventDefault();
      }
    });
    pane.append(
      el("div", { class: "nav-search" }, icon("search", { size: 14 }), input),
      el("div", { id: "rooms-review", class: "rooms-review" }),
      el("div", { class: "nav-heading" }, el("span", { id: "rooms-by" }, "By type"), el("span", { id: "rooms-count", class: "count" })),
      el("div", { id: "room-groups", class: "room-groups" }),
    );
    listKeys($("room-groups"));
  }
  $("rooms-by").textContent = finish.colouredBy() === "finish" ? "By floor finish" : "By type";
  renderReviewRow();
  renderRoomList();
}

/** The arrows through a list of buttons (rooms, groups, items). */
function listKeys(box) {
  box.addEventListener("keydown", (e) => {
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (!step && e.key !== "Home" && e.key !== "End") return;
    const rows = [...box.querySelectorAll("button")];
    const i = rows.indexOf(document.activeElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? rows.length - 1 : i + step;
    if (next < 0) return box.closest(".pane-body")?.querySelector("input")?.focus();
    rows[Math.min(rows.length - 1, next)]?.focus();
    e.preventDefault();
  });
}

// ---- items ----------------------------------------------------------------------------

function renderItems() {
  const pane = $("pane-items");
  if (!pane.dataset.made) {
    pane.dataset.made = "1";
    const input = el("input", { id: "item-find", type: "search", placeholder: "Find by tag", autocomplete: "off",
      "aria-label": "Find an item by its tag (7K2Q-XM9F-4DP: either case, with or without its hyphens), on any floor" });
    const go = () => {
      const typed = input.value;
      const tag = normalizeItemId(typed);
      if (!typed.trim()) return renderItemList();
      if (!tag) return renderItemList(typed);
      findAsset(tag, () => input.value === typed).then((r) => {
        if (r.error && input.value === typed) toast(r.error, true);
      });
    };
    input.addEventListener("input", go);
    const place = el("button", { type: "button", class: "btn-sm", "data-tip": "Place an item", "data-key": keysOf("tool.place")[0] || "" },
      icon("plus", { size: 14 }), "Place");
    place.addEventListener("click", () => run("tool.place"));
    pane.append(el("div", { class: "nav-search" }, icon("tag", { size: 14 }), input, place),
      el("div", { class: "nav-heading" }, el("span", {}, "On this floor"), el("span", { id: "items-count", class: "count" })),
      el("div", { id: "item-groups", class: "room-groups" }));
    listKeys($("item-groups"));
  }
  renderItemList($("item-find").value);
}

function roomOf(a) {
  return units().find((s) => !s.ignored && within([a.x, a.y], s.geometry)) || null;
}

function renderItemList(q = "") {
  const box = $("item-groups");
  if (!box || !state.floor) return;
  const text = q.trim().toLowerCase().replaceAll("-", "");
  const items = (state.floor.items || []).filter((a) => !a.retired || state.showHidden)
    .filter((a) => !text || a.id.toLowerCase().replaceAll("-", "").includes(text) || (typeOf(a.type)?.name_en || a.type).toLowerCase().includes(text));
  const byType = new Map();
  for (const a of items) {
    if (!byType.has(a.type)) byType.set(a.type, []);
    byType.get(a.type).push(a);
  }
  const order = categories().flatMap(([, types]) => types.map((t) => t.code));
  const parts = [...byType].sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0])).map(([type, list]) => {
    const t = typeOf(type);
    const key = `item:${type}`;
    const expanded = Boolean(text) || open.has(key);
    const add = el("button", { type: "button", class: "group-add btn-ghost btn-icon btn-sm", "aria-label": `Place a ${t?.name_en || type}`,
      "data-tip": `Place a ${t?.name_en || type}` }, icon("plus", { size: 13 }));
    add.addEventListener("click", () => placeType(type));
    const head = el("button", { type: "button", class: "group-head", "aria-expanded": String(expanded) },
      icon("chevron-right", { size: 14, cls: "chev" }), el("span", { class: "swatch", style: `background:${t?.color || "#8a8a8a"}` }),
      el("span", { class: "group-name" }, t?.name_en || type), el("span", { class: "count" }, String(list.length)));
    head.addEventListener("click", () => {
      if (open.has(key)) open.delete(key);
      else open.add(key);
      save("storeypath.review.groups", JSON.stringify([...open]));
      renderItemList($("item-find").value);
    });
    const rows = expanded ? list.map((a) => {
      const r = roomOf(a);
      const b = el("button", { type: "button", class: `room-row item-row${a.retired ? " tucked" : ""}`, "data-item": a.id,
        "aria-pressed": String(state.asset === a.id) },
      el("span", { class: "rr-name mono" }, a.id), el("span", { class: "rr-code" }, a.retired ? "deleted" : r ? title(r) : ""));
      b.addEventListener("click", () => {
        selectAsset(a.id);
        showAsset(a);
      });
      return b;
    }) : [];
    return el("div", { class: "group" }, el("div", { class: "group-head-row" }, head, add),
      expanded ? el("div", { class: "group-rows" }, ...rows) : null);
  });
  if (!items.length) {
    parts.push(el("div", { class: "empty" }, text ? "No item matches on this floor (a whole tag finds it on any floor)"
      : el("span", {}, "No items on this floor yet. ", el("span", { class: "muted" }, `Place one with the Place tool (${keyText(keysOf("tool.place")[0] || "i")}).`))));
  }
  box.replaceChildren(...parts);
  $("items-count").textContent = `${items.length} item${items.length === 1 ? "" : "s"}`;
}

// ---- view --------------------------------------------------------------------------------

function segmented(label, options, current, pick, { id = null } = {}) {
  const g = el("div", { class: "segmented block", role: "radiogroup", "aria-label": label, id });
  for (const [value, text, tip] of options) {
    const b = el("button", { type: "button", role: "radio", "aria-checked": String(value === current), "data-tip": tip || null }, text);
    b.addEventListener("click", () => pick(value));
    g.append(b);
  }
  return g;
}

function toggle(label, checked, change, { tip = null, key = null, disabled = false } = {}) {
  const input = el("input", { type: "checkbox", role: "switch", checked: checked || null, disabled: disabled || null });
  input.addEventListener("change", () => change(input.checked));
  return el("label", { class: "switch", "data-tip": tip, "data-key": key }, el("span", {}, label), input);
}

function renderView() {
  const pane = $("pane-view");
  if (pane.hidden) return;
  const k = (id) => keysOf(id)[0] || null;
  const look = view3d.world?.look ?? (view3d.look?.now?.() || { style: "real", quality: "auto" });
  const quality = el("select", { id: "quality", "aria-label": "Quality of the 3D view" },
    el("option", { value: "auto" }, look.quality === "auto" && look.drawn ? `Auto (${look.drawn === "low" ? "Low" : "High"})` : "Auto"),
    el("option", { value: "high" }, "High"), el("option", { value: "low" }, "Low"));
  quality.value = look.quality;
  quality.addEventListener("change", () => run(`view.quality-${quality.value}`));
  const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const section = (name, ...rows) => el("section", { class: "view-section" }, el("h3", {}, name), ...rows);
  pane.replaceChildren(
    section("The drawing",
      segmented("The drawing under the rooms", [["print", "As printed", "The drawing as printed, a pixel a centimetre"],
        ["lines", "Lines", "The drawing's lines"], ["off", "Off", "No drawing under the rooms"]], state.drawingMode,
      (v) => run(`view.drawing-${v}`)),
      toggle("Side by side with the print", state.side, () => run("view.side"), { tip: "The print on one side, the rooms on the other, moving together", key: k("view.side") })),
    section("The plan",
      toggle("Studio's labels", state.showLabels, () => run("view.labels"), { tip: "Rooms' names and numbers as Studio has them; off, the drawing's own texts (as printed)", key: k("view.labels") }),
      el("div", { class: "view-row" }, el("span", { class: "view-label" }, "Colour rooms by"),
        segmented("Colour the rooms by", [["type", "Type"], ["finish", "Floor finish"]], finish.colouredBy(), (v) => run(`view.colour-${v}`))),
      toggle("Show deleted", state.showHidden, () => run("view.deleted"), { tip: "What was deleted (and hidden), to restore it", key: k("view.deleted") })),
    section("3D and walking",
      el("div", { class: "view-row" }, el("span", { class: "view-label" }, "Look"),
        segmented("Look of the 3D view", [["real", "Real", "Floors finished by what each room is, plaster walls, soft shadows"],
          ["model", "Model", "White, like an architect's model, its edges drawn"]], look.style, (v) => run(`view.look-${v}`))),
      el("label", { class: "view-row" }, el("span", { class: "view-label" }, "Quality"), quality),
      toggle("Every floor of the building", view3d.allFloors, () => run("view.all-floors"), { tip: "In 3D: the building's floors all shown (as the floor stack's All)" }),
      toggle("Doors open as you walk into them", doorsMode() === "auto", () => run("view.doors-auto"),
        { tip: "Walking: off, a door opens and closes only with E or a click at it" })),
    section("Interface",
      segmented("Theme", [["dark", "Dark"], ["light", "Light"]], theme, (v) => run(`view.theme-${v}`), { id: "theme-switch" })),
  );
}

// ---- setting up ---------------------------------------------------------------------------

export function setupNavigator() {
  for (const t of TABS) {
    $(`tab-${t}`).addEventListener("click", () => showTab(t));
    $(`tab-${t}`).addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!step) return;
      const next = TABS[(TABS.indexOf(t) + step + TABS.length) % TABS.length];
      showTab(next);
      $(`tab-${next}`).focus();
      e.preventDefault();
    });
  }
  const tab = saved("storeypath.review.tab");
  showTab(TABS.includes(tab) ? tab : "rooms");
  renderRooms();
  on("floor", () => {
    renderRooms();
    if (!$("pane-items").hidden) renderItems();
    if (!$("pane-view").hidden) renderView();
  });
  on("spaces", () => {
    renderReviewRow();
    renderRoomList();
  });
  on("project", renderReviewRow);
  on("review", renderReviewRow);
  on("items", () => { if (!$("pane-items").hidden) renderItemList($("item-find")?.value || ""); });
  on("selection", () => {
    markRooms();
    for (const b of document.querySelectorAll("#item-groups .item-row")) b.setAttribute("aria-pressed", String(state.asset === b.dataset.item));
  });
  on("settings", () => {
    renderRooms();
    renderView();
    if (!$("pane-items").hidden) renderItems();
  });
  on("floors3d", renderView);
  on("catalogue", () => { if (!$("pane-items").hidden) renderItems(); });
}
