"""
tests/unit/test_smoke_harness_extraction.py
===========================================
Smoke Harness extraction contract (Criterion 4), driven entirely by MagicMock.

Every driver interaction is mocked, so this belongs to the fast unit tier: it needs no
emulator, no Appium session, and no child process (spec #247, ticket #249).
"""

from unittest.mock import MagicMock, patch

import pytest
from _feed_harness import GOOD_JD, ScriptedFeed, _card, _detail_page, script_pages

from boss_agent.enums import AuthStatus
from boss_agent.job_entities import JobPosting
from boss_agent.screening_policy import ScreeningPolicy
from boss_agent.search_entities import FilterConfig, SearchConfig
from boss_agent.workflows import SmokeHarness, TakeoverHandler


def _harness(driver, takeover_handler=None, **kwargs) -> SmokeHarness:
    """A harness whose takeover is pre-confirmed, so the auth gate is not the subject."""
    return SmokeHarness(
        driver=driver,
        takeover_handler=takeover_handler or TakeoverHandler(driver, auto_confirm_for_test=True),
        **kwargs,
    )


def test_smoke_harness_returns_the_job_the_feed_pipeline_extracted():
    """The harness is an adapter: what it returns is what the pipeline extracted.

    Issue #390. It used to open the first card and parse the detail page itself, through
    eight page objects it owned; now the feed run belongs to ``JobFeedPipeline`` and the
    harness hands back a posting from that run. A typed ``JobPosting`` is the contract —
    the return type a smoke test has always promised its caller.
    """
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    posting = JobPosting(
        title="资深 Python / Android 自动化专家",
        company_name="北京智联前沿科技有限公司",
        salary_range="40-65K·16薪",
        job_description=(
            "岗位职责：\n"
            "1. 负责大规模移动端自动化框架设计与高可靠执行引擎开发；\n"
            "2. 深度优化反爬风控拟真轨迹与验证码智能接管策略；\n"
            "3. 具备 5 年以上 Python / Android SDK / Appium 深度实战经验。"
        ),
    )
    harness = _harness(
        driver,
        search_config=SearchConfig(keyword=None),
        filter_config=FilterConfig(
            education=None, salary=None, experience=None, activity=None, company_scales=[]
        ),
        enable_greeting_draft=False,
    )
    script_pages(
        harness.pipeline,
        feed=ScriptedFeed(
            [[_card("资深 Python / Android 自动化专家", "北京智联前沿科技有限公司")]]
        ),
        detail=_detail_page(posting=posting),
    )

    with patch("time.sleep", return_value=None):
        job = harness.run_smoke_test()

    assert isinstance(job, JobPosting)
    assert job.title == "资深 Python / Android 自动化专家"
    assert job.company_name == "北京智联前沿科技有限公司"
    assert job.salary_range == "40-65K·16薪"
    assert len(job.job_description) >= 20
    assert "岗位职责" in job.job_description


def test_a_run_that_extracted_nothing_fails_loudly_instead_of_returning_nothing():
    """A smoke test that verified no posting is a failed smoke test.

    Issue #390. The old runner extracted the first card and returned it whatever screening
    then made of it; the pipeline withholds a posting the run withdraws, so a run can end
    with none at all. That cannot pass silently: the return type promises a posting, and
    the caller of a verification run acts on its verdict. The failure has to name the run's
    outcome and reason, because "nothing extracted" on a device is nearly always one of
    those, not a mystery.
    """
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    harness = _harness(
        driver,
        search_config=SearchConfig(keyword=None),
        filter_config=FilterConfig(
            education=None, salary=None, experience=None, activity=None, company_scales=[]
        ),
        # The posting sits in a blacklisted district: the run reaches its detail page and
        # then withdraws it, which is exactly the case that leaves no posting behind.
        screening_policy=ScreeningPolicy(
            business_district_blacklist=["陆家嘴"], max_commute_distance_km=None
        ),
        enable_greeting_draft=False,
    )
    script_pages(
        harness.pipeline,
        feed=ScriptedFeed([[_card("AI Agent 平台工程师", "智元创新")]]),
        detail=_detail_page(
            posting=JobPosting(
                title="AI Agent 平台工程师",
                company_name="智元创新",
                salary_range="40-60K",
                job_description=GOOD_JD,
                location_line="上海·浦东新区·陆家嘴",
            )
        ),
    )

    with pytest.raises(RuntimeError) as failure:
        harness.run_smoke_test()

    message = str(failure.value)
    assert "filtered_by_app_rule" in message
    assert "陆家嘴" in message


def test_an_unauthenticated_session_never_enters_the_feed():
    """A captcha stops the run before it starts, not after it has read the feed.

    Issue #390, AC3. The collapse moved feed discovery into the pipeline, and the pipeline
    knows nothing about challenges — it assumes a usable session. So the check that proves
    one has to stay in the adapter, in front of the run. This pins both halves: the run
    fails, and it fails having touched no card — a challenge is a reason to stop, not to
    start browsing somebody's job list anyway.
    """
    driver = MagicMock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    takeover = MagicMock()
    takeover.check_and_handle_takeover.return_value = AuthStatus.CHALLENGE_REQUIRED

    harness = _harness(
        driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword=None),
        enable_greeting_draft=False,
    )
    list_page = MagicMock()
    script_pages(harness.pipeline, feed=list_page, detail=_detail_page())

    with pytest.raises(RuntimeError, match="CHALLENGE_REQUIRED"):
        harness.run_smoke_test()

    list_page.extract_visible_job_cards.assert_not_called()


def test_smoke_harness_end_to_end_job_detail_extraction():
    """Verify that SmokeHarness executes the full lifecycle and parses a JobPosting.

    Issue #390: the lifecycle now runs inside ``JobFeedPipeline``. A driver double cannot
    produce a parseable feed card, so the run falls through to the pipeline's single-screen
    evaluation — still the real detail page object reading real elements, which is what
    this pins: the posting that comes back was parsed off the screen, not invented.
    """
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # Mock page elements
    mock_startup_elem = MagicMock()
    mock_startup_elem.rect = {"x": 200, "y": 1800, "width": 680, "height": 100}

    mock_job_card = MagicMock()
    mock_job_card.rect = {"x": 50, "y": 300, "width": 980, "height": 220}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深 Python / Android 自动化专家"

    mock_company_elem = MagicMock()
    mock_company_elem.text = "北京智联前沿科技有限公司"

    mock_salary_elem = MagicMock()
    mock_salary_elem.text = "40-65K·16薪"

    mock_desc_elem = MagicMock()
    mock_desc_elem.text = (
        "岗位职责：\n"
        "1. 负责大规模移动端自动化框架设计与高可靠执行引擎开发；\n"
        "2. 深度优化反爬风控拟真轨迹与验证码智能接管策略；\n"
        "3. 具备 5 年以上 Python / Android SDK / Appium 深度实战经验。"
    )
    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 950, "y": 100, "width": 80, "height": 80}

    def mock_find_elements(by, value):
        if "同意" in value or "好的" in value:
            return [mock_startup_elem]
        if "ly_menu" in value or "search" in value:
            return [mock_search_icon]
        if "tv_job_name" in value or "job_title" in value:
            return [mock_title_elem]
        if "tv_company_name" in value or "company_name" in value:
            return [mock_company_elem]
        if "tv_job_salary" in value or "salary" in value:
            return [mock_salary_elem]
        if "tv_job_desc" in value or "job_description" in value:
            return [mock_desc_elem]
        if "job_name" in value or "tv_position_name" in value:
            return [mock_job_card]
        if "ly_menu" in value or "search" in value or "搜索" in value:
            return [mock_job_card]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    from boss_agent.search_entities import FilterConfig, SearchConfig

    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword=None),
        filter_config=FilterConfig(
            education=None, salary=None, experience=None, activity=None, company_scales=[]
        ),
        enable_greeting_draft=False,
    )

    with patch("time.sleep", return_value=None):
        job_posting = harness.run_smoke_test()

    # Assert Criterion 4 specifications
    assert isinstance(job_posting, JobPosting)
    assert job_posting.title == "资深 Python / Android 自动化专家"
    assert job_posting.company_name == "北京智联前沿科技有限公司"
    assert job_posting.salary_range == "40-65K·16薪"
    assert len(job_posting.job_description) >= 20
    assert "岗位职责" in job_posting.job_description


def test_smoke_harness_with_search_enabled():
    """Verify that SmokeHarness executes search flow when search keyword is configured."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 950, "y": 100, "width": 80, "height": 80}

    mock_input_elem = MagicMock()
    mock_input_elem.rect = {"x": 150, "y": 120, "width": 700, "height": 60}

    mock_submit_elem = MagicMock()
    mock_submit_elem.rect = {"x": 900, "y": 200, "width": 100, "height": 60}

    mock_job_card = MagicMock()
    mock_job_card.rect = {"x": 50, "y": 300, "width": 980, "height": 220}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "Agent 开发工程师"

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 500, "y": 100, "width": 80, "height": 40}

    def mock_find_elements(by, value):
        if "et_search" in value or "EditText" in value:
            return [mock_input_elem]
        if "tv_search" in value or "搜索" in value:
            return [mock_submit_elem]
        if "ly_menu" in value or "search_icon" in value:
            return [mock_search_icon]
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "job_name" in value or "tv_position_name" in value or "cl_card_container" in value:
            return [mock_job_card]
        if "btn_confirm" in value or "确定" in value or "筛选" in value:
            return [mock_btn]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=None,  # default is SearchConfig(keyword="agent")
        filter_config=None,
        enable_greeting_draft=False,
    )

    with patch("time.sleep", return_value=None):
        job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
    mock_input_elem.send_keys.assert_called()


def test_smoke_harness_with_search_disabled():
    """Verify that SmokeHarness skips search when keyword is None."""
    from boss_agent.search_entities import FilterConfig, SearchConfig

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_input_elem = MagicMock()
    mock_job_card = MagicMock()
    mock_job_card.rect = {"x": 50, "y": 300, "width": 980, "height": 220}
    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深架构师"
    mock_search_icon = MagicMock()
    mock_search_icon.rect = {"x": 950, "y": 100, "width": 80, "height": 80}

    def mock_find_elements(by, value):
        if "ly_menu" in value or "search" in value:
            return [mock_search_icon]
        if "et_search" in value:
            return [mock_input_elem]
        if "job_name" in value or "tv_position_name" in value or "cl_card_container" in value:
            return [mock_job_card]
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "ly_menu" in value or "search" in value or "搜索" in value:
            return [mock_job_card]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword=None),
        filter_config=FilterConfig(
            education=None, salary=None, experience=None, activity=None, company_scales=[]
        ),
        enable_greeting_draft=False,
    )

    with patch("time.sleep", return_value=None):
        job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)
    mock_input_elem.send_keys.assert_not_called()
