"""
tests/unit/test_feed_pipeline_standalone.py
=============================================
The standalone execution seam of the Mobile Job Feed Pipeline (issue #389).

Every other suite that drives ``stream_jobs`` reaches the pipeline through
``_pipeline()`` with an explicit store, because the only composition that existed was the
worker one: ``for_task``, whose store is the broker's job ledger. That left the pipeline
unreachable without a PocketBase server behind it — the one thing this file closes.

What is asserted here is the seam a standalone caller actually holds: build the pipeline
with no store, drive a scripted feed, and read the extracted postings back as typed
objects instead of private-key dicts. Everything stays in memory — no broker, no Appium
session, no live LLM endpoint.
"""

from dataclasses import replace

import pytest
from _feed_harness import ScriptedFeed, _card, _detail_page, _pipeline, _posting

from boss_agent.feed_pipeline import FeedStreamConfig, JobFeedPipeline
from boss_agent.job_entities import JobPosting
from boss_agent.job_store import InMemoryJobRecordStore
from boss_agent.screening_policy import ScreeningPolicy


def test_pipeline_persists_to_an_in_memory_store_when_none_is_given():
    """Composing the pipeline without a store must still have somewhere to write."""
    pipeline = JobFeedPipeline(driver=None)

    assert isinstance(pipeline.store, InMemoryJobRecordStore)


class _EmptyLedger(InMemoryJobRecordStore):
    """A ledger that reports itself empty, the way a fresh real one does."""

    def __len__(self) -> int:
        return 0


def test_an_empty_ledger_is_honoured_rather_than_swapped_for_the_default():
    """Defaulting must key off "no store given", not off the store's truthiness.

    An empty ledger is falsy, and it is the normal state of a real one at the start of a
    run — so a truthiness-based default would hand the caller a different store the
    moment it had no records yet, and only while it had none.
    """
    store = _EmptyLedger()

    pipeline = JobFeedPipeline(driver=None, store=store)

    assert pipeline.store is store


@pytest.mark.asyncio
async def test_stream_jobs_hands_back_typed_postings_not_raw_dicts():
    """A standalone caller reads the run's extractions as ``JobPosting``, not key names.

    ``FeedStreamResult.jobs`` carries the persisted records, whose keys are the store's
    private vocabulary. They cannot change without breaking ``scrape_jobs``, so the
    postings a run actually extracted are exposed beside them as the type the platform
    screens with.
    """
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed([[_card("AI Agent 平台工程师", "智元创新")]])
    detail = _detail_page(posting=_posting("AI Agent 平台工程师", "智元创新"))
    pipeline = _pipeline(store, feed=feed, detail=detail)

    result = await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=1))

    assert [p.title for p in result.postings] == ["AI Agent 平台工程师"]
    assert result.postings[0].company_name == "智元创新"
    assert isinstance(result.postings[0], JobPosting)


@pytest.mark.asyncio
async def test_a_posting_the_run_withdraws_is_not_reported_as_extracted():
    """A card turned down at the detail stage leaves neither a record nor a posting.

    The typed list is the run's account of what it extracted, so it has to obey the same
    withdrawal the record list already does — otherwise a standalone caller reads back a
    posting the run explicitly discarded.
    """
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed([[_card("AI Agent 平台工程师", "智元创新")]])
    detail = _detail_page()
    detail.extract_job_posting.return_value = replace(
        _posting("AI Agent 平台工程师", "智元创新"),
        location_line="上海·浦东新区·陆家嘴",
    )
    pipeline = _pipeline(store, feed=feed, detail=detail)

    result = await pipeline.stream_jobs(
        FeedStreamConfig(
            keyword="Agent",
            max_jobs=1,
            screening_policy=ScreeningPolicy(
                business_district_blacklist=["陆家嘴"],
                max_commute_distance_km=None,
            ),
        )
    )

    assert result.skipped == 1
    assert result.jobs == []
    assert result.postings == []


@pytest.mark.asyncio
async def test_a_standalone_run_needs_no_broker_and_reports_telemetry():
    """The whole seam in one run: no store given, no broker, postings and telemetry back.

    This is the ticket's acceptance path — compose the pipeline with nothing but a driver,
    drive a scripted feed, and read both what was extracted and how the run went.
    """
    feed = ScriptedFeed([[_card("AI Agent 平台工程师", "智元创新")]])
    detail = _detail_page(posting=_posting("AI Agent 平台工程师", "智元创新"))
    # No store argument at all: this is what a caller with no broker behind it passes.
    pipeline = _pipeline(None, feed=feed, detail=detail)

    result = await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=1))

    assert isinstance(pipeline.store, InMemoryJobRecordStore)
    assert [p.title for p in result.postings] == ["AI Agent 平台工程师"]
    assert (result.scanned, result.processed, result.skipped) == (1, 1, 0)
    assert result.error_message is None
    # The default ledger kept the run's work, which is what makes it a ledger and not a
    # black hole: a standalone caller can read back what its own run collected.
    stored = await pipeline.store.list_job_records()
    assert [r["title"] for r in stored] == ["AI Agent 平台工程师"]
