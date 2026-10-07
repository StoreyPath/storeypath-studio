"""The catalogue of item types: the furniture and equipment people place on floors
(desks by grade, central photocopiers, access points, sofas, TVs…).

One catalogue serves every project of a Studio (``catalogue.json`` in its data
folder) and goes with every package, so a system reading it knows each type and
its fields. A type's code is its identity: kept for good, never given to another
type; a type no longer used is retired, not removed. Each field says who enters
it: StoreyPath (what is physical: size, colour, model) or the system that manages
the asset (wayfinder: an access point's network).

This is asset management: where things are and have been, not inventory. Nothing
here says which employee holds what; an inventory system keys its records to the
items' IDs, as any system keys its own to StoreyPath's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CATALOGUE_FORMAT = "storeypath-catalogue"
CATALOGUE_VERSION = 1
FILE_NAME = "catalogue.json"
_CODE_RE = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ItemField(BaseModel):
    """A detail an item of this type carries."""

    key: str  # stable, e.g. "network"
    name_en: str
    name_ar: str = ""
    kind: Literal["text", "number", "choice", "color"] = "text"
    choices: list[str] = Field(default_factory=list)  # for "choice"
    owner: Literal["storeypath", "system"] = "storeypath"  # who enters it: StoreyPath, or the system that manages the asset

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError(f"field key {v!r}: lower-case letters, digits and _, starting with a letter")
        return v


class ItemType(BaseModel):
    code: str  # stable identity, e.g. DESK-MANAGER
    name_en: str
    name_ar: str = ""
    category: Literal["furniture", "equipment", "appliance"] = "furniture"
    width: float = 1.0  # metres, along the item (its front)
    depth: float = 0.6  # metres, front to back
    height: float = 0.75  # metres: its top above the floor (floor), its size (wall, ceiling)
    mount: Literal["floor", "wall", "ceiling"] = "floor"
    elevation: float | None = None  # its bottom above the floor; None: on the floor, 1.2 m on a wall, under the ceiling
    color: str = "#8a8a8a"
    fields: list[ItemField] = Field(default_factory=list)
    retired: bool = False

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE_RE.match(v) or len(v) > 40:
            raise ValueError(f"type code {v!r}: upper-case letters and digits in parts joined by -, at most 40")
        return v

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError(f"colour {v!r}: #rrggbb")
        return v.lower()


class Catalogue(BaseModel):
    format: Literal["storeypath-catalogue"] = CATALOGUE_FORMAT
    format_version: int = CATALOGUE_VERSION
    types: list[ItemType] = Field(default_factory=list)

    def get(self, code: str) -> ItemType | None:
        return next((t for t in self.types if t.code == code), None)

    def check(self) -> None:
        codes = [t.code for t in self.types]
        if len(codes) != len(set(codes)):
            raise ValueError("two item types share a code")
        for t in self.types:
            keys = [f.key for f in t.fields]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{t.code}: two fields share a key")


def _desk(code: str, en: str, ar: str, width: float, depth: float, color: str) -> ItemType:
    return ItemType(code=code, name_en=en, name_ar=ar, category="furniture", width=width, depth=depth,
                    height=0.75, mount="floor", color=color)


def default_catalogue() -> Catalogue:
    """What a new Studio starts with: desks by grade, central photocopiers, access
    points, sofas and TVs. People add more as they need them."""
    return Catalogue(types=[
        _desk("DESK-PRESIDENT", "President's desk", "مكتب الرئيس", 2.4, 1.2, "#6b4a2b"),
        _desk("DESK-CLEVEL", "C-level desk", "مكتب الإدارة العليا", 2.2, 1.1, "#7a5532"),
        _desk("DESK-DIRECTOR", "Director's desk", "مكتب مدير", 2.0, 1.0, "#8a6238"),
        _desk("DESK-MANAGER", "Manager's desk", "مكتب مدير إدارة", 1.8, 0.9, "#9b7444"),
        _desk("DESK-SECTION-HEAD", "Head of section desk", "مكتب رئيس قسم", 1.6, 0.8, "#a8834f"),
        _desk("DESK-SENIOR", "Senior staff desk", "مكتب موظف أول", 1.4, 0.7, "#b8955f"),
        _desk("DESK-JUNIOR", "Junior staff desk", "مكتب موظف", 1.2, 0.6, "#c6a674"),
        ItemType(code="COPIER", name_en="Central photocopier", name_ar="آلة تصوير مركزية", category="equipment",
                 width=1.2, depth=0.7, height=1.2, mount="floor", color="#3b6ea5",
                 fields=[ItemField(key="model", name_en="Model", name_ar="الطراز"),
                         ItemField(key="network_name", name_en="Network name", name_ar="اسم الشبكة", owner="system")]),
        ItemType(code="ACCESS-POINT", name_en="Wireless access point", name_ar="نقطة وصول لاسلكية", category="equipment",
                 width=0.25, depth=0.25, height=0.05, mount="ceiling", color="#1f9d8b",
                 fields=[ItemField(key="color", name_en="Colour", name_ar="اللون", kind="color"),
                         ItemField(key="ssid", name_en="Network (SSID)", name_ar="اسم الشبكة اللاسلكية", owner="system"),
                         ItemField(key="vlan", name_en="VLAN", name_ar="الشبكة الافتراضية", kind="number", owner="system")]),
        ItemType(code="SOFA", name_en="Sofa", name_ar="أريكة", category="furniture",
                 width=2.0, depth=0.9, height=0.8, mount="floor", color="#7d6a8f",
                 fields=[ItemField(key="seats", name_en="Seats", name_ar="عدد المقاعد", kind="number")]),
        ItemType(code="TV", name_en="TV screen", name_ar="شاشة تلفاز", category="appliance",
                 width=1.4, depth=0.1, height=0.8, mount="wall", color="#2b2b30",
                 fields=[ItemField(key="size_in", name_en="Size (inches)", name_ar="المقاس (بوصة)", kind="number")]),
    ])


def load(folder: str | Path) -> Catalogue:
    """The Studio's catalogue (in ``folder``, its data folder): written with the
    default types the first time."""
    path = Path(folder) / FILE_NAME
    if not path.is_file():
        cat = default_catalogue()
        save(folder, cat)
        return cat
    cat = Catalogue.model_validate_json(path.read_text(encoding="utf-8"))
    cat.check()
    return cat


def save(folder: str | Path, cat: Catalogue) -> None:
    cat.check()
    path = Path(folder) / FILE_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cat.model_dump(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
