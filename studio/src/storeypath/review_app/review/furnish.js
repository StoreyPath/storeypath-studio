// Furniture layouts, and items copied and pasted.
//
// A furniture layout is the items of a room kept to be placed again in any room of the
// project (review.py save_furnishing): right-click a furnished room, "Create a furniture
// layout from this room…", name it and check its preview. "Place a furniture layout
// here…" in another room: choose a layout and the corner of the room it lines up with;
// the preview draws it there, an item that would stand outside the room or across a wall
// in red and left out. An item of the preview is dragged to where it fits (R turns the one
// dragged last 90°, Shift+R back; Reset puts them all back as the layout has them); Place
// puts in those that fit, each an item of its own with its own ID, one group, one change
// (undone as one). Its items keep their real sizes and where they stood from the corner of
// the room's box, unless moved in the preview.
//
// An item chosen, or its group, is copied (⌘C, or Copy in the right-click menu) and pasted
// (⌘V at the pointer, or Paste here) on any floor of the project: new items of the same
// types, turned as they were, one group when they are more than one.

import { editable, roomTaken, viewOnly, whyNotEditable } from "./access.js";
import { request } from "./api.js";
import { emit, on } from "./bus.js";
import { command } from "./commands.js";
import { el, svg } from "./dom.js";
import { keyNow, keyText } from "./keys.js";
import { toast } from "./notify.js";
import { fits, itemBox, ringsOf, roomAt } from "../fit.js";
import { assetShape } from "../itemshape.js";
import { middleOf, took } from "./groups.js";
import { chosenAll, chosenAsset, roomName } from "./items.js";
import { BASE, round4, state, typeOf, view3d, visible } from "./state.js";
import { pointerPlace } from "./view3d.js";

export const CORNERS = [ // the corner of a room's box a layout lines up with, as the plan shows it
  ["nw", "Top left"], ["ne", "Top right"], ["sw", "Bottom left"], ["se", "Bottom right"],
];

let cursor = null; // the plan point under the pointer (pointer.js), where ⌘V pastes

/** The key of a command, as the map has it now ("" when none runs it here). */
const key = (id) => {
  const k = keyNow(id);
  return k ? keyText(k) : "";
};

// ---- rooms -------------------------------------------------------------------------------

/** The room at a plan point (a space; a zone's own space), as an item's room is found. */
export function roomUnder(p) {
  const hit = p ? roomAt(p, (state.floor?.spaces || []).filter(visible)) : null;
  if (!hit) return null;
  return hit.kind === "zone" ? state.byId.get(hit.space_id) || null : hit;
}

/** A room's box, [x0, y0, x1, y1]: round its outer rings. */
function boxOf(room) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const ring of ringsOf(room.geometry)) {
    for (const [x, y] of ring) [x0, y0, x1, y1] = [Math.min(x0, x), Math.min(y0, y), Math.max(x1, x), Math.max(y1, y)];
  }
  return [x0, y0, x1, y1];
}

/** The items standing in a room (not taken away), as Studio finds an item's room. */
export function itemsIn(room) {
  return (state.floor?.items || []).filter((a) => !a.retired && roomUnder([a.x, a.y])?.id === room.id);
}

const typeName = (code) => typeOf(code)?.name_en || code;

/** "2 × Desk, junior staff · 1 × Sofa": what items are, by type. */
function tally(list) {
  const n = new Map();
  for (const a of list) n.set(typeName(a.type), (n.get(typeName(a.type)) || 0) + 1);
  return [...n].map(([name, count]) => `${count} × ${name}`).join(" · ");
}

const metres = (v) => `${(Math.round(v * 10) / 10).toFixed(1)}`;

/** A piece of a layout to be placed, drawn (``i``: its place in the layout): green where it
 * fits, red where it does not; ``picked``, the one dragged last. */
function ghostOf(q, i, picked = false) {
  const g = assetShape(q, typeOf(q.type) || q, `asset-ghost${q.ok ? "" : " refused"}${q.known ? " movable" : ""}${picked ? " picked" : ""}`);
  g.dataset.i = i;
  return g;
}

/** Pieces with their places, in the order they are drawn (the last on top: the larger
 * under the smaller, as the plan draws items; the one dragged last over all). */
function order(pieces) {
  const area = (q) => { const t = typeOf(q.type) || q; return (t.width ?? 1) * (t.depth ?? 0.6); };
  return [...pieces.entries()].sort(([i, p], [k, q]) => (p.picked ? 1 : 0) - (q.picked ? 1 : 0) || area(q) - area(p) || i - k);
}

/** A room drawn with items in it (the preview of a layout): ``items`` as Studio has them,
 * ``ghosts`` [{type, x, y, rotation, ok}] those to be placed (one that does not fit, red). */
function drawing(room, items = [], ghosts = []) {
  const [x0, y0, x1, y1] = boxOf(room);
  const pad = Math.max(x1 - x0, y1 - y0) * 0.06 + 0.2;
  const s = svg("svg", { class: "furnish-plan", viewBox: `${x0 - pad} ${-(y1 + pad)} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`,
    preserveAspectRatio: "xMidYMid meet", role: "img", "aria-label": `${roomName(room)} with the furniture of the layout` });
  const g = svg("g", { transform: "scale(1 -1)" });
  const d = ringsOf(room.geometry).map((ring) => `M${ring.map(([x, y]) => `${x} ${y}`).join("L")}Z`).join("");
  g.append(svg("path", { class: "furnish-room", d, "fill-rule": "evenodd" }));
  for (const [, a] of order(items)) g.append(assetShape(a, typeOf(a.type) || a, `asset${ghosts.length ? " furnish-there" : ""}`));
  const coming = svg("g", { class: "preview" }); // (drawn as the plan draws an item being placed: canvas.css)
  for (const [i, a] of order(ghosts)) coming.append(ghostOf(a, i, a.picked));
  g.append(coming);
  s.append(g);
  return s;
}

/** A dialog of this module (one at a time), removed when it closes. */
function dialog(title, meta, ...body) {
  document.querySelector("dialog.furnish")?.remove();
  const d = el("dialog", { class: "furnish", "aria-labelledby": "furnish-title" },
    el("div", { class: "furnish-head" }, el("h2", { id: "furnish-title" }, title), el("span", { class: "meta" }, meta)),
    ...body);
  document.body.append(d);
  d.addEventListener("close", () => d.remove());
  d.showModal();
  return d;
}

// ---- the project's layouts ---------------------------------------------------------------

/** The project's furniture layouts, read again (state.furnishings). */
export async function loadFurnishings() {
  try {
    state.furnishings = await request(`${BASE}/furnishings`);
  } catch (e) {
    toast(`No furniture layouts: ${e.message}`, true);
  }
  emit("furnishings");
  return state.furnishings;
}

/** "Create a furniture layout from this room…": its items, named, previewed, saved. */
export function openCreateLayout(p) {
  const room = roomUnder(p);
  if (!room) return toast("Right-click inside a room to make a layout of its furniture", true);
  if (viewOnly()) return;
  const mine = itemsIn(room);
  const [x0, y0, x1, y1] = boxOf(room);
  const name = el("input", { id: "furnish-name", type: "text", maxlength: 80, value: roomName(room) === "this room" ? "" : roomName(room),
    placeholder: "e.g. Manager's office", autocomplete: "off" });
  const status = el("p", { class: "furnish-status", role: "status" },
    mine.length ? `${mine.length} item${mine.length === 1 ? "" : "s"}: ${tally(mine)}.` : "There is no furniture in this room to make a layout of.");
  const save = el("button", { type: "button", class: "primary", id: "furnish-save" }, "Save the layout");
  const cancel = el("button", { type: "button" }, "Cancel");
  const d = dialog("Create a furniture layout", `From ${roomName(room)} · ${metres(x1 - x0)} × ${metres(y1 - y0)} m`,
    el("p", { class: "meta" }, "Its items are kept as they stand from the room's corner, each with its real size: " +
      "place the layout in another room with a right-click there, Place a furniture layout here."),
    el("label", { class: "field" }, "Name", name), drawing(room, mine), status,
    el("div", { class: "row furnish-buttons" }, cancel, save));
  const ready = () => { save.disabled = !mine.length || !name.value.trim(); };
  ready();
  name.addEventListener("input", ready);
  name.addEventListener("keydown", (e) => { if (e.key === "Enter" && !save.disabled) save.click(); });
  cancel.addEventListener("click", () => d.close());
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const f = await request(`${BASE}/floors/${state.floor.id}/furnishings`, { room: room.id, name: name.value });
      state.furnishings = [...(state.furnishings || []).filter((x) => x.id !== f.id), f];
      emit("furnishings");
      d.close();
      toast(`Saved the furniture layout “${f.name}” (${f.count} items): right-click a room, Place a furniture layout here`);
    } catch (e) {
      status.textContent = `Not saved: ${e.message}`;
      status.classList.add("error");
      ready();
    }
  });
  name.focus();
  name.select();
  return d;
}

/** A piece where it would stand in a room ({type, x, y, rotation}), checked: ``known`` when
 * its type is in the catalogue, ``ok`` when it is and it fits (inside the room, across no
 * wall: fit.js). */
export function checkedPiece(q, room) {
  const t = typeOf(q.type);
  return { ...q, known: Boolean(t), ok: Boolean(t) && fits([q.x, q.y], q.rotation || 0, itemBox(t), ringsOf(room.geometry)) };
}

/** Where a layout's pieces stand in a room, lined up with its ``corner`` (CORNERS): each
 * {type, x, y, rotation, known, ok} (checkedPiece). ``moved``: pieces moved or turned in the
 * preview, by their place in the layout ({x, y, rotation}), where they are put instead. */
export function placedPieces(f, room, corner = "sw", moved = new Map()) {
  const [x0, y0, x1, y1] = boxOf(room);
  return f.pieces.map((p, i) => {
    const x = corner.endsWith("w") ? x0 + p.x : x1 - (f.width - p.x);
    const y = corner.startsWith("s") ? y0 + p.y : y1 - (f.depth - p.y);
    return checkedPiece({ type: p.type, x, y, rotation: p.rotation || 0, ...moved.get(i) }, room);
  });
}

/** "Place a furniture layout here…": a layout chosen, lined up with a corner of the room,
 * previewed, and what fits placed (one change). */
export async function openPlaceLayout(p) {
  const room = roomUnder(p);
  if (!room) return toast("Right-click inside a room to place a furniture layout in it", true);
  if (viewOnly() || roomTaken(room.id)) return;
  const all = await loadFurnishings();
  if (!all?.length) return toast("No furniture layouts yet: right-click a furnished room, Create a furniture layout from this room", true);
  let chosen = all[0].id, corner = "sw", pieces = [];
  let moved = new Map(), picked = null; // pieces moved or turned in the preview (placedPieces), the one dragged last
  const there = itemsIn(room);
  const [x0, y0, x1, y1] = boxOf(room);
  const list = el("ul", { class: "furnish-list", role: "radiogroup", "aria-label": "Furniture layouts" });
  const corners = el("div", { class: "furnish-corners", role: "radiogroup", "aria-label": "The corner it lines up with" });
  const picture = el("div", { class: "furnish-picture" });
  const status = el("p", { class: "furnish-status", role: "status" });
  const place = el("button", { type: "button", class: "primary", id: "furnish-place" }, "Place");
  const reset = el("button", { type: "button", id: "furnish-reset", title: "Every item back where the layout has it" }, "Reset");
  const hint = el("p", { class: "meta furnish-hint" }, "Drag an item to move it where it fits · R turns the one dragged last 90° (Shift+R back)");
  const remove = el("button", { type: "button", class: "danger", id: "furnish-delete" }, "Delete this layout");
  const cancel = el("button", { type: "button" }, "Cancel");
  const d = dialog("Place a furniture layout", `In ${roomName(room)} · ${metres(x1 - x0)} × ${metres(y1 - y0)} m`,
    el("div", { class: "furnish-body" },
      el("div", { class: "furnish-side" }, el("h3", {}, "Layout"), list, el("h3", {}, "Line it up with the room's"), corners),
      el("div", { class: "furnish-main" }, picture, hint, status)),
    el("div", { class: "row furnish-buttons" }, remove, el("span", { class: "grow" }), reset, cancel, place));
  const layout = () => (state.furnishings || []).find((f) => f.id === chosen) || null;

  const show = () => {
    const f = layout();
    list.replaceChildren(...(state.furnishings || []).map((x) => {
      const b = el("button", { type: "button", role: "radio", class: "furnish-choice", "aria-checked": String(x.id === chosen), "data-id": x.id },
        el("span", { class: "furnish-name" }, x.name),
        el("span", { class: "meta" }, `${x.count} item${x.count === 1 ? "" : "s"} · ${metres(x.width)} × ${metres(x.depth)} m${x.room ? ` · from ${x.room}` : ""}`));
      b.addEventListener("click", () => { if (chosen !== x.id) [moved, picked] = [new Map(), null]; chosen = x.id; show(); });
      return el("li", {}, b);
    }));
    corners.replaceChildren(...CORNERS.map(([code, label]) => {
      const b = el("button", { type: "button", role: "radio", class: `furnish-corner ${code}`, "aria-checked": String(code === corner), "data-corner": code }, label);
      b.addEventListener("click", () => { if (corner !== code) [moved, picked] = [new Map(), null]; corner = code; show(); });
      return b;
    }));
    if (!f) return;
    pieces = placedPieces(f, room, corner, moved).map((q, i) => ({ ...q, picked: i === picked }));
    picture.replaceChildren(drawing(room, there, pieces));
    say();
  };
  /** What the preview shows, said, and Place and Reset as it allows. */
  const say = () => {
    const fit = pieces.filter((q) => q.ok).length, unknown = pieces.filter((q) => !q.known).length;
    const out = pieces.length - fit - unknown;
    const said = [fit === pieces.length ? `All ${fit} items fit.` : `${fit} of ${pieces.length} items fit.`];
    if (out) said.push(`${out} would stand outside the room or across a wall: drawn red, left out (drag ${out === 1 ? "it" : "them"} in to place ${out === 1 ? "it" : "them"}).`);
    if (moved.size) said.push(`${moved.size} moved here.`);
    if (unknown) said.push(`${unknown} ${unknown === 1 ? "is of a type" : "are of types"} no longer in the catalogue: left out.`);
    if (there.length) said.push(`The room has ${there.length} item${there.length === 1 ? "" : "s"} already: the layout goes beside them.`);
    status.textContent = said.join(" ");
    status.classList.toggle("error", !fit);
    place.disabled = !fit;
    place.textContent = fit && fit < pieces.length ? `Place ${fit} item${fit === 1 ? "" : "s"}` : "Place";
    reset.disabled = !moved.size;
  };
  show();

  // an item of the preview dragged where it fits, checked as it goes; R turns the one dragged last
  const planAt = (e) => { // the plan point under the pointer (the drawing's frame: its y up)
    const g = picture.querySelector(".furnish-plan > g");
    return new DOMPoint(e.clientX, e.clientY).matrixTransform(g.getScreenCTM().inverse());
  };
  /** The preview's pieces drawn again, piece ``picked`` marked. */
  const redraw = () => {
    pieces = pieces.map((q, k) => ({ ...q, picked: k === picked }));
    picture.querySelector(".furnish-plan .preview").replaceChildren(...order(pieces).map(([k, q]) => ghostOf(q, k, q.picked)));
    say();
  };
  /** Piece ``i`` put at {x, y, rotation} (some of them), checked there, drawn again. */
  const putPiece = (i, at) => {
    pieces[i] = checkedPiece({ ...pieces[i], ...at }, room);
    moved.set(i, { x: pieces[i].x, y: pieces[i].y, rotation: pieces[i].rotation });
    picked = i;
    redraw();
  };
  let drag = null;
  picture.addEventListener("pointerdown", (e) => {
    const g = e.target.closest?.(".preview .asset-ghost.movable");
    if (!g || e.button !== 0) return;
    e.preventDefault();
    const i = Number(g.dataset.i), p = planAt(e);
    picked = i;
    drag = { i, from: [p.x, p.y], at: [pieces[i].x, pieces[i].y] };
    picture.setPointerCapture(e.pointerId);
    redraw();
  });
  picture.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const p = planAt(e);
    putPiece(drag.i, { x: drag.at[0] + p.x - drag.from[0], y: drag.at[1] + p.y - drag.from[1] });
  });
  const drop = () => { drag = null; };
  picture.addEventListener("pointerup", drop);
  picture.addEventListener("pointercancel", drop);
  d.addEventListener("keydown", (e) => {
    if (e.key.toLowerCase() !== "r" || e.metaKey || e.ctrlKey || e.altKey || picked === null || e.target.closest("input")) return;
    e.preventDefault();
    putPiece(picked, { rotation: (((pieces[picked].rotation || 0) + (e.shiftKey ? -90 : 90)) % 360 + 360) % 360 });
  });
  reset.addEventListener("click", () => { [moved, picked] = [new Map(), null]; show(); });
  cancel.addEventListener("click", () => d.close());
  remove.addEventListener("click", async () => {
    const f = layout();
    if (!f || !confirm(`Delete the furniture layout “${f.name}”? What was placed of it stays as it is.`)) return;
    try {
      await request(`${BASE}/furnishings/${encodeURIComponent(f.id)}/delete`, {});
      await loadFurnishings();
      toast(`Deleted the furniture layout “${f.name}”`);
      if (!state.furnishings.length) return d.close();
      chosen = state.furnishings[0].id;
      show();
    } catch (e) {
      status.textContent = `Not deleted: ${e.message}`;
      status.classList.add("error");
    }
  });
  place.addEventListener("click", async () => {
    const f = layout(), fit = pieces.filter((q) => q.ok);
    if (!f || !fit.length) return;
    place.disabled = true;
    const got = await placeItems(fit, f.name);
    if (!got) return show();
    d.close();
    const left = pieces.length - fit.length;
    toast(`Placed “${f.name}”: ${fit.length} item${fit.length === 1 ? "" : "s"}${left ? `, ${left} left out` : ""}`);
  });
  return d;
}

/** Items placed in one go (``pieces``: [{type, x, y, rotation}]): one change, one group when
 * two or more; chosen. Studio's answer, or null (said why). */
async function placeItems(pieces, label) {
  if (viewOnly()) return null;
  try {
    const got = await request(`${BASE}/floors/${state.floor.id}/pieces`, {
      items: pieces.map((q) => ({ type: q.type, x: round4(q.x), y: round4(q.y), rotation: round4(q.rotation || 0) })),
      group: pieces.length > 1, label });
    took(got.items, { select: got.items.map((a) => a.id) });
    return got;
  } catch (e) {
    toast(`Not placed: ${e.message}`, true);
    return null;
  }
}

// ---- copy and paste ----------------------------------------------------------------------

/** Items copied (an item, or its group): their types, turns and where each stands from
 * their middle, kept to paste (state.clipboard) in this project. */
export function copyItems(list) {
  list = (list || []).filter((a) => a && !a.retired);
  if (!list.length) return toast("Choose an item to copy", true);
  const [mx, my] = middleOf(list);
  const label = list.length === 1 ? typeName(list[0].type) : `${list.length} items`;
  state.clipboard = { project: BASE, label, pieces: list.map((a) => ({ type: a.type, x: a.x - mx, y: a.y - my, rotation: a.rotation || 0 })) };
  emit("clipboard");
  const k = key("edit.paste");
  toast(`Copied ${label}: right-click where it goes, Paste here${k ? `, or press ${k} there` : ""}`);
}

/** What was copied pasted with its middle at ``p`` (plan metres): held in the room there,
 * every item inside it and across no wall, unless ``free`` (Alt). */
export async function pasteAt(p, free = state.alt) {
  const c = state.clipboard;
  if (!c || c.project !== BASE) return toast("Nothing copied: right-click an item, Copy", true);
  if (!p) return toast("Point at where it goes on the plan, then paste", true);
  if (viewOnly()) return null;
  const room = roomUnder(p);
  if (room && roomTaken(room.id)) return null;
  const pieces = c.pieces.map((q) => ({ type: q.type, x: p[0] + q.x, y: p[1] + q.y, rotation: q.rotation }));
  if (pieces.some((q) => !typeOf(q.type))) return toast("What was copied is of a type no longer in the catalogue", true);
  if (!free) {
    if (!room) return toast("Paste it in a room. To put it anywhere, hold Alt as you paste.", true);
    const rings = ringsOf(room.geometry);
    const out = pieces.filter((q) => !fits([q.x, q.y], q.rotation, itemBox(typeOf(q.type)), rings)).length;
    if (out) {
      return toast(`It does not fit in ${roomName(room)} there${pieces.length > 1 ? ` (${out} of ${pieces.length} would stand outside it or across a wall)` : ""}. ` +
        "To paste it anyway, hold Alt as you paste.", true);
    }
  }
  return placeItems(pieces, c.label);
}

/** Where ⌘V pastes: the point under the pointer, on the plan or in 3D. */
const pasteHere = () => (view3d.shown ? pointerPlace() : cursor);

export function setupFurnish() {
  on("cursor", (p) => { cursor = p; });
  on("floor", () => { cursor = null; });
  command({ id: "item.copy", title: "Copy the item chosen (or its group)", group: "Item", scope: "item", keys: ["mod+c"], palette: false,
    icon: "copy", when: () => Boolean(chosenAsset() || chosenAll().length), why: () => "Choose an item first",
    run: () => copyItems(chosenAll().length ? chosenAll() : [chosenAsset()]) });
  command({ id: "edit.paste", title: "Paste what was copied, where the pointer is", group: "Edit", icon: "corner-down-left", keys: ["mod+v"],
    words: "paste clipboard furniture items", when: () => Boolean(state.floor) && editable(), why: whyNotEditable,
    run: () => pasteAt(pasteHere()) }); // (nothing copied: it says so)
}
