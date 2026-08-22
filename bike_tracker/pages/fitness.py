"""Fitness view (masterplan §2.5)."""

import reflex as rx

from ..navigation import routes
from ..ui.base import base_page
from ._stub import stub_page


@rx.page(route=routes.FITNESS_ROUTE, title="Fitness")
def fitness() -> rx.Component:
    return base_page(
        stub_page(
            title="Fitness",
            icon="heart-pulse",
            summary=(
                "FTP and baseline history. Ride import already resolves the "
                "FTP that applied at the time of each ride from this history, "
                "so the numbers stored on past rides never move when a new "
                "evaluation is added."
            ),
            planned=[
                "Power and heart-rate zone cards from the current FTP and LTHR",
                "FTP and baseline (W/bpm) evolution over time",
                "The evaluation dialog on ride detail that feeds this view",
            ],
        )
    )
