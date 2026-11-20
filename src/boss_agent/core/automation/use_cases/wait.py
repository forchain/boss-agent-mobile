"""
boss_agent.core.automation.use_cases.wait
=========================================
Bounded polling of a condition, with an injectable clock and sleep.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from ..domain.errors import ElementWaitTimeoutError

T = TypeVar("T")


def wait_until(
    condition: Callable[[], T],
    timeout_sec: float = 10.0,
    poll_interval: float = 0.4,
    error_message: str = "Timed out waiting for condition",
    *,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Poll a callable condition until it returns a truthy value or timeout expires.

    A blocking condition call cannot be preempted (one Appium query can cost
    seconds), so the budget is checked *between* calls: once it is spent the loop
    exits without adding a trailing poll sleep, which would otherwise make a
    short timeout overshoot by a fixed amount.
    """
    now = clock or time.monotonic
    pause = sleep or time.sleep

    start_time = now()
    last_exception: Exception | None = None
    max_retries = max(int(timeout_sec / max(poll_interval, 0.001)), 1)
    retries = 0

    while retries < max_retries:
        retries += 1
        try:
            res = condition()
            if res:
                return res
        except Exception as e:
            last_exception = e

        remaining = timeout_sec - (now() - start_time)
        if remaining <= 0:
            break
        pause(min(poll_interval, remaining))

    msg = f"{error_message} after {timeout_sec:.1f}s"
    if last_exception:
        msg += f" (last error: {last_exception})"
    raise ElementWaitTimeoutError(msg)
