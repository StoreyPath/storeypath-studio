# Studio's pages, and Review in them

Studio's pages are plain HTML, CSS and ES modules, served as they are (no build step,
no framework): `index.html` (projects, a project, users, GPU helpers: `studio.js`),
`login.html`, `navigate.html`, and **Review** (`review.html` and `review/`), the floor
editor. This note is about Review, for whoever adds to it.

## What Review is made of

```
┌ top bar ─ where you are · 2D 3D Walk · search · undo redo history · who · Share ┐
├ rail ┬ navigator ─┬ the canvas (plan / 3D / walk) ──────────────┬ inspector ─────┤
│ tools│ Rooms Items│ [lock banner] [review bar] [tool options]   │ what is chosen,│
│      │ View       │                         floor stack · zoom  │ or the floor   │
├──────┴────────────┴─────────────────────────────────────────────┴────────────────┤
└ status bar ─ the tool's hint · pointer · chosen · who edits · saved ─────────────┘
```

| File | What it is |
|---|---|
| `review.html` | the frame: top bar, rail, panels, canvas layers, status bar |
| `theme.css` | every design token (colours, type, space, radii, shadows, motion); dark is `:root`, light is `[data-theme="light"]` |
| `ui.css` | the components every page can use: buttons, fields, segmented, switch, menu, tooltip, kbd, avatar, badge, toast, dialog |
| `review/shell.css`, `panels.css`, `canvas.css` | Review's frame, its panels, its canvas (the plan's colours are `--canvas-*` and `--plan-*`: light in both themes) |
| `review/main.js` | puts the page together, in order; `window.storeypathReview` for the console and the tests |
| `review/state.js` | the state (the floor, the plan's view, the tool…), the 3D view's, what is read from them |
| `review/bus.js` | `on(event, fn)`, `emit(event, detail)`: the panels hear what changed (events listed at its top) |
| `review/selection.js` | the one selection, the same in 2D, 3D and Walk |
| `review/commands.js` | the registry of commands |
| `review/keys.js` | the keyboard map, in scopes, from the commands |
| `review/tools.js` | the registry of tools; the rail and the tool options bar drawn from it |
| `review/inspector.js` | the inspector and the registry of its sections; `review/sections/*.js` the sections |
| `review/navigator.js` | Rooms, Items, View |
| `review/topbar.js`, `statusbar.js`, `floorstack.js`, `layout.js` | the bars, the floor stack and zoom, the panels' widths and hiding |
| `review/palette.js` | ⌘K / Ctrl+K: rooms, items, floors and every command |
| `review/reviewmode.js`, `likely.js` | review mode, and a room's likely types |
| `review/plan.js`, `pointer.js` | the plan in SVG, its view and labels; the pointer on it |
| `review/drawing.js` | Wall, Space, Divide, Door/window/opening (tools), and what is drawn here |
| `review/items.js` | furniture and equipment, the Place tool, an item's keys |
| `review/measure.js` | the Measure tool |
| `review/vertical.js`, `finish.js`, `sample.js` | Stairs and lifts, Paint finishes, Share an area: each its own tool and parts |
| `review/view3d.js` | 3D and walking (StoreyPath's world, `/viewer/src/world/world.js`) |
| `review/menu.js` | the right-click menu |
| `review/rooms.js` | a room's corrections, saved; capacity and grade |
| `review/live.js`, `../together.js` | others' changes shown live; presence, the floor's lock, undo and redo, History |
| `review/api.js`, `notify.js`, `access.js`, `dom.js`, `tooltip.js` | calls to Studio, toasts and the status line, who may edit, elements and icons, tooltips |
| `icons/lucide.svg` | the icons, one `<symbol>` each (Lucide, ISC: `icons/LICENSE-lucide.txt`) |

## Adding a tool

Register it once (from the module that does its work, in `main.js`'s setup order for
where it sits in the rail):

```js
import { tool } from "./tools.js";

tool({
  id: "column", label: "Column", icon: "circle-dot", key: "k",   // its key must be free: keys.js says if not
  group: "draw",                    // the rail's group: navigate, draw, place, measure, share
  views: ["2d"],                    // where it works; elsewhere its button is dimmed and says why
  wrongView: () => "Columns are drawn on the plan",
  edits: true, drawing: true,       // only for who may change the floor; only with its drawing
  hint: (view) => "Click where the column stands",            // the status bar, while in use
  options: () => [/* elements: the options bar over the canvas */],  // or leave it out
  start() {}, stop() {},
  escape: () => false,              // Esc: undo what is under way (true), else the tool stops
  plan: { hover(p, e) {}, click(p, e) {}, dblclick(p, e) {}, down(p, e) {}, move(p, e) {}, up(p, e) {} },
  keys: { enter: [() => {}, "finish it"] },   // its own keys, while it is in use
});
```

`p` is a point of the plan in metres. The rail, its tooltip and key, the command
`tool.column` in the palette and the keyboard map come with it. In 3D and walking the
world's `pick` event is the tool's (see Place and Paint in `view3d.js`). Call
`emit("tool-options")` when its options change and `emit("tool-progress")` when its hint
does.

## Adding a command

```js
import { command } from "./commands.js";

command({ id: "view.grid", title: "Show a grid", group: "View", icon: "layers",
  keys: ["shift+g"], when: () => view3d.mode === "2d", why: () => "On the plan",
  run: () => { /* … */ } });
```

It is in the palette at once (`palette: false` keeps it out), its key in the map and
in the list of keys (?). A menu runs it with `run("view.grid")`. Keys are scoped
(`scope`): `global`, `3d`, `item` (an item chosen), `review` (review mode), `walk`,
`tool:<id>`; the first scope that applies and has the key wins, and one key twice in a
scope is an error (`window.storeypathReview.keyConflicts()`; the browser tests check
it). Keys typed in a field are the field's.

## Adding an inspector section

```js
import { section } from "./inspector.js";

section({ id: "room.notes", kinds: ["space"], title: "Notes", order: 55, open: false,
  render: ({ space, editable }) => el("p", {}, "…"),   // null: not shown for this one
  summary: ({ space }) => "2" });                       // said in its heading when closed
```

Kinds: `floor` (nothing chosen), `space`, `spaces` (several), `asset` (an item),
`opening`, `drawn`. Whether a section is open is remembered. A field saved on Enter or
when left: `field(input, save)`; a labelled row: `row(label, control, note)`. The
inspector is drawn again when what it shows changes, but not under a field being typed
in (then when it is left), unless someone else changed what it shows.

## The selection

`selection.js` holds one kind at a time: `space` (one or several IDs), `asset`,
`opening`, `drawn`. `select(id, { fly, add })`, `selectSpaces(ids, { add })`,
`selectAsset(id)`, `selectItem({kind: "door", id} | {kind: "wall" | "divider", at, line})`,
`clearSelection()`. The plan, the 3D view, the address and the panels follow (bus:
`selection`). `state.selected`, `state.asset` and `state.item` read from it.

## The theme

Every colour, size, space, radius and shadow is a token in `theme.css`; a page's CSS
uses tokens only. `<html data-theme="light">` switches to the light theme (View, or
the palette's *Light interface*; remembered in the browser as `storeypath.theme`). The
canvas (the drawing, the plan) is light paper in both.

## Icons

`icons/lucide.svg` holds the icons used, from `lucide-static` 1.47.0 (ISC). To add one,
copy the children of its `icons/<name>.svg` into a new `<symbol id="<name>" viewBox="0 0
24 24">`; use it with `icon("<name>")` (dom.js) or `<svg class="icon"><use
href="/icons/lucide.svg#<name>"/></svg>`. Colour and stroke come from CSS (`.icon`).

## Tests

`tests/test_review_browser.py` runs `tests/browser/review.mjs` in headless Chrome (the
viewers' harness) on the demo project: add a test there for what you add. Node runs
`review/view3d.js`'s `refresh3d` and `build3d` in `tests/test_review_app_3d.py`.
