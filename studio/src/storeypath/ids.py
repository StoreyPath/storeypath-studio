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
"""

from __future__ import annotations

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
    if len(value) > MAX_ID_LENGTH:
        raise ValueError(f"invalid ID {value[:MAX_ID_LENGTH]!r}…: longer than an ID can be ({MAX_ID_LENGTH})")
    segments = tuple(value.split(SEPARATOR))
    make_id(*segments)  # validates count and every segment
    return ParsedId(segments)


# Items (furniture and equipment) are not part of the place hierarchy: a desk carried
# to another floor keeps its ID, and where it is, is data. PROJECT-I000142.
ITEM_CODE_RE = re.compile(r"\AI[0-9]{6}\Z")  # ASCII digits, the whole string
ITEM_ID_MAX_LENGTH = SEGMENT_MAX_LENGTH + len(SEPARATOR) + 7


def make_item_id(project: str, sequence: int) -> str:
    if sequence < 1:
        raise ValueError("item sequence starts at 1")
    return make_id(project, f"I{sequence:06d}")


def is_item_id(value: str) -> bool:
    """Whether ``value`` is an item's ID (two segments, the second I and six digits)."""
    if not isinstance(value, str) or len(value) > ITEM_ID_MAX_LENGTH:
        return False
    segments = value.split(SEPARATOR)
    return (len(segments) == 2 and bool(_SEGMENT_RE.fullmatch(segments[0]))
            and bool(ITEM_CODE_RE.fullmatch(segments[1])))


def format_object_code(sequence: int) -> str:
    if sequence < 1:
        raise ValueError("object sequence starts at 1")
    return f"{sequence:04d}"


def default_floor_code(ordinal: int) -> str:
    """F00 for the ground floor, F01, F02… above it, B01, B02… below it."""
    return f"F{ordinal:02d}" if ordinal >= 0 else f"B{-ordinal:02d}"
