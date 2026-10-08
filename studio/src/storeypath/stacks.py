"""Vertical stacks: which lift, stairs, escalator or ramp on one floor is the same one
on the others (format 0.8: the spaces' ``stack``, and navigation's ways between floors).

Drawings often leave lifts and stairs out, or draw them a little apart from floor to
floor, so the link is worked out from what is there, and a person may set it:

- two such spaces on different floors of a building are linked when they have the
  same object code (conversion gives a lift that lines up with one on another floor
  its code), or when they are of the same type and their footprints, in the
  building's own frame, overlap by at least STACK_OVERLAP of the smaller one;
- a person's link wins: a space linked by hand to another (``Override.stack``: that
  space's ID) is linked to it, and to nothing else by itself; one set as not linked
  (``Override.stack`` = NOT_LINKED) stands alone. Others may still be linked to it by
  hand.

What is linked, through any number of floors, is one stack. Its key is the ID of
its space on the lowest floor it serves (the lowest ID there): the same on every floor
it serves, no other stack's, and kept as long as that space is (and none is linked
below it).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely import STRtree
from shapely.geometry import shape

from .types import SpaceType

STACK_TYPES = frozenset({SpaceType.ELEVATOR.value, SpaceType.STAIRS.value, SpaceType.ESCALATOR.value,
                         SpaceType.RAMP.value})
STACK_OVERLAP = 0.3  # footprints on two floors overlapping by this share of the smaller one: one stack
NOT_LINKED = ""  # Override.stack: a person said this one is linked to no other floor's


def object_code(object_id: str) -> str:
    return object_id.rsplit("-", 1)[1]


def floor_of(object_id: str) -> str:
    return object_id.rsplit("-", 1)[0]


@dataclass
class Stack:
    """One lift, stairs, escalator or ramp through the floors it serves."""

    key: str  # the ID of its space on the lowest floor it serves
    type: str  # the type of most of its spaces (the lowest one's on a tie)
    members: list[str] = field(default_factory=list)  # its spaces' IDs, by ID
    how: dict[str, str] = field(default_factory=dict)  # space ID -> "code", "overlap", "person" or "alone"

    @property
    def floors(self) -> list[str]:
        return sorted({floor_of(m) for m in self.members})


def building_floors(ws, building_id: str) -> list[str]:
    """A building's floor IDs, lowest first."""
    return [fid for *_, fid in ws.iter_floors() if fid.startswith(building_id + "-")]


def stack_spaces(ws, building_id: str) -> dict[str, tuple]:
    """The spaces of a building that may be in a stack: active spaces of a vertical type
    (as corrected), not ignored. ID -> (its type, its shape)."""
    out = {}
    for fid in building_floors(ws, building_id):
        for r in ws.floor_objects(fid):
            if r.kind != "space" or not r.geometry:
                continue
            eff = ws.effective(r)
            if eff["type"] in STACK_TYPES and not eff["ignored"]:
                out[r.id] = (eff["type"], shape(r.geometry))
    return out


def stacks(ws, building_id: str) -> list[Stack]:
    """The stacks of a building, each with its spaces and how each came to be in it.
    Every lift, stairs and escalator space is in one (alone, when nothing links it);
    a ramp only with a space on another floor (a ramp on one floor is a slope on it)."""
    spaces = stack_spaces(ws, building_id)
    order = {f: n for n, f in enumerate(building_floors(ws, building_id))}
    ids = sorted(spaces)
    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    said = {i: ws.overrides[i].stack for i in ids if i in ws.overrides and ws.overrides[i].stack is not None}
    how = {}
    for i, target in sorted(said.items()):  # a person's links
        if target != NOT_LINKED and target in spaces and floor_of(target) != floor_of(i):
            union(i, target)
            how[i] = "person"
        else:
            how[i] = "alone"
    automatic = [i for i in ids if i not in said]
    by_code: dict[str, list[str]] = {}
    for i in automatic:
        by_code.setdefault(object_code(i), []).append(i)
    for same in by_code.values():
        for a in same[1:]:
            if floor_of(a) != floor_of(same[0]):
                union(same[0], a)
                how.setdefault(a, "code")
                how.setdefault(same[0], "code")
    if automatic:
        shapes = [spaces[i][1] for i in automatic]
        tree = STRtree(shapes)
        for n, a in enumerate(automatic):
            for m in sorted(int(k) for k in tree.query(shapes[n], predicate="intersects")):
                b = automatic[m]
                if m <= n or floor_of(a) == floor_of(b) or spaces[a][0] != spaces[b][0]:
                    continue
                smaller = min(shapes[n].area, shapes[m].area)
                if smaller > 0 and shapes[n].intersection(shapes[m]).area >= STACK_OVERLAP * smaller:
                    if find(a) != find(b):
                        how.setdefault(a, "overlap")
                        how.setdefault(b, "overlap")
                    union(a, b)
    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        types = [spaces[i][0] for i in members]
        kind = max(sorted(set(types), key=lambda t: types.index(t)), key=types.count)
        if kind == SpaceType.RAMP.value and len({floor_of(i) for i in members}) < 2:
            continue
        key = min(members, key=lambda i: (order[floor_of(i)], i))
        out.append(Stack(key=key, type=kind, members=sorted(members), how={i: how.get(i, "alone") for i in members}))
    return sorted(out, key=lambda s: (order[floor_of(s.key)], s.key))


def stack_of(ws, building_id: str) -> dict[str, str]:
    """Space ID -> the key of its stack, for the spaces of a building in one."""
    return {m: s.key for s in stacks(ws, building_id) for m in s.members}
