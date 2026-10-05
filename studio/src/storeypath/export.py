"""Writing the exchange package from a workspace."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from importlib.metadata import version
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import polylabel, unary_union

from .assets import format_spec
from .geometry import as_polygons
from .georef import Georeferencer
from .ids import child_id, make_id
from .package import (
    FILES,
    OBJECTS_CSV_COLUMNS,
    Changes,
    ExportInfo,
    Generator,
    Manifest,
    PlacementInfo,
    ProjectInfo,
    SourceInfo,
    json_schemas,
)
from .levels import DEFAULT_PARAPET_M
from .types import OpeningType, SpaceType
from .workspace import ExportRecord, Placement, Workspace, utcnow

COORD_DECIMALS = 7  # ~1 cm


class ExportError(Exception):
    pass


def _rounded(lonlat_geom) -> dict:
    out = shapely.transform(lonlat_geom, lambda c: np.round(c, COORD_DECIMALS))
    return json.loads(json.dumps(mapping(out)))  # tuples → lists


SLIVER_M2 = 0.01  # holes and parts smaller than this (1 dm²) do not survive 1 cm rounding


def _without_slivers(geom):
    """Polygons without holes and parts too small to keep their shape once rounded to
    the package's 1 cm: they come out as rings of one or two points, which readers
    (the 3D viewer's triangulator among them) cannot draw."""
    if not isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    parts = [Polygon(p.exterior, [h for h in p.interiors if Polygon(h).area >= SLIVER_M2])
             for p in as_polygons(geom) if p.area >= SLIVER_M2]
    if not parts:
        return geom
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _geo(geom, g: Georeferencer) -> dict:
    return _rounded(g.geometry(_without_slivers(geom)))


def _label_point(geom) -> tuple[float, float]:
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda p: p.area)
    if isinstance(geom, Polygon):
        p = polylabel(geom, tolerance=0.05)
    else:
        p = geom.representative_point()
    return p.x, p.y


def _lonlat(g: Georeferencer, xy) -> list[float]:
    lon, lat = g.lonlat(*xy)
    return [round(lon, COORD_DECIMALS), round(lat, COORD_DECIMALS)]


def _feature(fid: str, geometry: dict | None, props: dict) -> dict:
    return {"type": "Feature", "id": fid, "geometry": geometry, "properties": props}


def _hash(feature: dict) -> str:
    return hashlib.sha256(json.dumps(feature, sort_keys=True).encode()).hexdigest()[:16]


def unplaced(ws: Workspace) -> list[str]:
    """Buildings with floors that are not placed on the map yet."""
    return [make_id(ws.id, loc.code, b.code) for loc in ws.locations for b in loc.buildings
            if b.placement is None and any(f.outline for f in b.floors)]


def placements(ws: Workspace) -> dict[str, tuple[Placement, bool]]:
    """Each building's placement, and whether it is a real one. A building not
    placed yet goes around 0°N 0°E: its shape and size are true, its position on
    earth is not. Unplaced buildings of one location share an anchor (the middle of
    them all), so they keep their positions relative to each other as drawn."""
    out = {}
    for loc in ws.locations:
        loose = [b for b in loc.buildings if b.placement is None]
        outlines = [shape(f.outline) for b in loose for f in b.floors if f.outline]
        middle = unary_union(outlines).centroid if outlines else None
        provisional = Placement(lon=0.0, lat=0.0, x=round(middle.x, 3) if middle else 0.0,
                                y=round(middle.y, 3) if middle else 0.0, bearing=0.0)
        for b in loc.buildings:
            out[make_id(ws.id, loc.code, b.code)] = (b.placement, True) if b.placement else (provisional, False)
    return out


OUTDOOR = {SpaceType.TERRACE.value, SpaceType.BALCONY.value}


def _walls_and_parapets(walls, spaces, thickness: float | None):
    """A floor's walls, split into those that rise to the ceiling and the parapets. On
    a floor with a terrace or balcony, a wall that encloses no room is low: the wall
    around a roof, and anything built onto it (a pier, a box for a pipe). A room's
    own walls stay full height, terrace or not beside them."""
    if walls is None or walls.is_empty or not any(t in OUTDOOR for _, t in spaces):
        return walls, None
    reach = 1.5 * max(thickness or 0.2, 0.1) + 0.05  # across a wall, to the space on its far side
    rooms = unary_union([s for s, t in spaces if t not in OUTDOOR]).buffer(reach)
    low = walls.difference(rooms)
    low = unary_union([p for p in as_polygons(low) if p.area >= 0.01])
    if low.is_empty:
        return walls, None
    return walls.difference(low), low


def build_features(ws: Workspace) -> dict[str, list[dict]]:
    placed = placements(ws)
    out: dict[str, list[dict]] = {k: [] for k in ("location", "buildings", "floors", "spaces", "openings")}
    for loc in ws.locations:
        loc_id = make_id(ws.id, loc.code)
        footprints = []
        for b in loc.buildings:
            b_id = child_id(loc_id, b.code)
            g = Georeferencer(placed[b_id][0])
            outlines = []
            for f in sorted(b.floors, key=lambda f: f.ordinal):
                f_id = child_id(b_id, f.code)
                outline = shape(f.outline) if f.outline else None
                if outline is not None:
                    outlines.append(outline)
                spaces = [(shape(r.geometry), ws.effective(r)["type"]) for r in ws.floor_objects(f_id)
                          if r.kind == "space"]
                walls, parapets = _walls_and_parapets(shape(f.walls) if f.walls else None, spaces, f.wall_thickness)
                out["floors"].append(
                    _feature(
                        f_id,
                        _geo(outline, g) if outline is not None else None,
                        {"kind": "floor", "code": f.code, "name": f.name, "building_id": b_id,
                         "ordinal": f.ordinal, "elevation": f.elevation, "height": f.height,
                         "walls": _geo(walls, g) if walls is not None else None,
                         "wall_thickness_m": f.wall_thickness,
                         "parapets": _geo(parapets, g) if parapets is not None else None,
                         "parapet_height_m": (f.parapet_height or DEFAULT_PARAPET_M) if parapets is not None else None},
                    )
                )
                for r in sorted(ws.floor_objects(f_id), key=lambda r: r.id):
                    eff = ws.effective(r)
                    geom = shape(r.geometry)
                    if r.kind == "space":
                        out["spaces"].append(
                            _feature(
                                r.id,
                                _geo(geom, g),
                                {"kind": "space", "type": eff["type"], "name": eff["name"],
                                 "number": eff["number"], "floor_id": f_id,
                                 "area_m2": round(geom.area, 2),
                                 "display_point": _lonlat(g, _label_point(geom)),
                                 "hidden": eff["hidden"], "ignored": eff["ignored"]},
                            )
                        )
                    else:
                        out["openings"].append(
                            _feature(
                                r.id,
                                _geo(geom, g),
                                {"kind": "opening", "type": r.type, "floor_id": f_id,
                                 "connects": r.connects, "exterior": len(r.connects) == 1,
                                 "width_m": r.width,
                                 "span": [_lonlat(g, p) for p in r.span] if r.span else None,
                                 "hidden": eff["hidden"], "ignored": eff["ignored"]},
                            )
                        )
            footprint = None
            if outlines and g is not None:
                footprint = g.geometry(unary_union(outlines))
                footprints.append(footprint)
            out["buildings"].append(
                _feature(
                    b_id,
                    _rounded(footprint) if footprint is not None else None,
                    {"kind": "building", "code": b.code, "name": b.name, "location_id": loc_id,
                     "display_point": _round_pt(footprint.representative_point()) if footprint else None},
                )
            )
        hull = unary_union(footprints).convex_hull if footprints else None
        out["location"].append(
            _feature(
                loc_id,
                _rounded(hull) if hull else None,
                {"kind": "location", "code": loc.code, "name": loc.name, "address": loc.address,
                 "project_id": ws.id,
                 "display_point": _round_pt(hull.centroid) if hull else None},
            )
        )
    return out


def _round_pt(p) -> list[float]:
    return [round(p.x, COORD_DECIMALS), round(p.y, COORD_DECIMALS)]


def _objects_csv(ws: Workspace, features: dict[str, list[dict]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=OBJECTS_CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerow({"id": ws.id, "kind": "project", "name": ws.project.name, "project_id": ws.id})
    ordinals = {f["id"]: f["properties"]["ordinal"] for f in features["floors"]}
    for role in ("location", "buildings", "floors", "spaces", "openings"):
        for f in features[role]:
            p = f["properties"]
            segs = f["id"].split("-")
            ids = {lvl: "-".join(segs[:n]) for n, lvl in
                   ((2, "location_id"), (3, "building_id"), (4, "floor_id")) if len(segs) >= n}
            pt = p.get("display_point") or (f["geometry"]["coordinates"] if f["geometry"] and f["geometry"]["type"] == "Point" else None)
            w.writerow({
                "id": f["id"], "kind": p["kind"], "type": p.get("type", ""),
                "name": p.get("name") or "", "number": p.get("number") or "",
                "project_id": ws.id, **ids,
                "floor_ordinal": ordinals.get(ids.get("floor_id"), ""),
                "area_m2": p.get("area_m2", ""),
                "lon": pt[0] if pt else "", "lat": pt[1] if pt else "",
                "hidden": "true" if p.get("hidden") else "", "ignored": "true" if p.get("ignored") else "",
            })
    return buf.getvalue()


def export_package(ws: Workspace, out_path, *, record: bool = True) -> Manifest:
    """Write the package to ``out_path`` (a path, or a binary file object). With
    ``record`` the export is entered in the workspace, so the next one lists what
    changed since; without it (a preview) the workspace is left as it was."""
    features = build_features(ws)
    hashes = {f["id"]: _hash(f) for fs in features.values() for f in fs}

    prev = ws.exports[-1] if ws.exports else None
    sequence = (prev.sequence + 1) if prev else 1
    prev_hashes = prev.objects if prev else {}
    all_retired = sorted(i for i, r in ws.objects.items() if r.status == "retired")
    changes = Changes(
        sequence=sequence,
        previous_sequence=prev.sequence if prev else None,
        added=sorted(i for i in hashes if i not in prev_hashes),
        changed=sorted(i for i in hashes if i in prev_hashes and prev_hashes[i] != hashes[i]),
        retired=sorted(i for i in prev_hashes if i not in hashes),
        all_retired=all_retired,
    )

    now = utcnow()
    manifest = Manifest(
        generator=Generator(name="storeypath", version=version("storeypath")),
        project=ProjectInfo(id=ws.id, name=ws.project.name),
        export=ExportInfo(sequence=sequence, exported_at=now, previous_sequence=changes.previous_sequence),
        files={**FILES, "spec": "FORMAT.md", "schemas": "schema/"},
        counts={role: len(fs) for role, fs in features.items()},
        types={"space": [t.value for t in SpaceType], "opening": [t.value for t in OpeningType]},
        sources=[
            SourceInfo(floor_id=fid, file=Path(f.source.path).name, sha256=f.source.sha256)
            for _, _, f, fid in ws.iter_floors() if f.source
        ],
        placements={
            b_id: PlacementInfo(**p.model_dump(), placed=real) for b_id, (p, real) in placements(ws).items()
        },
    )

    if isinstance(out_path, (str, Path)):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", manifest.model_dump_json(indent=2))
        for role, fs in features.items():
            z.writestr(FILES[role], json.dumps({"type": "FeatureCollection", "features": fs}, ensure_ascii=False))
        z.writestr(FILES["objects"], _objects_csv(ws, features))
        z.writestr(FILES["changes"], changes.model_dump_json(indent=2))
        z.writestr("FORMAT.md", format_spec())
        for name, schema in json_schemas().items():
            z.writestr(f"schema/{name}", json.dumps(schema, indent=2))

    if record:
        ws.exports.append(ExportRecord(sequence=sequence, exported_at=now, file=Path(out_path).name, objects=hashes))
    return manifest
