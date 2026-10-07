"""
boss_agent.pages
================
Feature-centric page objects behind one backward-compatible facade (spec #395, ticket #398).

The monolith this package replaced held every screen in one 2,015-line file, so a chat-triage run
loaded the job-feed and filter-dialog adapters with it and a job-feed change shared a file with
rejection-descriptor parsing. The code is now partitioned by feature:

* :mod:`boss_agent.pages.base` -- `BaseBossPage`, selector telemetry, logging helpers, and the
  hardware-Back cadence constant every screen recovers with.
* :mod:`boss_agent.pages.system` -- startup dialog clearance and login readiness.
* :mod:`boss_agent.pages.job_feed` -- `JobListPage`, `SearchPage`, the two filter dialogs.
* :mod:`boss_agent.pages.job_detail` -- `JobDetailPage` and the commute-distance probe.
* :mod:`boss_agent.pages.communication` -- the 仅沟通 list, `ChatPage`, and `CommunicationCard`.

This module re-exports every name the monolith *defined* (plus the one domain type callers reach
through it), so every existing ``from boss_agent.pages import X`` -- in `feed_pipeline`,
`chat_triage`, `workflows`, the worker handlers, and the unit suite -- keeps resolving, and the
re-exported objects are the *same* objects the owning submodule holds rather than copies. The
monolith's wider attribute namespace did not survive the split; see the narrowing note below.

Consumers should migrate to the submodule they actually need: importing
``boss_agent.pages.communication`` no longer pulls in job discovery or filter dialogs. That
migration is tracked in the follow-up tickets of spec #395; until it completes, this facade is the
supported entry point.

**A deliberate narrowing, not an oversight.** The monolith bound 65 module-level names: 35 of its
own definitions plus 30 it merely imported. This facade re-exports the 35 and ``JobCardBrief``, so
36 names resolve and 29 do not. The unreachable ones are exactly the names a caller could only have
reached by accident — ``parse_card``, ``wait_until``, ``classify_chat_button``, ``AuthStatus``,
``JobPosting``, ``FilterConfig`` and their like, plus the stdlib and typing that leaked through it
(``time``, ``logging``, ``re``, ``hashlib``, ``dataclass``, ``Any``, ``Callable``, ``Point``,
``Console``). Each has a real owner in the tree (``boss_agent.card_parser``,
``boss_agent.identifier_helpers``, ``boss_agent.enums``, ``boss_agent.job_entities``,
``boss_agent.search_entities``), and every caller in this repo reaches them there; none reaches them
through the facade.

Do not "fix" the gap by re-adding the imports. An unused import on a facade is not inert — it
executes the module it names, which is precisely the cost the split removed: a 仅沟通 chat-triage
run paying for job-discovery adapters on every dispatch. A caller that needs one of these names
should import it from the module that defines it, which is narrower than the facade and free.

``logger`` is re-exported under its original name, ``boss_agent.pages``, because that string is a
public observability contract: log greps and string-targeted patches such as
``patch("boss_agent.pages.logger.error")`` address the channel by that name and must keep working.
"""

# Re-exported so callers that reach domain types *through* `pages` -- the three unit modules
# importing `JobCardBrief` today -- keep working across the split.
from ..job_entities import JobCardBrief
from .base import (
    BACK_INTERVAL_SEC,
    BaseBossPage,
    _log_error,
    _log_info,
    _log_selector_lookup,
    _log_warn,
    console,
    logger,
    ui_logger,
)
from .communication import (
    BACK_BUTTON_KEY,
    CARD_CHILD_TEXT_XPATH,
    CARD_DESCRIPTOR_SEPARATOR,
    FULLWIDTH_CARD_DESCRIPTOR_SEPARATOR,
    LIST_RECOVERY_MAX_STEPS,
    OUTBOUND_STATUS_MARKERS,
    ChatPage,
    CommunicationCard,
    CommunicationListPage,
    _normalize_card_descriptors,
    _whitespace_digest,
    compute_card_key,
    parse_company_from_descriptor,
)
from .job_detail import (
    COMMUTE_DISTANCE_PATTERN,
    COMMUTE_PROBE_MAX_SCROLLS,
    COMMUTE_PROBE_SCROLL_STRIDE_RATIO,
    COMMUTE_PROBE_STALLED_SWIPES,
    JobDetailPage,
)
from .job_feed import (
    FILTER_OPTION_SYNONYMS,
    FilterDialogPage,
    IndustryFilterDialogPage,
    JobListPage,
    LocatedJobCard,
    SearchPage,
)
from .system import LoginPage, StartupDialogPage

__all__ = [
    # base
    "BACK_INTERVAL_SEC",
    "BaseBossPage",
    "_log_error",
    "_log_info",
    "_log_selector_lookup",
    "_log_warn",
    "console",
    "logger",
    "ui_logger",
    # system
    "LoginPage",
    "StartupDialogPage",
    # job_feed
    "FILTER_OPTION_SYNONYMS",
    "FilterDialogPage",
    "IndustryFilterDialogPage",
    "JobListPage",
    "LocatedJobCard",
    "SearchPage",
    # job_detail
    "COMMUTE_DISTANCE_PATTERN",
    "COMMUTE_PROBE_MAX_SCROLLS",
    "COMMUTE_PROBE_SCROLL_STRIDE_RATIO",
    "COMMUTE_PROBE_STALLED_SWIPES",
    "JobDetailPage",
    # communication
    "BACK_BUTTON_KEY",
    "CARD_CHILD_TEXT_XPATH",
    "CARD_DESCRIPTOR_SEPARATOR",
    "FULLWIDTH_CARD_DESCRIPTOR_SEPARATOR",
    "LIST_RECOVERY_MAX_STEPS",
    "OUTBOUND_STATUS_MARKERS",
    "ChatPage",
    "CommunicationCard",
    "CommunicationListPage",
    "_normalize_card_descriptors",
    "_whitespace_digest",
    "compute_card_key",
    "parse_company_from_descriptor",
    # domain types reached through this facade
    "JobCardBrief",
]
