"""
tests.unit.test_screening_evaluate
==================================
Unit tests for single-job manual deep screening evaluation without critique bias
(ADR 0010, Spec #346, Issue #347).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from boss_agent.models import JobPosting, ScreeningPolicy
from boss_agent.screening import CandidateScreener, ScreeningVerdict


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


def test_evaluate_jd_approves_when_llm_approves():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": True,
        "reason": "【合格保留】复合工种正常落地偏向，未命中黑名单",
    }

    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()
    policy = ScreeningPolicy(enable_screening=True, jd_blacklist=["销售", "纯外包"])

    verdict = screener.evaluate_jd(
        job=job,
        policy=policy,
    )

    assert isinstance(verdict, ScreeningVerdict)
    assert verdict.approved is True
    assert "合格保留" in verdict.reason
    assert verdict.stage == "passed"

    call_args = mock_llm.chat_completion_json.call_args[0][0]
    user_prompt = call_args[-1]["content"]
    system_prompt = call_args[0]["content"]

    # Must NOT contain critique bias
    assert "用户纠偏" not in user_prompt
    assert "用户纠偏" not in system_prompt
    assert "Agent 开发工程师" in user_prompt


def test_evaluate_jd_rejects_when_llm_rejects():
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": False,
        "reason": "【淘汰：命中Java微服务架构黑名单】",
    }

    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()
    policy = ScreeningPolicy(enable_screening=True, jd_blacklist=["Java微服务"])

    verdict = screener.evaluate_jd(
        job=job,
        policy=policy,
    )

    assert verdict.approved is False
    assert "命中Java微服务架构黑名单" in verdict.reason
    assert verdict.stage == "filtered_by_deep_screener"


def test_evaluate_jd_unusable_short_jd():
    mock_llm = MagicMock()
    screener = CandidateScreener(llm_client=mock_llm)
    short_job = JobPosting(
        title="开发",
        company_name="某公司",
        salary_range="10-20K",
        job_description="太短",
    )

    verdict = screener.evaluate_jd(job=short_job)

    assert verdict.approved is False
    assert verdict.stage == "jd_unavailable"
    assert "too short" in verdict.reason
    assert "2 chars" in verdict.reason
    # LLM should never be called for an unusable JD
    mock_llm.chat_completion_json.assert_not_called()


def test_evaluate_jd_no_blacklist():
    mock_llm = MagicMock()
    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()
    policy = ScreeningPolicy(enable_screening=True, jd_blacklist=[])

    verdict = screener.evaluate_jd(job=job, policy=policy)

    assert verdict.approved is True
    assert verdict.stage == "passed"
    assert "默认放行" in verdict.reason
    mock_llm.chat_completion_json.assert_not_called()


def test_evaluate_jd_raises_on_llm_failure():
    """Never fabricate a verdict when LLM fails (ADR 0010 contract)."""
    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("simulated gateway timeout")

    screener = CandidateScreener(llm_client=mock_llm)
    job = _sample_job()
    policy = ScreeningPolicy(enable_screening=True, jd_blacklist=["Java"])

    with pytest.raises(RuntimeError) as exc_info:
        screener.evaluate_jd(
            job=job,
            policy=policy,
        )

    assert "LLM 客观精筛评估失败" in str(exc_info.value)
    assert "simulated gateway timeout" in str(exc_info.value)


def test_screener_alias_private_evaluate_jd():
    """CandidateScreener._evaluate_jd is an alias to evaluate_jd."""
    screener = CandidateScreener()
    assert hasattr(screener, "_evaluate_jd")
    assert screener._evaluate_jd == screener.evaluate_jd


def test_cli_evaluate_stdout_json(capsys):
    import scripts.refine_screening as script

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": True,
        "reason": "【合格保留】客观评估通过",
    }

    job_json = json.dumps(
        {
            "job_title": "Agent 开发工程师",
            "company_name": "智元未来",
            "job_description": "大模型智能体架构落地与系统接口开发，负责Agent工作流搭建",
        }
    )

    test_args = [
        "refine_screening.py",
        "--action",
        "evaluate",
        "--job",
        job_json,
        "--policy",
        json.dumps({"enable_screening": True, "jd_blacklist": ["Java微服务"]}),
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
    assert payload["stage"] == "passed"
    assert "合格保留" in payload["reason"]


def test_cli_evaluate_loads_policy_from_settings_when_flag_omitted(capsys):
    """Regression: without --policy the script must read the merged settings dict.

    The web Studio posts to /api/screening/evaluate without a policy payload, so
    this fallback is the only policy source on that path. It used to call
    ``load_settings().to_screening_policy()`` — a method that does not exist on the
    returned ``dict`` — so the AttributeError was swallowed and the screener ran with
    an empty policy, reporting "未配置黑名单" while a title blacklist was configured.
    """
    import scripts.refine_screening as script

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.return_value = {
        "approved": False,
        "reason": "【淘汰：命中标题黑名单】",
    }

    job_json = json.dumps(
        {
            "job_title": "解决方案架构师",
            "company_name": "智元未来",
            "job_description": "大模型智能体架构落地与系统接口开发，负责Agent工作流搭建",
        }
    )

    # Same shape as the merged Configuration Realm dict: lists live at top level.
    settings = {
        "enable_screening": True,
        "title_blacklist": ["解决方案", "产品经理"],
        "jd_blacklist": [],
    }

    test_args = ["refine_screening.py", "--action", "evaluate", "--job", job_json]

    with (
        patch("sys.argv", test_args),
        patch("boss_agent.settings.load_settings", return_value=settings),
        patch.object(script, "build_llm_client", return_value=mock_llm),
    ):
        script.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())

    # The configured title blacklist must reach the LLM prompt, not be dropped.
    mock_llm.chat_completion_json.assert_called_once()
    system_prompt = mock_llm.chat_completion_json.call_args[0][0][0]["content"]
    assert "解决方案" in system_prompt
    assert payload["approved"] is False
    assert payload["stage"] == "filtered_by_deep_screener"


def test_cli_evaluate_failure_stdout_json(capsys):
    import scripts.refine_screening as script

    mock_llm = MagicMock()
    mock_llm.chat_completion_json.side_effect = RuntimeError("LLM connection error")

    job_json = json.dumps(
        {
            "job_title": "Agent 开发工程师",
            "company_name": "智元未来",
            "job_description": "大模型智能体架构落地与系统接口开发，负责Agent工作流搭建",
        }
    )

    test_args = [
        "refine_screening.py",
        "--action",
        "evaluate",
        "--job",
        job_json,
        "--policy",
        json.dumps({"enable_screening": True, "jd_blacklist": ["Java"]}),
    ]

    with (
        patch("sys.argv", test_args),
        patch.object(script, "build_llm_client", return_value=mock_llm),
    ):
        script.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["success"] is False
    assert payload["evaluate_failed"] is True
    assert "LLM connection error" in payload["error"]
