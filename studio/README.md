# StoreyPath Studio

The authoring side of [StoreyPath](https://github.com/StoreyPath/storeypath):
converts DWG/DXF floor plans into StoreyPath packages, lets you review, correct and
complete the result (walls, doors, furniture and equipment), and exports packages
that keep the same object IDs every time.

- [Install](#install) · [In the browser](#in-the-browser) · [Commands](#commands)
- [Workflow](#workflow) · [Several floors in one drawing](#several-floors-in-one-drawing)
- [Reading a drawing without being told its layers](#reading-a-drawing-without-being-told-its-layers)
- [Private information](#private-information) · [The language model](#the-language-model) ·
  [Looking at the plans (vision)](#looking-at-the-plans-vision) ·
  [Symbols (research use only)](#symbols-drawn-in-a-plan-optional-research-use-only)
- [Finding spaces](#finding-spaces) · [Furniture and equipment](#furniture-and-equipment) ·
  [Other settings](#other-settings)
- [Layer-mapping profiles](#layer-mapping-profiles) · [DWG files](#dwg-files) ·
  [Development](#development)

The whole reading procedure, step by step:
[How Studio reads a drawing](../docs/HOW-STUDIO-READS-A-DRAWING.md).

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). From this folder:

```sh
uv sync
uv run storeypath demo demo/                  # sample drawings → workspace → packages (one per building)
uv run storeypath view demo/demo-HQ.storeypath   # open one in the viewer
```

DWG drawings and the language model need two more programs, `dwg2dxf` and
`llama-server`, and the model itself: [Without Docker](../README.md#without-docker)
has the steps for macOS and Linux. `uv sync --extra vision` adds what looking at the
plans with a vision model needs.

## In the browser

```sh
uv run storeypath serve --data projects/ --open
```

runs StoreyPath Studio as a web application (this is what the container runs):
create projects, drop in drawings, choose which plans are which floors, then
align, convert, review, place on the map and export — with progress for every
step. Everything is computed on this machine (and on the vision model's, when one
is set elsewhere). [What you can do in Studio](../README.md#what-you-can-do-in-studio)
goes through it.

Projects are kept in the data folder, one folder each named by the project's code:
`<data>/<code>/<code>.spproj`, with its `drawings/` and `exports/` beside it. The
catalogue of item types, `catalogue.json`, is in the data folder itself, for every
project.

To reach Studio from other machines, serve on all interfaces (`--host 0.0.0.0`).
It answers only to its own names: localhost, this machine's name and any address;
give another name it is reached by (a server's, a proxy's) with `--allowed-host`
(again for more) or `STOREYPATH_ALLOWED_HOSTS`. There are no user accounts yet:
trusted networks only.

## Commands

`storeypath <command> --help` gives every option.

| Command | What it does |
|---|---|
| `new FILE --name NAME` | create a workspace (`*.spproj`) for a new project; generates the project code |
| `save-as-new FILE TARGET --name NAME` | copy a workspace as a *different* project, with its own code (a plain file copy is the same project) |
| `add-location FILE CODE --name NAME` | add a location (site or campus); `--address` |
| `add-building FILE LOCATION-ID CODE --name NAME` | add a building to a location |
| `place FILE BUILDING-ID --lat --lon` | put a building on the map: a drawing point (`--x`, `--y`, `--units`), where it is on earth, and the bearing of the drawing's up (`--bearing`) |
| `views DRAWING` | list the plans in a drawing, their titles and floors, and the units it is read in; `--all` also elevations, sections and details; `--units` |
| `add-floor FILE BUILDING-ID DRAWING --ordinal N` | add a floor and its drawing; `--view` (a plan's number or part of its title), or `--region x0,y0,x1,y1`; `--units`, `--height`, `--parapet`, `--profile`, `--code`, `--name` |
| `align FILE BUILDING-ID` | line up floors drawn side by side; floors converted already stay where they are; `--reference` |
| `levels FILE BUILDING-ID` | floor heights and the roof's parapet from the level labels on sections and plans |
| `convert FILE` | read the drawings, keeping existing IDs; `--floor`, `--no-model`, `--no-vision`, `--no-symbols`; `--force` applies a reading held back because it would retire most of a floor |
| `list FILE` | objects with their IDs, types and labels; `--review` only the spaces that need a look, and why; `--floor`, `--retired` |
| `fix FILE ID` | correct a space: `--type`, `--name`, `--number` (`""` removes a wrong one); no options accepts it as it is; `--clear` removes the corrections |
| `review FILE` | open the review editor on this project (on this machine, port 8766) |
| `serve` | run the web app: `--data`, `--host`, `--port`, `--open`, `--allowed-host` |
| `export FILE -o OUT` | write one building's package (`--building`, by its ID or code; may be left out when the project has one), entered as an export only when valid |
| `validate PACKAGE` | check a package against the format |
| `private DRAWING OUT.dxf` | copy a drawing without its private information (see below) |
| `words DRAWING` | every word and string in a drawing, to look through for anything private |
| `view PACKAGE` | open a package in the viewer's example app |
| `demo DIR` | sample drawings, a workspace and its packages, to try things out |
| `profiles` | list the built-in layer-mapping profiles |
| `schema DIR` | write the JSON Schemas of the package files |

The commands that read texts take `--no-model` to leave the language model out.

## Workflow

A **workspace** (`*.spproj`) is the working file of one project. It keeps the
drawings' locations, every ID ever issued, your corrections and what you drew in
review, the items placed, every model answer, and the export history, so a project
can be re-converted and re-exported any number of times with the same IDs.

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
drawing — *as printed* (the drawing rendered as on paper, a pixel a centimetre,
drawn once and kept beside the workspace until the drawing changes; *Open print*
shows it full size; *Side by side* puts it beside Studio's spaces), or as its lines
— with the spaces that need a look listed first: no type, no name or number, the
labels of several rooms in one space (a doorway without a door block, or a missing
wall), a space open to the outside, and what a model decided. Click a space to
correct its type, name or number, accept it as it is (*Save*), set how many people
it seats, or **delete** it (not there, or not worth anything: a sliver, the outside).
A deleted space keeps its ID, is exported marked `ignored`, and comes back with
*Show deleted*. Right-click the plan to draw the walls, dividing lines, doors,
windows, openings and spaces the drawing leaves out, to resize a door, window or
opening, or to place furniture and equipment; the keys are on the page (W wall, V
divide, S space, D door, O opening, Del delete, N next to review, F fit). Every change
is saved to the workspace file immediately, and *Re-read drawing* converts a revised
drawing without leaving the page. The editor also shows the floor in 3D.

The same corrections are possible from the command line:

```sh
storeypath list acme.spproj --review                 # what needs a look, and why
storeypath fix acme.spproj K7Q2XM-RUH-HQ-F01-0014 --type office --name "Quiet room"
storeypath fix acme.spproj K7Q2XM-RUH-HQ-F01-0021    # accept as it is
storeypath fix acme.spproj K7Q2XM-RUH-HQ-F01-0022 --name ""   # drop a wrong name
```

Placing buildings on the map (`place`) is optional: an unplaced building is
exported, and shown in 3D, with its true shape and size around 0°N 0°E, marked
`placed: false` in the package. In the web app, each location's site plan places its
buildings relative to each other, and the site goes on the map as one.

A package holds **one building** (format 0.7): with several, `export --building`
says which. Its `changes.json` lists what changed in that building since the last
package that held it. An export also builds each floor in 3D ahead of time, as the
viewer would build it, and puts it in the package (`world/<floor-id>.glb`; see
*Pre-built 3D* in [FORMAT.md](../spec/FORMAT.md)), so that a slow machine shows the
building without building it. That takes Node.js (20.6 or newer; the container has
it): `node` on the `PATH`, or `STOREYPATH_NODE` set to it (empty: never). Without it
the package is exported as before, and the export says why.

Corrections are kept in the workspace and re-applied on every conversion. When a
revised drawing is converted, each space is matched to its previous version by room
number and by overlap: matches keep their ID, removed spaces are retired (their IDs
are never reused), and new spaces get new codes. A reading that finds no spaces on a
floor that has some, or would retire more than half of them (layers renamed, wrong
units), is held back: the floor keeps its rooms, and `convert --force` applies it if
the drawing really changed that much.

To continue a project in another Studio, the web app's *Download project* gives one
file (`*.storeypath-project`: the workspace, its drawings and the item types), which
the other Studio opens on its Projects page. A building's package opens there too:
its building is rebuilt with the same IDs, its floors without drawings until one is
added.

### Several floors in one drawing

Architects often put every plan of a building (with elevations, sections and a
site plan) in one drawing. `storeypath views` lists the plans it finds, with their
titles and the floor each title names; pick one per floor with `--view`, by number
or by part of its title (or give `--region x0,y0,x1,y1` yourself). The plans are
drawn apart from each other, so `align` works out how far: floors share columns and
outside walls, so each plan is moved to where its walls overlap the reference floor
(the lowest floor converted already, else the lowest).

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
language model), or the level each floor's plan marks (*+0.45 FFL*), and sets each
floor's height, its elevation and the roof's parapet. In the web app this happens
when the plans are found, and each plan's height and parapet can be changed before
it is added. Walls with a terrace or balcony on one side and no room on the other
are exported as `parapets`, that high.

Units are read from what is drawn, so a drawing whose unit setting is wrong is
still read right: door swings with their leaf (0.55 to 1.3 m), the typical dimension
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
outlines each hold one room's name; labels are texts that name rooms, and a layer
named for room tags holds labels whatever its texts (room codes such as
`RM-GF-33`). Dashed lines and evenly spaced lines (stair treads, tiles, tables) are
not walls. What each layer was read as is shown per floor in the web app and kept in
the workspace. A YAML profile (below) can still be given instead.

## Private information

A drawing carries more than the building. Each drawing added in the web app (unless
*Remove private information* is unticked) is first searched for what names people
and the project ([privacy.py](src/storeypath/privacy.py)): title blocks (the client,
owner, consultant, who drew and checked it, stamps, logos), names with a title,
phone numbers, emails, web addresses, permit, plot and licence numbers, block
attributes named for them, images, paper-space sheets and the file's hidden data;
with a model, also names without a title, companies and addresses (only the private
part of a text goes). A person unticks what to keep; only the cleaned copy is kept,
as `drawing-N.dxf`, and the file as sent is not. Each drawing's **Words** lists every
word and string left in it.

```sh
storeypath private house.dwg house-private.dxf   # the same cleaned copy
storeypath words house-private.dxf               # what is left, to look through
```

## The language model

Room names the rules don't know — abbreviations, misspellings, other languages —
are read by a small language model running locally with llama.cpp's
`llama-server`, on the CPU (on the GPU with a CUDA build, as in the GPU image); so
are sheet titles when finding plans, notes that state the units, level labels on
sections, rows of door and window schedules, and private texts in drawings. Its
answers are limited to StoreyPath's types by a JSON schema and are stored in the
workspace (`readings`), so a project converts the same way again, with or without
the model.

| Environment | |
|---|---|
| `STOREYPATH_MODEL` | the `.gguf` model (default: the one in `STOREYPATH_MODELS`; with several, the last by name) |
| `STOREYPATH_MODELS` | the model folder (default `/opt/storeypath/models`, as in the container) |
| `STOREYPATH_LLAMA_SERVER` | the `llama-server` program (default: from `PATH`) |
| `STOREYPATH_MODEL_URL` | use an already running `llama-server` instead |
| `STOREYPATH_THREADS` | CPU threads for the model (default: all) |
| `STOREYPATH_PARALLEL` | questions answered at once (default 1; more needs more memory) |
| `STOREYPATH_GPU_LAYERS` | layers on the GPU, with a CUDA build of `llama-server` (e.g. `99`: all; the GPU image sets it) |

Without a model everything works on the rules alone; `convert --no-model` skips it.
Outside the container, install `llama-server` and fetch the model as in
[Without Docker](../README.md#without-docker).
[eval/](eval) scores a model on room labels, sheet titles, layer names, unit notes and
level labels
(`uv run python eval/run.py --model path/to/model.gguf`).

## Looking at the plans (vision)

A drawing is made to be printed and read by people, and everything a builder needs is
on the print. Studio can look at it the same way: each room it found is drawn as
printed, outlined in red, and a vision model says whether that is really one room and
what kind it is. Code keeps the exact geometry and IDs; the model makes the calls a
person makes at a glance ([vision.py](src/storeypath/vision.py)):

- an unnamed area that is not a room (the garden inside a plot wall, a sheet frame, a
  gap) is set aside: deleted, and shown with *Show deleted* for a person to restore;
- a room with no type gets the one its furniture and fixtures show (a bed, a WC); a
  type the rules or the language model gave is kept;
- an outline holding several rooms is divided where they meet: the lines that may
  divide it (a line drawn across it, a wall carried on past where it stops) are shown
  one at a time in blue, its two sides lettered, and the model says what each side
  is. Where the sides differ, code cuts exactly along the line, then each piece is
  looked at on its own: a cut stays only where both pieces are rooms, of different
  kinds (a strip along the windows is part of the room it was cut from). There is no
  wall along a cut: the pieces are zones of the one space, named from the labels in
  them and typed as any space is;
- an outline that is only part of a room is noted for review;
- an area it saw as no room that doors join to several rooms (a corridor round a
  wing, a hall the rooms open onto) is kept as a way through, with a note;
- areas set aside that reach the edge of the plan (a yard, its pool) are taken out of
  the floor's outline and walls; rooms are never cut.

Each decision is marked `vision` for a person to check. The answers are kept in the
workspace by room shape, so converting again asks only about rooms that changed, and
the project converts the same way without the model. Where a vision model runs, it
also reads, in words, the rows of door and window schedules and the private texts in
drawings, in place of the language model.

The model is any OpenAI-compatible endpoint that takes images: `llama-server` (with
the model's `--mmproj`) or vLLM on a GPU, or a hosted service. Studio sends it views
of the plan and texts from the drawings, so a hosted service sees them.

| Environment | |
|---|---|
| `STOREYPATH_VISION_URL` | the endpoint, e.g. `http://127.0.0.1:8105/v1` (none: no vision) |
| `STOREYPATH_VISION_MODEL` | the model name, when the server serves several |
| `STOREYPATH_VISION_KEY` | a bearer token, for hosted services |
| `STOREYPATH_VISION_PARALLEL` | questions in flight at once (default 2) |

The GPU image ([docker/Dockerfile.gpu](../docker/Dockerfile.gpu)) holds Gemma 4 31B
(4-bit) and starts it with `llama-server` on the GPU at `127.0.0.1:8105`
([docker/start-gpu.sh](../docker/start-gpu.sh)), with `STOREYPATH_VISION_PARALLEL`
slots of `STOREYPATH_VISION_CONTEXT` tokens each (2 and 8192 by default);
`STOREYPATH_VISION=off` starts it without, and a `STOREYPATH_VISION_URL` given uses
that model instead.

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
`research`; the GPU image never holds it. Images built with it must not be published
or sold. On plans it was not trained on it mistakes things (wall-mounted air
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
  door's width with no window tag is taken as a sliding door.
- Blocks on door layers are doors only when they have a swing, a door's name, or a
  sliding door's shape: basins, baths and cars put on a door layer are not doors.
- A lift with no way in drawn is given a door on the wall it shares with a hall,
  lobby or corridor.
- Where a wall stops and another faces its end within `walls.max_doorway`, the gap
  is a doorway: closed, and recorded as an `opening` between the two rooms.
- Wider gaps in the outside walls (up to `walls.max_opening`) are spanned by the
  building's outline, so a room behind a missing door is kept, and listed for review.
- An open-plan area holding the labels of several rooms is divided where it is
  narrowest between them (cuts totalling at most `spaces.max_split`), and each part
  is listed for review. Across a gap in a wall the parts become separate spaces,
  joined by an `opening` there; across open floor, with no wall at all, the space
  stays whole and is divided into **zones**.

Set `spaces.method` in the profile to `outlines` or `walls` to use one way only.
Walls, dividing lines and spaces drawn in review are added to what the drawing
gives, at every conversion.

## Furniture and equipment

Items (desks by grade, central photocopiers, wireless access points, sofas, TVs,
beds) are placed on floors in the review editor, and kept in the workspace. Each has
an ID of its own, the project's code and its number (`K7Q2XM-I000142`), which stays
with it wherever it is carried; a deleted item's ID is never issued again.

Their types are the **catalogue** ([catalogue.py](src/storeypath/catalogue.py)):
`catalogue.json` in Studio's data folder, one for every project, written with the
default types the first time Studio needs it, and copied into every package. A type
has a `code` kept for good, English and Arabic names, a `category` (furniture,
equipment, appliance), a size, a `mount` (floor, wall, ceiling), a colour,
`workplaces` (a desk: 1) and, for desks, a `grade`, and its `fields`: each owned by
`storeypath` (entered in Studio) or by the `system` that manages the asset (entered
there, never in a package). Add or change types by editing the file (or `POST
/api/catalogue`); a type no longer used is marked `retired`, never removed (Studio
refuses a catalogue sent to it that drops one, and puts back a default type missing
from the file). `export` on the command line uses the catalogue of
the data folder the workspace is in, else the built-in types.

A space's or zone's capacity is the number set in review, else the workplaces of the
items standing in it; its grade is the highest grade among its desks.

## Other settings

| Environment | |
|---|---|
| `STOREYPATH_ALLOWED_HOSTS` | more names Studio may be reached by, separated by commas or spaces (`*`: any); as `serve --allowed-host` |
| `STOREYPATH_NODE` | Node.js for building the floors' 3D at export (default: `node` on the `PATH`; empty: never) |
| `STOREYPATH_SYMBOLS` | the SymPoint-V2 folder (default `/opt/storeypath/symbols`) |

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
