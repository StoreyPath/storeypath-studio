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
FORMAT_VERSION = "0.1.0"
FILE_EXTENSION = ".storeypath"

FILES = {
    "location": "location.geojson",
    "buildings": "buildings.geojson",
    "floors": "floors.geojson",
    "spaces": "spaces.geojson",
    "openings": "openings.geojson",
    "objects": "objects.csv",
    "changes": "changes.json",
}
OBJECTS_CSV_COLUMNS = [
    "id", "kind", "type", "name", "number", "project_id", "location_id",
    "building_id", "floor_id", "floor_ordinal", "area_m2", "lon", "lat",
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


class SpaceProps(_Props):
    kind: Literal["space"]
    type: SpaceType
    name: str | None = None
    number: str | None = None
    floor_id: str
    area_m2: float
    display_point: LonLat


class OpeningProps(_Props):
    kind: Literal["opening"]
    type: OpeningType
    floor_id: str
    connects: list[str] = Field(description="IDs of the spaces this opening joins")
    exterior: bool = Field(description="true when it leads outside the floor")


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
    "openings": OpeningProps,
}


def json_schemas() -> dict[str, dict]:
    """File name → JSON Schema for every JSON file in a package."""
    out = {"manifest.schema.json": Manifest.model_json_schema(), "changes.schema.json": Changes.model_json_schema()}
    for role, props in COLLECTIONS.items():
        out[f"{role}.schema.json"] = FeatureCollection[props].model_json_schema()
    return out
