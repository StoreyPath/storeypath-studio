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
    # Added in format 0.1 for homes and for prayer rooms.
    BEDROOM = "bedroom"
    LIVING_ROOM = "living_room"  # sitting room, lounge, majlis
    DINING_ROOM = "dining_room"
    BATHROOM = "bathroom"  # a private bathroom; public toilets are restroom
    DRESSING_ROOM = "dressing_room"
    LAUNDRY = "laundry"
    PRAYER_ROOM = "prayer_room"
    PARKING = "parking"  # garage, carport, parking
    BALCONY = "balcony"
    TERRACE = "terrace"  # terrace, roof deck, veranda, porch, courtyard
    OPEN_TO_BELOW = "open_to_below"  # a void over the floor below


class OpeningType(StrEnum):
    DOOR = "door"
    OPENING = "opening"  # a way through with no door: a doorway, or where open-plan rooms meet
    WINDOW = "window"  # not a way through: glazing in a wall


# Spaces that connect floors vertically. Their per-floor objects share one
# object code within a building (see ids.py).
VERTICAL_TYPES = frozenset({SpaceType.ELEVATOR, SpaceType.STAIRS, SpaceType.ESCALATOR})
