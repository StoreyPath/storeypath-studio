"""Floors pre-built in 3D when a package is exported: format 0.5's world/ (bake.py,
viewer/world/bake.mjs)."""

import json
import struct
import sys
import zipfile

import pytest

from storeypath.bake import NODE_ENV, baker, node
from storeypath.export import export_package
from storeypath.package import FILES, FORMAT_VERSION, json_schemas
from storeypath.validate import validate_package
from storeypath.workspace import Workspace

with_node = pytest.mark.skipif(node()[0] is None or baker() is None, reason="needs Node.js and the viewer's baker")


def _gltf(data: bytes) -> dict:
    """A binary glTF's JSON."""
    magic, version, _ = struct.unpack_from("<4sII", data)
    length, kind = struct.unpack_from("<I4s", data, 12)
    assert magic == b"glTF" and version == 2 and kind == b"JSON"
    return json.loads(data[20:20 + length])


@with_node
def test_a_package_exported_with_node_has_each_floor_pre_built(converted):
    ws, d, f_id, *_ = converted
    said = []
    manifest = export_package(ws, d / "out.storeypath", say=said.append)
    assert validate_package(d / "out.storeypath") == []
    assert said == ["3D pre-built: 1 floor(s)"]
    assert manifest.format_version == FORMAT_VERSION and manifest.files["world"] == "world/"
    with zipfile.ZipFile(d / "out.storeypath") as z:
        assert [n for n in z.namelist() if n.startswith("world/")] == [f"world/{f_id}.glb"]
        gltf = _gltf(z.read(f"world/{f_id}.glb"))
        spaces = {f["id"] for f in json.loads(z.read("spaces.geojson"))["features"]}
        zones = {f["id"] for f in json.loads(z.read("zones.geojson"))["features"]}
    x = gltf["scenes"][0]["extras"]["storeypath"]
    assert x["floor_id"] == f_id and x["project_id"] == ws.id and x["export_sequence"] == manifest.export.sequence
    assert spaces <= set(x["rooms"]) <= spaces | zones  # what _ROOM indexes
    assert x["origin"]["kx"] > 0 and x["options"]["cutHeight"] == 1.25
    names = {n["name"] for n in gltf["nodes"]}
    assert {"slab", "wall", "wallTop", "ceiling", "door", "obstacles", "skirting", "trim", "handle", "lights"} <= names
    assert any(n.startswith("floor:") for n in names) and any(n.startswith("volume:") for n in names)


@with_node
def test_a_floors_items_are_pre_built_apart_from_the_rest(converted):
    # Format 0.6: the items in pieces of their own (a viewer leaves them out until
    # asked for), a box each, with the IDs their vertices index (drawn in detail, a
    # viewer builds them from the items: builder 3); the builder's version, so that a
    # viewer builds again a floor made by another.
    ws, d, f_id, *_ = converted
    desk = ws.add_item("DESK-MANAGER", f_id, 3.0, 3.0, rotation=90)
    ap = ws.add_item("ACCESS-POINT", f_id, 4.0, 4.0)
    export_package(ws, d / "out.storeypath", record=False)
    with zipfile.ZipFile(d / "out.storeypath") as z:
        gltf = _gltf(z.read(f"world/{f_id}.glb"))
    x = gltf["scenes"][0]["extras"]["storeypath"]
    assert x["builder"] == 3 and x["items"] == [desk.id, ap.id]
    nodes = {n["name"]: n for n in gltf["nodes"]}
    assert {"items:light", "items:light:high"} <= set(nodes)  # the desk below the cut, the AP above
    assert not {"items", "items:high"} & set(nodes)
    assert nodes["items:light"]["extras"] == {"material": "item", "form": "light"}
    assert nodes["items:light:high"]["extras"] == {"material": "item", "view": "full", "form": "light"}
    attributes = gltf["meshes"][nodes["items:light"]["mesh"]]["primitives"][0]["attributes"]
    assert {"POSITION", "COLOR_0", "_ITEM", "_FINISH"} == set(attributes)


@with_node
def test_pre_building_adds_the_world_and_changes_nothing_else(converted, monkeypatch):
    ws, d, *_ = converted
    export_package(ws, d / "with.storeypath", record=False)
    monkeypatch.setenv(NODE_ENV, "")
    export_package(ws, d / "without.storeypath", record=False)
    with zipfile.ZipFile(d / "with.storeypath") as a, zipfile.ZipFile(d / "without.storeypath") as b:
        assert sorted(n for n in a.namelist() if not n.startswith("world/")) == sorted(b.namelist())
        for name in b.namelist():
            if name != "manifest.json":
                assert a.read(name) == b.read(name), name
        ma, mb = json.loads(a.read("manifest.json")), json.loads(b.read("manifest.json"))
    assert ma["files"].pop("world") == "world/"
    del ma["export"]["exported_at"], mb["export"]["exported_at"]
    assert ma == mb


def test_without_node_the_package_is_as_before_and_says_why(converted, monkeypatch, tmp_path):
    ws, d, *_ = converted
    before = {"manifest.json", *FILES.values(), "FORMAT.md", *(f"schema/{n}" for n in json_schemas())}

    def export(name):
        said = []
        manifest = export_package(ws, d / name, record=False, say=said.append)
        with zipfile.ZipFile(d / name) as z:
            assert set(z.namelist()) == before
        assert "world" not in manifest.files and validate_package(d / name) == []
        return said

    monkeypatch.setenv(NODE_ENV, "")
    assert export("a.storeypath") == [f"3D not pre-built: {NODE_ENV} is empty"]
    monkeypatch.setenv(NODE_ENV, str(tmp_path / "node"))
    assert export("b.storeypath") == [f"3D not pre-built: {NODE_ENV} ({tmp_path / 'node'}) is not a program"]
    monkeypatch.delenv(NODE_ENV)
    monkeypatch.setenv("PATH", str(tmp_path))  # no node on it
    assert export("c.storeypath") == [f"3D not pre-built: Node.js was not found (install it, or set {NODE_ENV})"]

    # not asked to (Studio's live 3D view): nothing said
    said = []
    export_package(ws, d / "d.storeypath", record=False, bake=False, say=said.append)
    assert said == [] and set(zipfile.ZipFile(d / "d.storeypath").namelist()) == before


@with_node
def test_a_building_with_no_floors_is_exported_as_before(tmp_path):
    ws = Workspace.new("Empty")
    ws.add_building(ws.add_location("SITE", "Site"), "HQ", "Headquarters")
    said = []
    manifest = export_package(ws, tmp_path / "empty.storeypath", say=said.append)
    assert said == ["3D not pre-built: the package has no floors"] and "world" not in manifest.files


@pytest.mark.skipif(baker() is None, reason="needs the viewer's baker")
def test_a_baker_that_fails_does_not_fail_the_export(converted, monkeypatch):
    ws, d, *_ = converted
    monkeypatch.setenv(NODE_ENV, sys.executable)  # not Node.js: it cannot read the baker
    said = []
    manifest = export_package(ws, d / "p.storeypath", say=said.append)
    assert said[0].startswith("3D not pre-built: the baker failed: SyntaxError")
    assert "world" not in manifest.files and validate_package(d / "p.storeypath") == []
