"""
tests/unit/_feed_harness.py
===========================
Shared scripted device for tests that drive the Mobile Job Feed Pipeline.

The pipeline is deep: pagination, screening, greeting dispatch and quota degradation all
sit behind ``stream_jobs``. Testing it without a device means standing in seven page
objects, a screener and a Job Record Store, and a second suite that needs the same seam
should not have to copy them — that is how a scripted feed drifts from the real one. So
``test_job_feed_pipeline.py`` and the greeting-provenance and JD-reuse suites all build
their pipeline through ``_pipeline()`` here.

The world is composed **whole** and injected at construction. The pipeline used to expose
its seven page objects as public attributes so a test could rebind them afterwards
(issue #481); that seam is gone — a page is either in the world the pipeline was built
with, or it is not there at all. ``scripted_world()`` is the one place that assembles a
complete set, so no suite can discover a missing screen the way the engine would.

Everything is in-memory: no Appium session, no bound port, no live LLM endpoint. Pass a
stub client into ``CandidateScreener`` — the screener fails open without one, and the only
symptom of forgetting is latency and a token bill — which is what ``stub_llm()`` is for.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from boss_agent.adapters.memory.job_store import InMemoryJobRecordStore, JobRecordStore
from boss_agent.adapters.outbound.device.pages.job_feed import LocatedJobCard, element_y_position
from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.job.domain.job_entities import JobCardBrief, JobPosting
from boss_agent.core.job.interfaces.device import FeedBoundary, FeedDeviceWorld
from boss_agent.core.job.use_cases.feed_pipeline import FeedStreamConfig, JobFeedPipeline
from boss_agent.core.job.use_cases.screening import CandidateScreener
from boss_agent.core.shared.enums import ChatButtonState, TargetAction

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
    """Viewport-by-viewport stand-in for a Boss search result feed.

    It speaks the list screen's typed vocabulary: a boundary is a ``FeedBoundary``
    (present-or-not plus a coordinate), a card is a brief plus an opaque handle, and
    opening a card is the screen's job — not the engine's.
    """

    def __init__(
        self,
        viewports: list[list[LocatedJobCard]],
        boundary_after: int | None = None,
        boundary_element: Any = None,
    ):
        self._viewports = viewports
        self._boundary_after = boundary_after
        # The banner's device handle, when a test wants one that has gone stale. The
        # screen reads its position the way the real one does, so an evicted handle
        # costs the position, not the run.
        self._boundary_element = boundary_element
        self.scrolls = 0
        # How many times the pipeline reset to the home feed before browsing. Home
        # recovery is a step the pipeline takes on a keyword-less run, and a test that
        # cannot see it can only assert that the run happened, not where it started.
        self.home_visits = 0
        # The cards the engine asked this screen to open, in order.
        self.opened_cards: list[Any] = []

    @property
    def _index(self) -> int:
        return min(self.scrolls, len(self._viewports) - 1)

    def get_feed_boundary(self) -> FeedBoundary:
        """The bottom-boundary banner: present or not, and where it sits."""
        if self._boundary_after is not None and self.scrolls >= self._boundary_after:
            if self._boundary_element is None:
                return FeedBoundary(visible=True, y_position=1200)
            return FeedBoundary(
                visible=True,
                y_position=element_y_position(self._boundary_element),
            )
        return FeedBoundary()

    def is_feed_bottom_reached(self) -> bool:
        return self.get_feed_boundary().visible

    def is_card_below_boundary(self, card: Any, boundary: FeedBoundary) -> bool:
        """Whether ``card`` sits at or below the banner — a recommendation, not a result."""
        if boundary.y_position is None:
            return False
        card_y = getattr(card, "y_position", None)
        return card_y is not None and card_y >= boundary.y_position

    def extract_visible_job_cards(self, max_cards: int = 10) -> list[LocatedJobCard]:
        return list(self._viewports[self._index])

    def scroll_job_list(self) -> None:
        self.scrolls += 1

    def select_first_job(self, timeout_sec: float = 10.0) -> bool:
        return True

    def open_card(self, card: Any = None, *, timeout_sec: float = 2.0) -> bool:
        """Open a card's detail page; the engine never touches the handle itself."""
        self.opened_cards.append(card)
        return True

    def navigate_to_home(self) -> bool:
        self.home_visits += 1
        return True

    def open_search(self, timeout_sec: float = 10.0, max_back_attempts: int = 10) -> bool:
        return True


def _scripted_search() -> MagicMock:
    """A search screen that is already on the search page and accepts any keyword."""
    search = MagicMock()
    search.is_search_page.return_value = True
    search.search.return_value = True
    return search


def scripted_world(
    *,
    feed: ScriptedFeed | None = None,
    detail: Any = None,
    search: Any = None,
    chat: Any = None,
    startup: Any = None,
    filter_page: Any = None,
    industry_filter_page: Any = None,
) -> FeedDeviceWorld:
    """Compose a **complete** scripted device world.

    Every screen the engine may reach for is present, so a suite can never pass by
    accident because a screen was missing: the world is built once, whole, and injected
    at construction (issue #481).
    """
    return FeedDeviceWorld(
        startup_page=startup if startup is not None else MagicMock(),
        list_page=feed if feed is not None else ScriptedFeed([]),
        search_page=search if search is not None else _scripted_search(),
        detail_page=detail if detail is not None else MagicMock(),
        chat_page=chat if chat is not None else MagicMock(),
        filter_page=filter_page if filter_page is not None else MagicMock(),
        industry_filter_page=(
            industry_filter_page if industry_filter_page is not None else MagicMock()
        ),
    )


def _pipeline(
    store: JobRecordStore,
    *,
    feed: ScriptedFeed | None = None,
    detail: MagicMock | None = None,
    screener: CandidateScreener | None = None,
    log: Any = None,
    is_cancelled: Any = None,
    chat: MagicMock | None = None,
    search: Any = None,
    filter_page: Any = None,
    industry_filter_page: Any = None,
    world: FeedDeviceWorld | None = None,
) -> JobFeedPipeline:
    """Build a pipeline whose device world is composed from scripted stand-ins."""
    return JobFeedPipeline(
        world=world
        if world is not None
        else scripted_world(
            feed=feed,
            detail=detail,
            chat=chat,
            search=search,
            filter_page=filter_page,
            industry_filter_page=industry_filter_page,
        ),
        store=store if store is not None else InMemoryJobRecordStore(),
        screener=screener,
        log=log,
        is_cancelled=is_cancelled,
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
