// Items: furniture and equipment. Desks by grade, central photocopiers, access points,
// sofas, TVs… (the Studio's catalogue of item types). Each has an ID of its own that stays
// with it wherever it is carried; it is saved at once, with no reading of the drawing
// again. An item stays in the room it was placed in and lines up with walls and items
// (fit.js); Alt places or drags it freely.
//
// The Place tool (I): choose a type in its options, click where it goes (in 3D and
// walking, on the floor under the pointer: its ghost follows it). An item chosen, in every
// view and whatever the tool: R turns it 90° (Shift: back), , and . by 15°, Del deletes it;
// the arrows move it (Shift: further), but walking, where they walk (drag it instead).

import { editable, viewOnly, whyNotEditable } from "./access.js";
import { request } from "./api.js";
import { emit } from "./bus.js";
import { command } from "./commands.js";
import { $, el, icon, svg } from "./dom.js";
import { openFloor } from "./floor.js";
import { toast } from "./notify.js";
import { centerOn, floorBounds, scaleFor } from "./plan.js";
import { DESK_SETS, fits, inRings, itemBox, ringsOf, roomAt, settle, tableChairs, visitorChairs } from "../fit.js";
import { selectAsset } from "./selection.js";
import { BASE, round4, state, typeOf, visible, view3d } from "./state.js";
import { setTool, tool } from "./tools.js";
import { aimSoon, update3d } from "./view3d.js";

export async function loadCatalogue() {
  try {
    state.catalogue = (await request("catalogue")).types.filter((t) => !t.retired);
  } catch (e) {
    toast(`No item types: ${e.message}`, true);
  }
  emit("catalogue");
}

/** The catalogue's types by category: [[category, [types]]], in its order. */
export function categories() {
  const groups = new Map();
  for (const t of state.catalogue) {
    if (!groups.has(t.category)) groups.set(t.category, []);
    groups.get(t.category).push(t);
  }
  return [...groups];
}

/** A desk's chair and what goes with its grade, in its own frame: its user towards -y. */
function deskSet(g, t, w, d) {
  const set = DESK_SETS[t?.grade] || DESK_SETS.junior;
  // [x, y] from its middle towards its user, as the viewers measure it
  const rect = (x, y, rw, rh, attrs) => g.append(svg("rect", { x, y: -(y + rh), width: rw, height: rh, ...attrs }));
  const color = t?.color || "#8a8a8a";
  if (set.return) rect(w / 2 - Math.min(0.45, w / 3), d / 2, Math.min(0.45, w / 3), 0.8, { fill: color });
  if (set.cabinet) rect(-w * 0.45, d / 2 + 0.95, w * 0.9, 0.45, { fill: color });
  const chair = (x, y, cw, cd, back) => { // its back at y + cd
    rect(x - cw / 2, y, cw, cd, { class: "chair", rx: 0.08 });
    rect(x - cw / 2, y + cd - back, cw, back, { class: "chair back", rx: 0.04 });
  };
  if (set.executive) chair(0, d / 2 + 0.08, 0.6, 0.62, 0.14);
  else rect(-0.22, d / 2 + 0.1, 0.44, 0.42, { class: "chair", rx: 0.1 });
  const { xs, vw, vd } = visitorChairs(set, w);
  for (const x of xs) { // facing its user: their backs away from it
    rect(x - vw / 2, -d / 2 - 0.15 - vd, vw, vd, { class: "chair", rx: 0.08 });
    rect(x - vw / 2, -d / 2 - 0.15 - vd, vw, 0.12, { class: "chair back", rx: 0.04 });
  }
}

/** A meeting table's chairs round it (tableChairs), their backs away from it. */
function tableSet(g, w, d) {
  const { at, board, cw, cd, gap } = tableChairs(w, d), back = board ? 0.14 : 0.1;
  for (const [x, y, [ox, oy]] of at) {
    // from ``near`` to ``far`` metres out from the edge, ``cw`` along it: the chair, its back
    const part = (near, far, attrs) => {
      const [x0, x1] = ox ? [x + ox * (gap + near), x + ox * (gap + far)].sort((p, q) => p - q) : [x - cw / 2, x + cw / 2];
      const [y0, y1] = oy ? [y + oy * (gap + near), y + oy * (gap + far)].sort((p, q) => p - q) : [y - cw / 2, y + cw / 2];
      g.append(svg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, ...attrs }));
    };
    part(0, cd, { class: "chair", rx: board ? 0.08 : 0.1 });
    part(cd - back, cd, { class: "chair back", rx: 0.04 });
  }
}

/** An item's shape on the plan: its footprint turned with it, the edge it faces
 * darker, a desk's chair and what goes with its grade, a meeting table's chairs; on
 * the ceiling, a circle. */
export function assetShape(a, t, cls) {
  const g = svg("g", { transform: `translate(${a.x} ${a.y}) rotate(${a.rotation || 0})`, class: cls });
  const w = t?.width ?? 1, d = t?.depth ?? 0.6;
  if (t?.mount === "ceiling") {
    g.classList.add("ceiling");
    g.append(svg("circle", { r: Math.max(w, d) / 2, fill: t?.color || "#8a8a8a" }));
  } else {
    g.append(svg("rect", { x: -w / 2, y: -d / 2, width: w, height: d, fill: t?.color || "#8a8a8a" }));
    g.append(svg("line", { x1: -w / 2, y1: -d / 2, x2: w / 2, y2: -d / 2, class: "front" })); // its front: the plan's -y, turned
    const kind = (t?.code || t?.type || "").split("-")[0];
    if (kind === "DESK") deskSet(g, t, w, d);
    else if (kind === "MEETING") tableSet(g, w, d);
  }
  return g;
}

export function renderAssets() {
  const layer = $("assets");
  layer.replaceChildren();
  for (const a of state.floor?.items || []) {
    if (a.retired && !state.showHidden) continue;
    const g = assetShape(a, typeOf(a.type) || a, "asset");
    g.dataset.asset = a.id;
    g.classList.toggle("chosen", a.id === state.asset);
    g.classList.toggle("retired", a.retired);
    g.append(svg("title", {}));
    g.lastChild.textContent = `${typeOf(a.type)?.name_en || a.type} · ${a.id}`;
    layer.append(g);
  }
}

export function assetOf(target) {
  const id = target?.closest?.("[data-asset]")?.dataset.asset;
  return id ? (state.floor?.items || []).find((a) => a.id === id) || null : null;
}

/** The item under a plan point (the one on top), shown ones only. */
export function assetAt(p) {
  const shown = (state.floor?.items || []).filter((a) => !a.retired || state.showHidden);
  for (const a of shown.slice().reverse()) {
    const t = typeOf(a.type) || a;
    const r = (-(a.rotation || 0) * Math.PI) / 180, dx = p[0] - a.x, dy = p[1] - a.y;
    const u = dx * Math.cos(r) - dy * Math.sin(r), v = dx * Math.sin(r) + dy * Math.cos(r);
    const reach = 4 / state.view.k;
    if (Math.abs(u) <= (t.width ?? 1) / 2 + reach && Math.abs(v) <= (t.depth ?? 0.6) / 2 + reach) return a;
  }
  return null;
}

/** What holds an item at ``at`` (of ``type``): the room it is in (a space; a zone's own
 * space), its rings, and the other items there mounted as it is (desks line up with
 * desks, not with the TV over them), for the magnet (fit.js). ``held``: whether the room
 * holds it (not when it stands across a wall already: it is held once it is in). */
export function placing(at, type, exceptId = null, rot = 0, reach = Math.max(0.15, 12 / state.view.k)) {
  const t = typeOf(type) || { code: type };
  const room = roomAt(at, (state.floor?.spaces || []).filter(visible));
  const rings = room ? ringsOf(room.geometry) : [];
  const mount = t.mount || "floor";
  const others = (state.floor?.items || [])
    .filter((o) => o.id !== exceptId && !o.retired && ((typeOf(o.type)?.mount || "floor") === mount) && (!room || inRings([o.x, o.y], rings)))
    .map((o) => ({ at: [o.x, o.y], rot: o.rotation || 0, box: itemBox(typeOf(o.type) || { code: o.type }) }));
  const box = itemBox(t);
  const held = !exceptId || !rings.length || fits(at, rot, box, rings);
  return { room, rings: held ? rings : [], others, box, mount, reach };
}

export const roomName = (room) => (room ? [room.name, room.number].filter(Boolean).join(" ") || "this room" : "this room");

/** The magnet's guides: what the item was drawn to, while it is placed or dragged. */
export function drawGuides(lines = []) {
  $("guides").replaceChildren(...lines.map(([a, b]) => svg("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1] })));
}

export async function placeAsset(type, p, free = state.alt, reach = undefined) {
  if (!type || viewOnly()) return;
  const ctx = placing(p, type, null, 0, reach);
  const got = settle({ want: { at: p, rot: 0 }, last: null, ...ctx, free });
  drawGuides();
  if (!got) {
    toast(`It does not fit in ${roomName(ctx.room)}. To put it anywhere, hold Alt as you click.`, true);
    return;
  }
  try {
    const a = await request(`${BASE}/floors/${state.floor.id}/items`,
      { type, x: round4(got.at[0]), y: round4(got.at[1]), rotation: round4(got.rot) });
    state.floor.items.push(a);
    renderAssets();
    update3d(); // in 3D at once: this floor's items drawn again, nothing else
    emit("items");
  } catch (e) {
    toast(`Not placed: ${e.message}`, true);
  }
}

export async function changeAsset(a, body) {
  if (viewOnly()) return;
  try {
    const got = await request(`${BASE}/items/${a.id}`, body);
    const list = state.floor.items;
    const i = list.findIndex((x) => x.id === a.id);
    if (got.floor_id && got.floor_id !== state.floor.id) {
      list.splice(i, 1);
      selectAsset(null);
      view3d.dirty.add(got.floor_id); // there, in 3D, when that floor is shown
      toast("Carried to the other floor: it keeps its ID");
    } else if (i >= 0) list[i] = { ...got, floor_id: state.floor.id };
    renderAssets();
    update3d();
    emit("items", [a.id]);
  } catch (e) {
    toast(`Not saved: ${e.message}`, true);
    await openFloor(state.floor.id, null, { keepView: true }); // as it is saved
  }
}

/** The item chosen (or null). */
export const chosenAsset = () => (state.floor?.items || []).find((x) => x.id === state.asset) || null;

/** The chosen item moved by ``dx``, ``dy`` metres, held in its room (the magnet off). */
export function nudgeAsset(a, dx, dy) {
  if (viewOnly()) return;
  const want = { at: [a.x + dx, a.y + dy], rot: a.rotation || 0 };
  const ctx = placing([a.x, a.y], a.type, a.id, a.rotation || 0);
  const got = settle({ want, last: { at: [a.x, a.y], rot: want.rot }, ...ctx, magnet: false });
  if (got && Math.hypot(got.at[0] - a.x, got.at[1] - a.y) > 1e-4) changeAsset(a, { x: round4(got.at[0]), y: round4(got.at[1]) });
  else toast(`Against the wall of ${roomName(ctx.room)} (drag it with Alt held to take it further)`);
}

/** An item turned where it stands, kept in its room (moved the least it takes, a
 * metre at most). */
export function turnAsset(a, rot) {
  if (viewOnly()) return;
  rot = ((rot % 360) + 360) % 360;
  const ctx = placing([a.x, a.y], a.type, a.id, a.rotation || 0);
  const got = settle({ want: { at: [a.x, a.y], rot }, last: { at: [a.x, a.y], rot: a.rotation || 0 }, ...ctx, magnet: false });
  if (!got) return toast(`No room to turn it there in ${roomName(ctx.room)}: drag it (Alt: freely) where there is`, true);
  const moved = Math.hypot(got.at[0] - a.x, got.at[1] - a.y) > 1e-4 ? { x: round4(got.at[0]), y: round4(got.at[1]) } : {};
  changeAsset(a, { rotation: round4(rot), ...moved });
}

/** An item by its ID (as typed: ``tag`` normalised): chosen, on its floor (that floor
 * opened when it is another), and brought into view. Whether it was found; ``still()``
 * says whether it is still wanted (the person typed on). */
export async function findAsset(tag, still = () => true) {
  let a = (state.floor?.items || []).find((x) => x.id === tag);
  if (!a) {
    let found;
    try {
      found = await request(`${BASE}/items/${encodeURIComponent(tag)}`);
    } catch (e) {
      return { error: /no item/i.test(e.message) ? `No item ${tag} on a floor of this project you may see` : e.message };
    }
    if (!still()) return { stale: true };
    if (found.floor_id !== state.floor?.id) await openFloor(found.floor_id);
    a = (state.floor?.items || []).find((x) => x.id === found.id);
    if (!a) return { error: `Item ${tag} not found` };
  }
  if (a.retired && !state.showHidden) toast(`${a.id} is deleted: Show deleted to see it`);
  selectAsset(a.id);
  showAsset(a);
  return { found: a };
}

/** An item brought into view on the plan. */
export function showAsset(a) {
  const floor = floorBounds();
  if (floor) centerOn([a.x, a.y, a.x, a.y], Math.max(scaleFor(floor), scaleFor([a.x - 4, a.y - 4, a.x + 4, a.y + 4], 20)));
}

export async function copyId(id) {
  try {
    await navigator.clipboard.writeText(id);
    toast(`${id} copied`);
  } catch {
    toast("Could not copy; select the ID and copy it", true);
  }
}

// ---- the Place tool ----------------------------------------------------------------------

let typesOpen = null; // the type picker, while it is open

function closeTypes() {
  typesOpen?.remove();
  typesOpen = null;
}

/** The types to place, by category, in a popover under ``anchor``; ``pick(code)``. */
export function typePicker(anchor, pick) {
  closeTypes();
  const tile = (t) => {
    const b = el("button", { type: "button", class: `type-tile${state.placeType === t.code ? " chosen" : ""}`, role: "option",
      "aria-selected": String(state.placeType === t.code), "data-tip": `${t.name_en}${t.name_ar ? ` · ${t.name_ar}` : ""} · ${t.width} × ${t.depth} m${t.mount && t.mount !== "floor" ? ` · on the ${t.mount}` : ""}` },
    el("span", { class: "swatch", style: `background:${t.color || "#8a8a8a"}` }), el("span", { class: "type-name" }, t.name_en));
    b.addEventListener("click", () => {
      closeTypes();
      pick(t.code);
    });
    return b;
  };
  const box = el("div", { class: "popover type-picker", role: "listbox", "aria-label": "What to place" },
    ...categories().map(([category, types]) => [el("div", { class: "menu-section" }, category[0].toUpperCase() + category.slice(1)),
      el("div", { class: "type-grid" }, ...types.map(tile))]));
  box.addEventListener("keydown", (e) => {
    const tiles = [...box.querySelectorAll(".type-tile")];
    const i = tiles.indexOf(document.activeElement);
    const step = { ArrowRight: 1, ArrowDown: 2, ArrowLeft: -1, ArrowUp: -2 }[e.key];
    if (step) {
      tiles[Math.max(0, Math.min(tiles.length - 1, i + step))]?.focus();
      e.preventDefault();
    } else if (e.key === "Escape") {
      closeTypes();
      anchor.focus();
      e.preventDefault();
      e.stopPropagation();
    }
  });
  document.body.append(box);
  const r = anchor.getBoundingClientRect();
  box.style.left = `${Math.max(8, Math.min(r.left, innerWidth - box.offsetWidth - 8))}px`;
  box.style.top = `${Math.min(r.bottom + 6, innerHeight - box.offsetHeight - 8)}px`;
  typesOpen = box;
  (box.querySelector(".type-tile.chosen") || box.querySelector(".type-tile"))?.focus();
}

/** What is placed: a type chosen (the Place tool taken when it is not in use). */
export function placeType(code) {
  state.placeType = code;
  if (state.tool !== "place") setTool("place");
  emit("tool-options");
  emit("tool-progress");
  if (view3d.shown) aimSoon();
}

function placeOptions() {
  const t = typeOf(state.placeType);
  const chooser = el("button", { type: "button", class: "type-chooser", "aria-haspopup": "listbox", "data-tip": "What to place" },
    t ? el("span", { class: "swatch", style: `background:${t.color || "#8a8a8a"}` }) : null,
    el("span", {}, t ? t.name_en : "Choose what to place…"), icon("chevron-down", { size: 14 }));
  chooser.addEventListener("click", () => (typesOpen ? closeTypes() : typePicker(chooser, placeType)));
  return [chooser, t ? el("span", { class: "to-note" }, `${t.width} × ${t.depth} m · Alt: anywhere, as it is`) : null];
}

/** The item chosen, its keys (the "item" scope: while one is chosen, whatever the tool or
 * the view: what is chosen wins). */
function itemCommand(id, title, keys, fn, repeat = false) {
  command({ id, title, group: "Item", scope: "item", keys, palette: false, repeat,
    when: () => Boolean(chosenAsset()) && editable(), why: () => (chosenAsset() ? whyNotEditable() : "Choose an item first"),
    run: () => fn(chosenAsset()) });
}

export function setupItems() {
  tool({
    id: "place", label: "Place an item", icon: "armchair", key: "i", group: "place", views: ["2d", "3d", "walk"], edits: true,
    words: "furniture equipment desk asset item add",
    hint: (view) => {
      const t = typeOf(state.placeType);
      if (!t) return "Choose what to place in the options above";
      return view === "walk" ? `Click on the floor where the ${t.name_en} goes (its ghost follows the pointer) · Alt-click: as it is · drag to look`
        : view === "3d" ? `Click on the floor where the ${t.name_en} goes · it lines up as on the plan · Alt-click: as it is`
          : `Click in a room where the ${t.name_en} goes · it lines up with walls and items · Alt-click: anywhere, as it is`;
    },
    options: placeOptions,
    start: () => {
      if (!state.placeType) requestAnimationFrame(() => document.querySelector(".type-chooser")?.click());
    },
    stop: () => {
      closeTypes();
      drawGuides();
      for (const id of ["preview", "preview-print"]) $(id).replaceChildren();
      view3d.world?.ghost(null);
    },
    plan: {
      hover: (p) => {
        const t = typeOf(state.placeType);
        if (!t || state.busy) return;
        const got = settle({ want: { at: p, rot: 0 }, last: null, ...placing(p, t.code), free: state.alt });
        drawGuides(got?.guides);
        const ghost = () => assetShape({ x: (got?.at ?? p)[0], y: (got?.at ?? p)[1], rotation: got?.rot ?? 0 }, t,
          got ? "asset-ghost" : "asset-ghost refused");
        for (const id of ["preview", "preview-print"]) $(id).replaceChildren(ghost());
      },
      click: (p, e) => {
        if (!state.placeType) return toast("Choose what to place first", true);
        placeAsset(state.placeType, p, e.altKey);
      },
    },
  });
  itemCommand("item.turn", "Turn the item 90°", ["r"], (a) => turnAsset(a, a.rotation + 90));
  itemCommand("item.turn-back", "Turn the item back 90°", ["shift+r"], (a) => turnAsset(a, a.rotation + 270));
  itemCommand("item.turn-left", "Turn the item 15° left", [","], (a) => turnAsset(a, a.rotation + 15), true);
  itemCommand("item.turn-right", "Turn the item 15° right", ["."], (a) => turnAsset(a, a.rotation + 345), true);
  for (const [key, dx, dy] of [["arrowleft", -1, 0], ["arrowright", 1, 0], ["arrowup", 0, 1], ["arrowdown", 0, -1]]) {
    itemCommand(`item.move-${key.slice(5)}`, `Move the item ${key.slice(5)}`, [key], (a) => nudgeAsset(a, dx * 0.1, dy * 0.1), true);
    itemCommand(`item.move-${key.slice(5)}-far`, `Move the item ${key.slice(5)} 1 m`, [`shift+${key}`], (a) => nudgeAsset(a, dx, dy), true);
  }
  document.addEventListener("pointerdown", (e) => {
    if (typesOpen && !e.target.closest?.(".type-picker, .type-chooser")) closeTypes();
  }, true);
}
