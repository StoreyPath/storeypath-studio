"""One editor a floor at a time (db/store.py, Review's calls): the first change a person
makes to a floor takes its lock; another person's change is refused (423, naming who
holds it, since when) until they leave it, let it go, leave it alone for LOCK_IDLE_S (a
page open on it keeps it), or an admin takes it over (recorded). Project-wide steps hold
the project's step lock, in the database: another Studio on it is told it is busy."""

import time

import pytest

from people import Team
from storeypath import accounts as acc
from storeypath.db import Database
from storeypath.db import store as store_module
from storeypath.db.store import StepLock
from storeypath.web import events as events_module


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.setattr("storeypath.review.BUSY_WAIT_S", 0.2)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def name(team, space_id, who, text):
    return team(who, "POST", f"objects/{space_id}", {"correction": {"name": text}})


def test_the_first_change_takes_the_floor_and_others_are_refused_naming_who(team):
    s0, s1 = (r.id for r in team.spaces(team.hq0)[:2])
    assert team("khalid", "GET", f"floors/{team.hq0}")[1]["lock"] is None
    status, _ = name(team, s0, "khalid", "Board room")
    assert status == 200
    lock = team("sara", "GET", f"floors/{team.hq0}")[1]["lock"]
    assert lock["who"]["name"] == "Khalid Engineer" and lock["floor"] == team.hq0 and "session" not in lock
    # sara may change the floor, but khalid is editing it: nothing changes
    status, refused = name(team, s1, "sara", "Mine")
    assert status == 423
    assert refused["error"].startswith("Khalid Engineer is editing this floor (since ")
    assert "you can look; you can edit when they are done" in refused["error"]
    assert refused["locked"]["who"]["username"] == "khalid" and refused["locked"]["floor"] == team.hq0
    assert team.studio.workspace(team.code).overrides.get(s1) is None
    for refused_call in (("POST", f"floors/{team.hq0}/items", {"type": "DESK-JUNIOR", "x": 2, "y": 2}),
                         ("POST", f"floors/{team.hq0}/edits", {"add": {"wall": [[10, 1], [10, 5]]}}),
                         ("POST", f"floors/{team.hq0}/convert", {})):
        assert team("sara", *refused_call)[0] == 423, refused_call
    # the other floors are hers to change; khalid goes on changing his
    other = team.spaces(team.hq1)[0].id
    assert name(team, other, "sara", "Hers")[0] == 200
    assert name(team, s1, "khalid", "Still his")[0] == 200
    assert team("khalid", "POST", f"objects/{other}", {"correction": {"name": "No"}})[0] == 423


def test_an_item_carried_needs_both_floors(team):
    status, item = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "DESK-JUNIOR", "x": 12, "y": 4})
    assert status == 200
    assert name(team, team.spaces(team.hq1)[0].id, "sara", "Sara's floor")[0] == 200
    status, refused = team("khalid", "POST", f"items/{item['id']}", {"floor_id": team.hq1})
    assert status == 423 and refused["locked"]["who"]["name"] == "Sara Ahmed"
    assert team.studio.workspace(team.code).items[item["id"]].floor_id == team.hq0


def test_done_editing_lets_the_floor_go(team):
    s0 = team.spaces(team.hq0)[0].id
    assert name(team, s0, "khalid", "His")[0] == 200
    assert team("sara", "POST", f"floors/{team.hq0}/release", {}) == (200, {"released": False})  # not hers
    assert name(team, s0, "sara", "Hers")[0] == 423
    assert team("khalid", "POST", f"floors/{team.hq0}/release", {}) == (200, {"released": True})
    assert team("khalid", "GET", f"floors/{team.hq0}")[1]["lock"] is None
    assert name(team, s0, "sara", "Hers")[0] == 200
    assert team("khalid", "GET", f"floors/{team.hq0}")[1]["lock"]["who"]["name"] == "Sara Ahmed"


def test_a_floor_left_alone_is_free_again_unless_its_page_is_open(team, monkeypatch):
    monkeypatch.setattr(store_module, "LOCK_IDLE_S", 2)
    monkeypatch.setattr(events_module, "HEARTBEAT_S", 0.3)
    s0 = team.spaces(team.hq0)[0].id
    page = team.stream("khalid", team.hq0)  # khalid's page, open on the floor
    try:
        assert page.next()[0] == "heartbeat"
        assert name(team, s0, "khalid", "His")[0] == 200
        time.sleep(3)  # longer than LOCK_IDLE_S: his page's heartbeats kept it
        assert name(team, s0, "sara", "Hers")[0] == 423
    finally:
        page.close()
    time.sleep(2.5)  # gone from the floor, without saying so: let go once nothing kept it
    assert team("sara", "GET", f"floors/{team.hq0}")[1]["lock"] is None
    assert name(team, s0, "sara", "Hers")[0] == 200
    with team.studio.db.transaction() as conn:  # (as though long ago)
        conn.execute("UPDATE floor_locks SET last_seen = now() - interval '1 hour'")
    assert team.studio.store.sweep_locks() == {team.code: [team.hq0]}
    with team.studio.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM floor_locks").fetchone()[0] == 0


def test_an_admin_takes_a_floor_over_and_that_is_recorded(team):
    s0 = team.spaces(team.hq0)[0].id
    assert name(team, s0, "khalid", "His")[0] == 200
    assert team("sara", "POST", f"floors/{team.hq0}/take-over", {})[0] == 403  # only an admin
    status, done = team("boss", "POST", f"floors/{team.hq0}/take-over", {})
    assert status == 200 and done["from"]["name"] == "Khalid Engineer"
    assert team("khalid", "GET", f"floors/{team.hq0}")[1]["lock"]["who"]["name"] == "The Boss"
    assert name(team, s0, "khalid", "Mine again")[0] == 423
    assert name(team, s0, "boss", "The boss's")[0] == 200
    row = next(r for r in team.studio.store.history(team.code) if r["kind"] == "take over")
    assert row["part"] == "floor" and row["floors"] == [team.hq0] and row["who"]["name"] == "The Boss"
    assert row["before"]["lock"]["name"] == "Khalid Engineer"
    lines = team("khalid", "GET", f"history?floor={team.hq0}")[1]["entries"]
    assert any(e["line"] == "took over editing the floor from Khalid Engineer" for e in lines)
    audit = team.accounts.audit_tail(5)
    assert audit[0]["action"] == "floor taken over" and audit[0]["target"] == team.hq0 and audit[0]["was"] == "khalid"


def test_project_wide_steps_hold_the_project_in_the_database(team):
    """Another Studio on the same database working on the project (its step lock held):
    Review's changes here are refused as busy, as when a job here works on it; a file is
    not opened into it."""
    s0 = team.spaces(team.hq0)[0].id
    other = Database(team.studio.db.url, migrate=False)  # another Studio's connections
    held = StepLock(other, team.code)
    try:
        assert held.acquire(timeout=1)
        status, busy = name(team, s0, "khalid", "During the step")
        assert status == 409 and busy["busy"] is True and "a job is working on this project" in busy["error"]
        assert not team.studio._changing(team.code).acquire(blocking=False)  # here, the step waits too
        status, _ = team("khalid", "POST", "delete", {"confirm": "Demo Campus"})
        assert status == 400 and team.studio.store.exists(team.code)
    finally:
        held.release()
        other.close()
    assert name(team, s0, "khalid", "After the step")[0] == 200
    lock = team.studio._changing(team.code)
    with lock:  # re-entrant here, one database lock however deep
        with lock:
            pass
        assert not StepLock(team.studio.db, team.code).acquire(blocking=False)
    again = StepLock(team.studio.db, team.code)
    assert again.acquire(blocking=False)  # let go once the outermost let it go
    again.release()
