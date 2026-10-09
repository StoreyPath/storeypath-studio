// Lifts and stairs in Review: drawn where the drawing leaves them out, and linked to
// the same lift or stairs on the other floors it serves (Studio's vertical.py and
// stacks.py). Its own file, joined to the page by:
//
//   setup(api)      once, with what of the page it uses (its state, its requests…): the
//                   Stairs and lifts tool (L), its kind (stairs, lift, escalator) in its
//                   options;
//   drawn(corners)  the tool's shape closed: a lift or stairs, drawn typed (two corners
//                   make a rectangle);
//   editor(space)   the inspector's "Floors it serves" of a lift or stairs (null for
//                   another space).
//
// Draw one with two opposite corners and Enter (or a double-click) for a rectangle, or
// click its corners and close it on the first. Studio draws it as a space on the floor
// (cut out of the room it is drawn in), reads the floor again and gives it its type.

const TOOLS = [["stairs", "Stairs"], ["elevator", "Lift"], ["escalator", "Escalator"]];
const WORDS = { elevator: "lift", stairs: "stairs", escalator: "escalator", ramp: "ramp" };
const VERTICAL = new Set(Object.keys(WORDS));
const HOW = { code: "same code", overlap: "drawn over it", person: "linked by hand", alone: "on its own" };
const SVG_NS = "http://www.w3.org/2000/svg";

let api = null; // the page's own, as setup() was given it
let kind = "stairs"; // the type being drawn ("stairs", "elevator", "escalator")
let box = null; // the inspector's part: the floors a lift or stairs serves
let asked = 0; // the last request for them: an older answer is not shown

const $ = (id) => document.getElementById(id);
const cap = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const word = (type) => WORDS[type] || type;
const active = () => api.state.tool === "stairs";

/** Once, before the page starts: the tool, its preview, the inspector's part. */
export function setup(given) {
  api = given;
  for (const world of ["world", "world-print"]) {
    const layer = document.createElementNS(SVG_NS, "g");
    layer.setAttribute("class", "vertical-preview");
    $(world).append(layer);
  }
  api.tool({
    id: "stairs", label: "Stairs and lifts", icon: "door-stairwell", key: "l", group: "draw", edits: true, drawing: true,
    words: "lift elevator escalator stair vertical",
    wrongView: () => "Stairs and lifts are drawn on the plan",
    hint: () => (api.state.corners.length >= 3 ? "Click the next corner · click the first, double-click or Enter to close it"
      : api.state.corners.length === 2 ? `Enter or double-click: a rectangular ${word(kind)} · or click more corners`
        : api.state.corners.length === 1 ? "Click the opposite corner (or its next corner) · Backspace takes it back"
          : `Click two opposite corners of the ${word(kind)}, or its outline's corners`),
    options: () => [api.el("div", { class: "segmented", role: "radiogroup", "aria-label": "What to draw" },
      ...TOOLS.map(([k, label]) => {
        const b = api.el("button", { type: "button", role: "radio", "aria-checked": String(kind === k) }, label);
        b.addEventListener("click", () => {
          kind = k;
          api.emit("tool-options");
          api.emit("tool-progress");
        });
        return b;
      })), api.el("span", { class: "to-note" }, "Added typed; then add it on the other floors it serves")],
    plan: {
      hover: (p) => {
        api.preview(p);
        preview(api.snapWall(p));
      },
      click: (p) => {
        api.toolClick(p);
        preview(api.snapWall(p));
      },
      dblclick: () => { if (api.state.corners.length >= 2) drawn(api.state.corners); },
    },
    keys: {
      enter: [() => drawn(api.state.corners), "close the shape (two corners: a rectangle)"],
      backspace: [() => {
        api.backCorner();
        preview(null);
      }, "take back the last corner"],
    },
    escape: () => {
      if (!api.state.corners.length) return false;
      api.state.corners = [];
      api.state.wallStart = null;
      api.clearPreview();
      preview(null);
      api.emit("tool-progress");
      return true;
    },
    stop: () => {
      api.clearPreview();
      preview(null);
    },
  });
}

/** Where it would go: the rectangle the corners so far make (with the pointer's). */
function preview(p) {
  const layers = document.querySelectorAll(".vertical-preview");
  const corners = api.state.corners;
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
      path.setAttribute("class", `vertical-ghost vertical-${kind}`);
      return path;
    }));
  }
}

/** The tool's shape closed (``ring``: its corners): a lift or stairs, sent typed; two
 * corners make a rectangle. */
export function drawn(ring) {
  if (!active()) return false;
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
  const what = kind;
  api.setTool(null);
  preview(null);
  send(what, shape);
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

/** The inspector's part of a lift, stairs, escalator or ramp: the floors of its building it
 * serves, as linked, and what changes that (null for another space). The same element
 * while the same space is shown (what is being chosen in it is kept). */
export function editor(s) {
  if (!s || s.kind !== "space" || !VERTICAL.has(s.type)) {
    if (box) box.dataset.id = "";
    return null;
  }
  box ??= api.el("div", { id: "ed-vertical", class: "vertical" });
  if (box.dataset.id !== s.id) {
    box.dataset.id = s.id;
    box.replaceChildren(api.el("h4", {}, "Floors it serves"), api.el("p", { class: "meta" }, "…"));
  } else if (box.querySelector(".vt-choose")) {
    return box; // the floors to add it on being chosen: kept while the floor is refreshed (others' changes)
  }
  const mine = ++asked;
  api.request(`${api.BASE}/objects/${s.id}/stack`).then((info) => {
    if (mine === asked && box.dataset.id === s.id) render(s, info);
  }).catch((e) => {
    if (mine === asked) box.replaceChildren(api.el("h4", {}, "Floors it serves"), api.el("p", { class: "meta" }, e.message));
  });
  return box;
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
