"""Lifts and stairs drawn in review and linked through the floors they serve
(vertical.py, stacks.py; Review's Stairs and Lift tools): drawn typed, cut out of the
room they are drawn in with a way through, added on other floors linked to the first,
linked and unlinked by hand (and kept so through a reading of the floor), and walked
through by the way from one floor to another."""

import io
import json
import time
import zipfile

import pytest
from shapely.geometry import Point, Polygon, shape

from storeypath import vertical
from storeypath.export import export_package
from storeypath.navigation import build_network, route
from storeypath.samples import build_demo
from storeypath.server import Studio
from storeypath.stacks import NOT_LINKED

# the demo's Headquarters (samples.office_floor, from 125, 48): the reception on the
# ground floor and the open office above it, 161–173 by 59.6–67.9; a lift drawn in their
# north-east corner, where the drawing has none
CORNER = [[170.0, 65.0], [172.5, 65.0], [172.5, 67.5], [170.0, 67.5]]


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


def wait(studio, job):
    for _ in range(600):
        if job.state in ("done", "failed"):
            break
        time.sleep(0.1)
    assert job.state == "done", job.error
    return job.result


@pytest.fixture
def campus(tmp_path):
    data = tmp_path / "data"
    build_demo(data / "demo")
    studio = Studio(data, model=NoModel(), warm=False)
    (code,) = studio.store.codes()
    hq = f"{code}-DEMO-HQ"
    return studio, code, hq, [f"{hq}-F00", f"{hq}-F01", f"{hq}-F02"]


def space(ws, floor, number=None, kind=None):
    return next(r for r in sorted(ws.floor_objects(floor), key=lambda r: r.id) if r.kind == "space"
                and (number is None or ws.effective(r)["number"] == number)
                and (kind is None or ws.effective(r)["type"] == kind))


def lift_at(studio, code, floor, ring=CORNER, kind="elevator"):
    """A lift drawn on a floor with Review's tool: the space found there, typed."""
    result = wait(studio, vertical.add(studio, code, floor, {"type": kind, "space": ring}))
    return result["space"]


def test_a_lift_drawn_in_a_room_is_typed_and_cut_out_of_it_with_a_way_through(campus):
    studio, code, hq, (f0, *_) = campus
    reception = space(studio.workspace(code), f0, "017")
    lift = lift_at(studio, code, f0)
    ws = studio.workspace(code)
    r = ws.objects[lift]
    assert ws.effective(r)["type"] == "elevator" and ws.overrides[lift].type == "elevator"
    assert shape(r.geometry).equals_exact(Polygon(CORNER), 0.01) or shape(r.geometry).symmetric_difference(
        Polygon(CORNER)).area < 0.05
    room = shape(ws.objects[reception.id].geometry)  # the reception, without it
    assert not room.contains(Point(171.25, 66.25)) and room.area < shape(reception.geometry).area - 6
    way = [o for o in ws.floor_objects(f0) if o.kind == "opening" and set(o.connects) == {lift, reception.id}]
    assert len(way) == 1 and way[0].type == "opening"
    edits = ws.floor(f0).edits.spaces
    assert len(edits) == 1  # one drawn space: the floor's edit, kept through every reading
    with pytest.raises(ValueError, match="type is"):
        vertical.add(studio, code, f0, {"type": "shaft", "space": CORNER})


def test_lifts_drawn_on_two_floors_are_linked_where_they_overlap(campus):
    studio, code, hq, (f0, f1, _) = campus
    a = lift_at(studio, code, f0)
    b = lift_at(studio, code, f1, [[x - 0.5, y] for x, y in CORNER])  # half a metre off: 80% over it
    info = vertical.serves(studio.workspace(code), a)
    here = {f["id"]: f["spaces"] for f in info["floors"]}
    assert [m["id"] for m in here[f1]] == [b] and here[f1][0]["how"] == "overlap"
    assert info["stack"] == a and info["setting"] is None  # its key: its space on the lowest floor
    # the demo's own lift is on every floor with the same code
    old = space(studio.workspace(code), f0, kind="elevator")
    floors = vertical.serves(studio.workspace(code), old.id)["floors"]
    assert all(len(f["spaces"]) == 1 for f in floors) and floors[1]["spaces"][0]["how"] == "code"


def test_added_on_other_floors_it_is_typed_and_linked_on_each(campus, tmp_path):
    studio, code, hq, (f0, f1, f2) = campus
    lift = lift_at(studio, code, f0)
    with pytest.raises(ValueError, match="another floor"):
        vertical.copy(studio, code, lift, {"floors": [f0]})
    result = wait(studio, vertical.copy(studio, code, lift, {"floors": [f1, f2]}))
    assert set(result["spaces"]) == {f1, f2} and not result["missed"]
    ws = studio.workspace(code)
    for f, s in result["spaces"].items():
        assert ws.effective(ws.objects[s])["type"] == "elevator" and ws.overrides[s].stack == lift
        assert shape(ws.objects[s].geometry).area == pytest.approx(Polygon(CORNER).area, abs=0.05)
    info = vertical.serves(ws, lift)
    assert [len(f["spaces"]) for f in info["floors"]] == [1, 1, 1]
    assert {m["how"] for f in info["floors"][1:] for m in f["spaces"]} == {"person"}
    # in the package: one stack, the same on the three floors
    buf = io.BytesIO()
    export_package(ws, buf, building=hq, record=False, bake=False)
    with zipfile.ZipFile(buf) as z:
        spaces = json.loads(z.read("spaces.geojson"))["features"]
    stacks = {f["id"]: f["properties"]["stack"] for f in spaces}
    assert stacks[lift] == stacks[result["spaces"][f1]] == stacks[result["spaces"][f2]] == lift


def test_a_persons_link_and_unlink_win_and_are_kept_through_a_reading(campus):
    studio, code, hq, (f0, f1, f2) = campus
    review = studio.review(code)
    far = lift_at(studio, code, f2)  # on the top floor alone, far from the demo's lifts
    old = {f: space(studio.workspace(code), f, kind="elevator").id for f in (f0, f1, f2)}
    review.correct(far, {"stack": old[f0]})  # linked by hand to the ground floor's
    ws = studio.workspace(code)
    assert vertical.serves(ws, far)["stack"] == old[f0]
    assert {m["id"]: m["how"] for f in vertical.serves(ws, far)["floors"] for m in f["spaces"]}[far] == "person"
    wait(studio, studio.convert(code, f2))  # read again: the drawn lift keeps its ID, and its link
    ws = studio.workspace(code)
    assert ws.overrides[far].stack == old[f0] and vertical.serves(ws, far)["stack"] == old[f0]
    review.correct(old[f1], {"stack": NOT_LINKED})  # the first floor's, not linked
    ws = studio.workspace(code)
    alone = vertical.serves(ws, old[f1])
    assert [len(f["spaces"]) for f in alone["floors"]] == [0, 1, 0] and alone["setting"] == NOT_LINKED
    assert ws.effective(ws.objects[old[f1]])["corrected"] is False  # a link alone checks nothing
    kinds = [h["kind"] for h in studio.store.history(code, limit=10)]
    assert kinds[:2] == ["unlink", "link"] or kinds[0] == "unlink"
    review.correct(old[f1], {"stack": None})  # as found again
    assert len(vertical.serves(studio.workspace(code), old[f1])["floors"][0]["spaces"]) == 1
    # a link to what is not a lift or stairs on another floor of its building is refused
    office = space(studio.workspace(code), f0, "001").id
    with pytest.raises(ValueError, match="another floor"):
        review.correct(far, {"stack": old[f2]})
    with pytest.raises(ValueError, match="not a lift"):
        review.correct(far, {"stack": office})
    with pytest.raises(ValueError, match="only a lift"):
        review.correct(office, {"stack": NOT_LINKED})


def test_the_way_between_floors_takes_a_lift_drawn_by_hand(campus):
    studio, code, hq, (f0, f1, f2) = campus
    lift = lift_at(studio, code, f0)
    wait(studio, vertical.copy(studio, code, lift, {"floors": [f1, f2]}))
    ws = studio.workspace(code)
    nav = build_network(ws, hq)
    start, end = space(ws, f0, "009").id, space(ws, f2, "209").id  # offices under and over it, at the east end
    way = route(nav, start, end, accessible=True)
    (change,) = way["changes"]
    assert change["by"] == "lift" and change["from_node"] == f"lift:{lift.rsplit('-', 1)[1]}@{f0}"
    assert change["to_floor_id"] == f2 and way["steps"][2]["text"] == "Take the lift up to Floor 2"
    # the demo's lifts are at the other end: the one drawn by hand is the way
    assert way["metres"] < 70  # (by its own lifts, over 90 m)


def test_a_lift_drawn_short_of_a_wall_opens_onto_the_room_not_onto_the_strip_left():
    # drawn 0.4 m short of the room's north wall: its way through faces the room, not
    # the strip left against the wall (its north edge, as long as the others)
    from shapely.geometry import box

    from storeypath.convert import _drawn_spaces
    from storeypath.extract import ExtractedSpace, FloorExtraction

    ex = FloorExtraction(spaces=[ExtractedSpace(polygon=box(0, 0, 12, 8.3), layer="rooms")], doors=[], outline=None,
                         scale=1.0)
    _drawn_spaces(ex, [[[5, 5.4], [7.5, 5.4], [7.5, 7.9], [5, 7.9]]], 1.0)
    (way,) = [d for d in ex.doors if d.source == "split"]
    assert way.connects == [0, 1]
    (x0, y0), (x1, y1) = way.span.coords
    assert not (y0 == y1 == pytest.approx(7.9)) and way.span.length == pytest.approx(2.5)


# ---- many people at once: one editor a floor (floor locks), and each one's undo ------------

@pytest.fixture
def team(tmp_path, monkeypatch):
    from people import Team
    from storeypath import accounts as acc

    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.setattr("storeypath.review.BUSY_WAIT_S", 0.2)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def drawn_by(team, who, floor, ring=CORNER, kind="elevator"):
    """A lift drawn on a floor by someone, from their page: the space found there."""
    status, job = team(who, "POST", f"floors/{floor}/vertical", {"type": kind, "space": ring})
    assert status == 200, job
    return team.finished(who, job)["result"]["space"]


def hold(team, who, floor):
    """Someone editing a floor: a change of theirs on it (an office renamed) takes its lock."""
    office = next(r for r in team.spaces(floor) if r.name == "OFFICE")
    assert team(who, "POST", f"objects/{office.id}", {"correction": {"name": f"{who}'s"}})[0] == 200


def test_a_lift_is_not_drawn_on_a_floor_someone_else_is_editing(team):
    hold(team, "khalid", team.hq0)
    status, refused = team("sara", "POST", f"floors/{team.hq0}/vertical", {"type": "elevator", "space": CORNER})
    assert status == 423 and refused["locked"]["who"]["username"] == "khalid"
    assert refused["error"].startswith("Khalid Engineer is editing this floor")
    assert team.studio.workspace(team.code).floor(team.hq0).edits.spaces == []  # nothing drawn
    lift = drawn_by(team, "khalid", team.hq0)  # his to draw on
    assert team.studio.workspace(team.code).overrides[lift].type == "elevator"
    # what it serves says who edits the other floors (not oneself)
    hold(team, "sara", f"{team.hq}-F02")
    status, info = team("khalid", "GET", f"objects/{lift}/stack")
    assert status == 200
    locked = {f["id"]: f["locked"] for f in info["floors"]}
    assert locked[f"{team.hq}-F02"]["who"]["name"] == "Sara Ahmed"
    assert locked[team.hq0] is None and locked[team.hq1] is None


def test_added_on_floors_the_one_someone_else_edits_is_left_out_and_named(team):
    lift = drawn_by(team, "khalid", team.hq0)
    f2 = f"{team.hq}-F02"
    hold(team, "sara", f2)
    status, job = team("khalid", "POST", f"objects/{lift}/copy", {"floors": [team.hq1, f2]})
    assert status == 200, job
    result = team.finished("khalid", job)["result"]
    assert list(result["spaces"]) == [team.hq1] and not result["missed"]
    (refused,) = result["refused"]
    assert refused["floor"] == f2 and refused["holder"]["who"]["name"] == "Sara Ahmed"
    assert "Sara Ahmed is editing this floor" in refused["error"]
    ws = team.studio.workspace(team.code)
    assert ws.floor(f2).edits.spaces == [] and ws.overrides[result["spaces"][team.hq1]].stack == lift
    # every floor asked for taken by someone else: refused as a change is (423), nothing drawn
    status, refused = team("khalid", "POST", f"objects/{lift}/copy", {"floors": [f2]})
    assert status == 423 and refused["locked"]["who"]["username"] == "sara"


def test_a_lift_drawn_is_undone_by_who_drew_it_its_type_then_its_shape(team):
    # Drawing a lift is two changes of its author's: the space drawn (an edit of the
    # floor) and its type (a correction). Undo takes them back one at a time, the latest
    # first: the type, then the drawing (the floor read again: the space is gone).
    before = {r.id for r in team.spaces(team.hq0)}
    lift = drawn_by(team, "khalid", team.hq0)
    status, done = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 200 and done["job"] is None, done
    ws = team.studio.workspace(team.code)
    assert ws.effective(ws.objects[lift])["type"] != "elevator"
    status, done = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 200 and done["job"] is not None and done["line"] == "drew a space", done
    team.finished("khalid", done["job"])
    ws = team.studio.workspace(team.code)
    assert ws.floor(team.hq0).edits.spaces == [] and ws.objects[lift].status == "retired"
    assert {r.id for r in team.spaces(team.hq0)} == before
    # sara has nothing of hers to undo there
    assert team("sara", "POST", "undo", {"floor": team.hq0})[0] == 409
