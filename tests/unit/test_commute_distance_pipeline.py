"""
tests/unit/test_commute_distance_pipeline.py
============================================
Pipeline integration for the commute distance App-Enforced Filter
(Spec #209, Ticket #212).

Covers the LangGraph state seam plus both worker handlers: a job beyond the
ceiling is flagged by the App-Enforced Filter node and remains relaxable by the
whitelist router, and the probed distance travels in the graph state.
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.graph import (
    JobApplicationState,
    app_enforced_filter_node,
    apply_relaxation_node,
    run_job_application_graph,
    whitelist_relaxer,
)
from boss_agent.models import ScreeningPolicy
from boss_agent.pages import JobCardBrief

DISTANT = 52.0
NEARBY = 18.5


# The LangGraph seam only. Worker-level integration (AUTO_APPLY / SCRAPE_JOBS
# handlers persisting distance verdicts) lives in
# tests/unit/test_worker_relaxation_integration.py, the seam Ticket #212 names.


def test_job_application_state_declares_commute_distance_field():
    assert "commute_distance_km" in JobApplicationState.__annotations__


def test_graph_carries_probed_distance_into_the_state():
    """The declared state field must actually be populated, not just declared:
    a distance carried on the card has to reach the App-Enforced Filter node."""
    card = JobCardBrief(
        title="大模型 Agent 平台架构师",
        company_name="某科技有限公司",
        recruiter_name="张先生",
        commute_distance_km=DISTANT,
        commute_distance_text="距离家庭住址52千米",
    )

    state = run_job_application_graph(
        card=card,
        policy=ScreeningPolicy(max_commute_distance_km=40.0, title_whitelist=["量子计算"]),
        jd_text="负责大模型应用与Agent工作流平台建设。",
        llm_client=MagicMock(),
    )

    assert state["commute_distance_km"] == pytest.approx(DISTANT)
    assert state["app_rule_pass"] is False
    assert "超过通勤上限" in state["app_rule_violation"]


def test_app_enforced_filter_node_flags_distance_beyond_ceiling():
    state: JobApplicationState = {
        "card": {"title": "大模型 Agent 平台架构师", "company_name": "某科技公司", "commute_distance_km": DISTANT},
        "screening_policy": ScreeningPolicy(max_commute_distance_km=40.0).to_dict(),
    }

    result = app_enforced_filter_node(state)

    assert result["app_rule_pass"] is False
    assert "距离家庭住址 52.0km" in result["app_rule_violation"]
    assert "通勤上限 40.0km" in result["app_rule_violation"]


def test_app_enforced_filter_node_passes_distance_within_ceiling():
    state: JobApplicationState = {
        "card": {"title": "Agent 工程师", "company_name": "某科技公司", "commute_distance_km": NEARBY},
        "screening_policy": ScreeningPolicy(max_commute_distance_km=40.0).to_dict(),
    }

    assert app_enforced_filter_node(state)["app_rule_pass"] is True


def test_app_enforced_filter_node_fails_open_without_distance():
    state: JobApplicationState = {
        "card": {"title": "远程 Agent 工程师", "company_name": "某科技公司"},
        "screening_policy": ScreeningPolicy(max_commute_distance_km=20.0).to_dict(),
    }

    assert app_enforced_filter_node(state)["app_rule_pass"] is True


def test_distant_job_without_whitelist_hit_is_rejected_by_router():
    state: JobApplicationState = {
        "card": {"title": "Java 后端开发工程师", "company_name": "某银行", "commute_distance_km": DISTANT},
        "screening_policy": ScreeningPolicy(
            max_commute_distance_km=40.0, title_whitelist=["大模型"]
        ).to_dict(),
        "app_rule_pass": False,
    }

    assert whitelist_relaxer(state) == "reject"


def test_distant_job_is_rescued_by_whitelist_relaxation():
    state: JobApplicationState = {
        "card": {"title": "大模型 Agent 平台架构师", "company_name": "某科技", "commute_distance_km": DISTANT},
        "screening_policy": ScreeningPolicy(
            max_commute_distance_km=40.0, title_whitelist=["大模型"]
        ).to_dict(),
        "app_rule_pass": False,
        "app_rule_violation": "【App端强制过滤】距离家庭住址 52.0km 超过通勤上限 40.0km",
    }

    assert whitelist_relaxer(state) == "relax"
    relaxed = apply_relaxation_node(state)
    assert relaxed["relaxed_by_whitelist"] is True
    assert "白名单放宽" in relaxed["relaxation_reason"]
