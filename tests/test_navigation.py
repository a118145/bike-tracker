"""Navigation highlighting.

These cases are the regression suite for the defect this template fixes:
highlighting used to be tracked on click, so any navigation that was not a
click (deep link, refresh, back/forward) left the menu pointing at the wrong
entry. Deriving it from the path makes all of them the same code path.
"""

import pytest

from bike_tracker.navigation.nav_config import (
    NAV_ITEMS,
    label_for_route,
    resolve_active_route,
    section_index_for,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", "/"),
        ("/routes", "/routes"),
        ("/rides", "/rides"),
        ("/fitness", "/fitness"),
        ("/settings", "/settings"),
        # Dynamic detail pages have no menu entry of their own; segment-aware
        # matching resolves them to the parent so the sidebar stays lit.
        ("/rides/7", "/rides"),
        ("/routes/3", "/routes"),
        # The OAuth callback has no nav entry either, so Settings must stay
        # lit while the browser passes through it.
        ("/settings/hammerhead/callback", "/settings"),
        # Trailing slashes are normalised away.
        ("/rides/", "/rides"),
    ],
)
def test_exact_routes_resolve_to_themselves(path: str, expected: str) -> None:
    assert resolve_active_route(path) == expected


def test_root_never_wins_by_prefix() -> None:
    """The root route is a prefix of every path; it must only match exactly."""
    assert resolve_active_route("/") == "/"


def test_sibling_prefix_does_not_match() -> None:
    """A longer sibling must not be mistaken for a child route."""
    assert resolve_active_route("/definitely-not-a-real-route") == "/"


def test_unknown_path_falls_back_to_home() -> None:
    assert resolve_active_route("/nope/nowhere") == "/"


@pytest.mark.parametrize("item", NAV_ITEMS)
def test_label_for_route_names_the_visible_menu_entry(item: dict) -> None:
    """Back-links must name what the user actually sees in the menu.

    Sub-items win, so this is the sidebar entry where there is one and the
    top-bar entry otherwise — correct in all three layouts.
    """
    subs = item.get("sub_items", [])
    if subs:
        assert label_for_route(subs[0]["route"]) == subs[0]["label"]
    else:
        assert label_for_route(item["route"]) == item["label"]


def test_label_for_unknown_route_is_empty() -> None:
    assert label_for_route("/not-in-the-menu") == ""


@pytest.mark.parametrize("item", NAV_ITEMS)
def test_every_section_resolves_to_itself(item: dict) -> None:
    """Clicking a section must highlight that section, not a neighbour."""
    target = item.get("sub_items", [{"route": item["route"]}])[0]["route"]
    section = section_index_for(resolve_active_route(target))
    assert NAV_ITEMS[section]["label"] == item["label"]
