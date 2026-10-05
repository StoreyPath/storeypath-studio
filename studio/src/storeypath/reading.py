"""What the texts in a drawing mean: rules first, the language model for the rest.

The layer-mapping rules know common words (OFFICE, CORRIDOR, WC…). Everything
else — abbreviations, misspellings, other languages — goes to the local language
model when there is one. Every answer is kept in the workspace, so converting the
same drawing again gives the same result, with or without the model.
"""

from __future__ import annotations

import re

from ezdxf.document import Drawing

from .cad import UnitClue, UnitsDecision, decide_units
from .llm import LABEL_QUESTION, LocalModel, ModelUnavailable, read_labels, read_unit_notes
from .profile import Profile
from .types import SpaceType
from .workspace import Reading, Workspace

# Texts that never name a room: levels, heights, tags, directions, scales, sizes.
NOT_A_ROOM = re.compile(
    r"""^\s*(
        [±+\-]?\s*\d+([.,]\d+)?\s*(m|mm)?\s*(ffl|fgl|ssl|lvl|tos|top|fl|sfl|ngl)?    # +0.45 FFL, 9.85
      | (ffl|fgl|ssl|lvl|el\.?|level)\s*[±+\-]?\s*\d+([.,]\d+)?                    # SSL+0.35, LEVEL 0.00
      | [a-z]{1,2}-?\d{1,3}[a-z]?                                                   # D1, W4, DW-12
      | up|dn|down|n|north
      | (\d+\s*)?(steps?|ramp)\s+(up|down|dn)(\s+\d+\s*steps?\s+(up|down|dn))*         # 3 STEPS UP, RAMP UP: level notes
      | hidden\s+door|open(\s+(to\s+)?below)?|below                                     # notes in a plan, not rooms
      | ((a\.?\s?/?\s?c|s\.?\s?a\.?\s?c|split|outdoor|indoor|condensing|packaged?)\s+)+units?  # SAC UNIT, A/C SPLIT UNIT
      | (fcu|ahu|cdu|vrf|vrv)(\s*-?\s*\d+)?                                          # FCU-1, AHU
      | scale\b.*|\d+\s*[x×]\s*\d+.*                                               # SCALE 1:100, 1200x600
      | \W*
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


class TextReader:
    """Reads texts for one conversion; remembers every answer in the workspace."""

    def __init__(self, ws: Workspace, profile: Profile, model: LocalModel | None = None):
        self.ws = ws
        self.profile = profile
        self.model = model if model is not None and model.available() else None
        self.question = f"{self.model.name}/{LABEL_QUESTION}" if self.model else None
        self.model_failed: str | None = None

    def _reading(self, text: str) -> Reading | None:
        """The saved answer for a text. A model's answer to an older question, or from
        another model, is set aside while a model is here to ask again (the question
        improves: SAC UNIT, SALAH); without one it stands. A person's always stands."""
        r = self.ws.readings.get(text)
        if r is not None and r.source == "model" and self.model is not None and r.asked != self.question:
            return None
        return r

    # ---- room or not ----------------------------------------------------------

    def is_room_name(self, text: str) -> bool | None:
        """True or False when known (rules, earlier answers); None when unsure."""
        text = text.strip()
        if not text or NOT_A_ROOM.match(text) or len(text) > 40 or len(text.split()) > 5:
            return False  # long texts are notes, not names
        if self.profile.classify(text, [], "")[0] != SpaceType.UNSPECIFIED:
            return True
        r = self._reading(text)
        return None if r is None else r.type is not None

    def learn(self, texts: list[str], rooms_only: bool = False) -> None:
        """Ask the model about the texts the rules and earlier answers do not know."""
        if self.model is None:
            return
        unknown = [t.strip() for t in texts if t.strip() and self._unknown(t.strip(), rooms_only)]
        if not unknown:
            return
        try:
            answers = read_labels(self.model, unknown, rooms_only=rooms_only)
        except ModelUnavailable as e:
            self.model_failed = str(e)
            self.model = None
            return
        for text, reading in answers.items():
            self.ws.readings[text] = Reading(type=reading.type, source="model", rooms_only=rooms_only,
                                             asked=self.question)

    def _unknown(self, text: str, rooms_only: bool) -> bool:
        if NOT_A_ROOM.match(text) or self.profile.classify(text, [], "")[0] != SpaceType.UNSPECIFIED:
            return False
        r = self._reading(text)
        # A "not a room" answer is asked again when the text turns out to label a room.
        return r is None or (rooms_only and r.type is None)

    # ---- room types -----------------------------------------------------------

    def room_type(self, name: str) -> SpaceType | None:
        """The type the model gave a room name (rules are applied elsewhere)."""
        r = self.ws.readings.get(name.strip())
        return r.type if r is not None else None


# ---- notes that state the units ---------------------------------------------------

# "ALL DIMENSIONS ARE IN MM", "UNITS: METRES": read by rule. Other languages and
# wordings go to the language model.
UNIT_STATEMENT = re.compile(
    r"\b(?:dim(?:ension)?s?|measurements?|units?)\b\.?\s*(?:are\s+)?(?:given\s+|shown\s+)?(?:in|:|=)\s*"
    r"(?P<unit>mm|millimet(?:er|re)s?|cm|centimet(?:er|re)s?|met(?:er|re)s?|m|inch(?:es)?|in|feet|foot|ft)\b",
    re.IGNORECASE,
)
# What the model is asked about: notes that talk about dimensions or units and name a
# unit, in a few languages. Sizes of single things ("20mm TILES") do not qualify.
NOTE_ABOUT_UNITS = re.compile(r"dim|unit|measur|cote|mesure|ma(?:ß|ss)e|medida|misur|afmeting|أبعاد|ابعاد|مقاس|قياس|وحد",
                              re.IGNORECASE)
NAMES_A_UNIT = re.compile(r"mm|cm|met(?:er|re|ro)|millim|centim|inch|feet|\bft\b|\bm\b|zoll|fu(?:ß|ss)|pulgad|pouce|"
                          r"pied|ملم|مم|متر|سم|سنتي|بوصة|إنش|قدم", re.IGNORECASE)
MAX_UNIT_NOTES = 8


def read_units(doc: Drawing, model: LocalModel | None = None) -> UnitsDecision:
    """The units to read a drawing in: what is drawn, and any note stating them."""
    return decide_units(doc, unit_note(doc, model))


def unit_note(doc: Drawing, model: LocalModel | None = None) -> UnitClue | None:
    """A note on the drawing that states its units, read by rule, else by the
    language model when there is one."""
    notes = _notes(doc)
    for note in notes:
        m = UNIT_STATEMENT.search(note)
        if m:
            return UnitClue("note", [_unit_of(m["unit"])], note)
    if model is None or not model.available():
        return None
    asked = [n for n in notes if NOTE_ABOUT_UNITS.search(n) and NAMES_A_UNIT.search(n)][:MAX_UNIT_NOTES]
    if not asked:
        return None
    try:
        answers = read_unit_notes(model, asked)
    except ModelUnavailable:
        return None
    return next((UnitClue("note", [answers[n]], n) for n in asked if answers.get(n)), None)


def _notes(doc: Drawing) -> list[str]:
    texts = []
    for layout in doc.layouts:
        for t in layout.query("TEXT MTEXT"):
            texts.append(t.plain_text() if t.dxftype() == "MTEXT" else t.dxf.text)
    return [t for t in dict.fromkeys(" ".join(t.split()) for t in texts) if 4 <= len(t) <= 200]


def _unit_of(word: str) -> str:
    w = word.lower()
    if w.startswith(("mm", "millim")):
        return "mm"
    if w.startswith(("cm", "centim")):
        return "cm"
    if w.startswith("m"):
        return "m"
    if w.startswith("in"):
        return "in"
    return "ft"
