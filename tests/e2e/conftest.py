"""
tests/e2e/conftest.py
=====================
E2E suite wiring (spec #218 ticket #222; revised by spec #247 ticket #250).

End-to-end runs used to stop residual Automation Worker / Web Dashboard instances before
the first test, so a routine `pytest tests/e2e` killed whatever the developer had running
in the background — and, because the runtime directory is shared between worktrees, that
included services belonging to other checkouts on the same machine. Enforced teardown is
now strictly opt-in:

    BOSS_AGENT_ENFORCE_TEARDOWN=1 uv run --extra dev pytest tests/e2e

Without the opt-in the suite is side-effect free: it allocates ephemeral ports and writes
its logs into pytest's `tmp_path` (see `test_web_api_logging_e2e.py`), so it coexists with
an active Web Dashboard on port 5173 and with a resident Automation Worker.

With the opt-in, `ServiceTeardownGate` stops the services recorded in
`.boss_agent/worker.pid` / `web.pid` and verifies that each one logged its shutdown — the
Automation Worker holds the Virtual Device Session and the Web Dashboard holds port 5173,
so a device-contending run would be worse than a stopped service. Services stay stopped
afterwards: relaunch them with `./run.sh worker` and `./run.sh web`.

Shared infrastructure — PocketBase (State Stream Broker), Appium, and the Android emulator
— is never touched either way.
"""

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

ENFORCE_ENV_VAR = "BOSS_AGENT_ENFORCE_TEARDOWN"
WORKER_STOP_TIMEOUT_SEC = 10.0
WEB_STOP_TIMEOUT_SEC = 20.0

_GATE_NOTES: list[str] = []


def _note(message: str) -> None:
    """Record a gate note for the terminal summary.

    Gate activity happens while pytest is still capturing output, so it is buffered here
    and flushed in `pytest_terminal_summary`, where capture is suspended and the note is
    guaranteed to be visible — developers must see *why* their services were stopped.
    """
    _GATE_NOTES.append(message)


@pytest.fixture(scope="session")
def e2e_service_teardown_gate() -> None:
    """Stop residual Worker / Web Dashboard instances — only when explicitly opted in."""
    if os.environ.get(ENFORCE_ENV_VAR) != "1":
        return

    # Imported here, not at module scope: the E2E conftest is loaded by broadly scoped
    # collections (`pytest tests`), where an inert run should not pull in service code.
    from boss_agent.services.teardown import ServiceTeardownGate, TeardownGateError

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
            f"Resolve the residual service, or re-run without {ENFORCE_ENV_VAR}=1.",
            returncode=1,
        )

    if report:
        _note("🧹 E2E pre-test teardown gate stopped residual services:")
        for line in report:
            _note(f"   {line}")
    else:
        _note("✅ E2E pre-test teardown gate: no residual services to stop.")


@pytest.fixture(scope="session", autouse=True)
def _session_loads_the_teardown_gate(e2e_service_teardown_gate: None) -> None:
    """Load the gate for every E2E session, opted in or not; the gate decides whether to act."""
    return None


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Print the gate report after capture ends, so it always reaches the developer."""
    if not _GATE_NOTES:
        return
    terminalreporter.write_sep("-", "E2E service teardown gate")
    for note in _GATE_NOTES:
        terminalreporter.write_line(note)
