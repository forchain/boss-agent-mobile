"""
tests.unit.test_greeting_prompt
===============================
Unit tests for the single living Greeting Prompt: file precedence loading
(ADR 0010) and prompt composition in the matching service.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent.greeting_prompt import load_greeting_prompt
from boss_agent.matching import JobMatchGreetingService
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.models import JobPosting

SEED_TEXT = "# 默认种子提示词\n严禁模板化套话。"
LOCAL_TEXT = "# 沉淀后的最终记忆\n第一句直击 JD 痛点，并突出海外留学与英文面试意愿。"


def _make_config(tmp_path: Path, seed: str | None = SEED_TEXT, local: str | None = None) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        (cfg / "greeting_prompt.example.md").write_text(seed, encoding="utf-8")
    if local is not None:
        (cfg / "greeting_prompt.local.md").write_text(local, encoding="utf-8")
    return cfg


def test_load_reads_explicit_path_verbatim(tmp_path):
    target = tmp_path / "custom_prompt.md"
    target.write_text(LOCAL_TEXT, encoding="utf-8")
    assert load_greeting_prompt(config_path=target) == LOCAL_TEXT


def test_local_document_wins_over_seed(tmp_path, monkeypatch):
    import boss_agent.greeting_prompt as gp

    _make_config(tmp_path, local=LOCAL_TEXT)
    monkeypatch.setattr(gp, "resolve_git_common_root", lambda: tmp_path)
    assert load_greeting_prompt() == LOCAL_TEXT


def test_empty_local_document_is_honored_not_replaced_by_seed(tmp_path, monkeypatch):
    """Clearing the settled memory must be expressible: an empty local file
    means an empty prompt, never a silent resurrection of the seed."""
    import boss_agent.greeting_prompt as gp

    _make_config(tmp_path, local="")
    monkeypatch.setattr(gp, "resolve_git_common_root", lambda: tmp_path)
    assert load_greeting_prompt() == ""


def test_seed_serves_as_default_before_first_save(tmp_path, monkeypatch):
    import boss_agent.greeting_prompt as gp

    _make_config(tmp_path, seed=SEED_TEXT)
    monkeypatch.setattr(gp, "resolve_git_common_root", lambda: tmp_path)
    assert load_greeting_prompt() == SEED_TEXT


def test_missing_document_fails_loudly(tmp_path, monkeypatch):
    import boss_agent.greeting_prompt as gp

    monkeypatch.setattr(gp, "resolve_git_common_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_greeting_prompt()


def _job() -> JobPosting:
    return JobPosting(
        title="Senior AI Architect",
        company_name="Global Tech",
        salary_range="50-70K",
        job_description="Responsible for global AI Agent architecture, requiring fluent English.",
    )


def test_evaluate_embeds_greeting_prompt_verbatim():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 88,
        "jd_key_requirements": ["英语"],
        "match_reasons": ["海外留学"],
        "greeting_message": "关注到贵司对英语协同的硬性要求。",
    }
    service = JobMatchGreetingService(llm_client=mock_llm)
    service.evaluate_and_draft_greeting(
        job=_job(),
        profile=StructuredCandidateProfile(name="张三", core_skills=["LangGraph 工程化落地"]),
        greeting_prompt=LOCAL_TEXT,
    )
    system_prompt = mock_llm.chat_completion_json.call_args[0][0][0]["content"]
    assert LOCAL_TEXT in system_prompt
    # Structural scaffolding survives alongside the editable document.
    assert "[求职者背景画像]" in system_prompt
    assert "LangGraph 工程化落地" in system_prompt
    assert "JSON" in system_prompt
    # The retired numbered-rules block is gone.
    assert "【打招呼个性化长期偏好准则" not in system_prompt
    assert "【触发条件】" not in system_prompt


def test_refine_uses_same_greeting_prompt_document():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {"revised_greeting": "修订后的招呼语全文。"}
    service = JobMatchGreetingService(llm_client=mock_llm)
    service.refine_with_critique(
        job=_job(),
        current_greeting="旧版招呼语",
        critique="更精炼",
        greeting_prompt=LOCAL_TEXT,
    )
    system_prompt = mock_llm.chat_completion_json.call_args[0][0][0]["content"]
    assert LOCAL_TEXT in system_prompt
    assert "【微调优化特别说明】" in system_prompt


def test_refine_greeting_prompt_rewrites_whole_document_preserving_points():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "prompt": LOCAL_TEXT + "\n7. 【主动给出作品集链接】。"
    }
    service = JobMatchGreetingService(llm_client=mock_llm)
    refined = service.refine_greeting_prompt(
        job=_job(),
        original_greeting="旧版泛泛招呼语",
        revised_greeting="直击英语协同痛点的新版招呼语",
        critique="强调海外留学和英语工作语言",
        current_prompt=LOCAL_TEXT,
    )

    assert refined == LOCAL_TEXT + "\n7. 【主动给出作品集链接】。"

    call_args = mock_llm.chat_completion_json.call_args[0][0]
    system_prompt = call_args[0]["content"]
    # The rewrite must be told to preserve every existing point.
    assert "保留" in system_prompt
    # The rewrite request must carry the current document as its base.
    user_prompt = call_args[1]["content"]
    assert LOCAL_TEXT in user_prompt
    assert "旧版泛泛招呼语" in user_prompt
    assert "直击英语协同痛点的新版招呼语" in user_prompt
    assert "强调海外留学和英语工作语言" in user_prompt
    assert "Senior AI Architect" in user_prompt


def test_refine_greeting_prompt_lazy_loads_current_document(tmp_path, monkeypatch):
    import boss_agent.greeting_prompt as gp

    _make_config(tmp_path, local=LOCAL_TEXT)
    monkeypatch.setattr(gp, "resolve_git_common_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {"prompt": "改进后的完整文档。"}
    service = JobMatchGreetingService(llm_client=mock_llm)
    service.refine_greeting_prompt(
        job=_job(),
        original_greeting="a",
        revised_greeting="b",
        critique="c",
    )
    user_prompt = mock_llm.chat_completion_json.call_args[0][0][1]["content"]
    assert LOCAL_TEXT in user_prompt


def test_refine_greeting_prompt_raises_on_llm_failure():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("simulated rewrite failure")
    service = JobMatchGreetingService(llm_client=mock_llm)
    with pytest.raises(RuntimeError) as exc_info:
        service.refine_greeting_prompt(
            job=_job(),
            original_greeting="a",
            revised_greeting="b",
            critique="c",
            current_prompt=LOCAL_TEXT,
        )
    assert "simulated rewrite failure" in str(exc_info.value)


def test_refine_greeting_prompt_rejects_empty_llm_output():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {"prompt": "   "}
    service = JobMatchGreetingService(llm_client=mock_llm)
    with pytest.raises(RuntimeError):
        service.refine_greeting_prompt(
            job=_job(),
            original_greeting="a",
            revised_greeting="b",
            critique="c",
            current_prompt=LOCAL_TEXT,
        )


def test_evaluate_lazy_loads_prompt_file(tmp_path, monkeypatch):
    import boss_agent.greeting_prompt as gp

    _make_config(tmp_path, local=LOCAL_TEXT)
    monkeypatch.setattr(gp, "resolve_git_common_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "match_score": 70,
        "jd_key_requirements": [],
        "match_reasons": [],
        "greeting_message": "hi",
    }
    service = JobMatchGreetingService(llm_client=mock_llm)
    service.evaluate_and_draft_greeting(job=_job())
    system_prompt = mock_llm.chat_completion_json.call_args[0][0][0]["content"]
    assert LOCAL_TEXT in system_prompt
