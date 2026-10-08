"""What Studio checks on every request before any call is made, and who is asking.

Studio answers only to its own names (allowed_hosts: localhost, this machine's name,
an address, and those given with --allowed-host or STOREYPATH_ALLOWED_HOSTS): a page
reached by another name that resolves here (DNS rebinding) is refused. What changes
something (POST, a JSON object; PUT, a file) is sent with the header
``X-StoreyPath: 1``, which a page of another site cannot send without a CORS
preflight (never answered here), and, when the browser says what page sent it
(Origin), from a page of Studio's. A request's body is at most so large (MAX_JSON,
MAX_UPLOAD; logging in and one's own password, SMALL_JSON), told by its
Content-Length before anything of it is read; one whose end is not told so
(Transfer-Encoding, or a Content-Length said twice) is refused. Every answer says it
is never to be shown in another page's frame, nor its type guessed, and is never kept
(no-store) unless it says otherwise. A request refused here, or answered before its
body was read, ends its connection: its body is never read as another request.

Behind a proxy Studio trusts (--trusted-proxy), who is asking is the address the proxy
says (client_address).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlparse

MAX_UPLOAD = 512 * 1024 * 1024  # a file sent (PUT)
MAX_JSON = 64 * 1024 * 1024  # a request's JSON body
SMALL_JSON = 4096  # …logging in and one's own password: a few names and passwords
SMALL_CALLS = ("/api/login", "/api/me/password")
ALLOWED_HOSTS_ENV = "STOREYPATH_ALLOWED_HOSTS"  # more names Studio may be reached by: "studio.example.org, studio"
TRUSTED_PROXIES_ENV = "STOREYPATH_TRUSTED_PROXIES"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
METHODS = ("GET", "POST", "PUT")  # what Studio answers; anything else (a CORS preflight too) is refused


# ---- Studio's names ----------------------------------------------------------------

def allowed_hosts(bound: str = "127.0.0.1", more=()) -> set[str]:
    """The names a browser may reach Studio by (the Host it sends, and the origin of a
    page that changes something): localhost and the loopback addresses, this machine's
    own name, the address it is bound to when that is a name, and those given (``more``,
    and STOREYPATH_ALLOWED_HOSTS, separated by commas or spaces; "*": any). An address
    (192.168.1.20, [fd00::5]) is always allowed as a Host: a page reached by another
    name that resolves here (DNS rebinding) sends that name, never an address."""
    names = {"localhost", "127.0.0.1", "::1"}
    try:
        own = socket.gethostname().strip().lower()
    except OSError:
        own = ""
    if own:
        short = own.split(".")[0]
        names |= {own, short, f"{short}.local"}
    if bound and bound not in ("0.0.0.0", "::", ""):
        names.add(bound.strip("[]").lower())
    given = list(more or []) + re.split(r"[,\s]+", os.environ.get(ALLOWED_HOSTS_ENV, ""))
    names |= {n.strip().strip("[]").lower() for n in given if n and n.strip()}
    return names


def _host_name(value: str) -> str | None:
    """The host in a Host header's value or an origin's host[:port], lowercase, without
    its port or brackets; None when it is not one."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        name, port = value[1:end], value[end + 1:]
        if end < 0 or (port and not re.fullmatch(r":\d{1,5}", port)):
            return None
        return name or None
    name, _, port = value.partition(":")
    if (port and not re.fullmatch(r"\d{1,5}", port)) or not re.fullmatch(r"[a-z0-9._-]+", name):
        return None
    return name


def _is_address(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


# ---- who is asking -------------------------------------------------------------------

def trusted_networks(more=()) -> list:
    """The proxies Studio takes the client's address from: ``more`` and
    STOREYPATH_TRUSTED_PROXIES (addresses or networks, separated by commas or spaces)."""
    given = [*more, *re.split(r"[,\s]+", os.environ.get(TRUSTED_PROXIES_ENV, ""))]
    out = []
    for g in (str(x).strip() for x in given):
        if not g:
            continue
        try:
            out.append(ipaddress.ip_network(g, strict=False))
        except ValueError:
            raise ValueError(f"a trusted proxy is an address or a network (10.0.0.5, 10.0.0.0/24): {g!r}") from None
    return out


def client_address(peer: str, headers, trusted: list) -> str | None:
    """Who is asking: the connection's address, unless it is a trusted proxy's, then the
    address that proxy says it serves: X-Real-IP (nginx: proxy_set_header X-Real-IP
    $remote_addr), and the nearest address in X-Forwarded-For that is not a trusted
    proxy's (read from the right: what a client puts there itself is further left).
    When both are said they must agree: a proxy that sets one passes the other on as
    the client sent it, and a client's own word is never taken for where it is. None
    (refused) when a trusted proxy says neither, they disagree, or one says what is not
    an address: never taken as the proxy's own (every person would share one address,
    and one person's failed logins would make everyone wait). An IPv4 address written
    as IPv6 (::ffff:203.0.113.7) is that IPv4 address. ``headers``: anything with
    get_all(name) (an email.message.Message, or Said)."""

    def ip(text):
        try:
            got = ipaddress.ip_address(text.strip().strip("[]"))
        except ValueError:
            return None
        return getattr(got, "ipv4_mapped", None) or got

    if not trusted or not peer:
        return peer
    at = ip(peer)
    if at is None or not any(at in n for n in trusted):
        return peer  # not a proxy we trust: what it says of others is not heard
    said = []  # what the proxy says, each way it says it
    real = headers.get_all("X-Real-IP") or []
    if len(real) > 1:
        return None
    if real:
        said.append(ip(real[0]))
    chain = [x for h in (headers.get_all("X-Forwarded-For") or []) for x in h.split(",") if x.strip()]
    if chain:
        nearest = None
        for hop in reversed(chain):
            got = ip(hop)
            if got is None or not any(got in n for n in trusted):
                nearest = got
                break
        said.append(nearest)
    if not said or None in said or len(set(said)) > 1:
        return None
    return str(said[0])


class Said:
    """A request's headers as client_address reads them (get_all), from an ASGI
    request's (starlette's Headers)."""

    def __init__(self, headers):
        self.headers = headers

    def get_all(self, name: str) -> list[str] | None:
        return self.headers.getlist(name.lower()) or None


# ---- the checks, as a request comes ----------------------------------------------------

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),  # never shown in another page's frame (clickjacking)
    (b"content-security-policy", b"frame-ancestors 'none'"),
]


class Guard:
    """Studio's checks on a request as it comes (an ASGI middleware, outside everything
    else): over HTTPS, a request that came in plain HTTP is sent on to https:// (for one
    of Studio's names; refused for any other); then the method, how its body is framed,
    its Host, and for a change, where it is from, Studio's header, its type and its
    size. What is refused here is answered with its connection closed. Every answer is
    given Studio's security headers."""

    def __init__(self, app, *, names: set[str], https: bool = False):
        self.app, self.names, self.any_name, self.https = app, set(names), "*" in names, https

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from starlette.datastructures import Headers

        head = Headers(scope=scope)
        lengths = head.getlist("content-length")
        framed = "transfer-encoding" not in head and len(lengths) <= 1 \
            and (not lengths or re.fullmatch(r"\d{1,15}", lengths[0].strip()) is not None)
        length = int(lengths[0]) if framed and lengths else 0
        state = {"read": length == 0}  # the whole body read: else the connection ends with the answer

        async def receiving():
            message = await receive()
            if message["type"] == "http.request" and not message.get("more_body", False):
                state["read"] = True
            return message

        async def sending(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                have = {k.lower() for k, _ in headers}
                if b"cache-control" not in have:
                    headers.append((b"cache-control", b"no-store"))
                headers += [(k, v) for k, v in SECURITY_HEADERS if k not in have]
                if not state["read"] and b"connection" not in have:
                    headers.append((b"connection", b"close"))
                message = {**message, "headers": headers}
            await send(message)

        if self.https and scope.get("scheme") != "https":
            return await self._to_https(scope, head, sending)
        method = scope["method"]
        if method not in METHODS:
            return await refuse(sending, 405, "Studio answers GET, POST and PUT", [(b"allow", b"GET, POST, PUT")])
        if not framed:
            return await refuse(sending, 400, "a request's body has a Content-Length")
        changes = method != "GET"
        if not self._ours(head, changes) or (changes and head.get("x-storeypath") != "1"):
            return await refuse(sending)
        if method == "PUT" and length > MAX_UPLOAD:
            return await refuse(sending, 413, "the file is too large")
        if method == "POST":
            if head.get("content-type", "").split(";")[0].strip() != "application/json":
                return await refuse(sending)
            # what anyone may send (logging in) is small: never read beyond it
            if length > (SMALL_JSON if scope["path"] in SMALL_CALLS else MAX_JSON):
                return await refuse(sending, 413, "too much to send at once")
        await self.app(scope, receiving, sending)

    def _named(self, name: str | None) -> bool:
        return name is not None and (self.any_name or name in self.names or _is_address(name))

    def _ours(self, head, changes: bool) -> bool:
        """Whether the request may be answered: addressed to one of Studio's names,
        and, when it ``changes`` something, sent by a page of Studio's (its Origin,
        when it has one: browsers send it with every POST and PUT)."""
        host_header = head.get("host") or ""
        if not self._named(_host_name(host_header)):
            return False
        origin = head.get("origin")
        if not changes or origin is None:
            return True
        u = urlparse(origin)
        if u.scheme not in ("http", "https") or not u.netloc:
            return False  # "null": a sandboxed frame, a file
        if u.netloc.lower() == host_header.strip().lower():
            return True  # this page's own
        theirs = _host_name(u.netloc)
        return theirs is not None and (self.any_name or theirs in self.names)

    async def _to_https(self, scope, head, send) -> None:
        """Plain HTTP to the HTTPS port: the same address, over HTTPS (for one of
        Studio's own names; refused for any other). Nothing else is done."""
        host = (head.get("host") or "").strip()
        if not self._named(_host_name(host)):
            return await refuse(send)
        where = scope.get("raw_path", scope["path"].encode()).decode("latin-1") or "/"
        if scope.get("query_string"):
            where += "?" + scope["query_string"].decode("latin-1")
        if not where.startswith("/") or where.startswith("//"):
            where = "/"
        body = b"Studio speaks HTTPS here\n"
        await send({"type": "http.response.start", "status": 307, "headers": [
            (b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()),
            (b"location", f"https://{host}{where}".encode("latin-1")), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


async def refuse(send, status: int = 403, error: str = "forbidden", extra=()) -> None:
    """A request refused as it came: never kept on its connection, where its body,
    unread, would be read as the next request."""
    body = json.dumps({"error": error}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"connection", b"close"), *extra]})
    await send({"type": "http.response.body", "body": body})
