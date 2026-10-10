# Installing StoreyPath Studio

Studio is one container with its database inside, with or without a GPU, and works with
no network at all. This page has every way to run it; the [README](../README.md) has the
short version.

- [Run it with Docker](#run-it) · [The demo campus](#the-demo-campus)
- [Offline images: Studio and the GPU helper](#offline-images-studio-and-the-gpu-helper) ·
  [Build the Studio image](#build-the-studio-image) ·
  [Carry it to an air-gapped machine](#carry-it-to-an-air-gapped-machine)
- [Without Docker](#without-docker) · [Requirements](#requirements) ·
  [Configuration](#configuration)

The GPU helper (optional, for the vision model) has its own page:
[With a GPU or without](GPU.md). Accounts, sharing, HTTPS and backups:
[Users, sharing and backups](ACCOUNTS.md).

## Run it

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
```

Then open https://localhost:8080 and log in as `admin`, password `admin` (change it
in the person menu, top right, when you like), create a project and drop in a
drawing. The browser warns once about Studio's own certificate: the log prints its
SHA-256 fingerprint, to compare with the one the browser shows. Everything Studio
keeps lives in the `storeypath` volume, so it survives restarts and upgrades: its
database (the projects with their drawings and exports, the accounts and who sees
what), its certificate and its caches. The database is PostgreSQL with PostGIS,
inside the container: it starts with Studio, and stops cleanly after it on `docker
stop`. The image needs no GPU and runs on amd64 and arm64: Linux, macOS and Windows
with Docker.

| | |
|---|---|
| Stop, start again | `docker stop storeypath` · `docker start storeypath` |
| Upgrade | `docker pull ghcr.io/storeypath/studio && docker rm -f storeypath`, then the `run` line again |
| The first admin's password | `admin` / `admin` at the first start; `-e STOREYPATH_ADMIN_PASSWORD=…` to start with another |
| Let others on your network use it | publish the port on all interfaces: `-p 8080:8080`, and add them on the Users page. They reach it by the machine's address or name: give it with `-e STOREYPATH_ALLOWED_HOSTS=studio.example,192.168.1.20` so it is on the certificate (Studio also refuses requests addressed to names it does not know, so a web page cannot reach it through DNS rebinding) |
| Your organization's certificate | mount it and pass it: `-v /etc/studio-tls:/tls:ro ghcr.io/storeypath/studio serve --host 0.0.0.0 --port 8080 --data /data --cert /tls/cert.pem --key /tls/key.pem` |
| Behind a proxy that speaks HTTPS | `… serve --host 0.0.0.0 --port 8080 --data /data --http --secure-cookies --allowed-host studio.example --trusted-proxy 10.0.0.5` (the proxy's address or network; or `-e STOREYPATH_TRUSTED_PROXIES=…`). The proxy must pass who is asking in `X-Real-IP` (nginx: `proxy_set_header X-Real-IP $remote_addr;`): Studio refuses calls through it without |
| Users, backups | the person menu (top right): *Users* for admins, *Download a backup*; or `docker exec storeypath storeypath users list`, `docker exec storeypath storeypath backup --out /tmp/studio.backup` ([Users, sharing and backups](ACCOUNTS.md)) |
| Look at the plans with GPU helpers | `-e STOREYPATH_VISION_URL=https://gpu1:8105/v1,https://gpu2:8105/v1 -e STOREYPATH_VISION_KEY=…` (the helpers' key): [Run the GPU helper](GPU.md#run-the-gpu-helper). Any OpenAI-compatible endpoint that takes images works too |
| Its database | inside, reached over a socket only (no port): `docker exec -it storeypath psql`. Its log: `/data/pg/log` |
| Another database | `-e STOREYPATH_DATABASE_URL=postgresql://user:password@host/storeypath`: your own PostgreSQL 17 with PostGIS 3, in place of the one inside (which then does not start). Optional: Studio needs none |
| Docker Compose | `docker compose -f docker/compose.yml up -d`, the same Studio and volume; `--profile gpu` adds the GPU helper beside it ([docker/compose.yml](../docker/compose.yml)) |
| Logs | `docker logs -f storeypath` |

The same image is the command-line tool: `docker run --rm -v "$PWD:/data"
ghcr.io/storeypath/studio views house.dwg`. [The command line](CLI.md) lists the
commands.

### The demo campus

```sh
docker exec storeypath storeypath demo --studio
```

puts the demo campus in Studio as a project (*Demo Campus*, `CAMP05`): all of it made
up, built the same every time. A main building of three floors and a pavilion of two,
read from their drawings (the pavilion's without room outlines, its rooms found from its
walls); furnished (desks by grade with their chairs, sofas, screens, copiers, access
points, a wayfinding kiosk at the entrance), finished (a marble reception, carpeted
offices, wood, tiled restrooms, accent walls), and a few rooms left for you to review.
Admins see it on the Projects page. `storeypath demo <folder>` writes it as files
instead: its drawings, the workspace and a package of each building, and
`drawings/main-building-sheet.dxf`, the main building's three plans on one sheet, to
drop into a project and see its plans found.

## Offline images: Studio and the GPU helper

Both images hold everything they need. Nothing is downloaded when they run: no
models, no telemetry, no map tiles, and the 3D view's libraries are served by Studio
itself. The models are fetched once, **before** the build, by scripts that check
their checksums, and baked in.

| | Studio | GPU helper (optional) |
|---|---|---|
| Built from | [docker/Dockerfile](../docker/Dockerfile) | [storeypath-gpu-helper](https://github.com/StoreyPath/storeypath-gpu-helper), a repository of its own |
| Published | `ghcr.io/storeypath/studio`, amd64 and arm64, by [the release workflow](../.github/workflows/release.yml) for each version tag | not published: build it yourself (amd64) |
| Models fetched first | `docker/fetch-models.sh`: Qwen3.5-4B (2.7 GB) | its `fetch-vision.sh`: Gemma 4 31B, 4-bit (about 18 GB) and its image encoder (about 1 GB) |
| Inside | Studio; its database, PostgreSQL 17 with PostGIS 3; LibreDWG's `dwg2dxf` for DWG; llama.cpp's `llama-server` for the CPU (it picks the fastest CPU code at start); the language model; Node.js for pre-built 3D; the viewers | llama.cpp's `llama-server` built for CUDA (A100/A30, A10/A40/RTX 30, L4/L40/RTX 40, H100/H200, Blackwell) with NVIDIA's CUDA runtime, and the vision model: nothing else |
| Starts | its database, then Studio; the language model loads in the background | the vision model on the GPU (a minute or two); Studio uses it once it answers |

### Build the Studio image

```sh
docker/fetch-models.sh                                   # once: the language model (2.7 GB, checksum-verified)
docker build -f docker/Dockerfile -t storeypath/studio .
```

`docker/fetch-models.sh 2B` and `--build-arg MODEL=Qwen3.5-2B-Q4_K_M.gguf` build a
smaller image with a smaller model ([The language model](GPU.md#the-language-model)).
Building the GPU helper: [Build the GPU helper](GPU.md#build-the-gpu-helper).

### Carry it to an air-gapped machine

On the machine with internet, save the image to a file (the published Studio image, or
one you built):

```sh
docker pull ghcr.io/storeypath/studio
docker save ghcr.io/storeypath/studio | gzip > storeypath-studio.tar.gz
docker save storeypath/gpu-helper | gzip -1 > storeypath-gpu-helper.tar.gz   # the GPU helper
```

Carry the file over, then on the isolated machine:

```sh
docker load -i storeypath-studio.tar.gz
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
```

(Pull with `--platform linux/amd64` or `linux/arm64` to carry an image for a machine
of the other kind.) The command-line tool runs with `--network none`; the web app
needs nothing but its published port.

## Without Docker

Studio is a Python program and runs from a clone of this repository with
[uv](https://docs.astral.sh/uv/), which fetches Python 3.12 itself if it needs to:

```sh
brew install uv          # macOS; on Linux: curl -LsSf https://astral.sh/uv/install.sh | sh
git clone --recurse-submodules https://github.com/StoreyPath/storeypath-studio
cd storeypath-studio/studio
uv sync
uv run storeypath serve --data ~/storeypath --open
```

That is the whole web app. Its data goes into a PostgreSQL database, as in the
container: any PostgreSQL 17 with PostGIS 3, named with `export
STOREYPATH_DATABASE_URL=postgresql://user:password@localhost/storeypath`. On its own it reads
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

Then, back in `storeypath-studio/studio`, fetch the model once and start Studio with it:

```sh
../docker/fetch-models.sh                            # 2.7 GB, checksum-verified
export STOREYPATH_MODELS="$PWD/../docker/models"
uv run storeypath serve --data ~/storeypath --open
```

Studio says what it found as it starts: `language model: Qwen3.5-4B-Q4_K_M; DWG:
yes` (and the vision model, when one is set), its address (`https://127.0.0.1:8080`,
with a certificate it made itself: the browser warns once) and, the first time, the
link to make the first admin. `--http` serves plain HTTP, for development on this
computer. Add the `export` lines to your shell profile to keep them. To update, `git
pull`, then `uv sync`. `uv run storeypath demo --studio` puts the demo campus in
Studio; [studio/](../studio) has every command and setting. Also, as you need them:

- **Vision**: `uv sync --extra vision`, and `STOREYPATH_VISION_URL` set to a vision
  model's endpoint. To serve Gemma 4 yourself on an NVIDIA GPU, fetch it with the
  GPU helper's `fetch-vision.sh` and run a CUDA build of `llama-server` with it and its
  `--mmproj`, as its [start.sh](https://github.com/StoreyPath/storeypath-gpu-helper/blob/main/start.sh) does (or run the GPU helper's
  image: [storeypath-gpu-helper](https://github.com/StoreyPath/storeypath-gpu-helper)).
- **The 2D plan page**: `npm ci && npm run build` in `storeypath-viewer/viewer/svg` (Studio offers
  *2D plan* once it is built).
- **Pre-built 3D in packages**: Node.js 20.6 or newer on the `PATH`.

## Requirements

Measured with a real architect's sheet set (a villa: one AutoCAD 2004 DWG, 91,000
entities on 44 layers, every plan side by side with elevations, sections and title
blocks), start to finish, inside containers limited to each size:

| | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores, 64-bit: x86-64 (Intel/AMD) or ARM64 (Apple Silicon, Graviton, Ampere) | 4 or more cores |
| Memory | 4 GB for the container | 8 GB on the machine |
| Disk | 8 GB: the image is about 3 GB to download, 7 GB unpacked | plus your drawings |
| GPU | none for Studio: the language model runs on the CPU | for the GPU helper: [see its page](GPU.md#run-the-gpu-helper) |
| Software | Linux with Docker 20.10 or newer; macOS or Windows with Docker Desktop (give it at least 4 GB of memory in its settings) | |
| Browser | a current Chrome, Edge, Firefox or Safari (WebGL 2) — built-in laptop graphics are plenty for the 3D view | |
| Network | to pull the image, once | none to run |

| The whole villa: 16 drawings found, 3 floors converted, exported | Time | Peak memory |
|---|---|---|
| 2 cores, 4 GB limit | 128 s | 1.4 GB |
| 2 cores | 129 s | 1.4 GB |
| 4 cores | 90 s | 1.4 GB |
| 18 cores (Apple M5 Max) | 37 s | |

On that drawing, with networking switched off and no setup of any kind:

| | |
|---|---|
| Drawings found and titled | 16, in 21 s |
| Floors stacked | 3, each within 1 cm of the structural grid |
| Rooms found | 54, of which 30 typed by the language model |
| Doors, openings and windows | 85, each with its width and position |
| Total, upload to valid package | 37 s on a laptop (Apple M5 Max); 90 s on 4 cores, 129 s on 2 |

The language model's weights are mapped from the image rather than loaded, which is
why the container needs so little memory of its own; more memory just keeps them
cached.

## Configuration

Studio is set by its command line (`storeypath serve --help`), the environment and,
for the GPU helpers, its *GPU helpers* page. The environment, in one place:

| Environment | | More |
|---|---|---|
| `STOREYPATH_DATABASE_URL` | Studio's database: a PostgreSQL 17 with PostGIS 3. The image sets it to its own | [studio/README.md](../studio/README.md#other-settings) |
| `STOREYPATH_ADMIN_PASSWORD` | the first admin's password, at a first start | [Accounts](ACCOUNTS.md) |
| `STOREYPATH_ALLOWED_HOSTS` | more names Studio may be reached by (each goes on its certificate) | [Run it](#run-it) |
| `STOREYPATH_TRUSTED_PROXIES` | a proxy in front of Studio, as `serve --trusted-proxy` | [Run it](#run-it) |
| `STOREYPATH_MODEL`, `STOREYPATH_MODELS`, `STOREYPATH_LLAMA_SERVER`, `STOREYPATH_MODEL_URL`, `STOREYPATH_THREADS`, `STOREYPATH_PARALLEL`, `STOREYPATH_GPU_LAYERS` | the language model: which, where, how it runs | [The language model](GPU.md#the-language-model) |
| `STOREYPATH_VISION_URL`, `STOREYPATH_VISION_KEY`, `STOREYPATH_VISION_MODEL`, `STOREYPATH_VISION_PARALLEL`, `STOREYPATH_VISION_CA`, `STOREYPATH_VISION_INSECURE` | the vision model: its helpers, their key, how they are reached | [The vision model](GPU.md#the-vision-model) |
| `STOREYPATH_NODE` | Node.js, for building the floors' 3D at export (empty: never) | [studio/README.md](../studio/README.md#other-settings) |
| `STOREYPATH_SYMBOLS` | SymPoint-V2's folder (research use only) | [Symbol spotting](GPU.md#symbol-spotting-research-use-only) |
| `STOREYPATH_PORT`, `STOREYPATH_BIND`, `STOREYPATH_VOLUME`, `STOREYPATH_IMAGE`, `STOREYPATH_HELPER_IMAGE`, `STOREYPATH_HELPER_GPU_MEMORY` | Docker Compose's: the port, the address it is published on, the volume, the images | [docker/compose.yml](../docker/compose.yml) |
