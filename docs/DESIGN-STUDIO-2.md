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

PostgreSQL is the system of record, and a project is entirely in it (owner, 2026-10-08):
its tree, objects, corrections and items, the drawings as sent (the bytes; and those
sent and waiting for a person to say what of them to keep), and each package exported,
the exact bytes that were sent beside its export record (`GET …/exports/<file>`
answers the same file, and the next export lists its changes against what was sent).
A step that needs a drawing as a file (conversion, reading plans, words, prints) is
given one written from the database for as long as it runs, then removed. On disk
there is only what is not data: the cache (`<data>/cache/`: rendered prints, files a
step is working on, model caches), made again when gone, and the server's TLS
certificate. Project files (`.spproj` inside `.storeypath-project`) and packages stay
the way projects and buildings go in and out of a Studio.

Schema `storeypath`, geometry in each building's own frame (local metres, SRID 0). The
GeoJSON is kept exactly as the project has it (JSONB: packages are compared by hashes of
it), and a PostGIS geometry is made from it beside it (generated, GiST-indexed).

| Table | Holds |
|---|---|
| `projects` | code (PK), name, created, `version` (one more at every change) |
| `locations`, `buildings` | as the workspace has them (placement, site position, `next_object_seq`), in order |
| `floors` | id, code, name, ordinal, elevation, height, parapet, source drawing (JSONB), outline (JSONB + geometry), conversion results (method, warnings, layers, walls as drawn, wall thickness, symbols), drawn edits (JSONB), `version` (one more at every change of it or of anything on it) |
| `objects` | spaces, zones, openings: project and id (PK), floor, kind, detected type and its source, name, number, label, geometry, the rest (connects, parent, zones, span, swings, sill, height, tag, issues, detected_ignored) as JSONB, status, created/retired |
| `overrides` | a person's corrections by object: type, name, number, hidden, ignored, capacity, a lift's stack, its floor and wall finish (codes of spec/finishes.json: migration 0004); who and when; `version` |
| `items` | id (PK: an asset's tag, one project's in the whole Studio; migration 0003), project, type, floor, x, y (and a point geometry), rotation, values (JSONB), status, created/retired, who and when, `version` |
| `readings`, `vision` | the text model's and the vision model's answers, kept by text and by room shape |
| `drawings` | project, name, the bytes, size, sha256, the words left in it, who sent it and when; `incoming` for one waiting for a person |
| `exports` | each export record (JSONB) in order, with the package's bytes as sent; a package kept from before records, on its own |
| `catalogue`, `settings` | the organization's item types; what else is the whole Studio's |
| `users`, `project_access`, `grants`, `sessions`, `audit` | accounts, as studio.db had them (a project's owner stays in `project_access`: who a code is shared with is kept before a project is opened and after it is gone) |
| `history` | every change: seq, project, version, at, who, part, kind, floor(s), targets, before, after, undoes/redoes |
| `floor_locks` | floor (PK), who, session, since, last seen: who is editing each floor (§3) |

`storeypath.db`: a connection pool (psycopg 3), numbered SQL migrations applied in
order at start under an advisory lock (as Studio's own role, which owns its database
and is no superuser: the image makes PostGIS before Studio starts), and `ProjectStore`:

- `load(code, building=None, floors=None) -> Workspace`: the in-memory project the
  pipeline already works on (conversion, export, bundles stay as they are), cached
  per project and read again (one statement: the database makes the JSON) when its
  `version` moved.
- Review's changes as small transactions on exactly what they touch (one override,
  one item, one floor's edits), each writing its history row in the same
  transaction and then `NOTIFY storeypath_changes` ({project, floors, seq, version}).
- `save_floor(ws, floor_id)`: a conversion's result for that floor (its objects,
  the floor row, the building's next ID number, new readings and vision answers)
  in one transaction; `save_project(ws)` for whatever else differs (placing,
  floors added, an export with its bytes, files opened into a project or in its place).

Moving over: `storeypath db import --data <folder>` brings every project folder
(`.spproj`, its drawings, its packages), `catalogue.json` and `studio.db` (accounts,
owners, sharing, audit) into the database, once; the first start does it by itself
when the database has no projects and the folder has some (or no users and a
studio.db). Nothing in the folder is deleted.

Backups (`storeypath backup|restore`, `GET /api/backup`, for an admin or whoever has
`backup`): the database is the whole of it — every table as one moment saw it (one
REPEATABLE READ transaction), streamed as gzip'd SQL (COPY blocks psql reads), with no
session and nothing half done; restored only into an empty database. An older Studio's
backup (a .tar.gz of its data folder) is restored by bringing that folder in.

## 3. Many people at once

As built (Phase D):

- **One editor per floor.** A person's change from a page (Review's corrections,
  items, drawn edits, a floor read again, an undo) takes the lock of each floor it
  touches, in the change's own transaction (`floor_locks`: floor, who, session, since,
  last seen; the project's row, held by every change, makes lock takers come one at a
  time). The lock is the person's, whichever of their sessions (the last one is kept):
  their other page is not "someone else". Another person's change is refused, nothing
  saved: 423 with who holds it and since when. It goes when they leave the floor (the
  page tells the server: *Done editing*, another floor, the page closed, with a
  keepalive request), after 15 minutes without a change or a heartbeat (a page's
  stream on the floor, `events?floor=`, keeps it at every heartbeat; the listening
  thread lets stale ones go every 30 s and tells the pages), or by an admin's *Take
  over* (a history row, part `floor`, kind `take over`, with whom it was taken from,
  and an audit entry). Others see it live: a banner on the floor and what changes it
  held (dimmed), a mark when you hold it.
- **Project-wide steps** (adding floors, reading every floor, placing buildings,
  exporting, opening a file into a project, deleting it) hold the project's step lock
  (`store.StepLock`): this Studio's thread lock, as before, and a PostgreSQL advisory
  lock on the project (`pg_advisory_lock(0x5370, hashtext(code))`, on a connection of
  its own while held), so another Studio on the same database waits too. Review's
  changes take it for a moment, as they took the thread lock (1 s, then Busy, 409).
  Steps of Studio's own take no floor lock: they are one at a time with Review's
  changes through the step lock.
- Rows keep their `version`s, raised at every write; changes to one project are made
  one after another (each holds the project's row), and an undo checks that what it
  undoes is as the change left it, so no write is refused on a version.
- **Live updates:** each process LISTENs once (`web/live.py`, from the first stream
  on, again after a pause when the connection is lost) and turns each NOTIFY into
  events on the streams of who may see it: `change` (the history row: who, what in
  words, its page, so the page that made it does not redraw it) once per floor it
  touched, to who sees that floor (a building's or the project's change, to who sees
  it whole); `presence` per floor (who has a page open on it, from the streams; who
  holds its lock), at once when a stream opens and whenever it changes; `deleted`.
  Job progress came that way already; Review and the project page follow jobs on
  their stream (asking `GET /api/jobs/<id>` only when the stream says nothing).
  Review refreshes the floor in place (view, selection, an editor being typed in
  kept; an editor whose object changed shown again) and toasts who did what; its
  header shows who is viewing and who is editing; a project's page, who is on which
  floor.
- **History and undo** (`history.py`, the stopped JSONL work's rules on the
  database's rows): every change recorded with who, before and after, and what says
  it in words as it was then (`label`, `what`, `was`, `now`…). `POST …/undo {floor?}`
  applies the inverse of the person's latest object, item or edit change (on that
  floor) through the same store change (the floor's lock, Busy as any change), as a
  row that `undoes` it; `…/redo` applies an undoing's inverse (`redoes`); a new
  change clears the person's redo; a file opened in place of the project or a
  building is a barrier. Refused (409) when someone else changed the same object, item
  or drawn shape since (naming who, when, what), or when the state is no longer as
  the change left it (read again from its drawing). An item added and undone is
  retired; a drawn edit undone is taken away and the floor read again (a job, as
  drawing does). `GET …/history?floor=&n=` lists rows newest first with who, when,
  the line, whether undone, of the floors the person may see, and what they would
  undo and redo; the History panel shows it and follows changes live.
- **GPU helpers** (admins): the helpers are kept in `settings` (`vision_helpers`:
  url, key, enabled, places at once each), edited on the GPU helpers page with each
  one's state, the models it lists (one serving another model is left out and shown
  so) and a Test that sends a sample room; `vision.VisionModel.configure` replaces
  its helpers without a restart (each Studio reads the setting again before a
  conversion). `STOREYPATH_VISION_URL`/`_KEY`/`_PARALLEL` are where it starts from
  while the database has none.

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
| D | floor locks, live updates (LISTEN/NOTIFY → SSE), history and undo/redo with the History panel, the GPU helpers page | done |
| E | performance (profiling; vision engine from the Dell measurements), the demo | last |

Targets: a floor of 900 spaces opens in Review in under 300 ms; a change saves in
under 50 ms; others see it within a second.
