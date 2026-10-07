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


def _in_studio(ws, ws_path):
    """The campus where a Studio keeps a project: data/<code>/<code>.spproj."""
    data = ws_path.parent.parent
    folder = data / ws.id
    ws_path.parent.rename(folder)
    path = folder / f"{ws.id}.spproj"
    (folder / ws_path.name).rename(path)
    ws.save(path)
    return data, path


def _spaces(ws, f_id):
    return sorted((r for r in ws.floor_objects(f_id) if r.kind == "space"), key=lambda r: r.id)


def test_what_was_set_in_review_survives_a_package_opened_in_another_studio(campus, tmp_path):
    # How many a room seats, set in review (0 among them), comes back with the
    # package: the next export from there lists nothing changed.
    from storeypath.bundle import open_file
    from storeypath.workspace import Override

    ws, _, hq, _ = campus
    f0 = f"{hq}-F00"
    meeting, room = _spaces(ws, f0)[:2]
    ws.overrides[meeting.id] = Override(capacity=0)
    ws.overrides[room.id] = Override(capacity=12, hidden=True)
    export_package(ws, tmp_path / "hq.storeypath", building=hq)
    open_file(tmp_path / "B", tmp_path / "hq.storeypath")
    b = _opened(tmp_path / "B", ws.id)
    assert b.overrides[meeting.id].capacity == 0 and b.overrides[room.id].capacity == 12
    assert b.effective(b.objects[room.id])["hidden"]
    export_package(b, tmp_path / "again.storeypath", building=hq)
    changes, _ = _read(tmp_path / "again.storeypath")
    assert (changes["added"], changes["changed"], changes["retired"]) == ([], [], [])
    with zipfile.ZipFile(tmp_path / "again.storeypath") as z:
        spaces = {f["id"]: f["properties"] for f in json.loads(z.read("spaces.geojson"))["features"]}
    assert (spaces[meeting.id]["capacity"], spaces[meeting.id]["capacity_from"]) == (0, "review")


def test_what_a_package_says_and_this_studio_does_not_keep_is_listed_as_changed(campus, tmp_path):
    # The package's export is taken as the last one, as rebuilt here: what the rebuilt
    # project does not say as the package does is listed as changed next, not hidden.
    from storeypath.bundle import open_file

    ws, _, hq, _ = campus
    room = _spaces(ws, f"{hq}-F00")[0]
    export_package(ws, tmp_path / "hq.storeypath", building=hq)
    odd = tmp_path / "odd.storeypath"
    with zipfile.ZipFile(tmp_path / "hq.storeypath") as z, zipfile.ZipFile(odd, "w") as w:
        for info in z.infolist():
            data = z.read(info)
            if info.filename == "spaces.geojson":  # seats 5 by its desks, where there are none
                doc = json.loads(data)
                p = next(f["properties"] for f in doc["features"] if f["id"] == room.id)
                p["capacity"], p["capacity_from"] = 5, "items"
                data = json.dumps(doc).encode()
            w.writestr(info.filename, data)
    open_file(tmp_path / "B", odd)
    export_package(_opened(tmp_path / "B", ws.id), tmp_path / "again.storeypath", building=hq)
    changes, _ = _read(tmp_path / "again.storeypath")
    assert changes["changed"] == [room.id] and changes["added"] == changes["retired"] == []


def test_a_buildings_own_package_opened_into_its_project_keeps_what_review_made(campus, tmp_path):
    # Its corrections, what was accepted, the capacity set, what was drawn on its floors
    # and spotted in their drawings: all kept, saying what the package says; exported
    # at once, nothing has changed.
    from storeypath.bundle import open_file
    from storeypath.workspace import Override

    ws, ws_path, hq, _ = campus
    data, path = _in_studio(ws, ws_path)
    f0 = f"{hq}-F00"
    s = _spaces(ws, f0)
    ws.overrides[s[0].id] = Override(capacity=0)
    ws.overrides[s[1].id] = Override(name="Board room")
    ws.overrides[s[2].id] = Override()  # accepted as it is
    floor = ws.floor(f0)
    floor.edits.walls.append([[1.0, 1.0], [3.0, 1.0]])
    floor.edits.dividers.append([[5.0, 1.0], [5.0, 3.0]])
    floor.symbols, floor.symbols_key = [{"class": "door", "x": 1.0, "y": 2.0}], "spotted-in-this-drawing"
    p = shape(s[3].geometry).representative_point()
    ws.add_item("DESK-MANAGER", f0, p.x, p.y)
    export_package(ws, tmp_path / "hq.storeypath", building=hq)
    ws.save(path)

    assert open_file(data, tmp_path / "hq.storeypath", replace=True)["replaced"] == [hq]
    here = Workspace.load(path)
    assert here.overrides[s[0].id].capacity == 0
    assert here.effective(here.objects[s[1].id])["name"] == "Board room" and here.overrides[s[1].id].name == "Board room"
    assert s[2].id in here.overrides and here.review_reasons(here.objects[s[2].id]) == []
    floor = here.floor(f0)
    assert floor.source is not None and floor.edits.walls == [[[1.0, 1.0], [3.0, 1.0]]]
    assert floor.edits.dividers == [[[5.0, 1.0], [5.0, 3.0]]]
    assert floor.symbols_key == "spotted-in-this-drawing" and floor.symbols
    export_package(here, tmp_path / "hq-2.storeypath", building=hq)
    changes, _ = _read(tmp_path / "hq-2.storeypath")
    assert (changes["added"], changes["changed"], changes["retired"], changes["moved_away"]) == ([], [], [], [])
    assert changes["previous_sequence"] == 3


def test_an_earlier_package_put_in_place_of_its_building_gives_no_id_again(workspace, tmp_path):
    # Opened in place of the building, an earlier package does not take its counter
    # back: the rooms a later reading gave IDs to (published since) are retired, not
    # forgotten, and no ID is issued again.
    from storeypath.bundle import open_file
    from storeypath.convert import convert_floor
    from storeypath.samples import office_floor, write_floor_dxf
    from storeypath.workspace import SourceDrawing
    from test_reimport import _renovate

    ws, d, f_id, b_id, _ = workspace
    convert_floor(ws, f_id, d)
    export_package(ws, tmp_path / "v1.storeypath", building=b_id, bake=False)
    write_floor_dxf(d / "level-2r.dxf", _renovate(office_floor(2)))
    ws.floor(f_id).source = SourceDrawing(path="level-2r.dxf")
    issued = sorted(convert_floor(ws, f_id, d).added)
    assert issued
    export_package(ws, tmp_path / "v2.storeypath", building=b_id, bake=False)
    counter = ws.building(b_id).next_object_seq
    data = tmp_path / "data"
    (data / ws.id).mkdir(parents=True)
    path = data / ws.id / f"{ws.id}.spproj"
    ws.save(path)

    open_file(data, tmp_path / "v1.storeypath", replace=True)
    here = Workspace.load(path)
    assert here.building(b_id).next_object_seq == counter
    assert all(here.objects[i].status == "retired" for i in issued)
    given = {i for i in here.objects}
    assert here.allocate_object_code(b_id) == f"{counter:04d}" and f"{f_id}-{counter:04d}" not in given
    export_package(here, tmp_path / "v3.storeypath", building=b_id, bake=False)
    changes, _ = _read(tmp_path / "v3.storeypath")
    assert set(issued) <= set(changes["retired"]) and set(issued) <= set(changes["all_retired"])


def test_packages_opened_in_any_order_list_nothing_changed(campus, tmp_path):
    # A Studio with the project as it was at its second export opens the next two
    # packages, the later first (and one of them twice): each building is next
    # compared with its own package, and exported at once lists nothing changed.
    from storeypath.bundle import export_project, open_file

    ws, ws_path, hq, annex = campus
    for b in (hq, annex):
        f0 = f"{b}-F00"
        for r in _spaces(ws, f0)[:4]:
            p = shape(r.geometry).representative_point()
            ws.add_item("DESK-SENIOR", f0, p.x, p.y, rotation=33.3)
    ws.save(ws_path)
    export_project(ws_path, tmp_path / "p.storeypath-project")
    data = tmp_path / "B"
    open_file(data, tmp_path / "p.storeypath-project")
    export_package(ws, tmp_path / "hq-3.storeypath", building=hq)
    export_package(ws, tmp_path / "annex-4.storeypath", building=annex)

    open_file(data, tmp_path / "annex-4.storeypath", replace=True)
    open_file(data, tmp_path / "hq-3.storeypath", replace=True)
    open_file(data, tmp_path / "hq-3.storeypath", replace=True)  # again
    b = _opened(data, ws.id)
    assert b.exports[-1].sequence == 4
    for building, previous in ((hq, 3), (annex, 4)):
        out = tmp_path / f"b-{building}.storeypath"
        export_package(b, out, building=building)
        changes, _ = _read(out)
        assert changes["previous_sequence"] == previous
        assert (changes["added"], changes["changed"], changes["retired"], changes["moved_away"]) == ([], [], [], [])


def test_a_buildings_package_opened_as_a_new_project_exports_as_it_was(campus, tmp_path):
    from storeypath.bundle import open_file

    ws, _, hq, annex = campus
    for b in (hq, annex):
        ws.add_item("DESK-SENIOR", *_ground(ws, b), rotation=33.3)
    export_package(ws, tmp_path / "hq.storeypath", building=hq)
    open_file(tmp_path / "C", tmp_path / "hq.storeypath")
    export_package(_opened(tmp_path / "C", ws.id), tmp_path / "c.storeypath", building=hq)
    changes, _ = _read(tmp_path / "c.storeypath")
    assert changes["previous_sequence"] == 3
    assert (changes["added"], changes["changed"], changes["retired"], changes["moved_away"]) == ([], [], [], [])


def test_a_building_of_a_site_stays_on_it_when_its_package_is_opened(campus, tmp_path):
    # A site on the map, its buildings standing as drawn: the ANNEX's own package
    # opened in its place keeps it on the site, and no building moves.
    from shapely.geometry import box, mapping

    from storeypath.bundle import open_file
    from storeypath.export import placements
    from storeypath.workspace import Placement

    ws, ws_path, hq, annex = campus
    data, path = _in_studio(ws, ws_path)
    loc = ws.locations[0]
    for b in loc.buildings:
        b.placement = None
    loc.placement = Placement(lon=46.6761, lat=24.7127, bearing=30.0)
    ws.floor(f"{annex}-F01").outline = mapping(box(0, 0, 300, 200))  # the ANNEX reaches further than the HQ
    export_package(ws, tmp_path / "annex.storeypath", building=annex)
    ws.save(path)
    before = placements(ws)
    open_file(data, tmp_path / "annex.storeypath", replace=True)
    here = Workspace.load(path)
    after = placements(here)
    assert here.building(annex).placement is None and here.building(annex).site is not None
    for b in (hq, annex):
        p, q = before[b][0], after[b][0]
        assert (p.lon, p.lat) == (q.lon, q.lat) and abs(p.bearing - q.bearing) < 1e-6
        assert abs(p.x - q.x) < 1e-3 and abs(p.y - q.y) < 1e-3
    export_package(here, tmp_path / "annex-2.storeypath", building=annex)
    assert annex not in _read(tmp_path / "annex-2.storeypath")[0]["changed"]


def _damaged(source, out):
    """A copy of a project file with one drawing's bytes damaged (its CRC no longer agrees)."""
    with zipfile.ZipFile(source) as z, zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as w:
        for info in z.infolist():
            w.writestr(info.filename, z.read(info.filename))
    raw = bytearray(out.read_bytes())
    with zipfile.ZipFile(out) as z:
        info = next(i for i in z.infolist() if i.filename.startswith("studio/drawings/") and i.filename.endswith(".dxf"))
    at = info.header_offset + 30 + len(info.filename) + 2000
    raw[at:at + 7] = b"XXXXXXX"
    out.write_bytes(bytes(raw))
    return out


def _projects(data):
    """What a data folder holds but its catalogue of item types: projects, and anything left over."""
    return sorted(p.name for p in data.iterdir() if p.name != "catalogue.json")


def _tree(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_a_damaged_project_file_leaves_the_project_as_it_was(campus, tmp_path):
    from storeypath.bundle import export_project, open_file

    ws, ws_path, _, _ = campus
    data, path = _in_studio(ws, ws_path)
    export_project(path, tmp_path / "p.storeypath-project")
    bad = _damaged(tmp_path / "p.storeypath-project", tmp_path / "bad.storeypath-project")
    before = _tree(data)
    with pytest.raises(zipfile.BadZipFile):
        open_file(data, bad, replace=True)
    assert _tree(data) == before and _projects(data) == [ws.id]
    open_file(data, tmp_path / "p.storeypath-project", replace=True)  # a whole one is put in its place
    assert _projects(data) == [ws.id] and Workspace.load(path).id == ws.id
    assert len(list((data / ws.id / "drawings").glob("*.dxf"))) == 5


def test_a_project_kept_elsewhere_in_the_data_folder_is_the_one_opened_into(campus, tmp_path):
    # Not in data/<code>/: a folder of another name, as the Studio finds it.
    from storeypath.bundle import ProjectExists, export_project, open_file

    ws, ws_path, hq, _ = campus
    data = ws_path.parent.parent
    ws.save(ws_path)
    export_package(ws, tmp_path / "hq.storeypath", building=hq)
    export_project(ws_path, tmp_path / "p.storeypath-project")
    with pytest.raises(ProjectExists):
        open_file(data, tmp_path / "hq.storeypath")
    assert open_file(data, tmp_path / "hq.storeypath", replace=True)["replaced"] == [hq]
    with pytest.raises(ProjectExists):
        open_file(data, tmp_path / "p.storeypath-project")
    open_file(data, tmp_path / "p.storeypath-project", replace=True)
    assert _projects(data) == ["demo"]  # no second copy
    assert Workspace.load(data / "demo" / f"{ws.id}.spproj").id == ws.id


def test_a_project_kept_as_a_file_of_the_data_folder_is_replaced_not_doubled(campus, tmp_path):
    from storeypath.bundle import ProjectExists, export_project, open_file

    ws, ws_path, _, _ = campus
    export_project(ws_path, tmp_path / "p.storeypath-project")
    data = tmp_path / "loose"
    data.mkdir()
    ws.save(data / "mine.spproj")
    with pytest.raises(ProjectExists):
        open_file(data, tmp_path / "p.storeypath-project")
    open_file(data, tmp_path / "p.storeypath-project", replace=True)
    assert _projects(data) == [ws.id]


def _crafted(out, ws):
    from storeypath.bundle import PROJECT_MANIFEST, WORKSPACE_FILE

    with zipfile.ZipFile(out, "w") as z:
        z.writestr(PROJECT_MANIFEST, json.dumps({"format": "storeypath-project",
                                                 "project": {"id": ws.id, "name": ws.project.name}}))
        z.writestr(WORKSPACE_FILE, ws.model_dump_json())
    return out


def test_a_project_file_reads_drawings_of_the_project_alone(tmp_path):
    # Its floors' drawing paths (absolute, or leaving the project) become the project's
    # drawings of those names: sending the project on never ships a file from elsewhere.
    from storeypath.bundle import export_project, open_file
    from storeypath.workspace import SourceDrawing

    secret = tmp_path / "outside" / "secret-notes.txt"
    secret.parent.mkdir()
    secret.write_text("private: not part of any project\n")
    ws = Workspace.new("Innocent project")
    loc = ws.add_location("SITE", "Site")
    b = ws.add_building(loc, "HQ", "HQ")
    ws.add_floor(b, 0, source=SourceDrawing(path=str(secret)))
    ws.add_floor(b, 1, source=SourceDrawing(path="../../outside/secret-notes.txt", profile="../../outside/p.yaml"))
    ws.add_floor(b, 2, source=SourceDrawing(path="C:\\plans\\level-2.dxf"))
    data = tmp_path / "data"
    open_file(data, _crafted(tmp_path / "crafted.storeypath-project", ws))
    opened = next((data / ws.id).glob("*.spproj"))
    floors = [f for *_, f, _ in Workspace.load(opened).iter_floors()]
    assert [f.source.path for f in floors] == ["drawings/secret-notes.txt"] * 2 + ["drawings/level-2.dxf"]
    assert floors[1].source.profile == "auto"
    export_project(opened, tmp_path / "sent.storeypath-project")
    with zipfile.ZipFile(tmp_path / "sent.storeypath-project") as z:
        assert not [n for n in z.namelist() if n.startswith("studio/drawings/")]

    nameless = Workspace.new("Nameless")
    nameless.add_floor(nameless.add_building(nameless.add_location("SITE", "Site"), "HQ", "HQ"), 0,
                       source=SourceDrawing(path="../.."))
    with pytest.raises(ValueError, match="not a file of the project"):
        open_file(tmp_path / "data2", _crafted(tmp_path / "nameless.storeypath-project", nameless))


def test_a_project_sent_ships_no_file_from_outside_its_folder(campus, tmp_path):
    from storeypath.bundle import export_project
    from storeypath.workspace import SourceDrawing

    ws, ws_path, hq, _ = campus
    secret = tmp_path / "secret.dxf"
    secret.write_text("0\nEOF\n")
    ws.floor(f"{hq}-F00").source = SourceDrawing(path=str(secret))
    ws.floor(f"{hq}-F01").source = SourceDrawing(path="../../secret.dxf")
    ws.save(ws_path)
    export_project(ws_path, tmp_path / "sent.storeypath-project")
    with zipfile.ZipFile(tmp_path / "sent.storeypath-project") as z:
        shipped = [n for n in z.namelist() if n.startswith("studio/drawings/")]
        sent = Workspace.model_validate_json(z.read("studio/project.spproj"))
    assert shipped and all(z_name.split("/")[-1].startswith(("hq-", "annex-")) for z_name in shipped)
    assert sent.floor(f"{hq}-F00").source.path == sent.floor(f"{hq}-F01").source.path == "drawings/secret.dxf"


def test_a_building_of_a_placed_site_does_not_move_when_another_changes(campus, tmp_path):
    # The site placed on the map, its buildings standing as drawn: the ANNEX given a
    # wider floor, and a new building added, move nothing else on the map.
    from shapely.geometry import box, mapping

    from storeypath.export import placements
    from storeypath.server import Studio
    from test_review import NoModel

    ws, ws_path, hq, annex = campus
    data, path = _in_studio(ws, ws_path)
    loc = ws.locations[0]
    for b in loc.buildings:
        b.placement = None
    ws.save(path)
    Studio(data, model=NoModel()).place_site(ws.id, f"{ws.id}-{loc.code}", {"lat": 24.7127, "lon": 46.6761})
    ws = Workspace.load(path)
    export_package(ws, tmp_path / "hq-1.storeypath", building=hq)
    before = placements(ws)[hq][0]
    ws.floor(ws.add_floor(annex, 5)).outline = mapping(box(0, 0, 400, 300))
    new = ws.add_building(f"{ws.id}-{loc.code}", "WING", "Wing")
    ws.floor(ws.add_floor(new, 0)).outline = mapping(box(-500, -500, -300, -200))
    assert placements(ws)[hq][0] == before
    export_package(ws, tmp_path / "hq-2.storeypath", building=hq)
    assert _read(tmp_path / "hq-2.storeypath")[0]["changed"] == []


def test_a_building_added_to_a_placed_site_moves_no_other(campus):
    # A site placed by an earlier Studio, never settled: a building added to it.
    from shapely.geometry import box, mapping

    from storeypath.export import placements
    from storeypath.workspace import Placement

    ws, _, hq, annex = campus
    loc = ws.locations[0]
    for b in loc.buildings:
        b.placement = None
    loc.placement = Placement(lon=46.6761, lat=24.7127)
    before = placements(ws)
    new = ws.add_building(f"{ws.id}-{loc.code}", "WING", "Wing")
    ws.floor(ws.add_floor(new, 0)).outline = mapping(box(-500, -500, -300, -200))
    after = placements(ws)
    assert after[hq] == before[hq] and after[annex] == before[annex]


def test_a_project_saved_as_new_gives_its_items_its_own_ids(campus, tmp_path):
    ws, _, hq, _ = campus
    desk = ws.add_item("DESK-MANAGER", *_ground(ws, hq))
    gone = ws.add_item("TV", *_ground(ws, hq))
    gone.status = "retired"
    copy = ws.save_as_new_project(tmp_path / "copy.spproj", "Copy")
    number = desk.id.split("-")[1]
    assert set(copy.items) == {f"{copy.id}-{number}", f"{copy.id}-{gone.id.split('-')[1]}"}
    moved = copy.items[f"{copy.id}-{number}"]
    assert moved.id == f"{copy.id}-{number}" and moved.floor_id == f"{copy.id}-DEMO-HQ-F00"
    assert copy.next_item_seq == ws.next_item_seq and copy.readings == ws.readings
    export_package(copy, tmp_path / "copy-hq.storeypath", building=f"{copy.id}-DEMO-HQ")
    changes, items = _read(tmp_path / "copy-hq.storeypath")
    assert items == {moved.id} and moved.id in changes["added"]
    assert f"{copy.id}-{gone.id.split('-')[1]}" in changes["all_retired"]
    assert Workspace.load(tmp_path / "copy.spproj").items == copy.items


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
