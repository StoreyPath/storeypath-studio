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


# Format versions


def test_a_format_version_is_read_strictly():
    from storeypath.package import FORMAT_VERSION, version_problem, version_tuple

    assert version_tuple("0.7.0") == (0, 7) and version_tuple("0.7") == (0, 7)
    assert version_tuple("0.7.1-rc.1") == (0, 7) and version_tuple("0.7.0+build.5") == (0, 7)
    assert version_problem(FORMAT_VERSION) is None and version_problem("0.7.1-rc.1") is None
    for newer in ("0.8.0-rc1", "0.8-rc1", "0.8", "0.10.0"):  # 0.8-rc1 was read as 0.0, and taken
        assert "newer" in version_problem(newer), newer
    for bad in ("", " 0.7.0", "0.7.0 ", "0.7.0\n", "1_0.7", "0.8a.0", "0.٧.0", "0", "0.7.0.1", "v0.7.0"):
        with pytest.raises(ValueError):
            version_tuple(bad)
        assert "is not a version" in version_problem(bad), bad


@pytest.mark.parametrize("version, why", [("0.8a.0", "is not a version"), (" 8", "is not a version"),
                                          ("", "is not a version"), ("0.8-rc1", "newer than this Studio")])
def test_a_package_whose_version_is_not_one_it_reads_is_refused(tmp_path, version, why):
    out = rewritten(tmp_path, **{"manifest.json": edited(lambda m: m.update(format_version=version))})
    errors = validate_package(out)
    assert len(errors) == 1 and why in errors[0], errors


# Numbers are numbers, true is true: as the schemas say, and as a strict reader reads them


def _props(i, **values):
    def change(fc):
        fc["features"][i]["properties"].update(values)
    return edited(change)


@pytest.mark.parametrize("file, i, values, field", [
    ("spaces.geojson", 0, {"hidden": "false"}, "hidden"),
    ("spaces.geojson", 0, {"ignored": 0}, "ignored"),
    ("spaces.geojson", 0, {"area_m2": "111.44"}, "area_m2"),
    ("spaces.geojson", 1, {"capacity": "4", "capacity_from": "review"}, "capacity"),
    ("spaces.geojson", 1, {"capacity": 4.0, "capacity_from": "review"}, "capacity"),
    ("floors.geojson", 0, {"ordinal": 0.0}, "ordinal"),
    ("floors.geojson", 0, {"elevation": "0"}, "elevation"),
    ("floors.geojson", 0, {"ordinal": True}, "ordinal"),
    ("openings.geojson", 0, {"exterior": "true"}, "exterior"),
    ("items.geojson", 0, {"heading": "200"}, "heading"),
])
def test_a_value_of_another_json_type_is_refused(tmp_path, file, i, values, field):
    errors = validate_package(rewritten(tmp_path, **{file: _props(i, **values)}))
    assert any(e.startswith(f"{file}:") and f"properties.{field}" in e for e in errors), errors


def test_a_sequence_or_count_of_another_json_type_is_refused(tmp_path):
    out = rewritten(tmp_path, **{"changes.json": edited(lambda c: c.update(sequence="1"))})
    assert any(e.startswith("changes.json:") and "sequence" in e for e in validate_package(out))
    out = rewritten(tmp_path, **{"manifest.json": edited(lambda m: m["counts"].update(spaces=72.0))})
    assert any(e.startswith("manifest.json:") and "counts" in e for e in validate_package(out))


def test_whole_numbers_where_the_format_has_decimals_are_read(tmp_path):
    def whole(fc):
        for f in fc["features"]:
            q = f["properties"]
            for k in ("width_m", "depth_m", "height_m", "elevation_m"):
                if isinstance(q[k], float) and q[k].is_integer():
                    q[k] = int(q[k])
            q["local"] = {k: int(v) if v.is_integer() else v for k, v in q["local"].items()}

    out = rewritten(tmp_path, **{"items.geojson": edited(whole)})
    with zipfile.ZipFile(out) as z:
        assert '"width_m": 2,' in z.read("items.geojson").decode()
    assert validate_package(out) == []


def _local(i=0, **values):
    def change(fc):
        fc["features"][i]["properties"]["local"].update(values)
    return edited(change)


@pytest.mark.parametrize("change", [
    {"items.geojson": _local(x_m=float("nan"))},
    {"items.geojson": _local(rotation_deg=float("inf"))},
    {"items.geojson": _props(0, display_point=[float("nan"), float("nan")])},
    {"items.geojson": lambda t: t.replace('"heading": ', '"heading": 1e999, "was": ', 1)},
    {"spaces.geojson": _props(0, area_m2=float("-inf"))},
    {"spaces.geojson": _props(0, later_property=float("nan"))},  # one it does not know: still not JSON
    {"floors.geojson": _props(0, ordinal=2**70)},
    {"catalogue.json": edited(lambda c: c["types"][0].update(width=float("nan")))},
    {"manifest.json": edited(lambda m: next(iter(m["placements"].values())).update(bearing=float("nan")))},
])
def test_a_number_that_is_not_finite_is_refused(tmp_path, change):
    (file,) = change
    errors = validate_package(rewritten(tmp_path, **change))
    assert errors and errors[0].startswith(f"{file}: not JSON:") and "number" in errors[0], errors


def test_the_models_refuse_numbers_that_are_not_finite():
    from pydantic import ValidationError

    from storeypath.package import ItemLocal, PlacementInfo

    with pytest.raises(ValidationError, match="finite"):
        ItemLocal(x_m=float("nan"), y_m=0, rotation_deg=0)
    with pytest.raises(ValidationError, match="finite"):
        PlacementInfo(lon=0, lat=0, x=0, y=0, bearing=float("inf"))
