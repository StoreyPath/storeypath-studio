// Item types: the catalogue of furniture and equipment people place on floors (catalogue.py),
// the organization's, one for every project of this Studio. Everyone sees it; admins, and
// those an admin let ("Item types", users.js), add types, change them and retire them
// (a type is never taken out: its code stays with the items that have it), and take types
// from a file: a catalogue (.json), a building's package (.storeypath: the types its items
// use, or every one) or a project file. The catalogue, or the types chosen, is saved as a
// .json for another Studio, or another system, to take.

import { sentAway } from "./account.js";
import { SHAPES, shapeOf } from "./fit.js";
import { assetShape, symbolBox } from "./itemshape.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const CATEGORIES = [["furniture", "Furniture"], ["equipment", "Equipment"], ["appliance", "Appliances"]];
const MOUNTS = [["floor", "Floor"], ["wall", "Wall"], ["ceiling", "Ceiling"]];
const GRADES = [["president", "President"], ["c_level", "C-level"], ["director", "Director"], ["manager", "Manager"],
  ["section_head", "Head of section"], ["senior", "Senior staff"], ["junior", "Junior staff"]];
const FIELD_KINDS = [["text", "Text"], ["number", "Number"], ["choice", "Choice"], ["color", "Colour"]];
/** How a type may be drawn (fit.js SHAPES), what each looks like, and a size to show it at. */
const SHAPE_ABOUT = {
  desk: ["Desk", "its chair, and what goes with its grade", { width: 1.6, depth: 0.8, grade: "senior" }],
  meeting_table: ["Meeting table", "as many chairs round it as its size seats", { width: 2.4, depth: 1.2 }],
  sofa: ["Sofa", "its seat between its arms", { width: 2, depth: 0.9 }],
  screen: ["Screen", "on a wall, facing the room", { width: 1.4, depth: 0.1 }],
  copier: ["Copier", "a machine on a cabinet", { width: 1.2, depth: 0.7 }],
  bed: ["Bed", "its headboard and pillows", { width: 1.7, depth: 2.1 }],
  kiosk: ["Kiosk", "a screen on a post, facing its front", { width: 0.6, depth: 0.45 }],
  access_point: ["Access point", "a disc under the ceiling", { width: 0.4, depth: 0.4, mount: "ceiling" }],
  box: ["Box", "its size, in its colour", { width: 1, depth: 0.6 }],
};
const CODE_RE = /^[A-Z0-9]+(-[A-Z0-9]+)*$/;
const KEY_RE = /^[a-z][a-z0-9_]{0,31}$/;
// what a new type starts as (catalogue.py ItemType's defaults)
const BLANK = { code: "", name_en: "", name_ar: "", category: "furniture", width: 1, depth: 0.6, height: 0.75, mount: "floor",
  elevation: null, color: "#8a8a8a", shape: "box", workplaces: 0, grade: null, fields: [], retired: false };
// what is compared between two types of a code (a file's and this Studio's), and how it is called
const COMPARED = [["name_en", "name"], ["name_ar", "Arabic name"], ["category", "category"], ["width", "size"],
  ["depth", "size"], ["height", "size"], ["mount", "mount"], ["elevation", "height off the floor"], ["color", "colour"],
  ["shape", "shape"], ["workplaces", "workplaces"], ["grade", "grade"], ["fields", "fields"], ["retired", "retired"]];

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

async function api(path, body, { raw } = {}) {
  const init = raw !== undefined
    ? { method: "PUT", headers: { "X-StoreyPath": "1", "Content-Type": "application/octet-stream" }, body: raw }
    : body === undefined ? {}
      : { method: "POST", headers: { "X-StoreyPath": "1", "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(`/api/${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (sentAway(res, data)) throw new Error("log in again");
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

const metres = (v) => `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const sizeOf = (t) => `${metres(t.width)} × ${metres(t.depth)} × ${metres(t.height)} m`;
const shapeName = (t) => (t.shape ? SHAPE_ABOUT[t.shape]?.[0] ?? t.shape : `${SHAPE_ABOUT[shapeOf(t)][0]}, by its code`);
const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** A type's symbol on the plan, as Review and the viewers draw it, framed whole: its front
 * towards the bottom (``px``: its larger side, in pixels). */
export function symbol(t, px = 44) {
  const [x0, y0, x1, y1] = symbolBox(t), pad = Math.max(x1 - x0, y1 - y0) * 0.08 + 0.05;
  const box = [x0 - pad, -y1 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad]; // (turned over: its front, -y, down)
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("viewBox", box.map((v) => Math.round(v * 1000) / 1000).join(" "));
  s.setAttribute("class", "item-symbol");
  s.setAttribute("aria-hidden", "true");
  const scale = px / Math.max(box[2], box[3]);
  s.setAttribute("width", String(Math.max(8, Math.round(box[2] * scale))));
  s.setAttribute("height", String(Math.max(8, Math.round(box[3] * scale))));
  const g = document.createElementNS(SVG_NS, "g");
  g.setAttribute("transform", "scale(1 -1)");
  g.append(assetShape({ x: 0, y: 0, rotation: 0 }, t, "asset"));
  s.append(g);
  return s;
}

/** What differs between a file's type and this Studio's of the same code: the names of
 * what differs (none: the same). */
function differences(theirs, ours) {
  const out = [];
  for (const [key, name] of COMPARED) if (!same(theirs[key], ours[key]) && !out.includes(name)) out.push(name);
  return out;
}

/** A catalogue saved as a .json file (``types``: those chosen, or every one). */
function download(types, name) {
  const body = JSON.stringify({ format: "storeypath-catalogue", format_version: 1, types }, null, 1) + "\n";
  const a = el("a", { href: URL.createObjectURL(new Blob([body], { type: "application/json" })), download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** The Item types page, into ``page``; ``me``: who is looking (whether they may change the
 * types); ``toast`` says how things went. */
export async function itemTypesPage(page, me, toast) {
  const may = Boolean(me?.local || me?.role === "admin" || (me?.capabilities || []).includes("catalogue"));
  const view = { types: [], query: "", category: "all", retired: false, open: null, chosen: new Set() };
  // the type being edited: a copy (``code`` its code here; null: a new one)
  let edit = null;

  const load = async () => {
    view.types = (await api("catalogue")).types;
  };

  /** One type saved: this Studio's catalogue as it is now, with ``t`` in it (in place of the
   * type ``code``, or added), saved whole (catalogue.py: a type is never taken out). */
  const saveType = async (t, code) => {
    const now = (await api("catalogue")).types;
    const at = now.findIndex((x) => x.code === (code ?? t.code));
    if (code === null && at >= 0) throw new Error(`a type ${t.code} is there already: its code is taken`);
    if (at >= 0) now[at] = t;
    else now.push(t);
    view.types = (await api("catalogue", { types: now })).types;
  };

  // ---- the list ---------------------------------------------------------------------------

  const listBox = el("div", { class: "it-list", role: "list", "aria-label": "Item types" });
  const counts = el("span", { class: "muted small", "data-testid": "it-count" });
  const chosenBar = el("div", { class: "it-chosen row", hidden: true });

  const shown = () => view.types.filter((t) => (view.retired || !t.retired)
    && (view.category === "all" || t.category === view.category)
    && (!view.query || [t.code, t.name_en, t.name_ar].some((s) => (s || "").toLowerCase().includes(view.query))));

  const renderChosen = () => {
    const n = view.chosen.size;
    chosenBar.hidden = !n;
    chosenBar.replaceChildren(
      el("span", { class: "grow small" }, `${n} chosen`),
      el("button", { type: "button", class: "btn-sm", onclick: () => {
        download(view.types.filter((t) => view.chosen.has(t.code)), "item-types.json");
      } }, `Save ${n} as a file`),
      el("button", { type: "button", class: "btn-sm btn-ghost", onclick: () => {
        view.chosen.clear();
        renderList();
      } }, "Clear"));
  };

  const row = (t) => {
    const box = el("input", { type: "checkbox", checked: view.chosen.has(t.code), "aria-label": `Choose ${t.name_en}`,
      onclick: (e) => e.stopPropagation(), onchange: () => {
        if (box.checked) view.chosen.add(t.code);
        else view.chosen.delete(t.code);
        renderChosen();
      } });
    const open = () => openEditor(t);
    return el("div", { class: `it-row${view.open === t.code ? " open" : ""}${t.retired ? " retired" : ""}`, role: "listitem",
      "data-code": t.code, tabindex: "0", onclick: open, onkeydown: (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      } },
    box,
    el("span", { class: "it-symbol" }, symbol(t)),
    el("span", { class: "it-name" },
      el("strong", {}, t.name_en || t.code),
      t.name_ar ? el("span", { class: "it-ar", dir: "rtl", lang: "ar" }, t.name_ar) : null,
      el("code", { class: "muted small" }, t.code)),
    el("span", { class: "it-meta small muted" },
      el("span", {}, sizeOf(t)),
      el("span", {}, shapeName(t), t.mount !== "floor" ? ` · on the ${t.mount}` : "")),
    el("span", { class: "it-badges" },
      el("span", { class: "swatch", style: `background:${t.color}`, title: t.color }),
      t.workplaces ? el("span", { class: "badge", title: "How many people work at one: counted into a room's capacity" },
        `${t.workplaces} ${t.workplaces === 1 ? "workplace" : "workplaces"}`) : null,
      t.grade ? el("span", { class: "badge" }, GRADES.find(([g]) => g === t.grade)?.[1] ?? t.grade) : null,
      t.retired ? el("span", { class: "badge warn" }, "Retired") : null));
  };

  const renderList = () => {
    const list = shown();
    counts.textContent = `${list.length} of ${view.types.length} types`;
    const groups = CATEGORIES.map(([c, name]) => [name, list.filter((t) => t.category === c)]).filter(([, ts]) => ts.length);
    listBox.replaceChildren(...(groups.length ? groups.map(([name, ts]) => el("section", { class: "it-group" },
      el("h3", {}, name, el("span", { class: "muted" }, ` ${ts.length}`)), ts.map(row)))
      : [el("p", { class: "empty" }, view.types.length ? "No type matches." : "No item types yet.")]));
    renderChosen();
  };

  // ---- the editor -------------------------------------------------------------------------

  const editor = el("aside", { class: "card it-editor", "aria-label": "Item type" });

  const renderEmpty = () => editor.replaceChildren(
    el("div", { class: "it-empty" },
      el("p", {}, "Choose a type to see it", may ? ", change it or retire it." : "."),
      may ? el("p", { class: "muted small" }, "Or add a new one: a code that is its identity for good, its names, how it is "
        + "drawn and its size. Every project of this Studio, and every package, carries it.") : null));

  function openEditor(t) {
    view.open = t ? t.code : null;
    edit = { code: t ? t.code : null, t: structuredClone(t ? { ...BLANK, ...t } : BLANK) };
    renderList();
    renderEditor();
    // (beside the list it stays in view; under it, on a narrow screen, it is shown)
    if (window.matchMedia?.("(max-width: 960px)").matches) editor.scrollIntoView?.({ block: "start" });
  }

  function renderEditor() {
    const t = edit.t, isNew = edit.code === null, off = !may;
    const preview = el("div", { class: "it-preview" });
    const drawPreview = () => preview.replaceChildren(symbol(t, 150));
    const changed = () => {
      drawPreview();
      problems.replaceChildren(...check(t, isNew).map((p) => el("li", {}, p)));
      save.disabled = off || check(t, isNew).length > 0;
    };
    const input = (key, attrs = {}, parse = (v) => v) => el("input", { value: t[key] ?? "", disabled: off, ...attrs,
      oninput: (e) => {
        t[key] = parse(e.target.value);
        changed();
      } });
    const num = (key, attrs = {}) => input(key, { type: "number", step: "0.05", min: "0", ...attrs },
      (v) => (v === "" ? (key === "elevation" ? null : 0) : Number(v)));
    const segmented = (key, options, after = () => {}) => {
      const seg = el("div", { class: "segmented", role: "radiogroup" }, options.map(([v, label]) => el("button", {
        type: "button", role: "radio", "aria-checked": String(t[key] === v), disabled: off, "data-value": v,
        onclick: () => {
          t[key] = v;
          for (const b of seg.children) b.setAttribute("aria-checked", String(b.dataset.value === v));
          after();
          changed();
        } }, label)));
      return seg;
    };

    // how it is drawn: a card each, its symbol at a size of its own
    const shapes = el("div", { class: "it-shapes", role: "radiogroup", "aria-label": "How it is drawn" });
    const drawShapes = () => shapes.replaceChildren(...[...SHAPES, null].map((s) => {
      const [name, about] = s ? SHAPE_ABOUT[s] : ["By its code", `as its code's first part says: now ${SHAPE_ABOUT[shapeOf({ code: t.code })][0].toLowerCase()}`];
      const sample = s ? { ...BLANK, color: t.color, ...SHAPE_ABOUT[s][2], shape: s } : { ...t, shape: null };
      return el("button", { type: "button", role: "radio", class: "it-shape", "aria-checked": String((t.shape ?? null) === s),
        "data-shape": s ?? "", disabled: off, title: about, onclick: () => {
          t.shape = s;
          if (s === "access_point") t.mount = "ceiling";
          renderEditor();
        } }, symbol(sample, 40), el("span", {}, name));
    }));
    drawShapes();

    const gradeRow = shapeOf(t) === "desk" || t.grade ? el("label", { class: "field" }, "Grade: who a desk is for",
      el("select", { disabled: off, onchange: (e) => {
        t.grade = e.target.value || null;
        changed();
      } }, el("option", { value: "" }, "None"), GRADES.map(([g, name]) => el("option", { value: g, selected: t.grade === g }, name))),
      el("span", { class: "muted small" }, "What goes with it (a return, a credenza, visitors' chairs); a room takes its highest desk's.")) : null;

    // its fields: the details each item of it carries
    const fieldRows = el("div", { class: "it-fields" });
    const drawFields = () => fieldRows.replaceChildren(
      ...(t.fields.length ? [el("div", { class: "it-field head small muted" }, "Key", "Name", "Arabic name", "Kind", "Who enters it", "")] : []),
      ...t.fields.map((f, i) => {
        const set = (key) => (e) => {
          f[key] = key === "choices" ? e.target.value.split(",").map((c) => c.trim()).filter(Boolean) : e.target.value;
          if (key === "kind") drawFields();
          changed();
        };
        return el("div", { class: "it-field" },
          el("input", { value: f.key, disabled: off, placeholder: "e.g. model", "aria-label": "Key", oninput: set("key") }),
          el("input", { value: f.name_en, disabled: off, placeholder: "Model", "aria-label": "Name", oninput: set("name_en") }),
          el("input", { value: f.name_ar || "", disabled: off, dir: "rtl", lang: "ar", "aria-label": "Arabic name", oninput: set("name_ar") }),
          el("select", { disabled: off, "aria-label": "Kind", onchange: set("kind") },
            FIELD_KINDS.map(([k, name]) => el("option", { value: k, selected: (f.kind || "text") === k }, name))),
          el("select", { disabled: off, "aria-label": "Who enters it", onchange: set("owner") },
            el("option", { value: "storeypath", selected: (f.owner || "storeypath") === "storeypath" }, "StoreyPath"),
            el("option", { value: "system", selected: f.owner === "system", title: "The system that manages the asset (wayfinder): never in a package" },
              "Managing system")),
          off ? el("span") : el("button", { type: "button", class: "btn-ghost btn-sm btn-icon", "aria-label": `Remove ${f.key || "this field"}`,
            title: "Remove this field", onclick: () => {
              t.fields.splice(i, 1);
              drawFields();
              changed();
            } }, "✕"),
          (f.kind || "text") === "choice" ? el("input", { class: "it-choices", value: (f.choices || []).join(", "), disabled: off,
            placeholder: "The choices, separated by commas", "aria-label": "Choices", oninput: set("choices") }) : null);
      }),
      off ? null : el("button", { type: "button", class: "btn-sm", onclick: () => {
        t.fields.push({ key: "", name_en: "", name_ar: "", kind: "text", choices: [], owner: "storeypath" });
        drawFields();
        changed();
      } }, "Add a field"));
    drawFields();

    // its colour: picked, or typed as #rrggbb (each says it to the other)
    const picker = el("input", { type: "color", value: t.color, disabled: off, "aria-label": "Colour", oninput: (e) => {
      t.color = e.target.value;
      hex.value = t.color;
      changed();
      drawShapes();
    } });
    const hex = el("input", { value: t.color, disabled: off, maxlength: "7", spellcheck: "false", "aria-label": "Colour as #rrggbb",
      oninput: (e) => {
        t.color = e.target.value.trim().toLowerCase();
        if (/^#[0-9a-f]{6}$/.test(t.color)) picker.value = t.color;
        changed();
      } });

    const problems = el("ul", { class: "it-problems", "aria-live": "polite" });
    const save = el("button", { type: "submit", class: "primary", hidden: off }, isNew ? "Add type" : "Save");
    const form = el("form", { class: "it-form", onsubmit: async (e) => {
      e.preventDefault();
      if (check(t, isNew).length) return;
      try {
        await saveType(clean(t), edit.code);
        toast(isNew ? `${t.code} added` : `${t.code} saved`);
        view.open = t.code;
        edit = { code: t.code, t: structuredClone(view.types.find((x) => x.code === t.code)) };
        renderList();
        renderEditor();
      } catch (err) {
        toast(err.message, true);
      }
    } },
    el("div", { class: "it-head" }, preview,
      el("div", { class: "grow" },
        el("h2", {}, isNew ? "New item type" : t.name_en || t.code),
        isNew ? null : el("code", { class: "muted" }, t.code),
        t.retired ? el("p", { class: "small" }, el("span", { class: "badge warn" }, "Retired"),
          " Not offered for placing any more; the items that have it keep it.") : null)),
    isNew ? el("label", { class: "field" }, "Code: its identity, kept for good",
      input("code", { required: true, placeholder: "e.g. LOCKER-TALL", autocapitalize: "characters", spellcheck: "false",
        maxlength: "40", "data-testid": "it-code" }, (v) => v.toUpperCase().replace(/\s+/g, "-")),
      el("span", { class: "muted small" }, "Capital letters and digits, in parts joined by - (DESK-MANAGER). It is never given to another type.")) : null,
    el("div", { class: "it-two" },
      el("label", { class: "field" }, "Name", input("name_en", { required: true, placeholder: "e.g. Tall locker", "data-testid": "it-name" })),
      el("label", { class: "field" }, "Arabic name", input("name_ar", { dir: "rtl", lang: "ar" }))),
    el("div", { class: "field" }, el("span", {}, "Category"), segmented("category", CATEGORIES.map(([c, n]) => [c, n === "Appliances" ? "Appliance" : n]))),
    el("div", { class: "field" }, el("span", {}, "How it is drawn"), shapes),
    el("div", { class: "it-three" },
      el("label", { class: "field" }, "Width (m)", num("width", { "data-testid": "it-width" })),
      el("label", { class: "field" }, "Depth (m)", num("depth")),
      el("label", { class: "field" }, t.mount === "floor" ? "Height (m)" : "Size up (m)", num("height"))),
    el("div", { class: "it-two" },
      el("div", { class: "field" }, el("span", {}, "Mounted on"), segmented("mount", MOUNTS, () => renderEditor())),
      el("label", { class: "field" }, "Off the floor (m)", num("elevation", {
        placeholder: t.mount === "floor" ? "on the floor" : t.mount === "wall" ? "1.2" : "under the ceiling" }))),
    el("div", { class: "it-two" },
      el("label", { class: "field" }, "Colour", el("span", { class: "row it-colour" }, picker, hex)),
      el("label", { class: "field" }, "Workplaces", num("workplaces", { step: "1", max: "100" }),
        el("span", { class: "muted small" }, "How many people work at one (a desk: 1): counted into a room's capacity."))),
    gradeRow,
    el("div", { class: "field" }, el("span", {}, "Fields: what each item of it carries"),
      el("span", { class: "muted small" }, "Entered in StoreyPath (a model, a colour) or kept by the system that manages the asset (an access point's network)."),
      fieldRows),
    problems,
    el("div", { class: "row it-actions" },
      off || isNew ? null : el("button", { type: "button", class: t.retired ? "" : "btn-danger", title: t.retired
        ? "Offered for placing again" : "No longer offered for placing; the items that have it keep it, and its code is never given to another",
      onclick: async () => {
        try {
          await saveType({ ...clean(t), retired: !t.retired }, edit.code);
          toast(t.retired ? `${t.code} restored` : `${t.code} retired`);
          edit = { code: t.code, t: structuredClone(view.types.find((x) => x.code === t.code)) };
          renderList();
          renderEditor();
        } catch (err) {
          toast(err.message, true);
        }
      } }, t.retired ? "Restore" : "Retire"),
      off || isNew ? null : el("button", { type: "button", class: "btn-ghost", title: "A new type, starting as this one",
        onclick: () => {
          const copy = { ...clean(t), code: `${t.code}-2`.slice(0, 40), retired: false };
          openEditor(null);
          edit.t = copy;
          renderEditor();
        } }, "Copy as new"),
      el("span", { class: "grow" }),
      el("button", { type: "button", class: "btn-ghost", onclick: () => {
        view.open = null;
        edit = null;
        renderList();
        renderEmpty();
      } }, off ? "Close" : "Cancel"),
      save),
    off ? el("p", { class: "muted small" }, "Only admins, and those an admin lets, change item types.") : null);
    editor.replaceChildren(form);
    changed();
  }

  // ---- taking types from a file -------------------------------------------------------------

  function importDialog() {
    const dialog = el("dialog", { class: "it-import", "aria-label": "Item types from a file" });
    const body = el("div", { class: "it-import-body" });
    const close = () => {
      dialog.close();
      dialog.remove();
    };
    const pick = el("input", { type: "file", accept: ".json,.storeypath,.storeypath-project,application/json", hidden: true,
      onchange: () => pick.files[0] && read(pick.files[0]) });
    const drop = el("label", { class: "drop", tabindex: "0", onkeydown: (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick.click();
      }
    } }, pick, el("strong", {}, "Choose a file"), " or drop it here: a catalogue (.json), a package (.storeypath) or a project file (.storeypath-project)");
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      if (e.dataTransfer.files[0]) read(e.dataTransfer.files[0]);
    });

    async function read(file) {
      body.replaceChildren(el("p", { class: "muted" }, `Reading ${file.name}…`));
      try {
        const got = await api("catalogue/types-in", undefined, { raw: file });
        await load();
        offer(file.name, got);
      } catch (err) {
        body.replaceChildren(drop, el("p", { class: "form-error" }, err.message));
      }
    }

    function offer(fileName, got) {
      const ours = new Map(view.types.map((t) => [t.code, t]));
      const ORDER = { new: 0, changed: 1, same: 2 }; // what there is to choose, first
      const rows = got.types.map((t) => {
        const here = ours.get(t.code), diff = here ? differences(t, here) : null;
        const status = !here ? "new" : diff.length ? "changed" : "same";
        return { t, here, diff, status, take: status === "new" };
      }).sort((a, b) => ORDER[a.status] - ORDER[b.status]);
      const tally = ["new", "changed", "same"].map((s) => [s, rows.filter((r) => r.status === s).length]).filter(([, k]) => k)
        .map(([s, k]) => `${k} ${s}`).join(", ");
      const n = () => rows.filter((r) => r.take).length;
      const go = el("button", { type: "button", class: "primary", "data-testid": "it-import-go", onclick: async () => {
        try {
          const now = (await api("catalogue")).types;
          for (const r of rows.filter((x) => x.take)) {
            const at = now.findIndex((x) => x.code === r.t.code);
            if (at >= 0) now[at] = r.t;
            else now.push(r.t);
          }
          view.types = (await api("catalogue", { types: now })).types;
          toast(`${n()} item ${n() === 1 ? "type" : "types"} taken from ${fileName}`);
          close();
          renderList();
        } catch (err) {
          toast(err.message, true);
        }
      } });
      const sayGo = () => {
        go.textContent = `Take ${n()} ${n() === 1 ? "type" : "types"}`;
        go.disabled = !n();
      };
      const from = { catalogue: "a catalogue", package: "a package", project: "a project file" }[got.source];
      body.replaceChildren(
        el("p", { class: "small" }, `${fileName}: ${from}${got.name ? ` of ${got.name}` : ""}, with ${got.types.length} item `
          + `${got.types.length === 1 ? "type" : "types"} (${tally}). New ones are chosen; one this Studio has, changed, is taken in `
          + "place of its own only when you choose it."),
        el("div", { class: "table-wrap it-import-list" }, el("table", { class: "it-import-table" },
          el("thead", {}, el("tr", {}, ["", "", "Type", "Here", ""].map((h) => el("th", {}, h)))),
          el("tbody", {}, rows.map((r) => {
            const box = el("input", { type: "checkbox", checked: r.take, disabled: r.status === "same",
              "aria-label": `Take ${r.t.code}`, onchange: () => {
                r.take = box.checked;
                sayGo();
              } });
            return el("tr", { class: r.status, "data-code": r.t.code },
              el("td", {}, box),
              el("td", { class: "it-symbol" }, symbol(r.t, 36)),
              el("td", {}, el("strong", {}, r.t.name_en || r.t.code), el("div", {}, el("code", { class: "muted small" }, r.t.code))),
              el("td", {}, r.status === "new" ? el("span", { class: "badge accent" }, "New")
                : r.status === "same" ? el("span", { class: "badge" }, "Same")
                  : el("span", {}, el("span", { class: "badge warn" }, "Changed"), el("div", { class: "muted small" }, r.diff.join(", ")))),
              el("td", { class: "muted small" }, sizeOf(r.t)));
          })))),
        el("div", { class: "row end" }, el("button", { type: "button", class: "btn-ghost", onclick: close }, "Cancel"), go));
      sayGo();
    }

    body.append(drop);
    dialog.append(el("div", { class: "row" }, el("h2", { class: "grow" }, "Item types from a file"),
      el("button", { type: "button", class: "btn-ghost btn-icon", "aria-label": "Close", onclick: close }, "✕")), body);
    dialog.addEventListener("cancel", (e) => {
      e.preventDefault();
      close();
    });
    document.body.append(dialog);
    dialog.showModal();
  }

  // ---- the page -----------------------------------------------------------------------------

  await load();
  const search = el("input", { type: "search", placeholder: "Find a type: its name or code", "aria-label": "Find a type",
    oninput: (e) => {
      view.query = e.target.value.trim().toLowerCase();
      renderList();
    } });
  const categories = el("div", { class: "segmented", role: "radiogroup", "aria-label": "Category" },
    [["all", "All"], ...CATEGORIES].map(([c, name]) => el("button", { type: "button", role: "radio", "aria-checked": String(c === "all"),
      "data-value": c, onclick: (e) => {
        view.category = c;
        for (const b of e.currentTarget.parentElement.children) b.setAttribute("aria-checked", String(b.dataset.value === c));
        renderList();
      } }, name)));
  const retired = el("label", { class: "check it-retired" }, el("input", { type: "checkbox", onchange: (e) => {
    view.retired = e.target.checked;
    renderList();
  } }), "Show retired");

  page.replaceChildren(
    el("section", { class: "row it-top" },
      el("div", { class: "grow" },
        el("a", { class: "back", href: "#/" }, "← Projects"),
        el("h1", {}, "Item types"),
        el("p", { class: "lead" }, "The furniture and equipment people place on floors, for every project of this Studio. "
          + "A package carries the types its items use, so another system knows them and may take them into its own.")),
      el("div", { class: "row" },
        may ? el("button", { type: "button", "data-testid": "it-import", onclick: importDialog }, "Take from a file…") : null,
        el("button", { type: "button", title: "Every type, as a .json file for another Studio or system to take",
          onclick: () => download(view.types, "item-types.json") }, "Save as a file"),
        may ? el("button", { type: "button", class: "primary", "data-testid": "it-new", onclick: () => openEditor(null) }, "New type") : null)),
    el("section", { class: "row it-filters" }, search, categories, retired, counts),
    chosenBar,
    el("div", { class: "it-layout" }, el("section", { class: "card it-list-card" }, listBox), editor));
  renderList();
  renderEmpty();
}

/** A type as it is saved: its fields' empty choices left out, a number where one is. */
function clean(t) {
  const out = structuredClone(t);
  for (const key of ["width", "depth", "height", "workplaces"]) out[key] = Number(out[key]);
  out.elevation = out.elevation === null || out.elevation === "" ? null : Number(out.elevation);
  out.fields = out.fields.map((f) => ({ key: f.key, name_en: f.name_en, name_ar: f.name_ar || "", kind: f.kind || "text",
    choices: (f.kind || "text") === "choice" ? f.choices || [] : [], owner: f.owner || "storeypath" }));
  return out;
}

/** What is wrong with a type before it is saved (the server checks again). */
function check(t, isNew) {
  const out = [];
  if (isNew && (!CODE_RE.test(t.code) || t.code.length > 40)) out.push("Its code: capital letters and digits in parts joined by -, at most 40.");
  if (!t.name_en.trim()) out.push("It needs a name.");
  for (const key of ["width", "depth", "height"]) {
    if (!(Number(t[key]) > 0 && Number(t[key]) <= 100)) out.push(`Its ${key}: in metres, more than 0 and at most 100.`);
  }
  if (t.elevation !== null && !(Number(t.elevation) >= 0 && Number(t.elevation) <= 100)) out.push("Off the floor: 0 to 100 metres.");
  if (!/^#[0-9a-f]{6}$/i.test(t.color)) out.push("Its colour: #rrggbb.");
  if (!(Number.isInteger(Number(t.workplaces)) && t.workplaces >= 0 && t.workplaces <= 100)) out.push("Workplaces: a whole number, 0 to 100.");
  const keys = t.fields.map((f) => f.key);
  for (const f of t.fields) {
    if (!KEY_RE.test(f.key)) out.push(`A field's key (${f.key || "empty"}): small letters, digits and _, starting with a letter.`);
    if (!f.name_en.trim()) out.push(`The field ${f.key || "without a key"} needs a name.`);
    if (f.kind === "choice" && !(f.choices || []).length) out.push(`The field ${f.key}: its choices.`);
  }
  if (new Set(keys).size !== keys.length) out.push("Two fields share a key.");
  return out;
}
