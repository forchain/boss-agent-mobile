from unittest.mock import MagicMock

from boss_agent.job_entities import JobCardBrief
from boss_agent.screening import (
    CandidateScreener,
    CardScreeningVerdict,
    CardVerdictStage,
    JobVerdictStage,
)
from boss_agent.screening_policy import ScreeningPolicy
from boss_agent.search_entities import SavedSearch


def test_screening_policy_serialization():
    policy = ScreeningPolicy(
        title_whitelist=["Python", "Agent"],
        title_blacklist=["Java", "产品经理"],
        company_blacklist=["某某外包"],
        jd_blacklist=["驻场", "出差"],
        enable_screening=True,
    )
    d = policy.to_dict()
    assert d["title_whitelist"] == ["Python", "Agent"]
    assert d["title_blacklist"] == ["Java", "产品经理"]
    assert d["company_blacklist"] == ["某某外包"]
    assert d["jd_blacklist"] == ["驻场", "出差"]

    restored = ScreeningPolicy.from_dict(d)
    assert restored.title_whitelist == ["Python", "Agent"]
    assert restored.title_blacklist == ["Java", "产品经理"]
    assert restored.enable_screening is True


def test_saved_search_screening_policy_roundtrip():
    data = {
        "id": "search-agent-1",
        "name": "Agent职位搜索",
        "screening_policy": {
            "title_whitelist": ["Agent", "LLM"],
            "title_blacklist": ["Java"],
        },
    }
    s = SavedSearch.from_dict("search-agent-1", data)
    assert s.screening_policy.title_whitelist == ["Agent", "LLM"]
    assert s.screening_policy.title_blacklist == ["Java"]

    dumped = s.to_dict()
    assert dumped["screening_policy"]["title_whitelist"] == ["Agent", "LLM"]


def test_keyword_screener_blacklist_rejection():
    policy = ScreeningPolicy(
        title_blacklist=["Java", "产品经理"],
    )
    card = JobCardBrief(
        title="高级 Java 后端开发工程师",
        company_name="某互联网公司",
        recruiter_name="张三",
    )
    screener = CandidateScreener(llm_client=MagicMock())
    verdict = screener.evaluate_card(card, policy)
    assert verdict.passed is False
    assert verdict.stage is CardVerdictStage.FILTERED_BY_KEYWORD
    assert "Java" in verdict.reason


def test_keyword_screener_company_blacklist_rejection():
    policy = ScreeningPolicy(
        company_blacklist=["软通动力"],
    )
    card = JobCardBrief(
        title="Python开发工程师",
        company_name="北京软通动力信息技术有限公司",
        recruiter_name="李四",
    )
    screener = CandidateScreener(llm_client=MagicMock())
    verdict = screener.evaluate_card(card, policy)
    assert verdict.passed is False
    assert verdict.stage is CardVerdictStage.FILTERED_BY_KEYWORD
    assert "软通动力" in verdict.reason


def test_keyword_screener_whitelist_miss_no_longer_rejects():
    """Whitelist is no longer an inclusion gate (issue #188): a card missing all whitelist
    tokens passes the keyword stage and continues down the pipeline for normal evaluation."""
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "大模型", "Python"],
    )
    card = JobCardBrief(
        title="Go语言云原生架构师",
        company_name="某科技公司",
        recruiter_name="王五",
        tags=["K8s", "Docker"],
    )
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "云原生架构岗位，未触犯任何黑名单"},
        {
            "match_score": 70,
            "greeting_message": "您好，看到贵司在招聘云原生架构师...",
        },
    ]
    screener = CandidateScreener(llm_client=mock_llm)
    jd_text = "岗位职责：负责容器平台与云原生基础设施建设，精通 Go 与 Kubernetes。"
    verdict = screener.evaluate_card(card, policy)
    assert verdict.passed is True
    assert verdict.stage is CardVerdictStage.PASSED
    assert verdict.reason == "通过卡片初筛"

    result = screener.evaluate_job(card, jd_text, policy=policy)

    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED
    assert result.greeting_message != ""


def test_keyword_screener_whitelist_hit_and_pass():
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "大模型", "Python"],
        title_blacklist=["Java"],
    )
    card = JobCardBrief(
        title="资深 AI Agent 架构研发",
        company_name="某独角兽公司",
        recruiter_name="赵六",
        tags=["LangChain", "Multi-Agent"],
    )
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 85,
        "greeting_message": "您好，关注到贵司在招聘AI Agent岗位...",
    }
    screener = CandidateScreener(llm_client=mock_llm)
    jd_text = (
        "岗位职责：负责智能体协同平台架构与大模型自动化体系建设，要求精通Python与Multi-Agent。"
    )
    verdict = screener.evaluate_card(card, policy)
    assert verdict.passed is True
    assert verdict.stage is CardVerdictStage.PASSED
    assert verdict.reason == "通过卡片初筛"

    result = screener.evaluate_job(card, jd_text, policy=policy)

    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED
    assert result.greeting_message != ""


def test_keyword_screener_disabled_policy():
    policy = ScreeningPolicy(
        title_blacklist=["Java"],
        enable_screening=False,
    )
    card = JobCardBrief(
        title="Java架构师",
        company_name="某金融科技",
        recruiter_name="钱七",
    )
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 75,
        "greeting_message": "您好！",
    }
    jd_text = "岗位职责：负责金融科技核心系统架构设计，具备10年以上Java与高并发经验。"
    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)
    assert verdict.passed is True
    assert verdict.stage is CardVerdictStage.PASSED

    result = screener.evaluate_job(card, jd_text, policy=policy)

    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED


def test_jd_semantic_screener_rejects_hidden_blacklist_in_jd():
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "AI"],
        title_blacklist=["销售"],
        jd_blacklist=["Java", "微服务", "驻场"],
    )
    card = JobCardBrief(
        title="AI Agent 高级开发",
        company_name="某某数智科技",
        recruiter_name="孙八",
    )
    jd_text = (
        "【岗位职责】\n"
        "1. 负责企业级电商结算系统与中台微服务建设；\n"
        "2. 熟练掌握 Java、Spring Cloud、JVM 调优与 MyBatis，具备高并发经验。\n"
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "pass": False,
        "reason": "JD正文明确要求熟练掌握Java与微服务架构，触犯黑名单技术栈",
    }

    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)

    result = screener.evaluate_job(card, jd_text, policy=policy)

    assert verdict.passed is True
    assert result.passed is False
    assert result.stage is JobVerdictStage.FILTERED_BY_DEEP_SCREENER
    assert "Java" in result.reason
    # A deep-screen veto is final: no greeting was ever drafted.
    assert result.greeting_message == ""
    mock_llm.chat_completion_json.assert_called_once()


def test_jd_semantic_screener_passes_compliant_jd_and_drafts_greeting():
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "AI"],
        title_blacklist=["销售"],
        jd_blacklist=["Java", "微服务"],
    )
    card = JobCardBrief(
        title="AI Agent 应用研发专家",
        company_name="智元创新",
        recruiter_name="周九",
    )
    jd_text = (
        "【岗位职责】\n"
        "1. 基于 LangGraph 与 LangChain 搭建多智能体协同框架；\n"
        "2. 熟练掌握 Python 与向量检索技术，负责端到端工作流优化。\n"
    )

    mock_llm = MagicMock()
    # First call: JDSemanticScreener; Second call: GreetingDrafter
    mock_llm.chat_completion_json.side_effect = [
        {
            "pass": True,
            "reason": "岗位完全契合Agent方向，未触犯任何黑名单",
        },
        {
            "match_score": 92,
            "jd_key_requirements": ["LangGraph", "Multi-Agent"],
            "match_reasons": ["丰富实战经验"],
            "greeting_message": "您好！看到贵司正在招聘AI Agent专家，我在LangGraph多智能体系统上有成熟落地经验...",
        },
    ]

    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)
    result = screener.evaluate_job(card, jd_text, policy=policy)

    assert verdict.passed is True
    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED
    assert "契合Agent方向" in result.reason
    assert "LangGraph" in result.greeting_message
    assert result.match_score == 92
    assert mock_llm.chat_completion_json.call_count == 2


def test_jd_semantic_screener_empty_jd_fallback():
    policy = ScreeningPolicy(title_whitelist=["Agent"])
    card = JobCardBrief(title="AI Agent研发", company_name="测试公司", recruiter_name="HR")

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 80,
        "greeting_message": "您好！看到贵司职位...",
    }
    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)

    result = screener.evaluate_job(card, "", policy=policy)

    assert verdict.passed is True
    # No JD means no evaluable verdict: reported honestly rather than passed silently.
    assert result.passed is False
    assert result.stage is JobVerdictStage.JD_UNAVAILABLE
    assert "missing or too short" in result.error_message
    assert result.greeting_message == ""
    # The drafting stage was never reached, so no tokens were spent on a greeting.
    mock_llm.chat_completion_json.assert_not_called()


def test_jd_semantic_screener_llm_exception_graceful_fallback():
    policy = ScreeningPolicy(
        title_whitelist=["Agent"],
        jd_blacklist=["Java"],
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("Rate limit exceeded")

    screener = CandidateScreener(llm_client=mock_llm)
    result = screener.evaluate_job(
        card=JobCardBrief(title="Agent 工程师", company_name="某公司", recruiter_name="HR"),
        # Substantive enough to be worth screening: a thin JD never reaches the LLM.
        jd_text="岗位职责：负责大模型算法研发与微调，要求熟悉 Python 与分布式训练框架。",
        policy=policy,
        draft_greeting=False,
    )

    assert result.passed is True
    assert "降级放行" in result.reason


def test_full_lifecycle_screening_with_profile():
    from boss_agent.candidate_entities import CandidateProfile

    profile = CandidateProfile(
        name="李华",
        years_of_experience=7,
        core_skills=["Python", "LangGraph", "Android"],
        target_positions=["AI Agent架构师"],
    )
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "架构"],
        title_blacklist=["销售", "外包"],
        jd_blacklist=["Java", "C++"],
    )
    card = JobCardBrief(
        title="AI Agent 移动端高级架构师",
        company_name="前沿智能",
        recruiter_name="张总监",
        salary_range="40-60K",
    )
    jd_text = (
        "岗位职责：\n负责基于手机端与大模型的 Agent 自动化系统构建，要求精通 Python 与 LangGraph。"
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "完全符合Agent研发要求"},
        {
            "match_score": 95,
            "jd_key_requirements": ["LangGraph端侧落地", "移动端结合"],
            "match_reasons": ["具备7年经验与LangGraph架构经验"],
            "greeting_message": "张总监您好！看到贵司招聘移动端Agent架构师，我具备7年经验且在LangGraph落地有深厚积累...",
        },
    ]

    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)
    result = screener.evaluate_job(card, jd_text, profile, policy)

    assert verdict.passed is True
    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED
    assert result.match_score == 95
    assert "张总" in result.greeting_message
    assert mock_llm.chat_completion_json.call_count == 2


def test_matches_card_keywords_with_digest_blacklist_rejection():
    """Card is rejected if jd_blacklist keyword appears in card digest."""
    policy = ScreeningPolicy(
        title_whitelist=["开发", "工程师"],
        jd_blacklist=["驻场", "外包", "兼职"],
    )
    passed, reason = policy.matches_card_keywords(
        title="Python开发工程师",
        company_name="某信息科技",
        tags=["Python", "全职"],
        digest="此职位需长期在银行客户现场驻场办公，负责系统维护",
    )
    assert passed is False
    assert reason == "命中岗位摘要黑名单关键词: '驻场'"


def test_matches_card_keywords_uncoupled_tags_do_not_trigger_title_blacklist():
    """Card tags do not trigger title_blacklist, preventing false-positive rejection."""
    policy = ScreeningPolicy(
        title_blacklist=["Java", "C++"],
    )
    passed, reason = policy.matches_card_keywords(
        title="高级后台研发工程师",
        company_name="某互联网公司",
        tags=["Java", "SpringCloud", "MySQL"],
        digest="负责核心微服务系统架构",
    )
    assert passed is True
    assert "通过卡片初筛" in reason


def test_matches_card_keywords_tags_do_not_trigger_title_blacklist_regression():
    """Regression test for #350: '资深Agent研发工程师' with tags ['Java', 'Python'] passes when title_blacklist=['Java']."""
    policy = ScreeningPolicy(
        title_blacklist=["Java"],
    )
    passed, reason = policy.matches_card_keywords(
        title="资深Agent研发工程师",
        company_name="智元创新",
        tags=["Java", "Python"],
        digest="负责前沿多智能体协同框架设计与核心落地",
    )
    assert passed is True
    assert "通过卡片初筛" in reason


def test_matches_card_keywords_uncoupled_tags_do_not_trigger_digest_blacklist():
    """Card tags do not trigger jd_blacklist, preventing false-positive rejection."""
    policy = ScreeningPolicy(
        jd_blacklist=["外包", "驻场"],
    )
    passed, reason = policy.matches_card_keywords(
        title="资深Agent研发工程师",
        company_name="某自研科技",
        tags=["外包", "驻场服务"],
        digest="自研核心产品研发，技术挑战大",
    )
    assert passed is True
    assert "通过卡片初筛" in reason


def test_matches_card_keywords_with_digest_whitelist_admission():
    """Generic title passes if whitelist keyword appears in digest."""
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "大模型"],
    )
    passed, reason = policy.matches_card_keywords(
        title="技术专家/TL",
        company_name="某独角兽公司",
        tags=["Python", "分布式"],
        digest="负责构建企业级 LLM Agent 协同平台与工作流引擎",
    )
    assert passed is True
    assert "通过卡片初筛" in reason


def test_evaluate_card_evaluates_digest():
    """Card screening rejects a card based on its digest, before any token is spent."""
    policy = ScreeningPolicy(
        title_whitelist=["Agent"],
        jd_blacklist=["驻场"],
    )
    card = JobCardBrief(
        title="AI Agent研发专家",
        company_name="某科技公司",
        recruiter_name="HR",
        digest="工作地点在客户现场，需要长期驻场支持",
    )
    mock_llm = MagicMock()
    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)
    assert verdict.passed is False
    assert verdict.stage is CardVerdictStage.FILTERED_BY_KEYWORD
    assert "驻场" in verdict.reason
    # A rejected card never reaches the JD stages, so no token is spent.
    mock_llm.chat_completion_json.assert_not_called()


# ----------------------------------------------------------------------------
# App-Enforced Filter node & Whitelist Relaxer routing (Ticket #189, Spec #187)
# ----------------------------------------------------------------------------


def test_card_screening_verdict_declares_relaxation_fields():
    """CardScreeningVerdict must carry the App-Enforced Filter and relaxation facets."""
    fields = CardScreeningVerdict.__dataclass_fields__
    for key in ("app_rule_pass", "app_rule_violation", "relaxed_by_whitelist", "relaxation_reason"):
        assert key in fields, f"missing verdict field: {key}"


def test_app_filter_direct_only_rejects_headhunter_without_whitelist():
    """Headhunter card under direct_only with no whitelist rescue exits at filtered_by_app_rule,
    short-circuiting before any JD fetching or LLM invocation."""
    policy = ScreeningPolicy(
        channel_preference="direct_only",
        jd_blacklist=["外包"],
    )
    card = JobCardBrief(
        title="AI Agent 后端工程师",
        company_name="某人力资源服务公司",
        recruiter_name="钟先生 · 猎头顾问",
    )
    assert card.is_headhunter is True

    mock_llm = MagicMock()
    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)

    # The keyword stage ran and passed; the App-Enforced Filter is what rejected it.
    assert verdict.stage is CardVerdictStage.FILTERED_BY_APP_RULE
    assert verdict.passed is False
    assert verdict.app_rule_pass is False
    assert "direct_only" in verdict.app_rule_violation
    assert verdict.relaxed_by_whitelist is False
    # Rejected jobs must never reach the JD semantic screener / drafter
    mock_llm.chat_completion_json.assert_not_called()


def test_app_filter_headhunter_only_rejects_direct_posting():
    """Direct posting violates headhunter_only and is recorded as filtered_by_app_rule."""
    policy = ScreeningPolicy(channel_preference="headhunter_only")
    card = JobCardBrief(
        title="Agent 平台工程师",
        company_name="智元创新",
        recruiter_name="周先生 · 技术总监",
    )
    assert card.is_headhunter is False

    mock_llm = MagicMock()
    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)
    assert verdict.app_rule_pass is False
    assert "headhunter_only" in verdict.app_rule_violation
    assert verdict.stage is CardVerdictStage.FILTERED_BY_APP_RULE
    mock_llm.chat_completion_json.assert_not_called()


def test_app_filter_violation_rescued_by_whitelist_relaxation():
    """Headhunter card violating direct_only is rescued when card facets hit a whitelist
    token: relaxed_by_whitelist=True, relaxation recorded, pipeline continues to drafter."""
    policy = ScreeningPolicy(
        channel_preference="direct_only",
        title_whitelist=["大模型"],
        jd_blacklist=["外包"],
    )
    card = JobCardBrief(
        title="大模型应用架构师",
        company_name="某人力资源服务公司",
        recruiter_name="林女士 · 资深猎头顾问",
        tags=["LLM"],
    )
    assert card.is_headhunter is True

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "岗位核心是大模型应用架构，未触犯黑名单"},
        {
            "match_score": 88,
            "greeting_message": "您好，看到贵司大模型架构师岗位，我在LLM应用落地有深厚积累...",
        },
    ]
    jd_text = (
        "岗位职责：主导企业级大模型应用与Agent工作流平台建设，负责LLM推理链编排、"
        "向量检索体系优化以及多智能体协同框架的架构设计与落地。"
    )
    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)

    assert verdict.passed is True
    assert verdict.app_rule_pass is False
    assert "direct_only" in verdict.app_rule_violation
    assert verdict.relaxed_by_whitelist is True
    assert "大模型" in verdict.relaxation_reason

    result = screener.evaluate_job(card, jd_text, policy=policy)

    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED
    assert result.greeting_message != ""
    # Rescued job must continue into the LLM stages
    mock_llm.chat_completion_json.assert_called()


def test_app_filter_clean_job_bypasses_relaxation():
    """Non-violated jobs bypass whitelist relaxation entirely, even with a whitelist set."""
    policy = ScreeningPolicy(
        channel_preference="direct_only",
        title_whitelist=["量子计算"],
        jd_blacklist=["驻场"],
    )
    card = JobCardBrief(
        title="Agent 平台工程师",
        company_name="智元创新",
        recruiter_name="周先生 · 技术总监",
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "岗位契合，未触犯黑名单"},
        {"match_score": 90, "greeting_message": "您好，我对贵司Agent平台岗位很感兴趣..."},
    ]
    jd_text = "岗位职责：负责Agent编排平台研发，要求精通Python。"
    screener = CandidateScreener(llm_client=mock_llm)
    verdict = screener.evaluate_card(card, policy)

    assert verdict.app_rule_pass is True
    assert verdict.app_rule_violation == ""
    assert verdict.relaxed_by_whitelist is False
    assert verdict.relaxation_reason == ""

    result = screener.evaluate_job(card, jd_text, policy=policy)

    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED


# ----------------------------------------------------------------------------
# Agent prompt precision (Ticket #190, Spec #187)
# ----------------------------------------------------------------------------


def _extract_system_prompt(mock_llm, call_index: int) -> str:
    """Pull the system message content from the nth chat_completion_json call."""
    messages = mock_llm.chat_completion_json.call_args_list[call_index].args[0]
    system_msgs = [m for m in messages if m.get("role") == "system"]
    assert system_msgs, "expected a system prompt in LLM messages"
    return str(system_msgs[0].get("content", ""))


def test_semantic_screener_prompt_has_zero_whitelist_veto():
    """JD semantic screening prompt must be stripped of the legacy whitelist rejection rule:
    whitelist tokens never appear as judging criteria at the JD stage."""
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "量子计算"],
        jd_blacklist=["Java"],
    )
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {"pass": True, "reason": "未触犯黑名单"}

    screener = CandidateScreener(llm_client=mock_llm)
    result = screener.evaluate_job(
        card=JobCardBrief(
            title="AI Agent 平台工程师", company_name="智元创新", recruiter_name="周先生"
        ),
        jd_text="岗位职责：负责Agent编排平台建设，配合Java数据团队提供接口支持。",
        policy=policy,
        draft_greeting=False,
    )
    assert result.passed is True
    mock_llm.chat_completion_json.assert_called_once()

    system_prompt = _extract_system_prompt(mock_llm, 0)
    assert "白名单目标关键词" not in system_prompt, (
        "legacy whitelist criteria line must be stripped from the JD screening prompt"
    )
    assert "量子计算" not in system_prompt, (
        "whitelist tokens must not leak into the JD screening prompt"
    )
    assert "与目标方向完全无关" not in system_prompt, (
        "legacy whitelist rejection rule must be deleted"
    )
    assert "Java" in system_prompt, "blacklist criteria must remain"
    # Core-vs-secondary-mention distinction guidance stays central to the prompt
    assert "协作" in system_prompt or "背景" in system_prompt
    assert "核心职责" in system_prompt or "主技术栈" in system_prompt


def test_semantic_screener_whitelist_only_policy_passes_without_llm():
    """With no blacklist configured, the screener has zero veto material: it must pass
    the job deterministically without burning an LLM call, whitelist or not."""
    policy = ScreeningPolicy(title_whitelist=["Agent"])
    mock_llm = MagicMock()

    screener = CandidateScreener(llm_client=mock_llm)
    result = screener.evaluate_job(
        card=JobCardBrief(title="云原生平台工程师", company_name="某公司", recruiter_name="招聘者"),
        jd_text="岗位职责：负责云原生容器平台建设，要求精通Go与Kubernetes。",
        policy=policy,
        draft_greeting=False,
    )
    assert result.passed is True
    mock_llm.chat_completion_json.assert_not_called()


def test_greeting_drafter_injects_active_blacklists_into_prompt():
    """The greeting drafter prompt dynamically carries jd/title blacklist tokens as
    negative disqualification constraints, preventing praise of disallowed stacks."""
    policy = ScreeningPolicy(
        jd_blacklist=["Java", "微服务"],
        title_blacklist=["外包"],
    )
    card = JobCardBrief(
        title="AI Agent 平台工程师",
        company_name="智元创新",
        recruiter_name="周先生 · 技术总监",
        tags=["Python"],
    )
    jd_text = (
        "岗位职责：主导多智能体协同平台建设；任职要求：精通Python与LangGraph，"
        "熟悉MySQL等传统后端组件，有微服务治理经验，配合Java中台团队交付接口。"
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "黑名单仅为协作提及，岗位主体是Agent平台"},
        {
            "match_score": 91,
            "jd_key_requirements": ["Multi-Agent编排", "LangGraph落地"],
            "match_reasons": ["具备LangGraph多智能体实战经验"],
            "greeting_message": "周总您好，看到贵司在招Agent平台工程师，我在LangGraph多智能体协同平台有成熟落地经验...",
        },
    ]

    screener = CandidateScreener(llm_client=mock_llm)
    result = screener.evaluate_job(card, jd_text, policy=policy)
    assert result.passed is True
    assert result.stage is JobVerdictStage.PASSED

    # Second LLM call is the greeting drafter
    drafter_prompt = _extract_system_prompt(mock_llm, 1)
    for token in ("Java", "微服务", "外包"):
        assert token in drafter_prompt, f"blacklist token '{token}' missing from drafter prompt"
    assert "严禁" in drafter_prompt
