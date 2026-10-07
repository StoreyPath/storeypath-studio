# StoreyPath Viewer

Read-only web viewer engines for StoreyPath packages, to embed in any web app. It
does no editing; packages are made with [StoreyPath Studio](../studio).

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
| `explode` | `0` | m between floors in the dollhouse view |
| `slab`, `doorHead`, `windowSill`, `windowHead`, `cutHeight` | `0.22`, `2.1`, `0.9`, `2.2`, `1.25` | m |

| Method | |
|---|---|
| `open(source)` | load a package (URL, `Blob`, `File`, `ArrayBuffer`); builds its first building |
| `setBuilding(id)` | build and show a building |
| `setFloor(id)` | one floor, or `null` for all; when walking, go to that floor |
| `setMode("dollhouse" \| "walk")` | orbit, or stand at the front door to walk in |
| `startWalking()` | take the mouse to look around (call it from a click: pointer lock) |
| `changeFloor(+1 \| -1)` | when walking: up or down a floor |
| `select(id, { go })` | highlight a space and fly (or, walking, go) to it |
| `setXray(on)`, `setCutaway(on)`, `setLabels(on)`, `setShowHidden(on)`, `setExplode(m)` | |
| `plan(floorId)` | a floor's walls and rooms in local meters, for drawing a minimap |
| `destroy()` | |

Properties: `package`, `building`, `floor`, `mode`, `selected`, `room` (the space
the walker is in), `walkFloor`, `atStairs`, `walking` (mouse taken), `player`
(`{ x, z, dx, dz, floor }`, for a minimap).

Events: `load`, `buildingchange`, `floorchange`, `modechange`, `select`
(`{ id, feature }`), `roomchange` (`{ id, type, name, number, stairs }`) and
`walklock` (`{ locked }`).

Walking: mouse to look, <kbd>W A S D</kbd> or the arrow keys to move,
<kbd>Shift</kbd> to run. Walls and windows stop you; doorways don't. Floor changes
are up to the page (the example uses <kbd>E</kbd>/<kbd>Q</kbd> where `atStairs` is
true). Walls come from the floors' `walls`, door and window openings from the
openings' `span` (format 0.1); a package without them shows rooms but no walls.

[examples/world](examples/world) is a complete page: floor picker, dollhouse
controls, room details, the walking HUD and minimap.

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
