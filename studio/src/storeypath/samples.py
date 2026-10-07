"""Synthetic floor plans with a known correct answer, for tests and demos.

The generated DXF looks like a typical architectural plan: millimetre units,
NCS layer names, double-line walls with door gaps, door blocks, windows in the
outside walls, room labels in three styles (TEXT, MTEXT and a tag block with
attributes), elevator car blocks, stair treads, furniture, a dimension and a
title, placed away from the drawing origin. Room outlines (A-AREA) can be left
out to get a plan whose spaces must be found from its walls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

WALL = 0.2  # m
LAYERS = {
    "A-WALL": 7, "A-GLAZ": 4, "A-AREA": 3, "A-AREA-IDEN": 2, "A-DOOR": 4, "A-EQPM-VERT": 6,
    "A-FLOR-STRS": 5, "I-FURN": 8, "A-ANNO-DIMS": 1, "A-ANNO-TTLB": 1,
}
WINDOW_TYPES = {"office", "meeting_room", "open_area", "lobby", "kitchen"}


@dataclass
class Door:
    x: float  # centre of the opening, on the wall centre line
    y: float
    axis: str  # "h": wall runs along x, "v": along y
    swing: int  # +1 / -1: side the leaf opens to (y for "h", x for "v")
    width: float = 0.9
    block: bool = True  # False: just an opening in the wall, no door drawn


@dataclass
class Cell:
    x0: float
    y0: float
    x1: float
    y1: float
    label: list[str] | None
    expected_type: str
    door: Door | None = None
    label_style: str = "text"  # text | mtext | tag
    blocks: list[str] = field(default_factory=list)
    furniture: bool = False
    outline: list[tuple[float, float]] | None = None  # any shape (an L-shaped room); x0…y1 are its bounds
    label_at: tuple[float, float] | None = None  # where its label goes, when not its middle

    @property
    def shape(self):
        return Polygon(self.outline) if self.outline else box(self.x0, self.y0, self.x1, self.y1)

    @property
    def center(self) -> tuple[float, float]:
        if self.label_at:
            return self.label_at
        if self.outline:
            p = self.shape.representative_point()
            return p.x, p.y
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    @property
    def name(self) -> str | None:
        names = [ln for ln in (self.label or []) if not ln[0].isdigit()]
        return " ".join(names) or None

    @property
    def number(self) -> str | None:
        return next((ln for ln in (self.label or []) if ln[0].isdigit()), None)


def office_floor(ordinal: int) -> list[Cell]:
    """A 48 × 20 m office floor: core on the west, corridor east-west, offices on both sides."""

    def num(seq: int) -> str:
        return f"{ordinal}{seq:02d}"

    cells = [
        Cell(0, 0, 4, 6, ["MEN WC"], "restroom", Door(3.55, 6, "h", -1)),
        Cell(4, 0, 8, 6, ["WOMEN WC"], "restroom", Door(6, 6, "h", -1)),
        Cell(0, 6, 3, 10, ["LIFT"], "elevator", Door(3, 8, "v", +1), blocks=["ELEVATOR_CAR"]),
        Cell(0, 10, 3, 14, None, "elevator", Door(3, 12, "v", +1), blocks=["ELEVATOR_CAR"]),
        Cell(3, 6, 8, 14, ["ELEV. LOBBY"], "lobby", Door(8, 10, "v", +1, 1.8)),
        Cell(0, 14, 8, 20, ["STAIR A"], "stairs", Door(5.5, 14, "h", +1)),
        Cell(8, 8.5, 48, 11.5, ["CORRIDOR"], "corridor", Door(48, 10, "v", -1, 1.8)),
    ]
    south = [(8, 12, "OFFICE"), (12, 16, "OFFICE"), (16, 20, "OFFICE"), (20, 28, "MEETING ROOM"),
             (28, 32, "OFFICE"), (32, 36, "OFFICE"), (36, 40, "OFFICE"), (40, 44, "OFFICE"), (44, 48, "OFFICE")]
    north = [(8, 12, "STORAGE"), (12, 16, "PANTRY"), (16, 20, "OFFICE"), (20, 24, "OFFICE"), (24, 28, None),
             (28, 32, "OFFICE"), (32, 36, "ELEC."), (36, 48, "RECEPTION" if ordinal == 0 else "OPEN OFFICE")]
    types = {"OFFICE": "office", "MEETING ROOM": "meeting_room", "STORAGE": "storage", "PANTRY": "kitchen",
             "ELEC.": "utility", "RECEPTION": "lobby", "OPEN OFFICE": "open_area", None: "unspecified"}
    seq = 1
    for x0, x1, name in south:
        style = "mtext" if name == "MEETING ROOM" else "text"
        cells.append(Cell(x0, 0, x1, 8.5, [name, num(seq)], types[name], Door((x0 + x1) / 2, 8.5, "h", -1),
                          label_style=style, furniture=name == "OFFICE"))
        seq += 1
    for x0, x1, name in north:
        label = [name, num(seq)] if name else [num(seq)]
        style = "tag" if name == "STORAGE" else "text"
        cells.append(Cell(x0, 11.5, x1, 20, label, types[name], Door((x0 + x1) / 2, 11.5, "h", +1),
                          label_style=style, furniture=name == "OFFICE"))
        seq += 1
    return cells


def simple_office() -> list[Cell]:
    """wayfinder's "simple-office" floor (its PDF test fixture, same rooms and
    numbers), with an open office on the east whose two halves are numbered
    separately (to be divided into zones in review) and a shaft in its corner."""
    cells = []
    north = [(0, 3.6, ["OFFICE", "F0-301"], "office"), (3.6, 7.2, ["OFFICE", "F0-302"], "office"),
             (7.2, 12, ["MANAGER OFFICE", "F0-303"], "office"), (12, 18, ["MEETING ROOM", "F0-304"], "meeting_room"),
             (18, 21.6, ["PANTRY", "F0-305"], "kitchen")]
    for x0, x1, label, kind in north:
        cells.append(Cell(x0, 8, x1, 13, label, kind, Door((x0 + x1) / 2, 8, "h", +1), furniture=kind == "office"))
    cells.append(Cell(0, 6, 21.6, 8, ["CORRIDOR", "F0-C01"], "corridor", Door(0, 7, "v", +1, 1.2)))
    cells.append(Cell(0, 0, 6, 6, ["OFFICE", "F0-315"], "office", Door(1.8, 6, "h", -1),
                      outline=[(0, 0), (3.6, 0), (3.6, 3), (6, 3), (6, 6), (0, 6)], label_at=(1.8, 3.2), furniture=True))
    south = [(3.6, 0, 6, 3, ["WC", "F0-316"], "restroom", Door(4.8, 3, "h", -1, 0.8), []),
             (6, 0, 9, 6, ["STAIR", "F0-317"], "stairs", Door(7.5, 6, "h", -1), []),
             (9, 3, 11.4, 6, ["LIFT", "F0-318"], "elevator", Door(10.2, 6, "h", -1, 1.0), ["ELEVATOR_CAR"]),
             (9, 0, 11.4, 3, ["ELEC. ROOM", "F0-319"], "utility", Door(11.4, 1.5, "v", -1), []),
             (11.4, 0, 15, 6, ["IT / SERVER ROOM", "F0-320"], "utility", Door(13.2, 6, "h", -1), []),
             (15, 0, 18, 6, ["STORAGE", "F0-321"], "storage", Door(16.5, 6, "h", -1), []),
             (18, 0, 21.6, 6, ["PRAYER ROOM", "F0-322"], "prayer_room", Door(19.8, 6, "h", -1), [])]
    for x0, y0, x1, y1, label, kind, door, blocks in south:
        cells.append(Cell(x0, y0, x1, y1, label, kind, door, blocks=blocks))
    cells.append(Cell(21.6, 0, 30, 13, ["OPEN OFFICE", "F0-330"], "open_area", Door(21.6, 7, "v", +1, 1.2),
                      outline=[(22.8, 0), (30, 0), (30, 13), (21.6, 13), (21.6, 1.2), (22.8, 1.2)], label_at=(25.8, 10)))
    cells.append(Cell(21.6, 0, 22.8, 1.2, ["SHAFT"], "shaft"))
    return cells


def _setup(doc) -> None:
    for name, color in LAYERS.items():
        doc.layers.add(name, color=color)
    elev = doc.blocks.new("ELEVATOR_CAR")
    elev.add_lwpolyline([(-1000, -1000), (1000, -1000), (1000, 1000), (-1000, 1000)], close=True)
    elev.add_line((-1000, -1000), (1000, 1000))
    elev.add_line((-1000, 1000), (1000, -1000))
    desk = doc.blocks.new("DESK")
    desk.add_lwpolyline([(-800, -400), (800, -400), (800, 400), (-800, 400)], close=True)
    tag = doc.blocks.new("ROOM_TAG")
    tag.add_attdef("NAME", (0, 200), dxfattribs={"height": 250}).set_placement((0, 200), align=TextEntityAlignment.MIDDLE_CENTER)
    tag.add_attdef("NUMBER", (0, -200), dxfattribs={"height": 250}).set_placement((0, -200), align=TextEntityAlignment.MIDDLE_CENTER)


def _door_block(doc, width_mm: int) -> str:
    name = f"DOOR_{width_mm}"
    if name not in doc.blocks:
        blk = doc.blocks.new(name)
        blk.add_line((0, 0), (0, width_mm))
        blk.add_arc((0, 0), width_mm, 0, 90)
    return name


def _windows(cells: list[Cell]) -> list[tuple[float, float, float]]:
    """(x centre, y of the wall, width) of a window in the outside wall of each
    office-like room along the long sides of the floor."""
    ymin, ymax = min(c.y0 for c in cells), max(c.y1 for c in cells)
    out = []
    for c in cells:
        if c.expected_type in WINDOW_TYPES and not c.outline:
            width = min(1.8, c.x1 - c.x0 - 1.2)
            for y in (ymin, ymax):
                if y in (c.y0, c.y1):
                    out.append(((c.x0 + c.x1) / 2, y, width))
    return out


def write_floor_dxf(
    path: str | Path,
    cells: list[Cell],
    *,
    origin=(125.0, 48.0),
    title: str = "FLOOR PLAN",
    area_outlines: bool = True,
    walls: str = "polylines",
) -> None:
    """Write a floor plan. ``area_outlines=False`` leaves out the room outlines.
    ``walls`` is how walls are drawn: "polylines" (closed outlines), "lines"
    (separate LINEs) or "hatch" (a solid fill only)."""
    write_sheet_dxf(path, [(cells, origin, title)], area_outlines=area_outlines, walls=walls)


def write_sheet_dxf(path: str | Path, plans: list[tuple[list[Cell], tuple[float, float], str]], **kw) -> None:
    """Write several floor plans side by side in one drawing, as architects often
    do: each is (cells, origin in meters, title)."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = ezdxf.units.MM
    _setup(doc)
    for cells, origin, title in plans:
        _draw_floor(doc, cells, origin=origin, title=title, **kw)
    doc.saveas(path)


def _draw_floor(doc, cells: list[Cell], *, origin, title: str, area_outlines: bool = True,
                walls: str = "polylines") -> None:
    msp = doc.modelspace()
    ox, oy = origin

    def mm(x: float, y: float) -> tuple[float, float]:
        return round((ox + x) * 1000, 3), round((oy + y) * 1000, 3)

    # walls: cell edges thickened, minus the door and window openings
    wall_mass = unary_union([c.shape.exterior for c in cells]).buffer(WALL / 2, join_style="mitre")
    gaps = []
    for c in cells:
        if c.door:
            d = c.door
            h = d.width / 2
            seg = LineString([(d.x - h, d.y), (d.x + h, d.y)] if d.axis == "h" else [(d.x, d.y - h), (d.x, d.y + h)])
            gaps.append(seg.buffer(WALL, cap_style="flat"))
    windows = _windows(cells)
    for x, y, width in windows:
        gaps.append(LineString([(x - width / 2, y), (x + width / 2, y)]).buffer(WALL, cap_style="flat"))
    wall_mass = wall_mass.difference(unary_union(gaps))
    for poly in getattr(wall_mass, "geoms", [wall_mass]):
        rings = [[mm(*p) for p in ring.coords] for ring in [poly.exterior, *poly.interiors]]
        if walls == "hatch":
            hatch = msp.add_hatch(color=7, dxfattribs={"layer": "A-WALL"})
            for i, pts in enumerate(rings):
                hatch.paths.add_polyline_path(pts[:-1], is_closed=True, flags=1 if i == 0 else 16)
            continue
        for pts in rings:
            if walls == "lines":
                for a, b in zip(pts, pts[1:]):
                    msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})
            else:
                msp.add_lwpolyline(pts[:-1], close=True, dxfattribs={"layer": "A-WALL"})
    # windows: outer face, glass and inner face across each opening
    for x, y, width in windows:
        for dy in (-WALL / 2, 0, WALL / 2):
            msp.add_line(mm(x - width / 2, y + dy), mm(x + width / 2, y + dy), dxfattribs={"layer": "A-GLAZ"})

    for c in cells:
        inset = WALL / 2
        if area_outlines:
            inner = c.shape.buffer(-inset, join_style="mitre")
            msp.add_lwpolyline([mm(*p) for p in list(inner.exterior.coords)[:-1]], close=True,
                               dxfattribs={"layer": "A-AREA"})
        cx, cy = c.center
        if c.label:
            if c.label_style == "mtext":
                msp.add_mtext("\\P".join(c.label), dxfattribs={"layer": "A-AREA-IDEN", "char_height": 250}).set_location(
                    mm(cx, cy), attachment_point=ezdxf.enums.MTextEntityAlignment.MIDDLE_CENTER)
            elif c.label_style == "tag":
                ref = msp.add_blockref("ROOM_TAG", mm(cx, cy), dxfattribs={"layer": "A-AREA-IDEN"})
                ref.add_auto_attribs({"NAME": c.label[0], "NUMBER": c.label[1]})
            else:
                for i, line in enumerate(c.label):
                    msp.add_text(line, height=250, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(
                        mm(cx, cy + 0.3 - 0.6 * i), align=TextEntityAlignment.MIDDLE_CENTER)
        for name in c.blocks:
            msp.add_blockref(name, mm(cx, cy), dxfattribs={"layer": "A-EQPM-VERT"})
        if c.furniture:
            msp.add_blockref("DESK", mm(cx, c.y0 + 2 if c.y0 < 1 else c.y1 - 2), dxfattribs={"layer": "I-FURN"})
            msp.add_text("DESK", height=150, dxfattribs={"layer": "I-FURN"}).set_placement(mm(cx, cy - 1.5))
        if c.expected_type == "stairs":
            x = c.x0 + 0.5
            while x < c.x1 - 0.5:
                msp.add_line(mm(x, c.y0 + 1.5), mm(x, c.y1 - 0.5), dxfattribs={"layer": "A-FLOR-STRS"})
                x += 0.3
        if c.door and c.door.block:
            d = c.door
            name = _door_block(doc, round(d.width * 1000))
            h = d.width / 2
            if d.axis == "h":
                rot, ins = (0, (d.x - h, d.y)) if d.swing > 0 else (180, (d.x + h, d.y))
            else:
                rot, ins = (270, (d.x, d.y + h)) if d.swing > 0 else (90, (d.x, d.y - h))
            msp.add_blockref(name, mm(*ins), dxfattribs={"layer": "A-DOOR", "rotation": rot})

    xmax = max(c.x1 for c in cells)
    msp.add_linear_dim(base=mm(0, -2), p1=mm(0, 0), p2=mm(xmax, 0), dxfattribs={"layer": "A-ANNO-DIMS"}).render()
    msp.add_text(title, height=600, dxfattribs={"layer": "A-ANNO-TTLB"}).set_placement(mm(0, -4.5))


def build_demo(directory: str | Path) -> tuple[Path, Path]:
    """Create sample drawings, a workspace and an exported package. Returns
    (workspace path, package path). The annex drawings have no room outlines, so
    its spaces are found from the walls."""
    from .convert import convert_floor
    from .export import export_package
    from .workspace import Placement, SourceDrawing, Workspace

    directory = Path(directory)
    (directory / "drawings").mkdir(parents=True, exist_ok=True)
    ws = Workspace.new("Demo Campus")
    loc = ws.add_location("DEMO", "Demo Campus", address="1 Example Street")
    buildings = [
        ("HQ", "Headquarters", Placement(lon=46.6761, lat=24.7127, x=125, y=48, bearing=20), 3),
        ("ANNEX", "Annex", Placement(lon=46.6769, lat=24.7121, x=125, y=48, bearing=110), 2),
    ]
    for code, name, placement, n_floors in buildings:
        b_id = ws.add_building(loc, code, name)
        ws.building(b_id).placement = placement
        for ordinal in range(n_floors):
            rel = Path("drawings") / f"{code.lower()}-level-{ordinal}.dxf"
            write_floor_dxf(directory / rel, office_floor(ordinal), title=f"{name.upper()} LEVEL {ordinal}",
                            area_outlines=code == "HQ", walls="polylines" if code == "HQ" else "lines")
            ws.add_floor(b_id, ordinal, name="Ground floor" if ordinal == 0 else f"Floor {ordinal}",
                         source=SourceDrawing(path=str(rel)))
    for _, _, _, floor_id in ws.iter_floors():
        convert_floor(ws, floor_id, directory)
    ws_path = directory / "demo.spproj"
    pkg_path = directory / "demo.storeypath"
    export_package(ws, pkg_path)
    ws.save(ws_path)
    return ws_path, pkg_path
