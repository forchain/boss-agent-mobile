"""
tests/unit/test_remote_adb_bridge.py
====================================
Unit tests for the Remote ADB Bridge service daemon (spec #241, ticket #243).
"""

from __future__ import annotations

import contextlib
import errno
import inspect
import logging
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

from boss_agent.services import remote_adb_bridge as bridge_module
from boss_agent.services.remote_adb_bridge import (
    KEEPALIVE_COUNT,
    KEEPALIVE_IDLE_SEC,
    KEEPALIVE_INTERVAL_SEC,
    ActiveSession,
    RemoteAdbBridge,
    build_argument_parser,
    configure_keepalive,
    get_primary_lan_ip,
    is_transient_relay_error,
    keepalive_options,
)

pytestmark = pytest.mark.e2e


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


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    """Poll `predicate` until it holds or the deadline passes.

    The bridge reaps a session from its relay threads, so the only honest way to
    observe the bookkeeping is to poll for it with a bounded deadline — never to
    sleep a fixed guess long enough to make a race look deterministic.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _reset_close(sock: socket.socket) -> None:
    """Close a socket with SO_LINGER 0 so the peer sees an RST, not an orderly FIN.

    An abrupt RST is the failure that used to be invisible: the peer must not be
    able to tell a severed client from a half-open one, and the survivor's session
    has to be untouched either way.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


class RecordingSocket:
    """Socket stand-in that records `setsockopt` calls instead of touching a real ABI."""

    def __init__(self, reject: set[int] | None = None):
        self.options: list[tuple[int, int, object]] = []
        self._reject = reject or set()

    def setsockopt(self, level, option, value):
        if option in self._reject:
            raise OSError(errno.ENOPROTOOPT, "option not supported")
        self.options.append((level, option, value))


class CountingSocket:
    """Socket stand-in that counts `close()` calls, to prove cleanup happens once."""

    def __init__(self):
        self.close_calls = 0

    def shutdown(self, _how) -> None:
        pass

    def close(self) -> None:
        self.close_calls += 1


class CollectingSink:
    """Write-end stand-in that accumulates whatever a relay forwards to it."""

    def __init__(self):
        self.written = bytearray()
        self.closed = False

    def sendall(self, data) -> None:
        self.written += bytes(data)

    def shutdown(self, _how) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FlakySource:
    """Read-end stand-in over a real fd (so `select` accepts it) that fails N recvs first."""

    def __init__(self, sock: socket.socket, failures: list[BaseException]):
        self._sock = sock
        self._failures = list(failures)

    def fileno(self) -> int:
        return self._sock.fileno()

    def recv_into(self, buf) -> int:
        if self._failures:
            raise self._failures.pop(0)
        return self._sock.recv_into(buf)


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


def test_concurrent_sessions_are_independent(target_server: MockTcpServer, tmp_path: Path):
    """Multiple LIVE clients may hold sessions simultaneously (root cause of the
    2026-09-24 eviction war): a new connection must never disconnect existing
    live sessions. Dead/half-open sessions are reaped by TCP keepalive instead."""
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
        # Client 1 connects and is fully functional
        c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c1.settimeout(3.0)
        c1.connect(("127.0.0.1", bridge.bound_port))
        c1.sendall(b"CLIENT_1")
        assert c1.recv(1024) == b"ECHO:CLIENT_1"

        # Client 2 connects while Client 1 is still live
        c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c2.settimeout(3.0)
        c2.connect(("127.0.0.1", bridge.bound_port))
        c2.sendall(b"CLIENT_2")
        assert c2.recv(1024) == b"ECHO:CLIENT_2"

        # Client 1 must remain fully usable after Client 2's arrival (no eviction)
        time.sleep(0.2)
        c1.sendall(b"CLIENT_1_AGAIN")
        assert c1.recv(1024) == b"ECHO:CLIENT_1_AGAIN"

        # Each bridge session owns its own independent target connection
        assert len(target_server.active_conns) == 2

        # Client 1 disconnecting must not disturb Client 2
        c1.close()
        time.sleep(0.3)
        c2.sendall(b"CLIENT_2_AGAIN")
        assert c2.recv(1024) == b"ECHO:CLIENT_2_AGAIN"

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


def test_boss_agent_imports_without_langsmith(monkeypatch: pytest.MonkeyPatch):
    """When langsmith is not installed or raises ImportError, boss_agent and
    its service submodules (e.g. remote_adb_bridge) must still import cleanly."""
    import sys

    monkeypatch.setitem(sys.modules, "langsmith", None)
    for mod in list(sys.modules):
        if mod == "boss_agent" or mod.startswith("boss_agent."):
            monkeypatch.delitem(sys.modules, mod, raising=False)

    import boss_agent  # noqa: F401
    import boss_agent.services.remote_adb_bridge  # noqa: F401


# --- Keepalive policy (ticket #366) ------------------------------------------------


def test_keepalive_idle_sits_in_the_jitter_tolerant_band():
    """The idle probe threshold has to ride out a Wi-Fi radio hand-off or a tunnel
    rekey. Both are sub-second-to-few-second events, so anything in the 30-60s band
    clears them with room to spare."""
    assert 30 <= KEEPALIVE_IDLE_SEC <= 60
    assert KEEPALIVE_INTERVAL_SEC >= 5
    assert KEEPALIVE_COUNT >= 3


def test_keepalive_tolerates_more_silence_than_any_link_handoff():
    """Silence tolerated before a peer is reaped: idle plus unacked probes. The
    pre-#366 policy (5s idle / 2s / 3) gave up after ~11s -- inside the window a
    single Wi-Fi roam occupies, which is why healthy sessions were being killed."""
    tolerated = KEEPALIVE_IDLE_SEC + KEEPALIVE_COUNT * KEEPALIVE_INTERVAL_SEC
    assert tolerated >= 90, (
        f"only {tolerated}s of silence tolerated; jitter will still kill sessions"
    )


def test_keepalive_options_darwin_sets_the_idle_interval_and_count():
    """Darwin takes all three knobs today, in idle/interval/count order.

    Its per-socket keepalive was historically the single `TCP_KEEPALIVE` idle knob, and a
    Darwin-only single-knob set was the reason this assertion used to pin one option. That
    is stale: current macOS honours `TCP_KEEPINTVL` and `TCP_KEEPCNT` per socket too
    (verified by `getsockopt` read-back on macOS 27.0.1), so declaring only the idle knob
    left the system defaults in charge — about a 75s interval and 8 probes, i.e. ~645s to
    reap a dead peer instead of the ~105s the policy states.

    Asserted against a pure option list, so this holds on a Linux test host without
    patching the `socket` module's ABI.
    """
    options = keepalive_options(platform="darwin")

    assert [name for _level, name, _value in options] == [
        "TCP_KEEPALIVE",
        "TCP_KEEPINTVL",
        "TCP_KEEPCNT",
    ]
    assert all(level == socket.IPPROTO_TCP for level, _name, _value in options)
    assert [value for _level, _name, value in options] == [
        KEEPALIVE_IDLE_SEC,
        KEEPALIVE_INTERVAL_SEC,
        KEEPALIVE_COUNT,
    ]


def test_darwin_and_linux_agree_on_which_policies_they_can_set():
    """The two supported platforms must not drift into different coverage.

    They name the idle knob differently (`TCP_KEEPALIVE` vs `TCP_KEEPIDLE`) but must carry
    the same three policy values, or one platform would silently fall back to system
    defaults for the interval and count — exactly the defect the Darwin set above had.
    """
    darwin = keepalive_options(platform="darwin")
    linux = keepalive_options(platform="linux")

    assert [value for _l, _n, value in darwin] == [value for _l, _n, value in linux]
    assert len({name for _l, name, _v in darwin} & {name for _l, name, _v in linux}) == 2, (
        "expected the interval and count knobs to be spelled identically on both platforms"
    )


def test_keepalive_options_linux_sets_exactly_the_three_knobs():
    options = keepalive_options(platform="linux")

    assert {name: value for _level, name, value in options} == {
        "TCP_KEEPIDLE": KEEPALIVE_IDLE_SEC,
        "TCP_KEEPINTVL": KEEPALIVE_INTERVAL_SEC,
        "TCP_KEEPCNT": KEEPALIVE_COUNT,
    }
    assert all(level == socket.IPPROTO_TCP for level, _name, _value in options)


def test_keepalive_never_carries_the_aggressive_pre_366_policy():
    """Regression guard: the 5s idle / 2s interval must not come back on any
    platform path, nor through an explicitly aggressive argument."""
    assert KEEPALIVE_IDLE_SEC != 5
    assert KEEPALIVE_INTERVAL_SEC != 2

    for platform, expected_idle_option in (
        ("darwin", "TCP_KEEPALIVE"),
        ("linux", "TCP_KEEPIDLE"),
        ("win32", "TCP_KEEPIDLE"),  # unknown platforms fall back to the three-knob set
    ):
        idle = {
            name: value
            for _level, name, value in keepalive_options(platform=platform)
            if name in ("TCP_KEEPALIVE", "TCP_KEEPIDLE")
        }
        assert set(idle) == {expected_idle_option}, platform
        assert idle[expected_idle_option] >= bridge_module.KEEPALIVE_IDLE_MIN_SEC, platform

    # An explicit attempt to re-arm the old policy is clamped back up to the floor.
    clamped = {
        name: value
        for _level, name, value in keepalive_options(
            platform="linux", idle_sec=5, interval_sec=2, count=3
        )
    }
    assert clamped["TCP_KEEPIDLE"] >= bridge_module.KEEPALIVE_IDLE_MIN_SEC
    assert clamped["TCP_KEEPINTVL"] >= bridge_module.KEEPALIVE_INTERVAL_MIN_SEC
    assert clamped["TCP_KEEPCNT"] >= bridge_module.KEEPALIVE_COUNT_MIN


def test_configure_keepalive_defaults_come_from_the_module_constants():
    """The policy must not be buried in a default argument again: omitting a value
    resolves to the named constant, so the number stays inspectable and testable."""
    parameters = inspect.signature(configure_keepalive).parameters

    assert parameters["idle_sec"].default is None
    assert parameters["interval_sec"].default is None
    assert parameters["count"].default is None

    resolved = {value for _lvl, _name, value in keepalive_options(platform=sys.platform)}
    assert resolved <= {KEEPALIVE_IDLE_SEC, KEEPALIVE_INTERVAL_SEC, KEEPALIVE_COUNT}


def test_configure_keepalive_applies_exactly_the_platform_option_set():
    """What reaches the socket is SO_KEEPALIVE + TCP_NODELAY plus the options this
    platform selects -- expectation computed from `keepalive_options`, so the
    assertion holds identically on a Darwin and a Linux host."""
    recorder = RecordingSocket()
    configure_keepalive(recorder)

    expected = keepalive_options(platform=sys.platform)
    expected_applied = [
        (socket.IPPROTO_TCP, getattr(socket, name), value)
        for _lvl, name, value in expected
        if hasattr(socket, name)  # an option the host lacks is skipped, never invented
    ]

    assert recorder.options == [
        (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
        *expected_applied,
    ]


def test_configure_keepalive_skips_unsupported_options_without_raising():
    """`contextlib.suppress` is the contract that turns an option this kernel will
    not take into a no-op, instead of a crash in the accept loop that would take the
    whole bridge down with it."""
    recorder = RecordingSocket(reject={socket.TCP_NODELAY})
    configure_keepalive(recorder, platform="win32")  # names absent on any POSIX host

    assert recorder.options[0] == (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    assert socket.TCP_NODELAY not in [opt for _lvl, opt, _v in recorder.options]


def test_configure_keepalive_skips_a_knob_the_host_does_not_define():
    """A platform may name an option this Python or kernel does not have.

    The Darwin set now names all three knobs on the strength of current macOS, so the
    getattr-and-skip path is a live code path rather than a hypothetical: an older host must
    get the knobs it does have, silently, instead of raising out of the accept loop.
    """
    absent = "TCP_KEEPCNT" if not hasattr(socket, "TCP_KEEPCNT") else "TCP_KEEPALIVE"
    declared = keepalive_options(platform="darwin")
    assert absent in [name for _l, name, _v in declared], (
        "this test needs a knob Darwin declares to be absent on the host"
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.delattr(socket, absent, raising=False)
        recorder = RecordingSocket()
        configure_keepalive(recorder, platform="darwin")
        expected = [
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
            *[
                (socket.IPPROTO_TCP, getattr(socket, name), value)
                for _level, name, value in declared
                if name != absent
            ],
        ]

    assert recorder.options == expected


def test_configure_keepalive_clamps_and_reports_an_aggressive_override(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.WARNING, logger="remote_adb_bridge"):
        options = {
            name: value for _lvl, name, value in keepalive_options(platform="linux", idle_sec=5)
        }

    assert options["TCP_KEEPIDLE"] == bridge_module.KEEPALIVE_IDLE_MIN_SEC
    assert any("idle" in record.getMessage().lower() for record in caplog.records)


def test_keepalive_cli_honours_env_override_for_a_bad_wifi_link(
    monkeypatch: pytest.MonkeyPatch,
):
    """An operator on a lossy link can retune without editing code."""
    monkeypatch.setenv("REMOTE_ADB_KEEPALIVE_IDLE_SEC", "90")
    monkeypatch.setenv("REMOTE_ADB_KEEPALIVE_INTERVAL_SEC", "20")
    monkeypatch.setenv("REMOTE_ADB_KEEPALIVE_COUNT", "4")

    args = build_argument_parser().parse_args([])

    assert (args.keepalive_idle_sec, args.keepalive_interval_sec, args.keepalive_count) == (
        90,
        20,
        4,
    )
    applied = {
        name: value
        for _lvl, name, value in keepalive_options(
            platform="linux",
            idle_sec=args.keepalive_idle_sec,
            interval_sec=args.keepalive_interval_sec,
            count=args.keepalive_count,
        )
    }
    assert applied["TCP_KEEPIDLE"] == 90
    assert applied["TCP_KEEPINTVL"] == 20
    assert applied["TCP_KEEPCNT"] == 4


def test_keepalive_cli_flag_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMOTE_ADB_KEEPALIVE_IDLE_SEC", "90")
    args = build_argument_parser().parse_args(["--keepalive-idle-sec", "120"])
    assert args.keepalive_idle_sec == 120


def test_keepalive_cli_ignores_garbage_env_and_documents_the_floor(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("REMOTE_ADB_KEEPALIVE_IDLE_SEC", "not-a-number")
    args = build_argument_parser().parse_args([])
    assert args.keepalive_idle_sec == KEEPALIVE_IDLE_SEC

    help_text = build_argument_parser().format_help()
    assert "REMOTE_ADB_KEEPALIVE_IDLE_SEC" in help_text
    assert str(bridge_module.KEEPALIVE_IDLE_MIN_SEC) in help_text


# --- Per-session fault isolation (ticket #366) -------------------------------------


def test_transient_relay_errors_are_classified_as_retryable():
    assert is_transient_relay_error(InterruptedError(errno.EINTR, "interrupted"))
    assert is_transient_relay_error(BlockingIOError(errno.EAGAIN, "try again"))
    assert is_transient_relay_error(TimeoutError(errno.EAGAIN, "timed out"))
    assert is_transient_relay_error(ConnectionResetError(errno.ECONNRESET, "reset"))
    # A genuinely fatal condition still terminates the session.
    assert not is_transient_relay_error(OSError(errno.EBADF, "bad file descriptor"))
    assert not is_transient_relay_error(OSError(errno.ENOTCONN, "not connected"))


def test_transient_upstream_error_is_retried_without_reaping_the_session():
    """A momentary EAGAIN on the read side must not cost a healthy session its
    lifetime -- the bytes that follow still have to arrive."""
    bridge = RemoteAdbBridge()
    left, right = socket.socketpair()
    sink = CollectingSink()
    session = ActiveSession(right, sink, ("10.0.0.9", 51234))
    bridge.register_session(session)
    source = FlakySource(left, [BlockingIOError(errno.EAGAIN, "try again")])

    relay = threading.Thread(
        target=bridge._relay_stream,
        args=(source, sink, session, "client->target"),
        daemon=True,
    )
    relay.start()
    try:
        right.sendall(b"RETRY_ME")
        assert _wait_until(lambda: b"RETRY_ME" in bytes(sink.written))
        # The transient error did not tear the session down.
        assert bridge.active_client_endpoints == [("10.0.0.9", 51234)]
        assert not session.closed.is_set()
    finally:
        right.close()
        relay.join(timeout=3.0)

    assert _wait_until(lambda: bridge.active_client_endpoints == [])


def test_relay_unregisters_its_session_even_on_base_exception():
    """A relay thread must never leave `_relay_stream` still registered -- including
    on a BaseException such as the SystemExit a test or a shutdown hook can raise."""
    bridge = RemoteAdbBridge()
    left, right = socket.socketpair()
    sink = CollectingSink()
    session = ActiveSession(right, sink, ("10.0.0.7", 41000))
    bridge.register_session(session)

    try:
        # Make the source readable first: the relay only recvs once select reports
        # the fd ready, so without a byte pending the injected error never fires.
        right.sendall(b"TRIGGER")
        with pytest.raises(SystemExit):
            bridge._relay_stream(
                FlakySource(left, [SystemExit(1)]), sink, session, "target->client"
            )
    finally:
        left.close()
        right.close()

    assert bridge.active_client_endpoints == []
    assert session.closed.is_set()
    assert sink.closed


def test_session_cleanup_happens_exactly_once_under_concurrent_callers(
    caplog: pytest.LogCaptureFixture,
):
    """Both relay threads and `stop()` race to reap the same session. Exactly one of
    them may close it, and only one teardown line may be logged for it."""
    client = CountingSocket()
    target = CountingSocket()
    bridge = RemoteAdbBridge()
    session = ActiveSession(client, target, ("10.0.0.11", 40000))
    bridge.register_session(session)

    start = threading.Barrier(8)

    def retire() -> None:
        start.wait(timeout=3.0)
        bridge.retire_session(session, "client->target", "test teardown")

    # Capture at INFO around the race itself: the winning teardown line is emitted
    # by whichever thread gets there first, and a late second call must add nothing.
    with caplog.at_level(logging.INFO, logger="remote_adb_bridge"):
        threads = [threading.Thread(target=retire) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3.0)

        assert client.close_calls == 1
        assert target.close_calls == 1
        assert bridge.active_client_endpoints == []

        bridge.retire_session(session, "client->target", "test teardown")

    teardown_lines = [
        record.getMessage()
        for record in caplog.records
        if "10.0.0.11:40000" in record.getMessage() and "client->target" in record.getMessage()
    ]
    assert len(teardown_lines) == 1, teardown_lines


def test_severed_client_does_not_disturb_a_concurrent_survivor(
    target_server: MockTcpServer, tmp_path: Path
):
    """The acceptance case: one client is severed mid-session and the other keeps
    flowing. Previously the whole session died silently on any relay error."""
    bridge = RemoteAdbBridge(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=target_server.port,
        pid_file=str(tmp_path / "bridge.pid"),
    )
    bridge.start(block=False)

    victim = survivor = None
    try:
        victim = socket.create_connection(("127.0.0.1", bridge.bound_port), timeout=3.0)
        victim.settimeout(3.0)
        victim.sendall(b"VICTIM")
        assert victim.recv(1024) == b"ECHO:VICTIM"

        survivor = socket.create_connection(("127.0.0.1", bridge.bound_port), timeout=3.0)
        survivor.settimeout(3.0)
        survivor.sendall(b"SURVIVOR")
        assert survivor.recv(1024) == b"ECHO:SURVIVOR"

        # Sever the victim with an RST rather than an orderly FIN.
        _reset_close(victim)
        victim = None

        # The survivor's session must drain away, and only the survivor's must go.
        def drained() -> bool:
            return len(bridge.active_client_endpoints) == 1

        assert _wait_until(drained, timeout=5.0), (
            f"expected only the survivor left, got {bridge.active_client_endpoints}"
        )

        # Traffic keeps flowing on the survivor across several round trips.
        for index in range(5):
            payload = f"SURVIVOR_{index}".encode()
            survivor.sendall(payload)
            assert survivor.recv(1024) == b"ECHO:" + payload
    finally:
        for sock in (victim, survivor):
            if sock is not None:
                with contextlib.suppress(Exception):
                    sock.close()
        bridge.stop()


def test_sessions_drain_to_empty_and_the_bridge_stays_usable(
    target_server: MockTcpServer, tmp_path: Path
):
    """Bookkeeping must actually drain after an abrupt disconnect, and the bridge
    must still accept clients afterwards."""
    bridge = RemoteAdbBridge(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=target_server.port,
        pid_file=str(tmp_path / "bridge.pid"),
    )
    bridge.start(block=False)

    client = None
    try:
        client = socket.create_connection(("127.0.0.1", bridge.bound_port), timeout=3.0)
        client.settimeout(3.0)
        client.sendall(b"BEFORE_RESET")
        assert client.recv(1024) == b"ECHO:BEFORE_RESET"
        assert len(bridge.active_client_endpoints) == 1

        _reset_close(client)
        client = None

        assert _wait_until(lambda: bridge.active_client_endpoints == [], timeout=5.0), (
            f"sessions never drained: {bridge.active_client_endpoints}"
        )

        # The bridge survived: a fresh client is served normally.
        with socket.create_connection(("127.0.0.1", bridge.bound_port), timeout=3.0) as fresh:
            fresh.settimeout(3.0)
            fresh.sendall(b"AFTER_RESET")
            assert fresh.recv(1024) == b"ECHO:AFTER_RESET"
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()
        bridge.stop()


def test_session_teardown_is_logged_with_client_and_direction(
    target_server: MockTcpServer, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """A client that keeps dropping was silently invisible before; the teardown line
    has to name the address and the direction that ended the relay."""
    bridge = RemoteAdbBridge(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=target_server.port,
        pid_file=str(tmp_path / "bridge.pid"),
    )
    bridge.start(block=False)

    client = None
    try:
        client = socket.create_connection(("127.0.0.1", bridge.bound_port), timeout=3.0)
        client.settimeout(3.0)
        client.sendall(b"GONE_SOON")
        assert client.recv(1024) == b"ECHO:GONE_SOON"
        client_addr = (
            f"{bridge.active_client_endpoints[0][0]}:{bridge.active_client_endpoints[0][1]}"
        )

        with caplog.at_level(logging.INFO, logger="remote_adb_bridge"):
            client.close()
            client = None
            assert _wait_until(lambda: bridge.active_client_endpoints == [], timeout=5.0)

        teardowns = [
            record.getMessage()
            for record in caplog.records
            if client_addr in record.getMessage()
            and ("->" in record.getMessage() or "retir" in record.getMessage().lower())
        ]
        assert teardowns, f"no teardown line naming {client_addr}: {caplog.messages}"
        assert any("client->target" in line or "target->client" in line for line in teardowns)
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()
        bridge.stop()
