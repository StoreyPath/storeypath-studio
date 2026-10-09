# StoreyPath

**Turn real-world DWG and DXF floor plans into indoor maps you can walk through —
read the way an architect reads them.** No layer standards to follow, no templates
to fill in, no cloud: drop in the drawing the architect gave you, and StoreyPath
finds the plans, the floors, the rooms, the doors and what every room is, gives
every object an ID that never changes, lets you check and complete it all (walls,
doors, furniture and equipment), and opens it as a 3D world.

One container, its database inside, with or without a GPU. Works with no network
at all.

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data ghcr.io/storeypath/studio
```

Then open **https://localhost:8080** and log in as `admin`, password `admin`. Studio
serves HTTPS with a certificate it makes itself, so the browser warns once (the log
shows the certificate's fingerprint to compare); everyone logs in and sees what is
shared with them ([Users, sharing and backups](#users-sharing-and-backups)). This
image needs no GPU and runs on amd64 and arm64: Linux, macOS and Windows with
Docker. On a machine with an NVIDIA GPU, an optional second image, the GPU helper,
serves a vision model that looks at the plans:
[Offline images: Studio and the GPU helper](#offline-images-studio-and-the-gpu-helper).

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
- [Navigation: from the kiosk to your office](#navigation-from-the-kiosk-to-your-office)
- [Walk through it](#walk-through-it)
- [With a GPU or without](#with-a-gpu-or-without)
- [Run it](#run-it) · [Users, sharing and backups](#users-sharing-and-backups) ·
  [Offline images: Studio and the GPU helper](#offline-images-studio-and-the-gpu-helper) ·
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
- **2D, 3D, Walk — one view.** The switch in the toolbar shows the floor as a plan,
  as built (orbit it), or walks you through it, in the same place: the floor, what
  you chose, the panel and the plan's view stay as they are, and switching is
  instant once the building is built. Walking: click the view to look, <kbd>W A S
  D</kbd> to move, <kbd>Esc</kbd> frees the mouse for the panel; the room you're in
  is shown, and you start in the room you chose, or where the 3D view or the plan
  was looking. In 3D and walking you edit as on the plan: a click chooses a room or
  an item (walking: at the cross) and its editor opens; choose an item in *Place*
  and click the floor (walking: aim the cross and click) — its ghost shows where it
  will go, held in its room and lined up as on the plan; drag items in 3D;
  <kbd>R</kbd>, <kbd>[</kbd> <kbd>]</kbd> and <kbd>Del</kbd> as on the plan.
  Walls, doors, dividers and spaces are drawn on the plan: right-click (or
  <kbd>2</kbd>) *Draw here in 2D* shows that place there. What changes — yours or
  others' — shows in 3D at once; drawn walls when the floor has been read again.
  *3D in its own window* opens the building on a page of its own.
- **Re-read drawing** reads the floor's drawing again (a revised one too): IDs and
  corrections are kept. A reading that would retire most of the floor's rooms is
  held back, the floor unchanged, and Studio asks before applying it (*Read
  anyway*).

Every change is saved to the project at once.

**Many people at once.** One person edits a floor at a time: the first change takes
it, and everyone else on it sees *Khalid is editing this floor since 10:20 — you can
look* with his changes appearing as he saves them (and who made each), until he is
*Done editing*, leaves the floor, or leaves it alone for 15 minutes (an admin may take
it over). Review's header shows who else is on the floor; a project's page, who is on
which. *Undo* and *Redo* (⌘Z, ⇧⌘Z) take back your own changes — refused, naming who,
when someone else changed the same thing since — and *History* lists who changed what
on the floor, in words ([more](studio/README.md#many-people-at-once)).

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
- **It stays in its room, and lines up.** An item belongs to the room it was placed
  in (right-clicked or clicked in): dragged, nudged or turned, it stops at that room's
  walls (a zone has no walls: the whole space holds it). Near a wall it turns square to
  it and moves up against it, into a corner too; near another item it lines up with it
  (desks into rows); a TV goes on the nearest wall, its screen into the room. A desk
  counts what is drawn round it (its cabinet goes against the wall, not through it).
  Hold <kbd>Alt</kbd> to place or drag it freely, or into another room.
- **Desks show who they are for.** Each grade's desk has its own size and shade, and
  is drawn (in review, on the plan and in 3D) with what goes with it: a junior's
  plain; a senior's with a return (an L-shaped desk); a head of section's with a
  visitor's chair as well; a manager's with two; a director's and a C-level's with a
  cabinet behind a high-backed chair too; the president's with armchairs for its
  visitors. Only the desk itself is the item: the rest is drawn round it.
- **A kiosk** (type `KIOSK`) is where a wayfinding kiosk stands, its screen at its
  front: wayfinder links each of its kiosks to one, so the kiosk's map shows "you are
  here", and a way to an office can later start from it.
- **An item's ID never changes when it moves**: it is an asset's tag, ten random
  symbols and a check symbol (`7K2Q-XM9F-4DP`), of no place and no project. Carried to
  another office, floor or building, it keeps it; deleted, its ID is never issued
  again. It is written on the asset as it is: a person may type it in either case,
  with or without its hyphens, O for 0 and I or L for 1 (Review's search box finds the
  item, `storeypath item-id` reads it), and the check symbol catches a symbol mistyped
  or two swapped.
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

*Export package* writes the package (`.storeypath`, [format 0.8](spec/FORMAT.md)) of
one building, chosen from the list: its floors, spaces, zones, doors, windows and
openings, its items and their catalogue, with every ID, and its walking network
([Navigation](#navigation-from-the-kiosk-to-your-office)). It is checked against the
format before it is kept, downloaded, and listed on the project page (newest first),
with links to view it as a *2D plan*, in *3D*, or as *Rooms by type*.

- Each package lists what was added, changed and retired in its building since that
  building was last exported (`changes.json`), so a system updates its mappings.
- Items carry where they stand in their building (`local`): moving a building on the
  map changes nothing in it, and the change list says only that the building moved.
- Each floor is also built in 3D ahead of time, so a slow machine shows it without
  building it (this takes Node.js, which the Studio image holds).

### Sending a project to another Studio

*Download project* gives one file, `<code>.storeypath-project`: the project with
every correction, edit and ID, its export history, its drawings and the Studio's
item types. Another Studio opens it on its Projects page (*Open a project or a
building's package*) and continues the project where it was (the item types it brings
are added there when an admin, or someone who may change the item types, opens it).
It is not a package: other systems read a building's package.

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

## Navigation: from the kiosk to your office

A person at a kiosk in the lobby types their employee number and is shown the way to
their office: StoreyPath works out where people can walk in each building, and the
way from any place in it to any other, the same in Studio, in the Go module and in
the viewers, so that a system that guides people (wayfinder) gives the answer Studio
gives.

- **The walking network** goes into every package (`navigation.json`, format 0.8):
  every door and opening, a point in each room and zone, the lifts and stairs on each
  floor, the entrances and the wayfinding kiosks, and the walks between them — across
  each room along the shortest way that keeps off its walls, through its doors, and
  from floor to floor by the lifts and stairs that are one (their *stack*). Plant
  rooms, shafts and voids are not walked; a way through someone's office costs a
  minute more than its time, so the corridor is taken.
- **The way** from a kiosk, an entrance or any room to a room, a zone or a desk: the
  quickest (walking at 1.3 m/s, a lift's wait, 12 s a flight of stairs), or without
  stairs (lifts and ramps alone). It comes as the line to draw on each floor, the
  changes of floor, and short steps ready to show — "Walk 48 m along CORRIDOR to the
  lift", "Take the lift up to Floor 1", "OFFICE 112 is on your left" — each also a
  kind and values, for a system to word in its own language (Arabic).
- **Navigate** in Studio (from a project's buildings, and from Review's toolbar):
  choose a start (a kiosk, an entrance, any room) and a destination, avoid stairs if
  need be, and see the steps, the route on each floor's plan and in 3D.
- **Stairs and lifts the drawing missed** are drawn in Review with its *Stairs* and
  *Lift* tools, already typed, and added to the other floors they serve in one go;
  each lift's or staircase's floors are linked by their code or where they overlap,
  and a person may link or unlink them by hand.
- **For other systems**: `Package.Navigation()` and `Route(from, to, opts)` in
  [Go](go), `route(pkg, from, to, { accessible })` in the [viewers](viewer), with
  `showRoute(route)` on the 2D plan and in the 3D world;
  [spec/conformance/routes.json](spec/conformance) holds ways every reader must find
  the same.

## Walk through it

Every package opens as a 3D world, straight from Studio, with nothing downloaded:
the walls at the thickness they were drawn, with skirting, parapets around roofs
and terraces, doorways you walk through, doors with architraves and handles,
windows with sills, boards and glass, furniture with its chairs, floors finished by
what each room is — carpet tiles in offices, terrazzo in lobbies, polished concrete
in corridors, porcelain in restrooms and kitchens, oak in homes — under a sun that
casts soft shadows, with ambient occlusion where surfaces meet. Two looks, one
click apart: **Real** (the default) or **Model**, white like an architect's model
with its edges drawn; and **Quality** Auto (Low on integrated, virtual or software
graphics), High or Low.

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
| Images | `ghcr.io/storeypath/studio` ([docker/Dockerfile](docker/Dockerfile)) | the same Studio, and the GPU helper ([docker/gpu-helper](docker/gpu-helper/Dockerfile)) on the machine with the GPU |
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
reads the same again on a machine without one. [How Studio reads a
drawing](docs/HOW-STUDIO-READS-A-DRAWING.md#with-a-gpu-and-without) says which step
uses which.

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
stop`.

| | |
|---|---|
| Stop, start again | `docker stop storeypath` · `docker start storeypath` |
| Upgrade | `docker pull ghcr.io/storeypath/studio && docker rm -f storeypath`, then the `run` line again |
| The first admin's password | `admin` / `admin` at the first start; `-e STOREYPATH_ADMIN_PASSWORD=…` to start with another |
| Let others on your network use it | publish the port on all interfaces: `-p 8080:8080`, and add them on the Users page. They reach it by the machine's address or name: give it with `-e STOREYPATH_ALLOWED_HOSTS=studio.example,192.168.1.20` so it is on the certificate (Studio also refuses requests addressed to names it does not know, so a web page cannot reach it through DNS rebinding) |
| Your organization's certificate | mount it and pass it: `-v /etc/studio-tls:/tls:ro ghcr.io/storeypath/studio serve --host 0.0.0.0 --port 8080 --data /data --cert /tls/cert.pem --key /tls/key.pem` |
| Behind a proxy that speaks HTTPS | `… serve --host 0.0.0.0 --port 8080 --data /data --http --secure-cookies --allowed-host studio.example --trusted-proxy 10.0.0.5` (the proxy's address or network; or `-e STOREYPATH_TRUSTED_PROXIES=…`). The proxy must pass who is asking in `X-Real-IP` (nginx: `proxy_set_header X-Real-IP $remote_addr;`): Studio refuses calls through it without |
| Users, backups | the person menu (top right): *Users* for admins, *Download a backup*; or `docker exec storeypath storeypath users list`, `docker exec storeypath storeypath backup --out /tmp/studio.backup` |
| Look at the plans with GPU helpers | `-e STOREYPATH_VISION_URL=http://gpu1:8105/v1,http://gpu2:8105/v1 -e STOREYPATH_VISION_KEY=…` (the helpers' key): [Run the GPU helper](#run-the-gpu-helper). Any OpenAI-compatible endpoint that takes images works too |
| Its database | inside, reached over a socket only (no port): `docker exec -it storeypath psql`. Its log: `/data/pg/log` |
| Another database | `-e STOREYPATH_DATABASE_URL=postgresql://user:password@host/storeypath`: your own PostgreSQL 17 with PostGIS 3, in place of the one inside (which then does not start). Optional: Studio needs none |
| Docker Compose | `docker compose -f docker/compose.yml up -d`, the same Studio and volume; `--profile gpu` adds the GPU helper beside it ([docker/compose.yml](docker/compose.yml)) |
| Logs | `docker logs -f storeypath` |

The same image is the command-line tool: `docker run --rm -v "$PWD:/data"
ghcr.io/storeypath/studio views house.dwg`. [The command line](#the-command-line)
lists the commands.

## Users, sharing and backups

Studio keeps who may use it in its database, with the projects: the accounts, who
each project is shared with, the sessions and an audit log. Everyone logs in.

- **Roles.** An *admin* adds users, sets their role, disables them or gives a new
  temporary password, sees and does everything, and may give a project another owner.
  An *engineer* creates projects and opens packages and project files as new ones,
  and owns what they make. A *user* sees only what is shared with them. An admin may
  let anyone download backups (`backup`) or change the item types (`catalogue`).
- **Sharing.** A project's owner shares it from its page (*Share…*): a person gets
  *view*, *edit* or *share* on the whole project, one of its buildings or one of its
  floors. A grant on the project covers its buildings and floors, ones added later
  too; on a building, its floors. Someone with *share* on a building shares within
  that building only. A person who may see only some floors sees those alone: on the
  project page, in Review (read only with *view*: no tools, a *View only* badge), in
  3D, and in the drawing (only their floor's part of a sheet).
- **First start.** With no users, Studio makes the first admin: `admin`, password
  `admin` (or `STOREYPATH_ADMIN_PASSWORD` when that is set). Anyone changes their own
  password in the person menu; passwords have no rules: each person's to choose.
- **HTTPS.** `storeypath serve` speaks HTTPS: with a certificate it makes itself in
  `<data>/tls/` for the names it is reached by (browsers warn once; it prints the
  fingerprint), or the organization's (`--cert`, `--key`); `--http --secure-cookies
  --trusted-proxy <its address>` behind a proxy that does the HTTPS, which must pass
  who is asking in `X-Real-IP` (Studio refuses calls through it without). Plain
  `http://` to its port is redirected.
- **Command line**, on the data folder, also while Studio runs: `storeypath users add
  NAME --role admin|engineer|user [--capability backup]` (asks for the password
  twice; `--password-stdin` for scripts), `users list`, `users passwd`, `users disable`
  / `enable`, `users role`.
- **Backups.** *Download a backup* (admins, and whoever may) or `storeypath backup`
  gives Studio's whole database as one file (a PostgreSQL dump, in its custom
  format): the projects with their drawings and exports, item types, the accounts with
  their passwords' hashes, sharing and the audit log (no session), so the `backup`
  capability hands those over too. Studio's certificate is not in it: a restored
  Studio makes a new one (browsers warn once). `storeypath restore` loads a backup
  into an empty Studio. The image holds `pg_dump`, `pg_restore` and `psql` (17) for
  doing it by hand.

Every setting, the audit log, and what each call of Studio's API needs:
[studio/README.md](studio/README.md#users-sharing-and-backups).

## Offline images: Studio and the GPU helper

Both images hold everything they need. Nothing is downloaded when they run: no
models, no telemetry, no map tiles, and the 3D view's libraries are served by Studio
itself. The models are fetched once, **before** the build, by scripts that check
their checksums, and baked in.

| | Studio | GPU helper (optional) |
|---|---|---|
| Built from | [docker/Dockerfile](docker/Dockerfile) | [docker/gpu-helper/Dockerfile](docker/gpu-helper/Dockerfile) |
| Published | `ghcr.io/storeypath/studio`, amd64 and arm64, by [the release workflow](.github/workflows/release.yml) for each version tag | not published: build it yourself (amd64) |
| Models fetched first | `docker/fetch-models.sh`: Qwen3.5-4B (2.7 GB) | `docker/fetch-vision.sh`: Gemma 4 31B, 4-bit (about 18 GB) and its image encoder (about 1 GB) |
| Inside | Studio; its database, PostgreSQL 17 with PostGIS 3; LibreDWG's `dwg2dxf` for DWG; llama.cpp's `llama-server` for the CPU (it picks the fastest CPU code at start); the language model; Node.js for pre-built 3D; the viewers | llama.cpp's `llama-server` built for CUDA (A100/A30, A10/A40/RTX 30, L4/L40/RTX 40, H100/H200, Blackwell) with NVIDIA's CUDA runtime, and the vision model: nothing else |
| Starts | its database, then Studio; the language model loads in the background | the vision model on the GPU (a minute or two); Studio uses it once it answers |

### Build the Studio image

```sh
docker/fetch-models.sh                                   # once: the language model (2.7 GB, checksum-verified)
docker build -f docker/Dockerfile -t storeypath/studio .
```

`docker/fetch-models.sh 2B` and `--build-arg MODEL=Qwen3.5-2B-Q4_K_M.gguf` build a
smaller image with a smaller model ([The language model](#the-language-model)).

### Build the GPU helper

On any machine with internet (an Apple Silicon Mac builds it too, through Docker
Desktop's x86 emulation, in an hour or two):

```sh
docker/fetch-vision.sh                                    # once: 19 GB, checksum-verified
docker build --platform linux/amd64 -f docker/gpu-helper/Dockerfile -t storeypath/gpu-helper .
```

llama.cpp is compiled for every GPU generation from A100 on by default;
`--build-arg CUDA_ARCHITECTURES="80-real;90-real"` builds for A100 and H100 alone,
which is quicker, and `--build-arg JOBS=4` limits how many compile at once.

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

### Run the GPU helper

On the GPU machine (Linux, Docker, the NVIDIA Container Toolkit, NVIDIA driver 525 or
newer), with a key of your own that Studio will send:

```sh
docker load -i storeypath-gpu-helper.tar.gz
docker run -d --name storeypath-gpu --gpus all -p 8105:8105 \
    -e STOREYPATH_HELPER_KEY=<a long random key, e.g. from openssl rand -hex 32> storeypath/gpu-helper
docker logs -f storeypath-gpu    # the model loads in a minute or two
```

Then tell Studio where it is, with the same key:

```sh
docker run -d --name storeypath -p 127.0.0.1:8080:8080 -v storeypath:/data \
    -e STOREYPATH_VISION_URL=http://gpu-machine:8105/v1 -e STOREYPATH_VISION_KEY=<the same key> \
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
| Several helpers | one on each GPU or machine (`--gpus '"device=0"' -p 8105:8105`, `--gpus '"device=1"' -p 8106:8105`, …), all with the same key, and Studio given them all: `STOREYPATH_VISION_URL=http://gpu1:8105/v1,http://gpu1:8106/v1,http://gpu2:8105/v1`. It spreads each floor's rooms over the helpers that answer, leaves out one that fails, and tries it again a while later |
| The key | every call but `/health` needs it; the helper does not start without one (`-e STOREYPATH_HELPER_OPEN=1` serves without, on a network nothing else can reach) |
| HTTPS | mount a certificate and its key: `-v /etc/helper-tls:/tls:ro -e STOREYPATH_HELPER_CERT=/tls/cert.pem -e STOREYPATH_HELPER_CERT_KEY=/tls/key.pem`, and give Studio `https://…`. Studio checks the certificate: `STOREYPATH_VISION_CA` (a file mounted into Studio) for one your organization made, `STOREYPATH_VISION_INSECURE=1` not to check a self-signed one on a trusted network. Without, the helper speaks plain HTTP: keep it on a trusted network, or behind a proxy that speaks HTTPS |
| Beside Studio, on one machine | `docker compose -f docker/compose.yml --profile gpu up -d`, the key in `docker/.env` ([docker/compose.yml](docker/compose.yml)) |
| Its log | `docker logs storeypath-gpu` |

Studio works the same without a helper, and a project read with one reads the same
again without (the answers are kept with it). The helper's engine is llama.cpp; vLLM
may replace it, as measurements decide ([docker/gpu-helper/Dockerfile](docker/gpu-helper/Dockerfile)).

### Symbol spotting: research use only

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

Then, back in `storeypath/studio`, fetch the model once and start Studio with it:

```sh
../docker/fetch-models.sh                            # 2.7 GB, checksum-verified
export STOREYPATH_MODELS="$PWD/../docker/models"
uv run storeypath serve --data ~/storeypath --open
```

Studio says what it found as it starts: `language model: Qwen3.5-4B-Q4_K_M; DWG:
yes` (and the vision model, when one is set), its address (`https://127.0.0.1:8080`,
with a certificate it made itself: the browser warns once) and, the first time, the
link to make the first admin. `--http` serves plain HTTP, for development on this
computer. Add the `export` lines to your shell
profile to keep them. To update, `git pull`, then `uv sync`. `uv run storeypath demo
demo/` builds a sample project to try; [studio/](studio) has every command and
setting. Also, as you need them:

- **Vision**: `uv sync --extra vision`, and `STOREYPATH_VISION_URL` set to a vision
  model's endpoint. To serve Gemma 4 yourself on an NVIDIA GPU, fetch it with
  `docker/fetch-vision.sh` and run a CUDA build of `llama-server` with it and its
  `--mmproj`, as [docker/gpu-helper/start.sh](docker/gpu-helper/start.sh) does (or
  run the GPU helper's image).
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
| Disk | 8 GB: the image is about 3 GB to download, 7 GB unpacked | plus your drawings |
| GPU | none for Studio: the language model runs on the CPU | for the GPU helper: see below |
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

**The GPU helper** needs an amd64 Linux machine with an NVIDIA GPU of a generation it
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

The GPU helper runs Gemma 4 31B (4-bit) with llama.cpp on the GPU; any
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
`validate`, `review` (the review editor), `serve` (the web app: `--allowed-host`,
`--cert`/`--key`, `--http`), `users` (`add`, `list`, `passwd`, `disable`, `enable`,
`role`), `backup` and `restore`, `private` and `words` (privacy), `demo`. Each, one line apiece:
[studio/README.md](studio/README.md#commands).

```sh
storeypath views house.dwg                                   # the plans on the sheets, and their floors
storeypath convert house.spproj                              # read the drawings; keeps existing IDs
storeypath export house.spproj --building VILLA -o villa.storeypath
```

## For other systems

- **The package format**: [spec/FORMAT.md](spec/FORMAT.md) (format 0.8) and JSON
  Schemas in [spec/schema](spec/schema): one building per package, plain JSON,
  GeoJSON and CSV in a ZIP, readable without our code. Each export says what was
  added, changed and retired; `objects.csv` lists every ID with its parents;
  `navigation.json` is the building's walking network, with the rule every reader
  finds the same way by.
- **Go**: [go/](go) reads and validates packages, and finds the way in a building,
  standard library only.
- **Viewers** to embed: [viewer/](viewer) (the 3D world, a map view) and
  [viewer/svg](viewer/svg) (a 2D plan with no WebGL, for any machine).
- **Conformance**: [spec/conformance](spec/conformance) holds packages that every
  reader must read the same way, with items, a building moved on the map and a desk
  carried to another building, and ways on them that every reader must find the same.
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
object code on every floor they serve when Studio finds them, and share one *stack*
however they were drawn. Items are the exception that proves the
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

## Licence

StoreyPath is [Apache License 2.0](LICENSE). The images also hold, each under its own
licence (all in `/usr/share/storeypath/licenses`): PostgreSQL (PostgreSQL Licence)
and PostGIS (GPL-2.0-or-later), as Debian packages, run as separate programs;
LibreDWG's `dwg2dxf` (GPL-3.0-or-later, run as a separate program; its exact source
is in the image), llama.cpp (MIT), the Qwen3.5 model weights (Apache-2.0), three.js
(MIT), N8AO (CC0-1.0), MapLibre GL JS (BSD-3-Clause), JSZip (MIT) and Node.js (MIT). The GPU helper
holds llama.cpp (MIT) with OpenSSL (Apache-2.0), the Gemma 4 model weights
(Apache-2.0) and NVIDIA's CUDA runtime and cuBLAS (CUDA Toolkit EULA,
redistributable). SymPoint-V2 is never in an image unless you fetch it yourself
before building, for research use only.
