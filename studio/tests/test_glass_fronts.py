"""Glass fronts: glazing drawn as panes in line, end to end (window blocks in a row on a
window layer), is one front of glass. Each stretch of it from wall to wall closes like a
wall, the partitions meeting it from inside parting it, so the rooms behind it are rooms
and the courtyard outside stays outside; never across a door (the door closes its own
gap), and never from lines on a layer that is not named for windows or glass (corridors'
outlines drawn along a facade). On a made-up ring of offices round a courtyard."""

import ezdxf
import pytest
from shapely.geometry import LineString, Point, box

from storeypath.analyse import analyse
from storeypath.cad import read_drawing
from storeypath.extract import extract_floor, find_courtyards
from storeypath.profile import DoorsConfig, Profile, WallsConfig
from storeypath.samples import ORIGIN, WALL, Cell, Door, Window, write_floor_dxf
from storeypath.walls import _glass_runs

FRONT_Y = 22.0  # the glass front's line: the courtyard's north side…
FRONT = (6.0 + WALL / 2, 24.0 - WALL / 2)  # …from the west office's wall to the east office's
COURTYARD = (6.0, 8.0, 24.0, 22.0)  # (its middle at 15, 15)
PANES = 5  # window blocks along it, each a little over 3.5 m: their joints are not at the partitions
DEPTH = 0.4  # a window block's depth across the facade (its frame's faces and its glass)


def _office(x0, y0, x1, y1, n, door, **kw):
    return Cell(x0, y0, x1, y1, ["OFFICE", n], "office", door, **kw)


def ring(*, partitions: bool = True, door: Door | None = None, beside: bool = False) -> list[Cell]:
    """Offices 30 × 30 m round a courtyard 18 × 14 m (COURTYARD), drawn by their walls; the
    courtyard's south, west and east sides walls with windows. On its north side, behind a glass
    front (FRONT: its gap in the wall, the glass drawn by _glazed), three offices 6 m wide (107,
    108, 109), the partitions between them meeting the glass from inside; or (not
    ``partitions``) one open office (OPEN OFFICE 107) the front's length; between the solid walls
    of the offices either side (106, 110). ``door``: a door in the front too; ``beside``: the
    doors between the three offices just inside the glass, their swings beside it."""
    north = ([_office(6, 22, 12, 30, "107", Door(6, 26, "v", +1)),
              _office(12, 22, 18, 30, "108", Door(12, 22.9 if beside else 26, "v", +1)),
              _office(18, 22, 24, 30, "109", Door(18, 22.9 if beside else 26, "v", +1))] if partitions
             else [Cell(6, 22, 24, 30, ["OPEN OFFICE", "107"], "open_area", Door(6, 26, "v", +1))])
    north[len(north) // 2].wall_windows.append(Window((FRONT[0] + FRONT[1]) / 2, FRONT_Y, "h", FRONT[1] - FRONT[0]))
    if door is not None:
        north[len(north) // 2].doors.append(door)
    return [
        _office(0, 0, 10, 8, "101", Door(5, 0, "h", +1), wall_windows=[Window(8, 8, "h", 2.0)]),
        _office(10, 0, 20, 8, "102", Door(10, 4, "v", +1), wall_windows=[Window(15, 8, "h", 2.0)]),
        _office(20, 0, 30, 8, "103", Door(20, 4, "v", +1), wall_windows=[Window(22, 8, "h", 2.0)]),
        _office(0, 8, 6, 22, "104", Door(3, 8, "h", +1), wall_windows=[Window(6, 15, "v", 2.0)]),
        _office(24, 8, 30, 22, "105", Door(27, 8, "h", +1), wall_windows=[Window(24, 15, "v", 2.0)]),
        _office(0, 22, 6, 30, "106", Door(3, 22, "h", +1)),
        *north,
        _office(24, 22, 30, 30, "110", Door(27, 22, "h", +1)),
    ]


def _mm(x, y):
    return (ORIGIN[0] + x) * 1000, (ORIGIN[1] + y) * 1000


def _glazed(doc, layer: str = "A-WINDOW", skip: tuple[float, float] | None = None, blocks: bool = True,
            depth: float = DEPTH) -> None:
    """The glass front drawn as window blocks end to end on ``layer`` (each overlapping the next
    a little, as drafters place them): each block its frame's two faces and its glass along it,
    ``depth`` deep (its inner face where the partitions end), and its mullions across its ends;
    ``skip`` (x from, to) left free (a door's opening). Not ``blocks``: the same as loose lines.
    The glazing write_floor_dxf drew across the whole gap, as one window, is taken out."""
    msp = doc.modelspace()
    for e in list(msp.query('LINE[layer=="A-GLAZ"]')):
        (x0, y0), (x1, y1) = [(p[0] / 1000 - ORIGIN[0], p[1] / 1000 - ORIGIN[1]) for p in (e.dxf.start, e.dxf.end)]
        if abs(y0 - y1) < 1e-6 and abs(y0 - FRONT_Y) <= WALL and abs(x1 - x0) > 10:
            msp.delete_entity(e)
    if layer not in doc.layers:
        doc.layers.add(layer)
    at = FRONT_Y + WALL - depth / 2  # (the gap in the wall reaches WALL either side of its line)
    step = (FRONT[1] - FRONT[0]) / PANES
    spans = [(FRONT[0] + i * step - 0.025, FRONT[0] + (i + 1) * step + 0.025) for i in range(PANES)]
    if skip is not None:
        spans = [piece for a, b in spans for piece in ((a, min(b, skip[0])), (max(a, skip[1]), b))
                 if piece[1] - piece[0] > 0.3]
    for a, b in spans:
        length = round((b - a) * 1000)
        lines = [((0, dy), (length, dy)) for dy in (-depth / 2 * 1000, 0, depth / 2 * 1000)]
        lines += [((x, -depth / 2 * 1000), (x, depth / 2 * 1000)) for x in (0, length)]
        if blocks:
            name = f"WINDOW {length} {round(depth * 1000)}"
            if name not in doc.blocks:
                block = doc.blocks.new(name)
                for p, q in lines:
                    block.add_line(p, q)
            msp.add_blockref(name, _mm(a, at), dxfattribs={"layer": layer})
        else:
            x0, y0 = _mm(a, at)
            for (px, py), (qx, qy) in lines:
                msp.add_line((x0 + px, y0 + py), (x0 + qx, y0 + qy), dxfattribs={"layer": layer})


def _read(tmp_path, cells, edit=None, walls: WallsConfig | None = None, more_doors: str | None = None):
    """The plan drawn (``edit`` changing it), read with its layers read from what is drawn on
    them (``walls``: other settings; ``more_doors``: a layer read as doors and windows too)."""
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False, walls="lines")
    if edit is not None:
        doc = ezdxf.readfile(tmp_path / "plan.dxf")
        edit(doc)
        doc.saveas(tmp_path / "plan.dxf")
    doc = read_drawing(tmp_path / "plan.dxf")
    profile = analyse(doc, 0.001, None, None).profile
    if walls is not None or more_doors is not None:
        data = profile.model_dump()
        if walls is not None:
            data["walls"] = {**walls.model_dump(), "layers": profile.walls.layers}
        if more_doors is not None:
            data["doors"] = DoorsConfig(**{**profile.doors.model_dump(),
                                           "layers": [*profile.doors.layers, more_doors]}).model_dump()
        profile = Profile(**data)
    return extract_floor(doc, profile)


def _at(x, y) -> Point:
    return Point(ORIGIN[0] + x, ORIGIN[1] + y)


def _room(ex, number):
    """The space or zone labelled ``number``."""
    return next((u for u in ex.units() if u.number == number), None)


def _open_to_the_courtyard(ex, number) -> bool:
    """Whether the office ``number`` is no room of its own: one with the courtyard (a zone of
    it, or the space holding it), or left out with it as the outside."""
    room = _room(ex, number)
    if room is None:
        return True
    space = ex.spaces[room.space] if getattr(room, "space", -1) >= 0 else room
    return space.polygon.contains(_at(15, 15))


def _inside_the_courtyard():
    x0, y0, x1, y1 = COURTYARD
    return box(ORIGIN[0] + x0 + 1, ORIGIN[1] + y0 + 1, ORIGIN[0] + x1 - 1, ORIGIN[1] + y1 - 1)


def _courtyard(ex):
    return next(s for s in ex.spaces if s.polygon.contains(_at(15, 15)))


def _behind_the_glass(ex, numbers):
    """Each office behind the glass a room of its own, the courtyard none of them."""
    yard = _courtyard(ex)
    for n in numbers:
        room = _room(ex, n)
        assert room is not None, f"{n} is in no space"
        assert room.polygon.area < 160, (n, room.polygon.area)  # (a room, not one with the courtyard)
        assert not room.polygon.intersects(_inside_the_courtyard())
    return yard


def test_a_glass_front_of_window_blocks_closes_the_rooms_behind_it(tmp_path):
    # Window blocks end to end, between two solid walls, no block from wall to wall: each pane
    # meets only the next. The partitions meeting the glass part it into stretches, each from wall
    # to wall: the three offices are rooms, the courtyard an area of its own outside them, each
    # office's glass a window onto it; set aside (as vision does), it is open to the sky
    ex = _read(tmp_path, ring(), _glazed)
    yard = _behind_the_glass(ex, ["107", "108", "109"])
    assert yard.name is None and 200 < yard.polygon.area < 260
    i = ex.spaces.index(yard)
    for n in ("107", "108", "109"):
        k = ex.spaces.index(_room(ex, n))
        assert [d for d in ex.doors if d.source == "window" and sorted(d.connects) == sorted([i, k])], n
    yard.ignored = True
    assert find_courtyards(ex) == 1 and yard.sky


@pytest.mark.parametrize("layer", ["A-GLAZ", "A-GLASS"])
def test_the_same_drawn_as_loose_lines_on_a_window_or_glass_layer(tmp_path, layer):
    # the same glass front, its panes drawn as loose lines, thin (read as glazing, not walls), on a
    # layer named for windows or for glass
    ex = _read(tmp_path, ring(), lambda doc: _glazed(doc, layer=layer, blocks=False, depth=0.06))
    _behind_the_glass(ex, ["107", "108", "109"])


def test_a_glass_front_with_no_partition_closes_from_wall_to_wall(tmp_path):
    # one open office behind the whole front: the run of glass closes from one solid wall to the
    # other, up to max_glass_front; a shorter limit leaves it open (the office joins the courtyard)
    ex = _read(tmp_path, ring(partitions=False), _glazed)
    _behind_the_glass(ex, ["107"])
    (tmp_path / "short").mkdir()
    short = WallsConfig(max_glass_front=FRONT[1] - FRONT[0] - 1.0)
    ex = _read(tmp_path / "short", ring(partitions=False), _glazed, walls=short)
    assert _open_to_the_courtyard(ex, "107")


def test_a_glass_front_is_parted_at_a_door_and_never_closed_across_it(tmp_path):
    # a door in the middle office's glass, onto the courtyard: its leaf, shut, closes against the
    # glass either side (no wall there); the glass closes up to it on both sides, the door is the
    # way between the office and the courtyard, and no window is laid across its opening
    door = Door(15, FRONT_Y, "h", -1, 0.9)
    ex = _read(tmp_path, ring(door=door), lambda doc: _glazed(doc, skip=(14.55, 15.45)))
    yard = _behind_the_glass(ex, ["107", "108", "109"])
    i, k = ex.spaces.index(yard), ex.spaces.index(_room(ex, "108"))
    opening = LineString([_at(14.6, FRONT_Y), _at(15.4, FRONT_Y)]).buffer(0.05)
    ways = [d for d in ex.doors if sorted(d.connects) == sorted([i, k])]
    assert [d.source for d in ways if d.footprint.intersects(opening)] == ["door"], [(d.source, d.point) for d in ways]
    assert len([d for d in ways if d.source == "window"]) == 2  # the glass either side
    assert not any(d.source == "window" and d.span is not None
                   and d.span.intersection(opening).length > 0.2 for d in ex.doors)


def test_doors_beside_the_glass_leave_it_closed(tmp_path):
    # the doors between the offices just inside the glass, their swings beside it: the glass
    # still closes each office, and each door joins the two offices it stands between
    ex = _read(tmp_path, ring(beside=True), _glazed)
    _behind_the_glass(ex, ["107", "108", "109"])
    rooms = [ex.spaces.index(_room(ex, n)) for n in ("107", "108", "109")]
    for a, b in zip(rooms, rooms[1:]):
        assert [d for d in ex.doors if d.source == "door" and sorted(d.connects) == sorted([a, b])]


def test_corridor_outlines_along_a_facade_close_nothing(tmp_path):
    # Not glass: a layer of corridors' outlines read as doors and windows (its lines lie in line
    # with the walls), drawn along the north offices' facade, which is open (no glass drawn), in
    # pieces end to end as a glass front would be. Its name says nothing of windows or glass: the
    # offices stay open to the courtyard
    def outlines(doc):
        _glazed(doc, layer="A-CORRIDOR", blocks=False)

    ex = _read(tmp_path, ring(), outlines, more_doors="A\\-CORRIDOR")
    assert all(_open_to_the_courtyard(ex, n) for n in ("107", "108", "109"))


@pytest.mark.parametrize("second, joined", [
    (LineString([(2.95, 0.0), (6.0, 0.0)]), True),  # overlapping
    (LineString([(3.0, 0.0), (6.0, 0.0)]), True),  # touching
    (LineString([(3.08, 0.0), (6.0, 0.0)]), True),  # a mullion apart
    (LineString([(3.3, 0.0), (6.0, 0.0)]), False),  # a gap: two fronts
    (LineString([(3.0, 0.05), (6.0, 0.05)]), False),  # beside it, not in line (the frame's other face)
    (LineString([(3.0, 0.0), (6.0, 0.5)]), False),  # turned
])
def test_panes_in_line_end_to_end_are_one_run(second, joined):
    runs = _glass_runs([LineString([(0.0, 0.0), (3.0, 0.0)]), second])
    assert (len(runs) == 1) == joined
    if joined:
        line, panes = runs[0]
        assert abs(line.length - 6.0) < 1e-6 and len(panes) == 2
