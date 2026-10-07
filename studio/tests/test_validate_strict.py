"""The validator takes a package as the format says it is, and nothing looser: what
it lets through, every reader must be able to read (the Go module's reader among
them). Each case is a conformance package with one thing wrong."""

import json
import zipfile

import pytest

from storeypath.assets import asset_dir
from storeypath.ids import MAX_ID_LENGTH, is_item_id, make_id, parse_id
from storeypath.validate import validate_package

PACKAGES = asset_dir("spec") / "conformance" / "packages"
HQ = PACKAGES / "campus-hq.storeypath"  # format 0.7: one building, its items placed in it


def rewritten(tmp_path, source=HQ, **files):
    """A copy of a package with some of its files changed (name → a function of its
    text), added (name → a function of None) or left out (name → None)."""
    out = tmp_path / f"case-{len(list(tmp_path.glob('case-*')))}.storeypath"
    with zipfile.ZipFile(source) as z, zipfile.ZipFile(out, "w") as w:
        names = z.namelist()
        for n in names + [n for n in files if n not in names]:
            if n in files and files[n] is None:
                continue
            data = z.read(n) if n in names else None
            if n in files:
                data = files[n](data.decode("utf-8") if data is not None else None)
            w.writestr(n, data)
    return out


def edited(fn):
    """A change to a JSON file, made in place on its document by ``fn``."""
    def apply(text):
        doc = json.loads(text)
        fn(doc)
        return json.dumps(doc, ensure_ascii=False)
    return apply


def first(role="spaces.geojson"):
    with zipfile.ZipFile(HQ) as z:
        return json.loads(z.read(role))["features"][0]


# IDs


def test_ids_are_ascii_and_the_whole_string():
    for bad in ("K7Q2XM-RUH\n", "K7Q2XM-RUH-HQ-F02-٠١٤٢", "K7Q2XM-RUH-HQ-F02-0142\n", "k7q2xm"):
        with pytest.raises(ValueError):
            parse_id(bad)
    with pytest.raises(ValueError):
        make_id("K7Q2XM", "HQ\n")
    assert is_item_id("K7Q2XM-I000142")
    for bad in ("K7Q2XM-I00014٢", "K7Q2XM-I000142\n", "K7Q2XM-I0001420", "k7q2xm-I000142", "-I000142"):
        assert not is_item_id(bad), bad


def test_an_id_longer_than_any_id_is_refused_before_it_is_taken_apart():
    longest = "-".join(["A" * 16] * 5)
    assert len(longest) == MAX_ID_LENGTH and parse_id(longest).level == "object"
    huge = "A-" * 8_000_000
    with pytest.raises(ValueError, match="longer than an ID can be"):
        parse_id(huge)
    assert not is_item_id("K7Q2XM-I" + "0" * 8_000_000)


def test_an_item_id_with_other_digits_is_refused(tmp_path):
    item = first("items.geojson")["id"]
    other = item[:-1] + "١"  # ARABIC-INDIC DIGIT ONE

    out = rewritten(tmp_path, **{"items.geojson": lambda t: t.replace(item, other),
                                 "objects.csv": lambda t: t.replace(item, other),
                                 "changes.json": lambda t: t.replace(item, other)})
    errors = validate_package(out)
    assert any(other in e and "not an item ID" in e for e in errors), errors
