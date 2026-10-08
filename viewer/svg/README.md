# StoreyPath plan viewer (SVG)

One floor of a StoreyPath package as a plain SVG plan: spaces and zones, walls,
doors with their swings, windows and openings, furniture and equipment, labels.
No WebGL and no
dependencies, so it runs on any machine: VDI desktops and kiosks with no GPU,
old browsers' worth of hardware, a phone. Written in strict TypeScript; ships ES
modules and type declarations.

The engine draws a **floor model** (`FloorPlan`, in `src/types.ts`): plain data in
the floor's local metres. A system can make it from a package
(`floorFromPackage`), or serve it from its own records, which is how wayfinder
uses it. The engine leaves meaning to its host: the host says how each space looks,
what it is called, which spaces can be chosen, and what happens when one is.

```html
<link rel="stylesheet" href="@storeypath/viewer-svg/plan.css">
<div id="plan" style="height: 600px"></div>

<script type="module">
  import { FloorPlanEngine, floorFromPackage, readPackage } from "@storeypath/viewer-svg";

  const pkg = await readPackage(await (await fetch("/files/headquarters.storeypath")).arrayBuffer());
  const engine = new FloorPlanEngine("#plan", {
    label: (s) => [s.name ?? "", s.number ?? ""],          // lines, any language
    styleOf: (s) => (taken.has(s.id) ? { className: "taken" } : null),
    interactive: (s) => s.type === "office" || s.kind === "zone",
  });
  engine.setFloor(floorFromPackage(pkg, "K7Q2XM-RUH-HQ-F02"));
  engine.addEventListener("select", (e) => console.log(e.detail.id));
  engine.select("K7Q2XM-RUH-HQ-F02-0142", { focus: "pan" });
</script>
```

`example/index.html` opens any `.storeypath` file (build first, then serve the
folder: `npm run build && python3 -m http.server`, and open `/example/`).

## What is shown

- **Units**: the zones of a divided space and the spaces with no zones — what
  people are placed in (see FORMAT.md, "Which to use"). Each is a `<path>` with
  `data-sp-id`. A divided space's outline is drawn over its zones; zone edges
  are light dashed lines, not walls.
- **Walls** and **parapets** as filled shapes, with their door and window gaps.
- **Doors** with their swings as drawn; **windows**; **openings** (doorways).
- **Labels** in screen space: upright and the same size at any zoom, shown only
  where they fit inside their space, each line in its own direction
  (`unicode-bidi: plaintext`: Arabic and English mix). They sit on the space's
  label point, which is inside it whatever its shape.
- **Items** (format 0.6: furniture and equipment) over the spaces and under the
  walls and labels, as Studio draws them: the footprint in its type's colour, its
  front edge darker, and a mark of its kind: a desk's chair (and, by its type's
  grade, visitors' chairs, a return, a cabinet behind a high-backed chair), a
  sofa's seat, the way a TV faces, a photocopier's lid, a kiosk's screen and the
  way it faces; an access point a small circle with a wifi mark; anything on the
  ceiling dashed, as overhead. Each is a `<g>` with `data-sp-item`, a button in the
  tab order. `items: false` (or `setItems(false)`) hides them; `interactiveItems: false` leaves them out of clicks, which then
  choose the space under them. A floor model gives them as `items`, each its middle
  (`at`), the way its front faces (`front`, a direction in the plan's
  coordinates), `width`, `depth`, `type`, `mount` and `color`.
- A **pin** (`setPin`) on a space's label point, for "you are here".

Hidden spaces (set in review) are left out unless `showHidden`; ignored ones always.

## Using it

| | |
|---|---|
| `new FloorPlanEngine(element, options)` | `label`, `ariaLabel`, `styleOf`, `interactive`, `colors`, `motion`, `padding`, `maxScale`, `labels`, `labelSize`, `items`, `interactiveItems`, `title` |
| `setFloor(plan, { fit })` | draw a floor; fitted unless `fit: false` |
| `setOptions(options)` / `restyle()` | new options; apply `styleOf` again when the host's data changes |
| `setItems(on)` / `itemsShown` | show the items, or hide them (shown unless `items: false`) |
| `select(id, { focus })` / `selected` | choose a space or an item (`focus: "pan"` brings it to the middle at the same zoom, `"zoom"` zooms to it) |
| `highlight(ids, { dim })` | bring some spaces out, dim the rest; `null` for none |
| `setPin(id)` / `markerOf(id)` | a pin in a space; a space's label point |
| `fit()`, `fitTo(ids)`, `focus(id, { zoom })`, `zoomBy(f, at)` | move the view |
| `camera()` / `setCamera(c)` | the view: screen = (x·k + tx, ∓y·k + ty) |
| `toScreen(p)` / `toPlan(s)` | between plan metres and the element's CSS pixels |
| events | `select` (`detail: { id, space, item }`: the space or the item chosen, from a click, a tap or the keyboard), `camerachange` |

A drawing's y grows upwards; set `yDown: true` on a floor model whose y grows
downwards (as a page's does).

People move the plan with a drag, the wheel, two fingers, or the keyboard (arrows,
`+`, `-`, `0` to fit). Spaces that can be chosen are buttons in the tab order:
Tab to one and press Enter. Moves are animated unless the system asks for reduced
motion. Resizing keeps the middle of the view where it was.

Theme with CSS variables on any ancestor (`--sp-wall`, `--sp-door`, `--sp-window`,
`--sp-select`, `--sp-highlight`, `--sp-dim-opacity`, `--sp-label`, `--sp-pin`, …: see
`src/plan.css`), or style the classes (`.sp-unit`, `.sp-zone`, `.sp-selected`,
`.sp-highlight`, `.sp-dim`, `.sp-type-office`, …). Space colours follow their type
unless `styleOf` or `colors` says otherwise.

For tests: the `<svg>` carries `data-cam="k,tx,ty"`, each unit `data-sp-id` and each
item `data-sp-item` (and `data-selected` when chosen), the pin `data-sp-pin` with
`data-plan-x`/`data-plan-y`.

## Coordinates

`floorFromPackage` turns the package's longitude and latitude back into the
building's local metres: the drawing as the architect drew it, whatever its
bearing on the map. It agrees with Studio to a millimetre on
`spec/conformance/localframe.json`; `LocalFrame` is exported for the same use.
Items of format 0.7 carry where they stand in those metres (`local`), and are put
there as they are: moving the building on the map moves nothing in its plan.
Older packages' items are placed by their point and heading on the map.

## Tests

`npm test` builds, then checks the frame and the package reader against
`spec/conformance/`, and the engine in headless Chrome with real input: clicks,
drags, the wheel, two-finger pinches, the keyboard, resizing, reduced motion, both
y directions. Positions must hold to a pixel. Set `CHROME` to a Chrome or Chromium
if it is not in the usual place.
