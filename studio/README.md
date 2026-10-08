# StoreyPath Studio

The authoring side of [StoreyPath](https://github.com/StoreyPath/storeypath):
converts DWG/DXF floor plans into StoreyPath packages, lets you review, correct and
complete the result (walls, doors, furniture and equipment), and exports packages
that keep the same object IDs every time.

- [Install](#install) · [In the browser](#in-the-browser) ·
  [Users, sharing and backups](#users-sharing-and-backups) · [Commands](#commands)
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

It is served over **HTTPS**, at `https://127.0.0.1:8080`, with a certificate Studio
makes itself the first time (below); the first start prints a link to set up the
first admin, and everyone then logs in ([Users, sharing and
backups](#users-sharing-and-backups)).

Projects are kept in the data folder, one folder each named by the project's code:
`<data>/<code>/<code>.spproj`, with its `drawings/` and `exports/` beside it. The
catalogue of item types, `catalogue.json`, is in the data folder itself, for every
project; so are the accounts, who each project is shared with and the audit log, in
one SQLite file, `studio.db`, and Studio's certificate, in `tls/`.

To reach Studio from other machines, serve on all interfaces (`--host 0.0.0.0`).
It answers only to its own names: localhost, this machine's name and any address;
give another name it is reached by (a server's, a proxy's) with `--allowed-host`
(again for more) or `STOREYPATH_ALLOWED_HOSTS`: it goes on the certificate too.

### HTTPS

| | |
|---|---|
| By default | Studio makes an EC P-256 key and a self-signed certificate in `<data>/tls/` (the key readable by its owner alone), valid 825 days, for `localhost`, `127.0.0.1`, `::1`, the address it is bound to, every `--allowed-host` / `STOREYPATH_ALLOWED_HOSTS` name or address, and this machine's name and addresses as found when it is made. It prints the certificate's SHA-256 fingerprint as it starts: browsers warn once about a certificate nobody vouches for, and the fingerprint they show should be that one. It is made again (browsers then warn again) only when it is within 30 days of its end, or lacks a name you give with `--allowed-host`; to reach Studio by another name or address, give it so |
| Your own certificate | `--cert cert.pem --key key.pem` (PEM, the chain after the certificate); Studio never changes it |
| Behind a proxy that speaks HTTPS | `--http --secure-cookies --trusted-proxy <the proxy's address>`: plain HTTP from the proxy, the session cookie sent over HTTPS only; give the name people use with `--allowed-host`. The proxy must say who is asking in `X-Real-IP` (nginx: `proxy_set_header X-Real-IP $remote_addr;`; `X-Forwarded-For` is taken when it sends no `X-Real-IP`): the limits on failed logins and the audit go by it, and a call through the proxy without it is refused (400), so a proxy left unset shows at once rather than putting everyone behind one address. From any other address those headers are not heard |
| On this computer, for development | `--http` |

Plain `http://` sent to the HTTPS port is redirected to the same address over
HTTPS. A connection that sends nothing for 60 seconds is closed (a large drawing on a
slow link uploads for as long as it keeps sending), and Studio serves 128 connections
at once: one more is closed as it comes. The review editor alone (`storeypath review`) and `storeypath view` serve
plain HTTP on 127.0.0.1, for this computer only.

## Users, sharing and backups

Everyone logs in; each person sees the projects they own or that are shared with
them, and is offered only what they may do there.

| Role | |
|---|---|
| admin | manages users (add, change role and capabilities, disable, reset a password), sees and does everything, gives a project another owner |
| engineer | creates projects and opens packages and project files as new projects; owns what they make |
| user | sees only what is shared with them |

An admin may also give anyone a **capability**: `backup` (download the whole data
folder) or `catalogue` (change the item types). Admins have both. A backup holds the
accounts with their passwords' scrypt hashes (a restore needs them) and the audit log:
`backup` hands those over too, so give it only to whom you would trust with them.

**Sharing.** A project's owner (and anyone they let) gives a person a **level** on a
**scope**: *view* (see it: plans, review, 3D, its packages), *edit* (change it as
well: corrections, items, walls and doors, drawings read again, placement) or
*share* (give others access within it, up to share); on the whole project, one of
its buildings, or one of its floors. A grant on the project covers all its buildings
and floors, ones added later too; on a building, all its floors. A person's level on
a floor is the highest of theirs on the floor, its building and its project; the
owner has share on the whole project, an admin everywhere. Someone with share on a
building shares that building and its floors, nothing wider; nobody changes their
own access, nor the owner's. Only the owner or an admin deletes a project. Projects
made before Studio had accounts have no owner: admins manage them, and may give them
one. A project file or package never carries users or access.

Someone who may see only some floors sees only those: the project page lists them
(and the drawings of those floors), Review offers only them, read only where they
may only view (a *View only* badge, no tools), and the 3D view is built with those
floors alone. A floor's drawing and print are its plan's part of the sheet; a floor
read from a whole sheet that other floors are read from too shows theirs, so it
needs view on each of them. A building's package holds every floor of it: making
one needs edit on the whole building, downloading one view on it.

**First start.** With no users yet, `storeypath serve` prints a link with a one-time
token, `https://<host>:8080/setup.html#<token>` (in a container: `docker logs`), to
make the first admin; it works until Studio stops, and once only. Unattended (a
container started by a script): set `STOREYPATH_ADMIN_PASSWORD` (and
`STOREYPATH_ADMIN_USER`, default `admin`), and that admin is made at the first start
instead.

**Passwords and sessions.** Passwords have at least 10 characters and are not the
username; Studio keeps only their scrypt hashes. A password an admin sets (a new
user, a reset) is temporary: it is changed at the next login before anything else.
A session lasts until it is not used for 8 hours, or a week at most, and survives
a restart of Studio; logging out, changing one's password, or an admin changing
someone's role or capabilities or disabling them ends their sessions. After 5 failed
logins for a username from one address, or 20 from one address, within 15 minutes,
logins from there wait (from 30 seconds, doubling, up to 15 minutes): someone else's
wrong passwords never lock a person out. A wrong current password, changing one's
own, counts as a failed login. Each try is counted before its password is checked,
and passwords are checked four at a time (scrypt takes 32 MiB each; more wait a
moment, then are answered 503); a wait is written in the audit log once in 15
minutes. Logging in takes at most 4 KB, and a username of more than 64 characters
is refused unread. Users are never deleted, only disabled.

**From the command line**, on the data folder (also while Studio runs):

```sh
storeypath users add s.ahmed --role engineer --name "Sara Ahmed" --data /data   # asks for the password twice
storeypath users add ops --role user --capability backup --password-stdin --data /data < pw.txt
storeypath users list --data /data
storeypath users passwd s.ahmed --data /data        # temporary: changed at the next login (--permanent: not)
storeypath users disable s.ahmed --data /data       # enable, again; role s.ahmed admin
storeypath backup --data /data --out studio-backup.tar.gz
storeypath restore studio-backup.tar.gz --data /new/empty/folder
```

**Backups.** *Download a backup* in the person menu (admins, and whoever has the
`backup` capability), `GET /api/backup`, or `storeypath backup`, gives the whole
data folder as `storeypath-backup-<UTC time>.tar.gz`: every project, the item types,
and `studio.db` (as a snapshot taken whole, never the file in use): the accounts with
their passwords' hashes, who each project is shared with, and the audit log, but no
session (nobody is logged in to a Studio restored from it). Studio's certificate and
key (`tls/`) never go in: a Studio restored makes a new certificate, and browsers warn
once about it, as at a first start (or give it yours with `--cert`, `--key`). It
leaves out work in progress (drawings sent and not yet cleaned of private
information, packages being written) and the prints Studio draws again when needed;
no link is followed or kept. It is written as it is sent, and no job starts while it
is. `storeypath restore` puts one back into an empty folder only, refusing anything
in it that would land outside. Keep backups as safe as the data folder itself.

**The audit log**, on the Users page: logins (and failed and throttled ones),
logouts, passwords changed and reset, users created, changed and disabled, grants
added, changed and removed, owners changed, projects created, opened and deleted,
exports, backups and the setup: when, who, from what address.

What each call of the API needs (`route()` in `server.py` asks first, in every case;
a test fails for a call without a rule). Logged out, every call but logging in, out
and the setup is answered 401; a project, building or floor someone may not see at
all, 404, as one that is not there; one they see but may not do this to, 403. A
request Studio cannot make sense of is answered 400 saying why; one that fails in
Studio itself, 500 with nothing of what went wrong (that goes to its log).

| Call | Needs |
|---|---|
| `POST login`, `logout`, `setup` (no users yet) | nobody |
| `GET me`, `POST me/password` | logged in (with a temporary password: nothing else) |
| `GET status`, `catalogue`, `projects` | logged in; projects: those they have any access to, each cut to what they see, with `can`. Where the server keeps things (the data folder in `status`, a project's `exports_folder`) is told to admins alone |
| `POST catalogue` | admin, or `catalogue` |
| `POST projects` | admin or engineer, who owns it |
| `PUT open` | a new project: admin or engineer, who owns it (when nothing is kept of who a project of its code was shared with; else an admin opens it, and that stays). A package into a project here: edit on each building it brings (on the project for a new one) and on each floor an item it holds comes from. A project file in place of one here: its owner or an admin. The file is read once, and every part of it that names its project must name the same one: the project checked is the one written. A new project never takes the place of a folder that is not its own. Someone who may not see the project here is answered as for a new project, never told its name |
| `GET projects/<code>`, `…/review` | any access; cut to what they see |
| `POST …/delete` | its owner or an admin |
| `GET …/access`, `POST …/access {user, scope, level}` | share on some part of it; changes within the parts they have share on |
| `POST …/owner {user}`, `GET admin/users`, `POST admin/users…`, `GET admin/audit` | admin |
| `GET users` | someone who may share something, or an admin: active users' id, username and name |
| `PUT …/drawings/<name>`, `POST …/incoming/…`, `GET …/drawings/<name>/words`, `POST …/drawings/<name>/plans` | edit on the project (a drawing is the project's, and may hold several floors) |
| `POST …/floors` (add floors) | view on the project (the drawing is the project's) and edit on each building they go into (on the project for a new building) |
| `POST …/convert` | edit on the project; with `floor`, edit on that floor |
| `POST …/buildings/<id>/site`, `…/placement` | edit on the building |
| `POST …/locations/<id>/arrange`, `…/placement` | edit on the project |
| `POST …/export {building}` | edit on that building |
| `GET …/exports/<file>` | view on the building it holds (on the project, for one no export entered) |
| `GET …/preview.storeypath[?building]` | any access: built with their floors alone |
| `GET …/project.storeypath-project` | view on the project |
| `GET …/floors/<id>`, `…/drawing`, `…/print`, `…/print.png` | view on the floor; its drawing and print as above |
| `POST …/floors/<id>/edits`, `…/items`, `…/convert`, `POST …/objects/<id>` | edit on the floor |
| `POST …/items/<id>` | edit on its floor, and on the floor it is carried to |
| `GET jobs/<id>` | an admin, or view (now) on what it works on: its project, building or floor (who started it too, while they still may) |
| `GET backup` | admin, or `backup` |

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
| `review FILE` | open the review editor on this project (on this machine, port 8766, plain HTTP, no accounts) |
| `serve` | run the web app, over HTTPS: `--data`, `--host`, `--port`, `--open`, `--allowed-host`; `--cert`, `--key` (your certificate), `--http` (plain HTTP), `--secure-cookies`, `--trusted-proxy` (also `STOREYPATH_TRUSTED_PROXIES`) |
| `users add USERNAME` | add a user: `--role admin\|engineer\|user`, `--name`, `--capability backup\|catalogue`, `--password-stdin`, `--permanent` (not to be changed at the first login); `--data` |
| `users list` · `passwd` · `disable` · `enable` · `role` | list the users; set a password (their sessions end); disable or enable one; change a role |
| `backup` | the whole data folder as one `.tar.gz` (`--data`, `--out`) |
| `restore FILE --data DIR` | put a backup into an empty folder |
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
beds, wayfinding kiosks) are placed on floors in the review editor, and kept in the workspace. Each has
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
the data folder the workspace is in, else the built-in types. A package or project
file opened in Studio adds the types it brings that the catalogue lacks only when an
admin, or someone with the `catalogue` capability, opens it; anyone else's opens all
the same, says which types were not added, and their items are drawn as plain items
until someone who may adds them (until then no package of that building can be made:
its items' types are not in the catalogue).

A space's or zone's capacity is the number set in review, else the workplaces of the
items standing in it; its grade is the highest grade among its desks.

## Other settings

| Environment | |
|---|---|
| `STOREYPATH_ALLOWED_HOSTS` | more names Studio may be reached by, separated by commas or spaces (`*`: any); as `serve --allowed-host` (each goes on Studio's certificate) |
| `STOREYPATH_ADMIN_PASSWORD` | with no users yet, the first admin is made at start with this password (no setup link) |
| `STOREYPATH_ADMIN_USER` | that admin's username (default `admin`) |
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
