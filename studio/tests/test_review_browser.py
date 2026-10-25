"""Review's page in a browser: tests/browser/review.mjs drives it in headless Chrome (the
viewers' harness) as a person would, on a copy of the tests' campus that `storeypath
review` serves (a database of its own, dropped when it stops). Choosing rooms (a click,
Shift-click, a band), every tool by its key, Measure, placing an item and finding it by
its tag, the command palette, a door's sizes, review mode's keys and undo, the panels,
the labels over the print, the light theme, another page's change shown live, 3D and
walking. And Studio's 3D page (world.html, tests/browser/world.mjs) on the demo campus
(`storeypath demo`): each of its controls, a room's details, walking and its doors,
presenting, the light theme, what its address says, and the way Review opens it. And the Item
types page (tests/browser/itemtypes.mjs), two people on one floor, one a room
(tests/browser/together.mjs), and pages opened again while Studio works
(tests/browser/progress.mjs). Needs
Node.js, Chrome (or Chromium; CHROME names another) and the viewer built."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from conftest import SERVER_URL

HERE = Path(__file__).parent
REPO = HERE.parent.parent
CHROMES = [os.environ.get("CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
           "/usr/bin/chromium", "/usr/bin/chromium-browser"]


def _missing() -> str | None:
    if shutil.which("node") is None:
        return "needs Node.js"
    if not any(c and Path(c).exists() for c in CHROMES):
        return "needs Chrome or Chromium (CHROME names it)"
    if not (REPO / "storeypath-viewer" / "viewer" / "svg" / "dist" / "read.js").is_file():
        return "needs the 2D viewer built (npm run build in storeypath-viewer/viewer/svg)"
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _reviewing(spproj: Path):
    """`storeypath review` serving a project (a database of its own, dropped when it
    stops): its address and the project's code."""
    port = _free_port()
    cli = Path(sys.executable).parent / "storeypath"
    server = subprocess.Popen([str(cli), "review", str(spproj), "--port", str(port), "--no-open"],
                              env={**os.environ, "STOREYPATH_DATABASE_URL": SERVER_URL},
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        code = None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and code is None:
            line = server.stdout.readline()
            if not line and server.poll() is not None:
                break
            if "review.html?p=" in line:
                code = line.split("review.html?p=")[1].split()[0]
        assert code, "storeypath review did not start"
        yield f"http://127.0.0.1:{port}", code
    finally:
        server.send_signal(signal.SIGINT)  # a clean stop: its database is dropped
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_review_in_a_browser(tmp_path):
    from storeypath.samples import build_demo

    spproj, _ = build_demo(tmp_path / "demo")
    with _reviewing(spproj) as (address, code):
        run = subprocess.run(["node", str(HERE / "browser" / "review.mjs"), address, code],
                             capture_output=True, text=True, timeout=900)
        assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_many_people_on_a_floor_in_a_browser(tmp_path, monkeypatch):
    """Two people on HQ's ground floor, one a room (tests/browser/together.mjs): Sara's page
    shows the room Khalid is editing as his, refuses to place an item in it, places one in
    hers, and frees his once he is done. A Studio with accounts (people.Team)."""
    from people import Team
    from storeypath import accounts as acc

    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    team = Team(tmp_path / "data")
    try:
        run = subprocess.run(["node", str(HERE / "browser" / "together.mjs"), f"http://127.0.0.1:{team.port}", team.code,
                              team.tokens["sara"], team.tokens["khalid"]], capture_output=True, text=True, timeout=600)
        assert run.returncode == 0, run.stdout + run.stderr
    finally:
        team.close()


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_work_in_progress_picked_up_by_a_page_opened_again(tmp_path, monkeypatch):
    """Pages opened while Studio works (tests/browser/progress.mjs): finding plans held
    twice here (each let go when the page says so), a reading of the ground floor after
    each, and a drawing waiting for khalid to choose what to keep."""
    import threading

    import ezdxf

    from people import Team
    from sessions import call
    from storeypath import accounts as acc
    from storeypath import server as server_module

    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    gates = [threading.Event(), threading.Event()]
    held = iter(gates)
    real = server_module.read_units

    def waiting(*args, **kwargs):
        assert next(held).wait(600), "never let go"
        return real(*args, **kwargs)

    monkeypatch.setattr(server_module, "read_units", waiting)
    team = Team(tmp_path / "data")
    try:
        sheet = tmp_path / "villa.dxf"
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (10, 0))
        doc.header["$LASTSAVEDBY"] = "someone"
        doc.saveas(sheet)
        _, job, _ = call(team.port, "PUT", f"/api/projects/{team.code}/drawings/Owner%20villa.dxf", raw=sheet.read_bytes(),
                         token=team.tokens["khalid"])
        assert team.finished("khalid", job)["result"]["pending"]
        for _ in gates:
            assert team("khalid", "POST", "drawings/hq-level-0.dxf/plans", {})[0] == 200
            assert team("khalid", "POST", f"floors/{team.hq0}/convert", {})[0] == 200
        run = subprocess.Popen(["node", str(HERE / "browser" / "progress.mjs"), f"http://127.0.0.1:{team.port}", team.code,
                                team.tokens["khalid"], team.hq0], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        said = []
        try:
            for line in run.stdout:
                said.append(line)
                if line.startswith("release "):
                    gates[int(line.split()[1]) - 1].set()
            assert run.wait(timeout=600) == 0, "".join(said)
        finally:
            run.kill()
    finally:
        for gate in gates:
            gate.set()
        team.close()


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_courtyards_in_a_browser(tmp_path, monkeypatch):
    """A floor with a courtyard and a light well open to the sky (tests/browser/courtyards.mjs):
    the plan's outline open there; the courtyard, set aside, chosen by a click where it is
    (deleted, not shown) and in 3D; said to be a room, open to the sky again, set aside as
    part of the floor from the menu, and undone, its floor's outline and 3D following."""
    import storeypath.vision as vision
    from test_courtyards import COURTYARD, ORIGIN, _area, as_image, ring_building

    monkeypatch.setattr(vision.FloorPrint, "view", as_image)
    ws, floors, _ = ring_building(tmp_path)
    spproj = tmp_path / "ring.spproj"
    ws.save(spproj)
    yard = _area(ws, floors[0], "courtyard")
    x0, y0, x1, y1 = COURTYARD
    middle = f"{ORIGIN[0] + (x0 + x1) / 2},{ORIGIN[1] + (y0 + y1) / 2}"
    with _reviewing(spproj) as (address, code):
        run = subprocess.run(["node", str(HERE / "browser" / "courtyards.mjs"), address, code, floors[0], yard.id, middle],
                             capture_output=True, text=True, timeout=600)
        assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_the_item_types_page_in_a_browser(tmp_path):
    """Studio's Item types page (tests/browser/itemtypes.mjs): a type added, changed,
    retired and restored; types taken from a file, some chosen; a file refused; the
    package export's choice of item types."""
    from storeypath.samples import build_demo

    spproj, _ = build_demo(tmp_path / "demo")
    files = tmp_path / "files"
    files.mkdir()
    with _reviewing(spproj) as (address, code):
        run = subprocess.run(["node", str(HERE / "browser" / "itemtypes.mjs"), address, code, str(files)],
                             capture_output=True, text=True, timeout=600)
        assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_the_3d_page_in_a_browser(tmp_path):
    from storeypath.samples import build_showcase

    spproj, _ = build_showcase(tmp_path / "demo")
    with _reviewing(spproj) as (address, code):
        run = subprocess.run(["node", str(HERE / "browser" / "world.mjs"), address, code],
                             capture_output=True, text=True, timeout=900)
        assert run.returncode == 0, run.stdout + run.stderr
