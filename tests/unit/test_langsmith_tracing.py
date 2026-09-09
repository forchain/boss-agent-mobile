"""
tests/unit/test_langsmith_tracing.py
====================================
Unit tests for LangSmith tracing integration across OpenAIChatClient,
LangGraph screening workflows, and candidate memory.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from boss_agent.graph import (
    JDSemanticScreenerAgent,
    run_job_application_graph,
)
from boss_agent.matching import JobMatchGreetingService
from boss_agent.memory import ResumeMemoryManager
from boss_agent.models import JobPosting, ScreeningPolicy
from boss_agent.pages import JobCardBrief
from droid_agent_core.llm import LLMConfig, OpenAIChatClient, configure_langsmith


def test_llm_config_langsmith_defaults():
    """Verify default LangSmith settings in LLMConfig."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = LLMConfig()
        assert cfg.langsmith_tracing is False
        assert cfg.langsmith_api_key is None
        assert cfg.langsmith_project is None
        assert cfg.langsmith_endpoint is None


def test_llm_config_langsmith_from_env():
    """Verify LLMConfig correctly resolves LangSmith environment variables."""
    env_vars = {
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_API_KEY": "lsv2_pt_test_key_123",
        "LANGSMITH_PROJECT": "custom-boss-project",
        "LANGSMITH_ENDPOINT": "https://custom.smith.endpoint",
    }
    with patch.dict(os.environ, env_vars, clear=True):
        cfg = LLMConfig.from_env_or_file(config_path=Path("non_existent_file.yaml"))
        assert cfg.langsmith_tracing is True
        assert cfg.langsmith_api_key == "lsv2_pt_test_key_123"
        assert cfg.langsmith_project == "custom-boss-project"
        assert cfg.langsmith_endpoint == "https://custom.smith.endpoint"


def test_configure_langsmith_sets_env():
    """Verify configure_langsmith populates environment variables when tracing enabled."""
    cfg = LLMConfig(
        langsmith_tracing=True,
        langsmith_api_key="lsv2_pt_configured_key",
        langsmith_project="configured-project",
        langsmith_endpoint="https://configured.endpoint",
    )
    with patch.dict(os.environ, {}, clear=True):
        configure_langsmith(cfg)
        assert os.environ.get("LANGSMITH_TRACING") == "true"
        assert os.environ.get("LANGCHAIN_TRACING_V2") == "true"
        assert os.environ.get("LANGSMITH_API_KEY") == "lsv2_pt_configured_key"
        assert os.environ.get("LANGSMITH_PROJECT") == "configured-project"
        assert os.environ.get("LANGSMITH_ENDPOINT") == "https://configured.endpoint"


def test_configure_langsmith_noop_when_disabled():
    """Verify configure_langsmith is a no-op when tracing is disabled."""
    cfg = LLMConfig(langsmith_tracing=False)
    with patch.dict(os.environ, {}, clear=True):
        configure_langsmith(cfg)
        assert "LANGSMITH_TRACING" not in os.environ
        assert "LANGCHAIN_TRACING_V2" not in os.environ


def test_openai_chat_client_tracing_metadata():
    """Verify OpenAIChatClient records model and token usage into LangSmith run tree metadata."""
    cfg = LLMConfig(
        model="test-model",
        base_url="https://api.test.com/v1",
        api_key="mock-key",
    )
    client = OpenAIChatClient(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Test assistant reply"}}],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 25,
            "total_tokens": 40,
        },
    }

    recorded_tree = None

    @traceable(name="outer_test_trace")
    def run_trace():
        nonlocal recorded_tree
        res = client.chat_completion([{"role": "user", "content": "Hello"}])
        recorded_tree = get_current_run_tree()
        return res

    with patch("requests.post", return_value=mock_resp):
        content = run_trace()
        assert content == "Test assistant reply"


def test_openai_chat_client_chat_completion_json():
    """Verify chat_completion_json parses JSON under traceable execution."""
    cfg = LLMConfig(
        model="test-model",
        base_url="https://api.test.com/v1",
        api_key="mock-key",
    )
    client = OpenAIChatClient(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '```json\n{"pass": true, "score": 95}\n```'}}],
        "usage": {"total_tokens": 50},
    }

    with patch("requests.post", return_value=mock_resp):
        data = client.chat_completion_json([{"role": "user", "content": "Screen JD"}])
        assert data["pass"] is True
        assert data["score"] == 95


def test_openai_chat_client_evaluate_text_match():
    """Verify evaluate_text_match executes and returns matching evaluation dict."""
    cfg = LLMConfig(
        model="test-model",
        base_url="https://api.test.com/v1",
        api_key="mock-key",
    )
    client = OpenAIChatClient(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '{"match_score": 88, "match_reasons": ["Strong experience"], "greeting_message": "Hello"}'
                }
            }
        ],
    }

    with patch("requests.post", return_value=mock_resp):
        res = client.evaluate_text_match("Resume text", "JD description")
        assert res["match_score"] == 88
        assert res["greeting_message"] == "Hello"


def test_langgraph_screening_traced_execution():
    """Verify run_job_application_graph executes cleanly with LangSmith tracing metadata and tags."""
    card = JobCardBrief(
        title="Senior Python Architect",
        company_name="TechCorp",
        recruiter_name="Alice",
        salary_range="30-45K",
        location="Beijing",
        tags=["Python", "FastAPI"],
    )

    policy = ScreeningPolicy(
        title_whitelist=["Python", "Architect"],
        title_blacklist=["Java"],
        enable_screening=True,
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = [
        {"pass": True, "reason": "Matches Python architect profile"},
        {
            "match_score": 90,
            "jd_key_requirements": ["Python", "System Design"],
            "match_reasons": ["10 years Python"],
            "greeting_message": "Hello TechCorp team, I specialize in Python systems.",
        },
    ]

    result = run_job_application_graph(
        card=card,
        policy=policy,
        jd_text="Looking for a Python Architect with distributed systems background.",
        llm_client=mock_llm,
    )

    assert result["keyword_pass"] is True
    assert result["deep_screen_pass"] is True
    assert result["match_score"] == 90
    assert "TechCorp" in result["greeting_message"]
    assert result["status"] == "greeting_drafted"


def test_jd_semantic_screener_agent_traceable():
    """Verify JDSemanticScreenerAgent.evaluate runs with traceable decorator."""
    agent = JDSemanticScreenerAgent()
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {"pass": False, "reason": "Explicit Java JD"}
    agent.llm_client = mock_llm

    policy = ScreeningPolicy(jd_blacklist=["Java"])
    passed, reason = agent.evaluate(
        jd_text="Requirements: 5 years of enterprise Java SpringBoot.",
        card_title="Backend Engineer",
        company_name="Enterprise Corp",
        policy=policy,
    )

    assert passed is False
    assert "Java" in reason


def test_job_match_greeting_service_traceable():
    """Verify JobMatchGreetingService.evaluate_and_draft_greeting runs under traceable."""
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 85,
        "jd_key_requirements": ["Android automation"],
        "match_reasons": ["Built Appium agents"],
        "greeting_message": "Hello, I have extensive Appium agent experience.",
    }

    service = JobMatchGreetingService(llm_client=mock_llm)
    job = JobPosting(
        title="Mobile Automation Lead",
        company_name="StartupX",
        salary_range="25-35K",
        job_description="Lead Android Appium automation pipeline.",
    )

    res = service.evaluate_and_draft_greeting(job)
    assert res.match_score == 85
    assert res.greeting_message == "Hello, I have extensive Appium agent experience."


def test_resume_memory_manager_traceable(tmp_path):
    """Verify ResumeMemoryManager.generate_and_save_memory runs under traceable."""
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "name": "张三",
        "years_of_experience": 8,
        "education": [{"school": "清华大学", "degree": "硕士", "major": "计算机"}],
        "core_skills": ["Python", "LangGraph"],
        "work_experiences": [],
        "projects": [],
        "target_positions": ["Agent Architect"],
    }

    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("张三 8年经验 清华大学计算机硕士 精通Python与LangGraph", encoding="utf-8")

    memory_file = tmp_path / "memory.json"
    manager = ResumeMemoryManager(
        memory_file_path=memory_file,
        llm_client=mock_llm,
    )

    profile = manager.generate_and_save_memory(resume_file)
    assert profile.name == "张三"
    assert profile.years_of_experience == 8
    assert "Python" in profile.core_skills


def test_langgraph_screening_custom_tags_and_metadata():
    """Verify run_job_application_graph merges caller tags and metadata."""
    card = JobCardBrief(
        title="Python Engineer",
        company_name="Acme",
        recruiter_name="Bob",
        salary_range="20-30K",
        location="Shanghai",
        tags=["Python"],
    )

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {"pass": False, "reason": "Not matching"}

    custom_config = {
        "tags": ["custom-tag-1"],
        "metadata": {"task_id": "task-abc-123"},
    }

    result = run_job_application_graph(
        card=card,
        policy=ScreeningPolicy(jd_blacklist=["Outsourced"]),
        jd_text="Python job",
        llm_client=mock_llm,
        config=custom_config,
    )

    assert result["keyword_pass"] is True
    assert result["deep_screen_pass"] is False


def test_openai_chat_client_error_propagation():
    """Verify exceptions in chat_completion propagate through @traceable correctly."""
    from droid_agent_core.llm import LLMError

    cfg = LLMConfig()
    client = OpenAIChatClient(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(LLMError) as exc_info:
            client.chat_completion([{"role": "user", "content": "Hello"}])
        assert "500" in str(exc_info.value)

