"""Looking at the plan the way a person reads the print: a vision model is shown each
room Studio found, outlined in red on the drawing, and says whether it is really one
room and what kind. Code keeps the exact geometry and IDs; the model answers the
judgement calls (a garden or a sheet frame is not a room; a bed makes a bedroom).

The model is any OpenAI-compatible chat endpoint that takes images: llama.cpp's
llama-server or vLLM on a GPU, or a hosted service.

    STOREYPATH_VISION_URL    e.g. http://127.0.0.1:8105/v1 (none: no vision)
    STOREYPATH_VISION_MODEL  model name, when the server serves several
    STOREYPATH_VISION_KEY    bearer token, for hosted services
    STOREYPATH_VISION_PARALLEL  questions in flight at once (default 2)

Rendering needs matplotlib and Pillow (`uv sync --extra vision`). Answers are kept in
the workspace (``vision``), keyed by the room's shape, so converting again asks only
about rooms that changed, and a project converts the same way without the model.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from shapely import wkt
from shapely.affinity import scale as scale_geom
from shapely.affinity import translate
from shapely.geometry import box

from .extract import ExtractedSpace, modelspace_entities
from .types import SpaceType

SYSTEM = ("You read architectural floor plans (CAD drawings, as printed) the way an architect does. "
          "Answer with exactly one of the given choices for each question.")

ROOM_QUESTION = (
    "This is part of an architectural floor plan. The red outline marks an area that a program found as one "
    "room. Look at the walls, doors, windows, furniture, fixtures and labels. Is the red outline exactly one "
    "room? And what kind of room is it?")
NOT_A_ROOM = "not a room (outside, a garden, a sheet frame, a shaft, a gap or inside a wall)"
OUTLINES = ["exactly one room", "two or more rooms merged together", "only part of a room", NOT_A_ROOM]
ROOM_TYPES: dict[str, SpaceType | None] = {
    "office": SpaceType.OFFICE,
    "meeting room": SpaceType.MEETING_ROOM,
    "bedroom": SpaceType.BEDROOM,
    "living or sitting room (majlis)": SpaceType.LIVING_ROOM,
    "dining room": SpaceType.DINING_ROOM,
    "kitchen or pantry": SpaceType.KITCHEN,
    "bathroom (with a bath or shower)": SpaceType.BATHROOM,
    "toilet or washroom (WC)": SpaceType.RESTROOM,
    "dressing room": SpaceType.DRESSING_ROOM,
    "laundry": SpaceType.LAUNDRY,
    "storage": SpaceType.STORAGE,
    "utility or plant room": SpaceType.UTILITY,
    "prayer room": SpaceType.PRAYER_ROOM,
    "corridor or hall": SpaceType.CORRIDOR,
    "lobby or entrance": SpaceType.LOBBY,
    "stairs": SpaceType.STAIRS,
    "lift": SpaceType.ELEVATOR,
    "void (open to the floor below)": SpaceType.OPEN_TO_BELOW,
    "terrace, balcony or roof": SpaceType.TERRACE,
    "carport or garage": SpaceType.PARKING,
    "open area": SpaceType.OPEN_AREA,
    "other room": SpaceType.ROOM,
    "not a room": None,
}
# Changing the question or the choices asks every room again.
QUESTION_VERSION = hashlib.sha1(json.dumps([SYSTEM, ROOM_QUESTION, OUTLINES, list(ROOM_TYPES)]).encode()).hexdigest()[:8]
CROP_PX = 768
CROP_MIN_M = 6.0  # a crop shows at least this much of the plan around a room


class VisionUnavailable(Exception):
    pass


class VisionModel:
    """An OpenAI-compatible chat endpoint that takes images."""

    def __init__(self, url: str | None = None, model: str | None = None, key: str | None = None,
                 parallel: int | None = None, timeout: float = 300.0):
        self.url = (url if url is not None else os.environ.get("STOREYPATH_VISION_URL", "")).rstrip("/")
        self.model = model or os.environ.get("STOREYPATH_VISION_MODEL") or ""
        self.key = key or os.environ.get("STOREYPATH_VISION_KEY") or ""
        self.parallel = parallel or int(os.environ.get("STOREYPATH_VISION_PARALLEL", "2"))
        self.timeout = timeout
        self._checked: bool | None = None
        self.failed: str | None = None

    @property
    def name(self) -> str:
        return self.model or self.url

    def available(self) -> bool:
        """Whether the endpoint answers (asked once)."""
        if not self.url:
            return False
        if self._checked is None:
            try:
                with urllib.request.urlopen(self._request("/models"), timeout=5) as r:
                    models = json.load(r).get("data") or []
                if not self.model and models:
                    self.model = models[0].get("id", "")
                self._checked = True
            except (OSError, ValueError, urllib.error.URLError) as e:
                self.failed = f"no vision model at {self.url}: {e}"
                self._checked = False
        return self._checked

    def _request(self, path: str, body: dict | None = None) -> urllib.request.Request:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        data = json.dumps(body).encode() if body is not None else None
        return urllib.request.Request(self.url + path, data=data, headers=headers)

    def ask(self, image: bytes, question: str, fields: dict[str, list[str]]) -> dict[str, str]:
        """One answer per field, each one of its choices."""
        schema = {"type": "object", "properties": {f: {"type": "string", "enum": c} for f, c in fields.items()},
                  "required": list(fields)}
        text = question + "".join(f"\nChoices for {f}:\n" + "\n".join(f"- {c}" for c in cs) for f, cs in fields.items())
        body = {
            "model": self.model, "temperature": 0, "max_tokens": 200,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": [
                             {"type": "image_url",
                              "image_url": {"url": "data:image/png;base64," + base64.b64encode(image).decode()}},
                             {"type": "text", "text": text}]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            with urllib.request.urlopen(self._request("/chat/completions", body), timeout=self.timeout) as r:
                out = json.load(r)
            answer = json.loads(out["choices"][0]["message"]["content"])
        except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
            raise VisionUnavailable(f"{self.name}: {e}") from e
        return {f: answer[f] for f in fields if answer.get(f) in fields[f]}


@dataclass
class RoomView:
    outline: str
    type: str


def render(doc, bbox, highlight, px: int = CROP_PX) -> bytes:
    """A part of the drawing as printed (black on white), with ``highlight``
    (shapely geometries, drawing units) outlined in red. PNG bytes."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    x0, y0, x1, y1 = bbox
    fig = Figure(figsize=(px / 100, px / 100), dpi=100)  # no pyplot: safe in Studio's job threads
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    side = max(x1 - x0, y1 - y0)
    pad = side  # blocks placed just outside reach in
    entities = list(modelspace_entities(doc, (x0 - pad, y0 - pad, x1 + pad, y1 + pad)))
    config = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK,
                           lineweight_scaling=0.6, min_lineweight=0.25)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=config).draw_entities(entities)
    for g in highlight:
        for part in getattr(g, "geoms", [g]):
            ring = getattr(part, "exterior", part)
            xs, ys = ring.xy
            ax.plot(xs, ys, color="red", linewidth=3, alpha=0.75)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.axis("off")
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=100, facecolor="white")
    return out.getvalue()


def _window(geom, scale: float, min_m: float = CROP_MIN_M):
    """A square around ``geom`` (drawing units), at least ``min_m`` meters wide and
    half again as wide as the geometry."""
    gx0, gy0, gx1, gy1 = geom.bounds
    side = max(min_m / scale, (gx1 - gx0) * 1.5, (gy1 - gy0) * 1.5)
    cx, cy = (gx0 + gx1) / 2, (gy0 + gy1) / 2
    return box(cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2).bounds


def room_key(model: str, sha: str, src, polygon) -> str:
    shape_text = wkt.dumps(polygon, rounding_precision=2)
    raw = f"{model}|{QUESTION_VERSION}|{sha}|{src.region}|{src.offset}|{shape_text}"
    return hashlib.sha1(raw.encode()).hexdigest()


def look_at_rooms(spaces: list[ExtractedSpace], doc, src, scale: float, sha: str, model: VisionModel | None,
                  answers: dict[str, dict], say=None) -> int:
    """Ask the vision model about every room (answers kept in ``answers``, reused
    when a room's shape is unchanged) and apply what it says. Returns how many
    questions were asked."""
    name = model.name if model is not None else None
    ox, oy = src.offset or (0.0, 0.0)

    def in_drawing(polygon):  # local meters → drawing units
        return translate(scale_geom(polygon, 1 / scale, 1 / scale, origin=(0, 0)), ox, oy)

    by_shape = {a.get("shape"): a for a in answers.values()}  # without the model: any answer about this shape
    views: list[RoomView | None] = []
    todo = []
    for k, space in enumerate(spaces):
        key = room_key(name, sha, src, space.polygon) if name else None
        kept = answers.get(key) if key else by_shape.get(wkt.dumps(space.polygon, rounding_precision=2))
        views.append(RoomView(kept["outline"], kept["type"]) if kept else None)
        if kept is None and model is not None:
            todo.append((k, key))
    asked = 0
    if todo and model is not None and model.available():
        images = []
        for k, _ in todo:  # rendering is not thread-safe through ezdxf's caches: one at a time
            inset = spaces[k].polygon.buffer(-0.12)  # inside the walls, so the walls stay visible
            region = in_drawing(spaces[k].polygon if inset.is_empty else inset)
            images.append(render(doc, _window(region, scale), [region]))
        fields = {"outline": OUTLINES, "type": list(ROOM_TYPES)}

        def one(item):
            (k, key), image = item
            try:
                return k, key, model.ask(image, ROOM_QUESTION, fields)
            except VisionUnavailable as e:
                model.failed = str(e)
                return k, key, None

        with ThreadPoolExecutor(max_workers=max(1, model.parallel)) as pool:
            for k, key, got in pool.map(one, zip(todo, images)):
                if got and "outline" in got and "type" in got:
                    answers[key] = {"outline": got["outline"], "type": got["type"], "model": name,
                                    "shape": wkt.dumps(spaces[k].polygon, rounding_precision=2)}
                    views[k] = RoomView(got["outline"], got["type"])
                    asked += 1
        if say is not None:
            say(f"vision: asked about {asked} of {len(spaces)} rooms")
    for space, view in zip(spaces, views):
        if view is not None:
            apply_view(space, view)
    return asked


def apply_view(space: ExtractedSpace, view: RoomView) -> None:
    """What the model saw, applied to a room: an unnamed area that is not a room is
    set aside (ignored, for a person to restore); one that looks like several rooms,
    or part of one, gets a note; an untyped room gets the type it looks like."""
    seen_type = ROOM_TYPES.get(view.type)
    not_a_room = view.outline == NOT_A_ROOM or view.type == "not a room"
    if not_a_room:
        if not space.name:
            space.ignored = True
            space.issues.append("vision: not a room (outside, a frame or a gap); set aside")
        else:
            space.issues.append("vision: does not look like a room; check it")
        return
    if view.outline == "two or more rooms merged together":
        space.issues.append("vision: looks like two or more rooms merged; split it")
    elif view.outline == "only part of a room":
        space.issues.append("vision: looks like only part of a room; check its outline")
    if space.type == SpaceType.UNSPECIFIED and seen_type is not None:
        space.type, space.type_source = seen_type, "vision"
        space.issues.append(f"vision: typed {seen_type.value.replace('_', ' ')} from what is drawn in it; check it")
