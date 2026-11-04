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
from storeypath.package import FORMAT_VERSION
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
    assert (shapes["DESK-MANAGER"], shapes["MEETING-TABLE-8"], shapes["CHAIR-TASK"], shapes["CHAIR-VISITOR"], shapes["CREDENZA"],
            shapes["CCTV-DOME"], shapes["TV"], shapes["ACCESS-POINT"], shapes["KIOSK"]) == \
        ("desk", "table", "chair", "side_chair", "storage", "camera", "screen", "access_point", "kiosk")
    of = lambda code, shape=None: catalogue.shape_of(catalogue.ItemType(code=code, name_en="x", shape=shape))  # noqa: E731
    assert (of("DESK-HOT"), of("MEETING-ROUND"), of("SCREEN-LED"), of("PRINTER-A3"), of("LOCKER"), of("LOCKER", "copier"),
            of("TABLE-X", "meeting_table")) == ("desk", "table", "screen", "copier", "box", "copier", "table")


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



def test_a_types_plan_format_0_13_2_is_checked_as_it_is_saved():
    """A type's drawing seen from above: lines of two points or more, rings of three or more,
    every point within its box to 1 mm; else refused, saying why."""
    ring = [[-0.5, -0.3], [0.5, -0.3], [0.5, 0.3], [-0.5, 0.3]]
    ok = {"code": "A", "width": 1.0, "depth": 0.6, "plan": {"lines": [ring + [ring[0]]], "outline": [[ring]]}}
    catalogue.check_types([ok])
    catalogue.check_types([{**ok, "plan": {"lines": [[[0.5005, 0.0], [0.0, 0.0]]]}}])  # (within 1 mm of its box)
    for plan, said in (({"lines": []}, "no lines"), ({"lines": [[[0, 0]]]}, "a line of fewer than 2"),
                       ({"lines": [[[0, 0], [0.6, 0]]]}, "outside its box"), ({"lines": [[[0, 0], [0, 0.31]]]}, "outside its box"),
                       ({"lines": [[[0, 0], [0.1, 0]]], "outline": [[[[0, 0], [0.1, 0]]]]}, "a ring of fewer than 3"),
                       ({"lines": [[[0, 0], ["x", 0]]]}, "outside its box")):
        with pytest.raises(ValueError, match=said):
            catalogue.check_types([{**ok, "plan": plan}])
    assert catalogue.ItemType.model_validate({**ok, "name_en": "A"}).plan.outline[0][0][2] == (0.5, 0.3)

# ---- the types a package carries -----------------------------------------------------------

def test_a_package_carries_the_types_its_items_use_each_once_or_the_whole_catalogue(converted, tmp_path):
    ws, d, f_id, b_id, *_ = converted
    ws.add_item("DESK-MANAGER", f_id, 3.0, 4.0)
    ws.add_item("DESK-MANAGER", f_id, 6.0, 4.0)
    ws.add_item("MEETING-TABLE-8", f_id, 9.0, 4.0)
    cat = catalogue.default_catalogue()
    used = export_package(ws, tmp_path / "used.storeypath", building=b_id, bake=False, catalogue=cat, record=False)
    assert sorted(_types_of(tmp_path / "used.storeypath")) == ["DESK-MANAGER", "MEETING-TABLE-8"]
    assert used.format_version == FORMAT_VERSION and validate_package(tmp_path / "used.storeypath") == []
    export_package(ws, tmp_path / "all.storeypath", building=b_id, bake=False, catalogue=cat, record=False, item_types="all")
    assert _types_of(tmp_path / "all.storeypath") == [t.code for t in cat.types]
    assert validate_package(tmp_path / "all.storeypath") == []
    with zipfile.ZipFile(tmp_path / "used.storeypath") as z:  # each with how it is drawn
        assert {t["code"]: t["shape"] for t in json.loads(z.read("catalogue.json"))["types"]} == \
            {"DESK-MANAGER": "desk", "MEETING-TABLE-8": "table"}
    # Studio's own viewers: every type (one no item has yet may be placed there)
    preview_package(ws, tmp_path / "preview.storeypath", catalogue=cat)
    assert len(_types_of(tmp_path / "preview.storeypath")) == len(cat.types)
    with pytest.raises(Exception, match="item types"):
        export_package(ws, tmp_path / "x.storeypath", building=b_id, bake=False, record=False, item_types="some")
    # (0.13.2) a type with a plan: carried as it is, and the package valid
    cat = catalogue.default_catalogue()
    desk = cat.get("DESK-MANAGER")
    w, d = desk.width / 2, desk.depth / 2
    desk.plan = catalogue.ItemPlan(lines=[[(-w, -d), (w, -d), (w, d), (-w, d), (-w, -d)], [(0, -d), (0, d)]],
                                   outline=[[[(-w, -d), (w, -d), (w, d), (-w, d)]]])
    export_package(ws, tmp_path / "plan.storeypath", building=b_id, bake=False, catalogue=cat, record=False)
    assert validate_package(tmp_path / "plan.storeypath") == []
    with zipfile.ZipFile(tmp_path / "plan.storeypath") as z:
        got = {t["code"]: t["plan"] for t in json.loads(z.read("catalogue.json"))["types"]}
    assert got["DESK-MANAGER"]["lines"][1] == [[0, -d], [0, d]] and len(got["DESK-MANAGER"]["outline"][0][0]) == 4
    assert got["MEETING-TABLE-8"] is None


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


# ---- a type's 3D model (format 0.9.2) ----------------------------------------------------------

def _exported(port, code, building, token) -> bytes:
    status, job, _ = call(port, "POST", f"/api/projects/{code}/export", {"building": building}, token=token)
    assert status == 200
    for _ in range(600):  # (the export is a job: followed until it is over)
        j = call(port, "GET", f"/api/jobs/{job['id']}", token=token)[1]
        if j["state"] not in ("waiting", "running"):
            break
        threading.Event().wait(0.1)
    assert j["state"] == "done", j
    return call(port, "GET", f"/api/projects/{code}/exports/{j['result']['file']}", token=token)[1]


def test_a_type_given_a_3d_model_takes_its_size_and_a_package_carries_it_once(studio, tmp_path):
    from test_models import sofa_in_cm

    port, _, accounts, tokens, code = studio
    kim = tokens["kim"]
    put = "/api/catalogue/types/SOFA/model?name=sofa.glb"
    assert call(port, "PUT", put, raw=sofa_in_cm(), token=tokens["bob"])[0] == 403
    status, made, _ = call(port, "PUT", put, raw=sofa_in_cm(), token=kim)
    assert status == 200 and (made["name"], made["units"], made["size"], made["triangles"]) == ("sofa.glb", "cm", [2.0, 0.9, 0.75], 12)
    sofa = lambda: next(t for t in call(port, "GET", "/api/catalogue", token=kim)[1]["types"] if t["code"] == "SOFA")  # noqa: E731
    assert sofa()["model"] == "models/SOFA.glb" and (sofa()["width"], sofa()["depth"], sofa()["height"]) == (2.0, 0.9, 0.75)
    status, glb, _ = call(port, "GET", "/api/catalogue/models/SOFA.glb", token=tokens["bob"])
    assert status == 200 and glb[:4] == b"glTF"
    status, svg, _ = call(port, "GET", "/api/catalogue/models/SOFA.svg?view=top", token=tokens["bob"])
    assert status == 200 and svg.startswith(b"<svg")

    # a package carries it once, beside its catalogue.json, and is valid
    blob = _exported(port, code, f"{code}-DEMO-HQ", tokens["boss"])
    (tmp_path / "hq.storeypath").write_bytes(blob)
    assert validate_package(tmp_path / "hq.storeypath") == []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        manifest = json.loads(z.read("manifest.json"))
        types = {t["code"]: t for t in json.loads(z.read(manifest["files"]["catalogue"]))["types"]}
        assert manifest["format_version"] == FORMAT_VERSION and manifest["files"]["models"] == "models/"
        assert types["SOFA"]["model"] == "models/SOFA.glb" and types["DESK-JUNIOR"]["model"] is None
        assert z.read("models/SOFA.glb") == glb and [n for n in z.namelist() if n.startswith("models/")] == ["models/SOFA.glb"]

    # what a page sends never says which types have a model
    cat = call(port, "GET", "/api/catalogue", token=kim)[1]
    for t in cat["types"]:
        t["model"] = None
    assert call(port, "POST", "/api/catalogue", {"types": cat["types"]}, token=kim)[0] == 200
    assert sofa()["model"] == "models/SOFA.glb"

    # turned a quarter: its width and depth swap; a file that is no model is refused, the model kept
    status, turned, _ = call(port, "POST", "/api/catalogue/types/SOFA/model/turn", {"turn": 90}, token=kim)
    assert status == 200 and turned["size"] == [0.9, 2.0, 0.75] and (sofa()["width"], sofa()["depth"]) == (0.9, 2.0)
    status, said, _ = call(port, "PUT", put, raw=b"solid sofa", token=kim)
    assert status == 400 and "not a binary glTF" in said["error"] and sofa()["model"]

    # made again from its file in units chosen (a tenth of the size the type was: doubted), then as its
    # size says and stood up (drawn Z up: its depth was its height), its turn kept
    fit = "/api/catalogue/types/SOFA/model/fit"
    status, made, _ = call(port, "POST", fit, {"units": "mm"}, token=kim)
    assert status == 200 and (made["size"], made["units"], made["units_from"], made["units_chosen"], made["turn"]) == \
        ([0.09, 0.2, 0.075], "mm", "chosen", "mm", 90)
    assert any("times the size the type was" in x for x in made["doubts"]) and sofa()["depth"] == 0.2
    status, made, _ = call(port, "POST", fit, {"units": "auto", "up": "z"}, token=kim)
    assert status == 200 and (made["size"], made["units"], made["units_from"], made["units_chosen"], made["up"]) == \
        ([0.75, 2.0, 0.9], "cm", "size", None, "z") and made["doubts"] == [] and made["kind"] == "glTF"
    assert call(port, "POST", fit, {"units": "furlong"}, token=kim)[0] == 400
    assert call(port, "POST", fit, {"up": "z"}, token=tokens["bob"])[0] == 403

    # taken away: drawn by its shape again
    assert call(port, "POST", "/api/catalogue/types/SOFA/model/delete", {}, token=kim)[0] == 200
    assert sofa()["model"] is None and call(port, "GET", "/api/catalogue/models/SOFA.glb", token=kim)[0] == 404
    assert [e["action"] for e in accounts.audit_tail() if e["action"].startswith("item type model")] == \
        ["item type model removed", "item type model refitted", "item type model refitted", "item type model turned",
         "item type model added"]


def test_a_project_file_carries_the_types_3d_models_to_a_studio_without_them(studio):
    # A project sent to another Studio takes its item types' 3D models with it, each with
    # the file it was made from and how (here turned a quarter): opened where the type has
    # no model, it is made again as it was, and can be turned there too; where the type
    # has one, that one is kept
    from urllib.parse import quote

    from test_models import sofa_in_cm

    port, st, _, tokens, code = studio
    boss = tokens["boss"]
    assert call(port, "PUT", "/api/catalogue/types/SOFA/model?name=sofa.glb", raw=sofa_in_cm(), token=boss)[0] == 200
    assert call(port, "POST", "/api/catalogue/types/SOFA/model/turn", {"turn": 90}, token=boss)[0] == 200
    sent = call(port, "GET", "/api/catalogue/models/SOFA.glb", token=boss)[1]
    status, blob, _ = call(port, "GET", f"/api/projects/{code}/project.storeypath-project", token=boss)
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert sorted(n for n in z.namelist() if n.startswith("models/")) == \
            ["models/SOFA.glb", "models/SOFA.json", "models/SOFA.source"]
        assert z.read("models/SOFA.glb") == sent and z.read("models/SOFA.source") == sofa_in_cm()
        assert json.loads(z.read("models/SOFA.json"))["turn"] == 90

    # (the other Studio: this one without the model)
    assert call(port, "POST", "/api/catalogue/types/SOFA/model/delete", {}, token=boss)[0] == 200
    again = f"/api/open?replace={quote(st.store.name(code))}"
    status, opened, _ = call(port, "PUT", again, raw=blob, token=boss)
    assert status == 200 and (opened["models_added"], opened["models_not_added"]) == (["SOFA"], []), opened
    assert call(port, "GET", "/api/catalogue/models/SOFA.glb", token=boss)[1] == sent
    made = call(port, "GET", "/api/catalogue/models", token=boss)[1]["SOFA"]
    assert (made["name"], made["turn"], made["units"], made["size"]) == ("sofa.glb", 90, "cm", [0.9, 2.0, 0.75])
    status, turned, _ = call(port, "POST", "/api/catalogue/types/SOFA/model/turn", {"turn": 0}, token=boss)
    assert status == 200 and turned["size"] == [2.0, 0.9, 0.75]  # (made from the file it came from)

    status, opened, _ = call(port, "PUT", again, raw=blob, token=boss)
    assert status == 200 and (opened["models_added"], opened["models_not_added"]) == ([], [])
    assert call(port, "GET", "/api/catalogue/models", token=boss)[1]["SOFA"]["turn"] == 0  # its own, kept


def test_a_type_given_a_model_in_another_format_says_what_was_left_out(studio):
    from storeypath.model_files import assimp_command
    from test_model_files import obj

    if assimp_command() is None:
        pytest.skip("Assimp is not installed (brew install assimp; apt install assimp-utils)")
    port, _, _, tokens, _ = studio
    o, _ = obj()  # its .mtl not sent
    status, made, _ = call(port, "PUT", "/api/catalogue/types/SOFA/model?name=sofa.obj", raw=o.encode(), token=tokens["kim"])
    assert status == 200 and (made["kind"], made["size"], made["units"]) == ("OBJ", [2.0, 0.9, 0.75], "cm")
    assert "its materials (sofa.mtl) were not sent" in made["notes"][0]
    status, said, _ = call(port, "PUT", "/api/catalogue/types/SOFA/model?name=sofa.skp", raw=b"\xff\xfeSketchUp", token=tokens["kim"])
    assert status == 400 and "export it from SketchUp Pro" in said["error"]
