"""Array helpers shared by the metric modules.

Nothing here knows about rides, models or Reflex — see ``metrics/__init__``
for why that boundary matters.
"""

from collections.abc import Iterable

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def as_float_array(values: Iterable[float | None] | FloatArray) -> FloatArray:
    """Coerce a channel to ``float64`` with ``None`` becoming ``NaN``.

    FIT records drop fields rather than writing zeros — a lost GPS fix or a
    dead cadence magnet simply has no value on that sample. Mapping those to
    NaN keeps them out of the averages instead of dragging them to zero.
    """
    if isinstance(values, np.ndarray):
        return values.astype(np.float64, copy=False)
    return np.array(
        [np.nan if v is None else float(v) for v in values],
        dtype=np.float64,
    )


def rolling_mean(values: FloatArray, window: int) -> FloatArray:
    """Trailing rolling mean.

    Returns an array of ``len(values) - window + 1`` fully-populated means;
    the leading partial windows are dropped rather than back-filled. That is
    what the normalized-power definition asks for — a partial window is not a
    30-second average — and it is why the result is shorter than the input.
    """
    if window <= 1:
        return values.copy()
    if len(values) < window:
        return np.empty(0, dtype=np.float64)
    cumulative = np.cumsum(np.insert(values, 0, 0.0))
    return (cumulative[window:] - cumulative[:-window]) / float(window)


def centered_mean(values: FloatArray, window: int) -> FloatArray:
    """Centred moving average that keeps the array length and the endpoints.

    Windows shrink at the edges instead of being zero-padded. Zero-padding a
    barometric altitude trace invents a ramp from 0 m to the starting
    elevation at each end, which shows up as hundreds of metres of phantom
    climbing.
    """
    if window <= 1 or len(values) == 0:
        return values.copy()
    cumulative = np.cumsum(np.insert(values, 0, 0.0))
    count = len(values)
    half = window // 2
    starts = np.maximum(0, np.arange(count) - half)
    ends = np.minimum(count, np.arange(count) + half + 1)
    return (cumulative[ends] - cumulative[starts]) / (ends - starts)


def channel_stats(values: FloatArray) -> tuple[float | None, float | None, float | None]:
    """``(mean, min, max)`` ignoring NaN, or ``(None, None, None)`` if empty.

    The Karoo shows min/max for every channel (``docs/image.png``) but the FIT
    session message carries only some of them, so the rest are derived here.
    """
    if len(values) == 0:
        return None, None, None
    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        return None, None, None
    return float(np.mean(finite)), float(np.min(finite)), float(np.max(finite))


def sample_intervals(time_s: FloatArray, max_gap_s: float = 10.0) -> FloatArray:
    """Seconds represented by each sample, clipped at ``max_gap_s``.

    Recording pauses leave multi-minute holes between consecutive samples (one
    of the test files has a 1735 s gap). Weighting a sample by the full gap
    would credit a 29-minute stop to whatever wattage happened to be recorded
    just before it, so gaps are clipped to a plausible sampling interval.
    """
    if len(time_s) == 0:
        return np.empty(0, dtype=np.float64)
    deltas = np.diff(time_s, prepend=time_s[0] - 1.0)
    return np.clip(deltas, 0.0, max_gap_s)


def mean_active(values: FloatArray) -> float | None:
    """Mean of the finite, non-zero samples, or None if there are none.

    Cadence and power need different averaging rules, and the difference is
    not arbitrary: coasting is real zero-power time, so it belongs in average
    power, but it is not pedalling, so it must not drag average cadence down.
    Measured against both reference files — the Karoo's average cadence is the
    non-zero mean (83.0 and 70.4 against its 83 and 71), where a plain mean
    gives 81.1 and 65.9.

    Kept separate from :func:`channel_stats` rather than added to it as a
    flag, because dropping zeros would change what "min" means for every
    existing caller.
    """
    if len(values) == 0:
        return None
    active = values[np.isfinite(values) & (values != 0.0)]
    if len(active) == 0:
        return None
    return float(np.mean(active))


def moving_time_axis(time_s: FloatArray, max_gap_s: float | None = None) -> FloatArray:
    """Cumulative *riding* time per sample, with pauses squeezed out.

    Wall-clock elapsed time turns a stopped rider into a flat line across the
    chart — the morning reference ride spends 29 of its 172 minutes parked.
    Capping each interval at the sampling period removes exactly that: an
    interval longer than one sample means the recorder was stopped, so it
    contributes one sample's worth of time rather than its full length.

    ``max_gap_s`` defaults to the median positive interval, which is the
    device's actual sampling period. That choice reproduces the Karoo's own
    ``total_timer_time`` to within 3 s on a 44-minute ride and 2 s on a 2h12
    ride containing a 29-minute stop — see ``tests/test_fit_roundtrip.py``.

    Returns an array the same length as ``time_s``, starting at zero.
    """
    if len(time_s) == 0:
        return np.empty(0, dtype=np.float64)
    if len(time_s) == 1:
        return np.zeros(1, dtype=np.float64)

    intervals = np.diff(time_s)
    if max_gap_s is None:
        positive = intervals[intervals > 0]
        max_gap_s = float(np.median(positive)) if positive.size else 1.0

    return np.concatenate([[0.0], np.cumsum(np.clip(intervals, 0.0, max_gap_s))])


def window_indices(time_s: FloatArray, start_s: float, end_s: float) -> tuple[int, int]:
    """Half-open sample range ``[i0, i1)`` covering ``start_s`` .. ``end_s``.

    Used to cut a lap out of a ride's streams. ``searchsorted`` clamps at both
    ends by construction, which matters here: the first lap's ``start_time``
    can precede the first record — it does so by two seconds on the evening
    reference ride — and an unclamped subtraction would produce a negative
    index that silently wraps around the array.
    """
    if len(time_s) == 0:
        return 0, 0
    i0 = int(np.searchsorted(time_s, start_s, side="left"))
    i1 = int(np.searchsorted(time_s, end_s, side="right"))
    return i0, max(i0, i1)
