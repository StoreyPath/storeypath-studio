// The keyboard map: one place where every key is bound to a command (commands.js), in
// scopes. A key is looked up in the scopes that apply now, the first that has it wins:
//
//   review   reviewing a floor's rooms one by one (1–9 a type, Enter accept, N / P)
//   walk     walking: W A S D, the arrows and Shift are the walker's; E the world's (doors)
//   tool     the tool in use (as "tool:space": Enter closes a space, Backspace a corner)
//   item     an item chosen: R and , . turn it, the arrows move it
//   3d       in 3D: a 2D tool's key shows that place on the plan with the tool
//   global   everywhere else
//
// Within a scope a key has one command: binding it twice is an error (said in the
// console, and the browser tests check there is none). Keys typed in a field are the
// field's, but for the few marked ``inFields`` (the palette's ⌘K). Chords are written
// "mod+shift+z" (mod: ⌘ on a Mac, Ctrl elsewhere), "pageup", "[", "?".

import { el } from "./dom.js";

export const SCOPES = ["review", "walk", "tool", "item", "3d", "global"];
export const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

const bindings = new Map(); // scope (or "tool:<id>") → chord → command id
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

/** Keys reserved in a scope: nothing of Review hears them there (the walker's, the world's). */
export function reserve(chords, scope) {
  if (!bindings.has(scope)) bindings.set(scope, new Map());
  for (const c of chords) bindings.get(scope).set(normal(c), null);
}

// ---- how keys are written -----------------------------------------------------------------

const MAC_MODS = { mod: "⌘", ctrl: "⌃", alt: "⌥", shift: "⇧" };
const PC_MODS = { mod: "Ctrl", ctrl: "Ctrl", alt: "Alt", shift: "Shift" };
const KEY_NAMES = { escape: "Esc", enter: "Enter", backspace: "⌫", delete: "Del", space: "Space", pageup: "PgUp", pagedown: "PgDn",
  arrowup: "↑", arrowdown: "↓", arrowleft: "←", arrowright: "→", "=": "+", "-": "−", tab: "Tab", home: "Home", end: "End" };

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
