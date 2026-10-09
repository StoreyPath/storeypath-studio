"""Finishes (format 0.9): StoreyPath's fixed set of floor and wall finishes (spec/finishes.json,
finishes.py), a room's choice of them in review (Override.floor_finish, wall_finish:
migration 0004), saved, undone and redone like every correction, one at a time or every
room of a type at once (one change), by who may edit the floor, while they hold its lock;
exported with each space and zone, and read back."""

import json
import re
import zipfile

import psycopg
import pytest

from people import Team
from storeypath import accounts as acc
from storeypath import finishes
from storeypath.assets import asset_dir
from storeypath.types import SpaceType


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def override(team, object_id):
    o = team.studio.workspace(team.code).overrides.get(object_id)
    return o.model_dump(exclude_none=True) if o is not None else None


def zone_of(team):
    """A zone: the first floor of the Headquarters' longest space divided in two by a line
    drawn across it (the floor read again)."""
    from shapely.geometry import shape

    ws = team.studio.workspace(team.code)
    zones = [r for r in ws.floor_objects(team.hq1) if r.kind == "zone"]
    if not zones:
        space = max((r for r in ws.floor_objects(team.hq1) if r.kind == "space"), key=lambda r: shape(r.geometry).area)
        x0, y0, x1, y1 = shape(space.geometry).bounds
        mid = (x0 + x1) / 2
        status, job = team("khalid", "POST", f"floors/{team.hq1}/edits",
                           {"add": {"divider": [[mid, y0 - 0.1], [mid, y1 + 0.1]]}})
        assert status == 200, job
        team.finished("khalid", job)
        team("khalid", "POST", f"floors/{team.hq1}/release", {})
        zones = [r for r in team.studio.workspace(team.code).floor_objects(team.hq1) if r.kind == "zone"]
    assert zones
    return sorted(zones, key=lambda r: r.id)[0]


def rooms(team, floor, type_):
    ws = team.studio.workspace(team.code)
    return sorted((r for r in ws.floor_objects(floor) if r.kind in ("space", "zone") and not r.zones
                   and ws.effective(r)["type"] == type_), key=lambda r: r.id)


# ---- the list ----------------------------------------------------------------------

def test_the_list_is_whole_and_its_copies_are_it():
    cat = finishes.catalogue()
    groups = {g["code"]: g["applies"] for g in cat["groups"]}
    assert len(groups) == len(cat["groups"]) and all(g["name"] and g["name_ar"] for g in cat["groups"])
    codes = [f["code"] for f in cat["finishes"]]
    assert len(codes) == len(set(codes)) and 40 <= len(codes) <= 60
    counts = {"floor": 0, "wall": 0}
    for f in cat["finishes"]:
        counts[f["applies"]] += 1
        assert finishes.well_formed(f["code"], f["applies"]) and f["code"].startswith(f["applies"].upper() + "-"), f
        assert groups[f["group"]] == f["applies"], f
        assert f["name"] and f["name_ar"] and re.fullmatch(r"#[0-9a-f]{6}", f["tone"]), f
        assert 0 < f["roughness"] <= 1 and f["size_m"] > 0 and f["paint"]["kind"], f
    assert counts["floor"] >= 25 and counts["wall"] >= 15
    for applies in finishes.APPLIES:  # every type has a default of its own kind
        for t in SpaceType:
            assert finishes.finish(cat["defaults"][applies][t.value])["applies"] == applies, (applies, t)
    assert finishes.finish(cat["exterior"])["applies"] == "wall"
    assert finishes.default("floor", "a type of later") == finishes.default("floor", "unspecified")
    # the viewers' and the Go module's copies (spec/finishes.mjs makes them)
    spec = asset_dir("spec") / "finishes.json"
    assert (spec.parent.parent / "go" / "finishes.json").read_bytes() == spec.read_bytes()
    module = (asset_dir("viewer") / "src" / "finishes.js").read_text(encoding="utf-8")
    copied = re.search(r"export const FINISHES = (\{.*?\n\});\n", module, re.S)
    assert copied and json.loads(copied.group(1)) == cat


def test_a_code_is_checked_for_what_it_finishes():
    assert finishes.check("FLOOR-CARPET-NAVY", "floor") == "FLOOR-CARPET-NAVY"
    assert finishes.check(None, "wall") is None
    for code, applies in (("WALL-PAINT-NAVY", "floor"), ("FLOOR-CARPET-NAVY", "wall"), ("FLOOR-NOT-ONE", "floor"),
                          ("floor-carpet-navy", "floor"), (7, "floor"), ("", "wall")):
        with pytest.raises(ValueError):
            finishes.check(code, applies)
    assert finishes.known("FLOOR-NOT-ONE", "floor") is None and finishes.known("WALL-STONE", "wall") == "WALL-STONE"


# ---- in review ---------------------------------------------------------------------

def test_a_rooms_finishes_are_saved_undone_and_redone(team):
    s = rooms(team, team.hq0, "office")[0]
    status, room = team("khalid", "POST", f"objects/{s.id}", {"floor_finish": "FLOOR-CARPET-NAVY"})
    assert status == 200 and room["floor_finish"] == "FLOOR-CARPET-NAVY" and room["wall_finish"] is None
    status, room = team("khalid", "POST", f"objects/{s.id}", {"wall_finish": "WALL-PAPER-LINEN"})
    assert status == 200 and room["floor_finish"] == "FLOOR-CARPET-NAVY" and room["wall_finish"] == "WALL-PAPER-LINEN"
    assert override(team, s.id) == {"floor_finish": "FLOOR-CARPET-NAVY", "wall_finish": "WALL-PAPER-LINEN"}
    # the floor as Review reads it
    floor = team("khalid", "GET", f"floors/{team.hq0}")[1]
    listed = next(x for x in floor["spaces"] if x["id"] == s.id)
    assert (listed["floor_finish"], listed["wall_finish"]) == ("FLOOR-CARPET-NAVY", "WALL-PAPER-LINEN")
    # a finish alone is no correction: the room's review stays as it was
    assert listed["correction"] is None
    # back to its type's
    assert team("khalid", "POST", f"objects/{s.id}", {"floor_finish": None})[1]["floor_finish"] is None
    label = f"{s.name} {s.number}"
    rows = team.studio.store.history(team.code, team.hq0, limit=3)
    assert [r["kind"] for r in rows] == ["finish"] * 3
    from storeypath import history
    assert [history.describe(r) for r in rows] == [f"set the floor of {label} back to its type's",
                                                   f"set the walls of {label} to Wallpaper, linen beige",
                                                   f"set the floor of {label} to Carpet tiles, navy"]
    status, done = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 200 and done["line"] == f"set the floor of {label} back to its type's"
    assert override(team, s.id)["floor_finish"] == "FLOOR-CARPET-NAVY"
    team("khalid", "POST", "undo", {"floor": team.hq0})
    assert override(team, s.id) == {"floor_finish": "FLOOR-CARPET-NAVY"}
    team("khalid", "POST", "redo", {"floor": team.hq0})
    assert override(team, s.id)["wall_finish"] == "WALL-PAPER-LINEN"


def test_a_finish_keeps_a_room_checked_and_a_check_keeps_its_finishes(team):
    s = rooms(team, team.hq0, "office")[0]
    assert team("khalid", "POST", f"objects/{s.id}", {"correction": {}})[0] == 200  # accepted as it is
    room = team("khalid", "POST", f"objects/{s.id}", {"floor_finish": "FLOOR-WOOD-OAK"})[1]
    assert room["correction"] == {} and room["reasons"] == []
    room = team("khalid", "POST", f"objects/{s.id}", {"correction": {"name": "Archive"}})[1]
    assert room["floor_finish"] == "FLOOR-WOOD-OAK" and room["correction"] == {"name": "Archive"}
    room = team("khalid", "POST", f"objects/{s.id}", {"reset": True})[1]
    assert room["floor_finish"] == "FLOOR-WOOD-OAK" and room["correction"] is None  # the finishes stay


def test_what_is_not_a_finish_for_it_is_refused(team):
    s = rooms(team, team.hq0, "office")[0]
    for body in ({"floor_finish": "WALL-PAINT-WHITE"}, {"wall_finish": "FLOOR-CARPET-NAVY"},
                 {"floor_finish": "FLOOR-NOT-ONE"}, {"floor_finish": 3}):
        status, said = team("khalid", "POST", f"objects/{s.id}", body)
        assert status == 400 and "finish" in said["error"], (body, said)
    zone = zone_of(team)
    status, said = team("khalid", "POST", f"objects/{zone.id}", {"wall_finish": "WALL-PAINT-WHITE"})
    assert status == 400 and "zone has no walls" in said["error"]
    assert team("khalid", "POST", f"objects/{zone.id}", {"floor_finish": "FLOOR-VINYL-GREY"})[0] == 200
    door = next(r for r in team.studio.workspace(team.code).floor_objects(team.hq0) if r.kind == "opening")
    assert team("khalid", "POST", f"objects/{door.id}", {"floor_finish": "FLOOR-VINYL-GREY"})[0] == 400
    assert override(team, s.id) is None


def test_every_room_of_a_type_is_finished_at_once_and_undone_as_one(team):
    offices = rooms(team, team.hq0, "office")
    assert len(offices) >= 3
    first = offices[0].id
    team("khalid", "POST", f"objects/{first}", {"correction": {"name": "Mine"}})  # (its correction stays)
    status, done = team("khalid", "POST", f"floors/{team.hq0}/finishes",
                        {"type": "office", "floor_finish": "FLOOR-CARPET-NAVY", "wall_finish": "WALL-PAINT-NAVY"})
    assert status == 200 and sorted(done["changed"]) == [r.id for r in offices], done["changed"]
    assert all(x["floor_finish"] == "FLOOR-CARPET-NAVY" and x["wall_finish"] == "WALL-PAINT-NAVY" for x in done["spaces"])
    assert override(team, first) == {"name": "Mine", "floor_finish": "FLOOR-CARPET-NAVY", "wall_finish": "WALL-PAINT-NAVY"}
    row = team.studio.store.history(team.code, team.hq0, limit=1)[0]
    from storeypath import history
    assert row["kind"] == "finish" and sorted(row["targets"]) == [r.id for r in offices]
    assert history.describe(row) == (f"set the floor of {len(offices)} offices to Carpet tiles, navy "
                                     "and its walls to Paint, accent navy")
    # nothing more to change: no change recorded
    again = team("khalid", "POST", f"floors/{team.hq0}/finishes", {"type": "office", "floor_finish": "FLOOR-CARPET-NAVY"})
    assert again[0] == 200 and again[1]["changed"] == []
    assert team.studio.store.history(team.code, team.hq0, limit=1)[0]["seq"] == row["seq"]
    # undone as one, redone as one
    status, undone = team("khalid", "POST", "undo", {"floor": team.hq0})
    assert status == 200 and undone["seq"] == row["seq"]
    assert override(team, first) == {"name": "Mine"} and all(override(team, r.id) is None for r in offices[1:])
    team("khalid", "POST", "redo", {"floor": team.hq0})
    assert all(override(team, r.id)["wall_finish"] == "WALL-PAINT-NAVY" for r in offices)
    # named rooms: a zone's walls are its space's (left alone)
    zone = zone_of(team)
    zf = zone.id.rsplit("-", 1)[0]
    status, named = team("khalid", "POST", f"floors/{zf}/finishes", {"ids": [zone.id], "floor_finish": "FLOOR-RUBBER",
                                                                     "wall_finish": "WALL-STONE"})
    assert status == 200 and override(team, zone.id) == {"floor_finish": "FLOOR-RUBBER"}
    assert team("khalid", "POST", f"floors/{team.hq0}/finishes", {"ids": [zone.id], "floor_finish": None})[0] == 404
    assert team("khalid", "POST", f"floors/{team.hq0}/finishes", {"type": "office"})[0] == 400
    assert team("khalid", "POST", f"floors/{team.hq0}/finishes", {"floor_finish": "FLOOR-RUBBER"})[0] == 400


def test_finishes_are_changed_by_who_may_edit_the_floor_while_they_hold_it(team):
    s = rooms(team, team.hq0, "office")[0]
    body = {"floor_finish": "FLOOR-CARPET-GREEN"}
    assert team("vera", "POST", f"objects/{s.id}", body)[0] == 403  # view only
    assert team("vera", "POST", f"floors/{team.hq0}/finishes", {"type": "office", **body})[0] == 403
    assert team("bob", "POST", f"floors/{team.hq0}/finishes", {"type": "office", **body})[0] in (403, 404)
    assert team("sara", "POST", f"objects/{s.id}", body)[0] == 200  # sara takes the floor's lock
    status, refused = team("khalid", "POST", f"objects/{s.id}", {"floor_finish": "FLOOR-WOOD-OAK"})
    assert status == 423 and refused["locked"]["who"]["name"] == "Sara Ahmed"
    assert team("khalid", "POST", f"floors/{team.hq0}/finishes", {"type": "office", **body})[0] == 423
    assert override(team, s.id) == body
    # khalid may not undo over sara's change of the room
    team("sara", "POST", f"floors/{team.hq0}/release", {})
    assert team("khalid", "POST", f"objects/{s.id}", {"wall_finish": "WALL-STONE"})[0] == 200


def test_the_database_keeps_only_codes_of_the_form(team):
    s = rooms(team, team.hq0, "office")[0]
    team("khalid", "POST", f"objects/{s.id}", {"floor_finish": "FLOOR-CARPET-NAVY"})
    with team.studio.store.db.connection() as conn:
        for column, value in (("floor_finish", "WALL-PAINT-WHITE"), ("wall_finish", "paint"), ("floor_finish", "FLOOR-")):
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(f"UPDATE storeypath.overrides SET {column} = %s WHERE object = %s", (value, s.id))


# ---- in packages -------------------------------------------------------------------

def test_finishes_are_exported_and_valid(team, tmp_path):
    from storeypath.export import export_package
    from storeypath.validate import validate_package

    office = rooms(team, team.hq0, "office")[0]
    zone = zone_of(team)
    team("khalid", "POST", f"objects/{office.id}", {"floor_finish": "FLOOR-MARBLE-BLACK", "wall_finish": "WALL-WOOD-WALNUT"})
    team("khalid", "POST", f"objects/{zone.id}", {"floor_finish": "FLOOR-TERRAZZO-DARK"})
    ws = team.studio.workspace(team.code)
    out = tmp_path / "hq.storeypath"
    export_package(ws, out, building=team.hq, bake=False)
    assert validate_package(out) == []
    with zipfile.ZipFile(out) as z:
        spaces = {f["id"]: f["properties"] for f in json.loads(z.read("spaces.geojson"))["features"]}
        zones = {f["id"]: f["properties"] for f in json.loads(z.read("zones.geojson"))["features"]}
        assert json.loads(z.read("manifest.json"))["format_version"] == "0.9.0"
    assert (spaces[office.id]["floor_finish"], spaces[office.id]["wall_finish"]) == ("FLOOR-MARBLE-BLACK",
                                                                                    "WALL-WOOD-WALNUT")
    assert zones[zone.id]["floor_finish"] == "FLOOR-TERRAZZO-DARK" and "wall_finish" not in zones[zone.id]
    others = [p for i, p in spaces.items() if i != office.id]
    assert all(p["floor_finish"] is None and p["wall_finish"] is None for p in others)


def test_a_package_with_a_finish_not_of_the_form_is_refused(tmp_path):
    from storeypath.validate import validate_package

    source = asset_dir("spec") / "conformance" / "packages" / "campus-hq.storeypath"

    def rewritten(change):
        out = tmp_path / "x.storeypath"
        with zipfile.ZipFile(source) as z, zipfile.ZipFile(out, "w") as w:
            for n in z.namelist():
                data = z.read(n)
                if n == "spaces.geojson":
                    doc = json.loads(data)
                    change(doc["features"][0]["properties"])
                    data = json.dumps(doc).encode()
                w.writestr(n, data)
        return validate_package(out)

    assert rewritten(lambda p: p.update(floor_finish="FLOOR-LATER-ONE")) == []  # a later version's: read as its type's
    assert rewritten(lambda p: p.update(floor_finish="WALL-PAINT-WHITE"))
    assert rewritten(lambda p: p.update(wall_finish="carpet"))
    assert rewritten(lambda p: p.update(wall_finish="WALL-" + "X" * 40))


def test_the_conformance_packages_have_finishes():
    with zipfile.ZipFile(asset_dir("spec") / "conformance" / "packages" / "campus-hq.storeypath") as z:
        spaces = [f["properties"] for f in json.loads(z.read("spaces.geojson"))["features"]]
        zones = [f["properties"] for f in json.loads(z.read("zones.geojson"))["features"]]
    got = {p["type"]: (p["floor_finish"], p["wall_finish"]) for p in spaces if p["floor_finish"] or p["wall_finish"]}
    assert got["lobby"] == ("FLOOR-MARBLE-WHITE", "WALL-STONE") and got["restroom"] == ("FLOOR-PORCELAIN-DARK", "WALL-TILE-MOSAIC")
    assert len(got) == 5 and sorted(p["floor_finish"] for p in zones if p["floor_finish"]) == ["FLOOR-TERRAZZO-LIGHT",
                                                                                              "FLOOR-VINYL-GREY"]
