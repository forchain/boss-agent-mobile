"""
boss_agent.core.job.domain.feed_stream
======================================
The declarative vocabulary one feed run speaks: its config, its result and its
per-card outcome (ADR 0013, Spec #231).

Everything here is data. ``FeedStreamConfig.from_payload`` parses a worker task payload
into that vocabulary; it takes the operator's resolved defaults as explicit arguments
(Screening Policy, greeting ceiling, cool-down window) rather than reading YAML or the
environment mid-construction. The edge that owns the task payload resolves those and
passes them in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from boss_agent.core.candidate.domain.candidate_profile import CandidateProfile
from boss_agent.core.job.domain.job_entities import JobPosting
from boss_agent.core.job.domain.screening_policy import (
    ScreeningPolicy,
    resolve_screening_policy,
)
from boss_agent.core.search.domain.models import FilterConfig
from boss_agent.core.shared.enums import (
    DEPTH_DECLARED,
    DEPTH_LEGACY_HALF_PAIR,
    DEPTH_LEGACY_PAIR,
    DEPTH_UNSTATED,
    TargetAction,
)
from boss_agent.core.shared.keyword_constants import DEFAULT_COMMUNICATION_COOLDOWN_DAYS
from boss_agent.core.task.domain.models import TaskType

logger = logging.getLogger(__name__)

DEFAULT_MAX_JOBS = 3


class JobAction(StrEnum):
    """What the pipeline did with a card once it reached a verdict."""

    SAVED = "saved"
    APPLIED = "applied"
    # Drafted, persisted, deliberately not sent this round — the record stays
    # re-sendable. Issue #298 renamed this from `offline_draft`: "offline draft" read
    # like a terminal product tier, when the intent is "not delivered yet".
    PENDING_SEND = "pending_send"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class JobOutcome:
    """Per-card report handed to the caller's ``on_job`` observer."""

    fingerprint: str
    title: str
    company_name: str
    status: str
    action: JobAction
    reason: str = ""
    score: int = 0
    greeting_message: str = ""
    record: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedStreamResult:
    """Aggregate outcome of one feed streaming run."""

    outcome: str = "no_candidates"
    reason: str = ""
    scanned: int = 0
    processed: int = 0
    skipped: int = 0
    jobs: list[dict[str, Any]] = field(default_factory=list)
    #: The postings this run actually extracted, typed instead of as store records.
    #: ``jobs`` speaks the store's vocabulary — its keys are private to the persistence
    #: layer and cannot change while the worker handlers read them — so a caller outside
    #: the worker had no way to read an extraction without hardcoding those keys
    #: (issue #389). These are what the platform screens with, and they exist only once a
    #: detail page actually yielded one: a card whose detail read failed still sits in
    #: ``jobs`` as itself, and a card the run withdraws at the detail stage leaves neither.
    postings: list[JobPosting] = field(default_factory=list)
    applied: bool = False
    applied_count: int = 0
    score: int = 0
    greeting_message: str = ""
    jd_key_requirements: list[str] = field(default_factory=list)
    job: dict[str, Any] | None = None
    cancelled: bool = False
    search_failed: bool = False
    boundary_reached: bool = False
    quota_exhausted: bool = False
    error_message: str | None = None


@dataclass
class FeedStreamConfig:
    """Declarative description of one feed run, parsed from a task payload."""

    target_action: TargetAction = TargetAction.SAVE_JD
    keyword: str | None = None
    max_jobs: int = DEFAULT_MAX_JOBS
    enable_search: bool = True
    # Filtering is switched off on the filter config itself, which owns the flag.
    filter_config: FilterConfig | None = None
    screening_policy: ScreeningPolicy = field(default_factory=ScreeningPolicy)
    cooldown_days: int = 0
    daily_greeting_limit: int = 20
    min_score: float = 70.0
    # The one gate the dispatch path reads: does this run put a greeting on the wire?
    # ``depth_expression`` records how the payload answered that, so a log line can say
    # whether it was read from the single declared intent or from the legacy pair an older
    # builder left in the queue (issue #302).
    send_greeting: bool = False
    depth_expression: str = DEPTH_UNSTATED
    # True when the payload itself stated a Target Action rather than being inferred from
    # the task type. A payload that states no depth at all keeps the default it always had:
    # nothing goes out.
    states_target_action: bool = False
    # Set when the payload was read through a shape producers are no longer allowed to
    # write. The run says so in its own log instead of failing the queued task.
    depth_warning: str = ""
    # A payload written before issue #428 can still name one posting through
    # ``direct_job_id``, and the broker is PocketBase-backed, so such a task can be
    # sitting in a queue when this code is deployed. No producer writes the key any more
    # and no execution path can act on it, so ``stream_jobs`` refuses the run outright
    # rather than reading the same payload as an ordinary keyword search — which would
    # turn "greet the one posting on screen" into a quota-consuming sweep of the board.
    retired_direct_target: bool = False
    candidate_profile: CandidateProfile | None = None
    source_task_id: str | None = None
    is_headhunter: bool | None = None

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any] | None,
        *,
        task_type: TaskType | str | None = None,
        default_policy: ScreeningPolicy | None = None,
        default_daily_greeting_limit: int = 20,
        default_cooldown_days: int = DEFAULT_COMMUNICATION_COOLDOWN_DAYS,
    ) -> FeedStreamConfig:
        """Parse the worker task payload this pipeline and both handlers share.

        ``task_type`` names the handler the task was routed to. Passing it here is what
        keeps depth answered once, in one place: the handlers used to overwrite
        ``target_action`` *after* parsing, so the config could hold a Target Action that
        disagreed with the ``send_greeting`` derived from the payload — the same "two
        answers to one question" shape #302 removes from the wire, reintroduced as mutable
        state one layer up.

        ``default_policy``, ``default_daily_greeting_limit`` and ``default_cooldown_days``
        are the operator's already-resolved values. The edge that owns configuration passes
        them; nothing here reads a file or the environment.
        """
        data = payload or {}
        target_action_val = data.get("target_action") or data.get("action")
        target_action = TargetAction.SAVE_JD
        if target_action_val:
            try:
                target_action = TargetAction(str(target_action_val).lower())
            except ValueError:
                target_action = TargetAction.SAVE_JD
        elif task_type is not None:
            # The task type names the action when the payload does not. Only an *absent*
            # Target Action is inferred this way: a payload that states one keeps it, so
            # this can never promote a save-only run into an outreach run.
            resolved_type = task_type if isinstance(task_type, TaskType) else TaskType(task_type)
            if resolved_type == TaskType.AUTO_APPLY:
                target_action = TargetAction.AUTO_APPLY

        raw_filter = data.get("filter")
        filter_config = (
            FilterConfig(
                education=raw_filter.get("education"),
                salary=raw_filter.get("salary"),
                experience=raw_filter.get("experience"),
                activity=raw_filter.get("activity"),
                company_scales=raw_filter.get("company_scales", []),
                industries=raw_filter.get("industries", []),
                channel_preference=raw_filter.get("channel_preference", ""),
                enable_filter=bool(data.get("enable_filter", True)),
            )
            if isinstance(raw_filter, dict)
            else None
        )

        # ---- the one depth question, answered once -------------------------------
        # A new payload states depth only as ``target_action``. Anything that still carries
        # the legacy keys is read the way the worker that received it read it, because a
        # task sitting in somebody's queue must not change meaning — or fail outright —
        # because the code underneath it was upgraded.
        legacy_preview = data.get("preview_only")
        legacy_auto_send = data.get("auto_send")
        legacy_half_warning = ""
        states_target_action = bool(data.get("target_action") or data.get("action"))
        if (legacy_preview is None) != (legacy_auto_send is None):
            # A documented historical shape: PR #297's own table shows the dashboard's
            # "run scheduled now" hand-building `preview_only: true` with no `auto_send`.
            # Read with the defaults that worker applied — `preview_only` defaulted True,
            # `auto_send` False, so a half-written pair was always a draft — and say so,
            # loudly, instead of letting it pass as an ordinary run.
            stated = "preview_only" if legacy_preview is not None else "auto_send"
            missing = "auto_send" if legacy_preview is not None else "preview_only"
            legacy_preview = True
            legacy_auto_send = False
            send_greeting = False
            depth_expression = DEPTH_LEGACY_HALF_PAIR
            legacy_half_warning = (
                f"task payload states `{stated}` without `{missing}` — read as a draft with "
                f"the defaults an older worker applied. Depth is one expression now: state "
                f"`target_action` (issue #302)."
            )
        elif legacy_preview is None and legacy_auto_send is None:
            # Single-expression shape (or a payload that states no depth at all, in which
            # case the Target Action default of save_jd keeps it a non-sending run — the
            # behaviour such a payload always had).
            send_greeting = target_action == TargetAction.AUTO_APPLY and states_target_action
            depth_expression = DEPTH_DECLARED if states_target_action else DEPTH_UNSTATED
        else:
            send_greeting = bool(legacy_auto_send) and not bool(legacy_preview)
            depth_expression = DEPTH_LEGACY_PAIR

        # ---- a retired execution path, read through a key nobody writes any more ----
        # `direct_job_id` named the one posting a targeted application greeted on screen.
        # Issue #428 retired that path, so the key is now unreadable by anything: the
        # config keeps no field to hold it and no code path can act on it. A producer
        # stopped writing it at the same time, but the queue outlives the deployment —
        # ``BaseTaskBroker`` is PocketBase-backed, so a task written by the pre-#428
        # builder can still be delivered to this worker. Such a payload also carries
        # `keyword` (the target's title) and `target_action=auto_apply`, so with nothing
        # gating it the run would fall into the keyword sweep below: "greet the one
        # posting on screen" silently becoming "sweep the board and greet up to the
        # ceiling", which spends real quota on a task the operator never read that way.
        legacy_direct_job_id = data.get("direct_job_id")
        retired_direct_target = bool(legacy_direct_job_id)
        retired_target_warning = ""
        if retired_direct_target:
            retired_target_warning = (
                f"task payload states `direct_job_id={legacy_direct_job_id}` — the "
                f"single-screen targeted-application path is retired (issues #426-#428). "
                f"The run is refused rather than read as a keyword search for "
                f"`{data.get('keyword')}`, which would greet postings this task never "
                f"named. Re-launch it from a Saved Search to sweep by keyword."
            )

        base_default_policy = default_policy if default_policy is not None else ScreeningPolicy()
        raw_policy = data.get("screening_policy")
        daily_limit = int(data.get("daily_greeting_limit") or default_daily_greeting_limit)

        if not raw_policy or not isinstance(raw_policy, dict):
            base_policy = base_default_policy
        elif raw_policy.get("enable_screening") is False:
            base_policy = ScreeningPolicy.from_dict(raw_policy)
        else:
            parsed_policy = ScreeningPolicy.from_dict(raw_policy)
            title_bl = (
                parsed_policy.title_blacklist
                if raw_policy.get("title_blacklist")
                else base_default_policy.title_blacklist
            )
            company_bl = (
                parsed_policy.company_blacklist
                if raw_policy.get("company_blacklist")
                else base_default_policy.company_blacklist
            )
            title_wl = (
                parsed_policy.title_whitelist
                if raw_policy.get("title_whitelist")
                else base_default_policy.title_whitelist
            )
            jd_bl = (
                parsed_policy.jd_blacklist
                if raw_policy.get("jd_blacklist")
                else base_default_policy.jd_blacklist
            )
            dist_bl = (
                parsed_policy.business_district_blacklist
                if raw_policy.get("business_district_blacklist")
                else base_default_policy.business_district_blacklist
            )
            dist_inspect = (
                parsed_policy.business_district_inspect_list
                if raw_policy.get("business_district_inspect_list")
                else base_default_policy.business_district_inspect_list
            )
            commute_km = (
                parsed_policy.max_commute_distance_km
                if "max_commute_distance_km" in raw_policy
                else base_default_policy.max_commute_distance_km
            )
            channel_pref = (
                parsed_policy.channel_preference
                if "channel_preference" in raw_policy
                else base_default_policy.channel_preference
            )
            base_policy = replace(
                base_default_policy,
                title_blacklist=title_bl,
                company_blacklist=company_bl,
                title_whitelist=title_wl,
                jd_blacklist=jd_bl,
                business_district_blacklist=dist_bl,
                business_district_inspect_list=dist_inspect,
                enable_screening=parsed_policy.enable_screening,
                channel_preference=channel_pref,
                max_commute_distance_km=commute_km,
            )
        # A strategy may pin its own recruitment channel; saying nothing means inherit the
        # policy resolved above (issue #368). The rule itself belongs to
        # ``resolve_screening_policy``, which the local Feed Verification run also goes
        # through — two callers, one answer.
        screening_policy = resolve_screening_policy(
            base_policy,
            channel_preference=_strategy_channel_preference(data.get("filter")),
        )

        return cls(
            target_action=target_action,
            keyword=data.get("keyword"),
            max_jobs=int(data.get("max_jobs", DEFAULT_MAX_JOBS)),
            enable_search=bool(data.get("enable_search", True)),
            filter_config=filter_config,
            screening_policy=screening_policy,
            cooldown_days=_coerce_cooldown_days(
                data.get("communication_cooldown_days"), default_cooldown_days
            ),
            daily_greeting_limit=daily_limit,
            min_score=float(data.get("min_score", 70)),
            source_task_id=data.get("source_task_id"),
            send_greeting=send_greeting,
            states_target_action=states_target_action,
            depth_expression=depth_expression,
            # A payload can be wrong about both depth *and* the retired target, so the two
            # warnings are joined rather than one silently overwriting the other.
            depth_warning=" ".join(w for w in (legacy_half_warning, retired_target_warning) if w),
            retired_direct_target=retired_direct_target,
            is_headhunter=data.get("is_headhunter"),
        )


def _coerce_cooldown_days(raw: Any, default: int) -> int:
    """The payload's cool-down override, or the edge-resolved default.

    An invalid value falls back to the built-in default rather than the resolved one,
    exactly as ``resolve_communication_cooldown_days`` always did: a corrupt payload must
    not silently inherit whatever the machine happens to be configured with.
    """
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid communication_cooldown_days value %r; falling back to default %d",
            raw,
            DEFAULT_COMMUNICATION_COOLDOWN_DAYS,
        )
        return DEFAULT_COMMUNICATION_COOLDOWN_DAYS


def _strategy_channel_preference(raw_filter: Any) -> str:
    """The recruitment channel a task's strategy pinned, or '' for "inherit global".

    Read the same two shapes ``SavedSearch.from_dict`` reads for the same PocketBase
    column, so a task queued before the value was normalized cannot resolve a different
    channel than the preset it was launched from. The value is normalized by the caller
    (``resolve_screening_policy``) rather than here — this only says where in the payload
    the answer lives, and the two halves of the question are not allowed to disagree.
    """
    if isinstance(raw_filter, str):
        try:
            import json

            raw_filter = json.loads(raw_filter)
        except Exception:
            return ""
    if not isinstance(raw_filter, dict):
        return ""
    return raw_filter.get("channel_preference", "") or ""
