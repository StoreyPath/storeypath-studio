// Review's commands that belong to no one tool or panel: the views, the panels, undo and
// redo, deleting and choosing, the floors, the drawing under the plan and how the floor is
// shown, the 3D view's look, the theme, Esc, the palette and the list of keys. The
// Select, Pan and Route tools. And the keyboard map's scopes (keys.js): which apply now.

import { editable, whyNotEditable } from "./access.js";
import { emit, on } from "./bus.js";
import { allCommands, command, getCommand, run, worksNow } from "./commands.js";
import { deleteItem } from "./drawing.js";
import { $, el, icon, save, saved } from "./dom.js";
import * as finish from "./finish.js";
import { openFloor, reconvert } from "./floor.js";
import { buildingFloors } from "./floorstack.js";
import { changeAsset, chosenAsset, copyId, renderAssets } from "./items.js";
import { effectiveMap, kbd, reserve, scopesApplying, setupKeys } from "./keys.js";
import { panelShown, showPanel } from "./layout.js";
import { closeMenu, menuOpen, openMenu } from "./menu.js";
import { showTab } from "./navigator.js";
import { toast } from "./notify.js";
import { openPalette, paletteOpen } from "./palette.js";
import { fit, panBy, planPoint, renderPlan, restyle, showUnderlay, viewport, zoomBy } from "./plan.js";
import { forEach, setFlag } from "./rooms.js";
import { reviewing, stopReview } from "./reviewmode.js";
import { chosenSpaces, clearSelection, sel, selectSpaces } from "./selection.js";
import { readable, state, units, view3d, visible } from "./state.js";
import { activeTool, escapeTool, tool } from "./tools.js";
import { doorsMode, drawHere, menuHere, pointerPlace, setAllFloors, setDoorsMode, setView, walkDoor, walkFloor } from "./view3d.js";
import { historyOpen, showHistory, step } from "../together.js";
import { navigateUrl } from "./topbar.js";

const in2d = () => view3d.mode === "2d" && Boolean(state.floor);
const NOT_2D = () => "On the plan (2D)";

/** Which of the keyboard map's scopes apply now, the first first: review mode, walking,
 * the item chosen (whatever the tool: what is chosen wins), the tool, the view. */
function scopes() {
  const list = [];
  if (reviewing()) list.push("review");
  if (view3d.mode === "walk") list.push("walk");
  if (state.asset) list.push("item");
  if (state.tool) list.push(`tool:${state.tool}`);
  list.push(view3d.shown ? "3d" : "2d");
  list.push("global");
  return list;
}

/** Esc: the nearest thing put away, one at a time. */
function escape(e) {
  if (e?.target?.closest?.("input, select, textarea")) return e.target.blur();
  if (menuOpen()) return closeMenu();
  if (paletteOpen()) return;
  if (finish.escape()) return; // the finish picker (or the dropper)
  if (document.querySelector(".type-picker")) return document.querySelector(".type-chooser")?.click();
  if (historyOpen()) return showHistory(false);
  if (escapeTool()) return;
  if (reviewing()) return stopReview();
  if (sel.kind) return clearSelection();
}

/** What is chosen deleted (rooms, an item, a door, window or opening; a drawn line taken away). */
async function deleteChosen() {
  if (sel.kind === "space") {
    const spaces = chosenSpaces().filter((s) => !s.ignored);
    if (spaces.length === 1) return setFlag(spaces[0].id, "ignored", true);
    if (spaces.length > 1) {
      const n = await forEach(spaces, (s) => setFlag(s.id, "ignored", true), "Deleting the rooms");
      return toast(`${n} rooms deleted`);
    }
  } else if (sel.kind === "asset") {
    const a = chosenAsset();
    if (a) return changeAsset(a, { retired: !a.retired });
  } else if (sel.kind === "opening" || sel.kind === "drawn") return deleteItem();
}

function setTheme(theme) {
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else document.documentElement.dataset.theme = "dark";
  save("storeypath.theme", theme);
  emit("settings");
}

function chooseLook({ style, quality }) {
  if (style) save("storeypath.world.style", style);
  if (quality) save("storeypath.world.quality", quality);
  if (style) view3d.world?.setStyle(style);
  if (quality) view3d.world?.setQuality(quality);
  emit("settings");
}

function floorStep(dir) {
  const floors = buildingFloors();
  const i = floors.findIndex((f) => f.id === state.floor?.id);
  const next = floors[i - dir]; // the top one first: up is before
  if (!next) return toast(dir > 0 ? "This is the top floor" : "This is the lowest floor");
  openFloor(next.id);
}

// rows of the list of keys said as one (the arrows, the likely types), by the commands' IDs:
// the keys shown (those of them that are theirs here), the others they cover (Shift with them)
const AS_ONE = [
  { ids: /^item\.move-\w+$/, title: "Move the item (Shift: 1 m)", keys: ["arrowleft", "arrowright", "arrowup", "arrowdown"] },
  { ids: /^view\.pan-\w+$/, title: "Move the plan (Shift: further)", keys: ["arrowleft", "arrowright", "arrowup", "arrowdown"] },
  { ids: /^review\.type-\d$/, title: "Set one of the likely types", keys: ["1", "9"], range: true },
];
const VIEW_NAMES = { "2d": "2D", "3d": "3D", walk: "Walk" };

/** What the mouse (or a finger) does in a view, with the tool in use: [what, what it does]. */
function gestures() {
  const mode = view3d.mode, t = state.tool;
  if (t === "place") {
    return [[mode === "2d" ? "Click in a room" : "Click on the floor", "Place the item there (it lines up)"], ["Alt-click", "Place it as it is, anywhere"]];
  }
  if (t === "paint") {
    return mode === "2d" ? [["Click a room", "Paint its floor"], ["Shift-click", "Paint its walls"], ["Alt-click", "Take up its finishes"]]
      : [["Click a floor or a wall", "Paint it (a wall: the side you see)"], ["Alt-click", "Take up its finish"]];
  }
  if (mode === "walk") {
    return [["Drag", "Look round (either button, or a finger)"], ["Click", "Choose, or open and close a door"], ["Double-click", "Go there"],
      ["Scroll", "A step on or back"], ["Drag the item chosen", "Carry it (Alt: freely)"], ["Right-click", "What can be done here"]];
  }
  if (mode === "3d") {
    return [["Click", "Choose a room or an item"], ["Drag", "Turn the view"], ["Shift-drag or right-drag", "Move the view"], ["Scroll", "Zoom"],
      ["Drag an item", "Carry it (Alt: freely)"], ["Right-click", "What can be done here"]];
  }
  return [["Click", "Choose a room, an item or a door"], ["Shift-click", "Add a room to those chosen"], ["Shift-drag", "Choose the rooms in a band"],
    ["Drag", "Move the plan, or carry an item (Alt: freely)"], ["Scroll", "Zoom"], ["Right-click", "What can be done here"]];
}

/** The situation the list of keys is for: the view, and what is chosen and in use. */
export function situation() {
  const parts = [VIEW_NAMES[view3d.mode]];
  if (reviewing()) parts.push("reviewing");
  if (state.asset) parts.push("an item chosen");
  else if (sel.kind === "space") parts.push(chosenSpaces().length > 1 ? "rooms chosen" : "a room chosen");
  else if (sel.kind) parts.push("a door or line chosen");
  const t = activeTool();
  if (state.tool && t) parts.push(t.label);
  return parts.join(" · ");
}

/** The keys that work now, grouped as the list shows them: [[group, [{ title, keys, ids, range }]]].
 * Every key here does what it says, in the view and with what is chosen and in use (the
 * map's first scope that has it; a command that can run and does here; a key reserved for
 * the walker or the plan). */
export function keysNow() {
  const groups = new Map();
  const add = (g, row) => {
    if (!groups.has(g)) groups.set(g, []);
    const rows = groups.get(g), same = rows.find((r) => r.title === row.title);
    if (!same) return rows.push({ also: [], ...row });
    for (const k of row.keys) if (!same.keys.includes(k)) same.keys.push(k);
    for (const k of row.also ?? []) if (!same.also.includes(k) && !same.keys.includes(k)) same.also.push(k);
    for (const id of row.ids) if (!same.ids.includes(id)) same.ids.push(id);
  };
  const toolNow = activeTool(), map = effectiveMap();
  for (const b of map.values()) {
    if (b.id === null) { // reserved: another's handler (the walker's Shift-keys: Shift held, to run)
      const run = b.owner === "walker" && b.chord.startsWith("shift+");
      add(b.scope === "walk" ? "Walking" : "On the plan", run
        ? { title: "Run (held while walking)", keys: ["shift"], also: [b.chord], ids: [`${b.owner}:${b.chord}`], held: true }
        : { title: b.title || "Another's", keys: [b.chord], ids: [`${b.owner}:${b.chord}`] });
      continue;
    }
    const c = getCommand(b.id);
    if (!c || !worksNow(c)) continue;
    const one = AS_ONE.find((a) => a.ids.test(c.id.replace(/-far$/, "")));
    const g = b.scope === "review" ? "Review mode" : b.scope === "item" ? "The item chosen" : b.scope === "walk" ? "Walking"
      : b.scope.startsWith("tool:") ? `While using ${toolNow?.label ?? "the tool"}` : c.group;
    if (!one) {
      add(g, { title: c.title, keys: [b.chord], ids: [c.id] });
      continue;
    }
    // (said as one: the keys of theirs shown that are theirs here; the others covered)
    const shown = one.keys.filter((k) => one.range || one.ids.test((map.get(k)?.id ?? "").replace(/-far$/, "")));
    add(g, { title: one.title, keys: shown, also: shown.includes(b.chord) ? [] : [b.chord], ids: [c.id], range: one.range });
  }
  return groups;
}

function keysHelp() {
  const groups = keysNow();
  const order = ["The item chosen", "Walking", "Review mode", ...[...groups.keys()].filter((g) => g.startsWith("While using")), "Tools", "View",
    "Edit", "Review", "Floors", "Panels", "Share", "On the plan", "Help"];
  const rank = (g) => (order.indexOf(g) + 99) % 99;
  const row = (r) => [el("dt", {}, r.title),
    el("dd", { "data-keys": r.keys.join(" "), "data-also": r.also.join(" "), "data-ids": r.ids.join(" "), "data-held": r.held ? "true" : null },
      ...r.keys.flatMap((k, i) => [i ? el("span", { class: "or" }, r.range ? "–" : r.keys.length > 2 ? "" : "or") : null, ...kbd(k)]))];
  const mouse = gestures();
  const box = el("dialog", { class: "dialog keys-help", "aria-label": "Keyboard shortcuts" },
    el("div", { class: "kh-head" }, el("div", {}, el("h2", {}, "Keyboard shortcuts"), el("p", { class: "kh-now muted" }, situation())),
      el("button", { type: "button", class: "btn-ghost btn-icon", "aria-label": "Close", onclick: () => box.close() }, icon("x", { size: 16 }))),
    el("div", { class: "kh-body" },
      ...[...groups].sort((a, b) => rank(a[0]) - rank(b[0])).map(([g, rows]) =>
        el("section", { "data-group": g }, el("h3", {}, g), el("dl", {}, ...rows.flatMap(row)))),
      el("section", { class: "kh-mouse", "data-group": "Mouse" }, el("h3", {}, view3d.mode === "walk" ? "The mouse, or a finger" : "The mouse"),
        el("dl", {}, ...mouse.flatMap(([what, does]) => [el("dt", {}, does), el("dd", {}, el("span", { class: "gesture" }, what))])))),
    el("p", { class: "kh-foot muted" }, "These are the keys for what is shown now: choose an item or a tool, or go to another view, for theirs. "
      + "Keys typed in a field are the field's."));
  box.addEventListener("close", () => box.remove());
  box.addEventListener("click", (e) => { if (e.target === box) box.close(); });
  document.body.append(box);
  box.showModal();
}

/** What each key does now, for the tests and the console: [{ chord, scope, id, owner, works }]. */
export function keyMap() {
  return [...effectiveMap().values()].map((b) => ({ ...b, works: b.id === null ? true : worksNow(b.id), idle: b.id ? getCommand(b.id)?.idle ?? null : null,
    scopes: scopesApplying() }));
}

export function setupActions() {
  // ---- the tools that are not drawing -------------------------------------------------------
  tool({ id: "select", label: "Select", icon: "mouse-pointer-2", key: "v", group: "navigate", views: ["2d", "3d", "walk"], keepsSelection: true,
    words: "choose pick pointer" });
  tool({ id: "pan", label: "Pan", icon: "hand", key: "h", group: "navigate", keepsSelection: true, cursor: "grab",
    wrongView: () => "In 3D, drag to turn the view, Shift-drag to move it",
    hint: () => "Drag to move the plan · scroll to zoom · Space held moves it with any tool" });
  tool({ id: "route", label: "Find the way", icon: "route", key: "g", group: "share", views: ["2d", "3d", "walk"],
    words: "navigate route kiosk wayfinding directions", action: () => window.open(navigateUrl(), "_blank", "noopener") });

  // ---- views ---------------------------------------------------------------------------------
  const converted = () => Boolean(state.floor?.converted_at);
  command({ id: "view.2d", title: "Show the plan (2D)", group: "View", icon: "map", keys: ["2"], works: () => view3d.mode !== "2d", idle: "shown already",
    run: () => setView("2d") });
  command({ id: "view.here-2d", title: "This place in 2D", group: "View", icon: "map", keys: ["2"], scope: "3d", palette: false,
    run: () => drawHere(pointerPlace()) });
  command({ id: "view.3d", title: "Show in 3D", group: "View", icon: "box", keys: ["3"], when: converted,
    why: () => "This floor is not converted yet", works: () => view3d.mode !== "3d", idle: "shown already", run: () => setView("3d") });
  command({ id: "view.walk", title: "Walk through it", group: "View", icon: "footprints", keys: ["4"], when: converted,
    why: () => "This floor is not converted yet", works: () => view3d.mode !== "walk", idle: "shown already", run: () => setView("walk") });
  command({ id: "view.fit", title: "Fit the floor in view", group: "View", icon: "maximize", keys: ["f"], when: in2d, why: NOT_2D, run: fit });
  command({ id: "view.zoom-in", title: "Zoom in", group: "View", icon: "plus", keys: ["="], when: in2d, why: NOT_2D, repeat: true, run: () => zoomBy(1.25) });
  command({ id: "view.zoom-out", title: "Zoom out", group: "View", icon: "minus", keys: ["-"], when: in2d, why: NOT_2D, repeat: true, run: () => zoomBy(0.8) });
  for (const [key, dx, dy] of [["arrowleft", 1, 0], ["arrowright", -1, 0], ["arrowup", 0, 1], ["arrowdown", 0, -1]]) {
    command({ id: `view.pan-${key.slice(5)}`, title: `Move the plan ${key.slice(5)}`, group: "View", keys: [key, `shift+${key}`], palette: false,
      repeat: true, when: in2d, why: () => (state.floor ? "The arrows move the plan in 2D: in 3D, drag to turn and Shift-drag to move" : NOT_2D()),
      run: (e) => panBy(dx * (e?.shiftKey ? 240 : 60), dy * (e?.shiftKey ? 240 : 60)) });
  }
  command({ id: "view.labels", title: "Studio's labels, or the drawing's texts", group: "View", icon: "type-outline", keys: ["t"],
    words: "names numbers text labels print drawing compare",
    run: () => {
      state.showLabels = !state.showLabels;
      $("labels").classList.toggle("hidden", !state.showLabels);
      $("map").classList.toggle("labels-on", state.showLabels);
      view3d.world?.setLabels(state.showLabels);
      save("storeypath.labels", state.showLabels ? "1" : "0");
      showUnderlay(); // (as printed: the print with its texts, or without them)
      emit("settings");
    } });
  command({ id: "view.side", title: "Side by side with the print", group: "View", icon: "printer", words: "compare print split",
    when: () => Boolean(state.floor?.source), why: () => "This floor has no drawing",
    run: () => {
      state.side = !state.side;
      save("storeypath.side", state.side ? "1" : "0");
      if (view3d.shown) setView("2d");
      showUnderlay();
      emit("settings");
    } });
  for (const [mode, name] of [["print", "as printed"], ["lines", "as its lines"], ["off", "off"]]) {
    command({ id: `view.drawing-${mode}`, title: `The drawing under the plan: ${name}`, group: "View", icon: "layers",
      run: () => {
        state.drawingMode = mode;
        save("storeypath.drawing", mode);
        showUnderlay();
        emit("settings");
      } });
  }
  command({ id: "view.colour-type", title: "Colour the rooms by type", group: "View", icon: "layers", run: () => {
    finish.colourRoomsBy("type");
    emit("settings");
  } });
  command({ id: "view.colour-finish", title: "Colour the rooms by floor finish", group: "View", icon: "paint-roller", run: () => {
    finish.colourRoomsBy("finish");
    emit("settings");
  } });
  command({ id: "view.deleted", title: "Show what was deleted", group: "View", icon: "eye", words: "hidden ignored restore",
    run: () => {
      state.showHidden = !state.showHidden;
      view3d.world?.setShowHidden(state.showHidden);
      renderAssets();
      if (state.floor) {
        renderPlan();
        restyle();
      }
      emit("settings");
      emit("spaces", null);
    } });
  command({ id: "view.all-floors", title: "Show every floor in 3D", group: "View", icon: "layers", words: "building stack all floors",
    run: () => setAllFloors(!view3d.allFloors) });
  for (const [style, name] of [["real", "Real"], ["model", "Model"]]) {
    command({ id: `view.look-${style}`, title: `3D look: ${name}`, group: "View", icon: "box", run: () => chooseLook({ style }) });
  }
  for (const [quality, name] of [["auto", "Auto"], ["high", "High"], ["low", "Low"]]) {
    command({ id: `view.quality-${quality}`, title: `3D quality: ${name}`, group: "View", icon: "box", run: () => chooseLook({ quality }) });
  }
  command({ id: "view.theme-light", title: "Light interface", group: "View", icon: "sun", words: "theme colours bright",
    when: () => document.documentElement.dataset.theme !== "light", why: () => "It is light", run: () => setTheme("light") });
  command({ id: "view.theme-dark", title: "Dark interface", group: "View", icon: "moon", words: "theme colours",
    when: () => document.documentElement.dataset.theme === "light", why: () => "It is dark", run: () => setTheme("dark") });

  // ---- panels ----------------------------------------------------------------------------------
  command({ id: "panel.navigator", title: "Show or hide the navigator", group: "Panels", icon: "panel-left-close", keys: ["["], run: () => showPanel("navigator") });
  command({ id: "panel.inspector", title: "Show or hide the inspector", group: "Panels", icon: "panel-right-close", keys: ["]"], run: () => showPanel("inspector") });
  command({ id: "panel.both", title: "Show or hide both panels", group: "Panels", icon: "maximize", keys: ["\\"],
    run: () => {
      const on_ = !(panelShown("navigator") || panelShown("inspector"));
      showPanel("navigator", on_);
      showPanel("inspector", on_);
    } });
  for (const [tab, name] of [["rooms", "Rooms"], ["items", "Items"], ["view", "View settings"]]) {
    command({ id: `panel.${tab}`, title: `Go to ${name}`, group: "Panels", icon: tab === "rooms" ? "list-checks" : tab === "items" ? "armchair" : "eye",
      run: () => {
        showPanel("navigator", true);
        showTab(tab);
        $(`tab-${tab}`).focus();
      } });
  }

  // ---- edit --------------------------------------------------------------------------------------
  const mayEdit = () => Boolean(state.floor) && editable();
  command({ id: "edit.undo", title: "Undo", group: "Edit", icon: "undo-2", keys: ["mod+z"], when: mayEdit, why: whyNotEditable, run: () => step(false) });
  command({ id: "edit.redo", title: "Redo", group: "Edit", icon: "redo-2", keys: ["mod+shift+z", "mod+y"], when: mayEdit, why: whyNotEditable, run: () => step(true) });
  command({ id: "edit.history", title: "History of this floor", group: "Edit", icon: "history", words: "who changed what log",
    run: () => showHistory(!historyOpen()) });
  command({ id: "edit.delete", title: "Delete what is chosen", group: "Edit", icon: "trash-2", keys: ["delete", "backspace"],
    when: () => Boolean(sel.kind) && editable(), why: () => (sel.kind ? whyNotEditable() : "Choose something first"), run: deleteChosen });
  command({ id: "edit.select-all", title: "Choose every room", group: "Edit", icon: "square-dashed", keys: ["mod+a"], when: () => Boolean(state.floor),
    run: () => selectSpaces(units().filter(visible).filter((s) => !s.ignored).map((s) => s.id)) });
  // the right-click menu from the keys: on the plan at the room chosen (its label), else the
  // middle of the view; in 3D and walking, at the pointer (or the middle)
  command({ id: "edit.menu", title: "What can be done here (the right-click menu)", group: "Edit", keys: ["shift+f10", "contextmenu"],
    palette: false, when: () => Boolean(state.floor) && (view3d.shown || editable()), why: () => (state.floor ? whyNotEditable() : "Open a floor first"),
    run: () => {
      if (view3d.shown) return menuHere();
      const { w, h, left, top } = viewport();
      const s = state.selected && state.byId.get(state.selected);
      const v = state.view;
      const at = s ? [s.label_point[0] * v.k + v.tx, -s.label_point[1] * v.k + v.ty] : [w / 2, h / 2];
      openMenu(left + at[0], top + at[1], planPoint(...at));
    } });
  command({ id: "edit.copy-id", title: "Copy the ID of what is chosen", group: "Edit", icon: "copy", words: "tag clipboard",
    when: () => Boolean(state.selected || state.asset || state.item?.id), why: () => "Choose a room, an item or a door first",
    run: () => copyId(state.selected || state.asset || state.item.id) });
  command({ id: "edit.escape", title: "Close, give up or let go (the nearest first)", group: "Edit", keys: ["escape"], palette: false, run: escape });

  // ---- floors --------------------------------------------------------------------------------------
  command({ id: "floor.up", title: "The floor above", group: "Floors", icon: "arrow-up", keys: ["pageup"], when: () => Boolean(state.floor),
    run: () => floorStep(1) });
  command({ id: "floor.down", title: "The floor below", group: "Floors", icon: "arrow-down", keys: ["pagedown"], when: () => Boolean(state.floor),
    run: () => floorStep(-1) });
  // walking: E the door under the pointer, else the nearest ahead (the world's, first), else
  // up at stairs or a lift; Q, PgUp and PgDn up and down there
  command({ id: "walk.door", title: "Open or close the door at the pointer, or ahead (at stairs: up)", group: "Walking", keys: ["e"], scope: "walk",
    palette: false, run: walkDoor });
  command({ id: "walk.up", title: "Up the stairs or the lift", group: "Walking", keys: ["pageup"], scope: "walk", palette: false, run: () => walkFloor(1) });
  command({ id: "walk.down", title: "Down the stairs or the lift", group: "Walking", keys: ["pagedown", "q"], scope: "walk", palette: false,
    run: () => walkFloor(-1) });
  command({ id: "floor.reconvert", title: "Re-read the floor's drawing", group: "Floors", icon: "refresh-cw", words: "convert read again revised",
    when: () => readable() && editable() && !state.converting, why: () => (readable() ? whyNotEditable() || "Being read" : "This floor has no drawing"),
    run: reconvert });

  // ---- help ------------------------------------------------------------------------------------------
  command({ id: "palette.open", title: "Search and commands", group: "Help", icon: "search", keys: ["mod+k", "/"], palette: false, run: openPalette });
  command({ id: "help.keys", title: "Keyboard shortcuts", group: "Help", icon: "keyboard", keys: ["?"], run: keysHelp });
  on("keys-help", keysHelp);

  // walking: the walker's keys are its own (W A S D, the arrows, Shift to run): nothing of
  // Review hears them there. E at a door is the world's: it takes it first (a key it took is
  // not Review's: keys.js), else walk.door. On the plan, Space held moves it (pointer.js).
  reserve(["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"], "walk", "walker", "Walk");
  reserve(["shift+w", "shift+a", "shift+s", "shift+d", "shift+arrowup", "shift+arrowdown", "shift+arrowleft", "shift+arrowright"], "walk",
    "walker", "Run");
  reserve(["space"], "2d", "plan", "Move the plan, held (with any tool)");
  command({ id: "view.doors-auto", title: "Doors open as you walk into them", group: "View", icon: "door-open", words: "walk doors open close manual",
    run: () => setDoorsMode(doorsMode() === "auto" ? "manual" : "auto") });

  const runner = (id, e) => {
    const c = getCommand(id);
    if (!c) return false;
    return run(id, e, { say: (why) => toast(why, true) });
  };
  runner.repeats = (id) => Boolean(getCommand(id)?.repeat);
  setupKeys({ scopes, run: runner });

  // the labels as this browser last had them
  if (saved("storeypath.labels") === "0") run("view.labels");
}
