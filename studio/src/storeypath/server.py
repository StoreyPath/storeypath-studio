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
                                               private=0 kept as sent
    POST /api/projects/<code>/incoming/<id> {keep: [found ids]}  → job: kept without the
                                               rest of it, as drawing-N.dxf
    POST /api/projects/<code>/incoming/<id>/cancel   not added; the file as sent is gone
    GET  /api/projects/<code>/drawings/<name>/words  every word and string left in it (text), to look
                                               through for anything private left behind
    POST /api/projects/<code>/drawings/<name>/plans {units?}  → job: its units and the plans in it
    POST /api/projects/<code>/floors {drawing, units, plans: [...]}  → job: add, align, convert
    POST /api/projects/<code>/convert          → job
    POST /api/projects/<code>/buildings/<id>/placement {lat, lon, bearing, x?, y?}
    POST /api/projects/<code>/export {building} → job: that building's package (one
                                               building per package)
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
    GET  /api/users                            who to share with (id, username, name)
    GET  /api/admin/users                      POST /api/admin/users {username, name, role, capabilities}
    POST /api/admin/users/<id> {name?, role?, capabilities?, active?}
    POST /api/admin/users/<id>/password        a temporary password, shown once
    GET  /api/admin/audit                      GET /api/backup (the database, .sql.gz)
    and the review editor's calls under /api/projects/<code>/ (see review.py)

What changes something (POST, a JSON object; PUT, a file) is sent with the header
``X-StoreyPath: 1``, which a page of another site cannot send, from a page of Studio's.
Studio answers only to its own names (allowed_hosts: localhost, this machine's name,
an address, and those given with --allowed-host or STOREYPATH_ALLOWED_HOSTS).
"""

from __future__ import annotations

import io
import math
import re
import threading
import traceback
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from .db.store import drawing_name
from .ids import make_id
from .cad import UNIT_NAMES, UNIT_WORDS, DrawingError, header_units, meters_per_unit, read_drawing
from .llm import LocalModel, ModelUnavailable, read_titles, worth_reading
from .sheets import read_title
from .profile import AUTO, load_profile
from .levels import DEFAULT_HEIGHT_M, DEFAULT_PARAPET_M, floor_levels, plan_level, read_level_marks
from .reading import NOT_A_ROOM, read_units
from .symbols import SymbolSpotter
from .vision import VisionModel
from .privacy import words
from .review import File, NotFound, Review
from .types import SpaceType
from .workspace import Placement, SourceDrawing, Workspace

DRAWING_TYPES = (".dwg", ".dxf")
INCOMING = ".incoming-"  # a drawing as sent, until its private copy is made
WORDS = ".words.txt"  # beside a drawing: every word and string left in it
DRAWING_NAME = re.compile(r"drawing-(\d+)\.dxf")


# ---- jobs ------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    title: str
    state: str = "waiting"  # waiting, running, done, failed
    log: list[str] = field(default_factory=list)
    result: object = None
    error: str | None = None
    started: str | None = None
    project: str | None = None  # the project it works on, and the part of it (scope): who may follow it
    scope: tuple[str, str | None] = ("project", None)
    user: str | None = None  # who started it

    def say(self, line: str) -> None:
        self.log.append(line)

    def view(self) -> dict:
        return {"id": self.id, "title": self.title, "state": self.state, "log": self.log,
                "result": self.result, "error": self.error}


class Jobs:
    """Long steps, run one at a time in the background (drawings are large and the
    language model has one slot)."""

    def __init__(self, data: Path | None = None):
        self.data = data
        self._jobs: dict[str, Job] = {}
        self._queue: Queue = Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, title: str, fn, project: str | None = None, scope: tuple = ("project", None),
               user: str | None = None) -> Job:
        job = Job(uuid.uuid4().hex[:12], title, project=project, scope=scope, user=user)
        self._jobs[job.id] = job
        self._queue.put((job, fn))
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            raise NotFound(f"no job {job_id}")
        return self._jobs[job_id]

    def _work(self):
        while True:
            job, fn = self._queue.get()
            try:
                job.state, job.started = "running", datetime.now(timezone.utc).isoformat()
                job.result = fn(job)
                job.state = "done"
            except Exception as e:  # reported on the page
                job.state, job.error = "failed", _message(e)
                job.say(job.error)
                traceback.print_exc()


def _message(e: Exception) -> str:
    return str(e) if isinstance(e, (DrawingError, ValueError, KeyError, ModelUnavailable)) else f"{type(e).__name__}: {e}"


# ---- the application -----------------------------------------------------------

CACHE = "cache"  # in the data folder: what is made again when gone (prints of floors)
WORK = "work"  # in the cache: drawings read from the database for a step, removed after it


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
                 first_start: bool = True):
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
        self.jobs = Jobs(self.data)
        if warm and self.model.available():
            # Load the model now, in the background, so the first drawing does not
            # wait for 2–3 GB of weights to come off the disk.
            threading.Thread(target=self.model.warm, daemon=True).start()
        self._reviews: dict[str, Review] = {}
        self._networks: dict[tuple, tuple] = {}  # (code, building, version) -> its walking network, worked out
        # One lock per project, held while a job changes it: converting one project
        # (minutes, with vision) never holds up another, and pages only read.
        self._lock = threading.Lock()  # the tables below
        self._project_locks: dict[str, threading.RLock] = {}
        self._opening = threading.Lock()  # one file opened at a time (Studio.open)
        self._pending: dict[str, dict] = {}  # drawings sent, waiting for a person to choose what goes
        self.store.drop_incoming()  # sent and never kept (Studio stopped meanwhile): not kept
        import shutil

        shutil.rmtree(self.work, ignore_errors=True)  # what a step left when Studio stopped
        self.imported = None
        if first_start:
            from .db.importer import first_start as bring_in

            self.imported = bring_in(self.data, self.db, self.store)

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

        with self._lock:
            old = self.store.catalogue()
            new = catalogue.Catalogue.model_validate({**body, "format": catalogue.CATALOGUE_FORMAT})
            if gone := sorted({t.code for t in old.types} - {t.code for t in new.types}):
                raise ValueError(f"types are retired, not removed: {', '.join(gone)}")
            self.store.save_catalogue(new)
            return new.model_dump()

    def _changing(self, code: str) -> threading.RLock:
        """The lock held while a job changes this project."""
        with self._lock:
            return self._project_locks.setdefault(code, threading.RLock())

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
        from .export import footprint, site_positions

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
                buildings.append({
                    "id": b_id, "code": b.code, "name": b.name,
                    "placement": b.placement.model_dump() if b.placement else None,
                    # its place on the site plan (as its drawing places it when not set) and
                    # its footprint in its own drawing metres, for the site plan to draw
                    "site": {**positions[b.code].model_dump(), "set": whole_b.site is not None},
                    "footprint": _rings(footprint(b)),
                    "centre": [round(centre.x, 2), round(centre.y, 2)] if centre is not None else None,
                    "floors": [{"id": f"{b_id}-{f.code}", "name": f.name, "ordinal": f.ordinal,
                                "drawing": Path(f.source.path).name if f.source else None,
                                "view": f.source.view if f.source else None,
                                "layers": f.layers, "converted": f.converted_at is not None,
                                "method": f.method}
                               for f in sorted(b.floors, key=lambda f: f.ordinal)],
                })
            tree.append({"id": f"{ws.id}-{loc.code}", "code": loc.code, "name": loc.name, "buildings": buildings,
                         "placement": loc.placement.model_dump() if loc.placement else None})
        drawings = [d["name"] for d in self.store.drawings(code) if Path(d["name"]).suffix.lower() in DRAWING_TYPES]
        exports = self.store.export_files(code)
        if sight is not None and not sight.whole:
            used = {Path(f.source.path).name for *_, f, _ in ws.iter_floors() if f.source}
            drawings = [d for d in drawings if d in used]
            exports = [e for e in exports if (held := package_buildings(full, e))
                       and all(rank(sight.building(b)) >= rank("view") for b in held)]
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
        as sent."""
        self._known(code)
        name = Path(name).name
        suffix = Path(name).suffix.lower()
        if suffix not in DRAWING_TYPES:
            raise ValueError("drawings are .dwg or .dxf files")
        if not private:
            self.store.put_drawing(code, name, body, by=by)
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
                                        "doc": doc, "report": report, "choices": choices, "reader": reader}
            job.say(f"found {len(choices.found)} things to take out: choose what to keep")
            return {"pending": token, "name": name, "found": choices.listed(), "reader": report.model}

        return self.jobs.submit(f"Looking for private information in {name}", run, project=code, user=uid)

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

        return self.jobs.submit(f"Adding {p['name']} without its private information", run, project=code, user=uid)

    def cancel_private(self, code: str, token: str) -> dict:
        with self._lock:
            p = self._pending.pop(token, None)
        if p is None or p["code"] != code:
            raise NotFound("no drawing waiting to be added")
        self.store.drop_incoming(code, p["incoming"])
        return {"cancelled": p["name"]}

    def _keep_private(self, code: str, incoming: str, doc, report, job: Job, by=None) -> dict:
        """The cleaned drawing kept as drawing-N.dxf, with its words; the file as sent gone."""
        import tempfile

        try:
            job.say(report.summary())
            taken = [int(m.group(1)) for d in self.store.drawings(code) if (m := DRAWING_NAME.fullmatch(d["name"]))]
            name = f"drawing-{max(taken, default=0) + 1}.dxf"
            self.work.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="storeypath-", dir=self.work) as folder:
                out = Path(folder) / name
                doc.saveas(out)
                data = out.read_bytes()
            self.store.put_drawing(code, name, data, words=words(doc, name, report.summary()), by=by)
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

    def plans(self, code: str, name: str, units: str | None = None, by=None) -> Job:
        """The plans in a drawing, read in ``units``, or in the units it shows."""
        name = self._drawing(code, name)
        if units is not None and not isinstance(units, str):
            raise ValueError(f"units: one of {', '.join(UNIT_NAMES)}")
        if units and units not in UNIT_NAMES:
            raise ValueError(f"unknown units {units!r}: use one of {', '.join(UNIT_NAMES)}")

        def run(job: Job):
            from .sheets import find_views

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
                out.append({"index": v.index, "title": v.title, "kind": kind, "floor": floor,
                            "building": r.building if r else None, "size": v.size, "region": v.region,
                            "preview": v.preview})
            # Floor heights and the roof's parapet, from the levels on its sections, and
            # where they give none, from the level each floor's plan marks.
            found = floor_levels(read_level_marks(doc, self.model if self.model.available() else None))
            marked = {}
            for v, o in zip(views, out):
                if o["kind"] in ("floor_plan", "roof_plan") and o["floor"] is not None and o["floor"] not in marked:
                    o["level"] = marked[o["floor"]] = plan_level(doc, v.region)
            found.add_plans(marked)
            job.say(f"levels: {found.summary()}" if found.summary() else "no floor levels on its sections or plans")
            levels = {"summary": found.summary() or None, "heights": {str(n): h for n, h in found.heights.items()},
                      "height": found.typical_height(), "roof": found.roof, "parapet": found.parapet,
                      "default_parapet": DEFAULT_PARAPET_M}
            return {"drawing": name, "units": used, "units_sure": sure, "units_reason": reason,
                    "units_chosen": bool(units), "units_said": header_units(doc), "levels": levels, "plans": out}

        return self.jobs.submit(f"Reading the plans in {name}", run, project=code, user=_uid(by))

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
                ws = self.store.load(code, floors=[])
                places = _floors_to_add(ws, plans)
                added: dict[str, list[str]] = {}  # building -> floors added or given this drawing
                replaced: set[str] = set()  # floors given this drawing in place of theirs
                made_locations: dict[str, str] = {}
                made_buildings: dict[tuple[str, str], str] = {}
                for place in places:
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
                        f_id = place.replaces
                        replaced.add(f_id)
                        job.say(f"floor {f_id}: its drawing is now {p.get('title') or 'plan ' + str(p.get('index'))}")
                    else:
                        f_id = ws.add_floor(b_id, place.ordinal, name=p.get("name") or None, source=source,
                                            height=float(p.get("height") or DEFAULT_HEIGHT_M),
                                            parapet_height=float(p["parapet"]) if p.get("parapet") else None)
                        job.say(f"floor {f_id}: {p.get('title') or 'plan ' + str(p.get('index'))}")
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
                                    key=lambda f: f.ordinal)
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

        return self.jobs.submit(f"Adding floors from {drawing}", run, project=code, user=_uid(by))

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

    def convert(self, code: str, floor: str | None = None, force: bool = False, by=None) -> Job:
        """Read floors' drawings again (one, or all). A reading that finds no rooms on a
        floor that has some, or would retire most of them, is held back and the floor
        keeps its rooms (convert.py); ``force`` applies it all the same."""
        if not isinstance(force, bool):
            raise ValueError("force is true or false")
        self._known(code)

        def run(job: Job):
            ws = self.workspace(code)
            floors = [fid for *_, fid in ws.iter_floors() if floor in (None, fid)]
            # a building read for the first time (its first reading failed, say) is put
            # beside the others when its drawing would stack it on one of them
            unread = {f"{ws.id}-{loc.code}-{b.code}" for loc in ws.locations for b in loc.buildings
                      if all(f.converted_at is None for f in b.floors)}
            result = self._convert(code, floors, job, force=force, by=by)
            if unread:
                self._stand_apart(code, unread, job, by=by)
            return result

        return self.jobs.submit("Converting", run, project=code, user=_uid(by),
                                scope=("floor", floor) if floor is not None else ("project", None))

    def _convert(self, code: str, floor_ids: list[str], job: Job, force: bool = False, by=None) -> dict:
        from .convert import convert_floor

        if self.model.available():
            job.say(f"reading texts with {self.model.name}")
        if self.symbols.available():
            job.say(f"spotting symbols with {self.symbols.name} (research use only)")
        if self.vision.available():
            job.say(f"looking at the rooms with {self.vision.name}")
        summaries, held, failed = [], [], []
        with self._changing(code):
            ws = self.store.load(code)
            names = {drawing_name(ws.floor(f).source.path) for f in floor_ids if ws.floor(f).source is not None}
            with self._files(code, names) as folder:
                for fid in floor_ids:
                    if ws.floor(fid).source is None:
                        continue
                    job.say(f"converting {fid}")
                    # a read that would retire most of a floor is not applied: the floor keeps its rooms
                    try:
                        report = convert_floor(ws, fid, folder, self.model, self.symbols,
                                               self.vision if self.vision.available() else None,
                                               say=lambda m: job.say("  " + m), force=force)
                    except Exception as e:  # this floor stays as it was; the others are converted
                        traceback.print_exc()
                        job.say(f"  {fid}: not converted: {_message(e)}")
                        failed.append(f"{fid}: {_message(e)}")
                        saved = self.store.load(code)  # as last saved, with what the models answered meanwhile
                        saved.readings.update(ws.readings)
                        saved.vision.update(ws.vision)
                        ws = saved
                        continue
                    job.say("  " + report.summary())
                    for w in report.warnings:
                        job.say("  warning: " + w)
                    summaries.append(report.summary())
                    if report.held:
                        held.append(fid)
                    self.store.save_floor(ws, fid, by=by, more={"summary": report.summary(), "held": report.held})
        if failed:  # the job fails, the floors converted kept
            raise ValueError(f"not converted: {'; '.join(failed)}")
        return {"summaries": summaries, "held": held}

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

    def export_building(self, code: str, body: dict | None = None) -> str:
        """The building a request to export names (``building``: its ID; may be left out
        when the project has one building)."""
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
        entered in its export history, and kept as sent (its bytes, in the database)."""
        building = self.export_building(code, body)

        def run(job: Job):
            with self._changing(code):
                ws = self.store.load(code)
                seq = (ws.exports[-1].sequence + 1) if ws.exports else 1  # a project opened from export 5 goes on at 6
                # by code, with the building's
                name = f"{ws.id}-{seq:03d}-{building.rsplit('-', 1)[-1]}.storeypath"
                job.say(f"writing {name}")
                self.work.mkdir(parents=True, exist_ok=True)
                manifest, data = make_valid_package(ws, name, building, self.catalogue(), job.say, folder=self.work)
                self.store.save(ws, by=by, part="building", kind="export", targets=[building],
                                more={"file": name, "sequence": manifest.export.sequence}, export_bytes={name: data})
            job.say("valid: " + ", ".join(f"{n} {k}" for k, n in manifest.counts.items()))
            loose = [b for b, p in manifest.placements.items() if not p.placed]
            if loose:
                job.say("not on the map yet (shapes are true, the position is not): " + ", ".join(loose))
            return {"file": name, "counts": manifest.counts}

        return self.jobs.submit("Exporting", run, project=code, scope=("building", building), user=_uid(by))

    def project_file(self, code: str) -> "Download":
        """The project as one file to send (*.storeypath-project): its workspace,
        drawings and item types, for another Studio to continue it (bundle.py). Not a
        package: other systems read a building's."""
        from .bundle import write_project_file

        self._known(code)
        buf = io.BytesIO()
        with self._changing(code):
            ws = self.store.load(code)
            drawings = {}
            for d in self.store.drawings(code):
                drawings[d["name"]] = self.store.drawing_bytes(code, d["name"])
                if (kept := self.store.words(code, d["name"])) is not None:
                    drawings[d["name"] + WORDS] = kept.encode("utf-8")
            write_project_file(ws, drawings, self.catalogue(), buf)
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
        ``sight``, only the floors that person may see (Sight.seen)."""
        from .export import ExportError, preview_package

        ws = self.store.load(code)
        if sight is not None:
            ws = sight.seen(ws)
        if not any(f.converted_at for _, _, f, _ in ws.iter_floors()):
            raise NotFound("nothing converted yet: add floors first")
        buf = io.BytesIO()
        try:
            preview_package(ws, buf, buildings=[building] if building else None, catalogue=self.catalogue())
        except ExportError as e:
            raise NotFound(str(e)) from None
        return buf.getvalue()

    def navigation(self, code: str, building_id: str, start: str | None = None, end: str | None = None,
                   accessible: bool = False) -> dict:
        """A building's walking network as it is now (navigation.build_network: what its
        next package will hold), or the way on it from ``start`` to ``end`` (a node's,
        space's, zone's or item's ID; on lifts and ramps alone when ``accessible``).
        Worked out once a version of the project."""
        from .navigation import NoRoute, route

        if (start is None) != (end is None):
            raise ValueError("a way needs both from and to")
        graph, items = self._network(code, building_id)
        if start is None:
            return {"building_id": building_id, **graph.nav}
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

    def export_file(self, code: str, name: str) -> "Download":
        """A package of the project, as it was sent."""
        self._known(code)
        if not isinstance(name, str) or not name:
            raise NotFound(f"no export {name}")
        name = Path(name).name
        return Download(self.store.export_bytes(code, name), name)


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


def make_valid_package(ws: Workspace, name: str, building: str | None, catalogue, say, folder=None):
    """A building's package made (in a folder of its own, removed after) and checked; when
    it is valid, entered as an export in ``ws`` (not saved) and given back with its
    bytes: (manifest, bytes). One that is not valid is not entered (its number is not
    used up): ValueError, saying what is wrong."""
    import shutil
    import tempfile

    from .export import export_package
    from .validate import validate_package

    where = Path(tempfile.mkdtemp(prefix=WRITING, dir=folder))
    try:
        part = where / name  # the name it is entered under
        before = list(ws.exports)
        manifest = export_package(ws, part, building=building, say=say, catalogue=catalogue)
        errors = validate_package(part)
        for e in errors:
            say("invalid: " + e)
        if errors:
            ws.exports[:] = before
            raise ValueError("the package failed validation: it was not kept, nor entered as an export")
        return manifest, part.read_bytes()
    finally:
        shutil.rmtree(where, ignore_errors=True)


def write_valid_package(ws: Workspace, ws_path: Path, out: Path, building: str | None, catalogue, say):
    """A building's package written to ``out`` and entered as an export in the workspace
    (saved to ``ws_path``) only when it is valid (make_valid_package). Raises
    ValueError, saying what is wrong, when it is not. (The command line's: files.)"""
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest, data = make_valid_package(ws, out.name, building, catalogue, say, folder=out.parent)
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


class FloorPlace(NamedTuple):
    """Where a chosen plan goes: its location and building (an ID when they exist,
    else a new code and name), its floor, and the floor it replaces (its ID) when it
    is a new drawing of a floor the building has."""

    location_id: str | None
    location: tuple[str, str] | None  # (code, name) of a new location
    building_id: str | None
    building: tuple[str, str] | None  # (code, name) of a new building
    ordinal: int
    plan: dict
    replaces: str | None


def _floors_to_add(ws: Workspace, plans: list[dict]) -> list[FloorPlace]:
    """Where each chosen plan goes. A plan names its location and building by ID
    (``location_id``, ``building_id``) or as new ones by name (``location``,
    ``building``; a building named like one of the location's is that one); without
    any, the project's first location, and a building of that name or "Main
    building". New ones get codes from their names that no other has. A plan on a
    floor the building has replaces that floor's drawing (its spaces keep their IDs)
    when it says ``replace``. Fails, before anything changes, when two plans would be
    the same floor, or a plan a floor that exists and is not to be replaced."""
    loc_codes = {loc.code for loc in ws.locations}
    new_locations: dict[str, tuple[str, str]] = {}  # name (lower) -> (code, name)
    new_buildings: dict[tuple[str, str], tuple[str, str]] = {}  # (location key, name lower) -> (code, name)
    chosen: dict[tuple[str, int], str] = {}
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
        if (b_key, ordinal) in chosen:
            raise ValueError(f"{chosen[b_key, ordinal]} and {title} are both floor {ordinal} of {b_name}: give one "
                             "of them another floor or building, or leave it out")
        has = next((f for f in building.floors if f.ordinal == ordinal), None) if building is not None else None
        if has and not p.get("replace"):
            raise ValueError(f"{b_name} already has floor {ordinal} ({has.name}): give {title} another floor, "
                             "or replace that floor's drawing")
        chosen[b_key, ordinal] = title
        out.append(FloorPlace(loc_id, new_loc, b_id, new_b, ordinal, p, f"{b_id}-{has.code}" if has else None))
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
    for key in ("height", "parapet"):
        if p.get(key) not in (None, "", 0):
            value = _finite(p[key], f"{title}: its {key}")
            if not 0 < value <= 100:
                raise ValueError(f"{title}: its {key} is metres, more than 0 and at most 100")
            p[key] = value


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
                 token: str | None = None):
        self.studio, self.accounts, self.address, self.token = studio, accounts, address, token
        self.user = LOCAL if accounts is None else user

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

    def floor(self, code: str, floor_id: str, level: str) -> Sight:
        sight = self.see(code)
        if not sight.floor(floor_id):
            raise NotFound(f"no floor {floor_id}")
        _need(sight.floor(floor_id), level, "this floor")
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

    def object(self, code: str, object_id: str) -> Sight:
        """A space, zone or opening corrected: edit on its floor."""
        from .ids import parse_id

        sight = self.see(code)
        try:
            floor_id = parse_id(object_id).prefix("floor")
        except (ValueError, TypeError):
            floor_id = None
        if floor_id is None or not sight.floor(floor_id) or object_id not in self._workspace(code).objects:
            raise NotFound(f"no active space, zone or opening {object_id}")
        _need(sight.floor(floor_id), "edit", "its floor")
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

    def export(self, code: str, body: dict) -> str:
        """A building's package made: edit on the building (it holds every floor of it)."""
        sight = self.see(code)
        building = self.studio.export_building(code, body)
        if not sight.sees_building(building):
            raise NotFound(f"no building {building}")
        _need(sight.building(building), "edit", "the whole building")
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
                for f in sorted(b.floors, key=lambda f: f.ordinal):
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

    def backup(self) -> Stream:
        """Studio's database, every project and account in it as one moment saw them
        (backup.py), written as it is sent."""
        name = backup_name()

        def done(outcome: str) -> None:
            self.audit("backup", name, outcome)

        return Stream(name, "application/gzip", lambda out: write_backup(self.studio.db, out), done)


CAPABILITY_NAMES = ("backup", "catalogue")
