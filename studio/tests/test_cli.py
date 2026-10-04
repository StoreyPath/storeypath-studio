from typer.testing import CliRunner

from storeypath.cli import app
from storeypath.cad import read_drawing
from storeypath.profile import load_profile
from storeypath.samples import office_floor, write_floor_dxf, write_sheet_dxf
from storeypath.sheets import align, find_views, floor_walls
from storeypath.workspace import Workspace

runner = CliRunner()


def run(*args):
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result.output.strip()


def test_full_workflow(tmp_path):
    ws_file = tmp_path / "proj.spproj"
    write_floor_dxf(tmp_path / "plans-l1.dxf", office_floor(1))

    run("new", ws_file, "--name", "CLI Project")
    code = Workspace.load(ws_file).id
    loc = run("add-location", ws_file, "SITE", "--name", "Site")
    assert loc == f"{code}-SITE"
    b = run("add-building", ws_file, loc, "HQ", "--name", "HQ building")
    run("place", ws_file, b, "--lat", "50", "--lon", "10", "--x", "125000", "--y", "48000", "--units", "mm")
    f = run("add-floor", ws_file, b, tmp_path / "plans-l1.dxf", "--ordinal", "1")
    assert f == f"{code}-SITE-HQ-F01"

    out = run("convert", ws_file)
    assert "62 new" in out  # 24 spaces + 24 doors + 14 windows

    review = dict(line.split()[:2] for line in run("list", ws_file, "--review").splitlines())
    assert sorted(review.values()) == ["elevator", "unspecified"]  # unlabelled elevator, unnamed room
    for object_id, type_ in review.items():
        if type_ == "unspecified":
            run("fix", ws_file, object_id, "--type", "office", "--name", "QUIET ROOM")
        else:
            run("fix", ws_file, object_id, "--name", "ELEVATOR 2")
    assert run("list", ws_file, "--review") == ""

    out = run("export", ws_file, "-o", tmp_path / "proj.storeypath")
    assert "export #1" in out
    assert "valid" in run("validate", tmp_path / "proj.storeypath")

    again = run("convert", ws_file)
    assert "62 kept, 0 new, 0 retired" in again


def test_errors_are_reported(tmp_path):
    ws_file = tmp_path / "p.spproj"
    run("new", ws_file, "--name", "P")
    result = runner.invoke(app, ["add-location", str(ws_file), "bad-code", "--name", "x"])
    assert result.exit_code == 1
    result = runner.invoke(app, ["new", str(ws_file), "--name", "again"])
    assert result.exit_code == 1 and "already exists" in result.output


def _two_floors(path):
    """Ground and first floor drawn side by side, the first 70 m right and 2.5 m up."""
    write_sheet_dxf(path, [(office_floor(0), (100.0, 50.0), "GROUND FLOOR PLAN"),
                           (office_floor(1), (170.0, 52.5), "FIRST FLOOR PLAN")], area_outlines=False)


def test_plans_side_by_side_are_found_and_aligned(tmp_path):
    _two_floors(tmp_path / "all.dxf")
    doc, profile = read_drawing(tmp_path / "all.dxf"), load_profile("ncs")
    views = find_views(doc, profile, 0.001)
    assert [(v.title, v.ordinal) for v in views] == [("GROUND FLOOR PLAN", 0), ("FIRST FLOOR PLAN", 1)]
    (tx, ty), overlap = align(floor_walls(doc, profile, 0.001, views[0].region),
                              floor_walls(doc, profile, 0.001, views[1].region))
    assert abs(tx - 70) < 0.03 and abs(ty - 2.5) < 0.03 and overlap > 0.9


def test_building_from_one_drawing(tmp_path):
    ws_file = tmp_path / "p.spproj"
    _two_floors(tmp_path / "all.dxf")
    run("new", ws_file, "--name", "Sheets")
    b = run("add-building", ws_file, run("add-location", ws_file, "SITE", "--name", "Site"), "HQ", "--name", "HQ")
    assert "GROUND FLOOR PLAN" in run("views", tmp_path / "all.dxf")
    run("add-floor", ws_file, b, tmp_path / "all.dxf", "--ordinal", "0", "--view", "ground")
    run("add-floor", ws_file, b, tmp_path / "all.dxf", "--ordinal", "1", "--view", "2")
    assert "shifted 70.0" in run("align", ws_file, b)
    run("convert", ws_file)
    ws = Workspace.load(ws_file)
    elevators = {}
    for r in ws.objects.values():
        if r.type == "elevator":
            elevators.setdefault(r.id.rsplit("-", 2)[1], set()).add(r.id.rsplit("-", 1)[1])
    # stacked correctly, the elevators on both floors share their object codes
    assert len(elevators) == 2 and elevators["F00"] == elevators["F01"]
