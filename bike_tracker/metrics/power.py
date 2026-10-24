"""Power-derived training metrics.

Every function is pure: arrays and scalars in, scalars out. Verified against
the values the Karoo itself wrote into the two FIT files in ``docs/`` — see
``tests/test_fit_roundtrip.py``.
"""

from dataclasses import dataclass

import numpy as np

from .arrays import FloatArray, as_float_array, rolling_mean, sample_intervals

# Coggan's normalized-power definition: a 30-second rolling average, raised to
# the fourth power, averaged, then the fourth root.
NP_WINDOW_S = 30.0

# Gross mechanical efficiency used to turn mechanical work into food energy.
# 22% reproduces the Karoo's own pairing exactly on the reference ride
# (392 kJ of work reported as 426 kcal).
GROSS_EFFICIENCY = 0.22
KJ_PER_KCAL = 4.184


def normalized_power(power: FloatArray, dt: float = 1.0, window_s: float = NP_WINDOW_S) -> float:
    """Normalized power in watts.

    Args:
        power: Power samples in watts on a uniform ``dt`` grid. NaN and
            negative values are treated as zero — coasting is real zero-power
            time and must stay in the average, or every descent inflates NP.
        dt: Seconds between samples.
        window_s: Rolling-average width; 30 s is the definition.

    Returns:
        Normalized power, or 0.0 when the ride is shorter than one window.
    """
    samples = np.nan_to_num(as_float_array(power), nan=0.0, posinf=0.0, neginf=0.0)
    samples = np.clip(samples, 0.0, None)
    if len(samples) == 0 or dt <= 0:
        return 0.0

    window = max(1, round(window_s / dt))
    averaged = rolling_mean(samples, window)
    if len(averaged) == 0:
        # Too short for a full window — the fourth-power weighting is
        # meaningless here, so fall back to the plain average.
        return float(np.mean(samples))
    return float(np.mean(averaged**4) ** 0.25)


# FTP is 95% of the best 20-minute average power (masterplan §2.4.2). The
# constant lives here rather than beside the FitnessEval model because it is
# domain arithmetic, and `metrics/` is the layer that may be tested without an
# app; `models/fitness.py` re-exports it for the existing public name.
FTP_FROM_20MIN_FACTOR = 0.95


def ftp_from_20min(avg_power_w: float) -> float:
    """FTP implied by the best 20-minute average power.

    The conventional 95% haircut. Applied here rather than at the call site so
    the one number that turns a test effort into an FTP is written down once.
    """
    return max(0.0, avg_power_w) * FTP_FROM_20MIN_FACTOR


def ftp_from_window(power: FloatArray) -> float | None:
    """FTP implied by a window's average power — the evaluation dialog's rule.

    A missing sample counts as zero watts, as a dropout or coast does on the
    head unit, so a gappy window cannot inflate the estimate. ``None`` when
    there are fewer than two samples. The ride page's FTP dialog and the REST
    API's window cutter both call this, so they quote the same estimate.
    """
    samples = as_float_array(power)
    if samples.size < 2:
        return None
    return ftp_from_20min(float(np.mean(np.nan_to_num(samples, nan=0.0))))


def intensity_factor(np_w: float, ftp_w: float) -> float:
    """Normalized power as a fraction of FTP."""
    if ftp_w <= 0:
        return 0.0
    return np_w / ftp_w


def tss(np_w: float, if_: float, duration_s: float) -> float:
    """Training Stress Score.

    The canonical form is ``(seconds x NP x IF) / (FTP x 3600) x 100``. FTP is
    implied by the pair given — ``FTP = NP / IF`` — so it does not need to be
    passed separately, and callers cannot accidentally combine an IF computed
    against one FTP with a different FTP here.

    ``duration_s`` is moving time, not elapsed time: a ride's stress does not
    accumulate while it is parked outside a bakery.
    """
    if np_w <= 0 or if_ <= 0 or duration_s <= 0:
        return 0.0
    ftp_w = np_w / if_
    return duration_s * np_w * if_ / (ftp_w * 3600.0) * 100.0


def hr_tss(hr: FloatArray, lthr_bpm: float, duration_s: float) -> float:
    """Heart-rate-based training load, for rides with no power meter.

    Scales quadratically with the ratio of average heart rate to lactate
    threshold heart rate, mirroring the shape of the power-based score so the
    two land on a comparable axis. They are still not interchangeable, which
    is why ``Ride.load_source`` records which one produced a given number
    (brief §5.1).
    """
    samples = as_float_array(hr)
    finite = samples[np.isfinite(samples)]
    if len(finite) == 0 or lthr_bpm <= 0 or duration_s <= 0:
        return 0.0
    ratio = float(np.mean(finite)) / lthr_bpm
    return duration_s / 3600.0 * ratio**2 * 100.0


def decoupling(power: FloatArray, hr: FloatArray) -> float | None:
    """Aerobic decoupling (Pw:HR) between the two halves of a ride, in percent.

    Efficiency factor is watts per beat. If it falls in the second half the
    result is positive: the same power cost more heartbeats, which is what
    aerobic decoupling means. Above roughly 5% the ride was beyond the
    rider's current aerobic durability.

    Returns None when either channel is too sparse to halve meaningfully.
    """
    power_samples = as_float_array(power)
    hr_samples = as_float_array(hr)
    count = min(len(power_samples), len(hr_samples))
    if count < 4:
        return None

    power_samples = power_samples[:count]
    hr_samples = hr_samples[:count]
    midpoint = count // 2

    def efficiency(start: int, stop: int) -> float | None:
        valid = np.isfinite(power_samples[start:stop]) & np.isfinite(hr_samples[start:stop])
        if not valid.any():
            return None
        mean_hr = float(np.mean(hr_samples[start:stop][valid]))
        if mean_hr <= 0:
            return None
        return float(np.mean(power_samples[start:stop][valid])) / mean_hr

    first = efficiency(0, midpoint)
    second = efficiency(midpoint, count)
    if first is None or second is None or first <= 0:
        return None
    return (first - second) / first * 100.0


def total_work_kj(power: FloatArray, time_s: FloatArray) -> float:
    """Mechanical work in kilojoules, integrating power over real sample times.

    Uses the actual timestamps rather than a sample count so recording pauses
    do not get charged at the wattage recorded just before them.
    """
    power_samples = np.nan_to_num(as_float_array(power), nan=0.0)
    times = as_float_array(time_s)
    if len(power_samples) == 0 or len(times) != len(power_samples):
        return 0.0
    return float(np.sum(np.clip(power_samples, 0.0, None) * sample_intervals(times)) / 1000.0)


def calories_kcal(work_kj: float, efficiency: float = GROSS_EFFICIENCY) -> float:
    """Food energy for a given amount of mechanical work.

    Cycling converts roughly 22% of metabolic energy into work at the pedals,
    so the metabolic cost is the work divided by that efficiency.
    """
    if work_kj <= 0 or efficiency <= 0:
        return 0.0
    return work_kj / KJ_PER_KCAL / efficiency


# The mean-maximal power curve shown on the ride-detail card, in seconds.
# Short windows describe sprinting, long ones describe endurance; the shape
# between them is what tells a sprint apart from a steady climb when both
# average the same wattage.
POWER_CURVE_WINDOWS_S: tuple[int, ...] = (5, 15, 30, 60, 300, 600, 1200, 3600)


def best_average_power(power: FloatArray, window_s: float, dt: float = 1.0) -> float | None:
    """Highest average power sustained over any ``window_s`` of the samples.

    The whole array is searched — unlike :func:`best_20min_window`, which looks
    only near a user's guess because it is estimating FTP from a deliberate
    test effort. Here every window is a candidate.

    Args:
        power: Power samples in watts on a uniform ``dt`` grid. NaN and
            negative values count as zero, as everywhere else: coasting is
            real zero-power time.
        window_s: Width of the averaging window.
        dt: Seconds between samples.

    Returns:
        The best average, or **None when the ride is shorter than the window**
        — there is no value to report, which the UI renders as an em dash
        rather than inventing a number from a partial window.
    """
    samples = np.clip(np.nan_to_num(as_float_array(power), nan=0.0), 0.0, None)
    if samples.size == 0 or dt <= 0 or window_s <= 0:
        return None

    window = max(1, round(window_s / dt))
    if samples.size < window:
        return None

    averages = rolling_mean(samples, window)
    return float(np.max(averages)) if averages.size else None


def power_curve(
    power: FloatArray,
    dt: float = 1.0,
    windows_s: tuple[int, ...] = POWER_CURVE_WINDOWS_S,
) -> dict[int, float | None]:
    """The best average for each window, keyed by window length in seconds."""
    return {w: best_average_power(power, w, dt=dt) for w in windows_s}


@dataclass(frozen=True)
class BestWindow:
    """The best-averaging window found by :func:`best_20min_window`."""

    avg_power_w: float
    start_index: int
    end_index: int  # exclusive


def best_20min_window(
    power: FloatArray,
    guess_frac: float = 0.5,
    dt: float = 1.0,
    window_s: float = 1200.0,
    search_s: float = 1200.0,
) -> BestWindow | None:
    """Highest-averaging window of ``window_s`` near a user's guess.

    Masterplan §2.4.2 "best fit": the user points roughly at where the effort
    was with a single slider, and the window then slides from ``guess -
    20 min`` to ``guess + 20 min`` looking for the real maximum. Searching the
    whole ride instead would happily return a sprint on the way home.

    Args:
        power: Power samples in watts on a uniform ``dt`` grid.
        guess_frac: Where the user pointed, 0..1 of the ride duration.
        dt: Seconds between samples.
        window_s: Window width; 20 min is the FTP convention.
        search_s: How far either side of the guess the window start may move.

    Returns:
        The best window, or None if the ride is shorter than ``window_s``.
    """
    samples = np.nan_to_num(as_float_array(power), nan=0.0)
    samples = np.clip(samples, 0.0, None)
    window = max(1, round(window_s / dt))
    if len(samples) < window or dt <= 0:
        return None

    averages = rolling_mean(samples, window)  # averages[i] covers [i, i+window)
    last_start = len(averages) - 1

    guess_index = round(float(np.clip(guess_frac, 0.0, 1.0)) * (len(samples) - 1))
    span = max(1, round(search_s / dt))
    low = int(np.clip(guess_index - span, 0, last_start))
    high = int(np.clip(guess_index + span, 0, last_start))

    offset = int(np.argmax(averages[low : high + 1]))
    start = low + offset
    return BestWindow(
        avg_power_w=float(averages[start]),
        start_index=start,
        end_index=start + window,
    )


def baseline(power: FloatArray, hr: FloatArray) -> float | None:
    """Aerobic baseline in watts per beat over the supplied window.

    Masterplan §2.4.2 defines the baseline test as holding a constant wattage
    below FTP while recording heart rate; the result is the ratio of the two
    averages. Slice the arrays to the evaluated window before calling.
    """
    power_samples = as_float_array(power)
    hr_samples = as_float_array(hr)
    count = min(len(power_samples), len(hr_samples))
    if count == 0:
        return None

    valid = np.isfinite(power_samples[:count]) & np.isfinite(hr_samples[:count])
    if not valid.any():
        return None
    mean_hr = float(np.mean(hr_samples[:count][valid]))
    if mean_hr <= 0:
        return None
    return float(np.mean(power_samples[:count][valid])) / mean_hr
