"""
tests/unit/test_commute_distance_pipeline.py
============================================
Pipeline integration for the commute distance App-Enforced Filter
(Spec #209, Ticket #212).

Covers the CandidateScreener seam: a job beyond the ceiling is flagged by the
App-Enforced Filter, and the probed distance travels from the card into that filter.
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.job_entities import JobCardBrief
from boss_agent.screening import CandidateScreener, CardFacets, CardVerdictStage
from boss_agent.screening_policy import ScreeningPolicy

DISTANT = 52.0
NEARBY = 18.5


# The screener seam only. Worker-level integration (AUTO_APPLY / SCRAPE_JOBS
# handlers persisting distance verdicts) lives in
# tests/unit/test_worker_relaxation_integration.py, the seam Ticket #212 names.


def _distant_card() -> JobCardBrief:
    return JobCardBrief(
        title="大模型 Agent 平台架构师",
        company_name="某科技有限公司",
        recruiter_name="张先生",
        commute_distance_km=DISTANT,
        commute_distance_text="距离家庭住址52千米",
    )


def test_evaluate_card_reads_the_probed_commute_distance():
    """The probed distance must survive the card -> facets read, not just be present:
    a distance carried on the card has to reach the App-Enforced Filter."""
    assert CardFacets.from_card(_distant_card()).commute_distance_km == pytest.approx(DISTANT)


def test_evaluate_card_rejects_a_card_beyond_the_commute_ceiling():
    """A job past max_commute_distance_km is rejected by the App-Enforced Filter.

    The whitelist here deliberately does not match the card's title, so the point is
    the one this file makes at the screener seam: an unmatched relaxation token leaves
    the violation standing. The matched case (and the worker handler that persists it)
    is the seam #212 named, in ``test_worker_relaxation_integration.py``.
    """
    screener = CandidateScreener(llm_client=MagicMock())

    verdict = screener.evaluate_card(
        _distant_card(),
        ScreeningPolicy(max_commute_distance_km=40.0, title_whitelist=["量子计算"]),
    )

    assert verdict.app_rule_pass is False
    assert "超过通勤上限" in verdict.app_rule_violation
    assert verdict.passed is False
    assert verdict.stage is CardVerdictStage.FILTERED_BY_APP_RULE
    # The whitelist did not match, so the violation stands.
    assert verdict.relaxed_by_whitelist is False


def test_evaluate_card_accepts_a_card_within_the_commute_ceiling():
    """The same filter must let a nearby card through, so the ceiling is really compared."""
    screener = CandidateScreener(llm_client=MagicMock())
    card = JobCardBrief(
        title="大模型 Agent 平台架构师",
        company_name="某科技有限公司",
        recruiter_name="张先生",
        commute_distance_km=NEARBY,
        commute_distance_text="距离家庭住址18.5千米",
    )

    verdict = screener.evaluate_card(card, ScreeningPolicy(max_commute_distance_km=40.0))

    assert verdict.app_rule_pass is True
    assert verdict.passed is True
    assert verdict.stage is CardVerdictStage.PASSED
