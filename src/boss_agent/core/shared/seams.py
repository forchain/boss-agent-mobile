"""
boss_agent.core.shared.seams
============================
Callable seams shared by the business aggregates and the adapters that drive them.

These are plain function contracts rather than classes: a pipeline asks its caller for
"how to log" and "have I been cancelled", and the caller already has a coroutine for
that. Keeping the aliases in one place makes the seam vocabulary agree across aggregates.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

#: Write one progress line to wherever the run's output belongs.
LogSink = Callable[[str], Awaitable[None]]

#: Ask whether the task this run belongs to has been cancelled.
CancelProbe = Callable[[], Awaitable[bool]]

#: Wait for ``seconds``, injectable so a test never waits out a settle window.
Pause = Callable[[float], Awaitable[None]]

__all__ = ["CancelProbe", "LogSink", "Pause"]
