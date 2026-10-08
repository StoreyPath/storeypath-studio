"""Where Review lets an item stand (review_app/fit.js): in the room it was placed in,
and, near a wall or another item, turned square to it and moved up against it (the
magnet). Runs fit.js in Node."""

import shutil
import subprocess
from pathlib import Path

import pytest

FIT_JS = Path(__file__).parent.parent / "src" / "storeypath" / "review_app" / "fit.js"

SCENARIO = r"""
import { corners, fits, itemBox, roomAt, settle, snap, wallsOf } from "__FIT__";
const check = (ok, what, got) => { if (!ok) { console.error("FAILED:", what, JSON.stringify(got)); process.exit(1); } };
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol;
const square = (x0, y0, x1, y1) => [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]];
// an office 4 m by 5 m; an L-shaped hall; a room with a pillar
const office = square(0, 0, 4, 5);
const hall = [[[0, 0], [6, 0], [6, 2], [2, 2], [2, 6], [0, 6], [0, 0]]];
const pillared = [square(0, 0, 6, 6)[0], [[2.8, 2.8], [3.2, 2.8], [3.2, 3.2], [2.8, 3.2], [2.8, 2.8]]];
const sofa = { code: "SOFA", width: 2, depth: 0.9 };
const tv = { code: "TV", width: 1.4, depth: 0.1, mount: "wall" };

// what a desk takes: its chair, and by its grade a return, a cabinet, visitors' chairs
const junior = itemBox({ code: "DESK-JUNIOR", width: 1.2, depth: 0.6, grade: "junior" });
const director = itemBox({ code: "DESK-DIRECTOR", width: 2, depth: 1, grade: "director" });
check(near(junior[1], -(0.3 + 0.52)) && near(junior[3], 0.3), "a junior's desk and its chair", junior);
check(near(director[1], -(0.5 + 1.4)) && near(director[3], 0.5 + 0.15 + 0.46), "a director's: its cabinet behind, visitors across", director);
check(JSON.stringify(itemBox(sofa)) === JSON.stringify([-1, -0.45, 1, 0.45]), "anything else: its footprint", itemBox(sofa));

// a wall's way into the room, whichever way its ring runs
for (const w of wallsOf(office)) {
  const mid = [(w.a[0] + w.b[0]) / 2 + w.n[0] * 0.1, (w.a[1] + w.b[1]) / 2 + w.n[1] * 0.1];
  check(mid[0] > 0 && mid[0] < 4 && mid[1] > 0 && mid[1] < 5, "a wall's normal points in", w);
}

// the room: the space a point is in, a zone's own space when it is in a zone
const spaces = [{ id: "S", kind: "space", geometry: { type: "Polygon", coordinates: office } },
  { id: "Z", kind: "zone", space_id: "S", geometry: { type: "Polygon", coordinates: square(0, 0, 2, 5) } },
  { id: "X", kind: "space", ignored: true, geometry: { type: "Polygon", coordinates: square(10, 0, 12, 2) } }];
check(roomAt([1, 1], spaces).id === "S" && roomAt([11, 1], spaces) === null && roomAt([20, 20], spaces) === null, "the room", 0);

// near a wall: turned square to it (the least turn) and moved up against it
let s = snap({ at: [2, 0.8], rot: 10, box: itemBox(sofa), rings: office, reach: 0.4 });
check(near(s.rot, 0) && near(Math.min(...corners(s.at, s.rot, itemBox(sofa)).map((c) => c[1])), 0) && s.guides.length >= 1,
  "a sofa near the south wall: square to it, against it", s);
s = snap({ at: [2, 2.5], rot: 10, box: itemBox(sofa), rings: office, reach: 0.4 });
check(near(s.rot, 10) && near(s.at[0], 2) && s.guides.length === 0, "in the middle of the room: left as it is", s);
// into a corner
s = snap({ at: [1.2, 0.6], rot: 0, box: itemBox(sofa), rings: office, reach: 0.4 });
const cs = corners(s.at, s.rot, itemBox(sofa));
check(near(Math.min(...cs.map((c) => c[0])), 0) && near(Math.min(...cs.map((c) => c[1])), 0) && s.guides.length === 2, "into the corner", s);
// a TV goes on the nearest wall, its back to it, its screen into the room, however far
s = snap({ at: [3.2, 2.5], rot: 0, box: itemBox(tv), rings: office, reach: 0.4, mount: "wall" });
const back = corners(s.at, s.rot, itemBox(tv)).map((c) => c[0]);
check(near(Math.max(...back), 4) && near(s.rot, 270), "a TV on the east wall, facing west into the room (turned 270: 90 faces east)", s);
// a ceiling item is not drawn to walls
s = snap({ at: [0.3, 2.5], rot: 0, box: [-0.125, -0.125, 0.125, 0.125], rings: office, reach: 0.4, mount: "ceiling" });
check(near(s.at[0], 0.3) && s.guides.length === 0, "an access point stays where it is put", s);

// another item: side by side with it, lined up with it
const desk = { code: "DESK-JUNIOR", width: 1.2, depth: 0.6, grade: "junior" };
const other = { at: [1, 2.5], rot: 0, box: itemBox(desk) };
s = snap({ at: [2.35, 2.45], rot: 3, box: itemBox(desk), rings: office, others: [other], reach: 0.3 });
const mine = corners(s.at, s.rot, itemBox(desk)), theirs = corners(other.at, other.rot, other.box);
check(near(s.rot, 0) && near(Math.min(...mine.map((c) => c[0])), Math.max(...theirs.map((c) => c[0])), 1e-9)
  && near(Math.min(...mine.map((c) => c[1])), Math.min(...theirs.map((c) => c[1])), 1e-9), "a desk beside another, square and lined up", s);

// kept in the room: dragged through a wall it stops at it; in an L, round the corner it may not go
const box = itemBox(sofa);
let got = settle({ want: { at: [2, -3], rot: 0 }, last: { at: [2, 2.5], rot: 0 }, box, rings: office, reach: 0.3 });
check(got.held && near(Math.min(...corners(got.at, got.rot, box).map((c) => c[1])), 0, 1e-4), "stopped against the south wall", got);
check(fits(got.at, got.rot, box, office), "and still in", got);
got = settle({ want: { at: [4, 4], rot: 0 }, last: { at: [1, 1], rot: 0 }, box: [-0.5, -0.5, 0.5, 0.5], rings: hall, reach: 0.1, magnet: false });
check(fits(got.at, got.rot, [-0.5, -0.5, 0.5, 0.5], hall) && got.held, "not through the inside corner of an L", got);
check(!fits([3, 3], 0, [-0.5, -0.5, 0.5, 0.5], pillared) && fits([1, 1], 0, [-0.5, -0.5, 0.5, 0.5], pillared), "a pillar is in the way", 0);
// placed across a wall: moved in; too big for the room: refused
got = settle({ want: { at: [3.9, 2.5], rot: 0 }, last: null, box, rings: office, reach: 0.3 });
check(got && fits(got.at, got.rot, box, office), "placed where it does not fit: moved in", got);
check(settle({ want: { at: [2, 2.5], rot: 0 }, last: null, box: [-3, -3, 3, 3], rings: office, reach: 0.3 }) === null, "too big: refused", 0);
// free (Alt): where it is asked, held by nothing
got = settle({ want: { at: [2, -3], rot: 7 }, last: { at: [2, 2.5], rot: 0 }, box, rings: office, free: true });
check(got.at[1] === -3 && got.rot === 7, "Alt: anywhere, as it is", got);
// turned where there is no room to: moved in the least it takes, or refused
got = settle({ want: { at: [0.5, 2.5], rot: 90 }, last: { at: [0.5, 2.5], rot: 0 }, box: [-1, -0.45, 1, 0.45], rings: office, magnet: false });
check(got && fits(got.at, 90, [-1, -0.45, 1, 0.45], office) && got.rot === 90, "turned by a wall: moved in to fit", got);
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js")
def test_items_stay_in_their_room_and_line_up(tmp_path):
    script = tmp_path / "fit.mjs"
    script.write_text(SCENARIO.replace("__FIT__", FIT_JS.as_uri()))
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == "ok", run.stderr or run.stdout
