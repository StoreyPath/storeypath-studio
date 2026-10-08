"""`storeypath users`, `backup` and `restore`: the accounts of a data folder changed from
the command line (while Studio runs, too), and the folder backed up and put back."""

import gzip
import io
import tarfile

import pytest
from typer.testing import CliRunner

from storeypath import accounts as acc
from storeypath.accounts import Accounts
from storeypath.backup import ROOT
from storeypath.cli import app

PASSWORD = "a long enough one"


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)


def run(*args, input=None):
    return CliRunner().invoke(app, [str(a) for a in args], input=input)


def test_users_are_added_listed_and_changed_from_the_command_line(tmp_path):
    studio = Accounts(tmp_path)  # Studio, running on the folder meanwhile
    r = run("users", "add", "Boss", "--role", "admin", "--name", "The Boss", "--data", tmp_path, "--permanent",
            input=f"{PASSWORD}\n{PASSWORD}\n")
    assert r.exit_code == 0, r.output
    boss = studio.by_username("boss")
    assert (boss.role, boss.name, boss.must_change_password) == ("admin", "The Boss", False)
    assert studio.login("boss", PASSWORD)[1].id == boss.id
    r = run("users", "add", "eng", "--role", "engineer", "--capability", "backup", "--capability", "catalogue",
            "--data", tmp_path, "--password-stdin", input=f"{PASSWORD}\n")
    assert r.exit_code == 0, r.output
    eng = studio.by_username("eng")
    assert eng.capabilities == ["backup", "catalogue"] and eng.must_change_password  # temporary, by default
    r = run("users", "add", "x1", "--data", tmp_path, "--password-stdin", input="short\n")
    assert r.exit_code == 0, r.output  # any password the person likes
    r = run("users", "add", "x2", "--data", tmp_path, "--password-stdin", input="\n")
    assert r.exit_code == 1 and "choose a password" in r.output
    r = run("users", "add", "eng", "--data", tmp_path, "--password-stdin", input=f"{PASSWORD}\n")
    assert r.exit_code == 1 and "already" in r.output
    r = run("users", "add", "bob", "--role", "king", "--data", tmp_path, "--password-stdin", input=f"{PASSWORD}\n")
    assert r.exit_code == 1 and "role" in r.output
    r = run("users", "list", "--data", tmp_path)
    assert r.exit_code == 0 and "boss" in r.output and "engineer" in r.output and "backup,catalogue" in r.output

    token = studio.start_session(studio.by_username("eng"))
    r = run("users", "passwd", "eng", "--data", tmp_path, "--password-stdin", "--permanent",
            input="another long one\n")
    assert r.exit_code == 0, r.output
    assert studio.session(token) is None  # its sessions ended, in the running Studio too
    assert studio.login("eng", "another long one")[1].must_change_password is False
    token = studio.start_session(studio.by_username("eng"))
    assert run("users", "role", "eng", "user", "--data", tmp_path).exit_code == 0
    assert studio.by_username("eng").role == "user" and studio.session(token) is None
    assert run("users", "disable", "eng", "--data", tmp_path).exit_code == 0
    with pytest.raises(acc.Unauthorized):
        studio.login("eng", "another long one")
    assert run("users", "enable", "eng", "--data", tmp_path).exit_code == 0
    assert studio.login("eng", "another long one")
    r = run("users", "disable", "boss", "--data", tmp_path)
    assert r.exit_code == 1 and "admin" in r.output  # the last admin who can log in
    assert run("users", "role", "nobody", "admin", "--data", tmp_path).exit_code == 1
    actions = [e["action"] for e in studio.audit_tail()]
    assert {"user created", "password reset", "user changed", "user disabled"} <= set(actions)


def test_the_command_line_backs_up_and_restores(tmp_path):
    from storeypath.db.store import ProjectStore
    from storeypath import db as studio_db
    from storeypath.workspace import Workspace

    data = tmp_path / "data"
    data.mkdir()
    ws = Workspace.new("Kept")
    ws.add_location("SITE", "Site")
    ProjectStore(studio_db.connect(data=data)).create(ws)
    Accounts(data).add_user("boss", PASSWORD, role="admin", must_change_password=False)
    out = tmp_path / "copy.sql.gz"
    r = run("backup", "--data", data, "--out", out)
    assert r.exit_code == 0, r.output
    dump = gzip.decompress(out.read_bytes()).decode()
    assert "COPY storeypath.users (" in dump and ws.id in dump
    assert any(e["action"] == "backup" for e in Accounts(data).audit_tail())
    r = run("backup", "--data", data, "--out", data / "inside.sql.gz")
    assert r.exit_code == 1 and "outside" in r.output
    r = run("restore", out, "--data", tmp_path / "new")
    assert r.exit_code == 0, r.output
    assert Accounts(tmp_path / "new").login("boss", PASSWORD)[1].role == "admin"
    assert ProjectStore(studio_db.connect(data=tmp_path / "new")).current(ws.id) == ws
    r = run("restore", out, "--data", tmp_path / "new")
    assert r.exit_code == 1 and "empty" in r.output
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo(f"{ROOT}/../x")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    r = run("restore", evil, "--data", tmp_path / "n2")
    assert r.exit_code == 1 and "outside" in r.output
