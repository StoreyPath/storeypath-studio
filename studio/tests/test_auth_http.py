"""Who may call what (server.Gate, asked first in every call of the web app): every
route tried as an admin, the project's owner, an engineer it is not shared with, a user
who may view one floor, a user who may edit one building, a user with no access, and
nobody logged in. A route added without a rule fails here: every route of the app
under /api/ must ask the gate first, and must have a row below; any other is one of
the pages, which need no session."""

import ast
import http.client
import inspect
import json
import re
import shutil
import textwrap
import threading
import time

import pytest
from fastapi.routing import APIRoute

from storeypath import accounts as acc
from storeypath.accounts import Accounts, Scope, cookie_name
from storeypath.samples import build_demo
from storeypath.server import Studio
from storeypath.web import create_app, make_server
from storeypath.web.pages import PAGES
from storeypath.workspace import Workspace

ROLES = ("admin", "owner", "engineer", "floor_viewer", "building_editor", "nobody", "anon")
PASSWORD = "everyone's password"


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


class Site:
    """The server and who is who on it."""

    def __init__(self, port, studio, accounts, ids, tokens, users, job, app):
        self.port, self.studio, self.accounts, self.ids = port, studio, accounts, ids
        self.tokens, self.users, self.job, self.app = tokens, users, job, app

    def send(self, method, path, body=None, token=None, raw=None, headers=None, stream=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        head = {"Cookie": f"{cookie_name(self.port)}={token}" if token else "none=1", **(headers or {})}
        data = None
        if raw is not None:
            data = raw
            head.update({"X-StoreyPath": "1", "Content-Type": "application/octet-stream"})
        elif body is not None:
            data = json.dumps(body).encode()
            head.update({"X-StoreyPath": "1", "Content-Type": "application/json"})
        conn.request(method, path, body=data, headers=head)
        res = conn.getresponse()
        if stream and res.status == 200:  # a stream of events: its first line, then gone
            blob = res.readline()
        else:
            blob = res.read()
        conn.close()
        try:
            payload = json.loads(blob) if res.getheader("Content-Type", "").startswith("application/json") else blob
        except ValueError:
            payload = blob
        return res.status, payload, res

    def as_(self, role, method, path, body=None, raw=None, fresh=False, stream=False):
        token = None if role == "anon" else self.accounts.start_session(self.users[role]) if fresh \
            else self.tokens[role]
        return self.send(method, path, body, token, raw, stream=stream)

    def wait_jobs(self):
        for _ in range(600):
            if all(j.state in ("done", "failed") for j in self.studio.jobs._jobs.values()):
                return
            time.sleep(0.1)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(acc, "SCRYPT_N", 2**10)  # quicker; the hashing itself is tested in test_accounts
        mp.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
        data = tmp_path_factory.mktemp("auth") / "data"
        ws_path, packages = build_demo(data / "demo")
        (ws_path.parent / "exports").mkdir()
        for p in packages:  # the demo's packages, as exports of the project
            shutil.copy(p, ws_path.parent / "exports" / p.name)
        ws = Workspace.load(ws_path)
        code = ws.id
        hq, annex = f"{code}-DEMO-HQ", f"{code}-DEMO-ANNEX"
        hq0, hq1, an0 = f"{hq}-F00", f"{hq}-F01", f"{annex}-F00"
        item = ws.add_item("DESK-JUNIOR", hq0, 2.0, 2.0)
        ws.save(ws_path)
        space = next(r.id for r in ws.floor_objects(hq0) if r.kind == "space")
        lift = next(r.id for r in ws.floor_objects(hq0) if r.kind == "space" and r.type == "elevator")
        studio = Studio(data, model=NoModel(), warm=False)
        accounts = Accounts(data)
        roles = {"admin": "admin", "owner": "engineer", "engineer": "engineer", "floor_viewer": "user",
                 "building_editor": "user", "nobody": "user", "spare": "user", "resettable": "user"}
        users = {name: accounts.add_user(name.replace("_", "-"), PASSWORD, role=role, must_change_password=False)
                 for name, role in roles.items()}
        accounts.set_owner(code, users["owner"].id)
        accounts.set_grant(code, users["floor_viewer"].id, Scope(kind="floor", id=hq0), "view", users["admin"].id)
        accounts.set_grant(code, users["building_editor"].id, Scope(kind="building", id=annex), "edit",
                           users["admin"].id)
        tokens = {name: accounts.start_session(u) for name, u in users.items()}
        srv = make_server(studio, port=0, accounts=accounts)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        ids = {"code": code, "hq": hq, "annex": annex, "hq0": hq0, "hq1": hq1, "an0": an0, "item": item.id,
               "space": space, "lift": lift, "loc": f"{code}-NOPE", "spare": users["spare"].id,
               "resettable": users["resettable"].id, "hq_pkg": packages[0].name, "annex_pkg": packages[1].name,
               "drawing": "hq-level-0.dxf", "token": "abc", "notes": "notes.txt", "missing": "missing.dxf"}
        s = Site(srv.server_port, studio, accounts, ids, tokens, users, None, srv.app)
        status, job, _ = s.send("POST", f"/api/projects/{code}/export", {"building": annex}, tokens["admin"])
        assert status == 200, job
        s.job = job["id"]
        s.wait_jobs()
        yield s
        srv.shutdown()
        srv.server_close()


# Each row: method, path ({…} filled from Site.ids), body, who passes and what they get
# (a status, or a status and words of the answer), and who is answered 404 (they may not
# see it at all). Everyone else logged in is answered 403; nobody logged in, 401.
ALL = {"admin", "owner", "engineer", "floor_viewer", "building_editor", "nobody"}
PROJECT = {"admin", "owner", "floor_viewer", "building_editor"}  # any access to the project
OUTSIDE = {"engineer", "nobody"}  # no access to it at all


class Row:
    def __init__(self, method, path, body=None, *, ok, passes, hidden=frozenset(), raw=None, anyone=False,
                 fresh=False, settle=False, stream=False):
        self.method, self.path, self.body, self.ok, self.passes = method, path, body, ok, set(passes)
        self.hidden, self.raw, self.anyone, self.fresh, self.settle = set(hidden), raw, anyone, fresh, settle
        self.stream = stream  # answered with a stream that does not end: its first line read

    @property
    def case(self) -> tuple[str, str]:
        """The route it is for: the method and the path with each {…} a capture."""
        return self.method, as_case(self.path.split("?")[0])


def as_case(path: str) -> str:
    """A path under /api/, each {…} (a capture) as *: "projects/*/floors/*"."""
    return re.sub(r"\{[a-z_0-9]+\}", "*", path.removeprefix("/api/"))


C = "/api/projects/{code}"
ROWS = [
    # (a login ends the session it is sent with: each is tried with a session of its own)
    Row("POST", "/api/login", {"username": "spare", "password": PASSWORD}, ok=200, passes=ALL, anyone=True,
        fresh=True),
    Row("POST", "/api/logout", {}, ok=200, passes=ALL, anyone=True, fresh=True),
    Row("GET", "/api/me", ok=200, passes=ALL),
    Row("POST", "/api/me/password", {"current": PASSWORD, "new": PASSWORD}, ok=(400, "new password"), passes=ALL),
    Row("GET", "/api/status", ok=200, passes=ALL),
    Row("GET", "/api/catalogue", ok=200, passes=ALL),
    Row("POST", "/api/catalogue", {"types": "nope"}, ok=400, passes={"admin"}),
    Row("GET", "/api/projects", ok=200, passes=ALL),
    Row("POST", "/api/projects", {"name": ""}, ok=(400, "needs a name"), passes={"admin", "owner", "engineer"}),
    # (who may change something somewhere may open a package into it: the file is read)
    Row("PUT", "/api/open", raw=b"not a zip file", ok=(400, "not a StoreyPath file"),
        passes={"admin", "owner", "engineer", "building_editor"}),
    Row("GET", "/api/jobs/{job}", ok=200, passes={"admin", "owner", "building_editor"},
        hidden={"floor_viewer", "engineer", "nobody"}),
    Row("GET", C, ok=200, passes=PROJECT, hidden=OUTSIDE),
    Row("GET", C + "/review", ok=200, passes=PROJECT, hidden=OUTSIDE),
    Row("POST", C + "/delete", {"confirm": "not its name"}, ok=(400, "type the project's name"),
        passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("GET", C + "/access", ok=200, passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("POST", C + "/access", {"user": "{spare}", "scope": {"kind": "project"}, "level": None}, ok=200,
        passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("POST", C + "/owner", {"user": "nobody-known"}, ok=(400, "no such user"), passes={"admin"}),
    Row("GET", "/api/users", ok=200, passes={"admin", "owner"}),
    Row("GET", C + "/drawings/{drawing}/words", ok=200, passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("POST", C + "/incoming/{token}", {}, ok=(404, "no drawing waiting"), passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("POST", C + "/incoming/{token}/cancel", {}, ok=(404, "no drawing waiting"), passes={"admin", "owner"},
        hidden=OUTSIDE),
    Row("PUT", C + "/drawings/{notes}", raw=b"x", ok=(400, ".dwg or .dxf"), passes={"admin", "owner"},
        hidden=OUTSIDE),
    Row("POST", C + "/drawings/{missing}/plans", {}, ok=(404, "no drawing"), passes={"admin", "owner"},
        hidden=OUTSIDE),
    Row("POST", C + "/floors", {"drawing": "hq-level-0.dxf", "plans": [
        {"building_id": "{annex}", "ordinal": 0, "region": [0, 0, 1, 1]}]}, ok=(400, "already has floor 0"),
        passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("POST", C + "/convert", {"force": "yes"}, ok=(400, "force is true or false"), passes={"admin", "owner"},
        hidden=OUTSIDE),
    Row("POST", C + "/buildings/{annex}/site", {}, ok=(400, "is a number"),
        passes={"admin", "owner", "building_editor"}, hidden=OUTSIDE | {"floor_viewer"}),
    Row("POST", C + "/buildings/{annex}/placement", {}, ok=(400, "is a number"),
        passes={"admin", "owner", "building_editor"}, hidden=OUTSIDE | {"floor_viewer"}),
    # the way in a building: view on the whole of it
    Row("GET", C + "/buildings/{annex}/navigation", ok=200, passes={"admin", "owner", "building_editor"},
        hidden=OUTSIDE | {"floor_viewer"}),
    Row("POST", C + "/locations/{loc}/arrange", {}, ok=400, passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("POST", C + "/locations/{loc}/placement", {}, ok=400, passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("POST", C + "/export", {"building": "{annex}"}, ok=200, passes={"admin", "owner", "building_editor"},
        hidden=OUTSIDE | {"floor_viewer"}, settle=True),
    Row("GET", C + "/exports/{annex_pkg}", ok=200, passes={"admin", "owner", "building_editor"},
        hidden=OUTSIDE | {"floor_viewer"}),
    Row("GET", C + "/preview.storeypath", ok=200, passes=PROJECT, hidden=OUTSIDE),
    Row("GET", C + "/project.storeypath-project", ok=200, passes={"admin", "owner"}, hidden=OUTSIDE),
    Row("GET", C + "/floors/{hq0}", ok=200, passes={"admin", "owner", "floor_viewer"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("GET", C + "/floors/{hq0}/drawing", ok=200, passes={"admin", "owner", "floor_viewer"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("GET", C + "/floors/{hq0}/print", ok=200, passes={"admin", "owner", "floor_viewer"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("GET", C + "/floors/{hq0}/print.png", ok=200, passes={"admin", "owner", "floor_viewer"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("POST", C + "/floors/{hq0}/edits", {}, ok=(400, "send"), passes={"admin", "owner"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("POST", C + "/floors/{hq0}/items", {"type": "NOPE", "x": 1, "y": 1}, ok=(400, "no item type"),
        passes={"admin", "owner"}, hidden=OUTSIDE | {"building_editor"}),
    Row("POST", C + "/items/{item}", {"x": "far"}, ok=(400, "is a number"), passes={"admin", "owner"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("POST", C + "/floors/{hq0}/convert", {"force": "yes"}, ok=(400, "force is true or false"),
        passes={"admin", "owner"}, hidden=OUTSIDE | {"building_editor"}),
    Row("POST", C + "/objects/{space}", {}, ok=(400, "nothing to change"), passes={"admin", "owner"},
        hidden=OUTSIDE | {"building_editor"}),
    # lifts and stairs drawn, and linked through the floors (vertical.py)
    Row("POST", C + "/floors/{hq0}/vertical", {"type": "lift"}, ok=(400, "type is"), passes={"admin", "owner"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("GET", C + "/objects/{lift}/stack", ok=200, passes={"admin", "owner", "floor_viewer"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("POST", C + "/objects/{lift}/copy", {"floors": []}, ok=(400, "choose the floors"), passes={"admin", "owner"},
        hidden=OUTSIDE | {"building_editor"}),
    Row("GET", "/api/admin/users", ok=200, passes={"admin"}),
    Row("POST", "/api/admin/users", {}, ok=(400, "username"), passes={"admin"}),
    Row("POST", "/api/admin/users/{spare}", {"role": "king"}, ok=(400, "role"), passes={"admin"}),
    Row("POST", "/api/admin/users/{resettable}/password", {}, ok=200, passes={"admin"}),
    Row("GET", "/api/admin/audit", ok=200, passes={"admin"}),
    Row("GET", "/api/backup", ok=200, passes={"admin"}),
    Row("GET", C + "/events", ok=200, passes=PROJECT, hidden=OUTSIDE, stream=True),
]


def fill(value, ids):
    if isinstance(value, str):
        return value.format(**ids) if "{" in value else value
    if isinstance(value, list):
        return [fill(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, ids) for k, v in value.items()}
    return value


def expected(row: Row, role: str):
    if role == "anon" and not row.anyone:
        return 401, None
    if role in row.passes or (row.anyone and role == "anon"):
        return row.ok if isinstance(row.ok, tuple) else (row.ok, None)
    if role in row.hidden:
        return 404, None
    return 403, None


@pytest.mark.parametrize("row", ROWS, ids=[f"{r.method} {r.path}" for r in ROWS])
def test_each_route_lets_through_only_who_may(site, row):
    ids = {**site.ids, "job": site.job}
    path = fill(row.path, ids)
    wrong = []
    for role in ROLES:
        want, words = expected(row, role)
        if row.anyone and role == "anon" and row.path.endswith("/logout"):
            want = 200
        status, payload, _ = site.as_(role, row.method, path, fill(row.body, ids), row.raw, fresh=row.fresh,
                                      stream=row.stream)
        said = json.dumps(payload) if isinstance(payload, (dict, list)) else ""
        if status != want or (words and words not in said):
            wrong.append(f"{role}: {status} {said[:160]} (wanted {want}{' ' + words if words else ''})")
        if row.settle:
            site.wait_jobs()
    assert not wrong, "\n".join(wrong)


def app_routes(app):
    """Every route of the app: (method, path as written, its function)."""
    out = []
    for route in app.routes:
        assert isinstance(route, APIRoute), f"{route!r}: not a route of the app's own (a mount, a router?)"
        out += [(method, route.path, route.endpoint) for method in sorted(route.methods)]
    return out


def api_cases(app):
    """(method, path) of every call of the API, a capture as *, and its function."""
    return [((method, as_case(path)), endpoint) for method, path, endpoint in app_routes(app)
            if path.startswith("/api/")]


def asks_the_gate(statement) -> bool:
    value = statement.value if isinstance(statement, (ast.Expr, ast.Assign, ast.Return)) else None
    return isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) \
        and isinstance(value.func.value, ast.Name) and value.func.value.id == "may"


def first_line(endpoint):
    """A call's first statement (after its docstring)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    body = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))).body
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        body = body[1:]
    return body[0]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return create_app(Studio(tmp_path_factory.mktemp("routes"), model=NoModel(), warm=False), accounts=None)


def test_every_call_asks_the_gate_first(app):
    cases = api_cases(app)
    assert len(cases) > 40
    for (method, path), endpoint in cases:
        assert asks_the_gate(first_line(endpoint)), f"{method} {path}: its first line does not ask the gate (may.…)"


def test_every_call_is_tried_here(app):
    cases = {key for key, _ in api_cases(app)}
    tried = {row.case for row in ROWS}
    # one call answers two paths (the project file by its old name too): both are the same rule
    tried.add(("GET", "projects/*/project.storeypath"))
    assert cases - tried == set(), "routes without a row in ROWS"
    assert tried - cases == set(), "rows for routes that are not there"


def test_what_needs_no_session_is_the_pages_alone(app):
    public = {(method, path) for method, path, _ in app_routes(app) if not path.startswith("/api/")}
    assert public == {("GET", p) for p in PAGES}, "a route outside /api/ that is not a page"
    for method, path, endpoint in app_routes(app):
        if not path.startswith("/api/"):
            source = inspect.getsource(endpoint)
            assert "May" not in source and "may." not in source, path  # a page asks nobody anything


def test_a_route_without_a_rule_fails_here(app):
    """What the two tests above catch: a call added without asking the gate first, or without a row."""
    def careless(code: str):
        return {"code": code}

    app.add_api_route("/api/projects/{code}/careless", careless, methods=["GET"])
    try:
        (_, endpoint), = [(k, e) for k, e in api_cases(app) if k == ("GET", "projects/*/careless")]
        assert not asks_the_gate(first_line(endpoint))
        assert ("GET", "projects/*/careless") not in {row.case for row in ROWS}
    finally:
        app.router.routes.pop()
