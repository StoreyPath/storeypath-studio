// Floors and walls finished, in Review (format 0.9: a room's floor_finish and a space's
// wall_finish, codes of StoreyPath's fixed set, ../../../../viewer/src/finishes.js; none,
// its type's). Its own file, joined to review.js by a few marked hooks:
//
//   setup(api)     once, with what of review.js it uses (its state, its requests…): the
//                  Paint button and its palette over the 3D view, Colour (by type or by
//                  floor finish) on the plan;
//   editor(space)  when a room's editor is shown: its Floor and Walls, each a picker of
//                  swatches (its type's first), and "Apply to every <type> on this floor";
//   fill(space)    the colour the plan fills a room with: its type's, or its floor's tone;
//   legend()       what the legend lists when the plan is coloured by floor finish;
//   pick(detail)   the 3D view's click while painting: a floor painted with the floor
//                  brush, a wall (the room on the side clicked) with the walls' brush;
//                  Alt-click, or the dropper, takes up the finishes there instead;
//   escape()       Esc: the palette or the paint tool put away.
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

const GROUPS = new Map(FINISHES.groups.map((g) => [g.code, g]));
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
function picker(anchor, { applies, current, typeDefault, onPick, defaultLabel = "As its type" }) {
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
  const left = Math.min(Math.max(8, r.right + 8), window.innerWidth - w - 8);
  const top = Math.min(Math.max(8, r.top - 40), window.innerHeight - h - 8);
  Object.assign(popover.style, { left: `${left}px`, top: `${top}px` });
  popover.querySelector(".fin-tile.chosen, .fin-tile")?.focus();
}

// ---- the room's editor --------------------------------------------------------------

/** A room's Floor and Walls, in its editor, and "Apply to every <type> on this floor". */
export function editor(s) {
  const box = $("ed-finishes");
  if (!box) return;
  if (!s) return box.replaceChildren();
  const el = api.el, may = !api.viewOnly();
  const space = spaceOf(s), now = shown(s);
  const field = (label, applies, own, code, note) => {
    const button = el("button", { type: "button", class: "fin-field", disabled: !may,
      title: may ? `Choose what its ${label.toLowerCase()} ${applies === "floor" ? "is" : "are"} finished in` : "View only" },
    swatch(code, 26), el("span", { class: "fin-name" }, finishOf(code)?.name ?? code));
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
  if (may) {
    const all = api.units().filter((u) => u.type === s.type && !u.ignored).length;
    const apply = el("button", { type: "button", class: "fin-apply", disabled: all < 2,
      title: `Give every ${s.type.replaceAll("_", " ")} on this floor this room's floor${s.kind === "space" ? " and walls" : ""}: one change, undone as one` },
    `Apply to every ${plural(s.type)} on this floor (${all})`);
    apply.addEventListener("click", () => applyToType(s));
    rows.push(apply);
  }
  box.replaceChildren(...rows);
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

/** The legend when coloured by floor finish: the finishes in use, with how many rooms. */
export function legend(rooms) {
  if (colourBy !== "finish") return null;
  const counts = new Map();
  for (const s of rooms) {
    const code = shown(s).floor;
    counts.set(code, (counts.get(code) || 0) + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1]).map(([code, n]) => api.el("li", { title: finishOf(code)?.name_ar ?? "" },
    swatch(code, 14), finishOf(code)?.name ?? code, api.el("span", { class: "count" }, String(n))));
}

// ---- the paint tool, in 3D and walking ------------------------------------------------

function brushes() {
  const el = api.el;
  const brush = (applies) => {
    const code = paint[applies];
    const b = el("button", { type: "button", class: "fin-brush", title: `${applies === "floor" ? "Floors" : "Walls"} are painted in this: choose another below` },
      swatch(code, 30), el("span", {}, el("small", {}, applies === "floor" ? "Floor" : "Walls"), finishOf(code)?.name ?? code));
    b.addEventListener("click", () => $("fin-grid-" + applies)?.scrollIntoView({ block: "start", behavior: "smooth" }));
    return b;
  };
  const dropper = el("button", { type: "button", class: `fin-dropper${paint.dropper ? " active" : ""}`,
    title: "Take up the finishes of what you click next (or Alt/Option-click)" }, "Dropper");
  dropper.addEventListener("click", () => {
    paint.dropper = !paint.dropper;
    renderPalette();
  });
  return el("div", { class: "fin-brushes" }, brush("floor"), brush("wall"), dropper);
}

function renderPalette() {
  const panel = $("paint-panel");
  if (!panel) return;
  panel.hidden = !paint.on;
  if (!paint.on) return;
  const el = api.el;
  const section = (applies) => [
    el("div", { class: "fin-section", id: `fin-grid-${applies}` }, applies === "floor" ? "Floors" : "Walls"),
    ...FINISHES.groups.filter((g) => g.applies === applies).map((g) => el("div", { class: "fin-grid small", title: g.name },
      ...of(applies).filter((f) => f.group === g.code).map((f) => {
        const b = el("button", { type: "button", class: `fin-chip${paint[applies] === f.code ? " chosen" : ""}`,
          title: `${f.name} · ${f.name_ar}` }, swatch(f.code, 30));
        b.addEventListener("click", () => {
          paint[applies] = f.code;
          renderPalette();
        });
        return b;
      }))),
  ];
  const close = el("button", { type: "button", class: "icon", title: "Stop painting (Esc)", "aria-label": "Stop painting" }, "×");
  close.addEventListener("click", () => setPaint(false));
  panel.replaceChildren(
    el("div", { class: "fin-head" }, el("strong", {}, "Paint"), close),
    brushes(),
    el("div", { class: "fin-palette" }, ...section("floor"), ...section("wall")),
    el("div", { class: "meta" }, paint.dropper ? "Click a floor or a wall to take up its finish"
      : "Click a floor, or a wall (its side towards you), to paint it · Alt-click takes up a finish · walking: Esc frees the mouse to choose"),
  );
}

/** The paint tool on or off (only in 3D and walking, for who may change the floor). */
export function setPaint(on) {
  if (on && api.viewOnly()) return api.toast("View only: you may look at this floor, not change it", true);
  paint.on = Boolean(on);
  paint.dropper = false;
  $("paint")?.classList.toggle("active", paint.on);
  $("map")?.classList.toggle("painting", paint.on);
  if (paint.on) api.stopTools();
  renderPalette();
  api.showWalk();
}

export const painting = () => paint.on;

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
    renderPalette();
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

/** Esc: the picker, else the paint tool, put away. Whether it was. */
export function escape() {
  if (popover) {
    closePicker();
    return true;
  }
  if (paint.on) {
    setPaint(false);
    return true;
  }
  return false;
}

/** Once, before the page starts: the Paint button and its palette, Colour on the plan. */
export function setup(given) {
  api = given;
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "finish.css";
  document.head.append(css);
  const el = api.el;
  const button = el("button", { type: "button", id: "paint", class: "edit-only only3dwalk",
    title: "Paint floors and walls: choose a finish, click a floor or a wall (Alt/Option-click takes up the finish there)" }, "Paint");
  button.addEventListener("click", () => setPaint(!paint.on));
  const colour = el("label", { class: "only2d", title: "Fill the rooms by their type, or by what their floor is finished in" }, "Colour",
    el("select", { id: "colour-by" }, el("option", { value: "type" }, "by type"), el("option", { value: "finish" }, "by floor finish")));
  colour.querySelector("select").addEventListener("change", (e) => {
    colourBy = e.target.value;
    api.restyle();
  });
  const place = $("place-type")?.closest("label");
  place?.after(button);
  $("drawing-label")?.after(colour);
  $("map").append(el("div", { id: "paint-panel", class: "paint-panel", hidden: "" }));
  document.addEventListener("pointerdown", (e) => {
    if (popover && !e.target.closest?.(".fin-picker") && !e.target.closest?.(".fin-field")) closePicker();
  }, true);
}
