"""Ride detail state (masterplan §2.4.2).

Holds four things that all key off the same selection: the map figure, the
y-t chart, the lap bars and the stat cards.

**Why the stats are one dict var rather than sixty computed vars.** Every
number on the page has to switch between "whole ride" and "selected lap" when
a lap bar is clicked. Exposing ``stats`` as a ``dict[str, str]`` of
pre-formatted values means that switch is one recomputation, and the card
layout in ``components/ride_stats.py`` stays a declarative table keyed by
name. Dict Vars support ``.get(key, default)`` on the frontend, so the cards
read from it directly.

Dynamic route note: ``@rx.page(route="/rides/[ride_id]")`` injects ``ride_id``
into *every* state class. It must not be declared as a field — that raises
``DynamicRouteArgShadowsStateVarError`` — hence the ``TYPE_CHECKING`` block.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import plotly.graph_objects as go
import reflex as rx
from sqlmodel import col, select

from .. import metrics
from ..components.map_view import (
    STYLE_LIGHT,
    MapMarker,
    MapTrace,
    build_map_figure,
)
from ..components.ride_chart import (
    CHANNEL_SPECS,
    ChartData,
    build_chart_figure,
    chart_indices,
)
from ..components.toast import notify_error, notify_success
from ..ingest import streams
from ..ingest.importer import create_manual_route, refresh_route_stats
from ..metrics import Point
from ..models import Bike, Lap, MetricFields, Ride, RideStream, Route
from ..utils.formatting import (
    EMPTY,
    format_balance,
    format_distance_km,
    format_duration,
    format_number,
    format_speed_kmh,
)
from ..utils.logging import get_logger
from .routes_state import NEW_ROUTE_NAME, RoutesState

log = get_logger(__name__).bind(state="RideDetailState")

TRACK_COLOUR = "#2563eb"
TRACK_DIMMED = "#94a3b8"

# Douglas-Peucker tolerance for what the map draws. Tighter than the stored
# route geometry because this is the ride itself at full zoom.
MAP_SIMPLIFY_EPSILON_M = 8.0


def _thin(points: list[Point]) -> list[Point]:
    """Simplify a track for drawing. A raw 1 Hz ride is ~10,000 points."""
    return metrics.simplify_polyline(points, epsilon_m=MAP_SIMPLIFY_EPSILON_M)


@dataclass
class LapBar:
    """One segment of the lap strip under the chart."""

    index: int = 0
    label: str = ""
    width_pct: float = 0.0
    distance_km: str = ""
    duration: str = ""


@dataclass
class SelectOption:
    """One entry in the bike or route dropdown."""

    value: str = ""
    label: str = ""


class RideDetailState(rx.State):
    if TYPE_CHECKING:
        ride_id: str  # injected by the dynamic route — never assign it

    not_found: bool = False
    title: str = ""
    subtitle: str = ""

    has_track: bool = False
    map_figure: go.Figure = go.Figure()
    chart_figure: go.Figure = go.Figure()

    stats: dict[str, str] = {}
    active_channels: list[str] = []
    available_channels: list[str] = []

    laps: list[LapBar] = []
    selected_lap: int = -1  # -1 means the whole ride

    bike_options: list[SelectOption] = []
    route_options: list[SelectOption] = []
    bike_value: str = ""
    route_value: str = ""

    # Raw channels for the currently loaded ride, kept off the wire. Private
    # (underscore) vars are never serialised to the client, which matters here:
    # these are the full-resolution streams.
    _channels: dict[str, list[float]] = {}
    _time_s: list[float] = []
    _lap_bounds: list[tuple[float, float]] = []
    _lap_slices: list[tuple[int, int]] = []

    @rx.var
    def lap_count(self) -> int:
        return len(self.laps)

    @rx.var
    def has_laps(self) -> bool:
        return len(self.laps) > 1

    @rx.var
    def scope_label(self) -> str:
        """What the stat cards are currently describing."""
        if self.selected_lap < 0:
            return "Whole ride"
        return f"Lap {self.selected_lap + 1}"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @rx.event
    def load(self):
        """Page ``on_load`` — fetch the ride and rebuild every derived view."""
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
        # Clearing the flag and yielding sends it as its own delta, which
        # unmounts the plot (running `Plotly.purge`, clearing `_promises`) so
        # the next mount coincides with the final figure and is gated on the
        # style `load` event.
        self.has_track = False
        yield

        self.not_found = False
        self.selected_lap = -1
        self.active_channels = []

        try:
            ride_id = int(self.ride_id)
        except (TypeError, ValueError):
            self.not_found = True
            log.info("ride_detail.bad_id", raw_id=self.ride_id)
            return

        with rx.session() as session:
            ride = session.get(Ride, ride_id)
            if ride is None:
                self.not_found = True
                self.title = "Ride not found"
                log.info("ride_detail.missing", ride_id=ride_id)
                return

            stream = session.exec(select(RideStream).where(RideStream.ride_id == ride_id)).first()
            laps = session.exec(
                select(Lap).where(Lap.ride_id == ride_id).order_by(col(Lap.index))
            ).all()
            bikes = session.exec(select(Bike).order_by(col(Bike.name))).all()
            all_routes = session.exec(select(Route).order_by(col(Route.name))).all()

        self.title = ride.name
        self.subtitle = ride.start_time.strftime("%A, %d %B %Y at %H:%M")

        self._load_channels(stream)
        self._build_laps(ride, laps)
        self._build_selectors(ride, bikes, all_routes)
        self._build_map(ride)
        self.stats = _stats(ride)
        self._rebuild_chart()

        log.info(
            "ride_detail.load",
            ride_id=ride_id,
            samples=len(self._time_s),
            laps=len(laps),
            channels=sorted(self.available_channels),
        )

    def _load_channels(self, stream: RideStream | None) -> None:
        self._channels = {}
        self._time_s = []
        self.available_channels = []
        if stream is None:
            return

        time_s = streams.decode(stream.time_s)
        self._time_s = [float(v) for v in time_s]

        for key, (column, _label, _unit, _colour) in CHANNEL_SPECS.items():
            values = streams.decode(getattr(stream, column, None))
            if values.size and bool(np.isfinite(values).any()):
                self._channels[key] = [float(v) for v in values]
                self.available_channels.append(key)

        altitude = streams.decode(stream.altitude_m)
        if altitude.size:
            self._channels["altitude"] = [float(v) for v in altitude]

        for name in ("lat", "lon"):
            values = streams.decode(getattr(stream, name))
            if values.size:
                self._channels[name] = [float(v) for v in values]

    def _build_laps(self, ride: Ride, laps: Sequence[Lap]) -> None:
        self._lap_bounds = []
        self._lap_slices = []
        self.laps = []
        if len(laps) < 2:
            # A single lap is the whole ride; showing one full-width bar would
            # be a control that cannot do anything.
            return

        # The stream's clock starts at the first record, the ride's at the
        # session start — two seconds apart on the evening ride. Without the
        # correction every lap window would be shifted by that much.
        origin = ride.start_time.timestamp() + ride.stream_offset_s
        time_s = np.asarray(self._time_s, dtype=np.float64)
        total_moving = sum(lap.moving_time_s or lap.elapsed_time_s for lap in laps) or 1.0

        for lap in laps:
            start_s = lap.start_time.timestamp() - origin
            end_s = start_s + lap.elapsed_time_s
            self._lap_bounds.append((start_s, end_s))
            self._lap_slices.append(metrics.window_indices(time_s, start_s, end_s))

            moving = lap.moving_time_s or lap.elapsed_time_s
            self.laps.append(
                LapBar(
                    index=lap.index,
                    label=str(lap.index + 1),
                    # Widths track *moving* time, because that is what the
                    # chart's axis above now measures. Using elapsed time here
                    # would leave the strip and the axis disagreeing by the
                    # length of every pause.
                    width_pct=round(moving / total_moving * 100.0, 4),
                    distance_km=format_distance_km(lap.distance_m),
                    duration=format_duration(moving),
                )
            )

    def _build_selectors(
        self, ride: Ride, bikes: Sequence[Bike], all_routes: Sequence[Route]
    ) -> None:
        self.bike_options = [SelectOption(value="", label="Unassigned")] + [
            SelectOption(value=str(bike.id), label=bike.name) for bike in bikes
        ]
        self._build_route_options(all_routes)
        self.bike_value = str(ride.bike_id) if ride.bike_id else ""
        self.route_value = str(ride.route_id) if ride.route_id else ""

    def _build_route_options(self, all_routes: Sequence[Route]) -> None:
        """The route dropdown's contents.

        Rebuilt after every reassignment, not only on load: a route can be
        pruned by that reassignment and must stop being offered immediately.
        """
        self.route_options = (
            [SelectOption(value="", label="No route")]
            + [SelectOption(value=str(route.id), label=route.name) for route in all_routes]
            + [SelectOption(value=CREATE_ROUTE_OPTION, label="Create new")]
        )

    def _build_map(self, ride: Ride) -> None:
        """Draw the track, the lap boundaries, and whatever is selected.

        With no lap selected the whole track is drawn in the accent colour and
        framed edge to edge. Selecting a lap dims the track, redraws that lap
        on top and reframes the viewport around it — so the lap is read in the
        context of the ride, the same way the chart shades it rather than
        cropping to it.
        """
        points = metrics.to_points(
            np.asarray(self._channels.get("lat", []), dtype=np.float64),
            np.asarray(self._channels.get("lon", []), dtype=np.float64),
        )
        self.has_track = len(points) >= 2
        if not self.has_track:
            self.map_figure = go.Figure()
            return

        selected = self._selected_slice()
        traces: list[MapTrace] = []
        focus: list[Point] | None = None

        if selected is None:
            traces.append(
                MapTrace(points=_thin(points), name=ride.name, color=TRACK_COLOUR, width=4.0)
            )
        else:
            segment = _thin(points[selected[0] : selected[1]])
            traces.append(
                MapTrace(points=_thin(points), color=TRACK_DIMMED, width=3.0, opacity=0.35)
            )
            if len(segment) >= 2:
                traces.append(
                    MapTrace(
                        points=segment,
                        name=f"Lap {self.selected_lap + 1}",
                        color=TRACK_COLOUR,
                        width=5.0,
                    )
                )
                focus = segment

        built = build_map_figure(
            traces,
            markers=self._lap_markers(points),
            focus=focus,
            cursor_track=self._cursor_track(),
            # Changes with the ride and the selection, so Plotly reapplies the
            # viewport instead of keeping the previous one. See build_map_figure.
            uirevision=f"{ride.id}:{self.selected_lap}",
            style=STYLE_LIGHT,
        )
        self.map_figure = built.figure

    def _cursor_track(self) -> tuple[list[float], list[float]] | None:
        """Coordinates sampled at exactly the chart's plotted indices.

        The hover cursor addresses this by the chart's own ``pointIndex``, so
        the two selections must be produced by the same function. Samples with
        no GPS fix stay in as NaN rather than being dropped — removing them
        would shift every later index and put the dot in the wrong place.
        """
        lat = np.asarray(self._channels.get("lat", []), dtype=np.float64)
        lon = np.asarray(self._channels.get("lon", []), dtype=np.float64)
        if lat.size == 0 or lon.size != lat.size:
            return None

        indices = chart_indices(len(self._time_s))
        if indices.size == 0:
            return None
        return lat[indices].tolist(), lon[indices].tolist()

    def _selected_slice(self) -> tuple[int, int] | None:
        """Sample range of the selected lap, or None for the whole ride."""
        if not 0 <= self.selected_lap < len(self._lap_slices):
            return None
        i0, i1 = self._lap_slices[self.selected_lap]
        return (i0, i1) if i1 - i0 >= 2 else None

    def _lap_markers(self, points: list[Point]) -> list[MapMarker]:
        """A pin where each lap begins, for laps 2..N.

        Only *internal* boundaries get one: the ride's start and finish are not
        "between two laps". A boundary whose sample has no GPS fix is skipped
        rather than drawn at a nonsense coordinate — the evening ride has no
        fix for its first nine samples.
        """
        markers: list[MapMarker] = []
        for lap_bar, (i0, _i1) in zip(self.laps, self._lap_slices, strict=True):
            if lap_bar.index == 0 or not 0 <= i0 < len(points):
                continue
            markers.append(
                MapMarker(
                    point=points[i0],
                    label=lap_bar.label,
                    hover=f"Lap {lap_bar.label} · {lap_bar.distance_km} km · {lap_bar.duration}",
                )
            )
        return markers

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    @rx.event
    def toggle_channel(self, channel: str):
        """Add or remove a channel from the chart (masterplan §2.4.2).

        Clicking a stat card is the switch: the card's appearance changes and
        its channel appears in the y-t chart below the map.
        """
        if channel not in self.available_channels:
            return
        if channel in self.active_channels:
            self.active_channels = [c for c in self.active_channels if c != channel]
        else:
            self.active_channels = [*self.active_channels, channel]
        self._rebuild_chart()

    @rx.event
    def select_lap(self, index: int):
        """Select a lap, or deselect it if it was already selected.

        Selecting switches every stat card to that lap's numbers; the chart
        keeps showing the whole ride with the lap highlighted, so the lap stays
        in context instead of being ripped out of it.
        """
        self.selected_lap = -1 if self.selected_lap == index else index

        with rx.session() as session:
            ride = session.get(Ride, int(self.ride_id))
            if ride is None:
                return
            if self.selected_lap < 0:
                self.stats = _stats(ride)
            else:
                lap = session.exec(
                    select(Lap).where(Lap.ride_id == ride.id).where(Lap.index == self.selected_lap)
                ).first()
                # A lap carries the full metric set, so the same function
                # serves it — no fallback to whole-ride numbers.
                self.stats = _stats(lap) if lap else _stats(ride)

            self._build_map(ride)

        self._rebuild_chart()

    @rx.event
    def set_bike(self, value: str):
        """Reassign the ride's bike, keeping both odometers honest.

        Masterplan §2.4.2 requires the dropdown to update the bike's ride
        association; because the odometer is maintained incrementally
        (§3.1), a reassignment has to subtract from one bike and add to the
        other rather than just rewriting a foreign key.
        """
        value = _from_select(value)
        self.bike_value = value
        new_bike_id = int(value) if value else None

        with rx.session() as session:
            ride = session.get(Ride, int(self.ride_id))
            if ride is None:
                return notify_error("That ride no longer exists.")

            old_bike_id = ride.bike_id
            if old_bike_id == new_bike_id:
                return None

            if old_bike_id is not None:
                previous = session.get(Bike, old_bike_id)
                if previous is not None:
                    previous.odo_m = max(0.0, previous.odo_m - ride.distance_m)
                    session.add(previous)
            if new_bike_id is not None:
                current = session.get(Bike, new_bike_id)
                if current is not None:
                    current.odo_m += ride.distance_m
                    session.add(current)

            ride.bike_id = new_bike_id
            session.add(ride)
            session.commit()
            new_bike = session.get(Bike, new_bike_id) if new_bike_id else None
            name = new_bike.name if new_bike is not None else "Unassigned"

        log.info("ride_detail.bike.set", ride_id=self.ride_id, bike_id=new_bike_id)
        return notify_success(f"Bike set to {name}.")

    @rx.event
    async def set_route(self, value: str):
        """Reassign the ride's route and refresh both routes' averages.

        The dropdown's last entry is an action, not a route: it creates one
        from this ride and hands straight over to the rename dialog.

        A generator rather than a plain handler because the create branch has
        to emit two things — a toast and the opened dialog — so every exit path
        here yields instead of returning a value.
        """
        if value == CREATE_ROUTE_OPTION:
            async for event in self._create_route():
                yield event
            return

        value = _from_select(value)
        self.route_value = value
        new_route_id = int(value) if value else None

        with rx.session() as session:
            ride = session.get(Ride, int(self.ride_id))
            if ride is None:
                yield notify_error("That ride no longer exists.")
                return

            old_route_id = ride.route_id
            if old_route_id == new_route_id:
                return

            ride.route_id = new_route_id
            session.add(ride)
            session.commit()

            # Read the name before refreshing: the refresh commits, a commit
            # expires every instance in the session, and the route the ride
            # just left may be deleted outright.
            new_route = session.get(Route, new_route_id) if new_route_id else None
            name = new_route.name if new_route is not None else "No route"

            # Both sides change: the route losing a ride and the one gaining it
            # each need their averages recomputed. The one being left may now
            # have no rides at all, in which case refresh_route_stats prunes it.
            for route_id in {old_route_id, new_route_id} - {None}:
                route = session.get(Route, route_id)
                if route is not None:
                    refresh_route_stats(session, route)

            # Rebuild the options: a pruned route must stop being offered, or
            # picking it would write a foreign key to a row that is gone.
            self._build_route_options(session.exec(select(Route).order_by(col(Route.name))).all())

        log.info("ride_detail.route.set", ride_id=self.ride_id, route_id=new_route_id)
        yield notify_success(f"Route set to {name}.")

    async def _create_route(self) -> AsyncIterator[Any]:
        """Split this ride onto a route of its own, then offer to name it.

        The route takes the ride's own polyline, so future rides along the same
        roads match into it exactly as they would a route the importer made.
        The old route is pruned by ``refresh_route_stats`` if this was its last
        ride.
        """
        with rx.session() as session:
            ride = session.get(Ride, int(self.ride_id))
            if ride is None:
                yield notify_error("That ride no longer exists.")
                return
            if not ride.map_points:
                yield notify_error("This ride has no GPS track to build a route from.")
                return

            old_route_id = ride.route_id
            route = create_manual_route(session, ride, NEW_ROUTE_NAME)
            route_id, route_name = route.id or 0, route.name

            if old_route_id is not None and old_route_id != route_id:
                previous = session.get(Route, old_route_id)
                if previous is not None:
                    refresh_route_stats(session, previous)
            refresh_route_stats(session, route)

            self._build_route_options(session.exec(select(Route).order_by(col(Route.name))).all())

        self.route_value = str(route_id)
        log.info("ride_detail.route.created", ride_id=self.ride_id, route_id=route_id)
        yield notify_success(f"Created “{route_name}”.")

        # Hand straight to the rename dialog so the placeholder does not
        # survive the interaction. Cancelling still leaves the route created
        # and assigned — it is just called "New route".
        #
        # States are flat, so this reaches RoutesState with get_state rather
        # than inheritance, and drives it through a plain method rather than
        # by firing its event handler.
        routes_state = await self.get_state(RoutesState)
        routes_state.prepare_rename(route_id, route_name)

    # ------------------------------------------------------------------
    # Chart
    # ------------------------------------------------------------------

    def _rebuild_chart(self) -> None:
        """Rebuild the chart figure for the current channel and lap selection."""
        lap_span: tuple[float, float] | None = None
        if 0 <= self.selected_lap < len(self._lap_bounds):
            lap_span = self._lap_bounds[self.selected_lap]

        self.chart_figure = build_chart_figure(
            ChartData.from_channels(
                time_s=np.asarray(self._time_s, dtype=np.float64),
                altitude_m=np.asarray(self._channels.get("altitude", []), dtype=np.float64),
                channels={
                    key: np.asarray(values, dtype=np.float64)
                    for key, values in self._channels.items()
                },
            ),
            list(self.active_channels),
            lap_span,
        )


# Radix's select rejects "" as an item value, so "unassigned" travels as a
# sentinel and is translated back here rather than in the component.
NONE_OPTION = "__none__"

# Second sentinel on the route dropdown: an action rather than a route.
# Radix rejects "" as an item value, so both specials travel as sentinels.
CREATE_ROUTE_OPTION = "__create__"


def _from_select(value: str) -> str:
    return "" if value == NONE_OPTION else value


def _stats(m: MetricFields) -> dict[str, str]:
    """Every stat-card value for one window, pre-formatted.

    Takes a :class:`MetricFields`, so a ``Ride`` and a ``Lap`` go through the
    same function and cannot drift. Keys match the specs in
    ``components/ride_stats.py``; the grouping there follows the colour blocks
    in ``docs/image.png``.
    """
    return {
        # Distance and time (temperature grouped here per masterplan §2.4.2)
        "distance": format_distance_km(m.distance_m),
        "moving_time": format_duration(m.moving_time_s),
        "paused_time": format_duration(m.paused_time_s),
        "avg_temperature": format_number(m.avg_temperature_c),
        "max_temperature": format_number(m.max_temperature_c),
        "min_temperature": format_number(m.min_temperature_c),
        # Elevation
        "elevation_gain": format_number(m.elevation_gain_m),
        "elevation_loss": format_number(m.elevation_loss_m),
        "avg_grade": format_number(m.avg_grade_pct, 1),
        "max_grade": format_number(m.max_grade_pct, 1),
        "min_grade": format_number(m.min_grade_pct, 1),
        "avg_altitude": format_number(m.avg_altitude_m),
        "max_altitude": format_number(m.max_altitude_m),
        "min_altitude": format_number(m.min_altitude_m),
        "avg_vam": format_number(m.avg_vam_mph),
        # Speed
        "avg_speed": format_speed_kmh(m.avg_speed_ms),
        "max_speed": format_speed_kmh(m.max_speed_ms),
        "min_speed": format_speed_kmh(m.min_speed_ms),
        # Heart rate
        "avg_hr": format_number(m.avg_hr_bpm),
        "max_hr": format_number(m.max_hr_bpm),
        "min_hr": format_number(m.min_hr_bpm),
        # Cadence
        "avg_cadence": format_number(m.avg_cadence_rpm),
        "max_cadence": format_number(m.max_cadence_rpm),
        "min_cadence": format_number(m.min_cadence_rpm),
        # Power
        "avg_power": format_number(m.avg_power_w),
        "max_power": format_number(m.max_power_w),
        "min_power": format_number(m.min_power_w),
        "normalized_power": format_number(m.normalized_power_w),
        "intensity_factor": format_number(m.intensity_factor, 2),
        "tss": format_number(m.tss),
        "ftp": format_number(m.ftp_at_time_w),
        "energy": format_number(m.energy_kj),
        "balance": format_balance(m.left_balance_pct),
        "decoupling": format_number(m.decoupling_pct, 1),
        # Mean-maximal power curve
        "best_power_5s": format_number(m.best_power_5s_w),
        "best_power_15s": format_number(m.best_power_15s_w),
        "best_power_30s": format_number(m.best_power_30s_w),
        "best_power_60s": format_number(m.best_power_60s_w),
        "best_power_300s": format_number(m.best_power_300s_w),
        "best_power_600s": format_number(m.best_power_600s_w),
        "best_power_1200s": format_number(m.best_power_1200s_w),
        "best_power_3600s": format_number(m.best_power_3600s_w),
        # Energy
        "calories": format_number(m.calories_kcal),
        "calories_per_hour": _per_hour(m.calories_kcal, m.moving_time_s),
    }


def _per_hour(total: float | None, seconds: float) -> str:
    if total is None or seconds <= 0:
        return EMPTY
    return format_number(total / (seconds / 3600.0))
