"""Converting a floor drawing into registered objects with stable IDs.

On every (re)conversion the new spaces and doors are matched against the
floor's current objects. A match keeps its ID; an object that is no longer in
the drawing is retired (its ID is never issued again); anything new gets a new
code from the building's counter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shapely import set_precision
from shapely.geometry import Point, mapping, shape

from .cad import file_sha256, read_drawing
from .extract import ExtractedSpace, FloorExtraction, extract_floor
from .ids import child_id, parse_id
from .profile import load_profile
from .types import VERTICAL_TYPES
from .workspace import ObjectRecord, Workspace, utcnow

MATCH_MIN_IOU = 0.5  # outlines overlapping this much are the same space
MATCH_MIN_IOU_SAME_NUMBER = 0.1  # …or this much when the room number is unchanged
VERTICAL_MIN_IOU = 0.5  # elevator/stairs outlines on two floors this aligned share a code
DOOR_MATCH_DISTANCE = 0.5  # m


@dataclass
class ConversionReport:
    floor_id: str
    kept: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unspecified: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.floor_id}: {len(self.kept)} kept, {len(self.added)} new, "
            f"{len(self.retired)} retired, {len(self.unspecified)} need a type"
        )


def convert_floor(ws: Workspace, floor_id: str, workspace_dir: str | Path = ".") -> ConversionReport:
    floor = ws.floor(floor_id)
    if floor.source is None:
        raise ValueError(f"floor {floor_id} has no source drawing")
    path = Path(workspace_dir) / floor.source.path
    doc = read_drawing(path)
    extraction = extract_floor(doc, load_profile(floor.source.profile), floor.source.units)
    report = apply_extraction(ws, floor_id, extraction)
    floor.source.sha256 = file_sha256(path)
    return report


def apply_extraction(ws: Workspace, floor_id: str, ex: FloorExtraction) -> ConversionReport:
    """Register an extracted floor in the workspace, reusing IDs where objects match."""
    report = ConversionReport(floor_id, warnings=list(ex.warnings))
    floor = ws.floor(floor_id)
    building_id = parse_id(floor_id).prefix("building")
    now = utcnow()

    existing = [r for r in ws.floor_objects(floor_id) if r.kind == "space"]
    pairs = _match_spaces(existing, ex.spaces)

    space_ids: list[str] = []
    for i, space in enumerate(ex.spaces):
        record = pairs.get(i)
        if record is None:
            record_id = _new_space_id(ws, floor_id, building_id, space)
            record = ObjectRecord(id=record_id, kind="space", type=space.type, geometry={})
            ws.objects[record_id] = record
            report.added.append(record_id)
        else:
            report.kept.append(record.id)
        record.type, record.type_source = space.type, space.type_source
        record.name, record.number = space.name, space.number
        record.geometry = _local(space.polygon)
        space_ids.append(record.id)
        if ws.effective(record)["type"] == "unspecified":
            report.unspecified.append(record.id)

    matched = {r.id for r in pairs.values()}
    for r in existing:
        if r.id not in matched:
            _retire(r, now, report)

    _apply_doors(ws, floor_id, building_id, ex, space_ids, now, report)

    floor.outline = _local(ex.outline) if ex.outline is not None else None
    floor.converted_at = now
    return report


def _local(geom) -> dict:
    """GeoJSON geometry in local meters, rounded to 0.1 mm."""
    return mapping(set_precision(geom, 1e-4))


def _retire(record: ObjectRecord, now, report: ConversionReport) -> None:
    record.status = "retired"
    record.retired_at = now
    report.retired.append(record.id)


def _iou(a, b) -> float:
    if not a.intersects(b):
        return 0.0
    inter = a.intersection(b).area
    return inter / (a.area + b.area - inter)


def _match_spaces(existing: list[ObjectRecord], spaces: list[ExtractedSpace]) -> dict[int, ObjectRecord]:
    """One-to-one matching, best candidates first."""
    old_shapes = [shape(r.geometry) for r in existing]
    candidates = []
    for i, s in enumerate(spaces):
        for j, r in enumerate(existing):
            iou = _iou(s.polygon, old_shapes[j])
            same_number = bool(s.number) and s.number == r.number
            if iou >= MATCH_MIN_IOU or (same_number and iou >= MATCH_MIN_IOU_SAME_NUMBER):
                candidates.append((iou + (1.0 if same_number else 0.0), i, j))
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
                and _iou(space.polygon, shape(r.geometry)) >= VERTICAL_MIN_IOU
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


def _apply_doors(ws, floor_id, building_id, ex: FloorExtraction, space_ids, now, report) -> None:
    existing = [r for r in ws.floor_objects(floor_id) if r.kind == "opening"]
    old_points = [shape(r.geometry) for r in existing]
    used: set[int] = set()
    for door in ex.doors:
        best, best_d = None, DOOR_MATCH_DISTANCE
        for j, p in enumerate(old_points):
            d = door.point.distance(p)
            if j not in used and d <= best_d:
                best, best_d = j, d
        if best is None:
            record_id = _allocate_id(ws, floor_id, building_id)
            record = ObjectRecord(id=record_id, kind="opening", type="door", geometry={})
            ws.objects[record_id] = record
            report.added.append(record_id)
        else:
            used.add(best)
            record = existing[best]
            report.kept.append(record.id)
        record.type, record.type_source = "door", "layer"
        record.geometry = mapping(Point(round(door.point.x, 4), round(door.point.y, 4)))
        record.connects = [space_ids[i] for i in door.connects]
    for j, r in enumerate(existing):
        if j not in used:
            _retire(r, now, report)
