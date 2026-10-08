"""What a person may not get round, over HTTP: a file opened changes only the project
it names, all through; logins and password checks cannot be used to fill Studio up,
lock someone out or run it out of memory; connections cannot be held open for ever;
a backup carries no certificate key and no session; nothing tells a person more than
they may see."""

import io
import json
import os
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import threading
import time
import urllib.error
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sessions import call
from storeypath import accounts as acc
from storeypath import server as server_module
from storeypath.accounts import Accounts, Scope, Throttled, Unauthorized
from storeypath.bundle import export_project
from storeypath.tls import context, studio_certificate
from storeypath.workspace import Project, Workspace
from test_auth_flows import PASSWORD, campus, quick_hashes, serve  # noqa: F401 (fixtures)


def rezip(blob: bytes, changes: dict) -> bytes:
    """A ZIP with some of its files changed: name -> its new bytes, a function of its
    JSON (the new JSON), or None to leave it out; a name it lacks is added."""
    src = zipfile.ZipFile(io.BytesIO(blob))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            if info.filename not in changes:
                z.writestr(info, src.read(info))
        for name, value in changes.items():
            if value is None:
                continue
            if callable(value):
                value = json.dumps(value(json.loads(src.read(name)))).encode()
            z.writestr(name, value)
    return out.getvalue()


def engineer(accounts, name="mallory"):
    """An engineer nothing is shared with, and their session."""
    user = accounts.add_user(name, PASSWORD, role="engineer", must_change_password=False)
    return user, accounts.start_session(user)


# ---- a file opened ---------------------------------------------------------------------

def test_a_file_whose_parts_name_different_projects_is_refused(campus):
    port, studio, accounts, users, tokens, ids = campus
    mallory, m = engineer(accounts)
    demo = ids["demo"]
    before = studio.path(demo).read_bytes()
    _, project_file, _ = call(port, "GET", f"/api/projects/{demo}/project.storeypath-project", token=tokens["boss"])
    new_code = lambda about: {**about, "project": {**about["project"], "id": "ZZZZ0000"}}  # noqa: E731
    forged = {
        # a project file: its project.json names a new project, its workspace the demo
        "project file": rezip(project_file, {"project.json": new_code}),
        # a package of HQ, with a project.json naming a new project
        "package": rezip(ids["packages"][0].read_bytes(), {"project.json": json.dumps(
            {"format": "storeypath-project", "project": {"id": "ZZZZ0000", "name": "Mine"}}).encode()}),
    }
    for what, blob in forged.items():
        for query in ("", "?replace=Demo%20Campus"):
            status, said, _ = call(port, "PUT", f"/api/open{query}", raw=blob, token=m)
            assert status == 400 and "different projects" in said["error"], (what, query, status, said)
            assert "Demo Campus" not in json.dumps(said) and demo not in json.dumps(said), (what, said)
    assert studio.path(demo).read_bytes() == before
    assert accounts.project_access(demo).owner == users["eng"].id
    assert "ZZZZ0000" not in accounts.all_access()


def test_a_new_project_never_takes_the_place_of_a_folder_that_is_not_its_own(campus, tmp_path):
    port, studio, accounts, users, tokens, ids = campus
    mallory, m = engineer(accounts)
    # a project kept in a folder not named by its code (moved there by hand)
    kept = Workspace.new("Kept by hand")
    kept.add_location("SITE", "Site")
    (studio.data / "QQQQ1111").mkdir()
    kept.save(studio.data / "QQQQ1111" / f"{kept.id}.spproj")
    # a new project whose code is that folder's name
    fresh = Workspace.new("Fresh")
    fresh.project = Project(code="QQQQ1111", name="Fresh")
    (tmp_path / "out" / "QQQQ1111").mkdir(parents=True)
    fresh.save(tmp_path / "out" / "QQQQ1111" / "QQQQ1111.spproj")
    buf = io.BytesIO()
    export_project(tmp_path / "out" / "QQQQ1111" / "QQQQ1111.spproj", buf)
    for query in ("", "?replace=Fresh"):
        status, said, _ = call(port, "PUT", f"/api/open{query}", raw=buf.getvalue(), token=m)
        assert status == 400 and "not this project" in said["error"], (query, status, said)
    assert studio.path(kept.id).parent.name == "QQQQ1111"  # still there, as it was
    assert accounts.project_access("QQQQ1111").owner is None


def test_a_new_projects_owner_is_never_put_in_place_of_one_kept(campus, tmp_path):
    port, studio, accounts, users, tokens, ids = campus
    mallory, m = engineer(accounts)
    # who a project of this code was shared with is kept (its folder was removed by hand)
    accounts.set_owner("QQQQ2222", users["eng"].id)
    fresh = Workspace.new("Fresh")
    fresh.project = Project(code="QQQQ2222", name="Fresh")
    (tmp_path / "out" / "QQQQ2222").mkdir(parents=True)
    fresh.save(tmp_path / "out" / "QQQQ2222" / "QQQQ2222.spproj")
    buf = io.BytesIO()
    export_project(tmp_path / "out" / "QQQQ2222" / "QQQQ2222.spproj", buf)
    status, said, _ = call(port, "PUT", "/api/open", raw=buf.getvalue(), token=m)
    assert status == 403 and "from before" in said["error"], (status, said)
    assert accounts.project_access("QQQQ2222").owner == users["eng"].id
    assert "QQQQ2222" not in studio._workspaces()  # nothing was opened
    # an admin opens it; who it is shared with stays as it was
    status, opened, _ = call(port, "PUT", "/api/open", raw=buf.getvalue(), token=tokens["boss"])
    assert status == 200 and opened["code"] == "QQQQ2222"
    assert accounts.project_access("QQQQ2222").owner == users["eng"].id


def test_a_file_opened_adds_item_types_only_for_who_may_change_them(campus):
    port, studio, accounts, users, tokens, ids = campus
    accounts.set_grant(ids["demo"], users["bob"].id, Scope(kind="building", id=ids["hq"]), "edit", users["eng"].id)
    more = lambda cat: {**cat, "types": [*cat["types"], {"code": "ZEBRA-DESK", "name_en": "Zebra desk"}]}  # noqa: E731
    blob = rezip(ids["packages"][0].read_bytes(), {"catalogue.json": more})  # HQ's, with a type not here
    status, opened, _ = call(port, "PUT", "/api/open?replace=Demo%20Campus", raw=blob, token=tokens["bob"])
    assert status == 200, opened
    assert opened["item_types_added"] == [] and opened["item_types_not_added"] == ["ZEBRA-DESK"]
    assert studio.catalogue().get("ZEBRA-DESK") is None  # the organization's catalogue, as it was
    accounts.update_user(users["bob"].id, capabilities=["catalogue"])  # may change the item types
    bob = accounts.start_session(accounts.user(users["bob"].id))
    status, opened, _ = call(port, "PUT", "/api/open?replace=Demo%20Campus", raw=blob, token=bob)
    assert status == 200 and opened["item_types_added"] == ["ZEBRA-DESK"] and opened["item_types_not_added"] == []
    assert studio.catalogue().get("ZEBRA-DESK") is not None


# ---- logging in --------------------------------------------------------------------------

class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def proxied(tmp_path):
    """Studio behind a proxy on this computer: who is asking is the X-Real-IP sent."""
    srv, studio, accounts = serve(tmp_path / "data", trusted_proxies=["127.0.0.1"])
    accounts.add_user("ali", PASSWORD, must_change_password=False)
    yield srv.server_port, studio, accounts
    srv.shutdown()
    srv.server_close()


def login(port, username, password, address="203.0.113.7"):
    return call(port, "POST", "/api/login", {"username": username, "password": password},
                headers={"X-Real-IP": address})


def test_a_username_too_long_to_be_one_is_refused_and_not_kept(proxied):
    port, studio, accounts = proxied
    status, said, _ = login(port, "x" * 3000, "nope nope")  # (within the body a login may have)
    assert status == 401 and said["error"] == "wrong username or password"
    entry = accounts.audit_tail(1)[0]
    assert entry["action"] == "login" and entry["outcome"] == "failed" and len(entry["target"]) <= 40
    assert not any(len(str(k)) > 200 for k in accounts._failures)


def test_logging_in_takes_a_small_body(proxied):
    port, studio, accounts = proxied
    for path in ("/api/login", "/api/setup", "/api/me/password"):
        status, said, _ = call(port, "POST", path, {"username": "ali", "password": "p" * 5000},
                               headers={"X-Real-IP": "203.0.113.7"})
        assert status == 413, (path, status, said)
    assert not accounts.audit_tail()  # nothing read, nothing kept


def test_logins_that_wait_are_written_down_once_a_window(proxied):
    port, studio, accounts = proxied
    for _ in range(acc.USER_FAILURES):
        assert login(port, "ali", "nope nope")[0] == 401
    for _ in range(20):
        assert login(port, "ali", "nope nope")[0] == 429
    outcomes = [e["outcome"] for e in accounts.audit_tail() if e["action"] == "login"]
    assert outcomes.count("failed") == acc.USER_FAILURES and outcomes.count("throttled") == 1, outcomes


def test_failed_logins_are_forgotten_when_old_and_never_kept_without_end(tmp_path, monkeypatch):
    clock = Clock()
    a = Accounts(tmp_path, clock=clock)
    for i in range(30):
        with pytest.raises(Unauthorized):
            a.login(f"u{i}", "nope nope", f"198.51.100.{i}")
    clock.t += acc.THROTTLE_WINDOW_S + 1
    with pytest.raises(Unauthorized):
        a.login("late", "nope nope", "198.51.100.200")
    assert len(a._failures) <= 2  # the old ones gone
    monkeypatch.setattr(acc, "FAILURE_KEYS_MAX", 50, raising=False)
    for i in range(200):  # many addresses at once: kept at most so many
        with pytest.raises(Unauthorized):
            a.login("u", "nope nope", f"198.18.{i // 250}.{i % 250}")
    assert len(a._failures) <= 50


def test_someone_elses_wrong_passwords_never_lock_a_person_out(proxied):
    port, studio, accounts = proxied
    for _ in range(acc.USER_FAILURES + 3):  # someone trying ali's password from elsewhere
        login(port, "ali", "nope nope", "198.51.100.66")
    assert login(port, "ali", "nope nope", "198.51.100.66")[0] == 429  # they wait
    status, me, _ = login(port, "ali", PASSWORD, "203.0.113.7")  # ali does not
    assert status == 200 and me["user"]["username"] == "ali"


def test_passwords_are_counted_before_they_are_checked_and_checked_a_few_at_a_time(proxied, monkeypatch):
    port, studio, accounts = proxied
    accounts._dummy_hash()
    checking, most, checked = [0], [0], [0]
    lock = threading.Lock()

    def slow(password, stored):  # scrypt: 32 MiB and a tenth of a second each
        with lock:
            checking[0] += 1
            checked[0] += 1
            most[0] = max(most[0], checking[0])
        time.sleep(0.2)
        with lock:
            checking[0] -= 1
        return False

    monkeypatch.setattr(acc, "verify_password", slow)
    with ThreadPoolExecutor(40) as pool:  # a burst from one address, each a username of its own
        statuses = list(pool.map(lambda i: login(port, f"u{i}", "nope nope", "198.51.100.9")[0], range(40)))
    assert set(statuses) <= {401, 429, 503}, statuses
    assert checked[0] <= acc.ADDRESS_FAILURES, checked  # no more than may fail, however many at once
    assert most[0] <= getattr(acc, "PASSWORD_CHECKS", 4), most


def test_an_address_a_client_says_itself_is_not_taken_through_a_proxy(proxied):
    """A proxy that sets X-Forwarded-For alone passes on the X-Real-IP a client sends: when
    the two disagree, the call is refused, never counted as from the address it claims."""
    port, studio, accounts = proxied
    statuses = [call(port, "POST", "/api/login", {"username": "ali", "password": "nope nope"},
                     headers={"X-Forwarded-For": "203.0.113.7", "X-Real-IP": f"198.51.100.{i}"})[0]
                for i in range(acc.ADDRESS_FAILURES + 5)]
    assert set(statuses) == {400}, statuses
    for i in range(acc.ADDRESS_FAILURES):  # the proxy's own say, alone: who is asking
        assert call(port, "POST", "/api/login", {"username": f"u{i}", "password": "nope nope"},
                    headers={"X-Forwarded-For": "203.0.113.7"})[0] == 401
    assert login(port, "z", "nope nope", "203.0.113.7")[0] == 429  # one address, however it was said


def test_an_ipv6_client_is_counted_by_its_network(tmp_path):
    a = Accounts(tmp_path)
    for i in range(acc.ADDRESS_FAILURES):  # an address of its own each time, from one /64
        with pytest.raises(Unauthorized):
            a.login(f"u{i}", "nope nope", f"2001:db8:1:2::{i + 1:x}")
    with pytest.raises(Throttled):
        a.login("ali", "nope nope", "2001:db8:1:2:ffff::1")
    with pytest.raises(Unauthorized):
        a.login("ali", "nope nope", "2001:db8:1:3::1")  # another network


def test_changing_ones_password_is_throttled_as_logging_in_is(proxied):
    port, studio, accounts = proxied
    ali = accounts.by_username("ali")
    token = accounts.start_session(ali)

    def change(current):
        return call(port, "POST", "/api/me/password", {"current": current, "new": "a new password now"},
                    token=token, headers={"X-Real-IP": "203.0.113.7"})

    for _ in range(acc.USER_FAILURES):
        assert change("not my password")[0] == 403
    status, said, res = change(PASSWORD)
    assert status == 429 and said["retry_after"] > 0, (status, said)
    assert accounts.login("ali", PASSWORD, "192.0.2.1")[1].username == "ali"  # unchanged, and not locked elsewhere


def test_the_owner_before_keeps_share_on_the_project_given_to_another(campus):
    port, studio, accounts, users, tokens, ids = campus
    code = ids["demo"]
    status, listing, _ = call(port, "POST", f"/api/projects/{code}/owner", {"user": users["bob"].id}, token=tokens["boss"])
    assert status == 200 and listing["owner"]["username"] == "bob"
    kept = [(g["user"]["username"], g["scope"]["kind"], g["level"]) for g in listing["grants"]]
    assert ("eng", "project", "share") in kept  # shown, and may be taken away like any grant
    assert call(port, "GET", f"/api/projects/{code}", token=tokens["eng"])[0] == 200
    entry = next(e for e in accounts.audit_tail() if e["action"] == "owner changed")
    assert entry["owner"] == "bob" and entry["was"] == "eng"


def test_two_studios_on_one_machine_keep_their_own_sessions(tmp_path):
    """Browsers keep one host's cookies for all its ports: each Studio's is named by its port."""
    import http.cookiejar
    import urllib.request

    jar = http.cookiejar.CookieJar()
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    servers = [serve(tmp_path / name) for name in ("one", "two")]
    try:
        def send(port, path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body else None,
                                         headers={"X-StoreyPath": "1", "Content-Type": "application/json"})
            try:
                with browser.open(req) as res:
                    return res.status, res.headers.get("Set-Cookie")
            except urllib.error.HTTPError as e:
                return e.code, None

        for srv, studio, accounts in servers:
            accounts.add_user("ali", PASSWORD, must_change_password=False)
            status, set_cookie = send(srv.server_port, "/api/login", {"username": "ali", "password": PASSWORD})
            assert status == 200 and set_cookie.startswith(f"sp_session_{srv.server_port}=")
        for srv, studio, accounts in servers:  # logging in to the second left the first's session be
            assert send(srv.server_port, "/api/me")[0] == 200
    finally:
        for srv, *_ in servers:
            srv.shutdown()
            srv.server_close()


# ---- files ---------------------------------------------------------------------------------

def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def lax_umask():
    """Files made as a lax umask would make them (readable by all), as on many servers."""
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_the_accounts_and_studios_key_are_the_owners_alone(tmp_path, lax_umask):
    data = tmp_path / "data"
    a = Accounts(data)
    a.add_user("ali", PASSWORD, must_change_password=False)
    files = ("studio.db", "studio.db-wal", "studio.db-shm")
    reading = sqlite3.connect(data / "studio.db")  # (the -wal and -shm are there while it is open)
    try:
        reading.execute("SELECT COUNT(*) FROM users").fetchone()
        assert {name: mode(data / name) for name in files} == dict.fromkeys(files, 0o600)
        for name in files:  # put there by hand, or by an older Studio
            os.chmod(data / name, 0o644)
        Accounts(data)
        assert {name: mode(data / name) for name in files} == dict.fromkeys(files, 0o600)
    finally:
        reading.close()
    made = studio_certificate(data, "127.0.0.1", [], machine=set())
    assert (mode(data / "tls"), mode(made.key), mode(made.cert)) == (0o700, 0o600, 0o600)
    os.chmod(data / "tls", 0o755)  # opened up by hand: closed again when Studio starts
    studio_certificate(data, "127.0.0.1", [], machine=set())
    assert mode(data / "tls") == 0o700


def test_what_the_command_line_writes_is_the_owners_alone(tmp_path, lax_umask):
    from typer.testing import CliRunner

    from storeypath.cli import app

    data = tmp_path / "data"
    Accounts(data).add_user("boss", PASSWORD, role="admin", must_change_password=False)
    out = tmp_path / "copy.tar.gz"
    r = CliRunner().invoke(app, ["backup", "--data", str(data), "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert mode(out) == 0o600  # every password's hash is in it
    r = CliRunner().invoke(app, ["restore", str(out), "--data", str(tmp_path / "new")])
    assert r.exit_code == 0, r.output
    assert mode(tmp_path / "new") == 0o700 and mode(tmp_path / "new" / "studio.db") == 0o600


# ---- what is told ------------------------------------------------------------------------

def test_the_servers_folders_are_told_to_admins_alone(campus):
    port, studio, accounts, users, tokens, ids = campus
    status, said, _ = call(port, "GET", "/api/status", token=tokens["eng"])
    assert status == 200 and "data" not in said
    assert call(port, "GET", "/api/status", token=tokens["boss"])[1]["data"] == str(studio.data)
    status, project, _ = call(port, "GET", f"/api/projects/{ids['demo']}", token=tokens["eng"])  # its owner
    assert status == 200 and "exports_folder" not in project and project["exports"]
    assert call(port, "GET", f"/api/projects/{ids['demo']}", token=tokens["boss"])[1]["exports_folder"]


def test_a_bad_body_is_answered_400_and_a_failure_500_without_what_went_wrong(campus, monkeypatch):
    port, studio, accounts, users, tokens, ids = campus
    code, boss = ids["demo"], tokens["boss"]
    plan = {"ordinal": 9, "building": "New", "region": [0, 0, 1, 1]}
    for path, body in (("/api/projects", {"name": 5}),
                       ("/api/projects", {"name": ["a name"]}),
                       (f"/api/projects/{code}/delete", {"confirm": 5}),
                       (f"/api/projects/{code}/drawings/hq-level-0.dxf/plans", {"units": ["m"]}),
                       (f"/api/projects/{code}/floors", {"drawing": 5, "plans": [plan]}),
                       (f"/api/projects/{code}/floors", {"drawing": "hq-level-0.dxf", "plans": [{**plan, "building": 5}]}),
                       (f"/api/projects/{code}/floors", {"drawing": "hq-level-0.dxf",
                                                         "plans": [{**plan, "building_id": 5}]}),
                       (f"/api/projects/{code}/floors", {"drawing": "hq-level-0.dxf",
                                                         "plans": [{**plan, "location": 5}]}),
                       (f"/api/admin/users/{users['bob'].id}", {"capabilities": 5})):
        status, said, _ = call(port, "POST", path, body, token=boss)
        assert status == 400, (path, body, status, said)

    def broken(*args, **kwargs):
        raise RuntimeError(f"{studio.data}/very/secret went wrong")

    monkeypatch.setattr(studio, "status", broken)
    status, said, _ = call(port, "GET", "/api/status", token=boss)
    assert status == 500 and "secret" not in json.dumps(said) and "RuntimeError" not in json.dumps(said), said


def test_a_job_is_followed_by_who_may_see_what_it_works_on_now(campus):
    port, studio, accounts, users, tokens, ids = campus
    annex = Scope(kind="building", id=ids["annex"])
    accounts.set_grant(ids["demo"], users["bob"].id, annex, "edit", users["eng"].id)
    status, job, _ = call(port, "POST", f"/api/projects/{ids['demo']}/export", {"building": ids["annex"]},
                          token=tokens["bob"])
    assert status == 200 and call(port, "GET", f"/api/jobs/{job['id']}", token=tokens["bob"])[0] == 200
    accounts.set_grant(ids["demo"], users["bob"].id, annex, None, users["eng"].id)  # no longer shared with bob
    assert call(port, "GET", f"/api/jobs/{job['id']}", token=tokens["bob"])[0] == 404  # though bob started it
    assert call(port, "GET", f"/api/jobs/{job['id']}", token=tokens["eng"])[0] == 200


# ---- the pages ---------------------------------------------------------------------------

ACCOUNT_JS = Path(__file__).parent.parent / "src" / "storeypath" / "review_app" / "account.js"
AFTER_LOGIN = r"""
globalThis.location = { origin: "http://127.0.0.1:8080" };
const src = (await import("node:fs")).readFileSync(process.argv[1], "utf8");
const { safeNext } = await import("data:text/javascript," + encodeURIComponent(src));
const wrong = [];
for (const raw of ["/.//evil.example", "/%2e//evil.example", "/a/..//evil.example", "/x/../..//evil.example",
                   "//evil.example", "/\\evil.example", "/\t/evil.example", "https://evil.example/",
                   "javascript:alert(1)", "/\n/evil.example", "", null]) {
  const next = safeNext(raw);
  // where location.replace(next) goes, from a page of Studio's
  if (new URL(next, "http://127.0.0.1:8080/login.html").origin !== "http://127.0.0.1:8080") wrong.push([raw, next]);
}
for (const [raw, want] of [["/review.html?p=K7Q2XM#f", "/review.html?p=K7Q2XM#f"], ["/#/p/K7Q2XM", "/#/p/K7Q2XM"]]) {
  if (safeNext(raw) !== want) wrong.push([raw, safeNext(raw)]);
}
console.log(JSON.stringify(wrong));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_after_logging_in_a_page_goes_to_studios_own_pages_alone():
    out = subprocess.run(["node", "--input-type=module", "-e", AFTER_LOGIN, str(ACCOUNT_JS)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [], out.stdout  # each: (what ?next= said, where the page went)


# ---- connections -------------------------------------------------------------------------

def closed_soon(sock, within: float) -> bool:
    """Whether the server closes ``sock`` (sending nothing more) within ``within`` seconds."""
    sock.settimeout(within)
    try:
        while True:
            if not sock.recv(4096):
                return True
    except (ssl.SSLError, ConnectionError):
        return True
    except socket.timeout:
        return False


@pytest.mark.parametrize("https", [False, True], ids=["http", "https"])
def test_a_connection_that_stops_sending_is_let_go(tmp_path, monkeypatch, https):
    monkeypatch.setattr(server_module, "READ_TIMEOUT_S", 0.5, raising=False)
    tls = trusting = None
    if https:
        made = studio_certificate(tmp_path / "data", "127.0.0.1", [], machine=set())
        tls, trusting = context(made.cert, made.key), ssl.create_default_context(cafile=str(made.cert))
    srv, studio, accounts = serve(tmp_path / "data", tls=tls)
    try:
        port = srv.server_port
        raw = socket.create_connection(("127.0.0.1", port))
        s = trusting.wrap_socket(raw, server_hostname="localhost") if https else raw
        s.sendall(b"GET /api/me HTTP/1.1\r\nHost: 127.0.0.1\r\n")  # and nothing more
        assert closed_soon(s, 5), "the connection is held open"
        s.close()
        # a request that keeps sending is answered, however long it takes as a whole
        raw = socket.create_connection(("127.0.0.1", port))
        s = trusting.wrap_socket(raw, server_hostname="localhost") if https else raw
        for line in (b"GET /login.html HTTP/1.1\r\n", b"Host: 127.0.0.1\r\n", b"Connection: close\r\n", b"\r\n"):
            s.sendall(line)
            time.sleep(0.3)
        s.settimeout(5)
        assert s.recv(12).startswith(b"HTTP/1.1 200")
        s.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_connections_beyond_the_most_are_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "MAX_CONNECTIONS", 3, raising=False)
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        port = srv.server_port
        held = [socket.create_connection(("127.0.0.1", port)) for _ in range(3)]  # each holds a thread
        time.sleep(0.3)
        more = socket.create_connection(("127.0.0.1", port))
        more.sendall(b"GET /login.html HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert closed_soon(more, 5), "served beyond the most"
        more.close()
        for s in held:
            s.close()
        time.sleep(0.3)
        assert call(port, "GET", "/login.html")[0] == 200  # room again
    finally:
        srv.shutdown()
        srv.server_close()
