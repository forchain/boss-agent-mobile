"""
boss_agent.enums
================
Domain enumerations and rank mappings for the Boss Application Layer (Issue #311, Spec #303).
"""

from __future__ import annotations

from enum import StrEnum


class AuthStatus(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"  # Captcha or SMS challenge


class JobRecordStatus(StrEnum):
    IGNORED = "ignored"
    JD_SAVED = "jd_saved"
    UNMATCHED = "unmatched"
    MATCHED = "matched"
    APPLIED = "applied"
    # Backward compatibility for legacy database records
    DIGEST_ONLY = "digest_only"


class TargetAction(StrEnum):
    SAVE_JD = "save_jd"
    AUTO_APPLY = "auto_apply"


#: SavedSearch target_action value for inbox cleanup strategies.
CHECK_CHAT_ACTION: str = "check_chat"


class TargetTaskType(StrEnum):
    """Worker task a strategy dispatches. `CHECK_CHAT` is deliberately outside
    :class:`TargetAction`: it is keyword-independent inbox cleanup, not a search
    depth, so it never takes part in the target-action rank ladder."""

    SCRAPE_JOBS = "SCRAPE_JOBS"
    AUTO_APPLY = "AUTO_APPLY"
    CHECK_CHAT = "CHECK_CHAT"


class ChatButtonState(StrEnum):
    """Engagement state reported by the detail page call-to-action button (`btn_chat`)."""

    UNCONTACTED = "uncontacted"
    COMMUNICATED = "communicated"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class ChannelPreference(StrEnum):
    """Recruitment channel target for App-Enforced Filters (Boss offers no native filter)."""

    ALL = "all"
    DIRECT_ONLY = "direct_only"
    HEADHUNTER_ONLY = "headhunter_only"


STATE_RANK: dict[str, int] = {
    JobRecordStatus.IGNORED: -1,
    JobRecordStatus.DIGEST_ONLY: 1,
    JobRecordStatus.JD_SAVED: 1,
    JobRecordStatus.UNMATCHED: 1,
    JobRecordStatus.MATCHED: 2,
    JobRecordStatus.APPLIED: 3,
}

TARGET_ACTION_RANK: dict[str, int] = {
    TargetAction.SAVE_JD: 1,
    TargetAction.AUTO_APPLY: 2,
}
