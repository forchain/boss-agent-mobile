"""
tests.unit.test_card_time
=========================
Unit tests for the 仅沟通 card timestamp parser (issue #239).

A card stamp is the *only* thing that lets one CHECK_CHAT run stop paging through
the 仅沟通 history, so the parser is deliberately strict: anything it cannot read
is reported as unreadable, and the caller then over-scans rather than truncates.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from boss_agent.card_time import CardTimestamp, TimePrecision, parse_card_timestamp

#: Fixed run instant: 2025-09-23 15:00 in the platform's own +08:00 zone. Pinned so
#: the relative forms ("昨天", "星期二") never depend on the machine's clock or zone.
TZ = timezone(timedelta(hours=8))
NOW = datetime(2025, 9, 23, 15, 0, tzinfo=TZ)


def parsed(raw: str, now: datetime = NOW) -> CardTimestamp:
    stamp = parse_card_timestamp(raw, now=now)
    assert stamp is not None, f"expected '{raw}' to parse"
    return stamp


# ---------------------------------------------------------------------------
# Today's stamps: a bare time means today
# ---------------------------------------------------------------------------


def test_bare_time_reads_as_today_at_minute_precision():
    stamp = parsed("14:30")
    assert stamp.at == datetime(2025, 9, 23, 14, 30, tzinfo=TZ)
    assert stamp.precision is TimePrecision.MINUTE


def test_explicit_today_prefix_is_accepted():
    assert parsed("今天 09:05").at == datetime(2025, 9, 23, 9, 5, tzinfo=TZ)


def test_the_specs_own_example_forms_are_accepted():
    """#239 names 今日 and 昨日 among the relative forms, so both spellings are read."""
    assert parsed("今日 14:30").at == datetime(2025, 9, 23, 14, 30, tzinfo=TZ)
    assert parsed("昨日 10:20").at == datetime(2025, 9, 22, 10, 20, tzinfo=TZ)
    assert parsed("今日").at.date() == datetime(2025, 9, 23).date()


def test_single_digit_hour_is_accepted():
    assert parsed("9:05").at == datetime(2025, 9, 23, 9, 5, tzinfo=TZ)


def test_surrounding_whitespace_and_fullwidth_colon_are_tolerated():
    """The stamp is rendered text, so the glyph forms are accepted, not assumed."""
    assert parsed("  昨天 10:20  ").at == datetime(2025, 9, 22, 10, 20, tzinfo=TZ)
    assert parsed("昨天 10：20").at == datetime(2025, 9, 22, 10, 20, tzinfo=TZ)


def test_just_now_reads_as_the_run_instant():
    stamp = parsed("刚刚")
    assert stamp.at == NOW
    assert stamp.precision is TimePrecision.MINUTE


# ---------------------------------------------------------------------------
# Relative day stamps
# ---------------------------------------------------------------------------


def test_yesterday_keeps_its_time():
    stamp = parsed("昨天 10:20")
    assert stamp.at == datetime(2025, 9, 22, 10, 20, tzinfo=TZ)
    assert stamp.precision is TimePrecision.MINUTE


def test_yesterday_without_a_time_is_day_precise():
    stamp = parsed("昨天")
    assert stamp.at == datetime(2025, 9, 22, 0, 0, tzinfo=TZ)
    assert stamp.precision is TimePrecision.DAY


def test_the_day_before_yesterday_is_understood():
    assert parsed("前天 08:15").at == datetime(2025, 9, 21, 8, 15, tzinfo=TZ)


def test_weekday_reads_as_its_most_recent_occurrence():
    """2025-09-23 is a Tuesday, so 星期一 is yesterday and 星期二 is today."""
    assert parsed("星期一").at.date() == datetime(2025, 9, 22).date()
    assert parsed("星期二").at.date() == datetime(2025, 9, 23).date()
    assert parsed("星期日").at.date() == datetime(2025, 9, 21).date()
    assert parsed("周二").at.date() == datetime(2025, 9, 23).date()


def test_weekday_keeps_its_time():
    assert parsed("星期一 19:40").at == datetime(2025, 9, 22, 19, 40, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Absolute stamps
# ---------------------------------------------------------------------------


def test_month_day_reads_as_the_current_year():
    stamp = parsed("09-21")
    assert stamp.at == datetime(2025, 9, 21, 0, 0, tzinfo=TZ)
    assert stamp.precision is TimePrecision.DAY


def test_month_day_keeps_its_time_at_minute_precision():
    stamp = parsed("09-21 11:47")
    assert stamp.at == datetime(2025, 9, 21, 11, 47, tzinfo=TZ)
    assert stamp.precision is TimePrecision.MINUTE


def test_month_day_ahead_of_today_rolls_back_to_the_previous_year():
    """A 12-31 stamp read in early January belongs to last year, not the future."""
    january = datetime(2026, 1, 3, 9, 0, tzinfo=TZ)
    assert parsed("12-31", now=january).at == datetime(2025, 12, 31, 0, 0, tzinfo=TZ)


def test_month_day_today_is_not_rolled_back():
    """Same-day is not 'ahead of today': only a strictly later date is."""
    assert parsed("09-23").at == datetime(2025, 9, 23, 0, 0, tzinfo=TZ)


def test_feb_29_resolves_to_the_last_leap_year():
    """A leap-day stamp read in a common year still names a real date."""
    assert parsed("02-29", now=datetime(2025, 6, 1, 9, 0, tzinfo=TZ)).at == datetime(
        2024, 2, 29, 0, 0, tzinfo=TZ
    )


def test_cross_year_stamp_keeps_its_own_year():
    stamp = parsed("2023-11-04")
    assert stamp.at == datetime(2023, 11, 4, 0, 0, tzinfo=TZ)
    assert stamp.precision is TimePrecision.DAY


def test_cross_year_stamp_with_a_time_is_minute_precise():
    assert parsed("2023-11-04 08:15").at == datetime(2023, 11, 4, 8, 15, tzinfo=TZ)


def test_slash_separators_are_accepted_for_both_lengths():
    """The separator is a rendered glyph, so both spellings are read the same way."""
    assert parsed("2023/11/04").at == datetime(2023, 11, 4, 0, 0, tzinfo=TZ)
    assert parsed("09/21").at == datetime(2025, 9, 21, 0, 0, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Unreadable stamps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "25:99",
        "14:5",
        "2023-11-04-08",
        "11/04/2023",
        "昨天10:20:30",
        "3天前",
        "上个月",
        "你已投递",
        "传音控股 | 算法工程师",
        "我们感谢您的投递，但您的专业技能与我们目前的职位需求并不完全吻合。",
    ],
)
def test_unreadable_text_is_reported_as_unreadable(raw):
    assert parse_card_timestamp(raw, now=NOW) is None


def test_an_out_of_range_clock_time_is_rejected_rather_than_rolled_over():
    assert parse_card_timestamp("24:00", now=NOW) is None
    assert parse_card_timestamp("13:60", now=NOW) is None


def test_an_impossible_calendar_date_is_rejected():
    assert parse_card_timestamp("02-30", now=NOW) is None
    assert parse_card_timestamp("2023-02-30", now=NOW) is None


# ---------------------------------------------------------------------------
# Precision-aware comparison against the execution cursor
# ---------------------------------------------------------------------------


def test_a_minute_stamp_is_not_before_the_minute_it_was_rendered_in():
    """Card stamps carry no seconds, so the cursor's own minute must not truncate."""
    assert parsed("14:30").is_before(datetime(2025, 9, 23, 14, 30, 59, tzinfo=TZ)) is False
    assert parsed("14:30").is_before(datetime(2025, 9, 23, 14, 30, 0, tzinfo=TZ)) is False
    assert parsed("14:30").is_before(datetime(2025, 9, 23, 14, 31, 0, tzinfo=TZ)) is True


def test_a_day_stamp_is_not_before_a_cursor_inside_its_own_day():
    """`09-21` covers the whole day; truncating on it would lose that day's messages."""
    assert parsed("09-21").is_before(datetime(2025, 9, 21, 23, 30, tzinfo=TZ)) is False
    assert parsed("09-21").is_before(datetime(2025, 9, 21, 0, 0, 1, tzinfo=TZ)) is False


def test_a_day_stamp_is_before_a_cursor_past_its_own_day():
    cursor = datetime(2025, 9, 22, 0, 30, tzinfo=UTC)
    assert parsed("09-21").is_before(cursor) is True


def test_comparison_holds_across_zones():
    """The cursor is UTC while the card is rendered in the device's zone."""
    cursor = datetime(2025, 9, 23, 6, 30, tzinfo=UTC)  # 14:30 +08:00
    assert parsed("14:30").is_before(cursor) is False
    assert parsed("14:29").is_before(cursor) is True
