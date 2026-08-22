"""Navigation config — single source of truth for the menu.

One schema serves all three layout variants:

* **top only**  — renders ``NAV_ITEMS`` labels, ignores ``sub_items``
* **side only** — renders a flat list (a section's ``sub_items`` if it has
  any, otherwise the section itself)
* **both**      — top bar renders sections, sidebar renders the active
  section's ``sub_items``

Adding a page means adding one entry here plus one ``@rx.page`` decorator.
No UI file changes.
"""

from typing import TypedDict

from . import routes


class SubNavItem(TypedDict):
    label: str
    icon: str
    route: str


class NavItem(TypedDict, total=False):
    label: str
    icon: str
    route: str
    sub_items: list[SubNavItem]  # optional — omitted in single-surface layouts


# Icons are lucide names: https://lucide.dev/icons
NAV_ITEMS: list[NavItem] = [
    {"label": "Dashboard", "icon": "layout-dashboard", "route": routes.HOME_ROUTE},
    {"label": "Routes", "icon": "route", "route": routes.ROUTES_ROUTE},
    {"label": "Rides", "icon": "bike", "route": routes.RIDES_ROUTE},
    {"label": "Fitness", "icon": "heart-pulse", "route": routes.FITNESS_ROUTE},
    {"label": "Bikes", "icon": "wrench", "route": routes.BIKES_ROUTE},
]


def _collect_routes() -> list[str]:
    """Every navigable route, longest first (so prefix matching picks the
    most specific entry without needing to sort at request time)."""
    collected: list[str] = []
    for item in NAV_ITEMS:
        collected.append(item["route"])
        for sub in item.get("sub_items", []):
            collected.append(sub["route"])
    return sorted(set(collected), key=len, reverse=True)


ALL_ROUTES: list[str] = _collect_routes()


def resolve_active_route(path: str) -> str:
    """Map a URL path to the nav route that should be highlighted.

    Longest match wins, and matching is *segment-aware*, which rules out two
    bugs a plain ``startswith`` would introduce:

    * ``"/"`` is a prefix of every path, so it only ever matches exactly.
    * ``/data-export`` must not match ``/data`` — hence the ``route + "/"``
      test rather than a bare prefix check.

    The segment rule is also what makes dynamic pages work: ``/data/5``
    resolves to ``/data`` and lights up its parent entry.

    Kept a plain function so it is unit-testable without a Reflex runtime.
    """
    normalised = path.rstrip("/") or "/"

    best = ""
    for route in ALL_ROUTES:
        if route == "/":
            continue  # never wins by prefix; it is the fallback below
        matches = normalised == route or normalised.startswith(route + "/")
        if matches and len(route) > len(best):
            best = route
    return best or "/"


def section_index_for(route: str) -> int:
    """Index into NAV_ITEMS of the section owning ``route``."""
    for i, item in enumerate(NAV_ITEMS):
        if item["route"] == route:
            return i
        if any(sub["route"] == route for sub in item.get("sub_items", [])):
            return i
    return 0


def label_for_route(route: str) -> str:
    """The menu label a route is shown under.

    Sub-items win over sections, which is what makes this correct in all three
    layouts: in ``both`` the sub-items are the sidebar, in ``side`` they are
    the flat list, and in ``top`` there are none — so this resolves to the
    sidebar entry wherever there is one and to the top-bar entry otherwise.

    Used for "Back to X" links on pages that have no menu entry of their own,
    so the label always names something the user can actually see in the nav.
    Hard-coding that string instead is how it ends up saying "Back to items"
    in an app whose menu says "Table".
    """
    for item in NAV_ITEMS:
        for sub in item.get("sub_items", []):
            if sub["route"] == route:
                return sub["label"]
    for item in NAV_ITEMS:
        if item["route"] == route:
            return item["label"]
    return ""
