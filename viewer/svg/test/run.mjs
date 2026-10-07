// node test/run.mjs: the frame and the package reader in Node, then the engine in
// headless Chrome, used as people use it: clicks, drags, the wheel, two fingers,
// the keyboard. Positions are checked to a pixel: nothing may drift.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { launch, readPackage, serve } from "./harness.mjs";
import { LocalFrame } from "../dist/frame.js";
import { floorFromPackage } from "../dist/package.js";
import { inside } from "../dist/geometry.js";
import { readPackage as readInBrowsers } from "../dist/read.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const conformance = join(root, "../../spec/conformance");
const FLOOR = "EWBSSN-DEMO-HQ-F01";
const OFFICE = "EWBSSN-DEMO-HQ-F01-0069"; // office 112
const HALL = "EWBSSN-DEMO-HQ-F01-0181"; // divided into two zones
const CORRIDOR_LIKE = "EWBSSN-DEMO-HQ-F01-0066"; // the lift lobby

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const near = (a, b, tolerance, what) => {
  const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
  if (!(d <= tolerance)) throw new Error(`${what}: ${JSON.stringify(a)} vs ${JSON.stringify(b)}, ${d.toFixed(3)} apart`);
};
const equal = (a, b, what) => {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${what}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
};
const truly = (v, what) => {
  if (!v) throw new Error(what);
};

// ---- in Node ------------------------------------------------------------------

const pkg = readPackage(join(conformance, "packages/campus.storeypath"));

test("the local frame agrees with Studio's to a millimetre", () => {
  const { tolerance_m: tolerance, vectors } = JSON.parse(readFileSync(join(conformance, "localframe.json"), "utf8"));
  for (const v of vectors) {
    const frame = new LocalFrame(v.placement);
    near(frame.toLocal(v.lonlat), v.local, tolerance, "to local");
    const [lon, lat] = frame.toLonLat(v.local);
    const metres = [(lon - v.lonlat[0]) * 111320 * Math.cos((lat * Math.PI) / 180), (lat - v.lonlat[1]) * 110574];
    near(metres, [0, 0], tolerance, "to longitude and latitude");
  }
});

test("a floor of a package: zones for a divided space, the space itself otherwise", () => {
  const plan = floorFromPackage(pkg, FLOOR);
  equal(plan.spaces.length, 25, "units");
  const zones = plan.spaces.filter((s) => s.kind === "zone");
  equal(zones.map((z) => z.container), [HALL, HALL], "zones of the hall");
  truly(!plan.spaces.some((s) => s.id === HALL), "the divided hall is not itself a unit");
  equal(plan.drawing.containers.map((c) => c.id), [HALL], "containers");
  equal(plan.drawing.openings.length, 38, "openings");
  equal(plan.drawing.openings.filter((o) => o.swings).length, 24, "doors with their swings");
  for (const s of plan.spaces) truly(inside(s.polygons, s.marker), `${s.id}: its label point is inside it`);
  truly(plan.drawing.walls.length > 0 && plan.drawing.outline.length > 0, "walls and outline");
});

test("hidden spaces are left out unless asked for; ignored ones always", () => {
  const changed = structuredClone(pkg);
  const office = changed.spaces.find((s) => s.id === OFFICE);
  office.properties.hidden = true;
  truly(!floorFromPackage(changed, FLOOR).spaces.some((s) => s.id === OFFICE), "hidden: left out");
  truly(floorFromPackage(changed, FLOOR, { showHidden: true }).spaces.some((s) => s.id === OFFICE), "hidden: asked for");
  office.properties.ignored = true;
  truly(!floorFromPackage(changed, FLOOR, { showHidden: true }).spaces.some((s) => s.id === OFFICE), "ignored");
});

test("readPackage reads a package file as the test's own ZIP reader does", async () => {
  const path = join(conformance, "packages/campus.storeypath");
  const read = await readInBrowsers(readFileSync(path));
  equal(read, pkg, "the same package");
  let said = "";
  try {
    await readInBrowsers(new TextEncoder().encode("not a zip at all, but long enough to look for a directory"));
  } catch (e) {
    said = e.message;
  }
  truly(/not a StoreyPath package/.test(said), `a file that is not a package: ${said}`);
});

test("an unplaced building reads in its own metres too", () => {
  const unplaced = readPackage(join(conformance, "packages/unplaced.storeypath"));
  const floor = unplaced.floors[0];
  const plan = floorFromPackage(unplaced, floor.id);
  truly(plan.spaces.length > 0, "spaces");
  for (const s of plan.spaces) truly(inside(s.polygons, s.marker), `${s.id}: its label point is inside it`);
});

// ---- in Chrome ----------------------------------------------------------------

const browser = [];
const inChrome = (name, fn) => browser.push({ name, fn });

inChrome("draws the floor: units, zones, containers, walls, doors and windows", async (page) => {
  await page.run(() => window.fresh());
  const got = await page.run(() => ({
    units: document.querySelectorAll(".sp-units [data-sp-id]").length,
    zones: document.querySelectorAll(".sp-units .sp-zone").length,
    containers: document.querySelectorAll(".sp-containers path").length,
    walls: document.querySelectorAll(".sp-walls .sp-wall").length,
    swings: document.querySelectorAll(".sp-openings .sp-swing").length,
    windows: document.querySelectorAll(".sp-openings .sp-window").length,
    buttons: document.querySelectorAll('.sp-units [role="button"][tabindex="0"]').length,
  }));
  equal(got, { units: 25, zones: 2, containers: 1, walls: 1, swings: 24, windows: 14, buttons: 25 }, "drawn");
});

inChrome("fits the floor in its box, inside the padding", async (page) => {
  await page.run(() => window.fresh({ padding: 24 }));
  const r = await page.run(() => {
    const plan = document.querySelector(".sp-outline path").getBoundingClientRect();
    const box = window.engine.svg.getBoundingClientRect();
    return [plan.left - box.left, plan.top - box.top, box.right - plan.right, box.bottom - plan.bottom];
  });
  for (const gap of r) truly(gap >= 23, `outline inside the padding: ${r}`);
  truly(Math.abs(r[0] - r[2]) <= 1 && Math.abs(r[1] - r[3]) <= 1, `centred: ${r}`);
  truly(Math.min(r[0], r[1]) <= 25, `as large as fits: ${r}`);
});

inChrome("a click chooses a space; a click on nothing chooses none", async (page) => {
  await page.run(() => window.fresh());
  const at = await page.run((id) => window.onPage(window.engine.markerOf(id)), OFFICE);
  await page.click(...at);
  equal(await page.run(() => window.chosen), [OFFICE], "chosen");
  equal(await page.run((id) => document.querySelector(`[data-sp-id="${id}"]`).dataset.selected, OFFICE), "true", "marked");
  equal(await page.run(() => document.querySelectorAll(".sp-selection path").length), 1, "its outline drawn over");
  await page.click(60, 40); // the plan's corner, off the floor
  equal(await page.run(() => [window.chosen, window.engine.selected]), [[OFFICE, null], null], "none");
});

inChrome("a drag pans exactly, and is not a click", async (page) => {
  await page.run(() => window.fresh());
  const before = await page.run((id) => window.onPage(window.engine.markerOf(id)), OFFICE);
  const from = await page.run((id) => window.onPage(window.engine.markerOf(id)), CORRIDOR_LIKE);
  await page.drag(from, [from[0] + 137, from[1] - 59]);
  const after = await page.run((id) => window.onPage(window.engine.markerOf(id)), OFFICE);
  near(after, [before[0] + 137, before[1] - 59], 1, "moved with the pointer");
  equal(await page.run(() => window.chosen), [], "nothing chosen");
});

inChrome("the wheel zooms about the pointer", async (page) => {
  await page.run(() => window.fresh());
  const at = [380, 290];
  const plan = await page.run((p) => {
    const r = window.engine.svg.getBoundingClientRect();
    return window.engine.toPlan([p[0] - r.left, p[1] - r.top]);
  }, at);
  const k = await page.run(() => window.engine.camera().k);
  await page.wheel(at[0], at[1], -240);
  await page.run(() => window.frames());
  truly((await page.run(() => window.engine.camera().k)) > k * 1.3, "zoomed in");
  near(await page.run((p) => window.onPage(p), plan), at, 1, "the point under the pointer stays");
  await page.wheel(at[0], at[1], 240);
  await page.run(() => window.frames());
  near(await page.run((p) => window.onPage(p), plan), at, 1, "and back out");
});

inChrome("two fingers zoom about the point between them, and pan with it", async (page) => {
  await page.run(() => window.fresh());
  const mid = [450, 330];
  const plan = await page.run((p) => {
    const r = window.engine.svg.getBoundingClientRect();
    return window.engine.toPlan([p[0] - r.left, p[1] - r.top]);
  }, mid);
  const k = await page.run(() => window.engine.camera().k);
  await page.touch("touchStart", [[mid[0] - 50, mid[1]], [mid[0] + 50, mid[1]]]);
  for (let i = 1; i <= 6; i++) {
    const s = 50 + i * 25, shift = i * 10;
    await page.touch("touchMove", [[mid[0] - s + shift, mid[1]], [mid[0] + s + shift, mid[1]]]);
  }
  await page.touch("touchEnd", []);
  await page.run(() => window.frames());
  const k2 = await page.run(() => window.engine.camera().k);
  truly(Math.abs(k2 / k - 4) < 0.01, `zoomed by the fingers' spread: ${k2 / k}`);
  near(await page.run((p) => window.onPage(p), plan), [mid[0] + 60, mid[1]], 1, "the point between them follows them");
  equal(await page.run(() => window.chosen), [], "nothing chosen");
});

inChrome("focus brings a space to the middle without zooming; or zooms to it", async (page) => {
  await page.run(() => window.fresh());
  await page.run(() => window.engine.zoomBy(3, [100, 100]));
  const k = await page.run(() => window.engine.camera().k);
  await page.run((id) => window.engine.focus(id, { animate: false }), OFFICE);
  equal(await page.run(() => window.engine.camera().k), k, "same zoom");
  near(await page.run((id) => window.engine.toScreen(window.engine.markerOf(id)), OFFICE), [400, 300], 1, "in the middle");
  await page.run(() => window.engine.focus("NOT-THERE", { animate: false })); // nothing happens
  await page.run((id) => window.engine.select(id, { focus: "zoom" }), "EWBSSN-DEMO-HQ-F01-0063");
  const gaps = await page.run(() => {
    const zone = document.querySelector('[data-sp-id="EWBSSN-DEMO-HQ-F01-0063"]').getBoundingClientRect();
    const box = window.engine.svg.getBoundingClientRect();
    return [zone.left - box.left, zone.top - box.top, box.right - zone.right, box.bottom - zone.bottom];
  });
  truly(gaps.every((g) => g >= 23) && Math.min(gaps[0] + gaps[2], gaps[1] + gaps[3]) <= 50, `the zone fills the view: ${gaps}`);
});

inChrome("animated moves end where they were sent", async (page) => {
  await page.run(() => window.fresh({ motion: true }));
  await page.run((id) => window.engine.focus(id), OFFICE);
  await page.run(() => new Promise((r) => setTimeout(r, 450)));
  near(await page.run((id) => window.engine.toScreen(window.engine.markerOf(id)), OFFICE), [400, 300], 1, "in the middle");
});

inChrome("no animation when the system asks for reduced motion", async (page) => {
  await page.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  try {
    await page.run(() => window.fresh({ motion: undefined }));
    await page.run((id) => window.engine.focus(id), OFFICE);
    near(await page.run((id) => window.engine.toScreen(window.engine.markerOf(id)), OFFICE), [400, 300], 1, "there at once");
  } finally {
    await page.send("Emulation.setEmulatedMedia", { features: [] });
  }
});

inChrome("resizing keeps the middle of the view still", async (page) => {
  await page.run(() => window.fresh());
  await page.run(() => window.engine.zoomBy(2.5, [300, 200]));
  const middle = await page.run(() => window.engine.toPlan([400, 300]));
  const k = await page.run(() => window.engine.camera().k);
  await page.run(() => { document.getElementById("plan").style.width = "560px"; document.getElementById("plan").style.height = "420px"; });
  await page.run(() => window.frames(3));
  near(await page.run((p) => window.engine.toScreen(p), middle), [280, 210], 1, "the same point in the middle");
  equal(await page.run(() => window.engine.camera().k), k, "same zoom");
  await page.run(() => { document.getElementById("plan").style.width = "800px"; document.getElementById("plan").style.height = "600px"; });
});

inChrome("zooming in and out many times does not drift", async (page) => {
  await page.run(() => window.fresh());
  const before = await page.run(() => window.engine.camera());
  await page.run(() => { for (let i = 0; i < 40; i++) { window.engine.zoomBy(1.7, [123.4, 456.7]); window.engine.zoomBy(1 / 1.7, [123.4, 456.7]); } });
  const after = await page.run(() => window.engine.camera());
  near([after.tx, after.ty], [before.tx, before.ty], 1, "camera");
  truly(Math.abs(after.k / before.k - 1) < 1e-6, "scale");
});

inChrome("highlight brings some spaces out and dims the rest", async (page) => {
  await page.run(() => window.fresh());
  await page.run((ids) => window.engine.highlight(ids), [OFFICE, CORRIDOR_LIKE]);
  const got = await page.run(() => ({
    lit: document.querySelectorAll(".sp-highlight").length, dim: document.querySelectorAll(".sp-dim").length,
  }));
  equal(got, { lit: 2, dim: 23 }, "classes");
  await page.run(() => window.engine.highlight(null));
  equal(await page.run(() => document.querySelectorAll(".sp-highlight, .sp-dim").length), 0, "cleared");
});

inChrome("the pin stands on a space's label point, and follows the plan", async (page) => {
  await page.run(() => window.fresh());
  await page.run((id) => window.engine.setPin(id), OFFICE);
  const pin = async () => page.run(() => {
    const p = document.querySelector("[data-sp-pin]");
    const m = p.getAttribute("transform").match(/translate\(([-\d.]+),([-\d.]+)\)/);
    return { id: p.dataset.spPin, at: [Number(m[1]), Number(m[2])] };
  });
  const first = await pin();
  equal(first.id, OFFICE, "its space");
  near(first.at, await page.run((id) => window.engine.toScreen(window.engine.markerOf(id)), OFFICE), 1, "on the label point");
  await page.drag([500, 500], [430, 520]);
  near((await pin()).at, [first.at[0] - 70, first.at[1] + 20], 1, "after a pan");
  await page.run(() => window.engine.setPin(null));
  equal(await page.run(() => document.querySelectorAll("[data-sp-pin]").length), 0, "taken away");
});

inChrome("labels: any language, upright, shown only where they fit", async (page) => {
  await page.run((office) => {
    window.labelled = office;
    return window.fresh({ label: (s) => (s.id === office ? ["مكتب المدير", "112"] : s.name ?? null) });
  }, OFFICE);
  const label = await page.run(() => {
    const texts = [...document.querySelectorAll(".sp-labels text")];
    const t = texts.find((x) => x.textContent.includes("مكتب"));
    return { lines: [...t.querySelectorAll("tspan")].map((x) => x.textContent), bidi: getComputedStyle(t).unicodeBidi,
      transform: t.getAttribute("transform"), count: texts.length };
  });
  equal(label.lines, ["مكتب المدير", "112"], "its lines");
  equal(label.bidi, "plaintext", "each line in its own direction");
  truly(!/scale|matrix|rotate/.test(label.transform), "not scaled or turned with the plan");
  await page.run(() => window.engine.zoomBy(0.26));
  const shown = await page.run(() => [...document.querySelectorAll(".sp-labels text")].filter((t) => t.getAttribute("visibility") === "visible").length);
  truly(shown < label.count, `small rooms lose their labels when zoomed out (${shown} of ${label.count})`);
});

inChrome("the keyboard: Tab to a space, Enter chooses it and keeps the focus", async (page) => {
  await page.run(() => window.fresh());
  await page.key("Tab", "Tab", 9); // the plan
  await page.key("Tab", "Tab", 9); // its first space
  const focused = await page.run(() => document.activeElement.getAttribute("data-sp-id"));
  truly(focused, "a space has the focus");
  await page.key("Enter", "Enter", 13);
  equal(await page.run(() => window.chosen), [focused], "chosen");
  equal(await page.run(() => document.activeElement.getAttribute("data-sp-id")), focused, "still focused");
  equal(await page.run(() => getComputedStyle(document.activeElement).outlineStyle), "none", "no browser focus ring on a shape");
  const k = await page.run(() => window.engine.camera().k);
  await page.key("+", "Equal", 187);
  truly((await page.run(() => window.engine.camera().k)) > k, "+ zooms in");
});

inChrome("spaces the host does not offer cannot be chosen", async (page) => {
  await page.run(() => window.fresh({ interactive: (s) => s.type === "office" }));
  const at = await page.run((id) => window.onPage(window.engine.markerOf(id)), CORRIDOR_LIKE);
  await page.click(...at);
  equal(await page.run(() => window.chosen), [], "nothing chosen");
  const lobby = await page.run((id) => document.querySelector(`[data-sp-id="${id}"]`).hasAttribute("tabindex"), CORRIDOR_LIKE);
  equal(lobby, false, "not in the tab order");
});

inChrome("the host styles spaces, and restyles them when its data changes", async (page) => {
  await page.run((office) => {
    window.taken = new Set();
    return window.fresh({ styleOf: (s) => (window.taken.has(s.id) ? { className: "taken", fill: "rgb(200, 0, 0)" } : null) });
  }, OFFICE);
  const fill = () => page.run((id) => getComputedStyle(document.querySelector(`[data-sp-id="${id}"]`)).fill, OFFICE);
  truly((await fill()) !== "rgb(200, 0, 0)", "free");
  await page.run((id) => { window.taken.add(id); window.engine.restyle(); }, OFFICE);
  equal(await fill(), "rgb(200, 0, 0)", "taken");
  equal(await page.run((id) => document.querySelector(`[data-sp-id="${id}"]`).classList.contains("taken"), OFFICE), true, "its class");
});

inChrome("inline CSS from the host wins over the type colour, and goes when it does", async (page) => {
  await page.run(() => {
    window.styled = true;
    return window.fresh({ styleOf: () => (window.styled ? { style: "fill: rgb(1, 2, 3); stroke-dasharray: 5 4" } : null) });
  });
  const look = () => page.run((id) => {
    const s = getComputedStyle(document.querySelector(`[data-sp-id="${id}"]`));
    return [s.fill, s.strokeDasharray];
  }, OFFICE);
  equal(await look(), ["rgb(1, 2, 3)", "5px, 4px"], "styled");
  await page.run(() => { window.styled = false; window.engine.restyle(); });
  truly((await look())[0] !== "rgb(1, 2, 3)", "back to its type colour");
});

inChrome("y up (a drawing) and y down (a page) both stand the right way up", async (page) => {
  const square = (y) => [[[0, y], [4, y], [4, y + 4], [0, y + 4], [0, y]]];
  for (const yDown of [false, true]) {
    await page.run((plan) => window.fresh({}, plan), {
      yDown, spaces: [{ id: "LOW", polygons: [square(0)] }, { id: "HIGH", polygons: [square(10)] }] });
    const [low, high] = await page.run(() => ["LOW", "HIGH"].map((id) => window.engine.toScreen(window.engine.markerOf(id))[1]));
    truly(yDown ? high > low : high < low, `yDown ${yDown}: y=10 is ${high > low ? "below" : "above"} y=0 on the screen`);
  }
});

inChrome("an L-shaped room's label point is inside it, not in its corner", async (page) => {
  const L = [[[0, 0], [10, 0], [10, 2], [2, 2], [2, 10], [0, 10], [0, 0]]];
  await page.run((plan) => window.fresh({}, plan), { spaces: [{ id: "L", polygons: [L] }] });
  const m = await page.run(() => window.engine.markerOf("L"));
  truly(inside([L], m), `inside: ${m}`);
});

inChrome("a new floor keeps the view when asked to", async (page) => {
  await page.run(() => window.fresh());
  await page.run(() => window.engine.zoomBy(2, [200, 200]));
  const cam = await page.run(() => window.engine.camera());
  await page.run(() => window.engine.setFloor(window.sp.floorFromPackage(window.pkg, "EWBSSN-DEMO-HQ-F02"), { fit: false }));
  equal(await page.run(() => window.engine.camera()), cam, "same view");
  equal(await page.run(() => document.querySelector("svg").dataset.cam.split(",").length), 3, "data-cam");
  truly((await page.run(() => window.cameras)) > 0, "camerachange events");
});

// ---- run ----------------------------------------------------------------------

let failed = 0;
const report = (name, error) => {
  if (error) failed++;
  console.log(`${error ? "FAIL" : "ok  "} ${name}${error ? `\n     ${error.message}` : ""}`);
};
for (const t of tests) {
  try {
    await t.fn();
    report(t.name);
  } catch (e) {
    report(t.name, e);
  }
}
const server = await serve(root, { "/fixture/campus.json": JSON.stringify(pkg) });
const page = await launch();
try {
  await page.open(`${server.url}/test/plan.html`);
  await page.run(() => window.ready);
  for (const t of browser) {
    const errors = page.errors.length;
    try {
      await t.fn(page);
      if (page.errors.length > errors) throw new Error(`errors in the page: ${page.errors.slice(errors).join("; ")}`);
      report(t.name);
    } catch (e) {
      report(t.name, e);
    }
  }
} finally {
  page.close();
  server.close();
}
console.log(failed ? `${failed} failed` : `all ${tests.length + browser.length} passed`);
process.exit(failed ? 1 : 0);
