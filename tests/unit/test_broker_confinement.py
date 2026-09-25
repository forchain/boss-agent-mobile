"""
tests/unit/test_broker_confinement.py
=====================================
Acceptance fence for ADR 0013's confinement of the State Stream Task Broker.

ADR 0013 moved job records out of `BaseTaskBroker` into a dedicated `JobRecordStore`,
but kept a delegating `JobRecordStoreFacade` mixin "for one transition cycle". That
cycle ended: zero production callers reached record storage through a broker while
~115 test call sites did, so the shim was load-bearing only for tests. These tests
keep it from accreting back — they assert the deletion is total, not that it happened
once.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]

#: The ten Job Record Store verbs ADR 0013 moved off the broker. A broker handle must
#: not offer any of them; record access goes through `broker.job_store`.
RECORD_VERBS = (
    "upsert_job_record",
    "get_job_record_by_fingerprint",
    "has_job_fingerprint",
    "get_job_record",
    "list_job_records",
    "update_job_record_status",
    "delete_job_record",
    "get_applied_direct_companies",
    "clear_job_communication",
    "count_today_applied_jobs",
)

RETIRED_NAMES = (
    "JobRecordStoreFacade",
    # The duck-typing helper that let any broker-shaped object satisfy store-typed
    # code, so the type story stopped saying which object was passed.
    'getattr(broker, "job_store"',
    "resolve_job_store",
)


SELF = Path(__file__).resolve()


def _python_sources() -> list[Path]:
    """Every Python source, minus this file: the fence cannot grep its own needles."""
    return [
        path
        for path in sorted(REPO_ROOT.glob("src/**/*.py")) + sorted(REPO_ROOT.glob("tests/**/*.py"))
        if path.resolve() != SELF
    ]


@pytest.mark.parametrize("retired", RETIRED_NAMES)
def test_the_retired_shim_is_referenced_nowhere(retired: str) -> None:
    """Acceptance grep: no reference to the facade type or the duck-typing helper."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _python_sources()
        if retired in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"{retired!r} still referenced by: {offenders}"


@pytest.mark.parametrize("verb", RECORD_VERBS)
def test_the_abstract_broker_offers_no_record_verb(verb: str) -> None:
    """A handle typed as the base broker has zero record verbs available."""
    from boss_agent.broker.pocketbase_adapter import BaseTaskBroker

    assert not hasattr(BaseTaskBroker, verb), (
        f"BaseTaskBroker.{verb} is back — record storage belongs to JobRecordStore, "
        "reachable as broker.job_store"
    )


@pytest.mark.parametrize("verb", RECORD_VERBS)
def test_a_concrete_broker_handle_offers_no_record_verb(verb: str) -> None:
    """And neither does an instance — the shim used to be inherited by both adapters."""
    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker

    assert not callable(getattr(InMemoryTaskBroker(), verb, None))


def test_the_broker_still_composes_the_store_it_no_longer_impersonates() -> None:
    """Deleting the delegation must not delete the composition."""
    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
    from boss_agent.job_store import JobRecordStore

    broker = InMemoryTaskBroker()
    assert isinstance(broker.job_store, JobRecordStore)
    assert not isinstance(broker, JobRecordStore)
