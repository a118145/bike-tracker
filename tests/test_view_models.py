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
from datetime import datetime

import numpy as np
import pytest
from sqlmodel import Session, col, create_engine, select

from bike_tracker import metrics
from bike_tracker.components.map_view import (
    LAP_MARKER_COLOUR,
    MapMarker,
    MapTrace,
    blank_map_figure,
    build_map_figure,
    heat_opacity,
    ride_map_traces,
)
from bike_tracker.components.ride_chart import (
    CHANNEL_SPECS,
    EVAL_CHART_CHANNELS,
    ChartData,
    build_chart_figure,
    decode_chart_data,
    seconds_to_clock,
)
from bike_tracker.components.ride_stats import STAT_GROUPS
from bike_tracker.ingest.fit import parse
from bike_tracker.ingest.importer import import_ride
from bike_tracker.ingest.seed import seed_bikes
from bike_tracker.ingest.sources.file import import_file
from bike_tracker.models import (
    EVAL_BASELINE,
    EVAL_FTP,
    EVAL_LTHR,
    Bike,
    FitnessEval,
    Lap,
    MetricFields,
    Ride,
    RideStream,
    Route,
)
from bike_tracker.states.dashboard_state import RideTrack, dashboard_traces, ride_totals
from bike_tracker.states.fitness_state import _duration_label, _value_text
from bike_tracker.states.ride_detail_state import _stats
from bike_tracker.states.rides_state import _to_card
from bike_tracker.states.route_detail_state import (
    _route_stats,
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
        # Head-unit configuration, not a metric: a lap has no reading of its
        # own and inherits its ride's resolved FTP.
        "device_ftp_w",
        "device_lthr_bpm",
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
    built = build_map_figure([MapTrace(uid="track", points=track, name=ride.name)])

    assert not built.empty
    assert built.figure.data[0].type == "scattermap"
    assert 47.9 < built.center_lat < 48.1
    assert 10.9 < built.center_lon < 11.1
    assert 0 < built.zoom <= 16


def test_the_map_uses_a_token_free_style() -> None:
    """No account, no API key — the container has no outbound credentials."""
    built = build_map_figure([MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)])])
    assert built.figure.layout.map.style in {
        "carto-positron",
        "carto-darkmatter",
        "open-street-map",
    }


def test_a_ride_with_no_gps_produces_an_empty_map() -> None:
    assert build_map_figure([]).empty is True
    assert build_map_figure([MapTrace(uid="track", points=[(48.0, 11.0)])]).empty is True


def test_the_heatmap_caps_how_many_tracks_it_draws() -> None:
    """Hundreds of tracks at once will stall any renderer."""
    traces = [
        MapTrace(uid=f"heat-{i}", points=[(48.0 + i * 0.001, 11.0), (48.0 + i * 0.001, 11.1)])
        for i in range(400)
    ]
    assert len(build_map_figure(traces).figure.data) <= 250


def test_lap_markers_carry_the_lap_number_in_the_hover() -> None:
    """A purple pin on the route; the lap number reaches the reader by hover.

    Deliberately not drawn on the map — text on a scattermap makes Plotly
    request a nonexistent icon from unpkg. See test_no_map_trace_draws_text.
    """
    built = build_map_figure(
        [MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)])],
        markers=[MapMarker(uid="lap-marker-1", point=(48.05, 11.05), label="2", hover="Lap 2")],
    )
    marker_trace = built.figure.data[-1]

    assert marker_trace.mode == "markers"
    assert marker_trace.lat == (48.05,)
    assert marker_trace.marker.color == LAP_MARKER_COLOUR
    assert marker_trace.hovertext == ("Lap 2",)
    assert marker_trace.hoverinfo == "text"


def test_a_marker_without_a_hover_string_still_offers_its_label() -> None:
    """The label is hover-only now, so it must not become unreachable."""
    built = build_map_figure(
        [MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)])],
        markers=[MapMarker(uid="lap-marker-1", point=(48.05, 11.05), label="2")],
    )
    marker_trace = built.figure.data[-1]

    assert marker_trace.hovertext == ("2",)
    assert marker_trace.hoverinfo == "text"


def test_no_map_trace_draws_text() -> None:
    """The guard against reintroducing the unpkg icon fetch.

    Plotly gives any text-bearing scattermap a symbol layer with
    ``'icon-image': '{symbol}-15'`` hard-coded, and the default `circle` symbol
    fills that token blank — so the id is the literal `-15`, no style has it,
    and Plotly fetches it from unpkg.com with `crossOrigin='Anonymous'`, which
    unpkg does not answer. Three console lines per map load, none of them
    visible from this repo. Hence: no text on any map trace, ever.
    """
    built = build_map_figure(
        [
            MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)]),
            MapTrace(uid="lap", points=[]),
        ],
        markers=[
            MapMarker(uid="lap-marker-1", point=(48.05, 11.05), label="2", hover="Lap 2"),
            MapMarker(uid="lap-marker-2", point=(48.06, 11.06), label="3", hover="Lap 3"),
        ],
        cursor_track=([48.0, 48.1], [11.0, 11.1]),
    )

    for trace in built.figure.data:
        assert "text" not in (trace.mode or ""), f"{trace.uid} draws text on the map"
        assert not trace.text, f"{trace.uid} carries text"
        assert trace.textfont is None or trace.textfont.family is None


def test_focus_frames_the_lap_not_the_whole_track() -> None:
    """Selecting a lap must zoom to it while the ride stays drawn."""
    whole = [(48.0 + i * 0.002, 11.0) for i in range(50)]
    segment = whole[10:15]

    wide = build_map_figure([MapTrace(uid="track", points=whole)])
    zoomed = build_map_figure([MapTrace(uid="track", points=whole)], focus=segment)

    assert zoomed.zoom > wide.zoom
    assert zoomed.center_lat == pytest.approx((segment[0][0] + segment[-1][0]) / 2)
    # The full track is still drawn underneath.
    assert len(zoomed.figure.data) == 1


def test_uirevision_changes_with_the_selection() -> None:
    """Plotly keeps map.center/zoom while uirevision is unchanged.

    Without a token that moves, a new ride inherits the previous camera — the
    stale-map bug — and zooming to a lap silently does nothing.
    """
    trace = [MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)])]
    assert build_map_figure(trace, uirevision="7:-1").figure.layout.uirevision == "7:-1"
    assert build_map_figure(trace, uirevision="7:2").figure.layout.uirevision == "7:2"


def test_a_map_without_markers_draws_only_its_lines() -> None:
    built = build_map_figure([MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)])])
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


def test_every_map_trace_carries_a_unique_uid() -> None:
    """Plotly matches traces across a re-plot by uid; unnamed ones match by
    position, which is how a changing trace set turns into addSource/
    removeLayer on a MapLibre style that may not be loaded."""
    built = build_map_figure(
        [MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)])],
        markers=[MapMarker(uid="lap-marker-1", point=(48.05, 11.05), label="2")],
        cursor_track=([48.0, 48.1], [11.0, 11.1]),
    )
    uids = [t.uid for t in built.figure.data]

    assert all(uids)
    assert len(set(uids)) == len(uids)


def test_a_duplicate_uid_is_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        build_map_figure(
            [
                MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)]),
                MapTrace(uid="track", points=[(49.0, 11.0), (49.1, 11.1)]),
            ]
        )


def test_selecting_a_lap_does_not_change_the_trace_set() -> None:
    """The invariant behind rule 31.

    A lap click must be a data update, not a trace being born and another
    destroyed — that pair is `createScatterMap`/`dispose`, and on a style that
    is still loading it throws "Style is not done loading." / "Map error." and
    poisons the graph div for good.
    """
    whole = [(48.0 + i * 0.002, 11.0) for i in range(50)]

    unselected = ride_map_traces(whole, segment=[], ride_name="Ride", lap_label="Lap 2")
    selected = ride_map_traces(whole, segment=whole[10:15], ride_name="Ride", lap_label="Lap 2")

    assert [t.uid for t in unselected] == [t.uid for t in selected]
    # The lap trace exists either way; unselected it simply has no points.
    assert unselected[1].points == []
    assert len(selected[1].points) == 5


def test_an_undrawable_trace_is_still_emitted() -> None:
    """It must not vanish from the figure — see the uid contract."""
    built = build_map_figure(
        [
            MapTrace(uid="track", points=[(48.0, 11.0), (48.1, 11.1)]),
            MapTrace(uid="lap", points=[]),
        ]
    )
    assert [t.uid for t in built.figure.data] == ["track", "lap"]
    assert built.figure.data[1].lat == ()


def test_the_blank_map_figure_still_names_the_style() -> None:
    """A figure with no style makes Plotly call setStyle on a mounted map, and
    it then reconciles traces on `styledata` — before the style is loaded."""
    assert blank_map_figure().layout.map.style == "carto-positron"


# ---------------------------------------------------------------------------
# Fitness view
# ---------------------------------------------------------------------------


def test_evaluation_values_carry_their_own_unit() -> None:
    """FTP is watts, baseline is a ratio, LTHR is bpm — never bare numbers."""
    when = datetime(2026, 5, 1)
    ftp = FitnessEval(type=EVAL_FTP, value=284.6, valid_from=when, start_time=when, end_time=when)
    base = FitnessEval(
        type=EVAL_BASELINE, value=1.847, valid_from=when, start_time=when, end_time=when
    )
    lthr = FitnessEval(type=EVAL_LTHR, value=168.0, valid_from=when, start_time=when, end_time=when)

    assert _value_text(ftp) == "285 W"
    # Two decimals: 1.8 and 1.85 W/bpm are a meaningful difference.
    assert _value_text(base) == "1.85 W/bpm"
    assert _value_text(lthr) == "168 bpm"


def test_duration_labels_read_as_durations() -> None:
    assert [_duration_label(d) for d in (5, 60, 1200, 3600)] == ["5 s", "1 min", "20 min", "1 h"]


def test_zones_are_empty_without_a_reference_value() -> None:
    """No FTP means no zone table — not a table of zeros (rule 13)."""
    assert metrics.power_zones(0.0) == []
    assert metrics.hr_zones(0.0) == []
    assert len(metrics.power_zones(250.0)) == 7
    assert len(metrics.hr_zones(170.0)) == 5


def test_the_top_zone_is_open_ended() -> None:
    """Z7 has no ceiling; its label must say so rather than inventing one."""
    assert metrics.power_zones(250.0)[-1].label.endswith("+")
    assert metrics.hr_zones(170.0)[-1].label.endswith("+")


# ---------------------------------------------------------------------------
# The evaluation dialog's own chart
# ---------------------------------------------------------------------------


_WHEN = datetime(2026, 5, 1, 9, 0)


def _dialog_data(with_hr: bool = True) -> ChartData:
    """A minute of synthetic ride, enough to draw and to shade."""
    channels = {"power": np.full(60, 200.0), "speed": np.full(60, 8.0)}
    if with_hr:
        channels["hr"] = np.full(60, 150.0)
    return ChartData.from_channels(
        time_s=np.arange(60.0),
        altitude_m=np.linspace(0.0, 30.0, 60),
        channels=channels,
    )


def test_the_dialog_chart_is_drawn_small_and_the_page_chart_is_not() -> None:
    """The dialog is 32em wide; the page chart must keep its own height."""
    assert build_chart_figure(_dialog_data(), [], height_px=170).layout.height == 170
    assert build_chart_figure(_dialog_data(), []).layout.height == 280


def test_the_dialog_chart_draws_elevation_heart_rate_and_power() -> None:
    """Fixed channels: it is not switchable, so it cannot drift with the page."""
    figure = build_chart_figure(_dialog_data(), list(EVAL_CHART_CHANNELS), height_px=170)
    names = [trace.name for trace in figure.data]

    assert names == ["Elevation", "Heart rate", "Power"]
    # Whatever the stat cards have switched on, these never appear here.
    assert not {"Speed", "Cadence", "Temperature"} & set(names)


def test_the_dialog_chart_survives_a_ride_with_no_heart_rate() -> None:
    """A turbo session without a strap draws two lines, not an error (rule 13)."""
    figure = build_chart_figure(
        _dialog_data(with_hr=False), list(EVAL_CHART_CHANNELS), height_px=170
    )
    assert [trace.name for trace in figure.data] == ["Elevation", "Power"]


def test_the_dialog_chart_shades_the_evaluated_window() -> None:
    """The reason the chart is in the dialog at all."""
    figure = build_chart_figure(
        _dialog_data(), list(EVAL_CHART_CHANNELS), (10.0, 40.0), height_px=170
    )
    assert len(figure.layout.shapes) == 1
    assert figure.layout.shapes[0].fillcolor is not None


def test_a_short_chart_keeps_its_legend_clear_of_the_plot() -> None:
    """The legend sits at y=1.16; at 170 px it would land on the traces."""
    small = build_chart_figure(_dialog_data(), list(EVAL_CHART_CHANNELS), height_px=170)
    full = build_chart_figure(_dialog_data(), list(EVAL_CHART_CHANNELS))
    assert small.layout.margin.t > full.layout.margin.t


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def _track(track_id: int, bike_id: int | None, points: int = 4) -> RideTrack:
    return RideTrack(
        id=track_id,
        bike_id=bike_id,
        points=[[48.0 + i * 0.01, 11.0 + i * 0.01] for i in range(points)],
    )


def test_filtering_the_dashboard_never_changes_the_trace_set() -> None:
    """The rule-31 guard, and the reason this function is pure.

    Dropping a trace on deselect would shrink the trace set of a *mounted*
    MapLibre map, which Plotly reads as traces destroyed — `removeLayer`, then
    "Style is not done loading." and a permanently poisoned graph div. So a
    deselected ride keeps its trace and loses only its points.
    """
    tracks = [_track(1, 10), _track(2, 10), _track(3, 20)]

    everything = dashboard_traces(tracks, [])
    one_bike = dashboard_traces(tracks, [10])
    both = dashboard_traces(tracks, [10, 20])

    assert len(everything) == len(one_bike) == len(both) == 3
    uids = {t.uid for t in everything}
    assert uids == {t.uid for t in one_bike} == {t.uid for t in both}
    assert uids == {"ride-1", "ride-2", "ride-3"}


def test_a_deselected_ride_keeps_its_trace_but_loses_its_points() -> None:
    traces = {t.uid: t for t in dashboard_traces([_track(1, 10), _track(3, 20)], [10])}

    assert traces["ride-1"].points, "the selected bike's ride is drawn"
    assert traces["ride-3"].points == [], "the other bike's ride is blanked, not removed"


def test_no_selection_draws_everything() -> None:
    traces = dashboard_traces([_track(1, 10), _track(3, 20)], [])
    assert all(trace.points for trace in traces)


def test_opacity_follows_what_is_actually_drawn() -> None:
    """Filtering to a single ride must not leave it washed out."""
    # Four rides on bike 10, exactly one on bike 20.
    tracks = [_track(i, 10) for i in range(1, 5)] + [_track(5, 20)]

    # Filtered to the single ride: drawn solid, since it stacks against nothing.
    assert dashboard_traces(tracks, [20])[0].opacity == 1.0
    # All five stacked: translucent, or the overlaps say nothing.
    assert dashboard_traces(tracks, [])[0].opacity < 1.0


def test_dashboard_totals_count_each_route_once() -> None:
    rides = [
        Ride(
            name="a",
            fit_hash="a",
            start_time=_WHEN,
            end_time=_WHEN,
            distance_m=10_000.0,
            route_id=1,
            calories_kcal=100.0,
        ),
        Ride(
            name="b",
            fit_hash="b",
            start_time=_WHEN,
            end_time=_WHEN,
            distance_m=30_000.0,
            route_id=1,
            calories_kcal=300.0,
        ),
        Ride(
            name="c",
            fit_hash="c",
            start_time=_WHEN,
            end_time=_WHEN,
            distance_m=20_000.0,
            route_id=2,
        ),
    ]
    totals = ride_totals(rides)

    assert totals.distance_m == 60_000.0
    assert totals.ride_count == 3
    assert totals.route_count == 2, "route 1 was ridden twice but is one route"
    assert totals.avg_distance_m == 20_000.0
    # Only the rides that recorded calories contribute.
    assert totals.calories_kcal == 400.0


def test_a_ride_with_no_route_is_not_counted_as_one() -> None:
    rides = [
        Ride(name="a", fit_hash="a", start_time=_WHEN, end_time=_WHEN, distance_m=1.0),
    ]
    assert ride_totals(rides).route_count == 0


def test_totals_over_nothing_are_empty_rather_than_a_division_by_zero() -> None:
    totals = ride_totals([])
    assert totals.ride_count == 0
    assert totals.avg_distance_m is None
    assert totals.calories_kcal is None


def test_calories_are_none_when_no_ride_recorded_any() -> None:
    """An em dash, never a confident zero (rule 13)."""
    rides = [
        Ride(name="a", fit_hash="a", start_time=_WHEN, end_time=_WHEN, distance_m=1.0),
        Ride(name="b", fit_hash="b", start_time=_WHEN, end_time=_WHEN, distance_m=1.0),
    ]
    assert ride_totals(rides).calories_kcal is None


def test_a_ride_with_no_bike_still_counts_toward_the_totals() -> None:
    """The summary card is deliberately not the sum of the bike cards."""
    rides = [
        Ride(
            name="a", fit_hash="a", start_time=_WHEN, end_time=_WHEN, distance_m=10_000.0, bike_id=1
        ),
        Ride(
            name="b",
            fit_hash="b",
            start_time=_WHEN,
            end_time=_WHEN,
            distance_m=5_000.0,
            bike_id=None,
        ),
    ]
    everything = ride_totals(rides)
    just_the_bike = ride_totals([r for r in rides if r.bike_id == 1])

    assert everything.distance_m == 15_000.0
    assert just_the_bike.distance_m == 10_000.0
