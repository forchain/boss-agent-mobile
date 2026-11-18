"""
boss_agent.core.search.use_cases.cron
=====================================
Pure Cron expression evaluation for SavedSearch schedules.

No clock, no environment and no timezone lookup lives here: callers inject the instant
to evaluate, so the schedule rules are testable without a polling loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def parse_cron_field(field_str: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field expression into a set of matching integers."""
    result: set[int] = set()
    for part in field_str.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            subparts = part.split("/", 1)
            range_part = subparts[0]
            try:
                step = int(subparts[1])
            except ValueError:
                step = 1
        else:
            range_part = part

        if range_part == "*":
            start, end = min_val, max_val
        elif "-" in range_part:
            start_str, end_str = range_part.split("-", 1)
            try:
                start, end = int(start_str), int(end_str)
            except ValueError:
                continue
        else:
            try:
                start = end = int(range_part)
            except ValueError:
                continue

        if step <= 0:
            step = 1

        for val in range(start, end + 1, step):
            if min_val <= val <= max_val:
                result.add(val)
    return result


def is_cron_match(cron_expr: str, dt: datetime) -> bool:
    """Check if the given datetime matches a standard 5-field cron expression.

    Format: minute hour day_of_month month day_of_week
    """
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False

    min_f, hour_f, dom_f, mon_f, dow_f = fields

    # Minute (0-59)
    if dt.minute not in parse_cron_field(min_f, 0, 59):
        return False

    # Hour (0-23)
    if dt.hour not in parse_cron_field(hour_f, 0, 23):
        return False

    # Month (1-12)
    if dt.month not in parse_cron_field(mon_f, 1, 12):
        return False

    # Day of week & Day of month handling
    python_weekday = dt.weekday()  # 0=Mon, 6=Sun
    cron_weekday = 0 if python_weekday == 6 else (python_weekday + 1)
    valid_dows = parse_cron_field(dow_f, 0, 7)
    if 7 in valid_dows:
        valid_dows.add(0)

    valid_doms = parse_cron_field(dom_f, 1, 31)

    dom_restricted = dom_f != "*"
    dow_restricted = dow_f != "*"

    if dom_restricted and dow_restricted:
        # If both DOM and DOW are specified, match if EITHER matches
        return dt.day in valid_doms or cron_weekday in valid_dows
    elif dom_restricted:
        return dt.day in valid_doms
    elif dow_restricted:
        return cron_weekday in valid_dows

    return True


def get_next_cron_run(cron_expr: str, after: datetime, max_days: int = 30) -> datetime | None:
    """Compute the next matching datetime for a cron expression after ``after``."""
    # Start search at the beginning of the next minute
    cur = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = cur + timedelta(days=max_days)

    while cur <= end:
        if is_cron_match(cron_expr, cur):
            return cur
        cur += timedelta(minutes=1)

    return None
