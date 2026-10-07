"""StoreyPath Studio as a web application: the whole workflow in a browser.

``storeypath serve --data /data`` serves the Studio pages and a JSON API on one
port. Everything runs locally: drawings are read here, the language model runs
here (llama-server, started on first use), and nothing is fetched from anywhere
else — Studio works on a machine with no network at all.

Projects live in the data folder, one folder each named by the project's code
(``<data>/<code>/<code>.spproj`` with its drawings and exports beside it; the name
people give a project is only shown). Long steps — reading a drawing's plans,
converting, exporting — run as jobs, one at a time, and report progress.

    GET  /api/status
    GET  /api/projects                         POST /api/projects {name}
    GET  /api/projects/<code>
    POST /api/projects/<code>/delete {confirm: its name}  the project and everything in it, gone
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
    POST /api/projects/<code>/export           → job; {"buildings": [IDs]} for some of them
    GET  /api/projects/<code>/exports/<file>
    GET  /api/projects/<code>/preview.storeypath[?building=<id>]   the project (or one
                                               building) as it is now, as a package
                                               (not recorded as an export)
    GET  /api/projects/<code>/project.storeypath   the project to send: a package that
                                               carries it, to be continued elsewhere
    PUT  /api/open[?replace=<name>]               a project from a file: a package, or a
                                               project sent (bundle.py)
    GET  /api/jobs/<id>
    and the review editor's calls under /api/projects/<code>/ (see review.py)
"""

from __future__ import annotations

import io
import json
import re
import threading
import traceback
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from importlib.metadata import version
from pathlib import Path
from queue import Queue
from typing import NamedTuple
from urllib.parse import parse_qs, unquote, urlparse

from shapely.geometry import shape
from shapely.ops import unary_union

from .assets import asset_dir
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
from .review import CONTENT_TYPES, File, NotFound, Review, floor_print, floor_print_png
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

    def say(self, line: str) -> None:
        self.log.append(line)

    def view(self) -> dict:
        return {"id": self.id, "title": self.title, "state": self.state, "log": self.log,
                "result": self.result, "error": self.error}


class Jobs:
    """Long steps, run one at a time in the background (drawings are large and the
    language model has one slot)."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._queue: Queue = Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, title: str, fn) -> Job:
        job = Job(uuid.uuid4().hex[:12], title)
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
            job.state, job.started = "running", datetime.now(timezone.utc).isoformat()
            try:
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
        self.jobs = Jobs()
        if warm and self.model.available():
            # Load the model now, in the background, so the first drawing does not
            # wait for 2–3 GB of weights to come off the disk.
            threading.Thread(target=self.model.warm, daemon=True).start()
        self._reviews: dict[Path, Review] = {}
        # One lock per project, held while a job changes it: converting one project
        # (minutes, with vision) never holds up another, and pages only read files.
        self._lock = threading.Lock()  # the tables below
        self._project_locks: dict[Path, threading.RLock] = {}
        self._pending: dict[str, dict] = {}  # drawings sent, waiting for a person to choose what goes
        for left in self.data.glob(f"*/drawings/{INCOMING}*"):  # sent, never cleaned: not kept
            left.unlink(missing_ok=True)

    # ---- projects ---------------------------------------------------------------

    def _workspaces(self) -> dict[str, Path]:
        found = {}
        for path in sorted(self.data.glob("*.spproj")) + sorted(self.data.glob("*/*.spproj")):
            try:
                code = json.loads(path.read_text(encoding="utf-8"))["project"]["code"]
            except (OSError, ValueError, KeyError):
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
                self._reviews[path] = Review(path)
            return self._reviews[path]

    def _changing(self, ws_path: Path) -> threading.RLock:
        """The lock held while a job changes this project."""
        with self._lock:
            return self._project_locks.setdefault(ws_path, threading.RLock())

    def status(self) -> dict:
        from .cad import dwg_converter

        return {
            "version": version("storeypath"),
            "model": self.model.name if self.model.available() else None,
            "model_ready": self.model.ready,
            "symbols": self.symbols.name if self.symbols.available() else None,
            "vision": self.vision.name if self.vision.available() else None,
            "dwg": dwg_converter() is not None,
            "dwg_converter": dwg_converter(),  # "oda", "libredwg" or None
            "data": str(self.data),
            # the 2D plan page: the plan engine compiled (npm run build in viewer/svg)
            "plan": (asset_dir("viewer") / "svg" / "dist" / "index.js").is_file(),
        }

    def projects(self) -> list[dict]:
        out = []
        for code, path in self._workspaces().items():
            ws = Workspace.load(path)
            spaces = [r for r in ws.objects.values() if r.kind == "space" and r.status == "active"]
            out.append({"code": code, "name": ws.project.name, "file": path.name,
                        "floors": sum(1 for _ in ws.iter_floors()), "spaces": len(spaces),
                        "review": sum(1 for r in spaces if ws.review_reasons(r))})
        return sorted(out, key=lambda p: p["name"].lower())

    def create(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("a project needs a name")
        # The folder is named by the project's code, which never changes, not by its
        # name, which people type (and retype, repeat, or write in Arabic).
        taken = self._workspaces()
        ws = Workspace.new(name)
        while ws.id in taken or (self.data / ws.id).exists():
            ws = Workspace.new(name)
        ws.add_location("SITE", name)
        folder = self.data / ws.id
        folder.mkdir(parents=True)
        ws.save(folder / f"{ws.id}.spproj")
        return {"code": ws.id}

    def project(self, code: str) -> dict:
        path = self.path(code)
        ws = Workspace.load(path)
        info = self.review(code).project()
        from .export import footprint, site_positions

        tree = []
        for loc in ws.locations:
            buildings = []
            positions = site_positions(loc)
            for b in loc.buildings:
                b_id = f"{ws.id}-{loc.code}-{b.code}"
                outline = [shape(f.outline) for f in b.floors if f.outline]
                centre = unary_union(outline).centroid if outline else None
                buildings.append({
                    "id": b_id, "code": b.code, "name": b.name,
                    "placement": b.placement.model_dump() if b.placement else None,
                    # its place on the site plan (as its drawing places it when not set) and
                    # its footprint in its own drawing metres, for the site plan to draw
                    "site": {**positions[b.code].model_dump(), "set": b.site is not None},
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
        return {**info, "locations": tree, "drawings": drawings, "exports": exports,
                "exported": len(ws.exports), "exports_folder": str(path.parent / "exports")}

    # ---- drawings ---------------------------------------------------------------

    def upload(self, code: str, name: str, body: bytes, private: bool = True) -> dict | Job:
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

        return self.jobs.submit(f"Looking for private information in {name}", run)

    def keep_private(self, code: str, token: str, body: dict) -> Job:
        """A drawing sent, kept without the private information found in it, all but
        what a person chose to keep (``keep``: ids of found things)."""
        with self._lock:
            p = self._pending.get(token)
            if p is None or p["code"] != code:
                raise NotFound("no drawing waiting to be added: send it again")
            del self._pending[token]
        keep = set(body.get("keep") or [])

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

        return self.jobs.submit(f"Adding {p['name']} without its private information", run)

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
        if (body.get("confirm") or "").strip() != ws.project.name.strip():
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
        path = self.path(code).parent / "drawings" / Path(name).name
        if not path.is_file():
            raise NotFound(f"no drawing {name}")
        return path

    def plans(self, code: str, name: str, units: str | None = None) -> Job:
        """The plans in a drawing, read in ``units``, or in the units it shows."""
        path = self._drawing(code, name)
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

        return self.jobs.submit(f"Reading the plans in {path.name}", run)

    def add_floors(self, code: str, body: dict) -> Job:
        ws_path = self.path(code)
        drawing = self._drawing(code, body.get("drawing", ""))
        units = body.get("units") or None  # the units the plans were found in: kept with each floor
        if units and units not in UNIT_NAMES:
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

        return self.jobs.submit(f"Adding floors from {drawing.name}", run)

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

    def convert(self, code: str, floor: str | None = None) -> Job:
        ws_path = self.path(code)

        def run(job: Job):
            ws = Workspace.load(ws_path)
            floors = [fid for *_, fid in ws.iter_floors() if floor in (None, fid)]
            # a building read for the first time (its first reading failed, say) is put
            # beside the others when its drawing would stack it on one of them
            unread = {f"{ws.id}-{loc.code}-{b.code}" for loc in ws.locations for b in loc.buildings
                      if all(f.converted_at is None for f in b.floors)}
            result = self._convert(ws_path, floors, job)
            if unread:
                self._stand_apart(ws_path, unread, job)
            return result

        return self.jobs.submit("Converting", run)

    def _convert(self, ws_path: Path, floor_ids: list[str], job: Job) -> dict:
        from .convert import convert_floor

        if self.model.available():
            job.say(f"reading texts with {self.model.name}")
        if self.symbols.available():
            job.say(f"spotting symbols with {self.symbols.name} (research use only)")
        if self.vision.available():
            job.say(f"looking at the rooms with {self.vision.name}")
        summaries = []
        with self._changing(ws_path):
            ws = Workspace.load(ws_path)
            for fid in floor_ids:
                if ws.floor(fid).source is None:
                    continue
                job.say(f"converting {fid}")
                report = convert_floor(ws, fid, ws_path.parent, self.model, self.symbols,
                                       self.vision if self.vision.available() else None, say=lambda m: job.say("  " + m))
                job.say("  " + report.summary())
                for w in report.warnings:
                    job.say("  warning: " + w)
                summaries.append(report.summary())
                ws.save(ws_path)
        return {"summaries": summaries}

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
        takes it off the map."""
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
            lat, lon = float(body["lat"]), float(body["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("latitude is -90…90 and longitude -180…180")
            b.placement = Placement(lat=lat, lon=lon, x=float(body.get("x", 0)), y=float(body.get("y", 0)),
                                    bearing=float(body.get("bearing", 0)))
            ws.save(ws_path)
        return {"placement": b.placement.model_dump()}

    def export(self, code: str, body: dict | None = None) -> Job:
        """A package of the project, or (``buildings``: IDs) of some of its buildings."""
        ws_path = self.path(code)
        buildings = (body or {}).get("buildings")
        if buildings is not None and (not isinstance(buildings, list) or not all(isinstance(b, str) for b in buildings)):
            raise ValueError("buildings: a list of building IDs")

        def run(job: Job):
            from .export import export_package
            from .validate import validate_package

            with self._changing(ws_path):
                ws = Workspace.load(ws_path)
                folder = ws_path.parent / "exports"
                folder.mkdir(exist_ok=True)
                seq = (ws.exports[-1].sequence + 1) if ws.exports else 1  # a project opened from export 5 goes on at 6
                # by code, as the folder; a part of the project with its building's code
                part = "" if buildings is None else "-" + (buildings[0].rsplit("-", 1)[-1] if len(buildings) == 1 else "PART")
                out = folder / f"{ws.id}-{seq:03d}{part}.storeypath"
                job.say(f"writing {out.name}")
                manifest = export_package(ws, out, buildings=buildings, say=job.say)
                ws.save(ws_path)
            errors = validate_package(out)
            for e in errors:
                job.say("invalid: " + e)
            if errors:
                raise ValueError("the package failed validation")
            job.say("valid: " + ", ".join(f"{n} {k}" for k, n in manifest.counts.items()))
            loose = [b for b, p in manifest.placements.items() if not p.placed]
            if loose:
                job.say("not on the map yet (shapes are true, the position is not): " + ", ".join(loose))
            return {"file": out.name, "counts": manifest.counts}

        return self.jobs.submit("Exporting", run)

    def project_file(self, code: str) -> "Download":
        """The project as one file to send: a package that carries the project (its
        workspace and drawings), for another Studio to continue it (bundle.py)."""
        from .bundle import export_project

        path = self.path(code)
        buf = io.BytesIO()
        with self._changing(path):
            export_project(path, buf)
        return Download(buf.getvalue(), f"{Workspace.load(path).id}-project.storeypath")

    def open(self, body: bytes, replace: str | None = None) -> dict:
        """A project from a file (a package, or a project sent): put in the data folder.
        When the project is here already, it is put in its place only when its name is
        typed (``replace``) and no job is working on it."""
        from .bundle import ProjectExists, open_file

        if not body:
            raise ValueError("the file is empty")
        tmp = self.data / f".opening-{uuid.uuid4().hex}.storeypath"
        tmp.write_bytes(body)
        try:
            try:
                return open_file(self.data, tmp)
            except ProjectExists as e:
                if replace is None:
                    raise
                if replace.strip() != e.name.strip():
                    raise ValueError(f"type the name of the project here, {e.name}, to replace it") from None
                path = self._workspaces().get(e.code)
                lock = self._changing(path) if path else None
                if lock is not None and not lock.acquire(blocking=False):
                    raise ValueError("a job is working on this project: open the file when the job is done") from None
                try:
                    opened = open_file(self.data, tmp, replace=True)
                    with self._lock:
                        self._reviews.pop(path, None)
                    return opened
                finally:
                    if lock is not None:
                        lock.release()
        except zipfile.BadZipFile:
            raise ValueError("not a StoreyPath file (.storeypath)") from None
        finally:
            tmp.unlink(missing_ok=True)

    def preview(self, code: str, building: str | None = None) -> bytes:
        """The project as a package, as it is now, for the 3D view: built in memory
        and not entered as an export. With ``building``, that building alone."""
        from .export import ExportError, export_package

        ws = Workspace.load(self.path(code))  # saved whole (workspace.py): no lock to read it
        if not any(f.converted_at for _, _, f, _ in ws.iter_floors()):
            raise NotFound("nothing converted yet: add floors first")
        buf = io.BytesIO()
        try:
            export_package(ws, buf, record=False, buildings=[building] if building else None, bake=False)
        except ExportError as e:
            raise NotFound(str(e)) from None
        return buf.getvalue()

    def export_file(self, code: str, name: str) -> Path:
        path = self.path(code).parent / "exports" / Path(name).name
        if not path.is_file():
            raise NotFound(f"no export {name}")
        return path


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


def _number(body: dict, key: str, default: float | None = None) -> float:
    value = body.get(key, default)
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} is a number") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{key} is a number")
    return v


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
        title = p.get("title") or f"plan {p.get('index')}"
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


# ---- HTTP ----------------------------------------------------------------------

def make_server(studio: Studio, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    app_dir = Path(str(resources.files("storeypath") / "review_app")).resolve()
    viewer_dir = asset_dir("viewer").resolve()
    theme = viewer_dir / "src" / "theme.js"
    local_only = host in ("127.0.0.1", "localhost", "::1")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _allowed(self) -> bool:
            # On the loopback interface, refuse requests addressed to other names
            # (DNS rebinding). Published on a network (a container), anyone who can
            # reach the port may use it, as with any server.
            if not local_only:
                return True
            host_name = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host_name in ("127.0.0.1", "localhost", "::1")

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
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
            if not self._allowed():
                return self._json(403, {"error": "forbidden"})
            url = urlparse(self.path)
            path = unquote(url.path)
            if path.startswith("/api/"):
                return self._api("GET", path.split("/")[2:], None, parse_qs(url.query))
            if path == "/theme.js":
                return self._file(theme, viewer_dir)
            if path.startswith("/viewer/"):
                return self._file(viewer_dir / path[len("/viewer/"):], viewer_dir)
            return self._file(app_dir / (path.lstrip("/") or "index.html"), app_dir)

        def do_PUT(self):
            if not self._allowed():
                return self._json(403, {"error": "forbidden"})
            url = urlparse(self.path)
            parts = unquote(url.path).split("/")[2:]
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                return self._json(413, {"error": "the file is too large"})
            # Uploads need a custom header: a page on another site cannot send one
            # without a CORS preflight, which this server never answers.
            if self.headers.get("X-StoreyPath") != "1":
                return self._json(403, {"error": "forbidden"})
            body = self.rfile.read(length)
            self._api("PUT", parts, body, parse_qs(url.query))

        def do_POST(self):
            if not self._allowed() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._json(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid JSON"})
            self._api("POST", unquote(urlparse(self.path).path).split("/")[2:], body, {})

        def _api(self, method: str, parts: list[str], body, query) -> None:
            try:
                data = route(method, parts, body, query)
            except NotFound as e:
                return self._json(404, {"error": str(e)})
            except ProjectExists as e:
                return self._json(409, {"error": f"{e.name} ({e.code}) is here already", "code": e.code, "name": e.name})
            except (DrawingError, ValueError, KeyError, ModelUnavailable) as e:
                return self._json(400, {"error": str(e).strip("'\"")})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
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

    def route(method: str, parts: list[str], body, query: dict):
        match method, parts:
            case "GET", ["status"]:
                return studio.status()
            case "GET", ["projects"]:
                return studio.projects()
            case "POST", ["projects"]:
                return studio.create(body.get("name", ""))
            case "GET", ["jobs", job_id]:
                return studio.jobs.get(job_id)
            case "GET", ["projects", code]:
                return studio.project(code)
            case "POST", ["projects", code, "delete"]:
                return studio.delete(code, body)
            case "GET", ["projects", code, "drawings", name, "words"]:
                return studio.words(code, name)
            case "POST", ["projects", code, "incoming", token]:
                return studio.keep_private(code, token, body)
            case "POST", ["projects", code, "incoming", token, "cancel"]:
                return studio.cancel_private(code, token)
            case "PUT", ["projects", code, "drawings", name]:
                return studio.upload(code, name, body, private=query.get("private", ["1"])[0] != "0")
            case "POST", ["projects", code, "drawings", name, "plans"]:
                return studio.plans(code, name, body.get("units") or None)
            case "POST", ["projects", code, "floors"]:
                return studio.add_floors(code, body)
            case "POST", ["projects", code, "convert"]:
                return studio.convert(code, body.get("floor"))
            case "POST", ["projects", code, "buildings", b_id, "site"]:
                return studio.move(code, b_id, body)
            case "POST", ["projects", code, "locations", loc_id, "arrange"]:
                return studio.arrange(code, loc_id)
            case "POST", ["projects", code, "locations", loc_id, "placement"]:
                return studio.place_site(code, loc_id, body)
            case "POST", ["projects", code, "buildings", b_id, "placement"]:
                return studio.place(code, b_id, body)
            case "POST", ["projects", code, "export"]:
                return studio.export(code, body)
            case "GET", ["projects", code, "exports", name]:
                return studio.export_file(code, name)
            case "GET", ["projects", code, "preview.storeypath"]:
                return studio.preview(code, (query.get("building") or [None])[0])
            case "GET", ["projects", code, "project.storeypath"]:
                return studio.project_file(code)
            case "PUT", ["open"]:
                return studio.open(body, (query.get("replace") or [None])[0])
            # the review editor
            case "GET", ["projects", code, "floors", floor_id]:
                return studio.review(code).floor(floor_id)
            case "GET", ["projects", code, "floors", floor_id, "drawing"]:
                return studio.review(code).drawing(floor_id)
            case "GET", ["projects", code, "floors", floor_id, "print"]:
                return floor_print(studio.review(code), floor_id)
            case "GET", ["projects", code, "floors", floor_id, "print.png"]:
                return floor_print_png(studio.review(code), floor_id)
            case "POST", ["projects", code, "floors", floor_id, "edits"]:
                studio.review(code).edit(floor_id, body)
                return studio.convert(code, floor_id)
            case "POST", ["projects", code, "floors", floor_id, "convert"]:
                return studio.convert(code, floor_id)
            case "POST", ["projects", code, "objects", object_id]:
                return studio.review(code).correct(object_id, body)
            case "GET", ["projects", code, "review"]:
                return studio.review(code).project()
        raise NotFound("not found")

    return ThreadingHTTPServer((host, port), Handler)
