"""Converting a floor drawing into registered objects with stable IDs.

On every (re)conversion the new spaces and doors are matched against the
floor's current objects. A match keeps its ID; an object that is no longer in
the drawing is retired (its ID is never issued again); anything new gets a new
code from the building's counter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shapely import STRtree, set_precision
from shapely.geometry import Point, mapping, shape

from .analyse import analyse, name_hint, plan_texts
from .cad import file_sha256, meters_per_unit, read_drawing, read_notes
from .extract import (ExtractedDoor, ExtractedSpace, FloorExtraction, _assign_labels, add_lift_doors, extract_floor,
                      keep_to_the_building, stair_flights, type_stairs, type_zoned_spaces)
from .geometry import iou
from .ids import child_id, parse_id
from .llm import LocalModel, worth_reading
from .profile import AUTO, load_profile, resolve_profile
from .reading import TextReader
from .symbols import MODEL as SYMBOLS_MODEL
from .symbols import Symbol, SymbolSpotter, from_records, to_records, type_rooms
from .vision import FloorPrint, VisionModel, keep_ways_through, look_at_rooms, split_merged
from .types import VERTICAL_TYPES, SpaceType
from .workspace import ObjectRecord, Workspace, utcnow

MATCH_MIN_IOU = 0.5  # outlines overlapping this much are the same space
MATCH_MIN_IOU_SAME_NUMBER = 0.1  # …or this much when the room number is unchanged
VERTICAL_MIN_IOU = 0.5  # elevator/stairs outlines on two floors this aligned share a code
DOOR_MATCH_DISTANCE = 0.5  # m
SAMPLE_TEXTS = 6  # unknown texts per layer asked about when reading what a layer holds
# A read that finds no spaces where a floor has some, or that would retire more than
# this share of them, is most likely a bad read (layers renamed, wrong units, a
# broken file): it is held back, the floor keeping its rooms and IDs, until it is
# applied on purpose (``force``).
HOLD_RETIRING_SHARE = 0.5


@dataclass
class ConversionReport:
    floor_id: str
    method: str = "outlines"
    kept: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unspecified: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    held: bool = False  # the read was not applied: it would have retired most of the floor

    def summary(self) -> str:
        if self.held:
            return f"{self.floor_id}: not changed: the drawing read too unlike the floor (see the warning)"
        source = " (spaces found from walls)" if self.method == "walls" else ""
        return (
            f"{self.floor_id}: {len(self.kept)} kept, {len(self.added)} new, "
            f"{len(self.retired)} retired, {len(self.unspecified)} need a type{source}"
        )


def convert_floor(
    ws: Workspace, floor_id: str, workspace_dir: str | Path = ".", model: LocalModel | None = None,
    symbols: SymbolSpotter | None = None, vision: VisionModel | None = None, say=None, force: bool = False,
) -> ConversionReport:
    """Read a floor's drawing and register what it holds. With the "auto" profile the
    layers are read from what is drawn on them; ``model`` (a local language model)
    reads the texts the rules do not know; ``symbols`` spots the fixtures drawn in
    rooms that have no name; ``vision`` looks at every room as drawn (vision.py).
    A read that would retire most of the floor is held back unless ``force`` (see
    apply_extraction): the floor is then left as it was."""
    floor = ws.floor(floor_id)
    if floor.source is None:
        raise ValueError(f"floor {floor_id} has no source drawing")
    if vision is not None:
        vision.failed = None  # only what goes wrong converting this floor is reported with it
    src = floor.source
    path = Path(workspace_dir) / src.path
    doc = read_drawing(path)
    base = load_profile(resolve_profile(src.profile, workspace_dir))
    reader = TextReader(ws, base, model)
    if src.profile == AUTO:
        reader.learn(plan_texts(doc, src.region, per_layer=SAMPLE_TEXTS,
                                unknown=lambda t: worth_reading(t) and reader.is_room_name(t) is None))
        analysis = analyse(doc, meters_per_unit(doc, src.units), src.region, reader.is_room_name, base)
        profile = analysis.profile
    else:
        profile, analysis = base, None
    extraction = extract_floor(doc, profile, src.units, src.region, src.offset,
                               skip_label=lambda t: reader.is_room_name(t) is False, drawn_walls=floor.edits.walls,
                               drawn_dividers=floor.edits.dividers)
    _drawn_spaces(extraction, floor.edits.spaces, profile.spaces.min_area)
    _read_room_types(extraction, reader)
    sha = file_sha256(path)
    spotted, spot_failed = _spot_symbols(floor, doc, profile, symbols, extraction.scale, sha)
    type_rooms(extraction.units(), spotted)
    add_lift_doors(extraction)
    if vision is not None or ws.vision:
        sheet = FloorPrint(doc, extraction.scale)
        units = extraction.units()
        merged = look_at_rooms(units, doc, src, extraction.scale, sha, vision, ws.vision, say, sheet=sheet)
        if zones := split_merged(extraction, units, doc, src, sha, vision, ws.vision, merged, say, sheet):
            _name_zones(extraction, zones, profile, reader, spotted)
            units = extraction.units()
            look_at_rooms(units, doc, src, extraction.scale, sha, vision, ws.vision, say,
                          only={i for i, u in enumerate(units) if any(u is z for z in zones)}, sheet=sheet)
        if (ways := keep_ways_through(extraction)) and say is not None:
            say(f"vision: {ways} area(s) it saw as no room kept, as doors join them to several rooms")
        type_stairs(extraction.units(), stair_flights(_floor_segments(doc, src, extraction.scale)))
        type_zoned_spaces(extraction.spaces, extraction.zones)
        keep_to_the_building(extraction)
    else:
        type_stairs(extraction.units(), stair_flights(_floor_segments(doc, src, extraction.scale)))
    _sizes_from_the_schedule(extraction, doc, src.region, vision, model, say)
    _drawn_openings(extraction, floor.edits.openings, profile.doors.reach)
    _resized_openings(extraction, floor.edits.resized)
    report = apply_extraction(ws, floor_id, extraction, force=force)
    if not report.held:  # a read held back leaves the floor as it was
        floor.layers = analysis.summary() if analysis is not None else []
        floor.source.sha256 = sha
    report.warnings += read_notes(doc)
    if reader.model_failed:
        report.warnings.append(f"the language model was not used: {reader.model_failed}")
    if spot_failed:
        report.warnings.append(f"symbols were not spotted: {spot_failed}")
    if vision is not None and vision.failed:
        report.warnings.append(f"vision: {vision.failed}")
    return report


NOT_SYMBOLS = {"dimension", "grid"}  # layers whose lines are not things in the building


def _spot_symbols(floor, doc, profile, spotter: SymbolSpotter | None, scale: float,
                  sha: str) -> tuple[list[Symbol], str | None]:
    """The symbols drawn in the floor's plan, and why they could not be found. They
    are kept with the floor and found again only when the drawing, the part of it
    read or its units change; without the model, the ones kept are used as long as
    they are of this same drawing."""
    src = floor.source
    key = f"{SYMBOLS_MODEL}/{sha}/{src.region}/{src.offset}/{scale:g}"
    if floor.symbols is not None and floor.symbols_key == key:
        return from_records(floor.symbols), None
    if spotter is None or not spotter.available():
        return [], None

    def skip(layer: str) -> bool:
        return name_hint(layer) in NOT_SYMBOLS or bool(profile.label_layers.fullmatch(layer))

    found = spotter.find(doc, src.region, src.offset, scale, skip)
    if spotter.failed is None:
        floor.symbols, floor.symbols_key = to_records(found), key
    return found, spotter.failed


def _read_room_types(ex: FloorExtraction, reader: TextReader) -> None:
    """Room names the rules do not know, typed by the language model."""
    unknown = [s.name for s in ex.units() if s.name and s.type == SpaceType.UNSPECIFIED]
    reader.learn(unknown, rooms_only=True)
    for s in ex.units():
        if s.name and s.type == SpaceType.UNSPECIFIED and (t := reader.room_type(s.name)) is not None:
            s.type, s.type_source = t, "model"


def _floor_segments(doc, src, scale: float) -> list:
    """Every straight piece drawn on a floor (not text, dimensions or hatches), local meters."""
    from shapely.geometry import box

    from .vision import _segments_in

    from .extract import drawing_extents

    ox, oy = src.offset or (0.0, 0.0)
    extent = src.region if src.region is not None else drawing_extents(doc)
    if extent is None:
        return []
    return _segments_in(doc, box(*extent), lambda x, y: ((x - ox) * scale, (y - oy) * scale))


def _name_zones(ex: FloorExtraction, zones: list, profile, reader: TextReader, spotted) -> None:
    """Zones vision divided an open space into, named from the labels inside each
    and typed as any space is."""
    _assign_labels(zones, ex.labels, profile, [])
    for z in zones:
        z.type, z.type_source = profile.classify(z.name, z.blocks, z.layer)
    _read_room_types(ex, reader)
    type_rooms(zones, spotted)


def _keep_package_values(ws: Workspace, record: ObjectRecord) -> None:
    """A space of a project rebuilt from a package (bundle.py), read from its drawing
    for the first time: what the package said of it (what a person may have set) is
    kept as its correction, over what the drawing is read as."""
    from .workspace import Override

    o = ws.overrides.get(record.id) or Override()
    if o.type is None and record.type in {t.value for t in SpaceType}:
        o.type = SpaceType(record.type)
    if o.name is None:
        o.name = record.name or ""
    if o.number is None:
        o.number = record.number or ""
    ws.overrides[record.id] = o


def apply_extraction(ws: Workspace, floor_id: str, ex: FloorExtraction, force: bool = False) -> ConversionReport:
    """Register an extracted floor in the workspace, reusing IDs where objects match.
    A read that finds no spaces on a floor that has some, or that would retire more
    than HOLD_RETIRING_SHARE of them, is held back unless ``force``: nothing changes,
    and the report says so (``held``) with a warning."""
    report = ConversionReport(floor_id, method=ex.method, warnings=list(ex.warnings))
    floor = ws.floor(floor_id)
    building_id = parse_id(floor_id).prefix("building")
    now = utcnow()

    existing = [r for r in ws.floor_objects(floor_id) if r.kind in ("space", "zone")]
    # What is used keeps its ID first (zones, and spaces with none), so a space that
    # an earlier conversion cut in two and that is now one space with two zones
    # hands its IDs to the zones; then the spaces divided into zones.
    zoned = {z.space for z in ex.zones}
    units = [*ex.zones, *(s for i, s in enumerate(ex.spaces) if i not in zoned)]
    containers = [s for i, s in enumerate(ex.spaces) if i in zoned]
    unit_pairs = _match_spaces(existing, units)
    taken = {r.id for r in unit_pairs.values()}
    container_pairs = _match_spaces([r for r in existing if r.id not in taken], containers)
    found = {id(units[i]): r for i, r in unit_pairs.items()} | {id(containers[i]): r for i, r in container_pairs.items()}
    if not force and (hold := _hold_back(existing, found, ex)):
        report.held = True
        report.warnings.append(hold)
        return report

    def register(obj, kind: str) -> ObjectRecord:
        record = found.get(id(obj))
        if record is None:
            record_id = _new_space_id(ws, floor_id, building_id, obj) if kind == "space" else \
                _allocate_id(ws, floor_id, building_id)
            record = ObjectRecord(id=record_id, kind=kind, type=obj.type, geometry={})
            ws.objects[record_id] = record
            report.added.append(record_id)
        else:
            report.kept.append(record.id)
            if record.type_source == "package":
                _keep_package_values(ws, record)
        record.kind = kind
        record.type, record.type_source = obj.type, obj.type_source
        record.name, record.number = obj.name, obj.number
        record.label = obj.label
        record.issues = list(obj.issues)
        record.detected_ignored = obj.ignored
        record.geometry = _local(obj.polygon)
        record.parent, record.zones = None, []
        return record

    space_records = [register(s, "space") for s in ex.spaces]
    space_ids = [r.id for r in space_records]
    for z in ex.zones:
        record = register(z, "zone")
        record.parent = space_ids[z.space]
        space_records[z.space].zones.append(record.id)
    report.unspecified += [r.id for r in (*space_records, *(ws.objects[i] for s in space_records for i in s.zones))
                           if not r.zones and ws.effective(r)["type"] == "unspecified"]

    matched = set(report.kept)
    for r in existing:
        if r.id not in matched:
            _retire(r, now, report)

    _apply_doors(ws, floor_id, building_id, ex, space_ids, now, report)

    floor.outline = _local(ex.outline) if ex.outline is not None else None
    floor.walls = _local(ex.walls) if ex.walls is not None else None
    floor.wall_thickness = ex.wall_thickness
    floor.converted_at = now
    floor.method, floor.warnings = ex.method, list(ex.warnings)
    return report


def _hold_back(existing: list[ObjectRecord], found: dict, ex: FloorExtraction) -> str | None:
    """Why a read is held back (see apply_extraction), or None to apply it."""
    if not existing:
        return None
    kept = {r.id for r in found.values()}
    retiring = sum(1 for r in existing if r.id not in kept)
    if ex.spaces and retiring <= HOLD_RETIRING_SHARE * len(existing):
        return None
    what = "no spaces" if not ex.spaces else f"{len(ex.spaces)} spaces, {len(existing) - retiring} of them where they were"
    return (f"nothing was changed: the drawing read {what}, so {retiring} of the floor's {len(existing)} spaces and "
            "their IDs would be retired. Check the drawing (its layers, units or part read); if it really "
            "changed this much, convert again with force (storeypath convert --force)")


def _sizes_from_the_schedule(ex: FloorExtraction, doc, region, vision, model, say) -> None:
    """Each tagged door's and window's sill and height, from the drawing's schedule of
    openings (schedule.py), its rows read by the strongest model that runs."""
    from .schedule import sizes_near
    from .vision import InWords

    if vision is not None and vision.available():
        reader = InWords(vision)
    else:
        reader = model if model is not None and model.available() else None
    sizes = sizes_near(doc, region, reader)
    sized = 0
    for d in ex.doors:
        if d.tag and (size := sizes.get(d.tag)) is not None:
            d.sill, d.height = size.sill, size.height
            sized += 1
    if sized and say:
        say(f"{sized} doors and windows sized from the schedule of openings")


def _local(geom) -> dict:
    """GeoJSON geometry in local meters, rounded to 0.1 mm."""
    return mapping(set_precision(geom, 1e-4))


def _retire(record: ObjectRecord, now, report: ConversionReport) -> None:
    record.status = "retired"
    record.retired_at = now
    report.retired.append(record.id)


def _match_spaces(existing: list[ObjectRecord], spaces: list[ExtractedSpace]) -> dict[int, ObjectRecord]:
    """One-to-one matching, best candidates first."""
    old_shapes = [shape(r.geometry) for r in existing]
    tree = STRtree(old_shapes)  # only spaces that overlap can match: no all-against-all on big floors
    candidates = []
    for i, s in enumerate(spaces):
        for j in sorted(int(k) for k in tree.query(s.polygon, predicate="intersects")):
            r = existing[j]
            overlap = iou(s.polygon, old_shapes[j])
            same_number = bool(s.number) and s.number == r.number
            if overlap >= MATCH_MIN_IOU or (same_number and overlap >= MATCH_MIN_IOU_SAME_NUMBER):
                candidates.append((overlap + (1.0 if same_number else 0.0), i, j))
    candidates.sort(reverse=True)
    pairs: dict[int, ObjectRecord] = {}
    used: set[int] = set()
    for _, i, j in candidates:
        if i not in pairs and j not in used:
            pairs[i] = existing[j]
            used.add(j)
    return pairs


def _new_space_id(ws: Workspace, floor_id: str, building_id: str, space: ExtractedSpace) -> str:
    """Elevators and stairs that line up with one on another floor of the same
    building reuse its object code, as long as that full ID was never issued. The
    other floor's is taken as a person corrected it (a lift the drawing left untyped)."""
    if space.type in VERTICAL_TYPES:
        floor_prefix = floor_id + "-"
        for r in ws.objects.values():
            if (
                r.status == "active"
                and ws.effective(r)["type"] in VERTICAL_TYPES
                and r.id.startswith(building_id + "-")
                and not r.id.startswith(floor_prefix)
                and iou(space.polygon, shape(r.geometry)) >= VERTICAL_MIN_IOU
            ):
                candidate = child_id(floor_id, parse_id(r.id).code)
                if candidate not in ws.objects:
                    return candidate
    return _allocate_id(ws, floor_id, building_id)


def _allocate_id(ws: Workspace, floor_id: str, building_id: str) -> str:
    while True:
        candidate = child_id(floor_id, ws.allocate_object_code(building_id))
        if candidate not in ws.objects:
            return candidate


def _opening_type(source: str) -> str:
    return {"door": "door", "glazing": "door", "assumed": "door", "window": "window",
            "drawn door": "door", "drawn window": "window", "drawn opening": "opening"}.get(source, "opening")


DRAWN_NOTE = "drawn in review: where the drawing encloses no space"


CARVE_SHARE = 0.5  # a space drawn this much inside the rooms found is cut out of them
WAY_ROOM_M = 1.5  # its way through is on the edge with the most room in front of it, this deep
CARVED_NOTE = "drawn in review: cut out of the room it was drawn in"


def _drawn_spaces(ex: FloorExtraction, drawn, min_area: float) -> None:
    """The spaces a person drew. Where the drawing encloses none (a colonnade between
    columns): without what the rooms found already cover, and joined to the ways
    through around it that led nowhere until then. Drawn mostly inside the rooms found
    (a lift or stairs the drawing left in a corridor): cut out of them, with a way
    through where they meet (no wall is drawn there); a room it covers whole is left as
    it is. Added as spaces, and to the floor's outline."""
    from shapely import union_all
    from shapely.geometry import Polygon

    from .geometry import as_polygons

    added = []
    for ring in drawn:
        rooms = union_all([s.polygon for s in ex.spaces if not s.ignored]) if ex.spaces else None
        area = Polygon(ring).buffer(0)
        if rooms is not None and not rooms.is_empty and area.intersection(rooms).area > CARVE_SHARE * area.area:
            added += _carve(ex, area, min_area)
            continue
        if rooms is not None and not rooms.is_empty:
            area = area.difference(rooms)
        for part in as_polygons(area):
            if part.area >= min_area:
                ex.spaces.append(ExtractedSpace(polygon=part, layer="drawn", issues=[DRAWN_NOTE]))
                added.append(part)
                _join_loose_doors(ex, len(ex.spaces) - 1)
    if added and ex.outline is not None:
        ex.outline = union_all([ex.outline, *added])


def _carve(ex: FloorExtraction, area, min_area: float) -> list:
    """A space drawn inside rooms found: cut out of each (one it covers whole stays as
    it is, and is left out of it), the largest part of each kept; and a way through
    (an opening, "split") on the longest edge it shares with each."""
    from shapely.geometry import LineString

    from .geometry import as_polygons

    hosts = []
    for i, s in enumerate(ex.spaces):
        if s.ignored or not s.polygon.intersects(area):
            continue
        rest = s.polygon.difference(area)
        if rest.area < min_area:  # covered whole: it is the room drawn, as it was found
            area = area.difference(s.polygon)
            continue
        if s.polygon.intersection(area).area < 0.01:
            continue
        s.polygon = max(as_polygons(rest), key=lambda p: p.area)
        for z in ex.zones:
            if z.space == i:
                left = [p for p in as_polygons(z.polygon.difference(area)) if p.area >= 0.01]
                if left:
                    z.polygon = max(left, key=lambda p: p.area)
        hosts.append(i)
    out = []
    for part in as_polygons(area.buffer(0)):
        if part.area < min_area:
            continue
        ex.spaces.append(ExtractedSpace(polygon=part, layer="drawn", issues=[CARVED_NOTE]))
        new = len(ex.spaces) - 1
        out.append(part)
        for i in hosts:
            shared = part.exterior.intersection(ex.spaces[i].polygon.buffer(0.02))
            edges = [LineString([a, b]) for g in getattr(shared, "geoms", [shared]) if isinstance(g, LineString)
                     for a, b in zip(g.coords, list(g.coords)[1:])]
            edges = [e for e in edges if e.length >= 0.3]
            if not edges:
                continue
            # the edge that opens onto most of the room (not onto a strip left by a wall)
            host = ex.spaces[i].polygon
            span = max(edges, key=lambda e: (round(e.buffer(WAY_ROOM_M, cap_style="flat").difference(part)
                                                   .intersection(host).area, 2), e.length))
            ex.doors.append(ExtractedDoor(footprint=span.buffer(0.15, cap_style="flat"),
                                          point=span.interpolate(0.5, normalized=True), connects=[i, new],
                                          source="split", span=span, width=round(span.length, 3)))
    return out


def _join_loose_doors(ex: FloorExtraction, new: int, reach: float = 0.4) -> None:
    """Ways through that led out of the floor (one space on their side) and open onto a
    space drawn where there was none: joined to it."""
    space = ex.spaces[new].polygon.buffer(reach)
    for d in ex.doors:
        if d.source == "window" or len(d.connects) != 1 or new in d.connects:
            continue
        if d.footprint.intersects(space):
            d.connects.append(new)


def _drawn_openings(ex: FloorExtraction, drawn, reach: float) -> None:
    """The doors, windows and openings a person added in review: openings like any,
    joined to the spaces beside them, with their gap cut in the walls."""
    from shapely.geometry import LineString

    from .extract import _connect_doors

    thickness = max(ex.wall_thickness or 0.2, 0.1)
    for o in drawn:
        span = LineString(o.span)
        if span.length < 0.2:
            continue
        found = _connect_doors(ex.spaces, [span.buffer(thickness, cap_style="flat")], reach, [],
                               [(span, round(span.length, 3))])
        for d in found:
            d.source = f"drawn {o.type}"
            d.sill, d.height = o.sill, o.height
            ex.doors.append(d)
        if ex.walls is not None:
            ex.walls = ex.walls.difference(span.buffer(thickness, cap_style="flat"))


RESIZE_REACH_M = 0.5  # an opening given another size in review is found this near its middle


def _resized_openings(ex: FloorExtraction, resized) -> None:
    """The drawing's doors, windows and openings given another size in review: each
    the opening nearest its middle, its gap in the walls made to fit (what it no longer
    takes is wall again, as thick as the wall on either side), a door's leaves with it."""
    if not resized:
        return
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union

    from .geometry import leaves_of_width, span_of_width

    thickness = max(ex.wall_thickness or 0.2, 0.1)
    rooms = unary_union([s.polygon for s in ex.spaces]) if ex.spaces else None
    middle = lambda d: d.span.interpolate(0.5, normalized=True) if d.span is not None else d.point  # noqa: E731
    for rz in resized:
        at = Point(rz.at)
        near = [(middle(d).distance(at), k) for k, d in enumerate(ex.doors) if not d.source.startswith("drawn")]
        near = [n for n in near if n[0] <= RESIZE_REACH_M]
        if not near:
            continue
        d = ex.doors[min(near)[1]]
        if rz.sill is not None:
            d.sill = rz.sill
        if rz.height is not None:
            d.height = rz.height
        if rz.width is None:
            continue
        if d.span is None or d.span.length < 0.05:
            d.width = rz.width
            continue
        old = [list(c) for c in d.span.coords]
        new = span_of_width(old, rz.width)
        d.swings = [tuple(map(tuple, leaf)) for leaf in leaves_of_width(d.swings, old, new)] if d.swings else []
        d.span, d.width = LineString(new), round(rz.width, 3)
        if ex.walls is not None:
            fill = _gap_band(ex.walls, LineString(old), thickness)
            if fill is not None and rooms is not None:
                fill = fill.difference(rooms)
            walls = ex.walls.union(fill) if fill is not None else ex.walls
            ex.walls = walls.difference(LineString(new).buffer(thickness, cap_style="flat"))


def _gap_band(walls, span, thickness: float):
    """The wall a gap interrupts, as if it did not: the hull of the wall's ends on either
    side of the span (None when there are none)."""
    from shapely.geometry import LineString

    (x0, y0), (x1, y1) = span.coords[0], span.coords[-1]
    ux, uy = (x1 - x0) / span.length, (y1 - y0) / span.length
    e = max(thickness, 0.3)
    longer = LineString([(x0 - ux * e, y0 - uy * e), (x1 + ux * e, y1 + uy * e)])
    ends = walls.intersection(longer.buffer(1.5 * thickness + 0.05, cap_style="flat"))
    return None if ends.is_empty else ends.convex_hull


def _apply_doors(ws, floor_id, building_id, ex: FloorExtraction, space_ids, now, report) -> None:
    """Openings keep their IDs by position: windows are matched only with windows,
    ways through (doors, doorways) only with ways through."""
    existing = [r for r in ws.floor_objects(floor_id) if r.kind == "opening"]
    old_points = [shape(r.geometry) for r in existing]
    tree = STRtree(old_points)
    used: set[int] = set()
    for door in ex.doors:
        kind = _opening_type(door.source)
        best, best_d = None, DOOR_MATCH_DISTANCE
        near = tree.query(door.point, predicate="dwithin", distance=DOOR_MATCH_DISTANCE)
        for j in sorted(int(k) for k in near):
            p = old_points[j]
            same_group = (existing[j].type == "window") == (kind == "window")
            d = door.point.distance(p)
            if j not in used and same_group and d <= best_d:
                best, best_d = j, d
        if best is None:
            record_id = _allocate_id(ws, floor_id, building_id)
            record = ObjectRecord(id=record_id, kind="opening", type=kind, geometry={})
            ws.objects[record_id] = record
            report.added.append(record_id)
        else:
            used.add(best)
            record = existing[best]
            report.kept.append(record.id)
        record.type, record.type_source = kind, door.source
        record.tag, record.issues = door.tag, list(door.issues)
        record.geometry = mapping(Point(round(door.point.x, 4), round(door.point.y, 4)))
        record.connects = [space_ids[i] for i in door.connects]
        record.span = [[round(x, 4), round(y, 4)] for x, y in door.span.coords] if door.span is not None else None
        record.width = door.width
        record.swings = [[[round(x, 4), round(y, 4)] for x, y in leaf] for leaf in door.swings] or None
        record.sill, record.height = door.sill, door.height
    for j, r in enumerate(existing):
        if j not in used:
            _retire(r, now, report)
