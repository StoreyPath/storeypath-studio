# How Studio reads a drawing

This is the procedure StoreyPath Studio follows, from the file an architect hands
over to the package other systems read. Each step says what it produces, what is
decided by rules and what by a model, and what a person checks. The code is in
[studio/src/storeypath/](../studio/src/storeypath); the file behind each step is
named in its heading.

- [Who decides what](#who-decides-what)
- [At a glance](#at-a-glance)
- [1. Opening the file](#1-opening-the-file-cadpy)
- [2. Taking out private information](#2-taking-out-private-information-privacypy)
- [3. Working out the units](#3-working-out-the-units-cadpy-readingpy)
- [4. Finding the plans on a sheet](#4-finding-the-plans-on-a-sheet-sheetspy-levelspy)
- [5. Choosing which plan is which floor](#5-choosing-which-plan-is-which-floor-a-person)
- [6. Lining the floors up](#6-lining-the-floors-up-sheetspy)
- [7. Reading a floor](#7-reading-a-floor-convertpy)
- [8. Keeping the IDs](#8-keeping-the-ids-convertpy)
- [9. Review](#9-review-a-person)
- [10. Export](#10-export-exportpy)
- [With a GPU and without](#with-a-gpu-and-without)

## Who decides what

Three kinds of reader take part:

- **Rules**: code that measures what is drawn (lines, arcs, texts, their sizes and
  where they stand). They always run and give the same answer every time.
- **The language model** (Qwen3.5-4B, run by llama.cpp inside the container, on the
  CPU; on the GPU in the GPU image): reads texts the rules do not know. It is asked
  short, closed questions (which of StoreyPath's room types does this label name?
  which floor does this title show?) and its answer is held to a JSON schema of
  fixed choices, so it can suggest but never invent.
- **The vision model** (only when one is set: Gemma 4 31B in the GPU image, or any
  OpenAI-compatible model that takes images, served elsewhere): looks at the plan
  as printed, one room at a time, and makes the calls a person makes at a glance.

Code keeps the exact geometry and the IDs; the models only answer questions about
it. Every answer a model gives is stored in the project (texts in `readings`, rooms
in `vision`, by the room's shape), so converting again asks only about what
changed, and gives the same result with or without the model. A person's
correction always wins over any of them.

## At a glance

```
drawing (DWG or DXF)
 │
 1  open it ................ DWG to DXF with dwg2dxf; broken entities repaired     rules
 2  take out private ....... title blocks, names, contacts, hidden file data       rules + model; a person chooses
 3  units .................. door swings, dimensions, text sizes, notes            rules + language model (notes)
 4  find the plans ......... wall groups with doors or room names, their titles    rules + language model (titles)
      floors and heights ... ground 0, basement -1, roof on top; levels            rules + language model
 5  choose the floors ...... which plan is which floor of which building           a person
 6  line the floors up ..... each plan moved onto the walls of the floor below     rules
 7  read each floor
      a  layers ............ what each layer holds, from what is drawn on it       rules + language model
      b  spaces ............ room outlines, else walls → envelope → regions        rules
      c  doors, windows .... swings, blocks, glazing, gaps, tags                   rules
      d  open areas ........ divided between their labels: spaces or zones         rules
      e  names, types ...... labels; types by rule, else by the language model     rules + language model
      f  fixtures .......... unnamed rooms typed by what is drawn (research only)  SymPoint-V2
      g  vision ............ each room looked at: a room or not, its type, merged  vision model
      h  door sizes ........ sill and height from the schedule of openings         rules + a model
      i  your edits ........ walls, dividers, doors, spaces drawn in review         a person, kept
 8  keep the IDs ........... matched to the last reading: kept, new, retired       rules
 9  review ................. what needs a look is listed; a person corrects it     a person
10  export ................. one building per package, with what changed           rules
```

In the web app, steps 1 and 2 run when a drawing is added, steps 3 and 4 when its
plans are found (at once for a single drawing added, else with *Find plans*), steps
6 to 8 when the chosen plans are added as floors, and steps 7 and 8 again whenever a
floor is read again (*Re-read drawing*, or after an edit in review). On the command line they are
`views`, `add-floor`, `align`, `levels` and `convert` (see
[studio/README.md](../studio/README.md#commands)).

## 1. Opening the file (`cad.py`)

- **DXF** is read with [ezdxf](https://ezdxf.mozman.at/). **DWG** is first converted
  to DXF by LibreDWG's `dwg2dxf` (in both images; outside them, `dwg2dxf` or the ODA
  File Converter if installed). A conversion still running after 10 minutes is
  stopped, with a note to save the drawing as DXF.
- **A damaged file is read as far as it can be.** ezdxf's auditor fixes or takes out
  what is broken (an insert of a block the drawing does not define, a spline with too
  few points, a hatch with a broken boundary), as CAD programs do on opening, so one
  broken entity does not stop a floor. What was taken out, or damage `dwg2dxf`
  reported, is shown as a warning on the floor.
- Plans and rooms are read from the model space.

## 2. Taking out private information (`privacy.py`)

A drawing carries more than the building. When a drawing is added in the web app
(the box *Remove private information* is ticked by default), Studio finds, before
the drawing enters the project:

- **title blocks**: two or more title-block words (CLIENT, OWNER, CONSULTANT, DRAWN
  BY, DRAWING NO, STAMP…) beside the long ruled line that parts the title column or
  strip off the sheet: the column or strip and everything in it; else the small
  closed box round those words;
- **names and numbers anywhere**: names with a title (Mr, Eng, Sheikh, السيد…),
  phone numbers, emails, web addresses, permit, plot, licence and registration
  numbers, and block attributes named for them;
- **images and embedded objects** (logos, signatures, scans) and the paper-space
  sheets;
- **hidden file data**: last saved by, project name, custom properties, hyperlinks,
  the paths of images and external references;
- **with a model**, whatever else it reads as private in the texts left: a name with
  no title, a company, an address. Only the private part of a text goes (*OFFICE -
  KHALID* keeps *OFFICE*), and a text the rules read as a room's name is never taken
  out whole. The vision model is asked, in words, when one is available; else the
  language model; else this is done by rule alone.

A person sees what was found, grouped, and unticks anything to keep (a child's
name on their room, say), or does not add the drawing at all. Only the cleaned copy
is kept, named `drawing-1.dxf`, `drawing-2.dxf`…: the file as sent, and its name,
are not. **Words**, beside each drawing, lists every word and string left in it to
look through. Plans, sections, room labels, dimensions and schedules stay.

On the command line `storeypath private` makes the same copy and `storeypath words`
lists the words; `add-floor` uses a drawing as it is given.

## 3. Working out the units (`cad.py`, `reading.py`)

A drawing's unit setting is often missing or wrong. Studio tries each unit (mm, cm,
m, in, ft) against what is drawn, the way a person checks a plan:

| Clue | Makes sense when |
|---|---|
| Door swings: quarter arcs with a leaf (a straight line from the hinge, as long as the arc's radius) | the leaf is 0.55 to 1.3 m. A basin's rounded corners have no leaf and do not count |
| The typical linear dimension | it is 0.3 to 8 m: the size of rooms, walls and openings |
| The typical text height | it is 0.05 to 1.5 m: 2 to 5 mm on paper at 1:20 to 1:300 |
| A note stating the units (*ALL DIMENSIONS ARE IN MM*) | read by rule in plain English, by the **language model** in other languages and wordings |

The units are orders of magnitude apart, so the wrong ones make no sense. The doors
come first, then the dimensions, the note and the text sizes (a note says what the
dimensions are labelled in, which is not always what the plan is drawn in). Studio
is **sure** when nothing disagrees and the doors, the dimensions or a note show the
units; otherwise it says it is not sure and a person chooses (the *Units* list in
the web app finds the plans again in the units chosen; `--units` on the command
line). The units are kept with each floor, so the project converts the same way
again.

## 4. Finding the plans on a sheet (`sheets.py`, `levels.py`)

A sheet set often has every floor plan side by side with elevations, sections, a
site plan and title blocks, sometimes each pasted in as a block.

- **The plans.** Walls are looked for on every layer: pairs of parallel lines a
  wall's thickness apart, grouped by nearness. A group with door swings or room
  names inside it is a plan; tables, frames and elevations have neither. A sheet
  frame drawn close round a plan is dropped from it. A sheet pasted in as one block
  (or a bound reference holding several plans) is taken apart, so each plan in it is
  read on its own.
- **Their titles.** The most title-like text just under each plan (or inside its
  bottom edge), about as wide as the plan.
- **What each title says.** Plain English titles are read by rule: *ground floor
  plan* is floor 0, *first floor plan* floor 1, *basement* -1, *second basement*
  -2, *roof*. The rest goes to the **language model**: other languages, a title that
  names a building (*OUT KITCHEN FLOOR PLAN*), or several floors (*FIRST & SECOND
  FLOOR PLAN*). It says the drawing's kind (floor plan, roof plan, site plan,
  elevation, section, detail, schedule, other), its floor number, and the building
  when the title names one other than the main building (an annex, a guard room).
- **Heights.** A plan has no heights, but the sections and elevations beside it do:
  *+3.65 FIRST FLOOR SLAB LVL*, *+6.95 ROOF SLAB LVL*, *+8.65 PARAPET LVL* give each
  floor's height and the parapet round the roof (plain English by rule, other
  languages by the language model). Where the sections give none, the level each
  plan marks in its rooms (*+0.45 FFL*) does. With neither, floors are 3.5 m high.

What it produces: every drawing on the sheets with its title, kind, floor number,
size and a thumbnail, and the floor heights.

## 5. Choosing which plan is which floor (a person)

In the web app each plan found is a card. Tick it to add it as a floor, and choose
its location and building (one the project has, or a new one), its floor number,
name, height and parapet. To start with, Studio ticks one plan per floor of a
building (the largest: a second *ground floor plan* on a sheet is often an
outbuilding's), puts a roof plan above the highest floor, and gives a drawing of a
single floor whose title names none the building's next floor. Two plans cannot be
the same floor of one building. A plan on a floor the building has already
replaces that floor's drawing only when *Replace its drawing* is ticked; its rooms
then keep their IDs.

On the command line: `storeypath views` lists the plans, and `add-floor --view`
picks one by its number or part of its title.

## 6. Lining the floors up (`sheets.py`)

Plans drawn side by side are metres apart. Floors share columns and outside walls,
so each new floor is moved to where its walls overlap most with its building's
lowest floor (the main body of each plan's walls, laid on a grid and compared).

- A floor read before (a new drawing of it) lines up on its own walls instead, so
  its rooms come back where they were and keep their IDs.
- A floor from another drawing is moved only when at least a quarter of its walls
  then line up; otherwise it stays where its drawing puts it (one drawing per floor
  usually shares the building's coordinates).
- A floor converted already is never moved: its rooms' IDs stand on where they are.
- A new building whose drawing would put it on top of another of its site (or very
  far off: a drawing with its own origin) is placed beside the others on the site
  plan.

## 7. Reading a floor (`convert.py`)

### a. What each layer holds (`analyse.py`, `profile.py`)

Layer names are a hint at best (`jun wall`, `ELE4`, `0`). With the default `auto`
profile each plan is measured layer by layer, and each layer is read as what is
drawn on it:

- **walls**: long pairs of parallel lines a wall's thickness apart (or arcs round one
  centre) that are part of the plan's main wall frame; furniture and cars stay in
  small separate pieces;
- **glazing**: thin pairs in line with the walls (a layer named for windows is
  glazing, however thick its frames are drawn);
- **doors**: quarter-circle swings as wide as a door;
- **columns**: small repeated squares or circles;
- **room outlines**: closed shapes each holding at most one room's name, together
  holding a good share of them;
- **labels**: texts that name rooms (by rule, else by the **language model**, which
  is shown a sample of each layer's unknown texts), and any layer named for room
  tags, whose room codes (*RM-GF-33*) are not names but say which room is which;
- **not walls**: lines drawn dashed (beams, things overhead) and evenly spaced lines
  (stair treads, tiles, tables).

Each plan is read on its own: one architect's layer can hold door leaves on one
sheet and roof parapets on another. What each layer was read as is listed per floor
(*How it was read*, on the project page). A YAML profile, the built-in `ncs` or one
of your own, can be given instead.

### b. Spaces (`extract.py`, `walls.py`)

**From room outlines**, when the drawing has them: tiny and repeated outlines are
dropped; a room drawn twice (to the walls' middle and to their faces) is one room;
an outline round others is the floor's outline, or a room with the others cut out of
it (an open office round two shafts).

**Otherwise from the walls**, as a person reads a plan:

1. Everything on the wall, window and column layers becomes one wall mass, however
   it is drawn: single or double lines, fills, straight, diagonal or curved. The X
   marks drawn across voids and lift cars are left out, and so are strays far from
   the plan (a line at a georeferenced drawing's origin).
2. The gaps in it are closed: a door along the closed position of its swing (both
   leaves of a double door), or across its block; glazing and door leaves drawn
   closed carry the wall across their gap; where a wall stops and another faces its
   end within 1.2 m, the gap is a doorway, closed and recorded as an opening.
3. Round the outside, gaps up to 4 m are spanned by the building's envelope, so a
   room behind a door that is not drawn is kept (and listed for review).
4. Each region left enclosed is a space. Areas that are the outside around the
   building (the garden inside a plot wall) are left out.

Spaces a person drew in review, where the drawing encloses none (a colonnade between
columns), are added.

### c. Doors, windows and openings (`extract.py`, `walls.py`)

- **Doors**: door blocks with a swing, a door's name or a sliding door's shape, and
  doors drawn as loose lines, by their swing; each with its width, its span from
  jamb to jamb and its leaves (which side it hinges on and which way it opens).
  Basins, baths and cars put on a door layer are not doors.
- **Windows**: glazing in the walls. **Openings**: doorways, ways through with no
  door.
- **Tags** (*D4*, *W12*, *SD2*) say which an opening is: a door tag makes a door of
  an opening drawn as glazing or left open; glazing between two rooms at a door's
  width with no window tag is taken as a sliding door.
- **A lift** with no way in drawn (its doors are often left out of plans) is given
  one on the wall it shares with a hall, lobby or corridor.

### d. Open areas: spaces and zones (`extract.py`, `split.py`)

A **space** is what walls, doors and windows enclose. A **zone** is a part of a space
used for one thing with no wall between it and the rest (a majlis and a dining area
in one hall). Zones divide their space exactly.

A space that holds the labels of several rooms is divided where it is narrowest
between them, straight across and square to its walls (the shortest cuts, at most
6 m in all). Across a gap in a wall (a doorway with no door drawn) the parts become
separate spaces joined by an opening; across open floor, with no wall at all, the
space stays whole and is divided into zones. Each part is listed for review. Lines a
person draws as dividers in review make zones the same way.

### e. Names, numbers and types (`extract.py`, `reading.py`, `llm.py`)

- **Name and number.** The label inside a space gives its name and number. A line
  that is a number on its own is the number (*MEETING ROOM 2* over *301*: the name
  is *MEETING ROOM 2*); room codes in parts (*B-12*, *1.02*, *2F-101*, *RM-GF-33*)
  are numbers. Texts that never name a room are left out: levels (*+0.45 FFL*), door
  and window tags (*D1*, *W4*), *UP* and *DN*, scales, sizes, air-conditioning units
  (*SAC UNIT*, *FCU-1*). A tag alone does not divide a space or give its number when
  the space holds a real label.
- **What the drawing says.** The text written in the space, exactly as written, is
  kept as its **drawing label** (`drawing_label` in the package): never changed in
  review, a key other systems can match their records on beside the ID.
- **Type.** First the profile's rules (*OFFICE*, *CORRIDOR*, *WC*…). A name they do
  not know goes to the **language model**, which answers from StoreyPath's fixed list
  of types: *F.DINNING* is a dining room, *EE.RM.* an electrical room, *مجلس رجال* a
  living room, *DORMITORIO* a bedroom, *VOID* is open to the floor below. A type it
  gave is marked `model`.
- **Stairs.** A space mostly taken up by flights of treads is stairs, whatever a
  model guessed; a name read from the drawing still decides.
- **Divided spaces** are named by their zones and take the type of their largest.

### f. Fixtures: research use only (`symbols/`)

Only when SymPoint-V2 was fetched (`docker/fetch-symbols.sh`) and is installed: a
network trained on floor plans spots the fixtures drawn in rooms that have **no
type**, and only symbols found with a score of 0.8 or more count. A toilet and a
bath make a bathroom, a toilet alone a WC, a stove or a fridge a kitchen, a washing
machine a laundry, a bed a bedroom, a flight of stairs filling the room a stair
room. Each room typed this way is marked `symbols:…` and listed for review. What it
finds is kept with the floor and found again only when the drawing, the part read
or its units change.

SymPoint-V2 is not part of StoreyPath: its repository states no licence and its
weights were trained on non-commercial data (FloorPlanCAD, CC BY-NC). It is **for
research only**, and images built with it must not be published or sold.

### g. Vision: looking at each room (`vision.py`)

When a vision model is set (or answers kept from an earlier reading), every room
(each zone, and each space not divided into zones) is shown to it as a person sees
the print: the plan around it, drawn as printed, the room outlined in red. It answers two questions: is the outline exactly
one room, two or more rooms merged, only part of a room, or not a room; and what
kind of room is it.

- **Not a room**, and no name: set aside (deleted, kept with its ID for a person to
  restore). So is one that also wraps round the other rooms (a yard named after the
  steps in it). A room with a name that does not look like one gets a note.
- **No type yet**: it gets the type it looks like, marked `vision`. A type from the
  rules or the language model is never replaced.
- **Merged rooms**: each line that may divide the room (a line drawn across it, a
  wall carried on past where it stops) is shown in blue, its sides lettered A and B,
  and the model says what each side is. Where they differ, code cuts exactly along
  the line; then each piece is looked at on its own, and a cut stays only where both
  pieces are rooms of different kinds (a strip along the windows is part of the room
  it was cut from). There is no wall along a cut: the pieces are zones of the one
  space.
- **Only part of a room**: a note for review.
- **Ways through**: an area set aside as no room that doors join to several rooms (a
  corridor round a wing, too winding for one picture; a hall the rooms open onto) is
  kept, as a corridor when narrow, else an open area, with a note to check it.
- **The building's edge**: areas set aside as no room that reach the edge of the
  plan (a yard inside the line marking the land, the pool in it) are taken out of
  the floor's outline and walls. Rooms are never cut.

Every decision is marked `vision` in the review notes. Questions are asked two at a
time by default (`STOREYPATH_VISION_PARALLEL`). Answers are kept by the room's shape,
so reading the floor again asks only about rooms that changed.

### h. Door and window sizes (`schedule.py`)

The schedule of openings (a table of tags with width, height and sill) nearest the
plan gives each tagged door and window its sill and height. Headers are matched as
drawings spell them (*HIEGTH*, *SILL HIGHT*), sizes read in metres or millimetres as
the table shows. With a model, each row is read as a person reads it (a sill written
under the wrong column, an arched window's *1200+ R=600*): the vision model asked in
words where one runs, else the language model; a row is kept when every size it
gives is a number in that row. Otherwise the rules read the table.

### i. What a person drew (`convert.py`)

Walls, dividers, spaces, doors, windows and openings drawn in review, and the
drawing's openings given another size, are applied at every reading of the floor,
so they survive every re-read and every revised drawing.

## 8. Keeping the IDs (`convert.py`)

Every object gets an ID that says where it is (`K7Q2XM-RUH-HQ-F02-0142`) and keeps
it when a revised drawing is read again:

- **Kept**: each space or zone is matched to one from the floor's last reading that
  it overlaps by half or more (intersection over union), or by a tenth when its room
  number is the same; the best pairs first, one to one. Zones, and spaces with none,
  are matched first, so a room a reading once cut in two, now one space with two
  zones, hands its IDs to the zones. Doors, windows and openings keep theirs by
  position, within 0.5 m; windows match only windows.
- **New**: anything unmatched gets the next code of its building. Lifts and stairs
  that line up with one on another floor of the building take its code
  (`…-F01-0023`, `…-F02-0023`), so they link the floors vertically; a lift a person
  typed in review counts.
- **Retired**: an object no longer in the drawing is retired, and its ID is never
  issued again.
- **Held back.** A reading that finds no spaces on a floor that has some, or that
  would retire more than half of them, is most likely a bad read (layers renamed,
  wrong units, a broken file). It is not applied: the floor keeps its rooms and IDs,
  and the warning says why. If the drawing really changed that much, apply it on
  purpose: *Read anyway* in the web app, `storeypath convert --force` on the
  command line.

A person's corrections (type, name, number, capacity, deleted) are kept by ID and
applied again after every reading.

## 9. Review (a person)

The review editor draws each floor over its drawing, as printed or as its lines, and
lists the spaces and zones that need a look, with why:

- no type, or no name or number;
- the labels of several rooms in one space (a doorway with no door block, or a
  missing wall), a part Studio divided off, a gap to the outside with no door or
  window drawn, stairs typed from their treads, a space drawn in review;
- what a model decided that a person should confirm: a type from vision or from
  symbols, a room that looks merged or only part of one, a way through kept.

Rooms vision set aside as no room are off the list: they show with *Show deleted*,
to be restored if they are rooms.

A person corrects the type, name or number (or saves it as it is, which takes it
off the list), deletes what is not a room, draws the walls, dividers, doors, windows,
openings and spaces the drawing leaves out, resizes openings, and places furniture
and equipment. Once corrected or accepted, only a missing type keeps a space on the
list. See [What you can do in Studio](../README.md#what-you-can-do-in-studio).

## 10. Export (`export.py`)

A package holds **one building**: its floors, spaces, zones, openings and items with
their IDs, and its location ([spec/FORMAT.md](../spec/FORMAT.md), format 0.7).

- It is written beside where it goes, checked against the format, and entered as an
  export only when it is valid; one that is not valid is not kept.
- `changes.json` lists what was added, changed and retired in that building since
  the last package that held it, compared in the building's own frame: moving a
  building on the map lists the building as changed, nothing in it.
- With Node.js at hand (it is in both images), each floor is also built in 3D ahead
  of time (`world/<floor-id>.glb`), so a slow machine shows it without building it.

## With a GPU and without

| | Without a GPU (CPU image, or no vision model set) | With a GPU (GPU image), or a vision model served elsewhere |
|---|---|---|
| Opening, units, plans, lining up, walls, spaces, doors, IDs | rules | rules |
| Room names, sheet titles, unit notes, level labels | rules, then the language model on the CPU | the same; in the GPU image the language model runs on the GPU |
| Private texts in a drawing, schedule rows | rules and the language model | rules and the vision model, asked in words |
| Is it a room? Merged rooms divided into zones; corridors kept as ways through | not done: a person checks the review list | the vision model, each decision listed for review |
| A type for a room with no name | stays without one, listed for review (or symbols, research only) | the type the vision model sees |

Without any model at all (`convert --no-model --no-vision`), everything still works
on the rules alone, with more left for a person to type in review. Answers kept from
an earlier reading are used either way.
