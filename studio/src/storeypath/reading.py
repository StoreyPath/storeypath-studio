"""What the texts in a drawing mean: rules first, the language model for the rest.

The layer-mapping rules know common words (OFFICE, CORRIDOR, WC…). Everything
else — abbreviations, misspellings, other languages — goes to the local language
model when there is one. Every answer is kept in the workspace, so converting the
same drawing again gives the same result, with or without the model.
"""

from __future__ import annotations

import re

from .llm import LocalModel, ModelUnavailable, read_labels
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
        self.model_failed: str | None = None

    # ---- room or not ----------------------------------------------------------

    def is_room_name(self, text: str) -> bool | None:
        """True or False when known (rules, earlier answers); None when unsure."""
        text = text.strip()
        if not text or NOT_A_ROOM.match(text) or len(text) > 40 or len(text.split()) > 5:
            return False  # long texts are notes, not names
        if self.profile.classify(text, [], "")[0] != SpaceType.UNSPECIFIED:
            return True
        r = self.ws.readings.get(text)
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
            self.ws.readings[text] = Reading(type=reading.type, source="model", rooms_only=rooms_only)

    def _unknown(self, text: str, rooms_only: bool) -> bool:
        if NOT_A_ROOM.match(text) or self.profile.classify(text, [], "")[0] != SpaceType.UNSPECIFIED:
            return False
        r = self.ws.readings.get(text)
        # A "not a room" answer is asked again when the text turns out to label a room.
        return r is None or (rooms_only and r.type is None)

    # ---- room types -----------------------------------------------------------

    def room_type(self, name: str) -> SpaceType | None:
        """The type the model gave a room name (rules are applied elsewhere)."""
        r = self.ws.readings.get(name.strip())
        return r.type if r is not None else None
