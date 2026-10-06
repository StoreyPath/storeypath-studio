"""Looking at the rooms with a vision model (vision.py), with a stand-in for the model."""

import json

import ezdxf
import pytest
from shapely.geometry import LineString, Point, Polygon, box, shape

import storeypath.vision as vision
from storeypath.convert import convert_floor
from storeypath.vision import MERGED, NOT_A_ROOM, VisionModel, candidate_cuts
from storeypath.workspace import Override


class FakeVision(VisionModel):
    """Sees what it is told about each room, by where the room is (the crop it is
    shown carries the room's middle, in meters: see the ``render`` stand-in)."""

    def __init__(self, seen):
        super().__init__(url="http://fake")
        self.model, self.seen, self.asked = "fake-vision", seen, 0

    def available(self):
        return True

    def ask(self, image, question, fields):
        self.asked += 1
        shown = json.loads(image)
        if "a" in fields:  # what is on each side of a line?
            a, b = (self.kind(Point(p)) for p in shown["letters"])
            return {"a": a, "b": b}
        return self.seen(Point(shown["room"]))

    def kind(self, point):
        return "office"


@pytest.fixture(autouse=True)
def area_as_image(monkeypatch):
    # the crop of a room, as the middle of what is outlined in it (the sample is in
    # millimetres), and where the letters are written: no rendering needed
    def middle(sheet, bbox, highlight, px=0, marks=(), letters=()):
        c = highlight[0].centroid
        return json.dumps({"room": [c.x / 1000, c.y / 1000],
                           "letters": [[x / 1000, y / 1000] for _, (x, y) in letters]}).encode()

    monkeypatch.setattr(vision.FloorPrint, "view", middle)


def _at(record):
    return shape(record.geometry)


def _unnamed(ws, f_id):
    return next(r for r in ws.floor_objects(f_id) if r.kind == "space" and r.type == "unspecified")


def test_an_unnamed_room_is_typed_by_what_vision_sees(workspace):
    ws, d, f_id, _, _ = workspace
    convert_floor(ws, f_id, d)
    unnamed = _unnamed(ws, f_id)  # IDs are kept across conversions
    sees = lambda p: {"outline": "exactly one room",  # noqa: E731
                      "type": "storage" if _at(unnamed).contains(p) else "office"}
    model = FakeVision(sees)
    convert_floor(ws, f_id, d, vision=model)
    room = ws.objects[unnamed.id]
    assert (room.type, room.type_source) == ("storage", "vision")
    assert any(i.startswith("vision: typed storage") for i in room.issues)
    assert all(r.type != "office" or r.name for r in ws.floor_objects(f_id) if r.kind == "space")  # named rooms keep theirs


def test_an_unnamed_area_that_is_not_a_room_is_set_aside_for_a_person(workspace):
    ws, d, f_id, _, _ = workspace
    convert_floor(ws, f_id, d)
    rid = _unnamed(ws, f_id).id
    room = _at(ws.objects[rid])
    model = FakeVision(lambda p: {"outline": NOT_A_ROOM if room.contains(p) else "exactly one room",
                                  "type": "not a room" if room.contains(p) else "office"})
    convert_floor(ws, f_id, d, vision=model)
    record = ws.objects[rid]
    assert record.detected_ignored and ws.effective(record)["ignored"] and ws.review_reasons(record) == []
    ws.overrides[rid] = Override(ignored=False)  # a person restores it
    assert not ws.effective(record)["ignored"]


def test_what_vision_saw_is_kept_and_not_asked_again(workspace):
    ws, d, f_id, _, _ = workspace
    model = FakeVision(lambda p: {"outline": "exactly one room", "type": "storage"})
    convert_floor(ws, f_id, d, vision=model)
    first = model.asked
    assert first == sum(1 for r in ws.floor_objects(f_id) if r.kind == "space")
    convert_floor(ws, f_id, d, vision=model)
    assert model.asked == first  # same drawing, same rooms: the answers are reused
    convert_floor(ws, f_id, d)  # no model at all: still the storage it was seen as
    assert all(r.type != "unspecified" for r in ws.floor_objects(f_id) if r.kind == "space")


def test_merged_rooms_are_flagged_and_a_named_room_is_never_set_aside(workspace):
    ws, d, f_id, _, _ = workspace
    convert_floor(ws, f_id, d)
    corridor = next(_at(r) for r in ws.floor_objects(f_id) if r.name == "CORRIDOR")
    model = FakeVision(lambda p: {"outline": "two or more rooms merged together" if corridor.contains(p) else NOT_A_ROOM,
                                  "type": "corridor or hall" if corridor.contains(p) else "not a room"})
    convert_floor(ws, f_id, d, vision=model)
    spaces = [r for r in ws.floor_objects(f_id) if r.kind == "space"]
    big = [r for r in spaces if r.name == "CORRIDOR"]
    assert big and all("vision: looks like two or more rooms merged; split it" in r.issues for r in big)
    assert all(not r.detected_ignored for r in spaces if r.name)


def test_a_named_area_that_is_not_a_room_and_wraps_the_house_is_set_aside():
    # a yard named after the landing in it: vision says not a room, and its outline
    # wraps round the rooms; both together, it is the outside
    from shapely.geometry import Polygon, box

    from storeypath.extract import ExtractedSpace
    from storeypath.vision import RoomView, apply_view, _wraps_the_rest

    house = [box(10, 10, 15, 15), box(15, 10, 20, 15), box(10, 15, 20, 20)]
    yard = Polygon([(0, 0), (30, 0), (30, 30), (0, 30), (0, 0)], [[(10, 10), (20, 10), (20, 20), (10, 20), (10, 10)]])
    spaces = [ExtractedSpace(p, "walls", name=n) for p, n in zip(house + [yard], ["A", "B", "C", "LANDING"])]
    assert _wraps_the_rest(3, spaces) and not _wraps_the_rest(0, spaces)
    landing, room = spaces[3], spaces[0]
    apply_view(landing, RoomView(NOT_A_ROOM, "not a room"), wraps=True)
    apply_view(room, RoomView(NOT_A_ROOM, "not a room"), wraps=False)
    assert landing.ignored and not room.ignored
    assert room.issues == ["vision: does not look like a room; check it"]


def test_a_room_may_divide_where_a_line_crosses_it_or_a_wall_stops():
    room = box(0, 0, 10, 4)
    floor_edge = LineString([(6, 0.1), (6, 3.9)])  # a floor finish changing, drawn wall to wall
    counter = LineString([(0, 3.4), (10, 3.4)])  # a counter's front edge: too close to the wall
    loose = LineString([(2, 1), (3, 2)])  # furniture in the middle
    cuts = candidate_cuts(room, [floor_edge, counter, loose])
    assert len(cuts) == 1 and cuts[0].equals_exact(LineString([(6, 0), (6, 4)]), 1e-6)
    ell = Polygon([(0, 0), (8, 0), (8, 3), (3, 3), (3, 9), (0, 9)])  # a sitting room and a corridor
    cuts = candidate_cuts(ell, [])
    assert {tuple(map(tuple, c.coords)) for c in cuts} >= {((3, 3), (3, 0)), ((3, 3), (0, 3))}


def test_treads_hatching_and_slanted_lines_are_not_where_rooms_meet():
    room = box(0, 0, 12, 4)
    treads = [LineString([(x, 0.05), (x, 3.95)]) for x in (1.0, 1.3, 1.6, 1.9, 2.2)]  # a stair
    slant = LineString([(5, 0.05), (7, 3.95)])  # a hatch line
    assert candidate_cuts(room, treads + [slant]) == []
    assert len(candidate_cuts(room, treads[:1] + [slant])) == 1  # one line alone may be where rooms meet


def _with_line(path, a, b):
    doc = ezdxf.readfile(path)
    doc.layers.add("A-FLOR-PATT")
    doc.modelspace().add_line(a, b, dxfattribs={"layer": "A-FLOR-PATT"})
    doc.saveas(path)


def test_a_merged_room_is_divided_where_vision_says_two_rooms_meet(workspace):
    ws, d, f_id, _, _ = workspace
    _with_line(d / "level-2.dxf", (145000, 56500), (145000, 59500))  # across the corridor, 20 m along the floor
    convert_floor(ws, f_id, d)
    corridor = next(r for r in ws.floor_objects(f_id) if r.name == "CORRIDOR")
    whole = _at(corridor)

    class Divides(FakeVision):  # a hall to the west of the line, a lobby to the east
        def kind(self, point):
            return "corridor or hall" if point.x < 145 else "lobby or entrance"

    def seen(p):  # the whole: two rooms merged; on their own, a hall and a lobby
        if not whole.contains(p):
            return {"outline": "exactly one room", "type": "office"}
        if abs(p.x - whole.centroid.x) < 0.5:
            return {"outline": MERGED, "type": "corridor or hall"}
        return {"outline": "exactly one room", "type": "corridor or hall" if p.x < 145 else "lobby or entrance"}

    model = Divides(seen)
    convert_floor(ws, f_id, d, vision=model)
    spaces = [r for r in ws.floor_objects(f_id) if r.kind == "space"]
    pieces = sorted((r for r in spaces if whole.contains(_at(r).representative_point())), key=lambda r: _at(r).bounds)
    assert [round(_at(r).bounds[0]) for r in pieces] == [133, 145]
    assert [r.name for r in pieces] == [None, "CORRIDOR"]  # the label is in the eastern part
    assert pieces[1].id == corridor.id  # the bigger part keeps the room's ID
    assert all("vision: divided where two rooms meet; check the dividing line" in r.issues for r in pieces)
    assert pieces[0].type == "corridor" and pieces[0].type_source == "vision"  # looked at again, on its own
    openings = [r for r in ws.floor_objects(f_id) if r.kind == "opening"]
    assert any(set(r.connects) == {p.id for p in pieces} for r in openings)
    office = next(r for r in spaces if r.name == "OFFICE" and round(_at(r).bounds[0]) == 137)  # its door is in the west part
    assert any(set(r.connects) == {office.id, pieces[0].id} for r in openings)
    asked = model.asked
    convert_floor(ws, f_id, d, vision=model)
    assert model.asked == asked  # the same lines are not asked about again
    convert_floor(ws, f_id, d)  # nor needed: what was seen is kept
    assert sorted(round(_at(r).bounds[0]) for r in ws.floor_objects(f_id)
                  if r.kind == "space" and whole.contains(_at(r).representative_point())) == [133, 145]


def test_what_vision_saw_is_used_when_the_model_does_not_answer(workspace):
    ws, d, f_id, _, _ = workspace
    convert_floor(ws, f_id, d, vision=FakeVision(lambda p: {"outline": "exactly one room", "type": "storage"}))

    class Down(FakeVision):
        def available(self):
            return False

    down = Down(lambda p: {})
    down.model = ""  # its name is only known once it answers
    convert_floor(ws, f_id, d, vision=down)
    assert down.asked == 0 and all(r.type != "unspecified" for r in ws.floor_objects(f_id) if r.kind == "space")


def test_a_cut_is_undone_where_the_pieces_look_like_one_room(workspace):
    ws, d, f_id, _, _ = workspace
    _with_line(d / "level-2.dxf", (145000, 56500), (145000, 59500))
    convert_floor(ws, f_id, d)
    whole = next(_at(r) for r in ws.floor_objects(f_id) if r.name == "CORRIDOR")

    class SidesDiffer(FakeVision):  # says the sides differ, but each piece looks like the same hall
        def kind(self, point):
            return "corridor or hall" if point.x < 145 else "lobby or entrance"

    model = SidesDiffer(lambda p: {"outline": MERGED if abs(p.x - whole.centroid.x) < 0.5 else "exactly one room",
                                   "type": "corridor or hall"} if whole.contains(p) else
                        {"outline": "exactly one room", "type": "office"})
    convert_floor(ws, f_id, d, vision=model)
    inside = [r for r in ws.floor_objects(f_id) if r.kind == "space" and whole.contains(_at(r).representative_point())]
    assert len(inside) == 1 and inside[0].name == "CORRIDOR"
