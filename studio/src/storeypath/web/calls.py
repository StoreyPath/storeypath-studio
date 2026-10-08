"""What every call of Studio's API has: who is asking (a Gate, asked first in every
call: server.Gate's rules, unchanged), what was sent, and the answer as HTTP.

A call is a plain function (``def``: FastAPI runs it in its thread pool, so nothing it
does holds up the event loop) whose first line asks the gate, ``may.…(…)``; a test
checks that every one does, and tries each as people with each kind of access
(tests/test_auth_http.py). What it returns is answered by answer(): JSON, a file, a
download, a session begun or ended, a job (followed on the project's events), a
backup streamed as it is written.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Callable

import anyio
from fastapi import Depends, Request
from starlette.responses import FileResponse, Response, StreamingResponse

from ..accounts import LOCAL, Accounts, Refused, cookie_name
from ..review import File
from ..server import Download, Gate, Job, LoggedIn, LoggedOut, Stream, Studio
from .guard import Said, client_address

PARSE_IN_THREAD = 256 * 1024  # a JSON body this large is read in a worker thread, not the event loop


@dataclass
class Web:
    """What the app's calls share: Studio, its accounts (None: everyone may do
    everything, on this computer alone), the proxies it trusts, whether its cookie is
    Secure, where its pages are, and the projects' events."""

    studio: Studio
    accounts: Accounts | None
    proxies: list
    secure: bool
    pages: Path
    viewer: Path
    events: object  # events.Events
    port: int = 0  # the port it listens on, when the Host says none (set when served)

    def cookie(self, name: str, token: str | None) -> str:
        parts = [f"{name}={token or ''}", "HttpOnly", "SameSite=Strict", "Path=/"]
        if token is None:
            parts.append("Max-Age=0")
        if self.secure:
            parts.append("Secure")
        return "; ".join(parts)


def web_of(request: Request) -> Web:
    return request.app.state.web


# ---- refusals that are not the gate's ---------------------------------------------------

class BadBody(Refused):
    """What was sent is not what a call takes (400)."""

    status = 400


class ProxySilent(Refused):
    """A trusted proxy that does not say who is asking (400): never taken as its own
    address. Its connection ends."""

    status = 400
    close = True


# ---- who is asking -------------------------------------------------------------------------

def cookie_of(request: Request) -> str:
    """The session cookie's name: by the port the browser reached Studio on (its
    Host's; else the one Studio listens on), so that another Studio on the same machine
    (browsers keep one host's cookies for all its ports) keeps its own."""
    port = re.search(r":(\d{1,5})$", (request.headers.get("host") or "").strip())
    if port:
        return cookie_name(port.group(1))
    server = request.scope.get("server")
    return cookie_name(server[1] if server else web_of(request).port)


def session_token(request: Request) -> str | None:
    """The session's token, from the request's cookie."""
    name = cookie_of(request)
    for part in (request.headers.get("cookie") or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name and value:
            return value
    return None


def gate(request: Request) -> Gate:
    """Who is asking, and what they may do (server.Gate): their session, from where.
    A def (not async): the session is looked up in the thread pool."""
    web = web_of(request)
    peer = request.client.host if request.client else ""
    address = client_address(peer, Said(request.headers), web.proxies)
    if address is None:
        raise ProxySilent("Studio is reached through a trusted proxy that does not say who is asking: "
                          "set X-Real-IP there (nginx: proxy_set_header X-Real-IP $remote_addr;)")
    token = session_token(request)
    user = web.accounts.session(token) if web.accounts is not None else LOCAL
    return Gate(web.studio, web.accounts, user, address, token)


async def the_studio(request: Request) -> Studio:
    return web_of(request).studio


# ---- what was sent -------------------------------------------------------------------------

def _not_a_number(token: str):
    raise ValueError(f"{token} is not a number JSON has")


def _parse(raw: bytes):
    # NaN and Infinity are not JSON: refused, never stored
    return json.loads(raw or b"{}", parse_constant=_not_a_number)


async def json_body(request: Request) -> dict:
    """A POST's body: a JSON object (an empty body is {}). Its size was checked as it
    came (guard.Guard); a large one is parsed in a worker thread."""
    raw = await request.body()
    try:
        body = await anyio.to_thread.run_sync(_parse, raw) if len(raw) > PARSE_IN_THREAD else _parse(raw)
    except ValueError:
        raise BadBody("invalid JSON") from None
    if not isinstance(body, dict):
        raise BadBody("send a JSON object")
    return body


class Upload:
    """A PUT's body, a file: read only when the call asks for it, after the gate (a
    person who may not send it is answered without it read; the connection then ends,
    guard.Guard). read() is called from the call's worker thread: the event loop reads
    the body meanwhile."""

    def __init__(self, request: Request):
        self.request = request

    async def _chunks(self) -> list[bytes]:
        return [chunk async for chunk in self.request.stream() if chunk]

    def read(self) -> bytes:
        return b"".join(anyio.from_thread.run(self._chunks))


async def upload_of(request: Request) -> Upload:
    return Upload(request)


async def query_of(request: Request) -> dict[str, list[str]]:
    """The query, as parse_qs reads it (each name a list; blank values left out)."""
    from urllib.parse import parse_qs

    return parse_qs(request.url.query)


May = Annotated[Gate, Depends(gate)]
Body = Annotated[dict, Depends(json_body)]
Sent = Annotated[Upload, Depends(upload_of)]
Query = Annotated[dict, Depends(query_of)]
TheStudio = Annotated[Studio, Depends(the_studio)]


class Calls:
    """The calls of one part of the API, in the order written: create_app adds each to
    the app as a route of its own (app.routes lists them all, for the tests)."""

    def __init__(self):
        self.calls: list[tuple[str, str, Callable]] = []

    def _add(self, method: str, *paths: str):
        def add(endpoint):
            self.calls += [(method, path, endpoint) for path in paths]
            return endpoint
        return add

    def get(self, *paths: str):
        return self._add("GET", *paths)

    def post(self, *paths: str):
        return self._add("POST", *paths)

    def put(self, *paths: str):
        return self._add("PUT", *paths)

    def add_to(self, app) -> None:
        for method, path, endpoint in self.calls:
            app.add_api_route(path, endpoint, methods=[method], include_in_schema=False)


# ---- the answer ---------------------------------------------------------------------------

def as_json(data, status: int = 200, headers: dict | None = None) -> Response:
    return Response(json.dumps(data, ensure_ascii=False).encode(), status_code=status,
                    media_type="application/json", headers=headers)


def answer(request: Request, data) -> Response:
    """What a call returned, as HTTP."""
    web = web_of(request)
    if isinstance(data, Response):
        return data
    if isinstance(data, LoggedIn):
        return as_json(data.data, headers={"Set-Cookie": web.cookie(cookie_of(request), data.token)})
    if isinstance(data, LoggedOut):
        return as_json({"logged_out": True}, headers={"Set-Cookie": web.cookie(cookie_of(request), None)})
    if isinstance(data, Stream):
        return Streamed(data)
    if isinstance(data, Download):  # made on the fly, saved by the browser
        return Response(data.data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{data.name}"'})
    if isinstance(data, File):  # a file shown as it is (a floor's print)
        return Response(data.data, media_type=data.content_type, headers={"Cache-Control": "private, max-age=86400"})
    if isinstance(data, Path):  # a file to download, sent from the disk as it is read
        return FileResponse(data, media_type="application/zip", filename=data.name,
                            content_disposition_type="attachment")
    if isinstance(data, bytes):  # a package made on the fly
        return Response(data, media_type="application/zip")
    if isinstance(data, Job):
        web.events.watch(data)  # its progress, on the project's events
        data = data.view()
    return as_json(data)


# ---- a download written as it is made --------------------------------------------------------

PIPE_DEPTH = 8  # pieces between the writer and the connection at most…
PIPE_PIECE = 64 * 1024  # …of about this much each


class _Pipe:
    """Bytes written in one thread (the backup's writer) and sent from the event loop:
    at most PIPE_DEPTH pieces between them, so a slow download holds little in memory.
    Writing to it once it is stopped (the download went away) raises BrokenPipeError,
    which ends the writer."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop, self.items = loop, asyncio.Queue()
        self.room = threading.Semaphore(PIPE_DEPTH)
        self.stopped = threading.Event()
        self.piece = bytearray()

    def write(self, data) -> int:
        self.piece += data
        if len(self.piece) >= PIPE_PIECE:
            self._send(bytes(self.piece))
            self.piece.clear()
        return len(data)

    def flush(self) -> None:
        pass

    def _send(self, piece: bytes) -> None:
        while not self.room.acquire(timeout=0.25):
            if self.stopped.is_set():
                break
        if self.stopped.is_set():
            raise BrokenPipeError("the download stopped")
        self._post(("data", piece))

    def _post(self, item) -> None:
        try:
            self.loop.call_soon_threadsafe(self.items.put_nowait, item)
        except RuntimeError:  # the event loop is gone
            self.stopped.set()

    def run(self, write: Callable) -> None:
        """The writer's thread: ``write(self)``, then the end (or what went wrong)."""
        try:
            write(self)
            if self.piece:
                self._send(bytes(self.piece))
            self._post(("end", None))
        except BaseException as e:  # sent on: the download fails
            self._post(("error", e))

    async def next(self):
        kind, value = await self.items.get()
        if kind == "data":
            self.room.release()
        return kind, value


class Streamed(StreamingResponse):
    """A download written as it is made (a backup, Gate.backup): no Content-Length;
    its connection closes after it. It is written in a thread of its own and sent as it
    is written; ``done`` is told how it went ("ok", "interrupted": the download went
    away, "failed") once it is sent whole, before its end is, or when it stops."""

    def __init__(self, stream: Stream):
        super().__init__(iter(()), media_type=stream.content_type, headers={
            "Content-Disposition": f'attachment; filename="{stream.name}"', "Connection": "close"})
        self.stream = stream

    async def __call__(self, scope, receive, send) -> None:
        loop = asyncio.get_running_loop()
        pipe = _Pipe(loop)
        writer = threading.Thread(target=pipe.run, args=(self.stream.write,), daemon=True,
                                  name=f"download {self.stream.name}")
        outcome, told = "failed", False
        gone = asyncio.Event()  # the download went away (uvicorn's send says nothing of it)

        async def listen():
            while (await receive())["type"] != "http.disconnect":
                pass
            gone.set()

        async def tell(how: str) -> None:
            nonlocal told
            told = True
            if how != "ok":
                pipe.stopped.set()  # the writer ends at its next write
            with anyio.CancelScope(shield=True):
                if writer.ident is not None:
                    await anyio.to_thread.run_sync(writer.join)
                await anyio.to_thread.run_sync(self.stream.done, how)

        listener, away = loop.create_task(listen()), loop.create_task(gone.wait())
        try:
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            writer.start()
            while True:
                getting = loop.create_task(pipe.next())
                await asyncio.wait({getting, away}, return_when=asyncio.FIRST_COMPLETED)
                if not getting.done() or gone.is_set():
                    getting.cancel()
                    outcome = "interrupted"
                    return
                kind, value = getting.result()
                if kind == "error":  # the writer failed: the download is cut short (no end sent)
                    raise value
                if kind == "end":
                    break
                await send({"type": "http.response.body", "body": value, "more_body": True})
            # sent whole: told so before its end is, so whoever reads it to the end finds it told
            await tell("ok")
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            listener.cancel()
            away.cancel()
            if not told:
                await tell(outcome)
