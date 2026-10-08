import os
import threading
from pathlib import Path

import psycopg
import pytest

from storeypath import db as studio_db
from storeypath.convert import convert_floor
from storeypath.samples import office_floor, write_floor_dxf
from storeypath.workspace import Placement, SourceDrawing, Workspace

# The PostgreSQL (with PostGIS) the tests make their databases on: each data folder a
# test uses gets a database of its own, made from a template that has the schema, and
# dropped after the test. Only databases named spa_… are made and dropped.
SERVER_URL = os.environ.get("STOREYPATH_TEST_DATABASE_URL",
                            "postgresql://storeypath:storeypath@127.0.0.1:55470/postgres")
PREFIX = f"spa_{os.getpid()}_"


def _url(name: str) -> str:
    return psycopg.conninfo.make_conninfo(SERVER_URL, dbname=name)


def _admin():
    return psycopg.connect(SERVER_URL, autocommit=True)


@pytest.fixture(scope="session")
def database_template():
    """A database with Studio's schema, to make each test's from."""
    name = f"{PREFIX}template"
    with _admin() as conn:
        _drop_stale(conn)
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    studio_db.migrate(_url(name))
    yield name
    with _admin() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _drop_stale(conn) -> None:
    """Databases a run of the tests that stopped part way left behind (its process gone)."""
    for (name,) in conn.execute("SELECT datname FROM pg_database WHERE datname LIKE 'spa\\_%'").fetchall():
        try:
            pid = int(name.split("_")[1])
        except (IndexError, ValueError):
            continue
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        except PermissionError:
            pass


class Databases:
    """The databases of one test, one a data folder, made when first asked for."""

    _count = 0
    _count_lock = threading.Lock()

    def __init__(self, template: str, wider: "Databases | None" = None):
        self.template = template
        self.wider = wider  # the session's: a folder that has one there keeps it
        self.made: dict[str, str] = {}  # data folder -> its database's URL
        self._lock = threading.Lock()

    def url(self, data=None) -> str:
        key = str(Path(data).resolve()) if data is not None else ""
        if self.wider is not None and key in self.wider.made:
            return self.wider.made[key]
        with self._lock:
            if key not in self.made:
                with Databases._count_lock:
                    Databases._count += 1
                    name = f"{PREFIX}{Databases._count}"
                with _admin() as conn:
                    conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{self.template}" STRATEGY FILE_COPY')
                self.made[key] = _url(name)
            return self.made[key]

    def drop(self) -> None:
        for url in self.made.values():
            studio_db.forget(url)
        with _admin() as conn:
            for url in self.made.values():
                name = psycopg.conninfo.conninfo_to_dict(url)["dbname"]
                assert name.startswith(PREFIX)
                conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        self.made.clear()


    def env(self, data=None) -> dict:
        """The environment of a process (``storeypath serve``…) that is to use the
        database of ``data``."""
        return {**os.environ, studio_db.URL_ENV: self.url(data)}


@pytest.fixture(scope="session", autouse=True)
def session_databases(database_template):
    """The databases of what a module or the session sets up (a Studio a module's tests
    share), one a data folder, dropped at the end."""
    dbs = Databases(database_template)
    patch = pytest.MonkeyPatch()
    patch.setattr(studio_db, "default_url", dbs.url)
    patch.delenv(studio_db.URL_ENV, raising=False)
    yield dbs
    patch.undo()
    dbs.drop()


@pytest.fixture(autouse=True)
def databases(database_template, session_databases, monkeypatch):
    """Each data folder a test's Studio, accounts or store uses: a database of its own
    (STOREYPATH_DATABASE_URL is not read)."""
    dbs = Databases(database_template, wider=session_databases)
    monkeypatch.setattr(studio_db, "default_url", dbs.url)
    yield dbs
    dbs.drop()


@pytest.fixture
def workspace(tmp_path: Path):
    """A one-building project with floor 2 drawn from the sample office plan.
    Returns (workspace, workspace dir, floor id, building id, sample cells)."""
    cells = office_floor(2)
    write_floor_dxf(tmp_path / "level-2.dxf", cells)
    ws = Workspace.new("Test Project")
    loc = ws.add_location("SITE", "Test Site")
    b_id = ws.add_building(loc, "HQ", "Headquarters")
    ws.building(b_id).placement = Placement(lon=10.0, lat=50.0, x=125, y=48, bearing=0)
    f_id = ws.add_floor(b_id, 2, source=SourceDrawing(path="level-2.dxf"))
    return ws, tmp_path, f_id, b_id, cells


@pytest.fixture
def converted(workspace):
    ws, d, f_id, b_id, cells = workspace
    report = convert_floor(ws, f_id, d)
    return ws, d, f_id, b_id, cells, report
