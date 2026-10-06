"""Checking an exchange package: structure, schemas, IDs and references."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from pydantic import ValidationError

from .ids import LEVELS, parse_id
from .package import COLLECTIONS, FILES, FORMAT_NAME, FORMAT_VERSION, Changes, FeatureCollection, Manifest

KIND_LEVEL = {"location": "location", "building": "building", "floor": "floor",
              "space": "object", "zone": "object", "opening": "object"}


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
            return z.read(name).decode("utf-8")

        raw = read("manifest.json")
        if raw is None:
            return errors
        try:
            manifest = Manifest.model_validate_json(raw)
        except ValidationError as e:
            return [f"manifest.json: {e}"]
        if manifest.format != FORMAT_NAME:
            errors.append(f"unknown format {manifest.format!r}")
        if manifest.format_version.split(".")[0] != FORMAT_VERSION.split(".")[0]:
            errors.append(f"unsupported format version {manifest.format_version}")
        project = manifest.project.id

        ids: dict[str, str] = {}  # id -> kind
        collections: dict[str, list] = {}
        for role, props in COLLECTIONS.items():
            text = read(FILES[role])
            if text is None:
                continue
            try:
                fc = FeatureCollection[props].model_validate_json(text)
            except ValidationError as e:
                errors.append(f"{FILES[role]}: {_first_errors(e)}")
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
            for z in zs:
                if ids.get(z) != "zone":
                    errors.append(f"{space}: lists unknown zone {z}")
        for f in collections.get("openings", []):
            expect_parent(f.id, f.properties.floor_id, "floor")
            for s in f.properties.connects:
                if ids.get(s) != "space":
                    errors.append(f"{f.id}: connects to unknown space {s}")
                elif not s.startswith(f.properties.floor_id + "-"):
                    errors.append(f"{f.id}: connects to {s} on another floor")

        text = read(FILES["objects"])
        if text is not None:
            rows = list(csv.DictReader(io.StringIO(text)))
            listed = {r["id"] for r in rows if r.get("kind") != "project"}
            if listed != set(ids):
                errors.append(
                    f"{FILES['objects']}: rows do not match the features "
                    f"({len(listed - set(ids))} extra, {len(set(ids) - listed)} missing)"
                )

        text = read(FILES["changes"])
        if text is not None:
            try:
                changes = Changes.model_validate_json(text)
                for i in changes.added + changes.changed:
                    if i not in ids:
                        errors.append(f"{FILES['changes']}: {i} is listed as added/changed but not in the package")
                for i in changes.all_retired:
                    if i in ids:
                        errors.append(f"{FILES['changes']}: retired ID {i} is still in the package")
                if changes.sequence != manifest.export.sequence:
                    errors.append(f"{FILES['changes']}: sequence does not match the manifest")
            except ValidationError as e:
                errors.append(f"{FILES['changes']}: {_first_errors(e)}")
    return errors


def _first_errors(e: ValidationError, n: int = 3) -> str:
    parts = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:n]]
    more = e.error_count() - n
    return "; ".join(parts) + (f" (+{more} more)" if more > 0 else "")


def load_package_json(path: str | Path, name: str):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(name))
