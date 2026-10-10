"""Synthetic floor plans with a known correct answer, for tests and demos.

The generated DXF looks like a typical architectural plan: millimetre units,
NCS layer names, double-line walls with door gaps, door blocks, windows in the
outside walls, room labels in three styles (TEXT, MTEXT and a tag block with
attributes), elevator car blocks, stair treads, furniture, a dimension and a
title, placed away from the drawing origin. Room outlines (A-AREA) can be left
out to get a plan whose spaces must be found from its walls.
"""

from __future__ import annotations

import math
import random
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
    double: bool = False  # two leaves, one hinged at each jamb


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
    doors: list[Door] = field(default_factory=list)  # more doors than its own (an entrance from outside)
    windows: int | None = None  # windows in each outside wall along the floor's long sides (None: one, by its type)
    labels: list[tuple[list[str], tuple[float, float]]] = field(default_factory=list)  # more labels, each where it goes

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
    """(x centre, y of the wall, width) of the windows in the outside wall of each
    office-like room along the long sides of the floor: one each, or as many as the
    room says (``Cell.windows``), evenly spaced."""
    ymin, ymax = min(c.y0 for c in cells), max(c.y1 for c in cells)
    out = []
    for c in cells:
        n = c.windows if c.windows is not None else 1 if c.expected_type in WINDOW_TYPES else 0
        if n <= 0 or c.outline:
            continue
        bay = (c.x1 - c.x0) / n
        width = min(1.8, bay - 1.2)
        for y in (ymin, ymax):
            if y in (c.y0, c.y1):
                out.extend((c.x0 + (i + 0.5) * bay, y, width) for i in range(n))
    return out


def _leaves(d: Door) -> list[tuple[tuple[float, float], float, float, bool]]:
    """Where a door's leaves are drawn: (hinge point, width, rotation in degrees,
    mirrored) of each, for the door block (a leaf along its +y, open; its swing from +x).
    One leaf hinged at one jamb, or two at both."""
    h = d.width / 2
    if d.axis == "h":
        jambs, along = ((d.x - h, d.y), (d.x + h, d.y)), (1.0, 0.0)
        opens = (0.0, float(d.swing))
    else:
        jambs, along = ((d.x, d.y - h), (d.x, d.y + h)), (0.0, 1.0)
        opens = (float(d.swing), 0.0)

    def leaf(hinge, closed, width):
        # the block's +x (closed) onto ``closed``, its +y (open) onto ``opens``: turned, or mirrored and turned
        if closed[0] * opens[1] - closed[1] * opens[0] > 0:
            return hinge, width, math.degrees(math.atan2(closed[1], closed[0])) % 360, False
        return hinge, width, (math.degrees(math.atan2(closed[1], closed[0])) + 180) % 360, True

    if not d.double:
        # as a single door always was: hinged where its swing puts the leaf's back
        if d.axis == "h":
            hinge, closed = (jambs[0], along) if d.swing > 0 else (jambs[1], (-1.0, 0.0))
        else:
            hinge, closed = (jambs[1], (0.0, -1.0)) if d.swing > 0 else (jambs[0], along)
        return [leaf(hinge, closed, d.width)]
    return [leaf(jambs[0], along, h), leaf(jambs[1], (-along[0], -along[1]), h)]


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
        for d in ([c.door] if c.door else []) + c.doors:
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
        for lines, (lx, ly) in c.labels:
            for i, line in enumerate(lines):
                msp.add_text(line, height=250, dxfattribs={"layer": "A-AREA-IDEN"}).set_placement(
                    mm(lx, ly + 0.3 - 0.6 * i), align=TextEntityAlignment.MIDDLE_CENTER)
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
        for d in ([c.door] if c.door else []) + c.doors:
            if not d.block:
                continue
            for hinge, width, rot, mirrored in _leaves(d):
                name = _door_block(doc, round(width * 1000))
                attribs = {"layer": "A-DOOR", "rotation": rot}
                if mirrored:
                    attribs["xscale"] = -1
                msp.add_blockref(name, mm(*hinge), dxfattribs=attribs)

    xmax = max(c.x1 for c in cells)
    msp.add_linear_dim(base=mm(0, -2), p1=mm(0, 0), p2=mm(xmax, 0), dxfattribs={"layer": "A-ANNO-DIMS"}).render()
    msp.add_text(title, height=600, dxfattribs={"layer": "A-ANNO-TTLB"}).set_placement(mm(0, -4.5))


def build_demo(directory: str | Path) -> tuple[Path, list[Path]]:
    """The tests' campus: plain office floors whose answer is known (office_floor), as
    sample drawings, a workspace and its buildings' packages. Returns (workspace path,
    package paths: one per building). The annex drawings have no room outlines, so its
    spaces are found from the walls. `storeypath demo` builds the showcase instead
    (build_showcase)."""
    from .convert import convert_floor
    from .export import export_package
    from .ids import make_id
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
    packages = []  # one per building
    for loc in ws.locations:
        for b in loc.buildings:
            packages.append(directory / f"demo-{b.code}.storeypath")
            export_package(ws, packages[-1], building=make_id(ws.id, loc.code, b.code))
    ws.save(ws_path)
    return ws_path, packages


# ---- the showcase: what `storeypath demo` builds ------------------------------------------
#
# A small campus, all of it made up: a main building of three floors (a reception with a
# wayfinding kiosk at the entrance, a café, a lounge, meeting rooms, open offices, offices
# for every grade up to the executives' floor, its lifts and stairs on every floor) and a
# pavilion of two, drawn without room outlines (its rooms found from its walls). Furnished,
# finished, a few rooms left for a person to review, and the same every time it is built
# (its project code, its items' tags), so that its pictures can be taken again.

SHOWCASE_NAME = "Demo Campus"
SHOWCASE_CODE = "CAMP05"  # the project's code: its IDs the same every time
SHOWCASE_SEED = 2026  # the items' tags, drawn the same every time
ORIGIN = (125.0, 48.0)  # where the plans are drawn, metres: away from the drawing's origin
MAIN_W, MAIN_D = 56.0, 21.0  # the main building, metres
CORRIDOR = (8.5, 11.5)  # its corridor's south and north walls
PAV_W, PAV_D = 30.0, 13.0  # the pavilion, metres


def _bays(width: float) -> int:
    """How many windows a room this wide has in its outside wall: one every 4 m or so."""
    return max(1, round(width / 4))


def _row(rooms: list[tuple], south: bool) -> list[Cell]:
    """The main building's rooms along its corridor, south of it or north: each (x0, x1,
    label, type[, options]); a door in the corridor wall at its middle, opening into the
    room, windows in its outside wall."""
    y0, y1 = (0.0, CORRIDOR[0]) if south else (CORRIDOR[1], MAIN_D)
    wall, swing = (CORRIDOR[0], -1) if south else (CORRIDOR[1], +1)
    cells = []
    for x0, x1, label, kind, *more in rooms:
        o = more[0] if more else {}
        door = o.get("door") or Door((x0 + x1) / 2, wall, "h", swing, o.get("door_width", 0.9), double=o.get("double", False))
        windows = o.get("windows", _bays(x1 - x0) if kind in WINDOW_TYPES else 0)
        cells.append(Cell(x0, y0, x1, y1, label, kind, door, label_style=o.get("style", "text"), doors=o.get("doors", []),
                          windows=windows))
    return cells


def main_floor(ordinal: int) -> list[Cell]:
    """A floor of the showcase's main building, 56 × 21 m: the core on the west (two lifts,
    the stairs, the restrooms, the lift lobby), a corridor east-west and rooms on both sides.
    Ground floor: the reception with the entrance, a café, a lounge, meeting rooms, an open
    office; first: open offices and offices; second: the executives', with a board room."""
    lift2 = ["LIFT"] if ordinal else None  # the ground floor's second lift has no label: Review asks
    core = [
        Cell(0, 0, 5, 6, ["WOMEN WC"], "restroom", Door(4.0, 6, "h", -1)),
        Cell(5, 0, 10, 6, ["MEN WC"], "restroom", Door(7.5, 6, "h", -1)),
        Cell(0, 6, 3, 10, ["LIFT"], "elevator", Door(3, 8, "v", +1, 1.1, block=False), blocks=["ELEVATOR_CAR"]),
        Cell(0, 10, 3, 14, lift2, "elevator", Door(3, 12, "v", +1, 1.1, block=False), blocks=["ELEVATOR_CAR"]),
        Cell(3, 6, 10, 14, ["LIFT LOBBY"], "lobby", Door(10, 10, "v", +1, 1.8, double=True)),
        Cell(0, 14, 10, MAIN_D, ["STAIR", "1"], "stairs", Door(6.5, 14, "h", +1)),
        Cell(10, CORRIDOR[0], MAIN_W, CORRIDOR[1], ["CORRIDOR"], "corridor", Door(MAIN_W, 10, "v", -1, 1.8, double=True)),
    ]
    if ordinal == 0:
        south = [
            (10, 22, ["RECEPTION", "001"], "lobby", {"door": Door(16, CORRIDOR[0], "h", -1, 1.8, double=True),
                                                    "doors": [Door(16, 0, "h", +1, 2.0, double=True)], "windows": 2}),
            (22, 26, ["OFFICE", "002"], "office"),
            (26, 34, ["MEETING ROOM", "003"], "meeting_room", {"style": "mtext"}),
            (34, 46, ["CAFE", "004"], "kitchen"),
            (46, 56, ["LOUNGE", "005"], "unspecified", {"windows": 2}),  # a name the rules do not know: Review asks
        ]
        north = [
            (10, 14, ["STORAGE", "006"], "storage", {"style": "tag"}),
            (14, 18, ["COPY ROOM", "007"], "unspecified", {"windows": 1}),  # the same
            (18, 30, ["OPEN OFFICE", "008"], "open_area"),
            (30, 34, ["OFFICE", "009"], "office"),
            (34, 38, ["OFFICE", "010"], "office"),
            (38, 42, ["OFFICE", "011"], "office"),
            (42, 46, ["ELEC.", "012"], "utility"),
            (46, 56, ["CONFERENCE", "013"], "meeting_room"),
        ]
    elif ordinal == 1:
        south = [
            (10, 22, ["OPEN OFFICE", "101"], "open_area"),
            (22, 26, ["OFFICE", "102"], "office"), (26, 30, ["OFFICE", "103"], "office"), (30, 34, ["OFFICE", "104"], "office"),
            (34, 42, ["MEETING ROOM", "105"], "meeting_room", {"style": "mtext"}),
            (42, 46, ["OFFICE", "106"], "office"), (46, 50, ["OFFICE", "107"], "office"),
            (50, 56, ["MANAGER", "108"], "office"),
        ]
        north = [
            (10, 14, ["STORAGE", "109"], "storage", {"style": "tag"}),
            (14, 18, ["PANTRY", "110"], "kitchen"),
            (18, 30, ["OPEN OFFICE", "111"], "open_area"),
            (30, 34, ["OFFICE", "112"], "office"), (34, 38, ["OFFICE", "113"], "office"),
            (38, 42, ["114"], "unspecified", {"windows": 1}),  # a number and no name: Review asks what it is
            (42, 46, ["ELEC.", "115"], "utility"),
            (46, 56, ["HUDDLE", "116"], "meeting_room"),
        ]
    else:
        south = [
            (10, 18, ["DIRECTOR", "201"], "office"), (18, 26, ["DIRECTOR", "202"], "office"),
            (26, 38, ["BOARD ROOM", "203"], "meeting_room", {"door_width": 1.8, "double": True, "windows": 3}),
            (38, 46, ["OFFICE", "204"], "office"),
            (46, 56, ["PRESIDENT OFFICE", "205"], "office"),
        ]
        north = [
            (10, 14, ["STORAGE", "206"], "storage", {"style": "tag"}), (14, 18, ["PANTRY", "207"], "kitchen"),
            (18, 26, ["OFFICE", "208"], "office"), (26, 34, ["MEETING ROOM", "209"], "meeting_room"),
            (34, 38, ["OFFICE", "210"], "office"), (38, 42, ["OFFICE", "211"], "office"),
            (42, 46, ["ELEC.", "212"], "utility"), (46, 51, ["OFFICE", "213"], "office"), (51, 56, ["OFFICE", "214"], "office"),
        ]
    return core + _row(south, True) + _row(north, False)


def pavilion_floor(ordinal: int) -> list[Cell]:
    """A floor of the showcase's pavilion, 30 × 13 m: its stairs, lift, a restroom, a
    meeting room and offices along a corridor, and on the east an open office. On the
    ground floor the open office holds two rooms' labels, the office's and a quiet
    zone's, with no wall between them: Studio divides it into two zones, for a person
    to check."""
    def n(k: int) -> str:
        return f"P{ordinal}-{k:02d}"

    cells = [
        Cell(0, 0, 4, 6, ["STAIR", n(1)], "stairs", Door(2, 6, "h", -1)),
        Cell(4, 3, 6.5, 6, ["LIFT", n(2)], "elevator", Door(5.25, 6, "h", -1, 1.0, block=False), blocks=["ELEVATOR_CAR"]),
        Cell(4, 0, 6.5, 3, ["SHAFT"], "shaft"),
        Cell(6.5, 0, 10, 6, ["WC", n(3)], "restroom", Door(8.25, 6, "h", -1, 0.8)),
        Cell(10, 0, 14, 6, ["STORAGE", n(4)], "storage", Door(12, 6, "h", -1)),
        Cell(14, 0, 24, 6, ["MEETING ROOM", n(5)], "meeting_room", Door(19, 6, "h", -1), windows=2),
        Cell(0, 6, 24, 8, ["CORRIDOR"], "corridor", Door(0, 7, "v", +1, 1.2) if ordinal == 0 else None),
    ]
    north = [(0, 4, "OFFICE"), (4, 8, "OFFICE"), (8, 12, "OFFICE"), (12, 16, "OFFICE"), (16, 20, "OFFICE"), (20, 24, "PANTRY")]
    for i, (x0, x1, name) in enumerate(north):
        cells.append(Cell(x0, 8, x1, PAV_D, [name, n(10 + i)], "office" if name == "OFFICE" else "kitchen",
                          Door((x0 + x1) / 2, 8, "h", +1), windows=1))
    quiet = [(["QUIET ZONE", n(21)], (27.0, 3.0))] if ordinal == 0 else []
    cells.append(Cell(24, 0, PAV_W, PAV_D, ["OPEN OFFICE", n(20)], "open_area", Door(24, 7, "v", +1, 1.2),
                      label_at=(27.0, 10.0), labels=quiet, windows=1))
    return cells


# what rooms are finished in (format 0.9), by their name, or "<name>@<floor's ordinal>" on one
# floor: a person's choice, kept as a correction (choosing finishes does not check a room)
FINISHES_BY_NAME = {
    "RECEPTION": ("FLOOR-MARBLE-WHITE", "WALL-WOOD-SLATS"),
    "LIFT LOBBY": ("FLOOR-MARBLE-BEIGE", "WALL-STONE"),
    "CORRIDOR@0": ("FLOOR-TERRAZZO-LIGHT", None),
    "CAFE": ("FLOOR-LVT-OAK", "WALL-PAINT-TERRACOTTA"),
    "LOUNGE": ("FLOOR-WOOD-OAK", "WALL-PAPER-GEOMETRIC"),
    "MEETING ROOM": ("FLOOR-CARPET-PATTERN", "WALL-PAINT-NAVY"),
    "CONFERENCE": ("FLOOR-CARPET-PATTERN", "WALL-PAINT-GREEN"),
    "HUDDLE": ("FLOOR-CARPET-GREEN", "WALL-PAINT-SAND"),
    "BOARD ROOM": ("FLOOR-WOOD-HERRINGBONE", "WALL-WOOD-WALNUT"),
    "PRESIDENT OFFICE": ("FLOOR-CARPET-CHARCOAL", "WALL-WOOD-WALNUT"),
    "DIRECTOR": ("FLOOR-CARPET-NAVY", "WALL-PAINT-OFFWHITE"),
    "OFFICE@2": ("FLOOR-CARPET-CHARCOAL", "WALL-PAPER-LINEN"),
    "OPEN OFFICE@0": ("FLOOR-CARPET-WARMGREY", None),
    "OPEN OFFICE@1": ("FLOOR-CARPET-GREY", None),
    "WOMEN WC": ("FLOOR-PORCELAIN-DARK", "WALL-TILE-GREY"),
    "MEN WC": ("FLOOR-PORCELAIN-DARK", "WALL-TILE-GREY"),
    "WC": ("FLOOR-PORCELAIN-GREY", "WALL-TILE-MOSAIC"),
    "COPY ROOM": ("FLOOR-VINYL-GREY", None),
    "PANTRY": ("FLOOR-PORCELAIN-BEIGE", "WALL-TILE-WHITE"),
}


class _Furnisher:
    """Items placed on one floor, by its rooms' cells (metres of the plan, before ORIGIN),
    each turned so that its front (where its user sits, or its screen) faces where it
    should; their tags drawn from ``rng``: the same every time."""

    def __init__(self, ws, floor_id: str, rng: random.Random):
        from .catalogue import default_catalogue

        self.ws, self.floor_id, self.rng = ws, floor_id, rng
        self.types = {t.code: t for t in default_catalogue().types}

    def add(self, code: str, x: float, y: float, rotation: float = 0.0, **values) -> None:
        """An item at (x, y), turned ``rotation``° counter-clockwise: 0, its front to the south."""
        from .ids import CROCKFORD_ALPHABET, ITEM_ID_SYMBOLS, _item_id_of, item_check_symbol
        from .workspace import Item

        tag = None
        while tag is None or tag in self.ws.items:  # (as new_item_id draws one, but from rng: the same every time)
            symbols = "".join(self.rng.choice(CROCKFORD_ALPHABET) for _ in range(ITEM_ID_SYMBOLS))
            tag = _item_id_of(symbols + item_check_symbol(symbols))
        self.ws.items[tag] = Item(id=tag, type=code, floor_id=self.floor_id, x=round(ORIGIN[0] + x, 3),
                                  y=round(ORIGIN[1] + y, 3), rotation=rotation % 360, values=values)

    @staticmethod
    def south(c: Cell) -> bool:
        """Whether the room is on the south side, its windows to the south."""
        return c.y0 == 0

    def desk(self, c: Cell, code: str, x: float | None = None) -> None:
        """A desk by the window, its user's back to it, facing the door; its chair and what
        goes with its grade (drawn round it) between it and the window."""
        t = self.types[code]
        behind = 1.4 if t.grade in ("director", "c_level", "president") else 0.74 if t.grade == "junior" else 0.8
        gap = behind + 0.25 + t.depth / 2
        x = (c.x0 + c.x1) / 2 if x is None else x
        if self.south(c):
            self.add(code, x, c.y0 + WALL / 2 + gap, 0)
        else:
            self.add(code, x, c.y1 - WALL / 2 - gap, 180)

    def by_door_wall(self, c: Cell, code: str, along: float, **values) -> None:
        """Against the wall the door is in, its middle ``along`` metres from the room's west
        side, facing into the room."""
        t = self.types[code]
        if self.south(c):
            self.add(code, c.x0 + along, c.y1 - WALL / 2 - t.depth / 2 - 0.05, 0, **values)
        else:
            self.add(code, c.x0 + along, c.y0 + WALL / 2 + t.depth / 2 + 0.05, 180, **values)

    def on_side(self, c: Cell, code: str, east: bool, y: float | None = None, **values) -> None:
        """Against the room's east or west wall, facing into the room: a screen, a sofa."""
        t = self.types[code]
        y = (c.y0 + c.y1) / 2 if y is None else y
        if east:
            self.add(code, c.x1 - WALL / 2 - t.depth / 2 - 0.02, y, 270, **values)
        else:
            self.add(code, c.x0 + WALL / 2 + t.depth / 2 + 0.02, y, 90, **values)

    def table(self, seats: int, x: float, y: float) -> None:
        """A meeting table for ``seats`` at (x, y), along the room (east-west): towards the
        screen on its east or west wall. Its chairs are drawn round it."""
        self.add(f"MEETING-TABLE-{seats}", x, y, 0)

    def ceiling(self, x: float, y: float) -> None:
        """A wireless access point on the ceiling."""
        self.add("ACCESS-POINT", x, y, 0, color="#f4f4f2")

    def benches(self, c: Cell, rows: tuple[float, ...], per_side: int = 3, code: str = "DESK-JUNIOR") -> None:
        """Desks in clusters of two rows facing each other, two clusters side by side along
        the room: ``rows``, the clusters' middles across it (metres from its window wall)."""
        t = self.types[code]
        width = c.x1 - c.x0
        for r in rows:
            y = c.y0 + r if self.south(c) else c.y1 - r
            for middle in (c.x0 + width * 0.27, c.x0 + width * 0.73):
                for i in range(per_side):
                    x = middle + (i - (per_side - 1) / 2) * t.width
                    self.add(code, x, y - t.depth / 2, 0)  # its user to the south
                    self.add(code, x, y + t.depth / 2, 180)  # and one across from them


def _furnish_main(f: _Furnisher, ordinal: int, cells: list[Cell]) -> None:
    """The main building's furniture and equipment: desks by grade, meeting tables of
    every size (16 seats in the board room), sofas and screens, copiers, access points on
    the ceilings, and a wayfinding kiosk at the entrance."""
    rooms = {(c.label[-1] if c.label[-1][:1].isdigit() else c.label[0]): c for c in cells if c.label}
    corridor = rooms["CORRIDOR"]
    for x in (16, 28, 40, 52):
        f.ceiling(x, (corridor.y0 + corridor.y1) / 2)
    f.ceiling(6.5, 10)  # the lift lobby
    if ordinal == 0:
        f.add("KIOSK", 13.6, 2.2, 0, model="Wayfinding kiosk, 32-inch")  # at the entrance, its screen to the doors
        f.add("DESK-SENIOR", 12.4, 6.4, 180)  # the reception desk: who sits at it faces the entrance
        f.add("SOFA", 19.6, 2.0, 180, seats=3)  # a waiting area: two sofas facing each other, a screen
        f.add("SOFA", 19.6, 5.3, 0, seats=3)
        f.on_side(rooms["001"], "TV", east=True, y=3.6, size_in=65)
        f.ceiling(16, 4.2)
        for number in ("002", "009", "010", "011"):
            f.desk(rooms[number], "DESK-SENIOR")
        f.on_side(rooms["003"], "TV", east=False, size_in=75)
        f.table(8, 30.4, 4.25)
        f.ceiling(30, 4.2)
        for x in (37.2, 42.8):  # the café's booths along its windows
            f.add("SOFA", x, 1.2, 180, seats=3)
            f.add("SOFA", x, 4.0, 0, seats=3)
        f.on_side(rooms["004"], "TV", east=True, y=5.8, size_in=55)
        f.ceiling(40, 4.2)
        f.add("SOFA", 50.0, 1.3, 180, seats=3)  # the lounge
        f.add("SOFA", 52.6, 3.6, 270, seats=2)
        f.add("SOFA", 50.0, 6.0, 0, seats=3)
        f.on_side(rooms["005"], "TV", east=False, y=3.6, size_in=65)
        f.by_door_wall(rooms["007"], "COPIER", 1.0, model="MFP 6055")
        f.benches(rooms["008"], (2.6, 6.3))
        f.ceiling(24, 16)
        f.on_side(rooms["013"], "TV", east=True, size_in=86)
        f.table(14, 50.6, 16.25)
        f.ceiling(51, 16)
    elif ordinal == 1:
        for number in ("101", "111"):
            f.benches(rooms[number], (2.6, 6.3))
        f.ceiling(16, 4.2)
        f.ceiling(24, 16)
        for number in ("102", "103", "104", "106", "107"):
            f.desk(rooms[number], "DESK-SECTION-HEAD")
        for number in ("112", "113"):
            f.desk(rooms[number], "DESK-SENIOR")
        f.desk(rooms["108"], "DESK-MANAGER")
        f.on_side(rooms["105"], "TV", east=False, size_in=75)
        f.table(6, 38.4, 4.25)
        f.ceiling(38, 4.2)
        f.add("COPIER", 28.6, 12.3, 180, model="MFP 4040")
        f.on_side(rooms["116"], "TV", east=True, size_in=65)  # the huddle: a table for four by the screen, a sofa
        f.table(4, 52.0, 16.25)
        f.add("SOFA", 50.0, 18.6, 180, seats=3)
    else:
        for number in ("201", "202"):
            c = rooms[number]
            f.desk(c, "DESK-DIRECTOR")
            f.by_door_wall(c, "SOFA", c.x1 - c.x0 - 1.5, seats=3)
            f.on_side(c, "TV", east=False, y=4.6, size_in=55)
        f.on_side(rooms["203"], "TV", east=False, size_in=86)  # the board room: a screen at each end
        f.table(16, 32.0, 4.25)
        f.on_side(rooms["203"], "TV", east=True, size_in=86)
        f.ceiling(32, 4.2)
        for number in ("204", "208"):
            c = rooms[number]
            f.desk(c, "DESK-CLEVEL")
            f.by_door_wall(c, "SOFA", 1.5, seats=3)
            f.on_side(c, "TV", east=True, size_in=55)
        f.desk(rooms["205"], "DESK-PRESIDENT", x=50.0)
        f.add("SOFA", 54.4, 5.4, 270, seats=3)
        f.add("SOFA", 51.0, 7.7, 0, seats=3)
        f.on_side(rooms["205"], "TV", east=False, y=5.6, size_in=75)
        f.ceiling(51, 4.2)
        f.on_side(rooms["209"], "TV", east=True, size_in=65)
        f.table(12, 29.6, 16.25)
        for number in ("210", "211", "213", "214"):
            f.desk(rooms[number], "DESK-MANAGER")
        f.add("COPIER", 12.0, 19.9, 180, model="MFP 6055")


def _furnish_pavilion(f: _Furnisher, ordinal: int, cells: list[Cell]) -> None:
    """The pavilion's: a desk in each office, a meeting room's table and screen, the open
    office's desks (and on the ground floor the quiet zone's sofas)."""
    for x in (6, 18):
        f.ceiling(x, 7)
    for c in cells:
        name = (c.label or [""])[0]
        if name == "OFFICE":
            f.desk(c, "DESK-SENIOR" if ordinal == 0 else "DESK-SECTION-HEAD")
        elif name == "MEETING ROOM":
            f.on_side(c, "TV", east=True, size_in=65)
            f.table(8 if ordinal == 0 else 6, 18.6, 3.0)
        elif name == "OPEN OFFICE":
            for y, rot in ((11.8, 180), (8.6, 0)):
                for x in (25.4, 26.6, 27.8, 29.0) if ordinal else (25.6, 27.0, 28.4):
                    f.add("DESK-JUNIOR", x, y, rot)
            if ordinal == 0:
                f.add("SOFA", 27.0, 1.2, 180, seats=3)  # the quiet zone
                f.add("SOFA", 29.3, 3.4, 270, seats=2)
            f.ceiling(27, 6.5)


def _finish(ws, floors: dict[str, list[str]]) -> None:
    """Each room finished as FINISHES_BY_NAME says, kept as a person's correction."""
    from . import finishes
    from .workspace import Override

    for ids in floors.values():
        for ordinal, floor_id in enumerate(ids):
            for r in ws.floor_objects(floor_id):
                fin = r.name and (FINISHES_BY_NAME.get(f"{r.name}@{ordinal}") or FINISHES_BY_NAME.get(r.name))
                if r.kind not in ("space", "zone") or not fin:
                    continue
                o = ws.overrides.setdefault(r.id, Override())
                o.floor_finish = finishes.check(fin[0], "floor")
                if r.kind == "space" and fin[1]:
                    o.wall_finish = finishes.check(fin[1], "wall")


def write_showcase_sheet(path: str | Path) -> None:
    """The main building's three plans side by side on one sheet, each titled, as an
    architect hands them over: a drawing to drop into Studio and see its plans found."""
    titles = ["GROUND FLOOR PLAN", "FIRST FLOOR PLAN", "SECOND FLOOR PLAN"]
    write_sheet_dxf(path, [(main_floor(i), (ORIGIN[0] + i * (MAIN_W + 14), ORIGIN[1]), t) for i, t in enumerate(titles)],
                    area_outlines=False)


def build_showcase(directory: str | Path) -> tuple[Path, list[Path]]:
    """What `storeypath demo` builds: the showcase campus (made up) as drawings, a
    workspace and its buildings' packages, the same every time. Returns (workspace path,
    package paths: one per building). Its drawings/ also hold main-building-sheet.dxf: the
    main building's three plans on one sheet, to drop into Studio and see them found."""
    from .convert import convert_floor
    from .export import export_package
    from .ids import make_id
    from .workspace import Project, SitePosition, SourceDrawing, Workspace

    directory = Path(directory)
    (directory / "drawings").mkdir(parents=True, exist_ok=True)
    ws = Workspace(project=Project(code=SHOWCASE_CODE, name=SHOWCASE_NAME))
    loc = ws.add_location("CAMPUS", SHOWCASE_NAME, address="1 Example Street")
    rng = random.Random(SHOWCASE_SEED)
    # (code, name, its floors' plans, how many, drawn with room outlines, its furniture, where on the site)
    buildings = [("MAIN", "Main Building", main_floor, 3, True, _furnish_main, (0.0, 0.0), (MAIN_W, MAIN_D)),
                 ("PAV", "Pavilion", pavilion_floor, 2, False, _furnish_pavilion, (6.0, -32.0), (PAV_W, PAV_D))]
    floors: dict[str, list[str]] = {}
    for code, name, plan, n_floors, outlines, _, (sx, sy), (w, d) in buildings:
        b_id = ws.add_building(loc, code, name)
        ws.building(b_id).site = SitePosition(x=sx, y=sy, pivot=(ORIGIN[0] + w / 2, ORIGIN[1] + d / 2))
        floors[b_id] = []
        for ordinal in range(n_floors):
            rel = Path("drawings") / f"{name.lower().replace(' ', '-')}-level-{ordinal}.dxf"
            write_floor_dxf(directory / rel, plan(ordinal), origin=ORIGIN, title=f"{name.upper()} LEVEL {ordinal}",
                            area_outlines=outlines, walls="polylines" if outlines else "lines")
            floors[b_id].append(ws.add_floor(b_id, ordinal, name="Ground floor" if ordinal == 0 else f"Floor {ordinal}",
                                             source=SourceDrawing(path=str(rel))))
    write_showcase_sheet(directory / "drawings" / "main-building-sheet.dxf")
    for _, _, _, floor_id in ws.iter_floors():
        convert_floor(ws, floor_id, directory)
    for (_, _, plan, _, _, furnish, _, _), ids in zip(buildings, floors.values()):
        for ordinal, floor_id in enumerate(ids):
            furnish(_Furnisher(ws, floor_id, rng), ordinal, plan(ordinal))
    _finish(ws, floors)
    ws_path = directory / "demo.spproj"
    packages = []  # one per building
    for code, *_ in buildings:
        packages.append(directory / f"demo-{code}.storeypath")
        export_package(ws, packages[-1], building=make_id(ws.id, loc.split("-")[-1], code))
    ws.save(ws_path)
    return ws_path, packages
