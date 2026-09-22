"""
tests/unit/test_worker_sigterm_cli.py
=====================================
Integration test for the Automation Worker CLI termination seam (spec #218, ticket #220).

Launches a real `scripts/worker.py` process and delivers a real POSIX signal, asserting
the externally observable contract: acknowledgment log, completion log, and exit code 0
inside the shutdown budget. The broker points at a closed port so no device is touched and
no State Stream Broker is required.
"""

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _service_harness import REPO_ROOT, subprocess_env, wait_for_log

UNREACHABLE_URL = "http://127.0.0.1:1"
READINESS_MARKER = "Worker daemon loop started"
SIGNAL_ACK_FRAGMENT = "🛑 [Worker] Received shutdown signal"
SHUTDOWN_COMPLETE_LOG = "👋 [Worker] Shutdown complete."
SHUTDOWN_BUDGET_SEC = 5.0


def _wait_for_log(log_path: Path, marker: str, timeout: float = 15.0) -> str:
    """Wait for `marker` in the worker log, returning the captured output for diagnostics."""
    wait_for_log(log_path, marker, timeout=timeout)
    return log_path.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("term_signal", [signal.SIGTERM, signal.SIGINT])
def test_worker_cli_exits_cleanly_on_termination_signal(
    tmp_path: Path, term_signal: signal.Signals
):
    """Verify SIGTERM/SIGINT produce verifiable shutdown logs and a clean exit code 0."""
    log_path = tmp_path / "worker.log"
    process: subprocess.Popen | None = None

    with open(log_path, "wb") as log_handle:
        process = subprocess.Popen(
            [
                sys.executable,
                "scripts/worker.py",
                "--worker-id",
                "sigterm-cli-test-worker",
                "--device-id",
                "emulator-5554",
                "--pb-url",
                UNREACHABLE_URL,
                "--appium-url",
                UNREACHABLE_URL,
                "--poll-interval",
                "0.2",
            ],
            cwd=str(REPO_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=subprocess_env(),
            start_new_session=True,
        )

        try:
            _wait_for_log(log_path, READINESS_MARKER)

            signaled_at = time.monotonic()
            process.send_signal(term_signal)
            returncode = process.wait(timeout=SHUTDOWN_BUDGET_SEC + 2.0)
            elapsed = time.monotonic() - signaled_at
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=SHUTDOWN_BUDGET_SEC)

    output = log_path.read_text(encoding="utf-8", errors="replace")

    assert returncode == 0, f"worker exited with {returncode}; output:\n{output}"
    assert (
        f"{SIGNAL_ACK_FRAGMENT} ({term_signal.name}), initiating graceful shutdown..." in output
    ), f"missing shutdown acknowledgment for {term_signal.name}; output:\n{output}"
    assert SHUTDOWN_COMPLETE_LOG in output, f"missing completion notice; output:\n{output}"
    assert elapsed < SHUTDOWN_BUDGET_SEC, (
        f"worker took {elapsed:.2f}s to exit after {term_signal.name}, budget {SHUTDOWN_BUDGET_SEC}s"
    )
