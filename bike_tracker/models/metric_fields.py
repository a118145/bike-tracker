"""Metric columns shared by :class:`~.ride.Ride` and :class:`~.ride.Lap`.

A lap is a ride in miniature: the same distance, the same climbing, the same
training load, measured over a shorter window. Selecting a lap on the detail
page must therefore fill the same thirty-odd stat cards a ride does, and the
only way to guarantee that as the schema grows is to give both tables one
definition of "the numbers".

Inheriting from a non-table ``SQLModel`` materialises every field below as a
real column on both tables — verified, including ``index=True`` and
``@ModelRegistry.register``, which sees only the two concrete tables because
the base never enters ``SQLModel.metadata``.

Inheritance alone cannot stop someone adding a metric straight to ``Ride``,
so ``tests/test_view_models.py`` pins the difference between the two field
sets to an explicit list of ride-only names.

**Which numbers are copied and which are computed** follows masterplan §3.3:
values that do not depend on the rider's fitness (distance, ascent, max speed)
are copied from the FIT message; normalized power is computed by the backend,
and so is everything derived from *our* FTP rather than the device's (IF, TSS,
decoupling). Import compares the two and logs the divergence — see
``ingest/summarize.py``.
"""

from sqlmodel import Field, SQLModel

# ``load_source`` values. Which one applies is decided per ride *and per lap*
# by ``power_available`` — brief §5.1 requires every load value to say where it
# came from, or commutes silently get compared with road rides.
LOAD_SOURCE_POWER = "power"
LOAD_SOURCE_HR = "hr"


class MetricFields(SQLModel):
    """Every measured or derived quantity, over whatever window applies.

    Not a table and not registered: it exists to be inherited.
    """

    # --- time and distance -------------------------------------------------
    elapsed_time_s: float = Field(default=0.0)
    moving_time_s: float = Field(default=0.0)
    paused_time_s: float = Field(default=0.0)
    distance_m: float = Field(default=0.0)

    # --- elevation ---------------------------------------------------------
    elevation_gain_m: float = Field(default=0.0)
    elevation_loss_m: float = Field(default=0.0)
    avg_altitude_m: float | None = Field(default=None)
    max_altitude_m: float | None = Field(default=None)
    min_altitude_m: float | None = Field(default=None)
    avg_grade_pct: float | None = Field(default=None)
    max_grade_pct: float | None = Field(default=None)
    min_grade_pct: float | None = Field(default=None)
    avg_vam_mph: float | None = Field(default=None)

    # --- speed -------------------------------------------------------------
    avg_speed_ms: float | None = Field(default=None)
    max_speed_ms: float | None = Field(default=None)
    min_speed_ms: float | None = Field(default=None)

    # --- heart rate --------------------------------------------------------
    avg_hr_bpm: int | None = Field(default=None)
    max_hr_bpm: int | None = Field(default=None)
    min_hr_bpm: int | None = Field(default=None)

    # --- cadence -----------------------------------------------------------
    avg_cadence_rpm: int | None = Field(default=None)
    max_cadence_rpm: int | None = Field(default=None)
    min_cadence_rpm: int | None = Field(default=None)

    # --- power -------------------------------------------------------------
    avg_power_w: int | None = Field(default=None)
    max_power_w: int | None = Field(default=None)
    min_power_w: int | None = Field(default=None)
    normalized_power_w: int | None = Field(default=None)
    energy_kj: float | None = Field(default=None)
    calories_kcal: float | None = Field(default=None)
    # Average share of total power delivered by the left leg, in percent.
    # Only the dual-sided Assioma reports it; the single-sided SRAM crank does
    # not (brief §2).
    left_balance_pct: float | None = Field(default=None)

    # --- training load -----------------------------------------------------
    power_available: bool = Field(default=False)
    load_source: str = Field(default=LOAD_SOURCE_HR)
    # The FTP that applied *when this ride happened*, resolved at import from
    # the FitnessEval history. Never look it up live: brief §5.2 — raising FTP
    # would otherwise rewrite every historical TSS and make the winter CTL
    # curve meaningless.
    #
    # On a lap this repeats the parent ride's value. That is deliberate
    # duplication: it is what lets the FTP card show something when a lap is
    # selected, and what makes a lap's TSS self-contained. The cost is that
    # correcting a ride's FTP means rewriting its laps too.
    ftp_at_time_w: int | None = Field(default=None)
    lthr_at_time_bpm: int | None = Field(default=None)
    intensity_factor: float | None = Field(default=None)
    tss: float | None = Field(default=None)
    # Pw:HR drift, first half against second half, in percent. Precomputed
    # rather than derived on demand (brief §5.4).
    decoupling_pct: float | None = Field(default=None)

    # --- temperature -------------------------------------------------------
    avg_temperature_c: float | None = Field(default=None)
    max_temperature_c: float | None = Field(default=None)
    min_temperature_c: float | None = Field(default=None)
