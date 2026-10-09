"""Making an area sample of a floor (see the package's docstring and
docs/AREA-SAMPLES.md)."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import ezdxf

from ..cad import drawing_units, header_units, meters_per_unit, read_drawing
from ..profile import load_profile
from .content import LocalIds, corrections_of, in_area, reading_of, texts_of
from .cut import cut, used_layers
from .frame import MARGIN_M, MAX_SIDE_M, Area, Frame
from .picture import draw
from .private import Privacy, names_and_codes

SAMPLE_FORMAT = "storeypath-area-sample"
SAMPLE_VERSION = 1
SUFFIX = ".spsample"
NOTE_MAX = 2000  # characters of a person's note
CUT_CACHE = 4  # drawings' parts kept between a preview and its download
_CUTS: OrderedDict = OrderedDict()
_CUTS_LOCK = threading.Lock()

FILES = {
    "manifest.json": "what this sample is: its format, when and by which Studio it was made, the area, what was "
                     "taken out for privacy, the person's note",
    "README.txt": "for a person who opens the file",
    "drawing.dxf": "the drawing's part in the area (DXF, the drawing's own units, the area's lower-left corner at 0,0)",
    "drawing.png": "the area as drawn",
    "reading.png": "the area with Studio's reading drawn over it",
    "reading.json": "what Studio decided in the area, and why",
    "corrections.json": "what people changed in the area: the right answers",
}
HOW_CUT = ("Every entity of the floor's plan that touches the area or its margin; one within them is kept whole, "
           "a line or fill running out of them is cut at the margin's edge, a block placed across the edge is "
           "taken apart. Paper space, layouts, external references, images, embedded objects and underlays are "
           "never in it; it is a new DXF document with only the layers, linetypes, styles and blocks it uses.")
FROM_DWG = ("Studio keeps every drawing as DXF: a DWG is converted with LibreDWG's dwg2dxf when it is added, "
            "and that DXF is what Studio reads. This part is of it.")

README = """\
StoreyPath area sample {id}

This file is a small part of a floor plan, shared to help improve StoreyPath Studio:
the drawing in the area that was chosen (drawing.dxf, which any CAD program opens),
the area as drawn (drawing.png), how Studio read it (reading.png, and reading.json:
the rooms, doors and windows it found, their types and who decided them), and what
people corrected there (corrections.json). manifest.json says when and by which
Studio it was made, and holds the note written with it.

Before it was made, private information was taken out: names of people, phone
numbers, emails and the like were replaced by placeholders ([NAME], [PHONE]…), and
so were the project's, site's, building's and floor's names; the area was moved so
that its lower-left corner is at 0,0, and the drawing file carries none of the
original file's own data. To look at it, unzip it and open the two pictures side by
side; developers read it with `storeypath sample inspect` and `storeypath sample
replay` (see docs/AREA-SAMPLES.md in StoreyPath Studio).
"""


@dataclass
class Sample:
    id: str
    files: dict[str, bytes]
    manifest: dict
    privacy: Privacy
    reading: dict = field(default_factory=dict)
    corrections: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"{self.id}{SUFFIX}"

    def zipped(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name in FILES:
                if name in self.files:
                    z.writestr(name, self.files[name])
        return out.getvalue()


def new_id() -> str:
    return base64.b32encode(os.urandom(5)).decode().lower()


def studio_version() -> dict:
    """This Studio's version, and its git commit when known (STOREYPATH_COMMIT, else the
    checkout it runs from)."""
    from importlib.metadata import PackageNotFoundError, version

    from ..assets import REPO_ROOT

    try:
        v = version("storeypath")
    except PackageNotFoundError:
        v = None
    commit = os.environ.get("STOREYPATH_COMMIT") or None
    if commit is None and (REPO_ROOT / ".git").exists():
        try:
            commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short=12", "HEAD"],
                                    capture_output=True, text=True, timeout=5).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            commit = None
    return {"version": v, "commit": commit}


def _part(review, floor_id: str, area: Area):
    """The drawing's part in the area (a fresh document each time, to be cleaned), its
    scale and what was done to make it. Kept for a few areas: a preview and its download
    read the drawing once."""
    ws = review.workspace()
    f = review._floor(ws, floor_id)
    src = f.source
    if src is None:
        raise ValueError("this floor has no drawing: an area sample is made from a floor's drawing")
    drawing, sha = review.source.drawing_key(src)
    key = (review.source.name, floor_id, drawing, sha, src.units, src.region, src.offset, area)
    with _CUTS_LOCK:
        kept = _CUTS.get(key)
        if kept is not None:
            _CUTS.move_to_end(key)
    if kept is None:
        with review.source.drawing_file(src) as path:
            whole = read_drawing(path)
            suffix = Path(src.path).suffix.lower()
        scale = meters_per_unit(whole, src.units)
        ox, oy = src.offset or (0.0, 0.0)
        frame = Frame(area)
        lx0, ly0, lx1, ly1 = frame.local_extent.bounds
        keep = (lx0 / scale + ox, ly0 / scale + oy, lx1 / scale + ox, ly1 / scale + oy)
        origin = (area.x0 / scale + ox, area.y0 / scale + oy)
        part = cut(whole, keep, origin, src.region, scale)
        if part.kept + part.cut == 0:
            raise ValueError("nothing of the floor's drawing is in this area: choose an area on the plan")
        info = {"units": drawing_units(whole, src.units), "header_units": header_units(whole),
                "dxf_version": whole.dxfversion, "suffix": suffix, "kept_whole": part.kept, "cut_at_edge": part.cut,
                "blocks_taken_apart": part.taken_apart, "left_out": dict(part.left_out)}
        kept = (part.text(), scale, info)
        with _CUTS_LOCK:
            _CUTS[key] = kept
            while len(_CUTS) > CUT_CACHE:
                _CUTS.popitem(last=False)
    text, scale, info = kept
    return ezdxf.read(io.StringIO(text)), scale, info


def dxf_strings(doc) -> list[str]:
    """Every text written in a DXF document: on the model space and in its blocks,
    attributes, attribute definitions' defaults and prompts, dimensions' own texts."""
    out = []
    for layout in [doc.modelspace(), *doc.blocks]:
        for e in layout:
            kind = e.dxftype()
            if kind in ("TEXT", "ATTDEF", "MTEXT"):
                out.append(_plain(e))
                if kind == "ATTDEF":
                    out.append(e.dxf.get("prompt", ""))
            elif kind == "INSERT":
                out += [_plain(a) for a in e.attribs]
            elif kind == "DIMENSION" and e.dxf.get("text", "") not in ("", "<>", " "):
                out.append(e.dxf.text)
    return [s for s in out if s and s.strip()]


def _plain(e) -> str:
    try:
        return e.plain_text(split=False) if e.dxftype() == "MTEXT" else e.plain_text()
    except Exception:
        return e.dxf.get("text", "") or ""


def scrub_drawing(doc, privacy: Privacy) -> None:
    """The texts of a drawing's part with what goes replaced (privacy.scrub), and its
    layers' and blocks' names without the project's names and codes."""
    def text_of(e):
        plain = _plain(e)
        new = privacy.scrub(plain)
        if new == plain:
            return
        if e.dxftype() == "MTEXT":
            e.text = new.replace("\n", "\\P")
        else:
            e.dxf.text = new

    for layout in [doc.modelspace(), *doc.blocks]:
        for e in layout:
            kind = e.dxftype()
            if kind in ("TEXT", "ATTDEF", "MTEXT", "ATTRIB"):
                text_of(e)
                if kind == "ATTDEF" and e.dxf.get("prompt"):
                    e.dxf.prompt = privacy.scrub(e.dxf.prompt)
            elif kind == "INSERT":
                for a in e.attribs:
                    text_of(a)
            elif kind == "DIMENSION" and e.dxf.get("text", "") not in ("", "<>", " "):
                e.dxf.text = privacy.scrub(e.dxf.text)
    for layer in list(doc.layers):
        name = layer.dxf.name
        new = privacy.scrub_name(name)
        if new != name and new not in doc.layers:
            layer.rename(new)
    renamed = {}
    for block in list(doc.blocks):
        name = block.name
        if name.startswith("*"):
            continue
        new = privacy.scrub_name(name)
        if new != name and new not in doc.blocks:
            doc.blocks.rename_block(name, new)
            renamed[name] = new
    if renamed:
        for layout in [doc.modelspace(), *doc.blocks]:
            for e in layout.query("INSERT"):
                if e.dxf.name in renamed:
                    e.dxf.name = renamed[e.dxf.name]


def build(review, floor_id: str, area, *, note: str = "", keep=(), remove=(), models: dict | None = None,
          preview: bool = False, catalogue=None) -> Sample:
    """An area sample of a floor (``review``: the project's Review): ``area`` [x0, y0,
    x1, y1] in the floor's local metres, at most 50 × 50 m; ``keep``: findings the person
    keeps (ids, from a preview), ``remove``: other texts they take out; ``models``: the
    language, vision and symbols models this Studio uses (names, or None)."""
    area = area if isinstance(area, Area) else Area.of(area)
    if not isinstance(note, str):
        raise ValueError("note: a text")
    note = note.strip()[:NOTE_MAX]
    for name, value in (("keep", keep), ("remove", remove)):
        if not isinstance(value, (list, tuple, set)) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{name}: a list of the ids a preview gave")
    ws = review.workspace()
    f = review._floor(ws, floor_id)
    catalogue = catalogue if catalogue is not None else review.catalogue()
    doc, scale, info = _part(review, floor_id, area)
    frame = Frame(area)
    rooms, openings, items = in_area(ws, floor_id, frame)
    ids = LocalIds([*rooms, *openings], items, frame)
    profile = load_profile(review.source.profile(f.source))
    texts = texts_of(doc, scale)
    settings = {"profile": f.source.profile, "units": info["units"], "units_chosen": f.source.units,
                "header_units": info["header_units"], "m_per_unit": scale,
                "part_of_a_sheet": f.source.region is not None, "moved_onto_floor_below": f.source.offset is not None}
    reading = reading_of(ws, floor_id, frame, ids, rooms, openings, profile=profile, texts=texts,
                         layers_used=used_layers(doc), settings=settings)
    corrections = corrections_of(ws, floor_id, frame, ids, rooms, openings, items, catalogue)

    strings = dxf_strings(doc)
    for s in reading["spaces"]:
        strings += [s["name"], s["number"], s["label"], s["now"]["name"], s["now"]["number"]]
    for o in corrections["objects"]:
        strings += [o.get("name"), o.get("number")]
    privacy = Privacy([s for s in strings if s], names_and_codes(ws, floor_id), keep, remove)
    scrub_drawing(doc, privacy)
    for x in reading["layers"]["in_sample"]:  # named as drawing.dxf names them
        x["layer"] = privacy.scrub_name(x["layer"])
    for t in reading["texts"]:
        t["layer"] = privacy.scrub_name(t["layer"])
    for it in corrections["items"]:  # an organization's own type codes, without its names
        it["type"] = privacy.scrub_name(it["type"])
    reading = privacy.scrub_all(reading)
    corrections = privacy.scrub_all(corrections)
    note = privacy.scrub_note(note)
    drawing_png, reading_png = draw(doc, scale, reading, corrections, preview=preview)

    sample_id = new_id()
    out = io.StringIO()
    doc.write(out)
    used = sorted({m for t in reading["texts"] for m in [((t["read_as"].get("kept_answer") or {}).get("asked") or "")
                                                        .split("/")[0]] if m}
                  | {v["model"] for v in reading["vision"] if v.get("model")})
    corrected = sum(1 for o in corrections["objects"])
    drawn = sum(len(v) for v in corrections["drawn"].values())
    manifest = {
        "format": SAMPLE_FORMAT, "format_version": SAMPLE_VERSION, "sample_id": sample_id,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "studio": studio_version(),
        "models": models if models is not None else {"language": None, "vision": None, "symbols": None,
                                                       "said": "not known: made from the command line"},
        "answers_kept_from": used,
        "area": {"width_m": area.width, "height_m": area.height, "margin_m": MARGIN_M, "max_side_m": MAX_SIDE_M,
                 "origin": "the area's lower-left corner is (0, 0); the JSON files are in metres from it"},
        "drawing": {"file": "drawing.dxf", "units": info["units"], "m_per_unit": scale,
                    "header_units": info["header_units"], "units_chosen_by_a_person": f.source.units is not None,
                    "coordinates": f"drawing units ({info['units']}): a point (x, y) of drawing.dxf is "
                                   f"(x × {scale:g}, y × {scale:g}) m in the JSON files",
                    "dxf_version": info["dxf_version"],
                    "from_dwg": FROM_DWG if info["suffix"] != ".dwg" else "converted from a DWG for reading (dwg2dxf)",
                    "how_cut": HOW_CUT, "entities": len(doc.modelspace()),
                    "kept_whole": info["kept_whole"], "cut_at_edge": info["cut_at_edge"],
                    "blocks_taken_apart": info["blocks_taken_apart"], "left_out": info["left_out"]},
        "counts": {"spaces": sum(1 for s in reading["spaces"] if s["kind"] == "space"),
                   "zones": sum(1 for s in reading["spaces"] if s["kind"] == "zone"),
                   "openings": len(reading["openings"]), "texts": len(reading["texts"]),
                   "vision_answers": len(reading["vision"]), "corrections": corrected, "drawn": drawn,
                   "items": len(corrections["items"])},
        "privacy": {
            "coordinates": "moved: the area's lower-left corner is (0, 0); nothing says where the building is",
            "ids": "Studio's IDs replaced by the sample's own (S1, Z1, D1, W1, O1, I1, T1)",
            "names_and_codes": "the project's, site's, building's and floor's names and codes, and the site's "
                               "address, replaced by [PROJECT], [SITE], [BUILDING], [FLOOR], [ADDRESS]",
            "drawing_file": "a new DXF: no header data but its units, no file properties, layouts, paper space, "
                            "external references, images, embedded objects, extra data or hyperlinks",
            "texts": privacy.summary(),
            "placeholders": "[NAME] a person's name, [NAME?] a word that may be a name, [PHONE], [EXT], [EMAIL], "
                            "[WEB], [ID-NO], [CONTACT], [TEXT] a text the person took out",
        },
        "note": note,
        "files": FILES,
    }
    files = {
        "manifest.json": _json(manifest), "README.txt": README.format(id=sample_id).encode(),
        "drawing.dxf": out.getvalue().encode("utf-8"), "drawing.png": drawing_png, "reading.png": reading_png,
        "reading.json": _json(reading), "corrections.json": _json(corrections),
    }
    return Sample(sample_id, files, manifest, privacy, reading, corrections)


def _json(value) -> bytes:
    return json.dumps(value, indent=1, ensure_ascii=False).encode("utf-8")


def preview(review, floor_id: str, body: dict, models: dict | None = None, catalogue=None) -> dict:
    """What a sample of the area would hold, for the person to look at before it is
    made: its two pictures (small), what is taken out (each finding switchable) and the
    other texts (each may be taken out), and its counts."""
    s = build(review, floor_id, body.get("area"), note=body.get("note") or "", keep=body.get("keep") or [],
              remove=body.get("remove") or [], models=models, preview=True, catalogue=catalogue)
    image = lambda b: "data:image/png;base64," + base64.b64encode(b).decode()  # noqa: E731
    return {"area": s.manifest["area"], "counts": s.manifest["counts"], "drawing": s.manifest["drawing"],
            "privacy": s.privacy.view(), "images": {"drawing": image(s.files["drawing.png"]),
                                                    "reading": image(s.files["reading.png"])},
            "layers": [x["layer"] for x in s.reading["layers"]["in_sample"]]}


def make(review, floor_id: str, body: dict, models: dict | None = None, catalogue=None) -> Sample:
    """The sample itself, to download (Sample.zipped())."""
    return build(review, floor_id, body.get("area"), note=body.get("note") or "", keep=body.get("keep") or [],
                 remove=body.get("remove") or [], models=models, catalogue=catalogue)
