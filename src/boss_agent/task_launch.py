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

Execution depth is the divergence this module owns most strictly, because it was the
one that made ``auto_apply`` a lie: dispatch needs ``auto_send=True`` *and*
``preview_only=False``, only an explicit ``LaunchMode.LIVE`` ever produced that pair, and
no scheduled or one-click path stated a mode. Every strategy trigger therefore drafted a
greeting, logged an offline draft, and never opened the chat — while the SavedSearch
that configured it says 自动打招呼, because CONTEXT.md makes Target Action *the* execution
depth. A targeted application had the same defect one layer closer to the wire: the job
detail's 定向投递 button hand-built an ``AUTO_APPLY`` payload that stated neither flag, so
the worker's defaults drafted instead of sent.

The remedy went one step further than deriving the pair from the Target Action whenever a
caller forgot to state a mode. "Preview the greeting, hold it back" stopped being an
execution depth at all (issue #298): an operator chooses between 深度存JD and 自动打招呼, and
whether a message leaves the device follows from that choice alone. No entry states a depth
for a search or a targeted application any more — the dedicated builders refuse one — and
the only surface that still says "drill" is the 拒信清扫 cleanup, whose ``dry_run`` is a
different intent on a different surface. Reading a greeting before it goes out is something
a human does in the Web Dashboard now, not a mode the agent runs in.

The two wire keys themselves survive this change: a task already queued was written by an
older builder and the worker still reads both. Collapsing the payload onto a single depth
expression is issue #302.

This module turns ``(kind, provenance, search, job, mode)`` into a validated payload. The
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

#: The threshold a targeted application is exempt from. The human already chose
#: the posting, so an LLM score must not quietly veto the greeting the way it vetoes
#: a feed sweep -- and a threshold left to the worker default would do exactly that.
DIRECT_APPLY_MIN_SCORE = 0

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
    DIRECT_APPLY = "direct_apply"
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
) -> TaskLaunch:
    """Launch a saved search as a scrape or an auto-apply run.

    ``min_score`` is a caller *choice*, not a competing default: the parameter exists so
    the modal can offer a stricter threshold than the baseline, and the baseline is what
    everyone else gets. Leaving it unset used to mean three different numbers.

    ``mode`` is accepted only so that it can be refused. A search's depth is the Target
    Action the operator configured; a caller restating it is precisely how the gate in
    PR #297 came to be missed five times over, so issue #298 closes the door rather than
    asking every caller to set the second switch correctly.
    """
    _refuse_stated_depth(mode, "a search")
    action = _target_action_for(search)
    task_type = (
        TaskType.AUTO_APPLY if action == TargetAction.AUTO_APPLY else TaskType.SCRAPE_JOBS
    )

    # A save-only search never greets, so its preview flags are not a caller choice.
    preview_only, auto_send = _preview_flags(action)

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


def _preview_flags(action: TargetAction) -> tuple[bool, bool]:
    """The (preview_only, auto_send) pair the handlers consume, derived from one input.

    Two wire keys, one intent, written by exactly one function: the worker dispatches only
    on ``auto_send and not preview_only``, so a caller that authors the pair can get either
    half wrong and the payload still looks valid. ``auto_apply`` sends — that is what the
    operator's configured depth means — and ``save_jd`` never does.

    Both keys stay on the wire because a queued task was built by an older builder and the
    worker still reads the pair. Issue #302 collapses them into the single expression this
    function already derives.
    """
    if action != TargetAction.AUTO_APPLY:
        return True, False  # A save-only search is preview by definition.
    return False, True


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


@dataclass(frozen=True)
class DirectApplyTarget:
    """The posting a 定向投递 acts on.

    Deliberately minimal: the worker re-reads the record from the State Stream Broker by
    ``job_id``, so a launch carries only what that record cannot resolve for itself — the
    greeting the human edited, and the profile it was written for.
    """

    job_id: str
    title: str = ""
    company_name: str = ""
    greeting_message: str = ""
    candidate_profile: dict[str, Any] | None = None


def build_direct_apply_launch(
    job: DirectApplyTarget,
    *,
    source: LaunchSource,
    mode: LaunchMode | None = None,  # accepted only to be refused, exactly like a search
) -> TaskLaunch:
    """Launch one targeted application: greet the posting already on screen.

    A targeted application is outreach by definition — that is what its button says — so
    it declares ``auto_apply`` instead of leaving the worker to infer it, and it states
    ``DIRECT_APPLY_MIN_SCORE`` instead of leaving the baseline veto on a posting the
    human just chose. It used to state neither: no ``target_action``, no preview pair, no
    threshold, so the worker's draft-only defaults decided, and a rerun of the payload
    fell through to a keyword sweep.

    There is no preview depth to choose here either (issue #298): whoever clicks 定向投递
    wants that greeting sent. Reading it first happens in the dashboard, where saving a
    greeting marks it as the human's own copy and the agent sends it verbatim.
    """
    if not job.job_id:
        raise LaunchContractError("a direct apply launch requires the job it targets")
    _refuse_stated_depth(mode, "a targeted application")

    preview_only, auto_send = _preview_flags(TargetAction.AUTO_APPLY)
    payload: dict[str, Any] = {
        "target_action": TargetAction.AUTO_APPLY.value,
        "direct_job_id": job.job_id,
        # The feed path labels records by the keyword it searched. A targeted run has no
        # search, so the posting's own title is the only label it can carry.
        "keyword": job.title,
        "job_title": job.title,
        "company_name": job.company_name,
        "greeting_message": job.greeting_message,
        "min_score": DIRECT_APPLY_MIN_SCORE,
        "preview_only": preview_only,
        "auto_send": auto_send,
        "preview_timeout_sec": DEFAULT_PREVIEW_TIMEOUT_SEC,
    }
    if job.candidate_profile:
        payload["candidate_profile"] = job.candidate_profile
    return TaskLaunch(task_type=TaskType.AUTO_APPLY, payload=payload, source=source)


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
    job: DirectApplyTarget | None = None,
    mode: LaunchMode | None = None,
    **kwargs: Any,
) -> TaskLaunch:
    """The one entry point: turn a launch request into a validated task.

    `mode` means "drill, or actually reply", and only the 拒信清扫 cleanup may state it. A
    search and a targeted application derive their depth from their Target Action and
    refuse the mode outright, so the vote every entry used to get is now cast once, here.
    """
    if kind == TaskKind.SEARCH:
        if search is None:
            raise LaunchContractError("a search launch requires a SavedSearch")
        return build_search_launch(search, source=source, mode=mode, **kwargs)
    if kind == TaskKind.DIRECT_APPLY:
        if job is None:
            raise LaunchContractError("a direct apply launch requires the job it targets")
        return build_direct_apply_launch(job, source=source, mode=mode, **kwargs)
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
