"""The conformance corpus: what every reader of the format (Studio's Python, the Go
module in go/, the viewer's JavaScript) must read the same way.

Packages of this format (0.7: one building per package), made by ``campus``: one
project, the demo campus (two buildings placed on the map, one floor's corridor
divided into two zones by a line drawn in review, and furniture and equipment,
furnish below), exported a building at a time:

- packages/campus-hq.storeypath (export 1) and campus-annex.storeypath (export 2):
  the Headquarters with desks of several grades in offices (two in one office, one
  in a zone), a photocopier and an access point in a corridor, a sofa in the
  reception, a TV on a meeting room's wall, an open office's desks and access
  point, a king-size bed in an office; the Annex, turned 110° on the map, with the
  president's desk;
- packages/campus-hq-2.storeypath (export 3): the Headquarters' next export after
  it was moved on the map (shifted and turned), a junior staff desk carried to the
  Annex and the TV taken away: only the building is changed, the desk is moved
  away (to the Annex), the TV retired; every item's position in the building
  (local) is as it was;
- packages/campus-annex-2.storeypath (export 4): the Annex's next export, the desk
  in it (changed: its new floor and position).

Packages of earlier formats, kept as they were made (readers still read them; the
git history has how): campus.storeypath and campus-world.storeypath (0.6, the
whole campus, the second pre-built in 3D); campus-whole-1, campus-part and
campus-whole-3 (0.4: a project's first export, whole; its second, of one building
(scope), after a room in each building was renamed: only the Headquarters' rename
is listed, and nothing of the Annex is retired; its third, whole, listing the
Annex's rename); unplaced.storeypath (0.3.1, a building not placed on the map yet:
around 0°N 0°E, true shape and size); simple-office.storeypath and
simple-office-2.storeypath (0.3.2, made by ``simple-office``: wayfinder's
"simple-office" floor, its PDF test fixture's rooms and numbers, F0-301 to F0-322,
with an open office divided into two numbered zones and a shaft hidden; then its
next export after the drawing was revised and more review: F0-302 renumbered
F0-302A, the pantry F0-305 deleted, F0-304 renamed BOARD ROOM, the prayer room
F0-322 retyped, the open office's south half divided again);
simple-office-world.storeypath (``world``: simple-office pre-built in 3D, as Studio
exports it when Node.js is there).

localframe.json: points in Studio's local drawing metres and where they are on
earth, for each building's placement, as Studio's projection gives them: a reader
that turns lon/lat back into local metres must agree to a millimetre.

Readers make their own broken variants of these packages to test their checks.
Run from studio/ after a format change, and commit the result:

    uv run python ../spec/conformance/make.py campus           # one step: campus, simple-office, world

Every run makes new projects, so new IDs: remake only what changed. ``world`` makes
no project: it bakes simple-office as it is (it needs Node.js), so run it after a
change to the viewer's builder (viewer/src/world/build.js).
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = ("campus", "simple-office", "world")


def main(names: list[str]) -> None:
    unknown = set(names) - set(STEPS)
    if unknown:
        raise SystemExit(f"unknown: {', '.join(sorted(unknown))} ({', '.join(STEPS)})")
    if not names:
        raise SystemExit(f"name the steps to run ({', '.join(STEPS)}): every run makes new IDs")
    work = Path(tempfile.mkdtemp())
    try:
        if "campus" in names:
            campus(work)
        if "simple-office" in names:
            simple_office(work)
        if "world" in names:
            world()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def world() -> None:
    """simple-office with its floors pre-built in 3D: its files as they are, the
    manifest of this format (naming its building) with world/, and the floors baked."""
    from storeypath.assets import format_spec
    from storeypath.bake import WORLD_DIR, bake_world
    from storeypath.package import FORMAT_VERSION, Manifest, Scope, json_schemas

    for name in ("simple-office",):
        source = HERE / "packages" / f"{name}.storeypath"
        baked, why = bake_world(source)
        if not baked:
            raise SystemExit(f"{name}: not baked: {why}")
        out = HERE / "packages" / f"{name}-world.storeypath"
        schemas = {f"schema/{n}": json.dumps(s, indent=2) for n, s in json_schemas().items()}
        with zipfile.ZipFile(source) as z, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
            manifest = Manifest.model_validate_json(z.read("manifest.json"))
            manifest.format_version = FORMAT_VERSION
            manifest.scope = Scope(buildings=list(manifest.placements))  # its one building
            manifest.files["world"] = WORLD_DIR
            w.writestr("manifest.json", manifest.model_dump_json(indent=2))
            for n in z.namelist():
                if n == "FORMAT.md":
                    w.writestr(n, format_spec())
                elif n in schemas:
                    w.writestr(n, schemas[n])
                elif n != "manifest.json":
                    w.writestr(n, z.read(n))
            for n, data in baked.items():
                w.writestr(n, data)
        print(f"wrote {out} ({len(baked)} floors)")


def campus(work: Path) -> None:
    from shapely.geometry import shape

    from storeypath.convert import convert_floor
    from storeypath.export import export_package
    from storeypath.georef import Georeferencer
    from storeypath.ids import make_id
    from storeypath.samples import build_demo
    from storeypath.workspace import Placement, utcnow

    out = HERE / "packages"
    out.mkdir(exist_ok=True)
    ws_path, _ = build_demo(work / "campus")
    from storeypath.workspace import Workspace

    ws = Workspace.load(ws_path)
    # one floor's longest space divided in two, with no wall: zones
    f_id = next(fid for *_, fid in ws.iter_floors() if fid.endswith("HQ-F01"))
    space = max((r for r in ws.floor_objects(f_id) if r.kind == "space"), key=lambda r: shape(r.geometry).area)
    x0, y0, x1, y1 = shape(space.geometry).bounds
    mid = (x0 + x1) / 2
    ws.floor(f_id).edits.dividers.append([[mid, y0 - 0.1], [mid, y1 + 0.1]])
    convert_floor(ws, f_id, ws_path.parent)
    furnish(ws)
    ws.exports = []  # the demo's own exports aside: these are the project's first
    hq, annex = (make_id(ws.id, loc.code, b.code) for loc in ws.locations for b in loc.buildings)
    export_package(ws, out / "campus-hq.storeypath", building=hq, bake=False)
    export_package(ws, out / "campus-annex.storeypath", building=annex, bake=False)

    placements = [b.placement for loc in ws.locations for b in loc.buildings]  # as placed, for localframe.json

    # the Headquarters moved on the map, a desk carried to the Annex, the TV taken away
    b = ws.building(hq)
    b.placement = b.placement.model_copy(update={"lon": round(b.placement.lon + 0.0002, 7),
                                                  "bearing": (b.placement.bearing + 15) % 360})
    desk = next(i for i in ws.items.values() if i.type == "DESK-JUNIOR" and i.floor_id.endswith("HQ-F00"))
    tv = next(i for i in ws.items.values() if i.type == "TV")
    floors = {f"{bb.code}-{f.code}": fid for _, bb, f, fid in ws.iter_floors()}
    office = max((r for r in ws.floor_objects(floors["ANNEX-F01"]) if r.kind == "space"
                  and ws.effective(r)["type"] == "office"), key=lambda r: shape(r.geometry).area)
    at = shape(office.geometry).representative_point()
    desk.floor_id, desk.x, desk.y, desk.rotation = floors["ANNEX-F01"], round(at.x, 3), round(at.y, 3), 90.0
    tv.status, tv.retired_at = "retired", utcnow()
    export_package(ws, out / "campus-hq-2.storeypath", building=hq, bake=False)
    export_package(ws, out / "campus-annex-2.storeypath", building=annex, bake=False)

    vectors = []
    placements.append(Placement(lon=0.0, lat=0.0, x=0, y=0, bearing=0))
    placements.append(Placement(lon=-73.9857, lat=40.7484, x=-1200.5, y=860.25, bearing=-35))  # far from 0°
    for p in placements:
        g = Georeferencer(p)
        for dx, dy in ((0, 0), (12.5, -3.25), (-150, 220), (480, 510), (-900, -40)):
            x, y = p.x + dx, p.y + dy
            lon, lat = g.lonlat(x, y)
            vectors.append({"placement": {"lon": p.lon, "lat": p.lat, "x": p.x, "y": p.y, "bearing": p.bearing},
                            "local": [x, y], "lonlat": [lon, lat]})
    (HERE / "localframe.json").write_text(json.dumps({"tolerance_m": 0.001, "vectors": vectors}, indent=1) + "\n")
    print(f"wrote {out}/campus-hq, campus-annex, campus-hq-2 and campus-annex-2 (.storeypath), and localframe.json")


def furnish(ws) -> None:
    """Campus's furniture and equipment, placed by the rooms' numbers and sides (the
    demo is drawn the same every time). On the Headquarters' ground floor: a
    director's desk; a senior and a junior staff desk against the next office's
    west wall; a manager's desk; a TV on the meeting room's wall; a photocopier
    against the corridor's wall, between two doors, and an access point in the
    corridor; a sofa in the reception. On its first floor: a head of section's desk,
    the open office's three desks and its access point (its capacity set to 8 in
    review), and a desk in a zone of the divided hall. In the Annex, turned 110° on the map: the president's desk. Back on
    the Headquarters' ground floor: a king-size bed in an office."""
    from shapely.geometry import shape

    from storeypath.workspace import Override

    floors = {f"{b.code}-{f.code}": fid for _, b, f, fid in ws.iter_floors()}

    def room(floor: str, number: str):
        f_id = floors[floor]
        r = next(r for r in ws.floor_objects(f_id) if r.kind == "space" and ws.effective(r)["number"] == number)
        return f_id, shape(r.geometry).bounds

    def place(code, f_id, x, y, rotation=0, **values):
        # rotation 0: its front (where its user sits) faces the plan's -y; 90: +x
        ws.add_item(code, f_id, round(x, 3), round(y, 3), rotation=rotation, values=values)

    f_id, (x0, y0, x1, y1) = room("HQ-F00", "001")  # an office on the south side, its door north
    place("DESK-DIRECTOR", f_id, (x0 + x1) / 2, y0 + 1.6)
    f_id, (x0, y0, x1, y1) = room("HQ-F00", "002")
    place("DESK-SENIOR", f_id, x0 + 0.4, y0 + 1.5, rotation=90)
    place("DESK-JUNIOR", f_id, x0 + 0.35, y0 + 4.0, rotation=90)
    f_id, (x0, y0, x1, y1) = room("HQ-F00", "003")
    place("DESK-MANAGER", f_id, (x0 + x1) / 2, y0 + 2.0)
    f_id, (x0, y0, x1, y1) = room("HQ-F00", "004")  # the meeting room
    place("TV", f_id, x0 + 0.07, (y0 + y1) / 2, rotation=90, size_in=65)
    f_id, (x0, y0, x1, y1) = room("HQ-F00", "005")  # the corridor, by this office's door and the meeting room's
    corridor = next(r for r in ws.floor_objects(f_id) if r.kind == "space" and ws.effective(r)["type"] == "corridor")
    cx0, cy0, cx1, cy1 = shape(corridor.geometry).bounds
    place("COPIER", f_id, x0 - 2.2, cy0 + 0.4, rotation=180, model="MFP-C450")
    place("ACCESS-POINT", f_id, x0 - 2.2, (cy0 + cy1) / 2, color="#ffffff")
    f_id, (x0, y0, x1, y1) = room("HQ-F00", "017")  # the reception
    place("SOFA", f_id, x1 - 0.5, (y0 + y1) / 2 + 0.4, rotation=270, seats=3)

    f_id, (x0, y0, x1, y1) = room("HQ-F01", "112")  # an office on the north side, its door south
    place("DESK-SECTION-HEAD", f_id, (x0 + x1) / 2, y1 - 1.6, rotation=180)
    f_id, (x0, y0, x1, y1) = room("HQ-F01", "117")  # the open office
    for k in range(3):
        place("DESK-JUNIOR", f_id, x0 + 1.9 + 1.5 * k, y1 - 2.4, rotation=180)
    open_office = next(r for r in ws.floor_objects(f_id) if r.kind == "space" and ws.effective(r)["number"] == "117")
    seats = ws.overrides.get(open_office.id) or Override()
    seats.capacity = 8  # set in review: more than its three desks so far
    ws.overrides[open_office.id] = seats
    place("ACCESS-POINT", f_id, x1 - 2.5, y0 + 2.5)  # clear of its label
    hall = max((r for r in ws.floor_objects(f_id) if r.kind == "zone"), key=lambda r: shape(r.geometry).bounds[0])
    hx0, hy0, hx1, hy1 = shape(hall.geometry).bounds  # the east zone of the divided hall, against its north wall
    place("DESK-JUNIOR", f_id, hx1 - 3.9, hy1 - 0.35)

    f_id, (x0, y0, x1, y1) = room("ANNEX-F00", "001")
    place("DESK-PRESIDENT", f_id, (x0 + x1) / 2, y0 + 2.0)

    # placed last, so the others keep their numbers
    f_id, (x0, y0, x1, y1) = room("HQ-F00", "006")  # an office for long shifts: its bed's head on the south wall
    place("BED-KING", f_id, (x0 + x1) / 2, y0 + 0.05 + 1.05, rotation=180)


def simple_office(work: Path) -> None:
    from dataclasses import replace

    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    from shapely.geometry import shape

    from storeypath.convert import convert_floor
    from storeypath.export import export_package
    from storeypath.samples import simple_office as cells
    from storeypath.samples import write_floor_dxf
    from storeypath.workspace import Override, Placement, SourceDrawing, Workspace

    origin = (40.0, 20.0)
    drawing = work / "simple-office.dxf"

    def label_south_half(path: Path) -> None:
        # the open office's south half has its own number
        doc = ezdxf.readfile(path)
        for i, line in enumerate(("OPEN OFFICE", "F0-331")):
            doc.modelspace().add_text(line, height=250, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(
                ((origin[0] + 26.4) * 1000, (origin[1] + 3.8 - 0.6 * i) * 1000), align=TextEntityAlignment.MIDDLE_CENTER)
        doc.saveas(path)

    write_floor_dxf(drawing, cells(), origin=origin, title="GROUND FLOOR PLAN")
    label_south_half(drawing)

    ws = Workspace.new("Simple Office")
    loc = ws.add_location("MAIN", "Main site")
    b_id = ws.add_building(loc, "HQ", "Headquarters")
    ws.building(b_id).placement = Placement(lon=51.5310, lat=25.2854, x=origin[0], y=origin[1], bearing=0)
    f_id = ws.add_floor(b_id, 0, name="Ground floor", source=SourceDrawing(path=drawing.name))
    convert_floor(ws, f_id, work)
    # in review: the open office divided between its two numbers, the shaft hidden
    spaces = [r for r in ws.floor_objects(f_id) if r.kind == "space"]
    hall = next(r for r in spaces if r.name == "OPEN OFFICE")
    x0, y0, x1, y1 = shape(hall.geometry).bounds
    ws.floor(f_id).edits.dividers.append([[x0 - 0.1, origin[1] + 6.5], [x1 + 0.1, origin[1] + 6.5]])
    convert_floor(ws, f_id, work)
    for r in ws.floor_objects(f_id):
        if r.kind == "space" and r.type == "shaft":
            ws.overrides[r.id] = Override(hidden=True)
        if r.kind == "zone":
            ws.overrides[r.id] = Override(type="office")  # team areas: people sit there
    out = HERE / "packages" / "simple-office.storeypath"
    export_package(ws, out, record=True)

    # export 2: the drawing revised, the storage room taken into the server room
    revised = []
    for c in cells():
        if c.label and "F0-321" in c.label:
            continue
        if c.label and "F0-320" in c.label:
            c = replace(c, x1=18)
        revised.append(c)
    write_floor_dxf(drawing, revised, origin=origin, title="GROUND FLOOR PLAN")
    label_south_half(drawing)
    # in review: the open office's south half divided again, and corrections
    ws.floor(f_id).edits.dividers.append([[(x0 + x1) / 2, origin[1] + 6.5], [(x0 + x1) / 2, y0 - 0.1]])
    convert_floor(ws, f_id, work)
    by_number = {r.number: r for r in ws.floor_objects(f_id) if r.number}
    ws.overrides[by_number["F0-302"].id] = Override(number="F0-302A")
    ws.overrides[by_number["F0-305"].id] = Override(hidden=True)  # deleted in review: kept, with its ID
    ws.overrides[by_number["F0-304"].id] = Override(name="BOARD ROOM")
    ws.overrides[by_number["F0-322"].id] = Override(type="meeting_room")
    for r in ws.floor_objects(f_id):
        if r.kind == "zone" and r.id not in ws.overrides:
            ws.overrides[r.id] = Override(type="office", number="F0-332", name="OPEN OFFICE")
    second = HERE / "packages" / "simple-office-2.storeypath"
    export_package(ws, second, record=True)
    print(f"wrote {out} and {second}")


if __name__ == "__main__":
    main(sys.argv[1:])
