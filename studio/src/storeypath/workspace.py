"""The workspace file (*.spproj): one project and everything needed to
re-convert and re-export it with the same IDs.

It holds the location → building → floor tree, the source drawing for each
floor, the building placements on the map, the registry of every object ID ever
issued (active and retired), the user's corrections and the export history.
Geometry is stored in local drawing coordinates, scaled to meters.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .ids import (
    child_id,
    default_floor_code,
    format_object_code,
    generate_project_code,
    make_id,
    parse_id,
    validate_segment,
)
from .types import SpaceType

WORKSPACE_FORMAT = "storeypath-workspace"
WORKSPACE_VERSION = 1
DEFAULT_FLOOR_HEIGHT = 3.5


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Placement(BaseModel):
    """Puts a building's drawing on the map.

    The drawing point (``x``, ``y``), in meters, sits at (``lon``, ``lat``), and the
    drawing's +Y axis points to compass ``bearing`` (degrees clockwise from north).
    """

    lon: float
    lat: float
    x: float = 0.0
    y: float = 0.0
    bearing: float = 0.0


class SourceDrawing(BaseModel):
    """Where a floor's plan comes from. One file may hold several floors side by
    side: ``region`` picks this floor's plan out of it, and ``offset`` moves it onto
    the other floors. Both are in drawing units."""

    path: str  # relative to the workspace file when possible
    profile: str = "auto"  # "auto": read from what is drawn; or a built-in name or YAML path
    units: str | None = None  # override the drawing's own unit setting
    region: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1
    offset: tuple[float, float] | None = None  # subtracted from every drawing point
    view: str | None = None  # title of the plan in the drawing, for people
    sha256: str | None = None  # of the file last converted


class Floor(BaseModel):
    code: str
    name: str
    ordinal: int
    elevation: float  # meters above the building's ground floor
    height: float = DEFAULT_FLOOR_HEIGHT
    parapet_height: float | None = None  # meters, the low walls around its terraces and balconies
    source: SourceDrawing | None = None
    outline: dict[str, Any] | None = None  # GeoJSON geometry, local meters
    converted_at: datetime | None = None
    method: str | None = None  # how the last conversion found spaces: outlines or walls
    warnings: list[str] = Field(default_factory=list)  # from the last conversion
    layers: list[str] = Field(default_factory=list)  # what each layer was read as (auto profile)
    walls: dict[str, Any] | None = None  # the walls as drawn, with door and window gaps (local meters)
    wall_thickness: float | None = None  # meters, typical
    # symbols spotted in the plan by a model (see symbols/), local meters, and what
    # they were spotted in: the drawing, region, offset and scale
    symbols: list[dict[str, Any]] | None = None
    symbols_key: str | None = None


class Building(BaseModel):
    code: str
    name: str
    placement: Placement | None = None
    floors: list[Floor] = Field(default_factory=list)
    next_object_seq: int = 1


class Location(BaseModel):
    code: str
    name: str
    address: str | None = None
    buildings: list[Building] = Field(default_factory=list)


class ObjectRecord(BaseModel):
    """One registered object. Retired records are kept so IDs are never reused."""

    id: str
    kind: Literal["space", "opening"]
    type: str  # detected type; a correction in Workspace.overrides wins over it
    type_source: str = "default"  # which rule produced the type
    name: str | None = None
    number: str | None = None
    geometry: dict[str, Any]  # GeoJSON geometry, local meters
    connects: list[str] = Field(default_factory=list)  # openings: the spaces they join
    span: list[list[float]] | None = None  # openings: jamb to jamb, local meters
    width: float | None = None  # openings: meters
    tag: str | None = None  # openings: the drawing's door or window tag (D4, W12)
    issues: list[str] = Field(default_factory=list)  # found on conversion, for review
    status: Literal["active", "retired"] = "active"
    created_at: datetime = Field(default_factory=utcnow)
    retired_at: datetime | None = None


class Override(BaseModel):
    """A correction made during review. Survives every re-conversion. One with no
    values set records that a person checked the object and accepted it."""

    type: SpaceType | None = None
    name: str | None = None
    number: str | None = None
    # Kept with its ID either way. Hidden: real, but not shown unless asked for (a
    # shaft, a plant room). Ignored: not worth anything (a sliver, a pocket); leave it out.
    hidden: bool | None = None
    ignored: bool | None = None


class Reading(BaseModel):
    """What a text in a drawing was read as: a room name of a type, or not a room
    name (``type`` None). Kept so that converting again gives the same answer."""

    type: SpaceType | None = None  # None: not a room name
    source: Literal["rules", "model", "person"] = "model"
    rooms_only: bool = False  # asked knowing the text labels a room
    asked: str | None = None  # a model's answer: the model and question it came from


class ExportRecord(BaseModel):
    sequence: int
    exported_at: datetime
    file: str
    objects: dict[str, str]  # ID -> content hash, used for the next export's change list


class Project(BaseModel):
    code: str
    name: str
    created_at: datetime = Field(default_factory=utcnow)


class Workspace(BaseModel):
    format: Literal["storeypath-workspace"] = WORKSPACE_FORMAT
    format_version: int = WORKSPACE_VERSION
    project: Project
    locations: list[Location] = Field(default_factory=list)
    objects: dict[str, ObjectRecord] = Field(default_factory=dict)
    overrides: dict[str, Override] = Field(default_factory=dict)
    readings: dict[str, Reading] = Field(default_factory=dict)  # text → what it means
    exports: list[ExportRecord] = Field(default_factory=list)

    # ---- files -------------------------------------------------------------

    @classmethod
    def new(cls, name: str) -> Workspace:
        return cls(project=Project(code=generate_project_code(), name=name))

    @classmethod
    def load(cls, path: str | Path) -> Workspace:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        data = self.model_dump(mode="json", exclude_none=True)
        Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    def save_as_new_project(self, path: str | Path, name: str) -> Workspace:
        """A copy that is a *different* project: new project code, fresh history."""
        copy = Workspace.model_validate(self.model_dump())
        old, new = self.project.code, generate_project_code()

        def recode(value: str) -> str:
            return new + value[len(old):]

        copy.project = Project(code=new, name=name)
        copy.objects = {
            recode(i): r.model_copy(
                update={"id": recode(i), "connects": [recode(c) for c in r.connects]}
            )
            for i, r in copy.objects.items()
        }
        copy.overrides = {recode(i): o for i, o in copy.overrides.items()}
        copy.exports = []
        copy.save(path)
        return copy

    # ---- tree --------------------------------------------------------------

    @property
    def id(self) -> str:
        return self.project.code

    def location(self, location_id: str) -> Location:
        p = parse_id(location_id)
        for loc in self.locations:
            if make_id(self.id, loc.code) == str(p):
                return loc
        raise KeyError(f"no location {location_id}")

    def building(self, building_id: str) -> Building:
        p = parse_id(building_id)
        loc = self.location(p.prefix("location"))
        for b in loc.buildings:
            if b.code == p.segments[2] and len(p.segments) == 3:
                return b
        raise KeyError(f"no building {building_id}")

    def floor(self, floor_id: str) -> Floor:
        p = parse_id(floor_id)
        b = self.building(p.prefix("building"))
        for f in b.floors:
            if f.code == p.segments[3] and len(p.segments) == 4:
                return f
        raise KeyError(f"no floor {floor_id}")

    def iter_floors(self):
        """Yield (location, building, floor, floor_id) for every floor."""
        for loc in self.locations:
            for b in loc.buildings:
                for f in sorted(b.floors, key=lambda f: f.ordinal):
                    yield loc, b, f, make_id(self.id, loc.code, b.code, f.code)

    def add_location(self, code: str, name: str, address: str | None = None) -> str:
        validate_segment(code)
        if any(loc.code == code for loc in self.locations):
            raise ValueError(f"location code {code} already used in this project")
        self.locations.append(Location(code=code, name=name, address=address))
        return make_id(self.id, code)

    def add_building(self, location_id: str, code: str, name: str) -> str:
        validate_segment(code)
        loc = self.location(location_id)
        if any(b.code == code for b in loc.buildings):
            raise ValueError(f"building code {code} already used in {location_id}")
        loc.buildings.append(Building(code=code, name=name))
        return child_id(location_id, code)

    def add_floor(
        self,
        building_id: str,
        ordinal: int,
        *,
        code: str | None = None,
        name: str | None = None,
        elevation: float | None = None,
        height: float = DEFAULT_FLOOR_HEIGHT,
        parapet_height: float | None = None,
        source: SourceDrawing | None = None,
    ) -> str:
        b = self.building(building_id)
        code = validate_segment(code or default_floor_code(ordinal))
        if any(f.code == code for f in b.floors):
            raise ValueError(f"floor code {code} already used in {building_id}")
        if any(f.ordinal == ordinal for f in b.floors):
            raise ValueError(f"floor ordinal {ordinal} already used in {building_id}")
        b.floors.append(
            Floor(
                code=code,
                name=name or f"Floor {ordinal}",
                ordinal=ordinal,
                elevation=ordinal * height if elevation is None else elevation,
                height=height,
                parapet_height=parapet_height,
                source=source,
            )
        )
        return child_id(building_id, code)

    def restack(self, building_id: str) -> None:
        """Each floor's elevation from the heights of the floors below it (and above
        it, below ground), with the ground floor at 0. A missing floor is taken to be
        as high as the one below it."""
        floors = sorted(self.building(building_id).floors, key=lambda f: f.ordinal)
        above = [f for f in floors if f.ordinal >= 0]
        below = [f for f in floors if f.ordinal < 0][::-1]
        prev = None
        for f in above:
            f.elevation = round(f.ordinal * f.height if prev is None
                                else prev.elevation + prev.height * (f.ordinal - prev.ordinal), 3)
            prev = f
        top = above[0] if above and above[0].ordinal == 0 else None
        for f in below:
            f.elevation = round((top.elevation if top else 0.0) - f.height * (top.ordinal - f.ordinal if top else -f.ordinal), 3)
            top = f

    # ---- objects -----------------------------------------------------------

    def allocate_object_code(self, building_id: str) -> str:
        b = self.building(building_id)
        code = format_object_code(b.next_object_seq)
        b.next_object_seq += 1
        return code

    def floor_objects(self, floor_id: str, *, include_retired: bool = False) -> list[ObjectRecord]:
        prefix = floor_id + "-"
        return [
            r
            for i, r in self.objects.items()
            if i.startswith(prefix) and (include_retired or r.status == "active")
        ]

    def effective(self, record: ObjectRecord) -> dict[str, Any]:
        """Type, name and number after applying the user's corrections. A name or
        number corrected to "" removes it."""
        o = self.overrides.get(record.id)
        return {
            "type": (o.type if o and o.type else record.type),
            "name": ((o.name or None) if o and o.name is not None else record.name),
            "number": ((o.number or None) if o and o.number is not None else record.number),
            "corrected": o is not None,
            "hidden": bool(o and o.hidden),
            "ignored": bool(o and o.ignored),
        }

    def review_reasons(self, record: ObjectRecord) -> list[str]:
        """Why a space needs a person to look at it; empty when it does not. Once
        corrected or accepted, only a missing type keeps it on the list; hidden and
        ignored spaces are off it."""
        if record.kind != "space" or record.status != "active":
            return []
        eff = self.effective(record)
        if eff["hidden"] or eff["ignored"]:
            return []
        reasons = ["no type"] if eff["type"] == "unspecified" else []
        if not eff["corrected"]:
            if not eff["name"] and not eff["number"]:
                reasons.append("no name or number")
            reasons.extend(record.issues)
        return reasons
