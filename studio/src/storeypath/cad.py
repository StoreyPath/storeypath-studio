"""Reading DXF and DWG drawings.

DXF is read directly with ezdxf. DWG is converted to DXF first by an external
program the user installs (not bundled, to keep this project permissively
licensed): LibreDWG's ``dwg2dxf`` or the ODA File Converter.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import units
from ezdxf.addons import odafc
from ezdxf.document import Drawing
from shapely import STRtree
from shapely.geometry import Point

UNIT_NAMES = {
    "mm": units.InsertUnits.Millimeters,
    "cm": units.InsertUnits.Centimeters,
    "m": units.InsertUnits.Meters,
    "in": units.InsertUnits.Inches,
    "ft": units.InsertUnits.Feet,
}


class DrawingError(Exception):
    pass


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_DRAWINGS: OrderedDict[tuple, Drawing] = OrderedDict()
_DRAWINGS_LOCK = threading.Lock()
KEEP_DRAWINGS = 2  # parsed drawings kept in memory (a large sheet set is a few hundred MB)


def _tolerate_broken_text_columns() -> None:
    """Real drawings (and DWG converted by LibreDWG) carry multi-column text boxes
    whose column data is broken: a column type no program writes, or columns linked
    to a sheet that is gone. ezdxf then refuses the whole drawing. A floor plan does
    not need text columns: such a box is read as plain text, as AutoCAD shows it."""
    import ezdxf.document
    import ezdxf.entities.mtext as mtext

    load_columns = mtext.load_columns_from_embedded_object

    def tolerant_columns(dxf, embedded_obj):
        try:
            return load_columns(dxf, embedded_obj)
        except (ValueError, KeyError, IndexError):
            return None

    def tolerant_post_init(self):
        for cmd in self._post_init_commands:
            try:
                cmd()
            except (KeyError, ValueError, AttributeError):  # a column linked to a missing sheet
                pass
        del self._post_init_commands

    mtext.load_columns_from_embedded_object = tolerant_columns
    ezdxf.document.Drawing._execute_post_init_commands = tolerant_post_init


_tolerate_broken_text_columns()


def read_drawing(path: str | Path) -> Drawing:
    """A drawing, parsed. The last few are kept in memory: converting a sheet set
    reads the same large file for every floor and step, and parsing it (after
    converting a DWG) takes seconds. The file changing on disk is noticed.
    Callers must not modify the drawing they get."""
    path = Path(path)
    if not path.exists():
        raise DrawingError(f"drawing not found: {path}")
    st = path.stat()
    key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    with _DRAWINGS_LOCK:
        if key in _DRAWINGS:
            _DRAWINGS.move_to_end(key)
            return _DRAWINGS[key]
    doc = _parse(path)
    with _DRAWINGS_LOCK:
        _DRAWINGS[key] = doc
        while len(_DRAWINGS) > KEEP_DRAWINGS:
            _DRAWINGS.popitem(last=False)
    return doc


def read_drawing_to_change(path: str | Path) -> Drawing:
    """A drawing parsed afresh and not kept: for a caller that changes it (privacy.py)."""
    path = Path(path)
    if not path.exists():
        raise DrawingError(f"drawing not found: {path}")
    return _parse(path)


def _parse(path: Path) -> Drawing:
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        return _audited(_read_dxf(path, path.name))
    if suffix == ".dwg":
        return _audited(_read_dwg(path))
    raise DrawingError(f"unsupported file type {suffix!r}: expected .dwg or .dxf")


def _read_dxf(path: Path, name: str) -> Drawing:
    """A DXF file; a damaged one read as far as ezdxf can recover it (said in the
    drawing's notes), DrawingError when nothing can be read from it."""
    try:
        return ezdxf.readfile(path)
    except OSError as e:
        raise DrawingError(f"cannot read {name}: {e}") from e
    except Exception as e:  # damaged: a bad value, a section cut short…
        first = e
    try:
        from ezdxf import recover

        doc, _ = recover.readfile(path)
    except Exception as e:
        raise DrawingError(f"cannot read {name}: {first}") from e
    if not len(doc.modelspace()):
        raise DrawingError(f"cannot read {name}: {first}")
    _note(doc, f"{name} is damaged ({first}): it was read as far as it could be; check its floors in review")
    return doc


def _note(doc: Drawing, note: str) -> None:
    doc.__dict__.setdefault("_storeypath_notes", []).append(note)


def _audited(doc: Drawing) -> Drawing:
    """The drawing as ezdxf's auditor leaves it: what it finds broken is fixed or
    taken out (an insert of a block the drawing does not define, a spline with too
    few points, a hatch with a broken boundary), as CAD programs do on opening, so
    one broken entity does not stop the reading of a floor. What was taken out is
    said in the drawing's notes (``read_notes``)."""
    try:
        auditor = doc.audit()
    except Exception:  # the auditor itself fails on it: read as it is
        return doc
    removed = [f.message for f in auditor.fixes if f.message.startswith(("Deleted", "Removed"))]
    if removed:
        more = f" (and {len(removed) - 1} more)" if len(removed) > 1 else ""
        _note(doc, f"{len(removed)} broken entities were left out of the drawing as it was read: {removed[0]}{more}")
    return doc


def read_notes(doc: Drawing) -> list[str]:
    """What a person should know of how a drawing was read: a damaged file, broken
    entities left out. For the conversion report."""
    return list(getattr(doc, "_storeypath_notes", []))


DWG_TIMEOUT_S = 600  # a DWG converter still at work after this long is stopped
# What LibreDWG says of a file that is damaged, not merely of objects it does not know
# (it reports errors on many good files' materials and the like).
DWG_DAMAGE = re.compile(r"CRC mismatch|Invalid object type|Invalid class index|Object handle not found|"
                        r"section.*overflow|error parsing", re.IGNORECASE)


def _read_dwg(path: Path) -> Drawing:
    if shutil.which("dwg2dxf"):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / (path.stem + ".dxf")
            try:
                result = subprocess.run(["dwg2dxf", "-y", "-o", str(out), str(path)], capture_output=True,
                                        text=True, errors="replace", timeout=DWG_TIMEOUT_S)
            except subprocess.TimeoutExpired as e:
                raise DrawingError(f"dwg2dxf did not finish converting {path.name} in {DWG_TIMEOUT_S // 60} "
                                   "minutes; save the drawing as DXF instead") from e
            except OSError as e:
                raise DrawingError(f"dwg2dxf could not run: {e}") from e
            said = [ln.strip() for ln in result.stderr.splitlines() if ln.strip()]
            if not out.exists() or out.stat().st_size == 0:
                raise DrawingError(f"dwg2dxf failed on {path.name}: {' '.join(said[-3:])}")
            doc = _read_dxf(out, path.name)
            damage = [ln for ln in said if DWG_DAMAGE.search(ln)]
            if result.returncode != 0 or damage:
                why = (damage or [ln for ln in said if ln.startswith("ERROR")] or said or ["no reason given"])[0]
                _note(doc, f"{path.name} looks damaged: converting it said \"{why}\" (and {len(said) - 1} more); "
                           "parts of it may be missing: check its floors in review, or save it again from the "
                           "CAD program")
            return doc
    if odafc.is_installed():
        try:
            return odafc.readfile(str(path))
        except Exception as e:
            raise DrawingError(f"the ODA File Converter could not read {path.name}: {e}") from e
    raise DrawingError(
        "reading DWG needs a converter: install LibreDWG (provides dwg2dxf) or the "
        "ODA File Converter, or save the drawing as DXF"
    )


def meters_per_unit(doc: Drawing, override: str | None = None) -> float:
    """Scale from drawing units to meters: ``override``, else what is drawn shows
    (see infer_units), else its $INSUNITS setting."""
    return units.conversion_factor(UNIT_NAMES[drawing_units(doc, override)], units.InsertUnits.Meters)


def drawing_units(doc: Drawing, override: str | None = None) -> str:
    """The units a drawing is read in (see meters_per_unit)."""
    if override:
        if override not in UNIT_NAMES:
            raise DrawingError(f"unknown units {override!r}: use one of {', '.join(UNIT_NAMES)}")
        return override
    inferred = getattr(doc, "_storeypath_units", False)
    if inferred is False:
        inferred = infer_units(doc)
        doc._storeypath_units = inferred
    if inferred:
        return inferred
    said = header_units(doc)
    if said:
        return said
    return "mm" if doc.header.get("$MEASUREMENT", 1) else "in"  # unitless: by measurement system


# ---- the units a drawing is really in ----------------------------------------------
#
# A drawing's unit setting is often missing or wrong, so the units are read from what
# is drawn, the way a person checks a plan: in the right units the doors are about a
# door wide, the dimensions are the size of rooms and walls, and the text is a size
# someone can read. Each clue is checked under every unit; the units are orders of
# magnitude apart, so the wrong ones make no sense. A note that states the units (read
# in reading.py) can be added to the clues.

M_PER_UNIT = {name: units.conversion_factor(code, units.InsertUnits.Meters) for name, code in UNIT_NAMES.items()}
DOOR_SWING_M = (0.55, 1.3)  # a door leaf: the radius of its swing
DIMENSION_M = (0.3, 8.0)  # the typical dimension on a plan: rooms, walls, openings
TEXT_HEIGHT_M = (0.05, 1.5)  # the typical text: 2–5 mm on paper, at 1:20 to 1:300
MAX_ARCS = 4000  # door swings are counted until this many arcs have been seen
CLUE_NAMES = {"doors": "the doors", "dimensions": "the dimensions", "text": "the text sizes"}


@dataclass
class UnitClue:
    """One thing in a drawing that shows its units. ``fits`` are the units it makes
    sense in: one is a clear answer, several narrow it down, none says nothing."""

    kind: str  # "note", "doors", "dimensions" or "text"
    fits: list[str]
    detail: str  # what was seen, for people (a note's own words)

    @property
    def name(self) -> str:
        return f'the note "{self.detail}"' if self.kind == "note" else CLUE_NAMES[self.kind]


@dataclass
class UnitsDecision:
    units: str
    sure: bool  # False: the clues disagree, or too little shows the units
    reason: str  # one sentence, for people
    clues: list[UnitClue] = field(default_factory=list)
    guessed: bool = False  # nothing drawn showed the units: the setting or measurement system was used


def infer_units(doc: Drawing) -> str | None:
    """The drawing's real units, read from what is drawn (see decide_units). None
    when nothing drawn shows them."""
    decision = decide_units(doc)
    return None if decision.guessed else decision.units


def decide_units(doc: Drawing, note: UnitClue | None = None) -> UnitsDecision:
    """The units to read a drawing in, and how sure that is. The doors come first,
    then the dimensions, a note stating the units, and the text sizes: a note says
    what the dimensions are labelled in, which is not always what the drawing is
    drawn in (a plan drawn in metres can label its dimensions in millimetres). Sure
    when nothing disagrees and the doors, the dimensions or a note show the units."""
    door, dims, text = _door_clue(doc), _dimension_clue(doc), _text_clue(doc)
    clues = [door, dims] + ([note] if note else []) + [text]
    said = header_units(doc)
    voiced = [c for c in clues if c.fits]
    pick = next((c.fits[0] for c in voiced if len(c.fits) == 1), None)
    if pick is None and voiced:
        common = set.intersection(*(set(c.fits) for c in voiced))
        pick = common.pop() if len(common) == 1 else None
    if pick is None:
        pick = said or ("mm" if doc.header.get("$MEASUREMENT", 1) else "in")
        how = "as the drawing's unit setting says" if said else "by the drawing's measurement system"
        return UnitsDecision(pick, False, f"Not sure of the units: no door swings, dimensions or notes show them. "
                                          f"Read in {UNIT_WORDS[pick]}, {how}.", clues, guessed=True)
    word = UNIT_WORDS[pick]
    agree = [c for c in voiced if pick in c.fits]
    against = [c for c in voiced if pick not in c.fits]
    setting = f" The drawing's own setting says {UNIT_WORDS[said]}." if said and said != pick else ""
    if against:
        others = " or ".join(UNIT_WORDS[u] for u in UNIT_WORDS if any(u in c.fits for c in against))
        return UnitsDecision(pick, False, f"Not sure of the units: {_names(agree)} {_say(agree)} {word}, but "
                                          f"{_names(against)} {_say(against)} {others}. Read in {word}.{setting}", clues)
    if not any(c.kind in ("note", "doors", "dimensions") for c in agree):
        return UnitsDecision(pick, False, f"Not sure of the units: only {_names(agree)} {_say(agree)} {word}. "
                                          f"Read in {word}.{setting}", clues)
    return UnitsDecision(pick, True, f"Read in {word}: {_names(agree)} "
                                     f"{'all ' if len(agree) > 2 else ''}{_say(agree)} so.{setting}", clues)


UNIT_WORDS = {"mm": "millimetres", "cm": "centimetres", "m": "metres", "in": "inches", "ft": "feet"}


def _names(clues: list[UnitClue]) -> str:
    names = [c.name for c in clues]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _say(clues: list[UnitClue]) -> str:
    return "says" if len(clues) == 1 and clues[0].kind == "note" else "say"


def _fits(length: float, band: tuple[float, float]) -> list[str]:
    return [u for u, k in M_PER_UNIT.items() if band[0] <= length * k <= band[1]]


def _door_clue(doc: Drawing) -> UnitClue:
    """Door swings: quarter-turn arcs with a leaf, a straight line from the hinge
    (the arc's centre) about as long as the radius. Fixtures, columns and window bays
    have quarter arcs too (a basin's rounded corners), but no leaf."""
    radii = _through_blocks(doc, _door_radii, MAX_ARCS)
    if len(radii) < 3:
        return UnitClue("doors", [], f"{len(radii)} door swings")
    votes = {u: sum(1 for r in radii if DOOR_SWING_M[0] <= r * k <= DOOR_SWING_M[1]) for u, k in M_PER_UNIT.items()}
    best = max(votes, key=votes.get)
    runner_up = sorted(votes.values())[-2]
    if votes[best] < 3:
        fits = []
    elif votes[best] >= 1.5 * runner_up:
        fits = [best]
    else:
        fits = [u for u, n in votes.items() if 1.5 * n >= votes[best]]
    return UnitClue("doors", fits, f"{len(radii)} door swings, typically {statistics.median(radii):g} across")


MAX_CLUE_DEPTH = 8  # blocks inside blocks followed this deep for the units clues


def _through_blocks(doc: Drawing, measure, most: int) -> list[float]:
    """What ``measure(layout)`` finds in the drawing and in the blocks placed in it,
    as placed (times each insert's scale), blocks in blocks too: a sheet pasted as one
    block, or a bound xref, holds the doors, dimensions and texts that show the units.
    Each block is measured once; one placed inside itself is not followed. At most
    ``most`` values."""
    memo: dict[str, list[float]] = {}

    def of(layout, within: tuple[str, ...]) -> list[float]:
        out = list(measure(layout))[:most]
        if len(within) >= MAX_CLUE_DEPTH:
            return out
        for insert in layout.query("INSERT"):
            if len(out) >= most:
                break
            name = insert.dxf.get("name", "")
            if name in within:
                continue
            if name not in memo:
                block = doc.blocks.get(name)
                memo[name] = of(block, (*within, name)) if block is not None else []
            k = _insert_scale(insert)
            out += [v * k for v in memo[name][: most - len(out)]]
        return out

    return of(doc.modelspace(), ())


def _door_radii(layout) -> list[float]:
    arcs = []
    for a in layout.query("ARC"):
        if len(arcs) >= MAX_ARCS:
            break
        if _quarter(a):
            arcs.append(a)
    if not arcs:
        return []
    leaves = _Leaves(layout)
    return [a.dxf.radius for a in arcs if leaves.hinge_of(a)]


def _quarter(arc) -> bool:
    return 80 <= (arc.dxf.end_angle - arc.dxf.start_angle) % 360 <= 100


class _Leaves:
    """The straight segments of a layout or block, found by their ends."""

    def __init__(self, entities):
        ends, lengths = [], []
        for e in entities:
            for a, b in _segments(e):
                length = math.dist(a, b)
                if length > 0:
                    ends += [Point(a), Point(b)]
                    lengths += [length, length]
        self.lengths = lengths
        self.tree = STRtree(ends) if ends else None

    def hinge_of(self, arc) -> bool:
        """Whether a segment about as long as the radius starts at the arc's centre."""
        c, r = arc.dxf.center, arc.dxf.radius
        if self.tree is None or not all(math.isfinite(v) for v in (c.x, c.y, r)):
            return False
        near = self.tree.query(Point(c.x, c.y), predicate="dwithin", distance=0.1 * r)
        return any(0.85 * r <= self.lengths[i] <= 1.15 * r for i in near)


def _segments(e) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    if e.dxftype() == "LINE":
        return [((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y))]
    if e.dxftype() == "LWPOLYLINE":
        pts = [tuple(p) for p in e.get_points("xy")]
        if e.closed and len(pts) > 2:
            pts.append(pts[0])
        return list(zip(pts, pts[1:]))
    return []


def _dimension_clue(doc: Drawing) -> UnitClue:
    """The typical length the drawing's linear dimensions measure."""
    lengths = _through_blocks(doc, _dimension_lengths, MAX_CLUES)
    if len(lengths) < 5:
        return UnitClue("dimensions", [], f"{len(lengths)} dimensions")
    typical = statistics.median(lengths)
    return UnitClue("dimensions", _fits(typical, DIMENSION_M), f"{len(lengths)} dimensions, typically {typical:g}")


MAX_CLUES = 20000  # dimensions or texts measured for the units, at most


def _dimension_lengths(layout) -> list[float]:
    lengths = []
    for d in layout.query("DIMENSION"):
        if len(lengths) >= MAX_CLUES:
            break
        if d.dimtype not in (0, 1):  # linear and aligned
            continue
        try:
            m = d.get_measurement()
        except (ValueError, TypeError, AttributeError, ZeroDivisionError):
            continue
        if isinstance(m, (int, float)) and m > 0 and math.isfinite(m):
            lengths.append(float(m))
    return lengths


def _text_clue(doc: Drawing) -> UnitClue:
    heights = _through_blocks(doc, _text_heights, MAX_CLUES)
    if len(heights) < 5:
        return UnitClue("text", [], f"{len(heights)} texts")
    typical = statistics.median(heights)
    return UnitClue("text", _fits(typical, TEXT_HEIGHT_M), f"{len(heights)} texts, typically {typical:g} high")


def _text_heights(layout) -> list[float]:
    heights = [t.dxf.height for t in layout.query("TEXT") if t.dxf.height > 0]
    heights += [t.dxf.char_height for t in layout.query("MTEXT") if t.dxf.char_height > 0]
    return [h for h in heights if math.isfinite(h)]


def _insert_scale(insert) -> float:
    return abs(insert.dxf.get("xscale", 1.0))


def header_units(doc: Drawing) -> str | None:
    """The unit the drawing says it uses, when it says one we know."""
    code = doc.header.get("$INSUNITS", 0)
    return next((name for name, c in UNIT_NAMES.items() if c == code), None)
