"""One-shot backfills for columns added after rides were already imported.

Run by hand after a migration that adds a *derived* column, never as part of
the migration itself: a migration must not import application code, or the
schema history stops being replayable once that code changes.

Only safe for values that are pure functions of data already stored. That
holds for ``Ride.map_points`` — it is geometry recomputed from the ride's own
streams — but would not hold for anything copied from the FIT file, since the
files themselves are never kept (masterplan §1.3).

    uv run python -m bike_tracker.ingest.backfill
"""

import reflex as rx
from sqlmodel import select

from .. import models  # noqa: F401  — registers the tables before the session opens
from ..models import Ride
from ..utils.logging import configure_logging, get_logger
from .importer import ensure_map_points

log = get_logger(__name__)


def backfill_map_points() -> int:
    """Fill ``Ride.map_points`` for every ride that predates the column."""
    with rx.session() as session:
        rides = session.exec(select(Ride)).all()
        return ensure_map_points(session, rides)


def main() -> None:
    configure_logging()
    filled = backfill_map_points()
    print(f"map_points: filled {filled} ride(s).")


if __name__ == "__main__":
    main()
