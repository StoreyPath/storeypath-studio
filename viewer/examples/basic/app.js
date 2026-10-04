// A complete viewer page built on the StoreyPath Viewer engine: building and
// floor picker, search, legend and a details panel. Open with ?pkg=<url>.

import { StoreyPathViewer, TYPE_COLORS, typeLabel } from "../../src/index.js";

const $ = (id) => document.getElementById(id);
// ?basemap=0 for no street map (offline), or a tile URL template for your own tiles.
const basemapParam = new URLSearchParams(location.search).get("basemap");
const basemap = basemapParam === null ? true : ["0", "off", "false", ""].includes(basemapParam) ? false : basemapParam;
const viewer = new StoreyPathViewer("#map", { basemap });

async function open(source) {
  try {
    await viewer.open(source);
    $("error").hidden = true;
    for (const id of ["nav-section", "search-section", "legend-section", "toolbar"]) $(id).hidden = false;
  } catch (err) {
    showError(err.message || String(err));
  }
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
  setTimeout(() => ($("error").hidden = true), 6000);
}

// ---- viewer events ----------------------------------------------------------

viewer.addEventListener("load", ({ detail: { package: pkg } }) => {
  $("project-name").textContent = pkg.project.name;
  const locations = pkg.locations.map((l) => l.properties.name).join(", ");
  $("project-meta").innerHTML =
    `Project <code>${esc(pkg.project.id)}</code> · ${esc(locations)}<br>` +
    `Export #${pkg.manifest.export.sequence} · ${new Date(pkg.manifest.export.exported_at).toLocaleString()}`;
  $("building").innerHTML = pkg.buildings
    .map((b) => `<option value="${esc(b.id)}">${esc(b.properties.name)} (${esc(b.properties.code)})</option>`)
    .join("");
  $("details").hidden = true;
  renderFloors();
  renderLegend();
});

viewer.addEventListener("buildingchange", () => {
  $("building").value = viewer.building;
  renderFloors();
  renderLegend();
});

viewer.addEventListener("floorchange", () => {
  for (const b of $("floors").children) b.classList.toggle("active", b.dataset.id === viewer.floor);
});

viewer.addEventListener("modechange", ({ detail: { mode } }) => {
  $("mode").textContent = mode === "stack" ? "Show one floor" : "Show all floors";
});

viewer.addEventListener("select", ({ detail: { id } }) => {
  if (id) renderDetails(id);
  else $("details").hidden = true;
});

// ---- sidebar ----------------------------------------------------------------

function renderFloors() {
  const floors = viewer.package.floorsOf(viewer.building);
  $("floors").innerHTML = "";
  for (const f of [...floors].reverse()) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = f.properties.code;
    b.title = f.properties.name;
    b.dataset.id = f.id;
    b.classList.toggle("active", f.id === viewer.floor);
    b.onclick = () => viewer.setFloor(f.id);
    $("floors").append(b);
  }
}

function renderLegend() {
  const counts = {};
  for (const s of viewer.package.search("", { buildingId: viewer.building, limit: Infinity })) {
    counts[s.properties.type] = (counts[s.properties.type] || 0) + 1;
  }
  $("legend").innerHTML = "";
  for (const [type, n] of Object.entries(counts).sort((a, b) => b[1] - a[1])) {
    const li = document.createElement("li");
    if (type === "unspecified") li.className = "review";
    li.innerHTML = `${swatch(type)}${esc(typeLabel(type))}<span class="count">${n}</span>`;
    li.title = "List these spaces";
    li.onclick = () => {
      $("search").value = `type:${type}`;
      runSearch();
    };
    $("legend").append(li);
  }
}

function runSearch() {
  const q = $("search").value.trim();
  const ul = $("results");
  ul.innerHTML = "";
  if (!q) return;
  const hits = q.startsWith("type:")
    ? viewer.package.search("", { type: q.slice(5), buildingId: viewer.building })
    : viewer.package.search(q);
  if (!hits.length) {
    ul.innerHTML = `<li class="sub">No matches</li>`;
    return;
  }
  for (const s of hits) {
    const { building, floor } = viewer.package.hierarchy(s.id);
    const li = document.createElement("li");
    li.innerHTML = `${swatch(s.properties.type)}<span>${esc(labelOf(s.properties))}</span>` +
      `<span class="sub">${esc(building.properties.code)} · ${esc(floor.properties.code)}</span>`;
    li.onclick = () => viewer.select(s.id);
    ul.append(li);
  }
}

function renderDetails(id) {
  const pkg = viewer.package;
  const space = pkg.get(id);
  const p = space.properties;
  const { project, location, building, floor } = pkg.hierarchy(id);
  const doors = pkg.doorsOf(id);
  const neighbours = doors.filter((d) => d.space);
  const exits = doors.length - neighbours.length;

  const box = $("details");
  box.hidden = false;
  box.innerHTML = `
    <h3>${esc(labelOf(p))}</h3>
    <div class="type">${swatch(p.type)}${esc(typeLabel(p.type))}</div>
    <div class="id-row"><code>${esc(id)}</code><button type="button" id="copy-id">Copy</button></div>
    <dl>
      <dt>Project</dt><dd>${esc(project.name)} <code>${esc(project.id)}</code></dd>
      <dt>Location</dt><dd>${esc(location.properties.name)} <code>${esc(location.properties.code)}</code></dd>
      <dt>Building</dt><dd>${esc(building.properties.name)} <code>${esc(building.properties.code)}</code></dd>
      <dt>Floor</dt><dd>${esc(floor.properties.name)} <code>${esc(floor.properties.code)}</code></dd>
      <dt>Area</dt><dd>${p.area_m2.toFixed(1)} m²</dd>
      <dt>Doors to</dt><dd>${neighbours.map((n) => `<span class="link" data-id="${esc(n.space.id)}">${esc(labelOf(n.space.properties))}</span>`).join(", ") || "—"}${exits ? ` (+${exits} exit)` : ""}</dd>
    </dl>
    ${p.type === "unspecified" ? `<div class="review">Type not recognised yet — correct it in StoreyPath Studio.</div>` : ""}`;
  $("copy-id").onclick = () => navigator.clipboard?.writeText(id);
  for (const a of box.querySelectorAll(".link")) a.onclick = () => viewer.select(a.dataset.id);
}

function labelOf(p) {
  return [p.number, p.name].filter(Boolean).join(" ") || typeLabel(p.type);
}

function swatch(type) {
  return `<span class="swatch" style="background:${TYPE_COLORS[type] || "#ccc"}"></span>`;
}

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// ---- controls ---------------------------------------------------------------

$("file").onchange = (e) => e.target.files[0] && open(e.target.files[0]);
$("building").onchange = (e) => viewer.setBuilding(e.target.value);
$("search").oninput = runSearch;
$("mode").onclick = () => viewer.setMode(viewer.mode === "stack" ? "floor" : "stack");
$("pitch").onclick = () => {
  const flat = viewer.map.getPitch() < 5;
  viewer.set3D(flat);
  $("pitch").textContent = flat ? "2D" : "3D";
};
$("basemap").onchange = (e) => viewer.setBasemap(e.target.checked);
$("show-hidden").onchange = (e) => viewer.setShowHidden(e.target.checked);
if (!basemap) $("basemap").closest("label").hidden = true;

let dragDepth = 0;
window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("drop").hidden = false; });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("drop").hidden = true; } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  $("drop").hidden = true;
  if (e.dataTransfer.files[0]) open(e.dataTransfer.files[0]);
});

const initial = new URLSearchParams(location.search).get("pkg");
if (initial) open(initial);

window.viewer = viewer; // handy from the browser console
