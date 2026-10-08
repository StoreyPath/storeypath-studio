"""What Review and Studio take from a request: finite numbers only; a change made
whole or not at all; what was chosen taken away, nothing else; a capacity that is
not a person's acceptance."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from storeypath.review import NotFound, Review
from storeypath.samples import build_demo
from storeypath.server import Studio
from sessions import admin_server
from storeypath.workspace import Workspace

NOT_NUMBERS = ["NaN", "nan", "Infinity", "-inf", "1e999", None, True, "", "x"]


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture
def demo(tmp_path):
    ws_path, _ = build_demo(tmp_path / "data" / "demo")
    ws = Workspace.load(ws_path)
    return ws_path, ws, f"{ws.id}-DEMO-HQ-F00"


def test_review_takes_finite_numbers_only(demo):
    ws_path, ws, f0 = demo
    r = Review(ws_path)
    before = ws_path.read_bytes()
    for bad in NOT_NUMBERS:
        with pytest.raises(ValueError):
            r.edit(f0, {"add": {"wall": [[0, 0], [bad, 3]]}})
        with pytest.raises(ValueError):
            r.edit(f0, {"add": {"space": [[200, 200], [210, 200], [210, bad], [200, 206]]}})
        with pytest.raises(ValueError):
            r.edit(f0, {"add": {"opening": {"type": "door", "span": [[0, 0], [bad, 0]]}}})
        with pytest.raises(ValueError):
            r.add_item(f0, {"type": "DESK-JUNIOR", "x": bad, "y": 1})
        with pytest.raises(ValueError):
            r.add_item(f0, {"type": "SOFA", "x": 1, "y": 1, "values": {"seats": bad}} if bad not in (None, "") else
                       {"type": "SOFA", "x": 1, "y": 1, "rotation": "NaN"})
    assert ws_path.read_bytes() == before


def test_studio_takes_finite_numbers_only(demo):
    ws_path, ws, _ = demo
    s = Studio(ws_path.parent.parent, model=NoModel(), warm=False)
    hq, site = f"{ws.id}-DEMO-HQ", f"{ws.id}-DEMO"
    before = ws_path.read_bytes()
    for bad in NOT_NUMBERS:
        for body in ({"lat": bad, "lon": 10}, {"lat": 50, "lon": 10, "x": bad}, {"lat": 50, "lon": 10, "bearing": bad}):
            with pytest.raises(ValueError):
                s.place(ws.id, hq, body)
        with pytest.raises(ValueError):
            s.place_site(ws.id, site, {"lat": 50, "lon": bad})
        with pytest.raises(ValueError):
            s.move(ws.id, hq, {"x": bad, "y": 0, "rotation": 0})
    assert ws_path.read_bytes() == before
    placed = s.place(ws.id, hq, {"lat": "50.5", "lon": 10, "bearing": -90})["placement"]
    assert (placed["lat"], placed["bearing"]) == (50.5, 270)


def test_a_plans_numbers_are_checked_before_anything_changes(demo):
    from storeypath.server import _floors_to_add

    ws_path, ws, _ = demo
    plan = {"index": 0, "title": "Plan", "ordinal": 7, "region": [0, 0, 10, 10]}
    assert _floors_to_add(ws, [dict(plan)])[0].ordinal == 7
    assert _floors_to_add(ws, [{**plan, "ordinal": 7.0, "height": "3.2"}])[0].plan["height"] == 3.2
    for bad in ({"region": [0, 0, "nan", 10]}, {"region": [0, 0, 10]}, {"ordinal": "NaN"}, {"ordinal": 1.5},
                {"ordinal": None}, {"ordinal": True}, {"height": "inf"}, {"height": -3}, {"parapet": "x"}):
        with pytest.raises(ValueError):
            _floors_to_add(ws, [{**plan, **bad}])


def test_json_nan_is_refused_by_the_server(demo):
    ws_path, ws, f0 = demo
    s = Studio(ws_path.parent.parent, model=NoModel(), warm=False)
    srv = admin_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        before = ws_path.read_bytes()
        for raw in (b'{"lat": NaN, "lon": 10}', b'{"lat": 50, "lon": 10, "x": Infinity}'):
            req = urllib.request.Request(f"{base}/api/projects/{ws.id}/buildings/{ws.id}-DEMO-HQ/placement", data=raw,
                                         headers={"Content-Type": "application/json", "X-StoreyPath": "1"})
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            assert e.value.code == 400 and json.loads(e.value.read())["error"] == "invalid JSON"
        assert ws_path.read_bytes() == before
    finally:
        srv.shutdown()
        srv.server_close()


def test_an_item_change_refused_changes_nothing(demo):
    """A change refused part way (here for its floor) leaves the item as it was, in
    the file and in the editor's copy that the next save writes."""
    ws_path, ws, f0 = demo
    r = Review(ws_path)
    item = r.add_item(f0, {"type": "COPIER", "x": 10, "y": 10, "values": {"model": "A3"}})
    refused = [
        ({"retired": True, "x": 99, "floor_id": f"{ws.id}-DEMO-HQ-F99"}, NotFound),
        ({"retired": True, "floor_id": 5}, NotFound),
        ({"x": 50, "type": "SOFA", "values": {"seats": "many"}}, ValueError),
        ({"type": "TV", "rotation": "NaN"}, ValueError),
        ({"y": 3, "retired": "yes"}, ValueError),
    ]
    for body, error in refused:
        with pytest.raises(error):
            r.change_item(item["id"], body)
    space = next(o for o in ws.floor_objects(f0) if o.kind == "space")
    r.correct(space.id, {"correction": {"name": "Something else"}})  # an unrelated change, saved
    it = Workspace.load(ws_path).items[item["id"]]
    assert (it.status, it.x, it.y, it.type, it.floor_id, it.values) == ("active", 10, 10, "COPIER", f0, {"model": "A3"})
    shown = {i["id"]: i for i in r.floor(f0)["items"]}[item["id"]]
    assert (shown["x"], shown["retired"]) == (10, False)
    moved = r.change_item(item["id"], {"x": 12, "rotation": 450, "values": {"model": "A4"}})
    assert (moved["x"], moved["rotation"], moved["values"]) == (12, 90, {"model": "A4"})


def test_taking_away_takes_what_was_chosen(demo):
    """A wall drawn across a space drawn in review (a colonnade, say): taking the wall
    away leaves the space, and the space the wall."""
    ws_path, ws, f0 = demo
    r = Review(ws_path)
    ring = [[200, 200], [210, 200], [210, 206], [200, 206]]
    wall, divider, span = [[205, 200], [205, 206]], [[200, 203], [210, 203]], [[201, 200], [202, 200]]

    def drawn():
        e = Workspace.load(ws_path).floor(f0).edits
        return e.walls, e.dividers, [o.span for o in e.openings], e.spaces

    def draw():
        for add in ({"space": ring}, {"wall": wall}, {"divider": divider}, {"opening": {"type": "door", "span": span}}):
            r.edit(f0, {"add": add})

    draw()
    r.edit(f0, {"remove": {"kind": "wall", "at": [205, 203], "shape": wall}})  # as the page sends it
    assert drawn() == ([], [divider], [span], [ring])
    r.edit(f0, {"remove": {"kind": "space", "at": [205, 203], "shape": ring + [ring[0]]}})  # closed, or not
    assert drawn() == ([], [divider], [span], [])
    r.edit(f0, {"remove": {"kind": "opening", "at": [201.5, 200], "shape": span}})
    assert drawn() == ([], [divider], [], [])
    with pytest.raises(NotFound):  # not drawn so: nothing else is taken
        r.edit(f0, {"remove": {"kind": "divider", "shape": [[200, 203], [209, 203]]}})
    with pytest.raises(NotFound):  # inside a space drawn, but no wall near: not the space
        r.edit(f0, {"remove": {"kind": "wall", "at": [201, 205]}})
    with pytest.raises(ValueError):
        r.edit(f0, {"remove": {"kind": ["wall"], "at": [201, 205]}})
    assert drawn() == ([], [divider], [], [])

    # a page that sends only a point: at one distance, the line before the space it lies in
    r.edit(f0, {"remove": {"kind": "divider", "shape": divider}})
    draw()
    r.edit(f0, {"remove": {"at": [205, 203]}})  # the wall's middle, on the divider too
    walls, dividers, *_ = drawn()
    assert len(walls) + len(dividers) == 1 and drawn()[3] == [ring]


def test_a_capacity_is_not_a_check(demo):
    """Setting (or clearing) how many a room seats does not take it off the review
    list: only a correction, or accepting it as it is, does; and either keeps its
    capacity, as its capacity keeps it checked."""
    ws_path, ws, _ = demo
    target = next(o for o in sorted(ws.objects.values(), key=lambda o: o.id)
                  if ws.review_reasons(o) and ws.review_reasons(o) != ["no type"])
    reasons = ws.review_reasons(target)
    r = Review(ws_path)

    seen = r.correct(target.id, {"capacity": 4})
    assert (seen["reasons"], seen["correction"], seen["capacity"]) == (reasons, None, 4)
    seen = r.correct(target.id, {"capacity": None})
    assert (seen["reasons"], seen["correction"]) == (reasons, None)
    assert target.id not in Workspace.load(ws_path).overrides  # nothing left of it

    # a capacity, then accepted as it is: checked, its capacity kept
    r.correct(target.id, {"capacity": 4})
    seen = r.correct(target.id, {"correction": {}})
    assert (seen["reasons"], seen["correction"], seen["capacity"]) == ([], {}, 4)
    assert not seen["hidden"]
    seen = r.correct(target.id, {"capacity": 6})  # still checked
    assert (seen["reasons"], seen["capacity"]) == ([], 6)
    seen = r.correct(target.id, {"capacity": None})  # and with no capacity
    assert (seen["reasons"], seen["correction"]) == ([], {})
    seen = r.correct(target.id, {"capacity": 2})
    assert seen["reasons"] == []
    # reset: no longer checked, its capacity kept
    seen = r.correct(target.id, {"reset": True})
    assert (seen["reasons"], seen["correction"], seen["capacity"]) == (reasons, None, 2)
    # accepted first, then a capacity: still checked
    r.correct(target.id, {"capacity": None})
    r.correct(target.id, {"correction": {}})
    seen = r.correct(target.id, {"capacity": 3})
    assert (seen["reasons"], seen["capacity"]) == ([], 3)
    # hidden, then shown again: as before capacities, no longer checked
    r.correct(target.id, {"hidden": True})
    seen = r.correct(target.id, {"hidden": False})
    assert (seen["reasons"], seen["hidden"], seen["capacity"]) == (reasons, False, 3)


def test_the_command_line_accepts_a_space_with_a_capacity(demo):
    from typer.testing import CliRunner

    from storeypath.cli import app

    ws_path, ws, _ = demo
    target = next(o for o in sorted(ws.objects.values(), key=lambda o: o.id)
                  if ws.review_reasons(o) and ws.review_reasons(o) != ["no type"])
    Review(ws_path).correct(target.id, {"capacity": 4})
    assert Workspace.load(ws_path).review_reasons(target)
    result = CliRunner().invoke(app, ["fix", str(ws_path), target.id])
    assert result.exit_code == 0, result.output
    saved = Workspace.load(ws_path)
    assert saved.review_reasons(saved.objects[target.id]) == [] and saved.overrides[target.id].capacity == 4
