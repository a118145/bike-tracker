"""Elevation, gradient and climbing-rate metrics.

## Why the stored ascent is copied from the FIT file

The Karoo's ``total_ascent`` cannot be reproduced from the altitude channel
in the same file, and both reference rides show why. Summing every positive
difference in the 1 Hz barometric trace gives +149 m where the device reports
+87 m, and +1065 m where it reports +638 m — the excess is sensor noise, and
it is a different amount of noise in each file (the second-difference spread
is 0.34 m on one ride and 0.71 m on the other). No single smoothing width or
threshold reproduces both: the filter that lands exactly on the first ride's
+87 m lands at +298 m against the second ride's +638 m.

That is not a tuning failure. The device accumulates ascent live, from
unrounded barometric samples at its own internal rate, and only afterwards
writes a 0.2 m-quantised altitude channel into the records. The two are
different data sources, so no post-hoc filter over the second one can be
expected to reproduce the first.

Hence, per masterplan §3.3 — values not derived from the rider's fitness are
copied from the FIT file — ``Ride.elevation_gain_m`` stores the device's
figure. :func:`elevation_gain_loss` is still needed, for route averages and
for rides from sources that carry no session totals, and import runs it as a
cross-check that logs a warning on a gross mismatch.
"""

import numpy as np

from .arrays import FloatArray, as_float_array, centered_mean

# Hysteresis band for :func:`elevation_gain_loss`. 1.5 m is the compromise
# that lands within ~10% of the device on both reference rides; smaller
# over-reports the noisier file, larger under-reports the quieter one.
GAIN_THRESHOLD_M = 1.5

# Smoothing width, in samples, used by :func:`vam` to decide which stretches
# of a ride count as climbing. Tuned on the reference rides: 15 s reproduces
# the Karoo's own VAM to within 1 m/h.
VAM_SMOOTH_WINDOW = 15


def elevation_gain_loss(
    altitude_m: FloatArray,
    threshold_m: float = GAIN_THRESHOLD_M,
) -> tuple[float, float]:
    """Total ascent and descent in metres, as a positive pair.

    Uses hysteresis rather than a plain sum of positive differences: a climb
    is committed only once the trace has turned back down by ``threshold_m``
    from its high point, so barometric jitter around a constant altitude
    contributes nothing while a genuine 30 m riser is counted in full.

    Read the module docstring before tuning ``threshold_m`` against a device's
    reported total — the two numbers come from different sources.
    """
    samples = as_float_array(altitude_m)
    samples = samples[np.isfinite(samples)]
    if len(samples) < 2:
        return 0.0, 0.0

    gain = loss = 0.0
    anchor = extreme = float(samples[0])
    direction = 0  # 0 undecided, +1 climbing, -1 descending

    for value in samples[1:]:
        current = float(value)
        if direction == 0:
            if current - anchor > threshold_m:
                direction, extreme = 1, current
            elif anchor - current > threshold_m:
                direction, extreme = -1, current
        elif direction == 1:
            if current > extreme:
                extreme = current
            elif extreme - current > threshold_m:
                gain += extreme - anchor
                anchor, extreme, direction = extreme, current, -1
        else:
            if current < extreme:
                extreme = current
            elif current - extreme > threshold_m:
                loss += anchor - extreme
                anchor, extreme, direction = extreme, current, 1

    # Commit the leg still in progress when the ride ended.
    if direction == 1:
        gain += extreme - anchor
    elif direction == -1:
        loss += anchor - extreme

    return gain, loss


def vam(
    altitude_m: FloatArray,
    time_s: FloatArray,
    smooth_window: int = VAM_SMOOTH_WINDOW,
    threshold_m: float = GAIN_THRESHOLD_M,
) -> float:
    """Average climbing rate in metres per hour, over climbing time only.

    "Velocità Ascensionale Media" measures how fast the rider goes *up*, so
    flat and descending time is excluded — averaging over the whole ride would
    reduce to ascent divided by duration, which says more about the route than
    about the rider.

    The two halves of the ratio come from different filters, and deliberately
    so. The **ascent** is :func:`elevation_gain_loss`, so VAM stays consistent
    with the climbing total shown next to it. The **climbing time** is the
    span where a smoothed trace is rising, because the hysteresis algorithm
    commits a climb in one lump at its summit and so cannot say how long that
    climb took. Smoothing is what stops 1 Hz barometric noise marking almost
    every sample as ascending.

    Checked against the reference ride: this returns 339 m/h where the Karoo
    reports 340.
    """
    altitudes = as_float_array(altitude_m)
    times = as_float_array(time_s)
    count = min(len(altitudes), len(times))
    if count < 2:
        return 0.0

    valid = np.isfinite(altitudes[:count]) & np.isfinite(times[:count])
    altitudes, times = altitudes[:count][valid], times[:count][valid]
    if len(altitudes) < 2:
        return 0.0

    gain, _ = elevation_gain_loss(altitudes, threshold_m)
    if gain <= 0:
        return 0.0

    smoothed = centered_mean(altitudes, smooth_window)
    ascending = (np.diff(smoothed) > 0) & (np.diff(times) > 0)
    climbing_time = float(np.sum(np.diff(times)[ascending]))
    if climbing_time <= 0:
        return 0.0
    return gain / climbing_time * 3600.0


def grade(altitude_m: FloatArray, distance_m: FloatArray, smooth_window: int = 10) -> FloatArray:
    """Gradient in percent per sample, as ``rise / run x 100``.

    Both channels are smoothed first: at 1 Hz a rider covers a handful of
    metres per sample, so an unsmoothed 0.2 m altitude quantum alone is a
    several-percent gradient. Samples where the bike did not move produce NaN
    rather than a division blow-up.
    """
    altitudes = as_float_array(altitude_m)
    distances = as_float_array(distance_m)
    count = min(len(altitudes), len(distances))
    if count < 2:
        return np.empty(0, dtype=np.float64)

    smoothed_alt = centered_mean(altitudes[:count], smooth_window)
    rise = np.diff(smoothed_alt)
    run = np.diff(distances[:count])

    with np.errstate(divide="ignore", invalid="ignore"):
        percent = np.where(run > 0.1, rise / run * 100.0, np.nan)
    return np.asarray(percent, dtype=np.float64)
