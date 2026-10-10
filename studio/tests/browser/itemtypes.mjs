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
  truly(got.groups.join() === "Furniture,Equipment,Appliances", `by category: ${got.groups}`);
  truly(/2\.4 × 1\.2 × 0\.75 m/.test(got.table) && /Meeting table/.test(got.table) && got.chairs === 8, `a table for 8: ${JSON.stringify(got)}`);
  noErrors("the list");
});

test("a search finds a type by its name or code; a category shows its own", async () => {
  await type('input[type="search"]', "kiosk");
  await until(() => document.querySelectorAll(".it-row").length === 1, "one type found");
  truly(await R(() => document.querySelector(".it-row").dataset.code) === "KIOSK", "the kiosk");
  await set('input[type="search"]', "");
  await click('.it-filters .segmented [data-value="appliance"]');
  const codes = await R(() => [...document.querySelectorAll(".it-row")].map((r) => r.dataset.code));
  truly(codes.join() === "TV", `appliances: ${codes}`);
  await click('.it-filters .segmented [data-value="all"]');
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
    ...ours.filter((t) => t.code.startsWith("DESK-")),
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
  // the whiteboard left out, the sofa's change taken
  await click('.it-import-table tr[data-code="WHITEBOARD"] input');
  await click('.it-import-table tr[data-code="SOFA"] input');
  truly(await R(() => document.querySelector('[data-testid="it-import-go"]').textContent) === "Take 2 types", "two to take");
  await click('[data-testid="it-import-go"]');
  await until(() => !document.querySelector("dialog.it-import"), "the dialog closes");
  const after = await catalogue();
  const got = { planter: after.find((t) => t.code === "PLANTER"), board: after.find((t) => t.code === "WHITEBOARD"),
    sofa: after.find((t) => t.code === "SOFA") };
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

test("a project's packages: their item types, those its items use or every one", async () => {
  await R((c) => (location.hash = `#/p/${c}`), code); // (the same page: its address's hash alone changes)
  const options = await until(() => {
    const s = document.querySelector('[data-testid="export-item-types"]');
    return s && [...s.options].map((o) => o.value);
  }, "the choice beside Export package", 30000);
  truly(options.join() === "used,all", `the choice: ${options}`);
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
