"""
tests/unit/test_filter_dialog_and_config.py
============================================
Filter configuration, the filter dialog page, and how a filter reaches a feed run.

The two filter tests that used to live at the bottom of this file drove ``SmokeHarness``
over a mocked driver and asserted that a walk finished — one of them merely asserted
``isinstance(job, JobPosting)``, which a posting built out of MagicMock attributes
satisfies without proving anything (issue #391). The filter behaviour they were reaching
for belongs to ``JobFeedPipeline``: it is the engine that now owns the dialog, and it is
what the interactive runner drives on a device. So they are asserted there, against the
config the run was actually handed, rather than against the order a page walk happened to
click things in.

The dialog's own vocabulary — option synonyms, reset-before-select ordering — is still
tested directly against ``FilterDialogPage``, because the pipeline still drives exactly
that object and those are its guarantees, not the harness's.
"""

from unittest.mock import MagicMock, patch

import pytest
from _feed_harness import ScriptedFeed, _card, _detail_page, _pipeline, _posting

from boss_agent.feed_pipeline import FeedStreamConfig
from boss_agent.pages import FilterDialogPage
from boss_agent.search_entities import FilterConfig

CONFIGURED = FilterConfig(
    education="硕士",
    salary="50K以上",
    experience="10年以上",
    activity="今日活跃",
    company_scales=["100-499人"],
)
NOTHING_TO_APPLY = FilterConfig(
    education=None,
    salary=None,
    experience=None,
    activity=None,
    company_scales=[],
)


def _feed_pipeline():
    """A pipeline over a scripted feed, with the filter dialog left observable."""
    return _pipeline(
        None,
        feed=ScriptedFeed([[_card("AI Agent 平台工程师", "智元创新")]]),
        detail=_detail_page(posting=_posting()),
    )


def test_filter_config_defaults_and_validation():
    cfg = FilterConfig()
    assert cfg.education == "硕士"
    assert cfg.salary == "5万元以上"
    assert cfg.experience == "10年以上"
    assert cfg.activity == "今日活跃"
    assert len(cfg.company_scales) == 4
    assert "100-499人" in cfg.company_scales
    assert "10000人以上" in cfg.company_scales
    assert cfg.has_filters is True

    # Empty or all "不限" filter config
    cfg_empty = FilterConfig(
        education=None,
        salary=None,
        experience=None,
        activity=None,
        company_scales=[],
    )
    assert cfg_empty.has_filters is False

    cfg_all_unlimited = FilterConfig(
        education="不限",
        salary="不限",
        experience="不限",
        activity="不限",
        company_scales=[],
    )
    assert cfg_all_unlimited.has_filters is False

    cfg_with_salary = FilterConfig(
        education="不限",
        salary="50K以上",
        experience="不限",
        activity="不限",
        company_scales=[],
    )
    assert cfg_with_salary.has_filters is True


def test_filter_dialog_page_interactions():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_filter_btn = MagicMock()
    mock_filter_btn.rect = {"x": 500, "y": 100, "width": 80, "height": 40}

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.rect = {"x": 400, "y": 1750, "width": 600, "height": 80}

    mock_reset_btn = MagicMock()
    mock_reset_btn.rect = {"x": 50, "y": 1750, "width": 300, "height": 80}

    mock_close_btn = MagicMock()
    mock_close_btn.rect = {"x": 30, "y": 100, "width": 80, "height": 80}

    mock_option_elem = MagicMock()
    mock_option_elem.rect = {"x": 300, "y": 500, "width": 200, "height": 60}

    def mock_find_elements(by, value):
        if "btn_confirm" in value or "确定" in value:
            return [mock_confirm_btn]
        if "btn_reset" in value or "清除" in value:
            return [mock_reset_btn]
        if "iv_back" in value or "iv_close" in value or "关闭" in value:
            return [mock_close_btn]
        if "筛选" in value:
            return [mock_filter_btn]
        if any(
            opt in value
            for opt in ["硕士", "5万元以上", "10年以上", "今日活跃", "100-499人", "应届生"]
        ):
            return [mock_option_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    page = FilterDialogPage(mock_driver)

    # Open filter
    assert page.open_filter() is True

    # Check is open
    assert page.is_dialog_open() is True

    # Select single option directly
    assert page.select_option("硕士") is True

    # Select single option using synonym: "50K以上" should match "5万元以上"
    assert page.select_option("50K以上") is True

    # Select "在校/应届" should match "应届生"
    assert page.select_option("在校/应届") is True

    # Selecting "不限" is safe no-op returning True
    assert page.select_option("不限") is True

    # Close dialog
    assert page.close_dialog() is True

    # Clear filters directly
    assert page.clear_filters() is True

    # Apply full filter configuration with standard 50K以上
    cfg = FilterConfig(
        education="硕士",
        salary="50K以上",
        experience="10年以上",
        activity="今日活跃",
        company_scales=["100-499人"],
    )
    assert page.apply_filters(cfg) is True


def test_apply_filters_resets_first_and_handles_empty_config():
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_filter_btn = MagicMock()
    mock_filter_btn.rect = {"x": 500, "y": 100, "width": 80, "height": 40}

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.rect = {"x": 400, "y": 1750, "width": 600, "height": 80}

    mock_reset_btn = MagicMock()
    mock_reset_btn.rect = {"x": 50, "y": 1750, "width": 300, "height": 80}

    actions = []

    def mock_find_elements(by, value):
        if "btn_confirm" in value or "确定" in value:
            return [mock_confirm_btn]
        if "btn_reset" in value or "清除" in value:
            return [mock_reset_btn]
        if "筛选" in value:
            return [mock_filter_btn]
        if "硕士" in value:
            mock_opt = MagicMock()
            mock_opt.rect = {"x": 300, "y": 500, "width": 200, "height": 60}
            return [mock_opt]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    page = FilterDialogPage(mock_driver)

    with patch.object(page.gestures, "human_click") as mock_click:

        def record_click(elem):
            if elem == mock_reset_btn:
                actions.append("reset")
            elif elem == mock_confirm_btn:
                actions.append("confirm")
            elif elem == mock_filter_btn:
                actions.append("open")
            else:
                actions.append("option")

        mock_click.side_effect = record_click

        cfg = FilterConfig(
            education="硕士",
            salary=None,
            experience=None,
            activity=None,
            company_scales=[],
        )
        assert page.apply_filters(cfg) is True

        # Assert reset was clicked BEFORE option was selected
        assert "reset" in actions
        assert "option" in actions
        assert actions.index("reset") < actions.index("option")
        assert actions[-1] == "confirm"

    # Now test apply_filters with empty config (has_filters=False) clears filters
    actions.clear()
    with patch.object(page.gestures, "human_click") as mock_click:

        def record_click(elem):
            if elem == mock_reset_btn:
                actions.append("reset")
            elif elem == mock_confirm_btn:
                actions.append("confirm")
            elif elem == mock_filter_btn:
                actions.append("open")
            else:
                actions.append("option")

        mock_click.side_effect = record_click

        empty_cfg = FilterConfig(
            education=None,
            salary=None,
            experience=None,
            activity=None,
            company_scales=[],
        )
        assert page.apply_filters(empty_cfg) is True
        assert actions == ["reset", "confirm"]


@pytest.mark.asyncio
async def test_a_configured_filter_reaches_the_dialog_as_the_operator_wrote_it():
    """A run applies the filter config it was given, untranslated.

    The interactive runner hands its ``FilterConfig`` straight through, so the object the
    dialog receives has to be that one — same industries, same education, same scales. If
    the pipeline rebuilt or defaulted it, an operator's preset would silently stop
    describing the search they asked for.
    """
    pipeline = _feed_pipeline()

    with patch("boss_agent.feed_pipeline.FilterDialogPage") as filter_cls:
        result = await pipeline.stream_jobs(
            FeedStreamConfig(keyword="Agent", max_jobs=1, filter_config=CONFIGURED)
        )

    filter_cls.return_value.apply_filters.assert_called_once()
    applied = filter_cls.return_value.apply_filters.call_args.args[0]
    assert applied is CONFIGURED
    assert (applied.education, applied.salary) == ("硕士", "50K以上")
    # The run is still a verification run: filtering decides what the feed shows, not
    # whether the run extracted anything.
    assert [p.title for p in result.postings] == ["AI Agent 平台工程师"]


@pytest.mark.asyncio
async def test_a_run_with_nothing_to_apply_clears_the_conditions_a_previous_run_left():
    """No configured filters means *clear*, not *leave whatever was there*.

    The app's filter dialog keeps its conditions between runs, so a run with nothing to
    apply inherits somebody else's search unless it actively clears them. Asserted on the
    page object the pipeline drives, which is where the behaviour moved in issue #390.

    ``test_job_feed_pipeline.py`` pins the same decision for the worker path, where the
    flag arrives through a task payload's ``enable_filter``; this is the runner's version,
    where the filter config arrives directly from the operator's preset.
    """
    pipeline = _feed_pipeline()

    with patch("boss_agent.feed_pipeline.FilterDialogPage") as filter_cls:
        await pipeline.stream_jobs(
            FeedStreamConfig(keyword="Agent", max_jobs=1, filter_config=NOTHING_TO_APPLY)
        )

    filter_cls.return_value.clear_filters.assert_called_once()
    filter_cls.return_value.apply_filters.assert_not_called()
