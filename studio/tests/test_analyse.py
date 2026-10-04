"""Reading drawings without being told their layers."""

import ezdxf
import pytest

from storeypath.analyse import analyse
from storeypath.cad import infer_units, meters_per_unit, read_drawing
from storeypath.extract import extract_floor
from storeypath.llm import LocalModel
from storeypath.profile import load_profile
from storeypath.reading import NOT_A_ROOM, TextReader
from storeypath.samples import _windows, office_floor, write_floor_dxf, write_sheet_dxf
from storeypath.sheets import find_views
from storeypath.types import SpaceType
from storeypath.workspace import Workspace

RULES = load_profile("ncs")


def rules_only(text):
    if NOT_A_ROOM.match(text):
        return False
    return True if RULES.classify(text, [], "")[0] != SpaceType.UNSPECIFIED else None


def _anonymous(path, **kw):
    """The sample office with every layer renamed to a meaningless name."""
    write_floor_dxf(path, office_floor(1), **kw)
    doc = ezdxf.readfile(path)
    names = {layer.dxf.name: f"L{i:02d}" for i, layer in enumerate(doc.layers) if layer.dxf.name not in ("0", "Defpoints")}
    for e in [*doc.modelspace(), *(e for blk in doc.blocks for e in blk)]:
        e.dxf.layer = names.get(e.dxf.layer, e.dxf.layer)
    doc.saveas(path)
    return {v: k for k, v in names.items()}


def _roles(analysis, original):
    return {original.get(r.layer, r.layer): r.roles for r in analysis.roles if r.roles}


def test_layers_read_from_what_is_drawn(tmp_path):
    original = _anonymous(tmp_path / "plan.dxf", area_outlines=False)
    a = analyse(read_drawing(tmp_path / "plan.dxf"), 0.001, None, rules_only)
    assert _roles(a, original) == {
        "A-WALL": ["walls"],  # pairs of lines 20 cm apart forming one frame
        "A-GLAZ": ["openings"],  # thin pairs in line with the walls: glazing
        "A-AREA-IDEN": ["labels"],
        "A-DOOR": ["openings"],  # quarter-turn swings
    }
    assert abs(a.wall_thickness - 0.2) < 0.01
    # stair treads (evenly spaced) and lift cars are not walls


def test_auto_profile_converts_like_the_hand_written_one(tmp_path):
    _anonymous(tmp_path / "plan.dxf", area_outlines=False)
    doc = read_drawing(tmp_path / "plan.dxf")
    ex = extract_floor(doc, analyse(doc, 0.001, None, rules_only).profile)
    cells = office_floor(1)
    doors = [d for d in ex.doors if d.source != "window"]
    windows = [d for d in ex.doors if d.source == "window"]
    assert len(ex.spaces) == len(cells) and len(doors) == sum(1 for c in cells if c.door)
    assert len(windows) == len(_windows(cells))
    drawn = sorted(width for _, _, width in _windows(cells))
    assert all(abs(a - b) < 0.05 for a, b in zip(sorted(w.width for w in windows), drawn))  # true widths
    assert all(d.span is not None and 0.8 < d.width < 2.0 for d in doors)  # every door knows its span
    assert ex.walls is not None and abs(ex.wall_thickness - 0.2) < 0.03


def test_room_outlines_are_recognised(tmp_path):
    write_floor_dxf(tmp_path / "plan.dxf", office_floor(1))
    a = analyse(read_drawing(tmp_path / "plan.dxf"), 0.001, None, rules_only)
    assert _roles(a, {})["A-AREA"] == ["outlines"]
    assert a.profile.spaces.layers == ["A\\-AREA"]


def test_dashed_lines_are_not_walls(tmp_path):
    write_floor_dxf(tmp_path / "plan.dxf", office_floor(1), area_outlines=False)
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    doc.layers.get("A-WALL").dxf.linetype = "DASHED"  # e.g. beams or walls above, drawn hidden
    doc.saveas(tmp_path / "dashed.dxf")
    a = analyse(read_drawing(tmp_path / "dashed.dxf"), 0.001, None, rules_only)
    assert "walls" not in _roles(a, {}).get("A-WALL", [])


def test_plans_are_found_on_a_sheet_without_knowing_the_layers(tmp_path):
    write_sheet_dxf(tmp_path / "all.dxf", [(office_floor(0), (100.0, 50.0), "GROUND FLOOR PLAN"),
                                           (office_floor(1), (170.0, 52.5), "FIRST FLOOR PLAN")], area_outlines=False)
    views = find_views(read_drawing(tmp_path / "all.dxf"), RULES, 0.001, auto=True, is_room_name=rules_only)
    assert [(v.title, v.ordinal) for v in views] == [("GROUND FLOOR PLAN", 0), ("FIRST FLOOR PLAN", 1)]


@pytest.mark.parametrize("unit, factor", [("mm", 1.0), ("m", 0.001), ("cm", 0.1)])
def test_units_are_read_from_the_doors(tmp_path, unit, factor):
    write_floor_dxf(tmp_path / "plan.dxf", office_floor(1))
    doc = ezdxf.readfile(tmp_path / "plan.dxf")
    if factor != 1.0:  # redraw the plan in other units, leaving the header saying mm
        m = ezdxf.math.Matrix44.scale(factor)
        for e in doc.modelspace():
            e.transform(m)
    doc.saveas(tmp_path / "scaled.dxf")
    doc = read_drawing(tmp_path / "scaled.dxf")
    assert infer_units(doc) == unit
    assert abs(meters_per_unit(doc) - {"mm": 0.001, "m": 1.0, "cm": 0.01}[unit]) < 1e-12


class FakeModel(LocalModel):
    """Answers like a language model would, from a fixed table; counts questions."""

    def __init__(self, answers):
        super().__init__(url="http://fake")
        self.answers, self.asked = answers, []

    def available(self):
        return True

    def ask(self, system, user, schema, max_tokens=1024):
        text = user.removeprefix("Text: ")
        self.asked.append(text)
        return {"type": self.answers.get(text, "not_a_room")}


def test_texts_the_rules_do_not_know_are_read_by_the_model_once():
    ws = Workspace.new("P")
    model = FakeModel({"F.DINNING": "dining_room", "MAJLIS": "living_room"})
    reader = TextReader(ws, RULES, model)
    reader.learn(["OFFICE", "F.DINNING", "MAJLIS", "+0.45 FFL", "D1", "FIRE EXTINGUISHER"])
    assert model.asked == ["F.DINNING", "MAJLIS", "FIRE EXTINGUISHER"]  # rules and patterns answer the rest
    assert reader.is_room_name("F.DINNING") and reader.is_room_name("OFFICE")
    assert reader.is_room_name("+0.45 FFL") is False and reader.is_room_name("FIRE EXTINGUISHER") is False
    assert reader.room_type("MAJLIS") == SpaceType.LIVING_ROOM
    # remembered in the workspace: a second reader (no model) knows the same
    again = TextReader(ws, RULES, None)
    assert again.room_type("F.DINNING") == SpaceType.DINING_ROOM


def test_conversion_types_rooms_with_the_model(workspace):
    ws, d, f_id, _, cells = workspace
    from storeypath.convert import convert_floor

    names = {c.name for c in cells if c.name}
    model = FakeModel({n: "room" for n in names})
    convert_floor(ws, f_id, d, model)
    by_name = {r.name: r for r in ws.floor_objects(f_id) if r.kind == "space"}
    # OFFICE is known to the rules; nothing in the sample needs the model but 214, which has no name
    assert by_name["OFFICE"].type_source.startswith("label:")
    assert "OFFICE" not in model.asked


def test_plain_titles_are_read_by_rule_and_the_rest_left_to_the_model():
    from storeypath.sheets import read_title

    assert read_title("MODIFIED GROUND FLOOR PLAN") == ("floor_plan", 0)
    assert read_title("SECOND FLOOR PLAN") == ("floor_plan", 2)
    assert read_title("ROOF DECK PLAN") == ("roof_plan", None)
    assert read_title("FRONT ELEVATION") == ("elevation", None)
    assert read_title("SITE DEVELOPMENT PLAN") == ("site_plan", None)
    for unsure in ("OUT KITCHEN FLOOR PLAN", "GUARD RM. PLAN DETAIL", "المسقط الأفقي للدور الأرضي", "TYPICAL OFFICE FLOOR"):
        assert read_title(unsure) is None
