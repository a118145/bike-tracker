"""Bikes section of the Settings view (masterplan §2.6.1).

A card per bike profile, an always-present card to add another, and one dialog
serving both create and edit. There is no empty state: the add card is the
empty state.

This lives in ``components/`` rather than in the page because §2.6 is a stack
of sections and the page's job is to compose them. Importing ``BikesState``
from here is the direction the folder contract allows — components read
states, states never read pages (AGENTS.md rule 4).
"""

import reflex as rx

from ..states.bikes_state import BikeCard, BikesState, SensorBadge
from .bike_dialog import bike_dialog
from .confirm_dialog import confirm_dialog


def _figure(value: rx.Var[str], unit: str, label: str) -> rx.Component:
    return rx.vstack(
        rx.text(label, size="1", color_scheme="gray"),
        rx.hstack(
            rx.text(value, size="4", weight="bold"),
            rx.text(unit, size="1", color_scheme="gray", padding_bottom="0.15em"),
            spacing="1",
            align="end",
        ),
        spacing="0",
        align="start",
    )


def _sensor_badge(sensor: SensorBadge) -> rx.Component:
    """Type plus ANT+ id, so it is obvious which bike owns which sensor."""
    return rx.badge(
        rx.hstack(
            rx.text(sensor.label, size="1"),
            rx.text(sensor.ant_id, size="1", weight="bold"),
            spacing="2",
            align="center",
        ),
        variant="soft",
        color_scheme="gray",
    )


def _bike_card(bike: BikeCard) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.vstack(
                    rx.heading(bike.name, size="4"),
                    rx.text(bike.ride_summary, size="1", color_scheme="gray"),
                    spacing="0",
                    align="start",
                ),
                rx.spacer(),
                # Stop the click reaching the card, or deleting would also
                # open the edit dialog behind the confirmation.
                rx.icon_button(
                    rx.icon("trash-2", size=17),
                    variant="ghost",
                    color_scheme="red",
                    title="Delete bike",
                    on_click=BikesState.ask_delete(bike.id).stop_propagation,
                ),
                width="100%",
                align="start",
            ),
            rx.hstack(
                _figure(bike.weight_kg, "kg", "System weight"),
                _figure(bike.odo_km, "km", "Odometer"),
                spacing="5",
                width="100%",
            ),
            rx.cond(
                bike.notes != "",
                rx.text(bike.notes, size="1", color_scheme="gray"),
            ),
            rx.cond(
                bike.sensors.length() > 0,
                rx.hstack(
                    rx.foreach(bike.sensors, _sensor_badge),
                    spacing="2",
                    wrap="wrap",
                    width="100%",
                ),
                rx.text(
                    "No sensors — rides cannot be auto-detected.",
                    size="1",
                    color=rx.color("amber", 11),
                ),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
        cursor="pointer",
        on_click=BikesState.open_edit(bike.id),
        style={"_hover": {"borderColor": rx.color("accent", 8)}},
    )


def _add_card() -> rx.Component:
    """Always present, so the page is never empty and never needs a stub."""
    return rx.card(
        rx.center(
            rx.vstack(
                rx.icon("plus", size=26, color=rx.color("accent", 10)),
                rx.text("Add bike profile", size="2", weight="medium"),
                spacing="2",
                align="center",
            ),
            height="100%",
            min_height="8em",
        ),
        width="100%",
        cursor="pointer",
        on_click=BikesState.open_create,
        border=f"1px dashed {rx.color('gray', 7)}",
        style={"_hover": {"borderColor": rx.color("accent", 8)}},
    )


def bikes_section() -> rx.Component:
    """The whole bikes block, dialogs included.

    The dialogs are rendered here rather than by the page so that moving the
    section never leaves its dialogs behind. They are portalled to the body by
    Radix, so their position in the tree costs nothing.
    """
    return rx.vstack(
        rx.vstack(
            rx.heading("Bikes", size="5"),
            rx.text(
                "Sensor ANT+ ids are what match an imported ride to a bike.",
                size="2",
                color_scheme="gray",
            ),
            spacing="1",
            align="start",
        ),
        rx.grid(
            rx.foreach(BikesState.bikes, _bike_card),
            _add_card(),
            columns="2",
            spacing="3",
            width="100%",
        ),
        bike_dialog(),
        confirm_dialog(
            open=BikesState.confirm_open,
            on_open_change=BikesState.set_confirm_open,
            on_confirm=BikesState.confirm_delete,
            title="Delete this bike?",
            description=BikesState.delete_description,
        ),
        spacing="3",
        width="100%",
        align="start",
    )
