// node spec/finishes.mjs: StoreyPath's finishes (spec/finishes.json, the one list) copied
// where each reader takes them from: viewer/src/finishes.js (the viewers, and Studio's
// pages, which load the viewer's sources) and go/finishes.json (embedded in the Go
// module). Studio reads spec/finishes.json itself. Each reader's tests check its copy is
// this list as it is: run this after changing it, and commit the copies with it.
//
// node spec/finishes.mjs --check: whether the copies are up to date (exit 1 if not).

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "finishes.json"), "utf8");
const data = JSON.parse(source);

/** The viewer's module: the list, and how a room's finishes are found from it. */
export function viewerModule(list = data) {
  return `// StoreyPath's finishes (format 0.9): the floors and walls a room may be given, a fixed
// set every reader has. Made from spec/finishes.json by spec/finishes.mjs: do not edit.
//
// A space or zone may name its floor finish (\`floor_finish\`) and a space its walls'
// (\`wall_finish\`); null, or a code this list does not have (a later version's), is its
// type's default. A zone with none takes its space's floor finish, else its own type's
// default. A wall's face shows the finish of the room it faces; a face outside every room,
// \`exterior\`.

/** The list: { version, exterior, groups, finishes, defaults: { floor, wall } by type }. */
export const FINISHES = ${JSON.stringify(list, null, 2)};

const BY_CODE = new Map(FINISHES.finishes.map((f) => [f.code, f]));

/** A finish by its code ({ code, applies, group, name, name_ar, tone, roughness, size_m,
 * paint }), or null. */
export function finishOf(code) {
  return (typeof code === "string" && BY_CODE.get(code)) || null;
}

/** A finish's code when it is one of this list's, for floors or walls (\`applies\`); else null. */
export function knownFinish(code, applies) {
  const f = finishOf(code);
  return f && f.applies === applies ? f.code : null;
}

/** The finish a type of room has when it is given none (an unknown type: as \`unspecified\`). */
export function defaultFinish(applies, type) {
  const table = FINISHES.defaults[applies];
  return table[type] ?? table.unspecified;
}

/** The floor finish a space or zone shows, from its properties (and, for a zone, its
 * space's): its own, else its space's, else its type's default. */
export function floorFinish(props, space = null) {
  return knownFinish(props?.floor_finish, "floor") ?? knownFinish(space?.floor_finish, "floor")
    ?? defaultFinish("floor", props?.type);
}

/** The finish of a space's walls, from its properties: its own, else its type's default. */
export function wallFinish(props) {
  return knownFinish(props?.wall_finish, "wall") ?? defaultFinish("wall", props?.type);
}

/** The finish of walls' faces outside every room. */
export const EXTERIOR = FINISHES.exterior;
`;
}

const outputs = [
  [join(here, "../viewer/src/finishes.js"), viewerModule()],
  [join(here, "../go/finishes.json"), source],
];

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const check = process.argv.includes("--check");
  let stale = 0;
  for (const [path, text] of outputs) {
    let have = null;
    try {
      have = readFileSync(path, "utf8");
    } catch { /* not made yet */ }
    if (have === text) continue;
    if (check) {
      console.error(`${path} is not spec/finishes.json as it is: run node spec/finishes.mjs`);
      stale++;
    } else {
      writeFileSync(path, text);
      console.log(`wrote ${path}`);
    }
  }
  if (stale) process.exit(1);
}
