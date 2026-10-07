"""The review page's 3D view, asked to show another building (or the project as
saved) while one is being built, builds it once that one is done: no request is
dropped, and a change saved meanwhile is not taken as shown. Runs review.js's
refresh3d and build3d in Node, with the page and the 3D world stood in for."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REVIEW_JS = Path(__file__).parent.parent / "src" / "storeypath" / "review_app" / "review.js"


def function(src: str, name: str) -> str:
    """A top-level function's source, by its braces."""
    start = re.search(rf"^(async )?function {name}\(", src, re.M).start()
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


SCENARIO = r"""
const elements = {};
const $ = (id) => (elements[id] ||= { textContent: "", hidden: true });
const BASE = "projects/P";
const state = { floor: { id: "P-SITE-A-F00" }, selected: null, asset: null, showHidden: false };
const view3d = { world: null, building: null, stale: true, shown: true, busy: null, again: false, picking: false };
const opened = [], floors = [];
let finish = null;
view3d.world = {
  open(url) { opened.push(decodeURIComponent(url.split("building=")[1])); return new Promise((r) => (finish = r)); },
  setFloor(id) { floors.push(id); },
};
const toast = (m) => { throw new Error(m); };
const show3d = (on) => { view3d.shown = on; };
const pick3d = () => {};
function changed3d() { view3d.stale = true; $("update3d").hidden = !view3d.shown; }
const tick = () => new Promise((r) => setTimeout(r, 0));
const check = (ok, what) => { if (!ok) { console.error("FAILED:", what, JSON.stringify({ opened, floors, view3d: { ...view3d, world: 0, busy: 0 } })); process.exit(1); } };

__FUNCTIONS__

// another building chosen while the first is built: built next
let first = refresh3d();
await tick();
state.floor = { id: "P-SITE-B-F01" };
let second = refresh3d();
finish(); await tick(); await tick();
check(opened.join() === "P-SITE-A,P-SITE-B", "the second building built after the first");
finish(); await first; await second;
check(view3d.building === "P-SITE-B" && floors.join() === "P-SITE-B-F01", "shows B's floor, never on A's world");

// a change saved while it is built: still to be shown (Update 3D offered), not taken as shown
changed3d();
first = refresh3d();
await tick();
changed3d();
finish(); await first;
check(view3d.stale && $("update3d").hidden === false, "a change saved meanwhile leaves it stale");
check(opened.length === 3, "not built again by itself");

// Update 3D clicked while one is built: built once more after it
view3d.stale = true;
first = refresh3d();
await tick();
view3d.stale = true;
second = refresh3d();
finish(); await tick(); await tick();
finish(); await first; await second;
check(opened.length === 5 && !view3d.stale && $("update3d").hidden, "built again, up to date");
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_3d_refresh_asked_while_busy_is_not_dropped(tmp_path):
    src = REVIEW_JS.read_text(encoding="utf-8")
    script = SCENARIO.replace("__FUNCTIONS__", function(src, "refresh3d") + "\n" + function(src, "build3d"))
    (tmp_path / "scenario.mjs").write_text(script, encoding="utf-8")
    run = subprocess.run(["node", str(tmp_path / "scenario.mjs")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == "ok", run.stdout + run.stderr
