"""
src/boss_agent/services/remote_adb_bridge.py
============================================
Remote ADB Bridge daemon with preemptive zombie session eviction (spec #241, ticket #243).

Binds to an external network interface (default 0.0.0.0:6555) and relays bidirectional
ADB traffic to the local Virtual Device Session (default 127.0.0.1:5555).

Key architectural guarantees:
1. Single-active-session exclusivity & Preemptive Eviction:
   ADB daemon over TCP cannot interleave framing bytes from multiple clients.
   When a new client connects, any existing active session is immediately and cleanly
   evicted (closed) to hand over the port without lockup.
2. Aggressive TCP keepalive:
   Sockets are configured with SO_KEEPALIVE and platform-specific idle/probe parameters
   to detect half-open sockets promptly.
3. Clean lifecycle management:
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


def configure_keepalive(
    sock: socket.socket,
    idle_sec: int = 5,
    interval_sec: int = 2,
    count: int = 3,
) -> None:
    """Enable and configure aggressive TCP keepalive on a socket."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    with contextlib.suppress(Exception):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # macOS (Darwin)
    if hasattr(socket, "TCP_KEEPALIVE"):
        with contextlib.suppress(Exception):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, idle_sec)

    # Linux
    if hasattr(socket, "TCP_KEEPIDLE"):
        with contextlib.suppress(Exception):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, idle_sec)
    if hasattr(socket, "TCP_KEEPINTVL"):
        with contextlib.suppress(Exception):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
    if hasattr(socket, "TCP_KEEPCNT"):
        with contextlib.suppress(Exception):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, count)


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

    def close(self) -> None:
        """Forcibly close both ends of the session."""
        if self.closed.is_set():
            return
        self.closed.set()
        for sock in (self.client_sock, self.target_sock):
            with contextlib.suppress(Exception):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(Exception):
                sock.close()


class RemoteAdbBridge:
    """Standalone TCP bridge with preemptive single-client eviction."""

    def __init__(
        self,
        listen_host: str = "0.0.0.0",
        listen_port: int = 6555,
        target_host: str = "127.0.0.1",
        target_port: int = 5555,
        pid_file: str | None = None,
        ready_file: str | None = None,
    ):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.pid_file = pid_file
        self.ready_file = ready_file

        self._server_sock: socket.socket | None = None
        self._current_session: ActiveSession | None = None
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
    def active_client_endpoint(self) -> tuple[str, int] | None:
        """Active client address if currently connected."""
        with self._session_lock:
            if self._current_session and not self._current_session.closed.is_set():
                return self._current_session.client_addr
            return None

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
        """Gracefully stop the bridge, evicting any active session and freeing the port."""
        if self._stop_event.is_set():
            return
        self._stop_event.set()

        # Evict active session
        with self._session_lock:
            if self._current_session:
                self._current_session.close()
                self._current_session = None

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
            configure_keepalive(client_sock)

            # Establish upstream connection to target AVD
            try:
                target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                target_sock.settimeout(3.0)
                target_sock.connect((self.target_host, self.target_port))
                target_sock.settimeout(None)
                configure_keepalive(target_sock)
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

            # Preemptive Eviction of prior session
            with self._session_lock:
                if self._current_session and not self._current_session.closed.is_set():
                    logger.warning(
                        "Evicting stagnant session %s:%d for new client %s:%d",
                        self._current_session.client_addr[0],
                        self._current_session.client_addr[1],
                        client_addr[0],
                        client_addr[1],
                    )
                    self._current_session.close()
                self._current_session = session

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

    def _relay_stream(
        self,
        src: socket.socket,
        dst: socket.socket,
        session: ActiveSession,
        direction: str,
    ) -> None:
        buf = bytearray(65536)
        try:
            while not session.closed.is_set() and not self._stop_event.is_set():
                # Use select with timeout so we check session.closed periodically
                r, _, _ = select.select([src], [], [], 0.5)
                if not r:
                    continue
                nbytes = src.recv_into(buf)
                if nbytes <= 0:
                    break
                dst.sendall(memoryview(buf)[:nbytes])
        except Exception:
            pass
        finally:
            session.close()
            with self._session_lock:
                if self._current_session is session:
                    self._current_session = None


def main() -> None:
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
        "--print-lan-ip",
        action="store_true",
        help="Print detected primary LAN IP and exit",
    )
    args = parser.parse_args()

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
