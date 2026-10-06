"""Taking private information out of a drawing before it enters a project.

A drawing carries more than the building: the title block on each sheet (the
client, owner, consultant, project, plot and permit numbers, who drew and checked
it, stamps, signatures, a logo), names and numbers elsewhere on the sheets, and
hidden data in the file (who saved it last, its properties, paths to images). None
of it is needed to map the building, and a project should not keep it. Studio keeps
only the copy this module makes, under a plain name (``drawing-1.dxf``).

What goes:

- title blocks: two or more title-block labels (CLIENT, OWNER, CONSULTANT, DRAWN
  BY, DRAWING NO, STAMP, SIGNATURE, …) beside the long ruled line that parts the
  title column off the drawing (down the right of a sheet) or the strip off it
  (along the bottom): the column or strip and everything in it; else the smallest
  closed box round the labels that holds little else (a title box in a corner);
- texts anywhere that name people or carry contact or registration numbers: names
  with a title (Mr, Eng, Sheikh, السيد …), phone numbers, emails, web addresses,
  permit, plot, licence and registration numbers, and the values of block
  attributes named for them;
- images and embedded objects (logos, signatures, scans) and the paper-space
  sheets (Studio reads the model space only);
- the file's hidden data: last saved by, project name, plot style, custom
  properties, hyperlinks, the paths of images and external references.

Plans, sections (for floor heights), room labels, dimensions and schedules stay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point, box

TITLE_LABELS = re.compile(
    r"(?i)^\s*(client|owner|consultant|contractor|developer|drawn\s*by|designed\s*by|checked\s*by|approved\s*by|"
    r"prepared\s*by|drawing\s*(no|number|title)|dwg\s*no|sheet\s*(no|title)|rev(ision)?\s*(no)?\b|stamp|"
    r"signature|registration|reg\.?\s*no|inquiry|enquiry|technical\s*manager|project\s*(no|name|title)?\s*:|"
    r"location\s*:|plot\s*(no|number)|"
    r"المالك|الاستشاري|المقاول|المشروع|رقم\s*المخطط|رقم\s*القطعة|التوقيع|الختم|رسم|تدقيق|اعتماد)")
PRIVATE_TEXT = re.compile(
    r"(?i)(\b(mr|mrs|ms|miss|dr|eng|engr|sheikh|shaikh|h\.\s?h)\.?\s+[a-z]{2,}"
    r"|السيد|السيدة|الشيخ|المهندس"
    r"|[\w.+-]+@[\w-]+\.[\w.]+|\bwww\.|https?://|\b[\w-]+\.(com|net|org|ae|sa|qa|kw|om|bh)\b"
    r"|(\+|00)\d[\d\s-]{6,}\d|\b(tel|phone|mob(ile)?|fax)\b\.?\s*[:.]?\s*\d|\bp\.?\s?o\.?\s*box\b"
    r"|\b(permit|licen[cs]e|plot|parcel|makani|reg(istration)?\.?)\s*(no|number|#)\b"
    r"|رخصة|رقم\s*القطعة|قطعة\s*رقم|هاتف|جوال|بريد)")
PRIVATE_ATTRIBS = re.compile(r"(?i)client|owner|consult|drawn|designed|checked|approv|signat|stamp|phone|^tel|email|"
                             r"plot|permit|licen|regist|reg_?no|project|engineer|architect|contractor|author")
LABELS_MIN = 2  # a title block has at least this many title-block labels
COLUMN_SHARE = 0.35  # a title column is at most this share of its height wide
NEAR_SHARE = 0.05  # its labels start this close to the line that parts it off (share of the line)
EXPLODE_DEPTH = 4  # blocks inside blocks, opened this deep to reach a title block
BOX_LABEL_SHARE = 0.15  # a box is a title block when this share of its texts are title-block labels
HEADER_VARS = ("$LASTSAVEDBY", "$PROJECTNAME", "$HYPERLINKBASE", "$STYLESHEET")


@dataclass
class PrivacyReport:
    title_blocks: int = 0  # panels removed
    entities: int = 0  # everything removed with them
    texts: int = 0  # private texts removed elsewhere
    attributes: int = 0  # block attribute values cleared
    images: int = 0  # images and embedded objects
    sheets: int = 0  # paper-space layouts emptied
    hidden: list[str] = field(default_factory=list)  # hidden data cleared

    def summary(self) -> str:
        parts = [f"{self.title_blocks} title block(s) ({self.entities} items)" if self.title_blocks else "",
                 f"{self.texts} private text(s)" if self.texts else "",
                 f"{self.attributes} attribute value(s)" if self.attributes else "",
                 f"{self.images} image(s)" if self.images else "",
                 f"{self.sheets} paper sheet(s)" if self.sheets else "",
                 "hidden file data" if self.hidden else ""]
        said = ", ".join(p for p in parts if p)
        return f"removed {said}" if said else "nothing private found"

    def view(self) -> dict:
        return {**self.__dict__, "summary": self.summary()}


def make_private(doc) -> PrivacyReport:
    """Take the private information out of ``doc`` (an ezdxf document), in place."""
    report = PrivacyReport()
    msp = doc.modelspace()
    removed_blocks: set[str] = set()
    for layout in doc.layouts:
        if layout.name != "Model" and len(layout):
            layout.delete_all_entities()
            report.sheets += 1
    panels = _title_panels(doc)
    if panels:
        report.title_blocks = len(panels)
        report.entities = sum(_remove_within(doc, msp, panels, removed_blocks))
    links = 0
    for layout in [msp, *(b for b in doc.blocks if not b.name.lower().startswith(("*model", "*paper")))]:
        for e in layout:  # hyperlinks: web addresses and the paths of people's files
            if e.has_xdata("PE_URL"):
                e.discard_xdata("PE_URL")
                links += 1
        for e in list(layout.query("IMAGE OLE2FRAME")):
            layout.delete_entity(e)
            report.images += 1
        for e in list(layout.query("TEXT MTEXT ATTDEF")):
            if PRIVATE_TEXT.search(_plain(e)):
                layout.delete_entity(e)
                report.texts += 1
        for insert in layout.query("INSERT"):
            for a in insert.attribs:
                value = a.dxf.get("text", "")
                if value and (PRIVATE_ATTRIBS.search(a.dxf.get("tag", "")) or PRIVATE_TEXT.search(value)):
                    a.dxf.text = ""
                    report.attributes += 1
    for d in list(doc.objects.query("IMAGEDEF")):  # the paths of the images
        doc.objects.delete_entity(d)
    if links:
        report.hidden.append(f"{links} hyperlink(s)")
    _clear_hidden(doc, report)
    _purge(doc, removed_blocks)
    return report


def _plain(e) -> str:
    try:
        return e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.get("text", "")
    except Exception:
        return ""


def _texts(doc):
    """(text, point) of every text in the model space, inside blocks too."""
    from .extract import _center, _text_lines, _walk

    for e, _ in _walk(doc.modelspace()):
        if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
            lines, c = _text_lines(e), _center(e)
            if lines and c:
                yield " ".join(lines), Point(c)


def _frames_and_lines(doc):
    """Closed rectangles (frames) and the upright and level ruled lines, model space
    and inside blocks, in drawing units. A line drawn in pieces is joined: ruled
    lines are (at, lo, hi) — x and the y span of an upright line, y and the x span
    of a level one."""
    from .extract import _flatten, _walk

    frames, upright, level = [], [], []
    for e, _ in _walk(doc.modelspace()):
        if e.dxftype() not in ("LINE", "LWPOLYLINE", "POLYLINE"):
            continue
        flat = _flatten(e, 1e-6)
        if not flat:
            continue
        pts, closed = flat
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if closed and len(set((round(x, 6), round(y, 6)) for x, y in pts)) == 4:
            if all(min(abs(x - min(xs)), abs(x - max(xs))) < 1e-6 * (1 + abs(x)) for x in xs):
                frames.append(box(min(xs), min(ys), max(xs), max(ys)))
        if closed:
            pts = [*pts, pts[0]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if abs(x0 - x1) <= 1e-9 * (1 + abs(x0)) and y0 != y1:
                upright.append((x0, min(y0, y1), max(y0, y1)))
            elif abs(y0 - y1) <= 1e-9 * (1 + abs(y0)) and x0 != x1:
                level.append((y0, min(x0, x1), max(x0, x1)))
    return frames, _joined(upright), _joined(level)


def _joined(lines):
    """Collinear pieces that touch or overlap, as one line each."""
    out = []
    for at, lo, hi in sorted(lines):
        last = out[-1] if out else None
        if last and abs(at - last[0]) <= 1e-6 * (1 + abs(at)) and lo <= last[2] + 1e-6 * (1 + abs(lo)):
            out[-1] = (last[0], last[1], max(last[2], hi))
        else:
            out.append((at, lo, hi))
    return out


def _title_panels(doc) -> list:
    """The title-block panels of the model space's sheets (boxes, drawing units):
    a column down the right of a sheet or a strip along its bottom, parted from the
    drawing by a long ruled line with the labels just beside it; else a closed box
    round the labels that holds little but the title block."""
    labels = [(t, p) for t, p in _texts(doc)]
    anchors = np.array([(p.x, p.y) for t, p in labels if TITLE_LABELS.search(t)], dtype=float).reshape(-1, 2)
    if len(anchors) < LABELS_MIN:
        return []
    frames, upright, level = _frames_and_lines(doc)
    claimed = np.zeros(len(anchors), dtype=bool)
    panels = []
    # a column right of an upright line; a strip below a level line is the same
    # turned a quarter (u, v) = (-y, x)
    for lines, turn, back in (
        (upright, lambda a: a, lambda u0, v0, u1, v1: box(u0, v0, u1, v1)),
        ([(-y, lo, hi) for y, lo, hi in level], lambda a: np.column_stack([-a[:, 1], a[:, 0]]),
         lambda u0, v0, u1, v1: box(v0, -u1, v1, -u0)),
    ):
        for (u0, v0, u1, v1), took in _columns(turn(anchors), lines, claimed):
            claimed |= took
            panels.append(back(u0, v0, u1, v1))
    # a closed box round labels not in a column: the smallest, if the labels are a
    # good share of what is written in it (not a sheet frame round a whole plan)
    points = np.array([(p.x, p.y) for _, p in labels], dtype=float).reshape(-1, 2)
    for frame in sorted(frames, key=lambda f: f.area):
        x0, y0, x1, y1 = frame.bounds
        inside = lambda a: (a[:, 0] >= x0) & (a[:, 0] <= x1) & (a[:, 1] >= y0) & (a[:, 1] <= y1)
        took = inside(anchors) & ~claimed
        if took.sum() < LABELS_MIN:
            continue
        if inside(anchors).sum() < BOX_LABEL_SHARE * inside(points).sum():
            continue
        claimed |= inside(anchors)
        panels.append(frame)
    return panels


def _columns(anchors, lines, claimed):
    """Title columns right of ruled lines (u, lo, hi): the line runs past the labels
    beside it, the nearest of them is within NEAR_SHARE of its length, and the column
    is at most COLUMN_SHARE of its length wide, closed on the right by the nearest
    long line past the labels. The line with the most labels wins; on a tie, the
    nearer (a double border)."""
    found = []
    lines = [(u, lo, hi) for u, lo, hi in lines if hi - lo > 0]
    for u, lo, hi in lines:
        length = hi - lo
        near = (anchors[:, 1] >= lo) & (anchors[:, 1] <= hi) & (anchors[:, 0] > u) & (anchors[:, 0] <= u + COLUMN_SHARE * length)
        if near.sum() < LABELS_MIN or anchors[near, 0].min() - u > NEAR_SHARE * length:
            continue
        found.append((int(near.sum()), -(anchors[near, 0].min() - u), u, lo, hi, near))
    found.sort(key=lambda f: (f[0], f[1]), reverse=True)
    for count, _, u, lo, hi, near in found:
        free = near & ~claimed
        if free.sum() < max(LABELS_MIN, 0.5 * count):
            continue
        length = hi - lo
        far = anchors[near, 0].max()
        ends = [w for w, a, b in lines if far < w <= u + COLUMN_SHARE * length and min(b, hi) - max(a, lo) >= 0.8 * length]
        end = min(ends) if ends else far + 0.25 * (far - u)
        claimed = claimed | near
        yield (u, lo, end, hi), near


def _remove_within(doc, msp, panels: list, removed_blocks: set[str]) -> list[int]:
    """Remove what stands in each panel: top-level entities whose middle is in it.
    A block placed across a panel (a sheet border drawn as one block, or a whole
    sheet) is exploded first, down to the pieces that lie in it. How many went from
    each panel."""
    from ezdxf import bbox as ebbox
    from shapely import STRtree

    from .extract import modelspace_entities

    tree = STRtree(panels)
    cache, kept = ebbox.Cache(), set()
    for _ in range(EXPLODE_DEPTH):
        exploded = False
        for insert in list(msp.query("INSERT")):
            if insert.dxf.handle in kept:
                continue
            kept.add(insert.dxf.handle)
            ext = ebbox.extents([insert], fast=True, cache=cache)
            if not ext.has_data:
                continue
            b = box(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
            if any(not panels[i].contains(b.centroid) for i in tree.query(b, predicate="intersects")):
                try:
                    insert.explode()
                    exploded = True
                except Exception:
                    pass
        if not exploded:
            break
    _reindex(doc)
    counts = []
    for panel in panels:
        removed = 0
        for e in list(modelspace_entities(doc, panel.bounds)):
            if not e.is_alive:
                continue
            try:
                if e.dxftype() == "INSERT":
                    removed_blocks.add(e.dxf.name)
                msp.delete_entity(e)
                removed += 1
            except Exception:
                pass
        counts.append(removed)
    _reindex(doc)
    return counts


def _reindex(doc) -> None:
    """The middle of every entity is measured once per drawing (extract.py): again,
    after entities are added or removed."""
    for attr in ("_storeypath_index",):
        if hasattr(doc, attr):
            delattr(doc, attr)


def _clear_hidden(doc, report: PrivacyReport) -> None:
    for var in HEADER_VARS:
        if doc.header.get(var):
            doc.header[var] = ""
            report.hidden.append(var)
    try:
        if len(doc.header.custom_vars):
            doc.header.custom_vars.clear()
            report.hidden.append("custom properties")
    except AttributeError:
        pass
    root = doc.rootdict
    for key in ("DWGPROPS",):
        if key in root:
            try:
                root.discard(key)
            except AttributeError:
                del root[key]
            report.hidden.append(key)
    for block in doc.blocks:  # external references: their paths name people's folders
        if block.block_record.is_xref if hasattr(block.block_record, "is_xref") else False:
            block.block.dxf.xref_path = ""
            report.hidden.append(f"xref {block.name}")


def _purge(doc, names: set[str]) -> None:
    """The definitions of the blocks removed with a title block (its frame, logo,
    stamp) once nothing places them any more. Other blocks stay: dimension styles
    and the like refer to blocks by name."""
    used = set()
    for layout in [*doc.layouts, *doc.blocks]:
        for insert in layout.query("INSERT"):
            used.add(insert.dxf.name)
    for name in names - used:
        if not name.startswith("*"):
            try:
                doc.blocks.delete_block(name, safe=False)
            except Exception:
                pass
