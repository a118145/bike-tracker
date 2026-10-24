"""Local time: what a stored timestamp means on the rider's calendar.

Every timestamp in this schema is naive UTC (SQLite drops tzinfo on write; see
``models/hammerhead.py``). That is right for storage and wrong for anything a
person reads or anything keyed by the calendar: a 07:30 commute is 05:30 UTC,
and a ride just after midnight would land on the previous day — and in the
previous training week.

``APP_TIMEZONE`` names the zone; it defaults to Europe/Berlin because that is
where the rider is. The UI's date formatting and the REST API both go through
:func:`to_local`, so a page and the MCP layer cannot disagree about which day
a ride happened on.
"""

import os
from datetime import UTC, date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Berlin"


@lru_cache(maxsize=8)
def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A typo in the environment must not take every page down; the default
        # is the zone this app was built for.
        return ZoneInfo(DEFAULT_TIMEZONE)


def app_timezone() -> ZoneInfo:
    """The configured zone, read per call so a test can set the environment."""
    return _zone(os.environ.get("APP_TIMEZONE", "").strip() or DEFAULT_TIMEZONE)


def to_local(value: datetime) -> datetime:
    """A stored timestamp as an aware local datetime. Naive means UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(app_timezone())


def local_date(value: datetime) -> date:
    """The calendar day a stored timestamp falls on, locally."""
    return to_local(value).date()
