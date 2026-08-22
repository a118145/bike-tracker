"""Pure metric functions.

No app, no database, no fixtures beyond plain arrays — that is the whole point
of the ``metrics`` import boundary. These pin the *behaviour* of each function
on inputs whose right answer can be worked out by hand;
``test_fit_roundtrip.py`` pins them against a real device instead.
"""

import itertools
import math

import numpy as np
import pytest

from bike_tracker import metrics
from bike_tracker.metrics import geo  # noqa: F401  (for geo.TILE_SIZE_PX in the fit helpers)

# ---------------------------------------------------------------------------
# Array helpers
# ---------------------------------------------------------------------------


def test_none_becomes_nan_not_zero() -> None:
    """A dropped sample must not be averaged in as a zero."""
    values = metrics.as_float_array([100, None, 200])
    assert np.isnan(values[1])
    mean, _low, _high = metrics.channel_stats(values)
    assert mean == 150.0


def test_channel_stats_on_an_empty_channel() -> None:
    assert metrics.channel_stats(np.empty(0)) == (None, None, None)


def test_channel_stats_when_every_sample_is_missing() -> None:
    assert metrics.channel_stats(metrics.as_float_array([None, None])) == (None, None, None)


def test_rolling_mean_drops_partial_windows() -> None:
    result = metrics.rolling_mean(np.array([1.0, 2.0, 3.0, 4.0]), 2)
    assert result.tolist() == [1.5, 2.5, 3.5]


def test_rolling_mean_shorter_than_window_is_empty() -> None:
    assert metrics.rolling_mean(np.array([1.0, 2.0]), 5).size == 0


def test_centered_mean_preserves_length_and_endpoints() -> None:
    """Zero-padding here would invent a ramp at each end of an altitude trace."""
    values = np.full(10, 200.0)
    smoothed = metrics.centered_mean(values, 5)
    assert len(smoothed) == 10
    assert smoothed[0] == pytest.approx(200.0)
    assert smoothed[-1] == pytest.approx(200.0)


def test_sample_intervals_clips_recording_pauses() -> None:
    """A 30-minute stop must not be credited at the wattage before it."""
    times = np.array([0.0, 1.0, 2.0, 1800.0])
    assert metrics.sample_intervals(times, max_gap_s=10.0).tolist() == [1.0, 1.0, 1.0, 10.0]


def test_mean_active_ignores_coasting() -> None:
    """Average cadence must not count the samples where nobody pedalled."""
    assert metrics.mean_active(np.array([0.0, 80.0, 90.0, 0.0])) == pytest.approx(85.0)
    assert np.mean(np.array([0.0, 80.0, 90.0, 0.0])) == pytest.approx(42.5)


def test_mean_active_of_an_all_zero_channel_is_none() -> None:
    assert metrics.mean_active(np.zeros(10)) is None
    assert metrics.mean_active(np.empty(0)) is None


def test_moving_time_axis_squeezes_out_a_pause() -> None:
    """A 97-second stop must cost one sample of axis, not 97 seconds of it."""
    axis = metrics.moving_time_axis(np.array([0.0, 1.0, 2.0, 3.0, 100.0, 101.0]))
    assert axis.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_moving_time_axis_matches_elapsed_when_nothing_was_paused() -> None:
    time_s = np.arange(100, dtype=float)
    np.testing.assert_allclose(metrics.moving_time_axis(time_s), time_s)


def test_moving_time_axis_starts_at_zero_and_keeps_its_length() -> None:
    time_s = np.array([17.0, 18.0, 25.0, 26.0])
    axis = metrics.moving_time_axis(time_s)
    assert len(axis) == len(time_s)
    assert axis[0] == 0.0


def test_moving_time_axis_of_degenerate_input() -> None:
    assert metrics.moving_time_axis(np.empty(0)).size == 0
    assert metrics.moving_time_axis(np.array([5.0])).tolist() == [0.0]


def test_window_indices_selects_the_right_samples() -> None:
    time_s = np.arange(10, dtype=float)
    assert metrics.window_indices(time_s, 2.0, 5.0) == (2, 6)


def test_window_indices_clamps_a_start_before_the_first_sample() -> None:
    """A lap can start before the first record — by 2 s on the evening ride.

    An unclamped subtraction would give a negative index, which silently wraps
    to the end of the array instead of failing.
    """
    time_s = np.arange(10, dtype=float)
    assert metrics.window_indices(time_s, -5.0, 2.0) == (0, 3)


def test_window_indices_clamps_an_end_past_the_last_sample() -> None:
    time_s = np.arange(10, dtype=float)
    assert metrics.window_indices(time_s, 8.0, 999.0) == (8, 10)


def test_window_indices_of_a_window_containing_nothing() -> None:
    time_s = np.array([0.0, 1.0, 2.0])
    i0, i1 = metrics.window_indices(time_s, 1.5, 1.6)
    assert i0 == i1


def test_window_indices_of_an_empty_ride() -> None:
    assert metrics.window_indices(np.empty(0), 0.0, 10.0) == (0, 0)


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


def test_normalized_power_of_constant_power_is_that_power() -> None:
    """The fourth-power weighting has nothing to bite on when power is steady."""
    assert metrics.normalized_power(np.full(600, 200.0)) == pytest.approx(200.0)


def test_normalized_power_exceeds_average_when_power_varies() -> None:
    """Variable power is harder than its average — that is what NP measures."""
    alternating = np.tile(np.concatenate([np.full(60, 100.0), np.full(60, 300.0)]), 10)
    assert metrics.normalized_power(alternating) > alternating.mean()


def test_normalized_power_counts_coasting_as_zero() -> None:
    """Descents are real zero-power time and must stay in the average."""
    with_zeros = np.concatenate([np.full(300, 200.0), np.zeros(300)])
    assert metrics.normalized_power(with_zeros) < 200.0


def test_normalized_power_treats_missing_samples_as_zero() -> None:
    gaps = metrics.as_float_array([None] * 300 + [200.0] * 300)
    assert metrics.normalized_power(gaps) == pytest.approx(
        metrics.normalized_power(np.concatenate([np.zeros(300), np.full(300, 200.0)]))
    )


def test_normalized_power_of_a_ride_shorter_than_one_window() -> None:
    """Falls back to the plain mean rather than returning zero."""
    assert metrics.normalized_power(np.full(10, 150.0)) == pytest.approx(150.0)


def test_normalized_power_of_nothing_is_zero() -> None:
    assert metrics.normalized_power(np.empty(0)) == 0.0


def test_intensity_factor_is_np_over_ftp() -> None:
    assert metrics.intensity_factor(230.0, 250.0) == pytest.approx(0.92)


def test_intensity_factor_without_an_ftp_is_zero() -> None:
    assert metrics.intensity_factor(230.0, 0.0) == 0.0


def test_one_hour_at_threshold_is_100_tss() -> None:
    """The defining case: TSS is calibrated so an hour at FTP scores 100."""
    assert metrics.tss(250.0, 1.0, 3600.0) == pytest.approx(100.0)


def test_tss_scales_with_duration_and_intensity_squared() -> None:
    assert metrics.tss(125.0, 0.5, 3600.0) == pytest.approx(25.0)
    assert metrics.tss(250.0, 1.0, 1800.0) == pytest.approx(50.0)


def test_tss_of_a_ride_with_no_power_is_zero() -> None:
    assert metrics.tss(0.0, 0.0, 3600.0) == 0.0


def test_one_hour_at_lthr_is_100_hr_tss() -> None:
    assert metrics.hr_tss(np.full(3600, 160.0), 160.0, 3600.0) == pytest.approx(100.0)


def test_hr_tss_ignores_missing_beats() -> None:
    hr = metrics.as_float_array([160.0, None, 160.0])
    assert metrics.hr_tss(hr, 160.0, 3600.0) == pytest.approx(100.0)


def test_decoupling_is_zero_when_efficiency_holds() -> None:
    power = np.full(100, 200.0)
    hr = np.full(100, 150.0)
    assert metrics.decoupling(power, hr) == pytest.approx(0.0)


def test_decoupling_is_positive_when_heart_rate_drifts_up() -> None:
    """Same power, higher heart rate in the second half — the classic drift."""
    power = np.full(100, 200.0)
    hr = np.concatenate([np.full(50, 150.0), np.full(50, 165.0)])
    result = metrics.decoupling(power, hr)
    assert result is not None
    assert result == pytest.approx(9.09, abs=0.05)


def test_decoupling_of_too_few_samples_is_none() -> None:
    assert metrics.decoupling(np.array([1.0]), np.array([1.0])) is None


def test_work_integrates_power_over_real_sample_times() -> None:
    """100 W held for 10 s is 1 kJ."""
    power = np.full(11, 100.0)
    time_s = np.arange(11, dtype=float)
    assert metrics.total_work_kj(power, time_s) == pytest.approx(1.0, abs=0.15)


def test_work_does_not_charge_a_pause_at_the_preceding_wattage() -> None:
    steady = metrics.total_work_kj(np.full(3, 100.0), np.array([0.0, 1.0, 2.0]))
    paused = metrics.total_work_kj(np.full(3, 100.0), np.array([0.0, 1.0, 1800.0]))
    assert paused < steady + 1.0


def test_calories_use_gross_cycling_efficiency() -> None:
    """392 kJ of work is the 426 kcal the reference ride reports."""
    assert metrics.calories_kcal(392.0) == pytest.approx(426.0, abs=1.0)


def test_best_window_finds_the_effort_near_the_guess() -> None:
    power = np.full(4000, 150.0)
    power[1000:2200] = 300.0  # a 20-minute effort starting at 1000 s
    window = metrics.best_20min_window(power, guess_frac=1100 / 4000)
    assert window is not None
    assert window.avg_power_w == pytest.approx(300.0)
    assert window.start_index == pytest.approx(1000, abs=5)


def test_best_window_ignores_efforts_outside_the_search_span() -> None:
    """Searching the whole ride would return the sprint on the way home."""
    power = np.full(8000, 150.0)
    power[200:1400] = 400.0  # a big effort at the very start
    window = metrics.best_20min_window(power, guess_frac=0.9, search_s=1200.0)
    assert window is not None
    assert window.avg_power_w < 400.0


def test_best_window_needs_a_ride_at_least_as_long_as_the_window() -> None:
    assert metrics.best_20min_window(np.full(100, 200.0)) is None


def test_best_average_power_finds_the_hardest_window() -> None:
    """A 30 s effort at 400 W is the best 30 s of the ride, wherever it sits."""
    power = np.concatenate([np.full(60, 100.0), np.full(30, 400.0), np.full(300, 150.0)])
    assert metrics.best_average_power(power, 30) == pytest.approx(400.0)


def test_best_average_power_dilutes_over_a_longer_window() -> None:
    """A 30 s spike cannot hold its wattage across a minute."""
    power = np.concatenate([np.full(60, 100.0), np.full(30, 400.0), np.full(300, 150.0)])
    assert metrics.best_average_power(power, 60) == pytest.approx(275.0)


def test_best_average_power_of_steady_power_is_that_power() -> None:
    assert metrics.best_average_power(np.full(600, 200.0), 300) == pytest.approx(200.0)


def test_best_average_power_is_none_when_the_window_does_not_fit() -> None:
    """No value to report is not the same as zero — the card shows an em dash."""
    assert metrics.best_average_power(np.full(100, 250.0), 3600) is None
    assert metrics.best_average_power(np.empty(0), 5) is None


def test_best_average_power_searches_the_whole_ride() -> None:
    """Unlike best_20min_window, which only looks near a user's guess."""
    power = np.full(4000, 100.0)
    power[3900:3950] = 500.0  # a sprint at the very end
    assert metrics.best_average_power(power, 30) == pytest.approx(500.0)


def test_best_average_power_counts_coasting_as_zero() -> None:
    power = np.concatenate([np.full(30, 300.0), np.zeros(30)])
    assert metrics.best_average_power(power, 60) == pytest.approx(150.0)


def test_the_power_curve_decreases_as_the_window_grows() -> None:
    """The defining shape: nobody sustains their sprint wattage for an hour."""
    rng = np.random.default_rng(0)
    power = np.abs(rng.normal(200, 80, 7200))
    curve = metrics.power_curve(power)
    values = [v for v in curve.values() if v is not None]
    assert len(values) == len(metrics.POWER_CURVE_WINDOWS_S)
    assert all(a >= b for a, b in itertools.pairwise(values))


def test_the_power_curve_drops_windows_that_do_not_fit() -> None:
    curve = metrics.power_curve(np.full(700, 200.0))
    assert curve[600] == pytest.approx(200.0)
    assert curve[1200] is None
    assert curve[3600] is None


def test_baseline_is_watts_per_beat() -> None:
    assert metrics.baseline(np.full(60, 180.0), np.full(60, 120.0)) == pytest.approx(1.5)


def test_baseline_without_heart_rate_is_none() -> None:
    assert metrics.baseline(np.full(60, 180.0), metrics.as_float_array([None] * 60)) is None


# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------


def test_elevation_ignores_noise_below_the_threshold() -> None:
    """Barometric jitter around a constant altitude is not climbing."""
    jitter = 200.0 + np.tile([0.0, 0.6, -0.6, 0.3], 250)
    gain, loss = metrics.elevation_gain_loss(jitter, threshold_m=1.5)
    assert gain == pytest.approx(0.0, abs=1.0)
    assert loss == pytest.approx(0.0, abs=1.0)


def test_elevation_counts_a_real_climb_in_full() -> None:
    profile = np.concatenate([np.linspace(100, 200, 500), np.linspace(200, 150, 250)])
    gain, loss = metrics.elevation_gain_loss(profile)
    assert gain == pytest.approx(100.0, abs=2.0)
    assert loss == pytest.approx(50.0, abs=2.0)


def test_elevation_commits_the_leg_still_running_at_the_finish() -> None:
    """A ride that ends mid-climb must still count that climb."""
    gain, _loss = metrics.elevation_gain_loss(np.linspace(100.0, 180.0, 400))
    assert gain == pytest.approx(80.0, abs=2.0)


def test_elevation_of_a_flat_ride_is_zero() -> None:
    assert metrics.elevation_gain_loss(np.full(100, 42.0)) == (0.0, 0.0)


def test_vam_measures_climbing_time_not_ride_time() -> None:
    """500 m climbed in an hour, then an hour on the flat, is still 500 m/h."""
    profile = np.concatenate([np.linspace(0, 500, 3600), np.full(3600, 500.0)])
    time_s = np.arange(7200, dtype=float)
    assert metrics.vam(profile, time_s) == pytest.approx(500.0, rel=0.1)


def test_vam_of_a_flat_ride_is_zero() -> None:
    assert metrics.vam(np.full(100, 10.0), np.arange(100, dtype=float)) == 0.0


def test_grade_is_rise_over_run() -> None:
    """A 5 m climb over 100 m is 5%."""
    altitude = np.linspace(0, 50, 1001)
    distance = np.linspace(0, 1000, 1001)
    percent = metrics.grade(altitude, distance)
    assert np.nanmean(percent) == pytest.approx(5.0, abs=0.2)


def test_grade_is_nan_where_the_bike_did_not_move() -> None:
    altitude = np.array([100.0, 101.0, 102.0])
    distance = np.array([0.0, 0.0, 0.0])
    assert np.isnan(metrics.grade(altitude, distance)).all()


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_haversine_against_a_known_distance() -> None:
    """One degree of latitude is about 111 km anywhere on Earth."""
    assert metrics.haversine_m((48.0, 11.0), (49.0, 11.0)) == pytest.approx(111_195, rel=0.01)


def test_simplify_collapses_a_straight_line_to_its_endpoints() -> None:
    straight = [(48.0 + i * 0.001, 11.0) for i in range(50)]
    assert metrics.simplify_polyline(straight, epsilon_m=10.0) == [straight[0], straight[-1]]


def test_simplify_keeps_a_corner() -> None:
    corner = [(48.0, 11.0), (48.01, 11.0), (48.01, 11.01)]
    assert len(metrics.simplify_polyline(corner, epsilon_m=10.0)) == 3


def test_simplify_never_drops_the_endpoints() -> None:
    track = [(48.0 + i * 0.0001, 11.0 + (i % 3) * 0.00001) for i in range(500)]
    simplified = metrics.simplify_polyline(track, epsilon_m=50.0)
    assert simplified[0] == track[0]
    assert simplified[-1] == track[-1]


def test_simplify_handles_a_long_track_without_recursing() -> None:
    """A 10,000-point track must not blow the interpreter stack."""
    track = [(48.0 + i * 0.00001, 11.0 + math.sin(i / 50) * 0.001) for i in range(10_000)]
    assert len(metrics.simplify_polyline(track, epsilon_m=5.0)) < len(track)


def test_resample_returns_exactly_the_requested_count() -> None:
    track = [(48.0 + i * 0.001, 11.0) for i in range(37)]
    assert len(metrics.resample_polyline(track, 100)) == 100


def test_a_route_matches_itself_perfectly() -> None:
    track = [(48.0 + i * 0.0005, 11.0 + i * 0.0005) for i in range(200)]
    assert metrics.route_match_score(track, track) == pytest.approx(1.0)


def test_a_route_matches_a_slightly_wobbly_repeat() -> None:
    """GPS wanders by tens of metres; the same road must still match."""
    track = [(48.0 + i * 0.0005, 11.0 + i * 0.0005) for i in range(200)]
    wobbled = [(lat + 0.0002 * math.sin(i / 7), lon) for i, (lat, lon) in enumerate(track)]
    assert metrics.route_match_score(track, wobbled) > 0.9


def test_different_routes_do_not_match() -> None:
    north = [(48.0 + i * 0.0005, 11.0) for i in range(200)]
    east = [(48.0, 11.0 + i * 0.0005) for i in range(200)]
    assert metrics.route_match_score(north, east) < 0.2


def test_match_score_is_symmetric() -> None:
    a = [(48.0 + i * 0.0005, 11.0 + i * 0.0003) for i in range(200)]
    b = [(48.0 + i * 0.0005, 11.0 + i * 0.0004) for i in range(200)]
    assert metrics.route_match_score(a, b) == pytest.approx(metrics.route_match_score(b, a))


def test_a_short_ride_inside_a_long_one_does_not_score_perfectly() -> None:
    """Containment is not equality — this is why the score takes the minimum."""
    long_route = [(48.0 + i * 0.0005, 11.0) for i in range(400)]
    short_route = long_route[:80]
    assert metrics.route_match_score(short_route, long_route) < 0.5


def test_viewport_centres_on_the_track() -> None:
    lat, lon, zoom = metrics.bounds_to_center_zoom([(48.0, 11.0), (48.2, 11.4)])
    assert lat == pytest.approx(48.1)
    assert lon == pytest.approx(11.2)
    assert 0 < zoom < 16


def test_a_smaller_track_gets_a_closer_zoom() -> None:
    wide = metrics.bounds_to_center_zoom([(48.0, 11.0), (49.0, 12.0)])[2]
    tight = metrics.bounds_to_center_zoom([(48.0, 11.0), (48.01, 11.01)])[2]
    assert tight > wide


def test_viewport_of_a_single_point_does_not_divide_by_zero() -> None:
    """No span in either axis; must return a finite zoom, not an infinity."""
    _lat, _lon, zoom = metrics.bounds_to_center_zoom([(48.0, 11.0)])
    assert math.isfinite(zoom)


def _visible_span(zoom: float, width_px: int, height_px: int) -> tuple[float, float]:
    """Degrees of longitude and Mercator units of latitude a viewport shows.

    The inverse of what :func:`bounds_to_center_zoom` solves, so a test can ask
    the question that actually matters — does the track fit? — instead of
    re-deriving the same formula and agreeing with itself.
    """
    scale = 360.0 / (metrics.geo.TILE_SIZE_PX * 2**zoom)
    return width_px * scale, height_px * scale


def _fits(points: list[tuple[float, float]], width_px: int, height_px: int) -> tuple[bool, bool]:
    _lat, _lon, zoom = metrics.bounds_to_center_zoom(points, width_px=width_px, height_px=height_px)
    visible_lon, visible_lat = _visible_span(zoom, width_px, height_px)
    min_lat, min_lon, max_lat, max_lon = metrics.bounding_box(points)
    needed_lon = max_lon - min_lon
    needed_lat = metrics.geo._mercator_y(max_lat) - metrics.geo._mercator_y(min_lat)
    return needed_lon <= visible_lon, needed_lat <= visible_lat


@pytest.mark.parametrize(
    "track",
    [
        pytest.param([(48.0, 11.0), (48.05, 11.05)], id="diagonal"),
        pytest.param([(48.0, 11.0), (48.30, 11.01)], id="tall-and-narrow"),
        pytest.param([(48.0, 11.0), (48.01, 11.40)], id="wide-and-flat"),
        pytest.param([(48.0, 11.0), (48.10, 11.10)], id="square"),
        pytest.param([(60.0, 11.0), (60.10, 11.10)], id="far-north"),
        pytest.param([(0.5, 11.0), (0.6, 11.10)], id="near-equator"),
    ],
)
def test_the_viewport_never_clips_the_track(track: list[tuple[float, float]]) -> None:
    """The regression guard for the map framing bug.

    Assuming 256 px tiles (the Google/Leaflet convention) instead of
    MapLibre's 512 put every zoom one level too high, so a track needed 174% of
    the viewport height — clipped top and bottom on every ride. This inverts
    the maths and demands the bounding box actually fit, in both axes.
    """
    fits_wide, fits_tall = _fits(track, 900, 460)
    assert fits_wide, "track is clipped left/right"
    assert fits_tall, "track is clipped top/bottom"


def test_the_viewport_fits_a_wide_map_and_a_square_one() -> None:
    """Aspect ratio must not change whether the track fits, only the margins."""
    track = [(48.0, 11.0), (48.10, 11.02)]
    for width, height in ((900, 460), (460, 460), (1600, 400), (400, 900)):
        fits_wide, fits_tall = _fits(track, width, height)
        assert fits_wide and fits_tall, f"clipped at {width}x{height}"


def test_the_viewport_leaves_a_margin() -> None:
    """`padding` should leave the track short of the edges, not touching."""
    track = [(48.0, 11.0), (48.10, 11.10)]
    _lat, _lon, zoom = metrics.bounds_to_center_zoom(track, width_px=900, height_px=460)
    _visible_lon, visible_lat = _visible_span(zoom, 900, 460)
    needed_lat = metrics.geo._mercator_y(48.10) - metrics.geo._mercator_y(48.0)
    assert 0.80 < needed_lat / visible_lat < 0.95


def test_viewport_of_an_empty_track() -> None:
    assert metrics.bounds_to_center_zoom([]) == (0.0, 0.0, 1.0)


def test_svg_projection_preserves_aspect_ratio() -> None:
    """A track twice as wide as it is tall must not come out square."""
    track = [(48.0, 11.0), (48.0, 11.02), (48.005, 11.02), (48.005, 11.0), (48.0, 11.0)]
    pairs = [p.split(",") for p in metrics.project_svg_points(track).split()]
    xs = [float(x) for x, _ in pairs]
    ys = [float(y) for _, y in pairs]
    assert (max(xs) - min(xs)) > (max(ys) - min(ys)) * 1.5


def test_svg_projection_stays_inside_the_viewbox() -> None:
    track = [(48.0 + i * 0.001, 11.0 + i * 0.002) for i in range(50)]
    for pair in metrics.project_svg_points(track).split():
        x, y = (float(v) for v in pair.split(","))
        assert 0 <= x <= metrics.PREVIEW_VIEWBOX
        assert 0 <= y <= metrics.PREVIEW_VIEWBOX


def test_svg_projection_puts_north_at_the_top() -> None:
    """SVG's y axis grows downward, so a northward leg must decrease y."""
    south_to_north = [(48.0, 11.0), (48.01, 11.0), (48.02, 11.0)]
    ys = [float(p.split(",")[1]) for p in metrics.project_svg_points(south_to_north).split()]
    assert ys[0] > ys[-1]


def test_svg_projection_of_a_ride_with_no_track_is_empty() -> None:
    assert metrics.project_svg_points([]) == ""
    assert metrics.project_svg_points([(48.0, 11.0)]) == ""


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def test_power_zones_span_ftp_correctly() -> None:
    zones = metrics.power_zones(250)
    assert len(zones) == 7
    assert zones[3].name == "Z4"  # threshold straddles FTP
    assert zones[3].low <= 250 <= (zones[3].high or 0)


def test_the_top_power_zone_is_open_ended() -> None:
    zones = metrics.power_zones(250)
    assert zones[-1].high is None
    assert zones[-1].label.endswith("+")


def test_zones_leave_no_gap_between_bands() -> None:
    """Every wattage must fall in exactly one zone."""
    zones = metrics.power_zones(250)
    for lower, upper in itertools.pairwise(zones):
        assert upper.low == (lower.high or 0) + 1


def test_hr_zones_are_relative_to_lthr() -> None:
    zones = metrics.hr_zones(170)
    assert len(zones) == 5
    assert zones[-1].low == pytest.approx(170, abs=2)


def test_zones_without_a_reference_value_are_empty() -> None:
    assert metrics.power_zones(0) == []
    assert metrics.hr_zones(0) == []
