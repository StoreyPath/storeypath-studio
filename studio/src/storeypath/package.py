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
FORMAT_VERSION = "0.7.0"
FILE_EXTENSION = ".storeypath"
# one building per package from 0.7 (before: a whole project, or a part of it)
ONE_BUILDING_FROM = (0, 7)

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


class ExportInfo(_Model):
    sequence: int = Field(description="1 for the project's first export, then 2, 3, …")
    exported_at: datetime
    previous_sequence: int | None = None


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
    """What changed since the previous export, so importers can update their ID mappings."""

    sequence: int
    previous_sequence: int | None
    added: list[str]
    changed: list[str]
    retired: list[str] = Field(description="IDs removed since the previous export")
    all_retired: list[str] = Field(description="every ID this project has ever retired")
    moved_away: list[MovedAway] = Field(default_factory=list, description=(
        "items in this building when it was last exported, carried since to another building of the "
        "project (format 0.7): not retired, they keep their IDs"))


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
    return out
