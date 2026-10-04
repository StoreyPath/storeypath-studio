from typer.testing import CliRunner

from storeypath.cli import app
from storeypath.samples import office_floor, write_floor_dxf
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
    assert "48 new" in out  # 24 spaces + 24 doors

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
    assert "48 kept, 0 new, 0 retired" in again


def test_errors_are_reported(tmp_path):
    ws_file = tmp_path / "p.spproj"
    run("new", ws_file, "--name", "P")
    result = runner.invoke(app, ["add-location", str(ws_file), "bad-code", "--name", "x"])
    assert result.exit_code == 1
    result = runner.invoke(app, ["new", str(ws_file), "--name", "again"])
    assert result.exit_code == 1 and "already exists" in result.output
