"""Route registration guard.

The classic Reflex failure is a page module that is never imported: the
``@rx.page`` decorator never runs, the route silently 404s, and nothing in
the codebase looks wrong. This test makes that a build failure.
"""

from reflex.config import get_config

# `rx.page` is the decorator function; the registry lives on the module, which
# Reflex swaps for a namespace class at import time.
from reflex.page import DECORATED_PAGES

from bike_tracker import bike_tracker as app_module  # noqa: F401
from bike_tracker.navigation import nav_config, routes

EXPECTED_ROUTES = {
    routes.HOME_ROUTE,
    routes.ROUTES_ROUTE,
    routes.ROUTE_DETAIL_ROUTE,
    routes.RIDES_ROUTE,
    routes.RIDE_DETAIL_ROUTE,
    routes.FITNESS_ROUTE,
    routes.SETTINGS_ROUTE,
    routes.HAMMERHEAD_CALLBACK_ROUTE,
    routes.NOT_FOUND_ROUTE,
}


def _registered_routes() -> set[str]:
    decorated = DECORATED_PAGES[get_config().app_name]
    return {page_args["route"] for _, page_args in decorated}


def test_every_expected_route_is_registered() -> None:
    missing = EXPECTED_ROUTES - _registered_routes()
    assert not missing, f"Route constants with no @rx.page: {sorted(missing)}"


def test_no_unexpected_routes() -> None:
    """Catches a page registered with a hard-coded path instead of a constant."""
    extra = _registered_routes() - EXPECTED_ROUTES
    assert not extra, f"Routes not declared in routes.py: {sorted(extra)}"


def test_every_nav_route_is_registered() -> None:
    """Every menu entry must lead somewhere real."""
    unreachable = set(nav_config.ALL_ROUTES) - _registered_routes()
    assert not unreachable, f"Nav entries with no page: {sorted(unreachable)}"
