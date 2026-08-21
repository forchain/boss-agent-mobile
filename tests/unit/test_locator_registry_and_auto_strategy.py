"""Unit tests for automatic selector strategy detection and external LocatorRegistry."""

import tempfile
from pathlib import Path

import yaml

from droid_agent_core.locators import (
    By,
    LocatorRegistry,
    parse_selector,
)


def test_parse_selector_auto_strategy_detection():
    # 1. XPath detection
    s1 = parse_selector("//*[@text='确定']")
    assert s1.by == By.XPATH
    assert s1.value == "//*[@text='确定']"

    s1_prefix = parse_selector("xpath=//*[@id='foo']")
    assert s1_prefix.by == By.XPATH
    assert s1_prefix.value == "//*[@id='foo']"

    # 2. Resource ID detection
    s2 = parse_selector("com.hpbr.bosszhipin:id/tv_title")
    assert s2.by == By.ID
    assert s2.value == "com.hpbr.bosszhipin:id/tv_title"

    s2_android_id = parse_selector("android:id/button1")
    assert s2_android_id.by == By.ID

    s2_prefix = parse_selector("id=com.app:id/main_btn")
    assert s2_prefix.by == By.ID
    assert s2_prefix.value == "com.app:id/main_btn"

    # 3. Class Name detection
    s3 = parse_selector("android.widget.EditText")
    assert s3.by == By.CLASS_NAME
    assert s3.value == "android.widget.EditText"

    s3_prefix = parse_selector("class=android.widget.TextView")
    assert s3_prefix.by == By.CLASS_NAME

    # 4. Accessibility ID / Content Description
    s4 = parse_selector("desc=返回")
    assert s4.by == By.ACCESSIBILITY_ID
    assert s4.by.value == "accessibility id"
    assert s4.value == "返回"

    s4_acc = parse_selector("accessibility_id=Back")
    assert s4_acc.by == By.ACCESSIBILITY_ID
    assert s4_acc.value == "Back"

    # 5. UIAutomator string
    s5 = parse_selector('uiautomator=new UiSelector().text("搜索")')
    assert s5.by == By.ANDROID_UIAUTOMATOR
    assert s5.by.value == "-android uiautomator"
    assert s5.value == 'new UiSelector().text("搜索")'

    # 6. Text shortcut
    s6 = parse_selector("text=确定")
    assert s6.by == By.XPATH
    assert s6.by.value == "xpath"
    assert "@text='确定'" in s6.value


def test_locator_registry_loading_and_local_override():
    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = Path(tmpdir) / "locators.yaml"
        local_path = Path(tmpdir) / "locators.local.yaml"

        base_data = {
            "search": {
                "input": "com.test:id/et_search",
                "btn": "//*[@text='搜索']",
                "list": [
                    "com.test:id/first_option",
                    "//*[@text='备选']",
                ],
            },
            "common": {
                "tag": "//*[@text='{text}']",
            },
        }

        # Local override changes search.input and adds a new key
        local_data = {
            "search": {
                "input": "com.test:id/et_search_custom_override",
            },
            "custom_key": "android.widget.Button",
        }

        base_path.write_text(yaml.dump(base_data), encoding="utf-8")
        local_path.write_text(yaml.dump(local_data), encoding="utf-8")

        registry = LocatorRegistry(base_config_path=base_path, local_config_path=local_path)

        # 1. Local override takes priority
        selectors = registry.get_selectors("search.input")
        assert len(selectors) == 1
        assert selectors[0].by == By.ID
        assert selectors[0].value == "com.test:id/et_search_custom_override"

        # 2. Non-overridden base key is preserved
        btn_sel = registry.get_selectors("search.btn")
        assert len(btn_sel) == 1
        assert btn_sel[0].by == By.XPATH
        assert btn_sel[0].value == "//*[@text='搜索']"

        # 3. Fallback list resolution
        list_sels = registry.get_selectors("search.list")
        assert len(list_sels) == 2
        assert list_sels[0].by == By.ID
        assert list_sels[1].by == By.XPATH

        # 4. Formatted template key
        formatted_sels = registry.get_selectors("common.tag", format_args={"text": "硕士"})
        assert len(formatted_sels) == 1
        assert formatted_sels[0].value == "//*[@text='硕士']"

        # 5. Local-only key
        custom_sels = registry.get_selectors("custom_key")
        assert len(custom_sels) == 1
        assert custom_sels[0].by == By.CLASS_NAME
