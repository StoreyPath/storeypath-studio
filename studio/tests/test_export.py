import csv
import io
import json
import zipfile

import pytest

from storeypath.convert import convert_floor
from storeypath.export import ExportError, export_package
from storeypath.georef import Georeferencer
from storeypath.samples import office_floor, write_floor_dxf
from storeypath.validate import validate_package
from storeypath.workspace import Override, Placement, SourceDrawing, Workspace


def _read(pkg, name):
    with zipfile.ZipFile(pkg) as z:
        data = z.read(name).decode()
    return json.loads(data) if name.endswith((".json", ".geojson")) else data


def _rewrite(pkg, name, mutate):
    """Copy the package with one JSON file changed."""
    out = pkg.with_name("tampered.storeypath")
    with zipfile.ZipFile(pkg) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.namelist():
            data = src.read(item)
            if item == name:
                doc = json.loads(data)
                mutate(doc)
                data = json.dumps(doc).encode()
            dst.writestr(item, data)
    return out


@pytest.fixture
def package(converted):
    ws, d, f_id, *_ = converted
    pkg = d / "out.storeypath"
    export_package(ws, pkg)
    return ws, d, f_id, pkg


def test_package_is_valid_and_complete(package):
    ws, _, f_id, pkg = package
    assert validate_package(pkg) == []
    spaces = _read(pkg, "spaces.geojson")["features"]
    assert len(spaces) == 24
    s = spaces[0]
    assert s["id"].startswith(f_id + "-")
    assert set(s["properties"]) >= {"kind", "type", "name", "number", "floor_id", "area_m2", "display_point"}
    lon, lat = s["properties"]["display_point"]
    assert 9.99 < lon < 10.01 and 49.99 < lat < 50.01
    with zipfile.ZipFile(pkg) as z:
        names = set(z.namelist())
    assert {"FORMAT.md", "schema/spaces.schema.json", "objects.csv", "changes.json"} <= names


def test_objects_csv_lists_every_id_with_its_parents(package):
    ws, _, f_id, pkg = package
    rows = list(csv.DictReader(io.StringIO(_read(pkg, "objects.csv"))))
    by_id = {r["id"]: r for r in rows}
    assert by_id[ws.id]["kind"] == "project"
    space = next(r for r in rows if r["kind"] == "space")
    assert space["floor_id"] == f_id
    assert space["building_id"] == f_id.rsplit("-", 1)[0]
    assert space["floor_ordinal"] == "2"
    active = {i for i, r in ws.objects.items() if r.status == "active"}
    assert active <= set(by_id)


def test_no_local_paths_leak(package):
    _, d, _, pkg = package
    manifest = _read(pkg, "manifest.json")
    assert all("/" not in s["file"] for s in manifest["sources"])
    assert str(d) not in json.dumps(manifest)


def test_change_list_between_exports(package):
    ws, d, f_id, pkg = package
    first = _read(pkg, "changes.json")
    assert first["sequence"] == 1 and first["previous_sequence"] is None
    assert len(first["added"]) == len(ws.objects) + 3  # + location, building, floor
    assert first["retired"] == []

    target = next(r.id for r in ws.floor_objects(f_id) if r.number == "214")
    ws.overrides[target] = Override(type="office")
    cells = [c for c in office_floor(2) if c.number != "209"]
    write_floor_dxf(d / "rev.dxf", cells)
    ws.floor(f_id).source = SourceDrawing(path="rev.dxf")
    report = convert_floor(ws, f_id, d)
    pkg2 = d / "out2.storeypath"
    export_package(ws, pkg2)
    assert validate_package(pkg2) == []

    second = _read(pkg2, "changes.json")
    assert second["sequence"] == 2 and second["previous_sequence"] == 1
    assert set(second["retired"]) == set(report.retired)  # room 209 and its door
    assert target in second["changed"]
    assert second["added"] == []
    assert set(report.retired) <= set(second["all_retired"])


def test_unplaced_building_exports_with_its_true_shape(converted):
    ws, d, _, b_id, *_ = converted
    export_package(ws, d / "a.storeypath")
    placed = json.loads(zipfile.ZipFile(d / "a.storeypath").read("spaces.geojson"))
    ws.building(b_id).placement = None
    path = d / "b.storeypath"
    manifest = export_package(ws, path)
    assert validate_package(path) == []
    assert manifest.placements[b_id].placed is False
    loose = json.loads(zipfile.ZipFile(path).read("spaces.geojson"))
    lons = [c[0] for f in loose["features"] for ring in f["geometry"]["coordinates"] for c in ring]
    assert max(abs(x) for x in lons) < 0.001  # around 0°N 0°E
    # same rooms, same sizes
    assert [f["properties"]["area_m2"] for f in loose["features"]] == [f["properties"]["area_m2"] for f in placed["features"]]


def test_validator_catches_problems(package):
    _, _, _, pkg = package

    def dup(doc):
        doc["features"].append(doc["features"][0])

    def bad_type(doc):
        doc["features"][0]["properties"]["type"] = "spaceship"

    def dangling(doc):
        doc["features"][0]["properties"]["connects"] = ["NOPE00-X-Y-F00-9999"]

    assert any("duplicate" in e for e in validate_package(_rewrite(pkg, "spaces.geojson", dup)))
    assert any("type" in e for e in validate_package(_rewrite(pkg, "spaces.geojson", bad_type)))
    assert any("unknown space" in e for e in validate_package(_rewrite(pkg, "openings.geojson", dangling)))


def test_georeferencing_anchor_and_bearing():
    g = Georeferencer(Placement(lon=10.0, lat=50.0, x=100, y=200, bearing=90))
    lon, lat = g.lonlat(100, 200)
    assert abs(lon - 10.0) < 1e-9 and abs(lat - 50.0) < 1e-9
    lon, lat = g.lonlat(100, 300)  # 100 m along the drawing's +Y = 100 m east
    assert lon > 10.0 and abs(lat - 50.0) < 1e-5
    assert abs((lon - 10.0) * 111_320 * 0.6428 - 100) < 1  # cos(50°) ≈ 0.6428


def test_workspace_round_trip_and_save_as_new(converted, tmp_path):
    ws, *_ = converted
    path = tmp_path / "a.spproj"
    ws.save(path)
    loaded = Workspace.load(path)
    assert loaded.model_dump(mode="json") == ws.model_dump(mode="json")

    copy = loaded.save_as_new_project(tmp_path / "b.spproj", "Copy")
    assert copy.id != ws.id
    assert all(i.startswith(copy.id + "-") for i in copy.objects)
    assert all(c.startswith(copy.id + "-") for r in copy.objects.values() for c in r.connects)
    assert copy.exports == []
