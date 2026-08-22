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


@dataclass
class MapTrace:
    """One polyline to draw on a map."""

    points: list[Point]
    name: str = ""
    color: str = "#2563eb"
    width: float = 3.0
    opacity: float = 1.0
    hover: bool = True


@dataclass
class MapMarker:
    """A labelled pin on the map — one per lap boundary.

    ``label`` is drawn inside the circle rather than beside it, which is what
    keeps a dense track legible. Plotly's ``marker.symbol`` would give a glyph
    closer to the reference screenshot, but it resolves against the map
    style's sprite sheet and silently falls back to a plain dot when the
    sprite is missing; text always renders, and a lap number says more than a
    generic icon anyway.
    """

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


def build_map_figure(
    traces: list[MapTrace],
    *,
    markers: list[MapMarker] | None = None,
    focus: list[Point] | None = None,
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

    for trace in traces[:MAX_HEATMAP_TRACES]:
        if len(trace.points) < 2:
            continue
        all_points.extend(trace.points)
        figure.add_trace(
            go.Scattermap(
                lat=[p[0] for p in trace.points],
                lon=[p[1] for p in trace.points],
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
                lat=[marker.point[0]],
                lon=[marker.point[1]],
                mode="markers+text",
                marker={"size": marker.size, "color": marker.color},
                text=[marker.label],
                textposition="middle center",
                textfont={"color": "white", "size": 11, "family": "Arial Black"},
                hovertext=[marker.hover or marker.label],
                hoverinfo="text" if marker.hover else "skip",
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


def route_map(figure: rx.Var[go.Figure], height: str = "460px") -> rx.Component:
    """Full-page interactive map. Zoom and pan come from the library."""
    return rx.box(
        rx.plotly(
            data=figure,
            config={
                "displayModeBar": False,
                "scrollZoom": True,
                "doubleClick": "reset",
            },
            use_resize_handler=True,
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
