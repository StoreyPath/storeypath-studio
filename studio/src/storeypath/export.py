"""Writing the exchange package from a workspace."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import tempfile
import zipfile
from importlib.metadata import version
from pathlib import Path

import numpy as np
import shapely
from shapely.ops import transform as shapely_transform
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import polylabel, unary_union

from .assets import format_spec
from .bake import WORLD_DIR, bake_world
from .geometry import as_polygons
from .georef import Georeferencer
from .ids import child_id, is_item_id, make_id
from .catalogue import GRADES, Catalogue, default_catalogue
from .package import (
    FILES,
    OBJECTS_CSV_COLUMNS,
    Changes,
    ExportInfo,
    Generator,
    Manifest,
    MovedAway,
    PlacementInfo,
    ProjectInfo,
    Scope,
    SourceInfo,
    json_schemas,
)
from .levels import DEFAULT_PARAPET_M
from .types import OpeningType, SpaceType
from .workspace import ExportRecord, LastPackage, Placement, SitePosition, Workspace, utcnow

COORD_DECIMALS = 7  # ~1 cm
LOCAL_DECIMALS = 4  # metres, in a building's own frame: a tenth of a millimetre
# every building where its own frame is, for what is compared between exports (moving a
# building on the map changes none of its rooms, doors or items)
OWN_FRAME = Placement(lon=0.0, lat=0.0, x=0.0, y=0.0, bearing=0.0)

log = logging.getLogger(__name__)


class ExportError(Exception):
    pass


def _rounded(lonlat_geom) -> dict:
    out = shapely.transform(lonlat_geom, lambda c: np.round(c, COORD_DECIMALS))
    # two corners under a centimetre apart become one point: written once, not as a
    # zero-length edge (strict readers take it for a ring crossing itself)
    out = shapely.remove_repeated_points(out)
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


SITE_GAP_M = 10.0  # buildings laid out side by side on a site plan stand this far apart


def footprint(b) -> object | None:
    """A building's footprint in its own drawing metres: its floors' outlines."""
    outlines = [shape(f.outline) for f in b.floors if f.outline]
    return unary_union(outlines) if outlines else None


def site_positions(loc) -> dict[str, SitePosition]:
    """Where each building of a location stands on its site plan (by building code).
    A building given no position stands as its drawing places it: around the site's
    drawing origin once buildings were given positions, else around the middle of
    them all."""
    if loc.site_origin is not None:
        mx, my = loc.site_origin
    else:
        loose = [b for b in loc.buildings if b.site is None]
        shapes = [fp for b in loose if (fp := footprint(b)) is not None]
        middle = unary_union(shapes).centroid if shapes else None
        mx, my = (middle.x, middle.y) if middle is not None else (0.0, 0.0)
    out = {}
    for b in loc.buildings:
        if b.site is not None:
            out[b.code] = b.site
            continue
        fp = footprint(b)
        c = fp.centroid if fp is not None else None
        cx, cy = (c.x, c.y) if c is not None else (mx, my)
        out[b.code] = SitePosition(x=round(cx - mx, 3), y=round(cy - my, 3), rotation=0.0,
                                   pivot=(round(cx, 3), round(cy, 3)))
    return out


def settle(loc) -> None:
    """Every building of a location keeps the place it has on its site plan now (as
    drawn, until then): one added, moved or put in place of another moves no other,
    and one added later as drawn stands where its drawing puts it relative to them.
    No building moves (as server.Studio's own settling, when a building is moved)."""
    positions = site_positions(loc)
    if loc.site_origin is None:
        loose = next((b for b in loc.buildings if b.site is None), None)
        if loose is not None:
            p = positions[loose.code]
            loc.site_origin = (round(p.pivot[0] - p.x, 4), round(p.pivot[1] - p.y, 4))
    for b in loc.buildings:
        if b.site is None:
            b.site = positions[b.code]


def on_site(site: SitePosition, x: float, y: float) -> tuple[float, float]:
    """A point of a building (its drawing metres) on its site plan."""
    r = math.radians(site.rotation)
    dx, dy = x - site.pivot[0], y - site.pivot[1]
    return site.x + dx * math.cos(r) + dy * math.sin(r), site.y - dx * math.sin(r) + dy * math.cos(r)


def site_footprint(b, pos: SitePosition):
    """A building's footprint on its site plan (None before any floor is read)."""
    fp = footprint(b)
    return None if fp is None else shapely_transform(lambda x, y, z=None: _on_site_xy(pos, x, y), fp)


def _on_site_xy(pos: SitePosition, xs, ys):
    r = math.radians(pos.rotation)
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    dx, dy = xs - pos.pivot[0], ys - pos.pivot[1]
    return pos.x + dx * math.cos(r) + dy * math.sin(r), pos.y - dx * math.sin(r) + dy * math.cos(r)


def beside(loc, b, gap: float = SITE_GAP_M) -> SitePosition | None:
    """A position for a building on its site plan beside the others (to their right,
    ``gap`` metres off, level with them), as it is turned; None when it has no
    footprint or there are no others to stand beside."""
    positions = site_positions(loc)
    others = [site_footprint(o, positions[o.code]) for o in loc.buildings if o is not b]
    others = [g for g in others if g is not None]
    pos = positions[b.code]
    own = site_footprint(b, pos)
    if own is None or not others:
        return None
    x0, y0, x1, y1 = unary_union(others).bounds
    ox0, oy0, ox1, oy1 = own.bounds
    return SitePosition(x=round(pos.x + (x1 + gap - ox0), 3), y=round(pos.y + ((y0 + y1) / 2 - (oy0 + oy1) / 2), 3),
                        rotation=pos.rotation, pivot=pos.pivot)


def placements(ws: Workspace) -> dict[str, tuple[Placement, bool]]:
    """Each building's placement, and whether it is a real one. A building placed
    on the map by itself keeps its placement. Any other stands where the site plan
    has it (site_positions): on the map when its location is placed, else around
    0°N 0°E, where its shape and size are true and its position on earth is not."""
    out = {}
    for loc in ws.locations:
        site = loc.placement or Placement(lon=0.0, lat=0.0, x=0.0, y=0.0, bearing=0.0)
        positions = site_positions(loc)
        for b in loc.buildings:
            b_id = make_id(ws.id, loc.code, b.code)
            if b.placement is not None:
                out[b_id] = (b.placement, True)
                continue
            # Every building of the site is anchored at the site's centre (one projection
            # for them all, so they stand exactly where the site plan has them): the
            # drawing point there is the one the building's position and turn put there.
            pos = positions[b.code]
            r = math.radians(pos.rotation)
            ax = pos.pivot[0] - (pos.x * math.cos(r) - pos.y * math.sin(r))
            ay = pos.pivot[1] - (pos.x * math.sin(r) + pos.y * math.cos(r))
            out[b_id] = (Placement(lon=site.lon, lat=site.lat, x=round(ax, 4), y=round(ay, 4),
                                   bearing=round((site.bearing + pos.rotation) % 360, 6)), loc.placement is not None)
    return out


OUTDOOR = {SpaceType.TERRACE.value, SpaceType.BALCONY.value}


def open_to_the_sky(ws: Workspace, floor_id: str) -> set[str]:
    """The terraces and balconies that are open to the sky: those with no window of
    their own. A window in a terrace's own outer wall (one that joins it to nothing
    else) encloses it, as a glazed veranda; a room's window looking onto it does not."""
    objects = ws.floor_objects(floor_id)
    enclosed = {r.connects[0] for r in objects if r.kind == "opening" and r.type == "window" and len(r.connects) == 1}
    return {r.id for r in objects if r.kind == "space" and ws.effective(r)["type"] in OUTDOOR and r.id not in enclosed}


def _walls_and_parapets(walls, spaces, thickness: float | None):
    """A floor's walls, split into those that rise to the ceiling and the parapets. On
    a floor with a terrace or balcony open to the sky, a wall that encloses no room is
    low: the wall around a roof, and anything built onto it (a pier, a box for a
    pipe). A room's own walls stay full height, terrace or not beside them.
    ``spaces``: (shape, open to the sky)."""
    if walls is None or walls.is_empty or not any(sky for _, sky in spaces):
        return walls, None
    reach = 1.5 * max(thickness or 0.2, 0.1) + 0.05  # across a wall, to the space on its far side
    rooms = unary_union([s for s, sky in spaces if not sky]).buffer(reach)
    low = walls.difference(rooms)
    low = unary_union([p for p in as_polygons(low) if p.area >= 0.01])
    if low.is_empty:
        return walls, None
    return walls.difference(low), low


WALL_ELEVATION_M = 1.2  # a wall item's bottom above the floor, when its type does not say


def floor_units(ws: Workspace, f_id: str) -> list:
    """A floor's spaces and zones in use, with their shapes: where items stand."""
    return [(shape(r.geometry), r) for r in ws.floor_objects(f_id)
            if r.kind in ("space", "zone") and r.geometry and not ws.effective(r)["ignored"]]


def standing_in(it, units) -> tuple[str | None, str | None]:
    """The space, and the zone when the space is divided, an item's middle stands in."""
    middle = shapely.Point(it.x, it.y)
    zone = next((rec for geom, rec in units if rec.kind == "zone" and geom.covers(middle)), None)
    space = zone.parent if zone else next((rec.id for geom, rec in units if rec.kind == "space" and geom.covers(middle)), None)
    return space, zone.id if zone else None


def seating(ws: Workspace, cat: Catalogue, f_id: str, units=None) -> dict[str, dict]:
    """What the items standing in each space and zone of a floor say of it: how many
    people work there (its desks' workplaces) and the highest grade among its desks.
    A divided space counts what stands in its zones too."""
    units = floor_units(ws, f_id) if units is None else units
    out: dict[str, dict] = {}
    for it in ws.floor_items(f_id):
        t = cat.get(it.type)
        if t is None or not (t.workplaces or t.grade):
            continue
        for unit in standing_in(it, units):
            if unit is None:
                continue
            seats = out.setdefault(unit, {"workplaces": 0, "grade": None})
            seats["workplaces"] += t.workplaces
            if t.grade and (seats["grade"] is None or GRADES.index(t.grade) < GRADES.index(seats["grade"])):
                seats["grade"] = t.grade
    return out


def capacity_of(eff: dict, seats: dict | None) -> tuple[int | None, str | None]:
    """How many people a space or zone is meant to seat, and what says so: set in
    review, else the workplaces of the items standing in it, else not known."""
    if eff["capacity"] is not None:
        return eff["capacity"], "review"
    if seats and seats["workplaces"]:
        return seats["workplaces"], "items"
    return None, None


def _item_feature(it, t, g: Georeferencer, bearing: float, b_id: str, units) -> dict:
    """An item as a feature: where it stands in its building's own frame (what it is
    placed by), its footprint on the map (its width along its rotation, its front
    facing the plan's -y turned with it), where its front faces on earth, and the
    zone or space its middle stands in."""
    w, d = (t.width, t.depth) if t else (1.0, 0.6)
    r = math.radians(it.rotation)
    ux, uy = math.cos(r), math.sin(r)  # along its width
    fx, fy = math.sin(r), -math.cos(r)  # its front
    corners = [(it.x + sx * ux * w / 2 + sy * fx * d / 2, it.y + sx * uy * w / 2 + sy * fy * d / 2)
               for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    space, zone = standing_in(it, units)
    mount = t.mount if t else "floor"
    elevation = t.elevation if t and t.elevation is not None else (0.0 if mount == "floor" else WALL_ELEVATION_M if mount == "wall" else None)
    own = {f.key for f in t.fields if f.owner == "storeypath"} if t else set()
    return _feature(it.id, _rounded(g.geometry(Polygon(corners))), {
        "kind": "item", "type": it.type, "category": t.category if t else "furniture",
        "name": t.name_en if t else it.type, "floor_id": it.floor_id, "building_id": b_id,
        "space_id": space, "zone_id": zone,
        "local": {"x_m": round(it.x, LOCAL_DECIMALS), "y_m": round(it.y, LOCAL_DECIMALS),
                  "rotation_deg": round(it.rotation % 360, 2)},
        "display_point": _lonlat(g, (it.x, it.y)),
        "heading": round((bearing + math.degrees(math.atan2(fx, fy))) % 360, 2),
        "width_m": w, "depth_m": d, "height_m": t.height if t else 0.75, "mount": mount,
        "elevation_m": elevation, "values": {k: v for k, v in it.values.items() if k in own},
    })


def build_features(ws: Workspace, cat: Catalogue | None = None, *, own_frame: bool = False) -> dict[str, list[dict]]:
    """Every feature of the project, on the map; with ``own_frame``, each building
    where its own frame is instead (what export compares between exports)."""
    placed = placements(ws)
    if own_frame:
        placed = {b: (OWN_FRAME, real) for b, (_, real) in placed.items()}
    cat = cat or default_catalogue()
    out: dict[str, list[dict]] = {k: [] for k in ("location", "buildings", "floors", "spaces", "zones", "openings", "items")}
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
                sky = open_to_the_sky(ws, f_id)
                spaces = [(shape(r.geometry), r.id in sky) for r in ws.floor_objects(f_id) if r.kind == "space"]
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
                seats = seating(ws, cat, f_id)
                for r in sorted(ws.floor_objects(f_id), key=lambda r: r.id):
                    eff = ws.effective(r)
                    geom = shape(r.geometry)
                    capacity, capacity_from = capacity_of(eff, seats.get(r.id))
                    grade = (seats.get(r.id) or {}).get("grade")
                    if r.kind == "space":
                        out["spaces"].append(
                            _feature(
                                r.id,
                                _geo(geom, g),
                                {"kind": "space", "type": eff["type"], "name": eff["name"],
                                 "number": eff["number"], "drawing_label": r.label, "floor_id": f_id,
                                 "area_m2": round(geom.area, 2),
                                 "display_point": _lonlat(g, _label_point(geom)),
                                 "zones": list(r.zones), "outdoor": r.id in sky,
                                 "capacity": capacity, "capacity_from": capacity_from, "grade": grade,
                                 "hidden": eff["hidden"], "ignored": eff["ignored"]},
                            )
                        )
                    elif r.kind == "zone":
                        out["zones"].append(
                            _feature(
                                r.id,
                                _geo(geom, g),
                                {"kind": "zone", "type": eff["type"], "name": eff["name"],
                                 "number": eff["number"], "drawing_label": r.label, "space_id": r.parent, "floor_id": f_id,
                                 "area_m2": round(geom.area, 2),
                                 "display_point": _lonlat(g, _label_point(geom)),
                                 "capacity": capacity, "capacity_from": capacity_from, "grade": grade,
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
                                 "swings": [[_lonlat(g, p) for p in leaf] for leaf in r.swings] if r.swings else None,
                                 "sill_m": r.sill, "height_m": r.height,
                                 "hidden": eff["hidden"], "ignored": eff["ignored"]},
                            )
                        )
            for f in sorted(b.floors, key=lambda f: f.ordinal):  # the furniture and equipment on each floor
                f_id = child_id(b_id, f.code)
                units = floor_units(ws, f_id)
                for it in sorted(ws.floor_items(f_id), key=lambda i: i.id):
                    out["items"].append(_item_feature(it, cat.get(it.type), g, placed[b_id][0].bearing, b_id, units))
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
    for role in ("location", "buildings", "floors", "spaces", "zones", "openings", "items"):
        for f in features[role]:
            p = f["properties"]
            # an item's ID says nothing of where it is: its floor does
            segs = (p["floor_id"] if role == "items" else f["id"]).split("-")
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
                "space_id": p.get("space_id") or "",
                "drawing_label": p.get("drawing_label") or "",
            })
    return buf.getvalue()


def in_buildings(buildings: list[str] | None):
    """Whether an ID is one of ``buildings`` or of what is in them (all IDs, without)."""
    if buildings is None:
        return lambda i: True
    prefixes = tuple(b + "-" for b in buildings)
    return lambda i: i in buildings or i.startswith(prefixes)


def compared(ws: Workspace, cat: Catalogue | None, held=lambda role, f: True) -> dict[str, str]:
    """What is compared between exports, by ID: each feature (``held`` says which) as
    it is in its building's own frame, so moving a building on the map changes none
    of its rooms, doors or items; a building, with its place on the map; a location,
    by what it says of itself (its outline is its buildings', compared themselves)."""
    placed = placements(ws)
    out = {}
    for role, fs in build_features(ws, cat, own_frame=True).items():
        for f in fs:
            if not held(role, f):
                continue
            if role == "buildings":
                f = {**f, "placement": placed[f["id"]][0].model_dump()}
            elif role == "location":
                f = {**f, "geometry": None, "properties": {k: v for k, v in f["properties"].items() if k != "display_point"}}
            out[f["id"]] = _hash(f)
    return out


def building_ids(ws: Workspace) -> list[str]:
    return [make_id(ws.id, loc.code, b.code) for loc in ws.locations for b in loc.buildings]


def item_number(item_id: str) -> int:
    """An item's own number (``K7Q2XM-I000142`` → 142)."""
    return int(item_id.split("-")[1][1:])


def next_item_number(ws: Workspace) -> int:
    """The number the project gives its next item: after every one it has given."""
    return max([ws.next_item_seq, *(item_number(i) + 1 for i in ws.items if is_item_id(i))])


def _in_building(i: str, b_id: str) -> bool:
    return i == b_id or i.startswith(b_id + "-")


def last_packages(ws: Workspace) -> tuple[dict[str, LastPackage], set[str]]:
    """Each building as the last package that held it had it (building ID -> it), and
    every item ID a package of the project has held: what the next export of a building
    lists its changes against. From a record of a Studio before these were kept, they
    are worked out from what it kept: the whole project as last exported, and the
    building each item was in then."""
    if not ws.exports:
        return {}, set()
    last = ws.exports[-1]
    if last.held is not None:
        return {b: p.model_copy(deep=True) for b, p in last.held.items()}, set(last.items_held or [])
    held = {}
    nowhere = {i for i, it in ws.items.items() if it.status == "retired" and not it.floor_id}  # from a package
    for b in building_ids(ws):
        seq = next((r.sequence for r in reversed(ws.exports) if r.buildings is None or b in r.buildings), None)
        if seq is None:
            continue
        location = b.rsplit("-", 1)[0]
        objects = {i: h for i, h in last.objects.items() if i == location or _in_building(i, b)}
        objects.update({i: last.objects[i] for i, at in last.places.items() if at == b and i in last.objects})
        retired = {i for i, r in ws.objects.items() if r.status == "retired" and _in_building(i, b)} | nowhere
        retired |= {i for i, it in ws.items.items() if it.status == "retired" and _in_building(it.floor_id, b)}
        held[b] = LastPackage(sequence=seq, objects=objects, retired=sorted(retired))
    return held, {i for i in last.objects if is_item_id(i)} | set(last.places)


def export_package(ws: Workspace, out_path, *, building: str | None = None, record: bool = True,
                   bake: bool = True, say=None, catalogue: Catalogue | None = None) -> Manifest:
    """Write the package of one building (its ID; may be left out when the project
    has one) to ``out_path`` (a path, or a binary file object): the building, its
    floors and what is on them, and its location; its manifest's ``scope`` names it.
    With ``record`` the export is entered in the workspace, so the next one lists
    what changed since; without it the workspace is left as it was.

    What changed is listed for that building alone, against the last package that
    held it (``previous_sequence``: that package's export), compared in its own frame:
    moving it on the map changes none of its rooms, doors or items (only the
    building is changed). An item that package held, carried since to another
    building, is listed as moved away; one taken away since, wherever it was then, as
    retired. One carried into the building that a package of the project held before
    is changed, not added.

    With ``bake`` its floors are pre-built in 3D (``world/``, bake.py) when Node.js is
    here; when it is not, the package is written without them. ``say`` is told
    which (else it is logged)."""
    known = building_ids(ws)
    if building is None:
        if len(known) != 1:
            raise ExportError(f"this project has {len(known)} buildings: choose the one to export "
                              "(a package holds one building)")
        building = known[0]
    if building not in known:
        raise ExportError(f"no building {building} in this project")
    return _package(ws, out_path, [building], record=record, bake=bake, say=say, catalogue=catalogue)


def preview_package(ws: Workspace, out_path, *, buildings: list[str] | None = None,
                    catalogue: Catalogue | None = None) -> Manifest:
    """The project as it is now, for Studio's own viewers: some buildings (all of them
    without ``buildings``), not entered as an export and not pre-built. It is never
    sent anywhere: what is sent is a building's package (export_package)."""
    known = building_ids(ws)
    buildings = known if buildings is None else buildings
    if unknown := sorted(set(buildings) - set(known)):
        raise ExportError(f"no building {', '.join(unknown)} in this project")
    if not buildings:
        raise ExportError("no building to show")
    return _package(ws, out_path, buildings, record=False, bake=False, catalogue=catalogue)


def _package(ws: Workspace, out_path, buildings: list[str], *, record: bool, bake: bool, say=None,
             catalogue: Catalogue | None = None) -> Manifest:
    cat = catalogue or default_catalogue()
    buildings = sorted(set(buildings))
    locations = {b.rsplit("-", 1)[0] for b in buildings}
    scoped = in_buildings(buildings)

    def held(role: str, f: dict) -> bool:
        if role == "location":
            return f["id"] in locations
        return scoped(f["properties"]["floor_id"] if role == "items" else f["id"])  # an item: where it is

    everything = build_features(ws, cat)
    features = {role: [f for f in fs if held(role, f)] for role, fs in everything.items()}
    placed = placements(ws)
    hashes = compared(ws, cat, held)

    # each building is compared with the last package that held it, whatever packages
    # of the project's other buildings came between
    held_before, known = last_packages(ws)
    sequence = (ws.exports[-1].sequence + 1) if ws.exports else 1
    last = {b: held_before[b] for b in buildings if b in held_before}
    prev_hashes: dict[str, str] = {}
    for p in last.values():
        prev_hashes.update(p.objects)
    places = {f["id"]: f["properties"]["building_id"] for f in everything["items"]}  # every item in use, now
    here = {f["id"] for f in features["items"]}
    gone = sorted(i for i in prev_hashes if i not in hashes)
    moved_away = [MovedAway(id=i, building_id=places[i]) for i in gone if is_item_id(i) and i in places]

    def taken_away(i: str) -> bool:  # an object of the building, or an item retired (wherever it was then)
        if is_item_id(i):
            return i not in ws.items or ws.items[i].status == "retired"
        return scoped(i)  # a location stays

    def active(i: str) -> bool:
        r = ws.items.get(i) if is_item_id(i) else ws.objects.get(i)
        return r is not None and r.status == "active"

    retired = [i for i in gone if taken_away(i)]
    # every ID the building has retired: those it listed before, and those retired since,
    # with an item none of the project's packages held taken away while it stood here
    ever = set(retired).union(*(p.retired for p in last.values()))
    ever |= {i for i, r in ws.objects.items() if r.status == "retired" and scoped(i)}
    ever |= {i for i, it in ws.items.items() if it.status == "retired" and scoped(it.floor_id) and i not in known}
    all_retired = sorted(i for i in ever if i not in hashes and not active(i))  # an item brought back is not
    changes = Changes(
        sequence=sequence,
        previous_sequence=max((p.sequence for p in last.values()), default=None),
        # an item a package of the project held before, carried into the building, is not new
        added=sorted(i for i in hashes if i not in prev_hashes and not (is_item_id(i) and i in known)),
        changed=sorted(i for i in hashes if (prev_hashes[i] != hashes[i] if i in prev_hashes
                                             else is_item_id(i) and i in known)),
        retired=retired,
        all_retired=all_retired,
        moved_away=moved_away,
    )

    now = utcnow()
    manifest = Manifest(
        generator=Generator(name="storeypath", version=version("storeypath")),
        project=ProjectInfo(id=ws.id, name=ws.project.name),
        export=ExportInfo(sequence=sequence, exported_at=now, previous_sequence=changes.previous_sequence,
                          next_item=next_item_number(ws)),
        files={**FILES, "spec": "FORMAT.md", "schemas": "schema/"},
        counts={role: len(fs) for role, fs in features.items()},
        types={"space": [t.value for t in SpaceType], "zone": [t.value for t in SpaceType],
               "opening": [t.value for t in OpeningType]},
        sources=[
            SourceInfo(floor_id=fid, file=Path(f.source.path).name, sha256=f.source.sha256)
            for _, _, f, fid in ws.iter_floors() if f.source and scoped(fid)
        ],
        placements={
            b_id: PlacementInfo(**p.model_dump(), placed=real) for b_id, (p, real) in placed.items() if scoped(b_id)
        },
        scope=Scope(buildings=buildings),
    )

    world: dict[str, bytes] = {}
    if bake:  # the package as it is so far, for the baker to read
        with tempfile.TemporaryDirectory(prefix="storeypath-export-") as tmp:
            plain = Path(tmp) / "package.storeypath"
            _write(plain, ws, manifest, features, changes, {}, cat)
            world, why = bake_world(plain)
        if world:
            manifest.files["world"] = WORLD_DIR
        (say or log.info)(f"3D pre-built: {len(world)} floor(s)" if world else f"3D not pre-built: {why}")

    if isinstance(out_path, (str, Path)):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    _write(out_path, ws, manifest, features, changes, world, cat)

    if record:
        held = dict(held_before)
        for b in buildings:  # (one: a package holds one building)
            location = b.rsplit("-", 1)[0]
            held[b] = LastPackage(
                sequence=sequence,
                objects={i: h for i, h in hashes.items()
                         if i == location or _in_building(i, b) or places.get(i) == b},
                retired=[i for i in all_retired if _in_building(i, b) or is_item_id(i)])
        ws.exports.append(ExportRecord(
            sequence=sequence, exported_at=now, file=Path(out_path).name, buildings=buildings,
            held=held, items_held=sorted(known | here)))
    return manifest


def _write(out, ws: Workspace, manifest: Manifest, features: dict[str, list[dict]], changes: Changes,
           extra: dict, cat: Catalogue) -> None:
    """The package's files, and ``extra`` (name → bytes or a path), into a ZIP. Every
    file is made before the ZIP is opened: a value JSON cannot hold (NaN, infinity)
    is refused, and nothing is written."""

    def strict(name: str, value, **kw) -> str:
        try:
            return json.dumps(value, allow_nan=False, **kw)
        except ValueError:
            raise ExportError(f"{name}: a value is not a number (NaN or infinite): not written, "
                              "as other systems could not read it") from None

    files = {"manifest.json": strict("manifest.json", manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)}
    for role, fs in features.items():
        files[FILES[role]] = strict(FILES[role], {"type": "FeatureCollection", "features": fs}, ensure_ascii=False)
    files[FILES["objects"]] = _objects_csv(ws, features)
    files[FILES["changes"]] = strict(FILES["changes"], changes.model_dump(mode="json"), ensure_ascii=False, indent=2)
    files[FILES["catalogue"]] = strict(FILES["catalogue"], cat.model_dump(mode="json"), ensure_ascii=False, indent=1)
    files["FORMAT.md"] = format_spec()
    for name, schema in json_schemas().items():
        files[f"schema/{name}"] = json.dumps(schema, indent=2)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
        for name, data in extra.items():
            if isinstance(data, Path):
                z.write(data, name)
            else:
                z.writestr(name, data)
