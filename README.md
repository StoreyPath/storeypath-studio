# StoreyPath

**Turn real-world DWG and DXF floor plans into indoor maps you can walk through —
read the way an architect reads them.** No layer standards to follow, no templates
to fill in, no cloud: drop in the drawing the architect gave you, and StoreyPath
finds the plans, the floors, the rooms, the doors and what every room is, gives
every object an ID that never changes, lets you check and complete it all (walls,
doors, furniture and equipment), and opens it as a 3D world.

One container, with or without a GPU. Works with no network at all.

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
```

Then open **http://localhost:8080**. This image needs no GPU and runs on amd64 and
arm64: Linux, macOS and Windows with Docker. On a machine with an NVIDIA GPU, a
second image adds a vision model that looks at the plans:
[Offline images, CPU and GPU](#offline-images-cpu-and-gpu).

![Walking through a converted floor: down the corridor, into an office](docs/images/walk.gif)

| Drop in a drawing: StoreyPath finds the plans on it | Check what it found, over the original drawing |
|---|---|
| ![Plans found on a sheet](docs/images/studio-plans.png) | ![The review editor](docs/images/review-editor.png) |
| **Cut away a floor, like a plan in 3D** | **X-ray: see-through walls, rooms by type** |
| ![A floor cut away, rooms labelled](docs/images/world-cutaway.png) | ![X-ray view](docs/images/world-xray.png) |
| **Lift the floors apart** | **Walk in, like a game** |
| ![Floors lifted apart](docs/images/world-explode.png) | ![An office, seen walking in](docs/images/walk-office.png) |

## Contents

- [It reads drawings like a person does](#it-reads-drawings-like-a-person-does) ·
  [How Studio reads a drawing, step by step](docs/HOW-STUDIO-READS-A-DRAWING.md)
- [What you can do in Studio](#what-you-can-do-in-studio): projects, drawings, the
  site plan, review, walls and doors, furniture and equipment, capacity, export
- [Walk through it](#walk-through-it)
- [With a GPU or without](#with-a-gpu-or-without)
- [Run it](#run-it) · [Offline images, CPU and GPU](#offline-images-cpu-and-gpu) ·
  [Without Docker](#without-docker) · [Requirements](#requirements)
- [The language model](#the-language-model) · [The vision model](#the-vision-model)
- [The command line](#the-command-line)
- [For other systems](#for-other-systems) · [How it fits together](#how-it-fits-together) ·
  [Stable IDs](#stable-ids)
- [Status](#status) · [Licence](#licence)

## It reads drawings like a person does

Real drawings are messy. Layers are named whatever the architect liked (`jun wall`,
`ELE4`, `0`), every floor sits side by side on one sheet next to elevations and
title blocks, doors are loose lines, the unit setting is wrong, and room names are
abbreviated, misspelt or in Arabic. StoreyPath doesn't ask you to clean any of that
up. It looks at what is drawn:

- **Finds the plans on a sheet set.** Every drawing on the sheets is found and its
  title read: *ground floor plan* is floor 0, *first floor plan* floor 1, *basement*
  floor -1, the *roof deck* above them, *guard room plan* is another building.
  Elevations, sections, title blocks and sheet frames are recognised and left out.
- **Works out the real units from what is drawn.** In the right units a door swing
  is about a door wide, the dimensions are the size of rooms and the text is a size
  someone can read, whatever the drawing's setting claims. Door swings are told from
  a basin's rounded corners by their leaf, and a note such as *ALL DIMENSIONS IN MM*
  is read in any language. When these disagree, Studio says so and you choose.
- **Knows what each layer holds without its name.** Walls are long pairs of
  parallel lines a wall's thickness apart that join into one frame (straight,
  diagonal or curved); glazing is thin pairs in line with the walls; doors are
  quarter-circle swings; columns are small repeated squares; room outlines each
  hold one room's name. Dashed lines (beams, things above) and evenly spaced lines
  (stair treads, tiles, tables) are not walls. Each plan is read on its own, so a
  layer that holds door leaves on one sheet and roof parapets on another is read
  right on both.
- **Stacks floors drawn side by side.** Floors share columns and outside walls, so
  each plan is moved to where its walls overlap the floor below — on a real sheet
  set, to within millimetres of where the structural grid puts it.
- **Reads the floor heights from the sections.** A plan has no heights, but the
  sections beside it do: *+3.65 FIRST FLOOR SLAB LVL*, *+6.95 ROOF SLAB LVL*,
  *+8.65 PARAPET LVL* give each floor's height and the parapet around the roof, in
  any language. The walls around a roof terrace or balcony are built as parapets,
  not as walls up to the ceiling.
- **Finds rooms even when nothing outlines them.** Door swings close the doors they
  stand in (both leaves of a double door); glazing seals windows, curved bays too;
  a wall that stops with another facing it is a doorway; wider gaps in the facade
  are spanned so no room leaks outside. An open-plan area holding several rooms'
  labels is divided where it is narrowest between them: into separate rooms across
  a gap in a wall, into zones of one room across open floor.
- **Understands room names in any language.** A small language model running
  inside the container reads the names the rules don't know: `F.DINNING` is a dining
  room, `EE.RM.` an electrical room, `مجلس رجال` a living room, `DORMITORIO` a
  bedroom, `VOID` is open to the floor below. It can only answer from StoreyPath's
  fixed list of types, its answers are kept with the project, and you confirm them.
  Room codes written in rooms (`RM-GF-33`) are read as their numbers, and the text
  as written is kept beside the ID for other systems to match on.
- **Looks at the plan, with a GPU.** A vision model is shown every room on the plan
  as printed, outlined in red, and says whether it is really a room and what kind:
  the garden inside a plot wall is set aside, a room with a bed and no name is a
  bedroom, a hall holding a sitting area and a dining area is divided into two
  zones where they meet. Each call it makes is marked for you to check, and a room
  it set aside can be restored.
- **Can tell a room by what is drawn in it (research use only).** Optionally, a
  network trained on floor plans (SymPoint-V2) spots the toilets, baths, stoves and
  stairs drawn in rooms that have no name, and types them for you to check. It is
  not StoreyPath's and is for research only: see
  [Symbol spotting](#symbol-spotting-research-use-only).
- **Shows you exactly what to check.** The review editor draws every floor over
  the original drawing and lists the few spaces that need a person: no type, the
  labels of two rooms in one, a dividing line it drew, a gap to the outside, what a
  model decided. Click, correct, done — and corrections survive every re-import.

The whole procedure, what rules decide and what the models decide at each step:
**[How Studio reads a drawing](docs/HOW-STUDIO-READS-A-DRAWING.md)**.

### On a real drawing

A villa's sheet set, as handed over by its architect: one AutoCAD 2004 DWG, 91,000
entities on 44 layers with the architect's own names, the unit set to millimetres
but drawn in metres, no door blocks, and every plan side by side with elevations,
sections, site plans and title blocks. In the container, with networking switched
off and no setup of any kind:

| | |
|---|---|
| Drawings found and titled | 16, in 21 s |
| Floors stacked | 3, each within 1 cm of the structural grid |
| Rooms found | 54, of which 30 typed by the language model |
| Doors, openings and windows | 85, each with its width and position |
| Total, upload to valid package | 37 s on a laptop (Apple M5 Max); 90 s on 4 cores, 129 s on 2 |

## What you can do in Studio

Studio is a web app: everything below is in the browser, and runs on the machine it
is served from.

### Projects, locations and buildings

A **project** holds one site or campus: its drawings and every ID ever issued for
it. Create one by name on the Projects page; Studio gives it a code that never
changes (`K7Q2XM`). Inside it are **locations** (sites, campuses) and their
**buildings**, made as you add floors: each plan you add goes to a location and
building the project has, or to a new one you name. *Delete project* removes a
project and everything in it, once you type its name.

### Adding drawings

- **Drop DWG or DXF files** on the project. With *Remove private information*
  ticked (the default), Studio first finds the title blocks (client, owner,
  consultant, who drew it, stamps), names, phone numbers, emails, permit and plot
  numbers, images and the file's hidden data, and shows them: untick anything to
  keep, then only the cleaned copy is kept, as `drawing-1.dxf`; the file you sent,
  and its name, are not. **Words**, beside each drawing, lists every word left in
  it.
- **Find plans** lists every drawing on the sheets with its title, size and a
  thumbnail. Tick the ones that are floors and say, for each, its location,
  building, floor number (0 ground, -1 a basement), name, height and parapet:
  Studio fills them in from the titles and the sections. Several plans on one sheet,
  one file per floor, an outbuilding on the same sheet: all work.
- **Units.** If Studio is not sure of the units it says so; choose them from the
  list and the plans are found again.
- **Add the chosen plans as floors**: Studio lines each building's floors up, reads
  them, and puts a new building that would stand on top of another beside it on
  the site plan. A plan on a floor that a building has already can **replace its
  drawing**: its rooms keep their IDs.
- The project page shows, per floor, the drawing and plan it came from and *How it
  was read* (what each layer was taken to hold). *Read* reads a floor again whose
  reading failed.

### The site plan

Each location has a **site plan**: its buildings drawn from above, where they stand
relative to each other. Drag a building to move it, or its round handle to turn it
(<kbd>Shift</kbd>: by 15°); with a building chosen, the arrows move it 1 m
(<kbd>Shift</kbd>: 10 m) and <kbd>[</kbd> <kbd>]</kbd> turn it 1° (<kbd>Shift</kbd>:
15°), or type its x, y and turn. *Side by side* lines them up 10 m apart.

**Place on the map** puts the whole site on the earth: the latitude and longitude of
its centre and the compass bearing of up. Every building goes with it. A building
can also be placed on the map by itself, which then wins over the site plan. Until
then buildings are exported around 0°N 0°E with their true shapes and sizes, marked
not placed. Moving or turning a building, on the site plan or the map, changes
nothing in it: its rooms, doors and items keep their IDs and their places in the
building.

### Review: correct, complete, check

*Review* on a floor opens the review editor: the floor drawn over its drawing.

- **The drawing**: *as printed* (the drawing rendered as on paper, a pixel a
  centimetre), as its *lines*, or *off*. *Side by side* puts the print on the left
  and Studio's spaces on the right, moving together. *Open print* shows the print
  full size in a new tab.
- **To review** lists the spaces and zones that need a look, and why; *Next*
  (<kbd>N</kbd>) goes through them. *All spaces* lists every one, with a filter.
- **Click a space** to see its ID (*Copy*), the text written in it on the drawing,
  why it is listed, and to correct its **type, name and number** (what Studio read is
  shown beside each). *Save* keeps your correction, or accepts it as it is; *Use
  detected* takes your correction away. **Capacity**: how many people it is meant to
  seat (see below). **Delete** takes what is not a room (a sliver, the outside) out of
  the plan, the lists and the 3D view; it keeps its ID (and is exported marked
  `ignored`), and *Show deleted* shows it again, to restore.
- **Draw what the drawing leaves out.** Right-click the plan:

  | | |
  |---|---|
  | *Add a door / a window / an opening here* | on a wall: a door 0.9 m, a window 1.2 m, an opening (a way through, no door) 1.0 m wide |
  | *Draw a wall from here* (<kbd>W</kbd>) | click its two ends; it snaps to walls and parts spaces as a drawn wall does |
  | *Divide a space from here* (<kbd>V</kbd>) | a line right across a space with no wall: it becomes two zones |
  | *Draw a space from here* (<kbd>S</kbd>) | click its corners (they snap to walls); the first again, a double-click or <kbd>Enter</kbd> closes it, <kbd>Backspace</kbd> takes a corner back. For an area the drawing encloses nowhere, such as a colonnade |
  | *Change size…* (on a door, window or opening) | its width, sill and height; *Size as drawn* goes back |
  | *Delete* / *Take it away* | a door, window or opening of the drawing is deleted (and can be restored); what you drew is taken away |
  | *Place an item here…* | furniture and equipment (below) |

  <kbd>D</kbd> and <kbd>O</kbd> add a door or an opening with a click on a wall,
  <kbd>Del</kbd> deletes what is chosen, <kbd>Esc</kbd> stops, <kbd>F</kbd> fits the
  floor in view. After each change the floor is read again, and what you drew is
  kept with the floor through every re-read and every revised drawing.
- **3D.** The *2D / 3D* switch shows the floor as built, in the editor: click a room
  to correct it as on the plan; *Update 3D* shows changes made since. *Walk in 3D*
  opens the floor in the 3D world in a new tab.
- **Re-read drawing** reads the floor's drawing again (a revised one too): IDs and
  corrections are kept. A reading that would retire most of the floor's rooms is
  held back, the floor unchanged, and Studio asks before applying it (*Read
  anyway*).

Every change is saved to the project at once.

### Furniture and equipment

Desks, photocopiers, access points, sofas, TVs, beds, kiosks: **items** are placed on
the floors in review.

- **The catalogue** of item types is the organization's: one `catalogue.json` in
  Studio's data folder, the same for every project, and copied into every package.
  A new Studio starts with desks by grade (president, C-level, director, manager,
  head of section, senior and junior staff), a central photocopier, a wireless
  access point, a sofa, a TV screen, king- and queen-size beds and a wayfinding
  kiosk, each with a code, English and Arabic names, a size, how it is mounted
  (floor, wall, ceiling) and a colour. Types are added or changed by editing that
  file; a type no longer used is marked `retired`, never removed, so its code stays
  with the items that have it and is never given to another type.
- **Place** one by choosing its type in *Place* on the toolbar (or right-click,
  *Place an item here…*) and clicking where it goes. Drag it to move it; <kbd>R</kbd>
  turns it 90°, <kbd>[</kbd> and <kbd>]</kbd> by 15°, the arrows move it (Shift:
  further), <kbd>Del</kbd> deletes it. Its panel changes its type, its turn, its floor
  (carry it to another floor or building) and its details.
- **A kiosk** (type `KIOSK`) is where a wayfinding kiosk stands, its screen at its
  front: wayfinder links each of its kiosks to one, so the kiosk's map shows "you are
  here", and a way to an office can later start from it.
- **An item's ID never changes when it moves**: it is the project's code and the
  item's own number (`K7Q2XM-I000142`), not its place. Carried to another office,
  floor or building, it keeps it; deleted, its ID is never issued again.
- **Who enters what.** Each type's details are fields owned by StoreyPath (what is
  physical: a colour, a size, a model) or by the system that manages the asset (an
  access point's network name or VLAN). Studio shows only its own; the others are
  named and entered in that system, and are never in a package.

This is asset management (where things are, and where they have been), not
inventory: nothing says who holds what.

### Capacity and grade

Each space and zone has a **capacity**, how many people it is meant to seat: the
number set in its panel in review (0: a room meant to seat nobody), else the
workplaces of the desks standing in it (a desk seats one; a space divided into zones
counts its zones'). Its **grade** is who it is laid out for: the highest grade among
its desks. Both go into the package as defaults a system placing people may keep or
replace.

### Export: one building per package

*Export package* writes the package (`.storeypath`, [format 0.7](spec/FORMAT.md)) of
one building, chosen from the list: its floors, spaces, zones, doors, windows and
openings, its items and their catalogue, with every ID. It is checked against the
format before it is kept, downloaded, and listed on the project page (newest first),
with links to view it as a *2D plan*, in *3D*, or as *Rooms by type*.

- Each package lists what was added, changed and retired in its building since that
  building was last exported (`changes.json`), so a system updates its mappings.
- Items carry where they stand in their building (`local`): moving a building on the
  map changes nothing in it, and the change list says only that the building moved.
- Each floor is also built in 3D ahead of time, so a slow machine shows it without
  building it (this takes Node.js, which both images hold).

### Sending a project to another Studio

*Download project* gives one file, `<code>.storeypath-project`: the project with
every correction, edit and ID, its export history, its drawings and the Studio's
item types. Another Studio opens it on its Projects page (*Open a project or a
building's package*) and continues the project where it was. It is not a package:
other systems read a building's package.

A building's **package** opens the same way: its building is rebuilt with the same
IDs, into its project when that is here (added, or put in place of that building
once you type the project's name). Its floors have no drawing until one is added;
then they are read again, lined up on the walls they had, and keep their IDs.

### The viewers

- **Walk in 3D** (a project, a building, a floor): the 3D world of the project as it
  is now, no export needed.
- **2D plan**: each floor as a plain SVG plan, rooms coloured by type, doors with
  their swings, items.
- **Rooms by type**: each room as a block coloured by its type, floors stacked, with
  search: a check of the rooms rather than of the walls.

## Walk through it

Every package opens as a 3D world, straight from Studio, with nothing downloaded:
the walls at the thickness they were drawn, parapets around roofs and terraces,
doorways you walk through, windows with
sills and glass, floors finished by what each room is — wood in offices and
bedrooms, tile in bathrooms and kitchens, stone in lobbies and stairs — under a
sun that casts real shadows.

| Dollhouse: orbit it, one floor or all | Walk: first person, like a game |
|---|---|
| ![The whole building](docs/images/world-hero.png) | ![Walking down a corridor](docs/images/walk-corridor.png) |
| **Click a room: its ID, area and where its doors lead** | **Studio: Walk in 3D, on any project, any time** |
| ![A room selected](docs/images/world-select.png) | ![A project in Studio](docs/images/studio-project.png) |

- **Dollhouse.** Orbit the building, pick a floor, cut the walls down to see the
  plan in 3D, lift the floors apart, or switch on x-ray: see-through walls and every
  room a translucent volume tinted by its type. Click a room for its ID, area and
  the rooms its doors lead to. Furniture and equipment show when one floor does.
- **Walk.** Start at the front door and walk in: mouse to look, <kbd>W A S D</kbd> to
  move, <kbd>Shift</kbd> to run. Walls and windows stop you; doorways don't. The
  name of the room you're in shows as you enter it, a minimap follows you, and at
  stairs or a lift <kbd>E</kbd> and <kbd>Q</kbd> take you up and down.

**Walk in 3D** in Studio always shows the project as it is now — no export needed.
It's [three.js](https://threejs.org) (WebGL), drawn from the package alone, so it
works the same embedded in your own app ([viewer/](viewer)).

## With a GPU or without

StoreyPath works both ways. With a GPU it does more of the checking a person would
otherwise do:

| | Without a GPU | With a GPU |
|---|---|---|
| Image | `ghcr.io/storeypath/studio` ([docker/Dockerfile](docker/Dockerfile)) | built from [docker/Dockerfile.gpu](docker/Dockerfile.gpu) |
| Reads the drawing | rules | rules |
| Reads the texts (room names, titles, notes, levels) | the language model, Qwen3.5-4B, on the CPU | the same model, on the GPU |
| Looks at the plan (is it a room? merged rooms; a type from what is drawn) | no: those are left to a person in review | the vision model, Gemma 4 31B, on the GPU |
| Needs | 2 CPU cores, 4 GB of memory | an NVIDIA GPU with 32 GB free |

Without a GPU, the CPU image can still use a vision model **served elsewhere** (a GPU
machine on your network, or a hosted service): set `STOREYPATH_VISION_URL` (and
`STOREYPATH_VISION_MODEL`, `STOREYPATH_VISION_KEY` as it needs). Views of the plan
around each room, the drawings' texts when they are checked for private
information, and the rows of their door and window schedules are then sent to it.

Every answer a model gives is kept with the project, so a project read with a GPU
reads the same again on a machine without one. [How Studio reads a
drawing](docs/HOW-STUDIO-READS-A-DRAWING.md#with-a-gpu-and-without) says which step
uses which.

## Run it

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
```

Open http://localhost:8080, create a project and drop in a drawing. Projects live
in the `storeypath` volume, so they survive restarts and upgrades:

| | |
|---|---|
| Stop, start again | `docker stop storeypath` · `docker start storeypath` |
| Upgrade | `docker pull ghcr.io/storeypath/studio && docker rm -f storeypath`, then the `run` line again |
| Let others on your network use it | publish the port on all interfaces: `-p 8080:8080` (there are no user accounts yet: trusted networks only). They reach it by the machine's address; to use a host name instead, allow it: `-e STOREYPATH_ALLOWED_HOSTS=studio.example` (Studio refuses requests addressed to names it does not know, so a web page cannot reach it through DNS rebinding) |
| Use a vision model served elsewhere | `-e STOREYPATH_VISION_URL=http://gpu-server:8105/v1` (any OpenAI-compatible endpoint that takes images) |
| Logs | `docker logs -f storeypath` |

The same image is the command-line tool: `docker run --rm -v "$PWD:/data"
ghcr.io/storeypath/studio views house.dwg`. [The command line](#the-command-line)
lists the commands.

## Offline images, CPU and GPU

Both images hold everything they need. Nothing is downloaded when they run: no
models, no telemetry, no map tiles, and the 3D view's libraries are served by Studio
itself. The models are fetched once, **before** the build, by scripts that check
their checksums, and baked in.

| | CPU image | GPU image |
|---|---|---|
| Built from | [docker/Dockerfile](docker/Dockerfile) | [docker/Dockerfile.gpu](docker/Dockerfile.gpu) |
| Published | `ghcr.io/storeypath/studio`, amd64 and arm64, by [the release workflow](.github/workflows/release.yml) for each version tag | not published: build it yourself (amd64) |
| Models fetched first | `docker/fetch-models.sh`: Qwen3.5-4B (2.7 GB) | `docker/fetch-models.sh` and `docker/fetch-vision.sh`: Gemma 4 31B, 4-bit (about 18 GB) and its image encoder (about 1 GB) |
| Inside | Studio; LibreDWG's `dwg2dxf` for DWG; llama.cpp's `llama-server` for the CPU (it picks the fastest CPU code at start); the language model; Node.js for pre-built 3D; the viewers | the same, with `llama-server` built for CUDA (A100/A30, A10/A40/RTX 30, L4/L40/RTX 40, H100/H200, Blackwell) and NVIDIA's CUDA runtime, and the vision model |
| Starts | Studio; the language model loads in the background | the vision model on the GPU (a minute or two), then Studio |

### Build the CPU image

```sh
docker/fetch-models.sh                                   # once: the language model (2.7 GB, checksum-verified)
docker build -f docker/Dockerfile -t storeypath/studio .
```

`docker/fetch-models.sh 2B` and `--build-arg MODEL=Qwen3.5-2B-Q4_K_M.gguf` build a
smaller image with a smaller model ([The language model](#the-language-model)).

### Build the GPU image

On any machine with internet (an Apple Silicon Mac builds it too, through Docker
Desktop's x86 emulation, in an hour or two):

```sh
docker/fetch-models.sh && docker/fetch-vision.sh          # once: 2.7 GB + 19 GB, checksum-verified
docker build --platform linux/amd64 -f docker/Dockerfile.gpu -t storeypath/studio:gpu .
```

llama.cpp is compiled for every GPU generation from A100 on by default;
`--build-arg CUDA_ARCHITECTURES="80-real;90-real"` builds for A100 and H100 alone,
which is quicker, and `--build-arg JOBS=4` limits how many compile at once.

### Carry it to an air-gapped machine

On the machine with internet, save the image to a file (the published CPU image, or
one you built):

```sh
docker pull ghcr.io/storeypath/studio
docker save ghcr.io/storeypath/studio | gzip > storeypath-studio.tar.gz
docker save storeypath/studio:gpu | gzip -1 > storeypath-studio-gpu.tar.gz   # the GPU image
```

Carry the file over, then on the isolated machine:

```sh
docker load -i storeypath-studio.tar.gz
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
```

(Pull with `--platform linux/amd64` or `linux/arm64` to carry an image for a machine
of the other kind.) The command-line tool runs with `--network none`; the web app
needs nothing but its published port.

### Run the GPU image

On the GPU machine (Linux, Docker, the NVIDIA Container Toolkit, NVIDIA driver 525 or
newer):

```sh
docker load -i storeypath-studio-gpu.tar.gz
docker run -d --name storeypath --gpus all -p 8080:8080 -v storeypath:/data storeypath/studio:gpu
docker logs -f storeypath        # "vision model ready", then Studio's address
```

| | |
|---|---|
| GPU memory | one NVIDIA card with 32 GB free: the vision model, two rooms looked at at once, and the language model, all on the GPU |
| Choose a card | `--gpus '"device=1"'` |
| Rooms looked at at once | `-e STOREYPATH_VISION_PARALLEL=2` (the default); each more needs more GPU memory. `STOREYPATH_VISION_CONTEXT` (default 8192) is the context each one gets |
| Without vision | `-e STOREYPATH_VISION=off`: rules and the language model only |
| A vision model served elsewhere | `-e STOREYPATH_VISION_URL=…`: the image's own is then not started |
| Reach it by a host name | `-e STOREYPATH_ALLOWED_HOSTS=<that name>` (addresses and localhost always work) |
| The model server's log | `docker exec storeypath cat /tmp/vision.log` |

Loading the vision model takes a minute or two after each start; if it does not
start (no GPU given to the container), Studio says so and runs without it. The GPU
image always starts Studio; for its command-line tool, give
`--entrypoint storeypath`.

### Symbol spotting: research use only

SymPoint-V2 types unnamed rooms by the fixtures drawn in them (a toilet and a bath:
a bathroom). It is **not StoreyPath's**: its repository states no licence and its
weights were trained on non-commercial data (FloorPlanCAD, CC BY-NC), so it is **for
research only**, and images built with it **must not be published or sold**.

It is never in an image unless you fetch it before building the CPU image:

```sh
docker/fetch-symbols.sh                                 # its code (pinned) and weights (checksum-verified)
docker build -f docker/Dockerfile -t storeypath/studio . # baked in, with PyTorch for the CPU
```

The release workflow leaves it out unless the repository variable
`STOREYPATH_SYMBOLS` is set to `research`; the GPU image never has it. See
[studio/README.md](studio/README.md#symbols-drawn-in-a-plan-optional-research-use-only).

## Without Docker

Studio is a Python program and runs from a clone of this repository with
[uv](https://docs.astral.sh/uv/), which fetches Python 3.12 itself if it needs to:

```sh
brew install uv          # macOS; on Linux: curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/StoreyPath/storeypath
cd storeypath/studio
uv sync
uv run storeypath serve --data ~/storeypath --open
```

That is the whole web app, with projects kept in `~/storeypath`. On its own it reads
DXF, and room names by the rules alone: the container also holds
[LibreDWG](https://www.gnu.org/software/libredwg/)'s `dwg2dxf` for DWG drawings and
[llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server` for the language
model, and without Docker you install them yourself.

**macOS**, with [Homebrew](https://brew.sh):

```sh
brew install libredwg llama.cpp
```

**Linux.** Neither is packaged by Debian or Ubuntu, so build them as the container
does (in any folder outside the repository):

```sh
sudo apt-get install build-essential cmake git curl ca-certificates xz-utils pkg-config python3

curl -fsSLO https://ftp.gnu.org/gnu/libredwg/libredwg-0.14.tar.xz
tar xf libredwg-0.14.tar.xz
(cd libredwg-0.14 && ./configure --disable-bindings && make -j"$(nproc)" && sudo make install && sudo ldconfig)

git clone --depth 1 --branch v0.5.0 https://github.com/ggml-org/llama.cpp
cmake -S llama.cpp -B llama.cpp/build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF
cmake --build llama.cpp/build --target llama-server -j"$(nproc)"
export STOREYPATH_LLAMA_SERVER="$PWD/llama.cpp/build/bin/llama-server"
```

**Windows:** use Docker, or the Linux steps in WSL.

Then, back in `storeypath/studio`, fetch the model once and start Studio with it:

```sh
../docker/fetch-models.sh                            # 2.7 GB, checksum-verified
export STOREYPATH_MODELS="$PWD/../docker/models"
uv run storeypath serve --data ~/storeypath --open
```

Studio says what it found as it starts: `language model: Qwen3.5-4B-Q4_K_M; DWG:
yes` (and the vision model, when one is set). Add the `export` lines to your shell
profile to keep them. To update, `git pull`, then `uv sync`. `uv run storeypath demo
demo/` builds a sample project to try; [studio/](studio) has every command and
setting. Also, as you need them:

- **Vision**: `uv sync --extra vision`, and `STOREYPATH_VISION_URL` set to a vision
  model's endpoint. To serve Gemma 4 yourself on an NVIDIA GPU, fetch it with
  `docker/fetch-vision.sh` and run a CUDA build of `llama-server` with it and its
  `--mmproj`, as [docker/start-gpu.sh](docker/start-gpu.sh) does.
- **The 2D plan page**: `npm ci && npm run build` in `viewer/svg` (Studio offers
  *2D plan* once it is built).
- **Pre-built 3D in packages**: Node.js 20.6 or newer on the `PATH`.

## Requirements

Measured with the villa above, start to finish, inside containers limited to each
size:

| | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores, 64-bit: x86-64 (Intel/AMD) or ARM64 (Apple Silicon, Graviton, Ampere) | 4 or more cores |
| Memory | 4 GB for the container | 8 GB on the machine |
| Disk | 7 GB: the image is about 3 GB to download, 6 GB unpacked | plus your drawings |
| GPU | none for the CPU image: the language model runs on the CPU | for the GPU image: see below |
| Software | Linux with Docker 20.10 or newer; macOS or Windows with Docker Desktop (give it at least 4 GB of memory in its settings) | |
| Browser | a current Chrome, Edge, Firefox or Safari (WebGL 2) — built-in laptop graphics are plenty for the 3D view | |
| Network | to pull the image, once | none to run |

| The whole villa: 16 drawings found, 3 floors converted, exported | Time | Peak memory |
|---|---|---|
| 2 cores, 4 GB limit | 128 s | 1.4 GB |
| 2 cores | 129 s | 1.4 GB |
| 4 cores | 90 s | 1.4 GB |
| 18 cores (Apple M5 Max) | 37 s | |

The language model's weights are mapped from the image rather than loaded, which is
why the container needs so little memory of its own; more memory just keeps them
cached.

**The GPU image** needs an amd64 Linux machine with an NVIDIA GPU of a generation it
is built for (A100/A30, A10/A40/RTX 30, L4/L40/RTX 40, H100/H200, Blackwell) with 32
GB free on one card, NVIDIA driver 525 or newer, Docker and the NVIDIA Container
Toolkit, and disk for an image of over 20 GB (the vision model alone is 19 GB).

## The language model

Small enough for any CPU, measured on [studio/eval](studio/eval) — 137 room labels
(English, Arabic, French, German, Spanish; abbreviated and misspelt), 25 sheet
titles and 39 layer names — running CPU-only in the container:

| Model (Q4_K_M) | Size | Room types | Room name or not | Sheet titles | Layer names |
|---|---|---|---|---|---|
| **Qwen3.5-4B** (default) | 2.7 GB | **97%** | 95% | 88% | 97% |
| Qwen3.5-2B | 1.3 GB | 84% | 84% | 92% | 92% |
| Qwen3.5-0.8B | 0.5 GB | 85% | 83% | 44% | 49% |
| rules alone | – | 44% | – | – | – |

It runs on [llama.cpp](https://github.com/ggml-org/llama.cpp), answers are held to a
JSON schema of StoreyPath's types, and every answer is stored in the project, so
converting again gives the same result with or without the model. Build with
`--build-arg MODEL=Qwen3.5-2B-Q4_K_M.gguf` for a smaller image. It also reads sheet
titles, notes that state the units, level labels on sections, rows of door and
window schedules, and the private texts in a drawing.

## The vision model

The GPU image runs Gemma 4 31B (4-bit) with llama.cpp on the GPU; any
OpenAI-compatible endpoint that takes images works instead (`llama-server` with
the model's `--mmproj`, vLLM, or a hosted service). Measured on 119 rooms of two
houses and an interior designer's furniture plan, each checked by hand, Gemma 4 31B
judged 84% of outlines and 85% of types right. It is asked only about rooms Studio
found, its answers are kept by each room's shape (reading again asks only about
rooms that changed), and each call it makes is marked for review. What it does,
step by step: [Vision](docs/HOW-STUDIO-READS-A-DRAWING.md#g-vision-looking-at-each-room-visionpy).

## The command line

Everything the web app does to a drawing can be done from the command line too, on
a workspace file (`*.spproj`): `new`, `add-location`, `add-building`, `add-floor`
(`--view`, `--units`), `views`, `align`, `levels`, `convert` (`--force`,
`--no-model`, `--no-vision`), `list --review`, `fix`, `place`, `export --building`,
`validate`, `review` (the review editor), `serve` (the web app, `--allowed-host`),
`private` and `words` (privacy), `demo`. Each, one line apiece:
[studio/README.md](studio/README.md#commands).

```sh
storeypath views house.dwg                                   # the plans on the sheets, and their floors
storeypath convert house.spproj                              # read the drawings; keeps existing IDs
storeypath export house.spproj --building VILLA -o villa.storeypath
```

## For other systems

- **The package format**: [spec/FORMAT.md](spec/FORMAT.md) (format 0.7) and JSON
  Schemas in [spec/schema](spec/schema): one building per package, plain JSON,
  GeoJSON and CSV in a ZIP, readable without our code. Each export says what was
  added, changed and retired; `objects.csv` lists every ID with its parents.
- **Go**: [go/](go) reads and validates packages, standard library only.
- **Viewers** to embed: [viewer/](viewer) (the 3D world, a map view) and
  [viewer/svg](viewer/svg) (a 2D plan with no WebGL, for any machine).
- **Conformance**: [spec/conformance](spec/conformance) holds packages that every
  reader must read the same way, with items, a building moved on the map and a desk
  carried to another building.
- **Items are for asset management**: where things are, and where they have been,
  never who holds them. An inventory system keys its records to the items' IDs, as
  every system keys its own to StoreyPath's.

## How it fits together

```
DWG / DXF ──▶ StoreyPath Studio ──▶ package (*.storeypath) ──▶ StoreyPath Viewer (in your web app)
               find · convert · review · export               └─▶ any other system: import objects.csv,
                                                                    map our IDs to its own
```

| Folder | What it is |
|---|---|
| [studio/](studio) | **StoreyPath Studio** (Python): reads drawings, keeps IDs stable across revisions, the web app and review editor, exports packages |
| [viewer/](viewer) | **StoreyPath Viewer** (JavaScript): to embed in web apps — the walk-through 3D world, a map view with search, and a 2D SVG plan |
| [spec/](spec) | **The package format**: [FORMAT.md](spec/FORMAT.md), JSON Schemas and conformance packages — everything a system needs to read a package without our code |
| [go/](go) | A Go module that reads and validates packages |
| [docker/](docker) | The two images (CPU and GPU) and the scripts that fetch their models |
| [docs/](docs) | [How Studio reads a drawing](docs/HOW-STUDIO-READS-A-DRAWING.md), and the pictures in this page |

## Stable IDs

Every object gets a hierarchical ID that says exactly where it is, and that stays
the same when revised drawings are imported again:

```
PROJECT-LOCATION-BUILDING-FLOOR-OBJECT        K7Q2XM-RUH-HQ-F02-0142
```

Systems store our ID as the key of their own mapping (to employees, desks,
bookings…). Each export carries a list of IDs added, changed and retired since
the previous one, and a retired ID is never issued again. Lifts and stairs keep one
object code on every floor they serve. Items are the exception that proves the
rule: their ID (`K7Q2XM-I000142`) is the project's and their own number, so it does
not change when they move.

## Status

| # | Milestone | State |
|---|---|---|
| 1 | Format v0, IDs, workspace, schemas, validator | done |
| 2 | One floor from a DXF with room outlines → package → viewer | done |
| 3 | Spaces from walls when there are no room outlines; review editor | done |
| 4 | Several floors and buildings aligned; placement | done: floors on one sheet found and stacked; placement by anchor + bearing; site plans |
| 5 | Navigation graph and routing | next |
| 6 | DWG and real-world samples | done for a first real sheet set; a wider sample collection next |
| 7 | Hardening: change reports, docs, releases | done: change lists; releases with a multi-arch image on ghcr.io |
| 8 | Reading drawings like a person: layers, plans, units, room names | done |
| 9 | Looking at the plans with a vision model, on a GPU | done: the GPU image |
| 10 | Completing a floor in review: walls, dividers, doors, windows, openings, spaces | done |
| 11 | Furniture and equipment with IDs of their own; capacity and grade | done (format 0.7) |

## Licence

StoreyPath is [Apache License 2.0](LICENSE). The images also hold, each under its own
licence (all in `/usr/share/storeypath/licenses`): LibreDWG's `dwg2dxf`
(GPL-3.0-or-later, run as a separate program; its exact source is in the image),
llama.cpp (MIT), the Qwen3.5 model weights (Apache-2.0), three.js (MIT), MapLibre GL
JS (BSD-3-Clause), JSZip (MIT) and Node.js (MIT). The GPU image adds the Gemma 4
model weights (Apache-2.0) and NVIDIA's CUDA runtime and cuBLAS (CUDA Toolkit EULA,
redistributable). SymPoint-V2 is never in an image unless you fetch it yourself
before building, for research use only.
