"""Files that live outside the Python package in the repository: the format
spec (``spec/``) and the viewer (``viewer/``).

In a source checkout they are read from the repository. Built wheels carry a
copy under ``storeypath/_bundled/`` (see ``hatch_build.py``).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def asset_dir(name: str) -> Path:
    in_repo = REPO_ROOT / name
    if (REPO_ROOT / "studio" / "pyproject.toml").is_file() and in_repo.is_dir():
        return in_repo
    bundled = Path(str(resources.files("storeypath") / "_bundled" / name))
    if bundled.is_dir():
        return bundled
    raise FileNotFoundError(f"cannot find the {name}/ folder; reinstall StoreyPath Studio")


def format_spec() -> str:
    return (asset_dir("spec") / "FORMAT.md").read_text(encoding="utf-8")
