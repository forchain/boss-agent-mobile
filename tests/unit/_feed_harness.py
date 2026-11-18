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
from unittest.mock import MagicMock

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


class FakeFeedPagePort:
    """In-memory FeedPagePort: scripted page verbs, no Appium, no page classes."""

    def __init__(
        self,
        *,
        feed: ScriptedFeed | None = None,
        detail: MagicMock | None = None,
        search: Any = None,
        chat: MagicMock | None = None,
        startup: Any = None,
    ) -> None:
        self.list_page = feed if feed is not None else ScriptedFeed([])
        self.detail_page = detail if detail is not None else MagicMock()
        self.search_page = search if search is not None else MagicMock()
        self.chat_page = chat if chat is not None else MagicMock()
        self.startup_page = startup if startup is not None else MagicMock()

    def is_startup_dialog_present(self) -> bool:
        return self.startup_page.is_dialog_present()

    def dismiss_startup_dialog(self) -> None:
        self.startup_page.dismiss_dialog()

    def navigate_to_home(self) -> bool:
        return self.list_page.navigate_to_home()

    def open_search(self, timeout_sec: float) -> bool:
        return self.list_page.open_search(timeout_sec=timeout_sec)

    def is_search_page(self) -> bool:
        return self.search_page.is_search_page()

    def search(self, keyword: str, timeout_sec: float) -> bool:
        return self.search_page.search(keyword, timeout_sec=timeout_sec)

    def get_feed_bottom_boundary(self) -> Any | None:
        return self.list_page.get_feed_bottom_boundary()

    def is_feed_bottom_reached(self) -> bool | None:
        return self.list_page.is_feed_bottom_reached()

    def extract_visible_job_cards(self, max_cards: int) -> list[LocatedJobCard]:
        return self.list_page.extract_visible_job_cards(max_cards=max_cards)

    def scroll_job_list(self) -> None:
        self.list_page.scroll_job_list()

    def select_first_job(self, timeout_sec: float) -> bool:
        return self.list_page.select_first_job(timeout_sec=timeout_sec)

    def navigate_back(self) -> None:
        self.detail_page.navigate_back()

    def navigate_back_from_chat(self) -> None:
        self.chat_page.navigate_back()

    def extract_commute_distance(self) -> tuple[float | None, str]:
        return self.detail_page.extract_commute_distance()

    def get_chat_button_state(self) -> Any:
        return self.detail_page.get_chat_button_state()

    def extract_job_posting(self, **kwargs: Any) -> JobPosting:
        return self.detail_page.extract_job_posting(**kwargs)

    def open_chat(self, timeout_sec: float) -> bool:
        return self.detail_page.open_chat(timeout_sec=timeout_sec)

    def type_greeting_message(self, greeting: str, timeout_sec: float) -> Any:
        return self.chat_page.type_greeting_message(greeting, timeout_sec=timeout_sec)

    def click_send(self, timeout_sec: float) -> bool:
        return self.chat_page.click_send(timeout_sec=timeout_sec)

    def apply_industry_filters(self, industries: list[str], timeout_sec: float) -> None:
        return None

    def apply_filters(self, filter_config: Any, timeout_sec: float) -> None:
        return None

    def clear_filters(self, timeout_sec: float) -> None:
        return None


def _seat_port(pipeline: JobFeedPipeline, port: FakeFeedPagePort) -> None:
    pipeline.page_port = port
    pipeline.list_page = port.list_page
    pipeline.detail_page = port.detail_page
    pipeline.search_page = port.search_page
    pipeline.chat_page = port.chat_page
    pipeline.startup_page = port.startup_page


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
    current = pipeline.page_port
    port = FakeFeedPagePort(
        feed=feed if feed is not None else getattr(current, "list_page", None),
        detail=detail if detail is not None else getattr(current, "detail_page", None),
        search=search if search is not None else getattr(current, "search_page", None),
        chat=chat if chat is not None else getattr(current, "chat_page", None),
        startup=getattr(current, "startup_page", None),
    )
    _seat_port(pipeline, port)
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
    search = MagicMock()
    search.is_search_page.return_value = True
    search.search.return_value = True
    port = FakeFeedPagePort(feed=feed, detail=detail, chat=chat, search=search)
    return JobFeedPipeline(
        driver=driver,
        store=store,
        screener=screener,
        log=log,
        is_cancelled=is_cancelled,
        page_port=port,
    )


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
