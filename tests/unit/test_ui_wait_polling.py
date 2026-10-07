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


def test_page_and_workflow_settling_pauses_do_not_cost_wall_clock(pages_pacing_modules):
    """The fixed pauses pages/workflows spend letting a device render must not be paid here.

    Each of these sits between a UI action and the read that confirms it landed, so on a
    mocked driver they are dead time: the element a real device would eventually paint never
    arrives, and the wait can only ever expire. That is the same trade the wait loop and the
    gesture pause already make, and it is where the tier's wall-clock actually went.

    `pages` is a package now, so the assertion covers every submodule that owns a settling
    pause rather than one module — a submodule that kept its own `import time` would otherwise
    pay real seconds while this test still reported success.
    """
    from boss_agent import workflows

    for module in (*pages_pacing_modules, workflows):
        started_at = time.monotonic()

        module.time.sleep(2.0)

        elapsed = time.monotonic() - started_at
        assert elapsed < 0.5, f"{module.__name__} paid {elapsed:.1f}s for a settling pause"


def test_dropping_the_pause_leaves_the_rest_of_the_clock_real(pages_pacing_modules):
    """Neutralising `sleep` must not freeze the clock those modules read to do budget maths.

    A wait loop that exits on `time.time() - started < timeout_sec` only behaves the same if
    the rest of the module still hands back the real clock, so that is the half worth pinning.
    """
    from boss_agent import workflows

    for module in (*pages_pacing_modules, workflows):
        assert abs(module.time.time() - time.time()) < 1.0, (
            f"{module.__name__} reads a clock that has drifted from the real one"
        )
        assert module.time.monotonic() > 0.0
