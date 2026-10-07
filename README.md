# StoreyPath

**Turn real-world DWG and DXF floor plans into indoor maps you can walk through —
read the way an architect reads them.** No layer standards to follow, no templates
to fill in, no cloud: drop in the drawing the architect gave you, and StoreyPath
finds the plans, the floors, the rooms, the doors and what every room is, gives
every object an ID that never changes, lets you check it all, and opens it as a 3D
world.

One container. CPU only. Works with no network at all.

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
```

Then open **http://localhost:8080**. Runs on amd64 and arm64: Linux, macOS and Windows
with Docker. See the [requirements](#requirements) and the [air-gapped install](#air-gapped-install).

![Walking through a converted floor: down the corridor, into an office](docs/images/walk.gif)

| Drop in a drawing: StoreyPath finds the plans on it | Check what it found, over the original drawing |
|---|---|
| ![Plans found on a sheet](docs/images/studio-plans.png) | ![The review editor](docs/images/review-editor.png) |
| **Cut away a floor, like a plan in 3D** | **X-ray: see-through walls, rooms by type** |
| ![A floor cut away, rooms labelled](docs/images/world-cutaway.png) | ![X-ray view](docs/images/world-xray.png) |
| **Lift the floors apart** | **Walk in, like a game** |
| ![Floors lifted apart](docs/images/world-explode.png) | ![An office, seen walking in](docs/images/walk-office.png) |

## It reads drawings like a person does

Real drawings are messy. Layers are named whatever the architect liked (`jun wall`,
`ELE4`, `0`), every floor sits side by side on one sheet next to elevations and
title blocks, doors are loose lines, the unit setting is wrong, and room names are
abbreviated, misspelt or in Arabic. StoreyPath doesn't ask you to clean any of that
up. It looks at what is drawn:

- **Finds the plans on a sheet set.** Every drawing on the sheets is found and its
  title read: *ground floor plan* is floor 0, *first floor plan* floor 1, the *roof
  deck* above them, *guard room plan* is another building. Elevations, sections,
  title blocks and sheet frames are recognised and left out.
- **Works out the real units from what is drawn.** In the right units a door swing
  is about 0.85 m across, the dimensions are the size of rooms and the text is a size
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
  labels is divided where it is narrowest between them.
- **Understands room names in any language.** A small language model running
  inside the container reads the names the rules don't know: `F.DINNING` is a dining
  room, `EE.RM.` an electrical room, `مجلس رجال` a living room, `DORMITORIO` a
  bedroom, `VOID` is open to the floor below. It can only answer from StoreyPath's
  fixed list of types, its answers are kept with the project, and you confirm them.
- **Can tell a room by what is drawn in it (research use only).** Optionally, a
  network trained on floor plans (SymPoint-V2) spots the toilets, baths, stoves and
  stairs drawn in rooms that have no name, and types them for you to check. It is
  not StoreyPath's and is for research only: see
  [Symbols drawn in a plan](studio/README.md#symbols-drawn-in-a-plan-optional-research-use-only).
- **Shows you exactly what to check.** The review editor draws every floor over
  the original drawing and lists the few spaces that need a person: no type, the
  labels of two rooms in one, a dividing line it drew, a gap to the outside. Click,
  correct, done — and corrections survive every re-import.

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
  the rooms its doors lead to.
- **Walk.** Start at the front door and walk in: mouse to look, <kbd>W A S D</kbd> to
  move, <kbd>Shift</kbd> to run. Walls and windows stop you; doorways don't. The
  name of the room you're in shows as you enter it, a minimap follows you, and at
  stairs or a lift <kbd>E</kbd> and <kbd>Q</kbd> take you up and down.

**Walk in 3D** in Studio always shows the project as it is now — no export needed.
It's [three.js](https://threejs.org) (WebGL), drawn from the package alone, so it
works the same embedded in your own app ([viewer/](viewer)).

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
| Logs | `docker logs -f storeypath` |

The same image is the command-line tool: `docker run --rm -v "$PWD:/data"
ghcr.io/storeypath/studio views house.dwg`. [studio/](studio) lists every command.

### Without Docker

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
yes`. Add the `export` lines to your shell profile to keep them. To update,
`git pull`, then `uv sync`. `uv run storeypath demo demo/` builds a sample project to
try; [studio/](studio) has every command and setting.

### Requirements

Measured with the villa above, start to finish, inside containers limited to each
size:

| | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores, 64-bit: x86-64 (Intel/AMD) or ARM64 (Apple Silicon, Graviton, Ampere) | 4 or more cores |
| Memory | 4 GB for the container | 8 GB on the machine |
| Disk | 7 GB: the image is about 3 GB to download, 6 GB unpacked | plus your drawings |
| GPU | none — the language model runs on the CPU | |
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

### Air-gapped install

On a machine with internet access:

```sh
docker pull ghcr.io/storeypath/studio
docker save ghcr.io/storeypath/studio | gzip > storeypath-studio.tar.gz
```

Carry the file over, then on the isolated machine:

```sh
docker load < storeypath-studio.tar.gz
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
```

Nothing in the image reaches out: no telemetry, no downloads, no map tiles; the
3D view's libraries are served by Studio itself. (Pull with `--platform linux/amd64`
or `linux/arm64` to carry an image for a machine of the other kind.) The
command-line tool also runs with `--network none`.

### Build it yourself

```sh
docker/fetch-models.sh                                   # once: the language model (2.7 GB, checksum-verified)
docker build -f docker/Dockerfile -t storeypath/studio .
```

`docker/fetch-symbols.sh` before building also bakes in SymPoint-V2 for typing
unnamed rooms by their fixtures; it is for research only and such images must not
be published (see [studio/README.md](studio/README.md#symbols-drawn-in-a-plan-optional-research-use-only)).

Releases are built the same way by [the release workflow](.github/workflows/release.yml),
natively for amd64 and arm64.

### With a GPU: vision included

[docker/Dockerfile.gpu](docker/Dockerfile.gpu) builds one image that also reads the
plans with a vision model ([how](studio/README.md#looking-at-the-plans-vision)):
Studio, Gemma 4 31B (4-bit) served by llama.cpp built for CUDA, and the language
model, all inside. About 24 GB; nothing is downloaded when it runs.

| | |
|---|---|
| GPU | NVIDIA, 32 GB free on one card: A100, H100 (and, with `CUDA_ARCHITECTURES`, A10/A40, L4/L40, RTX 30/40, Blackwell) |
| Software | Linux, Docker, the NVIDIA Container Toolkit, NVIDIA driver 525 or newer |

Build it on any machine with internet (an Apple Silicon Mac builds it too, through
Docker Desktop's x86 emulation, in an hour or two), then carry the file over:

```sh
docker/fetch-models.sh && docker/fetch-vision.sh          # once: 2.7 GB + 19 GB, checksum-verified
docker build --platform linux/amd64 -f docker/Dockerfile.gpu -t storeypath/studio:gpu .
docker save storeypath/studio:gpu | gzip -1 > storeypath-studio-gpu.tar.gz
```

On the GPU machine:

```sh
docker load -i storeypath-studio-gpu.tar.gz
docker run -d --name storeypath --gpus all -p 8080:8080 -v storeypath:/data storeypath/studio:gpu
docker logs -f storeypath        # "vision model ready", then Studio's address
```

Loading the model takes a minute or two after each start. Pick a card with
`--gpus '"device=1"'`; `-e STOREYPATH_VISION=off` runs without vision; the model
server's log is `/tmp/vision.log` in the container. The build compiles llama.cpp
for A100 and H100 and newer by default (`--build-arg
CUDA_ARCHITECTURES="80-real;90-real"` for just those two: a shorter build).

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
`--build-arg MODEL=Qwen3.5-2B-Q4_K_M.gguf` for a smaller image.

## How it fits together

```
DWG / DXF ──▶ StoreyPath Studio ──▶ package (*.storeypath) ──▶ StoreyPath Viewer (in your web app)
               find · convert · review · export               └─▶ any other system: import objects.csv,
                                                                    map our IDs to its own
```

| Folder | What it is |
|---|---|
| [studio/](studio) | **StoreyPath Studio** (Python): reads drawings, keeps IDs stable across revisions, the web app and review editor, exports packages |
| [viewer/](viewer) | **StoreyPath Viewer** (JavaScript): to embed in web apps — the walk-through 3D world, and a map view with search |
| [spec/](spec) | **The package format**: [FORMAT.md](spec/FORMAT.md) and JSON Schemas — everything a system needs to read a package without our code |
| [docker/](docker) | The one-container build |

## Stable IDs

Every object gets a hierarchical ID that says exactly where it is, and that stays
the same when revised drawings are imported again:

```
PROJECT-LOCATION-BUILDING-FLOOR-OBJECT        K7Q2XM-RUH-HQ-F02-0142
```

Systems store our ID as the key of their own mapping (to employees, desks,
bookings…). Each export carries a list of IDs added, changed and retired since
the previous one, and a retired ID is never issued again.

## Status

| # | Milestone | State |
|---|---|---|
| 1 | Format v0, IDs, workspace, schemas, validator | done |
| 2 | One floor from a DXF with room outlines → package → viewer | done |
| 3 | Spaces from walls when there are no room outlines; review editor | done |
| 4 | Several floors and buildings aligned; placement | done: floors on one sheet found and stacked; placement by anchor + bearing |
| 5 | Navigation graph and routing | next |
| 6 | DWG and real-world samples | done for a first real sheet set; a wider sample collection next |
| 7 | Hardening: change reports, docs, releases | done: change lists; releases with a multi-arch image on ghcr.io |
| 8 | Reading drawings like a person: layers, plans, units, room names | done |

## Licence

StoreyPath is [Apache License 2.0](LICENSE). The container also holds, each under
its own licence (all in `/usr/share/storeypath/licenses`): LibreDWG's `dwg2dxf`
(GPL-3.0-or-later, run as a separate program; its exact source is in the image),
llama.cpp (MIT), the Qwen3.5 model weights (Apache-2.0), three.js (MIT), MapLibre
GL JS (BSD-3-Clause) and JSZip (MIT). SymPoint-V2 is never in it unless you fetch it
yourself before building, for research use only.
