"""Ride ingestion: files in, rows out.

``fit.py`` is pure parsing, ``importer.py`` is the single entry point every
source funnels through, and ``sources/`` holds the sources themselves. The
Hammerhead poller in ``sources/hammerhead.py`` calls the same ``import_ride``
as the file source — which is the whole reason the split exists. The API
client and the poll timer live here too, per brief §3.2.
"""

from .fit import FitParseError, ParsedRide, name_from_filename, parse
from .importer import (
    ImportResult,
    import_ride,
    refresh_ftp_dependent,
    resolve_bike,
    resolve_fitness,
)
from .seed import seed_bikes

__all__ = [
    "FitParseError",
    "ImportResult",
    "ParsedRide",
    "import_ride",
    "name_from_filename",
    "parse",
    "refresh_ftp_dependent",
    "resolve_bike",
    "resolve_fitness",
    "seed_bikes",
]
