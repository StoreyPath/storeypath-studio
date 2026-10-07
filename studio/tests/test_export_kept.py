"""A package is kept, and entered as an export, only when it is valid: one that is not
leaves no file and no record (its number is not used up). The command line exports
with the item types of the Studio data folder the project is in."""

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from shapely.geometry import shape
from typer.testing import CliRunner

from storeypath import catalogue
from storeypath.cli import app
from storeypath.samples import build_demo
from storeypath.server import Studio, make_server
from storeypath.workspace import Workspace


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-StoreyPath": "1"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def finished(base, job):
    for _ in range(600):
        _, job = call(f"{base}/api/jobs/{job['id']}")
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError(job)


@pytest.fixture
def studio(tmp_path):
    ws_path, _ = build_demo(tmp_path / "data" / "demo")
    s = Studio(tmp_path / "data", model=NoModel(), warm=False)
    srv = make_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", ws_path
    srv.shutdown()
    srv.server_close()


def kept(ws_path: Path) -> list[str]:
    """What is in the project's exports folder, hidden files too."""
    folder = ws_path.parent / "exports"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def test_an_invalid_package_is_neither_kept_nor_entered(studio, monkeypatch):
    base, ws_path = studio
    ws = Workspace.load(ws_path)
    hq, before = f"{ws.id}-DEMO-HQ", len(ws.exports)
    monkeypatch.setattr("storeypath.validate.validate_package", lambda path: ["spaces.geojson: broken"])
    _, job = call(f"{base}/api/projects/{ws.id}/export", {"building": hq})
    job = finished(base, job)
    assert job["state"] == "failed" and "not kept" in job["error"]
    assert "invalid: spaces.geojson: broken" in job["log"]
    assert kept(ws_path) == [] and len(Workspace.load(ws_path).exports) == before
    _, project = call(f"{base}/api/projects/{ws.id}")
    assert project["exports"] == [] and project["exported"] == before
    monkeypatch.undo()
    _, job = call(f"{base}/api/projects/{ws.id}/export", {"building": hq})
    job = finished(base, job)
    assert job["state"] == "done"
    sequence = before + 1  # its number was not used up
    assert job["result"]["file"] == f"{ws.id}-{sequence:03d}-HQ.storeypath"
    assert kept(ws_path) == [job["result"]["file"]]
    last = Workspace.load(ws_path).exports[-1]
    assert (last.sequence, last.file) == (sequence, job["result"]["file"])


def test_a_package_is_kept_only_with_its_record(studio):
    """An item of a type no catalogue here has (a project file of an older format may
    hold one): whether its package is valid or not, the file and the record go together."""
    base, ws_path = studio
    ws = Workspace.load(ws_path)
    f0 = f"{ws.id}-DEMO-HQ-F00"
    room = max((r for r in ws.floor_objects(f0) if r.kind == "space"), key=lambda r: shape(r.geometry).area)
    p = shape(room.geometry).representative_point()
    ws.add_item("OLD-KIOSK", f0, p.x, p.y)
    ws.save(ws_path)
    before = len(ws.exports)
    _, job = call(f"{base}/api/projects/{ws.id}/export", {"building": f"{ws.id}-DEMO-HQ"})
    job = finished(base, job)
    recorded = len(Workspace.load(ws_path).exports) - before
    assert (job["state"] == "done") == bool(kept(ws_path)) == (recorded == 1)
    assert not any(name.startswith(".") for name in kept(ws_path))


def _demo_with_a_studio_type(tmp_path):
    data = tmp_path / "data"
    ws_path, _ = build_demo(data / "demo")
    cat = catalogue.load(data)
    cat.types.append(catalogue.ItemType(code="BENCH-4", name_en="Bench of four", workplaces=4, grade="junior"))
    catalogue.save(data, cat)
    ws = Workspace.load(ws_path)
    f0 = f"{ws.id}-DEMO-HQ-F00"
    room = max((r for r in ws.floor_objects(f0) if r.kind == "space"), key=lambda r: shape(r.geometry).area)
    p = shape(room.geometry).representative_point()
    ws.add_item("BENCH-4", f0, p.x, p.y)
    ws.save(ws_path)
    return ws_path, len(ws.exports)


def test_the_command_line_exports_with_the_studios_item_types(tmp_path):
    ws_path, before = _demo_with_a_studio_type(tmp_path)
    out = tmp_path / "out" / "hq.storeypath"
    result = CliRunner().invoke(app, ["export", str(ws_path), "-o", str(out), "--building", "HQ"])
    assert result.exit_code == 0, result.output
    import zipfile

    with zipfile.ZipFile(out) as z:
        assert "BENCH-4" in {t["code"] for t in json.loads(z.read("catalogue.json"))["types"]}
    exports = Workspace.load(ws_path).exports
    assert len(exports) == before + 1 and exports[-1].file == "hq.storeypath"
    assert sorted(p.name for p in out.parent.iterdir()) == ["hq.storeypath"]


def test_the_command_line_keeps_no_invalid_package(tmp_path, monkeypatch):
    ws_path, before = _demo_with_a_studio_type(tmp_path)
    out = tmp_path / "out" / "hq.storeypath"
    monkeypatch.setattr("storeypath.validate.validate_package", lambda path: ["spaces.geojson: broken"])
    result = CliRunner().invoke(app, ["export", str(ws_path), "-o", str(out), "--building", "HQ"])
    assert result.exit_code != 0 and "failed validation" in result.output and "broken" in result.output
    assert not out.exists() and list(out.parent.iterdir()) == []
    assert len(Workspace.load(ws_path).exports) == before
