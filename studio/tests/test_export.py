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


def test_walls_around_a_roof_terrace_are_parapets():
    # A 10 m square roof, a 4 m room in one corner, the rest a terrace.
    from shapely.geometry import Point, box

    from storeypath.export import _walls_and_parapets

    edge = box(0, 0, 10, 10).difference(box(0.2, 0.2, 9.8, 9.8))
    room_walls = box(4.0, 0.2, 4.2, 4.2).union(box(0.2, 4.0, 4.2, 4.2))
    pier = box(10, 6, 10.4, 6.4)  # built onto the outside of the parapet
    walls = edge.union(room_walls).union(pier)
    room = box(0.2, 0.2, 4.0, 4.0)
    terrace = box(0.2, 0.2, 9.8, 9.8).difference(box(0.2, 0.2, 4.2, 4.2))
    full, parapets = _walls_and_parapets(walls, [(room, False), (terrace, True)], 0.2)  # (shape, open to the sky)
    assert parapets.contains(Point(7, 9.9)) and parapets.contains(Point(9.9, 7))  # the roof's edge
    assert parapets.contains(Point(10.3, 6.2))  # and what is built onto it
    assert full.contains(Point(2, 0.1)) and full.contains(Point(0.1, 2))  # the room's outside walls
    assert full.contains(Point(4.1, 2)) and full.contains(Point(2, 4.1))  # between the room and the terrace
    assert abs(full.area + parapets.area - walls.area) < 1e-6
    assert _walls_and_parapets(walls, [(room, False)], 0.2) == (walls, None)  # no terrace, no parapets
    # a glazed veranda is enclosed: its walls stay full height
    assert _walls_and_parapets(walls, [(room, False), (terrace, False)], 0.2) == (walls, None)


def test_doors_keep_their_swings(package):
    # which side a door hinges on and which way it opens, as drawn: for the 3D view
    ws, _, f_id, pkg = package
    cells = [c for c in office_floor(2) if c.door and c.door.block]
    doors = [r for r in ws.floor_objects(f_id) if r.kind == "opening" and r.type == "door" and r.swings]
    assert len(doors) >= len(cells) - 1
    ox, oy = 125.0, 48.0  # where the sample plan is drawn
    for c in cells:
        d = c.door
        at = (ox + d.x, oy + d.y)
        r = min(doors, key=lambda r: (r.geometry["coordinates"][0] - at[0]) ** 2 + (r.geometry["coordinates"][1] - at[1]) ** 2)
        (hx, hy), (qx, qy) = r.swings[0]
        assert min(abs(hx - p[0]) + abs(hy - p[1]) for p in r.span) < 0.15  # hinged at a jamb
        across = (qy - at[1]) if d.axis == "h" else (qx - at[0])
        assert abs(abs(across) - d.width) < 0.15 and across * d.swing > 0  # open, to the side drawn
    openings = _read(pkg, "openings.geojson")["features"]
    assert any(len(o["properties"].get("swings") or []) == 1 for o in openings if o["properties"]["type"] == "door")


def test_a_space_carries_the_text_its_drawing_writes_in_it(converted, tmp_path):
    # The drawing's own label, as written: kept when a person renames the space, and
    # in objects.csv, as a key to match on beside the ID.
    import csv
    import io
    import json
    import zipfile

    from storeypath.export import export_package
    from storeypath.workspace import Override

    ws, d, f_id, *_ = converted
    office = next(r for r in ws.floor_objects(f_id) if r.kind == "space" and r.number == "201")
    assert office.label == "OFFICE\n201"
    ws.overrides[office.id] = Override(name="Board room", number="B-1")
    out = tmp_path / "p.storeypath"
    export_package(ws, out)
    with zipfile.ZipFile(out) as z:
        space = next(f for f in json.loads(z.read("spaces.geojson"))["features"] if f["id"] == office.id)
        rows = {r["id"]: r for r in csv.DictReader(io.StringIO(z.read("objects.csv").decode()))}
    assert (space["properties"]["name"], space["properties"]["number"]) == ("Board room", "B-1")
    assert space["properties"]["drawing_label"] == "OFFICE\n201"
    assert rows[office.id]["drawing_label"] == "OFFICE\n201"
    unlabelled = next(r for r in ws.floor_objects(f_id) if r.kind == "space" and r.label is None)
    assert rows[unlabelled.id]["drawing_label"] == ""


def test_corners_closer_than_the_rounding_are_written_once(converted, tmp_path):
    # Two corners 3 mm apart round to one point (1 cm): a ring that repeats it has a
    # zero-length edge, which strict readers take for the ring crossing itself.
    from shapely.geometry import Polygon, mapping, shape

    ws, d, f_id, *_ = converted
    room = next(r for r in ws.floor_objects(f_id) if r.kind == "space")
    pts = list(shape(room.geometry).exterior.coords)[:-1]
    x, y = pts[0]
    room.geometry = mapping(Polygon([(x, y), (x + 0.003, y), *pts[1:]]))
    export_package(ws, tmp_path / "p.storeypath", record=False)
    with zipfile.ZipFile(tmp_path / "p.storeypath") as z:
        spaces = {f["id"]: f for f in json.loads(z.read("spaces.geojson"))["features"]}
    ring = spaces[room.id]["geometry"]["coordinates"][0]
    assert all(ring[i] != ring[i + 1] for i in range(len(ring) - 1))
    assert validate_package(tmp_path / "p.storeypath") == []
