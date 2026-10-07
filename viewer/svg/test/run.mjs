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

test("a space with no name or number is labelled with what its drawing writes in it", () => {
  const changed = structuredClone(pkg);
  const office = changed.spaces.find((s) => s.id === OFFICE);
  Object.assign(office.properties, { name: null, number: null, drawing_label: "RM-GF-33" });
  equal(floorFromPackage(changed, FLOOR).spaces.find((s) => s.id === OFFICE).label, "RM-GF-33", "its drawing label");
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

test("a package with its floors pre-built in 3D (0.5, world/) reads as one without", async () => {
  const read = await readInBrowsers(readFileSync(join(conformance, "packages/campus-world.storeypath")));
  equal(floorFromPackage(read, FLOOR), floorFromPackage(pkg, FLOOR), "the same floor");
});

test("a floor's items: where each stands, the way it faces in the drawing, its type's colour", () => {
  const plan = floorFromPackage(pkg, "EWBSSN-DEMO-HQ-F00");
  equal(plan.items.length, pkg.items.filter((i) => i.properties.floor_id === "EWBSSN-DEMO-HQ-F00").length, "items");
  equal([...new Set(plan.items.map((i) => i.type))].sort(),
    ["ACCESS-POINT", "BED-KING", "COPIER", "DESK-DIRECTOR", "DESK-JUNIOR", "DESK-MANAGER", "DESK-SENIOR", "SOFA", "TV"], "types");
  const of = (type) => plan.items.find((i) => i.type === type);
  // placed in Studio facing the drawing's -y (the director's) and +x (against a west
  // wall): the building is turned 20° on the map, the drawing is not
  near(of("DESK-DIRECTOR").front, [0, -1], 1e-6, "the director's desk faces -y");
  near(of("DESK-SENIOR").front, [1, 0], 1e-6, "the senior's faces +x");
  equal([of("DESK-DIRECTOR").color, of("ACCESS-POINT").color, of("ACCESS-POINT").mount], ["#8a6238", "#1f9d8b", "ceiling"], "colours");
  equal([of("COPIER").width, of("COPIER").depth], [1.2, 0.7], "its size");
  for (const it of plan.items) {
    const f = pkg.items.find((i) => i.id === it.id).properties;
    const room = plan.spaces.find((s) => s.id === (f.zone_id ?? f.space_id));
    truly(room && inside(room.polygons, it.at), `${it.id} stands in ${f.space_id}`);
  }
  const annex = floorFromPackage(pkg, "EWBSSN-DEMO-ANNEX-F00").items; // turned 110°
  equal(annex.map((i) => i.type), ["DESK-PRESIDENT"], "the Annex's");
  near(annex[0].front, [0, -1], 1e-6, "faces the drawing's -y there too");
  const older = readPackage(join(conformance, "packages/unplaced.storeypath"));
  equal(floorFromPackage(older, older.floors[0].id).items, [], "an older package has none");
});

// format 0.7: a package a building, each item placed in its building (`local`);
// campus-hq-2 is the same building after it was moved on the map (shifted, turned 15°)
const HQ = "BYMBMX-DEMO-HQ";
const hq = readPackage(join(conformance, "packages/campus-hq.storeypath"));
const moved = readPackage(join(conformance, "packages/campus-hq-2.storeypath"));

test("a 0.7 package, one building: each item stands where `local` puts it in the building's own frame", async () => {
  equal(hq.manifest.scope.buildings, [HQ], "its one building");
  equal(await readInBrowsers(readFileSync(join(conformance, "packages/campus-hq.storeypath"))), hq, "read in browsers too");
  const frame = new LocalFrame(hq.manifest.placements[HQ]);
  let placed = 0;
  for (const floor of hq.floors) {
    const plan = floorFromPackage(hq, floor.id);
    for (const it of plan.items) {
      const p = hq.items.find((i) => i.id === it.id).properties;
      const r = (p.local.rotation_deg * Math.PI) / 180;
      near(it.at, [p.local.x_m, p.local.y_m], 1e-6, `${it.id} at its local point`);
      near(it.front, [Math.sin(r), -Math.cos(r)], 1e-9, `${it.id} turned as its local says`);
      near(it.at, frame.toLocal(p.display_point), 0.01, `${it.id}: its point on the map, rounded, is there too`);
      const room = plan.spaces.find((s) => s.id === (p.zone_id ?? p.space_id));
      truly(room && inside(room.polygons, it.at), `${it.id} stands in ${p.space_id}`);
      placed++;
    }
  }
  equal(placed, hq.items.length, "every item");
  const of = (type) => floorFromPackage(hq, "BYMBMX-DEMO-HQ-F00").items.find((i) => i.type === type);
  near(of("DESK-DIRECTOR").front, [0, -1], 1e-9, "the director's desk faces -y (rotation 0)");
  near(of("DESK-SENIOR").front, [1, 0], 1e-9, "the senior's faces +x (rotation 90)");
});

test("a building moved on the map: its items stand where they stood in its plan, facing the same way", () => {
  const was = hq.manifest.placements[HQ], is = moved.manifest.placements[HQ];
  truly(is.bearing - was.bearing === 15 && is.lon !== was.lon, "moved: shifted and turned 15°");
  for (const floor of hq.floors) {
    const before = floorFromPackage(hq, floor.id), after = floorFromPackage(moved, floor.id);
    const earlier = new Map(before.items.map((i) => [i.id, i]));
    for (const it of after.items) {
      const old = earlier.get(it.id);
      truly(old, `${it.id} was there before`);
      near(it.at, old.at, 1e-9, `${it.id} stands where it stood`);
      near(it.front, old.front, 1e-9, `${it.id} faces as it did`);
      const [p, q] = [hq, moved].map((pkg) => pkg.items.find((i) => i.id === it.id).properties);
      truly(Math.hypot(p.display_point[0] - q.display_point[0], p.display_point[1] - q.display_point[1]) > 1e-5
        && (q.heading - p.heading + 360) % 360 === 15, `${it.id}: its point and heading on the map moved with the building`);
    }
    // the rooms too, through the frame (each label point rounded to a centimetre on the map)
    for (const s of after.spaces) near(s.marker, before.spaces.find((x) => x.id === s.id).marker, 0.02, `${s.id} stays`);
  }
  // the desk carried to the Annex, and the TV taken away, are not in the Headquarters' plan
  const gone = hq.items.map((i) => i.id).filter((id) => !moved.items.some((i) => i.id === id));
  equal(gone, ["BYMBMX-I000003", "BYMBMX-I000005"], "gone");
  const annex = readPackage(join(conformance, "packages/campus-annex-2.storeypath"));
  const desk = floorFromPackage(annex, "BYMBMX-DEMO-ANNEX-F01").items.find((i) => i.id === "BYMBMX-I000003");
  near(desk.at, [143, 63.75], 1e-9, "the desk, in the Annex where its local says");
  near(desk.front, [1, 0], 1e-9, "facing +x there");
});

test("with no `local` (before 0.7) an item is placed by its point and heading on the map: the same place", () => {
  const older = structuredClone(hq);
  for (const i of older.items) delete i.properties.local;
  for (const floor of hq.floors) {
    const want = floorFromPackage(hq, floor.id).items, got = floorFromPackage(older, floor.id).items;
    equal(got.map((i) => i.id), want.map((i) => i.id), "the same items");
    got.forEach((it, k) => {
      near(it.at, want[k].at, 0.01, `${it.id}: there, to its point's rounding`);
      near(it.front, want[k].front, 1e-9, `${it.id}: facing the same way`);
    });
  }
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

inChrome("draws the items over the spaces and under the labels, each with a mark of its kind", async (page) => {
  await page.run(() => window.fresh({}, window.sp.floorFromPackage(window.pkg, "EWBSSN-DEMO-HQ-F00")));
  const got = await page.run(() => {
    const layers = [...document.querySelectorAll(".sp-world > g")].map((g) => g.getAttribute("class"));
    const count = (s) => document.querySelectorAll(s).length;
    const ap = document.querySelector(".sp-item-ap");
    return { layers, items: count(".sp-items [data-sp-item]"), desks: count(".sp-item-desk"), chairs: count(".sp-item-desk .sp-item-chair"),
      sofa: count(".sp-item-sofa .sp-item-mark"), tv: count(".sp-item-tv .sp-item-view"), copier: count(".sp-item-copier .sp-item-mark"), bed: count(".sp-item-bed .sp-item-mark"),
      ap: [ap.classList.contains("sp-item-overhead"), ap.querySelectorAll("path").length, /scale\(1,-1\)/.test(ap.getAttribute("transform"))],
      fronts: count(".sp-item-front"), buttons: count('.sp-items [role="button"][tabindex="0"]'),
      fill: getComputedStyle(document.querySelector(".sp-item-copier .sp-item-body")).fill,
      labelsLast: document.querySelector(".sp-labels").previousElementSibling === document.querySelector(".sp-world") };
  });
  equal(got.layers.indexOf("sp-items"), got.layers.indexOf("sp-containers") + 1, "over the spaces");
  truly(got.layers.indexOf("sp-items") < got.layers.indexOf("sp-walls") && got.labelsLast, `under the walls and labels: ${got.layers}`);
  equal([got.items, got.desks, got.chairs, got.sofa, got.tv, got.copier, got.fronts, got.buttons], [9, 4, 4, 1, 1, 1, 8, 9], "drawn");
  equal(got.bed, 4, "the bed: its headboard, two pillows and where its covers turn down");
  equal(got.ap, [true, 2, true], "the access point: overhead, a wifi mark, upright on the screen");
  equal(got.fill, "rgb(59, 110, 165)", "the copier in its type's colour");
});

inChrome("a click on an item chooses it: a select event with the item", async (page) => {
  await page.run(() => window.fresh({}, window.sp.floorFromPackage(window.pkg, "EWBSSN-DEMO-HQ-F01")));
  const desk = await page.run(() => {
    const it = window.sp.floorFromPackage(window.pkg, "EWBSSN-DEMO-HQ-F01").items.find((i) => i.type === "DESK-SECTION-HEAD");
    return { id: it.id, at: window.onPage(it.at) };
  });
  await page.click(...desk.at);
  const got = await page.run(() => ({ chosen: window.chosen, selected: window.engine.selected, item: window.picked.item?.type,
    space: window.picked.space, marked: document.querySelector(".sp-item.sp-selected")?.dataset.spItem,
    outline: document.querySelectorAll(".sp-selection path").length }));
  equal(got, { chosen: [desk.id], selected: desk.id, item: "DESK-SECTION-HEAD", space: null, marked: desk.id, outline: 1 }, "chosen");
  // and from the keyboard
  await page.run(() => window.engine.select(null));
  await page.run((id) => document.querySelector(`[data-sp-item="${id}"]`).focus(), desk.id);
  await page.key("Enter", "Enter", 13);
  equal(await page.run(() => window.engine.selected), desk.id, "Enter on an item chooses it");
});

inChrome("items can be hidden, or left out of clicks: then a click chooses the office under them", async (page) => {
  const office = "EWBSSN-DEMO-HQ-F01-0069"; // office 112, the head of section's desk in it
  const at = async () => page.run(() => {
    const it = window.sp.floorFromPackage(window.pkg, "EWBSSN-DEMO-HQ-F01").items.find((i) => i.type === "DESK-SECTION-HEAD");
    return window.onPage(it.at);
  });
  await page.run(() => window.fresh({ items: false }));
  equal(await page.run(() => [getComputedStyle(document.querySelector(".sp-items")).display, window.engine.itemsShown]),
    ["none", false], "hidden by the option");
  await page.click(...(await at()));
  equal(await page.run(() => window.chosen), [office], "the office");
  await page.run(() => window.engine.setItems(true));
  equal(await page.run(() => getComputedStyle(document.querySelector(".sp-items")).display), "inline", "shown again");
  const id =await page.run(() => document.querySelector(".sp-item-desk").dataset.spItem);
  await page.run((id) => window.engine.select(id), id);
  await page.run(() => window.engine.setItems(false));
  equal(await page.run(() => window.engine.selected), null, "a chosen item hidden is let go");
  await page.run(() => window.fresh({ interactiveItems: false }));
  await page.click(...(await at()));
  equal(await page.run(() => [window.chosen, document.querySelectorAll('.sp-items [tabindex]').length]), [[office], 0],
    "not clickable: the office under it");
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

inChrome("a space's drawing label is shown when it has no name or number", async (page) => {
  const lines = await page.run(async (office) => {
    const plan = window.sp.floorFromPackage(window.pkg, "EWBSSN-DEMO-HQ-F01");
    for (const s of plan.spaces) if (s.id === office) Object.assign(s, { name: null, number: null, label: "RM-GF-33" });
    await window.fresh({}, plan);
    return [...document.querySelectorAll(".sp-labels text")].map((t) => t.textContent);
  }, OFFICE);
  truly(lines.includes("RM-GF-33"), "the drawing's code as the label");
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
