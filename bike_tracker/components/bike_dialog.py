"""Create/edit dialog for a bike profile (masterplan §2.6).

One dialog serves both operations — ``BikesState.editing_id`` decides which —
so the field list, the casting and the validation display stay in one place
instead of two nearly-identical forms drifting apart.

Name, weight and notes are *uncontrolled*: they carry ``default_value`` and
arrive as a dict in ``on_submit``, which is what ``parse_form`` is built for.
``key=`` forces a remount whenever the dialog reopens so those defaults are
re-read.

The sensor table is the deliberate exception. Its rows are controlled, because
adding or removing one has to survive without remounting the form — a remount
would discard a half-typed name.
"""

import reflex as rx

from ..models import SENSOR_TYPES
from ..states.bikes_state import BikesState, SensorRow


def _field(
    label: str,
    name: str,
    # A state var is a Var at runtime but is annotated as its Python type, so
    # both spellings have to be accepted here.
    default_value: str | rx.Var[str],
    *,
    placeholder: str = "",
    input_type: str = "text",
    step: str | None = None,
) -> rx.Component:
    """One labelled input plus its error slot."""
    return rx.vstack(
        rx.text(label, size="2", weight="medium"),
        rx.input(
            name=name,
            default_value=default_value,
            placeholder=placeholder,
            type=input_type,
            step=step,
            width="100%",
        ),
        rx.cond(
            BikesState.form_errors.contains(name),
            rx.text(BikesState.form_errors[name], size="1", color=rx.color("red", 11)),
        ),
        spacing="1",
        width="100%",
        align="start",
    )


def _sensor_row(row: SensorRow) -> rx.Component:
    """Type, ANT+ id, label and a delete icon — one physical sensor."""
    return rx.vstack(
        rx.hstack(
            rx.select.root(
                # trigger width must be the same value as root width (both 42%). Otherwise, the max. val applies and the two inputs get smushed to 0% width.
                rx.select.trigger(placeholder="Type", width="42%"),
                rx.select.content(
                    *[rx.select.item(label, value=value) for value, label in SENSOR_TYPES]
                ),
                value=row.sensor_type,
                on_change=lambda value: BikesState.set_sensor_type(row.key, value),
                width="42%",
            ),
            rx.input(
                value=row.ant_id,
                on_change=lambda value: BikesState.set_sensor_ant_id(row.key, value),
                placeholder="ANT+ id",
                width="24%",
            ),
            rx.input(
                value=row.label,
                on_change=lambda value: BikesState.set_sensor_label(row.key, value),
                placeholder="Label (optional)",
                width="34%",
            ),
            # Do NOT add `size=` here: IconButton.create overwrites its child
            # icon's size from a fixed table, so the size=16 below would be
            # silently discarded.
            rx.icon_button(
                rx.icon("trash-2", size=16),
                variant="ghost",
                color_scheme="red",
                title="Remove sensor",
                type="button",
                on_click=BikesState.remove_sensor_row(row.key),
            ),
            spacing="2",
            width="100%",
            align="center",
        ),
        rx.cond(
            row.error != "",
            rx.text(row.error, size="1", color=rx.color("red", 11)),
        ),
        spacing="1",
        width="100%",
        align="start",
    )


def _sensors_section() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text("Sensors", size="2", weight="medium"),
            rx.spacer(),
            rx.button(
                rx.icon("plus", size=14),
                "Add sensor",
                size="1",
                variant="soft",
                type="button",
                on_click=BikesState.add_sensor_row,
            ),
            width="100%",
            align="center",
        ),
        rx.cond(
            BikesState.sensor_rows.length() > 0,
            rx.vstack(
                rx.foreach(BikesState.sensor_rows, _sensor_row),
                spacing="2",
                width="100%",
            ),
            rx.text(
                "No sensors yet. Without one, rides on this bike cannot be detected automatically.",
                size="1",
                color_scheme="gray",
            ),
        ),
        spacing="2",
        width="100%",
        align="start",
    )


def _odometer_section() -> rx.Component:
    """Only offered when editing — a bike being created has no rides yet."""
    return rx.cond(
        BikesState.is_editing,
        rx.hstack(
            rx.button(
                rx.icon("refresh-cw", size=14),
                "Recalculate odometer",
                size="1",
                variant="soft",
                color_scheme="gray",
                type="button",
                loading=BikesState.recalculating,
                on_click=BikesState.recalculate_odometer,
            ),
            rx.text(
                "Rebuilds the total from this bike's rides.",
                size="1",
                color_scheme="gray",
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
    )


def bike_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(BikesState.dialog_title),
            rx.dialog.description(
                "Weight is the system weight — rider plus bike. Sensor ANT+ ids "
                "are what match a ride to this bike on import.",
                size="2",
                color_scheme="gray",
                margin_bottom="1em",
            ),
            rx.form(
                rx.vstack(
                    _field("Name", "name", BikesState.form_name, placeholder="Canyon CF SL 7"),
                    _field(
                        "System weight (kg)",
                        "weight_kg",
                        BikesState.form_weight,
                        input_type="number",
                        step="0.1",
                    ),
                    _field("Notes", "notes", BikesState.form_notes, placeholder="Optional"),
                    rx.divider(margin_y="0.5em"),
                    _sensors_section(),
                    rx.divider(margin_y="0.5em"),
                    _odometer_section(),
                    rx.flex(
                        rx.dialog.close(
                            rx.button(
                                "Cancel",
                                type="button",
                                variant="soft",
                                color_scheme="gray",
                            ),
                        ),
                        # Deliberately NOT wrapped in rx.dialog.close — the
                        # dialog must stay open when validation fails.
                        rx.button(BikesState.submit_label, type="submit"),
                        spacing="3",
                        justify="end",
                        width="100%",
                        margin_top="0.5em",
                    ),
                    spacing="3",
                    width="100%",
                ),
                on_submit=BikesState.submit_form,
                reset_on_submit=False,
            ),
            max_width="36em",
            # Remount on every open so default_value picks up the new prefill.
            key=BikesState.form_key.to_string(),
        ),
        open=BikesState.dialog_open,
        on_open_change=BikesState.set_dialog_open,
    )
