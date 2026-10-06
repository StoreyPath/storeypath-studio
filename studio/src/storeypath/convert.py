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
from .cad import file_sha256, meters_per_unit, read_drawing
from .extract import (ExtractedSpace, FloorExtraction, _assign_labels, add_lift_doors, extract_floor,
                      keep_to_the_building, stair_flights, type_stairs, type_zoned_spaces)
from .geometry import iou
from .ids import child_id, parse_id
from .llm import LocalModel, worth_reading
from .profile import AUTO, load_profile, resolve_profile
from .reading import TextReader
from .symbols import MODEL as SYMBOLS_MODEL
from .symbols import Symbol, SymbolSpotter, from_records, to_records, type_rooms
from .vision import FloorPrint, VisionModel, look_at_rooms, split_merged
from .types import VERTICAL_TYPES, SpaceType
from .workspace import ObjectRecord, Workspace, utcnow

MATCH_MIN_IOU = 0.5  # outlines overlapping this much are the same space
MATCH_MIN_IOU_SAME_NUMBER = 0.1  # …or this much when the room number is unchanged
VERTICAL_MIN_IOU = 0.5  # elevator/stairs outlines on two floors this aligned share a code
DOOR_MATCH_DISTANCE = 0.5  # m
SAMPLE_TEXTS = 6  # unknown texts per layer asked about when reading what a layer holds


@dataclass
class ConversionReport:
    floor_id: str
    method: str = "outlines"
    kept: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unspecified: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        source = " (spaces found from walls)" if self.method == "walls" else ""
        return (
            f"{self.floor_id}: {len(self.kept)} kept, {len(self.added)} new, "
            f"{len(self.retired)} retired, {len(self.unspecified)} need a type{source}"
        )


def convert_floor(
    ws: Workspace, floor_id: str, workspace_dir: str | Path = ".", model: LocalModel | None = None,
    symbols: SymbolSpotter | None = None, vision: VisionModel | None = None, say=None,
) -> ConversionReport:
    """Read a floor's drawing and register what it holds. With the "auto" profile the
    layers are read from what is drawn on them; ``model`` (a local language model)
    reads the texts the rules do not know; ``symbols`` spots the fixtures drawn in
    rooms that have no name; ``vision`` looks at every room as drawn (vision.py)."""
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
        floor.layers = analysis.summary()
    else:
        profile, floor.layers = base, []
    extraction = extract_floor(doc, profile, src.units, src.region, src.offset,
                               skip_label=lambda t: reader.is_room_name(t) is False)
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
        type_stairs(extraction.units(), stair_flights(_floor_segments(doc, src, extraction.scale)))
        type_zoned_spaces(extraction.spaces, extraction.zones)
        keep_to_the_building(extraction)
    else:
        type_stairs(extraction.units(), stair_flights(_floor_segments(doc, src, extraction.scale)))
    report = apply_extraction(ws, floor_id, extraction)
    if reader.model_failed:
        report.warnings.append(f"the language model was not used: {reader.model_failed}")
    if spot_failed:
        report.warnings.append(f"symbols were not spotted: {spot_failed}")
    if vision is not None and vision.failed:
        report.warnings.append(f"vision: {vision.failed}")
    floor.source.sha256 = sha
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

    ox, oy = src.offset or (0.0, 0.0)
    if src.region is not None:
        region = box(*src.region)
    else:
        from ezdxf import bbox as ebbox

        ext = ebbox.extents(doc.modelspace(), fast=True)
        region = box(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
    return _segments_in(doc, region, lambda x, y: ((x - ox) * scale, (y - oy) * scale))


def _name_zones(ex: FloorExtraction, zones: list, profile, reader: TextReader, spotted) -> None:
    """Zones vision divided an open space into, named from the labels inside each
    and typed as any space is."""
    _assign_labels(zones, ex.labels, profile, [])
    for z in zones:
        z.type, z.type_source = profile.classify(z.name, z.blocks, z.layer)
    _read_room_types(ex, reader)
    type_rooms(zones, spotted)


def apply_extraction(ws: Workspace, floor_id: str, ex: FloorExtraction) -> ConversionReport:
    """Register an extracted floor in the workspace, reusing IDs where objects match."""
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
        record.kind = kind
        record.type, record.type_source = obj.type, obj.type_source
        record.name, record.number = obj.name, obj.number
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
    building reuse its object code, as long as that full ID was never issued."""
    if space.type in VERTICAL_TYPES:
        floor_prefix = floor_id + "-"
        for r in ws.objects.values():
            if (
                r.status == "active"
                and r.type in VERTICAL_TYPES
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
    return {"door": "door", "glazing": "door", "assumed": "door", "window": "window"}.get(source, "opening")


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
    for j, r in enumerate(existing):
        if j not in used:
            _retire(r, now, report)
