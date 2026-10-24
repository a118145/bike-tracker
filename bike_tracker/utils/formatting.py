"""Display formatting.

Server-side helpers for values that are formatted *before* they reach a state
var. Anything formatted here becomes a plain string in state, which keeps the
frontend free of formatting logic.

Note: these operate on Python values, not Reflex Vars. To format inside a
component, use a computed var on the state instead.
"""

from datetime import date, datetime
from decimal import Decimal

from .local_time import to_local

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_date(value: date | datetime | None, fmt: str = "%Y-%m-%d") -> str:
    """Format a date, with empty string for None (never the text 'None').

    A ``datetime`` is a stored timestamp — naive UTC — and is shown on the
    local calendar (``utils/local_time``). A plain ``date`` is already one.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = to_local(value)
    return value.strftime(fmt)


def format_datetime(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """A stored (naive UTC) timestamp in local time."""
    if value is None:
        return ""
    return to_local(value).strftime(fmt)


def format_currency(
    value: Decimal | float | None,
    symbol: str = "€",
    decimals: int = 2,
) -> str:
    """Format a monetary amount, e.g. ``1234.5 -> '1,234.50 €'``."""
    if value is None:
        return ""
    # U+00A0 keeps the amount and its symbol on one line.
    return f"{value:,.{decimals}f}\u00a0{symbol}"


def truncate(text: str, length: int = 60, suffix: str = "…") -> str:
    """Shorten text to ``length`` characters, including the suffix."""
    if len(text) <= length:
        return text
    return text[: max(0, length - len(suffix))].rstrip() + suffix


def humanize_bytes(size: int | float, decimals: int = 1) -> str:
    """Render a byte count as ``'1.5 MB'``."""
    value = float(size)
    for unit in _UNITS:
        if abs(value) < 1024.0 or unit == _UNITS[-1]:
            precision = 0 if unit == "B" else decimals
            return f"{value:.{precision}f} {unit}"
        value /= 1024.0
    return f"{value:.{decimals}f} {_UNITS[-1]}"


# ---------------------------------------------------------------------------
# Cycling display values
# ---------------------------------------------------------------------------
#
# All of these return the number *without* its unit, because the stat cards in
# `components/ride_stats.py` render the unit as separately styled small text
# (see docs/image.png). The em dash is the shared "no data" marker: rides
# without a power meter, cadence sensor or GPS fix are normal, not errors.

EMPTY = "—"


def format_distance_km(metres: float | None, decimals: int = 1) -> str:
    """Metres to kilometres, e.g. ``19185.02 -> '19.2'``."""
    if metres is None:
        return EMPTY
    return f"{metres / 1000.0:.{decimals}f}"


def format_duration(seconds: float | None) -> str:
    """Seconds as ``H:MM:SS``, or ``MM:SS`` under an hour.

    Ride times are read as durations, not clock times, so hours are not
    zero-padded — "44:24" and "2:52:09" both match how the head unit shows it.
    """
    if seconds is None or seconds < 0:
        return EMPTY
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_speed_kmh(metres_per_second: float | None, decimals: int = 1) -> str:
    if metres_per_second is None:
        return EMPTY
    return f"{metres_per_second * 3.6:.{decimals}f}"


def format_number(value: float | int | None, decimals: int = 0) -> str:
    if value is None:
        return EMPTY
    return f"{value:.{decimals}f}"


def format_balance(left_pct: float | None) -> str:
    """Power balance as ``'52/48'`` (left/right), per docs/image.png.

    Only the dual-sided Assioma reports this; the single-sided SRAM crank
    returns None and the card shows an em dash.
    """
    if left_pct is None:
        return EMPTY
    left = round(left_pct)
    return f"{left}/{100 - left}"
