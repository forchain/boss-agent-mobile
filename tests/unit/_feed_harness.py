"""
tests/unit/_feed_harness.py
===========================
Shared scripted device for tests that drive the Mobile Job Feed Pipeline.

The pipeline is deep: pagination, screening, greeting dispatch and quota degradation all
sit behind ``stream_jobs``. Testing it without a device means standing in four page
objects, a screener and a Job Record Store, and a second suite that needs the same seam
should not have to copy them — that is how a scripted feed drifts from the real one. So
``test_job_feed_pipeline.py`` and the greeting-provenance and JD-reuse suites all build
their pipeline through ``_pipeline()`` here.

Everything is in-memory: no Appium session, no bound port, no live LLM endpoint. Pass a
stub client into ``CandidateScreener`` — the screener fails open without one, and the only
symptom of forgetting is latency and a token bill — which is what ``stub_llm()`` is for.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from boss_agent.candidate_entities import CandidateProfile
from boss_agent.enums import ChatButtonState, TargetAction
from boss_agent.feed_pipeline import FeedStreamConfig, JobFeedPipeline
from boss_agent.job_entities import JobCardBrief, JobPosting
from boss_agent.job_store import JobRecordStore
from boss_agent.pages.job_feed import LocatedJobCard
from boss_agent.screening import CandidateScreener

TODAY = datetime.now(UTC).isoformat()

GOOD_JD = (
    "岗位职责：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
    "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
)


def _card(
    title: str,
    company: str,
    y: int | None = None,
    digest: str = "",
    location: str = "",
    recruiter_title: str = "",
) -> LocatedJobCard:
    """One scripted card: the parsed brief plus the element it would have been read from.

    ``recruiter_title`` is what the App-Enforced channel rules read: a card carrying
    ``猎头顾问`` resolves to the headhunter channel, which is the fact the direct-only
    strategies are about.
    """
    element = MagicMock()
    if y is not None:
        element.location = {"x": 0, "y": y}
    return LocatedJobCard(
        card=JobCardBrief(
            title=title,
            company_name=company,
            recruiter_name="王女士",
            recruiter_title=recruiter_title,
            salary_range="40-60K",
            digest=digest,
            location=location,
        ),
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


def stub_llm(approved: bool = True, reason: str = "stub verdict") -> MagicMock:
    """An LLM client that answers instantly, so no test in this tier reaches an endpoint.

    ``CandidateScreener`` builds a real client for itself whenever it is handed none, and
    both the JD semantic screener and the greeting drafter fail open on an error — so the
    only symptom of forgetting is seconds of latency and a token bill, never a failure. Any
    suite that lets a screening policy reach the screener should hand it this instead.
    """
    llm = MagicMock()
    llm.chat_completion_json.return_value = {"approved": approved, "reason": reason}
    return llm


class ScriptedFeed:
    """Viewport-by-viewport stand-in for a Boss search result feed."""

    def __init__(self, viewports: list[list[LocatedJobCard]], boundary_after: int | None = None):
        self._viewports = viewports
        self._boundary_after = boundary_after
        self.scrolls = 0
        # How many times the pipeline reset to the home feed before browsing. Home
        # recovery is a step the pipeline takes on a keyword-less run, and a test that
        # cannot see it can only assert that the run happened, not where it started.
        self.home_visits = 0

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

    def extract_visible_job_cards(self, max_cards: int = 10) -> list[LocatedJobCard]:
        return list(self._viewports[self._index])

    def scroll_job_list(self) -> None:
        self.scrolls += 1

    def select_first_job(self, timeout_sec: float = 10.0) -> bool:
        return True

    def navigate_to_home(self) -> bool:
        self.home_visits += 1
        return True

    def open_search(self, timeout_sec: float = 10.0, max_back_attempts: int = 10) -> bool:
        return True


def script_pages(
    pipeline: JobFeedPipeline,
    *,
    feed: ScriptedFeed | None = None,
    detail: MagicMock | None = None,
    search: Any = None,
    chat: MagicMock | None = None,
) -> JobFeedPipeline:
    """Seat scripted page objects on a pipeline that is already built.

    A run composes its own engine — the worker through ``for_task``, the interactive
    SmokeHarness in its constructor — so a test that wants a scripted device has nothing
    left to inject at construction time. Rebinding afterwards is that seam, and it is the
    same one ``_pipeline`` uses on the pipeline it builds, so a scripted feed cannot mean
    one thing here and another there.
    """
    if feed is not None:
        pipeline.list_page = feed
    if detail is not None:
        pipeline.detail_page = detail
    if search is not None:
        pipeline.search_page = search
    if chat is not None:
        pipeline.chat_page = chat
    return pipeline


def _pipeline(
    store: JobRecordStore,
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
    script_pages(pipeline, feed=feed, detail=detail, chat=chat)
    pipeline.search_page.is_search_page.return_value = True
    pipeline.search_page.search.return_value = True
    return pipeline


def _detail_page(
    state: ChatButtonState = ChatButtonState.UNCONTACTED, posting: JobPosting | None = None
):
    detail = MagicMock()
    detail.get_chat_button_state.return_value = state
    detail.extract_job_posting.return_value = posting or _posting()
    return detail


def _apply_config(**overrides) -> FeedStreamConfig:
    """A direct ``FeedStreamConfig`` for an outreach run.

    Depth is one field here — ``send_greeting`` — because that is the single answer the
    dispatch path reads. A test that wants the *wire* shape (and the legacy pair a queued
    task still carries) should go through ``FeedStreamConfig.from_payload`` instead, which
    is what ``test_depth_single_intent.py`` pins.
    """
    data = {
        "target_action": TargetAction.AUTO_APPLY,
        "keyword": "Agent",
        "max_jobs": 5,
        "send_greeting": True,
        "depth_expression": "declared_target_action",
        "min_score": 70.0,
        "candidate_profile": CandidateProfile(name="李华", core_skills=["Python"]),
    }
    data.update(overrides)
    return FeedStreamConfig(**data)
