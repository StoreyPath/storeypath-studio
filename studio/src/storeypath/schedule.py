"""Door and window sizes from a drawing's schedule of openings.

A drawing lists its door and window types in a table: each type's tag (D1, W4,
SD1), its width, height and sill, then material and remarks. A plan tags each
opening with its type, so the table gives each opening its real sill and height:
a long thin window from near the floor almost to the ceiling, a high window over a
counter, a door 2.20 high.

The table is read from where its texts stand: a header row with a width and a
height (and often a sill) column, and under it a row for each tag. Headers are
matched loosely, as drawings spell them ("HIEGTH", "SILL HIGHT", "SILL H"), and a
row's cells are read under their headers. Sizes are in metres or millimetres, as
the table's numbers show. A drawing often repeats its schedule on every sheet: a
floor takes the one nearest its plan.

Tables are drawn by people: a sill written under the wrong column, an arched
window's height as "1200+ R=600". With the language model, each row is read as a
person reads it (llm.read_schedule_row), and kept when every size it gives is a
number in that row (or two of them added); the rules read it otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import DOOR_TAGS, OPENING_TAG, WINDOW_TAGS, _center, _text_lines, _walk, modelspace_entities

ROW_REACH = 1.2  # text heights: a row's cells stand this close to its tag, up or down
HEADER_REACH = 3.0  # text heights: a header row's cells stand this close to each other, up or down
TAGS_BELOW = 400.0  # text heights: how far under its header a table may run
TAG_COLUMN = 30.0  # text heights: the tags stand this far left of the width column at most
TAG_ALIGN = 3.0  # text heights: a table's tags stand in one column, this close across
ROW_GAP = 4.0  # a table ends where the next tag is this many rows further down than usual
MM_OVER = 20.0  # a size over this is in millimetres
WIDTH_M, HEIGHT_M, SILL_M = (0.3, 8.0), (0.3, 10.0), (0.0, 3.0)  # sizes outside are misread

NUMBER = re.compile(r"\d*[.,]?\d+")


@dataclass
class OpeningSize:
    width: float | None = None  # metres
    height: float | None = None
    sill: float | None = None  # above the floor


@dataclass
class Schedule:
    at: tuple[float, float]  # where its header is, drawing units
    sizes: dict[str, OpeningSize] = field(default_factory=dict)  # tag → size
    headers: list[str] = field(default_factory=list)  # left to right
    rows: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # tag → (header, cell) left to right
    scale: float = 1.0  # metres per the table's unit


def read_schedules(doc, model=None) -> list[Schedule]:
    """Every schedule of openings in the drawing's model space; with the language
    model, its rows read by it."""
    cached = getattr(doc, "_storeypath_schedules", None)
    if cached is None:
        cached = doc._storeypath_schedules = _read(doc)
    if model is not None and model.available() and getattr(doc, "_storeypath_schedules_read", None) != model.name:
        _read_rows(cached, model)
        doc._storeypath_schedules_read = model.name
    return cached


def _read(doc) -> list[Schedule]:
    texts = []  # (text, x, y, height)
    for e, _ in _walk(modelspace_entities(doc)):
        kind = e.dxftype()
        if kind not in ("TEXT", "MTEXT", "ATTRIB"):
            continue
        lines, c = _text_lines(e), _center(e)
        h = e.dxf.get("char_height", 0) if kind == "MTEXT" else e.dxf.get("height", 0)
        if lines and c and h:
            texts.append((" ".join(lines), c[0], c[1], h))
    out = []
    for header in _headers(texts):
        if (table := _table(header, texts)) is not None:
            out.append(table)
    return out


def _read_rows(schedules: list[Schedule], model) -> None:
    """Each row as the language model reads it, kept when its sizes are the row's own
    numbers (or two of them added: an arch's rise on its height)."""
    from .llm import ModelUnavailable, read_schedule_row

    answers: dict[tuple, dict] = {}
    for s in schedules:
        for tag, cells in s.rows.items():
            key = (tuple(s.headers), tag, tuple(cells))
            if key not in answers:
                try:
                    answers[key] = read_schedule_row(model, s.headers, tag, cells)
                except ModelUnavailable:
                    return
            got = answers[key]
            numbers = [float(n.replace(",", ".")) for _, c in cells for n in NUMBER.findall(c)]
            sums = set(numbers) | {a + b for i, a in enumerate(numbers) for b in numbers[i + 1:]}
            ruled = s.sizes.get(tag, OpeningSize())
            read = {}
            for col, band in (("width", WIDTH_M), ("height", HEIGHT_M), ("sill", SILL_M)):
                v = got.get(col)
                ok = isinstance(v, (int, float)) and any(abs(v - n) < 1e-6 for n in sums)
                read[col] = (_within(v, s.scale, band) if ok else None) or getattr(ruled, col)
            if read["width"] is not None or read["height"] is not None:
                s.sizes[tag] = OpeningSize(read["width"], read["height"], read["sill"])


def sizes_near(doc, region, model=None) -> dict[str, OpeningSize]:
    """The opening sizes for a floor: each tag from the schedule nearest its plan."""
    schedules = read_schedules(doc, model)
    if not schedules:
        return {}
    if region is None:
        cx = cy = 0.0
    else:
        cx, cy = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
    out: dict[str, OpeningSize] = {}
    for s in sorted(schedules, key=lambda s: (s.at[0] - cx) ** 2 + (s.at[1] - cy) ** 2):
        for tag, size in s.sizes.items():
            out.setdefault(tag, size)
    return out


def _column(text: str) -> str | None:
    """Which column a header names: width, height or sill."""
    t = re.sub(r"[^a-z]", "", text.lower())
    if not t or len(t) > 14:
        return None
    if t.startswith("sill"):
        return "sill"
    if t in ("w", "wd", "wdth") or t.startswith("wid") or t.startswith("wdt"):
        return "width"
    if t in ("h", "ht", "hgt") or re.fullmatch(r"h(e|i|ei|ie)?(g|gh)(t|th|ht)h?", t):
        return "height"
    return None


def _headers(texts) -> list[dict[str, tuple[float, float, float]]]:
    """Header rows: a width and a height header side by side (and a sill when there
    is one): column → (x, y, text height)."""
    named = [(_column(t), x, y, h, t) for t, x, y, h in texts]
    widths = [(x, y, h, t) for c, x, y, h, t in named if c == "width"]
    out = []
    for wx, wy, wh, wt in widths:
        row = {"width": (wx, wy, wh, wt)}
        for col in ("height", "sill"):
            near = [(abs(x - wx), (x, y, h, t)) for c, x, y, h, t in named
                    if c == col and x > wx and abs(y - wy) <= HEADER_REACH * wh and x - wx <= 25 * wh]
            if near:
                row[col] = min(near)[1]
        if "height" in row:
            # the other headers in the row (leaves, material, remarks): cells under them
            # are not sizes
            right = max(x for x, _, _, _ in row.values())
            for i, (c, x, y, h, t) in enumerate(named):
                if c is None and wx < x < right + 25 * wh and abs(y - wy) <= HEADER_REACH * wh and not NUMBER.search(t):
                    row[f"other {i}"] = (x, y, h, t)
            out.append(row)
    return out


def _table(header, texts) -> Schedule | None:
    wx, wy, h, _ = header["width"]
    found = sorted(((tag, x, y) for t, x, y, th in texts
                    if wy - TAGS_BELOW * h < y < wy - 0.5 * h and wx - TAG_COLUMN * h < x < wx and (tag := _tag(t))),
                   key=lambda r: -r[2])
    tags = _rows(found, h)
    if not tags:
        return None
    columns = sorted((x, col) for col, (x, _, _, _) in header.items())
    names = {col: t for col, (_, _, _, t) in header.items()}
    raw: dict[str, dict[str, float]] = {}
    rows: dict[str, list[tuple[str, str]]] = {}
    for tag, tx, ty in tags:
        cells: dict[str, list[tuple[float, float]]] = {}
        written = []  # (x, header, cell)
        for t, x, y, th in texts:
            if abs(y - ty) > ROW_REACH * max(h, th) or x <= tx:
                continue
            col = _nearest_column(x, columns, h)
            if col is None:
                continue
            written.append((x, names[col], t))
            m = NUMBER.search(t)
            if m:
                cells.setdefault(col, []).append((abs(y - ty), float(m.group().replace(",", "."))))
        row = {col: min(vals)[1] for col, vals in cells.items() if not col.startswith("other")}
        if ("width" in row or "height" in row) and tag not in raw:
            raw[tag] = row
            rows[tag] = [(hd, cell) for _, hd, cell in sorted(written)]
    if not raw:
        return None
    big = any(v > MM_OVER for row in raw.values() for col, v in row.items() if col in ("width", "height"))
    k = 0.001 if big else 1.0
    sizes = {}
    for tag, row in raw.items():
        size = OpeningSize(*(_within(row.get(col), k, band) for col, band in
                             (("width", WIDTH_M), ("height", HEIGHT_M), ("sill", SILL_M))))
        if size.width is not None or size.height is not None:
            sizes[tag] = size
    headers = [names[col] for _, col in columns]
    return Schedule((wx, wy), sizes, headers, rows, k) if sizes else None


def _rows(found: list[tuple[str, float, float]], h: float) -> list[tuple[str, float, float]]:
    """The table's rows: tags down one column under the header, as long as they keep
    their spacing (a blank row or two between doors and windows is kept)."""
    if not found:
        return []
    column = found[0][1]
    rows = [r for r in found if abs(r[1] - column) <= TAG_ALIGN * h]
    out = rows[:1]
    for r in rows[1:]:
        gaps = [a[2] - b[2] for a, b in zip(out, out[1:])]
        usual = sorted(gaps)[len(gaps) // 2] if gaps else 3 * h
        if out[-1][2] - r[2] > ROW_GAP * usual:
            break
        out.append(r)
    return out


def _nearest_column(x: float, columns: list[tuple[float, str]], h: float) -> str | None:
    """The column a cell stands in: the header nearest across, within half the way to
    the next one (or a few text heights past the last)."""
    best = min(columns, key=lambda c: abs(c[0] - x))
    i = columns.index(best)
    gaps = [abs(columns[j][0] - best[0]) for j in (i - 1, i + 1) if 0 <= j < len(columns)]
    reach = min(gaps) / 2 if gaps else 6 * h
    return best[1] if abs(best[0] - x) <= max(reach, 2 * h) else None


def _tag(text: str) -> str | None:
    """A door or window type's tag, as the plans write it: D1, W12, SD1."""
    m = OPENING_TAG.fullmatch(text.strip().upper())
    return f"{m['kind']}{m['n']}" if m and m["kind"] in DOOR_TAGS | WINDOW_TAGS else None


def _within(v: float | None, k: float, band: tuple[float, float]) -> float | None:
    if v is None:
        return None
    v = round(v * k, 3)
    return v if band[0] <= v <= band[1] else None
