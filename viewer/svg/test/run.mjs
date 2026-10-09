// node test/run.mjs: the frame and the package reader in Node, then the engine in
// headless Chrome, used as people use it: clicks, drags, the wheel, two fingers,
// the keyboard. Positions are checked to a pixel: nothing may drift.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { launch, movedEast, readPackage, repack, serve, wrapped } from "./harness.mjs";
import { LocalFrame } from "../dist/frame.js";
import { floorFromPackage } from "../dist/package.js";
import { inside } from "../dist/geometry.js";
import { FORMAT_VERSION, readPackage as readInBrowsers } from "../dist/read.js";
import { Graph, route } from "../dist/navigation.js";
import { ITEM_ID_ALPHABET, isItemId, itemCheckSymbol, normalizeItemId } from "../dist/ids.js";

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
/** The same, numbers to a tolerance. */
const alike = (a, b, tolerance, what) => {
  if (typeof a === "number" && typeof b === "number") {
    if (!(Math.abs(a - b) <= tolerance)) throw new Error(`${what}: ${a} vs ${b}`);
  } else if (a && b && typeof a === "object" && typeof b === "object") {
    equal(Object.keys(a), Object.keys(b), `${what}: its parts`);
    for (const k of Object.keys(a)) alike(a[k], b[k], tolerance, `${what}.${k}`);
  } else equal(a, b, what);
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

test("across the antimeridian the frame is as anywhere: longitudes in (-180, 180]", () => {
  const { tolerance_m: tolerance, vectors } = JSON.parse(readFileSync(join(conformance, "localframe.json"), "utf8"));
  let across = 0;
  for (const v of vectors) {
    for (const at of [179.9999, -179.9999]) { // the same building turned round the earth to stand there
      const frame = new LocalFrame({ ...v.placement, lon: at });
      const want = [wrapped(v.lonlat[0] + at - v.placement.lon), v.lonlat[1]];
      near(frame.toLocal(want), v.local, tolerance, `to local at ${at}°`);
      const [lon, lat] = frame.toLonLat(v.local);
      truly(lon > -180 && lon <= 180, `${v.local} at ${at}°: longitude ${lon}`);
      const metres = [wrapped(lon - want[0]) * 111320 * Math.cos((lat * Math.PI) / 180), (lat - want[1]) * 110574];
      near(metres, [0, 0], tolerance, `to longitude and latitude at ${at}°`);
      if (Math.sign(lon) !== Math.sign(at)) across++;
    }
  }
  truly(across > 0, "no point across the antimeridian");
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

// spec/FORMAT.md, "Versioning": a reader reads its own major version, not newer than it
test("a package of a newer minor version, or of no version, is refused; older ones and newer patches are read", async () => {
  const path = join(conformance, "packages/campus-hq.storeypath");
  equal(`${readPackage(path).manifest.format_version}`.split(".").slice(0, 2).join("."), FORMAT_VERSION,
    "the viewer reads the version of the conformance packages");
  const said = async (version) => {
    const bytes = repack(path, (files) => { files.get("manifest.json").format_version = version; });
    try {
      const read = await readInBrowsers(bytes);
      return read.manifest.format_version === version ? "read" : "read, another version";
    } catch (e) {
      return e.message;
    }
  };
  for (const v of ["0.8.0", "0.8.12", "0.8", "0.7.0", "0.6.0", "0.3.1", "0.8.1-rc.1", "0.8.0+build.5"]) equal(await said(v), "read", v);
  for (const v of ["0.9.0", "0.9", "0.99.0", "0.9.0-rc1", "1.0.0", "2.7.0"]) {
    equal(await said(v), `This package is format ${v}, newer than this viewer's 0.8: update the viewer.`, v);
  }
  for (const v of ["", ".7", "0x0.7", "0.7.", "v0.7.0", "0.8a.0", "0.7.0.1", " 0.7.0", "0.7.0\n", "0.7.0-", "-1.7",
    "٠.٧", "０.７", 0.7, null, undefined]) {
    truly(/is not a version this viewer can read/.test(await said(v)), `${JSON.stringify(v)}: ${await said(v)}`);
  }
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

// a colour of the package's that would be CSS of its own in a style attribute
const BEACON = "red;fill:url(https://attacker.example/beacon.svg#a)";

test("a catalogue colour that is not #rrggbb is not used: the item takes the default colour", () => {
  const changed = structuredClone(pkg);
  const given = { COPIER: BEACON, "ACCESS-POINT": "#1F9D8B", SOFA: "rgb(1, 2, 3)", TV: "#abc", "BED-KING": "#3b6ea5\n",
    "DESK-DIRECTOR": 0x8a6238, "DESK-SENIOR": "url(#a)", "DESK-JUNIOR": "#3b6ea5 " };
  for (const t of changed.catalogue.types) if (t.code in given) t.color = given[t.code];
  const colors = Object.fromEntries(floorFromPackage(changed, "EWBSSN-DEMO-HQ-F00").items.map((i) => [i.type, i.color]));
  equal(Object.keys(given).map((type) => colors[type]),
    ["#8a8a8a", "#1F9D8B", "#8a8a8a", "#8a8a8a", "#8a8a8a", "#8a8a8a", "#8a8a8a", "#8a8a8a"], "colours");
});

// format 0.7: a package a building, each item placed in its building (`local`);
// campus-hq-2 is the same building after it was moved on the map (shifted, turned 15°)
const hq = readPackage(join(conformance, "packages/campus-hq.storeypath"));
const moved = readPackage(join(conformance, "packages/campus-hq-2.storeypath"));
const P = hq.manifest.project.id, HQ = `${P}-DEMO-HQ`; // remade samples are new projects

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
  const of = (type) => floorFromPackage(hq, `${HQ}-F00`).items.find((i) => i.type === type);
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
  const carried = hq.items.find((i) => i.properties.type === "DESK-JUNIOR" && i.properties.floor_id === `${HQ}-F00`).id;
  const tv = hq.items.find((i) => i.properties.type === "TV").id;
  equal(gone.sort(), [carried, tv].sort(), "gone");
  const annex = readPackage(join(conformance, "packages/campus-annex-2.storeypath"));
  const desk = floorFromPackage(annex, `${P}-DEMO-ANNEX-F01`).items.find((i) => i.id === carried);
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

test("a building across the antimeridian: its plan as anywhere else, its points back on the map in (-180, 180]", () => {
  const path = join(conformance, "packages/campus-hq.storeypath");
  const longitudes = (features) => {
    const lons = [];
    const visit = (c) => (typeof c[0] === "number" ? lons.push(c[0]) : c.forEach(visit));
    for (const f of features) visit(f.geometry.coordinates);
    return lons;
  };
  const lons = longitudes(hq.floors);
  const across = readPackage(movedEast(path, 180 - (Math.min(...lons) + Math.max(...lons)) / 2)); // its middle at 180°
  const moved = longitudes(across.floors);
  truly(moved.some((lon) => lon > 179.999) && moved.some((lon) => lon < -179.999), "across it");
  for (const floor of hq.floors) alike(floorFromPackage(across, floor.id), floorFromPackage(hq, floor.id), 1e-6, floor.id);
  const frame = new LocalFrame(across.manifest.placements[HQ]);
  for (const s of [...across.spaces, ...across.zones]) {
    const [lon, lat] = frame.toLonLat(frame.toLocal(s.properties.display_point));
    truly(lon > -180 && lon <= 180, `${s.id}: longitude ${lon}`);
    near([lon, lat], s.properties.display_point, 1e-9, `${s.id}: its label point back on the map`);
  }
});

test("an unplaced building reads in its own metres too", () => {
  const unplaced = readPackage(join(conformance, "packages/unplaced.storeypath"));
  const floor = unplaced.floors[0];
  const plan = floorFromPackage(unplaced, floor.id);
  truly(plan.spaces.length > 0, "spaces");
  for (const s of plan.spaces) truly(inside(s.polygons, s.marker), `${s.id}: its label point is inside it`);
});

// format 0.8: items' IDs (ids.js, the module the viewers share), as every reader of
// the format reads them: spec/conformance/asset-ids.json
const assetIds = JSON.parse(readFileSync(join(conformance, "asset-ids.json"), "utf8"));
test("items' IDs: check symbols, IDs right and wrong, and what people type, as every reader reads them", () => {
  equal(ITEM_ID_ALPHABET, assetIds.alphabet, "the alphabet");
  equal(itemCheckSymbol("7K2QXM9F4D"), "P", "FORMAT.md's worked example");
  for (const c of assetIds.check) equal(itemCheckSymbol(c.symbols), c.check, `the check symbol of ${c.symbols}`);
  for (const bad of ["7K2QXM9F4", "7K2QXM9F4DP", "7k2qxm9f4d", "7K2QXM9F4O", null]) {
    equal(itemCheckSymbol(bad), null, `no check symbol of ${bad}`);
  }
  for (const id of [...assetIds.valid, ...assetIds.swapped_unseen]) truly(isItemId(id), `${id} is an item's ID`);
  for (const id of [...assetIds.wrong_symbol, ...assetIds.swapped, ...assetIds.not_ids]) {
    truly(!isItemId(id), `${JSON.stringify(id)} is not an item's ID`);
  }
  for (const t of assetIds.typed) equal(normalizeItemId(t.text), t.id, `${JSON.stringify(t.text)} as typed`);
  equal([normalizeItemId(null), normalizeItemId(42)], [null, null], "not text");
  truly(hq.items.length && hq.items.every((i) => isItemId(i.id)), "every item of campus-hq has one");
});

// format 0.8: the walking network, and the way on it (navigation.js, the module both
// viewers share, as the plan engine's package carries it)
const routes = JSON.parse(readFileSync(join(conformance, "routes.json"), "utf8"));
const packages = new Map();
const packageOf = (name) => {
  if (!packages.has(name)) packages.set(name, readPackage(join(conformance, "packages", name)));
  return packages.get(name);
};
/** The same, whatever the order of an object's keys; numbers to a tolerance. */
const sameAs = (a, b, tolerance, what) => {
  if (typeof a === "number" && typeof b === "number") {
    if (!(Math.abs(a - b) <= tolerance)) throw new Error(`${what}: ${a} vs ${b}`);
  } else if (a && b && typeof a === "object" && typeof b === "object") {
    equal(Object.keys(a).sort(), Object.keys(b).sort(), `${what}: its keys`);
    for (const k of Object.keys(a)) sameAs(a[k], b[k], tolerance, `${what}.${k}`);
  } else equal(a, b, what);
};

for (const c of routes.routes) {
  test(`the way ${c.name}: found as Studio finds it`, () => {
    const got = route(packageOf(c.package), c.from, c.to, { accessible: c.accessible });
    const want = c.expect;
    truly(got, "no way found");
    sameAs(got.nodes, want.nodes, 0, "nodes");
    sameAs(got.changes, want.changes, 0, "changes");
    sameAs(got.steps, want.steps, 0, "steps");
    sameAs(got.legs, want.legs, 0, "legs");
    sameAs([got.metres, got.seconds], [want.metres, want.seconds], Math.min(routes.tolerance_m, routes.tolerance_s), "length and time");
    sameAs(got, want, 0, "the whole way");
  });
}

test("a 0.8 package carries its network, and its lifts' and stairs' stacks", async () => {
  const nav = hq.navigation;
  truly(nav && nav.nodes.length > 200 && nav.edges.length > 200 && nav.buildings[0] === HQ, "its network");
  equal((await readInBrowsers(readFileSync(join(conformance, "packages/campus-hq.storeypath")))).navigation, nav, "read in browsers too");
  equal(pkg.navigation, null, "an older package has none");
  const lifts = hq.spaces.filter((s) => s.properties.type === "elevator");
  equal(lifts.length, 6, "two lifts on three floors");
  const stacks = [...new Set(lifts.map((s) => s.properties.stack))];
  equal(stacks.length, 2, "two stacks");
  for (const key of stacks) truly(lifts.some((s) => s.id === key && s.properties.floor_id === `${HQ}-F00`), `${key}: its ground floor space`);
  truly(hq.spaces.filter((s) => s.properties.type === "office").every((s) => s.properties.stack === null), "an office has none");
  const plan = floorFromPackage(hq, `${HQ}-F01`);
  equal(plan.spaces.filter((s) => s.stack).map((s) => s.stack).sort(), [...stacks, hq.spaces.find((s) => s.properties.type === "stairs").properties.stack].sort(),
    "the plan's lifts and stairs carry theirs");
});

test("no way is null; an ID the network does not have, or a package without one, is an error", () => {
  const easy = routes.routes.find((c) => c.accessible && c.package === "campus-hq.storeypath");
  const noLifts = { ...hq.navigation, edges: hq.navigation.edges.filter((e) => e.kind !== "lift") };
  equal(route(noLifts, easy.from, easy.to, { accessible: true, items: hq.items }), null, "no lift: no way without stairs");
  truly(route(noLifts, easy.from, easy.to, { items: hq.items }).changes[0].by === "stairs", "with stairs, a way");
  const said = (fn) => {
    try {
      fn();
    } catch (e) {
      return e.message;
    }
    return "";
  };
  truly(/no node, place or item NOWHERE/.test(said(() => route(hq, "NOWHERE", easy.to))), "an unknown ID");
  truly(/no walking network/.test(said(() => route(pkg, "A", "B"))), "a package of 0.6");
  const g = new Graph(hq.navigation);
  equal(route(g, easy.to, easy.to).steps.at(-1).side, "here", "a Graph routes too");
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
      labelsLast: [...document.querySelector(".sp-plan").children].indexOf(document.querySelector(".sp-labels"))
        > [...document.querySelector(".sp-plan").children].indexOf(document.querySelector(".sp-world")) };
  });
  equal(got.layers.indexOf("sp-items"), got.layers.indexOf("sp-containers") + 1, "over the spaces");
  truly(got.layers.indexOf("sp-items") < got.layers.indexOf("sp-walls") && got.labelsLast, `under the walls and labels: ${got.layers}`);
  equal([got.items, got.desks, got.chairs, got.sofa, got.tv, got.copier, got.fronts, got.buttons], [9, 4, 4, 1, 1, 1, 8, 9], "drawn");
  equal(got.bed, 4, "the bed: its headboard, two pillows and where its covers turn down");
  equal(got.ap, [true, 2, true], "the access point: overhead, a wifi mark, upright on the screen");
  equal(got.fill, "rgb(59, 110, 165)", "the copier in its type's colour");
});

inChrome("a desk is drawn with what goes with its grade: visitors' chairs, a return, a cabinet, a high-backed chair", async (page) => {
  const plans = [`${HQ}-F00`, `${HQ}-F01`].map((id) => floorFromPackage(hq, id));
  equal(plans[0].items.find((i) => i.type === "DESK-DIRECTOR").grade, "director", "its type's grade");
  const drawn = {};
  for (const plan of plans) {
    await page.run((p) => window.fresh({}, p), plan);
    Object.assign(drawn, await page.run(() => Object.fromEntries([...document.querySelectorAll(".sp-item-desk")].map((g) => [
      g.querySelector("title").textContent, [g.querySelectorAll(".sp-item-body").length, g.querySelectorAll(".sp-item-visitor").length,
        g.querySelectorAll(".sp-item-back").length]]))));
  }
  // [desk, return, cabinet], visitors, chairs' backs (a high-backed one's and the visitors'),
  // by name (they are drawn in the order of their IDs, which is no order)
  const byName = (o) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : 1)));
  equal(byName(drawn), byName({ "Director's desk": [3, 2, 3], "Senior staff desk": [2, 0, 0], "Junior staff desk": [1, 0, 0],
    "Manager's desk": [2, 2, 2], "Head of section desk": [2, 1, 1] }), "by grade");
  const older = floorFromPackage(pkg, "EWBSSN-DEMO-HQ-F00"); // 0.6: no grades in its catalogue
  truly(older.items.every((i) => i.grade === null), "an older package's desks are plain");
});

inChrome("a wayfinding kiosk: its screen along its front, and the way it faces", async (page) => {
  const plan = floorFromPackage(hq, `${HQ}-F00`);
  const kiosk = plan.items.find((i) => i.type === "KIOSK");
  near(kiosk.front, [0, -1], 1e-6, "it faces the drawing's -y: the reception's door");
  await page.run((p) => window.fresh({}, p), plan);
  const got = await page.run((id) => {
    const g = document.querySelector(`[data-sp-item="${id}"]`);
    return [g.classList.contains("sp-item-kiosk"), g.querySelectorAll(".sp-item-screen").length, g.querySelectorAll(".sp-item-view").length,
      getComputedStyle(g.querySelector(".sp-item-body")).fill];
  }, kiosk.id);
  equal(got, [true, 1, 1, "rgb(217, 120, 43)"], "a kiosk: its screen, the way it faces, its type's colour");
});

inChrome("an item's colour reaches the page as a colour, never as CSS of its own", async (page) => {
  const changed = structuredClone(pkg);
  for (const t of changed.catalogue.types) if (t.code === "COPIER") t.color = BEACON;
  const plan = floorFromPackage(changed, "EWBSSN-DEMO-HQ-F00");
  const copier = plan.items.find((i) => i.type === "COPIER").id;
  for (const type of ["SOFA", "ACCESS-POINT"]) { // and the host's own, square and round, coloured so
    plan.items.push({ ...plan.items.find((i) => i.type === type), id: `HOST-${type}`, color: BEACON });
  }
  const got = await page.run(async (plan, copier) => {
    await window.fresh({}, plan);
    const body = (id) => document.querySelector(`[data-sp-item="${id}"] .sp-item-body`);
    return { styles: [...document.querySelectorAll(".sp-items [style]")].map((e) => e.getAttribute("style")),
      copier: getComputedStyle(body(copier)).fill, host: ["HOST-SOFA", "HOST-ACCESS-POINT"].map((id) => body(id).style.fill) };
  }, plan, copier);
  truly(got.styles.length > 0 && got.styles.every((s) => /^fill: [^;]*;$/.test(s) && !/url/.test(s)), `styles: ${got.styles}`);
  equal(got.copier, "rgb(138, 138, 138)", "the package's: the default colour");
  equal(got.host, ["", ""], "the host's: not a colour, none");
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

// a way from the kiosk in the Headquarters' reception up to an office, by lift
const lifted = routes.routes.find((c) => c.accessible && c.package === "campus-hq.storeypath" && c.expect.changes.length);
const floorNames = Object.fromEntries(hq.floors.map((f) => [f.id, f.properties.name]));

inChrome("a way is drawn on the floor it is on: its line, arrows, its start, and where it changes floor", async (page) => {
  const way = route(hq, lifted.from, lifted.to, { accessible: true });
  const [ground, up] = [way.legs[0], way.legs[1]];
  await page.run((p) => window.fresh({}, p), floorFromPackage(hq, ground.floor_id));
  const look = (shown = way) => page.run((way, names) => {
    if (way) window.engine.showRoute(way, { floorName: (id) => names[id] });
    const mark = (s) => [...document.querySelectorAll(s)].map((m) => ({ at: [Number(m.dataset.planX), Number(m.dataset.planY)],
      screen: m.getAttribute("transform"), text: m.querySelector("text")?.textContent ?? null, side: m.dataset.spRouteChange ?? null }));
    const r = window.engine.svg.getBoundingClientRect();
    return { legs: document.querySelector(".sp-route").dataset.spRoute ?? null, lines: document.querySelectorAll(".sp-route path").length,
      arrows: document.querySelectorAll(".sp-route-arrow").length, start: mark("[data-sp-route-start]"),
      end: mark("[data-sp-route-end]"), change: mark("[data-sp-route-change]"),
      box: [r.width, r.height] };
  }, shown, floorNames);
  const at = (p) => page.run((p) => window.engine.toScreen(p).map((v) => Math.round(v * 100) / 100), p);
  const where = (m) => m.screen.match(/translate\(([-\d.]+),([-\d.]+)\)/).slice(1).map(Number);
  let got = await look();
  equal([got.legs, got.lines, got.start.length, got.end.length, got.change.length], ["1", 2, 1, 0, 1], "the ground floor's leg");
  truly(got.arrows >= 2, `arrows along it: ${got.arrows}`);
  near(got.start[0].at, ground.points[0], 0.001, "the start, at the way's first point");
  near(where(got.start[0]), await at(ground.points[0]), 1, "…on the screen, to a pixel");
  equal([got.change[0].side, got.change[0].text], ["to", "Lift to Floor 1"], "where it leaves the floor");
  near(got.change[0].at, ground.points.at(-1), 0.001, "…at the lift");
  // the view moves: the marks with it
  await page.run(() => window.engine.zoomBy(2, [300, 200]));
  got = await look(null);
  near(where(got.start[0]), await at(ground.points[0]), 1, "the start, after zooming");
  // another floor: its leg, kept
  await page.run((p) => window.engine.setFloor(p), floorFromPackage(hq, up.floor_id));
  got = await look(null);
  equal([got.legs, got.start.length, got.end.length, got.change.length], ["1", 0, 1, 1], "the first floor's leg");
  equal([got.change[0].side, got.change[0].text], ["from", "Lift from Ground floor"], "where it comes onto the floor");
  near(got.end[0].at, up.points.at(-1), 0.001, "the end, at the way's last point");
  near(where(got.end[0]), await at(up.points.at(-1)), 1, "…on the screen, to a pixel");
  // a floor it does not go to: nothing; and taken away
  await page.run((p) => window.engine.setFloor(p), floorFromPackage(hq, `${HQ}-F02`));
  got = await look(null);
  equal([got.legs, got.lines, got.start.length, got.end.length, got.change.length, got.arrows], [null, 0, 0, 0, 0, 0], "the second floor");
  await page.run((p) => window.engine.setFloor(p), floorFromPackage(hq, up.floor_id));
  equal((await look(null)).lines, 2, "back on the first floor");
  await page.run(() => window.engine.clearRoute());
  got = await look(null);
  equal([got.legs, got.lines, got.end.length, got.change.length, got.arrows], [null, 0, 0, 0, 0], "cleared");
  equal(await page.run(() => window.engine.route), null, "no way shown");
});

inChrome("a way is under the labels, its line as wide at any zoom, styled by the host's CSS", async (page) => {
  const way = route(hq, lifted.from, lifted.to, { accessible: true });
  await page.run((p) => window.fresh({}, p), floorFromPackage(hq, way.legs[0].floor_id));
  const got = await page.run((way) => {
    window.engine.showRoute(way, { fit: true });
    const svg = window.engine.svg;
    const order = [...svg.children].map((c) => c.getAttribute("class"));
    const world = [...document.querySelector(".sp-world").children].map((c) => c.getAttribute("class"));
    const line = document.querySelector(".sp-route-line");
    const width = () => line.getBoundingClientRect();
    const before = getComputedStyle(line).strokeWidth;
    window.engine.zoomBy(3);
    const after = getComputedStyle(line).strokeWidth;
    svg.style.setProperty("--sp-route", "rgb(1, 2, 3)");
    const styled = getComputedStyle(line).stroke;
    // fitted: the leg's points inside the plan's box
    const r = svg.getBoundingClientRect();
    return { order, world, before, after, styled, effect: getComputedStyle(line).vectorEffect, box: [r.width, r.height] };
  }, way);
  truly(got.order.indexOf("sp-route-marks") < got.order.indexOf("sp-labels") && got.order.indexOf("sp-world") < got.order.indexOf("sp-route-marks"),
    `the marks over the plan, under the labels: ${got.order}`);
  equal(got.world.at(-1), "sp-route", "the line over the walls and doors");
  equal([got.before, got.after, got.effect], ["5px", "5px", "non-scaling-stroke"], "as wide at any zoom");
  equal(got.styled, "rgb(1, 2, 3)", "the host's colour");
  await page.run((way) => { window.engine.setCamera({ k: 0.5, tx: 0, ty: 0 }); window.engine.showRoute(way, { fit: true }); }, way);
  const inView = await page.run((points) => {
    const r = window.engine.svg.getBoundingClientRect();
    return points.every((p) => {
      const [x, y] = window.engine.toScreen(p);
      return x >= 0 && y >= 0 && x <= r.width && y <= r.height;
    });
  }, way.legs[0].points);
  truly(inView, "fit: the way on this floor in view");
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
