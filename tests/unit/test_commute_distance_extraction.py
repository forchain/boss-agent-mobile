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


def test_extract_commute_distance_default_budget_reaches_widget_beyond_three_scrolls():
    """An expanded multi-thousand-character JD pushes the widget further down than the
    original 3-swipe budget could cover (Ticket #263)."""
    page = _page(_distance_element("距离家庭住址19.5千米"), scroll_hits=5)

    distance_km, text = page.extract_commute_distance()

    assert distance_km == pytest.approx(19.5)
    assert text == "距离家庭住址19.5千米"
    assert page._scroll_page_up.call_count == 5


def test_extract_commute_distance_default_budget_is_relaxed_to_at_least_six_scrolls():
    """Absent widget: fail open only after spending the relaxed default budget.

    Both bounds are asserted on purpose: the ticket's floor of six swipes must hold even if
    the constant is retuned, and the default the probe actually uses must be that constant
    rather than a number copied into the method.
    """
    from boss_agent.pages import COMMUTE_PROBE_MAX_SCROLLS

    page = _page(None)

    assert page.extract_commute_distance() == (None, "")
    assert page._scroll_page_up.call_count >= 6
    assert page._scroll_page_up.call_count == COMMUTE_PROBE_MAX_SCROLLS


def test_extract_commute_distance_honours_explicit_budget_override():
    """Callers can raise or lower the budget; the default is not hard-wired."""
    page = _page(None)

    assert page.extract_commute_distance(max_scrolls=9) == (None, "")
    assert page._scroll_page_up.call_count == 9


def test_extract_commute_distance_stride_covers_most_of_the_viewport():
    """Each downward swipe must cover 50-55% of the viewport height: more ground per
    gesture than the original 40%, while staying clear of the top/bottom chrome."""
    page = _page(None)

    page.extract_commute_distance(max_scrolls=1)

    stride_px = page._scroll_page_up.call_args.args[0]
    assert 0.50 * 2400 <= stride_px <= 0.55 * 2400


def _bottom_limited_page(bottom_after_swipes: int, freeze_offset: float = -20.0) -> JobDetailPage:
    """A page whose scroll container cannot move past ``bottom_after_swipes`` swipes.

    Mirrors a device at the end of a long expanded JD: the description anchor keeps
    reporting the same screen position once the container is fully scrolled.
    """
    page = JobDetailPage(driver=MagicMock())
    page._get_window_size = MagicMock(return_value={"width": 1080, "height": 2400})
    page.gestures.human_swipe = MagicMock()

    desc = MagicMock()
    desc.text = "负责大模型应用与Agent工作流平台建设。" * 400
    state = {"swipes": 0}

    def _scroll(*_args, **_kwargs):
        state["swipes"] += 1

    page._scroll_page_up = MagicMock(side_effect=_scroll)

    def custom_find(key, **kwargs):
        if key == "job_detail.desc":
            frozen = max(freeze_offset, -10.0 * min(state["swipes"], bottom_after_swipes))
            desc.rect = {"x": 0, "y": frozen, "width": 1080, "height": 4000}
            return desc
        return None

    page.find_by_key = MagicMock(side_effect=custom_find)
    return page


def _stalling_then_moving_page() -> JobDetailPage:
    """A page whose anchor reports one stalled position while the content still moves.

    Models an anchor that has scrolled just above the viewport, where the driver clamps its
    bounds for a swipe instead of letting them fall.
    """
    page = _bottom_limited_page(bottom_after_swipes=1)
    # The anchor freezes for one swipe, then keeps moving again (which ends the clamp).
    desc_state = {"observations": 0}

    def custom_find(key, **kwargs):
        if key != "job_detail.desc":
            return None
        desc_state["observations"] += 1
        desc = MagicMock()
        desc.text = "负责大模型应用与Agent工作流平台建设。" * 400
        y = 0.0 if desc_state["observations"] <= 2 else -10.0 * (desc_state["observations"] - 2)
        desc.rect = {"x": 0, "y": y, "width": 1080, "height": 4000}
        return desc

    page.find_by_key = MagicMock(side_effect=custom_find)
    return page


def test_extract_commute_distance_stops_early_when_container_reaches_bottom():
    """A container that can no longer move must end the probe before the budget, instead
    of burning the remaining swipes on a widget that cannot come into view. Two stalled
    swipes (not one) are required before the probe accepts the bottom, so the extra swipe
    is the cost of not trusting a single clamped reading."""
    page = _bottom_limited_page(bottom_after_swipes=2)

    assert page.extract_commute_distance() == (None, "")
    assert page._scroll_page_up.call_count == 4


def test_extract_commute_distance_ignores_a_single_stalled_reading():
    """One immobile observation is not proof of the bottom: the probe must keep spending
    its budget rather than call 'bottom' while the page is still moving (Ticket #263)."""
    page = _stalling_then_moving_page()

    assert page.extract_commute_distance() == (None, "")
    assert page._scroll_page_up.call_count == 6


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


def test_extract_job_posting_honours_relaxed_probe_budget(monkeypatch):
    """The detail-extraction flow must inherit the relaxed budget: a widget that only
    appears after four swipes is unreachable under the old 3-swipe default (Ticket #263)."""
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)

    page = JobDetailPage(driver=MagicMock())
    page.wait_for_key = MagicMock(return_value=True)
    page._get_window_size = MagicMock(return_value={"width": 1080, "height": 2400})

    state = {"swipes": 0}
    page._scroll_page_up = MagicMock(
        side_effect=lambda *a, **kw: state.__setitem__("swipes", state["swipes"] + 1)
    )

    title = MagicMock()
    title.text = "大模型 Agent 平台架构师"
    desc = MagicMock()
    desc.text = "负责大模型应用与Agent工作流平台建设，覆盖推理链编排与多智能体协同。"
    distance = _distance_element("距离家庭住址19.5千米")

    def custom_find(key, **kwargs):
        if key == "job_detail.distance_tip":
            return distance if state["swipes"] >= 4 else None
        return {"job_detail.title": title, "job_detail.desc": desc}.get(key)

    page.find_by_key = MagicMock(side_effect=custom_find)

    posting = page.extract_job_posting(timeout_sec=2.0, probe_commute_distance=True)

    assert posting.commute_distance_km == pytest.approx(19.5)
    assert posting.commute_distance_text == "距离家庭住址19.5千米"
    assert state["swipes"] == 4


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
