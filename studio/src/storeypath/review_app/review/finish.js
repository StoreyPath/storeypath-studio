// Floors and walls finished, in Review (format 0.9: a room's floor_finish and a space's
// wall_finish, codes of StoreyPath's fixed set, viewer/src/finishes.js; none, its type's).
// Its own file, joined to the page by:
//
//   setup(api)     once, with what of the page it uses (its state, its requests…): the
//                  Paint tool (P), its brushes in the tool's options;
//   editor(space)  the inspector's Floor and Walls of a room, each a picker of swatches
//                  (its type's first), and "Apply to every <type> on this floor";
//   many(spaces)   the inspector's Floor and Walls of several rooms at once (one change);
//   fill(space)    the colour the plan fills a room with: its type's, or its floor's tone;
//   groups(rooms)  the rooms by floor finish, when the plan is coloured so (the navigator);
//   pick(detail)   the 3D view's click while painting: a floor painted with the floor
//                  brush, a wall (the room on the side clicked) with the walls' brush;
//                  Alt-click, or the dropper, takes up the finishes there instead; on the
//                  plan, a click paints a room's floor, a Shift-click its walls;
//   escape()       Esc: the picker put away.
//
// Each change is a room's correction like any other (saved at once, undone and redone,
// one editor a floor at a time, others see it live); "every <type>" is one change.

import { FINISHES, defaultFinish, finishOf, floorFinish, wallFinish } from "/viewer/src/finishes.js";
import { Painter } from "/viewer/src/world/finishes.js";

const $ = (id) => document.getElementById(id);
let api = null; // review.js's own, as setup() was given it
let painter = null; // paints the swatches, off the page's thread (made the first time one is shown)
const pictures = new Map(); // code → its swatch as a data URL, once painted
const waiting = new Map(); // code → the elements to show it in when it is
const paint = { on: false, floor: "FLOOR-CARPET-NAVY", wall: "WALL-PAINT-WHITE", dropper: false };
let colourBy = "type";

/** How the plan colours the rooms: "type" or "finish" (their floor's). */
export const colouredBy = () => colourBy;
export function colourRoomsBy(by) {
  colourBy = by === "finish" ? "finish" : "type";
  api.restyle();
}

const of = (applies) => FINISHES.finishes.filter((f) => f.applies === applies);
const plural = (type) => (type.endsWith("s") ? type : `${type}s`).replaceAll("_", " ");
const spaceOf = (s) => (s?.kind === "zone" ? api.state.byId.get(s.space_id) ?? null : null);

/** The finishes a room shows on the plan's page: its floor's (a zone's: its own, else its
 * space's, else its type's) and its walls' (a zone's: its space's). */
export function shown(s) {
  const space = spaceOf(s);
  return { floor: floorFinish(s, space), wall: wallFinish(space ?? s) };
}

// ---- swatches -----------------------------------------------------------------------

/** A swatch of a finish: its tone at once, its painting when it comes. */
export function swatch(code, size = 28) {
  const f = finishOf(code);
  const box = api.el("span", { class: "fin-swatch", style: `width:${size}px;height:${size}px;background-color:${f?.tone ?? "#ccc"}` });
  if (!f) return box;
  if (pictures.has(code)) box.style.backgroundImage = `url(${pictures.get(code)})`;
  else {
    if (!waiting.has(code)) {
      waiting.set(code, new Set());
      painter ??= new Painter();
      painter.swatch(code, 96).then((r) => {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 96;
        canvas.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(r.color.buffer, r.color.byteOffset, r.color.length), 96, 96), 0, 0);
        pictures.set(code, canvas.toDataURL());
        for (const el of waiting.get(code) ?? []) el.style.backgroundImage = `url(${pictures.get(code)})`;
        waiting.delete(code);
      }).catch(() => waiting.delete(code));
    }
    waiting.get(code)?.add(box);
  }
  return box;
}

// ---- the picker: a grid of swatches, its type's first --------------------------------

let popover = null;

function closePicker() {
  popover?.remove();
  popover = null;
}

/** A popover of finishes for ``applies`` by ``anchor``: its type's default first (null),
 * then each group's; ``onPick(code or null)``. */
export function picker(anchor, { applies, current, typeDefault, onPick, defaultLabel = "As its type" }) {
  closePicker();
  const el = api.el;
  const choose = (code) => () => {
    closePicker();
    onPick(code);
  };
  // a finish's tile; ``code`` null: its type's (shown in ``shows``)
  const tile = (code, label, chosen, shows = code) => {
    const b = el("button", { type: "button", class: `fin-tile${chosen ? " chosen" : ""}`, title: finishOf(shows)?.name_ar
      ? `${label} · ${finishOf(shows).name_ar}` : label }, swatch(shows, 40), el("span", {}, label));
    b.addEventListener("click", choose(code));
    return b;
  };
  const body = [];
  if (typeDefault) {
    const b = tile(null, `${defaultLabel}: ${finishOf(typeDefault)?.name ?? typeDefault}`, current === null, typeDefault);
    b.classList.add("fin-default");
    body.push(b);
  }
  for (const g of FINISHES.groups.filter((x) => x.applies === applies)) {
    body.push(el("div", { class: "fin-group" }, g.name));
    body.push(el("div", { class: "fin-grid" }, ...of(applies).filter((f) => f.group === g.code)
      .map((f) => tile(f.code, f.name, current === f.code))));
  }
  popover = el("div", { class: "fin-picker", role: "dialog", "aria-label": `${applies === "floor" ? "Floor" : "Walls"} finish` },
    el("div", { class: "fin-head" }, el("strong", {}, applies === "floor" ? "Floor" : "Walls"),
      el("button", { type: "button", class: "icon", title: "Close (Esc)", "aria-label": "Close" }, "×")),
    el("div", { class: "fin-body" }, ...body));
  popover.querySelector(".fin-head button").addEventListener("click", closePicker);
  document.body.append(popover);
  const r = anchor.getBoundingClientRect(), w = popover.offsetWidth, h = popover.offsetHeight;
  // beside what opened it: to its left in the inspector, below it elsewhere
  const inPanel = Boolean(anchor.closest(".inspector"));
  const left = inPanel ? Math.max(8, r.left - w - 8) : Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
  const top = inPanel ? Math.min(Math.max(8, r.top - 40), window.innerHeight - h - 8)
    : Math.min(Math.max(8, r.bottom + 8), window.innerHeight - h - 8);
  Object.assign(popover.style, { left: `${left}px`, top: `${top}px` });
  popover.querySelector(".fin-tile.chosen, .fin-tile")?.focus();
}

// ---- the room's editor --------------------------------------------------------------

/** A room's Floor and Walls (the inspector's), and "Apply to every <type> on this floor". */
export function editor(s) {
  const box = api.el("div", { id: "ed-finishes", class: "fin-editor" });
  if (!s) return box;
  const el = api.el, may = api.editable(); // (asked without a word: the inspector is drawn often)
  const space = spaceOf(s), now = shown(s);
  const field = (label, applies, own, code, note) => {
    const button = el("button", { type: "button", class: "fin-field",
      title: may ? `Choose what its ${label.toLowerCase()} ${applies === "floor" ? "is" : "are"} finished in` : "View only" },
    swatch(code, 26), el("span", { class: "fin-name" }, finishOf(code)?.name ?? code));
    button.disabled = !may; // (a property: el() would set the attribute, which disables it whatever its value)
    button.addEventListener("click", () => picker(button, { applies, current: own ?? null,
      typeDefault: applies === "floor" ? (space ? floorFinish({ type: s.type }, space) : defaultFinish("floor", s.type))
        : defaultFinish("wall", s.type),
      defaultLabel: applies === "floor" && space?.floor_finish ? "As its space" : "As its type",
      onPick: (picked) => api.saveSpace({ [`${applies}_finish`]: picked }, s.id) }));
    return el("div", { class: "field fin-row" }, el("span", {}, label), button, el("span", { class: "detected" }, note));
  };
  const rows = [field("Floor", "floor", s.floor_finish, now.floor,
    s.floor_finish ? "chosen here" : space?.floor_finish ? "as its space" : `as its type (${s.type.replaceAll("_", " ")})`)];
  if (s.kind === "space") {
    rows.push(field("Walls", "wall", s.wall_finish, now.wall,
      s.wall_finish ? "chosen here: each wall's face towards it" : "as its type: each wall's face towards it"));
  } else rows.push(el("div", { class: "meta" }, `Walls: its space's (${finishOf(now.wall)?.name ?? now.wall})`));
  const all = api.units().filter((u) => u.type === s.type && !u.ignored).length;
  if (may && all > 1) {
    const apply = el("button", { type: "button", class: "fin-apply btn-sm btn-ghost",
      title: `Give every ${s.type.replaceAll("_", " ")} on this floor this room's floor${s.kind === "space" ? " and walls" : ""}: one change, undone as one` },
    `Apply to every ${s.type.replaceAll("_", " ")} on this floor (${all})`);
    apply.addEventListener("click", () => applyToType(s));
    rows.push(apply);
  }
  box.replaceChildren(...rows);
  return box;
}

/** Several rooms' Floor and Walls, chosen for all at once (one change, undone as one). */
export function many(spaces) {
  const el = api.el, may = api.editable(); // (asked without a word: the inspector is drawn often)
  const field = (label, applies) => {
    const codes = new Set(spaces.filter((s) => applies === "floor" || s.kind === "space").map((s) => shown(s)[applies]));
    const one = codes.size === 1 ? [...codes][0] : null;
    const button = el("button", { type: "button", class: "fin-field" },
      one ? swatch(one, 26) : el("span", { class: "fin-swatch fin-mixed", style: "width:26px;height:26px" }),
      el("span", { class: "fin-name" }, one ? finishOf(one)?.name ?? one : `Mixed (${codes.size})`));
    button.disabled = !may;
    button.addEventListener("click", () => picker(button, { applies, current: one, typeDefault: null,
      onPick: (picked) => finishMany(spaces, { [`${applies}_finish`]: picked }) }));
    return el("div", { class: "field fin-row" }, el("span", {}, label), button);
  };
  const rows = [field("Floor", "floor")];
  if (spaces.some((s) => s.kind === "space")) rows.push(field("Walls", "wall"));
  return el("div", { class: "fin-editor" }, ...rows);
}

/** Rooms' finishes set at once: one change (ids). */
async function finishMany(spaces, body) {
  if (api.viewOnly()) return;
  try {
    const r = await api.request(`${api.BASE}/floors/${encodeURIComponent(api.state.floor.id)}/finishes`,
      { ids: spaces.map((s) => s.id), ...body });
    api.updated(r.spaces);
    api.toast(`${r.changed.length} room${r.changed.length === 1 ? "" : "s"}: ${said(body)}`);
  } catch (e) {
    api.toast(`Not saved: ${e.message}`, true);
  }
}

/** This room's finishes on every room of its type on the floor: one change. */
async function applyToType(s) {
  const body = { type: s.type, floor_finish: s.floor_finish ?? null };
  if (s.kind === "space") body.wall_finish = s.wall_finish ?? null;
  try {
    const r = await api.request(`${api.BASE}/floors/${encodeURIComponent(api.state.floor.id)}/finishes`, body);
    api.updated(r.spaces);
    api.toast(r.changed.length ? `${r.changed.length} ${plural(s.type)} finished as ${api.title(s)}` : `Every ${s.type.replaceAll("_", " ")} is finished so already`);
  } catch (e) {
    api.toast(`Not saved: ${e.message}`, true);
  }
}

/** What a saved change of finishes says (null: not one). */
export function said(body) {
  const parts = [];
  if ("floor_finish" in body) parts.push(`floor ${body.floor_finish ? finishOf(body.floor_finish)?.name : "as its type"}`);
  if ("wall_finish" in body) parts.push(`walls ${body.wall_finish ? finishOf(body.wall_finish)?.name : "as its type"}`);
  return parts.length ? parts.join(", ") : null;
}

// ---- the plan coloured by floor finish ----------------------------------------------

/** The colour the plan fills a room with when coloured by floor finish, else null. */
export function fill(s) {
  return colourBy === "finish" ? finishOf(shown(s).floor)?.tone ?? null : null;
}

/** The rooms by the finish of their floor, most first: [{code, name, tone, rooms}]. */
export function groups(rooms) {
  const by = new Map();
  for (const s of rooms) {
    const code = shown(s).floor;
    if (!by.has(code)) by.set(code, []);
    by.get(code).push(s);
  }
  return [...by].sort((a, b) => b[1].length - a[1].length)
    .map(([code, list]) => ({ code, name: finishOf(code)?.name ?? code, tone: finishOf(code)?.tone ?? "#ccc", rooms: list }));
}

// ---- the paint tool: in 3D and walking, and on the plan ----------------------------------

/** The brushes, in the tool's options: the floor's and the walls' finish (a click chooses
 * another), and the dropper. */
function brushes() {
  const el = api.el;
  const brush = (applies) => {
    const code = paint[applies];
    const b = el("button", { type: "button", class: "fin-brush", "data-tip": `${applies === "floor" ? "Floors" : "Walls"} are painted in this: click to choose another` },
      swatch(code, 24), el("span", { class: "fin-brush-text" }, el("small", {}, applies === "floor" ? "Floor" : "Walls"), finishOf(code)?.name ?? code));
    b.addEventListener("click", () => picker(b, { applies, current: code, typeDefault: null, onPick: (picked) => {
      if (picked) paint[applies] = picked;
      api.emit("tool-options");
    } }));
    return b;
  };
  const dropper = el("button", { type: "button", class: `fin-dropper btn-icon${paint.dropper ? " active" : ""}`, "aria-pressed": String(paint.dropper),
    "aria-label": "Dropper", "data-tip": "Take up the finishes of what you click next (or Alt-click)" }, api.icon("pipette", { size: 16 }));
  dropper.addEventListener("click", () => {
    paint.dropper = !paint.dropper;
    api.emit("tool-options");
    api.emit("tool-progress");
  });
  return [brush("floor"), brush("wall"), dropper];
}

/** The paint tool on or off (in 3D, walking and on the plan, for who may change the floor). */
export function setPaint(on) {
  api.setTool(on ? "paint" : null);
}

export const painting = () => api.state.tool === "paint";

/** The plan's click while painting: a room's floor (Shift: its walls; a zone's are its
 * space's), or its finishes taken up (Alt, or the dropper). */
function paintOnPlan(p, e) {
  const unit = api.spaceAt(...p);
  if (!unit) return api.toast("Click a room to paint its floor", true);
  pick({ floor: api.state.floor.id, space: unit.id, room: unit.kind === "zone" ? unit.space_id : unit.id,
    wall: e.shiftKey, altKey: e.altKey });
}

/** The 3D view's click while painting (the world's pick): painted, or taken up. Whether it was. */
export function pick(p) {
  if (!paint.on) return false;
  const st = api.state;
  if (!p || p.floor !== st.floor?.id) {
    api.toast("Click a floor or a wall of this floor", true);
    return true;
  }
  const unit = p.space ? st.byId.get(p.space) : null;
  const room = p.room ? st.byId.get(p.room) : unit;
  if (!unit) {
    api.toast(p.wall ? "That side of the wall faces outside: paint it from a room" : "That is outside every room", true);
    return true;
  }
  if (p.altKey || paint.dropper) { // taken up
    const now = shown(unit);
    paint.floor = now.floor;
    paint.wall = now.wall;
    paint.dropper = false;
    api.emit("tool-options");
    api.toast(`Took up ${finishOf(now.floor)?.name} and ${finishOf(now.wall)?.name}`);
    return true;
  }
  // (already so: nothing to change)
  if (p.wall) {
    const target = room?.kind === "space" ? room : unit.kind === "zone" ? st.byId.get(unit.space_id) : unit;
    if (target && shown(target).wall !== paint.wall) api.saveSpace({ wall_finish: paint.wall }, target.id);
  } else if (shown(unit).floor !== paint.floor) api.saveSpace({ floor_finish: paint.floor }, unit.id);
  return true;
}

/** Esc: the picker put away. Whether it was. */
export function escape() {
  if (popover) {
    closePicker();
    return true;
  }
  if (paint.dropper && painting()) {
    paint.dropper = false;
    api.emit("tool-options");
    return true;
  }
  return false;
}

/** Once, before the page starts: the Paint tool. */
export function setup(given) {
  api = given;
  api.tool({
    id: "paint", label: "Paint finishes", icon: "paint-roller", key: "p", group: "place", views: ["2d", "3d", "walk"],
    edits: true, keepsSelection: true, words: "finish floor wall carpet tiles paint colour",
    hint: (view) => (paint.dropper ? "Click a room (or in 3D a floor or a wall) to take up its finishes"
      : view === "2d" ? `Click a room: its floor gets ${finishOf(paint.floor)?.name} · Shift-click: its walls get ${finishOf(paint.wall)?.name} · Alt-click takes up its finishes`
        : view === "walk" ? "Aim at a floor or a wall and click to paint it · Alt-click takes up a finish · Esc frees the mouse"
          : "Click a floor, or a wall (its side towards you), to paint it · Alt-click takes up a finish"),
    options: brushes,
    start: () => {
      paint.on = true;
      paint.colouredBefore = colourBy; // the plan shows what is painted: by floor finish, while painting
      if (colourBy !== "finish") colourRoomsBy("finish");
      $("map")?.classList.add("painting");
      api.stopTools();
      api.showWalk();
    },
    stop: () => {
      paint.on = false;
      paint.dropper = false;
      if (paint.colouredBefore && paint.colouredBefore !== colourBy) colourRoomsBy(paint.colouredBefore);
      paint.colouredBefore = null;
      closePicker();
      $("map")?.classList.remove("painting");
      api.showWalk();
    },
    plan: { click: paintOnPlan },
  });
  document.addEventListener("pointerdown", (e) => {
    if (popover && !e.target.closest?.(".fin-picker") && !e.target.closest?.(".fin-field, .fin-brush")) closePicker();
  }, true);
}
