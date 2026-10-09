"""StoreyPath's finishes (format 0.9): the floors and walls a room may be given, a fixed
set every reader has, read from spec/finishes.json (the one list: the viewers and the Go
module have copies of it, made by spec/finishes.mjs).

A space or zone may name its floor finish and a space its walls' finish (a person sets
them in review: Override.floor_finish, wall_finish); none is its type's default. Studio
accepts only codes of this list, each for what it applies to (a floor or walls).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from .assets import asset_dir

# what a finish's code looks like (the package's schema says so too): a code a later
# version adds is read by a reader of this one, and shown as its type's default
FLOOR_CODE = r"^FLOOR(-[A-Z0-9]+)+$"
WALL_CODE = r"^WALL(-[A-Z0-9]+)+$"
CODE_MAX = 40
APPLIES = ("floor", "wall")


@lru_cache(maxsize=1)
def catalogue() -> dict:
    """The list as spec/finishes.json has it: version, exterior, groups, finishes, defaults."""
    return json.loads((asset_dir("spec") / "finishes.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _by_code() -> dict[str, dict]:
    return {f["code"]: f for f in catalogue()["finishes"]}


def finish(code: str | None) -> dict | None:
    """A finish by its code, or None."""
    return _by_code().get(code) if isinstance(code, str) else None


def default(applies: str, type_: str | None) -> str:
    """The finish a type of room has when given none (an unknown type: as unspecified)."""
    table = catalogue()["defaults"][applies]
    return table.get(str(type_ or "unspecified"), table["unspecified"])


def name(code: str | None) -> str:
    """A finish's English name, for a line of history ("" for none)."""
    f = finish(code)
    return f["name"] if f else (code or "")


def check(code, applies: str) -> str | None:
    """A finish from a request: one of the list's codes for ``applies`` ("floor" or
    "wall"), or None (its type's default). Anything else is a ValueError."""
    if code is None:
        return None
    f = finish(code)
    if f is None or f["applies"] != applies:
        raise ValueError(f"{applies}_finish is a code of StoreyPath's {applies} finishes (as FLOOR-CARPET-NAVY, "
                         f"WALL-PAINT-WHITE), or null for its type's default; {str(code)[:60]!r} is not one")
    return f["code"]


def known(code, applies: str) -> str | None:
    """A code when it is one of the list's for ``applies``; else None (its type's default)."""
    f = finish(code)
    return f["code"] if f is not None and f["applies"] == applies else None


def well_formed(code: str, applies: str) -> bool:
    """Whether a code has the form of a finish for ``applies`` (known or not)."""
    return isinstance(code, str) and len(code) <= CODE_MAX and \
        re.fullmatch(FLOOR_CODE if applies == "floor" else WALL_CODE, code) is not None
