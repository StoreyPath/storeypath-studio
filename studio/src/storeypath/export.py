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
from .ids import LAND_CODE, child_id, is_item_id, is_land, make_id
from .catalogue import GRADES, MODELS, Catalogue, default_catalogue, shape_of
from .package import (
    DRAWINGS,
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
from .navigation import NAVIGATION_FILE, build_site_network, navigation_file
from . import finishes
from .stacks import stack_of
from .types import BUILDING_LEVELS, OpeningType, SpaceType
from .workspace import (ExportRecord, LastPackage, Placement, SitePosition, Workspace, floor_order, site_bearing,
                        utcnow)

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
    try:
        out = shapely.remove_repeated_points(out)
    except shapely.errors.GEOSException:  # a ring under a centimetre across: not one once rounded
        out = _without_collapsed(out)
    return json.loads(json.dumps(mapping(out)))  # tuples → lists


def _without_collapsed(geom):
    """Polygons rounded, their repeated points written once and the rings that are then
    under three points (a hole or a part under a centimetre across) left out."""
    def ring(coords):
        pts = []
        for p in coords:
            if not pts or p != pts[-1]:
                pts.append(p)
        return pts if len(set(pts)) >= 3 else None

    parts = []
    for p in as_polygons(geom):
        shell = ring(p.exterior.coords)
        if shell is not None:
            parts.append(Polygon(shell, [h for h in (ring(i.coords) for i in p.interiors) if h is not None]))
    if not parts:
        return geom
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


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
        site = site_placement(loc)
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


def site_placement(loc) -> Placement:
    """Where a location's site is on the map: its centre (the origin of the site's frame,
    where its layout is drawn: format 0.12) at its placement's longitude and latitude, its
    up at its bearing; not on the map, around 0°N 0°E, its up at its north (site_bearing)."""
    if loc.placement is not None:
        return Placement(lon=loc.placement.lon, lat=loc.placement.lat, x=0.0, y=0.0, bearing=loc.placement.bearing)
    return Placement(lon=0.0, lat=0.0, x=0.0, y=0.0, bearing=site_bearing(loc))


OUTDOOR = {SpaceType.TERRACE.value, SpaceType.BALCONY.value, SpaceType.ROOF.value}


def open_to_the_sky(ws: Workspace, floor_id: str) -> set[str]:
    """The terraces, balconies and roofs that are open to the sky: those with no window
    of their own. A window in a terrace's own outer wall (one that joins it to nothing
    else) encloses it, as a glazed veranda; a room's window looking onto it does not."""
    objects = ws.floor_objects(floor_id)
    enclosed = {r.connects[0] for r in objects if r.kind == "opening" and r.type == "window" and len(r.connects) == 1}
    return {r.id for r in objects if r.kind == "space" and ws.effective(r)["type"] in OUTDOOR and r.id not in enclosed}


def courtyards(ws: Workspace, floor_id: str) -> list:
    """A floor's courtyards and light wells: its spaces set aside as no room that are open
    to the sky, as read (extract.find_courtyards) or as a person says (Override.sky). Each
    is a hole in the floor's outline (floor_outline), outdoors: the walking network does not
    cross it, and a door onto it leads outside. A location's layout has none (its land is
    all open to the sky)."""
    if is_land(floor_id):
        return []
    return [r for r in ws.floor_objects(floor_id) if r.kind == "space" and r.geometry and ws.effective(r)["sky"]]


def floor_outline(ws: Workspace, floor_id: str, floor=None):
    """A floor's outline as it is used (a package's floor, the building's footprint, a
    sample's): as read, with a hole where each of its courtyards and light wells is
    (courtyards); None for a floor with none. Rooms are never cut: a courtyard lies
    between them."""
    f = floor if floor is not None else ws.floor(floor_id)
    if not f.outline:
        return None
    outline = shape(f.outline)
    yards = courtyards(ws, floor_id)
    if not yards:
        return outline
    holes = unary_union([shape(r.geometry) for r in yards])
    try:
        cut = outline.difference(holes)
    except shapely.errors.GEOSException:  # (an outline that is not valid as it is kept)
        cut = shapely.make_valid(outline).difference(shapely.make_valid(holes))
    parts = [p for p in as_polygons(cut) if not p.is_empty]
    return (parts[0] if len(parts) == 1 else MultiPolygon(parts)) if parts else outline


def _walls_and_parapets(walls, spaces, thickness: float | None, yards=()):
    """A floor's walls, split into those that rise to the ceiling and the parapets. On
    a floor with a terrace, balcony or roof open to the sky, a wall that encloses no room
    is low: the wall around a roof, and anything built onto it (a pier, a box for a
    pipe). A room's own walls stay full height, terrace or not beside them. In a
    courtyard or light well (``yards``: their shapes, not among ``spaces``) a wall that
    encloses no room is low as well (a garden wall, a planter); on a floor with no
    terrace, balcony or roof, only there. ``spaces``: (shape, open to the sky)."""
    decks = any(sky for _, sky in spaces)
    if walls is None or walls.is_empty or not (decks or yards):
        return walls, None
    reach = 1.5 * max(thickness or 0.2, 0.1) + 0.05  # across a wall, to the space on its far side
    rooms = unary_union([s for s, sky in spaces if not sky]).buffer(reach)
    low = walls.difference(rooms)
    if not decks:  # (courtyards alone: the walls standing in them)
        low = low.intersection(unary_union(list(yards)))
    low = unary_union([p for p in as_polygons(low) if p.area >= 0.01])
    if low.is_empty:
        return walls, None
    return walls.difference(low), low


WALL_ELEVATION_M = 1.2  # a wall item's bottom above the floor, when its type does not say


class Units(list):
    """A floor's spaces and zones with their shapes, (shape, record) each, that finds
    those a point is in through a tree of their shapes (made the first time it is
    asked): a floor of a thousand rooms and a thousand desks in a second's fraction,
    not half a minute."""

    _tree = None

    def at(self, point) -> list:
        """Those whose shape covers ``point``, in their order."""
        if self._tree is None:
            self._tree = shapely.STRtree([geom for geom, _ in self])
        return [self[i] for i in sorted(self._tree.query(point, predicate="covered_by"))]


def floor_units(ws: Workspace, f_id: str) -> Units:
    """A floor's spaces and zones in use, with their shapes: where items stand."""
    return Units((shape(r.geometry), r) for r in ws.floor_objects(f_id)
                 if r.kind in ("space", "zone") and r.geometry and not ws.effective(r)["ignored"])


def standing_in(it, units) -> tuple[str | None, str | None]:
    """The space, and the zone when the space is divided, an item's middle stands in."""
    middle = shapely.Point(it.x, it.y)
    near = units.at(middle) if isinstance(units, Units) else units
    zone = next((rec for geom, rec in near if rec.kind == "zone" and geom.covers(middle)), None)
    space = zone.parent if zone else next((rec.id for geom, rec in near if rec.kind == "space" and geom.covers(middle)), None)
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


def walked_doors(ws: Workspace, floor_id: str) -> list:
    """The doors and openings of a floor, the ways through that a person walks (never a
    window), but those deleted: what an item that serves a door may be linked to."""
    return [r for r in ws.floor_objects(floor_id) if r.kind == "opening"
            and ws.effective(r)["type"] != "window" and not ws.effective(r)["ignored"]]


def _item_feature(it, t, g: Georeferencer, bearing: float, b_id: str, units, doors: set[str] = frozenset()) -> dict:
    """An item as a feature: where it stands in its building's own frame (what it is
    placed by), its footprint on the map (its width along its rotation, its front
    facing the plan's -y turned with it), where its front faces on earth, and the
    zone or space its middle stands in; the door it serves, while that is one of
    ``doors`` (its floor's: walked_doors); a camera's view, its own else its type's."""
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
    camera = t is not None and shape_of(t) == "camera"
    view = {key: (float(it.values[key]) if key in own and isinstance(it.values.get(key), (int, float)) else getattr(t, key))
            if camera else None for key in ("view_deg", "range_m")}
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
        "group": it.group, "door_id": it.door_id if it.door_id in doors and t is not None and t.serves_door else None,
        **view,
    })


def build_features(ws: Workspace, cat: Catalogue | None = None, *, own_frame: bool = False,
                   only: set[str] | None = None) -> dict[str, list[dict]]:
    """Every feature of the project, on the map; with ``own_frame``, each building
    where its own frame is instead (what export compares between exports). With
    ``only`` (building IDs), the floors, spaces, zones, openings and items of those
    buildings alone: every building and the location are as without it (the location's
    outline is all its buildings'), the rest is not worked out (a small building's
    preview does not pay for a big one beside it)."""
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
            whole = only is None or b_id in only
            # each lift's and stairs' key, the same on every floor it serves
            stack = stack_of(ws, b_id) if whole else {}
            outlines = []
            for f in sorted(b.floors, key=floor_order):  # (lowest first: a mezzanine after its floor)
                f_id = child_id(b_id, f.code)
                outline = floor_outline(ws, f_id, f)  # (open where its courtyards and light wells are)
                if outline is not None:
                    outlines.append(outline)
                if not whole:
                    continue
                sky = open_to_the_sky(ws, f_id)
                yards = {r.id: shape(r.geometry) for r in courtyards(ws, f_id)}
                spaces = [(shape(r.geometry), r.id in sky) for r in ws.floor_objects(f_id)
                          if r.kind == "space" and r.id not in yards]
                walls, parapets = _walls_and_parapets(shape(f.walls) if f.walls else None, spaces, f.wall_thickness,
                                                      list(yards.values()))
                out["floors"].append(
                    _feature(
                        f_id,
                        _geo(outline, g) if outline is not None else None,
                        {"kind": "floor", "code": f.code, "name": f.name, "building_id": b_id,
                         "ordinal": f.ordinal, "elevation": f.elevation, "height": f.height,
                         # what level it is (0.11); a mezzanine's floor it stands in, and how far above it
                         "type": f.type.value,
                         "host_floor_id": child_id(b_id, f.host) if f.mezzanine else None,
                         "above_host_m": f.above_host if f.mezzanine else None,
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
                                 "zones": list(r.zones), "outdoor": r.id in sky or r.id in yards,
                                 "capacity": capacity, "capacity_from": capacity_from, "grade": grade,
                                 "stack": stack.get(r.id),
                                 "floor_finish": finishes.known(eff["floor_finish"], "floor"),
                                 "wall_finish": finishes.known(eff["wall_finish"], "wall"),
                                 # walls finished otherwise one by one (0.9.3): lines in its own frame
                                 "wall_finishes": [{"line": w["line"], "finish": w["finish"]} for w in eff["wall_finishes"]
                                                   if finishes.known(w["finish"], "wall")] or None,
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
                                 "floor_finish": finishes.known(eff["floor_finish"], "floor"),
                                 "hidden": eff["hidden"], "ignored": eff["ignored"]},
                            )
                        )
                    else:
                        out["openings"].append(
                            _feature(
                                r.id,
                                _geo(geom, g),
                                {"kind": "opening", "type": r.type, "floor_id": f_id,
                                 # (onto a courtyard or light well: outside)
                                 "connects": r.connects, "exterior": sum(c not in yards for c in r.connects) == 1,
                                 "width_m": r.width,
                                 "span": [_lonlat(g, p) for p in r.span] if r.span else None,
                                 "swings": [[_lonlat(g, p) for p in leaf] for leaf in r.swings] if r.swings else None,
                                 "sill_m": r.sill, "height_m": r.height,
                                 "hidden": eff["hidden"], "ignored": eff["ignored"]},
                            )
                        )
            # the furniture and equipment on each floor
            for f in sorted(b.floors, key=floor_order) if whole else ():
                f_id = child_id(b_id, f.code)
                units = floor_units(ws, f_id)
                doors = {r.id for r in walked_doors(ws, f_id)}
                for it in sorted(ws.floor_items(f_id), key=lambda i: i.id):
                    out["items"].append(_item_feature(it, cat.get(it.type), g, placed[b_id][0].bearing, b_id, units, doors))
            footprint = None
            if outlines and g is not None:  # (its floors' outlines' holes and parts of no size left out)
                footprint = g.geometry(_without_slivers(unary_union(outlines)))
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


def north_said(loc) -> dict:
    """How a location's site's bearing is known (format 0.12, its layout's ``north``): the
    bearing its plan's up faces, and from what: the map (its placement), the drawing (its
    site plan's north arrow), a person; null: not known (up is north)."""
    if loc.placement is not None:
        return {"bearing": round(loc.placement.bearing % 360, 6), "from": "map"}
    if loc.north is not None:
        return {"bearing": round(loc.north.bearing % 360, 6), "from": loc.north.source}
    return {"bearing": 0.0, "from": None}


def footprint_of(ws: Workspace, r) -> str | None:
    """The building a footprint is, in a package: as a person said, else the one placed
    on it; None for none."""
    from .ids import parse_id

    o = ws.overrides.get(r.id)
    if o is not None and o.building is not None:
        return o.building or None
    loc = ws.location(parse_id(r.id).prefix("location"))
    return next((make_id(ws.id, loc.code, b.code) for b in loc.buildings
                 if b.site is not None and b.site.footprint == r.id), None)


def site_frame(b) -> dict | None:
    """Where a building's own frame lies on its location's site (format 0.12): the origin
    of its frame in the site's frame, and its turn (degrees clockwise); None when it is on
    the map by itself."""
    from .layout import site_frame_of

    if b.placement is not None or b.site is None:
        return None
    x, y, r = site_frame_of(b)
    return {"x_m": x, "y_m": y, "rotation_deg": r}


def layout_features(ws: Workspace, cat: Catalogue | None = None, *, own_frame: bool = False,
                    only: set[str] | None = None) -> dict[str, list[dict]]:
    """Every location's layout (format 0.12) as features: the layout itself (its boundary
    its geometry), its footprints, its areas (spaces), its gates (openings) and the items on
    it, on the map (its site's placement: the layout is drawn in the site's frame); with
    ``own_frame``, where the site's frame is (what export compares). ``only``: the lands
    (PROJECT-LOCATION-LAND) whose layouts are worked out."""
    cat = cat or default_catalogue()
    out: dict[str, list[dict]] = {k: [] for k in ("layout", "footprints", "spaces", "openings", "items")}
    for loc, land, f, f_id in ws.iter_layouts():
        land_id = f_id.rsplit("-", 1)[0]
        if only is not None and land_id not in only:
            continue
        loc_id = make_id(ws.id, loc.code)
        placed = OWN_FRAME if own_frame else site_placement(loc)
        g = Georeferencer(placed)
        outline = shape(f.outline) if f.outline else None
        walls = shape(f.walls) if f.walls else None
        out["layout"].append(_feature(f_id, _geo(outline, g) if outline is not None else None, {
            "kind": "layout", "code": f.code, "name": f.name, "location_id": loc_id,
            "area_m2": round(outline.area, 2) if outline is not None else None,
            "display_point": _lonlat(g, _label_point(outline)) if outline is not None else None,
            "walls": _geo(walls, g) if walls is not None else None,
            "wall_thickness_m": f.wall_thickness, "wall_height_m": f.height,
            "north": north_said(loc)}))
        positions = {make_id(ws.id, loc.code, b.code): b for b in loc.buildings}
        for r in sorted(ws.floor_objects(f_id), key=lambda r: r.id):
            eff = ws.effective(r)
            geom = shape(r.geometry)
            if r.kind == "space":
                out["spaces"].append(_feature(r.id, _geo(geom, g), {
                    "kind": "space", "type": eff["type"], "name": eff["name"], "number": eff["number"],
                    "drawing_label": r.label, "floor_id": f_id, "area_m2": round(geom.area, 2),
                    "display_point": _lonlat(g, _label_point(geom)), "zones": [], "outdoor": True,
                    "capacity": eff["capacity"], "capacity_from": "review" if eff["capacity"] is not None else None,
                    "grade": None, "stack": None, "floor_finish": None, "wall_finish": None, "wall_finishes": None,
                    "hidden": eff["hidden"], "ignored": eff["ignored"]}))
            elif r.kind == "opening":
                out["openings"].append(_feature(r.id, _geo(geom, g), {
                    "kind": "opening", "type": eff["type"] if eff["type"] in {t.value for t in OpeningType} else r.type,
                    "floor_id": f_id, "connects": r.connects,
                    "exterior": len(r.connects) < 2 or any(
                        ws.effective(ws.objects[c])["type"] == SpaceType.ROAD.value for c in r.connects if c in ws.objects),
                    "width_m": r.width, "span": [_lonlat(g, p) for p in r.span] if r.span else None,
                    "swings": None, "sill_m": None, "height_m": r.height,
                    "hidden": eff["hidden"], "ignored": eff["ignored"]}))
            elif r.kind == "footprint":
                b_id = footprint_of(ws, r)
                b = positions.get(b_id)
                out["footprints"].append(_feature(r.id, _geo(geom, g), {
                    "kind": "footprint", "layout_id": f_id, "building_id": b_id, "name": r.name,
                    "drawing_label": r.label, "area_m2": round(geom.area, 2),
                    "display_point": _lonlat(g, _label_point(geom)),
                    "site": site_frame(b) if b is not None else None,
                    "hidden": eff["hidden"], "ignored": eff["ignored"]}))
        units = floor_units(ws, f_id)
        doors = {r.id for r in walked_doors(ws, f_id)}
        for it in sorted(ws.floor_items(f_id), key=lambda i: i.id):
            out["items"].append(_item_feature(it, cat.get(it.type), g, placed.bearing, None, units, doors))
    return out


def _round_pt(p) -> list[float]:
    return [round(p.x, COORD_DECIMALS), round(p.y, COORD_DECIMALS)]


def _objects_csv(ws: Workspace, features: dict[str, list[dict]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=OBJECTS_CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerow({"id": ws.id, "kind": "project", "name": ws.project.name, "project_id": ws.id})
    ordinals = {f["id"]: f["properties"]["ordinal"] for f in features["floors"]}
    for role in ("location", "buildings", "floors", "layout", "spaces", "zones", "openings", "footprints", "items"):
        for f in features.get(role, []):
            p = f["properties"]
            # an item's ID says nothing of where it is: its floor does
            segs = (p["floor_id"] if role == "items" else f["id"]).split("-")
            ids = {lvl: "-".join(segs[:n]) for n, lvl in
                   ((2, "location_id"), (3, "building_id"), (4, "floor_id")) if len(segs) >= n}
            if len(segs) >= 3 and segs[2] == LAND_CODE:  # (a layout's: on the land, in no building; 0.12)
                ids.pop("building_id", None)
                if role == "layout":
                    ids.pop("floor_id", None)
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


def compared(ws: Workspace, cat: Catalogue | None, held=lambda role, f: True,
             only: set[str] | None = None) -> dict[str, str]:
    """What is compared between exports, by ID: each feature (``held`` says which) as
    it is in its building's own frame, so moving a building on the map changes none
    of its rooms, doors or items; a building, with its place on the map; a location,
    by what it says of itself (its outline is its buildings', compared themselves).
    ``only``: as build_features."""
    placed = placements(ws)
    out = {}
    features = build_features(ws, cat, own_frame=True, only=only)
    for role, fs in layout_features(ws, cat, own_frame=True, only=only).items():
        features[role] = features.get(role, []) + fs
    for role, fs in features.items():
        for f in fs:
            if not held(role, f):
                continue
            if role == "buildings":
                f = {**f, "placement": placed[f["id"]][0].model_dump()}
            elif role == "layout":  # (the site on the map, as a building is compared with its placement)
                loc = ws.location(f["properties"]["location_id"])
                f = {**f, "placement": site_placement(loc).model_dump()}
            elif role == "location":
                said = {k: v for k, v in f["properties"].items() if k != "display_point"}
                f = {**f, "geometry": None, "properties": said}
            out[f["id"]] = _hash(f)
    return out


def building_ids(ws: Workspace) -> list[str]:
    return [make_id(ws.id, loc.code, b.code) for loc in ws.locations for b in loc.buildings]


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
    return held, {i for i in last.objects if i in ws.items or is_item_id(i)} | set(last.places)


ITEM_TYPES = ("used", "all")  # what a package's catalogue.json holds: the types its items use, or every one


def export_package(ws: Workspace, out_path, *, building: str | None = None, record: bool = True,
                   bake: bool = True, say=None, catalogue: Catalogue | None = None,
                   item_types: str = "used", models=None, drawings=None) -> Manifest:
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
    which (else it is logged).

    Its catalogue.json holds the item types its items use, each once (``item_types``
    "used"), or the whole ``catalogue`` ("all": to hand another system every type, for
    it to choose which to take); with them the 3D model of each that has one
    (``models``: a type's code → its file, or None; format 0.9.2).

    With ``drawings`` it carries each floor's drawing too (format 0.11): its part of
    its drawing as drawn, every layer and text, in the building's own frame
    (``drawings/<floor-id>.view``, the manifest's source of the floor naming it).
    ``drawings(floors)`` makes them: given [(floor ID, its SourceDrawing)], the view of
    each, bytes (None: not to be had); FloorViews makes them from files."""
    if item_types not in ITEM_TYPES:
        raise ExportError(f"item types: {' or '.join(ITEM_TYPES)}, not {str(item_types)[:20]!r}")
    known = building_ids(ws)
    if building is None:
        if len(known) != 1:
            raise ExportError(f"this project has {len(known)} buildings: choose the one to export "
                              "(a package holds one building)")
        building = known[0]
    if building not in known:
        raise ExportError(f"no building {building} in this project")
    return _package(ws, out_path, [building], record=record, bake=bake, say=say, catalogue=catalogue,
                    item_types=item_types, models=models, drawings=drawings)


def land_ids(ws: Workspace) -> list[str]:
    """The lands of the project's locations that have a layout (format 0.12): PROJECT-LOCATION-LAND."""
    return [make_id(ws.id, loc.code, LAND_CODE) for loc in ws.locations if loc.layout is not None and loc.layout.floors]


def export_layout_package(ws: Workspace, out_path, layout: str, *, record: bool = True, say=None,
                          catalogue: Catalogue | None = None, item_types: str = "used", models=None,
                          drawings=None) -> Manifest:
    """Write the package of a location's layout (format 0.12; ``layout``: its ID, or its
    land's), a package of its own as a building's is: the layout, its footprints, areas,
    gates and the items on it, and its location; its manifest's ``scope`` names it. What
    changed is listed against the last package that held it, in the site's frame. With
    ``record`` the export is entered in the workspace (as export_package)."""
    land = layout if layout.count("-") == 2 else layout.rsplit("-", 1)[0]
    if item_types not in ITEM_TYPES:
        raise ExportError(f"item types: {' or '.join(ITEM_TYPES)}, not {str(item_types)[:20]!r}")
    if land not in land_ids(ws):
        raise ExportError(f"no layout {layout} in this project")
    return _package(ws, out_path, [land], record=record, bake=False, say=say, catalogue=catalogue,
                    item_types=item_types, models=models, drawings=drawings)


def preview_package(ws: Workspace, out_path, *, buildings: list[str] | None = None,
                    catalogue: Catalogue | None = None, models=None) -> Manifest:
    """The project as it is now, for Studio's own viewers: some buildings (all of them
    without ``buildings``), not entered as an export and not pre-built, with the whole
    catalogue (a type no item has yet may be placed in them); or a location's layout (its
    land's ID). It is never sent anywhere: what is sent is a building's package
    (export_package), or a layout's (export_layout_package)."""
    if buildings and len(buildings) == 1 and buildings[0] in land_ids(ws):
        return _package(ws, out_path, buildings, record=False, bake=False, catalogue=catalogue, item_types="all",
                        models=models, alone=True)
    known = building_ids(ws)
    buildings = known if buildings is None else buildings
    if unknown := sorted(set(buildings) - set(known)):
        raise ExportError(f"no building {', '.join(unknown)} in this project")
    if not buildings:
        raise ExportError("no building to show")
    return _package(ws, out_path, buildings, record=False, bake=False, catalogue=catalogue, item_types="all",
                    models=models, alone=True)


def _package(ws: Workspace, out_path, buildings: list[str], *, record: bool, bake: bool, say=None,
             catalogue: Catalogue | None = None, item_types: str = "used", models=None,
             alone: bool = False, drawings=None) -> Manifest:
    cat = catalogue or default_catalogue()
    buildings = sorted(set(buildings))
    locations = {b.rsplit("-", 1)[0] for b in buildings}
    scoped = in_buildings(buildings)
    # a location's layout (format 0.12) is a package of its own: its land's ID is its scope here
    lands = [b for b in buildings if is_land(b)]
    if lands and len(buildings) != 1:
        raise ExportError("a location's layout is a package of its own: export it alone")

    def held(role: str, f: dict) -> bool:
        if role == "location":
            return f["id"] in locations
        return scoped(f["properties"]["floor_id"] if role == "items" else f["id"])  # an item: where it is

    # a package is worked out from the whole project (an item that moved to another
    # building, or onto a layout, is said to have); a preview, from its buildings ``alone``
    only = set(buildings) if alone else None
    everything = build_features(ws, cat, only=only)
    for role, fs in layout_features(ws, cat, only=only).items():
        everything[role] = everything.get(role, []) + fs
    if not lands:  # (a building's package holds no layout)
        everything.pop("layout"), everything.pop("footprints")
    features = {role: [f for f in fs if held(role, f)] for role, fs in everything.items()}
    placed = placements(ws)
    sequence = (ws.exports[-1].sequence + 1) if ws.exports else 1
    if alone:  # a preview lists no changes against the packages sent: nothing that shows it reads them
        changes = Changes(sequence=sequence, previous_sequence=None, added=[], changed=[], retired=[],
                          all_retired=[], moved_away=[])
    else:
        hashes = compared(ws, cat, held, only=only)

        # each building is compared with the last package that held it, whatever packages
        # of the project's other buildings came between
        held_before, known = last_packages(ws)
        last = {b: held_before[b] for b in buildings if b in held_before}
        prev_hashes: dict[str, str] = {}
        for p in last.values():
            prev_hashes.update(p.objects)
        # every item in use, now: the building it stands in, or the land of the layout it stands on
        places = {f["id"]: f["properties"]["building_id"] or f["properties"]["floor_id"].rsplit("-", 1)[0]
                  for f in everything["items"]}
        here = {f["id"] for f in features["items"]}
        gone = sorted(i for i in prev_hashes if i not in hashes)
        moved_away = [MovedAway(id=i, building_id=_holder(ws, places[i])) for i in gone if i in places]

        def item(i: str) -> bool:  # an item's ID: one the project has, or a package of it held (of any format)
            return i in ws.items or i in known or is_item_id(i)

        def taken_away(i: str) -> bool:  # an object of the building, or an item retired (wherever it was then)
            if item(i):
                return i not in ws.items or ws.items[i].status == "retired"
            return scoped(i)  # a location stays

        def active(i: str) -> bool:
            r = ws.items.get(i) if item(i) else ws.objects.get(i)
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
            added=sorted(i for i in hashes if i not in prev_hashes and i not in known),
            changed=sorted(i for i in hashes if (prev_hashes[i] != hashes[i] if i in prev_hashes else i in known)),
            retired=retired,
            all_retired=all_retired,
            moved_away=moved_away,
        )

    now = utcnow()
    levels = [(fid, f) for _, _, f, fid in ws.iter_floors(layouts=True) if scoped(fid)]  # (a layout's: its own)
    if lands:  # the layout's package: its site's placement, by the layout's ID (format 0.12)
        layout_id = next((fid for fid, _ in levels), f"{lands[0]}-G00")
        loc = ws.location(lands[0].rsplit("-", 1)[0])
        site = site_placement(loc)
        placed_here = {layout_id: PlacementInfo(**site.model_dump(), placed=loc.placement is not None)}
        scope = Scope(buildings=[], layout=layout_id)
    else:
        placed_here = {b_id: PlacementInfo(**p.model_dump(), placed=real, site=site_frame(ws.building(b_id)))
                       for b_id, (p, real) in placed.items() if scoped(b_id)}
        scope = Scope(buildings=buildings)
    roles = [r for r in FILES if r in features or r in ("objects", "changes", "catalogue")]
    manifest = Manifest(
        generator=Generator(name="storeypath", version=version("storeypath")),
        project=ProjectInfo(id=ws.id, name=ws.project.name),
        export=ExportInfo(sequence=sequence, exported_at=now, previous_sequence=changes.previous_sequence),
        files={**{r: FILES[r] for r in roles if r != "navigation"}, "spec": "FORMAT.md", "schemas": "schema/"},
        counts={role: len(fs) for role, fs in features.items()},
        types={"space": [t.value for t in SpaceType], "zone": [t.value for t in SpaceType],
               "opening": [t.value for t in OpeningType], "floor": [t.value for t in BUILDING_LEVELS]},
        sources=[SourceInfo(floor_id=fid, file=Path(f.source.path).name, sha256=f.source.sha256)
                 for fid, f in levels if f.source],
        placements=placed_here,
        scope=scope,
    )

    # its item types (format 0.9.1): those its items use, each once, or the whole catalogue;
    # what the package's other files say (capacity, kiosks) is worked out from the whole
    used = {f["properties"]["type"] for f in features["items"]}
    written = cat if item_types == "all" else cat.model_copy(update={"types": [t for t in cat.types if t.code in used]})
    # and their 3D models (format 0.9.2), each once: a type whose model is not to be had
    # here says it has none, and is drawn by its shape
    model_files = {t.model: data for t in written.types if t.model and models is not None and (data := models(t.code))}
    written = written.model_copy(update={"types": [t.model_copy(update={"model": t.model if t.model in model_files
                                                                          else None}) for t in written.types]})
    if model_files:
        manifest.files["models"] = MODELS

    # the walking network of its building (format 0.8); a layout's, its land's, linked to its
    # buildings' at their entrances (0.13): a building with no way in from the land is said
    if lands:
        notes: list[dict] = []
        navigation = build_site_network(ws, layout_id, cat, notes)
        for n in notes:
            (say or log.info)(f"no way in from the land: {n['why']}")
    else:
        navigation = navigation_file(ws, buildings, cat, placed)
    manifest.files["navigation"] = NAVIGATION_FILE

    world: dict[str, bytes] = {}
    if bake and not lands:  # the package as it is so far, for the baker to read
        with tempfile.TemporaryDirectory(prefix="storeypath-export-") as tmp:
            plain = Path(tmp) / "package.storeypath"
            _write(plain, ws, manifest, features, changes, model_files, written, navigation)
            world, why = bake_world(plain)
        if world:
            manifest.files["world"] = WORLD_DIR
        (say or log.info)(f"3D pre-built: {len(world)} floor(s)" if world else f"3D not pre-built: {why}")

    # each floor's drawing as drawn (format 0.11), when asked for: its source names it
    views: dict[str, bytes] = {}
    if drawings is not None:
        floors = [(fid, f.source) for fid, f in levels if f.source]
        for (fid, _), data in zip(floors, drawings(floors) if floors else []):
            if data:
                views[f"{DRAWINGS}{fid}.view"] = data
        for s in manifest.sources:
            if f"{DRAWINGS}{s.floor_id}.view" in views:
                s.view = f"{DRAWINGS}{s.floor_id}.view"
        if views:
            manifest.files["drawings"] = DRAWINGS
        (say or log.info)(f"drawings: {len(views)} of {len(floors)} floor(s)")

    if isinstance(out_path, (str, Path)):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    _write(out_path, ws, manifest, features, changes, {**world, **model_files, **views}, written, navigation)

    if record:
        held_now = dict(held_before)
        for b in buildings:  # (one: a package holds one building)
            location = b.rsplit("-", 1)[0]
            held_now[b] = LastPackage(
                sequence=sequence,
                objects={i: h for i, h in hashes.items()
                         if i == location or _in_building(i, b) or places.get(i) == b},
                retired=[i for i in all_retired if _in_building(i, b) or item(i)])
        ws.exports.append(ExportRecord(
            sequence=sequence, exported_at=now, file=Path(out_path).name, buildings=buildings,
            held=held_now, items_held=sorted(known | here)))
    return manifest


def _holder(ws: Workspace, held_by: str) -> str:
    """What holds an item now, as a package says it (moved_away): its building, or the
    layout of the land it stands on (format 0.12)."""
    if is_land(held_by):
        loc = ws.location(held_by.rsplit("-", 1)[0])
        layout = loc.layout.floors[0].code if loc.layout is not None and loc.layout.floors else "G00"
        return f"{held_by}-{layout}"
    return held_by


def _write(out, ws: Workspace, manifest: Manifest, features: dict[str, list[dict]], changes: Changes,
           extra: dict, cat: Catalogue, navigation: dict | None = None) -> None:
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
    if navigation is not None:
        files[FILES["navigation"]] = strict(FILES["navigation"], navigation, ensure_ascii=False, separators=(",", ":"))
    files["FORMAT.md"] = format_spec()
    for name, schema in json_schemas().items():
        files[f"schema/{name}"] = json.dumps(schema, indent=2)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
        for name, data in extra.items():
            # (a drawing's view is gzip already: stored as it is)
            how = zipfile.ZIP_STORED if name.startswith(DRAWINGS) else None
            if isinstance(data, Path):
                z.write(data, name, compress_type=how)
            else:
                z.writestr(name, data, compress_type=how)


class FloorViews:
    """export_package's ``drawings``, made from drawing files (the command line's, and
    anyone's with the drawings at hand): ``file(source)`` is the file of a floor's drawing
    (None: not here). The floors of one drawing are drawn together, the drawing read once
    (drawview.write_floor_views); ``say`` hears how far each has got."""

    def __init__(self, file, say=None):
        self.file, self.say = file, say or (lambda line: None)

    def __call__(self, floors: list) -> list[bytes | None]:
        from .drawview import write_floor_views

        out: list[bytes | None] = [None] * len(floors)
        by_drawing: dict[Path, list[int]] = {}
        for i, (_, src) in enumerate(floors):
            path = self.file(src)
            if path is not None and Path(path).is_file():
                by_drawing.setdefault(Path(path), []).append(i)
        with tempfile.TemporaryDirectory(prefix="storeypath-views-") as tmp:
            for path, held in by_drawing.items():
                self.say(f"drawing {', '.join(floors[i][0].rsplit('-', 1)[-1] for i in held)} from {path.name}")
                parts = [{"out": str(Path(tmp) / f"{floors[i][0]}.view"), "region": floors[i][1].region,
                          "offset": floors[i][1].offset, "units": floors[i][1].units} for i in held]
                write_floor_views(path, parts, say=self.say)
                for i, part in zip(held, parts):
                    out[i] = Path(part["out"]).read_bytes()
        return out
