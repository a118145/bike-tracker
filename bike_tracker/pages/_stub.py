"""Shared body for pages that are navigable but not yet built.

Milestone 1 is a data-first vertical slice: rides are real end to end, and
the other four views exist so navigation is complete and every route returns
200 rather than a 404. Each stub names what will land there, so the page is
honest about being unfinished instead of looking broken.
"""

import reflex as rx


def stub_page(
    title: str,
    icon: str,
    summary: str,
    planned: list[str],
) -> rx.Component:
    """Heading plus a short note on what this view will contain."""
    return rx.vstack(
        rx.hstack(
            rx.icon(icon, size=26, color=rx.color("accent", 10)),
            rx.heading(title, size="7"),
            align="center",
            spacing="3",
        ),
        rx.text(summary, size="2", color_scheme="gray", max_width="42em"),
        rx.card(
            rx.vstack(
                rx.hstack(
                    rx.icon("hard-hat", size=16, color=rx.color("amber", 10)),
                    rx.text("Coming in a later milestone", size="2", weight="bold"),
                    align="center",
                    spacing="2",
                ),
                rx.vstack(
                    *[
                        rx.hstack(
                            rx.icon("dot", size=16, color=rx.color("gray", 9)),
                            rx.text(item, size="2", color_scheme="gray"),
                            align="center",
                            spacing="1",
                        )
                        for item in planned
                    ],
                    spacing="1",
                    align="start",
                ),
                spacing="3",
                align="start",
            ),
            max_width="42em",
        ),
        spacing="4",
        align="start",
        width="100%",
    )
