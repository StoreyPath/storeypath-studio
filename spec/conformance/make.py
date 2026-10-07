"""The conformance corpus: what every reader of the format (Studio's Python, the Go
module in go/, the viewer's JavaScript) must read the same way.

- packages/campus.storeypath: the demo campus, two buildings placed on the map,
  one floor's corridor divided into two zones by a line drawn in review;
- packages/unplaced.storeypath: one building not placed on the map yet (around
  0°N 0°E, true shape and size);
- packages/simple-office.storeypath: wayfinder's "simple-office" floor (its PDF
  test fixture: the same rooms and numbers, F0-301 to F0-322, an L-shaped office),
  with an open office divided into two numbered zones and a shaft hidden in
  review: for systems that import packages to test with;
- localframe.json: points in Studio's local drawing metres and where they are on
  earth, for each building's placement, as Studio's projection gives them: a
  reader that turns lon/lat back into local metres must agree to a millimetre.

Readers make their own broken variants of these packages to test their checks.
Run from studio/ after a format change, and commit the result:

    uv run python ../spec/conformance/make.py                  # all of them
    uv run python ../spec/conformance/make.py simple-office    # one: campus, simple-office

Every run makes new projects, so new IDs: remake only what changed.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(names: list[str]) -> None:
    unknown = set(names) - {"campus", "simple-office"}
    if unknown:
        raise SystemExit(f"unknown: {', '.join(sorted(unknown))} (campus, simple-office)")
    work = Path(tempfile.mkdtemp())
    try:
        if not names or "campus" in names:
            campus(work)
        if not names or "simple-office" in names:
            simple_office(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


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


def simple_office(work: Path) -> None:
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
    write_floor_dxf(drawing, cells(), origin=origin, title="GROUND FLOOR PLAN")
    # the open office's south half has its own number
    doc = ezdxf.readfile(drawing)
    for i, line in enumerate(("OPEN OFFICE", "F0-331")):
        doc.modelspace().add_text(line, height=250, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(
            ((origin[0] + 26.4) * 1000, (origin[1] + 3.8 - 0.6 * i) * 1000), align=TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(drawing)

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
    export_package(ws, out, record=False)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
