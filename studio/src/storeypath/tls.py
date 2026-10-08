"""Studio over HTTPS, with a certificate of its own.

``storeypath serve`` speaks HTTPS unless told ``--http``. With ``--cert`` and ``--key``
it uses the organization's certificate; without them it makes one the first time, in
``<data>/tls/`` (its owner's alone: the folder 0700, its files 0600): an EC P-256 key
and a self-signed certificate valid for VALID_DAYS, for the names Studio is reached
by — localhost and the loopback
addresses, the address it is bound to, every name and address given with
--allowed-host or STOREYPATH_ALLOWED_HOSTS, and, as found when it is made, this
machine's name and addresses. Browsers warn once about a certificate nobody vouches
for; its SHA-256 fingerprint, printed at start, is what to compare the browser's with.

It is made again (never one given with --cert) when it is near its end (RENEW_DAYS)
or lacks a name now asked for: one given with --allowed-host, or the bound address.
What the machine is called and its addresses as found change on their own (a
container's name and address are new each time it is made): they go on a new
certificate, but do not by themselves make one, which would make browsers warn again.
To reach Studio by an address or name not on it, give it with --allowed-host.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path

FOLDER = "tls"
CERT_FILE = "studio-cert.pem"
KEY_FILE = "studio-key.pem"
VALID_DAYS = 825  # what browsers accept for a server's certificate
RENEW_DAYS = 30  # made again this long before its end
ISSUER = "StoreyPath Studio"


@dataclass
class Certificate:
    cert: Path
    key: Path
    fingerprint: str  # SHA-256, as browsers show it: AB:CD:…
    made: bool  # made now (else the one there was)
    names: list[str]  # what it is for (its subject alternative names)


def context(cert: Path, key: Path | None = None) -> ssl.SSLContext:
    """A server's TLS context (TLS 1.2 and later) with this certificate and key."""
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(cert, key)
    return tls


def fingerprint(cert: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    c = x509.load_pem_x509_certificate(Path(cert).read_bytes())
    return ":".join(f"{b:02X}" for b in c.fingerprint(hashes.SHA256()))


def _split(names) -> tuple[set[str], set]:
    """Host names (lower case, as a certificate holds them) and addresses, apart."""
    dns, ips = set(), set()
    for n in names:
        n = (n or "").strip().strip("[]").lower()
        if not n or n == "*" or n in ("0.0.0.0", "::"):
            continue
        try:
            ips.add(ipaddress.ip_address(n.split("%")[0]))
            continue
        except ValueError:
            pass
        try:
            n.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            continue
        if all(part and len(part) <= 63 for part in n.split(".")):
            dns.add(n)
    return dns, ips


def machine_names() -> set[str]:
    """This machine's name, and its addresses as found now (none of it is sent anywhere:
    a UDP socket "connected" only to learn the address a packet would leave from)."""
    out = set()
    try:
        own = socket.gethostname().strip().lower()
    except OSError:
        own = ""
    if own:
        short = own.split(".")[0]
        out |= {own, short, f"{short}.local"}
        try:
            out |= {info[4][0] for info in socket.getaddrinfo(own, None)}
        except OSError:
            pass
    for family, probe in ((socket.AF_INET, "10.255.255.255"), (socket.AF_INET6, "fd00::1")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                out.add(s.getsockname()[0])
        except OSError:
            pass
    return out


def asked_names(host: str, allowed=()) -> set[str]:
    """What the certificate must hold: localhost and the loopback addresses, the address
    Studio is bound to (not 0.0.0.0 or ::), and every name given (``allowed``, and
    STOREYPATH_ALLOWED_HOSTS)."""
    import re

    from .web.guard import ALLOWED_HOSTS_ENV

    given = list(allowed or []) + re.split(r"[,\s]+", os.environ.get(ALLOWED_HOSTS_ENV, ""))
    return {"localhost", "127.0.0.1", "::1", host or ""} | {g for g in given if g}


def _held(cert) -> tuple[set[str], set]:
    from cryptography import x509

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return set(), set()
    return {n.lower() for n in san.get_values_for_type(x509.DNSName)}, set(san.get_values_for_type(x509.IPAddress))


def studio_certificate(data: str | Path, host: str = "127.0.0.1", allowed=(), *, now: dt.datetime | None = None,
                       machine: set[str] | None = None) -> Certificate:
    """Studio's own certificate in ``<data>/tls/``: the one there, or a new one when there is
    none, it ends within RENEW_DAYS, or it lacks one of asked_names()."""
    from cryptography import x509

    now = now or dt.datetime.now(dt.timezone.utc)
    folder = Path(data) / FOLDER
    cert_path, key_path = folder / CERT_FILE, folder / KEY_FILE
    _owners_alone(folder, 0o700)  # opened up by hand: closed again
    for path in (cert_path, key_path):
        _owners_alone(path, 0o600)
    asked_dns, asked_ips = _split(asked_names(host, allowed))
    had = (set(), set())
    if cert_path.is_file() and key_path.is_file():
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            had = _held(cert)
            if cert.not_valid_after_utc - now > dt.timedelta(days=RENEW_DAYS) and asked_dns <= had[0] \
                    and asked_ips <= had[1]:
                return Certificate(cert_path, key_path, fingerprint(cert_path), False, _listed(*had))
        except ValueError:
            pass  # not a certificate: made again
    machine_dns, machine_ips = _split(machine if machine is not None else machine_names())
    # the names it had stay on it: a browser that was told to trust it goes on trusting its successor's names
    dns, ips = asked_dns | machine_dns | had[0], asked_ips | machine_ips | had[1]
    _make(folder, cert_path, key_path, dns, ips, now)
    return Certificate(cert_path, key_path, fingerprint(cert_path), True, _listed(dns, ips))


def _listed(dns, ips) -> list[str]:
    return sorted(dns) + sorted(str(i) for i in ips)


def _make(folder: Path, cert_path: Path, key_path: Path, dns: set[str], ips: set, now: dt.datetime) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    _owners_alone(folder, 0o700)  # (whatever the umask)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ISSUER),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, ISSUER)])
    sans = [x509.DNSName(n) for n in sorted(dns)] + [x509.IPAddress(i) for i in sorted(ips, key=str)]
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))  # a clock a little behind still takes it
            .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(ski, critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False)
            .sign(key, hashes.SHA256()))
    _write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()), 0o600)
    _write(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o600)


def _owners_alone(path: Path, mode: int) -> None:
    """``path`` (when there, and its owner's) given ``mode`` when it is more open."""
    try:
        if os.stat(path).st_mode & 0o777 & ~mode:
            os.chmod(path, mode)
    except OSError:
        pass


def _write(path: Path, data: bytes, mode: int) -> None:
    """Written beside it, then put in its place (with its mode from the start)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
