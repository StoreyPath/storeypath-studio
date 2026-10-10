# With a GPU or without

StoreyPath works both ways. Without a GPU, rules read the drawing and a small language
model on the CPU reads its texts. With a GPU, a vision model also looks at the plans and
makes the calls a person makes at a glance; it runs in the GPU helper, a second, optional
image, or on any OpenAI-compatible server that takes images.

- [What each does](#what-each-does) · [Build the GPU helper](#build-the-gpu-helper) ·
  [Run the GPU helper](#run-the-gpu-helper)
- [The language model](#the-language-model) · [The vision model](#the-vision-model) ·
  [Symbol spotting (research use only)](#symbol-spotting-research-use-only)

Step by step, which model decides what: [How Studio reads a
drawing](HOW-STUDIO-READS-A-DRAWING.md#with-a-gpu-and-without).

## What each does

| | Without a GPU | With a GPU |
|---|---|---|
| Images | `ghcr.io/storeypath/studio` ([docker/Dockerfile](../docker/Dockerfile)) | the same Studio, and the GPU helper ([storeypath-gpu-helper](https://github.com/StoreyPath/storeypath-gpu-helper)) on the machine with the GPU |
| Reads the drawing | rules | rules |
| Reads the texts (room names, titles, notes, levels) | the language model, Qwen3.5-4B, on the CPU | the same |
| Looks at the plan (is it a room? merged rooms; a type from what is drawn) | no: those are left to a person in review | the vision model, Gemma 4 31B, on the GPU helper |
| Needs | 2 CPU cores, 4 GB of memory | and for the helper, an NVIDIA GPU with 32 GB free |

The GPU helper is optional and separate: Studio is told where it is
(`STOREYPATH_VISION_URL`, with the key it asks for) and uses it once it answers. One
Studio can use several helpers, on several GPUs or machines, and spreads each floor's
rooms over them. Any vision model **served elsewhere** works the same way (your own
OpenAI-compatible server, or a hosted service: `STOREYPATH_VISION_URL`, and
`STOREYPATH_VISION_MODEL`, `STOREYPATH_VISION_KEY` as it needs). Views of the plan
around each room, the drawings' texts when they are checked for private
information, and the rows of their door and window schedules are then sent to it.

Every answer a model gives is kept with the project, so a project read with a GPU
reads the same again on a machine without one.

## Build the GPU helper

From its own repository, [storeypath-gpu-helper](https://github.com/StoreyPath/storeypath-gpu-helper), on any machine with
internet (an Apple Silicon Mac builds it too, through Docker Desktop's x86 emulation,
in an hour or two):

```sh
git clone https://github.com/StoreyPath/storeypath-gpu-helper.git && cd storeypath-gpu-helper
./fetch-vision.sh fp8                                     # once: the model for vLLM, checksum-verified
docker build --platform linux/amd64 -f Dockerfile.vllm -t storeypath/gpu-helper:vllm .
# or, for an NVIDIA driver older than vLLM needs: llama.cpp
./fetch-vision.sh                                         # once: 19 GB, checksum-verified
docker build --platform linux/amd64 -t storeypath/gpu-helper .
```

Two builds of the same model, Gemma 4 31B: **vLLM** (`storeypath/gpu-helper:vllm`), about
four times as many rooms a minute (some 90 on one GPU against 22, with the same answers),
for NVIDIA driver 575 or newer (535 or newer on a data-center GPU such as an A100 or H100),
taking 44 GB of the card by default; and **llama.cpp** (`storeypath/gpu-helper`), for any
driver from 525. Studio sends the vLLM build 16 questions at once
(`STOREYPATH_VISION_PARALLEL=16`, or the helper's *places at once* on the GPU helpers
page); it learns the model's name from the helper. The helper's README has the details.

llama.cpp is compiled for every GPU generation from A100 on by default;
`--build-arg CUDA_ARCHITECTURES="80-real;90-real"` builds for A100 and H100 alone,
which is quicker, and `--build-arg JOBS=4` limits how many compile at once. To carry it
to a machine without internet, `docker save` it as Studio's image is
([Carry it to an air-gapped machine](INSTALL.md#carry-it-to-an-air-gapped-machine)).

## Run the GPU helper

On the GPU machine (Linux, Docker, the NVIDIA Container Toolkit, NVIDIA driver 525 or
newer), with a key of your own that Studio will send (at least 32 characters):

```sh
printf 'STOREYPATH_HELPER_KEY=%s\n' "$(openssl rand -hex 32)" > helper.env && chmod 600 helper.env
docker load -i storeypath-gpu-helper.tar.gz
docker run -d --name storeypath-gpu --gpus all -p 8105:8105 --env-file helper.env storeypath/gpu-helper
docker logs -f storeypath-gpu    # its certificate's fingerprint; the model loads in a minute or two
```

Then tell Studio where it is, with the same key:

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data \
    -e STOREYPATH_VISION_URL=https://gpu-machine:8105/v1 -e STOREYPATH_VISION_KEY=<the same key> \
    ghcr.io/storeypath/studio
```

Studio prints each helper as it starts, and whether it answers. Once Studio runs, an
admin sets the helpers on its *GPU helpers* page instead (in the person menu): each
one's address, key, whether it is used and how many rooms it takes at once, kept in
Studio's database and used at once, with how each is, the model it serves (all must
serve the same one) and a *Test* that sends it a sample room.

| | |
|---|---|
| GPU memory | one NVIDIA card with 32 GB free: the vision model, and two rooms looked at at once |
| Choose a card | `--gpus '"device=1"'` |
| Rooms looked at at once | `-e STOREYPATH_HELPER_SLOTS=2` (the default; each more needs more GPU memory), and as many on Studio's side: `STOREYPATH_VISION_PARALLEL`. `STOREYPATH_HELPER_CONTEXT` (default 8192) is the context each gets |
| Several helpers | one on each GPU or machine (`--gpus '"device=0"' -p 8105:8105`, `--gpus '"device=1"' -p 8106:8105`, …), all with the same key, and Studio given them all: `STOREYPATH_VISION_URL=https://gpu1:8105/v1,https://gpu1:8106/v1,https://gpu2:8105/v1`. It spreads each floor's rooms over the helpers that answer, leaves out one that fails, and tries it again a while later |
| The key | every call but `/health` needs it, and it must be at least 32 characters (`openssl rand -hex 32`); the helper does not start without one (`-e STOREYPATH_HELPER_OPEN=1` serves without, on a network nothing else can reach) |
| HTTPS | on by default: the helper makes a certificate of its own as it starts (its fingerprint in its log), so the calls and the key are encrypted. Studio cannot check that certificate, so it does not know *who* it is talking to: someone on your network posing as the helper could collect the key. Its GPU helpers page and log say so: *HTTPS, its own certificate (not checked)*. To close that, give the helper a certificate from your organisation's CA (`-v /etc/helper-tls:/tls:ro -e STOREYPATH_HELPER_CERT=/tls/cert.pem -e STOREYPATH_HELPER_CERT_KEY=/tls/key.pem`) and Studio that CA (`STOREYPATH_VISION_CA`, a file mounted into Studio): Studio then checks it, refuses one it does not vouch for and says *HTTPS, certificate checked*. Or keep the helpers on a network only Studio reaches. `STOREYPATH_HELPER_PLAIN_HTTP=1` serves plain HTTP (`http://…`), for an SSH tunnel or a proxy that speaks HTTPS. The helper's README has a [Security](https://github.com/StoreyPath/storeypath-gpu-helper#security) section |
| Beside Studio, on one machine | `docker compose -f docker/compose.yml --profile gpu up -d`, the key in `docker/.env` ([docker/compose.yml](../docker/compose.yml)) |
| Its log | `docker logs storeypath-gpu` |

Studio works the same without a helper, and a project read with one reads the same
again without (the answers are kept with it). The helper runs on vLLM where the driver
allows, else on llama.cpp ([its README](https://github.com/StoreyPath/storeypath-gpu-helper)).
Every setting of Studio's side: [studio/README.md](../studio/README.md#looking-at-the-plans-vision).

## The language model

Small enough for any CPU, measured on [studio/eval](../studio/eval) — 137 room labels
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
window schedules, and the private texts in a drawing. Its settings (`STOREYPATH_MODEL`,
`STOREYPATH_THREADS`, …): [studio/README.md](../studio/README.md#the-language-model).

## The vision model

The GPU helper runs Gemma 4 31B on the GPU (4-bit with llama.cpp, FP8 with vLLM); any
OpenAI-compatible endpoint that takes images works instead (`llama-server` with
the model's `--mmproj`, vLLM, or a hosted service). Measured on 119 rooms of two
houses and an interior designer's furniture plan, each checked by hand, Gemma 4 31B
judged 84% of outlines and 85% of types right. It is asked only about rooms Studio
found, its answers are kept by each room's shape (reading again asks only about
rooms that changed), and each call it makes is marked for review. What it does,
step by step: [Vision](HOW-STUDIO-READS-A-DRAWING.md#g-vision-looking-at-each-room-visionpy).

## Symbol spotting: research use only

SymPoint-V2 types unnamed rooms by the fixtures drawn in them (a toilet and a bath:
a bathroom). It is **not StoreyPath's**: its repository states no licence and its
weights were trained on non-commercial data (FloorPlanCAD, CC BY-NC), so it is **for
research only**, and images built with it **must not be published or sold**.

It is never in an image unless you fetch it before building the Studio image:

```sh
docker/fetch-symbols.sh                                 # its code (pinned) and weights (checksum-verified)
docker build -f docker/Dockerfile -t storeypath/studio . # baked in, with PyTorch for the CPU
```

The release workflow leaves it out unless the repository variable
`STOREYPATH_SYMBOLS` is set to `research`; the GPU helper never has it. See
[studio/README.md](../studio/README.md#symbols-drawn-in-a-plan-optional-research-use-only).
