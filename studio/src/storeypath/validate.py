"""Checking an exchange package: structure, schemas, IDs and references."""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .catalogue import Catalogue
from .ids import LEVELS, MAX_ID_LENGTH, is_item_id, parse_id
from .package import (COLLECTIONS, FILES, FORMAT_NAME, FORMAT_VERSION, ONE_BUILDING_FROM, Changes, FeatureCollection,
                      ItemProps, Manifest, version_problem, version_tuple)

KIND_LEVEL = {"location": "location", "building": "building", "floor": "floor",
              "space": "object", "zone": "object", "opening": "object"}
# the kinds of objects.csv rows this reader knows; rows of others (a later format's) are
# left alone, as a reader leaves unknown files and properties
KNOWN_KINDS = {"project", *KIND_LEVEL, "item"}
LOCAL_AGREES_M = 0.05  # an item's map position and its position in its building

M = TypeVar("M", bound=BaseModel)


def validate_package(path: str | Path) -> list[str]:
    """Return a list of problems; an empty list means the package is valid."""
    errors: list[str] = []
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        return [f"not a readable package: {e}"]

    with z:
        names = set(z.namelist())

        def read(name: str) -> str | None:
            if name not in names:
                errors.append(f"missing file {name}")
                return None
            try:
                return z.read(name).decode("utf-8")
            except UnicodeDecodeError as e:
                errors.append(f"{name}: not UTF-8 text ({e})")
                return None

        def load(name: str, model: type[M]) -> M | None:
            """A JSON file of the package, as its model: strictly (a number is a number,
            not a string of one; true is true, not "true"), as every reader reads it."""
            text = read(name)
            if text is None:
                return None
            try:
                doc = _strict_json(text)
            except ValueError as e:
                errors.append(f"{name}: not JSON: {e}")
                return None
            try:
                return model.model_validate_json(text, strict=True)
            except ValidationError as e:
                errors.append(f"{name}: {_first_errors(e, doc)}")
                return None

        manifest = load("manifest.json", Manifest)
        if manifest is None:
            return errors
        if manifest.format != FORMAT_NAME:
            errors.append(f"unknown format {manifest.format!r}")
        if (why := version_problem(manifest.format_version)) is not None:
            errors.append(why)
        try:
            version = version_tuple(manifest.format_version)
        except ValueError:  # not a version: which format's rules it follows is not known
            return errors
        project = manifest.project.id
        one_building = version >= ONE_BUILDING_FROM

        ids: dict[str, str] = {}  # id -> kind
        collections: dict[str, list] = {}
        for role, props in COLLECTIONS.items():
            fc = load(FILES[role], FeatureCollection[props])
            if fc is None:
                continue
            collections[role] = fc.features
            if manifest.counts.get(role) != len(fc.features):
                errors.append(f"{FILES[role]}: manifest counts {manifest.counts.get(role)}, file has {len(fc.features)}")
            for f in fc.features:
                kind = f.properties.kind
                try:
                    pid = parse_id(f.id)
                except ValueError as e:
                    errors.append(f"{FILES[role]}: {e}")
                    continue
                if f.id in ids:
                    errors.append(f"duplicate ID {f.id}")
                ids[f.id] = kind
                if pid.project != project:
                    errors.append(f"{f.id}: project segment is not the package's project {project}")
                if pid.level != KIND_LEVEL[kind]:
                    errors.append(f"{f.id}: a {kind} ID needs {LEVELS.index(KIND_LEVEL[kind]) + 1} segments")

        def expect_parent(child: str, parent: str | None, kind: str) -> None:
            if parent is None or ids.get(parent) != kind:
                errors.append(f"{child}: parent {kind} {parent} is not in the package")
            elif not child.startswith(parent + "-"):
                errors.append(f"{child}: ID does not start with its parent {parent}")

        if one_building:  # from 0.7, a package holds one building, and names it
            if manifest.scope is None or len(manifest.scope.buildings) != 1:
                errors.append("a package of this format holds one building: its manifest's scope names it")
            if len(collections.get("buildings", [])) != 1:
                errors.append(f"{FILES['buildings']}: a package of this format holds one building, "
                              f"this one {len(collections.get('buildings', []))}")
        if manifest.scope is not None:  # exactly the buildings it lists
            held = {f.id for f in collections.get("buildings", [])}
            for b in sorted(set(manifest.scope.buildings) - held):
                errors.append(f"scope lists building {b}, which is not in the package")
            for b in sorted(held - set(manifest.scope.buildings)):
                errors.append(f"building {b} is in the package but not in its scope")
        for f in collections.get("buildings", []):
            expect_parent(f.id, f.properties.location_id, "location")
        for f in collections.get("floors", []):
            expect_parent(f.id, f.properties.building_id, "building")
        for f in collections.get("spaces", []):
            expect_parent(f.id, f.properties.floor_id, "floor")
        zones_of = {f.id: set(f.properties.zones) for f in collections.get("spaces", [])}
        for f in collections.get("zones", []):
            expect_parent(f.id, f.properties.floor_id, "floor")
            space = f.properties.space_id
            if ids.get(space) != "space":
                errors.append(f"{f.id}: zone of unknown space {space}")
            elif f.id not in zones_of.get(space, set()):
                errors.append(f"{f.id}: not listed in the zones of its space {space}")
        for space, zs in zones_of.items():
            for zone in zs:
                if ids.get(zone) != "zone":
                    errors.append(f"{space}: lists unknown zone {zone}")
        for f in collections.get("openings", []):
            expect_parent(f.id, f.properties.floor_id, "floor")
            for s in f.properties.connects:
                if ids.get(s) != "space":
                    errors.append(f"{f.id}: connects to unknown space {s}")
                elif not s.startswith(f.properties.floor_id + "-"):
                    errors.append(f"{f.id}: connects to {s} on another floor")

        # Items (format 0.6): furniture and equipment, when the package has them. Their
        # IDs are the project's and their own number; where they are is data.
        if "items" in manifest.files:
            codes = None
            if "catalogue" in manifest.files and (cat := load(manifest.files["catalogue"], Catalogue)) is not None:
                codes = {t.code for t in cat.types}
            fc = load(manifest.files["items"], FeatureCollection[ItemProps])
            if fc is not None:
                items = fc.features
                if manifest.counts.get("items") != len(items):
                    errors.append(f"{manifest.files['items']}: manifest counts {manifest.counts.get('items')}, file has {len(items)}")
                for f in items:
                    q = f.properties
                    if not is_item_id(f.id) or not f.id.startswith(project + "-"):
                        errors.append(f"{f.id}: not an item ID of project {project} ({project}-I and six digits)")
                    if f.id in ids:
                        errors.append(f"duplicate ID {f.id}")
                    ids[f.id] = "item"
                    if ids.get(q.floor_id) != "floor" or not q.floor_id.startswith(q.building_id + "-"):
                        errors.append(f"{f.id}: on unknown floor {q.floor_id} (or not of building {q.building_id})")
                    for key, kind in ((q.space_id, "space"), (q.zone_id, "zone")):
                        if key is not None and (ids.get(key) != kind or not key.startswith(q.floor_id + "-")):
                            errors.append(f"{f.id}: in unknown {kind} {key} (or not on its floor)")
                    if codes is not None and q.type not in codes:
                        errors.append(f"{f.id}: type {q.type} is not in the catalogue")
                    if one_building and q.local is None:
                        errors.append(f"{f.id}: no position in its building (local)")
                    elif q.local is not None and (p := manifest.placements.get(q.building_id)) is not None:
                        lon, lat = _lonlat(p, q.local.x_m, q.local.y_m)
                        off = math.hypot((lon - q.display_point[0]) * 111_320 * math.cos(math.radians(lat)),
                                         (lat - q.display_point[1]) * 110_574)
                        if not off <= LOCAL_AGREES_M:  # (NaN is never within it)
                            errors.append(f"{f.id}: its map position is {off:.2f} m from its position in its building")

        unknown: set[str] = set()
        text = read(FILES["objects"])
        if text is not None:
            rows = list(csv.DictReader(io.StringIO(text)))
            unknown = {r["id"] for r in rows if r.get("kind") not in KNOWN_KINDS}  # a later format's
            listed = {r["id"] for r in rows if r.get("kind") != "project"} - unknown
            if listed != set(ids):
                errors.append(
                    f"{FILES['objects']}: rows do not match the features "
                    f"({len(listed - set(ids))} extra, {len(set(ids) - listed)} missing)"
                )

        changes = load(FILES["changes"], Changes)
        if changes is not None:
            for i in changes.added + changes.changed:
                if i not in ids and i not in unknown:
                    errors.append(f"{FILES['changes']}: {i} is listed as added/changed but not in the package")
            for i in changes.all_retired:
                if i in ids:
                    errors.append(f"{FILES['changes']}: retired ID {i} is still in the package")
            for m in changes.moved_away:
                if not is_item_id(m.id) or m.id in ids:
                    errors.append(f"{FILES['changes']}: {m.id} is listed as moved away but is not an item gone from here")
                if not m.building_id.startswith(project + "-") or m.building_id in ids:
                    errors.append(f"{FILES['changes']}: {m.id} moved to {m.building_id}, not another building of the project")
            if changes.sequence != manifest.export.sequence:
                errors.append(f"{FILES['changes']}: sequence does not match the manifest")
    return errors


def _lonlat(p, x: float, y: float) -> tuple[float, float]:
    """A point of a building's own frame on the map, by its placement."""
    from .georef import Georeferencer
    from .workspace import Placement

    return Georeferencer(Placement(lon=p.lon, lat=p.lat, x=p.x, y=p.y, bearing=p.bearing)).lonlat(x, y)


def _not_json(constant: str):
    raise ValueError(f"{constant} is not a number in JSON")


def _finite(number: str) -> float:
    value = float(number)
    if not math.isfinite(value):
        raise ValueError(f"{number[:32]} is too large a number")
    return value


def _whole(number: str) -> int:
    value = int(number)
    if not -2**63 <= value < 2**63:
        raise ValueError(f"{number[:32]} is too large a number")
    return value


def _strict_json(text: str):
    """A file's document, read as a strict JSON reader reads it: NaN and Infinity are
    not JSON, and a number too large for a double (or a whole number for 64 bits) is
    not one (Python's json reads them all), wherever they are, in properties a reader
    knows or not."""
    return json.loads(text, parse_constant=_not_json, parse_float=_finite, parse_int=_whole)


def _first_errors(e: ValidationError, doc=None, n: int = 3) -> str:
    def where(loc) -> str:
        at = ".".join(map(str, loc))
        if len(loc) > 1 and loc[0] == "features" and isinstance(loc[1], int):  # say which feature
            try:
                fid = doc["features"][loc[1]]["id"]
            except (KeyError, IndexError, TypeError):
                fid = None
            if isinstance(fid, str):
                return f"{fid[:MAX_ID_LENGTH]} ({at})"
        return at

    parts = [f"{where(err['loc'])}: {err['msg']}" for err in e.errors()[:n]]
    more = e.error_count() - n
    return "; ".join(parts) + (f" (+{more} more)" if more > 0 else "")


def load_package_json(path: str | Path, name: str):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(name))
