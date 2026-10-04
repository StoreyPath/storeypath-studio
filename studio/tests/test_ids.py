import pytest

from storeypath.ids import (
    CROCKFORD_ALPHABET,
    child_id,
    default_floor_code,
    format_object_code,
    generate_project_code,
    make_id,
    parse_id,
)


def test_project_code_is_six_unambiguous_characters():
    codes = {generate_project_code() for _ in range(500)}
    assert len(codes) > 490
    for code in codes:
        assert len(code) == 6
        assert set(code) <= set(CROCKFORD_ALPHABET)
        assert not set(code) & set("ILOU")


def test_ids_read_back_their_hierarchy():
    p = parse_id("K7Q2XM-RUH-HQ-F02-0142")
    assert p.level == "object"
    assert p.project == "K7Q2XM"
    assert p.prefix("location") == "K7Q2XM-RUH"
    assert p.prefix("building") == "K7Q2XM-RUH-HQ"
    assert p.prefix("floor") == "K7Q2XM-RUH-HQ-F02"
    assert p.parent == "K7Q2XM-RUH-HQ-F02"
    assert p.code == "0142"
    assert parse_id("K7Q2XM-RUH").level == "location"


def test_child_id_appends_one_level():
    assert child_id("K7Q2XM-RUH", "HQ") == "K7Q2XM-RUH-HQ"
    with pytest.raises(ValueError):
        child_id("K7Q2XM-RUH-HQ-F02-0142", "X")


@pytest.mark.parametrize("bad", ["hq", "H-Q", "", "H Q", "A" * 17])
def test_invalid_segments_are_rejected(bad):
    with pytest.raises(ValueError):
        make_id("K7Q2XM", bad)


def test_too_many_segments_rejected():
    with pytest.raises(ValueError):
        parse_id("A-B-C-D-E-F")


def test_codes():
    assert format_object_code(7) == "0007"
    assert format_object_code(12345) == "12345"
    assert default_floor_code(0) == "F00"
    assert default_floor_code(3) == "F03"
    assert default_floor_code(-1) == "B01"
