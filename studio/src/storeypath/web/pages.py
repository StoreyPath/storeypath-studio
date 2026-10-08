"""Studio's pages (review_app/) and the viewer's files (/viewer/…, /theme.js): no
session needed, as they hold no data. A path that resolves outside their folder, or to
nothing, is 404. Added last: any GET no call answers comes here, and one under /api/
is 404 (a call that is not there)."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from starlette.responses import FileResponse

from ..review import CONTENT_TYPES
from .calls import Calls, as_json, web_of

calls = Calls()
PAGES = ("/theme.js", "/viewer/{path:path}", "/{path:path}")  # every route that answers without a session


def _file(path: Path, root: Path):
    path = path.resolve()
    if root not in path.parents or not path.is_file():
        return as_json({"error": "not found"}, 404)
    return FileResponse(path, media_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"))


@calls.get("/theme.js")
def theme(request: Request):
    viewer = web_of(request).viewer
    return _file(viewer / "src" / "theme.js", viewer)


@calls.get("/viewer/{path:path}")
def viewer_file(path: str, request: Request):
    viewer = web_of(request).viewer
    return _file(viewer / path, viewer)


@calls.get("/{path:path}")
def page(path: str, request: Request):
    if path == "api" or path.startswith("api/"):
        return as_json({"error": "not found"}, 404)
    pages = web_of(request).pages
    return _file(pages / (path or "index.html"), pages)
