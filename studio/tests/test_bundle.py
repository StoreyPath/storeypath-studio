"""Projects sent as one file (a project file, for another Studio), and projects
rebuilt from packages: a building at a time, into the project when it is here."""

import json
import zipfile

import pytest
import shapely
import shapely.geometry

from storeypath.assets import asset_dir
from storeypath.bundle import ProjectExists, export_project, open_file
from storeypath.export import export_package
from storeypath.validate import validate_package
from storeypath.workspace import Workspace

PACKAGES = asset_dir("spec") / "conformance" / "packages"


@pytest.fixture
def review(converted):
    ws, d, f_id, *_ = converted
    path = d / "project.spproj"
    ws.save(path)
    return None, path, f_id


def _features(path):
    with zipfile.ZipFile(path) as z:
        m = json.loads(z.read("manifest.json"))
        return {role: {f["id"]: f for f in json.loads(z.read(m["files"][role]))["features"]}
                for role in ("location", "buildings", "floors", "spaces", "zones", "openings")}


def _same(again, before):
    """The same features, as a package keeps them: positions to the 7th decimal of a
    degree (about a centimetre), so areas within a centimetre along their sides."""
    from shapely.geometry import shape

    assert {r: set(fs) for r, fs in again.items()} == {r: set(fs) for r, fs in before.items()}
    for role, fs in before.items():
        for i, f in fs.items():
            g = again[role][i]
            for k, v in f["properties"].items():
                w = g["properties"][k]
                if k == "area_m2":
                    assert abs(w - v) <= 0.012 * 4 * v ** 0.5 + 0.01, (i, k, v, w)  # a cm along its sides
                elif k == "display_point" and v and role in ("spaces", "zones"):
                    # any good spot: a long even corridor has many, and a centimetre picks another
                    from shapely.geometry import Point
                    assert shape(g["geometry"]).contains(Point(w)), (i, k, w)
                elif k == "display_point" and v:
                    assert max(abs(a - b) for a, b in zip(v, w)) <= 2e-7, (i, k, v, w)
                elif k in ("walls", "parapets") and v:
                    assert shape(v).symmetric_difference(shape(w)).area < 1e-12, (i, k)
                else:
                    assert w == v, (i, k, v, w)
            if role in ("location", "buildings") and f["geometry"]:  # worked out from the floors
                assert shape(g["geometry"]).symmetric_difference(shape(f["geometry"])).area < 1e-9, i
            elif f["geometry"]:  # as written, a point the rounding repeated written once (older packages)
                once = json.loads(json.dumps(shapely.geometry.mapping(shapely.remove_repeated_points(shape(f["geometry"])))))
                assert g["geometry"] in (f["geometry"], once), i
            else:
                assert g["geometry"] == f["geometry"], i


def _of(features, building):
    """Of a project's features, those a package of one building holds."""
    location = building.rsplit("-", 1)[0]
    return {role: {i: f for i, f in fs.items() if (i == location if role == "location"
                                                    else i == building or i.startswith(building + "-"))}
            for role, fs in features.items()}


def _changes(path):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("changes.json"))


def test_a_package_opens_as_a_project_and_exports_as_it_was(tmp_path):
    # A package of an earlier format, of the whole campus: rebuilt, then exported a
    # building at a time (one per package), each with nothing changed but its items'
    # IDs. Numbered by the project then (format 0.6: EWBSSN-I000001), its items are
    # given asset IDs made from those (the same in any Studio that opens it): listed as
    # added, and the IDs of then as retired.
    from storeypath.ids import is_item_id, item_id_for_legacy

    source = PACKAGES / "campus.storeypath"
    opened = open_file(tmp_path, source)
    assert opened["how"] == "package" and opened["code"] == "EWBSSN" and opened["floors"] == 5 and opened["drawings"] == 0
    ws = Workspace.load(tmp_path / "EWBSSN" / "EWBSSN.spproj")
    before = _features(source)
    assert {r.id for r in ws.objects.values() if r.status == "active"} == \
        set(before["spaces"]) | set(before["zones"]) | set(before["openings"])
    hq = ws.building("EWBSSN-DEMO-HQ")
    assert hq.placement is not None and len(hq.floors) == 3 and all(f.source is None for f in hq.floors)
    with zipfile.ZipFile(source) as z:
        then = {f["id"]: f["properties"] for f in json.loads(z.read("items.geojson"))["features"]}
    assert then and set(ws.items) == {item_id_for_legacy(i) for i in then} and all(map(is_item_id, ws.items))
    for i, p in then.items():
        it = ws.items[item_id_for_legacy(i)]
        assert (it.type, it.floor_id, it.status) == (p["type"], p["floor_id"], "active")
    assert set(Workspace.load(tmp_path / "EWBSSN" / "EWBSSN.spproj").items) == set(ws.items)
    open_file(tmp_path / "other", source)
    assert set(Workspace.load(tmp_path / "other" / "EWBSSN" / "EWBSSN.spproj").items) == set(ws.items)

    previous = _changes(source)["sequence"]
    for n, b in enumerate(("EWBSSN-DEMO-HQ", "EWBSSN-DEMO-ANNEX"), 1):
        out = tmp_path / f"{b}.storeypath"
        export_package(ws, out, building=b)
        assert validate_package(out) == []
        changes = _changes(out)
        # the last package that held the building: the campus's, for both
        assert changes["sequence"] == previous + n and changes["previous_sequence"] == previous
        its = sorted(i for i, p in then.items() if p["building_id"] == b)
        assert changes["added"] == sorted(item_id_for_legacy(i) for i in its) and changes["retired"] == its
        assert (changes["changed"], changes["moved_away"]) == ([], []) and set(its) <= set(changes["all_retired"])
        _same(_features(out), _of(before, b))
    # and the next lists nothing (the items by their new IDs now)
    export_package(ws, tmp_path / "hq-again.storeypath", building="EWBSSN-DEMO-HQ")
    changes = _changes(tmp_path / "hq-again.storeypath")
    assert (changes["added"], changes["changed"], changes["retired"], changes["moved_away"]) == ([], [], [], [])


def test_an_unplaced_building_stays_unplaced(tmp_path):
    source = PACKAGES / "unplaced.storeypath"
    open_file(tmp_path, source)
    code = json.loads(zipfile.ZipFile(source).read("manifest.json"))["project"]["id"]
    ws = Workspace.load(tmp_path / code / f"{code}.spproj")
    assert all(b.placement is None for loc in ws.locations for b in loc.buildings)
    out = tmp_path / "again.storeypath"
    export_package(ws, out)
    _same(_features(out), _features(source))
    assert not any(p["placed"] for p in json.loads(zipfile.ZipFile(out).read("manifest.json"))["placements"].values())


def test_new_ids_follow_every_id_given(tmp_path):
    open_file(tmp_path, PACKAGES / "simple-office.storeypath")
    code = json.loads(zipfile.ZipFile(PACKAGES / "simple-office.storeypath").read("manifest.json"))["project"]["id"]
    ws = Workspace.load(tmp_path / code / f"{code}.spproj")
    b_id = f"{code}-MAIN-HQ"
    given = max(int(i.rsplit("-", 1)[1]) for i in ws.objects if i.startswith(b_id + "-F"))
    assert ws.building(b_id).next_object_seq == given + 1
    assert ws.allocate_object_code(b_id) == f"{given + 1:04d}"


def test_a_project_file_of_a_studio_before_asset_ids_opens_with_its_items_tagged(review, tmp_path):
    # Its items numbered by the project then (format 0.7): given asset IDs made from
    # those, the one retired left out (its ID is never in use again).
    from storeypath.bundle import read_file
    from storeypath.ids import is_item_id, item_id_for_legacy

    _, path, f_id = review
    ws = Workspace.load(path)
    desk = ws.add_item("DESK-MANAGER", f_id, 3.0, 3.0)
    gone = ws.add_item("TV", f_id, 4.0, 4.0)
    gone.status = "retired"
    then = {desk.id: f"{ws.id}-I000001", gone.id: f"{ws.id}-I000002"}
    ws.items = {then[i]: it.model_copy(update={"id": then[i]}) for i, it in ws.items.items()}
    ws.save(path)
    export_project(path, tmp_path / "p.storeypath-project")
    incoming = read_file(tmp_path / "p.storeypath-project")
    tag = item_id_for_legacy(f"{ws.id}-I000001")
    assert is_item_id(tag) and list(incoming.ws.items) == [tag]
    assert incoming.ws.items[tag].model_dump(exclude={"id"}) == desk.model_dump(exclude={"id"})


def test_a_project_travels_whole_and_continues(review, tmp_path):
    # Exported with its project, the file opens as the same project: its corrections,
    # edits, export history and drawings; it reads its drawings again.
    from storeypath.convert import convert_floor
    from storeypath.workspace import Override

    r, path, f_id = review
    ws = Workspace.load(path)
    space = next(o for o in ws.floor_objects(f_id) if o.kind == "space")
    ws.overrides[space.id] = Override(name="Board room")
    ws.floor(f_id).edits.walls.append([[0.0, 0.0], [1.0, 0.0]])
    export_package(ws, path.parent / "exports" / "first.storeypath")  # one export, recorded
    ws.save(path)
    sent = tmp_path / "sent.storeypath-project"
    export_project(path, sent)
    assert validate_package(sent) == ["missing file manifest.json"]  # not a package: no other system reads it
    with zipfile.ZipFile(sent) as z:
        names = set(z.namelist())
        about = json.loads(z.read("project.json"))
    assert {"studio/project.spproj", "catalogue.json"} <= names and "manifest.json" not in names
    assert about["format"] == "storeypath-project" and about["project"] == {"id": ws.id, "name": ws.project.name}

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    opened = open_file(elsewhere, sent)
    code = ws.project.code
    assert opened == {"code": code, "name": ws.project.name, "how": "project", "floors": 1,
                      "drawings": sum(1 for n in names if n.startswith("studio/drawings/")), "item_types_added": [],
                      "item_types_not_added": []}
    there = Workspace.load(elsewhere / code / f"{code}.spproj")
    assert there.overrides[space.id].name == "Board room" and there.floor(f_id).edits.walls == [[[0.0, 0.0], [1.0, 0.0]]]
    assert [e.sequence for e in there.exports] == [1]
    report = convert_floor(there, f_id, elsewhere / code)  # its drawing came with it
    assert report.summary()

    with pytest.raises(ProjectExists):
        open_file(elsewhere, sent)
    there.project.name = "changed here"
    there.save(elsewhere / code / f"{code}.spproj")
    open_file(elsewhere, sent, replace=True)
    assert Workspace.load(elsewhere / code / f"{code}.spproj").project.name == ws.project.name


def test_what_is_not_a_package_is_refused(tmp_path):
    bad = tmp_path / "bad.storeypath"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("manifest.json", "{}")
    with pytest.raises(ValueError):
        open_file(tmp_path, bad)


def test_a_buildings_package_opens_into_its_project(tmp_path):
    # The campus's packages, a building each, opened one after the other: one project
    # with both, its items where they stand in each building. A building's later
    # package is put in place of that building only when asked; its floors keep the
    # drawings they had here; the items it retired are retired, one it says moved
    # away waits where it was until its new building's package comes.
    from storeypath.workspace import SourceDrawing

    data = tmp_path / "data"
    data.mkdir()
    first = open_file(data, PACKAGES / "campus-hq.storeypath")
    second = open_file(data, PACKAGES / "campus-annex.storeypath")
    assert first["how"] == "package" and second["how"] == "building" and second["replaced"] == []
    code = first["code"]
    ws_path = data / code / f"{code}.spproj"
    ws = Workspace.load(ws_path)
    hq, annex = (f"{code}-DEMO-{b}" for b in ("HQ", "ANNEX"))
    assert [b.code for loc in ws.locations for b in loc.buildings] == ["HQ", "ANNEX"]
    with zipfile.ZipFile(PACKAGES / "campus-hq.storeypath") as z:
        placed = {f["id"]: f["properties"]["local"] for f in json.loads(z.read("items.geojson"))["features"]}
    assert all((ws.items[i].x, ws.items[i].y, ws.items[i].rotation) == (p["x_m"], p["y_m"], p["rotation_deg"])
               for i, p in placed.items())
    with zipfile.ZipFile(PACKAGES / "campus-annex.storeypath") as z:
        held = len(placed) + len(json.loads(z.read("items.geojson"))["features"])
    assert len([i for i in ws.items.values() if i.status == "active"]) == held  # the two buildings' items
    ws.building(hq).floors[0].source = SourceDrawing(path="drawings/hq-ground.dxf")
    ws.save(ws_path)

    with zipfile.ZipFile(PACKAGES / "campus-hq-2.storeypath") as z:
        changes = json.loads(z.read("changes.json"))
    (gone,), (away,) = changes["retired"], changes["moved_away"]
    with pytest.raises(ProjectExists) as e:
        open_file(data, PACKAGES / "campus-hq-2.storeypath")
    assert e.value.building == hq
    assert open_file(data, PACKAGES / "campus-hq-2.storeypath", replace=True)["replaced"] == [hq]
    ws = Workspace.load(ws_path)
    assert ws.building(hq).floors[0].source.path == "drawings/hq-ground.dxf"  # it can be read again
    assert ws.items[gone].status == "retired" and ws.items[away["id"]].floor_id.startswith(hq + "-")
    assert ws.building(annex).floors and ws.exports[-1].sequence == 3
    open_file(data, PACKAGES / "campus-annex-2.storeypath", replace=True)
    ws = Workspace.load(ws_path)
    assert ws.items[away["id"]].floor_id.startswith(annex + "-") and ws.exports[-1].sequence == 4
