"""The catalogue of item types: the furniture and the building's equipment people place on
floors (desks by grade, chairs, meeting tables, cupboards; cameras, card readers, smoke
detectors, extinguishers, access points, air-conditioning units…), each in a category.

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

from pydantic import BaseModel, Field, field_validator, model_validator

CATALOGUE_FORMAT = "storeypath-catalogue"
# 2 (format 0.10): categories by what a thing is for; a desk's and a table's chairs types of
# their own. A catalogue of 1 read so gives its default types this Studio's categories
CATALOGUE_VERSION = 2
FILE_NAME = "catalogue.json"
MODELS = "models/"  # types' 3D models (format 0.9.2): in a package, and beside a data folder's catalogue.json
_CODE_RE = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")
SOFA_COLOR = "#8a837a"  # a warm grey
# what older Studios gave their default types where this one gives otherwise: never a
# person's choice, so a catalogue still saying it takes this Studio's (the sofa was purple)
# (the desk return, first 1 by 0.5 m: it crowded its workstation's chair)
OLD_DEFAULTS = {"SOFA": {"color": {"#7d6a8f"}}, "DESK-RETURN": {"width": {1.0}, "depth": {0.5}}}
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# who a desk is for, highest first: a room takes the grade of the highest desk in it
GRADES = ("president", "c_level", "director", "manager", "section_head", "senior", "junior")
Grade = Literal["president", "c_level", "director", "manager", "section_head", "senior", "junior"]
# what a type is (format 0.10): furniture by what it is for, the building's equipment by the
# system it is part of; "furniture" and "equipment", anything else of either. In this order
# in Review (the box to find what to place); the first six and "furniture" are furniture
CATEGORIES = ("workstations", "seating", "tables", "storage", "soft_seating", "beds", "furniture",
              "security", "fire_safety", "it_av", "building_services", "appliances", "equipment")
Category = Literal["workstations", "seating", "tables", "storage", "soft_seating", "beds", "furniture",
                   "security", "fire_safety", "it_av", "building_services", "appliances", "equipment"]
OLD_CATEGORIES = {"appliance": "appliances"}  # 0.9's, read as
# how the viewers draw a type (format 0.9.1; 0.10: a desk and a table alone, their chairs types
# of their own): a desk, a table, an office chair, a visitor's chair, an armchair, a sofa, a
# cupboard or drawers, a screen, a copier, a bed, a kiosk, an access point, a camera, a plain
# box. None: by its code's first part, as before (DESK-…, MEETING-…, SOFA, TV and SCREEN, COPIER
# and PRINTER, ACCESS-…, BED-…, KIOSK), else a box. Only the drawing: what a type is stays with
# its code (a wayfinding kiosk is KIOSK or KIOSK-…)
SHAPES = ("desk", "table", "chair", "side_chair", "armchair", "sofa", "storage", "screen", "copier", "bed", "kiosk",
          "access_point", "camera", "box")
Shape = Literal["desk", "table", "chair", "side_chair", "armchair", "sofa", "storage", "screen", "copier", "bed", "kiosk",
                "access_point", "camera", "box"]
OLD_SHAPES = {"meeting_table": "table"}  # 0.9's (a table with its chairs), read as
_BY_CODE = {"DESK": "desk", "MEETING": "table", "SOFA": "sofa", "TV": "screen", "SCREEN": "screen",
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


MAX_PLAN_POINTS = 50_000  # a type's plan's points, lines and outline (format 0.13.2)
Point = tuple[float, float]


class ItemPlan(BaseModel):
    """How a type's items look from above, line for line as a drawing has them (format
    0.13.2; FORMAT.md, Plan drawings): metres in the type's own frame, its middle at (0, 0),
    x across it, its front towards -y. ``lines``: polylines (a closed one repeats its first
    point); ``outline``: its footprint for 3D, polygons of rings (the first the boundary,
    the rest holes; the first point not repeated). Drawn in place of its shape."""

    lines: list[list[Point]]
    outline: list[list[list[Point]]] | None = None


class ItemType(BaseModel):
    code: str  # stable identity, e.g. DESK-MANAGER
    name_en: str
    name_ar: str = ""
    category: Category = "furniture"
    width: float = 1.0  # metres, along the item (its front)
    depth: float = 0.6  # metres, front to back
    height: float = 0.75  # metres: its top above the floor (floor), its size (wall, ceiling)
    mount: Literal["floor", "wall", "ceiling"] = "floor"
    elevation: float | None = None  # its bottom above the floor; None: on the floor, 1.2 m on a wall, under the ceiling
    color: str = "#8a8a8a"
    shape: Shape | None = None  # how it is drawn (SHAPES; format 0.9.1); None: by its code
    # its 3D model in a package (format 0.9.2; models.py): models/<code>.glb, drawn in place of
    # its shape in 3D; None: none. It follows from the models Studio has, never from what is sent
    model: str | None = None
    plan: ItemPlan | None = None  # its drawing seen from above (format 0.13.2); None: drawn by its shape
    # how many people work at one (a desk: 1; a bench of four: 4; a sofa: 0): counted
    # into the capacity of the room it stands in, unless the room's is set in review
    workplaces: int = Field(0, ge=0, le=100)
    grade: Grade | None = None  # who it is for (desks by grade): a room takes its highest
    # a camera's view (format 0.10): how wide it sees (degrees across, 360 all round) and how
    # far (metres), for its items that are not given their own (their fields of the same keys)
    view_deg: float | None = Field(None, gt=0, le=360)
    range_m: float | None = Field(None, gt=0, le=500)
    # its items each serve a door (format 0.10): a card reader, a door controller, an exit
    # button, linked to the door they open (Item.door_id)
    serves_door: bool = False
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
        # where the catalogue is saved: check_shapes); 0.9's meeting_table is a table
        v = OLD_SHAPES.get(v, v)
        return v if v is None or v in SHAPES else None

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        return OLD_CATEGORIES.get(v, v)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError(f"colour {v!r}: #rrggbb")
        return v.lower()

    @model_validator(mode="after")
    def _model(self):
        if self.model is not None and self.model != model_path(self.code):
            raise ValueError(f"{self.code}'s model is {model_path(self.code)}, not {self.model!r}")
        return self


def model_path(code: str) -> str:
    """Where a type's 3D model is in a package (and beside a data folder's catalogue.json)."""
    return f"{MODELS}{code}.glb"


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


def _t(code: str, en: str, ar: str, category: str, size: tuple[float, float, float], color: str, *, mount: str = "floor",
       elevation: float | None = None, shape: str = "box", workplaces: int = 0, grade: str | None = None,
       fields: list[ItemField] | None = None, view: tuple[float, float] | None = None, serves_door: bool = False) -> ItemType:
    width, depth, height = size
    view_deg, range_m = view or (None, None)
    return ItemType(code=code, name_en=en, name_ar=ar, category=category, width=width, depth=depth, height=height, mount=mount,
                    elevation=elevation, color=color, shape=shape, workplaces=workplaces, grade=grade, fields=fields or [],
                    view_deg=view_deg, range_m=range_m, serves_door=serves_door)


def _desk(code: str, en: str, ar: str, width: float, depth: float, color: str, grade: str) -> ItemType:
    # the desk alone (format 0.10): its chair, a return, a credenza, visitors' chairs are
    # items of their own, placed with it as a set
    return _t(code, en, ar, "workstations", (width, depth, 0.75), color, shape="desk", workplaces=1, grade=grade)


def _meeting_table(seats: int, width: float, depth: float, color: str) -> ItemType:
    # the table alone (format 0.10): its chairs are items of their own, placed with it as a
    # set; meeting places, not workplaces (no one is allocated to one)
    ar = f"{seats} مقاعد" if seats <= 10 else f"{seats} مقعدًا"
    return _t(f"MEETING-TABLE-{seats}", f"Meeting table, {seats} seats", f"طاولة اجتماعات، {ar}", "tables",
              (width, depth, 0.75), color, shape="table")


# fields some types carry, entered in StoreyPath. A camera's view_deg and range_m are its own
# view, where it differs from its type's (a lens zoomed in, a camera looking down a corridor)
def _camera_fields() -> list[ItemField]:
    return [ItemField(key="view_deg", name_en="Field of view (°)", name_ar="زاوية الرؤية (°)", kind="number"),
            ItemField(key="range_m", name_en="Range (m)", name_ar="المدى (م)", kind="number"),
            ItemField(key="model", name_en="Model", name_ar="الطراز")]


def _model_field() -> list[ItemField]:
    return [ItemField(key="model", name_en="Model", name_ar="الطراز")]


OAK, WALNUT_TOP, CHAIR_FABRIC, LEATHER = "#a8845e", "#6b4a33", "#41444c", "#2b2622"
SECURITY, FIRE, SAFETY_GREEN, IT, SERVICES, APPLIANCE = "#3f51b5", "#d32f2f", "#2e7d32", "#00897b", "#607d8b", "#78909c"


def default_catalogue() -> Catalogue:
    """What a new Studio starts with: furniture (desks by grade, chairs, tables, storage, soft
    seating, beds) and the building's equipment whose place matters (security, fire and life
    safety, IT and AV, building services, appliances), each piece an item of its own. People
    add more as they need them."""
    return Catalogue(types=[
        # ---- workstations: desks by grade, alone (format 0.10)
        _desk("DESK-PRESIDENT", "President's desk", "مكتب الرئيس", 2.4, 1.2, "#6b4a2b", "president"),
        _desk("DESK-CLEVEL", "C-level desk", "مكتب الإدارة العليا", 2.2, 1.1, "#7a5532", "c_level"),
        _desk("DESK-DIRECTOR", "Director's desk", "مكتب مدير", 2.0, 1.0, "#8a6238", "director"),
        _desk("DESK-MANAGER", "Manager's desk", "مكتب مدير إدارة", 1.8, 0.9, "#9b7444", "manager"),
        _desk("DESK-SECTION-HEAD", "Head of section desk", "مكتب رئيس قسم", 1.6, 0.8, "#a8834f", "section_head"),
        _desk("DESK-SENIOR", "Senior staff desk", "مكتب موظف أول", 1.4, 0.7, "#b8955f", "senior"),
        _desk("DESK-JUNIOR", "Junior staff desk", "مكتب موظف", 1.2, 0.6, "#c6a674", "junior"),
        # an L-shaped desk's side part, at its user's side
        _t("DESK-RETURN", "Desk return", "ملحق جانبي للمكتب", "workstations", (0.8, 0.45, 0.75), "#9b7444", shape="desk"),
        _t("DESK-BENCH-4", "Bench desk, 4 places", "مكتب جماعي، 4 أماكن", "workstations", (2.8, 1.6, 0.75), "#c6a674",
           shape="desk", workplaces=4),
        _t("RECEPTION-DESK", "Reception desk", "مكتب استقبال", "workstations", (2.4, 0.8, 1.1), "#8a6238", shape="desk",
           workplaces=1),
        # ---- seating: each chair its own (format 0.10); its front where its sitter looks
        _t("CHAIR-EXECUTIVE", "Executive chair", "كرسي تنفيذي", "seating", (0.7, 0.7, 1.25), LEATHER, shape="chair"),
        _t("CHAIR-TASK", "Office chair", "كرسي مكتب", "seating", (0.62, 0.62, 1.0), CHAIR_FABRIC, shape="chair"),
        _t("CHAIR-MEETING", "Meeting chair", "كرسي اجتماعات", "seating", (0.6, 0.6, 0.95), "#4a4e57", shape="chair"),
        _t("CHAIR-BOARD", "Boardroom chair", "كرسي مجلس الإدارة", "seating", (0.68, 0.68, 1.2), LEATHER, shape="chair"),
        _t("CHAIR-VISITOR", "Visitor chair", "كرسي زائر", "seating", (0.5, 0.5, 0.88), "#5b5f69", shape="side_chair"),
        _t("SEATS-WAITING-3", "Waiting seats, 3", "مقاعد انتظار، 3 مقاعد", "seating", (1.7, 0.6, 0.8), "#5b5f69",
           shape="sofa"),
        # ---- tables: alone (format 0.10), sized to seat their number round them: in oak, a
        # board table's (12 and more) in walnut
        _meeting_table(4, 1.2, 1.2, OAK),
        _meeting_table(6, 1.8, 0.9, OAK),
        _meeting_table(8, 2.4, 1.2, OAK),
        _meeting_table(10, 3.0, 1.2, OAK),
        _meeting_table(12, 3.6, 1.4, WALNUT_TOP),
        _meeting_table(14, 4.2, 1.4, WALNUT_TOP),
        _meeting_table(16, 4.8, 1.5, WALNUT_TOP),
        _t("TABLE-TRAINING", "Training table", "طاولة تدريب", "tables", (1.6, 0.6, 0.75), "#c6a674", shape="table"),
        _t("TABLE-DINING-4", "Dining table, 4 seats", "طاولة طعام، 4 مقاعد", "tables", (1.2, 0.8, 0.75), OAK, shape="table"),
        _t("TABLE-COFFEE", "Coffee table", "طاولة قهوة", "tables", (1.0, 0.6, 0.42), WALNUT_TOP, shape="table"),
        _t("TABLE-SIDE", "Side table", "طاولة جانبية", "tables", (0.5, 0.5, 0.55), WALNUT_TOP, shape="table"),
        # ---- storage
        _t("PEDESTAL", "Drawer pedestal", "وحدة أدراج متنقلة", "storage", (0.42, 0.55, 0.6), "#8d9096", shape="storage"),
        _t("CREDENZA", "Credenza", "خزانة جانبية منخفضة", "storage", (1.6, 0.45, 0.72), "#7a5532", shape="storage"),
        _t("CABINET-FILING", "Filing cabinet", "خزانة ملفات", "storage", (0.8, 0.45, 1.32), "#9aa0a6", shape="storage"),
        _t("CUPBOARD-TALL", "Tall cupboard", "خزانة طويلة", "storage", (0.9, 0.45, 2.0), "#b8955f", shape="storage"),
        _t("BOOKCASE", "Bookcase", "خزانة كتب", "storage", (0.9, 0.35, 1.8), "#8a6238", shape="storage"),
        _t("LOCKERS-6", "Lockers, 6 doors", "خزائن شخصية، 6 أبواب", "storage", (0.9, 0.5, 1.8), "#7d8a96", shape="storage"),
        _t("SAFE", "Safe", "خزنة", "storage", (0.5, 0.5, 0.8), "#4a4d51", shape="storage"),
        # ---- soft seating
        _t("SOFA", "Sofa", "أريكة", "soft_seating", (2.0, 0.9, 0.8), SOFA_COLOR, shape="sofa",
           fields=[ItemField(key="seats", name_en="Seats", name_ar="عدد المقاعد", kind="number")]),
        _t("SOFA-2", "Two-seat sofa", "أريكة لشخصين", "soft_seating", (1.5, 0.85, 0.8), SOFA_COLOR, shape="sofa"),
        _t("ARMCHAIR", "Armchair", "كرسي بذراعين", "soft_seating", (0.75, 0.75, 0.88), "#7a6f64", shape="armchair"),
        # ---- beds: for an office with a bed (long shifts), a 180 or 160 by 200 cm mattress in
        # its frame; its height the headboard's top
        _t("BED-KING", "King-size bed", "سرير مقاس كينج", "beds", (1.9, 2.1, 1.0), "#8a5a6e", shape="bed"),
        _t("BED-QUEEN", "Queen-size bed", "سرير مقاس كوين", "beds", (1.7, 2.1, 1.0), "#a87b8c", shape="bed"),
        # ---- security: a camera looks the way its front faces
        # (a camera's view: a dome's wide lens over a room; a bullet's narrower, further, down a
        # corridor or at a door; a PTZ turns all round, and zooms far)
        _t("CCTV-DOME", "CCTV camera, dome", "كاميرا مراقبة قبّية", "security", (0.15, 0.15, 0.12), SECURITY,
           mount="ceiling", shape="camera", fields=_camera_fields(), view=(90, 12)),
        _t("CCTV-BULLET", "CCTV camera, bullet", "كاميرا مراقبة أسطوانية", "security", (0.1, 0.25, 0.1), SECURITY,
           mount="wall", elevation=2.6, shape="camera", fields=_camera_fields(), view=(60, 20)),
        _t("CCTV-PTZ", "CCTV camera, PTZ", "كاميرا مراقبة متحركة (PTZ)", "security", (0.22, 0.22, 0.3), SECURITY,
           mount="ceiling", shape="camera", fields=_camera_fields(), view=(360, 30)),
        # (each linked to the door it opens: placed, to the nearest)
        _t("CARD-READER", "Access card reader", "قارئ بطاقات الدخول", "security", (0.09, 0.04, 0.14), "#283593",
           mount="wall", elevation=1.1, fields=_model_field(), serves_door=True),
        _t("DOOR-CONTROLLER", "Door controller", "وحدة التحكم بالباب", "security", (0.3, 0.1, 0.4), "#283593",
           mount="wall", elevation=2.2, fields=_model_field(), serves_door=True),
        _t("EXIT-BUTTON", "Exit button", "زر الخروج", "security", (0.09, 0.04, 0.09), "#5c6bc0", mount="wall", elevation=1.1,
           serves_door=True),
        _t("INTERCOM", "Intercom", "جهاز اتصال داخلي (إنتركم)", "security", (0.12, 0.04, 0.2), "#283593", mount="wall",
           elevation=1.3, serves_door=True),
        _t("ALARM-KEYPAD", "Alarm keypad", "لوحة مفاتيح نظام الإنذار", "security", (0.15, 0.03, 0.12), "#283593",
           mount="wall", elevation=1.4),
        _t("MOTION-SENSOR", "Motion sensor", "حساس حركة", "security", (0.1, 0.1, 0.05), "#5c6bc0", mount="ceiling"),
        _t("PANIC-BUTTON", "Panic button", "زر الطوارئ", "security", (0.08, 0.04, 0.08), "#c62828", mount="wall",
           elevation=0.8),
        _t("TURNSTILE", "Turnstile", "بوابة دوّارة", "security", (1.2, 0.3, 1.0), "#5f6b7a"),
        # outdoors, on a location's land (format 0.12): a camera high on a pole or a facade; a
        # vehicle barrier at a gate, serving it as a card reader serves a door
        _t("CCTV-POLE", "CCTV camera on a pole", "كاميرا مراقبة على عمود", "security", (0.12, 0.3, 0.12), SECURITY,
           mount="wall", elevation=4.5, shape="camera", fields=_camera_fields(), view=(70, 30)),
        _t("BARRIER", "Vehicle barrier", "حاجز مركبات", "security", (3.5, 0.35, 1.1), "#e0b000", serves_door=True),
        # ---- fire and life safety
        _t("SMOKE-DETECTOR", "Smoke detector", "كاشف دخان", "fire_safety", (0.12, 0.12, 0.05), FIRE, mount="ceiling"),
        _t("HEAT-DETECTOR", "Heat detector", "كاشف حرارة", "fire_safety", (0.12, 0.12, 0.05), "#e57373", mount="ceiling"),
        _t("CALL-POINT", "Fire alarm call point", "نقطة نداء إنذار الحريق", "fire_safety", (0.09, 0.05, 0.09), FIRE,
           mount="wall", elevation=1.4),
        _t("FIRE-ALARM-PANEL", "Fire alarm panel", "لوحة التحكم بإنذار الحريق", "fire_safety", (0.45, 0.12, 0.35), FIRE,
           mount="wall", elevation=1.5, fields=_model_field()),
        _t("ALARM-SOUNDER", "Fire alarm sounder", "جرس إنذار الحريق", "fire_safety", (0.12, 0.08, 0.12), FIRE,
           mount="wall", elevation=2.3),
        _t("FIRE-EXTINGUISHER", "Fire extinguisher", "طفاية حريق", "fire_safety", (0.2, 0.2, 0.6), FIRE,
           fields=[ItemField(key="agent", name_en="Agent", name_ar="مادة الإطفاء", kind="choice",
                             choices=["CO2", "Dry powder", "Foam", "Water", "Wet chemical"]),
                   ItemField(key="capacity_kg", name_en="Capacity (kg)", name_ar="السعة (كغ)", kind="number")]),
        _t("FIRE-HOSE-REEL", "Fire hose reel cabinet", "خزانة بكرة خرطوم الحريق", "fire_safety", (0.8, 0.25, 0.8), FIRE,
           mount="wall", elevation=0.9),
        _t("FIRE-BLANKET", "Fire blanket", "بطانية الحريق", "fire_safety", (0.25, 0.08, 0.3), FIRE, mount="wall",
           elevation=1.4),
        _t("EMERGENCY-LIGHT", "Emergency light", "إضاءة الطوارئ", "fire_safety", (0.35, 0.12, 0.06), "#f9a825",
           mount="ceiling"),
        _t("EXIT-SIGN", "Exit sign", "لوحة مخرج الطوارئ", "fire_safety", (0.35, 0.03, 0.2), SAFETY_GREEN, mount="wall",
           elevation=2.2),
        _t("FIRST-AID-KIT", "First-aid kit", "حقيبة الإسعافات الأولية", "fire_safety", (0.4, 0.15, 0.3), SAFETY_GREEN,
           mount="wall", elevation=1.4),
        _t("AED", "Defibrillator (AED)", "جهاز الصدمات الكهربائية (AED)", "fire_safety", (0.4, 0.2, 0.45), SAFETY_GREEN,
           mount="wall", elevation=1.2),
        # ---- IT and AV
        _t("ACCESS-POINT", "Wireless access point", "نقطة وصول لاسلكية", "it_av", (0.25, 0.25, 0.05), "#1f9d8b",
           mount="ceiling", shape="access_point",
           fields=[ItemField(key="color", name_en="Colour", name_ar="اللون", kind="color"),
                   ItemField(key="ssid", name_en="Network (SSID)", name_ar="اسم الشبكة اللاسلكية", owner="system"),
                   ItemField(key="vlan", name_en="VLAN", name_ar="الشبكة الافتراضية", kind="number", owner="system")]),
        _t("NETWORK-RACK", "Network rack", "خزانة الشبكة (راك)", "it_av", (0.6, 1.0, 2.0), "#2f3238", shape="storage"),
        _t("FLOOR-BOX", "Floor box, data and power", "علبة أرضية للبيانات والكهرباء", "it_av", (0.3, 0.3, 0.02), "#546e7a"),
        _t("DATA-POINT", "Data point", "نقطة بيانات", "it_av", (0.09, 0.04, 0.09), IT, mount="wall", elevation=0.3),
        _t("COPIER", "Central photocopier", "آلة تصوير مركزية", "it_av", (1.2, 0.7, 1.2), "#3b6ea5", shape="copier",
           fields=[ItemField(key="model", name_en="Model", name_ar="الطراز"),
                   ItemField(key="network_name", name_en="Network name", name_ar="اسم الشبكة", owner="system")]),
        _t("PRINTER", "Printer", "طابعة", "it_av", (0.5, 0.5, 0.45), "#3b6ea5", shape="copier",
           fields=[ItemField(key="model", name_en="Model", name_ar="الطراز"),
                   ItemField(key="network_name", name_en="Network name", name_ar="اسم الشبكة", owner="system")]),
        _t("TV", "TV screen", "شاشة تلفاز", "it_av", (1.4, 0.1, 0.8), "#2b2b30", mount="wall", shape="screen",
           fields=[ItemField(key="size_in", name_en="Size (inches)", name_ar="المقاس (بوصة)", kind="number")]),
        _t("VIDEO-BAR", "Video-conference bar", "وحدة مؤتمرات الفيديو", "it_av", (0.9, 0.1, 0.12), "#2b2b30", mount="wall",
           elevation=1.0, fields=_model_field()),
        _t("PROJECTOR", "Projector", "جهاز عرض (بروجكتر)", "it_av", (0.4, 0.35, 0.15), "#eceff1", mount="ceiling",
           fields=_model_field()),
        _t("PROJECTION-SCREEN", "Projection screen", "شاشة إسقاط", "it_av", (2.4, 0.1, 0.1), "#eceff1", mount="ceiling"),
        _t("DIGITAL-SIGN", "Digital sign", "لافتة رقمية", "it_av", (0.6, 0.05, 1.0), "#2b2b30", mount="wall", elevation=1.2,
           shape="screen"),
        _t("ROOM-PANEL", "Room booking panel", "لوحة حجز القاعة", "it_av", (0.25, 0.04, 0.18), "#2b2b30", mount="wall",
           elevation=1.4, shape="screen"),
        # where a kiosk stands, its screen at its front: where people look for their office, and
        # later start the way to it (a system that guides people links its kiosks to these items)
        _t("KIOSK", "Wayfinding kiosk", "كشك إرشاد", "it_av", (0.6, 0.45, 1.7), "#d9782b", shape="kiosk",
           fields=_model_field()),
        # ---- building services
        _t("AC-CASSETTE", "Air-conditioning unit, ceiling cassette", "وحدة تكييف سقفية (كاسيت)", "building_services",
           (0.84, 0.84, 0.3), "#cfd8dc", mount="ceiling"),
        _t("AC-SPLIT", "Air-conditioning unit, wall split", "وحدة تكييف جدارية (سبليت)", "building_services",
           (0.9, 0.25, 0.3), "#cfd8dc", mount="wall", elevation=2.2),
        _t("THERMOSTAT", "Thermostat", "منظم الحرارة (ثرموستات)", "building_services", (0.09, 0.03, 0.09), SERVICES,
           mount="wall", elevation=1.4),
        _t("ELECTRICAL-PANEL", "Electrical panel", "لوحة توزيع الكهرباء", "building_services", (0.6, 0.2, 0.8), SERVICES,
           mount="wall", elevation=1.2),
        _t("WATER-HEATER", "Water heater", "سخان مياه", "building_services", (0.5, 0.5, 0.8), SERVICES, mount="wall",
           elevation=1.6),
        _t("HAND-DRYER", "Hand dryer", "مجفف الأيدي", "building_services", (0.3, 0.2, 0.3), SERVICES, mount="wall",
           elevation=1.0),
        # outdoors (format 0.12): a light on a pole, on a location's land
        _t("LIGHT-POLE", "Outdoor light pole", "عمود إنارة خارجي", "building_services", (0.25, 0.25, 5.0), "#90a4ae"),
        # ---- appliances
        _t("FRIDGE", "Fridge", "ثلاجة", "appliances", (0.7, 0.7, 1.8), APPLIANCE),
        _t("MICROWAVE", "Microwave", "ميكروويف", "appliances", (0.5, 0.4, 0.3), APPLIANCE, elevation=0.9),
        _t("COFFEE-MACHINE", "Coffee machine", "آلة قهوة", "appliances", (0.4, 0.45, 0.45), APPLIANCE, elevation=0.9),
        _t("WATER-DISPENSER", "Water dispenser", "مبرّد مياه", "appliances", (0.35, 0.35, 1.1), APPLIANCE),
        _t("VENDING-MACHINE", "Vending machine", "آلة بيع ذاتي", "appliances", (0.9, 0.8, 1.85), APPLIANCE),
        _t("SHREDDER", "Paper shredder", "آلة إتلاف الورق", "appliances", (0.45, 0.35, 0.7), APPLIANCE),
    ])


# ---- sets: pieces placed together, each an item of its own -------------------------------

class SetPiece(BaseModel):
    """One piece of a set: a type, where it stands from the set's middle in the set's own
    frame (metres; the set's front its -y, as an item's), and its turn (degrees counter-
    clockwise, as an item's: 0 its front the set's -y, 90 its +x, 180 its +y)."""

    type: str
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0


class ItemSet(BaseModel):
    """Pieces placed together in one go (format 0.10): a workstation (a desk, its chair, its
    pedestal…), a meeting table and its chairs. Each piece is an item of its own, with its
    own ID; placed, they are a group (Item.group), moved and turned together until it is
    undone. Studio's own: a package holds the items and their group, never the sets."""

    code: str
    name_en: str
    name_ar: str = ""
    category: Category
    pieces: list[SetPiece]


def _table_chairs(w: float, d: float) -> list[tuple[float, float, float]]:
    """Chairs round a table ``w`` long and ``d`` across, as fit.js tableChairs seats it: one
    in each 0.65 m of its long sides (its ends' 0.1 m aside), one at each end of a table
    at least 0.8 m across; each 0.35 m out from its edge, facing it: [(x, y, rotation)]."""
    n, out = max(1, int((w - 0.2) / 0.65 + 1e-9)), 0.35
    at = []
    for i in range(n):
        x = (i - (n - 1) / 2) * (w / n)
        at += [(x, d / 2 + out, 0.0), (x, -d / 2 - out, 180.0)]  # (facing the table: -y, +y)
    if d >= 0.8 - 1e-9:
        at += [(w / 2 + out, 0.0, 270.0), (-w / 2 - out, 0.0, 90.0)]  # (facing -x, +x)
    return [(round(x, 4), round(y, 4), r) for x, y, r in at]


def _workstation(grade: str, en: str, ar: str, desk: str, w: float, d: float) -> ItemSet:
    """A desk of a grade and what goes with it: its user's chair before it (its front, the
    plan's -y), a drawer pedestal under it at its user's right (an executive's desk has its
    own), a return at that side (an L, from senior staff up), a credenza behind the chair
    (from director up), visitors' chairs across it (one for a head of section, two from
    manager up: the president's, armchairs)."""
    executive, ret = grade in ("director", "c_level", "president"), grade != "junior"
    chair = "CHAIR-EXECUTIVE" if executive else "CHAIR-TASK"
    pieces = [SetPiece(type=desk)]
    pieces.append(SetPiece(type=chair, x=-0.1 if ret else 0.0, y=-(d / 2 + (0.45 if executive else 0.4)), rotation=180))
    if not executive:
        pieces.append(SetPiece(type="PEDESTAL", x=round(w / 2 - 0.3, 4), y=round(-d / 2 + 0.33, 4)))
    if ret:  # 0.8 m out from the desk's front at its user's right, its own front towards the chair
        pieces.append(SetPiece(type="DESK-RETURN", x=round(w / 2 - 0.225, 4), y=round(-d / 2 - 0.4, 4), rotation=270))
    if executive:
        pieces.append(SetPiece(type="CREDENZA", y=round(-(d / 2 + 1.175), 4), rotation=180))
    visitors = {"section_head": 1, "manager": 2, "director": 2, "c_level": 2, "president": 2}.get(grade, 0)
    if visitors:
        arm = grade == "president"
        half = max(0.45, min(w / 4, 0.62)) if arm else max(0.33, min(w / 4, 0.6))
        y = d / 2 + (0.575 if arm else 0.4)
        for x in ([0.0] if visitors == 1 else [-half, half]):
            pieces.append(SetPiece(type="ARMCHAIR" if arm else "CHAIR-VISITOR", x=round(x, 4), y=round(y, 4)))
    return ItemSet(code=f"SET-WORKSTATION-{grade.upper().replace('_', '-')}", name_en=en, name_ar=ar, category="workstations",
                   pieces=pieces)


def default_sets() -> list[ItemSet]:
    """What a Studio offers to place together: a workstation for each grade, a meeting table
    for 4 to 16 with its chairs (a board table's, 12 and more, high-backed), a lounge, a
    dining table for four, a bench desk for four. Sizes as the default types have them."""
    types = {t.code: t for t in default_catalogue().types}
    out = [_workstation(g, en, ar, desk, types[desk].width, types[desk].depth) for g, en, ar, desk in (
        ("junior", "Workstation, junior staff", "محطة عمل، موظف", "DESK-JUNIOR"),
        ("senior", "Workstation, senior staff", "محطة عمل، موظف أول", "DESK-SENIOR"),
        ("section_head", "Workstation, head of section", "محطة عمل، رئيس قسم", "DESK-SECTION-HEAD"),
        ("manager", "Workstation, manager", "محطة عمل، مدير إدارة", "DESK-MANAGER"),
        ("director", "Workstation, director", "محطة عمل، مدير", "DESK-DIRECTOR"),
        ("c_level", "Workstation, C-level", "محطة عمل، الإدارة العليا", "DESK-CLEVEL"),
        ("president", "Workstation, president", "محطة عمل، الرئيس", "DESK-PRESIDENT"))]
    for seats in (4, 6, 8, 10, 12, 14, 16):
        t = types[f"MEETING-TABLE-{seats}"]
        chair = "CHAIR-BOARD" if seats >= 12 else "CHAIR-MEETING"
        ar = f"{seats} مقاعد" if seats <= 10 else f"{seats} مقعدًا"
        out.append(ItemSet(code=f"SET-MEETING-{seats}", name_en=f"Meeting set, {seats} seats",
                           name_ar=f"طقم اجتماعات، {ar}", category="tables",
                           pieces=[SetPiece(type=t.code)] + [SetPiece(type=chair, x=x, y=y, rotation=r)
                                                             for x, y, r in _table_chairs(t.width, t.depth)]))
    out.append(ItemSet(code="SET-DINING-4", name_en="Dining set, 4 seats", name_ar="طقم طعام، 4 مقاعد", category="tables",
                       pieces=[SetPiece(type="TABLE-DINING-4")] + [SetPiece(type="CHAIR-VISITOR", x=x, y=y, rotation=r)
                                                                   for x in (-0.3, 0.3) for y, r in ((0.7, 0.0), (-0.7, 180.0))]))
    out.append(ItemSet(code="SET-LOUNGE", name_en="Lounge set", name_ar="طقم جلوس", category="soft_seating", pieces=[
        SetPiece(type="TABLE-COFFEE"), SetPiece(type="SOFA", y=0.95),
        SetPiece(type="ARMCHAIR", x=1.0, rotation=270), SetPiece(type="ARMCHAIR", x=-1.0, rotation=90)]))
    out.append(ItemSet(code="SET-BENCH-4", name_en="Bench desk, 4 places", name_ar="مكتب جماعي، 4 أماكن", category="workstations",
                       pieces=[SetPiece(type="DESK-BENCH-4")] + [SetPiece(type="CHAIR-TASK", x=x, y=y, rotation=r)
                                                                 for x in (-0.7, 0.7) for y, r in ((-1.2, 180.0), (1.2, 0.0))]))
    return out


def sets_of(cat: Catalogue) -> list[ItemSet]:
    """The sets a catalogue can place: those whose every piece is a type of it, not retired."""
    live = {t.code for t in cat.types if not t.retired}
    return [s for s in default_sets() if all(p.type in live for p in s.pieces)]


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
    for t in cat.types:  # a model is a type's when its file is beside the catalogue
        t.model = model_path(t.code) if (Path(folder) / model_path(t.code)).is_file() else None
    return cat


def models_in(folder: str | Path):
    """The models beside a data folder's catalogue.json (models/<code>.glb), by type code:
    what an export carries."""
    def model(code: str) -> bytes | None:
        path = Path(folder) / model_path(code)
        return path.read_bytes() if path.is_file() else None
    return model


def read(text: str) -> tuple[Catalogue, bool]:
    """A catalogue from its JSON, with what this Studio says of its own default types
    where an older file says nothing (how many people work at one, the grade a desk is
    for, how it is drawn, a camera's view, whether it serves a door) or says what an older Studio gave them (OLD_DEFAULTS); and
    whether any was filled in. One older than CATALOGUE_VERSION 2 (format 0.10) gives its
    default types this Studio's category and shape: categories by what a thing is for, and a
    desk and a table drawn alone (their chairs types of their own)."""
    raw = json.loads(text)
    defaults = {t.code: t for t in default_catalogue().types}
    filled = False
    older = isinstance(raw, dict) and isinstance(raw.get("format_version"), int) and raw["format_version"] < CATALOGUE_VERSION
    if older:
        raw["format_version"], filled = CATALOGUE_VERSION, True
    for t in raw.get("types", []):
        d = defaults.get(t.get("code"))
        if d is None:
            continue
        if older:
            t["category"], t["shape"] = d.category, d.shape
        for key in ("workplaces", "grade", "shape", "view_deg", "range_m", "serves_door"):
            if key not in t:
                t[key], filled = getattr(d, key), True
        for key, olds in OLD_DEFAULTS.get(d.code, {}).items():
            v = t.get(key)
            if (v.lower() if isinstance(v, str) else v) in olds and not isinstance(v, bool):
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
        for key, top, what in (("view_deg", 360, "degrees across, more than 0 and at most 360"),
                               ("range_m", 500, "metres, more than 0 and at most 500")):
            v = t.get(key)
            if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= top):
                raise ValueError(f"{which}: {key} is in {what}")
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
        if t.get("plan") is not None:
            check_plan(which, t["plan"], t.get("width"), t.get("depth"))


def check_plan(which: str, plan, width, depth) -> None:
    """A type's plan (format 0.13.2) as FORMAT.md has it: lines of two points or more, rings
    of three or more, every point a number within the type's box to 1 mm, at most
    MAX_PLAN_POINTS. ValueError, saying which type and why."""
    import math

    w = width if isinstance(width, (int, float)) and not isinstance(width, bool) else 1.0
    d = depth if isinstance(depth, (int, float)) and not isinstance(depth, bool) else 0.6
    if not isinstance(plan, dict) or not isinstance(plan.get("lines"), list) or not plan["lines"]:
        raise ValueError(f"{which}: its plan has no lines")
    n = 0

    def points(run, least: int, what: str) -> None:
        nonlocal n
        if not isinstance(run, list) or len(run) < least:
            raise ValueError(f"{which}: its plan has {what} of fewer than {least} points")
        for q in run:
            ok = (isinstance(q, (list, tuple)) and len(q) == 2
                  and all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in q))
            if not ok or abs(q[0]) > w / 2 + 0.001 or abs(q[1]) > d / 2 + 0.001:
                raise ValueError(f"{which}: its plan has a point {str(q)[:40]} outside its box ({w} x {d} m)")
        n += len(run)

    for line in plan["lines"]:
        points(line, 2, "a line")
    outline = plan.get("outline")
    if outline is not None:
        if not isinstance(outline, list):
            raise ValueError(f"{which}: its plan's outline is not a list of polygons")
        for poly in outline:
            if not isinstance(poly, list) or not poly:
                raise ValueError(f"{which}: its plan's outline has a polygon of no ring")
            for ring in poly:
                points(ring, 3, "a ring")
    if n > MAX_PLAN_POINTS:
        raise ValueError(f"{which}: its plan has {n} points, more than {MAX_PLAN_POINTS}")


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
    tmp.write_text(json.dumps(cat.model_dump(exclude={"types": {"__all__": {"model"}}}), ensure_ascii=False, indent=1)
                   + "\n", encoding="utf-8")
    tmp.replace(path)
