"""A location's layout (format 0.12; docs/DESIGN-LOCATION-LAYOUTS.md): its site plan read
into the layout's objects, keeping their IDs (read_layout, apply_layout), and the
location's buildings matched to the footprints on it (match_buildings) and placed on them
(place).

The layout's frame is the site's: its source's offset is chosen when it is first read so
that the middle of its plot is the site's centre. Areas and footprints keep their IDs by
overlap, as rooms do; gates by where they are, as doors do. A building is placed on its
footprint by its SitePosition, as a person moves it on the site plan: nothing in it
changes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from shapely import affinity
from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union

from .cad import file_sha256, meters_per_unit
from . import openings
from .convert import ConversionReport, _allocate_id, _drawing, _local, _match_spaces, _retire
from .geometry import as_polygons, iou
from .ids import LAND_CODE, make_id, parse_id
from .siteplan import (AREA, BUILDING, BUILDING_LEVEL, GROUND, NOTHING, OUTSIDE, SitePlanRead, building_name, plain,
                       read_site_plan, what_text_says)
from .types import FloorType, OpeningType, SpaceType
from .workspace import (Building, North, ObjectRecord, Override, Reading, SitePosition, Workspace, floor_order,
                        utcnow)

METHOD = "site plan"  # how a layout's objects were found (its level's method)
GATE_MATCH_M = openings.GATE_MATCH_M  # a gate read this near one read before is that one
MATCH_FLOOR = 0.35  # a footprint is matched to a building this sure, at least
SITE_READING = "site: "  # what a site plan's text says is kept among the project's readings by this and the text


# ---- what a site plan's texts say, kept ------------------------------------------------------

class SiteTexts:
    """What the texts of a site plan the rules do not know say, from the language model,
    kept in the project's readings (SITE_READING and the text) so that a layout read again
    says the same, with or without the model."""

    KINDS = {"building": BUILDING, "area": AREA, "ground_level": GROUND, "building_level": BUILDING,
             "outside": OUTSIDE, "nothing": NOTHING}

    def __init__(self, ws: Workspace, model=None):
        self.ws = ws
        self.model = model if model is not None and model.available() else None
        self.failed: str | None = None

    def __call__(self, texts: list[str]) -> dict[str, tuple]:
        from .llm import SITE_QUESTION, ModelUnavailable, read_site_texts

        asked = f"{self.model.name}/{SITE_QUESTION}" if self.model else None
        out, ask = {}, []
        for t in texts:
            r = self.ws.readings.get(SITE_READING + t)
            if r is not None and (r.source != "model" or self.model is None or (r.asked or "").endswith(asked or "")):
                out[t] = self._said(r)
            elif self.model is not None and re.search(r"[^\W\d_]", t):
                ask.append(t)
        if ask:
            try:
                answers = read_site_texts(self.model, ask)
            except ModelUnavailable as e:
                self.failed = str(e)
                answers = {}
            for t, (kind, area) in answers.items():
                self.ws.readings[SITE_READING + t] = Reading(
                    type=SpaceType(area) if area else None, source="model", asked=f"site:{kind}|{asked}")
                out[t] = self._said(self.ws.readings[SITE_READING + t])
        return {t: s for t, s in out.items() if s is not None}

    def _said(self, r: Reading) -> tuple | None:
        kind = (r.asked or "").removeprefix("site:").split("|", 1)[0]
        if r.type is not None:
            return AREA, r.type
        return (self.KINDS[kind], None) if kind in self.KINDS else None


# ---- reading the layout ------------------------------------------------------------------------

@dataclass
class LayoutRead:
    """A layout's site plan read (read_layout), to be applied to the project (apply_layout)."""

    layout_id: str
    read: SitePlanRead
    sha: str
    offset: tuple[float, float]  # what the source's offset is (drawing units): chosen at its first read
    notes: list[str] = field(default_factory=list)
    base: openings.LandBase | None = None  # what review's gates are put in on (openings.py)…
    edits_read: str = ""  # …what review had done to them when it was read (openings.edits_key)…
    keep_in: Path | None = None  # …and where it is kept, beside memory


def read_layout(ws: Workspace, layout_id: str, workspace_dir: str | Path = ".", model=None, say=None,
                reads: str | Path | None = None) -> LayoutRead:
    """Read a layout's site plan (siteplan.read_site_plan) in the layout's frame: its
    source's offset, or, read for the first time, the middle of its plot (the site's
    centre). What the model answered is kept in ``ws``. ``reads``: a folder where what
    review's gates are put in on is kept (openings.py), beside memory."""
    floor = ws.floor(layout_id)
    if not floor.layout:
        raise ValueError(f"{layout_id} is not a location's layout")
    if floor.source is None:
        raise ValueError(f"layout {layout_id} has no site plan")
    src = floor.source
    path = Path(workspace_dir) / src.path
    sha = file_sha256(path)
    doc = _drawing(path, sha)
    texts = SiteTexts(ws, model)
    read = read_site_plan(doc, src.region, src.units, src.offset, floor.edits, texts, say)
    offset = tuple(src.offset) if src.offset is not None else None
    frame = read.frame
    if offset is None and read.middle is not None:  # the plot's middle, the site's centre
        scale = meters_per_unit(doc, src.units)
        offset = (round(read.middle[0] / scale, 4), round(read.middle[1] / scale, 4))
        dx, dy = offset[0] * scale, offset[1] * scale
        _moved(read, dx, dy)
        frame = (dx, dy)  # (where the next read puts it: the offset chosen)
    notes = list(read.warnings)
    if texts.failed:
        notes.append(f"the language model was not used: {texts.failed}")
    base = openings.LandBase(floor_id=layout_id, gates=read.gates_found, areas=read.areas_found, dx=frame[0],
                             dy=frame[1]) if read.boundary is not None else None
    return LayoutRead(layout_id, read, sha, offset or (0.0, 0.0), notes, base, openings.edits_key(floor.edits),
                      Path(reads) if reads is not None else None)


def _moved(read: SitePlanRead, dx: float, dy: float) -> None:
    move = lambda g: affinity.translate(g, -dx, -dy)  # noqa: E731
    read.boundary = move(read.boundary) if read.boundary is not None else None
    read.walls = move(read.walls) if read.walls is not None else None
    for a in read.areas:
        a.polygon = move(a.polygon)
    for g in read.gates:
        g.span = move(g.span)
    for f in read.footprints:
        f.polygon = move(f.polygon)


def apply_layout(ws: Workspace, lr: LayoutRead, force: bool = False) -> ConversionReport:
    """A layout's read registered in ``ws``: its level (the plot as its outline, its walls,
    its north), its areas and footprints keeping their IDs by overlap, its gates by where
    they are; what is gone retired. A read that finds no plot where the layout has areas
    is held back (unless ``force``). The location's north from its arrow, where no person
    set it and the site is not on the map; the buildings matched to the footprints anew."""
    layout_id, read = lr.layout_id, lr.read
    report = ConversionReport(layout_id, method=METHOD, warnings=list(lr.notes))
    floor = ws.floor(layout_id)
    if lr.base is not None and openings.edits_key(floor.edits) != lr.edits_read:
        # review changed the layout's gates while it was read (at once: openings.py): as they are now
        read.gates = lr.base.worked(floor.edits)
    land_id = parse_id(layout_id).prefix("building")
    existing = [r for r in ws.floor_objects(layout_id)]
    if read.boundary is None and not force and any(r.kind == "space" for r in existing):
        report.held, report.hold = True, "nothing was changed: no plot was found in the site plan"
        report.warnings.append(report.hold)
        return report
    now = utcnow()
    floor.source.offset = lr.offset
    floor.source.sha256 = lr.sha
    floor.outline = _local(read.boundary) if read.boundary is not None else None
    floor.walls = _local(read.walls) if read.walls is not None else None
    floor.wall_thickness = read.wall_thickness
    floor.north = read.north
    floor.method, floor.warnings, floor.converted_at = METHOD, list(report.warnings), now
    # areas, footprints: by overlap
    for kind, found in (("space", read.areas), ("footprint", read.footprints)):
        before = [r for r in existing if r.kind == kind]
        pairs = _match_spaces(before, [_Shaped(f.polygon, None) for f in found])
        for i, f in enumerate(found):
            record = pairs.get(i)
            if record is None:
                record_id = _allocate_id(ws, layout_id, land_id)
                record = ws.objects[record_id] = ObjectRecord(id=record_id, kind=kind, type="footprint", geometry={})
                report.added.append(record_id)
            else:
                report.kept.append(record.id)
            record.geometry = _local(f.polygon)
            if kind == "space":
                record.type, record.type_source = f.type.value, f.type_source
                record.name, record.number, record.label = f.name, None, f.label
                record.issues = list(f.issues)
            else:
                record.type, record.type_source = "footprint", "drawing"
                record.name, record.number = building_name(f.texts), None
                record.label = "\n".join(f.texts) or None
                record.issues = [] if f.named else ["no building's name in it: matched by its shape alone"]
                record.levels = {"floor": f.floor_level, "ground": f.ground_level} \
                    if f.floor_level is not None or f.ground_level is not None else None
        kept = {id(pairs[i]) for i in pairs}
        for r in before:
            if id(r) not in kept:
                _retire(r, now, report)
    area_ids = [r.id if r is not None else None for r in _by_geometry(ws, layout_id, "space", read.areas)]
    # gates: by where they are (openings.match_gates)
    before = [r for r in existing if r.kind == "opening"]
    used: set[str] = set()
    for g, record in zip(read.gates, openings.match_gates(before, read.gates)):
        if record is not None:
            report.kept.append(record.id)
        else:
            record_id = _allocate_id(ws, layout_id, land_id)
            record = ws.objects[record_id] = ObjectRecord(id=record_id, kind="opening", type=OpeningType.GATE.value,
                                                          geometry={})
            report.added.append(record_id)
        used.add(record.id)
        openings.put_fields(record, openings.gate_fields(g, area_ids))
    for r in before:
        if r.id not in used:
            _retire(r, now, report)
    report.unspecified = [r.id for r in ws.floor_objects(layout_id) if r.kind == "space"
                          and ws.effective(r)["type"] == "unspecified"]
    # which way north is: from the arrow, where no person said and the site is not on the map
    loc = ws.location(parse_id(layout_id).prefix("location"))
    if read.north is not None and loc.placement is None and (loc.north is None or loc.north.source == "drawing"):
        loc.north = North(bearing=read.north.bearing, source="drawing", sure=read.north.sure)
    match_buildings(ws, make_id(ws.id, loc.code))
    if lr.base is not None:  # what review's gates are put in on, kept (openings.py)
        lr.base.register(ws, layout_id, area_ids)
        openings.keep(lr.base, lr.keep_in)
    return report


@dataclass
class _Shaped:
    polygon: Polygon
    number: str | None


def _by_geometry(ws: Workspace, layout_id: str, kind: str, found: list) -> list[ObjectRecord]:
    """The records of what was found (in its order), by their geometry as just registered."""
    records = [r for r in ws.floor_objects(layout_id) if r.kind == kind]
    out = []
    for f in found:
        g = _local(f.polygon)
        out.append(next((r for r in records if r.geometry == g), None))
    return out


def convert_layout(ws: Workspace, layout_id: str, workspace_dir: str | Path = ".", model=None, say=None,
                   force: bool = False) -> ConversionReport:
    """Read a layout's site plan and apply it (read_layout, apply_layout)."""
    return apply_layout(ws, read_layout(ws, layout_id, workspace_dir, model, say), force=force)


# ---- matching buildings to footprints ----------------------------------------------------------

NOISE = {"proposed", "prop", "new", "existing", "exist", "modified", "revised", "future", "the", "of", "plan",
         "floor", "ground", "first", "second", "third", "level", "roof", "deck", "layout", "drawing", "detail",
         "gf", "ff"}
GENERIC = {"building", "bldg", "block", "room", "rm", "house", "unit", "wing", "area", "main"}
SAME_WORD = {"bldg": "building", "blk": "block", "rm": "room", "hse": "house", "ext": "external"}


def _words(text: str) -> list[str]:
    """A name's words, lower case, as matching takes them (G+1 and the like left out)."""
    t = re.sub(r"\bg\s*\+\s*\d+\b", " ", plain(text).lower())
    return [SAME_WORD.get(w, w) for w in re.findall(r"[^\W_]+", t) if not w.isdigit()]


def label_score(labels: list[str], names: list[str]) -> float:
    """How well a footprint's texts name a building (its name, code, its plans' titles):
    0 to 1, by their words (the plan's state and its kind left out; a word a building
    may have any name share counts less; one letter as a word's first: an abbreviation)."""
    a = {w for t in labels for w in _words(t) if w not in NOISE or len(_words(t)) == 1}
    b = {w for t in names for w in _words(t) if w not in NOISE}
    a.discard("")
    if not a or not b:
        return 0.0
    weight = lambda w: 0.3 if w in GENERIC else 1.0  # noqa: E731

    def found(w: str, other: set[str]) -> float:
        if w in other:
            return 1.0
        if len(w) == 1 and any(o.startswith(w) and len(o) > 1 for o in other):
            return 0.5
        if len(w) > 1 and any(len(o) == 1 and w.startswith(o) for o in other):  # (its first letter, written alone)
            return 0.5
        if len(w) >= 3 and any(o.startswith(w) or w.startswith(o) for o in other if len(o) >= 3):
            return 0.7
        return 0.0

    of_a = sum(weight(w) * found(w, b) for w in a) / sum(weight(w) for w in a)
    of_b = sum(weight(w) * found(w, a) for w in b) / sum(weight(w) for w in b)
    if of_a == 0 or of_b == 0:
        return 0.0
    return round(2 * of_a * of_b / (of_a + of_b), 3)


@dataclass
class Fit:
    """A building's outline put on a footprint: turned ``rotation`` degrees counter-
    clockwise about its frame's origin, then moved ``dx``, ``dy`` (a point p of its frame
    is at R(rotation)·p + (dx, dy) in the site's); how much they overlap (IoU), and
    whether another turn fits as well (``ambiguous``)."""

    rotation: float
    dx: float
    dy: float
    iou: float
    ambiguous: bool = False

    def site(self, pivot) -> SitePosition:
        """As a SitePosition (turned clockwise about ``pivot``, the pivot at x, y)."""
        r = math.radians(self.rotation)
        px, py = pivot
        x = px * math.cos(r) - py * math.sin(r) + self.dx
        y = px * math.sin(r) + py * math.cos(r) + self.dy
        return SitePosition(x=round(x, 3), y=round(y, 3), rotation=round(-self.rotation % 360, 3) % 360,
                            pivot=(round(px, 3), round(py, 3)))


SIZES_APART = 2.5  # a building's outline and a footprint more unlike in size than this never match


def main_direction(poly) -> float:
    """The direction most of an outline's edges run (degrees, 0 to 90: a right angle's
    turn is the same), by their lengths."""
    weights = [0.0] * 90
    for p in as_polygons(poly):
        cs = list(p.exterior.coords)
        for a, b in zip(cs, cs[1:]):
            L = math.dist(a, b)
            if L < 0.2:
                continue
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90
            weights[int(ang) % 90] += L * (1 - (ang % 1))
            weights[(int(ang) + 1) % 90] += L * (ang % 1)
    smooth = [weights[i - 1] + 2 * weights[i] + weights[(i + 1) % 90] for i in range(90)]
    i = max(range(90), key=lambda k: smooth[k])
    # its middle, from the bins beside it
    lo, mid, hi = smooth[i - 1], smooth[i], smooth[(i + 1) % 90]
    shift = 0.5 * (lo - hi) / (lo - 2 * mid + hi) if (lo - 2 * mid + hi) != 0 else 0.0
    return (i + max(-0.5, min(0.5, shift))) % 90


def fit_shape(outline, footprint) -> Fit | None:
    """The building's outline (its own frame) fitted onto a footprint (the site's frame):
    the turns its main direction and the footprint's give (and their right angles, and
    no turn), their middles put together, then nudged and turned a little for the most
    overlap; never mirrored, never scaled (both are metres). None when they are too unlike
    in size."""
    if outline is None or footprint is None or outline.area <= 0 or footprint.area <= 0:
        return None
    if not 1 / SIZES_APART <= outline.area / footprint.area <= SIZES_APART:
        return None
    ca, cb = outline.centroid, footprint.centroid
    a0 = affinity.translate(outline, -ca.x, -ca.y)
    b0 = affinity.translate(footprint, -cb.x, -cb.y)
    base = (main_direction(b0) - main_direction(a0)) % 90
    turns = sorted({round((base + k * 90) % 360, 4) for k in range(4)} | {0.0, 90.0, 180.0, 270.0})
    tried = []
    for turn in turns:
        r = affinity.rotate(a0, turn, origin=(0, 0))
        score, tx, ty = _nudged(r, b0, 0.0, 0.0)
        tried.append((score, turn, tx, ty))
    tried.sort(key=lambda x: -x[0])
    best = None
    for score, turn, tx, ty in tried[:3]:  # the best turns, turned a little
        for step in (1.0, 0.25):
            for d in (-2, -1, 1, 2):
                t2 = turn + d * step
                r = affinity.rotate(a0, t2, origin=(0, 0))
                s2, x2, y2 = _nudged(r, b0, tx, ty)
                if s2 > score + 1e-4:
                    score, turn, tx, ty = s2, t2, x2, y2
        if best is None or score > best[0] + 1e-6:
            best = (score, turn % 360, tx, ty)
    score, turn, tx, ty = best
    # as good another way round (a rectangle turned half): the shape cannot tell; the
    # nearest to no turn is taken (drawings of one place are mostly drawn the same way up)
    alike = [(s, t, x, y) for s, t, x, y in tried if s >= score - 0.03 and _apart(t, turn) > 5]
    ambiguous = bool(alike)
    if ambiguous:
        options = [(score, turn, tx, ty), *alike]
        score, turn, tx, ty = min(options, key=lambda o: (_apart(o[1], 0), -o[0]))
        r = affinity.rotate(a0, turn, origin=(0, 0))
        score, tx, ty = _nudged(r, b0, tx, ty)
    # the turn about the frame's origin, then where the origin goes
    rr = math.radians(turn)
    ox = -(ca.x * math.cos(rr) - ca.y * math.sin(rr)) + cb.x + tx
    oy = -(ca.x * math.sin(rr) + ca.y * math.cos(rr)) + cb.y + ty
    return Fit(round(turn % 360, 3), round(ox, 4), round(oy, 4), round(score, 4), ambiguous)


def _apart(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _nudged(a, b, tx: float, ty: float) -> tuple[float, float, float]:
    """``a`` moved about (from tx, ty) for the most overlap with ``b``: IoU, x, y."""
    best = iou(affinity.translate(a, tx, ty), b)
    step = 0.5
    while step >= 0.02:
        moved = False
        for mx, my in ((step, 0), (-step, 0), (0, step), (0, -step)):
            s = iou(affinity.translate(a, tx + mx, ty + my), b)
            if s > best + 1e-6:
                best, tx, ty, moved = s, tx + mx, ty + my, True
                break
        if not moved:
            step /= 2
    return best, tx, ty


def outlines_of(b: Building) -> list:
    """What a building's footprint may be drawn as on a site plan, in its own frame: its
    ground floor's outline, and all its floors' together (a roof wider than its walls)."""
    out = []
    ground = next((f for f in sorted(b.floors, key=floor_order) if f.outline and f.ordinal == 0
                   and f.type == FloorType.FLOOR), None)
    if ground is not None:
        out.append(shape(ground.outline))
    every = [shape(f.outline) for f in b.floors if f.outline]
    if every:
        whole = unary_union(every)
        if not out or abs(whole.area - out[0].area) > 0.02 * out[0].area:
            out.append(whole)
    return [g.buffer(0) for g in out if not g.is_empty]


def names_in(texts: list[str]) -> list[str]:
    """Of a footprint's texts, those that may name it: not its levels, numbers or notes."""
    out = []
    for t in texts:
        said = what_text_says(t)
        if t and (said is None or (said[0] == BUILDING and not BUILDING_LEVEL.search(plain(t)))):
            out.append(t)
    return out


def names_of(b: Building) -> list[str]:
    """What a building is called: its name, its code, its plans' titles."""
    return [b.name, b.code, *(f.source.view for f in b.floors if f.source and f.source.view)]


def match_buildings(ws: Workspace, location_id: str, model=None, say=None) -> list[dict]:
    """Each footprint of a location's layout matched to the building of the location it
    most likely is (its record's ``match``), one building to one footprint, the surest
    first: by its texts against the building's names (the language model asked where the
    words cannot say), and by the building's outline fitted onto it. What was matched, as
    the records keep it."""
    loc = ws.location(location_id)
    layout = ws.layout_of(location_id)
    if layout is None:
        return []
    layout_id = f"{location_id}-{LAND_CODE}-{layout.code}"
    prints = [r for r in ws.floor_objects(layout_id) if r.kind == "footprint"]
    buildings = [(make_id(ws.id, loc.code, b.code), b) for b in loc.buildings]
    # what a person said a footprint is stands: that building is proposed for no other
    said = {r.id: o.building for r in prints if (o := ws.overrides.get(r.id)) is not None and o.building is not None}
    buildings = [(b_id, b) for b_id, b in buildings if b_id not in said.values()]
    pairs = []
    for r in prints:
        if r.id in said:
            continue
        fp = shape(r.geometry)
        labels = names_in((r.label or "").split("\n"))
        r.match = None
        for b_id, b in buildings:
            lab = label_score(labels, names_of(b))
            fits = [f for f in (fit_shape(o, fp) for o in outlines_of(b)) if f is not None]
            fit = max(fits, key=lambda f: f.iou) if fits else None
            pairs.append([r, b_id, b, lab, fit, "label" if lab else None])
    if model is not None and model.available():
        _ask_model(model, pairs, prints, buildings)
    scored = []
    for r, b_id, b, lab, fit, by in pairs:
        conf, how = _confidence(lab, fit, by)
        if conf >= MATCH_FLOOR:
            scored.append((conf, how, r, b_id, b, lab, fit))
    scored.sort(key=lambda x: (-x[0], x[2].id, x[3]))
    taken_r, taken_b, out = set(), set(), []
    for conf, how, r, b_id, b, lab, fit in scored:
        if r.id in taken_r or b_id in taken_b:
            continue
        taken_r.add(r.id)
        taken_b.add(b_id)
        outline = max(outlines_of(b), key=lambda o: fit_shape(o, shape(r.geometry)).iou
                      if fit_shape(o, shape(r.geometry)) else 0, default=None) if fit is not None else None
        r.match = {"building": b_id, "confidence": round(conf, 3), "by": how, "label": lab,
                   "shape": fit.iou if fit else None, "ambiguous": fit.ambiguous if fit else False,
                   "site": _placed(fit, outline, r).model_dump(mode="json") if fit is not None else None}
        out.append({"footprint": r.id, **r.match})
    for r in prints:
        if r.id in said:  # (as a person said: no other match is proposed for it)
            r.match = {"building": said[r.id] or None, "confidence": 1.0, "by": "person", "label": None,
                       "shape": None, "ambiguous": False, "site": None}
        elif r.match is None:
            r.match = {"building": None, "confidence": 0.0, "by": None, "label": 0.0, "shape": None,
                       "ambiguous": False, "site": None}
    if say is not None:
        say(f"buildings matched to {len(out)} of {len(prints)} footprint(s)")
    return out


def _placed(fit: Fit, outline, r: ObjectRecord) -> SitePosition:
    """The SitePosition a fit gives, about the middle of the outline fitted."""
    c = outline.centroid if outline is not None else Point(0, 0)
    pos = fit.site((c.x, c.y))
    pos.footprint = r.id
    return pos


def _confidence(lab: float, fit: Fit | None, by: str | None) -> tuple[float, str | None]:
    """How sure a footprint is a building, from its words and its shape, and what says so."""
    shape_ = fit.iou * (0.9 if fit.ambiguous else 1.0) if fit is not None else 0.0
    if lab > 0 and fit is not None:
        conf = 0.45 * lab + 0.55 * shape_ + (0.1 if lab >= 0.6 and shape_ >= 0.6 else 0.0)
        how = "both" if shape_ >= 0.5 else (by or "label")
        conf = max(conf, 0.85 * shape_, 0.75 * lab)
    elif fit is not None:
        conf, how = 0.85 * shape_, "shape"
    else:
        conf, how = 0.75 * lab, by or "label"
    return min(conf, 0.99), how if conf > 0 else None


def _ask_model(model, pairs: list, prints: list, buildings: list) -> None:
    """For a footprint whose words name no building plainly, the model is asked which
    building its texts name (by: "model")."""
    from .llm import ModelUnavailable, which_building

    names = [b.name for _, b in buildings]
    for r in prints:
        mine = [p for p in pairs if p[0] is r]
        if not r.name or any(p[3] >= 0.5 for p in mine):
            continue
        try:
            k = which_building(model, r.name, names)
        except ModelUnavailable:
            return
        if k is not None:
            for p in mine:
                if p[1] == buildings[k][0]:
                    p[3], p[5] = max(p[3], 0.7), "model"


# ---- placing buildings on their footprints -----------------------------------------------------

def footprint_building(ws: Workspace, r: ObjectRecord) -> str | None:
    """The building a footprint is, as a person said (its correction), else as matched;
    None for none ("" said so: skipped)."""
    o = ws.overrides.get(r.id)
    if o is not None and o.building is not None:
        return o.building or None
    return (r.match or {}).get("building")


def place(ws: Workspace, footprint_id: str, building_id: str | None, *, by_person: bool = True) -> SitePosition | None:
    """A footprint said to be ``building_id`` (None or "": none, skipped), as a person
    chose: kept as its correction, and the building placed on it (its SitePosition from
    its outline fitted there; a building without floors stands on it as the site's frame
    has it). The location's other buildings stay where they are on its site. The position
    given, or None."""
    from .export import settle

    r = ws.objects.get(footprint_id)
    if r is None or r.kind != "footprint" or r.status != "active":
        raise KeyError(f"no footprint {footprint_id}")
    loc = ws.location(parse_id(footprint_id).prefix("location"))
    o = ws.overrides.get(footprint_id) or Override()
    if not building_id:
        o.building = ""
        ws.overrides[footprint_id] = o
        return None
    b = ws.building(building_id)
    if b.code == LAND_CODE or parse_id(building_id).prefix("location") != make_id(ws.id, loc.code):
        raise ValueError(f"{building_id} is not a building of {make_id(ws.id, loc.code)}")
    for other_id, other in list(ws.overrides.items()):  # one footprint a building
        if other_id != footprint_id and other.building == building_id:
            other.building = None
    if by_person:
        o.building = building_id
        ws.overrides[footprint_id] = o
    fp = shape(r.geometry)
    settle(loc)
    outlines = outlines_of(b)
    fits = [(o_, f) for o_ in outlines if (f := fit_shape(o_, fp)) is not None]
    if fits:
        outline, fit = max(fits, key=lambda x: x[1].iou)
        pos = _placed(fit, outline, r)
    elif outlines:  # nothing like it in size: its middle on the footprint's, as it is turned
        c, m = unary_union(outlines).centroid, fp.centroid
        pos = SitePosition(x=round(m.x, 3), y=round(m.y, 3), rotation=0.0, pivot=(round(c.x, 3), round(c.y, 3)),
                           footprint=footprint_id)
    else:  # no floors yet: its frame the site's, standing on the footprint as drawn
        pos = SitePosition(x=0.0, y=0.0, rotation=0.0, pivot=(0.0, 0.0), footprint=footprint_id)
    b.site = pos
    b.placement = None  # on its location's site, not on the map by itself
    return pos


def place_all(ws: Workspace, location_id: str, *, again: bool = False, sure: float = 0.0,
              confirm: bool = False) -> list[tuple[str, str]]:
    """Every footprint of a location's layout with a building (a person's choice, else its
    match at least ``sure``) placed: those whose buildings stand on them already are left
    as they are, unless ``again`` (the layout read again, placed anew). ``confirm``: the
    matches are a person's choice now (Apply all). (footprint, building) of those placed."""
    layout = ws.layout_of(location_id)
    if layout is None:
        return []
    layout_id = f"{location_id}-{LAND_CODE}-{layout.code}"
    out = []
    for r in ws.floor_objects(layout_id):
        if r.kind != "footprint":
            continue
        o = ws.overrides.get(r.id)
        chosen = o is not None and o.building is not None
        b_id = footprint_building(ws, r)
        if not b_id or (not chosen and (r.match or {}).get("confidence", 0) < sure):
            continue
        b = ws.building(b_id)
        if b.site is not None and b.site.footprint == r.id and not again:
            continue
        place(ws, r.id, b_id, by_person=chosen or confirm)
        out.append((r.id, b_id))
    return out


def new_building(ws: Workspace, footprint_id: str, name: str | None = None, code: str | None = None) -> str:
    """A building of the location made for a footprint no building is (without floors yet:
    its drawings added later are matched to it), named from its texts, placed on it."""
    from .export import settle

    r = ws.objects.get(footprint_id)
    if r is None or r.kind != "footprint":
        raise KeyError(f"no footprint {footprint_id}")
    loc_id = parse_id(footprint_id).prefix("location")
    loc = ws.location(loc_id)
    name = (name or r.name or "Building").strip()[:80] or "Building"
    taken = {b.code for b in loc.buildings} | {LAND_CODE}
    base = code or (re.findall(r"[A-Z0-9]+", name.upper()) or ["B1"])[0][:8]
    code, n = base, 2
    while code in taken:
        code, n = f"{base[:14]}{n}", n + 1
    settle(loc)
    b_id = ws.add_building(loc_id, code, name)
    place(ws, footprint_id, b_id)
    return b_id


def site_frame_of(b: Building, pos: SitePosition | None = None) -> tuple[float, float, float] | None:
    """Where a building's frame lies on its site (format 0.12, placements' ``site``): its
    origin's x, y in the site's frame and its turn, degrees clockwise. None when it has no
    place on its site (placed on the map by itself)."""
    pos = pos or b.site
    if pos is None:
        return None
    r = math.radians(pos.rotation)
    px, py = pos.pivot
    x = pos.x - (px * math.cos(r) + py * math.sin(r))
    y = pos.y - (-px * math.sin(r) + py * math.cos(r))
    return round(x, 4), round(y, 4), round(pos.rotation % 360, 4)
