// node world.mjs <Studio's address> <project code>: Studio's 3D page (world.html) in headless
// Chrome, used as a person uses it (the viewers' harness: real clicks and keys over the
// DevTools protocol), on the demo campus that `storeypath review` serves
// (tests/test_review_browser.py starts it). Read only: nothing here changes the project.
// The run stops at the first failure and Chrome quits with it.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { launch } = await import(pathToFileURL(join(here, "../../../storeypath-viewer/viewer/svg/test/harness.mjs")).href);

const [base, code] = process.argv.slice(2);
const BUILDING = `${code}-CAMPUS-MAIN`;
const PKG = `/api/projects/${code}/preview.storeypath?building=${BUILDING}`;
const pageUrl = (more = "") => `${base}/world.html?${new URLSearchParams({ pkg: PKG, building: BUILDING })}${more}`;
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const truly = (v, what) => {
  if (!v) throw new Error(what);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const page = await launch({ webgl: true, timeout: 600 });
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

const CODES = { Escape: "Escape", PageUp: "PageUp", PageDown: "PageDown", "?": "Slash" };
const KEYS = { Escape: 27, PageUp: 33, PageDown: 34, "?": 191 };
/** A key pressed (as KeyboardEvent.key): a letter, a digit, Escape, PageUp, PageDown, ?. */
async function press(key) {
  const code = CODES[key] ?? (/\d/.test(key) ? `Digit${key}` : `Key${key.toUpperCase()}`);
  const keyCode = KEYS[key] ?? key.toUpperCase().charCodeAt(0);
  const text = key.length === 1 ? key : undefined;
  const modifiers = key === "?" ? 8 : 0;
  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode: keyCode, modifiers, ...(text ? { text } : {}) });
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: keyCode, modifiers });
  await sleep(80);
}

const pressed = (id) => R((id) => document.getElementById(id).getAttribute("aria-pressed") === "true", id);
const param = (name) => R((name) => new URLSearchParams(location.search).get(name), name);
const click = (selector) => R((s) => document.querySelector(s).click(), selector);
const noErrors = (what) => truly(!page.errors.length, `${what}: the console said ${JSON.stringify(page.errors)}`);

test("the page opens on the building, with no error: the top bar, the toolbar, the floors, the status bar", async () => {
  await page.open(pageUrl(), 1440, 900);
  await until(() => document.body.classList.contains("ready"), "the building is shown", 90000);
  const parts = await R(() => ({
    crumbs: [...document.querySelectorAll("#crumbs .crumb")].map((c) => c.textContent),
    toolbar: !document.getElementById("toolbar").hidden,
    floors: [...document.querySelectorAll("#floor-stack button")].map((b) => b.textContent),
    hint: document.getElementById("sb-hint").textContent,
    source: document.getElementById("sb-source").textContent,
    look: document.getElementById("sb-look").textContent,
    title: document.title,
    mode: document.querySelector("#mode [aria-checked='true']").dataset.mode,
  }));
  truly(parts.crumbs.join(" / ") === "Projects / Demo Campus / Main Building", `the crumbs: ${parts.crumbs}`);
  truly(parts.toolbar && parts.mode === "dollhouse", JSON.stringify(parts));
  truly(parts.floors.join() === "All,F02,F01,F00", `the floor stack: ${parts.floors}`);
  truly(/Dollhouse/.test(parts.hint) && /3 floors · As it is now/.test(parts.source) && /Real/.test(parts.look), JSON.stringify(parts));
  truly(parts.title.startsWith("Main Building · Demo Campus"), parts.title);
  truly(await R(() => document.querySelector("#crumb-project").getAttribute("href").endsWith("/#/p/CAMP05")), "the project crumb links to its page");
  noErrors("opening");
});

test("every button and field has a name a screen reader says", async () => {
  const nameless = await R(() => [...document.querySelectorAll("button, a[href], input, select")]
    .filter((e) => e.offsetParent !== null)
    .filter((e) => !(e.getAttribute("aria-label") || e.textContent.trim() || e.getAttribute("title") || e.labels?.length
      || e.closest("label") || e.getAttribute("aria-labelledby")))
    .map((e) => e.outerHTML.slice(0, 120)));
  truly(!nameless.length, `without a name: ${nameless.join("\n")}`);
});

test("the floor stack: a floor alone, All again; PgUp and PgDn go up and down", async () => {
  await click("#floor-stack button[data-id$='-F01']");
  truly(await R(() => window.storeypathWorld.floor?.endsWith("-F01")), "F01 shown");
  truly((await param("floor"))?.endsWith("-F01"), "the floor kept in the address");
  await press("PageUp");
  truly(await R(() => window.storeypathWorld.floor?.endsWith("-F02")), "PgUp: F02");
  await press("PageDown");
  await press("PageDown");
  truly(await R(() => window.storeypathWorld.floor?.endsWith("-F00")), "PgDn twice: F00");
  truly(await R(() => document.querySelector("#floor-stack [aria-current='true']").textContent === "F00"), "F00 marked");
  await click("#floor-stack .fs-all");
  truly(await R(() => window.storeypathWorld.floor === null), "All: every floor");
  truly((await param("floor")) === null, "no floor in the address");
});

test("X-ray, Cutaway, Labels, Items, Hidden: each by its button and its key, kept in the address", async () => {
  await click("#xray");
  truly(await pressed("xray") && (await param("xray")) === "1", "X-ray on");
  await press("x");
  truly(!(await pressed("xray")) && (await param("xray")) === null, "X: off again");
  await press("c");
  truly(await pressed("cutaway") && (await param("cutaway")) === "1", "C: the cutaway");
  await click("#cutaway");
  truly(!(await pressed("cutaway")), "off again");
  // (the labels are placed with the next frame drawn)
  const shown = () => [...document.querySelectorAll(".sp3d-label")].some((l) => l.offsetParent !== null && getComputedStyle(l).display !== "none");
  await until(shown, "labels are shown");
  await press("l");
  truly(!(await pressed("labels")), "L: the button says labels are off");
  await until(new Function(`return !(${shown})()`), "L: labels hidden");
  await click("#labels");
  truly(await pressed("labels"), "on again");
  await until(shown, "labels back");
  const items = await R(() => window.storeypathWorld.items);
  await press("i");
  truly((await R(() => window.storeypathWorld.items)) === !items && (await pressed("items")) === !items, "I: items the other way");
  await click("#items");
  truly((await R(() => window.storeypathWorld.items)) === items, "and back");
  await click("#hidden-spaces");
  truly(await pressed("hidden-spaces") && (await param("hidden")) === "1", "hidden spaces shown");
  await click("#hidden-spaces");
  truly(!(await pressed("hidden-spaces")), "and not");
  noErrors("the toggles");
});

test("Explode lifts the floors apart, every floor shown; a floor alone puts them back", async () => {
  await click("#floor-stack button[data-id$='-F00']");
  await R(() => {
    const r = document.getElementById("explode");
    r.value = "4";
    r.dispatchEvent(new Event("input", { bubbles: true }));
  });
  truly(await R(() => window.storeypathWorld.floor === null), "every floor shown");
  truly((await param("explode")) === "4" && await R(() => document.getElementById("explode-box").classList.contains("on")), "kept, marked");
  await click("#floor-stack button[data-id$='-F02']");
  truly(await R(() => document.getElementById("explode").value === "0") && (await param("explode")) === null, "back together");
  await click("#floor-stack .fs-all");
});

test("Look and Quality: Real or Model, Auto, High or Low, remembered", async () => {
  await click("#look [data-style='model']");
  truly(await R(() => window.storeypathWorld.look.style === "model" && localStorage.getItem("storeypath.world.style") === "model"), "Model");
  truly(/Model/.test(await R(() => document.getElementById("sb-look").textContent)), "the status bar says so");
  await click("#look [data-style='real']");
  await R(() => {
    const q = document.getElementById("quality");
    q.value = "low";
    q.dispatchEvent(new Event("change"));
  });
  truly(await R(() => window.storeypathWorld.look.quality === "low" && window.storeypathWorld.look.drawn === "low"), "Low");
  truly((await param("quality")) === "low", "kept in the address");
  await R(() => {
    const q = document.getElementById("quality");
    q.value = "auto";
    q.dispatchEvent(new Event("change"));
  });
  truly(await R(() => window.storeypathWorld.look.style === "real" && window.storeypathWorld.look.quality === "auto"), "back to Real, Auto");
  noErrors("the look");
});

test("a room chosen shows its details: its ID, floor, area, finishes, where its doors lead; Esc closes them", async () => {
  await R(() => {
    const w = window.storeypathWorld;
    w.setFloor(w.package.spaces.find((s) => s.properties.name === "RECEPTION").properties.floor_id);
    w.select(w.package.spaces.find((s) => s.properties.name === "RECEPTION").id);
  });
  await until(() => !document.getElementById("details").hidden, "the details");
  const card = await R(() => document.getElementById("details").textContent);
  truly(/RECEPTION/.test(card) && /CAMP05-CAMPUS-MAIN-F00-/.test(card) && /Marble, white/.test(card) && /Oak slats/.test(card), card);
  truly(/Outside/.test(card) && /CORRIDOR/.test(card), `its doors lead: ${card}`);
  truly(await R(() => /review\.html\?p=CAMP05#floor=.*space=/.test(document.querySelector("#details a.button").getAttribute("href"))),
    "a way to it in Review");
  await press("Escape");
  truly(await R(() => document.getElementById("details").hidden && window.storeypathWorld.selected === null), "Esc closes it");
  // an item: its tag and its type
  await R(() => {
    const w = window.storeypathWorld;
    w.select(w.package.items.find((i) => i.properties.type === "KIOSK").id);
  });
  await until(() => /Wayfinding kiosk/.test(document.getElementById("details").textContent), "the kiosk's details");
  truly(/[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{3}/.test(await R(() => document.getElementById("details").textContent)), "its tag");
  await press("Escape");
  noErrors("the details");
});

test("Walk: 4 walks in, the room you are in, the map; a door at the cross said in the status bar and E opens it", async () => {
  await press("4");
  await until(() => window.storeypathWorld.mode === "walk" && document.body.classList.contains("walking"), "walking");
  const walk = await R(() => ({ hud: !document.getElementById("walk-hud").hidden, map: !document.getElementById("minimap").hidden,
    enter: !document.getElementById("walk-enter").hidden, cutaway: getComputedStyle(document.getElementById("cutaway")).display,
    doors: getComputedStyle(document.getElementById("doors")).display, all: Boolean(document.querySelector("#floor-stack .fs-all")),
    hint: document.getElementById("sb-hint").textContent }));
  truly(walk.hud && walk.map && walk.enter && walk.cutaway === "none" && walk.doors !== "none" && !walk.all, JSON.stringify(walk));
  truly(/Walk/.test(walk.hint) && (await param("mode")) === "walk", walk.hint);
  await press("m");
  truly(await R(() => document.getElementById("minimap").hidden) && !(await pressed("map")), "M hides the map");
  await press("m");
  // stand before a door of the ground floor, facing it
  const door = await R(() => {
    const w = window.storeypathWorld, floor = w.package.floorsOf(w.building)[0].id;
    const d = w.plan(floor).doors.find((x) => Math.hypot(x.span[1][0] - x.span[0][0], x.span[1][1] - x.span[0][1]) < 1.2);
    const [[ax, az], [bx, bz]] = d.span, mx = (ax + bx) / 2, mz = (az + bz) / 2;
    const len = Math.hypot(bx - ax, bz - az), nx = -(bz - az) / len, nz = (bx - ax) / len;
    w.setMode("dollhouse");
    w.setMode("walk", { floor, at: { x: mx + nx * 1.1, z: mz + nz * 1.1 }, heading: Math.atan2(nx, nz) });
    return { id: d.id, open: d.open };
  });
  await until(() => !document.getElementById("sb-door").hidden, "the door ahead, said", 10000);
  truly(/Door ahead/.test(await R(() => document.getElementById("sb-door").textContent)), "the status bar says it");
  await press("e");
  await until((id, was) => window.storeypathWorld.doorOpen(id) === !was, "E opened or shut it", 5000, door.id, door.open);
  await press("3");
  await until(() => window.storeypathWorld.mode === "dollhouse" && !document.body.classList.contains("walking"), "3: the dollhouse");
  truly((await param("mode")) === null, "the address says so");
  noErrors("walking");
});

test("Auto doors: off, a door opens only with E or a click; remembered as Review remembers it", async () => {
  await press("4");
  await until(() => window.storeypathWorld.mode === "walk", "walking");
  await click("#doors");
  truly(await R(() => window.storeypathWorld.doors === "manual" && localStorage.getItem("storeypath.world.doors") === "manual"), "by hand");
  await click("#doors");
  truly(await R(() => window.storeypathWorld.doors === "auto"), "as you walk into them");
  await press("3");
});

test("presenting: P hides all but the building; T stops its turning; Esc comes back", async () => {
  await press("p");
  const on = await R(() => ({ presenting: document.body.classList.contains("presenting"),
    topbar: getComputedStyle(document.getElementById("topbar")).visibility, toolbar: getComputedStyle(document.getElementById("toolbar")).display,
    stack: getComputedStyle(document.getElementById("floor-stack")).display, status: getComputedStyle(document.getElementById("statusbar")).visibility,
    hint: !document.getElementById("present-hint").hidden, turning: window.storeypathWorldPage.state.turning }));
  truly(on.presenting && on.topbar === "hidden" && on.toolbar === "none" && on.stack === "none" && on.status === "hidden" && on.hint, JSON.stringify(on));
  truly(on.turning, "the building turns");
  const before = await R(() => ({ x: window.storeypathWorld.camera.position.x, z: window.storeypathWorld.camera.position.z }));
  await sleep(600);
  const after = await R(() => ({ x: window.storeypathWorld.camera.position.x, z: window.storeypathWorld.camera.position.z }));
  truly(Math.hypot(after.x - before.x, after.z - before.z) > 0.01, "it turned");
  await press("t");
  truly(!(await R(() => window.storeypathWorldPage.state.turning)), "T stops it");
  await press("Escape");
  truly(await R(() => !document.body.classList.contains("presenting") && getComputedStyle(document.getElementById("topbar")).visibility === "visible"),
    "Esc: back");
  noErrors("presenting");
});

test("the keys: ? lists them, Esc closes the list", async () => {
  await press("?");
  await until(() => document.getElementById("keys-dialog").open, "the keys' list");
  truly(await R(() => document.querySelectorAll("#keys-list .k").length >= 12), "the keys listed");
  await press("Escape");
  await until(() => !document.getElementById("keys-dialog").open, "closed");
});

test("the light theme: the top bar, the toolbar and the status bar light, remembered", async () => {
  const dark = await R(() => getComputedStyle(document.getElementById("toolbar")).backgroundColor);
  await click("#theme-toggle");
  const light = await R(() => ({ theme: document.documentElement.dataset.theme, kept: localStorage.getItem("storeypath.theme"),
    toolbar: getComputedStyle(document.getElementById("toolbar")).backgroundColor,
    topbar: getComputedStyle(document.getElementById("topbar")).backgroundColor }));
  truly(light.theme === "light" && light.kept === "light" && light.toolbar === "rgb(255, 255, 255)" && light.toolbar !== dark
    && light.topbar === "rgb(255, 255, 255)", JSON.stringify({ dark, light }));
  await click("#theme-toggle");
  truly(await R(() => document.documentElement.dataset.theme === "dark"), "back to dark");
});

test("opened again with what its address says: walking, x-ray, Model", async () => {
  await page.open(pageUrl("&mode=walk&xray=1&style=model&floor=" + encodeURIComponent(`${BUILDING}-F01`)), 1280, 800);
  await until(() => document.body.classList.contains("ready"), "shown again", 90000);
  truly(await R(() => window.storeypathWorld.mode === "walk" && window.storeypathWorld.look.style === "model"), "walking, Model");
  truly(await pressed("xray"), "x-ray on");
  await R(() => localStorage.setItem("storeypath.world.style", "real"));
  noErrors("opening again");
});

test("a package that is not Studio's own is not fetched; no package: what to do", async () => {
  await page.open(`${base}/world.html?pkg=${encodeURIComponent("https://example.invalid/x.storeypath")}`, 1000, 700);
  await until(() => !document.getElementById("problem").hidden, "said");
  truly(/Nothing to show/.test(await R(() => document.getElementById("problem").textContent)), "nothing to show");
  truly(await R(() => !performance.getEntriesByType("resource").some((r) => r.name.includes("example.invalid"))), "nothing fetched from it");
  await page.open(`${base}/world.html?pkg=${encodeURIComponent(`/api/projects/NOPE00/preview.storeypath`)}`, 1000, 700);
  await until(() => !document.getElementById("problem").hidden, "said");
  truly(/Not found/.test(await R(() => document.getElementById("problem").textContent)), "a project that is not here");
  page.errors.length = 0; // (the 404 is said in the console by the browser: it is the page's to say)
});

test("Review's Share menu and its floor's 3D window open this page, on the floor's building", async () => {
  await page.open(`${base}/review.html?p=${code}`, 1440, 900);
  await until(() => document.body.classList.contains("ready") && window.storeypathReview.state.floor, "Review", 60000);
  const opened = await R(() => {
    const got = [];
    window.open = (url) => got.push(url);
    window.storeypathReview.run("share.window");
    return got;
  });
  truly(opened.length === 1 && opened[0].startsWith("/world.html?"), `opened ${opened}`);
  const q = new URLSearchParams(opened[0].split("?")[1]);
  truly(q.get("pkg").startsWith(`/api/projects/${code}/preview.storeypath?building=`) && q.get("floor"), opened[0]);
  page.errors.length = 0;
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
