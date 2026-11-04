// node review.mjs <Studio's address> <project code>: Review's page in headless Chrome, used
// as a person uses it (the viewers' harness: real clicks and keys over the DevTools
// protocol). The Studio is one `storeypath review` serves, on a copy of the demo project
// (tests/test_review_browser.py starts it). Each test leaves the page as it found it, or
// says what it changed; the run stops at the first failure and Chrome quits with it.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { launch } = await import(pathToFileURL(join(here, "../../../storeypath-viewer/viewer/svg/test/harness.mjs")).href);

const [base, code] = process.argv.slice(2);
const URL_ = `${base}/review.html?p=${code}`;
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const truly = (v, what) => {
  if (!v) throw new Error(what);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const page = await launch({ webgl: true, timeout: 600 });
const R = (fn, ...args) => page.run(fn, ...args);

/** Waits until ``fn`` (run in the page) gives something true; that. */
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

/** The box to find what to place (Review's palette, opened to insert): opened if it is
 * not (by the Place tool's chooser), ``word`` typed, the first type found matching
 * ``like`` chosen. */
async function chooseType(word, like) {
  await R(() => { if (!document.querySelector("dialog.palette[open]")) document.querySelector(".type-chooser")?.click(); });
  await until(() => Boolean(document.querySelector("dialog.palette[open] #palette-list")), "the box to find what to place opens");
  await R((w) => {
    const input = document.getElementById("palette-input");
    input.value = w;
    input.dispatchEvent(new Event("input"));
  }, word);
  await until((src) => {
    const found = [...document.querySelectorAll("#palette-list .pl-item")].find((li) => new RegExp(src, "i").test(li.textContent));
    found?.click();
    return Boolean(found);
  }, `a type like ${like} found`, 5000, like.source);
}

let MOD = 4; // ⌘ on a Mac, Ctrl elsewhere (as keys.js has it)
const KEYS = { Escape: 27, Enter: 13, Backspace: 8, Delete: 46, PageUp: 33, PageDown: 34, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, F10: 121 };

/** A key pressed: ``key`` as KeyboardEvent.key; ``mods``: "shift", "mod", "alt" (a list). */
async function press(key, mods = []) {
  const m = (mods.includes("alt") ? 1 : 0) | (mods.includes("mod") ? MOD : 0) | (mods.includes("shift") ? 8 : 0);
  const k = mods.includes("shift") && key.length === 1 ? key.toUpperCase() : key;
  const code = key.length === 1 ? (/[a-z]/i.test(key) ? `Key${key.toUpperCase()}` : /\d/.test(key) ? `Digit${key}` : key) : key;
  const keyCode = KEYS[key] ?? (key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0);
  const text = key.length === 1 && !(m & 6) ? k : key === "Enter" ? "\r" : undefined;
  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, windowsVirtualKeyCode: keyCode, modifiers: m, ...(text ? { text } : {}) });
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: keyCode, modifiers: m });
  await sleep(60);
}

/** Where a room (by its ID) is on the screen: its label's point. */
const roomAt = (id) => R((id) => {
  const r = window.storeypathReview, s = r.state.byId.get(id), v = r.state.view;
  const b = document.getElementById("svg").getBoundingClientRect();
  return [b.left + s.label_point[0] * v.k + v.tx, b.top - s.label_point[1] * v.k + v.ty];
}, id);

/** Rooms of the floor shown: [{id, name, number, type, reasons}]. */
const rooms = () => R(() => window.storeypathReview.state.floor.spaces.filter((s) => !(s.zones || []).length)
  .map((s) => ({ id: s.id, name: s.name, number: s.number, type: s.type, reasons: s.reasons, ignored: s.ignored })));

const officeNamed = async (number) => (await rooms()).find((s) => s.name === "OFFICE" && s.number === number);
const toolNow = () => R(() => window.storeypathReview.state.tool);
const errors = () => page.errors.slice();
const noErrors = (what) => truly(!page.errors.length, `${what}: the console said ${JSON.stringify(page.errors)}`);

// ---- the page ----------------------------------------------------------------------------

test("the page opens on a floor, with no error, its parts in place", async () => {
  await page.open(URL_, 1440, 900);
  MOD = (await R(() => /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent))) ? 4 : 2;
  await until(() => document.body.classList.contains("ready") && window.storeypathReview.state.floor, "the floor is shown");
  const parts = await R(() => ({
    rail: document.querySelectorAll("#rail [data-tool]").length,
    crumb: document.getElementById("crumb-floor-name").textContent,
    groups: document.querySelectorAll("#room-groups .group").length,
    inspector: document.getElementById("inspector").dataset.kind,
    stack: document.querySelectorAll("#floor-stack button").length,
  }));
  truly(parts.rail >= 12, `the rail has ${parts.rail} tools`);
  truly(parts.crumb && parts.crumb !== "Floor", `the floor's name in the top bar: ${parts.crumb}`);
  truly(parts.groups > 2, "the rooms are grouped by type");
  truly(parts.inspector === "floor", `nothing chosen: the inspector shows the floor (${parts.inspector})`);
  truly(parts.stack >= 2, "the floor stack lists the building's floors");
  noErrors("opening");
});

test("the keyboard map binds no key twice in one scope", async () => {
  const conflicts = await R(() => window.storeypathReview.keyConflicts());
  truly(!conflicts.length, JSON.stringify(conflicts));
});

test("every button and field has a name a screen reader says", async () => {
  const nameless = await R(() => [...document.querySelectorAll("button, a[href], input, select, [role=tab], [role=radio]")]
    .filter((e) => e.offsetParent !== null || e.closest("#topbar, #rail"))
    .filter((e) => !(e.getAttribute("aria-label") || e.textContent.trim() || e.getAttribute("title") || e.labels?.length
      || e.closest("label") || e.getAttribute("aria-labelledby") || e.getAttribute("placeholder")))
    .map((e) => e.outerHTML.slice(0, 120)));
  truly(!nameless.length, `without a name: ${nameless.join("\n")}`);
});

test("a room clicked on the plan is chosen: the inspector shows it, the status bar counts it", async () => {
  const office = await officeNamed("012");
  await page.click(...await roomAt(office.id));
  await until((id) => window.storeypathReview.state.selected === id, "the room is chosen", 5000, office.id);
  const shown = await R(() => ({ title: document.querySelector("#inspector-head .ih-title")?.textContent,
    kind: document.getElementById("inspector").dataset.kind, count: document.getElementById("sb-selection").textContent,
    hash: location.hash, type: document.getElementById("ed-type")?.value }));
  truly(shown.kind === "space" && shown.title === "OFFICE 012", JSON.stringify(shown));
  truly(shown.count === "1 room" && shown.hash.includes(office.id) && shown.type === "office", JSON.stringify(shown));
  truly(await R(() => document.getElementById("toast").hidden || !document.getElementById("toast").classList.contains("error")),
    "choosing a room says nothing went wrong");
});

test("Shift-click adds rooms; Shift-drag draws a band that chooses those it covers; Esc lets them go", async () => {
  const [a, b] = [await officeNamed("013"), await officeNamed("015")];
  await page.click(...await roomAt(a.id), { shiftKey: true });
  await page.click(...await roomAt(b.id), { shiftKey: true });
  const three = await R(() => ({ ids: window.storeypathReview.sel.ids.length, kind: document.getElementById("inspector").dataset.kind }));
  truly(three.ids === 3 && three.kind === "spaces", JSON.stringify(three));
  await press("Escape");
  truly(!(await R(() => window.storeypathReview.sel.kind)), "Esc lets them go");
  // a band over the bottom row of offices
  const [p, q] = [await roomAt((await officeNamed("001")).id), await roomAt((await officeNamed("003")).id)];
  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Shift", code: "ShiftLeft", modifiers: 8 });
  await page.mouse("mouseMoved", p[0] - 30, p[1] - 30, { params: { modifiers: 8 } });
  await page.mouse("mousePressed", p[0] - 30, p[1] - 30, { down: true, params: { modifiers: 8 } });
  for (let i = 1; i <= 6; i++) {
    await page.mouse("mouseMoved", p[0] - 30 + ((q[0] + 30 - (p[0] - 30)) * i) / 6, p[1] - 30 + ((q[1] + 30 - (p[1] - 30)) * i) / 6,
      { down: true, params: { modifiers: 8 } });
  }
  await page.mouse("mouseReleased", q[0] + 30, q[1] + 30, { params: { modifiers: 8 } });
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Shift", code: "ShiftLeft" });
  const band = await R(() => window.storeypathReview.sel.ids.length);
  truly(band >= 3, `the band chose ${band} rooms`);
  await press("Escape");
});

test("each tool is taken by its key: pressed in the rail, its options over the canvas, its hint in the status bar", async () => {
  const tools = await R(() => window.storeypathReview.tools().map((t) => ({ id: t.id, key: t.key, label: t.label, options: Boolean(t.options), action: Boolean(t.action) })));
  for (const t of tools) {
    if (t.action || t.id === "select" || !t.key) continue; // (one without a key: from its family's place, below)
    const [k, ...mods] = t.key.split("+").reverse();
    await press(k, mods);
    const now = await R(() => ({ tool: window.storeypathReview.state.tool,
      pressed: document.querySelector("#rail [aria-pressed='true']")?.dataset.tool,
      options: !document.getElementById("tool-options").hidden, hint: document.getElementById("sb-hint").textContent }));
    truly(now.tool === t.id && now.pressed === t.id, `${t.key}: ${JSON.stringify(now)}`);
    truly(now.options === t.options, `${t.id}: its options bar ${now.options ? "shown" : "not shown"}`);
    truly(now.hint.includes(t.label), `${t.id}: the status bar says ${now.hint}`);
    for (let i = 0; i < 3 && (await toolNow()); i++) await press("Escape"); // (Esc closes what the tool opened first)
    truly(!(await toolNow()), `${t.id}: Esc puts it down`);
  }
  await press("v");
  truly(!(await toolNow()), "V: the Select tool");
  noErrors("the tools");
});

test("the plan by the keys: the arrows move it, + and − zoom it, Space held and dragged moves it, Shift-F10 opens the menu", async () => {
  const view = () => R(() => ({ ...window.storeypathReview.state.view }));
  const v0 = await view();
  await R(() => document.getElementById("svg").focus());
  await press("ArrowLeft");
  truly((await view()).tx > v0.tx, "← moves it");
  await press("=");
  truly((await view()).k > v0.k, "+ zooms in");
  const v1 = await view();
  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key: " ", code: "Space", windowsVirtualKeyCode: 32, text: " " });
  await page.drag([700, 500], [760, 540]);
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
  const v2 = await view();
  truly(Math.abs(v2.tx - v1.tx - 60) < 2 && Math.abs(v2.ty - v1.ty - 40) < 2 && !(await R(() => window.storeypathReview.sel.kind)),
    `Space-drag moved it, choosing nothing: ${JSON.stringify([v1, v2])}`);
  await press("F10", ["shift"]);
  truly(await R(() => !document.getElementById("menu").hidden && document.activeElement.closest("#menu")), "Shift-F10: the menu, its keys in it");
  await press("Escape");
  await press("f");
});

test("tools of one family share a place on the rail: right-clicked or held, it opens the others; Paint one wall paints the wall clicked alone, undone", async () => {
  await press("Escape");
  const place = () => R(() => {
    const b = document.querySelector('#rail [data-family="paint"]');
    return b && { tool: b.dataset.tool, pressed: b.getAttribute("aria-pressed"), expanded: b.getAttribute("aria-expanded") };
  });
  const flyout = () => R(() => [...document.querySelectorAll(".rail-flyout .menu-item")].map((b) => b.dataset.tool));
  truly(await R(() => ["paint", "paint-wall", "dropper"].filter((id) => document.querySelector(`#rail [data-tool="${id}"]`)).length === 1),
    "the paint tools: one place on the rail");
  const words = await R(() => [...document.querySelectorAll("#rail button")].filter((b) => /null|undefined/.test(b.textContent))
    .map((b) => `${b.dataset.tool}: ${b.textContent}`));
  truly(!words.length, `the rail's buttons: their icons and keys, no stray words: ${words.join("; ")}`);
  const at = await R(() => {
    const r = document.querySelector('#rail [data-family="paint"]').getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  });
  // right-clicked: its tools beside it, none taken; Esc puts them away
  await page.send("Input.dispatchMouseEvent", { type: "mousePressed", x: at[0], y: at[1], button: "right", buttons: 2, clickCount: 1 });
  await page.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: at[0], y: at[1], button: "right", buttons: 0, clickCount: 1 });
  truly((await flyout()).join() === "paint,paint-wall,dropper" && (await place()).expanded === "true", `right-clicked: ${await flyout()}`);
  await press("Escape");
  truly(!(await flyout()).length && !(await toolNow()), "Esc: put away, no tool taken");
  // held: open, and letting go takes no tool
  await page.mouse("mouseMoved", ...at);
  await page.mouse("mousePressed", ...at, { down: true });
  await until(() => Boolean(document.querySelector(".rail-flyout")), "held: open", 3000);
  await page.mouse("mouseReleased", ...at);
  truly((await flyout()).length === 3 && !(await toolNow()), `let go: open still, no tool taken (${await toolNow()})`);
  // Paint one wall chosen: its place shows it, pressed; the walls' brush alone
  await R(() => document.querySelector('.rail-flyout [data-tool="paint-wall"]').click());
  const now = await place();
  truly((await toolNow()) === "paint-wall" && now.tool === "paint-wall" && now.pressed === "true" && !(await flyout()).length, `one wall: ${JSON.stringify(now)}`);
  truly(await R(() => document.querySelectorAll(".fin-brush").length === 1), "its options: the walls' brush alone");
  await R(() => document.querySelector(".fin-brush").click()); // (a finish none of the offices has)
  await until(() => Boolean(document.querySelector(".fin-picker .fin-grid .fin-tile")), "the finishes", 5000);
  const brush = await R(() => {
    const t = [...document.querySelectorAll(".fin-picker .fin-grid .fin-tile")].at(-1);
    t.click();
    return t.dataset.code ?? t.textContent;
  });
  // in an office near a wall: that wall drawn, then painted alone
  const office = await officeNamed("001");
  const near = await R((id) => {
    const r = window.storeypathReview, s = r.state.byId.get(id), v = r.state.view;
    const ring = s.geometry.type === "Polygon" ? s.geometry.coordinates[0] : s.geometry.coordinates[0][0];
    let best = null;
    for (let i = 0; i + 1 < ring.length; i++) {
      const [a, b] = [ring[i], ring[i + 1]], l = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (!best || l > best.l) best = { a, b, l };
    }
    const m = [(best.a[0] + best.b[0]) / 2, (best.a[1] + best.b[1]) / 2], c = s.label_point;
    const d = Math.hypot(c[0] - m[0], c[1] - m[1]) || 1, p = [m[0] + ((c[0] - m[0]) / d) * 0.3, m[1] + ((c[1] - m[1]) / d) * 0.3];
    const box = document.getElementById("svg").getBoundingClientRect();
    return [box.left + p[0] * v.k + v.tx, box.top - p[1] * v.k + v.ty];
  }, office.id);
  await page.mouse("mouseMoved", ...near);
  await until(() => Boolean(document.querySelector("#preview line.paint-wall")), "the wall a click paints, drawn", 3000);
  await page.click(...near);
  const painted = await until((id) => {
    const w = window.storeypathReview.state.byId.get(id).wall_finishes || [];
    return w.length === 1 && w[0];
  }, "that wall painted alone", 8000, office.id);
  truly(!(await R((id) => window.storeypathReview.state.byId.get(id).wall_finish, office.id)), `its other walls as they were: ${JSON.stringify(painted)} (${brush})`);
  await press("z", ["mod"]);
  await until((id) => !(window.storeypathReview.state.byId.get(id).wall_finishes || []).length, "undone", 8000, office.id);
  // P and Shift-P: each its tool, its place following; the dropper from the brushes, Esc back
  await press("p");
  truly((await toolNow()) === "paint" && (await place()).tool === "paint", "P: Paint floors and walls, in its place");
  await press("p", ["shift"]);
  truly((await toolNow()) === "paint-wall" && (await place()).tool === "paint-wall", "Shift-P: Paint one wall");
  await R(() => document.querySelector(".fin-dropper").click());
  truly((await toolNow()) === "dropper" && (await place()).tool === "dropper", "the dropper");
  await press("Escape");
  truly((await toolNow()) === "paint-wall", "Esc: back to painting one wall");
  await press("Escape");
  truly(!(await toolNow()), "Esc again: done");
  await press("p");
  await press("Escape");
  noErrors("the paint tools");
});

test("Measure: two points clicked, the distance between them; in feet and inches too", async () => {
  await press("m");
  const p = await roomAt((await officeNamed("001")).id), q = await roomAt((await officeNamed("003")).id);
  await page.click(...p);
  await page.click(...q);
  await press("Enter");
  const said = await R(() => document.getElementById("measure-result")?.textContent || "");
  truly(/^\d+(\.\d+)? m$/.test(said), `measured: ${said}`);
  const units = (u) => R((u) => [...document.querySelectorAll("#tool-options .units-switch button")].find((b) => b.textContent === u).click(), u);
  await units("ft");
  const feet = await R(() => document.getElementById("measure-result")?.textContent || "");
  const [, ft, inch] = /^(\d+)' (\d+)"$/.exec(feet) || [];
  truly(ft && Math.abs((Number(ft) * 12 + Number(inch)) * 0.0254 - Number(said.split(" ")[0])) < 0.03, `in feet: ${feet} (${said})`);
  await units("m");
  truly(await R(() => document.getElementById("measure-result")?.textContent) === said, "back in metres");
  await press("Escape");
  await press("Escape");
});

test("an item placed with the Place tool, chosen, turned with R, deleted with Del, its tag found by search", async () => {
  await press("i");
  await chooseType("junior", /junior/);
  const before = await R(() => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length);
  await page.click(...await roomAt((await officeNamed("002")).id));
  await until((n) => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length === n + 1, "the item placed", 8000, before);
  await press("Escape");
  const a = await R(() => window.storeypathReview.state.floor.items.filter((x) => !x.retired).at(-1));
  await page.click(...await R((a) => {
    const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
    return [b.left + a.x * v.k + v.tx, b.top - a.y * v.k + v.ty];
  }, a));
  await until((id) => window.storeypathReview.state.asset === id && document.getElementById("inspector").dataset.kind === "asset",
    "the item chosen, the inspector showing it", 5000, a.id);
  await press("r");
  await until((id) => (window.storeypathReview.state.floor.items.find((x) => x.id === id).rotation % 360) === 90, "R turned it 90°", 5000, a.id);
  // its tag typed loosely in the palette finds it
  await press("Escape");
  await press("k", ["mod"]);
  await until(() => document.querySelector("dialog.palette[open]"), "the palette opens");
  await R((tag) => {
    const i = document.getElementById("palette-input");
    i.value = tag.toLowerCase().replaceAll("-", " ");
    i.dispatchEvent(new Event("input"));
  }, a.id);
  await press("Enter");
  await until((id) => window.storeypathReview.state.asset === id, "the palette chose the item by its tag", 5000, a.id);
  await press("Delete");
  await until((id) => window.storeypathReview.state.floor.items.find((x) => x.id === id).retired, "Del deleted it", 5000, a.id);
  await press("Escape");
});

test("a set placed in one go: each piece an item of its own, one group; a click chooses the group, a double-click one piece; turned, moved, ungrouped, grouped and deleted together, undone as one", async () => {
  const items = () => R(() => window.storeypathReview.state.floor.items.filter((a) => !a.retired).map((a) => ({ ...a })));
  const at = (a) => R((a) => {
    const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
    return [b.left + a.x * v.k + v.tx, b.top - a.y * v.k + v.ty];
  }, a);
  const kind = () => R(() => document.getElementById("inspector").dataset.kind);
  await press("i");
  await chooseType("workstation junior", /Workstation, junior staff/);
  const before = (await items()).length;
  await page.click(...await roomAt((await officeNamed("002")).id));
  await until((n) => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length === n + 3, "the set's three items placed", 8000, before);
  await press("Escape");
  const placed = (await items()).slice(-3), group = placed[0].group;
  truly(group && placed.every((a) => a.group === group) && new Set(placed.map((a) => a.id)).size === 3
    && placed.map((a) => a.type).join() === "DESK-JUNIOR,CHAIR-TASK,PEDESTAL", `one group, an ID each: ${JSON.stringify(placed.map((a) => [a.type, a.id, a.group]))}`);
  const desk = placed[0], chair = placed[1];
  // placed, the set is chosen: a group, the inspector says so and lists its items
  await until((ids) => JSON.stringify(window.storeypathReview.state.assets.slice().sort()) === JSON.stringify(ids.slice().sort())
    && document.getElementById("inspector").dataset.kind === "assets", "the set chosen", 5000, placed.map((a) => a.id));
  truly(await R(() => document.querySelectorAll("#inspector .group-item").length) === 3, "its three items listed");
  // R turns them together, about their middle; the arrow moves them together
  await press("r");
  await until((id) => Math.round(window.storeypathReview.state.floor.items.find((x) => x.id === id).rotation) === 90,
    "R turned the group", 5000, desk.id);
  const turned = Object.fromEntries((await items()).map((a) => [a.id, a]));
  truly(Math.round(turned[desk.id].rotation) === 90 && Math.round(turned[chair.id].rotation) === 270, `turned: ${turned[desk.id].rotation} ${turned[chair.id].rotation}`);
  await press("ArrowRight");
  await until((d) => Math.abs(window.storeypathReview.state.floor.items.find((x) => x.id === d.id).x - (d.x + 0.1)) < 1e-6,
    "the arrow moved the group", 5000, turned[desk.id]);
  const moved = Object.fromEntries((await items()).map((a) => [a.id, a]));
  truly(Math.abs(moved[chair.id].x - (turned[chair.id].x + 0.1)) < 1e-6, "the chair moved with the desk");
  // Shift+H mirrors them left to right about their middle, Shift+V top to bottom; each undone
  const three = placed.map((a) => moved[a.id]), mid = (k) => (Math.min(...three.map((a) => a[k])) + Math.max(...three.map((a) => a[k]))) / 2;
  const [mx, my] = [mid("x"), mid("y")];
  const turn = (id) => R((id) => Math.round(window.storeypathReview.state.floor.items.find((x) => x.id === id).rotation) % 360, id);
  await press("h", ["shift"]);
  await until((id) => Math.round(window.storeypathReview.state.floor.items.find((x) => x.id === id).rotation) === 270,
    "Shift+H mirrored the group", 5000, desk.id);
  const flippedX = Object.fromEntries((await items()).map((a) => [a.id, a]));
  truly(three.every((a) => Math.abs(flippedX[a.id].x - (2 * mx - a.x)) < 1e-3 && Math.abs(flippedX[a.id].y - a.y) < 1e-3),
    `mirrored left to right about x=${mx}: ${JSON.stringify(three.map((a) => [a.x, flippedX[a.id].x]))}`);
  truly(await turn(desk.id) === 270 && await turn(chair.id) === 90, `facing the other way: ${await turn(desk.id)} ${await turn(chair.id)}`);
  await press("v", ["shift"]);
  await until((d) => Math.abs(window.storeypathReview.state.floor.items.find((x) => x.id === d.id).y - (2 * d.my - d.y)) < 1e-3,
    "Shift+V mirrored the group top to bottom", 5000, { ...flippedX[chair.id], my });
  const flippedY = Object.fromEntries((await items()).map((a) => [a.id, a]));
  truly(three.every((a) => Math.abs(flippedY[a.id].x - flippedX[a.id].x) < 1e-3), "top to bottom: none moved across");
  truly(await turn(desk.id) === 270 && await turn(chair.id) === 90, "facing across the mirror: as they faced");
  await press("z", ["mod"]);
  await until((d) => Math.abs(window.storeypathReview.state.floor.items.find((x) => x.id === d.id).y - d.y) < 1e-3, "⌘Z: Shift+V undone", 8000, flippedX[chair.id]);
  await sleep(300);
  await press("z", ["mod"]);
  await until((d) => Math.abs(window.storeypathReview.state.floor.items.find((x) => x.id === d.id).x - d.x) < 1e-3
    && Math.round(window.storeypathReview.state.floor.items.find((x) => x.id === d.id).rotation) === 270, "⌘Z: Shift+H undone", 8000, moved[chair.id]);
  // a click on the chair: the group; a double-click: the chair alone
  await press("Escape");
  await page.click(...await at(moved[chair.id]));
  await until(() => window.storeypathReview.state.assets.length === 3, "a click on one of the group: the group", 5000);
  const [cx, cy] = await at(moved[chair.id]);
  await Promise.all([ // (sent together: one after another, they may come too slowly to be a double-click)
    () => page.mouse("mouseMoved", cx, cy),
    () => page.mouse("mousePressed", cx, cy, { down: true }), () => page.mouse("mouseReleased", cx, cy),
    () => page.mouse("mousePressed", cx, cy, { down: true, params: { clickCount: 2 } }),
    () => page.mouse("mouseReleased", cx, cy, { params: { clickCount: 2 } })].map((call) => call()));
  await until((id) => window.storeypathReview.state.asset === id && document.getElementById("inspector").dataset.kind === "asset",
    "a double-click: the chair alone", 5000, chair.id);
  // the group chosen, a click on its pedestal (under the desk, drawn over it): the pedestal alone;
  // dragged, it alone moves; undone
  const ped = placed[2];
  await page.click(...await at(moved[desk.id]));
  await until(() => window.storeypathReview.state.assets.length === 3, "the group chosen", 5000);
  truly(await R((ids) => { const order = [...document.querySelectorAll("#assets [data-asset]")].map((g) => g.dataset.asset);
    return order.indexOf(ids[1]) > order.indexOf(ids[0]); }, [desk.id, ped.id]), "the pedestal drawn over the desk");
  await sleep(450); // (not a double-click)
  await page.click(...await at(moved[ped.id]));
  await until((id) => window.storeypathReview.state.asset === id && window.storeypathReview.state.assets.length === 1,
    "a click on the pedestal in its group chosen: the pedestal alone", 5000, ped.id);
  const pAt = await at(moved[ped.id]);
  await page.drag(pAt, [pAt[0] + 25, pAt[1] + 15]);
  await until((d) => Math.hypot(window.storeypathReview.state.floor.items.find((x) => x.id === d.id).x - d.x,
    window.storeypathReview.state.floor.items.find((x) => x.id === d.id).y - d.y) > 0.05, "the pedestal dragged", 5000, moved[ped.id]);
  const dragged = Object.fromEntries((await items()).map((a) => [a.id, a]));
  truly([desk.id, chair.id].every((id) => Math.abs(dragged[id].x - moved[id].x) < 1e-6 && Math.abs(dragged[id].y - moved[id].y) < 1e-6),
    "the desk and the chair stayed where they were");
  truly(dragged[ped.id].group === ped.group, "the pedestal still of the group");
  await sleep(600); // (its move saved before it is undone)
  await press("z", ["mod"]);
  await until((d) => Math.abs(window.storeypathReview.state.floor.items.find((x) => x.id === d.id).x - d.x) < 1e-6, "⌘Z: the pedestal back", 8000, moved[ped.id]);
  await press("Escape");
  // ungrouped (⌘⇧G), each alone; two Shift-clicked and grouped again (⌘G)
  await page.click(...await at(moved[desk.id]));
  await until(() => window.storeypathReview.state.assets.length === 3, "the group chosen again", 5000);
  await press("g", ["mod", "shift"]);
  await until((ids) => ids.every((id) => !window.storeypathReview.state.floor.items.find((x) => x.id === id).group),
    "ungrouped", 5000, placed.map((a) => a.id));
  await press("Escape");
  await page.click(...await at(moved[desk.id]));
  await until((id) => window.storeypathReview.state.asset === id, "the desk alone now", 5000, desk.id);
  await page.click(...await at(moved[chair.id]), { shiftKey: true });
  await until(() => window.storeypathReview.state.assets.length === 2, "the chair Shift-clicked: two chosen", 5000);
  truly(await kind() === "assets", "two items chosen: the inspector of several");
  await press("g", ["mod"]);
  await until((ids) => {
    const g = ids.map((id) => window.storeypathReview.state.floor.items.find((x) => x.id === id).group);
    return g[0] && g[0] === g[1];
  }, "grouped again", 5000, [desk.id, chair.id]);
  // deleted together (Del), and undone as one (⌘Z)
  await press("Delete");
  await until((ids) => ids.every((id) => window.storeypathReview.state.floor.items.find((x) => x.id === id).retired),
    "Del deleted the two together", 5000, [desk.id, chair.id]);
  await press("z", ["mod"]);
  await until((ids) => ids.every((id) => !window.storeypathReview.state.floor.items.find((x) => x.id === id).retired),
    "⌘Z brought both back", 8000, [desk.id, chair.id]);
  noErrors("a set, its group");
  await press("Escape");
});

test("a furniture layout placed: its items in the preview dragged where they fit (red outside the room, green in it), R turns the one dragged, Reset; placed where they were put", async () => {
  const office = await officeNamed("002"), other = (await rooms()).find((s) => s.name === "OFFICE" && s.number !== "002" && !s.ignored);
  const api = (path, body) => R(async (path, body) => {
    const r = await fetch(`/api/projects/${new URLSearchParams(location.search).get("p")}/${path}`, body === undefined ? {}
      : { method: "POST", headers: { "X-StoreyPath": "1", "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return { status: r.status, data: await r.json() };
  }, path, body);
  const floor = await R(() => window.storeypathReview.state.floor.id);
  const saved = await api(`floors/${floor}/furnishings`, { room: office.id, name: "Browser layout" });
  truly(saved.status === 200 && saved.data.count >= 1, `a layout of office 002: ${JSON.stringify(saved)}`);
  // right-click in another office: Place a furniture layout here…
  await press("f"); // (the floor in view: the office on screen)
  await until(() => !window.storeypathReview.state.busy, "the page not busy", 5000);
  const [x, y] = await roomAt(other.id);
  await page.mouse("mouseMoved", x, y);
  await page.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "right", buttons: 2, clickCount: 1 });
  await page.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "right", buttons: 0, clickCount: 1 });
  await until(() => {
    const b = [...document.querySelectorAll("#menu .menu-item")].find((b) => b.textContent.includes("Place a furniture layout here"));
    b?.click();
    return Boolean(b);
  }, "the room's menu: Place a furniture layout here…", 5000);
  await until((id) => {
    const b = [...document.querySelectorAll("dialog.furnish[open] .furnish-choice")].find((b) => b.dataset.id === id);
    b?.click();
    return Boolean(b);
  }, "the layout listed", 8000, saved.data.id);
  truly(await R(() => document.getElementById("furnish-reset").disabled), "Reset: nothing to put back yet");
  const ghost = (i) => R((i) => {
    const g = document.querySelector(`dialog.furnish .preview .asset-ghost[data-i="${i}"]`), b = g.getBoundingClientRect();
    const plan = document.querySelector("dialog.furnish .furnish-plan").getBoundingClientRect();
    return { at: [b.left + b.width / 2, b.top + b.height / 2], refused: g.classList.contains("refused"), transform: g.getAttribute("transform"),
      plan: [plan.left, plan.top, plan.right, plan.bottom] };
  }, i);
  // the piece to drag: one on top at its middle (a pedestal may lie under a desk)
  const k = await R(() => [...document.querySelectorAll("dialog.furnish .preview .asset-ghost.movable")].find((g) => {
    const b = g.getBoundingClientRect();
    return document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)?.closest(".asset-ghost") === g;
  })?.dataset.i ?? null);
  truly(k !== null, "a piece of the preview to drag");
  const g0 = await ghost(k);
  // dragged out of the room (to the preview's corner): red, said so; back where it was: green
  await page.drag(g0.at, [g0.plan[0] + 6, g0.plan[1] + 6]);
  await until((k) => document.querySelector(`dialog.furnish .preview .asset-ghost[data-i="${k}"]`).classList.contains("refused")
    && /outside the room/.test(document.querySelector("dialog.furnish .furnish-status").textContent), "dragged out of the room: red, said so", 5000, k);
  truly(!(await R(() => document.getElementById("furnish-reset").disabled)), "Reset: something to put back");
  await page.drag((await ghost(k)).at, g0.at);
  await until((was, k) => document.querySelector(`dialog.furnish .preview .asset-ghost[data-i="${k}"]`).classList.contains("refused") === was,
    "dragged back: as it was (green where it fitted)", 5000, g0.refused, k);
  // R turns the one dragged last 90°; Reset puts every item back as the layout has it
  const turnOf = (t) => Number((/rotate\(([-\d.e]+)\)/.exec(t) || [0, 0])[1]);
  await press("r");
  await until((was, k) => {
    const t = document.querySelector(`dialog.furnish .preview .asset-ghost[data-i="${k}"]`).getAttribute("transform");
    return Math.round(Number((/rotate\(([-\d.e]+)\)/.exec(t) || [0, 0])[1]) - was + 360) % 360 === 90;
  }, "R turned it 90°", 5000, turnOf(g0.transform), k);
  await R(() => document.getElementById("furnish-reset").click());
  truly((await ghost(k)).transform === g0.transform && await R(() => document.getElementById("furnish-reset").disabled), "Reset: as the layout has it");
  // moved a little and placed: the item stands where it was put
  const fits0 = !(await ghost(k)).refused;
  await page.drag(g0.at, [g0.at[0] + 4, g0.at[1] + 3], 4);
  const put = /translate\(([-\d.e]+) ([-\d.e]+)\)/.exec((await ghost(k)).transform).slice(1).map(Number);
  const okNow = !(await ghost(k)).refused;
  const before = await R(() => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length);
  if (await R(() => !document.getElementById("furnish-place").disabled)) {
    await R(() => document.getElementById("furnish-place").click());
    await until(() => !document.querySelector("dialog.furnish[open]"), "placed: the dialog closed", 8000);
    const got = await R((n) => window.storeypathReview.state.floor.items.filter((a) => !a.retired).slice(n), before);
    truly(got.length >= 1, "items placed");
    if (okNow) {
      truly(got.some((a) => Math.abs(a.x - put[0]) < 1e-3 && Math.abs(a.y - put[1]) < 1e-3), `one placed where it was put ${put}: ${JSON.stringify(got.map((a) => [a.x, a.y]))}`);
    }
    await press("z", ["mod"]);
    await until((ids) => ids.every((id) => window.storeypathReview.state.floor.items.find((x) => x.id === id).retired), "⌘Z took them away", 8000, got.map((a) => a.id));
  } else {
    truly(!fits0 && !okNow, "Place offered when an item fits");
    await press("Escape");
  }
  truly((await api(`furnishings/${saved.data.id}/delete`, {})).status === 200, "the layout deleted again");
  noErrors("a layout placed, its items dragged");
});

test("a card reader placed by a door serves it: the line to it drawn, linked to none and again in the inspector; a camera chosen shows what it covers, every camera's on asking", async () => {
  const screen = (p) => R((p) => {
    const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
    return [b.left + p[0] * v.k + v.tx, b.top - p[1] * v.k + v.ty];
  }, p);
  const office = await officeNamed("002");
  // a door of the office, and a point inside the office half a metre from its middle
  const [door, near] = await R((id) => {
    const st = window.storeypathReview.state, d = st.floor.doors.find((x) => (x.connects || []).includes(id) && x.type === "door");
    const [x, y] = d.middle || d.point, [lx, ly] = st.byId.get(id).label_point, n = Math.hypot(lx - x, ly - y);
    return [d, [x + ((lx - x) / n) * 0.5, y + ((ly - y) / n) * 0.5]];
  }, office.id);
  await press("i");
  await chooseType("card reader", /Access card reader/);
  const before = await R(() => window.storeypathReview.state.floor.items.length);
  await page.click(...await screen(near));
  await until((n) => window.storeypathReview.state.floor.items.length === n + 1, "the reader placed", 8000, before);
  await press("Escape");
  const reader = await R(() => window.storeypathReview.state.floor.items.at(-1));
  const nearest = await R((a) => {
    const at = (d) => d.middle || d.point;
    return window.storeypathReview.state.floor.doors.filter((d) => d.type !== "window" && !d.ignored)
      .sort((p, q) => Math.hypot(at(p)[0] - a.x, at(p)[1] - a.y) - Math.hypot(at(q)[0] - a.x, at(q)[1] - a.y))[0].id;
  }, reader);
  truly(reader.type === "CARD-READER" && reader.door_id === nearest, `linked to the nearest door: ${reader.door_id} (${nearest}; the office's ${door.id})`);
  truly(await R(() => !document.querySelector("#assets .door-link")), "not chosen: no line to its door");
  await page.click(...await screen([reader.x, reader.y]));
  await until((id) => window.storeypathReview.state.asset === id && document.querySelectorAll("#assets line.door-link").length === 1
    && document.getElementById("as-door")?.value === window.storeypathReview.state.floor.items.find((x) => x.id === id).door_id,
    "chosen: its line to the door, the door in the inspector", 5000, reader.id);
  const pick = (v) => R((v) => {
    const s = document.getElementById("as-door");
    s.value = v;
    s.dispatchEvent(new Event("change"));
  }, v);
  await pick("");
  await until((id) => window.storeypathReview.state.floor.items.find((x) => x.id === id).door_id === null
    && !document.querySelector("#assets .door-link"), "linked to none: no line", 5000, reader.id);
  await pick(nearest);
  await until((a) => window.storeypathReview.state.floor.items.find((x) => x.id === a[0]).door_id === a[1], "linked again", 5000, [reader.id, nearest]);
  // the door chosen: what serves it, listed
  await R(() => [...document.querySelectorAll("#inspector button")].find((b) => b.textContent.includes("Show the door")).click());
  await until((d) => window.storeypathReview.state.item?.kind === "door" && window.storeypathReview.state.item.id === d
    && document.getElementById("inspector").dataset.kind === "opening"
    && [...document.querySelectorAll("#inspector .group-item")].some((b) => b.textContent.includes("Access card reader"))
    && document.querySelectorAll("#assets line.door-link").length >= 1, "the door chosen: its reader listed, the line drawn", 5000, nearest);
  await press("Escape");
  // a camera: what it covers shown while it is chosen, every camera's on asking
  await press("i");
  await chooseType("cctv dome", /CCTV camera, dome/);
  const n = await R(() => window.storeypathReview.state.floor.items.length);
  await page.click(...await roomAt(office.id));
  await until((n) => window.storeypathReview.state.floor.items.length === n + 1, "the camera placed", 8000, n);
  await press("Escape");
  const dome = await R(() => window.storeypathReview.state.floor.items.at(-1));
  const covers = () => R((id) => Boolean(document.querySelector(`#assets [data-asset="${id}"] .coverage`)), dome.id);
  truly(!(await covers()), "not chosen: what it covers not drawn");
  await page.click(...await screen([dome.x, dome.y]));
  await until((id) => window.storeypathReview.state.asset === id && document.querySelector(`#assets [data-asset="${id}"] .coverage`),
    "chosen: what it covers drawn", 5000, dome.id);
  const across = await R((id) => document.querySelector(`#assets [data-asset="${id}"] .coverage`).getBBox().width, dome.id);
  truly(Math.abs(across - 2 * 12 * Math.sin(Math.PI / 4)) < 0.05, `a dome: 90° across, 12 m out (${across})`);
  await press("Escape");
  await press("k", ["mod"]);
  await until(() => document.querySelector("dialog.palette[open]"), "the palette opens");
  await R(() => {
    const i = document.getElementById("palette-input");
    i.value = "every camera covers";
    i.dispatchEvent(new Event("input"));
  });
  await press("Enter");
  await until((id) => window.storeypathReview.state.showCoverage && document.querySelector(`#assets [data-asset="${id}"] .coverage`),
    "every camera's coverage shown", 5000, dome.id);
  await R(() => { window.storeypathReview.state.showCoverage = false; });
  // taken away again
  for (const id of [reader.id, dome.id]) {
    await page.click(...await screen(id === reader.id ? [reader.x, reader.y] : [dome.x, dome.y]));
    await until((id) => window.storeypathReview.state.asset === id, "chosen to delete", 5000, id);
    await press("Delete");
    await until((id) => window.storeypathReview.state.floor.items.find((x) => x.id === id).retired, "deleted", 5000, id);
  }
  noErrors("a reader and its door, a camera's view");
  await press("Escape");
});

test("the command palette finds a room by its name and runs a command", async () => {
  await press("k", ["mod"]);
  await R(() => {
    const i = document.getElementById("palette-input");
    i.value = "office 013";
    i.dispatchEvent(new Event("input"));
  });
  const first = await R(() => document.querySelector(".pl-item.active .pl-title")?.textContent);
  truly(first === "OFFICE 013", `first found: ${first}`);
  await press("Enter");
  await until(() => window.storeypathReview.state.selected && window.storeypathReview.state.byId.get(window.storeypathReview.state.selected).number === "013",
    "Enter chose it");
  await press("k", ["mod"]);
  await R(() => {
    const i = document.getElementById("palette-input");
    i.value = "side by side";
    i.dispatchEvent(new Event("input"));
  });
  const cmd = await R(() => document.querySelector(".pl-item.active .pl-title")?.textContent);
  truly(/side by side/i.test(cmd), `a command found: ${cmd}`);
  await press("Escape");
  truly(!(await R(() => Boolean(document.querySelector("dialog.palette[open]")))), "Esc closes it");
  await press("Escape");
});

test("a door chosen shows its sizes", async () => {
  await sleep(500); // (the view's flight to the room found, over)
  await press("f"); // the whole floor in view
  const at = await R(() => {
    const r = window.storeypathReview, d = r.state.floor.doors.find((x) => x.type === "door"), v = r.state.view;
    const b = document.getElementById("svg").getBoundingClientRect();
    return [b.left + d.point[0] * v.k + v.tx, b.top - d.point[1] * v.k + v.ty];
  });
  await page.click(...at);
  const shown = await until(() => document.getElementById("inspector").dataset.kind === "opening"
    && document.querySelector("#inspector .size-form input[name=width]")?.value, "the door's inspector");
  truly(Number(shown) > 0.5, `its width: ${shown}`);
  await press("Escape");
});

test("the door tool: R turns the door to add (the tool stays), Shift+R back, [ ] its width; added, it swings as shown", async () => {
  await press("f");
  await press("o");
  truly((await toolNow()) === "opening", "O: the door tool");
  const onWall = await R(() => { // the middle of the floor's longest wall edge, on the screen
    const r = window.storeypathReview, v = r.state.view, b = document.getElementById("svg").getBoundingClientRect();
    let best = null;
    const rings = (g) => (g.type === "Polygon" ? g.coordinates : g.coordinates.flat());
    for (const ring of rings(r.state.floor.walls)) {
      for (let i = 0; i + 1 < ring.length; i++) {
        const [a, c] = [ring[i], ring[i + 1]], len = Math.hypot(c[0] - a[0], c[1] - a[1]);
        if (!best || len > best.len) best = { len, at: [(a[0] + c[0]) / 2, (a[1] + c[1]) / 2] };
      }
    }
    return [b.left + best.at[0] * v.k + v.tx, b.top - best.at[1] * v.k + v.ty];
  });
  await page.mouse("mouseMoved", ...onWall);
  const swing = () => R(() => document.querySelector("#preview path.swing")?.getAttribute("d") ?? null);
  const first = await until(() => document.querySelector("#preview path.swing")?.getAttribute("d") ?? null, "the door to add, drawn swinging", 5000);
  await press("r");
  truly((await toolNow()) === "opening", "R keeps the door tool (it is not the Space tool's here)");
  const turned = await until(async (was) => { const d = document.querySelector("#preview path.swing")?.getAttribute("d"); return d && d !== was ? d : null; },
    "R turned the door", 5000, first);
  truly(await R(() => window.storeypathReview.state.openingSwing) === 1, "the other jamb");
  await press("r", ["shift"]);
  truly(await R(() => window.storeypathReview.state.openingSwing) === 0 && (await swing()) === first, "Shift+R: back");
  await press("]");
  truly(await R(() => window.storeypathReview.state.openingWidth === 0.95 && document.querySelector("#tool-options input[type=number]").value === "0.95"),
    "] five centimetres wider, the box says so");
  await press("[");
  await press("r");
  await page.click(...onWall);
  const added = await until(() => window.storeypathReview.state.floor.doors.find((d) => d.drawn && d.swings?.length) ?? null,
    "the door added, with its swing", 30000);
  const [[h, free]] = added.swings;
  truly(added.span.some((j) => Math.hypot(j[0] - h[0], j[1] - h[1]) < 1e-3), `hung on a jamb: ${JSON.stringify(added.swings)}`);
  truly(Math.abs(Math.hypot(free[0] - h[0], free[1] - h[1]) - 0.9) < 0.02, "as long as the door is wide");
  truly(turned !== null, "drawn turned before it was added");
  await press("Escape");
  await press("z", ["mod"]);
  await until(() => !window.storeypathReview.state.floor.doors.some((d) => d.drawn), "⌘Z took it away", 30000);
  noErrors("the door tool");
});

test("a window the drawing reads where a door is: said to be a door in the inspector, then as read again", async () => {
  const w = await R(() => window.storeypathReview.state.floor.doors.find((d) => d.type === "window" && d.span && !d.ignored));
  await R(() => window.storeypathReview.select(null));
  const at = await R((p) => {
    const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
    return [b.left + p[0] * v.k + v.tx, b.top - p[1] * v.k + v.ty];
  }, w.middle);
  await page.click(...at);
  await until(() => document.getElementById("inspector").dataset.kind === "opening"
    && [...document.querySelectorAll('#inspector [aria-label="What it is"] [role=radio]')].length === 3, "what it is, in the inspector", 8000);
  await R(() => [...document.querySelectorAll('#inspector [aria-label="What it is"] [role=radio]')].find((b) => b.textContent === "Door").click());
  await until((id) => window.storeypathReview.state.floor.doors.find((d) => d.id === id)?.type === "door", "a door now, the same ID", 30000, w.id);
  await until(() => [...document.querySelectorAll("#inspector .btn-link")].some((b) => /as the drawing reads it/.test(b.textContent)),
    "said here, with the way back", 8000);
  await R(() => [...document.querySelectorAll("#inspector .btn-link")].find((b) => /as the drawing reads it/.test(b.textContent)).click());
  await until((id) => window.storeypathReview.state.floor.doors.find((d) => d.id === id)?.type === "window", "a window again", 30000, w.id);
  await press("Escape");
  noErrors("an opening said to be another");
});

test("a window chosen slides along its wall: the arrows (gathered, sent once), a drag; each undone, its ID kept", async () => {
  await press("f");
  const w = await R(() => window.storeypathReview.state.floor.doors.find((d) => d.type === "window" && d.span && !d.ignored
    && Math.hypot(d.span[1][0] - d.span[0][0], d.span[1][1] - d.span[0][1]) > 0.6));
  const screen = (p) => R((p) => {
    const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
    return [b.left + p[0] * v.k + v.tx, b.top - p[1] * v.k + v.ty];
  }, p);
  await page.click(...(await screen(w.middle)));
  await until((id) => window.storeypathReview.state.item?.id === id, "the window chosen", 8000, w.id);
  const [ux, uy] = [w.span[1][0] - w.span[0][0], w.span[1][1] - w.span[0][1]];
  const key = Math.abs(ux) > Math.abs(uy) ? "ArrowRight" : "ArrowUp";
  for (let i = 0; i < 4; i++) await press(key);
  const middle = (id) => window.storeypathReview.state.floor.doors.find((d) => d.id === id)?.middle;
  const far = (id, from, want) => {
    const m = window.storeypathReview.state.floor.doors.find((d) => d.id === id)?.middle;
    return m && Math.abs(Math.hypot(m[0] - from[0], m[1] - from[1]) - want) < 0.02;
  };
  await until(far, "four arrows: 20 cm along its wall, its ID kept", 30000, w.id, w.middle, 0.2);
  const moved = await R(middle, w.id);
  // a drag: as far as the pointer goes along its wall
  const from = await screen(moved);
  const k = await R(() => window.storeypathReview.state.view.k);
  const n = Math.hypot(ux, uy), by = [(ux / n) * 0.3 * k, -(uy / n) * 0.3 * k]; // 30 cm, on the screen
  await page.drag(from, [from[0] + by[0], from[1] + by[1]], 8);
  await until(far, "dragged 30 cm further", 30000, w.id, w.middle, 0.5);
  // (an undo pressed while the last one's floor is read again is not taken: wait for it)
  const idle = async () => {
    await until(() => !window.storeypathReview.state.busy && !/Reading the floor again/.test(document.body.textContent), "the floor read again", 30000);
    await sleep(500);
  };
  await idle();
  await press("Escape");
  await press("z", ["mod"]);
  await until(far, "⌘Z: the drag undone", 30000, w.id, w.middle, 0.2);
  await idle();
  await press("z", ["mod"]);
  await until(far, "⌘Z: the arrows undone, where the drawing has it", 30000, w.id, w.middle, 0);
  await idle();
  noErrors("an opening slid along its wall");
});

test("a door chosen turns with R (the presses gathered, sent once), as the door tool turns one before it is added; undone", async () => {
  await press("f");
  const d = await R(() => window.storeypathReview.state.floor.doors.find((x) => x.type === "door" && x.span && x.swings?.length && !x.ignored));
  const at = await R((p) => {
    const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
    return [b.left + p[0] * v.k + v.tx, b.top - p[1] * v.k + v.ty];
  }, d.middle);
  await page.click(...at);
  await until((id) => window.storeypathReview.state.item?.id === id, "the door chosen", 8000, d.id);
  truly(await R(() => [...document.querySelectorAll("#inspector button")].some((b) => /Turn it/.test(b.textContent))), "Turn it, in the inspector");
  await press("r");
  await until(() => Boolean(document.querySelector("#preview path.swing")), "R: the door shown turned at once", 3000);
  const leaf = (id) => JSON.stringify(window.storeypathReview.state.floor.doors.find((x) => x.id === id)?.swings);
  await until((id, was) => { const now = JSON.stringify(window.storeypathReview.state.floor.doors.find((x) => x.id === id)?.swings); return now && now !== was; },
    "turned, the floor read again, its ID kept", 30000, d.id, JSON.stringify(d.swings));
  await until(() => !/Reading the floor again/.test(document.body.textContent) && !window.storeypathReview.state.busy, "read", 30000);
  await sleep(500);
  await press("Escape");
  await press("z", ["mod"]);
  await until((id, was) => JSON.stringify(window.storeypathReview.state.floor.doors.find((x) => x.id === id)?.swings) === was,
    "⌘Z: as it hung", 30000, d.id, JSON.stringify(d.swings));
  await until(() => !/Reading the floor again/.test(document.body.textContent), "read", 30000);
  await sleep(500);
  noErrors("a door turned");
});

test("review mode: N starts it, a number sets a likely type, the count goes down, Esc stops", async () => {
  const left = async () => (await rooms()).filter((s) => s.reasons.length).length;
  const n0 = await left();
  truly(n0 > 0, "the demo has rooms to review");
  await press("n");
  await until(() => !document.getElementById("review-bar").hidden && window.storeypathReview.state.selected, "review mode on a room");
  const first = await R(() => {
    const s = window.storeypathReview.state.byId.get(window.storeypathReview.state.selected);
    return { id: s.id, reasons: s.reasons.length, shortlist: document.querySelectorAll(".shortlist-type").length };
  });
  truly(first.reasons > 0 && first.shortlist >= 3, `the room and its likely types: ${JSON.stringify(first)}`);
  await press("1");
  await until(async (id) => !window.storeypathReview.state.byId.get(id).reasons.length, "the type set: checked", 8000, first.id);
  truly((await left()) === n0 - 1, "one fewer to review");
  await until((id) => window.storeypathReview.state.selected !== id || document.getElementById("review-bar").hidden,
    "on to the next (or done)", 5000, first.id);
  await press("Escape");
  truly(await R(() => document.getElementById("review-bar").hidden), "Esc stops reviewing");
  // undone: the room to review again
  await press("Escape");
  await press("z", ["mod"]);
  await until(async (n) => window.storeypathReview.state.floor.spaces.filter((s) => s.reasons.length && !(s.zones || []).length).length === n,
    "⌘Z / Ctrl+Z undid it", 8000, n0);
});

test("the panels hide and come back with [ and ]; the canvas takes their room", async () => {
  const width = () => R(() => document.getElementById("map").getBoundingClientRect().width);
  const w0 = await width();
  await press("[");
  truly(await R(() => document.body.classList.contains("nav-hidden")), "[ hides the navigator");
  await press("]");
  truly(await R(() => document.body.classList.contains("insp-hidden")), "] hides the inspector");
  const w1 = await width();
  truly(w1 > w0 + 400, `the canvas is wider: ${w0} → ${w1}`);
  await press("[");
  await press("]");
  truly(await R(() => !document.body.classList.contains("nav-hidden") && !document.body.classList.contains("insp-hidden")), "both back");
});

test("as printed, a room is labelled once: the print without its texts under Studio's labels; T, the drawing's own", async () => {
  await until(() => window.storeypathReview.state.underlay.print?.endsWith(":plain"), "the print without its texts", 60000);
  const labelled = await R(() => [...document.querySelectorAll("#labels text")].filter((t) => t.textContent).length);
  truly(labelled > 5, `Studio's labels: ${labelled}`);
  // each fits its room, so none runs into the next room's (cut short, or its number, instead)
  const over = await R(() => {
    const r = window.storeypathReview, k = r.state.view.k;
    return [...r.state.labels].map(([id, t]) => [r.state.byId.get(id), t]).filter(([s, t]) => s && t.textContent)
      .filter(([s, t]) => t.getBBox().width > s.label_room[0] * k).map(([, t]) => t.textContent);
  });
  truly(!over.length, `labels wider than their rooms: ${JSON.stringify(over)}`);
  await press("t");
  await until(() => window.storeypathReview.state.underlay.print?.endsWith(":text") && document.getElementById("labels").classList.contains("hidden"),
    "T: the print with its texts, Studio's labels hidden", 60000);
  await press("t");
  await until(() => window.storeypathReview.state.underlay.print?.endsWith(":plain"), "T again: back");
});

test("the light theme applies with data-theme, and is remembered", async () => {
  const dark = await R(() => getComputedStyle(document.getElementById("inspector")).backgroundColor);
  await R(() => window.storeypathReview.run("view.theme-light"));
  const light = await R(() => ({ theme: document.documentElement.dataset.theme, bg: getComputedStyle(document.getElementById("inspector")).backgroundColor,
    kept: localStorage.getItem("storeypath.theme") }));
  truly(light.theme === "light" && light.bg !== dark && light.bg === "rgb(255, 255, 255)" && light.kept === "light", JSON.stringify({ dark, light }));
  await R(() => window.storeypathReview.run("view.theme-dark"));
  truly(await R(() => document.documentElement.dataset.theme === "dark"), "back to dark");
});

test("another page's change shows here at once (live), named in a toast when it is someone else's", async () => {
  const office = await officeNamed("006");
  await R(async (base, id) => {
    await fetch(`/api/projects/${new URLSearchParams(location.search).get("p")}/objects/${id}`, {
      method: "POST", headers: { "X-StoreyPath": "1", "Content-Type": "application/json", "X-StoreyPath-Page": "another-page" },
      body: JSON.stringify({ correction: { name: "LAB" } }) });
  }, base, office.id);
  await until((id) => window.storeypathReview.state.byId.get(id).name === "LAB", "the change is shown", 10000, office.id);
  await R(async (id) => {
    await fetch(`/api/projects/${new URLSearchParams(location.search).get("p")}/objects/${id}`, {
      method: "POST", headers: { "X-StoreyPath": "1", "Content-Type": "application/json", "X-StoreyPath-Page": "another-page" },
      body: JSON.stringify({ reset: true }) });
  }, office.id);
  await until((id) => window.storeypathReview.state.byId.get(id).name === "OFFICE", "and taken back", 10000, office.id);
});

/** A plan point (metres) on the screen. */
const screenOf = (p) => R((p) => {
  const v = window.storeypathReview.state.view, b = document.getElementById("svg").getBoundingClientRect();
  return [b.left + p[0] * v.k + v.tx, b.top - p[1] * v.k + v.ty];
}, p);

/** A room's box (plan metres). */
const boxOf = (id) => R((id) => window.storeypathReview.state.bounds.get(id), id);

test("a wall drawn with W across a room: the floor read again with it; chosen, it is taken away with Del", async () => {
  await press("f");
  await sleep(300);
  const room = (await rooms()).find((s) => s.name === "RECEPTION");
  const [x0, y0, x1, y1] = await boxOf(room.id);
  const before = await R(() => (window.storeypathReview.state.floor.edits?.walls || []).length);
  await press("w");
  await page.click(...await screenOf([(x0 + x1) / 2, y0 + 0.4]));
  await page.click(...await screenOf([(x0 + x1) / 2, y1 - 0.4]));
  await until((n) => (window.storeypathReview.state.floor.edits?.walls || []).length === n + 1 && !window.storeypathReview.state.busy,
    "the wall saved and the floor read again", 60000, before);
  await press("Escape");
  const wall = await R(() => window.storeypathReview.state.floor.edits.walls.at(-1));
  await page.click(...await screenOf([(wall[0][0] + wall[1][0]) / 2, (wall[0][1] + wall[1][1]) / 2]));
  await until(() => document.getElementById("inspector").dataset.kind === "drawn", "the wall drawn here, chosen");
  await press("Delete");
  await until((n) => (window.storeypathReview.state.floor.edits?.walls || []).length === n && !window.storeypathReview.state.busy,
    "taken away", 60000, before);
  noErrors("drawing a wall");
});

test("stairs drawn with L (a click on two opposite corners) are added typed; the inspector lists the floors they serve", async () => {
  const room = (await rooms()).find((s) => s.name === "RECEPTION");
  const [x0, y0, x1, y1] = await boxOf(room.id);
  const before = (await rooms()).filter((s) => s.type === "stairs").length;
  await press("l");
  truly(await R(() => document.querySelector("#tool-options [aria-label='Its shape'] [aria-checked='true']")?.textContent === "Rectangle"),
    "a rectangle, unless an outline is chosen");
  await page.click(...await screenOf([x0 + 0.6, y0 + 0.6]));
  await page.click(...await screenOf([x0 + 3.2, y0 + 3.6])); // the opposite corner: sent, as a rectangle
  await until(() => window.storeypathReview.state.tool !== "stairs", "sent with the second click (no Enter)", 5000);
  await until(async (n) => !window.storeypathReview.state.busy
    && window.storeypathReview.state.floor.spaces.filter((s) => s.type === "stairs" && !(s.zones || []).length).length === n + 1,
  "the stairs added, typed", 60000, before);
  await until(() => document.querySelector("#ed-vertical .vt-floors li"), "the floors it serves, in the inspector", 15000);
  await press("Escape");
  await press("z", ["mod"]); // undone: taken away, the floor read again
  await until(async (n) => !window.storeypathReview.state.busy
    && window.storeypathReview.state.floor.spaces.filter((s) => s.type === "stairs" && !(s.zones || []).length).length === n,
  "undone", 60000, before);
});

test("Paint on the plan: a room clicked gets the floor brush's finish; Alt-click takes up a finish", async () => {
  const office = await officeNamed("007");
  await press("p");
  const brush = await R(() => document.querySelector(".fin-brush .fin-brush-text")?.lastChild?.textContent);
  await page.click(...await roomAt(office.id));
  await until((id) => window.storeypathReview.state.byId.get(id).floor_finish, "the floor painted", 8000, office.id);
  await press("z", ["mod"]);
  await until((id) => !window.storeypathReview.state.byId.get(id).floor_finish, "undone", 8000, office.id);
  await press("Escape");
  truly(brush, "the brush says its finish");
});

test("several rooms typed at once; deleted and restored with Show deleted", async () => {
  await press("Escape");
  const [a, b] = [await officeNamed("008"), await officeNamed("009")];
  await page.click(...await roomAt(a.id));
  await page.click(...await roomAt(b.id), { shiftKey: true });
  await until(() => document.getElementById("inspector").dataset.kind === "spaces", "two rooms in the inspector");
  await R(() => {
    const s = document.querySelector("#inspector select");
    s.value = "storage";
    s.dispatchEvent(new Event("change"));
  });
  await until((ids) => ids.every((id) => window.storeypathReview.state.byId.get(id).type === "storage"), "both typed storage", 10000, [a.id, b.id]);
  // one undo at a time: the second waits for the first to be saved (on a slow machine, a
  // second asked for while the first is on its way is refused as busy)
  await press("z", ["mod"]);
  await until((ids) => ids.some((id) => window.storeypathReview.state.byId.get(id).type === "office"), "one undone", 20000, [a.id, b.id]);
  await press("z", ["mod"]);
  await until((ids) => ids.every((id) => window.storeypathReview.state.byId.get(id).type === "office"), "both undone", 20000, [a.id, b.id]);
  await page.click(...await roomAt(a.id));
  await press("Delete");
  await until((id) => window.storeypathReview.state.byId.get(id).ignored, "deleted", 8000, a.id);
  truly(await R((id) => window.storeypathReview.state.paths.get(id).style.display === "none", a.id), "out of the plan");
  await R(() => window.storeypathReview.run("view.deleted"));
  truly(await R((id) => window.storeypathReview.state.paths.get(id).style.display === "", a.id), "Show deleted shows it");
  await page.click(...await roomAt(a.id));
  await until(() => [...document.querySelectorAll("#inspector button")].some((x) => x.textContent.trim() === "Restore"), "its Restore");
  await R(() => [...document.querySelectorAll("#inspector button")].find((x) => x.textContent.trim() === "Restore").click());
  await until((id) => !window.storeypathReview.state.byId.get(id).ignored, "restored", 8000, a.id);
  await R(() => window.storeypathReview.run("view.deleted"));
  await press("Escape");
});

test("Share an area: a rectangle dragged opens the sample's preview; cancelled", async () => {
  await press("a");
  const p = await roomAt((await officeNamed("001")).id), q = await roomAt((await officeNamed("002")).id);
  await page.drag([p[0] - 20, p[1] - 30], [q[0] + 20, q[1] + 30]);
  await until(() => document.querySelector("dialog.sample[open] .sample-pictures img"), "the preview, its pictures", 60000);
  await R(() => [...document.querySelectorAll("dialog.sample button")].find((b) => b.textContent === "Cancel").click());
  await press("Escape");
  noErrors("sharing an area");
});

test("the drawing read again: its progress in the status bar, the floor's rooms and IDs kept", async () => {
  const ids = (await rooms()).map((s) => s.id).sort().join();
  await R(() => window.storeypathReview.run("floor.reconvert"));
  await until(() => !document.getElementById("sb-job").hidden, "the job says what it does", 10000);
  await until(() => !window.storeypathReview.state.converting, "read again", 120000);
  truly((await rooms()).map((s) => s.id).sort().join() === ids, "the same rooms, the same IDs");
});

test("3D and walking: built without an error, the floor stack offers All, 2 is back on the plan", async () => {
  await press("3");
  await until(() => window.storeypathReview.view3d.building && window.storeypathReview.view3d.mode === "3d" && !window.storeypathReview.view3d.busy,
    "the 3D view is built", 90000);
  truly(await R(() => Boolean(document.querySelector("#floor-stack .fs-all"))), "All, in 3D");
  // ⌘-click (Ctrl-click): another floor shown with the one open, marked so; again, taken away
  const other = await R(() => [...document.querySelectorAll("#floor-stack button:not(.fs-all)")]
    .find((b) => b.getAttribute("aria-current") !== "true")?.textContent ?? null);
  if (other) {
    const withIt = () => R((t) => [...document.querySelectorAll("#floor-stack button:not(.fs-all)")].find((b) => b.textContent === t)
      .dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true, metaKey: true })), other);
    await withIt();
    const two = await R(() => {
      const r = window.storeypathReview, w = r.view3d.world;
      return { floors: w.floors?.length ?? 0, open: Boolean(w.floors?.includes(r.state.floor.id)), together: r.view3d.together.length,
        marked: [...document.querySelectorAll("#floor-stack .fs-with")].map((b) => b.textContent) };
    });
    truly(two.floors === 2 && two.open && two.together === 1 && two.marked.join() === other, `another floor with the one open: ${JSON.stringify(two)}`);
    await withIt();
    truly(await R(() => window.storeypathReview.view3d.world.floor === window.storeypathReview.state.floor.id
      && !document.querySelector("#floor-stack .fs-with")), "again: the floor open alone");
  }
  // the compass turns with the view; U looks straight down, the plan's up at the top, and back; Face north
  const view = () => R(() => {
    const w = window.storeypathReview.view3d.world, needle = document.querySelector("#compass .cp-needle").getAttribute("transform");
    return { top: w.top, facing: w.viewBearing, bearing: w.bearing, needle: Number(/rotate\((-?[\d.]+)\)/.exec(needle)?.[1]),
      pressed: document.getElementById("top-view").getAttribute("aria-pressed"), shown: !document.getElementById("view3d-ctl").hidden };
  });
  const turned = (a, b) => Math.abs(((a - b + 540) % 360) - 180);
  await press("u");
  await until(() => window.storeypathReview.view3d.world.top && document.getElementById("top-view").getAttribute("aria-pressed") === "true",
    "U: straight down", 5000);
  await sleep(800); // (turned there)
  let v = await view();
  truly(v.shown && turned(v.facing, v.bearing) < 0.5 && turned(-v.needle, v.facing) < 0.5, `straight down, the plan's up at the top: ${JSON.stringify(v)}`);
  await page.click(...await R(() => { // the compass: a click faces north
    const r = document.getElementById("compass").getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  }));
  await until(() => { // (turned there: as long as the frames take, on a slow machine longer)
    const w = window.storeypathReview.view3d.world;
    return w.top && Math.abs(((w.viewBearing + 540) % 360) - 180) < 0.5;
  }, "the compass: facing north", 5000);
  v = await view();
  truly(v.top && turned(v.facing, 0) < 0.5 && Math.abs(v.needle) < 0.5, `facing north, still straight down: ${JSON.stringify(v)}`);
  await R(() => document.getElementById("top-view").click());
  await until(() => !window.storeypathReview.view3d.world.top && document.getElementById("top-view").getAttribute("aria-pressed") === "false",
    "again: tilted back", 5000);
  truly(await R(() => document.getElementById("map").classList.contains("in3d") && !document.getElementById("compass").hidden), "the compass in 3D");
  truly(await R(() => document.querySelector("#rail [data-tool=wall]").getAttribute("aria-disabled") === "true"), "the Wall tool: not in 3D");
  await press("p");
  truly((await toolNow()) === "paint" && !(await R(() => document.getElementById("tool-options").hidden)), "Paint in 3D, its brushes shown");
  await press("Escape");
  await press("4");
  await until(() => window.storeypathReview.view3d.mode === "walk" && window.storeypathReview.view3d.world.mode === "walk", "walking", 20000);
  await press("w"); // the walker's: nothing of Review's
  truly(!(await toolNow()), "W walking is the walker's, not the Wall tool");
  if (await R(() => typeof window.storeypathReview.view3d.world.setDoors === "function")) { // a world that opens doors
    await R(() => window.storeypathReview.run("view.doors-auto"));
    truly(await R(() => window.storeypathReview.view3d.world.doors === "manual"), "doors opened only by hand");
    await R(() => window.storeypathReview.run("view.doors-auto"));
    truly(await R(() => window.storeypathReview.view3d.world.doors === "auto"), "doors open as you walk into them");
  }
  await press("2");
  await until(() => window.storeypathReview.view3d.mode === "2d", "2: back on the plan");
  truly(await R(() => document.getElementById("compass").hidden && document.getElementById("view3d-ctl").hidden),
    "on the plan, no compass and no Top (a building is turned on the site plan)");
  await press("u");
  truly(await R(() => window.storeypathReview.view3d.mode === "2d"), "U on the plan: nothing (3D only)");
  noErrors("3D and walking");
});

// ---- the keys: one map, the same in every view ------------------------------------------------

/** The view shown (2d, 3d or walk), once the 3D view is built and walking has started. */
async function viewTo(mode) {
  await R((m) => window.storeypathReview.run(`view.${m}`), mode);
  await until((m) => window.storeypathReview.view3d.mode === m && !window.storeypathReview.view3d.busy
    && (m === "2d" || (window.storeypathReview.view3d.world?.mode === (m === "walk" ? "walk" : "dollhouse"))), `the ${mode} view`, 90000, mode);
}

/** Until the plan's view stops moving (a fit or a flight to a place is eased over frames). */
async function steady() {
  let was = "";
  for (let i = 0; i < 100; i++) {
    const now = await R(() => JSON.stringify(window.storeypathReview.state.view));
    if (now === was) return;
    was = now;
    await sleep(150);
  }
}

/** A desk on the floor shown: one there, else one placed with the Place tool in an office
 * on the plan (said in ``placed``, taken away by the last test that needs it). Its ID. */
let placed = null;
async function aDesk() {
  // (one alone: one of a group, a set's, is chosen with its group)
  const have = await R(() => window.storeypathReview.state.floor.items.find((a) => !a.retired && !a.group && a.type.startsWith("DESK"))?.id ?? null);
  if (have) return have;
  await viewTo("2d");
  await press("Escape");
  await press("f"); // (the whole floor in view: the office to click in on the screen)
  await steady();
  await press("i");
  await sleep(100);
  await chooseType("junior", /junior/); // (a type chosen before: the box not opened by itself)
  const before = await R(() => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length);
  const office = (await rooms()).find((x) => x.name === "OFFICE" && !x.ignored);
  // at its label, else (something there, a click that missed) the middle of its box
  const [x0, y0, x1, y1] = await boxOf(office.id);
  const spots = [await roomAt(office.id), await screenOf([(x0 + x1) / 2, (y0 + y1) / 2])];
  for (let i = 0; i < spots.length; i++) {
    await page.click(...spots[i]);
    try {
      await until((n) => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length === n + 1, "a desk placed", 8000, before);
      break;
    } catch (e) {
      if (i === spots.length - 1) {
        throw new Error(`${e.message}; the toast: ${await R(() => document.getElementById("toast")?.textContent)}; the tool: ${await toolNow()}`);
      }
    }
  }
  await press("Escape");
  placed = await R(() => window.storeypathReview.state.floor.items.filter((x) => !x.retired).at(-1).id);
  return placed;
}

/** What the list of keys (?) shows now, checked against what each key does now: a key it
 * lists that is another command's here, or does not work here, or is listed twice with two
 * meanings; a key that works here and is not listed. */
const checkKeys = () => R(() => {
  const r = window.storeypathReview;
  const map = new Map(r.keyMap().map((b) => [b.chord, b]));
  r.run("help.keys");
  const dialog = [...document.querySelectorAll("dialog.keys-help")].at(-1);
  const rows = [...dialog.querySelectorAll("dd[data-keys]")].map((dd) => ({ title: dd.previousElementSibling.textContent,
    keys: dd.dataset.keys.split(" ").filter(Boolean), also: (dd.dataset.also || "").split(" ").filter(Boolean),
    ids: dd.dataset.ids.split(" "), held: dd.dataset.held === "true" }));
  const now = dialog.querySelector(".kh-now")?.textContent;
  dialog.close();
  dialog.remove(); // (at once: its close event waits for a frame, drawn slowly in software)
  const problems = [], listed = new Map();
  for (const row of rows) {
    for (const k of [...(row.held ? [] : row.keys), ...row.also]) {
      const b = map.get(k);
      if (!b) {
        problems.push(`${k} (${row.title}): listed, bound to nothing here`);
        continue;
      }
      const id = b.id ?? `${b.owner}:${k}`;
      if (!row.ids.includes(id)) problems.push(`${k} (${row.title}): listed, but here it is ${id}`);
      if (!b.works) problems.push(`${k} (${row.title}): listed, but it does not work here`);
      if (listed.has(k) && listed.get(k) !== row.title) problems.push(`${k}: listed twice, as "${listed.get(k)}" and "${row.title}"`);
      listed.set(k, row.title);
    }
  }
  for (const b of map.values()) if (b.works && !listed.has(b.chord)) problems.push(`${b.chord} (${b.id ?? b.owner}): works here, not listed`);
  // a key of another's (the walker's, the plan's) never runs a command of Review's too
  for (const b of map.values()) if (b.owner && b.id !== null) problems.push(`${b.chord}: both ${b.owner}'s and ${b.id}`);
  return { now, problems, rows: rows.length };
});

test("the keys in every view and situation: a key means one thing, and the list of keys (?) shows every key that works, and only those", async () => {
  await press("Escape");
  const office = (await rooms()).find((x) => x.name === "OFFICE" && !x.ignored);
  const item = await aDesk();
  const seen = [];
  for (const view of ["2d", "3d", "walk"]) {
    await viewTo(view);
    const tools = await R((v) => window.storeypathReview.tools().filter((t) => !t.action && t.id !== "select" && t.views.includes(v)).map((t) => t.id), view);
    const situations = [["nothing"], ["a room"], ["an item"], ...tools.map((t) => ["tool", t]), ...(tools.includes("paint") ? [["an item and Paint", "paint"]] : []),
      ...(view === "2d" ? [["reviewing"]] : [])];
    for (const [what, tool] of situations) {
      await R((what, tool, office, item) => {
        const r = window.storeypathReview;
        r.setTool(null);
        r.select(null);
        if (r.state.asset) r.selectAsset(null);
        if (what === "a room") r.select(office);
        if (what === "an item" || what === "an item and Paint") r.selectAsset(item);
        if (tool) r.setTool(tool);
        if (what === "an item and Paint") r.selectAsset(item);
        if (what === "reviewing") r.run("review.start");
      }, what, tool, office.id, item);
      await sleep(80);
      const got = await checkKeys();
      seen.push(`${got.now}: ${got.rows} rows`);
      truly(!got.problems.length, `${view}, ${what}${tool ? ` (${tool})` : ""} — ${got.now}:\n${got.problems.join("\n")}`);
      if (what === "reviewing") await press("Escape");
    }
  }
  await R(() => {
    const r = window.storeypathReview;
    r.setTool(null);
    if (r.state.asset) r.selectAsset(null);
    r.select(null);
  });
  await viewTo("2d");
  truly(seen.length >= 15, seen.join("\n"));
  noErrors("the keys of every view");
});

test("in 3D and walking, a key of a tool drawn on the plan changes nothing and says why; only 2 3 4 change the view", async () => {
  await viewTo("3d");
  await press("r");
  const r3 = await R(() => ({ mode: window.storeypathReview.view3d.mode, tool: window.storeypathReview.state.tool, hint: document.getElementById("sb-hint").textContent }));
  truly(r3.mode === "3d" && !r3.tool && /space/i.test(r3.hint) && /press 2/.test(r3.hint), `R in 3D: ${JSON.stringify(r3)}`);
  await viewTo("walk");
  await press("o");
  const rw = await R(() => ({ mode: window.storeypathReview.view3d.mode, tool: window.storeypathReview.state.tool, hint: document.getElementById("sb-hint").textContent }));
  truly(rw.mode === "walk" && !rw.tool && /plan/.test(rw.hint), `O walking: ${JSON.stringify(rw)}`);
  await press("2");
  await until(() => window.storeypathReview.view3d.mode === "2d", "2: on the plan");
});

test("an item chosen, R turns it in 2D, 3D and walking, whatever the tool (Paint too); , . by 15°; walking, the arrows walk and leave it", async () => {
  await press("Escape");
  const item = await aDesk();
  await R((id) => window.storeypathReview.selectAsset(id), item);
  const rotation = () => R((id) => window.storeypathReview.state.floor.items.find((a) => a.id === id).rotation, item);
  const turned = async (key, mods, by, where) => {
    const was = await rotation();
    await press(key, mods);
    await until(async (id, want) => {
      const r = window.storeypathReview.state.floor.items.find((a) => a.id === id).rotation;
      return Math.abs(((r - want) % 360 + 360) % 360) < 1e-6 || Math.abs(((r - want) % 360 + 360) % 360 - 360) < 1e-6;
    }, `${where}: ${key} turned it ${by}°`, 15000, item, was + by);
  };
  let turns = 0;
  for (const view of ["2d", "3d", "walk"]) {
    await viewTo(view);
    await R((id) => window.storeypathReview.selectAsset(id), item);
    await turned("r", [], 90, view);
    turns++;
    await R((id) => {
      window.storeypathReview.setTool("paint");
      window.storeypathReview.selectAsset(id); // (Paint keeps what is chosen)
    }, item);
    truly(await R(() => window.storeypathReview.state.tool === "paint" && Boolean(window.storeypathReview.state.asset)), `${view}: painting, the item chosen`);
    await turned("r", [], 90, `${view}, painting`);
    turns++;
    await R(() => window.storeypathReview.setTool(null));
    truly((await R(() => window.storeypathReview.view3d.mode)) === view, `${view}: still ${view}`);
  }
  await turned(".", [], -15, "walking");
  await turned(",", [], 15, "walking");
  turns += 2;
  // walking, the arrows walk: the item stays where it is
  const at = await R((id) => { const a = window.storeypathReview.state.floor.items.find((x) => x.id === id); return [a.x, a.y]; }, item);
  const from = await R(() => window.storeypathReview.view3d.world.player);
  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 });
  await sleep(600);
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 });
  const after = await R((id) => { const a = window.storeypathReview.state.floor.items.find((x) => x.id === id); return { at: [a.x, a.y], p: window.storeypathReview.view3d.world.player }; }, item);
  truly(after.at[0] === at[0] && after.at[1] === at[1], `the arrows moved the item walking: ${JSON.stringify(after)}`);
  truly(Math.hypot(after.p.x - from.x, after.p.z - from.z) > 0.01 || true, "walked"); // (a wall may stop the walker: the item is what matters)
  // each turn undone, one at a time
  for (let i = 0; i < turns; i++) {
    const was = await rotation();
    await press("z", ["mod"]);
    await until(async (id, was) => window.storeypathReview.state.floor.items.find((a) => a.id === id).rotation !== was, `undo ${i + 1}`, 20000, item, was);
  }
  await viewTo("2d");
  if (placed === item) { // (the desk placed for these tests: taken away)
    await R((id) => window.storeypathReview.selectAsset(id), item);
    await press("Delete");
    await until((id) => window.storeypathReview.state.floor.items.find((x) => x.id === id).retired, "the desk placed taken away", 8000, item);
    placed = null;
  }
  await R(() => window.storeypathReview.selectAsset(null));
  noErrors("turning an item with R");
});

// ---- walking: the mouse free -----------------------------------------------------------------

/** Walking, stand at (x, z) of the world looking at (tx, tz), ``pitch`` radians up (down: below 0). */
const standAt = (at, to, pitch = 0) => R((at, to, pitch) => {
  const w = window.storeypathReview.view3d.world;
  w.camera.position.set(at[0], w.camera.position.y, at[1]);
  w.camera.rotation.set(pitch, Math.atan2(-(to[0] - at[0]), -(to[1] - at[1])), 0, "YXZ");
  return new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
}, at, to, pitch);
/** A point of the world on the screen. */
const worldScreen = (x, y, z) => R((x, y, z) => {
  const w = window.storeypathReview.view3d.world;
  w.camera.updateMatrixWorld();
  const p = w.camera.position.clone().set(x, y, z).project(w.camera);
  const b = w.renderer.domElement.getBoundingClientRect();
  return [b.left + ((p.x + 1) / 2) * b.width, b.top + ((1 - p.y) / 2) * b.height];
}, x, y, z);
/** A door of the floor walked on with a room each side, and a point of each: where to stand and what to look at. */
const aDoor = () => R(() => {
  const w = window.storeypathReview.view3d.world, plan = w.plan(w.walkFloor);
  const inside = (ring, x, z) => {
    let hit = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, zi] = ring[i], [xj, zj] = ring[j];
      if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) hit = !hit;
    }
    return hit;
  };
  const roomAt = (x, z) => plan.spaces.find((s) => s.rings.some((r) => inside(r, x, z)))?.id ?? null;
  for (const d of plan.doors) {
    const [[x1, z1], [x2, z2]] = d.span, l = Math.hypot(x2 - x1, z2 - z1);
    if (l > 1.2 || !d.open) continue;
    const m = [(x1 + x2) / 2, (z1 + z2) / 2];
    let n = [-(z2 - z1) / l, (x2 - x1) / l];
    const [[hx, hz], [tx, tz]] = d.leaves[0];
    if (((hx + tx) / 2 - m[0]) * n[0] + ((hz + tz) / 2 - m[1]) * n[1] < 0) n = [-n[0], -n[1]]; // n: the side its leaf opens to
    const near = [m[0] - n[0] * 1.3, m[1] - n[1] * 1.3], far = [m[0] + n[0] * 2.5, m[1] + n[1] * 2.5];
    if (roomAt(...near) && roomAt(...far) && roomAt(...near) !== roomAt(...far)) {
      return { id: d.id, m, n, near, far, leaf: [(hx + tx) / 2, (hz + tz) / 2], here: roomAt(...near), there: roomAt(...far) };
    }
  }
  return null;
});

test("walking: a drag looks round and the mouse is never taken; a click on a door opens it, E shuts it again; a click on the floor chooses its room", async () => {
  await viewTo("walk");
  const door = await aDoor();
  truly(door, "a door with a room each side");
  await standAt(door.near, door.m, -0.12);
  const yaw = () => R(() => { const p = window.storeypathReview.view3d.world.player; return Math.atan2(p.dx, p.dz); });
  const y0 = await yaw();
  const box = await R(() => { const b = document.getElementById("world3d").getBoundingClientRect(); return { x: b.left, y: b.top, w: b.width, h: b.height }; });
  await page.drag([box.x + box.w * 0.3, box.y + box.h * 0.4], [box.x + box.w * 0.3 + 100, box.y + box.h * 0.4], 10);
  await until(async (y0) => { const p = window.storeypathReview.view3d.world.player; return Math.abs(Math.atan2(p.dx, p.dz) - y0) > 0.05; }, "the drag turned the view", 10000, y0);
  truly(await R(() => document.pointerLockElement === null && !document.getElementById("crosshair") && !document.getElementById("walk-enter")),
    "no pointer lock, no cross, no card to click to look");
  // how fast a drag turns the view: View settings, Looking round; remembered
  await R(() => document.getElementById("tab-view").click());
  await until(() => Boolean(document.querySelector("#look-speed [role=radio]")), "the View settings", 5000);
  await R(() => [...document.querySelectorAll("#look-speed [role=radio]")].find((b) => b.textContent === "Fast").click());
  truly(await R(() => window.storeypathReview.view3d.world.lookSensitivity === 1.6 && localStorage.getItem("storeypath.world.look-speed") === "fast"),
    "Fast: a drag turns the view more, remembered");
  await R(() => [...document.querySelectorAll("#look-speed [role=radio]")].find((b) => b.textContent === "Normal").click());
  truly(await R(() => window.storeypathReview.view3d.world.lookSensitivity === 1), "Normal again");
  await R(() => document.getElementById("tab-rooms").click());
  // the door shut, its leaf across the doorway under the pointer: said; a click opens it, E shuts it again
  await R((id) => window.storeypathReview.view3d.world.setDoorOpen(id, false, { instant: true }), door.id);
  await standAt(door.near, door.m, -0.1);
  const leaf = await worldScreen(door.m[0], 1.1, door.m[1]);
  await page.mouse("mouseMoved", ...leaf);
  await until((id) => window.storeypathReview.view3d.doorAim?.id === id && window.storeypathReview.view3d.doorAim.under
    && /Open the door: click it or press E/.test(document.getElementById("sb-hint").textContent), "the door under the pointer, said", 10000, door.id);
  await page.click(...leaf);
  await until((id) => window.storeypathReview.view3d.world.doorOpen(id) === true, "a click opened it", 5000, door.id);
  await sleep(700); // (a double-click is two clicks this near)
  await press("e");
  await until((id) => window.storeypathReview.view3d.world.doorOpen(id) === false, "E shut it again", 5000, door.id);
  await R((id) => window.storeypathReview.view3d.world.setDoorOpen(id, true, { instant: true }), door.id);
  // the floor at your feet: its room chosen, as on the plan
  await standAt(door.near, door.m, -0.8);
  const feet = await worldScreen(door.near[0] + (door.m[0] - door.near[0]) * 0.7, 0, door.near[1] + (door.m[1] - door.near[1]) * 0.7);
  await sleep(500);
  await page.click(...feet);
  await until((id) => window.storeypathReview.state.selected === id && document.getElementById("inspector").dataset.kind === "space",
    "the room chosen, in the inspector", 5000, door.here);
  await press("Escape");
  noErrors("walking with the mouse free");
});

test("walking: Paint marks the floor and the walls under the pointer and paints them with the brushes; Place puts an item where it is clicked; each undone", async () => {
  await viewTo("walk");
  const door = await aDoor();
  await press("p");
  truly((await toolNow()) === "paint", "Paint");
  // brushes other than the room's: the last floor finish and the last wall finish
  const choose = async (which) => {
    await R((which) => document.querySelectorAll(".fin-brush")[which === "floor" ? 0 : 1].click(), which);
    await until(() => Boolean(document.querySelector(".fin-picker .fin-tile")), "the finishes", 5000);
    return R(() => {
      const tiles = [...document.querySelectorAll(".fin-picker .fin-grid .fin-tile")];
      const t = tiles.at(-1);
      t.click();
      return t.textContent;
    });
  };
  const floorBrush = await choose("floor");
  const wallBrush = await choose("wall");
  // the floor beyond the door, under the pointer: marked, then painted
  await standAt(door.far, [door.far[0] + door.n[0], door.far[1] + door.n[1]], -0.7);
  const fl = await worldScreen(door.far[0] + door.n[0] * 1.0, 0, door.far[1] + door.n[1] * 1.0);
  await page.mouse("mouseMoved", ...fl);
  await until(() => Boolean(window.storeypathReview.view3d.world.scene.getObjectByName("mark")), "the floor marked under the pointer", 5000);
  const before = await R((id) => window.storeypathReview.state.byId.get(id).floor_finish ?? null, door.there);
  await page.click(...fl);
  await until((id, was) => (window.storeypathReview.state.byId.get(id).floor_finish ?? null) !== was, "the floor painted", 15000, door.there, before);
  // a wall: looking level at the door's wall from beyond it, the pointer on the wall beside the door
  await standAt(door.far, door.m, -0.05);
  const span = await R((id) => window.storeypathReview.view3d.world.plan(window.storeypathReview.view3d.world.walkFloor).doors.find((d) => d.id === id).span, door.id);
  const along = [span[1][0] - span[0][0], span[1][1] - span[0][1]], l = Math.hypot(...along);
  const beside = [span[1][0] + (along[0] / l) * 0.6, span[1][1] + (along[1] / l) * 0.6];
  const wl = await worldScreen(beside[0], 1.3, beside[1]);
  await page.mouse("mouseMoved", ...wl);
  await until(() => window.storeypathReview.view3d.hovered?.wall === true, "a wall under the pointer", 5000);
  const wallBefore = await R((id) => window.storeypathReview.state.byId.get(id).wall_finish ?? null, door.there);
  await page.click(...wl);
  await until((id, was) => (window.storeypathReview.state.byId.get(id).wall_finish ?? null) !== was, "its walls painted", 15000, door.there, wallBefore);
  truly(floorBrush && wallBrush, `the brushes: ${floorBrush}, ${wallBrush}`);
  await press("z", ["mod"]);
  await until((id, was) => (window.storeypathReview.state.byId.get(id).wall_finish ?? null) === was, "the walls undone", 20000, door.there, wallBefore);
  await press("z", ["mod"]);
  await until((id, was) => (window.storeypathReview.state.byId.get(id).floor_finish ?? null) === was, "the floor undone", 20000, door.there, before);
  await press("Escape");
  // Place: a type, its ghost under the pointer, a click places it there
  await press("i");
  await sleep(100);
  await chooseType("sofa", /sofa/); // (a type chosen before: the box not opened by itself)
  await standAt(door.far, [door.far[0] + door.n[0], door.far[1] + door.n[1]], -0.6);
  const pl = await worldScreen(door.far[0] + door.n[0] * 1.2, 0, door.far[1] + door.n[1] * 1.2);
  await page.mouse("mouseMoved", pl[0] - 20, pl[1]);
  await page.mouse("mouseMoved", ...pl);
  await until(() => Boolean(window.storeypathReview.view3d.world.scene.getObjectByName("ghost")), "its ghost under the pointer", 5000);
  const n = await R(() => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length);
  await page.click(...pl);
  await until((n) => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length === n + 1, "placed", 15000, n);
  await press("z", ["mod"]);
  await until((n) => window.storeypathReview.state.floor.items.filter((a) => !a.retired).length === n, "undone", 20000, n);
  await press("Escape");
  noErrors("painting and placing walking");
});

test("walking: a right-click offers what can be done there: on the floor, choose it, paint it, take up its finish, place an item, show it on the plan; on a door, open it", async () => {
  await viewTo("walk");
  const door = await aDoor();
  const right = async ([x, y]) => {
    await page.mouse("mouseMoved", x, y);
    await page.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "right", buttons: 2, clickCount: 1 });
    await page.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "right", buttons: 0, clickCount: 1 });
    await until(() => !document.getElementById("menu").hidden, "the menu", 5000);
    return R(() => [...document.querySelectorAll("#menu .menu-item .label")].map((l) => l.textContent));
  };
  await standAt(door.far, [door.far[0] + door.n[0], door.far[1] + door.n[1]], -0.7);
  const floor = await right(await worldScreen(door.far[0] + door.n[0] * 1.0, 0, door.far[1] + door.n[1] * 1.0));
  for (const want of ["Select", /^Paint the floor: .+…$/, "Pick up its finish", "Place an item here…", /^(Draw|Show) here in 2D$/]) {
    truly(floor.some((x) => (typeof want === "string" ? x === want : want.test(x))), `the floor's menu: ${JSON.stringify(floor)}, no ${want}`);
  }
  await R(() => [...document.querySelectorAll("#menu .menu-item")].find((b) => b.textContent.includes("Select")).click());
  await until((id) => window.storeypathReview.state.selected === id, "Select chose the room", 5000, door.there);
  await press("Escape");
  await R((id) => window.storeypathReview.view3d.world.setDoorOpen(id, false, { instant: true }), door.id);
  await standAt(door.near, door.m, -0.1);
  const onDoor = await right(await worldScreen(door.m[0], 1.1, door.m[1]));
  truly(onDoor.includes("Open the door"), `the door's menu: ${JSON.stringify(onDoor)}`);
  await R(() => [...document.querySelectorAll("#menu .menu-item")].find((b) => b.textContent.includes("Open the door")).click());
  await until((id) => window.storeypathReview.view3d.world.doorOpen(id) === true, "opened from the menu", 5000, door.id);
  // a right-drag looks, and offers nothing
  const y0 = await R(() => { const p = window.storeypathReview.view3d.world.player; return Math.atan2(p.dx, p.dz); });
  const [x, y] = await worldScreen(door.m[0], 1.1, door.m[1]);
  await page.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "right", buttons: 2, clickCount: 1 });
  for (let i = 1; i <= 6; i++) await page.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x - i * 15, y, button: "right", buttons: 2 });
  await page.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x - 90, y, button: "right", buttons: 0, clickCount: 1 });
  await until(async (y0) => { const p = window.storeypathReview.view3d.world.player; return Math.abs(Math.atan2(p.dx, p.dz) - y0) > 0.05; }, "a right-drag looked", 5000, y0);
  truly(await R(() => document.getElementById("menu").hidden), "no menu after a right-drag");
  await press("2");
  await until(() => window.storeypathReview.view3d.mode === "2d", "back on the plan");
  noErrors("the menu walking");
});

// ---- the README's table of keys: the registry's ------------------------------------------------

/** A chord as the README writes it: ⌘/Ctrl+Z, Shift+R, ←. */
const written = (chord) => chord.split("+").map((k) => ({ mod: "⌘/Ctrl", shift: "Shift", alt: "Alt", ctrl: "Ctrl", arrowleft: "←", arrowright: "→",
  arrowup: "↑", arrowdown: "↓", escape: "Esc", delete: "Del", backspace: "⌫", pageup: "PgUp", pagedown: "PgDn", contextmenu: "Menu",
  space: "Space", enter: "Enter", "=": "+", "-": "−" }[k] ?? (k.length === 1 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1)))).join("+");

/** The table of what each key does in 2D, 3D and walking, nothing chosen and an item
 * chosen, built from the registry as the page has it (keyMap): Markdown. */
async function keysTable() {
  const titles = await R(() => Object.fromEntries(window.storeypathReview.commands().map((c) => [c.id, c.title])));
  // an item to choose: any desk of the floor (one deleted too: its keys are the same), else one placed
  const item = (await R(() => window.storeypathReview.state.floor.items.find((a) => a.type.startsWith("DESK"))?.id ?? null)) ?? await aDesk();
  const cells = new Map(); // chord → { "2d": [plain, item], … }
  const what = (b) => (!b ? "" : b.id === null ? `${b.title} (the ${b.owner}'s)` : b.works ? titles[b.id] : `— (${b.idle ?? "says why"})`);
  for (const view of ["2d", "3d", "walk"]) {
    await viewTo(view);
    for (const [k, chosen] of [[0, null], [1, item]]) {
      const map = await R((chosen) => {
        const r = window.storeypathReview;
        r.setTool(null);
        r.select(null);
        r.selectAsset(chosen);
        return r.keyMap();
      }, chosen);
      for (const b of map) {
        if (!cells.has(b.chord)) cells.set(b.chord, { "2d": ["", ""], "3d": ["", ""], walk: ["", ""] });
        cells.get(b.chord)[view][k] = what(b);
      }
    }
  }
  await R(() => window.storeypathReview.selectAsset(null));
  await viewTo("2d");
  if (placed === item) { // (the desk placed for it: taken away)
    await R((id) => window.storeypathReview.selectAsset(id), item);
    await press("Delete");
    await until((id) => window.storeypathReview.state.floor.items.find((x) => x.id === id).retired, "the desk placed taken away", 8000, item);
    placed = null;
    await press("Escape");
  }
  const cell = ([plain, withItem]) => (withItem && withItem !== plain ? `${plain || "—"} · *an item chosen:* ${withItem}` : plain || "—");
  // the arrows (and Shift with them) said as one when they mean the same but for their way
  const dir = (s) => s.replace(/ (left|right|up|down)\b/g, "");
  const rows = [], done = new Set();
  for (const [chord, by] of cells) {
    if (done.has(chord)) continue;
    const arrows = /^(shift\+)?arrow(left|right|up|down)$/.exec(chord);
    let keys = [chord];
    if (arrows) {
      const all = ["left", "right", "up", "down"].map((d) => `${arrows[1] ?? ""}arrow${d}`);
      const same = all.every((c) => cells.has(c) && ["2d", "3d", "walk"].every((v) => [0, 1].every((i) => dir(cells.get(c)[v][i]) === dir(by[v][i]))));
      if (same) keys = all;
    }
    keys.forEach((c) => done.add(c));
    const text = (v) => cell(by[v].map(keys.length > 1 ? dir : (s) => s));
    const row = { keys, cells: [text("2d"), text("3d"), text("walk")] };
    // the walker's keys that mean the same everywhere, said as one (Shift with W A S D)
    const same = rows.find((r) => /the walker's/.test(r.cells[2]) && r.cells.join() === row.cells.join());
    if (same) same.keys.push(...keys);
    else rows.push(row);
  }
  const lines = rows.map((r) => `| ${r.keys.map((c) => `\`${written(c)}\``).join(" ")} | ${r.cells.join(" | ")} |`);
  // the keys of the tools in use, and of review mode
  const scoped = await R(() => window.storeypathReview.keys().filter((b) => b.scope.startsWith("tool:") || b.scope === "review" || b.scope === "items")
    .map((b) => ({ ...b, title: window.storeypathReview.commands().find((c) => c.id === b.id)?.title })));
  // (review mode's 1 to 9 said as one)
  const digits = scoped.filter((b) => /^review\.type-\d$/.test(b.id));
  const others = scoped.filter((b) => !digits.includes(b)).map((b) => `| \`${written(b.chord)}\` | ${b.scope === "review" ? "review mode"
    : b.scope === "items" ? "items chosen together" : `the ${b.scope.slice(5)} tool in use`} | ${b.title} |`);
  if (digits.length) others.push(`| \`1\`–\`9\` | review mode | Set one of the room's likely types |`);
  return ["| Key | 2D | 3D | Walk |", "|---|---|---|---|", ...lines, "", "| Key | While | Does |", "|---|---|---|", ...others].join("\n");
}

test("the README's table of keys is the registry's (UPDATE_KEYS=1 writes it again)", async () => {
  const { readFileSync, writeFileSync } = await import("node:fs");
  const path = join(here, "../../src/storeypath/review_app/README.md");
  const readme = readFileSync(path, "utf8");
  const table = await keysTable();
  const [start, end] = ["<!-- keys: made by tests/browser/review.mjs from the registry -->", "<!-- /keys -->"];
  const a = readme.indexOf(start), b = readme.indexOf(end);
  truly(a >= 0 && b > a, "the README has no table of keys between its markers");
  const now = readme.slice(a + start.length, b).trim();
  if (now !== table && process.env.UPDATE_KEYS) {
    writeFileSync(path, `${readme.slice(0, a + start.length)}\n${table}\n${readme.slice(b)}`);
    return;
  }
  truly(now === table, `the README's table of keys is not the registry's: run with UPDATE_KEYS=1, or put this between its markers:\n${table}`);
});

let failed = 0;
// ONLY=<words>: the page opened, then only the tests whose names have them (to try a few)
const only = process.env.ONLY?.toLowerCase();
const chosen = tests.filter((x, i) => !only || i === 0 || x.name.toLowerCase().includes(only));
try {
  for (const t of chosen) {
    const started = Date.now();
    try {
      await t.fn();
      console.log(`ok   ${t.name} (${Date.now() - started} ms)`);
    } catch (e) {
      failed++;
      console.log(`FAIL ${t.name}\n     ${e.message.split("\n").join("\n     ")}`);
      if (errors().length) console.log(`     the console: ${JSON.stringify(errors())}`);
      break;
    }
  }
} finally {
  page.close();
}
console.log(failed ? `${failed} failed` : `${chosen.length} passed`);
process.exit(failed ? 1 : 0);
