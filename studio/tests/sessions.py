"""Studio's server in the tests (storeypath.web, on uvicorn), with accounts: an admin
logged in, whose session every urllib request to that server carries, as a browser's
cookie would. (Studio never serves without accounts but on this computer, for
`storeypath review`.)"""

import re
import urllib.request
from urllib.parse import urlsplit

from storeypath.accounts import Accounts, cookie_name
from storeypath.web import make_server

ADMIN_PASSWORD = "the admin's password"
_tokens: dict[int, str] = {}  # a server's port -> the admin's session there


class _Session(urllib.request.BaseHandler):
    """The session of the server a request goes to, as a Cookie header (unless the
    request has one of its own), named as a browser's would be: by the port of the Host
    it is sent with (that of the address, unless the request says another)."""

    def http_request(self, req):
        port = urlsplit(req.full_url).port
        token = _tokens.get(port)
        if token and not req.has_header("Cookie"):
            said = re.search(r":(\d{1,5})$", req.get_header("Host") or "")
            req.add_unredirected_header("Cookie", f"{cookie_name(said.group(1) if said else port)}={token}")
        return req

    https_request = http_request


urllib.request.install_opener(urllib.request.build_opener(_Session()))


def admin_of(accounts: Accounts):
    """The admin of a data folder's accounts (made the first time)."""
    return accounts.by_username("admin") or accounts.add_user(
        "admin", ADMIN_PASSWORD, name="Admin", role="admin", must_change_password=False)


def admin_server(studio, **kw):
    """make_server with accounts, and the admin logged in for every urllib request to it."""
    accounts = kw.pop("accounts", None) or Accounts(studio.data)
    srv = make_server(studio, accounts=accounts, **kw)
    _tokens[srv.server_port] = accounts.start_session(admin_of(accounts))
    return srv


def cookie(port: int) -> str:
    """The Cookie header of the admin's session on the server at ``port``."""
    return f"{cookie_name(port)}={_tokens[port]}"


def call(port: int, method: str, path: str, body=None, token: str | None = None, raw: bytes | None = None,
         headers: dict | None = None, tls=None):
    """One request, as a page of Studio's sends it, with the session ``token`` (or none):
    (status, the answer — JSON read, else bytes —, the response)."""
    import http.client
    import json

    conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=60, context=tls) if tls is not None \
        else http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    head = {"Cookie": f"{cookie_name(port)}={token}" if token else "none=1", **(headers or {})}
    data = None
    if raw is not None:
        data = raw
        head = {"X-StoreyPath": "1", "Content-Type": "application/octet-stream", **head}
    elif body is not None:
        data = json.dumps(body).encode()
        head = {"X-StoreyPath": "1", "Content-Type": "application/json", **head}
    conn.request(method, path, body=data, headers=head)
    res = conn.getresponse()
    blob = res.read()
    conn.close()
    payload = blob
    if (res.getheader("Content-Type") or "").startswith("application/json"):
        try:
            payload = json.loads(blob)
        except ValueError:
            pass
    return res.status, payload, res
