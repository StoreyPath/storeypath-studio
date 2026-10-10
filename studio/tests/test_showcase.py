"""The demo `storeypath demo` builds (samples.build_showcase): a campus that is all made up,
read from its drawings as drawn, furnished sensibly (every item in its room, clear of the
walls), finished, a few rooms left to review, the way from its kiosk found through it, its
packages valid, and the same every time, so that its pictures can be taken again."""

import json
import math
import zipfile

import pytest
from shapely.geometry import Point, Polygon, shape
from typer.testing import CliRunner

from storeypath.catalogue import default_catalogue
from storeypath.cli import app
from storeypath.navigation import route
from storeypath.samples import (ORIGIN, SHOWCASE_CODE, build_showcase, main_floor, pavilion_floor)
from storeypath.validate import validate_package
from storeypath.workspace import Workspace

MAIN, PAV = f"{SHOWCASE_CODE}-CAMPUS-MAIN", f"{SHOWCASE_CODE}-CAMPUS-PAV"
TYPES = {t.code: t for t in default_catalogue().types}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("showcase")
    ws_path, packages = build_showcase(folder)
    return folder, Workspace.load(ws_path), packages


def _rooms(ws, floor_id):
    return [r for r in ws.floor_objects(floor_id) if r.kind in ("space", "zone")]


def test_two_buildings_their_floors_read_as_drawn(built):
    _, ws, packages = built
    assert ws.id == SHOWCASE_CODE and len(packages) == 2
    floors = {f_id: f for *_, f, f_id in ws.iter_floors()}
    assert sorted(floors) == [f"{MAIN}-F00", f"{MAIN}-F01", f"{MAIN}-F02", f"{PAV}-F00", f"{PAV}-F01"]
    assert floors[f"{MAIN}-F00"].method == "outlines" and floors[f"{PAV}-F00"].method == "walls"
    for f_id, cells in [(f"{MAIN}-F0{i}", main_floor(i)) for i in range(3)] + [(f"{PAV}-F0{i}", pavilion_floor(i)) for i in range(2)]:
        got = {}  # each room by its label as read: its name and number
        for r in _rooms(ws, f_id):
            e = ws.effective(r)
            got[" ".join(filter(None, (e["name"], e["number"])))] = e["type"]
        for c in cells:
            if c.label and not c.labels:  # (a room of two labels is divided into zones: below)
                assert got.get(" ".join(c.label)) == c.expected_type, (f_id, c.label, got.get(" ".join(c.label)))


def test_lifts_and_stairs_one_code_on_every_floor(built):
    _, ws, _ = built
    for building, n in ((MAIN, 3), (PAV, 2)):
        codes = {}
        for i in range(n):
            for r in _rooms(ws, f"{building}-F0{i}"):
                if ws.effective(r)["type"] in ("elevator", "stairs"):
                    codes.setdefault(r.id.split("-")[-1], set()).add(i)
        assert codes and all(len(floors) == n for floors in codes.values()), codes


def test_a_few_rooms_left_to_review_and_why(built):
    _, ws, _ = built
    left = {}
    for *_, f_id in ws.iter_floors():
        for r in _rooms(ws, f_id):
            if ws.review_reasons(r):
                e = ws.effective(r)
                left[(f_id.split("-")[-2] + "-" + f_id.split("-")[-1], e["name"], e["number"])] = ws.review_reasons(r)
    assert set(left) == {("MAIN-F00", None, None), ("MAIN-F00", "LOUNGE", "005"), ("MAIN-F00", "COPY ROOM", "007"),
                         ("MAIN-F01", None, "114"),
                         ("PAV-F00", "OPEN OFFICE", "P0-20"), ("PAV-F00", "QUIET ZONE", "P0-21")}, left
    assert left[("MAIN-F00", None, None)] == ["no name or number"]  # the lift with no label
    assert "no type" in left[("MAIN-F00", "LOUNGE", "005")]


def _footprint(item, extra_behind=0.0, round_it=(0.0, 0.0)):
    """An item's box on the plan (its desk's chair side counted in ``extra_behind``; a
    meeting table's chairs in ``round_it``: at its ends, along its sides)."""
    t = TYPES[item.type]
    w, d = t.width / 2 + round_it[0], t.depth / 2 + round_it[1]
    corners = [(-w, -d - extra_behind), (w, -d - extra_behind), (w, d), (-w, d)]  # its front (-y) is where its user sits
    a = math.radians(item.rotation)
    return Polygon([(item.x + x * math.cos(a) - y * math.sin(a), item.y + x * math.sin(a) + y * math.cos(a)) for x, y in corners])


def test_furnished_every_item_in_a_room_clear_of_its_walls(built):
    _, ws, _ = built
    kinds = {i.type for i in ws.items.values()}
    assert {"KIOSK", "COPIER", "ACCESS-POINT", "SOFA", "TV", "DESK-JUNIOR", "DESK-SENIOR", "DESK-SECTION-HEAD", "DESK-MANAGER",
            "DESK-DIRECTOR", "DESK-CLEVEL", "DESK-PRESIDENT"} | {f"MEETING-TABLE-{n}" for n in (4, 6, 8, 12, 14, 16)} <= kinds
    assert sum(1 for i in ws.items.values() if i.type == "KIOSK") == 1
    for *_, f_id in ws.iter_floors():
        rooms = [(r, shape(r.geometry)) for r in ws.floor_objects(f_id) if r.kind == "space"]
        for item in ws.floor_items(f_id):
            t = TYPES[item.type]
            chair = (1.4 if t.grade in ("director", "c_level", "president") else 0.74) if t.grade else 0.0
            # a meeting table's chairs, as the viewers seat it (fit.js tableChairs)
            out = (0.7 if t.width >= 3.6 else 0.52) if item.type.startswith("MEETING-") else 0.0
            box = _footprint(item, chair, (out if t.depth >= 0.8 else 0.0, out))
            inside = [r for r, poly in rooms if poly.buffer(0.02).contains(box)]
            assert inside, (f_id, item.type, item.x, item.y, item.rotation)
            # no two floor items overlap
            if t.mount == "floor":
                for other in ws.floor_items(f_id):
                    if other.id != item.id and TYPES[other.type].mount == "floor":
                        assert box.intersection(_footprint(other)).area < 1e-6, (item.type, other.type, item.x, item.y)


def test_the_kiosk_stands_at_the_entrance_in_the_reception(built):
    _, ws, _ = built
    kiosk = next(i for i in ws.items.values() if i.type == "KIOSK")
    reception = next(r for r in _rooms(ws, f"{MAIN}-F00") if r.name == "RECEPTION")
    assert kiosk.floor_id == f"{MAIN}-F00" and shape(reception.geometry).contains(Point(kiosk.x, kiosk.y))
    assert kiosk.y - ORIGIN[1] < 3  # by the entrance, on the south facade


def test_finished_as_a_person_chose(built):
    _, ws, packages = built
    reception = next(r for r in _rooms(ws, f"{MAIN}-F00") if r.name == "RECEPTION")
    assert ws.effective(reception)["floor_finish"] == "FLOOR-MARBLE-WHITE"
    assert ws.effective(reception)["wall_finish"] == "WALL-WOOD-SLATS"
    assert not ws.effective(reception)["corrected"]  # choosing finishes does not check a room
    with zipfile.ZipFile(packages[0]) as z:
        spaces = json.loads(z.read("spaces.geojson"))["features"]
    finished = [s for s in spaces if s["properties"]["floor_finish"]]
    assert len(finished) > 20 and {s["properties"]["wall_finish"] for s in finished} >= {"WALL-WOOD-WALNUT", "WALL-PAINT-NAVY"}


def test_the_way_from_the_kiosk_up_to_the_president_by_lift(built):
    _, ws, packages = built
    with zipfile.ZipFile(packages[0]) as z:
        nav = json.loads(z.read("navigation.json"))
    kiosk = next(i for i in ws.items.values() if i.type == "KIOSK")
    president = next(r for r in _rooms(ws, f"{MAIN}-F02") if r.name == "PRESIDENT OFFICE")
    way = route(nav, kiosk.id, president.id, accessible=True)
    assert [c["by"] for c in way["changes"]] == ["lift"] and way["changes"][0]["floors"] == 2
    assert way["legs"][0]["floor_id"] == f"{MAIN}-F00" and way["legs"][-1]["floor_id"] == f"{MAIN}-F02"
    assert 30 < way["metres"] < 120


def test_its_packages_are_valid(built):
    _, _, packages = built
    for p in packages:
        assert validate_package(p) == [], p


def test_the_same_every_time(built, tmp_path):
    _, ws, _ = built
    again = Workspace.load(build_showcase(tmp_path / "again")[0])
    assert sorted(again.items) == sorted(ws.items)
    assert {(i.id, i.type, i.x, i.y, i.rotation) for i in again.items.values()} == \
        {(i.id, i.type, i.x, i.y, i.rotation) for i in ws.items.values()}
    assert {k: (r.type, r.name, r.number) for k, r in again.objects.items()} == \
        {k: (r.type, r.name, r.number) for k, r in ws.objects.items()}


def test_the_demo_command(tmp_path):
    runner = CliRunner()
    said = runner.invoke(app, ["demo"])
    assert said.exit_code != 0 and "--studio" in said.output
    said = runner.invoke(app, ["demo", str(tmp_path / "demo")])
    assert said.exit_code == 0, said.output
    assert (tmp_path / "demo" / "demo.spproj").is_file() and (tmp_path / "demo" / "drawings" / "main-building-sheet.dxf").is_file()
    assert runner.invoke(app, ["demo", str(tmp_path / "demo")]).exit_code != 0  # not into a folder that is not empty


def test_the_demo_into_studio(tmp_path):
    from storeypath import db
    from storeypath.db.store import ProjectStore

    runner = CliRunner()
    said = runner.invoke(app, ["demo", "--studio"])
    assert said.exit_code == 0 and f"({SHOWCASE_CODE})" in said.output, said.output
    store = ProjectStore(db.connect())
    ws = store.load(SHOWCASE_CODE)
    assert len(ws.items) > 100 and len(list(ws.iter_floors())) == 5
    said = runner.invoke(app, ["demo", "--studio"])
    assert said.exit_code == 0 and "already" in said.output, said.output
