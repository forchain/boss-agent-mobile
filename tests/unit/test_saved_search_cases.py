"""
tests/unit/test_saved_search_cases.py
=====================================
Cross-language SavedSearch mapping parity — Python half.
"""

import json
from pathlib import Path

import pytest

from boss_agent.adapters.outbound.pocketbase.saved_search_store import (
    _wire_body,
    record_to_saved_search,
)
from boss_agent.core.search.domain.models import (
    SAVED_SEARCH_FILTER_KEYS,
    SAVED_SEARCH_MAX_JOBS,
)

FIXTURE_PATH = Path(__file__).parents[2] / "config" / "saved_search.cases.json"


def _fixture() -> dict:
    assert FIXTURE_PATH.is_file(), f"Missing fixture: {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_python_saved_search_constants_match_fixture() -> None:
    fx = _fixture()
    assert list(SAVED_SEARCH_FILTER_KEYS) == fx["filter_keys"]
    assert fx["default_max_jobs"] == SAVED_SEARCH_MAX_JOBS


@pytest.mark.parametrize("case", _fixture()["cases"], ids=lambda c: c["case"])
def test_python_record_to_saved_search_and_wire_body_match_fixture(case: dict) -> None:
    fx = _fixture()
    record = case["record"]
    expected = case["expected"]

    search = record_to_saved_search(record["id"], record)
    assert search.id == expected["id"]
    assert search.name == expected["name"]
    assert search.keyword == expected["keyword"]
    assert search.enable_search is expected["enable_search"]
    assert search.enable_filter is expected["enable_filter"]
    assert search.target_action == expected["target_action"]
    assert search.target_task_type == expected["target_task_type"]
    assert search.max_jobs == expected["max_jobs"]
    assert search.filter.channel_preference == expected["channel_preference"]

    wire = _wire_body(search)
    assert sorted(wire["filter"].keys()) == sorted(fx["filter_keys"])
    assert "unknown_legacy_extra" not in wire["filter"]
    assert wire["filter"]["channel_preference"] == expected["channel_preference"]
    assert wire["filter"]["enable_filter"] is expected["enable_filter"]
