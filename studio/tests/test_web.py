"""Studio on FastAPI and uvicorn (storeypath.web): a project's events, sent as they
happen to whoever may see them; a backup cut short; what a connection may hold (its
head's size and time); what is refused as it comes; a call that takes long holds up no
other; the app itself, without a server (httpx's ASGI transport)."""

import asyncio
import http.client
import json
import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from sessions import call
from storeypath import accounts as acc
from storeypath.accounts import Accounts, Scope, cookie_name
from storeypath.backup import jobs_paused
from storeypath.samples import build_demo
from storeypath.server import Studio
from storeypath.web import create_app, events as events_module, make_server
from storeypath.web import serve as served
from storeypath.workspace import Workspace

PASSWORD = "everyone's password"


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)


def serve(data, **kw):
    studio = Studio(data, model=NoModel(), warm=False)
    accounts = Accounts(data)
    srv = make_server(studio, port=0, accounts=accounts, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, studio, accounts


@pytest.fixture
def demo(tmp_path):
    """The demo campus served: its owner (an admin), vera (who may view HQ's ground
    floor) and bob (who may edit the Annex)."""
    data = tmp_path / "data"
    ws_path, _ = build_demo(data / "demo")
    code = Workspace.load(ws_path).id
    srv, studio, accounts = serve(data)
    users = {name: accounts.add_user(name, PASSWORD, role=role, must_change_password=False)
             for name, role in (("boss", "admin"), ("vera", "user"), ("bob", "user"))}
    hq, annex = f"{code}-DEMO-HQ", f"{code}-DEMO-ANNEX"
    accounts.set_grant(code, users["vera"].id, Scope(kind="floor", id=f"{hq}-F00"), "view", users["boss"].id)
    accounts.set_grant(code, users["bob"].id, Scope(kind="building", id=annex), "edit", users["boss"].id)
    tokens = {n: accounts.start_session(u) for n, u in users.items()}
    yield srv, studio, accounts, users, tokens, {"code": code, "hq": hq, "annex": annex, "hq0": f"{hq}-F00"}
    srv.shutdown()
    srv.server_close()


class Stream:
    """A page's stream of a project's events, as EventSource reads it."""

    def __init__(self, port: int, code: str, token: str):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.conn.request("GET", f"/api/projects/{code}/events", headers={"Cookie": f"{cookie_name(port)}={token}"})
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


# ---- a project's events -----------------------------------------------------------------

def test_a_jobs_progress_goes_to_whoever_may_follow_it(demo, monkeypatch):
    srv, studio, accounts, users, tokens, ids = demo
    monkeypatch.setattr(events_module, "HEARTBEAT_S", 0.3)
    port, code = srv.server_port, ids["code"]
    boss, vera, bob = (Stream(port, code, tokens[n]) for n in ("boss", "vera", "bob"))
    try:
        for s in (boss, vera, bob):
            assert s.res.status == 200 and s.res.getheader("Content-Type").startswith("text/event-stream")
            assert s.next()[0] == "heartbeat"
        status, job, _ = call(port, "POST", f"/api/projects/{code}/export", {"building": ids["annex"]},
                              token=tokens["bob"])
        assert status == 200
        for s in (boss, bob):  # an admin, and who may edit the Annex: its export, as it goes, to its end
            seen = s.until(lambda kind, data: kind == "job" and data["state"] in ("done", "failed"))
            jobs = [d for k, d in seen if k == "job"]
            assert {d["id"] for d in jobs} == {job["id"]} and jobs[-1]["state"] == "done"
            assert jobs[-1]["result"]["file"].endswith("-ANNEX.storeypath")
            log = [line for d in jobs for line in d["log"]]  # each line once, in order
            assert log == studio.jobs.get(job["id"]).log
        # vera (HQ's ground floor alone) hears the heartbeat, not the Annex's export
        seen = [vera.next() for _ in range(4)]
        assert [k for k, _ in seen] == ["heartbeat"] * 4
    finally:
        for s in (boss, vera, bob):
            s.close()


def test_what_is_published_goes_to_whoever_may_see_it(demo, monkeypatch):
    srv, studio, accounts, users, tokens, ids = demo
    monkeypatch.setattr(events_module, "HEARTBEAT_S", 0.3)
    port, code = srv.server_port, ids["code"]
    vera, bob = Stream(port, code, tokens["vera"]), Stream(port, code, tokens["bob"])
    try:
        assert vera.next()[0] == bob.next()[0] == "heartbeat"
        hub = srv.app.state.web.events
        for _ in range(50):  # both streams open
            if hub.watching(code) == 2:
                break
            time.sleep(0.05)
        hub.publish(code, "change", {"what": "a space renamed"}, ("floor", ids["hq0"]))
        hub.publish(code, "change", {"what": "a door moved"}, ("building", ids["annex"]))
        got = vera.until(lambda kind, data: kind == "change")
        assert got[-1] == ("change", {"what": "a space renamed"})
        got = bob.until(lambda kind, data: kind == "change")
        assert got[-1] == ("change", {"what": "a door moved"})
        # vera's floor is no longer shared with her: her stream ends at the next heartbeat
        accounts.set_grant(code, users["vera"].id, Scope(kind="floor", id=ids["hq0"]), None, users["boss"].id)
        end = time.monotonic() + 5
        while (got := vera.next()) is not None and time.monotonic() < end:
            assert got[0] == "heartbeat"
        assert got is None
        # logged out: bob's ends too
        accounts.logout(tokens["bob"])
        while (got := bob.next()) is not None:
            assert got[0] in ("heartbeat", "change")
    finally:
        vera.close()
        bob.close()


def test_events_need_a_session_and_the_project(demo):
    srv, studio, accounts, users, tokens, ids = demo
    port = srv.server_port
    assert call(port, "GET", f"/api/projects/{ids['code']}/events")[0] == 401
    outsider = accounts.add_user("nobody", PASSWORD, must_change_password=False)
    assert call(port, "GET", f"/api/projects/{ids['code']}/events", token=accounts.start_session(outsider))[0] == 404


def test_studio_stopping_ends_every_stream(tmp_path):
    srv, studio, accounts = serve(tmp_path / "data")
    admin = accounts.add_user("boss", PASSWORD, role="admin", must_change_password=False)
    token = accounts.start_session(admin)
    _, made, _ = call(srv.server_port, "POST", "/api/projects", {"name": "Here"}, token=token)
    s = Stream(srv.server_port, made["code"], token)
    assert s.next()[0] == "heartbeat"
    started = time.monotonic()
    srv.shutdown()
    srv.server_close()
    assert time.monotonic() - started < 3  # not waiting for the stream
    assert s.next() is None


# ---- a backup -----------------------------------------------------------------------------

def test_a_backup_cut_short_is_recorded_so_and_lets_jobs_run_again(tmp_path):
    data = tmp_path / "data"
    srv, studio, accounts = serve(data)
    try:
        (data / "big").mkdir()
        (data / "big" / "noise.bin").write_bytes(os.urandom(24 * 1024 * 1024))  # gzip makes it no smaller
        boss = accounts.add_user("boss", PASSWORD, role="admin", must_change_password=False)
        token = accounts.start_session(boss)
        s = socket.create_connection(("127.0.0.1", srv.server_port))
        s.sendall(f"GET /api/backup HTTP/1.1\r\nHost: 127.0.0.1\r\nCookie: {cookie_name(srv.server_port)}={token}"
                  "\r\n\r\n".encode())
        s.settimeout(10)
        assert s.recv(4096).startswith(b"HTTP/1.1 200")
        s.close()  # gone, a little way in
        for _ in range(100):
            entries = [e for e in accounts.audit_tail() if e["action"] == "backup"]
            if entries:
                break
            time.sleep(0.1)
        assert [e["outcome"] for e in entries] == ["interrupted"]
        with jobs_paused(data, timeout=2):  # what the backup held, let go
            pass
    finally:
        srv.shutdown()
        srv.server_close()


# ---- what a connection may hold ----------------------------------------------------------

def exchange(port: int, raw: bytes, wait: float = 2.0) -> tuple[bytes, bool]:
    s = socket.create_connection(("127.0.0.1", port))
    got, closed = b"", False
    try:
        s.sendall(raw)
        s.settimeout(wait)
        while True:
            chunk = s.recv(65536)
            if not chunk:
                closed = True
                break
            got += chunk
    except socket.timeout:
        pass
    except ConnectionError:
        closed = True
    finally:
        s.close()
    return got, closed


def test_a_head_too_large_is_refused(tmp_path):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        port = srv.server_port
        # (a little over the limit: all of it read before the answer, so the answer is not lost to a reset)
        big = b"GET /login.html HTTP/1.1\r\nHost: 127.0.0.1\r\n" + b"X-Filler: " + b"a" * 70_000 + b"\r\n\r\n"
        got, closed = exchange(port, big)
        assert got.startswith(b"HTTP/1.1 431") and closed
        # within the limit: answered (and a body larger than it is not a head)
        got, _ = exchange(port, b"GET /login.html HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Filler: " + b"a" * 8000
                          + b"\r\nConnection: close\r\n\r\n")
        assert got.startswith(b"HTTP/1.1 200")
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_head_sent_too_slowly_is_let_go(tmp_path, monkeypatch):
    monkeypatch.setattr(served, "HEAD_TIMEOUT_S", 0.6)
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        s = socket.create_connection(("127.0.0.1", srv.server_port))
        s.settimeout(0.2)
        started, closed = time.monotonic(), False
        try:
            s.sendall(b"GET /login.html HTTP/1.1\r\n")
            for i in range(40):  # a header every 0.1 s: never quiet for long, never done
                s.sendall(f"X-Slow-{i}: yes\r\n".encode())
                try:
                    if not s.recv(1024):
                        closed = True
                        break
                except socket.timeout:
                    pass
        except (BrokenPipeError, ConnectionError):
            closed = True
        assert closed and time.monotonic() - started < 3
    finally:
        s.close()
        srv.shutdown()
        srv.server_close()


# ---- what is refused as it comes ------------------------------------------------------------

def test_only_get_post_and_put_are_answered(tmp_path):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        port = srv.server_port
        for method in ("OPTIONS", "DELETE", "PATCH", "HEAD", "TRACE"):
            status, _, res = call(port, method, "/api/projects", headers={
                "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-storeypath, content-type"})
            assert status == 405, method
            assert not [h for h, _ in res.getheaders() if h.lower().startswith("access-control-")], method
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_file_sent_by_who_may_not_is_answered_unread(tmp_path):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        s = socket.create_connection(("127.0.0.1", srv.server_port))
        s.sendall(b"PUT /api/open HTTP/1.1\r\nHost: 127.0.0.1\r\nX-StoreyPath: 1\r\n"
                  b"Content-Type: application/octet-stream\r\nContent-Length: 400000000\r\n\r\n" + b"x" * 100_000)
        s.settimeout(5)
        got = b""
        while b"\r\n\r\n" not in got:
            got += s.recv(4096)
        head = got.split(b"\r\n\r\n")[0].decode().lower()
        assert head.startswith("http/1.1 401") and "connection: close" in head  # not logged in: not read
        s.close()
    finally:
        srv.shutdown()
        srv.server_close()


# ---- long calls --------------------------------------------------------------------------

def test_a_long_call_holds_up_no_other(tmp_path, monkeypatch):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        port = srv.server_port
        boss = accounts.add_user("boss", PASSWORD, role="admin", must_change_password=False)
        token = accounts.start_session(boss)
        real = studio.status

        def slow(paths=True):
            time.sleep(1.5)  # as reading a drawing, rendering a print, writing a package
            return real(paths)

        monkeypatch.setattr(studio, "status", slow)
        with ThreadPoolExecutor(6) as pool:
            started = time.monotonic()
            long_ones = [pool.submit(call, port, "GET", "/api/status", None, token) for _ in range(4)]
            time.sleep(0.2)
            assert call(port, "GET", "/login.html")[0] == 200
            assert call(port, "GET", "/api/me", token=token)[0] == 200
            quick = time.monotonic() - started
            assert all(f.result()[0] == 200 for f in long_ones)
            together = time.monotonic() - started
        assert quick < 1.0, quick  # answered while the long ones run
        assert together < 3.5, together  # the long ones side by side, not one after another
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_failure_is_answered_500_and_written_to_the_log(tmp_path, monkeypatch, caplog):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        boss = accounts.add_user("boss", PASSWORD, role="admin", must_change_password=False)

        def broken(*args, **kwargs):
            raise RuntimeError("the very secret cause")

        monkeypatch.setattr(studio, "status", broken)
        with caplog.at_level(logging.ERROR):
            status, said, res = call(srv.server_port, "GET", "/api/status", token=accounts.start_session(boss))
            for _ in range(50):
                if "the very secret cause" in caplog.text:
                    break
                time.sleep(0.05)
        assert status == 500 and "secret" not in json.dumps(said)
        assert res.getheader("X-Frame-Options") == "DENY" and res.getheader("Cache-Control") == "no-store"
        assert "the very secret cause" in caplog.text  # the log says what
    finally:
        srv.shutdown()
        srv.server_close()


# ---- the app itself ----------------------------------------------------------------------

def test_the_app_without_a_server(tmp_path):
    """create_app over httpx's ASGI transport: Studio's names, its header, the gate, the
    answers' headers (no TLS, no cookies here: those are tried through the server)."""
    studio = Studio(tmp_path / "data", model=NoModel(), warm=False)
    app = create_app(studio, accounts=None)  # on this computer, as `storeypath review`

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            me = await c.get("/api/me")
            assert me.status_code == 200 and me.json()["local"] is True
            assert me.headers["x-content-type-options"] == "nosniff" and me.headers["cache-control"] == "no-store"
            assert (await c.get("/api/me", headers={"Host": "evil.example"})).status_code == 403
            assert (await c.post("/api/projects", json={"name": "x"})).status_code == 403  # no X-StoreyPath
            made = await c.post("/api/projects", json={"name": "Here"}, headers={"X-StoreyPath": "1"})
            assert made.status_code == 200
            code = made.json()["code"]
            assert (await c.get(f"/api/projects/{code}")).json()["project"]["name"] == "Here"
            assert (await c.get("/api/projects/NOPE00")).status_code == 404
            assert (await c.get("/api/nothing/here")).json() == {"error": "not found"}
            page = await c.get("/login.html")
            assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
            assert (await c.get("/viewer/../../../etc/passwd")).status_code == 404

    asyncio.run(run())
