"""StoreyPath Studio's web application: the review editor and the whole workflow."""

import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import LineString, shape

from storeypath.package import FORMAT_VERSION
from storeypath.review import NotFound, Review
from storeypath.samples import office_floor, write_floor_dxf, write_sheet_dxf
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
    with pytest.raises(ValueError):  # a door has no type or name to correct…
        r.correct(door["id"], {"correction": {}})
    assert r.correct(door["id"], {"ignored": True})["ignored"]  # …it can only be deleted
    assert not r.correct(door["id"], {"ignored": False})["ignored"]


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
    assert status == 400 and error["error"].startswith("Main building already has floor 0 (")
    assert "replace that floor's drawing" in error["error"]


def test_one_drawing_per_floor_goes_into_the_same_building(studio, tmp_path):
    # A building drawn one floor per file, in the same coordinates: each file's plan
    # is added to the building chosen by its ID, lined up without moving; a revised
    # drawing of a floor replaces its drawing, its rooms keeping their IDs; another
    # location is made when asked for.
    from storeypath.samples import write_floor_dxf

    base, app = studio
    _, created = call(f"{base}/api/projects", {"name": "Large building"})
    code = created["code"]
    for n in (0, 1):
        write_floor_dxf(tmp_path / f"level-{n}.dxf", office_floor(n), origin=(100.0, 50.0), title=f"LEVEL {n} PLAN")
    write_floor_dxf(tmp_path / "level-0-rev.dxf", office_floor(0), origin=(100.0, 50.0), title="LEVEL 0 PLAN")

    def add(name, **where):
        call(f"{base}/api/projects/{code}/drawings/{name}?private=0", raw=(tmp_path / name).read_bytes())
        _, job = call(f"{base}/api/projects/{code}/drawings/{name}/plans", {})
        plan = max(wait(base, job)["plans"], key=lambda x: x["size"][0] * x["size"][1])
        status, job = call(f"{base}/api/projects/{code}/floors", {"drawing": name, "plans": [
            {"index": plan["index"], "title": plan["title"], "region": plan["region"], **where}]})
        return status, job

    status, job = add("level-0.dxf", building="Engineering", ordinal=0)
    wait(base, job)
    ws = Workspace.load(app.path(code))
    loc_id = f"{ws.id}-{ws.locations[0].code}"
    b_id = f"{loc_id}-{ws.locations[0].buildings[0].code}"
    status, job = add("level-1.dxf", location_id=loc_id, building_id=b_id, ordinal=1)
    wait(base, job)
    ws = Workspace.load(app.path(code))
    b = ws.building(b_id)
    assert [(f.ordinal, Path(f.source.path).name) for f in sorted(b.floors, key=lambda f: f.ordinal)] == \
        [(0, "level-0.dxf"), (1, "level-1.dxf")]
    first = next(f for f in b.floors if f.ordinal == 1)
    assert first.converted_at is not None and max(abs(v) for v in (first.source.offset or (0, 0))) < 0.05  # lined up as drawn
    assert len(ws.locations) == 1 and len(ws.locations[0].buildings) == 1

    # floor 0 again: refused, unless its drawing is replaced
    status, error = add("level-0-rev.dxf", building_id=b_id, ordinal=0)
    assert status == 400 and "already has floor 0" in error["error"]
    ground_id = f"{b_id}-{next(f.code for f in b.floors if f.ordinal == 0)}"
    before = {r.id for r in ws.floor_objects(ground_id) if r.kind == "space"}
    status, job = add("level-0-rev.dxf", building_id=b_id, ordinal=0, replace=True)
    wait(base, job)
    ws = Workspace.load(app.path(code))
    ground = ws.floor(ground_id)
    assert Path(ground.source.path).name == "level-0-rev.dxf"
    assert {r.id for r in ws.floor_objects(ground_id) if r.kind == "space"} == before  # the same rooms, the same IDs

    # a building in another location
    status, job = add("level-1.dxf", location="North campus", building="Workshop", ordinal=0)
    wait(base, job)
    ws = Workspace.load(app.path(code))
    assert [loc.name for loc in ws.locations] == ["Large building", "North campus"]
    assert ws.locations[1].code == "NORTH" and [b.name for b in ws.locations[1].buildings] == ["Workshop"]

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
    found = wait(base, job)  # what is private in it, for a person to choose what to keep
    assert any(f["id"] == "hidden $LASTSAVEDBY" and "someone" in f["label"] for f in found["found"])
    # sent again and not added: nothing of it is kept
    _, again = call(f"{base}/api/projects/{code}/drawings/Owner%20Name%20villa.dxf", raw=sheet.read_bytes())
    pending = wait(base, again)["pending"]
    assert call(f"{base}/api/projects/{code}/incoming/{pending}/cancel", {})[0] == 200
    assert call(f"{base}/api/projects/{code}/incoming/{pending}", {"keep": []})[0] == 404
    _, job = call(f"{base}/api/projects/{code}/incoming/{found['pending']}", {"keep": []})
    added = wait(base, job)  # kept without its private information, under a plain name
    assert added["drawing"] == "drawing-1.dxf" and "$LASTSAVEDBY" in added["privacy"]["hidden"]
    assert sorted(p.name for p in (app.path(code).parent / "drawings").iterdir()) == ["drawing-1.dxf",
                                                                                     "drawing-1.dxf.words.txt"]
    status, words = call(f"{base}/api/projects/{code}/drawings/drawing-1.dxf/words")
    words = words.decode()
    assert status == 200 and "OFFICE" in words and "someone" not in words  # what is left, and only that
    assert "When it was added: removed" in words
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


def test_pages_answer_while_a_job_changes_a_project(studio):
    # converting takes minutes with vision: no page waits for it, the project's own
    # included (it shows the project as last saved)
    base, app = studio
    codes = [call(f"{base}/api/projects", {"name": n})[1]["code"] for n in ("A", "B")]
    held, done = threading.Event(), threading.Event()

    def converting():
        with app._changing(app.path(codes[0])):
            held.set()
            done.wait(10)

    threading.Thread(target=converting, daemon=True).start()
    held.wait(5)
    try:
        for code in codes:
            with urllib.request.urlopen(f"{base}/api/projects/{code}", timeout=3) as res:
                assert res.status == 200
    finally:
        done.set()


def test_walls_doors_and_windows_drawn_in_review_are_kept(review):
    # A wall the drawing leaves out parts a room in two; a door added in it joins
    # them again; both survive converting the floor again, and can be taken away.
    from storeypath.convert import convert_floor
    from storeypath.workspace import Workspace

    r, path, f_id = review
    floor = r.floor(f_id)
    room = max((s for s in floor["spaces"] if s["kind"] == "space"), key=lambda s: s["area"])
    x0, y0, x1, y1 = shape(room["geometry"]).bounds
    mid = (x0 + x1) / 2
    r.edit(f_id, {"add": {"wall": [[mid, y0 - 0.1], [mid, y1 + 0.1]]}})
    ws = Workspace.load(path)
    convert_floor(ws, f_id, path.parent)
    ws.save(path)
    after = r.floor(f_id)
    halves = [s for s in after["spaces"] if s["kind"] == "space" and shape(s["geometry"]).intersects(shape(room["geometry"]))
              and shape(s["geometry"]).intersection(shape(room["geometry"])).area > 1]
    assert len(halves) == 2  # parted by the drawn wall
    door_at = (mid, (y0 + y1) / 2)
    r.edit(f_id, {"add": {"opening": {"type": "door", "span": [[mid, door_at[1] - 0.45], [mid, door_at[1] + 0.45]]}}})
    ws = Workspace.load(path)
    convert_floor(ws, f_id, path.parent)
    ws.save(path)
    drawn = [d for d in r.floor(f_id)["doors"] if d["drawn"]]
    assert len(drawn) == 1 and drawn[0]["type"] == "door" and len(drawn[0]["connects"]) == 2
    assert {h["id"] for h in halves} == set(drawn[0]["connects"])
    r.edit(f_id, {"remove": {"at": [mid, y0 + 0.5]}})  # the wall; the door stays drawn
    assert r.floor(f_id)["edits"]["walls"] == [] and len(r.floor(f_id)["edits"]["openings"]) == 1
    with pytest.raises(NotFound):
        r.edit(f_id, {"remove": {"at": [x0 - 50, y0 - 50]}})
    with pytest.raises(ValueError):
        r.edit(f_id, {"add": {"opening": {"type": "hatch", "span": [[0, 0], [1, 0]]}}})


def test_a_space_divided_by_a_line_drawn_in_review_has_two_zones(review):
    # No wall: one space used for two things; an opening drawn in a wall joins two
    # spaces as a way through with no door.
    from storeypath.convert import convert_floor
    from storeypath.workspace import Workspace

    r, path, f_id = review
    floor = r.floor(f_id)
    room = max((s for s in floor["spaces"] if s["kind"] == "space"), key=lambda s: s["area"])
    x0, y0, x1, y1 = shape(room["geometry"]).bounds
    mid = (x0 + x1) / 2
    r.edit(f_id, {"add": {"divider": [[mid, y0 + 0.2], [mid, y1 - 0.2]]}})  # short of the walls: it reaches them
    ws = Workspace.load(path)
    convert_floor(ws, f_id, path.parent)
    ws.save(path)
    after = r.floor(f_id)
    divided = next(s for s in after["spaces"] if s["kind"] == "space" and s["zones"]
                   and abs(s["area"] - room["area"]) < 0.5)
    zones = [s for s in after["spaces"] if s["id"] in divided["zones"]]
    assert len(zones) == 2 and all(z["kind"] == "zone" and z["space_id"] == divided["id"] for z in zones)
    assert abs(sum(z["area"] for z in zones) - room["area"]) < 0.5
    assert room["id"] in divided["zones"]  # what was used keeps its ID: a part of it, now a zone
    assert Workspace.load(path).floor(f_id).edits.walls == []  # no wall
    # an opening in the wall between two rooms
    rooms = sorted((s for s in after["spaces"] if s["kind"] == "space"), key=lambda s: -s["area"])
    a, b = rooms[0], next(s for s in rooms[1:] if shape(s["geometry"]).distance(shape(rooms[0]["geometry"])) < 0.5)
    between = shape(a["geometry"]).buffer(0.3).intersection(shape(b["geometry"]).buffer(0.3))
    c = between.centroid
    vertical = (between.bounds[3] - between.bounds[1]) > (between.bounds[2] - between.bounds[0])
    span = [[c.x, c.y - 0.5], [c.x, c.y + 0.5]] if vertical else [[c.x - 0.5, c.y], [c.x + 0.5, c.y]]
    r.edit(f_id, {"add": {"opening": {"type": "opening", "span": span}}})
    ws = Workspace.load(path)
    convert_floor(ws, f_id, path.parent)
    ws.save(path)
    way = next(d for d in r.floor(f_id)["doors"] if d["drawn"])
    assert way["type"] == "opening" and len(way["connects"]) == 2
    r.edit(f_id, {"remove": {"at": [mid, (y0 + y1) / 2]}})  # the dividing line
    assert r.floor(f_id)["edits"]["dividers"] == []


def test_a_project_is_deleted_only_when_its_name_is_typed(studio):
    base, app = studio
    _, created = call(f"{base}/api/projects", {"name": "Old site"})
    code = created["code"]
    folder = app.path(code).parent
    status, error = call(f"{base}/api/projects/{code}/delete", {"confirm": "old"})
    assert status == 400 and "type the project's name" in error["error"] and folder.exists()
    with app._changing(app.path(code)):  # a job working on it
        status, error = call(f"{base}/api/projects/{code}/delete", {"confirm": "Old site"})
    assert status == 400 and "job" in error["error"] and folder.exists()
    status, done = call(f"{base}/api/projects/{code}/delete", {"confirm": "Old site"})
    assert status == 200 and done["deleted"] == code
    assert not folder.exists() and code not in [p["code"] for p in call(f"{base}/api/projects")[1]]
    assert call(f"{base}/api/projects/{code}")[0] == 404


def _reconvert(path, f_id):
    from storeypath.convert import convert_floor

    ws = Workspace.load(path)
    convert_floor(ws, f_id, path.parent)
    ws.save(path)


def _wall_area(floor):
    return shape(floor["walls"]).area


def test_a_window_of_the_drawing_given_another_size_keeps_it_and_its_id(review):
    # Wider: the gap in the wall grows; narrower: what it no longer takes is wall
    # again; back to the drawing's size: as before. The window keeps its ID.
    r, path, f_id = review
    floor = r.floor(f_id)
    window = next(d for d in floor["doors"] if d["type"] == "window" and d["span"])
    width, walls = window["width"], _wall_area(floor)
    thickness = floor["wall_thickness"]
    assert window["resize"] is None

    r.edit(f_id, {"resize": {"at": window["middle"], "width": width + 0.6, "sill": 0.45, "height": 1.8}})
    assert len(Workspace.load(path).floor(f_id).edits.resized) == 1
    _reconvert(path, f_id)
    after = r.floor(f_id)
    w = next(d for d in after["doors"] if d["id"] == window["id"])
    assert abs(w["width"] - (width + 0.6)) < 1e-6 and abs(LineString(w["span"]).length - (width + 0.6)) < 1e-3
    assert w["sill"] == 0.45 and w["height"] == 1.8 and w["resize"] == {"width": round(width + 0.6, 3), "sill": 0.45, "height": 1.8}
    assert LineString(w["span"]).interpolate(0.5, normalized=True).distance(shape({"type": "Point", "coordinates": window["middle"]})) < 1e-3
    assert abs((walls - _wall_area(after)) - 0.6 * thickness) < 0.05 * thickness + 0.01  # the gap is wider

    r.edit(f_id, {"resize": {"at": w["middle"], "width": width - 0.4, "sill": None, "height": None}})
    assert len(Workspace.load(path).floor(f_id).edits.resized) == 1  # replaced, not added
    _reconvert(path, f_id)
    narrow = r.floor(f_id)
    w = next(d for d in narrow["doors"] if d["id"] == window["id"])
    assert abs(w["width"] - (width - 0.4)) < 1e-6 and w["sill"] != 0.45
    assert abs((_wall_area(narrow) - walls) - 0.4 * thickness) < 0.05 * thickness + 0.01  # wall again where it was open

    r.edit(f_id, {"resize": {"at": w["middle"], "width": None, "sill": None, "height": None}})
    assert Workspace.load(path).floor(f_id).edits.resized == []
    _reconvert(path, f_id)
    back = r.floor(f_id)
    w = next(d for d in back["doors"] if d["id"] == window["id"])
    assert abs(w["width"] - width) < 1e-6 and w["resize"] is None and abs(_wall_area(back) - walls) < 1e-3


def test_a_door_given_another_width_takes_its_leaf_with_it(review):
    r, path, f_id = review
    door = next(d for d in r.floor(f_id)["doors"] if d["type"] == "door" and d["span"] and d["swings"])
    leaf = lambda d: LineString(d["swings"][0]).length  # noqa: E731
    assert abs(leaf(door) - door["width"]) < 0.05
    r.edit(f_id, {"resize": {"at": door["middle"], "width": 1.2}})
    _reconvert(path, f_id)
    d = next(x for x in r.floor(f_id)["doors"] if x["id"] == door["id"])
    assert abs(d["width"] - 1.2) < 1e-6 and abs(leaf(d) - 1.2) < 0.05
    hinge = d["swings"][0][0]
    assert min(LineString([hinge, end]).length for end in d["span"]) < 1e-3  # hinged on a jamb


def test_an_opening_drawn_in_review_is_given_another_size(review):
    r, path, f_id = review
    floor = r.floor(f_id)
    rooms = sorted((s for s in floor["spaces"] if s["kind"] == "space"), key=lambda s: -s["area"])
    a, b = rooms[0], next(s for s in rooms[1:] if shape(s["geometry"]).distance(shape(rooms[0]["geometry"])) < 0.5)
    c = shape(a["geometry"]).buffer(0.3).intersection(shape(b["geometry"]).buffer(0.3)).centroid
    between = shape(a["geometry"]).buffer(0.3).intersection(shape(b["geometry"]).buffer(0.3))
    vertical = (between.bounds[3] - between.bounds[1]) > (between.bounds[2] - between.bounds[0])
    span = [[c.x, c.y - 0.45], [c.x, c.y + 0.45]] if vertical else [[c.x - 0.45, c.y], [c.x + 0.45, c.y]]
    r.edit(f_id, {"add": {"opening": {"type": "door", "span": span}}})
    r.edit(f_id, {"resize": {"at": [c.x, c.y], "width": 1.3, "height": 2.4}})
    drawn = Workspace.load(path).floor(f_id).edits
    assert drawn.resized == [] and abs(LineString(drawn.openings[0].span).length - 1.3) < 1e-6 and drawn.openings[0].height == 2.4
    _reconvert(path, f_id)
    d = next(x for x in r.floor(f_id)["doors"] if x["drawn"])
    assert abs(d["width"] - 1.3) < 1e-3 and d["height"] == 2.4

    with pytest.raises(ValueError):
        r.edit(f_id, {"resize": {"at": [c.x, c.y], "width": 0.1}})
    with pytest.raises(ValueError):
        r.edit(f_id, {"resize": {"at": [c.x, c.y], "sill": "high"}})
    with pytest.raises(NotFound):
        r.edit(f_id, {"resize": {"at": [c.x + 500, c.y + 500], "width": 1.0}})


def test_a_package_opened_gets_its_drawing_back_and_keeps_its_ids(studio, tmp_path):
    # A sheet with two floors side by side (the second moved onto the first), exported
    # as a package; the project is opened from the package alone (no drawing), and the
    # sheet added again to floor 1: lined up on the walls it has, its rooms keep their
    # IDs and what was set on them.
    base, app = studio
    _, created = call(f"{base}/api/projects", {"name": "Offices"})
    code = created["code"]
    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (100.0, 50.0), "GROUND FLOOR PLAN"),
                                             (office_floor(1), (170.0, 50.0), "FIRST FLOOR PLAN")])
    call(f"{base}/api/projects/{code}/drawings/sheet.dxf?private=0", raw=(tmp_path / "sheet.dxf").read_bytes())
    _, job = call(f"{base}/api/projects/{code}/drawings/sheet.dxf/plans", {})
    plans = sorted(wait(base, job)["plans"], key=lambda p: p["region"][0])
    _, job = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        {"index": p["index"], "title": p["title"], "region": p["region"], "building": "Main", "ordinal": n}
        for n, p in enumerate(plans)]})
    wait(base, job)
    ws = Workspace.load(app.path(code))
    b_id = f"{ws.id}-{ws.locations[0].code}-{ws.locations[0].buildings[0].code}"
    first = next(f for f in ws.building(b_id).floors if f.ordinal == 1)
    first_id = f"{b_id}-{first.code}"
    assert abs(first.source.offset[0]) > 10  # moved onto the ground floor
    rooms = {r.id for r in ws.floor_objects(first_id) if r.kind == "space"}
    named = next(r for r in ws.floor_objects(first_id) if r.kind == "space")
    call(f"{base}/api/projects/{code}/objects/{named.id}", {"correction": {"name": "Board room"}})

    with urllib.request.urlopen(f"{base}/api/projects/{code}/preview.storeypath") as res:
        package = res.read()
    call(f"{base}/api/projects/{code}/delete", {"confirm": "Offices"})
    status, opened = call(f"{base}/api/open", raw=package)
    assert status == 200 and opened["how"] == "package" and opened["code"] == code
    status, again = call(f"{base}/api/open", raw=package)
    assert status == 409 and again["name"] == "Offices"

    call(f"{base}/api/projects/{code}/drawings/sheet.dxf?private=0", raw=(tmp_path / "sheet.dxf").read_bytes())
    _, job = call(f"{base}/api/projects/{code}/drawings/sheet.dxf/plans", {})
    p = sorted(wait(base, job)["plans"], key=lambda p: p["region"][0])[1]
    status, job = call(f"{base}/api/projects/{code}/floors", {"drawing": "sheet.dxf", "plans": [
        {"index": p["index"], "title": p["title"], "region": p["region"], "building_id": b_id, "ordinal": 1, "replace": True}]})
    assert status == 200, job
    wait(base, job)
    ws = Workspace.load(app.path(code))
    assert {r.id for r in ws.floor_objects(first_id) if r.kind == "space"} == rooms  # the same rooms, the same IDs
    assert ws.effective(ws.objects[named.id])["name"] == "Board room"  # kept over the drawing's reading
    assert ws.floor(first_id).source.path.endswith("sheet.dxf")


def test_buildings_on_the_site_plan(studio, tmp_path):
    # A second building drawn at the same origin as the first is put beside it; one
    # drawn in the site's coordinates, apart, stays; a person moves and turns them,
    # lays them out side by side, and puts the site on the map.
    from storeypath.export import site_footprint, site_positions

    base, app = studio
    _, created = call(f"{base}/api/projects", {"name": "Campus"})
    code = created["code"]
    for name, origin in (("a.dxf", (100.0, 50.0)), ("b.dxf", (100.0, 50.0)), ("c.dxf", (100.0, 120.0))):
        write_floor_dxf(tmp_path / name, office_floor(0), origin=origin, title="GROUND FLOOR PLAN")

    def add(name, building):
        call(f"{base}/api/projects/{code}/drawings/{name}?private=0", raw=(tmp_path / name).read_bytes())
        _, job = call(f"{base}/api/projects/{code}/drawings/{name}/plans", {})
        plan = max(wait(base, job)["plans"], key=lambda x: x["size"][0] * x["size"][1])
        _, job = call(f"{base}/api/projects/{code}/floors", {"drawing": name, "plans": [
            {"index": plan["index"], "title": plan["title"], "region": plan["region"], "building": building, "ordinal": 0}]})
        wait(base, job)

    add("a.dxf", "Admin")
    add("b.dxf", "Clinic")
    add("c.dxf", "Labs")

    def site():
        ws = Workspace.load(app.path(code))
        loc = ws.locations[0]
        pos = site_positions(loc)
        return ws, loc, {b.name: site_footprint(b, pos[b.code]) for b in loc.buildings}

    ws, loc, fps = site()
    assert fps["Admin"].intersection(fps["Clinic"]).area < 1e-6  # put beside it
    assert next(b for b in loc.buildings if b.name == "Labs").site is None  # apart as drawn: stays
    assert fps["Labs"].intersection(fps["Admin"]).area < 1e-6

    _, p = call(f"{base}/api/projects/{code}")
    b = next(b for b in p["locations"][0]["buildings"] if b["name"] == "Labs")
    assert b["footprint"] and not b["site"]["set"]
    status, moved = call(f"{base}/api/projects/{code}/buildings/{b['id']}/site", {"x": 500, "y": -20, "rotation": 370})
    assert status == 200 and moved["site"]["rotation"] == 10
    ws, loc, fps = site()
    labs = next(x for x in loc.buildings if x.name == "Labs")
    assert (labs.site.x, labs.site.y) == (500, -20) and all(x.site is not None for x in loc.buildings)  # the others stay put
    status, error = call(f"{base}/api/projects/{code}/buildings/{b['id']}/site", {"x": "far", "y": 0, "rotation": 0})
    assert status == 400

    status, _ = call(f"{base}/api/projects/{code}/locations/{p['locations'][0]['id']}/arrange", {})
    ws, loc, fps = site()
    order = sorted(fps.items(), key=lambda kv: kv[1].bounds[0])
    assert [n for n, _ in order] == ["Admin", "Clinic", "Labs"]  # by code: ADMIN, CLINIC, LABS
    for (_, left), (_, right) in zip(order, order[1:]):
        assert right.bounds[0] - left.bounds[2] >= 9.99

    status, placed = call(f"{base}/api/projects/{code}/locations/{p['locations'][0]['id']}/placement",
                          {"lat": 25.28, "lon": 51.53, "bearing": 10})
    assert status == 200 and placed["placement"]["bearing"] == 10
    with urllib.request.urlopen(f"{base}/api/projects/{code}/preview.storeypath") as res:
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(res.read())).read("manifest.json"))
    assert all(pl["placed"] and abs(pl["lat"] - 25.28) < 1e-9 for pl in manifest["placements"].values())


def test_a_building_whose_first_reading_failed_is_read_again_and_put_beside(studio, tmp_path, monkeypatch):
    # The drawing of a second building, at the first one's origin, fails to read; it
    # is read again from its floor, and then stands beside the first, not on it.
    import storeypath.convert
    from storeypath.export import site_footprint, site_positions

    base, app = studio
    _, created = call(f"{base}/api/projects", {"name": "Campus"})
    code = created["code"]
    for name in ("a.dxf", "b.dxf"):
        write_floor_dxf(tmp_path / name, office_floor(0), origin=(100.0, 50.0), title="GROUND FLOOR PLAN")

    def add(name, building):
        call(f"{base}/api/projects/{code}/drawings/{name}?private=0", raw=(tmp_path / name).read_bytes())
        _, job = call(f"{base}/api/projects/{code}/drawings/{name}/plans", {})
        plan = max(wait(base, job)["plans"], key=lambda x: x["size"][0] * x["size"][1])
        _, job = call(f"{base}/api/projects/{code}/floors", {"drawing": name, "plans": [
            {"index": plan["index"], "title": plan["title"], "region": plan["region"], "building": building, "ordinal": 0}]})
        while job["state"] in ("waiting", "running"):
            time.sleep(0.1)
            _, job = call(f"{base}/api/jobs/{job['id']}")
        return job

    assert add("a.dxf", "Admin")["state"] == "done"
    read = storeypath.convert.convert_floor
    monkeypatch.setattr(storeypath.convert, "convert_floor", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("could not locate vertex")))
    assert add("b.dxf", "Clinic")["state"] == "failed"
    monkeypatch.setattr(storeypath.convert, "convert_floor", read)

    _, p = call(f"{base}/api/projects/{code}")
    clinic = next(b for b in p["locations"][0]["buildings"] if b["name"] == "Clinic")
    assert not clinic["floors"][0]["converted"]
    _, job = call(f"{base}/api/projects/{code}/floors/{clinic['floors'][0]['id']}/convert", {})
    wait(base, job)
    ws = Workspace.load(app.path(code))
    loc = ws.locations[0]
    pos = site_positions(loc)
    fps = {b.name: site_footprint(b, pos[b.code]) for b in loc.buildings}
    assert fps["Clinic"] is not None and fps["Admin"].intersection(fps["Clinic"]).area < 1e-6


def test_a_room_with_an_empty_shape_does_not_stop_the_floor_showing(review):
    # An older Studio could keep a sliver as a room with nothing in its outline.
    r, path, f_id = review
    ws = Workspace.load(path)
    room = next(o for o in ws.floor_objects(f_id) if o.kind == "space")
    room.geometry = {"type": "Polygon", "coordinates": []}
    ws.save(path)
    floor = r.floor(f_id)
    assert len(floor["spaces"]) == 23 and room.id not in {s["id"] for s in floor["spaces"]}


def test_a_package_of_one_building(studio, tmp_path):
    base, app = studio
    _, created = call(f"{base}/api/projects", {"name": "Campus"})
    code = created["code"]
    for name, building, origin in (("a.dxf", "Admin", (100.0, 50.0)), ("c.dxf", "Labs", (100.0, 120.0))):
        write_floor_dxf(tmp_path / name, office_floor(0), origin=origin, title="GROUND FLOOR PLAN")
        call(f"{base}/api/projects/{code}/drawings/{name}?private=0", raw=(tmp_path / name).read_bytes())
        _, job = call(f"{base}/api/projects/{code}/drawings/{name}/plans", {})
        plan = max(wait(base, job)["plans"], key=lambda x: x["size"][0] * x["size"][1])
        _, job = call(f"{base}/api/projects/{code}/floors", {"drawing": name, "plans": [
            {"index": plan["index"], "title": plan["title"], "region": plan["region"], "building": building, "ordinal": 0}]})
        wait(base, job)
    _, p = call(f"{base}/api/projects/{code}")
    labs = next(b["id"] for b in p["locations"][0]["buildings"] if b["name"] == "Labs")

    _, job = call(f"{base}/api/projects/{code}/export", {"buildings": [labs]})
    done = wait(base, job)
    assert done["file"] == f"{code}-001-LABS.storeypath" and done["counts"]["buildings"] == 1
    with urllib.request.urlopen(f"{base}/api/projects/{code}/exports/{done['file']}") as res:
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(res.read())).read("manifest.json"))
    assert manifest["scope"] == {"buildings": [labs]} and manifest["format_version"] == FORMAT_VERSION

    with urllib.request.urlopen(f"{base}/api/projects/{code}/preview.storeypath?building={labs}") as res:
        assert json.loads(zipfile.ZipFile(io.BytesIO(res.read())).read("manifest.json"))["counts"]["buildings"] == 1
    status, _ = call(f"{base}/api/projects/{code}/preview.storeypath?building={code}-SITE-NOPE")
    assert status == 404
    status, _ = call(f"{base}/api/projects/{code}/export", {"buildings": labs})
    assert status == 400


def test_a_space_studio_set_aside_is_restored_by_a_person(review):
    # Vision set a hall aside as not a room; a person who knows it is inside the
    # building restores it, and deleting it again puts it back as detected.
    r, path, f_id = review
    ws = Workspace.load(path)
    hall = next(o for o in ws.floor_objects(f_id) if o.kind == "space")
    hall.detected_ignored = True
    ws.save(path)
    restored = r.correct(hall.id, {"ignored": False})
    assert restored["ignored"] is False and Workspace.load(path).effective(Workspace.load(path).objects[hall.id])["ignored"] is False
    again = r.correct(hall.id, {"ignored": True})
    ws = Workspace.load(path)
    assert again["ignored"] is True and ws.effective(ws.objects[hall.id])["ignored"] is True


def test_a_space_drawn_where_the_drawing_encloses_none(review):
    # A colonnade between columns: no walls, so no space is found there. A person
    # draws its outline in review; it becomes a space through every re-read, never
    # over a room (what a room covers is left out), and can be taken away again.
    from storeypath.convert import DRAWN_NOTE, convert_floor

    r, path, f_id = review
    ws = Workspace.load(path)
    x0, y0, x1, y1 = shape(ws.floor(f_id).outline).bounds
    outside = [[x1 + 1, y0], [x1 + 5, y0], [x1 + 5, y0 + 4], [x1 + 1, y0 + 4]]  # beyond the walls
    over = [[x1 - 2, y0 + 6], [x1 + 3, y0 + 6], [x1 + 3, y0 + 9], [x1 - 2, y0 + 9]]  # 2 m of it over rooms
    r.edit(f_id, {"add": {"space": outside}})
    r.edit(f_id, {"add": {"space": over}})
    with pytest.raises(ValueError, match="at least"):
        r.edit(f_id, {"add": {"space": [[0, 0], [0.5, 0], [0.5, 0.5]]}})
    with pytest.raises(ValueError, match="cross itself"):
        r.edit(f_id, {"add": {"space": [[0, 0], [4, 4], [4, 0], [0, 4]]}})

    ws = Workspace.load(path)
    convert_floor(ws, f_id, path.parent)
    drawn = [o for o in ws.floor_objects(f_id) if o.kind == "space" and DRAWN_NOTE in o.issues]
    assert len(drawn) == 2
    areas = sorted(round(shape(o.geometry).area, 1) for o in drawn)
    assert areas[1] == 16.0 and 0 < areas[0] < 15.0  # the one over rooms without what they cover
    whole = next(o for o in drawn if round(shape(o.geometry).area, 1) == 16.0)
    assert shape(ws.floor(f_id).outline).contains(shape(whole.geometry).representative_point())  # the floor takes it in
    ids = {o.id for o in drawn}
    convert_floor(ws, f_id, path.parent)
    assert {o.id for o in ws.floor_objects(f_id) if o.kind == "space" and DRAWN_NOTE in o.issues} == ids  # kept, same IDs
    ws.save(path)

    r.edit(f_id, {"remove": {"at": [x1 + 3, y0 + 2]}})  # inside the first: taken away
    assert len(Workspace.load(path).floor(f_id).edits.spaces) == 1
