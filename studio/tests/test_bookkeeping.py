"""What a building's package lists against the last package that held it (format 0.7:
one building per package), and what a project keeps of a package opened into it."""

import json
import shutil
import zipfile

import pytest
from shapely.geometry import shape

from storeypath.export import export_package
from storeypath.validate import validate_package
from storeypath.workspace import Workspace


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The demo campus, built once: its two buildings exported once each (1: HQ, 2: ANNEX)."""
    from storeypath.samples import build_demo

    ws_path, _ = build_demo(tmp_path_factory.mktemp("built") / "demo")
    return ws_path


@pytest.fixture
def campus(built, tmp_path):
    """A copy of the demo campus: (workspace, its path, HQ's ID, the ANNEX's ID)."""
    folder = tmp_path / "data" / "demo"
    shutil.copytree(built.parent, folder)
    ws_path = folder / built.name
    ws = Workspace.load(ws_path)
    return ws, ws_path, f"{ws.id}-DEMO-HQ", f"{ws.id}-DEMO-ANNEX"


def _read(path):
    """A package's changes, and the IDs of its items."""
    assert validate_package(path) == []
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("changes.json")), {f["id"] for f in json.loads(z.read("items.geojson"))["features"]}


def _ground(ws, b_id):
    """A building's ground floor, and a point in its largest room."""
    f_id = f"{b_id}-F00"
    room = max((shape(r.geometry) for r in ws.floor_objects(f_id) if r.kind == "space"), key=lambda g: g.area)
    p = room.representative_point()
    return f_id, p.x, p.y


def _carry(item, to):
    item.floor_id, item.x, item.y = to


def test_an_item_moved_away_is_listed_by_the_building_whatever_was_exported_between(campus, tmp_path):
    # The desk carried from the HQ to the ANNEX, the ANNEX exported first: the HQ's
    # next package (its last held the desk) still lists it as moved away. Carried
    # back, the HQ's next holds it again (changed: not new), and the ANNEX's next
    # (its last held it) lists it as moved away, to the HQ.
    ws, _, hq, annex = campus
    in_hq = _ground(ws, hq)
    desk = ws.add_item("DESK-JUNIOR", *in_hq)
    export_package(ws, tmp_path / "hq-1.storeypath", building=hq)
    export_package(ws, tmp_path / "annex-1.storeypath", building=annex)

    _carry(desk, _ground(ws, annex))
    export_package(ws, tmp_path / "annex-2.storeypath", building=annex)
    changes, items = _read(tmp_path / "annex-2.storeypath")
    assert desk.id in items and desk.id in changes["changed"] and desk.id not in changes["added"]
    export_package(ws, tmp_path / "hq-2.storeypath", building=hq)
    changes, items = _read(tmp_path / "hq-2.storeypath")
    assert desk.id not in items and changes["moved_away"] == [{"id": desk.id, "building_id": annex}]
    assert desk.id not in changes["retired"]

    _carry(desk, in_hq)
    export_package(ws, tmp_path / "hq-3.storeypath", building=hq)
    changes, items = _read(tmp_path / "hq-3.storeypath")
    assert desk.id in items and desk.id in changes["changed"] and desk.id not in changes["added"]
    export_package(ws, tmp_path / "annex-3.storeypath", building=annex)
    changes, items = _read(tmp_path / "annex-3.storeypath")
    assert desk.id not in items and changes["moved_away"] == [{"id": desk.id, "building_id": hq}]
    assert desk.id not in changes["retired"]


def test_an_item_carried_back_before_its_new_building_is_exported_is_listed_again(campus, tmp_path):
    ws, _, hq, annex = campus
    in_hq = _ground(ws, hq)
    desk = ws.add_item("DESK-JUNIOR", *in_hq)
    export_package(ws, tmp_path / "hq-1.storeypath", building=hq)
    _carry(desk, _ground(ws, annex))
    export_package(ws, tmp_path / "hq-2.storeypath", building=hq)
    assert _read(tmp_path / "hq-2.storeypath")[0]["moved_away"] == [{"id": desk.id, "building_id": annex}]
    _carry(desk, in_hq)  # back, exactly where it was: the ANNEX not exported meanwhile
    export_package(ws, tmp_path / "hq-3.storeypath", building=hq)
    changes, items = _read(tmp_path / "hq-3.storeypath")
    assert desk.id in items and desk.id in changes["changed"]
    export_package(ws, tmp_path / "annex.storeypath", building=annex)
    changes, items = _read(tmp_path / "annex.storeypath")
    assert desk.id not in items and changes["moved_away"] == [] and desk.id not in changes["all_retired"]


def test_an_item_retired_after_it_was_carried_away_is_retired_by_the_building_that_held_it(campus, tmp_path):
    # Carried to the ANNEX and taken away there before either was exported: the HQ
    # (whose last package held it) retires it, in retired and all_retired alike; the
    # ANNEX, which never held it, says nothing of it.
    ws, _, hq, annex = campus
    desk = ws.add_item("DESK-JUNIOR", *_ground(ws, hq))
    export_package(ws, tmp_path / "hq-1.storeypath", building=hq)
    _carry(desk, _ground(ws, annex))
    desk.status = "retired"
    export_package(ws, tmp_path / "hq-2.storeypath", building=hq)
    changes, _ = _read(tmp_path / "hq-2.storeypath")
    assert changes["retired"] == [desk.id] and desk.id in changes["all_retired"] and changes["moved_away"] == []
    export_package(ws, tmp_path / "annex.storeypath", building=annex)
    changes, _ = _read(tmp_path / "annex.storeypath")
    assert desk.id not in changes["retired"] and desk.id not in changes["all_retired"]
    export_package(ws, tmp_path / "hq-3.storeypath", building=hq)
    changes, _ = _read(tmp_path / "hq-3.storeypath")
    assert changes["retired"] == [] and desk.id in changes["all_retired"]  # said once, kept for good


def test_an_item_never_exported_and_retired_is_retired_where_it_stood(campus, tmp_path):
    ws, _, hq, annex = campus
    chair = ws.add_item("DESK-JUNIOR", *_ground(ws, hq))
    chair.status = "retired"
    export_package(ws, tmp_path / "hq.storeypath", building=hq)
    export_package(ws, tmp_path / "annex.storeypath", building=annex)
    assert chair.id in _read(tmp_path / "hq.storeypath")[0]["all_retired"]
    assert chair.id not in _read(tmp_path / "annex.storeypath")[0]["all_retired"]
    chair.status = "active"  # brought back: no longer retired
    export_package(ws, tmp_path / "hq-2.storeypath", building=hq)
    changes, items = _read(tmp_path / "hq-2.storeypath")
    assert chair.id in items and chair.id not in changes["all_retired"]


def test_previous_sequence_is_the_last_export_of_the_building(campus, tmp_path):
    # Export numbers run on across the project's packages; a building's previous is
    # the last that held it, so a reader can tell it missed one of that building's.
    ws, _, hq, annex = campus
    assert [e.sequence for e in ws.exports] == [1, 2]  # HQ, then ANNEX
    m = export_package(ws, tmp_path / "hq.storeypath", building=hq)
    changes, _ = _read(tmp_path / "hq.storeypath")
    assert (m.export.sequence, m.export.previous_sequence) == (3, 1)
    assert (changes["sequence"], changes["previous_sequence"]) == (3, 1)
    m = export_package(ws, tmp_path / "annex.storeypath", building=annex)
    assert (m.export.sequence, m.export.previous_sequence) == (4, 2)
    assert export_package(ws, tmp_path / "hq-2.storeypath", building=hq).export.previous_sequence == 3


def test_a_buildings_first_package_has_no_previous(campus, tmp_path):
    ws, _, hq, _ = campus
    loc = ws.locations[0]
    b_id = ws.add_building(f"{ws.id}-{loc.code}", "NEW", "New wing")
    ws.add_floor(b_id, 0)
    m = export_package(ws, tmp_path / "new.storeypath", building=b_id)
    changes, _ = _read(tmp_path / "new.storeypath")
    assert m.export.previous_sequence is None and changes["previous_sequence"] is None
    assert changes["sequence"] == 3 and b_id in changes["added"]


def test_a_workspace_of_an_earlier_studio_goes_on_from_what_it_kept(campus, tmp_path):
    # A record written before each building's last package was kept: the whole
    # project as last exported, and where each item was. Its next export follows on.
    from storeypath.export import compared

    ws, _, hq, annex = campus
    desk = ws.add_item("DESK-JUNIOR", *_ground(ws, hq))
    export_package(ws, tmp_path / "hq-1.storeypath", building=hq)
    last = ws.exports[-1]
    last.objects, last.places = compared(ws, None), {desk.id: hq}
    last.held = last.items_held = None
    _carry(desk, _ground(ws, annex))
    export_package(ws, tmp_path / "hq-2.storeypath", building=hq)
    changes, _ = _read(tmp_path / "hq-2.storeypath")
    assert changes["previous_sequence"] == 3 and changes["moved_away"] == [{"id": desk.id, "building_id": annex}]
    assert changes["added"] == changes["retired"] == []
    export_package(ws, tmp_path / "annex.storeypath", building=annex)
    changes, _ = _read(tmp_path / "annex.storeypath")
    assert changes["previous_sequence"] == 2 and desk.id in changes["changed"]


def _without_next_item(source, out):
    """A copy of a package as a Studio before format 0.7's next item number wrote it."""
    with zipfile.ZipFile(source) as z, zipfile.ZipFile(out, "w") as w:
        for info in z.infolist():
            data = z.read(info)
            if info.filename == "manifest.json":
                m = json.loads(data)
                del m["export"]["next_item"]
                data = json.dumps(m).encode()
            w.writestr(info.filename, data)
    return out


def _opened(data, code):
    return Workspace.load(next((data / code).glob("*.spproj")))


def test_items_are_numbered_after_every_building_of_the_project(campus, tmp_path):
    # A Studio continuing one building's package numbers its new items after the
    # project's (the manifest's next_item), not after that building's alone: opening
    # the other building's package then adds its items beside them.
    from storeypath.bundle import open_file

    ws, _, hq, annex = campus
    ws.add_item("DESK-JUNIOR", *_ground(ws, hq))
    annex_desk = ws.add_item("DESK-SENIOR", *_ground(ws, annex))
    m = export_package(ws, tmp_path / "hq.storeypath", building=hq)
    export_package(ws, tmp_path / "annex.storeypath", building=annex)
    assert m.export.next_item == 3
    data = tmp_path / "B"
    open_file(data, tmp_path / "hq.storeypath")
    b = _opened(data, ws.id)
    f_id, x, y = _ground(b, hq)
    copier = b.add_item("COPIER", f_id, x + 1, y)
    assert copier.id == f"{ws.id}-I000003"
    b.save(next((data / ws.id).glob("*.spproj")))
    open_file(data, tmp_path / "annex.storeypath")
    b = _opened(data, ws.id)
    assert b.items[copier.id].type == "COPIER" and b.items[annex_desk.id].type == "DESK-SENIOR"


def test_items_moved_away_and_retired_are_counted_when_a_package_does_not_say(campus, tmp_path):
    from storeypath.bundle import open_file

    ws, _, hq, annex = campus
    desk = ws.add_item("DESK-JUNIOR", *_ground(ws, hq))
    tv = ws.add_item("TV", *_ground(ws, hq))
    export_package(ws, tmp_path / "hq-1.storeypath", building=hq)
    _carry(desk, _ground(ws, annex))
    tv.status = "retired"
    export_package(ws, tmp_path / "hq-2.storeypath", building=hq)
    old = _without_next_item(tmp_path / "hq-2.storeypath", tmp_path / "old.storeypath")
    assert validate_package(old) == []
    open_file(tmp_path / "B", old)  # it holds no item: one moved away, one retired
    assert _opened(tmp_path / "B", ws.id).next_item_seq == 3


def test_a_package_whose_item_has_the_id_of_another_item_here_is_refused(campus, tmp_path):
    # Two Studios numbering items apart (a package of a Studio before next_item): the
    # ANNEX's desk and the copier added here have one ID. Nothing is changed here.
    from storeypath.bundle import ItemClash, open_file

    ws, _, hq, annex = campus
    ws.add_item("DESK-JUNIOR", *_ground(ws, hq))
    ws.add_item("DESK-SENIOR", *_ground(ws, annex))
    export_package(ws, tmp_path / "hq.storeypath", building=hq)
    export_package(ws, tmp_path / "annex.storeypath", building=annex)
    data = tmp_path / "B"
    open_file(data, _without_next_item(tmp_path / "hq.storeypath", tmp_path / "old-hq.storeypath"))
    path = next((data / ws.id).glob("*.spproj"))
    b = Workspace.load(path)
    f_id, x, y = _ground(b, hq)
    copier = b.add_item("COPIER", f_id, x + 1, y)
    assert copier.id == f"{ws.id}-I000002"
    b.save(path)
    before = path.read_bytes()
    with pytest.raises(ItemClash, match="two items with one ID"):
        open_file(data, tmp_path / "annex.storeypath")
    assert path.read_bytes() == before


def test_a_value_json_cannot_hold_is_refused_and_nothing_is_written(campus, tmp_path):
    from storeypath.export import ExportError

    ws, _, hq, _ = campus
    ws.building(hq).floors[0].height = float("nan")
    before = len(ws.exports)
    for bake in (False, True):
        out = tmp_path / f"hq-{bake}.storeypath"
        with pytest.raises(ExportError, match="not a number"):
            export_package(ws, out, building=hq, bake=bake)
        assert not out.exists() and len(ws.exports) == before
