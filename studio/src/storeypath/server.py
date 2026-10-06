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
    PUT  /api/projects/<code>/drawings/<name>[?private=0]  (the file as the body)
                                               → job: the drawing kept without its
                                               private information, as drawing-N.dxf
                                               (privacy.py); with private=0 kept as sent
    POST /api/projects/<code>/drawings/<name>/plans {units?}  → job: its units and the plans in it
    POST /api/projects/<code>/floors {drawing, units, plans: [...]}  → job: add, align, convert
    POST /api/projects/<code>/convert          → job
    POST /api/projects/<code>/buildings/<id>/placement {lat, lon, bearing, x?, y?}
    POST /api/projects/<code>/export           → job
    GET  /api/projects/<code>/exports/<file>
    GET  /api/projects/<code>/preview.storeypath   the project as it is now, as a
                                               package (not recorded as an export)
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from importlib.metadata import version
from pathlib import Path
from queue import Queue
from urllib.parse import parse_qs, unquote, urlparse

from shapely.geometry import shape
from shapely.ops import unary_union

from .assets import asset_dir
from .cad import UNIT_NAMES, UNIT_WORDS, DrawingError, header_units, meters_per_unit, read_drawing
from .llm import LocalModel, ModelUnavailable, read_titles, worth_reading
from .sheets import read_title
from .profile import AUTO, load_profile
from .levels import DEFAULT_HEIGHT_M, DEFAULT_PARAPET_M, floor_levels, read_level_marks
from .reading import NOT_A_ROOM, read_units
from .symbols import SymbolSpotter
from .vision import VisionModel
from .review import CONTENT_TYPES, File, NotFound, Review, floor_print, floor_print_png
from .types import SpaceType
from .workspace import Placement, SourceDrawing, Workspace

MAX_UPLOAD = 512 * 1024 * 1024
DRAWING_TYPES = (".dwg", ".dxf")
INCOMING = ".incoming-"  # a drawing as sent, until its private copy is made
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
        self._lock = threading.RLock()
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

    def status(self) -> dict:
        from shutil import which

        from .cad import odafc

        return {
            "version": version("storeypath"),
            "model": self.model.name if self.model.available() else None,
            "model_ready": self.model.ready,
            "symbols": self.symbols.name if self.symbols.available() else None,
            "vision": self.vision.name if self.vision.available() else None,
            "dwg": bool(which("dwg2dxf")) or odafc.is_installed(),
            "data": str(self.data),
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
        tree = []
        for loc in ws.locations:
            buildings = []
            for b in loc.buildings:
                b_id = f"{ws.id}-{loc.code}-{b.code}"
                outline = [shape(f.outline) for f in b.floors if f.outline]
                centre = unary_union(outline).centroid if outline else None
                buildings.append({
                    "id": b_id, "code": b.code, "name": b.name,
                    "placement": b.placement.model_dump() if b.placement else None,
                    "centre": [round(centre.x, 2), round(centre.y, 2)] if centre is not None else None,
                    "floors": [{"id": f"{b_id}-{f.code}", "name": f.name, "ordinal": f.ordinal,
                                "drawing": Path(f.source.path).name if f.source else None,
                                "view": f.source.view if f.source else None,
                                "layers": f.layers, "converted": f.converted_at is not None}
                               for f in sorted(b.floors, key=lambda f: f.ordinal)],
                })
            tree.append({"id": f"{ws.id}-{loc.code}", "code": loc.code, "name": loc.name, "buildings": buildings})
        drawings = sorted(p.name for p in (path.parent / "drawings").glob("*")
                          if p.suffix.lower() in DRAWING_TYPES and not p.name.startswith(INCOMING)) \
            if (path.parent / "drawings").is_dir() else []
        exports = sorted((p.name for p in (path.parent / "exports").glob("*.storeypath")), reverse=True) \
            if (path.parent / "exports").is_dir() else []
        return {**info, "locations": tree, "drawings": drawings, "exports": exports,
                "exported": len(ws.exports)}

    # ---- drawings ---------------------------------------------------------------

    def upload(self, code: str, name: str, body: bytes, private: bool = True) -> dict | Job:
        """A drawing added to the project. Kept private (the default), a job takes out
        what names the people and the project — title blocks, names, contacts, hidden
        file data (privacy.py) — and only that copy is kept, as drawing-N.dxf: the
        file as sent, and its name, are not. Otherwise it is kept as sent."""
        name = Path(name).name
        suffix = Path(name).suffix.lower()
        if suffix not in DRAWING_TYPES:
            raise ValueError("drawings are .dwg or .dxf files")
        folder = self.path(code).parent / "drawings"
        folder.mkdir(exist_ok=True)
        if not private:
            (folder / name).write_bytes(body)
            return {"drawing": name, "bytes": len(body)}
        incoming = folder / f"{INCOMING}{uuid.uuid4().hex[:12]}{suffix}"
        incoming.write_bytes(body)

        def run(job: Job):
            from .cad import read_drawing_to_change
            from .privacy import make_private

            try:
                job.say("reading the drawing")
                doc = read_drawing_to_change(incoming)
                job.say("taking out the title blocks, names, contacts and hidden file data")
                report = make_private(doc)
                job.say(report.summary())
                taken = [int(m.group(1)) for p in folder.glob("drawing-*.dxf") if (m := DRAWING_NAME.fullmatch(p.name))]
                out = folder / f"drawing-{max(taken, default=0) + 1}.dxf"
                doc.saveas(out)
                job.say(f"kept as {out.name}")
            finally:
                incoming.unlink(missing_ok=True)
            return {"drawing": out.name, "privacy": report.view()}

        return self.jobs.submit("Adding a drawing without its private information", run)

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
            # Floor heights and the roof's parapet, from the levels on its sections.
            found = floor_levels(read_level_marks(doc, self.model if self.model.available() else None))
            job.say(f"levels: {found.summary()}" if found.levels else "no floor levels on its sections")
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
            from .convert import convert_floor
            from .sheets import align, floor_walls
            from .analyse import analyse

            with self._lock:
                ws = Workspace.load(ws_path)
                loc = ws.locations[0] if ws.locations else None
                places = _floors_to_add(ws, plans)
                loc_id = f"{ws.id}-{loc.code}" if loc else ws.add_location("SITE", ws.project.name)
                added: dict[str, list[str]] = {}
                for b_code, b_name, ordinal, p in places:
                    b_id = f"{loc_id}-{b_code}"
                    try:
                        ws.building(b_id)
                    except KeyError:
                        ws.add_building(loc_id, b_code, b_name)
                        job.say(f"building {b_name} ({b_code})")
                    source = SourceDrawing(path=str(drawing.relative_to(ws_path.parent)), profile=AUTO,
                                           units=units, region=tuple(p["region"]), view=p.get("title"))
                    f_id = ws.add_floor(b_id, ordinal, name=p.get("name") or None, source=source,
                                        height=float(p.get("height") or DEFAULT_HEIGHT_M),
                                        parapet_height=float(p["parapet"]) if p.get("parapet") else None)
                    added.setdefault(b_id, []).append(f_id)
                    job.say(f"floor {f_id}: {p.get('title') or 'plan ' + str(p.get('index'))}")
                for b_id in added:
                    ws.restack(b_id)  # elevations from the heights
                ws.save(ws_path)

            doc = read_drawing(drawing)
            for b_id in added:
                floors = sorted(ws.building(b_id).floors, key=lambda f: f.ordinal)
                if len(floors) < 2:
                    continue
                job.say(f"lining up the floors of {b_id}")
                ref = floors[0]
                scale = meters_per_unit(doc, ref.source.units)

                def walls(f):
                    prof = analyse(doc, scale, f.source.region, None, load_profile(AUTO)).profile
                    return floor_walls(doc, prof, scale, f.source.region)

                ref_walls = walls(ref)
                for f in floors[1:]:
                    if f.source is None or Path(f.source.path).name != drawing.name:
                        continue
                    (tx, ty), overlap = align(ref_walls, walls(f))
                    off = ref.source.offset or (0.0, 0.0)
                    f.source.offset = (off[0] + tx / scale, off[1] + ty / scale)
                    job.say(f"  {f.name}: moved {tx:.2f}, {ty:.2f} m; {overlap:.0%} of walls line up")
            with self._lock:
                ws.save(ws_path)
            self._convert(ws_path, [f for fs in added.values() for f in fs], job)
            return {"floors": [f for fs in added.values() for f in fs]}

        return self.jobs.submit(f"Adding floors from {drawing.name}", run)

    def convert(self, code: str, floor: str | None = None) -> Job:
        ws_path = self.path(code)

        def run(job: Job):
            ws = Workspace.load(ws_path)
            floors = [fid for *_, fid in ws.iter_floors() if floor in (None, fid)]
            return self._convert(ws_path, floors, job)

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
        with self._lock:
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

    def place(self, code: str, building_id: str, body: dict) -> dict:
        ws_path = self.path(code)
        with self._lock:
            ws = Workspace.load(ws_path)
            b = ws.building(building_id)
            lat, lon = float(body["lat"]), float(body["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("latitude is -90…90 and longitude -180…180")
            b.placement = Placement(lat=lat, lon=lon, x=float(body.get("x", 0)), y=float(body.get("y", 0)),
                                    bearing=float(body.get("bearing", 0)))
            ws.save(ws_path)
        return {"placement": b.placement.model_dump()}

    def export(self, code: str) -> Job:
        ws_path = self.path(code)

        def run(job: Job):
            from .export import export_package
            from .validate import validate_package

            with self._lock:
                ws = Workspace.load(ws_path)
                folder = ws_path.parent / "exports"
                folder.mkdir(exist_ok=True)
                seq = len(ws.exports) + 1
                out = folder / f"{ws.id}-{seq:03d}.storeypath"  # by code, as the folder
                job.say(f"writing {out.name}")
                manifest = export_package(ws, out)
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

    def preview(self, code: str) -> bytes:
        """The project as a package, as it is now, for the 3D view: built in memory
        and not entered as an export."""
        from .export import export_package

        with self._lock:
            ws = Workspace.load(self.path(code))
        if not any(f.converted_at for _, _, f, _ in ws.iter_floors()):
            raise NotFound("nothing converted yet: add floors first")
        buf = io.BytesIO()
        export_package(ws, buf, record=False)
        return buf.getvalue()

    def export_file(self, code: str, name: str) -> Path:
        path = self.path(code).parent / "exports" / Path(name).name
        if not path.is_file():
            raise NotFound(f"no export {name}")
        return path


def _floors_to_add(ws: Workspace, plans: list[dict]) -> list[tuple[str, str, int, dict]]:
    """Where each chosen plan goes: (building code, building name, floor, plan). A
    building is known by its name; a new one gets a code from its name that no other
    building has. Fails, before anything changes, when two plans would be the same
    floor of a building or the building already has that floor."""
    loc = ws.locations[0] if ws.locations else None
    existing = {b.name.strip().lower(): b for b in loc.buildings} if loc else {}
    codes = {key: b.code for key, b in existing.items()}
    taken = set(codes.values())
    chosen: dict[tuple[str, int], str] = {}
    out = []
    for p in plans:
        name = (p.get("building") or "Main building").strip()
        key = name.lower()
        if key not in codes:
            code = base = _code(p.get("building_code") or name)
            n = 2
            while code in taken:  # "Main building" and "Main kitchen" are two buildings
                code, n = f"{base[:14]}{n}", n + 1
            codes[key] = code
            taken.add(code)
        ordinal = int(p["ordinal"])
        title = p.get("title") or f"plan {p.get('index')}"
        if (key, ordinal) in chosen:
            raise ValueError(f"{chosen[key, ordinal]} and {title} are both floor {ordinal} of {name}: give one "
                             "of them another floor or building, or leave it out")
        has = next((f for f in existing[key].floors if f.ordinal == ordinal), None) if key in existing else None
        if has:
            raise ValueError(f"{name} already has floor {ordinal} ({has.name}): give {title} another floor "
                             "or building")
        chosen[key, ordinal] = title
        out.append((codes[key], name, ordinal, p))
    return out


def _code(text: str) -> str:
    """A building code from its name: its first word ("Main building" → MAIN)."""
    words = re.findall(r"[A-Z0-9]+", text.upper())
    return words[0][:8] if words else "B1"


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
            except (DrawingError, ValueError, KeyError, ModelUnavailable) as e:
                return self._json(400, {"error": str(e).strip("'\"")})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
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
            case "PUT", ["projects", code, "drawings", name]:
                return studio.upload(code, name, body, private=query.get("private", ["1"])[0] != "0")
            case "POST", ["projects", code, "drawings", name, "plans"]:
                return studio.plans(code, name, body.get("units") or None)
            case "POST", ["projects", code, "floors"]:
                return studio.add_floors(code, body)
            case "POST", ["projects", code, "convert"]:
                return studio.convert(code, body.get("floor"))
            case "POST", ["projects", code, "buildings", b_id, "placement"]:
                return studio.place(code, b_id, body)
            case "POST", ["projects", code, "export"]:
                return studio.export(code)
            case "GET", ["projects", code, "exports", name]:
                return studio.export_file(code, name)
            case "GET", ["projects", code, "preview.storeypath"]:
                return studio.preview(code)
            # the review editor
            case "GET", ["projects", code, "floors", floor_id]:
                return studio.review(code).floor(floor_id)
            case "GET", ["projects", code, "floors", floor_id, "drawing"]:
                return studio.review(code).drawing(floor_id)
            case "GET", ["projects", code, "floors", floor_id, "print"]:
                return floor_print(studio.review(code), floor_id)
            case "GET", ["projects", code, "floors", floor_id, "print.png"]:
                return floor_print_png(studio.review(code), floor_id)
            case "POST", ["projects", code, "floors", floor_id, "convert"]:
                return studio.convert(code, floor_id)
            case "POST", ["projects", code, "objects", object_id]:
                return studio.review(code).correct(object_id, body)
            case "GET", ["projects", code, "review"]:
                return studio.review(code).project()
        raise NotFound("not found")

    return ThreadingHTTPServer((host, port), Handler)
