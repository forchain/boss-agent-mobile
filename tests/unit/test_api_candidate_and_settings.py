"""
tests.unit.test_api_candidate_and_settings
==========================================
Unit and API integration tests for candidate profile broker persistence,
resume upload, LLM settings, and match evaluation sandbox.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from boss_agent.api.app import create_app
from boss_agent.broker.pocketbase_adapter import InMemoryTaskBroker
from boss_agent.matching import MatchGreetingResult
from boss_agent.memory import StructuredCandidateProfile


def test_broker_candidate_profile_crud():
    broker = InMemoryTaskBroker()
    
    profile = {
        "name": "张三",
        "years_of_experience": 5,
        "education": [{"school": "清华大学", "degree": "硕士", "major": "计算机"}],
        "core_skills": ["Python", "LLM", "Android"],
        "project_highlights": [{"name": "AI Agent 系统", "details": "主导开发"}],
        "target_positions": ["Agent 专家"],
        "raw_summary": "5年大模型经验",
    }
    
    import asyncio
    saved = asyncio.run(broker.save_candidate_profile(profile, user_id="test_user"))
    assert saved["name"] == "张三"
    
    retrieved = asyncio.run(broker.get_candidate_profile(user_id="test_user"))
    assert retrieved is not None
    assert retrieved["name"] == "张三"
    assert retrieved["core_skills"] == ["Python", "LLM", "Android"]


def test_candidate_profile_api_endpoints():
    broker = InMemoryTaskBroker()
    app = create_app(broker=broker)
    client = TestClient(app)

    # 1. Get default profile
    resp = client.get("/api/candidate/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "profile" in data

    # 2. Update profile
    update_payload = {
        "name": "李四",
        "years_of_experience": 8,
        "core_skills": ["Golang", "Kubernetes", "PyTorch"],
    }
    resp = client.put("/api/candidate/profile", json=update_payload)
    assert resp.status_code == 200
    updated_data = resp.json()
    assert updated_data["profile"]["name"] == "李四"
    assert updated_data["profile"]["years_of_experience"] == 8
    assert "Golang" in updated_data["profile"]["core_skills"]

    # 3. Verify get reflects updated profile
    resp = client.get("/api/candidate/profile")
    assert resp.status_code == 200
    assert resp.json()["profile"]["name"] == "李四"


def test_candidate_resume_upload_api():
    broker = InMemoryTaskBroker()
    app = create_app(broker=broker)
    client = TestClient(app)

    mock_profile = StructuredCandidateProfile(
        name="王五",
        years_of_experience=10,
        core_skills=["FastAPI", "Appium", "Agent"],
    )

    with patch("boss_agent.memory.ResumeMemoryManager.generate_and_save_memory", return_value=mock_profile):
        files = {"file": ("test_resume.txt", b"dummy content", "text/plain")}
        resp = client.post("/api/candidate/resume", files=files)
        
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["profile"]["name"] == "王五"
        assert data["profile"]["years_of_experience"] == 10


def test_llm_settings_api():
    app = create_app(broker=InMemoryTaskBroker())
    client = TestClient(app)

    # Mock in-memory config store
    in_memory_cfg = {"llm": {"provider": "openai", "model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1", "api_key": "sk-1234567890", "temperature": 0.3}}

    def mock_load():
        return in_memory_cfg

    def mock_save(cfg):
        nonlocal in_memory_cfg
        in_memory_cfg = cfg

    with patch("boss_agent.api.routes._load_llm_config", side_effect=mock_load), \
         patch("boss_agent.api.routes._save_llm_config", side_effect=mock_save):
        
        # Get settings
        resp = client.get("/api/settings/llm")
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "openai"

        # Update settings
        update_payload = {
            "provider": "minimax",
            "model": "abab6.5s-chat",
            "base_url": "https://api.minimax.chat/v1",
            "api_key": "sk-test-minimax-key-12345",
            "temperature": 0.5,
        }
        resp = client.put("/api/settings/llm", json=update_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "minimax"
        assert data["model"] == "abab6.5s-chat"


def test_match_evaluate_sandbox_api():
    broker = InMemoryTaskBroker()
    app = create_app(broker=broker)
    client = TestClient(app)

    mock_match_result = MatchGreetingResult(
        match_score=92,
        jd_key_requirements=["大模型 Agent 落地", "Android 端自动化"],
        match_reasons=["具备5年经验", "主导过类似项目"],
        greeting_message="针对贵司大模型 Agent 落地需求，我有丰富实战经验！",
    )

    with patch("boss_agent.matching.JobMatchGreetingService.evaluate_and_draft_greeting", return_value=mock_match_result):
        req_payload = {
            "job_title": "资深 Agent 研发",
            "company_name": "创新未来",
            "salary_range": "30-50K",
            "job_description": "负责移动端 Agent 研发与大模型架构",
        }
        resp = client.post("/api/match/evaluate", json=req_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["match_score"] == 92
        assert len(data["jd_key_requirements"]) == 2
        assert "移动端" in data["greeting_message"] or "Agent" in data["greeting_message"]


def test_dashboard_page_rendering():
    app = create_app(broker=InMemoryTaskBroker())
    client = TestClient(app)

    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Boss Agent Mobile" in resp.text
    assert "求职者画像" in resp.text
    assert "Task Control Center" in resp.text

