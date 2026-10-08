# StoreyPath Studio 2: design

Studio becomes a professional, multi-user tool for making and editing a workplace's
drawings, used by an organization's own engineers or by contractors. It stays
**standalone**: one image runs it with everything it needs, its own database
included, and it depends on no other system. Work moves between people and systems
as files (project files, `.storeypath` packages), as before.

Decided with the owner (2026-10-08): PostgreSQL + PostGIS; FastAPI on uvicorn; one
editor per floor at a time; the GPU model server as a separate, optional image.

## 1. What runs

| Image | Holds | Needs |
|---|---|---|
| `storeypath/studio` | Studio (FastAPI on uvicorn, HTTPS by default), its own PostgreSQL 17 + PostGIS 3 (started by the image, data in `/data/pg`, reached over a Unix socket only), the small text model on the CPU (llama.cpp), LibreDWG, Node.js for 3D baking | nothing else: `docker run -v storeypath:/data -p 8080:8080 storeypath/studio` |
| `storeypath/gpu-helper` (optional) | the vision model server, OpenAI-compatible, with its model baked in (engine: llama.cpp or vLLM, chosen by measurement) | a GPU; an API key that Studio sends |

- `STOREYPATH_DATABASE_URL`: use an external PostgreSQL (with PostGIS) instead of the
  image's own. Optional; never required.
- `STOREYPATH_VISION_URL`: one or more helpers (`https://gpu1:8105/v1,https://gpu2:8105/v1`),
  with `STOREYPATH_VISION_KEY`. Studio spreads rooms across the helpers it can reach
  and leaves out one that fails, until it answers again.
- Run without Docker (development): any PostgreSQL 17 with PostGIS; the dev one is a
  container on 127.0.0.1:55470 (55432 and 5433 are other systems' on this Mac).

## 2. Data

PostgreSQL is the system of record. Files stay for what is a file: drawings as
sent (`/data/projects/<code>/drawings/…`), the prints made of them, packages
exported. Project files (`.spproj` inside `.storeypath-project`) and packages stay
the way projects and buildings go in and out of a Studio.

Schema `storeypath`, geometry in each building's own frame (local metres, SRID 0):

| Table | Holds |
|---|---|
| `projects` | code (PK), name, created, owner, `next_item_seq`, `version` (bumped on every change) |
| `locations`, `buildings` | as the workspace has them (placement, site position, `next_object_seq`) |
| `floors` | code, name, ordinal, elevation, height, parapet, source drawing (JSONB), outline (geometry), conversion results (method, warnings, layers, walls as drawn, wall thickness, symbols), drawn edits (JSONB), `version` |
| `objects` | spaces, zones, openings: id (PK), floor, kind, detected type and its source, name, number, label, geometry, the rest (connects, parent, zones, span, swings, sill, height, tag, issues, detected_ignored) as JSONB, status, created/retired |
| `overrides` | a person's corrections by object: type, name, number, hidden, ignored, capacity; who and when; `version` |
| `items` | id (PK), type, floor, x, y, rotation, values (JSONB), status, created/retired, `version` |
| `readings`, `vision` | the text model's and the vision model's answers, kept by text and by room shape |
| `exports` | each export record (JSONB), by project and sequence |
| `catalogue` | the organization's item types |
| `users`, `project_access`, `grants`, `sessions`, `audit` | accounts, as in studio.db today |
| `history` | every change: seq, project, floor(s), at, who, kind, targets, before, after, undoes/redoes |
| `floor_locks` | floor (PK), who, session, since, last seen |

`storeypath.db`: a connection pool (psycopg 3), numbered SQL migrations applied in
order at start, and `ProjectStore`:

- `load(code, building=None, floors=None) -> Workspace`: the in-memory project the
  pipeline already works on (conversion, export, bundles stay as they are), cached
  per project and reloaded when its `version` moved.
- Review's changes as small transactions on exactly what they touch (one override,
  one item, one floor's edits), each writing its history row in the same
  transaction and then `NOTIFY storeypath_changes`.
- `save_floor(ws, floor_id)`: a conversion's result for that floor (its objects,
  the floor row, the building's next ID number, new readings and vision answers)
  in one transaction; `save_project(ws)` for opening/merging files.

Moving over: `storeypath db import --data <folder>` brings every project folder
(`.spproj`, drawings) and `studio.db` (accounts) into the database; the first start
does it by itself when the database is empty and the folder has projects. Nothing in
the folder is deleted.

## 3. Many people at once

- **One editor per floor.** The first change to a floor takes its lock for that person
  (that session); others see it live and read-only ("Khalid is editing this floor,
  since 10:20"). The lock goes when they leave the floor or after 15 minutes without
  a change or a heartbeat; an admin can take it over. A change without the lock is
  refused (423, who holds it).
- Project-wide steps (export, reading every floor, placing buildings, opening files
  into a project) take the project's advisory lock; Review on that project waits or
  is told it is busy, as now.
- Row `version`s check every write as a safety net.
- **Live updates:** the server LISTENs once and pushes each change, presence (who is
  viewing, who is editing) and job progress to browsers over Server-Sent Events
  (`GET /api/projects/<code>/events`), filtered by what each person may see. The page
  refreshes what changed and says who changed it.
- **History and undo:** every change recorded with who, before and after; each person
  undoes and redoes their own changes (refused, naming who, when someone changed the
  same thing since); a History panel per floor. (Rules: the stopped JSONL work,
  `history.py`, commit 57c2457.)

## 4. Server

FastAPI routers keep today's URLs. `Gate` becomes a dependency: every route keeps its
permission rule, and a test still fails a route without one. HTTPS by uvicorn with
Studio's own certificate (tls.py) or the organization's; the session cookie, the
X-StoreyPath / Origin / Host checks, body limits, timeouts and security headers as
today. Long steps run in a worker pool and report through the events stream.

## 5. Order of work

| Phase | What | Runs |
|---|---|---|
| A | database: schema, migrations, `ProjectStore`, accounts moved from SQLite, `db import`, Studio and Review on the database; the test suite green on it | in parallel with B and C |
| B | FastAPI server for every route, on Studio/Review/Accounts as they are (their methods are the contract with A) | in parallel |
| C | `gpu-helper` image, API key, several helpers in `STOREYPATH_VISION_URL`, the studio image without the GPU parts and with its own PostgreSQL | in parallel |
| D | floor locks, live updates (LISTEN/NOTIFY → SSE), history and undo/redo with the History panel | after A and B |
| E | performance (profiling; vision engine from the Dell measurements), the demo | last |

Targets: a floor of 900 spaces opens in Review in under 300 ms; a change saves in
under 50 ms; others see it within a second.
