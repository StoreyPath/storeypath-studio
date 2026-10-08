"""The review editor's calls: a floor, its drawing and print, what is drawn on it, its
items, its spaces, zones and openings corrected, and the floor read again; who edits
it (one person at a time: the first change takes its lock, Done editing lets it go, an
admin may take it over)."""

from __future__ import annotations

from fastapi import Request

from ..review import floor_print, floor_print_png
from .calls import Body, Calls, May, TheStudio, answer

calls = Calls()
F = "/api/projects/{code}/floors/{floor_id}"


@calls.get(F)
def floor(code: str, floor_id: str, request: Request, may: May, studio: TheStudio):
    may.floor(code, floor_id, "view")
    return answer(request, {**studio.review(code).floor(floor_id), "lock": studio.lock_of(code, floor_id)})


@calls.get(F + "/drawing")
def drawing(code: str, floor_id: str, request: Request, may: May, studio: TheStudio):
    may.drawing(code, floor_id)
    return answer(request, studio.review(code).drawing(floor_id))


@calls.get(F + "/print")
def print_info(code: str, floor_id: str, request: Request, may: May, studio: TheStudio):
    may.drawing(code, floor_id)
    return answer(request, floor_print(studio.review(code), floor_id))


@calls.get(F + "/print.png")
def print_png(code: str, floor_id: str, request: Request, may: May, studio: TheStudio):
    may.drawing(code, floor_id)
    return answer(request, floor_print_png(studio.review(code), floor_id))


@calls.post(F + "/edits")
def edits(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")
    studio.review(code).edit(floor_id, body, by=may.user, editor=may.editor)
    return answer(request, studio.convert(code, floor_id, by=may.uid))


@calls.post(F + "/items")
def add_item(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")
    return answer(request, studio.review(code).add_item(floor_id, body, by=may.user, editor=may.editor))


@calls.post("/api/projects/{code}/items/{item_id}")
def change_item(code: str, item_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.item(code, item_id, body)
    return answer(request, studio.review(code).change_item(item_id, body, by=may.user, editor=may.editor))


@calls.post(F + "/convert")
def convert_floor(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")
    return answer(request, studio.convert(code, floor_id, body.get("force", False), by=may.uid, editor=may.editor))


@calls.post("/api/projects/{code}/objects/{object_id}")
def correct(code: str, object_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.object(code, object_id)
    return answer(request, studio.review(code).correct(object_id, body, by=may.user, editor=may.editor))


# ---- who edits the floor ------------------------------------------------------------------

@calls.post(F + "/release")
def release(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "view")
    return answer(request, studio.release(code, floor_id, by=may.user))


@calls.post(F + "/take-over")
def take_over(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.take_over(code, floor_id)
    done = studio.take_over(code, floor_id, by=may.user, editor=may.editor)
    if done["from"] is not None:
        may.audit("floor taken over", floor_id, project=code, was=done["from"].get("username"))
    return answer(request, done)
