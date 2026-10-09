"""Reading an area sample someone sent: ``inspect`` says what is in it, ``replay``
reads its drawing with this Studio and compares what it finds with what Studio found
then and with what people corrected (docs/AREA-SAMPLES.md)."""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Point, box, shape

from .content import LocalIds, in_area, reading_of, texts_of
from .cut import used_layers
from .frame import Area, Frame
from .make import SAMPLE_FORMAT, SAMPLE_VERSION

MAX_FILE = 200 * 1024 * 1024  # a file in a sample is never this large: one that is, is not read
NEEDED = ("manifest.json", "reading.json", "corrections.json", "drawing.dxf")
MATCH_IOU = 0.5  # rooms overlapping this much (within the area) are the same room
DOOR_REACH_M = 0.5  # openings this close are the same
WEIGHTS = {"rooms": 0.5, "types": 0.3, "doors": 0.2}


class NotASample(ValueError):
    pass


@dataclass
class SampleFile:
    """An area sample, read: its manifest, reading and corrections, and its files' bytes."""

    path: Path
    files: dict[str, bytes]
    manifest: dict
    reading: dict
    corrections: dict

    @property
    def size(self) -> tuple[float, float]:
        return self.manifest["area"]["width_m"], self.manifest["area"]["height_m"]


def open_sample(path: str | Path) -> SampleFile:
    """A sample read from its file. Only the files a sample has are read (never written
    where the archive says), each at most MAX_FILE."""
    path = Path(path)
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise NotASample(f"{path} is not an area sample (not a ZIP file: {e})") from None
    files = {}
    with z:
        for info in z.infolist():
            name = info.filename
            if "/" in name or "\\" in name or name.startswith("."):
                continue  # a sample's files are at its top, named as make.FILES
            if info.file_size > MAX_FILE:
                raise NotASample(f"{name} in {path.name} is too large to be a sample's")
            files[name] = z.read(info)
    missing = [n for n in NEEDED if n not in files]
    if missing:
        raise NotASample(f"{path.name} is not an area sample: no {', '.join(missing)}")
    manifest = json.loads(files["manifest.json"])
    if manifest.get("format") != SAMPLE_FORMAT:
        raise NotASample(f"{path.name} is not an area sample (format {manifest.get('format')!r})")
    if manifest.get("format_version", 0) > SAMPLE_VERSION:
        raise NotASample(f"{path.name} is a sample of format {manifest['format_version']}: this Studio reads "
                         f"up to {SAMPLE_VERSION}; update it")
    return SampleFile(path, files, manifest, json.loads(files["reading.json"]), json.loads(files["corrections.json"]))


# ---- inspect ----------------------------------------------------------------------

def _room_words(s: dict) -> str:
    return " ".join(x for x in (s.get("name"), s.get("number")) if x) or "(no name)"


def inspect(path: str | Path) -> str:
    """A readable summary of a sample: what is in it, the note, counts, what Studio
    decided there and by whom, and the corrections as a diff of its reading."""
    s = open_sample(path)
    m, r, c = s.manifest, s.reading, s.corrections
    out = [f"Area sample {m['sample_id']} (format {m['format_version']}), made {m['created_at']}",
           f"  Studio {m['studio'].get('version')} commit {m['studio'].get('commit') or 'not known'}",
           "  models: " + ", ".join(f"{k} {v or 'none'}" for k, v in m["models"].items() if k != "said")
           + (f"; answers kept from {', '.join(m['answers_kept_from'])}" if m.get("answers_kept_from") else ""),
           f"  area {m['area']['width_m']:g} × {m['area']['height_m']:g} m (+{m['area']['margin_m']:g} m margin)",
           "", "Note: " + (m.get("note") or "(none)"), ""]
    st, d = r["settings"], m["drawing"]
    out += [f"Drawing: {d['entities']} entities ({d['kept_whole']} whole, {d['cut_at_edge']} cut at the edge, "
            f"{d['blocks_taken_apart']} blocks taken apart"
            + (f"; left out {', '.join(f'{n} {k}' for k, n in d['left_out'].items())}" if d["left_out"] else "") + ")",
            f"  units {st['units']} ({st['m_per_unit']:g} m a unit; the file says {st['header_units'] or 'none'}; "
            + ("chosen by a person" if st.get("units_chosen") else "worked out by Studio") + ")",
            f"  profile {st['profile']}; spaces found by {r['floor'].get('method')}; walls "
            f"{r['floor'].get('wall_thickness_m') or '?'} m thick"]
    for w in r["floor"].get("warnings") or []:
        out.append(f"  warning: {w}")
    out += ["", "Layers (what Studio read each as, on the floor):"]
    for x in r["layers"]["in_sample"]:
        out.append(f"  {x['layer']:<28} {x['entities']:>5}  {', '.join(x['read_as']) if x['read_as'] else '-'}")
    units = [x for x in r["spaces"] if not x["zones"]]
    out += ["", f"Rooms ({len(units)} spaces and zones; * at the edge of the area, read with what is beyond):"]
    for x in units:
        edge = "*" if x["at_edge"] else " "
        flags = (" SET ASIDE" if x["now"]["ignored"] else "") + (" hidden" if x["now"]["hidden"] else "")
        out.append(f" {edge}{x['id']:<4} {x['kind']:<5} {x['type']:<14} by {x['decided_by']:<16} "
                   f"{_room_words(x)}{flags}")
        for issue in x["issues"]:
            out.append(f"         - {issue}")
    by = {}
    for o in r["openings"]:
        by[(o["type"], o["found_as"])] = by.get((o["type"], o["found_as"]), 0) + 1
    out += ["", "Openings: " + (", ".join(f"{n} {t} ({how})" for (t, how), n in sorted(by.items())) or "none")]
    read = {}
    for t in r["texts"]:
        rules = t["read_as"].get("rules")
        kept = t["read_as"].get("kept_answer")
        how = ("rules: " + rules["type"]) if isinstance(rules, dict) else ("rules: not a room name" if rules else
                                                                            ("kept answer (" + kept["source"] + ")"
                                                                             if kept else "unknown"))
        read[how] = read.get(how, 0) + 1
    out += [f"Texts: {len(r['texts'])}: " + ", ".join(f"{n} {k}" for k, n in sorted(read.items(), key=lambda kv: -kv[1]))]
    if r["vision"]:
        out += ["", "Vision model's answers:"]
        for v in r["vision"]:
            if v["kind"] == "room":
                out.append(f"  {v['room'] or '(a shape no longer a room)'}: {v['outline']}; looks like {v['type']} "
                           f"({v['model']})")
            else:
                out.append(f"  line across {v['room'] or '?'}: A {v['a']}, B {v['b']} ({v['model']})")
    out += ["", "Corrections (Studio's reading → a person's):"]
    if not c["objects"]:
        out.append("  none")
    for o in c["objects"]:
        det = o["detected"]
        if o["accepted_as_is"]:
            out.append(f"  {o['id']}: accepted as read ({det['type']} {det['name'] or ''} {det['number'] or ''})")
            continue
        changes = [f"{k}: {det.get(k)!r} → {o[k]!r}" for k in ("type", "name", "number") if k in o]
        if "ignored" in o:
            changes.append("deleted" if o["ignored"] else "restored")
        changes += [f"{k}: {o[k]!r}" for k in ("hidden", "capacity", "floor_finish", "wall_finish") if k in o]
        out.append(f"  {o['id']}: " + "; ".join(changes))
    drawn = {k: len(v) for k, v in c["drawn"].items() if v}
    if drawn:
        out.append("  drawn in review: " + ", ".join(f"{n} {k}" for k, n in drawn.items()))
    if c["items"]:
        out.append(f"  {len(c['items'])} items placed: "
                   + ", ".join(sorted({f"{i['type']}" for i in c['items']})))
    p = m["privacy"]["texts"]
    out += ["", "Taken out for privacy: " + (", ".join(f"{n} {k}" for k, n in p["removed"].items()) or "nothing")
            + (f"; kept by the person: {', '.join(f'{n} {k}' for k, n in p['kept_by_person'].items())}"
               if p["kept_by_person"] else "")]
    return "\n".join(out) + "\n"


# ---- replay -----------------------------------------------------------------------

def _units(reading: dict, rect, corrected: bool) -> list[dict]:
    """The rooms of a reading (zones, and spaces with none) within the area: as Studio
    read them, or (``corrected``) as people corrected them."""
    out = []
    for s in reading["spaces"]:
        if s["zones"] or not s.get("geometry"):
            continue
        ignored = s["now"]["ignored"] if corrected else s.get("set_aside_by_vision", False)
        if ignored:
            continue
        g = shape(s["geometry"]).buffer(0).intersection(rect)
        if g.is_empty:
            continue
        out.append({"id": s["id"], "type": s["now"]["type"] if corrected else s["type"],
                    "name": s["now"]["name"] if corrected else s["name"], "geom": g, "at_edge": s["at_edge"]})
    return out


def _match(a: list[dict], b: list[dict]) -> list[tuple[dict, dict, float]]:
    """Rooms of two readings paired one to one, best overlap first."""
    pairs = []
    for x in a:
        for y in b:
            if x["geom"].intersects(y["geom"]):
                inter = x["geom"].intersection(y["geom"]).area
                union = x["geom"].area + y["geom"].area - inter
                score = inter / union if union > 0 else 0.0
                if score >= MATCH_IOU:
                    pairs.append((score, x["id"], y["id"], x, y))
    pairs.sort(key=lambda p: -p[0])
    used_a, used_b, out = set(), set(), []
    for score, ia, ib, x, y in pairs:
        if ia in used_a or ib in used_b:
            continue
        used_a.add(ia)
        used_b.add(ib)
        out.append((x, y, round(score, 3)))
    return out


def _openings(reading: dict, rect, corrected: bool) -> list[dict]:
    return [o for o in reading["openings"] if rect.covers(Point(*o["middle"])) and not (corrected and o["ignored"])]


def score(truth_reading: dict, reading: dict, rect) -> dict:
    """How a reading compares with the right answers (``truth_reading`` with its
    corrections): rooms found, missed and extra, types right and wrong, doors. Rooms at
    the edge of the area are listed but not scored."""
    truth = _units(truth_reading, rect, corrected=True)
    got = _units(reading, rect, corrected=False)
    pairs = _match(truth, got)
    inner = [t for t in truth if not t["at_edge"]]
    inner_ids = {t["id"] for t in inner}
    matched = [(t, g, s) for t, g, s in pairs if t["id"] in inner_ids]
    matched_got = {g["id"] for _, g, _ in pairs}
    missed = [t for t in inner if t["id"] not in {x["id"] for x, _, _ in pairs}]
    extra = [g for g in got if not g["at_edge"] and g["id"] not in matched_got]
    right = [(t, g) for t, g, _ in matched if t["type"] == g["type"]]
    wrong = [(t, g) for t, g, _ in matched if t["type"] != g["type"]]
    t_doors = _openings(truth_reading, rect, corrected=True)
    g_doors = _openings(reading, rect, corrected=False)
    used, door_pairs = set(), 0
    door_wrong = []
    for t in t_doors:
        near = [(Point(*t["middle"]).distance(Point(*g["middle"])), g) for g in g_doors if g["id"] not in used]
        near = [x for x in near if x[0] <= DOOR_REACH_M]
        if near:
            _, g = min(near, key=lambda x: x[0])
            used.add(g["id"])
            door_pairs += 1
            if g["type"] != t["type"]:
                door_wrong.append({"right": t["id"], "found": g["id"], "was": t["type"], "now": g["type"]})
    f1 = lambda hit, a, b: (2 * hit / (a + b)) if a + b else 1.0  # noqa: E731
    rooms_f1 = f1(len(matched), len(inner), len(matched) + len(extra))
    types_share = len(right) / len(matched) if matched else (1.0 if not inner else 0.0)
    doors_f1 = f1(door_pairs - len(door_wrong), len(t_doors), len(g_doors))
    total = round(100 * (WEIGHTS["rooms"] * rooms_f1 + WEIGHTS["types"] * types_share + WEIGHTS["doors"] * doors_f1))
    return {
        "score": total, "rooms_f1": round(rooms_f1, 3), "types_right_share": round(types_share, 3),
        "doors_f1": round(doors_f1, 3),
        "rooms": {"right_answers": len(inner), "found": len(matched), "missed": [t["id"] for t in missed],
                  "extra": [g["id"] for g in extra],
                  "pairs": [{"right": t["id"], "found": g["id"], "iou": s} for t, g, s in matched],
                  "at_edge_not_scored": [t["id"] for t in truth if t["at_edge"]]},
        "types": {"right": len(right),
                  "wrong": [{"room": t["id"], "found": g["id"], "right": t["type"], "read": g["type"],
                             "name": t["name"]} for t, g in wrong]},
        "doors": {"right_answers": len(t_doors), "found": len(g_doors), "matched": door_pairs, "wrong_type": door_wrong},
    }


def replay(path: str | Path, out: str | Path | None = None, *, model=None, vision=None, fresh: bool = False,
           edits: bool = True, units: str | None = None, say=print) -> dict:
    """Read a sample's drawing with this Studio (the same profile and units; the language
    and vision models given, when they answer; the answers kept in the sample, unless
    ``fresh``; what people drew, unless not ``edits``), and compare: Studio then and Studio
    now, each against the right answers (the reading with its corrections). With ``out``,
    write there replay.json, replay.png, the sample's two pictures and side-by-side.png."""
    import ezdxf

    from ..convert import convert_floor
    from ..profile import builtin_profiles, load_profile
    from ..workspace import DrawnOpening, FloorEdits, Reading, ResizedOpening, SourceDrawing, Workspace
    from .picture import draw

    s = open_sample(path)
    st = s.reading["settings"]
    w, h = s.size
    profile = st["profile"]
    notes = []
    if profile not in ("auto", *builtin_profiles()):
        notes.append(f"the sample was read with its project's own profile {profile!r}: replayed with auto")
        profile = "auto"
    use_units = st["units"] if units is None else (None if units == "auto" else units)
    with tempfile.TemporaryDirectory(prefix="storeypath-replay-") as tmp:
        tmp = Path(tmp)
        (tmp / "drawing.dxf").write_bytes(s.files["drawing.dxf"])
        ws = Workspace.new("Area sample replay")
        loc = ws.add_location("SAMPLE", "Sample")
        b = ws.add_building(loc, "AREA", "Area")
        fid = ws.add_floor(b, 0, source=SourceDrawing(path="drawing.dxf", profile=profile, units=use_units))
        d = s.corrections["drawn"]
        if edits:
            ws.floor(fid).edits = FloorEdits(
                walls=d.get("walls", []), dividers=d.get("dividers", []), spaces=d.get("spaces", []),
                openings=[DrawnOpening(**o) for o in d.get("openings", [])],
                resized=[ResizedOpening(**x) for x in d.get("resized", [])])
        kept_answers = 0
        if not fresh:
            kept_answers = _kept_answers(s.reading, ws, Reading)
        llm = model if model is not None and model.available() else None
        eye = vision if vision is not None and vision.available() else None
        said = {"language model": llm.name if llm else None, "vision model": eye.name if eye else None,
                "answers kept in the sample": kept_answers, "drawn edits": edits, "units": use_units or "worked out",
                "profile": profile}
        say("replaying with " + ("the language model " + llm.name if llm else "no language model")
            + ", " + ("the vision model " + eye.name if eye else "no vision model")
            + (f", {kept_answers} answers kept in the sample" if kept_answers else ", no kept answers")
            + ("" if edits else ", without what people drew") + (f", units {use_units}" if use_units else ""))
        for n in notes:
            say(n)
        report = convert_floor(ws, fid, tmp, llm, None, eye, say=lambda m: say(f"  {m}"), force=True)
        doc = ezdxf.readfile(tmp / "drawing.dxf")
        from ..cad import meters_per_unit

        scale = meters_per_unit(doc, use_units)
        frame = Frame(Area(0.0, 0.0, w, h), margin=s.reading["frame"]["area"][0] - s.reading["frame"]["extent"][0])
        rooms, openings, items = in_area(ws, fid, frame)
        ids = LocalIds([*rooms, *openings], items, frame)
        settings = {**st, "replayed_with": said}
        now = reading_of(ws, fid, frame, ids, rooms, openings, profile=load_profile(profile),
                         texts=texts_of(doc, scale), layers_used=used_layers(doc), settings=settings)
        now["floor"]["warnings"] = list(report.warnings)
        rect = box(0, 0, w, h)
        then_score = score(s.reading, s.reading, rect)
        now_score = score(s.reading, now, rect)
        changed = _changes(s.reading, now, rect)
        result = {"sample": s.manifest["sample_id"], "replayed_with": said, "notes": notes,
                  "studio_then": then_score, "studio_now": now_score, "then_vs_now": changed,
                  "reading": now}
        if out is not None:
            out = Path(out)
            out.mkdir(parents=True, exist_ok=True)
            _, replay_png = draw(doc, scale, now, s.corrections, title="Replay: this Studio's reading")
            (out / "replay.png").write_bytes(replay_png)
            for name in ("drawing.png", "reading.png"):
                if name in s.files:
                    (out / name).write_bytes(s.files[name])
            (out / "replay.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
            if "reading.png" in s.files:
                (out / "side-by-side.png").write_bytes(_side_by_side(s.files["reading.png"], replay_png))
        return result


def _kept_answers(reading: dict, ws, Reading) -> int:
    """The models' answers the sample keeps (texts, and rooms' and lines' shapes), put
    back for the replay: without the models, it reads as Studio did with them."""
    n = 0

    def put(text, ans):
        nonlocal n
        if not text or not ans or text in ws.readings:
            return
        try:
            ws.readings[text] = Reading(type=ans.get("type"), source=ans.get("source", "model"),
                                        rooms_only=ans.get("rooms_only", False), asked=ans.get("asked"))
            n += 1
        except ValueError:
            pass

    for t in reading["texts"]:
        put(t["text"], t["read_as"].get("kept_answer"))
        for line, ra in (t.get("lines_read_as") or {}).items():
            put(line, ra.get("kept_answer"))
    for s in reading["spaces"]:
        if s.get("name") and s.get("name_read_as"):
            put(s["name"], s["name_read_as"].get("kept_answer"))
    for i, v in enumerate(reading.get("vision", [])):
        if v["kind"] == "room" and v.get("shape_wkt"):
            ws.vision[f"sample-{i}"] = {"outline": v["outline"], "type": v["type"], "model": v["model"],
                                        "shape": v["shape_wkt"]}
            n += 1
        elif v.get("cut_wkt"):
            ws.vision[f"sample-{i}"] = {"a": v["a"], "b": v["b"], "model": v["model"], "cut": v["cut_wkt"]}
            n += 1
    return n


def _changes(then: dict, now: dict, rect) -> dict:
    """What the replay reads otherwise than Studio did then (before any correction)."""
    a, b = _units(then, rect, corrected=False), _units(now, rect, corrected=False)
    pairs = _match(a, b)
    paired_a, paired_b = {x["id"] for x, _, _ in pairs}, {y["id"] for _, y, _ in pairs}
    edge = lambda x: x["id"] + ("*" if x["at_edge"] else "")  # noqa: E731 (* at the edge of the area)
    return {"rooms_then": len(a), "rooms_now": len(b),
            "only_then": [edge(x) for x in a if x["id"] not in paired_a],
            "only_now": [edge(y) for y in b if y["id"] not in paired_b],
            "type_changed": [{"then": x["id"], "now": y["id"], "was": x["type"], "is": y["type"]}
                             for x, y, _ in pairs if x["type"] != y["type"]]}


def _side_by_side(left: bytes, right: bytes) -> bytes:
    from PIL import Image

    a, b = Image.open(io.BytesIO(left)).convert("RGB"), Image.open(io.BytesIO(right)).convert("RGB")
    height = max(a.height, b.height)
    out = Image.new("RGB", (a.width + b.width + 20, height), "white")
    out.paste(a, (0, 0))
    out.paste(b, (a.width + 20, 0))
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def summary(result: dict) -> str:
    """A replay's comparison, in words."""
    then, now = result["studio_then"], result["studio_now"]
    lines = [f"Replay of sample {result['sample']}",
             "  with " + ", ".join(f"{k}: {v}" for k, v in result["replayed_with"].items()), ""]
    for n in result.get("notes", []):
        lines.append(f"  note: {n}")
    lines += [f"Score against the right answers (corrections): Studio then {then['score']}, this Studio "
              f"{now['score']}",
              f"  rooms: {now['rooms']['found']} of {now['rooms']['right_answers']} found (then "
              f"{then['rooms']['found']}); missed {', '.join(now['rooms']['missed']) or 'none'}; extra "
              f"{', '.join(now['rooms']['extra']) or 'none'}"
              + (f"; at the edge, not scored: {', '.join(now['rooms']['at_edge_not_scored'])}"
                 if now["rooms"]["at_edge_not_scored"] else ""),
              f"  types: {now['types']['right']} right, {len(now['types']['wrong'])} wrong (then "
              f"{then['types']['right']} right, {len(then['types']['wrong'])} wrong)"]
    for x in now["types"]["wrong"]:
        lines.append(f"    {x['room']} ({x['name'] or 'no name'}): read {x['read']}, right {x['right']} "
                     f"(replay's {x['found']})")
    lines.append(f"  doors, windows, openings: {now['doors']['matched']} of {now['doors']['right_answers']} found, "
                 f"{now['doors']['found']} in all"
                 + (f", {len(now['doors']['wrong_type'])} of another kind" if now["doors"]["wrong_type"] else ""))
    c = result["then_vs_now"]
    lines += ["", f"This Studio against Studio then: {c['rooms_then']} rooms then, {c['rooms_now']} now"
              + (f"; only then: {', '.join(c['only_then'])}" if c["only_then"] else "")
              + (f"; only now (replay IDs): {', '.join(c['only_now'])}" if c["only_now"] else "")]
    for x in c["type_changed"]:
        lines.append(f"  {x['then']}: {x['was']} then, {x['is']} now ({x['now']})")
    for w in result["reading"]["floor"].get("warnings") or []:
        lines.append(f"  warning: {w}")
    return "\n".join(lines) + "\n"


def extract(path: str | Path, folder: str | Path) -> list[Path]:
    """A sample's files written into ``folder`` (only its own, by their own names)."""
    s = open_sample(path)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in s.files.items():
        target = folder / Path(name).name
        target.write_bytes(data)
        written.append(target)
    return written


__all__ = ["open_sample", "inspect", "replay", "summary", "score", "extract", "NotASample"]
