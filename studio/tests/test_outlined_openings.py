"""A door-wide stretch of a filled wall drawn by its outline alone, the wall's fill carrying on
past both its ends: an opening where it leads into a room with a label and no way in, and wall
anywhere else (beside a room's own door it is most likely a cabinet or a recess). On a made-up
floor of offices along a corridor, its walls solid fills."""

import ezdxf
import pytest
from shapely.geometry import Point

from storeypath.analyse import analyse
from storeypath.cad import read_drawing
from storeypath.extract import extract_floor
from storeypath.profile import Profile
from storeypath.samples import ORIGIN, WALL, Cell, Door, write_floor_dxf

LEFT = (10.0, 6.5)  # a stretch in the wall between 101 and 102…
RIGHT = (20.0, 6.5)  # …between 102 and 103…
TOP = (15.0, 13.0)  # …and between 104 and 105, each with its own door


def _gap(x, y, width):
    return Door(x, y, "v", 1, width, block=False)  # (the wall's fill stops either side)


def floor(*, left=1.0, label_102=True) -> list[Cell]:
    """A corridor; over it 101 and 103, each with a door onto it, and 102 between them with none;
    over those 104 and 105, each with a door. The wall's fill stops at LEFT, RIGHT and TOP."""
    return [
        Cell(0, 0, 30, 3, ["CORRIDOR"], "corridor", Door(15, 0, "h", -1)),
        Cell(0, 3, 10, 10, ["OFFICE", "101"], "office", Door(5, 3, "h", 1), doors=[_gap(*LEFT, left)]),
        Cell(10, 3, 20, 10, ["OFFICE", "102"] if label_102 else None, "office"),
        Cell(20, 3, 30, 10, ["OFFICE", "103"], "office", Door(25, 3, "h", 1), doors=[_gap(*RIGHT, 1.0)]),
        Cell(0, 10, 15, 16, ["OFFICE", "104"], "office", Door(5, 10, "h", 1), doors=[_gap(*TOP, 0.8)]),
        Cell(15, 10, 30, 16, ["OFFICE", "105"], "office", Door(25, 10, "h", 1)),
    ]


def _outlined(doc, at, width, *, filled=False):
    """The stretch drawn on the wall layer: a closed rectangle as thick as the wall (``filled``: with its fill)."""
    x, y = at
    points = [((ORIGIN[0] + px) * 1000, (ORIGIN[1] + py) * 1000) for px, py in (
        (x - WALL / 2, y - width / 2), (x + WALL / 2, y - width / 2), (x + WALL / 2, y + width / 2), (x - WALL / 2, y + width / 2))]
    msp = doc.modelspace()
    msp.add_lwpolyline(points, close=True, dxfattribs={"layer": "A-WALL"})
    if filled:
        msp.add_hatch(color=7, dxfattribs={"layer": "A-WALL"}).paths.add_polyline_path(points, is_closed=True)


def _read(tmp_path, cells, edit, walls="hatch"):
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False, walls=walls)
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    edit(doc)
    doc.saveas(tmp_path / "plan.dxf")
    doc = read_drawing(tmp_path / "plan.dxf")
    data = analyse(doc, 0.001, None, None).profile.model_dump()
    data["walls"]["layers"] = [r"A\-WALL"]  # (a floor this small, of fills alone, is not told by what is on it)
    return extract_floor(doc, Profile(**data))


def _space(ex, number):
    return next(i for i, s in enumerate(ex.spaces) if s.number == number)


def _ways(ex, number):
    i = _space(ex, number)
    return [d for d in ex.doors if i in d.connects and d.source != "window"]


def _all(doc):
    for at, width in ((LEFT, 1.0), (RIGHT, 1.0), (TOP, 0.8)):
        _outlined(doc, at, width)


def _at(x, y):
    return Point(ORIGIN[0] + x, ORIGIN[1] + y)


def test_it_is_the_way_into_a_room_with_none(tmp_path):
    # 102 has no door: the two stretches in its walls are its ways in, the rooms staying apart, and
    # each is an opening of its width, for review, with no wall left standing in it
    ex = _read(tmp_path, floor(), _all)
    assert {s.number for s in ex.spaces} >= {"101", "102", "103", "104", "105"}
    assert {tuple(sorted((ex.spaces[i].number for i in d.connects))) for d in _ways(ex, "102")} == {("101", "102"), ("102", "103")}
    for d in _ways(ex, "102"):
        assert d.source == "doorway" and d.width == pytest.approx(1.0, abs=0.01)
        assert any("only way into a room" in why for why in d.issues)
    assert not ex.walls.contains(_at(*LEFT)) and not ex.walls.contains(_at(*RIGHT))
    assert ex.walls.contains(_at(LEFT[0], LEFT[1] + 2))  # (the wall either side stands)


def test_beside_rooms_own_doors_it_is_wall(tmp_path):
    # 104 and 105 each have a door: the stretch between them is a cabinet or a recess, wall as drawn
    ex = _read(tmp_path, floor(), _all)
    a, b = _space(ex, "104"), _space(ex, "105")
    assert not [d for d in ex.doors if sorted(d.connects) == sorted([a, b])]
    assert ex.walls.contains(_at(*TOP))


def test_an_area_with_no_label_wants_no_way_in(tmp_path):
    # the same with nothing written in 102: a shaft or a gap between walls, not a room with no door
    ex = _read(tmp_path, floor(label_102=False), _all)
    a, b = _space(ex, "101"), _space(ex, "103")
    assert not [d for d in ex.doors if d.source == "doorway" and (a in d.connects or b in d.connects)]


def _taken(ex):
    return [d for d in ex.doors if any("no fill" in why for why in d.issues)]


def test_with_its_fill_it_is_wall(tmp_path):
    def edit(doc):
        _all(doc)
        _outlined(doc, LEFT, 1.0, filled=True)
        _outlined(doc, RIGHT, 1.0, filled=True)

    assert not _taken(_read(tmp_path, floor(), edit))


def test_where_walls_are_all_outlines_it_is_wall(tmp_path):
    # no fill stops at it: nothing tells it from the walls beside it
    assert not _taken(_read(tmp_path, floor(), _all, walls="polylines"))


def test_wider_than_a_door_it_is_wall(tmp_path):
    def edit(doc):
        _outlined(doc, LEFT, 3.2)
        _outlined(doc, RIGHT, 1.0, filled=True)
        _outlined(doc, TOP, 0.8)

    ex = _read(tmp_path, floor(left=3.2), edit)
    assert not _taken(ex) and _space(ex, "102") is not None
