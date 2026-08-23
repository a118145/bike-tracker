"""Map rendering — the only module that knows which map library is in use.

Two very different jobs, deliberately kept behind one small interface so a
later library swap touches this file and nothing else:

* :func:`route_map` — one full-page interactive map, Plotly's MapLibre-backed
  ``Scattermap`` on a token-free OpenStreetMap style.
* :func:`route_preview_svg` — a plain inline ``<svg>`` polyline for list
  cards. No map instance and no tile requests: a Rides list with 40 cards
  would otherwise mount 40 map widgets and fire hundreds of tile fetches for
  thumbnails a couple of centimetres wide.

**Why Plotly and not ``rxe.map``.** reflex-enterprise's Leaflet wrapper is
proprietary and its use is tied to a Reflex account. The pipeline brief
mandates a self-contained image on a homeserver with no inbound ports, and a
licence check inside that container is a risk not worth carrying here. Plotly
is already required for the ride-detail chart, so this is one frontend
dependency instead of two. The cost is real and accepted: no ``fit_bounds()``
(hence :func:`~..metrics.geo.bounds_to_center_zoom`) and coarser polyline
styling.

Trace naming note: ``go.Scattermap`` + ``layout.map`` are the current spelling;
``Scattermapbox`` + ``layout.mapbox`` still exist but are deprecated. Verified
against the installed plotly 6.9.0 and plotly.js 3.7.0.
"""

from dataclasses import dataclass, field

import plotly.graph_objects as go
import reflex as rx

from ..metrics import PREVIEW_VIEWBOX, Point, bounds_to_center_zoom
from .hover_cursor import CURSOR_TRACE, CURSOR_TRACK_TRACE, MAP_CLASS

# Token-free MapLibre styles. "open-street-map" needs no account of any kind;
# the carto styles are the muted variants used when the map should recede
# behind the track.
STYLE_LIGHT = "carto-positron"
STYLE_DARK = "carto-darkmatter"
STYLE_STREETS = "open-street-map"

# Hundreds of rides at ~10k raw points each will stall any renderer, so the
# heat map caps how many tracks it draws. Simplification upstream is what
# makes the ones it does draw cheap.
MAX_HEATMAP_TRACES = 250

# Lap-boundary markers, matching the purple of the lap bars under the chart so
# the two controls read as the same thing seen two ways (docs/image_3.png).
LAP_MARKER_COLOUR = "#a252cc"
LAP_MARKER_SIZE = 20

# The dot that tracks the chart cursor.
CURSOR_COLOUR = "#ef4444"

# Heat-map lines. One shared constant because the route detail map and the
# dashboard's all-routes map must read as the same kind of thing.
HEAT_COLOUR = "#2563eb"

# The ride track: full colour when the whole ride is in scope, greyed when a
# lap is highlighted on top of it.
TRACK_COLOUR = "#2563eb"
TRACK_DIMMED = "#94a3b8"

# Fixed uids for the two traces the hover cursor owns. Everything else names
# its own; see MapTrace.uid for why they exist at all.
CURSOR_TRACK_UID = "cursor-track"
CURSOR_UID = "cursor"


def heat_opacity(ride_count: int) -> float:
    """Per-trace alpha for a heat map of ``ride_count`` rides.

    A constant cannot serve both ends of the range. At 0.15 a single ride is
    nearly invisible; at 0.45 ten rides saturate to solid everywhere and the
    deviations the map exists to show disappear. This keeps a lone strand
    legible against the shared spine at every count.

    One ride is drawn **solid**: with nothing to stack against, transparency
    only makes it look washed out.
    """
    if ride_count <= 1:
        return 1.0
    return max(0.12, min(0.55, 1.6 / ride_count))


@dataclass
class MapTrace:
    """One polyline to draw on a map.

    ``uid`` is required and load-bearing. Plotly matches traces across a
    re-plot by uid, and when a figure supplies none it falls back to matching
    *by position* (``plots.js::getTraceUids``): index 0 keeps index 0's
    identity, and any surplus trace gets a freshly minted random uid. A figure
    whose trace count changes therefore reads to Plotly as one trace born and
    one destroyed, which on a MapLibre subplot means ``addSource`` and
    ``removeLayer`` — the two calls that throw "Style is not done loading."
    and "Map error." while the style is not settled, permanently poisoning the
    graph div. Naming every trace makes a re-plot a pure data update instead.
    """

    uid: str
    points: list[Point]
    name: str = ""
    color: str = "#2563eb"
    width: float = 3.0
    opacity: float = 1.0
    hover: bool = True


@dataclass
class MapMarker:
    """A pin on the map — one per lap boundary.

    ``label`` is **not drawn on the map**; it reaches the reader through the
    hover tooltip only, and that is deliberate. Plotly gives any ``scattermap``
    trace carrying text a MapLibre symbol layer with
    ``'icon-image': '{symbol}-15'`` hard-coded
    (``scattermap/convert.js``), and with the default ``circle`` symbol that id
    evaluates to the literal ``-15``. No style has such an image, so MapLibre
    fires ``styleimagemissing`` and Plotly's handler (``plots/map/map.js``)
    fetches it from ``unpkg.com`` with ``crossOrigin='Anonymous'`` — which
    unpkg does not answer, leaving a CORS error and a failed request in the
    console on every single map load.

    So ``docs/image_3.png`` shows a lap number inside the pin and this does
    not. That divergence is the fix, not an oversight. See AGENTS.md rule 22.
    """

    uid: str
    point: Point
    label: str = ""
    hover: str = ""
    color: str = LAP_MARKER_COLOUR
    size: int = LAP_MARKER_SIZE


@dataclass
class MapFigure:
    """A built figure plus the viewport it was framed with."""

    figure: go.Figure
    center_lat: float = 0.0
    center_lon: float = 0.0
    zoom: float = 1.0
    empty: bool = True
    traces: list[MapTrace] = field(default_factory=list)


def _check_unique_uids(traces: list[MapTrace], markers: list[MapMarker] | None) -> None:
    """Fail loudly on a duplicate uid rather than losing a trace silently.

    Plotly keeps one entry per uid, so two traces sharing one means the second
    overwrites the first on the map with no error anywhere.
    """
    uids = [t.uid for t in traces[:MAX_HEATMAP_TRACES]] + [m.uid for m in markers or []]
    duplicates = {uid for uid in uids if uids.count(uid) > 1}
    if duplicates or "" in uids:
        raise ValueError(f"map trace uids must be unique and non-empty: {sorted(duplicates)}")


def blank_map_figure(style: str = STYLE_LIGHT) -> go.Figure:
    """An empty figure that still names the style.

    Never `go.Figure()`: with no ``layout.map.style`` Plotly falls back to its
    own default (voyager), and a style *string* that differs from the mounted
    map's makes ``updateMap`` call ``map.setStyle`` and then reconcile traces
    on ``styledata`` — which fires before the style is actually loaded. That
    is the other way into "Style is not done loading."
    """
    figure = go.Figure()
    figure.update_layout(
        map={"style": style},
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return figure


def ride_map_traces(
    points: list[Point],
    *,
    segment: list[Point],
    ride_name: str,
    lap_label: str,
) -> list[MapTrace]:
    """The ride's two track traces: the whole path, and the highlighted lap.

    Always both, in this order, whether or not a lap is selected — an
    unselected lap is an *empty* trace, not an absent one. Keeping the set
    fixed is what lets Plotly take its update path on a lap click instead of
    destroying and recreating MapLibre layers. See :class:`MapTrace`.
    """
    selected = len(segment) >= 2
    return [
        MapTrace(
            uid="track",
            points=points,
            name="" if selected else ride_name,
            color=TRACK_DIMMED if selected else TRACK_COLOUR,
            width=3.0 if selected else 4.0,
            opacity=0.35 if selected else 1.0,
            hover=not selected,
        ),
        MapTrace(
            uid="lap",
            points=segment if selected else [],
            name=lap_label if selected else "",
            color=TRACK_COLOUR,
            width=5.0,
            hover=selected,
        ),
    ]


def build_map_figure(
    traces: list[MapTrace],
    *,
    markers: list[MapMarker] | None = None,
    focus: list[Point] | None = None,
    cursor_track: tuple[list[float], list[float]] | None = None,
    uirevision: str | None = None,
    style: str = STYLE_LIGHT,
    width_px: int = 900,
    height_px: int = 460,
) -> MapFigure:
    """Assemble the Plotly figure for a set of polylines.

    Built server-side and handed to :func:`route_map` as a state var, because
    ``rx.plotly`` takes the whole figure as its ``data`` prop.

    The viewport is computed from the points — Plotly's map has no
    ``fit_bounds()``, so framing is arithmetic here rather than a call into the
    library.

    Args:
        traces: Polylines to draw, in z-order.
        markers: Labelled pins, drawn above every line.
        focus: Frame the viewport on these points instead of on everything
            drawn. That is what lets a selected lap fill the view while the
            rest of the ride stays visible behind it.
        cursor_track: ``(lat, lon)`` sampled at exactly the chart's plotted
            indices, so a chart ``pointIndex`` addresses this array directly.
            Adds a hidden source trace plus the visible cursor dot that
            ``components/hover_cursor.py`` moves. NaN entries are kept: they
            preserve the index alignment where the GPS had no fix.
        uirevision: **Load-bearing, not cosmetic.** Plotly treats
            ``map.center`` and ``map.zoom`` as user-driven state and keeps them
            across a re-plot while this token is unchanged — and ``None``
            counts as unchanged, which is why a new ride could inherit the
            previous one's camera. Passing a token that changes with the ride
            and the lap selection is what makes the viewport follow them,
            while leaving a manual pan alone when only a chart channel is
            toggled.
    """
    figure = go.Figure()
    all_points: list[Point] = []
    _check_unique_uids(traces, markers)

    for trace in traces[:MAX_HEATMAP_TRACES]:
        # A trace with too few points to draw is still emitted, as an empty
        # one. Dropping it would shrink the trace set, and a trace that comes
        # and goes is exactly the dispose/create churn `uid` exists to
        # prevent. It contributes nothing to the framing either way.
        drawable = len(trace.points) >= 2
        if drawable:
            all_points.extend(trace.points)
        figure.add_trace(
            go.Scattermap(
                uid=trace.uid,
                lat=[p[0] for p in trace.points] if drawable else [],
                lon=[p[1] for p in trace.points] if drawable else [],
                mode="lines",
                line={"width": trace.width, "color": trace.color},
                opacity=trace.opacity,
                name=trace.name,
                hoverinfo="name" if trace.hover and trace.name else "skip",
                showlegend=False,
            )
        )

    for marker in markers or []:
        figure.add_trace(
            go.Scattermap(
                uid=marker.uid,
                lat=[marker.point[0]],
                lon=[marker.point[1]],
                # Markers only, never "markers+text". Text is what makes Plotly
                # build a symbol layer and reach for an icon that does not
                # exist; see MapMarker and AGENTS.md rule 22.
                mode="markers",
                marker={"size": marker.size, "color": marker.color},
                hovertext=[marker.hover or marker.label],
                # The label is only reachable by hover now, so this must not
                # switch off for a marker that carries a label but no separate
                # hover string.
                hoverinfo="text" if (marker.hover or marker.label) else "skip",
                showlegend=False,
            )
        )

    if cursor_track is not None:
        cursor_lat, cursor_lon = cursor_track
        figure.add_trace(
            go.Scattermap(
                uid=CURSOR_TRACK_UID,
                lat=cursor_lat,
                lon=cursor_lon,
                mode="markers",
                marker={"size": 1, "opacity": 0},
                name=CURSOR_TRACK_TRACE,
                hoverinfo="skip",
                showlegend=False,
            )
        )
        figure.add_trace(
            go.Scattermap(
                uid=CURSOR_UID,
                lat=[],
                lon=[],
                mode="markers",
                marker={"size": 13, "color": CURSOR_COLOUR},
                name=CURSOR_TRACE,
                hoverinfo="skip",
                showlegend=False,
            )
        )

    framed = focus if focus else all_points
    center_lat, center_lon, zoom = bounds_to_center_zoom(
        framed, width_px=width_px, height_px=height_px
    )

    figure.update_layout(
        map={
            "style": style,
            "center": {"lat": center_lat, "lon": center_lon},
            "zoom": zoom,
        },
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        showlegend=False,
        uirevision=uirevision,
        # Transparent paper lets the card behind the map supply the background,
        # so the map does not punch a white rectangle into a dark theme.
        paper_bgcolor="rgba(0,0,0,0)",
        height=height_px,
    )

    return MapFigure(
        figure=figure,
        center_lat=center_lat,
        center_lon=center_lon,
        zoom=zoom,
        empty=not all_points,
        traces=list(traces),
    )


def route_map(
    figure: rx.Var[dict],
    height: str = "460px",
    *,
    record_key: rx.Var[str] | str = "map",
) -> rx.Component:
    """Full-page interactive map. Zoom and pan come from the library.

    ``record_key`` is the React key and it is **load-bearing**, not a hint to
    the reconciler. It must change whenever the figure starts describing a
    different record. React then destroys and recreates the plot in the same
    commit, which runs ``Plotly.purge`` and sends the next mount through
    Plotly's ``createMap`` — the one path that waits for the MapLibre style.
    Without it, a figure belonging to another record can reach a live map via
    ``updateMap`` and throw "Style is not done loading." See AGENTS.md rule 29.
    """
    return rx.box(
        rx.plotly(
            key=record_key,
            # The figure travels as an already-serialised dict, because
            # Reflex's own `go.Figure` serializer strips every trace uid
            # (utils/plotly_json). `data` is declared `Var[Figure]`, so the
            # Var is re-typed here; the value on the wire is unchanged.
            data=figure.to(go.Figure),
            config={
                "displayModeBar": False,
                "scrollZoom": True,
                "doubleClick": "reset",
            },
            use_resize_handler=True,
            # The handle the hover-cursor JS uses. react-plotly renders
            # `className` onto the graph div and ignores `id`.
            class_name=MAP_CLASS,
            width="100%",
            height=height,
        ),
        width="100%",
        height=height,
        border_radius="0.6em",
        overflow="hidden",
        background=rx.color("gray", 3),
    )


def empty_map(message: str = "No GPS track for this ride.", height: str = "460px") -> rx.Component:
    """Placeholder for rides recorded without a GPS fix (turbo sessions)."""
    return rx.center(
        rx.vstack(
            rx.icon("map-pin-off", size=28, color=rx.color("gray", 8)),
            rx.text(message, size="2", color_scheme="gray"),
            spacing="2",
            align="center",
        ),
        width="100%",
        height=height,
        border_radius="0.6em",
        background=rx.color("gray", 3),
    )


def map_skeleton(height: str = "460px") -> rx.Component:
    """Placeholder occupying the map's exact box while the figure is built."""
    return rx.skeleton(
        loading=True,
        width="100%",
        height=height,
        border_radius="0.6em",
    )


def map_slot(
    figure: rx.Var[dict],
    *,
    loading: rx.Var[bool],
    has_track: rx.Var[bool],
    record_key: rx.Var[str] | str = "map",
    height: str = "460px",
    empty_message: str = "No GPS track for this ride.",
) -> rx.Component:
    """The map's three states: still loading, drawn, or no GPS at all.

    ``rx.cond`` and deliberately *not* an ``rx.skeleton`` wrapped around the
    plot. Radix keeps a skeleton's children mounted and merely hides them with
    ``visibility: hidden``, so wrapping would leave the Plotly div alive — and
    the whole point is that the div must not exist until the final figure
    does. ``rx.cond`` compiles to a JSX ternary, which unmounts for real and
    lets react-plotly run ``Plotly.purge``.
    """
    return rx.cond(
        loading,
        map_skeleton(height),
        rx.cond(
            has_track,
            route_map(figure, height, record_key=record_key),
            empty_map(empty_message, height),
        ),
    )


def route_preview_svg(
    points: rx.Var[str] | str,
    size: str = "84px",
    color: str | None = None,
) -> rx.Component:
    """Borderless inset thumbnail of a track (masterplan §2.4.1).

    Takes the pre-projected ``points`` string from :func:`preview_points`
    rather than raw coordinates: list cards are rendered inside ``rx.foreach``,
    where no per-item Python runs, so the projection has to have happened in
    the state already.
    """
    stroke = color or rx.color("accent", 9)
    return rx.box(
        rx.cond(
            points != "",
            rx.el.svg(
                rx.el.svg.polyline(
                    points=points,
                    fill="none",
                    stroke=stroke,
                    stroke_width="4",
                    stroke_linecap="round",
                    stroke_linejoin="round",
                ),
                view_box=f"0 0 {PREVIEW_VIEWBOX:.0f} {PREVIEW_VIEWBOX:.0f}",
                width="100%",
                height="100%",
            ),
            rx.center(
                rx.icon("map-pin-off", size=18, color=rx.color("gray", 8)),
                width="100%",
                height="100%",
            ),
        ),
        width=size,
        height=size,
        min_width=size,
        border_radius="0.5em",
        background=rx.color("gray", 3),
        overflow="hidden",
    )
