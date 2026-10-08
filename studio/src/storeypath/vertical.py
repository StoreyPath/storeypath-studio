"""Lifts and stairs a drawing leaves out, drawn in review and linked through the floors
they serve (stacks.py): Studio's side of Review's Stairs and Lift tools.

- add(): a lift, stairs or escalator drawn on a floor: a space drawn on it (the floor's
  edits, as Review's space tool draws one: cut out of the room it is drawn in), the
  floor read again, and the space found there given its type.
- copy(): one drawn again where it is (floors share their building's frame) on other
  floors of its building: each typed, and linked to it by hand (Override.stack).
- serves(): the floors of its building a lift or stairs is on, as it is linked to them:
  for Review's "Floors it serves".

Each drawing is an edit of its floor, kept through every reading of the drawing; the
type and the link are corrections of the space, kept with its ID.
"""

from __future__ import annotations

from shapely.geometry import Polygon, shape

from . import errors
from .accounts import rank
from .geometry import as_polygons, iou
from .review import NotFound, _ring
from .stacks import NOT_LINKED, STACK_TYPES, building_floors, floor_of, stacks

TOOLS = ("stairs", "elevator", "escalator")  # what Review's tools draw
FOUND_IOU = 0.5  # a space read where one was drawn, overlapping it this much, is the one drawn


def _uid(by) -> str | None:
    from .server import _uid as uid

    return uid(by)


def _vertical(ws, object_id: str):
    r = ws.objects.get(object_id)
    if r is None or r.status != "active" or r.kind != "space":
        raise NotFound(f"no active space {object_id}")
    if ws.effective(r)["type"] not in STACK_TYPES:
        raise ValueError(f"{object_id} is not a lift, stairs, escalator or ramp")
    return r


def _drawable(ws, floor_id: str) -> None:
    try:
        f = ws.floor(floor_id)
    except (KeyError, ValueError):
        raise NotFound(f"no floor {floor_id}") from None
    if f.source is None:
        raise ValueError(f"{f.name} has no drawing to draw on: add its drawing first")


def found(ws, floor_id: str, ring) -> str | None:
    """The space read on a floor where one was drawn (its ring): the one overlapping it
    most, if it overlaps it at least FOUND_IOU."""
    drawn = Polygon(ring).buffer(0)
    best = None
    for r in ws.floor_objects(floor_id):
        if r.kind != "space" or not r.geometry:
            continue
        overlap = iou(drawn, shape(r.geometry))
        if overlap >= FOUND_IOU and (best is None or (overlap, r.id) > best):
            best = (overlap, r.id)
    return best[1] if best else None


def _as(by, editor) -> dict:
    """Who a change is of, and (with floor locks) the page making it: what Review's own
    changes are given."""
    return {"by": by, **({"editor": editor} if editor is not None else {})}


def _typed(review, space_id: str, kind: str, link: str | None = None, by=None, editor=None) -> None:
    """A space given its type (its name and number as corrected, kept), and linked."""
    ws = review.workspace()
    current = ws.overrides.get(space_id)
    keep = current.model_dump(exclude_none=True, include={"name", "number"}) if current else {}
    body = {"correction": {**keep, "type": kind}}
    if link is not None:
        body["stack"] = link
    review.correct(space_id, body, **_as(by, editor))


def add(studio, code: str, floor_id: str, body: dict, by=None, editor=None):
    """A lift, stairs or escalator drawn on a floor (``{"type", "space": [[x, y], …]}``,
    local metres): drawn now (an edit of the floor, as Review's space tool makes), then a
    job reads the floor again and types the space found there. The job's result:
    ``{"space": its ID}``. ``editor``: the page making it, where floors are locked."""
    kind = body.get("type")
    if kind not in TOOLS:
        raise ValueError(f"type is {', '.join(TOOLS)}")
    ring = _ring(body.get("space"))
    review = studio.review(code)
    _drawable(review.workspace(), floor_id)
    review.edit(floor_id, {"add": {"space": ring}}, **_as(by, editor))
    what = {"elevator": "lift"}.get(kind, kind)

    def run(job):
        result = studio._convert(code, [floor_id], job, by=_uid(by))
        space = found(studio.workspace(code), floor_id, ring)
        if space is None:
            raise ValueError(f"the {what} drawn was not found when the floor was read again: draw it on the floor")
        _typed(review, space, kind, by=by, editor=editor)
        job.say(f"{space}: a {what}")
        return {"space": space, **result}

    return studio.jobs.submit(f"Drawing a {what}", run, project=code, user=_uid(by), scope=("floor", floor_id))


# Another person editing a floor (with floor locks: errors.Locked) leaves that floor out
LOCKED = getattr(errors, "Locked", ())


def copy(studio, code: str, object_id: str, body: dict, by=None, editor=None):
    """A lift, stairs or escalator drawn again, where it is, on other floors of its
    building (``{"floors": [floor IDs]}``), one floor at a time: drawn on each now, then
    a job reads them again and types the space found on each as it is, linked to it. A
    floor someone else is editing is left out, and said so (``refused``). The job's
    result: ``{"spaces": {floor: space}, "missed": [floors], "refused": [{floor,
    holder}]}``."""
    floors = body.get("floors")
    review = studio.review(code)
    ws = review.workspace()
    r = _vertical(ws, object_id)
    building = floor_of(floor_of(object_id))
    if not isinstance(floors, list) or not floors or not all(isinstance(f, str) for f in floors):
        raise ValueError("floors: choose the floors to add it on")
    floors = list(dict.fromkeys(floors))
    for f in floors:
        if f == floor_of(object_id) or floor_of(f) != building:
            raise ValueError(f"{f} is not another floor of its building")
        _drawable(ws, f)
    kind = ws.effective(r)["type"]
    part = max(as_polygons(shape(r.geometry)), key=lambda p: p.area)
    ring = [[round(x, 4), round(y, 4)] for x, y in list(part.exterior.coords)[:-1]]
    drawn, refused = [], []
    for f in floors:
        try:
            review.edit(f, {"add": {"space": ring}}, **_as(by, editor))
        except LOCKED as e:
            refused.append({"floor": f, "holder": getattr(e, "holder", None), "error": str(e)})
            continue
        drawn.append(f)
    if not drawn:
        raise refused_error(refused)

    def run(job):
        result = studio._convert(code, drawn, job, by=_uid(by))
        ws = studio.workspace(code)
        spaces, missed = {}, []
        for f in drawn:
            space = found(ws, f, ring)
            if space is None:
                missed.append(f)
                job.say(f"{f}: not found when the floor was read again")
                continue
            try:
                _typed(review, space, kind, link=object_id if space != object_id else None, by=by, editor=editor)
            except LOCKED as e:
                refused.append({"floor": f, "holder": getattr(e, "holder", None), "error": str(e)})
                continue
            spaces[f] = space
            job.say(f"{f}: {space}, linked")
        for x in refused:
            job.say(f"{x['floor']}: not added: {x['error']}")
        if not spaces:
            raise ValueError("it was not added on any floor: " + "; ".join(
                [f"{f}: not found when read again" for f in missed] + [x["error"] for x in refused]))
        return {"spaces": spaces, "missed": missed, "refused": refused, **result}

    return studio.jobs.submit("Adding it on floors", run, project=code, user=_uid(by), scope=("project", None))


def refused_error(refused: list[dict]):
    """No floor could be drawn on: the first refusal, as it was."""
    return LOCKED(refused[0]["error"], refused[0]["holder"]) if LOCKED else ValueError(refused[0]["error"])


def _label(ws, r) -> str:
    eff = ws.effective(r)
    words = {"elevator": "lift"}.get(eff["type"], eff["type"].replace("_", " "))
    said = " ".join(x for x in (eff["name"], eff["number"]) if x)
    return f"{said} ({words})" if said else f"{words[:1].upper()}{words[1:]} {r.id.rsplit('-', 1)[1]}"


def serves(ws, object_id: str, sight=None, locks=None, me: str | None = None) -> dict:
    """The floors of its building a lift, stairs, escalator or ramp is on, each with its
    spaces there and how they are linked (``how``: "code", "overlap" (found so),
    "person" (linked by hand), "alone"; ``setting``: the person's, null for as found);
    the lifts and stairs of other floors it may be linked with; only the floors
    ``sight`` lets its person see. ``locks``: who is editing which floor (store.locks);
    a floor someone other than ``me`` (a user's ID) edits says who (``locked``)."""
    r = _vertical(ws, object_id)
    building = floor_of(floor_of(object_id))
    seen = (lambda f: True) if sight is None else (lambda f: bool(sight.floor(f)))
    may_edit = (lambda f: True) if sight is None else (lambda f: rank(sight.floor(f)) >= rank("edit"))
    found_stacks = stacks(ws, building)
    mine = next((s for s in found_stacks if object_id in s.members), None)
    members = set(mine.members) if mine else {object_id}
    candidates = []
    floors = []
    for fid in building_floors(ws, building):
        if not seen(fid):
            continue
        f = ws.floor(fid)
        here = []
        for s in found_stacks:
            for m in s.members:
                if floor_of(m) != fid:
                    continue
                o = ws.overrides.get(m)
                entry = {"id": m, "label": _label(ws, ws.objects[m]), "type": ws.effective(ws.objects[m])["type"],
                         "stack": s.key, "how": s.how.get(m, "alone"), "setting": o.stack if o else None}
                if m in members:
                    here.append(entry)
                elif fid != floor_of(object_id):
                    candidates.append({**entry, "floor_id": fid, "floor": f.name})
        lock = (locks or {}).get(fid)
        other = lock and lock["who"]["id"] != me and {k: v for k, v in lock.items() if k != "session"}
        floors.append({"id": fid, "name": f.name, "ordinal": f.ordinal, "this": fid == floor_of(object_id),
                       "spaces": here, "drawing": f.source is not None, "edit": may_edit(fid),
                       "locked": other or None})
    o = ws.overrides.get(object_id)
    return {"id": object_id, "type": ws.effective(r)["type"], "label": _label(ws, r),
            "stack": mine.key if mine else None, "setting": o.stack if o else None,
            "how": mine.how.get(object_id, "alone") if mine else "alone",
            "not_linked": NOT_LINKED, "floors": floors, "candidates": candidates}
