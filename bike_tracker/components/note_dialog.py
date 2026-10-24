"""The ride's note: a header button and the dialog it opens (AGENTS.md rule 44).

The note explains, tags act. Nothing here feeds a metric — a faulty HR strap
is *described* in the note and *acted on* by a tag.

The button is the note's only place on the page. It is solid when the ride
has a note and shows the note on hover, so a glance at the header answers
"did I write something about this ride?" without opening anything.

Fields are *uncontrolled*: they carry ``default_value`` and arrive as a dict
in ``on_submit`` for ``parse_form``. ``key=`` remounts the content on every
open so the defaults are re-read. The quick fields are selects rather than
segmented controls because a Radix select submits through a hidden native
``<select>``, and a segmented control takes no part in a form.
"""

import reflex as rx

from ..models.ride import BREATHING_LIMIT_MAX, RPE_MAX, SLEEP_QUALITY_MAX
from ..states.ride_detail_state import NONE_OPTION, RideDetailState

# Words for the ends and the middle of each scale; the other steps are bare
# numbers. Borg CR10 anchors for RPE.
_SLEEP_LABELS = {1: "very poor", 3: "okay", 5: "very good"}
_RPE_LABELS = {1: "very light", 3: "moderate", 5: "hard", 7: "very hard", 10: "maximal"}
_BREATHING_LABELS = {1: "not limiting", 3: "noticeable", 5: "the limiter"}


def _error(name: str) -> rx.Component:
    return rx.cond(
        RideDetailState.note_errors.contains(name),
        rx.text(RideDetailState.note_errors[name], size="1", color=rx.color("red", 11)),
    )


def _scale(
    label: str,
    name: str,
    top: int,
    words: dict[int, str],
    default_value: rx.Var[str] | str,
) -> rx.Component:
    """A 1..top select with a "—" entry for "not recorded"."""
    items = [rx.select.item("—", value=NONE_OPTION)] + [
        rx.select.item(f"{n} — {words[n]}" if n in words else str(n), value=str(n))
        for n in range(1, top + 1)
    ]
    return rx.vstack(
        rx.text(label, size="2", weight="medium"),
        rx.select.root(
            rx.select.trigger(width="100%"),
            rx.select.content(*items),
            name=name,
            default_value=default_value,
        ),
        _error(name),
        spacing="1",
        width="100%",
        align="start",
    )


def note_marker(summary: rx.Var[str]) -> rx.Component:
    """A small note icon for a list card or table row; the note on hover.

    ``summary`` is the first line plus the quick fields
    (``models.ride.note_summary``). Renders nothing when it is empty, which is
    how "no note" travels in a view model.
    """
    return rx.cond(
        summary != "",
        rx.tooltip(
            rx.icon("notebook-pen", size=14, color=rx.color("gray", 10)),
            content=summary,
            white_space="pre-line",
            max_width="24em",
        ),
    )


def note_button() -> rx.Component:
    """Opens the dialog. Solid with a tooltip when the ride has a note.

    Disabled while ``stale``: on a client-side navigation it would otherwise
    open the previous ride's note, and saving it would write to the ride being
    loaded.
    """

    def button(variant: str, title: str = "") -> rx.Component:
        return rx.icon_button(
            rx.icon("notebook-pen", size=16),
            variant=variant,
            color_scheme="gray",
            disabled=RideDetailState.stale,
            on_click=RideDetailState.open_note,
            title=title,
            aria_label="Ride note",
        )

    return rx.cond(
        RideDetailState.has_note & ~RideDetailState.stale,
        rx.tooltip(
            button("solid"),
            content=RideDetailState.note_tooltip,
            # The tooltip keeps the note's line breaks.
            white_space="pre-line",
            max_width="24em",
        ),
        button("soft", "Add note"),
    )


def note_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Ride note"),
            rx.dialog.description(
                "For you and for the coach. A note explains a ride; to change how "
                "it is analysed, give it a tag.",
                size="2",
                color_scheme="gray",
                margin_bottom="1em",
            ),
            rx.form(
                rx.vstack(
                    rx.vstack(
                        rx.text_area(
                            name="text",
                            default_value=RideDetailState.note_text,
                            placeholder="How did it go? Anything unusual?",
                            rows="6",
                            width="100%",
                        ),
                        _error("text"),
                        spacing="1",
                        width="100%",
                    ),
                    rx.grid(
                        _scale(
                            "Sleep",
                            "sleep_quality",
                            SLEEP_QUALITY_MAX,
                            _SLEEP_LABELS,
                            RideDetailState.note_sleep,
                        ),
                        _scale("RPE", "rpe", RPE_MAX, _RPE_LABELS, RideDetailState.note_rpe),
                        _scale(
                            "Breathing limit",
                            "perceived_breathing_limit",
                            BREATHING_LIMIT_MAX,
                            _BREATHING_LABELS,
                            RideDetailState.note_breathing,
                        ),
                        columns="3",
                        spacing="3",
                        width="100%",
                    ),
                    rx.flex(
                        rx.button(
                            "Clear",
                            type="button",
                            variant="soft",
                            color_scheme="red",
                            disabled=~RideDetailState.has_note,
                            on_click=RideDetailState.clear_note,
                        ),
                        rx.spacer(),
                        rx.dialog.close(
                            rx.button("Cancel", type="button", variant="soft", color_scheme="gray"),
                        ),
                        # Not wrapped in rx.dialog.close — the dialog must stay
                        # open when validation fails.
                        rx.button("Save", type="submit"),
                        spacing="3",
                        width="100%",
                        margin_top="0.5em",
                    ),
                    spacing="3",
                    width="100%",
                ),
                on_submit=RideDetailState.submit_note,
                reset_on_submit=False,
            ),
            max_width="34em",
            key=RideDetailState.note_form_key.to_string(),
        ),
        open=RideDetailState.note_open,
        on_open_change=RideDetailState.set_note_open,
    )
