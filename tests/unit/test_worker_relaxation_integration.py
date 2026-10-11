"""
tests/unit/test_worker_relaxation_integration.py
================================================
End-to-end worker integration for App-Enforced Filters & Whitelist Relaxation
(Ticket #191, Spec #187): graph output state (app_rule_violation,
relaxed_by_whitelist, relaxation_reason) must be captured by AUTO_APPLY and
SCRAPE_JOBS handlers and persisted in job_records audit fields.
"""

from unittest.mock import MagicMock, patch

import pytest
from _card_fixtures import located

from boss_agent.broker.models import TaskType
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.job_entities import JobCardBrief, JobPosting
from boss_agent.worker.config import WorkerConfig
from boss_agent.worker.context import WorkerContext
from boss_agent.worker.handlers.auto_apply import AutoApplyHandler
from boss_agent.worker.handlers.scrape_jobs import ScrapeJobsHandler


def _mock_driver():
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    return driver


def _headhunter_posting():
    return JobPosting(
        title="大模型 Agent 平台架构师",
        company_name="某人力资源服务公司",
        salary_range="40-60K",
        job_description=(
            "主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
            "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
        ),
        recruiter_name="钟先生 · 猎头顾问",
    )


@pytest.mark.asyncio
async def test_auto_apply_rescued_headhunter_job_persists_relaxation_audit():
    """AUTO_APPLY: headhunter job violating direct_only but rescued by whitelist
    relaxation proceeds to greeting draft and persists relaxed_by_whitelist +
    screening_audit in the job record."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-relax-apply"), driver=_mock_driver())
    handler = AutoApplyHandler(llm_client=_drafting_llm())

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "大模型",
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["大模型"],
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage"),
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
        patch("boss_agent.feed_pipeline.ChatPage"),
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _headhunter_posting()

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output.get("applied") is False  # preview/offline draft mode

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型 Agent 平台架构师")
    assert rec["status"] == "matched"
    assert rec.get("relaxed_by_whitelist") is True
    audit = rec.get("screening_audit") or ""
    assert "direct_only" in audit, "app rule violation must be captured in audit trail"
    assert "大模型" in audit, "matched whitelist token must be captured in audit trail"

    finished = await broker.get_task(task.id)
    assert any("白名单放宽" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_auto_apply_app_rule_rejection_persists_ignored_record():
    """AUTO_APPLY: headhunter job violating direct_only without whitelist rescue
    must persist an ignored record with the violation reason and never reach LLM."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-reject-apply"), driver=_mock_driver())
    llm = _drafting_llm()
    handler = AutoApplyHandler(llm_client=llm)

    posting = _headhunter_posting()
    posting.title = "云原生平台工程师"
    posting.job_description = (
        "负责容器平台与云原生基础设施建设，精通Go与Kubernetes，主导服务网格治理。"
    )

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "平台",
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["量子计算"],
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage"),
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
        patch("boss_agent.feed_pipeline.ChatPage"),
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = posting

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output.get("status") == "filtered_by_app_rule"
    assert result.output.get("applied") is False

    ignored = await broker.job_store.list_job_records(status="ignored")
    assert len(ignored) == 1
    assert "direct_only" in (ignored[0].get("screened_reason") or "")
    assert ignored[0].get("relaxed_by_whitelist") in (False, None)

    # Rejected job must never consume LLM tokens
    llm.chat_completion_json.assert_not_called()


def _drafting_llm():
    llm = MagicMock()
    llm.chat_completion_json.return_value = {
        "match_score": 91,
        "jd_key_requirements": ["大模型平台架构"],
        "match_reasons": ["具备LLM推理链编排实战经验"],
        "greeting_message": "您好，看到贵司大模型Agent平台架构师岗位，我在推理链编排与多智能体落地有成熟经验...",
    }
    return llm


def _commute_posting(
    distance_km: float | None,
    text: str = "",
    title: str = "大模型 Agent 平台架构师",
    recruiter_name: str = "张先生 · 技术总监",
    is_headhunter: bool = False,
) -> JobPosting:
    return JobPosting(
        title=title,
        company_name="某科技有限公司",
        salary_range="40-60K",
        job_description=(
            "主导企业级大模型应用与Agent工作流平台建设，负责推理链编排、"
            "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
        ),
        recruiter_name=recruiter_name,
        is_headhunter=is_headhunter,
        commute_distance_km=distance_km,
        commute_distance_text=text,
    )


def _scrape_card(
    title: str = "云原生平台工程师",
    recruiter_name: str = "张先生 · 技术总监",
    location: str = "上海  浦东新区  张江",
) -> tuple[JobCardBrief, MagicMock]:
    card_elem = MagicMock()
    card = JobCardBrief(
        title=title,
        company_name="某科技有限公司",
        recruiter_name=recruiter_name,
        tags=["K8s"],
        digest="负责容器平台与云原生基础设施建设",
        location=location,
    )
    return card, card_elem


@pytest.mark.asyncio
async def test_scrape_jobs_relaxed_headhunter_card_persists_audit_fields():
    """SCRAPE_JOBS: headhunter card violating direct_only but hitting whitelist is
    rescued, navigated, and persisted with relaxed_by_whitelist + screening_audit."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-relax-scrape"), driver=_mock_driver())
    handler = ScrapeJobsHandler(llm_client=MagicMock())

    card_elem = MagicMock()
    card = JobCardBrief(
        title="大模型技术负责人",
        company_name="某人力资源服务公司",
        recruiter_name="钟先生 · 猎头顾问",
        tags=["LLM"],
        digest="负责大模型应用平台与Agent工作流架构",
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "大模型",
            "max_jobs": 5,
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["大模型"],
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = JobPosting(
            title="大模型技术负责人",
            company_name="某人力资源服务公司",
            salary_range="40-60K",
            job_description="完整职位要求：主导企业级大模型应用与Agent工作流平台建设，负责推理链编排。",
            recruiter_name="钟先生",
            recruiter_title="猎头顾问",
            is_headhunter=True,
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["skipped_count"] == 0
    assert result.output["scraped_count"] == 1
    card_elem.click.assert_called_once()

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型技术负责人")
    assert rec.get("relaxed_by_whitelist") is True
    audit = rec.get("screening_audit") or ""
    assert "direct_only" in audit and "大模型" in audit


@pytest.mark.asyncio
async def test_scrape_jobs_app_rule_violation_without_rescue_is_ignored():
    """SCRAPE_JOBS: headhunter card violating direct_only without whitelist hit is
    short-circuited to an ignored record without opening the detail page."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-reject-scrape"), driver=_mock_driver())
    handler = ScrapeJobsHandler(llm_client=MagicMock())

    card_elem = MagicMock()
    card = JobCardBrief(
        title="云原生平台工程师",
        company_name="某人力资源服务公司",
        recruiter_name="钟先生 · 猎头顾问",
        tags=["K8s"],
        digest="负责容器平台与云原生基础设施建设",
    )

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "平台",
            "max_jobs": 5,
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["量子计算"],
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = JobPosting(
            title="云原生平台工程师",
            company_name="某人力资源服务公司",
            salary_range="30-45K",
            job_description="完整职位要求：负责容器平台与云原生基础设施建设，精通Go语言。",
            recruiter_name="钟先生",
            recruiter_title="猎头顾问",
            is_headhunter=True,
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["skipped_count"] == 1
    assert result.output["scraped_count"] == 0
    card_elem.click.assert_not_called()
    detail_cls.return_value.extract_job_posting.assert_not_called()

    ignored = await broker.job_store.list_job_records(status="ignored")
    assert len(ignored) == 1
    assert "direct_only" in (ignored[0].get("screened_reason") or "")
    assert "direct_only" in (ignored[0].get("screening_audit") or "")


# ---------------------------------------------------------------------------
# Commute distance App-Enforced Filter (spec #209, Ticket #212)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_apply_distant_job_is_ignored_before_chat_entry():
    """A distant job outside the whitelist must never open chat or spend quota."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(
        config=WorkerConfig(worker_id="w-distance-apply"), driver=_mock_driver()
    )
    llm = _drafting_llm()
    handler = AutoApplyHandler(llm_client=llm)

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "大模型",
            "preview_only": False,
            "auto_send": True,
            "screening_policy": {
                "max_commute_distance_km": 40.0,
                "title_whitelist": ["量子计算"],
                # 考察名单 (spec #328): the bottom probe is bought only for a listed
                # district, so these distance verdicts are only earned with one.
                "business_district_inspect_list": ["张江"],
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage"),
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
        patch("boss_agent.feed_pipeline.ChatPage") as chat_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(
            52.0, "距离家庭住址52千米"
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["applied"] is False
    assert result.output["status"] == "filtered_by_app_rule"
    assert "超过通勤上限" in result.output["reason"]

    # Quota protection: chat must never be entered, and no LLM tokens consumed.
    detail_cls.return_value.open_chat.assert_not_called()
    chat_cls.return_value.type_greeting_message.assert_not_called()
    llm.chat_completion_json.assert_not_called()

    ignored = await broker.job_store.list_job_records(status="ignored")
    assert len(ignored) == 1
    assert ignored[0]["commute_distance_km"] == pytest.approx(52.0)
    assert ignored[0]["commute_distance_text"] == "距离家庭住址52千米"
    assert "超过通勤上限" in ignored[0]["screened_reason"]


@pytest.mark.asyncio
async def test_auto_apply_distant_job_is_never_rescued_by_whitelist():
    """Issue #437: a distant job is rejected even when its title hits a passion token.

    通勤距离是硬性过滤条件，白名单放宽不得豁免它。卡片初筛层在这里直接判定
    filtered_by_app_rule，因此投递额度（聊天入口与 LLM token）一分都不会花出去。
    """
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-distance-hard"), driver=_mock_driver())
    llm = _drafting_llm()
    handler = AutoApplyHandler(llm_client=llm)

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "大模型",
            "screening_policy": {"max_commute_distance_km": 40.0, "title_whitelist": ["大模型"]},
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage"),
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
        patch("boss_agent.feed_pipeline.ChatPage") as chat_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(
            52.0, "距离家庭住址52千米"
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["applied"] is False

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型 Agent 平台架构师")
    # The title hits "大模型", but a 52km commute is a hard filter: no exemption.
    assert rec["status"] == "ignored"
    assert rec["relaxed_by_whitelist"] is False
    assert "超过通勤上限" in (rec.get("screening_audit") or "")
    assert "白名单放宽" not in (rec.get("screening_audit") or "")
    assert "超过通勤上限" in (rec.get("screened_reason") or "")
    assert rec["commute_distance_km"] == pytest.approx(52.0)

    # 硬性淘汰发生在进入 JD/投递之前：聊天未开，token 未花。
    detail_cls.return_value.open_chat.assert_not_called()
    chat_cls.return_value.type_greeting_message.assert_not_called()
    llm.chat_completion_json.assert_not_called()


@pytest.mark.asyncio
async def test_auto_apply_nearby_job_proceeds_and_probes_distance():
    """A job inside the ceiling in a 考察名单 district is measured against it.

    Spec #328 moved the probe behind the 考察名单, so "the ceiling is active" is no longer
    by itself a reason to spend the bottom-swipe budget: the operator has to have named
    the district. (A station-only match is a second, later chance at that decision, and is
    exercised at the page seam in ``test_commute_distance_extraction``.)
    """
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-distance-near"), driver=_mock_driver())
    handler = AutoApplyHandler(llm_client=_drafting_llm())

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "大模型",
            "screening_policy": {
                "max_commute_distance_km": 40.0,
                "business_district_inspect_list": ["张江"],
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
        patch("boss_agent.feed_pipeline.ChatPage"),
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        listed_card, card_elem = _scrape_card(title="大模型 Agent 平台架构师")
        listing = _commute_posting(18.5, "距离家庭住址18.5千米")
        listing.location_line = "上海·浦东新区·张江(近13/16号线华夏中路地铁站)"
        listing.metro_station = "华夏中路地铁站"
        listing.metro_lines = "13/16号线"
        detail_cls.return_value.extract_job_posting.return_value = listing
        list_cls.return_value.extract_visible_job_cards.return_value = [
            located(listed_card, card_elem)
        ]
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["applied"] is False
    assert (
        detail_cls.return_value.extract_job_posting.call_args.kwargs["probe_commute_distance"]
        is True
    )

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型 Agent 平台架构师")
    assert rec["status"] == "matched"
    assert rec["commute_distance_km"] == pytest.approx(18.5)
    # Issue #332: the station the detail page read is recorded next to the 商圈.
    assert rec["metro_station"] == "华夏中路地铁站"
    assert rec["location_line"] == "上海·浦东新区·张江(近13/16号线华夏中路地铁站)"


@pytest.mark.asyncio
async def test_auto_apply_in_an_unlisted_district_skips_the_probe_and_defaults_satisfied():
    """The fully accelerated scan (spec #328): nobody asked for this district to be measured.

    The distance the detail page happened to carry is still screened when it is present —
    the gate buys no swipe, it never discards a measurement that was made.
    """
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-distance-near"), driver=_mock_driver())
    handler = AutoApplyHandler(llm_client=_drafting_llm())

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "大模型",
            "screening_policy": {
                "max_commute_distance_km": 40.0,
                "business_district_inspect_list": ["崇明区"],
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
        patch("boss_agent.feed_pipeline.ChatPage"),
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        card, card_elem = _scrape_card(
            title="大模型 Agent 平台架构师", location="上海  浦东新区  张江"
        )
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(
            18.5, "距离家庭住址18.5千米"
        )
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert (
        detail_cls.return_value.extract_job_posting.call_args.kwargs["probe_commute_distance"]
        is False
    )
    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型 Agent 平台架构师")
    assert rec["status"] == "matched"


@pytest.mark.asyncio
async def test_auto_apply_skips_distance_probe_when_filter_disabled():
    """With the ceiling disabled, no bottom scroll latency is paid."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(config=WorkerConfig(worker_id="w-distance-off"), driver=_mock_driver())
    handler = AutoApplyHandler(llm_client=_drafting_llm())

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "大模型",
            "screening_policy": {"max_commute_distance_km": None},
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage"),
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
        patch("boss_agent.feed_pipeline.ChatPage"),
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(None)

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert (
        detail_cls.return_value.extract_job_posting.call_args.kwargs["probe_commute_distance"]
        is False
    )


@pytest.mark.asyncio
async def test_auto_apply_headhunter_target_skips_distance_probe_and_proceeds():
    """Ticket #255: a target known to be a headhunter posting gets no bottom probe,
    and the fail-open distance keeps it eligible for the normal pipeline."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(
        config=WorkerConfig(worker_id="w-distance-apply-hh"), driver=_mock_driver()
    )
    handler = AutoApplyHandler(llm_client=_drafting_llm())

    task = await broker.create_task(
        task_type=TaskType.AUTO_APPLY,
        payload={
            "keyword": "大模型",
            "is_headhunter": True,
            "screening_policy": {"max_commute_distance_km": 40.0},
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage"),
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
        patch("boss_agent.feed_pipeline.ChatPage"),
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        search_cls.return_value.is_search_page.return_value = True
        search_cls.return_value.search.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(
            None, recruiter_name="钟先生 · 猎头顾问", is_headhunter=True
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    probe_kwargs = detail_cls.return_value.extract_job_posting.call_args.kwargs
    assert probe_kwargs["probe_commute_distance"] is False
    assert probe_kwargs["is_headhunter"] is True

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型 Agent 平台架构师")
    assert rec["status"] == "matched"
    assert rec["commute_distance_km"] is None

    finished = await broker.get_task(task.id)
    assert any("猎头岗位（企业信息保密），跳过底部通勤距离探测" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_scrape_jobs_headhunter_card_skips_distance_probe_and_fails_open():
    """Ticket #255: a headhunter card must never pay the bottom-probe cost, and the
    unknown distance it leaves behind must fail open rather than reject the job."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(
        config=WorkerConfig(worker_id="w-distance-scrape-hh"), driver=_mock_driver()
    )
    handler = ScrapeJobsHandler(llm_client=MagicMock())
    card, _card_elem = _scrape_card(recruiter_name="钟先生 · 猎头顾问")
    assert card.is_headhunter is True

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "大模型",
            "max_jobs": 5,
            "screening_policy": {"max_commute_distance_km": 40.0},
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, _card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(
            None, recruiter_name="钟先生 · 猎头顾问", is_headhunter=True
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    probe_kwargs = detail_cls.return_value.extract_job_posting.call_args.kwargs
    assert probe_kwargs["probe_commute_distance"] is False
    assert probe_kwargs["is_headhunter"] is True

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型 Agent 平台架构师")
    assert rec["status"] == "jd_saved"
    assert rec["commute_distance_km"] is None
    assert result.output["scraped_count"] == 1

    finished = await broker.get_task(task.id)
    assert any("猎头岗位（企业信息保密），跳过底部通勤距离探测" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_scrape_jobs_distant_job_is_ingested_as_ignored():
    """A distant job is persisted as ignored instead of jd_saved/unmatched."""
    broker = InMemoryTaskBroker()
    context = WorkerContext(
        config=WorkerConfig(worker_id="w-distance-scrape"), driver=_mock_driver()
    )
    handler = ScrapeJobsHandler(llm_client=MagicMock())
    card, _card_elem = _scrape_card()

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "平台",
            "max_jobs": 5,
            "screening_policy": {
                "max_commute_distance_km": 40.0,
                "title_whitelist": ["量子计算"],
                # 考察名单 (spec #328): the bottom probe is bought only for a listed
                # district, so these distance verdicts are only earned with one.
                "business_district_inspect_list": ["张江"],
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, _card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(
            52.0, "距离家庭住址52千米"
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["scraped_count"] == 0
    assert result.output["skipped_count"] == 1
    assert (
        detail_cls.return_value.extract_job_posting.call_args.kwargs["probe_commute_distance"]
        is True
    )

    ignored = await broker.job_store.list_job_records(status="ignored")
    assert len(ignored) == 1
    assert "超过通勤上限" in (ignored[0].get("screened_reason") or "")
    assert ignored[0]["commute_distance_km"] == pytest.approx(52.0)
    assert ignored[0]["commute_distance_text"] == "距离家庭住址52千米"


@pytest.mark.asyncio
async def test_scrape_jobs_distant_job_is_never_rescued_by_whitelist():
    """Issue #438: 详情页的通勤距离违例是硬性约束，白名单命中也不再豁免。

    The distance widget only renders at the bottom of the detail page, so this stage is
    the one that earns the verdict — and the stage that used to let a whitelist token
    ("云原生") carry a 52km job into the collected records. A hard constraint never
    consults Whitelist Relaxation, so the posting now leaves the collection flow with
    the hard-constraint nature of the verdict spelled out in both audit and log.
    """
    broker = InMemoryTaskBroker()
    context = WorkerContext(
        config=WorkerConfig(worker_id="w-distance-scrape-relax"), driver=_mock_driver()
    )
    handler = ScrapeJobsHandler(llm_client=MagicMock())
    card, _card_elem = _scrape_card()

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "云原生",
            "max_jobs": 5,
            "screening_policy": {"max_commute_distance_km": 40.0, "title_whitelist": ["云原生"]},
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, _card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(
            52.0, "距离家庭住址52千米", title="云原生平台工程师"
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["scraped_count"] == 0
    assert result.output["skipped_count"] == 1

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "云原生平台工程师")
    assert rec["status"] == "ignored"
    assert rec["relaxed_by_whitelist"] is False
    audit = rec.get("screening_audit") or ""
    assert "超过通勤上限" in audit
    assert "硬性约束不可豁免" in audit
    assert "白名单放宽" not in audit
    # Regression guard: the audit is a rendered list, never a stray leading separator.
    assert not audit.startswith("；")
    assert "硬性约束不可豁免" in (rec.get("screened_reason") or "")
    assert rec["commute_distance_km"] == pytest.approx(52.0)

    finished = await broker.get_task(task.id)
    assert any("🛑 [App端强制过滤]" in log and "硬性约束不可豁免" in log for log in finished.logs)


@pytest.mark.asyncio
async def test_scrape_jobs_enriched_title_cannot_rescue_a_distant_job():
    """A token carried only by the detail-page title buys no commute exemption.

    Card titles arrive truncated ("技术专家" where the detail page says
    "大模型技术专家"), so the enriched title used to be the facet the detail-stage
    relaxation judged. Under #438 that judgement is gone: whichever title is in hand,
    an over-ceiling distance is a hard constraint and the posting is dropped.
    """
    broker = InMemoryTaskBroker()
    context = WorkerContext(
        config=WorkerConfig(worker_id="w-distance-scrape-enriched"), driver=_mock_driver()
    )
    handler = ScrapeJobsHandler(llm_client=MagicMock())
    card, _card_elem = _scrape_card(title="技术专家")

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "大模型",
            "max_jobs": 5,
            "screening_policy": {"max_commute_distance_km": 40.0, "title_whitelist": ["大模型"]},
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, _card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = _commute_posting(
            52.0, "距离家庭住址52千米", title="大模型技术专家"
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["scraped_count"] == 0
    assert result.output["skipped_count"] == 1

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "大模型技术专家")
    assert rec["status"] == "ignored"
    assert rec["relaxed_by_whitelist"] is False
    audit = rec.get("screening_audit") or ""
    assert "超过通勤上限" in audit
    assert "硬性约束不可豁免" in audit
    assert "白名单放宽" not in audit


@pytest.mark.asyncio
async def test_scrape_jobs_skips_distance_probe_when_filter_disabled():
    broker = InMemoryTaskBroker()
    context = WorkerContext(
        config=WorkerConfig(worker_id="w-distance-scrape-off"), driver=_mock_driver()
    )
    handler = ScrapeJobsHandler(llm_client=MagicMock())
    card, _card_elem = _scrape_card()

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "平台",
            "max_jobs": 5,
            "screening_policy": {"max_commute_distance_km": 0},
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, _card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = JobPosting(
            title="云原生平台工程师",
            company_name="某科技有限公司",
            salary_range="30-45K",
            job_description="完整职位要求：负责容器平台与云原生基础设施建设，精通Go语言。",
            recruiter_name="张先生",
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert (
        detail_cls.return_value.extract_job_posting.call_args.kwargs["probe_commute_distance"]
        is False
    )
    assert result.output["scraped_count"] == 1


@pytest.mark.asyncio
async def test_scrape_jobs_channel_relaxation_does_not_rescue_a_distant_job():
    """Issue #438: a card rescued on the channel dimension is still dropped on distance.

    Channel preference is soft and commutes with Whitelist Relaxation; the commute
    ceiling is hard and does not. The audit trail has to keep both facts apart, so a
    rescued headhunter card is counted once for the channel and once — separately,
    and unrelaxed — for the distance.
    """
    broker = InMemoryTaskBroker()
    context = WorkerContext(
        config=WorkerConfig(worker_id="w-distance-invariant"), driver=_mock_driver()
    )
    handler = ScrapeJobsHandler(llm_client=MagicMock())
    card, _card_elem = _scrape_card(recruiter_name="钟先生 · 猎头顾问")
    assert card.is_headhunter is True

    task = await broker.create_task(
        task_type=TaskType.SCRAPE_JOBS,
        payload={
            "keyword": "云原生",
            "max_jobs": 5,
            "screening_policy": {
                "channel_preference": "direct_only",
                "title_whitelist": ["云原生"],
                "max_commute_distance_km": 40.0,
            },
        },
    )

    with (
        patch("boss_agent.feed_pipeline.StartupDialogPage") as startup_cls,
        patch("boss_agent.feed_pipeline.JobListPage") as list_cls,
        patch("boss_agent.feed_pipeline.SearchPage") as search_cls,
        patch("boss_agent.feed_pipeline.JobDetailPage") as detail_cls,
    ):
        startup_cls.return_value.is_dialog_present.return_value = False
        list_cls.return_value.extract_visible_job_cards.return_value = [located(card, _card_elem)]
        search_cls.return_value.is_search_page.return_value = True
        detail_cls.return_value.extract_job_posting.return_value = JobPosting(
            title="云原生平台工程师",
            company_name="某科技有限公司",
            salary_range="30-45K",
            job_description="完整职位要求：负责容器平台与云原生基础设施建设，精通Go语言。",
            recruiter_name="钟先生",
            recruiter_title="猎头顾问",
            is_headhunter=True,
            commute_distance_km=52.0,
            commute_distance_text="距离家庭住址52千米",
        )

        result = await handler.handle(task, broker, context)

    assert result.success is True
    assert result.output["scraped_count"] == 0
    assert result.output["skipped_count"] == 1

    records = await broker.job_store.list_job_records()
    rec = next(r for r in records if r.get("title") == "云原生平台工程师")
    assert rec["status"] == "ignored"
    # The record field reflects the final, hard verdict even though one soft
    # relaxation was recorded earlier in the audit trail.
    assert rec["relaxed_by_whitelist"] is False
    audit = rec.get("screening_audit") or ""
    # The soft channel violation keeps its exemption; the hard distance one does not,
    # so exactly one 白名单放宽 is recorded and none of record fields contradicts it.
    assert audit.count("白名单放宽") == 1
    assert "超过通勤上限" in audit
    assert "硬性约束不可豁免" in audit
    assert not audit.startswith("；")
