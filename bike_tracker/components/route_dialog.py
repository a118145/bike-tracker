"""Rename dialog for a route (masterplan §2.3.1).

A route's name is the only thing about it a user can set — everything else is
derived from the rides that matched it. The name starts auto-generated from
the ride that created the route, which is why it needs editing: the matcher
has already produced both "Evening Ride" and "Evening Ride (2)".

Mounted on **two** pages. The routes list opens it from the pencil on a card;
ride detail opens it straight after creating a route by hand, so the
placeholder "New route" does not survive the interaction. Both drive the same
``RoutesState``, reached from ride detail with ``get_state`` rather than
inheritance.

Uncontrolled input with ``default_value`` plus a ``form_key`` bumped on open,
as in ``bike_dialog`` — the remount is what makes the prefill take.
"""

import reflex as rx

from ..states.routes_state import RoutesState


def route_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Rename route"),
            rx.dialog.description(
                "Routes are named after the ride that first created them. "
                "Give this one a name you will recognise.",
                size="2",
                color_scheme="gray",
                margin_bottom="1em",
            ),
            rx.form(
                rx.vstack(
                    rx.vstack(
                        rx.text("Name", size="2", weight="medium"),
                        rx.input(
                            name="name",
                            default_value=RoutesState.form_name,
                            placeholder="Kraichgau loop",
                            auto_focus=True,
                            width="100%",
                        ),
                        rx.cond(
                            RoutesState.form_errors.contains("name"),
                            rx.text(
                                RoutesState.form_errors["name"],
                                size="1",
                                color=rx.color("red", 11),
                            ),
                        ),
                        spacing="1",
                        width="100%",
                        align="start",
                    ),
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
                        rx.button("Save", type="submit"),
                        spacing="3",
                        justify="end",
                        width="100%",
                        margin_top="0.5em",
                    ),
                    spacing="3",
                    width="100%",
                ),
                on_submit=RoutesState.submit_rename,
                reset_on_submit=False,
            ),
            max_width="26em",
            # Remount on every open so default_value picks up the new prefill.
            key=RoutesState.form_key.to_string(),
        ),
        open=RoutesState.dialog_open,
        on_open_change=RoutesState.set_dialog_open,
    )
