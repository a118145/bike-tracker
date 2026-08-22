"""Generic confirmation dialog for destructive actions.

Takes the copy and an event handler — it knows nothing about items, so any
state can reuse it.
"""

import reflex as rx
from reflex.event import EventType
from reflex.vars import Var


def confirm_dialog(
    *,
    open: Var[bool] | bool,
    on_open_change: EventType[bool],
    on_confirm: EventType[()],
    title: str = "Are you sure?",
    description: str = "This action cannot be undone.",
    confirm_label: str = "Delete",
    cancel_label: str = "Cancel",
    confirm_color: str = "red",
) -> rx.Component:
    """A yes/no dialog.

    Args:
        open: State var holding whether the dialog is visible.
        on_open_change: Handler taking a bool — also fires on Escape and on
            click-outside, which is why the open state must live in the state
            rather than being managed by Radix internally.
        on_confirm: Fired when the user accepts.
    """
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title(title),
            rx.alert_dialog.description(description),
            rx.flex(
                rx.alert_dialog.cancel(
                    rx.button(cancel_label, variant="soft", color_scheme="gray"),
                ),
                rx.alert_dialog.action(
                    rx.button(
                        confirm_label,
                        color_scheme=confirm_color,
                        on_click=on_confirm,
                    ),
                ),
                spacing="3",
                margin_top="1em",
                justify="end",
            ),
            max_width="26em",
        ),
        open=open,
        on_open_change=on_open_change,
    )
