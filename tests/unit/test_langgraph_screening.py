from boss_agent.graph import run_job_application_graph
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
    assert state["status"] == "keyword_passed"
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
    assert state["status"] == "keyword_passed"
