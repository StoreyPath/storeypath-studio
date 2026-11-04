// The inspector of several rooms at once, of an item, of a door, window or opening, and
// of a line drawn here.
//
// Several rooms: their type (each room's correction saved in turn: undone one at a
// time), their floor's and walls' finishes (one change), accepting those to review, and
// deleting them. An item: its type, turn, floor, details (a camera's view: its own, else
// its type's), the door it serves (a card reader's), the room it stands in. A door, window
// or opening: its sizes, and the items that serve it. A line drawn here: taken away.

import { editable } from "../access.js";
import { header, field, row, section } from "../inspector.js";
import { el, icon } from "../dom.js";
import { deleteItem, drawnLength, openingMeta, openingName, retype, sizeForm, turnChosenDoor } from "../drawing.js";
import * as finish from "../finish.js";
import { categories, changeAsset, copyId, doorOf, mirrorAsset, turnAsset } from "../items.js";
import { deleteAssets, groupAssets, groupOf, mirrorAssets, turnAssets, ungroupAssets } from "../groups.js";
import { categoryName } from "../../categories.js";
import { kbd, keysOf, keyText } from "../keys.js";
import { toast } from "../notify.js";
import { accept, correct, forEach, setFlag } from "../rooms.js";
import { select, selectAsset, selectItem } from "../selection.js";
import { color, floorTitle, readable, state, title, typeName, typeOf, typeOptions, units, view3d, within } from "../state.js";
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
    ...typeOptions()); // (on a layout, the land's types first)
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
    ...categories().map(([category, types]) => el("optgroup", { label: categoryName(category) },
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
  const mirror = (axis, ico, tip) => {
    const b = el("button", { type: "button", class: "btn-icon btn-sm", "aria-label": tip, "data-tip": tip, disabled: !may || null }, icon(ico, { size: 14 }));
    b.addEventListener("click", () => mirrorAsset(a, axis));
    return b;
  };
  const floor = el("select", { id: "as-floor", "aria-label": "Floor", disabled: !may || null },
    ...state.project.floors.filter((f) => f.id === state.floor.id || editable(f.id))
      .map((f) => el("option", { value: f.id }, f.layout ? floorTitle(f) : `${f.building} · ${f.name}`)));
  floor.value = state.floor.id;
  floor.addEventListener("change", () => { if (floor.value !== state.floor.id) changeAsset(a, { floor_id: floor.value }); });
  const about = [t && `${t.width} × ${t.depth} m`, t?.mount === "ceiling" ? "on the ceiling" : t?.mount === "wall" ? "on a wall" : ""].filter(Boolean).join(" · ");
  return [row("Type", type, about ? el("div", { class: "detected" }, about) : null),
    row("Turned", el("div", { class: "with-unit" }, turn, el("span", { class: "unit" }, "°"), by(90, "rotate-ccw", `Turn 90° (${key("item.turn")})`),
      by(-90, "rotate-cw", `Turn back 90° (${key("item.turn-back")})`),
      mirror("x", "flip-vertical-2", `Mirror left to right (${key("item.mirror-x")})`),
      mirror("y", "flip-horizontal-2", `Mirror top to bottom (${key("item.mirror-y")})`))),
    row("Floor", floor, el("div", { class: "detected" }, "Carried to another floor, it keeps its ID")),
    may ? el("p", { class: "hint" }, "Drag it to move it: it lines up with walls and items and stays in its room (Alt: freely). ",
      kbd("r"), " turns it 90°, ", kbd(","), " ", kbd("."), " by 15°, ", kbd("shift+h"), " ", kbd("shift+v"), " mirror it",
      view3d.mode === "walk" ? "." : ", the arrows move it.") : null];
}

function itemDetails({ asset: a, editable: may }) {
  if (!a) return null;
  const t = typeOf(a.type);
  const own = (t?.fields || []).filter((f) => f.owner === "storeypath");
  const others = (t?.fields || []).filter((f) => f.owner !== "storeypath").map((f) => f.name_en);
  if (!own.length && !others.length) return null;
  // a camera's own view, where it is given; else its type's (shown, greyed)
  const typed = { view_deg: t?.view_deg, range_m: t?.range_m };
  const rows = own.map((f) => {
    const input = f.kind === "choice"
      ? el("select", { disabled: !may || null }, el("option", { value: "" }, "—"), ...f.choices.map((c) => el("option", { value: c }, c)))
      : el("input", { type: f.kind === "number" ? "number" : f.kind === "color" ? "color" : "text", step: "any", disabled: !may || null,
        placeholder: typed[f.key] ?? null });
    input.value = a.values?.[f.key] ?? (f.kind === "color" ? "#000000" : "");
    input.addEventListener("change", () => changeAsset(a, { values: { ...a.values, [f.key]: input.value } }));
    return row(f.name_en, input);
  });
  if (own.some((f) => typed[f.key] != null)) {
    rows.push(el("p", { class: "hint" }, `Empty: as its type, ${t.view_deg ?? "?"}° across and ${t.range_m ?? "?"} m. `,
      "What it covers shows while it is chosen; every camera's: ", ...kbd("mod+k"), " Show what every camera covers."));
  }
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

/** The door an item serves (a card reader's, a door controller's: format 0.10): one of the
 * floor's doors and openings, the nearest first; placed, it was linked to the nearest. */
function itemDoor({ asset: a, editable: may }) {
  const t = a && typeOf(a.type);
  if (!t?.serves_door) return null;
  const at = (d) => d.middle || d.point;
  const far = (d) => Math.hypot(at(d)[0] - a.x, at(d)[1] - a.y);
  const near = (state.floor?.doors || []).filter((d) => d.type !== "window" && !d.ignored).sort((p, q) => far(p) - far(q)).slice(0, 12);
  const linked = doorOf(a);
  if (linked && !near.includes(linked)) near.push(linked);
  const between = (d) => (d.connects || []).map((id) => state.byId.get(id)).filter(Boolean).map(title).join(" · ");
  const pick = el("select", { id: "as-door", "aria-label": "The door it serves", disabled: !may || null },
    el("option", { value: "" }, "None"),
    a.door_id && !linked ? el("option", { value: a.door_id }, "Its door, deleted since") : null,
    ...near.map((d) => el("option", { value: d.id }, [openingName(d), `${far(d).toFixed(1)} m`, between(d)].filter(Boolean).join(" · "))));
  pick.value = a.door_id || "";
  pick.addEventListener("change", () => changeAsset(a, { door_id: pick.value || null }));
  const show = el("button", { type: "button", class: "btn-sm", disabled: !linked || null, "data-tip": "Choose the door: its readers are shown linked" },
    icon("door-open", { size: 14 }), "Show the door");
  show.addEventListener("click", () => linked && selectItem({ kind: "door", id: linked.id }));
  return [row("Door", pick),
    el("p", { class: "hint" }, linked ? `The door it opens, ${far(linked).toFixed(1)} m from it: the line on the plan.`
      : a.door_id ? "Its door was deleted: link it to another, or to none." : "Linked to no door: choose the one it opens."),
    el("div", { class: "sec-actions" }, show)];
}

/** The items that serve a door (its card readers, its controller): each, to choose it. */
function openingServed({ opening: d }) {
  if (!d || d.type === "window") return null;
  const by = (state.floor?.items || []).filter((a) => a.door_id === d.id && !a.retired);
  if (!by.length) return null;
  return el("ul", { class: "group-list" }, by.map((a) => {
    const t = typeOf(a.type);
    const b = el("button", { type: "button", class: "group-item", "data-tip": "Choose it" },
      el("span", { class: "swatch", style: `background:${t?.color || "#8a8a8a"}` }), el("span", { class: "grow" }, t ? t.name_en : a.type),
      el("code", { class: "tag" }, a.id));
    b.addEventListener("click", () => selectAsset(a.id, { alone: true }));
    return el("li", {}, b);
  }));
}

/** An item of a group, chosen alone: the group it is part of, to choose it whole, or to
 * take this item out of it (it keeps its ID). */
function itemGroup({ asset: a, editable: may }) {
  if (!a?.group) return null;
  const all = groupOf(a);
  const whole = el("button", { type: "button", class: "btn-sm" }, icon("layers", { size: 14 }), "Choose the group");
  whole.addEventListener("click", () => selectAsset(a.id));
  const out = el("button", { type: "button", class: "btn-sm", disabled: !may || null,
    "data-tip": "Out of its group: moved and turned alone from now on; its ID stays" }, "Take it out");
  out.addEventListener("click", () => ungroupAssets([a]));
  return [el("p", { class: "hint" }, `One of a group of ${all.length} items, placed or grouped together: a click chooses them all; a click on one of them then, this one alone (to move it by itself).`),
    el("div", { class: "sec-actions" }, whole, el("span", { class: "grow" }), out)];
}

// ---- items chosen together: a group, or several ---------------------------------------------

function itemsHeader({ assets, group }) {
  if (!assets.length) return null;
  const kinds = new Map();
  for (const a of assets) {
    const name = typeOf(a.type)?.name_en || a.type;
    kinds.set(name, (kinds.get(name) || 0) + 1);
  }
  return [el("div", { class: "ih-top" }, icon("layers", { size: 18, cls: "ih-icon" }),
    el("h2", { class: "ih-title" }, group ? `A group of ${assets.length} items` : `${assets.length} items chosen`),
    assets.some((a) => a.retired) ? el("div", { class: "ih-flags" }, el("span", { class: "badge danger" }, "Deleted")) : null),
  el("div", { class: "ih-sub" }, [...kinds].map(([name, n]) => (n > 1 ? `${n} × ${name}` : name)).join(" · ")),
  el("div", { class: "ih-sub" }, group ? "Each with its own ID: moved, turned and deleted together"
    : "Each with its own ID: grouped, they move and turn together")];
}

/** The items chosen, each: a click chooses it alone. */
function itemsList({ assets }) {
  if (!assets.length) return null;
  return el("ul", { class: "group-list" }, assets.map((a) => {
    const t = typeOf(a.type);
    const b = el("button", { type: "button", class: "group-item", "data-tip": "Choose this item alone" },
      el("span", { class: "swatch", style: `background:${t?.color || "#8a8a8a"}` }), el("span", { class: "grow" }, t ? t.name_en : a.type),
      el("code", { class: "tag" }, a.id));
    b.addEventListener("click", () => selectAsset(a.id, { alone: true }));
    return el("li", {}, b);
  }));
}

function itemsActions({ assets, group, editable: may }) {
  if (!assets.length || !may) return null;
  const btn = (label, ico, fn, attrs = {}) => {
    const b = el("button", { type: "button", class: "btn-sm", ...attrs }, icon(ico, { size: 14 }), label);
    b.addEventListener("click", fn);
    return b;
  };
  const deleted = assets.every((a) => a.retired);
  return el("div", { class: "sec-actions" },
    group ? btn("Ungroup", "square-split-horizontal", () => ungroupAssets(assets), { "data-key": keysOf("item.ungroup")[0] || null })
      : btn("Group", "layers", () => groupAssets(assets), { "data-key": keysOf("item.group")[0] || null }),
    btn("Turn 90°", "rotate-ccw", () => turnAssets(assets, 90)),
    btn("Mirror", "flip-vertical-2", () => mirrorAssets(assets, "x"), { "aria-label": "Mirror them left to right", "data-tip": `Mirror them left to right (${keyText(keysOf("item.mirror-x")[0] || "")})` }),
    btn("Mirror", "flip-horizontal-2", () => mirrorAssets(assets, "y"), { "aria-label": "Mirror them top to bottom", "data-tip": `Mirror them top to bottom (${keyText(keysOf("item.mirror-y")[0] || "")})` }),
    el("span", { class: "grow" }),
    btn(deleted ? "Restore" : "Delete", deleted ? "rotate-cw" : "trash-2", () => deleteAssets(assets, !deleted),
      { class: deleted ? "btn-sm" : "btn-sm btn-danger", "data-key": "delete" }));
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
  el("div", { class: "ih-sub" }, openingMeta(d) || (d.type === "window" ? "Window" : d.type === "door" ? "Door"
    : d.type === "gate" ? "A gate: a way through the boundary walls" : "An opening: a way through, no door")),
  el("div", { class: "ih-id" }, el("code", {}, d.id))];
}

function openingSize({ opening: d, editable: may }) {
  if (!d || d.ignored) return null;
  if (!readable()) return el("p", { class: "hint" }, "Its size changes with the floor's drawing: this floor has none yet");
  const form = sizeForm(d);
  if (!may) for (const e of form.elements) e.disabled = true;
  return [form, el("p", { class: "hint" }, "Empty: as the drawing (or its schedule) has it. The floor is read again with the new size."),
    may && !state.floor?.layout ? el("p", { class: "hint" }, "Drag it along its wall to move it, or the arrows (Shift: 50 cm).") : null];
}

/** What an opening is, a door, a window or a way through: one the drawing reads wrong (a
 * glazed door drawn like a window) said right, kept through every re-read. */
function openingType({ opening: d, editable: may }) {
  if (!d || d.ignored || state.floor?.layout) return null; // (on a layout every opening is a gate)
  if (!d.drawn && !readable()) return null;
  const pick = el("div", { class: "segmented", role: "radiogroup", "aria-label": "What it is" },
    ...[["door", "Door"], ["window", "Window"], ["opening", "Opening"]].map(([type, name]) => {
      const b = el("button", { type: "button", role: "radio", "aria-checked": String(d.type === type), disabled: !may || null,
        "data-tip": type === "opening" ? "A way through, no door" : `A ${type}` }, name);
      b.addEventListener("click", () => { if (d.type !== type) retype(d, type); });
      return b;
    }));
  if (d.resize?.type && may) {
    const back = el("button", { type: "button", class: "btn-link" }, "as the drawing reads it");
    back.addEventListener("click", () => retype(d, null));
    return [pick, el("p", { class: "hint" }, "Said here; back to ", back)];
  }
  const said = el("p", { class: "hint" }, d.drawn ? "Drawn here." : "Read wrong? Say what it is: kept through every re-read.");
  if (d.type !== "door" || !may || !d.span) return [pick, said];
  const turn = el("button", { type: "button", class: "btn-sm", "data-tip": "The other jamb, then the other side (R; Shift+R back)" },
    icon("rotate-cw", { size: 14 }), d.swings?.length ? "Turn it" : "Hang it");
  turn.addEventListener("click", () => turnChosenDoor(1));
  return [pick, said, row("Opens", el("div", { class: "with-unit" }, turn, el("span", { class: "unit" }, kbd("r"))))];
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
  section({ id: "item.door", kinds: ["asset"], title: "The door it serves", order: 25, render: itemDoor });
  section({ id: "item.where", kinds: ["asset"], title: "Where it stands", order: 30, render: itemWhere });
  section({ id: "item.history", kinds: ["asset"], title: "History", order: 40, open: false, render: ({ asset }) => asset && roomHistory(asset.id) });
  section({ id: "item.group", kinds: ["asset"], title: "Its group", order: 35, render: itemGroup });
  section({ id: "item.actions", kinds: ["asset"], title: "", order: 90, bare: true, render: itemActions });

  header({ kind: "assets", render: itemsHeader });
  section({ id: "items.list", kinds: ["assets"], title: "Items", order: 10, render: itemsList });
  section({ id: "items.actions", kinds: ["assets"], title: "", order: 90, bare: true, render: itemsActions });

  header({ kind: "opening", render: openingHeader });
  section({ id: "opening.type", kinds: ["opening"], title: "What it is", order: 5, render: openingType });
  section({ id: "opening.size", kinds: ["opening"], title: "Size", order: 10, render: openingSize });
  section({ id: "opening.served", kinds: ["opening"], title: "Served by", order: 20, render: openingServed });
  section({ id: "opening.actions", kinds: ["opening"], title: "", order: 90, bare: true, render: openingActions });

  header({ kind: "drawn", render: drawnHeader });
  section({ id: "drawn.actions", kinds: ["drawn"], title: "", order: 90, bare: true, render: drawnActions });
}
