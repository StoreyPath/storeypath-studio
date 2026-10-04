from storeypath.cad import read_drawing
from storeypath.extract import extract_floor
from storeypath.profile import load_profile
from storeypath.samples import office_floor, write_floor_dxf


def _extract(tmp_path, cells):
    write_floor_dxf(tmp_path / "plan.dxf", cells)
    return extract_floor(read_drawing(tmp_path / "plan.dxf"), load_profile("ncs"))


def _key(name, number):
    return (name or "", number or "")


def test_every_space_found_with_label_and_type(tmp_path):
    cells = office_floor(2)
    ex = _extract(tmp_path, cells)
    assert ex.warnings == []
    assert abs(ex.scale - 0.001) < 1e-12
    got = sorted((_key(s.name, s.number), s.type.value) for s in ex.spaces)
    expected = sorted((_key(c.name, c.number), c.expected_type) for c in cells)
    assert got == expected


def test_label_styles(tmp_path):
    ex = _extract(tmp_path, office_floor(1))
    by_number = {s.number: s for s in ex.spaces if s.number}
    assert by_number["104"].name == "MEETING ROOM"  # MTEXT on two lines
    assert by_number["110"].name == "STORAGE"  # block attributes
    assert by_number["114"].name is None  # number only → needs review
    assert by_number["114"].type == "unspecified"


def test_elevator_without_label_found_by_block(tmp_path):
    ex = _extract(tmp_path, office_floor(1))
    elevators = [s for s in ex.spaces if s.type == "elevator"]
    assert len(elevators) == 2
    assert {s.type_source.split(":")[0] for s in elevators} == {"label", "block"}


def test_rule_order_lobby_before_elevator(tmp_path):
    ex = _extract(tmp_path, office_floor(1))
    lobby = next(s for s in ex.spaces if s.name == "ELEV. LOBBY")
    assert lobby.type == "lobby"


def test_doors_join_the_right_spaces(tmp_path):
    cells = office_floor(1)
    ex = _extract(tmp_path, cells)
    assert len(ex.doors) == sum(1 for c in cells if c.door)
    pairs = {frozenset(_key(ex.spaces[i].name, ex.spaces[i].number) for i in d.connects) for d in ex.doors}
    assert frozenset({_key("OFFICE", "101"), _key("CORRIDOR", None)}) in pairs
    assert frozenset({_key("STAIR A", None), _key("ELEV. LOBBY", None)}) in pairs
    exterior = [d for d in ex.doors if len(d.connects) == 1]
    assert len(exterior) == 1 and ex.spaces[exterior[0].connects[0]].name == "CORRIDOR"


def test_door_point_sits_on_the_wall(tmp_path):
    ex = _extract(tmp_path, office_floor(1))
    office = next(i for i, s in enumerate(ex.spaces) if s.number == "101")
    door = next(d for d in ex.doors if office in d.connects)
    # office 101 spans x 8..12 m, its door is centred in the y = 8.5 m wall; drawing origin is (125, 48)
    assert abs(door.point.x - 135.0) < 0.05
    assert abs(door.point.y - 56.5) < 0.05


def test_floor_outline_covers_the_floor(tmp_path):
    ex = _extract(tmp_path, office_floor(1))
    assert ex.outline.geom_type == "Polygon"
    assert 930 < ex.outline.area <= 960  # 48 × 20 m minus the outer wall half


def test_units_override(tmp_path):
    write_floor_dxf(tmp_path / "plan.dxf", office_floor(1))
    ex = extract_floor(read_drawing(tmp_path / "plan.dxf"), load_profile("ncs"), units="cm")
    assert abs(ex.scale - 0.01) < 1e-12


def test_no_spaces_reports_the_layers_it_saw(tmp_path):
    write_floor_dxf(tmp_path / "plan.dxf", office_floor(1))
    profile = load_profile("ncs").model_copy(deep=True)
    profile.spaces.layers = ["NOPE"]
    profile.__dict__.pop("space_layers", None)
    ex = extract_floor(read_drawing(tmp_path / "plan.dxf"), profile)
    assert ex.spaces == []
    assert any("A-AREA" in w for w in ex.warnings)
