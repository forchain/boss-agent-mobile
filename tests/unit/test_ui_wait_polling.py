"""
tests/unit/test_ui_wait_polling.py
==================================
The unit tier's wall-clock contract for UI waits and gesture pauses (spec #247, ticket #248).

`tests/unit/conftest.py` removes the waiting, because a mocked driver can never satisfy a
wait loop and the tier must stay seconds-scale. These tests pin both halves of that trade:
waits and humanized pauses on a mocked driver return promptly, while their observable
behaviour — the timeout a wait raises, the value it hands back, the pause a gesture would
otherwise take — is unchanged.
"""

import time

import pytest

from droid_agent_core import locators
from droid_agent_core.gestures import HumanizedGestureExecutor

DEFAULT_BUDGET_SEC = 10.0


def test_unsatisfied_wait_returns_promptly_instead_of_burning_its_budget():
    """A 10s wait against a mocked driver must not cost 10s of wall-clock."""
    started_at = time.monotonic()

    with pytest.raises(TimeoutError) as excinfo:
        locators.wait_until(lambda: None, timeout_sec=DEFAULT_BUDGET_SEC)

    elapsed = time.monotonic() - started_at
    assert elapsed < 1.0, f"a {DEFAULT_BUDGET_SEC:.0f}s UI wait cost {elapsed:.1f}s of the tier"
    assert f"after {DEFAULT_BUDGET_SEC:.1f}s" in str(excinfo.value), str(excinfo.value)


def test_satisfied_wait_still_returns_the_condition_value():
    """Dropping the poll sleep must not change what a satisfied wait hands back."""
    calls = {"n": 0}

    def eventually_true():
        calls["n"] += 1
        return "found" if calls["n"] == 3 else None

    assert locators.wait_until(eventually_true, timeout_sec=DEFAULT_BUDGET_SEC) == "found"
    assert calls["n"] == 3, "the loop must stop the moment the condition is satisfied"


def test_humanized_gesture_pauses_do_not_cost_wall_clock():
    """A gesture's default 1.0-2.5s humanized pause must not be paid in the unit tier."""
    started_at = time.monotonic()

    HumanizedGestureExecutor().random_sleep()

    elapsed = time.monotonic() - started_at
    assert elapsed < 0.5, f"one gesture pause cost {elapsed:.1f}s of the tier"
