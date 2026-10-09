"""The exchange package (*.storeypath): models for every file in it.

These models are the format's source of truth: export writes them, validate
reads them, and the JSON Schemas shipped inside each package are generated from
them. See spec/FORMAT.md for the human-readable description.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field

from .types import OpeningType, SpaceType

FORMAT_NAME = "storeypath-package"
FORMAT_VERSION = "0.9.0"
FILE_EXTENSION = ".storeypath"
# one building per package from 0.7 (before: a whole project, or a part of it)
ONE_BUILDING_FROM = (0, 7)
# items' IDs are asset tags from 0.8 (7K2Q-XM9F-4DP; before: the project's code, -I and six digits)
ASSET_IDS_FROM = (0, 8)
# rooms' floor and wall finishes from 0.9 (spec/finishes.json)
FINISHES_FROM = (0, 9)

FILES = {
    "location": "location.geojson",
    "buildings": "buildings.geojson",
    "floors": "floors.geojson",
    "spaces": "spaces.geojson",
    "zones": "zones.geojson",
    "openings": "openings.geojson",
    "objects": "objects.csv",
    "changes": "changes.json",
    "items": "items.geojson",
    "catalogue": "catalogue.json",
    "navigation": "navigation.json",
}
OBJECTS_CSV_COLUMNS = [
    "id", "kind", "type", "name", "number", "project_id", "location_id",
    "building_id", "floor_id", "floor_ordinal", "area_m2", "lon", "lat", "hidden", "ignored", "space_id",
    "drawing_label",
]

LonLat = tuple[float, float]


class _Model(BaseModel):
    # Every number of a package is finite: NaN and Infinity are not JSON, and no
    # reader can place, draw or compare them.
    model_config = ConfigDict(allow_inf_nan=False)


class PointGeometry(_Model):
    type: Literal["Point"]
    coordinates: LonLat


class PolygonGeometry(_Model):
    type: Literal["Polygon"]
    coordinates: list[list[LonLat]]


class MultiPolygonGeometry(_Model):
    type: Literal["MultiPolygon"]
    coordinates: list[list[list[LonLat]]]


# what an area is drawn as: a space, a zone, a floor's outline, its walls, a building's
# footprint (an opening is a Point, an item's footprint a Polygon)
Polygonal = Annotated[Union[PolygonGeometry, MultiPolygonGeometry], Field(discriminator="type")]


class _Props(_Model):
    # Consumers must ignore properties they do not know: later format versions add some.
    model_config = ConfigDict(extra="allow")


class LocationProps(_Props):
    kind: Literal["location"]
    code: str
    name: str
    address: str | None = None
    project_id: str
    display_point: LonLat | None = None


class BuildingProps(_Props):
    kind: Literal["building"]
    code: str
    name: str
    location_id: str
    display_point: LonLat | None = None


class FloorProps(_Props):
    kind: Literal["floor"]
    code: str
    name: str
    building_id: str
    ordinal: int = Field(description="0 = ground floor, negative = below ground")
    elevation: float = Field(description="meters above the building's ground floor")
    height: float = Field(description="floor-to-floor height in meters")
    walls: Polygonal | None = Field(None, description="the walls as drawn, with their door and window gaps; "
                                                     "full height, except the parapets")
    wall_thickness_m: float | None = Field(None, description="the walls' typical thickness")
    parapets: Polygonal | None = Field(None, description="the low walls around terraces, balconies and roofs, "
                                                        "parapet_height_m high")
    parapet_height_m: float | None = Field(None, description="the parapets' height above the floor")


CAPACITY = ("how many people it is meant to seat (format 0.7): set in review, else the workplaces of the items "
            "standing in it (a desk: one); null when neither says")
CAPACITY_FROM = "what says so: review (a person set it) or items (its desks); null with no capacity"
GRADE = ("who it is laid out for (format 0.7): the highest grade among the desks standing in it "
         "(president, c_level, director, manager, section_head, senior, junior); null without one")
Grade = Literal["president", "c_level", "director", "manager", "section_head", "senior", "junior"]

DRAWING_LABEL = ("the text written in it on the drawing, as written (its lines joined by a line break; "
                 "never changed in review): a key to match on, beside the ID")
FLOOR_FINISH = ("what its floor is finished in (format 0.9): a code of StoreyPath's floor finishes (FORMAT.md, "
                "Finishes); null for its type's default (a zone's: its space's, else its type's). A reader that does "
                "not know a code shows the default")
WALL_FINISH = ("what its walls are finished in (format 0.9), on each wall's face towards it: a code of StoreyPath's "
               "wall finishes (FORMAT.md, Finishes); null for its type's default. A reader that does not know a code "
               "shows the default")
FloorFinish = Annotated[str, Field(pattern=r"^FLOOR(-[A-Z0-9]+)+$", max_length=40)]
WallFinish = Annotated[str, Field(pattern=r"^WALL(-[A-Z0-9]+)+$", max_length=40)]


class SpaceProps(_Props):
    kind: Literal["space"]
    type: SpaceType
    name: str | None = None
    number: str | None = None
    drawing_label: str | None = Field(None, description=DRAWING_LABEL)
    floor_id: str
    area_m2: float
    display_point: LonLat
    zones: list[str] = Field(default_factory=list, description=(
        "IDs of the zones this space is divided into, with no wall between them (empty: one use)"))
    outdoor: bool = Field(False, description=(
        "open to the sky: a terrace or balcony with no windows of its own (a glazed veranda is not)"))
    capacity: int | None = Field(None, ge=0, description=CAPACITY)
    capacity_from: Literal["review", "items"] | None = Field(None, description=CAPACITY_FROM)
    grade: Grade | None = Field(None, description=GRADE)
    stack: str | None = Field(None, description=(
        "a lift's, stairs', escalator's (or a ramp's between floors) stack (format 0.8): the same key on every "
        "floor the same one serves (the ID of its space on the lowest of them), no other's; null for any other space"))
    floor_finish: FloorFinish | None = Field(None, description=FLOOR_FINISH)
    wall_finish: WallFinish | None = Field(None, description=WALL_FINISH)
    hidden: bool = Field(False, description="real, but not shown unless asked for (a shaft, a plant room)")
    ignored: bool = Field(False, description="judged not worth anything by a person (a sliver, a pocket); leave it out")


class ZoneProps(_Props):
    kind: Literal["zone"]
    type: SpaceType
    name: str | None = None
    number: str | None = None
    drawing_label: str | None = Field(None, description=DRAWING_LABEL)
    space_id: str = Field(description="the space this zone is part of")
    floor_id: str
    area_m2: float
    display_point: LonLat
    capacity: int | None = Field(None, ge=0, description=CAPACITY)
    capacity_from: Literal["review", "items"] | None = Field(None, description=CAPACITY_FROM)
    grade: Grade | None = Field(None, description=GRADE)
    floor_finish: FloorFinish | None = Field(None, description=FLOOR_FINISH)
    hidden: bool = Field(False, description="real, but not shown unless asked for")
    ignored: bool = Field(False, description="judged not worth anything by a person; leave it out")


class OpeningProps(_Props):
    kind: Literal["opening"]
    type: OpeningType
    floor_id: str
    connects: list[str] = Field(description="IDs of the spaces this opening joins")
    exterior: bool = Field(description="true when it leads outside the floor")
    width_m: float | None = Field(None, description="clear width, jamb to jamb")
    span: list[LonLat] | None = Field(None, description="the opening across the wall, jamb to jamb")
    swings: list[list[LonLat]] | None = Field(
        None, description="a door's leaves as the plan draws them: each [hinge, its free edge when open]")
    sill_m: float | None = Field(None, description="how high above the floor it starts, from the drawing's schedule")
    height_m: float | None = Field(None, description="its height, from the drawing's schedule")
    hidden: bool = Field(False, description="real, but not shown unless asked for")
    ignored: bool = Field(False, description="judged not worth anything by a person; leave it out")


class ItemLocal(_Model):
    """Where an item stands in its building's own frame: what it is placed by. The
    building's position on the map never changes it."""

    x_m: float = Field(description="metres from the origin of the building's drawings, along their x")
    y_m: float = Field(description="metres from the origin of the building's drawings, along their y")
    rotation_deg: float = Field(description=(
        "the way its front faces, in degrees counter-clockwise: 0 the drawings' -y, 90 their +x"))


class ItemProps(_Props):
    kind: Literal["item"]
    type: str = Field(description="the item's type: a code of catalogue.json (DESK-MANAGER, COPIER, …)")
    category: Literal["furniture", "equipment", "appliance"]
    name: str = Field(description="its type's English name, for readers that do not read the catalogue")
    floor_id: str
    building_id: str
    space_id: str | None = Field(None, description="the space it stands in (null: in none of them)")
    zone_id: str | None = Field(None, description="the zone it stands in, when its space is divided")
    local: ItemLocal | None = Field(None, description=(
        "where it stands in its building (format 0.7): its middle and turn in the building's own frame, "
        "unchanged when the building moves on the map; the footprint, display_point and heading follow "
        "from it and the building's placement"))
    display_point: LonLat = Field(description="its middle, on the map")
    heading: float = Field(description="the way its front faces: degrees clockwise from north")
    width_m: float
    depth_m: float
    height_m: float
    mount: Literal["floor", "wall", "ceiling"]
    elevation_m: float | None = Field(None, description=(
        "how high above the floor its bottom is (null for one on the ceiling: just under it)"))
    values: dict[str, str | float] = Field(default_factory=dict, description=(
        "its details entered in StoreyPath, by the catalogue's field keys (the others belong to the "
        "system that manages the asset)"))


P = TypeVar("P", bound=_Props)


class Feature(_Model, Generic[P]):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: PointGeometry | Polygonal | None  # each kind's is narrower: its feature below
    properties: P


class LocationFeature(Feature[LocationProps]):
    geometry: Polygonal | None = Field(description="the convex hull of its buildings (null: none has a footprint)")


class BuildingFeature(Feature[BuildingProps]):
    geometry: Polygonal | None = Field(description="its footprint (null: no floor of it is converted)")


class FloorFeature(Feature[FloorProps]):
    geometry: Polygonal | None = Field(description="its outline (null: not known)")


class SpaceFeature(Feature[SpaceProps]):
    geometry: Polygonal = Field(description="what its walls, doors and windows enclose")


class ZoneFeature(Feature[ZoneProps]):
    geometry: Polygonal = Field(description="its part of its space")


class OpeningFeature(Feature[OpeningProps]):
    geometry: PointGeometry = Field(description="a point in the wall")


class ItemFeature(Feature[ItemProps]):
    id: str = Field(description=(
        "its asset ID (format 0.8): ten random symbols of Crockford's base32 and a check symbol, 4-4-3 "
        "(7K2Q-XM9F-4DP), of no project or place: carried anywhere, it keeps it"))
    geometry: PolygonGeometry = Field(description="its footprint, on the map")


F = TypeVar("F", bound=Feature)


class FeatureCollection(_Model, Generic[F]):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[F]


class Generator(_Model):
    name: str
    version: str


class ProjectInfo(_Model):
    id: str
    name: str


PREVIOUS_SEQUENCE = ("the sequence of the last export that held this building (format 0.7; null for its first): "
                     "what changes.json lists changes since. A gap from the last package of the building a "
                     "reader has applied means it missed one")


class ExportInfo(_Model):
    sequence: int = Field(description="1 for the project's first export, then 2, 3, …")
    exported_at: datetime
    previous_sequence: int | None = Field(None, description=PREVIOUS_SEQUENCE)


class SourceInfo(_Model):
    floor_id: str
    file: str = Field(description="file name only; local paths are not exported")
    sha256: str | None = None


class PlacementInfo(_Model):
    """Lets a consumer rebuild local drawing coordinates in meters."""

    lon: float
    lat: float
    x: float
    y: float
    bearing: float
    projection: str = "aeqd centred on (lon, lat), drawing +Y rotated to bearing"
    placed: bool = Field(True, description=(
        "false: the building is not placed on the map yet; it is exported around 0°N 0°E with its true "
        "shape and size, but its position on earth is not known"))


class Scope(_Model):
    """The building a package holds (from format 0.7, exactly one; before, the part of
    the project it held when not all of it)."""

    buildings: list[str] = Field(description="IDs of the buildings in the package (from 0.7: one); the rest of "
                                             "the project is not in it, and its absence says nothing about it")


class Manifest(BaseModel):
    format: Literal["storeypath-package"] = FORMAT_NAME
    format_version: str = FORMAT_VERSION
    generator: Generator
    project: ProjectInfo
    export: ExportInfo
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    length_unit: Literal["m"] = "m"
    files: dict[str, str]
    counts: dict[str, int]
    types: dict[str, list[str]]
    sources: list[SourceInfo]
    placements: dict[str, PlacementInfo]
    scope: Scope | None = Field(default=None, description=(
        "The building it holds (from 0.7, always one); before 0.7, absent: the whole project"))


class MovedAway(_Model):
    id: str = Field(description="an item's ID")
    building_id: str = Field(description="the building of the project it is in now")


class Changes(_Model):
    """What changed in the building since it was last exported, so importers can update
    their ID mappings."""

    sequence: int
    previous_sequence: int | None = Field(description=PREVIOUS_SEQUENCE)
    added: list[str]
    changed: list[str]
    retired: list[str] = Field(description="IDs removed since the building was last exported")
    all_retired: list[str] = Field(description=(
        "every ID this building has ever retired (from 0.7; before, the project): its own objects', and the "
        "items its packages held and that were taken away since, wherever they were then"))
    moved_away: list[MovedAway] = Field(default_factory=list, description=(
        "items in this building when it was last exported, carried since to another building of the "
        "project (format 0.7): not retired, they keep their IDs"))


NodeKind = Literal["door", "entrance", "approach", "room", "kiosk", "lift", "stairs", "escalator", "ramp"]
EdgeKind = Literal["walk", "door", "lift", "stairs", "escalator", "ramp"]


class NavFloor(_Model):
    id: str
    building_id: str
    name: str
    ordinal: int
    elevation: float


class NavPlace(_Model):
    """A space or zone a way goes through or to, and what a step calls it."""

    id: str
    kind: Literal["space", "zone"]
    space_id: str | None = Field(None, description="a zone's space")
    floor_id: str
    type: str
    label: str = Field(description='what a step calls it: "OFFICE 112", "Room 114", "the corridor"')


class NavLocal(_Model):
    x_m: float
    y_m: float


class NavNode(_Model):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    id: str = Field(description="stable: what it is (door:<opening>, room:<space or zone>, kiosk:<item>, "
                                "lift:<code>@<floor>, …)")
    kind: NodeKind
    floor_id: str
    space_id: str | None = Field(None, description="the space it is in (null for a door: see spaces)")
    zone_id: str | None = Field(None, description="the zone it is in, in a space divided into zones")
    local: NavLocal = Field(description="where it is in its building's own frame, metres")
    lonlat: LonLat
    opening_id: str | None = Field(None, description="a door's, entrance's or approach's opening (null for the way "
                                                     "into a lift or stairs no opening is drawn for)")
    spaces: list[str] | None = Field(None, description="a door's: the spaces it joins (one, for an entrance)")
    item_id: str | None = Field(None, description="a kiosk's item")
    stack: str | None = Field(None, description="a lift's, stairs', escalator's or ramp's stack")


class NavEdge(_Model):
    from_: str = Field(alias="from")
    to: str
    kind: EdgeKind
    length_m: float = Field(ge=0)
    seconds: float = Field(ge=0, description="walking at speed_m_s; lifts and stairs as FORMAT.md says")
    cost: float = Field(ge=0, description="what routing takes the fewest of: seconds, and more for a way into a room "
                                          "not for passing through")
    accessible: bool = Field(description="false for stairs and escalators")
    space_id: str | None = None
    zone_id: str | None = None
    path: list[tuple[float, float]] = Field(description="its line, from `from` to `to`, in the building's frame")


class Navigation(_Model):
    """navigation.json (format 0.8): the walking network of the package's building."""

    speed_m_s: float
    buildings: list[str]
    floors: list[NavFloor]
    places: list[NavPlace]
    nodes: list[NavNode]
    edges: list[NavEdge]


def version_problem(version: str) -> str | None:
    """Why this Studio does not read packages of a format version, or None: one that
    is not a version, one of another major version, or (before 1.0, where a minor
    version may change what a package means) of a newer minor version. Older ones,
    and newer patches, are read."""
    try:
        major, minor = version_tuple(version)
    except ValueError as e:
        return str(e)
    ours = version_tuple(FORMAT_VERSION)
    if major != ours[0]:
        return f"unsupported format version {version} (this Studio reads {ours[0]}.x)"
    if ours[0] == 0 and (major, minor) > ours:
        return f"format version {version} is newer than this Studio's {FORMAT_VERSION}: update StoreyPath Studio to read it"
    return None


# major.minor, a patch number, and a pre-release or build label (semantic versioning);
# ASCII digits only
_VERSION_RE = re.compile(r"(\d+)\.(\d+)(\.\d+)?([-+][0-9A-Za-z.-]+)?", re.ASCII)


def version_tuple(version: str) -> tuple[int, int]:
    """A format version's major and minor numbers ("0.7.0" → (0, 7)). Anything that
    is not a version (" 8", "1_0", "0.8a.0") is a ValueError, never read as another."""
    m = _VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    if m is None:
        raise ValueError(f"format version {version!r} is not a version (major.minor.patch, as {FORMAT_VERSION})")
    return int(m[1]), int(m[2])


COLLECTIONS: dict[str, type[Feature]] = {  # a package's collections of places, by role: their features
    "location": LocationFeature,
    "buildings": BuildingFeature,
    "floors": FloorFeature,
    "spaces": SpaceFeature,
    "zones": ZoneFeature,
    "openings": OpeningFeature,
}


def json_schemas() -> dict[str, dict]:
    """File name → JSON Schema for every JSON file in a package."""
    from .catalogue import Catalogue

    out = {"manifest.schema.json": Manifest.model_json_schema(), "changes.schema.json": Changes.model_json_schema()}
    for role, feature in COLLECTIONS.items():
        out[f"{role}.schema.json"] = FeatureCollection[feature].model_json_schema()
    out["items.schema.json"] = FeatureCollection[ItemFeature].model_json_schema()
    out["catalogue.schema.json"] = Catalogue.model_json_schema()
    out["navigation.schema.json"] = Navigation.model_json_schema(by_alias=True)
    return out
