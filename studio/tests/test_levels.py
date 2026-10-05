"""Floor heights from the level labels on a drawing's sections and elevations."""

import ezdxf
import pytest

from storeypath.levels import DEFAULT_HEIGHT_M, DEFAULT_PARAPET_M, floor_levels, read_level_marks
from storeypath.llm import LocalModel
from storeypath.workspace import Workspace

# As a villa's sections label them, each on several sections (and one stray parapet).
VILLA = ["%%p0.00 GROUND LVL."] * 3 + ["+0.35 GROUND FLOOR SLAB LVL."] * 3 + ["+3.65 FIRST FLOOR SLAB LVL."] * 3 \
    + ["+6.95 ROOF SLAB LVL."] * 3 + ["+8.65 PARAPET LVL."] * 3 + ["+8.25 PARAPET LVL."] \
    + ["+10.25 STAIR ROOF SLAB LVL."] * 2 + ["+3150 SSL", "+3.75 FFL", "+10.95 LVL.", "1050.01 SQ.M."]


def _drawing(texts):
    doc = ezdxf.new()
    for i, t in enumerate(texts):
        doc.modelspace().add_text(t, height=150).set_placement((0, i * 500))
    return doc


def test_a_villas_levels_give_its_floor_heights_and_parapet():
    found = floor_levels(read_level_marks(_drawing(VILLA)))
    assert found.levels == {0: 0.35, 1: 3.65, 2: 6.95}  # the roof is floor 2, above the first floor
    assert found.heights == {0: 3.3, 1: 3.3, 2: 3.3}  # the roof's rooms: up to the stair roof
    assert found.roof == 2 and found.parapet == 1.7  # 8.65 on most sections
    assert found.parapet_of(2) == 1.7 and found.parapet_of(1) == DEFAULT_PARAPET_M
    assert found.summary() == ("ground floor +0.35, first floor +3.65, roof +6.95, stair roof +10.25, "
                               "parapet 1.70 m above the roof")


def test_levels_in_millimetres_and_floors_without_labels():
    found = floor_levels(read_level_marks(_drawing(["+0 GROUND FLOOR FFL", "+3200 FIRST FLOOR FFL"])))
    assert found.levels == {0: 0.0, 1: 3.2} and found.heights == {0: 3.2}
    assert found.height(1) == 3.2  # the building's typical height
    assert floor_levels([]).height(0) == DEFAULT_HEIGHT_M


class LevelModel(LocalModel):
    def __init__(self, answers):
        super().__init__(url="http://fake")
        self.answers, self.asked = answers, []

    def available(self):
        return True

    def ask(self, system, user, schema, max_tokens=1024):
        label = user.removeprefix("Label: ")
        self.asked.append(label)
        kind, floor = self.answers.get(label, ("other", None))
        return {"marks": kind, "floor": floor}


def test_the_model_reads_level_labels_the_rules_do_not_know():
    doc = _drawing(["+0.30 منسوب بلاطة الدور الأرضي", "+3.60 منسوب بلاطة الدور الأول", "+6.90 منسوب السطح",
                    "+8.10 منسوب الدروة", "+3.75 FFL", "+2.10 LINTEL LVL"])
    model = LevelModel({"منسوب بلاطة الدور الأرضي": ("floor", 0), "منسوب بلاطة الدور الأول": ("floor", 1),
                        "منسوب السطح": ("roof", None), "منسوب الدروة": ("parapet", None)})
    found = floor_levels(read_level_marks(doc, model))
    assert "+3.75 FFL" not in model.asked and len(model.asked) == 5  # a bare FFL names no floor
    assert found.levels == {0: 0.3, 1: 3.6, 2: 6.9} and found.heights == {0: 3.3, 1: 3.3} and found.parapet == 1.2
    assert floor_levels(read_level_marks(doc, None)).levels == {}  # without a model nothing is read


def test_floors_are_stacked_on_the_heights_below_them():
    ws = Workspace.new("P")
    b = ws.add_building(ws.add_location("SITE", "Site"), "HQ", "HQ")
    for ordinal, height in ((-1, 3.0), (0, 3.3), (1, 3.3), (2, 3.6), (4, 3.0)):
        ws.add_floor(b, ordinal, height=height)
    ws.restack(b)
    got = {f.ordinal: f.elevation for f in ws.building(b).floors}
    assert got == {-1: -3.0, 0: 0.0, 1: 3.3, 2: 6.6, 4: pytest.approx(13.8)}  # floor 3, missing: as high as 2
