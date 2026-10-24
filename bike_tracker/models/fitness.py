"""FTP and baseline evaluations.

One row per evaluation the user performed on a ride. ``valid_from`` is the
start of that ride (masterplan §2.4.2), which is what makes
``Ride.ftp_at_time_w`` resolvable: import picks the newest evaluation whose
``valid_from`` is at or before the ride — the last value *before* the ride,
never the newest overall.

The method parameters are stored alongside the result because the evaluation
card must be re-openable for editing (masterplan §3.4, general note). Without
them the dialog cannot restore the sliders the user set.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from reflex.model import ModelRegistry
from sqlmodel import Field, SQLModel

from ..metrics import POWER_CURVE_WINDOWS_S, EffortWindow, best_effort_windows
from ..metrics.power import FTP_FROM_20MIN_FACTOR as _FTP_FROM_20MIN_FACTOR
from .ride import Ride

EVAL_FTP = "ftp"
EVAL_BASELINE = "baseline"
# Lactate threshold heart rate. Not offered by the ride-detail dialog, which
# masterplan §2.4.2 limits to FTP and baseline, but the Fitness view shows
# LTHR (§2.5) and heart-rate training load needs it, so the type exists from
# the start rather than arriving as a later migration.
EVAL_LTHR = "lthr"

# How the evaluation window was chosen.
METHOD_WINDOW = "window"  # two sliders: start % and end % of ride duration
METHOD_LAP = "lap"  # a lap of the ride
METHOD_BEST_FIT = "best_fit"  # sliding 20 min window around a guess (FTP only)

# Re-exported from metrics so the factor and the function that applies it
# cannot drift apart. `metrics/` may not import `models/`, but the reverse is
# fine — metrics imports nothing from this app at all.
FTP_FROM_20MIN_FACTOR = _FTP_FROM_20MIN_FACTOR


@ModelRegistry.register
class FitnessEval(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    # EVAL_FTP (watts) or EVAL_BASELINE (W/bpm).
    type: str = Field(index=True)
    value: float = Field(default=0.0)

    # Start of the ride this was derived from. Indexed because every import
    # does a "newest at or before this ride" lookup against it.
    valid_from: datetime = Field(index=True)
    ride_id: int | None = Field(default=None, foreign_key="ride.id", index=True)

    # The evaluated window itself.
    start_time: datetime
    end_time: datetime

    # --- method and its parameters, so the edit dialog can be reopened -----
    method: str = Field(default=METHOD_WINDOW)
    start_frac: float | None = Field(default=None)  # METHOD_WINDOW
    end_frac: float | None = Field(default=None)  # METHOD_WINDOW
    lap_index: int | None = Field(default=None)  # METHOD_LAP
    guess_frac: float | None = Field(default=None)  # METHOD_BEST_FIT

    # Companion values, so a baseline evaluation keeps the pair it came from.
    avg_power_w: float | None = Field(default=None)
    avg_hr_bpm: float | None = Field(default=None)

    note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# Window lengths of the Fitness page's power-curve toggle (masterplan §2.5),
# in days, and how many consecutive windows it draws.
CURVE_WINDOW_DAYS: tuple[int, ...] = (30, 90)
MAX_CURVE_WINDOWS = 4


def latest_evaluations(evaluations: Iterable[FitnessEval]) -> dict[str, FitnessEval]:
    """The current value of each evaluation type: the newest by ``valid_from``.

    The one definition of "current FTP / LTHR / baseline". The Fitness page,
    the Dashboard and the REST API all read it, so they cannot show three
    different FTPs. There is deliberately no fallback to the head unit's
    figure: the pages show "—" until an evaluation exists, and so does the
    API (AGENTS.md rule 45).
    """
    latest: dict[str, FitnessEval] = {}
    for record in sorted(evaluations, key=lambda e: e.valid_from):
        latest[record.type] = record
    return latest


def ride_efforts(ride: Ride) -> dict[int, float | None]:
    """The ride's stored mean-maximal curve, keyed by window length in seconds."""
    return {
        duration: getattr(ride, f"best_power_{duration}s_w", None)
        for duration in POWER_CURVE_WINDOWS_S
    }


def power_curve_windows(
    rides: Sequence[Ride], *, window_days: int, now: datetime
) -> list[EffortWindow]:
    """The Fitness page's best-effort windows, newest first.

    ``rides`` should come from ``analysis_rides()``; the curve describes
    training.
    """
    return best_effort_windows(
        [(ride.start_time, ride_efforts(ride)) for ride in rides],
        window_days=window_days,
        now=now,
        max_windows=MAX_CURVE_WINDOWS,
        durations_s=POWER_CURVE_WINDOWS_S,
    )
