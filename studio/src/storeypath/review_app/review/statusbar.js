// The status bar: what the tool in use expects (or what the pointer can do), what is under
// way (a print drawn, a floor read again), where the pointer is on the plan, what is
// chosen, who is editing the floor, whether every change is saved, and what went wrong.

import { editableByAccess } from "./access.js";
import { onSaving } from "./api.js";
import { on } from "./bus.js";
import { $, el, icon } from "./dom.js";
import { onToast, sayWith } from "./notify.js";
import { reviewing } from "./reviewmode.js";
import { chosenSpaces, sel } from "./selection.js";
import { state, view3d } from "./state.js";
import { activeTool, blocked } from "./tools.js";
import { doors } from "./view3d.js";
import { doneEditing, presence, takeOver } from "../together.js";

const VIEW_HINTS = {
  "2d": "Click to choose · Shift-click or ⌘-click to add · Shift-drag a band · drag or Space-drag to move the plan · right-click for more",
  "3d": "Click a room or an item · drag to turn · Shift-drag to move · scroll to zoom · right-click: draw here in 2D",
  walk: "Click the view to look · W A S D to move · Esc frees the mouse · right-click: this place in 2D",
};

/** Walking: what the person can do there now (a door aimed at, in a world that opens them). */
function walkHint() {
  if (view3d.doorAim) return `${view3d.doorAim.open ? "Close" : "Open"} the door: E or click`;
  return VIEW_HINTS.walk + (doors() ? " · E or click: open / close a door" : "");
}

let refused = "";
let refusedTimer = 0;

function renderHint() {
  const box = $("sb-hint");
  const t = activeTool();
  if (refused) {
    box.replaceChildren(icon("info", { size: 13 }), el("span", { class: "sb-error" }, refused));
    return;
  }
  if (reviewing()) {
    box.replaceChildren(el("span", { class: "sb-tool" }, "Reviewing"),
      el("span", {}, "1–9 a type · Enter accepts · N next · P back · Esc stops"));
    return;
  }
  if (state.tool && t) {
    const why = blocked(t);
    box.replaceChildren(icon(t.icon, { size: 13 }), el("span", { class: "sb-tool" }, t.label),
      el("span", {}, why || t.hint?.(view3d.mode) || ""));
    return;
  }
  if (!state.floor) return box.replaceChildren();
  if (state.converting) return box.replaceChildren(el("span", {}, "The floor's drawing is being read again…"));
  box.replaceChildren(el("span", {}, view3d.mode === "walk" ? walkHint() : VIEW_HINTS[view3d.mode] || ""));
}

function renderSelection() {
  const box = $("sb-selection");
  const n = sel.kind === "space" ? chosenSpaces().length : 0;
  const text = sel.kind === "space" ? (n === 1 ? (chosenSpaces()[0].kind === "zone" ? "1 zone" : "1 room") : `${n} rooms`)
    : sel.kind === "asset" ? "1 item" : sel.kind === "opening" ? "1 opening" : sel.kind === "drawn" ? "1 line drawn here" : "";
  box.textContent = text;
  box.dataset.tip = n > 1 ? "Rooms chosen: Shift-click or ⌘-click to add or take one out" : "";
}

function renderCursor(p) {
  $("sb-cursor").textContent = p && view3d.mode === "2d" ? `${p[0].toFixed(2)}, ${p[1].toFixed(2)} m` : "";
}

function renderLock() {
  const box = $("sb-lock");
  const { lock, mine, viewing } = presence();
  box.className = "sb-item sb-lock";
  const parts = [];
  if (!state.floor) return box.replaceChildren();
  if (!editableByAccess()) {
    parts.push(icon("eye", { size: 13 }), el("span", {}, "View only"));
  } else if (mine) {
    const done = el("button", { type: "button", "data-tip": "Let others edit it: your next change takes it again" }, "Done editing");
    done.addEventListener("click", doneEditing);
    parts.push(el("span", { class: "dot", "aria-hidden": "true" }), el("span", {}, "You're editing"), done);
  } else if (lock) {
    box.classList.add("other");
    parts.push(el("span", { class: "dot", "aria-hidden": "true" }), el("span", {}, `${lock.who.name || "Someone"} is editing`));
    if (presence().me?.role === "admin" || presence().me?.local) {
      const take = el("button", { type: "button", "data-tip": "Edit it yourself: they can no longer save changes to it" }, "Take over");
      take.addEventListener("click", takeOver);
      parts.push(take);
    }
  }
  const others = viewing.filter((p) => p.id !== lock?.who?.id);
  if (others.length) parts.push(el("span", { class: "muted" }, `· ${others.slice(0, 2).map((p) => (p.name || p.username).split(" ")[0]).join(", ")}${others.length > 2 ? ` +${others.length - 2}` : ""} viewing`));
  box.replaceChildren(...parts);
}

let savedTimer = 0;
function renderSave(what, error, sending) {
  const box = $("sb-save");
  clearTimeout(savedTimer);
  box.className = "sb-item sb-save";
  if (what === "saving" || sending > 0) {
    box.replaceChildren(el("span", { class: "spinner", "aria-hidden": "true" }), el("span", {}, "Saving…"));
  } else if (what === "failed") {
    box.classList.add("failed");
    box.replaceChildren(icon("triangle-alert", { size: 13 }), el("span", {}, "Not saved"));
    box.dataset.tip = error?.message || "";
  } else {
    box.classList.add("ok");
    box.replaceChildren(icon("check", { size: 13 }), el("span", {}, "Saved"));
    box.dataset.tip = "Every change is saved at once";
    savedTimer = setTimeout(() => {
      box.classList.remove("ok");
      box.replaceChildren(icon("check", { size: 13 }), el("span", {}, "All saved"));
    }, 2500);
  }
}

function renderJob(text) {
  const box = $("sb-job");
  box.hidden = !text;
  box.replaceChildren(...(text ? [el("span", { class: "spinner", "aria-hidden": "true" }), el("span", {}, text)] : []));
  box.dataset.tip = text || "";
}

export function setupStatusbar() {
  sayWith(renderJob);
  onSaving(renderSave);
  onToast((message, error) => {
    if (!error) return;
    refused = message;
    clearTimeout(refusedTimer);
    refusedTimer = setTimeout(() => {
      refused = "";
      renderHint();
    }, 8000);
    renderHint();
  });
  for (const e of ["tool", "view", "floor", "access", "tool-progress", "review", "converting", "walk"]) on(e, renderHint);
  on("tool-refused", (why) => {
    refused = why;
    clearTimeout(refusedTimer);
    refusedTimer = setTimeout(() => {
      refused = "";
      renderHint();
    }, 5000);
    renderHint();
  });
  on("selection", renderSelection);
  on("floor", renderSelection);
  on("cursor", renderCursor);
  on("view", () => renderCursor(null));
  for (const e of ["presence", "access", "floor"]) on(e, renderLock);
  $("sb-save").replaceChildren(icon("check", { size: 13 }), el("span", {}, "All saved"));
  $("sb-save").dataset.tip = "Every change is saved at once";
  renderHint();
}
