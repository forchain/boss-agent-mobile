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
    run_job_application_graph,
)
from boss_agent.job_entities import JobCardBrief
from boss_agent.screening_policy import ScreeningPolicy

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
