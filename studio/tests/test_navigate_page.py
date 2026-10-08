"""The Navigate page's own logic (review_app/navigate.js), run in Node on a conformance
package's network: what a way may start and end at (kiosks, then entrances, then every
room, each with its floor), the search over them, and times in words."""

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from storeypath.assets import asset_dir

PAGE = Path(__file__).parent.parent / "src" / "storeypath" / "review_app" / "navigate.js"

SCRIPT = r"""
const { choicesOf, search, duration } = await import(process.argv[1]);
const network = JSON.parse(await new Promise((done) => { let s = ""; process.stdin.on("data", (d) => (s += d)); process.stdin.on("end", () => done(s)); }));
const choices = choicesOf(network);
console.log(JSON.stringify({
  groups: choices.map((c) => c.group),
  kiosk: choices.find((c) => c.group === "kiosk"),
  entrance: choices.find((c) => c.group === "entrance"),
  office: search(choices, "office 112"),
  floor: search(choices, "floor 1 112").map((c) => c.id),
  nothing: search(choices, "no such room"),
  all: search(choices, "  ").length === choices.length,
  times: [duration(42.4), duration(60), duration(89.6), duration(184)],
}));
"""


@pytest.fixture(scope="module")
def page():
    if shutil.which("node") is None:
        pytest.skip("Node.js is not here")
    with zipfile.ZipFile(asset_dir("spec") / "conformance" / "packages" / "campus-hq.storeypath") as z:
        network = json.loads(z.read("navigation.json"))
    out = subprocess.run(["node", "--input-type=module", "-e", SCRIPT, PAGE.as_uri()], input=json.dumps(network),
                         capture_output=True, text=True, timeout=60, check=True)
    return network, json.loads(out.stdout)


def test_kiosks_then_entrances_then_rooms(page):
    network, got = page
    groups = got["groups"]
    assert groups[0] == "kiosk" and set(groups) == {"kiosk", "entrance", "room"}
    assert groups == sorted(groups, key=["kiosk", "entrance", "room"].index)
    kiosk = got["kiosk"]
    assert kiosk["label"] == "Kiosk in RECEPTION 017" and kiosk["sub"] == "Ground floor"
    assert kiosk["node"] == f"kiosk:{kiosk['id']}"  # asked for by its item's ID
    assert got["entrance"]["label"].startswith("Entrance into ") and got["entrance"]["id"].startswith("door:")


def test_rooms_are_found_by_name_number_and_floor(page):
    network, got = page
    (office,) = got["office"]
    assert office["label"] == "OFFICE 112" and office["sub"].startswith("Floor 1 · office")
    assert got["floor"] == [office["id"]]
    assert got["nothing"] == [] and got["all"]


def test_times_in_words(page):
    assert page[1]["times"] == ["42 s", "1 min", "1 min 30 s", "3 min"]
