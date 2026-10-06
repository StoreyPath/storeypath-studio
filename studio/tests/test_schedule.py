"""Door and window sizes from a drawing's schedule of openings."""

import ezdxf

from storeypath.schedule import read_schedules, sizes_near


def _table(rows, headers, x0=0.0, y0=0.0, h=0.2, pitch=0.8, xs=None):
    """A schedule as drawn: a header row, then a row per type, each cell a text."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    xs = xs or [x0 + 1.5 * i for i in range(len(headers) + 1)]
    for x, t in zip(xs[1:], headers):
        msp.add_text(t, height=h).set_placement((x, y0), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    msp.add_text("TYPE", height=h).set_placement((xs[0], y0), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    for i, (tag, *cells) in enumerate(rows):
        y = y0 - (i + 1) * pitch
        for x, t in zip(xs, [tag, *cells]):
            if t:
                msp.add_text(t, height=h).set_placement((x, y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc


def test_a_schedule_in_metres_with_its_headers_misspelt():
    doc = _table([("D1", "1.80", "2.20", ""), ("W1", "2.40", "6.40", ".45"), ("W6", ".80", "1.00", "OVAL AS SHOWN")],
                 ["WIDTH", "HIEGTH", "SILL H"])
    sizes = sizes_near(doc, None)
    assert (sizes["D1"].width, sizes["D1"].height, sizes["D1"].sill) == (1.8, 2.2, None)
    assert (sizes["W1"].width, sizes["W1"].height, sizes["W1"].sill) == (2.4, 6.4, 0.45)
    assert sizes["W6"].sill is None  # "as shown": no number


def test_a_schedule_in_millimetres_with_a_count_of_leaves():
    doc = _table([("D1", "900", "2100", "2", "-"), ("W1", "1200", "1400", "2", "1000")],
                 ["WIDTH", "HEIGHT", "No.OF LEAVES", "SILL HIGHT"])
    sizes = sizes_near(doc, None)
    assert (sizes["W1"].width, sizes["W1"].height, sizes["W1"].sill) == (1.2, 1.4, 1.0)  # not 2 mm
    assert sizes["D1"].sill is None


class FakeModel:
    """Reads rows as a person would, and once invents a size the row does not have."""
    name = "fake"

    def available(self):
        return True

    def ask(self, system, user, schema, max_tokens=0):
        if "Row W5" in user:  # the sill written under the leaves column
            return {"width": 1200, "height": 1600, "sill_height": 800}
        if "Row W6" in user:  # an arch: 1200+ R=600
            return {"width": 1200, "height": 1800, "sill_height": 3000}  # 3000 is in no cell
        return {"width": None, "height": None, "sill_height": None}


def test_the_model_reads_rows_as_people_wrote_them_and_is_checked():
    xs = [0.0, 1.5, 3.0, 4.5, 6.0]
    doc = _table([("W1", "1200", "1400", "2", "1000"), ("W5", "1200", "1600", "800", ""),
                  ("W6", "1200", "1200+ R=600", "800", "")],
                 ["WIDTH", "HEIGHT", "No.OF LEAVES", "SILL"], xs=xs)
    sizes = sizes_near(doc, None, FakeModel())
    assert sizes["W1"].sill == 1.0  # the model said nothing: the rules' reading stands
    assert sizes["W5"].sill == 0.8  # under the wrong column, read all the same
    assert sizes["W6"].height == 1.8 and sizes["W6"].sill is None  # 1200 + 600; 3000 is not in the row
    assert len(read_schedules(doc)) == 1
