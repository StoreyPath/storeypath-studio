# StoreyPath package format — version 0.9

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
| `navigation.json` | The walking network of the building: where a person can walk, and how long it takes, for finding the way (format 0.8) |
| `schema/*.schema.json` | JSON Schema for every JSON file |
| `world/<floor-id>.glb` | Optional: each floor already built in 3D, as binary glTF (Pre-built 3D) |
| `FORMAT.md` | This document |

Readers locate each file through `manifest.json → files`, by its role (`location`,
`buildings`, `floors`, `spaces`, `zones`, `openings`, `objects`, `changes`, `items`,
`catalogue`, `navigation`); the names in the table are the defaults.

**JSON.** Keys are case-sensitive: a key that differs from the format's only in case is
not that key, and a reader may refuse the file. Every number is finite (NaN and
Infinity are not JSON), and numbers and booleans are never written as strings.

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
  (`…-F01-0023`, `…-F02-0023`) when Studio finds them there itself. What links them
  across floors is their `stack` (0.8, Stacks): one drawn on a floor by hand has a code
  of its own, and the same stack.
- An ID is ASCII and at most 84 characters long (five segments of at most 16).
- Items (furniture and equipment) are not places: an item's ID is an asset ID, ten
  random symbols and a check symbol, `7K2Q-XM9F-4DP` (Asset IDs), of no project and no
  place. A place's ID never has that form (Studio's project codes are six symbols, and
  it refuses a building code that would give one): a reader taking an ID apart into
  its segments refuses an asset ID.

Importing systems should store our ID as the key of their own mapping, and use
`changes.json` on each new export to add, update and remove mappings.

## Coordinates

GeoJSON in WGS84 longitude/latitude (EPSG:4326, RFC 7946), rounded to 7 decimals
(about 1 cm). Lengths and heights are in meters. `manifest.json → placements` holds,
for each building, the anchor and bearing used to place the drawing, so local
drawing coordinates can be rebuilt if needed. It holds exactly one placement for each
building of the package, and so for every building an item stands in. Longitudes are
in (-180, 180]: a building across the antimeridian has longitudes on both sides of it.

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
windows of its own; a glazed veranda is not), `capacity`, `capacity_from`, `grade`
(Capacity), `stack` (Stacks), `floor_finish` and `wall_finish` (Finishes), `hidden`, `ignored`.
Geometry: Polygon or MultiPolygon. A space is what walls, doors and windows enclose:
walls stand on its edges.

**zone** — `type`, `name`, `number`, `drawing_label`, `space_id`, `floor_id`, `area_m2`,
`display_point`, `capacity`, `capacity_from`, `grade`, `floor_finish` (Finishes), `hidden`, `ignored`. Geometry:
Polygon or MultiPolygon. A zone is a
part of a space used for one thing, with no wall between it and the rest of the
space: a majlis and a dining area in one hall, a passage running into a living room,
team areas in an open office. The zones of a space divide it exactly (together they
cover it, without overlapping) and are listed in its `zones`, each once (a space lists
only its own zones: each one's `space_id` is that space); a space used for one thing
has none. Zone edges are not walls: draw them as light lines, and walk across
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

**Capacity** (0.7). `capacity` is how many people a space or zone is meant to seat:
the number a person set in review (`capacity_from: "review"`), else the workplaces of
the items standing in it (`"items"`: a desk seats one; a space divided into zones
counts its zones' too), else null. 0 is a room meant to seat nobody (a meeting room
set so); `capacity` and `capacity_from` are both given or both null. `grade` is who it is laid out for: the highest grade among the desks standing
in it (`president`, `c_level`, `director`, `manager`, `section_head`, `senior`,
`junior`), or null. Both are the building's as drawn and furnished: a system placing
people takes them as defaults, and may keep its own (a capacity it sets, a
designation of the office) by the space's ID, which no later package changes. A
change of capacity or grade is a change of the space in `changes.json`.

**opening** — `type`, `floor_id`, `connects` (IDs of the one or two spaces it joins),
`exterior` (true when it leads outside), `width_m` and `span` (jamb to jamb, when
known), `swings` (a door's leaves as the plan draws them, each `[hinge, free edge
when open]`, when known: which side it hinges on and which way it opens; two for a
double door), `sill_m` and `height_m` (how high above the floor it starts, and how
tall it is, from the drawing's schedule of openings, when known), `hidden`, `ignored`.
Geometry: Point in the wall (never null).

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
  "sequence": 3,              // this export; 1 for the project's first
  "previous_sequence": 1,     // the last export that held this building (null: its first)
  "added":   ["…"],           // IDs new since then
  "changed": ["…"],           // IDs whose geometry or properties changed
  "retired": ["…"],           // IDs removed since then
  "all_retired": ["…"],       // every ID this building has ever retired
  "moved_away": [             // items carried since to another building (0.7)
    { "id": "7K2Q-XM9F-4DP", "building_id": "K7Q2XM-RUH-ANNEX" } ]
}
```

Every list is about the package's building since it was last exported (0.7; before,
since the project's previous export). Export numbers run across the project, so
`previous_sequence` may be several lower than `sequence`: a reader that applied a
later package of this building than `previous_sequence`, or an earlier one, missed or
holds another of its exports. `previous_sequence` is before `sequence` and the same as
the manifest's `export.previous_sequence`.

- `added`: new since then. An item a package of the project held before, carried into
  this building, is `changed`, not added.
- `changed`: anything whose geometry or properties changed in the building's own frame
  (Coordinates): moving the building on the map lists the building as changed, and
  nothing in it. A location is changed when its name or address changes (its outline
  follows its buildings).
- `retired`: removed since then, including an item that package held and that was
  taken away since, wherever it was then. Nothing listed is in the package.
- `all_retired`: every ID this building has ever retired: its own objects', and the
  items its packages held that were taken away since. An ID in use again is not
  listed. A system that skipped an export uses it to clean up its mappings.
- `moved_away`: items the building's last package held that stand in another building
  now (not retired: that building's package holds them). An item is never both moved
  away and retired.

## Items

Items are the furniture and equipment people place on floors: desks (by grade:
a manager's, a junior staff member's), central photocopiers, wireless access points,
sofas, TVs. `items.geojson` holds them; `catalogue.json` says what each type is.

**An item's ID does not say where it is, nor whose it is.** It is an asset ID, the
tag on the asset: ten random symbols and a check symbol, `7K2Q-XM9F-4DP` (Asset IDs,
below). A desk carried to another office, another floor or another building keeps its
ID; where it stands is in its properties, and the project it is of is the package's.
Like every ID, an item's is never issued again once it is retired.

```json
{ "type": "Feature", "id": "7K2Q-XM9F-4DP",
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
  that does not read the catalogue. The geometry is its footprint, a Polygon.
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
  from north (a desk's front is where its user sits): (the placement's `bearing` + 180
  − `rotation_deg`) mod 360, which readers check to half a degree.
- `values` are its details entered in StoreyPath, by the catalogue's field keys.

`catalogue.json` (`schema/catalogue.schema.json`) lists the types: a `code` that is
kept for good and never given to another type (a type no longer used is `retired`),
English and Arabic names, a `category` (furniture, equipment, appliance), a size, how
it is mounted (`floor`, `wall`, `ceiling`), a colour (`#rrggbb`), `workplaces` (how many people work
at one: a desk, 1; 0 for most else) and `grade` (who a desk is for, 0.7), and its `fields`. Each field
says who enters it: `owner: "storeypath"` (what is physical: a colour, a size, a
model) or `"system"` (the system that manages the asset: an access point's network).
A reader keeps the `system` fields itself, by the item's ID; the package never
carries them. The catalogue is the organization's: the same for every project.

**Kiosks.** An item whose type is `KIOSK`, or `KIOSK-` and more (`KIOSK-WALL`), is
where a wayfinding kiosk stands: a screen where people type their employee number
and are shown their office. Its front is the side its screen faces, where people
stand to use it. A system running kiosks links each of its own to such an item by
the item's ID: the item's floor is the kiosk's floor, and its `local` position (its
`display_point` on the map) is the "you are here" of the kiosk's maps, and where a
way to an office will start. A kiosk moved keeps its ID, so the link holds; one
retired leaves its kiosk without a place until it is linked again.

Items are for asset management: where things are, and where they have been. They
are not inventory: nothing in a package says who holds what. An inventory system
keys its own records to the items' IDs, as every system keys its own to StoreyPath's.

Items go through `changes.json` as everything else: one moved, turned or given other
details in its building is `changed`, so a system can keep the history of where each
item has been. An item is in the package of the building it stands in. One carried
to another building since this building was last exported is listed in `moved_away`
with the building it went to: it is not retired, and that building's package holds it
(as `changed`, with its new floor and position) when that building is next exported.

### Asset IDs (0.8)

An asset ID is ten random symbols and a check symbol of Crockford's base32, whose
alphabet is `0123456789ABCDEFGHJKMNPQRSTVWXYZ` (no I, L, O or U), written in upper
case in groups of four, four and three joined by hyphens: `7K2Q-XM9F-4DP`. Packages
hold it only so (`^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{3}$`,
with its check symbol right), and systems should keep it so.

**Random.** The ten symbols are 50 random bits, from a cryptographically secure source.
A system issuing them checks a new one against every one it has issued (Studio: its
database's items, by their key) and draws again when it is taken. Between systems that
do not share their IDs, a clash is unlikely: two that issue 100,000 assets each give
one the same ID with a chance of about 1 in 110,000. An asset is one project's: a
reader that meets the same asset ID in packages of two projects (the manifests'
`project.id`) refuses the later one, as a clash, not the same asset. In packages of one
project it is the same asset, carried from building to building (`moved_away`).

**The check symbol** is Luhn mod 32 over the ten symbols' values (their places in the
alphabet: `0` is 0, `A` 10, `Z` 31). From the left, the 2nd, 4th, 6th, 8th and 10th
values are doubled: a doubled value *v* is 2*v* when that is below 32, else 2*v* − 31
(its two base-32 digits added). The ten are added; the check symbol's value is
(32 − sum mod 32) mod 32. Checked so, the eleven (the check symbol not doubled) add up to
a multiple of 32. For `7K2QXM9F4D`, the values 7 19 2 23 29 20 9 15 4 13 become
7 7 2 15 29 9 9 30 4 26, whose sum 138 is 10 mod 32: the check value is 22, `P`, and
the ID `7K2Q-XM9F-4DP`. The check catches every symbol mistyped, and every two
neighbouring symbols swapped but a `0` and a `Z` (`…0Z…` for `…Z0…`, as Luhn's 09
and 90): then it is right still.

**What people type.** Where a person gives an asset ID (a search box, a command line,
a call made for a person), a reader reads it so: letters in either case; `O` as `0`,
`I` and `L` as `1`; hyphens, spaces, tabs and line breaks left out wherever they are;
then eleven symbols whose check symbol is right are the asset ID, written as above.
Anything else, or more than 64 characters as typed, is not an asset ID. An ID read
from a package is never read so: it is written as above, or it is not an ID.

`spec/conformance/asset-ids.json` holds check symbols, IDs right and wrong, and what
people type with what it reads as, for every reader to agree on.

## Stacks (0.8)

A lift, a staircase or an escalator is one thing through the floors it serves; on
each floor it is a space of type `elevator`, `stairs` or `escalator`. Their `stack`
says which are the same one: a key the spaces of one lift share on every floor it
serves, and no other space of the building has. A ramp between floors (one ramp's
spaces on two or more floors) has one too; any other space has `stack: null`.

```json
"properties": { "kind": "space", "type": "elevator", "floor_id": "K7Q2XM-RUH-HQ-F02",
                "stack": "K7Q2XM-RUH-HQ-F00-0023", … }
```

Studio links them so: spaces of these types on different floors of a building are one
stack when they have the same object code, or when they are of the same type and their
outlines, in the building's own frame, overlap by at least 30% of the smaller one; and
through any number of floors (what is linked to what is linked is one stack). A
person may set it in review, and wins: a space linked by hand to another floor's is
linked to it alone (others may still be linked to it); one set as not linked stands
alone. The key is the ID of the stack's space on the lowest floor it serves (the
lowest ID there): it stays while that space does, and may change when the stack does. A system keys its own
lifts and stairs to the spaces' IDs; `stack` tells it which of them are one.

A stack may have more than one space on a floor (a staircase drawn as two spaces), and
none on a floor it passes without a door.

## Navigation (0.8)

`navigation.json` is the walking network of the building: the points a person is at or
passes (nodes), and the ways between them (edges), with how far each is and how long it
takes. Studio makes it from the package's spaces, zones, openings, stacks and kiosks,
so that every reader finds the same way between two places without working out any
geometry: Studio, the Go module and the viewers route on it the same way (Routing). A
package of format 0.8 may lack it (a reader then has no network to route on); one of
an earlier format has none.

```json
{
  "speed_m_s": 1.3,
  "buildings": ["K7Q2XM-RUH-HQ"],
  "floors": [ { "id": "K7Q2XM-RUH-HQ-F00", "building_id": "K7Q2XM-RUH-HQ", "name": "Ground floor",
                "ordinal": 0, "elevation": 0.0 } ],                       // lowest first
  "places": [ { "id": "K7Q2XM-RUH-HQ-F01-0069", "kind": "space", "space_id": null,
                "floor_id": "K7Q2XM-RUH-HQ-F01", "type": "office", "label": "OFFICE 112" } ],
  "nodes": [ { "id": "door:K7Q2XM-RUH-HQ-F00-0048", "kind": "door", "floor_id": "K7Q2XM-RUH-HQ-F00",
               "space_id": null, "zone_id": null, "local": { "x_m": 167.0, "y_m": 59.5 },
               "lonlat": [46.6772311, 24.7131542], "opening_id": "K7Q2XM-RUH-HQ-F00-0048",
               "spaces": ["K7Q2XM-RUH-HQ-F00-0001", "K7Q2XM-RUH-HQ-F00-0002"] } ],
  "edges": [ { "from": "approach:K7Q2XM-RUH-HQ-F00-0048@K7Q2XM-RUH-HQ-F00-0001",
               "to": "door:K7Q2XM-RUH-HQ-F00-0048", "kind": "door", "length_m": 1.5, "seconds": 1.2,
               "cost": 1.2, "accessible": true, "space_id": "K7Q2XM-RUH-HQ-F00-0001", "zone_id": null,
               "path": [[167.0, 58.0], [167.0, 59.5]] } ]
}
```

Positions are in the building's own frame (Coordinates), in metres, as items' `local`;
a node's `lonlat` is the same point on the map. Lengths are in metres, times in
seconds.

**Nodes.** Each has an `id` made from what it is, so it is the same from one export to
the next while that is:

| `kind` | `id` | Where |
|---|---|---|
| `door` | `door:<opening ID>` | each door and opening (`type` `door` or `opening`, never a window) between two spaces a person walks in, at the middle of its span; `spaces` lists them. A lift or stairs with no way in drawn (one drawn in review) joins a space it shares an edge with: `door:<space ID>+<space ID>` (the two in order), `opening_id` null |
| `entrance` | `door:<opening ID>` | a door or opening to the outside (`exterior`), on any floor: where a campus's ways would join the building's |
| `approach` | `approach:<opening ID>@<space ID>` | in front of a door in each space it opens into: half the space's depth there, at most 2 m (so the approaches along a corridor are on its middle line) |
| `room` | `room:<space or zone ID>` | where a person arrives in each space (in each zone of a space divided into zones): its label point, clear of the walls |
| `lift`, `stairs`, `escalator`, `ramp` | `lift:<object code>@<floor ID>`, … | each lift, stairs, escalator and ramp space, on its floor, in place of a `room` node; `stack` is its stack |
| `kiosk` | `kiosk:<item ID>` | each wayfinding kiosk (Items, Kiosks): where people stand before its screen, 0.6 m in front of it, in the space it stands in; `item_id` is the item |

`space_id` and `zone_id` are the space, and the zone of a divided space, a node is in
(a door's are null: `spaces` says what it joins). Readers ignore kinds of nodes and
edges they do not know: a later version may add some.

**What is walked.** A person walks in the spaces and zones of these types, unless
hidden or ignored in review: `corridor`, `lobby`, `open_area`, `elevator`, `stairs`,
`escalator`, `ramp`, `parking`, `terrace`, `balcony`, `living_room`, `dining_room`
(spaces to pass through), and `office`, `room`, `meeting_room`, `restroom`,
`kitchen`, `storage`, `bedroom`, `bathroom`, `dressing_room`, `laundry`,
`prayer_room`, `unspecified` (spaces a person goes to). Not `utility` (plant and
electrical rooms), `shaft` or `open_to_below`. A door into what is not walked is not
a node. The zones of a divided space are one room to walk across, as no wall parts
them (a zone not walked, a void, is left out of it).

**Edges.** Every edge joins two nodes, both ways; at most one joins any two. `path` is
its line from `from` to `to`, at least two points; a reader going from `to` to `from`
follows it backwards.

| `kind` | Joins | `seconds` |
|---|---|---|
| `door` | a door to each of its approaches: through the door | `length_m` / `speed_m_s` |
| `walk` | two nodes of one room (its approaches, room nodes, kiosks, lift or stairs), along the shortest way across it that keeps 0.35 m from its walls where it is wide enough (round its corners), and up to them in a passage narrower than that. A walk is left out where the room's other walks already join its ends within 2% and 5 cm: a corridor's approaches are joined each to the next | `length_m` / `speed_m_s` |
| `lift` | each floor a lift's stack serves to each other one | 30 (waiting) + 4 a floor |
| `stairs`, `escalator`, `ramp` | each floor its stack serves to the next one up it serves | 12 a floor |

`length_m` is the length of `path` (for a lift, stairs, escalator or ramp: the height
between the floors); "a floor" is each of the building's floors passed, by their
order. `space_id` is the space a `walk` or `door` edge is in (for a `door`, the space
of its approach), and `zone_id`, in a divided space, the zone most of its line is in.
`accessible` is false for `stairs` and `escalator`. `cost` is what routing takes the
fewest of: `seconds`, and 30 more for a `door` edge into a space a person goes to (an
office, a meeting room): a way through someone's office costs a minute more than it
takes, so the way round by the corridor is taken unless it is that much longer. A
way to or from such a room pays half of that, whichever way it takes. `seconds` and
`cost` have one decimal.

**Places.** `places` lists the spaces and zones the network goes through, with their
`type` and `label`: what a step calls them. The label is the name and the number
("OFFICE 112"; the name alone when it holds the number), the name, the type and the
number ("Room 114"), or "the" and the type ("the corridor"); a type in words, with
spaces for underscores, `elevator` as "lift" and `unspecified` as "room".

### Routing

A way is asked for from a place to a place, each one of: a node's ID; a space's ID
(its `lift`, `stairs`, `escalator`, `ramp` or `room` node; for a space divided into
zones, its zones' `room` nodes); a zone's ID (its `room` node); an item's ID (a
kiosk's node, else the zone or space it stands in). A place of several nodes is any of
them. With `accessible`, no edge whose `accessible` is false is taken.

Every reader takes the same way: each edge costs round(`cost` × 10), a whole number;
the nodes are taken in order of their distance from the start and, at one distance,
of their IDs (compared as strings of bytes), all the start's nodes at 0; a node keeps
the way it was first reached by at its least distance; the way ends at the first of
the destination's nodes taken. (Dijkstra's algorithm, its queue ordered by distance,
then ID.)

A way is given as:

- `nodes`: its nodes' IDs, in order; `metres` and `seconds`: the sums of its edges'
  `length_m` and `seconds`, added in that order, then rounded to two decimals as
  floor(x × 100 + 0.5) / 100.
- `legs`: the walking on one floor between rides from floor to floor (one more than
  the rides; one may have no edges): `floor_id`, `points` (the line to draw: its first
  node's point, then each edge's `path` in the way's direction, without a point the
  same as the one before it) and `metres` (rounded so).
- `changes`: each ride between floors (one `lift` edge, or `stairs`, `escalator` or
  `ramp` edges one after another): `by` (its kind), `from_floor_id`, `to_floor_id`,
  `from_node`, `to_node`, `floors` (how many of the building's floors apart) and
  `direction` (`up` or `down`).
- `steps`: what to tell a person, each a `kind`, values and `text` in English; a
  system words them in its own language from the kind and values.

| `kind` | Values | `text` |
|---|---|---|
| `start` | `node`, `node_kind`, `place`, `floor_id` | "Start at the kiosk in RECEPTION 017", "Start at the entrance into the corridor", "Start in OFFICE 001", "Start at the lift", "Start at the door of …" |
| `walk` | `floor_id`, `metres`, `along`, `to`, `place` | "Walk 24 m along CORRIDOR to the lift": for each leg at least half a metre long, in whole metres (floor(x + 0.5)); `along` is the place most of it is in (of those as long, the first by ID): "along" a corridor or ramp, "through" anything else; `to` is the next ride's kind ("the stairs"), or `destination` (then `place` is it, by its label). Walking mostly in the destination itself: "Walk 6 m to OPEN OFFICE 117" |
| `take` | `by`, `from_floor_id`, `to_floor_id`, `floors`, `direction` | "Take the lift up to Floor 1" (the floor by its name) |
| `arrive` | `place`, `floor_id`, `side` | "OFFICE 112 is on your left": `side` is `left` or `right` when the way's last door is the destination's own (from another place into it), else `ahead`; `here` when the start is the destination ("You are at …"). The label begins with a capital ("The meeting room is ahead") |

The side is where the destination's door is as a person walks to it: *w* is the way
they walk, from the point 2 m back along the way's line (or its start) to the approach
in front of the door; *r* the way into the room, from the door to the approach inside.
With *d* = *w*·*r* and *c* = *w*ₓ*r*ᵧ − *w*ᵧ*r*ₓ: `ahead` when *d* > 0 and
|*c*| ≤ 0.5 *d*, else `left` when *c* > 0, `right` when *c* < 0 (a building's frame has
*y* to the left of *x*, as drawings do).

A way that cannot be found (none, or none without stairs) is said to be none; an ID
the network does not have is an error.

## Finishes (0.9)

What a room's floor and walls are finished in: carpet tiles, porcelain, marble, oak
planks, paint, wallpaper, wall tiles… A space may name its floor's finish
(`floor_finish`) and its walls' (`wall_finish`), a zone its floor's (`floor_finish`: a
zone has no walls of its own), each a code of StoreyPath's finishes below: a fixed set
every reader has, the same in every package. They are what a person chose in review;
null is the room's type's default (Defaults).

```json
"properties": { "kind": "space", "type": "lobby", "floor_finish": "FLOOR-MARBLE-WHITE",
                "wall_finish": "WALL-STONE", … }
```

- **The floor** of a space shows its `floor_finish`, else its type's default. A zone
  shows its own, else its space's, else its type's default (a hall divided into zones
  finished as one, and one of them otherwise).
- **The walls.** A wall stands between two rooms (or a room and the outside): each side of
  it, each face, is finished as the room it faces is: the `wall_finish` of the space on
  that side (a zone's walls are its space's), else that space's type's default; a face
  towards no room (the building's outside, the jambs of openings), as `exterior` in the
  list. The wall over a door or a window, and under a window, is the wall.
- **A code a reader does not know** (one a later version adds) is shown as the type's
  default; a reader refuses only a code not of the form of a floor's (`FLOOR-` and
  capital letters, digits and hyphens: `^FLOOR(-[A-Z0-9]+)+$`, at most 40 characters)
  or a wall's (`^WALL(-[A-Z0-9]+)+$`). Codes are kept for good: a finish is never
  renamed to another code, nor its code given to another.

`spec/finishes.json` is the list as data, for every reader: each finish's `code`, what it
`applies` to (`floor` or `wall`), its `group`, its English and Arabic names (`name`,
`name_ar`), its colour on the whole (`tone`, `#rrggbb`: what to fill a room with on a plan
coloured by finish, or show before a texture), how rough it is (`roughness`, 0–1) and what
StoreyPath's viewer paints it with (`size_m`, the metres its image covers, and `paint`: a
kind of painter and its values; nothing is downloaded); and the defaults by type, and the
exterior's finish. The viewer has it as `viewer/src/finishes.js`, the Go module as
`go/finishes.json` (Finishes(), FloorFinishOf, WallFinishOf), Studio as it is.

Floors:

| Code | Name | الاسم | Group |
|---|---|---|---|
| `FLOOR-CARPET-CHARCOAL` | Carpet tiles, charcoal | بلاط سجاد، فحمي | Carpet |
| `FLOOR-CARPET-GREY` | Carpet tiles, mid grey | بلاط سجاد، رمادي متوسط | Carpet |
| `FLOOR-CARPET-BLUEGREY` | Carpet tiles, blue-grey | بلاط سجاد، رمادي مزرق | Carpet |
| `FLOOR-CARPET-WARMGREY` | Carpet tiles, warm grey | بلاط سجاد، رمادي دافئ | Carpet |
| `FLOOR-CARPET-NAVY` | Carpet tiles, navy | بلاط سجاد، كحلي | Carpet |
| `FLOOR-CARPET-BEIGE` | Carpet tiles, beige | بلاط سجاد، بيج | Carpet |
| `FLOOR-CARPET-GREEN` | Carpet tiles, green | بلاط سجاد، أخضر | Carpet |
| `FLOOR-CARPET-BURGUNDY` | Carpet tiles, burgundy | بلاط سجاد، عنابي | Carpet |
| `FLOOR-CARPET-PATTERN` | Patterned carpet, grey and blue | سجاد منقوش، رمادي وأزرق | Carpet |
| `FLOOR-CARPET-PRAYER` | Prayer carpet, rows | سجاد صلاة بصفوف | Carpet |
| `FLOOR-VINYL-GREY` | Vinyl sheet, grey | فينيل لفائف، رمادي | Vinyl |
| `FLOOR-VINYL-BLUE` | Vinyl sheet, clinic blue | فينيل لفائف، أزرق طبي | Vinyl |
| `FLOOR-LVT-OAK` | Vinyl planks, light oak look | ألواح فينيل بمظهر البلوط الفاتح | Vinyl |
| `FLOOR-LVT-WALNUT` | Vinyl planks, walnut look | ألواح فينيل بمظهر الجوز | Vinyl |
| `FLOOR-PORCELAIN-WHITE` | Porcelain tiles 60×60, white | بلاط بورسلان 60×60، أبيض | Porcelain tiles |
| `FLOOR-PORCELAIN-GREY` | Porcelain tiles 60×60, light grey | بلاط بورسلان 60×60، رمادي فاتح | Porcelain tiles |
| `FLOOR-PORCELAIN-BEIGE` | Porcelain tiles 60×60, beige | بلاط بورسلان 60×60، بيج | Porcelain tiles |
| `FLOOR-PORCELAIN-DARK` | Porcelain tiles 60×120, dark grey | بلاط بورسلان 60×120، رمادي داكن | Porcelain tiles |
| `FLOOR-MARBLE-WHITE` | Marble, white | رخام أبيض | Marble and terrazzo |
| `FLOOR-MARBLE-BEIGE` | Marble, beige | رخام بيج | Marble and terrazzo |
| `FLOOR-MARBLE-BLACK` | Marble, black | رخام أسود | Marble and terrazzo |
| `FLOOR-TERRAZZO-LIGHT` | Terrazzo, light | تيرازو فاتح | Marble and terrazzo |
| `FLOOR-TERRAZZO-DARK` | Terrazzo, dark | تيرازو داكن | Marble and terrazzo |
| `FLOOR-WOOD-OAK` | Oak planks, light | ألواح بلوط فاتح | Wood |
| `FLOOR-WOOD-WALNUT` | Walnut planks | ألواح جوز | Wood |
| `FLOOR-WOOD-HERRINGBONE` | Oak herringbone | باركيه بلوط بنقش عظم السمكة | Wood |
| `FLOOR-CONCRETE-POLISHED` | Polished concrete | خرسانة مصقولة | Concrete and resin |
| `FLOOR-CONCRETE` | Concrete | خرسانة | Concrete and resin |
| `FLOOR-EPOXY-GREY` | Epoxy, grey | إيبوكسي رمادي | Concrete and resin |
| `FLOOR-RUBBER` | Rubber, anti-slip studs | مطاط مانع للانزلاق | Technical |
| `FLOOR-RAISED-ACCESS` | Raised access floor | أرضية مرفوعة | Technical |

Walls:

| Code | Name | الاسم | Group |
|---|---|---|---|
| `WALL-PAINT-WHITE` | Paint, white | دهان أبيض | Paint |
| `WALL-PAINT-OFFWHITE` | Paint, warm off-white | دهان أبيض دافئ | Paint |
| `WALL-PAINT-GREY` | Paint, light grey | دهان رمادي فاتح | Paint |
| `WALL-PAINT-SAND` | Paint, sand | دهان رملي | Paint |
| `WALL-PAINT-BLUE` | Paint, pale blue | دهان أزرق فاتح | Paint |
| `WALL-PAINT-GREEN` | Paint, pale green | دهان أخضر فاتح | Paint |
| `WALL-PAINT-CHARCOAL` | Paint, accent dark grey | دهان رمادي داكن | Paint |
| `WALL-PAINT-NAVY` | Paint, accent navy | دهان كحلي | Paint |
| `WALL-PAINT-TERRACOTTA` | Paint, accent terracotta | دهان تيراكوتا | Paint |
| `WALL-PAPER-LINEN` | Wallpaper, linen beige | ورق جدران، كتان بيج | Wallpaper |
| `WALL-PAPER-STRIPES` | Wallpaper, grey stripes | ورق جدران، خطوط رمادية | Wallpaper |
| `WALL-PAPER-GEOMETRIC` | Wallpaper, geometric | ورق جدران، نقش هندسي | Wallpaper |
| `WALL-PAPER-DAMASK` | Wallpaper, damask | ورق جدران، دمشقي | Wallpaper |
| `WALL-TILE-WHITE` | Wall tiles 30×60, white | بلاط جدران 30×60، أبيض | Tiles |
| `WALL-TILE-GREY` | Wall tiles 30×60, grey | بلاط جدران 30×60، رمادي | Tiles |
| `WALL-TILE-MOSAIC` | Mosaic tiles, blue | فسيفساء زرقاء | Tiles |
| `WALL-WOOD-SLATS` | Oak slats | شرائح بلوط | Wood and stone |
| `WALL-WOOD-WALNUT` | Walnut panels | ألواح جوز للجدران | Wood and stone |
| `WALL-STONE` | Stone cladding | تكسية حجرية | Wood and stone |

**Defaults** (a room given none; a type a reader does not know, as `unspecified`; the
building's outside: `WALL-PAINT-WHITE`):

| Types | Floor | Walls |
|---|---|---|
| `bedroom`, `living_room`, `dining_room`, `dressing_room` | `FLOOR-WOOD-OAK` | `WALL-PAINT-WHITE` |
| `corridor` | `FLOOR-PORCELAIN-GREY` | `WALL-PAINT-WHITE` |
| `elevator`, `stairs`, `escalator`, `ramp` | `FLOOR-TERRAZZO-LIGHT` | `WALL-PAINT-WHITE` |
| `kitchen`, `balcony`, `terrace` | `FLOOR-PORCELAIN-BEIGE` | `WALL-PAINT-WHITE` |
| `lobby` | `FLOOR-MARBLE-BEIGE` | `WALL-PAINT-WHITE` |
| `meeting_room` | `FLOOR-CARPET-WARMGREY` | `WALL-PAINT-WHITE` |
| `office`, `room`, `open_area` | `FLOOR-CARPET-BLUEGREY` | `WALL-PAINT-WHITE` |
| `parking` | `FLOOR-EPOXY-GREY` | `WALL-PAINT-WHITE` |
| `prayer_room` | `FLOOR-CARPET-GREEN` | `WALL-PAINT-WHITE` |
| `restroom`, `bathroom`, `laundry` | `FLOOR-PORCELAIN-WHITE` | `WALL-TILE-WHITE` |
| `storage`, `utility`, `shaft`, `unspecified`, `open_to_below` | `FLOOR-CONCRETE` | `WALL-PAINT-WHITE` |

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
| `lights` | `lightPanel` | `walk` | ceiling panels, 60 cm square, about every 2.4 m in each space or zone (none in shafts and lifts): their faces down |
| `wall`, `wallTop` | `wall`, `wallTop` | `full` | the walls' faces (with those of the wall over and under openings), and their tops and undersides, full height |
| `parapet`, `parapetTop` | `wall`, `wallTop` | `full` | the parapets, likewise |
| `skirting` | `skirting` | | a skirting board along every face of the walls and parapets, and under windows |
| `heads`, `headTop` | `wallPlain`, `wallTop` | `full` | the wall over doors, doorways and windows: under it and its ends (its faces are `wall`'s), and its top |
| `sills` | `wallPlain` | | the wall under windows: its ends (its faces are `wall`'s) |
| `sillBoard` | `sillBoard` | | a board on each window's sill |
| `glass`, `frame` | `glass`, `frame` | `full` | windows: the glass, and its frame and mullions |
| `door`, `doorFrame` | `door`, `doorFrame` | `full` | door leaves, open as the plan draws them, and their frames |
| `trim`, `handle` | `trim`, `handle` | `full` | architraves round doors, on both faces of their wall; lever handles each side of each leaf |
| `items:light`, `items:light:high` | `item` | `full` for `high` | the floor's items, a box each (`"form": "light"`), for a view of many floors: those wholly below `options.cutHeight`, and the others (on a wall, under the ceiling) |
| `obstacles` | | | lines (mode `LINES`) at the floor, not drawn: what someone walking bumps into (walls, windows, open leaves) |

`view` says when a piece shows: `full` unless the walls are cut low, as on a plan;
`cut` only then; `walk` when walking on that floor; `xray` in the see-through view;
with none, always. The pieces of spaces and zones hidden or ignored in review are
meshes of their own (`floor:shaft:hidden`, `"hidden": true`), shown only when asked
for. A plain glTF viewer shows every mesh at once. The items' pieces are kept apart
from the rest, so that a viewer leaves them out until they are asked for; a floor
with no items has none. Drawn in detail (a desk with its chairs, and what goes with
its grade), items are not in the file: a viewer builds them from the items, as
quickly as it would read them (files of builder 2 have them, as `items` and
`items:high`).

The walls and parapets cut low are not in the file: they are `wall`, `wallTop`,
`parapet` and `parapetTop` with every vertex higher than `elevation` +
`options.cutHeight` brought down to that height (and on the faces, its v to
1 − `cutHeight`), shown with `view` `cut`, the tops with the material `wallCut`.
Texture coordinates (`TEXCOORD_0`) are only on what has a texture: the floor
finishes (metres east and north) and the walls' and parapets' faces (v = 1 − the
height above the floor, in metres). What is built of boxes (`heads`, `headTop`, `sills`,
`glass`, `frame`, `door`, `doorFrame`, `trim`, `handle`, `skirting`, `sillBoard`,
`lights`) shares its corners between its faces and has no normals: flat-shaded, as
glTF draws a mesh without them.

The floor finishes and volumes have a vertex attribute `_ROOM` (unsigned integer),
an index into `rooms` below: the space or zone each vertex belongs to, so that a
click on a floor tells which room it is. The walls' and parapets' faces (builder 4) have
it too: the space each face faces (Finishes), or a value past the end of `rooms` (65535,
or 4294967295 for a floor of more than 65534 rooms and zones) for a face towards none. A
viewer draws each triangle in the finish of its room (a floor's, a wall's), from the
package's properties: the file holds no finish, so it stays right when they change. The items' pieces have `_ITEM`, an index
into `items` below, likewise; their colours are the vertices' (`COLOR_0`: their
type's colour, and shades of it), how rough and how metallic each is `_FINISH`
(unsigned bytes, normalised: roughness, metalness), and they have no normals
(flat-shaded). The materials in the file are plain colours; StoreyPath's viewer
draws each piece with its own, by `material` and `type`, in the look it is asked for.

**Extras.** The scene's `extras.storeypath`:

| Key | |
|---|---|
| `project_id`, `building_id`, `floor_id` | what it is |
| `export_sequence` | the export it was built for: a file whose sequence is not the manifest's is stale; build that floor instead |
| `builder` | the version of StoreyPath's builder that made it: 4 since the room each wall's face faces (`_ROOM` on `wall` and `parapet`, and the faces of the wall over and under openings in `wall`); 3 since skirting, architraves, handles, window boards, ceiling panels and the finer furniture; 2 since items (format 0.6); a viewer builds a floor itself from a file of another version (a file without it is of 1, and has no items) |
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
  read a 0.5 package as it is, ignoring the folder (readers of 0.7 and later refuse a
  newer minor version instead: Versioning).

## Changes from 0.8

- Spaces' `floor_finish` and `wall_finish`, zones' `floor_finish` (Finishes): what a
  room's floor and walls are finished in, codes of StoreyPath's fixed set
  (`spec/finishes.json`); null, the type's default; a code a reader does not know, the
  type's default too.
- Pre-built 3D of builder 4: the walls' faces say which room each faces (`_ROOM`), and
  the faces of the wall over and under openings are in `wall`.
- A reader of 0.8 refuses a 0.9 package (Versioning); a reader of 0.9 reads 0.8 and
  earlier ones as before (every room as its type).

## Changes from 0.7

- `navigation.json` and `manifest.json → files.navigation`: the building's walking
  network (Navigation), and the rule by which every reader finds the same way on it.
- Spaces' `stack` (Stacks): which lifts, stairs and escalators on different floors are
  one, worked out from their codes and outlines, or set by a person.
- Items' IDs are asset IDs (Items, Asset IDs): `7K2Q-XM9F-4DP`, random with a check
  symbol, of no project, where they were the project's code and the item's number
  (`K7Q2XM-I000142`). `manifest.json → export.next_item` is gone: a reader ignores it
  in a package that has it. A package of 0.6 or 0.7 holds items by their IDs then.
- A reader of 0.7 refuses a 0.8 package (Versioning); a reader of 0.8 reads 0.7 and
  earlier ones as before (no stacks, no network, items by the project's numbers).

## Changes from 0.6

- One building per package (One building per package): `scope` always names the one
  building. A reader of 0.4 to 0.6, which read a newer minor version under the earlier
  rule, takes a 0.7 package as a part of a project.
- What `changes.json` compares is each building in its own frame: moving a building
  on the map lists the building as changed, not what is in it (Coordinates).
- Items' `local`: where an item stands in its building, the position it is placed by
  (Items). `changes.json → moved_away`: items carried to another building.
- The project file sent between Studios is a file of its own (`*.storeypath-project`),
  not a package carrying `studio/`.
- Spaces' and zones' `capacity`, `capacity_from` and `grade` (Capacity); the
  catalogue's `workplaces` and `grade`.
- `changes.json` is about the building: `previous_sequence` is the last export that held
  it, `all_retired` its own; `manifest.json → export.next_item` (until 0.8).
- Readers refuse a package of a newer minor version (Versioning), and check more: one
  placement for each building, items' headings, zones listed by their own space,
  finite numbers, case-sensitive keys.

## Changes from 0.5

- `items.geojson` and `catalogue.json`: furniture and equipment, with IDs of their own
  that do not change when they move (Items): until 0.8, the project's code, `-I` and
  six digits (`K7Q2XM-I000142`). Readers of 0.5 ignore them.
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
taken away (the building changed, and the office the desk left, which seats one fewer;
nothing else; the desk moved away; the TV retired; every item's `local` as it was),
then the Annex's, the desk in it. They have items: desks of several grades (one in a
zone, one in a building turned on the map), a photocopier, two access points, a sofa,
a TV, a bed and a wayfinding kiosk; lifts and stairs through the Headquarters' three
floors (their stacks), a corridor divided into two zones, and finishes (0.9) on some of
the Headquarters' rooms: the reception in white marble with stone walls, the meeting room
in oak herringbone with oak slats, an office in navy carpet with linen wallpaper, a
restroom in dark porcelain with mosaic, the divided hall's walls pale green and its two
zones' floors grey vinyl and light terrazzo; every other room as its type. `routes.json` holds ways
on the first two that every reader must find the same (Navigation, Routing): the same
nodes, changes of floor, legs and steps, and the same lengths and times to a
centimetre and a tenth of a second. `asset-ids.json` holds asset IDs (Asset IDs) every
reader must check and read the same: check symbols, IDs right, wrong by a symbol or
two swapped, and what people type. The others are packages of earlier formats, which
readers still read: `campus` (0.6, the whole campus, with items), `campus-world` and `simple-office-world` (pre-built in 3D: a reader reads
them as it reads `campus` and `simple-office`, and the viewer shows them as it shows
those), `campus-whole-1`, `campus-part`, `campus-whole-3` (0.4), `simple-office`,
`simple-office-2` and `unplaced`.

A reader that takes packages from uploads bounds what it reads. A building's package
is a few megabytes; the Go module reads by default at most 64 MiB a file and 256 MiB in
all, a file at most 100 times its size in the archive (files above 1 MiB), and at most
200,000 entries in any list, and refuses longer IDs before taking them apart.

## Versioning

`manifest.json → format_version` follows semantic versioning, and says which readers
can process a package. A reader reads packages of its own major version that are not
newer than it, and ignores unknown files, properties, and `objects.csv` rows of kinds it
does not know (as 0.6 added items), with the IDs of those rows where `changes.json` lists
them. Before 1.0 a minor version may change what a package means (0.4's `scope`, 0.7's
one building per package), so a reader refuses a package of a newer minor version
(0.10 for a reader of 0.9), saying it must be updated; a newer patch version (0.9.1)
only adds properties and is read. From 1.0, a reader reads any package of its major
version. A format version is ASCII `major.minor`, an optional `.patch`, and an optional
`-` or `+` suffix (`^[0-9]+\.[0-9]+(\.[0-9]+)?([-+][0-9A-Za-z.-]+)?$`); a reader refuses
anything else as not a format version.
