"""Reading DXF and DWG drawings.

DXF is read directly with ezdxf. DWG is converted to DXF first by an external
program the user installs (not bundled, to keep this project permissively
licensed): LibreDWG's ``dwg2dxf`` or the ODA File Converter.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import ezdxf
from ezdxf import units
from ezdxf.addons import odafc
from ezdxf.document import Drawing

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


def _parse(path: Path) -> Drawing:
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        try:
            return ezdxf.readfile(path)
        except (OSError, ezdxf.DXFStructureError) as e:
            raise DrawingError(f"cannot read {path.name}: {e}") from e
    if suffix == ".dwg":
        return _read_dwg(path)
    raise DrawingError(f"unsupported file type {suffix!r}: expected .dwg or .dxf")


def _read_dwg(path: Path) -> Drawing:
    if shutil.which("dwg2dxf"):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / (path.stem + ".dxf")
            result = subprocess.run(
                ["dwg2dxf", "-y", "-o", str(out), str(path)], capture_output=True, text=True
            )
            if not out.exists():
                raise DrawingError(f"dwg2dxf failed on {path.name}: {result.stderr.strip()}")
            return ezdxf.readfile(out)
    if odafc.is_installed():
        return odafc.readfile(str(path))
    raise DrawingError(
        "reading DWG needs a converter: install LibreDWG (provides dwg2dxf) or the "
        "ODA File Converter, or save the drawing as DXF"
    )


def meters_per_unit(doc: Drawing, override: str | None = None) -> float:
    """Scale from drawing units to meters: ``override``, else what the drawing's doors
    show (see infer_units), else its $INSUNITS setting."""
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


DOOR_LEAF_M = 0.85  # a typical door leaf, the radius of its swing


def infer_units(doc: Drawing, sample: int = 4000) -> str | None:
    """The drawing's real units, read from its doors: the most common radius of a
    quarter-turn arc is a door leaf, about 0.85 m. Drawings often carry a wrong
    unit setting; the doors do not lie. None when there are too few doors."""
    radii = []
    for e in doc.modelspace().query("ARC INSERT"):
        if len(radii) >= sample:
            break
        arcs = [e] if e.dxftype() == "ARC" else _block_arcs(e)
        for a in arcs:
            sweep = (a.dxf.end_angle - a.dxf.start_angle) % 360
            if 80 <= sweep <= 100:
                radii.append(a.dxf.radius * (_insert_scale(e) if e.dxftype() == "INSERT" else 1.0))
    if len(radii) < 3:
        return None
    votes = {}
    for name, code in UNIT_NAMES.items():
        per_m = 1 / units.conversion_factor(code, units.InsertUnits.Meters)
        votes[name] = sum(1 for r in radii if 0.55 <= r / per_m <= 1.3)
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    (best, n), (_, runner_up) = ranked[0], ranked[1]
    # Units are orders of magnitude apart: a clear winner is enough.
    return best if n >= 3 and n >= 1.5 * runner_up else None


def _block_arcs(insert):
    block = insert.doc.blocks.get(insert.dxf.name) if insert.doc else None
    return [a for a in block if a.dxftype() == "ARC"] if block is not None else []


def _insert_scale(insert) -> float:
    return abs(insert.dxf.get("xscale", 1.0))


def header_units(doc: Drawing) -> str | None:
    """The unit the drawing says it uses, when it says one we know."""
    code = doc.header.get("$INSUNITS", 0)
    return next((name for name, c in UNIT_NAMES.items() if c == code), None)
