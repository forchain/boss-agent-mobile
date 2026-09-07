import asyncio
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.matching import JobMatchGreetingService
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import FilterConfig
from boss_agent.pages import (
    ChatPage,
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobDetailPage,
    JobListPage,
    SearchPage,
    StartupDialogPage,
)
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult


class AutoApplyHandler(BaseTaskHandler):
    """Executes end-to-end matching and automated greeting application."""

    def __init__(self, llm_client: Any | None = None) -> None:
        self.llm_client = llm_client

    @property
    def task_type(self) -> TaskType:
        return TaskType.AUTO_APPLY

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
        min_score = float(payload.get("min_score", 70))
        preview_only = bool(payload.get("preview_only", True))
        auto_send = bool(payload.get("auto_send", False))
        preview_timeout_sec = float(payload.get("preview_timeout_sec", 3.0))

        # 1. Resolve Candidate Profile
        profile_data = payload.get("candidate_profile")
        if not profile_data:
            profile_data = await broker.get_candidate_profile(user_id="default")

        profile = (
            StructuredCandidateProfile.from_dict(profile_data)
            if profile_data
            else StructuredCandidateProfile()
        )

        mode_desc = (
            "Auto-Send" if (auto_send and not preview_only) else "Preview Draft Only (Safe Mode)"
        )
        await broker.append_log(
            task.id,
            f"Starting AUTO_APPLY (candidate='{profile.name}', keyword='{keyword}', "
            f"min_score={min_score}, mode='{mode_desc}')",
        )

        # 2. Reset / Dismiss Startup Dialogs
        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()

        list_page = JobListPage(driver)
        list_page.navigate_to_home()

        # 3. Search Keyword if specified
        enable_search = bool(payload.get("enable_search", True))
        if enable_search and keyword:
            search_page = SearchPage(driver)
            if not search_page.is_search_page():
                list_page.open_search(timeout_sec=5.0)
            search_page.search(keyword)
            await broker.append_log(task.id, f"Navigated to search results for '{keyword}'")
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

        # 4. Extract Job Posting

        detail_page = JobDetailPage(driver)
        chat_page = ChatPage(driver)

        try:
            job_posting = detail_page.extract_job_posting(timeout_sec=5.0)
        except Exception as e:
            await broker.append_log(task.id, f"Could not extract current job posting: {e}")
            return HandlerResult(success=False, error_message=str(e))

        # 5. Evaluate Match & Draft Tailored Greeting via LLM
        matching_svc = JobMatchGreetingService(
            llm_client=self.llm_client, candidate_profile=profile
        )
        match_result = matching_svc.evaluate_and_draft_greeting(job=job_posting, profile=profile)

        req_summary = (
            "; ".join(match_result.jd_key_requirements)
            if match_result.jd_key_requirements
            else "无"
        )
        await broker.append_log(
            task.id,
            f"Evaluated '{job_posting.title}' @ '{job_posting.company_name}': "
            f"Score {match_result.match_score}/100 | JD Requirements: [{req_summary}]",
        )
        await broker.append_log(
            task.id,
            f'Tailored Greeting Draft: "{match_result.greeting_message}"',
        )

        # 6. Branch Execution: Preview vs Auto-Send
        applied = False
        if auto_send and not preview_only:
            if match_result.match_score >= min_score:
                if detail_page.open_chat(timeout_sec=5.0):
                    chat_page.type_greeting_message(match_result.greeting_message, timeout_sec=5.0)
                    chat_page.click_send(timeout_sec=3.0)
                    applied = True
                    await broker.append_log(
                        task.id,
                        f"✅ [AUTO_SEND] Dispatched greeting message to {job_posting.title} @ {job_posting.company_name}",
                    )
                    chat_page.navigate_back()
            else:
                await broker.append_log(
                    task.id,
                    f"⏭️ [AUTO_SEND] Skipped: Match score {match_result.match_score} < threshold {min_score}",
                )
        else:
            # Preview mode (safe mode)
            if detail_page.open_chat(timeout_sec=5.0):
                chat_page.type_greeting_message(match_result.greeting_message, timeout_sec=5.0)
                await broker.append_log(
                    task.id,
                    f"⏳ [PREVIEW MODE] Entered greeting into chat box. Pausing for {preview_timeout_sec}s (NOT SENT)...",
                )
                await asyncio.sleep(preview_timeout_sec)
                chat_page.navigate_back()
                applied = False

        return HandlerResult(
            success=True,
            output={
                "applied": applied,
                "score": match_result.match_score,
                "jd_key_requirements": match_result.jd_key_requirements,
                "greeting_message": match_result.greeting_message,
                "job": {
                    "title": job_posting.title,
                    "company_name": job_posting.company_name,
                    "salary_range": job_posting.salary_range,
                },
            },
        )
