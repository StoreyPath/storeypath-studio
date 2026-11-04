"""StoreyPath Studio as a web application: the whole workflow in a browser.

``storeypath serve --data /data`` serves the Studio pages and a JSON API on one
port. Everything runs locally: drawings are read here, the language model runs
here (llama-server, started on first use), and nothing is fetched from anywhere
else — Studio works on a machine with no network at all.

Projects live in Studio's database (db/), whole: their drawings and the packages
exported of them too; the data folder holds only what is made again when gone (the
cache) and the server's certificate. A project is known by its code (the name people
give it is only shown). Long steps — reading a drawing's plans, converting,
exporting — run as jobs, one at a time, and report progress.

People log in (accounts.py; the first start makes admin / admin): every call but
logging in and out needs a session, and each needs a level (view, edit, share) on the narrowest part of
a project — the project, a building, a floor — that covers what it reads or changes;
nothing shows another floor's content to someone who may see only some floors. What
each call needs is checked first in route() (the table is in studio/README.md):

    POST /api/login {username, password}       → a session (cookie sp_session_<port>)
    POST /api/logout
    GET  /api/me                               POST /api/me/password {current, new}
    GET  /api/status
    GET  /api/projects                         POST /api/projects {name}
    GET  /api/projects/<code>
    POST /api/projects/<code>/delete {confirm: its name}  the project and everything in it, gone
    GET  /api/projects/<code>/access           POST /api/projects/<code>/access {user, scope, level}
    POST /api/projects/<code>/owner {user}     (an admin)
    PUT  /api/projects/<code>/drawings/<name>[?private=0]  (the file as the body)
                                               → job: what it holds that is private
                                               (privacy.py), {pending, found}; with
                                               private=0 kept as sent. A file the
                                               project has already: {drawing, already,
                                               added}, nothing kept
    POST /api/projects/<code>/incoming/<id> {keep: [found ids]}  → job: kept without the
                                               rest of it, as drawing-N.dxf
    POST /api/projects/<code>/incoming/<id>/cancel   not added; the file as sent is gone
    POST /api/projects/<code>/drawings/<name>/delete  the drawing gone, with what was made of
                                               it; refused (409) while a floor reads from it
    GET  /api/projects/<code>/incoming         drawings one sent that wait for one to choose what
                                               of them to keep (a page opened again asks again)
    GET  /api/projects/<code>/jobs             its jobs one may see: to run, running, or ended in
                                               the last 30 minutes (with what they gave)
    GET  /api/projects/<code>/drawings/<name>/words  every word and string left in it (text), to look
                                               through for anything private left behind
    POST /api/projects/<code>/drawings/<name>/plans {units?}  → job: its units and the plans in it
    POST /api/projects/<code>/drawings/<name>/view   the drawing drawn whole for the drawing
                                               viewer (drawview.py): where to get it, or a job
                                               that makes it; GET …/view: the view (gzip)
    POST /api/projects/<code>/floors {drawing, units, plans: [...]}  → job: add, align, convert
    POST /api/projects/<code>/convert          → job
    POST /api/projects/<code>/buildings/<id>/placement {lat, lon, bearing, x?, y?}
    POST /api/projects/<code>/export {building, item_types?, drawings?} → job: that
                                               building's package (one building per
                                               package); drawings: true, each floor's
                                               drawing in it too (format 0.11): edit
                                               on the project too
    GET  /api/projects/<code>/exports/<file>
    GET  /api/projects/<code>/preview.storeypath[?building=<id>]   the project (or one
                                               building) as it is now, for Studio's viewers
                                               (not recorded as an export, never sent):
                                               only the floors the person may see
    GET  /api/projects/<code>/project.storeypath-project   the project to send to another
                                               Studio, to be continued there
    PUT  /api/open[?replace=<name>]               a project from a file: a building's
                                               package, or a project file (bundle.py)
    GET  /api/jobs/<id>
    GET  /api/projects/<code>/events[?floor=<id>]   what happens in it, as it happens (web/events.py)
    GET  /api/projects/<code>/history[?floor=&n=]   who changed what, in words; what one would undo
    POST /api/projects/<code>/undo {floor?}    one's own latest change undone; …/redo: redone
    GET  /api/users                            who to share with (id, username, name)
    GET  /api/admin/users                      POST /api/admin/users {username, name, role, capabilities}
    POST /api/admin/users/<id> {name?, role?, capabilities?, active?}
    POST /api/admin/users/<id>/password        a temporary password, shown once
    GET  /api/admin/audit                      GET /api/backup (the database, .sql.gz)
    GET  /api/admin/helpers                    POST /api/admin/helpers {helpers}: the GPU helpers
    POST /api/admin/helpers/test {url, key?}   a sample room sent to one
    and the review editor's calls under /api/projects/<code>/ (see review.py)

What changes something (POST, a JSON object; PUT, a file) is sent with the header
``X-StoreyPath: 1``, which a page of another site cannot send, from a page of Studio's.
Studio answers only to its own names (allowed_hosts: localhost, this machine's name,
an address, and those given with --allowed-host or STOREYPATH_ALLOWED_HOSTS).
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import traceback
import uuid
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from queue import Queue
from typing import Callable, NamedTuple

from shapely.geometry import shape
from shapely.ops import unary_union

from .accounts import (LOCAL, Accounts, Forbidden, Scope, Sight, Unauthorized, User, rank, temporary_password)
from .assets import asset_dir
from .backup import backup_name, write_backup
from .bundle import ProjectExists
from .db.store import Editor, StepLock, drawing_name
from .ids import (LAND_CODE, default_floor_code, default_mezzanine_code, default_roof_code, is_land, make_id,
                  normalize_item_id, validate_segment)
from .cad import UNIT_NAMES, UNIT_WORDS, DrawingError, header_units, meters_per_unit, read_drawing
from .llm import LocalModel, ModelUnavailable, read_titles, worth_reading
from .sheets import mezzanine_of, read_title, title_level
from .profile import AUTO, load_profile
from .levels import DEFAULT_HEIGHT_M, DEFAULT_PARAPET_M, HEIGHT_M, floor_levels, plan_level, read_level_marks
from .reading import NOT_A_ROOM, read_units
from .symbols import SymbolSpotter
from .vision import VisionModel
from .privacy import words
from .errors import Busy, Conflict
from .review import File, NotFound, Review
from .sealed import sealed_name
from .types import FloorType, SpaceType
from .workspace import Placement, SourceDrawing, Workspace, floor_order

DRAWING_TYPES = (".dwg", ".dxf")
INCOMING = ".incoming-"  # a drawing as sent, until its private copy is made
WORDS = ".words.txt"  # beside a drawing: every word and string left in it
DRAWING_NAME = re.compile(r"drawing-(\d+)\.dxf")
POINT = re.compile(r"-?\d+(\.\d+)?,-?\d+(\.\d+)?")  # a point on a location's land: x,y in metres of its site's frame
PREVIEWS_KEPT = 12  # 3D packages kept, made (Studio.preview): a big building's is a few MB


# ---- jobs ------------------------------------------------------------------------

JOBS_SHOWN_S = 30 * 60  # a project's jobs listed (a page opened again): to run, running, or ended this long ago
JOBS_KEPT_S = 6 * 3600  # a job that ended this long ago is forgotten
LOG_SHOWN = 20  # the last lines of a job's log, as a project's jobs are listed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Stopped(Exception):
    """A job a person stopped (Jobs.stop), where it says how far it has got."""


@dataclass
class Job:
    id: str
    title: str
    state: str = "waiting"  # waiting, running, done, failed (a job stopped: failed, ``stopped``)
    log: list[str] = field(default_factory=list)
    result: object = None
    error: str | None = None
    started: str | None = None
    project: str | None = None  # the project it works on, and the part of it (scope): who may follow it
    scope: tuple[str, str | None] = ("project", None)
    user: str | None = None  # who started it
    # what it does, for a page opened again while it runs (or just after): private (a
    # drawing sent, cleaned), keep (cleaned as chosen), plans (found in a drawing), floors
    # (added from a drawing, then read), read (floors read again), export…; ``about``:
    # the drawing it is about (as sent, for private); ``floors``: those it reads
    kind: str = ""
    about: str | None = None
    floors: list[str] = field(default_factory=list)
    submitted: str = field(default_factory=_now_iso)
    ended: str | None = None
    stop_by: str | None = None  # who asked it to stop (as pages name them), once asked
    stopped: bool = False  # it ended stopped (failed: what it did before is kept)

    def say(self, line: str) -> None:
        self.log.append(line)

    def check(self) -> None:
        """Stopped, when a person asked it to stop: a step calls this where stopping
        leaves things whole (a read: each time it says how far it has got)."""
        if self.stop_by is not None:
            raise Stopped(f"stopped by {self.stop_by}")

    def view(self) -> dict:
        return {"id": self.id, "title": self.title, "kind": self.kind, "state": self.state, "log": self.log,
                "result": self.result, "error": self.error, "stopped": self.stopped}

    def listed(self, by: dict | None = None) -> dict:
        """As a project's jobs are listed: what it is and how it is, its last lines, and
        what it gave once it ended (``by``: who started it, as pages show people)."""
        over = self.state in ("done", "failed")
        return {"id": self.id, "title": self.title, "kind": self.kind, "about": self.about, "state": self.state,
                "scope": list(self.scope), "floors": list(self.floors), "by": by, "submitted": self.submitted,
                "started": self.started, "ended": self.ended, "log": self.log[-LOG_SHOWN:], "lines": len(self.log),
                "result": self.result if over else None, "error": self.error, "stopped": self.stopped,
                "stopping": self.stop_by is not None and not over}


class Jobs:
    """Long steps, run in the background one at a time in each of LANES: a floor read
    because a person drew on it (seconds) goes in a lane of its own, never behind a
    drawing read whole (minutes), and so does a drawing drawn whole for the drawing viewer
    (a person waits to see it); within a lane, one at a time (drawings are large). Each
    is kept, to be followed or listed again, until it ended JOBS_KEPT_S ago."""

    LANES = ("steps", "drawn", "views")

    def __init__(self, data: Path | None = None, drawn: int = 1):
        """``drawn``: floors read at once because people drew on them (a read of one floor
        waits for the one before it all the same: Studio._convert)."""
        self.data = data
        self._jobs: dict[str, Job] = {}
        self._queues: dict[str, Queue] = {lane: Queue() for lane in self.LANES}
        for lane in self.LANES:
            for n in range(drawn if lane == "drawn" else 1):
                threading.Thread(target=self._work, args=(lane,), name=f"jobs: {lane} {n}", daemon=True).start()

    def submit(self, title: str, fn, project: str | None = None, scope: tuple = ("project", None),
               user: str | None = None, kind: str = "", about: str | None = None,
               floors: list[str] | None = None, lane: str = "steps") -> Job:
        self._forget()
        job = Job(uuid.uuid4().hex[:12], title, project=project, scope=scope, user=user, kind=kind, about=about,
                  floors=list(floors or []))
        self._jobs[job.id] = job
        self._queues[lane].put((job, fn))
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            raise NotFound(f"no job {job_id}")
        return self._jobs[job_id]

    STOPPABLE = ("read",)  # the jobs that stop where they say how far they have got

    def stop(self, job: Job, who: str) -> Job:
        """A read of drawings asked to stop (``who``: as pages name them): one waiting
        never starts; one running stops the next time it says how far it has got (a
        floor's vision does every PROGRESS_S), the floor it was reading left as it was."""
        if job.kind not in self.STOPPABLE:
            raise ValueError("only a read of drawings can be stopped")
        if job.ended is None and job.stop_by is None:
            job.stop_by = who
            job.say(f"stopping: asked by {who}")
        return job

    def of(self, project: str) -> list[Job]:
        """A project's jobs, as a page opened again shows them: to run, running, or ended
        within JOBS_SHOWN_S; the latest first."""
        since = (datetime.now(timezone.utc) - timedelta(seconds=JOBS_SHOWN_S)).isoformat()
        jobs = [j for j in list(self._jobs.values()) if j.project == project and (j.ended is None or j.ended >= since)]
        return sorted(jobs, key=lambda j: j.submitted, reverse=True)

    def _forget(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(seconds=JOBS_KEPT_S)).isoformat()
        for job_id, job in list(self._jobs.items()):
            if job.ended is not None and job.ended < old:
                self._jobs.pop(job_id, None)

    def _work(self, lane: str):
        while True:
            job, fn = self._queues[lane].get()
            try:
                job.state, job.started = "running", _now_iso()
                job.check()  # (stopped while it waited)
                job.result = fn(job)
                job.ended = _now_iso()  # (before its state: a job done has ended)
                job.state = "done"
            except Stopped as e:  # what it did before is kept: it says what
                job.ended, job.stopped = _now_iso(), True
                job.state, job.error = "failed", str(e)
                job.say(job.error)
            except Exception as e:  # reported on the page
                job.ended = _now_iso()
                job.state, job.error = "failed", _message(e)
                job.say(job.error)
                traceback.print_exc()


def _message(e: Exception) -> str:
    return str(e) if isinstance(e, (DrawingError, ValueError, KeyError, ModelUnavailable)) else f"{type(e).__name__}: {e}"


# ---- the application -----------------------------------------------------------

CACHE = "cache"  # in the data folder: what is made again when gone (prints of floors)
WORK = "work"  # in the cache: drawings read from the database for a step, removed after it
TILES = "tiles"  # in the cache: drawings printed for the vision model, kept for a floor read again
READS = "reads"  # in the cache: each floor's last read, for it read again after a person drew on it
VIEWS = "views"  # in the cache: drawings drawn whole for the drawing viewer (drawview.py), by their content
VIEWS_KEPT = 40  # views kept of a project (a big drawing's is some MB), the latest used


class Studio:
    """The application: its projects in its database (``db``: the data folder's, as
    db.default_url says, unless given), and its data folder ``data`` for what is not
    data — the cache (prints of floors; drawings put in files while a step reads them)
    and the server's certificate. The first start with an empty database brings the
    projects of the data folder in (db/importer.py), when it has any, and the
    accounts and item types an older Studio kept there; nothing of the folder is
    deleted."""

    def __init__(self, data: str | Path, model: LocalModel | None = None, warm: bool = True,
                 symbols: SymbolSpotter | None = None, vision: VisionModel | None = None, db=None,
                 first_start: bool = True, read_workers: int = 0):
        from . import db as databases
        from .db.store import ProjectStore

        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.cache = self.data / CACHE
        self.work = self.cache / WORK
        self.db = db if db is not None else databases.connect(data=self.data)
        self.store = ProjectStore(self.db)
        self.model = model if model is not None else LocalModel()
        self.symbols = symbols if symbols is not None else SymbolSpotter()
        self.vision = vision if vision is not None else VisionModel()
        # floors read in processes of their own (workers.py), so that reads use the machine's
        # cores; none: read here, one at a time
        self.readers = None
        if read_workers:
            from .workers import ReadWorkers

            self.readers = ReadWorkers(read_workers)
        self.jobs = Jobs(self.data, drawn=max(1, read_workers))
        self._closing = threading.Event()  # (close: what runs in the background stops)
        if warm and self.model.available():
            # Load the model now, in the background, so the first drawing does not
            # wait for 2–3 GB of weights to come off the disk.
            threading.Thread(target=self.model.warm, daemon=True).start()
        self._reviews: dict[str, Review] = {}
        self._networks: dict[tuple, tuple] = {}  # (code, building, version) -> its walking network, worked out
        self._previews: OrderedDict[str, bytes] = OrderedDict()  # a preview's tag -> the package (preview_tag)
        self._started = uuid.uuid4().hex  # (a newer Studio may make previews otherwise: their tags are new)
        self._warm = warm  # what a step leaves to be done later done now, in the background (prints, reads)
        self._warming: set[str] = set()  # floors whose read is being worked out (warm)
        # One lock per project, held while a job changes it: converting one project
        # (minutes, with vision) never holds up another, and pages only read. It is the
        # database's too (StepLock): another Studio on it waits as this one does.
        self._lock = threading.Lock()  # the tables below
        self._project_locks: dict[str, StepLock] = {}
        self._helpers_said = None  # the GPU helpers as the database last said (vision_helpers)
        self._helpers_from_env = [dict(h) for h in getattr(self.vision, "helpers_said", list)()]
        self._opening = threading.Lock()  # one file opened at a time (Studio.open)
        self._pending: dict[str, dict] = {}  # drawings sent, waiting for a person to choose what goes
        self._viewing: dict[tuple[str, str], Job] = {}  # (code, view key) → the job drawing it (view)
        self.store.drop_incoming()  # sent and never kept (Studio stopped meanwhile): not kept
        import shutil

        shutil.rmtree(self.work, ignore_errors=True)  # what a step left when Studio stopped
        self.imported = None
        if first_start:
            from .db.importer import first_start as bring_in

            self.imported = bring_in(self.data, self.db, self.store)
        self.reload_helpers()

    # ---- projects ---------------------------------------------------------------

    def workspace(self, code: str) -> Workspace:
        """The project as it is now (NotFound when there is none): to look things up in,
        never to change."""
        return self.store.current(code)

    def _known(self, code: str) -> str:
        if not isinstance(code, str) or not self.store.exists(code):
            raise NotFound(f"no project {code}")
        return code

    def review(self, code: str) -> Review:
        self._known(code)
        with self._lock:
            if code not in self._reviews:
                from .review import StoredProject

                # its changes wait for (or are refused while) a job changes the project
                self._reviews[code] = Review(StoredProject(self.store, code, self.cache, self.work),
                                             catalogue=self.catalogue,
                                             changing=lambda code=code: self._changing(code))
            return self._reviews[code]

    def catalogue(self):
        """The item types of every project here (the organization's)."""
        return self.store.catalogue()

    def save_catalogue(self, body: dict) -> dict:
        """The catalogue replaced: types may be added, changed or retired, never taken
        out (their codes stay with the items that have them)."""
        from . import catalogue

        catalogue.check_types(body.get("types") if isinstance(body.get("types"), list) else [])
        with self._lock:
            old = self.store.catalogue()
            new = catalogue.Catalogue.model_validate({**body, "format": catalogue.CATALOGUE_FORMAT})
            if gone := sorted({t.code for t in old.types} - {t.code for t in new.types}):
                raise ValueError(f"types are retired, not removed: {', '.join(gone)}")
            had = {t.code: t.model for t in old.types}
            for t in new.types:  # (a type's model is what Studio has, whatever is sent)
                t.model = had.get(t.code)
            self.store.save_catalogue(new)
            return new.model_dump()

    # ---- item types' 3D models (models.py) ---------------------------------------------

    def models(self) -> dict:
        """What each type's model is, by its code: its name, triangles, size, turn, units,
        and what about its size no furniture is (``doubts``: models.doubts)."""
        from . import model_files, models

        out = self.store.models()
        for m in out.values():
            m["doubts"] = models.doubts(m["size"], m["size_before"])
            m["kind"] = model_files.kind_of(m["name"]) or "glTF"
        return out

    def add_model(self, code: str, data: bytes, name: str = "", turn: int = 0, by=None, units: str | None = None,
                  up: str = "y") -> dict:
        """A type's 3D model, made from a file of any format read (model_files.as_glb: glTF,
        FBX, OBJ, Collada… or a .zip of one with its textures; models.make_model; a
        ModelError, a ValueError, says why one cannot be), kept with the file as it came,
        and the type given the model's size (its width, depth and height), so the plan and
        3D agree. ``turn``, ``units`` (None: as the file or its size says) and ``up``: how
        it is made from the file. One it had is replaced. What it is now (models()), with
        what was left out of it (``notes``)."""
        from . import model_files, models

        self._type(code)
        if len(data) > models.MAX_FILE:
            raise models.ModelError(f"the file is {len(data) >> 20} MB: a model is at most {models.MAX_FILE >> 20} MB")
        name = Path(str(name or "")).name[:200] or f"{code}.glb"
        found = model_files.as_glb(data, name)
        made = models.make_model(found.glb, code, name, turn, generator=f"StoreyPath Studio {version('storeypath')}",
                                 units=units, stated=found.stated, up=up)
        with self._lock:
            cat = self.store.catalogue()
            t = cat.get(code)
            before = [t.width, t.depth, t.height]  # (kept from the first model: store.save_model)
            self.store.save_model(code, name, data, made, turn, by=by, units=units, up=up,
                                  before=before if all(isinstance(x, (int, float)) and x > 0 for x in before) else None)
            t.width, t.depth, t.height = made.size
            self.store.save_catalogue(cat)
        return {**self.models()[code], "notes": found.notes}

    def turn_model(self, code: str, turn, by=None) -> dict:
        """A type's model made again from its file, turned ``turn`` degrees (models.TURNS)
        from how it came: its width and depth then the type's."""
        return self.fit_model(code, {"turn": turn}, by=by)

    def fit_model(self, code: str, how: dict, by=None) -> dict:
        """A type's model made again from its file another way: ``how`` any of ``turn``
        (degrees: models.TURNS), ``units`` (models.UNIT_SIZE, or "auto": as the file or its
        size says) and ``up`` ("y", or "z": drawn Z up, stood up); the rest as it was."""
        from . import models

        if not isinstance(how, dict) or not how or set(how) - {"turn", "units", "up"}:
            raise ValueError("send any of turn, units and up")
        if "turn" in how and how["turn"] not in models.TURNS:
            raise ValueError(f"turn: {', '.join(map(str, models.TURNS))} (degrees, counter-clockwise seen from above)")
        if "units" in how and how["units"] != "auto" and how["units"] not in models.UNIT_SIZE:
            raise ValueError(f"units: auto, {', '.join(models.UNIT_SIZE)}")
        if "up" in how and how["up"] not in models.UPS:
            raise ValueError("up: y or z")
        self._type(code)
        source = self.store.model_source(code)
        if source is None:
            raise NotFound(f"{code} has no model")
        units = how.get("units", source["units"])
        return self.add_model(code, source["source"], source["name"], how.get("turn", source["turn"]), by=by,
                              units=None if units == "auto" else units, up=how.get("up", source["up"]))

    def drop_model(self, code: str) -> dict:
        """A type's model taken away: drawn by its shape again (its size kept)."""
        self._type(code)
        if not self.store.drop_model(code):
            raise NotFound(f"{code} has no model")
        return {"code": code, "model": None}

    def model_file(self, code: str) -> File:
        """A type's model as packages carry it (model/gltf-binary)."""
        data = self.store.model_file(code)
        if data is None:
            raise NotFound(f"{code} has no model")
        return File(data, "model/gltf-binary")

    def model_picture(self, code: str, view: str = "front") -> File:
        """A type's model drawn flat, as SVG (models.picture): from its front, above or side."""
        from . import models

        data = self.store.model_file(code)
        if data is None:
            raise NotFound(f"{code} has no model")
        return File(models.picture(data, view).encode(), "image/svg+xml")

    def _type(self, code: str):
        t = self.store.catalogue().get(code) if isinstance(code, str) else None
        if t is None:
            raise NotFound(f"no item type {code}")
        return t

    def types_in(self, data: bytes) -> dict:
        """The item types a file brings (a catalogue, a package or a project file), for a
        person to choose which to take into the catalogue: read, nothing changed
        (catalogue.types_in)."""
        from . import catalogue

        return catalogue.types_in(data)

    def _changing(self, code: str) -> StepLock:
        """The lock held while a job changes this project (the database's advisory lock
        on it, so that another Studio on the database waits too)."""
        with self._lock:
            lock = self._project_locks.get(code)
            if lock is None:
                lock = self._project_locks[code] = StepLock(self.db, code)
            return lock

    def status(self, paths: bool = True) -> dict:
        """What Studio can do; ``paths``: and where its data folder and database are (for
        an admin)."""
        from shutil import which

        from .cad import odafc
        from .db import shown

        return {
            "version": version("storeypath"),
            "model": self.model.name if self.model.available() else None,
            "model_ready": self.model.ready,
            "symbols": self.symbols.name if self.symbols.available() else None,
            "vision": self.vision.name if self.vision.available() else None,
            "dwg": bool(which("dwg2dxf")) or odafc.is_installed(),
            **({"data": str(self.data), "database": shown(self.db.url)} if paths else {}),
            # the 2D plan page: the plan engine compiled (npm run build in viewer/svg)
            "plan": (asset_dir("viewer") / "svg" / "dist" / "index.js").is_file(),
        }

    def projects(self, sight_of=None) -> list[dict]:
        """Every project, or with ``sight_of`` ((code, workspace) -> Sight: what a person
        may see of it), those they may see, each counted in what they see of it, with
        their level on it (``can``)."""
        out = []
        for code in self.store.codes():
            try:
                ws = self.store.current(code)
            except NotFound:
                continue  # deleted meanwhile
            sight = sight_of(code, ws) if sight_of is not None else None
            if sight is not None and not sight.any():
                continue
            seen = sight.seen(ws) if sight is not None else ws
            spaces = [r for r in seen.objects.values() if r.kind == "space" and r.status == "active"]
            entry = {"code": code, "name": ws.project.name, "file": code,
                     "floors": sum(1 for _ in seen.iter_floors()), "spaces": len(spaces),
                     "review": sum(1 for r in spaces if seen.review_reasons(r))}
            if sight is not None:
                entry["can"] = {k: v for k, v in sight.can().items() if k not in ("buildings", "floors")}
            out.append(entry)
        return sorted(out, key=lambda p: p["name"].lower())

    def create(self, name: str, kept=(), by=None) -> dict:
        """A new project, its code one no project here has, nor one ``kept`` (codes
        whose sharing is kept, accounts.py)."""
        from .db.store import ProjectExists as Taken

        if name is not None and not isinstance(name, str):
            raise ValueError("a project's name is text")
        name = (name or "").strip()
        if not name:
            raise ValueError("a project needs a name")
        taken = {*self.store.codes(), *kept}
        while True:
            ws = Workspace.new(name)
            if ws.id in taken:
                continue
            ws.add_location("SITE", name)
            try:
                self.store.create(ws, by=by)
            except Taken:
                continue  # made meanwhile
            return {"code": ws.id}

    def project(self, code: str, sight: Sight | None = None) -> dict:
        """The project's page: its locations, buildings and floors, drawings and packages.
        With ``sight``, only what that person may see of it: the floors (a building's
        footprint and middle drawn from those alone, where it stands on its site as it
        does for everyone), the drawings of those floors (all of them with a level on
        the whole project), and the packages of the buildings they see whole."""
        full = self.workspace(code)
        ws = sight.seen(full) if sight is not None else full
        info = self.review_project(code, sight)
        from .export import footprint, north_said, site_positions

        rooms: dict[str, int] = {}  # each floor's rooms and items in use (what deleting it retires)
        for i, r in full.objects.items():
            if r.status == "active" and r.kind == "space":
                rooms[i.rsplit("-", 1)[0]] = rooms.get(i.rsplit("-", 1)[0], 0) + 1
        items: dict[str, int] = {}
        for it in full.items.values():
            if it.status == "active" and it.floor_id:
                items[it.floor_id] = items.get(it.floor_id, 0) + 1

        tree = []
        for loc in ws.locations:
            buildings = []
            whole_loc = next(x for x in full.locations if x.code == loc.code)
            positions = site_positions(whole_loc)  # as everyone sees the site
            for b in loc.buildings:
                b_id = f"{ws.id}-{loc.code}-{b.code}"
                whole_b = next(x for x in whole_loc.buildings if x.code == b.code)
                outline = [shape(f.outline) for f in b.floors if f.outline]
                centre = unary_union(outline).centroid if outline else None
                # a building with no floors yet, on a footprint of its location's layout (0.12): it
                # stands as that footprint (its frame the site's there)
                stands = _footprint_shape(full, b) if not outline else None
                buildings.append({
                    "id": b_id, "code": b.code, "name": b.name,
                    "placement": b.placement.model_dump() if b.placement else None,
                    # its place on the site plan (as its drawing places it when not set) and
                    # its footprint in its own drawing metres, for the site plan to draw
                    "site": {**positions[b.code].model_dump(), "set": whole_b.site is not None},
                    "footprint": _rings(footprint(b) if stands is None else stands),
                    "on_footprint": b.site.footprint if b.site is not None else None,
                    "centre": [round(centre.x, 2), round(centre.y, 2)] if centre is not None else None,
                    # (each floor's part of its drawing, in drawing units, and how it was read:
                    # the drawing viewer outlines it)
                    "floors": [{"id": f"{b_id}-{f.code}", "name": f.name, "ordinal": f.ordinal,
                                "drawing": Path(f.source.path).name if f.source else None,
                                "view": f.source.view if f.source else None,
                                "region": list(f.source.region) if f.source and f.source.region else None,
                                "offset": list(f.source.offset) if f.source and f.source.offset else None,
                                "units": f.source.units if f.source else None,
                                "layers": f.layers, "converted": f.converted_at is not None,
                                "method": f.method, "rooms": rooms.get(f"{b_id}-{f.code}", 0),
                                "items": items.get(f"{b_id}-{f.code}", 0),
                                "code": f.code, "elevation": f.elevation, "height": f.height,
                                # what level it is; a mezzanine's floor it stands in, and how far above it
                                "type": f.type.value,
                                "host": f"{b_id}-{f.host}" if f.mezzanine else None,
                                "host_name": next((h.name for h in b.floors if h.code == f.host), None)
                                if f.mezzanine else None,
                                "above_host": f.above_host}
                               for f in sorted(b.floors, key=floor_order)],
                })
            tree.append({"id": f"{ws.id}-{loc.code}", "code": loc.code, "name": loc.name, "buildings": buildings,
                         "placement": loc.placement.model_dump() if loc.placement else None,
                         # which way north is (0.12): the site's bearing, and how it is known
                         "north": {**north_said(whole_loc), "sure": whole_loc.north.sure
                                   if whole_loc.north is not None else None},
                         # its layout (0.12): its land, read from a site plan
                         "layout": _layout_page(full, whole_loc, items) if loc.layout is not None and loc.layout.floors
                         else None})
        kept = [d for d in self.store.drawings(code) if Path(d["name"]).suffix.lower() in DRAWING_TYPES]
        exports = self.store.export_files(code)
        if sight is not None and not sight.whole:
            used = {Path(f.source.path).name for *_, f, _ in ws.iter_floors() if f.source}
            kept = [d for d in kept if d["name"] in used]
            exports = [e for e in exports if (held := package_buildings(full, e))
                       and all(rank(sight.building(b)) >= rank("view") for b in held)]
        # each drawing: when it was added, the others that are the same file (one of them is
        # spare: delete it), and why it may not be deleted (the floors that read from it)
        drawings = [{"name": d["name"], "added": d["uploaded_at"].isoformat() if d["uploaded_at"] else None,
                     "same_as": [o["name"] for o in kept if o["sha256"] == d["sha256"] and o["name"] != d["name"]],
                     "in_use": _in_use(ws, d["name"])[0]} for d in kept]
        return {**info, "locations": tree, "drawings": drawings, "exports": exports,
                "exported": len(full.exports) if sight is None or sight.whole else len(exports),
                **({"can": sight.can()} if sight is not None else {})}

    def review_project(self, code: str, sight: Sight | None = None) -> dict:
        """The review editor's project: its floors (with ``sight``, those the person may see,
        each with their level on it, ``can``), and the space types."""
        info = self.review(code).project()
        if sight is not None:
            info["floors"] = [{**f, "can": sight.floor(f["id"])} for f in info["floors"] if sight.floor(f["id"])]
            info["can"] = sight.can()
        return info

    # ---- drawings ---------------------------------------------------------------

    @contextmanager
    def _files(self, code: str, names=None, incoming: bool = False):
        """The project's drawings (``names``, or all) as files while the block runs (in
        the cache's work folder), then removed: the folder they are in, as
        ``drawings/<name>``."""
        self.work.mkdir(parents=True, exist_ok=True)
        with self.store.files(code, names, folder=self.work, incoming=incoming) as folder:
            yield folder

    def upload(self, code: str, name: str, body: bytes, private: bool = True, by=None) -> dict | Job:
        """A drawing added to the project. Kept private (the default), a job finds what
        names the people and the project — title blocks, names, contacts, hidden file
        data, what the language model reads as private (privacy.py) — and a person
        chooses what of it to keep (keep_private); only that copy is kept, as
        drawing-N.dxf: the file as sent, and its name, are not. Otherwise it is kept
        as sent. A file the project has already (sent before, whatever was kept of it,
        or kept as it is) is not kept again: the answer names the drawing it is
        (already)."""
        self._known(code)
        name = Path(name).name
        suffix = Path(name).suffix.lower()
        if suffix not in DRAWING_TYPES:
            raise ValueError("drawings are .dwg or .dxf files")
        sent = hashlib.sha256(body).hexdigest()  # (the file as sent: before it is cleaned or converted)
        same = self.store.same_drawing(code, sent)
        if same is not None:
            return _already(same)
        if not private:
            self.store.put_drawing(code, name, body, sent=sent, by=by)
            return {"drawing": name, "bytes": len(body)}
        token = uuid.uuid4().hex[:12]
        incoming = f"{INCOMING}{token}{suffix}"
        self.store.put_incoming(code, incoming, body, by=by)
        uid = _uid(by)

        def run(job: Job):
            from .cad import read_drawing_to_change
            from .privacy import Choices, make_private
            from .vision import InWords

            try:
                job.say("reading the drawing")
                with self._files(code, [incoming], incoming=True) as folder:
                    doc = read_drawing_to_change(folder / "drawings" / incoming)
                job.say("looking for title blocks, names, contacts and hidden file data")
                self.reload_helpers()
                reader = InWords(self.vision) if self.vision.available() else \
                    self.model if self.model.available() else None
                choices = Choices()
                report = make_private(doc, reader, job.say, choices)
            except Exception:
                self.store.drop_incoming(code, incoming)
                raise
            if not choices.found:  # nothing to choose: kept as it is
                return self._keep_private(code, incoming, doc, report, job, by)
            # what was found goes once a person says what to keep; the cleaned copy
            # waits, as it is what keeping nothing gives
            with self._lock:
                self._pending[token] = {"code": code, "incoming": incoming, "name": name,
                                        "doc": doc, "report": report, "choices": choices, "reader": reader,
                                        "user": uid, "since": _now_iso()}
            job.say(f"found {len(choices.found)} things to take out: choose what to keep")
            return {"pending": token, "name": name, "found": choices.listed(), "reader": report.model}

        return self.jobs.submit(f"Looking for private information in {name}", run, project=code, user=uid,
                                kind="private", about=name)

    def waiting(self, code: str, user: str | None = None, everyone: bool = False) -> list[dict]:
        """The drawings sent to a project that wait for a person to choose what of their
        private information to keep (what the page asks again): those ``user`` sent, or
        everyone's; the oldest first. Each as the job that found it gave it."""
        with self._lock:
            mine = [(token, p) for token, p in self._pending.items()
                    if p["code"] == code and (everyone or p.get("user") == user)]
        return [{"pending": token, "name": p["name"], "found": p["choices"].listed(), "reader": p["report"].model,
                 "since": p.get("since")} for token, p in sorted(mine, key=lambda x: x[1].get("since") or "")]

    def keep_private(self, code: str, token: str, body: dict, by=None) -> Job:
        """A drawing sent, kept without the private information found in it, all but
        what a person chose to keep (``keep``: ids of found things)."""
        keep = body.get("keep") or []
        if not isinstance(keep, list) or not all(isinstance(k, str) for k in keep):
            raise ValueError("keep: the ids of what was found to keep")
        keep = set(keep)
        with self._lock:
            p = self._pending.get(token)
            if p is None or p["code"] != code:
                raise NotFound("no drawing waiting to be added: send it again")
            del self._pending[token]
        uid = _uid(by)

        def run(job: Job):
            from .cad import read_drawing_to_change
            from .privacy import Choices, make_private

            doc, report = p["doc"], p["report"]
            if keep:  # read again, taking out all but what is kept
                job.say(f"taking out all but the {len(keep)} kept")
                try:
                    with self._files(code, [p["incoming"]], incoming=True) as folder:
                        doc = read_drawing_to_change(folder / "drawings" / p["incoming"])
                    choices = Choices(keep, p["choices"].model_found)
                    report = make_private(doc, p["reader"], job.say, choices)
                except Exception:
                    self.store.drop_incoming(code, p["incoming"])
                    raise
            return self._keep_private(code, p["incoming"], doc, report, job, by)

        return self.jobs.submit(f"Adding {p['name']} without its private information", run, project=code, user=uid,
                                kind="keep", about=p["name"])

    def cancel_private(self, code: str, token: str) -> dict:
        with self._lock:
            p = self._pending.pop(token, None)
        if p is None or p["code"] != code:
            raise NotFound("no drawing waiting to be added")
        self.store.drop_incoming(code, p["incoming"])
        return {"cancelled": p["name"]}

    def _keep_private(self, code: str, incoming: str, doc, report, job: Job, by=None) -> dict:
        """The cleaned drawing kept as drawing-N.dxf (a number no drawing of the project
        had: one deleted is not named again), with its words and the sha256 of the file
        as sent; the file as sent gone. The same file kept meanwhile (sent twice, both
        waiting): not kept again, the answer naming it (already)."""
        import tempfile

        try:
            sent = (self.store.drawing(code, incoming, incoming=True) or {}).get("sha256")
            same = self.store.same_drawing(code, sent) if sent else None
            if same is not None:
                job.say(f"already here: {same['name']}")
                return _already(same)
            job.say(report.summary())
            taken = [int(m.group(1)) for n in self.store.drawing_names_had(code) if (m := DRAWING_NAME.fullmatch(n))]
            name = f"drawing-{max(taken, default=0) + 1}.dxf"
            self.work.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="storeypath-", dir=self.work) as folder:
                out = Path(folder) / name
                doc.saveas(out)
                data = out.read_bytes()
            self.store.put_drawing(code, name, data, words=words(doc, name, report.summary()), sent=sent, by=by)
            job.say(f"kept as {name}")
            job.say(f"every word left in it: Words, beside {name}")
        finally:
            self.store.drop_incoming(code, incoming)
        return {"drawing": name, "privacy": report.view()}

    def words(self, code: str, name: str) -> File:
        """Every word and string left in a drawing, kept when it was added (or read now,
        for one added before), as text."""
        name = self._drawing(code, name)
        kept = self.store.words(code, name)
        if kept is None:
            from .cad import read_drawing

            with self._files(code, [name]) as folder:
                kept = words(read_drawing(folder / "drawings" / name), name)
            self.store.set_words(code, name, kept)
        return File(kept.encode("utf-8"), "text/plain; charset=utf-8")

    def delete_drawing(self, code: str, name: str, by=None) -> dict:
        """A drawing taken out of the project (a change in its history), with what was made
        of it: its words, its views for the drawing viewer and the packages (unless another
        drawing of the project is the same file: they are its too) and the prints drawn
        from it. Refused (Conflict, 409) while a floor reads from it, naming those floors:
        a floor keeps what was read, but without its drawing it could not be read again or
        printed; give it another drawing first. Waits a moment for a job changing the
        project, else Busy (one adding floors from it would give it a floor)."""
        from .review import BUSY_MESSAGE, BUSY_WAIT_S

        name = self._drawing(code, name)

        def check(ws: Workspace) -> None:  # (the project as it is in the change itself)
            said, floors = _in_use(ws, name)
            if said:
                raise Conflict(said, floors=floors)

        lock = self._changing(code)
        if not lock.acquire(timeout=BUSY_WAIT_S):
            raise Busy(BUSY_MESSAGE)
        try:
            sha = (self.store.drawing(code, name) or {}).get("sha256")
            self.store.remove_drawing(code, name, by=by, check=check)
        finally:
            lock.release()
        if sha and not any(d["sha256"] == sha for d in self.store.drawings(code)):
            for view in (self.cache / VIEWS / code).glob(f"{sha[:32]}-*.view"):
                view.unlink(missing_ok=True)
        for info in (self.cache / "prints" / code).glob("*.json"):  # (each says the drawing it was drawn from)
            try:
                drawn_from = json.loads(info.read_text()).get("drawing")
            except (OSError, ValueError, AttributeError):
                continue
            if drawn_from == name:
                info.with_suffix(".png").unlink(missing_ok=True)
                info.unlink(missing_ok=True)
        return {"deleted": name}

    def delete(self, code: str, body: dict, by=None) -> dict:
        """A project and everything in it (drawings, floors, corrections, packages, its
        history), gone: only when its name is typed to confirm and no job is changing
        it."""
        import shutil

        ws = self.workspace(code)
        confirm = body.get("confirm")
        if not isinstance(confirm, str) or confirm.strip() != ws.project.name.strip():
            raise ValueError("type the project's name to delete it")
        lock = self._changing(code)
        if not lock.acquire(blocking=False):
            raise ValueError("a job is working on this project: delete it when the job is done")
        try:
            self.store.delete(code)
            with self._lock:
                self._reviews.pop(code, None)
                self._project_locks.pop(code, None)
            shutil.rmtree(self.cache / "prints" / code, ignore_errors=True)
            shutil.rmtree(self.cache / VIEWS / code, ignore_errors=True)
        finally:
            lock.release()
        return {"deleted": code, "name": ws.project.name}

    def _drawing(self, code: str, name: str) -> str:
        """A drawing of the project, by its name (NotFound when it has none of that name)."""
        if not isinstance(name, str):
            raise ValueError("drawing: a drawing's name")
        self._known(code)
        name = Path(name).name
        if self.store.drawing(code, name) is None:
            raise NotFound(f"no drawing {name}")
        return name

    # ---- a drawing drawn whole, for the drawing viewer -----------------------------

    def view(self, code: str, name: str, by=None) -> dict | Job:
        """A drawing drawn whole for the drawing viewer (drawview.py), as its own program
        shows it: where to get it when it is made already (made once for its content,
        kept in the cache), else the job that makes it. A big drawing takes minutes: never
        while a request waits (a proxy in front of Studio stops waiting). In a read worker
        when there are any (workers.py): drawing it holds up nothing of the web server's."""
        name = self._drawing(code, name)
        key = self._view_key(code, name)
        if self._view_path(code, key).is_file():
            return self._view_made(code, name, key)
        uid = _uid(by)

        def run(job: Job):
            from .drawview import write_view

            out = self._view_path(code, key)
            if not out.is_file():
                with self._files(code, [name]) as folder:
                    drawing = folder / "drawings" / name
                    if self.readers is not None:
                        made = self.readers.view(key, {"drawing": str(drawing), "out": str(out)}, job.say)
                    else:
                        from .vision import _DRAWING

                        made = write_view(drawing, out, say=job.say, lock=_DRAWING)
                job.say(f"drawn in {made['seconds']:.0f} s: {made['bytes'] / 1e6:.1f} MB to send")
                self._prune_views(code, keep=out)
            return self._view_made(code, name, key)

        with self._lock:  # (asked twice while it is drawn: the same job)
            going = self._viewing.get((code, key))
            if going is not None and going.ended is None:
                return going
            job = self.jobs.submit(f"Getting {name} ready to view", run, project=code, user=uid, kind="view",
                                   about=name, lane="views")
            self._viewing = {k: j for k, j in self._viewing.items() if j.ended is None}
            self._viewing[(code, key)] = job
        return job

    def view_file(self, code: str, name: str) -> Path:
        """The drawing's view, made (view): NotFound until it is."""
        name = self._drawing(code, name)
        path = self._view_path(code, self._view_key(code, name))
        if not path.is_file():
            raise NotFound(f"{name} is not drawn whole yet: ask for it to be (POST …/view)")
        path.touch()  # (the latest used are kept: _prune_views)
        return path

    def _view_key(self, code: str, name: str) -> str:
        from .drawview import VIEW_VERSION

        sha = (self.store.drawing(code, name) or {}).get("sha256") or ""
        return f"{sha[:32]}-{VIEW_VERSION}"

    def _view_path(self, code: str, key: str) -> Path:
        return self.cache / VIEWS / code / f"{key}.view"

    def _view_made(self, code: str, name: str, key: str) -> dict:
        from urllib.parse import quote

        size = self._view_path(code, key).stat().st_size
        return {"drawing": name, "key": key, "bytes": size,
                "url": f"/api/projects/{quote(code)}/drawings/{quote(name)}/view?v={key}"}

    def _prune_views(self, code: str, keep: Path) -> None:
        """A project's views: those of an older VIEW_VERSION gone, and all but the
        VIEWS_KEPT latest used."""
        folder = self.cache / VIEWS / code
        views = sorted(folder.glob("*.view"), key=lambda p: p.stat().st_mtime, reverse=True)
        same = keep.stem.rsplit("-", 1)[0]
        for i, path in enumerate(views):
            if path != keep and (i >= VIEWS_KEPT or path.stem.rsplit("-", 1)[0] == same):
                path.unlink(missing_ok=True)

    def plans(self, code: str, name: str, units: str | None = None, by=None) -> Job:
        """The plans in a drawing, read in ``units``, or in the units it shows."""
        name = self._drawing(code, name)
        if units is not None and not isinstance(units, str):
            raise ValueError(f"units: one of {', '.join(UNIT_NAMES)}")
        if units and units not in UNIT_NAMES:
            raise ValueError(f"unknown units {units!r}: use one of {', '.join(UNIT_NAMES)}")

        def run(job: Job):
            from .sheets import NOT_A_FLOOR, find_views, floor_parts

            job.say(f"reading {name}")
            with self._files(code, [name]) as folder:
                doc = read_drawing(folder / "drawings" / name)
            if units:
                used, sure, reason = units, True, f"Read in {UNIT_WORDS[units]}, as you chose."
            else:
                if self.model.available():
                    job.say(f"looking for its units (notes read with {self.model.name})")
                decision = read_units(doc, self.model if self.model.available() else None)
                used, sure, reason = decision.units, decision.sure, decision.reason
            job.say(reason)
            rules = load_profile(AUTO)

            def is_room_name(text):
                if NOT_A_ROOM.match(text):
                    return False
                return True if rules.classify(text, [], "")[0] != SpaceType.UNSPECIFIED else None

            job.say("looking for plans")
            scale = meters_per_unit(doc, used)
            views = find_views(doc, rules, scale, auto=True, is_room_name=is_room_name)
            job.say(f"found {len(views)} drawings")
            # Plain titles are read by rule; the model reads the rest (other languages,
            # titles that name a building).
            plain = {v.title: read_title(v.title) for v in views if v.title}
            unsure = [t for t, r in plain.items() if r is None and worth_reading(t)]
            readings = {}
            if self.model.available() and unsure:
                job.say(f"reading {len(unsure)} of their titles with {self.model.name}")
                try:
                    readings = read_titles(self.model, unsure)
                except ModelUnavailable as e:
                    job.say(f"the language model was not used: {e}")
            out = []
            for v in views:
                r = readings.get(v.title or "")
                rule = plain.get(v.title)
                kind = r.kind if r else rule[0] if rule else ("floor_plan" if v.is_plan else "other")
                floor = r.floor if r and r.floor is not None else (rule[1] if rule and rule[1] is not None else v.ordinal)
                if kind == "roof_plan" and floor is None:
                    floor = v.ordinal
                # what level its title says: a mezzanine's plan (its floor the one it stands in), the
                # roof's (or the roof of what stands on it: an upper roof), a penthouse's (a floor)
                said = title_level(v.title) if kind in ("floor_plan", "roof_plan") else None
                host = mezzanine_of(v.title) if kind == "floor_plan" and said == "mezzanine" else None
                level = (FloorType.MEZZANINE if host is not None
                         else FloorType.ROOF if said in ("roof", "upper roof") or kind == "roof_plan" and said is None
                         else FloorType.FLOOR)
                out.append({"index": v.index, "title": v.title, "kind": kind,
                            "floor": host if host is not None else floor, "type": level.value,
                            "upper": said == "upper roof",  # (not chosen to start with: not a level people use)
                            "name": "Penthouse" if said == "penthouse" else None,
                            "building": r.building if r else None, "size": v.size, "region": v.region,
                            "preview": v.preview})
            # Floor heights and the roof's parapet, from the levels on its sections, and
            # where they give none, from the level each floor's plan marks; a mezzanine's
            # level above the floor it stands in, from both their plans' levels.
            found = floor_levels(read_level_marks(doc, self.model if self.model.available() else None))
            marked = {}
            for v, o in zip(views, out):
                if o["kind"] in ("floor_plan", "roof_plan") and o["floor"] is not None and o["type"] != "mezzanine" \
                        and o["floor"] not in marked:
                    o["level"] = marked[o["floor"]] = plan_level(doc, v.region)
            for v, o in zip(views, out):
                if o["type"] == "mezzanine":
                    o["level"] = level = plan_level(doc, v.region)
                    host_level = marked.get(o["floor"])
                    if level is not None and host_level is not None and 0 < level - host_level <= HEIGHT_M[1]:
                        o["above_host"] = round(level - host_level, 2)
            found.add_plans(marked)
            job.say(f"levels: {found.summary()}" if found.summary() else "no floor levels on its sections or plans")
            # parts of one floor drawn where they stand (a building's two wings): which plans
            # can be read together, nothing else drawn between them; and those that carry on
            # one numbering, offered together
            joins, together, _ = floor_parts(doc, [v.region for v in views],
                                             [o["floor"] if o["kind"] == "floor_plan" else NOT_A_FLOOR for o in out])
            for i, o in enumerate(out):
                o["joins"] = [views[j].index for j in joins[i]]
                o["together"] = [views[j].index for j in together[i]] if together[i] and o["kind"] == "floor_plan" else None
            levels = {"summary": found.summary() or None, "heights": {str(n): h for n, h in found.heights.items()},
                      "height": found.typical_height(), "roof": found.roof, "parapet": found.parapet,
                      "default_parapet": DEFAULT_PARAPET_M}
            return {"drawing": name, "units": used, "units_sure": sure, "units_reason": reason,
                    "units_chosen": bool(units), "units_said": header_units(doc), "levels": levels, "plans": out}

        return self.jobs.submit(f"Reading the plans in {name}", run, project=code, user=_uid(by), kind="plans", about=name)

    def add_floors(self, code: str, body: dict, by=None) -> Job:
        drawing = self._drawing(code, body.get("drawing", ""))
        units = body.get("units") or None  # the units the plans were found in: kept with each floor
        if units is not None and not isinstance(units, str) or units and units not in UNIT_NAMES:
            raise ValueError(f"unknown units {units!r}: use one of {', '.join(UNIT_NAMES)}")
        plans = body.get("plans") or []
        if not plans:
            raise ValueError("choose at least one plan")
        _floors_to_add(self.workspace(code), plans)  # two plans as one floor: say so now, change nothing

        def run(job: Job):
            from .sheets import align, floor_walls
            from .analyse import analyse

            with self._changing(code):
                if self.store.drawing(code, drawing) is None:  # (deleted since the floors were asked for)
                    raise NotFound(f"no drawing {drawing}: it was deleted")
                ws = self.store.load(code, floors=[])
                places = _floors_to_add(ws, plans)
                added: dict[str, list[str]] = {}  # building -> floors added or given this drawing
                replaced: set[str] = set()  # floors given this drawing in place of theirs
                made_locations: dict[str, str] = {}
                made_buildings: dict[tuple[str, str], str] = {}
                # (the floors first: a mezzanine's may be among them; then the roofs, above them)
                for place in sorted(places, key=lambda x: LEVEL_ORDER.index(x.type)):
                    p = place.plan
                    loc_id = place.location_id
                    if loc_id is None:
                        loc_code, loc_name = place.location
                        if loc_code not in made_locations:
                            made_locations[loc_code] = ws.add_location(loc_code, loc_name)
                            job.say(f"location {loc_name} ({loc_code})")
                        loc_id = made_locations[loc_code]
                    b_id = place.building_id
                    if b_id is None:
                        b_code, b_name = place.building
                        if (loc_id, b_code) not in made_buildings:
                            made_buildings[loc_id, b_code] = ws.add_building(loc_id, b_code, b_name)
                            job.say(f"building {b_name} ({b_code})")
                        b_id = made_buildings[loc_id, b_code]
                    source = SourceDrawing(path=f"drawings/{drawing}", profile=AUTO,
                                           units=units, region=tuple(p["region"]), view=p.get("title"))
                    if place.replaces:
                        # a new drawing of a floor the building has: its spaces keep their IDs
                        f = ws.floor(place.replaces)
                        f.source = source
                        if p.get("name"):
                            f.name = p["name"]
                        if p.get("height"):
                            f.height = float(p["height"])
                        if p.get("parapet"):
                            f.parapet_height = float(p["parapet"])
                        if f.mezzanine and p.get("above_host"):
                            f.above_host = float(p["above_host"])
                        f_id = place.replaces
                        replaced.add(f_id)
                        job.say(f"floor {f_id}: its drawing is now {p.get('title') or 'plan ' + str(p.get('index'))}")
                    else:
                        mezzanine = place.type == FloorType.MEZZANINE
                        f_id = ws.add_floor(b_id, place.ordinal, code=place.code, name=p.get("name") or None,
                                            source=source, height=float(p.get("height") or DEFAULT_HEIGHT_M),
                                            parapet_height=float(p["parapet"]) if p.get("parapet") else None,
                                            type=place.type,
                                            above_host=float(p["above_host"]) if mezzanine and p.get("above_host")
                                            else None)
                        job.say(f"floor {f_id}: {p.get('title') or 'plan ' + str(p.get('index'))}"
                                + (f", a mezzanine of floor {place.ordinal}" if mezzanine
                                   else ", the roof" if place.type == FloorType.ROOF else ""))
                    added.setdefault(b_id, []).append(f_id)
                for b_id in added:
                    ws.restack(b_id)  # elevations from the heights
                self.store.save(ws, floors=[], by=by, part="project", kind="floors",
                                targets=[f for fs in added.values() for f in fs], more={"drawing": drawing})

            # Each floor added lines up with its building's lowest floor (from this
            # drawing or another: one drawing per floor is common), so floors stand on
            # one another.
            docs: dict[str, object] = {}
            needed = {drawing_name(f.source.path) for b_id in added for f in ws.building(b_id).floors if f.source}
            with self._files(code, needed) as folder:

                def doc_of(f):
                    name = drawing_name(f.source.path)
                    if name not in docs:
                        docs[name] = read_drawing(folder / "drawings" / name)
                    return docs[name]

                def walls(f):
                    doc = doc_of(f)
                    scale = meters_per_unit(doc, f.source.units)
                    prof = analyse(doc, scale, f.source.region, None, load_profile(AUTO)).profile
                    return floor_walls(doc, prof, scale, f.source.region), scale

                for b_id, new in added.items():
                    ids = {fl_id.rsplit("-", 1)[-1] for fl_id in new}
                    # A new drawing of a floor that has walls (read before, or from a package)
                    # lines up on them: its rooms come back where they were, keeping their IDs.
                    own = set()
                    for f in ws.building(b_id).floors:
                        if f.code not in ids or f"{b_id}-{f.code}" not in replaced or not f.walls:
                            continue
                        f_walls, scale = walls(f)
                        (tx, ty), overlap = align(shape(f.walls), f_walls)
                        if overlap < MIN_ALIGN_OVERLAP:
                            tx = ty = 0.0
                            job.say(f"  {f.name}: too few walls line up with its walls before ({overlap:.0%}): kept where its drawing has it")
                        else:
                            job.say(f"  {f.name}: lined up on its walls before: moved {tx:.2f}, {ty:.2f} m; {overlap:.0%} line up")
                        f.source.offset = (tx / scale, ty / scale)
                        own.add(f.code)
                    floors = sorted((f for f in ws.building(b_id).floors if f.source is not None and f.code not in own),
                                    key=floor_order)
                    if len(floors) < 2:
                        continue
                    # the lowest floor that was there before, else the lowest one added
                    ref = next((f for f in floors if f.code not in ids), floors[0])
                    job.say(f"lining up the floors of {b_id} with {ref.name}")
                    ref_walls, ref_scale = walls(ref)
                    ref_off = ref.source.offset or (0.0, 0.0)
                    for f in floors:
                        if f is ref or f.code not in ids:
                            continue
                        f_walls, scale = walls(f)
                        (tx, ty), overlap = align(ref_walls, f_walls)
                        if f.source.path != ref.source.path and overlap < MIN_ALIGN_OVERLAP:
                            # another drawing, its walls too unlike: as its drawing places it (one
                            # drawing per floor usually shares the building's coordinates)
                            tx = ty = 0.0
                            job.say(f"  {f.name}: too few walls line up ({overlap:.0%}): kept where its drawing has it")
                        else:
                            job.say(f"  {f.name}: moved {tx:.2f}, {ty:.2f} m; {overlap:.0%} of walls line up")
                        # in this floor's drawing units, from where the reference stands
                        f.source.offset = ((ref_off[0] * ref_scale + tx) / scale, (ref_off[1] * ref_scale + ty) / scale)
            with self._changing(code):
                saved = self.store.load(code, floors=[])
                for b_id in added:
                    for f in ws.building(b_id).floors:
                        if f.source is not None:
                            saved_floor = next(x for x in saved.building(b_id).floors if x.code == f.code)
                            if saved_floor.source is not None:
                                saved_floor.source.offset = f.source.offset
                self.store.save(saved, floors=[], by=by, part="project", kind="align",
                                targets=[f for fs in added.values() for f in fs])
            self._convert(code, [f for fs in added.values() for f in fs], job, by=by)
            if made_buildings:
                self._stand_apart(code, set(made_buildings.values()), job, by=by)
            return {"floors": [f for fs in added.values() for f in fs]}

        return self.jobs.submit(f"Adding floors from {drawing}", run, project=code, user=_uid(by), kind="floors",
                                about=drawing)

    def _stand_apart(self, code: str, new: set[str], job: Job, by=None) -> None:
        """A new building whose drawing would put it on top of another of its site (or
        far off: a drawing with its own origin) is placed beside the others."""
        from .export import beside, site_footprint, site_positions

        with self._changing(code):
            ws = self.store.load(code, floors=[])
            moved = []
            for loc in ws.locations:
                for b in loc.buildings:
                    b_id = f"{ws.id}-{loc.code}-{b.code}"
                    if b_id not in new or b.site is not None or b.placement is not None:
                        continue
                    positions = site_positions(loc)
                    own = site_footprint(b, positions[b.code])
                    others = [g for o in loc.buildings if o is not b and (g := site_footprint(o, positions[o.code])) is not None]
                    if own is None or not others:
                        continue
                    near = unary_union(others)
                    if own.intersection(near).area < 0.01 * min(own.area, near.area) and own.distance(near) < FAR_APART_M:
                        continue  # stands apart already, as drawn (its drawing shares the site's coordinates)
                    _settle(loc, positions)
                    b.site = beside(loc, b)
                    moved.append(b_id)
                    job.say(f"{b.name}: its drawing put it on top of another building (or far off): placed beside them on the site plan")
            if moved:
                self.store.save(ws, floors=[], by=by, part="building", kind="site", targets=moved)

    def convert(self, code: str, floor: str | None = None, force: bool = False, by=None,
                editor: Editor | None = None, drawn: bool = False) -> Job:
        """Read floors' drawings again (one, or all). A reading that finds no rooms on a
        floor that has some, or would retire most of them, is held back and the floor
        keeps its rooms (convert.py); ``force`` applies it all the same. ``editor``: a
        person reading one floor again from a page, which waits until nobody else holds
        a room on it (Locked). ``drawn``: the floor read because a person drew on it (or
        undid that): the rooms people corrected that it retires are not held back for."""
        if not isinstance(force, bool):
            raise ValueError("force is true or false")
        self._known(code)
        if editor is not None and floor is not None:
            self.store.whole_floor(code, floor, by)

        def run(job: Job):
            ws = self.workspace(code)
            floors = [fid for *_, fid in ws.iter_floors(layouts=True) if floor in (None, fid)]
            # a building read for the first time (its first reading failed, say) is put
            # beside the others when its drawing would stack it on one of them
            unread = {f"{ws.id}-{loc.code}-{b.code}" for loc in ws.locations for b in loc.buildings
                      if all(f.converted_at is None for f in b.floors)}
            result = self._convert(code, floors, job, force=force, by=by, drawn=drawn)
            if unread:
                self._stand_apart(code, unread, job, by=by)
            return result

        return self.jobs.submit("Converting", run, project=code, user=_uid(by),
                                scope=("floor", floor) if floor is not None else ("project", None),
                                kind="read", floors=[floor] if floor is not None else None,
                                lane="drawn" if drawn and floor is not None else "steps")

    def _convert(self, code: str, floor_ids: list[str], job: Job, force: bool = False, by=None,
                 drawn: bool = False) -> dict:
        from .convert import apply_read, read_floor
        from .workers import models_settings

        job.floors = list(dict.fromkeys([*job.floors, *floor_ids]))  # (a page opened again shows them being read)
        self.reload_helpers()  # the GPU helpers as an admin last set them
        if self.model.available():
            job.say(f"reading texts with {self.model.name}")
        if self.symbols.available():
            job.say(f"spotting symbols with {self.symbols.name} (research use only)")
        if self.vision.available():
            job.say(f"looking at the rooms with {self.vision.name}")
        summaries, held, failed, why = [], [], [], {}
        read = []  # the floors read and saved
        vision = self.vision if self.vision.available() else None
        ws = self.store.load(code)
        floors = [f for f in floor_ids if ws.floor(f).source is not None]
        names = {drawing_name(ws.floor(f).source.path) for f in floors}
        side_by_side = self.readers is not None and len(floors) > 1
        settings = models_settings(self) if self.readers is not None else None
        noted = threading.Lock()
        with self._files(code, names) as folder:
            def one(fid: str) -> None:
                """A floor read and applied (or why not, in ``failed``)."""
                def say(line: str) -> None:  # (where a read stops when asked to)
                    job.check()
                    job.say(f"  {fid}: {line}" if side_by_side else "  " + line)

                job.check()
                job.say(f"converting {fid}")
                # A floor is read one read at a time, from the project as it is when its
                # read starts, and that read is applied to the project as it is when it
                # ends: what people changed meanwhile is kept (as any read keeps it), and
                # the project is held only while it is applied, a second or two.
                with StepLock(self.db, f"{fid} read"):
                    ws = self.store.load(code)
                    layout = ws.floor(fid).layout  # a location's layout (0.12): its site plan read here
                    # (what its drawing is: a layout's offset is chosen by its first read)
                    unread = {"sha256", "offset"} if layout else {"sha256"}
                    source = ws.floor(fid).source.model_dump(exclude=unread)
                    try:
                        if layout:
                            from .layout import read_layout

                            floor_read = read_layout(ws, fid, folder, self.model if self.model.available() else None,
                                                     say=say)
                        elif self.readers is not None:  # in a process of its own
                            floor_read, readings, answers = self.readers.read(fid, {
                                "ws": ws.model_dump_json(), "folder": str(folder), "tiles": str(self.cache / TILES),
                                "reads": str(self.cache / READS), "drawn": drawn, "models": settings},
                                say=say, stopping=lambda: job.stop_by is not None)
                            ws.readings.update(readings)
                            ws.vision.update(answers)
                        else:
                            floor_read = read_floor(ws, fid, folder, self.model, self.symbols, vision, say=say,
                                                    tiles=self.cache / TILES, drawn=drawn, reads=self.cache / READS)
                    except Exception as e:  # this floor stays as it was; the others are converted
                        # what the models answered meanwhile is kept, so reading it again asks only about the rest
                        self._keep_answers(code, ws, fid, by)
                        if isinstance(e, Stopped):
                            raise Stopped(f"{e}: {fid} stays as it was (what vision answered is kept: read again, "
                                          "it asks only about the rest)") from None
                        traceback.print_exc()
                        job.say(f"  {fid}: not converted: {_message(e)}")
                        with noted:
                            failed.append(f"{fid}: {_message(e)}")
                        return
                    with self._changing(code):
                        now = self.store.load(code)
                        floor = next((f for *_, f, i in now.iter_floors(layouts=True) if i == fid), None)
                        if floor is None or floor.source is None or \
                                floor.source.model_dump(exclude=unread) != source:
                            job.say(f"  {fid}: its drawing changed while it was read: not applied")
                            with noted:
                                failed.append(f"{fid}: its drawing changed while it was read")
                            return
                        now.readings.update(ws.readings)
                        now.vision.update(ws.vision)
                        # a read that would retire most of a floor is not applied: the floor keeps its rooms
                        if layout:
                            from .layout import apply_layout

                            report = apply_layout(now, floor_read, force=force)
                        else:
                            report = apply_read(now, floor_read, force=force, drawn=drawn)
                        self.store.save_floor(now, fid, by=by, more={"summary": report.summary(), "held": report.held})
                job.say("  " + report.summary())
                for w in report.warnings:
                    job.say(f"  {fid}: warning: {w}" if side_by_side else "  warning: " + w)
                with noted:
                    summaries.append(report.summary())
                    if report.held:
                        held.append(fid)
                        why[fid] = report.hold
                    read.append(fid)

            if side_by_side:  # each in its worker, on a core of its own
                with ThreadPoolExecutor(max_workers=min(len(floors), len(self.readers))) as pool:
                    for _ in pool.map(one, floors):
                        pass
            else:
                for fid in floors:
                    one(fid)
        if read and self._warm:
            self._print_ahead(code, read)
        if failed:  # the job fails, the floors converted kept
            raise ValueError(f"not converted: {'; '.join(failed)}")
        return {"summaries": summaries, "held": held, "why": why}

    def warm(self, code: str, floor_id: str) -> None:
        """A floor's read worked out in the background where Studio keeps none
        (convert.keep_a_read): the first wall a person draws on it is then found as
        quickly as the next. A floor once at a time, never while it is read."""
        from .convert import kept_read

        if not self._warm:
            return
        ws = self.workspace(code)
        f = next((f for *_, f, i in ws.iter_floors() if i == floor_id), None)
        if f is None or f.source is None or f.converted_at is None or kept_read(floor_id, f.source, self.cache / READS):
            return
        with self._lock:
            if floor_id in self._warming:
                return
            self._warming.add(floor_id)

        def run():
            from .convert import keep_a_read

            try:
                with StepLock(self.db, f"{floor_id} read"):
                    ws = self.workspace(code)
                    source = ws.floor(floor_id).source
                    with self._files(code, {drawing_name(source.path)}) as folder:
                        if self.readers is not None:  # in the worker that reads the floor: kept there
                            self.readers.keep(floor_id, {"ws": ws.model_dump_json(), "folder": str(folder),
                                                         "reads": str(self.cache / READS)})
                        else:
                            keep_a_read(ws, floor_id, folder, self.cache / READS)
            except Exception:  # (the first wall drawn reads the whole floor, as before)
                traceback.print_exc()
            finally:
                with self._lock:
                    self._warming.discard(floor_id)

        threading.Thread(target=run, name=f"warming {floor_id}", daemon=True).start()

    def close(self) -> None:
        """What Studio started stopped: the language model's server, the read workers,
        the prints being drawn ahead."""
        self._closing.set()
        if self.readers is not None:
            self.readers.close()
        self.model.close()

    def _keep_answers(self, code: str, ws: Workspace, fid: str, by) -> None:
        """What the models answered in a read of ``ws`` kept in the project, as it is now."""
        with self._changing(code):
            saved = self.store.load(code)
            saved.readings.update(ws.readings)
            saved.vision.update(ws.vision)
            self.store.save(saved, floors=[], by=by, part="project", kind="answers", targets=[fid])

    def _print_ahead(self, code: str, floor_ids: list[str]) -> None:
        """Floors' prints drawn now, in the background, where a read leaves them to be
        drawn (a drawing new, or the part of it read changed): without the drawing's
        texts (the one Review shows) and with them. A big floor opened in Review then
        waits for none (minutes, for a floor of a thousand rooms). One that fails is
        drawn when the floor is opened, saying why."""
        from psycopg_pool import PoolClosed

        from .review import floor_print

        def run():
            for fid in floor_ids:
                for text in (False, True):
                    if self._closing.is_set():
                        return
                    try:
                        floor_print(self.review(code), fid, text)
                    except PoolClosed:
                        return  # (Studio closed meanwhile, its database with it: nothing to say)
                    except Exception:
                        if self._closing.is_set():
                            return
                        traceback.print_exc()

        threading.Thread(target=run, name=f"prints of {code}", daemon=True).start()

    def move(self, code: str, building_id: str, body: dict, by=None) -> dict:
        """A building moved on its location's site plan: ``x``, ``y`` (metres from the
        site's centre) and ``rotation`` (degrees clockwise). The other buildings stay
        where they are on it."""
        from .export import site_positions
        from .workspace import SitePosition

        x, y, rotation = (_number(body, k) for k in ("x", "y", "rotation"))
        self._known(code)
        with self._changing(code):
            ws = self.store.load(code, floors=[])
            loc, b = self._building_of(ws, building_id)
            positions = _settle(loc, site_positions(loc))
            b.site = SitePosition(x=round(x, 3), y=round(y, 3), rotation=round(rotation % 360, 3), pivot=positions[b.code].pivot)
            self.store.save(ws, floors=[], by=by, part="building", kind="site", targets=[building_id])
        return {"site": b.site.model_dump()}

    def arrange(self, code: str, location_id: str, by=None) -> dict:
        """The location's buildings side by side on its site plan, left to right in the
        order of their codes, SITE_GAP_M apart, each as it is turned, centred on the
        site's centre line."""
        from .export import SITE_GAP_M, site_footprint, site_positions
        from .workspace import SitePosition

        self._known(code)
        with self._changing(code):
            ws = self.store.load(code, floors=[])
            loc = ws.location(location_id)
            positions = _settle(loc, site_positions(loc))
            cursor = None
            for b in sorted(loc.buildings, key=lambda b: b.code):
                at_centre = SitePosition(rotation=positions[b.code].rotation, pivot=positions[b.code].pivot)
                fp = site_footprint(b, at_centre)
                if fp is None:
                    continue
                x0, y0, x1, y1 = fp.bounds
                dx = 0.0 if cursor is None else cursor - x0
                b.site = SitePosition(x=round(dx, 3), y=round(-(y0 + y1) / 2, 3), rotation=at_centre.rotation, pivot=at_centre.pivot)
                cursor = x1 + dx + SITE_GAP_M
            self.store.save(ws, floors=[], by=by, part="project", kind="arrange", targets=[location_id])
        return {"sites": {b.code: b.site.model_dump() if b.site else None for b in loc.buildings}}

    def place_site(self, code: str, location_id: str, body: dict, by=None) -> dict:
        """The location's site on the map: its centre at ``lat``, ``lon``, its up at
        ``bearing``; every building not placed by itself goes with it. ``clear``
        takes it off the map. Its buildings keep the places they have on it: one
        changed or added later moves no other on the map."""
        from .export import site_positions

        self._known(code)
        with self._changing(code):
            ws = self.store.load(code, floors=[])
            loc = ws.location(location_id)
            if body.get("clear"):
                loc.placement = None
            else:
                lat, lon = _number(body, "lat"), _number(body, "lon")
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("latitude is -90…90 and longitude -180…180")
                _settle(loc, site_positions(loc))
                loc.placement = Placement(lat=lat, lon=lon, x=0.0, y=0.0, bearing=_number(body, "bearing", 0.0) % 360)
            self.store.save(ws, floors=[], by=by, part="project", kind="place", targets=[location_id])
        return {"placement": loc.placement.model_dump() if loc.placement else None}

    def _building_of(self, ws: Workspace, building_id: str):
        for loc in ws.locations:
            for b in loc.buildings:
                if f"{ws.id}-{loc.code}-{b.code}" == building_id:
                    return loc, b
        raise NotFound(f"no building {building_id}")

    def place(self, code: str, building_id: str, body: dict, by=None) -> dict:
        self._known(code)
        with self._changing(code):
            ws = self.store.load(code, floors=[])
            b = ws.building(building_id)
            lat, lon = _number(body, "lat"), _number(body, "lon")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("latitude is -90…90 and longitude -180…180")
            b.placement = Placement(lat=lat, lon=lon, x=_number(body, "x", 0.0), y=_number(body, "y", 0.0),
                                    bearing=_number(body, "bearing", 0.0) % 360)
            self.store.save(ws, floors=[], by=by, part="building", kind="place", targets=[building_id])
        return {"placement": b.placement.model_dump()}

    # ---- a location's layout (format 0.12: layout.py, siteplan.py) ---------------------------

    def add_layout(self, code: str, body: dict, by=None) -> Job:
        """A site plan of one of the project's drawings used as a location's layout:
        ``drawing``, its ``units`` (as its plans were found in), ``plan`` ({region, title});
        ``location_id`` (one of the project's), or ``location`` (a new one's name; with
        neither, the project's first location). A location that has a layout is given this
        plan in its place: what is on it keeps its IDs where it is still there. Read in a
        job: its plot, walls, gates, areas, footprints and north; the location's buildings
        matched to its footprints, none moved until a person says."""
        drawing = self._drawing(code, body.get("drawing", ""))
        units = body.get("units") or None
        if units is not None and not isinstance(units, str) or units and units not in UNIT_NAMES:
            raise ValueError(f"unknown units {units!r}: use one of {', '.join(UNIT_NAMES)}")
        plan = body.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("plan: the site plan to read ({region, title})")
        region = plan.get("region")
        if not isinstance(region, list) or len(region) != 4:
            raise ValueError("plan: its region, [x0, y0, x1, y1] in drawing units")
        region = tuple(_finite(v, "region") for v in region)
        if not (region[0] < region[2] and region[1] < region[3]):
            raise ValueError("plan: its region is x0, y0, x1, y1 with x0 < x1 and y0 < y1")
        title = plan.get("title") if isinstance(plan.get("title"), str) else None
        loc_id, loc_name = body.get("location_id") or None, (body.get("location") or "").strip() or None
        for key, value in (("location_id", loc_id), ("location", loc_name)):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{key} is text")
        if loc_id is not None:
            try:
                self.workspace(code).location(loc_id)
            except (KeyError, ValueError):
                raise ValueError(f"no location {loc_id}") from None

        def run(job: Job):
            with self._changing(code):
                if self.store.drawing(code, drawing) is None:
                    raise NotFound(f"no drawing {drawing}: it was deleted")
                ws = self.store.load(code, floors=[])
                where = loc_id
                if where is None and loc_name is not None:
                    same = next((x for x in ws.locations if x.name.strip().lower() == loc_name.lower()), None)
                    if same is not None:
                        where = make_id(ws.id, same.code)
                    else:
                        where = ws.add_location(_unique(_code(loc_name, "SITE"), {x.code for x in ws.locations}),
                                                loc_name)
                        job.say(f"location {loc_name} ({where})")
                if where is None:
                    if not ws.locations:
                        ws.add_location("SITE", ws.project.name)
                    where = make_id(ws.id, ws.locations[0].code)
                source = SourceDrawing(path=f"drawings/{drawing}", profile=AUTO, units=units, region=region,
                                       view=title)
                had = ws.layout_of(where)
                if had is not None and had.source is not None and had.source.path == source.path \
                        and tuple(had.source.region or ()) == region and had.source.units == units:
                    source.offset = had.source.offset  # the same plan: its frame as it was
                layout_id = ws.add_layout(where, source)
                self.store.save(ws, floors=[], by=by, part="project", kind="layout", targets=[layout_id],
                                more={"drawing": drawing})
            job.say(f"layout {layout_id}: {title or 'the site plan'}")
            result = self._convert(code, [layout_id], job, by=by)
            return {"layout": layout_id, **result}

        return self.jobs.submit(f"Reading the site plan in {drawing}", run, project=code, user=_uid(by), kind="layout",
                                about=drawing)

    def _location(self, code: str, location_id) -> None:
        """NotFound unless the project has this location."""
        self._known(code)
        try:
            self.workspace(code).location(location_id)
        except (KeyError, ValueError, TypeError):
            raise NotFound(f"no location {location_id}") from None

    def footprint(self, code: str, footprint_id: str, body: dict, by=None) -> dict:
        """A footprint of a location's layout said to be one of the location's buildings
        (``building``: its ID; "" or null: of none, skipped), that building placed on it so
        its ground floor lies on it; or a new building of the location made for it
        (``new``: true, with a ``name`` or the one its site plan gives), without floors. The
        location's other buildings stay where they are on its site."""
        from .layout import new_building, place
        from .review import BUSY_MESSAGE, BUSY_WAIT_S

        self._known(code)
        building = body.get("building")
        if building is not None and not isinstance(building, str):
            raise ValueError("building: a building's ID, or \"\" for none")
        if body.get("new") not in (None, False, True):
            raise ValueError("new: true or false")
        name = body.get("name")
        if name is not None and (not isinstance(name, str) or len(name) > 80):
            raise ValueError("name: a building's name, at most 80 characters")
        layout_id = footprint_id.rsplit("-", 1)[0]
        lock = self._changing(code)
        if not lock.acquire(timeout=BUSY_WAIT_S):
            raise Busy(BUSY_MESSAGE)
        try:
            ws = self.store.load(code, floors=[layout_id])
            try:
                if body.get("new"):
                    building = new_building(ws, footprint_id, name=name)
                    kind = "new building"
                else:
                    place(ws, footprint_id, building or None)
                    kind = "footprint"
            except KeyError as e:
                raise NotFound(str(e).strip("'")) from None
            b = ws.building(building) if building else None
            self.store.save(ws, floors=[layout_id], by=by, part="building", kind=kind,
                            targets=[x for x in (building, footprint_id) if x],
                            more={"building_name": b.name if b is not None else None})
            return {"footprint": footprint_id, "building": building or None,
                    "site": b.site.model_dump(mode="json") if b is not None and b.site is not None else None}
        finally:
            lock.release()

    def place_layout(self, code: str, location_id: str, body: dict | None = None, by=None) -> dict:
        """Every footprint of a location's layout put to its building, as matched (at least
        ``sure`` sure: the default, every match) or as a person said, each building placed
        on its footprint (Apply all): those standing on theirs already left as they are,
        unless ``again`` (placed anew, the layout read again)."""
        from .layout import MATCH_FLOOR, place_all

        body = body or {}
        sure = _finite(body.get("sure", MATCH_FLOOR), "sure")
        again = body.get("again", False)
        if not isinstance(again, bool):
            raise ValueError("again: true or false")
        self._location(code, location_id)
        with self._changing(code):
            ws = self.store.load(code, floors=[])
            layout = ws.layout_of(location_id)
            if layout is None:
                raise NotFound(f"{location_id} has no layout")
            layout_id = f"{location_id}-{LAND_CODE}-{layout.code}"
            ws = self.store.load(code, floors=[layout_id])
            placed = place_all(ws, location_id, again=again, sure=sure, confirm=True)
            if placed:
                self.store.save(ws, floors=[layout_id], by=by, part="building", kind="placed",
                                targets=[b for _, b in placed], more={"count": len(placed)})
        return {"placed": [{"footprint": f, "building": b} for f, b in placed]}

    def match_layout(self, code: str, location_id: str, by=None) -> Job:
        """The location's buildings matched to its layout's footprints again (a building's
        floors read since, say), the language model asked where words cannot say; nothing
        moved."""
        from .layout import match_buildings

        self._location(code, location_id)

        def run(job: Job):
            with self._changing(code):
                ws = self.store.load(code, floors=[])
                layout = ws.layout_of(location_id)
                if layout is None:
                    raise NotFound(f"{location_id} has no layout")
                layout_id = f"{location_id}-{LAND_CODE}-{layout.code}"
                ws = self.store.load(code, floors=[layout_id])
                found = match_buildings(ws, location_id, model=self.model if self.model.available() else None,
                                        say=job.say)
                self.store.save(ws, floors=[layout_id], by=by, part="floor", kind="read", targets=[layout_id],
                                more={"summary": f"{len(found)} footprint(s) matched"})
            return {"matched": found}

        return self.jobs.submit("Matching buildings to the layout", run, project=code, user=_uid(by), kind="match",
                                about=location_id)

    def set_north(self, code: str, location_id: str, body: dict, by=None) -> dict:
        """Which way north is on a location's site plan: ``bearing`` (degrees clockwise from
        north the site plan's up faces), as a person says; or ``from``: "drawing", as its
        layout's north arrow was read. On the map, the site's placement turns with it."""
        from .workspace import North, Placement

        self._location(code, location_id)
        with self._changing(code):
            ws = self.store.load(code, floors=[])
            loc = ws.location(location_id)
            if body.get("from") == "drawing":
                layout = ws.layout_of(location_id)
                if layout is None or layout.north is None:
                    raise ValueError("its site plan's north arrow was not found: give the bearing")
                north = North(bearing=layout.north.bearing % 360, source="drawing", sure=layout.north.sure)
            else:
                north = North(bearing=_number(body, "bearing") % 360, source="person")
            loc.north = north
            if loc.placement is not None:  # (the site on the map turns with it: the same bearing)
                loc.placement = Placement(**{**loc.placement.model_dump(), "bearing": north.bearing})
            self.store.save(ws, floors=[], by=by, part="project", kind="north", targets=[location_id],
                            more={"bearing": round(north.bearing, 2)})
        return {"north": north.model_dump(mode="json")}

    def export_building(self, code: str, body: dict | None = None) -> str:
        """The building a request to export names (``building``: its ID; may be left out
        when the project has one building); or a location's layout (``layout``: its ID, or
        its land's, 0.12): its land's ID, as a package of its own holds it."""
        from .export import land_ids

        layout = (body or {}).get("layout")
        if layout is None and is_land(str((body or {}).get("building") or "")):
            layout = body["building"]
        if layout is not None:
            if not isinstance(layout, str):
                raise ValueError("layout: a location's layout's ID")
            land = layout if layout.count("-") == 2 else layout.rsplit("-", 1)[0]
            if land not in land_ids(self.workspace(code)):
                raise ValueError(f"no layout {layout} in this project")
            return land
        building = (body or {}).get("building")
        if building is None and isinstance((body or {}).get("buildings"), list) and len(body["buildings"]) == 1:
            building = body["buildings"][0]  # as earlier pages sent it
        if building is not None and not isinstance(building, str):
            raise ValueError("building: a building's ID")
        from .export import building_ids

        known = building_ids(self.workspace(code))
        if building is None:
            if len(known) != 1:
                raise ValueError(f"choose the building to export: a package holds one building, this project has {len(known)}")
            building = known[0]
        elif building not in known:
            raise ValueError(f"no building {building} in this project")
        return building

    def export(self, code: str, body: dict | None = None, by=None) -> Job:
        """The package of one of the project's buildings (``building``: its ID; may be
        left out when the project has one building): made from the project as it is,
        entered in its export history, and kept as sent (its bytes, in the database).
        ``item_types``: "used" (its catalogue the types its items use) or "all" (the
        whole catalogue). ``drawings``: true, each floor's drawing goes in it too, as
        drawn, every layer and text (format 0.11: floor_views), and its history says so."""
        from .export import ITEM_TYPES

        building = self.export_building(code, body)
        item_types = (body or {}).get("item_types") or "used"
        if item_types not in ITEM_TYPES:
            raise ValueError(f"item_types: {' or '.join(ITEM_TYPES)}")
        drawings = (body or {}).get("drawings", False)
        if not isinstance(drawings, bool):
            raise ValueError("drawings: true or false")

        def run(job: Job):
            views = None
            if drawings:
                # each floor's drawing drawn first (a big floor's takes half a minute), and
                # kept: not while the project's other steps wait for the export
                floors = [(f"{building}-{f.code}", f.source) for f in self.store.current(code).building(building).floors
                          if f.source]
                job.say(f"the drawings of {len(floors)} floor(s): each drawn, unless it was before")
                self.floor_views(code, floors, job.say)

                def views(floors):  # (export_package's drawings: those made, or one changed since made now)
                    return [path.read_bytes() if path else None for path in self.floor_views(code, floors, job.say)]
            with self._changing(code):
                ws = self.store.load(code)
                seq = (ws.exports[-1].sequence + 1) if ws.exports else 1  # a project opened from export 5 goes on at 6
                # by code, with the building's (a layout's: its location's and LAND)
                own = "-".join(building.split("-")[1:3]) if is_land(building) else building.rsplit("-", 1)[-1]
                name = f"{ws.id}-{seq:03d}-{own}.storeypath"
                job.say(f"writing {name}")
                self.work.mkdir(parents=True, exist_ok=True)
                manifest, data = make_valid_package(ws, name, building, self.catalogue(), job.say, folder=self.work,
                                                    item_types=item_types, models=self.store.model_file,
                                                    drawings=views)
                drawn = sum(1 for s in manifest.sources if s.view)  # (the floors whose drawings it carries)
                self.store.save(ws, by=by, part="building", kind="export", targets=[building],
                                more={"file": name, "sequence": manifest.export.sequence,
                                      **({"drawings": drawn} if drawings else {})}, export_bytes={name: data})
            job.say("valid: " + ", ".join(f"{n} {k}" for k, n in manifest.counts.items()))
            loose = [b for b, p in manifest.placements.items() if not p.placed]
            if loose:
                job.say("not on the map yet (shapes are true, the position is not): " + ", ".join(loose))
            return {"file": name, "counts": manifest.counts, **({"drawings": drawn} if drawings else {})}

        return self.jobs.submit("Exporting", run, project=code, scope=("building", building), user=_uid(by),
                                kind="export", about=building)

    def floor_views(self, code: str, floors: list, say) -> list[Path | None]:
        """Each floor's part of its drawing as a package carries it (``floors``: [(floor
        ID, its SourceDrawing)]; drawview.write_floor_views), as a file kept in the cache
        by the drawing's content and the part, made once as a drawing drawn whole is
        (view): in a read worker when there are any, the floors of one drawing together.
        None for a floor whose drawing is not here."""
        out: list[Path | None] = [None] * len(floors)
        todo: dict[str, list[tuple[int, dict]]] = {}  # a drawing → its floors to draw: (where, the part)
        for i, (_, src) in enumerate(floors):
            name = drawing_name(src.path)
            info = self.store.drawing(code, name)
            if info is None:
                continue
            path = self._view_path(code, self._floor_view_key(info.get("sha256") or "", src))
            out[i] = path
            if path.is_file():
                path.touch()  # (the latest used are kept: _prune_views)
            else:
                todo.setdefault(name, []).append((i, {"out": str(path), "region": src.region, "offset": src.offset,
                                                      "units": src.units}))
        for name, parts in todo.items():
            say(f"drawing {', '.join(floors[i][0].rsplit('-', 1)[-1] for i, _ in parts)} from {name}")
            with self._files(code, [name]) as folder:
                task = {"drawing": str(folder / "drawings" / name), "floors": [part for _, part in parts]}
                if self.readers is not None:
                    self.readers.floor_views(name, task, say)
                else:
                    from .drawview import write_floor_views
                    from .vision import _DRAWING

                    write_floor_views(task["drawing"], task["floors"], say=say, lock=_DRAWING)
            for i, _ in parts:
                self._prune_views(code, keep=out[i])
        return out

    def _floor_view_key(self, sha: str, src) -> str:
        """A floor's part of a drawing in the cache (floor_views): the drawing's content,
        the part, and how Studio draws it."""
        from .drawview import VIEW_VERSION

        part = hashlib.sha1(json.dumps(["floor", src.region, src.offset, src.units]).encode()).hexdigest()[:12]
        return f"{sha[:32]}-{part}-{VIEW_VERSION}"

    def project_file(self, code: str) -> "Download":
        """The project as one file to send (*.storeypath-project): its workspace,
        drawings and item types with their 3D models, for another Studio to continue
        it (bundle.py). Not a package: other systems read a building's."""
        from .bundle import ProjectModel, write_project_file

        self._known(code)
        buf = io.BytesIO()
        with self._changing(code):
            ws = self.store.load(code)
            drawings = {}
            for d in self.store.drawings(code):
                drawings[d["name"]] = self.store.drawing_bytes(code, d["name"])
                if (kept := self.store.words(code, d["name"])) is not None:
                    drawings[d["name"] + WORDS] = kept.encode("utf-8")
            models = {}  # the item types' 3D models, each with the file it was made from and how
            for t_code, m in self.store.models().items():
                glb, src = self.store.model_file(t_code), self.store.model_source(t_code)
                if glb is not None and src is not None:
                    how = {"name": src["name"], "turn": src["turn"], "units": src["units"], "up": src["up"],
                           "size_before": m["size_before"]}
                    models[t_code] = ProjectModel(glb, src["source"], how)
            write_project_file(ws, drawings, self.catalogue(), buf, models=models)
        return Download(buf.getvalue(), f"{ws.id}.storeypath-project")

    def open(self, body: bytes, replace: str | None = None, allow=None, learn_types: bool = True, by=None) -> dict:
        """A project from a file (a building's package, or a project file): put in the
        database. A package of a project here adds its building to it; when that
        building is here already, or the file is a project file of a project here, it
        is put in its place only when the project's name is typed (``replace``). No
        job may be working on the project meanwhile.

        The file is read once (bundle.read_file: its parts name one project), and that
        project is the one checked and the one written: ``allow`` (the file as read,
        and whether its project is here) raises when the person may not, and may return
        what undoes what it did (a new project's owner) should the file not be opened
        after all. One file is opened at a time, so what was checked is still so when
        it is written. ``learn_types``: the item types its catalogue has and Studio's
        lacks are added (by who may change them), else listed as not added
        (``item_types_not_added``)."""
        from .bundle import open_into, read_file

        if not isinstance(body, bytes) or not body:
            raise ValueError("the file is empty")
        self.work.mkdir(parents=True, exist_ok=True)
        tmp = self.work / f".opening-{uuid.uuid4().hex}.storeypath"  # read as a file, then removed
        tmp.write_bytes(body)
        try:
            incoming = read_file(tmp)
            with self._opening:
                here = self.store.exists(incoming.code)
                undo = allow(incoming, here) if allow is not None else None
                try:
                    lock = self._changing(incoming.code) if here else None
                    if lock is not None and not lock.acquire(blocking=False):
                        raise ValueError("a job is working on this project: open the file when the job is done")
                    try:
                        try:
                            opened = open_into(self.store, tmp, incoming=incoming, existing=here,
                                               learn_types=learn_types, by=by)
                        except ProjectExists as e:
                            if replace is None:
                                raise
                            if replace.strip() != e.name.strip():
                                raise ValueError(f"type the name of the project here, {e.name}, to replace "
                                                 f"{'its building ' + e.building if e.building else 'it'}") from None
                            opened = open_into(self.store, tmp, replace=True, incoming=incoming, existing=here,
                                               learn_types=learn_types, by=by)
                    finally:
                        if lock is not None:
                            lock.release()
                except BaseException:
                    if undo is not None:
                        undo()
                    raise
                return opened
        except zipfile.BadZipFile:
            raise ValueError("not a StoreyPath file (.storeypath or .storeypath-project)") from None
        finally:
            tmp.unlink(missing_ok=True)

    def preview(self, code: str, building: str | None = None, sight: Sight | None = None) -> bytes:
        """The project as a package, as it is now, for the 3D view: built in memory
        and not entered as an export. With ``building``, that building alone; with
        ``sight``, only the floors that person may see (Sight.seen). Kept, by its tag
        (preview_tag): asked for again, it is not made again until what it holds changed."""
        from .export import ExportError, preview_package

        tag = self.preview_tag(code, building, sight)
        with self._lock:
            kept = self._previews.get(tag)
            if kept is not None:
                self._previews.move_to_end(tag)
                return kept
        ws = self.store.load(code)
        if sight is not None:
            ws = sight.seen(ws)
        if not any(f.converted_at for _, _, f, _ in ws.iter_floors(layouts=True)):
            raise NotFound("nothing converted yet: add floors first")
        buf = io.BytesIO()
        try:
            preview_package(ws, buf, buildings=[building] if building else None, catalogue=self.catalogue(),
                            models=self.store.model_file)
        except ExportError as e:
            raise NotFound(str(e)) from None
        data = buf.getvalue()
        with self._lock:  # (one changed while it was made: kept under the tag asked for, and made again next)
            self._previews[tag] = data
            while len(self._previews) > PREVIEWS_KEPT:
                self._previews.popitem(last=False)
        return data

    def preview_tag(self, code: str, building: str | None = None, sight: Sight | None = None) -> str:
        """What a preview is made from, as an HTTP ETag: the project's version, what of
        it the person sees, the catalogue's version (its types and their models) and
        this Studio. The same tag, the same package: a browser that has the package
        keeps it (it is answered "not changed", and nothing is sent)."""
        version = self.store.version(code)
        if version is None:
            raise NotFound(f"no project {code}")
        seen = None
        if sight is not None and not sight.whole:
            seen = (sorted(f for f, level in sight.floors.items() if level),
                    sorted(b for b, level in sight.buildings.items() if level))
        self.catalogue()  # (read first: Studio's first read of it may add the types it brings, moving its version on)
        said = json.dumps([code, building, version, seen, self.store.catalogue_version(), self._started], default=str)
        return '"' + hashlib.sha256(said.encode()).hexdigest()[:32] + '"'

    def navigation(self, code: str, building_id: str, start: str | None = None, end: str | None = None,
                   accessible: bool = False) -> dict:
        """A building's walking network as it is now (navigation.build_network: what its
        next package will hold), or the way on it from ``start`` to ``end`` (a node's,
        space's, zone's or item's ID; on lifts and ramps alone when ``accessible``). An
        item's ID may be as a person typed it (7k2q xm9f 4dp). Worked out once a version
        of the project."""
        from .navigation import NoRoute, route

        if (start is None) != (end is None):
            raise ValueError("a way needs both from and to")
        graph, items = self._network(code, building_id)
        if start is None:
            return {"building_id": building_id, **graph.nav}

        def known(ref: str) -> str:  # as given when the network has it, else an item's ID as typed
            if ref in graph.nodes or ref in graph.places or ref in items:
                return ref
            return normalize_item_id(ref) or ref

        start, end = known(start), known(end)
        try:
            return {"building_id": building_id, "route": route(graph, start, end, accessible=accessible, items=items)}
        except NoRoute as e:
            raise NotFound(str(e)) from None
        except KeyError as e:
            raise NotFound(str(e).strip("'\"")) from None

    def _network(self, code: str, building_id: str):
        from .navigation import Graph, build_network, items_in

        version = self.store.version(code)
        key = (code, building_id, version)
        with self._lock:
            kept = self._networks.get(key)
        if kept is not None:
            return kept
        ws = self.workspace(code)
        if building_id not in {make_id(ws.id, loc.code, b.code) for loc in ws.locations for b in loc.buildings}:
            raise NotFound(f"no building {building_id}")
        graph = Graph(build_network(ws, building_id, self.catalogue()))
        kept = (graph, items_in(ws, building_id))
        with self._lock:
            # one network a building, its latest version
            self._networks = {k: v for k, v in self._networks.items() if k[:2] != key[:2]}
            self._networks[key] = kept
        return kept

    def site_navigation(self, code: str, location_id: str, start: str | None = None, end: str | None = None,
                        accessible: bool = False, sight: Sight | None = None) -> dict:
        """A location's site's walking network as it is now (format 0.13: its layout's,
        navigation.build_site_network, with each of its buildings' the person may see whole:
        ``sight``), or the way across it from ``start`` to ``end``: a node's, an area's, a
        gate's, a room's or an item's ID (an item's as a person typed it), or a point on the
        land, "x,y" in metres of the site's frame. ``notes`` say why a building has no way in
        from the land; a way that cannot be found says why where it can."""
        from .navigation import NoRoute, site_route

        if (start is None) != (end is None):
            raise ValueError("a way needs both from and to")
        graph, items, notes, buildings = self._site_network(code, location_id, sight)
        if start is None:
            return {**graph.nav, "location_id": location_id, "buildings": buildings, "notes": notes}

        def known(ref: str):  # as given when the network has it, a point on the land, else an item's ID as typed
            if ref in graph.nodes or ref in graph.places or ref in items:
                return ref
            if POINT.fullmatch(ref):
                x, y = ref.split(",")
                return float(x), float(y)
            return normalize_item_id(ref) or ref

        try:
            way = site_route(graph, start=known(start), end=known(end), accessible=accessible, items=items, notes=notes)
        except NoRoute as e:
            raise NotFound(str(e)) from None
        except KeyError as e:
            raise NotFound(str(e).strip("'\"")) from None
        return {"location_id": location_id, "route": way}

    def _site_network(self, code: str, location_id: str, sight: Sight | None):
        """A location's site's network (its layout's and its buildings' as one), its items'
        places, why a building has no way in, and its buildings (each its ID and name): those
        ``sight`` sees whole (all, without it). Worked out once a version of the project."""
        from .export import floor_units, standing_in
        from .navigation import build_site_network, site_graph

        ws = self.workspace(code)
        try:
            loc = ws.location(location_id)
        except (KeyError, ValueError):
            raise NotFound(f"no location {location_id}") from None
        layout = ws.layout_of(location_id) if loc.layout is not None else None
        if layout is None or layout.converted_at is None:
            raise NotFound(f"{loc.name} has no layout read yet: a way across its site needs its land")
        layout_id = make_id(ws.id, loc.code, LAND_CODE, layout.code)
        seen = [make_id(ws.id, loc.code, b.code) for b in loc.buildings
                if any(f.converted_at for f in b.floors)
                and (sight is None or rank(sight.building(make_id(ws.id, loc.code, b.code))) >= rank("view"))]
        version = self.store.version(code)
        key = (code, location_id, version, tuple(seen))
        with self._lock:
            kept = self._networks.get(key)
        if kept is not None:
            return kept
        notes: list[dict] = []
        land = build_site_network(ws, layout_id, self.catalogue(), notes)
        items = {it.id: dict(zip(("space_id", "zone_id"), standing_in(it, floor_units(ws, layout_id))))
                 for it in ws.floor_items(layout_id)}
        navs = []
        for b_id in seen:
            graph, its = self._network(code, b_id)
            navs.append(graph.nav)
            items |= its
        names = {make_id(ws.id, loc.code, b.code): b.name for b in loc.buildings}
        buildings = [{"id": b, "name": names[b]} for b in seen]
        kept = (site_graph(land, navs), items, [n for n in notes if n["building_id"] in seen or sight is None],
                buildings)
        with self._lock:
            # one network a site, its latest version
            self._networks = {k: v for k, v in self._networks.items() if k[:2] != key[:2]}
            self._networks[key] = kept
        return kept

    def export_file(self, code: str, name: str) -> "Download":
        """A package of the project, as it was sent."""
        self._known(code)
        if not isinstance(name, str) or not name:
            raise NotFound(f"no export {name}")
        name = Path(name).name
        return Download(self.store.export_bytes(code, name), name)

    # ---- many people at once: undo, history, who edits a floor -------------------------

    def undo(self, code: str, floor: str | None = None, sight: Sight | None = None, by=None,
             editor: Editor | None = None, redo: bool = False) -> dict:
        """The person's latest change (on ``floor``) undone, or their latest undoing redone
        (Review.step_back): it needs edit on each floor it changed (as it did). What was
        drawn is read again (a job, as drawing does): ``job``."""
        if floor is not None and not isinstance(floor, str):
            raise ValueError("floor: a floor's ID")

        def check(row: dict) -> None:
            for f in row.get("floors") or []:
                if sight is not None and rank(sight.floor(f)) < rank("edit"):
                    raise Forbidden(f"undoing this needs edit access to {f}; you have {sight.floor(f) or 'none'}")

        done = self.review(code).step_back(floor, by=by, editor=editor, redo=redo, check=check)
        job = self.convert(code, done["floors"][0], by=_uid(by), drawn=True) if done.pop("read") and done["floors"] else None
        return {**done, "job": job}

    def history(self, code: str, floor: str | None = None, n=None, sight: Sight | None = None, by=None) -> dict:
        """The project's latest changes (on ``floor``), newest first, as the History panel
        lists them: those the person may see, each with who, when and what in words, and
        whether it was undone; and what they would undo and redo there now."""
        from . import history
        from .db.store import who_of

        self._known(code)
        try:
            n = max(0, min(int(n if n is not None else history.SHOWN), history.SHOWN_MAX))
        except (TypeError, ValueError):
            raise ValueError("n: how many changes, a whole number") from None
        visible = history.visible_to(sight)
        rows, seen, before = [], 0, None
        while len(rows) < n:  # those they may not see left out, until n are found (or many were looked at)
            batch = self.store.history(code, floor, limit=max(n * 2, 50), before=before)
            rows += [r for r in batch if visible(r)]
            seen += len(batch)
            if len(batch) < max(n * 2, 50) or seen > 20 * max(n, 1):
                break
            before = batch[-1]["seq"]
        rows = rows[:n]
        me = history.key(who_of(by))
        undone = self.store.undone(code, [r["seq"] for r in rows]) if rows else set()
        return {"entries": [history.shown(r, me, undone) for r in rows],
                **self.review(code).steps(by, floor, visible)}

    def editing(self, code: str, floor: str) -> dict:
        """Who is editing a floor, which rooms: {editors: [{who: {id, username, name},
        since, rooms}], rooms: {room: {who, since}}} (store.locks), empty when nobody."""
        return self.store.locks(code, [floor]).get(floor) or {"floor": floor, "editors": [], "rooms": {}}

    def release(self, code: str, floor: str, by=None) -> dict:
        """The person done editing a floor (or gone from it): their rooms on it let go."""
        return {"released": self.store.release_lock(code, floor, by)}

    def take_over(self, code: str, floor: str, by=None, editor: Editor | None = None, room: str | None = None) -> dict:
        """A room another person holds (or all others hold on the floor) taken over by an
        admin: the people it was taken from."""
        if room is not None and not isinstance(room, str):
            raise ValueError("room is a room's ID")
        return {"from": self.store.take_over(code, floor, by, editor, room)}

    def delete_floor(self, code: str, floor_id: str, body: dict, by=None) -> dict:
        """A floor taken out of its building: only when its name is typed to confirm, while
        nobody else is editing a room of it (Locked) and no job is changing the project
        (Busy). Its rooms, doors and items are retired, never erased: their IDs are never
        issued again (the building's counter goes on), and the building's next package
        lists them retired. Its drawing stays (other floors may be drawn on it). One
        change, recorded, that no undo goes back over."""
        from .review import BUSY_MESSAGE, BUSY_WAIT_S
        from .workspace import utcnow

        try:
            name = self.workspace(code).floor(floor_id).name
        except (KeyError, ValueError):
            raise NotFound(f"no floor {floor_id}") from None
        confirm = body.get("confirm")
        if not isinstance(confirm, str) or confirm.strip() != name.strip():
            raise ValueError("type the floor's name to delete it")
        self.store.whole_floor(code, floor_id, by)
        lock = self._changing(code)
        if not lock.acquire(timeout=BUSY_WAIT_S):
            raise Busy(BUSY_MESSAGE)
        try:
            ws = self.store.load(code, floors=[floor_id])
            b = ws.building(floor_id.rsplit("-", 1)[0])
            f = ws.floor(floor_id)
            standing = sorted((m for m in b.floors if m.mezzanine and m.host == f.code), key=floor_order)
            if standing:  # (each stands in it: its level is this floor's and its own)
                raise ValueError(f"{f.name} holds " + ", ".join(f"{m.name} ({m.code})" for m in standing)
                                 + ": delete " + ("that mezzanine" if len(standing) == 1 else "those mezzanines")
                                 + " first")
            now = utcnow()
            rooms = items = 0
            for r in ws.floor_objects(floor_id):
                r.status, r.retired_at = "retired", now
                rooms += r.kind == "space"
            for it in ws.items.values():
                if it.floor_id == floor_id and it.status == "active":
                    it.status, it.retired_at = "retired", now
                    items += 1
            b.floors = [x for x in b.floors if x.code != f.code]
            self.store.save(ws, floors=[floor_id], by=by, part="building", kind="floor deleted", targets=[floor_id],
                            more={"floor_name": f.name, "rooms": rooms, "items": items, "barrier": True})
        finally:
            lock.release()
        for png in [*self.cache.glob(f"prints/{code}/{floor_id}-*"), *self.cache.glob(f"prints/{code}/{floor_id}~*")]:
            png.unlink(missing_ok=True)  # its drawing's pictures: none is asked for again
        return {"deleted": floor_id, "name": f.name, "rooms": rooms, "items": items}

    # ---- the GPU helpers (vision.py), kept in the database ------------------------------

    HELPERS = "vision_helpers"  # settings key: {"helpers": [{url, key, enabled, parallel}]}

    def _stored_helpers(self) -> list[dict] | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = %s", (self.HELPERS,)).fetchone()
        return list(row[0].get("helpers") or []) if row and isinstance(row[0], dict) else None

    def reload_helpers(self) -> bool:
        """The vision model's helpers as the database says (when it says: else as
        STOREYPATH_VISION_URL said at the start), read again when they changed there:
        whether they did."""
        try:
            said = self._stored_helpers()
        except Exception:  # noqa: BLE001 (no database to ask: as they are)
            return False
        configure = getattr(self.vision, "configure", None)
        if said is None or said == self._helpers_said or configure is None:
            return False
        self._helpers_said = said
        configure(said)
        return True

    def helpers(self) -> dict:
        """The GPU helpers, as the admin page shows them: each one's address, whether it
        has a key (never the key), whether it is used, the questions it takes at once, how
        it is (answers, left out, not answering), the model it serves and how it is reached
        (``tls``: plain HTTP, HTTPS with its certificate checked or not)."""
        from .vision import TLS_PLAIN

        self.reload_helpers()
        stored = self._stored_helpers()
        said = stored if stored is not None else self._helpers_from_env
        if any(h.get("enabled", True) for h in said):
            self.vision.available()  # their lists of models read (again, for those due)
        state = {h["url"]: h for h in self.vision.helper_states()}
        out = []
        for h in said:
            s = state.get(h["url"], {})
            out.append({"url": h["url"], "key": bool(h.get("key")), "enabled": h.get("enabled", True),
                        "parallel": h.get("parallel") or 2, "state": s.get("state", "off" if not h.get("enabled", True)
                                                                         else "not asked yet"),
                        "error": s.get("error"), "models": s.get("models"), "busy": s.get("busy", 0),
                        "tls": s.get("tls") or (TLS_PLAIN if h["url"].lower().startswith("http://") else None)})
        return {"helpers": out, "model": self.vision.model or None, "from": "database" if stored is not None
                else "environment" if said else "none"}

    def save_helpers(self, body: dict) -> dict:
        """The GPU helpers replaced: ``helpers``, each {url, key?, enabled, parallel}; a key
        left out keeps the one that helper (by its address) has. Used at once."""
        from .vision import MAX_PARALLEL, helper_urls

        helpers = body.get("helpers")
        if not isinstance(helpers, list) or len(helpers) > 32:
            raise ValueError("helpers: a list of {url, key, enabled, parallel} (32 at most)")
        before = {h["url"]: h for h in (self._stored_helpers() or self._helpers_from_env)}
        out, seen = [], set()
        for h in helpers:
            if not isinstance(h, dict) or not isinstance(h.get("url"), str):
                raise ValueError("each helper is {url, key, enabled, parallel}")
            urls = helper_urls(h["url"])
            if len(urls) != 1 or not urls[0].lower().startswith(("http://", "https://")):
                raise ValueError(f"a helper's address is one http:// or https:// address: {h['url']!r}")
            url = urls[0]
            if url in seen:
                raise ValueError(f"{url} is listed twice")
            seen.add(url)
            key = h.get("key")
            if key is None:
                key = before.get(url, {}).get("key") or ""
            if not isinstance(key, str) or len(key) > 512:
                raise ValueError("a helper's key is text")
            parallel = h.get("parallel", 2)
            if isinstance(parallel, bool) or not isinstance(parallel, int) or not 1 <= parallel <= MAX_PARALLEL:
                raise ValueError(f"places at once: a whole number from 1 to {MAX_PARALLEL}")
            enabled = h.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError("enabled is true or false")
            out.append({"url": url, "key": key.strip(), "enabled": enabled, "parallel": parallel})
        from psycopg.types.json import Jsonb

        with self.db.transaction() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET "
                         "value = EXCLUDED.value", (self.HELPERS, Jsonb({"helpers": out})))
        self.reload_helpers()
        return self.helpers()

    def test_helper(self, body: dict) -> dict:
        """A sample room sent to one helper (``url``; its key as kept, or ``key``), as a
        conversion would ask it: what it answered, its model, how long it took, and how
        it was reached (``tls``, as ``helpers`` says it)."""
        from .vision import VisionModel, sample_question

        url = body.get("url")
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            raise ValueError("url: the helper's http:// or https:// address")
        kept = {h["url"]: h for h in (self._stored_helpers() or self._helpers_from_env)}.get(url.rstrip("/"), {})
        key = body.get("key") if isinstance(body.get("key"), str) else kept.get("key") or ""
        one = VisionModel(url=url, parallel=1, timeout=120.0)
        one.key = key  # this helper's own, as kept (not the environment's)
        for h in one.helpers:
            h.key = key
        started = datetime.now(timezone.utc)

        def tls():
            return one.helpers[0].tls if one.helpers else None

        if not one.available():
            return {"ok": False, "error": one.failed, "model": None, "tls": tls()}
        try:
            answer = sample_question(one)
        except Exception as e:  # noqa: BLE001 (said on the page)
            return {"ok": False, "error": str(e), "model": one.model, "tls": tls()}
        took = (datetime.now(timezone.utc) - started).total_seconds()
        return {"ok": True, "model": one.model, "answer": answer, "seconds": round(took, 2), "tls": tls()}


def _already(same: dict) -> dict:
    """The answer to a file sent that the project has already: the drawing it is, and when
    that was added (nothing is kept again)."""
    added = same.get("uploaded_at")
    return {"drawing": same["name"], "already": True, "added": added.isoformat() if added else None}


def _and(words: list[str], last: str = " and ") -> str:
    """Words listed as a sentence does: "a", "a and b", "a, b and c"."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + last + words[-1]


def _footprint_shape(ws: Workspace, b):
    """The footprint of its location's layout a building stands on (0.12), in its own
    frame: for a building with no floors yet, placed there with the site's frame as its own."""
    from shapely import affinity

    if b.site is None or not b.site.footprint:
        return None
    r = ws.objects.get(b.site.footprint)
    if r is None or r.status != "active":
        return None
    # (its own frame from the site's: the site position turned back)
    pos = b.site
    g = affinity.translate(shape(r.geometry), -pos.x, -pos.y)
    g = affinity.rotate(g, pos.rotation, origin=(0, 0))
    return affinity.translate(g, pos.pivot[0], pos.pivot[1])


def _layout_page(ws: Workspace, loc, items: dict) -> dict:
    """A location's layout as the project's page shows it (0.12): its site plan, how it
    was read, its plot, walls, areas, gates and footprints in the site's metres, each
    footprint with the building it is (a person's choice, else the one standing on it) and
    what it was matched to, the north its arrow says, and what is on it."""
    from .export import footprint_of
    from .layout import MATCH_FLOOR

    f = loc.layout.floors[0]
    f_id = make_id(ws.id, loc.code, loc.layout.code, f.code)
    objects = ws.floor_objects(f_id)
    standing = {b.site.footprint: make_id(ws.id, loc.code, b.code) for b in loc.buildings
                if b.site is not None and b.site.footprint}
    prints = []
    for r in sorted((r for r in objects if r.kind == "footprint"), key=lambda r: r.id):
        o = ws.overrides.get(r.id)
        prints.append({"id": r.id, "name": r.name, "label": r.label, "rings": _rings(shape(r.geometry)),
                       "issues": list(r.issues),
                       "building": footprint_of(ws, r), "chosen": o.building if o is not None else None,
                       "standing": standing.get(r.id), "match": r.match})
    return {
        "id": f_id, "land": f_id.rsplit("-", 1)[0], "name": f.name,
        "drawing": Path(f.source.path).name if f.source else None, "view": f.source.view if f.source else None,
        "region": list(f.source.region) if f.source and f.source.region else None,
        "offset": list(f.source.offset) if f.source and f.source.offset else None,
        "units": f.source.units if f.source else None, "converted": f.converted_at is not None,
        "warnings": f.warnings, "plot": _rings(shape(f.outline)) if f.outline else None,
        "walls": _rings(shape(f.walls)) if f.walls else None, "wall_height": f.height,
        "areas": [{"id": r.id, "type": ws.effective(r)["type"], "name": ws.effective(r)["name"],
                   "rings": _rings(shape(r.geometry))} for r in objects if r.kind == "space"],
        "gates": [{"id": r.id, "span": r.span, "width": r.width} for r in objects if r.kind == "opening"],
        "footprints": prints, "match_floor": MATCH_FLOOR,
        "north": f.north.model_dump(mode="json") if f.north else None,
        "items": items.get(f_id, 0),
    }


def _owning(name: str) -> str:
    """A name as what owns something: HQ's, Offices'."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _in_use(ws: Workspace, name: str) -> tuple[str | None, list[str]]:
    """Why a drawing may not be deleted, in words: the floors that read from it, by their
    buildings' names and their own ("drawing-9.dxf is the drawing of Offices' Ground floor
    and First floor: …"); and their IDs. (None, []) when no floor does."""
    buildings: dict[tuple[str, str], tuple[str, list[str]]] = {}
    ids = []
    for loc, b, f, f_id in ws.iter_floors(layouts=True):
        if f.source is not None and drawing_name(f.source.path) == name:
            owner = f"{loc.name} land" if f.layout else b.name or b.code
            buildings.setdefault((loc.code, b.code), (owner, []))[1].append(f.name or f.code)
            ids.append(f_id)
    if not ids:
        return None, []
    floors = _and([f"{_owning(b)} {_and(names)}" for b, names in buildings.values()], last=", and ")
    them = "that floor" if len(ids) == 1 else "those floors"
    return (f"{name} is the drawing of {floors}: give {them} another drawing first (Find plans in that "
            "drawing, then Replace its drawing), then delete this one"), ids


def _uid(by) -> str | None:
    """The id of who started a job (a user, or their id; None on this computer without
    accounts)."""
    if by is None or isinstance(by, str):
        return by
    return None if by is LOCAL else getattr(by, "id", None)


WRITING = ".writing-"  # a package being written, beside where it goes, until it is found valid


def package_buildings(ws: Workspace, name: str) -> list[str] | None:
    """The buildings a package of the project's exports holds, as the export that
    wrote it entered them; None when no export entered it (or it held the whole project,
    as packages before one building a package did)."""
    record = next((r for r in reversed(ws.exports) if r.file == Path(name).name), None)
    return list(record.buildings) if record is not None and record.buildings else None


def make_valid_package(ws: Workspace, name: str, building: str | None, catalogue, say, folder=None,
                       item_types: str = "used", models=None, drawings=None):
    """A building's package made (in a folder of its own, removed after) and checked; when
    it is valid, entered as an export in ``ws`` (not saved) and given back with its
    bytes: (manifest, bytes). One that is not valid is not entered (its number is not
    used up): ValueError, saying what is wrong. ``item_types``, ``models``, ``drawings``:
    export_package's."""
    import shutil
    import tempfile

    from .export import export_package
    from .validate import validate_package

    where = Path(tempfile.mkdtemp(prefix=WRITING, dir=folder))
    try:
        part = where / name  # the name it is entered under
        before = list(ws.exports)
        if is_land(building or ""):  # a location's layout (0.12): a package of its own
            from .export import export_layout_package

            manifest = export_layout_package(ws, part, building, say=say, catalogue=catalogue, item_types=item_types,
                                             models=models, drawings=drawings)
        else:
            manifest = export_package(ws, part, building=building, say=say, catalogue=catalogue,
                                      item_types=item_types, models=models, drawings=drawings)
        errors = validate_package(part)
        for e in errors:
            say("invalid: " + e)
        if errors:
            ws.exports[:] = before
            raise ValueError("the package failed validation: it was not kept, nor entered as an export")
        return manifest, part.read_bytes()
    finally:
        shutil.rmtree(where, ignore_errors=True)


def write_valid_package(ws: Workspace, ws_path: Path, out: Path, building: str | None, catalogue, say,
                        item_types: str = "used", models=None, drawings=None):
    """A building's package written to ``out`` and entered as an export in the workspace
    (saved to ``ws_path``) only when it is valid (make_valid_package). Raises
    ValueError, saying what is wrong, when it is not. (The command line's: files.)"""
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest, data = make_valid_package(ws, out.name, building, catalogue, say, folder=out.parent, item_types=item_types,
                                        models=models, drawings=drawings)
    part = out.with_name(f"{WRITING}{out.name}")
    part.write_bytes(data)
    part.replace(out)
    try:
        ws.save(ws_path)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return manifest


FAR_APART_M = 2000.0  # a building drawn this far from the others has a drawing of its own origin


def _settle(loc, positions: dict) -> dict:
    """Every building of the location keeps the place it has on the site plan now (as
    drawn, until then): moving one moves no other; and a building added later as
    drawn stands where its drawing puts it relative to them."""
    if loc.site_origin is None:
        for b in loc.buildings:
            if b.site is None and b.code in positions:
                p = positions[b.code]
                loc.site_origin = (round(p.pivot[0] - p.x, 4), round(p.pivot[1] - p.y, 4))
                break
    for b in loc.buildings:
        if b.site is None:
            b.site = positions[b.code]
    return positions


def _not_a_number(token: str):
    raise ValueError(f"{token} is not a number JSON has")


def _number(body: dict, key: str, default: float | None = None) -> float:
    return _finite(body.get(key, default), key)


def _rings(geom) -> list | None:
    """A footprint's outer rings, simplified, for drawing it small: [[[x, y], …], …]."""
    if geom is None or geom.is_empty:
        return None
    geom = geom.simplify(0.05)
    polys = getattr(geom, "geoms", [geom])
    return [[[round(x, 2), round(y, 2)] for x, y in p.exterior.coords] for p in polys if hasattr(p, "exterior")]


MIN_ALIGN_OVERLAP = 0.25  # floors from two drawings are moved to line up only when this much of their walls do


@dataclass
class Download:
    """A file made on the fly, for the browser to save under ``name``."""

    data: bytes
    name: str


LEVEL_ORDER = (FloorType.FLOOR, FloorType.MEZZANINE, FloorType.ROOF)  # the order plans are added in


class FloorPlace(NamedTuple):
    """Where a chosen plan goes: its location and building (an ID when they exist,
    else a new code and name), its floor, and the floor it replaces (its ID) when it
    is a new drawing of a floor the building has. What level it is (``type``): a
    mezzanine's floor is the one it stands in (its host); a mezzanine's code and a
    roof's are their own (``code``)."""

    location_id: str | None
    location: tuple[str, str] | None  # (code, name) of a new location
    building_id: str | None
    building: tuple[str, str] | None  # (code, name) of a new building
    ordinal: int
    plan: dict
    replaces: str | None
    type: FloorType = FloorType.FLOOR
    code: str | None = None  # the floor's code (a mezzanine's or a roof's: as chosen, else its default)


def _floors_to_add(ws: Workspace, plans: list[dict]) -> list[FloorPlace]:
    """Where each chosen plan goes. A plan names its location and building by ID
    (``location_id``, ``building_id``) or as new ones by name (``location``,
    ``building``; a building named like one of the location's is that one); without
    any, the project's first location, and a building of that name or "Main
    building". New ones get codes from their names that no other has. A plan on a
    floor the building has replaces that floor's drawing (its spaces keep their IDs)
    when it says ``replace``. A plan's ``type`` is the level it is: a floor (by
    default); a mezzanine, standing in the building's floor of its ordinal (there, or
    among these plans), ``above_host`` metres above it, with a ``code`` of its own (by
    default M00 over F00, M00B for a second); or a roof, above every floor of the
    building (its own and these plans'), with a ``code`` of its own (by default RF, RF2
    for a second). A mezzanine or a roof replaces the drawing of the building's own of
    that code when it says ``replace``. A floor added at or above a roof puts the roof
    above it. Fails, before anything changes, when two plans would be the same floor
    (or two of them one code), a plan a floor that exists and is not to be replaced, a
    mezzanine a floor the building does not have, or a roof not above every floor."""
    loc_codes = {loc.code for loc in ws.locations}
    new_locations: dict[str, tuple[str, str]] = {}  # name (lower) -> (code, name)
    new_buildings: dict[tuple[str, str], tuple[str, str]] = {}  # (location key, name lower) -> (code, name)
    chosen: dict[tuple[str, int], str] = {}  # (building, ordinal) -> the plan that is that floor
    codes: dict[str, dict[str, str]] = {}  # building -> code -> what has it (a floor, or a plan)
    later = []  # (index in out, building key, building, its name, title): mezzanines' hosts, roofs' places
    out = []
    for p in plans:
        if not isinstance(p, dict):
            raise ValueError("each plan is an object")
        for key in ("title", "name", "location", "location_id", "building", "building_id", "building_code"):
            if p.get(key) is not None and not isinstance(p[key], str):
                raise ValueError(f"a plan's {key} is text")
        title = p.get("title") or f"plan {p.get('index')}"
        _plan_numbers(p, title)
        # the location
        loc_id, new_loc, loc = p.get("location_id") or None, None, None
        if loc_id:
            try:
                loc = ws.location(loc_id)
            except KeyError:
                raise ValueError(f"{title}: no location {loc_id}") from None
        elif (p.get("location") or "").strip():
            name = p["location"].strip()
            loc = next((x for x in ws.locations if x.name.strip().lower() == name.lower()), None)
            if loc is not None:
                loc_id = make_id(ws.id, loc.code)
            else:
                if name.lower() not in new_locations:
                    new_locations[name.lower()] = (_unique(_code(name, "SITE"), loc_codes), name)
                new_loc = new_locations[name.lower()]
        elif ws.locations:
            loc = ws.locations[0]
            loc_id = make_id(ws.id, loc.code)
        else:
            if "" not in new_locations:
                new_locations[""] = ("SITE", ws.project.name)
            new_loc = new_locations[""]
        loc_key = loc_id or f"new:{new_loc[0]}"
        # the building
        b_id, new_b, building = p.get("building_id") or None, None, None
        if b_id:
            try:
                building = ws.building(b_id)
            except KeyError:
                raise ValueError(f"{title}: no building {b_id}") from None
            if loc_id and not b_id.startswith(loc_id + "-"):
                raise ValueError(f"{title}: building {b_id} is not in location {loc_id}")
        else:
            name = (p.get("building") or "Main building").strip()
            building = next((b for b in (loc.buildings if loc else []) if b.name.strip().lower() == name.lower()), None)
            if building is not None:
                b_id = f"{loc_id}-{building.code}"
            else:
                key = (loc_key, name.lower())
                if key not in new_buildings:
                    taken = {b.code for b in (loc.buildings if loc else [])} | {c for (k, _), (c, _) in new_buildings.items() if k == loc_key}
                    new_buildings[key] = (_unique(_code(p.get("building_code") or name), taken), name)
                new_b = new_buildings[key]
        b_key = b_id or f"{loc_key}:{new_b[0]}"
        b_name = building.name if building is not None else new_b[1]
        # the floor
        ordinal = int(p["ordinal"])
        level = FloorType(p.get("type") or FloorType.FLOOR)
        floors = building.floors if building is not None else []
        taken = codes.setdefault(b_key, {f.code: f"{f.name} ({f.code})" for f in floors})
        if level != FloorType.FLOOR:  # a mezzanine or a roof: by its code
            what = "mezzanine" if level == FloorType.MEZZANINE else "roof"
            code = p.get("code") or None
            if code is not None:
                try:
                    validate_segment(code)
                except ValueError:
                    raise ValueError(f"{title}: its code is 1 to 16 capital letters or digits, no hyphens ({code!r})") from None
            has = next((f for f in floors if f.type == level and f.code == code
                        and (level == FloorType.ROOF or f.ordinal == ordinal)), None)
            if has and not p.get("replace"):
                where = f" on floor {ordinal}" if level == FloorType.MEZZANINE else ""
                raise ValueError(f"{b_name} already has {has.name} ({has.code}){where}: give {title} another code, "
                                 f"or replace that {what}'s drawing")
            code = code or (default_mezzanine_code(ordinal, taken) if level == FloorType.MEZZANINE
                            else default_roof_code(taken))
            if has is None and code in taken:
                raise ValueError(f"{b_name}: the code {code} is {taken[code]}'s: give {title} another code")
            taken[code] = title
            later.append((len(out), b_key, building, b_name, title))
            out.append(FloorPlace(loc_id, new_loc, b_id, new_b, ordinal, p, f"{b_id}-{has.code}" if has else None,
                                  level, code))
            continue
        if (b_key, ordinal) in chosen:
            raise ValueError(f"{chosen[b_key, ordinal]} and {title} are both floor {ordinal} of {b_name}: give one "
                             "of them another floor or building, or leave it out")
        # (a mezzanine on that floor is not that floor; a roof there goes up above it)
        has = next((f for f in floors if f.ordinal == ordinal and f.type == FloorType.FLOOR), None)
        if has and not p.get("replace"):
            raise ValueError(f"{b_name} already has floor {ordinal} ({has.name}): give {title} another floor, "
                             "or replace that floor's drawing")
        code = has.code if has else default_floor_code(ordinal)
        if has is None and code in taken:
            raise ValueError(f"{b_name}: the code {code} is {taken[code]}'s: give {title} another floor")
        taken[code] = title
        chosen[b_key, ordinal] = title
        out.append(FloorPlace(loc_id, new_loc, b_id, new_b, ordinal, p, f"{b_id}-{has.code}" if has else None,
                              FloorType.FLOOR, code))
    for at, b_key, building, b_name, title in later:
        place = out[at]
        floors = building.floors if building is not None else []
        if place.type == FloorType.MEZZANINE:  # in a floor: the building's, or one of these plans
            if (b_key, place.ordinal) not in chosen and not any(f.ordinal == place.ordinal and f.type == FloorType.FLOOR
                                                                for f in floors):
                raise ValueError(f"{title} is a mezzanine of floor {place.ordinal}, which {b_name} does not have: add "
                                 "that floor too, or choose the floor it stands in")
            continue
        if place.replaces:  # (a roof's drawing replaced: it stays where it is)
            continue
        # a roof: above every floor, the building's and these plans'; one level of its floor
        top = max([f.ordinal for f in floors if f.type == FloorType.FLOOR]
                  + [n for (k, n) in chosen if k == b_key], default=None)
        if top is not None and place.ordinal <= top:
            raise ValueError(f"{title} is a roof: give it a floor above {b_name}'s top floor ({top}), "
                             f"{top + 1} or more")
        other = chosen.get((b_key, place.ordinal)) or next(
            (f"{f.name} ({f.code})" for f in floors if f.ordinal == place.ordinal and f.roof), None)
        if other is not None:
            raise ValueError(f"{other} and {title} are both floor {place.ordinal} of {b_name}: give one of them "
                             "another floor")
        chosen[b_key, place.ordinal] = title
    return out


def _plan_numbers(p: dict, title: str) -> None:
    """A chosen plan's numbers, checked (and made numbers) before anything changes: its
    region, four finite numbers; its floor, a whole number; its height and parapet,
    when given, metres more than nothing."""
    if p.get("region") is not None:
        region = p["region"]
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ValueError(f"{title}: its region is [x0, y0, x1, y1]")
        p["region"] = [_finite(v, f"{title}: its region") for v in region]
    ordinal, whole = p.get("ordinal"), None
    if isinstance(ordinal, int) and not isinstance(ordinal, bool):
        whole = ordinal
    elif isinstance(ordinal, float) and ordinal.is_integer():  # neither NaN nor an infinity
        whole = int(ordinal)
    elif isinstance(ordinal, str) and re.fullmatch(r"\s*-?\d{1,4}\s*", ordinal):
        whole = int(ordinal)
    if whole is None or not -50 <= whole <= 500:
        raise ValueError(f"{title}: its floor is a whole number")
    p["ordinal"] = whole
    for key in ("height", "parapet", "above_host"):
        if p.get(key) not in (None, "", 0):
            value = _finite(p[key], f"{title}: its {key}")
            if not 0 < value <= 100:
                raise ValueError(f"{title}: its {key} is metres, more than 0 and at most 100")
            p[key] = value
    if p.get("type") not in (None, *(t.value for t in FloorType)):
        raise ValueError(f"{title}: its type is one of {', '.join(t.value for t in FloorType)}")
    if p.get("code") is not None and not isinstance(p["code"], str):
        raise ValueError(f"{title}: its code is text")
    if p.get("code") is not None:
        p["code"] = p["code"].strip().upper() or None


def _finite(value, what: str) -> float:
    """A finite number from a request: NaN and the infinities are not numbers here."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"{what} is a number")
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{what} is a number") from None
    if not math.isfinite(v):
        raise ValueError(f"{what} is a number")
    return v


def _unique(code: str, taken: set[str]) -> str:
    """A code no other has: "Main building" and "Main kitchen" are MAIN and MAIN2."""
    out, n = code, 2
    while out in taken:
        out, n = f"{code[:14]}{n}", n + 1
    return out


def _words_of(drawing: Path) -> Path:
    return drawing.with_name(drawing.name + WORDS)


def _code(text: str, default: str = "B1") -> str:
    """A code from a name: its first word ("Main building" → MAIN)."""
    words = re.findall(r"[A-Z0-9]+", text.upper())
    return words[0][:8] if words else default


# ---- who may do what ---------------------------------------------------------------

@dataclass
class LoggedIn:
    """A session begun: its token goes into the cookie, and who it is of back to the page."""

    token: str
    data: dict


@dataclass
class LoggedOut:
    """A session ended: the cookie goes."""


@dataclass
class Stream:
    """A download written as it is made (a backup): no Content-Length, the connection
    closed after it. ``done`` is told how it went ("ok", "interrupted", "failed")."""

    name: str
    content_type: str
    write: Callable
    done: Callable


BACKUP_WAIT_S = 10.0  # a backup waits this long for a job to finish, then is refused
AUDIT_SHOWN = 200


def _floor_of(object_id) -> str | None:
    """The floor of an object, by its ID (None for what is not an object's ID)."""
    from .ids import parse_id

    try:
        return parse_id(object_id).prefix("floor")
    except (ValueError, TypeError, AttributeError):
        return None


def _need(have: str | None, level: str, what: str) -> None:
    if rank(have) < rank(level):
        raise Forbidden(f"this needs {level} access to {what}; you have {have or 'none'}")


class Gate:
    """What the person asking may do. route() asks it first in every case (a test checks
    that every case does): it raises Unauthorized (401) when nobody is logged in,
    Forbidden (403) when they are and may not, and NotFound (404) for a project,
    building, floor, object, item or job they may not see at all, as for one that is not
    there. Until a person changes a temporary password, only me, password and logout are
    answered. Without accounts (``storeypath review``, on this computer alone) the person
    is LOCAL, who may do everything."""

    def __init__(self, studio: Studio, accounts: Accounts | None, user: User | None, address: str = "",
                 token: str | None = None, page: str | None = None):
        self.studio, self.accounts, self.address, self.token = studio, accounts, address, token
        self.user = LOCAL if accounts is None else user
        self.page = page  # the page asking (X-StoreyPath-Page), told back with what it changes

    # ---- who is asking ------------------------------------------------------------

    def _who(self, password_gate: bool = True) -> User:
        if self.user is None:
            raise Unauthorized("log in to use Studio")
        if password_gate and self.user.must_change_password:
            raise Forbidden("change your password first: it was given to you to change", must_change_password=True)
        return self.user

    @property
    def uid(self) -> str | None:
        """The asking person's id (None without accounts)."""
        return None if self.user is None or self.user is LOCAL else self.user.id

    @property
    def session(self) -> str:
        """The asking session, as room locks keep it: part of the hash of its token
        (never the token); "local" on this computer without accounts."""
        import hashlib

        return hashlib.sha256(self.token.encode()).hexdigest()[:16] if self.token else "local"

    @property
    def editor(self) -> Editor:
        """Where a change of theirs comes from: their session, and the page that sent it."""
        return Editor(self.session, self.page)

    def audit(self, action: str, target=None, outcome: str = "ok", **more) -> None:
        if self.accounts is not None:
            self.accounts.audit(action, self.user, self.address, target, outcome, **more)

    def anyone(self) -> None:
        """Logging in and out: asked by anyone."""

    def me(self) -> User:
        """Logged in, even with a password to change."""
        return self._who(password_gate=False)

    def logged_in(self) -> User:
        return self._who()

    def admin(self) -> User:
        user = self._who()
        if user.role != "admin":
            raise Forbidden("only an admin may do this")
        return user

    def capability(self, name: str) -> User:
        user = self._who()
        if not user.can(name):
            raise Forbidden({"backup": "only an admin, or someone an admin let, may download a backup",
                             "catalogue": "only an admin, or someone an admin let, may change the item types"}[name])
        return user

    def create(self) -> User:
        user = self._who()
        if user.role not in ("admin", "engineer"):
            raise Forbidden("only admins and engineers create projects")
        return user

    def picker(self) -> User:
        """Someone who may share something (or an admin): to be shown the users to share with."""
        user = self._who()
        if self.accounts is not None and not self.accounts.shares_somewhere(user):
            raise Forbidden("you may not share anything")
        return user

    # ---- projects and their parts ------------------------------------------------------

    def sight_of(self, code: str, ws: Workspace) -> Sight:
        """What the asking person may do in a project (its workspace as it is)."""
        user = self._who()
        access = self.accounts.project_access(code) if self.accounts is not None else None
        return Sight(ws, access, None if user is LOCAL else user)

    def _workspace(self, code: str) -> Workspace:
        return self.studio.workspace(code)  # NotFound when there is no such project

    def see(self, code: str) -> Sight:
        """Any access to the project: a project nobody let them see is not there for them."""
        self._who()
        sight = self.sight_of(code, self._workspace(code))
        if not sight.any():
            raise NotFound(f"no project {code}")
        return sight

    def project(self, code: str, level: str) -> Sight:
        sight = self.see(code)
        _need(sight.project, level, "the whole project")
        return sight

    def own(self, code: str) -> Sight:
        sight = self.see(code)
        if not (sight.owner or sight.admin):
            raise Forbidden("only the project's owner or an admin may delete it")
        return sight

    def building(self, code: str, building_id: str, level: str) -> Sight:
        sight = self.see(code)
        if not sight.sees_building(building_id):
            raise NotFound(f"no building {building_id}")
        _need(sight.building(building_id), level, "the whole building")
        return sight

    def site(self, code: str, location_id: str, level: str) -> Sight:
        """A location's site (format 0.13): its land, its layout's, seen as a building is
        (its buildings are each seen as they are)."""
        sight = self.see(code)
        land = f"{location_id}-{LAND_CODE}" if isinstance(location_id, str) else ""
        if not sight.sees_building(land):
            raise NotFound(f"no location {location_id} with a layout")
        _need(sight.building(land), level, "the location's land")
        return sight

    def floor(self, code: str, floor_id: str, level: str) -> Sight:
        sight = self.see(code)
        if not sight.floor(floor_id):
            raise NotFound(f"no floor {floor_id}")
        _need(sight.floor(floor_id), level, "this floor")
        return sight

    def undo(self, code: str, floor) -> Sight:
        """Undoing (or redoing) one's own change: on a floor, edit on it; anywhere, any access
        (the change undone needs edit on each floor it changed: Studio.undo checks)."""
        if floor is None:
            return self.see(code)
        if not isinstance(floor, str):
            raise ValueError("floor: a floor's ID")
        return self.floor(code, floor, "edit")

    def history(self, code: str, floor) -> Sight:
        """The project's history: any access (each sees the changes of what they may see);
        of a floor, view on it."""
        if floor is None:
            return self.see(code)
        return self.floor(code, floor, "view")

    def take_over(self, code: str, floor_id: str) -> Sight:
        """Taking over rooms someone else is editing on a floor: an admin (who sees it)."""
        sight = self.floor(code, floor_id, "view")
        if self.user is not LOCAL and self.user.role != "admin":
            raise Forbidden("only an admin may take over rooms someone is editing")
        return sight

    def delete_floor(self, code: str, floor_id: str) -> Sight:
        """Deleting a floor: edit on its building (the building is changed; its packages
        list what the floor held as retired)."""
        sight = self.floor(code, floor_id, "view")
        building = floor_id.rsplit("-", 1)[0]
        _need(sight.building(building), "edit", f"the building {building}")
        return sight

    def convert(self, code: str, floor) -> Sight:
        """Reading drawings again: one floor's (edit on it), or every floor's (edit on the project)."""
        if floor is None:
            return self.project(code, "edit")
        if not isinstance(floor, str):
            raise ValueError("floor: a floor's ID")
        return self.floor(code, floor, "edit")

    def drawing(self, code: str, floor_id: str) -> Sight:
        """A floor's drawing, or its print: the part of the drawing that is this floor's
        plan (its region), and nothing else of it. A floor read from a whole drawing (no
        region) that other floors are read from too shows theirs: it needs view on each."""
        sight = self.floor(code, floor_id, "view")
        ws = self._workspace(code)
        f = ws.floor(floor_id)
        if f.source is None or f.source.region is not None:
            return sight
        mine = drawing_name(f.source.path)
        for *_, g, g_id in ws.iter_floors():
            if g_id != floor_id and g.source is not None and drawing_name(g.source.path) == mine \
                    and not sight.floor(g_id):
                raise Forbidden("this floor's drawing holds other floors you may not see, and no part of it "
                                "is marked as this floor's plan")
        return sight

    def object(self, code: str, object_id: str, body: dict | None = None) -> Sight:
        """A space, zone or opening corrected: edit on its floor. A lift or stairs linked
        to one on another floor (``stack``): that one seen too."""
        sight = self._space_seen(code, object_id, "no active space, zone or opening")
        _need(sight.floor(_floor_of(object_id)), "edit", "its floor")
        target = (body or {}).get("stack")
        if isinstance(target, str) and target:
            self._space_seen(code, target, "no space", sight)
        return sight

    def _space_seen(self, code: str, object_id, missing: str, sight: Sight | None = None) -> Sight:
        """The person's sight of a project, when they see the floor of an object of it
        (NotFound, saying ``missing``, when not)."""
        sight = sight or self.see(code)
        floor_id = _floor_of(object_id)
        if floor_id is None or not sight.floor(floor_id) or object_id not in self._workspace(code).objects:
            raise NotFound(f"{missing} {object_id}")
        return sight

    def stack(self, code: str, object_id: str) -> Sight:
        """The floors a lift or stairs serves: view on its floor (the others listed are
        those the person may see)."""
        return self._space_seen(code, object_id, "no space")

    def copy_vertical(self, code: str, object_id: str, body: dict) -> Sight:
        """A lift or stairs drawn again on other floors and linked: edit on its floor and on
        each of them."""
        sight = self._space_seen(code, object_id, "no space")
        _need(sight.floor(_floor_of(object_id)), "edit", "its floor")
        floors = body.get("floors")
        if not isinstance(floors, list) or not floors or not all(isinstance(f, str) for f in floors):
            raise ValueError("floors: choose the floors to add it on")
        for f in floors:
            if not sight.floor(f):
                raise NotFound(f"no floor {f}")
            _need(sight.floor(f), "edit", f"the floor {f}")
        return sight

    def find_item(self, code: str, item_id: str) -> Sight:
        """An item looked up by its ID (as a person typed it): view on its floor."""
        sight = self.see(code)
        it = self._workspace(code).items.get(normalize_item_id(item_id) or item_id)
        if it is None or not sight.floor(it.floor_id):
            raise NotFound(f"no item {item_id}")
        return sight

    def item(self, code: str, item_id: str, body: dict) -> Sight:
        """An item changed: edit on its floor, and on the floor it is carried to."""
        sight = self.see(code)
        it = self._workspace(code).items.get(item_id)
        if it is None or not sight.floor(it.floor_id):
            raise NotFound(f"no item {item_id}")
        _need(sight.floor(it.floor_id), "edit", "its floor")
        to = body.get("floor_id")
        if isinstance(to, str) and to != it.floor_id:
            if not sight.floor(to):
                raise NotFound(f"no floor {to}")
            _need(sight.floor(to), "edit", "the floor it goes to")
        return sight

    def items(self, code: str, body: dict) -> Sight:
        """Items changed together (a group moved, turned, taken away, grouped or let go):
        each as one changed alone (item), in a list of them each with its ID."""
        sight = self.see(code)
        asked = body.get("items")
        if not isinstance(asked, list) or not asked or not all(isinstance(a, dict) and isinstance(a.get("id"), str)
                                                                 for a in asked):
            raise ValueError("items: a list of {id, …}, one or more")
        for a in asked:
            self.item(code, a["id"], a)
        return sight

    def furnishing(self, code: str, furnishing_id: str) -> Sight:
        """A furniture layout deleted: edit on the floor it was taken from, or on the whole
        project; one that is not there, edit on the whole project to be told so."""
        sight = self.see(code)
        f = self._workspace(code).furnishings.get(furnishing_id)
        if f is not None and sight.floor(f.floor_id) and rank(sight.floor(f.floor_id)) >= rank("edit"):
            return sight
        _need(sight.project, "edit", "the floor it was taken from, or the whole project")
        return sight

    def add_floors(self, code: str, body: dict) -> Sight:
        """Floors added from one of the project's drawings: edit on each building they go
        into (on the project for a new building or location), and view on the whole
        project, as the drawing is the project's and may hold any of its floors."""
        sight = self.see(code)
        _need(sight.project, "view", "the whole project (its drawings)")
        plans = body.get("plans")
        if not isinstance(plans, list) or not plans:
            raise ValueError("choose at least one plan")
        for place in _floors_to_add(self._workspace(code), [dict(p) if isinstance(p, dict) else p for p in plans]):
            if place.building_id is None:
                _need(sight.project, "edit", "the whole project (a new building)")
            else:
                if not sight.sees_building(place.building_id):
                    raise NotFound(f"no building {place.building_id}")
                _need(sight.building(place.building_id), "edit", f"the building {place.building_id}")
        return sight

    def add_layout(self, code: str, body: dict) -> Sight:
        """A site plan used as a location's layout (0.12): edit on the whole project (its
        drawing is the project's; a location may be made, and its land is the location's)."""
        return self.project(code, "edit")

    def footprint(self, code: str, footprint_id: str, body: dict) -> Sight:
        """A footprint said to be a building (placed on it): edit on the land (its layout),
        and on that building (it moves on its site); a new building for it: edit on the
        whole project."""
        sight = self.see(code)
        if not isinstance(footprint_id, str) or not is_land(footprint_id):
            raise NotFound(f"no footprint {footprint_id}")
        land = footprint_id.rsplit("-", 2)[0]
        if not sight.sees_building(land):
            raise NotFound(f"no footprint {footprint_id}")
        _need(sight.building(land), "edit", "the location's layout")
        building = body.get("building")
        if body.get("new"):
            _need(sight.project, "edit", "the whole project (a new building)")
        elif isinstance(building, str) and building:
            if not sight.sees_building(building):
                raise NotFound(f"no building {building}")
            _need(sight.building(building), "edit", f"the building {building}")
        return sight

    def export(self, code: str, body: dict) -> str:
        """A building's package made: edit on the building (it holds every floor of it).
        With its drawings (``drawings``), edit on the whole project too, as to see a
        drawing whole (its every layer and text) or its words."""
        sight = self.see(code)
        building = self.studio.export_building(code, body)
        if not sight.sees_building(building):
            raise NotFound(f"no building {building}")
        _need(sight.building(building), "edit", "the whole building")
        if (body or {}).get("drawings") not in (None, False) and rank(sight.project) < rank("edit"):
            raise Forbidden("exporting with the drawings needs edit rights on the project")
        return building

    def export_file(self, code: str, name: str) -> Sight:
        """A package of the project's: view on the buildings it holds (on the project, for
        one no export entered)."""
        sight = self.see(code)
        held = package_buildings(self._workspace(code), name)
        if held is None:
            if not sight.whole:
                raise NotFound(f"no export {name}")
            return sight
        for b in held:
            if not sight.sees_building(b):
                raise NotFound(f"no export {name}")
            _need(sight.building(b), "view", "the whole building it holds")
        return sight

    def preview(self, code: str, building) -> Sight:
        sight = self.see(code)
        if building is not None and not sight.sees_building(building):
            raise NotFound(f"no building {building}")
        return sight

    def job(self, job_id: str) -> Job:
        """A job, to an admin and to those who may see what it works on now (who started
        it too, as long as they still may: what it reports is of that part)."""
        user = self._who()
        job = self.studio.jobs.get(job_id)
        if self.accounts is None or user.role == "admin":
            return job
        try:
            sight = self.see(job.project) if job.project else None
        except NotFound:
            sight = None
        if sight is None or not sight.scope(*job.scope):
            raise NotFound(f"no job {job_id}")
        return job

    def stop_job(self, job_id: str) -> Job:
        """A job a person may stop: who started it, an admin, or whoever may start it
        again (a read: edit on the floors it reads, or on the project)."""
        user = self._who()
        job = self.job(job_id)
        if self.accounts is None or user.role == "admin" or (job.user is not None and job.user == user.id):
            return job
        for floor in job.floors or [None]:
            self.convert(job.project, floor)
        return job

    def jobs(self, code: str) -> dict:
        """A project's jobs (Jobs.of) of the parts of it the person may see, as job() has
        it, each with who started it."""
        user = self._who()
        sight = self.see(code)
        everyone = self.accounts is None or user.role == "admin"
        people: dict[str | None, dict | None] = {}

        def who(uid):
            if uid not in people:
                found = self.accounts.user(uid) if self.accounts is not None and uid else None
                people[uid] = found.view() if found is not None else None
            return people[uid]

        return {"jobs": [j.listed(who(j.user)) for j in self.studio.jobs.of(code) if everyone or sight.scope(*j.scope)]}

    def waiting(self, code: str) -> dict:
        """The drawings the person sent to a project (an admin: anyone's) that wait for them
        to choose what to keep of what was found private in them: who may add drawings.
        Only theirs: what is listed is the private texts found."""
        user = self._who()
        self.project(code, "edit")
        everyone = self.accounts is None or user.role == "admin"
        return {"incoming": self.studio.waiting(code, self.uid, everyone=everyone)}

    def opening(self):
        """Who may open a file: a new project, an admin or an engineer, who owns it (made
        its owner now, only when nothing is kept of a project of that code: an admin
        opens one that is, and it keeps who it is shared with); a package into a project
        here, edit on each building it brings (on the project, for a new one) and on each
        floor an item it holds is carried from; a project file in place of the one here,
        its owner or an admin. Someone who may not see the project here is answered as
        for a new project they may not open, never told its name. Checked once the file
        is read (what it holds says what it changes, bundle.read_file): the check is
        returned, Studio.open calls it."""
        user = self._who()
        new = Forbidden("only admins and engineers open files as new projects")
        if user.role not in ("admin", "engineer") and self.accounts is not None \
                and not self.accounts.edits_somewhere(user):
            raise new  # nothing they could open: the file is not even read

        def allow(incoming, here: bool):
            code = incoming.code
            if not here:
                if user.role not in ("admin", "engineer"):
                    raise new
                if self.accounts is None or not self.uid:
                    return None
                if self.accounts.claim(code, self.uid):
                    return lambda: self.accounts.unclaim(code, self.uid)
                if user.role != "admin":
                    raise Forbidden("who a project of this code was shared with is kept from before: "
                                    "an admin may open it")
                return None
            sight = self.sight_of(code, self._workspace(code))
            if not sight.any():
                if user.role not in ("admin", "engineer"):
                    raise new
                raise Forbidden("this file's project cannot be opened here: ask an admin")
            if incoming.how == "project":
                if not (sight.owner or sight.admin):
                    raise Forbidden("only the project's owner or an admin may put a project file in its place")
                return None
            pkg, here = incoming.ws, self._workspace(code)
            from .export import building_ids

            known = set(building_ids(here))
            brought = building_ids(pkg)
            for b in brought:
                if b in known:
                    _need(sight.building(b), "edit", f"the building {b}")
                else:
                    _need(sight.project, "edit", "the whole project (a new building)")
            for i, it in pkg.items.items():  # an item of another building here, carried into this one
                mine = here.items.get(i)
                if mine is not None and mine.floor_id and sight.building_of.get(mine.floor_id) not in brought:
                    _need(sight.floor(mine.floor_id), "edit", f"the floor {i} is on")

        return allow

    # ---- what follows what was done --------------------------------------------------

    def kept_codes(self) -> set[str]:
        """The codes whose sharing is kept: a new project is given none of them."""
        return set(self.accounts.all_access()) if self.accounts is not None else set()

    def made(self, made: dict) -> dict:
        """A project created (its code one nothing is kept of): its maker owns it."""
        if self.accounts is not None:
            if self.uid:
                self.accounts.claim(made["code"], self.uid)
            self.audit("project created", made["code"])
        return made

    def opened(self, opened: dict) -> dict:
        """A file opened (a new project's owner was made when it was let through)."""
        if self.accounts is not None:
            self.audit("project opened", opened["code"], how=opened.get("how"),
                       buildings=opened.get("buildings"))
        return opened

    def deleted(self, code: str, deleted: dict) -> dict:
        if self.accounts is not None:
            self.accounts.forget(code)
            self.audit("project deleted", code, name=deleted.get("name"))
        return deleted

    # ---- the person's own ------------------------------------------------------------

    def login(self, body: dict) -> LoggedIn:
        if self.accounts is None:
            raise NotFound("Studio runs without accounts here")
        token, user = self.accounts.login(body.get("username"), body.get("password"), self.address)
        self.accounts.logout(self.token)  # the session it replaces, if any
        return LoggedIn(token, {"user": self._me(user), "must_change_password": user.must_change_password})

    def logout(self) -> LoggedOut:
        if self.accounts is not None:
            user = self.accounts.logout(self.token)
            if user is not None:
                self.accounts.audit("logout", user, self.address, user.username)
        return LoggedOut()

    def _me(self, user: User) -> dict:
        return {**user.view(), "role": user.role,
                "capabilities": list(CAPABILITY_NAMES) if user.role == "admin" else list(user.capabilities),
                "must_change_password": user.must_change_password, "local": self.accounts is None,
                "create": user.role in ("admin", "engineer"),
                "share": self.accounts is None or self.accounts.shares_somewhere(user)}

    def whoami(self) -> dict:
        return self._me(self.me())

    def change_password(self, body: dict) -> LoggedIn:
        user = self.me()
        if self.accounts is None:
            raise NotFound("Studio runs without accounts here")
        token = self.accounts.change_own_password(user, body.get("current"), body.get("new"), self.address)
        self.accounts.logout(self.token)
        user = self.accounts.user(user.id)
        return LoggedIn(token, {"user": self._me(user), "must_change_password": False})

    # ---- sharing -----------------------------------------------------------------------

    def share_some(self, code: str) -> Sight:
        """Share on some part of the project: to see and change who it is shared with there."""
        sight = self.see(code)
        if sight.most() != "share":
            raise Forbidden("you may not share anything in this project")
        return sight

    def _person(self, user_id) -> dict | None:
        if self.accounts is None or user_id is None:
            return None
        u = self.accounts.user(user_id)
        return u.view() if u else {"id": user_id, "username": "?", "name": "(not known)"}

    def access(self, code: str, sight: Sight) -> dict:
        """Who the project is shared with, where the asking person may share: its owner,
        the grants on the scopes they have share on (and inside them), and those scopes,
        as a tree, to share more."""
        ws = self._workspace(code)
        access = self.accounts.project_access(code) if self.accounts is not None else None
        names = {"project": ws.project.name}
        tree = []
        for loc in ws.locations:
            for b in loc.buildings:
                b_id = f"{ws.id}-{loc.code}-{b.code}"
                names[b_id] = b.name
                floors = []
                for f in sorted(b.floors, key=floor_order):
                    f_id = f"{b_id}-{f.code}"
                    names[f_id] = f"{f.name} · {b.name}"
                    if sight.floor(f_id) == "share":
                        floors.append({"kind": "floor", "id": f_id, "name": f.name})
                if sight.building(b_id) == "share" or floors:
                    tree.append({"kind": "building", "id": b_id, "name": b.name,
                                 "share": sight.building(b_id) == "share", "floors": floors})
        grants = []
        for g in (access.grants if access else []):
            if sight.scope(g.scope.kind, g.scope.id) != "share":
                continue
            grants.append({"user": self._person(g.user), "scope": {**g.scope.model_dump(),
                           "name": names.get(g.scope.id or "project", g.scope.id)},
                           "level": g.level, "by": self._person(g.by), "at": g.at})
        return {"project": {"code": code, "name": ws.project.name},
                "owner": self._person(access.owner if access else None),
                "you": {"level": sight.project, "owner": sight.owner, "admin": sight.admin},
                "scopes": {"project": sight.project == "share", "buildings": tree},
                "grants": grants}

    def grant(self, code: str, sight: Sight, body: dict) -> dict:
        """A person given a level on a scope of the project, or theirs taken away (level
        null): by someone with share on that scope (not wider), for anyone but
        themselves, and never the owner's access."""
        if self.accounts is None:
            raise ValueError("Studio runs without accounts here: nothing is shared")
        scope = body.get("scope")
        if not isinstance(scope, dict) or scope.get("kind") not in ("project", "building", "floor"):
            raise ValueError('scope: {"kind": "project"}, {"kind": "building", "id": …} or {"kind": "floor", "id": …}')
        kind, scope_id = scope["kind"], scope.get("id") if scope["kind"] != "project" else None
        if kind != "project" and (not isinstance(scope_id, str) or not (sight.floor(scope_id) if kind == "floor"
                                                                       else sight.sees_building(scope_id))):
            raise NotFound(f"no {kind} {scope_id}")
        _need(sight.scope(kind, scope_id), "share", f"the {kind}" if kind != "project" else "the whole project")
        user = self.accounts.user(body.get("user")) if isinstance(body.get("user"), str) else None
        if user is None:
            raise ValueError("no such user")
        if user.id == self.uid:
            raise Forbidden("you cannot change your own access: ask someone else who may share it")
        if user.id == sight.access.owner:
            raise Forbidden("the project's owner has every access to it already, and keeps it")
        level = body.get("level")
        if level is not None and not user.active:
            raise ValueError(f"{user.username} is disabled")
        done = self.accounts.set_grant(code, user.id, Scope(kind=kind, id=scope_id), level, self.uid)
        if done != "nothing":
            self.audit(f"grant {done}", code, who=user.username, scope=f"{kind} {scope_id or ''}".strip(),
                       level=level)
        return self.access(code, self.see(code))

    def set_owner(self, code: str, body: dict) -> dict:
        """The project given another owner; the owner before keeps share on the whole of
        it, as a grant of their own (Accounts.give)."""
        self._workspace(code)
        user = self.accounts.user(body.get("user")) if self.accounts and isinstance(body.get("user"), str) else None
        if user is None or not user.active:
            raise ValueError("no such user (or disabled)")
        before = self.accounts.give(code, user.id, self.uid)
        self.audit("owner changed", code, owner=user.username,
                   was=(self._person(before) or {}).get("username"))
        return self.access(code, self.see(code))

    def users_to_share(self) -> list[dict]:
        if self.accounts is None:
            return []
        return [u.view() for u in self.accounts.users() if u.active]

    # ---- users (admins) ----------------------------------------------------------------

    def _accounts(self) -> Accounts:
        if self.accounts is None:
            raise NotFound("Studio runs without accounts here")
        return self.accounts

    def all_users(self) -> list[dict]:
        """Everyone, and whether admin still has its first password (said on the page)."""
        accounts = self._accounts()
        return [{**u.view(full=True), "default_password": accounts.default_password(u)} for u in accounts.users()]

    def add_user(self, body: dict) -> dict:
        """A new user, with a temporary password (shown once) to change at the first login."""
        accounts = self._accounts()
        password = temporary_password()
        user = accounts.add_user(body.get("username"), password, name=body.get("name") or "",
                                 role=body.get("role") or "user", capabilities=body.get("capabilities") or [],
                                 must_change_password=True)
        self.audit("user created", user.username, role=user.role, capabilities=user.capabilities)
        return {"user": user.view(full=True), "password": password}

    def change_user(self, user_id: str, body: dict) -> dict:
        accounts = self._accounts()
        if accounts.user(user_id) is None:
            raise NotFound(f"no user {user_id}")
        fields = {k: body[k] for k in ("name", "role", "capabilities", "active") if k in body}
        user = accounts.update_user(user_id, **fields)
        if fields.get("active") is False:
            self.audit("user disabled", user.username)
        elif fields:
            self.audit("user changed", user.username, **{k: v for k, v in fields.items() if k != "name"})
        return user.view(full=True)

    def reset_password(self, user_id: str) -> dict:
        accounts = self._accounts()
        if accounts.user(user_id) is None:
            raise NotFound(f"no user {user_id}")
        password = temporary_password()
        user = accounts.set_password(user_id, password, temporary=True)
        self.audit("password reset", user.username)
        return {"user": user.view(full=True), "password": password}

    def audit_log(self, query: dict) -> list[dict]:
        try:
            n = int((query.get("n") or [AUDIT_SHOWN])[0])
        except ValueError:
            n = AUDIT_SHOWN
        return self._accounts().audit_tail(max(1, min(n, 2000)))

    # ---- the whole data folder -------------------------------------------------------

    def backup(self, password: str | None = None) -> Stream:
        """Studio's database, every project and account in it as one moment saw them
        (backup.py), written as it is sent; sealed with ``password`` when given
        (sealed.py: a piece at a time, as it is written)."""
        name = backup_name()

        def done(outcome: str) -> None:
            self.audit("backup", name, outcome)

        if password is None:
            return Stream(name, "application/gzip", lambda out: write_backup(self.studio.db, out), done)

        def sealed(out) -> None:
            from .sealed import Sealer

            sealer = Sealer(out, password)
            write_backup(self.studio.db, sealer)
            sealer.close()

        name = sealed_name(name)
        return Stream(name, "application/octet-stream", sealed, done)


CAPABILITY_NAMES = ("backup", "catalogue")
