// The tools, in one registry: what each is called, its icon and key, the views it works in,
// what the person must be allowed (to change the floor; the floor's drawing), what the
// status bar says while it is in use, its options (shown over the canvas while it is in
// use), and what it does with the pointer. The rail on the left is drawn from it; each
// tool is a command too (commands.js), so the palette and the keys find it.
//
//   tool({
//     id: "wall", label: "Wall", icon: "brick-wall", key: "w",
//     group: "draw",                    the rail's group (navigate, draw, place, measure, share)
//     views: ["2d"],                    where it works ("2d", "3d", "walk")
//     edits: true,                      only for who may change the floor
//     drawing: true,                    only on a floor with its drawing
//     keepsSelection: false,            what is chosen stays chosen when it starts
//     hint: (view) => "…",              what the status bar says while it is in use
//     options: () => [elements],        its options bar (null: none)
//     start(), stop(),                  when it starts and stops being the tool
//     escape: () => bool,               Esc: undo what is under way (true), else the tool stops
//     plan: { hover, click, dblclick, down, move, up },   the pointer on the plan (plan metres)
//     keys: { enter: [fn, "what it does"], … },          its own keys while in use
//     action: () => {},                 a tool that does something at once (not a mode)
//   })

import { editable, whyNotEditable } from "./access.js";
import { emit, on } from "./bus.js";
import { command } from "./commands.js";
import { $, el, icon } from "./dom.js";
import { bind, keyNow, keyText } from "./keys.js";
import { readable, state, view3d } from "./state.js";

const tools = new Map();
const VIEW_NAMES = { "2d": "2D", "3d": "3D", walk: "Walk" };
const GROUPS = ["navigate", "draw", "place", "measure", "share"];
let starting = false;

export function tool(spec) {
  const t = { group: "draw", views: ["2d"], edits: false, drawing: false, keepsSelection: false, ...spec };
  tools.set(t.id, t);
  for (const [chord, spec] of Object.entries(t.keys || {})) {
    const [fn, what] = Array.isArray(spec) ? spec : [spec, chord];
    command({ id: `tool.${t.id}.${chord}`, title: `${t.label}: ${what}`, group: "While drawing", palette: false,
      scope: `tool:${t.id}`, keys: [chord], run: fn });
  }
  command({
    id: `tool.${t.id}`, title: t.action ? t.label : `${t.label} tool`, group: "Tools", icon: t.icon,
    keys: t.key ? [t.key] : [], words: t.words || "",
    when: () => !blocked(t, { anyView: true }),
    why: () => blocked(t, { anyView: true }),
    works: () => Boolean(t.action) || t.views.includes(view3d.mode),
    run: (e) => useTool(t.id, e),
  });
  return t;
}

export const getTool = (id) => tools.get(id || "select") || null;
export const allTools = () => [...tools.values()];
export const activeId = () => state.tool || "select";
export const activeTool = () => tools.get(activeId());

/** Why a tool cannot be used now ("" when it can); ``anyView``: wherever it works. */
export function blocked(t, { anyView = false } = {}) {
  if (typeof t === "string") t = tools.get(t);
  if (!t) return "No such tool";
  if (!state.floor) return "Open a floor first";
  if (!anyView && !t.views.includes(view3d.mode)) {
    return t.wrongView?.(view3d.mode) || `Works in ${t.views.map((v) => VIEW_NAMES[v]).join(" and ")}`;
  }
  if (t.edits && !editable()) return whyNotEditable();
  if (t.drawing && !readable()) return "This floor has no drawing yet: add its drawing to change its walls and openings";
  if (t.available) return t.available() || "";
  return "";
}

/** A tool chosen from its button, the palette or its key. In a view it does not work in,
 * its key changes nothing (a letter never switches the view: only 2, 3 and 4 do) and the
 * status bar says why, and that 2 shows the place under the pointer on the plan; the
 * palette (asked for by name) takes the plan first, with the tool. */
function useTool(id, e) {
  const t = tools.get(id);
  if (t.action) return t.action(e);
  if (!t.views.includes(view3d.mode)) {
    if (e instanceof KeyboardEvent) {
      const why = t.wrongView?.(view3d.mode) || `${t.label} works in ${t.views.map((v) => VIEW_NAMES[v]).join(" and ")}`;
      emit("tool-refused", t.views.includes("2d") ? `${why} · press 2 to see this place on the plan` : why);
      return true;
    }
    if (t.views.includes("2d")) return emit("draw-here", { tool: id, centre: true });
    return false;
  }
  setTool(id);
  return true;
}

/** ``id`` the tool in use (null or "select": choosing). A tool that cannot be used now is
 * not taken (its reason is the status bar's). */
export function setTool(id) {
  id = id && id !== "select" ? id : null;
  const next = id ? tools.get(id) : null;
  if (id && !next) return;
  if (next) {
    const why = blocked(next);
    if (why) {
      emit("tool-refused", why);
      return;
    }
  }
  if (next?.action) return next.action();
  const was = getTool(state.tool);
  if (starting) return;
  starting = true;
  try {
    if (state.tool !== id) was?.stop?.();
    state.tool = id;
    state.wallStart = null;
    state.corners = [];
    $("map").classList.toggle("drawing-tool", Boolean(next) && next.cursor !== "default" && id !== "pan");
    $("map").dataset.tool = id || "select";
    if (next && !next.keepsSelection) emit("clear-selection");
    next?.start?.();
  } finally {
    starting = false;
  }
  emit("tool");
}

/** Esc in a tool: what is under way undone (a corner, a measure), else the tool stops. */
export function escapeTool() {
  const t = getTool(state.tool);
  if (!state.tool) return false;
  if (t?.escape?.()) return true;
  setTool(null);
  return true;
}

// ---- the rail ----------------------------------------------------------------------------

function railButton(t) {
  const b = el("button", { type: "button", "data-tool": t.id, "aria-label": t.label, "data-tip": t.label,
    "data-key": t.key || "", "aria-pressed": t.action ? null : "false" }, icon(t.icon, { size: 19 }));
  if (t.key) b.append(el("span", { class: "rail-key", "aria-hidden": "true" }, keyText(t.key)));
  b.addEventListener("click", (e) => {
    if (b.getAttribute("aria-disabled") === "true") return;
    useTool(t.id, e);
  });
  return b;
}

export function renderRail() {
  const rail = $("rail");
  const parts = [];
  for (const group of GROUPS) {
    const list = allTools().filter((t) => t.group === group);
    if (!list.length) continue;
    if (parts.length) parts.push(el("div", { class: "rail-sep", role: "separator" }));
    parts.push(...list.map(railButton));
  }
  parts.push(el("div", { class: "rail-spacer" }));
  const help = el("button", { type: "button", id: "keys-help", "aria-label": "Keyboard shortcuts", "data-tip": "Keyboard shortcuts", "data-key": "?" },
    icon("keyboard", { size: 19 }));
  help.addEventListener("click", () => emit("keys-help"));
  parts.push(help);
  rail.replaceChildren(...parts);
  markRail();
}

/** The rail as things are: the tool in use pressed, those that cannot be used now dimmed
 * and saying why. */
export function markRail() {
  for (const b of document.querySelectorAll("#rail [data-tool]")) {
    const t = tools.get(b.dataset.tool);
    const why = blocked(t);
    const elsewhere = why && !t.views.includes(view3d.mode) && t.views.includes("2d") && !blocked(t, { anyView: true });
    b.setAttribute("aria-disabled", why ? "true" : "false");
    b.dataset.why = elsewhere ? `${why} · 2 shows this place on the plan` : why;
    // its key, when the key chooses it here (walking, W A S D are the walker's)
    const key = t.key && !why && keyNow(`tool.${t.id}`) ? t.key : "";
    b.dataset.key = key;
    b.querySelector(".rail-key")?.classList.toggle("off", !key);
    if (!t.action) b.setAttribute("aria-pressed", String(activeId() === t.id));
  }
}

// ---- the options bar: the tool's options, over the canvas while it is in use --------------

export function renderOptions() {
  const bar = $("tool-options");
  const t = activeTool();
  const parts = t?.options?.() || null;
  if (!state.tool || !parts) {
    bar.hidden = true;
    bar.replaceChildren();
    return;
  }
  const done = el("button", { type: "button", class: "btn-ghost btn-icon btn-sm", "aria-label": `Stop: ${t.label}`, "data-tip": "Done", "data-key": "escape" },
    icon("x", { size: 15 }));
  done.addEventListener("click", () => setTool(null));
  bar.replaceChildren(el("div", { class: "to-name" }, icon(t.icon, { size: 16 }), el("span", {}, t.label)),
    el("div", { class: "to-sep" }), ...[parts].flat().filter(Boolean), el("div", { class: "to-sep" }), done);
  bar.hidden = false;
}

export function setupTools() {
  renderRail();
  on("tool", () => {
    markRail();
    renderOptions();
  });
  for (const e of ["view", "access", "floor", "selection", "review"]) on(e, markRail);
  on("tool-options", renderOptions);
  // a tool that no longer works where the person is (another view, view only): set down
  on("view", () => {
    const t = getTool(state.tool);
    if (state.tool && t && !t.views.includes(view3d.mode)) setTool(null);
  });
  on("access", () => {
    const t = getTool(state.tool);
    if (state.tool && t?.edits && !editable()) setTool(null);
  });
}

export { bind };
