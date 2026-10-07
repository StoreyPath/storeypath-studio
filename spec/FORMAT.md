# StoreyPath package format — version 0.6

A StoreyPath package (`*.storeypath`) describes one project: its locations, buildings,
floors, the spaces on each floor (offices, corridors, elevators, …), the zones that
divide open spaces, and the doors between spaces. It is a ZIP archive of plain JSON, GeoJSON and CSV files, so any
system can read it without special software.

A copy of this document is included in every package.

## Files

| File | Contents |
|---|---|
| `manifest.json` | Format version, project, export number, file list, counts, placements, and the buildings it holds when not all of them (`scope`) |
| `location.geojson` | Locations (sites, campuses) |
| `buildings.geojson` | Buildings, with their footprint |
| `floors.geojson` | Floors, with their outline, order, elevation and height |
| `spaces.geojson` | Rooms, offices, corridors, elevators, stairs, …: what walls and doors enclose |
| `zones.geojson` | The parts of an open space with no wall between them (a majlis and a dining area in one hall) |
| `openings.geojson` | Doors and other ways through, and which spaces each one connects |
| `objects.csv` | Every ID in one flat table, with its parent IDs — for building ID mappings |
| `changes.json` | IDs added, changed and retired since the previous export |
| `items.geojson` | Furniture and equipment on the floors: desks, photocopiers, access points, sofas, TVs, … (format 0.6) |
| `catalogue.json` | The types of items: their codes, names, sizes, how they are mounted, and the details each carries (format 0.6) |
| `schema/*.schema.json` | JSON Schema for every JSON file |
| `world/<floor-id>.glb` | Optional: each floor already built in 3D, as binary glTF (Pre-built 3D) |
| `FORMAT.md` | This document |
| `studio/` | Only in a project sent to be continued in another StoreyPath Studio: the project itself (its workspace, with every correction and edit, and its drawings). Other readers ignore it. |

Readers must locate files through `manifest.json → files`, not by fixed names.

The files are JSON, GeoJSON and CSV in a ZIP archive, each compressed on its own
(a floor's plan is typically a tenth of its size in the archive), so a reader takes
only the files it needs, one at a time, and can parse a file as it reads it.

## IDs

Every object has a hierarchical ID; each segment is upper-case letters and digits,
separated by hyphens:

```
PROJECT-LOCATION-BUILDING-FLOOR-OBJECT        K7Q2XM-RUH-HQ-F02-0142
```

- Every prefix is itself an ID: `K7Q2XM-RUH` is the location, `K7Q2XM-RUH-HQ-F02` the floor.
  The project, location, building and floor of any object can be read from its ID alone.
- The project code is randomly generated when the project is created, so packages from
  different projects can be imported into the same system without collisions.
- **IDs are stable.** Re-converting a revised drawing keeps the ID of every object that is
  still there. An object that disappears is *retired*, and its ID is never issued again.
- The object's type is **not** part of its ID: correcting a type does not change the ID.
- Elevators, stairs and escalators keep the same object code on every floor they serve
  (`…-F01-0023`, `…-F02-0023`), which links them vertically.

Importing systems should store our ID as the key of their own mapping, and use
`changes.json` on each new export to add, update and remove mappings.

## Coordinates

GeoJSON in WGS84 longitude/latitude (EPSG:4326, RFC 7946), rounded to 7 decimals
(about 1 cm). Lengths and heights are in meters. `manifest.json → placements` holds,
for each building, the anchor and bearing used to place the drawing, so local
drawing coordinates can be rebuilt if needed.

Placing a building on the map is optional. A building whose placement has
`"placed": false` is not on the map yet: it is exported around 0°N 0°E with its
true shape and size (unplaced buildings of one location keep their positions
relative to each other as drawn), but its position on earth is not known. Its
coordinates change once it is placed, and that export lists its objects as changed.

## Features

Every feature has a top-level `id` and a `properties.kind`. Readers must ignore
properties they do not know: later versions may add some.

**location** — `code`, `name`, `address`, `project_id`, `display_point`. Geometry: the
convex hull of its buildings (or null).

**building** — `code`, `name`, `location_id`, `display_point`. Geometry: footprint (or
null if no floor has been converted).

**floor** — `code`, `name`, `building_id`, `ordinal` (0 = ground, negative = below
ground), `elevation` (m above the ground floor), `height` (floor-to-floor, m),
`walls` (the walls as drawn, with their door and window gaps: a Polygon or
MultiPolygon, for drawing or modelling the floor; they rise to the ceiling),
`wall_thickness_m`, `parapets` (the low walls around terraces, balconies and roofs,
`parapet_height_m` high: a Polygon or MultiPolygon, or null) and
`parapet_height_m`. Geometry: floor outline (or null).

**space** — `type`, `name`, `number`, `drawing_label`, `floor_id`, `area_m2`,
`display_point` (a good spot for its label), `zones`, `outdoor` (open to the sky: a terrace or balcony with no
windows of its own; a glazed veranda is not), `hidden`, `ignored`. Geometry: Polygon
or MultiPolygon.
A space is what walls, doors and windows enclose: walls stand on its edges.

**zone** — `type`, `name`, `number`, `drawing_label`, `space_id`, `floor_id`, `area_m2`,
`display_point`, `hidden`, `ignored`. Geometry: Polygon or MultiPolygon. A zone is a
part of a space used for one thing, with no wall between it and the rest of the
space: a majlis and a dining area in one hall, a passage running into a living room,
team areas in an open office. The zones of a space divide it exactly (together they
cover it, without overlapping) and are listed in its `zones`; a space used for one
thing has none. Zone edges are not walls: draw them as light lines, and walk across
them freely.

**Names.** `name` and `number` are what StoreyPath read in the space's label and a
person may have corrected in review. `drawing_label` is the text written in the space
on the drawing, exactly as written (its lines joined by a line break; null when there
is none), never changed in review: the drawing's own name for the space, a key other
systems can match their records on, beside the ID. Systems keep their own display
names besides both.

**Which to use.** A space with zones is used through its zones; a space without
zones is used as a whole. Systems that place people or things in rooms should use
the zones of a space that has them, and the space itself otherwise.

**opening** — `type`, `floor_id`, `connects` (IDs of the one or two spaces it joins),
`exterior` (true when it leads outside), `width_m` and `span` (jamb to jamb, when
known), `swings` (a door's leaves as the plan draws them, each `[hinge, free edge
when open]`, when known: which side it hinges on and which way it opens; two for a
double door), `sill_m` and `height_m` (how high above the floor it starts, and how
tall it is, from the drawing's schedule of openings, when known), `hidden`, `ignored`.
Geometry: Point in the wall.

`hidden` and `ignored` are set by a person in review. A hidden object is real but
not shown unless asked for (a shaft, a plant room); an ignored one was judged not
worth anything (a sliver, a pocket) and is best left out. Both keep their IDs, so
they are exported like any other object, and are listed in `objects.csv`.

## Types

Space types: `office`, `room`, `meeting_room`, `corridor`, `lobby`, `elevator`,
`stairs`, `escalator`, `ramp`, `restroom`, `kitchen`, `storage`, `utility`, `shaft`,
`open_area`, `unspecified`, `bedroom`, `living_room`, `dining_room`, `bathroom`
(private; public toilets are `restroom`), `dressing_room`, `laundry`, `prayer_room`,
`parking`, `balcony`, `terrace`, `open_to_below` (a void over the floor below).

Opening types: `door`, `window` (glazing in a wall: not a way through), `opening` (a
way through a wall with no door: a doorway). Openings join spaces; the zones of a
space need none.

Zones use the space types.

Type names are never renamed or removed; new types may be added in later versions,
and `manifest.json → types` lists the ones this package may use. `unspecified`
means the converter could not decide and nobody has corrected it yet.

## objects.csv

One row per ID, including the project itself:

`id, kind, type, name, number, project_id, location_id, building_id, floor_id,
floor_ordinal, area_m2, lon, lat, hidden, ignored, space_id, drawing_label`

`kind` is `project`, `location`, `building`, `floor`, `space`, `zone` or `opening`.
Parent columns are empty where they do not apply; `space_id` is a zone's space.
`lon`/`lat` is the label point (spaces, zones, buildings, locations) or the door
position (openings).

## changes.json

```json
{
  "sequence": 3,              // this export; 1 for the first
  "previous_sequence": 2,
  "added":   ["…"],           // IDs new since the previous export
  "changed": ["…"],           // IDs whose geometry or properties changed
  "retired": ["…"],           // IDs removed since the previous export
  "all_retired": ["…"]        // every ID this project has ever retired
}
```

A system that skipped an export can use `all_retired` to clean up its mappings.

## Items

Items are the furniture and equipment people place on floors: desks (by grade:
a manager's, a junior staff member's), central photocopiers, wireless access points,
sofas, TVs. `items.geojson` holds them; `catalogue.json` says what each type is.

**An item's ID does not say where it is.** It is the project's code and the item's
own number, `I` and six digits: `K7Q2XM-I000142`. A desk carried to another office,
or another floor, keeps its ID; where it stands is in its properties. Like every ID,
an item's is never issued again once it is retired.

```json
{ "type": "Feature", "id": "K7Q2XM-I000142",
  "geometry": { "type": "Polygon", "coordinates": [ … ] },     // its footprint
  "properties": {
    "kind": "item", "type": "DESK-MANAGER", "category": "furniture", "name": "Manager's desk",
    "floor_id": "K7Q2XM-RUH-HQ-F02", "building_id": "K7Q2XM-RUH-HQ",
    "space_id": "K7Q2XM-RUH-HQ-F02-0142", "zone_id": null,       // where its middle stands
    "display_point": [46.67, 24.71], "heading": 270.0,          // its front faces west
    "width_m": 1.8, "depth_m": 0.9, "height_m": 0.75,
    "mount": "floor", "elevation_m": 0.0,                        // wall: its bottom; ceiling: null
    "values": { } } }
```

- `type` is a code of `catalogue.json`; `name` is that type's English name, for a reader
  that does not read the catalogue.
- `space_id`, and `zone_id` when the space is divided, are where its middle stands at
  export: null when it is in none.
- `heading` is the way its front faces, in degrees clockwise from north (a desk's
  front is where its user sits).
- `values` are its details entered in StoreyPath, by the catalogue's field keys.

`catalogue.json` (`schema/catalogue.schema.json`) lists the types: a `code` that is
kept for good and never given to another type (a type no longer used is `retired`),
English and Arabic names, a `category` (furniture, equipment, appliance), a size, how
it is mounted (`floor`, `wall`, `ceiling`), a colour, and its `fields`. Each field
says who enters it: `owner: "storeypath"` (what is physical: a colour, a size, a
model) or `"system"` (the system that manages the asset: its network, its asset tag).
A reader keeps the `system` fields itself, by the item's ID; the package never
carries them. The catalogue is the organization's: the same for every project.

Items go through `changes.json` as everything else: one moved, turned or given other
details is `changed`, so a system can keep the history of where each item has been.
In a package of part of a project, an item is held when its floor is; one carried
out of the part is not listed until a package holds where it went (or the whole
project).

## Part of a project

A package may hold only some of a project's buildings: `manifest.json → scope`
lists them.

```json
"scope": { "buildings": ["7KQ2MX-SITE-ENG"] }
```

It then holds those buildings, their floors and everything on them, and the
locations they stand in; `sources`, `placements` and `counts` cover them alone.
Without `scope` a package holds the whole project.

What is outside the scope is not in the package, and its absence says nothing
about it: a reader that keeps a project's buildings must leave the others as they
are, and compare a building only with the last package that held it.

`changes.json` lists what changed in those buildings alone since the previous
export (of the whole project or a part of it): an ID outside the scope is never
listed, nor retired. Export numbers run on across the project's packages, whole
or in part, so a later package always has a higher `sequence`.

## Pre-built 3D

A package may carry its floors already built in 3D, as StoreyPath's viewer builds
them from the features, so that a viewer on a slow machine shows a building
without building it: `world/<floor-id>.glb`, one binary glTF 2.0 file a floor,
listed in the manifest as `"world": "world/"`. Studio builds them when it exports,
when it can; a floor without a file is built from its features, as before. Readers
that do not draw in 3D ignore the folder.

**Frame.** One unit is a metre. x is east, y up and z south, from the building's
local origin (`origin` in the extras below): the middle of the box round its floors'
outlines in longitude and latitude (its footprint's, with no floors; 0, 0 with
neither), where a point (lon, lat) is at x = (lon − origin.lon) · kx and
z = −(lat − origin.lat) · ky, with kx = 111320 · cos(origin.lat) and ky = 110540
metres a degree. y is the height above the building's ground floor: a floor stands
at its `elevation`. Every floor of a building has the same origin, so the files
stack as they are, and every node's transform is the identity.

**Meshes.** One mesh a piece of the floor, each one primitive with one material.
The node names it; its `extras` say what it is: `material`, `view`, and `type` and
`hidden` where they apply.

| Name | `material` | `view` | What |
|---|---|---|---|
| `slab` | `slab` | | the floor slab, under the floor's outline |
| `floor:<type>` | `floor` | | the floor finish of the spaces and zones of that `type` |
| `volume:<type>` | `volume` | `xray` | each space of that `type` as a volume, up to its ceiling (to its parapets, when open to the sky) |
| `ceiling` | `ceiling` | `walk` | the ceiling, over every space but those open to the sky |
| `wall`, `wallTop` | `wall`, `wallTop` | `full` | the walls' faces, and their tops and undersides, full height |
| `parapet`, `parapetTop` | `wall`, `wallTop` | `full` | the parapets, likewise |
| `heads` | `wallPlain` | `full` | the wall over doors, doorways and windows |
| `sills` | `wallPlain` | | the wall under windows |
| `glass`, `frame` | `glass`, `frame` | `full` | windows: the glass, and its frame and mullions |
| `door`, `doorFrame` | `door`, `doorFrame` | `full` | door leaves, open as the plan draws them, and their frames |
| `obstacles` | | | lines (mode `LINES`) at the floor, not drawn: what someone walking bumps into (walls, windows, open leaves) |

`view` says when a piece shows: `full` unless the walls are cut low, as on a plan;
`cut` only then; `walk` when walking on that floor; `xray` in the see-through view;
with none, always. The pieces of spaces and zones hidden or ignored in review are
meshes of their own (`floor:shaft:hidden`, `"hidden": true`), shown only when asked
for. A plain glTF viewer shows every mesh at once.

The walls and parapets cut low are not in the file: they are `wall`, `wallTop`,
`parapet` and `parapetTop` with every vertex higher than `elevation` +
`options.cutHeight` brought down to that height (and on the faces, its v to
1 − `cutHeight`), shown with `view` `cut`, the tops with the material `wallCut`.
Texture coordinates (`TEXCOORD_0`) are only on what has a texture: the floor
finishes (metres east and north) and the walls' and parapets' faces (v = 1 − the
height above the floor, in metres).

The floor finishes and volumes have a vertex attribute `_ROOM` (unsigned integer),
an index into `rooms` below: the space or zone each vertex belongs to, so that a
click on a floor tells which room it is. The materials in the file are plain
colours; StoreyPath's viewer draws each piece with its own, by `material` and
`type`.

**Extras.** The scene's `extras.storeypath`:

| Key | |
|---|---|
| `project_id`, `building_id`, `floor_id` | what it is |
| `export_sequence` | the export it was built for: a file whose sequence is not the manifest's is stale; build that floor instead |
| `origin` | `lon`, `lat`, `kx`, `ky`: the frame above |
| `options` | the sizes it was built with, in metres: `slab` (thickness), `doorHead`, `windowSill`, `windowHead`, `wallThickness` (where the floor gives none), `cutHeight` |
| `elevation`, `wall_height` | the floor's elevation, and how high its walls rise above it |
| `rooms` | the IDs `_ROOM` indexes |

## Changes from 0.2

- `zones.geojson` and the `zone` kind: open areas are one space divided into zones,
  where 0.2 cut them into separate spaces joined by an `opening` with no wall.
- `space.zones`; `objects.csv` gains `space_id` (and documents `hidden`, `ignored`).
- An object's kind may change between exports when it is the same place in use:
  a 0.2 space that becomes a zone keeps its ID.

0.3.1 adds the openings' `swings`, `sill_m` and `height_m`, and the spaces' `outdoor`.
0.3.2 adds the spaces' and zones' `drawing_label` (and its column in `objects.csv`).

## Changes from 0.3

- `manifest.json → scope`: a package of some of a project's buildings (Part of a
  project). A reader of 0.3 that imports packages would take the buildings left
  out as removed: it must read `scope` before it applies a 0.4 package.

## Changes from 0.4

- `world/` and `manifest.json → files.world`: the floors pre-built in 3D (Pre-built
  3D), when the exporter could build them. Nothing else changes: a reader of 0.4
  reads a 0.5 package as it is, ignoring the folder.

## Changes from 0.5

- `items.geojson` and `catalogue.json`: furniture and equipment, with IDs of their own
  that do not change when they move (Items). Readers of 0.5 ignore them.

## Readers

`spec/conformance/` holds packages and coordinate pairs that every reader must read
the same way: Studio's own validator, the Go module in `go/` (for systems written in
Go), the viewer. A reader turning longitude and latitude back into a building's
local metres must agree with `localframe.json` to a millimetre; positions read from
a package are within about a centimetre of Studio's (7 decimals of a degree).
`campus-world` and `simple-office-world` are `campus` and `simple-office` with
their floors pre-built: a reader reads them as it reads those, and the viewer shows
them as it shows those.

## Versioning

`manifest.json → format_version` follows semantic versioning. Readers should accept
any package with the same major version and ignore unknown files and properties.
