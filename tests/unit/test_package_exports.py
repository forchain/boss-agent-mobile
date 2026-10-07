"""
tests/unit/test_package_exports.py
==================================
Guard on what ``import boss_agent`` actually binds.

Every import in ``boss_agent/__init__.py`` is wrapped in
``contextlib.suppress(ImportError)``, which is what lets the package degrade when an
optional dependency is absent. The cost is that a *real* defect — a typo, or an import
cycle — fails exactly the same way: the package imports cleanly and the name is simply
missing. Nothing raises, and the failure surfaces later as an ``ImportError`` in
whoever ran ``from boss_agent import X``.

That is not hypothetical. ``job_store`` imported ``boss_agent.broker.collection_schema``
at module scope, which ran the ``broker`` package ``__init__``, which imported
``boss_agent.job_store`` straight back while it was still executing. Seven names —
every ``JobRecordStore`` and ``JobFeedPipeline`` export — were silently unbound, and
the suite stayed green because a different module happened to re-enter the broker
package afterwards and let a retry succeed. Asserting against the namespace is what
turns that class of failure back into a red test.
"""

from __future__ import annotations

import boss_agent


def test_every_exported_name_actually_binds() -> None:
    """``__all__`` is a promise; a name listed there must resolve on the module."""
    missing = sorted(name for name in boss_agent.__all__ if not hasattr(boss_agent, name))

    assert missing == [], (
        "boss_agent.__all__ advertises names the package did not bind: "
        + ", ".join(missing)
        + ". A suppressed ImportError is hiding a real import failure."
    )
