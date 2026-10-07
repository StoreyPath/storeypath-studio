// node test/run.mjs (after npm run build): the built module and the baker in Node,
// then in headless Chrome with WebGL drawn in software: a conformance package
// opened, its floors and rooms built, a room chosen by a click and by select, its
// items drawn when asked for (or when one floor is shown), chosen by a click and
// bumped into by the walker, the same package pre-built (world/) shown the same,
// and the WebGL check telling a software renderer from a graphics card.

import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
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

/** A binary glTF's JSON. */
const gltfJSON = (glb) => JSON.parse(glb.toString("utf8", 20, 20 + glb.readUInt32LE(12)));

test("the baker writes each floor as binary glTF: its pieces, rooms and obstacles", () => {
  const out = mkdtempSync(join(tmpdir(), "sp-bake-"));
  try {
    execFileSync(process.execPath, [join(root, "bake.mjs"), join(packages, "simple-office.storeypath"), out]);
    const files = readdirSync(out);
    truly(files.length === 1 && files[0].endsWith("-F00.glb"), `files: ${files}`);
    const glb = readFileSync(join(out, files[0]));
    truly(glb.toString("latin1", 0, 4) === "glTF", "not binary glTF");
    const json = gltfJSON(glb);
    const x = json.scenes[0].extras.storeypath;
    truly(x.floor_id === files[0].slice(0, -4) && x.export_sequence === 1 && x.options.cutHeight === 1.25
      && x.origin.kx > 100000 && x.rooms.length >= 17, JSON.stringify({ ...x, rooms: x.rooms.length }));
    const node = (name) => json.nodes.find((n) => n.name === name);
    for (const name of ["slab", "wall", "wallTop", "floor:office", "volume:office", "ceiling", "door", "doorFrame",
      "glass", "frame", "heads", "sills", "floor:shaft:hidden", "obstacles"]) truly(node(name), `no ${name}`);
    truly(!node("wallLow") && !node("wallCut"), "the walls cut low are made from the full ones, not stored");
    const office = node("floor:office");
    truly(office.extras.material === "floor" && office.extras.type === "office" && !office.extras.view, JSON.stringify(office.extras));
    truly(json.meshes[office.mesh].primitives[0].attributes._ROOM !== undefined, "floors say whose room each vertex is");
    truly(node("floor:shaft:hidden").extras.hidden === true && node("wall").extras.view === "full", "hidden, view");
    truly(json.meshes[node("obstacles").mesh].primitives[0].mode === 1, "obstacles are lines");
    // the same package, the same files
    execFileSync(process.execPath, [join(root, "bake.mjs"), join(packages, "simple-office.storeypath"), join(out, "again")]);
    truly(readFileSync(join(out, "again", files[0])).equals(glb), "baked twice, not the same");
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

test("the baker writes a floor's items apart, in both forms, with the IDs they index", () => {
  const out = mkdtempSync(join(tmpdir(), "sp-bake-"));
  try {
    execFileSync(process.execPath, [join(root, "bake.mjs"), join(packages, "campus.storeypath"), out]);
    const json = gltfJSON(readFileSync(join(out, readdirSync(out).find((f) => f.endsWith("-HQ-F00.glb")))));
    const x = json.scenes[0].extras.storeypath;
    truly(x.builder === 2 && x.items.length === 9 && x.items.every((id) => /^[A-Z0-9]+-I\d{6}$/.test(id)), JSON.stringify(x.items));
    const node = (name) => json.nodes.find((n) => n.name === name);
    const want = { items: {}, "items:high": { view: "full" }, "items:light": { form: "light" },
      "items:light:high": { view: "full", form: "light" } };
    for (const [name, extras] of Object.entries(want)) {
      const n = node(name);
      truly(n && n.extras.material === "item" && n.extras.view === extras.view && n.extras.form === extras.form,
        `${name}: ${JSON.stringify(n?.extras)}`);
      const attributes = json.meshes[n.mesh].primitives[0].attributes;
      truly(attributes._ITEM !== undefined && attributes.COLOR_0 !== undefined && attributes.NORMAL === undefined,
        `${name}: ${Object.keys(attributes)}`); // flat-shaded: no normals to store
    }
    const tris = (name) => json.accessors[json.meshes[node(name).mesh].primitives[0].indices].count / 3;
    truly(tris("items") > 3 * tris("items:light"), `detailed ${tris("items")}, light ${tris("items:light")} triangles`);
    const upper = gltfJSON(readFileSync(join(out, readdirSync(out).find((f) => f.endsWith("-HQ-F02.glb")))));
    truly(upper.scenes[0].extras.storeypath.items.length === 0 && !upper.nodes.some((n) => n.name.startsWith("items")),
      "a floor with no items has no item pieces");
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});

// ---- in Chrome ----------------------------------------------------------------

const PAGE = `<!doctype html><meta charset="utf-8"><style>html,body{margin:0}#w,#v{width:900px;height:600px}</style>
<div id="w"></div><div id="v"></div>
<script src="/jszip.min.js"></script>
<script type="module">
  import * as sp from "/dist/world.js";
  window.sp = sp;
  window.frames = (n = 2) => new Promise((res) => {
    const step = () => (n-- > 0 ? requestAnimationFrame(step) : res());
    step();
  });
  // what a world shows: each floor's meshes (triangles, bounds), its labels, and the picture
  window.look = async (world, floor = null) => {
    world.setFloor(floor);
    world.select(null, { go: false });
    await window.frames();
    const pieces = {};
    for (const f of world.package.floorsOf(world.building)) {
      world.scene.getObjectByName(f.id).traverse((m) => {
        if (!m.isMesh) return;
        const g = m.geometry;
        g.computeBoundingBox();
        pieces[f.id + " " + m.name] = { tris: (g.index ? g.index.count : g.getAttribute("position").count) / 3,
          box: [...g.boundingBox.min.toArray(), ...g.boundingBox.max.toArray()], visible: m.visible };
      });
    }
    const r = world.renderer;
    r.render(world.scene, world.camera);
    const gl = r.getContext();
    const pixels = new Uint8Array(gl.drawingBufferWidth * gl.drawingBufferHeight * 4);
    gl.readPixels(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    const labels = [...document.querySelectorAll("#" + world.renderer.domElement.parentElement.id + " .sp3d-label")]
      .map((l) => l.textContent).sort();
    const obstacles = world.package.floorsOf(world.building).map((f) => world.plan(f.id).obstacles);
    return { pieces, pixels, labels, obstacles, calls: r.info.render.calls, prebuilt: world.prebuilt };
  };
  window.ready = true;
</script>`;

const file = (name) => readFileSync(join(packages, name));
const server = await serve(root, { "/page.html": PAGE, "/simple-office.storeypath": file("simple-office.storeypath"),
  "/campus.storeypath": file("campus.storeypath"), "/simple-office-world.storeypath": file("simple-office-world.storeypath"),
  "/campus-world.storeypath": file("campus-world.storeypath"), "/jszip.min.js": readFileSync(join(root, "../vendor/jszip.min.js")) });
const page = await launch({ webgl: true });
await page.open(`${server.url}/page.html`, 900, 600);
await page.run(async () => {
  for (let i = 0; i < 100 && !window.ready; i++) await new Promise((r) => setTimeout(r, 50));
});

test("the WebGL check refuses a software renderer", async () => {
  const r = await page.run(() => window.sp.webglSupport());
  truly(r.ok === false && (r.reason === "software" || r.reason === "no-webgl"), JSON.stringify(r));
});

test("the package reader reads items and their catalogue; an older package has none", async () => {
  const r = await page.run(async () => {
    const pkg = await window.sp.loadPackage("/campus.storeypath");
    const floor = pkg.floorsOf(pkg.buildings[0].id)[0].id;
    const on = pkg.itemsOn(floor);
    const copier = on.find((i) => i.properties.type === "COPIER");
    const where = pkg.hierarchy(copier.id);
    const old = await window.sp.loadPackage("/simple-office.storeypath");
    return { items: pkg.items.length, counted: pkg.manifest.counts.items, on: on.length,
      floors: on.every((i) => i.properties.floor_id === floor), got: pkg.get(copier.id) === copier,
      copier: pkg.itemType("COPIER"), none: pkg.itemType("SPACESHIP"), types: pkg.catalogue.types.length,
      where: [where.building?.id, where.floor?.id, where.object?.id], floor, id: copier.id,
      old: [old.items.length, old.catalogue, old.itemsOn(old.floors[0].id).length, old.itemType("COPIER")] };
  });
  truly(r.items === r.counted && r.items === 16 && r.on === 9 && r.floors && r.got, JSON.stringify(r));
  truly(r.copier.mount === "floor" && r.copier.color === "#3b6ea5" && r.copier.fields.some((f) => f.owner === "system")
    && r.none === null && r.types >= 11, JSON.stringify(r.copier));
  truly(r.where[1] === r.floor && r.where[0] === r.floor.split("-").slice(0, 3).join("-") && r.where[2] === r.id,
    `an item's place is its floor's: ${r.where}`);
  truly(JSON.stringify(r.old) === JSON.stringify([0, null, 0, null]), `older: ${JSON.stringify(r.old)}`);
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

test("a floor is drawn in a few dozen draw calls, not some for each room", async () => {
  const r = await page.run(async () => {
    const world = window.world;
    await world.open("/simple-office.storeypath");
    const seen = await window.look(world);
    const floor = world.package.floorsOf(world.building)[0].id;
    return { calls: seen.calls, rooms: world.plan(floor).spaces.length };
  });
  // before: a floor, a highlight and a volume a room, and the rest
  truly(r.rooms >= 16 && r.calls <= 40, JSON.stringify(r));
});

/** Pixels that differ by more than a little, of all. */
const differing = (a, b) => {
  let n = 0;
  for (let i = 0; i < a.length; i += 4) {
    if (Math.abs(a[i] - b[i]) > 8 || Math.abs(a[i + 1] - b[i + 1]) > 8 || Math.abs(a[i + 2] - b[i + 2]) > 8) n++;
  }
  return n / (a.length / 4);
};
/** The same meshes, as many triangles, in the same places. */
const samePieces = (live, baked, mode) => {
  const names = Object.keys(live).sort();
  truly(JSON.stringify(names) === JSON.stringify(Object.keys(baked).sort()), `${mode}: meshes `
    + `${names.filter((n) => !(n in baked))} built here only, ${Object.keys(baked).filter((n) => !(n in live))} pre-built only`);
  for (const n of names) {
    truly(live[n].tris === baked[n].tris && live[n].visible === baked[n].visible, `${mode}: ${n}: ${JSON.stringify([live[n], baked[n]])}`);
    truly(live[n].box.every((v, i) => Math.abs(v - baked[n].box[i]) < 1e-4), `${mode}: ${n} is elsewhere: ${live[n].box} ${baked[n].box}`);
  }
};

for (const name of ["simple-office", "campus"]) {
  test(`${name}, pre-built, looks as it does built here`, async () => {
    const look = async (pkg, mode) => {
      const r = await page.run(async (pkg, mode) => {
        const world = window.world;
        world.setItems(mode === "items" ? true : null); // every floor's items, a box each
        await world.open(pkg);
        world.setXray(mode === "xray");
        world.setShowHidden(mode === "hidden");
        world.setExplode(mode === "explode" ? 4 : 0);
        world.setMode(mode === "walk" ? "walk" : "dollhouse");
        // one floor (the lowest), its items detailed; in the cutaway, those below the cut
        const floor = mode === "floor" || mode === "floor-cut" ? world.package.floorsOf(world.building)[0].id : null;
        world.setCutaway(mode === "cutaway" || mode === "floor-cut");
        const seen = await window.look(world, floor);
        world.setMode("dollhouse");
        return { ...seen, pixels: Array.from(seen.pixels) };
      }, pkg, mode);
      return r;
    };
    for (const mode of ["dollhouse", "cutaway", "xray", "hidden", "explode", "walk", "items", "floor", "floor-cut"]) {
      const live = await look(`/${name}.storeypath`, mode);
      const baked = await look(`/${name}-world.storeypath`, mode);
      truly(live.prebuilt.length === 0 && baked.prebuilt.length >= 1, `pre-built: ${live.prebuilt} / ${baked.prebuilt}`);
      samePieces(live.pieces, baked.pieces, mode);
      truly(JSON.stringify(live.labels) === JSON.stringify(baked.labels), "labels differ");
      const walls = (o) => o.flat(2);
      truly(walls(live.obstacles).length > 100 && walls(live.obstacles).length === walls(baked.obstacles).length
        && walls(live.obstacles).every((v, i) => Math.abs(v - walls(baked.obstacles)[i]) < 1e-4), "the walker bumps into other things");
      const colours = new Set();
      for (let i = 0; i < live.pixels.length; i += 4 * 97) colours.add(live.pixels.slice(i, i + 3).join());
      truly(colours.size > 50, `${mode}: the picture is nearly blank (${colours.size} colours)`);
      const d = differing(live.pixels, baked.pixels);
      truly(d < 0.002, `${mode}: ${(d * 100).toFixed(2)}% of the picture differs`);
    }
    await page.run(() => {
      window.world.setCutaway(false);
      window.world.setXray(false);
      window.world.setShowHidden(false);
      window.world.setExplode(0);
      window.world.setItems(null);
    });
  });
}

test("on a pre-built floor, a click and select choose and highlight a room", async () => {
  const r = await page.run(async () => {
    const world = window.world;
    await world.open("/simple-office-world.storeypath");
    await window.frames();
    const office = world.package.units.find((u) => u.properties.number === "F0-302");
    world.select(office.id, { go: false });
    const lit = world.scene.getObjectByName("highlight");
    lit.geometry.computeBoundingBox();
    const { x, z } = world.toLocal(office.properties.display_point);
    const clicked = world.package.units.find((u) => u.properties.number === "F0-315");
    const p = world.toLocal(clicked.properties.display_point);
    const q = world.camera.position.clone().set(p.x, 0.05, p.z).project(world.camera);
    const box = world.renderer.domElement.getBoundingClientRect();
    return { prebuilt: world.prebuilt.length, selected: world.selected === office.id, lit: lit.visible,
      inside: lit.geometry.boundingBox.containsPoint({ x, y: lit.geometry.boundingBox.min.y, z }),
      id: clicked.id, at: [box.left + ((q.x + 1) / 2) * box.width, box.top + ((1 - q.y) / 2) * box.height] };
  });
  truly(r.prebuilt === 1 && r.selected && r.lit && r.inside, JSON.stringify(r));
  await page.click(r.at[0], r.at[1]);
  const got = await page.run(() => window.world.selected);
  truly(got === r.id, `clicked ${got}, want ${r.id}`);
});

test("another building's pre-built floors are read when it is shown", async () => {
  const r = await page.run(async () => {
    const world = window.world;
    const pkg = await world.open("/campus-world.storeypath");
    const first = world.prebuilt.slice();
    const other = pkg.buildings[1].id;
    await world.setBuilding(other);
    const floors = pkg.floorsOf(other).map((f) => f.id);
    world.setFloor(floors[1]);
    return { first, floors, prebuilt: world.prebuilt, floor: world.floor };
  });
  truly(r.first.length >= 2 && r.floors.every((f) => r.prebuilt.includes(f)) && r.floor === r.floors[1], JSON.stringify(r));
});

test("items are drawn only when asked for, or when one floor is shown: detailed on one, a box each on more", async () => {
  const r = await page.run(async () => {
    const world = window.world;
    await world.open("/campus.storeypath");
    const floors = world.package.floorsOf(world.building).map((f) => f.id);
    const seen = () => {
      const meshes = {};
      world.scene.traverse((m) => {
        if (m.isMesh && m.name.startsWith("items")) meshes[`${m.parent.name.split("-").pop()} ${m.name}`] = m.visible;
      });
      return { items: world.items, meshes };
    };
    const all = seen();
    world.setFloor(floors[0]);
    const one = seen();
    world.setCutaway(true);
    const cut = seen();
    world.setCutaway(false);
    world.setItems(false);
    const off = seen();
    world.setFloor(null);
    world.setItems(true);
    const every = seen();
    world.setItems(null);
    const auto = seen();
    return { all, one, cut, off, every, auto };
  });
  const shown = (s) => Object.keys(s.meshes).filter((k) => s.meshes[k]).sort().join(", ");
  truly(!r.all.items && Object.keys(r.all.meshes).length === 0, `all floors: none made ${JSON.stringify(r.all)}`);
  truly(r.one.items && shown(r.one) === "F00 items, F00 items:high", `one floor: ${JSON.stringify(r.one)}`);
  truly(shown(r.cut) === "F00 items", `cut low: those below the cut ${JSON.stringify(r.cut)}`);
  truly(!r.off.items && shown(r.off) === "", `asked not to: ${JSON.stringify(r.off)}`);
  truly(r.every.items && shown(r.every) === "F00 items:light, F00 items:light:high, F01 items:light, F01 items:light:high",
    `asked for, every floor: ${JSON.stringify(r.every)}`);
  truly(!r.auto.items && shown(r.auto) === "", `as before: ${JSON.stringify(r.auto)}`);
});

test("a floor full of items costs a few draw calls", async () => {
  const r = await page.run(async () => {
    const world = window.world;
    await world.open("/campus.storeypath");
    const floor = world.package.floorsOf(world.building)[0].id;
    world.setItems(false);
    const off = (await window.look(world, floor)).calls;
    world.setItems(true);
    const on = (await window.look(world, floor)).calls;
    world.setItems(null);
    return { off, on };
  });
  truly(r.on > r.off && r.on - r.off <= 4, JSON.stringify(r)); // two pieces, and their shadows
});

for (const name of ["campus", "campus-world"]) {
  test(`${name}: a click on an item chooses it, and select highlights it`, async () => {
    const at = await page.run(async (name) => {
      const world = window.world;
      await world.open(`/${name}.storeypath`);
      const floor = world.package.floorsOf(world.building)[0];
      world.setFloor(floor.id);
      const desk = world.package.itemsOn(floor.id).find((i) => i.properties.type === "DESK-DIRECTOR");
      world.select(desk.id); // the view goes to it
      await new Promise((r) => setTimeout(r, 1500));
      const lit = world.scene.getObjectByName("highlight");
      lit.geometry.computeBoundingBox();
      const { x, z } = world.toLocal(desk.properties.display_point);
      const y = floor.properties.elevation + desk.properties.height_m;
      const inside = lit.geometry.boundingBox.containsPoint({ x, y, z });
      world.select(null, { go: false });
      await window.frames();
      const p = world.camera.position.clone().set(x, y, z).project(world.camera);
      const box = world.renderer.domElement.getBoundingClientRect();
      window.said = new Promise((res) => world.addEventListener("select", (e) => res(e.detail), { once: true }));
      return { id: desk.id, prebuilt: world.prebuilt.length, inside,
        x: box.left + ((p.x + 1) / 2) * box.width, y: box.top + ((1 - p.y) / 2) * box.height };
    }, name);
    truly(at.inside && (name === "campus" ? at.prebuilt === 0 : at.prebuilt > 0), JSON.stringify(at));
    await page.click(at.x, at.y);
    const got = await page.run(async () => {
      const d = await window.said;
      return { id: d.id, kind: d.feature?.properties.kind, type: d.feature?.properties.type, selected: window.world.selected };
    });
    truly(got.id === at.id && got.selected === at.id && got.kind === "item" && got.type === "DESK-DIRECTOR",
      `clicked ${JSON.stringify(got)}, want ${at.id}`);
  });
}

test("the walker bumps into desks, not into access points", async () => {
  const r = await page.run(async () => {
    const world = window.world;
    await world.open("/campus.storeypath");
    const floor = world.package.floorsOf(world.building)[0];
    const items = world.package.itemsOn(floor.id);
    world.setMode("walk");
    // stand at `from`, look along (dx, dz), walk for a while; how far it went that way
    const walk = async (from, dx, dz) => {
      world.camera.position.set(from.x, world.camera.position.y, from.z);
      world.camera.rotation.set(0, Math.atan2(-dx, -dz), 0, "YXZ");
      window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyW" }));
      await new Promise((r) => setTimeout(r, 2500));
      window.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyW" }));
      const p = world.player;
      return (p.x - from.x) * dx + (p.z - from.z) * dz;
    };
    const facing = (item) => { // where its front faces, and along its width, in x and z
      const h = (item.properties.heading * Math.PI) / 180;
      return { front: [Math.sin(h), -Math.cos(h)], across: [-Math.cos(h), -Math.sin(h)] };
    };
    const desk = items.find((i) => i.properties.type === "DESK-DIRECTOR");
    const c = world.toLocal(desk.properties.display_point), { front } = facing(desk);
    const behind = desk.properties.depth_m / 2 + 1.0; // a metre behind it (its front is towards a window), walking at it
    const toDesk = await walk({ x: c.x - front[0] * behind, z: c.z - front[1] * behind }, front[0], front[1]);
    const ap = items.find((i) => i.properties.type === "ACCESS-POINT");
    const copier = items.find((i) => i.properties.type === "COPIER"); // in the same corridor, along it
    const a = world.toLocal(ap.properties.display_point), { across } = facing(copier);
    const underAp = await walk({ x: a.x - across[0] * 1.5, z: a.z - across[1] * 1.5 }, across[0], across[1]);
    world.setMode("dollhouse");
    return { toDesk, room: 1.0 - 0.22, underAp };
  });
  truly(r.toDesk > 0.3 && r.toDesk < r.room + 0.05, `walked ${r.toDesk.toFixed(2)} m at the desk, ${r.room} m from it`);
  truly(r.underAp > 2, `walked ${r.underAp.toFixed(2)} m under the access point`);
});

test("pre-built floors are used only as they were built: this export, this builder, these sizes", async () => {
  const r = await page.run(async () => {
    // other sizes: built here
    const other = new window.sp.StoreyPathWorld("#v", { cutHeight: 1.0 });
    await other.open("/campus-world.storeypath");
    const sized = other.prebuilt.length;
    other.destroy();
    // a file of an earlier export: built here
    const zip = await window.JSZip.loadAsync(await (await fetch("/campus-world.storeypath")).arrayBuffer());
    const manifest = JSON.parse(await zip.file("manifest.json").async("string"));
    manifest.export.sequence += 1;
    zip.file("manifest.json", JSON.stringify(manifest));
    await window.world.open(await zip.generateAsync({ type: "arraybuffer" }));
    const stale = window.world.prebuilt.length;
    // the files with their extras changed: as written, then of an older builder (before
    // 2, they had no items), which are built here
    const rewritten = async (change) => {
      const zip = await window.JSZip.loadAsync(await (await fetch("/campus-world.storeypath")).arrayBuffer());
      for (const name of Object.keys(zip.files).filter((n) => n.endsWith(".glb"))) {
        const glb = new Uint8Array(await zip.file(name).async("arraybuffer"));
        const length = new DataView(glb.buffer).getUint32(12, true);
        const json = JSON.parse(new TextDecoder().decode(glb.subarray(20, 20 + length)));
        change(json.scenes[0].extras.storeypath);
        const text = new TextEncoder().encode(JSON.stringify(json).padEnd(Math.ceil(JSON.stringify(json).length / 4) * 4));
        const out = new Uint8Array(20 + text.length + glb.length - 20 - length);
        out.set(glb.subarray(0, 20));
        out.set(text, 20);
        out.set(glb.subarray(20 + length), 20 + text.length);
        new DataView(out.buffer).setUint32(8, out.length, true);
        new DataView(out.buffer).setUint32(12, text.length, true);
        zip.file(name, out);
      }
      await window.world.open(await zip.generateAsync({ type: "arraybuffer" }));
      return window.world.prebuilt.length;
    };
    const same = await rewritten(() => {});
    const old = await rewritten((x) => delete x.builder);
    window.world.setFloor(window.world.package.floors[0].id);
    const furnished = window.world.scene.getObjectByName("items") !== undefined;
    return { sized, stale, same, old, furnished, floors: window.world.plan(window.world.package.floors[0].id) !== null };
  });
  truly(r.sized === 0 && r.stale === 0 && r.same >= 2 && r.old === 0 && r.furnished && r.floors, JSON.stringify(r));
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
