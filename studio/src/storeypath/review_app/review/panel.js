// Review's panel and toolbar: the floor and its drawing, the editors of what is chosen, the
// lists of spaces, the legend; the toolbar's view, drawing and item controls; the keys.

import { editable, editableByAccess, viewOnly } from "./access.js";
import { on } from "./bus.js";
import { $, el, save } from "./dom.js";
import { backCorner, closeSpace, deleteItem, fillSizeForm, openingMeta, openingName, resize, sizesFrom } from "./drawing.js";
import * as finish from "./finish.js";
import { reconvert, openFloor } from "./floor.js";
import { categories, changeAsset, chosenAsset, copyId, findAsset, nudgeAsset, renderAssets, turnAsset } from "./items.js";
import { setupLook } from "../look.js";
import { closeMenu } from "./menu.js";
import { toast } from "./notify.js";
import { fit, placeLabels, renderPlan, restyle, showUnderlay, styleSpace } from "./plan.js";
import { GRADE_NAMES, nextToReview, saveSpace, seatsOf, setFlag } from "./rooms.js";
import * as sample from "./sample.js";
import { select, selectAsset, selectItem } from "./selection.js";
import { buildingOf, BASE, code, color, matches, readable, reviewSpaces, state, title, tucked, typeLabel, typeOf, units,
  view3d, visible } from "./state.js";
import { setTool } from "./tools.js";
import { draggable3d, key3d, setView, WALKING } from "./view3d.js";
import * as vertical from "./vertical.js";
import { normalizeItemId } from "/viewer/src/ids.js"; // items' IDs as people type them (the viewers' own)

const LIST_LIMIT = 400;
const swatch = (type) => el("span", { class: "swatch", style: `background:${color(type)}` });

// ---- view only ------------------------------------------------------------------

/** The page as the person may use it on this floor: with a floor they may only view,
 * nothing that changes it is offered, and every field is read only; while someone else
 * edits it, what changes it is there but not to be used. */
function showViewOnly() {
  const only = !editableByAccess();
  const locked = !only && !editable();
  document.body.classList.toggle("view-only", only);
  document.body.classList.toggle("locked", locked);
  $("view-only").hidden = !only;
  if ((only || locked) && state.tool) setTool(null);
  for (const e of document.querySelectorAll("#ed-form input, #ed-form select, #asset-editor input, #asset-editor select, #it-size input")) {
    e.disabled = only || locked;
  }
  draggable3d(); // items carried in 3D only by who may change the floor
}

// ---- the floor -------------------------------------------------------------------

function floorOptionText(f) {
  const review = f.review ? ` · ${f.review} to review` : "";
  return f.converted ? `${f.name}${review}` : `${f.name} · not converted`;
}

function fillFloorSelect() {
  const groups = new Map();
  for (const f of state.project.floors) {
    const key = `${f.location} · ${f.building}`;
    if (!groups.has(key)) groups.set(key, el("optgroup", { label: key }));
    groups.get(key).append(el("option", { value: f.id }, floorOptionText(f)));
  }
  $("floor").replaceChildren(...groups.values());
  if (state.floor) $("floor").value = state.floor.id;
}

function renderFloorMeta() {
  const f = state.floor;
  if (!f) {
    $("floor-meta").textContent = "This project has no floors yet (add them with `storeypath add-floor`).";
    return;
  }
  const how = { walls: "Spaces found from walls", outlines: "Spaces from room outlines",
    package: "From a package, without its drawing" }[f.method];
  const parts = [f.converted_at ? how || "Converted" : "Not converted yet"];
  if (f.source) parts.push(f.source);
  if (f.converted_at) parts.push(new Date(f.converted_at).toLocaleString());
  $("floor-meta").textContent = parts.join(" · ");
  $("convert").disabled = !f.source || state.converting;
  $("open-print").hidden = !f.source;
  $("open-print").href = `/api/${BASE}/floors/${encodeURIComponent(f.id)}/print.png`;
  // 3D, 2D and walking are here, in one view; this is for showing the building full screen
  $("own-window").hidden = !f.converted_at;
  $("own-window").href = "/viewer/examples/world/index.html?" + new URLSearchParams({ // its building as it is now
    pkg: `/api/${BASE}/preview.storeypath?building=${encodeURIComponent(buildingOf(f.id))}`, floor: f.id });
  for (const b of document.querySelectorAll("#view-mode button[data-view='3d'], #view-mode button[data-view='walk']")) {
    b.disabled = !f.converted_at;
  }

  const box = $("warnings");
  box.hidden = !f.warnings.length;
  box.querySelector("summary").textContent =
    `${f.warnings.length} warning${f.warnings.length === 1 ? "" : "s"} from the last conversion`;
  box.querySelector("ul").replaceChildren(...f.warnings.map((w) => el("li", {}, w)));
}

// ---- lists and legend ----------------------------------------------------

function listItem(s, withReasons) {
  const quick = (label, flag, tip) => {
    const b = el("button", { type: "button", class: "quick", title: tip }, label);
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      setFlag(s.id, flag, true);
    });
    return b;
  };
  const li = el("li", { "data-id": s.id, class: tucked(s) ? "tucked" : "" },
    swatch(s.type),
    el("span", {}, title(s)),
    el("span", { class: "sub" }, s.ignored ? "deleted" : s.hidden ? "hidden"
      : `${s.kind === "zone" ? "zone · " : ""}${typeLabel(s.type)} · ${code(s.id)}`),
    withReasons ? el("span", { class: "why" }, s.reasons.join("; "),
      el("span", { class: "quicks edit-only" }, quick("Delete", "ignored",
        "Not there, or not worth anything: out of the plan and the package, kept with its ID"))) : null,
  );
  li.classList.toggle("selected", s.id === state.selected);
  li.addEventListener("click", () => select(s.id, { fly: true }));
  return li;
}

function renderLists() {
  if (!state.floor) return;
  const review = reviewSpaces();
  $("review-count").textContent = `(${review.length})`;
  $("review-list").replaceChildren(...review.map((s) => listItem(s, true)));
  $("review-done").hidden = review.length > 0 || !units().length;
  $("next").disabled = !review.length;

  const all = units().filter(visible);
  const shown = all.filter((s) => matches(s, state.filter));
  const tuckedCount = units().length - units().filter((s) => !tucked(s)).length;
  $("space-count").textContent = (state.filter ? `(${shown.length} of ${all.length})` : `(${all.length})`)
    + (tuckedCount && !state.showHidden ? ` · ${tuckedCount} deleted` : "");
  const items = shown.slice(0, LIST_LIMIT).map((s) => listItem(s, false));
  if (shown.length > LIST_LIMIT) items.push(el("li", { class: "meta" }, `${shown.length - LIST_LIMIT} more…`));
  $("space-list").replaceChildren(...items);
}

function renderLegend() {
  if (!state.floor) return;
  const byFinish = finish.legend(units().filter(visible)); // (coloured by floor finish: the finishes in use)
  if (byFinish) return $("legend").replaceChildren(...byFinish);
  const counts = new Map();
  for (const s of units().filter(visible)) counts.set(s.type, (counts.get(s.type) || 0) + 1);
  const types = state.project.types.filter((t) => counts.has(t));
  $("legend").replaceChildren(...types.map((t) =>
    el("li", {}, swatch(t), typeLabel(t), el("span", { class: "count" }, String(counts.get(t)))),
  ));
}

function markListSelection() {
  for (const li of document.querySelectorAll(".list li[data-id]")) {
    li.classList.toggle("selected", li.dataset.id === state.selected);
  }
}

function filterSpaces(text) {
  state.filter = text;
  state.floor.spaces.forEach(styleSpace);
  placeLabels();
  renderLists();
}

// ---- the space's editor -----------------------------------------------------

function formCorrection(s) {
  const d = s.detected;
  const c = {};
  const type = $("ed-type").value;
  const name = $("ed-name").value.trim();
  const number = $("ed-number").value.trim();
  if (type !== d.type) c.type = type;
  if (name !== (d.name || "")) c.name = name;
  if (number !== (d.number || "")) c.number = number;
  return c;
}

const same = (a, b) => JSON.stringify(a, Object.keys(a).sort()) === JSON.stringify(b, Object.keys(b).sort());

function updateEditorState() {
  const s = state.byId.get(state.selected);
  if (!s) return;
  const d = s.detected;
  const hint = (id, text, changed) => {
    $(id).textContent = text;
    $(id).classList.toggle("changed", changed);
  };
  const source = d.source === "default" ? "no rule matched" : d.source.replace(/:.*/, "");
  hint("ed-type-detected", `detected: ${typeLabel(d.type)} (${source})`, $("ed-type").value !== d.type);
  hint("ed-name-detected", d.name ? `detected: ${d.name}` : "none detected", $("ed-name").value.trim() !== (d.name || ""));
  hint("ed-number-detected", d.number ? `detected: ${d.number}` : "none detected",
    $("ed-number").value.trim() !== (d.number || ""));

  const c = formCorrection(s);
  const saveButton = $("ed-save");
  if (s.correction && same(c, s.correction)) {
    saveButton.textContent = "Saved";
    saveButton.disabled = true;
  } else if (!s.correction && !Object.keys(c).length) {
    saveButton.textContent = s.reasons.length ? "Accept as is" : "Save";
    saveButton.disabled = !s.reasons.length;
  } else {
    saveButton.textContent = "Save";
    saveButton.disabled = false;
  }
  $("ed-reset").hidden = !s.correction;
  $("ed-delete").textContent = s.ignored ? "Restore" : "Delete";
  $("ed-flags").textContent = s.ignored ? "Deleted" : s.hidden ? "Hidden" : "";
}

function renderEditor() {
  const s = state.byId.get(state.selected);
  $("editor").hidden = !s;
  if (!s) return;
  $("ed-title").textContent = title(s);
  $("ed-id").textContent = s.id;
  // the drawing's own text in it, as written: never changed here
  $("ed-label").textContent = s.drawing_label ? `In the drawing: ${s.drawing_label.split("\n").join(" · ")}` : "No text in the drawing";
  $("ed-reasons").replaceChildren(...s.reasons.map((r) => el("li", {}, r)));
  $("ed-type").value = s.type;
  $("ed-type").style.borderLeft = `6px solid ${color(s.type)}`;
  $("ed-name").value = s.name || "";
  $("ed-number").value = s.number || "";
  renderCapacity(s);
  $("ed-area").textContent = `${s.area} m²${s.correction ? " · corrected" : ""}`;
  updateEditorState();
  vertical.editor(s, $("editor")); // vertical.js: the floors a lift or stairs serves
  finish.editor(s, $("ed-finishes")); // finish.js: its floor and walls
}

function renderCapacity(s) {
  const seats = seatsOf(s);
  const input = $("ed-capacity");
  input.value = s.capacity_set ?? "";
  input.placeholder = seats.workplaces ? `${seats.workplaces} (its desks)` : "not known";
  const from = s.capacity_set != null
    ? (seats.workplaces ? `set here; its desks seat ${seats.workplaces}` : "set here")
    : seats.workplaces ? "from its desks: empty it to keep that" : "no desks in it: set it, or place desks";
  $("ed-capacity-from").textContent = from + (seats.grade ? ` · laid out for ${GRADE_NAMES[seats.grade]}` : "");
}

async function saveCapacity() {
  const s = state.byId.get(state.selected);
  if (!s) return;
  const raw = $("ed-capacity").value.trim();
  const capacity = raw === "" ? null : Number(raw);
  if (capacity !== null && !(Number.isInteger(capacity) && capacity >= 0)) return toastError("Capacity: a whole number from 0");
  if (capacity === (s.capacity_set ?? null)) return;
  await saveSpace({ capacity });
}

function toastError(message) {
  toast(message, true);
}

// ---- the door's, window's or drawn line's editor -------------------------------------

function renderItemEditor() {
  const item = state.item;
  $("item-editor").hidden = !item;
  if (!item) return;
  if (item.kind === "wall" || item.kind === "divider") {
    const list = item.kind === "wall" ? state.floor.edits?.walls : state.floor.edits?.dividers;
    const w = (list || []).find(([a, b]) => `${(a[0] + b[0]) / 2},${(a[1] + b[1]) / 2}` === item.at.join(","));
    $("it-title").textContent = item.kind === "wall" ? "Wall drawn here" : "Dividing line drawn here";
    $("it-id").textContent = "";
    $("it-meta").textContent = (w ? `${Math.hypot(w[1][0] - w[0][0], w[1][1] - w[0][1]).toFixed(2)} m long` : "")
      + (item.kind === "divider" ? " · no wall: the space's zones" : "");
    $("it-delete").textContent = "Take away";
    $("it-flags").textContent = "";
    $("it-size").hidden = true;
    return;
  }
  const d = state.floor.doors.find((x) => x.id === item.id);
  if (!d) return;
  $("it-title").textContent = openingName(d);
  $("it-id").textContent = d.id;
  $("it-meta").textContent = openingMeta(d);
  $("it-delete").textContent = d.drawn ? "Take away" : d.ignored ? "Restore" : "Delete";
  $("it-flags").textContent = d.ignored ? "Deleted" : "";
  $("it-size").hidden = d.ignored || !readable();
  fillSizeForm($("it-size"), d);
}

// ---- the item's editor -----------------------------------------------------------------

function fillCatalogue() {
  const groups = categories().map(([category, types]) => el("optgroup", { label: category[0].toUpperCase() + category.slice(1) },
    ...types.map((t) => el("option", { value: t.code }, t.name_en))));
  $("place-type").replaceChildren(el("option", { value: "" }, "an item…"), ...groups.map((g) => g.cloneNode(true)));
  $("as-type").replaceChildren(...groups);
}

function renderAssetEditor() {
  const a = chosenAsset();
  $("asset-editor").hidden = !a;
  if (!a) return;
  const t = typeOf(a.type);
  $("as-title").textContent = t ? t.name_en : a.type;
  $("as-id").textContent = a.id;
  const about = [t && `${t.width} m wide, ${t.depth} m deep`, t?.mount === "ceiling" ? "on the ceiling" : t?.mount === "wall" ? "on a wall" : ""]
    .filter(Boolean).join(" · ");
  $("as-meta").replaceChildren(...(t?.name_ar ? [el("bdi", { dir: "rtl", lang: "ar" }, t.name_ar), el("br")] : []), about);
  $("as-type").value = a.type;
  $("as-rotation").value = Math.round(a.rotation);
  // carried only to a floor the person may change
  $("as-floor").replaceChildren(...state.project.floors.filter((f) => f.id === state.floor.id || editable(f.id))
    .map((f) => el("option", { value: f.id }, `${f.building} · ${floorOptionText(f)}`)));
  $("as-floor").value = state.floor.id;
  const own = (t?.fields || []).filter((f) => f.owner === "storeypath");
  $("as-fields").replaceChildren(...own.map((f) => {
    const input = f.kind === "choice"
      ? el("select", {}, el("option", { value: "" }, "—"), ...f.choices.map((c) => el("option", { value: c }, c)))
      : el("input", { type: f.kind === "number" ? "number" : f.kind === "color" ? "color" : "text", step: "any" });
    input.value = a.values?.[f.key] ?? (f.kind === "color" ? "#000000" : "");
    input.addEventListener("change", () => changeAsset(a, { values: { ...a.values, [f.key]: input.value } }));
    return el("label", {}, f.name_en, input);
  }));
  const others = (t?.fields || []).filter((f) => f.owner !== "storeypath").map((f) => f.name_en);
  if (others.length) $("as-fields").append(el("p", { class: "meta", style: "grid-column: 1 / -1" }, `${others.join(", ")}: entered where the asset is managed (wayfinder)`));
  $("as-delete").textContent = a.retired ? "Restore" : "Delete";
  $("as-flags").textContent = a.retired ? "Deleted" : "";
  showViewOnly();
}

/** The keys of a chosen item: R turns it 90°, [ and ] by 15°, the arrows move it
 * (Shift: further), Delete takes it away. Whether the key was one of them. */
function assetKey(e) {
  const a = chosenAsset();
  if (!a || viewOnly()) return false;
  const step = e.shiftKey ? 1 : 0.1;
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
  if (e.key === "r" || e.key === "R") turnAsset(a, a.rotation + (e.shiftKey ? 270 : 90));
  else if (e.key === "[" || e.key === "]") turnAsset(a, a.rotation + (e.key === "]" ? 345 : 15));
  else if (moves[e.key]) nudgeAsset(a, ...moves[e.key]);
  else if (e.key === "Delete" || e.key === "Backspace") changeAsset(a, { retired: !a.retired });
  else return false;
  e.preventDefault();
  return true;
}

// ---- setting up -------------------------------------------------------------------------

export function setupPanel() {
  $("back").href = `/#/p/${encodeURIComponent(state.project?.project?.id || "")}`;
  $("floor").addEventListener("change", (e) => {
    sample.stop(); // sample.js: an area of the floor shown
    openFloor(e.target.value);
  });
  $("convert").addEventListener("click", reconvert);
  $("next").addEventListener("click", () => nextToReview());
  $("ed-close").addEventListener("click", () => select(null));
  $("ed-copy").addEventListener("click", () => copyId(state.selected));
  $("ed-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const s = state.byId.get(state.selected);
    if (s && !$("ed-save").disabled) saveSpace({ correction: formCorrection(s) });
  });
  $("ed-reset").addEventListener("click", () => saveSpace({ reset: true }));
  $("ed-delete").addEventListener("click", () => {
    const s = state.byId.get(state.selected);
    if (s) setFlag(s.id, "ignored", !s.ignored);
  });
  $("it-close").addEventListener("click", () => selectItem(null));
  $("it-delete").addEventListener("click", deleteItem);
  $("it-size").addEventListener("submit", (e) => {
    e.preventDefault();
    const d = state.item?.kind === "door" && state.floor.doors.find((x) => x.id === state.item.id);
    if (d) resize(d, sizesFrom($("it-size"), d));
  });
  $("it-size").querySelector(".as-drawn").addEventListener("click", () => {
    const d = state.item?.kind === "door" && state.floor.doors.find((x) => x.id === state.item.id);
    if (d) resize(d, { width: null, sill: null, height: null });
  });
  for (const id of ["ed-type", "ed-name", "ed-number"]) $(id).addEventListener("input", updateEditorState);
  $("ed-capacity").addEventListener("change", saveCapacity);
  $("ed-type").addEventListener("change", (e) => (e.target.style.borderLeft = `6px solid ${color(e.target.value)}`));
  $("search").addEventListener("input", (e) => {
    const typed = e.target.value;
    filterSpaces(typed.trim().toLowerCase());
    const tag = normalizeItemId(typed); // an item's ID, as typed (7k2q xm9f 4dp): that item
    if (!tag) return;
    findAsset(tag, () => $("search").value === typed).then((r) => {
      if (r.found) filterSpaces("");
      else if (r.error && $("search").value === typed && !units().some((s) => matches(s, state.filter))) toastError(r.error);
    });
  });

  // the asset editor
  $("as-copy").addEventListener("click", () => copyId(state.asset));
  $("place-type").addEventListener("change", (e) => {
    if (!e.target.value) return setTool(null);
    if (state.tool !== "place") setTool("place");
    state.placeType = e.target.value;
  });
  $("as-close").addEventListener("click", () => selectAsset(null));
  $("as-type").addEventListener("change", (e) => {
    const a = chosenAsset();
    if (a) changeAsset(a, { type: e.target.value });
  });
  $("as-rotation").addEventListener("change", (e) => {
    const a = chosenAsset();
    if (a && Number.isFinite(Number(e.target.value))) turnAsset(a, Number(e.target.value));
  });
  $("as-floor").addEventListener("change", (e) => {
    const a = chosenAsset();
    if (a && e.target.value !== state.floor.id) changeAsset(a, { floor_id: e.target.value });
  });
  $("as-delete").addEventListener("click", () => {
    const a = chosenAsset();
    if (a) changeAsset(a, { retired: !a.retired });
  });

  // the toolbar
  $("side-by-side").checked = state.side;
  $("side-by-side").addEventListener("change", (e) => {
    state.side = e.target.checked;
    save("storeypath.side", state.side ? "1" : "0");
    showUnderlay();
  });
  $("fit").addEventListener("click", fit);
  $("drawing-mode").value = state.drawingMode;
  $("drawing-mode").addEventListener("change", (e) => {
    state.drawingMode = e.target.value;
    save("storeypath.drawing", state.drawingMode);
    showUnderlay();
  });
  $("show-labels").addEventListener("change", (e) => {
    state.showLabels = e.target.checked;
    $("labels").classList.toggle("hidden", !e.target.checked);
    view3d.world?.setLabels(e.target.checked);
  });
  for (const b of document.querySelectorAll("#view-mode button")) {
    b.addEventListener("click", () => setView(b.dataset.view));
  }
  // the 3D view's look and quality, remembered in this browser (look.js)
  view3d.look = setupLook({ styles: [...document.querySelectorAll("#look button")], quality: $("quality") }, () => view3d.world);
  $("show-hidden").addEventListener("change", (e) => {
    view3d.world?.setShowHidden(e.target.checked);
    state.showHidden = e.target.checked;
    renderAssets();
    if (!state.floor) return;
    renderPlan();
    restyle();
    renderLists();
    renderLegend();
  });

  setupKeys();

  on("project", () => state.project && fillFloorSelect());
  on("floor", (d) => {
    renderFloorMeta();
    showViewOnly();
    renderLists();
    renderLegend();
    if (d?.refreshed && state.selected) {
      if (d.changedHere(state.selected)) renderEditor();
      else {
        updateEditorState();
        renderCapacity(state.byId.get(state.selected));
      }
    }
    if (d?.refreshed && state.asset && d.changedHere(state.asset)) renderAssetEditor();
    if (d?.refreshed && state.item && d.changedHere(state.item.id)) renderItemEditor();
  });
  on("converting", renderFloorMeta);
  on("spaces", () => {
    renderLists();
    renderLegend();
    renderEditor();
  });
  on("items", () => {
    const s = state.byId.get(state.selected);
    if (s) renderCapacity(s);
    renderAssetEditor();
  });
  on("selection", () => {
    renderEditor();
    renderItemEditor();
    renderAssetEditor();
    markListSelection();
  });
  on("catalogue", fillCatalogue);
  on("access", () => {
    showViewOnly();
    renderAssetEditor();
  });
  on("view", () => {
    for (const b of document.querySelectorAll("#view-mode button")) b.classList.toggle("active", b.dataset.view === view3d.mode);
  });
  on("tool", () => {
    if (state.tool !== "place") $("place-type").value = "";
  });
  on("settings", () => {
    renderLegend();
  });
}

/** The project's types, for the type select. */
export function fillTypes(types) {
  $("ed-type").replaceChildren(...types.map((t) => el("option", { value: t }, typeLabel(t))));
  $("back").href = `/#/p/${encodeURIComponent(state.project.project.id)}`;
  document.title = `${state.project.project.name} · StoreyPath Review`;
  $("project-name").textContent = state.project.project.name;
  $("project-meta").replaceChildren(el("code", {}, state.project.project.id), ` · ${state.project.file}`);
}

function setupKeys() {
  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest?.("input, select, textarea");
    if (e.key === "Escape") {
      if (!$("menu").hidden) closeMenu();
      else if (typing) e.target.blur();
      else if (finish.escape()) return; // the finish picker, or painting, put away
      else if (sample.escape()) return; // sample.js: no area to share after all
      else if (state.tool) setTool(null);
      else if (state.asset) selectAsset(null);
      else if (state.item) selectItem(null);
      else select(null);
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (view3d.mode === "walk" && WALKING.has(e.code)) return; // the walker's
    if (state.asset && !state.tool && assetKey(e)) return;
    if (view3d.shown && key3d(e)) return;
    if (state.tool === "space" && (e.key === "Backspace" || e.key === "Enter")) {
      e.preventDefault();
      if (e.key === "Enter") return closeSpace();
      backCorner();
      return;
    }
    if (e.key === "Delete" || e.key === "Backspace") {
      // what is chosen: a door, window, opening or drawn line; else a space
      if (state.item) deleteItem();
      else {
        const s = state.byId.get(state.selected);
        if (s && !s.ignored) setFlag(s.id, "ignored", true);
      }
      e.preventDefault();
      return;
    }
    if (e.key === "n" || e.key === "N") nextToReview();
    else if (view3d.shown) return; // (the drawing tools: on the plan, key3d)
    else if (e.key === "f" || e.key === "F") fit();
    else if (!readable() && "wWvVdDoOsS".includes(e.key)) toastError("This floor has no drawing yet: add its drawing to change its walls and openings");
    else if (e.key === "w" || e.key === "W") setTool("wall");
    else if (e.key === "v" || e.key === "V") setTool("divide");
    else if (e.key === "d" || e.key === "D") setTool("door");
    else if (e.key === "o" || e.key === "O") setTool("opening");
    else if (e.key === "s" || e.key === "S") setTool("space");
  });
}
