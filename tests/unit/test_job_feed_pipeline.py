"""
tests/unit/test_job_feed_pipeline.py
====================================
Unit tests for the deep Mobile Job Feed Pipeline (Spec #231 / Ticket #234).

The pipeline is driven against synthetic page state: a scripted list-feed and detail
page stand in for the device, so pagination, boundary termination, call-to-action
backout, quota degradation and cancellation are all exercised without Appium.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _card_fixtures import located
from _feed_harness import (
    GOOD_JD,
    TODAY,
    ScriptedFeed,
    _apply_config,
    _card,
    _detail_page,
    _pipeline,
    _posting,
)

from boss_agent.feed_pipeline import (
    FeedStreamConfig,
    JobAction,
    JobFeedPipeline,
    is_task_cancelled,
)
from boss_agent.job_store import InMemoryJobRecordStore
from boss_agent.models import (
    APPLIED_SOURCE_AGENT,
    ChatButtonState,
    JobCardBrief,
    JobPosting,
    JobRecordStatus,
    ScreeningPolicy,
    TargetAction,
)
from boss_agent.screening import CandidateScreener, JobVerdictStage


@pytest.mark.asyncio
async def test_streams_across_viewports_until_max_jobs():
    """Cards are read viewport by viewport, scrolling is humanized, and the cap holds."""
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed(
        [
            [_card("AI Agent 一号", "甲公司")],
            [_card("AI Agent 二号", "乙公司")],
            [_card("AI Agent 三号", "丙公司")],
        ]
    )
    detail = _detail_page()
    detail.extract_job_posting.side_effect = [
        _posting("AI Agent 一号", "甲公司"),
        _posting("AI Agent 二号", "乙公司"),
        _posting("AI Agent 三号", "丙公司"),
    ]
    pipeline = _pipeline(store, feed=feed, detail=detail)

    result = await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=3))

    assert result.scanned == 3
    assert result.boundary_reached is False
    assert len(result.jobs) == 3
    assert feed.scrolls >= 1
    titles = {r["title"] for r in await store.list_job_records()}
    assert titles == {"AI Agent 一号", "AI Agent 二号", "AI Agent 三号"}


@pytest.mark.asyncio
async def test_boundary_marker_terminates_pagination():
    """'暂无其他符合职位 / 为你推荐' ends the run without scrolling into recommendations."""
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed([[_card("AI Agent 一号", "甲公司")]], boundary_after=1)
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(store, feed=feed, detail=_detail_page(), log=log)

    result = await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=10))

    assert result.boundary_reached is True
    assert any("Feed Boundary" in line for line in logs)


@pytest.mark.asyncio
async def test_cards_below_the_boundary_marker_are_recommendations():
    """A card positioned below the boundary banner is not a search result."""
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed(
        [[_card("真实搜索结果", "甲公司", y=600), _card("推荐干扰项", "乙公司", y=1400)]]
    )
    feed._boundary_after = 0
    detail = _detail_page(posting=_posting("真实搜索结果", "甲公司"))
    pipeline = _pipeline(store, feed=feed, detail=detail)

    await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=10))

    # Only the card above the banner earned a detail-page visit.
    assert detail.extract_job_posting.call_count == 1
    records = await store.list_job_records()
    assert [r["title"] for r in records] == ["真实搜索结果"]


@pytest.mark.asyncio
async def test_search_failure_aborts_the_run():
    store = InMemoryJobRecordStore()
    pipeline = _pipeline(store)
    pipeline.search_page.search.return_value = False

    result = await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent"))

    assert result.search_failed is True
    assert "Agent" in (result.error_message or "")


@pytest.mark.asyncio
async def test_cancellation_stops_the_run_without_touching_the_feed():
    """A cancelled task must not fall back to evaluating whatever is on screen."""
    store = InMemoryJobRecordStore()
    detail = _detail_page()
    pipeline = _pipeline(
        store, feed=ScriptedFeed([]), detail=detail, is_cancelled=AsyncMock(return_value=True)
    )

    result = await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=5))

    assert result.cancelled is True
    detail.extract_job_posting.assert_not_called()
    assert await store.list_job_records() == []


# ---------------------------------------------------------------------------
# Call-to-action backout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_communicated_posting_is_recorded_applied_and_backed_out():
    """'继续沟通' costs one probe: no JD expansion, no tokens, and an exclusion anchor."""
    store = InMemoryJobRecordStore()
    detail = _detail_page(state=ChatButtonState.COMMUNICATED)
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[_card("大模型算法工程师", "深至科技")]]),
        detail=detail,
        screener=CandidateScreener(llm_client=MagicMock()),
    )

    result = await pipeline.stream_jobs(FeedStreamConfig(keyword="算法", max_jobs=1))

    detail.extract_job_posting.assert_not_called()
    detail.navigate_back.assert_called()
    records = await store.list_job_records(status="applied")
    assert len(records) == 1
    assert records[0]["applied_source"] == "platform_historical"
    assert not records[0]["applied_at"], "historical contacts must not consume the quota"
    assert await store.count_today_applied_jobs() == 0
    # The backout is a skip, not a scraped JD.
    assert result.jobs == []


@pytest.mark.asyncio
async def test_closed_posting_is_recorded_ignored_and_backed_out():
    store = InMemoryJobRecordStore()
    detail = _detail_page(state=ChatButtonState.CLOSED)
    pipeline = _pipeline(
        store, feed=ScriptedFeed([[_card("Agent 平台开发", "游族网络")]]), detail=detail
    )

    await pipeline.stream_jobs(FeedStreamConfig(keyword="agent", max_jobs=1))

    detail.extract_job_posting.assert_not_called()
    ignored = await store.list_job_records(status="ignored")
    assert len(ignored) == 1
    assert "停止招聘" in ignored[0]["screened_reason"]


@pytest.mark.asyncio
async def test_newly_communicated_company_suppresses_its_other_postings_in_run():
    """A contact discovered mid-run must exclude the employer's other roles immediately."""
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed(
        [
            [_card("大模型算法工程师", "深至科技")],
            [_card("算法平台工程师", "深至科技")],
        ]
    )
    detail = _detail_page(state=ChatButtonState.COMMUNICATED)
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(store, feed=feed, detail=detail, log=log)

    await pipeline.stream_jobs(FeedStreamConfig(keyword="算法", max_jobs=2))

    assert any("同企已沟通避嫌" in line for line in logs)
    assert detail.extract_job_posting.call_count == 0


# ---------------------------------------------------------------------------
# Screening integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_card_screening_rejects_before_any_detail_navigation():
    """A blacklisted card never earns a detail-page visit or an LLM call."""
    store = InMemoryJobRecordStore()
    detail = _detail_page()
    llm = MagicMock()
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[_card("高级销售经理", "黑名单外包科技")]]),
        detail=detail,
        screener=CandidateScreener(llm_client=llm),
    )

    result = await pipeline.stream_jobs(
        FeedStreamConfig(
            keyword="销售",
            max_jobs=1,
            screening_policy=ScreeningPolicy(company_blacklist=["黑名单外包科技"]),
        )
    )

    detail.extract_job_posting.assert_not_called()
    llm.chat_completion_json.assert_not_called()
    assert result.skipped == 1
    ignored = await store.list_job_records(status="ignored")
    assert "黑名单外包科技" in ignored[0]["screened_reason"]


@pytest.mark.asyncio
async def test_evaluate_card_runs_before_detail_and_evaluate_job_after():
    """The pipeline screens the card, then the full JD, through the screener seam."""
    store = InMemoryJobRecordStore()
    screener = MagicMock()
    verdict = MagicMock(passed=True, relaxed_by_whitelist=False, screening_audit="")
    screener.evaluate_card.return_value = verdict
    screener.evaluate_job.return_value = MagicMock(
        stage=__import__(
            "boss_agent.screening", fromlist=["JobVerdictStage"]
        ).JobVerdictStage.PASSED,
        passed=True,
        reason="精筛完成",
        match_score=88,
        match_reasons=["契合"],
        jd_key_requirements=["LangGraph"],
        greeting_message="您好",
    )
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[_card("AI Agent 工程师", "智元创新")]]),
        detail=_detail_page(),
        screener=screener,
    )

    await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=1))

    screener.evaluate_card.assert_called_once()
    screener.evaluate_job.assert_called_once()
    assert screener.evaluate_job.call_args.kwargs["jd_text"] == GOOD_JD
    # save_jd runs never burn greeting tokens.
    assert screener.evaluate_job.call_args.kwargs["draft_greeting"] is False


# ---------------------------------------------------------------------------
# Pre-search filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filtering_disabled_clears_conditions_instead_of_applying_them():
    """enable_filter=False clears the dialog even when conditions are configured.

    The flag has one owner — the filter config — so a run with conditions in the payload
    still clears the previous run's conditions rather than applying its own.
    """
    store = InMemoryJobRecordStore()
    pipeline = _pipeline(store, feed=ScriptedFeed([[_card("AI Agent 一号", "甲公司")]]))
    config = FeedStreamConfig.from_payload(
        {
            "keyword": "Agent",
            "max_jobs": 1,
            "enable_filter": False,
            "filter": {"education": "硕士", "salary": "20-30K", "industries": ["人工智能"]},
        }
    )
    assert config.filter_config is not None
    assert config.filter_config.enable_filter is False

    with (
        patch("boss_agent.feed_pipeline.FilterDialogPage") as filter_cls,
        patch("boss_agent.feed_pipeline.IndustryFilterDialogPage") as industry_cls,
    ):
        await pipeline.stream_jobs(config)

    filter_cls.return_value.clear_filters.assert_called_once()
    filter_cls.return_value.apply_filters.assert_not_called()
    industry_cls.return_value.apply_industry_filters.assert_not_called()


# ---------------------------------------------------------------------------
# Target action: batch outreach, quota degradation, auto-send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_outreach_dispatches_greetings_across_the_feed():
    """AUTO_APPLY paginates several cards and greets each qualified job."""
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed(
        [
            [_card("AI Agent 一号", "甲公司")],
            [_card("AI Agent 二号", "乙公司")],
        ]
    )
    detail = _detail_page()
    detail.extract_job_posting.side_effect = [
        _posting("AI Agent 一号", "甲公司"),
        _posting("AI Agent 二号", "乙公司"),
    ]
    llm = MagicMock()
    llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "契合"},
        {"match_score": 90, "match_reasons": ["契合"], "greeting_message": "您好甲"},
        {"pass": True, "reason": "契合"},
        {"match_score": 91, "match_reasons": ["契合"], "greeting_message": "您好乙"},
    ]
    chat = MagicMock()
    chat.click_send.return_value = True
    outcomes: list[Any] = []

    async def on_job(outcome):
        outcomes.append(outcome)

    pipeline = _pipeline(
        store,
        feed=feed,
        detail=detail,
        screener=CandidateScreener(llm_client=llm),
        chat=chat,
    )

    result = await pipeline.stream_jobs(
        _apply_config(screening_policy=ScreeningPolicy(jd_blacklist=["Java"])), on_job
    )

    assert result.applied_count == 2
    assert result.applied is True
    assert result.outcome == JobRecordStatus.APPLIED.value
    assert [o.action for o in outcomes] == [JobAction.APPLIED, JobAction.APPLIED]
    applied = await store.list_job_records(status="applied")
    assert len(applied) == 2
    assert all(r["applied_source"] == "agent_auto_send" for r in applied)
    assert await store.count_today_applied_jobs() == 2


@pytest.mark.asyncio
async def test_quota_is_read_once_per_card_and_the_log_reuses_that_read():
    """One daily-count round trip per card: the gate's read also feeds both log lines."""
    store = InMemoryJobRecordStore()
    reads = 0
    counted = store.count_today_applied_jobs

    async def counting():
        nonlocal reads
        reads += 1
        return await counted()

    store.count_today_applied_jobs = counting  # type: ignore[method-assign]

    feed = ScriptedFeed([[_card("AI Agent 一号", "甲公司"), _card("AI Agent 二号", "乙公司")]])
    detail = _detail_page()
    detail.extract_job_posting.side_effect = [
        _posting("AI Agent 一号", "甲公司"),
        _posting("AI Agent 二号", "乙公司"),
    ]
    llm = MagicMock()
    llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "契合"},
        {"match_score": 90, "match_reasons": ["契合"], "greeting_message": "您好甲"},
        {"pass": True, "reason": "契合"},
        {"match_score": 91, "match_reasons": ["契合"], "greeting_message": "您好乙"},
    ]
    chat = MagicMock()
    chat.click_send.return_value = True
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=feed,
        detail=detail,
        screener=CandidateScreener(llm_client=llm),
        log=log,
        chat=chat,
    )

    result = await pipeline.stream_jobs(
        _apply_config(max_jobs=2, screening_policy=ScreeningPolicy(jd_blacklist=["Java"]))
    )

    assert result.applied_count == 2
    assert reads == 2, f"expected one quota read per card, saw {reads}"
    assert any("(1/20 today)" in line for line in logs), logs
    assert any("(2/20 today)" in line for line in logs), logs


@pytest.mark.asyncio
async def test_quota_exhaustion_sends_nothing_and_keeps_the_record_resendable():
    """Once the daily limit is hit nothing goes out, discovery continues, and the record is
    left in the state the next run can actually send from (issue #298)."""
    store = InMemoryJobRecordStore()
    for i in range(20):
        await store.upsert_job_record(
            {
                "fingerprint": f"fp-applied-{i}",
                "title": f"Job {i}",
                "company_name": f"Company {i}",
                "recruiter_name": f"Recruiter {i}",
                "status": "applied",
                "applied_at": TODAY,
            }
        )
    assert await store.count_today_applied_jobs() == 20

    feed = ScriptedFeed([[_card("AI Agent 一号", "甲公司"), _card("AI Agent 二号", "乙公司")]])
    detail = _detail_page()
    detail.extract_job_posting.side_effect = [
        _posting("AI Agent 一号", "甲公司"),
        _posting("AI Agent 二号", "乙公司"),
    ]
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 90,
        "match_reasons": ["契合"],
        "greeting_message": "您好",
    }
    chat = MagicMock()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=feed,
        detail=detail,
        screener=CandidateScreener(llm_client=llm),
        log=log,
        chat=chat,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=2))

    assert result.quota_exhausted is True
    assert result.applied is False
    assert any("daily greeting limit reached" in line.lower() for line in logs)
    # Issue #298: a spent quota is "not this round", not a product tier called offline draft.
    assert any("not sent this round and stays re-sendable" in line for line in logs), logs
    assert not any("OFFLINE DRAFT" in line or "offline draft" in line for line in logs), logs
    chat.click_send.assert_not_called()
    assert detail.open_chat.call_count == 0, "no chat is opened once the quota is spent"
    # Both jobs were still discovered and kept re-sendable.
    matched = await store.list_job_records(status="matched")
    assert {r["title"] for r in matched} == {"AI Agent 一号", "AI Agent 二号"}
    assert result.outcome == JobRecordStatus.MATCHED.value


@pytest.mark.asyncio
async def test_unsent_card_after_a_dispatch_is_still_reported_as_pending_send():
    """Story 12: a quota-degraded card is unsent even once an earlier greeting went out.

    The degraded card must not be reported as skipped just because the same run had
    already dispatched a greeting to a different job.
    """
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed([[_card("AI Agent 一号", "甲公司"), _card("AI Agent 二号", "乙公司")]])
    detail = _detail_page()
    detail.extract_job_posting.side_effect = [
        _posting("AI Agent 一号", "甲公司"),
        _posting("AI Agent 二号", "乙公司"),
    ]
    llm = MagicMock()
    llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "契合"},
        {"match_score": 90, "match_reasons": ["契合"], "greeting_message": "您好甲"},
        {"pass": True, "reason": "契合"},
        {"match_score": 91, "match_reasons": ["契合"], "greeting_message": "您好乙"},
    ]
    chat = MagicMock()
    chat.click_send.return_value = True
    outcomes: list[Any] = []

    async def on_job(outcome):
        outcomes.append(outcome)

    pipeline = _pipeline(
        store,
        feed=feed,
        detail=detail,
        screener=CandidateScreener(llm_client=llm),
        chat=chat,
    )

    result = await pipeline.stream_jobs(
        _apply_config(
            max_jobs=2,
            daily_greeting_limit=1,
            screening_policy=ScreeningPolicy(jd_blacklist=["Java"]),
        ),
        on_job,
    )

    assert result.applied_count == 1
    assert result.quota_exhausted is True
    assert [o.status for o in outcomes] == ["applied", "matched"]
    assert [o.action for o in outcomes] == [
        JobAction.APPLIED,
        JobAction.PENDING_SEND,
    ], "the quota-degraded card is unsent, not skipped"
    matched = await store.list_job_records(status="matched")
    assert {r["title"] for r in matched} == {"AI Agent 二号"}


@pytest.mark.asyncio
async def test_a_queued_preview_payload_is_still_honoured_and_never_sends():
    """An in-flight task built before issue #298 keeps the depth it was written with.

    The preview tier is gone from the contract, but the wire keys stay readable precisely so
    a task already in the queue is not silently turned into a real dispatch while the
    worker upgrades underneath it (issue #298 keeps both keys for this reason).
    """
    store = InMemoryJobRecordStore()
    feed = ScriptedFeed([[_card("AI Agent 一号", "甲公司")]])
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting("AI Agent 一号", "甲公司")
    llm = MagicMock()
    llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "契合"},
        {"match_score": 90, "match_reasons": ["契合"], "greeting_message": "您好甲"},
    ]
    chat = MagicMock()
    chat.click_send.return_value = True
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=feed,
        detail=detail,
        screener=CandidateScreener(llm_client=llm),
        chat=chat,
        log=log,
    )

    legacy = _apply_config(preview_only=True, auto_send=False)
    result = await pipeline.stream_jobs(legacy)

    chat.click_send.assert_not_called()
    detail.open_chat.assert_not_called()
    assert result.applied is False
    assert await store.list_job_records(status="matched"), "the draft stays re-sendable"
    assert not any("OFFLINE DRAFT" in line for line in logs), logs


@pytest.mark.asyncio
async def test_unsent_greeting_is_not_recorded_as_applied():
    """A dispatch that never left the app stays a matched draft."""
    store = InMemoryJobRecordStore()
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 90,
        "match_reasons": ["契合"],
        "greeting_message": "您好",
    }
    chat = MagicMock()
    chat.click_send.return_value = False
    detail = _detail_page()
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[_card("AI Agent 一号", "甲公司")]]),
        detail=detail,
        screener=CandidateScreener(llm_client=llm),
        chat=chat,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1))

    assert result.applied is False
    assert await store.list_job_records(status="applied") == []
    matched = await store.list_job_records(status="matched")
    assert matched[0]["greeting_message"] == "王女士您好,幸会!"
    assert not matched[0]["applied_at"]


@pytest.mark.asyncio
async def test_below_threshold_scores_are_saved_without_outreach():
    store = InMemoryJobRecordStore()
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 40,
        "match_reasons": ["一般"],
        "greeting_message": "您好",
    }
    detail = _detail_page()
    chat = MagicMock()
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[_card("AI Agent 一号", "甲公司")]]),
        detail=detail,
        screener=CandidateScreener(llm_client=llm),
        chat=chat,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, min_score=70))

    assert result.applied is False
    chat.click_send.assert_not_called()
    assert await store.list_job_records(status="jd_saved")


@pytest.mark.asyncio
async def test_preview_mode_never_dispatches():
    store = InMemoryJobRecordStore()
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 95,
        "match_reasons": ["契合"],
        "greeting_message": "您好",
    }
    chat = MagicMock()
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[_card("AI Agent 一号", "甲公司")]]),
        detail=_detail_page(),
        screener=CandidateScreener(llm_client=llm),
        chat=chat,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, preview_only=True))

    chat.click_send.assert_not_called()
    assert result.applied is False
    assert result.outcome == JobRecordStatus.MATCHED.value


@pytest.mark.asyncio
async def test_the_post_expansion_jd_reaches_the_screener():
    """The JD the screener sees is the one the detail page finished extracting.

    Inline hotspot expansion itself is JobDetailPage's job (ADR 0009); what the pipeline
    must guarantee is that expansion output — not a truncated card snippet — is what gets
    screened, and that the visit is always unwound.
    """
    store = InMemoryJobRecordStore()
    detail = _detail_page()
    screener = MagicMock()
    screener.evaluate_card.return_value = MagicMock(
        passed=True, relaxed_by_whitelist=False, screening_audit=""
    )
    screener.evaluate_job.return_value = MagicMock(
        stage=JobVerdictStage.PASSED,
        passed=True,
        reason="精筛完成",
        match_score=88,
        match_reasons=[],
        jd_key_requirements=[],
        greeting_message="",
    )
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[_card("AI Agent 一号", "甲公司")]]),
        detail=detail,
        screener=screener,
    )

    await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=1))

    detail.extract_job_posting.assert_called_once()
    assert screener.evaluate_job.call_args.kwargs["jd_text"] == GOOD_JD
    assert detail.navigate_back.call_count >= 1


@pytest.mark.asyncio
async def test_incomplete_jd_is_flagged_and_kept_for_retry():
    """'查看更多' left in the JD is reported loudly and never treated as a verdict."""
    store = InMemoryJobRecordStore()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    truncated = JobPosting(
        title="AI Agent 一号",
        company_name="甲公司",
        salary_range="40-60K",
        job_description="岗位职责: 1. 核心智能体研发... 查看更多",
        recruiter_name="王女士",
    )
    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[_card("AI Agent 一号", "甲公司")]]),
        detail=_detail_page(posting=truncated),
        log=log,
    )

    await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=1))

    assert any("Incomplete JD Error" in line and "查看更多" in line for line in logs)
    records = await store.list_job_records()
    assert records[0]["status"] == JobRecordStatus.JD_SAVED.value


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------


def test_for_task_wires_the_brokers_job_ledger():
    """The pipeline takes the broker's job ledger at composition time.

    This replaced a duck-typing helper that fell back to the broker itself when the
    handle had no ledger, letting any broker-shaped object silently satisfy
    store-typed code — so the type story no longer said which object was passed.
    """
    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker

    broker = InMemoryTaskBroker()
    pipeline = JobFeedPipeline.for_task(broker, "task-1", driver=None)
    assert pipeline.store is broker.job_store


@pytest.mark.asyncio
async def test_is_task_cancelled_reads_the_task_lease():
    from boss_agent.broker.models import TaskStatus, TaskType
    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker

    broker = InMemoryTaskBroker()
    task = await broker.create_task(task_type=TaskType.SCRAPE_JOBS, payload={})
    assert await is_task_cancelled(broker, task.id) is False

    await broker.update_task_status(task.id, status=TaskStatus.CANCELLED)
    assert await is_task_cancelled(broker, task.id) is True


def test_config_from_payload_carries_the_task_contract():
    config = FeedStreamConfig.from_payload(
        {
            "keyword": "Agent",
            "max_jobs": 7,
            "target_action": "auto_apply",
            "min_score": 82,
            "preview_only": False,
            "auto_send": True,
            "communication_cooldown_days": 45,
            "daily_greeting_limit": 5,
            "direct_job_id": "rec-1",
            "filter": {"education": "硕士", "industries": ["人工智能"]},
            "screening_policy": {"title_blacklist": ["销售"]},
        }
    )

    assert config.keyword == "Agent"
    assert config.max_jobs == 7
    assert config.target_action == TargetAction.AUTO_APPLY
    assert config.min_score == 82.0
    assert config.single_screen is True
    assert config.direct_job_id == "rec-1"
    assert config.cooldown_days == 45
    assert config.daily_greeting_limit == 5
    assert config.filter_config is not None
    assert config.filter_config.industries == ["人工智能"]
    assert config.screening_policy.title_blacklist == ["销售"]

# ---------------------------------------------------------------------------
# The `matched` rung is not "depth reached" (issue #299)
#
# A greeting that was drafted but never delivered is not an outreach that happened. The
# ladder used to rank `matched` at the auto_apply depth, so a record left there — by the
# backend's "AI 评估" button, or by a quota-degraded run — was skipped by every later run
# and never went out. These tests pin the rung that actually counts as delivered.
# ---------------------------------------------------------------------------

_SCREEN = {"pass": True, "reason": "契合"}


def _greet(score: int = 90, greeting: str = "王总您好,幸会!") -> dict:
    return {"match_score": score, "match_reasons": ["契合"], "greeting_message": greeting}


def _llm(*verdicts: dict) -> MagicMock:
    llm = MagicMock()
    llm.chat_completion_json.side_effect = list(verdicts)
    return llm


def _drafted_record(card: JobCardBrief, **overrides) -> dict:
    """A `matched` record the way a draft leaves it: JD and greeting saved, nothing sent."""
    record = {
        "fingerprint": card.fingerprint,
        "title": card.title,
        "company_name": card.company_name,
        "recruiter_name": card.recruiter_name,
        "status": JobRecordStatus.MATCHED.value,
        "job_description": GOOD_JD,
        "greeting_message": "王总您好,幸会!我在 Agent 工作流编排上有多年经验…",
        "match_score": 88,
    }
    record.update(overrides)
    return record


@pytest.mark.asyncio
async def test_a_draft_that_never_sent_is_revisited_and_actually_sent(any_job_store):
    """The core of #299: `matched` must not read as "already greeted".

    Proven against both Job Record Store adapters, because the worker and the dashboard
    can be pointed at either and the state ladder must not disagree between them.
    """
    store = any_job_store
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_drafted_record(card.card))

    feed = ScriptedFeed([[card]])
    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=feed,
        detail=detail,
        chat=chat,
        screener=CandidateScreener(llm_client=_llm(_greet())),
        log=log,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1))

    detail.extract_job_posting.assert_called_once()
    chat.click_send.assert_called_once()
    assert result.applied is True

    stored = (await store.get_job_record_by_fingerprint(card.card.fingerprint)) or {}
    assert stored["status"] == JobRecordStatus.APPLIED.value
    assert stored["applied_source"] == APPLIED_SOURCE_AGENT
    assert stored["applied_at"], "a real dispatch carries the stamp the quota counts"
    assert not any("Skipping detail opening" in line for line in logs), logs


@pytest.mark.asyncio
async def test_a_dashboard_authored_draft_is_sendable_too():
    """A `matched` record written by the backend's "AI 评估" is the same rung.

    It never went through the feed at all, so it carries no dispatch stamp — which is
    exactly the record the old ladder buried forever.
    """
    store = InMemoryJobRecordStore()
    card = _card("大模型应用工程师", "煦象科技")
    await store.upsert_job_record(_drafted_record(card.card, match_score=None))

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=CandidateScreener(llm_client=_llm(_greet())),
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1))

    assert result.applied_count == 1
    assert chat.click_send.call_count == 1


@pytest.mark.asyncio
async def test_a_resend_counts_the_daily_quota_only_once():
    """Delivering a draft spends one slot — and re-running must not spend it twice."""
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_drafted_record(card.card))

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=CandidateScreener(llm_client=_llm(_greet(), _greet())),
    )

    await pipeline.stream_jobs(_apply_config(max_jobs=1))
    assert await store.count_today_applied_jobs() == 1

    # Second run over the same card: it is `applied` now, so it is skipped, not re-greeted.
    feed = ScriptedFeed([[card]])
    pipeline.list_page = feed
    await pipeline.stream_jobs(_apply_config(max_jobs=1))
    assert await store.count_today_applied_jobs() == 1
    assert chat.click_send.call_count == 1, "an applied record is not greeted twice in a day"


@pytest.mark.asyncio
async def test_an_unsent_greeting_does_not_anchor_the_same_company_pool():
    """A dispatch that never left the app stays re-sendable and claims no exclusion.

    Recording it as `applied` would put the employer in the 直招同企避嫌 pool without anyone
    ever being contacted, silencing its other postings for the whole cool-down window.
    """
    store = InMemoryJobRecordStore()
    card = _card("AI Agent 平台工程师", "智元创新")
    await store.upsert_job_record(_drafted_record(card.card))

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = False  # the send control was not available

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=CandidateScreener(llm_client=_llm(_greet())),
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1))

    assert result.applied is False
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.MATCHED.value
    assert not stored.get("applied_at")
    assert card.card.company_name not in await store.get_applied_direct_companies()
    assert await store.count_today_applied_jobs() == 0


@pytest.mark.asyncio
async def test_applied_and_ignored_records_are_still_skipped_for_their_own_reason():
    """The two rungs that *are* finished still skip, and the logs tell them apart."""
    store = InMemoryJobRecordStore()
    # A headhunter contact: the same-employer anchor deliberately ignores masked
    # channels, so this card reaches the state ladder instead of the 避嫌 guard.
    contacted = located(JobCardBrief(
        title="已沟通岗位", company_name="甲公司", recruiter_name="猎头王女士", is_headhunter=True
    ))
    rejected = _card("淘汰岗位", "乙公司")
    await store.upsert_job_record(
        {
            **_drafted_record(contacted.card, status=JobRecordStatus.APPLIED.value,
                              is_headhunter=True),
            "applied_at": TODAY,
            "applied_source": APPLIED_SOURCE_AGENT,
        }
    )
    await store.upsert_job_record(
        {
            **_drafted_record(rejected.card, status=JobRecordStatus.IGNORED.value),
            "screened_reason": "初筛淘汰：行业不符",
        }
    )

    detail = _detail_page()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[contacted, rejected]]),
        detail=detail,
        screener=CandidateScreener(llm_client=_llm()),
        log=log,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=2))

    detail.extract_job_posting.assert_not_called()
    assert result.skipped == 2
    assert any("冷却期内已沟通" in line for line in logs), logs
    assert any("Ignored Job" in line for line in logs), logs


@pytest.mark.asyncio
async def test_a_cooldown_expired_contact_is_released_and_greeted_again():
    """The release path is untouched by #299: a stale `applied` returns to the flow."""
    store = InMemoryJobRecordStore()
    long_ago = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    card = _card("冷却到期岗位", "丙公司")
    await store.upsert_job_record(
        {
            **_drafted_record(card.card, status=JobRecordStatus.APPLIED.value),
            "applied_at": long_ago,
            "applied_source": APPLIED_SOURCE_AGENT,
        }
    )

    detail = _detail_page()
    detail.extract_job_posting.return_value = _posting(card.card.title, card.card.company_name)
    chat = MagicMock()
    chat.click_send.return_value = True
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        chat=chat,
        screener=CandidateScreener(llm_client=_llm(_greet())),
        log=log,
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=1, cooldown_days=30))

    assert any("冷却放宽" in line for line in logs), logs
    assert result.applied_count == 1
    stored = await store.get_job_record_by_fingerprint(card.card.fingerprint)
    assert stored["status"] == JobRecordStatus.APPLIED.value


@pytest.mark.asyncio
async def test_a_draft_still_satisfies_a_depth_save_jd_run():
    """#299 lifts `matched` out of the *outreach* rung only.

    A 深度存JD sweep must not start re-opening detail pages it already holds a JD for, so a
    record past the save rung still counts as finished for a save-only run.
    """
    store = InMemoryJobRecordStore()
    card = _card("已有 JD 的岗位", "丁公司")
    await store.upsert_job_record(_drafted_record(card.card))

    detail = _detail_page()
    logs: list[str] = []

    async def log(line: str) -> None:
        logs.append(line)

    pipeline = _pipeline(
        store,
        feed=ScriptedFeed([[card]]),
        detail=detail,
        screener=CandidateScreener(llm_client=_llm()),
        log=log,
    )

    result = await pipeline.stream_jobs(
        FeedStreamConfig(target_action=TargetAction.SAVE_JD, keyword="Agent", max_jobs=1)
    )

    detail.extract_job_posting.assert_not_called()
    assert result.skipped == 1
    assert any("State Machine" in line for line in logs), logs


def test_the_write_ladder_and_the_visit_rule_answer_different_questions():
    """#299 keeps two rules apart that the old code conflated.

    `STATE_RANK` orders a record by how much is *known* about it, and that ordering is the
    monotonic write guard the store relies on — a later save-only pass must not demote an
    applied job. Whether a run still *owes* the posting work is a different question, and
    answering it with the write ladder is what silently buried every undelivered draft.
    """
    from boss_agent.models import STATE_RANK, TARGET_ACTION_RANK

    # The write ladder is unchanged, `matched` and its two documented exceptions included.
    assert STATE_RANK[JobRecordStatus.JD_SAVED] < STATE_RANK[JobRecordStatus.MATCHED]
    assert STATE_RANK[JobRecordStatus.MATCHED] < STATE_RANK[JobRecordStatus.APPLIED]
    # …and that is exactly why `matched` ranks as "enough" for an outreach run: it is
    # numerically at the auto_apply rung while still being an unsent draft.
    assert STATE_RANK[JobRecordStatus.MATCHED] == TARGET_ACTION_RANK[TargetAction.AUTO_APPLY]

    draft = {"status": JobRecordStatus.MATCHED.value, "job_description": GOOD_JD}
    applied = {**draft, "status": JobRecordStatus.APPLIED.value, "applied_at": TODAY}
    outreach = FeedStreamConfig(target_action=TargetAction.AUTO_APPLY)
    save_only = FeedStreamConfig(target_action=TargetAction.SAVE_JD)

    reached = JobFeedPipeline._depth_already_reached
    assert reached(outreach, draft["status"], draft) is False, "a draft still owes a send"
    assert reached(outreach, applied["status"], applied) is True
    assert reached(save_only, draft["status"], draft) is True
    # A save-only run with no JD on file still has work to do.
    assert reached(save_only, JobRecordStatus.UNMATCHED.value, {"job_description": ""}) is False
