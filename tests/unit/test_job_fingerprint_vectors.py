"""
tests/unit/test_job_fingerprint_vectors.py
==========================================
Cross-language Job Fingerprint parity — the Python half.

``config/fingerprint.vectors.json`` is consumed by this tier *and* by
``web/src/tests/jobFingerprintVectors.test.ts``. Neither implementation owns the
contract; the fixture does, and drift in either one fails its own suite. The drift
this pins is real: the dashboard computed fingerprints without stripping the
recruiter's ``·``-suffix while Python stripped it, so a job posted through the
dashboard never deduplicated against the same job scraped by the worker.
"""

import json
from pathlib import Path

import pytest

from boss_agent.models import compute_job_fingerprint

FIXTURE_PATH = Path(__file__).parents[2] / "config" / "fingerprint.vectors.json"


def _fixture() -> dict:
    assert FIXTURE_PATH.is_file(), (
        f"{FIXTURE_PATH} is the cross-language Job Fingerprint pin; both the pytest and "
        "vitest tiers read it."
    )
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _vectors() -> list[dict]:
    return _fixture()["vectors"]


def _by_case(case: str) -> dict:
    return next(vector for vector in _vectors() if vector["case"] == case)


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["case"])
def test_python_fingerprint_matches_shared_vector(vector: dict) -> None:
    """Every shipped vector is one Python must reproduce exactly."""
    assert (
        compute_job_fingerprint(
            vector["company_name"], vector["title"], vector["recruiter_name"]
        )
        == vector["fingerprint"]
    )


def test_fixture_pins_the_recruiter_suffix_rule() -> None:
    """The fixture only earns its keep if it covers the drift class it exists for.

    A recruiter read as ``张三`` and as ``张三·猎头顾问`` are the same person and must
    produce the same fingerprint; a fixture without that pair cannot catch the
    dashboard's stripped-vs-unstripped divergence coming back.
    """
    plain = _by_case("plain-name")
    suffixed = _by_case("recruiter-middot-title")
    assert plain["recruiter_name"] != suffixed["recruiter_name"]
    assert plain["fingerprint"] == suffixed["fingerprint"]


def test_fixture_covers_every_recruiter_separator() -> None:
    """Each separator Boss uses between name and title appears in at least one vector."""
    recruiters = " ".join(vector["recruiter_name"] for vector in _vectors())
    for separator in ("·", "•", "・"):
        assert separator in recruiters, f"no vector exercises the {separator!r} separator"


def test_fixture_vectors_are_distinct_keys_except_the_documented_pair() -> None:
    """Guard against a vector degenerating into a duplicate of another.

    If two vectors share a fingerprint by accident the fixture stops testing anything;
    the only intended collision is ``plain-name``/``recruiter-middot-title``.
    """
    fingerprints: dict[str, str] = {}
    collisions: set[str] = set()
    for vector in _vectors():
        prior = fingerprints.setdefault(vector["fingerprint"], vector["case"])
        if prior != vector["case"]:
            collisions.add("+".join(sorted((prior, vector["case"]))))
    assert collisions == {"plain-name+recruiter-middot-title"}
