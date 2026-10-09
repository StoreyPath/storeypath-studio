from dataclasses import replace

import pytest
from shapely.geometry import LineString, Point, box

from storeypath.cad import read_drawing
from storeypath.convert import convert_floor
from storeypath.extract import extract_floor
from storeypath.geometry import iou
from storeypath.profile import WallsConfig, load_profile
from storeypath.samples import WALL, office_floor, write_floor_dxf
from storeypath.walls import DoorShape, DoorSwing, open_issue, spaces_from_walls

ORIGIN = (125.0, 48.0)


def _extract(tmp_path, cells, profile="ncs", **kw):
    write_floor_dxf(tmp_path / "plan.dxf", cells, **kw)
    return extract_floor(read_drawing(tmp_path / "plan.dxf"), load_profile(profile) if isinstance(profile, str) else profile)


def _room(c):
    """Where a sample room's floor is: inside its walls, in drawing meters."""
    ox, oy = ORIGIN
    h = WALL / 2
    return box(ox + c.x0 + h, oy + c.y0 + h, ox + c.x1 - h, oy + c.y1 - h)


def _key(name, number):
    return (name or "", number or "")


@pytest.mark.parametrize("walls", ["polylines", "lines", "hatch"])
def test_spaces_found_from_walls_alone(tmp_path, walls):
    cells = office_floor(1)
    ex = _extract(tmp_path, cells, area_outlines=False, walls=walls)
    assert ex.method == "walls"
    assert ex.warnings == []
    assert len(ex.spaces) == len(cells)
    for c in cells:
        best = max(ex.spaces, key=lambda s: iou(s.polygon, _room(c)))
        assert iou(best.polygon, _room(c)) > 0.99, c.label
        assert (_key(best.name, best.number), best.type) == (_key(c.name, c.number), c.expected_type)
    assert len(ex.doors) == sum(1 for c in cells if c.door)
    assert 970 < ex.outline.area < 975  # 48 × 20 m to the outer faces of the outside walls


def test_outlines_are_used_when_the_drawing_has_them(tmp_path):
    ex = _extract(tmp_path, office_floor(1))
    assert ex.method == "outlines"


def test_walls_method_ignores_outlines(tmp_path):
    profile = load_profile("ncs").model_copy(deep=True)
    profile.spaces.method = "walls"
    cells = office_floor(1)
    ex = _extract(tmp_path, cells, profile)
    assert ex.method == "walls" and len(ex.spaces) == len(cells)


def test_outside_door_without_a_block_still_encloses_the_room(tmp_path):
    cells = office_floor(1)
    corridor = next(i for i, c in enumerate(cells) if c.expected_type == "corridor")
    cells[corridor] = replace(cells[corridor], door=replace(cells[corridor].door, block=False))
    ex = _extract(tmp_path, cells, area_outlines=False)
    assert len(ex.spaces) == len(cells)
    found = next(s for s in ex.spaces if s.type == "corridor")
    assert iou(found.polygon, _room(cells[corridor])) > 0.98
    assert found.issues == ["open to the outside through 1.8 m with no door or window drawn"]
    assert all(not s.issues for s in ex.spaces if s is not found)


def test_doorway_without_a_door_is_closed_and_joins_the_rooms(tmp_path):
    cells = office_floor(1)
    office = next(i for i, c in enumerate(cells) if c.number == "101")
    cells[office] = replace(cells[office], door=replace(cells[office].door, block=False))
    ex = _extract(tmp_path, cells, area_outlines=False, walls="lines")
    assert len(ex.spaces) == len(cells) and ex.warnings == []
    room = next(i for i, s in enumerate(ex.spaces) if s.number == "101")
    assert iou(ex.spaces[room].polygon, _room(cells[office])) > 0.99
    doorway = next(d for d in ex.doors if room in d.connects)
    assert doorway.source == "doorway"
    assert {ex.spaces[i].name for i in doorway.connects} == {"OFFICE", "CORRIDOR"}


def _wide_opening(cells):
    office = next(i for i, c in enumerate(cells) if c.number == "101")
    cells[office] = replace(cells[office], door=replace(cells[office].door, block=False, width=2.0))
    return office


def test_a_doorway_with_no_door_divides_two_spaces(tmp_path):
    # An office open onto the corridor through a 2 m gap in its wall, no door drawn:
    # the wall goes on in line beyond the gap, so they are two spaces joined there.
    cells = office_floor(1)
    office = _wide_opening(cells)
    ex = _extract(tmp_path, cells, area_outlines=False)
    assert len(ex.spaces) == len(cells) and ex.zones == []
    room = next(s for s in ex.spaces if s.number == "101")
    assert room.name == "OFFICE" and iou(room.polygon, _room(cells[office])) > 0.95
    assert any("gap in the wall" in i for i in room.issues)
    opening = next(d for d in ex.doors if d.source == "doorway" and room in [ex.spaces[i] for i in d.connects])
    assert {ex.spaces[i].name for i in opening.connects} == {"OFFICE", "CORRIDOR"}


def test_an_open_hall_with_two_uses_is_one_space_with_two_zones(tmp_path):
    # A majlis and a dining area in one hall, no wall between them: one space, divided
    # into two zones between their labels; the zones carry the names.
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.M
    for layer in ("A-WALL", "A-AREA-IDEN"):
        doc.layers.add(layer)
    msp = doc.modelspace()
    for a, b, c, d in ((-0.2, -0.2, 10.2, 5.2), (0, 0, 10, 5)):
        msp.add_lwpolyline([(a, b), (c, b), (c, d), (a, d)], close=True, dxfattribs={"layer": "A-WALL"})
    for text, x in (("MAJLIS", 2.5), ("DINING", 7.5)):
        msp.add_text(text, height=0.25, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement((x, 2.5))
    doc.saveas(tmp_path / "hall.dxf")
    ex = extract_floor(read_drawing(tmp_path / "hall.dxf"), load_profile("ncs"))
    assert len(ex.spaces) == 1 and ex.spaces[0].name is None
    assert sorted(z.name for z in ex.zones) == ["DINING", "MAJLIS"]
    assert all(z.space == 0 for z in ex.zones)
    assert abs(sum(z.polygon.area for z in ex.zones) - ex.spaces[0].polygon.area) < 0.01  # they divide it
    assert not any(d.source == "doorway" for d in ex.doors)
    assert {u.name for u in ex.units()} == {"MAJLIS", "DINING"}



def test_labels_that_say_the_same_do_not_split_a_space(tmp_path):
    # ROOF at both ends of a roof is one roof: the office opening wide onto the
    # corridor, labelled CORRIDOR too, stays part of the corridor.
    cells = office_floor(1)
    office = _wide_opening(cells)
    cells[office] = replace(cells[office], label=["CORRIDOR"])
    ex = _extract(tmp_path, cells, area_outlines=False)
    assert len(ex.spaces) == len(cells) - 1
    corridor = next(s for s in ex.spaces if s.name and "CORRIDOR" in s.name)
    assert corridor.name == "CORRIDOR" and not corridor.issues
    assert not [d for d in ex.doors if d.source == "split"]

def test_labels_too_far_apart_to_split_stay_one_space(tmp_path):
    profile = load_profile("ncs").model_copy(deep=True)
    profile.spaces.max_split = 1.0
    cells = office_floor(1)
    _wide_opening(cells)
    ex = _extract(tmp_path, cells, profile, area_outlines=False)
    assert len(ex.spaces) == len(cells) - 1
    merged = next(s for s in ex.spaces if s.number == "101")
    assert merged.issues and "2 rooms" in merged.issues[0]
    assert any("labels of several rooms" in w for w in ex.warnings)


def test_door_swing_closes_a_door_in_a_diagonal_wall():
    # A 45° wall with a 0.9 m door drawn as a leaf and a swing arc (no block).
    import math
    d = math.sqrt(0.5)
    outline = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    diag_a = [(0, 0), (4 * d, 4 * d)]
    diag_b = [((4 + 0.9) * d, (4 + 0.9) * d), (10, 10)]
    hinge = Point(4 * d, 4 * d)
    closed = Point((4 + 0.9) * d, (4 + 0.9) * d)
    leaf_open = Point(hinge.x - 0.9 * d, hinge.y + 0.9 * d)
    door = DoorShape(box(*LineString([hinge, closed, leaf_open]).bounds), [DoorSwing(hinge, (leaf_open, closed))])
    lines = [LineString(outline), LineString(diag_a), LineString(diag_b)]
    found = spaces_from_walls(lines, [], [door], [], [], WallsConfig(max_doorway=0.5), 0.5)
    assert sorted(round(p.area) for p in found.polygons) == [50, 50]


def test_ids_survive_switching_from_outlines_to_walls(workspace):
    ws, d, f_id, _, cells = workspace
    first = convert_floor(ws, f_id, d)
    write_floor_dxf(d / "level-2.dxf", cells, area_outlines=False, walls="lines")
    again = convert_floor(ws, f_id, d)
    assert again.method == "walls"
    assert (len(again.added), len(again.retired)) == (0, 0)
    assert sorted(again.kept) == sorted(first.added)


# A 10 × 6 m building of single-line walls with a 3 m wide, 1.5 m deep entrance
# recess in the south facade.
OUTLINE = [(0, 0), (3.5, 0), (3.5, 1.5), (6.5, 1.5), (6.5, 0), (10, 0), (10, 6), (0, 6), (0, 0)]


def _house(label_points=()):
    return spaces_from_walls([LineString(OUTLINE)], [], [], [], [Point(p) for p in label_points], WallsConfig(), 0.5)


def test_recess_in_the_facade_is_not_a_space():
    found = _house()
    assert len(found.pockets) == 1
    assert len(found.polygons) == 1
    assert abs(found.polygons[0].area - (60 - 4.5)) < 0.1
    assert abs(found.outline.area - (60 - 4.5)) < 0.5


def test_recess_with_a_label_is_a_space():
    found = _house(label_points=[(5, 0.7)])
    assert found.pockets == [] and len(found.polygons) == 2
    porch = min(found.polygons, key=lambda p: p.area)
    house = max(found.polygons, key=lambda p: p.area)
    assert open_issue(porch, found.open_edges)[0].startswith("open to the outside through 3.0 m")
    assert open_issue(house, found.open_edges) == []


def test_wall_core_thicker_than_the_limit_is_not_filled():
    # Two parallel lines 1 m apart are not one wall: the gap between them stays open.
    lines = [LineString([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]), LineString([(0, 5), (10, 5)]),
             LineString([(0, 6), (10, 6)])]
    found = spaces_from_walls(lines, [], [], [], [], WallsConfig(), 0.5)
    assert sorted(round(p.area) for p in found.polygons) == [10, 40, 50]
    found = spaces_from_walls(lines, [], [], [], [], WallsConfig(max_thickness=1.2), 0.5)
    assert sorted(round(p.area) for p in found.polygons) == [40, 50]


def test_rooms_are_divided_straight_across_not_on_the_diagonal():
    # Dining above, majlis below, no wall between: a nib on each side, at different
    # heights, makes the shortest cut a diagonal between their corners.
    import math

    from shapely.geometry import Polygon

    from storeypath.split import split_by_labels

    room = Polygon([(0, 0), (5.1, 0), (5.1, 4.0), (4.7, 4.0), (4.7, 4.6), (5.1, 4.6), (5.1, 7.5), (0, 7.5),
                    (0, 2.6), (0.4, 2.6), (0.4, 2.0), (0, 2.0)])
    parts, cuts = split_by_labels(room, [[Point(2.5, 6.5)], [Point(2.5, 1.0)]], max_cut=6.0, min_area=0.5)
    assert len(parts) == 2 and len(cuts) == 1
    (x0, y0), (x1, y1) = cuts[0].coords[0], cuts[0].coords[-1]
    assert abs(y1 - y0) < 0.01 and math.dist((x0, y0), (x1, y1)) < 5.2  # square to the walls, across the room
    assert abs(sum(p.area for p in parts) - room.area) < 1e-6


def test_markers_beside_the_plan_on_a_wall_layer_are_not_walls(tmp_path):
    # An elevation marker, a filled triangle, drawn on the wall layer a little way off
    # the building: not a wall. A column inside a room is.
    import ezdxf

    cells = office_floor(1)
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False)
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    ox, oy = ORIGIN
    xmax = max(c.x1 for c in cells)
    marker = [((ox + xmax + 0.8) * 1000, (oy + 5) * 1000), ((ox + xmax + 1.6) * 1000, (oy + 4.5) * 1000),
              ((ox + xmax + 1.6) * 1000, (oy + 5.5) * 1000)]
    hatch = doc.modelspace().add_hatch(dxfattribs={"layer": "A-WALL"})
    hatch.paths.add_polyline_path(marker, is_closed=True)
    office = next(c for c in cells if c.number == "101")
    cx, cy = office.center
    doc.modelspace().add_lwpolyline([((ox + cx + dx) * 1000, (oy + cy + dy) * 1000) for dx, dy in
                                     ((-0.15, -0.15), (0.15, -0.15), (0.15, 0.15), (-0.15, 0.15))],
                                    close=True, dxfattribs={"layer": "A-WALL"})
    doc.saveas(tmp_path / "plan.dxf")
    ex = extract_floor(read_drawing(tmp_path / "plan.dxf"), load_profile("ncs"))
    assert not ex.walls.intersects(Point(ox + xmax + 1.3, oy + 5))  # the marker
    assert not ex.outline.intersects(Point(ox + xmax + 1.3, oy + 5))  # nor a floor under it
    assert ex.walls.intersects(Point(ox + cx, oy + cy))  # the column


def test_an_x_across_a_room_does_not_cut_it(tmp_path):
    # A void, an opening to below or a lift car is marked with an X, often drawn on
    # the wall layer, even inside one polyline with the rectangle around it.
    import ezdxf

    cells = office_floor(1)
    room = next(c for c in cells if c.number == "101")
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False, walls="lines")
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    ox, oy = ORIGIN
    h = WALL / 2  # the X's rectangle on the room's wall faces, as drawn
    x0, y0, x1, y1 = (ox + room.x0 + h) * 1000, (oy + room.y0 + h) * 1000, (ox + room.x1 - h) * 1000, (oy + room.y1 - h) * 1000
    doc.modelspace().add_lwpolyline([(x0, y0), (x1, y1), (x0, y1), (x1, y0), (x0, y0)], dxfattribs={"layer": "A-WALL"})
    doc.saveas(tmp_path / "plan.dxf")
    ex = extract_floor(read_drawing(tmp_path / "plan.dxf"), load_profile("ncs"))
    assert len(ex.spaces) == len(cells)
    found = next(s for s in ex.spaces if s.number == "101")
    assert iou(found.polygon, _room(room)) > 0.95


def test_a_dashed_line_on_the_wall_layer_is_not_a_wall(tmp_path):
    # A dome or a void overhead is drawn dashed, often on the wall layer: a ring of
    # doubled lines like a round wall, but nothing stands there.
    import ezdxf

    cells = office_floor(1)
    room = next(c for c in cells if c.number == "101")
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False, walls="lines")
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    ox, oy = ORIGIN
    middle = ((ox + (room.x0 + room.x1) / 2) * 1000, (oy + (room.y0 + room.y1) / 2) * 1000)
    radius = min(room.x1 - room.x0, room.y1 - room.y0) / 3 * 1000
    for r in (radius, radius - WALL * 1000):
        doc.modelspace().add_circle(middle, r, dxfattribs={"layer": "A-WALL", "linetype": "DASHED"})
    doc.saveas(tmp_path / "plan.dxf")
    ex = extract_floor(read_drawing(tmp_path / "plan.dxf"), load_profile("ncs"))
    assert len(ex.spaces) == len(cells)
    found = next(s for s in ex.spaces if s.number == "101")
    assert iou(found.polygon, _room(room)) > 0.95
    assert not ex.walls.intersects(Point(middle[0] / 1000 + radius / 1000 - WALL / 2, middle[1] / 1000).buffer(0.05))


def test_the_garden_inside_a_plot_wall_is_not_a_room(tmp_path):
    # A plot wall drawn around the house with the same walls: the yard between them
    # holds every room in its hole. A person sees the outside, not a room.
    import ezdxf

    cells = office_floor(1)
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False, walls="lines")
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    ox, oy = ORIGIN
    for inset in (0.0, 0.2):
        a, b, c, d = ox - 8 + inset, oy - 8 + inset, ox + 56 - inset, oy + 28 - inset
        doc.modelspace().add_lwpolyline([(a * 1000, b * 1000), (c * 1000, b * 1000), (c * 1000, d * 1000), (a * 1000, d * 1000)],
                                         close=True, dxfattribs={"layer": "A-WALL"})
    doc.saveas(tmp_path / "plan.dxf")
    ex = extract_floor(read_drawing(tmp_path / "plan.dxf"), load_profile("ncs"))
    assert len(ex.spaces) == len(cells)  # the rooms, and no yard
    assert ex.outline.area < 60 * 30  # the building, not the plot


def test_the_yard_inside_the_land_line_is_not_a_room(tmp_path):
    # The land drawn as one thin line on the wall layer, the house built against it on
    # one side (so the yard wraps only part of the house) and a level note in the yard:
    # still the outside, and the line is not a wall.
    import ezdxf

    cells = office_floor(1)
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False, walls="lines")
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    ox, oy = ORIGIN
    top = oy + 20 + WALL / 2
    land = [(ox - 8, oy - 8), (ox + 56, oy - 8), (ox + 56, top), (ox - 8, top)]
    doc.modelspace().add_lwpolyline([(x * 1000, y * 1000) for x, y in land], close=True, dxfattribs={"layer": "A-WALL"})
    doc.modelspace().add_text("LANDING", height=250, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(
        ((ox - 4) * 1000, (oy + 5) * 1000))
    doc.saveas(tmp_path / "plan.dxf")
    ex = extract_floor(read_drawing(tmp_path / "plan.dxf"), load_profile("ncs"))
    assert len(ex.spaces) == len(cells) and not any(s.name == "LANDING" for s in ex.spaces)
    assert ex.outline.area < 49 * 21  # the building, not the land
    assert ex.walls.distance(Point(ox - 8, oy)) > 1 and ex.walls.distance(Point(ox + 30, oy - 8)) > 1


def test_a_plan_in_single_lines_has_no_yard_to_set_apart():
    # The same shapes drawn all in single lines: a big room on the edge is a room.
    lines = [LineString([(0, 0), (20, 0), (20, 10), (0, 10), (0, 0)]), LineString([(14, 0), (14, 10)]),
             LineString([(14, 5), (20, 5)])]
    found = spaces_from_walls(lines, [], [], [], [], WallsConfig(), 0.5)
    assert sorted(round(p.area) for p in found.polygons) == [30, 30, 140]


def test_the_outside_set_aside_takes_its_walls_out_of_the_building():
    # Vision set aside the yard inside a plot wall drawn as a wall: the floor's outline
    # and walls end at the house; a shaft set aside inside the house stays in it.
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from storeypath.extract import ExtractedSpace, FloorExtraction, keep_to_the_building

    def ring(a, b):
        return box(-a, -a, 10 + a, 10 + a).difference(box(-b, -b, 10 + b, 10 + b))

    rooms = [box(0, 0, 4.9, 10), box(5.1, 0, 10, 8)]
    shaft = box(5.1, 8.2, 10, 10)
    yard = Polygon(box(-5, -5, 15, 15).exterior, [box(-0.2, -0.2, 10.2, 10.2).exterior.coords])
    walls = unary_union([ring(5.2, 5), ring(0.2, 0), box(4.9, 0, 5.1, 10), box(5.1, 8, 10, 8.2)])
    spaces = [ExtractedSpace(p, "walls") for p in rooms] + [ExtractedSpace(shaft, "walls", ignored=True),
                                                         ExtractedSpace(yard, "walls", name="GARDEN", ignored=True)]
    ex = FloorExtraction(spaces, [], box(-5.2, -5.2, 15.2, 15.2), 1.0, walls=walls)
    keep_to_the_building(ex)
    assert abs(ex.outline.area - 10.4 ** 2) < 1  # the house, shaft and all
    assert ex.walls.distance(Point(-5.1, 5)) > 4 and ex.walls.distance(Point(-0.1, 5)) == 0


def test_a_flight_of_stairs_is_found_by_its_treads():
    from storeypath.extract import stair_flights

    flight = [LineString([(x, 0), (x, 1.2)]) for x in [i * 0.3 for i in range(10)]]
    pairs = [LineString([(x + 0.025, 0), (x + 0.025, 1.2)]) for x in [i * 0.3 for i in range(10)]]  # treads drawn double
    beside = [LineString([(x, 1.6), (x, 2.8)]) for x in [i * 0.3 for i in range(8)]]  # the next flight, turned back
    found = stair_flights(flight + pairs + beside)
    assert len(found) == 2
    tiles = [LineString([(x, 0), (x, 2.4)]) for x in [i * 0.3 for i in range(9)]] + \
        [LineString([(0, y), (2.4, y)]) for y in [i * 0.3 for i in range(9)]]  # a tiled floor is a grid, not a stair
    assert stair_flights(tiles) == []


def test_slivers_broken_off_a_room_are_not_rooms(monkeypatch):
    # Putting a room's outline back on the wall lines can, at a very sharp corner,
    # break hairline slivers off it (a large floor had five, the smallest with
    # nothing in it at all): only the room is kept.
    import storeypath.walls as walls
    from shapely.geometry import Polygon

    def with_slivers(geom):
        return [*as_parts(geom), Polygon([(0, 0), (0.03, 0.0001), (0, 0.0002)]), Polygon()]

    as_parts = walls.as_polygons
    monkeypatch.setattr(walls, "as_polygons", with_slivers)
    lines = [LineString([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]), LineString([(0, 5), (10, 5)])]
    found = spaces_from_walls(lines, [], [], [], [], WallsConfig(), 0.5)
    assert sorted(round(p.area) for p in found.polygons) == [50, 50]
