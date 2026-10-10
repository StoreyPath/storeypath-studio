// node navigate.mjs <Studio's address> <project code>: the Find the way page in headless
// Chrome, used as a person uses it (the viewers' harness: real clicks and keys over the
// DevTools protocol), on the demo project that `storeypath review` serves
// (tests/test_navigate_browser.py starts it). The way from the Headquarters' entrance to
// an office two floors up, by the stairs: in words, on the plan, played, swapped,
// step-free, in the light theme, in 3D. The run stops at the first failure and Chrome
// quits with it. Waits are by what the page shows, never by the clock alone: drawn in
// software, frames are slow.

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const { launch } = await import(pathToFileURL(join(here, "../../../storeypath-viewer/viewer/svg/test/harness.mjs")).href);

const [base, code] = process.argv.slice(2);
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const truly = (v, what) => {
  if (!v) throw new Error(what);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const page = await launch({ webgl: true, timeout: 800 });
const R = (fn, ...args) => page.run(fn, ...args);

/** Waits until ``fn`` (run in the page) gives something true; that. */
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

const KEYS = { ArrowUp: 38, ArrowDown: 40, Enter: 13 };
async function press(key) {
  await page.send("Input.dispatchKeyEvent", { type: "keyDown", key, code: key, windowsVirtualKeyCode: KEYS[key] ?? 0 });
  await page.send("Input.dispatchKeyEvent", { type: "keyUp", key, code: key, windowsVirtualKeyCode: KEYS[key] ?? 0 });
  await sleep(60);
}
/** Where an element is on the page: its middle. */
const middleOf = (selector) => R((s) => {
  const r = document.querySelector(s).getBoundingClientRect();
  return [r.left + r.width / 2, r.top + r.height / 2];
}, selector);
const errors = () => page.errors.slice();
const noErrors = (what) => truly(!page.errors.length, `${what}: the console said ${JSON.stringify(page.errors)}`);
const lit = () => R(() => [...document.querySelectorAll("#steps .step.here")].map((li) => Number(li.dataset.i)));
const nav = (fn) => R(fn);

// the way asked for: from the Headquarters' entrance on the ground floor to OFFICE 205, two floors up
const api = async (path) => (await fetch(`${base}/api/${path}`)).json();
const network = await api(`projects/${code}/buildings/${code}-DEMO-HQ/navigation`);
const office = network.places.find((p) => p.label === "OFFICE 205").id;
const entrance = network.nodes.find((n) => n.kind === "entrance" && n.floor_id.endsWith("-F00")).id;
const URL_ = `${base}/navigate.html?p=${code}&building=${code}-DEMO-HQ&from=${encodeURIComponent(entrance)}&to=${office}`;
const { route } = await api(`projects/${code}/buildings/${code}-DEMO-HQ/navigation?from=${encodeURIComponent(entrance)}&to=${office}`);

test("the page opens on the way: its summary, a card a step, the plan calm with the way on it, the floors it goes over numbered", async () => {
  await page.open(URL_, 1440, 900);
  await R(() => { localStorage.removeItem("storeypath.navigate.view"); localStorage.setItem("storeypath.theme", "dark"); });
  await page.open(URL_, 1440, 900);
  await until(() => document.querySelectorAll("#steps .step").length && document.querySelector(".sp-route[data-sp-route]"), "the way shown");
  const got = await R(() => ({
    metres: document.getElementById("sum-metres").textContent, time: document.getElementById("sum-time").textContent,
    sub: document.getElementById("sum-sub").textContent,
    steps: [...document.querySelectorAll("#steps .step")].map((li) => [li.className.split(" ")[1], li.querySelector(".step-text").textContent,
      Boolean(li.querySelector(".step-icon svg, .step-icon:empty"))]),
    heads: [...document.querySelectorAll("#steps .floor-head")].map((h) => h.textContent),
    turns: document.querySelectorAll(".step-turns li").length,
    floors: [...document.querySelectorAll("#floors button")].map((b) => [b.querySelector(".leg")?.textContent ?? null, b.classList.contains("active")]),
    plan: [...document.querySelector(".sp-plan").classList], from: document.getElementById("from-input").value,
    to: document.getElementById("to-input").value, play: document.getElementById("play").textContent.trim(),
  }));
  truly(got.metres === `${Math.round(route.metres)} m` && /min|s$/.test(got.time) && got.sub.includes("by stairs") && got.sub.includes("floors"),
    `the summary: ${JSON.stringify([got.metres, got.time, got.sub])}`);
  truly(JSON.stringify(got.steps.map((s) => [s[0], s[1]])) === JSON.stringify(route.steps.map((s) => [s.kind, s.text])) && got.steps.every((s) => s[2]),
    `a card a step, each with its picture: ${JSON.stringify(got.steps)}`);
  truly(got.heads.length === 2 && got.turns >= 1, `a heading a floor, turns along the walks: ${JSON.stringify(got)}`);
  truly(JSON.stringify(got.floors) === JSON.stringify([["1", true], [null, false], ["2", false]]), `the floors: ${JSON.stringify(got.floors)}`);
  truly(got.plan.includes("sp-style-wayfinding") && got.plan.includes("sp-theme-dark"), `the plan calm, dark as the page: ${got.plan}`);
  truly(got.from.startsWith("Entrance into") && got.to === "OFFICE 205" && got.play === "Play", JSON.stringify(got));
  const marks = await R(() => ({ start: Boolean(document.querySelector("[data-sp-route-start]")),
    change: document.querySelector("[data-sp-route-change]")?.textContent ?? null }));
  truly(marks.start && marks.change?.includes("Up to Floor 2"), `its marks: ${JSON.stringify(marks)}`);
  noErrors("opening");
});

test("a step clicked is lit, and the plan shows its floor and frames it", async () => {
  const last = route.steps.length - 1;
  const cam = await R(() => document.querySelector(".sp-plan").dataset.cam);
  await page.click(...await middleOf(`#step-${last}`));
  await until((i) => document.querySelector(`#step-${i}`).classList.contains("here"), "the arrival lit", 5000, last);
  await until(() => document.querySelector("[data-sp-route-end]") && document.querySelector("#floors button.active .leg")?.textContent === "2",
    "the plan on the floor it ends on");
  await until((before) => document.querySelector(".sp-plan").dataset.cam !== before, "the view framing it", 10000, cam);
  const got = await R(() => ({ card: document.querySelector(".sp-route-card")?.textContent, room: Boolean(document.querySelector("[data-sp-route-room]")) }));
  truly(got.card === "OFFICE 205 · Floor 2" && got.room, `its card and its room: ${JSON.stringify(got)}`);
  // a floor's tab: that floor, its part of the way framed
  await page.click(...await middleOf("#floors button:first-child"));
  await until(() => document.querySelector("[data-sp-route-start]"), "the first floor, by its tab");
  noErrors("a step clicked");
});

test("↑ and ↓ go through the steps, each shown", async () => {
  await R(() => document.getElementById("steps").focus());
  await press("ArrowUp");
  let now = await lit();
  truly(now.length === 1 && now[0] === route.steps.length - 2, `↑: the step before: ${now}`);
  await press("ArrowUp");
  await press("ArrowDown");
  now = await lit();
  truly(now[0] === route.steps.length - 2, `↓: the next: ${now}`);
  // the take step: the plan on the floor the stairs are taken from
  const take = route.steps.findIndex((s) => s.kind === "take");
  while ((await lit())[0] > take) await press("ArrowUp");
  await until(() => document.querySelector("[data-sp-route-change='to']"), "the stairs on the floor they are taken from");
  noErrors("the arrows");
});

test("Play walks the way on the plan, floor by floor: Pause, the bar filling, each step lit in turn, Again at its end", async () => {
  await page.click(...await middleOf("#play"));
  await until(() => document.getElementById("play").textContent.trim() === "Pause", "Pause, while it plays");
  const seen = new Set();
  const end = Date.now() + 120000;
  let walker = false, bar = 0;
  while (Date.now() < end) {
    const s = await R(() => ({ lit: [...document.querySelectorAll("#steps .step.here")].map((li) => Number(li.dataset.i)),
      label: document.getElementById("play").textContent.trim(), walker: Boolean(document.querySelector("[data-sp-route-walker]")),
      bar: parseFloat(document.querySelector("#progress span").style.width) || 0 }));
    s.lit.forEach((i) => seen.add(i));
    walker ||= s.walker;
    bar = Math.max(bar, s.bar);
    if (s.label === "Again") break;
    await sleep(80);
  }
  truly(await R(() => document.getElementById("play").textContent.trim() === "Again"), "played to its end");
  truly(walker && bar > 50, `a walker went along it, the bar filling: ${walker} ${bar}`);
  truly(route.steps.every((_, i) => seen.has(i) || route.steps[i].kind === "start"), `each step lit in turn: ${[...seen]}`);
  truly((await lit())[0] === route.steps.length - 1 && await R(() => Boolean(document.querySelector("[data-sp-route-end]"))),
    "at its end: the arrival lit, the floor it ends on");
  // again, paused part way: Play on and From the start
  await page.click(...await middleOf("#play"));
  await until(() => document.getElementById("play").textContent.trim() === "Pause", "playing again");
  await page.click(...await middleOf("#play"));
  await until(() => document.getElementById("play").textContent.trim() === "Play on" && !document.getElementById("restart").hidden, "paused");
  await page.click(...await middleOf("#restart"));
  await until(() => document.getElementById("play").textContent.trim() === "Pause", "from the start");
  await page.click(...await middleOf(`#step-0`)); // a step chosen: playing stops
  await until(() => document.getElementById("play").textContent.trim() === "Play" && !document.querySelector("[data-sp-route-walker]"), "stopped");
  noErrors("Play");
});

test("From and To swapped: the way back, said in the address", async () => {
  await page.click(...await middleOf("#swap"));
  await until(() => document.getElementById("from-input").value === "OFFICE 205"
    && document.querySelector("#steps .step.start .step-text")?.textContent.includes("OFFICE 205"), "the way back");
  truly(await R(() => new URLSearchParams(location.search).get("from").endsWith("-0136") || location.search.includes("0136")), "the address");
  await page.click(...await middleOf("#swap"));
  await until(() => document.getElementById("to-input").value === "OFFICE 205" && document.querySelectorAll("#steps .step").length, "and back again");
  noErrors("swapped");
});

test("step-free: the way by the lift, no stairs", async () => {
  await page.click(...await middleOf(".step-free input"));
  await until(() => document.getElementById("sum-sub").textContent.includes("by lift")
    && [...document.querySelectorAll("#steps .step.take .step-text")].every((t) => t.textContent.includes("lift")), "by the lift");
  truly(await R(() => document.getElementById("sum-sub").textContent.includes("step-free") && location.search.includes("accessible=1")), "said so");
  await until(() => document.querySelector("[data-sp-route-change]")?.textContent.includes("Up to Floor 2"), "the lift on the plan");
  await page.click(...await middleOf(".step-free input"));
  await until(() => document.getElementById("sum-sub").textContent.includes("by stairs"), "by the stairs again");
  noErrors("step-free");
});

test("the light theme: the page and the plan light, the way's marks the same", async () => {
  await page.click(...await middleOf("#theme-toggle"));
  await until(() => document.documentElement.dataset.theme === "light" && document.querySelector(".sp-plan").classList.contains("sp-theme-light"),
    "the plan light with the page");
  const got = await R(() => ({ background: getComputedStyle(document.body).backgroundColor,
    outline: getComputedStyle(document.querySelector(".sp-outline path")).fill, marks: document.querySelectorAll("[data-sp-route-start], [data-sp-route-change]").length }));
  truly(got.outline === "rgb(247, 246, 242)" && got.marks >= 2, `light: ${JSON.stringify(got)}`);
  await page.click(...await middleOf("#theme-toggle"));
  await until(() => document.querySelector(".sp-plan").classList.contains("sp-theme-dark"), "dark again");
  noErrors("the light theme");
});

test("3D: the way through the building, a step clicked framed there, Fly along to the destination", async () => {
  await page.click(...await middleOf("#view-mode [data-view='3d']"));
  await until(() => document.querySelector("#world3d canvas") && document.querySelector("#world3d .sp3d-route-tag"), "the way in 3D", 60000);
  const world = await R(() => {
    const w = window.storeypathNavigate.state.world;
    return { route: Boolean(w.route), legs: (() => { let n = 0; w.scene.traverse((o) => { if (o.name.startsWith("route:leg:")) n++; }); return n; })() };
  });
  truly(world.route && world.legs === route.legs.length, `drawn: ${JSON.stringify(world)}`);
  const take = route.steps.findIndex((s) => s.kind === "take");
  await page.click(...await middleOf(`#step-${take}`));
  await until((i) => window.storeypathNavigate.state.world.routeStep === i, "the stairs framed in 3D", 10000, take);
  await page.click(...await middleOf("#fly"));
  await until(() => window.storeypathNavigate.state.world.routePlay === "playing", "flying along");
  await until(() => window.storeypathNavigate.state.world.routePlay === null, "there", 120000);
  truly((await lit())[0] === route.steps.length - 1, "the arrival lit as it got there");
  await page.click(...await middleOf("#view-mode [data-view='plan']"));
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
      if (errors().length) console.log(`     the console: ${JSON.stringify(errors())}`);
      break;
    }
  }
} finally {
  page.close();
}
console.log(failed ? `${failed} failed` : `${tests.length} passed`);
process.exit(failed ? 1 : 0);
