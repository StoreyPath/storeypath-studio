// StoreyPath Studio: projects, drawings, plans, floors, placement and export.
// Everything is served and computed locally (see server.py); nothing is fetched
// from the internet.

const SVG_NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const KIND = {
  floor_plan: "Floor plan", roof_plan: "Roof plan", site_plan: "Site plan", elevation: "Elevation",
  section: "Section", detail: "Detail", schedule: "Schedule", other: "Other",
};
const UNITS = { mm: "millimetres (mm)", cm: "centimetres (cm)", m: "metres (m)", in: "inches (in)", ft: "feet (ft)" };
const FLOOR_NAMES = { "-2": "Second basement", "-1": "Basement", 0: "Ground floor", 1: "First floor",
  2: "Second floor", 3: "Third floor", 4: "Fourth floor", 5: "Fifth floor" };

// ---- helpers -----------------------------------------------------------------

async function api(path, body, { method, raw } = {}) {
  const init = {};
  if (raw !== undefined) {
    init.method = "PUT";
    init.headers = { "X-StoreyPath": "1", "Content-Type": "application/octet-stream" };
    init.body = raw;
  } else if (body !== undefined) {
    init.method = method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(`/api/${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `${res.status} ${res.statusText}`), { status: res.status, data });
  return data;
}

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

let toastTimer;
function toast(message, error = false, ms = undefined) {
  const t = $("toast");
  t.textContent = message;
  t.classList.toggle("error", error);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms ?? (error ? 9000 : 3000));
}

/** Run a server job, showing its progress; resolves with its result. */
async function runJob(started) {
  const panel = $("job");
  panel.hidden = false;
  panel.className = "job";
  let job = await started;
  $("job-title").textContent = job.title;
  while (job.state === "waiting" || job.state === "running") {
    $("job-log").replaceChildren(...job.log.slice(-12).map((l) => el("li", {}, l)));
    await new Promise((r) => setTimeout(r, 600));
    job = await api(`jobs/${job.id}`);
  }
  $("job-log").replaceChildren(...job.log.slice(-12).map((l) => el("li", {}, l)));
  panel.classList.add(job.state);
  setTimeout(() => (panel.hidden = true), job.state === "failed" ? 15000 : 4000);
  if (job.state === "failed") throw new Error(job.error || "failed");
  return job.result;
}

// ---- status -----------------------------------------------------------------

async function showStatus() {
  try {
    const s = await api("status");
    $("status").replaceChildren(
      el("span", { class: `chip ${s.model ? (s.model_ready ? "ok" : "") : "off"}`, title: "Reads room names, sheet titles and layer names" },
        s.model ? `Model: ${s.model}${s.model_ready ? "" : " (loading…)"}` : "No language model"),
      el("span", { class: `chip ${s.dwg ? "ok" : "off"}` }, s.dwg ? "Reads DWG and DXF" : "DXF only (no DWG reader)"),
      el("span", { class: "chip ok", title: "Nothing is sent anywhere" }, "Runs offline"),
      el("span", { class: "chip" }, `v${s.version}`),
    );
  } catch {
    $("status").replaceChildren(el("span", { class: "chip off" }, "Server not reachable"));
    return;
  }
}
setInterval(showStatus, 10000);

// ---- pages ------------------------------------------------------------------

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (parts[0] === "p" && parts[1]) await projectPage(decodeURIComponent(parts[1]));
    else await projectsPage();
  } catch (e) {
    $("page").replaceChildren(el("div", { class: "card" }, el("p", {}, e.message), el("a", { href: "#/" }, "All projects")));
  }
  window.scrollTo(0, 0);
}

async function projectsPage() {
  const projects = await api("projects");
  const name = el("input", { type: "text", placeholder: "Project name, e.g. Head office", required: true });
  const form = el("form", { class: "row", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const { code } = await api("projects", { name: name.value });
      location.hash = `#/p/${code}`;
    } catch (err) {
      toast(err.message, true);
    }
  } }, name, el("button", { class: "primary", type: "submit" }, "New project"));

  $("page").replaceChildren(
    el("section", {},
      el("h1", {}, "Projects"),
      el("p", { class: "lead" }, "A project holds the drawings of one site or campus and every object ID issued for it. Create one, add its drawings, and Studio finds the plans, floors and rooms.")),
    el("section", { class: "card" }, form),
    openCard(),
    projects.length
      ? el("section", { class: "projects" }, projects.map((p) =>
          el("a", { class: "card project-card", href: `#/p/${p.code}` },
            el("h3", {}, p.name),
            el("code", { class: "muted small" }, p.code),
            el("div", { class: "stat" }, el("b", {}, String(p.floors)), " floors · ", el("b", {}, String(p.spaces)), " spaces",
              p.review ? el("span", { class: "warn" }, ` · ${p.review} to review`) : null))))
      : el("p", { class: "empty" }, "No projects yet."),
  );
}

async function projectPage(code) {
  const p = await api(`projects/${code}`);
  document.title = `${p.project.name} · StoreyPath Studio`;
  const plansArea = el("div", { id: "plans-area" });

  const converted = p.locations.some((l) => l.buildings.some((b) => b.floors.some((f) => f.converted)));
  $("page").replaceChildren(
    el("section", {},
      el("a", { class: "back", href: "#/" }, "← Projects"),
      el("div", { class: "row" },
        el("div", { class: "grow" },
          el("h1", {}, p.project.name),
          el("p", { class: "lead" }, el("code", {}, p.project.id), ` · ${p.file}`)),
        converted ? el("a", { class: "button primary", href: worldUrl(code), target: "_blank",
          title: "The project as it is now, in 3D: orbit it as a dollhouse or walk through it" }, "Walk in 3D") : null)),
    drawingsCard(code, p, plansArea),
    plansArea,
    buildingsCard(code, p),
    exportCard(code, p),
    deleteCard(code, p),
  );
}

/** Deleting a project: everything in it goes, once its name is typed. */
function deleteCard(code, p) {
  const remove = async () => {
    const typed = prompt(`Delete ${p.project.name} and everything in it: its drawings, floors, corrections and exports? ` +
      "This cannot be undone. Type the project's name to delete it:");
    if (typed === null) return;
    try {
      await api(`projects/${code}/delete`, { confirm: typed });
      toast(`${p.project.name} deleted`);
      location.hash = "#/";
    } catch (e) {
      toast(e.message, true);
    }
  };
  return el("section", { class: "card danger-zone" },
    el("h2", {}, "Delete project"),
    el("p", { class: "muted small" }, "Its drawings, floors, corrections and exports all go. This cannot be undone."),
    el("button", { type: "button", class: "danger", onclick: remove }, "Delete project…"));
}

// ---- drawings and plans ----------------------------------------------------------

function drawingsCard(code, p, plansArea) {
  const input = el("input", { type: "file", accept: ".dwg,.dxf", hidden: true, multiple: true });
  const drop = el("label", { class: "drop" }, input,
    el("strong", {}, "Add drawings"), el("br"), "Drop DWG or DXF files here, or click to choose them.");
  // On by default: the project keeps only a copy without the title blocks (client,
  // owner, consultant, who drew it), names, contacts, logos and hidden file data.
  const keepPrivate = el("input", { type: "checkbox", checked: true });
  const privacy = el("label", { class: "check",
    title: "Removes each sheet's title block (client, owner, consultant, project, plot and permit numbers, who drew and checked it, stamps, logos), names, phone numbers and emails elsewhere, and the file's hidden data. Only the cleaned copy is kept, named drawing-1.dxf, drawing-2.dxf…; the file you add and its name are not." },
    keepPrivate, "Remove private information (title blocks, owner, names, contacts) as drawings are added");
  const upload = async (files) => {
    const cleaned = keepPrivate.checked;
    const added = [];
    for (const file of files) {
      try {
        toast(`Uploading ${file.name}…`);
        const sent = api(`projects/${code}/drawings/${encodeURIComponent(file.name)}${cleaned ? "" : "?private=0"}`,
          undefined, { raw: file });
        if (cleaned) {
          let result = await runJob(sent);
          if (result.pending) { // a person says what of it to keep
            const keep = await choosePrivate(file.name, result);
            if (keep === null) {
              await api(`projects/${code}/incoming/${result.pending}/cancel`, {});
              toast(`${file.name}: not added`);
              continue;
            }
            result = await runJob(api(`projects/${code}/incoming/${result.pending}`, { keep }));
          }
          added.push(result.drawing);
          toast(`${file.name} → ${result.drawing}: ${result.privacy.summary}. Its Words list shows everything left in it.`, false, 12000);
        } else {
          added.push((await sent).drawing);
        }
      } catch (e) {
        toast(`${file.name}: ${e.message}`, true);
        break;
      }
    }
    await projectPage(code);
    if (files.length === 1 && added.length === 1) findPlans(code, added[0], $("plans-area"));
  };
  input.addEventListener("change", () => upload([...input.files]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); });

  return el("section", { class: "card" },
    el("h2", {}, "Drawings"),
    drop,
    privacy,
    p.drawings.length ? el("ul", { class: "drawings" }, p.drawings.map((d) =>
      el("li", {}, el("code", { class: "grow" }, d),
        el("a", { class: "button", href: `/api/projects/${encodeURIComponent(code)}/drawings/${encodeURIComponent(d)}/words`,
          target: "_blank", title: "Every word and string left in this drawing, to look through for anything private left behind" },
          "Words"),
        el("button", { type: "button", "data-drawing": d, disabled: findingPlans !== null,
          onclick: () => findPlans(code, d, plansArea) }, findingPlans === d ? "Finding plans…" : "Find plans")))) : null,
  );
}

// What a drawing holds that is private, found as it is added: each goes unless it is
// unticked (a child's name on their room, kept on purpose). Resolves with the ids
// kept, or null to not add the drawing at all.
const PRIVATE_GROUPS = [
  ["title block", "Title blocks", "the client, owner, consultant, who drew it, stamps"],
  ["name", "Names with a title", "Mr, Dr, Eng, Sheikh, السيد …: the name goes, the rest of the text stays"],
  ["contact", "Contacts and numbers", "phone, email, web, P.O. box, permit, plot and licence numbers"],
  ["attribute", "Block attributes", "values filled in on blocks"],
  ["person", "People, read by the language model", "the name goes, the rest of the text stays"],
  ["company", "Companies, read by the language model", ""],
  ["address", "Addresses and places, read by the language model", ""],
  ["id number", "Numbers and dates, read by the language model", ""],
  ["other", "Other, read by the language model", ""],
  ["images", "Images", ""],
  ["sheets", "Paper sheets", ""],
  ["file data", "Hidden file data", "who saved it, printers, properties"],
];

function choosePrivate(fileName, found) {
  return new Promise((resolve) => {
    const boxes = [];
    const groups = PRIVATE_GROUPS.map(([kind, title, about]) => {
      const items = found.found.filter((f) => f.kind === kind);
      if (!items.length) return null;
      return el("section", { class: "private-group" },
        el("h3", {}, title, about ? el("span", { class: "muted small" }, ` · ${about}`) : null),
        el("ul", {}, items.map((f) => {
          const box = el("input", { type: "checkbox", checked: true });
          box.dataset.id = f.id;
          boxes.push(box);
          const parts = f.kind === "title block" ? f.detail.join(" · ")
            : f.detail.length ? `takes out: ${f.detail.join(", ")}` : "";
          return el("li", {}, el("label", {}, box,
            el("span", { class: "grow" }, el("span", {}, f.label), parts ? el("span", { class: "muted small detail" }, parts) : null),
            f.count > 1 ? el("span", { class: "muted small" }, `×${f.count}`) : null));
        })));
    }).filter(Boolean);
    const dialog = el("dialog", { class: "private-review" });
    const close = (keep) => {
      dialog.close();
      dialog.remove();
      resolve(keep);
    };
    const all = (on) => boxes.forEach((b) => (b.checked = on));
    dialog.append(
      el("h2", {}, `Private information in ${fileName}`),
      el("p", { class: "muted" }, "Ticked things are taken out before the drawing is kept; untick what you want to keep ",
        "(a child's name on their room, say). Nothing is kept until you choose."),
      el("div", { class: "row" },
        el("button", { type: "button", onclick: () => all(true) }, "Tick all"),
        el("button", { type: "button", onclick: () => all(false) }, "Untick all"),
        el("span", { class: "muted small grow" }, found.reader ? `Texts read by ${found.reader} as well as by rule.`
          : "No language model running: found by rule only.")),
      el("div", { class: "private-list" }, groups),
      el("div", { class: "row end" },
        el("button", { type: "button", onclick: () => close(null) }, "Don't add it"),
        el("button", { type: "button", class: "primary",
          onclick: () => close(boxes.filter((b) => !b.checked).map((b) => b.dataset.id)) },
          "Take out the ticked and add it")));
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); close(null); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

// The drawing whose plans are being found: one at a time (an upload starts it), and
// its button says so.
let findingPlans = null;

function showFindingPlans() {
  for (const b of document.querySelectorAll("button[data-drawing]")) {
    b.disabled = findingPlans !== null;
    b.textContent = findingPlans === b.dataset.drawing ? "Finding plans…" : "Find plans";
  }
}

/** Find the plans in a drawing, read in ``units`` (or the units it shows), and offer them as floors. */
async function findPlans(code, drawing, area, units) {
  if (findingPlans !== null) return;
  findingPlans = drawing;
  showFindingPlans();
  // Earlier results go: no floors are added from plans being found again.
  area.replaceChildren(el("section", { class: "card" }, el("h2", {}, `Plans in ${drawing}`),
    el("p", { class: "muted small" }, units ? `Finding the plans again, in ${UNITS[units]}…` : "Finding the plans…")));
  let result, project;
  try {
    result = await runJob(api(`projects/${code}/drawings/${encodeURIComponent(drawing)}/plans`, units ? { units } : {}));
    project = await api(`projects/${code}`); // its locations, buildings and floors, to add to
  } catch (e) {
    toast(e.message, true);
    area.replaceChildren();
    return;
  } finally {
    findingPlans = null;
    showFindingPlans();
  }
  const floorsKnown = result.plans.filter((x) => x.kind === "floor_plan" && x.floor !== null).map((x) => x.floor);
  const top = floorsKnown.length ? Math.max(...floorsKnown) : 0;
  const cards = result.plans.map((plan) => planCard(plan, top, result.levels, project));
  // One plan per floor of a building to start with, the largest: a second "ground
  // floor plan" on a sheet is often an outbuilding's.
  const largest = new Map();
  for (const c of cards.filter((c) => c.chosen())) {
    const { key } = c.place();
    if (!largest.has(key) || c.area > largest.get(key).area) largest.set(key, c);
  }
  cards.forEach((c) => { if (c.chosen() && largest.get(c.place().key) !== c) c.choose(false); });
  // A drawing of one floor whose plan does not say which (one file per floor is
  // common): the building's next floor.
  const floorPlans = cards.filter((c) => c.plan.kind === "floor_plan");
  if (floorPlans.length === 1 && floorPlans[0].plan.floor === null) floorPlans[0].nextFloor();
  // The plans are found at the drawing's scale, so other units mean finding them again.
  const unitChoice = el("select", { onchange: () => findPlans(code, drawing, area, unitChoice.value) },
    Object.entries(UNITS).map(([u, label]) => el("option", { value: u, selected: u === result.units }, label)));
  const add = el("button", { class: "primary", type: "button" }, "Add the chosen plans as floors");
  const updateCount = () => {
    const chosen = cards.filter((c) => c.chosen());
    const byPlace = new Map();
    chosen.forEach((c) => byPlace.set(c.place().key, [...(byPlace.get(c.place().key) || []), c]));
    let clash = null;
    for (const c of cards) {
      const { key, building, floor } = c.place();
      const others = (byPlace.get(key) || []).filter((o) => o !== c).map((o) => o.plan.title || `plan ${o.plan.index}`);
      const has = c.existing();
      if (has && c.chosen() && !c.replacing()) {
        c.say(`${building} already has floor ${floor} (${has.name}${has.drawing ? `, from ${has.drawing}` : ""}): replace its drawing, or give this plan another floor.`, true);
        clash = { building, floor };
      } else if (!others.length) c.say(has && c.replacing() ? `Replaces the drawing of ${has.name}: its rooms keep their IDs.` : "");
      else if (c.chosen()) {
        c.say(`${others.join(", ")} is also floor ${floor} of ${building}: give one of them another floor or building.`, true);
        clash = { building, floor };
      } else c.say(`Floor ${floor} of ${building} is ${others.join(", ")}. To add this plan too, give it another building or floor.`);
    }
    add.textContent = clash ? `Two plans are floor ${clash.floor} of ${clash.building}`
      : chosen.length ? `Add ${chosen.length} floor${chosen.length === 1 ? "" : "s"}` : "Choose plans to add";
    add.disabled = !chosen.length || clash !== null;
  };
  cards.forEach((c) => c.onChange(updateCount));
  updateCount();
  add.addEventListener("click", async () => {
    const plans = cards.filter((c) => c.chosen()).map((c) => c.value());
    try {
      await runJob(api(`projects/${code}/floors`, { drawing, units: result.units, plans }));
      toast("Floors added and converted");
      await projectPage(code);
    } catch (e) {
      toast(e.message, true);
    }
  });
  area.replaceChildren(el("section", { class: "card" },
    el("h2", {}, `Plans in ${drawing}`),
    el("p", { class: result.units_sure ? "muted small" : "unsure small" }, result.units_reason,
      result.units_sure ? "" : " If the plans below look the wrong size, choose the units."),
    el("p", { class: "muted small" }, result.levels.summary
      ? `Levels in the drawing: ${result.levels.summary}. Each floor's height and parapet are set from them.`
      : `No floor levels on its sections or plans: floors are ${result.levels.height} m high unless you change them.`),
    el("p", { class: "muted small" }, "Choose the plans that are floors; Studio lines them up and finds the rooms."),
    el("div", { class: "row" }, el("label", { class: "check" }, "Units ", unitChoice), el("span", { class: "grow" }), add),
    el("div", { class: "plans" }, cards.map((c) => c.node)),
  ));
  area.scrollIntoView({ behavior: "smooth" });
}

const NEW = "__new__"; // a new location or building, named in the box beside it

function planCard(plan, top, levels, project) {
  const isFloor = plan.kind === "floor_plan" || plan.kind === "roof_plan";
  const ordinal = plan.kind === "roof_plan" ? top + 1 : plan.floor;
  const check = el("input", { type: "checkbox", checked: isFloor && plan.kind !== "site_plan" && ordinal !== null });
  // Where it goes: project → location → building → floor. An existing location and
  // building are chosen from the project's; a new one is named.
  const locations = project.locations;
  const location = el("select", {}, ...locations.map((l) => el("option", { value: l.id }, l.name)),
    el("option", { value: NEW }, "New location…"));
  const locationName = el("input", { type: "text", value: project.project.name, placeholder: "location name" });
  const building = el("select");
  const buildingName = el("input", { type: "text", value: plan.building || "Main building", placeholder: "building name" });
  const floors = el("p", { class: "muted small" });
  const replace = el("input", { type: "checkbox" });
  const replaceLabel = el("label", { class: "check", hidden: true }, replace, "Replace its drawing (its rooms keep their IDs)");
  const chosenLocation = () => locations.find((l) => l.id === location.value) || null;
  const chosenBuilding = () => chosenLocation()?.buildings.find((b) => b.id === building.value) || null;
  const fillBuildings = () => {
    const loc = chosenLocation();
    const list = loc ? loc.buildings : [];
    building.replaceChildren(...list.map((b) => el("option", { value: b.id }, b.name)), el("option", { value: NEW }, "New building…"));
    // the building the plan names, else the only one there is, else a new one
    const named = list.find((b) => b.name.trim().toLowerCase() === (plan.building || "").trim().toLowerCase());
    building.value = named ? named.id : list.length === 1 ? list[0].id : NEW;
  };
  if (!locations.length) location.value = NEW;
  fillBuildings();
  const floor = el("input", { type: "number", step: "1", value: ordinal ?? 0 });
  const name = el("input", { type: "text", value: plan.kind === "roof_plan" ? "Roof" : FLOOR_NAMES[ordinal ?? 0] || `Floor ${ordinal}` });
  floor.addEventListener("input", () => { name.value = FLOOR_NAMES[floor.value] || `Floor ${floor.value}`; });
  // Height and parapet from the drawing's levels, following the floor number until changed by hand.
  const heightOf = (n) => levels.heights[String(n)] ?? levels.height;
  const parapetOf = (n) => (levels.parapet !== null && n === levels.roof ? levels.parapet : levels.default_parapet);
  const height = el("input", { type: "number", step: "0.05", min: "2", value: heightOf(ordinal ?? 0) });
  const parapet = el("input", { type: "number", step: "0.05", min: "0", value: parapetOf(ordinal ?? 0) });
  let heightSet = false, parapetSet = false;
  height.addEventListener("input", () => { heightSet = true; });
  parapet.addEventListener("input", () => { parapetSet = true; });
  floor.addEventListener("input", () => {
    if (!heightSet) height.value = heightOf(Number(floor.value));
    if (!parapetSet) parapet.value = parapetOf(Number(floor.value));
  });
  const existing = () => chosenBuilding()?.floors.find((f) => f.ordinal === Number(floor.value)) || null;
  const show = () => {
    locationName.hidden = location.value !== NEW;
    buildingName.hidden = building.value !== NEW;
    const b = chosenBuilding();
    floors.textContent = b ? (b.floors.length ? `${b.name} has: ${b.floors.map((f) => `${f.name} (${f.ordinal})`).join(", ")}` : `${b.name} has no floors yet`) : "";
    floors.hidden = !b;
    replaceLabel.hidden = !existing();
    if (!existing()) replace.checked = false;
  };
  location.addEventListener("change", () => { fillBuildings(); show(); });
  building.addEventListener("change", show);
  floor.addEventListener("input", show);
  show();
  const note = el("p", { class: "small", hidden: true });
  const node = el("article", { class: "plan" },
    thumbnail(plan),
    el("div", { class: "body" },
      el("div", { class: "row" }, el("span", { class: `badge ${isFloor ? "plan-kind" : ""}` }, KIND[plan.kind] || plan.kind),
        el("span", { class: "muted small" }, `${plan.size[0]} × ${plan.size[1]} m`)),
      el("div", { class: "title" }, plan.title || `Untitled plan ${plan.index}`),
      el("label", { class: "check" }, check, "Add as a floor"),
      el("div", { class: "fields" },
        el("label", { style: "grid-column: 1 / -1" }, "Location", location, locationName),
        el("label", { style: "grid-column: 1 / -1" }, "Building", building, buildingName),
        el("label", {}, "Floor", floor),
        el("label", {}, "Floor name", name),
        el("label", { title: "Floor to floor" }, "Height (m)", height),
        el("label", { title: "The low walls around its terraces and balconies" }, "Parapet (m)", parapet)),
      floors, replaceLabel,
      note));
  const sync = () => node.classList.toggle("chosen", check.checked);
  sync();
  return {
    node,
    plan,
    area: plan.size[0] * plan.size[1],
    chosen: () => check.checked,
    choose: (yes) => { check.checked = yes; sync(); },
    existing,
    replacing: () => replace.checked,
    /** The chosen building's next floor (0 for a new building), chosen. */
    nextFloor: () => {
      const b = chosenBuilding();
      floor.value = b && b.floors.length ? Math.max(...b.floors.map((f) => f.ordinal)) + 1 : 0;
      floor.dispatchEvent(new Event("input"));
      check.checked = true;
      sync();
    },
    /** The floor of a building this plan would be. */
    place: () => {
      const b = chosenBuilding();
      const locKey = location.value === NEW ? `new:${locationName.value.trim().toLowerCase()}` : location.value;
      const bName = b ? b.name : buildingName.value.trim() || "Main building";
      const bKey = b ? b.id : `${locKey}|new:${bName.toLowerCase()}`;
      return { key: `${bKey}|${Number(floor.value)}`, building: bName, floor: Number(floor.value) };
    },
    say: (text, warn = false) => { note.textContent = text; note.hidden = !text; note.className = warn ? "unsure small" : "muted small"; },
    onChange: (fn) => {
      check.addEventListener("change", () => { sync(); fn(); });
      for (const input of [location, locationName, building, buildingName, floor, replace]) {
        input.addEventListener(input.tagName === "SELECT" || input.type === "checkbox" ? "change" : "input", fn);
      }
    },
    value: () => {
      const b = chosenBuilding();
      return {
        index: plan.index, title: plan.title, region: plan.region,
        ...(location.value === NEW ? { location: locationName.value.trim() || project.project.name } : { location_id: location.value }),
        ...(b ? { building_id: b.id } : { building: buildingName.value.trim() || "Main building" }),
        ordinal: Number(floor.value), name: name.value, height: Number(height.value), parapet: Number(parapet.value),
        replace: Boolean(existing() && replace.checked),
      };
    },
  };
}

function thumbnail(plan) {
  const [w, h] = plan.size;
  const pad = 1.5;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w + 2 * pad} ${h + 2 * pad}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const d = plan.preview.map((pts) => {
    let s = `M${pts[0]} ${-pts[1]}`;
    for (let i = 2; i < pts.length; i += 2) s += `L${pts[i]} ${-pts[i + 1]}`;
    return s;
  }).join("");
  const g = document.createElementNS(SVG_NS, "g");
  g.setAttribute("transform", `translate(${pad - 1} ${h + pad + 1})`);
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", d);
  g.append(path);
  svg.append(g);
  return svg;
}

// ---- buildings, placement, export -------------------------------------------------

/** The 3D world showing the project as it is now: no export needed. */
function worldUrl(code, { building, floor } = {}) {
  const q = new URLSearchParams({ pkg: `/api/projects/${encodeURIComponent(code)}/preview.storeypath` });
  if (building) q.set("building", building);
  if (floor) q.set("floor", floor);
  return `/viewer/examples/world/index.html?${q}`;
}

function buildingsCard(code, p) {
  const buildings = p.locations.flatMap((l) => l.buildings);
  if (!buildings.length) {
    return el("section", { class: "card" }, el("h2", {}, "Buildings"),
      el("p", { class: "empty" }, "No floors yet: add a drawing and choose its plans."));
  }
  return el("section", { class: "buildings" }, buildings.map((b) => el("div", { class: "card" },
    el("div", { class: "row" }, el("h2", { class: "grow" }, b.name), el("code", { class: "muted small" }, b.id),
      b.floors.some((f) => f.converted)
        ? el("a", { class: "button", href: worldUrl(code, { building: b.id }), target: "_blank" }, "3D") : null),
    el("table", { class: "floors" },
      el("thead", {}, el("tr", {}, el("th", {}, "Floor"), el("th", {}, "From"), el("th", {}, "How it was read"), el("th", {}, ""))),
      el("tbody", {}, b.floors.map((f) => el("tr", {},
        el("td", {}, el("strong", {}, f.name), el("div", { class: "muted small" }, `${f.ordinal} · ${f.id.split("-").at(-1)}`)),
        el("td", {}, f.drawing || "–", el("div", { class: "muted small" }, f.view || "")),
        el("td", {}, f.layers.length
          ? el("details", {}, el("summary", {}, `${f.layers.length} layers recognised`), el("ul", { class: "layers" }, f.layers.map((l) => el("li", {}, l))))
          : el("span", { class: "muted small" }, f.method === "package" ? "from a package: add its drawing to read it again"
            : f.converted ? "with a layer profile" : "not converted")),
        el("td", { class: "actions" }, f.converted ? [
          el("a", { href: `/review.html?p=${encodeURIComponent(code)}#floor=${encodeURIComponent(f.id)}` }, "Review"),
          el("a", { href: worldUrl(code, { floor: f.id }), target: "_blank", title: "This floor in 3D" }, "3D"),
        ] : null))))),
    placementForm(code, b),
  )));
}

function placementForm(code, b) {
  const pl = b.placement || {};
  const lat = el("input", { type: "number", step: "any", value: pl.lat ?? "", placeholder: "e.g. 24.7136" });
  const lon = el("input", { type: "number", step: "any", value: pl.lon ?? "", placeholder: "e.g. 46.6753" });
  const bearing = el("input", { type: "number", step: "any", value: pl.bearing ?? 0 });
  const save = el("button", { type: "submit" }, b.placement ? "Update location" : "Save location");
  return el("form", { onsubmit: async (e) => {
    e.preventDefault();
    try {
      const [x, y] = b.placement ? [b.placement.x, b.placement.y] : (b.centre || [0, 0]);
      await api(`projects/${code}/buildings/${b.id}/placement`, { lat: lat.value, lon: lon.value, bearing: bearing.value, x, y });
      toast(`${b.name} placed`);
      projectPage(code);
    } catch (err) {
      toast(err.message, true);
    }
  } },
    el("p", { class: "muted small", style: "margin: 14px 0 0" }, b.placement
      ? "On the map: the building's middle sits at this latitude and longitude."
      : "Location on the map (optional). Without it the building exports and shows in 3D with its true shape and size; give the latitude and longitude of its middle, and the compass bearing of the drawing's up direction, when you want it on a map."),
    el("div", { class: "placement" },
      el("label", {}, "Latitude", lat), el("label", {}, "Longitude", lon), el("label", {}, "Bearing of up (°)", bearing), save));
}

// ---- a project sent, or a package -------------------------------------------------

/** Open a .storeypath: a project sent from another Studio (as it was), or any package
 * (rebuilt from it: the same IDs; its floors have no drawing until one is added). */
function openCard() {
  const input = el("input", { type: "file", accept: ".storeypath", hidden: true });
  const drop = el("label", { class: "drop" }, input,
    el("strong", {}, "Open a project or a package"), el("br"),
    "Drop a .storeypath here, or click to choose it: a project sent from another Studio, or any package.");
  const open = async (file, replace = null) => {
    try {
      toast(`Opening ${file.name}…`);
      const r = await api(`open${replace !== null ? `?replace=${encodeURIComponent(replace)}` : ""}`, undefined, { raw: file });
      toast(r.how === "project"
        ? `${r.name} opened as it was sent: ${r.floors} floor${r.floors === 1 ? "" : "s"}, ${r.drawings} drawing${r.drawings === 1 ? "" : "s"}.`
        : `${r.name} rebuilt from the package: ${r.floors} floor${r.floors === 1 ? "" : "s"}, the same IDs. To read a floor again, add its drawing to it.`, false, 12000);
      location.hash = `#/p/${r.code}`;
    } catch (e) {
      if (e.status === 409 && replace === null) {
        const typed = prompt(`${e.data.name} (${e.data.code}) is here already. Put the file in its place? What is here now ` +
          `(its drawings, corrections and exports) is lost. Type the project's name to replace it:`);
        if (typed !== null) return open(file, typed);
        return;
      }
      toast(`${file.name}: ${e.message}`, true);
    }
  };
  input.addEventListener("change", () => input.files[0] && open(input.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); e.dataTransfer.files[0] && open(e.dataTransfer.files[0]); });
  return el("section", { class: "card" }, drop);
}

function exportCard(code, p) {
  const button = el("button", { class: "primary", type: "button", onclick: async () => {
    try {
      const r = await runJob(api(`projects/${code}/export`, {}));
      toast(`Exported ${r.file}`);
      projectPage(code);
    } catch (e) {
      toast(e.message, true);
    }
  } }, "Export package");
  const send = el("a", { class: "button", href: `/api/projects/${encodeURIComponent(code)}/project.storeypath`,
    download: `${code}-project.storeypath`,
    title: "One file to send: a package any system reads, carrying this project (its drawings, corrections and edits) for another Studio to continue it" },
  "Download project");
  return el("section", { class: "card" },
    el("div", { class: "row" }, el("h2", { class: "grow" }, "Packages"), send, button),
    el("p", { class: "muted small" }, "A package (.storeypath) holds the buildings, floors, spaces and doors with their IDs, ready for the viewer and for any other system. Every export lists what changed since the one before. Download project gives one file to send to someone who continues the project in their Studio: they open it on their Projects page."),
    p.exports.length ? el("ul", { class: "exports" }, p.exports.map((f) => {
      const url = `/api/projects/${encodeURIComponent(code)}/exports/${encodeURIComponent(f)}`;
      return el("li", {}, el("code", { class: "grow" }, f),
        el("a", { href: url, download: f }, "Download"),
        el("a", { href: `/viewer/examples/world/index.html?pkg=${encodeURIComponent(url)}`, target: "_blank",
          title: "Walk through the building, or orbit it as a dollhouse" }, "Walk in 3D"),
        el("a", { href: `/viewer/examples/basic/index.html?basemap=0&pkg=${encodeURIComponent(url)}`, target: "_blank",
          title: "Floors as a stacked map, with search" }, "Map view"));
    })) : el("p", { class: "empty" }, "No packages yet."),
  );
}

window.addEventListener("hashchange", route);
showStatus();
route();
