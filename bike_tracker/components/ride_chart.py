"""The ride's y-t chart (masterplan §2.4.2).

Elevation is always drawn — grey line with an area fill, as in
``docs/image_2.png`` — and each channel the stat cards toggle on is added as
an extra line.

Figure construction is a module-level function rather than a state method so
it can be tested against a real ride's streams without a Reflex runtime, the
same way ``map_view.build_map_figure`` is.
"""

from dataclasses import dataclass

import numpy as np
import plotly.graph_objects as go
import reflex as rx
from reflex.vars.function import FunctionVar

from .. import metrics
from ..ingest import streams
from ..ingest.streams import IntArray
from ..metrics import FloatArray

# Channels the stat cards can toggle into the chart, mirroring the switch row
# in docs/image_2.png. Elevation is absent because it is the base trace and is
# always drawn.
CHANNEL_SPECS: dict[str, tuple[str, str, str, str]] = {
    # key: (RideStream column, display label, unit, line colour)
    "speed": ("speed_ms", "Speed", "km/h", "#0ea5e9"),
    "hr": ("hr_bpm", "Heart rate", "bpm", "#ef4444"),
    "cadence": ("cadence_rpm", "Cadence", "rpm", "#a855f7"),
    "power": ("power_w", "Power", "W", "#f59e0b"),
    "temperature": ("temperature_c", "Temperature", "°C", "#14b8a6"),
}

# Chart points per channel. A three-hour ride is ~10,000 samples; past a
# couple of thousand the extra points are narrower than a pixel and only cost
# payload on the way to the browser.
MAX_CHART_POINTS = 1500

ELEVATION_COLOUR = "#9ca3af"
ELEVATION_FILL = "rgba(156,163,175,0.35)"
LAP_HIGHLIGHT = "rgba(37,99,235,0.12)"


@dataclass
class ChartData:
    """Everything the chart needs, already decoded from a ``RideStream``.

    Two time bases, deliberately:

    * ``time_s`` — wall-clock seconds since the first record. This is what the
      database stores and what lap boundaries are expressed in, so it stays
      the coordinate system callers speak.
    * ``axis_s`` — *ride* time, with pauses squeezed out. This is what the
      chart plots, because a rider stopped at a level crossing should not get
      a flat line proportional to how long the barrier was down.
    """

    time_s: FloatArray
    axis_s: FloatArray
    altitude_m: FloatArray
    channels: dict[str, FloatArray]

    @classmethod
    def from_channels(
        cls,
        time_s: FloatArray,
        altitude_m: FloatArray,
        channels: dict[str, FloatArray],
    ) -> "ChartData":
        """Build from raw streams, deriving the ride-time axis."""
        return cls(
            time_s=time_s,
            axis_s=metrics.moving_time_axis(time_s),
            altitude_m=altitude_m,
            channels=channels,
        )

    def axis_at(self, elapsed_s: float) -> float:
        """Where a wall-clock instant falls on the ride-time axis.

        Lap boundaries arrive as elapsed seconds — that is what the database
        holds — so they have to be projected onto the compressed axis or the
        highlight would drift by the length of every pause before it.
        """
        if self.axis_s.size == 0:
            return 0.0
        return float(np.interp(elapsed_s, self.time_s, self.axis_s))


def build_chart_figure(
    data: ChartData,
    active_channels: list[str],
    lap_span: tuple[float, float] | None = None,
) -> go.Figure:
    """Assemble the y-t chart.

    Each active channel gets its own overlaying y-axis with hidden tick
    labels. Five channels in five different units cannot share one scale, and
    five visible axes would leave no room for the plot itself — so exact
    values come from the unified hover box instead of from axis gridlines.

    Args:
        data: Decoded streams for the ride.
        active_channels: Keys from :data:`CHANNEL_SPECS`, in the order the
            user switched them on.
        lap_span: ``(start_s, end_s)`` of a selected lap **in elapsed
            seconds**, shaded to keep the lap visible in the context of the
            whole ride rather than ripped out of it. Projected onto the
            ride-time axis here, so callers never deal with two coordinate
            systems.
    """
    figure = go.Figure()
    if data.time_s.size == 0:
        _apply_layout(figure, {}, show_legend=False)
        return figure

    indices = chart_indices(len(data.time_s))
    x_values = [seconds_to_clock(v) for v in data.axis_s[indices]]

    if data.altitude_m.size:
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=data.altitude_m[indices],
                name="Elevation",
                mode="lines",
                line={"color": ELEVATION_COLOUR, "width": 1},
                fill="tozeroy",
                fillcolor=ELEVATION_FILL,
                hovertemplate="%{y:.0f} m<extra>Elevation</extra>",
            )
        )

    overlay_axes: dict[str, dict[str, object]] = {}
    for position, channel in enumerate(active_channels):
        values = data.channels.get(channel)
        if values is None or values.size == 0 or channel not in CHANNEL_SPECS:
            continue
        _column, label, unit, colour = CHANNEL_SPECS[channel]
        axis_index = position + 2  # y1 belongs to elevation

        # Speed is stored in m/s and shown in km/h; everything else is already
        # in the unit the card displays.
        plotted = values[indices] * (3.6 if channel == "speed" else 1.0)

        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=plotted,
                name=label,
                mode="lines",
                line={"color": colour, "width": 1.6},
                yaxis=f"y{axis_index}",
                hovertemplate=f"%{{y:.0f}} {unit}<extra>{label}</extra>",
            )
        )
        overlay_axes[f"yaxis{axis_index}"] = {
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "showticklabels": False,
            "zeroline": False,
        }

    if lap_span is not None:
        figure.add_vrect(
            x0=seconds_to_clock(data.axis_at(lap_span[0])),
            x1=seconds_to_clock(data.axis_at(lap_span[1])),
            fillcolor=LAP_HIGHLIGHT,
            line_width=0,
            layer="below",
        )

    _apply_layout(figure, overlay_axes, show_legend=bool(active_channels))
    return figure


def chart_indices(sample_count: int) -> "IntArray":
    """Which samples the chart actually plots.

    Exported because the map's hover-cursor track must be sampled at exactly
    these indices — a chart ``pointIndex`` is an index into this selection, so
    if the two ever drift apart the dot lands somewhere else on the track.
    """
    return streams.downsample_indices(sample_count, MAX_CHART_POINTS)


def _apply_layout(
    figure: go.Figure,
    overlay_axes: dict[str, dict[str, object]],
    *,
    show_legend: bool,
) -> None:
    figure.update_layout(
        margin={"l": 48, "r": 16, "t": 8, "b": 32},
        height=280,
        hovermode="x unified",
        showlegend=show_legend,
        legend={"orientation": "h", "y": 1.16, "x": 0},
        # Transparent so the card behind supplies the background and the chart
        # follows the light/dark theme instead of punching a white rectangle.
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis={"showgrid": False, "type": "date", "tickformat": "%H:%M:%S"},
        yaxis={"title": "m", "showgrid": True, "gridcolor": "rgba(156,163,175,0.2)"},
        **overlay_axes,
    )


def seconds_to_clock(seconds: float) -> str:
    """Ride-time seconds as an ISO timestamp on a fixed epoch.

    Plotly has no "duration" axis type. Mapping the axis onto a date axis
    that starts at midnight is what produces the ``H:MM:SS`` tick labels a
    ride needs, without putting a real calendar date on the axis. Rides longer
    than 24 hours would wrap, which no ride in this dataset is.
    """
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"1970-01-01T{hours:02d}:{minutes:02d}:{secs:02d}"


def decode_chart_data(stream: object | None) -> ChartData:
    """Pull the chart's channels out of a ``RideStream`` row."""
    empty = np.empty(0, dtype=np.float64)
    if stream is None:
        return ChartData(time_s=empty, axis_s=empty, altitude_m=empty, channels={})

    channels: dict[str, FloatArray] = {}
    for key, (column, _label, _unit, _colour) in CHANNEL_SPECS.items():
        values = streams.decode(getattr(stream, column, None))
        if values.size and bool(np.isfinite(values).any()):
            channels[key] = values

    return ChartData.from_channels(
        time_s=streams.decode(getattr(stream, "time_s", None)),
        altitude_m=streams.decode(getattr(stream, "altitude_m", None)),
        channels=channels,
    )


def ride_chart(figure: rx.Var[go.Figure], height: str = "280px") -> rx.Component:
    """The chart itself. Zoom and pan come from the library.

    ``on_hover`` and ``on_unhover`` are handed raw ``FunctionVar``s rather than
    Reflex event handlers, which compiles them to a direct client-side call.
    A server-side handler here would round-trip on every mouse movement and
    re-serialize the whole map figure each time — see
    ``components/hover_cursor.py``.
    """
    return rx.plotly(
        data=figure,
        config={"displayModeBar": False, "scrollZoom": True, "doubleClick": "reset"},
        use_resize_handler=True,
        on_hover=rx.Var("window.__btHover").to(FunctionVar),
        on_unhover=rx.Var("window.__btUnhover").to(FunctionVar),
        width="100%",
        height=height,
    )
