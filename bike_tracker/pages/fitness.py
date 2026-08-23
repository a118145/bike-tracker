"""Fitness view (masterplan §2.5).

Layout order is the spec: the current values and the two zone tables across
the top, the two history charts below them, and the mean-maximal power curves
with their 30/90-day toggle at the bottom.

The evaluations themselves are made on ride detail — this page shows what they
add up to, and is the only place an LTHR can be entered, since §2.4.2's dialog
offers FTP and baseline only.
"""

import plotly.graph_objects as go
import reflex as rx

from ..components.ride_chart import chart_skeleton
from ..components.zone_table import zone_card
from ..models import EVAL_FTP, EVAL_LTHR
from ..navigation import routes
from ..states.fitness_state import CurveRow, EvalRow, FitnessState
from ..ui.base import base_page


def _value_tile(label: str, value: rx.Var[str], unit: str, when: rx.Var[str]) -> rx.Component:
    return rx.vstack(
        rx.text(label, size="1", color_scheme="gray"),
        rx.hstack(
            rx.heading(value, size="6", weight="medium"),
            rx.text(unit, size="1", color_scheme="gray", weight="bold", padding_bottom="0.2em"),
            spacing="1",
            align="end",
        ),
        rx.cond(
            when != "",
            rx.text("measured " + when, size="1", color_scheme="gray"),
            rx.text("not recorded", size="1", color_scheme="gray"),
        ),
        spacing="0",
        align="start",
        min_width="0",
    )


def _current_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("activity", size=16, color=rx.color("accent", 10)),
                rx.text("Current values", size="2", weight="bold"),
                rx.spacer(),
                rx.button(
                    rx.icon("pencil", size=13),
                    "FTP",
                    size="1",
                    variant="soft",
                    on_click=FitnessState.open_entry(EVAL_FTP),
                ),
                rx.button(
                    rx.icon("pencil", size=13),
                    "LTHR",
                    size="1",
                    variant="soft",
                    on_click=FitnessState.open_entry(EVAL_LTHR),
                ),
                width="100%",
                align="center",
                spacing="2",
            ),
            rx.grid(
                _value_tile("FTP", FitnessState.current_ftp, "W", FitnessState.current_ftp_date),
                _value_tile(
                    "LTHR", FitnessState.current_lthr, "bpm", FitnessState.current_lthr_date
                ),
                _value_tile(
                    "Baseline",
                    FitnessState.current_baseline,
                    "W/bpm",
                    FitnessState.current_baseline_date,
                ),
                columns="3",
                spacing="3",
                width="100%",
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
    )


def _history_card(
    title: str, icon: str, figure: rx.Var[dict], has_data: rx.Var[bool], missing: str
) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon(icon, size=16, color=rx.color("accent", 10)),
                rx.text(title, size="2", weight="bold"),
                align="center",
                spacing="2",
                width="100%",
            ),
            rx.cond(
                FitnessState.loading | ~has_data,
                rx.cond(
                    has_data,
                    chart_skeleton("220px"),
                    # An empty figure would draw a phantom axis of arbitrary
                    # dates; say there is nothing yet instead.
                    rx.center(
                        rx.text(missing, size="1", color_scheme="gray"),
                        height="220px",
                        width="100%",
                    ),
                ),
                rx.plotly(
                    # Serialised dict re-typed for the prop, as on the ride
                    # pages — see utils/plotly_json.
                    data=figure.to(go.Figure),
                    config={"displayModeBar": False},
                    use_resize_handler=True,
                    width="100%",
                    height="220px",
                ),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
    )


def _curve_row(row: CurveRow) -> rx.Component:
    return rx.table.row(
        rx.table.row_header_cell(
            rx.vstack(
                rx.text(row.label, size="1", weight="medium"),
                rx.text(row.ride_count, size="1", color_scheme="gray"),
                spacing="0",
                align="start",
            )
        ),
        # `.to(list[str])`: subscripting a dataclass field inside a foreach
        # yields an Any-typed Var, which the inner foreach cannot iterate.
        rx.foreach(row.cells.to(list[str]), lambda value: rx.table.cell(rx.text(value, size="1"))),
    )


def _curves_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("trending-up", size=16, color=rx.color("accent", 10)),
                rx.text("Max. average power", size="2", weight="bold"),
                rx.spacer(),
                rx.segmented_control.root(
                    rx.segmented_control.item("30 days", value="30"),
                    rx.segmented_control.item("90 days", value="90"),
                    value=FitnessState.window_choice,
                    on_change=FitnessState.set_window_choice,
                    size="1",
                ),
                width="100%",
                align="center",
                spacing="2",
            ),
            rx.cond(
                FitnessState.loading,
                chart_skeleton("320px"),
                rx.plotly(
                    data=FitnessState.curve_figure.to(go.Figure),
                    config={"displayModeBar": False},
                    use_resize_handler=True,
                    width="100%",
                    height="320px",
                ),
            ),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Window"),
                        rx.foreach(
                            FitnessState.curve_headers,
                            lambda header: rx.table.column_header_cell(header),
                        ),
                    )
                ),
                rx.table.body(rx.foreach(FitnessState.curve_rows, _curve_row)),
                variant="surface",
                size="1",
                width="100%",
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
    )


def _history_row(row: EvalRow) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.badge(row.kind, variant="soft", size="1")),
        rx.table.cell(rx.text(row.date, size="1")),
        rx.table.cell(rx.text(row.value_text, size="1", weight="medium")),
        rx.table.cell(rx.text(row.source, size="1", color_scheme="gray")),
        rx.table.cell(
            rx.icon_button(
                rx.icon("trash-2", size=14),
                variant="ghost",
                color_scheme="red",
                title="Delete this entry",
                on_click=FitnessState.delete_entry(row.id),
            )
        ),
    )


def _history_table() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.icon("history", size=16, color=rx.color("accent", 10)),
                rx.text("Evaluation history", size="2", weight="bold"),
                rx.spacer(),
                rx.text(
                    "Each entry applies from its date until the next one",
                    size="1",
                    color_scheme="gray",
                ),
                width="100%",
                align="center",
                spacing="2",
            ),
            rx.cond(
                FitnessState.history.length() > 0,
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Type"),
                            rx.table.column_header_cell("From"),
                            rx.table.column_header_cell("Value"),
                            rx.table.column_header_cell("Source"),
                            rx.table.column_header_cell(""),
                        )
                    ),
                    rx.table.body(rx.foreach(FitnessState.history, _history_row)),
                    variant="surface",
                    size="1",
                    width="100%",
                ),
                rx.text(
                    "No evaluations yet. Open a ride and use the Fitness menu, "
                    "or set a value by hand above.",
                    size="1",
                    color_scheme="gray",
                ),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        width="100%",
    )


def _entry_dialog() -> rx.Component:
    """Hand-entered FTP or LTHR. The only way to record an LTHR."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(FitnessState.entry_title),
            rx.dialog.description(
                "Dated today, so it applies from now on and leaves rides "
                "already scored against an older value alone.",
                size="2",
                color_scheme="gray",
                margin_bottom="1em",
            ),
            rx.vstack(
                rx.hstack(
                    rx.input(
                        value=FitnessState.entry_value,
                        on_change=FitnessState.set_entry_value,
                        placeholder="0",
                        type="number",
                        width="100%",
                        auto_focus=True,
                    ),
                    rx.text(FitnessState.entry_unit, size="2", color_scheme="gray"),
                    align="center",
                    spacing="2",
                    width="100%",
                ),
                rx.cond(
                    FitnessState.entry_error != "",
                    rx.text(FitnessState.entry_error, size="1", color=rx.color("red", 11)),
                ),
                rx.flex(
                    rx.dialog.close(
                        rx.button("Cancel", type="button", variant="soft", color_scheme="gray"),
                    ),
                    # Not wrapped in rx.dialog.close — an invalid number must
                    # leave the dialog open with the message showing.
                    rx.button("Save", type="button", on_click=FitnessState.save_entry),
                    spacing="3",
                    justify="end",
                    width="100%",
                    margin_top="0.5em",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="22em",
            key=FitnessState.entry_form_key.to_string(),
        ),
        open=FitnessState.entry_open,
        on_open_change=FitnessState.set_entry_open,
    )


@rx.page(route=routes.FITNESS_ROUTE, title="Fitness", on_load=FitnessState.load)
def fitness() -> rx.Component:
    return base_page(
        rx.vstack(
            rx.vstack(
                rx.heading("Fitness", size="7"),
                rx.text(
                    "Evaluations are made on a ride and dated to its start, so "
                    "the training load stored on past rides never moves.",
                    size="2",
                    color_scheme="gray",
                ),
                spacing="1",
                align="start",
            ),
            _current_card(),
            rx.grid(
                zone_card(
                    "Power zones",
                    "zap",
                    FitnessState.power_zones,
                    "No FTP recorded yet.",
                ),
                zone_card(
                    "Heart-rate zones",
                    "heart",
                    FitnessState.hr_zones,
                    "No LTHR recorded yet.",
                ),
                columns="2",
                spacing="3",
                width="100%",
            ),
            rx.grid(
                _history_card(
                    "FTP over time",
                    "trending-up",
                    FitnessState.ftp_figure,
                    FitnessState.has_ftp_history,
                    "No FTP evaluations yet.",
                ),
                _history_card(
                    "Baseline over time",
                    "gauge",
                    FitnessState.baseline_figure,
                    FitnessState.has_baseline_history,
                    "No baseline evaluations yet.",
                ),
                columns="2",
                spacing="3",
                width="100%",
            ),
            _curves_card(),
            _history_table(),
            _entry_dialog(),
            spacing="4",
            align="start",
            width="100%",
        )
    )
