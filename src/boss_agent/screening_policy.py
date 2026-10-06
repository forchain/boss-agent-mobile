"""
boss_agent.screening_policy
===========================
Screening policy domain entity and card-level keyword evaluation (Issue #311, Spec #303).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from boss_agent.enums import (
    INHERIT_CHANNEL,
    ChannelPreference,
    normalize_channel_preference,
)
from boss_agent.identifier_helpers import (
    is_headhunter_agency_name,
    is_masked_company_name,
)


@dataclass
class ScreeningPolicy:
    """Policy rules for multi-stage job screening.

    Blacklists enforce deterministic one-strike rejection over compact card facets.
    The whitelist is NOT an inclusion gate and never rejects a job: it exists solely
    as relaxation tokens for App-Enforced Filters (see ``evaluate_app_enforced_filters``
    and ``evaluate_whitelist_relaxation``).
    """

    title_whitelist: list[str] = field(default_factory=list)
    title_blacklist: list[str] = field(default_factory=list)
    company_blacklist: list[str] = field(default_factory=list)
    jd_blacklist: list[str] = field(default_factory=list)
    business_district_blacklist: list[str] = field(default_factory=list)
    #: Borderline business districts whose direct-hire postings are worth measuring
    #: the exact commute distance for (spec #328). Empty = measure nothing (distance
    #: screening skipped, zero swipe overhead).
    business_district_inspect_list: list[str] = field(default_factory=list)
    enable_screening: bool = True
    channel_preference: str = ChannelPreference.ALL
    max_commute_distance_km: float | None = 40.0
    #: Config file `load_default` resolved this policy from; where blacklist
    #: additions are written back. Excluded from equality and serialization.
    source_path: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.channel_preference = normalize_channel_preference(self.channel_preference)
        self.max_commute_distance_km = self._normalize_commute_limit(self.max_commute_distance_km)

    @staticmethod
    def _normalize_commute_limit(value: Any) -> float | None:
        """Coerce a commute ceiling to a float in kilometers, or None when filtering is off.

        Blank strings, ``null`` sentinels and unparseable values all mean "no ceiling":
        a corrupt config must widen the filter, never silently impose an unexpected one.
        """
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text or text.lower() in ("null", "none", "~"):
                return None
            try:
                return float(text)
            except ValueError:
                return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _match_location_token(tokens: list[str], location: str) -> str | None:
        """The first token contained in the location string (case-insensitive), or None.

        Iterates in list order, so an operator can sort longer or more specific names first.
        Returns the original token as typed in the policy rather than the normalized hit:
        the audit reason quotes their own entry rather than a normalized copy of it.
        """
        norm = (location or "").lower()
        for raw in tokens:
            token = (raw or "").strip()
            if token and token.lower() in norm:
                return token
        return None

    @property
    def is_commute_filter_active(self) -> bool:
        """True when the commute ceiling can actually reject something.

        Handlers gate the detail-page bottom probe on this: with the filter off,
        scrolling to find the distance widget would cost latency for nothing.
        """
        return (
            self.enable_screening
            and self.max_commute_distance_km is not None
            and self.max_commute_distance_km > 0
        )

    def should_probe_commute_distance(self, is_headhunter: bool | None, location: str = "") -> bool:
        """Whether this posting's detail page is worth probing for the distance widget.

        Three gates, all of which must hold. A disabled commute ceiling can reject
        nothing, so scrolling to find the distance widget would cost latency for nothing.
        The probe only buys anything for direct-hire postings: the platform conceals the
        hiring enterprise and its office address for headhunter roles, so ``home_tip_vf``
        is never rendered for them and the scroll budget would be spent discovering that.
        And the posting must sit in a district the operator asked to have measured — an
        empty list means nothing is probed at all, so a scan that only cares about the
        blacklist pays no swipe latency whatsoever. An unknown channel (``None``) still
        probes: an unrecognised direct hire must not be silently spared distance screening.
        """
        if not self.is_commute_filter_active or is_headhunter is True:
            return False
        return self._match_location_token(self.business_district_inspect_list, location) is not None

    def evaluate_location_blacklist(self, location: str) -> tuple[bool, str]:
        """Evaluate the business district blacklist against a location the card did not carry.

        The card's location facet names a district but no station, so a station entry in
        ``business_district_blacklist`` can only be judged once the detail page has been
        read and its location line is in hand (issue #333). Split out from
        ``matches_card_keywords`` so the detail stage reuses one list and one reason
        string without also re-running the title, company and JD blacklists against
        detail-page text, which those lists were never written for.

        Deterministic and one-strike, exactly as at the card stage: Whitelist Relaxation
        does not exempt a region the operator refused, and an absent or unreadable
        location fails open, because a posting the platform describes only as "上海"
        must not be discarded over missing data.
        """
        if not self.enable_screening:
            return True, ""

        token = self._match_location_token(self.business_district_blacklist, location)
        if token is None:
            return True, ""

        return (
            False,
            f"【商圈黑名单过滤】岗位所在区域/商圈 '{location}' 命中黑名单 '{token}'",
        )

    def evaluate_commute_distance(self, commute_distance_km: float | None) -> tuple[bool, str]:
        """Evaluate the commute distance App-Enforced Filter on its own.

        Split out because the distance is only knowable from the detail page bottom,
        after the card-level channel verdict was already rendered and audited — so the
        detail-stage check must not re-report a channel violation already adjudicated.

        Fails open (passes) whenever the distance is unknown or the ceiling is disabled
        (None or <= 0): an absent widget must never reject a job.
        """
        limit = self.max_commute_distance_km
        if not self.enable_screening or limit is None or limit <= 0:
            return True, ""

        if commute_distance_km is None or commute_distance_km <= limit:
            return True, ""

        return (
            False,
            f"【App端强制过滤】距离家庭住址 {commute_distance_km:.1f}km 超过通勤上限 {limit:.1f}km",
        )

    def evaluate_app_enforced_filters(
        self,
        is_headhunter: bool = False,
        commute_distance_km: float | None = None,
    ) -> tuple[bool, str]:
        """Evaluate App-Enforced Filters the Boss platform cannot express natively.

        Currently the recruitment channel preference (direct-hire vs headhunter) and
        the commute distance ceiling. Returns (passed: bool, violation: str);
        ``violation`` is an empty string when the card satisfies all App-Enforced
        Filters, otherwise every violated condition joined by '；', which the
        Whitelist Relaxation router may still exempt. Reporting each violated
        dimension — rather than the first one checked — keeps this caller's audit
        trail equivalent to SCRAPE_JOBS', which adjudicates the two dimensions at
        different stages and can therefore record both.
        """
        if not self.enable_screening:
            return True, ""

        violations: list[str] = []

        passed, commute_violation = self.evaluate_commute_distance(commute_distance_km)
        if not passed:
            violations.append(commute_violation)

        if self.channel_preference == ChannelPreference.DIRECT_ONLY and is_headhunter:
            violations.append(
                "【App端强制过滤】猎头代招岗位违反直聘渠道偏好 (channel_preference='direct_only')"
            )
        elif self.channel_preference == ChannelPreference.HEADHUNTER_ONLY and not is_headhunter:
            violations.append(
                "【App端强制过滤】直招岗位违反猎头渠道偏好 (channel_preference='headhunter_only')"
            )

        if not violations:
            return True, ""

        return False, "；".join(violations)

    def evaluate_whitelist_relaxation(
        self,
        title: str,
        company_name: str = "",
        tags: list[str] | None = None,
        digest: str = "",
    ) -> tuple[bool, str]:
        """Evaluate Whitelist Relaxation for a card that violated an App-Enforced Filter.

        Inspects the compact card facets (title, tags, company, digest) against the
        whitelist tokens, which encode subject matter the candidate cares deeply about
        or is strong in. Returns (is_relaxed: bool, matched_token: str); an empty
        whitelist simply means nothing can be relaxed. This method grants exemptions
        only — it never rejects, and non-matching jobs pass normal evaluation when no
        App-Enforced Filter was violated.
        """
        active_whitelist = [w.strip() for w in self.title_whitelist if w and w.strip()]
        if not active_whitelist:
            return False, ""

        facets = [
            (title or "").lower(),
            (company_name or "").lower(),
            " ".join(str(t).lower() for t in (tags or [])),
            (digest or "").lower(),
        ]
        for token in active_whitelist:
            normalized = token.lower()
            if any(normalized in facet for facet in facets):
                return True, token
        return False, ""

    def validate_can_blacklist_company(
        self,
        company_name: str,
        is_headhunter: bool = False,
    ) -> tuple[bool, str]:
        """Validate whether a company can safely be added to company_blacklist under guardrail rules.

        Returns (allowed: bool, notice: str).
        """
        if not company_name or not company_name.strip():
            return False, "公司名称不能为空"

        cleaned = company_name.strip()

        # Guardrail 1: Headhunter job posting's company name must not be blacklisted.
        # The two bases are reported distinctly: rejection triage reads cards, which
        # carry no recruiter title, so it can only ever trigger on the company name.
        if is_headhunter:
            return (
                False,
                f"【黑名单保护生效】岗位为猎头代招岗位，公司名称 '{cleaned}' 为聚合或代招渠道，禁止加入全局黑名单以避免误伤其他雇主",
            )
        if is_headhunter_agency_name(cleaned):
            return (
                False,
                f"【黑名单保护生效】'{cleaned}' 的企业名称表明其为人力资源/代招机构，禁止加入全局黑名单以避免误伤其代理的其他雇主",
            )

        # Guardrail 2: Masked / placeholder company name must not be blacklisted
        if is_masked_company_name(cleaned):
            return (
                False,
                f"【黑名单保护生效】'{cleaned}' 属于保密/占位公司名称（如某...公司），禁止加入全局黑名单以避免大范围误伤不相关企业",
            )

        return True, f"公司 '{cleaned}' 为真实直招企业，允许加入黑名单"

    def add_company_to_blacklist(
        self,
        company_name: str,
        is_headhunter: bool = False,
    ) -> tuple[bool, str]:
        """Attempt to add a company to company_blacklist with guardrail enforcement.

        Returns (success: bool, notice: str).
        """
        allowed, notice = self.validate_can_blacklist_company(
            company_name, is_headhunter=is_headhunter
        )
        if not allowed:
            return False, notice

        cleaned = company_name.strip()
        if cleaned not in self.company_blacklist:
            self.company_blacklist.append(cleaned)
            return (
                True,
                f"已成功将直招企业 '{cleaned}' 加入公司黑名单，后续该企业的岗位将自动过滤以节省每日投递额度",
            )
        return True, f"企业 '{cleaned}' 已在公司黑名单中"

    def remove_company_from_blacklist(self, company_name: str) -> bool:
        """Remove a company from company_blacklist."""
        cleaned = company_name.strip()
        if cleaned in self.company_blacklist:
            self.company_blacklist.remove(cleaned)
            return True
        return False

    def matches_card_keywords(
        self,
        title: str,
        company_name: str = "",
        tags: list[str] | None = None,
        digest: str = "",
        location: str = "",
    ) -> tuple[bool, str]:
        """Deterministic keyword evaluation for job card.

        Evaluates title, company_name, digest, and location against screening policy.
        Note: card tags are uncoupled from title and digest blacklists (#349, #350).
        Returns (passed: bool, reason: str).
        """
        if not self.enable_screening:
            return True, "筛选策略未启用，默认通过"

        norm_title = (title or "").lower()
        norm_company = (company_name or "").lower()
        norm_digest = (digest or "").lower()

        # 1. Check title blacklist (一票否决: 检查 title)
        for black in self.title_blacklist:
            b = black.strip().lower()
            if b and b in norm_title:
                return False, f"命中职位黑名单关键词: '{black}'"

        # 2. Check company blacklist (一票否决: 检查 company_name)
        for black in self.company_blacklist:
            b = black.strip().lower()
            if b and b in norm_company:
                return False, f"命中公司黑名单关键词: '{black}'"

        # 3. Check JD/Digest blacklist (一票否决: 检查 digest)
        for black in self.jd_blacklist:
            b = black.strip().lower()
            if b and b in norm_digest:
                return False, f"命中岗位摘要黑名单关键词: '{black}'"

        # 4. Business district blacklist (一票否决: 检查 location). The detail stage
        # re-evaluates this same list against the fuller location line (#333).
        location_pass, location_reason = self.evaluate_location_blacklist(location)
        if not location_pass:
            return False, location_reason

        # 白名单不再是准入闸门: 未命中白名单不拒绝卡片, 仅在 App 端强制过滤
        # 违例时由 evaluate_whitelist_relaxation 决定是否豁免放宽。
        return True, "通过卡片初筛"

    # Alias for API compatibility
    evaluate_card = matches_card_keywords

    def to_dict(self) -> dict[str, Any]:
        return {
            "title_whitelist": self.title_whitelist,
            "title_blacklist": self.title_blacklist,
            "company_blacklist": self.company_blacklist,
            "jd_blacklist": self.jd_blacklist,
            "business_district_blacklist": self.business_district_blacklist,
            "business_district_inspect_list": self.business_district_inspect_list,
            "enable_screening": self.enable_screening,
            "channel_preference": self.channel_preference,
            "max_commute_distance_km": self.max_commute_distance_km,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ScreeningPolicy:
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            title_whitelist=list(data.get("title_whitelist") or []),
            title_blacklist=list(data.get("title_blacklist") or []),
            company_blacklist=list(data.get("company_blacklist") or []),
            jd_blacklist=list(data.get("jd_blacklist") or []),
            business_district_blacklist=list(data.get("business_district_blacklist") or []),
            business_district_inspect_list=list(data.get("business_district_inspect_list") or []),
            enable_screening=bool(data.get("enable_screening", True)),
            channel_preference=normalize_channel_preference(
                data.get("channel_preference", ChannelPreference.ALL.value)
            ),
            # Absent key keeps the 40km default; an explicit null/blank means "disabled".
            max_commute_distance_km=cls._normalize_commute_limit(
                data["max_commute_distance_km"]
                if "max_commute_distance_km" in data
                else cls.__dataclass_fields__["max_commute_distance_km"].default
            ),
        )

    @classmethod
    def load_default(cls, config_path: str | Path | None = None) -> ScreeningPolicy:
        """Load ScreeningPolicy through the Configuration Realm chain."""
        from boss_agent.screening_config import load_screening_policy

        return load_screening_policy(config_path=config_path)

    def persist_company_blacklist(self, company_name: str) -> Path | None:
        """Write one blacklisted company back to this policy's active config file."""
        from boss_agent import screening_config

        return screening_config.persist_company_blacklist(self, company_name)

    def save_default(self, config_path: str | Path | None = None) -> Path:
        """Persist this policy to the declarative local YAML configuration file."""
        from boss_agent import screening_config

        return screening_config.save_policy(self, config_path=config_path)


def resolve_screening_policy(
    policy: ScreeningPolicy | None = None,
    *,
    channel_preference: Any = None,
) -> ScreeningPolicy:
    """The policy a strategy's channel preference produces. The input is never mutated.

    One rule, two callers, because a strategy's recruitment channel has to mean the same
    thing no matter who runs it: the worker feed pipeline
    (``FeedStreamConfig.from_payload``) and the interactive ``SmokeHarness`` both start
    from a preset's policy and then apply whatever the strategy itself stated. Resolved
    separately, the same preset screened one channel under a cron run and another under
    the local runner — the "same SavedSearch, two meanings" divergence this codebase has
    already paid for twice (PR #297, issue #302).

    A new policy is returned rather than the caller's being edited in place, because the
    caller's policy is often shared: ``SavedSearchRegistry.get`` hands back the object it
    stores, so mutating it would overwrite the global screening configuration for the
    rest of the process. A strategy that states nothing — absent, empty or unrecognized —
    yields the policy unchanged, which is the whole meaning of "inherit global".
    """
    if policy is None:
        policy = ScreeningPolicy.load_default()
    stated = normalize_channel_preference(channel_preference, default=INHERIT_CHANNEL)
    if not stated:
        return policy
    return replace(policy, channel_preference=stated)
