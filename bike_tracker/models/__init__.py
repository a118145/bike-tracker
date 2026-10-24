"""SQLModel tables.

**Every model must be imported here, and this package must be imported at app
start.** Reflex only sees models that the app actually imports, so a table
written exclusively by the importer — never rendered by a page — is silently
missed by ``reflex db makemigrations`` and you find out when the schema
diverges (brief §3.1, trap 1). ``bike_tracker.py`` imports this package for
that reason alone.
"""

from .bike import SENSOR_TYPES, Bike, BikeForm, BikeSensor
from .fitness import (
    CURVE_WINDOW_DAYS,
    EVAL_BASELINE,
    EVAL_FTP,
    EVAL_LTHR,
    FTP_FROM_20MIN_FACTOR,
    MAX_CURVE_WINDOWS,
    METHOD_BEST_FIT,
    METHOD_LAP,
    METHOD_WINDOW,
    FitnessEval,
    latest_evaluations,
    power_curve_windows,
    ride_efforts,
)
from .hammerhead import (
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_POLL_INTERVAL_MIN,
    HammerheadAccount,
    get_account,
    now_utc,
)
from .metric_fields import LOAD_SOURCE_HR, LOAD_SOURCE_POWER, MetricFields
from .ride import (
    Lap,
    Ride,
    RideNote,
    RideNoteForm,
    RideStream,
    analysis_rides,
    excluded_ride_ids,
    in_analysis,
    lap_window_s,
    load_note_summaries,
    note_summary,
    save_ride_note,
)
from .route import Route, RouteForm, RouteSummary, last_ridden_by_route, route_summary
from .tag import (
    TAG_COLORS,
    TAG_EFFECT_EXCLUDE,
    TAG_EFFECTS,
    TAG_NAME_MAX_LENGTH,
    RideTag,
    Tag,
    TagForm,
    match_tag,
)

__all__ = [
    "CURVE_WINDOW_DAYS",
    "DEFAULT_BACKFILL_DAYS",
    "DEFAULT_POLL_INTERVAL_MIN",
    "EVAL_BASELINE",
    "EVAL_FTP",
    "EVAL_LTHR",
    "FTP_FROM_20MIN_FACTOR",
    "LOAD_SOURCE_HR",
    "LOAD_SOURCE_POWER",
    "MAX_CURVE_WINDOWS",
    "METHOD_BEST_FIT",
    "METHOD_LAP",
    "METHOD_WINDOW",
    "SENSOR_TYPES",
    "TAG_COLORS",
    "TAG_EFFECTS",
    "TAG_EFFECT_EXCLUDE",
    "TAG_NAME_MAX_LENGTH",
    "Bike",
    "BikeForm",
    "BikeSensor",
    "FitnessEval",
    "HammerheadAccount",
    "Lap",
    "MetricFields",
    "Ride",
    "RideNote",
    "RideNoteForm",
    "RideStream",
    "RideTag",
    "Route",
    "RouteForm",
    "RouteSummary",
    "Tag",
    "TagForm",
    "analysis_rides",
    "excluded_ride_ids",
    "get_account",
    "in_analysis",
    "lap_window_s",
    "last_ridden_by_route",
    "latest_evaluations",
    "load_note_summaries",
    "match_tag",
    "note_summary",
    "now_utc",
    "power_curve_windows",
    "ride_efforts",
    "route_summary",
    "save_ride_note",
]
