from pathlib import Path

import pytest

from storeypath.convert import convert_floor
from storeypath.samples import office_floor, write_floor_dxf
from storeypath.workspace import Placement, SourceDrawing, Workspace


@pytest.fixture
def workspace(tmp_path: Path):
    """A one-building project with floor 2 drawn from the sample office plan.
    Returns (workspace, workspace dir, floor id, building id, sample cells)."""
    cells = office_floor(2)
    write_floor_dxf(tmp_path / "level-2.dxf", cells)
    ws = Workspace.new("Test Project")
    loc = ws.add_location("SITE", "Test Site")
    b_id = ws.add_building(loc, "HQ", "Headquarters")
    ws.building(b_id).placement = Placement(lon=10.0, lat=50.0, x=125, y=48, bearing=0)
    f_id = ws.add_floor(b_id, 2, source=SourceDrawing(path="level-2.dxf"))
    return ws, tmp_path, f_id, b_id, cells


@pytest.fixture
def converted(workspace):
    ws, d, f_id, b_id, cells = workspace
    report = convert_floor(ws, f_id, d)
    return ws, d, f_id, b_id, cells, report
