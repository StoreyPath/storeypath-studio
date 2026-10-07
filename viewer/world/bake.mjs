// node bake.mjs <package.storeypath> <outdir>: every floor of a package in 3D, built
// ahead of time by the world's own builder (../src/world/build.js) and written as
// binary glTF, <outdir>/<floor-id>.glb: what a package's world/ folder holds (format
// 0.5, spec/FORMAT.md, "Pre-built 3D"). Studio runs it when it exports, so that a
// slow machine shows the building without building it. Nothing to install and
// nothing downloaded: three.js and JSZip are the copies in ../vendor.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { register } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

// "three", "three/addons/…" and "jszip" are the vendored copies, as build.mjs bundles them
const vendor = pathToFileURL(join(here, "../vendor/")).href;
register(`data:text/javascript,${encodeURIComponent(`
  const to = (s) => s === "three" ? "three/three.module.js" : s.startsWith("three/addons/") ? "three/" + s.slice(6)
    : s === "jszip" ? "jszip.mjs" : null;
  export async function resolve(specifier, context, next) {
    const path = to(specifier);
    return path ? { url: ${JSON.stringify(vendor)} + path, shortCircuit: true } : next(specifier, context);
  }`)}`);

// GLTFExporter reads what it wrote with a FileReader, which Node does not have
globalThis.FileReader ??= class FileReader {
  readAsArrayBuffer(blob) {
    blob.arrayBuffer().then((buffer) => {
      this.result = buffer;
      this.onloadend?.();
    });
  }
};

const THREE = await import("three");
const { GLTFExporter } = await import("three/addons/exporters/GLTFExporter.js");
const { mergeVertices } = await import("three/addons/utils/BufferGeometryUtils.js");
const { loadPackage } = await import("../src/package.js");
const { TYPE_COLORS } = await import("../src/theme.js");
const { GEOMETRY, buildFloor, originOf } = await import("../src/world/build.js");

// Colours near the world's, so the files look right in any glTF viewer; the world
// draws each piece with its own materials, by name.
const COLORS = { slab: 0xd8d4cc, wall: 0xf1ede6, wallPlain: 0xf1ede6, wallTop: 0xcfc9bf, wallCut: 0x3a3a3f,
  ceiling: 0xfbfaf7, glass: 0xbcd6e4, frame: 0x5b5f66, door: 0x9a7350, doorFrame: 0x6b4a32 };
const SEE_THROUGH = { glass: 0.28, volume: 0.22 };
const materials = new Map();
function material({ material: key, type }) {
  const name = type ? `${key}:${type}` : key;
  if (!materials.has(name)) {
    const see = SEE_THROUGH[key];
    materials.set(name, new THREE.MeshStandardMaterial({ name, roughness: 0.9,
      color: type ? TYPE_COLORS[type] || TYPE_COLORS.unspecified : COLORS[key] ?? 0xffffff,
      ...(see ? { transparent: true, opacity: see } : {}) }));
  }
  return materials.get(name);
}

/** A floor as binary glTF: one mesh a piece, named as build.js names it, with
 * what it is in its extras; the walker's obstacles as lines; and in the scene's
 * extras, what the world needs to use it (FORMAT.md, "Pre-built 3D"). */
async function bakeFloor(pkg, floor, origin) {
  const { plan, pieces, rooms, obstacles } = buildFloor(pkg, floor, origin, GEOMETRY);
  const scene = new THREE.Scene();
  scene.name = floor.id;
  scene.userData.storeypath = {
    project_id: pkg.project.id, building_id: floor.properties.building_id, floor_id: floor.id,
    export_sequence: pkg.manifest.export?.sequence ?? null,
    origin, options: { ...GEOMETRY }, elevation: plan.elevation, wall_height: plan.wallHeight, rooms,
  };
  for (const p of pieces) {
    // a vertex shared by its triangles, not repeated for each: a smaller file, and
    // less for the graphics card to transform
    const mesh = new THREE.Mesh(p.geometry.index ? p.geometry : mergeVertices(p.geometry, 1e-6), material(p));
    mesh.name = p.name;
    mesh.userData = { material: p.material, ...(p.view && { view: p.view }), ...(p.type && { type: p.type }),
      ...(p.hidden && { hidden: true }) };
    scene.add(mesh);
  }
  const e = plan.elevation;
  const lines = new Float32Array(obstacles.length * 6);
  obstacles.forEach(([x1, z1, x2, z2], i) => lines.set([x1, e, z1, x2, e, z2], i * 6));
  const walls = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(lines, 3)),
    new THREE.MeshBasicMaterial({ name: "obstacles", color: 0xff0000 }));
  walls.name = "obstacles";
  scene.add(walls);
  const glb = await new GLTFExporter().parseAsync(scene, { binary: true, onlyVisible: false });
  scene.traverse((o) => o.geometry?.dispose());
  return { glb, pieces: pieces.length, rooms: rooms.length };
}

const [source, out] = process.argv.slice(2);
if (!source || !out) {
  console.error("usage: node bake.mjs <package.storeypath> <outdir>");
  process.exit(2);
}
const pkg = await loadPackage(readFileSync(source));
mkdirSync(out, { recursive: true });
for (const building of pkg.buildings) {
  const origin = originOf(pkg, building.id);
  for (const floor of pkg.floorsOf(building.id)) {
    const t = performance.now();
    const { glb, pieces, rooms } = await bakeFloor(pkg, floor, origin);
    writeFileSync(join(out, `${floor.id}.glb`), new Uint8Array(glb));
    console.log(`${floor.id}.glb: ${pieces} pieces, ${rooms} rooms, ${Math.round(glb.byteLength / 1024)} KiB, `
      + `${Math.round(performance.now() - t)} ms`);
  }
}
