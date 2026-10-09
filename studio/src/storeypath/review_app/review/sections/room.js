// The inspector of one room (a space or a zone): why it needs a look, what it is (its
// type, as detected and by whom), its name and number, how many it seats, its finishes,
// the floors a lift or stairs serves, what stands in it, what is known of it, its history.
// Each field is saved when it is changed (Enter, or leaving it); ↺ puts it back as
// detected.

import { header, field, row, section } from "../inspector.js";
import { run } from "../commands.js";
import { el, icon } from "../dom.js";
import * as finish from "../finish.js";
import { copyId } from "../items.js";
import { accept, correct, GRADE_NAMES, itemsIn, saveSpace, seatsOf, setFlag, useDetected } from "../rooms.js";
import { toast } from "../notify.js";
import { advance, likelyTypes, reviewing } from "../reviewmode.js";
import { select, selectAsset } from "../selection.js";
import { code, color, state, title, typeName, typeOf } from "../state.js";
import * as vertical from "../vertical.js";
import { roomHistory } from "./history.js";

/** Who said what a room is: its detected type's source in words. */
export function detectedBy(source) {
  if (!source || source === "default") return { who: "", by: "none" };
  const [kind, rest = ""] = source.split(/:(.*)/s);
  if (kind === "label") return { who: "rules: its label", by: "rules", rule: rest };
  if (kind === "block") return { who: "rules: a block drawn in it", by: "rules", rule: rest };
  if (kind === "layer") return { who: "rules: its layer", by: "rules", rule: rest };
  if (kind === "model") return { who: "the language model", by: "model" };
  if (kind === "vision") return { who: "vision (the plan looked at)", by: "vision" };
  if (kind === "treads") return { who: "rules (the treads drawn in it)", by: "rules" };
  if (kind === "zones") return { who: "rules (its zones)", by: "rules" };
  if (kind === "doors") return { who: "rules (its doors)", by: "rules" };
  if (kind === "package") return { who: "a package", by: "package" };
  return { who: source, by: "rules" };
}

const BY_ICON = { rules: "list-checks", model: "sparkles", vision: "eye", none: "info", package: "package", person: "users" };

const reset = (label, fn) => {
  const b = el("button", { type: "button", class: "btn-ghost btn-icon btn-sm reset", "aria-label": label, "data-tip": label },
    icon("rotate-ccw", { size: 13 }));
  b.addEventListener("click", fn);
  return b;
};

function roomHeader({ space: s }) {
  const flags = [];
  if (s.reasons.length) flags.push(el("span", { class: "badge warn", "data-tip": s.reasons.join("; ") }, icon("triangle-alert", { size: 11 }), "Check"));
  else if (s.correction) flags.push(el("span", { class: "badge ok", "data-tip": "A person checked it" }, icon("check", { size: 11 }), "Checked"));
  if (s.ignored) flags.push(el("span", { class: "badge danger" }, "Deleted"));
  else if (s.hidden) flags.push(el("span", { class: "badge" }, "Hidden"));
  const parent = s.kind === "zone" ? state.byId.get(s.space_id) : null;
  const copy = el("button", { type: "button", class: "btn-ghost btn-icon btn-sm", "aria-label": "Copy its ID", "data-tip": "Copy its ID" },
    icon("copy", { size: 13 }));
  copy.addEventListener("click", () => copyId(s.id));
  const kind = el("div", { class: "ih-sub" }, s.kind === "zone" ? "Zone" : "Space", " · ", typeName(s.type));
  if (parent) {
    const up = el("button", { type: "button", class: "btn-link" }, title(parent));
    up.addEventListener("click", () => select(parent.id, { fly: true }));
    kind.append(" · in ", up);
  }
  return [
    el("div", { class: "ih-top" }, el("span", { class: "ih-swatch", style: `background:${color(s.type)}` }),
      el("h2", { class: "ih-title" }, title(s)), el("div", { class: "ih-flags" }, ...flags)),
    kind,
    el("div", { class: "ih-id" }, el("code", { "data-tip": "Its ID: the same through every re-read and revised drawing" }, s.id), copy),
  ];
}

function check({ space: s, editable }) {
  if (!s.reasons.length) return null;
  const parts = [el("ul", { class: "reasons" }, ...s.reasons.map((r) => el("li", {}, icon("triangle-alert", { size: 13 }), el("span", {}, r))))];
  if (reviewing()) {
    const likely = likelyTypes(s);
    parts.push(el("div", { class: "shortlist-head" }, "Likely types", el("span", { class: "muted" }, "press its number")),
      el("div", { class: "shortlist", role: "group", "aria-label": "Likely types" }, ...likely.map((t, i) => {
        const b = el("button", { type: "button", class: `shortlist-type${t.type === s.type ? " current" : ""}`, disabled: !editable || null,
          "data-tip": t.why, "data-key": String(i + 1) },
        el("kbd", {}, String(i + 1)), el("span", { class: "swatch", style: `background:${color(t.type)}` }), el("span", {}, typeName(t.type)));
        b.addEventListener("click", () => correctAndGo(s, t.type));
        return b;
      })));
  }
  if (editable) {
    const ok = el("button", { type: "button", class: "btn-primary btn-sm", "data-tip": "It is right as it is: off the list to review", "data-key": reviewing() ? "enter" : null },
      icon("check", { size: 14 }), s.type === "unspecified" ? "Accept (still needs a type)" : "Accept as it is");
    ok.disabled = s.type === "unspecified";
    ok.addEventListener("click", () => accept(s).then((done) => { if (done && reviewing()) advance(s.id); }));
    const del = el("button", { type: "button", class: "btn-sm btn-danger", "data-tip": "Not a room (a sliver, the outside): out of the plan, kept with its ID" },
      icon("trash-2", { size: 14 }), "Delete");
    del.addEventListener("click", () => setFlag(s.id, "ignored", true));
    parts.push(el("div", { class: "sec-actions" }, ok, del));
  }
  return parts;
}

function correctAndGo(s, type) {
  correct(s, { type }).then((done) => { if (done && reviewing()) advance(s.id); });
}

function classification({ space: s, editable }) {
  const select_ = el("select", { id: "ed-type", "aria-label": "Type", disabled: !editable || null });
  select_.append(...state.project.types.map((t) => el("option", { value: t }, typeName(t))));
  select_.value = s.type;
  select_.style.setProperty("--type-colour", color(s.type));
  select_.addEventListener("change", () => correct(s, { type: select_.value }));
  const d = detectedBy(s.detected.source);
  const corrected = s.correction?.type !== undefined && s.correction.type !== s.detected.type;
  const said = d.by === "none" ? "no rule, language model or vision typed it" : `by ${d.who}`;
  const note = el("div", { class: "detected", "data-tip": d.rule ? `The rule: ${d.rule}` : null },
    icon(corrected ? BY_ICON.person : BY_ICON[d.by] || "info", { size: 12 }),
    corrected ? el("span", {}, "Set by a person · detected ", el("strong", {}, typeName(s.detected.type)), d.by === "none" ? "" : ` ${said}`)
      : el("span", {}, d.by === "none" ? "Not detected: " : "Detected ", said));
  return [el("div", { class: "insp-row type-row" }, el("span", { class: "insp-label" }, "Type"),
    el("div", { class: "insp-control with-reset" }, el("span", { class: "type-select" }, el("span", { class: "swatch", style: `background:${color(s.type)}` }), select_),
      corrected && editable ? reset(`Back to ${typeName(s.detected.type)}, as detected`, () => correct(s, { type: s.detected.type })) : null)), note];
}

function names({ space: s, editable }) {
  const one = (key, label) => {
    const input = field(el("input", { id: `ed-${key}`, type: "text", value: s[key] || "", placeholder: `no ${key}`,
      "aria-label": label, disabled: !editable || null, autocomplete: "off" }), (v) => correct(s, { [key]: v }));
    const detected = s.detected[key] || "";
    const changed = (s[key] || "") !== detected;
    const note = el("div", { class: "detected" }, changed ? `Detected: ${detected || "none"}` : detected ? "As detected" : "None detected");
    return el("div", { class: "insp-row" }, el("span", { class: "insp-label" }, label),
      el("div", { class: "insp-control with-reset" }, input,
        changed && editable ? reset(`Back to ${detected ? `“${detected}”` : "none"}, as detected`, () => correct(s, { [key]: detected })) : null), note);
  };
  return [one("name", "Name"), one("number", "Number")];
}

function capacity({ space: s, editable }) {
  const seats = seatsOf(s);
  const input = field(el("input", { id: "ed-capacity", type: "number", min: "0", max: "10000", step: "1", inputmode: "numeric",
    value: s.capacity_set ?? "", placeholder: seats.workplaces ? String(seats.workplaces) : "—",
    "aria-label": "Capacity: how many people it is meant to seat", disabled: !editable || null }), (raw) => {
    const v = raw.trim() === "" ? null : Number(raw);
    if (v !== null && !(Number.isInteger(v) && v >= 0)) return toast("Capacity: a whole number from 0", true);
    if (v !== (s.capacity_set ?? null)) saveSpace({ capacity: v }, s.id);
  });
  const from = s.capacity_set != null
    ? (seats.workplaces ? `Set here; its desks seat ${seats.workplaces}` : "Set here")
    : seats.workplaces ? "From its desks: empty it to keep that" : "No desks in it: set it, or place desks";
  return row("Seats", el("div", { class: "with-unit" }, input, el("span", { class: "unit" }, "people")),
    el("div", { class: "detected" }, from + (seats.grade ? ` · laid out for ${GRADE_NAMES[seats.grade]}` : "")));
}

function contents({ space: s, editable }) {
  const items = itemsIn(s);
  const list = items.map((a) => {
    const t = typeOf(a.type);
    const b = el("button", { type: "button", class: "content-row" }, el("span", { class: "swatch", style: `background:${t?.color || "#8a8a8a"}` }),
      el("span", { class: "cr-name" }, t?.name_en || a.type), el("code", { class: "cr-id" }, a.id));
    b.addEventListener("click", () => selectAsset(a.id));
    return b;
  });
  const place = editable ? el("button", { type: "button", class: "btn-sm btn-ghost", "data-tip": "Place an item in it" }, icon("plus", { size: 13 }), "Place an item") : null;
  place?.addEventListener("click", () => run("tool.place"));
  return [list.length ? el("div", { class: "content-list" }, ...list) : el("p", { class: "empty" }, "Nothing placed in it"), place];
}

function about({ space: s }) {
  const parts = [
    row("In the drawing", el("span", { class: "value" }, s.drawing_label ? s.drawing_label.split("\n").join(" · ") : "No text"), null,
      { tip: "The text written in it on the drawing, exactly as written: exported as drawing_label, for other systems to match on beside the ID" }),
    row("Area", el("span", { class: "value" }, `${s.area} m²`)),
    row("Kind", el("span", { class: "value" }, s.kind === "zone" ? "A zone: a part of a space with no wall" : (s.zones || []).length ? "A space divided into zones" : "A space")),
    row("Short code", el("code", { class: "value" }, code(s.id))),
  ];
  return parts;
}

function actions({ space: s, editable }) {
  if (!editable) return null;
  const del = el("button", { type: "button", class: "btn-sm btn-danger", "data-key": "delete",
    "data-tip": s.ignored ? "Back in the plan, the lists and the 3D view" : "Not there, or not worth anything (a sliver, the outside): out of the plan, the lists and the 3D view; kept with its ID, and back with Show deleted" },
  icon(s.ignored ? "rotate-cw" : "trash-2", { size: 14 }), s.ignored ? "Restore" : "Delete");
  del.addEventListener("click", () => setFlag(s.id, "ignored", !s.ignored));
  const detected = s.correction && Object.keys(s.correction).length
    ? el("button", { type: "button", class: "btn-sm btn-ghost", "data-tip": "Take away its type, name and number corrections: as detected" }, icon("rotate-ccw", { size: 14 }), "Use detected")
    : null;
  detected?.addEventListener("click", () => useDetected(s));
  return el("div", { class: "sec-actions" }, detected, el("span", { class: "grow" }), del);
}

export function setupRoomSections() {
  header({ kind: "space", render: roomHeader });
  section({ id: "room.check", kinds: ["space"], title: "Why it needs a look", order: 5, render: check });
  section({ id: "room.type", kinds: ["space"], title: "Classification", order: 10, render: classification,
    summary: ({ space }) => typeName(space.type) });
  section({ id: "room.names", kinds: ["space"], title: "Name and number", order: 20, render: names,
    summary: ({ space }) => title(space) });
  section({ id: "room.capacity", kinds: ["space"], title: "Capacity", order: 30, render: capacity,
    summary: ({ space }) => (space.capacity != null ? `${space.capacity}` : "") });
  section({ id: "room.finishes", kinds: ["space"], title: "Finishes", order: 40, render: ({ space }) => finish.editor(space) });
  section({ id: "room.vertical", kinds: ["space"], title: "Floors it serves", order: 45, render: ({ space }) => vertical.editor(space) });
  section({ id: "room.contents", kinds: ["space"], title: "Contents", order: 50, render: contents,
    summary: ({ space }) => `${itemsIn(space).length}` });
  section({ id: "room.about", kinds: ["space"], title: "About it", order: 60, open: false, render: about });
  section({ id: "room.history", kinds: ["space"], title: "History", order: 70, open: false, render: ({ space }) => roomHistory(space.id) });
  section({ id: "room.actions", kinds: ["space"], title: "", order: 90, bare: true, render: actions });
}
