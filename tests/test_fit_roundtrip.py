"""Computed metrics against the values a real Karoo wrote.

This is the load-bearing test of the milestone. Every derived number the app
shows is checked against the same number the head unit recorded in the
``session`` message of the same file. If these agree, the pages built on top
of them are showing the truth; if they do not, everything downstream is
quietly wrong.

**On tolerances.** A disagreement here is a bug in ``metrics/``, not a
rounding artefact, and the tolerances are tight enough to say so — normalized
power is held to 1%. The two exceptions are documented where they are set:
TSS, where the device uses a slightly shorter duration than total timer time,
and elevation, where the device's figure comes from a different data source
altogether (see ``metrics/elevation.py``).
"""

import itertools
from pathlib import Path

import numpy as np
import pytest
from sqlmodel import Session, col, create_engine, select

from bike_tracker import metrics
from bike_tracker.ingest import streams
from bike_tracker.ingest.fit import ParsedRide, name_from_filename, parse
from bike_tracker.ingest.importer import import_ride, refresh_route_stats
from bike_tracker.ingest.seed import seed_bikes
from bike_tracker.ingest.sources.file import import_file
from bike_tracker.models import Bike, Lap, MetricFields, Ride, RideStream, Route

from .conftest import ALL_FIT_FILES, EVENING_FIT, MORNING_FIT

# Normalized power is a pure computation over the recorded samples; ours and
# the device's must agree closely or one of them is wrong.
NP_TOLERANCE = 0.01

# The device's TSS implies a duration ~1-2% shorter than the total timer time
# the same file reports (2610 s against 2664 s on the evening ride). Since NP
# and IF match to within 0.2%, that difference is entirely the device's choice
# of duration, not an error in the formula.
TSS_TOLERANCE = 0.05

# Ascent is not reproducible from the record altitude channel at all — the
# Karoo accumulates it live from unrounded barometric samples and only then
# writes a 0.2 m-quantised trace into the records. This bound checks the
# algorithm is in the right territory, nothing more; the stored value is
# copied from the FIT file. See metrics/elevation.py.
ELEVATION_TOLERANCE = 0.40


def _channel(parsed: ParsedRide, name: str) -> metrics.FloatArray:
    return metrics.as_float_array(parsed.channels.get(name, []))


def _relative(computed: float, reference: float) -> float:
    return abs(computed - reference) / abs(reference)


def _get[T](session: Session, model: type[T], row_id: int | None) -> T:
    """``session.get`` that fails the test instead of returning None.

    Every use here is fetching a row the test just created, so a None is a
    broken test rather than a case to handle — and asserting it once keeps the
    real assertions readable.
    """
    assert row_id is not None
    row = session.get(model, row_id)
    assert row is not None, f"{model.__name__} {row_id} was not stored"
    return row


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_every_reference_file_parses(path: Path) -> None:
    parsed = parse(path.read_bytes())
    assert parsed.sample_count > 1000
    assert parsed.session, "no session message"
    assert parsed.start_time < parsed.end_time


def test_semicircles_are_converted_to_degrees(evening_ride: ParsedRide) -> None:
    """FIT stores coordinates as a 32-bit sweep; raw values are ~5.8e8."""
    lat = _channel(evening_ride, "lat")
    finite = lat[np.isfinite(lat)]
    assert 47.0 < float(finite[0]) < 56.0, "latitude is not in degrees"


def test_a_lost_gps_fix_is_missing_not_zero(evening_ride: ParsedRide) -> None:
    """This ride has no satellite lock for its first nine samples.

    Those must read as NaN. Coercing them to 0.0 would put the start of the
    ride in the Gulf of Guinea and wreck both the map viewport and the route
    match.
    """
    lat = _channel(evening_ride, "lat")
    assert np.isnan(lat[0])
    assert np.isfinite(lat).sum() == 2653


def test_the_ride_name_comes_from_the_filename() -> None:
    """FIT activity files have no name field; the Karoo puts it in the name."""
    assert name_from_filename("Karoo-Evening_Ride-2026-08-19-1723.fit") == "Evening Ride"
    assert name_from_filename("Karoo-Morning_Ride-2025-06-14-0856.fit") == "Morning Ride"


def test_an_unrecognised_filename_yields_no_name() -> None:
    assert name_from_filename("activity.fit") is None


def test_repeated_device_records_collapse_to_real_sensors(evening_ride: ParsedRide) -> None:
    """The Karoo re-emits its own creator record whenever the battery drops."""
    assert evening_ride.ant_ids == [44050, 13522]


def test_the_road_bike_file_carries_the_sram_ant_ids(morning_ride: ParsedRide) -> None:
    """Brief §7 listed the SRAM crank's ANT+ id as still to be recorded.

    It is in this file: 10517 is the power meter, 23828 the eTap group.
    """
    assert 10517 in morning_ride.ant_ids
    assert 23828 in morning_ride.ant_ids


# ---------------------------------------------------------------------------
# Metrics against the device
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_normalized_power_matches_the_device(path: Path) -> None:
    parsed = parse(path.read_bytes())
    reference = parsed.session["normalized_power"]
    computed = metrics.normalized_power(_channel(parsed, "power_w"))
    assert _relative(computed, reference) < NP_TOLERANCE, (
        f"NP {computed:.1f} W vs FIT {reference} W"
    )


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_intensity_factor_matches_the_device(path: Path) -> None:
    parsed = parse(path.read_bytes())
    ftp = parsed.session["threshold_power"]
    computed = metrics.intensity_factor(metrics.normalized_power(_channel(parsed, "power_w")), ftp)
    assert computed == pytest.approx(parsed.session["intensity_factor"], abs=0.005)


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_tss_matches_the_device(path: Path) -> None:
    parsed = parse(path.read_bytes())
    ftp = parsed.session["threshold_power"]
    np_w = metrics.normalized_power(_channel(parsed, "power_w"))
    computed = metrics.tss(
        np_w, metrics.intensity_factor(np_w, ftp), parsed.session["total_timer_time"]
    )
    assert _relative(computed, parsed.session["training_stress_score"]) < TSS_TOLERANCE


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_average_power_matches_the_device(path: Path) -> None:
    parsed = parse(path.read_bytes())
    mean, _low, _high = metrics.channel_stats(_channel(parsed, "power_w"))
    assert mean is not None
    assert round(mean) == pytest.approx(parsed.session["avg_power"], abs=1)


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_max_power_matches_the_device(path: Path) -> None:
    parsed = parse(path.read_bytes())
    _mean, _low, high = metrics.channel_stats(_channel(parsed, "power_w"))
    assert high == parsed.session["max_power"]


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_heart_rate_extremes_match_the_device(path: Path) -> None:
    parsed = parse(path.read_bytes())
    mean, _low, high = metrics.channel_stats(_channel(parsed, "hr_bpm"))
    assert mean is not None
    assert round(mean) == pytest.approx(parsed.session["avg_heart_rate"], abs=1)
    assert high == parsed.session["max_heart_rate"]


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_speed_extremes_match_the_device(path: Path) -> None:
    parsed = parse(path.read_bytes())
    _mean, _low, high = metrics.channel_stats(_channel(parsed, "speed_ms"))
    assert high == pytest.approx(parsed.session["enhanced_max_speed"], abs=0.01)


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_altitude_extremes_match_the_device(path: Path) -> None:
    parsed = parse(path.read_bytes())
    _mean, low, high = metrics.channel_stats(_channel(parsed, "altitude_m"))
    assert low == pytest.approx(parsed.session["enhanced_min_altitude"], abs=0.1)
    assert high == pytest.approx(parsed.session["enhanced_max_altitude"], abs=0.1)


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_elevation_gain_is_in_the_right_territory(path: Path) -> None:
    """A sanity bound, not an equality — read the tolerance comment above."""
    parsed = parse(path.read_bytes())
    gain, loss = metrics.elevation_gain_loss(_channel(parsed, "altitude_m"))
    assert _relative(gain, parsed.session["total_ascent"]) < ELEVATION_TOLERANCE
    assert _relative(loss, parsed.session["total_descent"]) < ELEVATION_TOLERANCE


@pytest.mark.parametrize("path", ALL_FIT_FILES, ids=lambda p: p.stem)
def test_the_moving_time_axis_matches_the_devices_timer(path: Path) -> None:
    """The chart's x-axis must measure riding, not waiting.

    Capping each sample interval at the sampling period reproduces the
    device's own ``total_timer_time`` — including on the morning ride, which
    contains a 29-minute stop that wall-clock elapsed time would show as a
    flat line across a sixth of the chart.
    """
    parsed = parse(path.read_bytes())
    axis = metrics.moving_time_axis(np.asarray(parsed.time_offsets_s()))
    assert float(axis[-1]) == pytest.approx(parsed.session["total_timer_time"], abs=5.0)


def test_the_moving_time_axis_is_shorter_than_elapsed_when_paused(
    morning_ride: ParsedRide,
) -> None:
    elapsed = np.asarray(morning_ride.time_offsets_s())
    axis = metrics.moving_time_axis(elapsed)
    assert float(axis[-1]) < float(elapsed[-1]) - 2000.0


def test_vam_matches_the_device(evening_ride: ParsedRide) -> None:
    """docs/image.png reports 340 m/h for this ride."""
    computed = metrics.vam(
        _channel(evening_ride, "altitude_m"),
        np.asarray(evening_ride.time_offsets_s()),
    )
    assert computed == pytest.approx(340.0, rel=0.05)


def test_work_and_calories_match_the_device(evening_ride: ParsedRide) -> None:
    """docs/image.png reports 392 kJ and 426 kcal for this ride."""
    work = metrics.total_work_kj(
        _channel(evening_ride, "power_w"),
        np.asarray(evening_ride.time_offsets_s()),
    )
    assert work == pytest.approx(392.0, abs=2.0)
    assert metrics.calories_kcal(work) == pytest.approx(426.0, abs=3.0)


def test_grade_extremes_match_the_device(evening_ride: ParsedRide) -> None:
    """docs/image.png reports +8.9% / -9.1% / Ø -0.3% for this ride."""
    mean, low, high = metrics.channel_stats(_channel(evening_ride, "grade_pct"))
    assert high == pytest.approx(8.9, abs=0.1)
    assert low == pytest.approx(-9.1, abs=0.1)
    assert mean == pytest.approx(-0.3, abs=0.1)


def test_power_balance_matches_the_device(evening_ride: ParsedRide) -> None:
    """docs/image.png reports 52/48 for this dual-sided power meter."""
    mean, _low, _high = metrics.channel_stats(_channel(evening_ride, "left_balance_pct"))
    assert mean is not None
    assert round(mean) == 52


def test_the_single_sided_crank_reports_no_balance(morning_ride: ParsedRide) -> None:
    """Absence must stay absent rather than becoming a fabricated 50/50."""
    assert "left_balance_pct" not in morning_ride.channels


# ---------------------------------------------------------------------------
# Stream storage
# ---------------------------------------------------------------------------


def test_streams_round_trip_through_compression(evening_ride: ParsedRide) -> None:
    original = _channel(evening_ride, "power_w")
    restored = streams.decode(streams.encode(original))
    assert restored.shape == original.shape
    np.testing.assert_allclose(restored, original, rtol=1e-6)


def test_stream_compression_actually_compresses(evening_ride: ParsedRide) -> None:
    """The whole reason for one blob per channel instead of one row per sample."""
    blob = streams.encode(_channel(evening_ride, "altitude_m"))
    assert blob is not None
    assert len(blob) < evening_ride.sample_count * 4 / 2


def test_missing_samples_survive_as_nan(evening_ride: ParsedRide) -> None:
    """float32 is used precisely so a dropped sample can still say "missing"."""
    restored = streams.decode(streams.encode(_channel(evening_ride, "lat")))
    assert np.isnan(restored[0])


def test_a_channel_with_no_data_is_stored_as_null() -> None:
    assert streams.encode([None, None, None]) is None
    assert streams.encode([]) is None


def test_decoding_a_missing_channel_gives_an_empty_array() -> None:
    assert streams.decode(None).size == 0


def test_downsampling_caps_the_point_count() -> None:
    assert len(streams.downsample_indices(10_000, 1500)) <= 1500
    assert len(streams.downsample_indices(500, 1500)) == 500


# ---------------------------------------------------------------------------
# Full import
# ---------------------------------------------------------------------------


def test_import_stores_the_device_figures_and_our_own(db_url: str) -> None:
    """End to end: the numbers on the ride row are the numbers on the device."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        assert result.ok and result.created

        ride = _get(session, Ride, result.ride_id)

        # Copied from the FIT session (masterplan §3.3).
        assert ride.distance_m == pytest.approx(19185.02, abs=0.1)
        assert ride.moving_time_s == 2664.0
        assert ride.paused_time_s == pytest.approx(21.0)
        assert ride.elevation_gain_m == 87.0
        assert ride.elevation_loss_m == 229.0
        assert ride.avg_power_w == 147
        assert ride.max_power_w == 679

        # Computed by us.
        assert ride.normalized_power_w == 155
        assert ride.intensity_factor == pytest.approx(0.675, abs=0.005)
        assert ride.tss == pytest.approx(33.7, abs=0.5)
        assert ride.energy_kj == pytest.approx(392, abs=2)
        assert ride.calories_kcal == pytest.approx(426, abs=3)
        assert ride.avg_vam_mph == pytest.approx(340, rel=0.05)
        assert ride.left_balance_pct == pytest.approx(51.7, abs=0.1)
        assert ride.decoupling_pct is not None
    engine.dispose()


def test_import_detects_the_bike_from_its_power_meter(db_url: str) -> None:
    """Each file is matched to its own bike despite a shared HR strap."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        evening = import_file(EVENING_FIT, session)
        morning = import_file(MORNING_FIT, session)

        bikes = {b.id: b.name for b in session.exec(select(Bike)).all()}
        assert bikes[_get(session, Ride, evening.ride_id).bike_id] == "Ideal Trekking"
        assert bikes[_get(session, Ride, morning.ride_id).bike_id] == "Canyon CF SL 7"
    engine.dispose()


def test_the_shared_chest_strap_alone_identifies_nothing(db_url: str) -> None:
    """44050 is on both bikes; matching on it would make every ride ambiguous.

    Stripping the power meter from the file must leave the ride unassigned
    rather than picking whichever bike the query happened to return first.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        parsed = parse(EVENING_FIT.read_bytes())
        parsed.devices = [d for d in parsed.devices if d.ant_device_number != 13522]

        result = import_ride(parsed, session, name="Ambiguous")
        assert result.ride.bike_id is None
    engine.dispose()


def test_import_is_idempotent(db_url: str) -> None:
    """Re-importing the same file is a no-op, not a duplicate ride.

    FIT files are never persisted, so the hash of the bytes is the only thing
    left that can recognise a re-import.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        first = import_file(EVENING_FIT, session)
        second = import_file(EVENING_FIT, session)

        assert first.created is True
        assert second.created is False
        assert first.ride_id == second.ride_id
        assert len(session.exec(select(Ride)).all()) == 1
    engine.dispose()


def test_import_stores_laps_and_streams(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)

        laps = session.exec(
            select(Lap).where(Lap.ride_id == result.ride_id).order_by(col(Lap.index))
        ).all()
        assert [lap.index for lap in laps] == [0, 1, 2, 3]
        assert laps[0].distance_m == pytest.approx(6580.89, abs=0.1)

        stream = session.exec(
            select(RideStream).where(RideStream.ride_id == result.ride_id)
        ).first()
        assert stream is not None
        assert stream.sample_count == 2662
        assert streams.decode(stream.power_w).size == 2662
        # Stored per sample so a lap can have its own balance, not the ride's.
        assert streams.decode(stream.left_balance_pct).size == 2662
    engine.dispose()


def test_laps_carry_the_full_metric_set(db_url: str) -> None:
    """Selecting a lap must not leave the page full of em dashes.

    Every metric column a ride has, a lap has too, computed over that lap's
    slice of the streams.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        laps = session.exec(
            select(Lap).where(Lap.ride_id == result.ride_id).order_by(col(Lap.index))
        ).all()

        ride = _get(session, Ride, result.ride_id)
        for lap in laps:
            for name in MetricFields.model_fields:
                # Wherever the ride has a value the lap must have one too.
                # Two exceptions, both meaningful rather than gaps:
                #   * fields None on the ride as well (lthr_at_time_bpm, with
                #     no LTHR evaluation on record);
                #   * power-curve windows longer than the lap, which have no
                #     answer — asserted precisely below instead.
                if getattr(ride, name) is None or name.startswith("best_power_"):
                    continue
                assert getattr(lap, name) is not None, f"lap {lap.index} has no {name}"
    engine.dispose()


def test_the_power_curve_covers_exactly_the_windows_that_fit(db_url: str) -> None:
    """A window longer than the lap has no answer, and must say so.

    Reporting a partial window would claim a sustained effort over more time
    than the rider actually spent.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        ride = _get(session, Ride, result.ride_id)
        laps = session.exec(
            select(Lap).where(Lap.ride_id == ride.id).order_by(col(Lap.index))
        ).all()

        for window in metrics.POWER_CURVE_WINDOWS_S:
            column = f"best_power_{window}s_w"
            for row in (ride, *laps):
                value = getattr(row, column)
                if window <= row.moving_time_s:
                    assert value is not None, f"{column} missing with {row.moving_time_s:.0f}s"
                    assert value > 0
                else:
                    assert value is None, f"{column} invented a value for {window}s"

        # The 44-minute ride has no hour; its 450 s final lap has no 10 minutes.
        assert ride.best_power_3600s_w is None
        assert ride.best_power_1200s_w is not None
        assert laps[-1].best_power_600s_w is None


def test_the_power_curve_decreases_with_window_length(db_url: str) -> None:
    """Nobody holds their sprint wattage for twenty minutes."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        ride = _get(session, Ride, import_file(MORNING_FIT, session).ride_id)
        values = [getattr(ride, f"best_power_{w}s_w") for w in metrics.POWER_CURVE_WINDOWS_S]
        assert all(v is not None for v in values), "the 2h12 ride fits every window"
        assert all(a >= b for a, b in itertools.pairwise(values))
    engine.dispose()


def test_lap_metrics_match_the_device(db_url: str) -> None:
    """Per lap, our numbers agree with the FIT lap message."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        laps = session.exec(
            select(Lap).where(Lap.ride_id == result.ride_id).order_by(col(Lap.index))
        ).all()

        # Copied straight from the FIT lap message: (avg_power, max_power,
        # avg_hr, max_hr, avg_cadence).
        assert [
            (lap.avg_power_w, lap.max_power_w, lap.avg_hr_bpm, lap.max_hr_bpm, lap.avg_cadence_rpm)
            for lap in laps
        ] == [
            (136, 679, 145, 160, 81),
            (149, 364, 151, 161, 83),
            (156, 432, 154, 162, 84),
            (148, 325, 152, 168, 82),
        ]

        # Normalized power is always computed, never copied — the morning
        # ride's lap message has no `normalized_power` at all. It must still
        # land on the device's figure where the device does report one.
        for lap, device_np in zip(laps, (150, 155, 160, 154), strict=True):
            assert lap.normalized_power_w is not None
            assert abs(lap.normalized_power_w - device_np) / device_np < 0.01
    engine.dispose()


def test_lap_totals_reconcile_with_the_ride(db_url: str) -> None:
    """The parts must add up to the whole, or the slicing is wrong."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        ride = _get(session, Ride, result.ride_id)
        laps = session.exec(select(Lap).where(Lap.ride_id == ride.id)).all()

        assert sum(lap.distance_m for lap in laps) == pytest.approx(ride.distance_m, rel=0.01)
        assert sum(lap.moving_time_s for lap in laps) == pytest.approx(ride.moving_time_s, abs=2)
        assert sum(lap.energy_kj or 0 for lap in laps) == pytest.approx(ride.energy_kj, rel=0.02)
        assert sum(lap.tss or 0 for lap in laps) == pytest.approx(ride.tss, rel=0.02)
    engine.dispose()


def test_a_lap_is_measured_over_its_own_window(db_url: str) -> None:
    """Values the FIT lap message does not carry must still be lap-scoped.

    Temperature is the sharpest case: it is session-only in FIT, so a lap's
    average can only come from the slice. The Karoo's sensor lags (brief
    §5.7), so the first lap really is cooler than the last.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        laps = session.exec(
            select(Lap).where(Lap.ride_id == result.ride_id).order_by(col(Lap.index))
        ).all()

        first_temp = laps[0].avg_temperature_c
        last_temp = laps[-1].avg_temperature_c
        assert first_temp is not None and last_temp is not None
        assert first_temp < last_temp
        # Minimum heart rate differs per lap; a borrowed value would not.
        assert len({lap.min_hr_bpm for lap in laps}) > 1
    engine.dispose()


def test_the_single_lap_of_a_one_lap_ride_equals_the_ride(db_url: str) -> None:
    """The morning ride has one lap covering everything — a free invariant."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(MORNING_FIT, session)
        ride = _get(session, Ride, result.ride_id)
        lap = session.exec(select(Lap).where(Lap.ride_id == ride.id)).one()

        assert lap.distance_m == pytest.approx(ride.distance_m)
        assert lap.avg_power_w == ride.avg_power_w
        assert lap.normalized_power_w == ride.normalized_power_w
        assert lap.moving_time_s == ride.moving_time_s
    engine.dispose()


def test_the_stream_origin_offset_is_recorded(db_url: str) -> None:
    """Session start and first record differ by 2 s on the evening ride.

    Without the column, every lap window sliced from the stored streams would
    be shifted by that much.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        ride = _get(session, Ride, import_file(EVENING_FIT, session).ride_id)
        assert ride.stream_offset_s == pytest.approx(2.0)
    engine.dispose()


def test_import_creates_a_route_and_a_preview(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        ride = _get(session, Ride, result.ride_id)

        assert ride.route_id is not None
        assert ride.preview_points, "list cards need precomputed thumbnail geometry"

        route = _get(session, Route, ride.route_id)
        assert route.ride_count == 1
        assert route.avg_distance_m == pytest.approx(ride.distance_m)
        assert 10 < len(route.points) < 1000, "route geometry must be simplified"
    engine.dispose()


def test_the_ride_track_is_stored_for_the_route_heat_map(db_url: str) -> None:
    """Rebuilding this per page load costs ~20 ms a ride; storing it costs none."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        ride = _get(session, Ride, import_file(EVENING_FIT, session).ride_id)

        assert 10 < len(ride.map_points) < 2000
        lats = [p[0] for p in ride.map_points]
        lons = [p[1] for p in ride.map_points]
        assert all(47.0 < lat < 56.0 for lat in lats), "not real latitudes"
        assert all(5.0 < lon < 16.0 for lon in lons), "not real longitudes"
        # Simplified, not the raw stream.
        assert len(ride.map_points) < 500
    engine.dispose()


def test_moving_the_only_ride_off_a_route_deletes_it(db_url: str) -> None:
    """A route with no rides is not an empty route; it should not exist."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        first = _get(session, Ride, import_file(EVENING_FIT, session).ride_id)
        second = _get(session, Ride, import_file(MORNING_FIT, session).ride_id)
        lonely = first.route_id
        target = second.route_id
        assert lonely is not None and target is not None and lonely != target

        first.route_id = target
        session.add(first)
        session.commit()

        assert refresh_route_stats(session, _get(session, Route, lonely)) is None
        assert session.get(Route, lonely) is None
    engine.dispose()


def test_moving_a_ride_off_a_shared_route_keeps_it(db_url: str) -> None:
    """Only the last ride leaving should prune; the route survives otherwise."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        first = _get(session, Ride, import_file(EVENING_FIT, session).ride_id)

        repeat = parse(EVENING_FIT.read_bytes())
        repeat.fit_hash = "a-second-lap-of-the-same-route"
        second = import_ride(repeat, session, name="Evening Ride again").ride
        assert second.route_id == first.route_id
        shared = first.route_id
        assert shared is not None

        first.route_id = None
        session.add(first)
        session.commit()

        survivor = refresh_route_stats(session, _get(session, Route, shared))
        assert survivor is not None
        assert survivor.ride_count == 1
        assert survivor.avg_distance_m == pytest.approx(second.distance_m)
    engine.dispose()


def test_importing_never_prunes_the_route_it_just_created(db_url: str) -> None:
    """assign_route commits the ride first, so the new route is never empty."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        ride = _get(session, Ride, import_file(EVENING_FIT, session).ride_id)
        assert ride.route_id is not None
        assert session.get(Route, ride.route_id) is not None
    engine.dispose()


def test_two_different_rides_do_not_share_a_route(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        evening = _get(session, Ride, import_file(EVENING_FIT, session).ride_id)
        morning = _get(session, Ride, import_file(MORNING_FIT, session).ride_id)
        assert evening.route_id != morning.route_id
    engine.dispose()


def test_the_same_ride_rematches_its_own_route(db_url: str) -> None:
    """The matcher must recognise a repeat of a route it already knows."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        first = _get(session, Ride, import_file(EVENING_FIT, session).ride_id)

        # Same track, different file identity — a second ride along that route.
        repeat = parse(EVENING_FIT.read_bytes())
        repeat.fit_hash = "repeat-of-the-same-route"
        second = import_ride(repeat, session, name="Evening Ride again").ride

        assert second.route_id == first.route_id
        route = _get(session, Route, first.route_id)
        assert route.ride_count == 2
    engine.dispose()


def test_import_puts_the_distance_on_the_odometer(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        result = import_file(EVENING_FIT, session)
        ride = _get(session, Ride, result.ride_id)
        bike = _get(session, Bike, ride.bike_id)
        assert bike.odo_m == pytest.approx(ride.distance_m)
    engine.dispose()


def test_ftp_falls_back_to_the_head_units_own_setting(db_url: str) -> None:
    """With no evaluation on record yet, the device's threshold_power is used.

    Without this the very first import would have a null TSS column.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        ride = _get(session, Ride, import_file(EVENING_FIT, session).ride_id)
        assert ride.ftp_at_time_w == 230
        assert ride.load_source == "power"
        assert ride.power_available is True
    engine.dispose()


@pytest.mark.parametrize(
    ("path", "expected_ftp"), [(EVENING_FIT, 230), (MORNING_FIT, 250)], ids=lambda v: str(v)
)
def test_import_stores_what_the_head_unit_was_configured_with(
    db_url: str, path: Path, expected_ftp: int
) -> None:
    """The device FTP has to survive the file, which is not kept (§1.3).

    It is the second step of the fallback chain: without a stored copy,
    deleting the last evaluation leaves the ride quoting a number backed by
    nothing. See ``ingest.importer.resolve_fitness``.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        ride = _get(session, Ride, import_file(path, session).ride_id)
        assert ride.device_ftp_w == expected_ftp
        # The Karoo writes no threshold heart rate. `max_heart_rate` is a
        # different quantity and must not be borrowed for it.
        assert ride.device_lthr_bpm is None
        assert parse(path.read_bytes()).session.get("max_heart_rate") is not None
    engine.dispose()


def test_import_reports_no_cross_check_warnings(db_url: str) -> None:
    """The headline claim of this milestone: our maths agrees with the device."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        for path in ALL_FIT_FILES:
            outcome = import_file(path, session)
            assert outcome.warnings == [], f"{path.name}: {outcome.warnings}"
    engine.dispose()
