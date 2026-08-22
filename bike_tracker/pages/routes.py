"""Routes list (masterplan §2.3.1)."""

import reflex as rx

from ..navigation import routes as route_constants
from ..ui.base import base_page
from ._stub import stub_page


@rx.page(route=route_constants.ROUTES_ROUTE, title="Routes")
def routes_page() -> rx.Component:
    return base_page(
        stub_page(
            title="Routes",
            icon="route",
            summary=(
                "Routes are derived automatically: each imported ride is "
                "matched against the routes already on record, and starts a "
                "new one when nothing fits. That matching already runs on "
                "import — this view is where the results will be listed."
            ),
            planned=[
                "Card per route with an inset map preview",
                "Distance, elevation, average ride time and ride count",
                "Rename a route",
            ],
        )
    )
