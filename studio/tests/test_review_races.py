"""A change made in Review while a job (converting, exporting) works on the project
is refused, saying why, rather than saved and then lost when the job saves the copy
of the project it loaded before it."""

import json
import threading
import urllib.error
import urllib.request

import pytest
from shapely.geometry import shape

from storeypath.review import Busy, Review
from storeypath.samples import build_demo
from storeypath.server import Studio
from sessions import admin_server
from storeypath.workspace import Workspace


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


def test_review_changes_wait_for_no_job(tmp_path, monkeypatch):
    monkeypatch.setattr("storeypath.review.BUSY_WAIT_S", 0.05)
    ws_path, _ = build_demo(tmp_path / "demo")
    ws = Workspace.load(ws_path)
    f0 = f"{ws.id}-DEMO-HQ-F00"
    space = next(r for r in ws.floor_objects(f0) if r.kind == "space")
    job = threading.RLock()
    r = Review(ws_path, changing=lambda: job)
    item = r.add_item(f0, {"type": "DESK-JUNIOR", "x": 1, "y": 1})
    before = ws_path.read_bytes()

    held, release = threading.Event(), threading.Event()

    def work():  # a job, holding the project while it works
        with job:
            held.set()
            release.wait(10)

    t = threading.Thread(target=work)
    t.start()
    held.wait(5)
    try:
        for change in (lambda: r.correct(space.id, {"correction": {"name": "During the job"}}),
                       lambda: r.add_item(f0, {"type": "DESK-JUNIOR", "x": 2, "y": 2}),
                       lambda: r.change_item(item["id"], {"x": 5}),
                       lambda: r.edit(f0, {"add": {"wall": [[0, 0], [3, 0]]}})):
            with pytest.raises(Busy, match="a job is working on this project"):
                change()
        assert ws_path.read_bytes() == before  # nothing written
    finally:
        release.set()
        t.join()
    assert r.correct(space.id, {"correction": {"name": "After the job"}})["name"] == "After the job"
    assert Workspace.load(ws_path).items[item["id"]].x == 1


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr("storeypath.review.BUSY_WAIT_S", 0.05)
    ws_path, _ = build_demo(tmp_path / "data" / "demo")
    s = Studio(tmp_path / "data", model=NoModel(), warm=False)
    srv = admin_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", s, ws_path
    srv.shutdown()
    srv.server_close()


def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-StoreyPath": "1"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def finished(base, job):
    import time

    for _ in range(600):
        _, job = call(f"{base}/api/jobs/{job['id']}")
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError(job)


@pytest.mark.parametrize("which", ["convert", "export"])
def test_a_job_never_loses_what_review_saved(studio, monkeypatch, which):
    """A correction or an item sent while a job works is refused (409, saying why), not
    saved and then lost when the job saves; one sent before or after is kept, and an
    item's number is never given twice."""
    base, _, ws_path = studio
    ws = Workspace.load(ws_path)
    code, f0, hq = ws.id, f"{ws.id}-DEMO-HQ-F00", f"{ws.id}-DEMO-HQ"
    space = max((r for r in ws.floor_objects(f0) if r.kind == "space"), key=lambda r: shape(r.geometry).area)
    p = shape(space.geometry).representative_point()

    started, go = threading.Event(), threading.Event()
    module, name = ("storeypath.convert", "convert_floor") if which == "convert" else ("storeypath.export", "export_package")
    real = getattr(__import__(module, fromlist=[name]), name)

    def slow(*a, **kw):  # the job, loaded and working
        started.set()
        go.wait(10)
        return real(*a, **kw)

    monkeypatch.setattr(f"{module}.{name}", slow)
    _, first = call(f"{base}/api/projects/{code}/floors/{f0}/items", {"type": "DESK-JUNIOR", "x": p.x, "y": p.y})
    number = int(first["id"][-6:])
    _, job = call(f"{base}/api/projects/{code}/{which}", {} if which == "convert" else {"building": hq})
    assert started.wait(10)
    try:
        status, answer = call(f"{base}/api/projects/{code}/objects/{space.id}", {"correction": {"name": "During"}})
        assert status == 409 and "a job is working on this project" in answer["error"]
        status, answer = call(f"{base}/api/projects/{code}/floors/{f0}/items", {"type": "DESK-JUNIOR", "x": p.x, "y": p.y})
        assert status == 409 and answer["busy"]
        status, _ = call(f"{base}/api/projects/{code}/items/{first['id']}", {"retired": True})
        assert status == 409
        status, _ = call(f"{base}/api/projects/{code}/floors/{f0}/edits", {"add": {"wall": [[0, 0], [3, 0]]}})
        assert status == 409
    finally:
        go.set()
    assert finished(base, job)["state"] == "done"
    saved = Workspace.load(ws_path)
    assert saved.items[first["id"]].status == "active"  # as before the job: nothing half kept
    assert space.id not in saved.overrides and saved.next_item_seq == number + 1
    # after the job, kept; and through the next job too
    assert call(f"{base}/api/projects/{code}/objects/{space.id}", {"correction": {"name": "After"}})[0] == 200
    _, second = call(f"{base}/api/projects/{code}/floors/{f0}/items", {"type": "DESK-JUNIOR", "x": p.x, "y": p.y})
    assert int(second["id"][-6:]) == number + 1  # the next number: none was given during the job
    go.set()
    _, job = call(f"{base}/api/projects/{code}/{which}", {} if which == "convert" else {"building": hq})
    assert finished(base, job)["state"] == "done"
    saved = Workspace.load(ws_path)
    assert saved.overrides[space.id].name == "After" and {first["id"], second["id"]} <= set(saved.items)
