# StoreyPath Studio

The authoring side of [StoreyPath](https://github.com/StoreyPath/storeypath):
converts DWG/DXF floor plans into StoreyPath packages, lets you review and
correct the result, and exports packages that keep the same object IDs every
time.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). From this folder:

```sh
uv sync
uv run storeypath demo demo/                  # sample drawings → workspace → package
uv run storeypath view demo/demo.storeypath   # open it in the viewer
```

## Workflow

A **workspace** (`*.spproj`) is the working file of one project. It keeps the
drawings' locations, every ID ever issued, your corrections and the export
history, so a project can be re-converted and re-exported any number of times
with the same IDs.

```sh
storeypath new acme.spproj --name "Acme Headquarters"           # generates the project code
storeypath add-location acme.spproj RUH --name "Riyadh campus"  # → K7Q2XM-RUH
storeypath add-building acme.spproj K7Q2XM-RUH HQ --name "Headquarters"
storeypath place acme.spproj K7Q2XM-RUH-HQ --lat 24.7136 --lon 46.6753 --x 125000 --y 48000 --units mm --bearing 20
storeypath add-floor acme.spproj K7Q2XM-RUH-HQ plans/level-0.dxf --ordinal 0
storeypath add-floor acme.spproj K7Q2XM-RUH-HQ plans/level-1.dwg --ordinal 1

storeypath convert acme.spproj                       # read drawings; keeps existing IDs
storeypath list acme.spproj --review                 # what needs a human look
storeypath fix acme.spproj K7Q2XM-RUH-HQ-F01-0014 --type office --name "Quiet room"
storeypath export acme.spproj -o acme.storeypath     # writes and validates the package
storeypath validate acme.storeypath
```

Corrections made with `fix` are kept in the workspace and re-applied on every
conversion. When a revised drawing is converted, each space is matched to its
previous version by room number and by overlap: matches keep their ID, removed
spaces are retired (their IDs are never reused), and new spaces get new codes.

`storeypath save-as-new` copies a workspace as a *different* project with its own
project code; a plain file copy is the same project.

## Layer-mapping profiles

How a drawing's layers, blocks and labels map onto StoreyPath's space types is
set by a YAML profile. The built-in `ncs` profile covers US National CAD Standard /
AIA layer names (`A-AREA`, `A-AREA-IDEN`, `A-DOOR`, …). Copy
[src/storeypath/profiles/ncs.yaml](src/storeypath/profiles/ncs.yaml) to support
other naming schemes and pass its path with `add-floor --profile`.

## DWG files

DXF is read directly. DWG needs an external converter, which is not bundled:
install [LibreDWG](https://www.gnu.org/software/libredwg/) (`dwg2dxf`) or the
[ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter),
or save the drawing as DXF.

## Development

```sh
uv run pytest
```

Tests run against generated floor plans (`storeypath.samples`) whose correct
answer is known. When the package models change, regenerate the committed
schemas with `uv run storeypath schema ../spec/schema` (a test checks they match).
