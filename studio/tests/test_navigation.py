"""Finding the way (navigation.py; FORMAT.md, "Navigation (0.8)" and "Stacks"): the
walking network Studio puts in a package, the way every reader finds on it (the
conformance ways of spec/conformance/routes.json), and which lifts and stairs are one
through the floors."""

import json
import math
import zipfile

import pytest
from shapely.geometry import Point, box, mapping, shape

from storeypath.assets import asset_dir
from storeypath.export import export_package
from storeypath.navigation import (CORNER_SLACK_M, MARGIN_M, ROOM_PENALTY_S, Graph, NoRoute, build_network, label_of, route,
                                   walking_region)
from storeypath.samples import build_demo
from storeypath.stacks import NOT_LINKED, stack_of, stacks
from storeypath.validate import validate_package
from storeypath.workspace import ObjectRecord, Override, Placement, Workspace

CONFORMANCE = asset_dir("spec") / "conformance"
ROUTES = json.loads((CONFORMANCE / "routes.json").read_text())


def package(name: str) -> tuple[dict, dict]:
    """A conformance package's network, and its items by ID."""
    with zipfile.ZipFile(CONFORMANCE / "packages" / name) as z:
        nav = json.loads(z.read("navigation.json"))
        items = {f["id"]: f for f in json.loads(z.read("items.geojson"))["features"]}
    return nav, items


@pytest.mark.parametrize("case", ROUTES["routes"], ids=[c["name"] for c in ROUTES["routes"]])
def test_each_conformance_way_is_found_as_every_reader_finds_it(case):
    nav, items = package(case["package"])
    got = route(Graph(nav), case["from"], case["to"], accessible=case["accessible"], items=items)
    want = case["expect"]
    assert got["nodes"] == want["nodes"]
    assert got["changes"] == want["changes"]
    assert got["steps"] == want["steps"]
    assert abs(got["metres"] - want["metres"]) <= ROUTES["tolerance_m"]
    assert abs(got["seconds"] - want["seconds"]) <= ROUTES["tolerance_s"]
    assert [(leg["floor_id"], leg["points"]) for leg in got["legs"]] == \
        [(leg["floor_id"], leg["points"]) for leg in want["legs"]]


def test_the_conformance_ways_show_what_they_are_for():
    by = {c["name"]: c["expect"] for c in ROUTES["routes"]}
    fast, easy = by["the kiosk to an office upstairs"], by["the kiosk to an office upstairs, without stairs"]
    assert [c["by"] for c in fast["changes"]] == ["stairs"] and [c["by"] for c in easy["changes"]] == ["lift"]
    assert fast["seconds"] < easy["seconds"]  # one floor: the stairs are quicker than waiting for the lift
    assert [s["kind"] for s in easy["steps"]] == ["start", "walk", "take", "walk", "arrive"]
    assert easy["steps"][2]["text"] == "Take the lift up to Floor 1"
    assert easy["steps"][-1]["side"] in ("left", "right")
    assert by["an office to itself"]["steps"][-1]["side"] == "here"
    assert by["the kiosk to a desk in a zone of the divided hall"]["steps"][-1]["place"].startswith(
        by["the kiosk to a desk in a zone of the divided hall"]["legs"][-1]["floor_id"])


# ---- the network --------------------------------------------------------------------------

def test_the_campus_network_has_every_kind_of_node_and_links_floors_by_stack():
    nav, items = package("campus-hq.storeypath")
    nodes = {n["id"]: n for n in nav["nodes"]}
    kinds = {n["kind"] for n in nav["nodes"]}
    assert {"door", "entrance", "approach", "room", "kiosk", "lift", "stairs"} <= kinds
    floors = [f["id"] for f in nav["floors"]]
    assert len(floors) == 3 and [f["ordinal"] for f in nav["floors"]] == [0, 1, 2]
    lifts = [e for e in nav["edges"] if e["kind"] == "lift"]
    stairs = [e for e in nav["edges"] if e["kind"] == "stairs"]
    # two lifts, each every floor to every other; one staircase, each floor to the next
    assert len(lifts) == 2 * 3 and len(stairs) == 2
    for e in lifts:
        a, b = nodes[e["from"]], nodes[e["to"]]
        assert a["stack"] == b["stack"] and a["floor_id"] != b["floor_id"] and e["accessible"]
        apart = abs(floors.index(a["floor_id"]) - floors.index(b["floor_id"]))
        assert e["seconds"] == 30 + 4 * apart
    for e in stairs:
        a, b = nodes[e["from"]], nodes[e["to"]]
        assert abs(floors.index(a["floor_id"]) - floors.index(b["floor_id"])) == 1 and not e["accessible"]
        assert e["seconds"] == 12
    # the kiosk: where people stand before its screen, in the reception
    kiosk = next(n for n in nav["nodes"] if n["kind"] == "kiosk")
    it = items[kiosk["item_id"]]["properties"]
    r = math.radians(it["local"]["rotation_deg"])
    front = (it["local"]["x_m"] + math.sin(r) * (it["depth_m"] / 2 + 0.6),
             it["local"]["y_m"] - math.cos(r) * (it["depth_m"] / 2 + 0.6))
    assert math.dist(front, (kiosk["local"]["x_m"], kiosk["local"]["y_m"])) < 0.01
    assert kiosk["space_id"] == it["space_id"]
    # an entrance is a door to the outside: one space beside it
    for n in nav["nodes"]:
        if n["kind"] == "entrance":
            assert len(n["spaces"]) == 1
    # every edge joins two nodes of the network, one edge a pair
    pairs = [tuple(sorted((e["from"], e["to"]))) for e in nav["edges"]]
    assert len(pairs) == len(set(pairs)) and all(a in nodes and b in nodes for a, b in pairs)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    ws_path, _ = build_demo(tmp_path_factory.mktemp("nav") / "demo")
    ws = Workspace.load(ws_path)
    return ws, f"{ws.id}-DEMO-HQ"


def test_a_corridors_doors_line_up_along_its_middle_and_are_joined_each_to_the_next(demo):
    ws, hq = demo
    nav = build_network(ws, hq)
    corridor = next(r for r in ws.floor_objects(f"{hq}-F00") if r.kind == "space" and ws.effective(r)["type"] == "corridor")
    x0, y0, x1, y1 = shape(corridor.geometry).bounds
    inside = [n for n in nav["nodes"] if n["space_id"] == corridor.id]
    approaches = [n for n in inside if n["kind"] == "approach"]
    assert len(approaches) > 15
    middle = (y0 + y1) / 2
    for n in approaches:
        if n["opening_id"] and x0 + 2.5 < n["local"]["x_m"] < x1 - 2.5:  # (not the doors at its ends)
            assert abs(n["local"]["y_m"] - middle) < 0.01, n["id"]
    walks = [e for e in nav["edges"] if e["kind"] == "walk" and e["space_id"] == corridor.id]
    assert len(walks) < 1.5 * len(inside)  # each to the next along it, not each to every other


def test_walks_keep_off_the_walls_where_the_room_is_wide_enough(demo):
    ws, hq = demo
    nav = build_network(ws, hq)
    spaces = {r.id: shape(r.geometry) for *_, fid in ws.iter_floors() for r in ws.floor_objects(fid)
              if r.kind == "space"}
    checked = 0
    for e in nav["edges"]:
        if e["kind"] != "walk":
            continue
        room = spaces[e["space_id"]]
        line = e["path"]
        for p in line:
            assert room.covers(Point(p).buffer(MARGIN_M - CORNER_SLACK_M - 0.01)), (e["from"], e["to"], p)
        checked += 1
    assert checked > 100


def test_a_narrow_passage_is_walked_up_to_its_walls():
    hall = box(0, 0, 10, 4).union(box(10, 1.8, 14, 2.3)).union(box(14, 0, 24, 4))  # a 0.5 m neck between two halls
    region = walking_region(hall)
    assert region.covers(Point(12, 2.05))  # the neck, walked
    assert not region.covers(Point(5, 0.1))  # the halls, kept off their walls
    assert len(getattr(region, "geoms", [region])) == 1


def test_labels_of_places():
    assert label_of("OFFICE", "112", "office") == "OFFICE 112"
    assert label_of("ROOM 3", "3", "room") == "ROOM 3"
    assert label_of(None, "114", "unspecified") == "Room 114"
    assert label_of(None, None, "corridor") == "the corridor"
    assert label_of(None, None, "elevator") == "the lift"
    assert label_of(None, None, "meeting_room") == "the meeting room"


# ---- a floor made by hand ------------------------------------------------------------------

def floor_of_rooms(rooms, doors, floors=1, zones=()):
    """A building of ``floors`` floors, each with the same rooms ((code, type, (x0, y0,
    x1, y1)), …) and doors ((code, [room codes], ((x, y), (x, y))), …): rooms 10 m deep,
    as a plan gives them, without drawings."""
    ws = Workspace.new("Hand")
    loc = ws.add_location("SITE", "Site")
    b = ws.add_building(loc, "B", "Building")
    ws.building(b).placement = Placement(lon=46.0, lat=24.0, x=0, y=0, bearing=0)
    fids = []
    for ordinal in range(floors):
        f = ws.add_floor(b, ordinal, name=f"Level {ordinal}")
        ws.floor(f).converted_at = ws.project.created_at
        fids.append(f)
        for code, kind, rect in rooms:
            rid = f"{f}-{code}"
            ws.objects[rid] = ObjectRecord(id=rid, kind="space", type=kind, geometry=mapping(box(*rect)))
        for code, parent, kind, rect in zones:
            zid, pid = f"{f}-{code}", f"{f}-{parent}"
            ws.objects[zid] = ObjectRecord(id=zid, kind="zone", type=kind, parent=pid, geometry=mapping(box(*rect)))
            ws.objects[pid].zones.append(zid)
        for code, joins, span in doors:
            oid = f"{f}-{code}"
            mid = ((span[0][0] + span[1][0]) / 2, (span[0][1] + span[1][1]) / 2)
            ws.objects[oid] = ObjectRecord(id=oid, kind="opening", type="door", connects=[f"{f}-{c}" for c in joins],
                                           span=[list(p) for p in span], geometry=mapping(Point(mid)))
    return ws, b, fids


def test_a_way_round_by_the_corridor_is_taken_before_one_through_an_office():
    # west hall — office (doors on both sides) — east hall; and a corridor round the
    # office's north joining both halls: through the office is shorter, by less than a minute
    rooms = [("0001", "lobby", (0, 0, 6, 10)), ("0002", "office", (6.2, 0, 12.2, 10)),
             ("0003", "lobby", (12.4, 0, 18.4, 10)), ("0004", "corridor", (0, 10.2, 18.4, 12.2))]
    doors = [("0010", ["0001", "0002"], ((6.1, 4.5), (6.1, 5.5))), ("0011", ["0002", "0003"], ((12.3, 4.5), (12.3, 5.5))),
             ("0012", ["0001", "0004"], ((2.5, 10.1), (3.5, 10.1))), ("0013", ["0003", "0004"], ((15, 10.1), (16, 10.1)))]
    ws, b, (f,) = floor_of_rooms(rooms, doors)
    nav = build_network(ws, b)
    way = route(nav, f"{f}-0001", f"{f}-0003")
    assert f"door:{f}-0012" in way["nodes"] and f"door:{f}-0010" not in way["nodes"]
    assert way["steps"][1]["text"].startswith("Walk") and "CORRIDOR" not in way["steps"][1]["text"]
    through = route(nav, f"{f}-0001", f"{f}-0002")  # into the office itself: by its door
    assert f"door:{f}-0010" in through["nodes"]
    door_in = next(e for e in nav["edges"] if e["kind"] == "door" and f"{f}-0002" in e["to"] + e["from"]
                   and e["space_id"] == f"{f}-0002")
    assert door_in["cost"] == pytest.approx(door_in["seconds"] + ROOM_PENALTY_S)


def test_what_is_not_walked_is_left_out():
    rooms = [("0001", "corridor", (0, 0, 20, 3)), ("0002", "utility", (0, 3.2, 5, 8)), ("0003", "shaft", (5.2, 3.2, 8, 8)),
             ("0004", "office", (8.2, 3.2, 14, 8))]
    doors = [("0010", ["0001", "0002"], ((2, 3.1), (3, 3.1))), ("0011", ["0001", "0004"], ((10, 3.1), (11, 3.1)))]
    ws, b, (f,) = floor_of_rooms(rooms, doors)
    ws.overrides[f"{f}-0004"] = Override(hidden=True)  # hidden in review: not walked either
    nav = build_network(ws, b)
    places = {n["space_id"] for n in nav["nodes"]} | {s for n in nav["nodes"] for s in n.get("spaces") or []}
    assert places - {None} == {f"{f}-0001"}
    with pytest.raises(KeyError):
        route(nav, f"{f}-0001", f"{f}-0002")


def test_the_zones_of_a_divided_space_are_walked_across_and_arrived_at():
    rooms = [("0001", "corridor", (0, 0, 20, 3)), ("0002", "open_area", (0, 3.2, 20, 12))]
    zones = [("0003", "0002", "office", (0, 3.2, 10, 12)), ("0004", "0002", "office", (10, 3.2, 20, 12))]
    doors = [("0010", ["0001", "0002"], ((2, 3.1), (3, 3.1)))]
    ws, b, (f,) = floor_of_rooms(rooms, doors, zones=zones)
    ws.overrides[f"{f}-0004"] = Override(name="TEAM B")
    nav = build_network(ws, b)
    way = route(nav, f"{f}-0001", f"{f}-0004")
    assert way["nodes"][-1] == f"room:{f}-0004"
    assert way["steps"][-1]["text"] == "TEAM B is ahead"  # walked into from the other zone: no door of its own
    assert route(nav, f"{f}-0001", f"{f}-0002")["nodes"][-1] == f"room:{f}-0003"  # the space: its nearer zone


def test_a_lift_with_no_door_drawn_joins_the_room_it_opens_onto():
    rooms = [("0001", "corridor", (0, 0, 20, 3)), ("0002", "elevator", (0, 3, 2.5, 5.5))]  # no wall, no door
    ws, b, fids = floor_of_rooms(rooms, [], floors=2)
    nav = build_network(ws, b)
    way = route(nav, f"{fids[0]}-0001", f"{fids[1]}-0001", accessible=True)
    assert [c["by"] for c in way["changes"]] == ["lift"]
    assert any(n.startswith("door:") and "+" in n for n in way["nodes"])


def test_without_stairs_there_may_be_no_way():
    rooms = [("0001", "corridor", (0, 0, 20, 3)), ("0002", "stairs", (0, 3.2, 3, 8))]
    doors = [("0010", ["0001", "0002"], ((1, 3.1), (2, 3.1)))]
    ws, b, fids = floor_of_rooms(rooms, doors, floors=2)
    nav = build_network(ws, b)
    assert route(nav, f"{fids[0]}-0001", f"{fids[1]}-0001")["changes"][0]["by"] == "stairs"
    with pytest.raises(NoRoute, match="without stairs"):
        route(nav, f"{fids[0]}-0001", f"{fids[1]}-0001", accessible=True)


# ---- stacks -------------------------------------------------------------------------------

def three_floors_of_lifts(codes, rects):
    """Three floors, each a corridor and a lift: ``codes`` and ``rects`` the lift's on each."""
    ws, b, fids = floor_of_rooms([("0001", "corridor", (0, 0, 20, 3))], [], floors=3)
    for f, code, rect in zip(fids, codes, rects):
        rid = f"{f}-{code}"
        ws.objects[rid] = ObjectRecord(id=rid, kind="space", type="elevator", geometry=mapping(box(*rect)))
    return ws, b, fids


def test_lifts_are_one_stack_by_their_code_or_where_they_overlap():
    ws, b, fids = three_floors_of_lifts(["0100", "0100", "0200"], [(0, 3, 2, 5), (5, 3, 7, 5), (5.5, 3, 7.5, 5)])
    (one,) = [s for s in stacks(ws, b)]  # the first two by their code, the last over the second (75%)
    assert one.key == f"{fids[0]}-0100" and one.floors == fids and one.type == "elevator"
    assert one.how[f"{fids[2]}-0200"] == "overlap" and one.how[f"{fids[0]}-0100"] == "code"
    ws, b, fids = three_floors_of_lifts(["0100", "0200", "0300"], [(0, 3, 2, 5), (1.5, 3, 3.5, 5), (10, 3, 12, 5)])
    assert sorted(len(s.members) for s in stacks(ws, b)) == [1, 1, 1]  # 25%, and apart: three lifts


def test_a_persons_link_or_unlink_wins():
    ws, b, fids = three_floors_of_lifts(["0100", "0100", "0300"], [(0, 3, 2, 5), (0, 3, 2, 5), (10, 3, 12, 5)])
    lone = f"{fids[2]}-0300"
    ws.overrides[lone] = Override(stack=f"{fids[1]}-0100")  # linked by hand, far from it
    assert set(stack_of(ws, b)) == {f"{fids[0]}-0100", f"{fids[1]}-0100", lone}
    assert len({*stack_of(ws, b).values()}) == 1
    ws.overrides[f"{fids[1]}-0100"] = Override(stack=NOT_LINKED)  # the middle one, not linked
    keys = stack_of(ws, b)
    assert keys[f"{fids[1]}-0100"] != keys[f"{fids[0]}-0100"]
    assert keys[lone] == keys[f"{fids[1]}-0100"]  # still linked to it by hand
    nav = build_network(ws, b)
    lifts = {(e["from"], e["to"]) for e in nav["edges"] if e["kind"] == "lift"}
    assert lifts == {tuple(sorted((f"lift:0100@{fids[1]}", f"lift:0300@{fids[2]}")))}


def test_a_package_carries_its_network_and_stacks(demo, tmp_path):
    ws, hq = demo
    out = tmp_path / "hq.storeypath"
    manifest = export_package(ws, out, building=hq, record=False, bake=False)
    assert manifest.format_version == "0.8.0" and manifest.files["navigation"] == "navigation.json"
    assert validate_package(out) == []
    with zipfile.ZipFile(out) as z:
        nav = json.loads(z.read("navigation.json"))
        spaces = json.loads(z.read("spaces.geojson"))["features"]
    assert nav["buildings"] == [hq] and nav["speed_m_s"] == 1.3
    lifts = [s["properties"] for s in spaces if s["properties"]["type"] == "elevator"]
    assert len(lifts) == 6 and len({p["stack"] for p in lifts}) == 2  # two lifts through three floors
    assert all(s["properties"]["stack"] is None for s in spaces if s["properties"]["type"] == "office")
    # and a broken network is found out
    with zipfile.ZipFile(out) as z, zipfile.ZipFile(tmp_path / "broken.storeypath", "w") as w:
        for name in z.namelist():
            data = z.read(name)
            if name == "navigation.json":
                doc = json.loads(data)
                doc["edges"][0]["to"] = "door:NOWHERE"
                data = json.dumps(doc)
            w.writestr(name, data)
    assert any("unknown node door:NOWHERE" in p for p in validate_package(tmp_path / "broken.storeypath"))
