"""
src/boss_agent/card_time.py
===========================
Parsing the timestamp a 仅沟通 card renders next to its last message (issue #239).

A CHECK_CHAT run needs one fact before it can stop paging: *is this card older
than the last run?* The platform renders that answer as a localised string
(``14:30``, ``昨天 10:20``, ``09-21``, ``2023-11-04``), so the string has to be
turned back into an instant — and, just as importantly, into an honest statement
of *how precise* that instant is.

That second part is why every result carries a `TimePrecision` rather than a bare
`datetime`. ``09-21`` covers a whole day: read as ``2025-09-21T00:00`` it would
look older than a cursor set at ``09-21 15:00``, and the scan would drop that
day's messages for good. `CardTimestamp.is_before` therefore compares at the
stamp's own resolution, so a coarse stamp only ever counts as older once the
cursor is past the whole period it covers.

The parser is deliberately strict about what it accepts: unreadable text returns
``None`` and the caller responds by scanning the card rather than truncating on
it. A missing optimisation is recoverable; a wrongly truncated history is not.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum

#: Weekday glyphs mapped to `datetime.weekday()` (Monday = 0). Both spellings of
#: Sunday are listed because the platform is inconsistent about it.
_WEEKDAY_TARGETS: dict[str, int] = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}

#: Days back from the run instant for each relative day word, keyed by every spelling
#: the platform uses for it (spec #239 names 今日 and 昨日, the UI renders both).
_RELATIVE_DAY_OFFSETS: dict[str, int] = {
    "今天": 0,
    "今日": 0,
    "昨天": 1,
    "昨日": 1,
    "前天": 2,
}

#: `HH:MM`, capturing the clock so callers can tell a timeless stamp from a timed one.
_CLOCK = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"

#: The alternation is built from the offsets map so a word can never be listed as
#: understood while the pattern that would match it is missing.
_RELATIVE_DAY_RE = re.compile(
    rf"^(?P<day>{'|'.join(_RELATIVE_DAY_OFFSETS)})(?P<clock>{_CLOCK})?$"
)
_WEEKDAY_RE = re.compile(rf"^(?:星期|周)(?P<weekday>[一二三四五六日天])(?P<clock>{_CLOCK})?$")
_FULL_DATE_RE = re.compile(
    rf"^(?P<year>\d{{4}})-(?P<month>\d{{1,2}})-(?P<day>\d{{1,2}})(?P<clock>{_CLOCK})?$"
)
_SHORT_DATE_RE = re.compile(
    rf"^(?P<month>\d{{1,2}})-(?P<day>\d{{1,2}})(?P<clock>{_CLOCK})?$"
)
_CLOCK_RE = re.compile(rf"^{_CLOCK}$")


class TimePrecision(StrEnum):
    """How much of the period a card stamp actually pins down."""

    #: An explicit clock reading, e.g. ``14:30`` or ``昨天 10:20``.
    MINUTE = "minute"
    #: A whole day, e.g. ``09-21``: the message fell somewhere inside it.
    DAY = "day"


@dataclass(frozen=True)
class CardTimestamp:
    """A card's rendered last-message time, with the resolution it was rendered at."""

    at: datetime
    precision: TimePrecision

    def is_before(self, moment: datetime) -> bool:
        """Whether this stamp is *certainly* older than `moment`.

        The comparison is made at the stamp's own resolution. A ``14:30`` stamp is
        not older than a cursor at ``14:30:59`` -- the card simply cannot say
        whether the message landed at :30 or :59 -- and a ``09-21`` stamp is not
        older than any cursor still inside 09-21. Treating a coarse stamp as an
        exact instant would truncate the boundary period and drop the messages
        inside it, which no later run would ever revisit.

        A naive ``moment`` is read as UTC rather than raising: this runs inside a
        long unattended device loop, where an unexpectedly naive cursor must cost a
        redundant scan, never the run.
        """
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if self.precision is TimePrecision.DAY:
            return self.at.date() < moment.astimezone(self.at.tzinfo).date()
        # The stamp has no seconds, so the cursor is floored to its own minute before
        # comparing: otherwise a cursor at 14:30:59 would read the 14:30 card on the
        # boundary as older and truncate a minute it has not actually covered.
        return self.at < moment.replace(second=0, microsecond=0)


def parse_card_timestamp(raw: str, *, now: datetime | None = None) -> CardTimestamp | None:
    """Parse one card's rendered timestamp, or return None when it is not one.

    `now` anchors the relative forms (``刚刚``, ``昨天``, ``星期二``) and supplies the
    zone the stamp is expressed in; it defaults to the current local time and is
    injected by tests so the relative cases never depend on the wall clock.

    Returns ``None`` for anything this does not recognise -- a message body, a
    recruiter name, a word the platform added since -- so the caller can fall back
    to scanning rather than guessing.
    """
    moment = now or datetime.now().astimezone()
    text = _normalize(raw)
    if not text:
        return None

    if text == "刚刚":
        return CardTimestamp(at=moment, precision=TimePrecision.MINUTE)

    # Each recognised shape resolves to the *day* the card names; `_stamp_on` then
    # applies the clock, if any, and reports the day as unknown when it is not real.
    day: date | None

    match = _RELATIVE_DAY_RE.match(text)
    if match:
        day = moment.date() - timedelta(days=_RELATIVE_DAY_OFFSETS[match.group("day")])
        return _stamp_on(day, match, zone=moment.tzinfo)

    match = _WEEKDAY_RE.match(text)
    if match:
        target = _WEEKDAY_TARGETS[match.group("weekday")]
        day = moment.date() - timedelta(days=(moment.weekday() - target) % 7)
        return _stamp_on(day, match, zone=moment.tzinfo)

    match = _FULL_DATE_RE.match(text)
    if match:
        day = _safe_date(
            int(match.group("year")), int(match.group("month")), int(match.group("day"))
        )
        return _stamp_on(day, match, zone=moment.tzinfo)

    match = _SHORT_DATE_RE.match(text)
    if match:
        day = _resolve_year(int(match.group("month")), int(match.group("day")), moment.date())
        return _stamp_on(day, match, zone=moment.tzinfo)

    match = _CLOCK_RE.match(text)
    if match:
        return _stamp_on(moment.date(), match, zone=moment.tzinfo)

    return None


def _normalize(raw: str) -> str:
    """Collapse whitespace and fold the glyph variants onto their ASCII forms.

    A card stamp is rendered text, so ``昨天　10:20`` (ideographic space) and
    ``10：20`` (fullwidth colon) are the same stamp; and because a build may draw
    either separator, both date separators are folded onto one before matching.
    """
    return "".join((raw or "").split()).replace("：", ":").replace("/", "-")


def _stamp_on(
    day: date | None, match: re.Match[str], *, zone: tzinfo | None
) -> CardTimestamp | None:
    """Build the stamp for a matched day, minute-precise whenever a clock is present."""
    if day is None:
        return None
    raw_hour = match.group("hour")
    if raw_hour is None:
        return CardTimestamp(
            at=datetime(day.year, day.month, day.day, tzinfo=zone),
            precision=TimePrecision.DAY,
        )
    hour, minute = int(raw_hour), int(match.group("minute"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return CardTimestamp(
        at=datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone),
        precision=TimePrecision.MINUTE,
    )


def _safe_date(year: int, month: int, day: int) -> date | None:
    """Build a date, reporting an impossible month/day as unknown rather than raising."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_year(month: int, day: int, today: date) -> date | None:
    """Resolve a year-less ``MM-DD`` against the run date.

    A stamp without a year is normally this year. It is last year when this year's
    date would still be in the future -- a ``12-31`` card read on January 3rd --
    and also when this year has no such date at all, which is what ``02-29`` needs
    in a common year. Both years failing means the stamp names no real date.
    """
    candidate = _safe_date(today.year, month, day)
    if candidate is not None and candidate <= today:
        return candidate
    return _safe_date(today.year - 1, month, day)


__all__ = ["CardTimestamp", "TimePrecision", "parse_card_timestamp"]
