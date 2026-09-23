"""
src/boss_agent/services/teardown.py
===================================
E2E Pre-Test Teardown Gate (spec #218, ticket #222).

End-to-end suites need exclusive use of the Virtual Device Session and of port 5173. This
gate inspects the shared runtime directory (`.boss_agent`), gracefully stops any residual
Automation Worker or Web Dashboard it finds there, verifies the services actually recorded
their shutdown, and leaves them stopped.

Shared infrastructure — PocketBase (State Stream Broker), Appium, and the Android
emulator — is deliberately out of scope and is never signalled.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from boss_agent.worker.daemon import SHUTDOWN_ACK_MARKER

# The worker's acknowledgment is owned by the producer; the dashboard's is emitted by
# `web.sh` (see its `log_web_event` call in `cmd_stop`) and can only be mirrored here.
WORKER_SHUTDOWN_ACK: str = SHUTDOWN_ACK_MARKER
WEB_SHUTDOWN_ACK: str = "Received stop command"

WORKER_PID_FILENAME: str = "worker.pid"
WEB_PID_FILENAME: str = "web.pid"
WORKER_LOG_FILENAME: str = "worker.log"
WEB_LOG_FILENAME: str = "web.log"

_POLL_INTERVAL_SEC: float = 0.05
_FORCE_KILL_GRACE_SEC: float = 2.0


class TeardownGateError(RuntimeError):
    """Raised when a conflicting service could not be verifiably shut down."""


class ServiceTeardownGate:
    """Stops residual Automation Worker / Web Dashboard instances before E2E tests run.

    `repo_root` must contain `web.sh` alongside the `.boss_agent` runtime directory it
    manages, matching the production layout.
    """

    WORKER_SHUTDOWN_ACK = WORKER_SHUTDOWN_ACK
    WEB_SHUTDOWN_ACK = WEB_SHUTDOWN_ACK

    def __init__(
        self,
        repo_root: Path,
        *,
        web_port: int = 5173,
        worker_stop_timeout_sec: float = 10.0,
        web_stop_timeout_sec: float = 20.0,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.runtime_dir = self.repo_root / ".boss_agent"
        self.web_port = web_port
        self.worker_stop_timeout_sec = worker_stop_timeout_sec
        self.web_stop_timeout_sec = web_stop_timeout_sec

    # ------------------------------------------------------------------ paths

    @property
    def worker_pid_file(self) -> Path:
        return self.runtime_dir / WORKER_PID_FILENAME

    @property
    def worker_log_file(self) -> Path:
        return self.runtime_dir / WORKER_LOG_FILENAME

    @property
    def web_pid_file(self) -> Path:
        return self.runtime_dir / WEB_PID_FILENAME

    @property
    def web_log_file(self) -> Path:
        return self.runtime_dir / WEB_LOG_FILENAME

    # -------------------------------------------------------------- detection

    @staticmethod
    def _is_alive(pid: int) -> bool:
        """Report whether `pid` can still run.

        An exited-but-unreaped process still answers `kill(pid, 0)`, so the process state
        is consulted as well: a zombie has terminated and must not keep the gate waiting.
        """
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        ps = shutil.which("ps")
        if ps is None:
            # The process state cannot be read at all. `kill(pid, 0)` just confirmed the PID
            # exists, and calling a live worker dead would skip its shutdown silently, so the
            # benefit of the doubt goes to "alive".
            return True
        result = subprocess.run(
            [ps, "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip()
        # A PID that was reaped between the two probes matches nothing and reports no state.
        if result.returncode != 0 or not state:
            return False
        return not state.startswith("Z")

    def _read_live_pid(self, pid_file: Path) -> int | None:
        """Return the PID recorded in `pid_file` if that process is still alive."""
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw.isdigit():
            return None
        pid = int(raw)
        return pid if self._is_alive(pid) else None

    def _port_listener_pid(self) -> int | None:
        """Return the PID listening on the dashboard port, if any.

        Restricted to sockets in LISTEN state: a browser, `curl`, or the E2E client itself
        merely *connected* to the port must never be mistaken for the dashboard.
        """
        lsof = shutil.which("lsof")
        if lsof is None:
            return None
        result = subprocess.run(
            [lsof, "-ti", f"tcp:{self.web_port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
        for token in result.stdout.split():
            if token.isdigit():
                return int(token)
        return None

    def _wait_for_exit(self, pid: int, timeout_sec: float) -> bool:
        """Poll until `pid` is gone, re-checking once past the deadline so a process that
        exits during the final poll interval is still reported as terminated."""
        deadline = time.monotonic() + timeout_sec
        while self._is_alive(pid) and time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL_SEC)
        return not self._is_alive(pid)

    # ------------------------------------------------------------- log slices

    @staticmethod
    def _log_size(log_file: Path) -> int:
        try:
            return log_file.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _log_tail(log_file: Path, offset: int) -> str:
        try:
            with open(log_file, encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                return handle.read()
        except OSError:
            return ""

    # ------------------------------------------------------------------- stop

    @staticmethod
    def _signal_worker(pid: int, sig: signal.Signals) -> None:
        """Deliver `sig` to the recorded worker PID together with its children.

        `run.sh` records the PID of its launcher (`uv run python3 scripts/worker.py`), whose
        child is the actual Automation Worker; signalling only the launcher can leave the real
        process — and therefore the device session — alive. Children are signalled first, since
        they are re-parented to `init` as soon as the launcher dies and can no longer be found
        by parent PID.

        A process that exits between detection and delivery is not an error: the caller only
        reports on processes it can still observe.
        """
        pkill = shutil.which("pkill")
        if pkill is not None:
            subprocess.run(
                [pkill, f"-{sig.value}", "-P", str(pid)],
                capture_output=True,
                text=True,
                check=False,
            )
        with contextlib.suppress(OSError):
            os.kill(pid, sig)

    def _require_shutdown_feedback(
        self, log_file: Path, log_offset: int, ack: str, *, failure: str
    ) -> None:
        """Raise TeardownGateError unless the service logged `ack` since `log_offset`."""
        if ack not in self._log_tail(log_file, log_offset):
            raise TeardownGateError(failure)

    def _stop_worker(self) -> list[str]:
        pid = self._read_live_pid(self.worker_pid_file)
        if pid is None:
            self.worker_pid_file.unlink(missing_ok=True)
            return []

        log_offset = self._log_size(self.worker_log_file)
        self._signal_worker(pid, signal.SIGTERM)
        exited_gracefully = self._wait_for_exit(pid, self.worker_stop_timeout_sec)
        if not exited_gracefully:
            # The launcher outlives a child that ignored SIGTERM, so the worker is still
            # reachable by parent PID here. Force-killing the launcher alone would re-parent
            # the worker to `init`, sparing it and leaving the device session claimed.
            self._signal_worker(pid, signal.SIGKILL)
            self._wait_for_exit(pid, _FORCE_KILL_GRACE_SEC)
        self.worker_pid_file.unlink(missing_ok=True)

        if exited_gracefully:
            detail = "exited without logging shutdown feedback"
            remedy = "the device session may not have been released cleanly"
        else:
            detail = f"ignored SIGTERM for {self.worker_stop_timeout_sec:.0f}s and was force-killed"
            remedy = "the device session is likely still held by Appium"
        self._require_shutdown_feedback(
            self.worker_log_file,
            log_offset,
            WORKER_SHUTDOWN_ACK,
            failure=(
                f"Automation Worker (PID {pid}) {detail}: no '{WORKER_SHUTDOWN_ACK}' line in "
                f"{self.worker_log_file}, so {remedy}. Restart the worker from this worktree so "
                "it runs the current graceful-shutdown code."
            ),
        )
        return [
            f"🛑 Stopped residual Automation Worker (PID {pid}); "
            f"verified graceful-shutdown feedback in {self.worker_log_file.name}"
        ]

    def _web_stop_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["WEB_PORT"] = str(self.web_port)
        env.setdefault("WEB_HOST", "127.0.0.1")
        env.setdefault("WEB_STOP_TIMEOUT_SEC", str(int(self.web_stop_timeout_sec)))
        return env

    def _stop_web(self) -> list[str]:
        pid = self._read_live_pid(self.web_pid_file) or self._port_listener_pid()
        if pid is None:
            self.web_pid_file.unlink(missing_ok=True)
            return []

        bash = shutil.which("bash")
        if bash is None:
            raise TeardownGateError("bash is required to stop the Web Dashboard via web.sh")

        log_offset = self._log_size(self.web_log_file)
        # web.sh may spend its graceful budget three times over (process exit, orphan reclaim,
        # port release), so the watchdog must allow for that rather than killing the runner
        # mid-recovery and reporting an unexplained timeout.
        watchdog_timeout = self.web_stop_timeout_sec * 3 + 15.0
        try:
            result = subprocess.run(
                [bash, "web.sh", "stop"],
                cwd=str(self.repo_root),
                env=self._web_stop_env(),
                capture_output=True,
                text=True,
                timeout=watchdog_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            captured = e.stdout or ""
            if isinstance(captured, bytes):
                captured = captured.decode(errors="replace")
            raise TeardownGateError(
                f"web.sh stop did not finish within {watchdog_timeout:.0f}s (PID {pid}); the "
                f"dashboard on port {self.web_port} may still be wedged:\n{captured}"
            ) from e

        if result.returncode != 0:
            raise TeardownGateError(
                f"web.sh stop failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
            )

        self._require_shutdown_feedback(
            self.web_log_file,
            log_offset,
            WEB_SHUTDOWN_ACK,
            failure=(
                f"Web Dashboard (PID {pid}) was signalled but logged no '{WEB_SHUTDOWN_ACK}' "
                f"record to {self.web_log_file}; port {self.web_port} release is unverified."
            ),
        )

        remaining = self._port_listener_pid()
        if remaining is not None:
            raise TeardownGateError(
                f"Port {self.web_port} is still held by PID {remaining} after web.sh stop."
            )

        return [
            f"🛑 Stopped residual Web Dashboard (PID {pid}); "
            f"verified shutdown feedback and port {self.web_port} release"
        ]

    # ---------------------------------------------------------------- enforce

    def enforce(self) -> list[str]:
        """Stop residual Worker and Web Dashboard instances, returning human-readable lines.

        Returns an empty list when the environment is already free of both services.
        Raises TeardownGateError when a service could not be shut down verifiably.
        """
        return [*self._stop_worker(), *self._stop_web()]
