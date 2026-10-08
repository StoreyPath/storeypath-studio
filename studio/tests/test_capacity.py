"""How many people a space or zone is meant to seat (format 0.7): set in review, else
the workplaces of the desks standing in it; and who it is laid out for, the highest
grade of its desks. Wayfinder takes them as defaults it may override."""

import json
import zipfile

import pytest
from shapely.geometry import shape

from storeypath import catalogue
from storeypath.export import export_package
from storeypath.review import Review
from storeypath.validate import validate_package


def _office(ws, f_id):
    """The largest space of the floor, and a point in it."""
    room = max((r for r in ws.floor_objects(f_id) if r.kind == "space"), key=lambda r: shape(r.geometry).area)
    p = shape(room.geometry).representative_point()
    return room, p.x, p.y


def _spaces(path):
    with zipfile.ZipFile(path) as z:
        return {f["id"]: f["properties"] for f in json.loads(z.read("spaces.geojson"))["features"]}


def test_desks_say_how_many_a_room_seats_and_who_it_is_for(converted, tmp_path):
    ws, d, f_id, *_ = converted
    room, x, y = _office(ws, f_id)
    ws.add_item("DESK-JUNIOR", f_id, x, y)
    ws.add_item("DESK-DIRECTOR", f_id, x + 0.5, y)
    ws.add_item("SOFA", f_id, x - 0.5, y)  # no workplace
    export_package(ws, tmp_path / "p.storeypath", bake=False)
    assert validate_package(tmp_path / "p.storeypath") == []
    spaces = _spaces(tmp_path / "p.storeypath")
    assert (spaces[room.id]["capacity"], spaces[room.id]["capacity_from"], spaces[room.id]["grade"]) == (2, "items", "director")
    empty = next(i for i, p in spaces.items() if i != room.id)
    assert (spaces[empty]["capacity"], spaces[empty]["capacity_from"], spaces[empty]["grade"]) == (None, None, None)


def test_a_capacity_set_in_review_wins_over_the_desks(converted, tmp_path):
    ws, d, f_id, *_ = converted
    path = d / "project.spproj"
    room, x, y = _office(ws, f_id)
    ws.add_item("DESK-SENIOR", f_id, x, y)
    ws.save(path)
    r = Review(path)
    seen = r.correct(room.id, {"capacity": 4})
    assert (seen["capacity"], seen["capacity_from"], seen["capacity_set"], seen["workplaces"], seen["grade"]) == \
        (4, "review", 4, 1, "senior")
    named = r.correct(room.id, {"correction": {"name": "Team room"}})  # a correction keeps the capacity
    assert named["name"] == "Team room" and named["capacity"] == 4
    assert r.correct(room.id, {"reset": True})["capacity"] == 4  # so does removing it
    from storeypath.workspace import Workspace

    export_package(Workspace.load(path), tmp_path / "p.storeypath", bake=False)
    assert _spaces(tmp_path / "p.storeypath")[room.id]["capacity"] == 4
    back = r.correct(room.id, {"capacity": None})  # as its desks say again
    assert (back["capacity"], back["capacity_from"]) == (1, "items")
    for bad in (-1, 2.5, "3", True):
        with pytest.raises(ValueError, match="capacity"):
            r.correct(room.id, {"capacity": bad})
    assert r.correct(room.id, {"capacity": 0})["capacity"] == 0  # seats nobody: a room to meet in


def test_a_divided_space_counts_its_zones_desks(converted, tmp_path):
    from storeypath.convert import convert_floor

    ws, d, f_id, *_ = converted
    room, x, y = _office(ws, f_id)
    x0, y0, x1, y1 = shape(room.geometry).bounds
    mid = (x0 + x1) / 2
    ws.floor(f_id).edits.dividers.append([[mid, y0 - 0.1], [mid, y1 + 0.1]])
    convert_floor(ws, f_id, d)  # the room becomes one of two zones (its ID kept) of a new space
    zones = [r for r in ws.floor_objects(f_id) if r.kind == "zone"]
    assert len(zones) == 2 and room.id in {z.id for z in zones} and zones[0].parent == zones[1].parent
    container = zones[0].parent
    for z in zones:
        p = shape(z.geometry).representative_point()
        ws.add_item("DESK-JUNIOR", f_id, p.x, p.y)
    export_package(ws, tmp_path / "p.storeypath", bake=False)
    with zipfile.ZipFile(tmp_path / "p.storeypath") as zf:
        got = {f["id"]: f["properties"]["capacity"] for f in json.loads(zf.read("zones.geojson"))["features"]}
    assert [got[z.id] for z in zones] == [1, 1] and _spaces(tmp_path / "p.storeypath")[container]["capacity"] == 2


def test_where_an_item_stands_is_found_through_the_tree_as_by_every_room(converted):
    """floor_units' tree (Units.at) finds the same space and zone as asking every room in
    turn: on rooms, zones, walls and outside, edges and corners included."""
    from types import SimpleNamespace

    from storeypath.convert import convert_floor
    from storeypath.export import Units, floor_units, standing_in

    ws, d, f_id, *_ = converted
    room, x, y = _office(ws, f_id)
    x0, y0, x1, y1 = shape(room.geometry).bounds
    ws.floor(f_id).edits.dividers.append([[(x0 + x1) / 2, y0 - 0.1], [(x0 + x1) / 2, y1 + 0.1]])
    convert_floor(ws, f_id, d)  # a space of two zones among the others
    units = floor_units(ws, f_id)
    assert isinstance(units, Units) and any(rec.kind == "zone" for _, rec in units)
    fx0, fy0, fx1, fy1 = shape(ws.floor(f_id).outline).bounds if ws.floor(f_id).outline else (0, 0, 60, 30)
    points = [(fx0 - 1 + (fx1 - fx0 + 2) * i / 37, fy0 - 1 + (fy1 - fy0 + 2) * j / 23) for i in range(38) for j in range(24)]
    for geom, _ in units:  # on their corners, too
        points += list(geom.exterior.coords) if geom.geom_type == "Polygon" else []
    found = 0
    for px, py in points:
        it = SimpleNamespace(x=px, y=py)
        assert standing_in(it, units) == standing_in(it, list(units)), (px, py)
        found += standing_in(it, units)[0] is not None
    assert found > len(points) // 3


def test_an_older_catalogue_learns_what_its_desks_seat(tmp_path):
    older = catalogue.default_catalogue().model_dump()
    for t in older["types"]:
        t.pop("workplaces"), t.pop("grade")
    older["types"].append({"code": "BENCH-4", "name_en": "Bench for four"})  # its own: left as it says
    (tmp_path / catalogue.FILE_NAME).write_text(json.dumps(older))
    cat = catalogue.load(tmp_path)
    assert (cat.get("DESK-PRESIDENT").workplaces, cat.get("DESK-PRESIDENT").grade) == (1, "president")
    assert (cat.get("SOFA").workplaces, cat.get("SOFA").grade) == (0, None)
    assert (cat.get("BENCH-4").workplaces, cat.get("BENCH-4").grade) == (0, None)
    assert "workplaces" in json.loads((tmp_path / catalogue.FILE_NAME).read_text())["types"][0]  # written back
