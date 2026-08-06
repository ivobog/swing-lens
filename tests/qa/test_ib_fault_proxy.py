from __future__ import annotations

import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while payload := self.request.recv(4096):
            self.request.sendall(payload)


@pytest.mark.integration
def test_ib_fault_proxy_relays_only_through_localhost() -> None:
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler) as upstream:
        upstream.daemon_threads = True
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        listen_port = _unused_local_port()
        script = Path(__file__).parents[2] / "scripts" / "qa" / "ib_fault_proxy.py"
        process = subprocess.Popen(
            [
                sys.executable,
                str(script),
                "--listen-port",
                str(listen_port),
                "--upstream-port",
                str(upstream.server_address[1]),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _wait_for_proxy(listen_port, process)
            with socket.create_connection(("127.0.0.1", listen_port), timeout=2) as client:
                client.sendall(b"read-only-market-data")
                assert client.recv(4096) == b"read-only-market-data"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            upstream.shutdown()
            upstream_thread.join(timeout=5)


def _unused_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_proxy(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        assert process.poll() is None, process.stderr.read() if process.stderr else "proxy stopped"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"IB fault proxy did not listen on port {port}.")
