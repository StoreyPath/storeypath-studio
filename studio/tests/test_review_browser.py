"""Review's page in a browser: tests/browser/review.mjs drives it in headless Chrome (the
viewers' harness) as a person would, on a copy of the demo project that `storeypath
review` serves (a database of its own, dropped when it stops). Choosing rooms (a click,
Shift-click, a band), every tool by its key, Measure, placing an item and finding it by
its tag, the command palette, a door's sizes, review mode's keys and undo, the panels,
the labels over the print, the light theme, another page's change shown live, 3D and
walking. Needs Node.js, Chrome (or Chromium; CHROME names another) and the viewer built."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
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


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_review_in_a_browser(tmp_path):
    from storeypath.samples import build_demo

    spproj, _ = build_demo(tmp_path / "demo")
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
        run = subprocess.run(["node", str(HERE / "browser" / "review.mjs"), f"http://127.0.0.1:{port}", code],
                             capture_output=True, text=True, timeout=900)
        assert run.returncode == 0, run.stdout + run.stderr
    finally:
        server.send_signal(signal.SIGINT)  # a clean stop: its database is dropped
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
