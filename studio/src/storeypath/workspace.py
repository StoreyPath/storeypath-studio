"""The workspace file (*.spproj): one project and everything needed to
re-convert and re-export it with the same IDs.

It holds the location → building → floor tree, the source drawing for each
floor, the building placements on the map, the registry of every object ID ever
issued (active and retired), the user's corrections and the export history.
Geometry is stored in local drawing coordinates, scaled to meters.
"""

from __future__ import annotations

import math

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .ids import (
    LAND_CODE,
    LAYOUT_CODE,
    child_id,
    default_floor_code,
    default_mezzanine_code,
    default_roof_code,
    format_object_code,
    generate_project_code,
    is_item_id,
    make_id,
    new_item_id,
    parse_id,
    validate_segment,
)
from .types import FloorType, SpaceType

WORKSPACE_FORMAT = "storeypath-workspace"
WORKSPACE_VERSION = 1
DEFAULT_FLOOR_HEIGHT = 3.5
MEZZANINE_NAME = "Mezzanine"
ROOF_NAME = "Roof"
LAND_NAME = "Land"  # a location's land (format 0.12), and its one level, its layout
LAYOUT_NAME = "Layout"
BOUNDARY_WALL_HEIGHT = 2.4  # metres: a layout's boundary walls, when nothing says
MIN_ABOVE_HOST = 2.4  # metres: a mezzanine's default level above its host floor, at least


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


class DrawnOpening(BaseModel):
    type: Literal["door", "window", "opening"]  # opening: a way through with no door
    span: list[list[float]]  # jamb to jamb, local meters
    sill: float | None = None  # metres above the floor, and its height, when given in review
    height: float | None = None
    # a door's leaf when open, as a drawing's swing is read: [hinge, its free edge], local
    # meters (the hinge on a jamb; which side of the wall it opens into); None: not given
    swing: list[list[float]] | None = None


class ResizedOpening(BaseModel):
    """A door, window or opening of the drawing given another size or type, or moved along its
    wall, in review. It is
    found again, at every conversion, by where it is: its middle (which a new width
    keeps)."""

    at: list[float]  # [x, y], local meters
    width: float | None = None  # jamb to jamb; None: as the drawing has it
    sill: float | None = None  # above the floor; None: as the drawing (its schedule) has it
    height: float | None = None
    # what it is: a door, a window or an opening (a way through, no door), where the drawing
    # is read wrong (a glazed door drawn like a window); None: as read
    type: Literal["door", "window", "opening"] | None = None
    # where its middle was moved to along its wall (local meters); None: where the drawing has it
    to: list[float] | None = None
    # a door's leaf when open, set in review: [hinge, its free edge] where it is now (local
    # meters); hung again on its nearest jamb, the same way, if its size or place changes after
    swing: list[list[float]] | None = None


class FloorEdits(BaseModel):
    """What a person drew on a floor in review, kept through every conversion: walls
    the drawing leaves out (they part spaces as drawn walls do), doors, windows and
    openings it does not show, lines dividing a space where there is no wall (its
    zones), the drawing's openings given another size, and spaces drawn where the
    drawing encloses none."""

    walls: list[list[list[float]]] = Field(default_factory=list)  # each [[x, y], [x, y]], local meters
    openings: list[DrawnOpening] = Field(default_factory=list)
    dividers: list[list[list[float]]] = Field(default_factory=list)  # each [[x, y], [x, y]], local meters
    resized: list[ResizedOpening] = Field(default_factory=list)  # openings of the drawing, another size
    # areas a person drew as spaces where the drawing encloses none (a colonnade
    # between columns): each a ring [[x, y], …], local meters
    spaces: list[list[list[float]]] = Field(default_factory=list)


class NorthRead(BaseModel):
    """What a site plan's north arrow was read as (a layout's level, format 0.12): the
    bearing the plan's up faces, degrees clockwise from north; how sure, 0 to 1; what it
    was read from, in words."""

    bearing: float
    sure: float = 0.5
    how: str = ""


class Floor(BaseModel):
    code: str
    name: str
    ordinal: int  # 0 the ground floor, 1 the first, -1 the first basement; a mezzanine: its host's
    elevation: float  # meters above the building's ground floor
    height: float = DEFAULT_FLOOR_HEIGHT
    # What level of its building it is (format 0.11): a floor (a storey), a mezzanine or
    # the roof. A roof is the building's top: open air, the rooms standing on it indoor,
    # its parapets its edge; no floor stands above it.
    type: FloorType = FloorType.FLOOR
    # A mezzanine: a floor standing in another floor of its building, over part of it. The
    # code of that floor (its host: one of the building's floors of type floor), and how
    # far above that floor's level its own is (metres): its elevation is the host's and
    # that. None for any other level.
    host: str | None = None
    above_host: float | None = None
    parapet_height: float | None = None  # meters, the low walls around its terraces, balconies and roof
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
    edits: FloorEdits = Field(default_factory=FloorEdits)  # drawn by a person in review
    # a layout's level (format 0.12): what its site plan's north arrow was read as; None:
    # none found (or a floor of a building)
    north: NorthRead | None = None

    @property
    def mezzanine(self) -> bool:
        return self.type == FloorType.MEZZANINE

    @property
    def roof(self) -> bool:
        return self.type == FloorType.ROOF

    @property
    def layout(self) -> bool:
        """A location's layout (its land's level), not a floor of a building."""
        return self.type == FloorType.LAYOUT


def floor_order(f: Floor) -> tuple:
    """Where a floor comes in its building, lowest first: by ordinal, then elevation (a
    mezzanine after the floor it stands in, before the next one up), then code."""
    return f.ordinal, f.elevation, f.code


def default_above_host(host_height: float) -> float:
    """How far above its host floor a mezzanine stands when nothing says: half the host's
    floor-to-floor height, at least MIN_ABOVE_HOST."""
    return round(max(host_height / 2, MIN_ABOVE_HOST), 2)


class SitePosition(BaseModel):
    """Where a building stands on its location's site plan: around a centre of the
    site's own, not on the map. The building's point ``pivot`` (its middle, in its
    own drawing metres) sits ``x``, ``y`` metres from the site's centre, the
    building turned ``rotation`` degrees clockwise. Placing the site on the map
    (Location.placement) places every building on it."""

    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    pivot: tuple[float, float] = (0.0, 0.0)
    # the footprint of its location's layout it was put on (format 0.12: its ID), as a person
    # applied it; None: placed by hand, or as drawn. A layout read again keeps it.
    footprint: str | None = None


class Building(BaseModel):
    code: str
    name: str
    placement: Placement | None = None  # on the map by itself: wins over its site position
    site: SitePosition | None = None  # on its location's site plan (None: as its drawing places it)
    floors: list[Floor] = Field(default_factory=list)
    next_object_seq: int = 1


class North(BaseModel):
    """Which way north is on a location's site plan (format 0.12): the bearing its up
    faces, degrees clockwise from north, as its layout's north arrow says (``drawing``)
    or a person set it (``person``), and how sure a reading was (0 to 1). On the map the
    site's placement has the same bearing: setting one sets both (site_bearing)."""

    bearing: float
    source: Literal["drawing", "person"] = "person"
    sure: float | None = None


class Location(BaseModel):
    code: str
    name: str
    address: str | None = None
    # the site on the map: its centre (x, y unused) at lon, lat, its up at bearing
    placement: Placement | None = None
    # the drawing point (metres) at the site's centre for buildings that stand as drawn:
    # set when buildings are first given positions, so the others stay where they are
    site_origin: tuple[float, float] | None = None
    buildings: list[Building] = Field(default_factory=list)
    # its land (format 0.12): kept as a building is, code LAND, apart from its buildings
    # (never one of them), its one level its layout (G00): the plot, its boundary walls and
    # gates, its areas, the footprints of its buildings, what is placed on it; in the site's
    # frame. None: no site plan read as its layout yet
    layout: Building | None = None
    # which way north is on its site plan (see North); None: not known (up is north)
    north: North | None = None


def site_bearing(loc: Location) -> float:
    """The compass bearing of a location's site plan's up: its placement's on the map,
    else its north's (read from its layout's north arrow, or set by a person), else 0."""
    if loc.placement is not None:
        return loc.placement.bearing
    return loc.north.bearing if loc.north is not None else 0.0


class ObjectRecord(BaseModel):
    """One registered object. Retired records are kept so IDs are never reused."""

    id: str
    # a footprint (format 0.12): a building's outline standing on a location's layout
    kind: Literal["space", "zone", "opening", "footprint"]
    type: str  # detected type; a correction in Workspace.overrides wins over it
    type_source: str = "default"  # which rule produced the type
    name: str | None = None
    number: str | None = None
    # spaces and zones: the text in it as the drawing writes it (never corrected): a key
    # other systems can match on, beside the ID
    label: str | None = None
    geometry: dict[str, Any]  # GeoJSON geometry, local meters
    connects: list[str] = Field(default_factory=list)  # openings: the spaces they join
    parent: str | None = None  # zones: the space they are part of
    zones: list[str] = Field(default_factory=list)  # spaces: the zones they are divided into
    span: list[list[float]] | None = None  # openings: jamb to jamb, local meters
    width: float | None = None  # openings: meters
    swings: list[list[list[float]]] | None = None  # doors: each leaf [hinge, free edge when open], local meters
    sill: float | None = None  # openings: meters above the floor, from the drawing's schedule
    height: float | None = None  # openings: meters, from the drawing's schedule
    tag: str | None = None  # openings: the drawing's door or window tag (D4, W12)
    issues: list[str] = Field(default_factory=list)  # found on conversion, for review
    detected_ignored: bool = False  # judged not a room on conversion (vision); a correction wins
    # footprints: the building of the location it was matched to when the layout was read
    # (layout.py): {building, confidence, by, label, shape, site: {x, y, rotation, pivot}};
    # None: none. A person's choice (Override.building) wins over it
    match: dict[str, Any] | None = None
    # footprints: the levels its site plan writes (format 0.13): its floor's (FFL, in it) and
    # the ground's nearest it (FGL, NGL), metres, either None when not written; None: neither.
    # How far its floor stands above the ground at its doors: a step into it, where both are known
    levels: dict[str, float | None] | None = None
    status: Literal["active", "retired"] = "active"
    created_at: datetime = Field(default_factory=utcnow)
    retired_at: datetime | None = None


class WallPaint(BaseModel):
    """One wall of a room finished otherwise than the rest (format 0.9.3): ``line`` along
    the wall's face towards the room, two points in metres of the building's own frame
    (as an item's place), and the ``finish`` it is in (a WALL-… code)."""

    line: list[list[float]] = Field(min_length=2, max_length=2)
    finish: str = Field(max_length=40)

    @field_validator("line")
    @classmethod
    def _line(cls, v):
        if any(len(p) != 2 or not all(math.isfinite(c) for c in p) for p in v) or v[0] == v[1]:
            raise ValueError("a wall's line is two different points, [x, y] each")
        return [[round(float(c), 4) for c in p] for p in v]


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
    # how many people it is meant to seat, set by a person; None: as its desks say
    capacity: int | None = Field(None, ge=0, le=10000)
    # a lift's, stairs' or escalator's link to the same one on other floors (stacks.py),
    # set by a person: the ID of a space on another floor it is linked with, or "" for
    # linked with none; None: linked as found (same object code, or footprints overlapping)
    stack: str | None = Field(None, max_length=100)
    # what its floor and (a space's) walls are finished in (format 0.9): codes of
    # spec/finishes.json (finishes.py); None: its type's default
    floor_finish: str | None = Field(None, max_length=40)
    wall_finish: str | None = Field(None, max_length=40)
    # (a space's) walls finished otherwise than the rest, one by one (format 0.9.3); None: none
    wall_finishes: list[WallPaint] | None = Field(None, max_length=200)
    # (a footprint's, format 0.12) the building of its location it is, chosen by a person: its
    # ID, or "" for none (no building of the location: skipped); None: as matched
    building: str | None = Field(None, max_length=100)


class Reading(BaseModel):
    """What a text in a drawing was read as: a room name of a type, or not a room
    name (``type`` None). Kept so that converting again gives the same answer."""

    type: SpaceType | None = None  # None: not a room name
    source: Literal["rules", "model", "person"] = "model"
    rooms_only: bool = False  # asked knowing the text labels a room
    asked: str | None = None  # a model's answer: the model and question it came from


class LastPackage(BaseModel):
    """A building as the last package that held it had it: what the building's next
    package lists its changes against."""

    sequence: int  # that package's export number
    # ID -> content hash (export.compared: in the building's own frame) of everything it
    # held: its location, the building, its floors, spaces, zones, openings and items
    objects: dict[str, str] = Field(default_factory=dict)
    retired: list[str] = Field(default_factory=list)  # the IDs its all_retired listed


class ExportRecord(BaseModel):
    sequence: int
    exported_at: datetime
    file: str
    buildings: list[str] | None = None  # the package held only these buildings
    # each building of the project as the last package that held it had it, as of this
    # export (building ID -> it); None in a record of a Studio before this was kept: it is
    # then worked out from objects and places (export.last_packages)
    held: dict[str, LastPackage] | None = None
    # every item ID a package of the project has held, as of this export: an item new to
    # them is added; one they have held, carried into a building, is changed
    items_held: list[str] | None = None
    # before held: the whole project as last exported (ID -> content hash), and each item's
    # building when last exported (item ID -> building ID)
    objects: dict[str, str] = Field(default_factory=dict)
    places: dict[str, str] = Field(default_factory=dict)


class Project(BaseModel):
    code: str
    name: str
    created_at: datetime = Field(default_factory=utcnow)


class Item(BaseModel):
    """A piece of furniture or equipment placed on a floor (catalogue.py: its type).
    Its ID is an asset's tag (ids.new_item_id), not its place nor its project's:
    carried to another room, floor or building, it keeps it. Taken away, it is
    retired, and its ID is never issued again."""

    id: str  # 7K2Q-XM9F-4DP
    type: str  # catalogue type code
    floor_id: str
    x: float  # local metres, the floor's frame (as the spaces)
    y: float
    rotation: float = 0.0  # degrees, counter-clockwise: the way its front faces, from the plan's +x
    values: dict[str, str | float] = Field(default_factory=dict)  # its StoreyPath fields
    # the group it was placed or grouped with (a workstation's desk, chair and pedestal; a
    # meeting table and its chairs; format 0.10): a key its group's items share; None: none
    group: str | None = None
    # the door it serves, of a type that serves one (a card reader, a door controller; format
    # 0.10): the door's ID, an opening of its floor; None: none
    door_id: str | None = None
    status: Literal["active", "retired"] = "active"
    created_at: datetime = Field(default_factory=utcnow)
    retired_at: datetime | None = None


class FurnishingPiece(BaseModel):
    """One item of a furniture layout: its type, where it stands from the corner of the
    room it was taken from (the room's box, its least x and y: local metres) and its turn."""

    type: str  # catalogue type code
    x: float
    y: float
    rotation: float = 0.0  # degrees, counter-clockwise, as an item's


class Furnishing(BaseModel):
    """A furniture layout of the project: the items of a room, kept to be placed again in
    any room of it (review.py save_furnishing, place_items). Its pieces stand from the
    corner of the room's box; ``width`` and ``depth`` are that box's size."""

    id: str  # L and eight symbols (Crockford's base32)
    name: str
    floor_id: str  # the floor it was taken from
    room: str | None = None  # the room it was taken from, as named then
    width: float
    depth: float
    pieces: list[FurnishingPiece] = Field(default_factory=list)
    created_by: str | None = None  # who saved it, as named then
    created_at: datetime = Field(default_factory=utcnow)


class Workspace(BaseModel):
    format: Literal["storeypath-workspace"] = WORKSPACE_FORMAT
    format_version: int = WORKSPACE_VERSION
    project: Project
    locations: list[Location] = Field(default_factory=list)
    objects: dict[str, ObjectRecord] = Field(default_factory=dict)
    overrides: dict[str, Override] = Field(default_factory=dict)
    readings: dict[str, Reading] = Field(default_factory=dict)  # text → what it means
    vision: dict[str, dict[str, Any]] = Field(default_factory=dict)  # what the vision model saw, per room shape
    exports: list[ExportRecord] = Field(default_factory=list)
    items: dict[str, Item] = Field(default_factory=dict)  # furniture and equipment, by ID
    furnishings: dict[str, Furnishing] = Field(default_factory=dict)  # furniture layouts, by ID

    # ---- files -------------------------------------------------------------

    @classmethod
    def new(cls, name: str) -> Workspace:
        return cls(project=Project(code=generate_project_code(), name=name))

    @classmethod
    def load(cls, path: str | Path) -> Workspace:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> None:
        """Written beside it, then put in its place: a page reading the project while
        a job saves it sees the old file or the new one, never half of one."""
        path = Path(path)
        data = self.model_dump(mode="json", exclude_none=True)
        part = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.part")
        part.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(part, path)

    def save_as_new_project(self, path: str | Path, name: str) -> Workspace:
        """A copy that is a *different* project: new project code, fresh history. Every
        ID is the new project's: its objects', and its items' (each a new tag: an asset
        is one project's) on the new project's floors. What texts were read as is kept
        (it is by text, not by ID)."""
        copy = Workspace.model_validate(self.model_dump())
        old, new = self.project.code, generate_project_code()

        def recode(value: str) -> str:
            return new + value[len(old):]

        copy.project = Project(code=new, name=name)
        copy.objects = {
            recode(i): r.model_copy(
                update={"id": recode(i), "connects": [recode(c) for c in r.connects],
                        "parent": recode(r.parent) if r.parent else None, "zones": [recode(z) for z in r.zones]}
            )
            for i, r in copy.objects.items()
        }
        copy.overrides = {recode(i): o for i, o in copy.overrides.items()}
        items, copy.items = copy.items, {}
        for it in items.values():
            new_id = copy.new_item_id(lambda i: i in items)
            copy.items[new_id] = it.model_copy(update={"id": new_id,
                                                       "floor_id": recode(it.floor_id) if it.floor_id else ""})
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
        """A building by its ID; the land of a location (its layout's, format 0.12) by its
        ID too (PROJECT-LOCATION-LAND), kept as a building is but none of them."""
        p = parse_id(building_id)
        loc = self.location(p.prefix("location"))
        if len(p.segments) == 3 and p.segments[2] == LAND_CODE and loc.layout is not None:
            return loc.layout
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

    def iter_floors(self, layouts: bool = False):
        """Yield (location, building, floor, floor_id) for every floor of every building;
        with ``layouts``, each location's layout after its buildings' floors (its land as
        the building, its level as the floor)."""
        for loc in self.locations:
            for b in loc.buildings:
                for f in sorted(b.floors, key=floor_order):
                    yield loc, b, f, make_id(self.id, loc.code, b.code, f.code)
            if layouts:
                yield from self._layout_of(loc)

    def iter_layouts(self):
        """Yield (location, its land, its layout's level, the layout's ID) for every
        location that has a layout (format 0.12)."""
        for loc in self.locations:
            yield from self._layout_of(loc)

    def _layout_of(self, loc: Location):
        if loc.layout is not None:
            for f in loc.layout.floors:
                yield loc, loc.layout, f, make_id(self.id, loc.code, LAND_CODE, f.code)

    def layout_of(self, location_id: str) -> Floor | None:
        """A location's layout (its land's level), or None."""
        loc = self.location(location_id)
        return loc.layout.floors[0] if loc.layout is not None and loc.layout.floors else None

    def add_layout(self, location_id: str, source: SourceDrawing | None = None, *, name: str | None = None,
                   wall_height: float = BOUNDARY_WALL_HEIGHT) -> str:
        """A location's layout (format 0.12), read from ``source`` (a site plan): its land
        (code LAND) and the land's one level (G00). The ID of the layout. One a location
        has already is given the new source instead (its objects keep their IDs)."""
        loc = self.location(location_id)
        if loc.layout is None:
            loc.layout = Building(code=LAND_CODE, name=LAND_NAME)
        if not loc.layout.floors:
            loc.layout.floors.append(Floor(code=LAYOUT_CODE, name=name or LAYOUT_NAME, ordinal=0, elevation=0.0,
                                           height=wall_height, type=FloorType.LAYOUT, source=source))
        else:
            f = loc.layout.floors[0]
            f.source = source if source is not None else f.source
            if name:
                f.name = name
        return make_id(self.id, loc.code, LAND_CODE, loc.layout.floors[0].code)

    def add_location(self, code: str, name: str, address: str | None = None) -> str:
        validate_segment(code)
        if any(loc.code == code for loc in self.locations):
            raise ValueError(f"location code {code} already used in this project")
        self.locations.append(Location(code=code, name=name, address=address))
        return make_id(self.id, code)

    def add_building(self, location_id: str, code: str, name: str) -> str:
        validate_segment(code)
        if code == LAND_CODE:
            raise ValueError(f"{LAND_CODE} is a location's land (its layout): choose another building code")
        loc = self.location(location_id)
        if any(b.code == code for b in loc.buildings):
            raise ValueError(f"building code {code} already used in {location_id}")
        if is_item_id(f"{location_id}-{code}"):  # (only of a project whose code is four symbols)
            raise ValueError(f"{location_id}-{code} would read as an item's ID: choose another building code")
        if loc.placement is not None:  # on the map: its buildings stay where they are on it
            from .export import settle

            settle(loc)
        loc.buildings.append(Building(code=code, name=name))
        return child_id(location_id, code)

    def add_floor(
        self,
        building_id: str,
        ordinal: int | None,
        *,
        code: str | None = None,
        name: str | None = None,
        elevation: float | None = None,
        height: float = DEFAULT_FLOOR_HEIGHT,
        parapet_height: float | None = None,
        source: SourceDrawing | None = None,
        type: FloorType | str = FloorType.FLOOR,
        above_host: float | None = None,
    ) -> str:
        """A level added to a building, of its ``type``: a floor, one per ordinal; a
        mezzanine, standing in the building's floor of that ordinal (its host),
        ``above_host`` metres above it (by default, half its height), any number of them on
        a floor; or a roof, above every floor (``ordinal`` None: the next one up). Codes are
        the building's own: F00, F01, B01 by ordinal; M00 for a mezzanine over F00, M00B
        for a second; RF for the roof, RF2 for a second. A roof stays its building's top: a
        floor added at or above it puts the roofs above it (ordinals are no part of IDs)."""
        b = self.building(building_id)
        level = FloorType(type)
        if level == FloorType.LAYOUT or b.code == LAND_CODE:
            raise ValueError("a location's layout is added as its layout (add_layout), not as a floor")
        taken = {f.code for f in b.floors}
        if level != FloorType.MEZZANINE and above_host is not None:
            raise ValueError("above_host is a mezzanine's")
        if ordinal is None and level != FloorType.ROOF:
            raise ValueError(f"a {level.value}'s ordinal: 0 the ground floor, 1 the first, -1 the first basement")
        host = None
        lift = 0  # how far the building's roofs go up, to stay above a floor added
        if level == FloorType.MEZZANINE:
            host = next((f for f in b.floors if f.ordinal == ordinal and f.type == FloorType.FLOOR), None)
            if host is None:
                raise ValueError(f"a mezzanine stands in a floor: {building_id} has no floor {ordinal}")
            if elevation is not None:
                raise ValueError("a mezzanine's elevation is its host floor's and above_host")
            above_host = default_above_host(host.height) if above_host is None else float(above_host)
            if not math.isfinite(above_host) or above_host <= 0:
                raise ValueError("a mezzanine stands above its host floor: above_host is metres, more than 0")
            code = validate_segment(code or default_mezzanine_code(ordinal, taken))
        elif level == FloorType.ROOF:
            top = max((f for f in b.floors if f.type == FloorType.FLOOR), key=lambda f: f.ordinal, default=None)
            if ordinal is None:
                ordinal = max((f.ordinal + 1 for f in b.floors if not f.mezzanine), default=0)
            if top is not None and ordinal <= top.ordinal:
                raise ValueError(f"a roof is its building's top: {building_id} has {top.name} ({top.code}) at floor "
                                 f"{top.ordinal}, so its roof is {top.ordinal + 1} or more")
            code = validate_segment(code or default_roof_code(taken))
            if any(f.ordinal == ordinal and not f.mezzanine for f in b.floors):
                raise ValueError(f"floor ordinal {ordinal} already used in {building_id}")
        else:
            code = validate_segment(code or default_floor_code(ordinal))
            if any(f.ordinal == ordinal and f.type == FloorType.FLOOR for f in b.floors):
                raise ValueError(f"floor ordinal {ordinal} already used in {building_id}")
            lowest_roof = min((f.ordinal for f in b.floors if f.roof), default=None)
            if lowest_roof is not None and ordinal >= lowest_roof:
                lift = ordinal - lowest_roof + 1
        if code in taken:
            raise ValueError(f"floor code {code} already used in {building_id}")
        for r in (f for f in b.floors if f.roof) if lift else ():
            r.ordinal += lift
            r.elevation = round(r.elevation + lift * height, 3)  # (restack puts it exactly)
        b.floors.append(
            Floor(
                code=code,
                name=name or (MEZZANINE_NAME if level == FloorType.MEZZANINE
                              else ROOF_NAME if level == FloorType.ROOF else f"Floor {ordinal}"),
                ordinal=ordinal,
                elevation=(round(host.elevation + above_host, 3) if host is not None
                           else ordinal * height if elevation is None else elevation),
                height=height,
                type=level,
                parapet_height=parapet_height,
                source=source,
                host=host.code if host is not None else None,
                above_host=above_host if host is not None else None,
            )
        )
        return child_id(building_id, code)

    def host_of(self, floor_id: str) -> Floor | None:
        """The floor a mezzanine stands in (None for a floor that is not one)."""
        f = self.floor(floor_id)
        if not f.mezzanine:
            return None
        return next((h for h in self.building(floor_id.rsplit("-", 1)[0]).floors if h.code == f.host), None)

    def restack(self, building_id: str) -> None:
        """Each floor's elevation from the heights of the floors below it (and above
        it, below ground), with the ground floor at 0. A missing floor is taken to be
        as high as the one below it. A mezzanine stands its height above its host's
        level, wherever that is now."""
        b = self.building(building_id)
        floors = sorted((f for f in b.floors if not f.mezzanine), key=lambda f: f.ordinal)
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
        hosts = {f.code: f for f in floors}
        for f in b.floors:
            if f.mezzanine and f.host in hosts:
                f.elevation = round(hosts[f.host].elevation + (f.above_host or 0.0), 3)

    # ---- objects -----------------------------------------------------------

    def allocate_object_code(self, building_id: str) -> str:
        b = self.building(building_id)
        code = format_object_code(b.next_object_seq)
        b.next_object_seq += 1
        return code

    def add_item(self, type_code: str, floor_id: str, x: float, y: float, rotation: float = 0.0,
                 values: dict | None = None, taken=None) -> Item:
        """A new item on a floor, with a new ID: one no item of the project has, nor
        any ``taken(id)`` says is in use (in Studio's database)."""
        self.floor(floor_id)  # raises when there is no such floor
        item = Item(id=self.new_item_id(taken), type=type_code, floor_id=floor_id,
                    x=x, y=y, rotation=rotation % 360, values=dict(values or {}))
        self.items[item.id] = item
        return item

    def new_item_id(self, taken=None) -> str:
        """An ID for a new item: drawn again while an item of the project has it (or a
        package of the project held it: one carried to a building whose package was not
        opened here), or ``taken(id)`` says one elsewhere has."""
        held = set(self.exports[-1].items_held or []) if self.exports else set()
        return new_item_id(lambda i: i in self.items or i in held or (taken is not None and taken(i)))

    def floor_items(self, floor_id: str, *, include_retired: bool = False) -> list[Item]:
        return [i for i in self.items.values()
                if i.floor_id == floor_id and (include_retired or i.status == "active")]

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
            # corrected, or checked by a person: a correction that sets its type, name or
            # number, or nothing at all (accepted as it is); with a capacity (or a link to
            # other floors, or finishes), accepted as it is says so with hidden false
            # (nothing is hidden as detected). A capacity alone is not a check: how many a
            # room seats says nothing of what it is; nor a lift's link alone, nor finishes.
            "corrected": o is not None and (o.type is not None or o.name is not None or o.number is not None
                                            or (o.capacity is None and o.stack is None and o.floor_finish is None
                                                and o.wall_finish is None and not o.wall_finishes
                                                and o.building is None)
                                            or o.hidden is False),
            "hidden": bool(o and o.hidden),
            "ignored": o.ignored if o is not None and o.ignored is not None else record.detected_ignored,
            "capacity": o.capacity if o is not None else None,
            # what its floor and walls are finished in, set by a person (format 0.9); None: as its type
            "floor_finish": o.floor_finish if o is not None else None,
            "wall_finish": o.wall_finish if o is not None and record.kind == "space" else None,
            # its walls finished otherwise one by one (format 0.9.3): [{line, finish}]; [] none
            "wall_finishes": [w.model_dump() for w in o.wall_finishes or []]
            if o is not None and record.kind == "space" else [],
            # a footprint's building, as a person chose it (format 0.12): its ID, "" none; None as matched
            "building": o.building if o is not None and record.kind == "footprint" else None,
        }

    def review_reasons(self, record: ObjectRecord) -> list[str]:
        """Why a space needs a person to look at it; empty when it does not. Once
        corrected or accepted, only a missing type keeps it on the list; hidden and
        ignored spaces are off it."""
        if record.kind not in ("space", "zone") or record.status != "active":
            return []
        if record.zones:  # a space divided into zones is reviewed through them
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
