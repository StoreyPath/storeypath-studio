// The README's pictures, taken again from the real app: node docs/media/capture.mjs [scene…]
//
// It starts a throwaway Studio (docs/media/stage.py: a database of its own, the demo campus,
// made-up people), drives it in headless Chrome drawing on the graphics card (the viewers'
// harness, "gpu": macOS, Metal), and writes docs/images/ (docs/media/encode.py: Pillow makes
// the WebP pictures and the animations). With scene names, those alone; --stage <file> uses a
// stage already running (its first line, saved); --raw <folder> keeps the screenshots as
// taken. Needs Studio's Python (studio/.venv: uv sync --extra vision), Node.js, Chrome and the
// viewer built (npm run build in storeypath-viewer/viewer/svg and /world).

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const PYTHON = join(ROOT, "studio/.venv/bin/python");
const { launch } = await import(pathToFileURL(join(ROOT, "storeypath-viewer/viewer/svg/test/harness.mjs")).href);

const argv = process.argv.slice(2);
const option = (name) => {
  const i = argv.indexOf(name);
  return i < 0 ? null : argv.splice(i, 2)[1];
};
const stageFile = option("--stage");
const OUT = resolve(option("--out") ?? join(ROOT, "docs/images"));
const VIEWER_OUT = resolve(option("--viewer-out") ?? join(ROOT, "storeypath-viewer/docs/images")); // the viewer's README's
const keepRaw = option("--raw");
const rawDir = keepRaw ?? mkdtempSync(join(tmpdir(), "sp-media-"));
const wanted = new Set(argv);
mkdirSync(rawDir, { recursive: true });
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const W = 1600, H = 1000; // the pictures' size (CSS pixels)

// ---- the stage: a Studio of its own ------------------------------------------------------------

let stage, stageProc = null;
if (stageFile) {
  stage = JSON.parse(readFileSync(stageFile, "utf8").split("\n")[0]);
} else {
  stageProc = spawn(PYTHON, [join(HERE, "stage.py")], { stdio: ["pipe", "pipe", "inherit"] });
  stage = await new Promise((ok, fail) => {
    let said = "";
    stageProc.stdout.on("data", (d) => {
      said += d;
      if (said.includes("\n")) ok(JSON.parse(said.split("\n")[0]));
    });
    stageProc.on("exit", (code) => fail(new Error(`the stage ended (${code})`)));
  });
}
const BASE = stage.address, DEMO = stage.demo;
const MAIN = `${DEMO}-CAMPUS-MAIN`; // the demo's main building
const stopStage = () => {
  if (stageProc && stageProc.exitCode === null) stageProc.stdin.end(); // it stops Studio and drops its database
};
process.on("exit", stopStage);

// ---- a browser, and what a scene does with it ----------------------------------------------------

/** A page in its own Chrome (its own cookies), logged in as ``who``. */
async function browser(who, { gl = "gpu" } = {}) {
  const page = await launch({ webgl: gl, timeout: 1200 });
  const R = (fn, ...args) => page.run(fn, ...args);
  const p = {
    page, R,
    errors: page.errors,
    async open(path, { width = W, height = H, dpr = 1 } = {}) {
      await page.open(BASE + path, width, height); // (loaded)
      if (dpr !== 1) await page.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: dpr, mobile: false });
    },
    async until(fn, what, ms = 30000, ...args) {
      const end = Date.now() + ms;
      let last;
      while (Date.now() < end) {
        last = await R(fn, ...args);
        if (last) return last;
        await sleep(100);
      }
      throw new Error(`${what} (last: ${JSON.stringify(last)})`);
    },
    /** A screenshot, as taken (a PNG in the raw folder): its path. */
    async shot(name, clip) {
      const { data } = await page.send("Page.captureScreenshot", { format: "png", ...(clip ? { clip: { ...clip, scale: 1 } } : {}) });
      const file = join(rawDir, `${name}.png`);
      writeFileSync(file, Buffer.from(data, "base64"));
      console.log(`  ${name}`);
      return file;
    },
    async key(key, { shift = false, meta = false } = {}) {
      const named = { Escape: [27, "Escape"], Enter: [13, "Enter"], PageUp: [33, "PageUp"], PageDown: [34, "PageDown"] };
      const [keyCode, code] = named[key] ?? [key.toUpperCase().charCodeAt(0), /\d/.test(key) ? `Digit${key}` : `Key${key.toUpperCase()}`];
      const modifiers = (shift ? 8 : 0) | (meta ? 4 : 0);
      const text = key.length === 1 && !meta ? key : key === "Enter" ? "\r" : undefined;
      await page.send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode: keyCode, modifiers, ...(text ? { text } : {}) });
      await page.send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: keyCode, modifiers });
      await sleep(120);
    },
    async type(text) {
      for (const ch of text) await page.send("Input.insertText", { text: ch });
    },
    close: () => page.close(),
  };
  // logged in, in this browser
  await p.open("/login.html");
  const status = await R(async (username, password) => (await fetch("/api/login", { method: "POST",
    headers: { "X-StoreyPath": "1", "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) })).status,
  who, stage.passwords[who]);
  if (status !== 200) throw new Error(`${who} could not log in (${status})`);
  return p;
}

/** Review open on a floor, ready (its plan drawn). */
async function review(p, floor, { theme = "dark", space = null } = {}) {
  await p.R((t) => localStorage.setItem("storeypath.theme", t), theme);
  const hash = new URLSearchParams({ floor, ...(space ? { space } : {}) });
  await p.open(`/review.html?p=${DEMO}#${hash}`);
  await p.until(() => document.body.classList.contains("ready") && window.storeypathReview?.state.floor, "Review", 60000);
  await p.until(() => !window.storeypathReview.state.busy && document.querySelectorAll("#spaces path").length > 5, "the plan", 30000);
  await sleep(1200);
}

/** The ID of a room of the floor Review shows, by its number or name. */
const roomId = (p, key) => p.R((key) => window.storeypathReview.state.floor.spaces
  .find((s) => s.number === key || s.name === key)?.id ?? null, key);

/** Review's 3D view, built, its world drawn as it will stay. */
async function review3d(p, mode = "3") {
  await p.key(mode);
  await p.until(() => window.storeypathReview.view3d.building && !window.storeypathReview.view3d.busy, "the 3D view", 120000);
  await p.R(() => window.storeypathReview.view3d.world.ready());
  await sleep(1500);
}

/** Studio's 3D page on a building of the demo. */
async function world(p, building, more = {}) {
  const q = new URLSearchParams({ pkg: `/api/projects/${DEMO}/preview.storeypath?building=${building}`, building, ...more });
  await p.open(`/world.html?${q}`);
  await p.until(() => document.body.classList.contains("ready"), "the 3D page", 120000);
  await p.R(() => window.storeypathWorld.ready());
  await sleep(1500);
}

/** The dollhouse camera put round the point it turns around: ``azimuth`` (degrees, from the
 * world's south, clockwise seen from above), ``elevation`` (degrees above the horizon) and
 * ``distance`` (metres; default as it is, times ``closer``). The orbit's target is kept: only the
 * camera moves. */
const orbit = (p, { azimuth, elevation, distance = null, closer = 1 }) => p.R((az, el, dist, closer) => {
  const w = window.storeypathWorld ?? window.storeypathReview.view3d.world;
  const cam = w.camera, t = w.target, P = cam.position;
  const d = cam.getWorldDirection(cam.position.clone()); // (a vector to fill)
  const flat = Math.hypot(t.x - P.x, t.z - P.z), along = flat / Math.max(1e-6, Math.hypot(d.x, d.z));
  const ty = P.y + d.y * along; // the target's height, where the camera looks
  const r = (dist ?? Math.hypot(t.x - P.x, ty - P.y, t.z - P.z)) * closer;
  const a = (az * Math.PI) / 180, e = (el * Math.PI) / 180;
  P.set(t.x + r * Math.cos(e) * Math.sin(a), ty + r * Math.sin(e), t.z + r * Math.cos(e) * Math.cos(a));
  return { target: [t.x, ty, t.z], r };
}, azimuth, elevation, distance, closer);

// ---- the scenes --------------------------------------------------------------------------------------
//
// Each writes one or more pictures (jobs for encode.py).

const jobs = []; // what encode.py makes of the screenshots
const scenes = {};

/** The review editor: the tools, the rooms to review, review mode on a room the rules could not type. */
scenes.review = async () => {
  const p = await browser("maya");
  await review(p, `${MAIN}-F00`);
  await p.R(() => window.storeypathReview.run("review.start"));
  await p.until(() => !document.getElementById("review-bar").hidden, "review mode");
  // to the copy room: the rules do not know its name
  for (let i = 0; i < 4; i++) {
    const name = await p.R(() => window.storeypathReview.state.byId.get(window.storeypathReview.state.selected)?.name);
    if (name === "COPY ROOM") break;
    await p.key("n");
    await sleep(700);
  }
  await sleep(1200);
  jobs.push({ in: await p.shot("review"), out: "review.webp", lossless: true });
  await p.key("Escape");
  p.close();
};

/** ⌘K: rooms, items, floors and commands, found as you type. */
scenes.palette = async () => {
  const p = await browser("maya");
  await review(p, `${MAIN}-F01`);
  await p.key("k", { meta: true });
  await p.until(() => document.querySelector("dialog.palette[open]"), "the palette");
  await p.type("manager");
  await sleep(900);
  jobs.push({ in: await p.shot("palette"), out: "palette.webp", lossless: true });
  await p.key("Escape");
  p.close();
};

/** The room or item of the floor Review shows chosen in its 3D view, as a click there chooses it. */
const choose3d = async (p, id, { go = true } = {}) => {
  await p.R((id, go) => window.storeypathReview.view3d.world.select(id, { go }), id, go);
  await p.R((id) => window.storeypathReview.view3d.world.dispatchEvent(new CustomEvent("select", { detail: { id } })), id);
  await p.until((id) => [window.storeypathReview.state.asset, window.storeypathReview.state.selected].includes(id), "chosen", 10000, id);
};

/** Where a point of a floor (the building's own frame, metres) is on the screen, in a world's
 * dollhouse view: found by asking the world what is under the screen, three times closer. */
const screenOf = (p, local) => p.R((local) => {
  const w = window.storeypathWorld ?? window.storeypathReview.view3d.world;
  const box = w.renderer.domElement.getBoundingClientRect();
  let s = [box.left + box.width / 2, box.top + box.height / 2];
  const at = (q) => w.pointAt(q[0], q[1])?.local;
  for (let i = 0; i < 6; i++) {
    const a = at(s), b = at([s[0] + 10, s[1]]), c = at([s[0], s[1] + 10]);
    if (!a || !b || !c) return s;
    const j = [[(b[0] - a[0]) / 10, (c[0] - a[0]) / 10], [(b[1] - a[1]) / 10, (c[1] - a[1]) / 10]];
    const det = j[0][0] * j[1][1] - j[0][1] * j[1][0], d = [local[0] - a[0], local[1] - a[1]];
    s = [s[0] + (j[1][1] * d[0] - j[0][1] * d[1]) / det, s[1] + (-j[1][0] * d[0] + j[0][0] * d[1]) / det];
  }
  return s;
}, local);

/** Furniture: an item chosen in 3D, its asset tag in the inspector. */
scenes.furniture = async () => {
  const p = await browser("maya");
  await review(p, `${MAIN}-F02`);
  await review3d(p);
  await p.R(() => window.storeypathReview.view3d.world.setCutaway(true));
  const desk = await p.R(() => window.storeypathReview.state.floor.items.find((a) => a.type === "DESK-PRESIDENT").id);
  await p.R((id) => window.storeypathReview.view3d.world.select(id), await roomId(p, "205")); // the view to its room
  await sleep(1500); // (the view's flight to it, over)
  await choose3d(p, desk, { go: false });
  await orbit(p, { azimuth: -18, elevation: 54, distance: 12 }); // over its cut-away window wall, close
  await sleep(2500);
  jobs.push({ in: await p.shot("furniture"), out: "furniture.webp", quality: 90 });
  p.close();
};

/** Placing an item in 3D: the types to place, its ghost where the pointer is, lined up with the wall. */
scenes.place = async () => {
  const p = await browser("maya");
  await review(p, `${MAIN}-F01`);
  await review3d(p);
  await p.R(() => window.storeypathReview.view3d.world.setCutaway(true));
  const office = await roomId(p, "113");
  await choose3d(p, office);
  await sleep(1500);
  await orbit(p, { azimuth: -24, elevation: 52, distance: 18 });
  await p.key("Escape");
  await p.key("i");
  await p.until(() => document.querySelector(".type-picker .type-tile"), "the types to place");
  await p.R(() => [...document.querySelectorAll(".type-tile")].find((b) => /copier/i.test(b.textContent))?.click());
  await sleep(600);
  // the pointer in the office's corner by its door: the magnet takes the copier to the walls, square to them
  const at = await screenOf(p, [125 + 34.85, 48 + 12.15]);
  for (let i = 5; i >= 0; i--) await p.page.mouse("mouseMoved", at[0] + i * 6, at[1] + i * 4);
  await p.R(async () => (await import("/review/view3d.js")).aimSoon()); // (as a move of the pointer does)
  await sleep(1200);
  jobs.push({ in: await p.shot("place"), out: "place.webp", quality: 90 });
  await p.key("Escape");
  p.close();
};

/** Paint finishes in 3D: the palette of floor finishes open over the floor. */
scenes.paint = async () => {
  const p = await browser("maya");
  await review(p, `${MAIN}-F00`);
  await review3d(p);
  await p.R(() => window.storeypathReview.view3d.world.setCutaway(true));
  await choose3d(p, await roomId(p, "004"));
  await sleep(1500);
  await orbit(p, { azimuth: -30, elevation: 50, distance: 26 });
  await p.key("Escape");
  await p.key("p");
  await p.until(() => !document.getElementById("tool-options").hidden, "the brushes");
  await p.R(() => document.querySelector(".fin-brush").click());
  await p.until(() => document.querySelector(".fin-picker"), "the floor finishes");
  await sleep(2000);
  jobs.push({ in: await p.shot("paint"), out: "paint.webp", quality: 90 });
  await p.key("Escape");
  await p.key("Escape");
  p.close();
};

/** Studio's 3D page: every floor of the main building, lifted apart, cut away. */
scenes.world = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { explode: "6", cutaway: "1" });
  await orbit(p, { azimuth: -34, elevation: 34 });
  await sleep(2500);
  jobs.push({ in: await p.shot("world"), out: "world.webp", quality: 90 });
  p.close();
};

/** The two looks of one floor: Real, and Model (white, its edges drawn), split down the middle. */
scenes.looks = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { floor: `${MAIN}-F01`, cutaway: "1" });
  await p.R(() => { // the view's middle on the corridor's: the floor in the middle of the picture
    const w = window.storeypathWorld;
    w.select(w.package.spaces.find((s) => s.properties.name === "CORRIDOR" && s.properties.floor_id.endsWith("-F01")).id);
  });
  await sleep(1500);
  await p.R(() => window.storeypathWorld.select(null, { go: false }));
  await orbit(p, { azimuth: 6, elevation: 56, distance: 44 }); // from the south: the floor across the picture
  await p.R(() => {
    document.body.classList.add("presenting");
    window.storeypathWorld.setLabels(false); // (no label cut in two)
  });
  await sleep(2500);
  const real = await p.shot("look-real");
  await p.R(() => window.storeypathWorld.setStyle("model"));
  await p.R(() => window.storeypathWorld.ready());
  await sleep(2500);
  const model = await p.shot("look-model");
  await p.R(() => window.storeypathWorld.setStyle("real"));
  jobs.push({ split: [real, model], out: "looks.webp", quality: 90 });
  p.close();
};

/** X-ray on the 3D page: see-through walls, every room tinted by its type. */
scenes.xray = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { xray: "1", floor: `${MAIN}-F00` });
  const { r } = await orbit(p, { azimuth: 24, elevation: 38 });
  await orbit(p, { azimuth: 24, elevation: 38, distance: r * 0.8 });
  await sleep(2500);
  jobs.push({ in: await p.shot("xray"), out: "xray.webp", quality: 90 });
  p.close();
};

/** Share an area: a part of the floor dragged over, its sample's preview (what goes, what is taken out). */
scenes.share = async () => {
  const p = await browser("maya");
  await review(p, `${MAIN}-F00`);
  await p.key("a");
  const [a, b] = await p.R(() => {
    const r = window.storeypathReview, by = (n) => r.state.floor.spaces.find((s) => s.number === n);
    const box = (s) => r.state.bounds.get(s.id);
    const v = r.state.view, svg = document.getElementById("svg").getBoundingClientRect();
    const at = (x, y) => [svg.left + x * v.k + v.tx, svg.top - y * v.k + v.ty];
    const [x0, , , y1] = box(by("001")), [, y0, x1] = box(by("003"));
    return [at(x0 - 0.6, y1 + 0.6), at(x1 + 0.6, y0 - 0.6)];
  });
  await p.page.drag(a, b, 12);
  await p.until(() => document.querySelector("dialog.sample[open] .sample-pictures img"), "the sample's preview", 90000);
  await p.R(() => {
    const note = document.querySelector("dialog.sample textarea");
    if (note) {
      note.value = "The reception's entrance doors are read as one wide door";
      note.dispatchEvent(new Event("input"));
    }
  });
  await sleep(1500);
  jobs.push({ in: await p.shot("share"), out: "share-area.webp", lossless: true });
  await p.R(() => [...document.querySelectorAll("dialog.sample button")].find((x) => x.textContent === "Cancel")?.click());
  await p.key("Escape");
  p.close();
};

/** The light theme: Review on the executives' floor, the president's office chosen. */
scenes.light = async () => {
  const p = await browser("maya");
  await review(p, `${MAIN}-F02`, { theme: "light" });
  const office = await roomId(p, "205");
  await p.R(async (id) => (await import("/review/selection.js")).select(id), office); // (as a click on it chooses it)
  await p.until((id) => window.storeypathReview.state.selected === id, "the office chosen", 10000, office);
  await sleep(1500);
  jobs.push({ in: await p.shot("light"), out: "light.webp", lossless: true });
  await p.R(() => localStorage.setItem("storeypath.theme", "dark"));
  p.close();
};

/** Many people at once: Omar looks at the first floor while Maya edits it (her lock, her changes
 * shown as she saves them, the history), and Lena looks too. Her changes are undone after. */
scenes.together = async () => {
  const floor = `${MAIN}-F01`;
  const maya = await browser("maya", { gl: false });
  const lena = await browser("lena", { gl: false });
  const omar = await browser("omar");
  await review(maya, floor);
  await review(lena, floor);
  await review(omar, floor);
  // Maya's changes, from her page: a type for the room the rules left, a desk moved, a room's walls
  const change = (path, body) => maya.R(async (path, body) => {
    const { PAGE } = await import("/together.js");
    const res = await fetch(`/api/projects/${new URLSearchParams(location.search).get("p")}/${path}`, { method: "POST",
      headers: { "X-StoreyPath": "1", "Content-Type": "application/json", "X-StoreyPath-Page": PAGE }, body: JSON.stringify(body) });
    return res.status;
  }, path, body);
  const room = await roomId(maya, "114"), huddle = await roomId(maya, "116");
  const desk = await maya.R(() => {
    const r = window.storeypathReview, office = r.state.floor.spaces.find((s) => s.number === "112");
    const [x0, y0, x1, y1] = r.state.bounds.get(office.id);
    return r.state.floor.items.find((a) => a.x > x0 && a.x < x1 && a.y > y0 && a.y < y1 && a.type.startsWith("DESK"));
  });
  const said = [await change(`objects/${room}`, { correction: { type: "office", name: "OFFICE" } }),
    await change(`items/${desk.id}`, { x: desk.x - 0.6 }),
    await change(`objects/${huddle}`, { wall_finish: "WALL-PAINT-TERRACOTTA" })];
  console.log(`    Maya's changes: ${said.join(" ")}`);
  await omar.until(() => !document.getElementById("lock-banner").hidden, "Maya's lock, shown", 20000);
  await omar.R(() => document.getElementById("history-open").click());
  await omar.until(() => document.querySelectorAll("#history-list li").length >= 2, "the history", 20000);
  await omar.R(() => document.activeElement?.blur());
  await omar.page.mouse("mouseMoved", 760, 900); // (no tooltip over the picture)
  await sleep(2500);
  jobs.push({ in: await omar.shot("together"), out: "together.webp", lossless: true });
  for (let i = 0; i < said.filter((s) => s === 200).length; i++) await change("undo", { floor });
  maya.close();
  lena.close();
  omar.close();
};

/** A drawing comes in: three plans on one sheet, found and titled, ready to add as floors. */
scenes.plans = async () => {
  const p = await browser("maya");
  await p.R(() => localStorage.setItem("storeypath.theme", "dark"));
  await p.open(`/#/p/${stage.fresh}`);
  await p.until(() => document.querySelector("label.drop input[type=file]"), "the project's page");
  const { result } = await p.page.send("Runtime.evaluate", { expression: "document.querySelector('label.drop input[type=file]')" });
  await p.page.send("DOM.setFileInputFiles", { files: [stage.sheet], objectId: result.objectId });
  // what is private in it, found as it comes in: taken out before it is kept
  await p.until(() => document.querySelector("dialog.private-review[open]"), "the private information found", 60000);
  await sleep(800);
  jobs.push({ in: await p.shot("privacy"), out: "privacy.webp", lossless: true });
  await p.R(() => document.querySelector("dialog.private-review button.primary").click());
  await p.until(() => document.querySelectorAll(".plans > *").length >= 3, "the plans found", 120000);
  await sleep(1200);
  await p.R(() => {
    document.querySelector(".plans").closest(".card").scrollIntoView({ block: "start" });
    window.scrollBy(0, -56); // (its heading in the picture too)
    document.getElementById("toast").hidden = true;
  });
  await sleep(800);
  jobs.push({ in: await p.shot("plans"), out: "plans-found.webp", lossless: true });
  p.close();
};

/** The 3D page walking: before an office's door, the cross on it, the door open; the map, the room. */
scenes.walk = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { floor: `${MAIN}-F01` });
  const door = await p.R(() => {
    const w = window.storeypathWorld, floor = `${w.building}-F01`;
    const office = w.package.spaces.find((s) => s.properties.number === "113");
    const c = w.toLocal(office.properties.display_point);
    const doors = w.plan(floor).doors.map((d) => ({ d, m: [(d.span[0][0] + d.span[1][0]) / 2, (d.span[0][1] + d.span[1][1]) / 2] }))
      .sort((a, b) => Math.hypot(a.m[0] - c.x, a.m[1] - c.z) - Math.hypot(b.m[0] - c.x, b.m[1] - c.z));
    const { d, m } = doors[0];
    const [[ax, az], [bx, bz]] = d.span, len = Math.hypot(bx - ax, bz - az);
    let nx = -(bz - az) / len, nz = (bx - ax) / len;
    if ((c.x - m[0]) * nx + (c.z - m[1]) * nz > 0) { nx = -nx; nz = -nz; } // the corridor's side
    w.setDoorOpen(d.id, false, { instant: true });
    w.setMode("walk", { floor, at: { x: m[0] + nx * 1.7, z: m[1] + nz * 1.7 }, heading: Math.atan2(nx, nz) });
    return d.id;
  });
  await sleep(1500);
  await p.page.click(800, 520); // the mouse taken: the cross shows
  await p.until(() => window.storeypathWorld.walking, "the mouse taken", 10000);
  await p.R((id) => window.storeypathWorld.setDoorOpen(id, true), door);
  await sleep(1800);
  jobs.push({ in: await p.shot("world-walk"), out: "world-walk.webp", quality: 90 });
  p.close();
};

// ---- animations: screenshots taken as fast as they come, then put on a timeline ------------------------

/** Frames of a page as it changes (JPEGs, each with when it was taken), until ``stop``; ``now()``
 * marks a moment, to cut the timeline at. ``clip``: a part of the page alone (CSS pixels). */
function recorder(p, name, { clip = null } = {}) {
  const frames = [];
  const t0 = Date.now();
  let on = true, paused = false, n = 0, taking = null;
  const loop = (async () => {
    while (on) {
      if (paused) {
        await sleep(20);
        continue;
      }
      const t = Date.now() - t0;
      taking = p.page.send("Page.captureScreenshot", { format: "jpeg", quality: 90, optimizeForSpeed: true,
        ...(clip ? { clip: { ...clip, scale: 1 } } : {}) });
      const { data } = await taking;
      taking = null;
      const file = join(rawDir, `${name}-${String(++n).padStart(4, "0")}.jpg`);
      writeFileSync(file, Buffer.from(data, "base64"));
      frames.push({ file, t });
    }
  })();
  return {
    frames,
    now: () => Date.now() - t0,
    /** No frames while the page goes to another (a screenshot then waits for it). */
    async pause() {
      paused = true;
      await taking;
    },
    resume() {
      paused = false;
    },
    async stop() {
      on = false;
      await loop;
      return frames;
    },
  };
}

const FPS = 12; // the animations' frames a second
const SLOW = 2.5; // how much slower a motion is taken than it is shown: screenshots come at ~8 a second

/** The recording between two moments (ms), played ``speed`` times as fast: [file, ms] at FPS. */
function clip(frames, from, to, speed = 1) {
  const out = [], step = 1000 / FPS;
  for (let t = from; t <= to; t += step * speed) {
    const f = frames.filter((x) => x.t <= t).at(-1) ?? frames[0];
    out.push([f.file, step]);
  }
  return out;
}

/** The recording as it was at a moment, held for ``ms``. */
function hold(frames, at, ms) {
  return [[(frames.filter((x) => x.t <= at).at(-1) ?? frames[0]).file, ms]];
}

/** One frame, for as long as the frames after it repeat it. */
function merged(timeline) {
  const out = [];
  for (const [file, ms] of timeline) {
    if (out.length && out.at(-1)[0] === file) out.at(-1)[1] += ms;
    else out.push([file, ms]);
  }
  return out.map(([f, ms]) => [f, Math.round(ms)]);
}

/** The dollhouse camera turned round its target, from ``from`` to ``to`` degrees of azimuth (and
 * elevation), over ``ms``, eased: drawn as the world draws its frames. */
const turnAround = (p, ms, from, to) => p.R((ms, from, to) => new Promise((done) => {
  const w = window.storeypathWorld ?? window.storeypathReview.view3d.world;
  const cam = w.camera, t = w.target, P = cam.position, d = cam.getWorldDirection(P.clone());
  const along = Math.hypot(t.x - P.x, t.z - P.z) / Math.max(1e-6, Math.hypot(d.x, d.z)), ty = P.y + d.y * along;
  const r0 = Math.hypot(t.x - P.x, ty - P.y, t.z - P.z);
  const start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / ms), e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
    const az = ((from[0] + (to[0] - from[0]) * e) * Math.PI) / 180, el = ((from[1] + (to[1] - from[1]) * e) * Math.PI) / 180;
    const r = r0 * (from[2] + (to[2] - from[2]) * e);
    P.set(t.x + r * Math.cos(el) * Math.sin(az), ty + r * Math.sin(el), t.z + r * Math.cos(el) * Math.cos(az));
    if (k < 1) requestAnimationFrame(step);
    else done();
  };
  requestAnimationFrame(step);
}), ms, from, to);

/** Walking: the walker carried ``metres`` the way it looks over ``ms`` (turning ``turn`` radians
 * as it goes), eased, as walking with the keys would (walls are not asked about). */
const walkOn = (p, ms, metres, turn = 0) => p.R((ms, metres, turn) => new Promise((done) => {
  const w = window.storeypathWorld ?? window.storeypathReview.view3d.world;
  const cam = w.camera, P0 = cam.position.clone(), yaw0 = cam.rotation.y;
  const d = cam.getWorldDirection(P0.clone()), len = Math.hypot(d.x, d.z), dx = d.x / len, dz = d.z / len;
  const start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / ms), e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
    cam.position.set(P0.x + dx * metres * e, P0.y, P0.z + dz * metres * e);
    cam.rotation.set(cam.rotation.x, yaw0 + turn * e, 0, "YXZ");
    if (k < 1) requestAnimationFrame(step);
    else done();
  };
  requestAnimationFrame(step);
}), ms, metres, turn);

/** The hero: a drawing comes in and its plans are found; Review; the floor in 3D; walking in
 * through the front door. About 14 seconds, looping. */
scenes.hero = async () => {
  const p = await browser("maya");
  await p.R(() => localStorage.setItem("storeypath.theme", "dark"));
  await p.open(`/#/p/${stage.fresh2}`);
  await p.until(() => document.querySelector("label.drop input[type=file]"), "the project's page");
  await sleep(800);
  const rec = recorder(p, "hero"), m = {};
  await sleep(700);
  m.drop = rec.now();
  const { result } = await p.page.send("Runtime.evaluate", { expression: "document.querySelector('label.drop input[type=file]')" });
  await p.page.send("DOM.setFileInputFiles", { files: [stage.sheet], objectId: result.objectId });
  await p.until(() => document.querySelector("dialog.private-review[open]"), "the private information", 60000);
  m.privacy = rec.now();
  await sleep(1200);
  await p.R(() => document.querySelector("dialog.private-review button.primary").click());
  m.adding = rec.now();
  await p.until(() => document.querySelectorAll(".plans > *").length >= 3, "the plans found", 120000);
  m.plans = rec.now();
  await p.R(() => { document.getElementById("toast").hidden = true; });
  await sleep(1800); // (the page scrolls to them)
  m.plansShown = rec.now();
  // Review, on the demo campus
  await rec.pause();
  await review(p, `${MAIN}-F00`);
  rec.resume();
  await sleep(150);
  m.review = rec.now();
  await sleep(700);
  await p.R(() => window.storeypathReview.run("review.start"));
  await sleep(1300);
  await p.key("n");
  await sleep(1300);
  await p.key("Escape");
  await p.key("Escape");
  m.reviewEnd = rec.now();
  // the floor in 3D, turned round
  await p.key("3");
  await p.until(() => window.storeypathReview.view3d.building && !window.storeypathReview.view3d.busy, "the 3D view", 120000);
  await p.R(() => window.storeypathReview.view3d.world.ready());
  await orbit(p, { azimuth: -40, elevation: 42 });
  await sleep(300);
  m.built = rec.now();
  // (the motions taken slowly, as fast as frames come, and played SLOW times as fast)
  await turnAround(p, 3200 * SLOW, [-40, 42, 1], [10, 50, 0.82]);
  m.turned = rec.now();
  // walking in through the front door, the panels hidden
  await p.key("\\");
  await p.key("4");
  await p.until(() => window.storeypathReview.view3d.world?.mode === "walk", "walking", 20000);
  const door = await p.R(() => {
    const w = window.storeypathReview.view3d.world, floor = window.storeypathReview.state.floor.id;
    w.setMode("dollhouse", { back: true });
    w.setMode("walk", { floor }); // outside the front door, looking in
    const P = w.camera.position, d = w.camera.getWorldDirection(P.clone());
    const ahead = { x: P.x + d.x * 2, z: P.z + d.z * 2 };
    const near = w.plan(floor).doors.map((x) => ({ x, m: [(x.span[0][0] + x.span[1][0]) / 2, (x.span[0][1] + x.span[1][1]) / 2] }))
      .sort((a, b) => Math.hypot(a.m[0] - ahead.x, a.m[1] - ahead.z) - Math.hypot(b.m[0] - ahead.x, b.m[1] - ahead.z))[0];
    w.setDoorOpen(near.x.id, false, { instant: true });
    w.setDoors("manual"); // (opened with E, as the picture says)
    return near.x.id;
  });
  await walkOn(p, 50, -2.6); // a few steps back from the doors, to see them first
  await p.page.click(800, 560); // the mouse taken: the cross
  await sleep(900);
  m.walk = rec.now();
  await walkOn(p, 1500 * SLOW, 3.0);
  await sleep(300);
  m.atDoor = rec.now();
  await p.key("e"); // (the door swings in its own time: half a second)
  await sleep(700);
  m.opened = rec.now();
  await walkOn(p, 2800 * SLOW, 2.6, 1.05); // in, turning to the kiosk and the reception desk
  await sleep(500);
  m.inside = rec.now();
  const frames = await rec.stop();
  await p.R((id) => {
    const w = window.storeypathReview.view3d.world;
    w.setDoors(localStorage.getItem("storeypath.world.doors") === "manual" ? "manual" : "auto");
    w.setDoorOpen(id, true, { instant: true });
  }, door);
  const timeline = [
    ...hold(frames, m.drop, 600),
    ...clip(frames, m.drop, m.privacy, 2),
    ...hold(frames, m.privacy + 400, 800),
    ...clip(frames, m.adding, m.plans, Math.max(1, (m.plans - m.adding) / 1000)),
    ...clip(frames, m.plans, m.plansShown, 1.6),
    ...hold(frames, m.plansShown, 500),
    ...clip(frames, m.review, m.reviewEnd, 1.4),
    ...hold(frames, m.built, 300),
    ...clip(frames, m.built, m.turned, SLOW * 1.35),
    ...hold(frames, m.walk, 300),
    ...clip(frames, m.walk, m.atDoor, SLOW),
    ...clip(frames, m.atDoor, m.opened, 1),
    ...clip(frames, m.opened, m.inside, SLOW),
  ];
  console.log(`    ${frames.length} frames; ${(timeline.reduce((s, [, ms]) => s + ms, 0) / 1000).toFixed(1)} s`);
  jobs.push({ frames: merged(timeline), out: "hero.webp", width: 1200, quality: 68 });
  p.close();
};

/** Walking up to the president's door on the 3D page, opening it with E, and walking in. */
scenes.walkdoor = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { floor: `${MAIN}-F02` });
  const door = await p.R(() => {
    const w = window.storeypathWorld, floor = `${w.building}-F02`;
    const office = w.package.spaces.find((s) => s.properties.number === "205");
    const c = w.toLocal(office.properties.display_point);
    const { d, m } = w.plan(floor).doors.map((d) => ({ d, m: [(d.span[0][0] + d.span[1][0]) / 2, (d.span[0][1] + d.span[1][1]) / 2] }))
      .sort((a, b) => Math.hypot(a.m[0] - c.x, a.m[1] - c.z) - Math.hypot(b.m[0] - c.x, b.m[1] - c.z))[0];
    const [[ax, az], [bx, bz]] = d.span, len = Math.hypot(bx - ax, bz - az);
    let nx = -(bz - az) / len, nz = (bx - ax) / len;
    if ((c.x - m[0]) * nx + (c.z - m[1]) * nz > 0) { nx = -nx; nz = -nz; } // the corridor's side
    w.setDoorOpen(d.id, false, { instant: true });
    w.setDoors("manual");
    // in the corridor, 3.2 m from before the door, looking along it: walked up to it, turning to it
    const h1 = Math.atan2(nx, nz), h0 = h1 + 1.15, end = { x: m[0] + nx * 1.2, z: m[1] + nz * 1.2 };
    w.setMode("walk", { floor, at: { x: end.x + Math.sin(h0) * 3.2, z: end.z + Math.cos(h0) * 3.2 }, heading: h0 });
    return d.id;
  });
  await p.page.click(800, 520);
  await p.until(() => window.storeypathWorld.walking, "the mouse taken", 10000);
  await sleep(800);
  const rec = recorder(p, "walkdoor"), m = {};
  await sleep(400);
  m.start = rec.now();
  await walkOn(p, 1700 * SLOW, 3.2, -1.15); // along the corridor, turning to the door
  await sleep(500);
  m.atDoor = rec.now();
  await p.key("e");
  await sleep(800);
  m.opened = rec.now();
  await walkOn(p, 2300 * SLOW, 3.4);
  await sleep(600);
  m.end = rec.now();
  const frames = await rec.stop();
  await p.R((id) => window.storeypathWorld.setDoorOpen(id, true, { instant: true }), door);
  jobs.push({ frames: merged([...hold(frames, m.start, 400), ...clip(frames, m.start, m.atDoor, SLOW), ...clip(frames, m.atDoor, m.opened, 1),
    ...clip(frames, m.opened, m.end, SLOW), ...hold(frames, m.end, 600)]), out: "walk-door.webp", width: 960, quality: 70 });
  p.close();
};

// ---- finding the way: Studio's Find the way page, the way drawn in, played, flown along -------------------
//
// The viewers move the way by their own clocks (a line drawing itself in, a dot walking it, the
// camera flying along it), so these are taken in slow motion: the page's clocks and its CSS
// animations run at RATE of real time while it is recorded, and the recording is played 1/RATE
// times as fast. What is shown is what a person sees, at its own pace.

const RATE = 0.4;

/** Installed in a page before its scripts: its clocks (performance.now, the time animation frames
 * are given, timers) and its CSS animations and transitions slowed by ``window.__spRate(rate)``
 * from that moment, without a jump. (DevTools' own Animation.setPlaybackRate leaves the plan
 * blank while it plays: the page's animations are slowed one by one instead.) */
const SLOWABLE = `(() => {
  const real = performance.now.bind(performance), frame = window.requestAnimationFrame.bind(window);
  const timeout = window.setTimeout.bind(window), interval = window.setInterval.bind(window);
  let base = real(), offset = base, rate = 1;
  const scaled = (t) => offset + (t - base) * rate;
  performance.now = () => scaled(real());
  window.requestAnimationFrame = (cb) => frame((t) => cb(scaled(t)));
  window.setTimeout = (fn, ms = 0, ...a) => timeout(fn, ms / rate, ...a);
  window.setInterval = (fn, ms = 0, ...a) => interval(fn, ms / rate, ...a);
  window.__spRate = (r) => { const now = real(); offset = scaled(now); base = now; rate = r; };
  const css = () => {
    for (const a of document.getAnimations()) if (a.playbackRate !== rate) a.playbackRate = rate;
    frame(css);
  };
  frame(css);
})();`;

/** The page's clocks and CSS animations at ``rate`` of real time (1: as they are). */
const slowMotion = (p, rate) => p.R((r) => window.__spRate(r), rate);

/** The demo's way: from the kiosk in the main building's reception to an office two floors up. */
async function wayEnds(p) {
  const net = await p.R(async (path) => (await fetch(path)).json(), `/api/projects/${DEMO}/buildings/${MAIN}/navigation`);
  const kiosk = net.nodes.find((n) => n.kind === "kiosk" && n.floor_id === `${MAIN}-F00`).item_id;
  const office = net.places.find((x) => x.label === "OFFICE 213" && x.floor_id === `${MAIN}-F02`);
  const floors = Object.fromEntries(net.floors.map((f) => [f.id, f.name]));
  return { kiosk, office: office.id, floors };
}

/** Studio's Find the way page from the kiosk (to the office, unless ``to: false``), slowable. */
async function findTheWay(p, { to = true, view = "plan", theme = "dark", width = W, height = H } = {}) {
  const ends = await wayEnds(p);
  await p.page.send("Page.addScriptToEvaluateOnNewDocument", { source: SLOWABLE });
  await p.R((t, v) => {
    localStorage.setItem("storeypath.theme", t);
    localStorage.setItem("storeypath.navigate.view", v);
  }, theme, view);
  const q = new URLSearchParams({ p: DEMO, building: MAIN, from: ends.kiosk, ...(to ? { to: ends.office } : {}) });
  await p.open(`/navigate.html?${q}`, { width, height });
  await p.until(() => window.storeypathNavigate?.state.engine && window.storeypathNavigate.state.planShows, "the plan", 60000);
  if (to) await p.until(() => document.querySelector(".sp-route[data-sp-route]") && document.querySelectorAll("#steps .step").length, "the way", 30000);
  if (view !== "plan") {
    await p.until(() => window.storeypathNavigate.state.worldShows && window.storeypathNavigate.state.world.route, "the way in 3D", 120000);
    await p.R(() => window.storeypathNavigate.state.world.ready());
  }
  return ends;
}

/** Where an element is on the page (CSS pixels), as a screenshot's clip. */
const boxOf = (p, selector) => p.R((s) => {
  const r = document.querySelector(s).getBoundingClientRect();
  return { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
}, selector);

/** The way played on the plan, as a person finds it: the office typed and chosen, the way drawing
 * itself in, then Play: a dot walks it, up the stairs to Floor 2 and on to the office. With
 * ``clip``, the plan alone (for the viewer's README). */
async function playTheWay(p, name, { area = null } = {}) {
  const rec = recorder(p, name, { clip: area }), m = {};
  await slowMotion(p, RATE);
  await sleep(500);
  m.start = rec.now();
  const to = await boxOf(p, "#to-input");
  await p.page.click(to.x + to.width / 2, to.y + to.height / 2);
  await sleep(500);
  for (const ch of "office 213") {
    await p.page.send("Input.insertText", { text: ch });
    await sleep(240);
  }
  await sleep(900);
  m.chosen = rec.now();
  await p.key("Enter");
  await p.until(() => document.querySelector(".sp-route[data-sp-route]"), "the way", 20000);
  await p.R(() => document.activeElement?.blur());
  await sleep(2200 / RATE); // drawn in (a second), then flowing
  await p.R(() => document.getElementById("play").click());
  await p.until(() => window.storeypathNavigate.state.playing === "ended", "played to the end", 120000);
  await sleep(1100 / RATE);
  m.end = rec.now();
  const frames = await rec.stop();
  await slowMotion(p, 1);
  return { frames, m };
}

/** Find the way, on the plan: typed, drawn in, played across two floors. */
scenes.route = async () => {
  const p = await browser("maya");
  await findTheWay(p, { to: false });
  await sleep(1500);
  const { frames, m } = await playTheWay(p, "route");
  jobs.push({ frames: merged([...hold(frames, m.start, 200), ...clip(frames, m.start, m.end, 1 / RATE), ...hold(frames, m.end, 900)]),
    out: "route.webp", width: 1200, quality: 70 });
  p.close();
};

/** Find the way in 3D: the way glowing through the building, then Fly along, up the stairs to the office. */
scenes["route-fly"] = async () => {
  const p = await browser("maya");
  await findTheWay(p, { view: "3d" });
  await sleep(2500);
  const rec = recorder(p, "route-fly", { clip: await boxOf(p, "#world-pane") }), m = {};
  await slowMotion(p, RATE);
  await sleep(1400 / RATE);
  m.start = rec.now();
  await p.R(() => document.getElementById("fly").click());
  await p.until(() => window.storeypathNavigate.state.world.routePlay === null && !document.getElementById("fly").disabled, "flown", 180000);
  await sleep(800 / RATE);
  m.end = rec.now();
  const frames = await rec.stop();
  await slowMotion(p, 1);
  jobs.push({ frames: merged([...hold(frames, m.start, 1000), ...clip(frames, m.start, m.end, 1 / RATE), ...hold(frames, m.end, 500)]),
    out: "route-fly.webp", width: 720, quality: 55 });
  p.close();
};

// ---- the viewer's own README: the world and the plan alone, no Studio round them -----------------------
//
// Taken only when asked (`viewer`, or a scene's name), into the viewer's checkout
// (storeypath-viewer/docs/images, or --viewer-out): its pictures are its own repository's.

const inViewer = (name) => join(VIEWER_OUT, name);

/** Studio's 3D page presenting (nothing but the world), still. */
async function bare(p) {
  await p.R(() => {
    window.storeypathWorldPage.setPresenting(true);
    window.storeypathWorldPage.setTurning(false);
    document.getElementById("present-hint").hidden = true;
  });
  await sleep(1200);
}

scenes["viewer-world"] = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { explode: "5", cutaway: "1" });
  await bare(p);
  await orbit(p, { azimuth: -36, elevation: 33 });
  await sleep(2500);
  jobs.push({ in: await p.shot("viewer-world"), out: inViewer("world.webp"), quality: 90 });
  p.close();
};

scenes["viewer-xray"] = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { xray: "1", floor: `${MAIN}-F01` });
  await bare(p);
  const { r } = await orbit(p, { azimuth: 28, elevation: 40 });
  await orbit(p, { azimuth: 28, elevation: 40, distance: r * 0.8 });
  await sleep(2500);
  jobs.push({ in: await p.shot("viewer-xray"), out: inViewer("xray.webp"), quality: 90 });
  p.close();
};

scenes["viewer-looks"] = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { floor: `${MAIN}-F02`, cutaway: "1" });
  await p.R(() => {
    const w = window.storeypathWorld;
    w.select(w.package.spaces.find((s) => s.properties.name === "CORRIDOR" && s.properties.floor_id.endsWith("-F02")).id);
  });
  await sleep(1500);
  await p.R(() => window.storeypathWorld.select(null, { go: false }));
  await bare(p);
  await p.R(() => window.storeypathWorld.setLabels(false));
  await orbit(p, { azimuth: 6, elevation: 56, distance: 44 });
  await sleep(2500);
  const real = await p.shot("viewer-real");
  await p.R(() => window.storeypathWorld.setStyle("model"));
  await p.R(() => window.storeypathWorld.ready());
  await sleep(2500);
  const model = await p.shot("viewer-model");
  await p.R(() => window.storeypathWorld.setStyle("real"));
  jobs.push({ split: [real, model], out: inViewer("looks.webp"), quality: 90 });
  p.close();
};

/** The 2D plan (viewer/svg's FloorPlanEngine, its example page): a floor, its items and doors. */
scenes["viewer-plan"] = async () => {
  const p = await browser("maya", { gl: false });
  await p.page.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
  const pkg = `/api/projects/${DEMO}/preview.storeypath?building=${MAIN}`;
  await p.open(`/viewer/svg/example/index.html?package=${encodeURIComponent(pkg)}`, { width: 1600, height: 760 });
  await p.until(() => document.querySelector("#floor option"), "the plan", 60000);
  await p.R((floor) => {
    document.querySelector("header").style.display = "none"; // the plan alone, the whole page
    document.body.style.gridTemplateRows = "minmax(0, 1fr)";
    const s = document.getElementById("floor");
    s.value = floor;
    s.dispatchEvent(new Event("change"));
    window.dispatchEvent(new Event("resize"));
  }, `${MAIN}-F01`);
  await sleep(800);
  await p.R(() => document.getElementById("fit").click());
  await sleep(1200);
  jobs.push({ in: await p.shot("viewer-plan"), out: inViewer("plan.webp"), lossless: true });
  p.close();
};

/** Walking through a door, the world alone: the cross, its hint, the door swinging open. */
scenes["viewer-walk"] = async () => {
  const p = await browser("maya");
  await world(p, MAIN, { floor: `${MAIN}-F02` });
  await bare(p);
  const door = await p.R(() => {
    const w = window.storeypathWorld, floor = `${w.building}-F02`;
    const office = w.package.spaces.find((s) => s.properties.number === "205");
    const c = w.toLocal(office.properties.display_point);
    const { d, m } = w.plan(floor).doors.map((d) => ({ d, m: [(d.span[0][0] + d.span[1][0]) / 2, (d.span[0][1] + d.span[1][1]) / 2] }))
      .sort((a, b) => Math.hypot(a.m[0] - c.x, a.m[1] - c.z) - Math.hypot(b.m[0] - c.x, b.m[1] - c.z))[0];
    const [[ax, az], [bx, bz]] = d.span, len = Math.hypot(bx - ax, bz - az);
    let nx = -(bz - az) / len, nz = (bx - ax) / len;
    if ((c.x - m[0]) * nx + (c.z - m[1]) * nz > 0) { nx = -nx; nz = -nz; }
    w.setDoorOpen(d.id, false, { instant: true });
    w.setDoors("manual");
    const h1 = Math.atan2(nx, nz), h0 = h1 + 1.15, end = { x: m[0] + nx * 1.2, z: m[1] + nz * 1.2 };
    w.setMode("walk", { floor, at: { x: end.x + Math.sin(h0) * 3.2, z: end.z + Math.cos(h0) * 3.2 }, heading: h0 });
    return d.id;
  });
  await p.page.click(800, 520);
  await p.until(() => window.storeypathWorld.walking, "the mouse taken", 10000);
  await p.R(() => { document.getElementById("minimap").hidden = true; });
  await sleep(800);
  const rec = recorder(p, "viewer-walk"), m = {};
  await sleep(400);
  m.start = rec.now();
  await walkOn(p, 1700 * SLOW, 3.2, -1.15);
  await sleep(500);
  m.atDoor = rec.now();
  await p.key("e");
  await sleep(800);
  m.opened = rec.now();
  await walkOn(p, 2300 * SLOW, 3.4);
  await sleep(600);
  m.end = rec.now();
  const frames = await rec.stop();
  await p.R((id) => window.storeypathWorld.setDoorOpen(id, true, { instant: true }), door);
  jobs.push({ frames: merged([...hold(frames, m.start, 400), ...clip(frames, m.start, m.atDoor, SLOW), ...clip(frames, m.atDoor, m.opened, 1),
    ...clip(frames, m.opened, m.end, SLOW), ...hold(frames, m.end, 600)]), out: inViewer("walk.webp"), width: 960, quality: 70 });
  p.close();
};

/** The way on the plan alone, in the light: drawn in, then played up the stairs to the office. */
scenes["viewer-route"] = async () => {
  const p = await browser("maya", { gl: false });
  await findTheWay(p, { to: false, theme: "light", width: 1500, height: 940 });
  await sleep(1500);
  const { frames, m } = await playTheWay(p, "viewer-route", { area: await boxOf(p, "#plan-pane") });
  jobs.push({ frames: merged([...hold(frames, m.chosen + 300, 300), ...clip(frames, m.chosen + 300, m.end, 1 / RATE), ...hold(frames, m.end, 900)]),
    out: inViewer("route.webp"), width: 900, quality: 72 });
  p.close();
};

/** The way in the 3D world alone: its glowing ribbon over each floor, the stairs' column between, its marks. */
scenes["viewer-route3d"] = async () => {
  const p = await browser("maya");
  const { kiosk, office, floors } = await wayEnds(p);
  await world(p, MAIN);
  await bare(p);
  const { route } = await p.R(async (path) => (await fetch(path)).json(),
    `/api/projects/${DEMO}/buildings/${MAIN}/navigation?${new URLSearchParams({ from: kiosk, to: office })}`);
  await p.R(async (route, floors) => {
    const w = window.storeypathWorld;
    w.setFloor(null);
    await w.showRoute(route, { fit: true, animate: false, startLabel: "You are here", floorName: (id) => floors[id] ?? id });
  }, route, floors);
  await sleep(2500);
  await orbit(p, { azimuth: -30, elevation: 27, closer: 0.76 }); // low and close: the way across the building
  await sleep(2500);
  jobs.push({ in: await p.shot("viewer-route3d"), out: inViewer("route-3d.webp"), quality: 90 });
  p.close();
};

// ---- run ---------------------------------------------------------------------------------------------

const asked = (s) => (s.startsWith("viewer") ? wanted.has(s) || wanted.has("viewer") : !wanted.size || wanted.has(s));
const order = Object.keys(scenes).filter(asked);
let failed = false;
for (const name of order) {
  console.log(name);
  try {
    await scenes[name]();
  } catch (e) {
    console.error(`${name}: ${e.stack || e.message}`);
    failed = true;
  }
}
writeFileSync(join(rawDir, "jobs.json"), JSON.stringify(jobs, null, 1));
const encoded = spawnSync(PYTHON, [join(HERE, "encode.py"), join(rawDir, "jobs.json"), OUT], { stdio: "inherit" });
stopStage();
if (!keepRaw) rmSync(rawDir, { recursive: true, force: true });
process.exit(failed || encoded.status ? 1 : 0);
