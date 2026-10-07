"""The conformance corpus: what every reader of the format (Studio's Python, the Go
module in go/, the viewer's JavaScript) must read the same way.

- packages/campus.storeypath: the demo campus, two buildings placed on the map,
  one floor's corridor divided into two zones by a line drawn in review;
- packages/unplaced.storeypath: one building not placed on the map yet (around
  0°N 0°E, true shape and size);
- localframe.json: points in Studio's local drawing metres and where they are on
  earth, for each building's placement, as Studio's projection gives them: a
  reader that turns lon/lat back into local metres must agree to a millimetre.

Readers make their own broken variants of these packages to test their checks.
Run from studio/ after a format change, and commit the result:

    uv run python ../spec/conformance/make.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    from shapely.geometry import shape

    from storeypath.convert import convert_floor
    from storeypath.export import export_package
    from storeypath.georef import Georeferencer
    from storeypath.samples import build_demo
    from storeypath.workspace import Placement

    out = HERE / "packages"
    out.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp())
    try:
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
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"wrote {out}/campus.storeypath, {out}/unplaced.storeypath and localframe.json")


if __name__ == "__main__":
    main()
