"""
tests/unit/test_commute_distance_extraction.py
==============================================
Page-object unit tests for detail-page bottom probing and commute distance
parsing (Spec #209, Ticket #211).

The Boss 直聘 search API exposes no distance filter, but the Job Detail Page
renders `<... id/home_tip_vf>` containing "距离家庭住址19.5千米" near its bottom.
Extraction must scroll for it, normalize every unit to kilometres, and fail open
when the widget is absent (no home address configured, remote job, or timeout).
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.models import JobPosting
from boss_agent.pages import JobDetailPage


def _page(locator_for_distance=None, scroll_hits: int = 0) -> JobDetailPage:
    """Build a JobDetailPage whose distance widget appears after N scrolls."""
    mock_driver = MagicMock()
    page = JobDetailPage(driver=mock_driver)
    page._get_window_size = MagicMock(return_value={"width": 1080, "height": 2400})
    page.gestures.human_swipe = MagicMock()
    page._scroll_page_up = MagicMock()

    state = {"scrolls": 0}

    def custom_find(key, **kwargs):
        if key != "job_detail.distance_tip":
            return None
        if state["scrolls"] < scroll_hits:
            return None
        return locator_for_distance

    page.find_by_key = MagicMock(side_effect=custom_find)
    page.gestures.human_swipe.side_effect = lambda *a, **kw: state.__setitem__(
        "scrolls", state["scrolls"] + 1
    )
    page._scroll_page_up.side_effect = lambda *a, **kw: state.__setitem__(
        "scrolls", state["scrolls"] + 1
    )
    return page


def _distance_element(text: str) -> MagicMock:
    elem = MagicMock()
    elem.text = text
    return elem


def test_locators_register_detail_page_distance_widget():
    """The ViewFlipper home_tip_vf widget must be discoverable by key."""
    from droid_agent_core.locators import get_global_locator_registry

    selectors = get_global_locator_registry().get_selectors("job_detail.distance_tip")
    assert selectors, "job_detail.distance_tip must be registered in config/locators.yaml"
    joined = " ".join(s.value for s in selectors)
    assert "home_tip_vf" in joined
    assert "tv_title" in joined


@pytest.mark.parametrize(
    ("raw", "expected_km"),
    [
        ("距离家庭住址19.5千米", 19.5),
        ("距离家庭住址35公里", 35.0),
        ("距住址12km", 12.0),
        ("距离家庭住址800米", 0.8),
        ("距离家庭住址650m", 0.65),
        ("距离家庭住址 52.25 千米", 52.25),
    ],
)
def test_extract_commute_distance_normalizes_every_unit(raw: str, expected_km: float):
    """千米/公里/km stay as-is; 米/m convert to km."""
    page = _page(_distance_element(raw))

    distance_km, text = page.extract_commute_distance()

    assert distance_km == pytest.approx(expected_km)
    assert text == raw


def test_extract_commute_distance_probes_toward_page_bottom():
    """The widget sits below the fold, so extraction must scroll to reveal it."""
    page = _page(_distance_element("距离家庭住址18千米"), scroll_hits=2)

    distance_km, text = page.extract_commute_distance()

    assert distance_km == pytest.approx(18.0)
    assert text == "距离家庭住址18千米"
    assert page._scroll_page_up.call_count == 2


def test_extract_commute_distance_stops_at_scroll_budget():
    """Bounded probing: a permanently absent widget must not scroll forever."""
    page = _page(None)

    distance_km, text = page.extract_commute_distance(max_scrolls=3)

    assert (distance_km, text) == (None, "")
    assert page._scroll_page_up.call_count == 3


def test_extract_commute_distance_fails_open_when_widget_absent():
    """No home address / remote job / timeout: never guess, never reject."""
    page = _page(None)

    assert page.extract_commute_distance(max_scrolls=1) == (None, "")


def test_extract_commute_distance_returns_none_when_text_unparseable():
    """A widget without a numeric distance must not fabricate a value."""
    page = _page(_distance_element("距离家庭住址未知"))

    distance_km, text = page.extract_commute_distance()

    assert distance_km is None
    assert text == "距离家庭住址未知"


@pytest.mark.parametrize("bogus_text", [None, MagicMock(), 42])
def test_extract_commute_distance_fails_open_on_non_string_widget_text(bogus_text):
    """A locator matching the wrong node (text=None, a mock, or a number) must not
    crash the detail inspection: treat it as an absent widget and fail open."""
    elem = MagicMock()
    elem.text = bogus_text
    page = _page(elem)

    assert page.extract_commute_distance() == (None, "")


def test_extract_job_posting_populates_distance_fields_when_probing_enabled(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)

    mock_driver = MagicMock()
    page = JobDetailPage(driver=mock_driver)
    page.wait_for_key = MagicMock(return_value=True)
    page._get_window_size = MagicMock(return_value={"width": 1080, "height": 2400})

    mock_title = MagicMock()
    mock_title.text = "大模型 Agent 平台架构师"
    mock_comp = MagicMock()
    mock_comp.text = "某科技公司"
    mock_sal = MagicMock()
    mock_sal.text = "40-60K"
    mock_desc = MagicMock()
    mock_desc.text = "负责大模型应用与Agent工作流平台建设，覆盖推理链编排与多智能体协同。"
    mock_desc.rect = {"x": 50, "y": 100, "width": 900, "height": 600}
    mock_distance = _distance_element("距离家庭住址19.5千米")

    def custom_find(key, **kwargs):
        return {
            "job_detail.title": mock_title,
            "job_detail.company": mock_comp,
            "job_detail.salary": mock_sal,
            "job_detail.desc": mock_desc,
            "job_detail.distance_tip": mock_distance,
        }.get(key)

    page.find_by_key = MagicMock(side_effect=custom_find)

    posting = page.extract_job_posting(timeout_sec=2.0, probe_commute_distance=True)

    assert posting.commute_distance_km == pytest.approx(19.5)
    assert posting.commute_distance_text == "距离家庭住址19.5千米"


def test_extract_job_posting_skips_probing_when_disabled(monkeypatch):
    """When distance filtering is off, no bottom scroll happens (saves swipe latency)."""
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)

    mock_driver = MagicMock()
    page = JobDetailPage(driver=mock_driver)
    page.wait_for_key = MagicMock(return_value=True)
    page._get_window_size = MagicMock(return_value={"width": 1080, "height": 2400})
    page._scroll_page_up = MagicMock()

    mock_title = MagicMock()
    mock_title.text = "大模型 Agent 平台架构师"
    mock_desc = MagicMock()
    mock_desc.text = "负责大模型应用与Agent工作流平台建设，覆盖推理链编排与多智能体协同。"

    seen_keys: list[str] = []

    def custom_find(key, **kwargs):
        seen_keys.append(key)
        return {
            "job_detail.title": mock_title,
            "job_detail.desc": mock_desc,
        }.get(key)

    page.find_by_key = MagicMock(side_effect=custom_find)

    posting = page.extract_job_posting(timeout_sec=2.0)

    assert posting.commute_distance_km is None
    assert posting.commute_distance_text == ""
    assert "job_detail.distance_tip" not in seen_keys
    assert page._scroll_page_up.call_count == 0


def _probing_detail_page(distance_text: str | None = None) -> tuple[JobDetailPage, list[str]]:
    """A detail page whose JD is already expanded, recording every locator key it probes.

    ``distance_text`` puts the bottom commute widget on the screen; ``_scroll_page_up``
    stays a mock so a test can assert how much gesture budget the probe actually spent.
    """
    page = JobDetailPage(driver=MagicMock())
    page.wait_for_key = MagicMock(return_value=True)
    page._get_window_size = MagicMock(return_value={"width": 1080, "height": 2400})
    page._scroll_page_up = MagicMock()

    title = MagicMock()
    title.text = "大模型 Agent 平台架构师"
    desc = MagicMock()
    desc.text = "负责大模型应用与Agent工作流平台建设，覆盖推理链编排与多智能体协同。"

    elements: dict[str, MagicMock] = {"job_detail.title": title, "job_detail.desc": desc}
    if distance_text is not None:
        elements["job_detail.distance_tip"] = _distance_element(distance_text)

    probed_keys: list[str] = []

    def custom_find(key, **kwargs):
        probed_keys.append(key)
        return elements.get(key)

    page.find_by_key = MagicMock(side_effect=custom_find)
    return page, probed_keys


def test_extract_job_posting_skips_probing_for_headhunter_posting(monkeypatch):
    """A headhunter posting conceals the hiring enterprise and its address, so the
    platform never renders the distance tip for it (Ticket #255). Probing would burn
    up to 3 swipes plus element-discovery timeouts on a widget that cannot exist, so
    the page must not scroll at all even while the ceiling is active."""
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)
    page, probed_keys = _probing_detail_page(distance_text="距离家庭住址19.5千米")

    posting = page.extract_job_posting(
        timeout_sec=2.0, probe_commute_distance=True, is_headhunter=True
    )

    assert posting.commute_distance_km is None
    assert posting.commute_distance_text == ""
    assert "job_detail.distance_tip" not in probed_keys
    assert page._scroll_page_up.call_count == 0


def test_extract_job_posting_probes_when_headhunter_status_unknown(monkeypatch):
    """Fail-open: an unknown recruitment channel must keep probing, because a direct
    hire misclassified as unknown would otherwise never be distance-screened."""
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)
    page, probed_keys = _probing_detail_page(distance_text="距离家庭住址19.5千米")

    posting = page.extract_job_posting(
        timeout_sec=2.0, probe_commute_distance=True, is_headhunter=None
    )

    assert "job_detail.distance_tip" in probed_keys
    assert posting.commute_distance_km == pytest.approx(19.5)
    assert posting.commute_distance_text == "距离家庭住址19.5千米"


def test_job_posting_defaults_distance_fields():
    posting = JobPosting(
        title="AI 工程师",
        company_name="某公司",
        salary_range="30-50K",
        job_description="负责AI应用落地。",
    )

    assert posting.commute_distance_km is None
    assert posting.commute_distance_text == ""
