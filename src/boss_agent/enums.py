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


#: How a task payload answered the one depth question (issue #302), recorded so a run can
#: say which shape it read. Once nothing reports `legacy_*`, the legacy reading goes away.
DEPTH_DECLARED = "declared_target_action"
DEPTH_LEGACY_PAIR = "legacy_preview_pair"
DEPTH_LEGACY_HALF_PAIR = "legacy_preview_half_pair"
DEPTH_UNSTATED = "unstated_default"

STATE_RANK: dict[str, int] = {
    JobRecordStatus.IGNORED: -1,
    JobRecordStatus.DIGEST_ONLY: 1,
    JobRecordStatus.JD_SAVED: 1,
    JobRecordStatus.UNMATCHED: 1,
    JobRecordStatus.MATCHED: 2,
    JobRecordStatus.APPLIED: 3,
}

#: The state rank each Target Action requires. This is the Depth Visit Rule's table:
#: 深度存JD is satisfied by a record that has reached the enrichment rung, while
#: 自动打招呼 is satisfied only at `applied` — a greeting that actually left the app.
#: `matched` used to sit at this action's rank, which is how an undelivered draft read as
#: finished work and was never revisited (issue #299).
TARGET_ACTION_RANK: dict[str, int] = {
    TargetAction.SAVE_JD: 1,
    TargetAction.AUTO_APPLY: STATE_RANK[JobRecordStatus.APPLIED],
}


def depth_already_reached(
    target_action: TargetAction, existing_status: str, existing_record: dict | None
) -> bool:
    """Whether a stored record has already met the depth this run is configured for.

    The two depths ask different questions, and the Job Lifecycle ladder answers only one of
    them. `STATE_RANK` orders a record by how much is *known* about it, which is the right
    guard for writes and for a 深度存JD sweep. It is the wrong guard for 自动打招呼, whose
    requirement is a delivered message: comparing ranks there counted a draft — `matched` —
    as finished work, and every undelivered greeting the backend or a spent quota produced
    was skipped forever after (issue #299).
    """
    rank = STATE_RANK.get(existing_status, 0)
    required = TARGET_ACTION_RANK.get(target_action, STATE_RANK[JobRecordStatus.JD_SAVED])
    if rank < required:
        return False
    if target_action != TargetAction.SAVE_JD:
        return True
    # A save-only pass is satisfied by enrichment itself, so a record that got as far as a
    # draft has plainly been read; one that never produced a JD is not done yet.
    return rank > required or bool(((existing_record or {}).get("job_description") or "").strip())
