"""
tests/unit/test_job_feed_pipeline.py
====================================
Unit tests for the deep Mobile Job Feed Pipeline (Spec #231 / Ticket #234).

The pipeline is driven against synthetic page state: a scripted list-feed and detail
page stand in for the device, so pagination, boundary termination, call-to-action
backout, quota degradation and cancellation are all exercised without Appium.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from boss_agent.feed_pipeline import (
    FeedStreamConfig,
    JobAction,
    JobFeedPipeline,
    is_task_cancelled,
    resolve_job_store,
)
from boss_agent.job_store import InMemoryJobRecordStore
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import (
    ChatButtonState,
    JobPosting,
    JobRecordStatus,
    ScreeningPolicy,
    TargetAction,
)
from boss_agent.pages import JobCardBrief
from boss_agent.screening import CandidateScreener, JobVerdictStage

GOOD_JD = (
    "岗位职责：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
    "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
)
TODAY = datetime.now(UTC).isoformat()


def _card(title: str, company: str, y: int | None = None, digest: str = "") -> JobCardBrief:
    element = MagicMock()
    if y is not None:
        element.location = {"x": 0, "y": y}
    return JobCardBrief(
        title=title,
        company_name=company,
        recruiter_name="王女士",
        salary_range="40-60K",
        digest=digest,
        element=element,
    )


def _posting(title: str = "AI Agent 平台工程师", company: str = "智元创新") -> JobPosting:
    return JobPosting(
        title=title,
        company_name=company,
        salary_range="40-60K",
        job_description=GOOD_JD,
        recruiter_name="王女士",
    )


class ScriptedFeed:
    """Viewport-by-viewport stand-in for a Boss search result feed."""

    def __init__(self, viewports: list[list[JobCardBrief]], boundary_after: int | None = None):
        self._viewports = viewports
        self._boundary_after = boundary_after
        self.scrolls = 0

    @property
    def _index(self) -> int:
        return min(self.scrolls, len(self._viewports) - 1)

    def get_feed_bottom_boundary(self) -> Any | None:
        if self._boundary_after is not None and self.scrolls >= self._boundary_after:
            boundary = MagicMock()
            boundary.text = "暂无其他符合职位，为你推荐"
            boundary.location = {"x": 0, "y": 1200}
            return boundary
        return None

    def is_feed_bottom_reached(self) -> bool:
        return self.get_feed_bottom_boundary() is not None

    def extract_visible_job_cards(self, max_cards: int = 10) -> list[JobCardBrief]:
        return list(self._viewports[self._index])

    def scroll_job_list(self) -> None:
        self.scrolls += 1

    def select_first_job(self, timeout_sec: float = 10.0) -> bool:
        return True

    def navigate_to_home(self) -> bool:
        return True

    def open_search(self, timeout_sec: float = 10.0, max_back_attempts: int = 10) -> bool:
        return True


def _pipeline(
    store: InMemoryJobRecordStore,
    *,
    feed: ScriptedFeed | None = None,
    detail: MagicMock | None = None,
    screener: CandidateScreener | None = None,
    log: Any = None,
    is_cancelled: Any = None,
    chat: MagicMock | None = None,
) -> JobFeedPipeline:
    """Build a pipeline whose page objects are scripted stand-ins."""
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage"),
        patch("boss_agent.feed_pipeline.JobListPage", return_value=feed or ScriptedFeed([])),
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage", return_value=detail or MagicMock()),
        patch("boss_agent.feed_pipeline.ChatPage", return_value=chat or MagicMock()),
    ):
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        pipeline = JobFeedPipeline(
            driver=driver,
            store=store,
            screener=screener,
            log=log,
            is_cancelled=is_cancelled,
        )
    # Rebind the scripted page objects: the patches above only applied during construction.
    if feed is not None:
        pipeline.list_page = feed
    if detail is not None:
        pipeline.detail_page = detail
    if chat is not None:
        pipeline.chat_page = chat
    pipeline.search_page.is_search_page.return_value = True
    pipeline.search_page.search.return_value = True
    return pipeline


def _detail_page(state: ChatButtonState = ChatButtonState.UNCONTACTED, posting: JobPosting | None = None):
    detail = MagicMock()
    detail.get_chat_button_state.return_value = state
    detail.extract_job_posting.return_value = posting or _posting()
    return detail


# ---------------------------------------------------------------------------
# Pagination and feed boundaries
# ---------------------------------------------------------------------------


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
    feed = ScriptedFeed([[_card("真实搜索结果", "甲公司", y=600), _card("推荐干扰项", "乙公司", y=1400)]])
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
    pipeline = _pipeline(store, feed=ScriptedFeed([]), detail=detail, is_cancelled=AsyncMock(return_value=True))

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
        stage=__import__("boss_agent.screening", fromlist=["JobVerdictStage"]).JobVerdictStage.PASSED,
        passed=True,
        reason="精筛完成",
        match_score=88,
        match_reasons=["契合"],
        jd_key_requirements=["LangGraph"],
        greeting_message="您好",
    )
    pipeline = _pipeline(store, feed=ScriptedFeed([[_card("AI Agent 工程师", "智元创新")]]), detail=_detail_page(), screener=screener)

    await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=1))

    screener.evaluate_card.assert_called_once()
    screener.evaluate_job.assert_called_once()
    assert screener.evaluate_job.call_args.kwargs["jd_text"] == GOOD_JD
    # save_jd runs never burn greeting tokens.
    assert screener.evaluate_job.call_args.kwargs["draft_greeting"] is False


# ---------------------------------------------------------------------------
# Target action: batch outreach, quota degradation, auto-send
# ---------------------------------------------------------------------------


def _apply_config(**overrides) -> FeedStreamConfig:
    data = {
        "target_action": TargetAction.AUTO_APPLY,
        "keyword": "Agent",
        "max_jobs": 5,
        "auto_send": True,
        "preview_only": False,
        "min_score": 70.0,
        "candidate_profile": StructuredCandidateProfile(name="李华", core_skills=["Python"]),
    }
    data.update(overrides)
    return FeedStreamConfig(**data)


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

    result = await pipeline.stream_jobs(_apply_config(screening_policy=ScreeningPolicy(jd_blacklist=["Java"])), on_job)

    assert result.applied_count == 2
    assert result.applied is True
    assert result.outcome == JobRecordStatus.APPLIED.value
    assert [o.action for o in outcomes] == [JobAction.APPLIED, JobAction.APPLIED]
    applied = await store.list_job_records(status="applied")
    assert len(applied) == 2
    assert all(r["applied_source"] == "agent_auto_send" for r in applied)
    assert await store.count_today_applied_jobs() == 2


@pytest.mark.asyncio
async def test_quota_exhaustion_degrades_to_offline_draft_and_keeps_discovering():
    """Once the daily limit is hit, outreach degrades to drafts but discovery continues."""
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
        store, feed=feed, detail=detail, screener=CandidateScreener(llm_client=llm), log=log, chat=chat
    )

    result = await pipeline.stream_jobs(_apply_config(max_jobs=2))

    assert result.quota_exhausted is True
    assert result.applied is False
    assert any("daily greeting limit reached" in line.lower() for line in logs)
    chat.click_send.assert_not_called()
    assert detail.open_chat.call_count == 0, "no chat is opened once the quota is spent"
    # Both jobs were still discovered and kept as drafts.
    matched = await store.list_job_records(status="matched")
    assert {r["title"] for r in matched} == {"AI Agent 一号", "AI Agent 二号"}
    assert result.outcome == JobRecordStatus.MATCHED.value


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
    assert matched[0]["greeting_message"] == "您好"
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
        store, feed=ScriptedFeed([[_card("AI Agent 一号", "甲公司")]]), detail=_detail_page(posting=truncated), log=log
    )

    await pipeline.stream_jobs(FeedStreamConfig(keyword="Agent", max_jobs=1))

    assert any("Incomplete JD Error" in line and "查看更多" in line for line in logs)
    records = await store.list_job_records()
    assert records[0]["status"] == JobRecordStatus.JD_SAVED.value


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------


def test_resolve_job_store_prefers_the_dedicated_seam():
    from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker

    broker = InMemoryTaskBroker()
    assert resolve_job_store(broker) is broker.job_store

    store = InMemoryJobRecordStore()
    assert resolve_job_store(store) is store


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
