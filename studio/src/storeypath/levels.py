"""Floor levels from the level labels on a drawing's sections and elevations.

A plan has no heights, but the sections and elevations on the same sheets mark
every floor's level: "+0.35 GROUND FLOOR SLAB LVL.", "+3.65 FIRST FLOOR SLAB
LVL.", "+6.95 ROOF SLAB LVL.", "+8.65 PARAPET LVL.". Read together they give each
floor's height (the levels of consecutive floors) and the parapet around the roof.
A level that sheets repeat counts once, at its most common value. Plain English is
read by rule; other languages and wordings by the language model.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from ezdxf.document import Drawing

from .llm import LocalModel, ModelUnavailable, read_level_labels
from .sheets import ORDINAL_WORDS

DEFAULT_HEIGHT_M = 3.5  # floor to floor, when nothing shows it
DEFAULT_PARAPET_M = 1.1  # a guard around a terrace or balcony, when nothing shows it
HEIGHT_M = (2.2, 8.0)  # floor-to-floor heights outside this are misread
PARAPET_M = (0.3, 2.5)
MAX_ASKED = 16

# "+3.65 FIRST FLOOR SLAB LVL.", "%%p0.00 GROUND LVL." (%%p is AutoCAD's ±), "+3650 FFL"
LEVEL = re.compile(r"^\s*(?P<sign>%%[pP]|±|\+|-)?\s*(?P<value>\d+(?:[.,]\d+)?)\s*(?:m\b\.?)?\s*(?P<rest>.*?)\s*$")
FLOORISH = re.compile(r"\b(floor|flr|slab|ssl|ffl|fl|level|lvl|storey|story)\b", re.IGNORECASE)
NATURAL_GROUND = re.compile(r"\b(gr\.?\s*lvl|ground\s+(lvl|level)|ngl|fgl|natural|road|street|site)\b", re.IGNORECASE)
PARAPET = re.compile(r"\bparapet\b", re.IGNORECASE)
TOP = re.compile(r"\b(stair|staircase|lift|machine|penthouse|upper|mumty|head\s*room)\b.*\broof\b|\broof\b.*\b(stair|lift)\b",
                 re.IGNORECASE)
ROOF = re.compile(r"\broof\b", re.IGNORECASE)


@dataclass
class LevelMark:
    text: str
    level: float  # metres
    what: str  # "floor", "roof" (the main roof), "top" (roof of the rooms on the roof), "parapet", "ground"
    floor: int | None = None  # for "floor"
    source: str = "rules"  # or "model"


@dataclass
class FloorLevels:
    """What a drawing's level labels say about a building's floors."""

    levels: dict[int, float] = field(default_factory=dict)  # floor → its level (m)
    heights: dict[int, float] = field(default_factory=dict)  # floor → floor-to-floor height (m)
    roof: int | None = None  # the floor on the roof (one above the highest named floor)
    parapet: float | None = None  # the roof's parapet, above the roof slab (m)
    marks: list[LevelMark] = field(default_factory=list)

    def height(self, ordinal: int) -> float:
        """A floor's height: from the drawing, else the building's typical one."""
        return self.heights.get(ordinal, self.typical_height())

    def typical_height(self) -> float:
        return round(statistics.median(self.heights.values()), 2) if self.heights else DEFAULT_HEIGHT_M

    def parapet_of(self, ordinal: int) -> float:
        return self.parapet if self.parapet is not None and ordinal == self.roof else DEFAULT_PARAPET_M

    def summary(self) -> str:
        """For people: "ground floor +0.35, first floor +3.65, roof +6.95, …"."""
        names = {-1: "basement", 0: "ground floor", 1: "first floor", 2: "second floor", 3: "third floor"}
        parts = [f"{names.get(n, f'floor {n}') if n != self.roof else 'roof'} {_signed(v)}"
                 for n, v in sorted(self.levels.items())]
        top = _mode([m.level for m in self.marks if m.what == "top"])
        if top is not None:
            parts.append(f"stair roof {_signed(top)}")
        if self.parapet is not None:
            parts.append(f"parapet {self.parapet:.2f} m above the roof")
        return ", ".join(parts)


def read_level_marks(doc: Drawing, model: LocalModel | None = None) -> list[LevelMark]:
    """The level labels anywhere in the drawing: the plain ones by rule, others by the
    language model when there is one."""
    marks, unread = [], {}
    for text in _texts(doc):
        m = LEVEL.match(text)
        if not m or not re.search(r"[^\W\d_]", m["rest"]):
            continue
        level = _metres(m["sign"], m["value"])
        if level is None:
            continue
        mark = _by_rule(text, m["rest"], level)
        words = [w for w in re.findall(r"[^\W\d_]+", m["rest"]) if not FLOORISH.fullmatch(w)]
        if mark is not None:
            marks.append(mark)
        elif words and (FLOORISH.search(m["rest"]) or not m["rest"].isascii()):  # not "+10.95 LVL." alone
            unread.setdefault(m["rest"].strip(), []).append((text, level))
    if unread and model is not None and model.available():
        asked = list(unread)[:MAX_ASKED]
        try:
            answers = read_level_labels(model, asked)
        except ModelUnavailable:
            answers = {}
        for rest in asked:
            what, floor = answers.get(rest, ("other", None))
            if what in ("floor", "roof", "top", "parapet", "ground") and (what != "floor" or floor is not None):
                marks += [LevelMark(text, level, what, floor, "model") for text, level in unread[rest]]
    return marks


def floor_levels(marks: list[LevelMark]) -> FloorLevels:
    """Each floor's level and height, the roof and its parapet, from the marks."""
    out = FloorLevels(marks=marks)
    by_floor: dict[int, list[float]] = {}
    for m in marks:
        if m.what == "floor":
            by_floor.setdefault(m.floor, []).append(m.level)
    out.levels = {n: _mode(v) for n, v in by_floor.items()}
    roof = _mode([m.level for m in marks if m.what == "roof"])
    if roof is not None:
        out.roof = (max(out.levels) + 1) if out.levels else None
        if out.roof is not None:
            out.levels[out.roof] = roof
    for n, level in out.levels.items():
        if n + 1 in out.levels and _within(out.levels[n + 1] - level, HEIGHT_M):
            out.heights[n] = round(out.levels[n + 1] - level, 2)
    top = _mode([m.level for m in marks if m.what == "top"])
    if out.roof is not None and top is not None and _within(top - roof, HEIGHT_M):
        out.heights[out.roof] = round(top - roof, 2)
    parapet = _mode([m.level for m in marks if m.what == "parapet"])
    if roof is not None and parapet is not None and _within(parapet - roof, PARAPET_M):
        out.parapet = round(parapet - roof, 2)
    return out


def _by_rule(text: str, rest: str, level: float) -> LevelMark | None:
    if PARAPET.search(rest):
        return LevelMark(text, level, "parapet")
    if TOP.search(rest):
        return LevelMark(text, level, "top")
    if NATURAL_GROUND.search(rest) and not re.search(r"\bfloor\b", rest, re.IGNORECASE):
        return LevelMark(text, level, "ground")
    if ROOF.search(rest) and FLOORISH.search(rest):
        return LevelMark(text, level, "roof")
    if FLOORISH.search(rest) and rest.isascii():
        for pattern, ordinal in ORDINAL_WORDS:
            if pattern.search(rest):
                return LevelMark(text, level, "floor", ordinal)
    return None


def _metres(sign: str | None, value: str) -> float | None:
    """Levels are written in metres (+3.65), sometimes in millimetres (+3650)."""
    v = float(value.replace(",", "."))
    if "." not in value and "," not in value and v:
        if v < 100:
            return None  # +50: too short to tell (but ±0 is 0 either way)
        v /= 1000
    return -v if sign == "-" else v


def _mode(values: list[float]) -> float | None:
    if not values:
        return None
    counts = Counter(round(v, 2) for v in values)
    return max(counts, key=lambda v: (counts[v], -abs(v)))


def _within(v: float, band: tuple[float, float]) -> bool:
    return band[0] <= v <= band[1]


def _signed(v: float) -> str:
    return f"{v:+.2f}" if v else "±0.00"


def _texts(doc: Drawing) -> list[str]:
    out = []
    for layout in doc.layouts:
        for t in layout.query("TEXT MTEXT"):
            out.append(" ".join((t.plain_text() if t.dxftype() == "MTEXT" else t.dxf.text).split()))
    return out
