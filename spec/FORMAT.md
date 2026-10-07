# StoreyPath package format — version 0.4

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
| `schema/*.schema.json` | JSON Schema for every JSON file |
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

## Readers

`spec/conformance/` holds packages and coordinate pairs that every reader must read
the same way: Studio's own validator, the Go module in `go/` (for systems written in
Go), the viewer. A reader turning longitude and latitude back into a building's
local metres must agree with `localframe.json` to a millimetre; positions read from
a package are within about a centimetre of Studio's (7 decimals of a degree).

## Versioning

`manifest.json → format_version` follows semantic versioning. Readers should accept
any package with the same major version and ignore unknown files and properties.
