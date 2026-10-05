"""Rooms typed by the symbols drawn in them (symbols/), with a stand-in for the model."""

import ezdxf
import pytest
from shapely.geometry import box, shape

from storeypath.convert import convert_floor
from storeypath.extract import ExtractedSpace
from storeypath.symbols import Symbol, SymbolSpotter, primitives, type_rooms
from storeypath.types import SpaceType


class FakeSpotter(SymbolSpotter):
    """Finds the symbols it is given, wherever they are."""

    def __init__(self, symbols):
        super().__init__()
        self.symbols, self.calls = symbols, 0

    def available(self):
        return True

    def find(self, doc, region=None, offset=None, scale=1.0, skip_layer=None):
        self.calls += 1
        return list(self.symbols)


def _at(label, x, y, size=0.4, score=0.95, lines=4):
    return Symbol(label, score, (x, y), (x - size / 2, y - size / 2, x + size / 2, y + size / 2), lines)


def _middle(record):
    return shape(record.geometry).centroid.coords[0]


def _unnamed_room(ws, f_id):
    """The sample office floor's one room with a number and no name."""
    return next(r for r in ws.floor_objects(f_id) if r.kind == "space" and r.type == "unspecified")


def test_an_unnamed_room_is_typed_by_its_fixtures_and_flagged(workspace):
    ws, d, f_id, _, _ = workspace
    convert_floor(ws, f_id, d)
    room = _unnamed_room(ws, f_id)
    x, y = _middle(room)
    office = next(r for r in ws.floor_objects(f_id) if r.type == "office")
    ox, oy = _middle(office)
    spotter = FakeSpotter([_at("toilet", x, y), _at("bath tub", x + 0.5, y), _at("toilet", ox, oy)])
    report = convert_floor(ws, f_id, d, symbols=spotter)
    room, office = ws.objects[room.id], ws.objects[office.id]
    assert (room.type, room.type_source) == ("bathroom", "symbols:bath tub+toilet")
    assert any("from the symbols drawn in it" in i for i in room.issues)
    assert office.type == "office"  # named rooms keep the type their name gives
    assert room.id not in report.unspecified


def test_symbols_found_once_are_kept_with_the_floor(workspace):
    ws, d, f_id, _, _ = workspace
    convert_floor(ws, f_id, d)
    x, y = _middle(_unnamed_room(ws, f_id))
    spotter = FakeSpotter([_at("washing machine", x, y)])
    convert_floor(ws, f_id, d, symbols=spotter)
    convert_floor(ws, f_id, d, symbols=spotter)
    assert spotter.calls == 1  # the drawing did not change: found again from the floor
    floor = ws.floor(f_id)
    assert [s["label"] for s in floor.symbols] == ["washing machine"]
    convert_floor(ws, f_id, d)  # without the model: still the laundry it was found to be
    assert _laundry(ws, f_id)
    floor.source.offset = (1.0, 0.0)  # the plan moved: the symbols kept are no longer where they were
    convert_floor(ws, f_id, d)
    assert not _laundry(ws, f_id)


def _laundry(ws, f_id):
    return any(r.type == "laundry" for r in ws.floor_objects(f_id) if r.kind == "space")


def _space(w, h, kind=SpaceType.UNSPECIFIED):
    return ExtractedSpace(box(0, 0, w, h), "A-WALL", type=kind)


def test_stairs_type_a_room_they_fill_not_a_hall_they_stand_in():
    stair_room, hall = _space(3, 5), _space(10, 10)
    flight = Symbol("stairs", 0.96, (1.5, 2.5), (0.2, 0.3, 2.8, 4.7), 14)
    type_rooms([stair_room], [flight])
    type_rooms([hall], [flight])
    assert stair_room.type == SpaceType.STAIRS and stair_room.type_source == "symbols:stairs"
    assert hall.type == SpaceType.UNSPECIFIED


def test_unsure_or_thin_symbols_type_nothing():
    rooms = [_space(3, 5), _space(3, 5), _space(3, 5)]
    type_rooms(rooms[:1], [Symbol("stairs", 0.96, (1.5, 2.5), (0.2, 0.3, 2.8, 4.7), 2)])  # two lines: not a flight
    type_rooms(rooms[1:2], [_at("gas stove", 1.5, 2.5, score=0.6)])
    type_rooms(rooms[2:], [_at("toilet", 9, 9)])  # outside the room
    assert [r.type for r in rooms] == [SpaceType.UNSPECIFIED] * 3
    alone = _space(2, 2)
    type_rooms([alone], [_at("toilet", 1, 1)])
    assert alone.type == SpaceType.RESTROOM  # a toilet and no bath: a WC


def test_arcs_in_mirrored_blocks_are_read_where_they_are_drawn():
    doc = ezdxf.new()
    block = doc.blocks.new("SINK")
    block.add_arc((100, 0), 50, 0, 180)
    block.add_circle((100, 0), 20)
    doc.modelspace().add_blockref("SINK", (5000, 3000), dxfattribs={"xscale": -1})
    found = primitives(doc)
    assert len(found) == 2
    for p in found:
        for x, y in p.points:
            assert 4800 < x < 5000 and 2900 < y < 3100  # mirrored about the insert point, not about 0


class BrokenSpotter(FakeSpotter):
    def find(self, *args, **kw):
        self.calls += 1
        self.failed = "SymPoint-V2 failed: out of memory"
        return []


def test_a_failed_run_is_reported_and_tried_again_next_time(workspace):
    ws, d, f_id, _, _ = workspace
    spotter = BrokenSpotter([])
    report = convert_floor(ws, f_id, d, symbols=spotter)
    assert "symbols were not spotted: SymPoint-V2 failed: out of memory" in report.warnings
    assert ws.floor(f_id).symbols is None
    convert_floor(ws, f_id, d, symbols=spotter)
    assert spotter.calls == 2


def test_a_missing_install_is_said_and_skipped(workspace, tmp_path):
    spotter = SymbolSpotter(tmp_path / "nowhere")
    assert "not installed" in spotter.missing() and not spotter.available()
    ws, d, f_id, _, _ = workspace
    report = convert_floor(ws, f_id, d, symbols=spotter)
    assert ws.floor(f_id).symbols is None and not any("symbols" in w for w in report.warnings)


@pytest.mark.skipif(not SymbolSpotter().available(), reason="SymPoint-V2 is not installed ($STOREYPATH_SYMBOLS)")
def test_sympoint_runs_on_a_plan(workspace):
    ws, d, f_id, _, _ = workspace
    spotter = SymbolSpotter()
    report = convert_floor(ws, f_id, d, symbols=spotter)
    assert spotter.failed is None, spotter.failed
    found = ws.floor(f_id).symbols
    assert found and not any("symbols" in w for w in report.warnings)
    assert {"single door", "double door"} & {s["label"] for s in found}  # the sample's doors are blocks
