"""
tests/unit/test_worker_cli_supervisor.py
========================================
Unit tests for the Automation Worker CLI supervision loop (spec #218, ticket #220).

The supervisor owns the daemon's termination contract: it must resolve on a real
SIGTERM/SIGINT, and it must never wait forever when the service loops it supervises have
already finished on their own.
"""

import asyncio
import signal

import pytest
from scripts.worker import run_services

SUPERVISION_BUDGET_SEC = 5.0


class FakeWorker:
    """Minimal stand-in recording the shutdown protocol calls the supervisor makes."""

    def __init__(self, exit_immediately: bool = False) -> None:
        self.exit_immediately = exit_immediately
        self.shutdown_calls: list[str] = []
        self.started = False

    async def start(self) -> None:
        self.started = True
        if not self.exit_immediately:
            await asyncio.Event().wait()

    async def shutdown(self, signal_name: str = "SIGTERM") -> None:
        self.shutdown_calls.append(signal_name)


async def _endless_service() -> None:
    await asyncio.Event().wait()


async def _signal_self_after(delay_sec: float, sig: signal.Signals) -> None:
    await asyncio.sleep(delay_sec)
    signal.raise_signal(sig)


@pytest.mark.asyncio
async def test_run_services_shuts_down_when_a_termination_signal_arrives():
    """Verify a real SIGTERM resolves supervision and runs the worker's shutdown protocol."""
    worker = FakeWorker()
    previous_handler = signal.signal(signal.SIGTERM, lambda *_: None)  # safety net
    try:
        await asyncio.wait_for(
            asyncio.gather(
                run_services(worker, [_endless_service()]),
                _signal_self_after(0.2, signal.SIGTERM),
            ),
            timeout=SUPERVISION_BUDGET_SEC,
        )
    finally:
        signal.signal(signal.SIGTERM, previous_handler)

    assert worker.started is True
    assert worker.shutdown_calls == ["SIGTERM"]


@pytest.mark.asyncio
async def test_run_services_does_not_hang_when_service_loops_finish_on_their_own():
    """Verify supervision ends when the worker loop returns without any signal."""
    worker = FakeWorker(exit_immediately=True)

    await asyncio.wait_for(
        run_services(worker),
        timeout=SUPERVISION_BUDGET_SEC,
    )

    assert worker.shutdown_calls, "the daemon must still shut down cleanly, not just exit"
