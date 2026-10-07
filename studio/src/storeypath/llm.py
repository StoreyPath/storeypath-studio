"""The local language model: reading the text in drawings.

Studio runs a small model (a GGUF file) with llama.cpp's ``llama-server``, on the
CPU, without any network. It is only asked short, closed questions — which of our
space types does this room label name, which floor does this sheet title show —
and its answer is held to a JSON schema built from fixed lists, so it can suggest
but never invent: every answer is one a person can check in review.

Configuration, from the environment:

``STOREYPATH_MODEL``         the .gguf model (default: the newest in the model folder)
``STOREYPATH_MODELS``        the model folder (default: /opt/storeypath/models)
``STOREYPATH_LLAMA_SERVER``  the llama-server program (default: found on PATH)
``STOREYPATH_MODEL_URL``     an already running llama-server to use instead
``STOREYPATH_THREADS``       CPU threads for the model (default: all)
``STOREYPATH_PARALLEL``      questions answered at once (default: 1; more needs more memory)
``STOREYPATH_GPU_LAYERS``    layers to put on the GPU, with a GPU build of llama-server (e.g. 99: all)

Without a model Studio works on its rules alone.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .types import SpaceType

DEFAULT_MODELS = Path("/opt/storeypath/models")
BATCH = 24  # labels per question
START_TIMEOUT_S = 180


class ModelUnavailable(Exception):
    pass


class BadAnswer(ModelUnavailable):
    """The model answered, but not with what was asked: a reply with no answer in it
    (content null, as when a reasoning model spends its tokens thinking), or one
    that is not a JSON object. One question is lost, not the model."""


def reply_answer(reply) -> dict:
    """The JSON object an OpenAI-compatible chat reply answers with. BadAnswer when
    it holds none."""
    try:
        content = reply["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise BadAnswer(f"the reply holds no answer ({type(e).__name__}: {e})") from e
    if not isinstance(content, str):
        raise BadAnswer("the reply holds no answer (none given: the model may have used its tokens up thinking)")
    try:
        answer = json.loads(content)
    except ValueError as e:
        raise BadAnswer(f"the answer is not JSON: {content[:80]!r}") from e
    if not isinstance(answer, dict):
        raise BadAnswer(f"the answer is not a JSON object: {content[:80]!r}")
    return answer


def _items(answer: dict, key: str) -> list[dict]:
    """The objects of a list in an answer, leaving out anything else."""
    items = answer.get(key)
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


class LocalModel:
    """A llama-server process (started on first use) or an existing server."""

    def __init__(self, url: str | None = None, model: str | Path | None = None, threads: int | None = None):
        self.url = url or os.environ.get("STOREYPATH_MODEL_URL")
        self.model = Path(model) if model else _find_model()
        self.threads = threads or int(os.environ.get("STOREYPATH_THREADS", "0")) or os.cpu_count() or 4
        self.parallel = max(1, int(os.environ.get("STOREYPATH_PARALLEL", "1")))
        self._proc: subprocess.Popen | None = None
        self._log: deque[str] = deque(maxlen=40)  # the server's last log lines, for errors
        self._log_reader: threading.Thread | None = None
        self._warmed = bool(self.url)
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        if self.model is not None:
            return self.model.stem
        return "remote model" if self.url else "none"

    @property
    def ready(self) -> bool:
        """Whether the model is loaded and answering."""
        return bool(self.url) and (self._proc is None or self._proc.poll() is None) and self._warmed

    def available(self) -> bool:
        return bool(self.url) or (self.model is not None and _server_program() is not None)

    def _ensure_started(self) -> str:
        with self._lock:
            if self.url and (self._proc is None or self._proc.poll() is None):
                return self.url
            program = _server_program()
            if self.model is None or program is None:
                raise ModelUnavailable("no language model: set STOREYPATH_MODEL and install llama-server")
            port = _free_port()
            # Several questions at once: on a CPU, answering four together costs little
            # more than answering one. Each gets 4096 tokens of context.
            args = [program, "-m", str(self.model), "--host", "127.0.0.1", "--port", str(port),
                    "-c", str(4096 * self.parallel), "-t", str(self.threads), "--jinja", "--no-webui",
                    "-np", str(self.parallel),
                    # Repacking copies the weights for faster maths: ~2.6 GB more memory for the
                    # 4B model, and no measurable gain on Studio's short questions.
                    "--no-repack"]
            if layers := os.environ.get("STOREYPATH_GPU_LAYERS"):
                args += ["-ngl", layers]
            self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                          errors="replace")
            # Its log is read as it is written: a pipe nobody reads fills up (64 KB) and
            # llama-server stops at its next log line, in the middle of a question.
            self._log.clear()
            self._log_reader = threading.Thread(target=self._log.extend, args=(self._proc.stderr,), daemon=True)
            self._log_reader.start()
            atexit.register(self.close)
            self.url = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    self._log_reader.join(timeout=2)
                    err = "".join(self._log)[-800:]
                    self._proc, self.url = None, None
                    raise ModelUnavailable(f"llama-server stopped: {err}")
                try:
                    with urllib.request.urlopen(f"{self.url}/health", timeout=2) as r:
                        if r.status == 200:
                            return self.url
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(0.25)
            self.close()
            raise ModelUnavailable("llama-server did not start in time")

    def warm(self) -> None:
        """Start the server and answer one question, so the weights are in memory."""
        try:
            self.ask("Answer in JSON.", "Say ok.", {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                                    "required": ["ok"]}, max_tokens=10)
            self._warmed = True
        except ModelUnavailable:
            pass

    def close(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc, self.url = None, os.environ.get("STOREYPATH_MODEL_URL")

    def ask(self, system: str, user: str, schema: dict, max_tokens: int = 1024) -> dict:
        """One question; the answer is JSON that fits ``schema``."""
        url = self._ensure_started()
        body = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0, "top_k": 1, "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(f"{url}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                reply = json.load(r)
        except (urllib.error.URLError, OSError) as e:
            raise ModelUnavailable(f"the language model did not answer: {e}") from e
        except ValueError as e:
            raise BadAnswer(f"the language model's reply is not JSON: {e}") from e
        answer = reply_answer(reply)
        self._warmed = True
        return answer


def _find_model() -> Path | None:
    if os.environ.get("STOREYPATH_MODEL"):
        return Path(os.environ["STOREYPATH_MODEL"])
    folder = Path(os.environ.get("STOREYPATH_MODELS", DEFAULT_MODELS))
    models = sorted(p for p in folder.glob("*.gguf") if "mmproj" not in p.name) if folder.is_dir() else []
    return models[-1] if models else None


def _server_program() -> str | None:
    return os.environ.get("STOREYPATH_LLAMA_SERVER") or shutil.which("llama-server")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- questions ----------------------------------------------------------------

# Specific types first, the catch-all "room" last: small models pick the first
# broad match. Examples in several languages, none of them from eval/labels.tsv.
TYPE_GUIDE = {
    "bedroom": "bedroom, master or guest bedroom, kids' room, maid's or driver's room (غرفة نوم, chambre, Schlafzimmer, habitación)",
    "living_room": "sitting room, living room, family room, lounge, majlis, salon, sala (also written salah or saleh), TV room (صالة, مجلس, séjour)",
    "dining_room": "dining room, dining area (طعام, salle à manger, Esszimmer)",
    "kitchen": "kitchen, pantry, kitchenette, coffee point (مطبخ, cuisine, cocina)",
    "bathroom": "private bathroom with a bath or shower, en-suite (حمام, salle d'eau, baño)",
    "restroom": "toilet, WC, washroom, hand-wash room, powder room (دورة مياه, toilettes, aseo)",
    "dressing_room": "dressing room, walk-in closet, wardrobe room (ملابس, dressing)",
    "laundry": "laundry: a room for washing and ironing clothes (غسيل, buanderie, lavandería)",
    "prayer_room": "prayer room, musalla (مصلى)",
    "office": "office, manager's or director's room (مكتب, bureau, despacho)",
    "meeting_room": "meeting, conference, board or huddle room (اجتماعات, réunion, reunión)",
    "open_area": "open-plan office, open work area with workstations",
    "lobby": "lobby, reception, foyer, entrance hall, landing, waiting area (استقبال, مدخل, accueil)",
    "corridor": "corridor, passage, hallway (ممر, couloir, pasillo)",
    "stairs": "stairs, staircase, stairwell (درج, سلم, escalier, escalera)",
    "elevator": "lift or elevator (مصعد, ascenseur, ascensor)",
    "escalator": "escalator",
    "ramp": "ramp",
    "storage": "store, storeroom, storage, archive (مخزن, rangement, almacén)",
    "utility": "electrical, mechanical, server, plant, pump, janitor or other technical room; DB room (electrical distribution board) (كهرباء, technique)",
    "shaft": "shaft, riser, duct",
    "parking": "garage, carport, parking (موقف, garage, aparcamiento)",
    "balcony": "balcony (شرفة, بلكونة, balcon)",
    "terrace": "terrace, roof deck, veranda, porch, courtyard, patio (سطح, فناء, terrasse)",
    "open_to_below": "void, open to below, double-height space over the floor below (فراغ, vide)",
    "room": "any other room that none of the types above fits: study, playroom, gym, classroom, library, a room with only a number",
}
assert set(TYPE_GUIDE) == {t.value for t in SpaceType} - {"unspecified"}

NOT_A_ROOM = "not_a_room"
LABEL_SYSTEM = (
    "You read the text written on architectural floor plans. The text can be in any "
    "language, abbreviated, misspelt or in capitals. For each text choose the type of "
    "room or area it names:\n"
    + "\n".join(f"- {k}: {v}" for k, v in TYPE_GUIDE.items())
    + f"\n- {NOT_A_ROOM}: the text does not name a room or area: a level or height "
    "(+0.45 FFL, ±0.00), a door or window tag (D1, W4), a direction (UP, DN), equipment "
    "or furniture (A/C SPLIT UNIT), a note, a dimension or a drawing title.\n"
    "Drawings abbreviate: RM room, CORR corridor, OFF office, CONF conference, ELEV "
    "elevator, EE or ELEC electrical, MECH mechanical, STR or STO store, M. master, F. "
    "family, H. hand, WC toilet, SAC or A/C a split air-conditioner on the wall (equipment, "
    "not a room); a number after a name is the room number.\n"
    "Answer in compact JSON."
)


# Changes when the question does: answers to an older question are asked again.
LABEL_QUESTION = hashlib.sha1(LABEL_SYSTEM.encode()).hexdigest()[:8]


@dataclass
class LabelReading:
    is_space: bool
    type: SpaceType | None


def read_labels(model: LocalModel, texts: list[str], rooms_only: bool = False) -> dict[str, LabelReading]:
    """Which texts are room names, and of which type. With ``rooms_only`` the texts
    are known to name rooms (labels inside spaces) and only the type is asked.

    One text per question: small models answering a list tend to repeat the
    previous answer. The instructions are the same every time, so the server keeps
    them cached and each question is quick."""
    choices = [*TYPE_GUIDE] if rooms_only else [*TYPE_GUIDE, NOT_A_ROOM]
    schema = {"type": "object", "properties": {"type": {"enum": choices}}, "required": ["type"]}

    def one(text: str) -> tuple[str, LabelReading | None]:
        try:
            kind = model.ask(LABEL_SYSTEM, f"Text: {text}", schema, max_tokens=30).get("type")
        except BadAnswer:
            return text, None  # this text is asked again next time
        is_space = isinstance(kind, str) and kind in TYPE_GUIDE
        return text, LabelReading(is_space, SpaceType(kind) if is_space else None)

    unique = list(dict.fromkeys(t.strip() for t in texts if t.strip()))
    with ThreadPoolExecutor(max_workers=getattr(model, "parallel", 1)) as pool:
        return {text: reading for text, reading in pool.map(one, unique) if reading is not None}


VIEW_KINDS = ["floor_plan", "site_plan", "roof_plan", "elevation", "section", "detail", "schedule", "other"]
TITLE_SYSTEM = (
    "You read the titles of drawings on architectural sheets, in any language. For each "
    "title say what kind of drawing it is:\n"
    "- floor_plan: the plan of one floor seen from above (plan, layout, floor; المسقط, "
    "مسقط, rez-de-chaussée, étage, Grundriss, Geschoss, planta), including a plan of a "
    "small building or an enlarged plan of part of one\n"
    "- roof_plan: the plan of a roof or roof deck\n"
    "- site_plan: a site, location or development plan of the plot\n"
    "- elevation: a facade seen from the side (واجهة, façade, Ansicht, alzado)\n"
    "- section: a cut through the building (قطاع, coupe, Schnitt, sección)\n"
    "- detail: a construction detail that is not a plan\n"
    "- schedule: a table of doors, windows or finishes\n"
    "- other: anything else\n"
    "For a floor plan also give the floor as a number: basement -1 (a second basement -2), ground floor 0 "
    "(rez-de-chaussée, Erdgeschoss, planta baja, الأرضي), first floor 1 (الأول), second 2, "
    "and so on; null when the title does not say. A penthouse is above the numbered "
    "floors: null, unless its title gives a number. Give the name of the building when the "
    "title names one other than the main building (an annex, guard room, outbuilding), "
    "otherwise null. Answer in compact JSON."
)


@dataclass
class TitleReading:
    kind: str
    floor: int | None
    building: str | None


def read_titles(model: LocalModel, titles: list[str]) -> dict[str, TitleReading]:
    unique = list(dict.fromkeys(t.strip() for t in titles if t and t.strip()))
    if not unique:
        return {}
    schema = {
        "type": "object",
        "properties": {"titles": {"type": "array", "minItems": len(unique), "maxItems": len(unique), "items": {
            "type": "object",
            "properties": {
                "n": {"type": "integer"},
                "kind": {"enum": VIEW_KINDS},
                "floor": {"type": ["integer", "null"]},
                "building": {"type": ["string", "null"]},
            },
            "required": ["n", "kind", "floor", "building"],
        }}},
        "required": ["titles"],
    }
    user = "Titles:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(unique, 1))
    answer = model.ask(TITLE_SYSTEM, user, schema, max_tokens=80 * len(unique) + 100)
    out = {}
    for item in _items(answer, "titles"):
        i = item.get("n", 0) - 1 if isinstance(item.get("n"), int) else -1
        if 0 <= i < len(unique):
            floor, kind, building = item.get("floor"), item.get("kind"), item.get("building")
            out[unique[i]] = TitleReading(kind if kind in VIEW_KINDS else "other",
                                          floor if isinstance(floor, int) else None,
                                          building if isinstance(building, str) and building else None)
    return out


LAYER_ROLES = ["wall", "column", "door", "window", "room_name", "room_outline", "stairs", "furniture",
               "fixtures", "dimension", "grid", "annotation", "hatch", "other"]
LAYER_SYSTEM = (
    "You read CAD layer names from architectural drawings (any language, often "
    "abbreviated, e.g. US National CAD Standard names like A-WALL or A-DOOR-IDEN). For "
    "each name say what the layer most likely holds: " + ", ".join(LAYER_ROLES) + ". Use "
    "other when the name gives no clue. Answer in compact JSON."
)


def read_layer_names(model: LocalModel, names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    unique = list(dict.fromkeys(names))
    for start in range(0, len(unique), BATCH * 2):
        batch = unique[start:start + BATCH * 2]
        schema = {
            "type": "object",
            "properties": {"layers": {"type": "array", "minItems": len(batch), "maxItems": len(batch), "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "role": {"enum": LAYER_ROLES}},
                "required": ["n", "role"],
            }}},
            "required": ["layers"],
        }
        user = "Layers:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(batch, 1))
        answer = model.ask(LAYER_SYSTEM, user, schema, max_tokens=40 * len(batch) + 100)
        for item in _items(answer, "layers"):
            i = item.get("n", 0) - 1 if isinstance(item.get("n"), int) else -1
            if 0 <= i < len(batch):
                out[batch[i]] = item.get("role") if item.get("role") in LAYER_ROLES else "other"
    return out


# Spelled out: small models mistake "in" (inches) for the word.
UNIT_CHOICES = {"millimetres": "mm", "centimetres": "cm", "metres": "m", "inches": "in", "feet": "ft"}
UNIT_NOTE_SYSTEM = (
    "You read notes written on architectural drawings, in any language. Say whether the "
    "note states the unit that all the drawing's dimensions or measurements are given in, "
    "and which unit. The size or thickness of one thing (20mm TILES, 150 mm SLAB, سماكة 20 "
    "مم) does not state it: answer none. Answer in compact JSON."
)


def read_unit_notes(model: LocalModel, notes: list[str]) -> dict[str, str | None]:
    """Which notes state the units the drawing's dimensions are in ("ALL DIMENSIONS
    ARE IN MM", in any language): the unit (mm, cm, m, in, ft), or None. One note per
    question, as for room labels."""
    schema = {"type": "object", "properties": {"units": {"enum": [*UNIT_CHOICES, "none"]}}, "required": ["units"]}
    out = {}
    for note in dict.fromkeys(n.strip() for n in notes if n.strip()):
        answer = model.ask(UNIT_NOTE_SYSTEM, f"Note: {note}", schema, max_tokens=30).get("units")
        out[note] = UNIT_CHOICES.get(answer) if isinstance(answer, str) else None
    return out



LEVEL_KINDS = ["floor", "roof", "top", "parapet", "ground", "other"]
LEVEL_SYSTEM = (
    "You read level labels on architectural sections and elevations, in any language: "
    "the words after a level in metres. Say what level they mark:\n"
    "- floor: the slab or finished floor of a floor; give its number: basement -1, ground "
    "floor 0, first floor 1, second floor 2, and so on\n"
    "- roof: the main roof slab, above the highest floor\n"
    "- top: the roof of a stair room, lift room or penthouse that stands on the roof\n"
    "- parapet: the top of the parapet wall around the roof (دروة, سترة, acrotère, Attika)\n"
    "- ground: the natural ground, road or site level outside (not the ground floor)\n"
    "- other: anything else: a window, a lintel, a beam, a tank, or a level that names "
    "no floor\n"
    "Sections abbreviate: FFL, SSL, OKFF, OKRD and NIV are levels of a floor; RDC, EG, PB "
    "and الأرضي are the ground floor; R+1, 1. OG and الأول the first floor; KG, UG and "
    "SS a basement.\n"
    "Answer in compact JSON."
)


def read_level_labels(model: LocalModel, labels: list[str]) -> dict[str, tuple[str, int | None]]:
    """What level each label marks: (kind, floor number for "floor"). One label per
    question, as for room labels."""
    schema = {"type": "object", "properties": {"marks": {"enum": LEVEL_KINDS}, "floor": {"type": ["integer", "null"]}},
              "required": ["marks", "floor"]}
    out = {}
    for label in dict.fromkeys(t.strip() for t in labels if t.strip()):
        answer = model.ask(LEVEL_SYSTEM, f"Label: {label}", schema, max_tokens=30)
        kind = answer.get("marks") if answer.get("marks") in LEVEL_KINDS else "other"
        floor = answer.get("floor")
        out[label] = (kind, floor if isinstance(floor, int) else None)
    return out

SCHEDULE_SYSTEM = (
    "You read one row of a door and window schedule from an architectural drawing: its cells, each with the "
    "header of the column it stands in. Answer with the opening's width, height and sill_height (how high above "
    "the floor the window starts), as numbers in the table's own units, or null when the row has none. "
    "Read it as a person would. The sill height is usually under SILL, but if a number that can only be a sill "
    "height (hundreds of millimetres, or more than 0.3 m) stands under a column that cannot hold it, such as a "
    "count of leaves, it is the sill height. A count of leaves (1, 2, 4) is never a size. A height written in two "
    "parts such as '1200+' and 'R=600' (an arched top) is the two added. A dash is null."
)


def read_schedule_row(model: LocalModel, headers: list[str], tag: str, cells: list[tuple[str, str]]) -> dict:
    """An opening type's width, height and sill as the schedule's row gives them (in
    the table's units, None where it gives none)."""
    schema = {"type": "object", "properties": {k: {"type": ["number", "null"]} for k in ("width", "height", "sill_height")},
              "required": ["width", "height", "sill_height"]}
    row = "; ".join(f"{h}: {c}" for h, c in cells)
    got = model.ask(SCHEDULE_SYSTEM, f"Headers: {' | '.join(headers)}\nRow {tag} — {row}", schema, max_tokens=60)
    return {"width": got.get("width"), "height": got.get("height"), "sill": got.get("sill_height")}


PRIVATE_KINDS = ["person", "company", "contact", "address", "id number", "other"]
PRIVATE_BATCH = 30  # texts per question
PRIVATE_SYSTEM = (
    "You check texts taken from an architectural drawing of a building for private information before the "
    "drawing is shared. Private: a person's name (with or without a title), a company, consultancy, contractor "
    "or developer name, a phone, fax or extension number, an email or web address, a street address, a plot, "
    "permit, licence, registration or ID number, a signature. Not private: room and space names (even 'DR. OFFICE' "
    "or 'MANAGER'), floor and drawing titles, building parts, materials, notes, dimensions, levels, grid labels, "
    "door and window tags, and words like CLIENT, OWNER or CONSULTANT on their own. For each private text give its "
    "number and the private part exactly as written in it."
)


def find_private(model: LocalModel, texts: list[str], say=None) -> dict[str, list[tuple[str, str]]]:
    """The private parts of texts from a drawing, as the model reads them: text →
    [(the part, as written in it; its kind)]. A part not found in its text is not
    taken: the model must point at it."""
    schema = {"type": "object", "properties": {"private": {"type": "array", "items": {
        "type": "object", "properties": {"n": {"type": "integer"}, "part": {"type": "string"},
                                         "kind": {"enum": PRIVATE_KINDS}},
        "required": ["n", "part", "kind"]}}}, "required": ["private"]}
    out: dict[str, list[tuple[str, str]]] = {}
    for start in range(0, len(texts), PRIVATE_BATCH):
        batch = texts[start:start + PRIVATE_BATCH]
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(batch))
        answer = model.ask(PRIVATE_SYSTEM, f"Texts:\n{numbered}", schema, max_tokens=60 + 30 * len(batch))
        for p in _items(answer, "private"):
            n, part = p.get("n"), (p.get("part") if isinstance(p.get("part"), str) else "").strip()
            if isinstance(n, int) and 1 <= n <= len(batch) and len(part) >= 2 and part.lower() in batch[n - 1].lower():
                out.setdefault(batch[n - 1], []).append((part, p.get("kind") if p.get("kind") in PRIVATE_KINDS else "other"))
        if say:
            say(f"read {min(start + PRIVATE_BATCH, len(texts))} of {len(texts)} texts for private information")
    return out


_NOT_WORDS = re.compile(r"^[\W\d_]*$")


def worth_reading(text: str) -> bool:
    """Texts the model is worth asking about: with letters in them."""
    return not _NOT_WORDS.match(text)
