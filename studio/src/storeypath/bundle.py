"""A project as one file to send, and a project from one.

A package (*.storeypath, export.py) is what other systems read: one building.
A project file (*.storeypath-project) is for another Studio to continue the
project: the workspace (every correction, edit and ID, its export history), its
drawings, and the item types it uses. It is not a package: no other system
reads it. (A project file of format 0.6 and before was a package of the whole
project with the project in ``studio/``; it opens as it did.)

Opening a project file gives the project back as it was. Opening a package
rebuilds its building from what the package holds: the same project code and
IDs, the spaces, zones and openings with their names, numbers, types and flags,
the walls, the placement, the items where they stand in the building, the IDs
retired, and its export number, so the next export follows on from it (a system
that applied it takes that one as the next). Into a project that is here
already, the building is added, or put in place of the one there (the others
are left as they are). Its floors have no drawing (one put in place of a floor
keeps that floor's): they are reviewed, corrected, walked through and exported;
to read one again, its drawing is added to it (its rooms keep their IDs, lined
up on the walls the floor has).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union

from .export import compared
from .ids import format_object_code, is_item_id, parse_id
from .package import FILES, Manifest
from .workspace import (
    Building,
    ExportRecord,
    Floor,
    Item,
    Location,
    ObjectRecord,
    Override,
    Placement,
    Project,
    Workspace,
)

STUDIO = "studio/"
PROJECT_EXTENSION = ".storeypath-project"
PROJECT_MANIFEST = "project.json"  # what a project file is: {"format": "storeypath-project", …}
PROJECT_FORMAT = "storeypath-project"
CATALOGUE_FILE = "catalogue.json"
WORKSPACE_FILE = "studio/project.spproj"
DRAWINGS = "studio/drawings/"
DRAWING_TYPES = {".dxf", ".dwg"}
MAX_FILES = 5000  # entries in a file opened
MAX_BYTES = 4 << 30  # what they hold, uncompressed
LOCAL_DECIMALS = 4  # metres: a tenth of a millimetre


class ProjectExists(Exception):
    """The project of a file opened is here already (or, for a building's package,
    that building is)."""

    def __init__(self, code: str, name: str, building: str | None = None):
        what = f"its building {building}" if building else "it"
        super().__init__(f"{name} ({code}) is here already, and {what}" if building else f"{name} ({code}) is here already")
        self.code, self.name, self.building = code, name, building


# ---- a project to send ---------------------------------------------------------


def export_project(ws_path: Path, out) -> None:
    """The project as one file for another Studio (*.storeypath-project): the
    workspace and its drawings (every floor's, wherever it is, and the others added
    to it), the workspace pointing at them there, and the item types of this
    Studio's catalogue. Not entered as an export (it is for people, not systems)."""
    from importlib.metadata import version

    from . import catalogue
    from .workspace import utcnow

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
    data = ws_path.parent.parent  # the Studio's data folder: its catalogue of item types, when it has one
    cat = catalogue.load(data) if (data / catalogue.FILE_NAME).is_file() else catalogue.default_catalogue()
    about = {"format": PROJECT_FORMAT, "format_version": 1, "project": {"id": ws.id, "name": ws.project.name},
             "made_at": utcnow().isoformat(), "generator": {"name": "storeypath", "version": version("storeypath")},
             "about": "A StoreyPath project, for StoreyPath Studio to continue it (open it on the Projects page). "
                      "It is not a package: other systems read a building's package (*.storeypath)."}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(PROJECT_MANIFEST, json.dumps(about, ensure_ascii=False, indent=2))
        z.writestr(WORKSPACE_FILE, shipped.model_dump_json(indent=1))
        z.writestr(CATALOGUE_FILE, json.dumps(cat.model_dump(), ensure_ascii=False, indent=1))
        for name, path in files.items():
            z.write(path, DRAWINGS + name)


# ---- a project from a file -----------------------------------------------------


def project_code(source: Path) -> str | None:
    """The code of the project a file is of (a package or a project file), or None."""
    try:
        with zipfile.ZipFile(source) as z:
            names = set(z.namelist())
            if PROJECT_MANIFEST in names:
                return json.loads(z.read(PROJECT_MANIFEST))["project"]["id"]
            if "manifest.json" in names:
                return json.loads(z.read("manifest.json"))["project"]["id"]
            if WORKSPACE_FILE in names:
                return json.loads(z.read(WORKSPACE_FILE))["project"]["code"]
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        return None
    return None


def open_file(data: Path, source: Path, *, replace: bool = False) -> dict:
    """A project from a file, put in ``data/<code>/``. A project file gives the
    project as it was; a package, its building rebuilt from it. Raises
    ProjectExists when the project is here and ``replace`` is not set: a project
    file is then put in its place; a package's building is added to it, or put in
    place of that building (the project's others are left as they are)."""
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
        existing = next(folder.glob("*.spproj"), None) if folder.exists() else None
        if how == "package" and existing is not None:
            here = Workspace.load(existing)
            b_ids = [b for b in _building_ids(ws)]
            there = [b for b in b_ids if b in _building_ids(here)]
            if there and not replace:
                raise ProjectExists(code, here.project.name, there[0])
            _merge(here, ws)
            here.save(existing)
            learned = _learn_types(data, source)
            return {"code": code, "name": here.project.name, "how": "building", "buildings": b_ids,
                    "replaced": there, "floors": sum(1 for _ in ws.iter_floors()), "drawings": 0,
                    "item_types_added": learned}
        if folder.exists():
            if not replace:
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
    learned = _learn_types(data, source)
    floors = sum(1 for _ in ws.iter_floors())
    return {"code": code, "name": ws.project.name, "how": how, "floors": floors, "drawings": drawings,
            "item_types_added": learned}


def _building_ids(ws: Workspace) -> list[str]:
    from .export import building_ids

    return building_ids(ws)


def _merge(here: Workspace, pkg: Workspace) -> None:
    """A package's buildings (rebuilt, ``pkg``) into the project here: each added, or
    put in place of the one with its code. A floor put in place of one keeps that
    floor's drawing, so it can be read again (its rooms keep their IDs). Items the
    package holds are put where it has them; one it retired is retired here; the
    others are left as they are (one carried to another building waits for that
    building's package). The package's export is entered as the last one, when it
    is later than the last here."""
    from .ids import make_id
    from .workspace import utcnow

    for loc in pkg.locations:
        mine = next((l for l in here.locations if l.code == loc.code), None)
        if mine is None:
            here.locations.append(loc)
            continue
        for b in loc.buildings:
            b_id = make_id(pkg.id, loc.code, b.code)
            old = next((x for x in mine.buildings if x.code == b.code), None)
            if old is not None:
                drawn = {f.code: f.source for f in old.floors}
                for f in b.floors:
                    if f.source is None and drawn.get(f.code) is not None:
                        f.source = drawn[f.code]
                        f.warnings = [w for w in f.warnings if "add this floor's drawing" not in w]
                mine.buildings[mine.buildings.index(old)] = b
            else:
                mine.buildings.append(b)
            prefix = b_id + "-"  # what is in it now is the package's; what it retired stays retired
            for i in [i for i, r in here.objects.items() if i.startswith(prefix) and r.status != "retired"]:
                del here.objects[i]
            for i in [i for i in here.overrides if i.startswith(prefix)]:
                del here.overrides[i]
    for i, r in pkg.objects.items():
        if r.status == "retired" and i in here.objects:
            continue  # kept as it was retired here, not as the package's bare record of it
        here.objects[i] = r
    here.overrides.update(pkg.overrides)
    now = utcnow()
    for i, it in pkg.items.items():
        if it.status == "retired":
            mine = here.items.get(i)
            if mine is None:
                here.items[i] = it
            elif mine.status != "retired":
                mine.status, mine.retired_at = "retired", now
        else:
            here.items[i] = it
    here.next_item_seq = max(here.next_item_seq, pkg.next_item_seq)
    record = pkg.exports[-1] if pkg.exports else None
    last = here.exports[-1] if here.exports else None
    if record is not None and (last is None or record.sequence > last.sequence):
        kept = {} if last is None else {i: h for i, h in last.objects.items() if i not in record.objects}
        places = {} if last is None else {i: b for i, b in last.places.items() if i not in record.places}
        here.exports.append(record.model_copy(update={"objects": {**kept, **record.objects},
                                                      "places": {**places, **record.places}}))


def _learn_types(data: Path, source: Path) -> list[str]:
    """The item types a package's (or a project file's) catalogue has and this
    Studio's lacks, added to it (a type's code is its identity everywhere: one
    already here is kept as it is)."""
    from . import catalogue

    with zipfile.ZipFile(source) as z:
        names = set(z.namelist())
        if "manifest.json" in names:
            manifest = Manifest.model_validate_json(z.read("manifest.json"))
            name = manifest.files.get("catalogue")
        else:
            name = CATALOGUE_FILE  # a project file
        if not name or name not in names:
            return []
        theirs = catalogue.Catalogue.model_validate_json(z.read(name))
    ours = catalogue.load(data)
    new = [t for t in theirs.types if ours.get(t.code) is None]
    if new:
        ours.types.extend(new)
        catalogue.save(data, ours)
    return [t.code for t in new]


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
    # furniture and equipment, back where the package has them: where each stands in
    # its building (0.7); from an earlier package, its map position and heading (where
    # its front faces on earth) back to the building's frame
    if "items" in manifest.files and manifest.files["items"] in z.namelist():
        for f in json.loads(z.read(manifest.files["items"]))["features"]:
            p = f["properties"]
            if p.get("local"):
                x, y, rotation = p["local"]["x_m"], p["local"]["y_m"], p["local"]["rotation_deg"]
            else:
                local = to_local[p["building_id"]]
                x, y = local.point(p["display_point"])
                rotation = (180.0 - (p["heading"] - local.p.bearing)) % 360
            ws.items[f["id"]] = Item(id=f["id"], type=p["type"], floor_id=p["floor_id"], x=x, y=y,
                                     rotation=round(rotation, 2), values=dict(p.get("values") or {}),
                                     created_at=exported_at)
    for rid in changes.get("all_retired") or []:
        # kept so that their IDs are never given again; what they were is not known
        if is_item_id(rid):
            ws.items.setdefault(rid, Item(id=rid, type="", floor_id="", x=0.0, y=0.0, status="retired",
                                          retired_at=exported_at))
            continue
        ws.objects.setdefault(rid, ObjectRecord(id=rid, kind="space", type="unspecified", type_source="package",
                                                geometry={"type": "Point", "coordinates": [0.0, 0.0]},
                                                status="retired", retired_at=exported_at))
    ws.next_item_seq = max((int(i.split("-")[1][1:]) for i in ws.items), default=0) + 1

    # a building not on the map stands where the package's site plan has it: its
    # anchor (the drawing point at the site's centre) and turn give its position
    from .export import footprint
    import math as _math
    from .workspace import SitePosition

    for b_id, b in buildings.items():
        pl = manifest.placements.get(b_id)
        if b.placement is not None or pl is None:
            continue
        fp = footprint(b)
        c = fp.centroid if fp is not None else None
        pivot = (round(c.x, 3), round(c.y, 3)) if c is not None else (pl.x, pl.y)
        r = _math.radians(pl.bearing)
        dx, dy = pivot[0] - pl.x, pivot[1] - pl.y
        b.site = SitePosition(x=round(dx * _math.cos(r) + dy * _math.sin(r), 4),
                              y=round(-dx * _math.sin(r) + dy * _math.cos(r), 4), rotation=pl.bearing, pivot=pivot)
        loc = next(l for l in ws.locations if any(x is b for x in l.buildings))
        if loc.site_origin is None and abs(pl.bearing) < 1e-9:
            loc.site_origin = (pl.x, pl.y)  # the drawing point at the site's centre, as drawn

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
    from .catalogue import Catalogue

    cat_file = manifest.files.get("catalogue")
    cat = Catalogue.model_validate_json(z.read(cat_file)) if cat_file and cat_file in z.namelist() else None
    ws.exports.append(ExportRecord(
        sequence=manifest.export.sequence, exported_at=exported_at, file=file_name, objects=compared(ws, cat),
        buildings=sorted(buildings) if manifest.scope is not None else None,
        places={i: it.floor_id.rsplit("-", 1)[0] for i, it in ws.items.items() if it.status == "active"}))
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
