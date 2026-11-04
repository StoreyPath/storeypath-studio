// node courtyards.mjs <Studio's address> <project code> <floor ID> <courtyard's ID> <x,y>: Review
// on a floor whose courtyard and light well are open to the sky (tests/test_courtyards.py's
// ring of offices, which tests/test_review_browser.py serves), used as a person uses it:
// the plan's outline open where they are; the courtyard, set aside (deleted, not shown),
// chosen by a click where it is (x,y: its middle, plan metres) and in 3D; said to be a room,
// open to the sky again, set aside as part of the floor from the menu, and undone, the
// floor's outline and its 3D following each.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { launch } = await import(pathToFileURL(join(here, "../../../storeypath-viewer/viewer/svg/test/harness.mjs")).href);

const [base, code, floorId, yardId, middle] = process.argv.slice(2);
const [mx, my] = middle.split(",").map(Number);
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const truly = (v, what) => {
  if (!v) throw new Error(what);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const page = await launch({ webgl: true, timeout: 600 });
const R = (fn, ...args) => page.run(fn, ...args);

async function until(fn, what, ms = 15000, ...args) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) {
    last = await R(fn, ...args);
    if (last) return last;
    await sleep(100);
  }
  throw new Error(`${what} (waited ${ms} ms; last: ${JSON.stringify(last)})`);
}

let MOD = 4;
async function press(key, mods = []) {
  const m = (mods.includes("mod") ? MOD : 0) | (mods.includes("shift") ? 8 : 0);
  const code_ = key.length === 1 ? `Key${key.toUpperCase()}` : key;
  const keyCode = key === "Escape" ? 27 : key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0;
  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key, code: code_, windowsVirtualKeyCode: keyCode, modifiers: m });
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key, code: code_, windowsVirtualKeyCode: keyCode, modifiers: m });
  await sleep(60);
}

/** Where a point of the plan (metres) is on the screen. */
const screenAt = (x, y) => R((x, y) => {
  const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
  return [b.left + x * v.k + v.tx, b.top - y * v.k + v.ty];
}, x, y);

/** How many rings the plan's outline has: its own, and a hole for each courtyard or light well. */
const rings = () => R(() => (document.getElementById("outline").getAttribute("d").match(/M/g) || []).length);
const yard = () => R((id) => {
  const s = window.storeypathReview.state.byId.get(id);
  return { ignored: s.ignored, sky: s.sky, shown: window.storeypathReview.state.paths.get(id).style.display !== "none" };
}, yardId);
const chosen = () => R(() => window.storeypathReview.state.selected);
const what = () => R(() => document.querySelector('#inspector [data-section="room.what"] [role=radio][aria-checked="true"]')?.dataset.what ?? null);
const clickWhat = (w) => R((w) => document.querySelector(`#inspector [data-section="room.what"] [data-what="${w}"]`).click(), w);
const noErrors = (when) => truly(!page.errors.length, `${when}: the console said ${JSON.stringify(page.errors)}`);

test("the floor opens with its outline open where its courtyard and light well are", async () => {
  await page.open(`${base}/review.html?p=${code}#floor=${encodeURIComponent(floorId)}`, 1440, 900);
  MOD = (await R(() => /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent))) ? 4 : 2;
  await until((id) => document.body.classList.contains("ready") && window.storeypathReview.state.floor?.id === id, "the floor is shown",
    30000, floorId);
  truly(await rings() === 3, `the outline's rings: ${await rings()}`);
  const y = await yard();
  truly(y.ignored && y.sky && !y.shown, `the courtyard: set aside, open to the sky, not drawn (${JSON.stringify(y)})`);
  noErrors("opening");
});

test("a click where the courtyard is chooses it: the inspector says it is open to the sky", async () => {
  await page.click(...await screenAt(mx, my));
  await until((id) => window.storeypathReview.state.selected === id, "the courtyard is chosen", 5000, yardId);
  truly((await yard()).shown, "chosen, it is drawn");
  await until(() => document.querySelector('#inspector [data-section="room.what"]'), "the inspector says what it is");
  truly(await what() === "sky", `what it is: ${await what()}`);
  noErrors("choosing it");
});

test("said to be a room, it is one: drawn, the outline whole there; open to the sky again, a hole again", async () => {
  await clickWhat("room");
  await until(() => window.storeypathReview.state.byId.get(window.storeypathReview.state.selected)?.ignored === false, "a room", 10000);
  truly(await rings() === 2 && (await yard()).shown && !(await yard()).sky, `a room: ${await rings()} rings, ${JSON.stringify(await yard())}`);
  truly(await chosen() === yardId, "still chosen");
  await until(() => document.querySelector('#inspector [data-section="room.what"] [data-what="sky"]'), "it may be said open to the sky");
  await clickWhat("sky");
  await until((id) => window.storeypathReview.state.byId.get(id).sky, "open to the sky", 10000, yardId);
  truly(await rings() === 3 && (await yard()).ignored, `open to the sky: ${await rings()} rings`);
  truly(await chosen() === yardId, "still chosen, to see it");
  noErrors("saying what it is");
});

test("the plan's menu sets it aside as part of the floor; undone, open to the sky again", async () => {
  const [x, y] = await screenAt(mx, my);
  await page.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "right", buttons: 2, clickCount: 1 });
  await page.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "right", buttons: 0, clickCount: 1 });
  await until(() => [...document.querySelectorAll("#menu .menu-item")].some((b) => /part of the floor/.test(b.textContent)),
    "the menu offers to set it aside as part of the floor");
  await R(() => [...document.querySelectorAll("#menu .menu-item")].find((b) => /part of the floor/.test(b.textContent)).click());
  await until((id) => { const s = window.storeypathReview.state.byId.get(id); return s.ignored && !s.sky; }, "set aside, not open", 10000, yardId);
  truly(await rings() === 2, `set aside: ${await rings()} rings`);
  await press("z", ["mod"]);
  await until((id) => window.storeypathReview.state.byId.get(id).sky, "undone: open to the sky", 20000, yardId);
  truly(await rings() === 3, `undone: ${await rings()} rings`);
  noErrors("the menu and undo");
});

test("in 3D the floor is open there, and a click on this floor where no room is shown chooses what is set aside there", async () => {
  await press("Escape");
  await press("3");
  await until(() => window.storeypathReview.view3d.building && window.storeypathReview.view3d.mode === "3d" && !window.storeypathReview.view3d.busy,
    "the 3D view is built", 90000);
  const rings3d = await until((id) => window.storeypathReview.view3d.world.package?.get(id)?.geometry?.coordinates?.length, "the floor in 3D",
    20000, floorId);
  truly(rings3d === 3, `the floor in 3D: ${rings3d} rings (its outline, two holes)`);
  const outdoor = await R((id) => window.storeypathReview.view3d.world.package.get(id).properties.outdoor, yardId);
  truly(outdoor === true, "the courtyard is outdoors in 3D");
  const picked = await R((args) => { // (the world's pick, as a click there makes it: no room shown at the point)
    const [floor, x, y] = args, w = window.storeypathReview.view3d.world;
    const e = new CustomEvent("pick", { cancelable: true, detail: { floor, x: 0, z: 0, local: [x, y], space: null, room: null, item: null,
      wall: false, door: null, button: 0 } });
    w.dispatchEvent(e);
    return { prevented: e.defaultPrevented, chosen: window.storeypathReview.state.selected };
  }, [floorId, mx, my]);
  truly(picked.prevented && picked.chosen === yardId, `chosen in 3D: ${JSON.stringify(picked)}`);
  await press("2");
  noErrors("3D");
});

let failed = 0;
try {
  for (const t of tests) {
    const started = Date.now();
    try {
      await t.fn();
      console.log(`ok   ${t.name} (${Date.now() - started} ms)`);
    } catch (e) {
      failed++;
      console.log(`FAIL ${t.name}\n     ${e.message.split("\n").join("\n     ")}`);
      if (page.errors.length) console.log(`     the console: ${JSON.stringify(page.errors)}`);
      break;
    }
  }
} finally {
  page.close();
}
console.log(failed ? `${failed} failed` : `${tests.length} passed`);
process.exit(failed ? 1 : 0);
