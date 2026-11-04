// node itemtypes.mjs <Studio's address> <project code> <a folder for files>: Studio's Item types
// page (index.html#/item-types) in headless Chrome, used as a person uses it (the viewers'
// harness: clicks, typing, a file chosen), on the copy of the tests' campus that `storeypath
// review` serves (tests/test_review_browser.py starts it, as an admin: this computer alone).
// A type added, changed, retired and restored; types taken from a file, some chosen; the
// package export's choice of item types. The run stops at the first failure.

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { launch } = await import(pathToFileURL(join(here, "../../../storeypath-viewer/viewer/svg/test/harness.mjs")).href);

const [base, code, folder] = process.argv.slice(2);
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const truly = (v, what) => {
  if (!v) throw new Error(what);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const page = await launch({ timeout: 600 });
const R = (fn, ...args) => page.run(fn, ...args);

async function until(fn, what, ms = 20000, ...args) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) {
    last = await R(fn, ...args);
    if (last) return last;
    await sleep(100);
  }
  throw new Error(`${what} (waited ${ms} ms; last: ${JSON.stringify(last)})`);
}

const click = (selector) => R((s) => {
  const e = document.querySelector(s);
  if (!e) throw new Error(`nothing is ${s}`);
  e.click();
}, selector);
/** Typed into a field, as keys type it (what it held first taken away). */
async function type(selector, text) {
  await R((s) => {
    const e = document.querySelector(s);
    e.focus();
    e.select?.();
  }, selector);
  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 });
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 });
  await page.send("Input.insertText", { text });
}
const catalogue = () => R(async () => (await (await fetch("/api/catalogue")).json()).types);
const typeOf = async (c) => (await catalogue()).find((t) => t.code === c);
/** What ``fn`` (here, in Node) gives once it is so, or its last. */
async function soon(fn, ok, ms = 20000) {
  const end = Date.now() + ms;
  let last = await fn();
  while (!ok(last) && Date.now() < end) {
    await sleep(100);
    last = await fn();
  }
  return last;
}
/** A choice made in a select (and said: "change"). */
const pick = (selector, value) => R((s, v) => {
  const e = document.querySelector(s);
  e.value = v;
  e.dispatchEvent(new Event("change", { bubbles: true }));
}, selector, value);
/** A field's value set at once, as a paste does (and said: "input"). */
const set = (selector, value) => R((s, v) => {
  const e = document.querySelector(s);
  e.value = v;
  e.dispatchEvent(new Event("input", { bubbles: true }));
}, selector, value);
const noErrors = (what) => truly(!page.errors.length, `${what}: the console said ${JSON.stringify(page.errors)}`);

test("the page lists the catalogue by category, each type with its symbol, how it is drawn and its size", async () => {
  await page.open(`${base}/index.html#/item-types`, 1440, 960);
  await until(() => document.querySelectorAll(".it-row").length > 5, "the list", 30000);
  const got = await R(() => ({
    title: document.querySelector(".page h1").textContent,
    groups: [...document.querySelectorAll(".it-group h3")].map((h) => h.firstChild.textContent),
    rows: document.querySelectorAll(".it-row").length,
    symbols: document.querySelectorAll(".it-row svg.item-symbol").length,
    table: document.querySelector('.it-row[data-code="MEETING-TABLE-8"] .it-meta').textContent,
    chairs: document.querySelectorAll('.it-row[data-code="MEETING-TABLE-8"] .chair:not(.back)').length,
    crumbs: [...document.querySelectorAll("#crumbs .crumb")].map((c) => c.textContent),
  }));
  const types = await catalogue();
  truly(got.title === "Item types" && got.crumbs.join("/") === "Projects/Item types", `the page: ${JSON.stringify(got)}`);
  truly(got.rows === types.filter((t) => !t.retired).length && got.symbols === got.rows, `every type, a symbol each: ${JSON.stringify(got)}`);
  truly(got.groups.join() === "Workstations,Seating,Tables,Storage,Soft seating,Beds,Security,Fire and life safety,IT and AV,Building services,Appliances",
    `by category, in their order: ${got.groups}`);
  // a table alone (format 0.10): its chairs are types of their own
  truly(/2\.4 × 1\.2 × 0\.75 m/.test(got.table) && /Table$/.test(got.table) && got.chairs === 0, `a table for 8: ${JSON.stringify(got)}`);
  noErrors("the list");
});

test("a search finds a type by its name or code; a category shows its own", async () => {
  await type('input[type="search"]', "kiosk");
  await until(() => document.querySelectorAll(".it-row").length === 1, "one type found");
  truly(await R(() => document.querySelector(".it-row").dataset.code) === "KIOSK", "the kiosk");
  await set('input[type="search"]', "");
  await pick(".it-filters select", "security");
  const codes = await R(() => [...document.querySelectorAll(".it-row")].map((r) => r.dataset.code));
  truly(codes.length > 5 && codes.includes("CCTV-DOME") && codes.includes("CARD-READER") && !codes.includes("TV"), `security: ${codes}`);
  await pick(".it-filters select", "all");
});

test("a new type: its code, names, how it is drawn and its size; refused until it has them; added to the catalogue", async () => {
  await click('[data-testid="it-new"]');
  await until(() => document.querySelector('[data-testid="it-code"]'), "the new type's form");
  const blocked = await R(() => ({ disabled: document.querySelector(".it-form button[type=submit]").disabled,
    problems: document.querySelectorAll(".it-problems li").length }));
  truly(blocked.disabled && blocked.problems >= 2, `a type with no code nor name: ${JSON.stringify(blocked)}`);
  await type('[data-testid="it-code"]', "locker tall");
  await type('[data-testid="it-name"]', "Tall locker");
  await type('[data-testid="it-width"]', "0.9");
  await click('.it-shape[data-shape="copier"]');
  const ready = await R(() => ({ code: document.querySelector('[data-testid="it-code"]').value,
    disabled: document.querySelector(".it-form button[type=submit]").disabled }));
  truly(ready.code === "LOCKER-TALL" && !ready.disabled, `ready to add: ${JSON.stringify(ready)}`);
  await click(".it-form button[type=submit]");
  await until(() => document.querySelector('.it-row[data-code="LOCKER-TALL"]'), "the new type in the list");
  const t = await typeOf("LOCKER-TALL");
  truly(t && t.name_en === "Tall locker" && t.width === 0.9 && t.shape === "copier" && !t.retired, `saved: ${JSON.stringify(t)}`);
  noErrors("a new type");
});

test("a type changed: its name, its colour, how it is drawn, a field; saved as it is shown", async () => {
  await click('.it-row[data-code="LOCKER-TALL"]');
  await until(() => document.querySelector(".it-form h2")?.textContent === "Tall locker", "its form");
  await type('[data-testid="it-name"]', "Tall locker, grey");
  await type('input[aria-label="Colour as #rrggbb"]', "#6b7280");
  await click('.it-shape[data-shape="box"]');
  await click(".it-fields > button");
  await type('.it-field:not(.head) input[aria-label="Key"]', "model");
  await type('.it-field:not(.head) input[aria-label="Name"]', "Model");
  await click(".it-form button[type=submit]");
  const t = await soon(() => typeOf("LOCKER-TALL"), (got) => got?.name_en === "Tall locker, grey");
  truly(t.name_en === "Tall locker, grey" && t.color === "#6b7280" && t.shape === "box"
    && JSON.stringify(t.fields) === JSON.stringify([{ key: "model", name_en: "Model", name_ar: "", kind: "text", choices: [], owner: "storeypath" }]),
  `changed: ${JSON.stringify(t)}`);
  const row = await R(() => document.querySelector('.it-row[data-code="LOCKER-TALL"] .it-name strong').textContent);
  truly(row === "Tall locker, grey", `the list says so: ${row}`);
  noErrors("a type changed");
});

test("a camera type: how wide it sees and how far, changed and saved; a card reader serves a door", async () => {
  await click('.it-row[data-code="CCTV-DOME"]');
  await until(() => document.querySelector(".it-form h2")?.textContent === "CCTV camera, dome", "its form");
  const view = () => R(() => [...document.querySelectorAll(".it-form label.field")].filter((l) => /^Sees /.test(l.textContent))
    .map((l) => l.querySelector("input").value));
  truly(JSON.stringify(await view()) === JSON.stringify(["90", "12"]), `its view: ${await view()}`);
  await R(() => {
    const i = [...document.querySelectorAll(".it-form label.field")].find((l) => l.textContent.startsWith("Sees across")).querySelector("input");
    i.value = "100";
    i.dispatchEvent(new Event("input", { bubbles: true }));
  });
  truly(await R(() => document.querySelector(".it-form").checkValidity()), "the form may be sent: a size of 0.12 m is no step of 0.05");
  await click(".it-form button[type=submit]");
  const t = await soon(() => typeOf("CCTV-DOME"), (got) => got?.view_deg === 100);
  truly(t.view_deg === 100 && t.range_m === 12, `saved: ${JSON.stringify(t)}`);
  await click('.it-row[data-code="CARD-READER"]');
  await until(() => document.querySelector(".it-form h2")?.textContent === "Access card reader", "the reader's form");
  truly(await R(() => document.querySelector('[data-testid="it-serves-door"]').checked
    && ![...document.querySelectorAll(".it-form label.field")].some((l) => /^Sees /.test(l.textContent))),
  "a card reader: serves a door, no view");
  noErrors("a camera's view, a reader's door");
});

test("a type retired: not listed unless retired ones are shown, marked so; restored", async () => {
  await click('.it-row[data-code="LOCKER-TALL"]');
  await until(() => [...document.querySelectorAll(".it-actions button")].some((b) => b.textContent === "Retire"), "Retire");
  await R(() => [...document.querySelectorAll(".it-actions button")].find((b) => b.textContent === "Retire").click());
  await until(() => !document.querySelector('.it-row[data-code="LOCKER-TALL"]'), "it leaves the list");
  truly((await soon(() => typeOf("LOCKER-TALL"), (t) => t.retired)).retired === true, "retired in the catalogue");
  await click(".it-retired input");
  await until(() => document.querySelector('.it-row.retired[data-code="LOCKER-TALL"] .badge.warn'), "shown, marked retired");
  await click('.it-row[data-code="LOCKER-TALL"]');
  await until(() => [...document.querySelectorAll(".it-actions button")].some((b) => b.textContent === "Restore"), "Restore");
  await R(() => [...document.querySelectorAll(".it-actions button")].find((b) => b.textContent === "Restore").click());
  await until(() => document.querySelector('.it-row[data-code="LOCKER-TALL"]:not(.retired)'), "restored in the list");
  truly((await soon(() => typeOf("LOCKER-TALL"), (t) => !t.retired)).retired === false, "restored in the catalogue");
  await click(".it-retired input");
  noErrors("retired and restored");
});

test("types from a file: new ones chosen, a changed one only when chosen, the same ones not offered; those chosen taken", async () => {
  const ours = await catalogue();
  const sofa = ours.find((t) => t.code === "SOFA");
  const file = join(folder, "from-another-studio.json");
  writeFileSync(file, JSON.stringify({ format: "storeypath-catalogue", format_version: 1, types: [
    ...ours.filter((t) => t.code.startsWith("DESK-")).map((t, i) => (i ? t : { ...t, plan: { lines: [[[-t.width / 2, 0], [t.width / 2, 0]]] } })),
    { ...sofa, color: "#5c6b73", width: 2.2 },
    { code: "PLANTER", name_en: "Planter", category: "furniture", width: 0.6, depth: 0.6, height: 1.2, color: "#2f9e44", shape: "box" },
    { code: "WHITEBOARD", name_en: "Whiteboard", category: "equipment", width: 1.8, depth: 0.05, height: 1.2, mount: "wall", color: "#f1f3f5" },
  ] }));
  await click('[data-testid="it-import"]');
  await until(() => document.querySelector("dialog.it-import[open] input[type=file]"), "the dialog");
  const { root } = await page.send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await page.send("DOM.querySelector", { nodeId: root.nodeId, selector: "dialog.it-import input[type=file]" });
  await page.send("DOM.setFileInputFiles", { nodeId, files: [file] });
  await until(() => document.querySelector(".it-import-table"), "what the file brings", 20000);
  const offered = await R(() => [...document.querySelectorAll(".it-import-table tbody tr")].map((r) => ({
    code: r.dataset.code, status: r.className, ticked: r.querySelector("input").checked, open: !r.querySelector("input").disabled,
    said: r.querySelector("td:nth-child(4)").textContent })));
  const by = Object.fromEntries(offered.map((o) => [o.code, o]));
  truly(offered.slice(0, 3).map((o) => o.status).join() === "new,new,changed", `what there is to choose, first: ${JSON.stringify(offered.slice(0, 4))}`);
  truly(by.PLANTER.ticked && by.WHITEBOARD.ticked && !by.SOFA.ticked && by.SOFA.open && /size/.test(by.SOFA.said) && /colour/.test(by.SOFA.said),
    `chosen: ${JSON.stringify(by)}`);
  truly(offered.filter((o) => o.status === "same").every((o) => !o.open && !o.ticked), "the same ones not offered");
  // one that differs only by a drawing of its own is a change to choose
  const drawn = ours.filter((t) => t.code.startsWith("DESK-"))[0].code;
  truly(by[drawn].open && by[drawn].status === "changed" && /drawing/.test(by[drawn].said), `its drawing: ${JSON.stringify(by[drawn])}`);
  await click(`.it-import-table tr[data-code="${drawn}"] input`);
  // the whiteboard left out, the sofa's change taken
  await click('.it-import-table tr[data-code="WHITEBOARD"] input');
  await click('.it-import-table tr[data-code="SOFA"] input');
  truly(await R(() => document.querySelector('[data-testid="it-import-go"]').textContent) === "Take 3 types", "three to take");
  await click('[data-testid="it-import-go"]');
  await until(() => !document.querySelector("dialog.it-import"), "the dialog closes");
  const after = await catalogue();
  const got = { planter: after.find((t) => t.code === "PLANTER"), board: after.find((t) => t.code === "WHITEBOARD"),
    sofa: after.find((t) => t.code === "SOFA") };
  truly(after.find((t) => t.code === drawn).plan?.lines?.length === 1, "its drawing taken");
  truly(got.planter?.shape === "box" && !got.board && got.sofa.color === "#5c6b73" && got.sofa.width === 2.2
    && after.length === ours.length + 1, `taken: ${JSON.stringify(got)} (${after.length} types)`);
  await until(() => document.querySelector('.it-row[data-code="PLANTER"]'), "listed");
  noErrors("types from a file");
});

test("a file that is not one of item types is refused, saying why", async () => {
  const file = join(folder, "notes.json");
  writeFileSync(file, JSON.stringify({ hello: "world" }));
  await click('[data-testid="it-import"]');
  await until(() => document.querySelector("dialog.it-import[open] input[type=file]"), "the dialog");
  const { root } = await page.send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await page.send("DOM.querySelector", { nodeId: root.nodeId, selector: "dialog.it-import input[type=file]" });
  await page.send("DOM.setFileInputFiles", { nodeId, files: [file] });
  const said = await until(() => document.querySelector("dialog.it-import .form-error")?.textContent, "a refusal");
  truly(/no list of types/.test(said), `said: ${said}`);
  await click('dialog.it-import button[aria-label="Close"]');
  page.errors.length = 0; // (the refusal's 400, as the console tells it)
});

/** A binary glTF of a box ``w`` x ``h`` x ``d`` (glTF's x, y, z) in centimetres: red, its front (+Z) blue. */
function boxGlb(w, h, d) {
  const p = [];
  for (const x of [-w / 2, w / 2]) for (const y of [0, h]) for (const z of [-d / 2, d / 2]) p.push(x, y, z);
  const body = [0, 2, 3, 0, 3, 1, 4, 5, 7, 4, 7, 6, 0, 1, 5, 0, 5, 4, 2, 6, 7, 2, 7, 3, 0, 4, 6, 0, 6, 2];
  const front = [1, 3, 7, 1, 7, 5];
  const pos = Buffer.from(new Float32Array(p).buffer);
  const parts = [pos, Buffer.from(new Uint32Array(body).buffer), Buffer.from(new Uint32Array(front).buffer)];
  const bin = Buffer.concat(parts);
  const views = [], at = [0, pos.length, pos.length + parts[1].length];
  parts.forEach((b, i) => views.push({ buffer: 0, byteOffset: at[i], byteLength: b.length }));
  const json = Buffer.from(JSON.stringify({
    asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }, { attributes: { POSITION: 0 }, indices: 2, material: 1 }] }],
    materials: [[0.8, 0.05, 0.05], [0.05, 0.05, 0.8]].map((c) => ({ pbrMetallicRoughness: { baseColorFactor: [...c, 1], metallicFactor: 0 } })),
    buffers: [{ byteLength: bin.length }], bufferViews: views,
    accessors: [{ bufferView: 0, componentType: 5126, count: 8, type: "VEC3", min: [-w / 2, 0, -d / 2], max: [w / 2, h, d / 2] },
      { bufferView: 1, componentType: 5125, count: body.length, type: "SCALAR" },
      { bufferView: 2, componentType: 5125, count: front.length, type: "SCALAR" }],
  }));
  const text = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
  const head = Buffer.alloc(12), th = Buffer.alloc(8), bh = Buffer.alloc(8);
  head.write("glTF", 0);
  head.writeUInt32LE(2, 4);
  head.writeUInt32LE(12 + 8 + text.length + 8 + bin.length, 8);
  th.writeUInt32LE(text.length, 0);
  th.writeUInt32LE(0x4e4f534a, 4);
  bh.writeUInt32LE(bin.length, 0);
  bh.writeUInt32LE(0x004e4942, 4);
  return Buffer.concat([head, th, text, bh, bin]);
}

test("a type's 3D model: added from a .glb, pictured, the type given its size; turned; read in other units, doubted; stood up; removed", async () => {
  const file = join(folder, "armchair.glb");
  writeFileSync(file, boxGlb(80, 90, 70)); // (centimetres: read so by its size)
  await click('.it-row[data-code="SOFA"]');
  await until(() => [...document.querySelectorAll(".it-model button")].some((b) => /Add a model/.test(b.textContent)), "Add a model");
  truly(await R(() => /drawn as its shape/.test(document.querySelector(".it-model").textContent)), "none yet");
  const { root } = await page.send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await page.send("DOM.querySelector", { nodeId: root.nodeId, selector: ".it-model input[type=file]" });
  await page.send("DOM.setFileInputFiles", { nodeId, files: [file] });
  await until(() => [...document.querySelectorAll(".it-model-views img")].filter((i) => i.complete && i.naturalWidth > 0).length === 2,
    "its pictures, from the front and above", 30000);
  const said = await R(() => document.querySelector(".it-model").textContent);
  truly(/armchair\.glb/.test(said) && /12 triangles/.test(said) && /centimetres/.test(said), `what it is: ${said}`);
  const sofa = await typeOf("SOFA");
  truly(sofa.model === "models/SOFA.glb" && sofa.width === 0.8 && sofa.depth === 0.7 && sofa.height === 0.9, `the type: ${JSON.stringify(sofa)}`);
  truly(await R(() => document.querySelector('[data-testid="it-width"]').value) === "0.8", "the form shows its size");
  truly(await R(() => Boolean(document.querySelector('.it-row[data-code="SOFA"] .badge')?.parentElement.textContent.includes("3D"))), "3D in the list");
  await R(() => [...document.querySelectorAll(".it-model button")].find((b) => b.textContent === "Turn 90°").click());
  const turned = await soon(() => typeOf("SOFA"), (t) => t.width === 0.7);
  truly(turned.width === 0.7 && turned.depth === 0.8, `turned: ${JSON.stringify(turned)}`);
  await until(() => /turned 90°/.test(document.querySelector(".it-model")?.textContent || ""), "said turned");
  // read in metres (as a file 100 times too big would be): its size doubted; then as its size says again
  const units = (v) => R((value) => {
    const s = document.querySelector(".it-model-units select");
    s.value = value;
    s.dispatchEvent(new Event("change"));
  }, v);
  await units("m");
  await until(() => /taller than furniture is/.test(document.querySelector(".it-model-doubt")?.textContent || ""), "its size doubted");
  truly((await typeOf("SOFA")).height === 90 && await R(() => document.querySelector(".it-model-units select").value) === "m", "read in metres");
  await units("auto");
  await until(() => !document.querySelector(".it-model-doubt") && /\(as its size says\)/.test(document.querySelector(".it-model")?.textContent || ""),
    "as its size says again");
  // stood up (drawn Z up): its depth was its height
  await R(() => [...document.querySelectorAll(".it-model button")].find((b) => b.textContent === "Stand it up").click());
  const stood = await soon(() => typeOf("SOFA"), (t) => t.height === 0.7);
  truly(stood.width === 0.9 && stood.depth === 0.8 && stood.height === 0.7, `stood up: ${JSON.stringify(stood)}`);
  await until(() => [...document.querySelectorAll(".it-model button")].some((b) => b.textContent === "Lay it as it came"
    && b.getAttribute("aria-pressed") === "true"), "said stood up");
  await R(() => [...document.querySelectorAll(".it-model button")].find((b) => b.textContent === "Remove").click());
  await until(() => /drawn as its shape/.test(document.querySelector(".it-model")?.textContent || ""), "removed");
  truly((await typeOf("SOFA")).model === null, "no model");
  noErrors("a type's 3D model");
});

test("a project's packages: their item types, those its items use or every one", async () => {
  await R((c) => (location.hash = `#/p/${c}`), code); // (the same page: its address's hash alone changes)
  const options = await until(() => {
    const s = document.querySelector('[data-testid="export-item-types"]');
    return s && [...s.options].map((o) => o.value);
  }, "the choice beside Export package", 30000);
  truly(options.join() === "used,all", `the choice: ${options}`);
});

test("the project file downloaded encrypted: its password asked for twice; taken from on Item types, asked for, the wrong one said so", async () => {
  // (a download: caught, not saved)
  await R(() => {
    window.__saved = [];
    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      if (this.download && this.href.startsWith("blob:")) return window.__saved.push({ name: this.download, href: this.href });
      return click.call(this);
    };
  });
  const type = (testid, value) => R(([t, v]) => {
    const i = document.querySelector(`dialog.seal [data-testid="${t}"]`);
    i.value = v;
    i.dispatchEvent(new Event("input", { bubbles: true }));
  }, [testid, value]);
  await click('[data-testid="project-sealed"]');
  await until(() => document.querySelector("dialog.seal[open] [data-testid=seal-password-again]"), "a password, twice");
  await type("seal-password", "correct horse battery");
  await type("seal-password-again", "correct horse batterY");
  await click('dialog.seal [data-testid="seal-ok"]');
  await until(() => /not the same/.test(document.querySelector("dialog.seal .seal-said")?.textContent || ""), "said: not the same");
  await type("seal-password-again", "correct horse battery");
  await click('dialog.seal [data-testid="seal-ok"]');
  const saved = await until(() => window.__saved?.[0], "downloaded", 30000);
  const head = await R(async (href) => {
    const blob = await (await fetch(href)).blob();
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return { magic: bin.slice(0, 8), b64: btoa(bin) };
  }, saved.href);
  truly(saved.name === `${code}.storeypath-project.enc` && head.magic === "SPSEALED", `downloaded: ${saved.name} ${head.magic}`);
  const file = join(folder, saved.name);
  writeFileSync(file, Buffer.from(head.b64, "base64"));

  // the item types it brings, from the file: its password asked for
  await R(() => (location.hash = "#/item-types"));
  await until(() => document.querySelector('[data-testid="it-import"]'), "Item types");
  await click('[data-testid="it-import"]');
  await until(() => document.querySelector("dialog.it-import[open] input[type=file]"), "the dialog");
  const { root } = await page.send("DOM.getDocument", { depth: -1 });
  const { nodeId } = await page.send("DOM.querySelector", { nodeId: root.nodeId, selector: "dialog.it-import input[type=file]" });
  await page.send("DOM.setFileInputFiles", { nodeId, files: [file] });
  await until(() => document.querySelector("dialog.seal[open] [data-testid=seal-password]")
    && !document.querySelector("dialog.seal [data-testid=seal-password-again]"), "its password asked for, once", 20000);
  await type("seal-password", "wrong horse battery");
  await click('dialog.seal [data-testid="seal-ok"]');
  await until(() => /not this file's/.test(document.querySelector("dialog.seal[open] .seal-said")?.textContent || ""), "the wrong one said so", 20000);
  await type("seal-password", "correct horse battery");
  await click('dialog.seal [data-testid="seal-ok"]');
  await until(() => document.querySelector(".it-import-table tbody tr"), "what the file brings", 20000);
  await click('dialog.it-import button[aria-label="Close"]');
  page.errors.length = 0; // (the 400s that asked for its password, as the console tells them)
});

let failed = 0;
try {
  for (const { name, fn } of tests) {
    try {
      await fn();
      console.log(`ok    ${name}`);
    } catch (e) {
      failed++;
      console.log(`FAIL  ${name}\n      ${e.message}`);
      break;
    }
  }
} finally {
  page.close();
}
console.log(failed ? `${failed} failed` : `all ${tests.length} passed`);
process.exit(failed ? 1 : 0);
