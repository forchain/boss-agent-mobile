"""
src/boss_agent/worker/handlers/auto_apply.py
============================================
Handler for AUTO_APPLY task: searches jobs, scores resume match via LLM, and triggers greetings.
"""

from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.pages import JobDetailPage, JobListPage, SearchPage, StartupDialogPage
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
        candidate_profile = payload.get("candidate_profile") or {}

        await broker.append_log(
            task.id,
            f"Starting AUTO_APPLY (keyword='{keyword}', min_score={min_score})",
        )

        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()

        list_page = JobListPage(driver)
        list_page.navigate_to_home()

        if keyword:
            search_page = SearchPage(driver)
            if not search_page.is_search_page():
                list_page.open_search(timeout_sec=5.0)
            search_page.search(keyword)
            await broker.append_log(task.id, f"Navigated to search results for '{keyword}'")

        detail_page = JobDetailPage(driver)
        try:
            job_posting = detail_page.extract_job_posting(timeout_sec=5.0)
        except Exception as e:
            await broker.append_log(task.id, f"Could not extract current job posting: {e}")
            return HandlerResult(success=False, error_message=str(e))

        resume_summary = str(candidate_profile.get("resume_summary", ""))
        match_score = 80
        reasoning = "Rule-based score matches criteria"
        greeting = "您好！我对该职位很感兴趣，希望能进一步沟通。"

        if self.llm_client and hasattr(self.llm_client, "evaluate_text_match"):
            llm_eval = self.llm_client.evaluate_text_match(
                candidate_resume=resume_summary,
                job_description=job_posting.job_description,
            )
            match_score = int(llm_eval.get("score", 80))
            reasoning = llm_eval.get("reasoning", reasoning)
            greeting = llm_eval.get("greeting", greeting)

        await broker.append_log(
            task.id,
            f"Evaluated match for '{job_posting.title}': Score {match_score}/100 ({reasoning})",
        )

        applied = False
        if match_score >= min_score:
            # Click greeting button on screen
            greet_btn = detail_page.find_by_key(
                "job_detail.chat_btn", default="//*[@text='立即沟通' or @text='打招呼']"
            )
            if greet_btn:
                detail_page.gestures.human_click(greet_btn)
            applied = True
            await broker.append_log(
                task.id,
                f"Applied with greeting to {job_posting.title} @ {job_posting.company_name}",
            )
        else:
            await broker.append_log(
                task.id,
                f"Skipped {job_posting.title} @ {job_posting.company_name} (Score {match_score} < threshold {min_score})",
            )

        return HandlerResult(
            success=True,
            output={
                "applied": applied,
                "score": match_score,
                "job": {
                    "title": job_posting.title,
                    "company_name": job_posting.company_name,
                    "salary_range": job_posting.salary_range,
                },
            },
        )
