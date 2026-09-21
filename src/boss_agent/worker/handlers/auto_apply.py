from datetime import UTC, datetime
from typing import Any

from boss_agent.broker.models import AutomationTask, TaskStatus, TaskType
from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.graph import run_job_application_graph
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import (
    APPLIED_SOURCE_AGENT,
    APPLIED_SOURCE_PLATFORM_HISTORICAL,
    EXPIRED_POSTING_REASON,
    ChatButtonState,
    FilterConfig,
    JobRecordStatus,
    ScreeningPolicy,
    is_masked_company_name,
)
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
from boss_agent.settings import resolve_communication_cooldown_days
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.base import BaseTaskHandler, HandlerResult
from boss_agent.worker.handlers.search_entry import run_search_entry


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
        search_name = payload.get("search_name") or payload.get("saved_search_name") or ""
        strategy_desc = f"strategy='{search_name}', " if search_name else ""
        await broker.append_log(
            task.id,
            f"Starting AUTO_APPLY ({strategy_desc}candidate='{profile.name}', keyword='{keyword}', "
            f"min_score={min_score}, mode='{mode_desc}')",
        )

        # 1.5. Resolve Screening Policy & Pre-flight Defense
        policy_raw = payload.get("screening_policy")
        policy = (
            ScreeningPolicy.from_dict(policy_raw) if policy_raw else ScreeningPolicy.load_default()
        )

        direct_job_id = payload.get("direct_job_id")
        existing_rec: dict[str, Any] | None = None
        if direct_job_id:
            existing_rec = await broker.get_job_record(direct_job_id)
            if existing_rec and existing_rec.get("status") == JobRecordStatus.IGNORED.value:
                reason = existing_rec.get("screened_reason") or "已被标记为初筛淘汰/忽略"
                await broker.append_log(
                    task.id,
                    f"🛑 [定向投递防御] 职位 '{existing_rec.get('title')}' @ '{existing_rec.get('company_name')}' 处于淘汰状态 ({reason})，已自动取消沟通以保护每日沟通额度。",
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
            passed, reason = policy.matches_card_keywords(
                title=target_title or "",
                company_name=target_company or "",
                tags=[],
                digest="",
            )
            if not passed:
                await broker.append_log(
                    task.id,
                    f"🛑 [定向投递防御] 职位 '{target_title}' @ '{target_company}' 命中初筛黑名单: {reason}。已自动取消沟通以保护每日沟通额度。",
                )
                if direct_job_id and existing_rec:
                    updated_data = dict(existing_rec)
                    updated_data["status"] = JobRecordStatus.IGNORED.value
                    updated_data["screened_reason"] = reason
                    await broker.upsert_job_record(updated_data)
                return HandlerResult(
                    success=True,
                    output={
                        "applied": False,
                        "status": "filtered_by_keyword",
                        "reason": reason,
                    },
                )

        # 1.7. Enterprise-level direct-hire exclusion (直招同企避嫌): a shared in-house HR candidate
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
            excluded_companies = await broker.get_applied_direct_companies(
                cooldown_days=resolve_communication_cooldown_days(payload)
            )
            if target_company.strip() in excluded_companies:
                await broker.append_log(
                    task.id,
                    f"⏭️ [同企已沟通避嫌] '{target_title}' @ '{target_company}' 所属直招企业已有沟通记录，"
                    f"已自动取消本次投递以保护每日沟通额度。如需复投，请在 Web 面板清除该企业沟通状态。",
                )
                return HandlerResult(
                    success=True,
                    output={
                        "applied": False,
                        "status": "skipped_enterprise_exclusion",
                        "reason": f"直招企业 '{target_company}' 已有沟通记录，处于避嫌冷却期内",
                    },
                )

        # 2. Reset / Dismiss Startup Dialogs
        startup_page = StartupDialogPage(driver)
        if startup_page.is_dialog_present():
            startup_page.dismiss_dialog()

        list_page = JobListPage(driver)

        # 3. Search Keyword if specified
        enable_search = bool(payload.get("enable_search", True))
        if enable_search and keyword:
            if await run_search_entry(broker, task.id, list_page, SearchPage(driver), keyword):
                await broker.append_log(task.id, f"Navigated to search results for '{keyword}'")
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

        # 4.5. Zero-cost backout: consult the call-to-action button before drafting or sending
        chat_state = detail_page.get_chat_button_state()
        if chat_state in (ChatButtonState.COMMUNICATED, ChatButtonState.CLOSED):
            if direct_job_id and existing_rec:
                updated_data = dict(existing_rec)
                if chat_state == ChatButtonState.COMMUNICATED:
                    updated_data["status"] = JobRecordStatus.APPLIED.value
                    updated_data["applied_source"] = APPLIED_SOURCE_PLATFORM_HISTORICAL
                    await broker.append_log(
                        task.id,
                        f"⏭️ [既有沟通] '{existing_rec.get('title')}' @ '{existing_rec.get('company_name')}' 平台已沟通，终止招呼语生成与发送",
                    )
                else:
                    updated_data["status"] = JobRecordStatus.IGNORED.value
                    updated_data["screened_reason"] = EXPIRED_POSTING_REASON
                    await broker.append_log(
                        task.id,
                        f"🛑 [岗位失效] '{existing_rec.get('title')}' @ '{existing_rec.get('company_name')}' 已停止招聘/下线，终止投递",
                    )
                await broker.upsert_job_record(updated_data)
            else:
                await broker.append_log(
                    task.id,
                    f"⏭️ [既有沟通] 当前屏幕岗位平台已沟通/已失效（{chat_state.value}），终止投递",
                )
            detail_page.navigate_back()
            return HandlerResult(
                success=True,
                output={"applied": False, "status": chat_state.value},
            )

        try:
            job_posting = detail_page.extract_job_posting(timeout_sec=5.0)
        except Exception as e:
            await broker.append_log(task.id, f"Could not extract current job posting: {e}")
            return HandlerResult(success=False, error_message=str(e))

        job_title = (job_posting.title or "").strip()
        if not job_title or job_title in ("未注明职位", "未注明岗位", "未知职位", "未知岗位"):
            await broker.append_log(
                task.id,
                f"❌ [Invalid Title] Current screen does not show a valid job title: '{job_posting.title}'. Skipping application.",
            )
            return HandlerResult(
                success=False,
                error_message=f"Current screen does not show a valid job title: '{job_posting.title}'",
            )

        # 5. Execute Multi-Stage Screening & Greeting Pipeline via LangGraph
        card = JobCardBrief(
            title=job_posting.title,
            company_name=job_posting.company_name,
            recruiter_name=job_posting.recruiter_name or "",
            recruiter_title=job_posting.recruiter_title or "",
            is_headhunter=job_posting.is_headhunter,
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
        app_rule_pass = bool(graph_result.get("app_rule_pass", True))
        relaxed = bool(graph_result.get("relaxed_by_whitelist", False))
        app_rule_violation = graph_result.get("app_rule_violation") or ""
        relaxation_reason = graph_result.get("relaxation_reason") or ""

        audit_parts: list[str] = []
        if app_rule_violation:
            audit_parts.append(f"App端强制过滤违例: {app_rule_violation}")
        if relaxation_reason:
            audit_parts.append(relaxation_reason)
        screening_audit = "；".join(audit_parts)

        if not keyword_pass:
            reason = graph_result.get("keyword_reason", "未通过关键字初筛")
            await broker.append_log(task.id, f"⏭️ [初筛淘汰] '{job_posting.title}': {reason}")
            await broker.upsert_job_record(
                {
                    "fingerprint": card.fingerprint,
                    "title": job_posting.title,
                    "company_name": job_posting.company_name,
                    "recruiter_name": job_posting.recruiter_name or "",
                    "salary_range": job_posting.salary_range,
                    "location": job_posting.location or "",
                    "job_description": job_posting.job_description,
                    "status": JobRecordStatus.IGNORED.value,
                    "screened_reason": reason,
                    "relaxed_by_whitelist": relaxed,
                    "screening_audit": screening_audit,
                    "search_keywords": [keyword] if keyword else [],
                    "source_task_id": task.id,
                }
            )
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

        if not app_rule_pass and not relaxed:
            reason = app_rule_violation or "未通过App端强制过滤"
            await broker.append_log(task.id, f"🛑 [App端强制过滤] '{job_posting.title}': {reason}")
            await broker.upsert_job_record(
                {
                    "fingerprint": card.fingerprint,
                    "title": job_posting.title,
                    "company_name": job_posting.company_name,
                    "recruiter_name": job_posting.recruiter_name or "",
                    "recruiter_title": job_posting.recruiter_title or "",
                    "is_headhunter": job_posting.is_headhunter,
                    "salary_range": job_posting.salary_range,
                    "location": job_posting.location or "",
                    "job_description": job_posting.job_description,
                    "status": JobRecordStatus.IGNORED.value,
                    "screened_reason": reason,
                    "relaxed_by_whitelist": False,
                    "screening_audit": screening_audit,
                    "search_keywords": [keyword] if keyword else [],
                    "source_task_id": task.id,
                }
            )
            return HandlerResult(
                success=True,
                output={
                    "applied": False,
                    "status": "filtered_by_app_rule",
                    "reason": reason,
                    "job": {
                        "title": job_posting.title,
                        "company_name": job_posting.company_name,
                    },
                },
            )

        if relaxed:
            await broker.append_log(
                task.id,
                f"🎗️ [白名单放宽] '{job_posting.title}' 获豁免继续评估: {relaxation_reason}",
            )

        if not deep_pass:
            reason = graph_result.get("deep_screen_reason", "未通过JD语义精筛")
            await broker.append_log(task.id, f"⏭️ [精筛淘汰] '{job_posting.title}': {reason}")
            await broker.upsert_job_record(
                {
                    "fingerprint": card.fingerprint,
                    "title": job_posting.title,
                    "company_name": job_posting.company_name,
                    "recruiter_name": job_posting.recruiter_name or "",
                    "salary_range": job_posting.salary_range,
                    "location": job_posting.location or "",
                    "job_description": job_posting.job_description,
                    "status": JobRecordStatus.IGNORED.value,
                    "screened_reason": reason,
                    "relaxed_by_whitelist": relaxed,
                    "screening_audit": screening_audit,
                    "search_keywords": [keyword] if keyword else [],
                    "source_task_id": task.id,
                }
            )
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

        # 6. Branch Execution: Daily greeting limit checking & Auto-Send vs Offline Draft
        from boss_agent.settings import load_settings

        sys_settings = load_settings()
        daily_limit = int(
            payload.get("daily_greeting_limit") or sys_settings.get("daily_greeting_limit", 20)
        )

        applied = False
        if auto_send and not preview_only:
            if match_score >= min_score:
                today_applied = await broker.count_today_applied_jobs()
                if today_applied >= daily_limit:
                    await broker.append_log(
                        task.id,
                        f"⚠️ [LIMIT REACHED] Daily greeting limit reached ({today_applied}/{daily_limit}). "
                        f"Degrading to offline draft for '{job_posting.title}' @ '{job_posting.company_name}' (status: matched).",
                    )
                    await broker.upsert_job_record(
                        {
                            "fingerprint": card.fingerprint,
                            "title": job_posting.title,
                            "company_name": job_posting.company_name,
                            "recruiter_name": job_posting.recruiter_name or "",
                            "salary_range": job_posting.salary_range,
                            "location": job_posting.location or "",
                            "job_description": job_posting.job_description,
                            "status": JobRecordStatus.MATCHED,
                            "match_score": match_score,
                            "greeting_message": greeting_message,
                            "jd_key_requirements": match_reasons,
                            "relaxed_by_whitelist": relaxed,
                            "screening_audit": screening_audit,
                            "search_keywords": [keyword] if keyword else [],
                            "source_task_id": task.id,
                        }
                    )
                    applied = False
                else:
                    cur_task = await broker.get_task(task.id)
                    if cur_task and cur_task.status == TaskStatus.CANCELLED:
                        await broker.append_log(
                            task.id,
                            "🛑 [Task Cancelled] Task was cancelled by user before chat dispatch.",
                        )
                        return HandlerResult(success=True, error_message="Task cancelled by user")

                    if detail_page.open_chat(timeout_sec=5.0):
                        chat_page.type_greeting_message(greeting_message, timeout_sec=5.0)
                        dispatched = chat_page.click_send(timeout_sec=3.0)
                        applied = dispatched
                        if dispatched:
                            await broker.append_log(
                                task.id,
                                f"✅ [AUTO_SEND] Dispatched greeting message to {job_posting.title} @ {job_posting.company_name} ({today_applied + 1}/{daily_limit} today)",
                            )
                        else:
                            await broker.append_log(
                                task.id,
                                f"⚠️ [AUTO_SEND] Could not send greeting to {job_posting.title} @ {job_posting.company_name}: "
                                "send control unavailable. Kept as a matched draft for manual sending.",
                            )
                        await broker.upsert_job_record(
                            {
                                "fingerprint": card.fingerprint,
                                "title": job_posting.title,
                                "company_name": job_posting.company_name,
                                "recruiter_name": job_posting.recruiter_name or "",
                                "salary_range": job_posting.salary_range,
                                "location": job_posting.location or "",
                                "job_description": job_posting.job_description,
                                # Only a message that actually left the app counts as applied;
                                # otherwise the employer would become a same-company exclusion
                                # anchor without ever having been contacted.
                                "status": JobRecordStatus.APPLIED
                                if dispatched
                                else JobRecordStatus.MATCHED,
                                "applied_at": datetime.now(UTC).isoformat() if dispatched else None,
                                "applied_source": APPLIED_SOURCE_AGENT if dispatched else "",
                                "match_score": match_score,
                                "greeting_message": greeting_message,
                                "jd_key_requirements": match_reasons,
                                "search_keywords": [keyword] if keyword else [],
                                "source_task_id": task.id,
                            }
                        )
                        chat_page.navigate_back()
            else:
                await broker.append_log(
                    task.id,
                    f"⏭️ [AUTO_SEND] Skipped: Match score {match_score} < threshold {min_score}",
                )
                await broker.upsert_job_record(
                    {
                        "fingerprint": card.fingerprint,
                        "title": job_posting.title,
                        "company_name": job_posting.company_name,
                        "recruiter_name": job_posting.recruiter_name or "",
                        "salary_range": job_posting.salary_range,
                        "location": job_posting.location or "",
                        "job_description": job_posting.job_description,
                        "status": JobRecordStatus.JD_SAVED,
                        "match_score": match_score,
                        "greeting_message": greeting_message,
                        "jd_key_requirements": match_reasons,
                        "search_keywords": [keyword] if keyword else [],
                        "source_task_id": task.id,
                    }
                )
        else:
            # Offline draft mode (Safe Mode - no typing in App)
            await broker.append_log(
                task.id,
                f"💾 [OFFLINE DRAFT] Saved JD and drafted greeting for '{job_posting.title}' (status: matched).",
            )
            await broker.upsert_job_record(
                {
                    "fingerprint": card.fingerprint,
                    "title": job_posting.title,
                    "company_name": job_posting.company_name,
                    "recruiter_name": job_posting.recruiter_name or "",
                    "salary_range": job_posting.salary_range,
                    "location": job_posting.location or "",
                    "job_description": job_posting.job_description,
                    "status": JobRecordStatus.MATCHED,
                    "match_score": match_score,
                    "greeting_message": greeting_message,
                    "jd_key_requirements": match_reasons,
                    "relaxed_by_whitelist": relaxed,
                    "screening_audit": screening_audit,
                    "search_keywords": [keyword] if keyword else [],
                    "source_task_id": task.id,
                }
            )
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
