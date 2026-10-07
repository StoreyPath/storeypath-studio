"""The exchange package (*.storeypath): models for every file in it.

These models are the format's source of truth: export writes them, validate
reads them, and the JSON Schemas shipped inside each package are generated from
them. See spec/FORMAT.md for the human-readable description.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field

from .types import OpeningType, SpaceType

FORMAT_NAME = "storeypath-package"
FORMAT_VERSION = "0.5.0"
FILE_EXTENSION = ".storeypath"

FILES = {
    "location": "location.geojson",
    "buildings": "buildings.geojson",
    "floors": "floors.geojson",
    "spaces": "spaces.geojson",
    "zones": "zones.geojson",
    "openings": "openings.geojson",
    "objects": "objects.csv",
    "changes": "changes.json",
}
OBJECTS_CSV_COLUMNS = [
    "id", "kind", "type", "name", "number", "project_id", "location_id",
    "building_id", "floor_id", "floor_ordinal", "area_m2", "lon", "lat", "hidden", "ignored", "space_id",
    "drawing_label",
]

LonLat = tuple[float, float]


class PointGeometry(BaseModel):
    type: Literal["Point"]
    coordinates: LonLat


class PolygonGeometry(BaseModel):
    type: Literal["Polygon"]
    coordinates: list[list[LonLat]]


class MultiPolygonGeometry(BaseModel):
    type: Literal["MultiPolygon"]
    coordinates: list[list[list[LonLat]]]


Geometry = Annotated[
    Union[PointGeometry, PolygonGeometry, MultiPolygonGeometry], Field(discriminator="type")
]


class _Props(BaseModel):
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
    walls: Geometry | None = Field(None, description="the walls as drawn, with their door and window gaps; "
                                                    "full height, except the parapets")
    wall_thickness_m: float | None = Field(None, description="the walls' typical thickness")
    parapets: Geometry | None = Field(None, description="the low walls around terraces, balconies and roofs, "
                                                       "parapet_height_m high")
    parapet_height_m: float | None = Field(None, description="the parapets' height above the floor")


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


P = TypeVar("P", bound=_Props)


class Feature(BaseModel, Generic[P]):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: Geometry | None
    properties: P


class FeatureCollection(BaseModel, Generic[P]):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature[P]]


class Generator(BaseModel):
    name: str
    version: str


class ProjectInfo(BaseModel):
    id: str
    name: str


class ExportInfo(BaseModel):
    sequence: int = Field(description="1 for the project's first export, then 2, 3, …")
    exported_at: datetime
    previous_sequence: int | None = None


class SourceInfo(BaseModel):
    floor_id: str
    file: str = Field(description="file name only; local paths are not exported")
    sha256: str | None = None


class PlacementInfo(BaseModel):
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


class Scope(BaseModel):
    """The part of the project a package holds, when it is not all of it."""

    buildings: list[str] = Field(description="IDs of the buildings in the package; the rest of the project is "
                                             "not in it, and its absence says nothing about them")


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
    scope: Scope | None = Field(default=None, description="Only some of the project's buildings; absent: all of it")


class Changes(BaseModel):
    """What changed since the previous export, so importers can update their ID mappings."""

    sequence: int
    previous_sequence: int | None
    added: list[str]
    changed: list[str]
    retired: list[str] = Field(description="IDs removed since the previous export")
    all_retired: list[str] = Field(description="every ID this project has ever retired")


COLLECTIONS: dict[str, type[_Props]] = {
    "location": LocationProps,
    "buildings": BuildingProps,
    "floors": FloorProps,
    "spaces": SpaceProps,
    "zones": ZoneProps,
    "openings": OpeningProps,
}


def json_schemas() -> dict[str, dict]:
    """File name → JSON Schema for every JSON file in a package."""
    out = {"manifest.schema.json": Manifest.model_json_schema(), "changes.schema.json": Changes.model_json_schema()}
    for role, props in COLLECTIONS.items():
        out[f"{role}.schema.json"] = FeatureCollection[props].model_json_schema()
    return out
