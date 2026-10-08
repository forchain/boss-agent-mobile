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
on who dispatched it. And task provenance — a first-class GLOSSARY.md attribute with
defined ``manual``/``test``/``scheduler`` semantics — existed nowhere in code, faked
instead with payload markers like ``startup_cleanup`` and ``scheduled``.

Execution depth is the divergence this module owns most strictly, because it was the
one that made ``auto_apply`` a lie: dispatch needs ``auto_send=True`` *and*
``preview_only=False``, only an explicit ``LaunchMode.LIVE`` ever produced that pair, and
no scheduled or one-click path stated a mode. Every strategy trigger therefore drafted a
greeting, logged an offline draft, and never opened the chat — while the SavedSearch
that configured it says 自动打招呼, because GLOSSARY.md makes Target Action *the* execution
depth. A targeted application had the same defect one layer closer to the wire: the job
detail's 定向投递 button hand-built an ``AUTO_APPLY`` payload that stated neither flag, so
the worker's defaults drafted instead of sent.

The remedy went one step further than deriving the pair from the Target Action whenever a
caller forgot to state a mode. "Preview the greeting, hold it back" stopped being an
execution depth at all (issue #298): an operator chooses between 深度存JD and 自动打招呼, and
whether a message leaves the device follows from that choice alone. No entry states a depth
for a search any more — the builder refuses one — and the only surface that still says
"drill" is the 拒信清扫 cleanup, whose ``dry_run`` is a different intent on a different
surface. Reading a greeting before it goes out is something
a human does in the Web Dashboard now, not a mode the agent runs in.

The pair itself is gone from everything this module produces (issue #302). Depth has one
expression on the wire — the `target_action` the operator configured — because a payload
with two keys that must both be right is a payload whose readers must guess which half the
writer meant. The worker still *reads* the old pair while tasks written by an earlier
builder sit in the queue, and refuses a payload that states exactly one half of it: that is
the shape that used to degrade silently.

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
from .enums import TargetAction, TargetTaskType
from .search_entities import SavedSearch
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
    """Task Provenance — where a task came from, as GLOSSARY.md defines it.

    A real attribute on the task record rather than a payload marker: startup
    reclamation cancels ``TEST`` tasks so an automated suite never contends with the
    live worker for the device, and the dashboard shows the rest.
    """

    MANUAL = "manual"
    TEST = "test"
    SCHEDULER = "scheduler"


class LaunchMode(StrEnum):
    """Whether a 拒信清扫 drill may reply, and nothing else.

    Two modes, not three: "dry run" and "draft" were the same wire intent expressed in two
    vocabularies — compute it, show it, do not send it — and collapsing them is the whole
    point of having one builder. ``None`` is the third, distinct state: *honour what the
    configuration already decided*, which for a cleanup is the operator's ``chat.dry_run``.

    It stopped being a depth override for searches and targeted applications in issue #298.
    Those kinds derive their depth from their Target Action and refuse a stated mode,
    because "generate it but keep it on the device" is no longer a run mode — it is what the
    dashboard does before a human decides to dispatch.
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
    candidate_profile: dict[str, Any] | None = None,
    min_score: int | None = None,
    mode: LaunchMode | None = None,
    preview_only: bool | None = None,
    auto_send: bool | None = None,
) -> TaskLaunch:
    """Launch a saved search as a scrape or an auto-apply run.

    ``min_score`` is a caller *choice*, not a competing default: the parameter exists so
    the modal can offer a stricter threshold than the baseline, and the baseline is what
    everyone else gets. Leaving it unset used to mean three different numbers.

    ``mode``, ``preview_only`` and ``auto_send`` are accepted only so that they can be
    refused. A search's depth is the Target Action the operator configured, stated once on
    the wire: PR #297 came from a gate that needed two keys to agree, and issues #298 and
    #302 remove the second switch and then the second key, rather than asking every caller
    to set both correctly.
    """
    _refuse_stated_depth(mode, "A search")
    _refuse_hand_authored_depth(preview_only, auto_send, "A search")
    action = _target_action_for(search)
    task_type = TaskType.AUTO_APPLY if action == TargetAction.AUTO_APPLY else TaskType.SCRAPE_JOBS

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
        # One depth expression: `target_action`, above. The legacy pair is not written
        # because a reader that has to combine two keys is a reader that can be handed half
        # of one (issue #302).
        "preview_timeout_sec": DEFAULT_PREVIEW_TIMEOUT_SEC,
    }
    if search.screening_policy is not None and search_dict.get("screening_policy"):
        payload["screening_policy"] = search_dict["screening_policy"]
    if candidate_profile:
        payload["candidate_profile"] = candidate_profile
    return TaskLaunch(task_type=task_type, payload=payload, source=source)


def _refuse_hand_authored_depth(preview_only: Any, auto_send: Any, subject: str) -> None:
    """Reject a caller that authors the legacy depth keys on a ``subject`` launch.

    ``preview_only`` and ``auto_send`` were one intent written twice, and the worker
    dispatched only when both halves agreed. That pairing is the whole PR #297 defect: a
    caller that set one and not the other produced a payload that looked valid and drafted
    instead of sending. Issue #302 takes the pair away from producers altogether, so writing
    it is now an error rather than a second opinion the contract had to live with.
    """
    stated = [
        name
        for name, value in (("preview_only", preview_only), ("auto_send", auto_send))
        if value is not None
    ]
    if stated:
        raise LaunchContractError(
            f"{subject} does not take {', '.join(stated)}: its depth is one expression — the "
            f"Target Action. The preview_only/auto_send pair exists only for the worker to "
            f"read tasks an older builder had already queued (issue #302)."
        )


def _refuse_stated_depth(mode: LaunchMode | None, subject: str) -> None:
    """Reject a caller that tries to declare the depth of a ``subject`` launch.

    The refusal is the point. The defect was never a wrong value in a payload — it was that
    five callers each got to restate a depth the contract had already decided, and one of
    them (issue #297) got it wrong everywhere except the modal's dropdown. Silently
    ignoring a stated mode would leave that door open for the next entry to walk through.
    """
    if mode is not None:
        stated = "auto_apply" if mode == LaunchMode.LIVE else "save_jd"
        raise LaunchContractError(
            f"{subject} does not take a launch mode: its execution depth follows its "
            f"Target Action, not its caller. Drop the mode (the Target Action already "
            f"states {stated}, and only 拒信清扫 still has a drill to declare)."
        )


def build_chat_cleanup_launch(
    *,
    source: LaunchSource,
    search: SavedSearch | None = None,
    mode: LaunchMode | None = None,
    rejection_reply_text: str | None = None,
    max_scan_depth: int | None = None,
    max_scroll_swipes: int | None = None,
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
        "max_scroll_swipes": (
            ack.max_scroll_swipes if max_scroll_swipes is None else int(max_scroll_swipes)
        ),
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
    """The one entry point: turn a launch request into a validated task.

    `mode` means "drill, or actually reply", and only the 拒信清扫 cleanup may state it. A
    search derives its depth from its Target Action and refuses the mode outright, so the
    vote every entry used to get is now cast once, here.
    """
    if kind == TaskKind.SEARCH:
        if search is None:
            raise LaunchContractError("a search launch requires a SavedSearch")
        return build_search_launch(search, source=source, mode=mode, **kwargs)
    if kind == TaskKind.CHAT_CLEANUP and (
        kwargs.get("preview_only") is not None or kwargs.get("auto_send") is not None
    ):
        # A cleanup has a drill, not a greeting depth; the pair means nothing here.
        _refuse_hand_authored_depth(
            kwargs.get("preview_only"), kwargs.get("auto_send"), "A rejection cleanup"
        )
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
