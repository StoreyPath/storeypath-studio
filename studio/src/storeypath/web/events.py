"""A project's events, sent to its open pages as they happen (Server-Sent Events):
``GET /api/projects/<code>/events``, for anyone with any access to the project (as its
page). A page keeps one stream open (EventSource); it is sent what happens in the
project that the person may see:

    event: job        a job's progress: {id, title, state, error, from, log[, result]}:
                      the log lines from ``from`` on; the result once it is done. A job
                      is sent to whoever may follow it (an admin, or view on what it
                      works on: its project, building or floor, as GET /api/jobs/<id>)
    event: heartbeat  {at}, at once and every HEARTBEAT_S: the stream is checked then
                      (the session, the person's access) and ends when they may no
                      longer see the project

What else happens is sent with Events.publish(code, kind, data, scope), from any
thread: each stream sends it on (``event: <kind>``) when its person may see ``scope``
(("project", None), ("building", id), ("floor", id)). Changes (who changed what, as
the history records it) and presence (who is viewing, who is editing a floor) come
this way. A stream that falls behind by STREAM_QUEUE events is ended: the page opens
it again (EventSource does, after ``retry``) and reads what it shows afresh.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable

import anyio
from fastapi import Request
from starlette.responses import StreamingResponse

from ..accounts import LOCAL, Refused, Sight
from ..review import NotFound
from ..server import Gate, Job
from .calls import Calls, May, TheStudio, web_of

HEARTBEAT_S = 15.0
JOB_POLL_S = 0.5  # a job's progress is looked at this often while a stream is open
JOBS_KEPT = 50  # jobs followed per project, the latest
STREAM_QUEUE = 1000  # events waiting to be sent on one stream, at most
RETRY_MS = 3000  # a page's EventSource opens the stream again after this


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def message(kind: str, data) -> bytes:
    """One event, as a stream sends it."""
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class _Stream:
    """One page's stream: what its person may see (refreshed every heartbeat), and the
    events published for it, waiting to be sent."""

    def __init__(self, loop: asyncio.AbstractEventLoop, sight: Sight):
        self.loop, self.sight = loop, sight
        self.waiting: asyncio.Queue = asyncio.Queue(STREAM_QUEUE)

    def sees(self, scope) -> bool:
        kind, scope_id = scope
        return bool(self.sight.scope(kind, scope_id))

    def offer(self, item) -> None:
        """From any thread: an event (or None: the stream ends)."""
        try:
            self.loop.call_soon_threadsafe(self._put, item)
        except RuntimeError:  # its event loop is gone
            pass

    def _put(self, item) -> None:
        try:
            self.waiting.put_nowait(item)
        except asyncio.QueueFull:  # fallen behind: ended, to be opened again
            while not self.waiting.empty():
                self.waiting.get_nowait()
            self.waiting.put_nowait(None)


class Events:
    """The events of every project, and the streams open on each."""

    def __init__(self):
        self._lock = threading.Lock()
        self._streams: dict[str, set[_Stream]] = {}
        self._jobs: dict[str, OrderedDict[str, Job]] = {}
        self._closed = False

    def publish(self, code: str, kind: str, data, scope: tuple[str, str | None] = ("project", None)) -> None:
        """Something that happened in a project, sent on to each of its streams whose
        person may see ``scope``. From any thread; never waits."""
        with self._lock:
            streams = list(self._streams.get(code, ()))
        for s in streams:
            s.offer((kind, data, tuple(scope)))

    def watch(self, job: Job) -> None:
        """A job started on a project: its progress is sent on that project's streams."""
        if not job.project:
            return
        with self._lock:
            jobs = self._jobs.setdefault(job.project, OrderedDict())
            jobs[job.id] = job
            jobs.move_to_end(job.id)
            while len(jobs) > JOBS_KEPT:
                jobs.popitem(last=False)

    def jobs(self, code: str) -> list[Job]:
        with self._lock:
            return list(self._jobs.get(code, {}).values())

    def watching(self, code: str) -> int:
        """How many streams are open on a project."""
        with self._lock:
            return len(self._streams.get(code, ()))

    def close(self) -> None:
        """Studio stops: every stream ends."""
        with self._lock:
            self._closed = True
            streams = [s for group in self._streams.values() for s in group]
        for s in streams:
            s.offer(None)

    async def stream(self, code: str, sight: Sight, check: Callable[[], Sight | None]):
        """A page's stream on a project (the bytes sent): ``sight``, what its person may
        see now; ``check``, what they may see then (None: no longer), asked every
        heartbeat in a worker thread."""
        loop = asyncio.get_running_loop()
        me = _Stream(loop, sight)
        with self._lock:
            if self._closed:
                return
            self._streams.setdefault(code, set()).add(me)
        try:
            yield f"retry: {RETRY_MS}\n\n".encode()
            yield message("heartbeat", {"at": _now()})
            # jobs over before the page came are not sent; those still to run or running are
            sent = {j.id: (j.state, len(j.log)) for j in self.jobs(code) if j.state in ("done", "failed")}
            beat = loop.time() + HEARTBEAT_S
            while True:
                for j in self.jobs(code):
                    now = (j.state, len(j.log))  # (its state first: a job that fails says so, then why)
                    if sent.get(j.id) != now and me.sees(j.scope):
                        yield message("job", _progress(j, *now, sent.get(j.id, ("", 0))[1]))
                        sent[j.id] = now
                try:
                    item = await asyncio.wait_for(me.waiting.get(), min(JOB_POLL_S, max(0.0, beat - loop.time())))
                except TimeoutError:
                    item = ()
                if item is None:
                    return
                if item:
                    kind, data, scope = item
                    if me.sees(scope):
                        yield message(kind, data)
                if loop.time() >= beat:
                    again = await anyio.to_thread.run_sync(check)
                    if again is None:
                        return
                    me.sight = again
                    yield message("heartbeat", {"at": _now()})
                    beat = loop.time() + HEARTBEAT_S
        finally:
            with self._lock:
                group = self._streams.get(code)
                if group is not None:
                    group.discard(me)
                    if not group:
                        del self._streams[code]


def _progress(job: Job, state: str, lines: int, since: int) -> dict:
    """A job as it was when looked at (``state``, ``lines`` of its log): its log from
    ``since`` (what was sent before) to there; its result once it is done."""
    out = {"id": job.id, "title": job.title, "state": state, "error": job.error, "from": since,
           "log": job.log[since:lines]}
    if state == "done":
        out["result"] = job.result
    return out


# ---- the call -----------------------------------------------------------------------------

calls = Calls()


@calls.get("/api/projects/{code}/events")
def events(code: str, request: Request, may: May, studio: TheStudio):
    sight = may.see(code)
    web = web_of(request)

    def check() -> Sight | None:
        """What the person may see of the project now: their session and access again."""
        user = web.accounts.session(may.token) if web.accounts is not None else LOCAL
        try:
            return Gate(studio, web.accounts, user, may.address, may.token).see(code)
        except (Refused, NotFound):
            return None

    return StreamingResponse(web.events.stream(code, sight, check), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no"})  # (a proxy in front: send each as it comes)
