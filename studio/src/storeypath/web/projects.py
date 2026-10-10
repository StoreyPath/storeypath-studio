"""The calls of projects: the list, a new one, a file opened, jobs, a project's page,
deleting it, its history (and each person's undo and redo), sharing it, its drawings,
its floors added and read again, its buildings and sites, and its packages."""

from __future__ import annotations

from fastapi import Request

from .calls import Body, Calls, May, Query, Sent, TheStudio, answer, web_of

calls = Calls()
P = "/api/projects/{code}"


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
    allow = may.opening()
    return answer(request, may.opened(studio.open(sent.read(), (query.get("replace") or [None])[0], allow,
                                                  learn_types=may.user.can("catalogue"), by=may.user)))


@calls.get("/api/jobs/{job_id}")
def job(job_id: str, request: Request, may: May):
    found = may.job(job_id)
    return answer(request, found)


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


@calls.post(P + "/drawings/{name}/plans")
def plans(code: str, name: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.project(code, "edit")
    return answer(request, studio.plans(code, name, body.get("units") or None, by=may.uid))


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


# ---- packages ----------------------------------------------------------------------------

@calls.post(P + "/export")
def export(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    building = may.export(code, body)
    job = studio.export(code, {"building": building, "item_types": body.get("item_types")}, by=may.uid)
    may.audit("export", building)
    return answer(request, job)


@calls.get(P + "/exports/{name}")
def export_file(code: str, name: str, request: Request, may: May, studio: TheStudio):
    may.export_file(code, name)
    return answer(request, studio.export_file(code, name))


@calls.get(P + "/preview.storeypath")
def preview(code: str, request: Request, may: May, studio: TheStudio, query: Query):
    sight = may.preview(code, (query.get("building") or [None])[0])
    return answer(request, studio.preview(code, (query.get("building") or [None])[0], sight))


# (the project file by its old name too)
@calls.get(P + "/project.storeypath-project", P + "/project.storeypath")
def project_file(code: str, request: Request, may: May, studio: TheStudio):
    may.project(code, "view")
    return answer(request, studio.project_file(code))
