"""The conformance corpus: what every reader of the format (Studio's Python, the Go
module in go/, the viewer's JavaScript) must read the same way.

- packages/campus.storeypath: the demo campus, two buildings placed on the map,
  one floor's corridor divided into two zones by a line drawn in review, and
  furniture and equipment on its floors (format 0.6, furnish below): desks of every
  grade in offices (two in one office, one in a zone, one in the Annex, which stands
  turned on the map), a photocopier and an access point in a corridor, a sofa in
  the reception, a TV on a meeting room's wall, an open office's desks and access
  point;
- packages/unplaced.storeypath: one building not placed on the map yet (around
  0°N 0°E, true shape and size);
- packages/simple-office.storeypath: wayfinder's "simple-office" floor (its PDF
  test fixture: the same rooms and numbers, F0-301 to F0-322, an L-shaped office),
  with an open office divided into two numbered zones and a shaft hidden in
  review: for systems that import packages to test with;
- packages/simple-office-2.storeypath: the same project's next export, after the
  drawing was revised (the storage room F0-321 taken into the server room F0-320)
  and more review: F0-302 renumbered F0-302A, the pantry F0-305 deleted, F0-304
  renamed BOARD ROOM, the prayer room F0-322 retyped a meeting room, and the open
  office's south half divided again (F0-331 smaller, a new zone F0-332): for
  systems to test what they make of a later export;
- packages/campus-part.storeypath: one building of a two-building project (manifest
  scope), its second export, after a room in each building was renamed: only the
  Headquarters' rename is listed, and nothing of the Annex is retired;
  packages/campus-whole-1.storeypath and campus-whole-3.storeypath are the same
  project's first and third exports, of the whole project: a system that keeps the
  project applies 1, then the part, then 3, whose changes list the Annex's rename
  (the Annex was last exported in 1) and nothing more of the Headquarters;
- packages/campus-world.storeypath and simple-office-world.storeypath: campus and
  simple-office (the same IDs) with their floors pre-built in 3D, as Studio exports
  them when Node.js is there (format 0.5: world/<floor-id>.glb): readers must read
  them as they read the others, and the viewer must show them as it shows those;
- localframe.json: points in Studio's local drawing metres and where they are on
  earth, for each building's placement, as Studio's projection gives them: a
  reader that turns lon/lat back into local metres must agree to a millimetre.

Readers make their own broken variants of these packages to test their checks.
Run from studio/ after a format change, and commit the result:

    uv run python ../spec/conformance/make.py                  # all of them
    uv run python ../spec/conformance/make.py simple-office    # one: campus, part, simple-office, items, world

Every run makes new projects, so new IDs: remake only what changed. ``items`` and
``world`` make no project: ``items`` places campus's furniture and equipment again
in the package as it is, its IDs kept (campus places them when it makes the
project); ``world`` bakes campus and simple-office as they are (it needs Node.js),
so run it after them, and after a change to the viewer's builder
(viewer/src/world/build.js).
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = ("campus", "part", "simple-office", "items", "world")


def main(names: list[str]) -> None:
    unknown = set(names) - set(STEPS)
    if unknown:
        raise SystemExit(f"unknown: {', '.join(sorted(unknown))} ({', '.join(STEPS)})")
    work = Path(tempfile.mkdtemp())
    try:
        if not names or "campus" in names:
            campus(work)
        if not names or "part" in names:
            part(work)
        if not names or "simple-office" in names:
            simple_office(work)
        if "items" in names and "campus" not in names:  # campus places them as it makes the project
            items()
        if not names or "world" in names:
            world()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def world() -> None:
    """campus and simple-office with their floors pre-built in 3D: their files as
    they are, the manifest of this format with world/, and the floors baked."""
    from storeypath.assets import format_spec
    from storeypath.bake import WORLD_DIR, bake_world
    from storeypath.package import FORMAT_VERSION, Manifest, json_schemas

    for name in ("campus", "simple-office"):
        source = HERE / "packages" / f"{name}.storeypath"
        baked, why = bake_world(source)
        if not baked:
            raise SystemExit(f"{name}: not baked: {why}")
        out = HERE / "packages" / f"{name}-world.storeypath"
        schemas = {f"schema/{n}": json.dumps(s, indent=2) for n, s in json_schemas().items()}
        with zipfile.ZipFile(source) as z, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
            manifest = Manifest.model_validate_json(z.read("manifest.json"))
            manifest.format_version = FORMAT_VERSION
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
    from storeypath.samples import build_demo
    from storeypath.workspace import Placement

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
    export_package(ws, out / "campus.storeypath", record=False)

    unplaced = ws.save_as_new_project(work / "unplaced.spproj", "Unplaced")
    for loc in unplaced.locations:
        loc.buildings = loc.buildings[:1]
        loc.buildings[0].placement = None
    unplaced.objects = {k: r for k, r in unplaced.objects.items() if "-HQ-" in k or k.count("-") < 3}
    export_package(unplaced, out / "unplaced.storeypath", record=False)

    vectors = []
    placements = [b.placement for loc in ws.locations for b in loc.buildings]
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
    print(f"wrote {out}/campus.storeypath, {out}/unplaced.storeypath and localframe.json")


def furnish(ws) -> None:
    """Campus's furniture and equipment, placed by the rooms' numbers and sides (the
    demo is drawn the same every time). On the Headquarters' ground floor: a
    director's desk; a senior and a junior staff desk against the next office's
    west wall; a manager's desk; a TV on the meeting room's wall; a photocopier
    against the corridor's wall, between two doors, and an access point in the
    corridor; a sofa in the reception. On its first floor: a head of section's desk,
    the open office's three desks and its access point, and a desk in a zone of the
    divided hall. In the Annex, turned 110° on the map: the president's desk. Back on
    the Headquarters' ground floor: a king-size bed in an office."""
    from shapely.geometry import shape

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
    place("ACCESS-POINT", f_id, x1 - 2.5, y0 + 2.5)  # clear of its label
    hall = max((r for r in ws.floor_objects(f_id) if r.kind == "zone"), key=lambda r: shape(r.geometry).bounds[0])
    hx0, hy0, hx1, hy1 = shape(hall.geometry).bounds  # the east zone of the divided hall, against its north wall
    place("DESK-JUNIOR", f_id, hx1 - 3.9, hy1 - 0.35)

    f_id, (x0, y0, x1, y1) = room("ANNEX-F00", "001")
    place("DESK-PRESIDENT", f_id, (x0 + x1) / 2, y0 + 2.0)

    # placed last, so the others keep their numbers
    f_id, (x0, y0, x1, y1) = room("HQ-F00", "006")  # an office for long shifts: its bed's head on the south wall
    place("BED-KING", f_id, (x0 + x1) / 2, y0 + 0.05 + 1.05, rotation=180)


def items() -> None:
    """campus with its furniture and equipment placed again (furnish), in the package
    as it is: its IDs kept, the items numbered from 1 and listed as added."""
    import csv
    import io

    from storeypath.assets import format_spec
    from storeypath.bundle import workspace_from_package
    from storeypath.catalogue import default_catalogue
    from storeypath.export import _objects_csv, build_features
    from storeypath.ids import is_item_id
    from storeypath.package import FILES, FORMAT_VERSION, Manifest, json_schemas

    path = HERE / "packages" / "campus.storeypath"
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
        ws = workspace_from_package(z, path.name)
    ws.items, ws.next_item_seq = {}, 1  # placed again
    furnish(ws)
    cat = default_catalogue()
    features = build_features(ws, cat)
    placed = features["items"]

    manifest = Manifest.model_validate_json(files["manifest.json"])
    manifest.format_version = FORMAT_VERSION
    manifest.files.update(items=FILES["items"], catalogue=FILES["catalogue"])
    manifest.counts["items"] = len(placed)
    files["manifest.json"] = manifest.model_dump_json(indent=2).encode()
    files[FILES["items"]] = json.dumps({"type": "FeatureCollection", "features": placed}, ensure_ascii=False).encode()
    files[FILES["catalogue"]] = json.dumps(cat.model_dump(), ensure_ascii=False, indent=1).encode()
    # their rows in objects.csv, in the columns it has
    rows = [r for r in csv.DictReader(io.StringIO(files[FILES["objects"]].decode())) if r["kind"] != "item"]
    columns = list(rows[0])
    empty = {role: [] for role in ("location", "buildings", "spaces", "zones", "openings")}
    rows += list(csv.DictReader(io.StringIO(_objects_csv(ws, {**empty, "floors": features["floors"], "items": placed}))))[1:]
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    files[FILES["objects"]] = out.getvalue().encode()
    changes = json.loads(files[FILES["changes"]])
    changes["added"] = [i for i in changes["added"] if not is_item_id(i)] + [f["id"] for f in placed]
    files[FILES["changes"]] = json.dumps(changes, indent=2).encode()
    files["FORMAT.md"] = format_spec().encode()
    files.update({f"schema/{n}": json.dumps(s, indent=2).encode() for n, s in json_schemas().items()})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in files.items():
            z.writestr(n, data)
    print(f"wrote {path} ({len(placed)} items)")


def part(work: Path) -> None:
    """One building of the demo campus, exported after the whole project was."""
    from storeypath.export import export_package
    from storeypath.samples import build_demo
    from storeypath.workspace import Override, Workspace

    import shutil

    ws_path, demo = build_demo(work / "part")  # the whole project, exported first
    ws = Workspace.load(ws_path)
    first = HERE / "packages" / "campus-whole-1.storeypath"
    shutil.copyfile(demo, first)
    for code in ("HQ", "ANNEX"):  # a room renamed in each building
        room = next(r for r in sorted(ws.objects.values(), key=lambda r: r.id)
                    if r.kind == "space" and f"-{code}-" in r.id and r.status != "retired")
        ws.overrides[room.id] = Override(name=f"RENAMED {code}")
    hq = next(f"{ws.id}-{loc.code}-{b.code}" for loc in ws.locations for b in loc.buildings if b.code == "HQ")
    out = HERE / "packages" / "campus-part.storeypath"
    export_package(ws, out, buildings=[hq])
    third = HERE / "packages" / "campus-whole-3.storeypath"
    export_package(ws, third)  # the whole project again: the Annex's rename is new to it
    print(f"wrote {first}, {out} and {third}")


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
