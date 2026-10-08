"""StoreyPath Studio as a web application: the whole workflow in a browser.

``storeypath serve --data /data`` serves the Studio pages and a JSON API on one
port. Everything runs locally: drawings are read here, the language model runs
here (llama-server, started on first use), and nothing is fetched from anywhere
else — Studio works on a machine with no network at all.

Projects live in the data folder, one folder each named by the project's code
(``<data>/<code>/<code>.spproj`` with its drawings and exports beside it; the name
people give a project is only shown). Long steps — reading a drawing's plans,
converting, exporting — run as jobs, one at a time, and report progress.

People log in (accounts.py): every call but logging in, out and the first setup
needs a session, and each needs a level (view, edit, share) on the narrowest part of
a project — the project, a building, a floor — that covers what it reads or changes;
nothing shows another floor's content to someone who may see only some floors. What
each call needs is checked first in route() (the table is in studio/README.md):

    POST /api/login {username, password}       → a session (cookie sp_session)
    POST /api/logout
    POST /api/setup {token, username, name, password}   the first admin (no users yet)
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
    GET  /api/admin/audit                      GET /api/backup (the data folder, .tar.gz)
    and the review editor's calls under /api/projects/<code>/ (see review.py)

What changes something (POST, a JSON object; PUT, a file) is sent with the header
``X-StoreyPath: 1``, which a page of another site cannot send, from a page of Studio's.
Studio answers only to its own names (allowed_hosts: localhost, this machine's name,
an address, and those given with --allowed-host or STOREYPATH_ALLOWED_HOSTS).
"""

from __future__ import annotations

import io
import json
import math
import re
import socket
import ssl
import sys
import threading
import traceback
import uuid
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from importlib.metadata import version
from pathlib import Path
from queue import Queue
from typing import Callable, NamedTuple
from urllib.parse import parse_qs, unquote, urlparse

from shapely.geometry import shape
from shapely.ops import unary_union

from .accounts import (COOKIE, LOCAL, Accounts, Forbidden, Refused, Scope, Sight, Unauthorized,
                       User, check_username, rank, temporary_password)
from .assets import asset_dir
from .backup import backup_name, jobs_paused, write_backup
from .bundle import ProjectExists
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
from .review import CONTENT_TYPES, Busy, File, NotFound, Review, floor_print, floor_print_png
from .types import SpaceType
from .workspace import Placement, SourceDrawing, Workspace

MAX_UPLOAD = 512 * 1024 * 1024
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
    language model has one slot). Each runs holding the data folder's job lock
    (backup.py): a backup waits for it, and no job starts while one is written."""

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
                with jobs_paused(self.data) if self.data is not None else ExitStack():
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

class Studio:
    def __init__(self, data: str | Path, model: LocalModel | None = None, warm: bool = True,
                 symbols: SymbolSpotter | None = None, vision: VisionModel | None = None):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.model = model if model is not None else LocalModel()
        self.symbols = symbols if symbols is not None else SymbolSpotter()
        self.vision = vision if vision is not None else VisionModel()
        self.jobs = Jobs(self.data)
        if warm and self.model.available():
            # Load the model now, in the background, so the first drawing does not
            # wait for 2–3 GB of weights to come off the disk.
            threading.Thread(target=self.model.warm, daemon=True).start()
        self._reviews: dict[Path, Review] = {}
        # One lock per project, held while a job changes it: converting one project
        # (minutes, with vision) never holds up another, and pages only read files.
        self._lock = threading.Lock()  # the tables below
        self._project_locks: dict[Path, threading.RLock] = {}
        self._opening = threading.Lock()  # one file opened at a time (Studio.open)
        self._pending: dict[str, dict] = {}  # drawings sent, waiting for a person to choose what goes
        for left in self.data.glob(f"*/drawings/{INCOMING}*"):  # sent, never cleaned: not kept
            left.unlink(missing_ok=True)
        for left in self.data.glob(f"*/exports/{WRITING}*"):  # a package never finished (Studio stopped)
            import shutil

            shutil.rmtree(left, ignore_errors=True)

    # ---- projects ---------------------------------------------------------------

    def _workspaces(self) -> dict[str, Path]:
        """Every project here, by its code: as bundle.find_project finds one (a folder
        being unpacked or put aside, hidden, holds none)."""
        found = {}
        for path in sorted(self.data.glob("*.spproj")) + sorted(self.data.glob("*/*.spproj")):
            if any(part.startswith(".") for part in path.relative_to(self.data).parts):
                continue
            try:
                code = json.loads(path.read_text(encoding="utf-8"))["project"]["code"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            found.setdefault(code, path)
        return found

    def path(self, code: str) -> Path:
        path = self._workspaces().get(code)
        if path is None:
            raise NotFound(f"no project {code}")
        return path

    def review(self, code: str) -> Review:
        path = self.path(code)
        with self._lock:
            if path not in self._reviews:
                # its changes wait for (or are refused while) a job changes the project
                self._reviews[path] = Review(path, catalogue=self.catalogue,
                                             changing=lambda path=path: self._changing(path))
            return self._reviews[path]

    def catalogue(self):
        """The item types of every project here (catalogue.json in the data folder)."""
        from . import catalogue

        return catalogue.load(self.data)

    def save_catalogue(self, body: dict) -> dict:
        """The catalogue replaced: types may be added, changed or retired, never taken
        out (their codes stay with the items that have them)."""
        from . import catalogue

        with self._lock:
            old = catalogue.load(self.data)
            new = catalogue.Catalogue.model_validate({**body, "format": catalogue.CATALOGUE_FORMAT})
            if gone := sorted({t.code for t in old.types} - {t.code for t in new.types}):
                raise ValueError(f"types are retired, not removed: {', '.join(gone)}")
            catalogue.save(self.data, new)
            return new.model_dump()

    def _changing(self, ws_path: Path) -> threading.RLock:
        """The lock held while a job changes this project."""
        with self._lock:
            return self._project_locks.setdefault(ws_path, threading.RLock())

    def status(self, paths: bool = True) -> dict:
        """What Studio can do; ``paths``: and where its data folder is (for an admin)."""
        from shutil import which

        from .cad import odafc

        return {
            "version": version("storeypath"),
            "model": self.model.name if self.model.available() else None,
            "model_ready": self.model.ready,
            "symbols": self.symbols.name if self.symbols.available() else None,
            "vision": self.vision.name if self.vision.available() else None,
            "dwg": bool(which("dwg2dxf")) or odafc.is_installed(),
            **({"data": str(self.data)} if paths else {}),
            # the 2D plan page: the plan engine compiled (npm run build in viewer/svg)
            "plan": (asset_dir("viewer") / "svg" / "dist" / "index.js").is_file(),
        }

    def projects(self, sight_of=None) -> list[dict]:
        """Every project, or with ``sight_of`` ((code, workspace) -> Sight: what a person
        may see of it), those they may see, each counted in what they see of it, with
        their level on it (``can``)."""
        out = []
        for code, path in self._workspaces().items():
            ws = Workspace.load(path)
            sight = sight_of(code, ws) if sight_of is not None else None
            if sight is not None and not sight.any():
                continue
            seen = sight.seen(ws) if sight is not None else ws
            spaces = [r for r in seen.objects.values() if r.kind == "space" and r.status == "active"]
            entry = {"code": code, "name": ws.project.name, "file": path.name,
                     "floors": sum(1 for _ in seen.iter_floors()), "spaces": len(spaces),
                     "review": sum(1 for r in spaces if seen.review_reasons(r))}
            if sight is not None:
                entry["can"] = {k: v for k, v in sight.can().items() if k not in ("buildings", "floors")}
            out.append(entry)
        return sorted(out, key=lambda p: p["name"].lower())

    def create(self, name: str, kept=()) -> dict:
        """A new project, its code one no project here has, nor one ``kept`` (codes
        whose sharing is kept, accounts.py)."""
        if name is not None and not isinstance(name, str):
            raise ValueError("a project's name is text")
        name = (name or "").strip()
        if not name:
            raise ValueError("a project needs a name")
        # The folder is named by the project's code, which never changes, not by its
        # name, which people type (and retype, repeat, or write in Arabic).
        taken = {*self._workspaces(), *kept}
        ws = Workspace.new(name)
        while ws.id in taken or (self.data / ws.id).exists():
            ws = Workspace.new(name)
        ws.add_location("SITE", name)
        folder = self.data / ws.id
        folder.mkdir(parents=True)
        ws.save(folder / f"{ws.id}.spproj")
        return {"code": ws.id}

    def project(self, code: str, sight: Sight | None = None) -> dict:
        """The project's page: its locations, buildings and floors, drawings and packages.
        With ``sight``, only what that person may see of it: the floors (a building's
        footprint and middle drawn from those alone, where it stands on its site as it
        does for everyone), the drawings of those floors (all of them with a level on
        the whole project), and the packages of the buildings they see whole; where the
        packages are kept on the server, to an admin alone."""
        path = self.path(code)
        full = Workspace.load(path)
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
        drawings = sorted(p.name for p in (path.parent / "drawings").glob("*")
                          if p.suffix.lower() in DRAWING_TYPES and not p.name.startswith(INCOMING)
                          and not p.name.endswith(WORDS)) \
            if (path.parent / "drawings").is_dir() else []
        exports = sorted((p.name for p in (path.parent / "exports").glob("*.storeypath")), reverse=True) \
            if (path.parent / "exports").is_dir() else []
        if sight is not None and not sight.whole:
            used = {Path(f.source.path).name for *_, f, _ in ws.iter_floors() if f.source}
            drawings = [d for d in drawings if d in used]
            exports = [e for e in exports if (held := package_buildings(full, e))
                       and all(rank(sight.building(b)) >= rank("view") for b in held)]
        return {**info, "locations": tree, "drawings": drawings, "exports": exports,
                "exported": len(full.exports) if sight is None or sight.whole else len(exports),
                # where the server keeps them: for an admin alone
                **({"exports_folder": str(path.parent / "exports")} if sight is None or sight.admin else {}),
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

    def upload(self, code: str, name: str, body: bytes, private: bool = True, by: str | None = None) -> dict | Job:
        """A drawing added to the project. Kept private (the default), a job finds what
        names the people and the project — title blocks, names, contacts, hidden file
        data, what the language model reads as private (privacy.py) — and a person
        chooses what of it to keep (keep_private); only that copy is kept, as
        drawing-N.dxf: the file as sent, and its name, are not. Otherwise it is kept
        as sent."""
        name = Path(name).name
        suffix = Path(name).suffix.lower()
        if suffix not in DRAWING_TYPES:
            raise ValueError("drawings are .dwg or .dxf files")
        folder = self.path(code).parent / "drawings"
        folder.mkdir(exist_ok=True)
        if not private:
            (folder / name).write_bytes(body)
            return {"drawing": name, "bytes": len(body)}
        token = uuid.uuid4().hex[:12]
        incoming = folder / f"{INCOMING}{token}{suffix}"
        incoming.write_bytes(body)

        def run(job: Job):
            from .cad import read_drawing_to_change
            from .privacy import Choices, make_private
            from .vision import InWords

            try:
                job.say("reading the drawing")
                doc = read_drawing_to_change(incoming)
                job.say("looking for title blocks, names, contacts and hidden file data")
                reader = InWords(self.vision) if self.vision.available() else \
                    self.model if self.model.available() else None
                choices = Choices()
                report = make_private(doc, reader, job.say, choices)
            except Exception:
                incoming.unlink(missing_ok=True)
                raise
            if not choices.found:  # nothing to choose: kept as it is
                return self._keep_private(folder, incoming, doc, report, job)
            # what was found goes once a person says what to keep; the cleaned copy
            # waits, as it is what keeping nothing gives
            with self._lock:
                self._pending[token] = {"code": code, "folder": folder, "incoming": incoming, "name": name,
                                        "doc": doc, "report": report, "choices": choices, "reader": reader}
            job.say(f"found {len(choices.found)} things to take out: choose what to keep")
            return {"pending": token, "name": name, "found": choices.listed(), "reader": report.model}

        return self.jobs.submit(f"Looking for private information in {name}", run, project=code, user=by)

    def keep_private(self, code: str, token: str, body: dict, by: str | None = None) -> Job:
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

        def run(job: Job):
            from .cad import read_drawing_to_change
            from .privacy import Choices, make_private

            doc, report = p["doc"], p["report"]
            if keep:  # read again, taking out all but what is kept
                job.say(f"taking out all but the {len(keep)} kept")
                try:
                    doc = read_drawing_to_change(p["incoming"])
                    choices = Choices(keep, p["choices"].model_found)
                    report = make_private(doc, p["reader"], job.say, choices)
                except Exception:
                    p["incoming"].unlink(missing_ok=True)
                    raise
            return self._keep_private(p["folder"], p["incoming"], doc, report, job)

        return self.jobs.submit(f"Adding {p['name']} without its private information", run, project=code, user=by)

    def cancel_private(self, code: str, token: str) -> dict:
        with self._lock:
            p = self._pending.pop(token, None)
        if p is None or p["code"] != code:
            raise NotFound("no drawing waiting to be added")
        p["incoming"].unlink(missing_ok=True)
        return {"cancelled": p["name"]}

    def _keep_private(self, folder: Path, incoming: Path, doc, report, job: Job) -> dict:
        """The cleaned drawing kept as drawing-N.dxf, with its words; the file as sent gone."""
        try:
            job.say(report.summary())
            taken = [int(m.group(1)) for p in folder.glob("drawing-*.dxf") if (m := DRAWING_NAME.fullmatch(p.name))]
            out = folder / f"drawing-{max(taken, default=0) + 1}.dxf"
            doc.saveas(out)
            job.say(f"kept as {out.name}")
            _words_of(out).write_text(words(doc, out.name, report.summary()), encoding="utf-8")
            job.say(f"every word left in it: Words, beside {out.name}")
        finally:
            incoming.unlink(missing_ok=True)
        return {"drawing": out.name, "privacy": report.view()}

    def words(self, code: str, name: str) -> File:
        """Every word and string left in a drawing, written when it was added (or now,
        for one added before), as text."""
        path = self._drawing(code, name)
        kept = _words_of(path)
        if not kept.exists() or kept.stat().st_mtime < path.stat().st_mtime:
            from .cad import read_drawing

            kept.write_text(words(read_drawing(path), path.name), encoding="utf-8")
        return File(kept.read_bytes(), "text/plain; charset=utf-8")

    def delete(self, code: str, body: dict) -> dict:
        """A project and everything in it (drawings, floors, corrections, exports),
        gone: only when its name is typed to confirm, no job is changing it, and it has
        a folder of its own in the data folder."""
        import shutil

        path = self.path(code)
        ws = Workspace.load(path)
        confirm = body.get("confirm")
        if not isinstance(confirm, str) or confirm.strip() != ws.project.name.strip():
            raise ValueError("type the project's name to delete it")
        folder = path.parent
        if folder.resolve().parent != self.data.resolve() or folder.name != ws.id:
            raise ValueError("this project is not in a folder of its own: remove it by hand")
        lock = self._changing(path)
        if not lock.acquire(blocking=False):
            raise ValueError("a job is working on this project: delete it when the job is done")
        try:
            shutil.rmtree(folder)
            with self._lock:
                self._reviews.pop(path, None)
                self._project_locks.pop(path, None)
        finally:
            lock.release()
        return {"deleted": code, "name": ws.project.name}

    def _drawing(self, code: str, name: str) -> Path:
        if not isinstance(name, str):
            raise ValueError("drawing: a drawing's name")
        path = self.path(code).parent / "drawings" / Path(name).name
        if not path.is_file():
            raise NotFound(f"no drawing {name}")
        return path

    def plans(self, code: str, name: str, units: str | None = None, by: str | None = None) -> Job:
        """The plans in a drawing, read in ``units``, or in the units it shows."""
        path = self._drawing(code, name)
        if units is not None and not isinstance(units, str):
            raise ValueError(f"units: one of {', '.join(UNIT_NAMES)}")
        if units and units not in UNIT_NAMES:
            raise ValueError(f"unknown units {units!r}: use one of {', '.join(UNIT_NAMES)}")

        def run(job: Job):
            from .sheets import find_views

            job.say(f"reading {path.name}")
            doc = read_drawing(path)
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
            return {"drawing": path.name, "units": used, "units_sure": sure, "units_reason": reason,
                    "units_chosen": bool(units), "units_said": header_units(doc), "levels": levels, "plans": out}

        return self.jobs.submit(f"Reading the plans in {path.name}", run, project=code, user=by)

    def add_floors(self, code: str, body: dict, by: str | None = None) -> Job:
        ws_path = self.path(code)
        drawing = self._drawing(code, body.get("drawing", ""))
        units = body.get("units") or None  # the units the plans were found in: kept with each floor
        if units is not None and not isinstance(units, str) or units and units not in UNIT_NAMES:
            raise ValueError(f"unknown units {units!r}: use one of {', '.join(UNIT_NAMES)}")
        plans = body.get("plans") or []
        if not plans:
            raise ValueError("choose at least one plan")
        _floors_to_add(Workspace.load(ws_path), plans)  # two plans as one floor: say so now, change nothing

        def run(job: Job):
            from .sheets import align, floor_walls
            from .analyse import analyse

            with self._changing(ws_path):
                ws = Workspace.load(ws_path)
                places = _floors_to_add(ws, plans)
                added: dict[str, list[str]] = {}  # building -> floors added or given this drawing
                replaced: set[str] = set()  # floors given this drawing in place of theirs
                made_locations: dict[str, str] = {}
                made_buildings: dict[tuple[str, str], str] = {}
                for place in places:
                    p = place.plan
                    loc_id = place.location_id
                    if loc_id is None:
                        code, name = place.location
                        if code not in made_locations:
                            made_locations[code] = ws.add_location(code, name)
                            job.say(f"location {name} ({code})")
                        loc_id = made_locations[code]
                    b_id = place.building_id
                    if b_id is None:
                        code, name = place.building
                        if (loc_id, code) not in made_buildings:
                            made_buildings[loc_id, code] = ws.add_building(loc_id, code, name)
                            job.say(f"building {name} ({code})")
                        b_id = made_buildings[loc_id, code]
                    source = SourceDrawing(path=str(drawing.relative_to(ws_path.parent)), profile=AUTO,
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
                ws.save(ws_path)

            # Each floor added lines up with its building's lowest floor (from this
            # drawing or another: one drawing per floor is common), so floors stand on
            # one another.
            docs: dict[Path, object] = {}

            def doc_of(f):
                path = ws_path.parent / f.source.path
                if path not in docs:
                    docs[path] = read_drawing(path)
                return docs[path]

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
            with self._changing(ws_path):
                saved = Workspace.load(ws_path)
                for b_id in added:
                    for f in ws.building(b_id).floors:
                        if f.source is not None:
                            saved_floor = next(x for x in saved.building(b_id).floors if x.code == f.code)
                            if saved_floor.source is not None:
                                saved_floor.source.offset = f.source.offset
                saved.save(ws_path)
            self._convert(ws_path, [f for fs in added.values() for f in fs], job)
            if made_buildings:
                self._stand_apart(ws_path, set(made_buildings.values()), job)
            return {"floors": [f for fs in added.values() for f in fs]}

        return self.jobs.submit(f"Adding floors from {drawing.name}", run, project=code, user=by)

    def _stand_apart(self, ws_path: Path, new: set[str], job: Job) -> None:
        """A new building whose drawing would put it on top of another of its site (or
        far off: a drawing with its own origin) is placed beside the others."""
        from .export import beside, site_footprint, site_positions

        with self._changing(ws_path):
            ws = Workspace.load(ws_path)
            moved = False
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
                    moved = True
                    job.say(f"{b.name}: its drawing put it on top of another building (or far off): placed beside them on the site plan")
            if moved:
                ws.save(ws_path)

    def convert(self, code: str, floor: str | None = None, force: bool = False, by: str | None = None) -> Job:
        """Read floors' drawings again (one, or all). A reading that finds no rooms on a
        floor that has some, or would retire most of them, is held back and the floor
        keeps its rooms (convert.py); ``force`` applies it all the same."""
        if not isinstance(force, bool):
            raise ValueError("force is true or false")
        ws_path = self.path(code)

        def run(job: Job):
            ws = Workspace.load(ws_path)
            floors = [fid for *_, fid in ws.iter_floors() if floor in (None, fid)]
            # a building read for the first time (its first reading failed, say) is put
            # beside the others when its drawing would stack it on one of them
            unread = {f"{ws.id}-{loc.code}-{b.code}" for loc in ws.locations for b in loc.buildings
                      if all(f.converted_at is None for f in b.floors)}
            result = self._convert(ws_path, floors, job, force=force)
            if unread:
                self._stand_apart(ws_path, unread, job)
            return result

        return self.jobs.submit("Converting", run, project=code, user=by,
                                scope=("floor", floor) if floor is not None else ("project", None))

    def _convert(self, ws_path: Path, floor_ids: list[str], job: Job, force: bool = False) -> dict:
        from .convert import convert_floor

        if self.model.available():
            job.say(f"reading texts with {self.model.name}")
        if self.symbols.available():
            job.say(f"spotting symbols with {self.symbols.name} (research use only)")
        if self.vision.available():
            job.say(f"looking at the rooms with {self.vision.name}")
        summaries, held, failed = [], [], []
        with self._changing(ws_path):
            ws = Workspace.load(ws_path)
            for fid in floor_ids:
                if ws.floor(fid).source is None:
                    continue
                job.say(f"converting {fid}")
                # a read that would retire most of a floor is not applied: the floor keeps its rooms
                try:
                    report = convert_floor(ws, fid, ws_path.parent, self.model, self.symbols,
                                           self.vision if self.vision.available() else None,
                                           say=lambda m: job.say("  " + m), force=force)
                except Exception as e:  # this floor stays as it was; the others are converted
                    traceback.print_exc()
                    job.say(f"  {fid}: not converted: {_message(e)}")
                    failed.append(f"{fid}: {_message(e)}")
                    saved = Workspace.load(ws_path)  # as last saved, with what the models answered meanwhile
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
                ws.save(ws_path)
        if failed:  # the job fails, the floors converted kept
            raise ValueError(f"not converted: {'; '.join(failed)}")
        return {"summaries": summaries, "held": held}

    def move(self, code: str, building_id: str, body: dict) -> dict:
        """A building moved on its location's site plan: ``x``, ``y`` (metres from the
        site's centre) and ``rotation`` (degrees clockwise). The other buildings stay
        where they are on it."""
        from .export import site_positions
        from .workspace import SitePosition

        x, y, rotation = (_number(body, k) for k in ("x", "y", "rotation"))
        ws_path = self.path(code)
        with self._changing(ws_path):
            ws = Workspace.load(ws_path)
            loc, b = self._building_of(ws, building_id)
            positions = _settle(loc, site_positions(loc))
            b.site = SitePosition(x=round(x, 3), y=round(y, 3), rotation=round(rotation % 360, 3), pivot=positions[b.code].pivot)
            ws.save(ws_path)
        return {"site": b.site.model_dump()}

    def arrange(self, code: str, location_id: str) -> dict:
        """The location's buildings side by side on its site plan, left to right in the
        order of their codes, SITE_GAP_M apart, each as it is turned, centred on the
        site's centre line."""
        from .export import SITE_GAP_M, site_footprint, site_positions
        from .workspace import SitePosition

        ws_path = self.path(code)
        with self._changing(ws_path):
            ws = Workspace.load(ws_path)
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
            ws.save(ws_path)
        return {"sites": {b.code: b.site.model_dump() if b.site else None for b in loc.buildings}}

    def place_site(self, code: str, location_id: str, body: dict) -> dict:
        """The location's site on the map: its centre at ``lat``, ``lon``, its up at
        ``bearing``; every building not placed by itself goes with it. ``clear``
        takes it off the map. Its buildings keep the places they have on it: one
        changed or added later moves no other on the map."""
        from .export import site_positions

        ws_path = self.path(code)
        with self._changing(ws_path):
            ws = Workspace.load(ws_path)
            loc = ws.location(location_id)
            if body.get("clear"):
                loc.placement = None
            else:
                lat, lon = _number(body, "lat"), _number(body, "lon")
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("latitude is -90…90 and longitude -180…180")
                _settle(loc, site_positions(loc))
                loc.placement = Placement(lat=lat, lon=lon, x=0.0, y=0.0, bearing=_number(body, "bearing", 0.0) % 360)
            ws.save(ws_path)
        return {"placement": loc.placement.model_dump() if loc.placement else None}

    def _building_of(self, ws: Workspace, building_id: str):
        for loc in ws.locations:
            for b in loc.buildings:
                if f"{ws.id}-{loc.code}-{b.code}" == building_id:
                    return loc, b
        raise NotFound(f"no building {building_id}")

    def place(self, code: str, building_id: str, body: dict) -> dict:
        ws_path = self.path(code)
        with self._changing(ws_path):
            ws = Workspace.load(ws_path)
            b = ws.building(building_id)
            lat, lon = _number(body, "lat"), _number(body, "lon")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("latitude is -90…90 and longitude -180…180")
            b.placement = Placement(lat=lat, lon=lon, x=_number(body, "x", 0.0), y=_number(body, "y", 0.0),
                                    bearing=_number(body, "bearing", 0.0) % 360)
            ws.save(ws_path)
        return {"placement": b.placement.model_dump()}

    def export_building(self, code: str, body: dict | None = None) -> str:
        """The building a request to export names (``building``: its ID; may be left out
        when the project has one building)."""
        ws_path = self.path(code)
        building = (body or {}).get("building")
        if building is None and isinstance((body or {}).get("buildings"), list) and len(body["buildings"]) == 1:
            building = body["buildings"][0]  # as earlier pages sent it
        if building is not None and not isinstance(building, str):
            raise ValueError("building: a building's ID")
        from .export import building_ids

        known = building_ids(Workspace.load(ws_path))  # saved whole (workspace.py): no lock to read it
        if building is None:
            if len(known) != 1:
                raise ValueError(f"choose the building to export: a package holds one building, this project has {len(known)}")
            building = known[0]
        elif building not in known:
            raise ValueError(f"no building {building} in this project")
        return building

    def export(self, code: str, body: dict | None = None, by: str | None = None) -> Job:
        """The package of one of the project's buildings (``building``: its ID; may be
        left out when the project has one building)."""
        ws_path = self.path(code)
        building = self.export_building(code, body)

        def run(job: Job):
            with self._changing(ws_path):
                ws = Workspace.load(ws_path)
                folder = ws_path.parent / "exports"
                folder.mkdir(exist_ok=True)
                seq = (ws.exports[-1].sequence + 1) if ws.exports else 1  # a project opened from export 5 goes on at 6
                # by code, as the folder, with the building's
                out = folder / f"{ws.id}-{seq:03d}-{building.rsplit('-', 1)[-1]}.storeypath"
                job.say(f"writing {out.name}")
                manifest = write_valid_package(ws, ws_path, out, building, self.catalogue(), job.say)
            job.say("valid: " + ", ".join(f"{n} {k}" for k, n in manifest.counts.items()))
            loose = [b for b, p in manifest.placements.items() if not p.placed]
            if loose:
                job.say("not on the map yet (shapes are true, the position is not): " + ", ".join(loose))
            return {"file": out.name, "counts": manifest.counts}

        return self.jobs.submit("Exporting", run, project=code, scope=("building", building), user=by)

    def project_file(self, code: str) -> "Download":
        """The project as one file to send (*.storeypath-project): its workspace,
        drawings and item types, for another Studio to continue it (bundle.py). Not a
        package: other systems read a building's."""
        from .bundle import export_project

        path = self.path(code)
        buf = io.BytesIO()
        with self._changing(path):
            export_project(path, buf)
        return Download(buf.getvalue(), f"{Workspace.load(path).id}.storeypath-project")

    def open(self, body: bytes, replace: str | None = None, allow=None, learn_types: bool = True) -> dict:
        """A project from a file (a building's package, or a project file): put in the
        data folder. A package of a project here adds its building to it; when that
        building is here already, or the file is a project file of a project here, it
        is put in its place only when the project's name is typed (``replace``). No
        job may be working on the project meanwhile.

        The file is read once (bundle.read_file: its parts name one project), and that
        project is the one checked and the one written: ``allow`` (the file as read,
        the workspace file of its project here or None) raises when the person may not,
        and may return what undoes what it did (a new project's owner) should the file
        not be opened after all. One file is opened at a time, so what was checked is
        still so when it is written. ``learn_types``: the item types its catalogue has
        and Studio's lacks are added (by who may change them), else listed as not added
        (``item_types_not_added``)."""
        from .bundle import ProjectExists, find_project, open_file, read_file

        if not isinstance(body, bytes) or not body:
            raise ValueError("the file is empty")
        tmp = self.data / f".opening-{uuid.uuid4().hex}.storeypath"
        tmp.write_bytes(body)
        try:
            incoming = read_file(tmp)
            with self._opening:
                path = find_project(self.data, incoming.code)
                undo = allow(incoming, path) if allow is not None else None
                try:
                    lock = self._changing(path) if path else None
                    if lock is not None and not lock.acquire(blocking=False):
                        raise ValueError("a job is working on this project: open the file when the job is done")
                    try:
                        try:
                            opened = open_file(self.data, tmp, incoming=incoming, existing=path,
                                               learn_types=learn_types)
                        except ProjectExists as e:
                            if replace is None:
                                raise
                            if replace.strip() != e.name.strip():
                                raise ValueError(f"type the name of the project here, {e.name}, to replace "
                                                 f"{'its building ' + e.building if e.building else 'it'}") from None
                            opened = open_file(self.data, tmp, replace=True, incoming=incoming, existing=path,
                                               learn_types=learn_types)
                    finally:
                        if lock is not None:
                            lock.release()
                except BaseException:
                    if undo is not None:
                        undo()
                    raise
                if path is not None:
                    with self._lock:
                        self._reviews.pop(path, None)
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

        ws = Workspace.load(self.path(code))  # saved whole (workspace.py): no lock to read it
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

    def export_file(self, code: str, name: str) -> Path:
        path = self.path(code).parent / "exports" / Path(name).name
        if not path.is_file():
            raise NotFound(f"no export {name}")
        return path


WRITING = ".writing-"  # a package being written, beside where it goes, until it is found valid


def package_buildings(ws: Workspace, name: str) -> list[str] | None:
    """The buildings a package of the project's exports folder holds, as the export that
    wrote it entered them; None when no export entered it (or it held the whole project,
    as packages before one building a package did)."""
    record = next((r for r in reversed(ws.exports) if r.file == Path(name).name), None)
    return list(record.buildings) if record is not None and record.buildings else None


def write_valid_package(ws: Workspace, ws_path: Path, out: Path, building: str | None, catalogue, say):
    """A building's package written to ``out`` and entered as an export in the workspace
    (saved to ``ws_path``) only when it is valid: it is written beside ``out`` first and
    checked; one that is not valid is not kept, nor entered (its number is not used up).
    Raises ValueError, saying what is wrong, when it is not."""
    import shutil
    import tempfile

    from .export import export_package
    from .validate import validate_package

    out.parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=WRITING, dir=out.parent))  # beside it: put in place by a rename
    try:
        part = folder / out.name  # the name it is entered under
        manifest = export_package(ws, part, building=building, say=say, catalogue=catalogue)
        errors = validate_package(part)
        for e in errors:
            say("invalid: " + e)
        if errors:
            raise ValueError("the package failed validation: it was not kept, nor entered as an export")
        part.replace(out)
        try:
            ws.save(ws_path)
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        return manifest
    finally:
        shutil.rmtree(folder, ignore_errors=True)


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
            raise Unauthorized("log in to use Studio", setup=not self.accounts.has_users())
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
        """Logging in, out, and the first setup: asked by anyone."""

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
        return self.studio.review(code).workspace()  # NotFound when there is no such project

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
        folder = self.studio.path(code).parent
        mine = (folder / f.source.path).resolve()
        for *_, g, g_id in ws.iter_floors():
            if g_id != floor_id and g.source is not None and (folder / g.source.path).resolve() == mine \
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
        """A job, to its person, and to those who may see what it works on."""
        user = self._who()
        job = self.studio.jobs.get(job_id)
        if self.accounts is None or user.role == "admin" or (job.user is not None and job.user == user.id):
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

        def allow(incoming, path):
            code = incoming.code
            if path is None:
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

    def setup(self, body: dict) -> LoggedIn:
        if self.accounts is None:
            raise NotFound("Studio runs without accounts here")
        token, user = self.accounts.setup(body.get("token"), body.get("username"), body.get("name"),
                                          body.get("password"), self.address)
        return LoggedIn(token, {"user": self._me(user), "must_change_password": False})

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
        self._workspace(code)
        user = self.accounts.user(body.get("user")) if self.accounts and isinstance(body.get("user"), str) else None
        if user is None or not user.active:
            raise ValueError("no such user (or disabled)")
        before = self.accounts.project_access(code).owner
        self.accounts.set_owner(code, user.id)
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
        return [{**u.view(full=True)} for u in self._accounts().users()]

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
        """The data folder as a .tar.gz, written as it is sent; no job runs meanwhile."""
        stack = ExitStack()
        try:
            stack.enter_context(jobs_paused(self.studio.data, timeout=BACKUP_WAIT_S))
        except TimeoutError:
            raise Busy("a job is running (reading a drawing, converting or exporting): download the backup "
                       "when it is done") from None
        name = backup_name()

        def done(outcome: str) -> None:
            stack.close()
            self.audit("backup", name, outcome)

        return Stream(name, "application/gzip", lambda out: write_backup(self.studio.data, out), done)


CAPABILITY_NAMES = ("backup", "catalogue")


# ---- HTTP ----------------------------------------------------------------------

MAX_JSON = 64 * 1024 * 1024  # a request's JSON body
SMALL_JSON = 4096  # …logging in, the setup and one's own password: a few names and passwords
SMALL_CALLS = (["login"], ["setup"], ["me", "password"])
DRAIN_MAX = 1024 * 1024  # a refused request's body is read (and dropped) up to this size; else the connection closes
ALLOWED_HOSTS_ENV = "STOREYPATH_ALLOWED_HOSTS"  # more names Studio may be reached by: "studio.example.org, studio"


def allowed_hosts(bound: str = "127.0.0.1", more=()) -> set[str]:
    """The names a browser may reach Studio by (the Host it sends, and the origin of a
    page that changes something): localhost and the loopback addresses, this machine's
    own name, the address it is bound to when that is a name, and those given (``more``,
    and STOREYPATH_ALLOWED_HOSTS, separated by commas or spaces; "*": any). An address
    (192.168.1.20, [fd00::5]) is always allowed as a Host: a page reached by another
    name that resolves here (DNS rebinding) sends that name, never an address."""
    import os
    import socket

    names = {"localhost", "127.0.0.1", "::1"}
    try:
        own = socket.gethostname().strip().lower()
    except OSError:
        own = ""
    if own:
        short = own.split(".")[0]
        names |= {own, short, f"{short}.local"}
    if bound and bound not in ("0.0.0.0", "::", ""):
        names.add(bound.strip("[]").lower())
    given = list(more or []) + re.split(r"[,\s]+", os.environ.get(ALLOWED_HOSTS_ENV, ""))
    names |= {n.strip().strip("[]").lower() for n in given if n and n.strip()}
    return names


def _host_name(value: str) -> str | None:
    """The host in a Host header's value or an origin's host[:port], lowercase, without
    its port or brackets; None when it is not one."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        name, port = value[1:end], value[end + 1:]
        if end < 0 or (port and not re.fullmatch(r":\d{1,5}", port)):
            return None
        return name or None
    name, _, port = value.partition(":")
    if (port and not re.fullmatch(r"\d{1,5}", port)) or not re.fullmatch(r"[a-z0-9._-]+", name):
        return None
    return name


def _is_address(name: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


LOOPBACK = ("127.0.0.1", "localhost", "::1")
HANDSHAKE_S = 10.0  # a connection says what it speaks, and makes its TLS handshake, within this
READ_TIMEOUT_S = 60.0  # a connection that sends nothing (or takes nothing sent) for this long is let go
MAX_CONNECTIONS = 128  # connections served at once (each holds a thread and a file); more are closed


class StudioServer(ThreadingHTTPServer):
    """The server: a connection a thread (its TLS handshake made there, not where
    connections are accepted, so one slow client holds up no other), at most
    ``max_connections`` at once: one more is closed as it comes. A connection whose
    handshake fails or times out, or that stops sending, is let go quietly."""

    daemon_threads = True
    max_connections = MAX_CONNECTIONS

    def __init__(self, *args, **kwargs):
        self._open = 0  # connections being served
        self._open_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        with self._open_lock:
            full = self._open >= self.max_connections
            if not full:
                self._open += 1
        if full:
            self.shutdown_request(request)  # closed: nothing read, nothing answered
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._served()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._served()

    def _served(self) -> None:
        with self._open_lock:
            self._open -= 1

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def trusted_networks(more=()) -> list:
    """The proxies Studio takes the client's address from: ``more`` and
    STOREYPATH_TRUSTED_PROXIES (addresses or networks, separated by commas or spaces)."""
    import ipaddress
    import os

    given = [*more, *re.split(r"[,\s]+", os.environ.get("STOREYPATH_TRUSTED_PROXIES", ""))]
    out = []
    for g in (x.strip() for x in given):
        if not g:
            continue
        try:
            out.append(ipaddress.ip_network(g, strict=False))
        except ValueError:
            raise ValueError(f"a trusted proxy is an address or a network (10.0.0.5, 10.0.0.0/24): {g!r}") from None
    return out


def client_address(peer: str, headers, trusted: list) -> str | None:
    """Who is asking: the connection's address, unless it is a trusted proxy's, then the
    address that proxy says it serves: X-Real-IP (nginx: proxy_set_header X-Real-IP
    $remote_addr), else the nearest address in X-Forwarded-For that is not a trusted
    proxy's (read from the right: what a client puts there itself is further left).
    None when a trusted proxy says neither, or says something that is not an address:
    refused, never taken as the proxy's own (every person would share one address, and
    one person's failed logins would make everyone wait)."""
    import ipaddress

    def ip(text):
        try:
            return ipaddress.ip_address(text.strip().strip("[]"))
        except ValueError:
            return None

    if not trusted or not peer:
        return peer
    at = ip(peer)
    if at is None or not any(at in n for n in trusted):
        return peer  # not a proxy we trust: what it says of others is not heard
    real = headers.get_all("X-Real-IP") or []
    if len(real) == 1:
        got = ip(real[0])
        return str(got) if got is not None else None
    if len(real) > 1:
        return None
    chain = [x for h in (headers.get_all("X-Forwarded-For") or []) for x in h.split(",") if x.strip()]
    for hop in reversed(chain):
        got = ip(hop)
        if got is None:
            return None
        if not any(got in n for n in trusted):
            return str(got)
    return None


def make_server(studio: Studio, host: str = "127.0.0.1", port: int = 8080,
                allowed: set[str] | list[str] | tuple = (), *, accounts: Accounts | None,
                secure_cookies: bool = False, tls: ssl.SSLContext | None = None,
                trusted_proxies=()) -> ThreadingHTTPServer:
    """Studio's pages and API on ``host``:``port``. Reached by names other than those
    of allowed_hosts (``allowed``: more of them), it answers 403.

    ``accounts`` is asked for by name, so that no caller gets an open server by
    default: people log in, and each call is let through by what they may do (Gate).
    None — no accounts, everyone may do everything — only on this computer (a loopback
    address), as ``storeypath review`` serves one project.

    ``tls``: served over HTTPS with that context (tls.py), and plain HTTP sent to the
    same port is answered with a redirect to its https:// address (what a connection
    speaks is told by its first byte, 0x16 for TLS). The session cookie is Secure over
    HTTPS, or with ``secure_cookies`` (plain HTTP behind a proxy that speaks HTTPS).

    ``trusted_proxies``: the proxies (addresses or networks; and those of
    STOREYPATH_TRUSTED_PROXIES) whose X-Real-IP (or X-Forwarded-For) says who is asking,
    for the limits on failed logins and the audit; a call through one that does not say
    is refused (client_address).

    A connection is let go when it sends nothing (or takes nothing it is sent) for
    READ_TIMEOUT_S, over HTTPS and plain HTTP alike; MAX_CONNECTIONS are served at once,
    and one more is closed as it comes."""
    if accounts is None and host not in LOOPBACK:
        raise ValueError(f"without accounts Studio serves this computer alone (127.0.0.1), not {host}")
    app_dir = Path(str(resources.files("storeypath") / "review_app")).resolve()
    viewer_dir = asset_dir("viewer").resolve()
    theme = viewer_dir / "src" / "theme.js"
    names = allowed_hosts(host, allowed)
    any_name = "*" in names
    secure = secure_cookies or tls is not None
    proxies = trusted_networks(trusted_proxies)

    def cookie(token: str | None) -> str:
        parts = [f"{COOKIE}={token or ''}", "HttpOnly", "SameSite=Strict", "Path=/"]
        if token is None:
            parts.append("Max-Age=0")
        if secure:
            parts.append("Secure")
        return "; ".join(parts)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # every read (and write) of a connection waits this long at most: a slow link
        # sending a large file is served as long as it sends; one that stops is let go
        timeout = READ_TIMEOUT_S
        _unread = 0  # what is left of the request's body: -1, not known (the connection is not kept)

        _plain = False  # plain HTTP to a port that speaks HTTPS: answered with a redirect

        def setup(self):
            if tls is not None:
                # in this connection's own thread: what it speaks, then its handshake
                sock = self.request
                sock.settimeout(HANDSHAKE_S)
                first = sock.recv(1, socket.MSG_PEEK)
                if first == b"\x16":  # a TLS handshake begins so
                    self.request = tls.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
                    self.request.do_handshake()
                elif first:
                    self._plain = True
                else:
                    raise ConnectionError("closed before it said anything")
            super().setup()  # (the read timeout, from here on)

        def finish(self):
            try:
                super().finish()
            finally:
                if isinstance(self.request, ssl.SSLSocket):
                    try:
                        self.request.close()
                    except OSError:
                        pass

        def handle(self):
            if self._plain:
                return self._to_https()
            return super().handle()

        def _to_https(self) -> None:
            """Plain HTTP to the HTTPS port: the same address, over HTTPS (for one of
            Studio's own names; refused for any other)."""
            self.close_connection = True
            self.raw_requestline = self.rfile.readline(65537)
            if not self.raw_requestline or not self.parse_request():
                return
            host = (self.headers.get("Host") or "").strip()
            name = _host_name(host)
            if name is None or not (any_name or name in names or _is_address(name)):
                return self._refuse()
            target = urlparse(self.path)
            where = (target.path or "/") + (f"?{target.query}" if target.query else "")
            if not where.startswith("/") or where.startswith("//"):
                where = "/"
            self._send(307, b"Studio speaks HTTPS here\n", "text/plain; charset=utf-8",
                       {"Location": f"https://{host}{where}"})

        def log_message(self, *args):
            pass

        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            lengths = self.headers.get_all("Content-Length") or []
            if self.headers.get("Transfer-Encoding") or len(lengths) > 1 \
                    or (lengths and not re.fullmatch(r"\d{1,15}", lengths[0].strip())):
                self._unread = -1  # where the body ends is not known: answered, and the connection closed
            else:
                self._unread = int(lengths[0]) if lengths else 0
            return True

        def _body(self) -> bytes:
            data = self.rfile.read(self._unread) if self._unread > 0 else b""
            self._unread = 0
            return data

        def _allowed(self, changes: bool = False) -> bool:
            """Whether the request may be answered: addressed to one of Studio's names,
            and, when it ``changes`` something, sent by a page of Studio's (its Origin,
            when it has one: browsers send it with every POST and PUT). A page of another
            site cannot send what changes things (application/json, X-StoreyPath: 1)
            without a CORS preflight, which this server never answers; reached through
            another name that resolves here (DNS rebinding), it is refused here."""
            host_header = self.headers.get("Host") or ""
            name = _host_name(host_header)
            if name is None or not (any_name or name in names or _is_address(name)):
                return False
            origin = self.headers.get("Origin")
            if not changes or origin is None:
                return True
            u = urlparse(origin)
            if u.scheme not in ("http", "https") or not u.netloc:
                return False  # "null": a sandboxed frame, a file
            if u.netloc.lower() == host_header.strip().lower():
                return True  # this page's own
            theirs = _host_name(u.netloc)
            return theirs is not None and (any_name or theirs in names)

        def _refuse(self, status: int = 403, error: str = "forbidden") -> None:
            """A request refused as it came: never kept on the connection, where its body,
            unread, would be read as the next request."""
            self.close_connection = True
            self._json(status, {"error": error})

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            if self._unread:
                # answered before its body was read: a small body is read and dropped; any
                # other ends the connection (never read as another request)
                if 0 < self._unread <= DRAIN_MAX:
                    try:
                        self._body()
                    except OSError:
                        pass
                self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")  # never shown in another page's frame (clickjacking)
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def _file(self, path: Path, root: Path) -> None:
            path = path.resolve()
            if root not in path.parents or not path.is_file():
                return self._json(404, {"error": "not found"})
            ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
            self._send(200, path.read_bytes(), ctype)

        def do_GET(self):
            if self._unread < 0:
                return self._refuse(400, "a request's body has a Content-Length")
            if not self._allowed():
                return self._refuse()
            url = urlparse(self.path)
            path = unquote(url.path)
            if path.startswith("/api/"):
                return self._api("GET", path.split("/")[2:], None, parse_qs(url.query))
            if path == "/theme.js":
                return self._file(theme, viewer_dir)
            if path.startswith("/viewer/"):
                return self._file(viewer_dir / path[len("/viewer/"):], viewer_dir)
            return self._file(app_dir / (path.lstrip("/") or "index.html"), app_dir)

        def _changing(self) -> bool:
            """Whether a request that changes something may: addressed to Studio, from a
            page of Studio's, with the header a page of another site cannot send without
            a CORS preflight (which this server never answers). Refused (the connection
            closed, its body unread) when not."""
            if self._unread < 0:
                self._refuse(400, "a request's body has a Content-Length")
                return False
            if not self._allowed(changes=True) or self.headers.get("X-StoreyPath") != "1":
                self._refuse()
                return False
            return True

        def do_PUT(self):
            if not self._changing():
                return
            if self._unread > MAX_UPLOAD:
                return self._refuse(413, "the file is too large")
            url = urlparse(self.path)
            parts = unquote(url.path).split("/")[2:]
            body = self._body()
            self._api("PUT", parts, body, parse_qs(url.query))

        def do_POST(self):
            if not self._changing():
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._refuse()
            parts = unquote(urlparse(self.path).path).split("/")[2:]
            # what anyone may send (logging in, the setup) is small: never read beyond it
            if self._unread > (SMALL_JSON if parts in SMALL_CALLS else MAX_JSON):
                return self._refuse(413, "too much to send at once")
            try:
                # NaN and Infinity are not JSON: refused, never stored
                body = json.loads(self._body() or b"{}", parse_constant=_not_a_number)
            except ValueError:
                return self._json(400, {"error": "invalid JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "send a JSON object"})
            self._api("POST", parts, body, {})

        def _token(self) -> str | None:
            """The session's token, from the request's cookie."""
            for part in (self.headers.get("Cookie") or "").split(";"):
                key, _, value = part.strip().partition("=")
                if key == COOKIE and value:
                    return value
            return None

        def _api(self, method: str, parts: list[str], body, query) -> None:
            address = client_address(self.client_address[0] if self.client_address else "", self.headers, proxies)
            if address is None:
                return self._refuse(400, "Studio is reached through a trusted proxy that does not say who is asking: "
                                         "set X-Real-IP there (nginx: proxy_set_header X-Real-IP $remote_addr;)")
            token = self._token()
            user = accounts.session(token) if accounts is not None else LOCAL
            may = Gate(studio, accounts, user, address, token)
            try:
                data = route(method, parts, body, query, may)
            except Refused as e:
                extra = {"Retry-After": str(e.more["retry_after"])} if "retry_after" in e.more else None
                return self._send(e.status, json.dumps({"error": str(e), **e.more}, ensure_ascii=False).encode(),
                                  "application/json", extra)
            except NotFound as e:
                return self._json(404, {"error": str(e)})
            except ProjectExists as e:
                return self._json(409, {"error": str(e), "code": e.code, "name": e.name, "building": e.building})
            except Busy as e:  # a job is changing the project: nothing changed
                return self._json(409, {"error": str(e), "busy": True})
            except (DrawingError, ValueError, KeyError, ModelUnavailable) as e:
                return self._json(400, {"error": str(e).strip("'\"")})
            except Exception:
                traceback.print_exc()  # what went wrong, in the server's log: never in the answer
                return self._json(500, {"error": "something went wrong in Studio: its log says what"})
            if isinstance(data, LoggedIn):
                return self._send(200, json.dumps(data.data, ensure_ascii=False).encode(), "application/json",
                                  {"Set-Cookie": cookie(data.token)})
            if isinstance(data, LoggedOut):
                return self._send(200, b'{"logged_out": true}', "application/json", {"Set-Cookie": cookie(None)})
            if isinstance(data, Stream):
                return self._stream(data)
            if isinstance(data, Download):  # made on the fly, saved by the browser
                return self._send(200, data.data, "application/zip",
                                  {"Content-Disposition": f'attachment; filename="{data.name}"'})
            if isinstance(data, File):  # a file shown as it is (a floor's print)
                return self._send(200, data.data, data.content_type, {"Cache-Control": "private, max-age=86400"})
            if isinstance(data, Path):  # a file to download
                return self._send(200, data.read_bytes(), "application/zip",
                                  {"Content-Disposition": f'attachment; filename="{data.name}"'})
            if isinstance(data, bytes):  # a package made on the fly
                return self._send(200, data, "application/zip")
            if isinstance(data, Job):
                data = data.view()
            self._json(200, data)

        def _stream(self, s: Stream) -> None:
            """A download written as it is made: the connection closes after it, which is
            how the browser knows it is whole (a stream cut short ends gzip unfinished)."""
            outcome = "failed"
            try:
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", s.content_type)
                self.send_header("Content-Disposition", f'attachment; filename="{s.name}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                s.write(self.wfile)
                self.wfile.flush()
                outcome = "ok"
            except (OSError, ssl.SSLError):
                outcome = "interrupted"
            finally:
                s.done(outcome)

    def route(method: str, parts: list[str], body, query: dict, may: Gate):
        # Every case asks the gate first (what it needs: tests/test_auth_http.py checks
        # that each does, and tries each as people with each kind of access).
        match method, parts:
            # logging in and out, and the person's own
            case "POST", ["login"]:
                may.anyone()
                return may.login(body)
            case "POST", ["logout"]:
                may.anyone()
                return may.logout()
            case "POST", ["setup"]:
                may.anyone()
                return may.setup(body)
            case "GET", ["me"]:
                may.me()
                return may.whoami()
            case "POST", ["me", "password"]:
                may.me()
                return may.change_password(body)
            case "GET", ["status"]:
                user = may.logged_in()
                return studio.status(paths=user.role == "admin")
            case "GET", ["catalogue"]:
                may.logged_in()
                return studio.catalogue().model_dump()
            case "POST", ["catalogue"]:
                may.capability("catalogue")
                return studio.save_catalogue(body)
            # projects
            case "GET", ["projects"]:
                may.logged_in()
                return studio.projects(may.sight_of)
            case "POST", ["projects"]:
                may.create()
                return may.made(studio.create(body.get("name", ""), may.kept_codes()))
            case "PUT", ["open"]:
                allow = may.opening()
                return may.opened(studio.open(body, (query.get("replace") or [None])[0], allow,
                                              learn_types=may.user.can("catalogue")))
            case "GET", ["jobs", job_id]:
                return may.job(job_id)
            case "GET", ["projects", code]:
                sight = may.see(code)
                return studio.project(code, sight)
            case "GET", ["projects", code, "review"]:
                sight = may.see(code)
                return studio.review_project(code, sight)
            case "POST", ["projects", code, "delete"]:
                may.own(code)
                return may.deleted(code, studio.delete(code, body))
            # sharing
            case "GET", ["projects", code, "access"]:
                sight = may.share_some(code)
                return may.access(code, sight)
            case "POST", ["projects", code, "access"]:
                sight = may.share_some(code)
                return may.grant(code, sight, body)
            case "POST", ["projects", code, "owner"]:
                may.admin()
                return may.set_owner(code, body)
            case "GET", ["users"]:
                may.picker()
                return may.users_to_share()
            # the project's drawings: they are the project's, and one may hold several floors
            case "GET", ["projects", code, "drawings", name, "words"]:
                may.project(code, "edit")
                return studio.words(code, name)
            case "POST", ["projects", code, "incoming", token]:
                may.project(code, "edit")
                return studio.keep_private(code, token, body, by=may.uid)
            case "POST", ["projects", code, "incoming", token, "cancel"]:
                may.project(code, "edit")
                return studio.cancel_private(code, token)
            case "PUT", ["projects", code, "drawings", name]:
                may.project(code, "edit")
                return studio.upload(code, name, body, private=query.get("private", ["1"])[0] != "0", by=may.uid)
            case "POST", ["projects", code, "drawings", name, "plans"]:
                may.project(code, "edit")
                return studio.plans(code, name, body.get("units") or None, by=may.uid)
            case "POST", ["projects", code, "floors"]:
                may.add_floors(code, body)
                return studio.add_floors(code, body, by=may.uid)
            case "POST", ["projects", code, "convert"]:
                may.convert(code, body.get("floor"))
                return studio.convert(code, body.get("floor"), body.get("force", False), by=may.uid)
            # buildings and sites
            case "POST", ["projects", code, "buildings", b_id, "site"]:
                may.building(code, b_id, "edit")
                return studio.move(code, b_id, body)
            case "POST", ["projects", code, "buildings", b_id, "placement"]:
                may.building(code, b_id, "edit")
                return studio.place(code, b_id, body)
            case "POST", ["projects", code, "locations", loc_id, "arrange"]:
                may.project(code, "edit")
                return studio.arrange(code, loc_id)
            case "POST", ["projects", code, "locations", loc_id, "placement"]:
                may.project(code, "edit")
                return studio.place_site(code, loc_id, body)
            # packages
            case "POST", ["projects", code, "export"]:
                building = may.export(code, body)
                job = studio.export(code, {"building": building}, by=may.uid)
                may.audit("export", building)
                return job
            case "GET", ["projects", code, "exports", name]:
                may.export_file(code, name)
                return studio.export_file(code, name)
            case "GET", ["projects", code, "preview.storeypath"]:
                sight = may.preview(code, (query.get("building") or [None])[0])
                return studio.preview(code, (query.get("building") or [None])[0], sight)
            case "GET", ["projects", code, "project.storeypath"] | ["projects", code, "project.storeypath-project"]:
                may.project(code, "view")
                return studio.project_file(code)
            # the review editor
            case "GET", ["projects", code, "floors", floor_id]:
                may.floor(code, floor_id, "view")
                return studio.review(code).floor(floor_id)
            case "GET", ["projects", code, "floors", floor_id, "drawing"]:
                may.drawing(code, floor_id)
                return studio.review(code).drawing(floor_id)
            case "GET", ["projects", code, "floors", floor_id, "print"]:
                may.drawing(code, floor_id)
                return floor_print(studio.review(code), floor_id)
            case "GET", ["projects", code, "floors", floor_id, "print.png"]:
                may.drawing(code, floor_id)
                return floor_print_png(studio.review(code), floor_id)
            case "POST", ["projects", code, "floors", floor_id, "edits"]:
                may.floor(code, floor_id, "edit")
                studio.review(code).edit(floor_id, body)
                return studio.convert(code, floor_id, by=may.uid)
            case "POST", ["projects", code, "floors", floor_id, "items"]:
                may.floor(code, floor_id, "edit")
                return studio.review(code).add_item(floor_id, body)
            case "POST", ["projects", code, "items", item_id]:
                may.item(code, item_id, body)
                return studio.review(code).change_item(item_id, body)
            case "POST", ["projects", code, "floors", floor_id, "convert"]:
                may.floor(code, floor_id, "edit")
                return studio.convert(code, floor_id, body.get("force", False), by=may.uid)
            case "POST", ["projects", code, "objects", object_id]:
                may.object(code, object_id)
                return studio.review(code).correct(object_id, body)
            # users (admins), the audit log and backups
            case "GET", ["admin", "users"]:
                may.admin()
                return may.all_users()
            case "POST", ["admin", "users"]:
                may.admin()
                return may.add_user(body)
            case "POST", ["admin", "users", user_id]:
                may.admin()
                return may.change_user(user_id, body)
            case "POST", ["admin", "users", user_id, "password"]:
                may.admin()
                return may.reset_password(user_id)
            case "GET", ["admin", "audit"]:
                may.admin()
                return may.audit_log(query)
            case "GET", ["backup"]:
                may.capability("backup")
                return may.backup()
        raise NotFound("not found")

    srv = StudioServer((host, port), Handler)
    srv.max_connections = MAX_CONNECTIONS
    return srv
