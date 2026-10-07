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


def test_the_local_frame_vectors_are_studios_projection():
    from storeypath.georef import Georeferencer
    from storeypath.workspace import Placement

    vectors = json.loads((asset_dir("spec") / "conformance" / "localframe.json").read_text())
    for v in vectors["vectors"]:
        lon, lat = Georeferencer(Placement(**v["placement"])).lonlat(*v["local"])
        assert abs(lon - v["lonlat"][0]) < 1e-9 and abs(lat - v["lonlat"][1]) < 1e-9
