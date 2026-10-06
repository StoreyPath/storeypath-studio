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
import math
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from shapely import wkt
from shapely.affinity import scale as scale_geom
from shapely.affinity import translate
from shapely.geometry import LineString, box

from .extract import ExtractedSpace, ExtractedZone, modelspace_entities
from .geometry import as_polygons
from .types import SpaceType

SYSTEM = ("You read architectural floor plans (CAD drawings, as printed) the way an architect does. "
          "Answer with exactly one of the given choices for each question.")

ROOM_QUESTION = (
    "This is part of an architectural floor plan. The red outline marks an area that a program found as one "
    "room. Look at the walls, doors, windows, furniture, fixtures and labels. Is the red outline exactly one "
    "room? And what kind of room is it? How plans draw things: stairs are a run of many parallel lines close "
    "together (the treads), often with the steps numbered in order and an arrow or UP/DN; a lift is a small box "
    "with a cross; a bathroom has a WC, a basin and a bath or shower; a bedroom has a bed.")
NOT_A_ROOM = "not a room (outside, a garden, a sheet frame, a shaft, a gap or inside a wall)"
MERGED = "two or more rooms merged together"
OUTLINES = ["exactly one room", MERGED, "only part of a room", NOT_A_ROOM]
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


RECHECK_S = 60.0  # an endpoint that did not answer is tried again after this long


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
        self._checked_at = 0.0
        self.failed: str | None = None

    @property
    def name(self) -> str:
        return self.model or self.url

    def available(self) -> bool:
        """Whether the endpoint answers (asked once; again a while after it did not)."""
        if not self.url:
            return False
        if self._checked is None or (not self._checked and time.monotonic() - self._checked_at > RECHECK_S):
            self._checked_at = time.monotonic()
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


class InWords:
    """The vision model asked in words only, as llm.LocalModel is asked: the stronger
    reader of tables and notes where it runs."""

    def __init__(self, vision: VisionModel):
        self.vision = vision

    @property
    def name(self) -> str:
        return self.vision.name

    def available(self) -> bool:
        return self.vision.available()

    def ask(self, system: str, user: str, schema: dict, max_tokens: int = 1024) -> dict:
        from .llm import ModelUnavailable

        body = {
            "model": self.vision.model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            with urllib.request.urlopen(self.vision._request("/chat/completions", body), timeout=self.vision.timeout) as r:
                out = json.load(r)
            return json.loads(out["choices"][0]["message"]["content"])
        except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
            raise ModelUnavailable(f"{self.name}: {e}") from e


@dataclass
class RoomView:
    outline: str
    type: str


def _print_config():
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration

    return Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK,
                         lineweight_scaling=0.6, min_lineweight=0.25)


def print_png(doc, bbox, width: int, height: int) -> bytes:
    """A part of the drawing as printed, black on white: ``bbox`` (drawing units)
    filling ``width`` × ``height`` pixels (the same shape). PNG."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    x0, y0, x1, y1 = bbox
    fig = Figure(figsize=(width / 100, height / 100), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    pad = max(x1 - x0, y1 - y0) * 0.1  # blocks placed just outside reach in
    entities = list(modelspace_entities(doc, (x0 - pad, y0 - pad, x1 + pad, y1 + pad)))
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=_print_config()).draw_entities(entities)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.axis("off")
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=100, facecolor="white")
    return out.getvalue()


def _print(doc, bbox, px: int, highlight=(), pad: float = 0.0, marks=(), letters=()):
    """A square part of the drawing as printed (black on white), ``px`` pixels a
    side: a matplotlib figure, ``highlight`` (shapely geometries, drawing units)
    outlined in red, ``marks`` (lines) in blue and ``letters`` ((text, (x, y)))
    written in blue. ``pad`` (drawing units) brings in blocks placed just outside."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    x0, y0, x1, y1 = bbox
    fig = Figure(figsize=(px / 100, px / 100), dpi=100)  # no pyplot: safe in Studio's job threads
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    entities = list(modelspace_entities(doc, (x0 - pad, y0 - pad, x1 + pad, y1 + pad)))
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=_print_config()).draw_entities(entities)
    for g in highlight:
        for part in getattr(g, "geoms", [g]):
            xs, ys = getattr(part, "exterior", part).xy
            ax.plot(xs, ys, color="red", linewidth=3, alpha=0.75)
    for line in marks:
        xs, ys = line.xy
        ax.plot(xs, ys, color="#0050ff", linewidth=4, alpha=0.7)
    for text, (x, y) in letters:
        ax.text(x, y, text, color="#0050ff", fontsize=px / 28, fontweight="bold", ha="center", va="center",
                bbox={"facecolor": "white", "edgecolor": "#0050ff", "boxstyle": "round,pad=0.2"})
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig


def render(doc, bbox, highlight, px: int = CROP_PX, marks=(), letters=()) -> bytes:
    """A part of the drawing as printed, with ``highlight`` outlined in red and
    ``marks`` and ``letters`` in blue. PNG."""
    x0, y0, x1, y1 = bbox
    fig = _print(doc, bbox, px, highlight, pad=max(x1 - x0, y1 - y0) / 2, marks=marks, letters=letters)
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=100, facecolor="white")
    return out.getvalue()


class FloorPrint:
    """A floor printed once, in overlapping tiles, and looked at a part at a time,
    as a person reads a printed sheet: each room's view is cut out of a tile and
    its outline drawn on it, instead of drawing the plan again for every room.
    Views wider than the tiles' overlap are drawn on their own."""

    PX_PER_M = CROP_PX / CROP_MIN_M  # the smallest view keeps its full detail
    STRIDE_M = 30.0
    OVERLAP_M = 12.0

    def __init__(self, doc, scale: float):
        self.doc, self.scale = doc, scale
        self.stride = self.STRIDE_M / scale  # drawing units
        self.overlap = self.OVERLAP_M / scale
        self.tile_px = round((self.STRIDE_M + self.OVERLAP_M) * self.PX_PER_M)
        self._tiles: dict[tuple[int, int], object] = {}

    def _tile(self, i: int, j: int):
        if (i, j) not in self._tiles:
            from PIL import Image

            x0, y0 = i * self.stride, j * self.stride
            side = self.stride + self.overlap
            fig = _print(self.doc, (x0, y0, x0 + side, y0 + side), self.tile_px, pad=self.overlap / 2)
            fig.canvas.draw()
            self._tiles[(i, j)] = Image.frombuffer("RGBA", fig.canvas.get_width_height(), fig.canvas.buffer_rgba()).convert("RGB")
        return self._tiles[(i, j)]

    def view(self, bbox, highlight, px: int = CROP_PX, marks=(), letters=()) -> bytes:
        x0, y0, x1, y1 = bbox
        if max(x1 - x0, y1 - y0) > self.overlap:
            return render(self.doc, bbox, highlight, px, marks, letters)
        from PIL import ImageDraw, ImageFont

        i, j = int(x0 // self.stride), int(y0 // self.stride)
        tile = self._tile(i, j)
        tx0, ty1 = i * self.stride, j * self.stride + self.stride + self.overlap
        k = self.tile_px / (self.stride + self.overlap)  # pixels per drawing unit

        def pixel(x, y):
            return (x - tx0) * k, (ty1 - y) * k

        left, top = pixel(x0, y1)
        right, bottom = pixel(x1, y0)
        out = tile.crop((round(left), round(top), round(right), round(bottom))).resize((px, px))
        f = px / (right - left)
        draw = ImageDraw.Draw(out, "RGBA")
        for g in highlight:
            for part in getattr(g, "geoms", [g]):
                pts = [((u - left) * f, (v - top) * f) for u, v in (pixel(x, y) for x, y in getattr(part, "exterior", part).coords)]
                draw.line(pts, fill=(255, 0, 0, 190), width=3)
        for line in marks:
            pts = [((u - left) * f, (v - top) * f) for u, v in (pixel(x, y) for x, y in line.coords)]
            draw.line(pts, fill=(0, 80, 255, 180), width=4)
        font = ImageFont.load_default(size=px // 20) if letters else None
        for text, (x, y) in letters:
            u, v = pixel(x, y)
            at = ((u - left) * f, (v - top) * f)
            box_ = draw.textbbox(at, text, font=font, anchor="mm")
            draw.rectangle([box_[0] - 6, box_[1] - 4, box_[2] + 6, box_[3] + 4], fill="white", outline=(0, 80, 255))
            draw.text(at, text, font=font, fill=(0, 80, 255), anchor="mm")
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()


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


def _see(polygons: list, doc, src, scale: float, sha: str, model: VisionModel | None, answers: dict[str, dict],
         sheet: FloorPrint | None = None) -> tuple[list[RoomView | None], int]:
    """What vision sees each area as (the room question, the area outlined in red),
    with how many questions were asked. Answers are kept in ``answers`` and reused
    while an area's shape is unchanged; without the model, any model's answer about
    the shape is used."""
    usable = model is not None and model.available()
    name = model.name if usable else None  # the model's own name is known once it has answered
    ox, oy = src.offset or (0.0, 0.0)

    def in_drawing(polygon):  # local meters → drawing units
        return translate(scale_geom(polygon, 1 / scale, 1 / scale, origin=(0, 0)), ox, oy)

    by_shape = {a["shape"]: a for a in answers.values() if "shape" in a} if not usable else {}
    views: list[RoomView | None] = [None] * len(polygons)
    todo = []
    for n, polygon in enumerate(polygons):
        key = room_key(name, sha, src, polygon) if name else None
        kept = answers.get(key) if key else by_shape.get(wkt.dumps(polygon, rounding_precision=2))
        if kept:
            views[n] = RoomView(kept["outline"], kept["type"])
        elif usable:
            todo.append((n, key))
    asked = 0
    if todo:
        images = []
        sheet = sheet or FloorPrint(doc, scale)
        for n, _ in todo:  # rendering is not thread-safe through ezdxf's caches: one at a time
            inset = polygons[n].buffer(-0.12)  # inside the walls, so the walls stay visible
            region = in_drawing(polygons[n] if inset.is_empty else inset)
            images.append(sheet.view(_window(region, scale), [region]))
        fields = {"outline": OUTLINES, "type": list(ROOM_TYPES)}

        def one(item):
            (n, key), image = item
            try:
                return n, key, model.ask(image, ROOM_QUESTION, fields)
            except VisionUnavailable as e:
                model.failed = str(e)
                return n, key, None

        with ThreadPoolExecutor(max_workers=max(1, model.parallel)) as pool:
            for n, key, got in pool.map(one, zip(todo, images)):
                if got and "outline" in got and "type" in got:
                    answers[key] = {"outline": got["outline"], "type": got["type"], "model": name,
                                    "shape": wkt.dumps(polygons[n], rounding_precision=2)}
                    views[n] = RoomView(got["outline"], got["type"])
                    asked += 1
    return views, asked


def look_at_rooms(spaces: list[ExtractedSpace], doc, src, scale: float, sha: str, model: VisionModel | None,
                  answers: dict[str, dict], say=None, only: set[int] | None = None,
                  sheet: FloorPrint | None = None) -> list[int]:
    """Ask the vision model about every room (or the ones in ``only``; answers kept
    in ``answers``, reused when a room's shape is unchanged) and apply what it says.
    Returns the rooms it saw as several rooms merged."""
    chosen = [k for k in range(len(spaces)) if only is None or k in only]
    seen, asked = _see([spaces[k].polygon for k in chosen], doc, src, scale, sha, model, answers, sheet)
    if say is not None and asked:
        say(f"vision: asked about {asked} of {len(chosen)} rooms")
    merged = []
    for k, view in zip(chosen, seen):
        if view is None:
            continue
        apply_view(spaces[k], view, wraps=_wraps_the_rest(k, spaces))
        if view.outline == MERGED and not spaces[k].ignored:
            merged.append(k)
    return merged


WRAPS_SHARE = 0.5  # an area whose outline wraps round this share of the other rooms' area…


def _wraps_the_rest(k: int, spaces: list[ExtractedSpace]) -> bool:
    """Whether a space's outline wraps round most of the other rooms, as the yard
    round a house does (the convex hull of its outline holds them)."""
    hull = spaces[k].polygon.convex_hull
    others = [s.polygon for j, s in enumerate(spaces) if j != k]
    total = sum(p.area for p in others)
    return total > 0 and sum(p.area for p in others if hull.contains(p.representative_point())) >= WRAPS_SHARE * total


def apply_view(space: ExtractedSpace, view: RoomView, wraps: bool = False) -> None:
    """What the model saw, applied to a room: an area that is not a room is set
    aside (ignored, for a person to restore) when it has no name, or when it also
    wraps round the other rooms (a yard named after the steps or landing in it);
    one that looks like several rooms, or part of one, gets a note; an untyped room
    gets the type it looks like."""
    seen_type = ROOM_TYPES.get(view.type)
    not_a_room = view.outline == NOT_A_ROOM or view.type == "not a room"
    if not_a_room:
        if not space.name:
            space.ignored = True
            space.issues.append("vision: not a room (outside, a frame or a gap); set aside")
        elif wraps:
            space.ignored = True
            space.issues.append("vision: not a room, and it wraps round the rooms (outside); set aside")
        else:
            space.issues.append("vision: does not look like a room; check it")
        return
    if view.outline == MERGED:
        space.issues.append("vision: looks like two or more rooms merged; split it")
    elif view.outline == "only part of a room":
        space.issues.append("vision: looks like only part of a room; check its outline")
    if space.type == SpaceType.UNSPECIFIED and seen_type is not None:
        space.type, space.type_source = seen_type, "vision"
        space.issues.append(f"vision: typed {seen_type.value.replace('_', ' ')} from what is drawn in it; check it")


# ---- dividing merged rooms -------------------------------------------------------------

SPLIT_QUESTION = (
    "This is part of an architectural floor plan. A program found the area outlined in red as one room. The "
    "blue line crosses it, with the letter A on one side and B on the other. Look at the walls, furniture, "
    "fixtures and labels on each side. What kind of room or area is side A, and what kind is side B?")
SPLIT_FIELDS = {"a": list(ROOM_TYPES), "b": list(ROOM_TYPES)}
SPLIT_VERSION = hashlib.sha1(json.dumps([SYSTEM, SPLIT_QUESTION, SPLIT_FIELDS]).encode()).hexdigest()[:8]
UNSURE = {"other room"}  # says nothing about where one room ends


def _two_rooms(seen: dict) -> bool:
    """Whether what vision saw on the two sides of a line is two different rooms."""
    a, b = seen.get("a"), seen.get("b")
    return a is not None and b is not None and a != b and not {a, b} & UNSURE


CUT_M = (0.6, 12.0)  # a dividing line is this long…
SQUARE_DEG = 8.0  # …and square to the room's walls, within this
TREAD_M = 1.0  # a line with two like it this close alongside is a stair's tread or a pattern
NECK_M = 5.0  # …and between two inside corners, at most this long
MAX_CUTS = 8  # lines asked about per merged room
MIN_PART_M2 = 2.0  # each side of a division is at least this big…
MIN_PART_WIDTH_M = 0.8  # …and this wide (a counter's front edge is no division)
TO_WALL_M = 0.6  # a drawn line counts when it ends this close to the room's edge
SPLIT_NOTE = "vision: a zone of an open space, divided where two uses meet (no wall); check the line"
CUT_GAP_M = 1.5  # a line alongside one already cut, closer than this, would leave a strip


def _extended(line: LineString, by: float) -> LineString:
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    ux, uy = (x1 - x0) / line.length * by, (y1 - y0) / line.length * by
    return LineString([(x0 - ux, y0 - uy), (x1 + ux, y1 + uy)])


def divided(polygon, cut: LineString) -> list | None:
    """The two pieces a straight cut divides a room into, each big and wide enough
    to be a room; None otherwise."""
    from shapely.ops import split as split_by_line

    pieces = [p for p in as_polygons(split_by_line(polygon, _extended(cut, 0.3)))]
    if len(pieces) != 2 or min(p.area for p in pieces) < MIN_PART_M2:
        return None
    if any(p.buffer(-MIN_PART_WIDTH_M / 2).is_empty for p in pieces):
        return None
    return pieces


def _chord(polygon, start, direction, reach: float) -> LineString | None:
    """From a point on a room's edge, straight on in ``direction`` (a unit vector)
    to where the line first meets the edge again."""
    import shapely

    sx, sy = start
    ray = LineString([(sx + direction[0] * 1e-6, sy + direction[1] * 1e-6),
                      (sx + direction[0] * reach, sy + direction[1] * reach)])
    hit = ray.intersection(polygon.boundary)
    if hit.is_empty:
        return None
    nearest = min((p for p in shapely.get_parts(shapely.extract_unique_points(hit))),
                  key=lambda p: (p.x - sx) ** 2 + (p.y - sy) ** 2)
    line = LineString([(sx, sy), (nearest.x, nearest.y)])
    return line if polygon.buffer(0.02).contains(line) else None


def _angle(line: LineString) -> float:
    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180


def _main_direction(polygon) -> float:
    """The direction of a room's walls (degrees, modulo 90): the one most of its
    edge runs along or square to."""
    weights: dict[int, float] = {}
    coords = list(polygon.exterior.coords)
    for a, b in zip(coords, coords[1:]):
        if a != b:
            k = round(_angle(LineString([a, b]))) % 90
            weights[k] = weights.get(k, 0.0) + math.dist(a, b)
    return float(max(weights, key=weights.get)) if weights else 0.0


def _square(line: LineString, main: float) -> bool:
    return abs((_angle(line) - main + 45) % 90 - 45) <= SQUARE_DEG


def _a_tread(seg: LineString, others: list[LineString]) -> bool:
    """Whether a line is one of a run of like lines side by side (a stair's or a
    ramp's treads, a floor pattern), not a single line where two rooms meet."""
    middle = seg.interpolate(0.5, normalized=True)
    like = 0
    for o in others:
        if o is seg or abs((_angle(o) - _angle(seg) + 90) % 180 - 90) > 3:
            continue
        if abs(o.length - seg.length) > 0.3 * seg.length:
            continue
        apart = LineString([_extended(seg, 50).interpolate(_extended(seg, 50).project(o.interpolate(0.5, normalized=True))),
                            o.interpolate(0.5, normalized=True)]).length
        if 0.05 < apart <= TREAD_M and o.distance(middle) <= TREAD_M + 0.5 * seg.length:
            like += 1
    return like >= 2


def _reaches_edge(line: LineString, edge) -> bool:
    import shapely

    return all(edge.distance(shapely.Point(c)) < 1e-6 for c in (line.coords[0], line.coords[-1]))


def candidate_cuts(polygon, segments) -> list[LineString]:
    """Where a merged room may divide: lines drawn across it from wall to wall (a
    floor finish changing, a threshold, a partition on another layer), then walls
    carried on past the corner where they stop, and necks between two such corners.
    Shortest first, near-duplicates once."""
    import shapely
    from shapely.geometry.polygon import orient

    edge = polygon.boundary
    main = _main_direction(polygon)
    drawn = []
    for seg in segments:
        if not CUT_M[0] <= seg.length <= CUT_M[1] or not _square(seg, main):
            continue
        middle = seg.interpolate(0.5, normalized=True)
        if not polygon.contains(middle):
            continue
        if edge.distance(shapely.Point(seg.coords[0])) > TO_WALL_M or edge.distance(shapely.Point(seg.coords[-1])) > TO_WALL_M:
            continue
        across = [g for g in shapely.get_parts(polygon.intersection(_extended(seg, TO_WALL_M + 0.2)))
                  if isinstance(g, LineString) and g.length > 0 and g.distance(middle) < 1e-6]
        if across and _reaches_edge(across[0], edge) and seg.length >= 0.7 * across[0].length:
            drawn.append((seg, across[0]))
    found = [(0, cut) for seg, cut in drawn if not _a_tread(seg, [s for s, _ in drawn])]
    ring = list(orient(polygon, 1.0).exterior.coords)[:-1]
    corners = []
    for i, (bx, by) in enumerate(ring):  # inside corners: a right turn on a counter-clockwise ring
        ax, ay = ring[i - 1]
        cx, cy = ring[(i + 1) % len(ring)]
        if (bx - ax) * (cy - by) - (by - ay) * (cx - bx) >= -1e-9:
            continue
        corners.append(shapely.Point(bx, by))
        for (px, py), (qx, qy) in (((ax, ay), (bx, by)), ((cx, cy), (bx, by))):  # each wall, carried on
            length = ((qx - px) ** 2 + (qy - py) ** 2) ** 0.5
            if length > 0 and (cut := _chord(polygon, (bx, by), ((qx - px) / length, (qy - py) / length), CUT_M[1])):
                if CUT_M[0] <= cut.length and _square(cut, main):
                    found.append((1, cut))
    inside = polygon.buffer(0.02)
    for i, a in enumerate(corners):
        for b in corners[i + 1:]:
            if CUT_M[0] <= a.distance(b) <= NECK_M:
                neck = LineString([a, b])
                if _square(neck, main) and inside.contains(neck):
                    found.append((1, neck))
    out: list[LineString] = []
    for _, cut in sorted(found, key=lambda kc: (kc[0], kc[1].length)):
        if any(cut.hausdorff_distance(o) < 0.4 for o in out) or divided(polygon, cut) is None:
            continue
        out.append(cut)
        if len(out) >= MAX_CUTS:
            break
    return out


def _segments_in(doc, region_drawing, to_local) -> list[LineString]:
    """Straight pieces of everything drawn over a room (not text or dimensions),
    in local meters."""
    from .extract import _flatten, _walk

    x0, y0, x1, y1 = region_drawing.bounds
    out = []
    for e, _ in _walk(modelspace_entities(doc, (x0, y0, x1, y1))):
        if e.dxftype() in ("TEXT", "MTEXT", "ATTRIB", "INSERT", "DIMENSION", "HATCH", "MULTILEADER"):
            continue
        try:
            flat = _flatten(e, 0.02)
        except Exception:
            continue
        if not flat:
            continue
        pts = [to_local(x, y) for x, y in flat[0]]
        out += [LineString([a, b]) for a, b in zip(pts, pts[1:]) if a != b]
    return out


def _parallel(a: LineString, b: LineString) -> bool:
    return abs((_angle(a) - _angle(b) + 90) % 180 - 90) <= 10


def _a_room(view: RoomView) -> bool:
    """Whether vision sees an area, on its own, as a room (or several)."""
    return view.outline in ("exactly one room", MERGED) and view.type != "not a room"


def _checked(pieces: list, cuts: list, doc, src, scale, sha, model, answers, sheet) -> tuple[list, list, int]:
    """A room's pieces after each was looked at on its own: a cut stays only where
    vision sees the pieces on its two sides as rooms of different kinds. Across the
    others (the same kind, or a piece that on its own is only part of a room or no
    room at all: a strip along the windows, a stair's flight) the pieces are joined
    again. Returns the pieces, the cuts kept and how many questions were asked."""
    pieces, cuts, asked = list(pieces), list(cuts), 0
    while cuts:
        views, n = _see(pieces, doc, src, scale, sha, model, answers, sheet)
        asked += n
        for cut in cuts:
            middle = cut.interpolate(0.5, normalized=True)
            sides = [i for i, p in enumerate(pieces) if p.distance(middle) < 0.05]
            if len(sides) != 2:
                continue
            a, b = views[sides[0]], views[sides[1]]
            if a is None or b is None or a.type == b.type or not (_a_room(a) and _a_room(b)):
                i, j = sides
                pieces[i] = max(as_polygons(pieces[i].union(pieces[j])), key=lambda p: p.area)
                del pieces[j]
                cuts.remove(cut)
                break
        else:
            break
    return pieces, cuts, asked


def split_merged(ex, units: list, doc, src, sha: str, model: VisionModel | None, answers: dict[str, dict],
                 merged: list[int], say=None, sheet: FloorPrint | None = None) -> list:
    """Divide the rooms vision saw as several merged: each line that may divide one
    is shown to the model in blue, its sides lettered A and B; where it sees two
    different kinds of room on the two sides, the room is cut exactly along the
    line. Each piece is then looked at on its own, and a cut stays only where the
    pieces on its two sides are different rooms. There is no wall along the cuts:
    the pieces are zones of the one space (``units`` are the zones and the spaces
    with none, ``merged`` indexes into them). Answers are kept in ``answers`` as
    for the rooms. Returns the zones made."""
    scale = ex.scale
    ox, oy = src.offset or (0.0, 0.0)
    usable = model is not None and model.available()
    name = model.name if usable else None
    by_cut = {a["cut"]: a for a in answers.values() if "cut" in a}  # without the model: any answer about this line

    def in_drawing(g):  # local meters → drawing units
        return translate(scale_geom(g, 1 / scale, 1 / scale, origin=(0, 0)), ox, oy)

    def to_local(x, y):
        return (x - ox) * scale, (y - oy) * scale

    lines = []  # [room, cut, key, answer, cut as text]
    for k in merged:
        polygon = units[k].polygon
        shape_text = wkt.dumps(polygon, rounding_precision=2)
        for cut in candidate_cuts(polygon, _segments_in(doc, in_drawing(polygon.buffer(TO_WALL_M)), to_local)):
            text = shape_text + "|" + wkt.dumps(cut, rounding_precision=2)
            key = hashlib.sha1(f"{name}|{SPLIT_VERSION}|{sha}|{src.region}|{src.offset}|{text}".encode()).hexdigest() if name else None
            lines.append([k, cut, key, answers.get(key) if key else by_cut.get(text), text])
    todo = [line for line in lines if line[3] is None] if usable else []
    asked = 0
    if todo:
        sheet = sheet or FloorPrint(doc, scale)
        images = []
        for k, cut, *_ in todo:  # rendering one at a time, as for the rooms
            polygon = units[k].polygon
            inset = polygon.buffer(-0.12)
            region = in_drawing(polygon if inset.is_empty else inset)
            middle = cut.interpolate(0.5, normalized=True)
            near = middle.buffer(min(2.0, max(0.8, cut.length / 2)))
            sides = []
            for piece in divided(polygon, cut) or []:
                part = piece.intersection(near)
                p = (part if not part.is_empty else piece).representative_point()
                sides.append(in_drawing(p).coords[0])
            images.append(sheet.view(_window(in_drawing(middle.buffer(cut.length * 0.75)), scale), [region],
                                     marks=[in_drawing(cut)], letters=list(zip("AB", sides))))

        def one(item):
            line, image = item
            try:
                return line, model.ask(image, SPLIT_QUESTION, SPLIT_FIELDS)
            except VisionUnavailable as e:
                model.failed = str(e)
                return line, {}

        with ThreadPoolExecutor(max_workers=max(1, model.parallel)) as pool:
            for line, got in pool.map(one, zip(todo, images)):
                if "a" in got and "b" in got:
                    line[3] = answers[line[2]] = {"a": got["a"], "b": got["b"], "model": name, "cut": line[4]}
                    asked += 1

    made: list = []
    rooms = looked = 0
    for k in merged:
        unit = units[k]
        accepted = sorted((cut for kk, cut, _, seen, _ in lines if kk == k and seen and _two_rooms(seen)),
                          key=lambda c: c.length)
        pieces, used = [unit.polygon], []
        for cut in accepted:  # shortest first; each cuts the piece it runs through
            if any(cut.distance(u) < CUT_GAP_M and _parallel(cut, u) for u in used):
                continue
            middle = cut.interpolate(0.5, normalized=True)
            for n, piece in enumerate(pieces):
                if piece.buffer(0.01).contains(middle) and (parts := divided(piece, cut)):
                    pieces[n:n + 1] = parts
                    used.append(cut)
                    break
        if len(pieces) >= 2:
            pieces, used, n = _checked(pieces, used, doc, src, scale, sha, model, answers, sheet)
            looked += n
        if len(pieces) < 2:
            continue
        rooms += 1
        keep = [i for i in unit.issues if not i.startswith(("vision:", "has the labels of"))]
        if isinstance(unit, ExtractedZone):  # a zone divided further: its parts replace it
            parent = unit.space
            ex.zones = [z for z in ex.zones if z is not unit]
        else:
            parent = next(i for i, s in enumerate(ex.spaces) if s is unit)
        zones = [ExtractedZone(polygon=p, layer=unit.layer, issues=[*keep, SPLIT_NOTE], space=parent)
                 for p in pieces]
        ex.zones.extend(zones)
        made += zones
    if say is not None and merged:
        say(f"vision: asked about {asked} dividing lines and {looked} pieces; divided {rooms} of {len(merged)} "
            f"open areas into {len(made)} zones")
    return made
