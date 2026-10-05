"""The llama-server process Studio starts and talks to."""

import os
import signal
import subprocess
import sys
import time

import pytest

from storeypath import llm
from storeypath.llm import LocalModel

# A stand-in for llama-server that logs as much as the real one: a lot while loading
# the model, and more for every request.
FAKE_SERVER = f"""#!{sys.executable}
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])
if os.environ.get("FAKE_SERVER_PID"):
    open(os.environ["FAKE_SERVER_PID"], "w").write(str(os.getpid()))
sys.stderr.write("loading the model " * 10000 + "\\n")
sys.stderr.flush()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        sys.stderr.write("request " * 2000 + "\\n")
        sys.stderr.flush()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{{}}")

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({{"choices": [{{"message": {{"content": json.dumps({{"ok": True}})}}}}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def test_a_server_that_logs_a_lot_keeps_answering(tmp_path, monkeypatch):
    # Its log is read as it is written: a pipe nobody reads fills up (64 KB) and the
    # server stops at its next log line, in the middle of a question.
    program = tmp_path / "llama-server"
    program.write_text(FAKE_SERVER)
    program.chmod(0o755)
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"")
    monkeypatch.setenv("STOREYPATH_LLAMA_SERVER", str(program))
    monkeypatch.setattr(llm, "START_TIMEOUT_S", 10)
    model = LocalModel(model=model_file)
    try:
        for _ in range(40):  # about 640 KB of request logs
            assert model.ask("system", "question", {"type": "object"}, max_tokens=10) == {"ok": True}
    finally:
        model.close()


def test_why_the_server_stopped_is_told(tmp_path, monkeypatch):
    program = tmp_path / "llama-server"
    program.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('loading\\n' * 5000 + "
                       "'error: failed to load model.gguf\\n')\nsys.exit(1)\n")
    program.chmod(0o755)
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"")
    monkeypatch.setenv("STOREYPATH_LLAMA_SERVER", str(program))
    with pytest.raises(llm.ModelUnavailable, match="llama-server stopped(.|\n)*failed to load model.gguf"):
        LocalModel(model=model_file).ask("system", "question", {"type": "object"})


def test_stopping_studio_stops_its_llama_server(tmp_path):
    # `kill` and `docker stop` send SIGTERM, which skips Python's exit handlers unless
    # Studio handles it: llama-server would be left running with the model in memory.
    program = tmp_path / "llama-server"
    program.write_text(FAKE_SERVER)
    program.chmod(0o755)
    (tmp_path / "model.gguf").write_bytes(b"")
    pid_file = tmp_path / "server.pid"
    env = {**os.environ, "STOREYPATH_LLAMA_SERVER": str(program), "STOREYPATH_MODEL": str(tmp_path / "model.gguf"),
           "FAKE_SERVER_PID": str(pid_file)}
    studio = subprocess.Popen([sys.executable, "-c", "from storeypath.cli import main; main()", "serve",
                               "--data", str(tmp_path / "data"), "--port", "0"],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):  # Studio starts the model in the background
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.1)
        server_pid = int(pid_file.read_text())
        studio.send_signal(signal.SIGTERM)
        studio.wait(timeout=15)
        for _ in range(50):
            try:
                os.kill(server_pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        os.kill(server_pid, signal.SIGKILL)
        raise AssertionError("llama-server was left running")
    finally:
        studio.kill()
