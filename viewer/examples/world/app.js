// A walk-through page built on StoreyPathWorld: orbit the building as a
// dollhouse, or walk through it in the first person. Open with ?pkg=<url>.
// Other parameters: building=<id>, floor=<id>, mode=walk, xray=1, cutaway=1, hidden=1.

import { StoreyPathWorld } from "../../src/world/world.js";
import { TYPE_COLORS, typeLabel } from "../../src/theme.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const world = new StoreyPathWorld("#world", { showHidden: params.get("hidden") === "1" });
window.storeypathWorld = world; // for the console
let showMap = true;

async function open(source) {
  $("loading").hidden = false;
  try {
    await world.open(source);
  } catch (err) {
    toast(err.message || String(err));
  } finally {
    $("loading").hidden = true;
  }
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  setTimeout(() => ($("toast").hidden = true), 6000);
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// ---- world events -----------------------------------------------------------------

world.addEventListener("load", async ({ detail: { package: pkg } }) => {
  $("project-name").textContent = pkg.project.name;
  $("project-meta").textContent = `${pkg.project.id} · export #${pkg.manifest.export.sequence}`
    + (world.prebuilt.length ? " · 3D pre-built" : "");
  $("building").innerHTML = pkg.buildings
    .map((b) => `<option value="${esc(b.id)}">${esc(b.properties.name)} (${esc(b.properties.code)})</option>`)
    .join("");
  $("building").hidden = pkg.buildings.length < 2;
  for (const id of ["floors", "controls"]) $(id).hidden = false;
  // ?building=<id>, or ?floor=<id> (its building)
  const floor = params.get("floor") && pkg.get(params.get("floor"));
  const building = floor ? floor.properties.building_id : params.get("building");
  if (building && pkg.get(building) && building !== world.building) await world.setBuilding(building);
  renderFloors();
  if (floor) world.setFloor(floor.id);
  if (params.get("xray") === "1") $("xray").click();
  if (params.get("cutaway") === "1") $("cutaway").click();
  if (params.get("mode") === "walk") world.setMode("walk");
});

world.addEventListener("buildingchange", () => {
  $("building").value = world.building;
  renderFloors();
});

world.addEventListener("floorchange", () => {
  const current = world.mode === "walk" ? world.walkFloor : world.floor;
  for (const b of $("floors").children) b.classList.toggle("active", (b.dataset.id || null) === current);
});

world.addEventListener("modechange", ({ detail: { mode } }) => {
  const walking = mode === "walk";
  document.body.classList.toggle("walking", walking);
  for (const b of document.querySelectorAll(".segmented button")) b.classList.toggle("active", b.dataset.mode === mode);
  $("hud").hidden = !walking;
  $("minimap").hidden = !walking || !showMap;
  $("details").hidden = true;
  $("enter").hidden = !walking;
  $("crosshair").hidden = true;
  renderFloors();
});

world.addEventListener("walklock", ({ detail: { locked } }) => {
  $("enter").hidden = locked || world.mode !== "walk";
  $("crosshair").hidden = !locked;
});

world.addEventListener("roomchange", ({ detail }) => {
  $("room-name").textContent = detail.id ? detail.name || typeLabel(detail.type) : "Outside";
  $("room-meta").textContent = detail.id ? [detail.name ? typeLabel(detail.type) : "", detail.number].filter(Boolean).join(" · ") : "";
  renderHint();
});

world.addEventListener("select", ({ detail: { id, feature } }) => {
  if (!id || world.mode === "walk") {
    $("details").hidden = true;
    return;
  }
  renderDetails(feature);
});

// ---- panels ---------------------------------------------------------------------------

function renderFloors() {
  const pkg = world.package;
  if (!pkg) return;
  const floors = [...pkg.floorsOf(world.building)].reverse();
  const walking = world.mode === "walk";
  const current = walking ? world.walkFloor : world.floor;
  $("floors").innerHTML = "";
  const add = (id, text, title) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.title = title;
    if (id) b.dataset.id = id;
    b.classList.toggle("active", (id || null) === current);
    b.onclick = () => world.setFloor(id);
    $("floors").append(b);
  };
  for (const f of floors) add(f.id, f.properties.code, f.properties.name);
  if (!walking && floors.length > 1) add(null, "All", "All floors");
}

function renderDetails(feature) {
  const p = feature.properties;
  const pkg = world.package;
  const floor = pkg.get(p.floor_id);
  const doors = pkg.doorsOf(feature.id);
  const area = p.area_m2 ?? p.area;
  $("details").innerHTML = `
    <h3>${esc(p.name || typeLabel(p.type))}</h3>
    <div class="type"><span class="swatch" style="background:${TYPE_COLORS[p.type] || TYPE_COLORS.unspecified}"></span>${esc(typeLabel(p.type))}</div>
    <dl>
      ${p.number ? `<dt>Number</dt><dd>${esc(p.number)}</dd>` : ""}
      <dt>ID</dt><dd><code>${esc(feature.id)}</code></dd>
      <dt>Floor</dt><dd>${esc(floor?.properties.name ?? p.floor_id)}</dd>
      ${area ? `<dt>Area</dt><dd>${Number(area).toFixed(1)} m²</dd>` : ""}
      ${p.hidden ? "<dt>Studio</dt><dd>hidden</dd>" : p.ignored ? "<dt>Studio</dt><dd>ignored</dd>" : ""}
    </dl>
    ${doors.length ? `<ul class="doors">${doors.map(({ space }) => space
      ? `<li><button type="button" data-id="${esc(space.id)}">${esc(space.properties.name || typeLabel(space.properties.type))}</button></li>`
      : `<li><button type="button" disabled>outside</button></li>`).join("")}</ul>` : ""}
    <p style="margin:12px 0 0"><button type="button" id="walk-here" class="primary">Walk here</button></p>`;
  $("details").hidden = false;
  for (const b of $("details").querySelectorAll(".doors button[data-id]")) b.onclick = () => world.select(b.dataset.id);
  $("walk-here").onclick = () => {
    world.setMode("walk");
    world.select(feature.id);
  };
}

function renderHint() {
  if (world.mode !== "walk") return;
  const list = world.package.floorsOf(world.building);
  const i = list.findIndex((f) => f.id === world.walkFloor);
  const ways = [];
  if (world.atStairs && list[i + 1]) ways.push(`E up to ${list[i + 1].properties.name}`);
  if (world.atStairs && list[i - 1]) ways.push(`Q down to ${list[i - 1].properties.name}`);
  $("room-hint").textContent = ways.join(" · ");
}

// ---- minimap ----------------------------------------------------------------------------

const mini = $("minimap");
const ctx = mini.getContext("2d");
let plan = null;
let planFloor = null;

function drawMinimap() {
  requestAnimationFrame(drawMinimap);
  if (world.mode !== "walk" || mini.hidden) return;
  const dpr = Math.min(window.devicePixelRatio, 2);
  const size = mini.clientWidth;
  if (mini.width !== size * dpr) mini.width = mini.height = size * dpr;
  if (planFloor !== world.walkFloor) {
    plan = world.plan(world.walkFloor);
    planFloor = world.walkFloor;
  }
  if (!plan) return;
  const me = world.player;
  const scale = (size / 26) * dpr; // about 26 m across
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, mini.width, mini.height);
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(0, 0, mini.width, mini.height, 13 * dpr);
  ctx.clip();
  ctx.setTransform(scale, 0, 0, scale, mini.width / 2 - me.x * scale, mini.height / 2 - me.z * scale);
  const path = (rings) => {
    ctx.beginPath();
    for (const ring of rings) {
      ring.forEach(([x, z], k) => (k ? ctx.lineTo(x, z) : ctx.moveTo(x, z)));
      ctx.closePath();
    }
  };
  for (const s of plan.spaces) {
    path(s.rings);
    ctx.fillStyle = (TYPE_COLORS[s.type] || TYPE_COLORS.unspecified) + (s.id === world.room?.id ? "ff" : "88");
    ctx.fill("evenodd");
  }
  path(plan.walls);
  ctx.fillStyle = "#2b2d33";
  ctx.fill("evenodd");
  // you: a dot and the way you look
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  const cx = mini.width / 2, cy = mini.height / 2, r = 5 * dpr;
  const a = Math.atan2(me.dz, me.dx);
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.arc(cx, cy, 34 * dpr, a - 0.5, a + 0.5);
  ctx.closePath();
  ctx.fillStyle = "rgba(11, 107, 203, 0.22)";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = "#0b6bcb";
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2 * dpr;
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}
requestAnimationFrame(drawMinimap);

// ---- input ------------------------------------------------------------------------------

for (const b of document.querySelectorAll(".segmented button")) b.onclick = () => world.setMode(b.dataset.mode);
$("enter-button").onclick = () => world.startWalking();
$("enter").onclick = (e) => {
  if (e.target === $("enter")) world.startWalking();
};
$("xray").onchange = (e) => world.setXray(e.target.checked);
$("cutaway").onchange = (e) => world.setCutaway(e.target.checked);
$("labels").onchange = (e) => world.setLabels(e.target.checked);
$("show-hidden").checked = params.get("hidden") === "1";
$("show-hidden").onchange = (e) => {
  world.setShowHidden(e.target.checked);
  planFloor = null;
};
$("explode").oninput = (e) => world.setExplode(e.target.value);
$("building").onchange = (e) => world.setBuilding(e.target.value);
$("file").onchange = (e) => e.target.files[0] && open(e.target.files[0]);

window.addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.repeat) return;
  if (!world.package) return;
  const walking = world.mode === "walk";
  if (e.code === "KeyF" && !walking) world.setMode("walk");
  else if (e.code === "KeyO" && walking) world.setMode("dollhouse");
  else if (e.code === "KeyX") $("xray").click();
  else if (e.code === "KeyC" && !walking) $("cutaway").click();
  else if (e.code === "KeyM" && walking) {
    showMap = !showMap;
    mini.hidden = !showMap;
  } else if (walking && (e.code === "KeyE" || e.code === "KeyQ" || e.code === "PageUp" || e.code === "PageDown")) {
    const up = e.code === "KeyE" || e.code === "PageUp";
    if (!world.atStairs) toast("Find stairs or a lift to change floors.");
    else if (!world.changeFloor(up ? 1 : -1)) toast(up ? "This is the top floor." : "This is the lowest floor.");
    renderHint();
  }
});

for (const type of ["dragenter", "dragover"]) {
  window.addEventListener(type, (e) => {
    e.preventDefault();
    $("drop").hidden = false;
  });
}
window.addEventListener("dragleave", (e) => {
  if (!e.relatedTarget) $("drop").hidden = true;
});
window.addEventListener("drop", (e) => {
  e.preventDefault();
  $("drop").hidden = true;
  const file = e.dataTransfer.files[0];
  if (file) open(file);
});

if (params.get("pkg")) open(params.get("pkg"));
