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
    EVAL_BASELINE,
    EVAL_FTP,
    EVAL_LTHR,
    FTP_FROM_20MIN_FACTOR,
    METHOD_BEST_FIT,
    METHOD_LAP,
    METHOD_WINDOW,
    FitnessEval,
)
from .metric_fields import LOAD_SOURCE_HR, LOAD_SOURCE_POWER, MetricFields
from .ride import (
    Lap,
    Ride,
    RideNote,
    RideStream,
)
from .route import Route

__all__ = [
    "EVAL_BASELINE",
    "EVAL_FTP",
    "EVAL_LTHR",
    "FTP_FROM_20MIN_FACTOR",
    "LOAD_SOURCE_HR",
    "LOAD_SOURCE_POWER",
    "METHOD_BEST_FIT",
    "METHOD_LAP",
    "METHOD_WINDOW",
    "SENSOR_TYPES",
    "Bike",
    "BikeForm",
    "BikeSensor",
    "FitnessEval",
    "Lap",
    "MetricFields",
    "Ride",
    "RideNote",
    "RideStream",
    "Route",
]
