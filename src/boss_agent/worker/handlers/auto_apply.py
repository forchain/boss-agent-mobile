import asyncio
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.graph import run_job_application_graph
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import FilterConfig, ScreeningPolicy
from boss_agent.pages import (
    ChatPage,
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobCardBrief,
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

        # 5. Execute Multi-Stage Screening & Greeting Pipeline via LangGraph
        policy_raw = payload.get("screening_policy")
        policy = ScreeningPolicy.from_dict(policy_raw) if policy_raw else ScreeningPolicy()

        card = JobCardBrief(
            title=job_posting.title,
            company_name=job_posting.company_name,
            recruiter_name=job_posting.recruiter_name or "",
            salary_range=job_posting.salary_range,
            location=job_posting.location or "",
            tags=job_posting.tags,
        )

        graph_result = run_job_application_graph(
            card=card,
            policy=policy,
            candidate_profile=profile,
            jd_text=job_posting.job_description,
            llm_client=self.llm_client,
        )

        keyword_pass = graph_result.get("keyword_pass", True)
        deep_pass = graph_result.get("deep_screen_pass", True)

        if not keyword_pass:
            reason = graph_result.get("keyword_reason", "未通过关键字初筛")
            await broker.append_log(task.id, f"⏭️ [初筛淘汰] '{job_posting.title}': {reason}")
            return HandlerResult(
                success=True,
                output={
                    "applied": False,
                    "status": "filtered_by_keyword",
                    "reason": reason,
                    "job": {
                        "title": job_posting.title,
                        "company_name": job_posting.company_name,
                    },
                },
            )

        if not deep_pass:
            reason = graph_result.get("deep_screen_reason", "未通过JD语义精筛")
            await broker.append_log(task.id, f"⏭️ [精筛淘汰] '{job_posting.title}': {reason}")
            return HandlerResult(
                success=True,
                output={
                    "applied": False,
                    "status": "filtered_by_deep_screener",
                    "reason": reason,
                    "job": {
                        "title": job_posting.title,
                        "company_name": job_posting.company_name,
                    },
                },
            )

        greeting_message = graph_result.get("greeting_message") or ""
        match_score = graph_result.get("match_score", 80)
        match_reasons = graph_result.get("match_reasons") or []
        req_summary = "; ".join(match_reasons) if match_reasons else "无"

        await broker.append_log(
            task.id,
            f"Evaluated '{job_posting.title}' @ '{job_posting.company_name}': "
            f"Score {match_score}/100 | Match Reasons: [{req_summary}]",
        )
        await broker.append_log(
            task.id,
            f'Tailored Greeting Draft: "{greeting_message}"',
        )

        # 6. Branch Execution: Preview vs Auto-Send
        applied = False
        if auto_send and not preview_only:
            if match_score >= min_score:
                if detail_page.open_chat(timeout_sec=5.0):
                    chat_page.type_greeting_message(greeting_message, timeout_sec=5.0)
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
                    f"⏭️ [AUTO_SEND] Skipped: Match score {match_score} < threshold {min_score}",
                )
        else:
            # Preview mode (safe mode)
            if detail_page.open_chat(timeout_sec=5.0):
                chat_page.type_greeting_message(greeting_message, timeout_sec=5.0)
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
                "score": match_score,
                "jd_key_requirements": match_reasons,
                "greeting_message": greeting_message,
                "job": {
                    "title": job_posting.title,
                    "company_name": job_posting.company_name,
                    "salary_range": job_posting.salary_range,
                },
            },
        )
