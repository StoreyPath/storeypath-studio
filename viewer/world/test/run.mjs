// node test/run.mjs (after npm run build): the built module in Node, then in
// headless Chrome with WebGL drawn in software: a conformance package opened,
// its floors and rooms built, a room chosen by a click and by select, and the
// WebGL check telling a software renderer from a graphics card.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { launch, serve } from "../../svg/test/harness.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const packages = join(root, "../../spec/conformance/packages");

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const truly = (v, what) => {
  if (!v) throw new Error(what);
};

// ---- in Node ------------------------------------------------------------------

test("the WebGL check says no where there is no document", async () => {
  const { webglSupport } = await import("../dist/support.js");
  const r = webglSupport();
  truly(r.ok === false && r.reason === "no-webgl", JSON.stringify(r));
});

test("the module carries three.js and JSZip: it imports nothing", () => {
  const code = readFileSync(join(root, "dist/world.js"), "utf8");
  truly(!/\bimport\s*[\s{*"']/.test(code.replace(/import\.meta/g, "")), "dist/world.js imports another module");
  truly(!/\bfrom\s*["'][^"'\s]+["']\s*;/.test(code), "dist/world.js imports another module"); // not a message's "from"
  truly(!/\bimport\s*\(/.test(code), "dist/world.js loads another module");
});

// ---- in Chrome ----------------------------------------------------------------

const PAGE = `<!doctype html><meta charset="utf-8"><style>html,body{margin:0}#w{width:900px;height:600px}</style>
<div id="w"></div>
<script type="module">
  import * as sp from "/dist/world.js";
  window.sp = sp;
  window.ready = true;
</script>`;

const server = await serve(root, { "/page.html": PAGE, "/simple-office.storeypath": readFileSync(join(packages, "simple-office.storeypath")),
  "/campus.storeypath": readFileSync(join(packages, "campus.storeypath")) });
const page = await launch({ webgl: true });
await page.open(`${server.url}/page.html`, 900, 600);
await page.run(async () => {
  for (let i = 0; i < 100 && !window.ready; i++) await new Promise((r) => setTimeout(r, 50));
});

test("the WebGL check refuses a software renderer", async () => {
  const r = await page.run(() => window.sp.webglSupport());
  truly(r.ok === false && (r.reason === "software" || r.reason === "no-webgl"), JSON.stringify(r));
});

test("a package opens as a world: its floor, rooms and labels", async () => {
  const r = await page.run(async () => {
    const world = new window.sp.StoreyPathWorld("#w");
    window.world = world;
    const loaded = new Promise((res) => world.addEventListener("load", (e) => res(e.detail.package.project.id), { once: true }));
    const pkg = await world.open("/simple-office.storeypath");
    await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    const floor = pkg.floorsOf(world.building)[0].id;
    const plan = world.plan(floor);
    return { project: await loaded, building: world.building, floors: pkg.floorsOf(world.building).length,
      spaces: plan.spaces.length, walls: plan.walls.length, canvas: !!document.querySelector("#w canvas"),
      labels: [...document.querySelectorAll("#w .sp3d-label")].map((l) => l.textContent) };
  });
  truly(r.floors === 1 && r.canvas, JSON.stringify(r));
  truly(r.spaces >= 16 && r.walls > 0, `rooms and walls: ${JSON.stringify(r)}`);
  truly(r.labels.some((l) => l.includes("F0-301")), `labels: ${r.labels.slice(0, 5)}`);
});

test("select highlights a room and says which", async () => {
  const r = await page.run(async () => {
    const world = window.world;
    const office = world.package.units.find((u) => u.properties.number === "F0-302");
    const said = new Promise((res) => world.addEventListener("select", (e) => res(e.detail), { once: true }));
    world.select(office.id, { go: false });
    const d = await said;
    return { id: office.id, said: d.id, number: d.feature?.properties.number, selected: world.selected };
  });
  truly(r.said === r.id && r.selected === r.id && r.number === "F0-302", JSON.stringify(r));
});

test("a click on a room chooses it", async () => {
  // where the room's label point is on the screen, as the camera sees it now
  const at = await page.run(async () => {
    const world = window.world;
    world.select(null, { go: false });
    const office = world.package.units.find((u) => u.properties.number === "F0-315");
    const { x, z } = world.toLocal(office.properties.display_point);
    const cam = world.camera;
    const p = cam.position.clone().set(x, 0.05, z).project(cam); // just above the ground floor
    const box = world.renderer.domElement.getBoundingClientRect();
    return { id: office.id, x: box.left + ((p.x + 1) / 2) * box.width, y: box.top + ((1 - p.y) / 2) * box.height };
  });
  await page.click(at.x, at.y);
  const got = await page.run(() => window.world.selected);
  truly(got === at.id, `clicked ${got} at ${Math.round(at.x)},${Math.round(at.y)}, want ${at.id}`);
});

test("another building's package replaces the first", async () => {
  const r = await page.run(async () => {
    const pkg = await window.world.open("/campus.storeypath");
    return { project: pkg.project.id, buildings: pkg.buildings.length, building: window.world.building };
  });
  truly(r.buildings === 2 && r.building, JSON.stringify(r));
});

test("destroy empties the container", async () => {
  const left = await page.run(() => {
    window.world.destroy();
    return document.querySelector("#w").children.length;
  });
  truly(left === 0, `${left} elements left`);
});

let failed = 0;
for (const t of tests) {
  try {
    await t.fn();
    console.log(`ok    ${t.name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL  ${t.name}\n      ${e.message}`);
  }
}
if (page.errors.length) {
  failed++;
  console.log(`FAIL  no errors in the page\n      ${page.errors.slice(0, 5).join("\n      ")}`);
}
page.close();
server.close();
console.log(failed ? `${failed} failed` : `all ${tests.length} passed`);
process.exit(failed ? 1 : 0);
