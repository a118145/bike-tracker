"""Page modules.

**Importing a module here IS its route registration** — the ``@rx.page``
decorator only runs on import. A page that is written but not listed below
silently 404s, and nothing else in the codebase looks wrong.
``tests/test_routes.py`` turns that into a build failure.
"""

from . import (
    bikes,
    dashboard,
    fitness,
    not_found,
    ride_detail,
    rides,
    route_detail,
    routes,
)

__all__ = [
    "bikes",
    "dashboard",
    "fitness",
    "not_found",
    "ride_detail",
    "rides",
    "route_detail",
    "routes",
]
