// The inspector of several rooms at once, of an item, of a door, window or opening, and
// of a line drawn here.
//
// Several rooms: their type (each room's correction saved in turn: undone one at a
// time), their floor's and walls' finishes (one change), accepting those to review, and
// deleting them. An item: its type, turn, floor, details, the room it stands in. A door,
// window or opening: its sizes. A line drawn here: taken away.

import { editable } from "../access.js";
import { header, field, row, section } from "../inspector.js";
import { el, icon } from "../dom.js";
import { deleteItem, drawnLength, openingMeta, openingName, sizeForm } from "../drawing.js";
import * as finish from "../finish.js";
import { categories, changeAsset, copyId, turnAsset } from "../items.js";
import { kbd, keysOf, keyText } from "../keys.js";
import { toast } from "../notify.js";
import { accept, correct, forEach, setFlag } from "../rooms.js";
import { select } from "../selection.js";
import { color, readable, state, title, typeName, typeOf, units, view3d, within } from "../state.js";
import { roomHistory } from "./history.js";

const key = (id) => {
  const k = keysOf(id)[0];
  return k ? keyText(k) : "";
};

// ---- several rooms ---------------------------------------------------------------------

function roomsHeader({ spaces }) {
  const types = new Set(spaces.map((s) => s.type));
  const chips = spaces.slice(0, 14).map((s) => {
    const b = el("button", { type: "button", class: "chip", "data-tip": `${title(s)} · ${typeName(s.type)}: choose it alone` },
      el("span", { class: "swatch", style: `background:${color(s.type)}` }), el("span", {}, title(s)));
    b.addEventListener("click", () => select(s.id, { fly: true }));
    return b;
  });
  if (spaces.length > 14) chips.push(el("span", { class: "chip more" }, `+${spaces.length - 14}`));
  return [el("div", { class: "ih-top" }, icon("square-dashed", { size: 18, cls: "ih-icon" }), el("h2", { class: "ih-title" }, `${spaces.length} rooms`)),
    el("div", { class: "ih-sub" }, types.size === 1 ? typeName([...types][0]) : `${types.size} types`, " · Shift- or ⌘-click to add or take one out"),
    el("div", { class: "chips" }, ...chips)];
}

function bulkType({ spaces, editable: may }) {
  const types = new Set(spaces.map((s) => s.type));
  const pick = el("select", { "aria-label": "Type of the rooms chosen", disabled: !may || null },
    types.size > 1 ? el("option", { value: "" }, `Mixed (${types.size} types)`) : null,
    ...state.project.types.map((t) => el("option", { value: t }, typeName(t))));
  pick.value = types.size === 1 ? [...types][0] : "";
  pick.addEventListener("change", async () => {
    const type = pick.value;
    if (!type) return;
    const todo = spaces.filter((s) => s.type !== type);
    const n = await forEach(todo, (s) => correct(state.byId.get(s.id) || s, { type }, { quiet: true }), `Typing the rooms ${typeName(type)}`);
    toast(`${n} room${n === 1 ? "" : "s"} typed ${typeName(type)}${n < todo.length ? `; ${todo.length - n} not` : ""}`, n < todo.length);
  });
  return [row("Type", pick), el("div", { class: "detected" }, "Each room is saved as its own change (undone one at a time)")];
}

function bulkCheck({ spaces, editable: may }) {
  const flagged = spaces.filter((s) => s.reasons.length && s.type !== "unspecified");
  const all = spaces.filter((s) => !s.ignored);
  const parts = [];
  if (may && flagged.length) {
    const ok = el("button", { type: "button", class: "btn-sm" }, icon("check", { size: 14 }), `Accept ${flagged.length} as they are`);
    ok.addEventListener("click", async () => {
      const n = await forEach(flagged, (s) => accept(state.byId.get(s.id) || s, { quiet: true }), "Accepting the rooms");
      toast(`${n} room${n === 1 ? "" : "s"} accepted as they are`);
    });
    parts.push(ok);
  }
  if (may && all.length) {
    const del = el("button", { type: "button", class: "btn-sm btn-danger" }, icon("trash-2", { size: 14 }), `Delete ${all.length}`);
    del.addEventListener("click", async () => {
      if (!confirm(`Delete ${all.length} rooms? They are kept with their IDs, and come back with Show deleted.`)) return;
      const n = await forEach(all, (s) => setFlag(s.id, "ignored", true), "Deleting the rooms");
      toast(`${n} room${n === 1 ? "" : "s"} deleted`);
    });
    parts.push(el("span", { class: "grow" }), del);
  }
  return parts.length ? el("div", { class: "sec-actions" }, ...parts) : null;
}

// ---- an item ----------------------------------------------------------------------------

function itemHeader({ asset: a }) {
  if (!a) return null;
  const t = typeOf(a.type);
  const copy = el("button", { type: "button", class: "btn-ghost btn-icon btn-sm", "aria-label": "Copy its tag", "data-tip": "Copy its tag" },
    icon("copy", { size: 13 }));
  copy.addEventListener("click", () => copyId(a.id));
  return [el("div", { class: "ih-top" }, el("span", { class: "ih-swatch", style: `background:${t?.color || "#8a8a8a"}` }),
    el("h2", { class: "ih-title" }, t ? t.name_en : a.type), a.retired ? el("div", { class: "ih-flags" }, el("span", { class: "badge danger" }, "Deleted")) : null),
  el("div", { class: "ih-sub" }, "Item", t?.name_ar ? [" · ", el("bdi", { dir: "rtl", lang: "ar" }, t.name_ar)] : null),
  el("div", { class: "ih-id" }, el("code", { class: "tag", "data-tip": "Its ID, the tag on it: the same wherever it goes" }, a.id), copy)];
}

function itemMain({ asset: a, editable: may }) {
  if (!a) return null;
  const t = typeOf(a.type);
  const type = el("select", { id: "as-type", "aria-label": "Type", disabled: !may || null },
    ...categories().map(([category, types]) => el("optgroup", { label: category[0].toUpperCase() + category.slice(1) },
      ...types.map((x) => el("option", { value: x.code }, x.name_en)))));
  type.value = a.type;
  type.addEventListener("change", () => changeAsset(a, { type: type.value }));
  const turn = field(el("input", { id: "as-rotation", type: "number", step: "1", value: Math.round(a.rotation || 0), "aria-label": "Turned, in degrees",
    disabled: !may || null }), (v) => {
    if (Number.isFinite(Number(v))) turnAsset(a, Number(v));
  });
  const by = (deg, ico, tip) => {
    const b = el("button", { type: "button", class: "btn-icon btn-sm", "aria-label": tip, "data-tip": tip, disabled: !may || null }, icon(ico, { size: 14 }));
    b.addEventListener("click", () => turnAsset(a, (a.rotation || 0) + deg));
    return b;
  };
  const floor = el("select", { id: "as-floor", "aria-label": "Floor", disabled: !may || null },
    ...state.project.floors.filter((f) => f.id === state.floor.id || editable(f.id))
      .map((f) => el("option", { value: f.id }, `${f.building} · ${f.name}`)));
  floor.value = state.floor.id;
  floor.addEventListener("change", () => { if (floor.value !== state.floor.id) changeAsset(a, { floor_id: floor.value }); });
  const about = [t && `${t.width} × ${t.depth} m`, t?.mount === "ceiling" ? "on the ceiling" : t?.mount === "wall" ? "on a wall" : ""].filter(Boolean).join(" · ");
  return [row("Type", type, about ? el("div", { class: "detected" }, about) : null),
    row("Turned", el("div", { class: "with-unit" }, turn, el("span", { class: "unit" }, "°"), by(90, "rotate-ccw", `Turn 90° (${key("item.turn")})`),
      by(-90, "rotate-cw", `Turn back 90° (${key("item.turn-back")})`))),
    row("Floor", floor, el("div", { class: "detected" }, "Carried to another floor, it keeps its ID")),
    may ? el("p", { class: "hint" }, "Drag it to move it: it lines up with walls and items and stays in its room (Alt: freely). ",
      kbd("r"), " turns it 90°, ", kbd(","), " ", kbd("."), " by 15°", view3d.mode === "walk" ? "." : ", the arrows move it.") : null];
}

function itemDetails({ asset: a, editable: may }) {
  if (!a) return null;
  const t = typeOf(a.type);
  const own = (t?.fields || []).filter((f) => f.owner === "storeypath");
  const others = (t?.fields || []).filter((f) => f.owner !== "storeypath").map((f) => f.name_en);
  if (!own.length && !others.length) return null;
  const rows = own.map((f) => {
    const input = f.kind === "choice"
      ? el("select", { disabled: !may || null }, el("option", { value: "" }, "—"), ...f.choices.map((c) => el("option", { value: c }, c)))
      : el("input", { type: f.kind === "number" ? "number" : f.kind === "color" ? "color" : "text", step: "any", disabled: !may || null });
    input.value = a.values?.[f.key] ?? (f.kind === "color" ? "#000000" : "");
    input.addEventListener("change", () => changeAsset(a, { values: { ...a.values, [f.key]: input.value } }));
    return row(f.name_en, input);
  });
  if (others.length) rows.push(el("p", { class: "hint" }, `${others.join(", ")}: entered where the asset is managed (wayfinder)`));
  return rows;
}

function itemWhere({ asset: a }) {
  if (!a) return null;
  const room = units().find((s) => !s.ignored && within([a.x, a.y], s.geometry));
  const go = room ? el("button", { type: "button", class: "btn-link" }, title(room)) : el("span", { class: "muted" }, "Outside every room");
  go.addEventListener?.("click", () => room && select(room.id, { fly: true }));
  return [row("In", go), row("At", el("code", { class: "value" }, `${a.x.toFixed(2)}, ${a.y.toFixed(2)} m`))];
}

function itemActions({ asset: a, editable: may }) {
  if (!a || !may) return null;
  const del = el("button", { type: "button", class: "btn-sm btn-danger", "data-key": "delete" },
    icon(a.retired ? "rotate-cw" : "trash-2", { size: 14 }), a.retired ? "Restore" : "Delete");
  del.addEventListener("click", () => changeAsset(a, { retired: !a.retired }));
  return el("div", { class: "sec-actions" }, el("span", { class: "grow" }), del);
}

// ---- a door, window or opening; a line drawn here -----------------------------------------

function openingHeader({ opening: d }) {
  if (!d) return null;
  const ico = d.type === "window" ? "app-window" : "door-open";
  return [el("div", { class: "ih-top" }, icon(ico, { size: 18, cls: "ih-icon" }), el("h2", { class: "ih-title" }, openingName(d)),
    d.ignored ? el("div", { class: "ih-flags" }, el("span", { class: "badge danger" }, "Deleted")) : null),
  el("div", { class: "ih-sub" }, openingMeta(d) || (d.type === "window" ? "Window" : d.type === "door" ? "Door" : "An opening: a way through, no door")),
  el("div", { class: "ih-id" }, el("code", {}, d.id))];
}

function openingSize({ opening: d, editable: may }) {
  if (!d || d.ignored) return null;
  if (!readable()) return el("p", { class: "hint" }, "Its size changes with the floor's drawing: this floor has none yet");
  const form = sizeForm(d);
  if (!may) for (const e of form.elements) e.disabled = true;
  return [form, el("p", { class: "hint" }, "Empty: as the drawing (or its schedule) has it. The floor is read again with the new size.")];
}

function openingActions({ opening: d, editable: may }) {
  if (!d || !may) return null;
  const b = el("button", { type: "button", class: "btn-sm btn-danger", "data-key": "delete" },
    icon(d.ignored && !d.drawn ? "rotate-cw" : "trash-2", { size: 14 }), d.drawn ? "Take it away" : d.ignored ? "Restore" : "Delete");
  b.addEventListener("click", deleteItem);
  return el("div", { class: "sec-actions" }, el("span", { class: "grow" }), b);
}

function drawnHeader({ drawn }) {
  if (!drawn) return null;
  const length = drawnLength(drawn);
  return [el("div", { class: "ih-top" }, icon(drawn.kind === "wall" ? "brick-wall" : "square-split-horizontal", { size: 18, cls: "ih-icon" }),
    el("h2", { class: "ih-title" }, drawn.kind === "wall" ? "Wall drawn here" : "Dividing line drawn here")),
  el("div", { class: "ih-sub" }, [length != null ? `${length.toFixed(2)} m long` : "", drawn.kind === "divider" ? "no wall: the space's zones" : ""].filter(Boolean).join(" · "))];
}

function drawnActions({ drawn, editable: may }) {
  if (!drawn) return null;
  const parts = [el("p", { class: "hint" }, "Kept with the floor through every re-read and revised drawing.")];
  if (may) {
    const b = el("button", { type: "button", class: "btn-sm btn-danger", "data-key": "delete" }, icon("trash-2", { size: 14 }), "Take it away");
    b.addEventListener("click", deleteItem);
    parts.push(el("div", { class: "sec-actions" }, el("span", { class: "grow" }), b));
  }
  return parts;
}

export function setupOtherSections() {
  header({ kind: "spaces", render: roomsHeader });
  section({ id: "rooms.type", kinds: ["spaces"], title: "Type", order: 10, render: bulkType });
  section({ id: "rooms.finishes", kinds: ["spaces"], title: "Finishes", order: 20, render: ({ spaces }) => finish.many(spaces) });
  section({ id: "rooms.actions", kinds: ["spaces"], title: "", order: 90, bare: true, render: bulkCheck });

  header({ kind: "asset", render: itemHeader });
  section({ id: "item.main", kinds: ["asset"], title: "Item", order: 10, render: itemMain });
  section({ id: "item.details", kinds: ["asset"], title: "Details", order: 20, render: itemDetails });
  section({ id: "item.where", kinds: ["asset"], title: "Where it stands", order: 30, render: itemWhere });
  section({ id: "item.history", kinds: ["asset"], title: "History", order: 40, open: false, render: ({ asset }) => asset && roomHistory(asset.id) });
  section({ id: "item.actions", kinds: ["asset"], title: "", order: 90, bare: true, render: itemActions });

  header({ kind: "opening", render: openingHeader });
  section({ id: "opening.size", kinds: ["opening"], title: "Size", order: 10, render: openingSize });
  section({ id: "opening.actions", kinds: ["opening"], title: "", order: 90, bare: true, render: openingActions });

  header({ kind: "drawn", render: drawnHeader });
  section({ id: "drawn.actions", kinds: ["drawn"], title: "", order: 90, bare: true, render: drawnActions });
}
