"""
tests/unit/test_rule_mirrors_vectors.py
=======================================
Cross-language parity tests for Greeting Provenance, Commute Coercion, Chat
Acknowledgment coercion, Cooldown Timestamps, and Salutation parsing — Python half.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from boss_agent.core.chat.domain.rejection import coerce_positive_int
from boss_agent.core.job.domain.screening_policy import ScreeningPolicy
from boss_agent.core.shared.identifier_helpers import (
    format_recruiter_greeting_prefix,
    greeting_is_human,
    is_communication_expired,
    parse_recruiter_title,
)
from boss_agent.core.shared.keyword_constants import COMMON_COMPOUND_SURNAMES

FIXTURE_PATH = Path(__file__).parents[2] / "config" / "rule_mirrors.vectors.json"


def _fixture() -> dict:
    assert FIXTURE_PATH.is_file(), f"Missing fixture: {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", _fixture()["greeting_provenance"], ids=lambda v: v["case"])
def test_greeting_provenance_parity(vector: dict) -> None:
    assert greeting_is_human(vector["record"]) is vector["expected_is_human"]


@pytest.mark.parametrize("vector", _fixture()["commute_limit"], ids=lambda v: v["case"])
def test_commute_limit_coercion_parity(vector: dict) -> None:
    assert ScreeningPolicy._normalize_commute_limit(vector["input"]) == vector["expected"]


def test_commute_limit_rejects_non_finite_float() -> None:
    assert ScreeningPolicy._normalize_commute_limit(float("inf")) is None
    assert ScreeningPolicy._normalize_commute_limit(float("-inf")) is None
    assert ScreeningPolicy._normalize_commute_limit(float("nan")) is None


@pytest.mark.parametrize(
    "vector", _fixture()["chat_acknowledgment_positive_int"], ids=lambda v: v["case"]
)
def test_chat_acknowledgment_positive_int_parity(vector: dict) -> None:
    assert coerce_positive_int(vector["input"], vector["fallback"]) == vector["expected"]


@pytest.mark.parametrize("vector", _fixture()["cooldown_timestamps"], ids=lambda v: v["case"])
def test_cooldown_timestamp_parity(vector: dict) -> None:
    now = datetime.fromisoformat(vector["now"].replace("Z", "+00:00"))
    assert (
        is_communication_expired(vector["record"], vector["cooldown_days"], now=now)
        is vector["expected_expired"]
    )


def test_compound_surnames_match_fixture() -> None:
    assert set(_fixture()["compound_surnames"]) == COMMON_COMPOUND_SURNAMES


@pytest.mark.parametrize("vector", _fixture()["salutation"], ids=lambda v: v["case"])
def test_salutation_parity(vector: dict) -> None:
    assert parse_recruiter_title(vector["raw"]) == vector["expected_title"]
    assert format_recruiter_greeting_prefix(vector["raw"]) == vector["expected_prefix"]
