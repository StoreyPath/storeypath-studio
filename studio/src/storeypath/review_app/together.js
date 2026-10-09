// Many people on one project at once, in Review (Studio's events: web/events.py,
// web/live.py). The page keeps one stream open on the floor it shows: what others
// change there comes as it is saved (Review refreshes it in place and says who did
// what), who is viewing and who is editing the floor (one editor a floor at a time: the
// first change takes it; Done editing, or leaving the floor, lets it go; an admin may
// take it over), and the progress of jobs. Each person undoes and redoes their own
// changes (the buttons; ⌘Z and ⇧⌘Z / Ctrl+Z and Ctrl+Y are Review's keys: step()), and
// the History drawer lists who changed what on the floor, live. Review's top bar shows
// who is here (presence()), its status bar who is editing, a banner when someone else is.

import { ProjectStream } from "./stream.js";

const $ = (id) => document.getElementById(id);

/** This page, as Studio is told with each call (X-StoreyPath-Page): a change it made
 * comes back on the stream marked so, and is not refreshed nor told twice. */
export const PAGE = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)).replace(/[^A-Za-z0-9-]/g, "");

const live = {
  code: null, base: null, me: null, hooks: null,
  stream: null, floor: null, // the stream (stream.js), on the floor shown
  lock: null, // who is editing the floor shown: {who: {id, name, username}, since}, or null
  viewing: [], // who else has it open
  steps: { undo: null, redo: null },
  historyOpen: false, historyTimer: 0, stepsTimer: 0,
};

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

const isMe = (p) => Boolean(p && live.me && p.id === live.me.id);
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** A person as a small round mark with their initial (their name on hover). */
export function avatar(p, cls = "") {
  const name = p?.name || p?.username || "?";
  let hue = 0;
  for (const c of p?.id || name) hue = (hue * 31 + c.charCodeAt(0)) % 360;
  return el("span", { class: `avatar ${cls}`.trim(), title: name, style: `--hue:${hue}`, "aria-label": name },
    name.slice(0, 1).toUpperCase());
}

// ---- the stream -----------------------------------------------------------------------

/** Start: ``hooks`` are Review's (request, toast, floor, editableByAccess, changed, elsewhere,
 * reload, showLock, status, presence). */
export function setupTogether({ code, base, me, hooks }) {
  Object.assign(live, { code, base, me, hooks });
  $("undo").addEventListener("click", () => step(false));
  $("redo").addEventListener("click", () => step(true));
  $("history-open").addEventListener("click", () => showHistory(!live.historyOpen));
  $("history-close").addEventListener("click", () => showHistory(false));
  // leaving the page: the floor let go (when it is ours), as leaving it for another does
  window.addEventListener("pagehide", () => leave(live.floor));
}

/** Who is on the floor shown: who is editing it ({who, since}, or null), whether that is
 * the person, and who else is viewing it. */
export function presence() {
  return { lock: live.lock, mine: Boolean(live.lock && isMe(live.lock.who)), viewing: live.viewing.slice(), me: live.me };
}

export const historyOpen = () => live.historyOpen;

/** The stream opened on the floor shown (the one before it closed, and let go when ours). */
export function onFloor(floor, lock) {
  if (live.floor && live.floor !== floor) leave(live.floor);
  live.lock = lock || null;
  live.viewing = [];
  if (live.floor !== floor || !live.stream) {
    live.stream?.close();
    live.floor = floor;
    const stream = (live.stream = new ProjectStream(live.code, floor, live.hooks.request));
    stream.reopened = () => live.hooks.reload(); // what changed while it was lost
    stream.on("change", heardChange).on("presence", heardPresence).on("deleted", () => {
      live.hooks.toast("This project was deleted", true);
      stream.close();
    });
  }
  render();
  refreshSteps();
  if (live.historyOpen) loadHistory();
}

function heardChange(c) {
  if (c.floor !== live.floor) { // another floor's, or the building's: of note to the 3D view
    live.hooks.elsewhere?.(c);
    return;
  }
  refreshSteps();
  if (live.historyOpen) loadHistorySoon();
  if (c.page === PAGE) return; // made here: shown already
  live.hooks.changed(c, isMe(c.who) ? null : `${c.who.name} ${c.line}`);
}

function heardPresence(p) {
  if (p.floor !== live.floor) return;
  const was = live.lock?.who?.id;
  live.lock = p.editing;
  live.viewing = p.viewing.filter((v) => !isMe(v));
  render();
  if (was !== live.lock?.who?.id) live.hooks.showLock();
}

/** What a change refused told of the floor's editor (423). */
export function heardLocked(locked) {
  if (!locked || locked.floor !== live.floor) return;
  live.lock = { who: locked.who, since: locked.since };
  render();
  live.hooks.showLock();
}

/** Whether someone else is editing the floor shown (then it is only looked at). */
export function lockedByOther() {
  return Boolean(live.lock && !isMe(live.lock.who));
}

/** The floor let go: Done editing, or gone from it (keepalive: also as the page closes). */
function leave(floor, { said = false } = {}) {
  if (!floor || !(live.lock && isMe(live.lock.who)) || floor !== live.floor) return Promise.resolve();
  if (!said) live.lock = null;
  return fetch(`/api/${live.base}/floors/${encodeURIComponent(floor)}/release`, {
    method: "POST", keepalive: true, body: "{}",
    headers: { "X-StoreyPath": "1", "Content-Type": "application/json", "X-StoreyPath-Page": PAGE },
  }).catch(() => {});
}

// ---- who is here, who is editing ---------------------------------------------------------

function render() {
  const editor = live.lock?.who;
  const others = live.viewing.filter((p) => p.id !== editor?.id);
  const mark = (p, what, cls = "") => {
    const a = avatar(p, cls);
    a.removeAttribute("title");
    a.dataset.tip = `${p.name || p.username} · ${what}`;
    a.setAttribute("aria-label", a.dataset.tip);
    return a;
  };
  $("presence").replaceChildren(...[
    editor && !isMe(editor) ? mark(editor, `editing since ${clock(live.lock.since)}`, "editing") : null,
    ...others.slice(0, 4).map((p) => mark(p, "viewing")),
    others.length > 4 ? el("span", { class: "more", "data-tip": others.slice(4).map((p) => p.name || p.username).join(", ") }, `+${others.length - 4}`) : null,
  ].filter(Boolean));

  const banner = $("lock-banner");
  if (editor && !isMe(editor) && live.hooks.editableByAccess()) {
    const take = live.me?.role === "admin" || live.me?.local
      ? el("button", { type: "button", class: "btn-sm", onclick: takeOver, "data-tip": "Edit it yourself: they can no longer save changes to it" }, "Take over")
      : null;
    banner.className = "lock-banner other";
    banner.replaceChildren(...[avatar(editor, "editing"),
      el("span", { class: "lb-text" }, el("strong", {}, editor.name), ` is editing this floor since ${clock(live.lock.since)}: you can look, and edit when they are done`),
      take].filter(Boolean));
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
  live.hooks.presence?.();
}

/** The person done editing the floor: others may edit it (their next change takes it again). */
export async function doneEditing() {
  await leave(live.floor, { said: true });
  live.lock = null;
  render();
  live.hooks.showLock();
}

export async function takeOver() {
  const who = live.lock?.who?.name || "they";
  if (!confirm(`Take over this floor from ${who}? They can no longer save changes to it, and are told so.`)) return;
  try {
    await live.hooks.request(`${live.base}/floors/${encodeURIComponent(live.floor)}/take-over`, {});
    live.lock = { who: { id: live.me.id, name: live.me.name || live.me.username }, since: new Date().toISOString() };
    render();
    live.hooks.showLock();
    live.hooks.toast(`You are editing this floor now (taken over from ${who})`);
  } catch (e) {
    live.hooks.toast(`Not taken over: ${e.message}`, true);
  }
}

// ---- jobs, followed on the stream -----------------------------------------------------------

/** A job followed to its end (its latest line said with ``say``; null once it ended): on
 * the floor's stream. Resolves with the job; throws when it failed. */
export async function followJob(job, say) {
  const follow = live.stream ? live.stream.follow(job, (log) => say(log.at(-1))) : Promise.resolve(job);
  job = await follow;
  say(null);
  if (!job) throw new Error("the page went to another floor");
  if (job.state === "failed") throw new Error(job.error || "failed");
  return job;
}

// ---- undo and redo: each person their own ------------------------------------------------

function refreshSteps() {
  clearTimeout(live.stepsTimer);
  live.stepsTimer = setTimeout(async () => {
    const floor = live.floor;
    try {
      const h = await live.hooks.request(`${live.base}/history?floor=${encodeURIComponent(floor)}&n=0`);
      if (floor !== live.floor) return;
      live.steps = { undo: h.undo, redo: h.redo };
    } catch {
      live.steps = { undo: null, redo: null };
    }
    showSteps();
  }, 120);
}

function showSteps() {
  const { undo, redo } = live.steps;
  $("undo").disabled = !undo;
  $("redo").disabled = !redo;
  $("undo").dataset.tip = undo ? `Undo: ${undo.line}` : "Nothing of yours to undo on this floor";
  $("redo").dataset.tip = redo ? `Redo: ${redo.line.replace(/^undid: /, "")}` : "Nothing of yours to redo on this floor";
  live.hooks.steps?.(live.steps);
}

/** What the person would undo and redo now ({undo, redo}: {line} or null). */
export const steps = () => live.steps;

let stepping = false;

/** The person's latest change on the floor undone (``redo``: their latest undoing redone). */
export async function step(redo) {
  if (stepping || !live.floor) return;
  if (!live.hooks.editableByAccess()) return live.hooks.toast("View only: you may look at this floor, not change it", true);
  if (lockedByOther()) return live.hooks.toast(`${live.lock.who.name} is editing this floor: you can edit when they are done`, true);
  stepping = true;
  try {
    const done = await live.hooks.request(`${live.base}/${redo ? "redo" : "undo"}`, { floor: live.floor });
    const line = redo ? done.line.replace(/^undid: /, "") : done.line;
    live.hooks.toast(`${redo ? "Redone" : "Undone"}: ${line}`);
    if (done.job) {
      live.hooks.status("Reading the floor again…");
      await followJob(done.job, (s) => live.hooks.status(s ?? ""));
    }
    await live.hooks.reload();
  } catch (e) {
    live.hooks.toast(`Not ${redo ? "redone" : "undone"}: ${e.message}`, true);
  } finally {
    stepping = false;
    refreshSteps();
  }
}

// ---- the History panel -----------------------------------------------------------------------

/** The History drawer open (or closed). */
export function showHistory(on) {
  live.historyOpen = on;
  $("history").hidden = !on;
  $("history-open").setAttribute("aria-pressed", String(on));
  $("history-open").classList.toggle("active", on);
  if (on) {
    loadHistory();
    $("history-close").focus({ preventScroll: true });
  }
}

function loadHistorySoon() {
  clearTimeout(live.historyTimer);
  live.historyTimer = setTimeout(loadHistory, 250);
}

async function loadHistory() {
  const floor = live.floor;
  if (!floor) return;
  let h;
  try {
    h = await live.hooks.request(`${live.base}/history?floor=${encodeURIComponent(floor)}&n=100`);
  } catch (e) {
    $("history-list").replaceChildren(el("li", { class: "meta" }, `Not shown: ${e.message}`));
    return;
  }
  if (floor !== live.floor) return;
  live.steps = { undo: h.undo, redo: h.redo };
  showSteps();
  $("history-floor").textContent = live.hooks.floorName();
  $("history-list").replaceChildren(...(h.entries.length ? h.entries.map((e) => el("li", {
    class: [e.undone ? "undone" : "", e.mine ? "mine" : "", h.undo?.seq === e.seq ? "next-undo" : ""].join(" ").trim(),
    title: new Date(e.at).toLocaleString() },
  avatar(e.who),
  el("div", { class: "what" },
    el("div", {}, el("strong", {}, e.mine ? "You" : e.who.name), " ", e.line),
    el("div", { class: "meta" }, when(e.at), e.undone ? el("span", { class: "tag" }, "undone") : null,
      h.undo?.seq === e.seq ? el("span", { class: "tag next" }, "⌘Z undoes this") : null)))) :
    [el("li", { class: "meta" }, "No changes on this floor yet.")]));
}

function when(iso) {
  const t = new Date(iso), now = new Date();
  const s = (now - t) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return t.toDateString() === now.toDateString() ? clock(iso) : t.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}
