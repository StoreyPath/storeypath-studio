"""What Studio's operations raise that the server answers in its own way."""

from __future__ import annotations


class NotFound(Exception):
    """What was asked for is not there (or not there for the person asking): 404."""


class Busy(Exception):
    """A job is changing the project (reading a drawing, converting, exporting): a
    change saved now would be lost when the job saves its own copy, so none is made."""


class Locked(Exception):
    """Another person is editing the floor (they hold its lock): nothing was changed,
    423. ``holder``: who, and since when ({floor, who: {id, username, name}, since})."""

    def __init__(self, message: str, holder: dict):
        super().__init__(message)
        self.holder = holder


class Conflict(Exception):
    """What was asked cannot be done as things are now (nothing to undo; what it would
    undo was changed since by someone): 409, with what the page needs to say so."""

    def __init__(self, message: str, **more):
        super().__init__(message)
        self.more = more
