# How StoreyPath fits together

- [The pieces](#how-it-fits-together) · [For other systems](#for-other-systems) ·
  [Stable IDs](#stable-ids) · [Status](#status)

## How it fits together

```
DWG / DXF ──▶ StoreyPath Studio ──▶ package (*.storeypath) ──▶ StoreyPath Viewer (in your web app)
               find · read · review · export                  └─▶ any other system: import objects.csv,
                                                                    map our IDs to its own
```

| Folder | What it is |
|---|---|
| [studio/](../studio) | **StoreyPath Studio** (Python): reads drawings, keeps IDs stable across revisions, the web app (Review, the 3D page, Navigate), exports packages |
| [storeypath-viewer/](https://github.com/StoreyPath/storeypath-viewer) | **StoreyPath Viewer**, a submodule (`git submodule update --init`): the package format ([spec/FORMAT.md](https://github.com/StoreyPath/storeypath-viewer/blob/main/spec/FORMAT.md), schemas, conformance), the viewers to embed in web apps (3D world, map view, 2D SVG plan) and the Go reader |
| [docker/](../docker) | The Studio image and the scripts that fetch its models; Compose with the GPU helper beside it |
| [docs/](.) | These pages, and the pictures of the README ([docs/media](media) takes them again) |

Studio is a FastAPI app on uvicorn with PostgreSQL and PostGIS for its database (the
image runs its own); its pages are plain HTML, CSS and ES modules, no build step
([studio/src/storeypath/review_app](../studio/src/storeypath/review_app/README.md)).
The decisions behind it: [Studio 2's design](DESIGN-STUDIO-2.md).

## For other systems

The format and everything that reads it are [StoreyPath Viewer](https://github.com/StoreyPath/storeypath-viewer), a repository
of its own that Studio pins as a submodule (`storeypath-viewer/`):

- **The package format**: [spec/FORMAT.md](https://github.com/StoreyPath/storeypath-viewer/blob/main/spec/FORMAT.md) (format 0.9) and JSON
  Schemas in [spec/schema](https://github.com/StoreyPath/storeypath-viewer/tree/main/spec/schema): one building per package, plain JSON,
  GeoJSON and CSV in a ZIP, readable without our code. Each export says what was
  added, changed and retired; `objects.csv` lists every ID with its parents;
  `navigation.json` is the building's walking network, with the rule every reader
  finds the same way by.
- **Go**: [go/](https://github.com/StoreyPath/storeypath-viewer/tree/main/go) (`github.com/storeypath/storeypath-viewer/go`) reads and
  validates packages, and finds the way in a building, standard library only.
- **Viewers** to embed: [viewer/](https://github.com/StoreyPath/storeypath-viewer/tree/main/viewer) (the 3D world, a map view) and
  [viewer/svg](https://github.com/StoreyPath/storeypath-viewer/tree/main/viewer/svg) (a 2D plan with no WebGL, for any machine).
- **Conformance**: [spec/conformance](https://github.com/StoreyPath/storeypath-viewer/tree/main/spec/conformance) holds packages that every
  reader must read the same way, with items, a building moved on the map and a desk
  carried to another building, and ways on them that every reader must find the same.
- **Items are for asset management**: where things are, and where they have been,
  never who holds them. An inventory system keys its records to the items' IDs, as
  every system keys its own to StoreyPath's.

## Stable IDs

Every object gets a hierarchical ID that says exactly where it is, and that stays
the same when revised drawings are imported again:

```
PROJECT-LOCATION-BUILDING-FLOOR-OBJECT        CAMP05-CAMPUS-MAIN-F02-0127
```

(the demo campus's president's office). Systems store our ID as the key of their own
mapping (to employees, desks, bookings…). Each export carries a list of IDs added,
changed and retired since the previous one, and a retired ID is never issued again.
Lifts and stairs keep one object code on every floor they serve when Studio finds them,
and share one *stack* however they were drawn. Items are the exception that proves the
rule: their ID is an asset's tag (`7K2Q-XM9F-4DP`: ten random symbols of Crockford's
base32 and a Luhn mod 32 check symbol), of no project or place, so it does not change
when they move, even to another building. A Studio draws a new one again while any
of its items has it; between Studios a clash is unlikely, and a reader refuses the same
item ID in packages of two projects.

## Status

| # | Milestone | State |
|---|---|---|
| 1 | Format v0, IDs, workspace, schemas, validator | done |
| 2 | One floor from a DXF with room outlines → package → viewer | done |
| 3 | Spaces from walls when there are no room outlines; review editor | done |
| 4 | Several floors and buildings aligned; placement | done: floors on one sheet found and stacked; placement by anchor + bearing; site plans |
| 5 | Navigation graph and routing | done (format 0.8): the walking network in every package, the same way in Studio, Go and the viewers |
| 6 | DWG and real-world samples | done for a first real sheet set; a wider sample collection next |
| 7 | Hardening: change reports, docs, releases | done: change lists; releases with a multi-arch image on ghcr.io |
| 8 | Reading drawings like a person: layers, plans, units, room names | done |
| 9 | Looking at the plans with a vision model, on a GPU | done: the GPU helper |
| 10 | Completing a floor in review: walls, dividers, doors, windows, openings, spaces | done |
| 11 | Furniture and equipment with IDs of their own; capacity and grade | done (format 0.7) |
| 12 | Many people at once: Studio on PostgreSQL, one editor a floor, changes seen live, undo and history | done |
| 13 | Floor and wall finishes | done (format 0.9) |
| 14 | Doors that open when walking; Studio's own 3D page | done |
