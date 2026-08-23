"""Hammerhead section of the Settings view (masterplan §2.6.2).

Three cards in one, chosen by what actually exists:

1. **Not configured** — the environment has no client id/secret. The card
   explains how to get them and which variables to set. There is deliberately
   no input field for either: a secret typed into a form is a secret on
   screen, in the state, on the wire and in a database backup.
2. **Not connected** — configured but never authorised. Shows the redirect URI
   to register at Hammerhead, an Authorize button, and a paste field.
3. **Connected** — the readout, the poll settings and *Poll now*.

The paste field exists because it is not known whether Hammerhead's redirect
URL field accepts a LAN address. The callback page handles the case where it
does; this handles the case where it does not.
"""

import reflex as rx

from ..states.hammerhead_state import HammerheadState


def _row(label: str, value: rx.Var[str] | str) -> rx.Component:
    return rx.hstack(
        rx.text(label, size="1", color_scheme="gray", min_width="9em"),
        rx.text(value, size="1", weight="medium"),
        spacing="2",
        align="center",
        width="100%",
    )


def _env_var(name: rx.Var[str]) -> rx.Component:
    return rx.code(name, size="1", variant="soft")


def _not_configured() -> rx.Component:
    return rx.vstack(
        rx.callout(
            "This app has no Hammerhead API client yet.",
            icon="key-round",
            color_scheme="amber",
            size="1",
            width="100%",
        ),
        rx.text("Getting a client:", size="2", weight="medium"),
        rx.ordered_list(
            rx.list_item("Sign in at dashboard.hammerhead.io and enable developer settings."),
            rx.list_item("Give the app a name, a description and an SVG logo."),
            rx.list_item("Accept the API licence agreement."),
            rx.list_item("Open the API settings tab for the client id, secret and redirect URLs."),
            size="1",
        ),
        rx.text("Then set these environment variables and restart:", size="2", weight="medium"),
        rx.hstack(
            rx.foreach(HammerheadState.env_vars, _env_var),
            spacing="2",
            wrap="wrap",
            width="100%",
        ),
        spacing="3",
        align="start",
        width="100%",
    )


def _not_connected() -> rx.Component:
    return rx.vstack(
        rx.text(
            "Authorize this app against your Hammerhead account. It asks for "
            "read access to activities and nothing else.",
            size="2",
            color_scheme="gray",
        ),
        _row("Redirect URI", HammerheadState.redirect_uri),
        rx.text(
            "That URI must be registered in your Hammerhead API settings.",
            size="1",
            color_scheme="gray",
        ),
        rx.button(
            rx.icon("external-link", size=15),
            "Authorize with Hammerhead",
            on_click=HammerheadState.start_authorize,
        ),
        rx.divider(),
        rx.text(
            "If the browser did not come back to this app, paste the code — or "
            "the whole URL you landed on — here.",
            size="1",
            color_scheme="gray",
        ),
        rx.hstack(
            rx.input(
                placeholder="code, or https://…?code=…&state=…",
                value=HammerheadState.paste_value,
                on_change=HammerheadState.set_paste_value,
                width="100%",
            ),
            rx.button(
                "Connect",
                on_click=HammerheadState.submit_code,
                loading=HammerheadState.exchanging,
            ),
            spacing="2",
            width="100%",
        ),
        spacing="3",
        align="start",
        width="100%",
    )


def _last_poll() -> rx.Component:
    """One line of history. A run table would be the log's job, not the UI's."""
    return rx.vstack(
        rx.hstack(
            rx.icon(
                rx.cond(HammerheadState.last_poll_ok, "circle-check", "circle-alert"),
                size=15,
                color=rx.cond(
                    HammerheadState.last_poll_ok, rx.color("grass", 10), rx.color("red", 10)
                ),
            ),
            rx.text(HammerheadState.last_poll, size="1", weight="medium"),
            rx.text(HammerheadState.last_poll_summary, size="1", color_scheme="gray"),
            spacing="2",
            align="center",
        ),
        rx.cond(
            HammerheadState.last_poll_error != "",
            rx.text(HammerheadState.last_poll_error, size="1", color=rx.color("red", 11)),
        ),
        spacing="1",
        align="start",
        width="100%",
    )


def _connected() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.badge("Connected", color_scheme="grass", variant="soft"),
            rx.spacer(),
            rx.button(
                "Disconnect",
                variant="soft",
                color_scheme="red",
                size="1",
                on_click=HammerheadState.disconnect,
            ),
            width="100%",
            align="center",
        ),
        _row("Account", HammerheadState.account_user_id),
        _row("Scopes", HammerheadState.scopes),
        _row("Connected", HammerheadState.connected_since),
        _row("Token expires", HammerheadState.token_expires),
        rx.divider(),
        rx.hstack(
            rx.text("Poll automatically", size="2", weight="medium"),
            rx.spacer(),
            rx.switch(
                checked=HammerheadState.enabled,
                on_change=HammerheadState.set_enabled,
            ),
            width="100%",
            align="center",
        ),
        rx.hstack(
            rx.text("Every", size="2", color_scheme="gray"),
            rx.input(
                default_value=HammerheadState.interval_min.to_string(),
                on_blur=HammerheadState.set_interval,
                type="number",
                min=1,
                width="6em",
                size="1",
            ),
            rx.text("minutes", size="2", color_scheme="gray"),
            spacing="2",
            align="center",
        ),
        rx.hstack(
            rx.text("Fetch rides from", size="2", color_scheme="gray"),
            rx.input(
                default_value=HammerheadState.backfill_start,
                on_blur=HammerheadState.set_backfill_start,
                type="date",
                size="1",
            ),
            spacing="2",
            align="center",
        ),
        rx.cond(
            HammerheadState.next_start_date != "",
            rx.text(
                "Next poll asks for activities from " + HammerheadState.next_start_date + ".",
                size="1",
                color_scheme="gray",
            ),
            rx.text(
                "Next poll asks for the whole account.",
                size="1",
                color_scheme="gray",
            ),
        ),
        rx.divider(),
        rx.hstack(
            rx.button(
                rx.icon("refresh-cw", size=15),
                HammerheadState.poll_button_label,
                on_click=HammerheadState.poll_now,
                loading=HammerheadState.polling,
            ),
            rx.spacer(),
            width="100%",
            align="center",
        ),
        rx.cond(HammerheadState.has_last_poll, _last_poll()),
        spacing="3",
        align="start",
        width="100%",
    )


def hammerhead_section() -> rx.Component:
    return rx.vstack(
        rx.vstack(
            rx.heading("Hammerhead", size="5"),
            rx.text(
                "Rides are fetched from the Karoo dashboard as FIT files and "
                "imported the same way an uploaded file is.",
                size="2",
                color_scheme="gray",
            ),
            spacing="1",
            align="start",
        ),
        rx.card(
            rx.cond(
                HammerheadState.configured,
                rx.cond(HammerheadState.connected, _connected(), _not_connected()),
                _not_configured(),
            ),
            width="100%",
        ),
        spacing="3",
        width="100%",
        align="start",
    )
