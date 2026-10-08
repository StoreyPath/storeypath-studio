"""Studio's web server refuses what a page of another site, or a name that is not
Studio's, sends it: and a refused request is never followed by its body read as
another request (on a kept-alive connection)."""

import json
import re
import socket
import threading
import urllib.error
import urllib.request

import pytest

from sessions import admin_server, cookie
from storeypath.server import Studio, allowed_hosts


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    servers = []

    def start(**kw):
        studio = Studio(tmp_path / "data", model=NoModel(), warm=False)
        srv = admin_server(studio, port=0, **kw)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_port, studio

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def exchange(port: int, raw: bytes, wait: float = 1.0) -> tuple[list[str], bool]:
    """``raw`` sent on one connection: the status lines answered on it, and whether the
    server closed it."""
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(raw)
    s.settimeout(wait)
    got, closed = b"", False
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                closed = True
                break
            got += chunk
    except socket.timeout:
        pass
    finally:
        s.close()
    return re.findall(r"HTTP/1\.\d \d{3}[^\r\n]*", got.decode(errors="replace")), closed


def request(method: str, path: str, port: int, body: bytes = b"", headers: dict | None = None) -> bytes:
    head = {"Host": f"127.0.0.1:{port}", "Content-Length": str(len(body)), "Cookie": cookie(port), **(headers or {})}
    return (f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in head.items() if v is not None)
            + "\r\n").encode() + body


def names(port: int) -> set[str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/projects") as r:
        return {p["name"] for p in json.load(r)}


JSON = {"Content-Type": "application/json", "X-StoreyPath": "1"}


def test_a_refused_request_body_is_never_read_as_another_request(server):
    """What a page of any site can send with fetch(…, {mode: "no-cors"}): a POST of
    text/plain whose body is a whole request, as JSON with Studio's header."""
    port, _ = server()
    inner_body = json.dumps({"name": "SMUGGLED"}).encode()
    inner = request("POST", "/api/projects", port, inner_body, JSON)
    for outer in (
        request("POST", "/api/projects", port, inner, {"Content-Type": "text/plain;charset=UTF-8",
                                                       "Origin": "https://evil.example", "Connection": "keep-alive"}),
        # JSON without Studio's header, and with it but from another site
        request("POST", "/api/projects", port, inner, {"Content-Type": "application/json"}),
        request("POST", "/api/projects", port, inner, {**JSON, "Origin": "https://evil.example"}),
        # an upload without the header, and one too large (its body not even sent)
        request("PUT", "/api/open", port, inner, {"Content-Type": "application/octet-stream"}),
        request("PUT", "/api/open", port, b"", {"X-StoreyPath": "1", "Content-Length": str(10**12)}) + inner,
        # a GET with a body
        request("GET", "/api/status", port, inner),
    ):
        statuses, _ = exchange(port, outer)
        assert len(statuses) == 1, statuses  # the request sent, and nothing it carried
    assert "SMUGGLED" not in names(port)


def test_a_body_whose_end_is_not_known_is_refused_and_the_connection_closed(server):
    port, _ = server()
    inner = request("POST", "/api/projects", port, json.dumps({"name": "SMUGGLED"}).encode(), JSON)
    chunked = request("POST", "/api/projects", port, b"", {**JSON, "Content-Length": None,
                                                         "Transfer-Encoding": "chunked"}) + b"0\r\n\r\n" + inner
    statuses, closed = exchange(port, chunked)
    assert statuses == ["HTTP/1.1 400 Bad Request"] and closed
    twice = request("POST", "/api/projects", port, b"{}", JSON).replace(b"Content-Length: 2\r\n",
                                                                         b"Content-Length: 2\r\nContent-Length: 0\r\n")
    statuses, closed = exchange(port, twice + inner)
    assert statuses == ["HTTP/1.1 400 Bad Request"] and closed
    assert "SMUGGLED" not in names(port)


def test_what_is_allowed_stays_on_its_connection(server):
    """Two requests on a kept-alive connection, both answered (the page's own use)."""
    port, _ = server()
    one = request("POST", "/api/projects", port, json.dumps({"name": "One"}).encode(), JSON)
    two = request("POST", "/api/projects", port, json.dumps({"name": "Two"}).encode(),
                  {**JSON, "Origin": f"http://127.0.0.1:{port}"})
    statuses, _ = exchange(port, one + two)
    assert statuses == ["HTTP/1.1 200 OK", "HTTP/1.1 200 OK"]
    assert {"One", "Two"} <= names(port)


def call(port, path, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    sent = {**(JSON if body is not None else {}), **(headers or {})}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={k: v for k, v in sent.items() if v is not None})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_changes_need_studios_header(server):
    port, _ = server()
    assert call(port, "/api/projects", {"name": "x"}, {"X-StoreyPath": None})[0] == 403
    assert call(port, "/api/projects", {"name": "x"}, {"X-StoreyPath": "0"})[0] == 403
    assert call(port, "/api/projects", {"name": "x"})[0] == 200


def test_studio_answers_only_to_its_own_names(server, monkeypatch):
    monkeypatch.setenv("STOREYPATH_ALLOWED_HOSTS", "studio.example.org, other.example")
    port, _ = server(allowed=["proxy.example"])
    # names that resolve here but are not Studio's (DNS rebinding): refused, read or write
    for host in ("evil.example", f"evil.example:{port}", "localhost.evil.example", "", "a b"):
        assert call(port, "/api/status", headers={"Host": host})[0] in (400, 403), host
        assert call(port, "/api/projects", {"name": "x"}, {"Host": host})[0] in (400, 403), host
    # localhost, an address (no rebinding sends one), and the names given
    for host in ("localhost", f"localhost:{port}", f"127.0.0.1:{port}", "[::1]:80", "192.168.1.20:8080",
                 "[fd00::5]", "studio.example.org", "OTHER.example:443", "proxy.example"):
        assert call(port, "/api/status", headers={"Host": host})[0] == 200, host
    assert call(port, "/api/projects", {"name": "x"}, {"Host": "studio.example.org"})[0] == 200
    assert {"localhost", "127.0.0.1", "::1", socket.gethostname().lower()} <= allowed_hosts()
    assert "studio.lan" in allowed_hosts("studio.lan") and "0.0.0.0" not in allowed_hosts("0.0.0.0")


def test_changes_only_from_studios_own_pages(server):
    port, _ = server(allowed=["studio.example.org"])
    for origin in ("https://evil.example", "null", f"http://evil.example:{port}", "file://", "chrome-extension://x"):
        assert call(port, "/api/projects", {"name": "x"}, {"Origin": origin})[0] == 403, origin
    # its own page, and one reached through a proxy by a name given
    assert call(port, "/api/projects", {"name": "a"}, {"Origin": f"http://127.0.0.1:{port}"})[0] == 200
    assert call(port, "/api/projects", {"name": "b"}, {"Origin": "https://studio.example.org"})[0] == 200
    # an address is a Host, not an origin of another page
    assert call(port, "/api/projects", {"name": "c"}, {"Origin": "http://192.168.1.20:3000"})[0] == 403
    # reading is not changing: a GET is answered whatever page asks (the browser keeps it from that page)
    assert call(port, "/api/status", headers={"Origin": "https://evil.example"})[0] == 200


def test_json_is_an_object_and_has_no_nan(server):
    port, _ = server()
    for raw in (b'{"name": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}', b"[1, 2]", b'"name"', b"{"):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/projects", data=raw, headers=JSON)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 400, raw
