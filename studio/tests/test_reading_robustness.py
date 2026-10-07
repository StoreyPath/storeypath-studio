"""Reading drawings that are broken, read wrong or answered wrong: one bad piece
costs that piece, never the floor's IDs or the rest of the conversion."""

from pathlib import Path

import ezdxf
from typer.testing import CliRunner

from storeypath.cli import app
from storeypath.convert import convert_floor
from storeypath.samples import office_floor, write_floor_dxf
from storeypath.workspace import SourceDrawing, Workspace

runner = CliRunner()


def _project(d: Path, drawing: str, profile: str = "ncs", ordinal: int = 2):
    ws = Workspace.new("T")
    loc = ws.add_location("S", "S")
    b = ws.add_building(loc, "B", "B")
    f = ws.add_floor(b, ordinal, source=SourceDrawing(path=drawing, profile=profile))
    return ws, f


def _active(ws, f):
    return {r.id for r in ws.floor_objects(f) if r.kind in ("space", "zone")}


def _renamed_layers(src: Path, out: Path) -> None:
    """The next revision of a drawing, its layers renamed by the architect."""
    doc = ezdxf.readfile(src)
    rename = {"A-WALL": "WALL-NEW", "A-AREA": "AREA-NEW", "A-AREA-IDEN": "ROOMTXT-NEW"}
    for e in doc.modelspace():
        e.dxf.layer = rename.get(e.dxf.layer, e.dxf.layer)
    doc.saveas(out)


# ---- a bad read does not retire a floor ------------------------------------------


def test_a_read_that_finds_no_rooms_keeps_the_floor_and_its_ids(tmp_path):
    write_floor_dxf(tmp_path / "rev1.dxf", office_floor(2))
    _renamed_layers(tmp_path / "rev1.dxf", tmp_path / "rev2.dxf")
    ws, f = _project(tmp_path, "rev1.dxf")
    first = convert_floor(ws, f, tmp_path)
    rooms, sha = _active(ws, f), ws.floor(f).source.sha256
    assert len(rooms) > 10 and not first.held

    ws.floor(f).source.path = "rev2.dxf"  # read with the old layer names: nothing found
    report = convert_floor(ws, f, tmp_path)
    assert report.held and report.retired == [] and report.added == []
    assert any(w.startswith("nothing was changed") and "force" in w for w in report.warnings)
    assert "not changed" in report.summary()
    assert _active(ws, f) == rooms and ws.floor(f).source.sha256 == sha

    ws.floor(f).source.path = "rev1.dxf"  # the profile fixed, or the old drawing back: the same rooms
    again = convert_floor(ws, f, tmp_path)
    assert not again.held and again.added == [] and again.retired == [] and _active(ws, f) == rooms


def test_a_read_that_would_retire_most_rooms_is_applied_only_when_forced(tmp_path):
    write_floor_dxf(tmp_path / "rev1.dxf", office_floor(2))
    # the same floor drawn 60 m away (a wrong offset, a moved origin)
    write_floor_dxf(tmp_path / "moved.dxf", office_floor(2), origin=(185.0, 48.0))
    ws, f = _project(tmp_path, "rev1.dxf")
    convert_floor(ws, f, tmp_path)
    rooms = _active(ws, f)

    ws.floor(f).source.path = "moved.dxf"
    held = convert_floor(ws, f, tmp_path)
    assert held.held and _active(ws, f) == rooms
    forced = convert_floor(ws, f, tmp_path, force=True)
    assert not forced.held and rooms <= set(forced.retired) and not (_active(ws, f) & rooms)


def test_the_cli_holds_a_bad_read_back_unless_forced(tmp_path):
    write_floor_dxf(tmp_path / "rev1.dxf", office_floor(2))
    _renamed_layers(tmp_path / "rev1.dxf", tmp_path / "rev2.dxf")
    ws_file = tmp_path / "p.spproj"
    ws, f = _project(tmp_path, "rev1.dxf")
    convert_floor(ws, f, tmp_path)
    rooms = _active(ws, f)
    ws.floor(f).source.path = "rev2.dxf"
    ws.save(ws_file)

    args = ["convert", str(ws_file), "--no-model", "--no-symbols", "--no-vision"]
    held = runner.invoke(app, args)
    assert held.exit_code == 1 and "not changed" in held.output and "--force" in held.output
    assert _active(Workspace.load(ws_file), f) == rooms
    forced = runner.invoke(app, [*args, "--force"])
    assert forced.exit_code == 0, forced.output
    assert not _active(Workspace.load(ws_file), f)
