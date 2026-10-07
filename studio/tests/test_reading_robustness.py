"""Reading drawings that are broken, read wrong or answered wrong: one bad piece
costs that piece, never the floor's IDs or the rest of the conversion."""

import io
import itertools
import json
from pathlib import Path

import ezdxf
import pytest
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


def test_a_room_that_cannot_be_drawn_costs_that_room_only():
    kept = []

    def draw(item):
        if item == 2:
            raise ValueError("a broken entity")
        return b"png"

    model = vision.VisionModel(url="http://vision.invalid/v1", parallel=2)
    vision._ask_each(list(range(6)), draw, lambda item, image: item, kept.append, model)
    assert sorted(kept) == [0, 1, 3, 4, 5] and "could not be drawn" in model.failed
