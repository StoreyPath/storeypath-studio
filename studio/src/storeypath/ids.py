"""Hierarchical object IDs.

    PROJECT-LOCATION-BUILDING-FLOOR-OBJECT      e.g. K7Q2XM-RUH-HQ-F02-0142

Every prefix is itself an ID: ``K7Q2XM-RUH`` is the location, ``K7Q2XM-RUH-HQ-F02``
the floor. Segments are upper-case letters and digits; the hyphen is reserved as
the separator.

- The project code is generated (random, Crockford base32) so independently
  created workspaces can share one deployment without colliding.
- Object codes are numeric and allocated from a per-building counter, so a code
  is never reused and the object's type is not part of its ID (reclassifying an
  object must not change its ID). Elevators and stairs reuse one code on every
  floor they serve.

Items (furniture and equipment) are assets, not places: their IDs are random tags
with a check symbol, 7K2Q-XM9F-4DP, of no project or place (below).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass

SEPARATOR = "-"
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # no I, L, O, U
PROJECT_CODE_LENGTH = 6
LEVELS = ("project", "location", "building", "floor", "object")
SEGMENT_MAX_LENGTH = 16
# the longest an ID can be (every level's segment at its longest): anything longer is
# refused before it is taken apart
MAX_ID_LENGTH = len(LEVELS) * SEGMENT_MAX_LENGTH + len(LEVELS) - 1

# ASCII only, the whole string (\Z: no trailing line break, as $ would let through)
_SEGMENT_RE = re.compile(rf"\A[A-Z0-9]{{1,{SEGMENT_MAX_LENGTH}}}\Z")


def generate_project_code() -> str:
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(PROJECT_CODE_LENGTH))


def validate_segment(code: str) -> str:
    if not isinstance(code, str) or not _SEGMENT_RE.fullmatch(code):
        raise ValueError(
            f"invalid code {code!r}: use 1-16 upper-case letters or digits, no hyphens"
        )
    return code


def make_id(*segments: str) -> str:
    if not 1 <= len(segments) <= len(LEVELS):
        raise ValueError(f"an ID has 1 to {len(LEVELS)} segments, got {len(segments)}")
    return SEPARATOR.join(validate_segment(s) for s in segments)


def child_id(parent_id: str, code: str) -> str:
    """The ID of ``code`` inside the object ``parent_id``."""
    return make_id(*parse_id(parent_id).segments, code)


@dataclass(frozen=True)
class ParsedId:
    segments: tuple[str, ...]

    @property
    def level(self) -> str:
        return LEVELS[len(self.segments) - 1]

    @property
    def project(self) -> str:
        return self.segments[0]

    @property
    def code(self) -> str:
        """The last segment: this object's own code within its parent."""
        return self.segments[-1]

    @property
    def parent(self) -> str | None:
        return SEPARATOR.join(self.segments[:-1]) or None

    def prefix(self, level: str) -> str:
        """The ID of the ancestor at ``level`` (or this ID itself)."""
        depth = LEVELS.index(level) + 1
        if depth > len(self.segments):
            raise ValueError(f"{self} has no {level} segment")
        return SEPARATOR.join(self.segments[:depth])

    def __str__(self) -> str:
        return SEPARATOR.join(self.segments)


def parse_id(value: str) -> ParsedId:
    """A place's ID taken apart. An item's ID (is_item_id) is not a place's: refused."""
    if len(value) > MAX_ID_LENGTH:
        raise ValueError(f"invalid ID {value[:MAX_ID_LENGTH]!r}…: longer than an ID can be ({MAX_ID_LENGTH})")
    if is_item_id(value):
        raise ValueError(f"{value} is an item's ID (an asset's), not a place's")
    segments = tuple(value.split(SEPARATOR))
    make_id(*segments)  # validates count and every segment
    return ParsedId(segments)


# ---- items: asset IDs ---------------------------------------------------------------
#
# Items (furniture and equipment) are assets: their IDs are not part of the place
# hierarchy, and not the project's either. A desk carried to another room, floor or
# building keeps its ID; where it is, and the project it is of, are data. Format 0.8:
# ten random symbols of Crockford's base32 and a check symbol, in groups of 4-4-3:
# 7K2Q-XM9F-4DP. The check symbol is Luhn mod 32 over the ten symbols' values: it
# catches every symbol mistyped, and every two neighbours swapped but 0 and Z.

ITEM_ID_SYMBOLS = 10  # random; then the check symbol
ITEM_ID_GROUPS = (4, 4, 3)
ITEM_ID_LENGTH = sum(ITEM_ID_GROUPS) + len(ITEM_ID_GROUPS) - 1  # 13: 7K2Q-XM9F-4DK
ITEM_ID_TYPED_MAX = 64  # what a person typed, before it is read: longer is not an item's ID
_N = len(CROCKFORD_ALPHABET)  # 32
_VALUE = {c: v for v, c in enumerate(CROCKFORD_ALPHABET)}
# what a person may type for each symbol: either case, O for 0, I and L for 1
_TYPED = {**_VALUE, **{c.lower(): v for c, v in _VALUE.items()}, "O": 0, "o": 0, "I": 1, "i": 1, "L": 1, "l": 1}
_IGNORED = frozenset("- \t\n\r\v\f")  # hyphens and spaces, wherever they are

# The form of an item's ID in formats 0.6 and 0.7: the project's code, -I and six
# digits (K7Q2XM-I000142). Only a reader of those packages meets it.
LEGACY_ITEM_CODE_RE = re.compile(r"\AI[0-9]{6}\Z")  # ASCII digits, the whole string
LEGACY_ITEM_ID_MAX_LENGTH = SEGMENT_MAX_LENGTH + len(SEPARATOR) + 7


def _luhn_sum(values) -> int:
    """Luhn mod 32: the values added, the 2nd, 4th, … 10th doubled first (every second
    one back from the last of the ten), a doubled value's two base-32 digits added
    (2v - 31 for v of 16 and more); mod 32. Of ten symbols and their check, 0."""
    total = 0
    for k, v in enumerate(values):
        if k % 2 == 1:
            v *= 2
            v = v // _N + v % _N
        total += v
    return total % _N


def item_check_symbol(symbols: str) -> str:
    """The check symbol of an item ID's ten symbols (canonical: Crockford's alphabet,
    upper case): Luhn mod 32 over their values."""
    if len(symbols) != ITEM_ID_SYMBOLS or any(c not in _VALUE for c in symbols):
        raise ValueError(f"{symbols!r}: not {ITEM_ID_SYMBOLS} symbols of Crockford's base32")
    return CROCKFORD_ALPHABET[(_N - _luhn_sum([_VALUE[c] for c in symbols])) % _N]


def _item_id_of(symbols: str) -> str:
    """Eleven symbols (with the check) written as an item's ID: 4-4-3."""
    out, at = [], 0
    for n in ITEM_ID_GROUPS:
        out.append(symbols[at:at + n])
        at += n
    return SEPARATOR.join(out)


def new_item_id(taken=None) -> str:
    """A new item's ID: ten symbols from a cryptographically secure source, and their
    check symbol. ``taken(id)`` says whether one is in use already (in the project,
    in Studio's database): another is drawn then."""
    while True:
        symbols = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(ITEM_ID_SYMBOLS))
        value = _item_id_of(symbols + item_check_symbol(symbols))
        if taken is None or not taken(value):
            return value


def is_item_id(value) -> bool:
    """Whether ``value`` is an item's ID as packages and storage write it (format 0.8):
    4-4-3 symbols of Crockford's base32, upper case, with hyphens, its check symbol
    right."""
    if not isinstance(value, str) or len(value) != ITEM_ID_LENGTH:
        return False
    groups = value.split(SEPARATOR)
    if [len(g) for g in groups] != list(ITEM_ID_GROUPS):
        return False
    symbols = "".join(groups)
    return all(c in _VALUE for c in symbols) and _luhn_sum([_VALUE[c] for c in symbols]) == 0


def normalize_item_id(text) -> str | None:
    """An item's ID as a person typed it (a search box, the command line, an API asked
    by a person), as written: either case; O read as 0, I and L as 1; hyphens and
    spaces left out. Eleven symbols with their check symbol right are an item's ID;
    anything else is not (None)."""
    if not isinstance(text, str) or len(text) > ITEM_ID_TYPED_MAX:
        return None
    values = []
    for c in text:
        if c in _IGNORED:
            continue
        v = _TYPED.get(c)
        if v is None:
            return None
        values.append(v)
    if len(values) != ITEM_ID_SYMBOLS + 1 or _luhn_sum(values) != 0:
        return None
    return _item_id_of("".join(CROCKFORD_ALPHABET[v] for v in values))


def item_id_for_legacy(value: str) -> str:
    """The ID Studio gives an item of a package of format 0.6 or 0.7 it opens: made from
    its ID there (K7Q2XM-I000142, the project's in it), so the same in every Studio,
    whichever of the project's packages is opened first, and in no other project."""
    digest = int.from_bytes(hashlib.sha256(f"storeypath item {value}".encode()).digest()[:7], "big") >> 6
    symbols = "".join(CROCKFORD_ALPHABET[(digest >> (5 * (ITEM_ID_SYMBOLS - 1 - k))) & (_N - 1)]
                      for k in range(ITEM_ID_SYMBOLS))  # 50 bits, five a symbol
    return _item_id_of(symbols + item_check_symbol(symbols))


def is_legacy_item_id(value) -> bool:
    """Whether ``value`` has the form of an item's ID in formats 0.6 and 0.7 (two
    segments, the second I and six digits: K7Q2XM-I000142)."""
    if not isinstance(value, str) or len(value) > LEGACY_ITEM_ID_MAX_LENGTH:
        return False
    segments = value.split(SEPARATOR)
    return (len(segments) == 2 and bool(_SEGMENT_RE.fullmatch(segments[0]))
            and bool(LEGACY_ITEM_CODE_RE.fullmatch(segments[1])))


def format_object_code(sequence: int) -> str:
    if sequence < 1:
        raise ValueError("object sequence starts at 1")
    return f"{sequence:04d}"


def default_floor_code(ordinal: int) -> str:
    """F00 for the ground floor, F01, F02… above it, B01, B02… below it."""
    return f"F{ordinal:02d}" if ordinal >= 0 else f"B{-ordinal:02d}"
