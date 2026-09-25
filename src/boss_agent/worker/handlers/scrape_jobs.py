"""
src/boss_agent/worker/handlers/scrape_jobs.py
=============================================
Handler for SCRAPE_JOBS task: searches, filters, and extracts structured job postings.
"""

import logging
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.models import (
    APPLIED_SOURCE_PLATFORM_HISTORICAL,
    EXPIRED_POSTING_REASON,
    HEADHUNTER_COMMUTE_PROBE_SKIP_REASON,
    INVALID_COMPANY_NAMES,
    STATE_RANK,
    TARGET_ACTION_RANK,
    ChatButtonState,
    FilterConfig,
    JobPosting,
    JobRecordStatus,
    ScreeningPolicy,
    TargetAction,
    commute_columns,
    is_communication_expired,
    is_direct_hire_company,
    is_invalid_company_name,
)
from boss_agent.pages import (
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobCardBrief,
    JobDetailPage,
    JobListPage,
    SearchPage,
    StartupDialogPage,
)
from boss_agent.settings import resolve_communication_cooldown_days
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult
from boss_agent.worker.handlers.search_entry import run_search_entry

logger = logging.getLogger(__name__)


def _app_filter_rejection_record(
    card: JobCardBrief,
    *,
    violation: str,
    audit: str,
    keyword: str | None,
    task_id: str,
    tags: list[str],
    digest: str,
    posting: JobPosting | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Build the ``ignored`` job record for a card rejected before detail inspection.

    Serves both the keyword-stage gatekeeper (``violation`` is its rejection reason,
    ``audit`` empty) and the App-Enforced Filter rejections, so one shape covers every
    pre-detail rejection.

    ``relaxed_by_whitelist`` is always False here: the field records whether the job
    was *admitted* by relaxation, and this record was rejected. Any relaxation granted
    on a different dimension survives in ``screening_audit`` as part of the trail.

    ``posting`` is supplied only by the detail-stage check, where the card facets have
    already been persisted and the commute distance is known.
    """
    record: dict[str, Any] = {
        "fingerprint": card.fingerprint,
        "title": title or card.title,
        "company_name": card.company_name,
        "recruiter_name": card.recruiter_name,
        "recruiter_title": getattr(card, "recruiter_title", "") or "",
        "is_headhunter": getattr(card, "is_headhunter", False),
        "company_scale": getattr(card, "company_scale", "") or "",
        "industry": getattr(card, "industry", "") or "",
        "tags": tags,
        "salary_range": getattr(card, "salary_range", "") or "",
        "location": getattr(card, "location", "") or "",
        "digest": digest,
        "job_description": (posting.job_description if posting else "") or "",
        "jd_key_requirements": tags,
        "status": JobRecordStatus.IGNORED.value,
        "screened_reason": violation,
        "relaxed_by_whitelist": False,
        "screening_audit": audit,
        "search_keywords": [keyword] if keyword else [],
        "source_task_id": task_id,
    }
    record.update(commute_columns(posting))
    return record


class ScrapeJobsHandler(BaseTaskHandler):
    """Executes search, filtering, and job posting extraction."""

    @property
    def task_type(self) -> TaskType:
        return TaskType.SCRAPE_JOBS

    async def handle(
        self,
        task: AutomationTask,
        broker: BaseTaskBroker,
        context: WorkerContext,
    ) -> HandlerResult:
        driver = context.driver
        if not driver:
            await broker.append_log(task.id, "Error: No driver session initialized")
            return HandlerResult(success=False, error_message="Driver session is unavailable")

        payload = task.payload or {}
        keyword = payload.get("keyword")
        max_jobs = int(payload.get("max_jobs", 3))

        target_action_val = payload.get("target_action") or payload.get("action")
        if target_action_val:
            try:
                target_action = TargetAction(str(target_action_val).lower())
            except ValueError:
                target_action = TargetAction.SAVE_JD
        else:
            target_action = TargetAction.SAVE_JD
        required_rank = TARGET_ACTION_RANK.get(target_action, 1)

        search_name = payload.get("search_name") or payload.get("saved_search_name") or ""
        strategy_desc = f"strategy='{search_name}', " if search_name else ""
        await broker.append_log(
            task.id,
            f"Starting SCRAPE_JOBS ({strategy_desc}keyword='{keyword}', target_action='{target_action.value}', max_jobs={max_jobs})",
        )

        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()

        list_page = JobListPage(driver)

        enable_search = bool(payload.get("enable_search", True))
        if enable_search and keyword:
            if await run_search_entry(broker, task.id, list_page, SearchPage(driver), keyword):
                await broker.append_log(task.id, f"Executed search for keyword '{keyword}'")
            else:
                return HandlerResult(
                    success=False,
                    output={"error": f"Failed to execute search for keyword '{keyword}'"},
                )
        elif not enable_search:
            list_page.navigate_to_home()
            await broker.append_log(
                task.id,
                "enable_search is False; browsing home recommendations without search keyword",
            )

        enable_filter = bool(payload.get("enable_filter", True))
        filter_raw = payload.get("filter")
        filter_cfg: FilterConfig | None = None
        if enable_filter and isinstance(filter_raw, dict):
            filter_cfg = FilterConfig(
                education=filter_raw.get("education"),
                salary=filter_raw.get("salary"),
                experience=filter_raw.get("experience"),
                activity=filter_raw.get("activity"),
                company_scales=filter_raw.get("company_scales", []),
                industries=filter_raw.get("industries", []),
                enable_filter=True,
            )

        if filter_cfg and filter_cfg.has_industry_filters:
            industry_page = IndustryFilterDialogPage(driver)
            await broker.append_log(task.id, f"Applying industry filter: {filter_cfg.industries}")
            try:
                industry_page.apply_industry_filters(filter_cfg.industries, timeout_sec=5.0)
            except Exception as ex:
                await broker.append_log(task.id, f"Notice applying industry filter: {ex}")

        filter_page = FilterDialogPage(driver)
        if filter_cfg and filter_cfg.has_filters:
            await broker.append_log(
                task.id,
                f"Applying general filters: education={filter_cfg.education}, salary={filter_cfg.salary}, experience={filter_cfg.experience}",
            )
            try:
                filter_page.apply_filters(filter_cfg, timeout_sec=5.0)
            except Exception as ex:
                await broker.append_log(task.id, f"Notice applying general filters: {ex}")
        else:
            await broker.append_log(
                task.id,
                "No active general filters configured; actively clearing filter dialog conditions",
            )
            try:
                filter_page.clear_filters(timeout_sec=5.0)
            except Exception as ex:
                await broker.append_log(task.id, f"Notice clearing general filters: {ex}")

        scraped_jobs: list[dict[str, Any]] = []
        skipped_count = 0
        scanned_fingerprints: set[str] = set()
        consecutive_empty_scrolls = 0
        detail_page = JobDetailPage(driver)
        is_cancelled = False

        policy_raw = payload.get("screening_policy")
        policy = (
            ScreeningPolicy.from_dict(policy_raw) if policy_raw else ScreeningPolicy.load_default()
        )
        # Commute distance can only be read off the detail page bottom, so the probe is
        # armed here and the verdict rendered once the posting has been extracted. Which
        # cards are worth probing is decided per card, once its channel is known.
        commute_filter_active = policy.is_commute_filter_active
        if commute_filter_active:
            await broker.append_log(
                task.id,
                f"📍 [App端强制过滤] Active ceiling {policy.max_commute_distance_km:.1f}km; "
                f"probing detail page bottom for the distance widget (direct-hire postings only).",
            )

        cooldown_days = resolve_communication_cooldown_days(payload)
        applied_direct_companies = await broker.get_applied_direct_companies(
            cooldown_days=cooldown_days
        )
        if applied_direct_companies:
            await broker.append_log(
                task.id,
                f"🏢 [避嫌池] 已加载 {len(applied_direct_companies)} 家已沟通直招企业（冷却期 {cooldown_days or '永久'}）",
            )

        while len(scanned_fingerprints) < max_jobs:
            # 0. Check if task was cancelled by user
            cur_task = await broker.get_task(task.id)
            if cur_task and cur_task.status == TaskStatus.CANCELLED:
                await broker.append_log(
                    task.id,
                    "🛑 [Task Cancelled] Task was cancelled by user. Terminating scrape pagination.",
                )
                is_cancelled = True
                break

            # 1. Boundary check: Check if feed bottom banner is reached
            bottom_elem = list_page.get_feed_bottom_boundary()
            boundary_y: float | None = None
            if bottom_elem is not None:
                try:
                    loc = getattr(bottom_elem, "location", None)
                    if isinstance(loc, dict):
                        boundary_y = float(loc.get("y", 0))
                except Exception:
                    boundary_y = None

            # 2. Extract visible cards in current viewport
            visible_cards = list_page.extract_visible_job_cards(max_cards=10)
            if not visible_cards:
                if bottom_elem is not None:
                    await broker.append_log(
                        task.id,
                        "🛑 [Feed Boundary] Detected '暂无其他符合职位 / 为你推荐' end-of-feed marker. Terminating search pagination.",
                    )
                break

            new_cards_in_view = 0
            for card in visible_cards:
                if len(scanned_fingerprints) >= max_jobs:
                    break

                # Exclude recommended cards that appear BELOW the feed bottom boundary marker
                if boundary_y is not None and card.element is not None:
                    try:
                        card_loc = getattr(card.element, "location", None)
                        if isinstance(card_loc, dict):
                            card_y = float(card_loc.get("y", 0))
                            if card_y >= boundary_y:
                                # Card is below boundary marker: it is a recommended job, not search result
                                continue
                    except Exception:
                        pass

                if card.fingerprint in scanned_fingerprints:
                    continue

                scanned_fingerprints.add(card.fingerprint)
                new_cards_in_view += 1

                # Skip incomplete or partially visible cards without genuine company name
                comp_name = (card.company_name or "").strip()
                if not comp_name or comp_name == "未知公司":
                    skipped_count += 1
                    await broker.append_log(
                        task.id,
                        f"⏭️ [Incomplete Card] Skipping partially visible card without company name: '{card.title}'",
                    )
                    continue

                # 3. Enterprise-level direct-hire exclusion (直招同企避嫌): a shared in-house HR
                # candidate pool means one contact suppresses the company's other postings.
                if (
                    is_direct_hire_company(comp_name, getattr(card, "is_headhunter", False))
                    and comp_name in applied_direct_companies
                ):
                    skipped_count += 1
                    await broker.append_log(
                        task.id,
                        f"⏭️ [同企已沟通避嫌] '{card.title}' @ '{comp_name}' 直招企业已有沟通，跳过该企业其他岗位",
                    )
                    continue

                # 4. State Machine Check against existing database record
                existing_record = await broker.get_job_record_by_fingerprint(card.fingerprint)
                if existing_record:
                    existing_status = existing_record.get("status", "unmatched")
                    if existing_status == JobRecordStatus.IGNORED:
                        skipped_count += 1
                        await broker.append_log(
                            task.id,
                            f"⏭️ [Ignored Job] Skipping previously rejected job: '{card.title}' @ '{card.company_name}'",
                        )
                        continue

                    is_released = False
                    if existing_status == JobRecordStatus.APPLIED:
                        is_released = is_communication_expired(existing_record, cooldown_days)
                        if not is_released:
                            skipped_count += 1
                            await broker.append_log(
                                task.id,
                                f"⏭️ [已沟通岗位] '{card.title}' @ '{card.company_name}' 冷却期内已沟通，跳过详情页",
                            )
                            await broker.upsert_job_record(
                                {
                                    "fingerprint": card.fingerprint,
                                    "company_name": card.company_name,
                                    "title": card.title,
                                    "recruiter_name": card.recruiter_name,
                                    "search_keywords": [keyword] if keyword else [],
                                }
                            )
                            continue
                        # Transition back to jd_saved so fresh evaluation can persist a new state
                        # while the previously extracted JD is preserved.
                        await broker.clear_job_communication(existing_record["id"])
                        await broker.append_log(
                            task.id,
                            f"♻️ [冷却放宽] '{card.title}' @ '{card.company_name}' 距上次沟通已超过 {cooldown_days} 天，已释放回待评估流",
                        )

                    cur_rank = STATE_RANK.get(existing_status, 1)
                    has_full_jd = bool((existing_record.get("job_description") or "").strip())
                    is_already_progressed = cur_rank > TARGET_ACTION_RANK.get(
                        TargetAction.SAVE_JD, 1
                    )
                    if not is_released and cur_rank >= required_rank and (
                        is_already_progressed
                        or target_action != TargetAction.SAVE_JD
                        or has_full_jd
                    ):
                        skipped_count += 1
                        await broker.append_log(
                            task.id,
                            f"⏭️ [State Machine] '{card.title}' already at '{existing_status}' (>= target '{target_action.value}'). Skipping detail opening.",
                        )
                        await broker.upsert_job_record(
                            {
                                "fingerprint": card.fingerprint,
                                "company_name": card.company_name,
                                "title": card.title,
                                "recruiter_name": card.recruiter_name,
                                "search_keywords": [keyword] if keyword else [],
                            }
                        )
                        continue

                # 5. Preliminary screening (zero-token gatekeeper)
                digest_text = getattr(card, "digest", "") or getattr(card, "snippet", "") or ""
                card_tags = getattr(card, "tags", []) or []

                passed, reason = policy.matches_card_keywords(
                    title=card.title,
                    company_name=card.company_name,
                    tags=card_tags,
                    digest=digest_text,
                )
                if not passed:
                    skipped_count += 1
                    ignored_record = _app_filter_rejection_record(
                        card,
                        violation=reason,
                        audit="",
                        keyword=keyword,
                        task_id=task.id,
                        tags=card_tags,
                        digest=digest_text,
                    )
                    await broker.upsert_job_record(ignored_record)
                    await broker.append_log(
                        task.id,
                        f"⏭️ [初筛淘汰] '{card.title}' @ '{card.company_name}': {reason}",
                    )
                    continue

                # 5.5 App-Enforced Filters (platform-inexpressible constraints, e.g. recruitment
                # channel) with Whitelist Relaxation rescue before detail navigation.
                app_pass, app_violation = policy.evaluate_app_enforced_filters(
                    is_headhunter=getattr(card, "is_headhunter", False)
                )
                is_relaxed = False
                screening_audit = ""
                if not app_pass:
                    is_relaxed, relax_token = policy.evaluate_whitelist_relaxation(
                        title=card.title,
                        company_name=card.company_name,
                        tags=card_tags,
                        digest=digest_text,
                    )
                    screening_audit = f"App端强制过滤违例: {app_violation}"
                    if not is_relaxed:
                        skipped_count += 1
                        await broker.upsert_job_record(
                            _app_filter_rejection_record(
                                card,
                                violation=app_violation,
                                audit=screening_audit,
                                keyword=keyword,
                                task_id=task.id,
                                tags=card_tags,
                                digest=digest_text,
                            )
                        )
                        await broker.append_log(
                            task.id,
                            f"🛑 [App端强制过滤] '{card.title}' @ '{card.company_name}': {app_violation}",
                        )
                        continue
                    screening_audit += f"；【白名单放宽】命中关键词 '{relax_token}'，予以豁免"
                    await broker.append_log(
                        task.id,
                        f"🎗️ [白名单放宽] '{card.title}' 命中 '{relax_token}' 豁免渠道限制，继续采集",
                    )

                # 6. Execute Action based on target_action
                rec_type = "[猎头]" if getattr(card, "is_headhunter", False) else "[直招]"
                card_record = {
                    "fingerprint": card.fingerprint,
                    "title": card.title,
                    "company_name": card.company_name,
                    "recruiter_name": card.recruiter_name,
                    "recruiter_title": getattr(card, "recruiter_title", "") or "",
                    "is_headhunter": getattr(card, "is_headhunter", False),
                    "company_scale": getattr(card, "company_scale", "") or "",
                    "industry": getattr(card, "industry", "") or "",
                    "tags": card_tags,
                    "salary_range": getattr(card, "salary_range", "") or "",
                    "location": getattr(card, "location", "") or "",
                    "digest": digest_text,
                    "job_description": existing_record.get("job_description", "")
                    if existing_record
                    else "",
                    "jd_key_requirements": card_tags,
                    "status": JobRecordStatus.JD_SAVED.value
                    if (existing_record and (existing_record.get("job_description") or "").strip())
                    else JobRecordStatus.UNMATCHED.value,
                    "relaxed_by_whitelist": is_relaxed,
                    "screening_audit": screening_audit,
                    "search_keywords": [keyword] if keyword else [],
                    "source_task_id": task.id,
                }

                # Ingest card first, then inspect detail to enrich full JD
                persisted = await broker.upsert_job_record(card_record)
                scraped_jobs.append(persisted)

                await broker.append_log(
                    task.id,
                    f"🔍 [Detail Inspection] Inspecting {rec_type} '{card.title}' @ '{card.company_name}'",
                )
                clicked = False
                if card.element and hasattr(card.element, "click"):
                    try:
                        card.element.click()
                        clicked = True
                    except Exception:
                        pass
                if not clicked:
                    clicked = list_page.select_first_job(timeout_sec=2.0)

                if clicked:
                    # Zero-cost backout: consult the detail page call-to-action button before
                    # spending any time on description expansion or JD extraction.
                    chat_state = detail_page.get_chat_button_state()
                    if chat_state in (ChatButtonState.COMMUNICATED, ChatButtonState.CLOSED):
                        # Drop the optimistic card-level record appended above; this card is a
                        # skip, not a scraped JD.
                        if scraped_jobs and scraped_jobs[-1] is persisted:
                            scraped_jobs.pop()
                        skipped_count += 1
                        if chat_state == ChatButtonState.COMMUNICATED:
                            terminal_record = dict(card_record)
                            terminal_record["status"] = JobRecordStatus.APPLIED.value
                            terminal_record["applied_source"] = APPLIED_SOURCE_PLATFORM_HISTORICAL
                            await broker.upsert_job_record(terminal_record)
                            if is_direct_hire_company(
                                comp_name, terminal_record.get("is_headhunter")
                            ):
                                applied_direct_companies.add(comp_name)
                            await broker.append_log(
                                task.id,
                                f"⏭️ [既有沟通] '{card.title}' @ '{card.company_name}' 平台已沟通，免提 JD 直接退出",
                            )
                        else:
                            terminal_record = dict(card_record)
                            terminal_record["status"] = JobRecordStatus.IGNORED.value
                            terminal_record["screened_reason"] = EXPIRED_POSTING_REASON
                            await broker.upsert_job_record(terminal_record)
                            await broker.append_log(
                                task.id,
                                f"🛑 [岗位失效] '{card.title}' @ '{card.company_name}' 已停止招聘/下线，跳过",
                            )
                        detail_page.navigate_back()
                        continue

                    # Headhunter postings conceal the hiring enterprise, so the platform
                    # renders no distance widget on their detail page: probing would spend
                    # the swipe budget on a widget that cannot exist (ticket #255).
                    card_is_headhunter = getattr(card, "is_headhunter", False)
                    card_probe_commute_distance = policy.should_probe_commute_distance(
                        card_is_headhunter
                    )
                    if commute_filter_active and not card_probe_commute_distance:
                        await broker.append_log(
                            task.id,
                            f"📍 [App端强制过滤] '{card.title}' @ '{card.company_name}' "
                            f"{HEADHUNTER_COMMUTE_PROBE_SKIP_REASON}",
                        )

                    try:
                        job_posting = detail_page.extract_job_posting(
                            timeout_sec=4.0,
                            fallback_company=card.company_name,
                            fallback_title=card.title,
                            probe_commute_distance=card_probe_commute_distance,
                            is_headhunter=card_is_headhunter,
                        )
                        post_title = (job_posting.title or "").strip()
                        card_title = (card.title or "").strip()
                        effective_title = (
                            post_title
                            if (
                                post_title
                                and post_title
                                not in ("未注明职位", "未注明岗位", "未知职位", "未知岗位")
                            )
                            else card_title
                        )
                        if not effective_title or effective_title in (
                            "未注明职位",
                            "未注明岗位",
                            "未知职位",
                            "未知岗位",
                        ):
                            logger.warning(
                                "Skipping job detail enrichment due to missing/invalid title: '%s' @ '%s'",
                                effective_title,
                                card.company_name,
                            )
                            continue

                        # 5.5 Commute distance App-Enforced Filter (spec #209). The
                        # distance widget only exists at the bottom of the detail page,
                        # so this verdict can only be rendered here. The channel
                        # dimension is deliberately excluded: it was already adjudicated
                        # (and audited) from the card before navigating.
                        commute_pass, commute_violation = policy.evaluate_commute_distance(
                            job_posting.commute_distance_km
                        )
                        if job_posting.commute_distance_km is not None:
                            await broker.append_log(
                                task.id,
                                f"📍 [App端强制过滤] '{effective_title}' 距家庭住址 "
                                f"{job_posting.commute_distance_km:.1f}km"
                                + (
                                    " (在通勤上限内)"
                                    if commute_pass
                                    else f" (超过上限 {policy.max_commute_distance_km:.1f}km)"
                                ),
                            )

                        if not commute_pass:
                            commute_relaxed, commute_token = policy.evaluate_whitelist_relaxation(
                                title=effective_title,
                                company_name=card.company_name,
                                tags=card_tags,
                                digest=digest_text,
                            )
                            audit_parts = [screening_audit, f"App端强制过滤违例: {commute_violation}"]
                            screening_audit = "；".join(p for p in audit_parts if p)

                            if not commute_relaxed:
                                # Guarded like the chat-state rollback above: only drop the
                                # optimistic record we appended for this card, so a shifted
                                # append order cannot miscount an earlier job.
                                if scraped_jobs and scraped_jobs[-1] is persisted:
                                    scraped_jobs.pop()
                                skipped_count += 1
                                await broker.upsert_job_record(
                                    _app_filter_rejection_record(
                                        card,
                                        violation=commute_violation,
                                        audit=screening_audit,
                                        keyword=keyword,
                                        task_id=task.id,
                                        tags=card_tags,
                                        digest=digest_text,
                                        posting=job_posting,
                                        title=effective_title,
                                    )
                                )
                                await broker.append_log(
                                    task.id,
                                    f"🛑 [App端强制过滤] '{effective_title}' @ '{card.company_name}': "
                                    f"{commute_violation}，已标记为淘汰并停止采集",
                                )
                                continue

                            is_relaxed = True
                            screening_audit += (
                                f"；【白名单放宽】命中关键词 '{commute_token}'，予以豁免"
                            )
                            await broker.append_log(
                                task.id,
                                f"🎗️ [白名单放宽] '{effective_title}' 命中 '{commute_token}' "
                                f"豁免通勤距离限制，继续采集",
                            )

                        post_company = (job_posting.company_name or "").strip()
                        card_company = (card.company_name or "").strip()
                        effective_company = (
                            post_company
                            if (
                                post_company
                                and post_company not in INVALID_COMPANY_NAMES
                                and not is_invalid_company_name(post_company)
                            )
                            else card_company
                        )

                        enriched_data = {
                            "fingerprint": card.fingerprint,
                            "title": effective_title,
                            "company_name": effective_company,
                            "recruiter_name": card.recruiter_name
                            or job_posting.recruiter_name
                            or "招聘者",
                            "recruiter_title": card.recruiter_title
                            or getattr(job_posting, "recruiter_title", "")
                            or "",
                            "is_headhunter": card.is_headhunter
                            or getattr(job_posting, "is_headhunter", False),
                            "company_scale": card.company_scale
                            or getattr(job_posting, "company_scale", "")
                            or "",
                            "industry": card.industry or getattr(job_posting, "industry", "") or "",
                            "tags": card_tags or getattr(job_posting, "tags", []) or [],
                            "salary_range": job_posting.salary_range
                            or card_record.get("salary_range", ""),
                            "location": job_posting.location or card_record.get("location", ""),
                            "digest": digest_text,
                            "job_description": job_posting.job_description
                            or card_record.get("job_description", ""),
                            "status": JobRecordStatus.JD_SAVED.value,
                            "relaxed_by_whitelist": is_relaxed,
                            "screening_audit": screening_audit,
                            **commute_columns(job_posting),
                            "search_keywords": [keyword] if keyword else [],
                            "source_task_id": task.id,
                        }
                        persisted = await broker.upsert_job_record(enriched_data)
                        if not persisted:
                            logger.error(
                                "Failed to persist enriched job record: title='%s', company='%s'",
                                effective_title,
                                effective_company,
                            )
                            await broker.append_log(
                                task.id,
                                f"❌ [Enrich Error] Failed to persist enriched job record for '{effective_title}' @ '{effective_company}'",
                            )
                        else:
                            scraped_jobs[-1] = persisted
                            hh_tag = "[猎头]" if enriched_data["is_headhunter"] else "[直招]"
                            persisted_desc = persisted.get("job_description") or ""
                            persisted_title = persisted.get("title", effective_title)
                            if "查看更多" in persisted_desc:
                                logger.error(
                                    "Incomplete JD: '查看更多' still present in extracted JD for %s '%s'",
                                    hh_tag,
                                    persisted_title,
                                )
                                await broker.append_log(
                                    task.id,
                                    f"❌ [Incomplete JD Error] '查看更多' was detected in extracted JD for {hh_tag} '{persisted_title}'",
                                )
                            else:
                                await broker.append_log(
                                    task.id,
                                    f"✨ [Enriched Detail] Extracted full JD for {hh_tag} '{persisted_title}' ({len(persisted_desc)} chars)",
                                )
                    except Exception as e:
                        logger.error(
                            "Failed to extract detail for '%s': %s",
                            getattr(card, "title", "job"),
                            e,
                        )
                        await broker.append_log(
                            task.id,
                            f"❌ [Detail Error] Failed to extract detail for '{getattr(card, 'title', 'job')}': {e}",
                        )
                    finally:
                        detail_page.navigate_back()

            # 7. Post-screen checks: Reached max_jobs or empty scrolls safeguard
            if len(scanned_fingerprints) >= max_jobs:
                await broker.append_log(
                    task.id,
                    f"🎯 [Scan Quota Reached] Completed scan of {len(scanned_fingerprints)} jobs (max_jobs={max_jobs}).",
                )
                break

            if new_cards_in_view == 0:
                consecutive_empty_scrolls += 1
                if consecutive_empty_scrolls >= 3:
                    await broker.append_log(
                        task.id,
                        "🛑 [Feed Safeguard] 3 consecutive scrolls produced no new cards. Terminating pagination.",
                    )
                    break
            else:
                consecutive_empty_scrolls = 0

            # 8. Check bottom marker before scrolling
            if bottom_elem is not None or list_page.is_feed_bottom_reached() is True:
                await broker.append_log(
                    task.id,
                    "🛑 [Feed Boundary] Detected '暂无其他符合职位 / 为你推荐' end-of-feed marker. Terminating search pagination.",
                )
                break

            # 9. Perform humanized scroll
            list_page.scroll_job_list()
        if is_cancelled:
            total_scanned = len(scanned_fingerprints)
            summary = f"Finished scraping: task was cancelled by user (scanned {total_scanned}, scraped {len(scraped_jobs)}, skipped {skipped_count})"
            await broker.append_log(task.id, summary)
            return HandlerResult(
                success=True,
                output={
                    "total_scanned": total_scanned,
                    "total_scraped": len(scraped_jobs),
                    "skipped_count": skipped_count,
                    "scraped_jobs": scraped_jobs,
                },
            )

        if not scanned_fingerprints:
            # Fallback for mock environments or direct detail view
            total_scanned = 1
            try:
                job_posting = detail_page.extract_job_posting(timeout_sec=5.0)
                post_title = (job_posting.title or "").strip()
                post_company = (job_posting.company_name or "").strip()
                if (
                    not post_title
                    or post_title in ("未注明职位", "未注明岗位", "未知职位", "未知岗位")
                    or not post_company
                    or post_company in ("未注明公司", "未知公司")
                ):
                    logger.warning(
                        "Fallback job extraction ignored due to invalid title or company: title='%s', company='%s'",
                        post_title,
                        post_company,
                    )
                else:
                    from boss_agent.models import compute_job_fingerprint

                    fp = compute_job_fingerprint(
                        post_company,
                        post_title,
                        job_posting.recruiter_name or "招聘者",
                    )
                    if await broker.has_job_fingerprint(fp):
                        skipped_count += 1
                    else:
                        persisted = await broker.upsert_job_record(
                            {
                                "fingerprint": fp,
                                "title": post_title,
                                "company_name": post_company,
                                "recruiter_name": job_posting.recruiter_name or "招聘者",
                                "recruiter_title": getattr(job_posting, "recruiter_title", "")
                                or "",
                                "is_headhunter": getattr(job_posting, "is_headhunter", False),
                                "company_scale": getattr(job_posting, "company_scale", "") or "",
                                "industry": getattr(job_posting, "industry", "") or "",
                                "tags": getattr(job_posting, "tags", []) or [],
                                "salary_range": job_posting.salary_range,
                                "location": job_posting.location,
                                "digest": getattr(job_posting, "digest", "") or "",
                                "job_description": job_posting.job_description,
                                "status": JobRecordStatus.JD_SAVED.value,
                                **commute_columns(job_posting),
                                "search_keywords": [keyword] if keyword else [],
                                "source_task_id": task.id,
                            }
                        )
                        if persisted:
                            scraped_jobs.append(persisted)
                            rec_tag = (
                                "[猎头]"
                                if getattr(job_posting, "is_headhunter", False)
                                else "[直招]"
                            )
                            await broker.append_log(
                                task.id,
                                f"✅ Extracted {rec_tag} job: {post_title} @ {post_company} ({job_posting.salary_range})",
                            )
            except Exception as e:
                await broker.append_log(task.id, f"Notice on job extraction: {e}")
        else:
            total_scanned = len(scanned_fingerprints)

        summary = f"Finished scraping: scanned {total_scanned}, scraped {len(scraped_jobs)} job postings, skipped {skipped_count}"
        await broker.append_log(task.id, summary)
        return HandlerResult(
            success=True,
            output={
                "total_scanned": total_scanned,
                "scraped_count": len(scraped_jobs),
                "skipped_count": skipped_count,
                "jobs": scraped_jobs,
            },
        )
