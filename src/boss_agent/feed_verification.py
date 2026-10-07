"""
boss_agent.feed_verification
=============================
One composition of a verification feed run, for both of the callers that verify on a device.

Issues #389-#391 removed the second *implementation* of a job feed: ``SmokeHarness``
became an adapter over ``JobFeedPipeline`` (#390), and ``scripts/run_live_test.py`` stopped
going through the harness and drove the engine itself (#391). What neither ticket removed
was the composition *around* the engine — translating what an operator asked for into one
``FeedStreamConfig``, wiring the run's log to a console a person is watching, raising the
app before anything reads the screen, loading the candidate profile, and refusing to report
success for a run that extracted nothing. That block had settled into two copies, one per
caller, and it was already drifting: #391 changed the greeting gate in one of them and left
the other on the older rule.

So it lives here. It is the last "two implementations of one feed" this refactor was
supposed to remove, and it was reintroduced at the CLI edge by the same change that removed
the others — which is why it is stated plainly in this docstring rather than left to be
rediscovered.

What stays with a caller is what is genuinely the caller's: which screening policy a saved
search resolves to, who owns the device session and the auth gate in front of it, what the
run does about screenshots, and which flags exist at all.

Two differences are deliberate and must survive any future "simplification":

* **Preset resolution.** The harness still accepts a ``SavedSearch`` by id and resolves the
  preset's channel preference through ``resolve_screening_policy`` (issue #368), so one
  preset cannot mean two things depending on who ran it. The CLI resolves its own preset and
  hands over concrete configs.
* **The screening policy itself.** The CLI keeps ``ScreeningPolicy()`` — the engine's own
  default — because resolving it would call ``ScreeningPolicy.load_default()``, read the
  operator's global screening configuration, and change which cards a live run accepts. A
  verification run's job is to report what the engine does, not to re-decide what the engine
  should accept. That decision, and its reason, stay at the CLI call site where they are
  made; this module only carries the policy it is handed.
"""

import time
from pathlib import Path
from typing import Any

from rich.console import Console

from .async_bridge import run_sync
from .enums import TargetAction
from .feed_pipeline import FeedStreamConfig, FeedStreamResult, JobFeedPipeline
from .job_entities import JobPosting
from .matching import JobMatchGreetingService, MatchGreetingResult
from .memory import ResumeMemoryManager, StructuredCandidateProfile
from .screening import CandidateScreener
from .screening_policy import ScreeningPolicy
from .search_entities import FilterConfig, SearchConfig

console = Console()

BOSS_PACKAGE = "com.hpbr.bosszhipin"


async def console_log_sink(line: str) -> None:
    """Print one line of the pipeline's run log to the console a person is watching.

    The pipeline reports every decision it makes through this sink — search entry, each
    screened card, why a card was turned down, the greeting it drafted. Both callers read
    that report on a terminal rather than in a worker task log, which is the whole reason
    their reader is a human: the lines used to be the step prints of a procedural walk, and
    they have to stay on the console or a verification run goes quiet exactly when an
    operator needs to see which screen it is on.
    """
    console.print(line)


def activate_app(driver: Any, package_name: str = BOSS_PACKAGE) -> bool:
    """Bring Boss 直聘 to the foreground before anything reads the screen.

    Activation belongs to whoever opened the session — the pipeline starts from whatever
    page it is handed and has no opinion about which app is in front. Failures are swallowed
    on purpose: a driver without ``activate_app``, or an app already in front, is not a reason
    to abandon a verification run, and the run itself fails loudly if the screen really is
    wrong.
    """
    if hasattr(driver, "activate_app"):
        try:
            driver.activate_app(package_name)
            time.sleep(1.0)
            return True
        except Exception:
            pass
    return False


def load_candidate_profile(
    *,
    memory_manager: ResumeMemoryManager,
    resume_file: str | Path | None,
    force_refresh_memory: bool,
    matching_service: JobMatchGreetingService | None = None,
) -> StructuredCandidateProfile | None:
    """Load the profile the run screens against, or ``None`` when there is none.

    ``memory_manager`` is injected rather than constructed here because the two callers own
    it differently: the harness holds one for its whole life (and a test asserts against it),
    the CLI builds one per invocation. ``matching_service`` is the same story — the harness
    holds the object its caller injected, and the greeting has to be drafted by *that* object
    rather than by a second one the pipeline built for itself.

    A verification run must survive an unreadable profile: the feed pass is what it is here
    to verify, and a missing greeting does not invalidate an extraction. The messages are
    printed rather than raised because on a device "the greeting will be skipped" is the
    only clue the operator gets about why the card that comes back looks the way it does.
    """
    try:
        profile = memory_manager.load_memory(
            force_refresh=force_refresh_memory,
            resume_file=resume_file,
        )
    except FileNotFoundError:
        console.print(
            "[dim]No candidate resume or memory profile configured. Greeting draft will be skipped.[/dim]"
        )
        return None
    except Exception as e:
        console.print(f"[yellow]⚠️  Failed to pre-load candidate memory upfront: {e}[/yellow]")
        return None

    if profile:
        if matching_service is not None:
            matching_service.set_candidate_profile(profile)
        console.print(
            f"👤 [bold green]Candidate Memory Profile Active:[/bold green] "
            f"[bold cyan]{profile.name}[/bold cyan] "
            f"({profile.years_of_experience}年经验, "
            f"核心技能: {', '.join(profile.core_skills[:3])})"
        )
    return profile


def build_verification_pipeline(
    driver: Any,
    *,
    matching_service: JobMatchGreetingService,
    log: Any = console_log_sink,
) -> JobFeedPipeline:
    """Compose the engine for one verification run: no store, nothing persisted.

    The worker builds its pipeline through ``for_task()`` with a store behind it; a
    verification run has no broker and wants no writes, so ``store`` is left at its default
    in-memory one (issue #389) and every record the run makes dies with it.

    The screener carries *this* run's matching service rather than one the pipeline builds
    for itself, so the greeting is drafted by the object the caller configured and can assert
    on. That is the only wiring here that a caller cannot substitute for itself.
    """
    return JobFeedPipeline(
        driver=driver,
        screener=CandidateScreener(matching_service=matching_service),
        log=log,
    )


def _verification_config(
    *,
    search_config: SearchConfig,
    filter_config: FilterConfig,
    screening_policy: ScreeningPolicy,
    candidate_profile: StructuredCandidateProfile | None,
    enable_greeting_draft: bool,
) -> FeedStreamConfig:
    """Translate what an operator asked for into the one declarative run the engine reads.

    ``FeedStreamConfig`` is the pipeline's whole vocabulary: it states an intent and the
    engine decides which card that means. Everything here is shared so that "a verification
    run" means one thing to both callers — the depth a card is read at, how many are read,
    and whether the app searches at all are not decisions an adapter should be choosing
    separately.
    """
    should_search = search_config.should_search
    # The greeting gate is a conjunction, and it was a conjunction before #390: a run only
    # asks for outreach when it wanted a greeting AND actually has a candidate to greet
    # with. Keying the target action off the flag alone meant ``--no-resume`` (no profile)
    # still issued the screening and drafting calls a greeting-less run has no use for.
    draft_greeting = enable_greeting_draft and candidate_profile is not None
    return FeedStreamConfig(
        # A verification run drafts a greeting; it never sends one. ``send_greeting=False``
        # is the depth the dispatch path reads: the screener still drafts, the record stays
        # re-sendable, and nothing is typed into an employer's chat window — a smoke test
        # has no business messaging a real recruiter.
        target_action=TargetAction.AUTO_APPLY if draft_greeting else TargetAction.SAVE_JD,
        send_greeting=False,
        # ``should_search`` is the whole search decision, keyword included: a config with no
        # keyword states ``enable_search=False`` so the pipeline resets to the home feed and
        # browses recommendations, which is what both callers always did with a keyword-less
        # config — reset to home, then do not search.
        keyword=search_config.keyword if should_search else None,
        enable_search=should_search,
        # A verification run reads one posting. The old procedural flow opened the top card
        # and stopped there; ``max_jobs=1`` says the same thing to the scanner.
        max_jobs=1,
        # The filter config carries its own enable flag and its own "nothing to apply", so
        # handing it over unchanged gets both halves of the old filter step: apply what is
        # configured, clear the dialog when nothing is.
        filter_config=filter_config,
        # What the caller resolved, not what this module would have resolved. The CLI hands
        # over the engine default on purpose; see the module docstring for why the two
        # callers disagree about this and why that disagreement is the point.
        screening_policy=screening_policy,
        candidate_profile=candidate_profile,
    )


def run_verification_feed(
    *,
    pipeline: JobFeedPipeline,
    search_config: SearchConfig,
    filter_config: FilterConfig,
    screening_policy: ScreeningPolicy,
    candidate_profile: StructuredCandidateProfile | None,
    enable_greeting_draft: bool,
) -> FeedStreamResult:
    """Run the feed once and return its report — or refuse to report a run that read nothing.

    ``stream_jobs`` is the single entry point for feed discovery and extraction, so this is
    deliberately thin: translate, run, check that something came back.

    The empty-postings guard catches a specific failure, and it is worth being precise about
    which one. A posting enters ``result.postings`` when a detail page yields one, and leaves
    again when a *detail-stage* App-Enforced Filter turns it down — the business-district
    blacklist or the commute ceiling. The deep screener never removes a posting: it ends the
    run with a verdict on the one already extracted. So an empty list means the run opened no
    card, or every card it opened was withdrawn before extraction was kept — a run that never
    read a job description at all. Reporting success for that would be the one failure mode a
    verification script must not have.

    What the guard must *not* do is fail a run whose only card was rejected by the screening
    engine. That card still proves the app launched, searched, opened a posting and parsed
    its JD, which is exactly what this run exists to verify; failing it would make the script
    report broken every time the engine correctly turns a card down. Callers that report a
    pass in prose are responsible for saying which of the two happened (issue #391).
    """
    config = _verification_config(
        search_config=search_config,
        filter_config=filter_config,
        screening_policy=screening_policy,
        candidate_profile=candidate_profile,
        enable_greeting_draft=enable_greeting_draft,
    )
    result = run_sync(pipeline.stream_jobs(config))
    if not result.postings:
        # The outcome and the counters are in the message because on a device "nothing was
        # read" is almost never a mystery: it is one screen's worth of explanation, and the
        # caller is the only one who can see that screen.
        raise RuntimeError(
            f"Verification run extracted no job posting "
            f"(outcome={result.outcome}, "
            f"reason={result.reason or 'n/a'}, "
            f"error={result.error_message or 'n/a'}, "
            f"scanned={result.scanned}, processed={result.processed}, "
            f"skipped={result.skipped})"
        )
    return result


def render_greeting_match_card(
    *,
    matching_service: JobMatchGreetingService,
    posting: JobPosting,
    result: FeedStreamResult,
    candidate_profile: StructuredCandidateProfile | None,
    enable_greeting_draft: bool,
) -> None:
    """Render the match card for a run that drafted a greeting; otherwise do nothing.

    Rendering is presentation and the pipeline does not present — the run reports what it
    drafted, and the operator reads it here.

    On what the card can honestly show: ``FeedStreamResult`` carries the run's score and its
    extracted JD key requirements, so those are passed straight through. It does *not* carry
    the per-point match reasons — the pipeline logs them on the way through and
    ``_persist_verdict`` does not keep them, because the record persists the greeting and the
    verdict, not the reasoning behind them. Inventing a plausible list there would have made
    the card say "no reasons" and, worse, made any list that did appear unreadable as data,
    so the slot says where the reasons actually went instead (issue #391).

    ``SAVE_JD`` still costs a semantic screen: the pipeline computes the evaluation before it
    branches on the target action, so a run with ``--no-greeting`` is screened but drafts
    nothing. That is the engine's designed behaviour for the worker — it is what keeps a
    saved JD clean — and it is named here rather than worked around, because a verification
    run reporting the engine's own decision is the whole point of this script.
    """
    if not (enable_greeting_draft and candidate_profile is not None and result.greeting_message):
        return

    matching_service.render_match_card(
        posting,
        MatchGreetingResult(
            match_score=result.score,
            match_reasons=["逐条匹配理由未随提取结果落盘，已在上方运行日志中逐条输出"],
            jd_key_requirements=list(result.jd_key_requirements),
            greeting_message=result.greeting_message,
        ),
    )
