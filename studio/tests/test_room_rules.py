"""What a room's label alone makes it (profiles/ncs.yaml): electrical and telecom rooms where the
label says so, what runs through the floors a shaft whatever it carries, and an abbreviation
that could as well be another room left for the language model to read."""

import pytest

from storeypath.profile import load_profile
from storeypath.types import SpaceType as T

RULES = load_profile("ncs")


def typed(label: str) -> T | None:
    kind, rule = RULES.classify(label, [], "")
    return kind if rule != "default" else None


@pytest.mark.parametrize("label, kind", [
    ("ELEC.", T.ELECTRICAL_ROOM), ("ELECTRICAL ROOM", T.ELECTRICAL_ROOM), ("EE.RM.", T.ELECTRICAL_ROOM),
    ("DB ROOM", T.ELECTRICAL_ROOM), ("LV ROOM", T.ELECTRICAL_ROOM), ("LV PANEL ROOM", T.ELECTRICAL_ROOM),
    ("SWITCHGEAR ROOM", T.ELECTRICAL_ROOM), ("SUB-STATION", T.ELECTRICAL_ROOM), ("غرفة كهرباء", T.ELECTRICAL_ROOM),
    ("IDF", T.IDF_ROOM), ("MDF", T.IDF_ROOM), ("TELECOM ROOM", T.IDF_ROOM), ("COMMS ROOM", T.IDF_ROOM),
    ("TEL. ROOM", T.IDF_ROOM), ("غرفة اتصالات", T.IDF_ROOM),
    ("MECH/ELEC ROOM", T.UTILITY), ("SERVER ROOM", T.UTILITY),
])
def test_a_label_that_says_so(label, kind):
    assert typed(label) == kind


@pytest.mark.parametrize("label", ["TELECOM RISER", "TELECOM SHAFT", "LV SHAFT", "LV RISER", "DB RISER", "ELEC. SHAFT",
                                   "ELEC RISER", "IDF RISER", "DUCT"])
def test_what_runs_through_the_floors_is_a_shaft(label):
    assert typed(label) == T.SHAFT


@pytest.mark.parametrize("label", ["E.R.", "E.R. WAITING", "E.R. DOCTORS", "TER", "TER.", "PRIVATE TER.", "ROOF TER.",
                                   "LV", "LV. RM", "DB", "MAIN DB", "DB ADMIN", "قسم الاتصالات", "قسم الكهرباء"])
def test_what_could_be_another_room_is_for_the_model_to_read(label):
    assert typed(label) not in (T.ELECTRICAL_ROOM, T.IDF_ROOM)


@pytest.mark.parametrize("label, kind", [("TELECOM OFFICE", T.OFFICE), ("TELECOM MANAGER", T.OFFICE), ("LV STORE", T.STORAGE),
                                         ("ELEC. CORRIDOR", T.CORRIDOR)])
def test_what_a_room_is_comes_before_what_it_serves(label, kind):
    assert typed(label) == kind
