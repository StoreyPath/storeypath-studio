"""The catalogue of item types: the furniture and equipment people place on floors
(desks by grade, central photocopiers, access points, sofas, TVs…).

One catalogue serves every project of a Studio (its database; ``catalogue.json`` in
a data folder). A package carries the types its items use (or, exported so, the whole
catalogue), each once, so a system reading it knows each type, its fields and how it is
drawn (its shape), and may take them into its own; a project file carries the whole. A type's code is its identity: kept for good, never given to another
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
SOFA_COLOR = "#8a837a"  # a warm grey
# what older Studios gave their default types where this one gives otherwise: never a
# person's choice, so a catalogue still saying it takes this Studio's (the sofa was purple)
OLD_DEFAULTS = {"SOFA": {"color": {"#7d6a8f"}}}
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# who a desk is for, highest first: a room takes the grade of the highest desk in it
GRADES = ("president", "c_level", "director", "manager", "section_head", "senior", "junior")
Grade = Literal["president", "c_level", "director", "manager", "section_head", "senior", "junior"]
# how the viewers draw a type (format 0.9.1): a desk with what its grade has, a meeting table with
# its chairs round it, …, a plain box. None: by its code's first part, as before (DESK-…, MEETING-…,
# SOFA, TV and SCREEN, COPIER and PRINTER, ACCESS-…, BED-…, KIOSK), else a box. Only the drawing:
# what a type is stays with its code (a wayfinding kiosk is KIOSK or KIOSK-…)
SHAPES = ("desk", "meeting_table", "sofa", "screen", "copier", "bed", "kiosk", "access_point", "box")
Shape = Literal["desk", "meeting_table", "sofa", "screen", "copier", "bed", "kiosk", "access_point", "box"]
_BY_CODE = {"DESK": "desk", "MEETING": "meeting_table", "SOFA": "sofa", "TV": "screen", "SCREEN": "screen",
            "COPIER": "copier", "PRINTER": "copier", "ACCESS": "access_point", "BED": "bed", "KIOSK": "kiosk"}


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
    shape: Shape | None = None  # how it is drawn (SHAPES; format 0.9.1); None: by its code
    # how many people work at one (a desk: 1; a bench of four: 4; a sofa: 0): counted
    # into the capacity of the room it stands in, unless the room's is set in review
    workplaces: int = Field(0, ge=0, le=100)
    grade: Grade | None = None  # who it is for (desks by grade): a room takes its highest
    fields: list[ItemField] = Field(default_factory=list)
    retired: bool = False

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE_RE.match(v) or len(v) > 40:
            raise ValueError(f"type code {v!r}: upper-case letters and digits in parts joined by -, at most 40")
        return v

    @field_validator("shape", mode="before")
    @classmethod
    def _shape(cls, v):
        # one this Studio does not know (a later format's) is read as none: drawn by its code,
        # as the format says of a reader that does not know it (a person's typo is refused
        # where the catalogue is saved: check_shapes)
        return v if v is None or v in SHAPES else None

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


def shape_of(t: ItemType) -> str:
    """How a type is drawn: its shape, else as its code's first part says (as the viewers
    read a package without shapes), else a box."""
    return t.shape or _BY_CODE.get(t.code.split("-")[0], "box")


def _desk(code: str, en: str, ar: str, width: float, depth: float, color: str, grade: str) -> ItemType:
    return ItemType(code=code, name_en=en, name_ar=ar, category="furniture", width=width, depth=depth,
                    height=0.75, mount="floor", color=color, shape="desk", workplaces=1, grade=grade)


def _meeting_table(seats: int, width: float, depth: float, color: str) -> ItemType:
    # its chairs drawn round it by the viewers, as many as its size seats (tableChairs):
    # meeting places, not workplaces (no one is allocated to one)
    ar = f"{seats} مقاعد" if seats <= 10 else f"{seats} مقعدًا"
    return ItemType(code=f"MEETING-TABLE-{seats}", name_en=f"Meeting table, {seats} seats", name_ar=f"طاولة اجتماعات، {ar}",
                    category="furniture", width=width, depth=depth, height=0.75, mount="floor", color=color,
                    shape="meeting_table")


def default_catalogue() -> Catalogue:
    """What a new Studio starts with: desks by grade, meeting tables, central
    photocopiers, access points, sofas, TVs, beds and wayfinding kiosks. People add more
    as they need them."""
    return Catalogue(types=[
        _desk("DESK-PRESIDENT", "President's desk", "مكتب الرئيس", 2.4, 1.2, "#6b4a2b", "president"),
        _desk("DESK-CLEVEL", "C-level desk", "مكتب الإدارة العليا", 2.2, 1.1, "#7a5532", "c_level"),
        _desk("DESK-DIRECTOR", "Director's desk", "مكتب مدير", 2.0, 1.0, "#8a6238", "director"),
        _desk("DESK-MANAGER", "Manager's desk", "مكتب مدير إدارة", 1.8, 0.9, "#9b7444", "manager"),
        _desk("DESK-SECTION-HEAD", "Head of section desk", "مكتب رئيس قسم", 1.6, 0.8, "#a8834f", "section_head"),
        _desk("DESK-SENIOR", "Senior staff desk", "مكتب موظف أول", 1.4, 0.7, "#b8955f", "senior"),
        _desk("DESK-JUNIOR", "Junior staff desk", "مكتب موظف", 1.2, 0.6, "#c6a674", "junior"),
        # sized to seat their number round them: in oak, a board table's (12 and more) in walnut
        _meeting_table(4, 1.2, 1.2, "#a8845e"),
        _meeting_table(6, 1.8, 0.9, "#a8845e"),
        _meeting_table(8, 2.4, 1.2, "#a8845e"),
        _meeting_table(12, 3.6, 1.4, "#6b4a33"),
        _meeting_table(14, 4.2, 1.4, "#6b4a33"),
        _meeting_table(16, 4.8, 1.5, "#6b4a33"),
        ItemType(code="COPIER", name_en="Central photocopier", name_ar="آلة تصوير مركزية", category="equipment",
                 width=1.2, depth=0.7, height=1.2, mount="floor", color="#3b6ea5", shape="copier",
                 fields=[ItemField(key="model", name_en="Model", name_ar="الطراز"),
                         ItemField(key="network_name", name_en="Network name", name_ar="اسم الشبكة", owner="system")]),
        ItemType(code="ACCESS-POINT", name_en="Wireless access point", name_ar="نقطة وصول لاسلكية", category="equipment",
                 width=0.25, depth=0.25, height=0.05, mount="ceiling", color="#1f9d8b", shape="access_point",
                 fields=[ItemField(key="color", name_en="Colour", name_ar="اللون", kind="color"),
                         ItemField(key="ssid", name_en="Network (SSID)", name_ar="اسم الشبكة اللاسلكية", owner="system"),
                         ItemField(key="vlan", name_en="VLAN", name_ar="الشبكة الافتراضية", kind="number", owner="system")]),
        ItemType(code="SOFA", name_en="Sofa", name_ar="أريكة", category="furniture",
                 width=2.0, depth=0.9, height=0.8, mount="floor", color=SOFA_COLOR, shape="sofa",
                 fields=[ItemField(key="seats", name_en="Seats", name_ar="عدد المقاعد", kind="number")]),
        ItemType(code="TV", name_en="TV screen", name_ar="شاشة تلفاز", category="appliance",
                 width=1.4, depth=0.1, height=0.8, mount="wall", color="#2b2b30", shape="screen",
                 fields=[ItemField(key="size_in", name_en="Size (inches)", name_ar="المقاس (بوصة)", kind="number")]),
        # for an office with a bed (long shifts): a 180 or 160 by 200 cm mattress in its
        # frame; its height the headboard's top
        ItemType(code="BED-KING", name_en="King-size bed", name_ar="سرير مقاس كينج", category="furniture",
                 width=1.9, depth=2.1, height=1.0, mount="floor", color="#8a5a6e", shape="bed"),
        ItemType(code="BED-QUEEN", name_en="Queen-size bed", name_ar="سرير مقاس كوين", category="furniture",
                 width=1.7, depth=2.1, height=1.0, mount="floor", color="#a87b8c", shape="bed"),
        # where a kiosk stands, its screen at its front: where people look for their
        # office, and later start the way to it (a system that guides people links its
        # kiosks to these items)
        ItemType(code="KIOSK", name_en="Wayfinding kiosk", name_ar="كشك إرشاد", category="equipment",
                 width=0.6, depth=0.45, height=1.7, mount="floor", color="#d9782b", shape="kiosk",
                 fields=[ItemField(key="model", name_en="Model", name_ar="الطراز")]),
    ])


def load(folder: str | Path) -> Catalogue:
    """The Studio's catalogue (in ``folder``, its data folder): written with the
    default types the first time, and given the default types a newer Studio brings
    that it lacks (types are retired, never taken away: one missing was never there),
    and what a newer Studio says of its default types where the file says nothing
    (how many people work at one, the grade a desk is for)."""
    path = Path(folder) / FILE_NAME
    if not path.is_file():
        cat = default_catalogue()
        save(folder, cat)
        return cat
    cat, filled = read(path.read_text(encoding="utf-8"))
    cat.check()
    known = {t.code for t in cat.types}
    new = [t for t in default_catalogue().types if t.code not in known]
    if new or filled:
        cat.types.extend(new)
        save(folder, cat)
    return cat


def read(text: str) -> tuple[Catalogue, bool]:
    """A catalogue from its JSON, with what this Studio says of its own default types
    where an older file says nothing (how many people work at one, the grade a desk is
    for, how it is drawn) or says what an older Studio gave them (OLD_DEFAULTS); and
    whether any was filled in."""
    raw = json.loads(text)
    defaults = {t.code: t for t in default_catalogue().types}
    filled = False
    for t in raw.get("types", []):
        d = defaults.get(t.get("code"))
        if d is None:
            continue
        for key in ("workplaces", "grade", "shape"):
            if key not in t:
                t[key], filled = getattr(d, key), True
        for key, olds in OLD_DEFAULTS.get(d.code, {}).items():
            if isinstance(t.get(key), str) and t[key].lower() in olds:
                t[key], filled = getattr(d, key), True
    return Catalogue.model_validate(raw), filled


def check_types(types: list) -> None:
    """What a person sends to save (the catalogue's JSON, its ``types``), checked beyond
    what reading it checks: each shape one of SHAPES (one this Studio does not know is
    read as none, so a typo would be lost), a size more than nothing and at most 100 m,
    an elevation not below the floor. ValueError, saying which type and why."""
    for i, t in enumerate(types):
        if not isinstance(t, dict):
            continue  # (refused as it is read)
        which = str(t.get("code") or f"type {i + 1}")[:40]
        shape = t.get("shape")
        if shape is not None and shape not in SHAPES:
            raise ValueError(f"{which}: shape {str(shape)[:40]!r} is not one of {', '.join(SHAPES)}")
        for key in ("width", "depth", "height"):
            v = t.get(key)
            if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= 100):
                raise ValueError(f"{which}: {key} is in metres, more than 0 and at most 100")
        v = t.get("elevation")
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 100):
            raise ValueError(f"{which}: elevation is in metres above the floor, 0 to 100")


MAX_CATALOGUE = 16 << 20  # a catalogue's JSON, read from a file a person sends (types_in)


def types_in(data: bytes) -> dict:
    """The item types a file brings, for a person to choose which to take into this
    Studio's catalogue (the Item types page): a catalogue (.json: this format's, or
    {"types": [...]}, or the list of types alone), a building's package (.storeypath: its
    catalogue.json) or a project file (.storeypath-project: its catalogue). Each type as
    this Studio reads it (one of its own default codes filled in as read() fills it).
    {"source": "catalogue" | "package" | "project", "name": the project's or None,
    "types": [...]}; ValueError, saying why, for anything else."""
    import io
    import zipfile

    from pydantic import ValidationError

    name = None
    if data[:4] == b"PK\x03\x04":  # a ZIP: a package or a project file
        from .bundle import CATALOGUE_FILE, MAX_BYTES, MAX_FILES, PROJECT_MANIFEST

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos = {i.filename: i for i in z.infolist()}
                if len(infos) > MAX_FILES or sum(i.file_size for i in infos.values()) > MAX_BYTES:
                    raise ValueError("the file holds too much to be a StoreyPath file")
                if "manifest.json" in infos:
                    source, about = "package", json.loads(z.read("manifest.json"))
                    part = (about.get("files") or {}).get("catalogue") if isinstance(about, dict) else None
                elif PROJECT_MANIFEST in infos:
                    source, about, part = "project", json.loads(z.read(PROJECT_MANIFEST)), CATALOGUE_FILE
                else:
                    raise ValueError("not a StoreyPath package or project file: it has no manifest.json or project.json")
                name = ((about.get("project") or {}).get("name") or None) if isinstance(about, dict) else None
                if not isinstance(part, str) or part not in infos:
                    raise ValueError(f"this {source} brings no item types: it has no catalogue")
                if infos[part].file_size > MAX_CATALOGUE:
                    raise ValueError("its catalogue is too large to be read")
                text = z.read(part).decode("utf-8")
        except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("the file is damaged: not a package or a project file that can be read") from None
    else:
        source = "catalogue"
        if len(data) > MAX_CATALOGUE:
            raise ValueError("too large to be a catalogue of item types")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("not a catalogue of item types (.json), a package (.storeypath) or a project file") from None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("not a catalogue of item types: it is not JSON") from None
    if isinstance(raw, list):
        raw = {"types": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("types"), list):
        raise ValueError("not a catalogue of item types: it has no list of types")
    if raw.get("format", CATALOGUE_FORMAT) != CATALOGUE_FORMAT:
        raise ValueError(f"not a catalogue of item types: its format is {str(raw.get('format'))[:60]!r}")
    if not raw["types"]:
        raise ValueError("the file brings no item types")
    try:
        cat, _ = read(json.dumps({"types": raw["types"]}))
        cat.check()
    except ValidationError as e:
        first = e.errors()[0]
        where = first.get("loc", ())
        which = ""
        if len(where) >= 2 and where[0] == "types" and isinstance(where[1], int):
            t = raw["types"][where[1]]
            code = t.get("code") if isinstance(t, dict) else None
            which = f"type {where[1] + 1}{f' ({str(code)[:40]})' if code else ''}: "
            where = where[2:]
        field = ".".join(str(w) for w in where)
        said = str(first.get("msg", "not valid")).removeprefix("Value error, ")
        raise ValueError(f"{which}{field + ': ' if field else ''}{said}") from None
    return {"source": source, "name": name, "types": [t.model_dump(mode="json") for t in cat.types]}


def save(folder: str | Path, cat: Catalogue) -> None:
    cat.check()
    path = Path(folder) / FILE_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cat.model_dump(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
