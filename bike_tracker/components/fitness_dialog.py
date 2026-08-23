"""FTP / baseline evaluation dialog (masterplan §2.4.2).

Three choices, in order: **what** to evaluate, **how** to pick the window, and
then the window itself. The third step changes shape with the second, which is
why the methods are radio-like rather than a wizard — the user can see all
three options at once and switch without losing the others.

Opened from the Fitness dropdown in the ride-detail header, or by clicking a
saved evaluation's badge on the Power card. Both paths land in
``RideDetailState``, which already holds the ride's decoded streams in memory,
so every slider move is arithmetic rather than a database round-trip.

**Sliders commit, they do not stream.** Every control binds
``on_value_commit``, which fires when a thumb is released.  ``on_change`` fires
continuously during a drag and would re-serialise the whole chart figure on
every mouse move.
"""

import reflex as rx

from ..components.ride_chart import static_chart
from ..models import EVAL_BASELINE, EVAL_FTP, METHOD_BEST_FIT, METHOD_LAP, METHOD_WINDOW
from ..states.ride_detail_state import RideDetailState


def _section(title: str, hint: str, *children: rx.Component) -> rx.Component:
    return rx.vstack(
        rx.text(title, size="2", weight="medium"),
        rx.text(hint, size="1", color_scheme="gray"),
        *children,
        spacing="2",
        width="100%",
        align="start",
    )


def _type_choice() -> rx.Component:
    return _section(
        "What to evaluate",
        "FTP is 95% of the best 20 minutes. Baseline is watts per heartbeat.",
        rx.segmented_control.root(
            rx.segmented_control.item("FTP", value=EVAL_FTP),
            rx.segmented_control.item("Baseline", value=EVAL_BASELINE),
            value=RideDetailState.eval_type,
            on_change=RideDetailState.set_eval_type,
            width="100%",
        ),
    )


def _method_choice() -> rx.Component:
    return _section(
        "How to pick the window",
        "Best fit slides a 20-minute window around your guess; it is FTP only.",
        rx.segmented_control.root(
            rx.segmented_control.item("Window", value=METHOD_WINDOW),
            rx.segmented_control.item("Lap", value=METHOD_LAP),
            rx.segmented_control.item("Best fit", value=METHOD_BEST_FIT),
            value=RideDetailState.eval_method,
            on_change=RideDetailState.set_eval_method,
            width="100%",
        ),
    )


def _window_input() -> rx.Component:
    """One slider, two thumbs — §2.4.2's "two sliders on the same track"."""
    return _section(
        "Start and end",
        "Percent of ride time.",
        rx.slider(
            default_value=RideDetailState.eval_window,
            min=0,
            max=100,
            step=0.5,
            on_value_commit=RideDetailState.set_eval_window,
            width="100%",
        ),
    )


def _lap_input() -> rx.Component:
    return _section(
        "Lap",
        "The evaluation covers exactly that lap.",
        rx.select.root(
            rx.select.trigger(placeholder="Lap", width="100%"),
            rx.select.content(
                rx.foreach(
                    RideDetailState.eval_lap_options,
                    lambda option: rx.select.item(option.label, value=option.value),
                )
            ),
            value=RideDetailState.eval_lap,
            on_change=RideDetailState.set_eval_lap,
            width="100%",
        ),
    )


def _best_fit_input() -> rx.Component:
    return _section(
        "Roughly where was the effort?",
        "The 20-minute window slides 20 minutes either side of this point.",
        rx.slider(
            default_value=RideDetailState.eval_guess,
            min=0,
            max=100,
            step=1,
            on_value_commit=RideDetailState.set_eval_guess,
            width="100%",
        ),
    )


def _window_chart() -> rx.Component:
    """Elevation, heart rate and power, with the evaluated window shaded.

    The dialog covers the page's own chart and dims what is left of it, so the
    shading has to be in here to be seen at all. Fixed channels and no hover
    bridge — this chart reports, it does not steer anything.
    """
    return rx.box(
        static_chart(RideDetailState.eval_chart_figure),
        width="100%",
        border_radius="0.4em",
        overflow="hidden",
    )


def _result() -> rx.Component:
    """The live preview. Recomputed on every commit, saved only on Save."""
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.text("Result", size="1", color_scheme="gray"),
                rx.spacer(),
                rx.text(RideDetailState.eval_window_label, size="1", color_scheme="gray"),
                width="100%",
                align="center",
            ),
            rx.heading(RideDetailState.eval_result_text, size="6"),
            rx.cond(
                RideDetailState.eval_detail_text != "",
                rx.text(RideDetailState.eval_detail_text, size="1", color_scheme="gray"),
            ),
            rx.cond(
                RideDetailState.eval_error != "",
                rx.text(RideDetailState.eval_error, size="1", color=rx.color("red", 11)),
            ),
            spacing="1",
            width="100%",
            align="start",
        ),
        width="100%",
    )


def fitness_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(RideDetailState.eval_dialog_title),
            rx.dialog.description(
                "The saved entry is dated to the start of this ride, so it "
                "applies to this ride and to every later one until the next "
                "evaluation.",
                size="2",
                color_scheme="gray",
                margin_bottom="1em",
            ),
            rx.vstack(
                _type_choice(),
                rx.divider(),
                _method_choice(),
                rx.divider(),
                rx.cond(RideDetailState.eval_method_is_window, _window_input()),
                rx.cond(RideDetailState.eval_method_is_lap, _lap_input()),
                rx.cond(RideDetailState.eval_method_is_best_fit, _best_fit_input()),
                _window_chart(),
                _result(),
                rx.flex(
                    rx.dialog.close(
                        rx.button("Cancel", type="button", variant="soft", color_scheme="gray"),
                    ),
                    # Deliberately NOT wrapped in rx.dialog.close — the dialog
                    # must stay open when there is nothing valid to save.
                    rx.button(
                        RideDetailState.eval_submit_label,
                        type="button",
                        disabled=~RideDetailState.eval_has_result,
                        on_click=RideDetailState.save_evaluation,
                    ),
                    spacing="3",
                    justify="end",
                    width="100%",
                    margin_top="0.5em",
                ),
                spacing="3",
                width="100%",
            ),
            max_width="32em",
            # Remount on open so the sliders pick up their restored positions.
            key=RideDetailState.eval_form_key.to_string(),
        ),
        open=RideDetailState.eval_open,
        on_open_change=RideDetailState.set_eval_open,
    )


def fitness_menu() -> rx.Component:
    """Header dropdown that opens the dialog (Arne's change to §2.4.2).

    ``rx.menu`` rather than a third ``rx.select`` beside Bike and Route: these
    are actions, not a value to hold, and a select would have to be reset after
    every use. Items disable themselves when the ride lacks the channel the
    evaluation needs — an FTP test without a power meter has nothing to read.
    """
    return rx.hstack(
        rx.icon("heart-pulse", size=15, color=rx.color("gray", 10)),
        rx.menu.root(
            rx.menu.trigger(
                rx.button(
                    "Fitness",
                    rx.icon("chevron-down", size=14),
                    variant="soft",
                    color_scheme="gray",
                ),
            ),
            rx.menu.content(
                rx.menu.item(
                    "Evaluate FTP...",
                    disabled=~RideDetailState.can_evaluate_ftp,
                    on_click=RideDetailState.open_evaluation(EVAL_FTP),
                ),
                rx.menu.item(
                    "Evaluate baseline...",
                    disabled=~RideDetailState.can_evaluate_baseline,
                    on_click=RideDetailState.open_evaluation(EVAL_BASELINE),
                ),
            ),
        ),
        align="center",
        spacing="2",
    )
