"""Unit tests for boss_agent.salary module (issue #338).

Verifies bounds parsing, salary string detection, closest tier fallback mapping,
and Python-side parity with web/src/lib/salary.ts.
"""

import math

from boss_agent.config_realm import DEFAULT_SALARY_OPTIONS
from boss_agent.salary import (
    find_closest_salary_tier,
    is_salary_text,
    normalize_salary,
    parse_salary_bounds,
)


def test_parse_salary_bounds_open_ended():
    assert parse_salary_bounds("15K以下") == (0.0, 15.0)
    assert parse_salary_bounds("15k以下") == (0.0, 15.0)
    assert parse_salary_bounds("3000元以下") == (0.0, 3.0)
    assert parse_salary_bounds("1.5万以下") == (0.0, 15.0)

    bounds_top = parse_salary_bounds("45K以上")
    assert bounds_top is not None
    assert bounds_top[0] == 45.0
    assert math.isinf(bounds_top[1])

    bounds_50k = parse_salary_bounds("50K以上")
    assert bounds_50k is not None
    assert bounds_50k[0] == 50.0
    assert math.isinf(bounds_50k[1])

    bounds_5w = parse_salary_bounds("5万元以上")
    assert bounds_5w is not None
    assert bounds_5w[0] == 50.0
    assert math.isinf(bounds_5w[1])


def test_parse_salary_bounds_ranges():
    assert parse_salary_bounds("15-25K") == (15.0, 25.0)
    assert parse_salary_bounds("15-25k") == (15.0, 25.0)
    assert parse_salary_bounds("3000-5000元") == (3.0, 5.0)
    assert parse_salary_bounds("1-2万元") == (10.0, 20.0)
    assert parse_salary_bounds("1-2万") == (10.0, 20.0)
    assert parse_salary_bounds("2.5-3.5万") == (25.0, 35.0)


def test_parse_salary_bounds_invalids():
    assert parse_salary_bounds("") is None
    assert parse_salary_bounds("不限") is None
    assert parse_salary_bounds(None) is None
    assert parse_salary_bounds("硕士") is None
    assert parse_salary_bounds("今日活跃") is None


def test_is_salary_text():
    # Valid salary strings
    assert is_salary_text("15K以下") is True
    assert is_salary_text("15-25K") is True
    assert is_salary_text("45K以上") is True
    assert is_salary_text("5万元以上") is True
    assert is_salary_text("50K以上") is True
    assert is_salary_text("3000元以下") is True
    assert is_salary_text("1.5万以下") is True

    # Non-salary strings must return False
    assert is_salary_text("硕士") is False
    assert is_salary_text("本科") is False
    assert is_salary_text("10年以上") is False
    assert is_salary_text("1年以内") is False
    assert is_salary_text("在校/应届") is False
    assert is_salary_text("今日活跃") is False
    assert is_salary_text("100-499人") is False
    assert is_salary_text("1万人以上") is False
    assert is_salary_text("10000人以上") is False
    assert is_salary_text("不限") is False
    assert is_salary_text("") is False
    assert is_salary_text(None) is False


def test_salary_bounds_value_object():
    from boss_agent.salary import SalaryBounds

    b = SalaryBounds(15.0, 25.0)
    assert b.min_k == 15.0
    assert b.max_k == 25.0
    assert b.midpoint == 20.0
    # Unpacking and tuple equality
    min_val, max_val = b
    assert min_val == 15.0
    assert max_val == 25.0
    assert b == (15.0, 25.0)
    assert b[0] == 15.0
    assert b[1] == 25.0


def test_find_closest_salary_tier_default_ladder():
    ladder = DEFAULT_SALARY_OPTIONS  # ["15K以下", "15-25K", "25-35K", "35-45K", "45K以上"]

    # Exact matches
    for tier in ladder:
        assert find_closest_salary_tier(tier, ladder) == tier

    # High ceiling fallbacks (>= 45K)
    assert find_closest_salary_tier("50K以上", ladder) == "45K以上"
    assert find_closest_salary_tier("5万元以上", ladder) == "45K以上"
    assert find_closest_salary_tier("50k以上", ladder) == "45K以上"

    # Lower open-ended must not be forced to 45K以上
    assert find_closest_salary_tier("10K以上", ladder) in ("15K以下", "15-25K")

    # Low floor fallbacks
    assert find_closest_salary_tier("3K以下", ladder) == "15K以下"
    assert find_closest_salary_tier("3000元以下", ladder) == "15K以下"
    assert find_closest_salary_tier("3-5K", ladder) == "15K以下"
    assert find_closest_salary_tier("5-10K", ladder) == "15K以下"

    # Intermediate ranges
    assert find_closest_salary_tier("10-20K", ladder) == "15-25K"
    assert find_closest_salary_tier("1-2万元", ladder) == "15-25K"
    assert find_closest_salary_tier("20-50K", ladder) in ("25-35K", "35-45K")
    assert find_closest_salary_tier("2-5万", ladder) in ("25-35K", "35-45K")


def test_normalize_salary_parity_with_web():
    ladder = DEFAULT_SALARY_OPTIONS

    # Preserves exact tier
    assert normalize_salary("25-35K", ladder) == "25-35K"

    # Empty / 不限
    assert normalize_salary("", ladder) == ""
    assert normalize_salary("不限", ladder) == ""
    assert normalize_salary(None, ladder) == ""

    # Legacy ladder bounds equality
    legacy_ladder = ["0-3K", "3-5K", "5-10K", "10K以上"]
    assert normalize_salary("3000-5000元", legacy_ladder) == "3-5K"
    assert normalize_salary("3000元以下", legacy_ladder) == "0-3K"
    assert normalize_salary("1万元以上", legacy_ladder) == "10K以上"

    # Never widens a contained tier (matches web)
    assert normalize_salary("3-5K", ladder) == "3-5K"
    assert normalize_salary("5万元以上", ladder) == "5万元以上"
