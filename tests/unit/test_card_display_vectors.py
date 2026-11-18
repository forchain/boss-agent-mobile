"""
tests/unit/test_card_display_vectors.py
=======================================
Cross-language Card Display (Job Digest & tag sanitization) parity — Python half.
"""

import json
from pathlib import Path

import pytest

from boss_agent.core.shared.identifier_helpers import (
    extract_digest_from_jd,
    sanitize_tags,
)

FIXTURE_PATH = Path(__file__).parents[2] / "config" / "card_display.vectors.json"


def _fixture() -> dict:
    assert FIXTURE_PATH.is_file(), f"Missing fixture: {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", _fixture()["digest_vectors"], ids=lambda v: v["case"])
def test_python_extract_digest_matches_shared_vector(vector: dict) -> None:
    assert extract_digest_from_jd(vector["jd"], max_chars=vector["max_chars"]) == vector["expected"]


@pytest.mark.parametrize("vector", _fixture()["tag_vectors"], ids=lambda v: v["case"])
def test_python_sanitize_tags_matches_shared_vector(vector: dict) -> None:
    job = vector["job"]
    cleaned = sanitize_tags(
        vector["raw_tags"],
        recruiter_name=job["recruiter_name"],
        recruiter_title=job["recruiter_title"],
        location=job["location"],
        company_name=job["company_name"],
        title=job["title"],
    )
    assert cleaned == vector["expected"]
