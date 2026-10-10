# Studio's pages, and Review in them

Studio's pages are plain HTML, CSS and ES modules, served as they are (no build step,
no framework): `index.html` (projects, a project, item types, users, GPU helpers:
`studio.js`, `itemtypes.js`, `users.js`, `helpers.js`),
`login.html`, `navigate.html`, `world.html` (a building in 3D, the whole window: `world.js`,
`world.css`), and **Review** (`review.html` and `review/`), the floor editor. This note is
mostly about Review, for whoever adds to it.

## The 3D page

`world.html?pkg=<one of Studio's packages>[&building=…][&floor=…]` shows a building in 3D,
read only: Review's *3D window* and a project's *Walk in 3D* (and a building's or an
export's *3D*) open it. It is StoreyPath's world (`/viewer/src/world/world.js`) in Studio's
frame: the top bar (where you are, Dollhouse or Walk, the keys, presenting, full screen,
the theme, the account), a toolbar over the world (X-ray, Cutaway, Explode, Labels, Items,
Hidden; walking, the map and the doors; Look and Quality: `look.js`), the floor stack, a
room's or an item's details (a click, walking too), walking's map and the room you are in,
and a status bar (what the view expects, the door <kbd>E</kbd> works, how it is drawn).
Walking never takes the mouse: a drag looks, a double-click goes there (the world's own,
`viewer/README.md`, "Walking"). `P` presents: nothing but the
building, turning slowly in the dollhouse (`T` stops it), `Esc` back. What it shows is kept
in its address (`mode`, `xray`, `cutaway`, `explode`, `items`, `hidden`, `style`,
`quality`, `floor`), and only a package of Studio's own (`/api/projects/…`) is read.
`tests/browser/world.mjs` drives it.

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
| `fit.js`, `itemshape.js` | where an item may stand and the magnet; how a type is drawn (`shapeOf`) and its symbol on a plan (the Item types page draws the same) |
| `review/measure.js` | the Measure tool |
| `review/vertical.js`, `finish.js`, `sample.js` | Stairs and lifts, Paint finishes, Share an area: each its own tool and parts |
| `review/view3d.js` | 3D and walking (StoreyPath's world, `/viewer/src/world/world.js`): the pointer, what a click would do marked under it, the ghost, items carried, the HUD |
| `review/menu.js` | the right-click menu, on the plan and in 3D and walking (`menu3d`) |
| `review/rooms.js` | a room's corrections, saved; capacity and grade |
| `review/live.js`, `../together.js` | others' changes shown live; presence, the floor's lock, undo and redo, History |
| `review/api.js`, `notify.js`, `access.js`, `dom.js`, `tooltip.js` | calls to Studio, toasts and the status line, who may edit, elements and icons, tooltips |
| `icons/lucide.svg` | the icons, one `<symbol>` each (Lucide, ISC: `icons/LICENSE-lucide.txt`) |

The other pages use the same `theme.css`, `ui.css`, icons and top bar (`.topbar` in
`ui.css`: the mark home, where you are, the page's own controls, the theme switch, the
account): `style.css` is what they share besides (and their older token names, as
aliases), `studio.css` and `navigate.css` each page's own, `chrome.js` their crumbs
(`setCrumbs`), tooltips and theme switch.

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
world's `pick` event (a click where the pointer is) is the tool's (see Place and Paint in
`view3d.js`). Its key where it does not work (a 2D tool in 3D or walking) changes nothing
and says why in the status bar: a letter never switches the view. Call
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
in the list of keys (?). A menu runs it with `run("view.grid")`. `works: () => …` says
when its key does what it says (the list of keys shows only those; a tool's key elsewhere
only says why).

## The keys

One map (`keys.js`), in scopes, the first that applies and has the key wins:
`review` (review mode), `walk` (walking: W A S D and the arrows reserved for the walker),
`item` (an item chosen, whatever the tool: R , . turn it, the arrows move it, Del deletes
it — what is chosen wins), `tool:<id>` (the tool in use: Enter, Backspace), `3d` (3D and
walking: 2 shows the place under the pointer on the plan) or `2d` (Space held moves the
plan, reserved for `pointer.js`), and `global`. Keys others handle are reserved in their
scope with who handles them (`reserve(chords, scope, owner, title)`), so the map says them
and Review hears nothing there; one key twice in a scope is an error
(`window.storeypathReview.keyConflicts()`). Keys typed in a field are the field's, and a
dialog's or a menu's keys are theirs. A letter changes the tool, never the view: only
2, 3 and 4 do (and 2 in 3D or walking shows the place under the pointer on the plan); a
key that cannot work where you are says why in the status bar. The list of keys (?) shows
the keys of the view and of what is chosen and in use, and nothing else; tooltips, the
palette and the menus show a command's key only where it runs it (`keyNow`).

What each key does, made from the registry by `tests/browser/review.mjs` (which fails
when this table is not the registry's; `UPDATE_KEYS=1` writes it again). Walking, the
world itself takes <kbd>E</kbd> first when there is a door (under the pointer, else
ahead).

<!-- keys: made by tests/browser/review.mjs from the registry -->
| Key | 2D | 3D | Walk |
|---|---|---|---|
| `Space` | Move the plan, held (with any tool) (the plan's) | — | — |
| `V` | Select tool | Select tool | Select tool |
| `H` | Pan tool | — (says why) | — (says why) |
| `G` | Find the way | Find the way | Find the way |
| `2` | — (shown already) | This place in 2D | This place in 2D |
| `3` | Show in 3D | — (shown already) | Show in 3D |
| `4` | Walk through it | Walk through it | — (shown already) |
| `F` | Fit the floor in view | — (says why) | — (says why) |
| `+` | Zoom in | — (says why) | — (says why) |
| `−` | Zoom out | — (says why) | — (says why) |
| `←` `→` `↑` `↓` | Move the plan · *an item chosen:* Move the item | — (says why) · *an item chosen:* Move the item | Walk (the walker's) |
| `Shift+←` `Shift+→` `Shift+↑` `Shift+↓` | Move the plan · *an item chosen:* Move the item 1 m | — (says why) · *an item chosen:* Move the item 1 m | Run (the walker's) |
| `T` | Studio's labels, or the drawing's texts | Studio's labels, or the drawing's texts | Studio's labels, or the drawing's texts |
| `[` | Show or hide the navigator | Show or hide the navigator | Show or hide the navigator |
| `]` | Show or hide the inspector | Show or hide the inspector | Show or hide the inspector |
| `\` | Show or hide both panels | Show or hide both panels | Show or hide both panels |
| `⌘/Ctrl+Z` | Undo | Undo | Undo |
| `⌘/Ctrl+Shift+Z` | Redo | Redo | Redo |
| `⌘/Ctrl+Y` | Redo | Redo | Redo |
| `Del` | — (says why) · *an item chosen:* Delete what is chosen | — (says why) · *an item chosen:* Delete what is chosen | — (says why) · *an item chosen:* Delete what is chosen |
| `⌫` | — (says why) · *an item chosen:* Delete what is chosen | — (says why) · *an item chosen:* Delete what is chosen | — (says why) · *an item chosen:* Delete what is chosen |
| `⌘/Ctrl+A` | Choose every room | Choose every room | Choose every room |
| `Shift+F10` | What can be done here (the right-click menu) | What can be done here (the right-click menu) | What can be done here (the right-click menu) |
| `Menu` | What can be done here (the right-click menu) | What can be done here (the right-click menu) | What can be done here (the right-click menu) |
| `Esc` | Close, give up or let go (the nearest first) | Close, give up or let go (the nearest first) | Close, give up or let go (the nearest first) |
| `PgUp` | The floor above | The floor above | Up the stairs or the lift |
| `PgDn` | The floor below | The floor below | Down the stairs or the lift |
| `⌘/Ctrl+K` | Search and commands | Search and commands | Search and commands |
| `/` | Search and commands | Search and commands | Search and commands |
| `?` | Keyboard shortcuts | Keyboard shortcuts | Keyboard shortcuts |
| `W` | Wall tool | — (says why) | Walk (the walker's) |
| `R` | Space tool · *an item chosen:* Turn the item 90° | — (says why) · *an item chosen:* Turn the item 90° | — (says why) · *an item chosen:* Turn the item 90° |
| `D` | Divide tool | — (says why) | Walk (the walker's) |
| `O` | Door, window, opening tool | — (says why) | — (says why) |
| `L` | Stairs and lifts tool | — (says why) | — (says why) |
| `I` | Place an item tool | Place an item tool | Place an item tool |
| `P` | Paint finishes tool | Paint finishes tool | Paint finishes tool |
| `M` | Measure tool | — (says why) | — (says why) |
| `A` | Share an area tool | — (says why) | Walk (the walker's) |
| `N` | Next room to review | Next room to review | Next room to review |
| `Shift+N` | Room before, to review | Room before, to review | Room before, to review |
| `Shift+R` | — · *an item chosen:* Turn the item back 90° | — · *an item chosen:* Turn the item back 90° | — · *an item chosen:* Turn the item back 90° |
| `,` | — · *an item chosen:* Turn the item 15° left | — · *an item chosen:* Turn the item 15° left | — · *an item chosen:* Turn the item 15° left |
| `.` | — · *an item chosen:* Turn the item 15° right | — · *an item chosen:* Turn the item 15° right | — · *an item chosen:* Turn the item 15° right |
| `E` | — | — | Open or close the door at the pointer, or ahead (at stairs: up) |
| `Q` | — | — | Down the stairs or the lift |
| `S` | — | — | Walk (the walker's) |
| `Shift+W` `Shift+A` `Shift+S` `Shift+D` | — | — | Run (the walker's) |

| Key | While | Does |
|---|---|---|
| `Enter` | the space tool in use | Space: close the shape |
| `⌫` | the space tool in use | Space: take back the last corner |
| `Enter` | the stairs tool in use | Stairs and lifts: close the shape (two corners: a rectangle) |
| `⌫` | the stairs tool in use | Stairs and lifts: take back the last corner |
| `Enter` | the measure tool in use | Measure: finish the measure |
| `⌫` | the measure tool in use | Measure: take back the last point |
| `N` | review mode | Next room to review |
| `P` | review mode | Room before |
| `Shift+N` | review mode | Room before |
| `Enter` | review mode | Accept the room as it is |
| `1`–`9` | review mode | Set one of the room's likely types |
<!-- /keys -->

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
the palette's *Light interface*, or the sun and moon in the other pages' top bar; one
setting for every page, remembered in the browser as `storeypath.theme`). The canvas
(the drawing, the plan) is light paper in both.

## Icons

`icons/lucide.svg` holds the icons used, from `lucide-static` 1.47.0 (ISC). To add one,
copy the children of its `icons/<name>.svg` into a new `<symbol id="<name>" viewBox="0 0
24 24">`; use it with `icon("<name>")` (dom.js) or `<svg class="icon"><use
href="/icons/lucide.svg#<name>"/></svg>`. Colour and stroke come from CSS (`.icon`).

## Tests

`tests/test_review_browser.py` runs `tests/browser/review.mjs` in headless Chrome (the
viewers' harness) on the tests' campus, and `tests/browser/world.mjs` (the 3D page) on the
demo campus: add a test there for what you add. Node runs `review/view3d.js`'s `refresh3d`
and `build3d` in `tests/test_review_app_3d.py`.
