"""Buildings on a location's site plan: around a centre of the site's own, moved
and turned by a person, placed on the map all at once."""

import io
import math
import zipfile
import json

from shapely.geometry import shape

from storeypath.bundle import open_file
from storeypath.convert import convert_floor
from storeypath.export import export_package, on_site, placements, site_footprint, site_positions
from storeypath.georef import Georeferencer
from storeypath.samples import office_floor, write_floor_dxf
from storeypath.workspace import Placement, SitePosition, SourceDrawing, Workspace


def _campus(tmp_path, origins=((100.0, 50.0), (100.0, 50.0))):
    """Two buildings whose drawings each have their own origin: drawn on top of
    each other."""
    ws = Workspace.new("Campus")
    loc = ws.add_location("SITE", "Campus")
    ids = []
    for n, origin in enumerate(origins):
        write_floor_dxf(tmp_path / f"b{n}.dxf", office_floor(0), origin=origin)
        b_id = ws.add_building(loc, f"B{n}", f"Building {n}")
        f_id = ws.add_floor(b_id, 0, source=SourceDrawing(path=f"b{n}.dxf"))
        convert_floor(ws, f_id, tmp_path)
        ids.append(b_id)
    return ws, ws.locations[0], ids


def _lonlat(pl: Placement, x, y):
    return Georeferencer(pl).lonlat(x, y)


def test_with_no_positions_buildings_stand_as_drawn(tmp_path):
    ws, loc, ids = _campus(tmp_path, ((100.0, 50.0), (160.0, 50.0)))
    pls = placements(ws)
    from shapely.ops import unary_union

    middle = unary_union([shape(f.outline) for b in loc.buildings for f in b.floors]).centroid
    for b_id in ids:
        pl, real = pls[b_id]
        assert not real and (pl.lon, pl.lat, pl.bearing) == (0.0, 0.0, 0.0)
        assert math.hypot(pl.x - middle.x, pl.y - middle.y) < 1e-3  # one anchor for them all, as before


def test_a_building_moved_and_turned_stands_where_the_site_plan_has_it(tmp_path):
    ws, loc, ids = _campus(tmp_path)
    pos = site_positions(loc)
    b = loc.buildings[1]
    b.site = SitePosition(x=80.0, y=-15.0, rotation=90.0, pivot=pos[b.code].pivot)
    site = Placement(lon=0.0, lat=0.0)
    pl, _ = placements(ws)[ids[1]]
    px, py = b.site.pivot
    # its middle at (80, -15) of the site
    assert all(abs(a - c) < 1e-9 for a, c in zip(_lonlat(pl, px, py), _lonlat(site, 80.0, -15.0)))
    # turned a quarter clockwise: 10 m to its east is 10 m south of its middle on the site
    assert all(abs(a - c) < 1e-9 for a, c in zip(_lonlat(pl, px + 10, py), _lonlat(site, 80.0, -25.0)))
    assert on_site(b.site, px + 10, py) == (80.0 + 10 * math.cos(math.pi / 2), -15.0 - 10 * math.sin(math.pi / 2))


def test_the_site_on_the_map_places_every_building(tmp_path):
    ws, loc, ids = _campus(tmp_path)
    loc.placement = Placement(lon=51.53, lat=25.28, bearing=30.0)
    pls = placements(ws)
    assert all(pls[i][1] for i in ids)
    pos = site_positions(loc)
    for i, b in zip(ids, loc.buildings):
        lon, lat = _lonlat(pls[i][0], *pos[b.code].pivot)
        assert abs(lon - 51.53) < 0.01 and abs(lat - 25.28) < 0.01
    # a building placed by itself keeps its own placement
    own = Placement(lon=46.0, lat=24.0, x=1.0, y=2.0, bearing=5.0)
    loc.buildings[0].placement = own
    assert placements(ws)[ids[0]] == (own, True)


def test_side_by_side_and_back_through_a_package(tmp_path):
    ws, loc, ids = _campus(tmp_path)
    a, b = site_footprint(loc.buildings[0], site_positions(loc)["B0"]), site_footprint(loc.buildings[1], site_positions(loc)["B1"])
    assert a.intersection(b).area > 0.9 * a.area  # on top of each other, as drawn
    # side by side, as the Studio does it (server.arrange): B1 to the right of B0
    from storeypath.export import beside
    for o in loc.buildings:
        o.site = site_positions(loc)[o.code]
    loc.buildings[1].site = beside(loc, loc.buildings[1])
    loc.buildings[1].site.rotation = 15.0
    a, b = site_footprint(loc.buildings[0], loc.buildings[0].site), site_footprint(loc.buildings[1], loc.buildings[1].site)
    assert a.intersection(b).area < 1e-6
    # sent as packages, a building each, opened elsewhere one after the other (the
    # second into the project the first made): the same site plan
    data = tmp_path / "data"
    data.mkdir()
    m1 = {}
    for i in ids:
        out = tmp_path / f"{i}.storeypath"
        export_package(ws, out, building=i, record=False)
        m1.update(json.loads(zipfile.ZipFile(out).read("manifest.json"))["placements"])
        opened = open_file(data, out)
    assert opened["how"] == "building" and opened["replaced"] == []
    there = Workspace.load(data / opened["code"] / f"{opened['code']}.spproj")
    assert [b.code for b in there.locations[0].buildings] == ["B0", "B1"]
    m2 = {}
    for i in ids:
        again = io.BytesIO()
        export_package(there, again, building=i, record=False)
        m2.update(json.loads(zipfile.ZipFile(again).read("manifest.json"))["placements"])
    for k in m1:
        for f in ("lon", "lat", "bearing"):
            assert abs(m1[k][f] - m2[k][f]) < 1e-9
        # the same drawing point at the site's centre
        assert abs(m1[k]["x"] - m2[k]["x"]) < 2e-3 and abs(m1[k]["y"] - m2[k]["y"]) < 2e-3
