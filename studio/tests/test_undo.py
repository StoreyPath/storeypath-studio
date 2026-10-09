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


def test_an_undo_needs_the_floor_and_edit_on_it(team):
    s, t = office(team, team.hq0), office(team, team.hq0, 1)
    assert team("khalid", "POST", f"objects/{s.id}", {"ignored": True})[0] == 200
    assert team("khalid", "POST", f"floors/{team.hq0}/release", {})[0] == 200
    assert team("sara", "POST", f"objects/{t.id}", {"ignored": True})[0] == 200  # sara is editing it now
    status, refused = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 423 and refused["locked"]["who"]["name"] == "Sara Ahmed"
    assert override(team, s.id) == {"ignored": True}
    assert team("vera", "POST", "undo", {"floor": team.hq0})[0] == 403  # view only
    assert team("sara", "POST", f"floors/{team.hq0}/release", {})[0] == 200
    assert team("khalid", "POST", "undo", {"floor": team.hq0})[0] == 200 and override(team, s.id) is None


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
