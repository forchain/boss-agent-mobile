"""
tests/unit/test_evaluate_match_script.py
========================================
Unit and CLI tests for scripts/evaluate_match.py recruiter salutation integration.
"""

import json
import subprocess
import sys
from pathlib import Path


def test_evaluate_match_script_extracts_recruiter_and_formats_fallback():
    """When LLM evaluation fails, fallback greeting must use dynamic recruiter prefix."""
    job_payload = {
        "title": "AI Agent 架构师",
        "company": "未来智元",
        "salary": "40-60K",
        "description": "岗位职责：负责智能体工作流与微服务通信编排，精通Python与LangGraph。",
        "recruiter_name": "张女士 · 招聘总监",
        "recruiter_title": "招聘总监",
    }

    # Run scripts/evaluate_match.py with an invalid LLM key to force fallback
    root_dir = Path(__file__).resolve().parent.parent.parent
    script_path = root_dir / "scripts" / "evaluate_match.py"

    proc = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--job",
            json.dumps(job_payload, ensure_ascii=False),
            "--llm-config",
            json.dumps(
                {"api_key": "invalid-key-for-test", "base_url": "http://127.0.0.1:9"},
                ensure_ascii=False,
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["match_score"] == 50
    assert data["greeting_message"].startswith("张女士您好,幸会!")


def test_evaluate_match_script_compound_surname_fallback():
    """Compound surname recruiter must format as 'xx总您好,幸会!'."""
    job_payload = {
        "title": "Python 开发工程师",
        "company": "测试科技",
        "salary": "30-50K",
        "description": "岗位职责：微服务核心研发，掌握异步编程框架与高性能通信机制。",
        "recruiter_name": "诸葛孔明",
    }

    root_dir = Path(__file__).resolve().parent.parent.parent
    script_path = root_dir / "scripts" / "evaluate_match.py"

    proc = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--job",
            json.dumps(job_payload, ensure_ascii=False),
            "--llm-config",
            json.dumps(
                {"api_key": "invalid-key-for-test", "base_url": "http://127.0.0.1:9"},
                ensure_ascii=False,
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["greeting_message"].startswith("诸葛总您好,幸会!")


def test_evaluate_match_script_generic_fallback_when_recruiter_absent():
    """Missing or English recruiter falls back to '您好,幸会!'."""
    job_payload = {
        "title": "全栈工程师",
        "company": "测试科技",
        "salary": "30-50K",
        "description": "岗位职责：全栈业务系统开发，具备扎实的工程研发落地能力。",
        "recruiter_name": "Alice",
    }

    root_dir = Path(__file__).resolve().parent.parent.parent
    script_path = root_dir / "scripts" / "evaluate_match.py"

    proc = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--job",
            json.dumps(job_payload, ensure_ascii=False),
            "--llm-config",
            json.dumps(
                {"api_key": "invalid-key-for-test", "base_url": "http://127.0.0.1:9"},
                ensure_ascii=False,
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["greeting_message"].startswith("您好,幸会!")
