"""
tests/e2e/conftest.py
=====================
E2E Pre-Test Teardown Gate wiring (spec #218, ticket #222).

End-to-end suites need exclusive use of the Virtual Device Session and of the Web
Dashboard port. Before any test in this directory runs, the gate stops residual
Automation Worker / Web Dashboard instances that are still holding those resources and
verifies that each one recorded its shutdown — no sleeps, no guessing.

Services stay stopped afterwards (clean teardown policy): re-launch with `./run.sh worker`
and `./run.sh web` when you want them back.

Shared infrastructure — PocketBase (State Stream Broker), Appium, and the Android emulator
— is never touched. Set `BOSS_AGENT_SKIP_SERVICE_GATE=1` to bypass the gate entirely.
"""

import os
from pathlib import Path

import pytest

from boss_agent.services.teardown import ServiceTeardownGate, TeardownGateError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SKIP_ENV_VAR = "BOSS_AGENT_SKIP_SERVICE_GATE"
WORKER_STOP_TIMEOUT_SEC = 10.0
WEB_STOP_TIMEOUT_SEC = 20.0


def _note(message: str) -> None:
    """Record a gate note for the terminal summary.

    Gate activity happens while pytest is still capturing output, so it is buffered here
    and flushed in `pytest_terminal_summary`, where capture is suspended and the note is
    guaranteed to be visible — developers must see *why* their services were stopped.
    """
    _GATE_NOTES.append(message)


_GATE_NOTES: list[str] = []


@pytest.fixture(scope="session", autouse=True)
def e2e_service_teardown_gate() -> None:
    """Stop residual Worker / Web Dashboard instances before the E2E suite starts."""
    if os.environ.get(SKIP_ENV_VAR) == "1":
        _note(f"⚠️  {SKIP_ENV_VAR}=1 — E2E service teardown gate skipped.")
        return

    gate = ServiceTeardownGate(
        repo_root=REPO_ROOT,
        web_port=int(os.environ.get("WEB_PORT", "5173")),
        worker_stop_timeout_sec=WORKER_STOP_TIMEOUT_SEC,
        web_stop_timeout_sec=WEB_STOP_TIMEOUT_SEC,
    )

    try:
        report = gate.enforce()
    except TeardownGateError as e:
        pytest.exit(
            f"E2E pre-test teardown gate failed: {e}\n"
            f"Resolve the residual service (or set {SKIP_ENV_VAR}=1 to bypass the gate).",
            returncode=1,
        )

    if report:
        _note("🧹 E2E pre-test teardown gate stopped residual services:")
        for line in report:
            _note(f"   {line}")
    else:
        _note("✅ E2E pre-test teardown gate: no residual services to stop.")


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Print the gate report after capture ends, so it always reaches the developer."""
    if not _GATE_NOTES:
        return
    terminalreporter.write_sep("-", "E2E service teardown gate")
    for note in _GATE_NOTES:
        terminalreporter.write_line(note)
