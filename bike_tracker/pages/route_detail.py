"""Route detail (masterplan §2.3.2)."""

import reflex as rx

from ..navigation import routes as route_constants
from ..ui.base import base_page
from ._stub import stub_page


@rx.page(route=route_constants.ROUTE_DETAIL_ROUTE, title="Route")
def route_detail() -> rx.Component:
    return base_page(
        stub_page(
            title="Route",
            icon="route",
            summary=("One route, every ride along it, and how the numbers changed between them."),
            planned=[
                "Page-wide map with every matched ride overlaid, so small deviations are visible",
                "Route-level card: distance, elevation, average ride time",
                "Table of matching rides — date, speed, power, NP, TSS",
            ],
        )
    )
