"""
tests/unit/test_location_line_matching.py
=========================================
The Job Detail Page location line, the one place a metro station is published
(Issue #332), and the two screening lists that have to see it (Issue #333).

The card's location facet (`tv_distance`) names a district and nothing finer, so a
station an operator wants to refuse — or to measure — can only be judged after the
detail page has been read. Two consequences are asserted here:

* the line is parsed into the parts the filters and the UI need, and degrades to
  nothing rather than raising when the platform renders no line (or a node without
  readable text);
* the same two lists are consulted at the detail stage, where the station is finally
  known — a hit rejects before the LLM is asked anything, and a station-only 考察名单
  match can still authorise the bottom commute probe the card stage declined.
"""

from unittest.mock import MagicMock

import pytest

from boss_agent.models import JobLocationLine, ScreeningPolicy
from boss_agent.pages import JobDetailPage

FULL_LINE = "上海·浦东新区·张江(近13/16号线华夏中路地铁站)"


# --------------------------------------------------------------------------- #
# Parsing the line
# --------------------------------------------------------------------------- #
def test_full_line_splits_into_city_district_quarter_lines_and_station():
    parsed = JobLocationLine.parse(FULL_LINE)

    assert parsed.city == "上海"
    assert parsed.district == "浦东新区"
    assert parsed.business_district == "张江"
    assert parsed.metro_lines == "13/16号线"
    assert parsed.metro_station == "华夏中路地铁站"
    # The platform's own rendering is what policy lists match over and what an audit
    # reason quotes, so it survives verbatim.
    assert parsed.raw == FULL_LINE
    assert parsed.match_text == FULL_LINE


def test_a_line_without_a_metro_suffix_records_no_station():
    parsed = JobLocationLine.parse("上海·浦东新区·张江")

    assert parsed.business_district == "张江"
    assert parsed.metro_lines == ""
    assert parsed.metro_station == ""
    assert parsed.match_text == "上海·浦东新区·张江"


def test_a_station_quoted_without_a_line_is_still_a_place():
    """``近张江高科`` names something worth filtering on even with no line spec."""
    parsed = JobLocationLine.parse("上海(近张江高科)")

    assert parsed.city == "上海"
    assert parsed.metro_lines == ""
    assert parsed.metro_station == "张江高科"


def test_full_width_parentheses_and_a_single_line_number():
    parsed = JobLocationLine.parse("上海·浦东新区·张江（近2号线广兰路地铁站）")

    assert parsed.metro_lines == "2号线"
    assert parsed.metro_station == "广兰路地铁站"


@pytest.mark.parametrize("raw", ["", "   ", "上海"])
def test_a_line_that_names_no_station_parses_to_no_station(raw: str):
    """A city-only line is ordinary, not a failure: it must not raise or invent a station."""
    parsed = JobLocationLine.parse(raw)

    assert parsed.metro_station == ""
    assert parsed.raw == raw.strip()


# --------------------------------------------------------------------------- #
# The location line is registered and read
# --------------------------------------------------------------------------- #
def test_locators_register_the_detail_page_location_line():
    from droid_agent_core.locators import get_global_locator_registry

    selectors = get_global_locator_registry().get_selectors("job_detail.location_line")
    assert selectors, "job_detail.location_line must be registered in config/locators.yaml"
    assert "tv_required_location" in " ".join(s.value for s in selectors)


def _page_with_line(line_text, probe_result=(22.0, "距离家庭住址22千米")) -> JobDetailPage:
    """A JobDetailPage whose header carries ``line_text`` and nothing else worth finding."""
    page = JobDetailPage(driver=MagicMock())
    page._get_window_size = MagicMock(return_value={"width": 1080, "height": 2400})
    page.gestures.human_swipe = MagicMock()
    page._scroll_page_up = MagicMock()
    page.expand_description_if_collapsed = MagicMock()
    page._current_description = "岗位职责：主导企业级大模型应用与 Agent 平台建设。"

    def _elem(text):
        elem = MagicMock()
        elem.text = text
        return elem

    def find(key, **kwargs):
        return {
            "job_detail.title": _elem("Agent 平台工程师"),
            "job_detail.company": _elem("智元创新"),
            "job_detail.salary": _elem("40-60K"),
            "job_detail.location_line": _elem(line_text),
        }.get(key)

    page.find_by_key = MagicMock(side_effect=find)
    page.find_now = MagicMock(side_effect=find)
    page.extract_commute_distance = MagicMock(return_value=probe_result)
    return page


def test_the_detail_page_records_the_station_it_read():
    posting = _page_with_line(FULL_LINE).extract_job_posting(timeout_sec=0.1)

    assert posting.location_line == FULL_LINE
    assert posting.metro_station == "华夏中路地铁站"
    assert posting.metro_lines == "13/16号线"


def test_a_node_without_readable_text_records_no_station_and_does_not_raise():
    """The driver can match a container with no text; that reads as "no station"."""
    page = _page_with_line(MagicMock())

    posting = page.extract_job_posting(timeout_sec=0.1)

    assert posting.location_line == ""
    assert posting.metro_station == ""


# --------------------------------------------------------------------------- #
# The station can authorise the commute probe the card stage declined (spec #328/#333)
# --------------------------------------------------------------------------- #
def test_a_station_only_listing_upgrades_the_commute_probe():
    """The card stage saw a district nobody listed; the line names a station somebody did.

    The page already holds the line before any swipe, so asking here is free — and the
    bottom probe it may authorise is the expensive part this gate exists to avoid.
    """
    policy = ScreeningPolicy(max_commute_distance_km=40.0, business_district_inspect_list=["华夏中路"])
    page = _page_with_line(FULL_LINE)

    posting = page.extract_job_posting(
        timeout_sec=0.1,
        probe_commute_distance=False,
        is_headhunter=False,
        commute_probe_upgrade=lambda line: policy.should_probe_commute_distance(
            False, location=line
        ),
    )

    page.extract_commute_distance.assert_called_once()
    assert posting.commute_distance_km == pytest.approx(22.0)


def test_a_clean_line_buys_no_probe_even_when_asked():
    policy = ScreeningPolicy(max_commute_distance_km=40.0, business_district_inspect_list=["华夏中路"])
    page = _page_with_line("上海·徐汇区·漕河泾(近9号线桂林路地铁站)")

    posting = page.extract_job_posting(
        timeout_sec=0.1,
        probe_commute_distance=False,
        is_headhunter=False,
        commute_probe_upgrade=lambda line: policy.should_probe_commute_distance(
            False, location=line
        ),
    )

    page.extract_commute_distance.assert_not_called()
    # Fail open: an unmeasured distance stays unknown rather than becoming a rejection.
    assert posting.commute_distance_km is None


def test_the_upgrade_never_overrules_a_headhunter_or_a_yes_the_card_stage_already_gave():
    """The second look may only add a probe, never remove one, and never resurrect a
    headhunter's: the platform cannot render the widget for those at all."""
    policy = ScreeningPolicy(max_commute_distance_km=40.0, business_district_inspect_list=["华夏中路"])
    page = _page_with_line(FULL_LINE)

    posting = page.extract_job_posting(
        timeout_sec=0.1,
        probe_commute_distance=False,
        is_headhunter=True,
        commute_probe_upgrade=lambda line: policy.should_probe_commute_distance(
            True, location=line
        ),
    )
    page.extract_commute_distance.assert_not_called()
    assert posting.commute_distance_km is None

    # A card stage that already said yes keeps its probe, whatever the line says.
    page2 = _page_with_line("上海·徐汇区·漕河泾")
    page2.extract_job_posting(
        timeout_sec=0.1,
        probe_commute_distance=True,
        is_headhunter=False,
        commute_probe_upgrade=lambda line: False,
    )
    page2.extract_commute_distance.assert_called_once()


# --------------------------------------------------------------------------- #
# The same two lists, judged again on the full line (issue #333)
# --------------------------------------------------------------------------- #
def test_a_blacklisted_station_rejects_with_the_shared_reason():
    policy = ScreeningPolicy(business_district_blacklist=["华夏中路"])

    passed, reason = policy.evaluate_location_blacklist(FULL_LINE)

    assert passed is False
    # One reason string, shared with the card stage, naming the line and the operator's
    # own token — not a normalized copy of either.
    assert reason == (
        f"【商圈黑名单过滤】岗位所在区域/商圈 '{FULL_LINE}' 命中黑名单 '华夏中路'"
    )


def test_a_blacklisted_district_still_rejects_a_line_that_names_no_station():
    policy = ScreeningPolicy(business_district_blacklist=["崇明区"])

    passed, reason = policy.evaluate_location_blacklist("上海·崇明区·城桥")

    assert passed is False
    assert "崇明区" in reason


@pytest.mark.parametrize("raw", ["", "上海·徐汇区·漕河泾(近9号线桂林路地铁站)"])
def test_a_line_that_hits_nothing_is_never_rejected(raw: str):
    policy = ScreeningPolicy(business_district_blacklist=["华夏中路", "崇明区"])

    assert policy.evaluate_location_blacklist(raw) == (True, "")


def test_a_line_matches_only_as_the_platform_renders_it():
    """Matching is plain substring, so a 线路 entry must be written as the line shows it.

    A station interchange is rendered ``13/16号线``, which does not contain ``13号线``.
    Guessing at a line's shape would need a tokenizer over a string the platform already
    formats consistently, and the operator can always see the rendered line on their own
    screen — so the rule stays the same one the district list has always used.
    """
    exact = ScreeningPolicy(business_district_blacklist=["13/16号线"])
    assert exact.evaluate_location_blacklist(FULL_LINE)[0] is False

    partial = ScreeningPolicy(business_district_blacklist=["13号线"])
    assert partial.evaluate_location_blacklist(FULL_LINE)[0] is True

    # The station name is the entry an operator can always read off the screen.
    station = ScreeningPolicy(business_district_blacklist=["华夏中路"])
    assert station.evaluate_location_blacklist(FULL_LINE)[0] is False


def test_a_disabled_policy_rejects_nothing_at_either_stage():
    policy = ScreeningPolicy(
        enable_screening=False, business_district_blacklist=["华夏中路"]
    )

    assert policy.evaluate_location_blacklist(FULL_LINE) == (True, "")
    assert policy.matches_card_keywords("Agent 平台工程师", location="上海  浦东新区  张江")[0] is True
