"""Navigation state.

This is the only state that lives outside ``states/`` — it belongs to the
navigation package because it is meaningless without ``nav_config``.

**Everything here is derived, nothing is stored.** The active route is read
from the router on every render, so deep links, F5, and browser back/forward
all highlight correctly. There are deliberately no event handlers: an earlier
implementation tracked clicks in ``sidebar_state``/``active_route`` vars and
desynced the moment a user navigated by any means other than clicking a link.

Consequence for the UI: nav links are plain ``rx.link(href=...)``. Never add
an ``on_click`` to "keep state in sync" — there is no state to sync.
"""

import reflex as rx

from .nav_config import NAV_ITEMS, resolve_active_route, section_index_for


class NavState(rx.State):
    @rx.var
    def active_route(self) -> str:
        """The nav route matching the current URL. See ``resolve_active_route``."""
        return resolve_active_route(self.router.url.path)

    @rx.var
    def active_section(self) -> int:
        """Index into ``NAV_ITEMS`` of the section containing the active route."""
        return section_index_for(self.active_route)

    @rx.var
    def active_section_label(self) -> str:
        """Section name, shown above the sidebar's submenu."""
        return NAV_ITEMS[self.active_section]["label"]
