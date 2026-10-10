// Every command Review has, in one registry: what it is called, where it is grouped, its
// icon, its keys, when it can run (and why not), and what it does. The command palette
// lists them, the menus and buttons run them, and the keyboard map (keys.js) binds their
// keys. A new feature adds its commands here, with command({...}).
//
//   command({
//     id: "view.3d",                 unique: <group>.<name>
//     title: "Show in 3D",           as the palette and menus say it
//     group: "View",                 the palette's and the shortcuts list's heading
//     icon: "box",                   an icon of icons/lucide.svg (optional)
//     keys: ["3"],                   its keys (keys.js: "mod+shift+z", "pageup", "[", …)
//     scope: "global",               where its keys work (keys.js: SCOPES); default global
//     when: () => true,              whether it can run now (else shown disabled)
//     why: () => "…",                why it cannot (the palette and tooltips say it)
//     works: () => true,             whether its key does what it says here (a tool's key where
//                                    the tool does not work only says why): the list of keys
//     idle: "shown already",         what its key does where it does not work (default: says why)
//     run: (event) => {},            what it does
//     palette: true,                 listed in the palette (false: keys or menus only)
//     repeat: false,                 a held key runs it again (arrows, zoom)
//     words: "other words to find it by",
//   })

import { bind } from "./keys.js";

const commands = new Map();

export function command(spec) {
  if (commands.has(spec.id)) console.error(`command ${spec.id} registered twice`);
  const c = { group: "General", palette: true, scope: "global", keys: [], ...spec };
  commands.set(c.id, c);
  for (const k of c.keys) bind(k, c.scope, c.id);
  return c;
}

export const getCommand = (id) => commands.get(id) || null;
export const allCommands = () => [...commands.values()];

/** Whether a command can run now. */
export function canRun(c) {
  if (typeof c === "string") c = commands.get(c);
  if (!c) return false;
  try {
    return !c.when || Boolean(c.when());
  } catch {
    return false;
  }
}

/** Whether a command's key does what the command says, now: it can run, and does here
 * (the list of keys shows those; a tool's key in a view it does not work in only says why). */
export function worksNow(c) {
  if (typeof c === "string") c = commands.get(c);
  if (!canRun(c)) return false;
  try {
    return !c.works || Boolean(c.works());
  } catch {
    return false;
  }
}

/** Why a command cannot run now ("" when it can). */
export function whyNot(c) {
  if (typeof c === "string") c = commands.get(c);
  if (!c || canRun(c)) return "";
  return c.why?.() || "Not available here";
}

/** A command run by its ID (its ``why`` said when it cannot); whether it ran. */
export function run(id, event, { quiet = false, say = null } = {}) {
  const c = commands.get(id);
  if (!c) {
    console.error(`no command ${id}`);
    return false;
  }
  if (!canRun(c)) {
    if (!quiet && say) say(whyNot(c));
    return false;
  }
  c.run(event);
  return true;
}
