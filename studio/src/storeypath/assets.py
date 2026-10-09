"""Files that live outside the Python package: the format spec (``spec/``) and the
viewer (``viewer/``), both from StoreyPath Viewer, a repository of its own.

In a source checkout they are read from its submodule (``storeypath-viewer/``).
Built wheels carry a copy under ``storeypath/_bundled/`` (see ``hatch_build.py``).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
VIEWER_REPO = REPO_ROOT / "storeypath-viewer"  # the submodule


def asset_dir(name: str) -> Path:
    if (REPO_ROOT / "studio" / "pyproject.toml").is_file():
        for in_repo in (VIEWER_REPO / name, REPO_ROOT / name):
            if in_repo.is_dir():
                return in_repo
    bundled = Path(str(resources.files("storeypath") / "_bundled" / name))
    if bundled.is_dir():
        return bundled
    raise FileNotFoundError(f"cannot find the {name}/ folder: in a checkout, run "
                            "`git submodule update --init`; else reinstall StoreyPath Studio")


def format_spec() -> str:
    return (asset_dir("spec") / "FORMAT.md").read_text(encoding="utf-8")
