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

DWG drawings and the language model need two more programs, `dwg2dxf` and
`llama-server`, and the model itself: [Without Docker](../README.md#without-docker)
has the steps for macOS and Linux.

## In the browser

```sh
uv run storeypath serve --data projects/ --open
```

runs StoreyPath Studio as a web application (this is what the container runs):
create projects, drop in drawings, choose which plans are which floors, then
align, convert, review, place on the map and export — with progress for every
step. Everything is computed on this machine.

## Workflow

A **workspace** (`*.spproj`) is the working file of one project. It keeps the
drawings' locations, every ID ever issued, your corrections and the export
history, so a project can be re-converted and re-exported any number of times
with the same IDs.

```sh
storeypath new acme.spproj --name "Acme Headquarters"           # generates the project code
storeypath add-location acme.spproj RUH --name "Riyadh campus"  # → K7Q2XM-RUH
storeypath add-building acme.spproj K7Q2XM-RUH HQ --name "Headquarters"
storeypath place acme.spproj K7Q2XM-RUH-HQ --lat 24.7136 --lon 46.6753 --x 125000 --y 48000 --units mm --bearing 20  # optional
storeypath add-floor acme.spproj K7Q2XM-RUH-HQ plans/level-0.dxf --ordinal 0
storeypath add-floor acme.spproj K7Q2XM-RUH-HQ plans/level-1.dwg --ordinal 1

storeypath convert acme.spproj                       # read drawings; keeps existing IDs
storeypath review acme.spproj                        # check and correct, in the browser
storeypath export acme.spproj -o acme.storeypath     # writes and validates the package
storeypath validate acme.storeypath
```

`storeypath review` opens the review editor: each floor drawn over its original
drawing, with the spaces that need a look listed first: no type, no name or
number, the labels of several rooms in one space (a doorway without a door
block, or a missing wall), or a space open to the outside. Click a space to
correct its type, name or number, accept it as it is, **ignore** it (not worth
anything: a sliver, a pocket) or **hide** it (real, but not shown unless asked
for: a shaft). Hidden and ignored spaces keep their IDs, are marked as such in the
package, and appear again with *Hidden and ignored* in the editor and the viewer. Every change is saved
to the workspace file immediately, and *Re-read drawing* converts a revised
drawing without leaving the page.

The same is possible from the command line:

```sh
storeypath list acme.spproj --review                 # what needs a look, and why
storeypath fix acme.spproj K7Q2XM-RUH-HQ-F01-0014 --type office --name "Quiet room"
storeypath fix acme.spproj K7Q2XM-RUH-HQ-F01-0021    # accept as it is
storeypath fix acme.spproj K7Q2XM-RUH-HQ-F01-0022 --name ""   # drop a wrong name
```

Placing buildings on the map (`place`) is optional: an unplaced building is
exported, and shown in 3D, with its true shape and size around 0°N 0°E, marked
`placed: false` in the package.

Corrections are kept in the workspace and re-applied on every
conversion. When a revised drawing is converted, each space is matched to its
previous version by room number and by overlap: matches keep their ID, removed
spaces are retired (their IDs are never reused), and new spaces get new codes.

### Several floors in one drawing

Architects often put every plan of a building (with elevations, sections and a
site plan) in one drawing. `storeypath views` lists the plans it finds, with their
titles and the floor each title names; pick one per floor with `--view`, by number
or by part of its title (or give `--region x0,y0,x1,y1` yourself). The plans are
drawn apart from each other, so `align` works out how far: floors share columns and
outside walls, so each plan is moved to where its walls overlap the floor below.

```sh
storeypath views house.dwg
storeypath add-floor house.spproj K7Q2XM-HOME-VILLA house.dwg --ordinal 0 --view "ground floor"
storeypath add-floor house.spproj K7Q2XM-HOME-VILLA house.dwg --ordinal 1 --view "first floor"
storeypath align house.spproj K7Q2XM-HOME-VILLA
storeypath levels house.spproj K7Q2XM-HOME-VILLA   # floor heights from the sections
storeypath convert house.spproj
```

`levels` reads the level labels on the sheets' sections and elevations (*+3.65 FIRST
FLOOR SLAB LVL*, *+6.95 ROOF SLAB LVL*, *+8.65 PARAPET LVL*; other languages by the
language model) and sets each floor's height, its elevation and the roof's parapet.
In the web app this happens when the plans are found, and each plan's height and
parapet can be changed before it is added. Walls with a terrace or balcony on one
side and no room on the other are exported as `parapets`, that high.

Units are read from what is drawn, so a drawing whose unit setting is wrong is
still read right: door swings with their leaf (about 0.85 m), the typical dimension
(the size of rooms and walls), the typical text height, and a note that states the
units (*ALL DIMENSIONS ARE IN MM*, read by the language model in other languages).
`views` says what it found; when the clues disagree or show too little it says it is
not sure, and `add-floor` warns. `--units` overrides. The units are kept with each
floor, in the web app too, so a project converts the same way again.

## Reading a drawing without being told its layers

The default profile, `auto`, reads every plan's layers from what is drawn on them
(see [analyse.py](src/storeypath/analyse.py)): walls are pairs of parallel lines a
wall's thickness apart that join into one frame; glazing is thin pairs in line with
the walls (and a layer named for windows is glazing, however thick its frames are
drawn); doors are quarter-circle swings; columns are small repeated shapes; room
outlines each hold one room's name; labels are texts that name rooms. Dashed lines
and evenly spaced lines (stair treads, tiles, tables) are not walls. What each layer
was read as is shown per floor in the web app and kept in the workspace. A YAML
profile (below) can still be given instead.

## The language model

Room names the rules don't know — abbreviations, misspellings, other languages —
are read by a small language model running locally on the CPU with llama.cpp's
`llama-server`; so are sheet titles when finding plans, notes that state the units, and
level labels on sections. Its answers are limited to
StoreyPath's types by a JSON schema and are stored in the workspace (`readings`),
so a project converts the same way again, with or without the model.

| Environment | |
|---|---|
| `STOREYPATH_MODEL` | the `.gguf` model (default: the newest in `STOREYPATH_MODELS`) |
| `STOREYPATH_MODELS` | the model folder (default `/opt/storeypath/models`, as in the container) |
| `STOREYPATH_LLAMA_SERVER` | the `llama-server` program (default: from `PATH`) |
| `STOREYPATH_MODEL_URL` | use an already running `llama-server` instead |
| `STOREYPATH_THREADS` | CPU threads for the model (default: all) |
| `STOREYPATH_GPU_LAYERS` | layers on the GPU, with a CUDA build of `llama-server` (e.g. `99`: all; the GPU image sets it) |

Without a model everything works on the rules alone; `convert --no-model` skips it.
Outside the container, install `llama-server` and fetch the model as in
[Without Docker](../README.md#without-docker).
[eval/](eval) scores a model on room labels, sheet titles, layer names, unit notes and
level labels
(`uv run python eval/run.py --model path/to/model.gguf`).

`storeypath save-as-new` copies a workspace as a *different* project with its own
project code; a plain file copy is the same project.

## Looking at the plans (vision)

A drawing is made to be printed and read by people, and everything a builder needs is
on the print. Studio can look at it the same way: each room it found is drawn as
printed, outlined in red, and a vision model says whether that is really one room and
what kind it is. Code keeps the exact geometry and IDs; the model makes the calls a
person makes at a glance:

- an unnamed area that is not a room (the garden inside a plot wall, a sheet frame, a
  gap) is set aside: listed under "Hidden and ignored", for a person to restore;
- a room with no type gets the one its furniture and fixtures show (a bed, a WC);
- an outline holding several rooms is divided where they meet: the lines that may
  divide it (a line drawn across it, a wall carried on past where it stops) are shown
  one at a time in blue, its two sides lettered, and the model says what each side
  is. Where the sides differ, code cuts exactly along the line, then each piece is
  looked at on its own: a cut stays only where both pieces are rooms, of different
  kinds (a strip along the windows is part of the room it was cut from). The pieces
  take the names of the labels in them, the doors go to the piece they stand by, and
  an opening joins the pieces;
- an outline that is only part of a room is listed for review.

Every decision is marked `vision` and listed for a person to check. The answers are
kept in the workspace by room shape, so converting again asks only about rooms that
changed, and the project converts the same way without the model.

The model is any OpenAI-compatible endpoint that takes images: `llama-server` (with
the model's `--mmproj`) or vLLM on a GPU, or a hosted service.

| Environment | |
|---|---|
| `STOREYPATH_VISION_URL` | the endpoint, e.g. `http://127.0.0.1:8105/v1` (none: no vision) |
| `STOREYPATH_VISION_MODEL` | the model name, when the server serves several |
| `STOREYPATH_VISION_KEY` | a bearer token, for hosted services |
| `STOREYPATH_VISION_PARALLEL` | questions in flight at once (default 2) |

Outside Docker, `uv sync --extra vision` adds what rendering needs (matplotlib,
Pillow). Measured on 119 rooms of two houses and an interior designer's furniture
plan, each checked by hand, Gemma 4 31B (4-bit, about 22 GB of GPU memory) judged 84%
of outlines and 85% of types right; `convert --no-vision` skips it.

## Symbols drawn in a plan (optional, research use only)

A room with no name can still be told by what is drawn in it. With
[SymPoint-V2](https://github.com/nicehuster/SymPointV2), a network trained on
floor plans to spot doors, windows, fixtures and stairs, Studio types such rooms:
a toilet and a bath make a bathroom, a toilet alone a WC (`restroom`), a stove or a
fridge a kitchen, a washing machine a laundry, a bed a bedroom, a flight of stairs
filling the room a stair room. Only symbols found with a score of 0.8 or more count,
named rooms keep the type their name gives, and every room typed this way is listed
for review (`type_source` `symbols:…`). What it finds is kept with the floor and used
again until the drawing, the part of it read or its units change.

It is not part of StoreyPath and is not installed with it: its repository states no
licence and its weights were trained on non-commercial data (FloorPlanCAD, CC BY-NC),
so use it **for research only**. To try it:

```sh
docker/fetch-symbols.sh                                 # its code (pinned) and weights (checksum-verified)
docker build -f docker/Dockerfile -t storeypath/studio . # baked in, PyTorch for the CPU; offline as before
```

or outside Docker, `uv sync --extra symbols` and `STOREYPATH_SYMBOLS=../docker/symbols`.
It runs on the CPU in a process of its own (a plan takes a few seconds);
`convert --no-symbols` skips it. Images are built without it unless it was fetched,
and release images only when the repository variable `STOREYPATH_SYMBOLS` is
`research`. On plans it was not trained on it mistakes things (wall-mounted air
conditioners for windows, grid lines for walls), so it is used only to type rooms, and
only from fixtures inside them.

## Finding spaces

Spaces come from room outlines (closed polylines drawn around each room, such as
NCS `A-AREA`) when a drawing has them. Otherwise they are found from the walls,
the way a person reads a plan:

- Everything on the wall, window and column layers forms the walls, whether drawn
  as single lines, double lines or fills, straight, diagonal or curved.
- Doors close the openings they stand in: door blocks, or doors drawn as loose
  lines, by the closed position of their swing (both leaves of a double door).
- Glazing and closed door leaves on door/window layers continue the wall across
  their gap; a cross marking a lift car does not.
- Door and window tags (D4, W12, SD2) say which an opening is: a door tag makes a
  door of an opening drawn as glazing or left open. Glazing between two rooms at a
  door's width with no window tag is a sliding door, listed for review.
- Blocks on door layers are doors only when they have a swing, a door's name, or a
  sliding door's shape: basins, baths and cars put on a door layer are not doors.
- A lift with no way in drawn is given a door, for review, on the wall it shares
  with a hall, lobby or corridor.
- Where a wall stops and another faces its end within `walls.max_doorway`, the gap
  is a doorway: closed, and recorded as an `opening` between the two rooms.
- Wider gaps in the outside walls (up to `walls.max_opening`) are spanned by the
  building's outline, so a room behind a missing door is kept, and listed for review.
- An open-plan area holding the labels of several rooms is divided where it is
  narrowest between them (cuts totalling at most `spaces.max_split`); each part is
  listed for review, and the dividing lines become `opening`s.

Set `spaces.method` in the profile to `outlines` or `walls` to use one way only.

## Layer-mapping profiles

How a drawing's layers, blocks and labels map onto StoreyPath's space types is
set by a YAML profile. The built-in `ncs` profile covers US National CAD Standard /
AIA layer names (`A-AREA`, `A-AREA-IDEN`, `A-DOOR`, …). Copy
[src/storeypath/profiles/ncs.yaml](src/storeypath/profiles/ncs.yaml) to support
other naming schemes and pass its path with `add-floor --profile`.

## DWG files

DXF is read directly. DWG needs an external converter, which this package does not
include (the container does): install [LibreDWG](https://www.gnu.org/software/libredwg/)
(`dwg2dxf`, see [Without Docker](../README.md#without-docker)) or the
[ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter),
or save the drawing as DXF.

## Development

```sh
uv run pytest
```

Tests run against generated floor plans (`storeypath.samples`) whose correct
answer is known. When the package models change, regenerate the committed
schemas with `uv run storeypath schema ../spec/schema` (a test checks they match).
