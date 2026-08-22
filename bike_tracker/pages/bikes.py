"""Bikes view (masterplan §2.6)."""

import reflex as rx

from ..navigation import routes
from ..ui.base import base_page
from ._stub import stub_page


@rx.page(route=routes.BIKES_ROUTE, title="Bikes")
def bikes() -> rx.Component:
    return base_page(
        stub_page(
            title="Bikes",
            icon="wrench",
            summary=(
                "Bike profiles and their ANT+ sensors. Both bikes are seeded "
                "with their real sensor ids already, which is what lets ride "
                "import work out which bike a ride was done on."
            ),
            planned=[
                "Card per bike with name, system weight and odometer",
                "Add, edit and delete profiles",
                "Sensor table per bike: type plus ANT+ id",
            ],
        )
    )
