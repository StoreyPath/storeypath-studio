// Lifts and stairs in Review: drawn where the drawing leaves them out, and linked to
// the same lift or stairs on the other floors it serves (Studio's vertical.py and
// stacks.py). Its own file, joined to review.js by a few marked hooks:
//
//   setup(api)      once, with what of review.js it uses (its state, its requests…);
//   drawn(corners)  when the space tool closes a shape: one this file started is a lift
//                   or stairs, drawn typed (two corners make a rectangle);
//   editor(space)   when a space's editor is shown: a lift's or stairs' floors.
//
// The Stairs, Lift and Escalator buttons start Review's own space tool: click two
// opposite corners and press Enter (or double-click) for a rectangle, or click its
// corners and close it on the first. Studio draws it as a space on the floor (cut out
// of the room it is drawn in), reads the floor again and gives it its type.

const TOOLS = [["stairs", "Stairs"], ["elevator", "Lift"], ["escalator", "Escalator"]];
const WORDS = { elevator: "lift", stairs: "stairs", escalator: "escalator", ramp: "ramp" };
const VERTICAL = new Set(Object.keys(WORDS));
const HOW = { code: "same code", overlap: "drawn over it", person: "linked by hand", alone: "on its own" };
const SVG_NS = "http://www.w3.org/2000/svg";

let api = null; // review.js's own, as setup() was given it
let tool = null; // the type being drawn ("stairs", "elevator", "escalator")
let corners = null; // the corners the space tool draws it with (its array: the tool's own while it lasts)
let box = null; // the editor's part: the floors a lift or stairs serves
let asked = 0; // the last request for them: an older answer is not shown

const $ = (id) => document.getElementById(id);
const cap = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const word = (type) => WORDS[type] || type;

/** Once, before the page starts: the buttons, the preview, the editor's part. */
export function setup(given) {
  api = given;
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "vertical.css";
  document.head.append(css);

  const group = api.el("span", { class: "segmented vertical-tools only2d edit-only", role: "group",
    "aria-label": "Draw a lift or stairs the drawing leaves out" });
  for (const [kind, label] of TOOLS) {
    const b = api.el("button", { type: "button", "data-vertical": kind,
      title: `${label}: draw one the drawing leaves out (two corners, or its outline); it is added typed, and can be added on other floors` }, label);
    b.addEventListener("click", () => start(kind));
    group.append(b);
  }
  const toolbar = $("toolbar");
  toolbar.insertBefore(group, toolbar.querySelector(".hint.only2d.edit-only") || null);

  for (const [pane, world] of [["svg", "world"], ["svg-print", "world-print"]]) {
    const layer = document.createElementNS(SVG_NS, "g");
    layer.setAttribute("class", "vertical-preview");
    $(world).append(layer);
    $(pane).addEventListener("pointermove", (e) => {
      showButtons();
      const r = $(pane).getBoundingClientRect();
      preview(api.snapWall(api.planPoint(e.clientX - r.left, e.clientY - r.top)));
    });
    $(pane).addEventListener("dblclick", (e) => { // two corners: a rectangle
      if (active() && corners.length === 2) {
        e.preventDefault();
        drawn(corners);
      }
    });
  }
  document.addEventListener("keydown", () => setTimeout(() => { showButtons(); if (!active()) preview(null); }));

  box = api.el("div", { id: "ed-vertical", class: "vertical", hidden: "" });
  $("editor").append(box);
}

// ---- drawing one --------------------------------------------------------------------

/** Whether the space tool is drawing a lift or stairs started here. */
function active() {
  return Boolean(tool) && api.state.tool === "space" && api.state.corners === corners;
}

function showButtons() {
  for (const b of document.querySelectorAll("[data-vertical]")) {
    b.classList.toggle("active", active() && b.dataset.vertical === tool);
  }
}

function start(kind) {
  if (api.viewOnly()) return;
  if (!api.readable()) {
    api.toast("This floor has no drawing yet: add its drawing to draw on it", true);
    return;
  }
  const again = active() && tool === kind;
  api.setTool(null);
  tool = null;
  if (!again) {
    api.setTool("space");
    tool = kind;
    corners = api.state.corners;
    api.toast(`${cap(word(kind))}: click two opposite corners and press Enter (or double-click) for a rectangle, ` +
      "or click its corners and close it on the first. Esc to stop.");
  }
  showButtons();
}

/** Where it would go: the rectangle the corners so far make (with the pointer's). */
function preview(p) {
  const layers = document.querySelectorAll(".vertical-preview");
  const shapes = [];
  if (p && active() && corners.length && corners.length <= 2) {
    const [a] = corners;
    const b = corners.length === 2 ? corners[1] : p;
    shapes.push([[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]]);
  }
  for (const layer of layers) {
    layer.replaceChildren(...shapes.map((ring) => {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", `M${ring.map(([x, y]) => `${x},${y}`).join("L")}Z`);
      path.setAttribute("class", `vertical-ghost vertical-${tool}`);
      return path;
    }));
  }
}

/** Hook: the space tool closes ``ring`` (its corners). One started here is a lift or
 * stairs: two corners make a rectangle; it is sent typed. True when it was one. */
export function drawn(ring) {
  if (!active() || ring !== corners) return false;
  let shape = ring.map(([x, y]) => [x, y]);
  if (shape.length < 2) {
    api.toast("Click two opposite corners, or its outline's corners", true);
    return true;
  }
  if (shape.length === 2) {
    const [[x0, y0], [x1, y1]] = shape;
    if (Math.abs(x1 - x0) < 0.5 || Math.abs(y1 - y0) < 0.5) {
      api.toast("A lift or stairs is at least half a metre each way", true);
      return true;
    }
    shape = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  }
  const kind = tool;
  tool = null;
  corners = null;
  api.setTool(null);
  preview(null);
  showButtons();
  send(kind, shape);
  return true;
}

async function send(kind, ring) {
  if (api.viewOnly()) return;
  api.state.busy = true;
  $("map").classList.add("busy");
  try {
    const job = await api.followJob(await api.request(`${api.BASE}/floors/${api.state.floor.id}/vertical`,
      { type: kind, space: ring }), `Drawing the ${word(kind)}…`);
    await reload(job.result.space);
    api.toast(`${cap(word(kind))} added; the floor was read again`);
  } catch (e) {
    api.toast(`Not added: ${e.message}`, true);
  } finally {
    api.state.busy = false;
    $("map").classList.remove("busy");
  }
}

/** The floor read again, as it is shown, with ``spaceId`` chosen (not zoomed to). */
async function reload(spaceId) {
  api.state.project = await api.request(`${api.BASE}/review`);
  api.fillFloorSelect();
  await api.openFloor(api.state.floor.id, null, { keepView: true });
  api.select(spaceId);
}

// ---- the floors it serves -------------------------------------------------------------

/** Hook: a space's editor is shown. A lift's, stairs', escalator's or ramp's has the
 * floors of its building it serves, as linked, and what changes that. */
export function editor(s) {
  if (!box) return;
  if (!s || s.kind !== "space" || !VERTICAL.has(s.type)) {
    box.hidden = true;
    box.dataset.id = "";
    return;
  }
  box.hidden = false;
  if (box.dataset.id !== s.id) {
    box.dataset.id = s.id;
    box.replaceChildren(api.el("h4", {}, "Floors it serves"), api.el("p", { class: "meta" }, "…"));
  } else if (box.querySelector(".vt-choose")) {
    return; // the floors to add it on being chosen: kept while the floor is refreshed (others' changes)
  }
  const mine = ++asked;
  api.request(`${api.BASE}/objects/${s.id}/stack`).then((info) => {
    if (mine === asked && box.dataset.id === s.id) render(s, info);
  }).catch((e) => {
    if (mine === asked) box.replaceChildren(api.el("h4", {}, "Floors it serves"), api.el("p", { class: "meta" }, e.message));
  });
}

function render(s, info) {
  const el = api.el;
  const said = info.setting === null ? "linked as found" : info.setting === info.not_linked ? "not linked, by hand"
    : "linked by hand";
  const rows = info.floors.map((f) => {
    const spaces = f.spaces.length ? f.spaces.map((m) => el("span", { class: "vt-space" }, m.label,
      el("span", { class: "meta" }, m.id === s.id ? " · this one" : ` · ${HOW[m.how] || m.how}`)))
      : [el("span", { class: "meta" }, "none")];
    if (f.locked && !f.this) spaces.push(el("span", { class: "meta vt-locked" }, editing(f)));
    return el("li", { class: f.this ? "vt-this" : f.spaces.length ? "vt-linked" : "vt-none", "data-floor": f.id },
      el("span", { class: "vt-floor" }, f.name), el("span", { class: "vt-spaces" }, ...spaces));
  });
  const parts = [el("div", { class: "vt-head" }, el("h4", {}, "Floors it serves"), el("span", { class: "meta" }, said)),
    el("ul", { class: "vt-floors" }, ...rows)];

  const missing = info.floors.filter((f) => !f.this && !f.spaces.length);
  const actions = el("div", { class: "row edit-only vt-actions" });
  if (missing.length) {
    const add = el("button", { type: "button", "data-vt": "add",
      title: "Draw it on other floors, where it is here: typed, and linked to this one" }, "Add on floors…");
    add.addEventListener("click", () => chooseFloors(s, info, missing));
    actions.append(add);
  }
  if (info.candidates.length) {
    const pick = el("select", { "data-vt": "link", title: "Link it to a lift or stairs on another floor: the same one" },
      el("option", { value: "" }, "Link with…"),
      ...info.candidates.map((c) => el("option", { value: c.id }, `${c.floor} · ${c.label}`)));
    pick.addEventListener("change", () => pick.value && api.saveSpace({ stack: pick.value }, s.id));
    actions.append(pick);
  }
  if (info.setting !== info.not_linked && info.floors.some((f) => !f.this && f.spaces.length)) {
    const unlink = el("button", { type: "button", "data-vt": "unlink", title: "Not the same as those on the other floors" },
      "Unlink");
    unlink.addEventListener("click", () => api.saveSpace({ stack: info.not_linked }, s.id));
    actions.append(unlink);
  }
  if (info.setting !== null) {
    const auto = el("button", { type: "button", "data-vt": "auto",
      title: "Linked as Studio finds it: the same code, or drawn over it on the other floors" }, "Link as found");
    auto.addEventListener("click", () => api.saveSpace({ stack: null }, s.id));
    actions.append(auto);
  }
  parts.push(actions);
  box.replaceChildren(...parts);
}

/** Who else is editing a floor (Studio's floor locks): their name, since when. */
function editing(f) {
  const since = new Date(f.locked.since).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${f.locked.who?.name || "Someone"} is editing it (since ${since})`;
}

/** The floors to add it on, ticked; then each drawn, read again, typed and linked. A
 * floor someone else is editing is not offered; one taken meanwhile is left out by
 * Studio, and named when it is done. */
function chooseFloors(s, info, missing) {
  const el = api.el;
  const form = el("form", { class: "vt-choose" });
  for (const f of missing) {
    const why = !f.drawing ? "no drawing" : !f.edit ? "view only" : f.locked ? editing(f) : "";
    const tick = el("input", { type: "checkbox", value: f.id });
    if (why) tick.disabled = true;
    else tick.checked = true;
    form.append(el("label", {}, tick, f.name, why ? el("span", { class: "meta" }, why) : null));
  }
  const go = el("button", { type: "submit", class: "primary" }, "Add");
  const cancel = el("button", { type: "button" }, "Cancel");
  cancel.addEventListener("click", () => render(s, info));
  form.append(el("div", { class: "row" }, go, cancel));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const floors = [...form.querySelectorAll("input:checked")].map((i) => i.value);
    if (!floors.length) return api.toast("Tick the floors to add it on", true);
    if (api.viewOnly()) return;
    go.disabled = true;
    api.state.busy = true;
    $("map").classList.add("busy");
    try {
      const job = await api.followJob(await api.request(`${api.BASE}/objects/${s.id}/copy`, { floors }),
        `Adding the ${word(s.type)} on ${floors.length} floor${floors.length > 1 ? "s" : ""}…`);
      form.remove(); // chosen: the floors it serves shown again, as they are now
      box.dataset.id = "";
      await reload(s.id);
      const name = (id) => info.floors.find((f) => f.id === id)?.name || id;
      const left = [...job.result.missed.map((id) => `not found on ${name(id)}`),
        ...(job.result.refused || []).map((x) => `${name(x.floor)} not changed: ${x.error}`)];
      api.toast(`Added on ${Object.keys(job.result.spaces).map(name).join(", ")}, linked to this one` +
        (left.length ? `; ${left.join("; ")}` : ""), left.length > 0);
    } catch (err) {
      api.toast(`Not added: ${err.message}`, true);
      render(s, info);
    } finally {
      api.state.busy = false;
      $("map").classList.remove("busy");
    }
  });
  box.querySelector(".vt-actions")?.replaceWith(form);
}
