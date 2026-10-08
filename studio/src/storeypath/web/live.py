"""What changes in Studio's database, sent to the pages as it happens: one connection
LISTENs on ``storeypath_changes`` (every change NOTIFYs it once it commits, whichever
Studio or command made it: db/store.py) and hands each change to the events hub
(events.py), which sends it on to the streams whose person may see it.

A change ({project, floors, seq, version[, page, locks]}) is read from the history (who,
what, in words: history.describe) and sent as ``change`` once for each floor it changed
(a building's or the project's change, once, to who sees that whole); a floor whose
lock changed hands ({project, locks}), or that someone came to or left, is sent as
``presence``: who is viewing it, who is editing it. A project deleted
({project, deleted}) is sent as ``deleted``. Locks nothing kept for LOCK_IDLE_S are let
go every SWEEP_S (store.sweep_locks), their floors' pages told the same way.

Only projects with a stream open are read about. The connection is made again (after
a pause, up to RECONNECT_MAX_S) when it is lost; what changed meanwhile is not sent
(the pages read what they show again when their stream opens again).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time

import psycopg

from .. import history
from ..db import CHANNEL

WAIT_S = 0.25  # the listening looks for other work this often
SWEEP_S = 30.0
RECONNECT_MAX_S = 10.0

log = logging.getLogger("storeypath.live")


class Live:
    """Studio's one LISTEN, in a thread of its own, from start() until stop()."""

    def __init__(self, events, studio):
        self.events, self.studio = events, studio
        self._todo: queue.SimpleQueue = queue.SimpleQueue()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="storeypath live")
        self.listening = threading.Event()  # (the tests wait for it)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(5)

    def presence(self, code: str, floors) -> None:
        """Who is on these floors to be sent again (someone came or left)."""
        self._todo.put((code, list(floors)))

    # ---- the thread -----------------------------------------------------------------

    def _run(self) -> None:
        pause = 0.5
        while not self._stopped.is_set():
            try:
                with psycopg.connect(self.studio.db.url, autocommit=True, connect_timeout=10) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    self.listening.set()
                    pause, swept = 0.5, time.monotonic()
                    while not self._stopped.is_set():
                        for note in conn.notifies(timeout=WAIT_S):
                            self._heard(note.payload)
                        self._others()
                        if time.monotonic() - swept >= SWEEP_S:
                            swept = time.monotonic()
                            self.studio.store.sweep_locks()
            except Exception as e:  # noqa: BLE001 (lost: made again, the pages told nothing meanwhile)
                self.listening.clear()
                if self._stopped.is_set():
                    return
                log.warning("listening to the database's changes: %s (again in %.1f s)", e, pause)
                self._stopped.wait(pause)
                pause = min(pause * 2, RECONNECT_MAX_S)

    def _others(self) -> None:
        """Presence asked for meanwhile (someone came to a floor, or left it): each floor once."""
        asked: dict[str, set[str]] = {}
        while True:
            try:
                code, floors = self._todo.get_nowait()
            except queue.Empty:
                break
            asked.setdefault(code, set()).update(floors)
        for code, floors in asked.items():
            self._presence(code, floors)

    def _heard(self, payload: str) -> None:
        try:
            said = json.loads(payload)
            code = said["project"]
        except (ValueError, KeyError, TypeError):
            return
        if not self.events.watching(code):
            return
        try:
            if said.get("deleted"):
                self.events.publish(code, "deleted", {"project": code}, ("any", None))
                return
            if said.get("seq") is not None:
                self._change(code, said)
            if said.get("locks"):
                self._presence(code, said["locks"])
        except Exception:  # noqa: BLE001 (one change not sent: the next ones are)
            log.exception("sending a change of %s on", code)

    def _change(self, code: str, said: dict) -> None:
        row = self.studio.store.history_row(code, said["seq"])
        if row is None:
            return
        data = {"seq": row["seq"], "version": row["version"], "at": row["at"], "who": history.person(row["who"]),
                "part": row["part"], "kind": row["kind"], "floors": row["floors"], "targets": row["targets"],
                "line": history.describe(row), "undoes": row.get("undoes"), "redoes": row.get("redoes"),
                "page": said.get("page")}
        if row["floors"]:
            for f in row["floors"]:
                self.events.publish(code, "change", {**data, "floor": f}, ("floor", f))
        elif row["part"] == "building" and row["targets"]:
            self.events.publish(code, "change", {**data, "floor": None}, ("building", row["targets"][0]))
        else:
            self.events.publish(code, "change", {**data, "floor": None}, ("project", None))

    def _presence(self, code: str, floors) -> None:
        if not self.events.watching(code):
            return
        locks = self.studio.store.locks(code, floors)
        for f in sorted(set(floors)):
            self.events.publish(code, "presence", self.events.presence(code, f, locks.get(f)), ("floor", f))
