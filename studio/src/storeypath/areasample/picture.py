"""The pictures of an area sample: drawing.png, the area as drawn (as Studio prints a
floor: black on white), and reading.png, the same with Studio's reading drawn over it
and a legend. Both are drawn from the sample's own drawing.dxf, so they show exactly
what the file holds, private texts already replaced."""

from __future__ import annotations

import io
import re
from functools import lru_cache

import numpy as np
from shapely.geometry import shape

from ..assets import asset_dir

FULL_PX_PER_M = 200  # a pixel half a centimetre…
FULL_MAX_PX = 5000  # …within this many pixels a side: a room's label stays legible
PREVIEW_MAX_PX = 900
FALLBACK = "#9db8d2"
DOOR, WINDOW, OPENING = "#f08c00", "#1098ad", "#f08c00"
FOUND_WALLS, DRAWN = "#e03131", "#c2255c"


@lru_cache(maxsize=1)
def type_colors() -> dict[str, str]:
    """The viewer's colours of the space types (theme.js), as Review shows them."""
    try:
        text = (asset_dir("viewer") / "src" / "theme.js").read_text(encoding="utf-8")
        block = re.search(r"TYPE_COLORS\s*=\s*\{(.*?)\}", text, re.S).group(1)
        return dict(re.findall(r"(\w+):\s*\"(#[0-9a-fA-F]{6})\"", block))
    except (OSError, AttributeError, FileNotFoundError):
        return {}


def px_per_m(extent: list[float], preview: bool = False) -> float:
    x0, y0, x1, y1 = extent
    side = max(x1 - x0, y1 - y0, 0.01)
    return min(FULL_PX_PER_M, (PREVIEW_MAX_PX if preview else FULL_MAX_PX) / side)


def _plan(doc, scale: float, extent: list[float], per_m: float):
    """The drawing's part as printed (black on white): the figure and its pixels."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from ..vision import _DRAWING, _print_config

    x0, y0, x1, y1 = extent
    width, height = max(1, round((x1 - x0) * per_m)), max(1, round((y1 - y0) * per_m))
    # lines at least a little over a pixel wide however large the picture: a print's own
    # minimum (a quarter millimetre) is a hair at a pixel half a centimetre
    config = _print_config().with_changes(min_lineweight=max(0.25, 0.4 * max(width, height) / 2000))
    with _DRAWING:  # ezdxf's drawing caches are not thread-safe: one drawing at a time
        fig = Figure(figsize=(width / 100, height / 100), dpi=100)
        FigureCanvasAgg(fig)
        ax = fig.add_axes((0, 0, 1, 1))
        Frontend(RenderContext(doc), MatplotlibBackend(ax), config=config).draw_entities(doc.modelspace())
        ax.set_xlim(x0 / scale, x1 / scale)
        ax.set_ylim(y0 / scale, y1 / scale)
        ax.axis("off")
        fig.canvas.draw()
        pixels = np.asarray(fig.canvas.buffer_rgba())[..., :3].copy()
    return fig, pixels


def _png(fig) -> bytes:
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=100, facecolor="white")
    return out.getvalue()


def draw(doc, scale: float, reading: dict, corrections: dict, *, preview: bool = False,
         title: str = "Studio's reading") -> tuple[bytes, bytes]:
    """(drawing.png, reading.png) of a sample: ``reading`` and ``corrections`` as the
    sample's JSON files have them (metres, the sample's frame)."""
    extent = reading["frame"]["extent"]
    per_m = px_per_m(extent, preview)
    fig, pixels = _plan(doc, scale, extent, per_m)
    drawing = _png(fig)
    return drawing, overlay(pixels, reading, corrections, per_m, title=title)


def overlay(pixels, reading: dict, corrections: dict, per_m: float, title: str = "Studio's reading") -> bytes:
    """The plan's pixels with a reading drawn over them, and a legend beside."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Polygon as PolygonPatch, Rectangle

    x0, y0, x1, y1 = reading["frame"]["extent"]
    _, _, w, h = reading["frame"]["area"]
    height, width = pixels.shape[:2]
    big = max(width, height)
    legend_w = max(320, round(big * 0.3))
    px = lambda points: points * 72 / 100  # noqa: E731 (points of a font or line at 100 dpi)
    fs_legend = max(9.0, min(36.0, px(big / 70)))
    fs_label = max(6.0, min(28.0, px(0.2 * per_m)))
    line = max(1.0, px(big / 900))

    fig = Figure(figsize=((width + legend_w) / 100, height / 100), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, width / (width + legend_w), 1))
    ax.imshow(pixels, extent=(x0, x1, y0, y1), origin="upper", interpolation="nearest")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.axis("off")
    colors = type_colors()

    # outside the area chosen: its margin, greyed
    for rect in ((x0, y0, -x0, y1 - y0), (w, y0, x1 - w, y1 - y0), (0, y0, w, -y0), (0, h, w, y1 - h)):
        ax.add_patch(Rectangle(rect[:2], rect[2], rect[3], facecolor="#868e96", alpha=0.18, edgecolor="none"))
    ax.add_patch(Rectangle((0, 0), w, h, fill=False, edgecolor="#343a40", linestyle=(0, (6, 4)), linewidth=line))

    walls = reading["floor"].get("walls_found")
    if walls:
        for ring in _rings(walls):
            ax.plot(*zip(*ring), color=FOUND_WALLS, linewidth=line * 0.8, alpha=0.7)

    types_seen: dict[str, int] = {}
    for s in reading["spaces"]:
        if not s.get("geometry"):
            continue
        g = shape(s["geometry"])
        divided = bool(s.get("zones"))
        color = colors.get(s["type"], FALLBACK)
        ignored = s["now"]["ignored"]
        for poly in getattr(g, "geoms", [g]):
            if poly.geom_type != "Polygon":
                continue
            ax.add_patch(PolygonPatch(list(poly.exterior.coords), closed=True,
                                      facecolor="none" if divided else ("#adb5bd" if ignored else color),
                                      alpha=1 if divided else (0.25 if ignored else 0.38),
                                      edgecolor="#495057", linewidth=line * (1.4 if divided else 0.7),
                                      hatch="//" if ignored else None))
        if divided:
            continue
        types_seen[s["type"]] = types_seen.get(s["type"], 0) + 1
        from ..export import _label_point

        lx, ly = _label_point(g)
        # below the middle, where the drawing's own label usually is, when the room has room
        below = min(1.0, (g.bounds[3] - g.bounds[1]) * 0.2)
        if g.contains(_pt(lx, ly - below)):
            ly -= below
        words = [s["id"], s["name"] or "", s["number"] or ""]
        lines = [" ".join(w for w in words if w), f"{s['type'].replace('_', ' ')} · {s['decided_by']}"]
        if ignored:
            lines.append("deleted" if s["now"]["corrected"] else "set aside")
        now = s["now"]
        if now["corrected"] and (now["type"] != s["type"] or now["name"] != s["name"] or now["number"] != s["number"]):
            lines.append("→ person: " + " ".join(x for x in (now["type"].replace("_", " "), now["name"] or "",
                                                               now["number"] or "") if x))
        ax.text(lx, ly, "\n".join(lines), fontsize=fs_label, ha="center", va="center", color="#212529",
                linespacing=1.15, clip_on=True,
                bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none", "boxstyle": "round,pad=0.15"})

    for d in reading["openings"]:
        if not d.get("span"):
            continue
        xs, ys = zip(*d["span"])
        style = {"door": (DOOR, "-"), "window": (WINDOW, "-"), "opening": (OPENING, (0, (3, 2)))}[d["type"]]
        ax.plot(xs, ys, color=style[0], linestyle=style[1], linewidth=line * 3, alpha=0.4 if d["ignored"] else 0.95,
                solid_capstyle="butt")
        for leaf in d.get("swings") or []:
            (hx, hy), (fx, fy) = leaf
            ax.plot([hx, fx], [hy, fy], color=style[0], linewidth=line, alpha=0.9)
        mx, my = d["middle"]
        ax.text(mx, my, d["id"], fontsize=fs_label * 0.75, color=style[0], ha="center", va="bottom", clip_on=True,
                fontweight="bold")

    drawn = corrections.get("drawn", {})
    for wall in drawn.get("walls", []):
        ax.plot(*zip(*wall), color=DRAWN, linewidth=line * 4, alpha=0.8, solid_capstyle="round")
    for div in drawn.get("dividers", []):
        ax.plot(*zip(*div), color="#0ca678", linewidth=line * 2.5, linestyle=(0, (4, 3)))
    for o in drawn.get("openings", []):
        ax.plot(*zip(*o["span"]), color=DRAWN, linewidth=line * 5, alpha=0.8)
    for ring in drawn.get("spaces", []):
        ax.add_patch(PolygonPatch(ring, closed=True, fill=False, edgecolor=DRAWN, linewidth=line * 2,
                                  linestyle=(0, (5, 3))))
    for it in corrections.get("items", []):
        x, y = it["at"]
        ax.add_patch(Rectangle((x - 0.3, y - 0.3), 0.6, 0.6, facecolor="#495057", alpha=0.6, edgecolor="white",
                               linewidth=line * 0.5))
        ax.text(x, y - 0.4, it["id"], fontsize=fs_label * 0.7, ha="center", va="top", color="#495057", clip_on=True)

    # the legend, beside the plan
    lg = fig.add_axes((width / (width + legend_w), 0, legend_w / (width + legend_w), 1))
    lg.axis("off")
    n_spaces = sum(1 for s in reading["spaces"] if not s.get("zones"))
    head = [title, f"{w:g} × {h:g} m", f"{n_spaces} spaces and zones · {len(reading['openings'])} openings",
            f"walls found by {reading['floor'].get('method') or '?'}"]
    lg.text(0.06, 0.985, "\n".join(head), transform=lg.transAxes, va="top", ha="left", fontsize=fs_legend,
            linespacing=1.35)
    handles = [Patch(facecolor=colors.get(t, FALLBACK), alpha=0.6, edgecolor="#495057",
                     label=f"{t.replace('_', ' ')} ({n})") for t, n in sorted(types_seen.items())]
    handles += [
        Line2D([], [], color=DOOR, linewidth=4, label="door (D)"),
        Line2D([], [], color=WINDOW, linewidth=4, label="window (W)"),
        Line2D([], [], color=OPENING, linewidth=4, linestyle=(0, (3, 2)), label="opening, no door (O)"),
        Line2D([], [], color=FOUND_WALLS, linewidth=1.5, label="walls as Studio found them"),
        Line2D([], [], color=DRAWN, linewidth=4, label="drawn by a person in review"),
        Line2D([], [], color="#0ca678", linewidth=2.5, linestyle=(0, (4, 3)), label="divider drawn (zones)"),
        Patch(facecolor="#495057", alpha=0.6, label="item placed (I)"),
        Patch(facecolor="#adb5bd", alpha=0.4, hatch="//", edgecolor="#495057", label="deleted or set aside"),
        Line2D([], [], color="#343a40", linestyle=(0, (6, 4)), label="the area chosen"),
        Patch(facecolor="#868e96", alpha=0.25, label="its 1 m margin"),
    ]
    lg.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.03, 0.86), frameon=False, fontsize=fs_legend * 0.85,
              handlelength=1.8, labelspacing=0.55, borderaxespad=0)
    lg.text(0.06, 0.015, "Labels: ID name number\ntype · who decided it\n→ person: a correction",
            transform=lg.transAxes, va="bottom", ha="left", fontsize=fs_legend * 0.8, color="#495057",
            linespacing=1.3)
    return _png(fig)


def _pt(x, y):
    from shapely.geometry import Point

    return Point(x, y)


def _rings(geojson: dict):
    g = shape(geojson)
    for part in getattr(g, "geoms", [g]):
        if part.geom_type == "Polygon":
            yield list(part.exterior.coords)
            for hole in part.interiors:
                yield list(hole.coords)
        elif part.geom_type in ("LineString", "LinearRing"):
            yield list(part.coords)
