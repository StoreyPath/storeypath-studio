"""Backups (backup.py): Studio's whole database as one .sql.gz, one moment's, written as
it is read; put back only into an empty database. An older Studio's backup (its data
folder, a .tar.gz) is brought in as its folder, refusing anything that would land
outside it."""

import gzip
import hashlib
import io
import tarfile

import pytest

from old_studio import studio_db
from storeypath import accounts as acc
from storeypath import db as studio_db_module
from storeypath.accounts import Accounts
from storeypath.backup import FIRST_LINE, ROOT, restore, write_backup
from storeypath.db.store import ProjectStore
from storeypath.review import Review, StoredProject
from storeypath.workspace import ExportRecord

PASSWORD = "a long password"


@pytest.fixture
def data(converted, tmp_path, monkeypatch):
    """A Studio's database: a project with a correction, a drawing, a package sent and a
    drawing sent and not yet kept; an admin who owns it, logged in; the audit log."""
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    ws, d, f_id, *_ = converted
    ws.floor(f_id).source.path = "drawings/level-2.dxf"
    ws.exports.append(ExportRecord(sequence=1, exported_at=ws.project.created_at, file="p-001.storeypath"))
    folder = tmp_path / "data"
    store = ProjectStore(studio_db_module.connect(data=folder))
    store.create(ws, drawings={"level-2.dxf": ((d / "level-2.dxf").read_bytes(), "its words")},
                 export_bytes={"p-001.storeypath": b"PK the package as sent"})
    store.put_incoming(ws.id, ".incoming-abc.dxf", b"sent, not yet cleaned: private")
    space = next(r.id for r in ws.floor_objects(f_id) if r.kind == "space")
    Review(StoredProject(store, ws.id, folder / "cache")).correct(space, {"hidden": True})
    a = Accounts(folder)
    boss = a.add_user("boss", PASSWORD, role="admin", must_change_password=False)
    a.set_owner(ws.id, boss.id)
    a.audit("login", boss, "1.2.3.4")
    token = a.start_session(boss)
    return folder, store, ws, token


def text(blob: bytes) -> str:
    return gzip.decompress(blob).decode()


def test_a_backup_holds_everything_but_sessions_and_what_is_half_done(data):
    folder, store, ws, token = data
    out = io.BytesIO()
    counts = write_backup(store.db, out)
    dump = text(out.getvalue())
    assert dump.startswith(FIRST_LINE + "\n-- {")
    for table in ("users", "projects", "objects", "overrides", "drawings", "exports", "audit", "history", "catalogue"):
        assert f"COPY storeypath.{table} (" in dump
    assert "COPY storeypath.sessions" not in dump and hashlib.sha256(token.encode()).hexdigest() not in dump
    assert "not yet cleaned" not in dump and ".incoming-abc.dxf" not in dump  # its private information is in it
    assert "\\\\x" in dump  # (bytes, as COPY writes them)
    assert counts["rows"] > 50 and counts["tables"] == 18
    assert Accounts(folder).session(token) is not None  # the sessions of the Studio backed up are as they were


class Pipe:
    """Something with write() alone, like a socket: no seeking, no telling."""

    def __init__(self):
        self.parts = []

    def write(self, b):
        self.parts.append(bytes(b))
        return len(b)


def test_a_backup_is_written_as_a_stream(data):
    folder, store, *_ = data
    pipe = Pipe()
    write_backup(store.db, pipe)
    assert len(pipe.parts) > 1 and text(b"".join(pipe.parts)).startswith(FIRST_LINE)


def test_restore_puts_it_all_back_into_an_empty_database(data, tmp_path, databases):
    folder, store, ws, token = data
    archive = tmp_path / "b.sql.gz"
    with open(archive, "wb") as f:
        write_backup(store.db, f)
    target = databases.url(tmp_path / "restored")
    counts = restore(archive, target)
    assert counts["rows"] > 50
    back = ProjectStore(studio_db_module.connect(target))
    assert back.current(ws.id).model_dump(mode="json") == store.current(ws.id).model_dump(mode="json")
    assert back.drawing_bytes(ws.id, "level-2.dxf") == store.drawing_bytes(ws.id, "level-2.dxf")
    assert back.words(ws.id, "level-2.dxf") == "its words"
    assert back.export_bytes(ws.id, "p-001.storeypath") == b"PK the package as sent"
    assert back.drawings(ws.id, incoming=True) == []
    assert [e["kind"] for e in back.history(ws.id)] == [e["kind"] for e in store.history(ws.id)]
    accounts = Accounts(tmp_path / "restored")
    boss = accounts.by_username("boss")
    assert boss.role == "admin" and accounts.login("boss", PASSWORD)
    assert accounts.project_access(ws.id).owner == boss.id
    assert accounts.session(token) is None  # nobody is logged in to it
    # every numbering goes on where it was: the next change and audit entry follow on
    Review(StoredProject(back, ws.id, tmp_path / "cache")).add_item(f"{ws.id}-SITE-HQ-F02", {"type": "SOFA", "x": 1, "y": 1})
    assert back.history(ws.id)[0]["seq"] > store.history(ws.id)[0]["seq"]
    accounts.audit("backup", boss, "1.2.3.4")
    with pytest.raises(ValueError, match="empty"):
        restore(archive, target)
    with pytest.raises(ValueError, match="empty"):
        restore(archive, store.db.url)


def test_an_older_studios_backup_is_brought_in_as_its_folder(converted, tmp_path, databases, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    ws, d, f_id, *_ = converted
    old = tmp_path / "old"
    (old / ws.id / "drawings").mkdir(parents=True)
    (old / ws.id / "drawings" / "level-2.dxf").write_bytes((d / "level-2.dxf").read_bytes())
    ws.floor(f_id).source.path = "drawings/level-2.dxf"
    ws.save(old / ws.id / f"{ws.id}.spproj")
    studio_db(old, PASSWORD, owner_of=[ws.id])
    archive = tmp_path / "old.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(old, arcname=ROOT)
    counts = restore(archive, databases.url(tmp_path / "restored"))
    assert counts["projects"] == 1 and counts["users"] == 3
    back = ProjectStore(studio_db_module.connect(data=tmp_path / "restored"))
    assert back.current(ws.id).model_dump(mode="json") == ws.model_dump(mode="json")
    assert Accounts(tmp_path / "restored").login("boss", PASSWORD)


def evil(tmp_path, *members):
    path = tmp_path / "evil.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = 2
                tar.addfile(info, io.BytesIO(b"hi"))
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, f"{ROOT}/a"
                tar.addfile(info)
            elif kind == "device":
                info.type = tarfile.CHRTYPE
                tar.addfile(info)
    return path


@pytest.mark.parametrize("members", [
    [(f"{ROOT}/../outside.txt", "file")],
    [("/etc/cron.d/x", "file")],
    [(f"{ROOT}/link", "symlink")],
    [(f"{ROOT}/a", "file"), (f"{ROOT}/b", "hardlink")],
    [(f"{ROOT}/dev", "device")],
    [("elsewhere/x.txt", "file")],
    [],
])
def test_restore_refuses_what_would_land_outside_or_is_not_a_plain_file(tmp_path, members, databases):
    target = databases.url(tmp_path / "restored")
    with pytest.raises(ValueError):
        restore(evil(tmp_path, *members), target)
    assert ProjectStore(studio_db_module.connect(target)).codes() == []
    assert not (tmp_path / "outside.txt").exists()


def test_a_file_that_is_no_backup_is_refused(tmp_path, databases):
    junk = tmp_path / "junk.sql.gz"
    junk.write_bytes(gzip.compress(FIRST_LINE.encode() + b'\n-- {"format": "storeypath-backup", "migration": 1}\n'
                                   b"DROP TABLE storeypath.users;\n"))
    with pytest.raises(ValueError, match="not a StoreyPath backup"):
        restore(junk, databases.url(tmp_path / "restored"))
