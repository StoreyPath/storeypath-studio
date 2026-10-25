"""Courtyards and light wells: areas inside a building set aside as no room that are open
to the sky (extract.find_courtyards), each a hole in its floor's outline where the floor is
used (export.floor_outline): the package's floor and building, its area, the walking
network (never across one; a door onto one leads outside). A person's word on what an
area set aside is wins, through every re-read. On made-up floors: a ring of offices round
a courtyard, with a light well, a shaft and a gap between double walls (samples.py)."""

import json
import zipfile

import pytest
from shapely.geometry import Point, Polygon, box, shape

import storeypath.vision as vision
from storeypath.convert import convert_floor
from storeypath.export import courtyards, export_package, floor_outline
from storeypath.extract import (COURTYARD_WIDTH_M, ExtractedDoor, ExtractedSpace, FloorExtraction, find_courtyards,
                                keep_to_the_building)
from storeypath.navigation import build_network, route
from storeypath.review import Review
from storeypath.samples import COURTYARD, GAP, LIGHT_WELL, ORIGIN, SHAFT, courtyard_floor, write_floor_dxf
from storeypath.validate import validate_package
from storeypath.types import SpaceType
from storeypath.vision import NOT_A_ROOM, VisionModel
from storeypath.workspace import Override, Placement, SourceDrawing, Workspace

AREAS = {"courtyard": COURTYARD, "light well": LIGHT_WELL, "shaft": SHAFT, "gap": GAP}


def _at(area) -> Polygon:
    x0, y0, x1, y1 = area
    return box(ORIGIN[0] + x0, ORIGIN[1] + y0, ORIGIN[0] + x1, ORIGIN[1] + y1)


class SeesNoRoomThere(VisionModel):
    """Vision, made up: the four areas with no name are no room; the rest one room each."""

    def __init__(self):
        super().__init__(url="http://fake")
        self.model = "fake-vision"

    def available(self):
        return True

    def ask(self, image, question, fields):
        p = Point(json.loads(image)["room"])
        if any(_at(a).contains(p) for a in AREAS.values()):
            return {"outline": NOT_A_ROOM, "type": "not a room"}
        return {"outline": "exactly one room", "type": "office"}


def as_image(sheet, bbox, highlight, px=0, marks=(), letters=()):
    """The crop of a room, as the middle of what is outlined in it (in metres): no rendering
    (FloorPrint.view's stand-in)."""
    c = highlight[0].centroid
    return json.dumps({"room": [c.x / 1000, c.y / 1000]}).encode()


@pytest.fixture(autouse=True)
def area_as_image(monkeypatch):
    monkeypatch.setattr(vision.FloorPrint, "view", as_image)


def ring_building(directory):
    """A building of two floors, its ground floor and the first, each a ring of offices
    round a courtyard (samples.courtyard_floor) drawn in ``directory``, read with vision
    (SeesNoRoomThere; FloorPrint.view as as_image): (workspace, the floors' IDs, the
    building's)."""
    ws = Workspace.new("Courtyards")
    loc = ws.add_location("SITE", "Site")
    b_id = ws.add_building(loc, "RING", "Ring")
    ws.building(b_id).placement = Placement(lon=10.0, lat=50.0, x=125, y=48, bearing=0)
    floors = []
    for ordinal in (0, 1):
        name = f"level-{ordinal}.dxf"
        write_floor_dxf(directory / name, courtyard_floor(ordinal), area_outlines=False)
        floors.append(ws.add_floor(b_id, ordinal, source=SourceDrawing(path=name)))
    for f_id in floors:
        convert_floor(ws, f_id, directory, vision=SeesNoRoomThere())
    return ws, floors, b_id


@pytest.fixture
def ring(tmp_path):
    """ring_building: (workspace, its folder, the floors' IDs, the building's)."""
    ws, floors, b_id = ring_building(tmp_path)
    return ws, tmp_path, floors, b_id


def _area(ws, f_id, which):
    """The space of a floor that is one of AREAS."""
    at = _at(AREAS[which])
    return next(r for r in ws.floor_objects(f_id) if r.kind == "space" and shape(r.geometry).intersection(at).area
                > 0.8 * shape(r.geometry).area)


def _office(ws, f_id, number):
    return next(r for r in ws.floor_objects(f_id) if r.kind == "space" and r.number == number)


def _holes(geom) -> list[Polygon]:
    return [Polygon(h) for p in getattr(geom, "geoms", [geom]) for h in p.interiors]


def test_a_courtyard_and_a_light_well_are_open_to_the_sky_a_shaft_and_a_gap_are_not(ring):
    ws, _, floors, _ = ring
    for f_id in floors:  # the ground floor too: a courtyard at the ground is outside
        yard, well, shaft, gap = (_area(ws, f_id, k) for k in AREAS)
        assert all(r.detected_ignored for r in (yard, well, shaft, gap))  # vision: no room, all four
        assert yard.detected_sky and well.detected_sky and not shaft.detected_sky and not gap.detected_sky
        assert {r.id for r in courtyards(ws, f_id)} == {yard.id, well.id}
        rooms = [r for r in ws.floor_objects(f_id) if r.kind == "space" and not r.detected_ignored]
        assert len(rooms) == 14 and not any(r.detected_sky or ws.effective(r)["sky"] for r in rooms)

        read = shape(ws.floor(f_id).outline)  # as read: whole, as before
        assert _holes(read) == []
        used = floor_outline(ws, f_id)
        holes = _holes(used)
        assert len(holes) == 2 and all(h.covers(Point(p)) for h, p in zip(
            sorted(holes, key=lambda h: -h.area), (_at(COURTYARD).centroid, _at(LIGHT_WELL).centroid)))
        assert used.area == pytest.approx(read.area - shape(yard.geometry).area - shape(well.geometry).area, abs=0.01)
        # rooms are never cut: every room is within the floor as used
        assert all(used.buffer(1e-6).covers(shape(r.geometry)) for r in rooms)
        assert used.covers(shape(shaft.geometry)) and used.covers(shape(gap.geometry))  # part of the floor


def test_the_rule_needs_windows_of_rooms_room_enough_and_the_inside(tmp_path):
    # by the rule alone, on areas set aside as made up: windows of rooms onto it, a circle
    # COURTYARD_WIDTH_M across in it, more than the outside's reach from the outline's edge
    def space(b, ignored=False, **kw):
        return ExtractedSpace(box(*b), "walls", ignored=ignored, **kw)

    rooms = [space((0, 0, 30, 6)), space((0, 24, 30, 30)), space((0, 6, 6, 24)), space((24, 6, 30, 24))]
    yard = space((6.2, 6.2, 23.8, 23.8), ignored=True)  # windows of two rooms onto it
    blind = space((6.2, 6.2, 23.8, 23.8), ignored=True)  # the same, no window: a shaft
    narrow = space((30.2, 0, 32.2, 30), ignored=True)  # 2 m across, a window onto it
    typed = space((6.2, 6.2, 23.8, 23.8), ignored=True, type=SpaceType.STORAGE)

    def window(a, b, at):
        return ExtractedDoor(footprint=Point(at).buffer(0.2), point=Point(at), connects=[a, b], source="window")

    for area, windows, sky in ((yard, True, True), (blind, False, False), (typed, True, False)):
        ex = FloorExtraction([*rooms, area], [window(0, 4, (15, 6.1)), window(2, 4, (6.1, 15))] if windows else [],
                             box(0, 0, 30, 30), 1.0)
        assert find_courtyards(ex) == int(sky) and area.sky is sky and not any(r.sky for r in rooms)
    ex = FloorExtraction([*rooms, narrow], [window(3, 4, (30.1, 15))], box(0, 0, 32.4, 30), 1.0)
    assert COURTYARD_WIDTH_M > 2 and find_courtyards(ex) == 0  # a gap: too narrow, and at the edge besides
    # a yard set aside at the plan's edge is the outside round the building, never a courtyard
    out = space((-10, -10, 40, 40), ignored=True)
    out.polygon = out.polygon.difference(box(-0.2, -0.2, 30.2, 30.2))
    ex = FloorExtraction([*rooms, yard, out], [window(0, 4, (15, 6.1)), window(0, 5, (15, -0.1))],
                         box(-10, -10, 40, 40), 1.0)
    keep_to_the_building(ex)
    assert find_courtyards(ex) == 1 and yard.sky and not out.sky


def test_the_package_has_its_floors_open_where_the_courtyards_are(ring):
    ws, d, floors, b_id = ring
    pkg = d / "ring.storeypath"
    export_package(ws, pkg, bake=False)
    assert validate_package(pkg) == []
    with zipfile.ZipFile(pkg) as z:
        features = {name: json.loads(z.read(f"{name}.geojson"))["features"]
                    for name in ("floors", "spaces", "openings", "buildings")}
    for f in features["floors"]:
        assert f["geometry"]["type"] == "Polygon" and len(f["geometry"]["coordinates"]) == 3  # its shell, two holes
    building = features["buildings"][0]["geometry"]
    assert len(building["coordinates"]) == 3  # the footprint too (every floor open there)
    spaces = {s["id"]: s["properties"] for s in features["spaces"]}
    for f_id in floors:
        yard, well, shaft = (_area(ws, f_id, k) for k in ("courtyard", "light well", "shaft"))
        assert spaces[yard.id]["outdoor"] and spaces[yard.id]["ignored"] and spaces[well.id]["outdoor"]
        assert not spaces[shaft.id]["outdoor"]
        onto = [o["properties"] for o in features["openings"] if yard.id in o["properties"]["connects"]]
        doors = [o for o in onto if o["type"] == "door"]
        assert len(doors) == 2 and all(o["exterior"] for o in doors)  # doors onto it lead outside
        assert sum(o["type"] == "window" for o in onto) >= 8


def test_the_walking_network_goes_round_a_courtyard_not_across_it(ring):
    ws, _, floors, b_id = ring
    f_id = floors[0]
    yard = _area(ws, f_id, "courtyard")
    nav = build_network(ws, b_id)
    assert not any(n.get("space_id") == yard.id for n in nav["nodes"])
    assert not any(e["space_id"] == yard.id for e in nav["edges"])
    onto = {o.id for o in ws.floor_objects(f_id) if o.kind == "opening" and yard.id in o.connects and o.type == "door"}
    assert {n["opening_id"] for n in nav["nodes"] if n["kind"] == "entrance"} >= onto  # ways out into it
    south, north = _office(ws, f_id, "002"), _office(ws, f_id, "010")
    round_ = route(nav, south.id, north.id)
    assert not any(f"door:{o}" in round_["nodes"] for o in onto) and round_["metres"] > 40

    # a person says the courtyard is a room after all: walked across, the shorter way
    ws.overrides[yard.id] = Override(ignored=False)
    across = route(build_network(ws, b_id), south.id, north.id)
    assert any(f"door:{o}" in across["nodes"] for o in onto) and across["metres"] < round_["metres"]
    assert yard.id not in {r.id for r in courtyards(ws, f_id)} and _holes(floor_outline(ws, f_id)) != []


def test_what_a_person_says_an_area_is_wins_and_holds_through_a_re_read(ring):
    ws, d, floors, _ = ring
    f_id = floors[1]
    path = d / "ring.spproj"
    ws.save(path)
    r = Review(path)
    yard, well, shaft, gap = (_area(ws, f_id, k) for k in AREAS)

    def floor():
        return {s["id"]: s for s in r.floor(f_id)["spaces"]}

    shown = floor()
    assert shown[yard.id]["sky"] and shown[yard.id]["sky_read"] and shown[yard.id]["sky_said"] is None
    assert not shown[shaft.id]["sky"] and not shown[shaft.id]["sky_read"]

    room = r.correct(yard.id, {"ignored": False})  # a room after all
    assert (room["ignored"], room["sky"], room["sky_read"]) == (False, False, True)
    aside = r.correct(well.id, {"ignored": True, "sky": False})  # set aside, part of the floor
    assert (aside["ignored"], aside["sky"], aside["sky_said"]) == (True, False, False)
    opened = r.correct(shaft.id, {"sky": True})  # open to the sky, as a person says: over the rule
    assert (opened["ignored"], opened["sky"], opened["sky_said"]) == (True, True, True)
    with pytest.raises(ValueError):
        r.correct(gap.id, {"sky": True, "ignored": False})  # open to the sky is no room

    def holes():
        w = Workspace.load(path)
        return sorted(round(h.area, 1) for h in _holes(floor_outline(w, f_id)))

    expected = [round(shape(shaft.geometry).area, 1)]
    assert holes() == expected
    w = Workspace.load(path)  # read again, as a revised drawing is: the people's word kept
    convert_floor(w, f_id, d, vision=SeesNoRoomThere())
    w.save(path)
    assert holes() == expected
    again = floor()
    assert (again[yard.id]["ignored"], again[yard.id]["sky"]) == (False, False)
    assert (again[well.id]["ignored"], again[well.id]["sky"]) == (True, False)
    assert (again[shaft.id]["ignored"], again[shaft.id]["sky"]) == (True, True)

    back = r.correct(well.id, {"sky": None})  # as read: open to the sky again
    assert back["sky"] and back["sky_said"] is None
    assert len(holes()) == 2


def test_without_vision_a_courtyard_a_person_deletes_is_open_to_the_sky(tmp_path):
    # read by the rules alone, nothing is set aside: the courtyard is a room to review; deleted
    # by a person, the rule makes it open to the sky, and its floor open there
    ws = Workspace.new("Courtyards")
    b_id = ws.add_building(ws.add_location("SITE", "Site"), "RING", "Ring")
    write_floor_dxf(tmp_path / "level-0.dxf", courtyard_floor(0), area_outlines=False)
    f_id = ws.add_floor(b_id, 0, source=SourceDrawing(path="level-0.dxf"))
    convert_floor(ws, f_id, tmp_path)
    yard = _area(ws, f_id, "courtyard")
    assert yard.detected_sky and not yard.detected_ignored and not ws.effective(yard)["sky"]
    assert _holes(floor_outline(ws, f_id)) == []
    ws.overrides[yard.id] = Override(ignored=True)
    assert ws.effective(yard)["sky"] and len(_holes(floor_outline(ws, f_id))) == 1


def test_a_package_opens_with_its_courtyards_as_they_were(ring, tmp_path):
    # A project rebuilt from its package (bundle.py): each courtyard and light well (set
    # aside, outdoors) is open to the sky again, its floor whole as read round it, so that a
    # person may still say it is a room; exported again, nothing changed.
    from storeypath.bundle import open_file

    ws, d, floors, b_id = ring
    pkg = d / "ring.storeypath"
    export_package(ws, pkg, bake=False)
    opened = open_file(tmp_path / "opened", pkg)
    w = Workspace.load(tmp_path / "opened" / opened["code"] / f"{opened['code']}.spproj")
    for f_id in floors:
        assert {r.id for r in courtyards(w, f_id)} == {_area(ws, f_id, k).id for k in ("courtyard", "light well")}
        assert _holes(shape(w.floor(f_id).outline)) == [] and len(_holes(floor_outline(w, f_id))) == 2
    again = tmp_path / "again.storeypath"
    export_package(w, again, bake=False)
    with zipfile.ZipFile(again) as z:
        changes = json.loads(z.read("changes.json"))
    assert (changes["added"], changes["changed"], changes["retired"]) == ([], [], [])
    yard = _area(w, floors[0], "courtyard")
    w.overrides[yard.id] = Override(ignored=False)  # a room after all
    assert len(_holes(floor_outline(w, floors[0]))) == 1
