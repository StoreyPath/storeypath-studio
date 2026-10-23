"""Each person undoes and redoes their own changes (Review.step_back, history.py): the
inverse is applied as any change is (the floor's lock, a job's step lock), recorded as
an undo of that change by who undid it; a new change clears their redo; an undo over
what someone changed since is refused, naming them. An item added and undone is
retired (its ID is never given again); a wall drawn and undone is taken away and the
floor read again, its rooms' IDs as reading again gives them. The History panel lists
who did what, in words, undone ones marked, of the floors the person may see."""

import time

import pytest
from shapely.geometry import shape

from people import Team
from storeypath import accounts as acc
from storeypath.workspace import Override


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def override(team, object_id):
    o = team.studio.workspace(team.code).overrides.get(object_id)
    return o.model_dump(exclude_none=True) if o is not None else None


def office(team, floor, n=0):
    return sorted((r for r in team.spaces(floor) if r.name == "OFFICE"), key=lambda r: r.number)[n]


def test_every_correction_is_undone_and_redone(team):
    s = office(team, team.hq0)
    steps = [{"correction": {"name": "Board room", "type": "meeting_room"}}, {"capacity": 12}, {"hidden": True},
             {"hidden": False}, {"ignored": True}, {"ignored": False}, {"reset": True}]
    states = [override(team, s.id)]
    for body in steps:
        assert team("khalid", "POST", f"objects/{s.id}", body)[0] == 200
        states.append(override(team, s.id))
    assert states[1] == {"name": "Board room", "type": "meeting_room"} and states[2]["capacity"] == 12
    lines = []
    for want in reversed(states[:-1]):  # each undone, the latest first
        status, done = team("khalid", "POST", "undo", {"floor": team.hq0})
        assert status == 200 and done["job"] is None, done
        lines.append(done["line"])
        assert override(team, s.id) == want
    label = f"{s.name} {s.number}"
    assert lines == [f"took the corrections off Board room {s.number}", f"restored Board room {s.number}",
                     f"deleted Board room {s.number}", f"showed Board room {s.number} again",
                     f"hid Board room {s.number}", f"set Board room {s.number} to seat 12",
                     f"corrected {label}: meeting room, Board room {s.number}"]
    status, nothing = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 409 and nothing["error"] == "nothing of yours to undo on this floor"
    for want in states[1:]:  # and redone, in order
        status, done = team("khalid", "POST", "redo", {"floor": team.hq0})
        assert status == 200
        assert override(team, s.id) == want
    assert team("khalid", "POST", "redo", {})[0] == 409
    rows = team.studio.store.history(team.code, team.hq0, limit=30)
    assert [r.get("undoes") is not None for r in rows[7:14]] == [True] * 7  # recorded, by who undid them
    assert all(r["who"]["name"] == "Khalid Engineer" for r in rows[:14])
    # the first redone is the last undoing; the last redone, the first
    assert rows[6]["redoes"] == rows[7]["seq"] and rows[0]["redoes"] == rows[13]["seq"]


def test_an_item_added_and_undone_is_retired_and_its_id_never_given_again(team):
    status, a = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "DESK-MANAGER", "x": 12, "y": 4})
    assert status == 200
    status, done = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 200 and done["line"] == f"placed Manager's desk {a['id']}"
    ws = team.studio.workspace(team.code)
    assert ws.items[a["id"]].status == "retired" and ws.items[a["id"]].retired_at is not None
    _, b = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "DESK-JUNIOR", "x": 14, "y": 4})
    assert b["id"] != a["id"] and a["id"] in team.studio.workspace(team.code).items  # (kept: never drawn again)
    assert team("khalid", "POST", "redo", {})[0] == 409  # a new change: nothing to redo
    # moved, turned, given another type, carried, deleted: each undone back
    item = b["id"]
    for body in ({"x": 15.5, "y": 5}, {"rotation": 90}, {"type": "DESK-SENIOR"}, {"floor_id": team.hq1},
                 {"retired": True}):
        before = team.studio.workspace(team.code).items[item].model_dump(exclude={"retired_at"})
        assert team("khalid", "POST", f"items/{item}", body)[0] == 200
        status, done = team("khalid", "POST", "undo", {})
        assert status == 200, done
        assert team.studio.workspace(team.code).items[item].model_dump(exclude={"retired_at"}) == before
    assert done["line"] == f"deleted Junior staff desk {item}"
    # redo: the item placed again (the same one: never a new number)
    assert team("khalid", "POST", "redo", {})[0] == 200
    assert team.studio.workspace(team.code).items[item].status == "retired"


def test_a_wall_drawn_and_undone_is_taken_away_and_the_floor_read_again(team):
    floor = team.an0  # the Annex: rooms found from its walls
    room = max(team.spaces(floor), key=lambda r: shape(r.geometry).area if r.name == "MEETING ROOM" else 0)
    x0, y0, x1, y1 = shape(room.geometry).bounds
    mid = x0 + (x1 - x0) * 2 / 3  # a third of it parted off
    before = {r.id for r in team.spaces(floor) if r.status == "active"}
    status, job = team("bob", "POST", f"floors/{floor}/edits", {"add": {"wall": [[mid, y0 - 0.1], [mid, y1 + 0.1]]}})
    assert status == 200
    team.finished("bob", job)
    halves = {r.id for r in team.spaces(floor) if r.status == "active"
              and shape(r.geometry).intersection(shape(room.geometry)).area > 1}
    assert len(halves) == 2 and room.id in halves  # parted: the larger part keeps its ID
    new = (halves - {room.id}).pop()
    status, done = team("bob", "POST", "undo", {"floor": floor})
    assert status == 200 and done["line"] == "drew a wall" and done["job"]["state"] in ("waiting", "running", "done")
    team.finished("bob", done["job"])
    ws = team.studio.workspace(team.code)
    assert ws.floor(floor).edits.walls == []
    assert {r.id for r in team.spaces(floor) if r.status == "active"} == before  # one room again, its own ID
    assert ws.objects[new].status == "retired"  # the other half's ID: retired, never given again
    status, done = team("bob", "POST", "redo", {"floor": floor})
    assert status == 200 and done["job"] is not None
    team.finished("bob", done["job"])
    assert len(team.studio.workspace(team.code).floor(floor).edits.walls) == 1
    again = {r.id for r in team.spaces(floor) if r.status == "active"
             and shape(r.geometry).intersection(shape(room.geometry)).area > 1}
    assert len(again) == 2 and room.id in again
    rows = team.studio.store.history(team.code, floor, limit=10)
    edits = [r for r in rows if r["part"] == "edit"]
    assert [(r["kind"], bool(r.get("undoes")), bool(r.get("redoes"))) for r in edits] == \
        [("draw", False, True), ("draw", True, False), ("draw", False, False)]
    assert edits[1]["before"] == edits[2]["after"] and edits[1]["after"] == []


def test_a_door_moved_along_its_wall_and_undone_keeps_its_id(team):
    # Its record goes with it, and back with the undo, at once (no read: openings.py), as the
    # next read finds it by where it is: its ID kept however far it went (an opening is
    # matched by position, 0.5 m).
    import math

    floor = team.an0
    door = max((r for r in team.studio.workspace(team.code).floor_objects(floor)
                if r.kind == "opening" and r.type == "door" and r.span and r.status == "active"),
               key=lambda r: math.dist(r.span[0], r.span[-1]))
    (x0, y0), (x1, y1) = door.span[0], door.span[-1]
    n = math.dist((x0, y0), (x1, y1))
    m = [(x0 + x1) / 2, (y0 + y1) / 2]
    status, job = team("bob", "POST", f"floors/{floor}/edits",
                       {"move": {"at": m, "to": [m[0] + (x1 - x0) / n * 0.6, m[1] + (y1 - y0) / n * 0.6]}})
    if status != 200:  # (its wall too short for 0.6 m: a little)
        status, job = team("bob", "POST", f"floors/{floor}/edits",
                           {"move": {"at": m, "to": [m[0] + (x1 - x0) / n * 0.1, m[1] + (y1 - y0) / n * 0.1]}})
    assert status == 200 and job["at_once"] and job["floor"] == floor, job
    now = team.studio.workspace(team.code).objects[door.id]
    assert now.status == "active" and math.dist(now.geometry["coordinates"], door.geometry["coordinates"]) > 0.05
    status, done = team("bob", "POST", "undo", {"floor": floor})
    assert status == 200 and done["line"].startswith("moved") and done["line"].endswith("along its wall")
    assert done["job"] is None  # at once too
    back = team.studio.workspace(team.code).objects[door.id]
    assert back.status == "active" and math.dist(back.geometry["coordinates"], door.geometry["coordinates"]) < 0.01


def test_a_window_moved_twice_and_undone_twice_is_where_the_drawing_has_it(team):
    import math

    floor = team.an0
    w = next(r for r in team.studio.workspace(team.code).floor_objects(floor) if r.kind == "opening"
             and r.type == "window" and r.span and r.status == "active" and math.dist(r.span[0], r.span[-1]) > 0.6)
    (x0, y0), (x1, y1) = w.span[0], w.span[-1]
    n = math.dist((x0, y0), (x1, y1))
    u, m = ((x1 - x0) / n, (y1 - y0) / n), [(x0 + x1) / 2, (y0 + y1) / 2]
    where = lambda: team.studio.workspace(team.code).objects[w.id]  # noqa: E731
    for d in (0.2, 0.5):
        r = where()
        at = [(r.span[0][0] + r.span[-1][0]) / 2, (r.span[0][1] + r.span[-1][1]) / 2]
        status, job = team("bob", "POST", f"floors/{floor}/edits", {"move": {"at": at, "to": [m[0] + u[0] * d, m[1] + u[1] * d]}})
        assert status == 200 and job["at_once"], job
    for want in (0.2, 0.0):
        status, done = team("bob", "POST", "undo", {"floor": floor})
        assert status == 200 and done["job"] is None, done
        assert abs(math.dist(where().geometry["coordinates"], w.geometry["coordinates"]) - want) < 0.01, want
    assert team.studio.workspace(team.code).floor(floor).edits.resized == []


def test_each_person_undoes_their_own(team):
    a, b = office(team, team.hq0), office(team, team.hq1)
    assert team("khalid", "POST", f"objects/{a.id}", {"correction": {"name": "Khalid's"}})[0] == 200
    assert team("sara", "POST", f"objects/{b.id}", {"correction": {"name": "Sara's"}})[0] == 200
    status, done = team("khalid", "POST", "undo", {})  # his latest anywhere: not hers, made after it
    assert status == 200 and override(team, a.id) is None and override(team, b.id) == {"name": "Sara's"}
    assert team("sara", "POST", "undo", {})[0] == 200 and override(team, b.id) is None
    # a new change clears what one would redo
    assert team("khalid", "POST", f"objects/{a.id}", {"correction": {"name": "Again"}})[0] == 200
    status, nothing = team("khalid", "POST", "redo", {})
    assert status == 409 and nothing["nothing"] is True
    assert team("sara", "POST", "redo", {})[0] == 200 and override(team, b.id) == {"name": "Sara's"}


def test_an_undo_over_what_another_changed_since_is_refused_naming_them(team):
    s = office(team, team.hq0)
    assert team("khalid", "POST", f"objects/{s.id}", {"correction": {"name": "Khalid's"}})[0] == 200
    assert team("khalid", "POST", f"floors/{team.hq0}/release", {})[0] == 200
    assert team("sara", "POST", f"objects/{s.id}", {"correction": {"name": "Sara's"}})[0] == 200
    assert team("sara", "POST", f"floors/{team.hq0}/release", {})[0] == 200
    status, refused = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 409
    assert refused["error"].startswith(f"{s.name} {s.number} was changed since by Sara Ahmed at ")  # as he saw it
    assert refused["error"].endswith(f"(renamed Khalid's {s.number} to Sara's {s.number}): nothing was undone")
    assert refused["conflict"]["who"] == "Sara Ahmed" and not refused["conflict"]["mine"]
    assert override(team, s.id) == {"name": "Sara's"}


def test_an_undo_needs_the_room_and_edit_on_it(team):
    s, t = office(team, team.hq0), office(team, team.hq0, 1)
    assert team("khalid", "POST", f"objects/{s.id}", {"hidden": True})[0] == 200
    assert team("khalid", "POST", f"floors/{team.hq0}/release", {})[0] == 200
    inside = shape(s.geometry).representative_point()
    assert team("sara", "POST", f"floors/{team.hq0}/items", {"type": "DESK-JUNIOR", "x": inside.x, "y": inside.y})[0] == 200
    assert team("sara", "POST", f"objects/{t.id}", {"hidden": True})[0] == 200  # sara is editing his room now, and another
    status, refused = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 423 and refused["locked"]["who"]["name"] == "Sara Ahmed" and refused["locked"]["room"] == s.id
    assert override(team, s.id) == {"hidden": True}
    assert team("vera", "POST", "undo", {"floor": team.hq0})[0] == 403  # view only
    assert team("sara", "POST", f"floors/{team.hq0}/release", {})[0] == 200
    assert team("khalid", "POST", "undo", {"floor": team.hq0})[0] == 200 and override(team, s.id) is None
    assert override(team, t.id) == {"hidden": True}  # hers stays


def test_the_history_says_who_did_what_and_what_was_undone(team):
    s = office(team, team.hq0)
    assert team("khalid", "POST", f"objects/{s.id}", {"ignored": True})[0] == 200
    _, item = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "DESK-MANAGER", "x": 12, "y": 4})
    assert team("khalid", "POST", f"items/{item['id']}", {"x": 13, "y": 4})[0] == 200
    assert team("khalid", "POST", "undo", {"floor": team.hq0})[0] == 200  # the move
    assert team("sara", "POST", f"objects/{office(team, team.hq1).id}", {"hidden": True})[0] == 200
    status, h = team("vera", "GET", f"history?floor={team.hq0}&n=10")
    assert status == 200
    shown = [(e["who"]["name"], e["line"], e["undone"]) for e in h["entries"]]
    desk = f"Manager's desk {item['id']}"
    assert shown[:4] == [("Khalid Engineer", f"undid: moved {desk}", False), ("Khalid Engineer", f"moved {desk}", True),
                         ("Khalid Engineer", f"placed {desk}", False),
                         ("Khalid Engineer", f"deleted {s.name} {s.number}", False)]
    assert all(team.hq0 in e["floors"] or not e["floors"] for e in h["entries"])
    assert h["undo"] is None and h["redo"] is None  # vera changed nothing
    # vera sees the floors she may see: none of HQ's first floor, nor the Annex
    _, everything = team("vera", "GET", "history?n=50")
    assert everything["entries"] and all(e["floors"] and team.hq0 in e["floors"] for e in everything["entries"])
    assert team("vera", "GET", f"history?floor={team.hq1}")[0] == 404
    # khalid is told what he would undo and redo there
    _, his = team("khalid", "GET", f"history?floor={team.hq0}&n=1")
    assert len(his["entries"]) == 1 and his["undo"]["line"] == f"placed {desk}"
    assert his["redo"]["line"] == f"undid: moved {desk}"
    _, whole = team("sara", "GET", "history?n=3")
    assert whole["entries"][0]["line"] == f"hid {office(team, team.hq1).name} {office(team, team.hq1).number}"
    assert whole["entries"][0]["mine"] is True and whole["undo"]["seq"] == whole["entries"][0]["seq"]


def test_a_set_placed_is_its_pieces_each_an_item_one_group_and_undone_as_one(team):
    """A set placed (catalogue.py default_sets): each piece an item with its own ID, where the
    set puts it turned with it, all one group; moved together, grouped and let go as one
    change each; undone as one: a set placed and undone, every piece retired."""
    import math

    from storeypath.catalogue import default_sets

    junior = next(s for s in default_sets() if s.code == "SET-WORKSTATION-JUNIOR")
    status, placed = team("khalid", "POST", f"floors/{team.hq0}/sets",
                          {"set": "SET-WORKSTATION-JUNIOR", "x": 12, "y": 4, "rotation": 90})
    assert status == 200, placed
    items, group = placed["items"], placed["group"]
    assert [i["type"] for i in items] == [p.type for p in junior.pieces] and len({i["id"] for i in items}) == len(items)
    assert group and all(i["group"] == group for i in items)
    for i, p in zip(items, junior.pieces):  # turned 90°: the set's +x is the plan's +y
        assert math.isclose(i["x"], 12 - p.y, abs_tol=1e-6) and math.isclose(i["y"], 4 + p.x, abs_tol=1e-6)
        assert math.isclose(i["rotation"], (p.rotation + 90) % 360)
    ws = team.studio.workspace(team.code)
    assert all(ws.items[i["id"]].group == group for i in items)
    # one line of history; moved together, then let go of the group, then grouped again
    _, h = team("khalid", "GET", f"history?floor={team.hq0}&n=1")
    assert h["entries"][0]["line"] == f"placed Workstation, junior staff ({len(items)} items)"
    moved = [{"id": i["id"], "x": i["x"] + 1, "y": i["y"]} for i in items]
    status, got = team("khalid", "POST", "items", {"items": moved})
    assert status == 200 and [i["x"] for i in got["items"]] == [m["x"] for m in moved]
    assert team("khalid", "GET", f"history?floor={team.hq0}&n=1")[1]["entries"][0]["line"] == f"moved {len(items)} items"
    ids = [{"id": i["id"]} for i in items]
    assert team("khalid", "POST", "items", {"items": ids, "group": None})[0] == 200
    assert all(team.studio.workspace(team.code).items[i["id"]].group is None for i in items)
    status, got = team("khalid", "POST", "items", {"items": ids[:2], "group": "new"})
    assert status == 200 and got["items"][0]["group"] and got["items"][0]["group"] == got["items"][1]["group"] != group
    assert team("khalid", "POST", "items", {"items": ids[:1], "group": "new"})[0] == 400  # (a group is two or more)
    # each undone back, as one: grouped, let go, moved; then the set placed: every piece retired
    for want in (None, group, group):
        assert team("khalid", "POST", "undo", {"floor": team.hq0})[0] == 200
        assert {team.studio.workspace(team.code).items[i["id"]].group for i in items} == {want}
    ws = team.studio.workspace(team.code)
    assert [ws.items[i["id"]].x for i in items] == [i["x"] for i in items]
    status, done = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 200 and done["line"] == f"placed Workstation, junior staff ({len(items)} items)"
    assert all(team.studio.workspace(team.code).items[i["id"]].status == "retired" for i in items)
    assert team("khalid", "POST", f"floors/{team.hq0}/sets", {"set": "SET-NOTHING", "x": 1, "y": 1})[0] == 400


def test_a_card_reader_serves_the_door_it_is_placed_by_and_a_camera_is_drawn_by_its_view(team, tmp_path):
    """An item of a type that serves a door (a card reader) placed within DOOR_REACH_M of
    one is linked to the nearest; linked to another, or none, as one change undone as one;
    carried to another floor or made a type that serves none, unlinked. A camera's view is
    its own (its fields), else its type's. The package says both, and drops a link to a door
    deleted since."""
    import json
    import zipfile

    from shapely.geometry import LineString, Point

    from storeypath.export import export_package, walked_doors
    from storeypath.review import DOOR_REACH_M
    from storeypath.validate import validate_package

    ws = team.studio.workspace(team.code)
    doors = walked_doors(ws, team.hq0)
    at = lambda r: LineString(r.span) if r.span else Point(r.geometry["coordinates"])  # noqa: E731
    door = min(doors, key=lambda r: r.id)
    middle = at(door).interpolate(0.5, normalized=True) if door.span else at(door)
    near = (middle.x + 0.25, middle.y)
    nearest = min(doors, key=lambda r: (at(r).distance(Point(near)), r.id))
    status, reader = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "CARD-READER", "x": near[0], "y": near[1]})
    assert status == 200 and reader["door_id"] == nearest.id
    # far from every door: none
    far = next((x, y) for x in range(-40, 80, 2) for y in range(-40, 80, 2)
               if min(at(r).distance(Point(x, y)) for r in doors) > DOOR_REACH_M + 1)
    status, alone = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "CARD-READER", "x": far[0], "y": far[1]})
    assert status == 200 and alone["door_id"] is None
    status, linked = team("khalid", "POST", f"items/{alone['id']}", {"door_id": door.id})
    assert status == 200 and linked["door_id"] == door.id
    _, h = team("khalid", "GET", f"history?floor={team.hq0}&n=1")
    assert h["entries"][0]["line"] == f"linked Access card reader {alone['id']} to the door …-{door.id.rsplit('-', 1)[-1]}"
    assert team("khalid", "POST", "undo", {"floor": team.hq0})[0] == 200
    assert team.studio.workspace(team.code).items[alone["id"]].door_id is None
    window = next((r for r in ws.floor_objects(team.hq0) if r.kind == "opening" and r.type == "window"), None)
    for body in ({"door_id": "NO-SUCH-DOOR"}, *([{"door_id": window.id}] if window else []), {"door_id": 7}):
        assert team("khalid", "POST", f"items/{alone['id']}", body)[0] == 400
    status, sofa = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "SOFA", "x": far[0], "y": far[1]})
    status, err = team("khalid", "POST", f"items/{sofa['id']}", {"door_id": door.id})
    assert status == 400 and "serves no door" in err["error"]
    # a camera: its own view where given, else its type's; a view past all round, refused
    assert team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "CCTV-DOME", "x": 3, "y": 3,
                                                                "values": {"view_deg": 400}})[0] == 400
    _, dome = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "CCTV-DOME", "x": 3, "y": 3, "values": {"view_deg": 120}})

    def package(name):
        export_package(team.studio.workspace(team.code), tmp_path / name, bake=False, record=False, building=team.hq)
        assert validate_package(tmp_path / name) == []
        with zipfile.ZipFile(tmp_path / name) as z:
            return {f["id"]: f["properties"] for f in json.loads(z.read("items.geojson"))["features"]}

    items = package("1.storeypath")
    assert items[reader["id"]]["door_id"] == nearest.id and items[alone["id"]]["door_id"] is None
    assert (items[dome["id"]]["view_deg"], items[dome["id"]]["range_m"]) == (120, 12)  # its own angle, its type's range
    assert (items[sofa["id"]]["view_deg"], items[sofa["id"]]["door_id"]) == (None, None)
    # its door deleted: the package links it to none (the link is kept, should it come back)
    status, _ = team("khalid", "POST", f"objects/{nearest.id}", {"ignored": True})
    assert status == 200, _
    assert package("2.storeypath")[reader["id"]]["door_id"] is None
    assert team.studio.workspace(team.code).items[reader["id"]].door_id == nearest.id
    # made a sofa, or carried to another floor: unlinked
    _, made = team("khalid", "POST", f"items/{reader['id']}", {"type": "SOFA"})
    assert made["door_id"] is None
    _, carried = team("khalid", "POST", f"items/{linked['id']}", {"door_id": door.id})
    _, carried = team("khalid", "POST", f"items/{linked['id']}", {"floor_id": team.hq1})
    assert carried["door_id"] is None
