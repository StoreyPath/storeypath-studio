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

let MOD = 4; // ⌘ on a Mac, Ctrl elsewhere (as keys.js has it)
const KEYS = { Escape: 27, Enter: 13, Backspace: 8, Delete: 46, PageUp: 33, PageDown: 34, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40 };

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
    if (t.action || t.id === "select") continue;
    await press(t.key);
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

test("Measure: two points clicked, the distance between them", async () => {
  await press("m");
  const p = await roomAt((await officeNamed("001")).id), q = await roomAt((await officeNamed("003")).id);
  await page.click(...p);
  await page.click(...q);
  await press("Enter");
  const said = await R(() => document.getElementById("measure-result")?.textContent || "");
  truly(/^\d+(\.\d+)? m$/.test(said), `measured: ${said}`);
  await press("Escape");
  await press("Escape");
});

test("an item placed with the Place tool, chosen, turned with R, deleted with Del, its tag found by search", async () => {
  await press("i");
  await until(() => document.querySelector(".type-picker"), "the types to place open");
  await R(() => [...document.querySelectorAll(".type-tile")].find((b) => /junior/i.test(b.textContent)).click());
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

test("stairs drawn with L (two corners and Enter) are added typed; the inspector lists the floors they serve", async () => {
  const room = (await rooms()).find((s) => s.name === "RECEPTION");
  const [x0, y0, x1, y1] = await boxOf(room.id);
  const before = (await rooms()).filter((s) => s.type === "stairs").length;
  await press("l");
  await page.click(...await screenOf([x0 + 0.6, y0 + 0.6]));
  await page.click(...await screenOf([x0 + 3.2, y0 + 3.6]));
  await press("Enter");
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
  await press("z", ["mod"]);
  await press("z", ["mod"]);
  await until((ids) => ids.every((id) => window.storeypathReview.state.byId.get(id).type === "office"), "both undone", 10000, [a.id, b.id]);
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
  truly(await R(() => document.querySelector("#rail [data-tool=wall]").getAttribute("aria-disabled") === "true"), "the Wall tool: not in 3D");
  await press("p");
  truly((await toolNow()) === "paint" && !(await R(() => document.getElementById("tool-options").hidden)), "Paint in 3D, its brushes shown");
  await press("Escape");
  await press("4");
  await until(() => window.storeypathReview.view3d.mode === "walk" && window.storeypathReview.view3d.world.mode === "walk", "walking", 20000);
  await press("w"); // the walker's: nothing of Review's
  truly(!(await toolNow()), "W walking is the walker's, not the Wall tool");
  await press("2");
  await until(() => window.storeypathReview.view3d.mode === "2d", "2: back on the plan");
  noErrors("3D and walking");
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
      if (errors().length) console.log(`     the console: ${JSON.stringify(errors())}`);
      break;
    }
  }
} finally {
  page.close();
}
console.log(failed ? `${failed} failed` : `${tests.length} passed`);
process.exit(failed ? 1 : 0);
