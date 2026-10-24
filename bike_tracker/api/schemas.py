"""Response models of the REST API — the contract the MCP server documents.

Separate from the tables (brief §6): a table class returned as a response would
drag every schema change into the MCP contract unnoticed. The one deliberate
exception is :class:`MetricsOut`, which *inherits* the non-table
:class:`~..models.MetricFields`. That is the parity rule (AGENTS.md rule 45)
made structural: the API reports exactly the numbers the stat cards show, and
a metric added to ``MetricFields`` (rule 16) appears here by construction. The
committed ``mcp_server/openapi_v1.json`` snapshot then fails until the MCP
docstrings are updated, so the addition is noticed rather than silent.

Key names carry their unit (rule 12) and equal the model field names, so
nothing is renamed between the database and the LLM. Times come as
``start_time_utc`` plus the local calendar (``local_date``, ``weekday``,
``local_start_time``) from ``utils/local_time`` — the same conversion the UI
uses.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from ..models import MetricFields


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricsOut(MetricFields):
    """Every stored metric of a ride, a lap or a cut window. See module docstring."""


# --- version -----------------------------------------------------------------


class VersionOut(ApiModel):
    app_version: str
    api_version: str
    schema_revision: str | None
    timezone: str


# --- athlete -----------------------------------------------------------------


class PowerZoneOut(ApiModel):
    name: str
    description: str
    low_w: int
    high_w: int | None


class HrZoneOut(ApiModel):
    name: str
    description: str
    low_bpm: int
    high_bpm: int | None


class SensorOut(ApiModel):
    sensor_type: str
    ant_id: int
    label: str | None


class BikeOut(ApiModel):
    id: int
    name: str
    weight_kg: float
    odo_m: float
    sensors: list[SensorOut]


class TagOut(ApiModel):
    id: int
    name: str
    color: str
    effect: str | None


class AthleteOut(ApiModel):
    ftp_w: float | None
    ftp_valid_from: date | None
    lthr_bpm: float | None
    lthr_valid_from: date | None
    baseline_w_per_bpm: float | None
    baseline_valid_from: date | None
    power_zones: list[PowerZoneOut]
    hr_zones: list[HrZoneOut]
    bikes: list[BikeOut]
    tags: list[TagOut]


# --- evaluations -------------------------------------------------------------


class EvaluationOut(ApiModel):
    id: int
    type: str
    # The unit depends on the type, so it travels next to the value rather
    # than in its name: W for ftp, bpm for lthr, W/bpm for baseline.
    value: float
    unit: str
    valid_from_utc: datetime
    local_date: date
    ride_id: int | None
    method: str
    source: str


# --- activities --------------------------------------------------------------


class ActivityRow(ApiModel):
    id: int
    name: str
    start_time_utc: datetime
    local_date: date
    local_start_time: str
    weekday: str
    bike_id: int | None
    bike_name: str | None
    route_id: int | None
    route_name: str | None
    distance_m: float
    moving_time_s: float
    elevation_gain_m: float
    avg_power_w: int | None
    normalized_power_w: int | None
    avg_hr_bpm: int | None
    avg_cadence_rpm: int | None
    intensity_factor: float | None
    tss: float | None
    load_source: str
    ftp_at_time_w: int | None
    decoupling_pct: float | None
    num_laps: int
    evaluations: list[str]
    tags: list[str]
    excluded: bool
    note_summary: str | None


class ActivitiesOut(ApiModel):
    from_date: date | None
    to_date: date | None
    bike_id: int | None
    route_id: int | None
    tag: str | None
    include_excluded: bool
    limit: int
    count: int
    activities: list[ActivityRow]


class NoteOut(ApiModel):
    text: str | None
    sleep_quality: int | None
    rpe: int | None
    perceived_breathing_limit: int | None


class LapOut(MetricsOut):
    index: int
    start_time_utc: datetime
    local_start_time: str
    # The lap on the stream clock, so a sub-window ("lap 2 without its first
    # five minutes") can be asked of the window cutter directly.
    window_start_s: float
    window_end_s: float


class ActivityOut(MetricsOut):
    id: int
    name: str
    start_time_utc: datetime
    local_date: date
    local_start_time: str
    weekday: str
    sport: str
    bike_id: int | None
    bike_name: str | None
    route_id: int | None
    route_name: str | None
    num_laps: int
    tags: list[str]
    excluded: bool
    note: NoteOut | None
    evaluations: list[EvaluationOut]
    stream_channels: list[str]
    laps: list[LapOut]


class WindowOut(MetricsOut):
    activity_id: int
    lap_index: int | None
    start_s: float
    end_s: float
    sample_count: int
    ftp_estimate_w: float | None
    baseline_w_per_bpm: float | None


class StreamsOut(ApiModel):
    activity_id: int
    start_s: float
    end_s: float
    step_s: int
    sample_count: int
    time_s: list[float]
    channels: dict[str, list[float | None]]


# --- routes ------------------------------------------------------------------


class RouteOut(ApiModel):
    id: int
    name: str
    ride_count: int
    avg_distance_m: float | None
    avg_elevation_gain_m: float | None
    avg_moving_time_s: float | None
    last_ridden_local_date: date | None


class RoutesOut(ApiModel):
    count: int
    routes: list[RouteOut]


class RoutePassOut(ApiModel):
    activity_id: int
    name: str
    start_time_utc: datetime
    local_date: date
    local_start_time: str
    weekday: str
    bike_id: int | None
    bike_name: str | None
    moving_time_s: float
    avg_speed_ms: float | None
    avg_power_w: int | None
    normalized_power_w: int | None
    best_power_1200s_w: int | None
    intensity_factor: float | None
    tss: float | None
    avg_hr_bpm: int | None
    tags: list[str]
    excluded: bool
    note_summary: str | None


class RouteHistoryOut(ApiModel):
    route: RouteOut
    include_excluded: bool
    rides_in_analysis: int
    rides_excluded: int
    fastest_moving_time_s: float | None
    fastest_on_local_date: date | None
    avg_tss: float | None
    best_tss: float | None
    passes: list[RoutePassOut]


# --- fitness -----------------------------------------------------------------


class CurveWindowOut(ApiModel):
    window_days: int
    label: str
    start_local_date: date
    end_local_date: date
    ride_count: int
    best_power_5s_w: float | None
    best_power_15s_w: float | None
    best_power_30s_w: float | None
    best_power_60s_w: float | None
    best_power_300s_w: float | None
    best_power_600s_w: float | None
    best_power_1200s_w: float | None
    best_power_3600s_w: float | None


class FitnessOut(ApiModel):
    evaluations: list[EvaluationOut]
    power_curves: list[CurveWindowOut]
