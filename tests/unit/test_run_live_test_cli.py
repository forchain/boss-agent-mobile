"""
tests/unit/test_run_live_test_cli.py
====================================
The interactive verification runner's seam (issue #391).

``scripts/run_live_test.py`` used to drive device verification by handing itself to
``SmokeHarness``, so a live run walked a second implementation of a job feed — eight page
objects deep, and the one nobody exercises on a device. It now drives ``JobFeedPipeline``
itself: the same entry point the worker uses, so what a device run verifies is the engine
that actually runs in production.

The two things the pipeline cannot answer for itself stay in the runner, in front of the
run: activating the app, which belongs to whoever opened the session, and the auth
challenge, because the pipeline assumes a usable session and knows nothing about captchas.
Both are asserted here, because losing either would march a verification run into a login
wall or leave it scanning a screen nobody was looking at.

Everything stays in memory — the Appium session is a stand-in, the pipeline is scripted
through the shared harness, and the candidate screener is handed a stub LLM client so the
fast tier never reaches an endpoint.

Where the seams are: the runner resolves its own preset and device session, so it still
constructs its own ``ResumeMemoryManager`` and ``JobMatchGreetingService`` and the tests
still patch those on the script module. The engine composition — ``JobFeedPipeline`` plus the
screener that carries this run's matching service — moved to ``boss_agent.feed_verification``
in the #391 review, so those two patches moved with it. That is the point of the move: the
runner and ``SmokeHarness`` now build one pipeline in one place, and a test that watched the
runner build it watches the shared composition instead.
"""

from unittest.mock import MagicMock, patch

import pytest
import scripts.run_live_test as runner
from _feed_harness import ScriptedFeed, _card, _detail_page, _posting, script_pages, stub_llm

from boss_agent.enums import AuthStatus, JobRecordStatus, TargetAction
from boss_agent.feed_pipeline import FeedStreamResult, JobFeedPipeline
from boss_agent.job_entities import JobPosting
from boss_agent.matching import JobMatchGreetingService
from boss_agent.memory import StructuredCandidateProfile
from boss_agent.screening import CandidateScreener, JobVerdictStage

# Captured before any patching: the tests replace these attributes to observe composition,
# and must still be able to reach the real classes through them.
_REAL_PIPELINE = JobFeedPipeline
_REAL_SCREENER = CandidateScreener
_REAL_GREETING = JobMatchGreetingService

POSTING_TITLE = "资深 Python / Android 自动化专家"
POSTING_COMPANY = "北京智联前沿科技有限公司"
CANDIDATE = StructuredCandidateProfile(
    name="李华", years_of_experience=8, core_skills=["Python", "Android"]
)


def _run(
    tmp_path,
    *,
    auth_status: AuthStatus = AuthStatus.AUTHENTICATED,
    enable_greeting_draft: bool = False,
    feed: ScriptedFeed | None = None,
    detail_yields_nothing: bool = False,
    profile: StructuredCandidateProfile | None = CANDIDATE,
):
    """Drive ``run_live_test`` over a scripted device; return what the run was made of.

    The pipeline the runner composes is captured and scripted through ``script_pages`` —
    the seam ``SmokeHarness``-owned pipelines already get — so ``stream_jobs`` really runs
    over a scripted feed rather than being mocked into agreeing with the test. The config
    it was handed is recorded too, because the runner's decisions live in that config and
    nowhere else.

    ``profile`` is what the memory manager answers with, so "a greeting was asked for and
    there is nothing to draft with" is one keyword rather than a hand-built mock.
    """
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    driver.page_source = "<hierarchy/>"

    session = MagicMock()
    session.start.return_value = driver

    composed: list[JobFeedPipeline] = []
    configs: list[object] = []
    posting = JobPosting(
        title=POSTING_TITLE,
        company_name=POSTING_COMPANY,
        salary_range="40-65K·16薪",
        job_description="岗位职责：负责移动端自动化框架设计与高可靠执行引擎开发，深度优化拟真轨迹。",
    )
    detail = _detail_page(posting=posting)
    if detail_yields_nothing:
        # `_detail_page(posting=None)` would fall back to its own default posting, so the
        # "unreadable detail page" case has to be stated on the page directly.
        detail.extract_job_posting.return_value = None

    def _compose_pipeline(**kwargs):
        pipeline = _REAL_PIPELINE(**kwargs)
        composed.append(pipeline)
        script_pages(
            pipeline,
            feed=feed
            if feed is not None
            else ScriptedFeed([[_card(POSTING_TITLE, POSTING_COMPANY)]]),
            detail=detail,
        )
        real_stream = pipeline.stream_jobs

        async def _record(config, on_job=None):
            configs.append(config)
            return await real_stream(config, on_job)

        pipeline.stream_jobs = _record
        return pipeline

    takeover = MagicMock()
    takeover.check_and_handle_takeover.return_value = auth_status
    memory = MagicMock()
    memory.load_memory.return_value = profile
    verdict = stub_llm()

    with (
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("time.sleep", return_value=None),
        patch.object(runner, "AppiumSession", return_value=session),
        patch.object(runner, "TakeoverHandler", return_value=takeover),
        patch.object(runner, "ResumeMemoryManager", return_value=memory),
        patch("boss_agent.feed_verification.JobFeedPipeline", side_effect=_compose_pipeline),
        patch("boss_agent.feed_verification.CandidateScreener") as screener_cls,
        patch.object(runner, "JobMatchGreetingService") as greeting_cls,
    ):
        screener_cls.side_effect = lambda **kw: _REAL_SCREENER(llm_client=verdict, **kw)
        greeting_cls.side_effect = lambda **kw: _REAL_GREETING(llm_client=stub_llm(), **kw)
        outcome = runner.run_live_test(
            search_id=None,
            keyword="Agent",
            device_udid="emulator-5554",
            server_url="http://127.0.0.1:4723",
            enable_greeting_draft=enable_greeting_draft,
        )

    return outcome, composed, configs, takeover, session, driver, memory


def test_the_runner_drives_the_job_feed_pipeline_and_reports_its_extraction(tmp_path, capsys):
    """A device run is a pipeline run now, and the posting it reports is that run's.

    The runner used to call ``SmokeHarness.run_smoke_test()``, which meant a live device
    verified a bespoke page-walking implementation while production ran the pipeline. The
    assertion is on the reporting end: the title the operator sees comes from the posting
    the composed pipeline extracted, so it fails if the runner stops reading that result.
    """
    outcome, composed, configs, _takeover, _session, _driver, _memory = _run(tmp_path)

    assert outcome is True
    assert len(composed) == 1 and isinstance(composed[0], JobFeedPipeline)
    assert len(configs) == 1, "exactly one feed run per verification"
    printed = capsys.readouterr().out
    assert POSTING_TITLE in printed
    assert POSTING_COMPANY in printed


def test_a_failed_auth_gate_stops_the_run_before_the_feed_is_read(tmp_path):
    """The challenge gate stays in front of the run, not behind it.

    The pipeline assumes a usable session and begins reading somebody's job list, so a
    captcha has to stop a verification run *before* it starts. A runner that composed the
    engine first and asked the gate afterwards would have already walked a login wall.
    """
    outcome, composed, _configs, takeover, _session, _driver, _memory = _run(
        tmp_path, auth_status=AuthStatus.CHALLENGE_REQUIRED
    )

    assert takeover.check_and_handle_takeover.called
    assert composed == [], "no feed engine may be composed behind an unresolved challenge"
    assert outcome is False


def test_the_app_is_brought_to_the_foreground_before_the_run(tmp_path):
    """Activation belongs to whoever opened the session, so the runner keeps doing it.

    The pipeline starts from whatever screen it is handed; a run that never raised Boss
    直聘 would scan whatever the previous run left on the device.
    """
    _outcome, _composed, _configs, _takeover, _session, driver, _memory = _run(tmp_path)

    driver.activate_app.assert_called_with("com.hpbr.bosszhipin")


def test_a_run_that_extracted_nothing_fails_the_verification(tmp_path):
    """Silence is not a pass.

    The pipeline reports only what it kept: a card whose detail page yielded nothing, or
    one the run withdrew at screening, never becomes a posting. A run that extracted
    nothing verified nothing, so it must report failure rather than hand back the first
    card regardless of what screening made of it.
    """
    outcome, _composed, _configs, _takeover, _session, _driver, _memory = _run(
        tmp_path, detail_yields_nothing=True
    )

    assert outcome is False


def test_the_device_session_is_torn_down_whether_the_run_passes_or_fails(tmp_path):
    """A verification run must not leave the emulator holding a session on failure."""
    _outcome, _composed, _configs, _takeover, session, _driver, _memory = _run(tmp_path)
    assert session.stop.called

    outcome, _composed, _configs, _takeover, session, _driver, _memory = _run(
        tmp_path, detail_yields_nothing=True
    )
    assert outcome is False
    assert session.stop.called, "a failed run must still release the device session"


def test_the_run_verifies_one_posting_and_never_sends_a_greeting(tmp_path):
    """The config states one intent: check that a posting can be extracted, send nothing.

    Asserted on the config the runner actually handed ``stream_jobs``. ``send_greeting``
    stays false in every case — a verification run that typed into an employer's chat
    window would message a real recruiter — and ``max_jobs=1`` is the old runner's "open
    the top card and stop" translated into the vocabulary the engine reads.
    """
    _outcome, _composed, configs, _takeover, _session, _driver, _memory = _run(tmp_path)

    config = configs[0]
    assert config.keyword == "Agent"
    assert config.enable_search is True
    assert config.max_jobs == 1
    assert config.send_greeting is False


def test_no_greeting_changes_the_run_rather_than_quietening_its_output(tmp_path):
    """``--no-greeting`` means it says: no profile load, no greeting requested.

    It reads as "disable LLM match analysis and greeting draft generation". Loading a
    candidate profile would call the LLM to parse a resume for a run that has already
    decided it has no use for the answer, so the flag has to skip the work, not merely the
    printing — and the run has to say save-only intent instead of outreach.
    """
    outcome, _composed, configs, _takeover, _session, _driver, memory = _run(
        tmp_path, enable_greeting_draft=False
    )

    assert outcome is True
    assert not memory.load_memory.called, "no profile may be loaded when no greeting is wanted"
    assert configs[0].candidate_profile is None
    assert configs[0].target_action is TargetAction.SAVE_JD


def test_greeting_drafting_hands_the_pipeline_the_profile_it_loaded(tmp_path):
    """The other side of the same flag: with greetings on, the profile still reaches the run.

    ``--no-greeting`` must not have been implemented by dropping the profile on the floor
    for every invocation, so the greeting path is pinned too: the loaded profile is the
    one the pipeline screens against, and the run states outreach intent.
    """
    outcome, _composed, configs, _takeover, _session, _driver, memory = _run(
        tmp_path, enable_greeting_draft=True
    )

    assert outcome is True
    assert memory.load_memory.called
    assert configs[0].candidate_profile is CANDIDATE
    assert configs[0].target_action is TargetAction.AUTO_APPLY
    assert configs[0].send_greeting is False


def test_a_wanted_greeting_with_no_profile_states_save_only(tmp_path):
    """The greeting gate is a conjunction, and the conjunction is the whole point.

    Issue #391's review. Keying the target action off ``enable_greeting_draft`` alone meant
    ``--resume`` with an unreadable resume — or none at all — still stated ``AUTO_APPLY``, so
    a run with no candidate to greet issued the LLM screening and drafting calls the old
    runner never made. Reaching out requires both halves: the operator asked for a greeting
    *and* there is a profile to draft it from.
    """
    outcome, _composed, configs, _takeover, _session, _driver, memory = _run(
        tmp_path, enable_greeting_draft=True, profile=None
    )

    assert outcome is True
    assert memory.load_memory.called, "the greeting path still asks for a profile"
    assert configs[0].candidate_profile is None
    assert configs[0].target_action is TargetAction.SAVE_JD, (
        "no profile means nothing to draft, so the run must not state outreach intent"
    )
    assert configs[0].send_greeting is False


def test_a_run_the_screening_engine_turned_down_says_so_instead_of_claiming_a_match():
    """ "PASSED" is one word for two facts, and the runner has to say which one it is.

    Issue #391's review. A card the deep screener rejects is still a card the app opened and
    whose JD the run parsed — that is the extraction this script verifies — so it exits zero
    and must not be rewritten into a failure. But it is not the claim a matched card earns,
    and one green line cannot honestly carry both.

    The branch is pinned on the report rather than through a scripted run because this
    runner deliberately hands the engine ``ScreeningPolicy()``: an empty blacklist means the
    deep screener always admits, so today the engine cannot turn a card down here at all. The
    line is a guard on the outcome the pipeline reports, not a routine event — which is
    exactly why it has to be right the first time it does happen.
    """
    posting = _posting()
    screened_out = FeedStreamResult(
        outcome=JobVerdictStage.FILTERED_BY_DEEP_SCREENER.value,
        reason="命中驻场黑名单",
        postings=[posting],
    )
    accepted = FeedStreamResult(outcome=JobRecordStatus.MATCHED.value, score=93, postings=[posting])

    rejected_line = runner._verdict_line(screened_out)
    assert "PASSED" in rejected_line
    assert "screened out" in rejected_line
    assert "命中驻场黑名单" in rejected_line
    assert "🎉" not in rejected_line, "a screened-out run must not read as a matched one"

    accepted_line = runner._verdict_line(accepted)
    assert "🎉" in accepted_line
    assert "screened out" not in accepted_line
    assert "outcome=matched" in accepted_line


def test_preview_timeout_still_parses_but_says_it_paces_nothing(capsys):
    """The flag survives, and no longer lies about what it does.

    The pause it was written for watched a greeting being typed into a live chat box. A
    verification run types nothing into one, so the pause went away with the typing
    (issue #390). Invocations are in muscle memory and runbooks, so the flag is still
    accepted — but an operator who passes it is told plainly that it waits for nothing,
    rather than watching a number they set do nothing at all.
    """
    with (
        patch.object(runner.sys, "argv", ["run_live_test.py", "--preview-timeout", "9"]),
        patch.object(runner, "load_runner_settings", return_value={}),
        patch.object(runner, "run_live_test", return_value=True) as run_mock,
        pytest.raises(SystemExit) as exit_info,
    ):
        runner.main()

    assert exit_info.value.code == 0
    assert run_mock.called, "the flag must not break the invocation"
    assert "preview_timeout_sec" not in run_mock.call_args.kwargs
    printed = capsys.readouterr().out
    assert "--preview-timeout" in printed
    assert "inert" in printed


def test_a_preview_timeout_set_in_settings_is_announced_too(capsys):
    """The notice covers both routes in, or it covers the one an operator notices less.

    Issue #391's review. ``preview_timeout_sec`` is in ``DEFAULTS``, so every operator's
    settings file carries it and an operator who deliberately changed it to wait longer got
    silence while ``--preview-timeout`` printed a warning. Both spellings of the same dead
    number deserve the same word. The *unchanged* default must stay quiet, or the warning
    would lead every run in the world.
    """
    with (
        patch.object(runner.sys, "argv", ["run_live_test.py"]),
        patch.object(runner, "load_runner_settings", return_value={"preview_timeout_sec": 10.0}),
        patch.object(runner, "run_live_test", return_value=True),
        pytest.raises(SystemExit),
    ):
        runner.main()

    printed = capsys.readouterr().out
    assert "preview_timeout_sec" in printed
    assert "10.0" in printed and "inert" in printed


def test_an_untouched_preview_timeout_default_prints_no_warning(capsys):
    """The other half of the same fix: silence for a number nobody set.

    ``DEFAULTS`` states 3.0, so without this the notice would fire on every invocation and
    train operators to scroll past it — which is how the flag's warning stopped meaning
    anything in the first place.
    """
    with (
        patch.object(runner.sys, "argv", ["run_live_test.py"]),
        patch.object(runner, "load_runner_settings", return_value={"preview_timeout_sec": 3.0}),
        patch.object(runner, "run_live_test", return_value=True),
        pytest.raises(SystemExit),
    ):
        runner.main()

    assert "inert" not in capsys.readouterr().out


def test_a_failed_run_still_exits_nonzero(capsys):
    """Exit codes are a contract with runbooks; a failed verification must not report 0."""
    with (
        patch.object(runner.sys, "argv", ["run_live_test.py"]),
        patch.object(runner, "load_runner_settings", return_value={}),
        patch.object(runner, "run_live_test", return_value=False),
        pytest.raises(SystemExit) as exit_info,
    ):
        runner.main()

    assert exit_info.value.code == 1
