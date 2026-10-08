"""Backups (backup.py): the whole data folder as one .tar.gz, written as it is read, and
put back only into an empty folder, refusing anything that would land outside it."""

import io
import os
import sqlite3
import tarfile
import threading
import time

import pytest

from storeypath import accounts as acc
from storeypath.accounts import Accounts
from storeypath.backup import ROOT, jobs_paused, restore, write_backup


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    d = tmp_path / "data"
    (d / "K7Q2XM" / "drawings").mkdir(parents=True)
    (d / "K7Q2XM" / "K7Q2XM.spproj").write_text('{"project": {"code": "K7Q2XM"}}')
    (d / "K7Q2XM" / "drawings" / "drawing-1.dxf").write_bytes(b"0\nSECTION\n" * 1000)
    (d / "K7Q2XM" / "drawings" / ".incoming-abc.dxf").write_bytes(b"sent, not yet cleaned: private")
    (d / "K7Q2XM" / ".storeypath-cache" / "prints").mkdir(parents=True)
    (d / "K7Q2XM" / ".storeypath-cache" / "prints" / "x.png").write_bytes(b"png")
    (d / "catalogue.json").write_text("{}")
    (d / "half.json.tmp").write_text("half written")
    a = Accounts(d)
    a.add_user("boss", "a long password", role="admin", must_change_password=False)
    a.set_owner("K7Q2XM", a.by_username("boss").id)
    a.audit("login", a.by_username("boss"), "1.2.3.4")
    os.symlink("/etc/passwd", d / "K7Q2XM" / "link.txt")
    return d


def names(blob: bytes) -> set[str]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {m.name for m in tar.getmembers()}


def test_a_backup_holds_the_projects_users_sharing_and_audit_and_nothing_half_done(data):
    out = io.BytesIO()
    counts = write_backup(data, out)
    got = names(out.getvalue())
    assert {f"{ROOT}/catalogue.json", f"{ROOT}/studio.db",
            f"{ROOT}/K7Q2XM/K7Q2XM.spproj", f"{ROOT}/K7Q2XM/drawings/drawing-1.dxf"} <= got
    assert not any(n.endswith((".tmp", ".lock")) or ".incoming-" in n or ".storeypath-cache" in n
                   or n.endswith("link.txt") for n in got), got
    assert counts["files"] == len([n for n in got if "." in n.rsplit("/", 1)[-1]])
    with tarfile.open(fileobj=io.BytesIO(out.getvalue()), mode="r:gz") as tar:
        assert all(m.isfile() or m.isdir() for m in tar.getmembers())
        assert all(m.uid == 0 and m.uname == "" for m in tar.getmembers())  # nobody's names of this computer
        assert not any(m.name.endswith(("-wal", "-shm", "-journal")) for m in tar.getmembers())
        snapshot = tar.extractfile(f"{ROOT}/studio.db").read()
    copy = data.parent / "snapshot.db"
    copy.write_bytes(snapshot)
    with sqlite3.connect(copy) as db:  # whole by itself: no -wal beside it
        assert db.execute("SELECT username FROM users").fetchall() == [("boss",)]
        assert db.execute("SELECT action FROM audit").fetchall() == [("login",)]


class Pipe:
    """Something with write() alone, like a socket: no seeking, no telling."""

    def __init__(self):
        self.parts = []

    def write(self, b):
        self.parts.append(bytes(b))
        return len(b)


def test_a_backup_is_written_as_a_stream(data):
    pipe = Pipe()
    write_backup(data, pipe)
    assert len(pipe.parts) > 1 and f"{ROOT}/studio.db" in names(b"".join(pipe.parts))


def test_no_job_starts_while_a_backup_is_written(data):
    order = []
    with jobs_paused(data):
        t = threading.Thread(target=lambda: (jobs_paused(data).__enter__(), order.append("job")))
        t.start()
        time.sleep(0.3)
        order.append("backup done")
    t.join(5)
    assert order == ["backup done", "job"]
    with jobs_paused(data):  # held: another gives up after its timeout
        with pytest.raises(TimeoutError):
            with jobs_paused(data, timeout=0.3):
                pass


def test_restore_puts_it_all_back_into_an_empty_folder(data, tmp_path):
    archive = tmp_path / "b.tar.gz"
    with open(archive, "wb") as f:
        write_backup(data, f)
    target = tmp_path / "restored"
    counts = restore(archive, target)
    assert (target / "K7Q2XM" / "drawings" / "drawing-1.dxf").read_bytes() == \
        (data / "K7Q2XM" / "drawings" / "drawing-1.dxf").read_bytes()
    back = Accounts(target)
    assert back.by_username("boss").role == "admin" and back.login("boss", "a long password")
    assert back.project_access("K7Q2XM").owner == back.by_username("boss").id
    assert oct(os.stat(target / "studio.db").st_mode & 0o777) == oct(0o600)
    assert counts["files"] == 4  # the catalogue, the accounts, the project and its drawing
    with pytest.raises(ValueError, match="not empty"):
        restore(archive, target)
    with pytest.raises(ValueError, match="not empty"):
        restore(archive, data)


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
def test_restore_refuses_what_would_land_outside_or_is_not_a_plain_file(tmp_path, members):
    target = tmp_path / "restored"
    with pytest.raises(ValueError):
        restore(evil(tmp_path, *members), target)
    assert not target.exists() or not any(target.iterdir())
    assert not (tmp_path / "outside.txt").exists()
