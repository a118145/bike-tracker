"""Tags section of the Settings view.

The one place tags are defined (AGENTS.md rule 43). A row per tag with its
pill, its effect and how many rides carry it; a button to add another; one
dialog for create and edit. Ride detail only attaches what exists here.
"""

import reflex as rx

from ..models import TAG_COLORS, TAG_NAME_MAX_LENGTH
from ..states.tags_state import EFFECT_OPTIONS, EffectOption, TagRow, TagsState
from .confirm_dialog import confirm_dialog
from .tags import tag_pill


def _row(tag: TagRow) -> rx.Component:
    return rx.hstack(
        tag_pill(tag.pill, size="2"),
        rx.cond(
            tag.effect_label != "",
            rx.text(tag.effect_label, size="1", color=rx.color("amber", 11)),
        ),
        rx.spacer(),
        rx.text(tag.ride_summary, size="1", color_scheme="gray", white_space="nowrap"),
        rx.icon_button(
            rx.icon("pencil", size=15),
            variant="ghost",
            color_scheme="gray",
            title="Edit tag",
            on_click=TagsState.open_edit(tag.id),
        ),
        rx.icon_button(
            rx.icon("trash-2", size=15),
            variant="ghost",
            color_scheme="red",
            title="Delete tag",
            on_click=TagsState.ask_delete(tag.id),
        ),
        width="100%",
        align="center",
        spacing="3",
        padding_y="0.35em",
        border_bottom=f"1px solid {rx.color('gray', 4)}",
    )


def _error(name: str) -> rx.Component:
    return rx.cond(
        TagsState.form_errors.contains(name),
        rx.text(TagsState.form_errors[name], size="1", color=rx.color("red", 11)),
    )


def _labelled(label: str, control: rx.Component, name: str) -> rx.Component:
    return rx.vstack(
        rx.text(label, size="2", weight="medium"),
        control,
        _error(name),
        spacing="1",
        width="100%",
        align="start",
    )


def _effect_item(option: EffectOption) -> rx.Component:
    return rx.select.item(option.label, value=option.value)


def tag_dialog() -> rx.Component:
    """Create/edit. Uncontrolled fields, remounted on open via ``key``."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(TagsState.dialog_title),
            rx.dialog.description(
                "A tag's effect is what it does; its name is only a label. "
                "Renaming never changes which rides count in analysis.",
                size="2",
                color_scheme="gray",
                margin_bottom="1em",
            ),
            rx.form(
                rx.vstack(
                    _labelled(
                        "Name",
                        rx.input(
                            name="name",
                            default_value=TagsState.form_name,
                            placeholder="Not training",
                            max_length=TAG_NAME_MAX_LENGTH,
                            width="100%",
                        ),
                        "name",
                    ),
                    _labelled(
                        "Colour",
                        rx.select.root(
                            rx.select.trigger(width="100%"),
                            rx.select.content(
                                *[
                                    rx.select.item(
                                        rx.badge(color, color_scheme=color, variant="soft"),
                                        value=color,
                                    )
                                    for color in TAG_COLORS
                                ]
                            ),
                            name="color",
                            default_value=TagsState.form_color,
                        ),
                        "color",
                    ),
                    _labelled(
                        "Effect",
                        rx.select.root(
                            rx.select.trigger(width="100%"),
                            rx.select.content(rx.foreach(EFFECT_OPTIONS, _effect_item)),
                            name="effect",
                            default_value=TagsState.form_effect,
                        ),
                        "effect",
                    ),
                    rx.flex(
                        rx.dialog.close(
                            rx.button("Cancel", type="button", variant="soft", color_scheme="gray"),
                        ),
                        # Not wrapped in rx.dialog.close — the dialog must stay
                        # open when validation fails.
                        rx.button(TagsState.submit_label, type="submit"),
                        spacing="3",
                        justify="end",
                        width="100%",
                        margin_top="0.5em",
                    ),
                    spacing="3",
                    width="100%",
                ),
                on_submit=TagsState.submit_form,
                reset_on_submit=False,
            ),
            max_width="28em",
            key=TagsState.form_key.to_string(),
        ),
        open=TagsState.dialog_open,
        on_open_change=TagsState.set_dialog_open,
    )


def tags_section() -> rx.Component:
    """The whole tags block, dialogs included."""
    return rx.vstack(
        rx.hstack(
            rx.vstack(
                rx.heading("Tags", size="5"),
                rx.text(
                    "Attach these to rides on the ride page. A tag with an effect "
                    "changes how the ride is analysed.",
                    size="2",
                    color_scheme="gray",
                ),
                spacing="1",
                align="start",
            ),
            rx.spacer(),
            rx.button(
                rx.icon("plus", size=16),
                "Add tag",
                variant="soft",
                on_click=TagsState.open_create,
            ),
            width="100%",
            align="center",
        ),
        rx.cond(
            TagsState.has_tags,
            rx.vstack(rx.foreach(TagsState.tags, _row), spacing="0", width="100%"),
            rx.text("No tags defined yet.", size="2", color_scheme="gray"),
        ),
        tag_dialog(),
        confirm_dialog(
            open=TagsState.confirm_open,
            on_open_change=TagsState.set_confirm_open,
            on_confirm=TagsState.confirm_delete,
            title="Delete this tag?",
            description=TagsState.delete_description,
        ),
        spacing="3",
        width="100%",
        align="start",
    )
