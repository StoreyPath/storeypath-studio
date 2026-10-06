"""Taking private information out of a drawing before it enters a project."""

import ezdxf
from typer.testing import CliRunner

from storeypath.cli import app
from storeypath.privacy import make_private
from storeypath.samples import office_floor, write_sheet_dxf

M = 1000  # the sample sheet is drawn in millimetres


def _sheet(path, title_block="column"):
    """A floor plan on a sheet with a title block, a logo, a phone number in a note,
    hidden file data and a paper-space sheet."""
    write_sheet_dxf(path, [(office_floor(0), (100.0, 50.0), "GROUND FLOOR PLAN")], area_outlines=False)
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    msp.add_lwpolyline([(90 * M, 30 * M), (190 * M, 30 * M), (190 * M, 90 * M), (90 * M, 90 * M)], close=True)
    if title_block == "column":  # down the right of the sheet, parted off by a long line
        msp.add_line((170 * M, 30 * M), (170 * M, 90 * M))
        x, ys = 172, range(80, 40, -6)
    else:  # a box in the corner
        msp.add_lwpolyline([(165 * M, 30 * M), (190 * M, 30 * M), (190 * M, 45 * M), (165 * M, 45 * M)], close=True)
        x, ys = 166, range(43, 31, -3)
    for y, (label, value) in zip(ys, [("CLIENT :", "SALEM HOUSE"), ("CONSULTANT :", "NORTH STAR DESIGN"),
                                      ("DRAWN BY :", "RICKY"), ("DRAWING NO. :", "A02")]):
        msp.add_text(label, height=300).set_placement((x * M, y * M))
        msp.add_text(value, height=400).set_placement((x * M, (y - 1) * M))
    logo = doc.blocks.new("STUDIO_LOGO")
    logo.add_circle((0, 0), 800)
    msp.add_blockref("STUDIO_LOGO", ((x + 8) * M, ys[-1] * M - 2 * M))
    image = doc.add_image_def(filename="C:/Users/someone/logo.png", size_in_pixel=(100, 100))
    msp.add_image(image, insert=(92 * M, 32 * M), size_in_units=(1000, 1000))  # a stamp, off the title block
    msp.add_text("FOR QUERIES CALL TEL: 4446844", height=300).set_placement((100 * M, 35 * M))
    msp.add_text("SCHEDULE OF OPENINGS", height=400).set_placement((150 * M, 85 * M))
    doc.header["$LASTSAVEDBY"] = "someone"
    doc.header.custom_vars.append("Client", "Salem")
    doc.layouts.new("A02").add_text("CLIENT : SALEM HOUSE")
    doc.saveas(path)


def _texts(doc):
    return {e.dxf.text for e in doc.modelspace().query("TEXT")}


def test_the_title_column_goes_and_the_plan_stays(tmp_path):
    _sheet(tmp_path / "sheet.dxf")
    doc = ezdxf.readfile(tmp_path / "sheet.dxf")
    rooms = {t for t in _texts(doc) if t in ("OFFICE", "CORRIDOR", "LIFT", "GROUND FLOOR PLAN")}
    walls = len(doc.modelspace().query("LINE LWPOLYLINE"))
    report = make_private(doc)
    assert report.title_blocks == 1
    assert report.images == 1 and report.sheets == 1 and report.texts == 1
    assert "$LASTSAVEDBY" in report.hidden and "custom properties" in report.hidden
    doc.saveas(tmp_path / "private.dxf")  # it saves: nothing it needs was taken
    doc = ezdxf.readfile(tmp_path / "private.dxf")
    texts = _texts(doc)
    for gone in ("CLIENT :", "SALEM HOUSE", "NORTH STAR DESIGN", "RICKY", "FOR QUERIES CALL TEL: 4446844"):
        assert gone not in texts
    assert rooms <= texts and "SCHEDULE OF OPENINGS" in texts  # the plan, its title and schedules stay
    assert len(doc.modelspace().query("LINE LWPOLYLINE")) >= walls - 2  # the column's line, at most
    assert not doc.modelspace().query("IMAGE") and not doc.objects.query("IMAGEDEF")
    assert "STUDIO_LOGO" not in doc.blocks
    assert doc.header.get("$LASTSAVEDBY", "") == "" and not len(doc.header.custom_vars)
    assert not len(doc.layouts.get("A02"))


def test_a_title_box_in_the_corner_goes(tmp_path):
    _sheet(tmp_path / "sheet.dxf", title_block="box")
    doc = ezdxf.readfile(tmp_path / "sheet.dxf")
    report = make_private(doc)
    assert report.title_blocks == 1
    texts = _texts(doc)
    assert "SALEM HOUSE" not in texts and "OFFICE" in texts and "GROUND FLOOR PLAN" in texts


def test_notes_that_name_an_owner_are_not_a_title_block(tmp_path):
    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (100.0, 50.0), "GROUND FLOOR PLAN")], area_outlines=False)
    doc = ezdxf.readfile(tmp_path / "sheet.dxf")
    doc.modelspace().add_text("OWNER OR CONTRACTOR MUST PROVIDE SMOKE DETECTORS", height=300).set_placement((100 * M, 40 * M))
    before = len(doc.modelspace())
    report = make_private(doc)
    assert report.title_blocks == 0 and len(doc.modelspace()) == before
    assert report.texts == report.images == report.attributes == 0


def test_private_copy_from_the_command_line(tmp_path):
    _sheet(tmp_path / "sheet.dxf")
    result = CliRunner().invoke(app, ["private", str(tmp_path / "sheet.dxf"), str(tmp_path / "out.dxf")])
    assert result.exit_code == 0, result.output
    assert "1 title block(s)" in result.output
    assert "SALEM HOUSE" not in _texts(ezdxf.readfile(tmp_path / "out.dxf"))


def test_rooms_named_for_a_role_stay_and_names_go(tmp_path):
    # A large building's rooms: "DR. OFFICE" is a room, "DR. KHALID" a person; the printer
    # and plot style a sheet was set up with name a computer and its owner.
    from storeypath.privacy import words

    doc = ezdxf.new()
    msp = doc.modelspace()
    for i, t in enumerate(["DR. OFFICE", "ENG. ROOM", "CONSULTANT DR. KHALID", "MR. JOHN SAMPLE", "EXT. 2345",
                           "NURSE STATION"]):
        msp.add_text(t, height=0.2).set_placement((0, i))
    layout = doc.layouts.get("Layout1")
    layout.dxf_layout.dxf.plot_configuration_file = "\\\\\\\\SERVER\\\\HP Officejet"
    layout.dxf_layout.dxf.current_style_sheet = "ricky1.ctb"
    report = make_private(doc)
    texts = _texts(doc)
    assert {"DR. OFFICE", "ENG. ROOM", "CONSULTANT", "NURSE STATION"} <= texts
    assert not texts & {"CONSULTANT DR. KHALID", "MR. JOHN SAMPLE", "EXT. 2345"}
    assert "printer and plot style names" in report.hidden
    listed = words(doc, "test.dxf")
    assert "NURSE STATION" in listed and "Layer names" in listed
    assert "SERVER" not in listed and "ricky" not in listed
