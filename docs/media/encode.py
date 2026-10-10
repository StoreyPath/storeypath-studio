"""The README's pictures made from the screenshots docs/media/capture.mjs took: each job of
its jobs.json, into docs/images/. Pillow alone (no ffmpeg, no gifski): stills as WebP
(lossless for Studio's pages, lossy for the 3D) or PNG, two views split down the middle,
and animated WebP from frames.

    studio/.venv/bin/python docs/media/encode.py <jobs.json> <out folder>

A job: {"in": png, "out": name, "width"?, "crop"?: [x, y, w, h], "lossless"?, "quality"?},
{"split": [left png, right png], "out", …}, or {"frames": [[png, ms], …], "out", "width",
"quality"?} (an animation, looping).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def _fit(img: Image.Image, job: dict) -> Image.Image:
    if job.get("crop"):
        x, y, w, h = job["crop"]
        img = img.crop((x, y, x + w, y + h))
    width = job.get("width")
    if width and img.width != width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    return img


def _save(img: Image.Image, out: Path, job: dict) -> None:
    if out.suffix == ".png":
        img.convert("RGB").save(out, optimize=True)
    elif job.get("lossless"):
        img.convert("RGB").save(out, lossless=True, quality=100, method=6)
    else:
        img.convert("RGB").save(out, quality=job.get("quality", 88), method=6)


def _split(job: dict) -> Image.Image:
    """Two views of one place side by side, cut down the middle, a line between them."""
    left, right = (Image.open(p).convert("RGB") for p in job["split"])
    out = left.copy()
    half = left.width // 2
    out.paste(right.crop((half, 0, right.width, right.height)), (half, 0))
    draw = ImageDraw.Draw(out)
    line = max(2, left.width // 800)
    draw.rectangle((half - line // 2, 0, half + (line + 1) // 2 - 1, out.height), fill=(255, 255, 255))
    return out


def _animate(job: dict, out: Path) -> None:
    frames, durations = [], []
    for path, ms in job["frames"]:
        frames.append(_fit(Image.open(path).convert("RGB"), job))
        durations.append(int(ms))
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0,
                   quality=job.get("quality", 70), method=job.get("method", 4), minimize_size=True,
                   allow_mixed=job.get("mixed", False))


def main(jobs_file: str, folder: str) -> None:
    out_dir = Path(folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    for job in json.loads(Path(jobs_file).read_text()):
        out = out_dir / job["out"]
        if "frames" in job:
            _animate(job, out)
        else:
            img = _split(job) if "split" in job else Image.open(job["in"])
            _save(_fit(img, job), out, job)
        print(f"  {out.name}: {out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main(*sys.argv[1:3])
