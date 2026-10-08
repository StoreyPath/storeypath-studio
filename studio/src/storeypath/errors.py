"""What Studio's operations raise that the server answers in its own way."""

from __future__ import annotations


class NotFound(Exception):
    """What was asked for is not there (or not there for the person asking): 404."""


class Busy(Exception):
    """A job is changing the project (reading a drawing, converting, exporting): a
    change saved now would be lost when the job saves its own copy, so none is made."""
