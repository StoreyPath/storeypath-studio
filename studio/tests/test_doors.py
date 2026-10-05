"""Doors as a person reads them: by their swing, their tag and where they stand, not
by the layer something was put on."""

from dataclasses import replace

import ezdxf
from shapely.geometry import Point

from storeypath.analyse import analyse
from storeypath.cad import read_drawing
from storeypath.extract import add_lift_doors, extract_floor
from storeypath.samples import WALL, office_floor, write_floor_dxf

ORIGIN = (125.0, 48.0)


def _mm(x, y):
    return (ORIGIN[0] + x) * 1000, (ORIGIN[1] + y) * 1000


def _plan(tmp_path, cells, edit=None):
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False, walls="lines")
    if edit is not None:
        doc = ezdxf.readfile(tmp_path / "plan.dxf")
        edit(doc)
        doc.saveas(tmp_path / "plan.dxf")
    doc = read_drawing(tmp_path / "plan.dxf")
    return extract_floor(doc, analyse(doc, 0.001, None, None).profile)  # layers read from what is drawn


def test_fixtures_on_a_door_layer_are_not_doors(tmp_path):
    def basins(doc):
        block = doc.blocks.new("A$C597558D8")  # an anonymous basin, as AutoCAD names them
        block.add_lwpolyline([(0, 0), (600, 0), (600, 450), (0, 450)], close=True)
        block.add_circle((300, 225), 150)
        for x in (9.0, 13.0, 17.0):
            doc.modelspace().add_blockref("A$C597558D8", _mm(x, 3.0), dxfattribs={"layer": "A-DOOR"})

    plain, with_basins = _plan(tmp_path, office_floor(1)), _plan(tmp_path, office_floor(1), basins)
    assert len(with_basins.doors) == len(plain.doors)


def _glazed_doorway(tmp_path, tag=None):
    """Office 101's doorway with glazing drawn across it, as a sliding door is often
    drawn, and maybe a tag beside it."""
    cells = office_floor(1)
    i = next(i for i, c in enumerate(cells) if c.number == "101")
    cells[i] = replace(cells[i], door=replace(cells[i].door, block=False))
    d = cells[i].door

    def glaze(doc):
        for dy in (-WALL / 2, 0, WALL / 2):
            doc.modelspace().add_line(_mm(d.x - d.width / 2, d.y + dy), _mm(d.x + d.width / 2, d.y + dy),
                                      dxfattribs={"layer": "A-GLAZ"})
        if tag:
            doc.modelspace().add_text(tag, height=150, dxfattribs={"layer": "A-ANNO-SYMB"}).set_placement(
                _mm(d.x, d.y - 0.5))

    ex = _plan(tmp_path, cells, glaze)
    at = Point(ORIGIN[0] + d.x, ORIGIN[1] + d.y)  # extraction works in meters
    return ex, min(ex.doors, key=lambda o: o.point.distance(at))


def test_glazing_between_two_rooms_is_a_sliding_door(tmp_path):
    _, opening = _glazed_doorway(tmp_path)
    assert opening.source == "glazing" and len(opening.connects) == 2
    assert opening.issues == ["drawn as glazing between two rooms, taken as a sliding door; check it"]


def test_a_tag_says_whether_an_opening_is_a_door_or_a_window(tmp_path):
    _, door = _glazed_doorway(tmp_path, "D2")
    assert (door.source, door.tag, door.issues) == ("door", "D2", [])
    (tmp_path / "w").mkdir()
    _, window = _glazed_doorway(tmp_path / "w", "W-3")
    assert (window.source, window.tag) == ("window", "W3")


def test_a_lift_drawn_without_a_door_opens_onto_the_lobby(tmp_path):
    cells = office_floor(1)
    i = next(i for i, c in enumerate(cells) if c.label == ["LIFT"])
    cells[i] = replace(cells[i], door=None)
    ex = _plan(tmp_path, cells)
    lift = next(k for k, s in enumerate(ex.spaces) if s.name == "LIFT")
    assert not any(lift in d.connects for d in ex.doors)
    add_lift_doors(ex)
    door = next(d for d in ex.doors if lift in d.connects)
    other = ex.spaces[next(k for k in door.connects if k != lift)]
    assert door.source == "assumed" and other.name == "ELEV. LOBBY"  # not the WC next to it
    assert door.issues and "check it" in door.issues[0]
    assert abs(door.span.length - 0.9) < 1e-6 and abs(door.point.x - (ORIGIN[0] + 3)) < 0.2  # in their shared wall


def test_a_layer_named_for_doors_holding_the_walls_is_walls(tmp_path):
    # Drafters sometimes draw walls on whatever layer is current: here every wall is
    # on "jundoor". Its name says doors, but it holds the whole wall frame.
    def move(doc):
        doc.layers.add("jundoor")
        for e in doc.modelspace().query('LINE[layer=="A-WALL"]'):
            e.dxf.layer = "jundoor"

    write_floor_dxf(tmp_path / "plan.dxf", office_floor(1), area_outlines=False, walls="lines")
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    move(doc)
    doc.saveas(tmp_path / "plan.dxf")
    roles = {r.layer: r.roles for r in analyse(read_drawing(tmp_path / "plan.dxf"), 0.001, None, None).roles}
    assert "walls" in roles["jundoor"]
