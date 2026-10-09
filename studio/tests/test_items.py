"""Furniture and equipment on floors (items) and the catalogue of their types."""

import pytest

from storeypath import catalogue
from storeypath.ids import is_item_id
from storeypath.workspace import Workspace


def test_the_catalogue_starts_with_desks_by_grade_copiers_access_points_sofas_tvs_beds_and_kiosks(tmp_path):
    cat = catalogue.load(tmp_path)  # written the first time
    codes = [t.code for t in cat.types]
    assert {"DESK-PRESIDENT", "DESK-MANAGER", "DESK-JUNIOR", "COPIER", "ACCESS-POINT", "SOFA", "TV",
            "BED-KING", "BED-QUEEN", "KIOSK"} <= set(codes)
    ap = cat.get("ACCESS-POINT")
    assert ap.mount == "ceiling" and {f.key: f.owner for f in ap.fields}["ssid"] == "system"  # the network: wayfinder's
    assert {f.key: f.owner for f in ap.fields}["color"] == "storeypath"
    kiosk = cat.get("KIOSK")  # where people look up their office, and later start the way to it
    assert (kiosk.category, kiosk.mount, kiosk.workplaces, kiosk.grade) == ("equipment", "floor", 0, None)
    assert all(t.name_ar for t in cat.types)
    assert catalogue.load(tmp_path) == cat  # read back as written


def test_a_catalogue_written_by_an_older_studio_gains_the_new_types_and_keeps_its_own(tmp_path):
    # a Studio's catalogue from before beds and kiosks, its sofa made wider and a type of its own
    older = catalogue.default_catalogue()
    older.types = [t for t in older.types if not t.code.startswith(("BED-", "KIOSK"))]
    older.get("SOFA").width = 2.4
    older.types.append(catalogue.ItemType(code="PLANT", name_en="Plant", name_ar="نبتة"))
    older.get("TV").retired = True
    catalogue.save(tmp_path, older)
    cat = catalogue.load(tmp_path)
    codes = [t.code for t in cat.types]
    assert codes[-3:] == ["BED-KING", "BED-QUEEN", "KIOSK"] and "PLANT" in codes
    assert cat.get("SOFA").width == 2.4 and cat.get("TV").retired  # its own changes kept
    assert catalogue.load(tmp_path) == cat  # and written: the next load adds nothing


def test_a_type_code_and_a_field_key_are_checked():
    with pytest.raises(ValueError):
        catalogue.ItemType(code="desk manager", name_en="x")
    with pytest.raises(ValueError):
        catalogue.ItemField(key="Network Name", name_en="x")
    cat = catalogue.Catalogue(types=[catalogue.ItemType(code="A", name_en="a"), catalogue.ItemType(code="A", name_en="b")])
    with pytest.raises(ValueError, match="share a code"):
        cat.check()


def test_an_item_keeps_its_id_wherever_it_goes(converted):
    # An item's ID is an asset's tag, of no project or place: carried to another floor,
    # it keeps it; taken away, it is retired and its ID never issued again.
    ws, d, f_id, b_id, *_ = converted
    first = ws.add_item("DESK-MANAGER", f_id, 3.0, 4.0, rotation=450)
    second = ws.add_item("COPIER", f_id, 6.0, 1.0)
    assert is_item_id(first.id) and is_item_id(second.id) and first.id != second.id
    assert ws.id not in first.id and first.rotation == 90
    assert [i.id for i in ws.floor_items(f_id)] == [first.id, second.id]  # in the order they were placed
    second.status = "retired"
    third = ws.add_item("SOFA", f_id, 1.0, 1.0)
    assert is_item_id(third.id) and [i.id for i in ws.floor_items(f_id)] == [first.id, third.id]
    path = d / "p.spproj"
    ws.save(path)
    again = Workspace.load(path)
    assert again.items[first.id] == first and "next_item_seq" not in path.read_text()


def test_a_new_item_is_given_no_id_the_project_or_another_has(converted, monkeypatch):
    # drawn again while an item of the project has it, or ``taken`` says one elsewhere has
    from storeypath import ids

    ws, d, f_id, *_ = converted
    first = ws.add_item("DESK-MANAGER", f_id, 3.0, 4.0)
    elsewhere, free = "7K2Q-XM9F-4DP", "ZZZZ-ZZZZ-ZZA"
    # what is drawn: the project's item's ID, then the one taken elsewhere, then one free
    symbols = iter("".join(i.replace("-", "")[:10] for i in (first.id, elsewhere, free)))
    monkeypatch.setattr(ids.secrets, "choice", lambda alphabet: next(symbols))
    second = ws.add_item("COPIER", f_id, 6.0, 1.0, taken=lambda i: i == elsewhere)
    assert second.id == free and next(symbols, None) is None


def test_a_building_code_cannot_make_an_item_id():
    # a project of four symbols (not one Studio makes): a building ID 4-4-3 with its check
    # right would read as an item's
    from storeypath.workspace import Project

    ws = Workspace(project=Project(code="7K2Q", name="P"))
    loc = ws.add_location("XM9F", "Somewhere")
    with pytest.raises(ValueError, match="item's ID"):
        ws.add_building(loc, "4DP", "Here")
    assert ws.add_building(loc, "4DK", "There") == "7K2Q-XM9F-4DK"
    assert ws.add_location("I000001", "Anywhere") == "7K2Q-I000001"  # (an item's form before format 0.8)


def test_items_in_a_package_and_what_changed_of_them(converted, tmp_path):
    import csv
    import io
    import json
    import zipfile

    from shapely.geometry import shape

    from storeypath.export import export_package
    from storeypath.validate import validate_package

    ws, d, f_id, b_id, *_ = converted
    office = next(r for r in ws.floor_objects(f_id) if r.kind == "space" and r.type == "office")
    mx, my = shape(office.geometry).representative_point().coords[0]
    desk = ws.add_item("DESK-MANAGER", f_id, mx, my, rotation=90, values={"anything": "not its type's"})
    ap = ws.add_item("ACCESS-POINT", f_id, mx + 0.3, my, values={"color": "#ff0000"})

    def package(name):
        export_package(ws, tmp_path / name, bake=False)
        assert validate_package(tmp_path / name) == []
        with zipfile.ZipFile(tmp_path / name) as z:
            return {n: z.read(n) for n in z.namelist()}

    files = package("1.storeypath")
    items = {f["id"]: f for f in json.loads(files["items.geojson"])["features"]}
    p = items[desk.id]["properties"]
    assert p["space_id"] == office.id and p["floor_id"] == f_id and p["building_id"] == b_id
    assert p["type"] == "DESK-MANAGER" and (p["width_m"], p["depth_m"]) == (1.8, 0.9)
    assert p["values"] == {}  # only its type's StoreyPath fields are sent
    assert items[ap.id]["properties"]["values"] == {"color": "#ff0000"} and items[ap.id]["properties"]["elevation_m"] is None
    foot = shape(items[desk.id]["geometry"])
    assert foot.geom_type == "Polygon" and len(foot.exterior.coords) == 5  # its footprint, four corners
    assert "ACCESS-POINT" in {t["code"] for t in json.loads(files["catalogue.json"])["types"]}
    rows = {r["id"]: r for r in csv.DictReader(io.StringIO(files["objects.csv"].decode()))}
    assert rows[desk.id]["kind"] == "item" and rows[desk.id]["floor_id"] == f_id and rows[desk.id]["building_id"] == b_id

    desk.x += 1.0  # carried across the room
    ap.status = "retired"  # taken away
    changes = json.loads(package("2.storeypath")["changes.json"])
    assert desk.id in changes["changed"] and ap.id in changes["retired"] and ap.id in changes["all_retired"]


def test_a_heading_says_which_way_an_item_faces_on_earth(converted, tmp_path):
    import json
    import zipfile

    from storeypath.export import export_package
    from storeypath.workspace import Placement

    ws, d, f_id, b_id, *_ = converted
    b = next(b for loc in ws.locations for b in loc.buildings)
    b.placement = Placement(lon=46.7, lat=24.7, x=0, y=0, bearing=0)  # the plan's up is north
    facing = {0: 180.0, 90: 90.0, 180: 0.0, 270: 270.0}  # rotation 0: its front faces the plan's -y, south
    ids = {ws.add_item("SOFA", f_id, 1.0, 1.0, rotation=r).id: h for r, h in facing.items()}
    export_package(ws, tmp_path / "p.storeypath", bake=False, record=False)
    with zipfile.ZipFile(tmp_path / "p.storeypath") as z:
        got = {f["id"]: f["properties"]["heading"] for f in json.loads(z.read("items.geojson"))["features"]}
    assert {i: got[i] for i in ids} == ids


def test_items_placed_moved_and_taken_away_in_review(tmp_path):
    # Over HTTP, as the Review page does: place an item, move it, give it details,
    # carry it to another floor, take it away and bring it back.
    import threading

    from sessions import admin_server
    from storeypath.server import Studio
    from test_review import NoModel, call, office_floor, wait, write_floor_dxf

    s = Studio(tmp_path / "data", model=NoModel())
    srv = admin_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        _, cat = call(f"{base}/api/catalogue")
        assert "DESK-MANAGER" in {t["code"] for t in cat["types"]}
        _, created = call(f"{base}/api/projects", {"name": "Offices"})
        code = created["code"]
        floors = []
        for name, ordinal in (("a.dxf", 0), ("b.dxf", 1)):
            write_floor_dxf(tmp_path / name, office_floor(ordinal), title="FLOOR PLAN")
            call(f"{base}/api/projects/{code}/drawings/{name}?private=0", raw=(tmp_path / name).read_bytes())
            _, job = call(f"{base}/api/projects/{code}/drawings/{name}/plans", {})
            plan = max(wait(base, job)["plans"], key=lambda x: x["size"][0] * x["size"][1])
            _, job = call(f"{base}/api/projects/{code}/floors", {"drawing": name, "plans": [
                {"index": plan["index"], "title": plan["title"], "region": plan["region"], "building": "HQ", "ordinal": ordinal}]})
            wait(base, job)
        _, p = call(f"{base}/api/projects/{code}")
        f0, f1 = (f["id"] for f in p["locations"][0]["buildings"][0]["floors"])

        status, item = call(f"{base}/api/projects/{code}/floors/{f0}/items", {"type": "ACCESS-POINT", "x": 3, "y": 4,
                                                                          "values": {"color": "#00ff00"}})
        assert status == 200 and is_item_id(item["id"]) and item["values"] == {"color": "#00ff00"}
        status, err = call(f"{base}/api/projects/{code}/floors/{f0}/items", {"type": "ACCESS-POINT", "x": 3, "y": 4,
                                                                         "values": {"ssid": "Staff"}})
        assert status == 400 and "system that manages" in err["error"]  # the network is wayfinder's to enter
        status, err = call(f"{base}/api/projects/{code}/floors/{f0}/items", {"type": "SPACESHIP", "x": 0, "y": 0})
        assert status == 400
        _, moved = call(f"{base}/api/projects/{code}/items/{item['id']}", {"x": 5, "rotation": 450, "floor_id": f1})
        assert (moved["x"], moved["rotation"]) == (5, 90)
        _, floor = call(f"{base}/api/projects/{code}/floors/{f1}")
        assert [i["id"] for i in floor["items"]] == [item["id"]]  # now on the other floor, the same ID
        _, gone = call(f"{base}/api/projects/{code}/items/{item['id']}", {"retired": True})
        assert gone["retired"] is True
        _, back = call(f"{base}/api/projects/{code}/items/{item['id']}", {"retired": False})
        assert back["retired"] is False

        cat["types"] = [t for t in cat["types"] if t["code"] != "SOFA"]
        status, err = call(f"{base}/api/catalogue", cat)
        assert status == 400 and "retired, not removed" in err["error"]
        _, full = call(f"{base}/api/catalogue")
        full["types"].append({"code": "PLANT", "name_en": "Plant", "name_ar": "نبتة", "width": 0.5, "depth": 0.5})
        status, saved = call(f"{base}/api/catalogue", full)
        assert status == 200 and "PLANT" in {t["code"] for t in saved["types"]}
    finally:
        srv.shutdown()
        srv.server_close()


def test_items_come_back_when_a_package_opens_as_a_project(converted, tmp_path, monkeypatch):
    # A project rebuilt from a package has its items where they were, turned as they
    # were, with their details; retired item IDs are never given again; the receiving
    # Studio learns the item types it lacks.
    from storeypath import catalogue
    from storeypath.bundle import open_file
    from storeypath.export import export_package
    from storeypath.workspace import Placement

    ws, d, f_id, b_id, *_ = converted
    b = next(b for loc in ws.locations for b in loc.buildings)
    b.placement = Placement(lon=46.7, lat=24.7, x=10, y=5, bearing=30)
    cat = catalogue.default_catalogue()
    cat.types.append(catalogue.ItemType(code="PLANT", name_en="Plant", name_ar="نبتة", width=0.5, depth=0.5))
    desk = ws.add_item("DESK-DIRECTOR", f_id, 12.5, 7.25, rotation=37, values={})
    plant = ws.add_item("PLANT", f_id, 3.0, 2.0, rotation=200)
    gone = ws.add_item("SOFA", f_id, 1.0, 1.0)
    gone.status = "retired"
    export_package(ws, tmp_path / "p.storeypath", bake=False, catalogue=cat)

    data = tmp_path / "other-studio"
    data.mkdir()
    opened = open_file(data, tmp_path / "p.storeypath")
    assert opened["item_types_added"] == ["PLANT"] and catalogue.load(data).get("PLANT").name_ar == "نبتة"
    again = Workspace.load(data / opened["code"] / f"{opened['code']}.spproj")
    for it in (desk, plant):
        back = again.items[it.id]
        assert (back.type, back.floor_id) == (it.type, it.floor_id)
        assert abs(back.x - it.x) < 0.02 and abs(back.y - it.y) < 0.02 and abs((back.rotation - it.rotation + 180) % 360 - 180) < 0.1
    assert again.items[gone.id].status == "retired"
    from storeypath import ids

    symbols = iter(gone.id.replace("-", "")[:10] + "Z" * 10)  # the retired one's drawn first
    monkeypatch.setattr(ids.secrets, "choice", lambda alphabet: next(symbols))
    assert again.add_item("COPIER", f_id, 0, 0).id == "ZZZZ-ZZZZ-ZZA"  # not given again


def test_an_item_is_found_by_its_id_as_a_person_types_it(tmp_path, monkeypatch):
    # Review's search box and the Navigate page take an item's ID as typed: either case,
    # O for 0, I and L for 1, with or without its hyphens; of a floor the person sees.
    from urllib.parse import quote

    from shapely.geometry import shape

    from people import Team
    from storeypath import accounts as acc

    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    team = Team(tmp_path / "data")
    try:
        office = next(r for r in team.spaces(team.hq0) if r.name == "OFFICE")
        p = shape(office.geometry).representative_point()
        status, desk = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "DESK-JUNIOR", "x": p.x, "y": p.y})
        assert status == 200
        typed = desk["id"].lower().replace("-", " ").replace("0", "o").replace("1", "l")
        status, found = team("vera", "GET", f"items/{quote(typed)}")  # vera sees the ground floor
        assert status == 200 and found["id"] == desk["id"] and found["floor_id"] == team.hq0
        assert team("bob", "GET", f"items/{quote(typed)}")[0] == 404  # bob sees the Annex alone
        wrong = typed[:-1] + ("x" if typed[-1] != "x" else "y")  # its check symbol wrong: not an ID
        assert team("khalid", "GET", f"items/{quote(wrong)}")[0] == 404
        # the way from it, asked with it as typed
        status, way = team("khalid", "GET", f"buildings/{team.hq}/navigation?from={quote(typed)}&to={office.id}")
        assert status == 200 and way["route"]["from"] == desk["id"], way
    finally:
        team.close()
