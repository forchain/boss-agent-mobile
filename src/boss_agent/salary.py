"""Salary ladder parsing and reconciliation helpers (issue #337, #338).

Mirrors web/src/lib/salary.ts to ensure cross-language parity in salary range
interpretation, bounds parsing, and fallback mapping across mobile automation
and the web dashboard.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

from .config_realm import (
    DEFAULT_SALARY_OPTIONS,
    DEFAULT_TOP_SALARY_TIER,
)
from .config_realm import (
    salary_options as get_salary_options,
)

UNLIMITED = "不限"


@dataclass(frozen=True)
class SalaryBounds:
    """Salary range bounds normalized to thousands of RMB (K).

    Supports tuple unpacking and equality for backwards compatibility
    with existing tests expecting (min_k, max_k).
    """

    min_k: float
    max_k: float

    @property
    def midpoint(self) -> float:
        """Midpoint for proximity calculation between salary tiers."""
        if math.isinf(self.max_k):
            return self.min_k * 1.25
        return (self.min_k + self.max_k) / 2.0

    def is_exact_match(self, other: SalaryBounds) -> bool:
        """Check if both lower and upper bounds are identical."""
        return self.min_k == other.min_k and self.max_k == other.max_k

    def __iter__(self) -> Iterator[float]:
        yield self.min_k
        yield self.max_k

    def __getitem__(self, index: int) -> float:
        return (self.min_k, self.max_k)[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, tuple) and len(other) == 2:
            return (self.min_k, self.max_k) == other
        if isinstance(other, SalaryBounds):
            return self.min_k == other.min_k and self.max_k == other.max_k
        return False


def _scale_magnitude(value: float, unit: str) -> float | None:
    """Scale a numeric value according to its unit into thousands (K)."""
    if unit in ("万", "万元", "w", "W"):
        return value * 10.0
    if unit in ("千", "千元", "k", "K"):
        return value
    if unit == "元":
        return value / 1000.0
    if unit == "":
        return value
    return None


def parse_salary_bounds(label: str | None) -> SalaryBounds | None:
    """Parse a tier label into its bounds in thousands (K), or None if invalid.

    Handles:
    - Open-ended ceiling/floor: `45K以上`, `15K以下`, `3000元以下`, `5万元以上`
    - Range: `15-25K`, `3000-5000元`, `1-2万元`, `20-50k`
    - Single magnitude: `15K`, `5万元`
    """
    text = (label or "").strip()
    if not text or text == UNLIMITED:
        return None

    # Open-ended band: `45K以上`, `15K以下`, `3000元以下`.
    open_ended = re.match(r"^(.+?)\s*(以上|以下|起|\+)$", text)
    if open_ended:
        base_text = open_ended.group(1).strip()
        direction = open_ended.group(2)
        base_bounds = parse_salary_bounds(base_text)
        if not base_bounds:
            return None
        return (
            SalaryBounds(min_k=0.0, max_k=base_bounds.min_k)
            if direction == "以下"
            else SalaryBounds(min_k=base_bounds.min_k, max_k=float("inf"))
        )

    # A range (`15-25K`) or single value (`15K`).
    parts = [p.strip() for p in re.split(r"\s*[-~～—]\s*", text) if p.strip()]
    if not parts or len(parts) > 2:
        return None

    def _parse_magnitude(part: str) -> tuple[float, str] | None:
        m = re.match(r"^(\d+(?:\.\d+)?)\s*(.*)$", part)
        if not m:
            return None
        try:
            val = float(m.group(1))
        except (ValueError, TypeError):
            return None
        unit = m.group(2).replace(" ", "")
        return val, unit

    parsed = [_parse_magnitude(p) for p in parts]
    if any(p is None for p in parsed):
        return None

    # Inherit unit from right part if left part has no unit (e.g. 15-25K, 3000-5000元)
    unit = next((p[1] for p in reversed(parsed) if p and p[1]), "")

    assert parsed[0] is not None
    lower = _scale_magnitude(parsed[0][0], unit)
    upper = (
        _scale_magnitude(parsed[1][0], unit)
        if len(parsed) == 2 and parsed[1] is not None
        else lower
    )
    if lower is None or upper is None:
        return None

    return SalaryBounds(min_k=min(lower, upper), max_k=max(lower, upper))


def is_salary_text(text: str | None) -> bool:
    """Check whether a given filter string represents a salary option."""
    if not text:
        return False
    trimmed = text.strip()
    if not trimmed or trimmed == UNLIMITED:
        return False

    # Exclude non-salary dimensions immediately (company scale '人', experience '年', age '岁')
    if any(kw in trimmed for kw in ("人", "年", "岁")):
        return False

    # Check for known salary units: K, k, 万, 万元, 千, 千元, 元, w, W
    has_salary_unit = bool(re.search(r"(\d+(\.\d+)?)\s*(k|K|万|万元|千|千元|元|w|W)", trimmed))
    if has_salary_unit:
        return True

    # Check open-ended suffix with salary unit
    if re.search(r"(以上|以下|起|\+)", trimmed) and any(
        u in trimmed for u in ("k", "K", "万", "元", "千", "w", "W")
    ):
        return True

    return parse_salary_bounds(trimmed) is not None


def find_closest_salary_tier(
    value: str | None, available_tiers: Sequence[str] | None = None
) -> str | None:
    """Find the closest valid salary tier in available_tiers for a requested salary.

    Ensures backward compatibility for legacy stored values (such as "5万元以上"
    or "50K以上" mapping to ceiling "45K以上", or "3K以下" mapping to "15K以下")
    without raising errors or stalling automation tasks (issue #338).
    """
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed or trimmed == UNLIMITED:
        return None

    tiers = list(available_tiers) if available_tiers else get_salary_options({})
    if not tiers:
        tiers = list(DEFAULT_SALARY_OPTIONS)

    # 1. Exact string match
    if trimmed in tiers:
        return trimmed

    target_bounds = parse_salary_bounds(trimmed)
    if not target_bounds:
        return None

    # 2. Exact numeric bounds match
    for tier in tiers:
        candidate_bounds = parse_salary_bounds(tier)
        if candidate_bounds and target_bounds.is_exact_match(candidate_bounds):
            return tier

    # 3. High ceiling fallback: when requested lower bound meets or exceeds the top tier
    # e.g., "50K以上" or "5万元以上" (min=50) -> "45K以上" (when top tier has min=45)
    top_tier = None
    top_bounds: SalaryBounds | None = None
    for tier in tiers:
        candidate_bounds = parse_salary_bounds(tier)
        if candidate_bounds and (top_bounds is None or candidate_bounds.min_k > top_bounds.min_k):
            top_bounds = candidate_bounds
            top_tier = tier

    if top_tier and top_bounds and target_bounds.min_k >= top_bounds.min_k:
        return top_tier

    # 4. Low floor fallback: e.g. "3K以下", "3000元以下", "3-5K", "5-10K"
    # If target upper bound <= lowest tier's upper bound
    lowest_tier = None
    lowest_bounds: SalaryBounds | None = None
    for tier in tiers:
        candidate_bounds = parse_salary_bounds(tier)
        if candidate_bounds and (
            lowest_bounds is None or candidate_bounds.max_k < lowest_bounds.max_k
        ):
            lowest_bounds = candidate_bounds
            lowest_tier = tier

    if lowest_tier and lowest_bounds and target_bounds.max_k <= lowest_bounds.max_k:
        return lowest_tier

    # 5. Distance / midpoint proximity
    best_tier = None
    best_distance = float("inf")

    for tier in tiers:
        candidate_bounds = parse_salary_bounds(tier)
        if not candidate_bounds:
            continue
        distance = abs(target_bounds.midpoint - candidate_bounds.midpoint)
        if distance < best_distance:
            best_distance = distance
            best_tier = tier

    return best_tier


def normalize_salary(value: str | None, options: Sequence[str] | None = None) -> str:
    """Reconcile a stored salary value against the configured ladder (exact bounds match).

    Mirrors web/src/lib/salary.ts:normalizeSalary.
    """
    raw = (value or "").strip()
    if not raw or raw == UNLIMITED:
        return ""

    configured = list(options) if options else get_salary_options({})
    if raw in configured:
        return raw

    target = parse_salary_bounds(raw)
    if target:
        for label in configured:
            bounds = parse_salary_bounds(label)
            if bounds and target.is_exact_match(bounds):
                return label

    return raw


__all__ = [
    "DEFAULT_SALARY_OPTIONS",
    "DEFAULT_TOP_SALARY_TIER",
    "UNLIMITED",
    "SalaryBounds",
    "find_closest_salary_tier",
    "is_salary_text",
    "normalize_salary",
    "parse_salary_bounds",
]
