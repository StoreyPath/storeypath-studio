"""One building per package (format 0.7): each package names its building and
lists what changed in it alone, compared in the building's own frame; items carry
where they stand in their building; one carried to another building is moved
away, not retired."""

import json
import zipfile

import pytest
import shapely
from shapely.geometry import shape

from storeypath.assets import asset_dir
from storeypath.export import ExportError, export_package
from storeypath.validate import validate_package
from storeypath.workspace import Override, Workspace

PACKAGES = asset_dir("spec") / "conformance" / "packages"


@pytest.fixture
def campus(tmp_path):
    """The demo campus, its two buildings exported once each (build_demo)."""
    from storeypath.samples import build_demo

    ws_path, packages = build_demo(tmp_path / "demo")
    ws = Workspace.load(ws_path)
    ids = {b.code: f"{ws.id}-{loc.code}-{b.code}" for loc in ws.locations for b in loc.buildings}
    return ws, ids, packages


def _read(path):
    with zipfile.ZipFile(path) as z:
        m = json.loads(z.read("manifest.json"))
        return m, json.loads(z.read("changes.json")), {f["id"]: f for f in json.loads(z.read("items.geojson"))["features"]}


def _ground(ws, b_id):
    """A building's ground floor, and a point in its largest room."""
    f_id = f"{b_id}-F00"
    room = max((shape(r.geometry) for r in ws.floor_objects(f_id) if r.kind == "space"), key=lambda g: g.area)
    p = room.representative_point()
    return f_id, p.x, p.y


def test_a_package_holds_one_building_and_lists_its_changes_alone(campus, tmp_path):
    ws, ids, packages = campus
    assert [p.name for p in packages] == ["demo-HQ.storeypath", "demo-ANNEX.storeypath"]
    assert all(validate_package(p) == [] for p in packages)
    hq, annex = ids["HQ"], ids["ANNEX"]
    with pytest.raises(ExportError, match="2 buildings: choose"):
        export_package(ws, tmp_path / "x.storeypath")
    with pytest.raises(ExportError, match="no building"):
        export_package(ws, tmp_path / "x.storeypath", building=f"{ws.id}-DEMO-NOPE")
    rooms = {code: next(r for r in sorted(ws.objects.values(), key=lambda r: r.id)
                        if r.kind == "space" and r.id.startswith(b + "-")) for code, b in ids.items()}
    for code, room in rooms.items():
        ws.overrides[room.id] = Override(name=f"RENAMED {code}")

    m = export_package(ws, tmp_path / "hq.storeypath", building=hq)
    assert validate_package(tmp_path / "hq.storeypath") == []
    assert m.scope.buildings == [hq] and list(m.placements) == [hq] and m.counts["buildings"] == 1
    assert all(s.floor_id.startswith(hq + "-") for s in m.sources)
    with zipfile.ZipFile(tmp_path / "hq.storeypath") as z:
        changes = json.loads(z.read("changes.json"))
        feature_ids = [f["id"] for name in ("buildings.geojson", "floors.geojson", "spaces.geojson", "openings.geojson")
                       for f in json.loads(z.read(name))["features"]]
        locations = [f["id"] for f in json.loads(z.read("location.geojson"))["features"]]
    assert feature_ids and all(i == hq or i.startswith(hq + "-") for i in feature_ids)
    assert locations == [hq.rsplit("-", 1)[0]]
    assert changes["changed"] == [rooms["HQ"].id] and changes["retired"] == [] and changes["added"] == []

    export_package(ws, tmp_path / "annex.storeypath", building=annex)
    with zipfile.ZipFile(tmp_path / "annex.storeypath") as z:
        changes = json.loads(z.read("changes.json"))
    assert changes["changed"] == [rooms["ANNEX"].id]  # the Annex's rename, not yet exported
    assert changes["added"] == [] and changes["retired"] == []


def test_moving_a_building_changes_nothing_in_it(campus, tmp_path):
    # Its rooms, doors and items stay where they are in its own frame: only the
    # building is changed, and each item's position in it (local) is as it was.
    ws, ids, _ = campus
    hq = ids["HQ"]
    f_id, x, y = _ground(ws, hq)
    desk = ws.add_item("DESK-MANAGER", f_id, x, y, rotation=90)
    export_package(ws, tmp_path / "a.storeypath", building=hq)
    b = ws.building(hq)
    b.placement = b.placement.model_copy(update={"lon": b.placement.lon + 0.001, "bearing": (b.placement.bearing + 30) % 360})
    export_package(ws, tmp_path / "b.storeypath", building=hq)
    assert validate_package(tmp_path / "b.storeypath") == []
    _, _, before = _read(tmp_path / "a.storeypath")
    _, changes, after = _read(tmp_path / "b.storeypath")
    assert (changes["added"], changes["changed"], changes["retired"]) == ([], [hq], [])
    p, q = before[desk.id]["properties"], after[desk.id]["properties"]
    assert p["local"] == q["local"] == {"x_m": round(x, 4), "y_m": round(y, 4), "rotation_deg": 90.0}
    assert p["display_point"] != q["display_point"] and round((q["heading"] - p["heading"]) % 360, 2) == 30.0


def test_an_item_carried_to_another_building_is_moved_away_not_retired(campus, tmp_path):
    ws, ids, _ = campus
    hq, annex = ids["HQ"], ids["ANNEX"]
    f_id, x, y = _ground(ws, hq)
    desk = ws.add_item("DESK-JUNIOR", f_id, x, y)
    tv = ws.add_item("TV", f_id, x + 1, y)
    export_package(ws, tmp_path / "hq-1.storeypath", building=hq)
    export_package(ws, tmp_path / "annex-1.storeypath", building=annex)
    there, ax, ay = _ground(ws, annex)
    desk.floor_id, desk.x, desk.y = there, ax, ay
    tv.status = "retired"

    room = next(i for i, r in ws.objects.items() if i.startswith(f_id + "-") and r.kind == "space"
                and shape(r.geometry).covers(shapely.Point(x, y)))
    export_package(ws, tmp_path / "hq-2.storeypath", building=hq)
    assert validate_package(tmp_path / "hq-2.storeypath") == []
    _, changes, items = _read(tmp_path / "hq-2.storeypath")
    assert changes["moved_away"] == [{"id": desk.id, "building_id": annex}] and desk.id not in items
    assert changes["retired"] == [tv.id] and tv.id in changes["all_retired"]
    assert changes["changed"] == [room]  # it seats one fewer: its desk went
    export_package(ws, tmp_path / "annex-2.storeypath", building=annex)
    _, changes, items = _read(tmp_path / "annex-2.storeypath")
    assert desk.id in changes["changed"] and changes["added"] == [] and desk.id in items  # moved in, not new
    export_package(ws, tmp_path / "hq-3.storeypath", building=hq)
    _, changes, _ = _read(tmp_path / "hq-3.storeypath")
    assert changes["moved_away"] == [] and changes["retired"] == []  # each said once


def _rewritten(source, out, **files):
    """A copy of a package with some of its files replaced (name → text, or a function of the text)."""
    with zipfile.ZipFile(source) as z, zipfile.ZipFile(out, "w") as w:
        for n in z.namelist():
            data = z.read(n).decode() if n in files else z.read(n)
            w.writestr(n, files[n](data) if callable(files.get(n)) else files.get(n, data))
    return out


def test_a_package_of_this_format_names_its_one_building(tmp_path):
    def two(text):
        m = json.loads(text)
        m["scope"]["buildings"].append(m["scope"]["buildings"][0] + "X")
        return json.dumps(m)

    def none(text):
        m = json.loads(text)
        del m["scope"]
        return json.dumps(m)

    assert any("holds one building" in e for e in
               validate_package(_rewritten(PACKAGES / "campus-hq.storeypath", tmp_path / "a.storeypath", **{"manifest.json": two})))
    assert any("holds one building" in e for e in
               validate_package(_rewritten(PACKAGES / "campus-hq.storeypath", tmp_path / "b.storeypath", **{"manifest.json": none})))
    assert validate_package(PACKAGES / "campus.storeypath") == []  # 0.6: a whole project then


def test_an_items_position_in_its_building_agrees_with_the_map(tmp_path):
    def shifted(text):
        fc = json.loads(text)
        fc["features"][0]["properties"]["local"]["x_m"] += 1.0
        return json.dumps(fc)

    def without(text):
        fc = json.loads(text)
        del fc["features"][0]["properties"]["local"]
        return json.dumps(fc)

    errors = validate_package(_rewritten(PACKAGES / "campus-hq.storeypath", tmp_path / "a.storeypath", **{"items.geojson": shifted}))
    assert len(errors) == 1 and "m from its position in its building" in errors[0]
    errors = validate_package(_rewritten(PACKAGES / "campus-hq.storeypath", tmp_path / "b.storeypath", **{"items.geojson": without}))
    assert len(errors) == 1 and "no position in its building" in errors[0]


def test_rows_of_kinds_a_reader_does_not_know_are_left_alone(tmp_path):
    # A later format may add kinds (0.6 added items): their rows, and their IDs in
    # changes.json, are not errors.
    def row(text):
        return text + "EWBSSN-P000001,plant,PALM,,,EWBSSN,,,,,,,,,,,\n"

    def listed(text):
        c = json.loads(text)
        c["added"].append("EWBSSN-P000001")
        return json.dumps(c)

    out = _rewritten(PACKAGES / "campus-hq.storeypath", tmp_path / "a.storeypath",
                     **{"objects.csv": row, "changes.json": listed})
    assert validate_package(out) == []


def test_a_package_newer_than_this_studio_is_refused(tmp_path):
    # Before 1.0 a minor version may change what a package means: a newer one is
    # refused, saying so; older ones and newer patches are read.
    from storeypath.package import FORMAT_VERSION, version_problem

    assert version_problem(FORMAT_VERSION) is None and version_problem("0.4.0") is None
    major, minor, _ = FORMAT_VERSION.split(".")
    assert version_problem(f"{major}.{minor}.9") is None
    assert "update StoreyPath Studio" in version_problem(f"{major}.{int(minor) + 1}.0")
    assert "unsupported" in version_problem("1.0.0")

    def newer(text):
        m = json.loads(text)
        m["format_version"] = f"{major}.{int(minor) + 1}.0"
        return json.dumps(m)

    errors = validate_package(_rewritten(PACKAGES / "campus-hq.storeypath", tmp_path / "n.storeypath", **{"manifest.json": newer}))
    assert len(errors) == 1 and "newer than this Studio" in errors[0]
