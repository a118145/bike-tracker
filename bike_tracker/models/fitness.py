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

from datetime import UTC, datetime

from reflex.model import ModelRegistry
from sqlmodel import Field, SQLModel

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

# FTP is 95% of the best 20-minute average power (masterplan §2.4.2).
FTP_FROM_20MIN_FACTOR = 0.95


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
