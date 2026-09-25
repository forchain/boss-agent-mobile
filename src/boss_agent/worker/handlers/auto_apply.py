"""
src/boss_agent/worker/handlers/auto_apply.py
============================================
Handler for AUTO_APPLY: streams screened jobs out of the mobile feed and dispatches
tailored greetings up to the daily quota.

Feed pagination, screening, greeting dispatch and quota degradation all live in the
deep ``JobFeedPipeline`` (ADR 0013). This handler keeps only the task-level pre-flight
defense that protects the daily greeting quota before the device is touched, then
configures the run with ``target_action=auto_apply``.
"""

import logging
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.feed_pipeline import FeedStreamConfig, JobFeedPipeline
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import JobRecordStatus, TargetAction, is_masked_company_name
from boss_agent.screening import CandidateScreener
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult

logger = logging.getLogger(__name__)


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
        store = broker.job_store
        screener = CandidateScreener(llm_client=self.llm_client)
        profile = await self._resolve_profile(broker, payload)

        config = FeedStreamConfig.from_payload(payload)
        config.source_task_id = task.id
        config.candidate_profile = profile
        # The task type names the action, so an AUTO_APPLY run always streams outreach
        # even when the payload omitted the redundant target_action field.
        config.target_action = TargetAction.AUTO_APPLY

        mode_desc = (
            "Auto-Send" if (config.auto_send and not config.preview_only)
            else "Preview Draft Only (Safe Mode)"
        )
        search_name = payload.get("search_name") or payload.get("saved_search_name") or ""
        strategy_desc = f"strategy='{search_name}', " if search_name else ""
        await broker.append_log(
            task.id,
            f"Starting AUTO_APPLY ({strategy_desc}candidate='{profile.name}', "
            f"keyword='{config.keyword}', min_score={config.min_score}, mode='{mode_desc}')",
        )

        refusal = await self._preflight(broker, task, payload, config, store, screener)
        if refusal is not None:
            return refusal

        pipeline = JobFeedPipeline.for_task(
            broker=broker, task_id=task.id, driver=driver, screener=screener
        )
        result = await pipeline.stream_jobs(config)

        if result.search_failed:
            return HandlerResult(
                success=False,
                output={"error": result.error_message, "search_failed": True},
            )

        output = {
            "applied": result.applied,
            "status": result.outcome,
            "score": result.score,
            "jd_key_requirements": result.jd_key_requirements,
            "greeting_message": result.greeting_message,
            "job": result.job,
            "processed_count": result.processed,
            "skipped_count": result.skipped,
        }
        if result.cancelled:
            return HandlerResult(
                success=True, error_message="Task cancelled by user", output=output
            )
        return HandlerResult(success=True, output=output)

    async def _resolve_profile(
        self, broker: BaseTaskBroker, payload: dict[str, Any]
    ) -> StructuredCandidateProfile:
        profile_data = payload.get("candidate_profile")
        if not profile_data:
            profile_data = await broker.get_candidate_profile(user_id="default")
        return (
            StructuredCandidateProfile.from_dict(profile_data)
            if profile_data
            else StructuredCandidateProfile()
        )

    async def _preflight(
        self,
        broker: BaseTaskBroker,
        task: AutomationTask,
        payload: dict[str, Any],
        config: FeedStreamConfig,
        store: Any,
        screener: CandidateScreener,
    ) -> HandlerResult | None:
        """Refuse a targeted application that is not worth a single device interaction.

        These checks guard the daily greeting quota: an already-rejected posting, a
        blacklisted company, or an employer already contacted under the same-employer
        cool-down must never cost a communication slot.
        """
        direct_job_id = config.direct_job_id
        existing_rec: dict[str, Any] | None = None
        if direct_job_id:
            existing_rec = await store.get_job_record(direct_job_id)
            if existing_rec and existing_rec.get("status") == JobRecordStatus.IGNORED.value:
                reason = existing_rec.get("screened_reason") or "已被标记为初筛淘汰/忽略"
                await broker.append_log(
                    task.id,
                    f"🛑 [定向投递防御] 职位 '{existing_rec.get('title')}' @ "
                    f"'{existing_rec.get('company_name')}' 处于淘汰状态 ({reason})，"
                    f"已自动取消沟通以保护每日沟通额度。",
                )
                return HandlerResult(
                    success=True,
                    output={
                        "applied": False,
                        "status": "ignored_job_protected",
                        "reason": reason,
                    },
                )

        target_title = payload.get("job_title") or (
            existing_rec.get("title") if existing_rec else None
        )
        target_company = payload.get("company_name") or (
            existing_rec.get("company_name") if existing_rec else None
        )
        if target_title or target_company:
            verdict = screener.evaluate_card(
                {"title": target_title or "", "company_name": target_company or ""},
                config.screening_policy,
            )
            if not verdict.passed:
                await broker.append_log(
                    task.id,
                    f"🛑 [定向投递防御] 职位 '{target_title}' @ '{target_company}' "
                    f"命中初筛黑名单: {verdict.reason}。已自动取消沟通以保护每日沟通额度。",
                )
                if direct_job_id and existing_rec:
                    updated_data = dict(existing_rec)
                    updated_data["status"] = JobRecordStatus.IGNORED.value
                    updated_data["screened_reason"] = verdict.reason
                    await store.upsert_job_record(updated_data)
                return HandlerResult(
                    success=True,
                    output={
                        "applied": False,
                        "status": "filtered_by_keyword",
                        "reason": verdict.reason,
                    },
                )

        # Enterprise-level direct-hire exclusion (直招同企避嫌): a shared in-house HR candidate
        # pool means a second contact under the same employer is redundant. Fail open when the
        # recruitment channel is unknown, and let the cool-down window release stale contacts.
        target_is_headhunter = payload.get("is_headhunter")
        if target_is_headhunter is None and existing_rec:
            target_is_headhunter = existing_rec.get("is_headhunter")
        if (
            target_company
            and target_is_headhunter is False
            and not is_masked_company_name(target_company.strip())
        ):
            excluded_companies = await store.get_applied_direct_companies(
                cooldown_days=config.cooldown_days
            )
            if target_company.strip() in excluded_companies:
                await broker.append_log(
                    task.id,
                    f"⏭️ [同企已沟通避嫌] '{target_title}' @ '{target_company}' "
                    f"所属直招企业已有沟通记录，已自动取消本次投递以保护每日沟通额度。"
                    f"如需复投，请在 Web 面板清除该企业沟通状态。",
                )
                return HandlerResult(
                    success=True,
                    output={
                        "applied": False,
                        "status": "skipped_enterprise_exclusion",
                        "reason": (
                            f"直招企业 '{target_company}' 已有沟通记录，处于避嫌冷却期内"
                        ),
                    },
                )
        return None
