"""Pure calculations — the one import boundary in this project.

**Nothing in this package may import ``reflex`` or ``..models``.** Arrays and
dataclasses in, scalars out (brief §3.3).

That is not architectural decoration. It is the reason the physics and
training-metric tests run in milliseconds with no app, no database and no
fixtures — which in turn is what makes it practical to check every derived
number against the values a real Karoo wrote into a real FIT file. Everything
outside this package may be as Reflex-flavoured as it likes.
"""

from .arrays import (
    FloatArray,
    as_float_array,
    centered_mean,
    channel_stats,
    mean_active,
    moving_time_axis,
    rolling_mean,
    sample_intervals,
    window_indices,
)
from .elevation import (
    GAIN_THRESHOLD_M,
    VAM_SMOOTH_WINDOW,
    elevation_gain_loss,
    grade,
    vam,
)
from .geo import (
    LOOP_MARGIN_M,
    PREVIEW_PADDING,
    PREVIEW_VIEWBOX,
    Point,
    bbox_overlap,
    bounding_box,
    bounds_to_center_zoom,
    haversine_m,
    is_loop,
    opposite_directions,
    polyline_length_m,
    project_svg_points,
    resample_polyline,
    route_match_score,
    simplify_polyline,
    to_points,
)
from .power import (
    FTP_FROM_20MIN_FACTOR,
    GROSS_EFFICIENCY,
    NP_WINDOW_S,
    POWER_CURVE_WINDOWS_S,
    BestWindow,
    baseline,
    best_20min_window,
    best_average_power,
    calories_kcal,
    decoupling,
    ftp_from_20min,
    ftp_from_window,
    hr_tss,
    intensity_factor,
    normalized_power,
    power_curve,
    total_work_kj,
    tss,
)
from .trends import EffortWindow, RideEfforts, best_effort_windows
from .zones import HR_ZONE_BANDS, POWER_ZONE_BANDS, Zone, hr_zones, power_zones

__all__ = [
    "FTP_FROM_20MIN_FACTOR",
    "GAIN_THRESHOLD_M",
    "GROSS_EFFICIENCY",
    "HR_ZONE_BANDS",
    "LOOP_MARGIN_M",
    "NP_WINDOW_S",
    "POWER_CURVE_WINDOWS_S",
    "POWER_ZONE_BANDS",
    "PREVIEW_PADDING",
    "PREVIEW_VIEWBOX",
    "VAM_SMOOTH_WINDOW",
    "BestWindow",
    "EffortWindow",
    "FloatArray",
    "Point",
    "RideEfforts",
    "Zone",
    "as_float_array",
    "baseline",
    "bbox_overlap",
    "best_20min_window",
    "best_average_power",
    "best_effort_windows",
    "bounding_box",
    "bounds_to_center_zoom",
    "calories_kcal",
    "centered_mean",
    "channel_stats",
    "decoupling",
    "elevation_gain_loss",
    "ftp_from_20min",
    "ftp_from_window",
    "grade",
    "haversine_m",
    "hr_tss",
    "hr_zones",
    "intensity_factor",
    "is_loop",
    "mean_active",
    "moving_time_axis",
    "normalized_power",
    "opposite_directions",
    "polyline_length_m",
    "power_curve",
    "power_zones",
    "project_svg_points",
    "resample_polyline",
    "rolling_mean",
    "route_match_score",
    "sample_intervals",
    "simplify_polyline",
    "to_points",
    "total_work_kj",
    "tss",
    "vam",
    "window_indices",
]
