"""Canonical object types.

These names are part of the exchange format. A type, once published, is never
renamed or removed; new types may only be added in a later format version.
"""

from enum import StrEnum


class SpaceType(StrEnum):
    OFFICE = "office"
    ROOM = "room"
    MEETING_ROOM = "meeting_room"
    CORRIDOR = "corridor"
    LOBBY = "lobby"
    ELEVATOR = "elevator"
    STAIRS = "stairs"
    ESCALATOR = "escalator"
    RAMP = "ramp"
    RESTROOM = "restroom"
    KITCHEN = "kitchen"
    STORAGE = "storage"
    UTILITY = "utility"
    SHAFT = "shaft"
    OPEN_AREA = "open_area"
    UNSPECIFIED = "unspecified"


class OpeningType(StrEnum):
    DOOR = "door"


# Spaces that connect floors vertically. Their per-floor objects share one
# object code within a building (see ids.py).
VERTICAL_TYPES = frozenset({SpaceType.ELEVATOR, SpaceType.STAIRS, SpaceType.ESCALATOR})
