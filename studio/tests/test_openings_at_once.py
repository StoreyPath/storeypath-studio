"""Doors, windows, openings and gates changed at once, with no read (openings.py): after
every change the floor is what reading it again with the same edits makes it.

Each made-up floor (and layout) is kept twice, identical but for its project's code: on one
every change is made at once; on the other the same change is made with nothing done at
once and the floor read again after it, as Studio did before. After each change both hold
the same objects (retired ones too), IDs, the spaces each opening joins, spans, leaves,
sizes, the same walls with their gaps, the same building's next object number and the same
walking network (and a layout's land's network). The changes: doors, windows and openings
added and taken away, a door added where the drawing has no wall, moves across another
opening's gap, a moved door given another width, types changed back and forth, doors
turned, sizes given; undo and redo, and two people editing different rooms at once."""

import json
import math
import time

import pytest

from people import Team
from storeypath import accounts as acc
from storeypath import convert, openings
from storeypath.accounts import Scope
from storeypath.convert import convert_floor
from storeypath.layout import convert_layout
from storeypath.navigation import build_network, build_site_network
from storeypath.review import NotFound, Review
from storeypath.samples import (build_demo, office_floor, sample_site_plan, site_plan_region, write_floor_dxf,
                                write_site_plan_dxf)
from storeypath.workspace import Placement, SourceDrawing, Workspace

ORIGIN = (125.0, 48.0)  # where the made-up floors are drawn, metres


def P(x: float, y: float) -> list[float]:
    """A point of a made-up office floor (office_floor's metres) on the floor, local metres."""
    return [round(ORIGIN[0] + x, 4), round(ORIGIN[1] + y, 4)]


def _relative(value, code: str):
    """``value`` (JSON) with the project's code taken out of every ID in it."""
    return json.loads(json.dumps(value).replace(f"{code}-", "P-"))


def _held(ws: Workspace, floor_id: str) -> dict:
    """What a floor holds, as compared: every object of it (retired ones too) as kept, but
    when it was made and retired; its walls, edits, its building's next object number; its
    building's walking network (a layout's: its land's)."""
    f = ws.floor(floor_id)
    building = floor_id.rsplit("-", 1)[0]
    objects = {r.id: r.model_dump(mode="json", exclude={"created_at", "retired_at"})
               for r in ws.floor_objects(floor_id, include_retired=True)}
    network = build_site_network(ws, floor_id) if f.layout else build_network(ws, building)
    return _relative({"objects": objects, "walls": f.walls, "edits": f.edits.model_dump(mode="json"),
                      "next": ws.building(building).next_object_seq, "network": network}, ws.id)


def _same(a: dict, b: dict) -> None:
    """Two floors' holdings the same: what differs, said by object."""
    for key in ("edits", "next", "walls"):
        assert a[key] == b[key], key
    assert set(a["objects"]) == set(b["objects"]), set(a["objects"]) ^ set(b["objects"])
    for i in a["objects"]:
        assert a["objects"][i] == b["objects"][i], i
    assert a["network"] == b["network"]


@pytest.fixture
def read_after(monkeypatch):
    """Projects (by code) whose changes are made with nothing done at once: the floor is
    to be read again after them, as before."""
    codes: set[str] = set()
    real = openings.at_once

    def at_once(ws, floor_id, folder=None):
        return "read again, for the comparison" if ws.id in codes else real(ws, floor_id, folder)

    monkeypatch.setattr(openings, "at_once", at_once)
    return codes


# ---- made-up floors and layouts, each change made both ways -------------------------------------

def _floor_project(tmp_path, name: str, cells, **drawn):
    """A project of one made-up floor, read (its read kept, as Studio keeps it)."""
    folder = tmp_path / name
    folder.mkdir()
    write_floor_dxf(folder / "plan.dxf", cells, **drawn)
    ws = Workspace.new(name)
    b_id = ws.add_building(ws.add_location("SITE", "Site"), "B", "Building")
    ws.building(b_id).placement = Placement(lon=10.0, lat=50.0, x=125, y=48, bearing=0)
    f_id = ws.add_floor(b_id, 0, source=SourceDrawing(path="plan.dxf"))
    convert_floor(ws, f_id, folder)
    path = folder / "project.spproj"
    ws.save(path)
    return Review(path), path, f_id


def _layout_project(tmp_path, name: str):
    folder = tmp_path / name
    folder.mkdir()
    plan = sample_site_plan()
    write_site_plan_dxf(folder / "site.dxf", plan)
    ws = Workspace.new(name)
    loc = ws.add_location("SITE", "Plot")
    layout = ws.add_layout(loc, SourceDrawing(path="site.dxf", units="m", region=site_plan_region(plan)))
    convert_layout(ws, layout, folder)
    path = folder / "project.spproj"
    ws.save(path)
    return Review(path), path, layout


def _read_again(path, floor_id: str) -> None:
    ws = Workspace.load(path)
    if ws.floor(floor_id).layout:
        convert_layout(ws, floor_id, path.parent)
    else:
        convert_floor(ws, floor_id, path.parent, drawn=True)
    ws.save(path)


class Both:
    """A made-up project twice: ``now`` changed at once, ``read`` read again after each change."""

    def __init__(self, now, read, codes: set):
        (self.r, self.path, self.f), (self.rb, self.path_b, self.fb) = now, read
        codes.add(Workspace.load(self.path_b).id)
        self.changes = 0

    @property
    def ws(self) -> Workspace:
        return Workspace.load(self.path)

    def __call__(self, body: dict) -> None:
        """One change, made both ways: refused both ways alike, or the floors the same after it."""
        errors = []
        for review, path, f, at_once in ((self.r, self.path, self.f, True), (self.rb, self.path_b, self.fb, False)):
            try:
                said = review.edit(f, body)
            except (ValueError, NotFound) as e:
                errors.append(type(e).__name__)
                continue
            if at_once:
                assert said == {"at_once": True}, (body, said)
            else:
                assert said.get("read") == "read again, for the comparison", said
                _read_again(path, f)
        assert errors in ([], ["ValueError", "ValueError"], ["NotFound", "NotFound"]), (body, errors)
        self.changes += not errors
        _same(_held(Workspace.load(self.path), self.f), _held(Workspace.load(self.path_b), self.fb))

    def opening(self, at, kind=None):
        """The floor's opening nearest ``at`` (as the page has it: its middle)."""
        doors = [d for d in self.r.floor(self.f)["doors"] if kind is None or d["type"] == kind]
        return min(doors, key=lambda d: math.dist(d["middle"], at))


@pytest.mark.parametrize("level, drawn", [(0, {"area_outlines": True, "walls": "polylines"}),
                                          (0, {"area_outlines": False, "walls": "lines"}),
                                          (1, {"area_outlines": True, "walls": "lines"})],
                         ids=["outlines", "walls", "upper floor"])
def test_every_change_of_openings_at_once_is_what_reading_the_floor_again_gives(tmp_path, read_after, level, drawn):
    both = Both(_floor_project(tmp_path, "now", office_floor(level), **drawn),
                _floor_project(tmp_path, "read", office_floor(level), **drawn), read_after)
    hinge, other = P(12, 3.55), P(12, 4.45)
    # a door between two offices, hung on its south jamb opening east; turned onto the other jamb
    both({"add": {"opening": {"type": "door", "span": [hinge, other], "swing": [hinge, P(12.9, 3.55)]}}})
    both({"resize": {"at": P(12, 4.0), "swing": [other, P(11.1, 4.45)]}})
    # a way through between two north rooms, and a window in the outer wall, given its sill and height
    way = [P(20, 14.5), P(20, 15.5)]
    both({"add": {"opening": {"type": "opening", "span": way}}})
    both({"add": {"opening": {"type": "window", "span": [P(31.0, 0), P(31.8, 0)]}}})
    both({"resize": {"at": P(31.4, 0), "sill": 0.9, "height": 1.5}})
    # a door beside an office's door, moved along the corridor wall across that door's gap, and back
    both({"add": {"opening": {"type": "door", "span": [P(18.65, 8.5), P(19.55, 8.5)]}}})
    both({"move": {"at": P(19.1, 8.5), "to": P(16.9, 8.5)}})
    both({"move": {"at": P(16.9, 8.5), "to": P(19.1, 8.5)}})
    # a door where the drawing has no wall: in the middle of a room
    both({"add": {"opening": {"type": "door", "span": [P(41.0, 16.0), P(41.9, 16.0)]}}})
    # the drawing's door of an office: moved, given another width there, turned, moved back, as drawn
    door = both.opening(P(34, 8.5), "door")
    both({"move": {"at": door["middle"], "to": [door["middle"][0] + 0.3, door["middle"][1]]}})
    moved = both.opening(P(34.3, 8.5), "door")
    both({"resize": {"at": moved["middle"], "width": 1.1}})
    wide = both.opening(P(34.3, 8.5), "door")
    (x0, y0), (x1, y1) = wide["span"]
    both({"resize": {"at": wide["middle"], "swing": [[x1, y1], [x1, y1 - 1.1]]}})
    both({"move": {"at": wide["middle"], "to": P(34, 8.5)}})
    both({"resize": {"at": both.opening(P(34, 8.5), "door")["middle"], "width": None}})
    # a window of the drawing made a door (a way in from outside), as read, an opening, a window, as read
    window = both.opening(P(10, 0), "window")
    for kind in ("door", None, "opening", "window", None, "door"):
        both({"resize": {"at": window["middle"], "type": kind}})
    # what was drawn taken away
    both({"remove": {"kind": "opening", "at": P(20, 15), "shape": way}})
    both({"remove": {"kind": "opening", "at": P(12, 4.0), "shape": [hinge, other]}})
    # refused alike: off its wall, too wide, nothing there
    both({"move": {"at": P(41.45, 16.0), "to": P(60, 16.0)}})
    both({"add": {"opening": {"type": "door", "span": [P(10, 8.5), P(17, 8.5)]}}})
    both({"resize": {"at": P(-30, -30), "width": 1.0}})
    assert both.changes == 22
    assert any(r.status == "retired" for r in both.ws.floor_objects(both.f, include_retired=True)
               if r.kind == "opening")  # (what was taken away: retired, as a read retires it)


def test_every_change_of_a_layouts_gates_at_once_is_what_reading_its_site_plan_again_gives(tmp_path, read_after):
    both = Both(_layout_project(tmp_path, "now"), _layout_project(tmp_path, "read"), read_after)
    gates = both.r.floor(both.f)["doors"]
    vehicle = max(gates, key=lambda d: d["width"])
    north = [[0.0, 17.9], [3.0, 17.9]]
    both({"add": {"opening": {"type": "gate", "span": north}}})  # a gate the drawing leaves out
    both({"resize": {"at": vehicle["middle"], "width": 4.0}})  # the vehicle gate narrowed
    both({"move": {"at": [1.5, 17.9], "to": [3.5, 17.9]}})  # the gate drawn, along its wall
    both({"resize": {"at": [3.5, 17.9], "width": 2.0}})
    both({"resize": {"at": vehicle["middle"], "width": None}})  # as drawn
    both({"remove": {"kind": "opening", "at": [3.5, 17.9]}})
    assert both.changes == 6


def test_a_door_to_the_outside_added_at_once_is_a_way_in_from_the_site_as_reading_again_makes_it(tmp_path, read_after):
    # A house on a made-up plot with no door to the outside: one added in its hall's outer wall
    # (at once) is its entrance from the land, the site's network as the house read again gives.
    from test_site_routes import SHUT, _site

    sides, layouts = [], []
    for name in ("now", "read"):
        folder = tmp_path / name
        folder.mkdir()
        ws, loc, layout, made = _site(folder, SHUT, kiosk=False)
        path = folder / "project.spproj"
        ws.save(path)
        house = next(f for *_, f in ws.iter_floors() if f.startswith(made["HOUSE"] + "-"))
        sides.append((Review(path), path, house))
        layouts.append(layout)
    both = Both(*sides, read_after)

    def site(path, layout):
        ws = Workspace.load(path)
        return _relative(build_site_network(ws, layout), ws.id)

    entrances = lambda net: [n for n in net["nodes"] if n["kind"] == "entrance"]  # noqa: E731
    houses = lambda net: {n.get("building_id") for n in entrances(net)}  # noqa: E731
    assert not any(b.endswith("-HOUSE") for b in houses(site(both.path, layouts[0])))
    both({"add": {"opening": {"type": "door", "span": [P(8.05, 0), P(8.95, 0)]}}})
    now, read = site(both.path, layouts[0]), site(both.path_b, layouts[1])
    assert now == read and any(b.endswith("-HOUSE") for b in houses(now))  # a way in, as reading again gives
    both({"remove": {"kind": "opening", "at": P(8.5, 0)}})
    now, read = site(both.path, layouts[0]), site(both.path_b, layouts[1])
    assert now == read and not any(b.endswith("-HOUSE") for b in houses(now))


def test_a_read_that_ends_after_a_change_of_openings_at_once_has_it(tmp_path):
    # The floor read again (Read again) while a door is added at once: the read, applied
    # after, has the door, as the floor's edits are when it is applied (convert.apply_read).
    r, path, f = _floor_project(tmp_path, "floor", office_floor(0))
    read = convert.read_floor(Workspace.load(path), f, path.parent)  # (begun before the door was added)
    assert r.edit(f, {"add": {"opening": {"type": "door", "span": [P(12, 3.55), P(12, 4.45)]}}}) == {"at_once": True}
    at_once = _held(Workspace.load(path), f)
    now = Workspace.load(path)
    convert.apply_read(now, read)
    drawn = [o for o in now.floor_objects(f) if o.kind == "opening" and o.type_source == "drawn door"]
    assert len(drawn) == 1 and len(drawn[0].connects) == 2
    _same(_held(now, f), at_once)


def test_what_cannot_be_done_at_once_says_why_and_the_floor_is_read_again(tmp_path):
    r, path, f = _floor_project(tmp_path, "floor", office_floor(0))
    door = {"add": {"opening": {"type": "door", "span": [P(12, 3.55), P(12, 4.45)]}}}
    assert r.edit(f, {"add": {"wall": [P(40, 11.6), P(40, 20)]}}) == {"read": None}  # (a wall: read, as ever)
    said = r.edit(f, door)  # walls drawn since the floor's read: its rooms may be others
    assert said == {"read": "walls, dividing lines or spaces were drawn on it since it was last read"}
    _read_again(path, f)
    assert r.edit(f, {"remove": {"kind": "opening", "at": P(12, 4.0)}}) == {"at_once": True}
    openings.forget(f)  # (Studio started again, the read kept nowhere)
    assert r.edit(f, door) == {"read": openings.NO_BASE}
    _read_again(path, f)
    ws = Workspace.load(path)
    ws.floor(f).source.sha256 = "another drawing"  # (as a floor given another drawing, not read yet)
    ws.save(path)
    assert r.edit(f, {"resize": {"at": P(12, 4.0), "width": 1.0}}) == \
        {"read": "its drawing, or the part of it read, changed since it was last read"}
    _read_again(path, f)
    assert r.edit(f, {"resize": {"at": P(12, 4.0), "width": 1.1}}) == {"at_once": True}


# ---- in Studio: two people, undo and redo, the pages told ---------------------------------------

@pytest.fixture
def two(tmp_path, monkeypatch, read_after):
    """Studio with the demo campus twice (team.code: changed at once; team.other: read again
    after each change), each person's access the same on both."""
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(openings, "BASES_KEPT", 64)
    other_path, _ = build_demo(tmp_path / "data" / "other")
    t = Team(tmp_path / "data")
    t.other = Workspace.load(other_path).id
    read_after.add(t.other)
    boss = t.users["boss"].id
    t.accounts.set_owner(t.other, t.users["khalid"].id)
    t.accounts.set_grant(t.other, t.users["sara"].id, Scope(kind="project"), "edit", boss)
    yield t
    t.close()


def _both(team, who: str, path: str, body: dict | None = None):
    """A call made on both projects (its floor's ID, if any, of each): the floors the same
    after it (read again on the other, as before)."""
    out = []
    for code, at_once in ((team.code, True), (team.other, False)):
        status, said = team(who, "POST", f"/api/projects/{code}/{path.replace('{code}', code)}",
                            json.loads(json.dumps(body or {}).replace("{code}", code)))
        assert status == 200, said
        job = said.get("job", said) if path in ("undo", "redo") else said
        if at_once:
            assert (said.get("job") is None) if path in ("undo", "redo") else said["at_once"] is True
        elif job is not None:
            team.finished(who, job)
        out.append(said)
    a, b = team.studio.workspace(team.code), team.studio.workspace(team.other)
    for floor in (team.hq0, team.an0):
        _same(_held(a, floor), _held(b, floor.replace(team.code, team.other)))
    return out[0]


def test_two_people_undo_and_redo_at_once_as_reading_again_gives(two):
    team = two
    hq0, an0 = "floors/{code}-DEMO-HQ-F00/edits", "floors/{code}-DEMO-ANNEX-F00/edits"
    # two people on HQ's ground floor at once, in different rooms: khalid between two offices, sara
    # between two rooms across the corridor; and sara on the Annex's
    _both(team, "khalid", hq0, {"add": {"opening": {"type": "door", "span": [P(12, 3.55), P(12, 4.45)],
                                                     "swing": [P(12, 3.55), P(12.9, 3.55)]}}})
    _both(team, "sara", hq0, {"add": {"opening": {"type": "door", "span": [P(20, 14.5), P(20, 15.4)]}}})
    _both(team, "khalid", hq0, {"move": {"at": P(12, 4.0), "to": P(12, 5.0)}})
    _both(team, "sara", hq0, {"resize": {"at": P(20, 14.95), "swing": [P(20, 14.5), P(19.1, 14.5)]}})
    window = min((d for d in team("sara", "GET", f"floors/{team.an0}")[1]["doors"] if d["type"] == "window"),
                 key=lambda d: math.dist(d["middle"], P(10, 0)))
    _both(team, "sara", an0, {"resize": {"at": window["middle"], "type": "door"}})
    # each undoes their own, the latest first; redone
    _both(team, "khalid", "undo", {"floor": "{code}-DEMO-HQ-F00"})  # the move
    _both(team, "sara", "undo", {"floor": "{code}-DEMO-ANNEX-F00"})  # the window made a door
    _both(team, "sara", "undo", {"floor": "{code}-DEMO-HQ-F00"})  # the turn
    _both(team, "khalid", "redo", {"floor": "{code}-DEMO-HQ-F00"})
    _both(team, "sara", "redo", {"floor": "{code}-DEMO-HQ-F00"})
    _both(team, "sara", "redo", {"floor": "{code}-DEMO-ANNEX-F00"})
    _both(team, "khalid", hq0, {"remove": {"kind": "opening", "at": P(12, 5.0)}})
    _both(team, "khalid", "undo", {"floor": "{code}-DEMO-HQ-F00"})  # the door taken away: back, its ID kept
    lines = lambda code: [e["line"] for e in team("khalid", "GET", f"/api/projects/{code}/history?n=30")[1]["entries"]  # noqa: E731
                          if e["part"] == "edit"]
    assert lines(team.code) == lines(team.other) and lines(team.code)[0] == "undid: took away a door drawn here"


def test_a_change_at_once_takes_the_rooms_on_both_sides_and_the_pages_are_sent_what_changed(two):
    team = two
    f = team.hq0
    whole = team("khalid", "GET", f"floors/{f}")[1]
    v = whole["version"]
    status, said = team("khalid", "POST", f"floors/{f}/edits",
                        {"add": {"opening": {"type": "door", "span": [P(12, 3.55), P(12, 4.45)]}}})
    assert status == 200 and said["at_once"] is True and said["floor"] == f and said["version"] > v
    delta = team("khalid", "GET", f"floors/{f}?since={v}")[1]
    door = next(d for d in delta["doors"] if d["drawn"])
    assert len(delta["doors"]) == 1 and "doors_all" not in delta  # what changed alone, not all the floor's openings
    assert "walls" in delta and len(delta["edits"]["openings"]) == 1 and delta["spaces"] == []
    rooms = set(door["connects"])
    assert len(rooms) == 2
    editing = team("sara", "GET", f"floors/{f}")[1]["editing"]
    held = {room for e in editing["editors"] for room in e["rooms"]}
    assert rooms <= held  # khalid holds the rooms on both sides of it
    # sara may not move it (its rooms are his), nor put a door into one of them; elsewhere she may
    status, refused = team("sara", "POST", f"floors/{f}/edits", {"move": {"at": door["middle"], "to": P(12, 5.0)}})
    assert status == 423 and refused["locked"]["who"]["name"] == "Khalid Engineer"
    status, _ = team("sara", "POST", f"floors/{f}/edits", {"add": {"opening": {"type": "opening", "span": [P(20, 14.5), P(20, 15.5)]}}})
    assert status == 200


def test_studio_started_again_changes_openings_at_once_from_the_read_it_kept(two):
    team = two
    f = team.an0
    # a wall drawn: the floor read again, and what review's openings are put in on kept in Studio's cache
    status, job = team("bob", "POST", f"floors/{f}/edits", {"add": {"wall": [P(40, 11.6), P(40, 20)]}})
    assert status == 200 and "id" in job
    team.finished("bob", job)
    kept = team.studio.cache / "reads" / f"{f}.openings"
    for _ in range(100):  # (written in the background)
        if kept.is_file():
            break
        time.sleep(0.05)
    assert kept.is_file()
    openings.forget()  # (Studio started again: nothing in memory)
    status, said = team("bob", "POST", f"floors/{f}/edits", {"add": {"opening": {"type": "door", "span": [P(12, 3.55), P(12, 4.45)]}}})
    assert status == 200 and said["at_once"] is True
    # with nothing kept anywhere, the floor is read again, and the job says why
    openings.forget()
    (team.studio.cache / "reads" / f"{f}.openings").unlink()
    status, job = team("bob", "POST", f"floors/{f}/edits", {"remove": {"kind": "opening", "at": P(12, 4.0)}})
    assert status == 200 and "id" in job
    done = team.finished("bob", job)
    assert done["log"][0] == f"read again, not changed at once: {openings.NO_BASE}"
    assert not any(o.type_source == "drawn door" for o in team.studio.workspace(team.code).floor_objects(f))
    # a floor read by an older Studio: opened by one who may edit it, it is read as it is in the
    # background (applied to a copy alone), and its doors are added at once after
    openings.forget()
    kept.unlink()
    team.studio._warm = True
    before = _held(team.studio.workspace(team.code), f)
    assert team("bob", "GET", f"floors/{f}")[1]["openings_at_once"] is False
    for _ in range(600):
        if team.studio.openings_at_once(team.code, f):
            break
        time.sleep(0.1)
    assert team("bob", "GET", f"floors/{f}")[1]["openings_at_once"] is True
    _same(_held(team.studio.workspace(team.code), f), before)  # (the project itself unchanged)
    status, said = team("bob", "POST", f"floors/{f}/edits", {"add": {"opening": {"type": "door", "span": [P(12, 3.55), P(12, 4.45)]}}})
    assert status == 200 and said["at_once"] is True
