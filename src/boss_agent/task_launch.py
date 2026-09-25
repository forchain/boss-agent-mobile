"""
boss_agent.task_launch
======================
One owner for the ``AutomationTask`` payload contract.

The payload is the wire contract between the Web Dashboard / Automation Scheduler /
Startup Rejection Cleanup Barrier on one side and the Task Handler Strategy on the
other. It had no owner: at least seven builders produced it with already-diverged
semantics, and nothing validated it.

The divergences were not cosmetic. ``min_score`` defaulted to 75 in the launch modal
and 70 in the scheduler, and was simply absent from the dashboard's "run scheduled
now" button. ``preview_only`` was ``True`` from every web builder and ``False`` from
the scheduler, so the *same* SavedSearch produced inverted execution depth depending
on who dispatched it. And task provenance — a first-class CONTEXT.md attribute with
defined ``manual``/``test``/``scheduler`` semantics — existed nowhere in code, faked
instead with payload markers like ``startup_cleanup`` and ``scheduled``.

This module turns ``(kind, provenance, search, mode)`` into a validated payload. The
defaults are declared once, the wire keys stay stable for rollout, and
``config/task_launch.cases.json`` pins the output so the TypeScript builder cannot
drift from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .broker.collection_schema import SAVED_SEARCH_MAX_JOBS
from .broker.models import TaskType
from .models import SavedSearch, TargetAction, TargetTaskType
from .settings import resolve_chat_acknowledgment_settings

#: The baseline relevance threshold. One value, so a manual launch and a scheduled run
#: of the same SavedSearch cannot disagree about which jobs qualify.
MIN_SCORE = 70

#: Baseline job ceiling for a search dispatch. An alias, not a restatement: the
#: Collection Schema declares the `saved_searches.max_jobs` default, and a second
#: literal here is how 20-vs-30 drifted apart in the first place.
DEFAULT_MAX_JOBS = SAVED_SEARCH_MAX_JOBS

#: Seconds the worker previews a drafted greeting before moving on.
DEFAULT_PREVIEW_TIMEOUT_SEC = 3.0


class LaunchSource(StrEnum):
    """Task Provenance — where a task came from, as CONTEXT.md defines it.

    A real attribute on the task record rather than a payload marker: startup
    reclamation cancels ``TEST`` tasks so an automated suite never contends with the
    live worker for the device, and the dashboard shows the rest.
    """

    MANUAL = "manual"
    TEST = "test"
    SCHEDULER = "scheduler"


class LaunchMode(StrEnum):
    """How deep a launch is allowed to go.

    Two modes, not three: "dry run" and "draft" were the same wire intent expressed in
    two vocabularies — compute it, show it, do not send it — and collapsing them is the
    whole point of having one builder. ``None`` is a third, distinct state: *honour the
    configured* ``chat.dry_run``, which is what the searches-page trigger has always
    meant by omitting the key.
    """

    DRAFT = "draft"
    LIVE = "live"


class TaskKind(StrEnum):
    """The Task Handler Strategy a launch targets."""

    SEARCH = "search"
    CHAT_CLEANUP = "chat_cleanup"
    LOGIN_DIAGNOSTIC = "login_diagnostic"


class LaunchContractError(ValueError):
    """A launch request the worker could not honour as written."""


def _target_action_for(search: SavedSearch) -> TargetAction:
    """The SavedSearch's configured execution depth, resolved the same way everywhere.

    A malformed `target_action` is rejected here rather than silently defaulting: a
    typo used to fall through to `SAVE_JD`, quietly turning a greeting run into a
    save-only pass.
    """
    raw = search.target_action or (
        TargetAction.AUTO_APPLY
        if search.target_task_type == TargetTaskType.AUTO_APPLY
        else TargetAction.SAVE_JD
    )
    try:
        action = TargetAction(raw)
    except ValueError as exc:
        raise LaunchContractError(
            f"SavedSearch {search.id!r} declares an unknown target_action {raw!r}; "
            f"expected one of {[a.value for a in TargetAction]}"
        ) from exc
    if action not in (TargetAction.AUTO_APPLY, TargetAction.SAVE_JD):
        raise LaunchContractError(
            f"SavedSearch {search.id!r} declares an unsupported target_action {raw!r}; "
            f"expected one of {[a.value for a in TargetAction]}"
        )
    return action


@dataclass(frozen=True)
class TaskLaunch:
    """A validated launch: the task type, the payload, and where it came from.

    Provenance travels as a *task attribute*, not a payload key: the worker's startup
    sweep and the dashboard both need to read it, and neither should have to do payload
    archaeology for a field the record already has a column for.
    """

    task_type: TaskType
    payload: dict[str, Any] = field(default_factory=dict)
    source: LaunchSource = LaunchSource.MANUAL


def build_search_launch(
    search: SavedSearch,
    *,
    source: LaunchSource,
    mode: LaunchMode | None = LaunchMode.DRAFT,
    candidate_profile: dict[str, Any] | None = None,
    min_score: int | None = None,
) -> TaskLaunch:
    """Launch a saved search as a scrape or an auto-apply run.

    ``min_score`` is a caller *choice*, not a competing default: the parameter exists so
    the modal can offer a stricter threshold than the baseline, and the baseline is what
    everyone else gets. Leaving it unset used to mean three different numbers.
    """
    action = _target_action_for(search)
    task_type = (
        TaskType.AUTO_APPLY if action == TargetAction.AUTO_APPLY else TaskType.SCRAPE_JOBS
    )

    # A save-only search never greets, so its preview flags are not a caller choice.
    preview_only, auto_send = _preview_flags(action, mode)

    search_dict = search.to_dict()
    payload: dict[str, Any] = {
        "saved_search_id": search.id,
        "search_id": search.id,
        "search_name": search.name,
        "keyword": search_dict.get("keyword") or "",
        "enable_search": search.enable_search,
        "enable_filter": search.enable_filter,
        "filter": search_dict.get("filter") or {},
        "target_action": action.value,
        "max_jobs": search.max_jobs or DEFAULT_MAX_JOBS,
        "min_score": MIN_SCORE if min_score is None else int(min_score),
        "preview_only": preview_only,
        "auto_send": auto_send,
        "preview_timeout_sec": DEFAULT_PREVIEW_TIMEOUT_SEC,
    }
    if search_dict.get("screening_policy"):
        payload["screening_policy"] = search_dict["screening_policy"]
    if candidate_profile:
        payload["candidate_profile"] = candidate_profile
    return TaskLaunch(task_type=task_type, payload=payload, source=source)


def _preview_flags(action: TargetAction, mode: LaunchMode | None) -> tuple[bool, bool]:
    """The (preview_only, auto_send) pair the handlers consume today.

    Wire keys stay stable for rollout; only who computes them changes. A save-only
    search is preview by definition — it has nothing to send.
    """
    if action != TargetAction.AUTO_APPLY:
        return True, False
    if mode == LaunchMode.LIVE:
        return False, True
    return True, False


def build_chat_cleanup_launch(
    *,
    source: LaunchSource,
    search: SavedSearch | None = None,
    mode: LaunchMode | None = None,
    rejection_reply_text: str | None = None,
    max_scan_depth: int | None = None,
) -> TaskLaunch:
    """Launch a 仅沟通 rejection cleanup.

    Rejection cleanup is keyword-independent, so it carries resolved triage settings
    rather than a search strategy. ``mode=None`` lets the configured ``chat.dry_run``
    win — the convention the searches-page trigger already used and the reason the
    other two callers should stop stating a ``dry_run`` of their own.
    """
    ack = resolve_chat_acknowledgment_settings()

    payload: dict[str, Any] = {
        "dry_run": ack.dry_run if mode is None else mode == LaunchMode.DRAFT,
        "rejection_reply_text": (
            ack.rejection_reply_text if rejection_reply_text is None else rejection_reply_text
        ),
        "max_scan_depth": ack.max_scan_depth if max_scan_depth is None else int(max_scan_depth),
    }
    if search is not None:
        payload["saved_search_id"] = search.id
        payload["search_id"] = search.id
        payload["search_name"] = search.name
    return TaskLaunch(task_type=TaskType.CHECK_CHAT, payload=payload, source=source)


def build_login_diagnostic_launch(*, source: LaunchSource) -> TaskLaunch:
    """Launch the CHECK_LOGIN probe.

    It carries provenance and nothing else: the handler reads no payload at all, and the
    ``mode: "diagnostic"`` key it used to be given had no reader — the diagnostic intent
    is provenance, which the record now carries as an attribute.
    """
    return TaskLaunch(task_type=TaskType.CHECK_LOGIN, payload={}, source=source)


def build_launch(
    kind: TaskKind,
    *,
    source: LaunchSource,
    search: SavedSearch | None = None,
    mode: LaunchMode | None = None,
    **kwargs: Any,
) -> TaskLaunch:
    """The one entry point: turn a launch request into a validated task."""
    if kind == TaskKind.SEARCH:
        if search is None:
            raise LaunchContractError("a search launch requires a SavedSearch")
        return build_search_launch(search, source=source, mode=mode, **kwargs)
    if kind == TaskKind.CHAT_CLEANUP:
        return build_chat_cleanup_launch(source=source, search=search, mode=mode, **kwargs)
    if kind == TaskKind.LOGIN_DIAGNOSTIC:
        return build_login_diagnostic_launch(source=source)
    raise LaunchContractError(f"unknown task kind {kind!r}")


def coerce_source(raw: Any) -> LaunchSource:
    """A task record's provenance, defaulting to ``manual`` for legacy tasks.

    Tasks created before provenance existed carry no ``source``; they are treated as
    manual so the startup sweep never reclaims a task whose origin it cannot prove.
    """
    try:
        return LaunchSource(str(raw))
    except ValueError:
        return LaunchSource.MANUAL
