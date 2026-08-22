"""What the pages actually render, checked against a real imported ride.

The Reflex event machinery needs a runtime, so these drive the pure functions
the states delegate to — card building, stat formatting, chart and map figure
construction. Together with ``reflex compile`` (which validates the component
tree) that covers the path from a FIT file to the strings and figures on
screen without needing a browser.

Values are checked against ``docs/image.png``, which is the Karoo's own
rendering of the evening ride.
"""

from collections.abc import Iterator

import numpy as np
import pytest
from sqlmodel import Session, col, create_engine, select

from bike_tracker.components.map_view import (
    LAP_MARKER_COLOUR,
    MapMarker,
    MapTrace,
    build_map_figure,
)
from bike_tracker.components.ride_chart import (
    CHANNEL_SPECS,
    build_chart_figure,
    decode_chart_data,
    seconds_to_clock,
)
from bike_tracker.components.ride_stats import STAT_GROUPS
from bike_tracker.ingest.fit import parse
from bike_tracker.ingest.importer import import_ride
from bike_tracker.ingest.seed import seed_bikes
from bike_tracker.ingest.sources.file import import_file
from bike_tracker.models import Bike, Lap, MetricFields, Ride, RideStream, Route
from bike_tracker.states.ride_detail_state import _stats
from bike_tracker.states.rides_state import _to_card
from bike_tracker.states.route_detail_state import (
    _route_stats,
    heat_opacity,
)
from bike_tracker.states.route_detail_state import _to_row as _to_ride_row
from bike_tracker.states.routes_state import _to_card as _to_route_card
from bike_tracker.utils.formatting import (
    EMPTY,
    format_balance,
    format_distance_km,
    format_duration,
    format_speed_kmh,
)

from .conftest import EVENING_FIT

ImportedRide = tuple[Ride, list[Lap], RideStream | None, dict[int | None, str]]


@pytest.fixture
def imported(db_url: str) -> Iterator[ImportedRide]:
    """The evening ride, imported and read back with its laps and streams."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        ride = session.get(Ride, result.ride_id)
        assert ride is not None
        laps = list(
            session.exec(select(Lap).where(Lap.ride_id == ride.id).order_by(col(Lap.index))).all()
        )
        stream = session.exec(select(RideStream).where(RideStream.ride_id == ride.id)).first()
        bikes = {b.id: b.name for b in session.exec(select(Bike)).all()}
        yield ride, laps, stream, bikes
    engine.dispose()


# ---------------------------------------------------------------------------
# Model field parity
# ---------------------------------------------------------------------------

# Everything a ride has that a lap cannot: identity, provenance, relations, and
# the ride-level bookkeeping a lap has no business carrying.
RIDE_ONLY_FIELDS = frozenset(
    {
        "bike_id",
        "external_id",
        "fit_hash",
        "has_trailer",
        "imported_at",
        "map_points",
        "name",
        "num_laps",
        "preview_points",
        "route_id",
        "source",
        "sport",
        "stream_offset_s",
    }
)


def test_every_metric_field_exists_on_both_tables() -> None:
    """A lap must be able to fill the same stat cards a ride does."""
    shared = set(MetricFields.model_fields)
    assert shared <= set(Ride.model_fields)
    assert shared <= set(Lap.model_fields)
    assert len(shared) > 30, "the shared base looks suspiciously small"


def test_a_metric_added_to_ride_alone_is_a_failure() -> None:
    """Inheritance cannot stop someone adding a column straight to ``Ride``.

    Pinning the difference to an explicit list does. If this fails, the new
    field almost certainly belongs on ``MetricFields`` instead — and if it
    genuinely is ride-only, add it to ``RIDE_ONLY_FIELDS`` deliberately.
    """
    assert set(Ride.model_fields) - set(Lap.model_fields) == RIDE_ONLY_FIELDS
    assert set(Lap.model_fields) - set(Ride.model_fields) == {"ride_id", "index"}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_durations_read_as_durations_not_clock_times() -> None:
    """docs/image.png shows "44:24" and "00:20", not "00:44:24"."""
    assert format_duration(2664) == "44:24"
    assert format_duration(21) == "00:21"
    assert format_duration(10362) == "2:52:42"


def test_missing_values_render_as_an_em_dash() -> None:
    """A ride with no power meter is normal, not an error."""
    assert format_duration(None) == EMPTY
    assert format_speed_kmh(None) == EMPTY
    assert format_balance(None) == EMPTY


def test_distance_and_speed_convert_to_display_units() -> None:
    assert format_distance_km(19185.02) == "19.2"
    assert format_speed_kmh(7.202) == "25.9"


def test_balance_renders_as_a_left_right_pair() -> None:
    assert format_balance(51.67) == "52/48"


# ---------------------------------------------------------------------------
# Rides list cards
# ---------------------------------------------------------------------------


def test_a_ride_card_carries_everything_the_list_shows(imported: ImportedRide) -> None:
    ride, _laps, _stream, bikes = imported
    card = _to_card(ride, bikes)

    assert card.name == "Evening Ride"
    assert card.distance_km == "19.2"
    assert card.elevation_m == "87"
    assert card.duration == "44:24"
    assert card.bike_name == "Ideal Trekking"
    assert card.detail_href == f"/rides/{ride.id}"


def test_a_ride_card_carries_its_thumbnail_geometry(imported: ImportedRide) -> None:
    """Cards render inside rx.foreach, so this cannot be computed later."""
    ride, _laps, _stream, bikes = imported
    card = _to_card(ride, bikes)
    assert card.preview_points
    assert len(card.preview_points.split()) > 10


def test_an_unassigned_ride_says_so_rather_than_showing_a_blank(imported: ImportedRide) -> None:
    ride, _laps, _stream, _bikes = imported
    ride.bike_id = None
    assert _to_card(ride, {}).bike_name == "Unassigned"


# ---------------------------------------------------------------------------
# Stat cards
# ---------------------------------------------------------------------------


def test_every_stat_the_cards_ask_for_actually_exists(imported: ImportedRide) -> None:
    """The cards are a declarative table; a typo in a key would silently
    render an em dash forever. This is what catches that."""
    ride, _laps, _stream, _bikes = imported
    stats = _stats(ride)
    for group in STAT_GROUPS:
        for stat in group.stats:
            assert stat.key in stats, f"{group.title} -> {stat.key} has no value"


def test_the_stat_values_match_the_devices_own_rendering(imported: ImportedRide) -> None:
    """Straight from docs/image.png."""
    ride, _laps, _stream, _bikes = imported
    stats = _stats(ride)

    assert stats["distance"] == "19.2"
    assert stats["moving_time"] == "44:24"
    assert stats["paused_time"] == "00:21"
    assert stats["elevation_gain"] == "87"
    assert stats["elevation_loss"] == "229"
    assert stats["avg_grade"] == "-0.3"
    assert stats["max_grade"] == "8.9"
    assert stats["min_grade"] == "-9.1"
    assert stats["avg_speed"] == "25.9"
    assert stats["max_speed"] == "47.5"
    assert stats["avg_hr"] == "150"
    assert stats["max_hr"] == "168"
    assert stats["min_hr"] == "105"
    assert stats["avg_cadence"] == "83"
    assert stats["max_cadence"] == "98"
    assert stats["avg_power"] == "147"
    assert stats["max_power"] == "679"
    assert stats["normalized_power"] == "155"
    # The Karoo renders 0.68 from its own NP of ~155.5; ours is 155.18, so
    # the two land either side of the 0.675 rounding boundary. That is the
    # same 0.2% NP difference test_fit_roundtrip already accepts, surfacing at
    # two decimal places rather than a new discrepancy.
    assert stats["intensity_factor"] == "0.67"
    assert stats["tss"] == "34"
    assert stats["ftp"] == "230"
    # 392.6 kJ. The Karoo prints 392 kJ / 426 kcal; we round to 393 / 427.
    # A 0.3% difference in the work integral, the same order as the NP gap.
    assert stats["energy"] == "393"
    assert stats["calories"] == "427"
    assert stats["balance"] == "52/48"
    assert stats["avg_vam"] == "339"


def test_lap_stats_come_from_the_laps_own_slice(imported: ImportedRide) -> None:
    """A lap's numbers must describe the lap, not the ride it belongs to.

    This is the inverse of the test it replaces. That one asserted the values
    were em dashes, which merely pinned the gap; this asserts they are present
    **and different from the ride's**, so a regression that quietly falls back
    to whole-ride figures now fails instead of looking plausible.
    """
    ride, laps, _stream, _bikes = imported
    ride_stats = _stats(ride)
    lap_stats = _stats(laps[0])

    # Present where they used to be an em dash.
    for key in ("min_hr", "tss", "calories", "avg_vam", "min_power", "avg_temperature"):
        assert lap_stats[key] != EMPTY, f"{key} is still empty for a lap"

    # Lap-scoped, not borrowed.
    assert lap_stats["distance"] == "6.6"
    assert ride_stats["distance"] == "19.2"
    assert lap_stats["avg_vam"] != ride_stats["avg_vam"]
    assert lap_stats["avg_temperature"] != ride_stats["avg_temperature"]
    assert float(lap_stats["tss"]) < float(ride_stats["tss"])

    # Lap 1 is the fast opening descent and contains the ride's slowest heart
    # rate, so its floor legitimately equals the ride's — the later laps are
    # where a borrowed value would show up.
    assert lap_stats["min_hr"] == ride_stats["min_hr"] == "105"
    assert _stats(laps[1])["min_hr"] == "143"
    assert _stats(laps[2])["min_hr"] == "142"


def test_lap_values_still_match_the_device(imported: ImportedRide) -> None:
    """The FIT lap message is the reference wherever it has an opinion."""
    _ride, laps, _stream, _bikes = imported
    first = _stats(laps[0])

    assert first["avg_power"] == "136"
    assert first["max_power"] == "679"
    assert first["normalized_power"] == "150"
    assert first["avg_hr"] == "145"
    assert first["max_hr"] == "160"
    assert first["avg_cadence"] == "81"
    assert first["avg_speed"] == "30.3"
    assert first["elevation_gain"] == "20"


def test_lap_temperature_is_not_the_rides(imported: ImportedRide) -> None:
    """Temperature is session-only in FIT, so a lap's must be computed.

    The Karoo's sensor lags (brief §5.7): the first lap of the evening ride
    averages 25 °C while the ride averages 27 °C. Copying the ride value would
    hide exactly that.
    """
    ride, laps, _stream, _bikes = imported
    assert _stats(ride)["avg_temperature"] == "27"
    assert _stats(laps[0])["avg_temperature"] == "25"
    assert _stats(laps[-1])["avg_temperature"] == "28"


def test_every_lap_produces_a_full_stat_set(imported: ImportedRide) -> None:
    ride, laps, _stream, _bikes = imported
    keys = set(_stats(ride))
    for lap in laps:
        lap_stats = _stats(lap)
        assert set(lap_stats) == keys
        # Power-curve windows longer than the lap are legitimately empty —
        # `test_the_power_curve_card_empties_windows_that_do_not_fit` pins
        # that. Everything else must be populated.
        gaps = {
            key
            for key, value in lap_stats.items()
            if value == EMPTY and not key.startswith("best_power_")
        }
        assert not gaps, f"lap {lap.index} still has gaps: {sorted(gaps)}"


def test_the_power_curve_card_empties_windows_that_do_not_fit(
    imported: ImportedRide,
) -> None:
    """The em dash is the answer when there is no such window in the ride."""
    ride, laps, _stream, _bikes = imported
    ride_stats = _stats(ride)

    assert ride_stats["best_power_5s"] == "484"
    assert ride_stats["best_power_1200s"] == "155"
    assert ride_stats["best_power_3600s"] == EMPTY, "a 44-minute ride has no hour"

    # Lap 4 runs 450 s, so it has no ten-minute effort either.
    last = _stats(laps[-1])
    assert last["best_power_300s"] != EMPTY
    assert last["best_power_600s"] == EMPTY
    assert last["best_power_1200s"] == EMPTY


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------


def test_elapsed_seconds_become_readable_axis_labels() -> None:
    assert seconds_to_clock(0) == "1970-01-01T00:00:00"
    assert seconds_to_clock(2664) == "1970-01-01T00:44:24"
    assert seconds_to_clock(10362) == "1970-01-01T02:52:42"


def test_the_chart_always_draws_elevation(imported: ImportedRide) -> None:
    """Masterplan §2.4.2: the height profile is there whatever else is not."""
    _ride, _laps, stream, _bikes = imported
    figure = build_chart_figure(decode_chart_data(stream), [])

    assert len(figure.data) == 1
    assert figure.data[0].name == "Elevation"
    assert figure.data[0].fill == "tozeroy"


def test_toggling_a_card_adds_its_channel(imported: ImportedRide) -> None:
    _ride, _laps, stream, _bikes = imported
    figure = build_chart_figure(decode_chart_data(stream), ["power"])

    names = [trace.name for trace in figure.data]
    assert names == ["Elevation", "Power"]
    assert figure.data[1].yaxis == "y2"


def test_each_extra_channel_gets_its_own_hidden_axis(imported: ImportedRide) -> None:
    """Five units cannot share a scale, and five visible axes leave no plot."""
    _ride, _laps, stream, _bikes = imported
    figure = build_chart_figure(decode_chart_data(stream), ["power", "hr", "cadence"])

    assert [t.yaxis for t in figure.data] == [None, "y2", "y3", "y4"]
    for axis in ("yaxis2", "yaxis3", "yaxis4"):
        assert figure.layout[axis].overlaying == "y"
        assert figure.layout[axis].showticklabels is False


def test_speed_is_charted_in_the_unit_the_card_shows(imported: ImportedRide) -> None:
    """Stored in m/s, displayed in km/h — the two must not disagree."""
    _ride, _laps, stream, _bikes = imported
    figure = build_chart_figure(decode_chart_data(stream), ["speed"])
    # The chart is downsampled, so the exact 47.5 km/h peak sample may not
    # survive. What matters is the unit: ~47, not the stored ~13.2 m/s.
    assert 40.0 < float(np.nanmax(figure.data[1].y)) < 50.0


def test_the_chart_is_downsampled_for_the_wire(imported: ImportedRide) -> None:
    _ride, _laps, stream, _bikes = imported
    figure = build_chart_figure(decode_chart_data(stream), ["power"])
    assert len(figure.data[0].x) <= 1500


def test_a_selected_lap_is_highlighted_in_context(imported: ImportedRide) -> None:
    """The lap is shaded within the whole ride, not extracted from it."""
    _ride, _laps, stream, _bikes = imported
    figure = build_chart_figure(decode_chart_data(stream), [], lap_span=(0.0, 784.0))
    assert len(figure.layout.shapes) == 1


def test_a_ride_with_no_streams_still_produces_a_figure(imported: ImportedRide) -> None:
    """A turbo session with no sensors must not crash the detail page."""
    figure = build_chart_figure(decode_chart_data(None), ["power"])
    assert len(figure.data) == 0


def test_the_chart_channels_and_the_cards_agree(imported: ImportedRide) -> None:
    """Every toggleable card must name a channel the chart knows about."""
    for group in STAT_GROUPS:
        if group.channel:
            assert group.channel in CHANNEL_SPECS


def test_the_chart_axis_measures_riding_not_waiting(imported: ImportedRide) -> None:
    """A pause must cost the axis one sample, not its wall-clock length."""
    ride, _laps, stream, _bikes = imported
    data = decode_chart_data(stream)

    assert float(data.axis_s[-1]) == pytest.approx(ride.moving_time_s, abs=5.0)
    assert float(data.axis_s[-1]) < float(data.time_s[-1])


def test_the_axis_is_monotonic_and_starts_at_zero(imported: ImportedRide) -> None:
    _ride, _laps, stream, _bikes = imported
    axis = decode_chart_data(stream).axis_s
    assert axis[0] == 0.0
    assert bool(np.all(np.diff(axis) >= 0))


def test_a_lap_span_is_projected_onto_the_ride_time_axis(imported: ImportedRide) -> None:
    """Lap bounds arrive as elapsed seconds and must not be plotted raw."""
    _ride, _laps, stream, _bikes = imported
    data = decode_chart_data(stream)

    assert data.axis_at(0.0) == pytest.approx(0.0, abs=1.0)
    assert data.axis_at(float(data.time_s[-1])) == pytest.approx(float(data.axis_s[-1]), abs=1.0)
    # Halfway through the ride in wall-clock terms is at or before halfway in
    # riding terms, never after it.
    midpoint = float(data.time_s[-1]) / 2
    assert data.axis_at(midpoint) <= midpoint + 1.0


def test_a_chart_with_no_samples_has_an_empty_axis() -> None:
    data = decode_chart_data(None)
    assert data.axis_s.size == 0
    assert data.axis_at(10.0) == 0.0


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------


def test_the_map_frames_the_track(imported: ImportedRide) -> None:
    ride, _laps, _stream, _bikes = imported
    track = [(48.0, 11.0), (48.02, 11.03), (48.01, 11.05)]
    built = build_map_figure([MapTrace(points=track, name=ride.name)])

    assert not built.empty
    assert built.figure.data[0].type == "scattermap"
    assert 47.9 < built.center_lat < 48.1
    assert 10.9 < built.center_lon < 11.1
    assert 0 < built.zoom <= 16


def test_the_map_uses_a_token_free_style() -> None:
    """No account, no API key — the container has no outbound credentials."""
    built = build_map_figure([MapTrace(points=[(48.0, 11.0), (48.1, 11.1)])])
    assert built.figure.layout.map.style in {
        "carto-positron",
        "carto-darkmatter",
        "open-street-map",
    }


def test_a_ride_with_no_gps_produces_an_empty_map() -> None:
    assert build_map_figure([]).empty is True
    assert build_map_figure([MapTrace(points=[(48.0, 11.0)])]).empty is True


def test_the_heatmap_caps_how_many_tracks_it_draws() -> None:
    """Hundreds of tracks at once will stall any renderer."""
    traces = [
        MapTrace(points=[(48.0 + i * 0.001, 11.0), (48.0 + i * 0.001, 11.1)]) for i in range(400)
    ]
    assert len(build_map_figure(traces).figure.data) <= 250


def test_lap_markers_carry_the_lap_number() -> None:
    """docs/image_3.png: a purple circle on the route, white glyph inside."""
    built = build_map_figure(
        [MapTrace(points=[(48.0, 11.0), (48.1, 11.1)])],
        markers=[MapMarker(point=(48.05, 11.05), label="2", hover="Lap 2")],
    )
    marker_trace = built.figure.data[-1]

    assert marker_trace.mode == "markers+text"
    assert marker_trace.text == ("2",)
    assert marker_trace.marker.color == LAP_MARKER_COLOUR
    assert marker_trace.textfont.color == "white"


def test_focus_frames_the_lap_not_the_whole_track() -> None:
    """Selecting a lap must zoom to it while the ride stays drawn."""
    whole = [(48.0 + i * 0.002, 11.0) for i in range(50)]
    segment = whole[10:15]

    wide = build_map_figure([MapTrace(points=whole)])
    zoomed = build_map_figure([MapTrace(points=whole)], focus=segment)

    assert zoomed.zoom > wide.zoom
    assert zoomed.center_lat == pytest.approx((segment[0][0] + segment[-1][0]) / 2)
    # The full track is still drawn underneath.
    assert len(zoomed.figure.data) == 1


def test_uirevision_changes_with_the_selection() -> None:
    """Plotly keeps map.center/zoom while uirevision is unchanged.

    Without a token that moves, a new ride inherits the previous camera — the
    stale-map bug — and zooming to a lap silently does nothing.
    """
    trace = [MapTrace(points=[(48.0, 11.0), (48.1, 11.1)])]
    assert build_map_figure(trace, uirevision="7:-1").figure.layout.uirevision == "7:-1"
    assert build_map_figure(trace, uirevision="7:2").figure.layout.uirevision == "7:2"


def test_a_map_without_markers_draws_only_its_lines() -> None:
    built = build_map_figure([MapTrace(points=[(48.0, 11.0), (48.1, 11.1)])])
    assert all(t.mode == "lines" for t in built.figure.data)


# ---------------------------------------------------------------------------
# Routes views (masterplan §2.3)
# ---------------------------------------------------------------------------


def test_a_route_card_carries_everything_the_list_shows(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        ride = _get_ride(session, import_file(EVENING_FIT, session).ride_id)
        route = session.get(Route, ride.route_id)
        assert route is not None

        card = _to_route_card(route, ride.start_time)

    assert card.name == route.name
    assert card.distance_km == "19.2"
    assert card.elevation_m == "87"
    assert card.duration == "44:24"
    assert card.ride_count == 1
    assert card.ride_summary == "Ridden once"
    assert card.detail_href == f"/routes/{route.id}"
    # Projected from the stored polyline, not from a decompressed stream.
    assert card.preview_points
    assert len(card.preview_points.split()) > 10
    engine.dispose()


def test_route_cards_are_ordered_by_how_often_they_are_ridden() -> None:
    """The list should answer "which are my regulars", not "what was last"."""
    routes = [
        Route(id=1, name="Once", ride_count=1),
        Route(id=2, name="Thrice", ride_count=3),
        Route(id=3, name="Twice", ride_count=2),
    ]
    latest = {1: "2026-08-20", 2: "2020-01-01", 3: "2026-08-19"}

    pairs = [(r, latest[r.id or 0]) for r in routes]
    pairs.sort(key=lambda p: (p[0].ride_count, p[1] or ""), reverse=True)

    assert [r.name for r, _ in pairs] == ["Thrice", "Twice", "Once"]


def test_ties_go_to_the_most_recently_ridden() -> None:
    routes = [Route(id=1, name="Older", ride_count=2), Route(id=2, name="Newer", ride_count=2)]
    latest = {1: "2020-01-01", 2: "2026-08-19"}

    pairs = [(r, latest[r.id or 0]) for r in routes]
    pairs.sort(key=lambda p: (p[0].ride_count, p[1] or ""), reverse=True)

    assert [r.name for r, _ in pairs] == ["Newer", "Older"]


@pytest.mark.parametrize(
    ("rides", "expected"),
    [(1, 1.0), (2, 0.55), (3, 0.53), (10, 0.16), (30, 0.12), (100, 0.12)],
)
def test_heat_opacity_adapts_to_the_ride_count(rides: int, expected: float) -> None:
    """A constant alpha cannot serve one ride and thirty at once."""
    assert heat_opacity(rides) == pytest.approx(expected, abs=0.01)


def test_a_single_ride_route_is_drawn_solid() -> None:
    """With nothing to stack against, transparency only looks washed out."""
    assert heat_opacity(1) == 1.0
    assert heat_opacity(0) == 1.0


def test_heat_opacity_keeps_a_lone_strand_visible_against_the_spine() -> None:
    """The deviation the map exists to show must not vanish at any count."""
    for rides in (2, 3, 5, 10, 30):
        alpha = heat_opacity(rides)
        spine = 1 - (1 - alpha) ** rides
        assert spine - alpha > 0.2, f"no contrast at {rides} rides"


def test_a_ride_row_carries_what_differs_between_passes(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        ride = _get_ride(session, import_file(EVENING_FIT, session).ride_id)
        row = _to_ride_row(ride)

    assert row.duration == "44:24"
    assert row.avg_speed == "25.9"
    assert row.avg_power == "147"
    assert row.normalized_power == "155"
    assert row.best_20min == "155"
    assert row.tss == "34"
    assert row.avg_hr == "150"
    assert row.detail_href == f"/rides/{ride.id}"
    engine.dispose()


def test_route_stats_include_the_fastest_pass(db_url: str) -> None:
    """On a repeated route the best time is the number actually chased."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        first = _get_ride(session, import_file(EVENING_FIT, session).ride_id)

        repeat = parse(EVENING_FIT.read_bytes())
        repeat.fit_hash = "a-slower-lap"
        slower = import_ride(repeat, session, name="Evening Ride again").ride
        slower.moving_time_s = first.moving_time_s + 300
        session.add(slower)
        session.commit()

        route = session.get(Route, first.route_id)
        assert route is not None
        stats = _route_stats(route, [first, slower])

    assert stats["fastest"] == "44:24", "the faster of the two"
    assert stats["fastest_on"] == first.start_time.strftime("%d %b %Y")
    assert stats["avg_tss"] != EMPTY
    assert stats["best_tss"] != EMPTY
    engine.dispose()


def test_route_stats_of_a_route_with_no_rides_do_not_crash() -> None:
    """Unreachable once pruning works, but the card must not explode."""
    stats = _route_stats(Route(id=1, name="Empty", ride_count=0), [])
    assert stats["fastest"] == EMPTY
    assert stats["avg_tss"] == EMPTY


def _get_ride(session: Session, ride_id: int | None) -> Ride:
    assert ride_id is not None
    ride = session.get(Ride, ride_id)
    assert ride is not None
    return ride
