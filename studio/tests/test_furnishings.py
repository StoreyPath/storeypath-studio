"""Furniture layouts (Review.save_furnishing, delete_furnishing, place_items): the items
of a room kept as a layout of the project, each from the corner of the room's box; placed
again as items of their own, in one go and one change, undone as one; deleted by whoever
may edit the floor it was taken from or the whole project. And items placed in one go
(a paste) as one group."""

import math

import pytest
from shapely.geometry import shape

from people import Team
from storeypath import accounts as acc


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def office(team, floor, n=0):
    return sorted((r for r in team.spaces(floor) if r.name == "OFFICE"), key=lambda r: r.number)[n]


def furnish(team, room, who="khalid"):
    """Two items placed in a room, near its box's corner: their IDs and where they stand."""
    g = shape(room.geometry)
    x0, y0, x1, y1 = g.bounds
    spots = [(x0 + 0.9, y0 + 0.8, 0), (x0 + 1.6, y0 + 1.7, 90)]
    placed = []
    for x, y, rot in spots:
        assert g.contains(shape({"type": "Point", "coordinates": [x, y]})), "a spot outside the office"
        status, it = team(who, "POST", f"floors/{team.hq0}/items", {"type": "DESK-JUNIOR", "x": x, "y": y, "rotation": rot})
        assert status == 200, it
        placed.append(it)
    return placed, (x0, y0, x1, y1)


def test_a_rooms_items_are_kept_as_a_layout_from_the_corner_of_its_box(team):
    room = office(team, team.hq0)
    placed, (x0, y0, x1, y1) = furnish(team, room)
    elsewhere = office(team, team.hq0, 1)  # an item in another room is not taken
    ex = shape(elsewhere.geometry).representative_point()
    assert team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "SOFA", "x": ex.x, "y": ex.y})[0] == 200

    status, f = team("khalid", "POST", f"floors/{team.hq0}/furnishings", {"room": room.id, "name": "  Junior   office "})
    assert status == 200, f
    assert f["name"] == "Junior office" and f["count"] == 2 and f["floor_id"] == team.hq0 and f["id"].startswith("L")
    assert math.isclose(f["width"], x1 - x0, abs_tol=1e-3) and math.isclose(f["depth"], y1 - y0, abs_tol=1e-3)
    got = sorted((p["x"], p["y"], p["rotation"], p["type"]) for p in f["pieces"])
    want = sorted((round(it["x"] - x0, 4), round(it["y"] - y0, 4), it["rotation"], it["type"]) for it in placed)
    for (gx, gy, gr, gt), (wx, wy, wr, wt) in zip(got, want):
        assert math.isclose(gx, wx, abs_tol=1e-3) and math.isclose(gy, wy, abs_tol=1e-3) and gr == wr and gt == wt
    assert f["created_by"] == "Khalid Engineer" and f["missing"] == []
    # one line of the project's history; listed for anyone who sees the project
    _, h = team("khalid", "GET", "history?n=1")
    assert h["entries"][0]["line"] == "saved the furniture layout Junior office (2 items)"
    for who in ("khalid", "sara", "vera"):
        status, listed = team(who, "GET", "furnishings")
        assert status == 200 and [x["id"] for x in listed] == [f["id"]], who
    # kept in the database: read again whole, it is there
    kept = team.studio.store.load(team.code).furnishings[f["id"]]
    assert kept.name == "Junior office" and len(kept.pieces) == 2


def test_a_layout_is_refused_without_a_name_with_a_name_taken_or_of_an_empty_room(team):
    room = office(team, team.hq0)
    furnish(team, room)
    assert team("khalid", "POST", f"floors/{team.hq0}/furnishings", {"room": room.id, "name": " "})[0] == 400
    assert team("khalid", "POST", f"floors/{team.hq0}/furnishings", {"room": room.id, "name": "x" * 81})[0] == 400
    assert team("khalid", "POST", f"floors/{team.hq0}/furnishings", {"room": room.id, "name": "Office"})[0] == 200
    status, taken = team("sara", "POST", f"floors/{team.hq0}/furnishings", {"room": room.id, "name": "OFFICE"})
    assert status == 409 and "named OFFICE already" in taken["error"]
    empty = office(team, team.hq0, 1)
    status, none = team("khalid", "POST", f"floors/{team.hq0}/furnishings", {"room": empty.id, "name": "Empty"})
    assert status == 400 and "no furniture in this room" in none["error"]
    status, nowhere = team("khalid", "POST", f"floors/{team.hq0}/furnishings", {"room": f"{team.hq0}-9999", "name": "No"})
    assert status == 404 and "no room" in nowhere["error"]
    # view only: not made
    assert team("vera", "POST", f"floors/{team.hq0}/furnishings", {"room": room.id, "name": "Mine"})[0] == 403


def test_items_placed_in_one_go_are_one_group_one_change_undone_as_one(team):
    room = office(team, team.hq0, 1)
    x0, y0, *_ = shape(room.geometry).bounds
    body = {"items": [{"type": "DESK-JUNIOR", "x": x0 + 1, "y": y0 + 1, "rotation": 450},
                      {"type": "SOFA", "x": x0 + 2, "y": y0 + 1}], "label": "Junior office"}
    status, placed = team("khalid", "POST", f"floors/{team.hq0}/pieces", body)
    assert status == 200, placed
    items, group = placed["items"], placed["group"]
    assert [i["type"] for i in items] == ["DESK-JUNIOR", "SOFA"] and len({i["id"] for i in items}) == 2
    assert group and all(i["group"] == group for i in items) and items[0]["rotation"] == 90
    _, h = team("khalid", "GET", f"history?floor={team.hq0}&n=1")
    assert h["entries"][0]["line"] == "placed Junior office (2 items)"
    status, done = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 200 and done["line"] == "placed Junior office (2 items)"
    ws = team.studio.workspace(team.code)
    assert all(ws.items[i["id"]].status == "retired" for i in items)
    # one alone: no group; asked for none: none
    status, one = team("khalid", "POST", f"floors/{team.hq0}/pieces", {"items": body["items"][:1]})
    assert status == 200 and one["group"] is None
    status, two = team("khalid", "POST", f"floors/{team.hq0}/pieces", {"items": body["items"], "group": False})
    assert status == 200 and two["group"] is None
    # refused: no items, an unknown type, a place that is no number, too many
    assert team("khalid", "POST", f"floors/{team.hq0}/pieces", {"items": []})[0] == 400
    assert team("khalid", "POST", f"floors/{team.hq0}/pieces", {"items": [{"type": "NOPE", "x": 1, "y": 1}]})[0] == 400
    assert team("khalid", "POST", f"floors/{team.hq0}/pieces", {"items": [{"type": "SOFA", "x": "far", "y": 1}]})[0] == 400
    many = {"items": [{"type": "SOFA", "x": x0 + 1, "y": y0 + 1}] * 501}
    assert team("khalid", "POST", f"floors/{team.hq0}/pieces", many)[0] == 400
    assert team("vera", "POST", f"floors/{team.hq0}/pieces", body)[0] == 403


def test_a_layout_is_deleted_by_who_edits_its_floor_or_the_whole_project(team):
    room = office(team, team.hq0)
    furnish(team, room)
    status, f = team("khalid", "POST", f"floors/{team.hq0}/furnishings", {"room": room.id, "name": "Office"})
    assert status == 200
    assert team("bob", "POST", f"furnishings/{f['id']}/delete", {})[0] == 403  # (edits the annex alone)
    assert team("vera", "POST", f"furnishings/{f['id']}/delete", {})[0] == 403  # (sees its floor)
    status, gone = team("sara", "POST", f"furnishings/{f['id']}/delete", {})  # (edits the whole project)
    assert status == 200 and gone == {"id": f["id"], "deleted": True}
    assert team("khalid", "GET", "furnishings")[1] == []
    _, h = team("khalid", "GET", "history?n=1")
    assert h["entries"][0]["line"] == "deleted the furniture layout Office"
    status, again = team("sara", "POST", f"furnishings/{f['id']}/delete", {})
    assert status == 404 and "no furniture layout" in again["error"]
    # what was placed of a layout stays when it is deleted
    status, f2 = team("khalid", "POST", f"floors/{team.hq0}/furnishings", {"room": room.id, "name": "Office 2"})
    x0, y0, *_ = shape(office(team, team.hq0, 1).geometry).bounds
    status, placed = team("khalid", "POST", f"floors/{team.hq0}/pieces",
                          {"items": [{"type": p["type"], "x": x0 + p["x"], "y": y0 + p["y"], "rotation": p["rotation"]}
                                     for p in f2["pieces"]], "label": f2["name"]})
    assert status == 200
    assert team("khalid", "POST", f"furnishings/{f2['id']}/delete", {})[0] == 200  # (its maker, who edits its floor)
    ws = team.studio.workspace(team.code)
    assert all(ws.items[i["id"]].status == "active" for i in placed["items"])
