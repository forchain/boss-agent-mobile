"""Unit tests for multi-point inline description probing and ClickableSpan expansion."""

from unittest.mock import MagicMock

from boss_agent.pages import JobDetailPage
from droid_agent_core.gestures import (
    HumanizedGestureExecutor,
    calculate_probe_coordinate,
)


def test_calculate_probe_coordinate_bottom_left_percentage_and_pixels():
    rect = {"x": 100, "y": 200, "width": 400, "height": 500}

    # Case 1: x <= 1.0 (90% width), y > 1.0 (20px up from bottom)
    # bl_x = 100, bl_y = 700
    # X = 100 + 360 = 460
    # Y = 700 - 20 = 680
    cx, cy = calculate_probe_coordinate(rect, [0.90, 20], origin="bottom-left")
    assert cx == 460.0
    assert cy == 680.0

    # Case 2: x <= 1.0 (70% width), y <= 1.0 (10% up from bottom)
    # X = 100 + 280 = 380
    # Y = 700 - 50 = 650
    cx2, cy2 = calculate_probe_coordinate(rect, [0.70, 0.10], origin="bottom-left")
    assert cx2 == 380.0
    assert cy2 == 650.0

    # Case 3: Pixel values for both x and y
    # x = 50px from left, y = 60px up from bottom
    # X = 100 + 50 = 150
    # Y = 700 - 60 = 640
    cx3, cy3 = calculate_probe_coordinate(rect, [50, 60], origin="bottom-left")
    assert cx3 == 150.0
    assert cy3 == 640.0


def test_calculate_probe_coordinate_clamping():
    rect = {"x": 100, "y": 200, "width": 100, "height": 100}

    # x exceeds width, y exceeds height
    cx, cy = calculate_probe_coordinate(rect, [150, 150], origin="bottom-left")
    # Must be clamped within [left + 2, left + width - 2] = [102, 198]
    assert cx == 198.0
    # Y clamped within [top + 2, top + height - 2] = [202, 298]
    assert cy == 202.0


def test_human_click_at_point():
    mock_driver = MagicMock()
    mock_driver.tap = MagicMock()

    executor = HumanizedGestureExecutor(driver=mock_driver)
    executor.human_click_at_point(500.0, 800.0, jitter_px=2.0)

    assert mock_driver.tap.called
    args, kwargs = mock_driver.tap.call_args
    tapped_points = args[0]
    tx, ty = tapped_points[0]
    assert 495.0 <= tx <= 505.0
    assert 795.0 <= ty <= 805.0


def test_expand_description_skips_when_not_truncated():
    mock_driver = MagicMock()
    mock_driver.find_elements.return_value = []

    page = JobDetailPage(driver=mock_driver)

    mock_desc = MagicMock()
    mock_desc.text = "岗位职责: 1. 负责AI工具链研发。任职资格: 1. 熟练使用Python。"
    mock_desc.rect = {"x": 50, "y": 100, "width": 900, "height": 600}

    def custom_find(key, **kwargs):
        if key == "job_detail.expand_btn":
            return None
        if key == "job_detail.desc":
            return mock_desc
        return None

    page.find_by_key = MagicMock(side_effect=custom_find)
    page.gestures.human_click_at_point = MagicMock()

    page.expand_description_if_collapsed()
    # No click should happen because text does not contain "查看更多"
    assert not page.gestures.human_click_at_point.called


def test_expand_description_early_stopping_on_first_hit(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    mock_driver = MagicMock()
    page = JobDetailPage(driver=mock_driver)

    mock_desc = MagicMock()
    mock_desc.text = "岗位职责: 1. 负责AI工具链研发... 查看更多"
    mock_desc.rect = {"x": 50, "y": 100, "width": 900, "height": 600}

    def custom_find(key, **kwargs):
        if key == "job_detail.expand_btn":
            return None
        if key == "job_detail.desc":
            return mock_desc
        return None

    page.find_by_key = MagicMock(side_effect=custom_find)

    def fake_click(x, y, **kw):
        mock_desc.text = "岗位职责: 1. 负责AI工具链研发。任职资格: 1. 熟练掌握Python与自动化。"

    page.gestures.human_click_at_point = MagicMock(side_effect=fake_click)

    page.expand_description_if_collapsed()

    assert page.gestures.human_click_at_point.call_count == 1


def test_expand_description_multi_point_retry_until_success(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    mock_driver = MagicMock()
    page = JobDetailPage(driver=mock_driver)

    mock_desc = MagicMock()
    mock_desc.text = "岗位职责: 1. 负责AI工具链研发... 查看更多"
    mock_desc.rect = {"x": 50, "y": 100, "width": 900, "height": 600}

    def custom_find(key, **kwargs):
        if key == "job_detail.expand_btn":
            return None
        if key == "job_detail.desc":
            return mock_desc
        return None

    page.find_by_key = MagicMock(side_effect=custom_find)

    call_counter = [0]

    def fake_click(x, y, **kw):
        call_counter[0] += 1
        if call_counter[0] == 2:
            mock_desc.text = "岗位职责: 1. 负责AI工具链研发。\n2. 全文已展开。"

    page.gestures.human_click_at_point = MagicMock(side_effect=fake_click)

    page.expand_description_if_collapsed()

    assert page.gestures.human_click_at_point.call_count == 2
