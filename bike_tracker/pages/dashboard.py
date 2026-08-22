"""Dashboard — the landing page (masterplan §2.2)."""

import reflex as rx

from ..navigation import routes
from ..ui.base import base_page
from ._stub import stub_page


@rx.page(route=routes.HOME_ROUTE, title="Dashboard")
def dashboard() -> rx.Component:
    return base_page(
        stub_page(
            title="Dashboard",
            icon="layout-dashboard",
            summary=(
                "Totals across every ride, grouped by bike profile, above a "
                "page-wide heat map of everywhere you have ridden."
            ),
            planned=[
                "Heat map of all routes, drawn semi-transparently so repeated roads stand out",
                "Summary cards: total distance, rides, routes, calories, current FTP and zones",
                "Per-bike cards that filter the map when toggled",
            ],
        )
    )
