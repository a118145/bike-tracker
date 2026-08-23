"""One-shot backfills for columns added after rides were already imported.

Run by hand after a migration that adds a *derived* column, never as part of
the migration itself: a migration must not import application code, or the
schema history stops being replayable once that code changes.

Only safe for values that are pure functions of data already stored. That
holds for ``Ride.map_points`` — it is geometry recomputed from the ride's own
streams — but would not hold for anything copied from the FIT file, since the
files themselves are never kept (masterplan §1.3).

    uv run python -m bike_tracker.ingest.backfill            # map_points
    uv run python -m bike_tracker.ingest.backfill --routes   # re-derive routes
"""

import sys

import reflex as rx
from sqlmodel import col, select

from .. import (
    models,  # noqa: F401  — registers the tables before the session opens
)
from ..models import Ride, Route
from ..utils.logging import configure_logging, get_logger
from .importer import assign_route, ensure_map_points

log = get_logger(__name__)


def backfill_map_points() -> int:
    """Fill ``Ride.map_points`` for every ride that predates the column."""
    with rx.session() as session:
        rides = session.exec(select(Ride)).all()
        return ensure_map_points(session, rides)


def rebuild_routes() -> tuple[int, int]:
    """Re-derive every route from scratch, oldest ride first.

    Routes are pure derived data — they exist because rides matched each other
    — so when the matcher changes, the routes it produced are stale. Nothing
    here is recoverable from the routes themselves, which is what makes
    throwing them away safe: the geometry comes from the rides.

    **Oldest first, deliberately.** A route is established and named by the
    first time it was ridden, which is both the natural reading and the reason
    this cannot simply reuse the order rides happen to have been imported in —
    the Hammerhead poller works newest-first, so route names came from the most
    recent ride of each group.

    Rides, laps and streams are never touched; only ``Route`` rows and
    ``Ride.route_id``.

    Returns:
        ``(rides_considered, routes_created)``.
    """
    with rx.session() as session:
        # Drop the assignment before the routes, or the foreign key is briefly
        # dangling and `refresh_route_stats` would prune against stale counts.
        rides = list(session.exec(select(Ride).order_by(col(Ride.start_time))).all())
        for ride in rides:
            ride.route_id = None
            session.add(ride)
        session.commit()

        for route in session.exec(select(Route)).all():
            session.delete(route)
        session.commit()

        matched = 0
        for ride in rides:
            track = [(p[0], p[1]) for p in ride.map_points or []]
            if not track:
                continue
            assign_route(ride, track, session)
            matched += 1

        routes = len(session.exec(select(Route)).all())

    log.info("routes.rebuilt", rides=matched, routes=routes)
    return matched, routes


def main() -> None:
    configure_logging()

    if "--routes" in sys.argv:
        rides, routes = rebuild_routes()
        print(f"routes: rebuilt {routes} route(s) from {rides} ride(s).")
        return

    filled = backfill_map_points()
    print(f"map_points: filled {filled} ride(s).")


if __name__ == "__main__":
    main()
