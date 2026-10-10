import json

from storeypath.assets import asset_dir, format_spec
from storeypath.package import FORMAT_VERSION, json_schemas


def test_committed_schemas_match_the_models():
    """spec/schema/ must be regenerated when the package models change:
    uv run storeypath schema ../spec/schema"""
    folder = asset_dir("spec") / "schema"
    committed = {p.name: json.loads(p.read_text()) for p in folder.glob("*.json")}
    assert committed == json_schemas()


def test_spec_documents_the_current_version():
    major_minor = ".".join(FORMAT_VERSION.split(".")[:2])
    assert f"version {major_minor}" in format_spec().splitlines()[0]


def test_the_conformance_packages_are_valid():
    """What every reader must read the same way (spec/conformance/; the Go module in
    go/ checks the same packages)."""
    from storeypath.validate import validate_package

    folder = asset_dir("spec") / "conformance" / "packages"
    packages = sorted(folder.glob("*.storeypath"))
    assert len(packages) >= 2
    for p in packages:
        assert validate_package(p) == [], p.name


def test_the_campus_packages_have_furniture_and_equipment():
    """Items for every reader to read, in campus's packages of this format (a
    building each): desks of several grades in offices (one in a zone, one in a
    building turned on the map), a photocopier in a corridor, two access points
    under the ceiling, a sofa, a TV on a wall, a bed; each where it stands in its
    building (local)."""
    import zipfile

    items, spaces, codes = [], {}, set()
    for name in ("campus-hq", "campus-annex"):
        with zipfile.ZipFile(asset_dir("spec") / "conformance" / "packages" / f"{name}.storeypath") as z:
            manifest = json.loads(z.read("manifest.json"))
            held = json.loads(z.read(manifest["files"]["items"]))["features"]
            spaces.update({f["id"]: f["properties"] for f in json.loads(z.read(manifest["files"]["spaces"]))["features"]})
            codes |= {t["code"] for t in json.loads(z.read(manifest["files"]["catalogue"]))["types"]}
        # (of this format: its major and minor version; a patch only adds, so its packages stand)
        assert manifest["format_version"].split(".")[:2] == FORMAT_VERSION.split(".")[:2]
        assert manifest["counts"]["items"] == len(held)
        assert all(f["properties"]["building_id"] == manifest["scope"]["buildings"][0] for f in held)
        items += held
    by_type: dict[str, list[dict]] = {}
    for f in items:
        by_type.setdefault(f["properties"]["type"], []).append(f["properties"])
    assert set(by_type) <= codes and all(p["local"] for ps in by_type.values() for p in ps)
    desks = [p for t, ps in by_type.items() if t.startswith("DESK-") for p in ps]
    assert len({p["type"] for p in desks}) >= 5
    assert {spaces[p["space_id"]]["type"] for p in desks if p["zone_id"] is None} == {"office", "open_area"}
    assert any(p["zone_id"] for p in desks) and len({p["building_id"] for p in desks}) == 2
    assert [spaces[p["space_id"]]["type"] for p in by_type["COPIER"]] == ["corridor"]
    assert len(by_type["ACCESS-POINT"]) == 2 and all(p["mount"] == "ceiling" and p["elevation_m"] is None
                                                      for p in by_type["ACCESS-POINT"])
    assert len(by_type["SOFA"]) == 1 and [(p["mount"], p["elevation_m"]) for p in by_type["TV"]] == [("wall", 1.2)]
    assert [spaces[p["space_id"]]["type"] for p in by_type["BED-KING"]] == ["office"]
    # a wayfinding kiosk in the reception, facing its door (the drawing's -y)
    assert [(spaces[p["space_id"]]["type"], p["local"]["rotation_deg"]) for p in by_type["KIOSK"]] == [("lobby", 0)]


def test_the_local_frame_vectors_are_studios_projection():
    from storeypath.georef import Georeferencer
    from storeypath.workspace import Placement

    vectors = json.loads((asset_dir("spec") / "conformance" / "localframe.json").read_text())
    for v in vectors["vectors"]:
        lon, lat = Georeferencer(Placement(**v["placement"])).lonlat(*v["local"])
        assert abs(lon - v["lonlat"][0]) < 1e-9 and abs(lat - v["lonlat"][1]) < 1e-9
