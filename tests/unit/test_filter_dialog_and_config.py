"""Unit tests for FilterConfig and FilterDialogPage."""

from unittest.mock import MagicMock, patch

from boss_agent.config_realm import DEFAULT_TOP_SALARY_TIER, salary_options
from boss_agent.models import FilterConfig
from boss_agent.pages import FilterDialogPage


def test_filter_config_defaults_and_validation():
    cfg = FilterConfig()
    assert cfg.education == "硕士"
    # The default must name a tier the app's filter dialog actually offers (issue #337).
    # It used to be "5万元以上", which exists in neither the legacy nor the current ladder.
    assert cfg.salary == DEFAULT_TOP_SALARY_TIER
    assert cfg.salary in salary_options({})
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


def test_smoke_harness_with_filter_config():
    from boss_agent.models import JobPosting, SearchConfig
    from boss_agent.workflows import SmokeHarness, TakeoverHandler

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深 Agent 架构师"

    def mock_find_elements(by, value):
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "chat" in value or "editText_with_scrollbar" in value or "btn_chat" in value:
            return []
        return [mock_btn]

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword="agent"),
        filter_config=FilterConfig(),
    )

    job = harness.run_smoke_test()
    assert isinstance(job, JobPosting)


def test_smoke_harness_clears_filters_when_no_filter_config():
    from boss_agent.models import FilterConfig, JobPosting, SearchConfig
    from boss_agent.workflows import SmokeHarness, TakeoverHandler

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    mock_btn = MagicMock()
    mock_btn.rect = {"x": 50, "y": 50, "width": 100, "height": 50}

    mock_title_elem = MagicMock()
    mock_title_elem.text = "资深 Agent 架构师"

    def mock_find_elements(by, value):
        if "tv_job_name" in value:
            return [mock_title_elem]
        if "chat" in value or "editText_with_scrollbar" in value or "btn_chat" in value:
            return []
        return [mock_btn]

    mock_driver.find_elements.side_effect = mock_find_elements

    takeover = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    empty_cfg = FilterConfig(
        education=None,
        salary=None,
        experience=None,
        activity=None,
        company_scales=[],
    )
    harness = SmokeHarness(
        driver=mock_driver,
        takeover_handler=takeover,
        search_config=SearchConfig(keyword="agent"),
        filter_config=empty_cfg,
    )

    with patch.object(harness.filter_dialog, "clear_filters") as mock_clear:
        job = harness.run_smoke_test()
        assert isinstance(job, JobPosting)
        mock_clear.assert_called_once()


def test_filter_dialog_selects_all_new_salary_tiers():
    """AC 1: select_option reliably selects all new salary tiers."""
    from boss_agent.config_realm import DEFAULT_SALARY_OPTIONS

    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    clicked_options = []

    def mock_find_elements(by, value):
        for tier in DEFAULT_SALARY_OPTIONS:
            if f"@text='{tier}'" in value or f"text='{tier}'" in value:
                elem = MagicMock()
                elem.rect = {"x": 300, "y": 500, "width": 200, "height": 60}
                elem._tier_name = tier
                return [elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements
    page = FilterDialogPage(mock_driver)

    with patch.object(
        page.gestures,
        "human_click",
        side_effect=lambda elem: clicked_options.append(getattr(elem, "_tier_name", None)),
    ):
        for tier in DEFAULT_SALARY_OPTIONS:
            assert page.select_option(tier) is True

    assert clicked_options == DEFAULT_SALARY_OPTIONS


def test_filter_dialog_selects_salary_casing_and_unit_synonyms():
    """AC 3: FILTER_OPTION_SYNONYMS includes casing/unit synonyms for new salary tiers."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # The screen only displays the canonical new tiers
    canonical_tiers = ["15K以下", "15-25K", "25-35K", "35-45K", "45K以上"]
    clicked_tiers = []

    def mock_find_elements(by, value):
        for tier in canonical_tiers:
            if f"@text='{tier}'" in value or f"text='{tier}'" in value:
                elem = MagicMock()
                elem.rect = {"x": 300, "y": 500, "width": 200, "height": 60}
                elem._tier_name = tier
                return [elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements
    page = FilterDialogPage(mock_driver)

    synonym_test_cases = [
        # 15K以下 variants
        ("15k以下", "15K以下"),
        ("1.5万以下", "15K以下"),
        ("1.5万元以下", "15K以下"),
        ("15000元以下", "15K以下"),
        # 15-25K variants
        ("15-25k", "15-25K"),
        ("1.5-2.5万", "15-25K"),
        ("1.5-2.5万元", "15-25K"),
        # 25-35K variants
        ("25-35k", "25-35K"),
        ("2.5-3.5万", "25-35K"),
        ("2.5-3.5万元", "25-35K"),
        # 35-45K variants
        ("35-45k", "35-45K"),
        ("3.5-4.5万", "35-45K"),
        ("3.5-4.5万元", "35-45K"),
        # 45K以上 variants
        ("45k以上", "45K以上"),
        ("4.5万以上", "45K以上"),
        ("4.5万元以上", "45K以上"),
        ("45000以上", "45K以上"),
        ("45000元以上", "45K以上"),
    ]

    with patch.object(
        page.gestures,
        "human_click",
        side_effect=lambda elem: clicked_tiers.append(getattr(elem, "_tier_name", None)),
    ):
        for input_val, expected_tier in synonym_test_cases:
            clicked_tiers.clear()
            assert page.select_option(input_val) is True, f"Failed to select {input_val}"
            assert clicked_tiers == [expected_tier], (
                f"For {input_val}, expected {expected_tier} but clicked {clicked_tiers}"
            )


def test_filter_dialog_navigates_to_salary_category_tab_when_not_immediately_visible():
    """AC 2: If salary section not visible on right panel, clicks '薪资' tab on left first."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    salary_tab_clicked = False
    events = []

    mock_salary_tab = MagicMock()
    mock_salary_tab._name = "tab:薪资"

    mock_salary_option = MagicMock()
    mock_salary_option._name = "option:45K以上"

    def mock_find_elements(by, value):
        # Category tab on the left
        if "薪资" in value and (
            "category_tab" in value
            or "tv_category_name" in value
            or "text='薪资'" in value
            or "@text='薪资'" in value
        ):
            return [mock_salary_tab]
        # Right panel options: 45K以上 is ONLY visible after clicking 薪资 tab
        if ("45K以上" in value or "45k以上" in value) and salary_tab_clicked:
            return [mock_salary_option]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements
    page = FilterDialogPage(mock_driver)

    def record_click(elem):
        nonlocal salary_tab_clicked
        name = getattr(elem, "_name", str(elem))
        events.append(name)
        if name == "tab:薪资":
            salary_tab_clicked = True

    with patch.object(page.gestures, "human_click", side_effect=record_click):
        success = page.select_option("45K以上", auto_scroll=False)
        assert success is True
        assert events == ["tab:薪资", "option:45K以上"]


def test_filter_dialog_legacy_salary_fallback_mapping():
    """AC 4: Legacy stored values (5万元以上, 50K以上, etc.) map gracefully to closest valid option."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # Screen only has modern tiers: 15K以下, 15-25K, 25-35K, 35-45K, 45K以上
    canonical_tiers = ["15K以下", "15-25K", "25-35K", "35-45K", "45K以上"]
    clicked = []

    def mock_find_elements(by, value):
        for tier in canonical_tiers:
            if f"@text='{tier}'" in value or f"text='{tier}'" in value:
                elem = MagicMock()
                elem._tier = tier
                return [elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements
    page = FilterDialogPage(mock_driver)

    with patch.object(
        page.gestures,
        "human_click",
        side_effect=lambda elem: clicked.append(getattr(elem, "_tier", None)),
    ):
        # 5万元以上 and 50K以上 map to ceiling 45K以上
        clicked.clear()
        assert page.select_option("5万元以上") is True
        assert clicked == ["45K以上"]

        clicked.clear()
        assert page.select_option("50K以上") is True
        assert clicked == ["45K以上"]

        clicked.clear()
        assert page.select_option("50k以上") is True
        assert clicked == ["45K以上"]

        # Legacy lower bounds map to closest available option
        clicked.clear()
        assert page.select_option("3K以下") is True
        assert clicked == ["15K以下"]

        clicked.clear()
        assert page.select_option("3000元以下") is True
        assert clicked == ["15K以下"]

        clicked.clear()
        assert page.select_option("3-5K") is True
        assert clicked == ["15K以下"]

        clicked.clear()
        assert page.select_option("5-10K") is True
        assert clicked == ["15K以下"]

        clicked.clear()
        assert page.select_option("10-20K") is True
        assert clicked == ["15-25K"]

        # 20-50K and 2-5万 ranges map to closest tier 25-35K
        clicked.clear()
        assert page.select_option("20-50K") is True
        assert clicked == ["25-35K"]

        clicked.clear()
        assert page.select_option("2-5万") is True
        assert clicked == ["25-35K"]

        clicked.clear()
        assert page.select_option("2-5万元") is True
        assert clicked == ["25-35K"]

        clicked.clear()
        assert page.select_option("1-2万元") is True
        assert clicked == ["15-25K"]


def test_filter_dialog_apply_filters_multi_category_navigation():
    """Verify apply_filters navigates categories across multiple dimensions."""
    mock_driver = MagicMock()
    mock_driver.get_window_size.return_value = {"width": 1080, "height": 2400}

    # Simulate dialog state: dialog is open
    current_category = "学历"  # Dialog opens on education
    clicked_elements = []

    def mock_find_elements(by, value):
        # Dialog buttons
        if "btn_reset" in value or "重置" in value:
            btn = MagicMock()
            btn._tag = "btn:reset"
            return [btn]
        if "btn_confirm" in value or "确定" in value:
            btn = MagicMock()
            btn._tag = "btn:confirm"
            return [btn]
        if "filter_entry" in value or "filter" in value:
            btn = MagicMock()
            btn._tag = "btn:filter_entry"
            return [btn]

        # Category tabs on left
        for cat in ["学历", "薪资", "经验"]:
            if f"@text='{cat}'" in value or f"text='{cat}'" in value:
                tab = MagicMock()
                tab._tag = f"tab:{cat}"
                return [tab]

        # Right panel options - only visible when corresponding category tab is active
        if current_category == "学历" and ("本科" in value or "本科学历" in value):
            opt = MagicMock()
            opt._tag = "option:本科"
            return [opt]
        if current_category == "薪资" and ("45K以上" in value or "45k以上" in value):
            opt = MagicMock()
            opt._tag = "option:45K以上"
            return [opt]
        if current_category == "经验" and ("3-5年" in value or "3-5年经验" in value):
            opt = MagicMock()
            opt._tag = "option:3-5年"
            return [opt]

        return []

    mock_driver.find_elements.side_effect = mock_find_elements
    page = FilterDialogPage(mock_driver)

    def record_click(elem):
        nonlocal current_category
        tag = getattr(elem, "_tag", str(elem))
        clicked_elements.append(tag)
        if tag.startswith("tab:"):
            current_category = tag.split(":", 1)[1]

    with (
        patch.object(page, "is_dialog_open", return_value=True),
        patch.object(page.gestures, "human_click", side_effect=record_click),
    ):
        cfg = FilterConfig(
            education="本科",
            salary="50K以上",  # Legacy salary mapping to 45K以上, requires 薪资 tab
            experience="3-5年",  # Requires 经验 tab
        )
        assert page.apply_filters(cfg) is True

    # Expected sequence:
    # 1. Reset filter
    # 2. Option "本科" (visible on initial 学历 panel)
    # 3. Category tab "薪资", then Option "45K以上"
    # 4. Category tab "经验", then Option "3-5年"
    # 5. Confirm button
    assert clicked_elements == [
        "btn:reset",
        "option:本科",
        "tab:薪资",
        "option:45K以上",
        "tab:经验",
        "option:3-5年",
        "btn:confirm",
    ]
