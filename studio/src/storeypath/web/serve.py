"""Studio served by uvicorn: one process (Studio keeps its jobs and caches in memory),
its calls in a pool of WORKER_THREADS threads, its connections handled by an event loop.

HTTPS and plain HTTP share one port: what a connection speaks is told by its first
byte (0x16 begins a TLS handshake), peeked at before anything is read. A TLS one is
handed to uvicorn with Studio's certificate (tls.py, or the organization's), its
handshake made in the event loop within HANDSHAKE_S; a plain one is answered with a
redirect to its https:// address (guard.Guard), and closed.

What a connection may hold, and for how long:

- MAX_CONNECTIONS are served at once; one more is closed as it comes.
- A connection that says nothing, or stops sending a request (its head or its body),
  or stops taking what it is sent, for READ_TIMEOUT_S is let go; one that keeps
  sending a body, however slowly, is served. One between requests is let go after
  KEEP_ALIVE_S. While Studio works on a call, its connection waits as long as it takes.
- A request's head (its line and headers) is sent within HEAD_TIMEOUT_S of its first
  byte, and is at most MAX_HEAD bytes (431 beyond).
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import threading

import anyio
import uvicorn
from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

from .app import create_app
from .guard import LOOPBACK, allowed_hosts

HANDSHAKE_S = 10.0  # a connection says what it speaks, and makes its TLS handshake, within this
READ_TIMEOUT_S = 60.0  # a connection that sends nothing (or takes nothing sent) for this long is let go
HEAD_TIMEOUT_S = 30.0  # a request's line and headers are sent within this, from their first byte
KEEP_ALIVE_S = 15  # a connection between requests is kept this long
MAX_CONNECTIONS = 128  # connections served at once; more are closed
MAX_HEAD = 64 * 1024  # a request's line and headers, at most
WORKER_THREADS = 64  # calls run at once (each a thread of the pool)
GRACEFUL_S = 10  # at a stop, requests being answered are given this long
BACKLOG = 512


class _Ticket:
    """A connection's place among the MAX_CONNECTIONS: given back once, when it ends."""

    def __init__(self, front: "Front"):
        self.front, self.held = front, True

    def give_back(self) -> None:
        if self.held:
            self.held = False
            self.front.open -= 1


class Connection(HttpToolsProtocol):
    """One connection, as uvicorn serves it: let go when it stops (READ_TIMEOUT_S) or
    takes longer than HEAD_TIMEOUT_S to send a request's head, and refused a head larger
    than MAX_HEAD."""

    def __init__(self, *args, ticket: _Ticket, read_timeout: float, head_timeout: float, max_head: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.ticket, self.read_timeout, self.head_timeout, self.max_head = ticket, read_timeout, head_timeout, max_head
        self._heard = 0.0  # when it last sent something, or took what it was sent
        self._watch: asyncio.TimerHandle | None = None
        self._in_head, self._head = True, 0  # a request's head being read, and how much of it
        self._head_began: float | None = None
        self._heads = 0  # heads read whole
        self._refused = False  # a head too large: answered, and nothing more read

    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        self._heard = self.loop.time()
        self._arm(self.read_timeout)

    def connection_lost(self, exc) -> None:
        super().connection_lost(exc)
        if self._watch is not None:
            self._watch.cancel()
        self.ticket.give_back()

    def data_received(self, data: bytes) -> None:
        if self._refused:
            return
        self._heard = self.loop.time()
        in_head, heads = self._in_head, self._heads
        if in_head and self._head_began is None:
            self._head_began = self._heard
            self._arm(min(self.head_timeout, self.read_timeout))
        super().data_received(data)
        # all of it the head of one request, not yet whole: never kept beyond MAX_HEAD
        if in_head and self._in_head and self._heads == heads and not self.transport.is_closing():
            self._head += len(data)
            if self._head > self.max_head:
                self._too_large()

    def on_headers_complete(self) -> None:
        if self._refused:
            return
        self._in_head, self._head, self._head_began = False, 0, None
        self._heads += 1
        # the head read whole (in one piece, perhaps): its line and headers, as sent
        if len(self.url) + 16 + sum(len(k) + len(v) + 4 for k, v in self.headers) > self.max_head:
            return self._too_large()
        super().on_headers_complete()

    def on_body(self, body: bytes) -> None:
        if not self._refused:
            super().on_body(body)

    def on_message_complete(self) -> None:
        if self._refused:
            return
        super().on_message_complete()
        self._in_head, self._head, self._head_began = True, 0, None

    def resume_writing(self) -> None:
        self._heard = self.loop.time()
        super().resume_writing()

    def _waiting_for_client(self) -> bool:
        """Whether the connection waits for its client: for a request (or the rest of
        one), or to take what it is sent. Not while Studio works on a call."""
        cycle = self.cycle
        return cycle is None or cycle.response_complete or cycle.more_body or self.flow.write_paused

    def _arm(self, delay: float) -> None:
        if self._watch is not None:
            self._watch.cancel()
        self._watch = self.loop.call_later(max(delay, 0.01), self._check)

    def _check(self) -> None:
        if self.transport.is_closing():
            return
        now = self.loop.time()
        due = []
        if self._waiting_for_client():
            due.append(self._heard + self.read_timeout)
        if self._in_head and self._head_began is not None:
            due.append(self._head_began + self.head_timeout)
        if due and min(due) <= now:
            self.transport.abort()  # (close() would wait for what it cannot send)
            return
        self._arm(min(due) - now if due else self.read_timeout)

    def _too_large(self) -> None:
        self._refused = True
        if self.cycle is not None and not self.cycle.response_complete:
            self.transport.abort()  # mid-answer: nothing more can be said on it
            return
        body = b"the request's headers are too large"
        self.transport.write(b"HTTP/1.1 431 Request Header Fields Too Large\r\ncontent-type: text/plain; charset=utf-8"
                             b"\r\ncontent-length: %d\r\nconnection: close\r\n\r\n%s" % (len(body), body))
        self.transport.close()


class Front:
    """Studio's listening socket: each connection accepted (MAX_CONNECTIONS at most),
    what it speaks peeked at, and handed to uvicorn, over TLS or not. Closed (stops
    accepting) and waited for as uvicorn closes its servers."""

    def __init__(self, sock: socket.socket, protocol, tls: ssl.SSLContext | None, max_connections: int,
                 handshake_s: float):
        self.sock, self.protocol, self.tls = sock, protocol, tls
        self.max_connections, self.handshake_s = max_connections, handshake_s
        self.open = 0  # connections being served (or handed over)
        self._accepting: asyncio.Task | None = None
        self._handing: set[asyncio.Task] = set()

    @property
    def sockets(self):
        return [self.sock]

    def start(self) -> None:
        self._accepting = asyncio.get_running_loop().create_task(self._accept())

    async def _accept(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                conn, _ = await loop.sock_accept(self.sock)
            except asyncio.CancelledError:
                raise
            except OSError:  # out of files for a moment, a connection reset as it came
                await asyncio.sleep(0.05)
                continue
            if self.open >= self.max_connections:
                conn.close()  # nothing read, nothing answered
                continue
            self.open += 1
            task = loop.create_task(self._hand_over(conn, _Ticket(self)))
            self._handing.add(task)
            task.add_done_callback(self._handing.discard)

    async def _hand_over(self, conn: socket.socket, ticket: _Ticket) -> None:
        loop = asyncio.get_running_loop()
        try:
            conn.setblocking(False)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            tls = None
            if self.tls is not None:
                first = await self._first_byte(conn)
                if not first:
                    raise ConnectionError("closed before it said anything")
                tls = self.tls if first == b"\x16" else None  # a TLS handshake begins so; else plain HTTP
            more = {"ssl": tls, "ssl_handshake_timeout": self.handshake_s, "ssl_shutdown_timeout": self.handshake_s} \
                if tls is not None else {}
            await loop.connect_accepted_socket(lambda: self.protocol(ticket), conn, **more)
        except BaseException as e:  # a handshake that failed or took too long, a connection gone
            conn.close()
            ticket.give_back()
            if not isinstance(e, (OSError, ssl.SSLError, TimeoutError)):
                raise

    async def _first_byte(self, conn: socket.socket) -> bytes:
        """The connection's first byte, left to be read: within HANDSHAKE_S."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.handshake_s
        while True:
            try:
                return conn.recv(1, socket.MSG_PEEK)
            except (BlockingIOError, InterruptedError):
                pass
            ready = loop.create_future()
            loop.add_reader(conn.fileno(), lambda: ready.done() or ready.set_result(None))
            try:
                await asyncio.wait_for(ready, max(deadline - loop.time(), 0))
            finally:
                loop.remove_reader(conn.fileno())

    def close(self) -> None:
        if self._accepting is not None:
            self._accepting.cancel()
        for task in list(self._handing):
            task.cancel()

    async def wait_closed(self) -> None:
        waiting = [t for t in (self._accepting, *self._handing) if t is not None]
        if waiting:
            await asyncio.gather(*waiting, return_exceptions=True)


class Server(uvicorn.Server):
    """uvicorn's server, on Studio's own listening socket (Front) and connections; at a
    stop, the projects' event streams end first, so that nothing waits for them."""

    def __init__(self, config: uvicorn.Config, *, sock: socket.socket, tls: ssl.SSLContext | None, events=None):
        super().__init__(config)
        self.sock, self.tls, self.events = sock, tls, events
        self.front: Front | None = None

    async def startup(self, sockets=None) -> None:
        await self.lifespan.startup()
        anyio.to_thread.current_default_thread_limiter().total_tokens = WORKER_THREADS
        loop = asyncio.get_running_loop()
        config, state = self.config, self.server_state

        def protocol(ticket: _Ticket) -> Connection:
            return Connection(config=config, server_state=state, app_state=self.lifespan.state, _loop=loop,
                              ticket=ticket, read_timeout=READ_TIMEOUT_S, head_timeout=HEAD_TIMEOUT_S,
                              max_head=MAX_HEAD)

        self.front = Front(self.sock, protocol, self.tls, MAX_CONNECTIONS, HANDSHAKE_S)
        self.front.start()
        self.servers = [self.front]
        self.started = True

    async def shutdown(self, sockets=None) -> None:
        if self.events is not None:
            self.events.close()
        await super().shutdown(sockets=None)


class Served:
    """Studio served on one port: bound (and listening) when made, as make_server
    returns it; ``serve_forever()`` serves until ``shutdown()`` (from another thread)
    stops it, as socketserver's servers do; ``server_close()`` closes the port."""

    def __init__(self, app, sock: socket.socket, tls: ssl.SSLContext | None):
        self.app, self.socket, self.tls = app, sock, tls
        app.state.web.port = self.server_port
        config = uvicorn.Config(
            app, http="httptools", ws="none", lifespan="off", loop="auto",
            proxy_headers=False,  # who is asking behind a proxy: Studio's own rules (guard.client_address)
            server_header=False, access_log=False, log_config=None, log_level="warning",
            timeout_keep_alive=KEEP_ALIVE_S, timeout_graceful_shutdown=GRACEFUL_S,
            limit_concurrency=MAX_CONNECTIONS + 1,  # (connections are held to MAX_CONNECTIONS as they come)
            backlog=BACKLOG)
        self.server = Server(config, sock=sock, tls=tls, events=app.state.web.events)
        self._serving = threading.Event()
        self._stopped = threading.Event()

    @property
    def server_port(self) -> int:
        return self.socket.getsockname()[1]

    def serve_forever(self) -> None:
        self._serving.set()
        try:
            self.server.run()
        finally:
            self._stopped.set()

    def shutdown(self) -> None:
        self.server.should_exit = True
        if self._serving.is_set():
            self._stopped.wait()

    def server_close(self) -> None:
        self.socket.close()


def listen(host: str, port: int) -> socket.socket:
    """Studio's listening socket on ``host``:``port`` (0: any free port)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host.strip("[]"), port))
        sock.listen(BACKLOG)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


def make_server(studio, host: str = "127.0.0.1", port: int = 8080, allowed=(), *, accounts,
                secure_cookies: bool = False, tls: ssl.SSLContext | None = None, trusted_proxies=()) -> Served:
    """Studio's pages and API on ``host``:``port`` (app.create_app), served by uvicorn.
    Reached by names other than those of allowed_hosts (``allowed``: more of them), it
    answers 403.

    ``accounts`` is asked for by name, so that no caller gets an open server by
    default. None — no accounts, everyone may do everything — only on this computer (a
    loopback address), as ``storeypath review`` serves one project.

    ``tls``: served over HTTPS with that context (tls.py), and plain HTTP sent to the
    same port is answered with a redirect to its https:// address. The session cookie is
    Secure over HTTPS, or with ``secure_cookies`` (plain HTTP behind a proxy that speaks
    HTTPS). ``trusted_proxies``: the proxies whose X-Real-IP (or X-Forwarded-For) says
    who is asking (guard.client_address)."""
    if accounts is None and host not in LOOPBACK:
        raise ValueError(f"without accounts Studio serves this computer alone (127.0.0.1), not {host}")
    app = create_app(studio, accounts=accounts, allowed_hosts=allowed_hosts(host, allowed),
                     secure_cookies=secure_cookies, trusted_proxies=trusted_proxies, https=tls is not None)
    return Served(app, listen(host, port), tls)
