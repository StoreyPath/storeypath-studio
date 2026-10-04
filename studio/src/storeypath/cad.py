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


def read_drawing(path: str | Path) -> Drawing:
    path = Path(path)
    if not path.exists():
        raise DrawingError(f"drawing not found: {path}")
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
    """Scale from drawing units to meters, from ``override`` or the drawing's $INSUNITS."""
    if override:
        if override not in UNIT_NAMES:
            raise DrawingError(f"unknown units {override!r}: use one of {', '.join(UNIT_NAMES)}")
        code = UNIT_NAMES[override]
    else:
        code = doc.header.get("$INSUNITS", 0)
        if code == 0:  # unitless: fall back to the measurement system
            code = units.InsertUnits.Millimeters if doc.header.get("$MEASUREMENT", 1) else units.InsertUnits.Inches
    return units.conversion_factor(code, units.InsertUnits.Meters)
