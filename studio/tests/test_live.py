"""What changes, sent to the pages as it happens (web/live.py): Studio LISTENs once on
the database's changes and sends each on the streams of the people who may see it (a
floor's, to who may see that floor), with who made it and what it was in words; who is
on each floor (viewing it, editing it) as they come and go."""

import pytest

from people import Team, listening
from storeypath import accounts as acc
from storeypath.web import events as events_module


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.setattr(events_module, "HEARTBEAT_S", 0.5)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    t = Team(tmp_path / "data")
    yield t
    t.close()


def changes(kind, data):
    return kind == "change"


def test_a_change_goes_to_who_may_see_its_floor(team):
    vera = team.stream("vera", team.hq0)  # view on HQ's ground floor alone
    sara = team.stream("sara")  # the project page, the whole project
    try:
        assert vera.next()[0] == sara.next()[0] == "heartbeat"
        listening(team)
        # a change on a floor vera may not see: sara hears it, vera does not
        hq1 = team.spaces(team.hq1)[0]
        assert team("khalid", "POST", f"objects/{hq1.id}", {"ignored": True}, page="khalids-page")[0] == 200
        got = sara.until(changes)[-1][1]
        assert got["floor"] == team.hq1 and got["kind"] == "delete" and got["page"] == "khalids-page"
        # one on hers: both hear it, who made it and what it was
        office = next(r for r in team.spaces(team.hq0) if r.name == "OFFICE")
        assert team("khalid", "POST", f"objects/{office.id}", {"ignored": True})[0] == 200
        got = vera.until(changes)
        assert [k for k, d in got if k == "change"] == ["change"]  # the HQ first floor's never came
        seen = got[-1][1]
        assert seen["floor"] == team.hq0 and seen["targets"] == [office.id]
        assert seen["who"]["name"] == "Khalid Engineer" and seen["who"]["id"] == team.users["khalid"].id
        assert seen["line"] == f"deleted {office.name} {office.number}" and seen["page"] is None
        assert sara.until(changes)[-1][1]["seq"] == seen["seq"]
        # an item carried from one floor to another: once for each floor, to who sees it
        _, item = team("khalid", "POST", f"floors/{team.hq0}/items", {"type": "DESK-JUNIOR", "x": 12, "y": 4})
        vera.until(lambda k, d: k == "change" and d["kind"] == "add")
        assert team("khalid", "POST", f"items/{item['id']}", {"floor_id": team.hq1})[0] == 200
        carried = vera.until(lambda k, d: k == "change" and d["kind"] == "carry")[-1][1]
        assert carried["floor"] == team.hq0 and carried["line"] == f"carried Junior staff desk …-{item['id'][-7:]} to Floor 1"
        floors = {sara.until(lambda k, d: k == "change" and d["kind"] == "carry")[-1][1]["floor"],
                  sara.until(lambda k, d: k == "change" and d["kind"] == "carry")[-1][1]["floor"]}
        assert floors == {team.hq0, team.hq1}
    finally:
        vera.close()
        sara.close()


def test_a_stream_on_a_floor_the_person_may_not_see_is_not_there(team):
    status = team("vera", "GET", f"events?floor={team.hq1}")[0]
    assert status == 404


def test_who_is_on_a_floor_comes_and_goes(team):
    sara = team.stream("sara")  # the project page: who is on which floor
    try:
        assert sara.next()[0] == "heartbeat"
        listening(team)
        vera = team.stream("vera", team.hq0)
        came = sara.until(lambda k, d: k == "presence" and d["viewing"])[-1][1]
        assert came["floor"] == team.hq0 and [p["name"] for p in came["viewing"]] == ["Vera Viewer"]
        assert came["editing"] is None
        khalid = team.stream("khalid", team.hq0)
        both = sara.until(lambda k, d: k == "presence" and len(d["viewing"]) == 2)[-1][1]
        assert [p["name"] for p in both["viewing"]] == ["Khalid Engineer", "Vera Viewer"]
        # vera's page, opened now, is told at once who is there
        late = team.stream("vera", team.hq0)
        assert late.next()[0] == "heartbeat"
        first = late.until(lambda k, d: k == "presence")[-1][1]
        assert first["floor"] == team.hq0 and {p["name"] for p in first["viewing"]} == {"Khalid Engineer",
                                                                                        "Vera Viewer"}
        late.close()
        # khalid's first change: he is editing it
        office = team.spaces(team.hq0)[0]
        assert team("khalid", "POST", f"objects/{office.id}", {"correction": {"name": "Board"}})[0] == 200
        editing = vera.until(lambda k, d: k == "presence" and d["editing"])[-1][1]
        assert editing["editing"]["who"]["name"] == "Khalid Engineer" and editing["editing"]["since"]
        # done editing: nobody is
        assert team("khalid", "POST", f"floors/{team.hq0}/release", {})[1] == {"released": True}
        assert vera.until(lambda k, d: k == "presence" and d["editing"] is None)[-1][1]["floor"] == team.hq0
        khalid.close()
        gone = sara.until(lambda k, d: k == "presence" and len(d["viewing"]) == 1)[-1][1]
        assert [p["name"] for p in gone["viewing"]] == ["Vera Viewer"]
        vera.close()
        sara.until(lambda k, d: k == "presence" and not d["viewing"])
    finally:
        sara.close()


def test_a_floor_read_again_is_a_job_followed_on_the_stream_and_a_change(team):
    bob = team.stream("bob", team.an0)
    try:
        assert bob.next()[0] == "heartbeat"
        listening(team)
        status, job = team("bob", "POST", f"floors/{team.an0}/convert", {})
        assert status == 200
        seen, done, read = [], None, None
        while done is None or read is None:  # (the change is saved before the job is done)
            seen = bob.until(lambda k, d: k == "job" and d["state"] == "done" or k == "change" and d["kind"] == "read")
            kind, data = seen[-1]
            done, read = (data, read) if kind == "job" else (done, data)
        assert done["id"] == job["id"] and done["result"]["summaries"]
        assert read["floor"] == team.an0 and read["line"] == "read the floor's drawing again"
        assert read["who"]["name"] == "Bob Annex"
    finally:
        bob.close()


def test_a_project_deleted_is_said_on_its_streams(team):
    sara = team.stream("sara")
    try:
        assert sara.next()[0] == "heartbeat"
        listening(team)
        assert team("khalid", "POST", "delete", {"confirm": "Demo Campus"})[0] == 200
        assert sara.until(lambda k, d: k == "deleted")[-1] == ("deleted", {"project": team.code})
    finally:
        sara.close()
