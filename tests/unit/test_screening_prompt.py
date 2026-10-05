"""
tests.unit.test_screening_prompt
================================
Unit tests for the single living Screening Prompt: file precedence loading
(ADR 0010, Spec #340) and prompt composition in CandidateScreener.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent.job_entities import JobCardBrief
from boss_agent.screening import CandidateScreener, JobVerdictStage
from boss_agent.screening_policy import ScreeningPolicy
from boss_agent.screening_prompt import load_screening_prompt

SEED_TEXT = "# 默认种子精筛提示词\n严禁臆造黑名单外淘汰条件。"
LOCAL_TEXT = "# 沉淀后的最终精筛记忆\n复合技术工种正常落地偏向不予淘汰。"


def _make_config(tmp_path: Path, seed: str | None = SEED_TEXT, local: str | None = None) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        (cfg / "screening_prompt.example.md").write_text(seed, encoding="utf-8")
    if local is not None:
        (cfg / "screening_prompt.local.md").write_text(local, encoding="utf-8")
    return cfg


def test_load_reads_explicit_path_verbatim(tmp_path):
    target = tmp_path / "custom_screening_prompt.md"
    target.write_text(LOCAL_TEXT, encoding="utf-8")
    assert load_screening_prompt(config_path=target) == LOCAL_TEXT


def test_local_document_wins_over_seed(tmp_path, monkeypatch):
    import boss_agent.screening_prompt as sp

    _make_config(tmp_path, local=LOCAL_TEXT)
    monkeypatch.setattr(sp, "resolve_git_common_root", lambda: tmp_path)
    assert load_screening_prompt() == LOCAL_TEXT


def test_empty_local_document_is_honored_not_replaced_by_seed(tmp_path, monkeypatch):
    """Clearing the settled memory must be expressible: an empty local file
    means an empty prompt, never a silent resurrection of the seed."""
    import boss_agent.screening_prompt as sp

    _make_config(tmp_path, local="")
    monkeypatch.setattr(sp, "resolve_git_common_root", lambda: tmp_path)
    assert load_screening_prompt() == ""


def test_seed_serves_as_default_before_first_save(tmp_path, monkeypatch):
    import boss_agent.screening_prompt as sp

    _make_config(tmp_path, seed=SEED_TEXT)
    monkeypatch.setattr(sp, "resolve_git_common_root", lambda: tmp_path)
    assert load_screening_prompt() == SEED_TEXT


def test_stray_cwd_local_never_outranks_shared_root_document(tmp_path, monkeypatch):
    """A stray local document dropped in some unrelated working directory must
    not shadow the shared-root document the Settings UI reads and writes."""
    import boss_agent.screening_prompt as sp

    root = tmp_path / "root"
    _make_config(root, seed=SEED_TEXT)
    stray_cwd = tmp_path / "stray-worktree"
    _make_config(stray_cwd, seed="其他精筛种子", local="散落的本地精筛文档")
    monkeypatch.setattr(sp, "resolve_git_common_root", lambda: root)
    monkeypatch.chdir(stray_cwd)
    assert load_screening_prompt() == SEED_TEXT


def test_shared_root_local_wins_over_cwd_documents(tmp_path, monkeypatch):
    import boss_agent.screening_prompt as sp

    root = tmp_path / "root"
    _make_config(root, seed=SEED_TEXT, local=LOCAL_TEXT)
    stray_cwd = tmp_path / "other-worktree"
    _make_config(stray_cwd, local="别的 worktree 的本地精筛文档")
    monkeypatch.setattr(sp, "resolve_git_common_root", lambda: root)
    monkeypatch.chdir(stray_cwd)
    assert load_screening_prompt() == LOCAL_TEXT


def test_missing_document_fails_loudly(tmp_path, monkeypatch):
    import boss_agent.screening_prompt as sp

    monkeypatch.setattr(sp, "resolve_git_common_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_screening_prompt()


def test_screener_uses_injected_screening_prompt():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": True,
        "reason": "【合格保留】未触犯黑名单",
    }
    custom_prompt = "这是专属测试的精筛提示词"
    screener = CandidateScreener(llm_client=mock_llm, screening_prompt=custom_prompt)

    card = JobCardBrief(
        title="Agent 开发工程师",
        company_name="智能未来",
        recruiter_name="李总",
        tags=["Python", "Agent"],
    )
    jd_text = (
        "负责基于 LLM 的 Agent 系统核心架构设计与工程落地，"
        "同时负责后端业务逻辑开发与高性能接口对接，确保系统高并发稳定运行。"
    )
    policy = ScreeningPolicy(enable_screening=True, jd_blacklist=["销售", "外包"])

    res = screener.evaluate_job(card=card, jd_text=jd_text, policy=policy, draft_greeting=False)

    assert res.passed is True
    assert res.stage is JobVerdictStage.PASSED

    # Verify custom_prompt was embedded in system prompt passed to LLM
    call_args = mock_llm.chat_completion_json.call_args[0][0]
    system_prompt = call_args[0]["content"]
    assert custom_prompt in system_prompt
    assert "销售" in system_prompt
    assert "外包" in system_prompt


def test_screener_loads_living_screening_prompt_from_config(tmp_path, monkeypatch):
    import boss_agent.screening_prompt as sp

    _make_config(tmp_path, local="【本地沉淀规则】：复合技术工种正常落地偏向不予淘汰")
    monkeypatch.setattr(sp, "resolve_git_common_root", lambda: tmp_path)

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": True,
        "reason": "【合格保留】符合复合工种准则",
    }
    screener = CandidateScreener(llm_client=mock_llm)

    card = JobCardBrief(
        title="AI Agent 平台架构师",
        company_name="云端智元",
        recruiter_name="张总",
        tags=["Python", "FastAPI"],
    )
    jd_text = (
        "负责基于大语言模型的多智能体协同编排平台研发，"
        "同时承担后端微服务中间件开发、核心业务逻辑与高性能接口对接。"
    )
    policy = ScreeningPolicy(enable_screening=True, jd_blacklist=["纯Java业务"])

    res = screener.evaluate_job(card=card, jd_text=jd_text, policy=policy, draft_greeting=False)

    assert res.passed is True
    call_args = mock_llm.chat_completion_json.call_args[0][0]
    system_prompt = call_args[0]["content"]
    assert "【本地沉淀规则】：复合技术工种正常落地偏向不予淘汰" in system_prompt


def test_baseline_seed_prompt_contains_composite_and_blacklist_iron_laws():
    """Verify that the default seed prompt explicitly solidifies the two iron laws
    preventing over-rejection of composite technical roles (e.g. Agent + Backend duties)."""
    seed_prompt = load_screening_prompt()
    assert "绝对禁止臆造黑名单外淘汰条件" in seed_prompt
    assert (
        "复合技术工种正常落地偏向严禁误杀" in seed_prompt
        or "复合技术工种正常落地偏向" in seed_prompt
    )
    assert "白名单零否决权" in seed_prompt
