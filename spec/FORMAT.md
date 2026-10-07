# StoreyPath package format — version 0.7

A StoreyPath package (`*.storeypath`) describes one building of a project: the
building, its location, its floors, the spaces on each floor (offices, corridors,
elevators, …), the zones that divide open spaces, the doors between spaces, and the
furniture and equipment on them. A project of several buildings is several packages,
one each (One building per package). It is a ZIP archive of plain JSON, GeoJSON and
CSV files, so any system can read it without special software.

A copy of this document is included in every package.

## Files

| File | Contents |
|---|---|
| `manifest.json` | Format version, project, export number, file list, counts, the building's placement, and the building it holds (`scope`) |
| `location.geojson` | Locations (sites, campuses) |
| `buildings.geojson` | The building, with its footprint |
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
coordinates change once it is placed.

**A building's own frame.** Every building has its own frame: the coordinates of
its drawings, in metres, shared by all its floors. The placement only says where
that frame is on the map, so moving a building (placing it, turning it, shifting it
on its site plan) changes the longitude and latitude of everything in it, and
nothing in the building itself: its rooms, doors and items stay where they are in
its frame, `changes.json` lists only the building as changed, and items carry their
position in the frame (`local`). The anchor point (`x`, `y`) of a placement is the
point of the frame put at (`lon`, `lat`): it may move between exports; the frame
does not.

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

`kind` is `project`, `location`, `building`, `floor`, `space`, `zone`, `opening` or
`item` (0.6). A reader skips rows of a kind it does not know: a later format may add some.
Parent columns are empty where they do not apply; `space_id` is a zone's space.
`lon`/`lat` is the label point (spaces, zones, buildings, locations) or the door
position (openings).

## changes.json

```json
{
  "sequence": 3,              // this export; 1 for the first
  "previous_sequence": 2,
  "added":   ["…"],           // IDs new since the building was last exported
  "changed": ["…"],           // IDs whose geometry or properties changed
  "retired": ["…"],           // IDs removed since then
  "all_retired": ["…"],       // every ID this building has ever retired
  "moved_away": [             // items carried since to another building (0.7)
    { "id": "K7Q2XM-I000142", "building_id": "K7Q2XM-RUH-ANNEX" } ]
}
```

What changed is what changed in the building's own frame (Coordinates): moving the
building on the map lists the building as changed, and nothing in it. A system that
skipped an export can use `all_retired` to clean up its mappings.

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
  "geometry": { "type": "Polygon", "coordinates": [ … ] },     // its footprint, on the map
  "properties": {
    "kind": "item", "type": "DESK-MANAGER", "category": "furniture", "name": "Manager's desk",
    "floor_id": "K7Q2XM-RUH-HQ-F02", "building_id": "K7Q2XM-RUH-HQ",
    "space_id": "K7Q2XM-RUH-HQ-F02-0142", "zone_id": null,       // where its middle stands
    "local": { "x_m": 31.25, "y_m": 12.4, "rotation_deg": 90 },  // where it stands in its building
    "display_point": [46.67, 24.71], "heading": 270.0,          // its front faces west
    "width_m": 1.8, "depth_m": 0.9, "height_m": 0.75,
    "mount": "floor", "elevation_m": 0.0,                        // wall: its bottom; ceiling: null
    "values": { } } }
```

- `type` is a code of `catalogue.json`; `name` is that type's English name, for a reader
  that does not read the catalogue.
- `space_id`, and `zone_id` when the space is divided, are where its middle stands at
  export: null when it is in none.
- `local` (0.7) is where it stands in its building: its middle in the building's own
  frame (`x_m`, `y_m`: metres from the origin of the building's drawings), and the way
  its front faces there (`rotation_deg`, counter-clockwise: 0 is the drawings' −y, 90
  their +x). This is what the item is placed by: moving the building on the map never
  changes it, so a system keeping the history of where items have been keeps `local`
  and the floor, not the longitude and latitude.
- The footprint, `display_point` and `heading` follow from `local` and the building's
  placement, for maps: `heading` is the way its front faces, in degrees clockwise
  from north (a desk's front is where its user sits).
- `values` are its details entered in StoreyPath, by the catalogue's field keys.

`catalogue.json` (`schema/catalogue.schema.json`) lists the types: a `code` that is
kept for good and never given to another type (a type no longer used is `retired`),
English and Arabic names, a `category` (furniture, equipment, appliance), a size, how
it is mounted (`floor`, `wall`, `ceiling`), a colour, and its `fields`. Each field
says who enters it: `owner: "storeypath"` (what is physical: a colour, a size, a
model) or `"system"` (the system that manages the asset: an access point's network).
A reader keeps the `system` fields itself, by the item's ID; the package never
carries them. The catalogue is the organization's: the same for every project.

Items are for asset management: where things are, and where they have been. They
are not inventory: nothing in a package says who holds what. An inventory system
keys its own records to the items' IDs, as every system keys its own to StoreyPath's.

Items go through `changes.json` as everything else: one moved, turned or given other
details in its building is `changed`, so a system can keep the history of where each
item has been. An item is in the package of the building it stands in. One carried
to another building since this building was last exported is listed in `moved_away`
with the building it went to: it is not retired, and that building's package holds it
(as `changed`, with its new floor and position) when that building is next exported.

## One building per package

From 0.7 a package holds exactly one building: `manifest.json → scope` names it.

```json
"scope": { "buildings": ["7KQ2MX-SITE-ENG"] }
```

It holds that building, its floors and everything on them, and the location it
stands in; `sources`, `placements` and `counts` cover it alone. A project of several
buildings is exported as several packages, one each.

What is not in the package says nothing about the project's other buildings: a
reader that keeps a project's buildings leaves the others as they are, and compares
a building only with the last package that held it. `changes.json` lists what
changed in that building alone since it was last exported. Export numbers run on
across the project's packages, so a later package always has a higher `sequence`.

Packages of 0.4 to 0.6 may hold several buildings, or a whole project (without
`scope`); readers still read them. StoreyPath Studio no longer writes them. A
StoreyPath *project file* (`*.storeypath-project`, sent from one Studio to another,
with the drawings) is not a package: readers refuse it.

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
| `items`, `items:high` | `item` | `full` for `high` | the floor's items, each drawn as a simple shape of its kind (a desk: its top, ends, modesty panel and a chair): those wholly below `options.cutHeight`, and the others (on a wall, under the ceiling) |
| `items:light`, `items:light:high` | `item` | `full` for `high` | the same items as a box each (`"form": "light"`), for a view of many floors |
| `obstacles` | | | lines (mode `LINES`) at the floor, not drawn: what someone walking bumps into (walls, windows, open leaves) |

`view` says when a piece shows: `full` unless the walls are cut low, as on a plan;
`cut` only then; `walk` when walking on that floor; `xray` in the see-through view;
with none, always. The pieces of spaces and zones hidden or ignored in review are
meshes of their own (`floor:shaft:hidden`, `"hidden": true`), shown only when asked
for. A plain glTF viewer shows every mesh at once. The items' pieces are kept apart
from the rest, so that a viewer leaves them out until they are asked for; a floor
with no items has none.

The walls and parapets cut low are not in the file: they are `wall`, `wallTop`,
`parapet` and `parapetTop` with every vertex higher than `elevation` +
`options.cutHeight` brought down to that height (and on the faces, its v to
1 − `cutHeight`), shown with `view` `cut`, the tops with the material `wallCut`.
Texture coordinates (`TEXCOORD_0`) are only on what has a texture: the floor
finishes (metres east and north) and the walls' and parapets' faces (v = 1 − the
height above the floor, in metres).

The floor finishes and volumes have a vertex attribute `_ROOM` (unsigned integer),
an index into `rooms` below: the space or zone each vertex belongs to, so that a
click on a floor tells which room it is. The items' pieces have `_ITEM`, an index
into `items` below, likewise; their colours are the vertices' (`COLOR_0`: their
type's colour, and shades of it), and they have no normals (flat-shaded, as glTF
draws a mesh without them). The materials in the file are plain colours;
StoreyPath's viewer draws each piece with its own, by `material` and `type`.

**Extras.** The scene's `extras.storeypath`:

| Key | |
|---|---|
| `project_id`, `building_id`, `floor_id` | what it is |
| `export_sequence` | the export it was built for: a file whose sequence is not the manifest's is stale; build that floor instead |
| `builder` | the version of StoreyPath's builder that made it: 2 since items (format 0.6); a viewer builds a floor itself from a file of another version (a file without it is of 1, and has no items) |
| `origin` | `lon`, `lat`, `kx`, `ky`: the frame above |
| `options` | the sizes it was built with, in metres: `slab` (thickness), `doorHead`, `windowSill`, `windowHead`, `wallThickness` (where the floor gives none), `cutHeight` |
| `elevation`, `wall_height` | the floor's elevation, and how high its walls rise above it |
| `rooms` | the IDs `_ROOM` indexes |
| `items` | the IDs `_ITEM` indexes (format 0.6) |

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

## Changes from 0.6

- One building per package (One building per package): `scope` always names the one
  building. A reader of 0.4 to 0.6 reads a 0.7 package as a part of a project.
- What `changes.json` compares is each building in its own frame: moving a building
  on the map lists the building as changed, not what is in it (Coordinates).
- Items' `local`: where an item stands in its building, the position it is placed by
  (Items). `changes.json → moved_away`: items carried to another building.
- The project file sent between Studios is a file of its own (`*.storeypath-project`),
  not a package carrying `studio/`.

## Changes from 0.5

- `items.geojson` and `catalogue.json`: furniture and equipment, with IDs of their own
  that do not change when they move (Items). Readers of 0.5 ignore them.
- Pre-built 3D: each floor's items in pieces of their own, and the extras' `builder`
  and `items`. A viewer builds a floor itself from a file of an earlier builder.

## Readers

`spec/conformance/` holds packages and coordinate pairs that every reader must read
the same way: Studio's own validator, the Go module in `go/` (for systems written in
Go), the viewer. A reader turning longitude and latitude back into a building's
local metres must agree with `localframe.json` to a millimetre; positions read from
a package are within about a centimetre of Studio's (7 decimals of a degree).
`campus-hq`, `campus-annex`, `campus-hq-2` and `campus-annex-2` are one project of
this format, exported a building at a time: its first two exports, then the
Headquarters' after it was moved on the map, a desk carried to the Annex and a TV
taken away (only the building changed; the desk moved away; the TV retired; every
item's `local` as it was), then the Annex's, the desk in it. They have items:
desks of several grades (one in a zone, one in a building turned on the map), a
photocopier, two access points, a sofa, a TV and a bed. The others are packages of
earlier formats, which readers still read: `campus` (0.6, the whole campus, with
items), `campus-world` and `simple-office-world` (pre-built in 3D: a reader reads
them as it reads `campus` and `simple-office`, and the viewer shows them as it shows
those), `campus-whole-1`, `campus-part`, `campus-whole-3` (0.4), `simple-office`,
`simple-office-2` and `unplaced`.

## Versioning

`manifest.json → format_version` follows semantic versioning. Readers should accept
any package with the same major version and ignore unknown files, properties, and
`objects.csv` rows of kinds they do not know (as 0.6 added items), with the IDs of those
rows where `changes.json` lists them.
