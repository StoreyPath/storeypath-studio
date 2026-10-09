"""Symbols drawn in a plan (doors, toilets, stoves, stairs…) spotted by a model
trained on floor plans, SymPoint-V2, to type the rooms that carry no name.

SymPoint-V2 is not part of StoreyPath and is not installed with it: its repository
states no licence, so it is used for research only, fetched by docker/fetch-symbols.sh
into a folder of its own (``$STOREYPATH_SYMBOLS``, /opt/storeypath/symbols in the
image). Without that folder, or without PyTorch, nothing here runs and plans are read
as before.

It runs in a process of its own (worker.py), so Studio never loads PyTorch itself.
What it finds is kept on the floor, and used again when the drawing has not changed.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

import ezdxf.path
from ezdxf.document import Drawing
from shapely.geometry import Point, box

from ..extract import ExtractedSpace, _walk, modelspace_entities
from ..types import SpaceType

SYMBOLS_DIR = "/opt/storeypath/symbols"
MODEL = "SymPoint-V2"
COMMANDS = ["Line", "Arc", "circle", "ellipse"]  # the model's primitive kinds, in its order
DEFAULT_LINEWEIGHT = 25  # 0.25 mm, for lines that set none
TIMEOUT_S = 600
MAX_PRIMITIVES = 60_000  # beyond, it runs out of time and memory on a CPU (a large floor: ~480,000)
MIN_SCORE = 0.8  # symbols found less surely than this are not used
STAIR_MIN_LINES = 6  # a flight of stairs is drawn as many treads
FILLS_ROOM = 0.25  # stairs, lifts and escalators cover at least this share of their room

# Rooms by the fixtures in them, first match wins. Each entry: the room type, the
# symbols of which one must be there, and symbols of which one must also be there.
ROOM_FIXTURES: list[tuple[SpaceType, set[str], set[str]]] = [
    (SpaceType.BATHROOM, {"toilet", "squat toilet", "urinal"}, {"bath", "bath tub"}),
    (SpaceType.BATHROOM, {"bath", "bath tub"}, set()),
    (SpaceType.RESTROOM, {"toilet", "squat toilet", "urinal"}, set()),
    (SpaceType.KITCHEN, {"gas stove", "refrigerator"}, set()),
    (SpaceType.LAUNDRY, {"washing machine"}, set()),
    (SpaceType.BEDROOM, {"bed"}, set()),
    (SpaceType.LIVING_ROOM, {"sofa", "TV cabinet"}, set()),
]
FILLING: dict[str, SpaceType] = {"stairs": SpaceType.STAIRS, "elevator": SpaceType.ELEVATOR,
                                 "escalator": SpaceType.ESCALATOR}


@dataclass
class Symbol:
    label: str  # one of the model's classes: "single door", "toilet", "stairs"…
    score: float
    point: tuple[float, float]  # its middle, local meters
    box: tuple[float, float, float, float]  # x0, y0, x1, y1, local meters
    lines: int  # how many straight lines it is drawn with


@dataclass
class Primitive:
    command: int  # index into COMMANDS
    points: list[tuple[float, float]]  # 4 along it, drawing units
    length: float
    layer: str
    weight: float


class SymbolSpotter:
    """SymPoint-V2 in ``folder``: its code in SymPointV2/, its weights in weights/."""

    def __init__(self, folder: str | Path | None = None, python: str | None = None):
        self.folder = Path(folder or os.environ.get("STOREYPATH_SYMBOLS") or SYMBOLS_DIR)
        self.python = python or sys.executable
        self.failed: str | None = None

    @property
    def name(self) -> str:
        return MODEL

    def missing(self) -> str | None:
        """Why it cannot run here, or None."""
        if not (self.folder / "SymPointV2" / "svgnet" / "model" / "svgnet.py").is_file():
            return f"{MODEL} is not installed in {self.folder}"
        if not (self.folder / "weights" / "best.pth").is_file():
            return f"{MODEL}'s weights are not in {self.folder / 'weights'}"
        if self.python == sys.executable and not _has_torch():
            return "PyTorch is not installed"
        return None

    def available(self) -> bool:
        return self.missing() is None

    def find(self, doc: Drawing, region=None, offset=None, scale: float = 1.0,
             skip_layer: Callable[[str], bool] | None = None) -> list[Symbol]:
        """The symbols in one floor's plan, in local meters."""
        self.failed = None
        prims = primitives(doc, region, skip_layer)
        if not prims:
            return []
        if len(prims) > MAX_PRIMITIVES:
            self.failed = f"the plan is too large for {MODEL}: {len(prims):,} lines, where it reads up to {MAX_PRIMITIVES:,}"
            return []
        x0, y0, x1, y1 = region or _extent(prims)
        layers = {name: i + 1 for i, name in enumerate(sorted({p.layer for p in prims}))}
        data = {  # as in the model's training data: origin at the top left, y down
            "commands": [p.command for p in prims],
            "args": [[v for x, y in p.points for v in (x - x0, y1 - y)] for p in prims],
            "lengths": [p.length for p in prims], "widths": [p.weight for p in prims],
            "layers": [layers[p.layer] for p in prims], "width": x1 - x0, "height": y1 - y0,
        }
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [_package_root(), os.environ.get("PYTHONPATH")]))}
        try:
            done = subprocess.run([self.python, "-m", "storeypath.symbols.worker", str(self.folder)],
                                  input=json.dumps(data), capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.failed = f"{MODEL} did not run: {e}"
            return []
        if done.returncode != 0:
            self.failed = f"{MODEL} failed: {(done.stderr.strip().splitlines() or ['no message'])[-1]}"
            return []
        found = json.loads(done.stdout)
        dx, dy = offset or (0.0, 0.0)
        symbols = []
        for s in found["symbols"]:
            members = [prims[i] for i in s["members"]]
            if not members or s["label"] in ("bg", "wall", "curtain wall"):
                continue
            xs = [(x - dx) * scale for p in members for x, _ in p.points]
            ys = [(y - dy) * scale for p in members for _, y in p.points]
            symbols.append(Symbol(
                label=s["label"], score=s["score"],
                point=(round(sum(xs) / len(xs), 3), round(sum(ys) / len(ys), 3)),
                box=(round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)),
                lines=sum(p.command == 0 for p in members),
            ))
        return symbols


@cache
def _has_torch() -> bool:
    return importlib.util.find_spec("torch") is not None


def _package_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def _extent(prims: list[Primitive]) -> tuple[float, float, float, float]:
    xs = [x for p in prims for x, _ in p.points]
    ys = [y for p in prims for _, y in p.points]
    return min(xs), min(ys), max(xs), max(ys)


def _weight(e, layer: str, doc: Drawing) -> float:
    lw = e.dxf.get("lineweight", -1)
    if lw < 0 and layer in doc.layers:
        lw = doc.layers.get(layer).dxf.get("lineweight", -3)
    return float(lw) if lw > 0 else DEFAULT_LINEWEIGHT


def _along(a, b) -> list[tuple[float, float]]:
    return [(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f) for f in (0, 1 / 3, 2 / 3, 1)]


def _shapes(e):
    """(command, 4 points, length) for one entity: lines and arcs by their ends and
    thirds, circles and ellipses by their quarters, polylines and hatch edges as lines."""
    kind = e.dxftype()
    if kind == "LINE":
        a, b = (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)
        yield 0, _along(a, b), math.dist(a, b)
    elif kind == "ARC":  # by vertices(), in world coordinates: arcs in mirrored blocks are not
        a0, a1 = e.dxf.start_angle, e.dxf.end_angle
        sweep = (a1 - a0) % 360 or 360
        pts = [(v.x, v.y) for v in e.vertices([a0 + sweep * f for f in (0, 1 / 3, 2 / 3, 1)])]
        yield 1, pts, e.dxf.radius * math.radians(sweep)
    elif kind == "CIRCLE":
        yield 2, [(v.x, v.y) for v in e.vertices([0, 90, 180, 270])], 2 * math.pi * e.dxf.radius
    elif kind == "ELLIPSE":
        tool = e.construction_tool()
        yield 3, [(v.x, v.y) for v in tool.vertices([0, math.pi / 2, math.pi, 3 * math.pi / 2])], tool.major_axis.magnitude * 4
    elif kind in ("LWPOLYLINE", "POLYLINE"):
        for part in e.virtual_entities():
            yield from _shapes(part)
    elif kind == "HATCH":
        for path in ezdxf.path.from_hatch(e):
            pts = [(v.x, v.y) for v in path.flattening(1.0)]
            for a, b in zip(pts, pts[1:]):
                yield 0, _along(a, b), math.dist(a, b)


def primitives(doc: Drawing, region=None, skip_layer: Callable[[str], bool] | None = None) -> list[Primitive]:
    """The plan's lines, arcs and circles as the model reads them, in drawing units.
    Texts, dimensions and layers ``skip_layer`` names (grids, room tags) are left out."""
    out = []
    for e, layer in _walk(modelspace_entities(doc, region)):
        if skip_layer is not None and skip_layer(layer):
            continue
        try:
            shapes = list(_shapes(e))
        except Exception:  # broken or unsupported geometry
            continue
        weight = _weight(e, layer, doc)
        out.extend(Primitive(c, pts, length, layer, weight) for c, pts, length in shapes
                   if length > 0 and (region is None or _overlaps(pts, region)))
    return out


def _overlaps(points, region) -> bool:
    """Whether a primitive reaches into the plan: a block placed in it may hold
    lines far outside."""
    x0, y0, x1, y1 = region
    xs, ys = [x for x, _ in points], [y for _, y in points]
    return max(xs) >= x0 and min(xs) <= x1 and max(ys) >= y0 and min(ys) <= y1


def type_rooms(spaces: list[ExtractedSpace], symbols: list[Symbol]) -> None:
    """Rooms with no type, typed by the symbols drawn in them: stairs that fill a
    room make it a stair room, a toilet and a bath a bathroom… Each is flagged for a
    person to check, since a model found the symbols."""
    sure = [s for s in symbols if s.score >= MIN_SCORE]
    for space in spaces:
        if space.type != SpaceType.UNSPECIFIED:
            continue
        inside = [s for s in sure if space.polygon.contains(Point(s.point))]
        found = _room_type(space, inside)
        if found is not None:
            kind, why = found
            space.type, space.type_source = kind, f"symbols:{'+'.join(sorted(why))}"
            space.issues.append(f"typed {kind.value.replace('_', ' ')} from the symbols drawn in it "
                                f"({', '.join(sorted(why))}); check it")


def _room_type(space: ExtractedSpace, inside: list[Symbol]) -> tuple[SpaceType, set[str]] | None:
    area = space.polygon.area
    for s in inside:
        kind = FILLING.get(s.label)
        if kind is None or (s.label == "stairs" and s.lines < STAIR_MIN_LINES):
            continue
        if box(*s.box).intersection(space.polygon).area >= FILLS_ROOM * area:
            return kind, {s.label}
    labels = {s.label for s in inside}
    for kind, one_of, also in ROOM_FIXTURES:
        if labels & one_of and (not also or labels & also):
            return kind, (labels & one_of) | (labels & also)
    return None


def to_records(symbols: list[Symbol]) -> list[dict]:
    return [asdict(s) for s in symbols]


def from_records(records: list[dict]) -> list[Symbol]:
    return [Symbol(r["label"], r["score"], tuple(r["point"]), tuple(r["box"]), r["lines"]) for r in records]
