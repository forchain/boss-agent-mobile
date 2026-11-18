"""The Feed Verdict & Ledger seam, tested without Appium (spec #444, ticket #454)."""

from pathlib import Path

import pytest

from boss_agent.enums import JobRecordStatus
from boss_agent.feed_ledger import FeedVerdictLedger
from boss_agent.job_store import InMemoryJobRecordStore
from boss_agent.keyword_constants import GREETING_SOURCE_AGENT, GREETING_SOURCE_HUMAN
from boss_agent.screening import JobEvaluationResult, JobVerdictStage

REPO_ROOT = Path(__file__).parents[2]


def _evaluation(score: int = 0, greeting: str = "") -> JobEvaluationResult:
    return JobEvaluationResult(
        passed=True,
        stage=JobVerdictStage.PASSED,
        match_score=score,
        match_reasons=[],
        jd_key_requirements=["要求1"],
        greeting_message=greeting,
    )


async def _noop(_: str) -> None:
    return None


def test_the_ledger_has_no_appium_or_page_reference() -> None:
    source = (REPO_ROOT / "src" / "boss_agent" / "feed_ledger.py").read_text(encoding="utf-8")
    assert "import appium" not in source.lower()
    assert "from .pages" not in source


def test_quota_exhausted_is_a_pure_boundary_check() -> None:
    assert FeedVerdictLedger.quota_exhausted(19, 20) is False
    assert FeedVerdictLedger.quota_exhausted(20, 20) is True
    assert FeedVerdictLedger.quota_exhausted(0, 0) is True


def test_greeting_provenance_prefers_what_the_run_states() -> None:
    evaluation = _evaluation(greeting="agent draft")
    assert FeedVerdictLedger.greeting_source({}, evaluation) == GREETING_SOURCE_AGENT
    assert (
        FeedVerdictLedger.greeting_source({"greeting_source": GREETING_SOURCE_HUMAN}, evaluation)
        == GREETING_SOURCE_HUMAN
    )
    assert FeedVerdictLedger.greeting_source({}, _evaluation()) == ""


@pytest.mark.parametrize(
    ("company", "headhunter", "expected"),
    [
        ("直招科技有限公司", False, True),
        ("某猎头", True, False),
        ("", False, False),
    ],
)
def test_enterprise_exclusion_applies_only_to_direct_hire(company, headhunter, expected) -> None:
    record = {"company_name": company, "is_headhunter": headhunter}
    assert FeedVerdictLedger.applies_enterprise_exclusion(record) is expected


@pytest.mark.asyncio
async def test_persist_verdict_keeps_a_human_copy_and_the_stored_score() -> None:
    store = InMemoryJobRecordStore()
    ledger = FeedVerdictLedger(store, _noop)

    persisted = await ledger.persist_verdict(
        {"fingerprint": "fp-1", "title": "岗位", "company_name": "公司", "match_score": 88},
        JobRecordStatus.MATCHED,
        _evaluation(score=0, greeting=""),
    )

    assert persisted.record["greeting_source"] == ""
    assert persisted.record["status"] == JobRecordStatus.MATCHED.value
    assert persisted.record["match_score"] == 88
    assert persisted.match_score == 88


@pytest.mark.asyncio
async def test_upsert_preserves_the_store_refusal_of_a_placeholder_card() -> None:
    """``None`` from the store means "nothing was written" and must survive this seam.

    The pipeline's ``persisted is None`` guard is the only thing keeping a placeholder
    card out of ``result.jobs`` and off the detail page; coercing the store's refusal to
    ``{}`` here made that guard unreachable (Spec #303, story #8).
    """
    ledger = FeedVerdictLedger(InMemoryJobRecordStore(), _noop)

    refused = await ledger.upsert(
        {"title": "未注明职位", "company_name": "未注明公司", "recruiter_name": ""}
    )

    assert refused is None
