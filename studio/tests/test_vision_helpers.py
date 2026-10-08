"""Studio and its GPU helpers (vision.py): several model servers named in
STOREYPATH_VISION_URL, the key sent to each, questions spread over the ones that
answer, one that fails left out for a while and tried again. The helpers here are
small fake servers on free ports."""

import json
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import storeypath.vision as vision
from storeypath.llm import ModelUnavailable
from storeypath.vision import OUTLINES, InWords, VisionModel, VisionUnavailable

FIELDS = {"outline": OUTLINES}
ANSWER = {"outline": "exactly one room"}


class FakeHelper:
    """An OpenAI-compatible model server: lists ``model``, answers every question with
    ANSWER after ``delay``; ``state`` "ok", "down" (503 to everything, as while
    loading) or "bad" (400 to questions); with ``key``, a call without it gets 401."""

    def __init__(self, model="gemma-4-31B-it-Q4_K_M", key=None, delay=0.0, tls=None):
        self.model, self.key, self.delay, self.state = model, key, delay, "ok"
        self.asked = self.busy = self.most = 0
        self.keys: list[str | None] = []
        self.lock = threading.Lock()
        helper = self

        class Handler(BaseHTTPRequestHandler):
            def _refused(self):
                with helper.lock:
                    helper.keys.append(self.headers.get("Authorization"))
                if helper.state == "down":
                    return self._reply(503, {"error": {"message": "Loading model"}})
                if helper.key and self.headers.get("Authorization") != f"Bearer {helper.key}":
                    return self._reply(401, {"error": {"message": "Invalid API Key"}})
                return False

            def do_GET(self):
                if self._refused() is False:
                    self._reply(200, {"object": "list", "data": [{"id": helper.model, "object": "model"}]})

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if self._refused() is not False:
                    return
                if helper.state == "bad":
                    return self._reply(400, {"error": {"message": "the request exceeds the context"}})
                with helper.lock:
                    helper.asked += 1
                    helper.busy += 1
                    helper.most = max(helper.most, helper.busy)
                time.sleep(helper.delay)
                with helper.lock:
                    helper.busy -= 1
                self._reply(200, {"model": helper.model,
                                  "choices": [{"message": {"content": json.dumps(ANSWER)}}]})

            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return True

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if tls is not None:
            self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.url = f"{'https' if tls else 'http'}://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


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
def quick(monkeypatch):
    """Helpers left out a moment, not seconds."""
    monkeypatch.setattr(vision, "BACKOFF_S", 0.3)
    monkeypatch.setattr(vision, "RECHECK_S", 0.6)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _until(check, within=3.0):
    end = time.monotonic() + within
    while not check():
        assert time.monotonic() < end, "not in time"
        time.sleep(0.02)


def test_helpers_are_named_by_commas_or_spaces():
    model = VisionModel(url=" https://gpu1:8105/v1/, http://gpu2:8105/v1  gpu3:8105 ", parallel=3)
    assert [h.url for h in model.helpers] == ["https://gpu1:8105/v1", "http://gpu2:8105/v1"]
    assert model.ignored == ["gpu3:8105"] and "gpu3:8105" in model.failed
    assert model.parallel == 6  # three at once on each
    assert any("gpu3:8105: not used" in line for line in model.describe())
    assert VisionModel(url="").available() is False and VisionModel(url="").describe() == []


def test_questions_are_spread_over_the_helpers_each_taking_its_own_number_at_once(helpers):
    a, b = helpers(delay=0.05), helpers(delay=0.05)
    model = VisionModel(url=f"{a.url},{b.url}", parallel=2)
    assert model.available() and model.name == "gemma-4-31B-it-Q4_K_M"
    assert model.parallel == 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(lambda _: model.ask(b"png", "?", FIELDS), range(24)))
    assert answers == [ANSWER] * 24
    assert a.asked + b.asked == 24 and a.asked >= 6 and b.asked >= 6
    assert a.most == 2 and b.most == 2  # never more than each takes, both kept busy


def test_the_key_goes_with_every_call(helpers):
    a, b = helpers(key="s3cret"), helpers(key="s3cret")
    model = VisionModel(url=f"{a.url} {b.url}", key="s3cret")
    assert model.available()
    for _ in range(4):
        model.ask(b"png", "?", FIELDS)
    InWords(model).ask("system", "user", {})
    assert a.keys and b.keys and set(a.keys + b.keys) == {"Bearer s3cret"}
    assert "with a key" in model.describe()[0]

    wrong = VisionModel(url=a.url, key="guess")
    assert not wrong.available()
    assert "401" in wrong.failed and "STOREYPATH_VISION_KEY" in wrong.failed


def test_a_helper_that_fails_is_left_out_its_questions_answered_by_the_others_and_tried_again(helpers, quick):
    a, b = helpers(), helpers()
    model = VisionModel(url=f"{a.url},{b.url}", parallel=1)
    assert model.available()
    a.state = "down"
    assert model.ask(b"png", "?", FIELDS) == ANSWER  # tried on a first, then on b
    assert a.asked == 0 and b.asked == 1
    posts_to_a = sum(1 for _ in a.keys)
    for _ in range(5):  # a is left out: not even tried
        assert model.ask(b"png", "?", FIELDS) == ANSWER
    assert len(a.keys) == posts_to_a and b.asked == 6
    assert any(a.url in line and "not answering" in line for line in model.describe())

    a.state = "ok"
    time.sleep(0.35)  # its time out is over: asked again
    for _ in range(4):
        assert model.ask(b"png", "?", FIELDS) == ANSWER
    assert a.asked >= 2
    assert any(a.url in line and "answers" in line for line in model.describe())


def test_a_helper_left_out_longer_each_time_it_fails_again(helpers, quick):
    a, b = helpers(), helpers()
    model = VisionModel(url=f"{a.url},{b.url}", parallel=1)
    assert model.available()
    a.state = "down"
    outs = []
    for _ in range(3):
        _until(lambda: model.helpers[0].out_until <= time.monotonic())
        model.ask(b"png", "?", FIELDS)  # a tried again, and fails again
        h = model.helpers[0]
        outs.append(round(h.out_until - time.monotonic(), 1))
    assert outs[0] < outs[1] <= outs[2] <= vision.RECHECK_S  # 0.3, 0.6, then no longer than 0.6


def test_a_helper_still_loading_at_the_start_joins_once_it_answers(helpers, quick):
    a, b = helpers(), helpers()
    b.state = "down"  # loading its model
    model = VisionModel(url=f"{a.url},{b.url}", parallel=1)
    assert model.available()
    assert model.helpers[1].serves is None and "503" in model.helpers[1].error
    b.state = "ok"
    end = time.monotonic() + 3
    while b.asked == 0:
        assert time.monotonic() < end, "b never asked"
        model.ask(b"png", "?", FIELDS)
        time.sleep(0.02)
    assert model.helpers[1].serves is True


def test_a_helper_serving_another_model_is_left_out(helpers):
    a, b = helpers(model="gemma-4-31B-it-Q4_K_M"), helpers(model="another-model")
    model = VisionModel(url=f"{a.url},{b.url}")
    assert model.available() and model.name == "gemma-4-31B-it-Q4_K_M"  # the first decides
    assert model.parallel == 2
    for _ in range(4):
        model.ask(b"png", "?", FIELDS)
    assert a.asked == 4 and b.asked == 0  # its answers would be filed under the wrong name
    assert any(b.url in line and "serves another-model, not gemma-4-31B-it-Q4_K_M" in line
               for line in model.describe())


def test_with_no_helper_answering_vision_is_not_used_and_says_why():
    urls = [f"http://127.0.0.1:{_closed_port()}/v1", f"http://127.0.0.1:{_closed_port()}/v1"]
    model = VisionModel(url=",".join(urls), model="m")
    assert not model.available()
    assert model.failed.startswith("no vision model answers:") and all(u in model.failed for u in urls)
    with pytest.raises(VisionUnavailable):
        model.ask(b"png", "?", FIELDS)
    with pytest.raises(ModelUnavailable):
        InWords(model).ask("system", "user", {})


def test_one_helper_is_still_asked_while_it_fails(helpers, quick):
    # with nothing else to ask, each question tries it (as with one endpoint before)
    a = helpers()
    model = VisionModel(url=a.url)
    assert model.available()
    a.state = "down"
    with pytest.raises(VisionUnavailable, match="503"):
        model.ask(b"png", "?", FIELDS)
    a.state = "ok"
    assert model.ask(b"png", "?", FIELDS) == ANSWER  # at once, though it was left out


def test_a_question_that_cannot_be_answered_leaves_its_helper_in(helpers):
    a, b = helpers(), helpers()
    model = VisionModel(url=f"{a.url},{b.url}", parallel=1)
    assert model.available()
    a.state = b.state = "bad"
    with pytest.raises(VisionUnavailable, match="400"):
        model.ask(b"png", "?", FIELDS)
    assert all(h.out_until == 0 and h.failures == 0 for h in model.helpers)
    a.state = b.state = "ok"
    assert model.ask(b"png", "?", FIELDS) == ANSWER


def test_tls_to_a_helper_is_checked_unless_said_otherwise(helpers, tmp_path, monkeypatch):
    from storeypath.tls import context, studio_certificate

    made = studio_certificate(tmp_path, machine=set())  # self-signed, for 127.0.0.1 and localhost
    a = helpers(tls=context(made.cert, made.key))
    assert a.url.startswith("https://")

    checked = VisionModel(url=a.url)
    assert not checked.available() and "CERTIFICATE_VERIFY_FAILED" in checked.failed

    monkeypatch.setenv("STOREYPATH_VISION_INSECURE", "1")
    insecure = VisionModel(url=a.url)
    assert insecure.available() and insecure.ask(b"png", "?", FIELDS) == ANSWER
    assert "its certificate not checked" in insecure.describe()[1]
    monkeypatch.delenv("STOREYPATH_VISION_INSECURE")

    monkeypatch.setenv("STOREYPATH_VISION_CA", str(made.cert))  # that certificate trusted
    pinned = VisionModel(url=a.url)
    assert pinned.available() and pinned.ask(b"png", "?", FIELDS) == ANSWER
    assert isinstance(pinned._tls, ssl.SSLContext) and pinned._tls.verify_mode == ssl.CERT_REQUIRED
