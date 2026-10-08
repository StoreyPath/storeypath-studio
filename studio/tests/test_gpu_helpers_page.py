"""The GPU helpers page (an admin's): the vision model's helpers kept in the database —
each one's address, key, whether it is used, the questions it takes at once — used
without a restart; STOREYPATH_VISION_URL and its key where Studio starts from while the
database has none; how each helper is (and one serving another model, left out); a
sample room sent to one to try it. The helpers are small fake servers on free ports."""

import pytest

from people import Team
from storeypath import accounts as acc
from storeypath.server import Studio
from storeypath.vision import sample_room_png
from test_vision_helpers import FakeHelper

MODEL = "gemma-4-31B-it-Q4_K_M"


@pytest.fixture
def helpers():
    made = []

    def make(**kw):
        made.append(FakeHelper(**kw))
        return made[-1]

    yield make
    for h in made:
        h.close()


@pytest.fixture
def team(tmp_path, monkeypatch, helpers):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)
    first = helpers(key="first-key")
    monkeypatch.setenv("STOREYPATH_VISION_URL", first.url)
    monkeypatch.setenv("STOREYPATH_VISION_KEY", "first-key")
    monkeypatch.setenv("STOREYPATH_VISION_PARALLEL", "3")
    monkeypatch.delenv("STOREYPATH_VISION_MODEL", raising=False)
    t = Team(tmp_path / "data")
    t.first = first
    yield t
    t.close()


def test_the_page_starts_from_the_environment_and_says_how_each_helper_is(team):
    assert team("khalid", "GET", "/api/admin/helpers")[0] == 403  # an admin's
    status, page = team("boss", "GET", "/api/admin/helpers")
    assert status == 200 and page["from"] == "environment" and page["model"] == MODEL
    (h,) = page["helpers"]
    assert h == {"url": team.first.url, "key": True, "enabled": True, "parallel": 3, "state": "answers",
                 "error": None, "models": [MODEL], "busy": 0}
    assert "first-key" not in str(page)  # never the key itself


def test_helpers_saved_are_used_at_once_and_by_another_studio_on_the_database(team, helpers, tmp_path):
    second = helpers(key="second-key")
    status, page = team("boss", "POST", "/api/admin/helpers", {"helpers": [
        {"url": team.first.url, "enabled": True, "parallel": 2},  # its key kept, as it was not sent
        {"url": second.url + "/", "key": "second-key", "enabled": True, "parallel": 4}]})
    assert status == 200 and page["from"] == "database"
    assert [(h["url"], h["key"], h["parallel"], h["state"]) for h in page["helpers"]] == \
        [(team.first.url, True, 2, "answers"), (second.url, True, 4, "answers")]
    vision = team.studio.vision  # the same model, its helpers replaced: no restart
    assert [(h.url, h.key, h.parallel) for h in vision.helpers] == \
        [(team.first.url, "first-key", 2), (second.url, "second-key", 4)]
    assert team.accounts.audit_tail(1)[0]["action"] == "gpu helpers changed"
    # another Studio on the same database starts with them, and follows a change
    other = Studio(team.studio.data, model=team.studio.model, warm=False, first_start=False)
    assert [h.url for h in other.vision.helpers] == [team.first.url, second.url]
    team("boss", "POST", "/api/admin/helpers", {"helpers": [
        {"url": team.first.url, "enabled": False, "parallel": 2}, {"url": second.url, "enabled": True, "parallel": 4}]})
    assert other.reload_helpers() is True and [h.url for h in other.vision.helpers] == [second.url]
    assert other.reload_helpers() is False  # (nothing changed since)
    _, page = team("boss", "GET", "/api/admin/helpers")
    assert [(h["enabled"], h["state"]) for h in page["helpers"]] == [(False, "off"), (True, "answers")]
    # what is refused, nothing changed
    for wrong in ({"helpers": [{"url": "ftp://gpu"}]}, {"helpers": [{"url": second.url}, {"url": second.url}]},
                  {"helpers": [{"url": second.url, "parallel": 0}]}, {"helpers": [{"url": second.url, "enabled": "yes"}]}):
        assert team("boss", "POST", "/api/admin/helpers", wrong)[0] == 400, wrong
    assert [h.url for h in team.studio.vision.helpers] == [second.url]


def test_a_helper_serving_another_model_is_shown_left_out(team, helpers):
    other = helpers(model="qwen3-vl-8b", key="first-key")
    status, page = team("boss", "POST", "/api/admin/helpers", {"helpers": [
        {"url": team.first.url, "enabled": True, "parallel": 2},
        {"url": other.url, "key": "first-key", "enabled": True, "parallel": 2}]})
    assert status == 200 and page["model"] == MODEL
    assert [(h["state"], h["models"]) for h in page["helpers"]] == [("answers", [MODEL]),
                                                                    ("other model", ["qwen3-vl-8b"])]
    assert page["helpers"][1]["error"] == f"serves qwen3-vl-8b, not {MODEL}"


def test_the_test_button_sends_a_sample_room(team, helpers):
    status, tried = team("boss", "POST", "/api/admin/helpers/test", {"url": team.first.url})
    assert status == 200 and tried["ok"] is True and tried["model"] == MODEL
    assert tried["answer"] == {"outline": "exactly one room"} and tried["seconds"] >= 0
    assert team.first.asked == 1 and team.first.keys[-1] == "Bearer first-key"  # its own key, as kept
    stranger = helpers(key="its-own-key")
    status, tried = team("boss", "POST", "/api/admin/helpers/test", {"url": stranger.url, "key": "wrong"})
    assert status == 200 and tried["ok"] is False and "401" in tried["error"]
    status, tried = team("boss", "POST", "/api/admin/helpers/test", {"url": stranger.url, "key": "its-own-key"})
    assert tried["ok"] is True
    assert team("sara", "POST", "/api/admin/helpers/test", {"url": stranger.url})[0] == 403


def test_the_sample_room_is_a_png():
    png = sample_room_png(64)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and b"IHDR" in png and png.endswith(b"IEND\xaeB`\x82")
