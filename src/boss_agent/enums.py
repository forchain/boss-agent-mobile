"""
boss_agent.enums
================
Domain enumerations and rank mappings for the Boss Application Layer (Issue #311, Spec #303).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


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


class ScreeningStage(StrEnum):
    """Which screening stage produced a terminal ``ignored`` verdict.

    Persisted as ``JobRecord.screening_stage`` so a reader can label a rejection by the
    stage that actually made it. Every rejection used to be written as the same
    ``ignored`` + ``screened_reason`` pair, so the dashboard showed all of them as
    初筛淘汰 — even the deep screener's, whose evidence lives in the full JD and never in
    the card digest the card stage sees. Values reuse the verdict-stage vocabularies in
    :mod:`boss_agent.screening` where they overlap.
    """

    #: Card stage (no JD read): a keyword/company blacklist hit.
    CARD_KEYWORD = "filtered_by_keyword"
    #: Card stage: an App-Enforced Filter violation (e.g. business-district blacklist).
    CARD_APP_RULE = "filtered_by_app_rule"
    #: Full-JD semantic blacklist screening — the 「精筛」 stage.
    DEEP_SCREENER = "filtered_by_deep_screener"
    #: Detail stage: an App-Enforced Filter (commute ceiling / district) after the JD.
    DETAIL_APP_RULE = "detail_app_rule"
    #: The posting was closed / stopped hiring.
    EXPIRED = "expired_posting"


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


#: The channel a *strategy* states when it names none: defer to the global setting
#: (issue #368). Not a ``ChannelPreference`` member, because "no preference of my own" is
#: a state the wire has to be able to express and the screening rules have no use for.
INHERIT_CHANNEL = ""


def normalize_channel_preference(value: Any, default: str = ChannelPreference.ALL.value) -> str:
    """Coerce a channel preference to a valid value, or to ``default`` if unrecognized.

    One coercion with two defaults, because the two callers need genuinely different
    ones and neither can be derived from the other: a *policy* that is never configured
    must be restrictive enough to matter (``all`` — a screening rule with no target
    would be inert), while a *strategy* that says nothing must defer to whatever the
    operator configured globally (``INHERIT_CHANNEL``). Reading an unknown strategy value
    as ``all`` would silently widen a search to every channel — the one outcome an
    operator who typed a preference would never expect.
    """
    try:
        return ChannelPreference(str(value or "").strip().lower()).value
    except ValueError:
        return default


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
