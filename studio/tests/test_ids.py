import json
import random

import pytest

from storeypath.assets import asset_dir
from storeypath.ids import (
    CROCKFORD_ALPHABET,
    child_id,
    default_floor_code,
    format_object_code,
    generate_project_code,
    is_item_id,
    is_legacy_item_id,
    item_id_for_legacy,
    item_check_symbol,
    make_id,
    new_item_id,
    normalize_item_id,
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


# ---- items: asset IDs (format 0.8) -------------------------------------------------

VECTORS = json.loads((asset_dir("spec") / "conformance" / "asset-ids.json").read_text())


def _written(whole: str) -> str:
    return f"{whole[:4]}-{whole[4:8]}-{whole[8:]}"


def test_a_new_item_id_is_ten_random_symbols_and_its_check_4_4_3():
    ids = {new_item_id() for _ in range(2000)}
    assert len(ids) == 2000
    for i in ids:
        groups = i.split("-")
        assert [len(g) for g in groups] == [4, 4, 3] and set("".join(groups)) <= set(CROCKFORD_ALPHABET)
        assert is_item_id(i) and normalize_item_id(i) == i and item_check_symbol("".join(groups)[:10]) == i[-1]
    for at in range(10):  # every symbol in every place, as random draws give them
        assert {i.replace("-", "")[at] for i in ids} == set(CROCKFORD_ALPHABET)


def test_a_new_item_id_is_drawn_again_while_it_is_taken():
    drawn = []

    def taken(i):
        drawn.append(i)
        return len(drawn) < 4  # the first three are in use

    i = new_item_id(taken)
    assert len(drawn) == 4 and i == drawn[-1] and len(set(drawn)) == 4


def test_the_check_symbol_is_luhn_mod_32():
    assert VECTORS["alphabet"] == CROCKFORD_ALPHABET
    assert item_check_symbol("7K2QXM9F4D") == "P"  # FORMAT.md's worked example
    for v in VECTORS["check"]:
        assert item_check_symbol(v["symbols"]) == v["check"], v
    for bad in ("7K2QXM9F4", "7K2QXM9F4DP", "7k2qxm9f4d", "7K2QXM9F4O"):
        with pytest.raises(ValueError):
            item_check_symbol(bad)


def test_the_conformance_ids_are_read_as_every_reader_reads_them():
    assert VECTORS["valid"][0] == "7K2Q-XM9F-4DP" and all(is_item_id(i) for i in VECTORS["valid"])
    assert not any(is_item_id(i) for i in VECTORS["wrong_symbol"] + VECTORS["swapped"] + VECTORS["not_ids"])
    assert all(is_item_id(i) for i in VECTORS["swapped_unseen"])  # a 0 and a Z beside it: the check cannot see it
    for v in VECTORS["typed"]:
        assert normalize_item_id(v["text"]) == v["id"], v


def test_every_symbol_wrong_is_caught_and_every_swap_but_0_and_z():
    rng = random.Random(3)
    unseen = set()
    for _ in range(300):
        whole = new_item_id().replace("-", "")
        for at in range(11):
            for c in CROCKFORD_ALPHABET:
                if c != whole[at]:
                    assert not is_item_id(_written(whole[:at] + c + whole[at + 1:])), (whole, at, c)
        at = rng.randrange(9)  # a 0 and a Z somewhere, often beside each other
        symbols = whole[:at] + rng.choice("0Z") + rng.choice("0Z") + whole[at + 2:10]
        whole = symbols + item_check_symbol(symbols)
        for at in range(10):
            a, b = whole[at], whole[at + 1]
            if a != b and is_item_id(_written(whole[:at] + b + a + whole[at + 2:])):
                unseen.add(a + b)
    assert unseen == {"0Z", "Z0"}


def test_what_people_type_is_read_as_an_item_id():
    assert normalize_item_id("7k2q xm9f 4dp") == "7K2Q-XM9F-4DP"
    assert normalize_item_id("7K2Q-XM9F-4DP") == "7K2Q-XM9F-4DP"
    assert normalize_item_id("lOZO-iabc-Ldd") == "10Z0-1ABC-1DD"  # O for 0; I and L for 1
    for not_one in ("7K2Q-XM9F-4DK", "7K2Q-XM9F-4D", "7K2U-XM9F-4DP", "7K2Q-XM9F-4ıP", "K7Q2XM-RUH-HQ",
                    None, 42, "7K2Q" + " " * 100 + "XM9F4DP"):
        assert normalize_item_id(not_one) is None, not_one


def test_an_item_id_is_not_a_place_id():
    # 4-4-3 reads as a building's ID (three segments): refused, as an item's
    with pytest.raises(ValueError, match="an item's ID"):
        parse_id("7K2Q-XM9F-4DP")
    assert parse_id("7K2Q-XM9F-4DK").level == "building"  # its check wrong: not an item's ID
    assert not is_item_id("K7Q2XM-RUH-HQ") and not is_item_id("K7Q2XM-I000142")


def test_the_item_ids_of_formats_0_6_and_0_7_are_known_as_such():
    assert is_legacy_item_id("K7Q2XM-I000142") and not is_legacy_item_id("7K2Q-XM9F-4DP")
    for bad in ("K7Q2XM-I00014٢", "K7Q2XM-I000142\n", "K7Q2XM-I0001420", "k7q2xm-I000142", "-I000142"):
        assert not is_legacy_item_id(bad), bad
    assert not is_legacy_item_id("K7Q2XM-I" + "0" * 8_000_000)
    # an item of a package of then, opened, is given an asset's ID made from its ID then:
    # the same every time, and another for every other
    tags = {item_id_for_legacy(f"K7Q2XM-I{n:06d}") for n in range(1, 2001)}
    assert len(tags) == 2000 and all(map(is_item_id, tags))
    assert item_id_for_legacy("K7Q2XM-I000142") == item_id_for_legacy("K7Q2XM-I000142") != \
        item_id_for_legacy("K7Q2XN-I000142")
