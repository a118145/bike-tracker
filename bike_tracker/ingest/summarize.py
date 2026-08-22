"""Turning a window of streams into the numbers a Ride or a Lap stores.

One function serves both. A lap is a ride in miniature, so computing its
metrics differently would be how the two quietly drift apart — and the FIT
session and lap messages happen to use the same key names for almost
everything, which is what makes a single implementation possible.

The rule is masterplan §3.3: **copy what the device wrote, compute what it did
not**. Anything computed uses the device's own definitions (see
:func:`_average_cadence` and :func:`_average_speed`), so the two agree wherever
they overlap and a value does not change meaning depending on which path
produced it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .. import metrics
from ..metrics import FloatArray
from ..models import LOAD_SOURCE_HR, LOAD_SOURCE_POWER, MetricFields
from ..utils.logging import get_logger
from .fit import ParsedRide

log = get_logger(__name__)

# Relative disagreement between our maths and the device's, beyond which import
# raises a user-visible warning.
METRIC_TOLERANCE = 0.05
# Elevation gets its own, far looser bound: the device's ascent comes from a
# different data source altogether (see ``metrics/elevation.py``).
ELEVATION_TOLERANCE = 0.40

_EMPTY = np.empty(0, dtype=np.float64)


@dataclass
class ChannelWindow:
    """Aligned per-sample channels over some slice of a ride.

    ``time_s`` is seconds since the ride's first record, so a lap's window is
    just a pair of indices into the same arrays rather than a separate copy.
    """

    time_s: FloatArray = field(default_factory=lambda: _EMPTY)
    channels: dict[str, FloatArray] = field(default_factory=dict)

    @classmethod
    def from_parsed(cls, parsed: ParsedRide) -> "ChannelWindow":
        return cls(
            time_s=np.asarray(parsed.time_offsets_s(), dtype=np.float64),
            channels={
                name: metrics.as_float_array(values) for name, values in parsed.channels.items()
            },
        )

    def slice(self, i0: int, i1: int) -> "ChannelWindow":
        return ChannelWindow(
            time_s=self.time_s[i0:i1],
            channels={name: values[i0:i1] for name, values in self.channels.items()},
        )

    def get(self, name: str) -> FloatArray:
        """A channel, or an empty array when the ride never recorded it."""
        return self.channels.get(name, _EMPTY)

    @property
    def span_s(self) -> float:
        return float(self.time_s[-1] - self.time_s[0]) if self.time_s.size >= 2 else 0.0

    @property
    def sample_interval_s(self) -> float:
        """Typical seconds between samples, for the rolling-window widths."""
        if self.time_s.size < 2:
            return 1.0
        intervals = np.diff(self.time_s)
        positive = intervals[intervals > 0]
        return float(np.median(positive)) if positive.size else 1.0


def summarize(
    window: ChannelWindow,
    *,
    fit: Mapping[str, Any] | None = None,
    ftp_w: int | None = None,
    lthr_bpm: int | None = None,
) -> MetricFields:
    """Every metric for one window, as a populated :class:`MetricFields`.

    Returns a model instance rather than a dict on purpose. SQLModel silently
    ignores unknown keyword arguments on a table class — ``Ride(avg_powr_w=9)``
    sets nothing and raises nothing — so a typo among forty keys flowing
    through ``**values`` into two different tables would be invisible. A model
    makes the same typo an ``AttributeError`` at the point it is written.

    Args:
        window: The samples to summarize. Already sliced for a lap.
        fit: The device's own summary for this window — a FIT ``session`` or
            ``lap`` message. ``None`` means compute everything.
        ftp_w: FTP as of this ride, for intensity factor and TSS.
        lthr_bpm: Lactate threshold heart rate, for the no-power fallback.
    """
    fit = fit or {}

    power = window.get("power_w")
    hr = window.get("hr_bpm")
    cadence = window.get("cadence_rpm")
    speed = window.get("speed_ms")
    altitude = window.get("altitude_m")
    distance = window.get("distance_m")
    temperature = window.get("temperature_c")
    balance = window.get("left_balance_pct")

    elapsed_s = _float_or(fit.get("total_elapsed_time"), window.span_s)
    moving_s = _float_or(fit.get("total_timer_time"), elapsed_s)

    has_power = bool(power.size) and bool(np.isfinite(power).any()) and float(np.nanmax(power)) > 0

    # Normalized power is always computed, never copied: the morning reference
    # ride's lap message has no `normalized_power` field at all.
    np_w = metrics.normalized_power(power, dt=window.sample_interval_s) if has_power else 0.0
    if_ = metrics.intensity_factor(np_w, ftp_w) if ftp_w else 0.0
    load = metrics.tss(np_w, if_, moving_s) if has_power and if_ else None
    if load is None and lthr_bpm:
        load = metrics.hr_tss(hr, lthr_bpm, moving_s)

    work_kj = metrics.total_work_kj(power, window.time_s) if has_power else None

    # Gradient: prefer the device's own channel, which is smoothed against the
    # wheel sensor, and fall back to computing it from altitude over distance.
    grade_series = window.channels.get("grade_pct")
    if grade_series is None or not np.isfinite(grade_series).any():
        grade_series = metrics.grade(altitude, distance)

    computed_gain, computed_loss = metrics.elevation_gain_loss(altitude)

    avg_speed, min_speed, max_speed = metrics.channel_stats(speed)
    avg_hr, min_hr, max_hr = metrics.channel_stats(hr)
    _plain_cadence, min_cad, max_cad = metrics.channel_stats(cadence)
    avg_pwr, min_pwr, max_pwr = metrics.channel_stats(power)
    avg_alt, min_alt, max_alt = metrics.channel_stats(altitude)
    avg_grade, min_grade, max_grade = metrics.channel_stats(grade_series)
    avg_temp, min_temp, max_temp = metrics.channel_stats(temperature)
    avg_balance, _, _ = metrics.channel_stats(balance)

    distance_m = _float_or(fit.get("total_distance"), _travelled(distance))

    return MetricFields(
        elapsed_time_s=elapsed_s,
        moving_time_s=moving_s,
        paused_time_s=max(0.0, elapsed_s - moving_s),
        distance_m=distance_m,
        # Ascent is copied from the device wherever it exists; the computed
        # pair is a cross-check only (see metrics/elevation.py).
        elevation_gain_m=_float_or(fit.get("total_ascent"), computed_gain),
        elevation_loss_m=_float_or(fit.get("total_descent"), computed_loss),
        avg_altitude_m=_pick(fit.get("enhanced_avg_altitude") or fit.get("avg_altitude"), avg_alt),
        max_altitude_m=_pick(fit.get("enhanced_max_altitude") or fit.get("max_altitude"), max_alt),
        min_altitude_m=_pick(fit.get("enhanced_min_altitude") or fit.get("min_altitude"), min_alt),
        avg_grade_pct=avg_grade,
        max_grade_pct=max_grade,
        min_grade_pct=min_grade,
        avg_vam_mph=metrics.vam(altitude, window.time_s) or None,
        avg_speed_ms=_pick(
            fit.get("enhanced_avg_speed") or fit.get("avg_speed"),
            _average_speed(distance_m, moving_s, avg_speed),
        ),
        max_speed_ms=_pick(fit.get("enhanced_max_speed") or fit.get("max_speed"), max_speed),
        min_speed_ms=min_speed,
        avg_hr_bpm=_int_or(fit.get("avg_heart_rate"), avg_hr),
        max_hr_bpm=_int_or(fit.get("max_heart_rate"), max_hr),
        min_hr_bpm=_int_or(None, min_hr),
        avg_cadence_rpm=_int_or(fit.get("avg_cadence"), _average_cadence(cadence)),
        max_cadence_rpm=_int_or(fit.get("max_cadence"), max_cad),
        min_cadence_rpm=_int_or(None, min_cad),
        avg_power_w=_int_or(fit.get("avg_power"), avg_pwr) if has_power else None,
        max_power_w=_int_or(fit.get("max_power"), max_pwr) if has_power else None,
        min_power_w=_int_or(None, min_pwr) if has_power else None,
        normalized_power_w=round(np_w) if has_power else None,
        energy_kj=work_kj,
        calories_kcal=metrics.calories_kcal(work_kj) if work_kj else None,
        left_balance_pct=avg_balance,
        power_available=has_power,
        load_source=LOAD_SOURCE_POWER if has_power and if_ else LOAD_SOURCE_HR,
        ftp_at_time_w=ftp_w,
        lthr_at_time_bpm=lthr_bpm,
        intensity_factor=if_ or None,
        tss=load,
        decoupling_pct=metrics.decoupling(power, hr) if has_power else None,
        # Temperature is session-only in FIT — a lap message has no
        # `avg_temperature`, so this is always computed for laps. That matters:
        # the evening ride's first lap averages 25 °C against the ride's 27 °C
        # because the sensor was still warming up.
        avg_temperature_c=_pick(fit.get("avg_temperature"), avg_temp),
        max_temperature_c=_pick(fit.get("max_temperature"), max_temp),
        min_temperature_c=min_temp,
    )


def cross_check(fit: Mapping[str, Any], computed: MetricFields, window: ChannelWindow) -> list[str]:
    """Compare our maths against the device's, per masterplan §3.3.

    Only ever called for the whole ride. Lap ascents are small integers — 10 m,
    20 m — so a relative gap explodes on them: one short lap would trip the
    elevation tolerance on correct data and bury the real signal in noise.
    Per-lap divergence is logged at debug instead.
    """
    warnings: list[str] = []

    device_np = fit.get("normalized_power")
    np_w = computed.normalized_power_w
    if np_w and device_np and _relative_gap(np_w, float(device_np)) > METRIC_TOLERANCE:
        warnings.append(f"Normalized power {np_w:.0f} W differs from the FIT value {device_np} W.")
        log.warning("ride.import.mismatch", metric="normalized_power", computed=np_w, fit=device_np)

    gain, loss = metrics.elevation_gain_loss(window.get("altitude_m"))
    for label, value, key in (
        ("Ascent", gain, "total_ascent"),
        ("Descent", loss, "total_descent"),
    ):
        device_value = fit.get(key)
        if device_value and _relative_gap(value, float(device_value)) > ELEVATION_TOLERANCE:
            warnings.append(f"{label} {value:.0f} m differs from the FIT value {device_value} m.")
            log.info("ride.import.mismatch", metric=key, computed=round(value), fit=device_value)

    return warnings


# ---------------------------------------------------------------------------
# The device's averaging conventions
# ---------------------------------------------------------------------------
#
# Verified against both reference files. Getting these wrong is invisible while
# the FIT message supplies the value and only shows up on a window the device
# never summarized — which is exactly every lap field it does not carry.


def _average_cadence(cadence: FloatArray) -> float | None:
    """Mean of the pedalling samples only.

    Coasting is not pedalling, so the device leaves zero-cadence samples out.
    Including them gives 81.1 rpm where the Karoo reports 83.
    """
    return metrics.mean_active(cadence)


def _average_speed(distance_m: float, moving_s: float, channel_mean: float | None) -> float | None:
    """Distance over moving time, not the mean of the speed channel.

    The two differ by more than rounding: on the evening ride's first lap the
    channel mean is 30.8 km/h where the device reports 30.3, because a mean
    over samples weights a stationary second the same as a moving one.
    """
    if moving_s > 0 and distance_m > 0:
        return distance_m / moving_s
    return channel_mean


def _travelled(distance: FloatArray) -> float | None:
    """Distance covered within a window.

    The FIT ``distance`` channel is cumulative over the whole ride, so a lap's
    distance is the difference across its slice — not the last value, which
    would give every lap the ride's total up to that point.
    """
    finite = distance[np.isfinite(distance)]
    if finite.size == 0:
        return None
    return float(finite[-1] - finite[0])


# ---------------------------------------------------------------------------
# Small conversions
# ---------------------------------------------------------------------------


def _pick(primary: Any, fallback: float | None) -> float | None:
    """First usable of the two, preserving None rather than coercing to zero."""
    for value in (primary, fallback):
        if value is not None and np.isfinite(float(value)):
            return float(value)
    return None


def _float_or(primary: Any, fallback: float | None) -> float:
    """Like :func:`_pick`, but for non-nullable columns."""
    return _pick(primary, fallback) or 0.0


def _int_or(primary: Any, fallback: float | None) -> int | None:
    value = _pick(primary, fallback)
    return None if value is None else round(value)


def _relative_gap(a: float, b: float) -> float:
    if b == 0:
        return 0.0 if a == 0 else 1.0
    return abs(a - b) / abs(b)
