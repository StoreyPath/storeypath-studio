"""A package's 3D, built ahead of time: format 0.5's ``world/`` folder, one binary
glTF a floor, so a slow machine shows a building without building it.

The viewer's own builder (viewer/src/world/build.js) makes it, run in Node.js by
viewer/world/bake.mjs, so a floor pre-built looks as one the viewer builds. Node.js
is ``STOREYPATH_NODE`` (empty: none), else ``node`` on the PATH. Without it the
package is exported as before, without ``world/``, and the reason is told.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .assets import asset_dir

NODE_ENV = "STOREYPATH_NODE"
WORLD_DIR = "world/"
TIMEOUT_S = 900  # a large building's floors take a second or two each; a campus, more


def node() -> tuple[str | None, str | None]:
    """The Node.js to bake with, or None and why there is none."""
    given = os.environ.get(NODE_ENV)
    if given is not None:
        if not given.strip():
            return None, f"{NODE_ENV} is empty"
        found = shutil.which(given)
        return (found, None) if found else (None, f"{NODE_ENV} ({given}) is not a program")
    found = shutil.which("node")
    return (found, None) if found else (None, f"Node.js was not found (install it, or set {NODE_ENV})")


def baker() -> Path | None:
    """viewer/world/bake.mjs, when it and what it needs are here."""
    try:
        viewer = asset_dir("viewer")
    except FileNotFoundError:
        return None
    script = viewer / "world" / "bake.mjs"
    needs = (script, viewer / "src" / "world" / "build.js", viewer / "vendor" / "three" / "three.module.js",
             viewer / "vendor" / "three" / "addons" / "exporters" / "GLTFExporter.js", viewer / "vendor" / "jszip.mjs")
    return script if all(p.is_file() for p in needs) else None


def bake_world(package: str | Path) -> tuple[dict[str, bytes], str | None]:
    """Every floor of ``package`` in 3D (``world/<floor-id>.glb`` → its bytes), or
    nothing and why."""
    exe, why = node()
    if exe is None:
        return {}, why
    script = baker()
    if script is None:
        return {}, "the 3D baker (viewer/world/bake.mjs, with three.js) is not installed"
    with tempfile.TemporaryDirectory(prefix="storeypath-world-") as out:
        try:
            run = subprocess.run([exe, str(script), str(package), out], capture_output=True, text=True,
                                 timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return {}, f"building it took more than {TIMEOUT_S // 60} minutes"
        except OSError as e:
            return {}, f"Node.js could not be run: {e}"
        if run.returncode != 0:
            return {}, f"the baker failed: {_error(run.stderr)}"
        return {WORLD_DIR + p.name: p.read_bytes() for p in sorted(Path(out).glob("*.glb"))}, None


def _error(stderr: str) -> str:
    """What went wrong, from what Node.js wrote: the error, not its stack."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in lines:
        if re.match(r"^\w*Error\b", line):
            return line
    lines = [line for line in lines if not line.startswith(("at ", "Node.js v"))]
    return lines[-1] if lines else "no message"
