from unittest.mock import MagicMock

from boss_agent.graph import JDSemanticScreenerAgent, run_job_application_graph
from boss_agent.models import SavedSearch, ScreeningPolicy
from boss_agent.pages import JobCardBrief


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
    state = run_job_application_graph(card, policy=policy)
    assert state["keyword_pass"] is False
    assert state["status"] == "filtered_by_keyword"
    assert "Java" in state["keyword_reason"]


def test_keyword_screener_company_blacklist_rejection():
    policy = ScreeningPolicy(
        company_blacklist=["软通动力"],
    )
    card = JobCardBrief(
        title="Python开发工程师",
        company_name="北京软通动力信息技术有限公司",
        recruiter_name="李四",
    )
    state = run_job_application_graph(card, policy=policy)
    assert state["keyword_pass"] is False
    assert state["status"] == "filtered_by_keyword"
    assert "软通动力" in state["keyword_reason"]


def test_keyword_screener_whitelist_not_hit():
    policy = ScreeningPolicy(
        title_whitelist=["Agent", "大模型", "Python"],
    )
    card = JobCardBrief(
        title="Go语言云原生架构师",
        company_name="某科技公司",
        recruiter_name="王五",
        tags=["K8s", "Docker"],
    )
    state = run_job_application_graph(card, policy=policy)
    assert state["keyword_pass"] is False
    assert state["status"] == "filtered_by_keyword"
    assert "未命中任何职位白名单" in state["keyword_reason"]


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
    state = run_job_application_graph(card, policy=policy, llm_client=mock_llm)
    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is True
    assert state["status"] == "greeting_drafted"
    assert state["keyword_reason"] == "通过卡片初筛"
    assert state["greeting_message"] != ""


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
    state = run_job_application_graph(card, policy=policy, llm_client=mock_llm)
    assert state["keyword_pass"] is True
    assert state["status"] == "greeting_drafted"


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

    state = run_job_application_graph(
        card=card,
        policy=policy,
        jd_text=jd_text,
        llm_client=mock_llm,
    )

    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is False
    assert state["status"] == "filtered_by_deep_screener"
    assert "Java" in state["deep_screen_reason"]
    # Verify greeting_drafter was NOT called
    assert "greeting_message" not in state or not state["greeting_message"]
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

    state = run_job_application_graph(
        card=card,
        policy=policy,
        jd_text=jd_text,
        llm_client=mock_llm,
    )

    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is True
    assert state["status"] == "greeting_drafted"
    assert "契合Agent方向" in state["deep_screen_reason"]
    assert "LangGraph" in state["greeting_message"]
    assert state["match_score"] == 92
    assert mock_llm.chat_completion_json.call_count == 2


def test_jd_semantic_screener_empty_jd_fallback():
    policy = ScreeningPolicy(title_whitelist=["Agent"])
    card = JobCardBrief(title="AI Agent研发", company_name="测试公司", recruiter_name="HR")

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 80,
        "greeting_message": "您好！看到贵司职位...",
    }
    state = run_job_application_graph(
        card=card,
        policy=policy,
        jd_text="",
        llm_client=mock_llm,
    )

    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is True
    assert "跳过语义精筛" in state["deep_screen_reason"]
    assert state["status"] == "greeting_drafted"


def test_jd_semantic_screener_llm_exception_graceful_fallback():
    policy = ScreeningPolicy(
        title_whitelist=["Agent"],
        jd_blacklist=["Java"],
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("Rate limit exceeded")

    agent = JDSemanticScreenerAgent(llm_client=mock_llm)
    passed, reason = agent.evaluate(
        jd_text="要求熟悉大模型研发与微调",
        card_title="Agent 工程师",
        policy=policy,
    )

    assert passed is True
    assert "降级放行" in reason


def test_full_lifecycle_job_application_graph_with_profile():
    from boss_agent.memory import StructuredCandidateProfile

    profile = StructuredCandidateProfile(
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
        "岗位职责：\n"
        "负责基于手机端与大模型的 Agent 自动化系统构建，要求精通 Python 与 LangGraph。"
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

    state = run_job_application_graph(
        card=card,
        policy=policy,
        jd_text=jd_text,
        candidate_profile=profile,
        llm_client=mock_llm,
    )

    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is True
    assert state["status"] == "greeting_drafted"
    assert state["match_score"] == 95
    assert "张总监" in state["greeting_message"]
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
    assert "驻场" in reason


def test_matches_card_keywords_with_tags_blacklist_rejection():
    """Card is rejected if title_blacklist keyword appears in tags."""
    policy = ScreeningPolicy(
        title_blacklist=["Java", "C++"],
    )
    passed, reason = policy.matches_card_keywords(
        title="高级后台研发工程师",
        company_name="某互联网公司",
        tags=["Java", "SpringCloud", "MySQL"],
        digest="负责核心微服务系统架构",
    )
    assert passed is False
    assert "Java" in reason


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


def test_keyword_screener_in_graph_evaluates_digest():
    """LangGraph keyword_screener node rejects card based on digest and stops graph execution."""
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
    state = run_job_application_graph(card=card, policy=policy, llm_client=mock_llm)
    assert state["keyword_pass"] is False
    assert state["status"] == "filtered_by_keyword"
    assert "驻场" in state["keyword_reason"]
    # LLM should never be called when card fails keyword screener
    mock_llm.chat_completion_json.assert_not_called()



