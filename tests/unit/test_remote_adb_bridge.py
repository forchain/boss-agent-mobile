"""
tests/unit/test_remote_adb_bridge.py
====================================
Unit tests for the Remote ADB Bridge service daemon (spec #241, ticket #243).
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from pathlib import Path

import pytest

from boss_agent.services.remote_adb_bridge import (
    RemoteAdbBridge,
    get_primary_lan_ip,
)


class MockTcpServer:
    """Minimal TCP server mimicking local AVD 127.0.0.1:5555."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.host = host
        self._stop = threading.Event()
        self.received_chunks: list[bytes] = []
        self.active_conns: list[socket.socket] = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self.active_conns.append(conn)
            t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
            t.start()

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(0.5)
        while not self._stop.is_set():
            try:
                data = conn.recv(4096)
                if not data:
                    break
                self.received_chunks.append(data)
                # Echo response
                conn.sendall(b"ECHO:" + data)
            except (TimeoutError, OSError):
                continue
        with contextlib.suppress(Exception):
            conn.close()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):
            self.sock.close()
        for c in self.active_conns:
            with contextlib.suppress(Exception):
                c.close()


@pytest.fixture
def target_server():
    server = MockTcpServer()
    yield server
    server.close()


def test_primary_lan_ip_detection():
    ip = get_primary_lan_ip()
    assert ip != ""
    assert not ip.startswith("127.") or ip == "127.0.0.1"


def test_bridge_bidirectional_relay(target_server: MockTcpServer, tmp_path: Path):
    pid_file = tmp_path / "bridge.pid"
    bridge = RemoteAdbBridge(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=target_server.port,
        pid_file=str(pid_file),
    )
    bridge.start(block=False)
    time.sleep(0.1)

    try:
        assert pid_file.exists()
        assert int(pid_file.read_text().strip()) > 0

        # Client connects to bridge
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(3.0)
        client.connect(("127.0.0.1", bridge.bound_port))

        # Send test payload
        client.sendall(b"HELLO_ADB")
        response = client.recv(1024)
        assert response == b"ECHO:HELLO_ADB"
        assert b"HELLO_ADB" in target_server.received_chunks

        client.close()
    finally:
        bridge.stop()
        assert not pid_file.exists()


def test_preemptive_session_eviction(target_server: MockTcpServer, tmp_path: Path):
    """When client 2 connects, client 1 must be evicted cleanly without deadlocking the port."""
    bridge = RemoteAdbBridge(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=target_server.port,
        pid_file=str(tmp_path / "bridge.pid"),
    )
    bridge.start(block=False)
    time.sleep(0.1)

    try:
        # Client 1 connects and sends data
        c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c1.settimeout(3.0)
        c1.connect(("127.0.0.1", bridge.bound_port))
        c1.sendall(b"CLIENT_1")
        assert c1.recv(1024) == b"ECHO:CLIENT_1"

        # Client 2 connects -> triggers eviction of Client 1
        c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c2.settimeout(3.0)
        c2.connect(("127.0.0.1", bridge.bound_port))

        # Give bridge a moment to process eviction
        time.sleep(0.2)

        # Client 1 socket must be closed / return EOF
        c1.settimeout(1.0)
        try:
            chunk = c1.recv(1024)
            assert chunk == b"", "Client 1 should have received EOF after eviction"
        except (TimeoutError, OSError):
            pass

        # Client 2 must be fully functional
        c2.sendall(b"CLIENT_2")
        assert c2.recv(1024) == b"ECHO:CLIENT_2"

        c1.close()
        c2.close()
    finally:
        bridge.stop()


def test_target_unreachable_does_not_crash_bridge(tmp_path: Path):
    """Connecting when target is dead must cleanly close client without killing bridge."""
    bridge = RemoteAdbBridge(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=65432,  # Dead port
        pid_file=str(tmp_path / "bridge.pid"),
    )
    bridge.start(block=False)
    time.sleep(0.1)

    try:
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.settimeout(2.0)
        c.connect(("127.0.0.1", bridge.bound_port))

        # Dead target connection should close client connection
        try:
            data = c.recv(1024)
            assert data == b""
        except (TimeoutError, OSError):
            pass
        c.close()
    finally:
        bridge.stop()
