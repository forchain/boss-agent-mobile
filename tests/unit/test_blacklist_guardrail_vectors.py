"""
tests/unit/test_blacklist_guardrail_vectors.py
==============================================
Cross-language Headhunter Agency & Masked Company Guardrail parity — Python half.

``config/blacklist_guardrail.vectors.json`` is consumed by both this tier and
``web/src/tests/blacklistGuardrailVectors.test.ts``.
"""

import json
from pathlib import Path

import pytest

from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.shared.identifier_helpers import (
    is_headhunter_agency_name,
    is_masked_company_name,
)
from boss_agent.core.shared.keyword_constants import HEADHUNTER_AGENCY_KEYWORDS

FIXTURE_PATH = Path(__file__).parents[2] / "config" / "blacklist_guardrail.vectors.json"


def _fixture() -> dict:
    assert FIXTURE_PATH.is_file(), f"Missing shared guardrail fixture: {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _vectors() -> list[dict]:
    return _fixture()["vectors"]


def test_python_agency_keywords_match_fixture() -> None:
    assert list(HEADHUNTER_AGENCY_KEYWORDS) == _fixture()["agency_keywords"]


def test_every_agency_keyword_is_exercised_by_a_vector() -> None:
    agency_names = [v["company_name"] for v in _vectors() if v["expected_is_agency"]]
    for keyword in _fixture()["agency_keywords"]:
        assert any(keyword in name for name in agency_names), (
            f"Agency keyword {keyword!r} is not covered by any fixture vector"
        )


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["case"])
def test_python_guardrail_matches_shared_vector(vector: dict) -> None:
    name = vector["company_name"]
    is_headhunter = vector["is_headhunter"]
    policy = ScreeningPolicy()

    assert is_headhunter_agency_name(name) is vector["expected_is_agency"]
    assert is_masked_company_name(name) is vector["expected_is_masked"]
    allowed, _notice = policy.validate_can_blacklist_company(name, is_headhunter=is_headhunter)
    assert allowed is vector["expected_allowed"]
