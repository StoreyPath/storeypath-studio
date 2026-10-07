"""Reading drawings that are broken, read wrong or answered wrong: one bad piece
costs that piece, never the floor's IDs or the rest of the conversion."""

import io
import itertools
import json
from pathlib import Path

import ezdxf
import pytest
from ezdxf.enums import TextEntityAlignment
from typer.testing import CliRunner

import storeypath.llm as llm
import storeypath.vision as vision
from storeypath.cli import app
from storeypath.convert import convert_floor
from storeypath.llm import ModelUnavailable
from storeypath.samples import office_floor, simple_office, write_floor_dxf
from storeypath.types import SpaceType
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


# ---- one floor's failure is that floor's ---------------------------------------


def _three_floors(d: Path) -> tuple[Path, list[str]]:
    ws = Workspace.new("T")
    b = ws.add_building(ws.add_location("S", "S"), "B", "B")
    floors = []
    for n in range(3):
        write_floor_dxf(d / f"f{n}.dxf", office_floor(n))
        floors.append(ws.add_floor(b, n, source=SourceDrawing(path=f"f{n}.dxf", profile="ncs")))
    ws.save(d / "p.spproj")
    return d / "p.spproj", floors


def _failing_on(floor_id: str, real):
    def convert(ws, fid, *a, **k):
        if fid == floor_id:  # half-way through: an answer kept, the floor's objects in pieces
            ws.vision["seen"] = {"outline": "exactly one room", "type": "office", "shape": "POINT (0 0)"}
            ws.objects.clear()
            raise RuntimeError("something unforeseen")
        return real(ws, fid, *a, **k)

    return convert


def _rooms_by_floor(ws_file: Path, floors: list[str]) -> list[int]:
    ws = Workspace.load(ws_file)
    return [sum(1 for r in ws.floor_objects(f) if r.kind == "space") for f in floors]


def test_the_cli_converts_the_other_floors_when_one_fails(tmp_path, monkeypatch):
    import storeypath.cli as cli

    ws_file, floors = _three_floors(tmp_path)
    monkeypatch.setattr(cli, "convert_floor", _failing_on(floors[1], convert_floor))
    result = runner.invoke(app, ["convert", str(ws_file), "--no-model", "--no-symbols", "--no-vision"])
    assert result.exit_code == 1 and f"{floors[1]}: not converted: RuntimeError: something unforeseen" in result.output
    first, failed, last = _rooms_by_floor(ws_file, floors)
    assert first > 10 and failed == 0 and last > 10
    assert "seen" in Workspace.load(ws_file).vision  # what the models answered is kept


def test_the_studio_converts_the_other_floors_when_one_fails(tmp_path, monkeypatch):
    import storeypath.convert
    from storeypath.server import Job, Studio

    class NoModel:
        name, ready = "none", False

        def available(self):
            return False

    data = tmp_path / "data"
    data.mkdir()
    ws_file, floors = _three_floors(data)
    monkeypatch.setattr(storeypath.convert, "convert_floor", _failing_on(floors[1], convert_floor))
    studio = Studio(data, model=NoModel(), warm=False, vision=vision.VisionModel(url=""))
    job = Job("j", "Converting")
    with pytest.raises(ValueError, match=f"not converted: {floors[1]}: RuntimeError: something unforeseen"):
        studio._convert(ws_file, floors, job)  # the job fails…
    first, failed, last = _rooms_by_floor(ws_file, floors)  # …the other floors converted
    assert first > 10 and failed == 0 and last > 10


# ---- lining floors up again ---------------------------------------------------------


def _cli(*args) -> str:
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result.output.strip()


def test_aligning_again_moves_only_the_floors_not_converted(tmp_path):
    # The README's way: floors from one sheet, aligned, converted. Later a basement
    # is added and align run again: the floors converted keep their frames and IDs.
    from storeypath.samples import write_sheet_dxf

    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (125.0, 48.0), "GROUND FLOOR PLAN"),
                                             (office_floor(1), (185.0, 48.0), "FIRST FLOOR PLAN"),
                                             (office_floor(-1), (245.0, 48.0), "BASEMENT FLOOR PLAN")])
    ws_file = tmp_path / "p.spproj"
    _cli("new", ws_file, "--name", "P")
    loc = _cli("add-location", ws_file, "SITE", "--name", "S")
    b = _cli("add-building", ws_file, loc, "HQ", "--name", "HQ")
    _cli("add-floor", ws_file, b, tmp_path / "sheet.dxf", "--ordinal", "0", "--view", "ground", "--no-model")
    _cli("add-floor", ws_file, b, tmp_path / "sheet.dxf", "--ordinal", "1", "--view", "first", "--no-model")
    _cli("align", ws_file, b)
    _cli("convert", ws_file, "--no-model", "--no-symbols", "--no-vision")
    ws = Workspace.load(ws_file)
    offsets = {f.code: f.source.offset for f in ws.building(b).floors}
    rooms = {i for i, r in ws.objects.items() if r.kind == "space" and r.status == "active"}

    _cli("add-floor", ws_file, b, tmp_path / "sheet.dxf", "--ordinal", "-1", "--view", "basement", "--no-model")
    out = _cli("align", ws_file, b)
    assert "converted already: kept where it is" in out
    _cli("convert", ws_file, "--no-model", "--no-symbols", "--no-vision")
    ws = Workspace.load(ws_file)
    floors = {f.code: f for f in ws.building(b).floors}
    assert all(floors[code].source.offset == offset for code, offset in offsets.items())
    assert all(ws.objects[i].status == "active" for i in rooms)
    # the basement stands where the ground floor does: 120 m to its left on the sheet
    ground, basement = (floors[c].source.offset or (0.0, 0.0) for c in ("F00", "B01"))
    assert abs(basement[0] - ground[0] - 120000) < 50 and abs(basement[1] - ground[1]) < 50


def test_floors_drawn_in_other_units_are_lined_up_in_metres(tmp_path):
    # The reference floor is drawn in millimetres and already moved; the other is
    # drawn in metres elsewhere: its offset, in its own units, puts it on the reference.
    write_floor_dxf(tmp_path / "mm.dxf", office_floor(0), origin=(125.0, 48.0))
    write_floor_dxf(tmp_path / "m.dxf", office_floor(1), origin=(30.0, 10.0))
    doc = ezdxf.readfile(tmp_path / "m.dxf")
    for e in doc.modelspace():
        e.scale_uniform(0.001)
    doc.units = ezdxf.units.M
    doc.saveas(tmp_path / "m.dxf")
    ws = Workspace.new("T")
    b = ws.add_building(ws.add_location("S", "S"), "B", "B")
    ws.add_floor(b, 0, source=SourceDrawing(path="mm.dxf", profile="ncs", units="mm", offset=(125000.0, 48000.0)))
    f1 = ws.add_floor(b, 1, source=SourceDrawing(path="m.dxf", profile="ncs", units="m"))
    ws.save(tmp_path / "p.spproj")
    _cli("align", tmp_path / "p.spproj", b)
    x, y = Workspace.load(tmp_path / "p.spproj").floor(f1).source.offset
    assert abs(x - 30.0) < 0.05 and abs(y - 10.0) < 0.05


def test_a_stray_wall_far_off_does_not_spread_the_alignment_grid():
    import time

    from shapely.affinity import translate
    from shapely.geometry import box
    from shapely.ops import unary_union

    from storeypath.sheets import align

    plan = unary_union([box(0, 0, 50, 30).difference(box(0.2, 0.2, 49.8, 29.8)), box(20, 0, 20.2, 30)])
    stray = box(20000, 20000, 20000.2, 20000.2)  # a line at a georeferenced drawing's origin
    started = time.monotonic()
    (tx, ty), overlap = align(plan, unary_union([translate(plan, 7, -3), stray]))
    assert abs(tx - 7) < 0.15 and abs(ty + 3) < 0.15 and overlap > 0.8
    assert time.monotonic() - started < 10


def test_a_very_large_plan_is_aligned_on_a_coarser_grid():
    from shapely.affinity import translate
    from shapely.geometry import box
    from shapely.ops import unary_union

    from storeypath.sheets import align

    walls = [box(0, 0, 1500, 900).difference(box(0.3, 0.3, 1499.7, 899.7))]
    walls += [box(x, 0, x + 0.3, 900) for x in range(100, 1500, 137)]
    plan = unary_union(walls)
    (tx, ty), overlap = align(plan, translate(plan, 12.0, 4.0))
    assert abs(tx - 12) < 0.5 and abs(ty - 4) < 0.5 and overlap > 0.5


# ---- room outlines ------------------------------------------------------------------


def _outlines_drawing():
    doc = ezdxf.new("R2018", setup=True)
    doc.units = ezdxf.units.M
    for layer in ("A-AREA", "A-AREA-IDEN"):
        doc.layers.add(layer)
    return doc


def _room(doc, x0, y0, x1, y1, name=None):
    m = doc.modelspace()
    m.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "A-AREA"})
    if name:
        m.add_text(name, dxfattribs={"layer": "A-AREA-IDEN", "height": 0.2}).set_placement(
            ((x0 + x1) / 2, (y0 + y1) / 2), align=TextEntityAlignment.MIDDLE_CENTER)


def _extract(doc):
    from storeypath.extract import extract_floor
    from storeypath.profile import load_profile

    return extract_floor(doc, load_profile("ncs"), "m")


def test_an_open_office_round_two_shafts_is_a_room_with_holes():
    doc = _outlines_drawing()
    _room(doc, 0, 0, 20, 12, "OPEN OFFICE 201")
    _room(doc, 3, 3, 4, 4, "SHAFT")
    _room(doc, 15, 8, 16, 9, "SHAFT")
    _room(doc, 20, 0, 26, 6, "OFFICE 202")
    _room(doc, 20, 6, 26, 12, "OFFICE 203")
    ex = _extract(doc)
    rooms = {(s.name, s.number): round(s.polygon.area, 1) for s in ex.spaces}
    assert rooms == {("OPEN OFFICE", "201"): 238.0, ("OFFICE", "202"): 36.0, ("OFFICE", "203"): 36.0, ("SHAFT", None): 1.0}
    assert sum(1 for s in ex.spaces if s.name == "SHAFT") == 2
    assert round(ex.outline.area) == 26 * 12


def test_an_outline_round_the_rooms_is_the_floors():
    doc = _outlines_drawing()
    _room(doc, 0, 0, 30, 10)  # the floor's outline, no label of its own
    _room(doc, 0, 0, 10, 10, "OFFICE 101")
    _room(doc, 10, 0, 20, 10, "OFFICE 102")
    _room(doc, 30, 0, 34, 4, "BALCONY")  # drawn outside it
    ex = _extract(doc)
    assert sorted(s.name for s in ex.spaces) == ["BALCONY", "OFFICE", "OFFICE"]
    assert round(ex.outline.area) == 30 * 10 + 16


def test_rooms_drawn_gross_and_net_are_one_room_each_and_no_ring():
    doc = _outlines_drawing()
    rooms = [(0, 0, 5, 4, "OFFICE 101"), (5, 0, 10, 4, "OFFICE 102"), (0, 4, 10, 6, "CORRIDOR C1"),
             (10, 0, 11.2, 1.2, "DUCT D1")]
    for x0, y0, x1, y1, name in rooms:
        _room(doc, x0, y0, x1, y1)  # to the walls' middle
        _room(doc, x0 + 0.1, y0 + 0.1, x1 - 0.1, y1 - 0.1, name)  # to their faces
    ex = _extract(doc)
    assert sorted((s.name, s.number) for s in ex.spaces) == [
        ("CORRIDOR", "C1"), ("DUCT", "D1"), ("OFFICE", "101"), ("OFFICE", "102")]
    assert all(not s.polygon.interiors for s in ex.spaces)


def test_cleaning_thousands_of_outlines_is_quick():
    import time

    from shapely.geometry import box

    from storeypath.extract import _clean_spaces
    from storeypath.profile import load_profile

    shapes = [(box(i % 50 * 3, i // 50 * 3, i % 50 * 3 + 2.5, i // 50 * 3 + 2.5), "A-AREA") for i in range(3000)]
    started = time.monotonic()
    kept, containers = _clean_spaces(shapes, load_profile("ncs"), [])
    assert len(kept) == 3000 and not containers and time.monotonic() - started < 10


# ---- lifts and stairs through the floors ---------------------------------------------


def test_a_lift_corrected_in_review_shares_its_code_with_the_floor_above(tmp_path):
    from shapely.affinity import translate
    from shapely.geometry import shape

    from storeypath.geometry import iou
    from storeypath.ids import parse_id
    from storeypath.workspace import Override

    lower = office_floor(2)
    lifts = [translate(c.shape, 125, 48) for c in lower if c.expected_type == "elevator"]
    for c in lower:  # the drawing of floor 2 does not say they are lifts
        if c.expected_type == "elevator":
            c.label, c.blocks = None, []
    write_floor_dxf(tmp_path / "l2.dxf", lower)
    write_floor_dxf(tmp_path / "l3.dxf", office_floor(3))
    ws = Workspace.new("T")
    b = ws.add_building(ws.add_location("S", "S"), "HQ", "HQ")
    f2 = ws.add_floor(b, 2, source=SourceDrawing(path="l2.dxf"))
    f3 = ws.add_floor(b, 3, source=SourceDrawing(path="l3.dxf"))
    convert_floor(ws, f2, tmp_path)
    corrected = 0
    for r in ws.floor_objects(f2):
        if r.kind == "space" and any(iou(shape(r.geometry), lift) > 0.8 for lift in lifts):
            assert r.type != "elevator"
            ws.overrides[r.id] = Override(type="elevator")  # a person says what it is
            corrected += 1
    assert corrected == len(lifts) > 0
    convert_floor(ws, f3, tmp_path)

    def codes(f):
        return sorted(parse_id(r.id).code for r in ws.floor_objects(f) if ws.effective(r)["type"] == "elevator")

    assert codes(f3) == codes(f2)


# ---- files that cannot be read, or only in part ------------------------------------


def _bad_files(d: Path) -> dict[str, bytes]:
    write_floor_dxf(d / "base.dxf", simple_office())
    base = (d / "base.dxf").read_bytes()
    return {
        "empty.dxf": b"",
        "garbage.dxf": bytes(range(256)) * 16,
        "truncated.dxf": base[: len(base) // 3],
        "text.dxf": b"hello world\nthis is not a dxf\n",
        "badfloat.dxf": base.replace(b"\n 10\n", b"\n 10\nNOTAFLOAT\n", 5),
        "utf16.dxf": base.decode("cp1252", "ignore").encode("utf-16"),
    }


def test_a_file_that_cannot_be_read_is_a_drawing_error(tmp_path):
    from storeypath.cad import DrawingError, read_drawing

    for name, data in _bad_files(tmp_path).items():
        (tmp_path / name).write_bytes(data)
        with pytest.raises(DrawingError, match="cannot read"):
            read_drawing(tmp_path / name)


def _fake_dwg2dxf(d: Path, monkeypatch, body: str) -> None:
    """A stand-in for LibreDWG's dwg2dxf on PATH: a shell script with ``body``
    ($OUT is where it writes the DXF)."""
    import os

    bin_dir = d / "bin"
    bin_dir.mkdir()
    script = bin_dir / "dwg2dxf"
    script.write_text(f'#!/bin/sh\nOUT="$3"\n{body}\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_a_dwg_converted_with_signs_of_damage_is_read_and_said(tmp_path, monkeypatch):
    from storeypath.cad import read_drawing, read_notes

    write_floor_dxf(tmp_path / "plan.dxf", simple_office())
    _fake_dwg2dxf(tmp_path, monkeypatch, f'cp "{tmp_path / "plan.dxf"}" "$OUT"\n'
                                         'echo "ERROR: Invalid specularmap.transmatrix size 16" >&2\n'
                                         'echo "Warning: check_CRC mismatch 23047-23112 = 65" >&2')
    (tmp_path / "plan.dwg").write_bytes(b"AC1032")
    notes = read_notes(read_drawing(tmp_path / "plan.dwg"))
    assert len(notes) == 1 and "plan.dwg looks damaged" in notes[0] and "CRC mismatch" in notes[0]


def test_a_dwg_converted_cleanly_has_nothing_to_say(tmp_path, monkeypatch):
    from storeypath.cad import read_drawing, read_notes

    write_floor_dxf(tmp_path / "plan.dxf", simple_office())
    _fake_dwg2dxf(tmp_path, monkeypatch, f'cp "{tmp_path / "plan.dxf"}" "$OUT"\n'
                                         'echo "ERROR: Invalid specularmap.transmatrix size 16" >&2')
    (tmp_path / "plan.dwg").write_bytes(b"AC1032")
    assert read_notes(read_drawing(tmp_path / "plan.dwg")) == []


def test_a_dwg_converter_that_hangs_is_stopped(tmp_path, monkeypatch):
    import storeypath.cad as cad

    _fake_dwg2dxf(tmp_path, monkeypatch, "sleep 30")
    monkeypatch.setattr(cad, "DWG_TIMEOUT_S", 1)
    (tmp_path / "plan.dwg").write_bytes(b"AC1032")
    with pytest.raises(cad.DrawingError, match="did not finish"):
        cad.read_drawing(tmp_path / "plan.dwg")


def test_a_dwg_converter_that_writes_a_broken_dxf_is_a_drawing_error(tmp_path, monkeypatch):
    from storeypath.cad import DrawingError, read_drawing

    _fake_dwg2dxf(tmp_path, monkeypatch, 'printf "  0\\nSECTION\\n  2\\nENTITIES\\n 10\\nNOTAFLOAT\\n" > "$OUT"\nexit 1')
    (tmp_path / "plan.dwg").write_bytes(b"AC1032")
    with pytest.raises(DrawingError, match="cannot read plan.dwg"):
        read_drawing(tmp_path / "plan.dwg")


# ---- plans inside a block ---------------------------------------------------------


def _sheet_in_a_block(d: Path) -> Path:
    """Three floor plans side by side, the whole sheet pasted into the drawing as one
    block (as a bound xref is), the drawing's unit setting left out."""
    from ezdxf.addons import importer

    from storeypath.samples import write_sheet_dxf

    write_sheet_dxf(d / "flat.dxf", [(office_floor(0), (125.0, 48.0), "GROUND FLOOR PLAN"),
                                     (office_floor(1), (185.0, 48.0), "FIRST FLOOR PLAN"),
                                     (office_floor(2), (245.0, 48.0), "SECOND FLOOR PLAN")])
    src = ezdxf.readfile(d / "flat.dxf")
    doc = ezdxf.new("R2018", setup=True)
    imp = importer.Importer(src, doc)
    imp.import_entities(src.modelspace(), doc.blocks.new("SHEET"))
    imp.finalize()
    doc.modelspace().add_blockref("SHEET", (0, 0))
    doc.header["$INSUNITS"] = 0
    doc.saveas(d / "inblock.dxf")
    return d / "inblock.dxf"


def test_plans_inside_one_block_are_each_their_own_floor(tmp_path):
    from storeypath.analyse import analyse
    from storeypath.cad import decide_units, read_drawing
    from storeypath.extract import extract_floor
    from storeypath.profile import load_profile
    from storeypath.sheets import find_views

    doc = read_drawing(_sheet_in_a_block(tmp_path))
    units = decide_units(doc)
    assert units.units == "mm" and units.sure  # the doors and texts in the block show it
    views = [v for v in find_views(doc, load_profile("ncs"), 0.001, auto=True) if v.is_plan]
    assert [(v.title, v.ordinal) for v in views] == [
        ("GROUND FLOOR PLAN", 0), ("FIRST FLOOR PLAN", 1), ("SECOND FLOOR PLAN", 2)]
    for n, v in enumerate(views):
        profile = analyse(doc, 0.001, v.region, None, load_profile("ncs")).profile
        ex = extract_floor(doc, profile, None, v.region)
        assert len(ex.spaces) == 24 and {s.number[0] for s in ex.spaces if s.number} == {str(n)}


def test_a_block_placed_inside_itself_is_walked_once():
    from storeypath.extract import _walk

    doc = ezdxf.new("R2018")
    loop = doc.blocks.new("LOOP")
    loop.add_line((0, 0), (1000, 0), dxfattribs={"layer": "A-WALL"})
    for i in range(6):
        loop.add_blockref("LOOP", (i * 10, 0))
    doc.modelspace().add_blockref("LOOP", (0, 0))
    walked = list(_walk(doc.modelspace()))
    assert sum(1 for e, _ in walked if e.dxftype() == "LINE") == 1


# ---- a malformed answer costs one question ---------------------------------------


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _replying(*contents):
    """A stand-in for urlopen answering chat questions with each of ``contents`` in
    turn (a JSON body each), and the list of models with one."""
    queue = itertools.cycle(contents)

    def urlopen(req, timeout=None):
        if req.full_url.endswith("/models"):
            return _Reply(json.dumps({"data": [{"id": "fake-vl"}]}).encode())
        return _Reply(json.dumps(next(queue)).encode())

    return urlopen


MALFORMED = {
    "content null": {"choices": [{"message": {"role": "assistant", "content": None, "reasoning_content": "…"}}]},
    "no choices": {"choices": []},
    "a JSON list": {"choices": [{"message": {"content": '["office"]'}}]},
    "a number": {"choices": [{"message": {"content": "7"}}]},
    "not JSON": {"choices": [{"message": {"content": "an office"}}]},
    "a list for a reply": [1, 2],
}


@pytest.mark.parametrize("reply", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_malformed_reply_is_a_model_that_did_not_answer(reply, monkeypatch):
    monkeypatch.setattr(vision.urllib.request, "urlopen", _replying(reply))
    model = vision.VisionModel(url="http://vision.invalid/v1", model="x")
    with pytest.raises(vision.VisionUnavailable):
        model.ask(b"png", "?", {"outline": vision.OUTLINES})
    with pytest.raises(ModelUnavailable):
        vision.InWords(model).ask("system", "user", {})
    with pytest.raises(ModelUnavailable):
        llm.LocalModel(url="http://llm.invalid").ask("system", "user", {})


def test_one_malformed_label_answer_costs_that_label_only(monkeypatch):
    good = {"choices": [{"message": {"content": json.dumps({"type": "office"})}}]}
    monkeypatch.setattr(llm.urllib.request, "urlopen", _replying(good, MALFORMED["content null"], good))
    read = llm.read_labels(llm.LocalModel(url="http://llm.invalid"), ["BUREAU", "SALA", "MAKTAB"], rooms_only=True)
    assert len(read) == 2 and all(r.type == SpaceType.OFFICE for r in read.values())


def test_a_room_vision_cannot_answer_about_costs_that_room_only(tmp_path, monkeypatch):
    # An endpoint that answers every room but one, for which its reply has no
    # content (a reasoning model that spent max_tokens thinking): the conversion
    # goes on, and every answer received is kept.
    write_floor_dxf(tmp_path / "f.dxf", simple_office())
    ws, f = _project(tmp_path, "f.dxf", profile="auto", ordinal=0)
    answered = {"choices": [{"message": {"content": json.dumps({"outline": "exactly one room", "type": "office"})}}]}
    monkeypatch.setattr(vision.urllib.request, "urlopen",
                        _replying(*([answered] * 5), MALFORMED["content null"], *([answered] * 40)))
    monkeypatch.setattr(vision.FloorPrint, "view", lambda self, *a, **k: b"png")
    model = vision.VisionModel(url="http://vision.invalid/v1", parallel=2)
    report = convert_floor(ws, f, tmp_path, vision=model)
    rooms = sum(1 for r in ws.floor_objects(f) if r.kind == "space")
    assert rooms > 6 and len(ws.vision) == rooms - 1
    assert any(w.startswith("vision:") and "no answer" in w for w in report.warnings)


def _missing_door_block(doc):
    doc.modelspace().add_blockref("NO_SUCH_BLOCK", (130000, 50000), dxfattribs={"layer": "A-DOOR"})


def _missing_block(doc):  # an unbound xref, a purged block
    doc.modelspace().add_blockref("NO_SUCH_BLOCK2", (130000, 50000), dxfattribs={"layer": "A-FLOR-STRS"})


def _spline_of_one_point(doc):
    s = doc.modelspace().add_spline(dxfattribs={"layer": "A-WALL"})
    s.control_points = [(130000, 50000, 0)]
    s.dxf.degree = 3


def _hatch_with_a_broken_edge(doc):
    h = doc.modelspace().add_hatch(dxfattribs={"layer": "A-WALL"})
    h.paths.add_edge_path().add_spline(control_points=[(130000, 50000)], degree=3)


def _polyline_through_nan(doc):
    doc.modelspace().add_lwpolyline([(130000, 50000), (float("nan"), 50000), (131000, 51000)],
                                    dxfattribs={"layer": "A-WALL"})


def _line_from_nan(doc):
    doc.modelspace().add_line((float("nan"), float("nan")), (130000, 50000), dxfattribs={"layer": "A-WALL"})


def _arc_round_nan(doc):
    doc.modelspace().add_arc((float("nan"), 50000), 900, 0, 90, dxfattribs={"layer": "A-DOOR"})


def _arc_of_huge_radius(doc):  # flattening it would never end
    doc.modelspace().add_arc((130000, 50000), 1e300, 0, 90, dxfattribs={"layer": "A-WALL"})


BROKEN = {f.__name__.strip("_").replace("_", " "): f for f in (
    _missing_door_block, _missing_block, _spline_of_one_point, _hatch_with_a_broken_edge, _polyline_through_nan,
    _line_from_nan, _arc_round_nan, _arc_of_huge_radius)}


@pytest.fixture(scope="module")
def plain_office(tmp_path_factory):
    """The sample office drawn with walls only, and how many objects each profile reads in it."""
    d = tmp_path_factory.mktemp("plain")
    write_floor_dxf(d / "base.dxf", simple_office(), area_outlines=False)
    counts = {}
    for profile in ("ncs", "auto"):
        ws, f = _project(d, "base.dxf", profile=profile, ordinal=0)
        counts[profile] = len(convert_floor(ws, f, d).added)
    return d, counts


@pytest.mark.parametrize("profile", ["ncs", "auto"])
@pytest.mark.parametrize("breakage", BROKEN.values(), ids=BROKEN.keys())
def test_a_broken_entity_is_left_out_not_the_floor(plain_office, breakage, profile, tmp_path):
    d, counts = plain_office
    doc = ezdxf.readfile(d / "base.dxf")
    breakage(doc)
    doc.saveas(tmp_path / "broken.dxf")
    ws, f = _project(tmp_path, "broken.dxf", profile=profile, ordinal=0)
    report = convert_floor(ws, f, tmp_path)
    assert len(report.added) == counts[profile]


def test_what_the_auditor_takes_out_is_said(plain_office, tmp_path):
    d, _ = plain_office
    doc = ezdxf.readfile(d / "base.dxf")
    _missing_block(doc)
    doc.saveas(tmp_path / "broken.dxf")
    ws, f = _project(tmp_path, "broken.dxf", ordinal=0)
    report = convert_floor(ws, f, tmp_path)
    assert any("1 broken entities were left out" in w and "BLOCK" in w for w in report.warnings)


@pytest.mark.parametrize("breakage", [_missing_door_block, _missing_block, _hatch_with_a_broken_edge])
def test_a_broken_entity_the_auditor_misses_is_left_out_too(plain_office, breakage, monkeypatch, tmp_path):
    import storeypath.cad as cad

    monkeypatch.setattr(cad, "_audited", lambda doc: doc)
    d, counts = plain_office
    doc = ezdxf.readfile(d / "base.dxf")
    breakage(doc)
    doc.saveas(tmp_path / "broken.dxf")
    ws, f = _project(tmp_path, "broken.dxf", ordinal=0)
    assert len(convert_floor(ws, f, tmp_path).added) == counts["ncs"]


def test_views_are_drawn_only_a_little_ahead_of_the_model():
    import threading
    import time

    lock = threading.Lock()
    counts = {"drawn": 0, "answered": 0, "most": 0}

    def draw(item):
        with lock:
            counts["drawn"] += 1
            counts["most"] = max(counts["most"], counts["drawn"] - counts["answered"])
        return b"png"

    def ask(item, image):
        time.sleep(0.01)
        with lock:
            counts["answered"] += 1
        return item

    kept = []
    vision._ask_each(list(range(40)), draw, ask, kept.append, vision.VisionModel(url="http://x", parallel=2))
    assert sorted(kept) == list(range(40)) and counts["most"] <= 4


def test_only_the_last_few_tiles_of_a_floor_are_kept(monkeypatch):
    class Canvas:
        def draw(self):
            pass

        def get_width_height(self):
            return 4, 4

        def buffer_rgba(self):
            return bytes(64)

    class Figure:
        canvas = Canvas()

    printed = []
    monkeypatch.setattr(vision, "_print", lambda doc, bbox, px, **k: printed.append(bbox) or Figure())
    sheet = vision.FloorPrint(None, 0.001)
    for i in range(10):
        sheet._tile(i, 0)
    sheet._tile(9, 0)  # kept: not printed again
    assert len(printed) == 10 and len(sheet._tiles) == vision.FloorPrint.KEEP_TILES


def test_a_room_that_cannot_be_drawn_costs_that_room_only():
    kept = []

    def draw(item):
        if item == 2:
            raise ValueError("a broken entity")
        return b"png"

    model = vision.VisionModel(url="http://vision.invalid/v1", parallel=2)
    vision._ask_each(list(range(6)), draw, lambda item, image: item, kept.append, model)
    assert sorted(kept) == [0, 1, 3, 4, 5] and "could not be drawn" in model.failed
