"""The review page's 3D view is built once and kept: asked to show another building
while one is being built, it builds it once that one is done (no request dropped); a
floor changed meanwhile (walls drawn, the floor read again) is read again afterwards,
that floor alone, never taken as shown; a building moved is built again whole. Runs
Review's refresh3d and build3d (review/view3d.js) in Node, with the page and the 3D world stood in for."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

VIEW3D_JS = Path(__file__).parent.parent / "src" / "storeypath" / "review_app" / "review" / "view3d.js"


def function(src: str, name: str) -> str:
    """A top-level function's source, by its braces (an export as a plain function)."""
    start = re.search(rf"^(export )?(async )?function {name}\(", src, re.M).start()
    if src.startswith("export ", start):
        start += len("export ")
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
const at = "2026-10-09T10:00:00";
const state = { floor: { id: "P-SITE-A-F00", converted_at: at }, selected: null, asset: null, showHidden: false };
const view3d = { world: null, building: null, stale: true, shown: true, mode: "3d", busy: null, again: false, picking: false,
  dirty: new Set() };
const buildingOf = (id) => id.split("-").slice(0, 3).join("-");
const opened = [], reloaded = [], floors = [], said = [];
let finish = null, fail = false;
view3d.world = {
  open(url) { opened.push(decodeURIComponent(url.split("building=")[1])); return new Promise((r) => (finish = r)); },
  reload(url, { floors }) {
    reloaded.push(floors.join("+"));
    return fail ? Promise.reject(new Error("offline")) : new Promise((r) => (finish = r));
  },
  setFloor(id) { floors.push(id); },
};
const toast = (m) => { said.push(m); };
const say = () => {};
const setView = (mode) => { view3d.shown = mode !== "2d"; };
const pick3d = () => {};
let updated = 0, modes = 0;
const update3d = () => { updated++; };
const applyMode = () => { modes++; };
const tick = () => new Promise((r) => setTimeout(r, 0));
const check = (ok, what) => { if (!ok) { console.error("FAILED:", what, JSON.stringify({ opened, reloaded, floors, said, view3d: { ...view3d, world: 0, busy: 0, dirty: [...view3d.dirty] } })); process.exit(1); } };

__FUNCTIONS__

// another building chosen while the first is built: built next
let first = refresh3d();
await tick();
state.floor = { id: "P-SITE-B-F01", converted_at: at };
let second = refresh3d();
finish(); await tick(); await tick();
check(opened.join() === "P-SITE-A,P-SITE-B", "the second building built after the first");
finish(); await first; await second;
check(view3d.building === "P-SITE-B" && floors.join() === "P-SITE-B-F01", "shows B's floor, never on A's world");
check(updated === 1 && modes >= 1, "its items and rooms as the page has them, in the mode asked for");

// shown again: nothing built again (the world is kept)
await refresh3d();
check(opened.length === 2 && reloaded.length === 0 && floors.length === 2, "switching back builds nothing");

// a floor read again (walls drawn): that floor alone; another changed meanwhile: after it
view3d.dirty.add("P-SITE-B-F01");
first = refresh3d();
await tick();
check(reloaded.join() === "P-SITE-B-F01", "that floor alone read again, not the building");
view3d.dirty.add("P-SITE-B-F00");
second = refresh3d();
finish(); await tick(); await tick();
check(reloaded.join() === "P-SITE-B-F01,P-SITE-B-F00", "the floor changed meanwhile read again after it");
finish(); await first; await second;
check(view3d.dirty.size === 0 && opened.length === 2, "up to date, the building never built again whole");

// another building's floor changed: left for when that building is shown
view3d.dirty.add("P-SITE-A-F00");
await refresh3d();
check(reloaded.length === 2 && view3d.dirty.has("P-SITE-A-F00"), "not read for this building");

// a read that fails: said, and the floor still to read (2D shown meanwhile)
fail = true;
view3d.dirty.add("P-SITE-B-F01");
await refresh3d();
check(said.length === 1 && view3d.dirty.has("P-SITE-B-F01") && !view3d.shown, "not lost when it fails");
fail = false;
view3d.shown = true;

// the building moved (stale): built again whole, which holds every change of it
view3d.stale = true;
first = refresh3d();
await tick();
finish(); await first;
check(opened.join() === "P-SITE-A,P-SITE-B,P-SITE-B" && !view3d.stale, "built again whole when moved");
check(!view3d.dirty.has("P-SITE-B-F01") && view3d.dirty.has("P-SITE-A-F00"), "this building's changes in it; the other's kept");
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_3d_refresh_asked_while_busy_is_not_dropped(tmp_path):
    src = VIEW3D_JS.read_text(encoding="utf-8")
    script = SCENARIO.replace("__FUNCTIONS__", function(src, "refresh3d") + "\n" + function(src, "build3d"))
    (tmp_path / "scenario.mjs").write_text(script, encoding="utf-8")
    run = subprocess.run(["node", str(tmp_path / "scenario.mjs")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == "ok", run.stdout + run.stderr
