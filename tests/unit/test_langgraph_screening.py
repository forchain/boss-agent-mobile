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
    state = run_job_application_graph(card, policy=policy)
    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is True
    assert state["status"] == "deep_screen_passed"
    assert state["keyword_reason"] == "通过卡片初筛"


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
    state = run_job_application_graph(card, policy=policy)
    assert state["keyword_pass"] is True
    assert state["status"] == "deep_screen_passed"


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
    mock_llm.chat_completion_json.assert_called_once()


def test_jd_semantic_screener_passes_compliant_jd():
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
    mock_llm.chat_completion_json.return_value = {
        "pass": True,
        "reason": "岗位完全契合Agent方向，未触犯任何黑名单",
    }

    state = run_job_application_graph(
        card=card,
        policy=policy,
        jd_text=jd_text,
        llm_client=mock_llm,
    )

    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is True
    assert state["status"] == "deep_screen_passed"
    assert "契合Agent方向" in state["deep_screen_reason"]


def test_jd_semantic_screener_empty_jd_fallback():
    policy = ScreeningPolicy(title_whitelist=["Agent"])
    card = JobCardBrief(title="AI Agent研发", company_name="测试公司", recruiter_name="HR")

    mock_llm = MagicMock()
    state = run_job_application_graph(
        card=card,
        policy=policy,
        jd_text="",
        llm_client=mock_llm,
    )

    assert state["keyword_pass"] is True
    assert state["deep_screen_pass"] is True
    assert "跳过语义精筛" in state["deep_screen_reason"]
    mock_llm.chat_completion_json.assert_not_called()


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

