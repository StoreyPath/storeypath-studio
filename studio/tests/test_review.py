"""StoreyPath Studio's web application: the review editor and the whole workflow."""

import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile

import ezdxf
import pytest
from shapely.geometry import LineString

from storeypath.review import NotFound, Review
from storeypath.samples import office_floor, write_sheet_dxf
from storeypath.server import Studio, make_server
from storeypath.workspace import Override, Workspace


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture
def review(converted):
    ws, d, f_id, *_ = converted
    path = d / "project.spproj"
    ws.save(path)
    return Review(path), path, f_id


def _by_number(floor, number):
    return next(s for s in floor["spaces"] if s["number"] == number)


def test_project_lists_floors_with_review_counts(review):
    r, _, f_id = review
    p = r.project()
    assert [f["id"] for f in p["floors"]] == [f_id]
    assert p["floors"][0]["spaces"] == 24 and p["floors"][0]["review"] == 2
    assert "office" in p["types"] and "bedroom" in p["types"]


def test_floor_has_spaces_doors_and_why_to_review(review):
    r, _, f_id = review
    floor = r.floor(f_id)
    assert len(floor["spaces"]) == 24
    assert sorted(d["type"] for d in floor["doors"]).count("door") == 24
    assert sum(1 for d in floor["doors"] if d["type"] == "window") == 14
    assert floor["method"] == "outlines"
    assert _by_number(floor, "214")["reasons"] == ["no type"]
    office = _by_number(floor, "201")
    assert office["detected"]["source"].startswith("label:")
    assert office["geometry"]["type"] == "Polygon" and len(office["label_point"]) == 2


def test_correct_saves_to_the_workspace_file(review):
    r, path, f_id = review
    space = _by_number(r.floor(f_id), "214")
    updated = r.correct(space["id"], {"correction": {"type": "office", "name": " Quiet room "}})
    assert (updated["type"], updated["name"], updated["reasons"]) == ("office", "Quiet room", [])
    saved = Workspace.load(path).overrides[space["id"]]
    assert (saved.type, saved.name) == ("office", "Quiet room")
    updated = r.correct(space["id"], {"correction": {"type": "storage"}})
    assert updated["name"] is None and updated["correction"] == {"type": "storage"}
    updated = r.correct(space["id"], {"reset": True})
    assert updated["correction"] is None and updated["reasons"] == ["no type"]


def test_accepting_and_removing_a_detected_name(review):
    r, _, f_id = review
    floor = r.floor(f_id)
    unnamed = next(s for s in floor["spaces"] if s["reasons"] == ["no name or number"])
    assert r.correct(unnamed["id"], {"correction": {}})["reasons"] == []
    office = _by_number(floor, "201")
    assert r.correct(office["id"], {"correction": {"name": ""}})["name"] is None


def test_hiding_and_ignoring(review):
    r, path, f_id = review
    floor = r.floor(f_id)
    sliver = next(s for s in floor["spaces"] if s["reasons"] == ["no type"])
    office = _by_number(floor, "201")
    s1 = r.correct(sliver["id"], {"ignored": True})
    assert s1["ignored"] and s1["reasons"] == []  # off the review list
    s2 = r.correct(office["id"], {"correction": {"name": "Quiet room"}})
    s2 = r.correct(office["id"], {"hidden": True})
    assert s2["hidden"] and s2["name"] == "Quiet room"  # a flag keeps the correction
    s2 = r.correct(office["id"], {"correction": {"name": "Focus room"}})
    assert s2["hidden"] and s2["name"] == "Focus room"  # a correction keeps the flag
    assert r.project()["floors"][0]["review"] == 1
    # survives re-conversion and reaches the package
    ws = Workspace.load(path)
    from storeypath.convert import convert_floor
    from storeypath.export import export_package
    convert_floor(ws, f_id, path.parent)
    export_package(ws, path.parent / "p.storeypath")
    spaces = {f["id"]: f["properties"] for f in json.loads(
        zipfile.ZipFile(path.parent / "p.storeypath").read("spaces.geojson"))["features"]}
    assert spaces[sliver["id"]]["ignored"] and spaces[office["id"]]["hidden"]
    assert not spaces[office["id"]]["ignored"]
    # un-hiding a space that had no other correction leaves no trace
    r.correct(sliver["id"], {"ignored": False})
    assert sliver["id"] not in Workspace.load(path).overrides


def test_bad_corrections_are_refused(review):
    r, _, f_id = review
    space = _by_number(r.floor(f_id), "201")
    with pytest.raises(ValueError):
        r.correct(space["id"], {"correction": {"type": "castle"}})
    with pytest.raises(ValueError):
        r.correct(space["id"], {"correction": {"colour": "red"}})
    with pytest.raises(NotFound):
        r.correct(f_id + "-9999", {"correction": {}})
    door = r.floor(f_id)["doors"][0]
    with pytest.raises(NotFound):
        r.correct(door["id"], {"correction": {}})


def test_changes_made_elsewhere_are_picked_up(review):
    r, path, f_id = review
    space = _by_number(r.floor(f_id), "214")
    ws = Workspace.load(path)  # e.g. `storeypath fix` in another terminal
    ws.overrides[space["id"]] = Override(type="kitchen")
    ws.save(path)
    assert _by_number(r.floor(f_id), "214")["type"] == "kitchen"


def test_drawing_linework(review):
    r, _, f_id = review
    drawing = r.drawing(f_id)
    assert drawing["groups"]["walls"] and drawing["groups"]["doors"] and drawing["groups"]["outlines"]
    labels = [t for t in drawing["texts"] if t[5]]
    assert any("OFFICE" in t[4] for t in labels)


def test_a_floor_as_printed_lies_under_its_spaces(review):
    from PIL import Image

    from storeypath.review import floor_print, floor_print_png

    r, path, f_id = review
    info = floor_print(r, f_id)
    x0, y0, x1, y1 = info["bounds"]
    floor = r.floor(f_id)
    for s in floor["spaces"]:  # every space is on the print
        ring = s["geometry"]["coordinates"][0]
        assert all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in ring)
    png = floor_print_png(r, f_id)
    assert png.content_type == "image/png"
    image = Image.open(io.BytesIO(png.data))
    assert image.size == (info["width"], info["height"])
    assert abs(info["width"] / info["height"] - (x1 - x0) / (y1 - y0)) < 0.01  # not stretched
    assert info["px_per_m"] == 100
    dark = sum(1 for p in image.convert("L").tobytes() if p < 128)
    assert dark > 0.002 * info["width"] * info["height"]  # something is drawn on it
    kept = list((path.parent / ".storeypath-cache" / "prints").glob(f"{f_id}-*.png"))
    assert len(kept) == 1 and floor_print(r, f_id)["key"] == info["key"]  # drawn once, then kept


# ---- over HTTP --------------------------------------------------------------------

@pytest.fixture
def studio(tmp_path):
    s = Studio(tmp_path / "data", model=NoModel())
    srv = make_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", s
    srv.shutdown()
    srv.server_close()


def call(url, body=None, method=None, raw=None, headers=None):
    if raw is not None:
        req = urllib.request.Request(url, data=raw, method="PUT", headers={"X-StoreyPath": "1", **(headers or {})})
    else:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as res:
            payload = res.read()
            ctype = res.headers.get("Content-Type", "")
            return res.status, (json.loads(payload) if ctype == "application/json" else payload)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(base, job):
    for _ in range(600):
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.1)
        _, job = call(f"{base}/api/jobs/{job['id']}")
    assert job["state"] == "done", job
    return job["result"]



def test_two_plans_as_one_floor_are_refused_before_anything_changes(studio, tmp_path):
    # A sheet with an outbuilding whose plan is titled like the house's.
    base, app = studio
    _, created = call(f"{base}/api/projects", {"name": "Villa"})
    code = created["code"]
    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (100.0, 50.0), "GROUND FLOOR PLAN"),
                                             (office_floor(0), (170.0, 50.0), "GROUND FLOOR PLAN")], area_outlines=False)
    call(f"{base}/api/projects/{code}/drawings/sheet.dxf?private=0", raw=(tmp_path / "sheet.dxf").read_bytes())
    _, job = call(f"{base}/api/projects/{code}/drawings/sheet.dxf/plans", {})
    house, annex = wait(base, job)["plans"]

    def plan(p, building):
        return {"index": p["index"], "title": p["title"], "region": p["region"], "building": building, "ordinal": 0}

    status, error = call(f"{base}/api/projects/{code}/floors",
                         {"drawing": "sheet.dxf", "plans": [plan(house, "Main building"), plan(annex, "Main building")]})
    assert status == 400
    assert error["error"].startswith("GROUND FLOOR PLAN and GROUND FLOOR PLAN are both floor 0 of Main building")
    assert Workspace.load(app.path(code)).locations[0].buildings == []  # nothing was added

    # Buildings are known by name: "Main annex" is not "Main building", though both start with Main.
    _, job = call(f"{base}/api/projects/{code}/floors",
                  {"drawing": "sheet.dxf", "plans": [plan(house, "Main building"), plan(annex, "Main annex")]})
    wait(base, job)
    buildings = Workspace.load(app.path(code)).locations[0].buildings
    assert [(b.code, b.name, len(b.floors)) for b in buildings] == [("MAIN", "Main building", 1), ("MAIN2", "Main annex", 1)]

    status, error = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [plan(house, "main building")]})
    assert status == 400 and error["error"].startswith("main building already has floor 0 (")

def test_the_whole_workflow_in_the_browser(studio, tmp_path):
    base, app = studio
    status, body = call(f"{base}/")
    assert status == 200 and b"StoreyPath Studio" in body
    _, s = call(f"{base}/api/status")
    assert s["model"] is None and "version" in s

    _, created = call(f"{base}/api/projects", {"name": "Head office"})
    code = created["code"]
    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (100.0, 50.0), "GROUND FLOOR PLAN"),
                                             (office_floor(1), (170.0, 52.5), "FIRST FLOOR PLAN")], area_outlines=False)
    sheet = tmp_path / "sheet.dxf"
    doc = ezdxf.readfile(sheet)
    doc.header["$LASTSAVEDBY"] = "someone"
    doc.saveas(sheet)
    status, job = call(f"{base}/api/projects/{code}/drawings/Owner%20Name%20villa.dxf", raw=sheet.read_bytes())
    assert status == 200
    added = wait(base, job)  # kept without its private information, under a plain name
    assert added["drawing"] == "drawing-1.dxf" and "$LASTSAVEDBY" in added["privacy"]["hidden"]
    assert [p.name for p in (app.path(code).parent / "drawings").iterdir()] == ["drawing-1.dxf"]
    assert call(f"{base}/api/projects/{code}")[1]["drawings"] == ["drawing-1.dxf"]
    # as sent, when asked
    _, kept = call(f"{base}/api/projects/{code}/drawings/sheet.dxf?private=0", raw=sheet.read_bytes())
    assert kept["drawing"] == "sheet.dxf"
    (app.path(code).parent / "drawings" / "sheet.dxf").unlink()

    _, job = call(f"{base}/api/projects/{code}/drawings/drawing-1.dxf/plans", {})
    found = wait(base, job)
    assert found["units"] == "mm" and found["units_sure"] and not found["units_chosen"]
    assert found["units_reason"].startswith("Read in millimetres: the doors")
    # other units: the plans are found again, at that scale
    _, job = call(f"{base}/api/projects/{code}/drawings/drawing-1.dxf/plans", {"units": "cm"})
    in_cm = wait(base, job)
    assert in_cm["units"] == "cm" and in_cm["units_chosen"]
    assert in_cm["units_reason"] == "Read in centimetres, as you chose."
    status, error = call(f"{base}/api/projects/{code}/drawings/drawing-1.dxf/plans", {"units": "furlongs"})
    assert status == 400 and "unknown units" in error["error"]
    plans = [(p["title"], p["kind"], p["floor"]) for p in found["plans"]]
    assert plans == [("GROUND FLOOR PLAN", "floor_plan", 0), ("FIRST FLOOR PLAN", "floor_plan", 1)]
    assert found["plans"][0]["preview"]

    chosen = [{"index": p["index"], "title": p["title"], "region": p["region"], "building": "HQ",
               "ordinal": p["floor"], "name": p["title"].title()} for p in found["plans"]]
    _, job = call(f"{base}/api/projects/{code}/floors", {"drawing": "drawing-1.dxf", "units": found["units"],
                                                         "plans": chosen})
    added = wait(base, job)
    assert len(added["floors"]) == 2
    ws = Workspace.load(app.path(code))
    assert [f.source.units for _, _, f, _ in ws.iter_floors()] == ["mm", "mm"]  # kept with each floor
    log = call(f"{base}/api/jobs/{job['id']}")[1]["log"]
    assert any("moved 70.0" in line for line in log)  # the first floor was drawn 70 m to the right

    _, project = call(f"{base}/api/projects/{code}")
    building = project["locations"][0]["buildings"][0]
    assert [f["name"] for f in building["floors"]] == ["Ground Floor Plan", "First Floor Plan"]
    assert all(f["converted"] and f["layers"] for f in building["floors"])
    _, floor = call(f"{base}/api/projects/{code}/floors/{building['floors'][1]['id']}")
    assert len(floor["spaces"]) == len(office_floor(1))

    # the 3D view shows the project as it is, without an export
    status, preview = call(f"{base}/api/projects/{code}/preview.storeypath")
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(preview)) as z:
        floors = json.loads(z.read("floors.geojson"))["features"]
        assert all(f["properties"]["walls"] for f in floors)
    assert call(f"{base}/api/projects/{code}")[1]["exports"] == []

    x, y = building["centre"]
    status, _ = call(f"{base}/api/projects/{code}/buildings/{building['id']}/placement",
                     {"lat": 24.7, "lon": 46.6, "bearing": 0, "x": x, "y": y})
    assert status == 200
    _, job = call(f"{base}/api/projects/{code}/export", {})
    exported = wait(base, job)
    assert exported["file"] == f"{code}-001.storeypath"  # by code, as the project's folder
    status, package = call(f"{base}/api/projects/{code}/exports/{exported['file']}")
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(package)) as z:
        assert "spaces.geojson" in z.namelist()


def test_http_refuses_other_sites(studio):
    base, _ = studio
    _, created = call(f"{base}/api/projects", {"name": "P"})
    code = created["code"]
    # a form or fetch from another site cannot send application/json, nor a custom header, without a preflight
    assert call(f"{base}/api/projects", {"name": "x"}, headers={"Content-Type": "text/plain"})[0] == 403
    req = urllib.request.Request(f"{base}/api/projects/{code}/drawings/a.dxf", data=b"x", method="PUT")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403
    # a page reached through another host name (DNS rebinding)
    assert call(f"{base}/api/projects", headers={"Host": "evil.example:80"})[0] == 403
    assert call(f"{base}/../server.py")[0] == 404
    assert call(f"{base}/api/projects/{code}/drawings/x.exe", raw=b"MZ")[0] == 400


def test_pages_have_unique_ids():
    """Scripts find elements by ID; two elements with one ID break the page."""
    import re
    from pathlib import Path

    app = Path(__file__).parent.parent / "src" / "storeypath" / "review_app"
    for page in app.glob("*.html"):
        ids = re.findall(r'\sid="([^"]+)"', page.read_text(encoding="utf-8"))
        assert len(ids) == len(set(ids)), f"{page.name}: {sorted({i for i in ids if ids.count(i) > 1})}"


def test_the_editor_gets_the_lines_studio_divided_rooms_along(tmp_path):
    # An office opening 2 m wide onto the corridor, no door: Studio divides them across
    # the gap in the wall, and the editor draws that line.
    from dataclasses import replace

    from storeypath.convert import convert_floor
    from storeypath.samples import write_floor_dxf
    from storeypath.workspace import SourceDrawing

    cells = office_floor(1)
    i = next(i for i, c in enumerate(cells) if c.number == "101")
    cells[i] = replace(cells[i], door=replace(cells[i].door, block=False, width=2.0))
    write_floor_dxf(tmp_path / "plan.dxf", cells, area_outlines=False)
    ws = Workspace.new("P")
    f_id = ws.add_floor(ws.add_building(ws.add_location("SITE", "Site"), "HQ", "HQ"), 1,
                        source=SourceDrawing(path="plan.dxf"))
    convert_floor(ws, f_id, tmp_path)
    ws.save(tmp_path / "p.spproj")
    doors = Review(tmp_path / "p.spproj").floor(f_id)["doors"]
    lines = [d["divider"] for d in doors if d["divider"]]
    assert len(lines) == 1
    (line,) = lines[0]
    assert 1.5 <= LineString(line).length <= 2.5  # across the opening
    assert all(d["divider"] is None for d in doors if d["type"] == "door")


def test_floor_heights_come_from_the_levels_on_the_sheet(studio, tmp_path):
    import ezdxf

    base, app = studio
    _, created = call(f"{base}/api/projects", {"name": "Levels"})
    code = created["code"]
    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (100.0, 50.0), "GROUND FLOOR PLAN"),
                                             (office_floor(1), (170.0, 52.5), "FIRST FLOOR PLAN")], area_outlines=False)
    doc = ezdxf.readfile(tmp_path / "sheet.dxf")
    for i, text in enumerate(["+0.00 GROUND FLOOR SLAB LVL.", "+3.40 FIRST FLOOR SLAB LVL.", "+6.80 ROOF SLAB LVL.",
                              "+8.00 PARAPET LVL."]):
        doc.modelspace().add_text(text, height=250).set_placement((400000, 50000 + 1000 * i))  # a section, aside
    doc.saveas(tmp_path / "sheet.dxf")
    call(f"{base}/api/projects/{code}/drawings/sheet.dxf?private=0", raw=(tmp_path / "sheet.dxf").read_bytes())
    _, job = call(f"{base}/api/projects/{code}/drawings/sheet.dxf/plans", {})
    found = wait(base, job)
    levels = found["levels"]
    assert levels["heights"] == {"0": 3.4, "1": 3.4} and levels["roof"] == 2 and levels["parapet"] == 1.2
    assert levels["summary"].startswith("ground floor ±0.00, first floor +3.40, roof +6.80")

    chosen = [{"index": p["index"], "title": p["title"], "region": p["region"], "building": "HQ",
               "ordinal": p["floor"], "height": 3.4, "parapet": 1.1} for p in found["plans"][:2]]
    _, job = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "units": "mm", "plans": chosen})
    wait(base, job)
    floors = {f.ordinal: f for _, _, f, _ in Workspace.load(app.path(code)).iter_floors()}
    assert {n: (f.elevation, f.height, f.parapet_height) for n, f in floors.items()} == {
        0: (0.0, 3.4, 1.1), 1: (3.4, 3.4, 1.1)}


def test_project_folders_are_named_by_code_not_by_name(studio):
    base, app = studio
    names = ["Villa", "Villa", "فيلا الرياض"]
    codes = [call(f"{base}/api/projects", {"name": n})[1]["code"] for n in names]
    assert len(set(codes)) == 3
    for code, name in zip(codes, names):
        path = app.path(code)
        assert path == app.data / code / f"{code}.spproj"  # the name people type is only shown
        assert Workspace.load(path).project.name == name
    assert sorted(p.name for p in app.data.iterdir()) == sorted(codes)
