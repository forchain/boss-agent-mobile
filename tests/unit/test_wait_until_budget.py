"""
tests/unit/test_wait_until_budget.py
====================================
`wait_until` must spend its declared timeout budget, not exceed it.

A blocking condition call (one Appium query can cost 0.1-5s) cannot be
preempted, but the poll loop must not add a trailing `poll_interval` sleep on
top of a blown budget — that is what turned `timeout_sec=0.5` into ~5.6s on
device and hid the real cost from the caller.
"""

import pytest

from droid_agent_core import locators


@pytest.fixture
def fake_clock(monkeypatch):
    """Drive wait_until's clock and record every sleep it requests."""
    clock = {"now": 1000.0}
    sleeps: list[float] = []
    monkeypatch.setattr(locators.time, "time", lambda: clock["now"])
    monkeypatch.setattr(locators.time, "sleep", lambda seconds: sleeps.append(seconds))
    return clock, sleeps


def test_no_trailing_sleep_once_budget_is_exhausted(fake_clock):
    clock, sleeps = fake_clock

    def slow_condition():  # a single Appium query that overruns the budget
        clock["now"] += 5.2
        return None

    with pytest.raises(TimeoutError):
        locators.wait_until(slow_condition, timeout_sec=0.5, poll_interval=0.4)

    assert sleeps == [], f"nothing may be slept after the budget is blown, got {sleeps}"


def test_sleeps_stay_within_remaining_budget(fake_clock):
    clock, sleeps = fake_clock

    def slowish_condition():
        clock["now"] += 0.1
        return None

    with pytest.raises(TimeoutError):
        locators.wait_until(slowish_condition, timeout_sec=1.0, poll_interval=0.4)

    assert sleeps, "still polls while budget remains"
    assert all(s <= 0.4 + 1e-9 for s in sleeps), f"never sleep past poll_interval: {sleeps}"
    assert sum(sleeps) <= 1.0, f"total sleep must stay inside the budget: {sleeps}"


def test_returns_value_as_soon_as_condition_is_truthy(fake_clock):
    clock, sleeps = fake_clock
    calls = {"n": 0}

    def eventually_true():
        clock["now"] += 0.2
        calls["n"] += 1
        return "found" if calls["n"] == 3 else None

    assert locators.wait_until(eventually_true, timeout_sec=5.0, poll_interval=0.4) == "found"
    assert len(sleeps) == 2, "must stop polling once satisfied"
