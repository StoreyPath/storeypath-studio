"""What was changed in a project, and by whom, as people read it; and the rules of
undoing and redoing one's own changes.

Every change is a row of the project's history in Studio's database (db/store.py:
``history``), written in the change's own transaction:

    seq       its number, in the order changes were made
    at        when (UTC)
    who       {id, username, name} of the person logged in; {"local": true} on this
              computer without accounts (storeypath review); "command line"
    part      what it changed: "object" (a space, zone or opening), "item", "edit"
              (what was drawn on the floor), "floor" (read again; taken over), "building",
              "project"
    kind      the change: correct, reset, delete, restore, hide, show, capacity, link,
              unlink, as found, finish (an object; finish may be of many rooms at once:
              before and after {"overrides": {id: …}}); add, move, turn, retype, values, carry, delete, restore (an
              item); draw, erase, resize (an edit); read, take over (a floor); create,
              open, import, floors, align, site, place, arrange, export, drawing…
    floors    the floors it changed (none: the building's, or the project's)
    targets   the IDs it changed
    before, after   what it changed, as it was and as it became: an object's
              correction ({"override": … or null}), an item ({"item": … or null}), the
              shapes drawn ([…] taken from and added to the floor's ``list`` of them)
    undoes, redoes  the change it undid (or the undoing it redid)

and what makes it a readable line, kept as things were then (label, what, was, now,
changes, to_name…).

Each person undoes their own changes made in review (an object, an item, what was
drawn), the latest first, and redoes what they undid until they make another change
(stacks). An undo is a change like any other, recorded as a row of its own by whoever
undid it. A change is never undone over what was changed since: the undo is refused,
naming who changed it (Conflict). A file opened in place of the project (or of a
building: ``barrier``) is a row no undo goes back over.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .errors import Conflict
from .ids import is_item_id

COMMAND_LINE = "command line"
SHOWN = 50  # rows listed, unless asked for more…
SHOWN_MAX = 500  # …up to this
UNDOABLE = ("object", "item", "edit")  # what a person changed in review, and can undo
STACK_DEPTH = 2000  # a person's latest rows read to work out what they may undo


# ---- who ---------------------------------------------------------------------------

def key(who) -> str:
    """The person a row is of, to tell one person's changes from another's."""
    if who == COMMAND_LINE or not isinstance(who, dict):
        return COMMAND_LINE
    if who.get("local"):
        return "local"
    return f"user:{who.get('id')}"


def name(who) -> str:
    if who == COMMAND_LINE or not isinstance(who, dict):
        return "Command line"
    if who.get("local"):
        return "This computer"
    return who.get("name") or who.get("username") or "Someone"


def person(who) -> dict:
    """Who a row is of, as the pages are told: their id and name."""
    if who == COMMAND_LINE or not isinstance(who, dict):
        return {"id": None, "name": "Command line"}
    if who.get("local"):
        return {"id": "local", "name": "This computer"}
    return {"id": who.get("id"), "name": name(who), "username": who.get("username")}


def when(at) -> str:
    """A time, for a message: 10:41 UTC on 8 Oct 2026."""
    try:
        t = at if isinstance(at, datetime) else datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(at)
    t = t.astimezone(timezone.utc)
    return f"{t:%H:%M} UTC on {t.day} {t:%b %Y}"


# ---- undoing ---------------------------------------------------------------------

def undoable(e: dict) -> bool:
    return e.get("part") in UNDOABLE


def stacks(entries: list[dict], me: str) -> tuple[list[dict], list[dict]]:
    """A person's changes they may undo (the last is undone first), and their undoings
    they may redo (likewise), from the rows of the project oldest first (their own,
    and the barriers). A change made clears what they may redo; a file opened in place
    of the project (or of a building: its floors) is a row no undo goes back over."""
    undo: list[dict] = []
    redo: list[dict] = []
    for e in entries:
        if e.get("barrier"):
            floors = set(e.get("floors") or [])

            def kept(x):
                return bool(floors) and not floors & set(x.get("floors") or [])

            undo, redo = [x for x in undo if kept(x)], [x for x in redo if kept(x)]
            continue
        if key(e.get("who")) != me or not undoable(e):
            continue
        if e.get("undoes"):
            undo = [x for x in undo if x["seq"] != e["undoes"]]
            redo.append(e)
        elif e.get("redoes"):
            redo = [x for x in redo if x["seq"] != e["redoes"]]
            undo.append(e)
        else:
            undo.append(e)
            redo = []
    return undo, redo


def latest(stack: list[dict], floor: str | None = None) -> dict | None:
    """The last of a stack (on ``floor``, when given)."""
    return next((e for e in reversed(stack) if floor is None or floor in (e.get("floors") or [])), None)


def touches(later: dict, entry: dict) -> bool:
    """Whether a later row changed what ``entry`` changed: one of its IDs, or one of the
    shapes it drew or took away (on the same floor, in the same list)."""
    if entry.get("part") == "edit":
        if later.get("part") != "edit" or later.get("list") != entry.get("list") \
                or not set(later.get("floors") or []) & set(entry.get("floors") or []):
            return False
        mine = (entry.get("before") or []) + (entry.get("after") or [])
        return any(x in mine for x in (later.get("before") or []) + (later.get("after") or []))
    return bool(set(later.get("targets") or []) & set(entry.get("targets") or []))


def refused(entry: dict, by: dict | None, me: str, doing: str) -> Conflict:
    """Why ``entry`` is not undone (or redone): what it changed was changed since (by
    the row ``by``, when one says so)."""
    what = subject(entry)
    if by is None:
        return Conflict(f"{what} was changed since (read again from its drawing, or a file opened in its "
                        f"place): nothing was {doing}")
    who = "you" if key(by.get("who")) == me else name(by.get("who"))
    return Conflict(f"{_first_up(what)} was changed since by {who} at {when(by.get('at'))} ({describe(by)}): "
                    f"nothing was {doing}",
                    conflict={"seq": by["seq"], "who": name(by.get("who")), "at": by.get("at"),
                              "line": describe(by), "mine": key(by.get("who")) == me})


def _first_up(text: str) -> str:
    return text[:1].upper() + text[1:]


# ---- lines to read -----------------------------------------------------------------

def short(object_id: str | None) -> str:
    """An ID as lines show it: a place's, its last part (…-0013); an item's, whole
    (7K2Q-XM9F-4DP: the tag on it, which says nothing of where it is)."""
    if not object_id:
        return ""
    return object_id if is_item_id(object_id) else f"…-{object_id.rsplit('-', 1)[-1]}"


def type_label(value) -> str:
    return str(value or "unspecified").replace("_", " ")


def _named(shown: dict | None) -> str:
    shown = shown or {}
    return " ".join(x for x in (shown.get("name"), shown.get("number")) if x)


OPENINGS = ("door", "window", "opening")


def subject(e: dict) -> str:
    """What a row changed, as a sentence names it: OFFICE 012, a space …-0013,
    Manager's desk 7K2Q-XM9F-4DP, a wall."""
    part, target = e.get("part"), (e.get("targets") or [None])[0]
    if part == "object":
        what = e.get("what") or "space"
        if e.get("label"):
            return e["label"]
        article = "an" if what[:1] in "aeiou" else "a"
        return f"{article} {what} {short(target)}".strip()
    if part == "item":
        return f"{e.get('label') or 'an item'} {short(target)}".strip()
    if part == "edit":
        return _drawn(e)
    return "it"


def _drawn(e: dict) -> str:
    what = e.get("what") or "drawing"
    if e.get("list") == "resized" or e.get("kind") == "resize":
        return f"the {what}" if what in OPENINGS else "an opening"
    return {"wall": "a wall", "divider": "a dividing line", "space": "a space", "door": "a door",
            "window": "a window", "opening": "an opening"}.get(what, f"a {what}")


def describe(e: dict) -> str:
    """A row as people read it, without who: "deleted OFFICE 012", "moved Manager's
    desk 7K2Q-XM9F-4DP", "drew a wall"; an undo, "undid: …"."""
    line = _describe(e)
    if e.get("undoes"):
        return f"undid: {line}"
    if e.get("redoes"):
        return f"redid: {line}"
    return line


def _describe(e: dict) -> str:
    part, kind = e.get("part"), e.get("kind")
    it = subject(e)
    if part == "object":
        if kind == "correct":
            was, now_ = e.get("was") or {}, e.get("now") or {}
            changed = [k for k in ("type", "name", "number") if was.get(k) != now_.get(k)]
            if not changed:
                return f"accepted {it} as it is"
            if "type" not in changed:
                return f"renamed {it} to {_named(now_) or 'no name'}"
            if changed == ["type"]:
                return f"made {it} a {type_label(now_.get('type'))}"
            return f"corrected {it}: {type_label(now_.get('type'))}, {_named(now_) or 'no name'}"
        if kind == "capacity":
            seats = (e.get("after") or {}).get("override") or {}
            return f"set {it} to seat {seats['capacity']}" if seats.get("capacity") is not None \
                else f"left what {it} seats to its desks"
        if kind == "finish":  # its floor's or walls' finish (finishes.py); many rooms at once
            return _finished(e, it)
        if kind == "link":  # a lift's or stairs' link to another floor's (stacks.py)
            to = ((e.get("after") or {}).get("override") or {}).get("stack")
            return f"linked {it} with {short(to)} on another floor" if to else f"linked {it} with another floor's"
        return {"reset": f"took the corrections off {it}", "delete": f"deleted {it}", "restore": f"restored {it}",
                "hide": f"hid {it}", "show": f"showed {it} again", "unlink": f"unlinked {it} from other floors",
                "as found": f"linked {it} through the floors as found"}.get(kind, f"changed {it}")
    if part == "item":
        verbs = {"add": "placed", "move": "moved", "turn": "turned", "values": "changed the details of",
                 "delete": "deleted", "restore": "restored"}
        changes = e.get("changes") or [kind]
        if "carry" in changes:
            return f"carried {it} to {e.get('to_name') or 'another floor'}"
        if "retype" in changes:
            return f"made {short((e.get('targets') or [None])[0])} a {e.get('new_label') or 'another type'}"
        words = [verbs[c] for c in changes if c in verbs]
        return f"{' and '.join(words) if words else 'changed'} {it}"
    if part == "edit":
        if kind == "draw":
            return f"{'added' if e.get('what') in OPENINGS else 'drew'} {it}"
        if kind == "erase":
            return f"took away {it} drawn here"
        return f"gave {it} another size"
    if part == "floor":
        if kind == "take over":
            return f"took over editing the floor from {e.get('from_name') or 'someone'}"
        held = " (held back: it would have retired most of the rooms)" if e.get("held") else ""
        return f"read the floor's drawing again{held}"
    lines = {
        "create": "created the project", "import": "brought the project in from a folder",
        "floors": f"added floors from {e.get('drawing') or 'a drawing'}", "align": "lined up the floors",
        "drawing": "added a drawing", "drawing removed": "took a drawing away",
        "export": f"exported {e.get('file') or 'a package'}", "arrange": "set the buildings side by side",
        "site": "moved a building on the site plan",
        "place": "placed a building on the map" if part == "building" else "placed the site on the map",
        "open": "opened a file into the project" if part == "building" else "opened the project from a file",
    }
    return lines.get(kind) or kind or "changed the project"


def _finished(e: dict, it: str) -> str:
    """A finish's row in words: "set the floor of OFFICE 012 to Carpet tiles, navy"."""
    from .finishes import name as finish_name

    def to(code):
        return finish_name(code) if code else "its type's"

    floor, wall = e.get("floor_to"), e.get("wall_to")
    if floor is not None and wall is not None:
        if not floor and not wall:
            return f"set the floor and walls of {it} back to its type's"
        return f"set the floor of {it} to {to(floor)} and its walls to {to(wall)}"
    if wall is not None:
        return f"set the walls of {it} to {to(wall)}" if wall else f"set the walls of {it} back to its type's"
    if floor is not None:
        return f"set the floor of {it} to {to(floor)}" if floor else f"set the floor of {it} back to its type's"
    return f"changed the finishes of {it}"


def visible_to(sight):
    """Whether a person (their Sight, accounts.py) may see a row: one that changed floors,
    with view on one of them; one of a building, view on the building as a whole; one of
    the project, view on the project. All of them, for a person who may see the whole
    project."""
    def visible(e: dict) -> bool:
        if sight is None or sight.whole:
            return True
        floors = e.get("floors") or []
        if floors:
            return any(sight.floor(f) for f in floors)
        if e.get("part") == "building":
            return all(sight.building(b) for b in (e.get("targets") or [None]))
        return False

    return visible


def shown(e: dict, me: str, undone: set) -> dict:
    """A row as the History panel lists it."""
    out = {"seq": e["seq"], "at": e.get("at"), "who": person(e.get("who")), "mine": key(e.get("who")) == me,
           "part": e.get("part"), "kind": e.get("kind"), "line": describe(e), "floors": e.get("floors") or [],
           "targets": e.get("targets") or [], "undone": e["seq"] in undone}
    for k in ("undoes", "redoes"):
        if e.get(k):
            out[k] = e[k]
    return out


def next_steps(entries: list[dict], me: str, floor: str | None = None, visible=None) -> dict:
    """What the person would undo now (on ``floor``), and what they would redo: {seq, line}
    of each, or None."""
    undo, redo = stacks(entries, me)
    visible = visible or (lambda e: True)
    out = {}
    for name_, stack in (("undo", undo), ("redo", redo)):
        e = latest(stack, floor)
        out[name_] = {"seq": e["seq"], "line": describe(e), "at": e.get("at")} if e is not None and visible(e) else None
    return out
