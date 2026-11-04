"""The calls of projects: the list, a new one, a file opened, jobs, a project's page,
deleting it, its history (and each person's undo and redo), sharing it, its drawings
(and each drawn whole, to look at), its floors added and read again, its buildings and
sites, and its packages."""

from __future__ import annotations

import logging
import time

from fastapi import Request, Response
from starlette.responses import FileResponse

from ..accounts import Refused
from ..bundle import ProjectExists
from ..errors import Busy, NotFound
from .calls import Body, Calls, May, Query, Sent, TheStudio, answer, unsealed, web_of

calls = Calls()
P = "/api/projects/{code}"
log = logging.getLogger("storeypath.open")
REFUSED = (ValueError, Refused, ProjectExists, NotFound, Busy)  # said to the person: no fault of Studio's


@calls.get("/api/projects")
def projects(request: Request, may: May, studio: TheStudio):
    may.logged_in()
    return answer(request, studio.projects(may.sight_of))


@calls.post("/api/projects")
def create(request: Request, may: May, studio: TheStudio, body: Body):
    may.create()
    return answer(request, may.made(studio.create(body.get("name", ""), may.kept_codes(), by=may.user)))


@calls.put("/api/open")
def open_file(request: Request, may: May, studio: TheStudio, sent: Sent, query: Query):
    """A project file or a package opened (sealed with a password: X-StoreyPath-Password).
    Each one opened, or not and why, is written to Studio's log, with how long it took:
    the page may have lost the answer (a proxy that stopped waiting), the log has it."""
    allow = may.opening()
    started, size, who = time.monotonic(), None, may.user.username
    try:
        data = unsealed(request, sent.read())
        size = len(data)
        opened = studio.open(data, (query.get("replace") or [None])[0], allow,
                             learn_types=may.user.can("catalogue"), by=may.user)
    except Exception as e:
        said = f"a file not opened ({_opening(who, size, started)}): {type(e).__name__}: {e}"
        if isinstance(e, REFUSED):
            log.warning(said)
        else:
            log.exception(said)
        raise
    log.info(f"opened {opened.get('code')} ({opened.get('how')}; {_opening(who, size, started)})")
    return answer(request, may.opened(opened))


def _opening(who: str, size: int | None, started: float) -> str:
    """Who sent a file to open, how big it was, how long it took."""
    big = "size unknown" if size is None else f"{size / 1e6:.1f} MB"
    return f"by {who}, {big}, {time.monotonic() - started:.0f} s"


@calls.get("/api/jobs/{job_id}")
def job(job_id: str, request: Request, may: May):
    found = may.job(job_id)
    return answer(request, found)


@calls.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str, request: Request, may: May, studio: TheStudio):
    """A read of drawings stopped (Jobs.stop): by who started it, an admin, or whoever may read them again."""
    found = may.stop_job(job_id)
    who = may.user
    return answer(request, studio.jobs.stop(found, (who.name or who.username) if who is not None else "someone").view())


@calls.get(P)
def project(code: str, request: Request, may: May, studio: TheStudio):
    sight = may.see(code)
    return answer(request, studio.project(code, sight))


@calls.get(P + "/review")
def review_project(code: str, request: Request, may: May, studio: TheStudio):
    sight = may.see(code)
    return answer(request, studio.review_project(code, sight))


@calls.post(P + "/delete")
def delete(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.own(code)
    return answer(request, may.deleted(code, studio.delete(code, body, by=may.user)))


# ---- history: each person undoes and redoes their own changes ------------------------------

def _undone(request: Request, done: dict):
    job = done.pop("job", None)
    if job is not None:
        web_of(request).events.watch(job)  # what was drawn, read again: its progress on the project's events
    return answer(request, {**done, "job": job.view() if job is not None else None})


@calls.post(P + "/undo")
def undo(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    sight = may.undo(code, body.get("floor"))
    return _undone(request, studio.undo(code, body.get("floor"), sight, by=may.user, editor=may.editor))


@calls.post(P + "/redo")
def redo(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    sight = may.undo(code, body.get("floor"))
    return _undone(request, studio.undo(code, body.get("floor"), sight, by=may.user, editor=may.editor, redo=True))


@calls.get(P + "/history")
def history(code: str, request: Request, may: May, studio: TheStudio, query: Query):
    sight = may.history(code, (query.get("floor") or [None])[0])
    return answer(request, studio.history(code, (query.get("floor") or [None])[0], (query.get("n") or [None])[0],
                                          sight, by=may.user))


# ---- sharing ------------------------------------------------------------------------------

@calls.get(P + "/access")
def access(code: str, request: Request, may: May):
    sight = may.share_some(code)
    return answer(request, may.access(code, sight))


@calls.post(P + "/access")
def grant(code: str, request: Request, may: May, body: Body):
    sight = may.share_some(code)
    return answer(request, may.grant(code, sight, body))


@calls.post(P + "/owner")
def owner(code: str, request: Request, may: May, body: Body):
    may.admin()
    return answer(request, may.set_owner(code, body))


# ---- the project's drawings: they are the project's, and one may hold several floors ------

@calls.get(P + "/drawings/{name}/words")
def words(code: str, name: str, request: Request, may: May, studio: TheStudio):
    may.project(code, "edit")
    return answer(request, studio.words(code, name))


@calls.get(P + "/jobs")
def project_jobs(code: str, request: Request, may: May):
    """Its jobs the person may see: to run, running, or ended in the last 30 minutes (a page
    opened again shows them, and follows those still going)."""
    listed = may.jobs(code)
    return answer(request, listed)


@calls.get(P + "/incoming")
def incoming(code: str, request: Request, may: May):
    """The drawings the person sent that wait for them to choose what to keep."""
    waiting = may.waiting(code)
    return answer(request, waiting)


@calls.post(P + "/incoming/{token}")
def keep_private(code: str, token: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.keep_private(code, token, body, by=may.uid))


@calls.post(P + "/incoming/{token}/cancel")
def cancel_private(code: str, token: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.cancel_private(code, token))


@calls.put(P + "/drawings/{name}")
def upload(code: str, name: str, request: Request, may: May, studio: TheStudio, sent: Sent, query: Query):
    may.project(code, "edit")
    return answer(request, studio.upload(code, name, sent.read(), private=query.get("private", ["1"])[0] != "0",
                                         by=may.uid))


@calls.post(P + "/drawings/{name}/delete")
def delete_drawing(code: str, name: str, request: Request, may: May, studio: TheStudio, body: Body):
    """A drawing taken out of the project, by whoever may add one: refused (409, naming
    them) while a floor reads from it. Recorded in the audit log."""
    may.project(code, "edit")
    done = studio.delete_drawing(code, name, by=may.user)
    may.audit("drawing deleted", done["deleted"], project=code)
    return answer(request, done)


@calls.post(P + "/drawings/{name}/plans")
def plans(code: str, name: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.plans(code, name, body.get("units") or None, by=may.uid))


@calls.post(P + "/drawings/{name}/view")
def view(code: str, name: str, request: Request, may: May, studio: TheStudio):
    """A drawing drawn whole for the drawing viewer (drawview.py): where to get it when it
    is made, else the job that makes it. Who may see its words may see it."""
    may.project(code, "edit")
    return answer(request, studio.view(code, name, by=may.uid))


@calls.get(P + "/drawings/{name}/view")
def view_file(code: str, name: str, request: Request, may: May, studio: TheStudio):
    """The view made, sent from the disk as it is (gzip already: not compressed again;
    the page opens it). Asked for by its key (?v=, Studio.view's url): kept by the browser."""
    may.project(code, "edit")
    path = studio.view_file(code, name)
    return answer(request, FileResponse(path, media_type="application/gzip",
                                        headers={"Cache-Control": "private, max-age=86400"}))


@calls.post(P + "/floors")
def add_floors(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.add_floors(code, body)
    return answer(request, studio.add_floors(code, body, by=may.uid))


@calls.post(P + "/convert")
def convert(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.convert(code, body.get("floor"))
    return answer(request, studio.convert(code, body.get("floor"), body.get("force", False), by=may.uid))


# ---- buildings and sites -----------------------------------------------------------------

@calls.post(P + "/buildings/{building_id}/site")
def move(code: str, building_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.building(code, building_id, "edit")
    return answer(request, studio.move(code, building_id, body, by=may.user))


@calls.post(P + "/buildings/{building_id}/name")
def rename_building(code: str, building_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.building(code, building_id, "edit")
    return answer(request, studio.rename_building(code, building_id, body, by=may.user))


@calls.post(P + "/buildings/{building_id}/placement")
def place(code: str, building_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.building(code, building_id, "edit")
    return answer(request, studio.place(code, building_id, body, by=may.user))


@calls.post(P + "/locations/{location_id}/arrange")
def arrange(code: str, location_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.arrange(code, location_id, by=may.user))


@calls.post(P + "/locations/{location_id}/placement")
def place_site(code: str, location_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.place_site(code, location_id, body, by=may.user))


# ---- a location's layout (format 0.12) ---------------------------------------------------

@calls.post(P + "/layouts")
def add_layout(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    """A site plan of one of the project's drawings used as a location's layout: a job."""
    may.add_layout(code, body)
    return answer(request, studio.add_layout(code, body, by=may.uid))


@calls.post(P + "/footprints/{footprint_id}")
def footprint(code: str, footprint_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    """A footprint said to be one of the location's buildings (placed on it), of none, or a new one's."""
    may.footprint(code, footprint_id, body)
    return answer(request, studio.footprint(code, footprint_id, body, by=may.user))


@calls.post(P + "/locations/{location_id}/layout/place")
def place_layout(code: str, location_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.place_layout(code, location_id, body, by=may.user))


@calls.post(P + "/locations/{location_id}/layout/match")
def match_layout(code: str, location_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.match_layout(code, location_id, by=may.uid))


@calls.post(P + "/locations/{location_id}/north")
def north(code: str, location_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.set_north(code, location_id, body, by=may.user))


# ---- packages ----------------------------------------------------------------------------

@calls.post(P + "/export")
def export(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    building = may.export(code, body)  # (a location's layout: its land's ID, 0.12)
    job = studio.export(code, {"building": building, "item_types": body.get("item_types"),
                               "drawings": body.get("drawings", False)}, by=may.uid)
    may.audit("export", building)
    return answer(request, job)


@calls.get(P + "/exports/{name}")
def export_file(code: str, name: str, request: Request, may: May, studio: TheStudio):
    may.export_file(code, name)
    return answer(request, studio.export_file(code, name))


@calls.post(P + "/exports/{name}")
def export_file_sealed(code: str, name: str, request: Request, may: May, studio: TheStudio, body: Body):
    """A package, sealed with the password sent ({password}: sealed.py), to send where
    others could read it."""
    may.export_file(code, name)
    return answer(request, _sealed(studio.export_file(code, name), body))


def _sealed(download, body: dict):
    from ..sealed import check_password, seal, sealed_name
    from ..server import Download

    password = check_password(body.get("password"))
    return Download(seal(download.data, password), sealed_name(download.name))


@calls.get(P + "/preview.storeypath")
def preview(code: str, request: Request, may: May, studio: TheStudio, query: Query):
    """The 3D view's package, with its tag: the browser keeps it, and asks again with the
    tag (If-None-Match), answered 304 and nothing sent while it is the same."""
    sight = may.preview(code, (query.get("building") or [None])[0])
    building = (query.get("building") or [None])[0]
    tag = studio.preview_tag(code, building, sight)
    kept = {"ETag": tag, "Cache-Control": "private, no-cache"}  # (kept, never shown again without asking)
    if tag in _tags(request.headers.get("if-none-match")):
        return Response(status_code=304, headers=kept)
    return Response(studio.preview(code, building, sight), media_type="application/zip", headers=kept)


def _tags(said: str | None) -> set[str]:
    """The tags an If-None-Match says (a weak one as the same)."""
    return {t.strip().removeprefix("W/") for t in (said or "").split(",") if t.strip()}


# (the project file by its old name too)
@calls.get(P + "/project.storeypath-project", P + "/project.storeypath")
def project_file(code: str, request: Request, may: May, studio: TheStudio):
    may.project(code, "view")
    return answer(request, studio.project_file(code))


@calls.post(P + "/project.storeypath-project")
def project_file_sealed(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    """The project file, sealed with the password sent ({password})."""
    may.project(code, "view")
    from ..sealed import check_password

    check_password(body.get("password"))  # (before the file is made)
    return answer(request, _sealed(studio.project_file(code), body))
