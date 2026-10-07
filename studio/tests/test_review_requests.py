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
from storeypath.server import Studio, make_server
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
    srv = make_server(s, port=0)
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
