# StoreyPath Viewer

Read-only web viewer engines for StoreyPath packages, to embed in any web app. It
does no editing of its own; packages are made with [StoreyPath Studio](../studio),
whose Review edits on top of the 3D world through its [editing API](#the-3d-world).

- **`StoreyPathWorld`**, the 3D world: walls, doorways, windows and floor finishes
  built from the package, with sunlight and shadows. Orbit it as a dollhouse (one
  floor or all, cut away, x-ray) or walk through it in the first person. Built on
  [three.js](https://threejs.org).
- **`StoreyPathViewer`**, the map view: floors on a map, search, and spaces
  highlighted by their StoreyPath ID — for example where employees sit. Built on
  [MapLibre GL JS](https://maplibre.org/).
- **`FloorPlanEngine`** in [`svg/`](svg/), one floor as a plain SVG plan: no WebGL
  and no dependencies, for machines with no GPU (VDI desktops, kiosks). Strict
  TypeScript, with its own build and tests.
- **`@storeypath/viewer-world`** in [`world/`](world/): the 3D world as one ES
  module with three.js and JSZip inside, type declarations, and `webglSupport()`
  to ask first whether the machine can show it well; for applications that
  install it as a package rather than serve these files.

Plain ES modules, no build step. Neither downloads anything but the package.

## The 3D world

```html
<script type="importmap">
  { "imports": {
      "three": "./vendor/three/three.module.js",
      "three/addons/": "./vendor/three/addons/",
      "jszip": "./vendor/jszip.mjs" } }
</script>

<div id="world" style="height: 600px; position: relative"></div>

<script type="module">
  import { StoreyPathWorld } from "./storeypath-viewer/src/world/world.js";

  const world = new StoreyPathWorld("#world");
  await world.open("/files/headquarters.storeypath");

  world.setFloor("K7Q2XM-RUH-HQ-F02");
  world.setCutaway(true);
  world.select("K7Q2XM-RUH-HQ-F02-0142");             // fly to an office
  // later, from a click:  world.startWalking();
</script>
```

With a bundler, `import { StoreyPathWorld } from "@storeypath/viewer/world"` and
install `three` and `jszip` alongside it.

`new StoreyPathWorld(container, options)`

| Option | Default | |
|---|---|---|
| `labels` | `true` | room names in the dollhouse view |
| `showHidden` | `false` | spaces marked hidden or ignored in Studio |
| `items` | `null` | furniture and equipment: `true`, `false`, or `null`: shown when one floor is |
| `explode` | `0` | m between floors in the dollhouse view |
| `slab`, `doorHead`, `windowSill`, `windowHead`, `cutHeight` | `0.22`, `2.1`, `0.9`, `2.2`, `1.25` | m |

| Method | |
|---|---|
| `open(source)` | load a package (URL, `Blob`, `File`, `ArrayBuffer`); builds its first building |
| `setBuilding(id)` | build and show a building; a promise, resolved once it is shown |
| `setFloor(id)` | one floor, or `null` for all; when walking, go to that floor |
| `setMode("dollhouse" \| "walk", { at, floor, heading, back })` | orbit, or stand at the front door to walk in; walking from `at` (`{ x, z }`, local metres: the middle of the nearest room when in none), on `floor`, facing `heading`; back to orbiting, round the whole building, or with `back`, where the view was before walking |
| `startWalking()` | take the mouse to look around (call it from a click: pointer lock); `stopWalking()` gives it back |
| `changeFloor(+1 \| -1)` | when walking: up or down a floor |
| `select(id, { go })` | highlight a space, zone or item and fly (or, walking, go) to it |
| `setXray(on)`, `setCutaway(on)`, `setLabels(on)`, `setShowHidden(on)`, `setExplode(m)` | |
| `setItems(on)` | furniture and equipment: `true`, `false`, or `null` (shown when one floor is) |
| `plan(floorId)` | a floor's walls, rooms, items and obstacles in local meters, for drawing a minimap |
| `showRoute(route, { fly, color, casing, arrow, start, end })` | draw a way (`route()`, below): an edged ribbon over each floor it walks on, through the lift or stairs between them, its start and end marked, in the page's colours (CSS colours; by default the plan viewer's); with `fly`, the camera goes along it |
| `flyRoute({ seconds })` | take the camera along the way shown, the floors it is not on faded meanwhile (a promise); `clearRoute()` takes it away; `route` is the way shown |
| `reload(source, { floors })` | read the package again and build only `floors` again (default: all of the building shown), where they stand: the view, mode, floor shown, selection, walker and a way shown are kept |
| `pause()`, `resume()` | stop drawing while the page hides the world (kept as it is), and draw again at once |
| `destroy()` | |

**Editing on top of it** (what Studio's Review does in 3D and walking; the world
changes nothing by itself — the page decides, saves, and tells it):

| Method | |
|---|---|
| `pointAt(clientX, clientY)` | what is under a point of the screen — walking with the mouse taken, or given no point, under the crosshair: `{ floor, x, z, local, space, item }` (`x`, `z`: local metres; `local`: `[x, y]` in the building's own frame, the metres of its drawings, as Studio and an item's `local` have them; the space or zone it is in; the item in the way). The first thing in the way counts: aimed at a wall, the point is on the floor just before it. From the plan's walls and the items' boxes, not triangles: well under a millisecond on a floor of a thousand rooms, so it can follow the pointer, or the crosshair every frame |
| `worldPoint([x, y])`, `buildingPoint({ x, z })` | the building's own frame into the world and back (through its placement, format 0.7; null without one) |
| `setFloorItems(floorId, items)` | replace one floor's furniture and equipment without building anything else again (a thousand desks in milliseconds): each `{ id, type, x, y, rotation }` in the building's own frame (rotation: degrees counter-clockwise, its front its own −y), with `width`, `depth`, `height`, `mount`, `elevation`, `color`, `grade` where they are not its type's in the package's catalogue |
| `ghost(item \| null)` | where an item would go: see-through over its floor, green, or red with `ok: false`, its footprint outlined, with the magnet's `guides` (`[[x, y], [x, y]]` each); `null` takes it away |
| `setDraggable(on)` | items carried across their floor by a drag in the dollhouse view: `itemdragstart`, `itemdrag`, `itemdragend` say where (`{ id, floor, x, z, local, altKey, shiftKey }`; a press that does not move stays a click) |
| `updateSpace(id, { name, number, type, hidden, ignored })` | a room corrected: its label at once; its floor built again when its type (its finish) or whether it shows changed |

Properties: `target` (where the dollhouse view looks, `{ x, z }`), `paused`, `draggable`.

Properties: `package`, `building`, `floor`, `mode`, `selected`, `room` (the space
the walker is in), `walkFloor`, `atStairs`, `walking` (mouse taken), `player`
(`{ x, z, dx, dz, floor }`, for a minimap), `prebuilt` (the floors shown from the
package's pre-built 3D), `items` (whether items are drawn now).

Items (format 0.6: desks, photocopiers, access points, sofas, TVs, …) are drawn
in their type's colour as simple shapes of their kind, on their floor, where they
stand in their building (format 0.7: `local`, put on the map by the building's
placement, as its walls are; older packages: their point and heading on the map).
They cost nothing until shown: their geometry is made (or taken from the
pre-built file) the first time a floor shows them, detailed when one floor is
shown, a box each when more are, in two meshes a floor (below the cut, and above
it: on a wall, under the ceiling). A click on one chooses it, as on a room
(`select` with the item's feature); the walker bumps into those on the floor.

A floor is built from the package's features by
[src/world/build.js](src/world/build.js), merged into a few dozen meshes (one for
the walls, one for each type of floor finish, …) whatever its number of rooms. A
package exported by Studio with Node.js at hand carries each floor already built
(format 0.5, `world/<floor-id>.glb`, made by the same build.js:
[world/bake.mjs](world/bake.mjs)); the world shows those as they are, unless they
are of another export or the world was given other sizes, and builds the rest.
Either way it looks the same.

Events: `load`, `buildingchange`, `floorchange`, `modechange`, `select`
(`{ id, feature }`), `roomchange` (`{ id, type, name, number, stairs }`),
`walklock` (`{ locked }`), `pick` (a click on the dollhouse view, or walking with
the mouse taken at the crosshair: what `pointAt` says is there, with `button`,
`altKey`, `shiftKey`; cancelable — unless a listener calls `preventDefault()`, what
was clicked is selected), `itemdragstart`, `itemdrag`, `itemdragend` (with
`cancelled`) and `reload` (`{ floors }`).

Rooms' labels show where there is room for them on the screen (a room at least
56 px across), and are placed again only when the view moves: a floor of a thousand
rooms orbits as smoothly as a floor of ten.

Walking: mouse to look, <kbd>W A S D</kbd> or the arrow keys to move,
<kbd>Shift</kbd> to run. Walls and windows stop you; doorways don't. Floor changes
are up to the page (the example uses <kbd>E</kbd>/<kbd>Q</kbd> where `atStairs` is
true). Walls come from the floors' `walls`, door and window openings from the
openings' `span` (format 0.1); a package without them shows rooms but no walls.

[examples/world](examples/world) is a complete page: floor picker, dollhouse
controls, room details, the walking HUD and minimap.

## Finding the way

A package of format 0.8 carries its building's walking network (`navigation.json`,
`pkg.navigation`; null in older packages). [src/navigation.js](src/navigation.js),
one module both viewers share (no dependencies), finds the way on it exactly as
StoreyPath Studio and the Go module do (spec/FORMAT.md, "Navigation (0.8)";
spec/conformance/routes.json):

```js
import { route } from "@storeypath/viewer/navigation"; // or from @storeypath/viewer, -world, -svg

const pkg = await loadPackage("/files/headquarters.storeypath");
// from a kiosk (its item's ID) to an office (a space's, a zone's, an item's or a node's ID)
const way = route(pkg, "K7Q2XM-I000017", "K7Q2XM-RUH-HQ-F01-0069", { accessible: true });
way.steps.map((s) => s.text);
// ["Start at the kiosk in RECEPTION 017", "Walk 48 m along CORRIDOR to the lift",
//  "Take the lift up to Floor 1", "Walk 24 m along CORRIDOR to OFFICE 112", "OFFICE 112 is on your left"]
world.showRoute(way, { fly: true });
```

`route(pkg, from, to, { accessible })` answers the way: `nodes`, `legs` (the walking
on each floor: `floor_id`, `points` in the building's own metres, `metres`),
`changes` (each ride between floors: `by`, `from_floor_id`, `to_floor_id`,
`floors`, `direction`), `metres`, `seconds` and `steps` (each a `kind` — `start`,
`walk`, `take`, `arrive` — its values, and its `text` in English, for a system to
word in its own language); `null` when there is none (none without stairs, with
`accessible`); it throws for an ID the network does not have. `Graph` and
`shortest` are there for more. Spaces of lifts and stairs carry their `stack`: the
same on every floor one serves.

## The map view

```html
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
<link rel="stylesheet" href="storeypath-viewer/src/viewer.css">
<script type="importmap">
  { "imports": {
      "maplibre-gl": "https://esm.sh/maplibre-gl@4.7.1",
      "jszip": "https://esm.sh/jszip@3.10.1" } }
</script>

<div id="map" style="height: 600px"></div>

<script type="module">
  import { StoreyPathViewer } from "./storeypath-viewer/src/index.js";

  const viewer = new StoreyPathViewer("#map");
  const pkg = await viewer.open("/files/headquarters.storeypath");

  viewer.select("K7Q2XM-RUH-HQ-F02-0142");          // fly to an office
  viewer.highlight(["K7Q2XM-RUH-HQ-F02-0143"]);      // e.g. a colleague's desk
  viewer.addEventListener("select", (e) => console.log(e.detail.id));
</script>
```

With a bundler, `import { StoreyPathViewer } from "@storeypath/viewer"` and
install `maplibre-gl` and `jszip` alongside it.

### API

`new StoreyPathViewer(container, options)`

| Option | Default | |
|---|---|---|
| `basemap` | `true` | map under the buildings: `true` for OpenStreetMap (online), a tile URL template for your own tile server, or `false` (offline) |
| `labels` | `true` | room names and numbers on the current floor |
| `showHidden` | `false` | show spaces and doors marked hidden or ignored in Studio |
| `labelMinZoom` | `18.6` | hide labels when zoomed out further |
| `roomHeight` | `2.4` | meters; how tall rooms are drawn on a single floor |
| `colors` | | `{ type: color }` overrides per space type |
| `selectColor` | `#ff8a00` | |
| `background` | `#ecebe6` | map background |
| `mapOptions` | `{}` | passed to `maplibregl.Map` |

| Method | |
|---|---|
| `open(source)` | load a package from a URL, `Blob`, `File` or `ArrayBuffer`; resolves to the package |
| `setBuilding(id, { floor })` | show a building (its ground floor unless `floor` is given) |
| `setFloor(id)` | show a floor |
| `setMode("floor" \| "stack")` | one floor with labels and doors, or every floor at its real height |
| `select(id, { fly })` | select a space, switching building and floor as needed |
| `clearSelection()` | |
| `highlight(ids, { color })` | color a set of spaces; replaces the previous highlight |
| `clearHighlight()` | |
| `fitTo(features)` | frame features (default: the current building) |
| `set3D(on)`, `setBasemap(visible)`, `setShowHidden(on)` | |
| `destroy()` | |

Properties: `package`, `building`, `floor`, `mode`, `selected`, `map` (the
MapLibre map, for anything else).

Events (`addEventListener`, data in `event.detail`): `load`, `buildingchange`,
`floorchange`, `modechange`, `select` (`{ id, feature }`, `id` is `null` when
cleared).

The package object (`viewer.package`, or `loadPackage(source)` on its own):
`get(id)`, `hierarchy(id)` → `{ project, location, building, floor, object }`,
`floorsOf(buildingId)`, `spacesOn(floorId)`, `doorsOf(spaceId)`,
`search(query, { type, buildingId, floorId, limit })`, and the raw `manifest`,
`buildings`, `floors`, `spaces`, `openings` collections.

## Examples

- [examples/world](examples/world): the 3D world — dollhouse and walk-through —
  open with `?pkg=<url>` (also `&building=<id>`, `&floor=<id>`, `&mode=walk`, `&xray=1`,
  `&cutaway=1`, `&hidden=1`).
- [examples/basic](examples/basic): the map view with building and floor picker,
  search, legend and details — open with `?pkg=<url>`.
- [examples/minimal](examples/minimal): the smallest embed.

Serve the `viewer/` folder over HTTP (ES modules don't load from `file://`), or
run `storeypath view <package>` from Studio.
