"""The review editor's calls: a floor, its drawing and print, what is drawn on it, its
items, its spaces, zones and openings corrected, the floor read again, its lifts and
stairs drawn and linked through the floors (vertical.py); who edits it (one person at a
time: the first change takes its lock, Done editing lets it go, an admin may take it
over); an area of it shared as a sample (areasample/: previewed, then downloaded)."""

from __future__ import annotations

from fastapi import Request

from .. import areasample, vertical
from ..accounts import rank
from ..review import floor_print, floor_print_png
from ..server import Download
from .calls import Body, Calls, May, Query, TheStudio, answer

calls = Calls()
F = "/api/projects/{code}/floors/{floor_id}"


@calls.get(F)
def floor(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, query: Query):
    """The floor; ``?since=<version>``: what changed after it (Review.floor)."""
    sight = may.floor(code, floor_id, "view")
    since = (query.get("since") or [None])[0]
    if since is not None and not since.isdigit():
        raise ValueError("since: a version, a whole number")
    floor = studio.review(code).floor(floor_id, since=int(since) if since is not None else None)
    if since is None and rank(sight.floor(floor_id)) >= rank("edit"):
        studio.warm(code, floor_id)  # opened by someone who may draw on it: its read ready for them
    return answer(request, {**floor, "editing": studio.editing(code, floor_id)})


@calls.get(F + "/drawing")
def drawing(code: str, floor_id: str, request: Request, may: May, studio: TheStudio):
    may.drawing(code, floor_id)
    return answer(request, studio.review(code).drawing(floor_id))


def _with_text(query: dict) -> bool:
    """Whether a print is asked for with the drawing's texts (?text=0: without them)."""
    return (query.get("text") or ["1"])[0] not in ("0", "false", "no")


@calls.get(F + "/print")
def print_info(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, query: Query):
    may.drawing(code, floor_id)
    return answer(request, floor_print(studio.review(code), floor_id, _with_text(query)))


@calls.get(F + "/print.png")
def print_png(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, query: Query):
    may.drawing(code, floor_id)
    return answer(request, floor_print_png(studio.review(code), floor_id, _with_text(query)))


def _models(studio) -> dict:
    """The models this Studio reads with, by name (an area sample says them; a vision
    helper known only by its address is not named)."""
    def name(model, attr="name"):
        try:
            return getattr(model, attr, None) if model is not None and model.available() else None
        except Exception:
            return None

    vision = name(studio.vision, "model") or ("a vision model" if name(studio.vision) else None)
    return {"language": name(studio.model), "vision": vision, "symbols": name(studio.symbols)}


@calls.post(F + "/sample/preview")
def sample_preview(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    """What an area sample of the floor would hold (its pictures, what is taken out):
    anyone who may see the floor's drawing."""
    may.drawing(code, floor_id)
    return answer(request, areasample.preview(studio.review(code), floor_id, body, models=_models(studio),
                                              catalogue=studio.catalogue()))


@calls.post(F + "/sample")
def sample(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    """An area sample of the floor, to download (<id>.spsample): anyone who may see the
    floor's drawing. Recorded in the audit log."""
    may.drawing(code, floor_id)
    made = areasample.make(studio.review(code), floor_id, body, models=_models(studio), catalogue=studio.catalogue())
    size = made.manifest["area"]
    may.audit("area sample", floor_id, project=code, sample=made.id, area=f"{size['width_m']:g} x {size['height_m']:g} m")
    return answer(request, Download(made.zipped(), made.name))


@calls.post(F + "/edits")
def edits(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")
    studio.review(code).edit(floor_id, body, by=may.user, editor=may.editor)
    return answer(request, studio.convert(code, floor_id, by=may.uid, drawn=True))


@calls.post(F + "/items")
def add_item(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")
    return answer(request, studio.review(code).add_item(floor_id, body, by=may.user, editor=may.editor))


@calls.post(F + "/sets")
def place_set(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")
    return answer(request, studio.review(code).place_set(floor_id, body, by=may.user, editor=may.editor))


@calls.post(F + "/pieces")
def place_items(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")  # (a furniture layout placed, or items pasted)
    return answer(request, studio.review(code).place_items(floor_id, body, by=may.user, editor=may.editor))


@calls.get("/api/projects/{code}/furnishings")
def furnishings(code: str, request: Request, may: May, studio: TheStudio):
    may.see(code)
    return answer(request, studio.review(code).furnishings())


@calls.post(F + "/furnishings")
def save_furnishing(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")  # (made of a room of this floor)
    return answer(request, studio.review(code).save_furnishing(floor_id, body, by=may.user, editor=may.editor))


@calls.post("/api/projects/{code}/furnishings/{furnishing_id}/delete")
def delete_furnishing(code: str, furnishing_id: str, request: Request, may: May, studio: TheStudio):
    may.furnishing(code, furnishing_id)
    return answer(request, studio.review(code).delete_furnishing(furnishing_id, by=may.user, editor=may.editor))


@calls.post("/api/projects/{code}/items")
def change_items(code: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.items(code, body)  # (each, as one alone is: edit on its floor)
    return answer(request, studio.review(code).change_items(body, by=may.user, editor=may.editor))


@calls.get("/api/projects/{code}/items/{item_id}")
def find_item(code: str, item_id: str, request: Request, may: May, studio: TheStudio):
    may.find_item(code, item_id)  # (as a person typed it: 7k2q xm9f 4dp)
    return answer(request, studio.review(code).find_item(item_id))


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
    may.object(code, object_id, body)
    return answer(request, studio.review(code).correct(object_id, body, by=may.user, editor=may.editor))


@calls.post(F + "/finishes")
def finish_rooms(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    """Many rooms' finishes at once (every room of a type on the floor, or those named): edit on the floor."""
    may.floor(code, floor_id, "edit")
    return answer(request, studio.review(code).finish_rooms(floor_id, body, by=may.user, editor=may.editor))


# ---- who edits the floor ------------------------------------------------------------------

@calls.post(F + "/release")
def release(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "view")
    return answer(request, studio.release(code, floor_id, by=may.user))


@calls.post(F + "/take-over")
def take_over(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.take_over(code, floor_id)
    room = body.get("room")
    done = studio.take_over(code, floor_id, by=may.user, editor=may.editor, room=room)
    if done["from"]:
        may.audit("room taken over" if room else "floor taken over", room or floor_id, project=code,
                  was=", ".join(p.get("username") or "?" for p in done["from"]))
    return answer(request, done)


@calls.post(F + "/delete")
def delete_floor(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    """A floor taken out of its building, its name typed to confirm (edit on the building):
    its rooms, doors and items retired. Recorded in the audit log."""
    may.delete_floor(code, floor_id)
    done = studio.delete_floor(code, floor_id, body, by=may.user)
    may.audit("floor deleted", floor_id, project=code, name=done["name"], rooms=done["rooms"], items=done["items"])
    return answer(request, done)


# ---- lifts and stairs: drawn where the drawing leaves them out, linked through the floors ----

@calls.post(F + "/vertical")
def add_vertical(code: str, floor_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.floor(code, floor_id, "edit")
    return answer(request, vertical.add(studio, code, floor_id, body, by=may.user, editor=may.editor))


@calls.get("/api/projects/{code}/objects/{object_id}/stack")
def stack(code: str, object_id: str, request: Request, may: May, studio: TheStudio):
    sight = may.stack(code, object_id)
    return answer(request, vertical.serves(studio.workspace(code), object_id, sight, studio.store.locks(code),
                                           may.user.id if may.user is not None else None))


@calls.post("/api/projects/{code}/objects/{object_id}/copy")
def copy_vertical(code: str, object_id: str, request: Request, may: May, studio: TheStudio, body: Body):
    may.copy_vertical(code, object_id, body)
    return answer(request, vertical.copy(studio, code, object_id, body, by=may.user, editor=may.editor))
