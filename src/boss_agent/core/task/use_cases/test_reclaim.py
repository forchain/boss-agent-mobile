"""
src/boss_agent/core/task/use_cases/test_reclaim.py
==================================================
Startup decision that reclaims test-sourced tasks (GLOSSARY.md: Task Provenance).

The decision lives here so the provenance rule is testable without a worker loop; the
worker daemon owns the scan, the broker writes and the operator-facing log line.
"""

from __future__ import annotations

from typing import Any

from boss_agent.core.search.domain.launch import LaunchSource, coerce_source

#: Why a test-sourced task was cancelled. Says what happened, not who to blame.
TEST_RECLAIM_REASON = "Test-sourced task reclaimed at worker startup"

#: How many queued tasks the startup reclamation scan reads.
RECLAIM_SCAN_LIMIT = 100


def is_test_sourced(source: Any) -> bool:
    """Whether a task's provenance means an automated suite owns it."""
    return coerce_source(source) is LaunchSource.TEST


__all__ = ["RECLAIM_SCAN_LIMIT", "TEST_RECLAIM_REASON", "is_test_sourced"]
