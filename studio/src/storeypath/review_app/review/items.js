// Items: furniture and equipment. Desks by grade, central photocopiers, access points,
// sofas, TVs… (the Studio's catalogue of item types). Each has an ID of its own that stays
// with it wherever it is carried; it is saved at once, with no reading of the drawing
// again. An item stays in the room it was placed in and lines up with walls and items
// (fit.js); Alt places or drags it freely.

import { viewOnly } from "./access.js";
import { request } from "./api.js";
import { emit } from "./bus.js";
import { $, svg } from "./dom.js";
import { openFloor } from "./floor.js";
import { toast } from "./notify.js";
import { centerOn, floorBounds, scaleFor } from "./plan.js";
import { DESK_SETS, fits, inRings, itemBox, ringsOf, roomAt, settle, visitorChairs } from "../fit.js";
import { selectAsset } from "./selection.js";
import { BASE, round4, state, typeOf, visible, view3d } from "./state.js";
import { update3d } from "./view3d.js";

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

/** An item's shape on the plan: its footprint turned with it, the edge it faces
 * darker, and a desk's chair and what goes with its grade; on the ceiling, a circle. */
export function assetShape(a, t, cls) {
  const g = svg("g", { transform: `translate(${a.x} ${a.y}) rotate(${a.rotation || 0})`, class: cls });
  const w = t?.width ?? 1, d = t?.depth ?? 0.6;
  if (t?.mount === "ceiling") {
    g.classList.add("ceiling");
    g.append(svg("circle", { r: Math.max(w, d) / 2, fill: t?.color || "#8a8a8a" }));
  } else {
    g.append(svg("rect", { x: -w / 2, y: -d / 2, width: w, height: d, fill: t?.color || "#8a8a8a" }));
    g.append(svg("line", { x1: -w / 2, y1: -d / 2, x2: w / 2, y2: -d / 2, class: "front" })); // its front: the plan's -y, turned
    if ((t?.code || t?.type || "").split("-")[0] === "DESK") deskSet(g, t, w, d);
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
