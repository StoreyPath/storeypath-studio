"""A project as one file to send, and a project from one.

A package (*.storeypath, export.py) is what other systems read. Exported to be
continued in another Studio, it also carries the project in ``studio/``: the
workspace (every correction, edit and ID, its export history) and its
drawings. Other readers ignore that folder.

Opening such a file gives the project back as it was. Opening a package
without it rebuilds the project from what the package holds: the same project
code and IDs, the spaces, zones and openings with their names, numbers, types
and flags, the walls, the placements, the IDs retired, and its export number,
so the next export follows on from it (a system that applied it takes that
one as the next). Its floors have no drawing: they are reviewed, corrected,
walked through and exported; to read one again, its drawing is added to it
(its rooms keep their IDs, lined up on the walls the floor has).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union

from .export import _hash, build_features, export_package
from .ids import format_object_code, parse_id
from .package import FILES, Manifest
from .workspace import (
    Building,
    ExportRecord,
    Floor,
    Location,
    ObjectRecord,
    Override,
    Placement,
    Project,
    Workspace,
)

STUDIO = "studio/"
WORKSPACE_FILE = "studio/project.spproj"
DRAWINGS = "studio/drawings/"
DRAWING_TYPES = {".dxf", ".dwg"}
MAX_FILES = 5000  # entries in a file opened
MAX_BYTES = 4 << 30  # what they hold, uncompressed
LOCAL_DECIMALS = 4  # metres: a tenth of a millimetre


class ProjectExists(Exception):
    """The project of a file opened is here already."""

    def __init__(self, code: str, name: str):
        super().__init__(f"{name} ({code}) is here already")
        self.code, self.name = code, name


# ---- a project to send ---------------------------------------------------------


def export_project(ws_path: Path, out) -> None:
    """The project as a package that carries it: what any system reads, and in
    ``studio/`` the workspace and its drawings (every floor's, wherever it is, and
    the others added to it), the workspace pointing at them there. Not entered as
    an export (it is for people, not systems)."""
    ws_path = Path(ws_path)
    folder = ws_path.parent
    ws = Workspace.load(ws_path)
    shipped = ws.model_copy(deep=True)
    files: dict[str, Path] = {}  # name in drawings/ -> the file

    def ship(path: Path) -> str:
        path = path.resolve()
        for name, p in files.items():
            if p == path:
                return name
        name, n = path.name, 2
        while name in files:
            name, n = f"{path.stem}-{n}{path.suffix}", n + 1
        files[name] = path
        words = path.with_name(path.name + ".words.txt")
        if words.is_file():
            files[name + ".words.txt"] = words
        return name

    for _, _, f, _ in shipped.iter_floors():
        if f.source is None:
            continue
        path = Path(f.source.path) if Path(f.source.path).is_absolute() else folder / f.source.path
        if path.is_file():
            f.source.path = f"drawings/{ship(path)}"
    drawings = folder / "drawings"
    if drawings.is_dir():
        for p in sorted(drawings.iterdir()):
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in DRAWING_TYPES:
                ship(p)
    extra: dict[str, bytes | Path] = {WORKSPACE_FILE: shipped.model_dump_json(indent=1).encode()}
    extra.update({DRAWINGS + name: p for name, p in files.items()})
    export_package(ws, out, record=False, extra=extra)


# ---- a project from a file -----------------------------------------------------


def open_file(data: Path, source: Path, *, replace: bool = False) -> dict:
    """A project from a package file, put in ``data/<code>/``: as it was, when the
    file carries it, else rebuilt from the package. Raises ProjectExists when the
    project is here and ``replace`` is not set (then it is put in its place)."""
    from .validate import validate_package

    with zipfile.ZipFile(source) as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_BYTES:
            raise ValueError("the file holds too much to be a StoreyPath package")
        names = {i.filename for i in infos}
        if WORKSPACE_FILE in names:
            ws = Workspace.model_validate_json(z.read(WORKSPACE_FILE))
            how = "project"
        else:
            errors = validate_package(source)
            if errors:
                raise ValueError("not a package that can be opened: " + "; ".join(errors[:3]))
            ws = workspace_from_package(z, source.name)
            how = "package"
        code = ws.project.code
        if not re.fullmatch(r"[A-Z0-9]{4,16}", code):
            raise ValueError(f"not a project code: {code!r}")
        folder = data / code
        if folder.exists():
            if not replace:
                existing = next(folder.glob("*.spproj"), None)
                name = ws.project.name
                if existing is not None:
                    try:
                        name = json.loads(existing.read_text(encoding="utf-8"))["project"]["name"]
                    except (OSError, ValueError, KeyError):
                        pass
                raise ProjectExists(code, name)
            _remove(folder, data)
        (folder / "drawings").mkdir(parents=True)
        drawings = 0
        for info in infos:
            if not info.filename.startswith(DRAWINGS) or info.is_dir():
                continue
            name = PurePosixPath(info.filename).name  # a name only: nothing outside the project
            if not name or name.startswith(".") or name != info.filename[len(DRAWINGS):]:
                continue
            (folder / "drawings" / name).write_bytes(z.read(info))
            drawings += 1
    ws.save(folder / f"{code}.spproj")
    floors = sum(1 for _ in ws.iter_floors())
    return {"code": code, "name": ws.project.name, "how": how, "floors": floors, "drawings": drawings}


def _remove(folder: Path, data: Path) -> None:
    import shutil

    folder = folder.resolve()
    if folder.parent != data.resolve():
        raise ValueError("not a project folder")
    shutil.rmtree(folder)


def workspace_from_package(z: zipfile.ZipFile, file_name: str) -> Workspace:
    """A workspace rebuilt from a package: its project, locations, buildings,
    floors and objects in local metres, with the IDs it gave and those it
    retired, and its export as the last one."""
    manifest = Manifest.model_validate_json(z.read("manifest.json"))
    files = {**FILES, **manifest.files}
    features = {role: json.loads(z.read(files[role]))["features"] for role in
                ("location", "buildings", "floors", "spaces", "zones", "openings")}
    changes = json.loads(z.read(files["changes"]))
    ws = Workspace(project=Project(code=manifest.project.id, name=manifest.project.name))

    to_local = {b_id: _Local(p) for b_id, p in manifest.placements.items()}
    locations: dict[str, Location] = {}
    for f in features["location"]:
        p = f["properties"]
        locations[f["id"]] = Location(code=p["code"], name=p["name"], address=p.get("address"))
        ws.locations.append(locations[f["id"]])
    buildings: dict[str, Building] = {}
    for f in features["buildings"]:
        p = f["properties"]
        pl = manifest.placements.get(f["id"])
        placement = Placement(lon=pl.lon, lat=pl.lat, x=pl.x, y=pl.y, bearing=pl.bearing) if pl and pl.placed else None
        buildings[f["id"]] = Building(code=p["code"], name=p["name"], placement=placement)
        locations[p["location_id"]].buildings.append(buildings[f["id"]])
    floor_building: dict[str, str] = {}
    exported_at = manifest.export.exported_at
    for f in features["floors"]:
        p = f["properties"]
        b_id = p["building_id"]
        local = to_local[b_id]
        walls = [local.geometry(g) for g in (p.get("walls"), p.get("parapets")) if g]
        buildings[b_id].floors.append(Floor(
            code=p["code"], name=p["name"], ordinal=p["ordinal"], elevation=p["elevation"], height=p["height"],
            parapet_height=p.get("parapet_height_m") if p.get("parapets") else None,
            outline=mapping(local.geometry(f["geometry"])) if f.get("geometry") else None,
            walls=mapping(unary_union(walls)) if walls else None, wall_thickness=p.get("wall_thickness_m"),
            converted_at=exported_at, method="package",
            warnings=[f"rebuilt from {file_name}: add this floor's drawing to read it again"]))
        floor_building[f["id"]] = b_id

    for kind, role in (("space", "spaces"), ("zone", "zones"), ("opening", "openings")):
        for f in features[role]:
            p = f["properties"]
            local = to_local[floor_building[p["floor_id"]]]
            r = ObjectRecord(id=f["id"], kind=kind, type=p["type"], type_source="package",
                             geometry=mapping(local.geometry(f["geometry"])), created_at=exported_at)
            if kind != "opening":
                r.name, r.number, r.label = p.get("name"), p.get("number"), p.get("drawing_label")
            if kind == "space":
                r.zones = list(p.get("zones") or [])
            elif kind == "zone":
                r.parent = p["space_id"]
            else:
                r.connects = list(p.get("connects") or [])
                r.width, r.sill, r.height = p.get("width_m"), p.get("sill_m"), p.get("height_m")
                if p.get("span"):
                    r.span = [local.point(q) for q in p["span"]]
                if p.get("swings"):
                    r.swings = [[local.point(q) for q in leaf] for leaf in p["swings"]]
            ws.objects[r.id] = r
            if p.get("hidden") or p.get("ignored"):
                ws.overrides[r.id] = Override(hidden=bool(p.get("hidden")) or None, ignored=bool(p.get("ignored")) or None)
    for rid in changes.get("all_retired") or []:
        # kept so that their IDs are never given again; what they were is not known
        ws.objects.setdefault(rid, ObjectRecord(id=rid, kind="space", type="unspecified", type_source="package",
                                                geometry={"type": "Point", "coordinates": [0.0, 0.0]},
                                                status="retired", retired_at=exported_at))

    # each building gives new IDs after every one it gave
    for b_id, b in buildings.items():
        codes = [parse_id(i).code for i in ws.objects if i.startswith(b_id + "-") and parse_id(i).level == "object"]
        numbers = [int(c) for c in codes if c.isdigit()]
        b.next_object_seq = max(numbers, default=0) + 1
        assert format_object_code(b.next_object_seq)  # a code it can write

    # its export, as the last one: the next lists what changed since it. What it
    # was is taken as this workspace exports it: a package keeps positions to about a
    # centimetre, so areas and label points worked out again may differ by as much,
    # which is no change.
    again = build_features(ws)
    ws.exports.append(ExportRecord(sequence=manifest.export.sequence, exported_at=exported_at, file=file_name,
                                   objects={f["id"]: _hash(f) for fs in again.values() for f in fs}))
    return ws


class _Local:
    """Longitude and latitude back to a building's local drawing metres: the
    inverse of georef.Georeferencer for its placement."""

    def __init__(self, placement):
        import math

        self.p = placement
        b = math.radians(placement.bearing)
        self.cos, self.sin = math.cos(b), math.sin(b)
        self.to_plane = Transformer.from_crs(
            "EPSG:4326",
            f"+proj=aeqd +lat_0={placement.lat} +lon_0={placement.lon} +x_0=0 +y_0=0 +ellps=WGS84 +units=m",
            always_xy=True,
        )

    def xy(self, lon, lat):
        east, north = self.to_plane.transform(lon, lat)
        dx = east * self.cos - north * self.sin
        dy = east * self.sin + north * self.cos
        return self.p.x + dx, self.p.y + dy

    def point(self, lonlat) -> list[float]:
        x, y = self.xy(lonlat[0], lonlat[1])
        return [round(x, LOCAL_DECIMALS), round(y, LOCAL_DECIMALS)]

    def geometry(self, geojson: dict):
        g = transform(lambda lon, lat, z=None: self._many(lon, lat), shape(geojson))
        return _rounded(g)

    def _many(self, lon, lat):
        import numpy as np

        xs, ys = [], []
        for a, b in zip(np.atleast_1d(lon), np.atleast_1d(lat)):
            x, y = self.xy(float(a), float(b))
            xs.append(x)
            ys.append(y)
        return xs, ys


def _rounded(geom):
    from shapely import set_precision

    return set_precision(geom, 10 ** -LOCAL_DECIMALS, mode="pointwise")
