"""
tests.unit.test_screening_critique
==================================
Unit tests for screening critique retest and living screening prompt refinement
(never-fabricate contract, ADR 0010, Spec #340).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from boss_agent.job_entities import JobPosting
from boss_agent.screening import CandidateScreener, ScreeningVerdict
from boss_agent.screening_policy import ScreeningPolicy


def _sample_job() -> JobPosting:
    return JobPosting(
        title="Agent 开发工程师",
        company_name="智元未来",
        salary_range="30-50K",
        job_description=(
            "负责大语言模型多智能体架构落地，基于 LangGraph 构建 Agent 工作流。"
            "负责后端服务接口与微服务中间件对接，要求掌握 Python/FastAPI。"
        ),
    )


def test_retest_with_critique_approves_when_llm_approves():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": True,
        "reason": "【合格保留】复合工种正常落地偏向，未命中黑名单",
    }

    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()
    critique = "该岗位主体是Agent开发，后端职责是正常落地所需，请予放行。"
    policy = ScreeningPolicy(enable_screening=True, jd_blacklist=["销售", "外包"])

    verdict = screener.retest_with_critique(
        job=job,
        critique=critique,
        policy=policy,
    )

    assert isinstance(verdict, ScreeningVerdict)
    assert verdict.approved is True
    assert "合格保留" in verdict.reason

    call_args = mock_llm.chat_completion_json.call_args[0][0]
    user_prompt = call_args[-1]["content"]
    assert critique in user_prompt
    assert "Agent 开发工程师" in user_prompt


def test_retest_with_critique_rejects_when_llm_rejects():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": False,
        "reason": "【淘汰：命中外包驻场黑名单】",
    }

    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()
    critique = "请复核"

    verdict = screener.retest_with_critique(
        job=job,
        critique=critique,
    )

    assert verdict.approved is False
    assert "命中外包驻场黑名单" in verdict.reason


def test_retest_with_critique_raises_on_llm_failure():
    """Never fabricate a verdict when LLM fails (ADR 0010 contract)."""
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("simulated network timeout")

    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()

    with pytest.raises(RuntimeError) as exc_info:
        screener.retest_with_critique(
            job=job,
            critique="请放行",
        )

    assert "LLM 纠偏重测失败" in str(exc_info.value)
    assert "simulated network timeout" in str(exc_info.value)


def test_refine_screening_prompt_success():
    mock_llm = MagicMock()
    refined_text = "# 修订后的精筛准则\n新增：复合技术工种正常落地偏向不予淘汰。"
    mock_llm.chat_completion_json.return_value = {"prompt": refined_text}

    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()

    result = screener.refine_screening_prompt(
        job=job,
        critique="不要因为写了后端接口就淘汰Agent岗位",
        original_verdict="淘汰 (误判为后端岗位)",
        revised_verdict="合格保留",
        current_prompt="# 初始准则",
    )

    assert result == refined_text
    call_args = mock_llm.chat_completion_json.call_args[0][0]
    user_prompt = call_args[-1]["content"]
    assert "不要因为写了后端接口就淘汰Agent岗位" in user_prompt
    assert "淘汰 (误判为后端岗位)" in user_prompt
    assert "# 初始准则" in user_prompt


def test_refine_screening_prompt_raises_on_empty_or_failure():
    """Never fabricate a prompt rewrite on failure or empty return."""
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {"prompt": ""}

    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()

    with pytest.raises(RuntimeError) as exc_info:
        screener.refine_screening_prompt(
            job=job,
            critique="纠偏意见",
            current_prompt="# 初始准则",
        )

    assert "提示词打磨失败" in str(exc_info.value)


def test_cli_retest_stdout_json(capsys):
    import scripts.refine_screening as script

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": True,
        "reason": "【合格保留】重测通过",
    }

    job_json = json.dumps(
        {
            "job_title": "Agent 开发工程师",
            "company_name": "智元未来",
            "job_description": "大模型智能体架构落地与系统接口开发",
        }
    )

    test_args = [
        "refine_screening.py",
        "--action",
        "retest",
        "--job",
        job_json,
        "--critique",
        "复合工种应予放行",
    ]

    with (
        patch("sys.argv", test_args),
        patch.object(script, "build_llm_client", return_value=mock_llm),
    ):
        script.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["success"] is True
    assert payload["approved"] is True
    assert "合格保留" in payload["reason"]
