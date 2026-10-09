// The inspector (right): what is chosen, in sections, or the floor when nothing is. Its
// sections come from one registry, by what is chosen:
//
//   floor    nothing chosen: the floor (review progress, its drawing, warnings)
//   space    one room (space or zone)
//   spaces   several rooms (what can be changed for all at once)
//   asset    an item (furniture or equipment)
//   opening  a door, window or opening
//   drawn    a wall or dividing line drawn here
//
//   header({ kind: "space", render: (ctx) => [elements] })     its top: name, badges, ID
//   section({
//     id: "room.type",              unique; its open or closed state is remembered by it
//     kinds: ["space"],             what it is shown for
//     title: "Classification",      its heading
//     order: 20,                    top to bottom
//     open: true,                   open at first (false: closed until opened)
//     render: (ctx) => element | [elements] | null,   null: not shown for this one
//     summary: (ctx) => "text",     said in its heading when it is closed (optional)
//   })
//
// ctx: { kind, space, spaces, asset, opening, drawn, floor, editable }. A section's
// render is called again whenever what it shows may have changed; while a field in the
// inspector is being typed in, the inspector is not drawn again under it (it is when the
// field is left), unless what it shows was changed by someone else.

import { editable } from "./access.js";
import { on } from "./bus.js";
import { $, el, icon, save, saved } from "./dom.js";
import { chosenSpaces, sel } from "./selection.js";
import { state } from "./state.js";

const sections = [];
const headers = new Map();
const closed = new Set(JSON.parse(saved("storeypath.review.closed") || "[]"));
const opened = new Set(JSON.parse(saved("storeypath.review.opened") || "[]")); // those closed at first, opened
let pending = false;
let lastKey = "";

export function section(spec) {
  sections.push({ open: true, order: 50, ...spec });
  sections.sort((a, b) => a.order - b.order);
}

export function header(spec) {
  headers.set(spec.kind, spec);
}

/** What is chosen, as the sections are given it. */
export function context() {
  const spaces = chosenSpaces();
  const kind = sel.kind === "space" ? (spaces.length > 1 ? "spaces" : spaces.length ? "space" : "floor")
    : sel.kind === "asset" ? "asset" : sel.kind === "opening" ? "opening" : sel.kind === "drawn" ? "drawn" : "floor";
  return {
    kind,
    space: kind === "space" ? spaces[0] : null,
    spaces,
    asset: kind === "asset" ? (state.floor?.items || []).find((a) => a.id === sel.ids[0]) || null : null,
    opening: kind === "opening" ? (state.floor?.doors || []).find((d) => d.id === sel.ids[0]) || null : null,
    drawn: kind === "drawn" ? sel.line : null,
    floor: state.floor,
    editable: editable(),
  };
}

const isOpen = (s) => (s.open ? !closed.has(s.id) : opened.has(s.id));

function setOpen(s, on_) {
  if (s.open) on_ ? closed.delete(s.id) : closed.add(s.id);
  else on_ ? opened.add(s.id) : opened.delete(s.id);
  save("storeypath.review.closed", JSON.stringify([...closed]));
  save("storeypath.review.opened", JSON.stringify([...opened]));
}

function sectionBox(s, ctx) {
  let body;
  try {
    body = s.render(ctx);
  } catch (e) {
    console.error(`inspector section ${s.id}:`, e);
    return null;
  }
  if (body === null || body === undefined) return null;
  if (s.bare) return el("div", { class: "insp-bare", "data-section": s.id }, body);
  const shown = isOpen(s);
  const id = `sec-${s.id.replace(/\W/g, "-")}`;
  const summary = !shown && s.summary ? s.summary(ctx) : "";
  const head = el("button", { type: "button", class: "sec-head", "aria-expanded": String(shown), "aria-controls": id },
    icon("chevron-right", { size: 14, cls: "chev" }), el("span", { class: "sec-title" }, s.title),
    summary ? el("span", { class: "sec-summary" }, summary) : null);
  const box = el("section", { class: `insp-section${shown ? " open" : ""}`, "data-section": s.id },
    head, el("div", { class: "sec-body", id, hidden: !shown || null }, body));
  head.addEventListener("click", () => {
    setOpen(s, !isOpen(s));
    render({ force: true });
    document.querySelector(`[data-section="${s.id}"] .sec-head`)?.focus();
  });
  return box;
}

/** The inspector drawn for what is chosen. ``force``: even under a field being typed in. */
export function render({ force = false } = {}) {
  const body = $("inspector-body");
  const typing = body.contains(document.activeElement) && document.activeElement.matches("input, select, textarea");
  const ctx = context();
  const key = `${ctx.kind}:${ctx.space?.id || ctx.asset?.id || ctx.opening?.id || ctx.drawn?.at || ctx.spaces.map((s) => s.id).join(",")}`;
  if (typing && !force && key === lastKey) {
    pending = true;
    return;
  }
  pending = false;
  const scroll = key === lastKey ? body.scrollTop : 0;
  lastKey = key;
  const h = headers.get(ctx.kind);
  $("inspector-head").replaceChildren(...(h ? [h.render(ctx)].flat().filter(Boolean) : []));
  body.replaceChildren(...sections.filter((s) => s.kinds.includes(ctx.kind)).map((s) => sectionBox(s, ctx)).filter(Boolean));
  body.scrollTop = scroll;
  $("inspector").dataset.kind = ctx.kind;
}

/** The inspector drawn again unless a field of it is being typed in (then when it is left). */
export const refresh = () => render();

/** The inspector's own field: saved on Enter or when left, Esc puts it back as it was. */
export function field(input, save_) {
  const was = () => input.dataset.was ?? "";
  input.dataset.was = input.value;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      input.blur();
    } else if (e.key === "Escape") {
      input.value = was();
      e.preventDefault();
      e.stopPropagation();
      input.blur();
    }
  });
  input.addEventListener("change", () => {
    if (input.value !== was()) save_(input.value);
  });
  return input;
}

/** A section's row: a label, what it holds, and a note under it. */
export function row(label, control, note = null, { tip = null } = {}) {
  return el("div", { class: "insp-row", "data-tip": tip }, el("span", { class: "insp-label" }, label),
    el("div", { class: "insp-control" }, control, note));
}

export function setupInspector() {
  for (const e of ["selection", "spaces", "items", "access", "catalogue", "converting", "review", "project"]) {
    on(e, () => render());
  }
  // the floor read again: drawn again at once (under a field too) when what it shows changed
  on("floor", (d) => {
    const ctx = context();
    const id = ctx.space?.id || ctx.asset?.id || ctx.opening?.id;
    render({ force: Boolean(d?.refreshed && id && d.changedHere(id)) });
  });
  // a field left: what was put off is drawn now
  $("inspector-body").addEventListener("focusout", () => setTimeout(() => { if (pending) render(); }));
  render();
}
