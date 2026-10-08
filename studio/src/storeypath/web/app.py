"""Studio's pages and API as one FastAPI app (create_app), with the same URLs, bodies,
answers and rules as the server before it (server.py's route()).

Every call asks the gate first (calls.py): 401 when nobody is logged in, 403 when they
are and may not, 404 for what they may not see at all, as for what is not there. What
Studio cannot make sense of is answered 400 saying why; a project a job is changing,
409; a failure in Studio itself, 500 with nothing of what went wrong (that goes to the
log). guard.Guard checks each request before any of that, and gives every answer
Studio's security headers.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from ..accounts import Accounts, Refused
from ..assets import asset_dir
from ..bundle import ProjectExists
from ..cad import DrawingError
from ..errors import Conflict, Locked
from ..llm import ModelUnavailable
from ..review import Busy, NotFound
from ..server import Studio
from . import account, events, floors, pages, projects
from .calls import Web, as_json
from .guard import Guard, allowed_hosts as studio_names, trusted_networks


class StudioApp(FastAPI):
    """FastAPI, with guard.Guard outside everything else (its server-error answer too)."""

    def build_middleware_stack(self):
        return Guard(super().build_middleware_stack(), **self.state.guard)


def create_app(studio: Studio, *, accounts: Accounts | None, allowed_hosts=None, secure_cookies: bool = False,
               trusted_proxies=(), https: bool = False) -> FastAPI:
    """Studio's pages and API.

    ``accounts`` is asked for by name, so that no caller gets an open Studio by
    default: people log in, and each call is let through by what they may do (Gate).
    None — no accounts, everyone may do everything — is for this computer alone
    (``storeypath review``; make_server serves it on a loopback address only).

    ``allowed_hosts``: the names Studio answers to (guard.allowed_hosts; by default
    those of a Studio on this computer). ``https``: Studio is served over HTTPS: a
    request that comes in plain HTTP is sent on to https://, and the session cookie is
    Secure (as with ``secure_cookies``: plain HTTP behind a proxy that speaks HTTPS).
    ``trusted_proxies``: the proxies (addresses or networks; and those of
    STOREYPATH_TRUSTED_PROXIES) whose X-Real-IP (or X-Forwarded-For) says who is
    asking; a call through one that does not say is refused (guard.client_address)."""
    names = set(allowed_hosts) if allowed_hosts is not None else studio_names()
    app = StudioApp(openapi_url=None, docs_url=None, redoc_url=None, redirect_slashes=False)
    app.state.guard = {"names": names, "https": https}
    app.state.web = Web(studio=studio, accounts=accounts, proxies=trusted_networks(trusted_proxies),
                        secure=secure_cookies or https,
                        pages=Path(str(resources.files("storeypath") / "review_app")).resolve(),
                        viewer=asset_dir("viewer").resolve(), events=events.Events())
    app.state.web.events.attach(studio)  # the database's changes, to the streams (live.py)
    for part in (account, projects, floors, events, pages):  # the pages last: any other GET goes there
        part.calls.add_to(app)
    for kind, answer in ANSWERS.items():
        app.add_exception_handler(kind, answer)
    return app


# ---- what is refused, as HTTP ------------------------------------------------------------

async def _refused(request: Request, e: Refused):
    headers = {"Retry-After": str(e.more["retry_after"])} if "retry_after" in e.more else {}
    if getattr(e, "close", False):
        headers["Connection"] = "close"
    return as_json({"error": str(e), **e.more}, e.status, headers)


async def _not_found(request: Request, e: NotFound):
    return as_json({"error": str(e)}, 404)


async def _exists(request: Request, e: ProjectExists):
    return as_json({"error": str(e), "code": e.code, "name": e.name, "building": e.building}, 409)


async def _busy(request: Request, e: Busy):  # a job is changing the project: nothing changed
    return as_json({"error": str(e), "busy": True}, 409)


async def _locked(request: Request, e: Locked):  # another person is editing the floor: nothing changed
    return as_json({"error": str(e), "locked": e.holder}, 423)


async def _conflict(request: Request, e: Conflict):  # nothing to undo, or changed since by someone
    return as_json({"error": str(e), **e.more}, 409)


async def _bad(request: Request, e: Exception):
    return as_json({"error": str(e).strip("'\"")}, 400)


async def _invalid(request: Request, e: RequestValidationError):
    return as_json({"error": "the request is not what this call takes"}, 400)


async def _no_call(request: Request, e: StarletteHTTPException):
    """No call answers this method and path (404 and 405 alike, as before)."""
    if e.status_code in (404, 405):
        return as_json({"error": "not found"}, 404)
    return as_json({"error": str(e.detail)}, e.status_code)


async def _cut_short(request: Request, e: ClientDisconnect):
    return as_json({"error": "the request was cut short"}, 400, {"Connection": "close"})


async def _failed(request: Request, e: Exception):
    """Anything else: what went wrong goes to the log (the exception goes on to the
    server, which writes it there), never in the answer."""
    return as_json({"error": "something went wrong in Studio: its log says what"}, 500)


ANSWERS = {
    Refused: _refused, NotFound: _not_found, ProjectExists: _exists, Busy: _busy, Locked: _locked, Conflict: _conflict,
    DrawingError: _bad, ValueError: _bad, KeyError: _bad, ModelUnavailable: _bad,
    RequestValidationError: _invalid, StarletteHTTPException: _no_call, ClientDisconnect: _cut_short,
    Exception: _failed,
}
