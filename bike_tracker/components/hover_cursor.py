"""Chart-to-map hover cursor: a dot that follows the mouse along the track.

Hovering the y-t chart tells you *when* something happened; this makes it also
tell you *where*. It is the one piece of custom JavaScript in the project, and
the reason for it is latency: a mouse moving across the chart fires hover
events continuously, and an ordinary Reflex handler would round-trip each one
over the websocket *and* re-serialize the entire map figure — tens of
kilobytes per mouse movement.

**How it stays client-side.** Handing a ``FunctionVar`` to ``on_hover``
compiles to a direct call —
``onHover:((_e0) => (window.__btHover(extractPoints(_e0?.points))))`` — which
bypasses Reflex's event queue and socket entirely. Nothing reaches the server.

**Why a Component subclass rather than an asset file.** ``Plotly`` is a
CommonJS module bundled into the page, so it never lands on ``window`` and a
plain ``<script>`` cannot reach ``Plotly.restyle``. ``add_imports`` plus
``add_custom_code`` put the handlers in the page's module scope where the
import is in scope. This is the same mechanism Reflex's own plotly wrapper
uses to inject its ``extractPoints`` helper.

No binding step is needed, and therefore none of the usual remount fragility:
the handlers are module-level constants evaluated at page load, and Reflex
wires them through react-plotly's own ``onHover`` prop, so they survive ride
switches and lap selection without re-attaching anything.
"""

import reflex as rx
from reflex.utils.imports import ImportDict, ImportVar

# Trace names the map figure uses, matched in JS by name rather than index —
# the trace count changes when a lap is selected, so positions are not stable.
CURSOR_TRACE = "__cursor"
CURSOR_TRACK_TRACE = "__cursor_track"

# Class on the map's plotly div. react-plotly renders `className` onto the
# graph div itself; it ignores `id`, so this is the handle that works.
MAP_CLASS = "bt-map"

_HOVER_JS = f"""
// Chart-to-map hover cursor. See components/hover_cursor.py.
const btMapDiv = () => document.querySelector(".{MAP_CLASS}");

const btTraceIndex = (gd, name) =>
    gd && gd.data ? gd.data.findIndex((t) => t && t.name === name) : -1;

window.__btHover = (points) => {{
    const gd = btMapDiv();
    if (!gd || !gd.data || !points || !points.length) return;

    const i = points[0].pointIndex;
    if (i === null || i === undefined) return;

    const track = btTraceIndex(gd, "{CURSOR_TRACK_TRACE}");
    const cursor = btTraceIndex(gd, "{CURSOR_TRACE}");
    if (track < 0 || cursor < 0) return;

    const lat = gd.data[track].lat;
    const lon = gd.data[track].lon;
    if (!lat || i >= lat.length) return;

    // A sample with no GPS fix has no position to point at; leaving the dot
    // where it was is better than dropping it at (0, 0).
    if (lat[i] === null || Number.isNaN(lat[i])) return;

    Plotly.restyle(gd, {{lat: [[lat[i]]], lon: [[lon[i]]]}}, [cursor]);
}};

window.__btUnhover = () => {{
    const gd = btMapDiv();
    if (!gd || !gd.data) return;
    const cursor = btTraceIndex(gd, "{CURSOR_TRACE}");
    if (cursor < 0) return;
    Plotly.restyle(gd, {{lat: [[]], lon: [[]]}}, [cursor]);
}};
"""


class HoverCursorBridge(rx.Component):
    """Renders nothing; installs the hover handlers in the page's module scope.

    A bare ``rx.Component`` with no tag compiles to an empty node, so mounting
    it costs nothing in the DOM — the point is purely its ``add_imports`` and
    ``add_custom_code``.
    """

    def add_imports(self) -> ImportDict:
        """Pull Plotly into this page so the custom code below can call it.

        The ``dist/plotly`` subpath, not the package root. The root entry is
        CommonJS and its Vite interop shim references ``global``, which does
        not exist in a browser module — importing it blanks the whole page
        with ``ReferenceError: global is not defined``. ``dist/plotly`` is the
        browser build, and it is the exact specifier react-plotly.js itself
        imports, so the two share one copy rather than bundling plotly twice.
        """
        return {"plotly.js/dist/plotly": ImportVar(tag="Plotly", is_default=True)}

    def add_custom_code(self) -> list[str]:
        return [_HOVER_JS]


def hover_cursor_bridge() -> rx.Component:
    """Mount once on any page that pairs a chart with a map."""
    return HoverCursorBridge.create()
