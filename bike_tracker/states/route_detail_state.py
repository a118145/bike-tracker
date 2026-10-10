"""Route detail state (masterplan §2.3.2).

Three things about one route: where it goes, what it is, and how the rider's
numbers on it have changed. The last of those is the point — a route is a
fixed course, so everything that differs between two passes is the rider.

Dynamic route note: ``@rx.page(route="/routes/[route_id]")`` injects
``route_id`` into *every* state class. It must not be declared as a field —
that raises ``DynamicRouteArgShadowsStateVarError`` — hence the
``TYPE_CHECKING`` block. On any other page it is the empty string, which is
why ``load`` guards the ``int()``.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import reflex as rx
from sqlmodel import col, select

from ..components.map_view import (
    HEAT_COLOUR,
    STYLE_LIGHT,
    MapTrace,
    blank_map_figure,
    build_map_figure,
    heat_opacity,
)
from ..models import Ride, Route
from ..navigation import routes as route_urls
from ..utils.formatting import (
    EMPTY,
    format_distance_km,
    format_duration,
    format_number,
    format_speed_kmh,
)
from ..utils.logging import get_logger
from ..utils.plotly_json import figure_to_dict

log = get_logger(__name__).bind(state="RouteDetailState")

# Douglas-Peucker tolerance is already baked into `Ride.map_points`; this is
# only the safety cap on how many rides the map will draw at once.
MAX_HEAT_RIDES = 250


@dataclass
class RideRow:
    """One row of the rides table — everything pre-formatted."""

    id: int = 0
    date: str = ""
    duration: str = ""
    avg_speed: str = ""
    avg_power: str = ""
    normalized_power: str = ""
    best_20min: str = ""
    intensity_factor: str = ""
    tss: str = ""
    avg_hr: str = ""
    detail_href: str = ""
    # Excluded from analysis: listed, dimmed, and left out of the map and the
    # summary card (AGENTS.md rule 42).
    excluded: bool = False


class RouteDetailState(rx.State):
    if TYPE_CHECKING:
        route_id: str  # injected by the dynamic route — never assign it

    not_found: bool = False
    title: str = ""
    subtitle: str = ""

    # See RideDetailState.loading — same contract, one plot instead of two.
    loading: bool = True
    loaded_id: str = ""
    has_track: bool = False
    # Serialised, not `go.Figure` — see utils/plotly_json.
    map_figure: dict = figure_to_dict(blank_map_figure(STYLE_LIGHT))
    # React key for the map plot — see map_view.route_map.
    map_key: str = "route-none"
    truncated_note: str = ""

    stats: dict[str, str] = {}
    rides: list[RideRow] = []

    @rx.var
    def stale(self) -> bool:
        """True while what is on screen may belong to another route.

        See RideDetailState.stale.
        """
        return self.loading or self.loaded_id != self.route_id

    @rx.var
    def ride_count(self) -> int:
        return len(self.rides)

    @rx.event
    def load(self):
        """Page ``on_load`` — fetch the route, its rides, and build both views."""
        # Unmount the map before touching the database.
        #
        # Reflex does not reset state on client-side navigation, so arriving
        # from another route this still holds the *previous* one's figure and
        # `has_track=True`. React would therefore mount the plot immediately
        # with the stale figure, MapLibre would start fetching the basemap
        # style, and the `on_load` delta would land mid-fetch. Plotly's
        # `updateMap` only waits for the style when the style *string* changes
        # — it is "carto-positron" both times — so it goes straight to
        # `addSource` and MapLibre throws "Style is not done loading."
        #
        # That throw is not recoverable: the rejected promise stays in
        # `gd._promises` forever, and `Plots.previousPromises` is the first
        # step of every later plot, so the div never redraws again. Hence the
        # stale map that only a full remount fixed.
        #
        # Raising the flag and yielding sends it as its own delta, which swaps
        # the plot for a skeleton (running `Plotly.purge`, clearing
        # `_promises`) so the next mount coincides with the final figure and
        # goes through `createMap`, which *does* wait for the style.
        #
        # Best-effort only: batched deltas can skip the skeleton frame
        # entirely. The React key on the plot (`map_key`) is the actual
        # guarantee. See map_view.route_map and AGENTS.md rule 29.
        self.loading = True
        yield

        self.not_found = False
        self.truncated_note = ""

        try:
            route_id = int(self.route_id)
        except (TypeError, ValueError):
            self.not_found = True
            log.info("route_detail.bad_id", raw_id=self.route_id)
            return

        with rx.session() as session:
            route = session.get(Route, route_id)
            if route is None:
                self.not_found = True
                self.title = "Route not found"
                log.info("route_detail.missing", route_id=route_id)
                return

            rides = list(
                session.exec(
                    select(Ride)
                    .where(Ride.route_id == route_id)
                    .order_by(col(Ride.start_time).desc())
                ).all()
            )
            name = route.name

        # The table lists every ride; everything that summarises the route
        # describes only the rides in analysis.
        counted = [ride for ride in rides if not ride.excluded]
        self.title = name
        self.subtitle = _ridden(len(counted), len(rides) - len(counted))
        self.rides = [_to_row(ride) for ride in rides]
        self.stats = _route_stats(route, counted)
        self._build_map(route_id, counted)
        # Last, with no `yield` in between, so the flag and the figure land in
        # one delta.
        self.loading = False
        self.loaded_id = str(route_id)

        log.info("route_detail.load", route_id=route_id, rides=len(rides))

    def _build_map(self, route_id: int, rides: list[Ride]) -> None:
        """One translucent trace per ride, so repetition darkens the shared path.

        Masterplan §2.3.2 wants the deviations between passes visible, which is
        what stacking equally-weighted translucent lines gives: overlapping
        segments composite towards opaque, a detour taken once stays faint.

        The polylines come straight from ``Ride.map_points``. Rebuilding them
        from the streams would cost ~20 ms per ride — seconds on a commuting
        route — which is why they are precomputed at import.
        """
        drawn = [ride for ride in rides if ride.map_points][:MAX_HEAT_RIDES]
        self.has_track = bool(drawn)
        if not drawn:
            self.map_key = f"route-{route_id}"
            self.map_figure = figure_to_dict(blank_map_figure(STYLE_LIGHT))
            return

        if len(drawn) < len([r for r in rides if r.map_points]):
            self.truncated_note = f"Showing {len(drawn)} of {len(rides)} rides."

        opacity = heat_opacity(len(drawn))
        traces = [
            MapTrace(
                uid=f"heat-{ride.id}",
                points=[(p[0], p[1]) for p in ride.map_points],
                color=HEAT_COLOUR,
                width=4.0,
                opacity=opacity,
                # No per-ride identity on this map, and 30 named traces would
                # stack 30 tooltips over each other.
                hover=False,
            )
            for ride in drawn
        ]

        built = build_map_figure(
            traces,
            # Mandatory: Plotly keeps map.center/zoom while uirevision is
            # unchanged, and None counts as unchanged — so route A would
            # inherit route B's camera.
            uirevision=f"route:{route_id}",
        )
        self.map_key = f"route-{route_id}"
        self.map_figure = figure_to_dict(built.figure)


# ---------------------------------------------------------------------------
# View-model mapping
# ---------------------------------------------------------------------------


def _ridden(counted: int, excluded: int) -> str:
    """``"Ridden 4 times · 1 excluded"`` — the count is rides in analysis."""
    if counted == 0:
        text = "No rides in analysis"
    elif counted == 1:
        text = "Ridden once"
    else:
        text = f"Ridden {counted} times"
    return f"{text} · {excluded} excluded" if excluded else text


def _to_row(ride: Ride) -> RideRow:
    return RideRow(
        id=ride.id or 0,
        date=ride.start_time.strftime("%d %b %Y"),
        duration=format_duration(ride.moving_time_s),
        avg_speed=format_speed_kmh(ride.avg_speed_ms),
        avg_power=format_number(ride.avg_power_w),
        normalized_power=format_number(ride.normalized_power_w),
        best_20min=format_number(ride.best_power_1200s_w),
        intensity_factor=format_number(ride.intensity_factor, 2),
        tss=format_number(ride.tss),
        avg_hr=format_number(ride.avg_hr_bpm),
        detail_href=f"{route_urls.RIDES_ROUTE}/{ride.id}",
        excluded=ride.excluded,
    )


def _route_stats(route: Route, rides: list[Ride]) -> dict[str, str]:
    """The route-level card: what does not change meaningfully per ride.

    The fastest time and the TSS figures come from the same rides already
    fetched for the table, so they cost no extra query.
    """
    times = [r.moving_time_s for r in rides if r.moving_time_s > 0]
    loads = [r.tss for r in rides if r.tss is not None]

    fastest = min(times) if times else None
    fastest_ride = (
        min((r for r in rides if r.moving_time_s == fastest), key=lambda r: r.start_time)
        if fastest is not None
        else None
    )

    return {
        "distance": format_distance_km(route.avg_distance_m),
        "elevation": format_number(route.avg_elevation_gain_m),
        "avg_time": format_duration(route.avg_moving_time_s),
        "ride_count": str(route.ride_count),
        "fastest": format_duration(fastest) if fastest is not None else EMPTY,
        "fastest_on": (fastest_ride.start_time.strftime("%d %b %Y") if fastest_ride else EMPTY),
        "avg_tss": format_number(sum(loads) / len(loads)) if loads else EMPTY,
        "best_tss": format_number(max(loads)) if loads else EMPTY,
    }
