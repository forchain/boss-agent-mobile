"""
boss_agent.core.chat.domain.triage
===================================
The triage run's vocabulary: why it stopped, what it did to one card, and the report
the caller maps into task telemetry.
"""

from dataclasses import dataclass
from enum import StrEnum

#: Badge texts the platform renders in `iv_msg_status` for a thread whose last
#: message is the candidate's own or an unsent draft (spec #205). Matched explicitly
#: rather than keyed on the node's mere presence: a future badge with different wording
#: would otherwise silently stop every rejection from being detected.
OUTBOUND_STATUS_MARKERS: tuple[str, ...] = ("送达", "已读", "草稿")


class StopReason(StrEnum):
    """Why a triage run stopped.

    The first seven are the scan's own exits. ``LIST_UNREACHABLE`` is the entry
    failure that precedes them: the list could not be opened, so no card was read.
    """

    UNREAD_CLEARED = "unread_cleared"
    EMPTY_LIST = "empty_list"
    CANCELLED = "cancelled"
    MAX_SCAN_DEPTH = "max_scan_depth"
    SCAN_CEILING = "scan_ceiling"
    SCROLL_CEILING = "scroll_ceiling"
    LOST_LIST = "lost_list"
    LIST_UNREACHABLE = "list_unreachable"


class TriageKind(StrEnum):
    """What triage did with one communication card."""

    #: Outbound Message Indicator present: waiting on the recruiter, never touched.
    SKIPPED = "skipped"
    PRESERVED = "preserved"
    ACKNOWLEDGED = "acknowledged"
    DRY_RUN = "dry_run"
    FAILED = "failed"


@dataclass(frozen=True)
class TriageOutcome:
    """Result of triaging one card, tallied into the run's report."""

    kind: TriageKind
    is_rejection: bool = False
    #: Set when the employer was newly added to the company blacklist.
    blacklisted: bool = False
    #: Set when a guardrail (headhunter agency / masked name) refused the employer.
    guardrail_blocked: bool = False
    navigated: bool = False
    #: Set when the platform never returned to the list; the scan must stop rather
    #: than read cards off an unknown screen.
    lost_list: bool = False


@dataclass(frozen=True)
class TriageReport:
    """What one triage run found and did, for the caller's task telemetry."""

    stop_reason: StopReason
    dry_run: bool = False
    scanned: int = 0
    evaluated: int = 0
    skipped_outbound: int = 0
    acknowledged: int = 0
    preserved: int = 0
    failed: int = 0
    rejections: int = 0
    guardrail_blocked: int = 0
    blacklisted_companies: tuple[str, ...] = ()
    visited_keys: frozenset[str] = frozenset()
    scroll_swipes: int = 0

    @property
    def blacklisted_count(self) -> int:
        """How many companies this run newly added to the blacklist."""
        return len(self.blacklisted_companies)

    def summary_line(self) -> str:
        """The operator-facing summary of this run, rendered by the dashboard."""
        note = f" [{', '.join(self.blacklisted_companies)}]" if self.blacklisted_companies else ""
        return (
            f"Finished CHECK_CHAT: scanned {self.scanned} card(s), "
            f"scrolled {self.scroll_swipes} swipe(s), "
            f"evaluated {self.evaluated} message(s), "
            f"{self.skipped_outbound} skipped as outbound, "
            f"{self.rejections} rejection(s) detected, "
            f"{self.blacklisted_count} company(ies) blacklisted{note}, "
            f"{self.guardrail_blocked} blocked by guardrails, "
            f"{self.acknowledged} acknowledged, "
            f"{self.preserved} preserved, "
            f"{self.failed} failed "
            f"(stop_reason={self.stop_reason.value}, dry_run={self.dry_run})"
        )
