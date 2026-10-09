// node build.mjs: dist/world.js, the 3D world with three.js, JSZip and N8AO (its
// ambient occlusion) inside (from ../vendor, the copies the examples and Studio
// use), so that a system installs one file and downloads nothing at run time;
// dist/support.js, the WebGL check, small enough to load before deciding to load
// the world; their declarations; and the licences of what is inside.

import { build } from "esbuild";
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const vendor = join(here, "../vendor");
const dist = join(here, "dist");

// "three", "three/addons/…" and "jszip" are the vendored copies
const vendored = {
  name: "vendored",
  setup(b) {
    b.onResolve({ filter: /^three$/ }, () => ({ path: join(vendor, "three/three.module.js") }));
    b.onResolve({ filter: /^three\/addons\// }, (a) => ({ path: join(vendor, "three", a.path.slice("three/".length)) }));
    b.onResolve({ filter: /^jszip$/ }, () => ({ path: join(vendor, "jszip.min.js") }));
  },
};

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist);
const common = { bundle: true, format: "esm", target: "es2020", minify: true, legalComments: "none", logLevel: "warning" };
await build({ ...common, entryPoints: [join(here, "src/index.js")], outfile: join(dist, "world.js"), plugins: [vendored] });
await build({ ...common, entryPoints: [join(here, "src/support.js")], outfile: join(dist, "support.js") });
copyFileSync(join(here, "src/world.d.ts"), join(dist, "world.d.ts"));
copyFileSync(join(here, "src/support.d.ts"), join(dist, "support.d.ts"));
copyFileSync(join(here, "../src/navigation.d.ts"), join(dist, "navigation.d.ts")); // the way (format 0.8), shared

const version = (path) => readFileSync(path, "utf8");
writeFileSync(join(dist, "THIRD-PARTY-LICENSES.txt"), [
  "dist/world.js contains, besides StoreyPath's viewer (Apache-2.0, LICENSE):",
  "",
  "three.js r186 (0.186.1), https://threejs.org: the core and the addons the 3D world uses",
  "-".repeat(78),
  version(join(vendor, "three/LICENSE")).trim(),
  "",
  "JSZip 3.10.1, https://stuk.github.io/jszip/ (dual MIT/GPL-3.0; used under MIT)",
  "-".repeat(78),
  version(join(vendor, "LICENSE-jszip.markdown")).trim(),
  "",
  "N8AO 2.0.1, https://github.com/N8python/n8ao: ambient occlusion (CC0-1.0, public domain)",
  "-".repeat(78),
  version(join(vendor, "n8ao/LICENSE")).trim(),
  "",
].join("\n"));
console.log("built dist/world.js, dist/support.js");
