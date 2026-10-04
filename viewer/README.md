# StoreyPath Viewer

A read-only 3D web viewer engine for StoreyPath packages. Embed it in any web
app to show buildings floor by floor, find rooms, and highlight spaces by their
StoreyPath ID — for example where employees sit. It does no editing; packages are
made with [StoreyPath Studio](../studio).

Built on [MapLibre GL JS](https://maplibre.org/). Plain ES modules, no build step.

## Use

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

## API

`new StoreyPathViewer(container, options)`

| Option | Default | |
|---|---|---|
| `basemap` | `true` | OpenStreetMap tiles under the buildings |
| `labels` | `true` | room names and numbers on the current floor |
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
| `set3D(on)`, `setBasemap(visible)` | |
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

- [examples/basic](examples/basic): a complete viewer page with building and
  floor picker, search, legend and details — open with `?pkg=<url>`.
- [examples/minimal](examples/minimal): the smallest embed.

Serve the `viewer/` folder over HTTP (ES modules don't load from `file://`), or
run `storeypath view <package>` from Studio.
