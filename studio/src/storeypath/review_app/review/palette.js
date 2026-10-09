// The command palette (⌘K, Ctrl+K or /): one box to find anything. Rooms (by name, number,
// type or ID), items (by tag, typed loosely: any case, with or without hyphens; a tag on
// another floor is found too), floors, and every command (commands.js) with its key.
// Arrows move, Enter runs, Esc closes.

import { canRun, allCommands, getCommand, whyNot } from "./commands.js";
import { $, el, icon, save, saved } from "./dom.js";
import { openFloor } from "./floor.js";
import { findAsset, showAsset } from "./items.js";
import { keysOf, kbd } from "./keys.js";
import { toast } from "./notify.js";
import { select, selectAsset } from "./selection.js";
import { code, color, state, title, typeLabel, typeOf, units, visible } from "./state.js";
import { normalizeItemId } from "/viewer/src/ids.js";

// with nothing typed: what was run from here last, then what is most asked for
const SUGGESTED = ["review.start", "tool.place", "tool.wall", "view.3d", "view.walk", "edit.history", "share.export",
  "view.side", "view.labels", "panel.view", "help.keys"];
const recent = () => {
  try {
    return JSON.parse(saved("storeypath.review.recent") || "[]").filter((id) => getCommand(id));
  } catch {
    return [];
  }
};
const remember = (id) => save("storeypath.review.recent", JSON.stringify([id, ...recent().filter((x) => x !== id)].slice(0, 5)));

let dialog = null;
let results = [];
let active = 0;
let returnTo = null;

/** How well ``q`` matches ``text``: its words found at the start of words score most, in
 * order more than anywhere; 0: not at all. */
export function score(q, text) {
  if (!q) return 1;
  const t = (text || "").toLowerCase();
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  let total = 0;
  for (const w of words) {
    const i = t.indexOf(w);
    if (i < 0) {
      // the letters in order (a loose typing): little
      let j = 0;
      for (const ch of t) if (ch === w[j]) j++;
      if (j < w.length) return 0;
      total += 1;
      continue;
    }
    total += i === 0 ? 12 : /[\s\-_·(]/.test(t[i - 1]) ? 8 : 4;
    if (t === w) total += 10;
  }
  return total;
}

function sources(q) {
  const out = [];
  const tag = normalizeItemId(q);
  // items: by tag (loosely) on this floor; a whole tag on another floor is looked up
  const loose = q.toLowerCase().replace(/[^a-z0-9]/g, "");
  for (const a of state.floor?.items || []) {
    if (a.retired && !state.showHidden) continue;
    const t = typeOf(a.type);
    const s = loose.length >= 3 && a.id.toLowerCase().replace(/-/g, "").includes(loose) ? 30
      : score(q, `${t?.name_en || a.type} ${a.id}`) / 2;
    if (q && s > 0) out.push({ group: "Items", score: s, icon: "tag", swatch: t?.color, title: t?.name_en || a.type, sub: a.id,
      run: () => {
        selectAsset(a.id);
        showAsset(a);
      } });
  }
  if (tag && !(state.floor?.items || []).some((a) => a.id === tag)) {
    out.push({ group: "Items", score: 40, icon: "tag", title: `Find item ${tag}`, sub: "on any floor of this project",
      run: () => findAsset(tag).then((r) => r.error && toast(r.error, true)) });
  }
  // rooms of the floor shown
  for (const s of units().filter(visible)) {
    const sc = score(q, `${title(s)} ${s.name || ""} ${s.number || ""} ${typeLabel(s.type)} ${code(s.id)} ${s.id}`);
    if (sc > 0 && q) out.push({ group: "Rooms", score: sc + (s.reasons.length ? 0.5 : 0), swatch: color(s.type), title: title(s),
      sub: `${typeLabel(s.type)} · ${code(s.id)}${s.reasons.length ? " · to review" : ""}`, run: () => select(s.id, { fly: true }) });
  }
  // floors
  for (const f of state.project?.floors || []) {
    const sc = score(q, `${f.name} ${f.building} ${f.location} floor`);
    if (sc > 0 && q) out.push({ group: "Floors", score: sc / 1.5, icon: "layers", title: f.name, sub: `${f.location} · ${f.building}${f.review ? ` · ${f.review} to review` : ""}`,
      current: f.id === state.floor?.id, run: () => openFloor(f.id) });
  }
  // commands
  for (const c of allCommands()) {
    if (!c.palette) continue;
    const sc = score(q, `${c.title} ${c.group} ${c.words || ""}`);
    if (sc <= 0) continue;
    const ok = canRun(c);
    out.push({ group: "Commands", id: c.id, score: sc * 1.1 + (ok ? 1 : 0), icon: c.icon || "command", title: c.title, sub: ok ? c.group : whyNot(c),
      keys: keysOf(c.id).slice(0, 1), disabled: !ok, run: () => {
        remember(c.id);
        c.run();
      } });
  }
  return out;
}

const ORDER = ["Rooms", "Items", "Commands", "Floors"];

function render(q) {
  const list = $("palette-list");
  const found = sources(q.trim());
  // without words: the commands, by group; with: the best of each kind, the best kind first
  let shown;
  if (!q.trim()) {
    const byId = new Map(found.filter((r) => r.group === "Commands" && !r.disabled).map((r) => [r.id, r]));
    const last = recent().map((id) => byId.get(id)).filter(Boolean).map((r) => ({ ...r, group: "Recent" }));
    const picked = new Set(last.map((r) => r.id));
    shown = [...last, ...SUGGESTED.filter((id) => !picked.has(id)).map((id) => byId.get(id)).filter(Boolean).map((r) => ({ ...r, group: "Suggested" }))];
  }
  else {
    const best = new Map();
    for (const r of found) best.set(r.group, Math.max(best.get(r.group) || 0, r.score));
    const groups = [...best.keys()].sort((a, b) => best.get(b) - best.get(a) || ORDER.indexOf(a) - ORDER.indexOf(b));
    shown = groups.flatMap((g) => found.filter((r) => r.group === g).sort((a, b) => b.score - a.score).slice(0, g === "Rooms" ? 8 : 6));
  }
  results = shown;
  active = Math.min(active, Math.max(0, shown.length - 1));
  const parts = [];
  let group = null;
  shown.forEach((r, i) => {
    if (r.group !== group) {
      group = r.group;
      parts.push(el("li", { class: "pl-group", role: "presentation" }, group));
    }
    const li = el("li", { class: `pl-item${i === active ? " active" : ""}${r.disabled ? " disabled" : ""}`, role: "option", id: `pl-${i}`,
      "aria-selected": String(i === active), "aria-disabled": r.disabled ? "true" : null },
    r.swatch ? el("span", { class: "swatch", style: `background:${r.swatch}` }) : icon(r.icon || "command", { size: 15 }),
    el("span", { class: "pl-text" }, el("span", { class: "pl-title" }, r.title), r.sub ? el("span", { class: "pl-sub" }, r.sub) : null),
    r.current ? el("span", { class: "badge" }, "shown") : null,
    r.keys?.length ? el("span", { class: "pl-keys" }, kbd(r.keys[0])) : null);
    li.addEventListener("pointermove", () => {
      if (active !== i) {
        active = i;
        mark();
      }
    });
    li.addEventListener("click", () => choose(i));
    parts.push(li);
  });
  if (!shown.length) parts.push(el("li", { class: "pl-empty" }, "Nothing found"));
  list.replaceChildren(...parts);
  $("palette-input").setAttribute("aria-activedescendant", shown.length ? `pl-${active}` : "");
}

function mark() {
  for (const li of document.querySelectorAll("#palette-list .pl-item")) {
    const on = li.id === `pl-${active}`;
    li.classList.toggle("active", on);
    li.setAttribute("aria-selected", String(on));
    if (on) li.scrollIntoView({ block: "nearest" });
  }
  $("palette-input").setAttribute("aria-activedescendant", results.length ? `pl-${active}` : "");
}

function choose(i) {
  const r = results[i];
  if (!r || r.disabled) return;
  close();
  r.run();
}

export const paletteOpen = () => Boolean(dialog?.open);

export function close() {
  if (!dialog?.open) return;
  dialog.close();
}

export function openPalette() {
  if (!dialog) {
    const input = el("input", { id: "palette-input", type: "text", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-list",
      "aria-autocomplete": "list", autocomplete: "off", spellcheck: "false", placeholder: "Find a room, an item's tag, a floor or a command…" });
    dialog = el("dialog", { class: "dialog palette", "aria-label": "Search and commands" },
      el("div", { class: "pl-search" }, icon("search", { size: 16 }), input, el("kbd", {}, "Esc")),
      el("ul", { id: "palette-list", class: "pl-list", role: "listbox", "aria-label": "Found" }),
      el("div", { class: "pl-foot" }, el("span", {}, kbd("arrowup"), kbd("arrowdown"), " move"), el("span", {}, kbd("enter"), " choose"),
        el("span", {}, "Rooms by name, number, type or ID · items by tag")));
    document.body.append(dialog);
    input.addEventListener("input", () => {
      active = 0;
      render(input.value);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!results.length) return;
        active = (active + (e.key === "ArrowDown" ? 1 : results.length - 1)) % results.length;
        mark();
      } else if (e.key === "Enter") {
        e.preventDefault();
        choose(active);
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
    });
    dialog.addEventListener("close", () => {
      returnTo?.focus?.({ preventScroll: true });
      returnTo = null;
    });
    dialog.addEventListener("click", (e) => { if (e.target === dialog) close(); }); // the backdrop
  }
  if (dialog.open) return;
  returnTo = document.activeElement;
  const input = $("palette-input");
  input.value = "";
  active = 0;
  render("");
  dialog.showModal();
  input.focus();
}
