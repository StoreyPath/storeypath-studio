"""Area samples (areasample/): a part of a floor, how Studio read it and what people
corrected, with nothing private in it. Every drawing here is made by samples.py."""

import io
import json
import zipfile

import ezdxf
import pytest
from shapely.geometry import shape
from typer.testing import CliRunner

from storeypath.areasample import Area, build
from storeypath.areasample.private import Privacy, parts_of
from storeypath.areasample.tools import inspect, open_sample, replay, summary
from storeypath.cli import app
from storeypath.convert import convert_floor
from storeypath.review import Review
from storeypath.samples import office_floor, simple_office, write_floor_dxf, write_sheet_dxf
from storeypath.workspace import Override, SourceDrawing, Workspace

ORIGIN = (512345.0, 2789012.0)  # metres: a drawing on a national grid, far from 0, 0
AREA = [ORIGIN[0] + 6, ORIGIN[1] - 0.5, ORIGIN[0] + 30, ORIGIN[1] + 20.5]  # local metres: the west half of the plan
SECRETS = ["KHALID", "NOURA", "4412 9087", "EXT 7731", "EXAMPLE.COM", "JSMITH", "SECRET TOWER", "ACME", "LUSAIL",
           "MARINA", "INTRANET", "LOGO.PNG", "512345", "2789012", "J SMITH"]


def _bait(path):
    """A synthetic floor with private things in it: names, contacts, file data, a
    hyperlink, an image, a sheet in paper space, a layer named for the project, and
    far from the origin."""
    write_floor_dxf(path, office_floor(2), origin=ORIGIN, title="ACME TOWER LEVEL 2")
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    def mm(x, y):
        return (ORIGIN[0] + x) * 1000, (ORIGIN[1] + y) * 1000

    for text, at in (("DR. KHALID AL-ALI", (10, 5.5)), ("TEL +974 4412 9087", (14, 5.5)), ("EXT 7731", (14, 4.5)),
                     ("j.smith@example.com", (18, 5.5)), ("NOURA BINT SAAD", (10, 2.5))):
        msp.add_text(text, height=200, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(mm(*at))
    doc.layers.add("ACME-NOTES")
    msp.add_text("SEE NOTE", height=200, dxfattribs={"layer": "ACME-NOTES"}).set_placement(mm(22, 15))
    doc.header["$LASTSAVEDBY"] = "jsmith"
    doc.header["$PROJECTNAME"] = "Secret Tower"
    doc.header.custom_vars.append("Client", "Acme Holding")
    doc.appids.add("PE_URL")
    wall = msp.query("LWPOLYLINE[layer=='A-WALL']").first
    wall.set_xdata("PE_URL", [(1000, "http://intranet.acme.qa/drawings")])
    image = doc.add_image_def(filename=r"C:\Users\jsmith\logo.png", size_in_pixel=(64, 64))
    msp.add_image(image, insert=mm(13, 6), size_in_units=(800, 800))
    doc.layouts.new("Sheet A1").add_text("DRAWN BY J SMITH")
    doc.saveas(path)


@pytest.fixture
def project(tmp_path):
    """A project (a workspace file) of one floor read from the bait drawing, with a
    person's corrections, a wall drawn and an item placed in the area."""
    _bait(tmp_path / "level-2.dxf")
    ws = Workspace.new("Acme Tower")
    loc = ws.add_location("LUS", "Lusail Marina")
    b = ws.add_building(loc, "HQ", "Acme Tower One")
    f_id = ws.add_floor(b, 2, name="Level 2", source=SourceDrawing(path="level-2.dxf", units="mm"))
    convert_floor(ws, f_id, tmp_path)
    spaces = {r.number: r for r in ws.floor_objects(f_id) if r.kind == "space" and r.number}
    ws.overrides[spaces["214"].id] = Override(type="office", name="MR. JOHN SMITH OFFICE")  # a right answer…
    ws.overrides[spaces["213"].id] = Override(ignored=True)  # …a deletion…
    ws.overrides[spaces["212"].id] = Override()  # …and one accepted as read
    x, y = ORIGIN
    ws.floor(f_id).edits.walls.append([[x + 20, y + 14], [x + 20, y + 19]])
    item = ws.add_item("DESK-JUNIOR", f_id, x + 10, y + 3)
    path = tmp_path / "project.spproj"
    ws.save(path)
    return path, f_id, ws, item


def _files(sample) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(sample.zipped())) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_nothing_private_is_in_a_sample(project):
    path, f_id, ws, item = project
    s = build(Review(path), f_id, AREA, note="ask KHALID about the corridor")
    files = _files(s)
    assert sorted(files) == sorted(["manifest.json", "README.txt", "drawing.dxf", "drawing.png", "reading.png",
                                    "reading.json", "corrections.json"])
    for name, data in files.items():
        if name.endswith(".png"):
            continue
        text = data.decode("utf-8").upper()
        for secret in SECRETS + [ws.id, item.id]:
            assert secret.upper() not in text, f"{secret} in {name}"
    doc = ezdxf.read(io.StringIO(files["drawing.dxf"].decode()))
    assert doc.header.get("$LASTSAVEDBY") != "jsmith" and not doc.header.get("$PROJECTNAME")
    assert not list(doc.header.custom_vars)
    assert "DWGPROPS" not in doc.rootdict
    assert not doc.modelspace().query("IMAGE OLE2FRAME") and not list(doc.objects.query("IMAGEDEF"))
    assert all(len(layout) == 0 for layout in doc.layouts if layout.name != "Model")  # nothing on paper
    assert not any(e.xdata for e in doc.modelspace())
    assert "PROJECT-NOTES" in doc.layers  # the layer named for the project, renamed…
    assert "PROJECT-NOTES" in [x["layer"] for x in s.reading["layers"]["in_sample"]]  # …and so said
    # moved: the area's lower-left corner at 0, 0 (the drawing in mm, 1 m margin; a text whose middle is
    # in the margin is kept whole)
    box = ezdxf.bbox.extents(doc.modelspace())
    assert -3000 < box.extmin.x and box.extmax.x < 27000 and -3000 < box.extmin.y and box.extmax.y < 24000
    labels = [t["text"] for t in json.loads(files["reading.json"])["texts"]]
    assert "DR. [NAME]" not in labels and "[NAME]" in labels and "[PHONE]" in labels and "[EXT]" in labels
    assert "[EMAIL]" in labels and "[NAME?]" in labels
    m = json.loads(files["manifest.json"])
    assert m["note"] == "ask [NAME?] about the corridor" or "KHALID" not in m["note"]
    assert m["privacy"]["texts"]["removed"]["name"] >= 1
    assert s.privacy.leftovers(json.dumps([json.loads(files[n]) for n in ("reading.json", "corrections.json")])) == []


def test_private_parts_of_texts():
    assert parts_of("OFFICE") == [] and parts_of("MAJLIS") == [] and parts_of("SALAH") == [] and parts_of("WC") == []
    assert parts_of("MEETING ROOM 204") == [] and parts_of("RM-GF-33") == [] and parts_of("+0.45 FFL") == []
    assert parts_of("MR. JOHN SMITH OFFICE") == [("MR. JOHN SMITH", "name")]
    assert parts_of("OFFICE - KHALID AL SULAITI") == [("KHALID AL SULAITI", "maybe a name")]
    assert parts_of("TEL: +974 4412 9087") == [("TEL: +974 4412 9087", "phone")]
    assert parts_of("EXT. 2345") == [("EXT. 2345", "extension")]
    assert parts_of("a.b@x.org") == [("a.b@x.org", "email")]
    assert parts_of("مكتب السيد أحمد") == [("السيد أحمد", "name")]
    p = Privacy(["OFFICE DR. KHALID", "STORE"], [("Acme Tower", "project")], keep=(), remove=())
    assert p.scrub("office dr. khalid") == "office [NAME]" and p.scrub("ACME TOWER LOBBY") == "[PROJECT] LOBBY"
    finding = p.view()["found"][0]
    kept = Privacy(["OFFICE DR. KHALID", "STORE"], [], keep=[finding["id"]],
                   remove=[p.view()["others"][0]["id"]])
    assert kept.scrub("OFFICE DR. KHALID") == "OFFICE DR. KHALID" and kept.scrub("STORE") == "[TEXT]"


def test_the_area_is_capped_and_never_empty(project):
    path, f_id, *_ = project
    with pytest.raises(ValueError, match="at most 50"):
        Area.of([0, 0, 50.5, 10])
    with pytest.raises(ValueError, match="empty"):
        Area.of([0, 0, 0.2, 10])
    with pytest.raises(ValueError, match="x0, y0, x1, y1"):
        Area.of([0, 0, 10])
    assert Area.of([10, 20, 0, 0]).as_list() == [0, 0, 10, 20]  # any two opposite corners
    with pytest.raises(ValueError, match="nothing of the floor's drawing"):
        build(Review(path), f_id, [0, 0, 10, 10])  # far from the plan


def test_the_drawing_part_reads_as_the_floor_did(project, tmp_path):
    path, f_id, ws, _ = project
    s = build(Review(path), f_id, AREA)
    (tmp_path / "a.spsample").write_bytes(s.zipped())
    doc = ezdxf.read(io.StringIO(s.files["drawing.dxf"].decode()))
    auditor = doc.audit()
    assert not auditor.has_errors
    assert doc.header["$INSUNITS"] == 4  # mm, as drawn
    dims = doc.modelspace().query("DIMENSION")
    assert all(d.dxf.geometry in doc.blocks for d in dims)  # each dimension keeps its drawing
    result = replay(tmp_path / "a.spsample", tmp_path / "out", say=lambda m: None)
    now = result["studio_now"]
    deleted = next(x["id"] for x in s.reading["spaces"] if x["number"] == "213")  # a person said: not a room
    assert now["rooms"]["missed"] == [] and now["rooms"]["extra"] == [deleted]
    assert now["rooms"]["found"] == now["rooms"]["right_answers"] > 5
    assert result["then_vs_now"]["rooms_then"] == result["then_vs_now"]["rooms_now"]
    # the same rooms with the same types as Studio found then, but where a person corrected one
    assert [w["room"] for w in now["types"]["wrong"]] == [w["room"] for w in result["studio_then"]["types"]["wrong"]]
    assert result["studio_then"]["types"]["wrong"]  # 214: no type then, an office now (a person said)
    for name in ("replay.json", "replay.png", "drawing.png", "reading.png", "side-by-side.png"):
        assert (tmp_path / "out" / name).stat().st_size > 0
    assert "Score against the right answers" in summary(result)


def test_reading_and_corrections_say_what_was_decided_and_changed(project):
    path, f_id, ws, item = project
    s = build(Review(path), f_id, AREA)
    r, c = s.reading, s.corrections
    by_number = {x["number"]: x for x in r["spaces"]}
    office = by_number["201"]
    assert office["id"].startswith("S") and office["type"] == "office" and office["decided_by"] == "rules"
    assert office["at_edge"] is False and office["inside_share"] == 1.0
    ring = shape(office["geometry"])
    x0, y0, x1, y1 = ring.bounds  # the sample's frame: the area's lower-left corner at 0, 0
    assert 1.9 < x0 < 2.2 and 0.5 < y0 < 0.7 and 5.8 < x1 < 6.1
    assert r["settings"]["units"] == "mm" and r["settings"]["profile"] == "auto" and r["settings"]["m_per_unit"] == 0.001
    roles = {x["layer"]: x["read_as"] for x in r["layers"]["in_sample"]}
    assert roles["A-WALL"] == ["walls"] and roles["A-AREA-IDEN"] == ["labels"]
    doors = [d for d in r["openings"] if d["type"] == "door"]
    assert doors and all(d["id"].startswith("D") for d in doors)
    assert any(office["id"] in d["connects"] for d in doors)
    texts = {t["text"]: t for t in r["texts"]}
    assert texts["OFFICE"]["read_as"]["rules"]["type"] == "office"
    assert texts["201"]["read_as"]["rules"].startswith("not a room name")
    assert r["floor"]["method"] == "outlines" and r["floor"]["walls_found"]
    fixed = {o["id"]: o for o in c["objects"]}
    s214, s213, s212 = by_number["214"]["id"], by_number["213"]["id"], by_number["212"]["id"]
    assert fixed[s214]["detected"]["type"] == "unspecified" and fixed[s214]["type"] == "office"
    assert fixed[s214]["name"] == "[NAME] OFFICE"
    assert fixed[s213]["ignored"] is True and fixed[s212]["accepted_as_is"] is True
    assert c["drawn"]["walls"] == [[[14.0, 14.5], [14.0, 19.5]]]
    assert c["items"] == [{"id": "I1", "type": "DESK-JUNIOR", "category": "furniture", "at": [4.0, 3.5],
                           "rotation": 0.0, "in": office["id"]}]
    s_path = path.parent / "s.spsample"
    s_path.write_bytes(s.zipped())
    said = inspect(s_path)
    assert "Corrections (Studio's reading → a person's):" in said
    assert f"{s214}: type: 'unspecified' → 'office'" in said and f"{s213}: deleted" in said


def test_choices_in_the_preview_keep_or_take_out_texts(project):
    path, f_id, *_ = project
    review = Review(path)
    first = build(review, f_id, AREA, preview=True)
    view = first.privacy.view()
    khalid = next(f for f in view["found"] if "KHALID" in f["text"])
    store = next(o for o in view["others"] if o["text"] == "STORAGE 210")
    assert khalid["removed"] and not khalid["always"]
    s = build(review, f_id, AREA, keep=[khalid["id"]], remove=[store["id"]], note="STORAGE 210 is fine")
    texts = [t["text"] for t in s.reading["texts"]]
    assert "DR. KHALID AL-ALI" in texts  # kept, as the person chose
    assert "STORAGE 210" not in texts and "[TEXT]" in json.dumps(s.reading)
    assert s.manifest["privacy"]["texts"]["kept_by_person"] == {"name": 1}
    assert s.manifest["note"] == "STORAGE 210 is fine"  # a drawing's text taken out, not the person's words


def test_a_sample_never_holds_another_floors_plan(tmp_path):
    write_sheet_dxf(tmp_path / "sheet.dxf", [(office_floor(0), (0, 0), "PLAN A"), (simple_office(), (60, 0), "PLAN B")])
    ws = Workspace.new("Sheet")
    b = ws.add_building(ws.add_location("S", "S"), "B", "B")
    f_id = ws.add_floor(b, 0, source=SourceDrawing(path="sheet.dxf", units="mm", region=(-5000, -10000, 55000, 30000)))
    convert_floor(ws, f_id, tmp_path)
    ws.save(tmp_path / "p.spproj")
    s = build(Review(tmp_path / "p.spproj"), f_id, [35, -1, 75, 21])  # across the edge of its part of the sheet
    text = s.files["drawing.dxf"].decode()
    assert "F0-3" not in text and "PRAYER" not in text  # plan B's labels
    assert "OFFICE" in text


def test_the_cli_makes_inspects_and_replays_a_sample(project, tmp_path):
    path, f_id, *_ = project
    runner = CliRunner()
    area = ",".join(str(v) for v in AREA)
    listed = runner.invoke(app, ["sample", "make", str(path), "--floor", f_id, "--area", area, "--list"])
    assert listed.exit_code == 0 and "[NAME]" in listed.output and "DR. KHALID AL-ALI" in listed.output
    made = runner.invoke(app, ["sample", "make", str(path), "--floor", f_id, "--area", area, "-o", str(tmp_path),
                               "--note", "room 214 had no type"])
    assert made.exit_code == 0, made.output
    sample = next(tmp_path.glob("*.spsample"))
    assert open_sample(sample).manifest["note"] == "room 214 had no type"
    shown = runner.invoke(app, ["sample", "inspect", str(sample)])
    assert shown.exit_code == 0 and "Note: room 214 had no type" in shown.output and "Rooms (" in shown.output
    replayed = runner.invoke(app, ["sample", "replay", str(sample), "--no-model", "--no-vision", "-o",
                                   str(tmp_path / "replay")])
    assert replayed.exit_code == 0, replayed.output
    assert "no language model, no vision model" in replayed.output and "Studio then" in replayed.output
    assert (tmp_path / "replay" / "replay.png").exists()
    bad = tmp_path / "not.spsample"
    bad.write_bytes(b"not a zip")
    refused = runner.invoke(app, ["sample", "inspect", str(bad)])
    assert refused.exit_code == 1 and "not an area sample" in refused.output
