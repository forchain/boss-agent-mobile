"""
tests/unit/test_commute_distance_screening.py
=============================================
Domain-level unit tests for the commute distance App-Enforced Filter
(Spec #209, Ticket #210). The ceiling is a condition the Boss platform cannot
express natively, so our own policy judges it — and every violation stays
subject to Whitelist Relaxation.
"""

import tempfile
from pathlib import Path

import yaml

from boss_agent.models import ScreeningPolicy


def test_commute_limit_defaults_to_40km():
    """A fresh policy filters commutes beyond 40km unless configured otherwise."""
    assert ScreeningPolicy().max_commute_distance_km == 40.0


def test_commute_violation_flags_distance_beyond_ceiling():
    policy = ScreeningPolicy(max_commute_distance_km=40.0)

    passed, violation = policy.evaluate_app_enforced_filters(commute_distance_km=52.0)

    assert passed is False
    assert violation == "【App端强制过滤】距离家庭住址 52.0km 超过通勤上限 40.0km"


def test_commute_distance_at_or_below_ceiling_passes():
    policy = ScreeningPolicy(max_commute_distance_km=40.0)

    assert policy.evaluate_app_enforced_filters(commute_distance_km=40.0) == (True, "")
    assert policy.evaluate_app_enforced_filters(commute_distance_km=19.5) == (True, "")


def test_commute_unknown_distance_fails_open():
    """An absent distance widget (no home address, remote job, timeout) must never reject."""
    policy = ScreeningPolicy(max_commute_distance_km=40.0)

    assert policy.evaluate_app_enforced_filters(commute_distance_km=None) == (True, "")


def test_commute_filter_disabled_by_none_or_non_positive_limit():
    for disabled in (None, 0.0, -1.0):
        policy = ScreeningPolicy(max_commute_distance_km=disabled)
        assert policy.evaluate_app_enforced_filters(commute_distance_km=500.0) == (True, "")


def test_commute_filter_bypassed_when_screening_disabled():
    policy = ScreeningPolicy(max_commute_distance_km=10.0, enable_screening=False)

    assert policy.evaluate_app_enforced_filters(commute_distance_km=99.0) == (True, "")


def test_evaluate_commute_distance_ignores_channel_dimension():
    """Detail-stage screening evaluates distance alone: the channel verdict was
    already rendered from the card before the page was opened."""
    policy = ScreeningPolicy(channel_preference="direct_only", max_commute_distance_km=40.0)

    assert policy.evaluate_commute_distance(52.0) == (
        False,
        "【App端强制过滤】距离家庭住址 52.0km 超过通勤上限 40.0km",
    )
    assert policy.evaluate_commute_distance(18.0) == (True, "")
    assert policy.evaluate_commute_distance(None) == (True, "")


def test_evaluate_commute_distance_respects_disabled_filter():
    assert ScreeningPolicy(max_commute_distance_km=None).evaluate_commute_distance(500.0) == (True, "")
    assert ScreeningPolicy(enable_screening=False).evaluate_commute_distance(500.0) == (True, "")


def test_commute_filter_active_only_with_positive_ceiling():
    """Handlers gate the detail-page bottom probe on this, so it must be exact."""
    assert ScreeningPolicy(max_commute_distance_km=40.0).is_commute_filter_active is True
    assert ScreeningPolicy(max_commute_distance_km=None).is_commute_filter_active is False
    assert ScreeningPolicy(max_commute_distance_km=0.0).is_commute_filter_active is False
    assert ScreeningPolicy(max_commute_distance_km=-5.0).is_commute_filter_active is False
    # Screening off means nothing can be rejected, so no probe should be paid for.
    assert (
        ScreeningPolicy(max_commute_distance_km=40.0, enable_screening=False).is_commute_filter_active
        is False
    )


def test_commute_violation_still_relaxable_by_whitelist():
    """Every App-Enforced Filter violation remains subject to Whitelist Relaxation."""
    policy = ScreeningPolicy(max_commute_distance_km=20.0, title_whitelist=["大模型"])

    passed, violation = policy.evaluate_app_enforced_filters(commute_distance_km=35.0)
    assert passed is False
    assert violation

    relaxed, token = policy.evaluate_whitelist_relaxation(
        title="大模型 Agent 平台架构师",
        company_name="某科技公司",
        tags=[],
        digest="",
    )
    assert relaxed is True
    assert token == "大模型"


def test_commute_violation_not_relaxable_without_whitelist_hit():
    policy = ScreeningPolicy(max_commute_distance_km=20.0, title_whitelist=["大模型"])

    passed, _ = policy.evaluate_app_enforced_filters(commute_distance_km=35.0)
    assert passed is False
    relaxed, _token = policy.evaluate_whitelist_relaxation(
        title="Java 后端开发工程师",
        company_name="某银行",
        tags=["Java"],
        digest="",
    )
    assert relaxed is False


def test_commute_limit_serialization_roundtrip():
    policy = ScreeningPolicy(max_commute_distance_km=25.5)

    data = policy.to_dict()
    assert data["max_commute_distance_km"] == 25.5
    assert ScreeningPolicy.from_dict(data).max_commute_distance_km == 25.5


def test_commute_limit_roundtrip_when_disabled():
    """A disabled filter (null on disk) must not silently revert to the 40km default."""
    serialized = ScreeningPolicy(max_commute_distance_km=None).to_dict()

    assert serialized["max_commute_distance_km"] is None
    assert ScreeningPolicy.from_dict(serialized).max_commute_distance_km is None


def test_commute_limit_coerces_blank_and_string_values():
    assert ScreeningPolicy.from_dict({"max_commute_distance_km": ""}).max_commute_distance_km is None
    assert (
        ScreeningPolicy.from_dict({"max_commute_distance_km": "null"}).max_commute_distance_km is None
    )
    assert (
        ScreeningPolicy.from_dict({"max_commute_distance_km": "35.5"}).max_commute_distance_km == 35.5
    )


def test_commute_limit_save_and_load_roundtrip(tmp_path: Path | None = None):
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "settings.local.yaml"
        ScreeningPolicy(max_commute_distance_km=28.0, title_whitelist=["Agent"]).save_default(
            config_path=target
        )

        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert raw["max_commute_distance_km"] == 28.0

        loaded = ScreeningPolicy.load_default(config_path=target)
        assert loaded.max_commute_distance_km == 28.0
        assert loaded.title_whitelist == ["Agent"]


def test_commute_limit_loads_from_example_config():
    example_path = Path("config/settings.example.yaml")
    assert example_path.exists(), "config/settings.example.yaml must exist"

    policy = ScreeningPolicy.load_default(config_path=example_path)
    assert policy.max_commute_distance_km == 40.0
