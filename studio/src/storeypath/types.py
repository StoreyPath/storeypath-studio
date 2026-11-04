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
    # Added in format 0.13.1, utility before: switchboards and distribution boards; a
    # floor's telecom room (an intermediate distribution frame: racks, patch panels)
    ELECTRICAL_ROOM = "electrical_room"
    IDF_ROOM = "idf_room"
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
    TERRACE = "terrace"  # terrace, roof garden, veranda, porch, courtyard
    OPEN_TO_BELOW = "open_to_below"  # a void over the floor below
    # Added in format 0.11: the open roof of a building, its deck (a roof level's open
    # ground, behind its parapets), open to the sky
    ROOF = "roof"
    # Added in format 0.12: a location's land (its layout), outdoors, each an area of it
    DRIVEWAY = "driveway"  # where cars drive in from a gate
    GARDEN = "garden"  # a garden, lawn, planting
    COURTYARD = "courtyard"  # an open space closed round by buildings or walls
    YARD = "yard"  # the plot's open ground round its buildings: none of the others
    PATH = "path"  # a footpath, a walkway
    GATE_AREA = "gate_area"  # the ground just inside a gate, where people and cars come in
    ROAD = "road"  # a street or road, outside the plot's boundary
    POOL = "pool"  # a swimming pool


class FloorType(StrEnum):
    """What level of its building a floor is (format 0.11)."""

    FLOOR = "floor"  # a storey: ground floor, first floor, a basement, a top floor
    MEZZANINE = "mezzanine"  # a floor standing in another floor of its building, over part of it
    ROOF = "roof"  # the building's roof as a level: open air, the rooms standing on it indoor
    # Studio's own: a location's layout, its land (format 0.12), kept as a level of the
    # location's land: never a floor of a building, nor a floor's type in a package
    LAYOUT = "layout"


# the levels of a building, as packages type floors (FloorType but the layout)
BUILDING_LEVELS = (FloorType.FLOOR, FloorType.MEZZANINE, FloorType.ROOF)


class OpeningType(StrEnum):
    DOOR = "door"
    OPENING = "opening"  # a way through with no door: a doorway, or where open-plan rooms meet
    WINDOW = "window"  # not a way through: glazing in a wall
    GATE = "gate"  # format 0.12: a way through a location's boundary walls (its layout)


# The types only land has (format 0.12): a floor's rooms are never read as these
LAND_ONLY = frozenset({SpaceType.DRIVEWAY, SpaceType.GARDEN, SpaceType.COURTYARD, SpaceType.YARD, SpaceType.PATH,
                       SpaceType.GATE_AREA, SpaceType.ROAD, SpaceType.POOL})

# The areas of a location's land (format 0.12), as Review offers them on a layout: those
# there are outdoors first. (Any space type may be given; these are what land is.)
LAND_TYPES = (SpaceType.YARD, SpaceType.PARKING, SpaceType.DRIVEWAY, SpaceType.GARDEN, SpaceType.COURTYARD,
              SpaceType.PATH, SpaceType.GATE_AREA, SpaceType.ROAD, SpaceType.POOL, SpaceType.TERRACE,
              SpaceType.OPEN_AREA, SpaceType.UNSPECIFIED)


# Spaces that connect floors vertically. Their per-floor objects share one
# object code within a building (see ids.py).
VERTICAL_TYPES = frozenset({SpaceType.ELEVATOR, SpaceType.STAIRS, SpaceType.ESCALATOR})
