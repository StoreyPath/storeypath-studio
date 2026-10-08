"""An older Studio's data folder brought into the database (db/importer.py): `storeypath
db import`, and Studio's first start on an empty database. Projects with their
drawings and packages, the item types, and studio.db's accounts, owners, sharing and
audit log come in; again, nothing comes in twice; the folder is left as it was."""

import hashlib
import shutil

import pytest
from typer.testing import CliRunner

from old_studio import studio_db
from storeypath import accounts as acc
from storeypath import catalogue
from storeypath import db as studio_db_module
from storeypath.accounts import Accounts
from storeypath.cli import app
from storeypath.db.importer import import_folder
from storeypath.db.store import ProjectStore
from storeypath.server import Studio, write_valid_package
from storeypath.workspace import Workspace

PASSWORD = "an old password"


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture
def old(converted, tmp_path, monkeypatch):
    """A data folder of an older Studio: one project (its drawing in drawings/, a package
    exported and one kept from before records, a profile file of its own), its item types
    with one of its own, and studio.db."""
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    ws, d, f_id, b_id, *_ = converted
    data = tmp_path / "old"
    folder = data / ws.id
    (folder / "drawings").mkdir(parents=True)
    shutil.copy(d / "level-2.dxf", folder / "drawings" / "level-2.dxf")
    (folder / "drawings" / "level-2.dxf.words.txt").write_text("words left in it")
    (folder / "drawings" / "spare.dxf").write_bytes(b"0\nEOF\n")
    (folder / "drawings" / ".incoming-abc.dxf").write_bytes(b"sent, never kept")
    ws.floor(f_id).source.path = "drawings/level-2.dxf"
    ws.add_item("PLANT", f_id, 1.0, 1.0)
    cat = catalogue.default_catalogue()
    cat.types.append(catalogue.ItemType(code="PLANT", name_en="Plant"))
    catalogue.save(data, cat)
    write_valid_package(ws, folder / f"{ws.id}.spproj", folder / "exports" / f"{ws.id}-001-HQ.storeypath",
                        b_id, cat, lambda line: None)
    (folder / "exports" / "before-records.storeypath").write_bytes(b"PK an old package")
    ws = Workspace.load(folder / f"{ws.id}.spproj")
    ids = studio_db(data, PASSWORD, owner_of=[ws.id], shared=[(ws.id, "floor", f_id)])
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in data.rglob("*") if p.is_file()}
    return data, ws, f_id, ids, before


def test_a_data_folder_comes_in_whole_and_only_once(old, tmp_path):
    data, ws, f_id, ids, before = old
    db = studio_db_module.connect(data=tmp_path / "new")
    store = ProjectStore(db)
    report = import_folder(data, db, store)
    assert report["projects"] == [ws.id] and report["drawings"] == 2 and report["packages"] == 2
    assert (report["users"], report["grants"], report["audit"], report["catalogue"]) == (3, 1, 1, True)
    back = store.current(ws.id)
    assert back.model_dump(mode="json") == ws.model_dump(mode="json")  # the drawing's path was drawings/ already
    assert {d["name"] for d in store.drawings(ws.id)} == {"level-2.dxf", "spare.dxf"}  # not one sent and never kept
    assert store.drawing_bytes(ws.id, "level-2.dxf") == (data / ws.id / "drawings" / "level-2.dxf").read_bytes()
    assert store.words(ws.id, "level-2.dxf") == "words left in it"
    package = f"{ws.id}-001-HQ.storeypath"
    assert store.export_bytes(ws.id, package) == (data / ws.id / "exports" / package).read_bytes()
    assert set(store.export_files(ws.id)) == {package, "before-records.storeypath"}
    assert store.export_bytes(ws.id, "before-records.storeypath") == b"PK an old package"
    assert store.catalogue().get("PLANT").name_en == "Plant"
    accounts = Accounts(tmp_path / "new")
    assert accounts.login("vera", PASSWORD)[1].id == ids["vera"]  # the same ids, the same passwords
    assert accounts.by_username("eng").capabilities == ["backup"]
    access = accounts.project_access(ws.id)
    assert access.owner == ids["boss"] and [(g.user, g.scope.id, g.level) for g in access.grants] == \
        [(ids["vera"], f_id, "view")]
    assert accounts.audit_tail()[-1]["action"] == "login"
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM sessions WHERE token_hash = 'abc'").fetchone() == (0,)
    history = store.history(ws.id)
    assert [(e["kind"], e["who"]) for e in history] == [("import", "command line")]
    # again: nothing comes in twice
    again = import_folder(data, db, store)
    assert again["projects"] == [] and [s["code"] for s in again["skipped"]] == [ws.id]
    assert (again["users"], again["grants"], again["audit"]) == (0, 0, 0)
    assert len(accounts.audit_tail()) == 2  # (the login above, and the one from studio.db)
    # and the folder is as it was
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in data.rglob("*") if p.is_file()}


def test_studio_brings_the_folder_in_at_its_first_start(old):
    data, ws, f_id, ids, _ = old
    studio = Studio(data, model=NoModel(), warm=False)
    assert studio.imported["projects"] == [ws.id] and studio.store.codes() == [ws.id]
    assert studio.review(ws.id).floor(f_id)["items"][0]["type"] == "PLANT"
    assert Studio(data, model=NoModel(), warm=False).imported is None  # the database has projects now
    assert not (data / "cache").exists() or not any((data / "cache").iterdir())


def test_the_command_line_imports_a_folder(old, databases):
    data, ws, *_ = old
    r = CliRunner().invoke(app, ["db", "import", "--data", str(data)])
    assert r.exit_code == 0, r.output
    assert f"{ws.id} (" in r.output and "3 users" in r.output
    assert ProjectStore(studio_db_module.connect(data=data)).codes() == [ws.id]
    r = CliRunner().invoke(app, ["db", "import", "--data", str(data)])
    assert r.exit_code == 0 and "in the database already" in r.output
    r = CliRunner().invoke(app, ["db", "url"])
    assert r.exit_code == 0 and "spa_" in r.output and ":***@" in r.output
