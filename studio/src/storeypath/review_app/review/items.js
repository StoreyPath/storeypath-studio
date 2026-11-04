// Items: furniture and equipment. Desks by grade, central photocopiers, access points,
// sofas, TVs… (the Studio's catalogue of item types). Each has an ID of its own that stays
// with it wherever it is carried; it is saved at once, with no reading of the drawing
// again. An item stays in the room it was placed in and lines up with walls and items
// (fit.js); Alt places or drags it freely.
//
// The Place tool (I): choose a type, or a set (groups.js: a workstation, a meeting table and
// its chairs, each piece an item of its own), in its options, click where it goes (in 3D
// and walking, on the floor under the pointer: its ghost follows it). An item chosen, or
// items chosen together (a group), in every view and whatever the tool: R turns it 90°
// (Shift: back), , and . by 15°, Shift+H and Shift+V mirror it (left to right, top to
// bottom: groups.js), Del deletes it; the arrows move it (Shift: further), but
// walking, where they walk (drag it instead); ⌘G groups items chosen, ⌘⇧G lets them go.

import { editable, roomTaken, viewOnly, whyNotEditable } from "./access.js";
import { request } from "./api.js";
import { emit } from "./bus.js";
import { command } from "./commands.js";
import { $, el, icon, svg } from "./dom.js";
import { openFloor } from "./floor.js";
import { toast } from "./notify.js";
import { openInsert } from "./palette.js";
import { centerOn, floorBounds, scaleFor } from "./plan.js";
import { fits, inRings, itemBox, ringsOf, roomAt, settle } from "../fit.js";
import { assetShape } from "../itemshape.js";
import { groupAssets, mirrorAssets, mirrored, moveAssets, placeSet, setBox, setGhost, setOf, turnAssets, ungroupAssets } from "./groups.js";
import { selectAsset } from "./selection.js";
import { BASE, round4, state, typeOf, visible, view3d } from "./state.js";
import { setTool, tool } from "./tools.js";
import { aimSoon, update3d } from "./view3d.js";

export async function loadCatalogue() {
  try {
    const got = await request("catalogue");
    state.catalogue = got.types.filter((t) => !t.retired);
    state.sets = got.sets || []; // what is placed together (catalogue.py default_sets)
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

/** Items in the order the plan draws them, the last on top: the larger under the smaller (a
 * drawer pedestal over the desk it stands under, to be clicked), those chosen over the rest;
 * as the floor lists them otherwise. A click takes the one on top (assetAt). */
export function drawOrder(list) {
  const area = (a) => { const t = typeOf(a.type) || a; return (t.width ?? 1) * (t.depth ?? 0.6); };
  const chosen = (a) => (state.assets.includes(a.id) ? 1 : 0);
  return list.map((a, i) => ({ a, i })).sort((p, q) => chosen(p.a) - chosen(q.a) || area(q.a) - area(p.a) || p.i - q.i).map((x) => x.a);
}

export function renderAssets() {
  const layer = $("assets");
  layer.replaceChildren();
  for (const a of drawOrder(state.floor?.items || [])) {
    if (a.retired && !state.showHidden) continue;
    const chosen = state.assets.includes(a.id);
    const g = assetShape(a, typeOf(a.type) || a, "asset", { coverage: chosen || state.showCoverage });
    g.dataset.asset = a.id;
    g.classList.toggle("chosen", chosen);
    g.classList.toggle("retired", a.retired);
    g.append(svg("title", {}));
    g.lastChild.textContent = `${typeOf(a.type)?.name_en || a.type} · ${a.id}`;
    layer.append(g);
  }
  // the door an item chosen serves (a card reader's), or the items that serve the door chosen:
  // a line from each to its door, and a ring round the door
  const door = state.item?.kind === "door" ? state.item.id : null;
  for (const a of state.floor?.items || []) {
    if (!a.door_id || a.retired || !(state.assets.includes(a.id) || a.door_id === door)) continue;
    const d = (state.floor.doors || []).find((x) => x.id === a.door_id);
    if (!d) continue;
    const [x, y] = d.middle || d.point;
    layer.append(svg("line", { class: "door-link", x1: a.x, y1: a.y, x2: x, y2: y }),
      svg("circle", { class: "door-link", cx: x, cy: y, r: Math.max(0.3, (d.width || 0.9) / 2 + 0.1) }));
  }
}

/** The door an item serves (a card reader's: format 0.10), as the floor has it; null: none, or
 * one deleted since. */
export const doorOf = (a) => (a?.door_id && (state.floor?.doors || []).find((d) => d.id === a.door_id && !d.ignored)) || null;

export function assetOf(target) {
  const id = target?.closest?.("[data-asset]")?.dataset.asset;
  return id ? (state.floor?.items || []).find((a) => a.id === id) || null : null;
}

// A click this near an item (screen pixels) is on it: a small one (a camera on a site plan)
// is only a few pixels across.
const ITEM_REACH_PX = 6;

/** The item under a plan point (the one on top), or within ITEM_REACH_PX of its footprint;
 * shown ones only. */
export function assetAt(p) {
  const shown = (state.floor?.items || []).filter((a) => !a.retired || state.showHidden);
  for (const a of drawOrder(shown).reverse()) {
    const t = typeOf(a.type) || a;
    const r = (-(a.rotation || 0) * Math.PI) / 180, dx = p[0] - a.x, dy = p[1] - a.y;
    const u = dx * Math.cos(r) - dy * Math.sin(r), v = dx * Math.sin(r) + dy * Math.cos(r);
    const reach = ITEM_REACH_PX / state.view.k;
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

/** Whether items are all one group (and none is in no group). */
export const sameGroup = (list) => list.length > 0 && Boolean(list[0].group) && list.every((a) => a.group === list[0].group);

export const roomName = (room) => (room ? [room.name, room.number].filter(Boolean).join(" ") || "this room" : "this room");

/** The room an item stands in (its ID), as it is placed; the item itself when it stands in
 * none: whose it is to change now (Studio's room locks). */
export const itemRoom = (a) => roomAt([a.x, a.y], (state.floor?.spaces || []).filter(visible))?.id || a.id;

/** True (and says so) when someone else is editing the room an item stands in. */
export const itemTaken = (a) => roomTaken(itemRoom(a));

/** The magnet's guides: what the item was drawn to, while it is placed or dragged. */
export function drawGuides(lines = []) {
  $("guides").replaceChildren(...lines.map(([a, b]) => svg("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1] })));
}

/** What the box to find what to place chose, placed at ``p``: a type's item, or a set
 * ({set: code}): its pieces, each an item of its own, one group. */
export function placeAsset(choice, p, free = state.alt, reach = undefined) {
  return choice?.set ? placeSetAt(choice.set, p, free, reach) : placeItem(choice, p, free, reach);
}

/** Where a set would go at ``p``: its pieces' box held in the room and drawn to its walls
 * and items (fit.js), as an item's is; null where it does not fit. */
export function settleSet(set, p, free = state.alt, reach = undefined, rot = 0) {
  const ctx = { ...placing(p, set.pieces[0].type, null, rot, reach), box: setBox(set) };
  return { ctx, got: settle({ want: { at: p, rot }, last: null, ...ctx, free }) };
}

async function placeSetAt(code, p, free, reach) {
  const set = setOf(code);
  if (!set || viewOnly()) return;
  const { ctx, got } = settleSet(set, p, free, reach);
  if (ctx.room && roomTaken(ctx.room.id)) return;
  drawGuides();
  if (!got) return toast(`It does not fit in ${roomName(ctx.room)}. To put it anywhere, hold Alt as you click.`, true);
  await placeSet(code, got.at, got.rot);
}

async function placeItem(type, p, free = state.alt, reach = undefined) {
  if (!type || viewOnly()) return;
  const ctx = placing(p, type, null, 0, reach);
  if (ctx.room && roomTaken(ctx.room.id)) return;
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
    if (typeOf(type)?.serves_door) { // linked to the nearest door, or to none: said, to choose it
      const d = doorOf(a);
      toast(d ? `Linked to the door it is beside${d.tag ? ` (${d.tag})` : ""}: choose it to link another`
        : "No door within 2 m: choose it to link the door it opens");
    }
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

/** The item chosen alone (or null). */
export const chosenAsset = () => (state.floor?.items || []).find((x) => x.id === state.asset) || null;

/** The items chosen (one, or a group, or several), as the floor has them. */
export const chosenAll = () => state.assets.map((id) => (state.floor?.items || []).find((x) => x.id === id)).filter(Boolean);

/** The chosen item moved by ``dx``, ``dy`` metres, held in its room (the magnet off). */
export function nudgeAsset(a, dx, dy) {
  if (viewOnly() || itemTaken(a)) return;
  const want = { at: [a.x + dx, a.y + dy], rot: a.rotation || 0 };
  const ctx = placing([a.x, a.y], a.type, a.id, a.rotation || 0);
  const got = settle({ want, last: { at: [a.x, a.y], rot: want.rot }, ...ctx, magnet: false });
  if (got && Math.hypot(got.at[0] - a.x, got.at[1] - a.y) > 1e-4) changeAsset(a, { x: round4(got.at[0]), y: round4(got.at[1]) });
  else toast(`Against the wall of ${roomName(ctx.room)} (drag it with Alt held to take it further)`);
}

/** An item turned where it stands, kept in its room (moved the least it takes, a
 * metre at most). */
export function turnAsset(a, rot) {
  if (viewOnly() || itemTaken(a)) return;
  rot = ((rot % 360) + 360) % 360;
  const ctx = placing([a.x, a.y], a.type, a.id, a.rotation || 0);
  const got = settle({ want: { at: [a.x, a.y], rot }, last: { at: [a.x, a.y], rot: a.rotation || 0 }, ...ctx, magnet: false });
  if (!got) return toast(`No room to turn it there in ${roomName(ctx.room)}: drag it (Alt: freely) where there is`, true);
  const moved = Math.hypot(got.at[0] - a.x, got.at[1] - a.y) > 1e-4 ? { x: round4(got.at[0]), y: round4(got.at[1]) } : {};
  changeAsset(a, { rotation: round4(rot), ...moved });
}

/** An item mirrored where it stands (``axis``: groups.js mirrored): turned as the mirror
 * shows it, kept in its room as when it is turned. One facing along the mirror looks the
 * same mirrored: said so, nothing changed. */
export function mirrorAsset(a, axis) {
  if (viewOnly() || itemTaken(a)) return;
  const rot = mirrored(a.rotation || 0, axis);
  if (Math.abs(((rot - (a.rotation || 0)) % 360 + 540) % 360 - 180) < 1e-6) {
    return toast(`It looks the same mirrored ${axis === "x" ? "left to right" : "top to bottom"}: it faces along the mirror. ` +
      "Mirror its group, or turn it", true);
  }
  turnAsset(a, rot);
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

/** What the Place tool places: a type's code, or a set ({set: code}). */
export function placeType(choice) {
  state.placeSet = choice?.set ? setOf(choice.set) : null;
  const code = choice?.set ? null : choice;
  state.placeType = code;
  if (state.tool !== "place") setTool("place");
  emit("tool-options");
  emit("tool-progress");
  if (view3d.shown) aimSoon();
}

function placeOptions() {
  const t = typeOf(state.placeType), set = state.placeSet;
  const chooser = el("button", { type: "button", class: "type-chooser", "aria-haspopup": "listbox", "data-tip": "What to place" },
    t ? el("span", { class: "swatch", style: `background:${t.color || "#8a8a8a"}` }) : set ? icon("layers", { size: 14 }) : null,
    el("span", {}, t ? t.name_en : set ? set.name_en : "Choose what to place…"), icon("chevron-down", { size: 14 }));
  chooser.addEventListener("click", () => openInsert(placeType));
  const note = t ? `${t.width} × ${t.depth} m · Alt: anywhere, as it is`
    : set ? `${set.pieces.length} items, each with its own ID, one group · Alt: anywhere, as it is` : null;
  return [chooser, note ? el("span", { class: "to-note" }, note) : null];
}

/** What the Place tool places now, as words (a type's name, a set's), or null. */
const placingName = () => typeOf(state.placeType)?.name_en || state.placeSet?.name_en || null;

/** The item chosen, its keys (the "item" scope: while one is chosen, whatever the tool or
 * the view: what is chosen wins); ``many``, the same for items chosen together (a group). */
function itemCommand(id, title, keys, fn, repeat = false, many = null) {
  command({ id, title, group: "Item", scope: "item", keys, palette: false, repeat,
    when: () => (chosenAsset() || (many && chosenAll().length > 1)) && editable(),
    why: () => (chosenAsset() || chosenAll().length ? whyNotEditable() : "Choose an item first"),
    run: () => (chosenAsset() ? fn(chosenAsset()) : many?.(chosenAll())) });
}

export function setupItems() {
  tool({
    id: "place", label: "Place an item", icon: "armchair", key: "i", group: "place", views: ["2d", "3d", "walk"], edits: true,
    words: "furniture equipment desk asset item add",
    hint: (view) => {
      const name = placingName();
      if (!name) return "Choose what to place in the options above";
      return view === "walk" ? `Click on the floor where the ${name} goes (its ghost follows the pointer) · Alt-click: as it is · drag to look`
        : view === "3d" ? `Click on the floor where the ${name} goes · it lines up as on the plan · Alt-click: as it is`
          : `Click in a room where the ${name} goes · it lines up with walls and items · Alt-click: anywhere, as it is`;
    },
    options: placeOptions,
    start: () => { // nothing to place yet: the box to find it, unless chosen meanwhile (or put down)
      const none = () => !state.placeType && !state.placeSet && state.tool === "place" && !document.querySelector("dialog.palette[open]");
      if (none()) requestAnimationFrame(() => none() && openInsert(placeType));
    },
    stop: () => {
      drawGuides();
      for (const id of ["preview", "preview-print"]) $(id).replaceChildren();
      view3d.world?.ghost(null);
    },
    plan: {
      hover: (p) => {
        const t = typeOf(state.placeType), set = state.placeSet;
        if (set && !state.busy) { // a set: its pieces where they would stand, drawn to the room's walls as one
          const { got } = settleSet(set, p, state.alt);
          drawGuides(got?.guides);
          for (const id of ["preview", "preview-print"]) $(id).replaceChildren(setGhost(set, got?.at ?? p, got?.rot ?? 0, got ? "asset-ghost" : "asset-ghost refused"));
          return;
        }
        if (!t || state.busy) return;
        const got = settle({ want: { at: p, rot: 0 }, last: null, ...placing(p, t.code), free: state.alt });
        drawGuides(got?.guides);
        const ghost = () => assetShape({ x: (got?.at ?? p)[0], y: (got?.at ?? p)[1], rotation: got?.rot ?? 0 }, t,
          got ? "asset-ghost" : "asset-ghost refused");
        for (const id of ["preview", "preview-print"]) $(id).replaceChildren(ghost());
      },
      click: (p, e) => {
        if (state.placeSet) return placeAsset({ set: state.placeSet.code }, p, e.altKey);
        if (!state.placeType) return toast("Choose what to place first", true);
        placeAsset(state.placeType, p, e.altKey);
      },
    },
  });
  itemCommand("item.turn", "Turn the item 90°", ["r"], (a) => turnAsset(a, a.rotation + 90), false, (l) => turnAssets(l, 90));
  itemCommand("item.turn-back", "Turn the item back 90°", ["shift+r"], (a) => turnAsset(a, a.rotation + 270), false, (l) => turnAssets(l, -90));
  itemCommand("item.turn-left", "Turn the item 15° left", [","], (a) => turnAsset(a, a.rotation + 15), true, (l) => turnAssets(l, 15));
  itemCommand("item.turn-right", "Turn the item 15° right", ["."], (a) => turnAsset(a, a.rotation + 345), true, (l) => turnAssets(l, -15));
  itemCommand("item.mirror-x", "Mirror the item left to right", ["shift+h"], (a) => mirrorAsset(a, "x"), false, (l) => mirrorAssets(l, "x"));
  itemCommand("item.mirror-y", "Mirror the item top to bottom", ["shift+v"], (a) => mirrorAsset(a, "y"), false, (l) => mirrorAssets(l, "y"));
  for (const [key, dx, dy] of [["arrowleft", -1, 0], ["arrowright", 1, 0], ["arrowup", 0, 1], ["arrowdown", 0, -1]]) {
    itemCommand(`item.move-${key.slice(5)}`, `Move the item ${key.slice(5)}`, [key], (a) => nudgeAsset(a, dx * 0.1, dy * 0.1), true,
      (l) => moveAssets(l, dx * 0.1, dy * 0.1));
    itemCommand(`item.move-${key.slice(5)}-far`, `Move the item ${key.slice(5)} 1 m`, [`shift+${key}`], (a) => nudgeAsset(a, dx, dy), true,
      (l) => moveAssets(l, dx, dy));
  }
  // items chosen together: made one group, or let go of theirs
  command({ id: "item.group", title: "Group the items chosen", group: "Item", scope: "items", keys: ["mod+g"], words: "group together set",
    when: () => chosenAll().length > 1 && !sameGroup(chosenAll()) && editable(),
    why: () => (chosenAll().length > 1 ? (sameGroup(chosenAll()) ? "They are one group already" : whyNotEditable())
      : "Choose two items or more first (Shift-click each)"),
    run: () => groupAssets(chosenAll()) });
  command({ id: "item.ungroup", title: "Ungroup the items chosen", group: "Item", scope: "items", keys: ["mod+shift+g"], words: "ungroup split apart set",
    when: () => chosenAll().some((a) => a.group) && editable(),
    why: () => (chosenAll().some((a) => a.group) ? whyNotEditable() : "Choose a group first"),
    run: () => ungroupAssets(chosenAll().filter((a) => a.group)) });
}
