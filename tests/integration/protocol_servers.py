from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time
from typing import Any, Iterator

from aiosmtpd.controller import Controller  # type: ignore[import-untyped]
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class MessageSink:
    def __init__(self) -> None:
        self.messages: list[bytes] = []

    async def handle_DATA(
        self, server: object, session: object, envelope: Any
    ) -> str:
        del server, session
        self.messages.append(bytes(envelope.original_content))
        return "250 Message accepted for delivery"


@contextmanager
def smtp_server(tmp_path: Path) -> Iterator[tuple[int, Path, MessageSink]]:
    certificate, key = create_certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    sink = MessageSink()

    def authenticate(mechanism: str, login: bytes, password: bytes) -> bool:
        return mechanism in {"LOGIN", "PLAIN"} and login == b"owner" and password == b"password"

    port = free_port()
    controller = Controller(
        sink,
        hostname="127.0.0.1",
        port=port,
        tls_context=context,
        require_starttls=True,
        auth_required=True,
        auth_require_tls=True,
        auth_callback=authenticate,
    )
    controller.start()
    try:
        yield port, certificate, sink
    finally:
        controller.stop()


@contextmanager
def caldav_server(tmp_path: Path) -> Iterator[str]:
    certificate, key = create_certificate(tmp_path)
    port = free_port()
    command = [
        sys.executable,
        "-m",
        "radicale",
        "-H",
        f"127.0.0.1:{port}",
        "-s",
        "true",
        "-c",
        str(certificate),
        "-k",
        str(key),
        "--auth-type",
        "none",
        "--storage-filesystem-folder",
        str(tmp_path / "radicale-storage"),
        "--logging-level",
        "warning",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        wait_for_port(port, process)
        yield f"https://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def create_certificate(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_path = tmp_path / "localhost.crt"
    key_path = tmp_path / "localhost.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, key_path


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_port(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("protocol_server_start_failed")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("protocol_server_start_timeout")
