"""Dashboard state (masterplan §2.2).

A page-wide heat map of every ride above the cards that total them: one card
per bike profile, plus a summary card carrying the whole-database totals and
the current FTP with its training zones.

**The bike cards filter the map, and that is the interesting constraint.** A
filter that removed traces would change the trace set of a mounted MapLibre
map, which is the destroy/create churn AGENTS.md rule 31 exists to prevent —
it throws "Style is not done loading." and permanently poisons the graph div.
So every ride keeps its trace at all times and a deselected one is emitted
*empty*. :func:`dashboard_traces` is the whole of that rule, in one pure
function, which is why it lives at module level with a test rather than inside
the state.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import reflex as rx
from sqlmodel import col, select

from .. import metrics
from ..components.map_view import (
    HEAT_COLOUR,
    STYLE_LIGHT,
    MapTrace,
    blank_map_figure,
    build_map_figure,
    heat_opacity,
)
from ..components.zone_table import ZoneRow, zone_rows
from ..models import EVAL_FTP, EVAL_LTHR, Bike, FitnessEval, Ride, analysis_rides
from ..utils.formatting import EMPTY, format_date, format_distance_km, format_number
from ..utils.logging import get_logger
from ..utils.plotly_json import figure_to_dict

log = get_logger(__name__).bind(state="DashboardState")

# Same cap as the route heat map: hundreds of polylines will stall any
# renderer, and this page draws every ride there is rather than one route's.
MAX_DASHBOARD_RIDES = 250


@dataclass(frozen=True)
class RideTotals:
    """Aggregates over some set of rides. ``None`` means "not available"."""

    distance_m: float = 0.0
    ride_count: int = 0
    route_count: int = 0
    avg_distance_m: float | None = None
    calories_kcal: float | None = None


@dataclass
class RideTrack:
    """The three things the heat map needs from a ride.

    Plain data rather than the ``Ride`` row: state outlives the session that
    loaded it, and Reflex may persist state between events, so ORM instances
    have no business living here.
    """

    id: int = 0
    bike_id: int | None = None
    points: list[list[float]] = field(default_factory=list)


@dataclass
class BikeSummary:
    """One bike's card. Pre-formatted — cards render inside ``rx.foreach``."""

    id: int = 0
    name: str = ""
    distance_km: str = EMPTY
    ride_count: str = "0"
    route_count: str = "0"
    avg_km: str = EMPTY
    calories: str = EMPTY


def ride_totals(rides: Sequence[Ride]) -> RideTotals:
    """Total distance, rides, distinct routes, average and calories.

    Calories are nullable on a ride — a bike with no power meter records none
    — so an all-null set gives ``None`` rather than a confident zero, and the
    card shows an em dash (rule 13). A route is counted once however many
    times it was ridden, and rides with no route are not counted at all.
    """
    if not rides:
        return RideTotals()

    distance = sum(ride.distance_m for ride in rides)
    calories = [ride.calories_kcal for ride in rides if ride.calories_kcal is not None]
    routes = {ride.route_id for ride in rides if ride.route_id is not None}

    return RideTotals(
        distance_m=distance,
        ride_count=len(rides),
        route_count=len(routes),
        avg_distance_m=distance / len(rides),
        calories_kcal=sum(calories) if calories else None,
    )


def ride_tracks(rides: Sequence[Ride]) -> list[RideTrack]:
    """The heat map's input, detached from the ORM."""
    return [
        RideTrack(id=ride.id or 0, bike_id=ride.bike_id, points=list(ride.map_points))
        for ride in rides
        if ride.map_points
    ]


def dashboard_traces(
    tracks: Sequence[RideTrack],
    selected_bike_ids: Iterable[int],
) -> list[MapTrace]:
    """One translucent trace per ride, always — deselected ones are empty.

    Emitting every ride every time is what keeps the trace set constant across
    a filter toggle. Plotly matches traces by uid and a set that shrinks reads
    as traces destroyed, which on a MapLibre subplot means ``removeLayer``
    (AGENTS.md rule 31). ``build_map_figure`` emits a point-less trace as an
    empty one and leaves it out of the framing, so a blanked ride disappears
    from view without disappearing from the figure.

    Setting ``visible=False`` would *not* do: plotly's scattermap treats any
    trace whose ``visible`` is not ``True`` as hidden and removes its layers.

    Opacity comes from how many rides are actually drawn, so the heat stays
    legible whether one bike is selected or none.
    """
    selected = set(selected_bike_ids)
    drawn = list(tracks[:MAX_DASHBOARD_RIDES])
    shown = [t for t in drawn if not selected or t.bike_id in selected]
    opacity = heat_opacity(len(shown))
    visible = {t.id for t in shown}

    return [
        MapTrace(
            uid=f"ride-{track.id}",
            # `map_points` is a JSON column, so its entries are lists;
            # MapTrace wants the Point tuple alias.
            points=[(p[0], p[1]) for p in track.points] if track.id in visible else [],
            color=HEAT_COLOUR,
            width=4.0,
            opacity=opacity,
            # 250 named traces would stack 250 tooltips on one road.
            hover=False,
        )
        for track in drawn
    ]


class DashboardState(rx.State):
    loading: bool = True

    map_figure: dict = figure_to_dict(blank_map_figure(STYLE_LIGHT))
    map_key: str = "dashboard"
    has_track: bool = False
    truncated_note: str = ""

    # Bike ids whose rides the map is limited to. Empty means "everything".
    selected_bikes: list[int] = []

    bike_cards: list[BikeSummary] = []

    total_distance: str = EMPTY
    total_rides: str = "0"
    total_routes: str = "0"
    total_avg: str = EMPTY
    total_calories: str = EMPTY

    current_ftp: str = EMPTY
    current_ftp_date: str = ""
    power_zones: list[ZoneRow] = []
    hr_zones: list[ZoneRow] = []

    # Every ride with a track, kept so a filter toggle is a redraw rather than
    # another trip to the database.
    _tracks: list[RideTrack] = []

    @rx.var
    def has_bikes(self) -> bool:
        return len(self.bike_cards) > 0

    @rx.var
    def is_filtered(self) -> bool:
        return len(self.selected_bikes) > 0

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @rx.event
    def load(self):
        """Page ``on_load`` — totals, cards and the heat map.

        The bare ``yield`` after raising the flag is the rule 29 protocol: it
        ships as its own delta so the plot unmounts before the new figure
        arrives, and the flag is cleared as the last statement so the figure
        and the flag land together.
        """
        self.loading = True
        yield

        with rx.session() as session:
            # Rides in analysis only: the totals and the heat map describe
            # training. The odometer on the bikes page counts everything.
            rides = list(session.exec(analysis_rides().order_by(col(Ride.start_time))).all())
            bikes = list(session.exec(select(Bike).order_by(col(Bike.name))).all())
            evaluations = list(
                session.exec(select(FitnessEval).order_by(col(FitnessEval.valid_from))).all()
            )

        self._tracks = ride_tracks(rides)
        self._build_cards(rides, bikes)
        self._apply_fitness(evaluations)
        self._build_map()

        self.loading = False
        log.info("dashboard.load", rides=len(rides), bikes=len(bikes))

    def _build_cards(self, rides: Sequence[Ride], bikes: Sequence[Bike]) -> None:
        """Per-bike cards, and the totals across everything.

        The summary is deliberately **not** the sum of the cards: a ride with
        no bike assigned counts toward the totals but belongs to no card.
        """
        totals = ride_totals(rides)
        self.total_distance = format_distance_km(totals.distance_m)
        self.total_rides = str(totals.ride_count)
        self.total_routes = str(totals.route_count)
        self.total_avg = (
            format_distance_km(totals.avg_distance_m) if totals.avg_distance_m else EMPTY
        )
        self.total_calories = format_number(totals.calories_kcal) if totals.calories_kcal else EMPTY

        self.bike_cards = []
        for bike in bikes:
            own = [ride for ride in rides if ride.bike_id == bike.id]
            per_bike = ride_totals(own)
            self.bike_cards.append(
                BikeSummary(
                    id=bike.id or 0,
                    name=bike.name,
                    distance_km=format_distance_km(per_bike.distance_m) if own else EMPTY,
                    ride_count=str(per_bike.ride_count),
                    route_count=str(per_bike.route_count),
                    avg_km=(
                        format_distance_km(per_bike.avg_distance_m)
                        if per_bike.avg_distance_m
                        else EMPTY
                    ),
                    calories=(
                        format_number(per_bike.calories_kcal) if per_bike.calories_kcal else EMPTY
                    ),
                )
            )

    def _apply_fitness(self, evaluations: Sequence[FitnessEval]) -> None:
        """Current FTP and the zones that follow from it.

        A direct query rather than ``get_state(FitnessState)``: that state's
        load also builds three plotly figures this page has no use for.
        """
        latest: dict[str, FitnessEval] = {}
        for record in evaluations:  # ordered by valid_from, so the last wins
            latest[record.type] = record

        ftp = latest.get(EVAL_FTP)
        lthr = latest.get(EVAL_LTHR)

        self.current_ftp = format_number(ftp.value) if ftp else EMPTY
        self.current_ftp_date = format_date(ftp.valid_from) if ftp else ""
        self.power_zones = zone_rows(metrics.power_zones(ftp.value if ftp else 0.0))
        self.hr_zones = zone_rows(metrics.hr_zones(lthr.value if lthr else 0.0))

    # ------------------------------------------------------------------
    # Map
    # ------------------------------------------------------------------

    @rx.event
    def toggle_bike(self, bike_id: int):
        """Limit the map to this bike's rides, or stop limiting it.

        Only the map changes: the cards keep showing their own bike's numbers
        whatever is selected, because they are what the filter is chosen
        *from*.
        """
        if bike_id in self.selected_bikes:
            self.selected_bikes = [b for b in self.selected_bikes if b != bike_id]
        else:
            self.selected_bikes = [*self.selected_bikes, bike_id]
        self._build_map()

    def _build_map(self) -> None:
        traces = dashboard_traces(self._tracks, self.selected_bikes)
        self.has_track = any(trace.points for trace in traces)

        self.truncated_note = (
            f"Showing {MAX_DASHBOARD_RIDES} of {len(self._tracks)} rides."
            if len(self._tracks) > MAX_DASHBOARD_RIDES
            else ""
        )

        if not traces:
            self.map_figure = figure_to_dict(blank_map_figure(STYLE_LIGHT))
            return

        built = build_map_figure(
            traces,
            # Encodes the selection so the viewport re-frames onto the bike
            # being examined. Plotly keeps map.center/zoom while this is
            # unchanged, and None counts as unchanged (rule 19).
            uirevision=f"dashboard:{sorted(self.selected_bikes)}",
        )
        self.map_figure = figure_to_dict(built.figure)
