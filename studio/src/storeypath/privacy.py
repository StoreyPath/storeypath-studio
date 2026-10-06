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
  properties, hyperlinks, the paths of images and external references;
- with a language model, whatever else it reads as private in the texts left: a
  name without a title, a company, an address. The model points at the private
  part of each text and only that goes ("OFFICE - KHALID" keeps "OFFICE"); a text
  the room rules read as a room's name is never removed whole.

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
# A person's name with a title before it: "DR. KHALID", "ENG AHMED ALI", "د. أحمد". The
# title before a room or a role ("DR. OFFICE", "ENG. ROOM") is a room's name, kept.
ROLE_WORDS = (r"(office|offices|room|rooms|lounge|station|clinic|area|duty|on[- ]?call|rest|changing|toilet|wc|"
              r"washroom|store|suite|cabin|desk|residence|residents|quarters|sleeping|lockers?|pantry|kitchen|"
              r"library|studio|workshop|lab|laboratory|stat|entrance|corridor|waiting)\b")
TITLED_NAME = re.compile(
    r"(?i)(\b(mr|mrs|ms|miss|dr|eng|engr|sheikh|shaikh|h\.\s?h)\.?\s+(?!" + ROLE_WORDS + r")[^\W\d_]{2,}"
    r"(\s+(al[- ])?[^\W\d_]{2,}){0,3}"
    r"|(السيد|السيدة|الشيخ|المهندس|الدكتور|د\.)\s*[^\W\d_]{2,}(\s+[^\W\d_]{2,}){0,3})")
PRIVATE_TEXT = re.compile(
    r"(?i)([\w.+-]+@[\w-]+\.[\w.]+|\bwww\.|https?://|\b[\w-]+\.(com|net|org|ae|sa|qa|kw|om|bh)\b"
    r"|(\+|00)\d[\d\s-]{6,}\d|\b(tel|phone|mob(ile)?|fax)\b\.?\s*[:.]?\s*\d|\bp\.?\s?o\.?\s*box\b"
    r"|\bext(n|ension)?\b\.?\s*[:.]?\s*\d{2,5}\b"
    r"|\b(permit|licen[cs]e|plot|parcel|makani|reg(istration)?\.?)\s*(no|number|#)\b"
    r"|رخصة|رقم\s*القطعة|قطعة\s*رقم|هاتف|جوال|بريد)")
PRIVATE_ATTRIBS = re.compile(r"(?i)client|owner|consult|drawn|designed|checked|approv|signat|stamp|phone|^tel|email|"
                             r"plot|permit|licen|regist|reg_?no|project|engineer|architect|contractor|author")
LABELS_MIN = 2  # a title block has at least this many title-block labels
COLUMN_SHARE = 0.35  # a title column is at most this share of its height wide
NEAR_SHARE = 0.05  # its labels start this close to the line that parts it off (share of the line)
EXPLODE_DEPTH = 4  # blocks inside blocks, opened this deep to reach a title block
BOX_LABEL_SHARE = 0.15  # a box is a title block when this share of its texts are title-block labels
# a CAD program's own identifiers: a handle ("393E3"), a GUID
IDENTIFIER = re.compile(r"[0-9A-Fa-f]{1,16}|\{?[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}?")
KEPT_FOR_THEMSELVES = ("Extra data on entities (application: string)", "Stored records")  # where handles are
PLOT_NAMES = (("plot_configuration_file", "None"), ("current_style_sheet", ""), ("page_setup_name", ""))
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
    by_model: int = 0  # texts the language model found private (part or whole)
    model: str | None = None  # the model that read the texts, if one did
    kinds: dict[str, int] = field(default_factory=dict)  # what it found: person, company, …

    def summary(self) -> str:
        parts = [f"{self.title_blocks} title block(s) ({self.entities} items)" if self.title_blocks else "",
                 f"{self.texts} private text(s)" if self.texts else "",
                 f"{self.attributes} attribute value(s)" if self.attributes else "",
                 f"{self.images} image(s)" if self.images else "",
                 f"{self.sheets} paper sheet(s)" if self.sheets else "",
                 "hidden file data" if self.hidden else "",
                 (f"{self.by_model} more private text(s) the language model found ("
                  + ", ".join(f"{n} {k}" for k, n in sorted(self.kinds.items())) + ")") if self.by_model else ""]
        said = ", ".join(p for p in parts if p)
        read = f"; texts read by {self.model}" if self.model else "; no language model: rules only"
        return (f"removed {said}" if said else "nothing private found") + read

    def view(self) -> dict:
        return {**self.__dict__, "summary": self.summary()}


class Choices:
    """What was found to take out, and whether each goes: all of it (the default),
    or all but what a person chose to keep. Found things are grouped so a person
    decides once for each: a title block, a text (wherever it is written), the
    images, the paper sheets, an item of hidden file data. The language model's
    reading is kept, so applying a choice does not ask it again."""

    def __init__(self, keep: set[str] | None = None, model_found: dict | None = None):
        self.keep = set(keep or ())
        self.found: dict[str, dict] = {}  # id → {id, kind, label, count, detail}
        self.model_found = model_found  # text → [(part, kind)], from finding

    def take(self, key: str, kind: str, label: str, detail: list[str] | None = None) -> bool:
        """Record a thing found; True when it goes."""
        f = self.found.setdefault(key, {"id": key, "kind": kind, "label": label, "count": 0, "detail": detail or [],
                                        "order": len(self.found)})
        f["count"] += 1
        return key not in self.keep

    def listed(self) -> list[dict]:
        order = {k: i for i, k in enumerate(("title block", "name", "contact", "attribute", "person", "company",
                                              "address", "id number", "other", "images", "sheets", "file data"))}
        return sorted(self.found.values(), key=lambda f: (order.get(f["kind"], 99), f["order"]))


def make_private(doc, reader=None, say=None, choices: Choices | None = None) -> PrivacyReport:
    """Take the private information out of ``doc`` (an ezdxf document), in place;
    with ``reader`` (a language model, as llm.LocalModel), the texts left read by it
    too. ``choices`` records what was found and keeps what a person chose to keep."""
    choices = choices if choices is not None else Choices()
    report = PrivacyReport()
    msp = doc.modelspace()
    removed_blocks: set[str] = set()
    sheets = [layout for layout in doc.layouts if layout.name != "Model" and len(layout)]
    for layout in sheets:
        if choices.take("sheets", "sheets", "Paper-space sheets (Studio reads the model space only)"):
            layout.delete_all_entities()
            report.sheets += 1
    panels = _title_panels(doc)
    if panels:
        # One choice for them all (a drawing repeats its title block on every sheet),
        # shown by what is filled in beside their labels: the client, the consultant,
        # who drew it, the project
        texts = list(_texts(doc))
        values: list[str] = []
        for panel in panels:
            inside = [(t, p) for t, p in texts if panel.contains(p)]
            labels = [p for t, p in inside if TITLE_LABELS.search(t)]
            filled = [(t, p) for t, p in inside
                      if not TITLE_LABELS.search(t) and not t.rstrip().endswith(":") and 2 < len(t) <= 50
                      and re.search(r"[^\W\d_]{2,}", t)]
            for at in labels:
                if filled:
                    values.append(min(filled, key=lambda tp: tp[1].distance(at))[0])
        values = list(dict.fromkeys(values))
        chosen = []
        for panel in panels:
            if choices.take("title blocks", "title block", f"Title blocks ({len(panels)} sheet{'s' if len(panels) > 1 else ''})",
                            values[:16]):
                chosen.append(panel)
        if chosen:
            report.title_blocks = len(chosen)
            report.entities = sum(_remove_within(doc, msp, chosen, removed_blocks))
    links, images = 0, 0
    for layout in [msp, *(b for b in doc.blocks if not b.name.lower().startswith(("*model", "*paper")))]:
        for e in layout:  # hyperlinks: web addresses and the paths of people's files
            if e.has_xdata("PE_URL") and choices.take("links", "file data", "Hyperlinks on things drawn"):
                e.discard_xdata("PE_URL")
                links += 1
        for e in list(layout.query("IMAGE OLE2FRAME")):
            if choices.take("images", "images", "Images and embedded objects (logos, signatures, scans)"):
                layout.delete_entity(e)
                report.images += 1
                images += 1
        for e in list(layout.query("TEXT MTEXT ATTDEF")):
            text = _plain(e)
            said = " ".join(text.split())
            if PRIVATE_TEXT.search(text):
                if choices.take(f"text {said}", "contact", said):
                    layout.delete_entity(e)
                    report.texts += 1
            elif TITLED_NAME.search(text):
                names = [m.group(0).strip() for m in TITLED_NAME.finditer(text)]
                if choices.take(f"name {said}", "name", said, names):
                    rest = " ".join(TITLED_NAME.sub(" ", text).split()).strip(" -:,.")
                    if rest:  # "CONSULTANT DR. KHALID": the room's name stays
                        if e.dxftype() == "MTEXT":
                            e.text = rest
                        else:
                            e.dxf.text = rest
                    else:
                        layout.delete_entity(e)
                    report.texts += 1
        for insert in layout.query("INSERT"):
            for a in insert.attribs:
                value, tag = a.dxf.get("text", ""), a.dxf.get("tag", "")
                if value and (PRIVATE_ATTRIBS.search(tag) or PRIVATE_TEXT.search(value) or TITLED_NAME.search(value)):
                    if choices.take(f"attribute {tag} {value}", "attribute", f"{tag}: {value}"):
                        a.dxf.text = ""
                        report.attributes += 1
    if "images" not in choices.keep:  # the paths of the images, unless they are kept
        for d in list(doc.objects.query("IMAGEDEF")):
            doc.objects.delete_entity(d)
    if links:
        report.hidden.append(f"{links} hyperlink(s)")
    _clear_hidden(doc, report, choices)
    _purge(doc, removed_blocks)
    if reader is not None and (choices.model_found is not None or reader.available()):
        _read_by_model(doc, reader, report, say, choices)
    return report


def _read_by_model(doc, reader, report: PrivacyReport, say=None, choices: Choices | None = None) -> None:
    """The texts left, read by a language model for what the rules cannot know: a
    name without a title, a company, an address. Only the part it points at goes; a
    text the room rules read as a room's name is never removed whole."""
    from .extract import OPENING_TAG
    from .llm import ModelUnavailable, find_private
    from .profile import AUTO, load_profile
    from .types import SpaceType

    choices = choices if choices is not None else Choices()
    rooms = load_profile(AUTO)
    msp = doc.modelspace()
    written = []  # (layout or None for an attribute, entity, text)
    for layout in [msp, *(b for b in doc.blocks if not b.name.lower().startswith(("*model", "*paper")))]:
        for e in layout.query("TEXT MTEXT ATTDEF"):
            written.append((layout, e, _plain(e)))
        for insert in layout.query("INSERT"):
            written += [(None, a, a.dxf.get("text", "")) for a in insert.attribs]

    def worth_asking(t: str) -> bool:  # words, not numbers, levels or tags
        t = t.strip()
        return len(t) >= 3 and bool(re.search(r"[^\W\d_]{2,}", t)) and not OPENING_TAG.fullmatch(t.upper()) \
            and not re.match(r"^\s*(%%[pP]|±|\+|-)?\s*\d", t)

    found = choices.model_found
    if found is None:
        texts = sorted({" ".join(t.split()) for _, _, t in written if worth_asking(t)})
        if say:
            say(f"reading {len(texts)} texts with {reader.name} for private information")
        try:
            found = find_private(reader, texts, say)
        except ModelUnavailable as e:
            if say:
                say(f"the language model was not used: {e}")
            return
        choices.model_found = found
    report.model = getattr(reader, "name", None)
    for layout, e, text in written:
        said = " ".join(text.split())
        parts = found.get(said)
        if not parts:
            continue
        rest = text
        for part, _ in parts:
            rest = re.sub(re.escape(part), " ", rest, flags=re.IGNORECASE)
        rest = " ".join(rest.split()).strip(" -:,.()")
        if not re.search(r"[^\W_]", rest):
            if rooms.classify(text, [], "")[0] != SpaceType.UNSPECIFIED or _names_a_room(text):
                continue  # a room's name, whatever the model thought of it
            rest = ""
        kind = parts[0][1]
        if not choices.take(f"model {said}", kind, said, [p for p, _ in parts]):
            continue
        for _, k in parts:
            report.kinds[k] = report.kinds.get(k, 0) + 1
        if e.dxftype() == "ATTRIB":
            e.dxf.text = rest
        elif not rest:
            layout.delete_entity(e)
        elif e.dxftype() == "MTEXT":
            e.text = rest
        else:
            e.dxf.text = rest
        report.by_model += 1


_ROOM_WORDS: set[str] | None = None
ROOM_SHORT = {"rm", "wc", "elec", "mech", "lav", "stor", "str", "kit", "bed", "liv", "din", "pwr", "ups", "ahu", "fcu",
              "mep", "comms", "srv", "lab", "maid", "guest", "pantry", "toilet", "bath", "dress", "foyer", "hall"}
_NOT_ROOM_WORDS = {"room", "rooms", "any", "other", "the", "and", "with", "for", "its", "that", "none", "types", "above",
                   "fits", "only", "number", "also", "written", "over", "below", "floor", "space", "area", "private",
                   "bath", "shower", "washing", "ironing", "clothes", "technical", "electrical", "distribution", "board"}


def _names_a_room(text: str) -> bool:
    """Whether a text has a word that names a kind of room (majlis, bedroom, pantry,
    مجلس …), from the words Studio gives the language model for room types."""
    global _ROOM_WORDS
    if _ROOM_WORDS is None:
        from .llm import TYPE_GUIDE

        _ROOM_WORDS = {w for guide in TYPE_GUIDE.values() for w in re.findall(r"[^\W\d_]{3,}", guide.lower())}
        _ROOM_WORDS -= _NOT_ROOM_WORDS
    return any(w in _ROOM_WORDS or w in ROOM_SHORT for w in re.findall(r"[^\W\d_]{2,}", text.lower()))


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


def _clear_hidden(doc, report: PrivacyReport, choices: "Choices | None" = None) -> None:
    choices = choices if choices is not None else Choices()
    names = {"$LASTSAVEDBY": "Last saved by", "$PROJECTNAME": "Project name", "$HYPERLINKBASE": "Hyperlink base",
             "$STYLESHEET": "Plot style"}
    for var in HEADER_VARS:
        value = doc.header.get(var)
        if value and choices.take(f"hidden {var}", "file data", f"{names.get(var, var)}: {value}"):
            doc.header[var] = ""
            report.hidden.append(var)
    try:
        props = list(doc.header.custom_vars)
        if props and choices.take("hidden properties", "file data", "File properties",
                                  [f"{k}: {v}" for k, v in props][:12]):
            doc.header.custom_vars.clear()
            report.hidden.append("custom properties")
    except (AttributeError, TypeError):
        pass
    root = doc.rootdict
    for key in ("DWGPROPS",):
        if key in root and choices.take("hidden DWGPROPS", "file data", "Drawing properties (author, title, comments)"):
            try:
                root.discard(key)
            except AttributeError:
                del root[key]
            report.hidden.append(key)
    # Sheet setups name the printer (a computer or network name: "\\\\SERVER\\HP …") and the
    # plot style (often after its owner: "ricky1.ctb")
    setups = [layout.dxf_layout for layout in doc.layouts] + list(doc.objects.query("PLOTSETTINGS"))
    named = sorted({v for s in setups for a, blank in PLOT_NAMES
                    if (v := s.dxf.get(a)) not in (None, "", blank)})
    if named and choices.take("hidden plot", "file data", "Printer and plot style names", named):
        for setup in setups:
            for attr, blank in PLOT_NAMES:
                if setup.dxf.get(attr) not in (None, "", blank):
                    setup.dxf.set(attr, blank)
        report.hidden.append("printer and plot style names")
    for block in doc.blocks:  # external references: their paths name people's folders
        if getattr(block.block_record, "is_xref", False) and block.block.dxf.get("xref_path"):
            if choices.take(f"hidden xref {block.name}", "file data", f"External reference path: {block.block.dxf.xref_path}"):
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


def words(doc, title: str = "", cleaning: str | None = None) -> str:
    """Every word and string left in a drawing, for a person to look through for
    anything private left behind: what is written on the drawing (inside blocks
    too), block attributes, the names of layers, blocks and styles, dimension text,
    extra data on entities, sheet setups and file settings. Each different string
    once, with how many times it is there."""
    from collections import Counter

    from .extract import _text_lines, _walk

    found: dict[str, Counter] = {}
    identifiers = Counter()  # handles and GUIDs: numbers CAD programs keep, not words

    def add(section: str, value) -> None:
        v = " ".join(str(value).split())
        if not v:
            return
        bare = v.split(": ", 1)[-1]
        if section in KEPT_FOR_THEMSELVES and IDENTIFIER.fullmatch(bare):
            identifiers[section] += 1
            return
        found.setdefault(section, Counter())[v] += 1

    for e, _ in _walk(doc.modelspace()):
        kind = e.dxftype()
        if kind in ("TEXT", "MTEXT", "ATTRIB"):
            add("Written on the drawing", " ".join(_text_lines(e)))
        elif kind == "DIMENSION" and e.dxf.get("text", "") not in ("", "<>", " "):
            add("Dimension text", e.dxf.get("text"))
        if kind == "INSERT":
            for a in e.attribs:
                add("Block attributes (tag: value)", f"{a.dxf.get('tag', '')}: {a.dxf.get('text', '')}")
    for block in doc.blocks:
        if block.name.lower().startswith(("*model", "*paper")):
            continue
        if not block.name.startswith("*"):
            add("Block names", block.name)
        for a in block.query("ATTDEF"):
            add("Block attribute definitions (tag | prompt | default)",
                f"{a.dxf.get('tag', '')} | {a.dxf.get('prompt', '')} | {a.dxf.get('text', '')}")
    for layer in doc.layers:
        add("Layer names", layer.dxf.name)
        try:
            add("Layer descriptions", layer.description)
        except Exception:  # none, or kept in a way this reader does not know
            pass
    for style in doc.styles:
        add("Text styles and fonts", " | ".join(v for v in (style.dxf.get("name", ""), style.dxf.get("font", ""),
                                                            style.dxf.get("bigfont", "")) if v))
    for lt in doc.linetypes:
        add("Line types", f"{lt.dxf.name} {lt.dxf.get('description', '')}")
    for ds in doc.dimstyles:
        add("Dimension styles", ds.dxf.name)
    for app in doc.appids:
        add("Applications that left data", app.dxf.name)
    for layout in [doc.modelspace(), *doc.blocks]:
        for e in layout:
            if not e.xdata:
                continue
            for app, tags in e.xdata.data.items():
                for t in tags:
                    if isinstance(t.value, str):
                        add("Extra data on entities (application: string)", f"{app}: {t.value}")
    for layout in doc.layouts:
        d = layout.dxf_layout.dxf
        add("Sheet setups (sheet | printer | plot style | setup)",
            " | ".join(str(v) for v in (layout.name, d.get("plot_configuration_file", ""),
                                        d.get("current_style_sheet", ""), d.get("page_setup_name", ""))))
    for var in doc.header.varnames():
        v = doc.header.get(var)
        if isinstance(v, str) and v:
            add("File settings", f"{var} = {v}")
    try:
        for k, v in doc.header.custom_vars:
            add("File properties", f"{k} = {v}")
    except (AttributeError, TypeError):
        pass
    for x in doc.objects.query("XRECORD"):
        for t in x.tags:
            if isinstance(t.value, str):
                add("Stored records", t.value)

    lines = [f"Words and strings in {title or 'the drawing'}, as the project keeps it.",
             "Look through it for anything private left behind (names, phone numbers, the client, the",
             "consultant, computer or network names) and report it, so it can be taken out too."]
    if cleaning:
        lines += ["", f"When it was added: {cleaning}."]
    for section, counts in found.items():
        lines += ["", f"== {section} ({len(counts)} different) =="]
        lines += [f"{n:6}  {v}" for v, n in sorted(counts.items(), key=lambda kv: kv[0].lower())]
    if identifiers:
        lines += ["", "Left out: " + ", ".join(f"{n} handles and GUIDs in {s.lower()}" for s, n in identifiers.items())
                  + " (numbers CAD programs keep for themselves, not words)."]
    return "\n".join(lines) + "\n"
