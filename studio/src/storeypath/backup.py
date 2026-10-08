"""The whole data folder as one file, and a data folder from one.

A backup (``storeypath-backup-<UTC yyyymmdd-HHMMSS>.tar.gz``) holds every project
folder (its workspace, drawings, corrections and exports), the catalogue of item
types, and studio.db: the accounts (with the passwords' scrypt hashes: a restore needs
them), who each project is shared with and the audit log, all under one folder,
``storeypath-data/``. Whoever may download one therefore holds every password's hash
and the whole audit log. The database goes in as a snapshot taken whole (sqlite3's
backup), never as the file in use nor its -wal and -shm, and with no session in it
(blanked in the copy, never in the database in use). Studio's certificate and its key
(tls/) never go in: a Studio restored makes its own (browsers warn once about it).
A backup leaves out what is half done or made again when needed: files being written
(*.tmp, *.part), lock files, drawings sent and not yet kept (their private information
is still in them), packages and projects being written or unpacked, and the prints
Studio draws of floors (.storeypath-cache). Nothing but regular files and folders goes
in: a link is neither followed nor kept.

It is written as it is read (a gzip'd tar stream), so a large folder needs no copy on
the disk. While it is written, no job starts (jobs.lock, held by Studio's job and by a
backup, here or from the command line): no project is caught half written by a job;
the review editor's changes replace each file whole.

``restore`` puts a backup into an empty folder: a member with an absolute path, ``..``,
a link or a device is refused, and the whole file is checked before anything is written.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import tarfile
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

try:
    import fcntl
except ImportError:
    fcntl = None

ROOT = "storeypath-data"  # every member is under this folder
JOBS_LOCK = "jobs.lock"  # held while a job runs, and while a backup is written
SKIP_SUFFIXES = (".tmp", ".part", ".lock")
SKIP_PREFIXES = (".incoming-", ".writing-", ".opening-", ".unpacking-", ".replaced-")
SKIP_DIRS = (".storeypath-cache",)
DB_FILE = "studio.db"  # accounts.DB_FILE: put in as a snapshot, never as it is on the disk
DB_FILES = (DB_FILE, DB_FILE + "-wal", DB_FILE + "-shm", DB_FILE + "-journal")
TLS_FOLDER = "tls"  # tls.FOLDER: Studio's certificate and key, never in a backup
_THREADS: dict[str, threading.Lock] = {}  # where there is no flock: one process's threads
_THREADS_LOCK = threading.Lock()


def backup_name(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"storeypath-backup-{now:%Y%m%d-%H%M%S}.tar.gz"


@contextmanager
def jobs_paused(data: Path, timeout: float | None = None):
    """The data folder's job lock: held by Studio while a job runs, and by a backup
    while it is written (another process's too). ``timeout`` (seconds): TimeoutError
    when it is not had by then."""
    path = Path(data) / JOBS_LOCK
    deadline = None if timeout is None else time.monotonic() + timeout
    if fcntl is None:  # one process: its threads
        with _THREADS_LOCK:
            lock = _THREADS.setdefault(str(path.resolve()), threading.Lock())
        if not lock.acquire(timeout=-1 if timeout is None else timeout):
            raise TimeoutError("a job is running")
        try:
            yield
        finally:
            lock.release()
        return
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)  # an open file of its own: flock tells threads apart too
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if deadline is not None else 0))
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("a job is running") from None
                time.sleep(0.2)
        yield
    finally:
        os.close(fd)


def _kept(name: str, is_dir: bool) -> bool:
    if is_dir:
        return name not in SKIP_DIRS and not name.startswith(SKIP_PREFIXES)
    return not name.endswith(SKIP_SUFFIXES) and not name.startswith(SKIP_PREFIXES)


def members(data: Path):
    """(path, name in the backup) of what a backup holds, folders before what is in them."""
    data = Path(data)

    def walk(folder: Path, rel: PurePosixPath):
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            try:
                mode = e.stat(follow_symlinks=False).st_mode
            except OSError:
                continue
            if stat.S_ISDIR(mode):
                if _kept(e.name, True) and not (folder == data and e.name == TLS_FOLDER):
                    yield Path(e.path), rel / e.name
                    yield from walk(Path(e.path), rel / e.name)
            elif stat.S_ISREG(mode) and _kept(e.name, False) and not (folder == data and e.name in DB_FILES):
                yield Path(e.path), rel / e.name
            # links, sockets, devices: not kept

    yield data, PurePosixPath(ROOT)
    yield from walk(data, PurePosixPath(ROOT))


def write_backup(data: Path, out) -> dict:
    """The data folder written to ``out`` (a file opened for writing, or anything with
    write()) as a gzip'd tar stream. How many files and bytes it holds."""
    files = size = 0

    def add(tar, info, f=None) -> None:
        info.uid = info.gid = 0  # nobody's names of this computer
        info.uname = info.gname = ""
        tar.addfile(info, f)

    with tarfile.open(fileobj=out, mode="w|gz") as tar:
        for path, name in members(data):
            try:
                info = tar.gettarinfo(str(path), arcname=str(name))
            except OSError:
                continue  # gone meanwhile
            if info.isdir():
                add(tar, info)
            elif info.isreg():
                try:
                    with open(path, "rb") as f:
                        add(tar, info, f)
                except (OSError, tarfile.TarError):
                    continue
                files, size = files + 1, size + info.size
            if str(name) == ROOT and (Path(data) / DB_FILE).is_file():
                # the accounts, as they are now: a snapshot, whole whatever is being written
                fd, tmp = tempfile.mkstemp(prefix="storeypath-db-", suffix=".tmp")
                os.close(fd)
                try:
                    source, copy = sqlite3.connect(Path(data) / DB_FILE), sqlite3.connect(tmp)
                    try:
                        source.backup(copy)
                        source.close()
                        # no session goes in (in the copy alone), nor what is left of one
                        # in the file's free pages
                        if copy.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
                                        ).fetchone():
                            copy.execute("DELETE FROM sessions")
                            copy.commit()
                            copy.execute("VACUUM")
                    finally:
                        copy.close()
                        source.close()
                    snapshot = tar.gettarinfo(tmp, arcname=f"{ROOT}/{DB_FILE}")
                    snapshot.mode = 0o600
                    with open(tmp, "rb") as f:
                        add(tar, snapshot, f)
                    files, size = files + 1, size + snapshot.size
                finally:
                    os.unlink(tmp)
    return {"files": files, "bytes": size}


def check_backup(archive: Path) -> list[tarfile.TarInfo]:
    """The members of a backup, each named as it goes into the data folder; ValueError
    when any is not a regular file or folder under storeypath-data/, or has an absolute
    path or .. in it."""
    out = []
    with tarfile.open(archive, "r:*") as tar:
        for m in tar.getmembers():
            p = PurePosixPath(m.name)
            if p.is_absolute() or m.name.startswith("/") or "\\" in m.name or ".." in p.parts:
                raise ValueError(f"not a StoreyPath backup: {m.name!r} would land outside the folder")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"not a StoreyPath backup: {m.name!r} is a link or a device")
            if not p.parts or p.parts[0] != ROOT:
                raise ValueError(f"not a StoreyPath backup: {m.name!r} is not in {ROOT}/")
            out.append(m)
    if not out:
        raise ValueError("not a StoreyPath backup: it is empty")
    return out


def restore(archive: Path, data: Path) -> dict:
    """A backup put into ``data``, which must be empty (or not there yet): the projects,
    the catalogue, the accounts and who each project is shared with, as they were."""
    data = Path(data)
    if data.exists() and (not data.is_dir() or any(data.iterdir())):
        raise ValueError(f"{data} is not empty: restore into an empty folder")
    wanted = check_backup(archive)
    data.mkdir(parents=True, exist_ok=True)
    files = 0
    with tarfile.open(archive, "r:*") as tar:
        chosen = []
        for m in tar.getmembers():
            p = PurePosixPath(m.name)
            if len(p.parts) == 1:
                continue  # the folder itself: it is ``data``
            m.name = str(PurePosixPath(*p.parts[1:]))
            chosen.append(m)
            files += m.isfile()
        tar.extractall(data, members=chosen, filter="data")
    if (data / DB_FILE).exists():
        os.chmod(data / DB_FILE, 0o600)
    return {"files": files, "members": len(wanted)}
