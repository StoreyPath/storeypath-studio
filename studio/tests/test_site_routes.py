"""Ways across a location's site (format 0.13; navigation.py, FORMAT.md "Routes across a
site"): the land's walking network from its layout (its areas walked as one ground inside
the plot and one outside it, a node in each gate, kiosks on the land), the entrances where
each building's doors to the outside meet its footprint, linked to the same doors in the
buildings' own networks, ways from a gate into a building, from one building to another and
without steps, a building with no way in said plainly, the layout's package with its
network, the validators, and Studio's call for Navigate. Every site here is made up
(samples.sample_site_plan), as the conformance package site-plot is."""

import json
import math
import zipfile

import pytest
from shapely.geometry import LineString, Point, shape

from people import Team
from storeypath import accounts as acc
from storeypath import siteplan
from storeypath.convert import convert_floor
from storeypath.export import export_layout_package, export_package, placements
from storeypath.ids import LAND_CODE
from storeypath.layout import convert_layout, place_all
from storeypath.navigation import (NoRoute, build_network, build_site_network, route, site_entrances, site_graph,
                                   site_route, to_site)
from storeypath.package import FORMAT_VERSION
from storeypath.samples import (Cell, Door, sample_site_plan, site_plan_region, write_floor_dxf,
                                write_site_plan_dxf)
from storeypath.validate import validate_package
from storeypath.workspace import Override, Placement, SourceDrawing, Workspace

HOUSE = [Cell(0, 0, 5, 12, ["LIVING"], "living_room", Door(5, 6, "v", +1)),
         Cell(5, 0, 12, 18, ["HALL"], "lobby", Door(8.5, 0, "h", -1))]  # its one door to the outside: the hall's
SHUT = [HOUSE[0], Cell(5, 0, 12, 18, ["HALL"], "lobby", Door(5, 9, "v", -1))]  # no door to the outside
GUEST = [Cell(0, 0, 8, 6, ["BEDROOM"], "bedroom", Door(4, 0, "h", -1))]


def _site(tmp_path, house=HOUSE, kiosk=True):
    """The made-up plot of site-plot: a main house (its floor written +0.45 FFL beside the
    ground's ±0.00 FGL) and two guest houses, each drawn on its own and placed on its
    footprint; the site on the map; a kiosk just inside the pedestrian gate."""
    plan = sample_site_plan()
    write_site_plan_dxf(tmp_path / "site-plan.dxf", plan)
    write_floor_dxf(tmp_path / "house.dxf", house, title="MAIN HOUSE GROUND FLOOR PLAN")
    write_floor_dxf(tmp_path / "guest.dxf", GUEST, title="GUEST HOUSE GROUND FLOOR PLAN")
    ws = Workspace.new("Made-up plot")
    loc = ws.add_location("PLOT", "Made-up plot")
    made = {}
    for code, name, drawing in (("HOUSE", "Main house", "house.dxf"), ("GUESTA", "Guest house A", "guest.dxf"),
                                ("GUESTB", "Guest house B", "guest.dxf")):
        made[code] = ws.add_building(loc, code, name)
        f_id = ws.add_floor(made[code], 0, name="Ground floor", source=SourceDrawing(path=drawing))
        convert_floor(ws, f_id, tmp_path)
    layout = ws.add_layout(loc, SourceDrawing(path="site-plan.dxf", units="m", region=site_plan_region(plan)))
    convert_layout(ws, layout, tmp_path)
    place_all(ws, loc, confirm=True)
    ws.location(loc).placement = Placement(lon=46.68, lat=24.71, bearing=30.0)
    if kiosk:
        small = min((r for r in ws.floor_objects(layout) if r.kind == "opening"), key=lambda r: r.width)
        (x0, y0), (x1, _) = small.span
        ws.add_item("KIOSK", layout, round((x0 + x1) / 2, 3), round(y0 + 3.0, 3), rotation=0)
    return ws, loc, layout, made


def _room(ws, b_id, name):
    return next(r.id for r in ws.objects.values()
                if r.id.startswith(b_id + "-") and r.kind == "space" and r.name == name)


def _gates(ws, layout):
    # the pedestrian gate, then the vehicle gate
    return sorted((r for r in ws.floor_objects(layout) if r.kind == "opening"), key=lambda r: r.width)


def _navs(ws, made):
    return [build_network(ws, b) for b in made.values()]


@pytest.fixture(scope="module")
def plot(tmp_path_factory):
    ws, loc, layout, made = _site(tmp_path_factory.mktemp("plot"))
    notes = []
    land = build_site_network(ws, layout, notes=notes)
    return ws, loc, layout, made, land, notes


# ---- the land's network --------------------------------------------------------------------

def test_the_lands_network_from_its_layout(plot):
    ws, loc, layout, made, land, notes = plot
    nodes = {n["id"]: n for n in land["nodes"]}
    kinds = lambda k: [n for n in land["nodes"] if n["kind"] == k]  # noqa: E731
    assert land["layout"] == layout and land["buildings"] == []
    assert land["floors"] == [{"id": layout, "building_id": None, "name": "Layout", "ordinal": 0, "elevation": 0.0}]
    assert all(n["floor_id"] == layout for n in land["nodes"]) and notes == []
    # an arrival in each area walked: the yard, the parking, the garden and the street (no pool here)
    areas = {r.id: ws.effective(r)["type"] for r in ws.floor_objects(layout) if r.kind == "space"}
    assert sorted(areas[n["space_id"]] for n in kinds("room")) == ["garden", "parking", "road", "yard"]
    # a node in each gate, an approach on each side of it: the yard inside, the street outside
    pedestrian, vehicle = _gates(ws, layout)
    for gate in (pedestrian, vehicle):
        node = nodes[f"door:{gate.id}"]
        assert node["kind"] == "gate" and node["spaces"] == gate.connects and node["opening_id"] == gate.id
        sides = sorted(areas[n["space_id"]] for n in kinds("approach") if n["opening_id"] == gate.id)
        assert sides == ["road", "yard"]
    labels = {p["id"]: p["label"] for p in land["places"]}
    assert (labels[pedestrian.id], labels[vehicle.id]) == ("the pedestrian gate", "the vehicle gate")
    assert labels[next(i for i, t in areas.items() if t == "yard")] == "the yard"
    # the kiosk inside the pedestrian gate: where people stand before it, in the yard
    (kiosk,) = kinds("kiosk")
    assert areas[kiosk["space_id"]] == "yard" and kiosk["item_id"] in ws.items
    # the ways across the land: round the buildings and their footprints, never through them;
    # the street and the yard joined by the gates alone
    prints = [shape(r.geometry).buffer(-0.05) for r in ws.floor_objects(layout) if r.kind == "footprint"]
    outside = {n["id"] for n in land["nodes"] if n["space_id"] and areas[n["space_id"]] == "road"}
    for e in land["edges"]:
        if e["kind"] == "walk":
            line = LineString(e["path"]) if len(e["path"]) > 1 else Point(e["path"][0])
            assert all(line.intersection(fp).length < 1e-6 for fp in prints), e
            assert (e["from"] in outside) == (e["to"] in outside), e


def test_entrances_are_the_buildings_doors_to_the_outside_at_their_footprints(plot):
    ws, loc, layout, made, land, notes = plot
    nodes = {n["id"]: n for n in land["nodes"]}
    entrances = [n for n in land["nodes"] if n["kind"] == "entrance"]
    assert sorted(n["building_id"] for n in entrances) == sorted(made.values())
    for n in entrances:
        b_id = n["building_id"]
        door = ws.objects[n["opening_id"]]
        assert door.id.startswith(f"{b_id}-F00-") and len(door.connects) == 1  # its ground floor's door to the outside
        # at the door's middle, on the site, by the building's own frame and where it stands
        (x0, y0), (x1, y1) = door.span
        assert math.dist(to_site(ws, b_id)((x0 + x1) / 2, (y0 + y1) / 2), (n["local"]["x_m"], n["local"]["y_m"])) < 0.01
        fp = shape(ws.objects[n["footprint_id"]].geometry)
        assert fp.boundary.distance(Point(n["local"]["x_m"], n["local"]["y_m"])) < 0.5
        assert n["spaces"] == [n["footprint_id"]]
        # linked to the same door in the building's own network: no line, no length
        link = next(e for e in land["edges"] if e["kind"] == "link" and e["to"] == n["id"])
        assert link["from"] == f"door:{door.id}" and link["path"] == [] and link["length_m"] == 0 and link["cost"] == 0
        assert f"door:{door.id}" not in nodes
        building = {m["id"]: m for m in build_network(ws, b_id)["nodes"]}
        assert building[f"door:{door.id}"]["kind"] == "entrance"
    # a step into the house (its floor 0.45 m above the ground, as the site plan writes them): not without stairs
    house = next(n for n in entrances if n["building_id"] == made["HOUSE"])
    assert (house["rise_m"], house["steps"]) == (0.45, True)
    assert not next(e for e in land["edges"] if e["kind"] == "link" and e["to"] == house["id"])["accessible"]
    assert all(n["steps"] is None and n["rise_m"] is None for n in entrances if n is not house)  # (no level written)


def test_a_site_plans_levels_say_how_far_a_floor_stands_above_the_ground():
    assert siteplan.level_of("+0.45 FFL") == 0.45 and siteplan.level_of("FFL +450") == 0.45
    assert siteplan.level_of("±0.00 FGL") == 0.0 and siteplan.level_of("NGL -0.15") == -0.15
    assert siteplan.level_of("F.F.L 12,75") == 12.75 and siteplan.level_of("FFL") is None


def test_a_ramp_at_an_entrance_takes_its_step(tmp_path):
    ws, loc, layout, made = _site(tmp_path, kiosk=False)
    yard = next(r for r in ws.floor_objects(layout) if r.kind == "space" and r.type == "yard")
    ws.overrides[yard.id] = Override(type="ramp")  # (the ground before the house's door a ramp, as a person says)
    land = build_site_network(ws, layout)
    house = next(n for n in land["nodes"] if n["kind"] == "entrance" and n["building_id"] == made["HOUSE"])
    assert house["steps"] is False and house["rise_m"] == 0.45
    gate = _gates(ws, layout)[0]
    way = site_route(land, _navs(ws, made), gate.id, _room(ws, made["HOUSE"], "LIVING"), accessible=True)
    assert [s["kind"] for s in way["steps"]] == ["start", "walk", "enter", "walk", "arrive"]


# ---- ways across the site ------------------------------------------------------------------

def test_from_a_gate_into_a_building_and_from_one_building_to_another(plot):
    ws, loc, layout, made, land, notes = plot
    navs = _navs(ws, made)
    pedestrian, vehicle = _gates(ws, layout)
    living, bedroom_b = _room(ws, made["HOUSE"], "LIVING"), _room(ws, made["GUESTB"], "BEDROOM")
    way = site_route(land, navs, pedestrian.id, living)
    assert [s["text"] for s in way["steps"]] == [
        "Start at the pedestrian gate", "Walk 28 m through the yard to the entrance", "Go into Main house",
        "Walk 11 m along HALL to LIVING", "LIVING is on your left"]
    assert [leg["floor_id"] for leg in way["legs"]] == [layout, f"{made['HOUSE']}-F00"]
    (change,) = way["changes"]
    assert change["by"] == "entrance" and change["direction"] == "in" and change["floors"] == 0
    enter = way["steps"][2]
    assert enter == {"kind": "enter", "building_id": made["HOUSE"], "place": enter["place"],
                     "from_floor_id": layout, "to_floor_id": f"{made['HOUSE']}-F00", "text": "Go into Main house"}
    assert ws.objects[enter["place"]].kind == "footprint"
    # the outdoor leg in the site's frame, ending at the house's door there; the indoor one in the house's
    door = ws.objects[change["to_node"].removeprefix("door:")]
    (x0, y0), (x1, y1) = door.span
    assert math.dist(way["legs"][0]["points"][-1], to_site(ws, made["HOUSE"])((x0 + x1) / 2, (y0 + y1) / 2)) < 0.01
    assert way["legs"][1]["points"][0] == [round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3)]
    assert way["metres"] == pytest.approx(sum(leg["metres"] for leg in way["legs"]), abs=0.02)
    # from the house to a guest house: out, across the yard, in
    across = site_route(land, navs, living, bedroom_b)
    assert [s["kind"] for s in across["steps"]] == ["start", "walk", "leave", "walk", "enter", "walk", "arrive"]
    assert [c["direction"] for c in across["changes"]] == ["out", "in"]
    assert [leg["floor_id"] for leg in across["legs"]] == [f"{made['HOUSE']}-F00", layout, f"{made['GUESTB']}-F00"]
    assert (across["steps"][2]["text"], across["steps"][4]["text"]) == ("Go out of Main house", "Go into Guest house B")
    # and back, the same way
    back = site_route(land, navs, bedroom_b, living)
    assert back["nodes"] == across["nodes"][::-1] and back["metres"] == pytest.approx(across["metres"], abs=0.02)


def test_from_a_kiosk_or_a_point_on_the_land(plot):
    ws, loc, layout, made, land, notes = plot
    navs = _navs(ws, made)
    kiosk = next(i for i in ws.items.values() if i.floor_id == layout and i.type == "KIOSK")
    way = site_route(land, navs, kiosk.id, _room(ws, made["GUESTA"], "BEDROOM"))
    assert way["nodes"][0] == f"kiosk:{kiosk.id}" and way["steps"][0]["text"] == "Start at the kiosk in the yard"
    # a point on the land: the land's node nearest it
    graph = site_graph(land, navs)
    parking = next(r for r in ws.floor_objects(layout) if r.kind == "space" and r.type == "parking")
    at = shape(parking.geometry).centroid
    start = graph.nearest(at.x, at.y)
    assert graph.nodes[start]["floor_id"] == layout
    for point in ((at.x, at.y), [at.x, at.y], {"x_m": at.x, "y_m": at.y}):
        assert site_route(graph, start=point, end=_room(ws, made["HOUSE"], "HALL"))["from"] == start
    # on the land alone: the gate to the parking
    gate = _gates(ws, layout)[1]
    alone = site_route(graph, start=gate.id, end=parking.id)
    assert {leg["floor_id"] for leg in alone["legs"]} == {layout} and not alone["changes"]
    assert alone["steps"][-1]["text"] == "PARKING is ahead"


def test_without_stairs_a_step_at_an_entrance_is_not_taken(plot):
    ws, loc, layout, made, land, notes = plot
    navs = _navs(ws, made)
    gate = _gates(ws, layout)[0]
    with pytest.raises(NoRoute, match=r"without stairs or steps: the entrance into Main house has a step \(its floor "
                                      r"0.45 m above the ground"):
        site_route(land, navs, gate.id, _room(ws, made["HOUSE"], "LIVING"), accessible=True)
    easy = site_route(land, navs, gate.id, _room(ws, made["GUESTA"], "BEDROOM"), accessible=True)
    bedroom = _room(ws, made["GUESTA"], "BEDROOM")
    assert easy["accessible"] and easy["nodes"] == site_route(land, navs, gate.id, bedroom)["nodes"]
    # out of the house, without stairs: no way either
    with pytest.raises(NoRoute):
        site_route(land, navs, _room(ws, made["HOUSE"], "LIVING"), gate.id, accessible=True)


def test_a_building_with_no_door_to_the_outside_is_said_plainly(tmp_path):
    ws, loc, layout, made = _site(tmp_path, house=SHUT, kiosk=False)
    notes = []
    land = build_site_network(ws, layout, notes=notes)
    (note,) = notes
    assert note["building_id"] == made["HOUSE"] and note["footprint_id"].startswith(layout)
    assert note["why"].startswith("Main house: no door to the outside on its ground floor")
    assert not any(n.get("building_id") == made["HOUSE"] for n in land["nodes"])
    with pytest.raises(NoRoute, match="no way into it from the land: Main house: no door to the outside"):
        site_route(land, _navs(ws, made), _gates(ws, layout)[0].id, _room(ws, made["HOUSE"], "LIVING"), notes=notes)
    # the others are entered as before
    assert site_route(land, _navs(ws, made), _gates(ws, layout)[0].id, _room(ws, made["GUESTA"], "BEDROOM"))


def test_a_building_off_its_footprint_is_asked_whether_it_is_placed(tmp_path):
    ws, loc, layout, made = _site(tmp_path, kiosk=False)
    b = ws.building(made["GUESTA"])
    b.site = b.site.model_copy(update={"x": b.site.x + 30.0})  # moved by hand, off its footprint
    _, notes = site_entrances(ws, layout)
    (note,) = notes
    assert note["building_id"] == made["GUESTA"] and "is it placed on its footprint?" in note["why"]


def test_a_building_placed_on_the_map_by_itself_is_entered_where_the_map_puts_it(tmp_path):
    ws, loc, layout, made = _site(tmp_path, kiosk=False)
    before = {n["id"]: n["local"] for n in build_site_network(ws, layout)["nodes"] if n["kind"] == "entrance"}
    b_id = made["GUESTB"]
    pl, _ = placements(ws)[b_id]
    ws.building(b_id).placement, ws.building(b_id).site = pl, None  # the same place, on the map by itself
    after = {n["id"]: n["local"] for n in build_site_network(ws, layout)["nodes"] if n["kind"] == "entrance"}
    assert set(after) == set(before)
    for i, p in after.items():
        assert math.dist((p["x_m"], p["y_m"]), (before[i]["x_m"], before[i]["y_m"])) < 0.01, i


# ---- the package --------------------------------------------------------------------------

def test_the_layouts_package_carries_its_network_and_validates(tmp_path):
    ws, loc, layout, made = _site(tmp_path)
    out = tmp_path / "plot.storeypath"
    said = []
    m = export_layout_package(ws, out, layout, say=said.append)
    assert m.files["navigation"] == "navigation.json" and m.format_version == FORMAT_VERSION
    assert validate_package(out) == [] and not [line for line in said if "no way in" in line]
    paths = {}
    for code, b_id in made.items():
        paths[code] = tmp_path / f"{code}.storeypath"
        export_package(ws, paths[code], building=b_id, bake=False)
        assert validate_package(paths[code]) == []

    def read(path):
        with zipfile.ZipFile(path) as z:
            items = {f["id"]: f for f in json.loads(z.read("items.geojson"))["features"]}
            return json.loads(z.read("navigation.json")), items

    land, items = read(out)
    navs = []
    for path in paths.values():
        nav, its = read(path)
        navs.append(nav)
        items |= its
    # the packages give the way Studio finds on its own networks
    gate, living = _gates(ws, layout)[0].id, _room(ws, made["HOUSE"], "LIVING")
    assert site_route(land, navs, gate, living, items=items) == \
        site_route(build_site_network(ws, layout), _navs(ws, made), gate, living)
    # a building's network alone is as before: no land, no link
    assert all(nav.get("layout") is None and not any(e["kind"] == "link" for e in nav["edges"]) for nav in navs)

    def broken(change):
        with zipfile.ZipFile(out) as z:
            files = {n: z.read(n) for n in z.namelist()}
        doc = json.loads(files["navigation.json"])
        change(doc)
        files["navigation.json"] = json.dumps(doc).encode()
        bad = tmp_path / "bad.storeypath"
        with zipfile.ZipFile(bad, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        return validate_package(bad)

    def another_door(doc):
        next(e for e in doc["edges"] if e["kind"] == "link")["from"] += "9"

    def into_another_building(doc):
        next(n for n in doc["nodes"] if n["kind"] == "entrance")["building_id"] = f"{ws.id}-PLOT-ELSE"

    def off_the_layout(doc):
        doc["nodes"][0]["floor_id"] = f"{made['HOUSE']}-F00"

    assert any("does not join an entrance on the land" in e for e in broken(another_door))
    assert any("does not stand on footprint" in e for e in broken(into_another_building))
    assert any("unknown layout" in e for e in broken(off_the_layout))
    # a layout's package of 0.12 had no network
    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json"))
    manifest["format_version"] = "0.12.0"
    with zipfile.ZipFile(out) as z:
        files = {n: z.read(n) for n in z.namelist()}
    files["manifest.json"] = json.dumps(manifest).encode()
    old = tmp_path / "old.storeypath"
    with zipfile.ZipFile(old, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    assert any("has no walking network (0.13)" in e for e in validate_package(old))


def test_a_layouts_package_says_which_building_has_no_way_in(tmp_path):
    ws, loc, layout, made = _site(tmp_path, house=SHUT, kiosk=False)
    said = []
    export_layout_package(ws, tmp_path / "plot.storeypath", layout, say=said.append)
    assert any(line.startswith("no way in from the land: Main house: no door to the outside") for line in said)


# ---- in Studio: Navigate's call ------------------------------------------------------------

@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def test_navigates_call_gives_the_sites_network_and_ways_across_it(team, tmp_path):
    from sessions import call

    plan = sample_site_plan()
    write_site_plan_dxf(tmp_path / "site.dxf", plan)
    status, answer, _ = call(team.port, "PUT", f"/api/projects/{team.code}/drawings/site.dxf?private=0",
                             raw=(tmp_path / "site.dxf").read_bytes(), token=team.tokens["khalid"])
    assert status == 200, answer
    status, job = team("khalid", "POST", f"drawings/{answer['drawing']}/plans", {})
    found = team.finished("khalid", job)["result"]
    site = next(p for p in found["plans"] if p["kind"] == "site_plan")
    loc = f"{team.code}-DEMO"
    status, job = team("khalid", "POST", "layouts", {"drawing": answer["drawing"], "units": found["units"],
                                                    "plan": {"region": site["region"], "title": site["title"]},
                                                    "location_id": loc})
    assert status == 200, job
    team.finished("khalid", job)
    layout = f"{loc}-{LAND_CODE}-G00"
    page = team("khalid", "GET", f"/api/projects/{team.code}")[1]
    house = next(f for x in page["locations"] if x["id"] == loc for f in x["layout"]["footprints"]
                 if f["name"] == "Main House")
    # the Headquarters said to stand on the house's footprint: far too big for it, its doors off its edge
    assert team("khalid", "POST", f"footprints/{house['id']}", {"building": team.hq})[0] == 200
    status, net = team("khalid", "GET", f"locations/{loc}/navigation")
    assert status == 200, net
    assert net["layout"] == layout and net["location_id"] == loc
    assert {b["id"] for b in net["buildings"]} == {team.hq, team.annex}  # (each building's network with the land's)
    assert {f["id"] for f in net["floors"]} >= {layout, team.hq0, team.an0}
    assert any(n["kind"] == "gate" for n in net["nodes"]) and any(n["floor_id"] == team.hq0 for n in net["nodes"])
    (note,) = net["notes"]
    assert note["building_id"] == team.hq and "is it placed on its footprint?" in note["why"]
    # a way on the land: a gate to the parking; from a point on it
    gate = min((n for n in net["nodes"] if n["kind"] == "gate"), key=lambda n: n["id"])["opening_id"]
    parking = next(p["id"] for p in net["places"] if p.get("type") == "parking")
    status, got = team("khalid", "GET", f"locations/{loc}/navigation?from={gate}&to={parking}")
    assert status == 200, got
    way = got["route"]
    assert way["steps"][0]["text"].startswith("Start at the ") and way["steps"][-1]["place"] == parking
    status, got = team("khalid", "GET", f"locations/{loc}/navigation?from=0,-10&to={parking}&accessible=1")
    assert status == 200 and got["route"]["from"].startswith(("room:", "approach:", "kiosk:", "door:", "entrance:"))
    # a kiosk placed on the land in Review, just inside a gate: a way starts at it, as at one indoors
    gate_node = next(n for n in net["nodes"] if n["kind"] == "gate" and n["opening_id"] == gate)
    inside = next(n for n in net["nodes"] if n["kind"] == "approach" and n["opening_id"] == gate
                  and next(p for p in net["places"] if p["id"] == n["space_id"])["type"] != "road")
    x, y = inside["local"]["x_m"], inside["local"]["y_m"]
    turn = math.degrees(math.atan2(-(gate_node["local"]["x_m"] - x), -(gate_node["local"]["y_m"] - y))) % 360
    status, kiosk = team("khalid", "POST", f"floors/{layout}/items",
                         {"type": "KIOSK", "x": x, "y": y, "rotation": turn})
    assert status == 200, kiosk
    status, got = team("khalid", "GET", f"locations/{loc}/navigation?from={kiosk['id']}&to={parking}")
    assert status == 200, got
    assert got["route"]["nodes"][0] == f"kiosk:{kiosk['id']}"
    assert got["route"]["steps"][0]["text"].startswith("Start at the kiosk")
    # into the Headquarters: no way in from the land, and why
    office = next(r.id for r in team.spaces(team.hq0) if r.type == "office")
    status, got = team("khalid", "GET", f"locations/{loc}/navigation?from={gate}&to={office}")
    assert status == 404 and "is it placed on its footprint?" in got["error"]
    assert team("khalid", "GET", f"locations/{loc}/navigation?from={gate}&to=NOWHERE")[0] == 404
    # vera sees one floor of the Headquarters: not the land; bob (the Annex) neither
    assert team("vera", "GET", f"locations/{loc}/navigation")[0] in (403, 404)
    assert team("bob", "GET", f"locations/{loc}/navigation")[0] in (403, 404)
    # sara (the whole project) does; a location without a layout is said so
    assert team("sara", "GET", f"locations/{loc}/navigation")[0] == 200
    status, got = team("khalid", "GET", f"locations/{team.code}-NOPE/navigation")
    assert status == 404 and "with a layout" in got["error"]
