"""Looking at the rooms with a vision model (vision.py), with a stand-in for the model."""

import pytest
from shapely.geometry import Point, shape

import storeypath.vision as vision
from storeypath.convert import convert_floor
from storeypath.vision import NOT_A_ROOM, VisionModel
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
        x, y = (float(v) for v in image.decode().split(","))
        return self.seen(Point(x, y))


@pytest.fixture(autouse=True)
def area_as_image(monkeypatch):
    # the crop of a room, as the middle of what is outlined in it (the sample is in
    # millimetres): no rendering needed
    def middle(sheet, bbox, highlight, px=0):
        c = highlight[0].centroid
        return f"{c.x / 1000},{c.y / 1000}".encode()

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
