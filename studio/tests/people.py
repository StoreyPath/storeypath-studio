"""A team on the demo campus, for the tests of many people at once: Studio served (with
accounts) and who is who on it, each with a session of their own; a page's stream of
events, as EventSource reads it."""

import http.client
import json
import threading
import time

from sessions import call
from storeypath.accounts import Accounts, Scope, cookie_name
from storeypath.samples import build_demo
from storeypath.server import Studio
from storeypath.web import make_server
from storeypath.workspace import Workspace

PASSWORD = "everyone's password"


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


class Team:
    """Studio on the demo campus: khalid (an engineer, its owner), sara (edit on the whole
    project), vera (view on HQ's ground floor), bob (edit on the Annex), boss (an admin)."""

    def __init__(self, data, **studio_kw):
        ws_path, _ = build_demo(data / "demo")
        self.ws = Workspace.load(ws_path)
        code = self.code = self.ws.id
        self.studio = Studio(data, model=NoModel(), warm=False, **studio_kw)
        self.accounts = Accounts(data)
        self.srv = make_server(self.studio, port=0, accounts=self.accounts)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_port
        roles = {"khalid": ("engineer", "Khalid Engineer"), "sara": ("user", "Sara Ahmed"),
                 "vera": ("user", "Vera Viewer"), "bob": ("user", "Bob Annex"), "boss": ("admin", "The Boss")}
        self.users = {n: self.accounts.add_user(n, PASSWORD, name=name, role=role, must_change_password=False)
                      for n, (role, name) in roles.items()}
        self.hq, self.annex = f"{code}-DEMO-HQ", f"{code}-DEMO-ANNEX"
        self.hq0, self.hq1, self.an0 = f"{self.hq}-F00", f"{self.hq}-F01", f"{self.annex}-F00"
        self.accounts.set_owner(code, self.users["khalid"].id)
        boss = self.users["boss"].id
        self.accounts.set_grant(code, self.users["sara"].id, Scope(kind="project"), "edit", boss)
        self.accounts.set_grant(code, self.users["vera"].id, Scope(kind="floor", id=self.hq0), "view", boss)
        self.accounts.set_grant(code, self.users["bob"].id, Scope(kind="building", id=self.annex), "edit", boss)
        self.tokens = {n: self.accounts.start_session(u) for n, u in self.users.items()}

    def __call__(self, who, method, path, body=None, page=None):
        """A call as ``who`` (a page of theirs, when ``page`` says which): (status, answer)."""
        path = path if path.startswith("/api/") else f"/api/projects/{self.code}/{path}"
        status, payload, _ = call(self.port, method, path, body, token=self.tokens[who],
                                  headers={"X-StoreyPath-Page": page} if page else None)
        return status, payload

    def spaces(self, floor):
        return [r for r in self.studio.workspace(self.code).floor_objects(floor) if r.kind == "space"]

    def finished(self, who, job, within=120):
        end = time.monotonic() + within
        while job["state"] not in ("done", "failed"):
            assert time.monotonic() < end, job
            time.sleep(0.1)
            job = self(who, "GET", f"/api/jobs/{job['id']}")[1]
        assert job["state"] == "done", job
        return job

    def stream(self, who, floor=None):
        return Stream(self.port, self.code, self.tokens[who], floor)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Stream:
    """A page's stream of a project's events (on a floor), as EventSource reads it."""

    def __init__(self, port: int, code: str, token: str, floor: str | None = None):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        path = f"/api/projects/{code}/events" + (f"?floor={floor}" if floor else "")
        self.conn.request("GET", path, headers={"Cookie": f"{cookie_name(port)}={token}"})
        self.res = self.conn.getresponse()

    def next(self):
        """The next event, (kind, data); None when the stream has ended."""
        kind = data = None
        while True:
            line = self.res.readline()
            if not line:
                return None
            line = line.decode().rstrip("\n")
            if line.startswith("event: "):
                kind = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
            elif not line and kind:
                return kind, data

    def until(self, wanted, within: float = 20):
        """Events up to the first ``wanted`` (kind, data) says yes to."""
        seen, end = [], time.monotonic() + within
        while time.monotonic() < end:
            got = self.next()
            assert got is not None, f"the stream ended: {seen}"
            seen.append(got)
            if wanted(*got):
                return seen
        raise AssertionError(f"not seen in {within} s: {seen}")

    def close(self):
        self.conn.close()


def listening(team: Team, within: float = 10) -> None:
    """Until Studio listens to the database's changes (from the first stream on)."""
    end = time.monotonic() + within
    hub = team.srv.app.state.web.events
    while hub.live is None or not hub.live.listening.is_set():
        assert time.monotonic() < end, "not listening"
        time.sleep(0.02)
