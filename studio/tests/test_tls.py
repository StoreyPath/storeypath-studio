"""Studio over HTTPS (tls.py): a certificate it makes itself, for the names it is reached
by, made again only when it must be; plain HTTP to the same port sent on to https://;
a slow TLS handshake holding up no other connection; `storeypath serve` HTTPS by
default."""

import datetime as dt
import ipaddress
import os
import re
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time

import pytest
from cryptography import x509

from sessions import call
from storeypath import accounts as acc
from storeypath.accounts import Accounts
from storeypath.server import Studio
from storeypath.web import make_server
from storeypath.tls import RENEW_DAYS, VALID_DAYS, context, studio_certificate

PASSWORD = "everyone's password"


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)


def held(cert_path):
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return cert, set(san.get_values_for_type(x509.DNSName)), {str(i) for i in san.get_values_for_type(x509.IPAddress)}


def test_studio_makes_a_certificate_for_the_names_it_is_reached_by(tmp_path, monkeypatch):
    monkeypatch.setenv("STOREYPATH_ALLOWED_HOSTS", "studio.example.org, 10.1.2.3")
    made = studio_certificate(tmp_path, "192.168.7.7", ["Proxy.Example"], machine={"box", "box.local", "172.17.0.5"})
    assert made.made and made.cert == tmp_path / "tls" / "studio-cert.pem"
    assert stat.S_IMODE(made.key.stat().st_mode) == 0o600
    cert, dns, ips = held(made.cert)
    assert {"localhost", "studio.example.org", "proxy.example", "box", "box.local"} <= dns
    assert {"127.0.0.1", "::1", "192.168.7.7", "10.1.2.3", "172.17.0.5"} <= ips
    life = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert dt.timedelta(days=VALID_DAYS) <= life <= dt.timedelta(days=VALID_DAYS + 2)
    assert cert.issuer == cert.subject  # self-signed
    assert isinstance(cert.public_key().curve, type(cert.public_key().curve)) and cert.public_key().key_size == 256
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca  # a server's, not a CA's
    assert re.fullmatch(r"([0-9A-F]{2}:){31}[0-9A-F]{2}", made.fingerprint)


def test_it_is_made_again_only_when_it_must_be(tmp_path):
    first = studio_certificate(tmp_path, "0.0.0.0", [], machine={"box"})
    again = studio_certificate(tmp_path, "0.0.0.0", [], machine={"another-box", "10.9.9.9"})
    # what the machine is called changes by itself (a container made again): not a reason
    assert not again.made and again.fingerprint == first.fingerprint
    asked = studio_certificate(tmp_path, "0.0.0.0", ["studio.lan"], machine={"box"})
    assert asked.made and asked.fingerprint != first.fingerprint
    _, dns, _ = held(asked.cert)
    assert {"studio.lan", "box", "localhost"} <= dns  # the names it had stay on it
    now = dt.datetime.now(dt.timezone.utc)
    soon = now + dt.timedelta(days=VALID_DAYS - RENEW_DAYS + 1)  # near its end
    assert studio_certificate(tmp_path, "0.0.0.0", ["studio.lan"], machine=set(), now=soon).made
    (tmp_path / "tls" / "studio-cert.pem").write_text("not a certificate")
    assert studio_certificate(tmp_path, "0.0.0.0", [], machine=set()).made


@pytest.fixture
def https(tmp_path):
    made = studio_certificate(tmp_path / "data", "127.0.0.1", [], machine=set())
    studio = Studio(tmp_path / "data", model=NoModel(), warm=False)
    accounts = Accounts(tmp_path / "data")
    accounts.add_user("ali", PASSWORD, must_change_password=False)
    srv = make_server(studio, port=0, accounts=accounts, tls=context(made.cert, made.key))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    trusting = ssl.create_default_context(cafile=str(made.cert))  # a browser told to trust it
    yield srv.server_port, trusting, made
    srv.shutdown()
    srv.server_close()


def test_https_with_the_studios_certificate_and_a_secure_cookie(https):
    port, trusting, made = https
    status, me, res = call(port, "POST", "/api/login", {"username": "ali", "password": PASSWORD}, tls=trusting)
    assert status == 200 and "Secure" in res.getheader("Set-Cookie")
    assert call(port, "GET", "/login.html", tls=trusting)[0] == 200
    with socket.create_connection(("127.0.0.1", port)) as raw:  # by name: localhost is on it too
        with trusting.wrap_socket(raw, server_hostname="localhost") as s:
            assert s.version() in ("TLSv1.2", "TLSv1.3")
    with pytest.raises(ssl.SSLCertVerificationError):  # a name not on it is refused by the client
        with socket.create_connection(("127.0.0.1", port)) as raw:
            trusting.wrap_socket(raw, server_hostname="elsewhere.example")


def test_plain_http_to_the_https_port_is_sent_on_to_https(https):
    port, trusting, made = https
    status, body, res = call(port, "GET", "/review.html?p=K7Q2XM")
    assert status == 307 and res.getheader("Location") == f"https://127.0.0.1:{port}/review.html?p=K7Q2XM"
    status, _, res = call(port, "POST", "/api/login", {"username": "ali", "password": PASSWORD})
    assert status == 307 and res.getheader("Set-Cookie") is None  # nothing done over plain HTTP
    status, _, res = call(port, "GET", "/", headers={"Host": "evil.example"})
    assert status == 403 and res.getheader("Location") is None  # sent on only to Studio's own names


def test_a_slow_client_holds_up_no_other(https):
    port, trusting, made = https
    idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(3)]  # say nothing
    half = socket.create_connection(("127.0.0.1", port))
    half.sendall(b"\x16\x03\x01")  # a handshake begun, never finished
    try:
        started = time.monotonic()
        assert call(port, "GET", "/login.html", tls=trusting)[0] == 200
        assert time.monotonic() - started < 3
    finally:
        for s in idle + [half]:
            s.close()


def test_serve_speaks_https_by_default_and_prints_how_to_check_it(tmp_path):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "STOREYPATH_ADMIN_PASSWORD": PASSWORD}
    env.pop("STOREYPATH_ALLOWED_HOSTS", None)
    proc = subprocess.Popen([sys.executable, "-c", "from storeypath.cli import main; main()", "serve",
                             "--data", str(tmp_path / "data"), "--port", "0", "--allowed-host", "studio.lan"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        lines = []
        while not any("https://" in line and "Ctrl+C" in line for line in lines):
            line = proc.stdout.readline()
            assert line, "".join(lines)
            lines.append(line)
        said = "".join(lines)
        port = int(re.search(r"https://127\.0\.0\.1:(\d+)/", said).group(1))
        fingerprint = re.search(r"SHA-256 ([0-9A-F:]{95})", said).group(1)
        assert "studio.lan" in said and "admin" in said  # the first admin, from the environment
        cert = tmp_path / "data" / "tls" / "studio-cert.pem"
        trusting = ssl.create_default_context(cafile=str(cert))
        status, me, res = call(port, "POST", "/api/login", {"username": "admin", "password": PASSWORD}, tls=trusting)
        assert status == 200 and "Secure" in res.getheader("Set-Cookie")
        seen = ssl.get_server_certificate(("127.0.0.1", port))
        der = ssl.PEM_cert_to_DER_cert(seen)
        import hashlib

        assert ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest()) == fingerprint
        assert ipaddress.ip_address("127.0.0.1") in x509.load_pem_x509_certificate(cert.read_bytes()).extensions \
            .get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)
    finally:
        proc.terminate()
        proc.wait(10)
