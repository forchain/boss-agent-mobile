"""
boss_agent.core.job.use_cases.feed_pipeline
===========================================
Deep Mobile Job Feed Pipeline (ADR 0013, Spec #231).

One engine drives every mobile job-discovery run: two-anchor search entry, bounded
Back-only recovery, filter dialogs, viewport card iteration with the bottom-boundary
guard, call-to-action button inspection with instant historical backout, inline JD
expansion, card screening, full-JD evaluation and daily-quota degradation.

Worker handlers configure it and read its result; they never touch a coordinate, a
page object or a scroll. The device surface is injected as a
:class:`~boss_agent.core.job.interfaces.device.FeedDeviceWorld`, so this module never
imports Appium or constructs a page object. The only behavioural difference between
saving a JD and running batch outreach is ``FeedStreamConfig.target_action``.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from boss_agent.core.job.domain.feed_records import (
    card_facets_record,
    card_record,
    effective_company,
    effective_title,
    enriched_record,
    posting_from_record,
)
from boss_agent.core.job.domain.feed_stream import (
    FeedStreamConfig,
    FeedStreamResult,
    JobAction,
    JobOutcome,
)
from boss_agent.core.job.domain.job_entities import JobCardBrief
from boss_agent.core.job.domain.screening import (
    CardScreeningVerdict,
    CardVerdictStage,
    JobEvaluationResult,
    JobVerdictStage,
)
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.job.interfaces.device import FeedDeviceWorld
from boss_agent.core.job.interfaces.store import JobRecordStore
from boss_agent.core.job.use_cases.screening import CandidateScreener
from boss_agent.core.shared.enums import (
    ChatButtonState,
    JobRecordStatus,
    ScreeningStage,
    TargetAction,
    depth_already_reached,
)
from boss_agent.core.shared.errors import BrokerError, TransportError
from boss_agent.core.shared.identifier_helpers import (
    greeting_is_human,
    is_communication_expired,
    is_direct_hire_company,
    jd_is_usable_on_file,
)
from boss_agent.core.shared.keyword_constants import (
    APPLIED_SOURCE_AGENT,
    APPLIED_SOURCE_PLATFORM_HISTORICAL,
    EXPIRED_POSTING_REASON,
    GREETING_SOURCE_AGENT,
    GREETING_SOURCE_HUMAN,
    HEADHUNTER_COMMUTE_PROBE_SKIP_REASON,
    INVALID_JOB_TITLES,
    NOT_INSPECTED_DISTRICT_SKIP_REASON,
)
from boss_agent.core.task.interfaces.broker import BaseTaskBroker
from boss_agent.core.task.use_cases.cancellation import is_task_cancelled

logger = logging.getLogger(__name__)

__all__ = [
    "BOUNDARY_LOG",
    "JobAction",
    "JobFeedPipeline",
    "JobOutcome",
    "FeedStreamConfig",
    "FeedStreamResult",
    "card_rejection_stage",
    "station_probe_upgrade",
]


def card_rejection_stage(verdict: CardScreeningVerdict) -> ScreeningStage:
    """The persisted rejection stage for a card-stage (no-JD) verdict.

    Card screening is 初筛: it only ever sees the card, so every rejection it makes is
    attributed to the card stage regardless of which rule fired — the rule *kind* stays
    in ``screened_reason``.
    """
    if verdict.stage is CardVerdictStage.FILTERED_BY_APP_RULE:
        return ScreeningStage.CARD_APP_RULE
    return ScreeningStage.CARD_KEYWORD


MAX_SEARCH_ATTEMPTS = 2
OPEN_SEARCH_TIMEOUT_SEC = 5.0
SEARCH_SUBMIT_TIMEOUT_SEC = 10.0
CARDS_PER_VIEWPORT = 10
CONSECUTIVE_EMPTY_SCROLL_LIMIT = 3

BOUNDARY_LOG = (
    "🛑 [Feed Boundary] Detected '暂无其他符合职位 / 为你推荐' end-of-feed marker. "
    "Terminating search pagination."
)


@dataclass
class _CardRun:
    """Mutable state threaded through the per-card stages."""

    config: FeedStreamConfig
    result: FeedStreamResult
    on_job: Callable[[JobOutcome], Awaitable[None]] | None = None
    card: JobCardBrief | None = None
    # The element the brief was read from. Only the boundary check and the detail tap
    # touch it; every other stage works from `card`.
    located: Any | None = None
    card_record: dict[str, Any] = field(default_factory=dict)
    verdict: CardScreeningVerdict | None = None
    # Position of this card's record in ``result.jobs``, replaced in place by the
    # enriched record and dropped when the card turns out to be a skip.
    jobs_index: int | None = None
    # The same position in ``result.postings``, once a detail page has yielded one. It is
    # set later than ``jobs_index`` — the card record is written before the detail read —
    # and stays ``None`` when no posting was ever extracted, which is what keeps a card
    # that failed its detail read out of the typed list while remaining in ``jobs``.
    postings_index: int | None = None
    # Greetings already dispatched today, as read by this card's quota check. The read is
    # reused by the log lines instead of paying a second round trip per card.
    applied_today: int = 0
    # The record this card already has on file, before anything in this run rewrote it.
    existing_record: dict[str, Any] | None = None
    # Whose words this run must send, when the answer is not "the agent's": a human's copy,
    # plus where it came from, so the log can say it. See `_resolve_human_greeting`.
    human_greeting: str = ""
    human_greeting_origin: str = ""


def _element_y(elem: Any) -> float | None:
    """Vertical position of a UI element, or None when the driver does not report one.

    Both callers read a coordinate the pipeline will use to decide something worth
    getting wrong: the per-card boundary check drops a card sitting below the feed's
    "no more results" banner, and the banner read decides whether pagination is over. A
    missing coordinate is therefore a fact worth handling, and the one that matters most
    is an element the driver has already evicted — processing a card opens its detail
    page and navigates back, which rebuilds the feed and invalidates the handles held for
    the cards not yet reached, so the boundary check is the read most likely to meet a
    stale handle. An unreachable element has no position, so it answers None like any
    other unreadable one and its caller falls through to its existing "position unknown"
    path; letting the driver's error escape would spend a whole run to learn that one
    coordinate was unavailable.

    Duck-typed and exception-broad on purpose: the pipeline never imports the driver's
    exception types (``_open_detail`` guards its own element read the same way), and a
    handle can also fail on a driver-level error other than staleness.
    """
    if elem is None:
        return None
    try:
        location = getattr(elem, "location", None)
        if not isinstance(location, dict):
            return None
        return float(location.get("y", 0))
    except Exception:
        return None


def station_probe_upgrade(
    policy: ScreeningPolicy, is_headhunter: bool | None
) -> Callable[[str], bool]:
    """The station-aware re-decision the detail page makes before probing (#333).

    The card stage can only see a district, so it may decline a probe for a posting whose
    商圈 nobody listed but whose 地铁站 somebody did. The detail page holds the full
    location line by then, and consulting it there costs nothing — the line is captured in
    the same pass as the title, before any swipe — while the bottom probe it may authorise
    is the expensive part. The page treats a True here as an upgrade only, so a
    station-aware "no" can never overrule a card stage that had already said yes.

    Kept as a named closure rather than a bare lambda because the policy call is the whole
    point: the page stays ignorant of ScreeningPolicy and the pipeline keeps the decision.
    """

    def _should_probe_with_station(location_line: str) -> bool:
        return policy.should_probe_commute_distance(is_headhunter, location=location_line)

    return _should_probe_with_station


class JobFeedPipeline:
    """Streams screened job postings out of a mobile search feed.

    Dependencies are injected so a run can be driven against synthetic page state:
    a :class:`FeedDeviceWorld` for every screen interaction, a Job Record Store for every
    persistence decision, a Candidate Screener for every screening decision, a log sink
    and a cancellation probe. The concrete page objects are composed by the caller
    (``boss_agent.adapters.outbound.device.pages.feed_bundle`` in production), which is
    what keeps Appium out of
    this module's import graph.
    """

    def __init__(
        self,
        world: FeedDeviceWorld,
        store: JobRecordStore,
        screener: CandidateScreener | None = None,
        log: Callable[[str], Awaitable[None]] | None = None,
        is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.world = world
        self.store = store
        self.screener = screener or CandidateScreener()
        self._log_sink = log
        self._cancel_probe = is_cancelled

        # The five attributes the engine has always exposed; the filter dialogs are part
        # of the same injected bundle but stay private to the run.
        self.startup_page = world.startup_page
        self.list_page = world.list_page
        self.search_page = world.search_page
        self.detail_page = world.detail_page
        self.chat_page = world.chat_page
        self._filter_page = world.filter_page
        self._industry_filter_page = world.industry_filter_page

        # Companies contacted during this run: a newly discovered communication must
        # suppress the company's other postings immediately, not on the next pass.
        self._excluded_companies: set[str] = set()

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------
    @classmethod
    def for_task(
        cls,
        broker: BaseTaskBroker,
        task_id: str,
        world: FeedDeviceWorld,
        *,
        store: JobRecordStore,
        screener: CandidateScreener | None = None,
    ) -> "JobFeedPipeline":
        """Wire a pipeline to a worker task: its job ledger, log sink and cancel probe.

        The job ledger is passed explicitly rather than pulled off the broker port,
        keeping ``BaseTaskBroker`` confined to task-stream lifecycle verbs (ADR 0013).
        """
        return cls(
            world=world,
            store=store,
            screener=screener,
            log=lambda line: broker.append_log(task_id, line),
            is_cancelled=lambda: is_task_cancelled(broker, task_id),
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def stream_jobs(
        self,
        config: FeedStreamConfig,
        on_job: Callable[[JobOutcome], Awaitable[None]] | None = None,
    ) -> FeedStreamResult:
        """Run one feed pass and return what happened to every job it saw."""
        result = FeedStreamResult()

        if self.startup_page and self.startup_page.is_dialog_present():
            self.startup_page.dismiss_dialog()

        if config.screening_policy and config.screening_policy.is_commute_filter_active:
            await self._log(
                f"📍 [App端强制过滤] Active ceiling {config.screening_policy.max_commute_distance_km:.1f}km; "
                f"probing detail page bottom for the distance widget (direct-hire postings only)."
            )

        if config.retired_direct_target:
            # The single-screen branch, kept as a refusal. Issue #428 removed the path that
            # could greet one posting on screen, but a task queued by the pre-#428 builder
            # can still arrive carrying `direct_job_id`, and that payload also carries a
            # keyword. Failing closed here is the whole point: falling through to the
            # search below would sweep the board for that keyword and greet up to the
            # ceiling, spending quota the queued task never asked to spend.
            if config.depth_warning:
                await self._log(f"⚠️ [Legacy Depth] {config.depth_warning}")
            await self._log(
                "🛑 [Retired Path] 定向投递 single-screen execution is retired; this task "
                "is refused without searching, greeting or reading a card."
            )
            result.outcome = "retired_direct_target"
            result.reason = config.depth_warning
            return result

        if config.enable_search and config.keyword:
            if not await self._enter_search(config.keyword):
                result.search_failed = True
                result.error_message = f"Failed to execute search for keyword '{config.keyword}'"
                return result
            await self._log(f"Executed search for keyword '{config.keyword}'")
        elif not config.enable_search:
            self.list_page.navigate_to_home()
            await self._log(
                "enable_search is False; browsing home recommendations without search keyword"
            )

        await self._apply_filters(config)
        try:
            self._excluded_companies = await self.store.get_applied_direct_companies(
                cooldown_days=config.cooldown_days
            )
        except (BrokerError, TransportError) as e:
            await self._log(
                f"⚠️ [持久化降级] 直招避嫌池读取遇到持久化异常（{e}），中断投递以避免重复沟通"
            )
            raise
        if self._excluded_companies:
            await self._log(
                f"🏢 [避嫌池] 已加载 {len(self._excluded_companies)} 家已沟通直招企业"
                f"（冷却期 {config.cooldown_days or '永久'}）"
            )

        await self._scan_feed(config, result, on_job)
        return result

    # ------------------------------------------------------------------
    # Search entry, filters, pagination
    # ------------------------------------------------------------------
    async def _enter_search(self, keyword: str) -> bool:
        """Enter the search screen and submit ``keyword``, retrying once on failure."""
        for attempt in range(MAX_SEARCH_ATTEMPTS):
            if not self.search_page.is_search_page():
                self.list_page.open_search(timeout_sec=OPEN_SEARCH_TIMEOUT_SEC)
            if self.search_page.search(keyword, timeout_sec=SEARCH_SUBMIT_TIMEOUT_SEC):
                return True
            if attempt < MAX_SEARCH_ATTEMPTS - 1:
                await self._log(
                    f"⚠️ 第 {attempt + 1}/{MAX_SEARCH_ATTEMPTS} 次进入搜索页面并搜索 '{keyword}'"
                    f"失败，准备第 {attempt + 2} 次尝试..."
                )

        await self._log(
            f"❌ 已尝试 {MAX_SEARCH_ATTEMPTS} 次仍未能进入搜索页面或执行关键词搜索: '{keyword}'，"
            f"重试次数已耗尽，终止任务以避免误操作推荐流"
        )
        return False

    async def _apply_filters(self, config: FeedStreamConfig) -> None:
        """Apply the pre-search filter dialogs, clearing stale conditions when none apply.

        A previous run's conditions persist inside the app's filter dialog, so a run with
        no general filters (or with filtering disabled) must actively clear them rather
        than inherit somebody else's search.
        """
        # The filter config carries its own enable flag: a disabled run still clears the
        # conditions a previous run left in the dialog.
        filter_cfg = config.filter_config

        if filter_cfg and filter_cfg.has_industry_filters:
            await self._log(f"Applying industry filter: {filter_cfg.industries}")
            try:
                self._industry_filter_page.apply_industry_filters(
                    filter_cfg.industries, timeout_sec=5.0
                )
            except Exception as ex:
                await self._log(f"Notice applying industry filter: {ex}")

        filter_page = self._filter_page
        if filter_cfg and filter_cfg.has_filters:
            await self._log(
                f"Applying general filters: education={filter_cfg.education}, "
                f"salary={filter_cfg.salary}, experience={filter_cfg.experience}"
            )
            try:
                filter_page.apply_filters(filter_cfg, timeout_sec=5.0)
            except Exception as ex:
                await self._log(f"Notice applying general filters: {ex}")
        else:
            await self._log(
                "No active general filters configured; actively clearing filter dialog conditions"
            )
            try:
                filter_page.clear_filters(timeout_sec=5.0)
            except Exception as ex:
                await self._log(f"Notice clearing general filters: {ex}")

    async def _scan_feed(
        self,
        config: FeedStreamConfig,
        result: FeedStreamResult,
        on_job: Callable[[JobOutcome], Awaitable[None]] | None,
    ) -> None:
        """Walk the result feed viewport by viewport until the run's target is met."""
        scanned_fingerprints: set[str] = set()
        consecutive_empty_scrolls = 0

        while len(scanned_fingerprints) < config.max_jobs:
            if await self._is_cancelled():
                result.cancelled = True
                await self._log(
                    "🛑 [Task Cancelled] Task was cancelled by user. Terminating scrape pagination."
                )
                return

            bottom_elem = self.list_page.get_feed_bottom_boundary()
            boundary_y = _element_y(bottom_elem)
            visible_cards = self.list_page.extract_visible_job_cards(max_cards=CARDS_PER_VIEWPORT)
            if not visible_cards:
                if bottom_elem is not None:
                    result.boundary_reached = True
                    await self._log(BOUNDARY_LOG)
                break

            new_cards_in_view = 0
            for located in visible_cards:
                card = located.card
                if len(scanned_fingerprints) >= config.max_jobs:
                    break
                card_y = _element_y(located.element)
                if boundary_y is not None and card_y is not None and card_y >= boundary_y:
                    # Below the boundary marker: a recommendation, not a search result.
                    continue
                if card.fingerprint in scanned_fingerprints:
                    continue
                scanned_fingerprints.add(card.fingerprint)
                new_cards_in_view += 1
                result.scanned = len(scanned_fingerprints)

                try:
                    await self._process_card(
                        _CardRun(
                            config=config,
                            result=result,
                            on_job=on_job,
                            card=card,
                            located=located,
                        )
                    )
                except Exception as e:
                    # One unrecoverable card must not abandon the rest of the batch:
                    # report it and carry on with the next result.
                    result.skipped += 1
                    logger.exception("Card processing failed for '%s'", card.title)
                    await self._log(
                        f"❌ [Card Error] Failed to process '{card.title}' @ "
                        f"'{card.company_name}': {e}"
                    )
                if result.cancelled:
                    return

            if len(scanned_fingerprints) >= config.max_jobs:
                await self._log(
                    f"🎯 [Scan Quota Reached] Completed scan of {len(scanned_fingerprints)} jobs "
                    f"(max_jobs={config.max_jobs})."
                )
                break

            if new_cards_in_view == 0:
                consecutive_empty_scrolls += 1
                if consecutive_empty_scrolls >= CONSECUTIVE_EMPTY_SCROLL_LIMIT:
                    await self._log(
                        "🛑 [Feed Safeguard] 3 consecutive scrolls produced no new cards. "
                        "Terminating pagination."
                    )
                    break
            else:
                consecutive_empty_scrolls = 0

            if bottom_elem is not None or self.list_page.is_feed_bottom_reached() is True:
                result.boundary_reached = True
                await self._log(BOUNDARY_LOG)
                break

            self.list_page.scroll_job_list()

        if result.scanned == 0 and not result.cancelled:
            # No card was ever readable: the operator may be parked on a detail page
            # (or a driver double is standing in for one), so evaluate what is on screen.
            await self._evaluate_current_posting(
                _CardRun(config=config, result=result, on_job=on_job)
            )

    # ------------------------------------------------------------------
    # Per-card stages
    # ------------------------------------------------------------------
    async def _process_card(self, run: _CardRun) -> None:
        """Gate one card on card-level rules before spending a detail-page visit on it."""
        card = run.card
        assert card is not None
        result, config = run.result, run.config

        company = (card.company_name or "").strip()
        if not company or company == "未知公司":
            result.skipped += 1
            await self._log(
                f"⏭️ [Incomplete Card] Skipping partially visible card without company name: "
                f"'{card.title}'"
            )
            return

        if (
            is_direct_hire_company(company, card.is_headhunter)
            and company in self._excluded_companies
        ):
            result.skipped += 1
            await self._log(
                f"⏭️ [同企已沟通避嫌] '{card.title}' @ '{company}' "
                f"直招企业已有沟通，跳过该企业其他岗位"
            )
            return

        existing_record = await self.store.get_job_record_by_fingerprint(card.fingerprint)
        if existing_record and not await self._passes_state_machine(run, existing_record, company):
            return

        run.verdict = self.screener.evaluate_card(card, config.screening_policy)
        if not run.verdict.passed:
            await self._persist_rejection(run, run.verdict)
            return
        if run.verdict.relaxed_by_whitelist:
            await self._log(
                f"🎗️ [白名单放宽] '{card.title}' 命中 '{run.verdict.matched_token}' "
                f"豁免渠道限制，继续采集"
            )

        run.card_record = card_record(
            card,
            keyword=config.keyword,
            source_task_id=config.source_task_id,
            verdict=run.verdict,
            existing_record=existing_record,
        )
        persisted = await self.store.upsert_job_record(dict(run.card_record))
        if persisted is None:
            # Incomplete/placeholder card: nothing was written, so there is no record to
            # inspect, index, or count. Appending the empty result would inflate the run's
            # job list and hand ``_inspect_detail`` a record with no id (Spec #303, story #8).
            return
        # The card itself is already a result: a detail-page failure must not lose it.
        run.result.jobs.append(persisted)
        run.jobs_index = len(run.result.jobs) - 1
        run.existing_record = existing_record
        await self._inspect_detail(run, persisted, existing_record)

    async def _passes_state_machine(
        self, run: _CardRun, existing_record: dict[str, Any], company: str
    ) -> bool:
        """Decide whether a previously seen card deserves a fresh detail-page visit."""
        card = run.card
        assert card is not None
        result, config = run.result, run.config
        card_record_identity = {
            "fingerprint": card.fingerprint,
            "company_name": card.company_name,
            "title": card.title,
            "recruiter_name": card.recruiter_name,
            "search_keywords": [config.keyword] if config.keyword else [],
        }

        existing_status = existing_record.get("status", "unmatched")
        if existing_status == JobRecordStatus.IGNORED:
            result.skipped += 1
            await self._log(
                f"⏭️ [Ignored Job] Skipping previously rejected job: '{card.title}' @ "
                f"'{card.company_name}'"
            )
            return False

        is_released = False
        if existing_status == JobRecordStatus.APPLIED:
            is_released = is_communication_expired(existing_record, config.cooldown_days)
            if not is_released:
                result.skipped += 1
                await self._log(
                    f"⏭️ [已沟通岗位] '{card.title}' @ '{card.company_name}' "
                    f"冷却期内已沟通，跳过详情页"
                )
                await self.store.upsert_job_record(card_record_identity)
                return False
            # Transition back to jd_saved so fresh evaluation can persist a new state
            # while the previously extracted JD is preserved.
            await self.store.clear_job_communication(existing_record["id"])
            await self._log(
                f"♻️ [冷却放宽] '{card.title}' @ '{card.company_name}' 距上次沟通已超过 "
                f"{config.cooldown_days} 天，已释放回待评估流"
            )

        if not is_released and depth_already_reached(
            config.target_action, existing_status, existing_record
        ):
            result.skipped += 1
            await self._log(
                f"⏭️ [State Machine] '{card.title}' already at '{existing_status}' "
                f"(target '{config.target_action.value}' reached). Skipping detail opening."
            )
            await self.store.upsert_job_record(card_record_identity)
            return False
        return True

    async def _persist_rejection(self, run: _CardRun, verdict: CardScreeningVerdict) -> None:
        """Record a card that never earned a detail-page visit."""
        card = run.card
        assert card is not None
        run.result.skipped += 1
        if verdict.stage is CardVerdictStage.FILTERED_BY_APP_RULE:
            await self._log(
                f"🛑 [App端强制过滤] '{card.title}' @ '{card.company_name}': {verdict.reason}"
            )
        else:
            await self._log(
                f"⏭️ [初筛淘汰] '{card.title}' @ '{card.company_name}': {verdict.reason}"
            )
        await self.store.upsert_job_record(
            {
                **card_facets_record(
                    card,
                    keyword=run.config.keyword,
                    source_task_id=run.config.source_task_id,
                ),
                "status": JobRecordStatus.IGNORED.value,
                "screened_reason": verdict.reason,
                "screening_stage": card_rejection_stage(verdict).value,
                "relaxed_by_whitelist": False,
                "screening_audit": verdict.screening_audit,
            }
        )

    async def _inspect_detail(
        self,
        run: _CardRun,
        persisted: dict[str, Any],
        existing_record: dict[str, Any] | None,
    ) -> None:
        """Open the detail page, screen the JD, and apply the target action."""
        card = run.card
        assert card is not None
        tag = "[猎头]" if getattr(card, "is_headhunter", False) else "[直招]"
        await self._log(
            f"🔍 [Detail Inspection] Inspecting {tag} '{card.title}' @ '{card.company_name}'"
        )

        if not await self._open_detail(run.located):
            return
        try:
            if await self._back_out_if_contacted(run, card.fingerprint):
                return
            posting = await self._read_posting(run, card, existing_record)
            if posting is None:
                return
            # From here the posting exists as a thing the run extracted, whether or not
            # the stage below keeps it; the typed list records that, and ``_reject_...``
            # withdraws it again if the detail-stage rules turn the card down (issue #389).
            run.result.postings.append(posting)
            run.postings_index = len(run.result.postings) - 1
            await self._evaluate_and_act(run, posting)
        finally:
            self.detail_page.navigate_back()

    async def _read_posting(
        self,
        run: _CardRun,
        card: JobCardBrief,
        existing_record: dict[str, Any] | None,
    ) -> Any | None:
        """The posting behind this card — from the record's JD when it can be, else from the device.

        Issue #301. A card that comes back for a second look has usually been read already,
        and the detail-page work that costs real time — scrolling to the `查看更多` hotspot,
        tapping it, re-reading the body, hunting the distance widget below the fold — buys
        nothing when the Job Record already holds a usable JD. So that is asked first.

        The contact-control probe is deliberately *not* skipped: a posting the platform has
        already communicated with, or that has stopped hiring, is worth no evaluation work at
        all and must still be recorded as such. That check costs one read of the button.
        """
        stored_jd = str((existing_record or {}).get("job_description") or "").strip()
        inventory_jd = stored_jd if jd_is_usable_on_file(stored_jd) else ""
        if not inventory_jd:
            return await self._extract_posting(card, run.config.screening_policy)

        record = existing_record or {}
        commute_km, commute_text = await self._commute_for_inventory(run, card, record)
        # The mapping is owned by feed_records, beside its inverse: a card judged from the
        # stored record must be judged from the same facets a live read would have supplied,
        # including the placeholder guards on title and company (#301).
        posting = posting_from_record(
            record,
            card,
            inventory_jd,
            commute_distance_km=commute_km,
            commute_distance_text=commute_text,
        )
        skipped = "「查看更多」展开与正文重复抽取"
        if commute_km is not None:
            skipped += "、底部通勤距离探测"
        await self._log(
            f"♻️ [复用库存 JD] '{posting.title}' 使用岗位记录中的 JD（{len(inventory_jd)} 字），"
            f"跳过{skipped}"
        )
        return posting

    async def _commute_for_inventory(
        self, run: _CardRun, card: JobCardBrief, record: dict[str, Any]
    ) -> tuple[float | None, str]:
        """The commute distance for an inventory visit: reuse it, probe it, or call it unknown.

        Only the text is reusable wholesale. With the ceiling active and no distance ever
        measured for this posting, skipping the probe would turn a real rejection into a
        fail-open pass — so the widget is still fetched, just without the expansion and the
        re-read wrapped around it, which is where the time actually goes. A headhunter
        posting is spared exactly as a fresh visit spares it, and an unknown distance stays
        fail-open.
        """
        stored = record.get("commute_distance_km")
        if stored is not None:
            return float(stored), str(record.get("commute_distance_text") or "")

        policy = run.config.screening_policy
        is_headhunter = bool(record.get("is_headhunter") or card.is_headhunter)
        location = str(record.get("location") or card.location or "")
        if not policy.should_probe_commute_distance(is_headhunter, location=location):
            if policy.is_commute_filter_active and is_headhunter:
                await self._log(
                    f"📍 [App端强制过滤] '{card.title}' {HEADHUNTER_COMMUTE_PROBE_SKIP_REASON}"
                )
            return None, ""

        try:
            # The page object owns its own swipe budget; this path only declines to re-read
            # the description around the probe.
            distance_km, distance_text = self.detail_page.extract_commute_distance()
        except Exception as e:
            logger.warning("Commute probe failed for '%s': %s", card.title, e)
            return None, ""
        return distance_km, str(distance_text or "")

    async def _open_detail(self, located: Any | None) -> bool:
        """Tap the card (or fall back to the first list item) to reach its detail page.

        The one place besides the boundary check that needs the device handle rather
        than the parsed brief.
        """
        element = located.element if located is not None else None
        if element is not None and hasattr(element, "click"):
            try:
                element.click()
                return True
            except Exception:
                pass
        return self.list_page.select_first_job(timeout_sec=2.0)

    async def _back_out_if_contacted(self, run: _CardRun, fingerprint: str) -> bool:
        """Zero-cost backout: consult the call-to-action button before reading any JD.

        A posting already communicated with, or one that has stopped hiring, is worth
        nothing to evaluate — and a platform contact must still be recorded as `applied`
        so its company joins the same-company exclusion pool.
        """
        chat_state = self.detail_page.get_chat_button_state()
        if chat_state not in (ChatButtonState.COMMUNICATED, ChatButtonState.CLOSED):
            return False

        card = run.card
        assert card is not None
        run.result.skipped += 1
        if run.jobs_index is not None:
            # Drop the optimistic card-level record: this card is a skip, not a scraped JD.
            run.result.jobs.pop(run.jobs_index)
            run.jobs_index = None
        terminal = dict(
            run.card_record
            or card_facets_record(
                card,
                keyword=run.config.keyword,
                source_task_id=run.config.source_task_id,
            )
        )
        if chat_state == ChatButtonState.COMMUNICATED:
            terminal["status"] = JobRecordStatus.APPLIED.value
            terminal["applied_source"] = APPLIED_SOURCE_PLATFORM_HISTORICAL
            company = (card.company_name or "").strip()
            if is_direct_hire_company(company, terminal.get("is_headhunter")):
                self._excluded_companies.add(company)
            await self._log(
                f"⏭️ [既有沟通] '{card.title}' @ '{card.company_name}' 平台已沟通，免提 JD 直接退出"
            )
        else:
            terminal["status"] = JobRecordStatus.IGNORED.value
            terminal["screened_reason"] = EXPIRED_POSTING_REASON
            terminal["screening_stage"] = ScreeningStage.EXPIRED.value
            await self._log(
                f"🛑 [岗位失效] '{card.title}' @ '{card.company_name}' 已停止招聘/下线，跳过"
            )
        saved = await self.store.upsert_job_record(terminal)
        await self._emit(
            run,
            JobOutcome(
                fingerprint=fingerprint,
                title=card.title,
                company_name=card.company_name,
                status=str(terminal["status"]),
                action=JobAction.SKIPPED,
                reason=terminal.get("screened_reason", ""),
                record=saved or {},
            ),
        )
        return True

    async def _extract_posting(
        self, card: JobCardBrief, policy: ScreeningPolicy | None = None
    ) -> Any | None:
        """Read the detail page, returning None when no usable title is on screen.

        A missing detail-page title is not fatal: the card's own title still identifies
        the posting, and dropping the enrichment would lose a job we already hold.
        """
        card_is_headhunter = getattr(card, "is_headhunter", False)
        card_location = str(getattr(card, "location", "") or "")
        resolved_policy = policy or ScreeningPolicy()
        should_probe = resolved_policy.should_probe_commute_distance(
            card_is_headhunter, location=card_location
        )
        if resolved_policy.is_commute_filter_active and not should_probe:
            reason = (
                HEADHUNTER_COMMUTE_PROBE_SKIP_REASON
                if card_is_headhunter
                else NOT_INSPECTED_DISTRICT_SKIP_REASON
            )
            await self._log(f"📍 [App端强制过滤] '{card.title}' @ '{card.company_name}' {reason}")

        try:
            posting = self.detail_page.extract_job_posting(
                timeout_sec=4.0,
                fallback_company=card.company_name,
                fallback_title=card.title,
                probe_commute_distance=should_probe,
                is_headhunter=card_is_headhunter,
                commute_probe_upgrade=station_probe_upgrade(resolved_policy, card_is_headhunter),
            )
        except Exception as e:
            logger.error("Failed to extract detail for '%s': %s", card.title, e)
            return None

        if effective_title(posting, card) in INVALID_JOB_TITLES:
            logger.warning(
                "Skipping job detail enrichment due to missing/invalid title: '%s' @ '%s'",
                posting.title,
                posting.company_name,
            )
            return None
        return posting

    def _resolve_human_greeting(self, run: _CardRun) -> tuple[str, str]:
        """The human-authored greeting this run must send verbatim, and where it came from.

        One source outranks the agent (issue #300): the record's own greeting, when its
        provenance says a human wrote it. Anything else — an agent draft, a record predating
        provenance, a marker whose text is empty — returns ``("", "")`` and the run drafts
        as it always did. The origin comes back with the text so the log can name the source
        instead of guessing.
        """
        if greeting_is_human(run.existing_record):
            return str(run.existing_record.get("greeting_message")).strip(), "岗位记录人工稿"
        return "", ""

    async def _reject_detail_posting(
        self,
        run: _CardRun,
        card: JobCardBrief,
        enriched: dict[str, Any],
        posting: Any,
        *,
        reason: str,
        audit_note: str,
        log_prefix: str,
    ) -> None:
        """End a run whose detail page has already decided the posting is unacceptable.

        Shared by the two detail-stage App-Enforced Filters — the business district
        blacklist (issue #333) and the commute ceiling (spec #209) — so they cannot drift
        in what they leave behind: the optimistic card record is withdrawn rather than left
        standing as a collected job, the stored record carries the reason and the audit
        trail, and the outcome is emitted once.

        Both callers now arrive having already settled the severity question (#438), so
        the job's final standing is a rejection. ``relaxed_by_whitelist`` therefore
        records the OUTCOME — was this job admitted through a relaxation? — and is
        False here even when an earlier stage had relaxed it: the card-stage channel
        relaxation it may have earned did not survive the hard commute verdict. The
        field and ``screening_audit`` do not contradict each other; the audit is the
        history (it still reads 【白名单放宽】 for the rescued channel violation) and the
        field is where that history ended up. This only records and reports the verdict.
        """
        enriched["relaxed_by_whitelist"] = False
        enriched["screening_audit"] = "；".join(
            p for p in (enriched.get("screening_audit", ""), audit_note) if p
        )
        enriched["status"] = JobRecordStatus.IGNORED.value
        enriched["screened_reason"] = reason
        enriched["screening_stage"] = ScreeningStage.DETAIL_APP_RULE.value

        run.result.skipped += 1
        if run.jobs_index is not None and run.result.jobs:
            run.result.jobs.pop(run.jobs_index)
            run.jobs_index = None
        # The extracted posting is withdrawn with its record: a card rejected here was
        # never collected, so leaving it behind would report a posting the run discarded.
        if run.postings_index is not None and run.result.postings:
            run.result.postings.pop(run.postings_index)
            run.postings_index = None

        saved = await self.store.upsert_job_record(enriched)
        title = effective_title(posting, card)
        company = effective_company(posting, card)
        await self._log(f"{log_prefix} '{title}' @ '{company}': {reason}，已标记为淘汰并停止采集")
        await self._emit(
            run,
            JobOutcome(
                fingerprint=card.fingerprint,
                title=title,
                company_name=company,
                status=JobRecordStatus.IGNORED.value,
                action=JobAction.SKIPPED,
                reason=reason,
                record=saved or {},
            ),
        )
        if run.result.outcome == "no_candidates":
            run.result.outcome = CardVerdictStage.FILTERED_BY_APP_RULE.value
            run.result.reason = reason
            run.result.job = {
                "title": title,
                "company_name": company,
                "salary_range": getattr(posting, "salary_range", ""),
            }

    async def _evaluate_and_act(self, run: _CardRun, posting: Any) -> None:
        """Run full-JD evaluation and persist whichever outcome the target action implies."""
        card = run.card
        assert card is not None
        config = run.config

        posting_title = effective_title(posting, card)
        posting_company = effective_company(posting, card)
        jd_text = posting.job_description or ""
        enriched = enriched_record(
            card,
            posting,
            keyword=config.keyword,
            source_task_id=config.source_task_id,
            card_record=run.card_record,
            verdict=run.verdict,
        )
        is_headhunter = enriched["is_headhunter"]

        # Business district blacklist, second look (issue #333). The card stage judged the
        # district facet and passed this posting; the detail page has since read the full
        # location line, station included, and a station entry in the same list cannot be
        # judged any earlier. Deterministic and one-strike like the card stage, so
        # Whitelist Relaxation is deliberately not consulted: an operator who refuses a
        # region does not get it back because the title said something they like. Runs
        # before the LLM is asked anything, so a refused station costs no tokens.
        location_line = str(getattr(posting, "location_line", "") or "")
        location_pass, location_violation = config.screening_policy.evaluate_location_blacklist(
            location_line or str(card.location or "")
        )
        if not location_pass:
            await self._reject_detail_posting(
                run,
                card,
                enriched,
                posting,
                reason=location_violation,
                audit_note=f"App端强制过滤违例: {location_violation}",
                log_prefix="🛑 [商圈黑名单过滤]",
            )
            return

        # Commute distance App-Enforced Filter (spec #209). The
        # distance widget only exists at the bottom of the detail page,
        # so this verdict can only be rendered here.
        # An over-ceiling distance is a hard constraint (#438, completing the hard/soft
        # split #437 drew for the non-native filters), so — unlike the channel preference
        # — it never consults Whitelist Relaxation: an operator who will not commute the
        # distance does not get the job back because the title said something they like.
        commute_dist = getattr(posting, "commute_distance_km", None)
        commute_pass, commute_violation = config.screening_policy.evaluate_commute_distance(
            commute_dist
        )
        if commute_dist is not None:
            await self._log(
                f"📍 [App端强制过滤] '{posting_title}' 距家庭住址 {commute_dist:.1f}km"
                + (
                    " (在通勤上限内)"
                    if commute_pass
                    else f" (超过上限 {config.screening_policy.max_commute_distance_km:.1f}km)"
                )
            )

        if not commute_pass:
            # commute_violation already carries 硬性约束不可豁免 (composed by the policy),
            # so the stored record, the emitted outcome and the real-time log all name it
            # once, in the same words the card stage used.
            await self._reject_detail_posting(
                run,
                card,
                enriched,
                posting,
                reason=commute_violation,
                audit_note=f"App端强制过滤违例: {commute_violation}",
                log_prefix="🛑 [App端强制过滤]",
            )
            return

        # Resolution happens before the screener, not after: a human copy means there is
        # nothing to draft, and "nothing to draft" means no token is spent drafting it.
        run.human_greeting, run.human_greeting_origin = self._resolve_human_greeting(run)

        filter_dict = (
            config.filter_config.to_dict()
            if config.filter_config and hasattr(config.filter_config, "to_dict")
            else None
        )
        if hasattr(posting, "search_filter") and not getattr(posting, "search_filter", None):
            posting.search_filter = filter_dict
        if hasattr(card, "search_filter") and not getattr(card, "search_filter", None):
            card.search_filter = filter_dict

        evaluation = self.screener.evaluate_job(
            card=card,
            jd_text=jd_text,
            profile=config.candidate_profile,
            policy=config.screening_policy,
            draft_greeting=config.target_action == TargetAction.AUTO_APPLY
            and not run.human_greeting,
            search_filter=filter_dict,
        )

        if "查看更多" in jd_text:
            logger.error(
                "Incomplete JD: '查看更多' still present in extracted JD for %s '%s'",
                "[猎头]" if is_headhunter else "[直招]",
                posting_title,
            )
            await self._log(
                f"❌ [Incomplete JD Error] '查看更多' was detected in extracted JD for "
                f"{'[猎头]' if is_headhunter else '[直招]'} '{posting_title}'"
            )

        if evaluation.stage is JobVerdictStage.FILTERED_BY_DEEP_SCREENER:
            await self._log(f"⏭️ [精筛淘汰] '{posting_title}': {evaluation.reason}")
            await self._finalize_verdict(run, enriched, JobRecordStatus.IGNORED, evaluation)
            return

        if evaluation.stage is JobVerdictStage.JD_UNAVAILABLE:
            # No evaluable JD means no verdict: keep the record retryable rather than
            # burying the posting, and never spend a greeting on it.
            await self._log(
                f"⚠️ [JD Unavailable] '{posting_title}' @ '{posting_company}': {evaluation.reason}"
            )
            await self._finalize_verdict(run, enriched, JobRecordStatus.JD_SAVED, evaluation)
            return

        if config.target_action == TargetAction.SAVE_JD:
            await self._save_jd(run, enriched, evaluation, is_headhunter)
            return

        await self._apply_greeting(run, enriched, evaluation, is_headhunter)

    async def _save_jd(
        self,
        run: _CardRun,
        enriched: dict[str, Any],
        evaluation: JobEvaluationResult,
        is_headhunter: bool,
    ) -> None:
        """Persist the extracted JD for the dashboard without spending greeting tokens."""
        tag = "[猎头]" if is_headhunter else "[直招]"
        described = enriched.get("job_description") or ""
        await self._log(
            f"✨ [Enriched Detail] Extracted full JD for {tag} '{enriched['title']}' "
            f"({len(described)} chars)"
        )
        await self._finalize_verdict(run, enriched, JobRecordStatus.JD_SAVED, evaluation)

    async def _apply_greeting(
        self,
        run: _CardRun,
        enriched: dict[str, Any],
        evaluation: JobEvaluationResult,
        is_headhunter: bool,
    ) -> None:
        """Draft and, when allowed, dispatch the tailored greeting for one job."""
        config = run.config
        title, company = enriched["title"], enriched["company_name"]
        # A human copy is the text; the screener's draft is the fallback. Neither one is
        # rewritten here — `ensure_greeting_prefix` belongs to the drafting path, and
        # applying it to approved words would send something the human never signed off on.
        greeting = run.human_greeting or evaluation.greeting_message
        if run.human_greeting:
            await self._log(
                f"📖 [复用人工招呼语] '{title}' @ '{company}': 来源 "
                f"{run.human_greeting_origin}，跳过匹配起草，原文逐字发送。"
            )
            await self._log(f'Greeting To Send (verbatim): "{greeting}"')
        else:
            await self._log(
                f"Evaluated '{title}' @ '{company}': Score {evaluation.match_score}/100 | "
                f"Match Reasons: [{'; '.join(evaluation.match_reasons) or '无'}]"
            )
            await self._log(f'Tailored Greeting Draft: "{greeting}"')

        # What this run persists as its verdict, whoever wrote the words.
        enriched["greeting_message"] = greeting
        enriched["greeting_source"] = (
            GREETING_SOURCE_HUMAN if run.human_greeting else GREETING_SOURCE_AGENT
        )

        # Only a task queued before issue #298 can still arrive here: the contract's own
        # payloads state one depth expression and dispatch on it. The branch stays because an
        # in-flight payload that asked for the old draft-only shape is still honoured.
        if config.depth_warning:
            await self._log(f"⚠️ [Legacy Depth] {config.depth_warning}")

        if not config.send_greeting:
            await self._log(
                f"⏸️ [NOT SENT] '{title}' @ '{company}' was drafted but this task's depth "
                f"does not dispatch it; the record stays re-sendable (status: matched)."
            )
            await self._finalize_verdict(run, enriched, JobRecordStatus.MATCHED, evaluation)
            return

        if not run.human_greeting and evaluation.match_score < config.min_score:
            await self._log(
                f"⏭️ [AUTO_SEND] Skipped: Match score {evaluation.match_score} < "
                f"threshold {config.min_score}"
            )
            await self._finalize_verdict(run, enriched, JobRecordStatus.JD_SAVED, evaluation)
            return

        # The score gate above is a feed-sweep rule. A human-approved copy is not score
        # gated (issue #300): there is no score, because nothing was drafted to be scored.
        # The quota, the cancellation probe and the company guard apply to it unchanged.
        if await self._quota_exhausted(run):
            applied_today = run.applied_today
            # Running out of quota is not a depth a task chose. The JD and the draft are
            # saved, the record stays in the re-sendable `matched` state, and the next run
            # owes it a real dispatch — which is what issue #299 makes happen instead of
            # letting `matched` read as "already done".
            await self._log(
                f"⚠️ [LIMIT REACHED] Daily greeting limit reached "
                f"({applied_today}/{config.daily_greeting_limit}). '{title}' @ '{company}' is "
                f"not sent this round and stays re-sendable (status: matched)."
            )
            await self._finalize_verdict(run, enriched, JobRecordStatus.MATCHED, evaluation)
            return

        if await self._is_cancelled():
            run.result.cancelled = True
            await self._log("🛑 [Task Cancelled] Task was cancelled by user before chat dispatch.")
            return

        dispatched = False
        if self.detail_page.open_chat(timeout_sec=5.0):
            self.chat_page.type_greeting_message(greeting, timeout_sec=5.0)
            dispatched = self.chat_page.click_send(timeout_sec=3.0)
            if dispatched:
                # The count read at the quota gate, plus the greeting just sent.
                applied_today = run.applied_today + 1
                await self._log(
                    f"✅ [AUTO_SEND] Dispatched greeting message to {title} @ {company} "
                    f"({applied_today}/{config.daily_greeting_limit} today)"
                )
            else:
                await self._log(
                    f"⚠️ [AUTO_SEND] Could not send greeting to {title} @ {company}: "
                    "send control unavailable. Not counted as communicated; the record "
                    "stays re-sendable (status: matched)."
                )
            self.chat_page.navigate_back()

        # Only a message that actually left the app counts as applied; otherwise the
        # employer would become a same-company exclusion anchor without being contacted.
        status = JobRecordStatus.APPLIED if dispatched else JobRecordStatus.MATCHED
        if dispatched:
            enriched["applied_at"] = datetime.now(UTC).isoformat()
            enriched["applied_source"] = APPLIED_SOURCE_AGENT
        await self._finalize_verdict(run, enriched, status, evaluation)
        if dispatched:
            company_name = (enriched.get("company_name") or "").strip()
            if is_direct_hire_company(company_name, enriched.get("is_headhunter")):
                self._excluded_companies.add(company_name)

    async def _quota_exhausted(self, run: _CardRun) -> bool:
        """Whether the daily greeting quota leaves no room for another dispatch.

        The count is read here once per card and kept on the run so the callers that only
        need it for a log line reuse this read rather than issuing their own.
        """
        try:
            run.applied_today = await self.store.count_today_applied_jobs()
        except TransportError as e:
            await self._log(f"⚠️ [持久化降级] 今日投递额度查询遇到持久化异常（{e}），无法确认配额")
            raise
        if run.applied_today >= run.config.daily_greeting_limit:
            run.result.quota_exhausted = True
            return True
        return False

    async def _finalize_verdict(
        self,
        run: _CardRun,
        payload: dict[str, Any],
        status: JobRecordStatus,
        evaluation: JobEvaluationResult,
    ) -> dict[str, Any]:
        """Persist one verdict and keep ``result.jobs`` tracking the scraped records."""
        saved = await self._persist_verdict(run, payload, status, evaluation)
        if saved:
            if run.jobs_index is not None and run.jobs_index < len(run.result.jobs):
                run.result.jobs[run.jobs_index] = saved
            else:
                run.result.jobs.append(saved)
                run.jobs_index = len(run.result.jobs) - 1
        return saved

    async def _persist_verdict(
        self,
        run: _CardRun,
        payload: dict[str, Any],
        status: JobRecordStatus,
        evaluation: JobEvaluationResult,
    ) -> dict[str, Any]:
        """Persist one terminal verdict and report it to the observer."""
        # An explicit greeting on the payload is the text this run decided to send. Only
        # the agent's own draft comes from the evaluation, so a reused human copy is stored
        # as what was actually sent rather than as an empty field (#300).
        greeting_message = payload.get("greeting_message") or evaluation.greeting_message
        greeting_source = payload.get("greeting_source") or (
            GREETING_SOURCE_AGENT if evaluation.greeting_message else ""
        )
        # A run that reused a human copy asked no model to score the posting, so
        # ``evaluation.match_score`` is the dataclass default — 0. And 0 is not "unknown",
        # it is "the worst score possible": writing it deletes the very number the operator
        # used to approve that copy in the panel. The stored score survives unless this run
        # actually produced a new one.
        # A run that reused a human copy asked no model to score the posting, so
        # ``evaluation.match_score`` is the dataclass default - 0. And 0 is not "unknown",
        # it is "the worst score possible": writing it deletes the number the operator used
        # to approve that copy in the panel. So the key is left out when this run computed
        # no score and the stored value stands; a never-scored record stays unscored rather
        # than claiming a zero it was never given.
        match_score = evaluation.match_score or payload.get("match_score")
        screened_reason = payload.get("screened_reason") or (
            evaluation.reason if status is JobRecordStatus.IGNORED else ""
        )
        # The only rejection that reaches here is the deep screener's — the card, expiry
        # and detail-App-filter paths persist their own stage. Without this the feed
        # labelled every full-JD rejection 初筛淘汰, since the card never saw the evidence.
        screening_stage = payload.get("screening_stage") or (
            ScreeningStage.DEEP_SCREENER.value
            if status is JobRecordStatus.IGNORED
            and getattr(evaluation, "stage", None) is JobVerdictStage.FILTERED_BY_DEEP_SCREENER
            else ""
        )
        payload = {
            **payload,
            "status": status.value,
            "greeting_message": greeting_message,
            "greeting_source": greeting_source,
            "screened_reason": screened_reason,
            "screening_stage": screening_stage,
            "jd_key_requirements": evaluation.jd_key_requirements
            or payload.get("jd_key_requirements", []),
        }
        if match_score is not None:
            payload["match_score"] = match_score
        try:
            saved = await self.store.upsert_job_record(dict(payload)) or {}
        except TransportError as e:
            await self._log(f"⚠️ [持久化降级] 岗位记录写入遇到持久化异常（{e}）")
            saved = dict(payload)
        if match_score is None:
            # Nothing was scored this run, so the run reports what the record says rather
            # than a 0 that would read as judged-and-rejected.
            match_score = saved.get("match_score")
        match_score = match_score or 0
        run.result.processed += 1
        if run.result.outcome == "no_candidates":
            # The primary outcome is the first job that earned a verdict: the run's
            # summary describes what it was really about, not whichever card came last.
            run.result.outcome = status.value
            run.result.job = {
                "title": payload.get("title", ""),
                "company_name": payload.get("company_name", ""),
                "salary_range": payload.get("salary_range", ""),
            }
            run.result.score = match_score
            run.result.greeting_message = greeting_message
            run.result.jd_key_requirements = evaluation.jd_key_requirements
        # The action follows from the verdict alone: a matched record is a greeting that has
        # not left the device yet, whether or not this run already dispatched an earlier one.
        if status is JobRecordStatus.MATCHED:
            await self._emit(
                run,
                JobOutcome(
                    fingerprint=payload.get("fingerprint", ""),
                    title=payload.get("title", ""),
                    company_name=payload.get("company_name", ""),
                    status=status.value,
                    action=JobAction.PENDING_SEND,
                    score=match_score,
                    greeting_message=greeting_message,
                    record=saved,
                ),
            )
        elif status is JobRecordStatus.APPLIED:
            run.result.applied = True
            run.result.applied_count += 1
            await self._emit(
                run,
                JobOutcome(
                    fingerprint=payload.get("fingerprint", ""),
                    title=payload.get("title", ""),
                    company_name=payload.get("company_name", ""),
                    status=status.value,
                    action=JobAction.APPLIED,
                    score=match_score,
                    greeting_message=greeting_message,
                    record=saved,
                ),
            )
        else:
            await self._emit(
                run,
                JobOutcome(
                    fingerprint=payload.get("fingerprint", ""),
                    title=payload.get("title", ""),
                    company_name=payload.get("company_name", ""),
                    status=status.value,
                    action=JobAction.SAVED
                    if status is JobRecordStatus.JD_SAVED
                    else JobAction.SKIPPED,
                    score=match_score,
                    greeting_message=greeting_message,
                    record=saved,
                ),
            )
        return saved

    # ------------------------------------------------------------------
    # On-screen evaluation (the empty-feed fallback)
    # ------------------------------------------------------------------
    async def _evaluate_current_posting(self, run: _CardRun) -> None:
        """Evaluate whichever posting is already on screen, without scanning a feed."""
        if await self._is_cancelled():
            run.result.cancelled = True
            return

        run.result.scanned = max(run.result.scanned, 1)

        chat_state = self.detail_page.get_chat_button_state()
        if chat_state in (ChatButtonState.COMMUNICATED, ChatButtonState.CLOSED):
            is_historical = chat_state == ChatButtonState.COMMUNICATED
            if is_historical:
                await self._log("⏭️ [既有沟通] 当前屏幕岗位平台已沟通，终止投递")
            else:
                await self._log("🛑 [岗位失效] 当前屏幕岗位已停止招聘/下线，终止投递")
            run.result.outcome = (
                JobRecordStatus.APPLIED.value if is_historical else JobRecordStatus.IGNORED.value
            )
            run.result.skipped += 1
            self.detail_page.navigate_back()
            return

        policy = run.config.screening_policy or ScreeningPolicy()
        target_is_headhunter = run.config.is_headhunter
        # No stored record stands behind this screen: a scan that read no card never opened a
        # detail page, so the posting's district is unknown and the commute list is matched
        # against nothing — the same fail-open an unread card location would get.
        probe_commute_distance = policy.should_probe_commute_distance(target_is_headhunter)
        if policy.is_commute_filter_active and not probe_commute_distance:
            reason = (
                HEADHUNTER_COMMUTE_PROBE_SKIP_REASON
                if target_is_headhunter
                else NOT_INSPECTED_DISTRICT_SKIP_REASON
            )
            await self._log(f"📍 [App端强制过滤] '当前岗位' {reason}")

        try:
            posting = self.detail_page.extract_job_posting(
                timeout_sec=5.0,
                probe_commute_distance=probe_commute_distance,
                is_headhunter=target_is_headhunter,
                commute_probe_upgrade=station_probe_upgrade(policy, target_is_headhunter),
            )
        except Exception as e:
            run.result.error_message = str(e)
            await self._log(f"Could not extract current job posting: {e}")
            return

        title = (posting.title or "").strip()
        if title in INVALID_JOB_TITLES:
            run.result.error_message = (
                f"Current screen does not show a valid job title: '{posting.title}'"
            )
            await self._log(
                f"❌ [Invalid Title] Current screen does not show a valid job title: "
                f"'{posting.title}'. Skipping application."
            )
            return

        # No digest: a digest derived from this very JD would let the deterministic card
        # stage string-match a blacklist token out of context and pre-empt the semantic
        # screen that exists precisely to judge those mentions correctly.
        card = JobCardBrief(
            title=posting.title,
            company_name=posting.company_name,
            recruiter_name=posting.recruiter_name or "",
            recruiter_title=posting.recruiter_title or "",
            is_headhunter=getattr(posting, "is_headhunter", False),
            salary_range=posting.salary_range,
            location=posting.location or "",
            tags=list(getattr(posting, "tags", None) or []),
            commute_distance_km=getattr(posting, "commute_distance_km", None),
            commute_distance_text=str(getattr(posting, "commute_distance_text", "") or ""),
        )
        run.card = card
        run.card_record = card_facets_record(
            card,
            keyword=run.config.keyword,
            source_task_id=run.config.source_task_id,
        )

        run.verdict = self.screener.evaluate_card(card, run.config.screening_policy)
        if not run.verdict.passed:
            run.result.skipped += 1
            if run.verdict.stage is CardVerdictStage.FILTERED_BY_APP_RULE:
                await self._log(f"🛑 [App端强制过滤] '{title}': {run.verdict.reason}")
            else:
                await self._log(f"⏭️ [初筛淘汰] '{title}': {run.verdict.reason}")
            await self.store.upsert_job_record(
                {
                    **run.card_record,
                    "status": JobRecordStatus.IGNORED.value,
                    "screened_reason": run.verdict.reason,
                    "screening_stage": card_rejection_stage(run.verdict).value,
                    "relaxed_by_whitelist": False,
                    "screening_audit": run.verdict.screening_audit,
                }
            )
            run.result.outcome = run.verdict.stage.value
            run.result.reason = run.verdict.reason
            if run.result.job is None:
                run.result.job = {
                    "title": title,
                    "company_name": posting.company_name,
                    "salary_range": posting.salary_range,
                }
            return
        if run.verdict.relaxed_by_whitelist:
            await self._log(
                f"🎗️ [白名单放宽] '{title}' 获豁免继续评估: {run.verdict.relaxation_reason}"
            )

        run.result.postings.append(posting)
        run.postings_index = len(run.result.postings) - 1
        await self._evaluate_and_act(run, posting)

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    async def _emit(self, run: _CardRun, outcome: JobOutcome) -> None:
        if run.on_job is not None:
            await run.on_job(outcome)

    async def _log(self, message: str) -> None:
        logger.info(message)
        if self._log_sink is not None:
            await self._log_sink(message)

    async def _is_cancelled(self) -> bool:
        if self._cancel_probe is None:
            return False
        return await self._cancel_probe()
