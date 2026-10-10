"""Item types (catalogue.py): how each is drawn (its shape, format 0.9.1), the types a package
carries (those its items use, each once, or the whole catalogue), and the item types a file
brings, read for a person to choose which to take (the Item types page)."""

import io
import json
import threading
import zipfile

import pytest

from sessions import call
from storeypath import accounts as acc
from storeypath import catalogue
from storeypath.accounts import Accounts
from storeypath.bundle import write_project_file
from storeypath.export import export_package, preview_package
from storeypath.samples import build_demo
from storeypath.server import Studio
from storeypath.validate import validate_package
from storeypath.web import make_server
from storeypath.workspace import Workspace


def _types_of(package) -> list[str]:
    with zipfile.ZipFile(package) as z:
        manifest = json.loads(z.read("manifest.json"))
        return [t["code"] for t in json.loads(z.read(manifest["files"]["catalogue"]))["types"]]


# ---- how a type is drawn -------------------------------------------------------------------

def test_every_default_type_says_how_it_is_drawn_and_one_without_is_drawn_by_its_code():
    cat = catalogue.default_catalogue()
    shapes = {t.code: t.shape for t in cat.types}
    assert all(shapes.values()) and set(shapes.values()) <= set(catalogue.SHAPES)
    assert (shapes["DESK-MANAGER"], shapes["MEETING-TABLE-8"], shapes["TV"], shapes["ACCESS-POINT"], shapes["KIOSK"]) == \
        ("desk", "meeting_table", "screen", "access_point", "kiosk")
    of = lambda code, shape=None: catalogue.shape_of(catalogue.ItemType(code=code, name_en="x", shape=shape))  # noqa: E731
    assert (of("DESK-HOT"), of("SCREEN-LED"), of("PRINTER-A3"), of("LOCKER"), of("LOCKER", "copier")) == \
        ("desk", "screen", "copier", "box", "copier")


def test_a_shape_this_studio_does_not_know_is_read_as_none_and_an_older_catalogue_gains_its_defaults_shapes(tmp_path):
    later = catalogue.ItemType.model_validate({"code": "PLANT", "name_en": "Plant", "shape": "plant"})  # a later format's
    assert later.shape is None and catalogue.shape_of(later) == "box"
    # a catalogue written before shapes: its default types take theirs, its own type none,
    # and one a person left without a shape (null) keeps none
    older = catalogue.default_catalogue().model_dump(mode="json")
    for t in older["types"]:
        del t["shape"]
    older["types"].append({"code": "PLANT", "name_en": "Plant"})
    cat, filled = catalogue.read(json.dumps(older))
    assert filled and cat.get("SOFA").shape == "sofa" and cat.get("PLANT").shape is None
    cat.get("SOFA").shape = None
    again, filled = catalogue.read(json.dumps(cat.model_dump(mode="json")))
    assert again.get("SOFA").shape is None and not filled


def test_what_a_person_saves_is_checked_beyond_reading_it():
    for bad, said in (({"code": "A", "shape": "rocket"}, "shape 'rocket'"), ({"code": "A", "width": 0}, "width"),
                      ({"code": "A", "height": 500}, "height"), ({"code": "A", "elevation": -1}, "elevation"),
                      ({"code": "A", "depth": True}, "depth")):
        with pytest.raises(ValueError, match=said):
            catalogue.check_types([bad])
    catalogue.check_types([{"code": "A", "shape": None, "width": 1.2, "elevation": 0}])  # (fine)


# ---- the types a package carries -----------------------------------------------------------

def test_a_package_carries_the_types_its_items_use_each_once_or_the_whole_catalogue(converted, tmp_path):
    ws, d, f_id, b_id, *_ = converted
    ws.add_item("DESK-MANAGER", f_id, 3.0, 4.0)
    ws.add_item("DESK-MANAGER", f_id, 6.0, 4.0)
    ws.add_item("MEETING-TABLE-8", f_id, 9.0, 4.0)
    cat = catalogue.default_catalogue()
    used = export_package(ws, tmp_path / "used.storeypath", building=b_id, bake=False, catalogue=cat, record=False)
    assert sorted(_types_of(tmp_path / "used.storeypath")) == ["DESK-MANAGER", "MEETING-TABLE-8"]
    assert used.format_version == "0.9.1" and validate_package(tmp_path / "used.storeypath") == []
    export_package(ws, tmp_path / "all.storeypath", building=b_id, bake=False, catalogue=cat, record=False, item_types="all")
    assert _types_of(tmp_path / "all.storeypath") == [t.code for t in cat.types]
    assert validate_package(tmp_path / "all.storeypath") == []
    with zipfile.ZipFile(tmp_path / "used.storeypath") as z:  # each with how it is drawn
        assert {t["code"]: t["shape"] for t in json.loads(z.read("catalogue.json"))["types"]} == \
            {"DESK-MANAGER": "desk", "MEETING-TABLE-8": "meeting_table"}
    # Studio's own viewers: every type (one no item has yet may be placed there)
    preview_package(ws, tmp_path / "preview.storeypath", catalogue=cat)
    assert len(_types_of(tmp_path / "preview.storeypath")) == len(cat.types)
    with pytest.raises(Exception, match="item types"):
        export_package(ws, tmp_path / "x.storeypath", building=b_id, bake=False, record=False, item_types="some")


def test_the_command_line_exports_every_item_type_when_asked(tmp_path):
    from typer.testing import CliRunner

    from storeypath.cli import app

    ws_path, _ = build_demo(tmp_path / "demo")
    ws = Workspace.load(ws_path)
    hq = f"{ws.id}-DEMO-HQ"
    ws.add_item("SOFA", f"{hq}-F00", 2.0, 2.0)
    ws.save(ws_path)
    runner = CliRunner()
    r = runner.invoke(app, ["export", str(ws_path), "-o", str(tmp_path / "hq.storeypath"), "--building", "HQ"])
    assert r.exit_code == 0, r.output
    assert _types_of(tmp_path / "hq.storeypath") == ["SOFA"]
    r = runner.invoke(app, ["export", str(ws_path), "-o", str(tmp_path / "hq-all.storeypath"), "--building", "HQ",
                            "--item-types", "all"])
    assert r.exit_code == 0, r.output
    assert len(_types_of(tmp_path / "hq-all.storeypath")) == len(catalogue.default_catalogue().types)
    r = runner.invoke(app, ["export", str(ws_path), "-o", str(tmp_path / "x.storeypath"), "--building", "HQ",
                            "--item-types", "most"])
    assert r.exit_code != 0 and "item types" in r.output


# ---- the types a file brings ---------------------------------------------------------------

def test_the_types_a_catalogue_file_brings_in_any_of_its_forms():
    types = [t.model_dump(mode="json") for t in catalogue.default_catalogue().types[:3]]
    for sent in ({"format": "storeypath-catalogue", "format_version": 1, "types": types}, {"types": types}, types):
        got = catalogue.types_in(json.dumps(sent).encode())
        assert (got["source"], got["name"], [t["code"] for t in got["types"]]) == \
            ("catalogue", None, [t["code"] for t in types])
    # one of this Studio's default codes, filled in as it reads its own; a new one as it is
    got = catalogue.types_in(json.dumps([{"code": "SOFA", "name_en": "Sofa"}, {"code": "PLANT", "name_en": "Plant"}]).encode())
    assert [(t["code"], t["shape"]) for t in got["types"]] == [("SOFA", "sofa"), ("PLANT", None)]
    assert catalogue.types_in(("﻿" + json.dumps(types)).encode())["types"]  # a byte-order mark first


def test_the_types_a_package_and_a_project_file_bring(converted, tmp_path):
    ws, d, f_id, b_id, *_ = converted
    ws.add_item("KIOSK", f_id, 3.0, 4.0)
    export_package(ws, tmp_path / "p.storeypath", building=b_id, bake=False, record=False)
    got = catalogue.types_in((tmp_path / "p.storeypath").read_bytes())
    assert (got["source"], got["name"], [t["code"] for t in got["types"]]) == ("package", "Test Project", ["KIOSK"])
    buf = io.BytesIO()
    write_project_file(ws, {}, catalogue.default_catalogue(), buf)
    got = catalogue.types_in(buf.getvalue())
    assert (got["source"], got["name"], len(got["types"])) == ("project", "Test Project", len(catalogue.default_catalogue().types))


def test_a_file_that_brings_no_item_types_is_refused_saying_why():
    def zipped(files: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in files.items():
                z.writestr(name, data)
        return buf.getvalue()

    for sent, said in (
            (b"", "not JSON"), (b"hello", "not JSON"), (b"\xff\xfe\x00", "not a catalogue"),
            (b'{"hello": "world"}', "no list of types"), (b'{"types": []}', "brings no item types"),
            (b'{"format": "something-else", "types": [{}]}', "its format"),
            (json.dumps({"types": [{"code": "desk manager", "name_en": "x"}]}).encode(), "type 1 \\(desk manager\\): code"),
            (json.dumps({"types": [{"code": "A", "name_en": "a"}, {"code": "A", "name_en": "b"}]}).encode(), "share a code"),
            (b"PK\x03\x04 not a zip", "damaged"),
            (zipped({"readme.txt": "hello"}), "no manifest.json or project.json"),
            (zipped({"manifest.json": json.dumps({"project": {"name": "X"}, "files": {}})}), "brings no item types"),
            (zipped({"project.json": json.dumps({"project": {"name": "X"}})}), "brings no item types")):
        with pytest.raises(ValueError, match=said):
            catalogue.types_in(sent)


# ---- through Studio's server ---------------------------------------------------------------

class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """Studio with the tests' campus (its items: a desk, a sofa), an admin, a user who may
    change the item types and one who may not."""
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    data = tmp_path / "data"
    ws_path, _ = build_demo(data / "demo")
    ws = Workspace.load(ws_path)
    ws.add_item("DESK-JUNIOR", f"{ws.id}-DEMO-HQ-F00", 2.0, 2.0)
    ws.add_item("SOFA", f"{ws.id}-DEMO-HQ-F00", 5.0, 2.0)
    ws.save(ws_path)
    studio = Studio(data, model=NoModel(), warm=False)
    accounts = Accounts(data)
    srv = make_server(studio, port=0, accounts=accounts)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    people = {name: accounts.add_user(name, "a password", role=role, capabilities=caps, must_change_password=False)
              for name, role, caps in (("boss", "admin", []), ("kim", "user", ["catalogue"]), ("bob", "user", []))}
    tokens = {n: accounts.start_session(u) for n, u in people.items()}
    yield srv.server_port, studio, accounts, tokens, ws.id
    srv.shutdown()
    srv.server_close()


def test_the_types_a_file_brings_are_read_for_who_may_change_them(studio):
    port, _, _, tokens, _ = studio
    sent = json.dumps({"types": [{"code": "PLANTER", "name_en": "Planter", "shape": "box"}]}).encode()
    status, got, _ = call(port, "PUT", "/api/catalogue/types-in", raw=sent, token=tokens["kim"])
    assert status == 200 and got["source"] == "catalogue" and [t["code"] for t in got["types"]] == ["PLANTER"]
    assert call(port, "GET", "/api/catalogue", token=tokens["kim"])[1]["types"][-1]["code"] != "PLANTER"  # read, not taken
    assert call(port, "PUT", "/api/catalogue/types-in", raw=sent, token=tokens["bob"])[0] == 403
    status, said, _ = call(port, "PUT", "/api/catalogue/types-in", raw=b"hello", token=tokens["boss"])
    assert status == 400 and "not JSON" in said["error"]


def test_a_type_saved_is_checked_and_what_changed_is_in_the_audit_log(studio):
    port, _, accounts, tokens, _ = studio
    _, cat, _ = call(port, "GET", "/api/catalogue", token=tokens["kim"])
    typo = {"types": cat["types"] + [{"code": "PLANTER", "name_en": "Planter", "shape": "plantr"}]}
    status, said, _ = call(port, "POST", "/api/catalogue", typo, token=tokens["kim"])
    assert status == 400 and "PLANTER: shape 'plantr'" in said["error"]
    cat["types"][0]["color"] = "#123456"
    status, saved, _ = call(port, "POST", "/api/catalogue",
                            {"types": cat["types"] + [{"code": "PLANTER", "name_en": "Planter", "shape": "box"}]}, token=tokens["kim"])
    assert status == 200 and saved["types"][-1]["shape"] == "box"
    entry = accounts.audit_tail()[0]
    assert (entry["action"], entry["user"]["username"], entry["added"], entry["changed"]) == \
        ("item types changed", "kim", ["PLANTER"], [cat["types"][0]["code"]])
    assert call(port, "POST", "/api/catalogue", {"types": cat["types"]}, token=tokens["bob"])[0] == 403


def test_a_package_exported_through_studio_carries_the_types_asked_for(studio):
    port, st, _, tokens, code = studio
    hq = f"{code}-DEMO-HQ"
    for asked, want in ((None, ["DESK-JUNIOR", "SOFA"]), ("all", None)):
        status, job, _ = call(port, "POST", f"/api/projects/{code}/export",
                              {"building": hq, **({"item_types": asked} if asked else {})}, token=tokens["boss"])
        assert status == 200
        for _ in range(600):  # (the export is a job: followed until it is over)
            j = call(port, "GET", f"/api/jobs/{job['id']}", token=tokens["boss"])[1]
            if j["state"] not in ("waiting", "running"):
                break
            threading.Event().wait(0.1)
        assert j["state"] == "done", j
        _, blob, _ = call(port, "GET", f"/api/projects/{code}/exports/{j['result']['file']}", token=tokens["boss"])
        types = _types_of(io.BytesIO(blob))
        assert sorted(types) == want if want else len(types) == len(st.catalogue().types)
    status, said, _ = call(port, "POST", f"/api/projects/{code}/export", {"building": hq, "item_types": "most"},
                           token=tokens["boss"])
    assert status == 400 and "item_types" in said["error"]
