"""Projects sent as one file: a package that carries the project, and a project
rebuilt from a package."""

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


def _changes(path):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("changes.json"))


def test_a_package_opens_as_a_project_and_exports_as_it_was(tmp_path):
    source = PACKAGES / "campus.storeypath"
    opened = open_file(tmp_path, source)
    assert opened["how"] == "package" and opened["code"] == "EWBSSN" and opened["floors"] == 5 and opened["drawings"] == 0
    ws = Workspace.load(tmp_path / "EWBSSN" / "EWBSSN.spproj")
    before = _features(source)
    assert {r.id for r in ws.objects.values() if r.status == "active"} == \
        set(before["spaces"]) | set(before["zones"]) | set(before["openings"])
    hq = ws.building("EWBSSN-DEMO-HQ")
    assert hq.placement is not None and len(hq.floors) == 3 and all(f.source is None for f in hq.floors)

    out = tmp_path / "again.storeypath"
    export_package(ws, out)
    assert validate_package(out) == []
    changes = _changes(out)
    previous = _changes(source)["sequence"]
    assert changes["sequence"] == previous + 1 and changes["previous_sequence"] == previous
    assert (changes["added"], changes["changed"], changes["retired"]) == ([], [], [])  # nothing was changed
    _same(_features(out), before)


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
    sent = tmp_path / "sent.storeypath"
    export_project(path, sent)
    assert validate_package(sent) == []  # still a package any system reads
    with zipfile.ZipFile(sent) as z:
        names = set(z.namelist())
        assert "studio/project.spproj" in names and json.loads(z.read("manifest.json"))["files"]["studio"] == "studio/"

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    opened = open_file(elsewhere, sent)
    code = ws.project.code
    assert opened == {"code": code, "name": ws.project.name, "how": "project", "floors": 1,
                      "drawings": sum(1 for n in names if n.startswith("studio/drawings/")), "item_types_added": []}
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
