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
    STATE_RANK,
    TARGET_ACTION_RANK,
    FilterConfig,
    JobRecordStatus,
    ScreeningPolicy,
    TargetAction,
)
from boss_agent.pages import (
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobDetailPage,
    JobListPage,
    SearchPage,
    StartupDialogPage,
)
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

logger = logging.getLogger(__name__)


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
            enrich_jd = bool(payload.get("enrich_jd", True))
            target_action = TargetAction.SAVE_JD if enrich_jd else TargetAction.DIGEST_ONLY
        required_rank = TARGET_ACTION_RANK.get(target_action, 2)

        await broker.append_log(
            task.id, f"Starting SCRAPE_JOBS (keyword='{keyword}', target_action='{target_action.value}', max_jobs={max_jobs})"
        )

        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()

        list_page = JobListPage(driver)
        list_page.navigate_to_home()

        enable_search = bool(payload.get("enable_search", True))
        if enable_search and keyword:
            search_page = SearchPage(driver)
            if not search_page.is_search_page():
                list_page.open_search(timeout_sec=5.0)
            search_page.search(keyword)
            await broker.append_log(task.id, f"Executed search for keyword '{keyword}'")
        elif not enable_search:
            await broker.append_log(
                task.id,
                "enable_search is False; browsing home recommendations without search keyword",
            )

        enable_filter = bool(payload.get("enable_filter", True))
        filter_raw = payload.get("filter")
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
            if filter_cfg.has_industry_filters:
                industry_page = IndustryFilterDialogPage(driver)
                await broker.append_log(
                    task.id, f"Applying industry filter: {filter_cfg.industries}"
                )
                try:
                    industry_page.apply_industry_filters(filter_cfg.industries, timeout_sec=5.0)
                except Exception as ex:
                    await broker.append_log(task.id, f"Notice applying industry filter: {ex}")

            if filter_cfg.has_filters:
                filter_page = FilterDialogPage(driver)
                await broker.append_log(
                    task.id,
                    f"Applying general filters: education={filter_cfg.education}, salary={filter_cfg.salary}, experience={filter_cfg.experience}",
                )
                try:
                    filter_page.apply_filters(filter_cfg, timeout_sec=5.0)
                except Exception as ex:
                    await broker.append_log(task.id, f"Notice applying general filters: {ex}")
        elif not enable_filter:
            await broker.append_log(task.id, "enable_filter is False; skipped job filtering")

        scraped_jobs: list[dict[str, Any]] = []
        skipped_count = 0
        scanned_fingerprints: set[str] = set()
        consecutive_empty_scrolls = 0
        detail_page = JobDetailPage(driver)

        policy_raw = payload.get("screening_policy")
        policy = ScreeningPolicy.from_dict(policy_raw) if policy_raw else ScreeningPolicy()

        while len(scanned_fingerprints) < max_jobs:
            # 0. Check if task was cancelled by user
            cur_task = await broker.get_task(task.id)
            if cur_task and cur_task.status == TaskStatus.CANCELLED:
                await broker.append_log(
                    task.id,
                    "🛑 [Task Cancelled] Task was cancelled by user. Terminating scrape pagination.",
                )
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

                # 3. State Machine Check against existing database record
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

                    cur_rank = STATE_RANK.get(existing_status, 1)
                    if cur_rank >= required_rank:
                        skipped_count += 1
                        await broker.append_log(
                            task.id,
                            f"⏭️ [State Machine] '{card.title}' already at '{existing_status}' (>= target '{target_action.value}'). Skipping detail opening.",
                        )
                        await broker.upsert_job_record({
                            "fingerprint": card.fingerprint,
                            "company_name": card.company_name,
                            "title": card.title,
                            "recruiter_name": card.recruiter_name,
                            "search_keywords": [keyword] if keyword else [],
                        })
                        continue

                # 4. Preliminary screening (zero-token gatekeeper)
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
                    ignored_record = {
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
                        "job_description": "",
                        "jd_key_requirements": card_tags,
                        "status": JobRecordStatus.IGNORED.value,
                        "search_keywords": [keyword] if keyword else [],
                        "source_task_id": task.id,
                    }
                    await broker.upsert_job_record(ignored_record)
                    await broker.append_log(
                        task.id,
                        f"⏭️ [初筛淘汰] '{card.title}' @ '{card.company_name}': {reason}",
                    )
                    continue

                # 5. Execute Action based on target_action
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
                    "job_description": existing_record.get("job_description", "") if existing_record else "",
                    "jd_key_requirements": card_tags,
                    "status": JobRecordStatus.DIGEST_ONLY.value,
                    "search_keywords": [keyword] if keyword else [],
                    "source_task_id": task.id,
                }

                if target_action == TargetAction.DIGEST_ONLY:
                    persisted = await broker.upsert_job_record(card_record)
                    scraped_jobs.append(persisted)
                    await broker.append_log(
                        task.id,
                        f"✅ [Direct Ingestion] Recorded {rec_type} job from search list: '{card.title}' @ '{card.company_name}' ({card_record.get('salary_range', '')})",
                    )
                    continue

                # For SAVE_JD (or AUTO_APPLY): Ingest card digest first, then inspect detail
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
                    try:
                        job_posting = detail_page.extract_job_posting(timeout_sec=4.0)
                        enriched_data = {
                            "fingerprint": card.fingerprint,
                            "title": job_posting.title or card.title,
                            "company_name": job_posting.company_name or card.company_name,
                            "recruiter_name": card.recruiter_name or job_posting.recruiter_name or "招聘者",
                            "recruiter_title": card.recruiter_title or getattr(job_posting, "recruiter_title", "") or "",
                            "is_headhunter": card.is_headhunter or getattr(job_posting, "is_headhunter", False),
                            "company_scale": card.company_scale or getattr(job_posting, "company_scale", "") or "",
                            "industry": card.industry or getattr(job_posting, "industry", "") or "",
                            "tags": card_tags or getattr(job_posting, "tags", []) or [],
                            "salary_range": job_posting.salary_range or card_record.get("salary_range", ""),
                            "location": job_posting.location or card_record.get("location", ""),
                            "digest": digest_text,
                            "job_description": job_posting.job_description or card_record.get("job_description", ""),
                            "status": JobRecordStatus.JD_SAVED.value,
                            "search_keywords": [keyword] if keyword else [],
                            "source_task_id": task.id,
                        }
                        persisted = await broker.upsert_job_record(enriched_data)
                        scraped_jobs[-1] = persisted
                        hh_tag = "[猎头]" if enriched_data["is_headhunter"] else "[直招]"
                        if "查看更多" in (persisted.get("job_description") or ""):
                            logger.error(
                                "Incomplete JD: '查看更多' still present in extracted JD for %s '%s'",
                                hh_tag,
                                persisted.get("title", ""),
                            )
                            await broker.append_log(
                                task.id,
                                f"❌ [Incomplete JD Error] '查看更多' was detected in extracted JD for {hh_tag} '{persisted['title']}'",
                            )
                        else:
                            await broker.append_log(
                                task.id,
                                f"✨ [Enriched Detail] Extracted full JD for {hh_tag} '{persisted['title']}' ({len(persisted.get('job_description', ''))} chars)",
                            )
                    except Exception as e:
                        logger.error("Failed to extract detail for '%s': %s", getattr(card, "title", "job"), e)
                        await broker.append_log(task.id, f"❌ [Detail Error] Failed to extract detail for '{getattr(card, 'title', 'job')}': {e}")
                    finally:
                        detail_page.navigate_back()

            # 6. Post-screen checks: Reached max_jobs or empty scrolls safeguard
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

            # 7. Check bottom marker before scrolling
            if bottom_elem is not None or list_page.is_feed_bottom_reached() is True:
                await broker.append_log(
                    task.id,
                    "🛑 [Feed Boundary] Detected '暂无其他符合职位 / 为你推荐' end-of-feed marker. Terminating search pagination.",
                )
                break

            # 8. Perform humanized scroll
            list_page.scroll_job_list()
        if not scanned_fingerprints:
            # Fallback for mock environments or direct detail view
            total_scanned = 1
            try:
                job_posting = detail_page.extract_job_posting(timeout_sec=5.0)
                from boss_agent.models import compute_job_fingerprint

                fp = compute_job_fingerprint(
                    job_posting.company_name, job_posting.title, job_posting.recruiter_name or "招聘者"
                )
                if await broker.has_job_fingerprint(fp):
                    skipped_count += 1
                else:
                    persisted = await broker.upsert_job_record(
                        {
                            "fingerprint": fp,
                            "title": job_posting.title,
                            "company_name": job_posting.company_name,
                            "recruiter_name": job_posting.recruiter_name or "招聘者",
                            "recruiter_title": getattr(job_posting, "recruiter_title", "") or "",
                            "is_headhunter": getattr(job_posting, "is_headhunter", False),
                            "company_scale": getattr(job_posting, "company_scale", "") or "",
                            "industry": getattr(job_posting, "industry", "") or "",
                            "tags": getattr(job_posting, "tags", []) or [],
                            "salary_range": job_posting.salary_range,
                            "location": job_posting.location,
                            "digest": getattr(job_posting, "digest", "") or "",
                            "job_description": job_posting.job_description,
                            "status": JobRecordStatus.JD_SAVED.value if target_action != TargetAction.DIGEST_ONLY else JobRecordStatus.DIGEST_ONLY.value,
                            "search_keywords": [keyword] if keyword else [],
                            "source_task_id": task.id,
                        }
                    )
                    scraped_jobs.append(persisted)
                    rec_tag = "[猎头]" if getattr(job_posting, "is_headhunter", False) else "[直招]"
                    await broker.append_log(
                        task.id,
                        f"✅ Extracted {rec_tag} job: {job_posting.title} @ {job_posting.company_name} ({job_posting.salary_range})",
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

