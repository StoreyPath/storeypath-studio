"""A throwaway Studio for the README's pictures (docs/media/capture.mjs starts it):

- a database of its own, made on a PostgreSQL with PostGIS (STOREYPATH_MEDIA_SERVER, else
  the tests' 127.0.0.1:55470) and dropped when this ends;
- the demo campus in it (`storeypath demo`, all made up), and a project with no drawing
  yet, for a drawing to be dropped into;
- a few made-up people: an admin, Maya Chen (an engineer, the demo's owner), Omar Haddad
  (edits it), Lena Fischer (views it), each with a password drawn now;
- `storeypath serve --http` on 127.0.0.1.

It says on its first line, as JSON, where Studio is and who may log in, then serves until
its standard input closes (or it is stopped), and leaves nothing behind.

Run with Studio's Python: studio/.venv/bin/python docs/media/stage.py [--port 8798]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import psycopg

SERVER = os.environ.get("STOREYPATH_MEDIA_SERVER", "postgresql://storeypath:storeypath@127.0.0.1:55470/postgres")
PEOPLE = {  # username: (name, role, the demo's level)
    "admin": ("Studio Admin", "admin", None),
    "maya": ("Maya Chen", "engineer", "owner"),
    "omar": ("Omar Haddad", "user", "edit"),
    "lena": ("Lena Fischer", "user", "view"),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8798)
    args = ap.parse_args()

    name = f"sp_media_{os.getpid()}"
    with psycopg.connect(SERVER, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = psycopg.conninfo.make_conninfo(SERVER, dbname=name)
    folder = Path(tempfile.mkdtemp(prefix="storeypath-media-"))
    server = None
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    try:
        os.environ["STOREYPATH_DATABASE_URL"] = url
        from storeypath import db
        from storeypath.accounts import Accounts, Scope
        from storeypath.db.importer import import_folder
        from storeypath.db.store import ProjectStore
        from storeypath.samples import SHOWCASE_CODE, build_showcase
        from storeypath.workspace import Workspace

        database = db.connect(url)
        build_showcase(folder / "demo")
        import_folder(folder / "demo", database, accounts=False)
        store = ProjectStore(database)
        # projects with no drawing yet, for a drawing to be dropped into (a picture, and the animation)
        fresh, fresh2 = Workspace.new("New Campus"), Workspace.new("Harbour Campus")
        store.create(fresh)
        store.create(fresh2)
        accounts = Accounts(db=database)
        passwords, ids = {}, {}
        for username, (full, role, _) in PEOPLE.items():
            passwords[username] = secrets.token_urlsafe(18)
            ids[username] = accounts.add_user(username, passwords[username], name=full, role=role,
                                              must_change_password=False).id
        for code in (SHOWCASE_CODE, fresh.id, fresh2.id):
            accounts.set_owner(code, ids["maya"])
            for username, (_, _, level) in PEOPLE.items():
                if level in ("view", "edit"):
                    accounts.set_grant(code, ids[username], Scope(kind="project"), level, ids["admin"])
        db.forget(url)

        data = folder / "data"
        data.mkdir()
        server = subprocess.Popen([str(Path(sys.executable).parent / "storeypath"), "serve", "--data", str(data), "--http",
                                   "--port", str(args.port)], env={**os.environ, "STOREYPATH_DATABASE_URL": url},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        address = f"http://127.0.0.1:{args.port}"
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"{address}/login.html", timeout=2)
                break
            except OSError:
                if time.monotonic() > deadline or server.poll() is not None:
                    raise SystemExit("Studio did not start")
                time.sleep(0.3)
        print(json.dumps({"address": address, "demo": SHOWCASE_CODE, "fresh": fresh.id, "fresh2": fresh2.id, "passwords": passwords,
                          "sheet": str(folder / "demo" / "drawings" / "main-building-sheet.dxf")}), flush=True)
        threading.Thread(target=lambda: (sys.stdin.read(), stopping.set()), daemon=True).start()
        while not stopping.is_set() and server.poll() is None:
            stopping.wait(0.5)
    finally:
        if server is not None and server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
        with psycopg.connect(SERVER, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
