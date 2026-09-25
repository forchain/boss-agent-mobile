"""
tests/unit/conftest.py
======================
Unit-tier pacing (spec #247, ticket #248).

Unit tests drive mocked drivers, so a UI wait loop can only ever end in its timeout branch —
the element a real device would eventually show never arrives. Spending each loop's declared
budget in real time (10s by default, several waits per workflow test) bought minutes of
wall-clock and asserted nothing extra; the same is true of the humanized pauses the gesture
engine inserts between actions, which exist to defeat anti-bot detection on a device that is
not there in a unit test.

So this tier removes the waiting, not the logic behind it:

* the sleep *between* polls of `droid_agent_core.locators.wait_until` — iteration count,
  remaining-budget arithmetic, the `TimeoutError` a never-satisfied condition raises, and
  the value a satisfied one returns are all untouched;
* the pause `HumanizedGestureExecutor` takes between gestures — jitter, tap duration, and
  every action it performs are untouched.

Everything else keeps real time, including the short fixed pauses in `boss_agent.pages` and
the shutdown budgets the lifecycle suites measure. `tests/unit/test_ui_wait_polling.py`
pins both halves of the contract.

One thing pacing cannot fix is a test that reaches for a *live* LLM: the JD semantic screener
and the greeting drafter build a real client whenever a handler is handed none, and the
screener fails open, so the call shows up only as seconds of latency. Inject a stub client
into the handler under test — and to audit the tier, run it against a poisoned endpoint
(`LLM_BASE_URL=http://127.0.0.1:9 LLM_API_KEY=bogus pytest`) or with a plugin that replaces
`droid_agent_core.llm.OpenAIChatClient`.
"""

import time

import pytest

from droid_agent_core import gestures, locators


class _InstantPacingTime:
    """`time` proxy: every attribute forwards to the real module, `sleep` does nothing."""

    def __init__(self, real_time) -> None:
        self._real_time = real_time

    def sleep(self, _seconds: float) -> None:
        return None

    def __getattr__(self, name: str):
        return getattr(self._real_time, name)


@pytest.fixture(autouse=True)
def instant_ui_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let UI wait loops exhaust their budget, and gestures play out, without waiting."""
    for module in (locators, gestures):
        monkeypatch.setattr(module, "time", _InstantPacingTime(time))
