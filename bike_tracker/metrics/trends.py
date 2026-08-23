"""Best efforts aggregated over consecutive date windows (masterplan §2.5).

The Fitness view overlays one mean-maximal power curve per 30- or 90-day
window, so a season's worth of form is readable at every duration at once:
a sprint that improves while the hour power falls says something a single
curve cannot.

Each ride already carries its own curve in the eight ``best_power_*_w``
columns, computed at import. So this is a max-reduce over stored scalars, not
a pass over any stream — which is what keeps the page a database read.

Pure, like everything here: dates and dicts in, dataclasses out. No models,
no Reflex.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# One entry per ride: when it happened, and its best average for each window
# length in seconds. A duration the ride was too short for is None and must
# stay None rather than becoming a zero (AGENTS.md rule 13).
RideEfforts = tuple[datetime, dict[int, float | None]]


@dataclass(frozen=True)
class EffortWindow:
    """The best effort at each duration across one span of dates."""

    label: str
    start: datetime
    end: datetime  # exclusive
    ride_count: int = 0
    # duration_s -> best watts anyone managed in this span, None if nobody did.
    best: dict[int, float | None] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """No rides at all — drawn as nothing rather than as a flat zero line."""
        return self.ride_count == 0


def best_effort_windows(
    entries: Sequence[RideEfforts],
    *,
    window_days: int,
    now: datetime,
    max_windows: int = 4,
    durations_s: Sequence[int] = (),
) -> list[EffortWindow]:
    """Bucket rides into consecutive windows ending at ``now``.

    Windows are counted **backwards from now**, so the first one is always
    "the last 30 days" and the comparison the page exists to make — am I
    stronger than I was a month ago — lines up without the user doing date
    arithmetic. Anchoring them to calendar months instead would make the most
    recent window a partial one of varying length, which is not comparable
    with the others.

    Args:
        entries: One ``(ride_start, {duration_s: watts | None})`` per ride, in
            any order.
        window_days: Span of one window; 30 or 90 per the masterplan.
        now: Right edge of the most recent window.
        max_windows: How many windows back to build.
        durations_s: Which durations to report. Defaults to every duration
            mentioned by any entry, sorted.

    Returns:
        Windows **newest first**, including empty ones — a span with no riding
        is itself information, and dropping it would silently compress the
        time axis.
    """
    if window_days <= 0 or max_windows <= 0:
        return []

    keys = sorted(durations_s) if durations_s else _all_durations(entries)
    span = timedelta(days=window_days)

    windows: list[EffortWindow] = []
    for index in range(max_windows):
        end = now - span * index
        start = end - span
        # Half-open [start, end): a ride exactly on a boundary belongs to the
        # newer window, so no ride is counted twice or dropped between two.
        in_window = [efforts for when, efforts in entries if start <= when < end]

        windows.append(
            EffortWindow(
                label=_label(index, window_days),
                start=start,
                end=end,
                ride_count=len(in_window),
                best={key: _best(in_window, key) for key in keys},
            )
        )
    return windows


def _all_durations(entries: Sequence[RideEfforts]) -> list[int]:
    seen: set[int] = set()
    for _when, efforts in entries:
        seen.update(efforts)
    return sorted(seen)


def _best(rows: Sequence[dict[int, float | None]], duration_s: int) -> float | None:
    """Highest value anyone reached at this duration, or None if nobody did.

    None where no ride in the window was long enough — a 20-minute best cannot
    be synthesised from a 10-minute ride, and reporting 0 W would draw a curve
    that collapses to the floor exactly where the data runs out.
    """
    values = [row[duration_s] for row in rows if row.get(duration_s) is not None]
    return max(v for v in values if v is not None) if values else None


def _label(index: int, window_days: int) -> str:
    if index == 0:
        return f"Last {window_days} d"
    return f"{index * window_days}-{(index + 1) * window_days} d ago"
