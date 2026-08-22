"""Ride ingestion: files in, rows out.

``fit.py`` is pure parsing, ``importer.py`` is the single entry point every
source funnels through, and ``sources/`` holds the sources themselves. The
Hammerhead poller lands in ``sources/hammerhead.py`` later and calls the same
``import_ride`` — which is the whole reason the split exists.
"""

from .fit import FitParseError, ParsedRide, name_from_filename, parse
from .importer import ImportResult, import_ride, resolve_bike, resolve_fitness
from .seed import seed_bikes

__all__ = [
    "FitParseError",
    "ImportResult",
    "ParsedRide",
    "import_ride",
    "name_from_filename",
    "parse",
    "resolve_bike",
    "resolve_fitness",
    "seed_bikes",
]
