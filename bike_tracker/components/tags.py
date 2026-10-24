"""Tag pills, drawn the same on every page that shows a ride's tags.

An excluding tag carries an eye-off icon and says so on hover, so a dimmed
ride explains itself wherever it is met (AGENTS.md rules 42 and 43).
"""

import reflex as rx

from ..states.tags_state import TagPill

EXCLUDES_TITLE = "Excludes this ride from analysis"


def tag_pill(
    pill: TagPill,
    *,
    size: str = "1",
    on_remove: rx.EventHandler | None = None,
    disabled: rx.Var[bool] | bool = False,
) -> rx.Component:
    """One tag. With ``on_remove``, it gets an ``x`` that takes it off the ride."""
    children: list[rx.Component] = [
        rx.cond(pill.excludes, rx.icon("eye-off", size=12)),
        rx.text(pill.name),
    ]
    if on_remove is not None:
        children.append(
            rx.icon_button(
                rx.icon("x", size=12),
                size="1",
                variant="ghost",
                color_scheme=pill.color,
                radius="full",
                title="Remove tag",
                disabled=disabled,
                on_click=on_remove(pill.id),
                # Ghost buttons carry a negative margin to line up optically
                # with text; inside a badge that eats into the padding.
                margin="0",
                height="1.1em",
                width="1.1em",
            )
        )
    return rx.badge(
        *children,
        variant="soft",
        color_scheme=pill.color,
        radius="full",
        size=size,
        title=rx.cond(pill.excludes, EXCLUDES_TITLE, ""),
        gap="0.3em",
        align_items="center",
    )
