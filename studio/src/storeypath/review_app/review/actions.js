// Review's commands that belong to no one tool or panel: the views, the panels, undo and
// redo, deleting and choosing, the floors, the drawing under the plan and how the floor is
// shown, the 3D view's look, the theme, Esc, the palette and the list of keys. The
// Select, Pan and Route tools. And the keyboard map's scopes (keys.js): which apply now.

import { editable, whyNotEditable } from "./access.js";
import { emit, on } from "./bus.js";
import { allCommands, command, getCommand, run } from "./commands.js";
import { deleteItem } from "./drawing.js";
import { $, el, icon, save, saved } from "./dom.js";
import * as finish from "./finish.js";
import { openFloor, reconvert } from "./floor.js";
import { buildingFloors } from "./floorstack.js";
import { changeAsset, chosenAsset, copyId, renderAssets } from "./items.js";
import { allBindings, kbd, reserve, setupKeys } from "./keys.js";
import { panelShown, showPanel } from "./layout.js";
import { closeMenu, menuOpen } from "./menu.js";
import { showTab } from "./navigator.js";
import { toast } from "./notify.js";
import { openPalette, paletteOpen } from "./palette.js";
import { fit, panBy, renderPlan, restyle, showUnderlay, zoomBy } from "./plan.js";
import { forEach, setFlag } from "./rooms.js";
import { reviewing, stopReview } from "./reviewmode.js";
import { chosenSpaces, clearSelection, sel, selectSpaces } from "./selection.js";
import { readable, state, units, view3d, visible } from "./state.js";
import { escapeTool, tool } from "./tools.js";
import { doorsMode, drawHere, pointerPlace, setAllFloors, setDoorsMode, setView, walkFloor } from "./view3d.js";
import { historyOpen, showHistory, step } from "../together.js";
import { navigateUrl } from "./topbar.js";

const in2d = () => view3d.mode === "2d" && Boolean(state.floor);
const NOT_2D = () => "On the plan (2D)";

/** Which of the keyboard map's scopes apply now, the first first. */
function scopes() {
  const list = [];
  if (reviewing()) list.push("review");
  if (view3d.mode === "walk") list.push("walk");
  if (state.tool) list.push(`tool:${state.tool}`);
  if (state.asset && !state.tool) list.push("item");
  if (view3d.shown) list.push("3d");
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

// rows of the list of keys said as one (the arrows), by the commands' IDs they stand for
const AS_ONE = [
  { ids: /^item\.move-\w+$/, title: "Move the item (Shift: 1 m)", keys: ["arrowleft", "arrowright", "arrowup", "arrowdown"] },
  { ids: /^item\.move-\w+-far$/, title: null },
  { ids: /^view\.pan-\w+$/, title: "Move the plan (Shift: further)", keys: ["arrowleft", "arrowright", "arrowup", "arrowdown"] },
  { ids: /^review\.type-\d$/, title: "Set one of the likely types", keys: ["1", "9"], range: true },
];

function keysHelp() {
  const groups = new Map();
  for (const c of allCommands()) {
    const keys = allBindings().filter((b) => b.id === c.id);
    if (!keys.length) continue;
    const g = c.scope === "review" ? "Review mode" : c.scope === "item" ? "An item chosen" : c.group;
    if (!groups.has(g)) groups.set(g, []);
    const one = AS_ONE.find((a) => a.ids.test(c.id));
    if (one && !one.title) continue;
    const title = one ? one.title : c.title;
    if (groups.get(g).some((r) => r.title === title)) continue;
    groups.get(g).push({ title, keys: one ? one.keys : keys.map((k) => k.chord), range: one?.range });
  }
  groups.set("Walking", [{ title: "Move, run", keys: ["w", "a", "s", "d", "shift"] }, { title: "Up or down at stairs and lifts", keys: ["pageup", "pagedown"] },
    { title: "Free the mouse", keys: ["escape"] }]);
  groups.set("On the plan", [{ title: "Move the plan (any tool)", keys: ["space"] }, { title: "Add a room to those chosen", keys: ["shift"] },
    { title: "Place or drag freely", keys: ["alt"] }]);
  const order = ["Tools", "View", "Edit", "Review", "Review mode", "An item chosen", "While drawing", "Floors", "Panels", "Share", "Help", "On the plan", "Walking"];
  const box = el("dialog", { class: "dialog keys-help", "aria-label": "Keyboard shortcuts" },
    el("div", { class: "kh-head" }, el("h2", {}, "Keyboard shortcuts"), el("button", { type: "button", class: "btn-ghost btn-icon", "aria-label": "Close", onclick: () => box.close() }, icon("x", { size: 16 }))),
    el("div", { class: "kh-body" }, ...[...groups].sort((a, b) => (order.indexOf(a[0]) + 99) % 99 - (order.indexOf(b[0]) + 99) % 99).map(([g, rows]) =>
      el("section", {}, el("h3", {}, g), el("dl", {}, ...rows.flatMap((r) => [el("dt", {}, r.title),
        el("dd", {}, ...r.keys.flatMap((k, i) => [i ? el("span", { class: "or" }, r.range ? "–" : r.keys.length > 2 ? "" : "or") : null, ...kbd(k)]))]))))),
    el("p", { class: "kh-foot muted" }, "Keys typed in a field are the field's. In review mode 1–9 set a type; with an item chosen R and the arrows are the item's."));
  box.addEventListener("close", () => box.remove());
  box.addEventListener("click", (e) => { if (e.target === box) box.close(); });
  document.body.append(box);
  box.showModal();
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
  command({ id: "view.2d", title: "Show the plan (2D)", group: "View", icon: "map", keys: ["2"], run: () => setView("2d") });
  command({ id: "view.here-2d", title: "This place in 2D", group: "View", icon: "map", keys: ["2"], scope: "3d", palette: false,
    run: () => drawHere(pointerPlace()) });
  command({ id: "view.3d", title: "Show in 3D", group: "View", icon: "box", keys: ["3"], when: converted,
    why: () => "This floor is not converted yet", run: () => setView("3d") });
  command({ id: "view.walk", title: "Walk through it", group: "View", icon: "footprints", keys: ["4"], when: converted,
    why: () => "This floor is not converted yet", run: () => setView("walk") });
  command({ id: "view.fit", title: "Fit the floor in view", group: "View", icon: "maximize", keys: ["f"], when: in2d, why: NOT_2D, run: fit });
  command({ id: "view.zoom-in", title: "Zoom in", group: "View", icon: "plus", keys: ["="], when: in2d, why: NOT_2D, repeat: true, run: () => zoomBy(1.25) });
  command({ id: "view.zoom-out", title: "Zoom out", group: "View", icon: "minus", keys: ["-"], when: in2d, why: NOT_2D, repeat: true, run: () => zoomBy(0.8) });
  for (const [key, dx, dy] of [["arrowleft", 1, 0], ["arrowright", -1, 0], ["arrowup", 0, 1], ["arrowdown", 0, -1]]) {
    command({ id: `view.pan-${key.slice(5)}`, title: `Move the plan ${key.slice(5)}`, group: "View", keys: [key, `shift+${key}`], palette: false,
      repeat: true, when: in2d, run: (e) => panBy(dx * (e?.shiftKey ? 240 : 60), dy * (e?.shiftKey ? 240 : 60)) });
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
  command({ id: "edit.copy-id", title: "Copy the ID of what is chosen", group: "Edit", icon: "copy", words: "tag clipboard",
    when: () => Boolean(state.selected || state.asset || state.item?.id), why: () => "Choose a room, an item or a door first",
    run: () => copyId(state.selected || state.asset || state.item.id) });
  command({ id: "edit.escape", title: "Close, give up or let go (the nearest first)", group: "Edit", keys: ["escape"], palette: false, run: escape });

  // ---- floors --------------------------------------------------------------------------------------
  command({ id: "floor.up", title: "The floor above", group: "Floors", icon: "arrow-up", keys: ["pageup"], when: () => Boolean(state.floor),
    run: () => floorStep(1) });
  command({ id: "floor.down", title: "The floor below", group: "Floors", icon: "arrow-down", keys: ["pagedown"], when: () => Boolean(state.floor),
    run: () => floorStep(-1) });
  // walking, at stairs or a lift: E (unless a door took it: the world's, first) and Q, or PgUp and PgDn
  command({ id: "walk.up", title: "Up the stairs", group: "Floors", keys: ["pageup", "e"], scope: "walk", palette: false, run: () => walkFloor(1) });
  command({ id: "walk.down", title: "Down the stairs", group: "Floors", keys: ["pagedown", "q"], scope: "walk", palette: false, run: () => walkFloor(-1) });
  command({ id: "floor.reconvert", title: "Re-read the floor's drawing", group: "Floors", icon: "refresh-cw", words: "convert read again revised",
    when: () => readable() && editable() && !state.converting, why: () => (readable() ? whyNotEditable() || "Being read" : "This floor has no drawing"),
    run: reconvert });

  // ---- help ------------------------------------------------------------------------------------------
  command({ id: "palette.open", title: "Search and commands", group: "Help", icon: "search", keys: ["mod+k", "/"], palette: false, run: openPalette });
  command({ id: "help.keys", title: "Keyboard shortcuts", group: "Help", icon: "keyboard", keys: ["?"], run: keysHelp });
  on("keys-help", keysHelp);

  // walking: the walker's keys are its own (W A S D, the arrows, Shift): nothing of Review
  // hears them there. E at a door is the world's: it takes it first (a key it took is not
  // Review's: keys.js), else E goes up stairs as before.
  reserve(["w", "a", "s", "d", "shift+w", "shift+a", "shift+s", "shift+d", "arrowup", "arrowdown", "arrowleft", "arrowright",
    "shift+arrowup", "shift+arrowdown", "shift+arrowleft", "shift+arrowright"], "walk");
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
