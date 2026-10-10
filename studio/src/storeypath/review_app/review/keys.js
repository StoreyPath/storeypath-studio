// The keyboard map: one place where every key is bound to a command (commands.js), in
// scopes. A key is looked up in the scopes that apply now, the first that has it wins:
//
//   review   reviewing a floor's rooms one by one (1–9 a type, Enter accept, N / P)
//   walk     walking: W A S D, the arrows and Shift are the walker's (reserved for it);
//            E a door (the world's, first), else up the stairs; Q down
//   item     an item chosen: R and , . turn it, the arrows move it (walking: R , . only) —
//            whatever tool is in use: what is chosen wins over the tool
//   tool     the tool in use (as "tool:space": Enter closes a space, Backspace a corner)
//   3d       in 3D and walking: 2 shows the place under the pointer on the plan
//   2d       on the plan: Space held moves it (pointer.js's, reserved here)
//   global   everywhere else
//
// Context wins: a key means what the thing chosen, the view or the tool make of it, and
// only letters for tools and 2 3 4 change the tool or the view. A tool's key where the
// tool does not work says why (in the status bar) and changes nothing.
//
// Within a scope a key has one command: binding it twice is an error (said in the
// console, and the browser tests check there is none). Keys others handle (the walker's,
// the plan's Space) are reserved in their scope with who handles them, so that the map
// says them too, and nothing of Review hears them there (effectiveMap: what each key
// does now). Keys typed in a field are the field's, but for the few marked ``inFields``
// (the palette's ⌘K). Chords are written "mod+shift+z" (mod: ⌘ on a Mac, Ctrl
// elsewhere), "pageup", "[", "?".

import { el } from "./dom.js";

export const SCOPES = ["review", "walk", "item", "tool", "3d", "2d", "global"];
export const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

const bindings = new Map(); // scope (or "tool:<id>") → chord → command id (null: reserved for another's handler)
const owners = new Map(); // scope → chord → { owner, title }: who handles a key reserved there
const conflicts = [];
let scopesNow = () => ["global"];
let runCommand = () => false;
const IN_FIELDS = new Set(["mod+k", "escape"]);

/** Normalised: modifiers in one order, the key lower case. */
export function normal(chord) {
  const parts = chord.toLowerCase().split("+");
  const key = parts.pop() || "+";
  const mods = ["mod", "ctrl", "alt", "shift"].filter((m) => parts.includes(m));
  return [...mods, key].join("+");
}

export function bind(chord, scope, id) {
  const c = normal(chord);
  if (!bindings.has(scope)) bindings.set(scope, new Map());
  const map = bindings.get(scope);
  if (map.has(c) && map.get(c) !== id) {
    conflicts.push({ scope, chord: c, ids: [map.get(c), id] });
    console.error(`key ${c} in ${scope}: both ${map.get(c)} and ${id}`);
    return;
  }
  map.set(c, id);
}

/** Keys bound twice in one scope (none, when the map is right). */
export const keyConflicts = () => conflicts.slice();

/** Every binding: [{scope, chord, id}]. */
export function allBindings() {
  return [...bindings].flatMap(([scope, map]) => [...map].map(([chord, id]) => ({ scope, chord, id })));
}

/** The keys of a command, in the scope it is bound in. */
export function keysOf(id) {
  return allBindings().filter((b) => b.id === id).map((b) => b.chord);
}

const NAMES = { escape: "escape", esc: "escape", " ": "space", spacebar: "space", del: "delete", return: "enter",
  "+": "=", add: "=", subtract: "-", apps: "contextmenu" };

/** The chord a key press makes. Shift is kept for letters, digits and the named keys, and
 * left out for a character it makes (? is shift+/ on most keyboards: it is "?"). */
export function chordOf(e) {
  let key = e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase();
  key = NAMES[key] || key;
  if (/^Key[A-Z]$/.test(e.code) && (e.altKey || e.metaKey || e.ctrlKey)) key = e.code.slice(3).toLowerCase(); // ⌥ letters
  if (/^Digit\d$/.test(e.code)) key = e.code.slice(5);
  const printable = key.length === 1 && !/[a-z0-9]/.test(key);
  const mods = [];
  if (MAC ? e.metaKey : e.ctrlKey) mods.push("mod");
  if (MAC && e.ctrlKey) mods.push("ctrl");
  if (e.altKey) mods.push("alt");
  if (e.shiftKey && !printable) mods.push("shift");
  return [...mods, key].join("+");
}

const typing = (t) => t?.closest?.("input:not([type=checkbox]):not([type=radio]):not([type=button]), select, textarea, [contenteditable]");

/** Start: ``scopes()`` says which scopes apply now, in order; ``run(id, e)`` runs a command
 * (and whether it did). */
export function setupKeys({ scopes, run }) {
  scopesNow = scopes;
  runCommand = run;
  document.addEventListener("keydown", onKey);
}

function onKey(e) {
  if (e.defaultPrevented || e.isComposing) return;
  const chord = chordOf(e);
  if (typing(e.target) && !IN_FIELDS.has(chord)) return;
  // Enter and Space on a button, a link or a tab are theirs
  if ((chord === "enter" || chord === "space") && e.target.closest?.("button, a[href], [role=button], [role=tab], [role=option], summary")) return;
  if (e.target.closest?.("dialog[open]")) return; // a dialog's keys are its own (Esc closes it)
  for (const scope of scopesNow()) {
    const id = bindings.get(scope)?.get(chord);
    if (id === undefined) continue;
    if (id === null) return; // reserved here (the walker's keys): nothing else hears it
    if (e.repeat && !runCommand.repeats?.(id)) {
      e.preventDefault();
      return;
    }
    if (runCommand(id, e) !== false) e.preventDefault();
    return;
  }
}

/** Keys reserved in a scope for another's handler (``owner``: "walker", "plan"…, doing
 * ``title``): nothing of Review hears them there, and the map says whose they are. */
export function reserve(chords, scope, owner = "another", title = "") {
  if (!bindings.has(scope)) bindings.set(scope, new Map());
  if (!owners.has(scope)) owners.set(scope, new Map());
  for (const c of chords) {
    const k = normal(c), was = bindings.get(scope).get(k);
    if (was) {
      conflicts.push({ scope, chord: k, ids: [was, `${owner}'s`] });
      console.error(`key ${k} in ${scope}: both ${was} and the ${owner}'s`);
      continue;
    }
    bindings.get(scope).set(k, null);
    owners.get(scope).set(k, { owner, title });
  }
}

/** What each key does now: for each chord bound in a scope that applies, the first such
 * scope's — { chord, scope, id (null: reserved), owner, title (a reserved key's) }. */
export function effectiveMap(scopes = scopesNow()) {
  const out = new Map();
  for (const scope of scopes) {
    for (const [chord, id] of bindings.get(scope) ?? []) {
      if (out.has(chord)) continue;
      const own = id === null ? owners.get(scope)?.get(chord) ?? { owner: "another", title: "" } : null;
      out.set(chord, { chord, scope, id, owner: own?.owner ?? null, title: own?.title ?? "" });
    }
  }
  return out;
}

/** The scopes that apply now, the first first. */
export const scopesApplying = () => scopesNow();

/** The key that runs a command now, as the map has it (its first chord that resolves to it
 * in the scopes that apply), or "" when none does (another's here, or not this view's). */
export function keyNow(id) {
  const map = effectiveMap();
  return keysOf(id).find((c) => map.get(c)?.id === id) || "";
}

// ---- how keys are written -----------------------------------------------------------------

const MAC_MODS = { mod: "⌘", ctrl: "⌃", alt: "⌥", shift: "⇧" };
const PC_MODS = { mod: "Ctrl", ctrl: "Ctrl", alt: "Alt", shift: "Shift" };
const KEY_NAMES = { escape: "Esc", enter: "Enter", backspace: "⌫", delete: "Del", space: "Space", pageup: "PgUp", pagedown: "PgDn",
  arrowup: "↑", arrowdown: "↓", arrowleft: "←", arrowright: "→", "=": "+", "-": "−", tab: "Tab", home: "Home", end: "End",
  contextmenu: "Menu" };

/** A chord as its keys' caps: ["⇧", "⌘", "Z"] on a Mac, ["Ctrl", "Shift", "Z"] elsewhere. */
export function caps(chord) {
  const parts = normal(chord).split("+");
  const key = parts.pop();
  const name = KEY_NAMES[key] || (key.length === 1 ? key.toUpperCase() : key[0].toUpperCase() + key.slice(1));
  if (MAC) return [...["ctrl", "alt", "shift", "mod"].filter((m) => parts.includes(m)).map((m) => MAC_MODS[m]), name];
  return [...parts.map((m) => PC_MODS[m]), name];
}

/** A chord as text: "⇧⌘Z", "Ctrl+Shift+Z". */
export function keyText(chord) {
  const c = caps(chord);
  return MAC ? c.join("") : c.join("+");
}

/** A chord as <kbd>s. */
export function kbd(chord) {
  return caps(chord).map((k) => el("kbd", {}, k));
}
