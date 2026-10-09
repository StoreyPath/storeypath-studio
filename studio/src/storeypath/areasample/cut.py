"""The drawing's part of an area sample: the entities of a floor's plan that touch the
area, in a DXF file of their own.

- Everything of the floor's plan (its region of the sheet, as the floor reads it)
  whose extents touch the area and its margin is taken. An entity lying within them
  is kept whole, as drawn: its layer, linetype, colour and the block it places.
- A line, polyline, arc, circle, ellipse or spline running out of them is **cut at the
  margin's edge** (as short straight pieces, on its layer, linetype and colour). A closed
  one (a room's outline, a wall drawn as one closed outline) is cut as an area: what of
  it is inside, closed along the edge, so a room crossing the edge is still read as a
  room, its part inside. A fill (HATCH) is cut to the edge the same way.
- A text (or a point, a solid) running out is kept whole when its middle is inside,
  else left out; a dimension or leader running out is taken apart into its lines (cut)
  and its text. A block placed across the edge (a sheet pasted as one block, a wall
  block running out) is taken apart, and its pieces are treated the same way; a block
  placed wholly inside stays a block.
- Never in the part: paper space and layouts, external references, images, embedded
  (OLE) objects and underlays, proxies, and infinite lines.
- It is written into a **new** DXF document (ezdxf's Importer): the layers, linetypes,
  text styles, dimension styles and blocks it uses come with it, and nothing else of
  the file does: no header but its units, no file properties, no objects, no layouts.
  Extra data and extension dictionaries on entities and table entries are dropped.
- It is moved so that the area's lower-left corner is (0, 0), in the drawing's own
  units (not converted: how Studio worked out the units is part of what a sample shows).
"""

from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass, field

import ezdxf
from ezdxf.addons import Importer
from ezdxf.addons.importer import IMPORT_ENTITIES
from shapely.geometry import LineString, Polygon, box

from ..extract import MAX_BLOCK_DEPTH, _entity_index

LEFT_OUT = {"IMAGE", "OLE2FRAME", "OLEFRAME", "PDFUNDERLAY", "DWFUNDERLAY", "DGNUNDERLAY", "ACAD_PROXY_ENTITY",
            "VIEWPORT", "WIPEOUT", "XLINE", "RAY", "3DSOLID", "REGION", "BODY", "SURFACE", "LIGHT", "SUN"}
CUT_AS_LINES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
KEPT_BY_MIDDLE = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "POINT", "SHAPE", "SOLID", "TRACE", "3DFACE"}  # whole, or not
COMMON = ("layer", "color", "linetype", "lineweight", "ltscale", "true_color", "transparency", "invisible")
TEXT_KEYS = ("text", "insert", "height", "rotation", "style", "width", "oblique", "halign", "valign", "align_point",
             "text_generation_flag", "layer", "color", "true_color", "linetype", "lineweight")
CURVE_STEP_M = 0.02  # a curve cut at the edge is kept as straight pieces this close to it
HEADER_KEPT = ("$INSUNITS", "$MEASUREMENT", "$LTSCALE", "$PSLTSCALE", "$CELTSCALE", "$DIMSCALE", "$LUNITS", "$LUPREC")


@dataclass
class Part:
    """The drawing's part: a DXF document (moved, (0, 0) the area's lower-left corner)
    and what was done to make it."""

    doc: object
    kept: int = 0  # entities kept whole
    cut: int = 0  # entities cut at the edge
    taken_apart: int = 0  # blocks placed across the edge, taken apart
    left_out: Counter = field(default_factory=Counter)  # by DXF type: never in a sample, or outside

    def text(self) -> str:
        out = io.StringIO()
        self.doc.write(out)
        return out.getvalue()


def cut(doc, keep_box: tuple[float, float, float, float], origin: tuple[float, float],
        region: tuple[float, float, float, float] | None, scale: float) -> Part:
    """The part of ``doc`` (an ezdxf document, never changed) in ``keep_box`` (drawing
    units: the area and its margin), of the plan in ``region`` (the floor's part of the
    sheet; None: all of it), moved by ``-origin`` (drawing units)."""
    kx0, ky0, kx1, ky1 = keep_box
    keep = box(*keep_box)
    tol = CURVE_STEP_M / scale
    target = ezdxf.new(doc.dxfversion if doc.dxfversion >= "AC1015" else "AC1032", setup=False)
    part = Part(target)
    chosen: list = []

    def inside(lo, hi) -> bool:
        return lo[0] >= kx0 - tol and hi[0] <= kx1 + tol and lo[1] >= ky0 - tol and hi[1] <= ky1 + tol

    def extents(e):
        try:
            ext = ezdxf.bbox.extents([e], fast=True)
        except Exception:
            return None
        if not ext.has_data:
            return None
        return (ext.extmin.x, ext.extmin.y), (ext.extmax.x, ext.extmax.y)

    def take(e, lo, hi, depth: int, layer: str | None = None) -> None:
        kind = e.dxftype()
        if layer is not None and e.dxf.get("layer", "0") == "0":
            e.dxf.layer = layer  # a block's piece on layer 0 takes the block's layer, as CAD shows it
        if kind in LEFT_OUT:
            part.left_out[kind] += 1
            return
        if lo[0] > kx1 or hi[0] < kx0 or lo[1] > ky1 or hi[1] < ky0:
            return  # does not touch the area
        whole = inside(lo, hi)
        if kind == "INSERT":
            block = doc.blocks.get(e.dxf.get("name", ""))
            if block is None or getattr(block.block_record, "is_xref", False) or block.block_record.is_any_layout:
                part.left_out["external reference" if block is not None else "INSERT"] += 1
                return
            if whole and depth == 0:
                chosen.append(e)
                part.kept += 1
                return
            if depth >= MAX_BLOCK_DEPTH:
                part.left_out["INSERT"] += 1
                return
            part.taken_apart += 1
            insert_layer = e.dxf.get("layer", "0")
            for a in e.attribs:  # its attributes, as texts of their own
                text = ezdxf.entities.Text.new(dxfattribs={k: a.dxf.get(k) for k in TEXT_KEYS if a.dxf.hasattr(k)})
                if text.dxf.get("layer", "0") == "0":
                    text.dxf.layer = insert_layer
                ext = extents(text)
                if ext:
                    take(text, *ext, depth + 1)
            try:
                pieces = list(e.virtual_entities())
            except Exception:
                part.left_out["INSERT"] += 1
                return
            for piece in pieces:
                ext = extents(piece)
                if ext:
                    take(piece, *ext, depth + 1, insert_layer)
            return
        if kind not in IMPORT_ENTITIES:  # an MLINE, a table, a multileader: as its lines and texts
            try:
                pieces = list(e.virtual_entities())
            except Exception:
                part.left_out[kind] += 1
                return
            part.taken_apart += 1
            for piece in pieces:
                ext = extents(piece)
                if ext:
                    take(piece, *ext, depth + 1)
            return
        if whole:
            chosen.append(e)
            part.kept += 1
            return
        if kind in CUT_AS_LINES:
            pieces = _cut_lines(e, keep, tol)
            if pieces is None:
                part.left_out[kind] += 1
            elif pieces:
                chosen.extend(pieces)
                part.cut += 1
            return
        if kind == "HATCH":
            pieces = _cut_fill(e, keep, tol)
            if pieces is None:
                part.left_out[kind] += 1
            elif pieces:
                chosen.extend(pieces)
                part.cut += 1
            return
        if kind not in KEPT_BY_MIDDLE and depth < MAX_BLOCK_DEPTH:
            try:  # a dimension or leader running out: its lines (cut) and text, not the whole of it
                pieces = list(e.virtual_entities())
            except Exception:
                pieces = None
            if pieces:
                part.taken_apart += 1
                for piece in pieces:
                    ext = extents(piece)
                    if ext:
                        take(piece, *ext, depth + 1, e.dxf.get("layer", "0"))
                return
        middle = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
        if kx0 <= middle[0] <= kx1 and ky0 <= middle[1] <= ky1:
            chosen.append(e)
            part.kept += 1

    entities, centres, boxes = _entity_index(doc)
    for i, e in enumerate(entities):
        x0, y0, x1, y1 = boxes[i]
        if x0 > kx1 or x1 < kx0 or y0 > ky1 or y1 < ky0:
            continue
        if region is not None:  # the floor's plan: what has its middle in the floor's part of the sheet
            cx, cy = centres[i]
            rx0, ry0, rx1, ry1 = region
            if not (rx0 <= cx <= rx1 and ry0 <= cy <= ry1):
                continue
        take(e, (x0, y0), (x1, y1), 0)

    importer = Importer(doc, target)
    msp = target.modelspace()
    for e in chosen:
        importer.import_entity(e, msp)
    for e in list(msp):
        try:
            e.translate(-origin[0], -origin[1], 0)
        except Exception:  # an entity that cannot be moved is not kept where it was
            part.left_out[e.dxftype()] += 1
            msp.delete_entity(e)
    for dim in msp.query("DIMENSION"):  # each dimension's drawing, as a block of its own (the Importer leaves it out)
        content = getattr(dim, "virtual_block_content", None)
        if dim.dxf.get("geometry") or not content:
            continue
        block = target.blocks.new_anonymous_block(type_char="D")
        for piece in content:
            importer.import_entity(piece, block)
        dim.dxf.geometry = block.name
        dim.virtual_block_content = None
    importer.finalize()
    for name in HEADER_KEPT:
        value = doc.header.get(name)
        if value is not None:
            target.header[name] = value
    target.header["$LASTSAVEDBY"] = "storeypath"
    _strip_extra_data(target)
    return part


def _flat(e, tol: float) -> list[tuple[float, float]] | None:
    try:
        path = ezdxf.path.make_path(e)
        return [(v.x, v.y) for v in path.flattening(tol)]
    except Exception:
        return None


def _attribs(e, keys=COMMON) -> dict:
    return {k: e.dxf.get(k) for k in keys if e.dxf.hasattr(k)}


def _closed(e, pts) -> bool:
    kind = e.dxftype()
    if kind == "CIRCLE":
        return True
    if kind in ("LWPOLYLINE", "POLYLINE") and getattr(e, "is_closed", False):
        return True
    return len(pts) > 3 and abs(pts[0][0] - pts[-1][0]) + abs(pts[0][1] - pts[-1][1]) < 1e-9


def _cut_lines(e, keep, tol: float) -> list | None:
    """A line running out of ``keep``, cut at its edge: LWPOLYLINEs on its layer,
    linetype and colour (None when it cannot be read as a line). A closed shape (a
    room's outline, a column) is cut as an area: what of it is inside, closed along the
    edge, so a room crossing the edge is still a room, its part inside."""
    pts = _flat(e, tol)
    if not pts or len(pts) < 2:
        return None
    rings: list[list] = []
    try:
        if _closed(e, pts) and len(pts) >= 3:
            area = Polygon(pts).buffer(0).intersection(keep)
            for g in getattr(area, "geoms", [area]):
                if isinstance(g, Polygon) and not g.is_empty:
                    rings.append([list(g.exterior.coords), True])
                    rings += [[list(hole.coords), True] for hole in g.interiors]
        else:
            inside = LineString(pts).intersection(keep)
            rings = [[list(g.coords), False] for g in getattr(inside, "geoms", [inside])
                     if isinstance(g, LineString) and g.length > 0]
    except Exception:
        return None
    out = []
    width = e.dxf.get("const_width") if e.dxftype() == "LWPOLYLINE" else None
    for coords, closed in rings:
        attribs = _attribs(e)
        if width:
            attribs["const_width"] = width
        pl = ezdxf.entities.LWPolyline.new(dxfattribs=attribs)
        pl.set_points([(x, y) for x, y in (coords[:-1] if closed else coords)], format="xy")
        pl.closed = closed
        out.append(pl)
    return out


def _cut_fill(e, keep, tol: float) -> list | None:
    """A fill running out of ``keep``, cut to it: the same HATCH (pattern, colour,
    layer) with the boundary of what of it is inside."""
    try:
        rings = [list(p.flattening(tol)) for p in ezdxf.path.from_hatch(e)]
    except Exception:
        return None
    area = None
    for ring in rings:
        if len(ring) < 3:
            continue
        poly = Polygon([(v.x, v.y) for v in ring]).buffer(0)
        area = poly if area is None else area.symmetric_difference(poly)  # holes by even-odd, as hatches fill
    if area is None:
        return None
    inside = area.intersection(keep)
    polys = [g for g in getattr(inside, "geoms", [inside]) if isinstance(g, Polygon) and not g.is_empty]
    out = []
    for poly in polys:
        copy = e.copy()
        copy.paths.clear()
        copy.paths.add_polyline_path(list(poly.exterior.coords)[:-1], is_closed=True, flags=1)
        for hole in poly.interiors:
            copy.paths.add_polyline_path(list(hole.coords)[:-1], is_closed=True, flags=0)
        out.append(copy)
    return out


def _strip_extra_data(doc) -> None:
    """Extra data (XDATA: hyperlinks, an application's strings), extension dictionaries
    and reactors off every entity and table entry: none of it is needed to read a plan."""
    def strip(e):
        if getattr(e, "xdata", None):
            for app in list(e.xdata.data):
                e.discard_xdata(app)
        try:
            if e.has_extension_dict:
                e.discard_extension_dict()
        except Exception:
            pass
        try:
            if e.has_reactors():
                e.set_reactors([])
        except Exception:
            pass

    for layout in [doc.modelspace(), *doc.blocks]:
        for e in layout:
            strip(e)
            for a in getattr(e, "attribs", ()) or ():
                strip(a)
    for table in (doc.layers, doc.linetypes, doc.styles, doc.dimstyles, doc.block_records):
        for entry in table:
            strip(entry)


def used_layers(doc) -> Counter:
    """How many entities are on each layer (model space and blocks)."""
    n = Counter()
    for layout in [doc.modelspace(), *(b for b in doc.blocks if not b.name.lower().startswith(("*model", "*paper")))]:
        for e in layout:
            n[e.dxf.get("layer", "0")] += 1
    return n
