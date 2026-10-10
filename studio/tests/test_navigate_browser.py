"""The Find the way page in a browser: tests/browser/navigate.mjs drives it in headless
Chrome (the viewers' harness) as a person would, on a copy of the demo project that
`storeypath review` serves (a database of its own, dropped when it stops): the way from
an entrance to an office upstairs shown in words and on the plan, a step clicked and
framed, the arrows through the steps, Play to the end, From and To swapped, step-free,
the light theme, and the 3D view. Needs Node.js, Chrome (or Chromium; CHROME names
another) and the viewer built."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import SERVER_URL
from test_review_browser import _free_port, _missing

HERE = Path(__file__).parent


@pytest.mark.skipif(_missing() is not None, reason=_missing() or "")
def test_find_the_way_in_a_browser(tmp_path):
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
        run = subprocess.run(["node", str(HERE / "browser" / "navigate.mjs"), f"http://127.0.0.1:{port}", code],
                             capture_output=True, text=True, timeout=900)
        assert run.returncode == 0, run.stdout + run.stderr
    finally:
        server.send_signal(signal.SIGINT)  # a clean stop: its database is dropped
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
