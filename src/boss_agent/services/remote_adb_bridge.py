"""
src/boss_agent/services/remote_adb_bridge.py
============================================
Remote ADB Bridge daemon with concurrent multi-client sessions (spec #241, ticket #243,
revised after the 2026-09-24 eviction-war incident).

Binds to an external network interface (default 0.0.0.0:6555) and relays bidirectional
ADB traffic to the local Virtual Device Session (default 127.0.0.1:5555).

Key architectural guarantees:
1. Concurrent independent sessions:
   adbd accepts multiple simultaneous TCP clients (each becomes its own transport), so
   every accepted client gets its own paired (client, target) relay. A new connection
   NEVER disconnects a live session. (The original "Preemptive Session Eviction" design
   livelocked whenever two or more auto-reconnecting adb servers were connected at once:
   each evicted side reconnected within ~1s and evicted the holder, an endless war.)
2. Jitter-tolerant TCP keepalive:
   Sockets carry SO_KEEPALIVE with an idle threshold sized for a real network
   (see KEEPALIVE_IDLE_SEC) rather than for a datacentre LAN. Keepalive here is a
   backstop against a genuinely half-open session, not a latency probe: the pre-#366
   policy re-armed every 2s after 5s of quiet, so a Wi-Fi radio hand-off or a
   tunnel rekey was enough for the kernel to declare a healthy peer dead and tear
   the session down. A dead socket still gets reaped, just only after silence no
   transient network event produces.
3. Per-session fault isolation:
   A relay error is confined to the session that produced it. Transient errors are
   retried with backoff instead of reaping a healthy session, fatal ones reap only
   their own session, and the teardown is performed exactly once no matter how many
   of that session's relay threads and `stop()` reach it.
4. Clean lifecycle management:
   Handles SIGTERM/SIGINT signals gracefully, reclaiming ports and cleaning up PID files.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import logging
import os
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time

logger = logging.getLogger("remote_adb_bridge")

# TCP keepalive policy (#366). The idle threshold is what decides when the kernel
# starts probing a silent peer, and the total silence it tolerates is
# `idle + count * interval` -- the old 5s/2s/3 gave up after ~11s, comfortably inside
# the window a single Wi-Fi roam, a 5GHz/2GHz hand-off, or a VPN rekey occupies.
# That is why healthy external sessions were being cut ("外网/Wi-Fi 客户端频繁掉线").
# 45s idle clears any of those with an order of magnitude to spare, and 6 unacked
# 10s probes mean a truly dead peer (phone powered off, cable pulled) is still
# reaped in ~105s rather than lingering forever. The jitter floor below is the
# policy's own safety rail: no override, env var or argument can drive it back to
# the aggressive regime.
KEEPALIVE_IDLE_SEC = 45
KEEPALIVE_INTERVAL_SEC = 10
KEEPALIVE_COUNT = 6
KEEPALIVE_IDLE_MIN_SEC = 30
KEEPALIVE_INTERVAL_MIN_SEC = 5
KEEPALIVE_COUNT_MIN = 3
KEEPALIVE_IDLE_MAX_SEC = 3600
KEEPALIVE_INTERVAL_MAX_SEC = 600
KEEPALIVE_COUNT_MAX = 100

# Which TCP-level keepalive knob each platform actually has. Darwin exposes a single
# idle knob (`TCP_KEEPALIVE`, a.k.a. net.inet.tcp.keepidle) and no interval or probe
# count at all; Linux has all three. An unknown platform gets the Linux set, and any
# name the running kernel does not define is skipped rather than invented.
_KEEPALIVE_OPTION_NAMES: dict[str, tuple[str, ...]] = {
    "darwin": ("TCP_KEEPALIVE",),
    "linux": ("TCP_KEEPIDLE", "TCP_KEEPINTVL", "TCP_KEEPCNT"),
}
_KEEPALIVE_FALLBACK_OPTIONS = _KEEPALIVE_OPTION_NAMES["linux"]

# Socket option name -> which of the three policy values it carries.
_KEEPALIVE_OPTION_VALUES: dict[str, str] = {
    "TCP_KEEPALIVE": "idle",  # Darwin
    "TCP_KEEPIDLE": "idle",  # Linux
    "TCP_KEEPINTVL": "interval",
    "TCP_KEEPCNT": "count",
}

# Relay tuning (#366). A momentary upstream hiccup is retried a bounded number of
# times before the session is given up on, so an adb client that re-establishes
# immediately does not cost the session its lifetime.
RELAY_SELECT_TIMEOUT_SEC = 0.5
RELAY_MAX_TRANSIENT_RETRIES = 3
RELAY_RETRY_BACKOFF_SEC = 0.05


def _clamp_keepalive(label: str, value: int, minimum: int, maximum: int) -> int:
    """Hold a keepalive value inside the jitter-tolerant band, reporting a clamp."""
    clamped = max(minimum, min(maximum, value))
    if clamped != value:
        logger.warning(
            "Keepalive %s=%d is outside the jitter-tolerant band [%d, %d]; using %d. "
            "Lower values reap healthy sessions during Wi-Fi hand-offs.",
            label,
            value,
            minimum,
            maximum,
            clamped,
        )
    return clamped


def keepalive_options(
    platform: str | None = None,
    idle_sec: int | None = None,
    interval_sec: int | None = None,
    count: int | None = None,
) -> list[tuple[int, str, int]]:
    """Resolve the `(level, socket option name, value)` triples to apply on `platform`.

    Kept separate from `configure_keepalive` and expressed as option *names* rather
    than resolved constants, so both the Darwin single-knob branch and the Linux
    three-knob branch are observable from any host without a socket that has the
    other platform's ABI. `platform` defaults to the running platform.
    """
    resolved_platform = sys.platform if platform is None else platform
    names = _KEEPALIVE_OPTION_NAMES.get(resolved_platform, _KEEPALIVE_FALLBACK_OPTIONS)
    values = {
        "idle": _clamp_keepalive(
            "idle",
            KEEPALIVE_IDLE_SEC if idle_sec is None else int(idle_sec),
            KEEPALIVE_IDLE_MIN_SEC,
            KEEPALIVE_IDLE_MAX_SEC,
        ),
        "interval": _clamp_keepalive(
            "interval",
            KEEPALIVE_INTERVAL_SEC if interval_sec is None else int(interval_sec),
            KEEPALIVE_INTERVAL_MIN_SEC,
            KEEPALIVE_INTERVAL_MAX_SEC,
        ),
        "count": _clamp_keepalive(
            "count",
            KEEPALIVE_COUNT if count is None else int(count),
            KEEPALIVE_COUNT_MIN,
            KEEPALIVE_COUNT_MAX,
        ),
    }
    return [(socket.IPPROTO_TCP, name, values[_KEEPALIVE_OPTION_VALUES[name]]) for name in names]


def configure_keepalive(
    sock: socket.socket,
    idle_sec: int | None = None,
    interval_sec: int | None = None,
    count: int | None = None,
    platform: str | None = None,
) -> None:
    """Enable and configure jitter-tolerant TCP keepalive on a socket.

    `idle_sec`, `interval_sec` and `count` fall back to the module constants and are
    clamped to the jitter-tolerant band, so keepalive can never be armed more
    aggressively than `KEEPALIVE_IDLE_MIN_SEC`. Every option beyond `SO_KEEPALIVE` is
    applied under `contextlib.suppress`: a platform or kernel that does not have it
    silently keeps the default instead of taking the accept loop down.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    with contextlib.suppress(Exception):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    for level, name, value in keepalive_options(
        platform=platform, idle_sec=idle_sec, interval_sec=interval_sec, count=count
    ):
        option = getattr(socket, name, None)
        if option is None:
            continue  # not this platform's knob; keepalive still works at the default
        with contextlib.suppress(Exception):
            sock.setsockopt(level, option, value)


def is_transient_relay_error(exc: BaseException) -> bool:
    """Whether a relay I/O error is a momentary hiccup rather than a dead session.

    EINTR, EAGAIN and a socket timeout are the kernel telling the thread to try
    again. ECONNRESET is included because the peers here are adb transports that
    re-establish immediately after a reset, so a bounded retry is cheaper than
    reaping a session the client is about to use again. Everything else -- a closed
    descriptor, an unconnected socket -- is fatal for this session.
    """
    if isinstance(exc, (InterruptedError, BlockingIOError, TimeoutError)):
        return True
    return isinstance(exc, ConnectionResetError)


def get_primary_lan_ip() -> str:
    """Resolve the host machine's primary non-loopback IPv4 LAN address."""
    # Method 1: Check outbound route to internet
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        outbound_ip = s.getsockname()[0]
        s.close()
        ip_obj = ipaddress.ip_address(outbound_ip)
        # Avoid loopback, link-local, and RFC 2544 benchmark (198.18.0.0/15 often used by clash/tun)
        if (
            ip_obj.is_private
            and not ip_obj.is_loopback
            and not ip_obj.is_link_local
            and not (outbound_ip.startswith("198.18.") or outbound_ip.startswith("198.19."))
        ):
            return outbound_ip
    except Exception:
        pass

    # Method 2: Enumerate interfaces via ifconfig / ip route
    try:
        out = subprocess.check_output(["ifconfig"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", line)
            if m:
                ip_str = m.group(1)
                try:
                    ip = ipaddress.ip_address(ip_str)
                    if (
                        ip.is_private
                        and not ip.is_loopback
                        and not ip.is_link_local
                        and not (ip_str.startswith("198.18.") or ip_str.startswith("198.19."))
                    ):
                        return ip_str
                except ValueError:
                    continue
    except Exception:
        pass

    return "127.0.0.1"


class ActiveSession:
    """Encapsulates a paired (client, target) socket forwarding session."""

    def __init__(
        self,
        client_sock: socket.socket,
        target_sock: socket.socket,
        client_addr: tuple[str, int],
    ):
        self.client_sock = client_sock
        self.target_sock = target_sock
        self.client_addr = client_addr
        self.closed = threading.Event()
        self.created_at = time.time()
        # Guards the check-and-set below so two relay threads racing to reap the
        # same session cannot both decide they own the teardown.
        self._state_lock = threading.Lock()
        # Whether this session has already been closed and unregistered. Read and
        # written only under the owning bridge's `_session_lock`.
        self.retired = False

    def close(self) -> None:
        """Forcibly close both ends of the session. Idempotent and race-free."""
        with self._state_lock:
            if self.closed.is_set():
                return
            self.closed.set()
        for sock in (self.client_sock, self.target_sock):
            with contextlib.suppress(Exception):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(Exception):
                sock.close()


class RemoteAdbBridge:
    """Standalone TCP bridge with concurrent independent per-client sessions."""

    def __init__(
        self,
        listen_host: str = "0.0.0.0",
        listen_port: int = 6555,
        target_host: str = "127.0.0.1",
        target_port: int = 5555,
        pid_file: str | None = None,
        ready_file: str | None = None,
        keepalive_idle_sec: int | None = None,
        keepalive_interval_sec: int | None = None,
        keepalive_count: int | None = None,
    ):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.pid_file = pid_file
        self.ready_file = ready_file
        self.keepalive_idle_sec = keepalive_idle_sec
        self.keepalive_interval_sec = keepalive_interval_sec
        self.keepalive_count = keepalive_count

        self._server_sock: socket.socket | None = None
        self._sessions: set[ActiveSession] = set()
        self._session_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._accept_thread: threading.Thread | None = None

    @property
    def bound_port(self) -> int:
        """The actual bound port (useful when listen_port=0)."""
        if self._server_sock:
            return self._server_sock.getsockname()[1]
        return self.listen_port

    @property
    def active_client_endpoints(self) -> list[tuple[str, int]]:
        """Addresses of all currently connected clients."""
        with self._session_lock:
            return [s.client_addr for s in self._sessions if not s.closed.is_set()]

    def register_session(self, session: ActiveSession) -> None:
        """Add a session to the active set. Idempotent and race-free.

        Concurrent live clients are fully independent: registering one session has
        no effect on any other, which is the guarantee the eviction war used to
        break.
        """
        with self._session_lock:
            self._sessions.add(session)

    def retire_session(self, session: ActiveSession, direction: str, reason: str) -> None:
        """Close and unregister `session` exactly once, and say so in the log.

        Both of a session's relay threads and `stop()` reach this concurrently on
        teardown, so the first caller wins: the loser's close would be a no-op but
        its log line would be noise, and a client that keeps dropping has to be
        diagnosable from one line per session naming the address, the relay
        direction that ended it, and why.
        """
        with self._session_lock:
            if session.retired:
                return
            session.retired = True
            self._sessions.discard(session)

        session.close()
        logger.info(
            "Retired session %s:%d via %s (%s); %d session(s) remain",
            session.client_addr[0],
            session.client_addr[1],
            direction,
            reason,
            len(self.active_client_endpoints),
        )

    def start(self, block: bool = True) -> None:
        """Bind the listener and start accepting connections."""
        self._stop_event.clear()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.listen_host, self.listen_port))
        sock.listen(10)
        sock.settimeout(0.5)
        self._server_sock = sock

        if self.pid_file:
            os.makedirs(os.path.dirname(os.path.abspath(self.pid_file)), exist_ok=True)
            with open(self.pid_file, "w", encoding="utf-8") as f:
                f.write(f"{os.getpid()}\n")

        if self.ready_file:
            os.makedirs(os.path.dirname(os.path.abspath(self.ready_file)), exist_ok=True)
            with open(self.ready_file, "w", encoding="utf-8") as f:
                f.write(f"{self.bound_port}\n")

        logger.info(
            "Remote ADB Bridge listening on %s:%d -> %s:%d (PID: %d)",
            self.listen_host,
            self.bound_port,
            self.target_host,
            self.target_port,
            os.getpid(),
        )

        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="BridgeAcceptThread", daemon=True
        )
        self._accept_thread.start()

        if block:
            try:
                while not self._stop_event.is_set():
                    time.sleep(0.5)
            except (KeyboardInterrupt, SystemExit):
                self.stop()

    def stop(self) -> None:
        """Gracefully stop the bridge, closing all active sessions and freeing the port."""
        if self._stop_event.is_set():
            return
        self._stop_event.set()

        # Close all active sessions. They go through the same exactly-once teardown
        # their relay threads use, so a session already dying is not torn down twice.
        with self._session_lock:
            sessions = list(self._sessions)
        for session in sessions:
            self.retire_session(session, "bridge shutdown", "stop() requested")

        # Close listener socket
        if self._server_sock:
            with contextlib.suppress(Exception):
                self._server_sock.close()
            self._server_sock = None

        if self._accept_thread and self._accept_thread.is_alive():
            self._accept_thread.join(timeout=1.0)

        # Cleanup PID and ready files
        for fpath in (self.pid_file, self.ready_file):
            if fpath and os.path.exists(fpath):
                with contextlib.suppress(Exception):
                    os.remove(fpath)

        logger.info("Remote ADB Bridge stopped cleanly.")

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._server_sock:
                break
            try:
                client_sock, client_addr = self._server_sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break

            logger.info("Accepted incoming client connection from %s:%d", *client_addr)
            self._configure_keepalive(client_sock)

            # Establish upstream connection to target AVD
            try:
                target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                target_sock.settimeout(3.0)
                target_sock.connect((self.target_host, self.target_port))
                target_sock.settimeout(None)
                self._configure_keepalive(target_sock)
            except Exception as exc:
                logger.warning(
                    "Failed to connect to target AVD %s:%d: %s. Closing client.",
                    self.target_host,
                    self.target_port,
                    exc,
                )
                with contextlib.suppress(Exception):
                    client_sock.close()
                continue

            session = ActiveSession(client_sock, target_sock, client_addr)

            # Register the session; concurrent live clients are fully independent.
            self.register_session(session)

            # Start bidirectional relay threads
            t1 = threading.Thread(
                target=self._relay_stream,
                args=(client_sock, target_sock, session, "client->target"),
                daemon=True,
            )
            t2 = threading.Thread(
                target=self._relay_stream,
                args=(target_sock, client_sock, session, "target->client"),
                daemon=True,
            )
            t1.start()
            t2.start()

    def _configure_keepalive(self, sock: socket.socket) -> None:
        """Apply this bridge's keepalive policy (operator overrides or the constants)."""
        configure_keepalive(
            sock,
            idle_sec=self.keepalive_idle_sec,
            interval_sec=self.keepalive_interval_sec,
            count=self.keepalive_count,
        )

    def _relay_stream(
        self,
        src: socket.socket,
        dst: socket.socket,
        session: ActiveSession,
        direction: str,
    ) -> None:
        """Relay bytes in one direction until the session ends, then retire it.

        A transient read or write error is retried with backoff rather than killing
        the session, so an upstream hiccup on one client costs neither that client's
        session nor any concurrent one. Only a fatal error, an orderly peer close, or
        a shutdown ends the relay -- and the teardown below runs on every exit path,
        BaseException included, so a relay thread cannot leave its session registered.
        """
        buf = bytearray(65536)
        transient_retries = 0
        reason = "peer closed the stream"
        try:
            while not session.closed.is_set() and not self._stop_event.is_set():
                try:
                    # Use select with timeout so we check session.closed periodically
                    r, _, _ = select.select([src], [], [], RELAY_SELECT_TIMEOUT_SEC)
                    if not r:
                        continue
                    nbytes = src.recv_into(buf)
                    if nbytes <= 0:
                        break
                    dst.sendall(memoryview(buf)[:nbytes])
                except Exception as exc:
                    if (
                        transient_retries >= RELAY_MAX_TRANSIENT_RETRIES
                        or not is_transient_relay_error(exc)
                    ):
                        reason = f"{type(exc).__name__}: {exc}"
                        break
                    transient_retries += 1
                    logger.debug(
                        "Transient %s error on %s for %s:%d, retry %d/%d",
                        type(exc).__name__,
                        direction,
                        session.client_addr[0],
                        session.client_addr[1],
                        transient_retries,
                        RELAY_MAX_TRANSIENT_RETRIES,
                    )
                    time.sleep(RELAY_RETRY_BACKOFF_SEC * transient_retries)
                else:
                    transient_retries = 0
        finally:
            self.retire_session(session, direction, reason)


def _env_int(name: str, default: int) -> int:
    """Read an integer tuning override from the environment, falling back to `default`."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("Ignoring non-integer %s=%r; using %d", name, raw, default)
        return default


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the daemon's CLI, including the keepalive tuning overrides (#366)."""
    parser = argparse.ArgumentParser(description="Remote ADB Bridge Daemon")
    parser.add_argument(
        "--host",
        default=os.environ.get("REMOTE_ADB_HOST", "0.0.0.0"),
        help="Listening host interface (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("REMOTE_ADB_PORT", "6555")),
        help="Listening port (default: 6555)",
    )
    parser.add_argument(
        "--target-host",
        default="127.0.0.1",
        help="Target AVD host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--target-port",
        type=int,
        default=5555,
        help="Target AVD ADB port (default: 5555)",
    )
    parser.add_argument(
        "--pid-file",
        default=os.environ.get("REMOTE_ADB_PID_FILE", ".boss_agent/remote_bridge.pid"),
        help="PID file location",
    )
    parser.add_argument(
        "--ready-file",
        default=os.environ.get("REMOTE_ADB_READY_FILE", ".boss_agent/remote_bridge.ready"),
        help="Ready file location",
    )
    parser.add_argument(
        "--keepalive-idle-sec",
        type=int,
        default=_env_int("REMOTE_ADB_KEEPALIVE_IDLE_SEC", KEEPALIVE_IDLE_SEC),
        help=(
            "Seconds of silence before TCP keepalive probes a client "
            f"(default: {KEEPALIVE_IDLE_SEC}, env: REMOTE_ADB_KEEPALIVE_IDLE_SEC). "
            f"Clamped to at least {KEEPALIVE_IDLE_MIN_SEC}s: a shorter probe reaps "
            "healthy sessions during Wi-Fi hand-offs. Raise it on a lossy link."
        ),
    )
    parser.add_argument(
        "--keepalive-interval-sec",
        type=int,
        default=_env_int("REMOTE_ADB_KEEPALIVE_INTERVAL_SEC", KEEPALIVE_INTERVAL_SEC),
        help=(
            "Seconds between unacknowledged keepalive probes "
            f"(default: {KEEPALIVE_INTERVAL_SEC}, env: REMOTE_ADB_KEEPALIVE_INTERVAL_SEC). "
            f"Clamped to at least {KEEPALIVE_INTERVAL_MIN_SEC}s. macOS exposes no such "
            "knob, so this only applies on Linux."
        ),
    )
    parser.add_argument(
        "--keepalive-count",
        type=int,
        default=_env_int("REMOTE_ADB_KEEPALIVE_COUNT", KEEPALIVE_COUNT),
        help=(
            "Unacknowledged probes before a silent peer is declared dead "
            f"(default: {KEEPALIVE_COUNT}, env: REMOTE_ADB_KEEPALIVE_COUNT). "
            f"Clamped to at least {KEEPALIVE_COUNT_MIN}. macOS exposes no such knob, "
            "so this only applies on Linux."
        ),
    )
    parser.add_argument(
        "--print-lan-ip",
        action="store_true",
        help="Print detected primary LAN IP and exit",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()

    if args.print_lan_ip:
        print(get_primary_lan_ip())
        sys.exit(0)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    bridge = RemoteAdbBridge(
        listen_host=args.host,
        listen_port=args.port,
        target_host=args.target_host,
        target_port=args.target_port,
        pid_file=args.pid_file,
        ready_file=args.ready_file,
        keepalive_idle_sec=args.keepalive_idle_sec,
        keepalive_interval_sec=args.keepalive_interval_sec,
        keepalive_count=args.keepalive_count,
    )

    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    def _signal_handler(sig, frame):
        logger.info("Received signal %d, shutting down...", sig)
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    bridge.start(block=True)


if __name__ == "__main__":
    main()
