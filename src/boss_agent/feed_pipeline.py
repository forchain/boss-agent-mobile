"""
boss_agent.feed_pipeline
========================
Deep Mobile Job Feed Pipeline (ADR 0013, Spec #231).

One engine drives every mobile job-discovery run: two-anchor search entry, bounded
Back-only recovery, filter dialogs, viewport card iteration with the bottom-boundary
guard, call-to-action button inspection with instant historical backout, inline JD
expansion, card screening, full-JD evaluation and daily-quota degradation.

Worker handlers configure it and read its result; they never touch a coordinate, a
page object or a scroll. The only behavioural difference between saving a JD and
running batch outreach is ``FeedStreamConfig.target_action``.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .feed_records import (
    card_facets_record,
    card_record,
    effective_company,
    effective_title,
    enriched_record,
)
from .job_store import INVALID_JOB_TITLES, JobRecordStore
from .memory import StructuredCandidateProfile
from .models import (
    APPLIED_SOURCE_AGENT,
    APPLIED_SOURCE_PLATFORM_HISTORICAL,
    EXPIRED_POSTING_REASON,
    STATE_RANK,
    TARGET_ACTION_RANK,
    ChatButtonState,
    FilterConfig,
    JobRecordStatus,
    ScreeningPolicy,
    TargetAction,
    is_communication_expired,
    is_direct_hire_company,
)
from .pages import (
    ChatPage,
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobCardBrief,
    JobDetailPage,
    JobListPage,
    SearchPage,
    StartupDialogPage,
)
from .screening import (
    CandidateScreener,
    CardScreeningVerdict,
    CardVerdictStage,
    JobEvaluationResult,
    JobVerdictStage,
)
from .settings import resolve_communication_cooldown_days

logger = logging.getLogger(__name__)

MAX_SEARCH_ATTEMPTS = 2
OPEN_SEARCH_TIMEOUT_SEC = 5.0
SEARCH_SUBMIT_TIMEOUT_SEC = 10.0
CARDS_PER_VIEWPORT = 10
CONSECUTIVE_EMPTY_SCROLL_LIMIT = 3
DEFAULT_MAX_JOBS = 3

BOUNDARY_LOG = (
    "🛑 [Feed Boundary] Detected '暂无其他符合职位 / 为你推荐' end-of-feed marker. "
    "Terminating search pagination."
)


class JobAction(StrEnum):
    """What the pipeline did with a card once it reached a verdict."""

    SAVED = "saved"
    APPLIED = "applied"
    OFFLINE_DRAFT = "offline_draft"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class JobOutcome:
    """Per-card report handed to the caller's ``on_job`` observer."""

    fingerprint: str
    title: str
    company_name: str
    status: str
    action: JobAction
    reason: str = ""
    score: int = 0
    greeting_message: str = ""
    record: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedStreamResult:
    """Aggregate outcome of one feed streaming run."""

    outcome: str = "no_candidates"
    scanned: int = 0
    processed: int = 0
    skipped: int = 0
    jobs: list[dict[str, Any]] = field(default_factory=list)
    applied: bool = False
    applied_count: int = 0
    score: int = 0
    greeting_message: str = ""
    jd_key_requirements: list[str] = field(default_factory=list)
    job: dict[str, Any] | None = None
    cancelled: bool = False
    search_failed: bool = False
    boundary_reached: bool = False
    quota_exhausted: bool = False
    error_message: str | None = None


@dataclass
class FeedStreamConfig:
    """Declarative description of one feed run, parsed from a task payload."""

    target_action: TargetAction = TargetAction.SAVE_JD
    keyword: str | None = None
    max_jobs: int = DEFAULT_MAX_JOBS
    enable_search: bool = True
    # Filtering is switched off on the filter config itself, which owns the flag.
    filter_config: FilterConfig | None = None
    screening_policy: ScreeningPolicy = field(default_factory=ScreeningPolicy)
    cooldown_days: int = 0
    daily_greeting_limit: int = 20
    min_score: float = 70.0
    preview_only: bool = True
    auto_send: bool = False
    candidate_profile: StructuredCandidateProfile | None = None
    source_task_id: str | None = None
    # A targeted application acts on the posting already on screen instead of scanning.
    single_screen: bool = False
    direct_job_id: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "FeedStreamConfig":
        """Parse the worker task payload this pipeline and both handlers share."""
        from .settings import load_settings

        data = payload or {}
        target_action_val = data.get("target_action") or data.get("action")
        target_action = TargetAction.SAVE_JD
        if target_action_val:
            try:
                target_action = TargetAction(str(target_action_val).lower())
            except ValueError:
                target_action = TargetAction.SAVE_JD

        raw_filter = data.get("filter")
        filter_config = (
            FilterConfig(
                education=raw_filter.get("education"),
                salary=raw_filter.get("salary"),
                experience=raw_filter.get("experience"),
                activity=raw_filter.get("activity"),
                company_scales=raw_filter.get("company_scales", []),
                industries=raw_filter.get("industries", []),
                enable_filter=bool(data.get("enable_filter", True)),
            )
            if isinstance(raw_filter, dict)
            else None
        )

        raw_policy = data.get("screening_policy")
        daily_limit = int(
            data.get("daily_greeting_limit") or load_settings().get("daily_greeting_limit", 20)
        )

        return cls(
            target_action=target_action,
            keyword=data.get("keyword"),
            max_jobs=int(data.get("max_jobs", DEFAULT_MAX_JOBS)),
            enable_search=bool(data.get("enable_search", True)),
            filter_config=filter_config,
            screening_policy=(
                ScreeningPolicy.from_dict(raw_policy)
                if raw_policy
                else ScreeningPolicy.load_default()
            ),
            cooldown_days=resolve_communication_cooldown_days(data),
            daily_greeting_limit=daily_limit,
            min_score=float(data.get("min_score", 70)),
            preview_only=bool(data.get("preview_only", True)),
            auto_send=bool(data.get("auto_send", False)),
            source_task_id=data.get("source_task_id"),
            single_screen=bool(data.get("direct_job_id")),
            direct_job_id=data.get("direct_job_id"),
        )


@dataclass
class _CardRun:
    """Mutable state threaded through the per-card stages."""

    config: FeedStreamConfig
    result: FeedStreamResult
    on_job: Callable[[JobOutcome], Awaitable[None]] | None = None
    card: JobCardBrief | None = None
    card_record: dict[str, Any] = field(default_factory=dict)
    verdict: CardScreeningVerdict | None = None
    # Position of this card's record in ``result.jobs``, replaced in place by the
    # enriched record and dropped when the card turns out to be a skip.
    jobs_index: int | None = None
    # Greetings already dispatched today, as read by this card's quota check. The read is
    # reused by the log lines instead of paying a second round trip per card.
    applied_today: int = 0


def _element_y(elem: Any) -> float | None:
    """Vertical position of a UI element, or None when the driver does not report one."""
    if elem is None:
        return None
    location = getattr(elem, "location", None)
    if not isinstance(location, dict):
        return None
    try:
        return float(location.get("y", 0))
    except (TypeError, ValueError):
        return None


async def is_task_cancelled(broker: Any, task_id: str) -> bool:
    """Whether the user cancelled the task this run belongs to."""
    from .broker.models import TaskStatus

    task = await broker.get_task(task_id)
    return task is not None and task.status == TaskStatus.CANCELLED


class JobFeedPipeline:
    """Streams screened job postings out of a mobile search feed.

    Dependencies are injected so a run can be driven against synthetic page state:
    a Job Record Store for every persistence decision, a Candidate Screener for every
    screening decision, a log sink and a cancellation probe.
    """

    def __init__(
        self,
        driver: Any,
        store: JobRecordStore,
        screener: CandidateScreener | None = None,
        log: Callable[[str], Awaitable[None]] | None = None,
        is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.driver = driver
        self.store = store
        self.screener = screener or CandidateScreener()
        self._log_sink = log
        self._cancel_probe = is_cancelled

        self.startup_page = StartupDialogPage(driver) if driver else None
        self.list_page = JobListPage(driver) if driver else None
        self.search_page = SearchPage(driver) if driver else None
        self.detail_page = JobDetailPage(driver) if driver else None
        self.chat_page = ChatPage(driver) if driver else None

        # Companies contacted during this run: a newly discovered communication must
        # suppress the company's other postings immediately, not on the next pass.
        self._excluded_companies: set[str] = set()

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------
    @classmethod
    def for_task(
        cls,
        broker: Any,
        task_id: str,
        driver: Any,
        screener: CandidateScreener | None = None,
    ) -> "JobFeedPipeline":
        """Wire a pipeline to a worker task: its job ledger, log sink and cancel probe.

        Both worker handlers compose their run this way, so task plumbing lives in one
        place instead of being re-derived per handler. The broker's job ledger is
        resolved here, at composition time, rather than by a duck-typing fallback that
        let any broker-shaped object silently satisfy store-typed code.
        """
        return cls(
            driver=driver,
            store=broker.job_store,
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
        if not self.driver:
            result.error_message = "Driver session is unavailable"
            return result

        if self.startup_page and self.startup_page.is_dialog_present():
            self.startup_page.dismiss_dialog()

        if config.single_screen:
            await self._evaluate_current_posting(
                _CardRun(config=config, result=result, on_job=on_job)
            )
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
        self._excluded_companies = await self.store.get_applied_direct_companies(
            cooldown_days=config.cooldown_days
        )
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
                IndustryFilterDialogPage(self.driver).apply_industry_filters(
                    filter_cfg.industries, timeout_sec=5.0
                )
            except Exception as ex:
                await self._log(f"Notice applying industry filter: {ex}")

        filter_page = FilterDialogPage(self.driver)
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
            for card in visible_cards:
                if len(scanned_fingerprints) >= config.max_jobs:
                    break
                card_y = _element_y(card.element)
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
                        _CardRun(config=config, result=result, on_job=on_job, card=card)
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
        # The card itself is already a result: a detail-page failure must not lose it.
        run.result.jobs.append(persisted)
        run.jobs_index = len(run.result.jobs) - 1
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

        required_rank = TARGET_ACTION_RANK.get(config.target_action, 1)
        cur_rank = STATE_RANK.get(existing_status, 1)
        has_full_jd = bool((existing_record.get("job_description") or "").strip())
        is_already_progressed = cur_rank > TARGET_ACTION_RANK.get(TargetAction.SAVE_JD, 1)
        if (
            not is_released
            and cur_rank >= required_rank
            and (
                is_already_progressed or config.target_action != TargetAction.SAVE_JD or has_full_jd
            )
        ):
            result.skipped += 1
            await self._log(
                f"⏭️ [State Machine] '{card.title}' already at '{existing_status}' "
                f"(>= target '{config.target_action.value}'). Skipping detail opening."
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

        if not await self._open_detail(card):
            return
        try:
            if await self._back_out_if_contacted(run, card.fingerprint):
                return
            posting = self._extract_posting(card)
            if posting is None:
                return
            await self._evaluate_and_act(run, posting)
        finally:
            self.detail_page.navigate_back()

    async def _open_detail(self, card: JobCardBrief) -> bool:
        """Tap the card (or fall back to the first list item) to reach its detail page."""
        if card.element is not None and hasattr(card.element, "click"):
            try:
                card.element.click()
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

    def _extract_posting(self, card: JobCardBrief) -> Any | None:
        """Read the detail page, returning None when no usable title is on screen.

        A missing detail-page title is not fatal: the card's own title still identifies
        the posting, and dropping the enrichment would lose a job we already hold.
        """
        try:
            posting = self.detail_page.extract_job_posting(
                timeout_sec=4.0,
                fallback_company=card.company_name,
                fallback_title=card.title,
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

        evaluation = self.screener.evaluate_job(
            card=card,
            jd_text=jd_text,
            profile=config.candidate_profile,
            policy=config.screening_policy,
            draft_greeting=config.target_action == TargetAction.AUTO_APPLY,
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
        greeting = evaluation.greeting_message
        await self._log(
            f"Evaluated '{title}' @ '{company}': Score {evaluation.match_score}/100 | "
            f"Match Reasons: [{'; '.join(evaluation.match_reasons) or '无'}]"
        )
        await self._log(f'Tailored Greeting Draft: "{greeting}"')

        if not (config.auto_send and not config.preview_only):
            await self._log(
                f"💾 [OFFLINE DRAFT] Saved JD and drafted greeting for '{title}' (status: matched)."
            )
            await self._finalize_verdict(run, enriched, JobRecordStatus.MATCHED, evaluation)
            return

        if evaluation.match_score < config.min_score:
            await self._log(
                f"⏭️ [AUTO_SEND] Skipped: Match score {evaluation.match_score} < "
                f"threshold {config.min_score}"
            )
            await self._finalize_verdict(run, enriched, JobRecordStatus.JD_SAVED, evaluation)
            return

        if await self._quota_exhausted(run):
            applied_today = run.applied_today
            await self._log(
                f"⚠️ [LIMIT REACHED] Daily greeting limit reached "
                f"({applied_today}/{config.daily_greeting_limit}). Degrading to offline draft "
                f"for '{title}' @ '{company}' (status: matched)."
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
                    "send control unavailable. Kept as a matched draft for manual sending."
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
        run.applied_today = await self.store.count_today_applied_jobs()
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
        payload = {
            **payload,
            "status": status.value,
            "match_score": evaluation.match_score,
            "greeting_message": evaluation.greeting_message,
            "jd_key_requirements": evaluation.jd_key_requirements
            or payload.get("jd_key_requirements", []),
        }
        saved = await self.store.upsert_job_record(dict(payload)) or {}
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
            run.result.score = evaluation.match_score
            run.result.greeting_message = evaluation.greeting_message
            run.result.jd_key_requirements = evaluation.jd_key_requirements
        # The action follows from the verdict alone: a matched record is a draft awaiting a
        # manual send, whether or not this run already dispatched an earlier greeting.
        if status is JobRecordStatus.MATCHED:
            await self._emit(
                run,
                JobOutcome(
                    fingerprint=payload.get("fingerprint", ""),
                    title=payload.get("title", ""),
                    company_name=payload.get("company_name", ""),
                    status=status.value,
                    action=JobAction.OFFLINE_DRAFT,
                    score=evaluation.match_score,
                    greeting_message=evaluation.greeting_message,
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
                    score=evaluation.match_score,
                    greeting_message=evaluation.greeting_message,
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
                    score=evaluation.match_score,
                    greeting_message=evaluation.greeting_message,
                    record=saved,
                ),
            )
        return saved

    # ------------------------------------------------------------------
    # Single-screen evaluation (direct targets and empty feeds)
    # ------------------------------------------------------------------
    async def _evaluate_current_posting(self, run: _CardRun) -> None:
        """Evaluate whichever posting is already on screen, without scanning a feed."""
        if await self._is_cancelled():
            run.result.cancelled = True
            return

        run.result.scanned = max(run.result.scanned, 1)
        target_record = await self._target_record(run.config)

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
            if target_record:
                terminal = dict(target_record)
                if is_historical:
                    terminal["status"] = JobRecordStatus.APPLIED.value
                    terminal["applied_source"] = APPLIED_SOURCE_PLATFORM_HISTORICAL
                else:
                    terminal["status"] = JobRecordStatus.IGNORED.value
                    terminal["screened_reason"] = EXPIRED_POSTING_REASON
                await self.store.upsert_job_record(terminal)
            self.detail_page.navigate_back()
            return

        try:
            posting = self.detail_page.extract_job_posting(timeout_sec=5.0)
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
                    "relaxed_by_whitelist": False,
                    "screening_audit": run.verdict.screening_audit,
                }
            )
            run.result.outcome = run.verdict.stage.value
            return
        if run.verdict.relaxed_by_whitelist:
            await self._log(
                f"🎗️ [白名单放宽] '{title}' 获豁免继续评估: {run.verdict.relaxation_reason}"
            )

        await self._evaluate_and_act(run, posting)

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    async def _target_record(self, config: FeedStreamConfig) -> dict[str, Any] | None:
        """The record a targeted application is about, when the task names one."""
        if not config.direct_job_id:
            return None
        return await self.store.get_job_record(config.direct_job_id)

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
